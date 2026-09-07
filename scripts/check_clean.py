"""
Script di verifica manuale del livello di pulizia: per ogni ticker
dell'universo esegue clean_prices/clean_fundamentals (src/cleaning/), salva
il risultato pulito (via src/storage/io.py: save_clean_prices /
save_clean_fundamentals) e stampa un confronto raw -> clean leggibile, per
controllare "a occhio" cosa e' stato corretto o solo segnalato. Stesso
spirito di check_prices.py/check_fundamentals.py.

Uso: python scripts/check_clean.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cleaning.fundamentals_cleaner import clean_fundamentals  # noqa: E402
from src.cleaning.prices_cleaner import clean_prices  # noqa: E402
from src.config.loader import get_ticker_list  # noqa: E402
from src.storage.io import (  # noqa: E402
    load_latest_raw_fundamentals,
    load_latest_raw_prices,
    save_clean_fundamentals,
    save_clean_prices,
)


def check_prices_ticker(ticker: str) -> None:
    raw = load_latest_raw_prices(ticker)
    df_clean, report = clean_prices(ticker)

    if report["status"] == "nessun_dato":
        print(f"{ticker:6s}  PREZZI        nessun raw disponibile")
        return
    if report["status"] == "errore":
        print(f"{ticker:6s}  PREZZI        ERRORE: {report['alerts']}")
        return

    path = save_clean_prices(ticker, df_clean)
    n_raw = report["righe_raw"]
    n_clean = report["righe_clean"]
    n_nulli_raw = int(raw[["open", "high", "low", "close", "adj_close", "volume"]].isna().sum().sum())
    n_nulli_clean = int(df_clean[["open", "high", "low", "close", "adj_close", "volume"]].isna().sum().sum())

    print(f"{ticker:6s}  PREZZI        righe raw={n_raw:5d} -> clean={n_clean:5d}  "
          f"nulli raw={n_nulli_raw} -> clean={n_nulli_clean}  salvato in {path.name}")

    if report["alerts"]:
        for a in report["alerts"]:
            print(f"                - {a}")
    else:
        print("                nessuna correzione o segnalazione: dati gia' puliti")
    print()


def check_fundamentals_ticker(ticker: str) -> None:
    raw = load_latest_raw_fundamentals(ticker)
    df_clean, report = clean_fundamentals(ticker)

    if report["status"] == "nessun_dato":
        print(f"{ticker:6s}  FONDAMENTALI  nessun raw disponibile")
        return
    if report["status"] == "errore":
        print(f"{ticker:6s}  FONDAMENTALI  ERRORE: {report['alerts']}")
        return

    path = save_clean_fundamentals(ticker, df_clean)
    riga_raw = raw.iloc[-1]
    riga_clean = df_clean.iloc[-1]

    print(f"{ticker:6s}  FONDAMENTALI  fonte={report['fonte']}  data={report['data']}  "
          f"salvato in {path.name}")

    # mostra solo i campi effettivamente cambiati tra raw e clean (nullati
    # dal controllo outlier), cosi' il confronto resta leggibile
    campi = ["pe_ratio", "eps", "profit_margin", "operating_margin",
              "dividend_yield", "debt_to_equity", "market_cap"]
    cambiati = [c for c in campi if riga_raw.get(c) != riga_clean.get(c)
                and not (pd_isna(riga_raw.get(c)) and pd_isna(riga_clean.get(c)))]
    if cambiati:
        for c in cambiati:
            print(f"                {c}: raw={riga_raw.get(c)!r} -> clean={riga_clean.get(c)!r}")

    if report["alerts"]:
        for a in report["alerts"]:
            print(f"                - {a}")
    else:
        print("                nessuna correzione o segnalazione: dati gia' puliti")
    print()


def pd_isna(val) -> bool:
    import pandas as pd
    try:
        return bool(pd.isna(val))
    except TypeError:
        return False


if __name__ == "__main__":
    tickers = get_ticker_list()

    print(f"=== Controllo pulizia PREZZI ({len(tickers)} ticker) ===\n")
    for ticker in tickers:
        check_prices_ticker(ticker)

    print(f"=== Controllo pulizia FONDAMENTALI ({len(tickers)} ticker) ===\n")
    for ticker in tickers:
        check_fundamentals_ticker(ticker)
