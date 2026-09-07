# equity-data-pipeline

A config-driven Python data pipeline for equities: it ingests daily prices and
company fundamentals from free APIs, cleans and validates them, computes base
features (returns, rolling volatility), tracks data quality run after run, and
renders an interactive HTML report. It has been running daily since July 2026.

This is **Phase 1** of a larger project (screening → backtesting → portfolio
monitoring). The idea is that none of the later phases are worth building on
top of a shaky data layer, so the data layer came first and is the only part
considered "done" today. The full design document, with the roadmap for the
next phases, is in [`docs/design-document.md`](docs/design-document.md)
(in Italian, as are the code comments).

## What it does

```
config/universe.yaml ──► ingestion ──► data/raw ──► cleaning ──► data/clean
                           │                                        │
                    yfinance (prices)                       features (returns,
                    Alpha Vantage ─fallback─► Finnhub       20d/60d volatility)
                    (fundamentals)                                  │
                                                                    ▼
                                    quality report (JSON) + daily HTML report
```

- **Ingestion** (`src/ingestion/`) — one module per source, each downloading a
  ticker at a time so a single failure never blocks the batch. The Alpha
  Vantage client respects the free-tier limits (5 req/min, 25 req/day) with a
  persisted daily counter, and falls back to Finnhub when the quota is
  exhausted, normalising units so both sources land on the same schema.
- **Cleaning** (`src/cleaning/`) — deduplication, gap and outlier checks on
  OHLCV prices; plausibility ranges and cross-source consistency checks on
  fundamentals. Nothing is silent: every correction or warning ends up in a
  report attached to the run.
- **Features** (`src/features/`) — simple and log returns on adjusted close,
  rolling volatility (20 and 60 trading days, annualised with √252).
- **Storage** (`src/storage/io.py`) — the single entry point for reading and
  writing the raw / clean / features layers as Parquet, so the on-disk format
  can change without touching the rest of the code.
- **Reporting** (`src/reporting/`) — a JSON data-quality report per run
  (per ticker and per data type: ok / from cache / failed / no data, null
  counts, cleaner alerts) and an interactive HTML report (Chart.js) with
  indexed performance, per-ticker detail and auto-generated commentary.
- **Orchestration** (`scripts/run_daily_update.py`) — the one command that
  runs the whole chain; `scripts/check_*.py` are manual inspection tools that
  print human-readable raw → clean comparisons.

## Quick start

```bash
git clone https://github.com/StefanoG9/equity-data-pipeline.git
cd equity-data-pipeline
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then add your Alpha Vantage / Finnhub keys
python scripts/run_daily_update.py
```

The report lands in `data/reports/latest.html`. Prices alone work without any
API key (yfinance); fundamentals need the keys in `.env`.

On Windows, `test_all.ps1` runs the full pipeline plus all the check scripts
and opens the report at the end.

## Configuration

- `config/universe.yaml` — the ticker universe (ticker, name, sector,
  exchange, currency). Ships with a deliberately small 10-stock set used to
  validate the pipeline before scaling up.
- `config/sources.yaml` — per data type: primary source, refresh cadence,
  rate limits, fallback source.
- `.env` — API keys (never committed; see `.env.example`).

## Project layout

```
config/       universe and data-source settings
scripts/      entry points: daily update + manual checks
src/
  ingestion/  yfinance, Alpha Vantage, Finnhub clients
  cleaning/   validation and correction of prices and fundamentals
  features/   returns and volatility
  storage/    Parquet I/O for the raw / clean / features layers
  reporting/  data-quality JSON and daily HTML report
  config/     YAML loader
data/         generated at runtime (git-ignored)
docs/         design document and roadmap
```

## Roadmap

- **Phase 2 — Screening:** fundamental and technical indicators, filters and
  scoring over the universe, producing a motivated shortlist.
- **Phase 3 — Backtesting:** rule-based strategies on historical data, with
  explicit handling of look-ahead and survivorship bias, transaction costs
  and a benchmark comparison.
- **Phase 4 — Monitoring:** tracking a real or simulated portfolio over time.
- **Phase 5 — Automation:** stable scheduling, shareable dashboards,
  additional markets and data sources.

Next steps on the current codebase: a `pytest` suite for the cleaning and
feature modules, and splitting `daily_report.py` into smaller components.

## Stack

Python 3.10+, pandas, numpy, pyarrow, PyYAML, requests, python-dotenv,
yfinance · Alpha Vantage and Finnhub APIs · Chart.js for the report.
