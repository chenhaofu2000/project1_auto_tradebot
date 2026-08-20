"""
Tests for the API layer.

Runs against the real database and config file WITHOUT starting a
server, using FastAPI's TestClient. Safe to run any time: every DB
query is read-only, and the config test only writes values it read
first, so it restores the original state.

Run with:
    uv run python -m tests.test_api
"""

from fastapi.testclient import TestClient
from loguru import logger

from api import settings_store as store
from api.app import app

client = TestClient(app)


def _check(resp, label: str) -> object:
    if resp.status_code == 503:
        logger.warning(f"{label}: 503 - {resp.json().get('detail')}")
        return None
    assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text}"
    return resp.json()


def test_health() -> None:
    data = _check(client.get("/api/health"), "health")
    if data is None:
        return
    logger.info("Table row counts:")
    for table, stats in data["tables"].items():
        logger.info(f"  {table:12s} {stats}")


def test_sentiment() -> None:
    coins = _check(client.get("/api/sentiment/coins?window_hours=24"), "coins")
    if coins is None:
        return
    logger.info(f"Sentiment over 24h ({len(coins)} coins):")
    for c in coins[:8]:
        logger.info(f"  {c['coin']:6s} avg={c['avg_score']:+.2f} mentions={c['mentions']}")

    if coins:
        top = coins[0]["coin"]
        hist = _check(client.get(f"/api/sentiment/history?coin={top}&hours=24"), "history")
        logger.info(f"{top} history: {len(hist)} points")

    news = _check(client.get("/api/sentiment/news?limit=3"), "news")
    logger.info(f"Recent news ({len(news)}):")
    for n in news:
        logger.info(f"  [{n['score']:+.2f}] {n['coins']} {n['title'][:55]}")


def test_technical() -> None:
    latest = _check(client.get("/api/technical/latest"), "technical latest")
    if latest is None:
        return
    logger.info(f"Latest indicators ({len(latest)} symbols):")
    for t in latest:
        logger.info(
            f"  {t['symbol']:9s} close={t['close_price']:>10.2f} "
            f"rsi={t['rsi']:.1f} mom={t['momentum_score']:+.2f} "
            f"px/ema45={t['price_vs_ema45']:+.1f}%"
        )

    if latest:
        sym = latest[0]["symbol"]
        hist = _check(client.get(f"/api/technical/history?symbol={sym}&hours=168"), "tech history")
        logger.info(f"{sym} indicator history: {len(hist)} points (deduplicated)")


def test_klines() -> None:
    avail = _check(client.get("/api/klines/available"), "klines available")
    if avail is None:
        return
    logger.info(f"Backfilled K-lines: {avail}")
    if avail:
        sym, iv = avail[0]["symbol"], avail[0]["interval"]
        candles = _check(client.get(f"/api/klines?symbol={sym}&interval={iv}&limit=5"), "klines")
        logger.info(f"{sym} {iv} last {len(candles)} candles:")
        for c in candles:
            logger.info(f"  t={c['open_time']} close={c['close']:.2f}")
        times = [c["open_time"] for c in candles]
        assert times == sorted(times), "candles must be in ascending time order"


def test_settings_never_leaks_key() -> None:
    """The single most important test in this file."""
    resp = client.get("/api/settings")
    data = _check(resp, "settings")
    if data is None:
        return

    real_key = store._load_raw().get("llm", {}).get("api_key", "")
    body = resp.text

    assert data["llm"]["api_key"] == store.MASK or data["llm"]["api_key"] == ""
    if real_key and real_key != "PUT_YOUR_BAILIAN_KEY_HERE":
        assert real_key not in body, "API KEY LEAKED IN RESPONSE"
        logger.info("Key never appears in the response body: OK")
    logger.info(f"api_key_configured = {data['llm']['api_key_configured']}")


def test_settings_write_preserves_key() -> None:
    """Writing the masked value back must not destroy the stored key."""
    key_before = store._load_raw().get("llm", {}).get("api_key", "")

    resp = client.put("/api/settings", json={"llm": {"api_key": store.MASK}})
    _check(resp, "settings write (mask)")

    key_after = store._load_raw().get("llm", {}).get("api_key", "")
    assert key_before == key_after, "KEY WAS DESTROYED BY A MASKED WRITE"
    logger.info("Masked write preserved the stored key: OK")


def test_settings_validation() -> None:
    """Invalid values must be rejected without modifying the file."""
    before = store._load_raw()

    resp = client.put("/api/settings", json={"technical": {"rsi_period": -5}})
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}"
    logger.info(f"Rejected bad value: {resp.json()['detail']}")

    after = store._load_raw()
    assert before == after, "config changed despite a rejected request"
    logger.info("Config unchanged after rejection: OK")


def test_settings_roundtrip() -> None:
    """A real edit applies, then restore the original value."""
    original = store._load_raw()["technical"]["rsi_period"]
    try:
        resp = client.put("/api/settings", json={"technical": {"rsi_period": 21}})
        data = _check(resp, "settings roundtrip")
        assert store._load_raw()["technical"]["rsi_period"] == 21
        logger.info(f"Updated fields: {data['_meta']['updated_fields']}")
    finally:
        client.put("/api/settings", json={"technical": {"rsi_period": original}})
        assert store._load_raw()["technical"]["rsi_period"] == original
        logger.info(f"Restored rsi_period to {original}: OK")


def main() -> None:
    tests = [
        ("HEALTH", test_health),
        ("SENTIMENT", test_sentiment),
        ("TECHNICAL", test_technical),
        ("KLINES", test_klines),
        ("SETTINGS: no key leak", test_settings_never_leaks_key),
        ("SETTINGS: key preserved", test_settings_write_preserves_key),
        ("SETTINGS: validation", test_settings_validation),
        ("SETTINGS: roundtrip", test_settings_roundtrip),
    ]
    failures = 0
    for label, fn in tests:
        logger.info("=" * 60)
        logger.info(label)
        try:
            fn()
        except AssertionError as e:
            logger.error(f"FAILED: {e}")
            failures += 1
        except Exception as e:
            logger.exception(f"ERROR: {e}")
            failures += 1

    logger.info("=" * 60)
    if failures:
        logger.error(f"{failures} test(s) failed")
    else:
        logger.info("All API tests passed")


if __name__ == "__main__":
    main()
