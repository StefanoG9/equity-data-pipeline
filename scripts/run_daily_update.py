"""
Unico comando dell'aggiornamento serale: scarica i prezzi dell'universo,
esegue la pulizia (prezzi + fondamentali, se disponibili), calcola le
feature di base (rendimenti + volatilita', sezione 9.7), genera il report
di qualita' dati (sezione 9.10) e infine il report HTML. Pensato per
essere lanciato manualmente ora, e schedulato (cron / Task Scheduler /
scheduling Cowork) piu' avanti.

Uso: python scripts/run_daily_update.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cleaning.fundamentals_cleaner import clean_fundamentals  # noqa: E402
from src.cleaning.prices_cleaner import clean_prices  # noqa: E402
from src.config.loader import get_ticker_list  # noqa: E402
from src.features.volatility import compute_features  # noqa: E402
from src.ingestion.prices_yf import fetch_prices  # noqa: E402
from src.reporting.daily_report import generate_report  # noqa: E402
from src.reporting.quality_report import generate_quality_report  # noqa: E402
from src.storage.io import (  # noqa: E402
    save_clean_fundamentals,
    save_clean_prices,
    save_features,
)


def main() -> None:
    print(f"=== Aggiornamento serale — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    tickers = get_ticker_list()
    print(f"1/5 — Scarico prezzi per {len(tickers)} ticker...")
    # NB: per yfinance il parametro `end` e' ESCLUSO dal download: con
    # end=oggi la chiusura di oggi non arriverebbe mai. Usiamo domani, cosi'
    # un run serale (dopo la chiusura del mercato) include il dato di oggi.
    domani = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    esiti = fetch_prices(tickers, start="2020-01-01", end=domani)
    ok = sum(1 for e in esiti.values() if e == "ok")
    print(f"     {ok}/{len(tickers)} ticker aggiornati con successo\n")

    print("2/5 — Pulizia dati (prezzi + fondamentali)...")
    n_alert = 0
    report_pulizia_prezzi: dict[str, dict] = {}
    report_pulizia_fondamentali: dict[str, dict] = {}
    for ticker in tickers:
        df_p, rep_p = clean_prices(ticker)
        report_pulizia_prezzi[ticker] = rep_p
        if df_p is not None:
            save_clean_prices(ticker, df_p)
        df_f, rep_f = clean_fundamentals(ticker)
        report_pulizia_fondamentali[ticker] = rep_f
        if df_f is not None:
            save_clean_fundamentals(ticker, df_f)
        alerts = rep_p.get("alerts", []) + rep_f.get("alerts", [])
        # "nessun dato raw disponibile" sui fondamentali e' normale nei primi
        # giorni (rotazione Alpha Vantage): non lo contiamo come alert vero.
        alerts = [a for a in alerts if a != "nessun dato raw disponibile"]
        n_alert += len(alerts)
        for a in alerts:
            print(f"     [{ticker}] {a}")
    print(f"     Pulizia completata ({n_alert} segnalazioni)\n")

    print("3/5 — Calcolo feature di base (rendimenti + volatilita')...")
    n_alert_feat = 0
    report_features: dict[str, dict] = {}
    for ticker in tickers:
        df_feat, rep_feat = compute_features(ticker)
        report_features[ticker] = rep_feat
        if df_feat is not None:
            save_features(ticker, df_feat)
        for a in rep_feat.get("alerts", []):
            n_alert_feat += 1
            print(f"     [{ticker}] {a}")
    ok_feat = sum(1 for r in report_features.values() if r["status"] == "ok")
    print(f"     Feature calcolate per {ok_feat}/{len(tickers)} ticker ({n_alert_feat} segnalazioni)\n")

    print("4/5 — Genero il report di qualita' dati...")
    quality_path = generate_quality_report(
        fetch_esiti_prezzi=esiti,
        report_pulizia_prezzi=report_pulizia_prezzi,
        report_pulizia_fondamentali=report_pulizia_fondamentali,
        report_features=report_features,
    )
    print(f"     Report qualita': {quality_path}\n")

    print("5/5 — Genero il report HTML...")
    report_path = generate_report()
    print(f"     Report pronto: {report_path}\n")

    print("=== Fatto ===")
    print(f"Apri {report_path.parent.parent / 'latest.html'} per vedere l'ultimo report.")


if __name__ == "__main__":
    main()
