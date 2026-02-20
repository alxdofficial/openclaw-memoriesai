# Performance Optimization — SmartWait Pipeline

## Overview

This document summarizes all performance improvements made to the OpenClaw
agentic-computer-use pipeline, along with a cost analysis of vision backends.

---

## Before / After

| Metric | Before | After |
|---|---|---|
| Vision HTTP overhead (per call) | 100–300 ms (new TLS + TCP every call) | ~0 ms (persistent connection) |
| Ollama cold-start penalty | 5–30 s (model reloaded each time) | 0 s (model stays loaded for 10 min) |
| Frame capture blocking | Blocks event loop 30–100 ms | Off-thread via `run_in_executor` |
| JPEG encoding blocking | Blocks event loop 10–50 ms | Off-thread via `run_in_executor` |
| Multiple active wait jobs | Evaluated **serially** (one at a time) | Evaluated **in parallel** via `asyncio.gather` |
| Pixel diff computation | Full 1920×1080 numpy array (~6.2M cells) | 320×? downsampled array (~200K cells, 30× faster) |
| `openclaw` CLI injection | Blocks event loop for up to 10 s | Async subprocess, event loop unblocked |
| Dashboard frame buffer | Captured at 4 fps (every 250 ms) | Captured at 2 fps (every 500 ms) |
| Vision cloud option | None | OpenRouter added (no GPU required) |

---

## Changes in Detail

### Phase 1 — HTTP Connection Pooling (`vision/backends/*.py`)

**Problem:** Each vision call created a new `httpx.AsyncClient`, performed a
full TLS handshake + TCP connection, then tore it down after one request.

**Fix:** Module-level persistent `httpx.AsyncClient` instances, reused across
all calls. Created lazily on first use.

**Files changed:** `claude.py`, `ollama.py`, `vllm.py`

**Gain:** Saves 100–300 ms per vision call for cloud APIs, 20–50 ms for local Ollama.

---

### Phase 1b — OpenRouter Vision Backend (`vision/backends/openrouter.py`)

**New feature:** A new `openrouter` backend lets you route SmartWait vision
queries to any model on OpenRouter (Claude Haiku, Gemini Flash, etc.) without
running a local GPU.

**Config:**
```bash
ACU_VISION_BACKEND=openrouter
OPENROUTER_API_KEY=sk-or-...
ACU_OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-001  # or anthropic/claude-haiku-4-5
```

**Files changed:** `vision/backends/openrouter.py` (new), `vision/__init__.py`

---

### Phase 2 — Async Frame Capture (`wait/engine.py`)

**Problem:** `capture_screen()` (Xlib round-trip) and `frame_to_jpeg()` /
`frame_to_thumbnail()` (PIL compression) were called bare in the async event
loop, blocking it for 30–150 ms per poll cycle.

**Fix:** Both operations are now dispatched to the thread pool via
`loop.run_in_executor(None, ...)`.

**Note:** A module-level `asyncio.Lock` (`_CAPTURE_LOCK`) serializes Xlib
calls across concurrent job evaluations, since Xlib is not thread-safe.

**Gain:** Event loop freed during capture; reduces latency for other async
work (API responses, DB writes, dashboard polling) competing for the loop.

---

### Phase 3 — Parallel Job Evaluation (`wait/engine.py`)

**Problem:** The engine loop processed exactly one wait job per iteration.
If job A was waiting on a slow vision call (1–30 s), job B was completely
blocked.

**Fix:** The loop now collects **all overdue jobs** at each tick and evaluates
them concurrently with `asyncio.gather()`. Vision I/O is pure async and fully
parallel; frame captures share `_CAPTURE_LOCK` to stay thread-safe.

Both `_resolve_job` and `_timeout_job` guard against jobs that were concurrently
cancelled or resolved (check `if job.id not in self.jobs` before acting).

**Gain:** With 2 active wait jobs, evaluation latency is cut in half. With N
jobs, all are evaluated in the time of the slowest single vision call.

---

### Phase 4 — Ollama keep_alive (`config.py`)

**Problem:** `OLLAMA_KEEP_ALIVE` defaulted to `"0"`, meaning Ollama unloaded
the vision model from VRAM immediately after each response. The next call paid
a 5–30 s cold-start reload penalty.

**Fix:** Default changed to `"10m"` — model stays in VRAM for 10 minutes of
inactivity, then unloads automatically.

**Override:** Set `OLLAMA_KEEP_ALIVE=0` in `.env` to revert to the old
behaviour (useful if VRAM is shared and you need it freed between tasks).

**Gain:** Eliminates the dominant bottleneck for local Ollama inference between
closely-spaced wait jobs.

---

### Phase 5 — (deferred)

Reducing `FRAME_MAX_DIM` for vision inputs was considered but skipped: small
vision models (minicpm-v, UI-TARS) miss text at lower resolution. Full 1920px
frames are still sent to the model. Re-evaluate if you switch to a
higher-capability model like Gemini 2.0 Flash.

---

### Phase 6 — Diff Downsampling (`capture/diff.py`)

**Problem:** `PixelDiffGate` computed pixel-level differences on the full
1920×1080 frame — ~6.2 million array cells via numpy every poll cycle.

**Fix:** Frames are decimated to ≤320 px wide (integer stride, ~200K cells)
before the diff comparison. The downsampled copy is stored as `last_frame`.
The full-resolution frame is still passed to the vision model when a diff is
detected.

**Config:** `ACU_DIFF_MAX_WIDTH=320` (default). Increase for higher accuracy
on scenes with subtle per-pixel changes; decrease to save more CPU.

**Gain:** ~30× reduction in diff computation cost. At 2 s poll interval this
saves ~15 ms/s of CPU on slow hardware.

---

### Phase 7 — Async Subprocess (`wait/engine.py`)

**Problem:** `_inject_system_event` used `subprocess.run()` with a 10 s
timeout to call the `openclaw` CLI. This is a **blocking** call that froze
the entire event loop for up to 10 s on each resolved wait job.

**Fix:** Replaced with `asyncio.create_subprocess_exec()` +
`asyncio.wait_for(proc.communicate(), timeout=10)`. The process runs
concurrently; the event loop stays responsive during the CLI call.

**Gain:** Event loop is no longer frozen on wait resolution. Dashboard
updates, new wait jobs, and DB writes all proceed normally during the CLI call.

---

### Phase 8 — Frame Buffer Rate (`daemon.py`)

**Problem:** The dashboard frame buffer captured a new screenshot every 250 ms
(~4 fps), making 4 Xlib round-trips per second regardless of activity.

**Fix:** Sleep increased to 500 ms (~2 fps). Dashboard live-view still feels
live at 2 fps; humans don't perceive the difference during monitoring.

**Gain:** Halved Xlib load on the system display thread. Reduces contention
with wait engine captures on `_CAPTURE_LOCK`.

---

## Vision Backend Cost Comparison

For SmartWait, each evaluation sends 1–4 JPEG screenshots + a text prompt
(~200–400 tokens) and receives a structured verdict (~100–200 tokens).

### Local (Ollama)

| Option | Cost | GPU required | Cold start |
|---|---|---|---|
| `minicpm-v` / `llava` | Free | Yes (4–8 GB VRAM) | 5–30 s without keep_alive |
| UI-TARS 7B (vLLM) | Free | Yes (16 GB VRAM) | 5–30 s |

### Cloud via OpenRouter

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Image cost (est.) | Notes |
|---|---|---|---|---|
| `google/gemini-2.0-flash-001` | **$0.10** | **$0.40** | ~$0.00011/image | Fastest, cheapest |
| `anthropic/claude-haiku-4-5` | $1.00 | $5.00 | ~$0.0011/image | Reliable, better at text |
| `anthropic/claude-sonnet-4-5` | $3.00 | $15.00 | ~$0.003/image | High quality, expensive |

**Estimated cost per SmartWait evaluation** (1 screenshot, 300 input tokens, 150 output tokens):

| Model | Approx. cost |
|---|---|
| Gemini 2.0 Flash | ~$0.00022 |
| Claude Haiku 4.5 | ~$0.0022 |
| Claude Sonnet 4.5 | ~$0.0066 |

At 1000 wait evaluations per day:

| Model | Daily cost | Monthly cost |
|---|---|---|
| Gemini 2.0 Flash | ~$0.22 | ~$6.60 |
| Claude Haiku 4.5 | ~$2.20 | ~$66 |

### Is OpenRouter more expensive than going direct?

**No — for most cases it's the same price or nearly so.**

- OpenRouter passes through provider pricing with **no markup on the model cost itself**.
- Platform fee: ~5.5% on credit purchases (e.g., buying $100 of credits costs $5.50 extra).
- BYOK (bring your own key): OpenRouter now offers the first 1M requests with no additional fee.
- Gemini 2.0 Flash through OpenRouter is priced identically to Google AI Studio rates.
- Claude Haiku 4.5 through OpenRouter is priced identically to Anthropic's direct API rates.

**Recommendation:** Use `google/gemini-2.0-flash-001` via OpenRouter for the best cost/speed ratio.
It is **10× cheaper** than Claude Haiku and has excellent multimodal capability for UI screenshots.

Set in `.env`:
```bash
ACU_VISION_BACKEND=openrouter
OPENROUTER_API_KEY=sk-or-...
ACU_OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-001
```

---

## Summary — Recommended Config for Production

```bash
# Vision backend
ACU_VISION_BACKEND=openrouter
OPENROUTER_API_KEY=sk-or-...
ACU_OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-001

# SmartWait tuning
ACU_PARTIAL_STREAK_RESOLVE=2   # was 3 — faster resolution
ACU_MAX_POLL_INTERVAL=5.0      # was 15s — more responsive
OLLAMA_KEEP_ALIVE=10m          # avoids cold-start if also using Ollama

# Diff gate (optional)
ACU_DIFF_MAX_WIDTH=320         # default — reduce CPU for diff
```

---

*Last updated: 2026-02-20*
*Commit: d95a610*
