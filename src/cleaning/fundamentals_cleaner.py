"""
Modulo di pulizia per i fondamentali. Legge l'ultimo raw via
src/storage/io.py (load_latest_raw_fundamentals), segnala/nulla gli
outlier evidenti sui singoli campi (P/E assurdo, margini fuori range
plausibile, dividend yield o debt/equity fuori scala) e verifica la
coerenza tra le fonti (Alpha Vantage e Finnhub). Le unita' di misura sono
gia' state normalizzate in ingestion (vedi fundamentals_finnhub.py, che
converte percentuali/milioni allo stesso formato di Alpha Vantage): qui non
ci si fida ciecamente di quella normalizzazione, la si ricontrolla
confrontando i valori effettivi delle due fonti.

Il salvataggio del risultato passa da src/storage/io.py
(save_clean_fundamentals): questo modulo si limita a calcolare, decide chi
lo chiama (per ora scripts/check_clean.py) se e quando persistere.

Non solleva mai eccezione per un singolo ticker: in caso di problema il
report segnala lo status e la funzione ritorna (None, report).
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.storage.io import (  # noqa: E402
    list_raw_fundamentals_downloads,
    load_latest_raw_fundamentals,
)

# --- Soglie di plausibilita' per singolo campo -----------------------------
# La distinzione e' tra valori "insoliti ma reali" (si segnalano soltanto,
# restano nel dato pulito) e valori "quasi certamente un errore" (si
# nullano, il valore originale finisce comunque nel report per controllo).

# Un P/E negativo (utili negativi) e' realistico e comune (aziende in perdita):
# non viene nullato, solo segnalato come informativo. Un P/E enorme in
# valore assoluto (utili quasi zero rispetto al prezzo) e' invece piu'
# probabile un errore di dato che una situazione di mercato reale.
PE_ASSOLUTO_MASSIMO = 500

# I margini sono frazioni (0.25 = 25%). Oltre questi limiti e' quasi
# certamente un errore di unita' di misura (es. percentuale non convertita
# in frazione) piuttosto che un margine realmente cosi' estremo.
MARGINE_MIN = -5.0
MARGINE_MAX = 1.5

# Dividend yield come frazione. Oltre il 20% e' raro ma possibile (titoli
# in forte stress finanziario): si segnala senza nullare. Oltre il 100% e'
# quasi certamente un errore di unita' di misura.
DIVIDEND_YIELD_SOSPETTO = 0.20
DIVIDEND_YIELD_MASSIMO = 1.0

# Debt/equity negativo capita con equity negativo (raro ma reale): si
# segnala senza nullare. Valori estremi in valore assoluto sono piu'
# probabilmente errori di dato.
DEBT_EQUITY_MASSIMO = 50.0

# Sopra questa differenza relativa tra le due fonti per lo stesso campo, si
# segnala un'incoerenza da controllare manualmente. Le due fonti possono
# avere date di riferimento (latest_quarter) diverse, quindi un po' di
# scostamento e' normale: solo scostamenti grandi sono un problema.
SOGLIA_DIFFERENZA_RELATIVA_FONTI = 0.30

CAMPI_CONFRONTO_FONTI = [
    "pe_ratio", "eps", "dividend_yield", "market_cap",
    "profit_margin", "operating_margin", "debt_to_equity",
]


def _valido(val) -> bool:
    """True se val e' un numero utilizzabile (non None, non NaN)."""
    if val is None:
        return False
    try:
        return not pd.isna(val)
    except TypeError:
        return False


def _diff_relativa(a, b) -> float | None:
    if not _valido(a) or not _valido(b):
        return None
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def _controlla_outlier(riga: dict) -> tuple[dict, list[str]]:
    """
    Ritorna (riga_corretta, alerts). Nulla i campi fuori dai range
    "impossibili" (probabile errore di dato/unita' di misura), segnala
    senza nullare quelli fuori dai range "plausibili ma insoliti".
    """
    riga = dict(riga)
    alerts = []

    pe = riga.get("pe_ratio")
    if _valido(pe):
        if pe < 0:
            alerts.append(f"P/E negativo ({pe:.1f}): utili negativi, informativo, non nullato")
        if abs(pe) > PE_ASSOLUTO_MASSIMO:
            alerts.append(f"P/E fuori range plausibile ({pe:.1f}): nullato")
            riga["pe_ratio"] = None

    for campo in ("profit_margin", "operating_margin"):
        val = riga.get(campo)
        if _valido(val) and (val < MARGINE_MIN or val > MARGINE_MAX):
            alerts.append(f"{campo} fuori range plausibile ({val:.2f}): nullato")
            riga[campo] = None

    dy = riga.get("dividend_yield")
    if _valido(dy):
        if dy < 0:
            alerts.append(f"dividend_yield negativo ({dy:.4f}): nullato")
            riga["dividend_yield"] = None
        elif dy > DIVIDEND_YIELD_MASSIMO:
            alerts.append(f"dividend_yield fuori range plausibile ({dy:.4f}): nullato")
            riga["dividend_yield"] = None
        elif dy > DIVIDEND_YIELD_SOSPETTO:
            alerts.append(f"dividend_yield insolitamente alto ({dy * 100:.1f}%), da verificare, non nullato")

    de = riga.get("debt_to_equity")
    if _valido(de):
        if de < 0:
            alerts.append(f"debt_to_equity negativo ({de:.2f}): possibile equity negativo, da verificare, non nullato")
        if abs(de) > DEBT_EQUITY_MASSIMO:
            alerts.append(f"debt_to_equity fuori range plausibile ({de:.2f}): nullato")
            riga["debt_to_equity"] = None

    return riga, alerts


def _confronta_fonti(ticker: str) -> list[str]:
    """
    Carica tutti i download raw storici del ticker, prende l'ultimo
    disponibile per ciascuna fonte (alpha_vantage e finnhub) e confronta i
    campi comuni. Scostamenti relativi oltre SOGLIA_DIFFERENZA_RELATIVA_FONTI
    vengono segnalati (mai corretti automaticamente: senza un terzo
    riferimento non sappiamo quale fonte sia piu' affidabile per quel
    campo specifico, serve controllo umano).
    """
    files = list_raw_fundamentals_downloads(ticker)
    if not files:
        return []

    per_fonte: dict[str, pd.Series] = {}
    for path in files:
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty:
            continue
        riga = df.iloc[-1]
        fonte = riga.get("source")
        if fonte:
            # i file sono ordinati per timestamp crescente nel nome:
            # sovrascrivendo teniamo sempre l'ultimo download per fonte.
            per_fonte[fonte] = riga

    if len(per_fonte) < 2:
        return []

    alerts = []
    fonti = sorted(per_fonte.keys())
    for i in range(len(fonti)):
        for j in range(i + 1, len(fonti)):
            f1, f2 = fonti[i], fonti[j]
            r1, r2 = per_fonte[f1], per_fonte[f2]
            for campo in CAMPI_CONFRONTO_FONTI:
                v1, v2 = r1.get(campo), r2.get(campo)
                diff = _diff_relativa(v1, v2)
                if diff is not None and diff > SOGLIA_DIFFERENZA_RELATIVA_FONTI:
                    alerts.append(
                        f"{campo}: {f1}={v1:.4g} vs {f2}={v2:.4g} (scostamento {diff * 100:.0f}%)"
                    )

    return alerts


def clean_fundamentals(ticker: str) -> tuple[pd.DataFrame | None, dict]:
    """
    Pulisce l'ultimo download raw di fondamentali per un ticker:
      1. nulla/segnala outlier evidenti sui singoli campi (vedi
         _controlla_outlier);
      2. confronta l'ultimo dato di ciascuna fonte disponibile
         (Alpha Vantage / Finnhub) e segnala incoerenze rilevanti (vedi
         _confronta_fonti).

    Ritorna (df_pulito, report). df_pulito e' None se non c'e' nessun raw
    disponibile (report["status"] == "nessun_dato") o in caso di errore
    imprevisto (report["status"] == "errore"). Non solleva mai eccezione.
    """
    report: dict = {"ticker": ticker}

    try:
        raw = load_latest_raw_fundamentals(ticker)
    except Exception as e:
        report["status"] = "errore"
        report["alerts"] = [f"errore in lettura raw: {e}"]
        return None, report

    if raw is None or raw.empty:
        report["status"] = "nessun_dato"
        report["alerts"] = ["nessun dato raw disponibile"]
        return None, report

    riga = raw.iloc[-1].to_dict()
    riga_corretta, alerts_outlier = _controlla_outlier(riga)
    alerts_fonti = _confronta_fonti(ticker)

    df_clean = pd.DataFrame([riga_corretta])[raw.columns]

    report.update({
        "status": "ok",
        "fonte": riga.get("source"),
        "data": riga.get("date"),
        "alerts_outlier": alerts_outlier,
        "alerts_incoerenza_fonti": alerts_fonti,
        "alerts": alerts_outlier + alerts_fonti,
    })

    return df_clean, report


if __name__ == "__main__":
    # Self-test manuale: `python src/cleaning/fundamentals_cleaner.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    for ticker in get_ticker_list():
        df, report = clean_fundamentals(ticker)
        stato = report["status"]
        n_alert = len(report.get("alerts", []))
        print(f"{ticker:6s} status={stato:10s} alert={n_alert}")
        for a in report.get("alerts", []):
            print(f"        - {a}")
