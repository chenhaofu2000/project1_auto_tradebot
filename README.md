# project1_auto_tradebot

Event-driven, plugin-based crypto trading system, built module by module.
Module A (sentiment) is complete and persists to SQLite. Module B
(technical analysis) is in progress. Module C (execution) not started.

## Architecture

```
                 EventBus (async pub/sub, fault-isolated)
                              |
   NewsCrawler --NEWS--> SentimentScorer --SENTIMENT_SCORE--> SentimentAggregator
   (RSS feeds)           (batched LLM calls)                  (rolling window + hype detection)
        |                        |
        +----------> StoragePlugin (SQLite: news, sentiment, snapshots)

   KlineFetcher --KLINE--> IndicatorEngine --TECHNICAL_SCORE--> (strategy layer, TBD)
   (Binance public API)    (raw indicators only:
                            momentum, RSI, volume ratio,
                            price vs EMA45/EMA125)
```

**Design boundary:** the indicator layer publishes *raw, objective*
numbers only. It never combines them into a single score. How to weight
momentum vs RSI vs volume vs EMA distance -- and what thresholds trigger
a trade -- is a strategy decision that lives in its own layer. This keeps
strategy iteration fully decoupled from data collection, and makes the
raw indicators reusable across different strategies during backtesting.

- **core/**: framework layer (event bus, event types, plugin base class,
  config loader, SQLite schema/connection)
- **plugins/sentiment/**: Module A - news ingestion, LLM scoring, aggregation
- **plugins/storage/**: persists news + sentiment events to SQLite
- **plugins/technical/**: Module B (in progress) - K-line ingestion, indicators
- **scripts/plot_sentiment.py**: renders a time-series PNG of sentiment per coin
- **tests/**: smoke tests and end-to-end pipeline tests

## Setup

1. Install [uv](https://docs.astral.sh/uv/)
2. In the project root:

```powershell
uv sync
```

3. Open `config.yaml` and paste your LLM API key (Aliyun Bailian / DeepSeek,
   any OpenAI-compatible endpoint works). **Never commit config.yaml.**

## Run

```powershell
# Test the event bus (no network needed)
uv run python -m tests.test_event_bus

# Test the RSS crawler (network needed, no API key needed)
uv run python -m tests.test_news_crawler

# Full Module A pipeline, no persistence (network + API key needed)
uv run python -m tests.test_sentiment

# Full Module A pipeline WITH SQLite persistence
uv run python -m tests.test_storage

# Module B: verify Binance public API connectivity (network only, no key)
uv run python -m tests.test_kline_fetcher

# Module B: full technical pipeline, prints raw indicators (network only, no key)
uv run python -m tests.test_technical

# Long-running production entry point (Ctrl+C to stop)
uv run python main.py

# Plot sentiment-over-time chart from accumulated SQLite data
uv run python scripts/plot_sentiment.py
```

## Key design decisions

1. **Event bus over direct calls** - modules are decoupled; adding
   technical-analysis or trading modules later requires zero changes
   to existing code.
2. **Per-handler exception isolation** (`gather(return_exceptions=True)`) -
   one buggy plugin cannot crash the system.
3. **Batched LLM scoring** - headlines are buffered and scored in one
   call per batch, cutting API cost/latency ~10x vs per-item calls.
4. **UTC timestamps everywhere** - keeps backtest and live data aligned.
5. **Secrets in config.yaml, gitignored** - keys never enter source control.
6. **Stateless K-line computation** - each poll re-fetches a full candle
   window rather than patching incremental state, trading a little
   bandwidth for correctness and simpler debugging.
7. **Indicators are raw, strategy is separate** - the indicator engine
   emits unweighted values (RSI as a raw 0-100, EMA deviations as signed
   percentages, etc.) and deliberately publishes no combined score. All
   weighting and thresholds live in a user-owned strategy layer, so the
   same recorded indicators can be replayed against many strategies
   during backtesting.

## Roadmap

- [x] Module A: sentiment pipeline (RSS -> LLM scoring -> rolling aggregation)
- [x] SQLite persistence for news + sentiment
- [x] Module B step 1: K-line ingestion from Binance public API
- [x] Module B step 2: raw indicators (momentum, RSI, volume, EMA45/125)
- [ ] Additional price-action indicators (distance to N-day high/low,
      user-defined support/resistance levels)
- [ ] Persist technical indicators + fetch historical K-lines for backtesting
- [ ] Strategy layer: user-owned weighting function + signal thresholds
- [ ] Independent risk layer (position caps, daily-loss circuit breaker)
- [ ] Paper-trading executor, then live execution
- [ ] Backtesting engine (sentiment + technical vs actual price action)
- [ ] X/KOL monitoring plugin, Telegram alerting
