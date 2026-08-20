"""
LLM-based sentiment scorer plugin.

Subscribes to NEWS events, buffers them briefly, then scores a batch
of headlines in a single LLM call (batching cuts token cost and
latency dramatically vs. one call per headline).

For each news item the LLM returns:
- coins: ticker symbols mentioned (e.g. ["BTC", "HYPE"])
- score: -1.0 (very bearish) .. 1.0 (very bullish)
- reason: one short sentence

Each result is published as a SENTIMENT_SCORE event.
"""

import asyncio
import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin

SYSTEM_PROMPT = """You are a crypto news sentiment analyzer.
You will receive a JSON array of news items, each with an "idx" and "title".
For EACH item, output an object:
  {"idx": <same idx>, "coins": [...], "score": <float>, "reason": "<short>"}

Rules:
- "coins": uppercase ticker symbols explicitly mentioned or clearly implied
  (e.g. Bitcoin->BTC, Ethereum->ETH). Empty list if none.
- "score": -1.0 = very bearish, 0 = neutral, 1.0 = very bullish.
  Judge market impact, not writing tone.
- "reason": max 15 words, in English.
- Output ONLY a JSON array. No markdown, no extra text."""


class SentimentScorer(Plugin):
    """Batch LLM scorer: NEWS in, SENTIMENT_SCORE out."""

    name = "sentiment_scorer"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)

        llm_cfg = self.config.get("llm", {})
        self._client = AsyncOpenAI(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url", ""),
        )
        self._model: str = llm_cfg.get("model", "deepseek-v3")

        sent_cfg = self.config.get("sentiment", {})
        self.batch_window: float = float(sent_cfg.get("batch_window", 10.0))
        self.batch_max_size: int = int(sent_cfg.get("batch_max_size", 10))

        self._buffer: list[Event] = []
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self.bus.subscribe(EventType.NEWS, self._on_news)
        self._task = asyncio.create_task(self._flush_loop())
        logger.info(f"[{self.name}] started (model={self._model})")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"[{self.name}] stopped")

    # ------------------------------------------------------------------
    async def _on_news(self, event: Event) -> None:
        """Buffer incoming news; flushing happens in the background loop."""
        self._buffer.append(event)

    async def _flush_loop(self) -> None:
        """Every batch_window seconds, score whatever is in the buffer."""
        while self._running:
            await asyncio.sleep(self.batch_window)
            if not self._buffer:
                continue
            batch, self._buffer = self._buffer[: self.batch_max_size * 5], []
            # Score in chunks of batch_max_size
            for i in range(0, len(batch), self.batch_max_size):
                chunk = batch[i : i + self.batch_max_size]
                try:
                    await self._score_chunk(chunk)
                except Exception as e:
                    logger.exception(f"[{self.name}] scoring failed: {e}")

    # ------------------------------------------------------------------
    async def _score_chunk(self, chunk: list[Event]) -> None:
        """One LLM call for a chunk of news items."""
        payload = [{"idx": i, "title": ev.data.get("title", "")} for i, ev in enumerate(chunk)]

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,  # deterministic scoring
        )

        text = resp.choices[0].message.content or "[]"
        text = text.replace("```json", "").replace("```", "").strip()

        try:
            results = json.loads(text)
        except json.JSONDecodeError:
            logger.error(f"[{self.name}] bad LLM response: {text[:200]}")
            return

        for item in results:
            idx = item.get("idx")
            if idx is None or idx >= len(chunk):
                continue
            src_event = chunk[idx]
            await self.bus.publish(
                Event(
                    type=EventType.SENTIMENT_SCORE,
                    source=self.name,
                    data={
                        "coins": item.get("coins", []),
                        "score": float(item.get("score", 0.0)),
                        "reason": item.get("reason", ""),
                        "title": src_event.data.get("title", ""),
                        "feed": src_event.data.get("feed", ""),
                        "link": src_event.data.get("link", ""),
                    },
                )
            )
        logger.info(f"[{self.name}] scored {len(results)} items")
