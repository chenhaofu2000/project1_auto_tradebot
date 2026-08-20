"""
Standalone LLM connectivity test.

Isolates the LLM API call from the rest of the pipeline, with an
explicit short timeout, so connection problems fail fast and loud
instead of hanging silently.

Run with:
    uv run python -m tests.test_llm_direct
"""

import asyncio

from loguru import logger
from openai import AsyncOpenAI

from core.config import load_config


async def run_test() -> None:
    config = load_config()
    llm_cfg = config["llm"]

    logger.info(f"base_url = {llm_cfg['base_url']}")
    logger.info(f"model    = {llm_cfg['model']}")
    logger.info("Sending a single test request with a 20s timeout...")

    client = AsyncOpenAI(
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg["base_url"],
        timeout=20.0,  # fail fast instead of hanging forever
    )

    try:
        resp = await client.chat.completions.create(
            model=llm_cfg["model"],
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            temperature=0.0,
        )
        logger.info(f"SUCCESS. Model replied: {resp.choices[0].message.content!r}")
    except Exception as e:
        logger.error(f"FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(run_test())
