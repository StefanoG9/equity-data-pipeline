"""
Script di verifica manuale del layer di feature: per ogni ticker
dell'universo esegue compute_features (src/features/volatility.py, che
unisce rendimenti e volatilita'), salva il risultato (via
src/storage/io.py: save_features) e stampa un riepilogo leggibile per
controllare "a occhio" i valori. Stesso spirito di check_clean.py.

Uso: python scripts/check_features.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config.loader import get_ticker_list  # noqa: E402
from src.features.volatility import compute_features  # noqa: E402
from src.storage.io import save_features  # noqa: E402


def _fmt(val, spec: str = ".4f") -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "n/d"
    return format(val, spec)


def check_features_ticker(ticker: str) -> None:
    df, report = compute_features(ticker)

    if report["status"] == "nessun_dato":
        print(f"{ticker:6s}  FEATURES      nessun dato clean disponibile")
        return
    if report["status"] == "errore":
        print(f"{ticker:6s}  FEATURES      ERRORE: {report['alerts']}")
        return

    path = save_features(ticker, df)

    ultima = df.iloc[-1]
    n_nan_ret = int(df["simple_return"].isna().sum())
    n_nan_vol20 = int(df["volatility_20d"].isna().sum())
    n_nan_vol60 = int(df["volatility_60d"].isna().sum())

    print(f"{ticker:6s}  FEATURES      righe={len(df):5d}  "
          f"{report['returns']['prima_data']} -> {report['ultima_data']}  salvato in {path.name}")
    print(f"                ultimo rendimento: semplice={_fmt(ultima['simple_return'], '+.4%')}  "
          f"log={_fmt(ultima['log_return'], '+.4f')}")
    print(f"                volatilita' annualizzata: 20gg={_fmt(ultima['volatility_20d'], '.2%')}  "
          f"60gg={_fmt(ultima['volatility_60d'], '.2%')}")
    print(f"                NaN: rendimenti={n_nan_ret} (attesi >=1: prima riga)  "
          f"vol20={n_nan_vol20} (attesi >=20)  vol60={n_nan_vol60} (attesi >=60)")

    if report["alerts"]:
        for a in report["alerts"]:
            print(f"                - {a}")
    else:
        print("                nessuna segnalazione")
    print()


if __name__ == "__main__":
    tickers = get_ticker_list()

    print(f"=== Controllo FEATURE di base ({len(tickers)} ticker) ===\n")
    for ticker in tickers:
        check_features_ticker(ticker)
