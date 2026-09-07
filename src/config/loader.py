"""
Unico punto di lettura della configurazione del progetto.
Nessun altro modulo deve aprire direttamente universe.yaml o sources.yaml:
tutti passano da qui, cosi' se cambia il formato del config cambia solo questo file.
"""

from pathlib import Path
import yaml

# Cartella config/, calcolata rispetto alla posizione di questo file
# (src/config/loader.py -> risali di due livelli -> root del progetto -> config/)
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"


def load_universe() -> list[dict]:
    """
    Ritorna la lista di ticker dell'universo, ciascuno con i suoi metadati
    (nome, settore, borsa, valuta), leggendo config/universe.yaml.
    """
    path = CONFIG_DIR / "universe.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["tickers"]


def get_ticker_list() -> list[str]:
    """Scorciatoia: solo la lista di simboli, es. ["AAPL", "MSFT", ...]."""
    return [t["ticker"] for t in load_universe()]


def load_sources_config() -> dict:
    """
    Ritorna i parametri di ciascuna fonte dati (refresh, rate limit, fallback),
    leggendo config/sources.yaml.
    """
    path = CONFIG_DIR / "sources.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    # Piccolo self-check manuale: `python src/config/loader.py`
    universe = load_universe()
    print(f"Universo caricato: {len(universe)} ticker")
    for t in universe:
        print(f"  {t['ticker']:6s} {t['nome']:25s} settore={t['settore']}")

    sources = load_sources_config()
    print("\nFonti configurate:", list(sources.keys()))
