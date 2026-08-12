"""
Plot sentiment scores over time, one line per coin.

Reads from data/tradebot.db (sentiment table), explodes the JSON
"coins" column so a single news item mentioning multiple coins
contributes to each coin's series, then plots a time-series chart.

Run with:
    uv run python scripts/plot_sentiment.py

Output:
    data/sentiment_chart.png
"""

import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tradebot.db"
_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sentiment_chart.png"

# Only plot coins with at least this many data points (avoids a chart
# cluttered with one-off mentions of obscure tickers).
MIN_POINTS_TO_PLOT = 2


def load_sentiment_data() -> pd.DataFrame:
    """Load the sentiment table and explode multi-coin rows."""
    conn = sqlite3.connect(_DB_PATH)
    df = pd.read_sql_query(
        "SELECT title, coins, score, scored_at FROM sentiment ORDER BY scored_at",
        conn,
    )
    conn.close()

    if df.empty:
        return df

    # coins column is stored as a JSON string like '["BTC", "ETH"]'
    df["coins"] = df["coins"].apply(json.loads)
    df["scored_at"] = pd.to_datetime(df["scored_at"])

    # One row per (coin, news item) pair
    df = df.explode("coins").rename(columns={"coins": "coin"})
    df = df.dropna(subset=["coin"])
    df = df[df["coin"] != ""]

    return df


def plot(df: pd.DataFrame) -> None:
    if df.empty:
        print("No sentiment data with coin mentions found yet. Run the pipeline first.")
        return

    counts = df["coin"].value_counts()
    coins_to_plot = counts[counts >= MIN_POINTS_TO_PLOT].index.tolist()

    if not coins_to_plot:
        print(
            f"No coin has >= {MIN_POINTS_TO_PLOT} data points yet. "
            f"Let the pipeline run longer, or lower MIN_POINTS_TO_PLOT."
        )
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    for coin in coins_to_plot:
        coin_df = df[df["coin"] == coin].sort_values("scored_at")
        ax.plot(
            coin_df["scored_at"],
            coin_df["score"],
            marker="o",
            markersize=4,
            label=coin,
        )

    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Time")
    ax.set_ylabel("Sentiment score (-1 bearish .. +1 bullish)")
    ax.set_title("Crypto News Sentiment Over Time")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()

    fig.savefig(_OUTPUT_PATH, dpi=150)
    print(f"Chart saved to: {_OUTPUT_PATH}")
    print(f"Coins plotted: {coins_to_plot}")


if __name__ == "__main__":
    data = load_sentiment_data()
    plot(data)
