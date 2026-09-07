"""
Generatore del report giornaliero.

Per ogni ticker dell'universo legge l'ultimo download raw (via
src/storage/io.py), calcola un riepilogo (ultimo prezzo, variazione
giornaliera, min/max, volume, alert di qualita'), confronta con l'ultimo
report generato in precedenza (se esiste), genera alcune considerazioni
testuali automatiche, e produce un'unica pagina HTML interattiva
(Chart.js via CDN):

  - grafico principale: andamento indicizzato (base 100) di tutti i
    ticker. Cliccando una voce della legenda si evidenzia la riga
    corrispondente in tabella e si aggiorna il grafico di dettaglio;
  - grafico a barre: variazione media per settore, cliccabile per
    filtrare la tabella su quel settore;
  - grafico di dettaglio: prezzo + volume (doppio asse) per il ticker
    selezionato (dal grafico, dalla tabella, o da un menu a tendina);
  - tabella con colonne ordinabili cliccando l'intestazione;
  - box "consiglio d'investimento" (per gioco, con disclaimer): podio da
    inizio anno, candidato per la prossima settimana secondo il segnale in
    uso, verifica dei consigli passati (dallo storico JSON) e — quando le
    giornate di verifica accumulate bastano — scelta adattiva del segnale
    col miglior track record: l'analisi migliora col crescere dello storico.

Tutti i dati (serie storiche incluse) sono incorporati come JSON nella
pagina: il file resta un unico HTML, ma richiede una connessione
internet per caricare Chart.js dal CDN la prima volta che si apre.

  - un file JSON di storico (data/reports/history/<YYYY-MM-DD>/report_<HH-MM-SS>.json)
    usato dal prossimo report per calcolare i confronti;
  - la pagina HTML (data/reports/<YYYY-MM-DD>/report_<YYYY-MM-DD>_<HH-MM-SS>.html),
    organizzata in una sottocartella per data, con una copia sempre
    aggiornata in data/reports/latest.html.

Nessun altro modulo dovrebbe generare report "a mano": tutto passa da qui.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config.loader import load_universe  # noqa: E402
from src.storage.io import (  # noqa: E402
    load_latest_features,
    load_latest_raw_fundamentals,
    load_latest_raw_prices,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "data" / "reports"
HISTORY_DIR = REPORTS_DIR / "history"

COLOR_UP = "#4ade80"
COLOR_DOWN = "#f87171"
COLOR_PALETTE = ["#60a5fa", "#f0b45a", "#a78bfa", "#4ade80", "#f87171",
                  "#38bdf8", "#fb923c", "#e879f9", "#facc15", "#94a3b8"]

SPARKLINE_WINDOW_DAYS = 180   # ultimi 180 giorni di borsa (~8,5 mesi) per mini-serie e grafici
MOVIMENTO_NOTEVOLE_PCT = 3.0  # soglia oltre la quale segnaliamo un movimento nelle considerazioni

# "Consiglio di investimento" (per gioco, NON consulenza finanziaria):
# finestre di momentum in giorni di borsa per il suggerimento sulla
# prossima settimana (~1 settimana e ~1 mese).
MOMENTUM_BREVE_GIORNI = 5
MOMENTUM_LUNGO_GIORNI = 21

# Varianti di segnale tra cui l'analisi sceglie. Finche' le giornate di
# verifica accumulate (consigli passati con settimana successiva completa)
# sono meno di MIN_GIORNATE_ADATTIVE si usa VARIANTE_DEFAULT; da li' in poi
# il report confronta le varianti sulle giornate passate (senza look-ahead:
# ogni pick simulato usa solo i dati noti a quella data) e adotta quella
# col miglior rendimento medio settimanale — l'analisi migliora man mano
# che il periodo di controllo cresce.
VARIANTI_SEGNALE = ("momentum_breve", "momentum_lungo", "blend_vol")
VARIANTE_DEFAULT = "blend_vol"
MIN_GIORNATE_ADATTIVE = 8
MAX_GIORNATE_BACKTEST = 60  # tetto alle giornate rivalutate nel confronto varianti
ETICHETTE_VARIANTI = {
    "momentum_breve": f"momentum a {MOMENTUM_BREVE_GIORNI} giorni",
    "momentum_lungo": f"momentum a {MOMENTUM_LUNGO_GIORNI} giorni",
    "blend_vol": "momentum misto rapportato alla volatilità",
}

# Heatmap di correlazione: finestra (ultimi ~12 mesi di borsa) e minimo di
# osservazioni in comune perche' una coppia sia considerata affidabile.
CORRELAZIONE_FINESTRA_GIORNI = 252
CORRELAZIONE_MIN_OSSERVAZIONI = 60
# Sopra questa soglia una coppia di titoli e' considerata "molto correlata"
# nelle considerazioni automatiche (diversificazione di fatto scarsa).
CORRELAZIONE_ALTA = 0.7


# ---------------------------------------------------------------------------
# Calcolo dei riepiloghi per ticker
# ---------------------------------------------------------------------------

def compute_ticker_summary(ticker_info: dict, df: pd.DataFrame | None) -> dict:
    """
    Calcola il riepilogo per un singolo ticker a partire dal suo DataFrame
    raw (gia' caricato da chi chiama, per non rileggerlo due volte).
    Ritorna sempre un dizionario, anche se il dato manca o e' vuoto.
    """
    base = {
        "ticker": ticker_info["ticker"],
        "nome": ticker_info.get("nome", ticker_info["ticker"]),
        "settore": ticker_info.get("settore", "n/d"),
    }

    if df is None or df.empty:
        return {**base, "status": "errore", "alerts": ["nessun dato disponibile"]}

    ultima = df.iloc[-1]
    penultima = df.iloc[-2] if len(df) >= 2 else None

    last_price = float(ultima["close"])
    last_date = ultima["date"]
    last_volume = int(ultima["volume"]) if pd.notna(ultima["volume"]) else None

    daily_change_pct = None
    if penultima is not None and penultima["close"]:
        daily_change_pct = (last_price - penultima["close"]) / penultima["close"] * 100

    min_close = float(df["close"].min())
    max_close = float(df["close"].max())

    alerts = []
    n_nulli = int(df[["open", "high", "low", "close", "adj_close", "volume"]].isna().sum().sum())
    if n_nulli > 0:
        alerts.append(f"{n_nulli} valori nulli")
    if (df["close"] <= 0).any():
        alerts.append("prezzi <= 0 presenti")
    if min_close > 0 and max_close / min_close > 20:
        alerts.append("rapporto max/min >20x (controllare split non aggiustato)")

    return {
        **base,
        "status": "ok",
        "last_price": round(last_price, 2),
        "last_date": last_date.strftime("%Y-%m-%d"),
        "daily_change_pct": round(daily_change_pct, 2) if daily_change_pct is not None else None,
        "min_close": round(min_close, 2),
        "max_close": round(max_close, 2),
        "last_volume": last_volume,
        "n_rows": len(df),
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Riepilogo fondamentali (P/E, EPS, margini, debito, dividend yield)
# ---------------------------------------------------------------------------

def _safe(val):
    """Normalizza NaN/None (puo' capitare dopo il giro su parquet) a None."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def compute_fundamentals_summary(df: pd.DataFrame | None) -> dict:
    """
    Estrae dall'ultimo download raw di fondamentali (via
    src/ingestion/fundamentals_av.py o fundamentals_finnhub.py) i campi da
    mostrare nel report. Ritorna tutti None se il ticker non ha ancora
    fondamentali scaricati: capita spesso nei primi giorni, perche' Alpha
    Vantage ha solo 25 richieste/giorno e ruota l'universo (vedi
    select_rotation in fundamentals_av.py) invece di scaricare tutto subito.
    """
    vuoto = {
        "pe_ratio": None, "dividend_yield": None, "debt_to_equity": None,
        "profit_margin": None, "operating_margin": None, "market_cap": None,
        "fundamentals_date": None, "fundamentals_source": None,
    }
    if df is None or df.empty:
        return vuoto

    riga = df.iloc[-1]
    return {
        "pe_ratio": _safe(riga.get("pe_ratio")),
        "dividend_yield": _safe(riga.get("dividend_yield")),
        "debt_to_equity": _safe(riga.get("debt_to_equity")),
        "profit_margin": _safe(riga.get("profit_margin")),
        "operating_margin": _safe(riga.get("operating_margin")),
        "market_cap": _safe(riga.get("market_cap")),
        "fundamentals_date": _safe(riga.get("date")),
        "fundamentals_source": _safe(riga.get("source")),
    }


# ---------------------------------------------------------------------------
# Storico / confronto con il report precedente
# ---------------------------------------------------------------------------

def load_previous_report() -> dict | None:
    """
    Ritorna il contenuto dell'ultimo report salvato in JSON, o None.
    I report sono organizzati in sottocartelle per data (YYYY-MM-DD/report_HH-MM-SS.json):
    i nomi, sia della cartella che del file, sono stringhe ordinabili
    cronologicamente, quindi basta un sort lessicografico sul path completo.
    """
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("*/report_*.json"))
    if not files:
        return None
    with open(files[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def add_comparison(current: list[dict], previous: dict | None) -> list[dict]:
    """
    Aggiunge a ciascun riepilogo corrente la variazione % rispetto
    all'ultimo prezzo registrato nel report precedente (se disponibile).
    """
    prev_by_ticker = {}
    if previous:
        for row in previous.get("tickers", []):
            if row.get("status") == "ok":
                prev_by_ticker[row["ticker"]] = row["last_price"]

    for row in current:
        row["change_vs_previous_report_pct"] = None
        if row["status"] == "ok" and row["ticker"] in prev_by_ticker:
            prev_price = prev_by_ticker[row["ticker"]]
            if prev_price:
                row["change_vs_previous_report_pct"] = round(
                    (row["last_price"] - prev_price) / prev_price * 100, 2
                )
    return current


# ---------------------------------------------------------------------------
# Considerazioni testuali automatiche
# ---------------------------------------------------------------------------

def generate_considerations(summaries: list[dict], previous: dict | None) -> list[str]:
    """
    Genera alcune osservazioni in linguaggio naturale a partire dai dati
    del giorno, per rendere il report leggibile a colpo d'occhio senza
    dover interpretare la tabella riga per riga.
    """
    note = []
    ok_rows = [r for r in summaries if r["status"] == "ok"]
    errori_rows = [r for r in summaries if r["status"] != "ok"]

    if errori_rows:
        nomi = ", ".join(r["ticker"] for r in errori_rows)
        note.append(f"Attenzione: {len(errori_rows)} ticker senza dati validi in questo run ({nomi}).")

    changes = [r["daily_change_pct"] for r in ok_rows if r["daily_change_pct"] is not None]
    if changes:
        avg = sum(changes) / len(changes)
        n_su = sum(1 for c in changes if c > 0)
        n_giu = sum(1 for c in changes if c < 0)
        verso = "in rialzo" if avg > 0 else ("in ribasso" if avg < 0 else "stabile")
        note.append(
            f"L'universo è mediamente {verso} oggi ({avg:+.2f}%): {n_su} titoli in rialzo, "
            f"{n_giu} in ribasso su {len(changes)} con dati validi."
        )

    by_sector: dict[str, list[float]] = {}
    for r in ok_rows:
        if r["daily_change_pct"] is not None:
            by_sector.setdefault(r["settore"], []).append(r["daily_change_pct"])
    if len(by_sector) > 1:
        medie_settore = {s: sum(v) / len(v) for s, v in by_sector.items()}
        miglior_settore = max(medie_settore, key=medie_settore.get)
        peggior_settore = min(medie_settore, key=medie_settore.get)
        note.append(
            f"Settore migliore: {miglior_settore} ({medie_settore[miglior_settore]:+.2f}% medio). "
            f"Settore peggiore: {peggior_settore} ({medie_settore[peggior_settore]:+.2f}% medio)."
        )

    notevoli = [r for r in ok_rows if r["daily_change_pct"] is not None
                and abs(r["daily_change_pct"]) >= MOVIMENTO_NOTEVOLE_PCT]
    if notevoli:
        dettagli = ", ".join(f"{r['ticker']} ({r['daily_change_pct']:+.2f}%)" for r in notevoli)
        note.append(f"Movimenti superiori a ±{MOVIMENTO_NOTEVOLE_PCT:.0f}% oggi: {dettagli}.")

    alert_rows = [r for r in summaries if r.get("alerts")]
    if alert_rows:
        dettagli = "; ".join(f"{r['ticker']}: {', '.join(r['alerts'])}" for r in alert_rows)
        note.append(
            f"Alert di qualità dati su {len(alert_rows)} ticker ({dettagli}) — "
            f"da risolvere nel modulo di pulizia prima di usare questi dati nello screening."
        )

    if previous:
        prev_changes = []
        for row in ok_rows:
            if row.get("change_vs_previous_report_pct") is not None:
                prev_changes.append(row["change_vs_previous_report_pct"])
        if prev_changes:
            avg_prev = sum(prev_changes) / len(prev_changes)
            if abs(avg_prev) >= 1.0:
                verso = "salito" if avg_prev > 0 else "sceso"
                note.append(
                    f"Rispetto al report precedente ({previous.get('generated_at')}), il valore medio "
                    f"dell'universo è {verso} del {avg_prev:+.2f}%."
                )
    else:
        note.append("Questo è il primo report: da qui in poi ogni run mostrerà anche il confronto con il precedente.")

    return note


def generate_fundamentals_considerations(summaries: list[dict]) -> list[str]:
    """
    Osservazioni automatiche su P/E, dividend yield e debito, sullo stesso
    modello di generate_considerations() ma per i fondamentali. Molti ticker
    possono non avere ancora dati (rotazione Alpha Vantage): lo segnaliamo
    esplicitamente invece di lasciar sembrare un errore.
    """
    note = []
    con_dati = [r for r in summaries if r.get("pe_ratio") is not None]
    senza_dati = [r for r in summaries if r["status"] == "ok" and r.get("pe_ratio") is None]

    if senza_dati:
        nomi = ", ".join(r["ticker"] for r in senza_dati)
        note.append(
            f"Fondamentali non ancora disponibili per {len(senza_dati)} ticker ({nomi}) — "
            f"normale nei primi giorni: Alpha Vantage concede solo 25 richieste/giorno e "
            f"src/ingestion/fundamentals_av.py ruota l'universo invece di scaricare tutto insieme."
        )

    if len(con_dati) >= 2:
        piu_economico = min(con_dati, key=lambda r: r["pe_ratio"])
        msg = f"P/E più basso nel portafoglio: {piu_economico['ticker']} ({piu_economico['pe_ratio']:.1f}x)."

        con_yield = [r for r in con_dati if r.get("dividend_yield") is not None]
        if con_yield:
            top_yield = max(con_yield, key=lambda r: r["dividend_yield"])
            msg += f" Dividend yield più alto: {top_yield['ticker']} ({top_yield['dividend_yield'] * 100:.2f}%)."
        note.append(msg)

        by_sector_pe: dict[str, list[float]] = {}
        for r in con_dati:
            by_sector_pe.setdefault(r["settore"], []).append(r["pe_ratio"])
        if len(by_sector_pe) > 1:
            medie = {s: sum(v) / len(v) for s, v in by_sector_pe.items()}
            settore_caro = max(medie, key=medie.get)
            settore_economico = min(medie, key=medie.get)
            note.append(
                f'Per P/E medio, il settore più "caro" è {settore_caro} ({medie[settore_caro]:.1f}x), '
                f'il più "a sconto" è {settore_economico} ({medie[settore_economico]:.1f}x).'
            )

        n_senza_debito = sum(1 for r in con_dati if r.get("debt_to_equity") is None)
        if n_senza_debito == len(con_dati):
            note.append(
                "Nessun dato di debt/equity disponibile: Alpha Vantage (fonte primaria) non lo espone "
                "nell'endpoint OVERVIEW — arriva solo per i ticker scaricati via Finnhub (fallback)."
            )

    return note


# ---------------------------------------------------------------------------
# Heatmap di correlazione tra i rendimenti giornalieri (dal layer features)
# ---------------------------------------------------------------------------

def build_correlation_matrix(tickers: list[str]) -> pd.DataFrame | None:
    """
    Costruisce la matrice di correlazione dei rendimenti semplici
    giornalieri tra i ticker dell'universo, leggendo il layer features
    (load_latest_features — le feature si calcolano UNA volta in
    src/features/ e qui si riusano soltanto, mai ricalcolate). Finestra:
    ultimi CORRELAZIONE_FINESTRA_GIORNI giorni di borsa per ticker,
    allineati per data. Ritorna None se meno di 2 ticker hanno feature
    disponibili.
    """
    series = {}
    for ticker in tickers:
        df = load_latest_features(ticker)
        if df is None or df.empty or "simple_return" not in df.columns:
            continue
        s = df.set_index("date")["simple_return"].dropna()
        if len(s) >= CORRELAZIONE_MIN_OSSERVAZIONI:
            series[ticker] = s.tail(CORRELAZIONE_FINESTRA_GIORNI)

    if len(series) < 2:
        return None

    wide = pd.DataFrame(series)
    return wide.corr(min_periods=CORRELAZIONE_MIN_OSSERVAZIONI)


def _correlation_cell_color(v) -> str:
    """
    Colore di sfondo per una cella della heatmap: rosso tanto piu' intenso
    quanto piu' la correlazione e' positiva (poca diversificazione), blu per
    correlazione negativa, sfondo neutro del tema per ~0 o mancante.
    """
    if v is None or pd.isna(v):
        return "#171d2b"
    base = (23, 29, 43)                                  # #171d2b, sfondo card
    target = (248, 113, 113) if v >= 0 else (96, 165, 250)  # rosso / blu del tema
    a = min(abs(float(v)), 1.0)
    r, g, b = (round(base[i] + (target[i] - base[i]) * a) for i in range(3))
    return f"rgb({r},{g},{b})"


def render_correlation_html(corr: pd.DataFrame | None) -> str:
    """
    Rende la matrice di correlazione come tabella HTML colorata (niente JS:
    i colori sono inline, cosi' la heatmap funziona anche offline). Se la
    matrice non e' disponibile, spiega come popolarla invece di sparire.
    """
    if corr is None:
        return ('<p class="muted">Heatmap non disponibile: servono le feature di almeno 2 ticker. '
                'Esegui <code>python scripts/check_features.py</code> (o il run serale) per calcolarle.</p>')

    tickers = list(corr.columns)
    head = "".join(f'<th class="corr-th">{t}</th>' for t in tickers)
    righe = ""
    for t1 in tickers:
        celle = ""
        for t2 in tickers:
            v = corr.loc[t1, t2]
            testo = "n/d" if pd.isna(v) else f"{v:.2f}"
            titolo = f"{t1} vs {t2}: {testo}"
            celle += (f'<td class="corr-td" style="background:{_correlation_cell_color(v)};" '
                      f'title="{titolo}">{testo}</td>')
        righe += f'<tr><th class="corr-th">{t1}</th>{celle}</tr>'

    return (f'<div class="corr-wrap"><table class="corr-table">'
            f'<thead><tr><th class="corr-th"></th>{head}</tr></thead>'
            f'<tbody>{righe}</tbody></table></div>'
            f'<p class="hint">Correlazione dei rendimenti giornalieri (adj_close, layer features), '
            f'ultimi {CORRELAZIONE_FINESTRA_GIORNI} giorni di borsa. Rosso = alta correlazione positiva '
            f'(i titoli si muovono insieme: diversificazione di fatto scarsa), blu = correlazione negativa.</p>')


def generate_correlation_considerations(corr: pd.DataFrame | None) -> list[str]:
    """
    Osservazioni automatiche sulla diversificazione reale del portafoglio,
    a partire dalla matrice di correlazione (stesso spirito di
    generate_considerations).
    """
    if corr is None or len(corr.columns) < 2:
        return []

    tickers = list(corr.columns)
    coppie = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            v = corr.iloc[i, j]
            if pd.notna(v):
                coppie.append((tickers[i], tickers[j], float(v)))
    if not coppie:
        return []

    note = []
    media = sum(v for _, _, v in coppie) / len(coppie)
    t1_max, t2_max, v_max = max(coppie, key=lambda c: c[2])
    t1_min, t2_min, v_min = min(coppie, key=lambda c: c[2])
    note.append(
        f"Correlazione media tra i rendimenti giornalieri: {media:.2f} "
        f"(ultimi {CORRELAZIONE_FINESTRA_GIORNI} giorni di borsa). "
        f"Coppia più correlata: {t1_max}-{t2_max} ({v_max:.2f}); "
        f"meno correlata: {t1_min}-{t2_min} ({v_min:.2f})."
    )

    molto_correlate = [(a, b, v) for a, b, v in coppie if v >= CORRELAZIONE_ALTA]
    if molto_correlate:
        dettagli = ", ".join(f"{a}-{b} ({v:.2f})" for a, b, v in molto_correlate)
        note.append(
            f"{len(molto_correlate)} coppie con correlazione ≥ {CORRELAZIONE_ALTA:.1f}: {dettagli} — "
            f"questi titoli tendono a muoversi insieme, la diversificazione reale tra loro è limitata."
        )
    return note


# ---------------------------------------------------------------------------
# Consiglio di investimento (per gioco — NON e' consulenza finanziaria)
# ---------------------------------------------------------------------------

def _trailing_return_pct(df: pd.DataFrame, giorni: int) -> float | None:
    """Rendimento % sugli ultimi `giorni` giorni di borsa (close su close)."""
    closes = df["close"].dropna()
    if len(closes) < giorni + 1:
        return None
    base = float(closes.iloc[-(giorni + 1)])
    if base <= 0:
        return None
    return (float(closes.iloc[-1]) / base - 1) * 100


def compute_ytd_return_pct(df: pd.DataFrame | None, anno: int) -> float | None:
    """
    Rendimento % da inizio anno `anno`. Baseline: l'ultima chiusura
    dell'anno precedente se presente nella serie (il vero "inizio anno"),
    altrimenti la prima chiusura disponibile dell'anno corrente.
    """
    if df is None or df.empty:
        return None
    d = df.dropna(subset=["close"])
    if d.empty:
        return None
    anni = d["date"].dt.year
    precedente = d[anni < anno]
    corrente = d[anni == anno]
    if corrente.empty:
        return None
    base = float(precedente["close"].iloc[-1]) if not precedente.empty else float(corrente["close"].iloc[0])
    if base <= 0:
        return None
    return (float(corrente["close"].iloc[-1]) / base - 1) * 100


def _forward_week_return_pct(df: pd.DataFrame, day_str: str) -> float | None:
    """
    Rendimento % nei MOMENTUM_BREVE_GIORNI giorni di borsa successivi
    all'ultima chiusura disponibile alla data `day_str` (YYYY-MM-DD).
    None se la finestra non e' ancora completa (consiglio troppo recente)
    o se la data precede l'inizio della serie.
    """
    d = df.dropna(subset=["close"]).reset_index(drop=True)
    pos = int((d["date"] <= pd.Timestamp(day_str)).sum()) - 1
    if pos < 0 or pos + MOMENTUM_BREVE_GIORNI >= len(d):
        return None
    base = float(d["close"].iloc[pos])
    if base <= 0:
        return None
    return (float(d["close"].iloc[pos + MOMENTUM_BREVE_GIORNI]) / base - 1) * 100


def load_history_tip_days() -> list[dict]:
    """
    Consigli emessi nei report passati, uno per giornata (l'ultimo run del
    giorno vince, coerente con load_previous_report). Ritorna una lista di
    {'data': 'YYYY-MM-DD', 'ticker': <pick>} in ordine cronologico. I report
    precedenti all'introduzione del consiglio semplicemente non compaiono.
    """
    if not HISTORY_DIR.exists():
        return []
    per_giorno: dict[str, Path] = {}
    for f in sorted(HISTORY_DIR.glob("*/report_*.json")):
        per_giorno[f.parent.name] = f
    out = []
    for giorno, f in sorted(per_giorno.items()):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        pick = ((data.get("investment_tip") or {}).get("prossima_settimana") or {}).get("ticker")
        if pick:
            out.append({"data": giorno, "ticker": pick})
    return out


def evaluate_past_tips(history_tips: list[dict], dfs_by_ticker: dict) -> dict:
    """
    Valuta i consigli passati con finestra completa: rendimento del pick
    nella settimana di borsa successiva vs media dell'universo nello stesso
    periodo. Ritorna {'esiti': [...], 'riepilogo': {...}} (vuoti se non c'e'
    ancora nulla di valutabile). E' il "controllo" che, accumulandosi run
    dopo run, permette all'analisi di migliorare (vedi select_signal_variant).
    """
    esiti = []
    for t in history_tips:
        df_pick = dfs_by_ticker.get(t["ticker"])
        if df_pick is None or df_pick.empty:
            continue
        r_pick = _forward_week_return_pct(df_pick, t["data"])
        if r_pick is None:
            continue
        rendimenti_universo = []
        for df in dfs_by_ticker.values():
            if df is None or df.empty:
                continue
            r = _forward_week_return_pct(df, t["data"])
            if r is not None:
                rendimenti_universo.append(r)
        if not rendimenti_universo:
            continue
        r_uni = sum(rendimenti_universo) / len(rendimenti_universo)
        esiti.append({
            "data": t["data"], "ticker": t["ticker"],
            "rendimento_pct": round(r_pick, 2),
            "universo_pct": round(r_uni, 2),
            "extra_pct": round(r_pick - r_uni, 2),
            "battuto_universo": r_pick > r_uni,
        })

    riepilogo: dict = {}
    if esiti:
        n = len(esiti)
        vinti = sum(1 for e in esiti if e["battuto_universo"])
        riepilogo = {
            "n_valutati": n,
            "n_battuto_universo": vinti,
            "hit_rate_pct": round(vinti / n * 100, 1),
            "extra_medio_pct": round(sum(e["extra_pct"] for e in esiti) / n, 2),
            "ultimo": esiti[-1],
        }
    return {"esiti": esiti, "riepilogo": riepilogo}


def _variant_pick_at(dfs_by_ticker: dict, feats_by_ticker: dict,
                     cutoff: pd.Timestamp, variante: str) -> str | None:
    """
    Il ticker che una variante di segnale avrebbe scelto usando SOLO i dati
    con data <= cutoff (niente look-ahead, come da filosofia del progetto).
    """
    punteggi: dict[str, float] = {}
    blend: dict[str, tuple[float, float | None]] = {}
    for ticker, df in dfs_by_ticker.items():
        if df is None or df.empty:
            continue
        d = df[df["date"] <= cutoff]
        m_breve = _trailing_return_pct(d, MOMENTUM_BREVE_GIORNI)
        m_lungo = _trailing_return_pct(d, MOMENTUM_LUNGO_GIORNI)
        if m_breve is None or m_lungo is None:
            continue
        if variante == "momentum_breve":
            punteggi[ticker] = m_breve
        elif variante == "momentum_lungo":
            punteggi[ticker] = m_lungo
        else:  # blend_vol
            vol = None
            feats = feats_by_ticker.get(ticker)
            if feats is not None and not feats.empty and "volatility_20d" in feats.columns:
                serie = feats[feats["date"] <= cutoff]["volatility_20d"].dropna()
                if not serie.empty and float(serie.iloc[-1]) > 0:
                    vol = float(serie.iloc[-1]) * 100
            blend[ticker] = ((m_breve + m_lungo) / 2, vol)

    if variante == "blend_vol":
        vols = [v for _, v in blend.values() if v]
        vol_default = sum(vols) / len(vols) if vols else None
        for ticker, (trend, vol) in blend.items():
            v = vol or vol_default
            punteggi[ticker] = trend / v if v else trend

    if not punteggi:
        return None
    return max(punteggi, key=punteggi.get)


def select_signal_variant(dfs_by_ticker: dict, feats_by_ticker: dict,
                          giornate: list[str]) -> tuple[str, dict]:
    """
    Sceglie la variante di segnale per il consiglio di oggi. Con meno di
    MIN_GIORNATE_ADATTIVE giornate valutabili ritorna il default; altrimenti
    simula ogni variante su quelle giornate (pick con soli dati noti allora,
    valutato sulla settimana successiva) e adotta quella col rendimento
    medio migliore. Ritorna (variante, medie_per_variante): il secondo e'
    vuoto finche' la modalita' adattiva non e' attiva.
    """
    giornate = giornate[-MAX_GIORNATE_BACKTEST:]
    if len(giornate) < MIN_GIORNATE_ADATTIVE:
        return VARIANTE_DEFAULT, {}

    medie: dict[str, float] = {}
    for variante in VARIANTI_SEGNALE:
        rendimenti = []
        for giorno in giornate:
            pick = _variant_pick_at(dfs_by_ticker, feats_by_ticker, pd.Timestamp(giorno), variante)
            if pick is None:
                continue
            r = _forward_week_return_pct(dfs_by_ticker[pick], giorno)
            if r is not None:
                rendimenti.append(r)
        if rendimenti:
            medie[variante] = round(sum(rendimenti) / len(rendimenti), 2)

    if not medie:
        return VARIANTE_DEFAULT, {}
    return max(medie, key=medie.get), medie


def build_investment_tip(dfs_by_ticker: dict, summaries: list[dict], anno: int) -> dict:
    """
    Costruisce i dati del "consiglio di investimento" del report. Quattro parti:

      - col senno di poi: podio da inizio anno dell'universo (piu' media e
        peggiore, per contesto);
      - per la prossima settimana: il ticker premiato dal segnale in uso,
        con contesto (settore, distanza dai massimi, volatilita' vs universo,
        P/E se disponibile) e un'alternativa;
      - verifica: esito dei consigli passati (dallo storico JSON) sulla
        settimana di borsa successiva, con track record aggregato;
      - segnale adattivo: da MIN_GIORNATE_ADATTIVE giornate valutabili in su
        la variante di segnale viene scelta in base a cio' che ha reso di
        piu' finora (select_signal_variant) — l'analisi migliora man mano
        che il periodo di controllo cresce.

    E' un gioco statistico sui prezzi passati, NON una raccomandazione:
    i rendimenti passati non predicono quelli futuri, men che meno su una
    settimana. Il disclaimer va sempre mostrato insieme al risultato.
    """
    info = {r["ticker"]: r for r in summaries}
    feats_by_ticker = {t: load_latest_features(t) for t in dfs_by_ticker}

    # --- classifica da inizio anno ------------------------------------
    ytd: list[dict] = []
    for ticker, df in dfs_by_ticker.items():
        r_ytd = compute_ytd_return_pct(df, anno)
        if r_ytd is not None:
            ytd.append({"ticker": ticker,
                        "nome": info.get(ticker, {}).get("nome", ticker),
                        "ytd_pct": round(r_ytd, 2)})
    ytd.sort(key=lambda r: r["ytd_pct"], reverse=True)

    # --- verifica dei consigli passati + scelta del segnale ------------
    verifica = evaluate_past_tips(load_history_tip_days(), dfs_by_ticker)
    giornate_valutabili = [e["data"] for e in verifica["esiti"]]
    variante, medie_varianti = select_signal_variant(dfs_by_ticker, feats_by_ticker,
                                                     giornate_valutabili)

    # --- candidati per la prossima settimana ---------------------------
    candidati: list[dict] = []
    for ticker, df in dfs_by_ticker.items():
        if df is None or df.empty:
            continue
        m_breve = _trailing_return_pct(df, MOMENTUM_BREVE_GIORNI)
        m_lungo = _trailing_return_pct(df, MOMENTUM_LUNGO_GIORNI)
        if m_breve is None or m_lungo is None:
            continue

        vol20_pct = None
        feats = feats_by_ticker.get(ticker)
        if feats is not None and not feats.empty and "volatility_20d" in feats.columns:
            v = feats["volatility_20d"].iloc[-1]
            if pd.notna(v) and float(v) > 0:
                vol20_pct = round(float(v) * 100, 1)

        recenti = df["close"].dropna().tail(SPARKLINE_WINDOW_DAYS)
        dal_massimo_pct = (round((float(recenti.iloc[-1]) / float(recenti.max()) - 1) * 100, 1)
                           if len(recenti) else None)

        r = info.get(ticker, {})
        candidati.append({
            "ticker": ticker,
            "nome": r.get("nome", ticker),
            "settore": r.get("settore", "n/d"),
            "pe_ratio": r.get("pe_ratio"),
            "mom_breve_pct": round(m_breve, 2),
            "mom_lungo_pct": round(m_lungo, 2),
            "trend_pct": round((m_breve + m_lungo) / 2, 2),
            "vol20_pct": vol20_pct,
            "dal_massimo_pct": dal_massimo_pct,
        })

    # Punteggio secondo la variante in uso. Per blend_vol, chi non ha ancora
    # la volatilita' (feature non calcolate) usa la media degli altri, per
    # non essere escluso ne' avvantaggiato.
    vols = [c["vol20_pct"] for c in candidati if c["vol20_pct"]]
    vol_default = sum(vols) / len(vols) if vols else None
    for c in candidati:
        if variante == "momentum_breve":
            c["score"] = c["mom_breve_pct"]
        elif variante == "momentum_lungo":
            c["score"] = c["mom_lungo_pct"]
        else:
            v = c["vol20_pct"] or vol_default
            c["score"] = round(c["trend_pct"] / v, 4) if v else c["trend_pct"]
    candidati.sort(key=lambda c: c["score"], reverse=True)

    tip: dict = {
        "anno": anno,
        "segnale": variante,
        "segnale_etichetta": ETICHETTE_VARIANTI[variante],
        "segnale_adattivo": bool(medie_varianti),
        "medie_varianti": medie_varianti,
        "n_giornate_valutabili": len(giornate_valutabili),
        "vol_media_universo_pct": round(sum(vols) / len(vols), 1) if vols else None,
        "track_record": verifica["riepilogo"],
    }
    if ytd:
        tip["ytd_podio"] = ytd[:3]
        tip["ytd_migliore"] = ytd[0]
        tip["ytd_peggiore"] = ytd[-1]
        tip["ytd_medio_pct"] = round(sum(r["ytd_pct"] for r in ytd) / len(ytd), 2)
    if candidati:
        tip["prossima_settimana"] = candidati[0]
        tip["alternativa"] = candidati[1] if len(candidati) > 1 else None
    return tip


def render_investment_tip_html(tip: dict) -> str:
    """
    Rende il consiglio come contenuto HTML del box dedicato. Se mancano i
    dati spiega il perche' invece di sparire (stesso stile della heatmap).
    """
    if not tip.get("ytd_podio") and not tip.get("prossima_settimana"):
        return ('<p class="muted">Consiglio non disponibile: servono i prezzi di almeno un ticker. '
                'Esegui <code>python scripts/run_daily_update.py</code> per scaricarli.</p>')

    voci = []

    podio = tip.get("ytd_podio")
    if podio:
        b = podio[0]
        p = tip["ytd_peggiore"]
        classifica = ", ".join(f"{i + 1}. {r['ticker']} {_fmt_pct(r['ytd_pct'])}"
                               for i, r in enumerate(podio))
        controvalore = f"{round(1000 * (1 + b['ytd_pct'] / 100)):,}".replace(",", ".")
        voci.append(
            f"<li><strong>Col senno di poi</strong> — podio da inizio {tip['anno']}: {classifica}. "
            f"1.000&nbsp;€ su {b['ticker']} ({b['nome']}) il 1° gennaio varrebbero oggi circa "
            f"{controvalore}&nbsp;€. Media dell'universo {_fmt_pct(tip['ytd_medio_pct'])}, "
            f"fanalino di coda {p['ticker']} ({_fmt_pct(p['ytd_pct'])}).</li>"
        )

    c = tip.get("prossima_settimana")
    if c:
        dettagli = [f"{_fmt_pct(c['mom_lungo_pct'])} nell'ultimo mese",
                    f"{_fmt_pct(c['mom_breve_pct'])} nell'ultima settimana"]
        if c.get("dal_massimo_pct") is not None:
            dettagli.append("sui massimi degli ultimi 180 giorni" if c["dal_massimo_pct"] >= -0.5
                            else f"{c['dal_massimo_pct']:+.1f}% dai massimi degli ultimi 180 giorni")
        if c.get("vol20_pct"):
            vm = tip.get("vol_media_universo_pct")
            confronto = ""
            if vm:
                confronto = (" (più tranquillo della media dell'universo)" if c["vol20_pct"] < vm
                             else " (più nervoso della media dell'universo)")
            dettagli.append(f"volatilità annualizzata {c['vol20_pct']:.0f}%{confronto}")
        if c.get("pe_ratio") is not None:
            dettagli.append(f"P/E {c['pe_ratio']:.1f}x")

        alt = tip.get("alternativa")
        if alt:
            alt_vol = f", volatilità {alt['vol20_pct']:.0f}%" if alt.get("vol20_pct") else ""
            alt_txt = (f" Alternativa: <strong>{alt['ticker']}</strong> "
                       f"(trend {_fmt_pct(alt['trend_pct'])}{alt_vol}).")
        else:
            alt_txt = ""
        voci.append(
            f"<li><strong>Per la prossima settimana</strong> — il segnale in uso "
            f"({tip['segnale_etichetta']}) indica <strong>{c['ticker']}</strong> "
            f"({c['nome']}, {c['settore']}): " + ", ".join(dettagli) + f".{alt_txt}</li>"
        )

    tr = tip.get("track_record") or {}
    ultimo = tr.get("ultimo")
    if ultimo:
        esito = ("consiglio azzeccato" if ultimo["battuto_universo"]
                 else "avrebbe reso di più la media dell'universo")
        txt = (f"<li><strong>Verifica</strong> — il consiglio del {ultimo['data']} "
               f"(<strong>{ultimo['ticker']}</strong>) ha reso {_fmt_pct(ultimo['rendimento_pct'])} nella "
               f"settimana di borsa successiva, contro {_fmt_pct(ultimo['universo_pct'])} dell'universo: "
               f"{esito}.")
        if tr.get("n_valutati", 0) >= 2:
            txt += (f" Track record complessivo: {tr['n_battuto_universo']}/{tr['n_valutati']} consigli "
                    f"sopra la media ({tr['hit_rate_pct']:.0f}%), extra-rendimento medio "
                    f"{_fmt_pct(tr['extra_medio_pct'])}.")
        voci.append(txt + "</li>")

    if tip.get("segnale_adattivo"):
        medie = tip.get("medie_varianti", {})
        dettaglio = "; ".join(f"{ETICHETTE_VARIANTI[k]}: {_fmt_pct(v)}/settimana"
                              for k, v in sorted(medie.items(), key=lambda kv: kv[1], reverse=True))
        voci.append(
            f"<li><strong>Analisi adattiva attiva</strong> — con {tip['n_giornate_valutabili']} giornate "
            f"di verifica accumulate, il consiglio usa il segnale che avrebbe reso di più finora "
            f"(simulazione senza look-ahead sui giorni passati): {dettaglio}.</li>"
        )
    else:
        n = tip.get("n_giornate_valutabili", 0)
        voci.append(
            f"<li><strong>Analisi in rodaggio</strong> — giornate di verifica accumulate: {n} su "
            f"{MIN_GIORNATE_ADATTIVE} necessarie. Da lì in poi il report confronterà automaticamente "
            f"i segnali ({', '.join(ETICHETTE_VARIANTI.values())}) e userà quello col miglior track "
            f"record: più cresce il periodo di controllo, migliore diventa l'analisi.</li>"
        )

    return (f'<ul>{"".join(voci)}</ul>'
            f'<p class="hint">Gioco statistico basato solo sui prezzi passati dell\'universo '
            f'(momentum a {MOMENTUM_BREVE_GIORNI} e {MOMENTUM_LUNGO_GIORNI} giorni di borsa, volatilità a '
            f'20 giorni dal layer features, verifica sui report storici): NON è una raccomandazione di '
            f'investimento e non va presa sul serio.</p>')


# ---------------------------------------------------------------------------
# Sparkline SVG (decorativa, per riga di tabella - niente JS necessario)
# ---------------------------------------------------------------------------

def make_inline_sparkline_svg(df: pd.DataFrame, up: bool, width: int = 130, height: int = 30) -> str:
    recent = df.tail(SPARKLINE_WINDOW_DAYS)
    closes = recent["close"].tolist()
    if len(closes) < 2:
        return ""

    lo, hi = min(closes), max(closes)
    rng = (hi - lo) if hi != lo else 1
    n = len(closes)
    pts = []
    for i, c in enumerate(closes):
        x = (i / (n - 1)) * (width - 4) + 2
        y = height - 2 - ((c - lo) / rng) * (height - 4)
        pts.append(f"{x:.1f},{y:.1f}")

    color = COLOR_UP if up else COLOR_DOWN
    points_attr = " ".join(pts)
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline-svg">'
            f'<polyline points="{points_attr}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


# ---------------------------------------------------------------------------
# Serie dati per i grafici interattivi (embed JSON)
# ---------------------------------------------------------------------------

def build_series_payload(dfs_by_ticker: dict) -> dict:
    """
    Costruisce, per ogni ticker, le serie (date/close/volume) sugli ultimi
    SPARKLINE_WINDOW_DAYS giorni, in una forma JSON-serializzabile, per
    alimentare i grafici Chart.js lato browser.
    """
    payload = {}
    for ticker, df in dfs_by_ticker.items():
        if df is None or df.empty:
            continue
        recent = df.tail(SPARKLINE_WINDOW_DAYS)
        dates = recent["date"].dt.strftime("%Y-%m-%d").tolist()
        closes = [None if pd.isna(v) else round(float(v), 4) for v in recent["close"]]
        volumes = [None if pd.isna(v) else int(v) for v in recent["volume"]]
        payload[ticker] = {"dates": dates, "close": closes, "volume": volumes}
    return payload


def build_fundamentals_bubble_payload(summaries: list[dict]) -> list[dict]:
    """
    Prepara i punti per il grafico a bolle Valore/Dividendo (P/E in x,
    dividend yield in y, bolla dimensionata sul market cap, colore per
    settore). Include solo i ticker che hanno sia P/E che dividend yield:
    gli altri (fondamentali non ancora scaricati) sono esclusi qui e
    conteggiati a parte da chi chiama, per mostrare quanti mancano.
    """
    punti = []
    for r in summaries:
        pe = r.get("pe_ratio")
        dy = r.get("dividend_yield")
        if pe is None or dy is None:
            continue
        punti.append({
            "ticker": r["ticker"],
            "settore": r["settore"],
            "pe": round(pe, 2),
            "yieldPct": round(dy * 100, 2),
            "marketCap": r.get("market_cap") or 0,
        })
    return punti


# ---------------------------------------------------------------------------
# Rendering HTML
# ---------------------------------------------------------------------------

def _fmt_pct(value: float | None) -> str:
    if value is None:
        return '<span class="muted">n/d</span>'
    cls = "up" if value > 0 else ("down" if value < 0 else "flat")
    segno = "+" if value > 0 else ""
    return f'<span class="{cls}">{segno}{value:.2f}%</span>'


def _fmt_num(value) -> str:
    if value is None:
        return "n/d"
    return f"{value:,}"


def _fmt_opt(value, decimals: int = 2) -> str:
    """Numero opzionale (fondamentali): 'n/d' se mancante, altrimenti a decimals cifre."""
    if value is None:
        return '<span class="muted">n/d</span>'
    return f"{value:.{decimals}f}"


def _fmt_yield_pct(value) -> str:
    """Dividend yield salvato come frazione (0.023) -> mostrato come percentuale."""
    if value is None:
        return '<span class="muted">n/d</span>'
    return f"{value * 100:.2f}%"


def render_html(tickers_summary: list[dict], generated_at: datetime, previous: dict | None,
                 sparkline_svgs: dict[str, str], series_payload: dict,
                 considerations: list[str], correlation_html: str = "",
                 investment_tip_html: str = "") -> str:
    ok_rows = [r for r in tickers_summary if r["status"] == "ok"]

    changes = [r["daily_change_pct"] for r in ok_rows if r["daily_change_pct"] is not None]
    avg_change = sum(changes) / len(changes) if changes else None

    # solo le righe con variazione calcolata: `or -999` tratterebbe una
    # variazione di esattamente 0.00% come dato mancante.
    con_variazione = [r for r in ok_rows if r["daily_change_pct"] is not None]
    best = max(con_variazione, key=lambda r: r["daily_change_pct"], default=None)
    worst = min(con_variazione, key=lambda r: r["daily_change_pct"], default=None)

    n_alerts = sum(1 for r in tickers_summary if r["alerts"])
    prev_generated_at = previous.get("generated_at") if previous else None

    righe_html = ""
    for r in sorted(tickers_summary, key=lambda r: r["ticker"]):
        if r["status"] != "ok":
            righe_html += f"""
            <tr class="error-row">
              <td>{r['ticker']}</td><td>{r['nome']}</td><td>{r['settore']}</td>
              <td colspan="9" class="down">ERRORE: {', '.join(r['alerts'])}</td>
            </tr>"""
            continue

        alert_badge = ""
        if r["alerts"]:
            alert_badge = f'<span class="badge">{" · ".join(r["alerts"])}</span>'

        spark = sparkline_svgs.get(r["ticker"], "")

        dc = r["daily_change_pct"]
        cvp = r["change_vs_previous_report_pct"]

        pe = r.get("pe_ratio")
        dy = r.get("dividend_yield")
        de = r.get("debt_to_equity")

        fonte = r.get("fundamentals_source")
        data_fond = r.get("fundamentals_date")
        if fonte:
            etichetta_fonte = "AV" if fonte == "alpha_vantage" else ("FH" if fonte == "finnhub" else fonte)
            fund_tooltip = f'title="Fondamentali: {etichetta_fonte}, aggiornati il {data_fond}"'
            ticker_cell = f'<strong>{r["ticker"]}</strong> <span class="src-badge" {fund_tooltip}>{etichetta_fonte}</span>'
        else:
            ticker_cell = f'<strong title="Fondamentali non ancora scaricati">{r["ticker"]}</strong>'

        righe_html += f"""
        <tr id="row-{r['ticker']}" class="ticker-row" data-ticker="{r['ticker']}" data-sector="{r['settore']}"
            data-price="{r['last_price']}" data-change="{dc if dc is not None else ''}"
            data-volume="{r['last_volume'] or ''}">
          <td>{ticker_cell}</td>
          <td>{r['nome']}</td>
          <td>{r['settore']}</td>
          <td data-sort-value="{r['last_price']}">{r['last_price']:.2f}</td>
          <td data-sort-value="{dc if dc is not None else -999999}">{_fmt_pct(dc)}</td>
          <td data-sort-value="{cvp if cvp is not None else -999999}">{_fmt_pct(cvp)}</td>
          <td>{spark}</td>
          <td data-sort-value="{r['last_volume'] or 0}">{_fmt_num(r['last_volume'])}</td>
          <td data-sort-value="{pe if pe is not None else -999999}">{_fmt_opt(pe)}</td>
          <td data-sort-value="{dy if dy is not None else -999999}">{_fmt_yield_pct(dy)}</td>
          <td data-sort-value="{de if de is not None else -999999}">{_fmt_opt(de)}</td>
          <td>{alert_badge}</td>
        </tr>"""

    confronto_html = ""
    if prev_generated_at:
        confronto_html = f'<p class="muted">Confrontato con il report precedente del {prev_generated_at}</p>'
    else:
        confronto_html = '<p class="muted">Nessun report precedente trovato: questo è il primo, non ci sono confronti storici.</p>'

    considerazioni_html = "".join(f"<li>{nota}</li>" for nota in considerations)

    default_ticker = (best or (ok_rows[0] if ok_rows else None))
    default_ticker_code = default_ticker["ticker"] if default_ticker else ""

    ticker_options_html = "".join(
        f'<option value="{r["ticker"]}">{r["ticker"]} — {r["nome"]}</option>'
        for r in sorted(ok_rows, key=lambda r: r["ticker"])
    )

    # dati per il JS: solo cio' che serve a runtime (serie + poche info di riepilogo)
    js_data = {
        "series": series_payload,
        "sectorAverages": {},
        "defaultTicker": default_ticker_code,
    }
    by_sector_js: dict[str, list[float]] = {}
    for r in ok_rows:
        if r["daily_change_pct"] is not None:
            by_sector_js.setdefault(r["settore"], []).append(r["daily_change_pct"])
    js_data["sectorAverages"] = {
        s: round(sum(v) / len(v), 2) for s, v in by_sector_js.items()
    }

    fund_points = build_fundamentals_bubble_payload(ok_rows)
    js_data["fundamentals"] = fund_points
    n_fund_esclusi = len(ok_rows) - len(fund_points)

    if fund_points:
        nota_esclusi = (
            f'<p class="hint">{n_fund_esclusi} ticker esclusi per dati P/E o dividend yield mancanti.</p>'
            if n_fund_esclusi else ""
        )
        fundamentals_chart_html = f'<canvas id="chart-fundamentals"></canvas>{nota_esclusi}'
    else:
        fundamentals_chart_html = (
            '<p class="muted">Nessun dato fondamentale ancora disponibile: esegui '
            '<code>python src/ingestion/fundamentals_av.py</code> per popolarlo.</p>'
        )

    report_data_json = json.dumps(js_data, ensure_ascii=False).replace("</", "<\\/")
    palette_json = json.dumps(COLOR_PALETTE)

    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Report portafoglio — {generated_at.strftime('%Y-%m-%d %H:%M')}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: #0f1420; color: #e8eaf0; margin: 0; padding: 32px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 15px; color: #cdd3e0; margin: 32px 0 12px; }}
  .subtitle {{ color: #8b93a7; margin-top: 0; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }}
  .card {{ background: #171d2b; border: 1px solid #262e42; border-radius: 10px; padding: 16px 20px; min-width: 160px; }}
  .card .label {{ font-size: 12px; color: #8b93a7; text-transform: uppercase; letter-spacing: .04em; }}
  .card .value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .considerazioni {{ background: #171d2b; border: 1px solid #262e42; border-radius: 10px; padding: 16px 20px; margin-bottom: 28px; }}
  .considerazioni ul {{ margin: 8px 0 0; padding-left: 20px; }}
  .considerazioni li {{ margin-bottom: 8px; font-size: 14px; line-height: 1.5; }}
  .tip-box {{ border-left: 3px solid #f0b45a; }}
  .charts {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .charts .chart-box {{ background: #171d2b; border: 1px solid #262e42; border-radius: 10px; padding: 16px; }}
  .chart-box h3 {{ font-size: 13px; color: #cdd3e0; margin: 0 0 10px; font-weight: 600; }}
  .chart-box canvas {{ max-height: 300px; }}
  .detail-box {{ grid-column: 1 / -1; }}
  .detail-box .detail-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; flex-wrap: wrap; gap: 8px; }}
  .detail-box select {{ background: #1d2436; color: #e8eaf0; border: 1px solid #262e42; border-radius: 6px; padding: 6px 10px; font-size: 13px; }}
  .hint {{ color: #8b93a7; font-size: 12px; margin-top: 6px; }}
  table {{ width: 100%; border-collapse: collapse; background: #171d2b; border-radius: 10px; overflow: hidden; }}
  th, td {{ padding: 10px 14px; text-align: left; font-size: 14px; border-bottom: 1px solid #262e42; vertical-align: middle; }}
  th {{ background: #1d2436; color: #8b93a7; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; cursor: pointer; user-select: none; }}
  th:hover {{ color: #e8eaf0; }}
  tr:last-child td {{ border-bottom: none; }}
  .ticker-row {{ cursor: pointer; transition: background-color .15s ease; }}
  .ticker-row:hover {{ background: #1a2133; }}
  .ticker-row.row-highlight {{ background: #22304a; box-shadow: inset 3px 0 0 #60a5fa; }}
  .ticker-row.row-dim {{ opacity: .35; }}
  .up {{ color: #4ade80; font-weight: 600; }}
  .down {{ color: #f87171; font-weight: 600; }}
  .flat {{ color: #8b93a7; }}
  .muted {{ color: #8b93a7; font-size: 13px; }}
  .badge {{ background: #3a2a1a; color: #f0b45a; padding: 2px 8px; border-radius: 6px; font-size: 12px; }}
  .src-badge {{ background: #1d2436; color: #8b93a7; padding: 1px 5px; border-radius: 4px; font-size: 10px; cursor: help; border: 1px solid #262e42; }}
  .error-row td {{ background: #241418; }}
  .sparkline-svg {{ display: block; }}
  .wide-box {{ grid-column: 1 / -1; }}
  .corr-wrap {{ overflow-x: auto; }}
  .corr-table {{ border-collapse: collapse; width: auto; margin: 0 auto; background: transparent; }}
  .corr-table .corr-th {{ background: transparent; color: #8b93a7; font-size: 11px; padding: 4px 6px; text-align: center; cursor: default; }}
  .corr-table .corr-td {{ border: 1px solid #0f1420; font-size: 11px; padding: 6px 8px; text-align: center; color: #e8eaf0; min-width: 46px; cursor: help; }}
</style>
</head>
<body>
  <h1>Report portafoglio — {generated_at.strftime('%d/%m/%Y %H:%M')}</h1>
  <p class="subtitle">Aggiornamento serale dopo la chiusura dei mercati</p>

  <div class="cards">
    <div class="card"><div class="label">Ticker ok</div><div class="value">{len(ok_rows)}/{len(tickers_summary)}</div></div>
    <div class="card"><div class="label">Variazione media giornaliera</div><div class="value">{_fmt_pct(round(avg_change, 2) if avg_change is not None else None)}</div></div>
    <div class="card"><div class="label">Migliore oggi</div><div class="value">{(best['ticker'] + " " + _fmt_pct(best['daily_change_pct'])) if best else "n/d"}</div></div>
    <div class="card"><div class="label">Peggiore oggi</div><div class="value">{(worst['ticker'] + " " + _fmt_pct(worst['daily_change_pct'])) if worst else "n/d"}</div></div>
    <div class="card"><div class="label">Alert attivi</div><div class="value">{n_alerts}</div></div>
  </div>

  <h2>Considerazioni</h2>
  <div class="considerazioni">
    <ul>{considerazioni_html}</ul>
  </div>

  <h2>Consiglio d'investimento <span class="hint">— per gioco, non è consulenza finanziaria</span></h2>
  <div class="considerazioni tip-box">
    {investment_tip_html}
  </div>

  <h2>Grafici <span class="hint">— clicca una voce in legenda o una riga in tabella per evidenziarla ed esplorarne il dettaglio</span></h2>
  <div class="charts">
    <div class="chart-box">
      <h3>Andamento indicizzato (base 100), ultimi {SPARKLINE_WINDOW_DAYS} giorni</h3>
      <canvas id="chart-index"></canvas>
    </div>
    <div class="chart-box">
      <h3>Variazione media per settore (clicca una barra per filtrare la tabella)</h3>
      <canvas id="chart-sector"></canvas>
    </div>
    <div class="chart-box wide-box">
      <h3>Valore vs Dividendo (P/E in orizzontale, dividend yield in verticale, bolla = market cap, colore = settore)</h3>
      {fundamentals_chart_html}
    </div>
    <div class="chart-box wide-box">
      <h3>Correlazione tra i rendimenti giornalieri — diversificazione reale del portafoglio</h3>
      {correlation_html}
    </div>
    <div class="chart-box detail-box">
      <div class="detail-header">
        <h3 style="margin:0;">Dettaglio titolo — prezzo e volume</h3>
        <select id="detail-ticker-select">{ticker_options_html}</select>
      </div>
      <canvas id="chart-detail"></canvas>
    </div>
  </div>

  {confronto_html}

  <table id="tickers-table">
    <thead>
      <tr>
        <th data-sort="ticker">Ticker</th>
        <th data-sort="text">Nome</th>
        <th data-sort="text">Settore</th>
        <th data-sort="numeric">Ultimo prezzo</th>
        <th data-sort="numeric">Var. giorno</th>
        <th data-sort="numeric">Var. vs report prec.</th>
        <th>Andamento ({SPARKLINE_WINDOW_DAYS} gg di borsa)</th>
        <th data-sort="numeric">Volume</th>
        <th data-sort="numeric">P/E</th>
        <th data-sort="numeric">Div. Yield</th>
        <th data-sort="numeric">Debt/Equity</th>
        <th>Alert</th>
      </tr>
    </thead>
    <tbody>
      {righe_html}
    </tbody>
  </table>

  <p class="muted" style="margin-top:24px;">Generato automaticamente da run_daily_update.py — dati grezzi in data/raw/prices e data/raw/fundamentals, storico report in data/reports/history. I fondamentali (P/E, dividend yield, debt/equity) vengono da Alpha Vantage (AV) o Finnhub (FH) e si aggiornano a rotazione, non tutti i giorni: la data accanto al ticker indica quando sono stati scaricati. I grafici richiedono una connessione internet per caricare Chart.js.</p>

<script>
const REPORT_DATA = {report_data_json};
const PALETTE = {palette_json};
const COLOR_UP = "{COLOR_UP}";
const COLOR_DOWN = "{COLOR_DOWN}";

let selectedTicker = REPORT_DATA.defaultTicker;
let selectedSector = null;
let detailChart = null;

function highlightRow(ticker) {{
  document.querySelectorAll('.ticker-row').forEach(row => row.classList.remove('row-highlight'));
  const row = document.getElementById('row-' + ticker);
  if (row) {{
    row.classList.add('row-highlight');
    row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}
}}

function filterBySector(sector) {{
  selectedSector = (selectedSector === sector) ? null : sector;
  document.querySelectorAll('.ticker-row').forEach(row => {{
    if (!selectedSector || row.dataset.sector === selectedSector) {{
      row.classList.remove('row-dim');
    }} else {{
      row.classList.add('row-dim');
    }}
  }});
}}

function selectTicker(ticker) {{
  if (!REPORT_DATA.series[ticker]) return;
  selectedTicker = ticker;
  document.getElementById('detail-ticker-select').value = ticker;
  highlightRow(ticker);
  updateDetailChart(ticker);
}}

function updateDetailChart(ticker) {{
  const series = REPORT_DATA.series[ticker];
  if (!series || !detailChart) return;
  detailChart.data.labels = series.dates;
  detailChart.data.datasets[0].data = series.close;
  detailChart.data.datasets[1].data = series.volume;
  detailChart.options.plugins.title.text = ticker + ' — prezzo di chiusura e volume';
  detailChart.update();
}}

document.addEventListener('DOMContentLoaded', function () {{

  // --- Grafico indicizzato (base 100) --------------------------------
  const tickers = Object.keys(REPORT_DATA.series);
  let indexDatasets = tickers.map((t, i) => {{
    const closes = REPORT_DATA.series[t].close;
    const base = closes.find(v => v !== null) || 1;
    const indexed = closes.map(v => v === null ? null : (v / base) * 100);
    return {{
      label: t,
      data: indexed,
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length],
      borderWidth: 1.8,
      pointRadius: 0,
      pointHoverRadius: 5,
      tension: 0.15,
    }};
  }});
  const allDates = tickers.length ? REPORT_DATA.series[tickers[0]].dates : [];

  const indexChart = new Chart(document.getElementById('chart-index').getContext('2d'), {{
    type: 'line',
    data: {{ labels: allDates, datasets: indexDatasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'nearest', intersect: false }},
      plugins: {{
        legend: {{
          position: 'top',
          labels: {{ color: '#8b93a7', usePointStyle: true, boxWidth: 8, font: {{ size: 11 }} }},
          onClick: function (e, legendItem, legend) {{
            const index = legendItem.datasetIndex;
            const ci = legend.chart;
            if (ci.isDatasetVisible(index)) {{
              ci.hide(index);
              legendItem.hidden = true;
            }} else {{
              ci.show(index);
              legendItem.hidden = false;
            }}
            selectTicker(legendItem.text);
          }}
        }},
        tooltip: {{
          callbacks: {{
            label: ctx => `${{ctx.dataset.label}}: ${{ctx.parsed.y === null ? 'n/d' : ctx.parsed.y.toFixed(1)}}`
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b93a7', maxTicksLimit: 8 }}, grid: {{ color: '#262e42' }} }},
        y: {{ ticks: {{ color: '#8b93a7' }}, grid: {{ color: '#262e42' }} }}
      }},
      onClick: function (evt) {{
        const points = indexChart.getElementsAtEventForMode(evt, 'nearest', {{ intersect: false }}, true);
        if (points.length) {{
          const datasetIndex = points[0].datasetIndex;
          selectTicker(indexChart.data.datasets[datasetIndex].label);
        }}
      }}
    }}
  }});

  // --- Grafico settoriale ---------------------------------------------
  const sectors = Object.keys(REPORT_DATA.sectorAverages).sort(
    (a, b) => REPORT_DATA.sectorAverages[a] - REPORT_DATA.sectorAverages[b]
  );
  const sectorValues = sectors.map(s => REPORT_DATA.sectorAverages[s]);
  const sectorColors = sectorValues.map(v => v >= 0 ? COLOR_UP : COLOR_DOWN);

  const sectorChart = new Chart(document.getElementById('chart-sector').getContext('2d'), {{
    type: 'bar',
    data: {{ labels: sectors, datasets: [{{ data: sectorValues, backgroundColor: sectorColors, borderRadius: 4 }}] }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: ctx => `${{ctx.parsed.x.toFixed(2)}}% medio` }} }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b93a7' }}, grid: {{ color: '#262e42' }} }},
        y: {{ ticks: {{ color: '#8b93a7' }}, grid: {{ display: false }} }}
      }},
      onClick: function (evt) {{
        const points = sectorChart.getElementsAtEventForMode(evt, 'nearest', {{ intersect: true }}, true);
        if (points.length) {{
          filterBySector(sectors[points[0].index]);
        }}
      }}
    }}
  }});

  // --- Grafico fondamentali (Valore vs Dividendo) -----------------------
  const fundPoints = REPORT_DATA.fundamentals || [];
  const fundCanvas = document.getElementById('chart-fundamentals');
  if (fundPoints.length && fundCanvas) {{
    const sectorsFund = [...new Set(fundPoints.map(p => p.settore))];
    const maxCap = Math.max(...fundPoints.map(p => p.marketCap || 0), 1);
    const radiusFor = cap => 5 + Math.sqrt((cap || 0) / maxCap) * 28;

    const fundDatasets = sectorsFund.map((s, i) => ({{
      label: s,
      data: fundPoints.filter(p => p.settore === s).map(p => ({{
        x: p.pe, y: p.yieldPct, r: radiusFor(p.marketCap), ticker: p.ticker
      }})),
      backgroundColor: PALETTE[i % PALETTE.length] + 'cc',
      borderColor: PALETTE[i % PALETTE.length],
    }}));

    new Chart(fundCanvas.getContext('2d'), {{
      type: 'bubble',
      data: {{ datasets: fundDatasets }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'top', labels: {{ color: '#8b93a7', usePointStyle: true, boxWidth: 8, font: {{ size: 11 }} }} }},
          tooltip: {{
            callbacks: {{
              label: ctx => `${{ctx.raw.ticker}}: P/E ${{ctx.raw.x}}, yield ${{ctx.raw.y}}%`
            }}
          }}
        }},
        scales: {{
          x: {{ title: {{ display: true, text: 'P/E', color: '#8b93a7' }}, ticks: {{ color: '#8b93a7' }}, grid: {{ color: '#262e42' }} }},
          y: {{ title: {{ display: true, text: 'Dividend yield %', color: '#8b93a7' }}, ticks: {{ color: '#8b93a7' }}, grid: {{ color: '#262e42' }} }}
        }}
      }}
    }});
  }}

  // --- Grafico di dettaglio (prezzo + volume) --------------------------
  const initial = REPORT_DATA.series[selectedTicker] || {{ dates: [], close: [], volume: [] }};
  detailChart = new Chart(document.getElementById('chart-detail').getContext('2d'), {{
    data: {{
      labels: initial.dates,
      datasets: [
        {{
          type: 'line', label: 'Prezzo di chiusura', data: initial.close,
          borderColor: '#60a5fa', backgroundColor: '#60a5fa', yAxisID: 'y',
          pointRadius: 0, borderWidth: 2, tension: 0.15,
        }},
        {{
          type: 'bar', label: 'Volume', data: initial.volume,
          backgroundColor: 'rgba(139,147,167,0.35)', yAxisID: 'y1',
        }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#8b93a7' }} }},
        title: {{ display: true, text: (selectedTicker || '') + ' — prezzo di chiusura e volume', color: '#cdd3e0', font: {{ size: 12 }} }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b93a7', maxTicksLimit: 10 }}, grid: {{ color: '#262e42' }} }},
        y: {{ position: 'left', ticks: {{ color: '#8b93a7' }}, grid: {{ color: '#262e42' }} }},
        y1: {{ position: 'right', ticks: {{ color: '#8b93a7' }}, grid: {{ display: false }} }}
      }}
    }}
  }});

  // --- Interazioni tabella / dropdown -----------------------------------
  document.querySelectorAll('.ticker-row').forEach(row => {{
    row.addEventListener('click', () => selectTicker(row.dataset.ticker));
  }});

  document.getElementById('detail-ticker-select').addEventListener('change', function () {{
    selectTicker(this.value);
  }});

  if (selectedTicker) {{
    document.getElementById('detail-ticker-select').value = selectedTicker;
    highlightRow(selectedTicker);
  }}

  // --- Tabella ordinabile -------------------------------------------------
  document.querySelectorAll('#tickers-table th').forEach((th, colIndex) => {{
    let sortDir = 'desc';
    th.addEventListener('click', () => {{
      const tbody = document.querySelector('#tickers-table tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const isNumeric = th.dataset.sort === 'numeric';
      rows.sort((a, b) => {{
        const cellA = a.children[colIndex];
        const cellB = b.children[colIndex];
        if (!cellA || !cellB) return 0;
        let valA = isNumeric ? parseFloat(cellA.dataset.sortValue || cellA.textContent) : cellA.textContent.trim().toLowerCase();
        let valB = isNumeric ? parseFloat(cellB.dataset.sortValue || cellB.textContent) : cellB.textContent.trim().toLowerCase();
        if (isNumeric) {{ valA = isNaN(valA) ? -Infinity : valA; valB = isNaN(valB) ? -Infinity : valB; }}
        const cmp = valA < valB ? -1 : valA > valB ? 1 : 0;
        return sortDir === 'asc' ? cmp : -cmp;
      }});
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});

}});
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_report() -> Path:
    """
    Genera il report completo: calcola i riepiloghi, li confronta con lo
    storico, prepara le serie per i grafici interattivi, la heatmap di
    correlazione (dal layer features) e le considerazioni, salva il JSON
    di storico e la pagina HTML (+ latest.html).
    Ritorna il percorso del file HTML.
    """
    generated_at = datetime.now(timezone.utc).astimezone()
    universe = load_universe()

    dfs_by_ticker = {t["ticker"]: load_latest_raw_prices(t["ticker"]) for t in universe}
    fund_dfs_by_ticker = {t["ticker"]: load_latest_raw_fundamentals(t["ticker"]) for t in universe}

    previous = load_previous_report()
    summaries = [compute_ticker_summary(t, dfs_by_ticker[t["ticker"]]) for t in universe]
    summaries = add_comparison(summaries, previous)

    for r in summaries:
        r.update(compute_fundamentals_summary(fund_dfs_by_ticker.get(r["ticker"])))

    summaries_by_ticker = {r["ticker"]: r for r in summaries}
    sparkline_svgs = {}
    for ticker, df in dfs_by_ticker.items():
        if df is None or df.empty:
            continue
        up = (summaries_by_ticker[ticker].get("daily_change_pct") or 0) >= 0
        sparkline_svgs[ticker] = make_inline_sparkline_svg(df, up)

    series_payload = build_series_payload(dfs_by_ticker)

    corr = build_correlation_matrix([t["ticker"] for t in universe])
    correlation_html = render_correlation_html(corr)

    investment_tip = build_investment_tip(dfs_by_ticker, summaries, generated_at.year)
    investment_tip_html = render_investment_tip_html(investment_tip)

    considerations = generate_considerations(summaries, previous)
    considerations += generate_fundamentals_considerations(summaries)
    considerations += generate_correlation_considerations(corr)

    date_folder = generated_at.strftime("%Y-%m-%d")
    time_name = generated_at.strftime("%H-%M-%S")

    reports_day_dir = REPORTS_DIR / date_folder
    history_day_dir = HISTORY_DIR / date_folder
    reports_day_dir.mkdir(parents=True, exist_ok=True)
    history_day_dir.mkdir(parents=True, exist_ok=True)

    history_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "tickers": summaries,
        # salvato nello storico per poter verificare, nei report futuri,
        # se il "consiglio" della settimana ci avrebbe azzeccato
        "investment_tip": investment_tip,
    }
    history_path = history_day_dir / f"report_{time_name}.json"
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history_payload, f, indent=2, ensure_ascii=False)

    html = render_html(summaries, generated_at, previous, sparkline_svgs, series_payload,
                       considerations, correlation_html, investment_tip_html)
    html_path = reports_day_dir / f"report_{date_folder}_{time_name}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    latest_path = REPORTS_DIR / "latest.html"
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)

    return html_path


if __name__ == "__main__":
    path = generate_report()
    print(f"Report generato: {path}")
    print(f"Copia sempre aggiornata: {REPORTS_DIR / 'latest.html'}")
# fine modulo
