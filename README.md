# project1_auto_tradebot

An event-driven, plugin-based crypto market analysis system, built module by module.

It continuously ingests crypto news and market data, turns them into
quantified factors (news sentiment via an LLM; technical indicators from
K-lines), persists everything to SQLite, and exposes it over a read-only
HTTP API. The whole thing runs as two Docker containers orchestrated with
docker compose, designed to run 24/7.

The factor layer only produces **raw, objective values**. How to weight
those factors into a trading signal is deliberately left to a separate
strategy layer (not yet built), so factors can be recorded now and
back-tested against many strategies later.

## Architecture

```
                    EventBus  (async pub/sub, per-handler fault isolation)
                                        |
  Module A (sentiment)
    NewsCrawler --NEWS--> SentimentScorer --SENTIMENT_SCORE--> SentimentAggregator
    (CoinDesk / CoinTelegraph RSS)  (batched LLM calls)        (rolling window + hype flag)

  Module B (technical)
    KlineFetcher --KLINE--> IndicatorEngine --TECHNICAL_SCORE-->
    (Binance public API)    (momentum, RSI, volume ratio, EMA45/EMA125 deviations)

  Persistence
    StoragePlugin  <-- subscribes to NEWS, SENTIMENT_SCORE, TECHNICAL_SCORE
                   --> SQLite (data/tradebot.db)

  Serving
    FastAPI (read-only)  --> reads SQLite, serves HTTP + interactive /docs
```

Two processes, two containers:

- **collector** (`main.py`): runs every plugin, writes to SQLite.
- **api** (`run_api.py`): reads SQLite, serves the data over HTTP.

Both mount the same host `data/` directory, so the API always reads
exactly what the collector wrote.

## Project layout

```
project1_auto_tradebot/
├── config.yaml              # non-secret parameters (API key comes from env)
├── .env                     # LLM_API_KEY lives here, git-ignored, never committed
├── main.py                  # collector entry point (24/7 loop)
├── run_api.py               # API server entry point
├── Dockerfile.collector     # image for the collector
├── Dockerfile.api           # image for the API
├── docker-compose.yml       # orchestrates both containers
├── .dockerignore
│
├── core/                    # framework layer (no business logic)
│   ├── event_types.py       # event enum
│   ├── events.py            # Event data model (pydantic, UTC timestamps)
│   ├── handlers.py          # handler type signatures
│   ├── event_bus.py         # async pub/sub bus
│   ├── plugin.py            # plugin base class (uniform start/stop)
│   ├── config.py            # YAML loader + env-var secret overlay
│   └── database.py          # SQLite schema + connection
│
├── plugins/
│   ├── sentiment/           # Module A
│   │   ├── news_crawler.py
│   │   ├── scorer.py
│   │   └── aggregator.py
│   ├── technical/           # Module B
│   │   ├── kline_fetcher.py
│   │   └── indicator_engine.py
│   └── storage/
│       └── storage_plugin.py
│
├── api/                     # FastAPI read-only backend
│   ├── db.py                # read-only queries (opens SQLite in ro mode)
│   ├── settings_store.py    # config read/write with API-key protection
│   ├── app.py               # FastAPI app assembly
│   └── routers/             # health, sentiment, technical, settings
│
├── scripts/
│   ├── download_history.py  # one-off historical K-line backfill
│   └── plot_sentiment.py    # sentiment-over-time chart
│
└── tests/                   # smoke + end-to-end tests per module
```

## Setup (local, without Docker)

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync`
3. Put your LLM API key in the environment (Aliyun Bailian / any
   OpenAI-compatible endpoint):

   ```powershell
   $env:LLM_API_KEY = "your-key"
   ```

   `config.yaml` keeps `llm.api_key` empty on purpose; the key is read
   from the environment so no plaintext key sits on disk.

## Run (local, without Docker)

```powershell
# framework smoke test (no network, no key)
uv run python -m tests.test_event_bus

# RSS crawler (network, no key)
uv run python -m tests.test_news_crawler

# Binance connectivity (network, no key)
uv run python -m tests.test_kline_fetcher

# technical pipeline, prints raw indicators (network, no key)
uv run python -m tests.test_technical

# full sentiment + storage pipeline (network + key)
uv run python -m tests.test_storage

# API layer tests (no server needed)
uv run python -m tests.test_api

# 24/7 collector
uv run python main.py

# API server, then open http://127.0.0.1:8000/docs
uv run python run_api.py

# one-off: backfill historical K-lines for backtesting
uv run python scripts/download_history.py
```

## Run (Docker, recommended)

One command builds both images and starts both containers:

```powershell
docker compose up --build
```

- collector starts ingesting; api serves on http://127.0.0.1:8000/docs
- the API key is read from `.env` (`LLM_API_KEY=...`), never baked into
  the image and never on the command line
- `./data` is mounted into both containers, so the database survives
  container restarts

Stop:

```powershell
docker compose down
```

## Key design decisions

1. **Event bus over direct calls** — modules are decoupled; adding a new
   module means adding a subscriber, not editing existing code.
2. **Per-handler exception isolation** (`gather(return_exceptions=True)`)
   — one buggy plugin cannot crash the system.
3. **Batched LLM scoring** — headlines are buffered and scored one batch
   per call, cutting token cost and latency versus per-item calls.
4. **UTC timestamps everywhere** — keeps live and historical data aligned
   for backtesting.
5. **Indicators are raw; strategy is separate** — the indicator engine
   emits unweighted values and publishes no combined score. Weighting and
   thresholds belong to a user-owned strategy layer, so recorded factors
   can be replayed against many strategies.
6. **Drop the in-progress candle** — indicators are computed only from
   closed candles, so live behaviour matches backtests and volume/RSI are
   never contaminated by a partially-formed bar.
7. **Stateless K-line fetch** — each poll re-fetches a full candle window
   instead of patching incremental state; a little more bandwidth buys
   correctness and simpler debugging.
8. **Secrets via environment injection** — the API key moved from
   hard-coded → config file → environment variable. In containers it comes
   from `.env`; on AWS it will come from SSM Parameter Store at runtime.
   No plaintext key on disk anywhere.
9. **Read-only API** — the API opens SQLite in read-only mode, so the
   dashboard is structurally unable to corrupt collector data. Config
   writes are the one exception and are guarded (see below).
10. **API key never leaves the settings endpoint** — reads return a mask,
    and writing the mask back cannot overwrite a real stored key.

## Deployment roadmap (AWS)

The collector is a genuine 24/7 service — a laptop lid closing kills it —
which is the real reason it belongs on a server rather than a personal
machine.

- [x] Dockerize collector and API
- [x] docker compose orchestration (multi-container)
- [x] Secrets via environment injection (`.env` locally)
- [ ] Provision an EC2 instance (free tier), install Docker + compose
- [ ] Ship images / repo to EC2, `docker compose up -d`
- [ ] Security group: expose only the API port, lock down the rest
- [ ] IAM Role on the instance + SSM Parameter Store for the API key
      (no secret on the instance disk)
- [ ] CloudWatch/billing alarm to catch runaway cost
- [ ] GitHub Actions: run tests on every push to main (CI)

**Not deploying to the cloud:** anything that can place real orders. Only
the read-only collection + analysis pipeline is cloud-facing; a program
that can move money on a public host is a different security problem
entirely and is out of scope for now.

## Feature status

**Working**: event bus, news ingestion, LLM sentiment scoring, rolling
aggregation with hype detection, Binance K-line ingestion, raw technical
indicators (momentum / RSI / volume / EMA45 / EMA125), SQLite persistence
for news + sentiment + technical, historical K-line backfill, read-only
FastAPI backend, Docker + compose, environment-variable secrets.

**Not yet built**: strategy layer (factor weighting + signal thresholds),
risk layer, order execution, backtesting engine, web frontend, X/KOL
monitoring, alerting.

## External services

- **Aliyun Bailian** (Singapore, OpenAI-compatible) — LLM sentiment
  scoring, model `qwen3.7-plus`. The only paid dependency; free tier
  covers roughly a month+ of usage, ~¥3-4/month after that.
- **Binance public REST API** — K-line / volume data. Free, no key.
- **CoinDesk & CoinTelegraph RSS** — news source. Free.