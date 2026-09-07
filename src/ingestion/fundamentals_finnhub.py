"""
Punto di contatto con Finnhub per i dati fondamentali. Stessa interfaccia di
fundamentals_av.py (fetch_fundamentals(tickers) -> dict di esiti), usato come
fallback quando Alpha Vantage fallisce o ha esaurito la quota giornaliera
(vedi config/sources.yaml, sezione "fundamentals" -> fallback: finnhub).

Servono due chiamate per ticker (profile2 + metric), perche' Finnhub divide
anagrafica/market cap (profile2) dai rapporti finanziari (metric). Il piano
gratuito di Finnhub non ha un limite giornaliero documentato come Alpha
Vantage, ma un limite di burst intorno a 60 richieste/minuto: qui usiamo una
pausa prudenziale tra le chiamate invece di fidarci del numero esatto (che
puo' cambiare per piano/account). Se il tuo piano ha limiti diversi, aggiorna
SECONDS_BETWEEN_CALLS di conseguenza.

Nota sulle unita' di misura: i campi restituiti da Finnhub non sono sempre
nella stessa unita' di quelli di Alpha Vantage. Per rendere le due fonti
intercambiabili nello schema comune, qui normalizziamo:
  - margini e dividend yield: Finnhub li da' in percentuale (es. 25.3 =
    25.3%), Alpha Vantage in frazione (0.253) -> dividiamo per 100;
  - market cap: Finnhub lo da' in milioni di USD -> moltiplichiamo per 1e6;
  - revenue_ttm: Finnhub (piano gratuito) non espone il ricavo totale TTM
    direttamente, solo il ricavo per azione (revenuePerShareTTM) -> lo
    ricaviamo moltiplicando per le azioni in circolazione (shareOutstanding,
    anch'esso in milioni su Finnhub).
Queste mappature sono basate sulla documentazione pubblica Finnhub al
momento della scrittura: vale la pena ricontrollarle su un output reale
appena hai una chiave, perche' i nomi dei campi non sono garantiti stabili.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.ingestion.fundamentals_av import FUNDAMENTALS_SCHEMA_COLUMNS  # noqa: E402
from src.storage.io import save_raw_fundamentals  # noqa: E402

import pandas as pd

load_dotenv()

BASE_URL = "https://finnhub.io/api/v1"
SOURCE_NAME = "finnhub"

# Pausa prudenziale tra chiamate (vedi docstring: nessun limite giornaliero
# documentato, ma un limite al minuto sul piano gratuito).
SECONDS_BETWEEN_CALLS = 1.1


def _oggi() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get(endpoint: str, params: dict, api_key: str) -> tuple[dict | None, str]:
    """
    GET generico verso Finnhub. Ritorna (json, esito), esito uno tra
    "ok", "vuoto", "errore", "limite_raggiunto" (HTTP 429).
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            params={**params, "token": api_key},
            timeout=15,
        )
    except Exception as e:
        return None, f"errore: {e}"

    if resp.status_code == 429:
        return None, "limite_raggiunto"
    try:
        resp.raise_for_status()
    except Exception as e:
        return None, f"errore: {e}"

    data = resp.json()
    if not data:
        return None, "vuoto"
    return data, "ok"


def _num(d: dict, *chiavi) -> float | None:
    """Ritorna il primo valore numerico trovato tra le chiavi candidate."""
    for chiave in chiavi:
        val = d.get(chiave)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def fetch_one(ticker: str, api_key: str) -> tuple[pd.DataFrame | None, str]:
    """
    Scarica profilo + metriche di un singolo ticker da Finnhub. Ritorna
    (DataFrame, esito) nello stesso formato di fundamentals_av.fetch_one.
    """
    profile, esito = _get("stock/profile2", {"symbol": ticker}, api_key)
    if esito == "limite_raggiunto":
        return None, "limite_raggiunto"
    if esito != "ok":
        print(f"  [{esito.upper()}] {ticker} (profile2)")
        return None, "errore" if esito.startswith("errore") else esito
    time.sleep(SECONDS_BETWEEN_CALLS)

    metric_resp, esito = _get(
        "stock/metric", {"symbol": ticker, "metric": "all"}, api_key,
    )
    if esito == "limite_raggiunto":
        return None, "limite_raggiunto"
    if esito != "ok":
        print(f"  [{esito.upper()}] {ticker} (metric)")
        return None, "errore" if esito.startswith("errore") else esito

    metric = metric_resp.get("metric") or {}
    if not metric and not profile:
        return None, "vuoto"

    revenue_per_share = _num(metric, "revenuePerShareTTM")
    shares_outstanding_milioni = _num(profile, "shareOutstanding")
    revenue_ttm = None
    if revenue_per_share is not None and shares_outstanding_milioni is not None:
        revenue_ttm = revenue_per_share * shares_outstanding_milioni * 1_000_000

    market_cap_milioni = _num(profile, "marketCapitalization")
    market_cap = market_cap_milioni * 1_000_000 if market_cap_milioni is not None else None

    profit_margin = _num(metric, "netProfitMarginTTM")
    if profit_margin is not None:
        profit_margin /= 100

    operating_margin = _num(metric, "operatingMarginTTM")
    if operating_margin is not None:
        operating_margin /= 100

    dividend_yield = _num(metric, "dividendYieldIndicatedAnnual", "currentDividendYieldTTM")
    if dividend_yield is not None:
        dividend_yield /= 100

    debt_to_equity = _num(
        metric,
        "totalDebt/totalEquityAnnual",
        "totalDebt/totalEquityQuarterly",
        "totalDebtToEquity",
    )

    row = {
        "date": _oggi(),
        "ticker": ticker,
        "pe_ratio": _num(metric, "peTTM", "peBasicExclExtraTTM", "peExclExtraTTM"),
        "forward_pe": _num(metric, "peForward"),
        "eps": _num(metric, "epsTTM", "epsInclExtraItemsTTM", "epsBasicExclExtraItemsTTM"),
        "revenue_ttm": revenue_ttm,
        "profit_margin": profit_margin,
        "operating_margin": operating_margin,
        "debt_to_equity": debt_to_equity,
        "dividend_yield": dividend_yield,
        "market_cap": market_cap,
        "latest_quarter": None,  # non disponibile da profile2/metric
        "source": SOURCE_NAME,
    }
    df = pd.DataFrame([row])[FUNDAMENTALS_SCHEMA_COLUMNS]
    return df, "ok"


def fetch_fundamentals(tickers: list[str]) -> dict:
    """
    Scarica i fondamentali per una lista di ticker da Finnhub (fallback).
    Stessa interfaccia di fundamentals_av.fetch_fundamentals: salva ogni
    risultato come raw e ritorna un riepilogo per ticker. Non implementa
    rotazione perche' Finnhub free tier non ha un limite giornaliero
    documentato (vedi docstring del modulo); rispetta solo il ritmo al
    minuto tra una chiamata e l'altra.
    """
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        print("  [ERRORE] FINNHUB_API_KEY non impostata (vedi .env.example)")
        return {t: "errore" for t in tickers}

    esiti = {}
    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] Scarico fondamentali {ticker} (Finnhub)...")
        df, esito = fetch_one(ticker, api_key)

        if esito == "limite_raggiunto":
            esiti[ticker] = "limite_raggiunto"
            for rimanente in tickers[i + 1:]:
                esiti[rimanente] = "limite_raggiunto"
            break

        if df is None:
            esiti[ticker] = esito
        else:
            path = save_raw_fundamentals(ticker, df)
            esiti[ticker] = "ok"
            print(f"  -> salvato in {path.name}")

        if i < len(tickers) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    return esiti


if __name__ == "__main__":
    # Self-test manuale: `python src/ingestion/fundamentals_finnhub.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    tickers = get_ticker_list()
    esiti = fetch_fundamentals(tickers)

    print("\n--- Riepilogo ---")
    for ticker, esito in esiti.items():
        print(f"  {ticker:6s} {esito}")

    ok = sum(1 for e in esiti.values() if e == "ok")
    print(f"\n{ok}/{len(tickers)} ticker scaricati con successo")
