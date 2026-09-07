"""
Feature di base: rendimenti giornalieri (punto 9.7 del documento di
progetto). Legge l'ultima versione CLEAN dei prezzi via src/storage/io.py
(load_latest_clean_prices) e calcola rendimenti semplici e logaritmici
sull'adj_close (che incorpora split e dividendi: usare il close darebbe
falsi salti di rendimento in corrispondenza di ogni stacco).

Come per i cleaner (vedi prices_cleaner.py), questo modulo si limita a
calcolare e ritorna (df, report): il salvataggio passa da
src/storage/io.py (save_features), e decide chi chiama se e quando
persistere (scripts/run_daily_update.py e scripts/check_features.py).
Le feature vanno calcolate UNA volta qui e riusate a valle: screening,
backtest e monitoraggio non devono mai ricalcolarle per conto proprio.

Non solleva mai eccezione per un singolo ticker: in caso di problema il
report segnala lo status e la funzione ritorna (None, report), cosi' un
chiamante che itera su piu' ticker puo' proseguire con gli altri.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.storage.io import load_latest_clean_prices  # noqa: E402

# Sotto questo numero di righe la serie e' troppo corta per essere utile a
# valle (la volatilita' a 60 giorni non sarebbe nemmeno calcolabile): non e'
# un errore, ma va segnalato esplicitamente.
MIN_RIGHE_UTILI = 60


def compute_returns(ticker: str) -> tuple[pd.DataFrame | None, dict]:
    """
    Calcola i rendimenti giornalieri per un ticker a partire dall'ultima
    versione clean dei prezzi:

      - simple_return: adj_close[t] / adj_close[t-1] - 1
      - log_return:    ln(adj_close[t] / adj_close[t-1])

    I rendimenti sono calcolati solo dove adj_close e' presente e > 0: dove
    manca (o e' non positivo, che renderebbe il log non definito) il
    rendimento resta NaN e viene segnalato nel report, mai riempito in
    silenzio (stessa filosofia della pulizia, vedi sezione 9.5 del progetto).

    Ritorna (df, report). df ha colonne: date, ticker, adj_close,
    simple_return, log_return. df e' None se non esiste nessuna versione
    clean (report["status"] == "nessun_dato") o in caso di errore
    imprevisto (report["status"] == "errore"). Non solleva mai eccezione.
    """
    report: dict = {"ticker": ticker}

    try:
        clean = load_latest_clean_prices(ticker)
    except Exception as e:
        report["status"] = "errore"
        report["alerts"] = [f"errore in lettura clean: {e}"]
        return None, report

    if clean is None or clean.empty:
        report["status"] = "nessun_dato"
        report["alerts"] = ["nessuna versione clean disponibile"]
        return None, report

    try:
        df = (clean[["date", "ticker", "adj_close"]]
              .copy()
              .sort_values("date")
              .reset_index(drop=True))

        adj = df["adj_close"]
        n_nulli_adj = int(adj.isna().sum())
        n_non_positivi = int(((adj <= 0) & adj.notna()).sum())

        # Prezzi non positivi resi NaN solo per il calcolo: nel df restano
        # com'erano (il dato clean non si tocca qui), ma il rendimento su
        # quelle righe non e' definibile.
        adj_valido = adj.where(adj > 0)
        rapporto = adj_valido / adj_valido.shift(1)

        df["simple_return"] = rapporto - 1
        df["log_return"] = np.log(rapporto)

        # NaN "fisiologico" e' solo la prima riga (nessun giorno precedente):
        # tutto il resto viene da buchi in adj_close e va segnalato.
        n_nan_rendimenti = int(df["simple_return"].isna().sum()) - 1

        alerts = []
        if n_nulli_adj:
            alerts.append(f"{n_nulli_adj} adj_close nulli nel clean: rendimenti NaN su quelle righe (e le successive)")
        if n_non_positivi:
            alerts.append(f"{n_non_positivi} adj_close <= 0: rendimento non definibile, lasciato NaN")
        if n_nan_rendimenti > 0 and not (n_nulli_adj or n_non_positivi):
            alerts.append(f"{n_nan_rendimenti} rendimenti NaN oltre la prima riga (da verificare)")
        if len(df) < MIN_RIGHE_UTILI:
            alerts.append(f"serie corta ({len(df)} righe < {MIN_RIGHE_UTILI}): volatilita' a 60 giorni non calcolabile")

        report.update({
            "status": "ok",
            "righe": len(df),
            "prima_data": df["date"].min().strftime("%Y-%m-%d"),
            "ultima_data": df["date"].max().strftime("%Y-%m-%d"),
            "rendimenti_nan_oltre_prima_riga": max(n_nan_rendimenti, 0),
            "alerts": alerts,
        })
        return df, report

    except Exception as e:
        report["status"] = "errore"
        report["alerts"] = [f"errore nel calcolo dei rendimenti: {e}"]
        return None, report


if __name__ == "__main__":
    # Self-test manuale: `python src/features/returns.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    for ticker in get_ticker_list():
        df, report = compute_returns(ticker)
        stato = report["status"]
        n_alert = len(report.get("alerts", []))
        extra = ""
        if df is not None:
            ultimo = df["simple_return"].iloc[-1]
            extra = f" righe={len(df)} ultimo_rendimento={ultimo:+.4%}" if pd.notna(ultimo) else f" righe={len(df)}"
        print(f"{ticker:6s} status={stato:12s} alert={n_alert}{extra}")
        for a in report.get("alerts", []):
            print(f"        - {a}")
