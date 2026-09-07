"""
Feature di base: volatilita' storica rolling (punto 9.7 del documento di
progetto). Calcolata come deviazione standard dei rendimenti logaritmici
giornalieri (da src/features/returns.py, quindi in ultima analisi dai dati
CLEAN) su finestre di 20 e 60 giorni di borsa, annualizzata con sqrt(252).

Si usano i rendimenti logaritmici e non quelli semplici perche' sono
additivi nel tempo: la loro deviazione standard scala correttamente con la
radice del tempo, che e' esattamente l'ipotesi dietro l'annualizzazione.

Stessa interfaccia e filosofia di returns.py: compute_volatility(ticker)
-> (df, report), mai eccezioni per un singolo ticker, salvataggio a carico
del chiamante via src/storage/io.py. C'e' anche compute_features(ticker),
che unisce rendimenti e volatilita' in un unico DataFrame: e' quello che
la pipeline salva con save_features, cosi' i moduli a valle trovano tutte
le feature di base in un file solo.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.features.returns import compute_returns  # noqa: E402

# Finestre rolling in giorni di borsa (vedi sezione 9.7: una o due finestre
# standard, qui 20 ~ un mese e 60 ~ un trimestre).
FINESTRE_GIORNI = (20, 60)

# Giorni di borsa in un anno, per l'annualizzazione: vol_annua = vol_giornaliera * sqrt(252).
GIORNI_BORSA_ANNO = 252


def compute_volatility(ticker: str,
                       returns_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame | None, dict]:
    """
    Calcola la volatilita' storica rolling annualizzata per un ticker, su
    finestre di 20 e 60 giorni (colonne volatility_20d, volatility_60d).
    Ogni finestra richiede il numero pieno di osservazioni (min_periods =
    finestra): finche' non ci sono, il valore resta NaN — meglio nessun
    numero che un numero calcolato su una finestra monca.

    returns_df permette di riusare i rendimenti gia' calcolati dal chiamante
    (evita di ricalcolarli quando si producono entrambe le feature nello
    stesso run); se None vengono calcolati qui via compute_returns.

    Ritorna (df, report). df ha colonne: date, ticker, volatility_20d,
    volatility_60d. df e' None negli stessi casi di compute_returns.
    Non solleva mai eccezione.
    """
    report: dict = {"ticker": ticker}

    if returns_df is None:
        returns_df, rep_ret = compute_returns(ticker)
        if returns_df is None:
            # propaga status e alert del passo rendimenti
            report["status"] = rep_ret["status"]
            report["alerts"] = rep_ret.get("alerts", [])
            return None, report

    try:
        df = returns_df[["date", "ticker"]].copy()

        alerts = []
        for finestra in FINESTRE_GIORNI:
            col = f"volatility_{finestra}d"
            df[col] = (returns_df["log_return"]
                       .rolling(finestra, min_periods=finestra)
                       .std(ddof=1) * np.sqrt(GIORNI_BORSA_ANNO))
            n_validi = int(df[col].notna().sum())
            if n_validi == 0:
                alerts.append(f"{col}: nessun valore calcolabile (serie piu' corta di {finestra} rendimenti validi)")

        if len(df) < max(FINESTRE_GIORNI):
            alerts.append(f"serie corta ({len(df)} righe < {max(FINESTRE_GIORNI)})")

        ultime = {f"volatility_{w}d": (round(float(df[f'volatility_{w}d'].iloc[-1]), 4)
                                        if pd.notna(df[f"volatility_{w}d"].iloc[-1]) else None)
                  for w in FINESTRE_GIORNI}

        report.update({
            "status": "ok",
            "righe": len(df),
            "ultima_data": df["date"].max().strftime("%Y-%m-%d"),
            "volatilita_ultima": ultime,
            "alerts": alerts,
        })
        return df, report

    except Exception as e:
        report["status"] = "errore"
        report["alerts"] = [f"errore nel calcolo della volatilita': {e}"]
        return None, report


def compute_features(ticker: str) -> tuple[pd.DataFrame | None, dict]:
    """
    Calcola in un colpo solo tutte le feature di base per un ticker
    (rendimenti + volatilita') e le unisce in un unico DataFrame con
    colonne: date, ticker, adj_close, simple_return, log_return,
    volatility_20d, volatility_60d. E' questo DataFrame che va salvato con
    save_features e riusato tale e quale da screening/backtest/monitoraggio.

    Il report combina quelli dei due calcoli ("returns" e "volatility") piu'
    uno status complessivo e la lista unica degli alert. Non solleva mai
    eccezione.
    """
    df_ret, rep_ret = compute_returns(ticker)
    if df_ret is None:
        return None, {"ticker": ticker, "status": rep_ret["status"],
                      "returns": rep_ret, "volatility": None,
                      "alerts": rep_ret.get("alerts", [])}

    df_vol, rep_vol = compute_volatility(ticker, returns_df=df_ret)
    if df_vol is None:
        return None, {"ticker": ticker, "status": rep_vol["status"],
                      "returns": rep_ret, "volatility": rep_vol,
                      "alerts": rep_ret.get("alerts", []) + rep_vol.get("alerts", [])}

    df = df_ret.merge(df_vol.drop(columns=["ticker"]), on="date", how="left")

    report = {
        "ticker": ticker,
        "status": "ok",
        "righe": len(df),
        "ultima_data": rep_ret["ultima_data"],
        "returns": rep_ret,
        "volatility": rep_vol,
        "alerts": rep_ret.get("alerts", []) + rep_vol.get("alerts", []),
    }
    return df, report


if __name__ == "__main__":
    # Self-test manuale: `python src/features/volatility.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    for ticker in get_ticker_list():
        df, report = compute_volatility(ticker)
        stato = report["status"]
        n_alert = len(report.get("alerts", []))
        extra = ""
        if df is not None:
            v = report.get("volatilita_ultima", {})
            extra = f" vol20={v.get('volatility_20d')} vol60={v.get('volatility_60d')}"
        print(f"{ticker:6s} status={stato:12s} alert={n_alert}{extra}")
        for a in report.get("alerts", []):
            print(f"        - {a}")
