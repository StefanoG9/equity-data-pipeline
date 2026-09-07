"""
Report di qualita' dati (punto 9.10 del documento di progetto, uno dei
criteri di chiusura della Fase 1). Per ogni run produce un JSON in
data/quality_reports/ con, per ticker e per tipo di dato (prezzi,
fondamentali, features):

  - esito del run: "ok" (aggiornato in questo run), "da_cache" (nessun
    aggiornamento in questo run, si usa l'ultimo dato buono disponibile),
    "fallito" (il fetch di questo run e' fallito ma esiste un dato
    precedente), "nessun_dato" (non e' mai esistito nulla);
  - righe e valori nulli dell'ultima versione disponibile;
  - alert dei cleaner / del calcolo feature (mai silenziati, vedi
    prices_cleaner.py);
  - freshness: data dell'ultimo dato contenuto, eta' in giorni, e timestamp
    di quando il file e' stato prodotto (dal nome file, vedi
    src/storage/io.py:latest_snapshot_path).

E' questo report che permette di fidarsi (o non fidarsi) dei dati usati a
valle in screening e backtest. Chiamato da scripts/run_daily_update.py con
gli esiti del fetch e i report dei cleaner; eseguibile anche da solo
(`python src/reporting/quality_report.py`), nel qual caso tutto cio' che
esiste su disco viene classificato "da_cache" (nessun fetch in questo run).

File di output: data/quality_reports/quality_<YYYY-MM-DD>_<HH-MM-SS>.json
(mai sovrascritto, stessa politica dello storage) + una copia sempre
aggiornata in data/quality_reports/latest.json.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config.loader import get_ticker_list  # noqa: E402
from src.storage.io import (  # noqa: E402
    latest_snapshot_path,
    load_latest_clean_fundamentals,
    load_latest_clean_prices,
    load_latest_features,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
QUALITY_DIR = ROOT_DIR / "data" / "quality_reports"

# Colonne su cui contare i nulli, per tipo di dato. Per i fondamentali i
# None sono spesso "campo non fornito dalla fonte" piu' che un errore, ma
# vanno comunque contati: e' il lettore del report a giudicare.
COLONNE_CHIAVE = {
    "prezzi": ["open", "high", "low", "close", "adj_close", "volume"],
    "fondamentali": ["pe_ratio", "eps", "dividend_yield", "debt_to_equity",
                      "profit_margin", "operating_margin", "market_cap"],
    "features": ["adj_close", "simple_return", "log_return",
                  "volatility_20d", "volatility_60d"],
}

# Oltre questa eta' (giorni di calendario dall'ultimo dato contenuto) i
# PREZZI sono considerati stantii: con i weekend 2-3 giorni sono normali,
# 5+ vogliono dire che qualcosa non si sta aggiornando. Per i fondamentali
# la soglia e' molto piu' larga: si aggiornano a rotazione (quota Alpha
# Vantage) e un dato trimestrale resta valido a lungo.
FRESHNESS_MAX_GIORNI_PREZZI = 5
FRESHNESS_MAX_GIORNI_FONDAMENTALI = 120


def _conta_nulli(df: pd.DataFrame | None, colonne: list[str]) -> int | None:
    if df is None:
        return None
    presenti = [c for c in colonne if c in df.columns]
    return int(df[presenti].isna().sum().sum())


def _freshness(df: pd.DataFrame | None, path: Path | None, colonna_data: str = "date") -> dict:
    """
    Estrae le informazioni di freshness: ultima data contenuta nel dato,
    eta' in giorni rispetto a oggi, e timestamp di produzione del file
    (dal nome, formato %Y%m%dT%H%M%SZ di src/storage/io.py).
    """
    out: dict = {"ultima_data_dato": None, "eta_giorni": None, "file_prodotto_il": None}

    if path is not None:
        try:
            prodotto = datetime.strptime(path.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            out["file_prodotto_il"] = prodotto.strftime("%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            out["file_prodotto_il"] = path.stem  # nome fuori standard: riportato com'e'

    if df is not None and not df.empty and colonna_data in df.columns:
        ultima = pd.to_datetime(df[colonna_data]).max()
        if pd.notna(ultima):
            out["ultima_data_dato"] = ultima.strftime("%Y-%m-%d")
            out["eta_giorni"] = int((pd.Timestamp.now().normalize() - ultima.normalize()).days)

    return out


def _esito(fetch_esito: str | None, df) -> str:
    """
    Combina l'esito del fetch di questo run con cio' che esiste su disco.
    fetch_esito None significa "nessun fetch tentato in questo run" (es.
    fondamentali nei run serali, o run standalone del report).
    """
    esiste = df is not None
    if fetch_esito == "ok":
        return "ok"
    if fetch_esito is None:
        return "da_cache" if esiste else "nessun_dato"
    # fetch tentato e fallito
    return "fallito" if not esiste else "fallito_uso_cache"


def _sezione(tipo: str, df, path, fetch_esito, alerts: list[str]) -> dict:
    fresh = _freshness(df, path)
    sezione = {
        "esito": _esito(fetch_esito, df),
        "righe": len(df) if df is not None else 0,
        "nulli_colonne_chiave": _conta_nulli(df, COLONNE_CHIAVE[tipo]),
        "alerts": alerts,
        **fresh,
    }

    # freshness fuori soglia: aggiunta come alert esplicito, cosi' basta
    # guardare gli alert per sapere se c'e' qualcosa che non va.
    soglia = {"prezzi": FRESHNESS_MAX_GIORNI_PREZZI,
              "fondamentali": FRESHNESS_MAX_GIORNI_FONDAMENTALI}.get(tipo)
    if soglia and sezione["eta_giorni"] is not None and sezione["eta_giorni"] > soglia:
        sezione["alerts"] = alerts + [
            f"dato stantio: ultimo {tipo} di {sezione['eta_giorni']} giorni fa (soglia {soglia})"
        ]
    return sezione


def generate_quality_report(fetch_esiti_prezzi: dict[str, str] | None = None,
                            report_pulizia_prezzi: dict[str, dict] | None = None,
                            report_pulizia_fondamentali: dict[str, dict] | None = None,
                            report_features: dict[str, dict] | None = None) -> Path:
    """
    Genera e salva il report di qualita' del run corrente. Tutti i
    parametri sono opzionali: se mancano (run standalone) il report viene
    ricostruito da cio' che esiste su disco, con esito "da_cache" e senza
    gli alert dei cleaner (che esistono solo durante un run di pipeline).

    - fetch_esiti_prezzi: dict ticker -> "ok"/"errore" da
      src/ingestion/prices_yf.py:fetch_prices;
    - report_pulizia_prezzi / report_pulizia_fondamentali: dict ticker ->
      report del rispettivo cleaner (src/cleaning/);
    - report_features: dict ticker -> report di
      src/features/volatility.py:compute_features.

    Ritorna il percorso del file JSON scritto.
    """
    fetch_esiti_prezzi = fetch_esiti_prezzi or {}
    report_pulizia_prezzi = report_pulizia_prezzi or {}
    report_pulizia_fondamentali = report_pulizia_fondamentali or {}
    report_features = report_features or {}

    generated_at = datetime.now(timezone.utc).astimezone()
    tickers = get_ticker_list()

    per_ticker: dict[str, dict] = {}
    for ticker in tickers:
        df_p = load_latest_clean_prices(ticker)
        df_f = load_latest_clean_fundamentals(ticker)
        df_feat = load_latest_features(ticker)

        # I fondamentali non vengono fetchati dalla pipeline serale (quota
        # Alpha Vantage): l'esito del fetch resta None -> "da_cache".
        per_ticker[ticker] = {
            "prezzi": _sezione(
                "prezzi", df_p,
                latest_snapshot_path("clean", "prices", ticker),
                fetch_esiti_prezzi.get(ticker),
                report_pulizia_prezzi.get(ticker, {}).get("alerts", []),
            ),
            "fondamentali": _sezione(
                "fondamentali", df_f,
                latest_snapshot_path("clean", "fundamentals", ticker),
                None,
                report_pulizia_fondamentali.get(ticker, {}).get("alerts", []),
            ),
            "features": _sezione(
                "features", df_feat,
                latest_snapshot_path("features", None, ticker),
                None,
                report_features.get(ticker, {}).get("alerts", []),
            ),
        }

    # Riepilogo globale: quanti ticker per esito, per tipo di dato, piu' il
    # totale degli alert — le tre cose da guardare per prime.
    riepilogo: dict = {"n_ticker": len(tickers), "totale_alert": 0}
    for tipo in ("prezzi", "fondamentali", "features"):
        conteggi: dict[str, int] = {}
        for ticker in tickers:
            sez = per_ticker[ticker][tipo]
            conteggi[sez["esito"]] = conteggi.get(sez["esito"], 0) + 1
            riepilogo["totale_alert"] += len(sez["alerts"])
        riepilogo[tipo] = conteggi

    payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "riepilogo": riepilogo,
        "tickers": per_ticker,
    }

    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    nome = f"quality_{generated_at.strftime('%Y-%m-%d_%H-%M-%S')}.json"
    path = QUALITY_DIR / nome
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    latest = QUALITY_DIR / "latest.json"
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return path


if __name__ == "__main__":
    # Run standalone: `python src/reporting/quality_report.py`
    path = generate_quality_report()
    print(f"Report di qualita' generato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    print(json.dumps(payload["riepilogo"], indent=2, ensure_ascii=False))
