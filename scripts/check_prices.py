"""
Script di verifica manuale: legge l'ultimo download raw di ogni ticker
dell'universo e stampa un riepilogo leggibile per controllare "a occhio"
se i dati sono sensati (range di date, righe, valori nulli, prezzi
plausibili). Da eseguire dopo ogni run di prices_yf.py.

Uso: python scripts/check_prices.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config.loader import get_ticker_list  # noqa: E402
from src.storage.io import load_latest_raw_prices  # noqa: E402


def check_ticker(ticker: str) -> None:
    df = load_latest_raw_prices(ticker)
    if df is None:
        print(f"{ticker:6s}  NESSUN DATO SCARICATO")
        return

    n_righe = len(df)
    data_min = df["date"].min().date()
    data_max = df["date"].max().date()
    n_nulli = df[["open", "high", "low", "close", "adj_close", "volume"]].isna().sum().sum()
    prezzo_min = df["close"].min()
    prezzo_max = df["close"].max()
    ultima_riga = df.iloc[-1]

    print(f"{ticker:6s}  righe={n_righe:5d}  periodo={data_min} -> {data_max}  "
          f"nulli={n_nulli}  close_min={prezzo_min:.2f}  close_max={prezzo_max:.2f}")
    print(f"        ultima chiusura: {ultima_riga['date'].date()} "
          f"close={ultima_riga['close']:.2f}  volume={int(ultima_riga['volume']):,}")

    # controlli minimi di plausibilita', gia' un embrione della validazione
    # che scriveremo in modo strutturato in src/cleaning/
    problemi = []
    if n_nulli > 0:
        problemi.append(f"{n_nulli} valori nulli")
    if (df["close"] <= 0).any():
        problemi.append("prezzi <= 0 presenti")
    if prezzo_max / prezzo_min > 20:
        problemi.append("rapporto max/min sospetto (>20x, controllare split)")

    if problemi:
        print(f"        ATTENZIONE: {', '.join(problemi)}")
    print()


if __name__ == "__main__":
    tickers = get_ticker_list()
    print(f"Controllo {len(tickers)} ticker (ultimo download disponibile per ciascuno)\n")
    for ticker in tickers:
        check_ticker(ticker)
