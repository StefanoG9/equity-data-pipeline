"""
Script di verifica manuale: legge l'ultimo download raw di fondamentali di
ogni ticker dell'universo e stampa un riepilogo leggibile per controllare
"a occhio" se i dati sono sensati (fonte, P/E, EPS, margini, dividend
yield). Da eseguire dopo ogni run di fundamentals_av.py / fundamentals_finnhub.py.

Uso: python scripts/check_fundamentals.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config.loader import get_ticker_list  # noqa: E402
from src.storage.io import load_latest_raw_fundamentals  # noqa: E402


def _fmt(val, decimali=2) -> str:
    if val is None or val != val:  # NaN check senza importare numpy/pandas
        return "n/d"
    return f"{val:.{decimali}f}"


def check_ticker(ticker: str) -> None:
    df = load_latest_raw_fundamentals(ticker)
    if df is None:
        print(f"{ticker:6s}  NESSUN DATO SCARICATO")
        return

    riga = df.iloc[-1]
    fonte = riga.get("source", "?")
    data = riga.get("date", "?")

    print(f"{ticker:6s}  fonte={fonte:13s}  data={data}")
    print(f"        P/E={_fmt(riga.get('pe_ratio'))}  "
          f"forward_P/E={_fmt(riga.get('forward_pe'))}  "
          f"EPS={_fmt(riga.get('eps'))}  "
          f"dividend_yield={_fmt(riga.get('dividend_yield'), 4)}")
    print(f"        ricavi_TTM={_fmt(riga.get('revenue_ttm'), 0)}  "
          f"margine_utile={_fmt(riga.get('profit_margin'), 4)}  "
          f"margine_operativo={_fmt(riga.get('operating_margin'), 4)}  "
          f"debito/equity={_fmt(riga.get('debt_to_equity'))}")

    # controlli minimi di plausibilita', stesso spirito di check_prices.py
    problemi = []
    campi_chiave = ["pe_ratio", "eps", "revenue_ttm", "dividend_yield"]
    mancanti = [c for c in campi_chiave if riga.get(c) is None or riga.get(c) != riga.get(c)]
    if mancanti:
        problemi.append(f"campi mancanti: {', '.join(mancanti)}")
    if riga.get("debt_to_equity") is None:
        problemi.append("debt_to_equity assente (normale se fonte=alpha_vantage, vedi docstring modulo)")
    pe = riga.get("pe_ratio")
    if pe is not None and pe == pe and pe < 0:
        problemi.append("P/E negativo (utili negativi? controllare)")

    if problemi:
        print(f"        NOTA: {'; '.join(problemi)}")
    print()


if __name__ == "__main__":
    tickers = get_ticker_list()
    print(f"Controllo {len(tickers)} ticker (ultimo download disponibile per ciascuno)\n")
    for ticker in tickers:
        check_ticker(ticker)
