"""
API server entry point.

    uv run python run_api.py

Then open http://127.0.0.1:8000/docs

This is a separate process from main.py. Run both at the same time in
two terminals: main.py collects data, run_api.py serves it.

Bound to 127.0.0.1 on purpose: the settings endpoints can write to
config.yaml and there is no authentication, so this must not be exposed
to the network as-is.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,      # auto-restart on code changes during development
        log_level="info",
    )
