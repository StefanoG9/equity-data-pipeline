"""
Punto di contatto con Alpha Vantage per i dati fondamentali (endpoint
OVERVIEW): P/E, EPS, ricavi, margini, dividend yield. Fonte primaria per i
fondamentali secondo config/sources.yaml.

Rate limit Alpha Vantage (piano gratuito, vedi config/sources.yaml):
  - 5 richieste al minuto
  - 25 richieste al giorno
Questo modulo rispetta entrambi i limiti:
  - tra una chiamata e l'altra aspetta abbastanza da restare sotto 5/minuto;
  - tiene un contatore giornaliero persistito su disco (si azzera da solo al
    cambio di data) e non lo supera mai, nemmeno chiamando lo script piu'
    volte nello stesso giorno;
  - se l'universo ha piu' ticker della quota giornaliera, ruota: ogni run
    sceglie prima i ticker senza dati o con il download raw piu' vecchio,
    cosi' nell'arco di piu' giorni tutto l'universo viene coperto.

Nota sui campi: l'endpoint OVERVIEW di Alpha Vantage non espone un
indicatore di debito/leva finanziaria (non c'e' "DebtToEquity" ne' un totale
del debito: quello richiederebbe l'endpoint BALANCE_SHEET, che consumerebbe
una seconda chiamata per ticker e dimezzerebbe la quota giornaliera). Per
questo "debt_to_equity" qui e' sempre None: viene eventualmente popolato dal
fallback Finnhub (src/ingestion/fundamentals_finnhub.py), che lo fornisce
con una sola chiamata aggiuntiva.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config.loader import load_sources_config  # noqa: E402
from src.storage.io import (  # noqa: E402
    DATA_DIR,
    list_raw_fundamentals_downloads,
    save_raw_fundamentals,
)

load_dotenv()

API_URL = "https://www.alphavantage.co/query"
SOURCE_NAME = "alpha_vantage"

# Schema comune ai fondamentali, condiviso con fundamentals_finnhub.py cosi'
# che le due fonti siano intercambiabili a valle (cleaning/report non devono
# sapere da dove viene il dato).
FUNDAMENTALS_SCHEMA_COLUMNS = [
    "date", "ticker", "pe_ratio", "forward_pe", "eps", "revenue_ttm",
    "profit_margin", "operating_margin", "debt_to_equity", "dividend_yield",
    "market_cap", "latest_quarter", "source",
]

# File di stato per il contatore giornaliero di richieste usate.
USAGE_STATE_PATH = DATA_DIR / "raw" / "fundamentals" / ".av_usage.json"

# Margine di sicurezza sopra il minimo teorico (60s / 5 richieste = 12s).
SECONDS_BETWEEN_CALLS = 13


def _oggi() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_usage_state() -> dict:
    """
    Legge {"date": "YYYY-MM-DD", "used": N} dal file di stato. Se il file
    non esiste o si riferisce a un giorno diverso da oggi, riparte da zero:
    la quota giornaliera di Alpha Vantage si resetta a mezzanotte UTC.
    """
    if USAGE_STATE_PATH.exists():
        try:
            state = json.loads(USAGE_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    else:
        state = {}

    if state.get("date") != _oggi():
        state = {"date": _oggi(), "used": 0}
    return state


def _save_usage_state(state: dict) -> None:
    USAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def get_remaining_quota_today() -> int:
    """Quante richieste Alpha Vantage restano disponibili per oggi."""
    sources = load_sources_config()
    limite_giornaliero = sources["fundamentals"]["rate_limit"]["per_giorno"]
    state = _load_usage_state()
    return max(0, limite_giornaliero - state.get("used", 0))


def _registra_chiamata() -> None:
    state = _load_usage_state()
    state["used"] = state.get("used", 0) + 1
    _save_usage_state(state)


def _ultimo_download_timestamp(ticker: str) -> str:
    """
    Ritorna il timestamp (dal nome file) dell'ultimo download raw
    disponibile per il ticker, o stringa vuota se non e' mai stato scaricato.
    Una stringa vuota ordina prima di qualunque timestamp reale, quindi i
    ticker mai scaricati vengono scelti per primi dalla rotazione.
    """
    files = list_raw_fundamentals_downloads(ticker)
    if not files:
        return ""
    return files[-1].stem


def select_rotation(tickers: list[str], quota: int) -> list[str]:
    """
    Se l'universo sta dentro la quota, ritorna tutti i ticker. Altrimenti
    sceglie i `quota` ticker "piu' vecchi" (mai scaricati, o scaricati meno
    di recente), cosi' su piu' giorni consecutivi l'intero universo viene
    coperto a rotazione senza mai superare il limite giornaliero.
    """
    if len(tickers) <= quota:
        return list(tickers)

    ordinati = sorted(tickers, key=_ultimo_download_timestamp)
    return ordinati[:quota]


def _parse_overview(data: dict, ticker: str) -> pd.DataFrame | None:
    """Converte la risposta JSON di OVERVIEW nello schema comune."""
    if not data or "Symbol" not in data:
        return None

    def _num(chiave):
        val = data.get(chiave)
        if val in (None, "", "None", "-"):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    row = {
        "date": _oggi(),
        "ticker": ticker,
        "pe_ratio": _num("PERatio"),
        "forward_pe": _num("ForwardPE"),
        "eps": _num("EPS"),
        "revenue_ttm": _num("RevenueTTM"),
        "profit_margin": _num("ProfitMargin"),
        "operating_margin": _num("OperatingMarginTTM"),
        "debt_to_equity": None,  # non disponibile in OVERVIEW, vedi docstring
        "dividend_yield": _num("DividendYield"),
        "market_cap": _num("MarketCapitalization"),
        "latest_quarter": data.get("LatestQuarter"),
        "source": SOURCE_NAME,
    }
    return pd.DataFrame([row])[FUNDAMENTALS_SCHEMA_COLUMNS]


def fetch_one(ticker: str, api_key: str) -> tuple[pd.DataFrame | None, str]:
    """
    Scarica l'OVERVIEW di un singolo ticker. Ritorna (DataFrame, esito),
    dove esito e' uno tra: "ok", "vuoto", "errore", "limite_raggiunto".
    "limite_raggiunto" significa che Alpha Vantage ha risposto con un
    messaggio di rate limit (giornaliero o al minuto): in questo caso il
    chiamante dovrebbe interrompere il batch, non solo saltare il ticker.
    """
    try:
        resp = requests.get(
            API_URL,
            params={"function": "OVERVIEW", "symbol": ticker, "apikey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERRORE] {ticker}: {e}")
        return None, "errore"

    # Alpha Vantage non usa codici HTTP per segnalare il rate limit: risponde
    # 200 con un campo "Note" (limite al minuto) o "Information" (limite
    # giornaliero/chiave non valida) al posto dei dati.
    if "Note" in data or "Information" in data:
        msg = data.get("Note") or data.get("Information")
        print(f"  [LIMITE] {ticker}: {msg}")
        return None, "limite_raggiunto"

    df = _parse_overview(data, ticker)
    if df is None:
        print(f"  [VUOTO] {ticker}: nessun dato fondamentale restituito")
        return None, "vuoto"

    return df, "ok"


def fetch_fundamentals(tickers: list[str]) -> dict:
    """
    Scarica i fondamentali per una lista di ticker da Alpha Vantage,
    rispettando il rate limit (5/minuto, 25/giorno) e ruotando il
    sottoinsieme di ticker se l'universo supera la quota giornaliera
    residua. Salva ogni risultato come raw (via storage/io.py) e ritorna un
    riepilogo per ticker:
    {"AAPL": "ok", "XYZ": "errore", "MSFT": "saltato_quota", ...}.
    Non solleva mai eccezione per un singolo ticker fallito.
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        print("  [ERRORE] ALPHA_VANTAGE_API_KEY non impostata (vedi .env.example)")
        return {t: "errore" for t in tickers}

    quota = get_remaining_quota_today()
    if quota == 0:
        print("  [LIMITE] quota giornaliera Alpha Vantage gia' esaurita per oggi")
        return {t: "saltato_quota" for t in tickers}

    da_scaricare = select_rotation(tickers, quota)
    saltati = [t for t in tickers if t not in da_scaricare]

    if saltati:
        print(f"  Rotazione attiva: {len(da_scaricare)}/{len(tickers)} ticker oggi "
              f"(quota residua: {quota}). Rimandati a domani: {', '.join(saltati)}")

    esiti = {t: "saltato_quota" for t in saltati}

    for i, ticker in enumerate(da_scaricare):
        print(f"[{i+1}/{len(da_scaricare)}] Scarico fondamentali {ticker} (Alpha Vantage)...")
        df, esito = fetch_one(ticker, api_key)

        if esito == "limite_raggiunto":
            # il rate limit e' globale per la chiave, non per ticker: non ha
            # senso continuare a chiamare, i restanti falliranno allo stesso
            # modo e sprechiamo solo tempo.
            esiti[ticker] = "limite_raggiunto"
            for rimanente in da_scaricare[i + 1:]:
                esiti[rimanente] = "limite_raggiunto"
            break

        _registra_chiamata()

        if df is None:
            esiti[ticker] = esito
        else:
            path = save_raw_fundamentals(ticker, df)
            esiti[ticker] = "ok"
            print(f"  -> salvato in {path.name}")

        # rispetta il limite di 5 richieste/minuto, tranne dopo l'ultima
        if i < len(da_scaricare) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    return esiti


if __name__ == "__main__":
    # Self-test manuale: `python src/ingestion/fundamentals_av.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.loader import get_ticker_list  # noqa: E402

    tickers = get_ticker_list()
    esiti = fetch_fundamentals(tickers)

    print("\n--- Riepilogo ---")
    for ticker, esito in esiti.items():
        print(f"  {ticker:6s} {esito}")

    ok = sum(1 for e in esiti.values() if e == "ok")
    print(f"\n{ok}/{len(tickers)} ticker scaricati con successo")
