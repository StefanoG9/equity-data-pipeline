"""
Modulo di pulizia per i prezzi OHLCV. Legge l'ultimo raw via
src/storage/io.py (load_latest_raw_prices), applica una serie di controlli
e correzioni, e ritorna sia il DataFrame pulito sia un report dettagliato
di cosa e' stato corretto o solo segnalato (niente e' mai silenzioso: ogni
intervento finisce nel report). Il salvataggio del risultato passa da
src/storage/io.py (save_clean_prices): questo modulo si limita a
calcolare, decide chi lo chiama se e quando persistere (per ora
scripts/check_clean.py).

Le colonne trattate sono quelle dello schema comune definito in
src/ingestion/prices_yf.py (SCHEMA_COLUMNS): date, ticker, open, high, low,
close, adj_close, volume, currency.

Non solleva mai eccezione per un singolo ticker: in caso di problema il
report segnala lo status e la funzione ritorna (None, report), cosi' un
chiamante che itera su piu' ticker puo' proseguire con gli altri (stesso
spirito delle funzioni di ingestion, vedi prices_yf.py/fundamentals_av.py).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.storage.io import load_latest_raw_prices  # noqa: E402

NUMERIC_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]

# Buchi di al massimo questa lunghezza (righe nulle consecutive dentro la
# serie) vengono forward-filled; oltre questa soglia lasciamo il buco
# com'e' (non ha senso "inventare" tanti giorni di prezzo consecutivi) e lo
# segnaliamo soltanto.
MAX_FFILL_NULLI_CONSECUTIVI = 3

# Un buco di questa lunghezza o piu' in giorni *lavorativi* mancanti (gia'
# al netto dei weekend, calcolati con pandas bdate_range) e' considerato
# anomalo. Le festivita' di borsa normali (Natale, Independence Day, ecc.)
# non superano quasi mai 1-2 giorni lavorativi consecutivi: 5+ giorni
# lavorativi di fila mancanti puntano quasi certamente a un problema nel
# download piuttosto che al calendario festivo.
BUCO_ANOMALO_GIORNI_LAVORATIVI = 5

# Un salto superiore a questo fattore (in valore assoluto, sia in su che in
# giu') nel rapporto close/adj_close da un giorno al successivo e' il
# segno di uno split (o reverse split) non ancora riflesso nell'adj_close
# scaricato, oppure di un errore di dato. E' piu' solido del controllo
# globale "max/min > 20x" gia' presente in scripts/check_prices.py, perche'
# localizza la data esatta del salto invece di confondere un vero split con
# una normale crescita di prezzo distribuita su piu' anni.
SPLIT_JUMP_FATTORE = 1.5


def _rimuovi_duplicati(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Rimuove righe duplicate per data, tenendo l'ultima occorrenza."""
    n_prima = len(df)
    df = df.drop_duplicates(subset="date", keep="last")
    return df, n_prima - len(df)


def _ordina_e_verifica(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Ordina per data ascendente. Ritorna anche se non lo era gia'."""
    era_ordinato = bool(df["date"].is_monotonic_increasing)
    df = df.sort_values("date").reset_index(drop=True)
    return df, not era_ordinato


def _trova_buchi_anomali(df: pd.DataFrame) -> list[dict]:
    """
    Confronta le date presenti con il calendario dei giorni lavorativi
    (business day) tra il minimo e il massimo della serie. Ogni run di
    giorni lavorativi mancanti consecutivi piu' lungo della soglia
    BUCO_ANOMALO_GIORNI_LAVORATIVI viene riportato (con data di inizio,
    fine, e numero di giorni mancanti). Buchi piu' corti sono considerate
    normali festivita' di borsa e non vengono segnalati qui.
    """
    if df.empty:
        return []

    attesi = pd.bdate_range(df["date"].min(), df["date"].max())
    presenti = set(df["date"])
    mancanti = pd.DatetimeIndex(sorted(d for d in attesi if d not in presenti))

    if len(mancanti) == 0:
        return []

    # Le posizioni delle date mancanti dentro il calendario atteso: run di
    # date mancanti consecutive hanno posizioni consecutive, quindi
    # "posizione - indice progressivo" e' costante all'interno di un run
    # (stesso trucco usato piu' sotto per i run di nulli).
    posizioni = attesi.get_indexer(mancanti)
    gruppi = posizioni - np.arange(len(posizioni))

    buchi = []
    for _, idx_gruppo in pd.Series(range(len(posizioni))).groupby(gruppi):
        run = mancanti[idx_gruppo.to_numpy()]
        if len(run) >= BUCO_ANOMALO_GIORNI_LAVORATIVI:
            buchi.append({
                "da": run[0].strftime("%Y-%m-%d"),
                "a": run[-1].strftime("%Y-%m-%d"),
                "giorni_lavorativi_mancanti": int(len(run)),
            })

    return buchi


def _gestisci_nulli(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    """
    Per ciascuna colonna numerica: forward-fill solo le run di nulli
    consecutivi lunghe al massimo MAX_FFILL_NULLI_CONSECUTIVI; i buchi piu'
    lunghi (o i nulli iniziali di serie, che ffill non puo' comunque
    riempire) restano nulli e vengono contati a parte.
    Ritorna (df_corretto, filled_counts, remaining_counts): entrambi dict
    colonna -> numero di valori, presenti solo per le colonne toccate.
    """
    df = df.copy()
    filled_counts = {}
    remaining_counts = {}

    for col in NUMERIC_COLUMNS:
        nulli_prima = df[col].isna()
        if not nulli_prima.any():
            continue

        # Gruppi di run: il contatore si incrementa a ogni valore non
        # nullo, cosi' ogni blocco di nulli consecutivi condivide lo stesso
        # id di gruppo del valore non nullo che lo precede.
        gruppo = (~nulli_prima).cumsum()
        lunghezza_run = nulli_prima.groupby(gruppo).transform("sum")
        da_riempire = nulli_prima & (lunghezza_run <= MAX_FFILL_NULLI_CONSECUTIVI)

        if da_riempire.any():
            df.loc[da_riempire, col] = df[col].ffill()[da_riempire]

        nulli_dopo = df[col].isna()
        n_riempiti = int((nulli_prima & ~nulli_dopo).sum())
        n_rimasti = int(nulli_dopo.sum())

        if n_riempiti:
            filled_counts[col] = n_riempiti
        if n_rimasti:
            remaining_counts[col] = n_rimasti

    return df, filled_counts, remaining_counts


def _rileva_split_non_aggiustati(df: pd.DataFrame) -> list[dict]:
    """
    Cerca salti bruschi nel rapporto close/adj_close: normalmente questo
    rapporto e' stabile (o si muove lentamente per via dei dividendi); se
    cambia bruscamente da un giorno al successivo di un fattore >=
    SPLIT_JUMP_FATTORE (o il suo reciproco), e' quasi certamente uno split
    (o reverse split) non ancora riflesso in adj_close al momento del
    download.
    """
    validi = df["adj_close"].notna() & (df["adj_close"] != 0) & df["close"].notna()
    if validi.sum() < 2:
        return []

    rapporto = pd.Series(np.nan, index=df.index)
    rapporto[validi] = df.loc[validi, "close"] / df.loc[validi, "adj_close"]
    variazione = rapporto / rapporto.shift(1)

    eventi = []
    for idx in df.index:
        v = variazione.loc[idx]
        if pd.isna(v):
            continue
        if v >= SPLIT_JUMP_FATTORE or v <= 1 / SPLIT_JUMP_FATTORE:
            eventi.append({
                "data": df.loc[idx, "date"].strftime("%Y-%m-%d"),
                "rapporto_prima": round(float(rapporto.shift(1).loc[idx]), 4),
                "rapporto_dopo": round(float(rapporto.loc[idx]), 4),
                "fattore_stimato": round(float(v), 2),
            })

    return eventi


def clean_prices(ticker: str) -> tuple[pd.DataFrame | None, dict]:
    """
    Pulisce l'ultimo download raw di prezzi per un ticker:
      1. rimuove duplicati per data (tiene l'ultima occorrenza);
      2. verifica/ripristina l'ordinamento cronologico;
      3. segnala buchi anomali nei giorni di borsa (vedi
         _trova_buchi_anomali);
      4. gestisce i valori nulli: forward-fill dei buchi brevi, segnalazione
         di quelli lasciati nulli;
      5. segnala split non aggiustati sospetti confrontando close e
         adj_close (vedi _rileva_split_non_aggiustati).

    Ritorna (df_pulito, report). df_pulito e' None se non c'e' nessun raw
    disponibile (report["status"] == "nessun_dato") o in caso di errore
    imprevisto (report["status"] == "errore"). Non solleva mai eccezione.
    """
    report: dict = {"ticker": ticker}

    try:
        raw = load_latest_raw_prices(ticker)
    except Exception as e:
        report["status"] = "errore"
        report["alerts"] = [f"errore in lettura raw: {e}"]
        return None, report

    if raw is None or raw.empty:
        report["status"] = "nessun_dato"
        report["alerts"] = ["nessun dato raw disponibile"]
        return None, report

    report["righe_raw"] = len(raw)

    df, n_duplicati = _rimuovi_duplicati(raw)
    df, non_ordinato = _ordina_e_verifica(df)
    df, filled_counts, remaining_counts = _gestisci_nulli(df)
    buchi = _trova_buchi_anomali(df)
    split_sospetti = _rileva_split_non_aggiustati(df)

    alerts = []
    if n_duplicati:
        alerts.append(f"{n_duplicati} righe duplicate per data rimosse")
    if non_ordinato:
        alerts.append("le date non erano ordinate: riordinate cronologicamente")
    if filled_counts:
        dettagli = ", ".join(f"{c}: {n}" for c, n in filled_counts.items())
        alerts.append(f"valori nulli forward-filled (buchi brevi, <= {MAX_FFILL_NULLI_CONSECUTIVI} righe): {dettagli}")
    if remaining_counts:
        dettagli = ", ".join(f"{c}: {n}" for c, n in remaining_counts.items())
        alerts.append(f"valori nulli rimasti (buchi troppo lunghi per forward-fill, o a inizio serie): {dettagli}")
    if buchi:
        dettagli = "; ".join(f"{b['da']} -> {b['a']} ({b['giorni_lavorativi_mancanti']} gg lavorativi)" for b in buchi)
        alerts.append(f"buchi anomali nei giorni di borsa: {dettagli}")
    if split_sospetti:
        dettagli = "; ".join(f"{s['data']} (fattore ~{s['fattore_stimato']:.2f}x)" for s in split_sospetti)
        alerts.append(f"possibile split non aggiustato: {dettagli}")
    if (df["close"] <= 0).any():
        alerts.append("prezzi close <= 0 presenti (non corretto automaticamente, richiede verifica manuale)")

    report.update({
        "status": "ok",
        "righe_clean": len(df),
        "duplicati_rimossi": n_duplicati,
        "date_riordinate": non_ordinato,
        "nulli_forward_filled": filled_counts,
        "nulli_rimasti": remaining_counts,
        "buchi_anomali": buchi,
        "split_sospetti": split_sospetti,
        "alerts": alerts,
    })

    return df, report


if __name__ == "__main__":
    # Self-test manuale: `python src/cleaning/prices_cleaner.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    for ticker in get_ticker_list():
        df, report = clean_prices(ticker)
        stato = report["status"]
        n_alert = len(report.get("alerts", []))
        print(f"{ticker:6s} status={stato:10s} alert={n_alert}")
        for a in report.get("alerts", []):
            print(f"        - {a}")
