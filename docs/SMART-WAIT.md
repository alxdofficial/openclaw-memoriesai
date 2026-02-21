# Smart Wait — Design & Architecture

## Overview

Smart Wait lets an LLM agent delegate screen monitoring to a lightweight daemon. Instead of
the LLM polling the screen itself (expensive, slow), it hands off a condition string and a
timeout. The daemon watches the screen every second, asks a vision model "is this done?",
and wakes the LLM the moment it is — or when the timeout expires.

## Why Not Just Poll?

| Approach | Cost (5-min wait) | Latency | Intelligence |
|---|---|---|---|
| LLM screenshot polling (5s) | ~60 Claude vision calls ($$$) | 5 s | High |
| `sleep(300)` | Free | 300 s (wastes time) | None |
| `exec notifyOnExit` | Free | Instant (CLI only, no GUI) | N/A |
| **Smart Wait** | **~$0.000045/eval × N evals** | **1–2 s** | **High** |

At 1 eval/second over 5 minutes = 300 evals × $0.000045 = **$0.013**. For a 30s wait = $0.0013.

---

## Architecture

```
OpenClaw LLM
    │
    │  smart_wait(criteria="download finished", timeout=300)
    ▼
  Daemon (agentic_computer_use daemon)
    │
    │  Registers WaitJob in WaitEngine
    ▼
  WaitEngine (async loop, 1 s poll)
    │
    ├── capture_screen() via Xlib → numpy frame
    ├── frame_to_jpeg() → JPEG @ 960px max, quality 72
    │
    │  "Is this condition met? YES: / NO:"
    ▼
  OpenRouter → Gemini Flash Lite 2.0
    │
    ├── NO → sleep 1 s, repeat
    └── YES (or timeout) ──▶ openclaw system event → LLM wakes
```

---

## WaitJob Lifecycle

### 1. LLM creates a wait job

The LLM calls `smart_wait` with:
- `criteria`: natural-language condition, e.g. `"Firefox has finished loading the page"`
- `timeout`: maximum seconds to wait before waking the LLM regardless
- `target`: `screen` (full desktop), `window:<name or id>`, or `pty:<session>`
- `task_id`: optional, links the job to a task for automatic progress logging

The daemon registers a `WaitJob` in the `WaitEngine` and returns immediately.
The LLM context is freed — it does nothing while waiting.

### 2. Engine loop (1 second poll)

```python
POLL_INTERVAL = 1.0  # fixed — no adaptive logic

while jobs:
    overdue = [j for j in jobs if j.next_check_at <= now]
    await asyncio.gather(*[_evaluate_job(j) for j in overdue])
```

All overdue jobs are evaluated **in parallel** via `asyncio.gather`. With 3 concurrent wait
jobs, total latency per tick = time of the slowest single vision call.

### 3. Per-evaluation flow

```
capture frame → encode JPEG → ask vision model → parse YES/NO → act or sleep
```

**Frame capture** is serialized per display (Xlib is not thread-safe per connection)
via `_CAPTURE_LOCKS: dict[str, asyncio.Lock]`. Jobs on different Xvfb displays run
captures in parallel.

**JPEG encoding** (`frame_to_jpeg`) is dispatched off-thread via `run_in_executor` —
the async event loop is never blocked by PIL compression.

### 4. Vision evaluation

The engine sends one JPEG + a short prompt to Gemini Flash Lite via OpenRouter:

```
Look at this screenshot and tell me if the following condition is met.

CONDITION: Firefox has finished loading the page

Time elapsed waiting: 12s

Reply with ONLY one of these two formats:
YES: <one sentence of visible evidence confirming the condition is met>
NO: <one sentence explaining what is missing or not yet visible>
```

The model replies in ~200–400 ms. The response is parsed to YES or NO:

```python
def _parse_verdict(response: str) -> tuple[str, str]:
    if response.upper().startswith("YES"):
        return ("resolved", detail)
    return ("watching", detail)
```

**There is no partial verdict.** The condition is either met (YES) or not (NO).
Timeout is the only fallback if the condition is never satisfied.

### 5. Resolution

**On YES:** The engine removes the job, persists `resolved` to the DB, saves a
screenshot, updates the linked task, and injects a system event into OpenClaw:

```bash
openclaw system event --text "[smart_wait resolved] Job X: <criteria> → <evidence>" --mode now
```

OpenClaw wakes the LLM immediately with that message as context.

**On timeout:** Same flow with `state=timeout` and the message:
`"[smart_wait timeout] Job X: <criteria> — Timeout after 300s."`
The LLM wakes and decides how to handle it.

---

## Key Design Decisions

### No pixel diff gate

Earlier versions skipped vision evaluation when the screen was "static" (< 1% pixel change
at 320px). This was removed because:
- Small but important changes (status indicator, tiny dialog, checkmark) don't trigger 1% diff
- With Gemini Flash Lite at ~$0.000045/call, polling every 1s for 5 minutes costs $0.013
- The correctness cost of missing a change far outweighs the tiny API cost

### No partial verdict

The earlier design had a `partial` verdict that accelerated polling and counted streaks.
Removed because:
- Ambiguous: "70% complete" is no more actionable than "not done yet"
- Streak logic added complexity without meaningful benefit
- **Timeout is the correct fallback** — the LLM set a timeout because it knows how long the task should take

### Fixed 1 second poll interval

Adaptive polling (speeding up on partial, slowing on static) was removed with the diff gate
and partial verdict. Fixed 1s is simpler, predictable, and fast enough for all GUI wait conditions.

### Single frame per evaluation

Earlier versions maintained a rolling context window (4 frames + 3 verdicts). Removed:
- The model's job is simple — evaluate one screenshot for a binary condition
- Context history adds tokens (cost) without improving YES/NO accuracy
- Single-frame eval is faster and cheaper

---

## Vision Backend

**Default: OpenRouter → Gemini 2.0 Flash Lite**

```bash
ACU_VISION_BACKEND=openrouter
OPENROUTER_API_KEY=sk-or-...
ACU_OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-lite-001  # default
```

| Model | Cost/eval | Notes |
|---|---|---|
| `google/gemini-2.0-flash-lite-001` | ~$0.000045 | Default — fastest, cheapest |
| `google/gemini-2.0-flash-001` | ~$0.00022 | Higher quality if needed |
| `anthropic/claude-haiku-4-5` | ~$0.0022 | Reliable, good at OCR |
| Local Ollama (`minicpm-v`) | Free | Requires GPU; 5–30s cold-start |

**Why Gemini Flash Lite?**
- Designed for low-latency, high-volume image classification tasks
- YES/NO from a screenshot is exactly that task
- ~5× cheaper than Gemini 2.0 Flash, 50× cheaper than Claude Haiku
- 200–400 ms response time via OpenRouter

**Frame size:** 960px max dimension, JPEG quality 72. This is sufficient for reading
UI state — progress bars, dialog boxes, page content. Full 1920px is unnecessary for
binary condition evaluation and would double payload size.

---

## Frame Capture Pipeline

```
Xvfb (:99) ← virtual X11 framebuffer in RAM
    │
    ▼
python-xlib reads pixels (Xlib round-trip, ~5–15 ms)
    │
    ▼
numpy array (H × W × 3, BGR)
    │
    ▼ channel swap BGR→RGB + resize to ≤960px + JPEG encode (quality 72)
    │  dispatched off-thread via run_in_executor
    ▼
~30–60 KB JPEG sent to vision model
```

### Headless Setup (Xvfb)

```bash
# Install
sudo apt install xvfb xfce4

# Start virtual display (1280x720, 24-bit color)
Xvfb :99 -screen 0 1280x720x24 -ac &
export DISPLAY=:99

# Start window manager
startxfce4 &
```

Systemd service:
```ini
# /etc/systemd/system/xvfb.service
[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x720x24 -ac
Restart=always
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `ACU_VISION_BACKEND` | `ollama` | Vision backend: `ollama`, `openrouter`, `claude`, `vllm` |
| `ACU_OPENROUTER_VISION_MODEL` | `google/gemini-2.0-flash-lite-001` | Model for OpenRouter backend |
| `OPENROUTER_API_KEY` | _(empty)_ | OpenRouter API key |
| `ACU_FRAME_MAX_DIM` | `960` | Max pixel dimension for vision model input |
| `ACU_FRAME_JPEG_QUALITY` | `72` | JPEG quality for vision model input |
| `ACU_VISION_SYSTEM_INSTRUCTIONS` | _(see config.py)_ | System prompt for vision model |

---

## PTY Targets

`target=pty:<session_id>` links the wait to a terminal session identity. Frame capture is
still visual (screen pipeline), not terminal-buffer parsing. For best results:

- Prefer `target=window:<name>` so the model sees only the terminal window
- The full desktop capture works too — the model can identify the relevant terminal

---

## Multiple Concurrent Wait Jobs

The engine handles all active jobs in a single async loop. Example: 3 concurrent waits:

```
Tick 1 (t=0s):
  Job A: capture :99 → vision call (200ms)
  Job B: capture :99 → vision call (200ms)  ← serialized with A on :99
  Job C: capture :100 → vision call (200ms) ← parallel (different display)

Tick 2 (t=1s):
  A: NO, schedule next_check_at = t+1s
  B: YES → resolve → inject openclaw event → task update
  C: NO, schedule next_check_at = t+1s
```

Jobs on the same display serialize frame capture (Xlib safety). Vision calls are always
fully parallel — they're pure async HTTP.

---

*Last updated: 2026-02-21*
