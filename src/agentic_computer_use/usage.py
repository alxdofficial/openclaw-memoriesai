"""AI API usage and cost tracking.

Records token usage and estimated cost for every paid AI API call.
Data stored in the same SQLite DB as tasks (usage_events table).
"""
import asyncio
import logging
import time

from . import config, db

log = logging.getLogger(__name__)

# ── Pricing table ────────────────────────────────────────────────
# (input_cost_per_1m_tokens, output_cost_per_1m_tokens) in USD
# Keyed by lowercase model name prefix — longest match wins.
_PRICING: list[tuple[str, float, float]] = [
    # Anthropic — Claude 4 family
    ("claude-opus-4",               15.00,  75.00),
    ("claude-sonnet-4",              3.00,  15.00),
    ("claude-haiku-4",               0.80,   4.00),
    # Anthropic — Claude 3.x legacy
    ("claude-3-5-sonnet",            3.00,  15.00),
    ("claude-3-5-haiku",             0.80,   4.00),
    ("claude-3-opus",               15.00,  75.00),
    ("claude-3-sonnet",              3.00,  15.00),
    ("claude-3-haiku",               0.25,   1.25),
    # OpenRouter — Google models
    ("google/gemini-2.5-flash",      0.15,   0.60),
    ("google/gemini-2.0-flash-lite", 0.075,  0.30),
    ("google/gemini-2.0-flash",      0.10,   0.40),
    ("google/gemini-flash",          0.10,   0.40),
    # OpenRouter — ByteDance UI-TARS
    ("bytedance/ui-tars",            0.40,   0.40),
    # Gemini Live (Google AI Dev) — approximate; SDK doesn't expose token counts
    ("gemini-2.5-flash",             0.15,   0.60),
    ("gemini-2.0-flash-live",        0.10,   0.40),
    ("gemini-live",                  0.10,   0.40),
    ("gemini",                       0.10,   0.40),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 1,
    cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage_events(task_id);
"""


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    key = model.lower()
    for prefix, in_rate, out_rate in _PRICING:
        if key.startswith(prefix):
            return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return 0.0


async def record(
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    task_id: str | None = None,
    requests: int = 1,
) -> None:
    """Record a single API call. Fire-and-forget safe — all errors are logged."""
    cost = _calc_cost(model, input_tokens, output_tokens)
    try:
        conn = await db.get_db()
        try:
            await conn.execute(
                """INSERT INTO usage_events
                   (id, ts, provider, model, task_id, input_tokens, output_tokens, requests, cost_usd)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (db.new_id(), time.time(), provider, model, task_id,
                 input_tokens, output_tokens, requests, cost),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception as e:
        log.debug(f"usage.record error: {e}")


def record_nowait(
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    task_id: str | None = None,
    requests: int = 1,
) -> None:
    """Schedule record() as a fire-and-forget asyncio task."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(record(provider, model, input_tokens, output_tokens, task_id, requests))
    except RuntimeError:
        pass  # no running event loop — skip silently


async def get_stats(since_ts: float | None = None) -> dict:
    """Return aggregated usage stats grouped by provider+model.

    Returns:
      {
        "rows": [
          {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "requests": 42,
            "input_tokens": 123456,
            "output_tokens": 7890,
            "cost_usd": 0.49
          },
          ...
        ],
        "totals": { "requests": N, "input_tokens": N, "output_tokens": N, "cost_usd": N },
        "daily": [{"date": "2026-03-04", "cost_usd": 0.12, "requests": 5}, ...]
      }
    """
    conn = await db.get_db()
    try:
        await conn.executescript(_SCHEMA)

        where = "WHERE ts >= ?" if since_ts else ""
        params = (since_ts,) if since_ts else ()

        rows = []
        async with conn.execute(
            f"""SELECT provider, model,
                       SUM(requests) as requests,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cost_usd) as cost_usd
                FROM usage_events {where}
                GROUP BY provider, model
                ORDER BY cost_usd DESC""",
            params,
        ) as cur:
            async for row in cur:
                rows.append({
                    "provider": row["provider"],
                    "model": row["model"],
                    "requests": row["requests"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "cost_usd": round(row["cost_usd"], 6),
                })

        # Daily breakdown (last 30 days)
        daily = []
        async with conn.execute(
            """SELECT date(ts, 'unixepoch') as day,
                      SUM(cost_usd) as cost_usd,
                      SUM(requests) as requests,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens
               FROM usage_events
               WHERE ts >= strftime('%s','now','-30 days')
               GROUP BY day
               ORDER BY day ASC""",
        ) as cur:
            async for row in cur:
                daily.append({
                    "date": row["day"],
                    "cost_usd": round(row["cost_usd"], 6),
                    "requests": row["requests"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                })

        totals = {
            "requests": sum(r["requests"] for r in rows),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        }
        return {"rows": rows, "totals": totals, "daily": daily}
    finally:
        await conn.close()
