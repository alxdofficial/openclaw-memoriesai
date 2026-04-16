"""Log slicing + per-task ZIP bundler.

Two surfaces:

  read_journal(since, until) -> str
      Slices the systemd journal for detm-daemon. Uses `journalctl -u
      detm-daemon` which works unprivileged on most systemd setups when
      the service is owned by the invoking user (which detm-daemon is —
      see install.sh). If it fails (permission, missing journalctl) the
      caller gets an explanatory header but no raise.

  build_task_bundle(task_id) -> (bytes, str)
      Returns (zip_bytes, filename). The zip contains:
        - README.txt        — contents + provenance
        - task.json         — full hierarchical dump (items + actions + logs)
        - messages.json     — message thread
        - journal.log       — journalctl slice covering the task's time window
        - debug.log         — ~/.agentic-computer-use/logs/debug.log slice
                              (whole file, only helpful if ACU_DEBUG=1)
        - screenshots/      — per-action before/after thumbnails that still
                              exist on disk (older tasks may have pruned ones)
        - live_sessions/…   — events.jsonl + frames + audio, if any
        - recordings/       — .mp4 clips referenced by the task
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import subprocess
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

JOURNAL_UNIT = "detm-daemon"
MAX_JOURNAL_LINES = 200_000  # hard cap so a 10-year window doesn't OOM the daemon


# ── Journal slicing ───────────────────────────────────────────

def _format_ts(value: str | float | None) -> str | None:
    """Accept ISO-8601, epoch seconds, or None. Return a string journalctl
    accepts (`YYYY-MM-DD HH:MM:SS`), or None if not parseable."""
    if value is None or value == "":
        return None
    # Epoch seconds (as number or numeric string)
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        pass
    # ISO-8601 (YYYY-MM-DDTHH:MM:SS[.ms][Z|±HH:MM])
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return s  # let journalctl try to parse it — it accepts many formats


def read_journal(
    since: str | float | None = None,
    until: str | float | None = None,
    *,
    lines: int | None = None,
    unit: str = JOURNAL_UNIT,
) -> str:
    """Return a journalctl slice as a single string. Failure returns a
    one-line header explaining why — callers can include it in a bundle
    without special-casing."""
    cmd = ["journalctl", "-u", unit, "--no-pager", "--output=short-iso"]
    s = _format_ts(since)
    u = _format_ts(until)
    if s:
        cmd += ["--since", s]
    if u:
        cmd += ["--until", u]
    if lines is not None and lines > 0:
        cmd += ["-n", str(min(lines, MAX_JOURNAL_LINES))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "# journalctl not installed — cannot read system journal\n"
    except subprocess.TimeoutExpired:
        return "# journalctl timed out after 30s\n"
    if r.returncode != 0:
        return f"# journalctl returned {r.returncode}: {r.stderr.strip()[:500]}\n"
    out = r.stdout
    # Enforce line cap post-hoc too
    split = out.splitlines()
    if len(split) > MAX_JOURNAL_LINES:
        out = "\n".join(split[-MAX_JOURNAL_LINES:])
        out = (
            f"# truncated: showed last {MAX_JOURNAL_LINES} of {len(split)} lines\n"
            + out
        )
    return out


def read_debug_log_slice(since: str | None = None) -> str:
    """Read debug.log (only populated when ACU_DEBUG=1). Optionally filter
    by a since timestamp. If the file is missing or empty, returns an
    explanatory header."""
    path = config.DATA_DIR / "logs" / "debug.log"
    if not path.exists():
        return "# debug.log does not exist (ACU_DEBUG=0 during this task)\n"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"# debug.log unreadable: {e}\n"
    if not since:
        return text
    # The daemon's debug log lines are prefixed HH:MM:SS.mmm (no date).
    # So "since" filtering on this file isn't reliable; return the whole file
    # with a header and let the downloader grep.
    return (
        "# debug.log has HH:MM:SS timestamps only (no date); returning full file.\n"
        "# Filter client-side if you need a subrange.\n"
        + text
    )


# ── Task bundle ───────────────────────────────────────────────

async def build_task_bundle(task_id: str) -> tuple[bytes, str]:
    """Walk the task's data (db + files) and build an in-memory ZIP.

    Returns (zip_bytes, filename). Raises ValueError if the task doesn't
    exist.
    """
    from .task import manager

    summary = await manager.get_task_summary(task_id=task_id, detail_level="actions")
    if summary.get("error"):
        raise ValueError(summary["error"])

    messages = await manager.get_task_messages(task_id=task_id, limit=10000) \
        if hasattr(manager, "get_task_messages") else {"messages": []}

    # Time window: derive from earliest action's created_at → last_update.
    # Summary doesn't carry a top-level created_at, so we scan actions.
    started = _earliest_action_ts(summary)
    finished = summary.get("last_update") or summary.get("updated_at")
    journal = read_journal(
        since=_maybe_pad(started, -60),     # 60s of context before
        until=_maybe_pad(finished, 60),     # 60s of context after
    )
    debug_slice = read_debug_log_slice()

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)

    def _writestr(name: str, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        zf.writestr(name, data)

    _writestr("task.json", json.dumps(summary, indent=2, default=str))
    _writestr("messages.json", json.dumps(messages, indent=2, default=str))
    _writestr("journal.log", journal)
    _writestr("debug.log", debug_slice)

    # Screenshots: walk action_details for referenced filenames and copy
    # whatever still exists on disk.
    screenshots_dir = config.DATA_DIR / "screenshots"
    found_shots, missing_shots = 0, 0
    for item in summary.get("items", []):
        for a in item.get("action_details", []) or []:
            for key in ("before_screenshot", "after_screenshot", "screenshot"):
                shot = a.get(key)
                if not shot:
                    continue
                # Some fields store an id (no extension), others a filename
                for fname in _shot_candidates(shot):
                    src = screenshots_dir / fname
                    if src.exists():
                        try:
                            zf.write(src, arcname=f"screenshots/{fname}")
                            found_shots += 1
                        except OSError:
                            missing_shots += 1
                        break
                else:
                    missing_shots += 1

    # Recordings: check for .mp4 files referenced by action_details or live_sessions
    rec_dir = config.DATA_DIR / "recordings"
    if rec_dir.exists():
        for rec in rec_dir.glob(f"*{task_id}*"):
            if rec.is_file():
                zf.write(rec, arcname=f"recordings/{rec.name}")

    # Live sessions referenced by the task
    ls_root = config.DATA_DIR / "live_sessions"
    if ls_root.exists():
        for sess_dir in _live_sessions_for_task(summary):
            full = ls_root / sess_dir
            if full.is_dir():
                for p in full.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=f"live_sessions/{sess_dir}/{p.relative_to(full)}")

    # README goes in last so it reflects what actually got packed.
    footer = (
        f"\n--\nPacked at {datetime.now(timezone.utc).isoformat()}:\n"
        f"  screenshots: {found_shots} included, {missing_shots} referenced-but-missing\n"
        f"  journal lines: {len(journal.splitlines())}\n"
        f"  debug.log bytes: {len(debug_slice)}\n"
    )
    readme = _bundle_readme(task_id, summary, started, finished) + footer
    _writestr("README.txt", readme)

    zf.close()
    payload = buf.getvalue()
    fname = f"detm-task-{task_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
    return payload, fname


def _earliest_action_ts(summary: dict) -> str | None:
    """Find the earliest action.created_at across all plan items."""
    stamps: list[str] = []
    for item in summary.get("items", []):
        for a in item.get("action_details", []) or []:
            t = a.get("created_at")
            if t:
                stamps.append(t)
    return min(stamps) if stamps else None


def _bundle_readme(task_id: str, summary: dict[str, Any], started: str | None,
                   finished: str | None) -> str:
    name = summary.get("name", "?")
    status = summary.get("status", "?")
    items = len(summary.get("items", []))
    return f"""DETM task bundle
task_id:  {task_id}
name:     {name}
status:   {status}
started:  {started or "?"}
finished: {finished or "?"}
items:    {items}

Contents:
  README.txt       this file
  task.json        full hierarchical task dump
  messages.json    task message thread
  journal.log      systemd journal slice (journalctl -u detm-daemon)
                   for the task's time window (±60s padding)
  debug.log        ~/.agentic-computer-use/logs/debug.log
                   (only populated when ACU_DEBUG=1)
  screenshots/     per-action screenshots that still exist on disk
  live_sessions/   per-live-session events.jsonl + frames + audio
  recordings/      per-task .mp4 clips if any

Notes:
  Older tasks may have had their screenshots pruned by the storage-cleanup
  loop (ACU_MAX_RECORDINGS_MB). The action records in task.json still
  reference filenames that no longer exist; consider those data points
  as "timing and outcome preserved, pixel evidence missing".
"""


def _maybe_pad(ts: str | None, seconds: int) -> str | None:
    """Nudge an ISO timestamp by ±seconds so the journal slice has some
    context before/after the task's exact window."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ts
    from datetime import timedelta
    return (dt + timedelta(seconds=seconds)).isoformat()


def _shot_candidates(value: str) -> list[str]:
    """Screenshot fields are sometimes an id, sometimes a filename."""
    if "/" in value or "\\" in value:
        value = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if value.endswith((".jpg", ".jpeg", ".png")):
        return [value]
    return [f"{value}_before.jpg", f"{value}_after.jpg",
            f"{value}_before_thumb.jpg", f"{value}_after_thumb.jpg"]


_SESSION_ID_RE = re.compile(r"session[_-]id[=:\s]+(\w+)")


def _live_sessions_for_task(summary: dict) -> list[str]:
    """Heuristically dig session_ids out of action_details (gui_agent
    records them). Also fall back to any 'live_sessions/<id>' mentioned
    anywhere in the dump."""
    ids: set[str] = set()
    text = json.dumps(summary, default=str)
    for m in _SESSION_ID_RE.finditer(text):
        ids.add(m.group(1))
    return sorted(ids)
