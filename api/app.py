"""
FastAPI application for the trading bot dashboard.

Runs as a SEPARATE PROCESS from main.py:

    main.py   -> collects data, writes to SQLite
    run_api.py -> reads SQLite, serves HTTP

This separation means the dashboard cannot corrupt collector data (it
opens the DB read-only), the collector keeps running if the API
crashes, and either side can be restarted independently.

Start with:
    uv run python run_api.py

Then open http://127.0.0.1:8000/docs for interactive API docs.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import health, sentiment, settings, technical


def create_app() -> FastAPI:
    app = FastAPI(
        title="Auto Trade Bot API",
        description=(
            "Read-only access to collected sentiment and technical factors, "
            "plus configuration management. Factor values are raw: no "
            "weighting or trading interpretation is applied at this layer."
        ),
        version="0.1.0",
    )

    # The future frontend will run on its own dev-server port, which
    # counts as a different origin. Localhost-only for now; this must be
    # tightened before the API is ever exposed beyond this machine.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite default
            "http://127.0.0.1:5173",
            "http://localhost:3000",  # CRA / Next default
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(sentiment.router)
    app.include_router(technical.router)
    app.include_router(settings.router)

    @app.get("/", tags=["health"])
    def root() -> dict:
        return {
            "service": "auto-trade-bot-api",
            "docs": "/docs",
            "endpoints": [
                "/api/health",
                "/api/sentiment/coins",
                "/api/sentiment/history?coin=BTC&hours=24",
                "/api/sentiment/news?limit=50",
                "/api/technical/latest",
                "/api/technical/history?symbol=BTCUSDT&hours=168",
                "/api/klines?symbol=BTCUSDT&interval=1h&limit=200",
                "/api/settings",
            ],
        }

    return app


app = create_app()
