"""
Unico punto di accesso allo storage. Gestisce il livello "raw" (dati grezzi
cosi' come scaricati dalle fonti), il livello "clean" (dati puliti prodotti
da src/cleaning/, vedi prices_cleaner.py e fundamentals_cleaner.py) e il
livello "features" (feature di base prodotte da src/features/, vedi
returns.py e volatility.py).

Nessun modulo di ingestion/pulizia dovrebbe scrivere file parquet "a mano":
passa sempre da qui, cosi' se cambia il formato di storage cambia solo questo file.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
FEATURES_DIR = DATA_DIR / "features"


def save_raw_prices(ticker: str, df: pd.DataFrame) -> Path:
    """
    Salva i prezzi grezzi di un ticker con un timestamp di download nel nome
    file. Non sovrascrive mai un download precedente: ogni run aggiunge un
    nuovo file, cosi' resta ricostruibile "cosa si sapeva" in un dato momento
    (serve per evitare look-ahead bias piu' avanti, in fase di backtest).
    """
    ticker_dir = RAW_DIR / "prices" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    downloaded_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ticker_dir / f"{downloaded_at}.parquet"

    df.to_parquet(path, index=False)
    return path


def load_latest_raw_prices(ticker: str) -> pd.DataFrame | None:
    """
    Ritorna l'ultimo download raw disponibile per un ticker (il file con il
    timestamp piu' recente), oppure None se non esiste ancora nulla.
    """
    ticker_dir = RAW_DIR / "prices" / ticker
    if not ticker_dir.exists():
        return None

    files = sorted(ticker_dir.glob("*.parquet"))
    if not files:
        return None

    return pd.read_parquet(files[-1])


def list_raw_downloads(ticker: str) -> list[Path]:
    """Elenca tutti i download raw storici disponibili per un ticker."""
    ticker_dir = RAW_DIR / "prices" / ticker
    if not ticker_dir.exists():
        return []
    return sorted(ticker_dir.glob("*.parquet"))


# --- Fondamentali -----------------------------------------------------
# Stesso pattern dei prezzi: mai sovrascrivere, timestamp nel nome file,
# una sottocartella per ticker. L'unica differenza e' la fonte (alpha_vantage
# o finnhub), che salviamo come colonna "source" dentro al DataFrame invece
# che nel path, cosi' load_latest_raw_fundamentals ritorna sempre l'ultimo
# dato disponibile indipendentemente da quale fonte lo abbia prodotto.

def save_raw_fundamentals(ticker: str, df: pd.DataFrame) -> Path:
    """
    Salva i fondamentali grezzi di un ticker con un timestamp di download
    nel nome file. Non sovrascrive mai un download precedente, per lo stesso
    motivo dei prezzi: ricostruibilita' storica di "cosa si sapeva quando".
    """
    ticker_dir = RAW_DIR / "fundamentals" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    downloaded_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ticker_dir / f"{downloaded_at}.parquet"

    df.to_parquet(path, index=False)
    return path


def load_latest_raw_fundamentals(ticker: str) -> pd.DataFrame | None:
    """
    Ritorna l'ultimo download raw di fondamentali disponibile per un ticker
    (il file con il timestamp piu' recente, a prescindere dalla fonte che lo
    ha prodotto), oppure None se non esiste ancora nulla.
    """
    ticker_dir = RAW_DIR / "fundamentals" / ticker
    if not ticker_dir.exists():
        return None

    files = sorted(ticker_dir.glob("*.parquet"))
    if not files:
        return None

    return pd.read_parquet(files[-1])


def list_raw_fundamentals_downloads(ticker: str) -> list[Path]:
    """Elenca tutti i download raw storici di fondamentali per un ticker."""
    ticker_dir = RAW_DIR / "fundamentals" / ticker
    if not ticker_dir.exists():
        return []
    return sorted(ticker_dir.glob("*.parquet"))


# --- Livello "clean" ---------------------------------------------------
# Stesso pattern del raw (timestamp nel nome file, mai sovrascritto, una
# sottocartella per ticker) ma sotto data/clean/ invece di data/raw/. Non
# sovrascrivere mai neanche qui: le euristiche di pulizia in src/cleaning/
# cambieranno nel tempo, e tenere lo storico permette di vedere come
# cambia l'esito della pulizia, non solo del dato grezzo. Chi produce
# questi file e' src/cleaning/prices_cleaner.py e fundamentals_cleaner.py;
# chi li salva effettivamente e' chi chiama quei moduli (per ora
# scripts/check_clean.py), non i moduli di pulizia stessi.

def save_clean_prices(ticker: str, df: pd.DataFrame) -> Path:
    """
    Salva la versione pulita dei prezzi di un ticker (prodotta da
    src/cleaning/prices_cleaner.py:clean_prices). Non sovrascrive mai una
    pulizia precedente, stesso motivo del raw: ricostruibilita' storica.
    """
    ticker_dir = CLEAN_DIR / "prices" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    cleaned_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ticker_dir / f"{cleaned_at}.parquet"

    df.to_parquet(path, index=False)
    return path


def load_latest_clean_prices(ticker: str) -> pd.DataFrame | None:
    """
    Ritorna l'ultima versione pulita disponibile dei prezzi per un ticker,
    oppure None se non e' mai stata prodotta.
    """
    ticker_dir = CLEAN_DIR / "prices" / ticker
    if not ticker_dir.exists():
        return None

    files = sorted(ticker_dir.glob("*.parquet"))
    if not files:
        return None

    return pd.read_parquet(files[-1])


def save_clean_fundamentals(ticker: str, df: pd.DataFrame) -> Path:
    """
    Salva la versione pulita dei fondamentali di un ticker (prodotta da
    src/cleaning/fundamentals_cleaner.py:clean_fundamentals). Stesso
    pattern di save_clean_prices.
    """
    ticker_dir = CLEAN_DIR / "fundamentals" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    cleaned_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ticker_dir / f"{cleaned_at}.parquet"

    df.to_parquet(path, index=False)
    return path


def load_latest_clean_fundamentals(ticker: str) -> pd.DataFrame | None:
    """
    Ritorna l'ultima versione pulita disponibile dei fondamentali per un
    ticker, oppure None se non e' mai stata prodotta.
    """
    ticker_dir = CLEAN_DIR / "fundamentals" / ticker
    if not ticker_dir.exists():
        return None

    files = sorted(ticker_dir.glob("*.parquet"))
    if not files:
        return None

    return pd.read_parquet(files[-1])


# --- Livello "features" --------------------------------------------------
# Stesso pattern di clean (timestamp nel nome file, mai sovrascritto, una
# sottocartella per ticker) ma sotto data/features/. Contiene le feature di
# base della sezione 9.7 del progetto (rendimenti + volatilita', prodotte
# da src/features/returns.py e volatility.py). Vanno calcolate UNA volta
# nella pipeline e riusate a valle: screening, backtest e monitoraggio le
# leggono da qui (load_latest_features), non le ricalcolano per conto
# proprio (rischio di incoerenze tra moduli).

def save_features(ticker: str, df: pd.DataFrame) -> Path:
    """
    Salva le feature di base di un ticker (prodotte da
    src/features/volatility.py:compute_features). Non sovrascrive mai una
    versione precedente, stesso motivo di raw e clean: ricostruibilita'
    storica di "cosa si sapeva quando".
    """
    ticker_dir = FEATURES_DIR / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    computed_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ticker_dir / f"{computed_at}.parquet"

    df.to_parquet(path, index=False)
    return path


def load_latest_features(ticker: str) -> pd.DataFrame | None:
    """
    Ritorna l'ultima versione disponibile delle feature di base per un
    ticker, oppure None se non e' mai stata prodotta.
    """
    ticker_dir = FEATURES_DIR / ticker
    if not ticker_dir.exists():
        return None

    files = sorted(ticker_dir.glob("*.parquet"))
    if not files:
        return None

    return pd.read_parquet(files[-1])


# --- Ispezione dello storage (per il report di qualita') ------------------

def latest_snapshot_path(layer: str, kind: str | None, ticker: str) -> Path | None:
    """
    Ritorna il percorso dell'ultimo file salvato per un ticker in un dato
    livello ("raw", "clean", "features") e tipo di dato ("prices",
    "fundamentals"; None per features, che non ha sottotipi), oppure None.
    Serve al report di qualita' (src/reporting/quality_report.py) per la
    freshness: il timestamp nel nome file dice QUANDO il dato e' stato
    prodotto, indipendentemente dal contenuto. Nessun altro modulo dovrebbe
    ricostruire i percorsi di storage a mano: passa da qui.
    """
    base = {"raw": RAW_DIR, "clean": CLEAN_DIR, "features": FEATURES_DIR}[layer]
    ticker_dir = base / kind / ticker if kind else base / ticker
    if not ticker_dir.exists():
        return None
    files = sorted(ticker_dir.glob("*.parquet"))
    return files[-1] if files else None
