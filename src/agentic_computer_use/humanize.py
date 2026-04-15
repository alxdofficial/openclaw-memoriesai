"""Reliability-preserving humanization for xdotool-driven actions.

Default ON (env: ACU_HUMANIZE=1). Tests flip it off via the `humanize_off`
pytest fixture. Sensitive sub-agents (LinkedIn, TikTok, Instagram) inherit
the default; speed-critical sub-tasks call `humanize_set(False)` for a
session.

Design rule: only add humanizations that cannot change the final state of
the GUI. So we vary *timing* and *mouse path* but never *what gets typed*
or *where we click* (beyond a tiny bbox-bounded jitter). Specifically:

  ✅ variable inter-key typing delays   — timing only, text unchanged
  ✅ word-boundary pauses               — timing only
  ✅ quadratic Bezier mouse paths       — endpoint is exact target
  ✅ click dwell (mousedown → mouseup)  — 50-120ms, well under drag threshold
  ✅ pre-action "thinking" pause        — sleep before the next tool
  ✅ tiny click jitter (±1–2 px)        — clamped inside known bbox
  ❌ typos + backspace                  — would change final text
  ❌ overshoot + correct                — could misclick small targets
  ❌ random scroll velocity             — not asked for, mild signal
  ❌ rare double-click                  — risks triggering wrong UI

Everything is pure (returns schedules) except `apply_thinking_pause` which
sleeps. Callers decide when to `time.sleep` so the engine can still log
timing accurately.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Generator

# ── State singleton ────────────────────────────────────────────

def _read_env_default() -> bool:
    return os.environ.get("ACU_HUMANIZE", "1").strip().lower() in ("1", "true", "yes", "on")


class _State:
    """Thread-safe humanization enable flag (the daemon is aiohttp-async but
    we still hold a lock so POST /humanize and concurrent tool calls can't
    race)."""
    def __init__(self) -> None:
        self._enabled = _read_env_default()
        self._since = time.time()
        self._reason = "default from ACU_HUMANIZE"
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        return self._enabled

    def set(self, enabled: bool, reason: str = "") -> None:
        with self._lock:
            self._enabled = bool(enabled)
            self._since = time.time()
            self._reason = reason or ("explicit enable" if enabled else "explicit disable")

    def snapshot(self) -> dict:
        return {
            "enabled": self._enabled,
            "since": self._since,
            "reason": self._reason,
            "defaults": {
                "typing_mean_ms": int(TYPING_MEAN_S * 1000),
                "typing_range_ms": [int(TYPING_MIN_S * 1000), int(TYPING_MAX_S * 1000)],
                "word_pause_prob": WORD_PAUSE_PROB,
                "word_pause_range_ms": [WORD_PAUSE_MIN_MS, WORD_PAUSE_MAX_MS],
                "click_dwell_range_ms": [CLICK_DWELL_MIN_MS, CLICK_DWELL_MAX_MS],
                "thinking_pause_range_ms": [THINKING_MIN_MS, THINKING_MAX_MS],
                "click_jitter_px": CLICK_JITTER_PX,
                "mouse_min_steps": MOUSE_MIN_STEPS,
                "mouse_step_target_ms": MOUSE_STEP_TARGET_MS,
            },
        }


_state = _State()


def is_enabled() -> bool:
    return _state.is_enabled()


def set_enabled(enabled: bool, reason: str = "") -> None:
    _state.set(enabled, reason)


def snapshot() -> dict:
    return _state.snapshot()


def _reset_for_tests() -> None:
    """Restore the env-default state. Only meant to be called from pytest."""
    _state.__init__()  # type: ignore[misc]


# ── Tunables (module-level so `snapshot()` can report them) ────

TYPING_MEAN_S = 0.115          # ~110ms per key = ~100 wpm with pauses
TYPING_SIGMA = 0.38            # lognormal σ
TYPING_MIN_S = 0.055           # 55ms floor (fast but possible)
TYPING_MAX_S = 0.320           # 320ms ceiling (longest plausible intra-word)

WORD_PAUSE_PROB = 0.22         # 22% of whitespace/punctuation boundaries
WORD_PAUSE_MIN_MS = 180
WORD_PAUSE_MAX_MS = 600

CLICK_DWELL_MIN_MS = 40
CLICK_DWELL_MAX_MS = 120       # below 200ms drag threshold on every DE

THINKING_MIN_MS = 180
THINKING_MAX_MS = 900

CLICK_JITTER_PX = 2            # ±N px, default 2 (~clicking center of a 20px button)
CLICK_JITTER_BBOX_INSET = 0.15 # when bbox given, keep jitter within 15% inset

MOUSE_MIN_STEPS = 6            # even short moves get a brief arc
MOUSE_MAX_STEPS = 40
MOUSE_STEP_TARGET_MS = 14      # ~70fps, sub-frame visible in 30fps frame buffer
MOUSE_FITTS_A = 140.0          # ms, fixed overhead
MOUSE_FITTS_B = 80.0           # ms per log2 unit of normalized distance

BEZIER_CTRL_OFFSET_MIN = 0.08  # control pt offset 8–25% of distance
BEZIER_CTRL_OFFSET_MAX = 0.25


# ── Typing cadence ─────────────────────────────────────────────

def _lognormal_delay(rng: random.Random | None = None) -> float:
    r = rng or random
    # Shift lognormal so its mode is near TYPING_MEAN_S.
    mu = math.log(TYPING_MEAN_S)
    x = r.lognormvariate(mu, TYPING_SIGMA)
    return max(TYPING_MIN_S, min(TYPING_MAX_S, x))


def typing_delays(text: str, seed: int | None = None) -> Generator[float, None, None]:
    """Yield per-character delay (seconds to wait AFTER typing this char).

    Reliability invariant: the text is unchanged; only the schedule varies.
    """
    if not is_enabled():
        # Match legacy xdotool `--delay 12` exactly so timing regressions are bisectable.
        for _ in text:
            yield 0.012
        return

    rng = random.Random(seed) if seed is not None else random
    prev_boundary = False
    for ch in text:
        delay = _lognormal_delay(rng)
        is_boundary = ch in (" ", "\t", "\n", ".", ",", ";", ":", "!", "?")
        if is_boundary and not prev_boundary and rng.random() < WORD_PAUSE_PROB:
            delay += rng.uniform(WORD_PAUSE_MIN_MS, WORD_PAUSE_MAX_MS) / 1000.0
        prev_boundary = is_boundary
        yield delay


# ── Click dwell, jitter, thinking pause ────────────────────────

def click_dwell_s(rng: random.Random | None = None) -> float:
    if not is_enabled():
        return 0.0
    r = rng or random
    return r.uniform(CLICK_DWELL_MIN_MS, CLICK_DWELL_MAX_MS) / 1000.0


def thinking_pause_s(rng: random.Random | None = None) -> float:
    if not is_enabled():
        return 0.0
    r = rng or random
    return r.uniform(THINKING_MIN_MS, THINKING_MAX_MS) / 1000.0


def apply_thinking_pause() -> float:
    """Sleep for a human-like pre-action pause. Returns seconds slept."""
    dt = thinking_pause_s()
    if dt > 0:
        time.sleep(dt)
    return dt


def jitter_click_coords(
    x: int, y: int,
    bbox: tuple[int, int, int, int] | None = None,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    """Return (x', y') with a tiny jitter, strictly inside bbox when given.

    `bbox` is (x0, y0, x1, y1) in absolute screen coords.
    """
    if not is_enabled():
        return x, y
    r = rng or random
    jx = r.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    jy = r.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    nx, ny = x + jx, y + jy
    if bbox:
        x0, y0, x1, y1 = bbox
        inset_w = max(1, int((x1 - x0) * CLICK_JITTER_BBOX_INSET))
        inset_h = max(1, int((y1 - y0) * CLICK_JITTER_BBOX_INSET))
        nx = max(x0 + inset_w, min(x1 - inset_w, nx))
        ny = max(y0 + inset_h, min(y1 - inset_h, ny))
    return nx, ny


# ── Mouse path ─────────────────────────────────────────────────

@dataclass(frozen=True)
class MouseWaypoint:
    x: int
    y: int
    sleep_before_next_s: float


def _fitts_duration_ms(distance_px: float) -> float:
    # Simple distance-only scaling (we don't always know target width);
    # this undershoots Fitts' slightly but feels natural and is fast enough.
    normalized = max(1.0, distance_px / 100.0)
    return MOUSE_FITTS_A + MOUSE_FITTS_B * math.log2(1.0 + normalized)


def _quad_bezier(t: float, p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    u = 1.0 - t
    x = u * u * p0[0] + 2.0 * u * t * p1[0] + t * t * p2[0]
    y = u * u * p0[1] + 2.0 * u * t * p1[1] + t * t * p2[1]
    return x, y


def _ease_in_out(t: float) -> float:
    # Smoothstep — slow start, fast middle, slow end. Matches how humans move.
    return t * t * (3.0 - 2.0 * t)


def bezier_path(
    x0: int, y0: int, x1: int, y1: int,
    *,
    screen_bounds: tuple[int, int, int, int] | None = None,
    seed: int | None = None,
) -> list[MouseWaypoint]:
    """Compute a waypoint schedule from (x0,y0) to (x1,y1).

    Reliability invariants:
      * The last waypoint's (x, y) is exactly (x1, y1).
      * The path never leaves screen_bounds (if given).
      * When humanization is off, returns a single waypoint at the target
        (caller just teleports).
    """
    if not is_enabled():
        return [MouseWaypoint(x1, y1, 0.0)]

    rng = random.Random(seed) if seed is not None else random
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    if distance < 2.0:
        return [MouseWaypoint(x1, y1, 0.0)]

    duration_s = _fitts_duration_ms(distance) / 1000.0
    n_steps = max(MOUSE_MIN_STEPS,
                  min(MOUSE_MAX_STEPS,
                      int(duration_s * 1000.0 / MOUSE_STEP_TARGET_MS)))

    # Perpendicular offset to make the path arc slightly — random side,
    # small magnitude so it can't leave the screen even for long moves.
    perp_x, perp_y = -dy / distance, dx / distance
    offset_frac = rng.uniform(BEZIER_CTRL_OFFSET_MIN, BEZIER_CTRL_OFFSET_MAX)
    side = rng.choice((-1.0, 1.0))
    offset_mag = distance * offset_frac * side
    mid_x = (x0 + x1) / 2.0 + perp_x * offset_mag
    mid_y = (y0 + y1) / 2.0 + perp_y * offset_mag

    # Clamp control point to screen bounds (if known) so the Bezier stays visible.
    if screen_bounds:
        sx0, sy0, sx1, sy1 = screen_bounds
        mid_x = max(sx0 + 1, min(sx1 - 1, mid_x))
        mid_y = max(sy0 + 1, min(sy1 - 1, mid_y))

    p0, p1, p2 = (x0, y0), (mid_x, mid_y), (x1, y1)
    step_sleep = duration_s / n_steps

    waypoints: list[MouseWaypoint] = []
    for i in range(1, n_steps):
        t = _ease_in_out(i / n_steps)
        px, py = _quad_bezier(t, p0, p1, p2)
        if screen_bounds:
            sx0, sy0, sx1, sy1 = screen_bounds
            px = max(sx0, min(sx1 - 1, px))
            py = max(sy0, min(sy1 - 1, py))
        waypoints.append(MouseWaypoint(int(round(px)), int(round(py)), step_sleep))

    # Exact landing — invariant that makes this safe for tiny targets.
    waypoints.append(MouseWaypoint(x1, y1, 0.0))
    return waypoints
