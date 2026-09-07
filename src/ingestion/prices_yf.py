"""
Unico punto di contatto con yfinance per i prezzi OHLCV.
Scarica un ticker alla volta (piu' robusto: se uno fallisce, non blocca gli
altri), normalizza le colonne allo schema comune di base, e salva il raw
tramite src/storage/io.py.
"""

import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.storage.io import save_raw_prices  # noqa: E402


# Colonne attese in output, gia' allineate allo schema comune (sezione 9.6
# del documento di progetto): date, ticker, open, high, low, close,
# adj_close, volume, currency.
SCHEMA_COLUMNS = [
    "date", "ticker", "open", "high", "low", "close", "adj_close",
    "volume", "currency",
]


def fetch_one(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """
    Scarica i prezzi di un singolo ticker da yfinance tra start e end
    (formato "YYYY-MM-DD"). Ritorna un DataFrame nello schema comune, o
    None se il download fallisce o non produce dati.
    """
    try:
        raw = yf.download(
            ticker, start=start, end=end,
            auto_adjust=False, progress=False,
        )
    except Exception as e:
        print(f"  [ERRORE] {ticker}: {e}")
        return None

    if raw is None or raw.empty:
        print(f"  [VUOTO] {ticker}: nessun dato restituito")
        return None

    # yfinance con un solo ticker puo' restituire colonne multi-livello:
    # appiattiamo per sicurezza.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    df = raw.reset_index().rename(columns={
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })
    df["ticker"] = ticker
    df["currency"] = "USD"  # ipotesi per l'universo attuale, tutto NYSE/NASDAQ

    df = df[SCHEMA_COLUMNS]
    return df


def fetch_prices(tickers: list[str], start: str, end: str) -> dict:
    """
    Scarica i prezzi per una lista di ticker, salva ciascuno come raw
    (via storage/io.py), e ritorna un riepilogo dell'esito per ticker:
    {"AAPL": "ok", "XYZ": "errore", ...}. Non solleva mai eccezione per un
    singolo ticker fallito: prosegue con gli altri.
    """
    esiti = {}
    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] Scarico {ticker}...")
        df = fetch_one(ticker, start, end)

        if df is None:
            esiti[ticker] = "errore"
            continue

        path = save_raw_prices(ticker, df)
        esiti[ticker] = "ok"
        print(f"  -> {len(df)} righe salvate in {path.name}")

        # piccola pausa per non martellare l'endpoint, buona norma anche
        # se yfinance non ha un rate limit ufficiale
        time.sleep(0.3)

    return esiti


if __name__ == "__main__":
    # Self-test manuale: `python src/ingestion/prices_yf.py`
    from datetime import datetime, timedelta

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    tickers = get_ticker_list()
    # `end` e' ESCLUSO per yfinance: usiamo domani per includere la
    # chiusura di oggi in un run serale (stessa logica di run_daily_update).
    domani = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    esiti = fetch_prices(tickers, start="2020-01-01", end=domani)

    print("\n--- Riepilogo ---")
    for ticker, esito in esiti.items():
        print(f"  {ticker:6s} {esito}")

    ok = sum(1 for e in esiti.values() if e == "ok")
    print(f"\n{ok}/{len(tickers)} ticker scaricati con successo")
