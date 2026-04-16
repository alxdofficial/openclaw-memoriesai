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


# ── Global logs dump (all sources, AI-agent manifest) ──────────

OPENCLAW_ROOT = Path.home() / ".openclaw"
OPENCLAW_GATEWAY_UNIT = "openclaw-gateway"   # user-level systemd unit
_SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{40,}"),      # OpenRouter
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{40,}"),     # Anthropic
    re.compile(r"ghp_[A-Za-z0-9]{36}"),            # GitHub PAT
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),         # Google API key
]
_REDACTED = "***REDACTED***"

# Errors worth grepping to ERRORS.log
_ERROR_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback|Exception|Error:)\b")
# HTTP 4xx/5xx access lines — the "agent forgot task_register" fingerprint
_HTTP_REJECT_RE = re.compile(r'aiohttp\.access.*HTTP/1\.[01]" (4\d{2}|5\d{2})')


def _redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


def read_user_journal(
    since: str | float | None = None,
    until: str | float | None = None,
    *,
    unit: str = OPENCLAW_GATEWAY_UNIT,
) -> str:
    """journalctl --user -u <unit> — OpenClaw gateway lives in the user journal."""
    cmd = ["journalctl", "--user", "-u", unit, "--no-pager", "--output=short-iso"]
    s = _format_ts(since)
    u = _format_ts(until)
    if s: cmd += ["--since", s]
    if u: cmd += ["--until", u]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "# journalctl not installed\n"
    except subprocess.TimeoutExpired:
        return "# journalctl timed out after 30s\n"
    if r.returncode != 0:
        return f"# user journal ({unit}) returned {r.returncode}: {r.stderr.strip()[:300]}\n"
    out = r.stdout
    split = out.splitlines()
    if len(split) > MAX_JOURNAL_LINES:
        out = f"# truncated: last {MAX_JOURNAL_LINES} of {len(split)} lines\n" + \
              "\n".join(split[-MAX_JOURNAL_LINES:])
    return out


def _session_overlaps_window(session_path: Path, since_dt, until_dt) -> bool:
    """Cheap check: first line's timestamp vs the window. Sessions are
    append-only, so first-line time <= all subsequent lines."""
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        obj = json.loads(first)
        start = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
        if until_dt and start > until_dt:
            return False
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        # Fall back to mtime
        try:
            mtime = datetime.fromtimestamp(session_path.stat().st_mtime, tz=timezone.utc)
            if since_dt and mtime < since_dt:
                return False
        except OSError:
            return False
    return True


def _openclaw_sessions_in_window(since_dt, until_dt) -> list[Path]:
    """Return active session files (no 'deleted'/'bak'/'reset' suffix) whose
    content overlaps [since_dt, until_dt]."""
    root = OPENCLAW_ROOT / "agents"
    if not root.exists():
        return []
    hits: list[Path] = []
    for agent_dir in root.iterdir():
        sess_dir = agent_dir / "sessions"
        if not sess_dir.is_dir():
            continue
        for f in sess_dir.iterdir():
            if not f.name.endswith(".jsonl"):
                continue
            if _session_overlaps_window(f, since_dt, until_dt):
                hits.append(f)
    return sorted(hits)


def _extract_errors(sources: dict[str, str]) -> str:
    """Grep ERROR/CRITICAL/Traceback lines from each named source. Returns
    a flat log with `[source] ` tags so operators can triage at a glance."""
    out: list[str] = []
    for name, text in sources.items():
        for line in text.splitlines():
            if _ERROR_RE.search(line):
                out.append(f"[{name}] {line}")
    return "\n".join(out) + ("\n" if out else "")


def _extract_rejected_calls(daemon_journal: str) -> str:
    out: list[str] = []
    for line in daemon_journal.splitlines():
        if _HTTP_REJECT_RE.search(line):
            out.append(line)
    if not out:
        return "# no 4xx/5xx HTTP rejections found in window — if the agent was calling tools without task_register, either the requests weren't sent or they were routed somewhere else.\n"
    return (
        "# HTTP 4xx/5xx access lines — most often the fingerprint of an agent calling\n"
        "# desktop_look / gui_agent / task_log_action WITHOUT having called task_register\n"
        "# first. Cross-reference each timestamp against openclaw/sessions/*.jsonl to see\n"
        "# what the model was attempting at that moment.\n\n"
        + "\n".join(out) + "\n"
    )


async def build_logs_dump(since: str | float | None = None,
                          until: str | float | None = None) -> tuple[bytes, str]:
    """Central logs dump. Returns (zip_bytes, filename).

    Includes DETM daemon logs + SQLite dump + OpenClaw sessions + gateway
    journal, with a MANIFEST.md written for AI agents to read.
    """
    import time as _time
    import sqlite3 as _sqlite3

    # Resolve window with sensible defaults
    if since is None:
        since = _time.time() - 86400  # last 24h
    since_str = _format_ts(since)
    until_str = _format_ts(until) if until else None

    since_dt = _parse_any_ts(since)
    until_dt = _parse_any_ts(until) if until else datetime.now(timezone.utc)

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)

    def _w(name: str, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        zf.writestr(name, data)

    # 1. DETM daemon journal
    daemon_journal = read_journal(since=since, until=until)
    _w("detm/daemon.journal.log", daemon_journal)

    # 2. DETM debug log
    _w("detm/debug.log", read_debug_log_slice())

    # 3. DETM SQLite slice — tasks + children within window
    detm_rows = _dump_detm_sqlite(since_dt, until_dt)
    _w("detm/tasks.json", json.dumps(detm_rows["tasks"], indent=2, default=str))
    _w("detm/actions-flat.json", json.dumps(detm_rows["actions"], indent=2, default=str))
    _w("detm/task-messages.json", json.dumps(detm_rows["messages"], indent=2, default=str))
    _w("detm/wait-jobs.json", json.dumps(detm_rows["waits"], indent=2, default=str))
    _w("detm/usage-events.csv", detm_rows["usage_csv"])
    _w("detm/screenshots-index.json", json.dumps(detm_rows["shots"], indent=2))
    _w("detm/rejected-calls.log", _extract_rejected_calls(daemon_journal))

    # 4. Live sessions referenced by tasks in window
    ls_root = config.DATA_DIR / "live_sessions"
    if ls_root.exists():
        for sess_id in detm_rows["live_session_ids"]:
            full = ls_root / sess_id
            if full.is_dir():
                for p in full.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=f"detm/live_sessions/{sess_id}/{p.relative_to(full)}")

    # 5. OpenClaw gateway journal
    gateway_journal = read_user_journal(since=since, until=until)
    _w("openclaw/gateway.journal.log", gateway_journal)

    # 6. OpenClaw config/command logs
    oc_logs_dir = OPENCLAW_ROOT / "logs"
    for name in ("commands.log", "config-audit.jsonl", "config-health.json"):
        p = oc_logs_dir / name
        if p.exists():
            try:
                _w(f"openclaw/{name}", _redact(p.read_text(encoding="utf-8", errors="replace")))
            except OSError as e:
                _w(f"openclaw/{name}.error", f"could not read: {e}\n")

    # 7. OpenClaw agent sessions overlapping the window
    session_files = _openclaw_sessions_in_window(since_dt, until_dt)
    for sp in session_files:
        try:
            content = sp.read_text(encoding="utf-8", errors="replace")
            _w(f"openclaw/sessions/{sp.parent.parent.name}/{sp.name}", _redact(content))
        except OSError as e:
            _w(f"openclaw/sessions/{sp.name}.error", f"could not read: {e}\n")

    # 8. ERRORS.log — pre-filtered from all text sources
    errors_log = _extract_errors({
        "detm-daemon.journal": daemon_journal,
        "detm-debug": read_debug_log_slice(),
        "openclaw-gateway.journal": gateway_journal,
    })
    _w("ERRORS.log", errors_log or "# no ERROR/CRITICAL/Traceback lines in window\n")

    # 9. MANIFEST.md — AI-agent reading guide
    manifest = _dump_manifest(
        since_str=since_str, until_str=until_str,
        daemon_journal_lines=len(daemon_journal.splitlines()),
        gateway_journal_lines=len(gateway_journal.splitlines()),
        tasks=len(detm_rows["tasks"]),
        actions=len(detm_rows["actions"]),
        sessions=len(session_files),
        errors=len(errors_log.splitlines()) if errors_log else 0,
        rejected=sum(1 for line in daemon_journal.splitlines() if _HTTP_REJECT_RE.search(line)),
    )
    _w("MANIFEST.md", manifest)

    zf.close()
    fname = f"detm-dump-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
    return buf.getvalue(), fname


def _parse_any_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _dump_detm_sqlite(since_dt: datetime | None, until_dt: datetime | None) -> dict:
    """Pull rows from data.db within the window. Returns a dict keyed by
    table name; all fields pre-serialized as JSON-ready values."""
    import sqlite3 as _sqlite3
    db = config.DB_PATH
    if not db.exists():
        return {"tasks": [], "actions": [], "messages": [], "waits": [],
                "usage_csv": "# no data.db\n", "shots": [], "live_session_ids": set()}

    since_iso = since_dt.isoformat() if since_dt else "1970-01-01T00:00:00+00:00"
    until_iso = until_dt.isoformat() if until_dt else "9999-12-31T23:59:59+00:00"

    con = _sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    con.row_factory = _sqlite3.Row
    try:
        tasks = [dict(r) for r in con.execute(
            "SELECT * FROM tasks WHERE updated_at BETWEEN ? AND ? ORDER BY updated_at",
            (since_iso, until_iso),
        )]
        task_ids = [t["id"] for t in tasks]

        # For structured view, hydrate each task with plan_items + actions
        for t in tasks:
            items = [dict(r) for r in con.execute(
                "SELECT * FROM plan_items WHERE task_id=? ORDER BY ordinal", (t["id"],),
            )]
            for item in items:
                item_actions = [dict(r) for r in con.execute(
                    "SELECT * FROM actions WHERE plan_item_id=? ORDER BY created_at",
                    (item["id"],),
                )]
                for a in item_actions:
                    a["logs"] = [dict(r) for r in con.execute(
                        "SELECT * FROM action_logs WHERE action_id=? ORDER BY created_at",
                        (a["id"],),
                    )]
                item["actions"] = item_actions
            t["plan_items"] = items

        actions = [dict(r) for r in con.execute(
            "SELECT * FROM actions WHERE created_at BETWEEN ? AND ? ORDER BY created_at",
            (since_iso, until_iso),
        )]

        messages = [dict(r) for r in con.execute(
            "SELECT * FROM task_messages WHERE created_at BETWEEN ? AND ? ORDER BY created_at",
            (since_iso, until_iso),
        )]

        waits = [dict(r) for r in con.execute(
            "SELECT * FROM wait_jobs WHERE created_at BETWEEN ? AND ? ORDER BY created_at",
            (since_iso, until_iso),
        )]

        # usage_events uses `ts REAL` instead of ISO string
        since_epoch = since_dt.timestamp() if since_dt else 0
        until_epoch = until_dt.timestamp() if until_dt else 9e12
        usage_rows = list(con.execute(
            "SELECT id, ts, provider, model, task_id, input_tokens, output_tokens, requests, cost_usd "
            "FROM usage_events WHERE ts BETWEEN ? AND ? ORDER BY ts",
            (since_epoch, until_epoch),
        ))
    finally:
        con.close()

    import csv, io as _io
    usage_buf = _io.StringIO()
    w = csv.writer(usage_buf)
    w.writerow(["id", "ts_iso", "provider", "model", "task_id",
                "input_tokens", "output_tokens", "requests", "cost_usd"])
    for r in usage_rows:
        ts_iso = datetime.fromtimestamp(r[1], tz=timezone.utc).isoformat()
        w.writerow([r[0], ts_iso] + list(r[2:]))

    # Screenshots index: walk actions, check disk for referenced files
    shots_dir = config.DATA_DIR / "screenshots"
    shots_index = []
    for a in actions:
        out = a.get("output_data") or ""
        # Heuristic: look for screenshot_id patterns in action id or output
        aid = a["id"]
        for suffix in ("_before.jpg", "_after.jpg"):
            fname = f"{aid}{suffix}"
            shots_index.append({
                "action_id": aid,
                "filename": fname,
                "exists": (shots_dir / fname).exists() if shots_dir.exists() else False,
                "action_summary": (a.get("summary") or "")[:80],
                "created_at": a.get("created_at"),
            })

    # Live session ids referenced anywhere in the actions dump
    live_ids: set[str] = set()
    blob = json.dumps(actions, default=str)
    for m in re.finditer(r"live_sessions/(\w+)", blob):
        live_ids.add(m.group(1))
    for m in _SESSION_ID_RE.finditer(blob):
        live_ids.add(m.group(1))

    return {
        "tasks": tasks,
        "actions": actions,
        "messages": messages,
        "waits": waits,
        "usage_csv": usage_buf.getvalue(),
        "shots": shots_index,
        "live_session_ids": live_ids,
    }


def _dump_manifest(*, since_str: str | None, until_str: str | None,
                   daemon_journal_lines: int, gateway_journal_lines: int,
                   tasks: int, actions: int, sessions: int,
                   errors: int, rejected: int) -> str:
    return f"""# DETM Logs Dump — AI Agent Reading Guide

Packed at: {datetime.now(timezone.utc).isoformat()}
Window:    {since_str or "(start of time)"} → {until_str or "(now)"}

## Summary of what's in this zip

  {daemon_journal_lines:>6}  lines in `detm/daemon.journal.log`
  {gateway_journal_lines:>6}  lines in `openclaw/gateway.journal.log`
  {tasks:>6}  DETM tasks (in `detm/tasks.json`)
  {actions:>6}  DETM actions (in `detm/actions-flat.json`)
  {sessions:>6}  OpenClaw agent sessions (in `openclaw/sessions/*/`)
  {errors:>6}  pre-filtered error lines (in `ERRORS.log`)
  {rejected:>6}  HTTP 4xx/5xx rejected calls (in `detm/rejected-calls.log`)

## How to read this, if you are an AI agent

**Start with `ERRORS.log`.** It pre-filters every `ERROR`/`CRITICAL`/
`Traceback` line from all text sources with a `[source]` tag. If the
user gave you this dump to diagnose a problem, scan here first.

**Then cross-reference by timestamp.** Every file uses UTC ISO-8601 in
its timestamps. To trace a single user action:
  1. Find the timestamp of interest (often from `ERRORS.log` or a user
     complaint like "the gui_agent failed around 3pm").
  2. Open `openclaw/sessions/<agent>/<uuid>.jsonl` and scan for that
     time — each line has a `timestamp` field. This shows you what
     the LLM was trying to do: model calls, tool calls, thinking.
  3. Open `detm/daemon.journal.log` at the same timestamp — this is
     what the daemon received (HTTP request log) and what its modules
     logged while handling it.
  4. If the action was tied to a DETM task, find the task_id in
     `detm/tasks.json` (keyed by id) for the full hierarchy.

## The "agent forgot task_register" fingerprint

OpenClaw's SKILL.md tells agents to always call `task_register` before
any desktop/GUI tool. When an agent skips this step, the daemon
**rejects** subsequent tool calls (because `actions.task_id` is NOT
NULL in the schema). Those rejections show up as HTTP 4xx access lines.

  → See `detm/rejected-calls.log` — extracted 4xx/5xx lines from the
    daemon journal. Each line has a timestamp; cross-reference it
    against `openclaw/sessions/*.jsonl` to see what the agent was
    trying to do at that moment.

If `rejected-calls.log` is non-empty, the agent failed to follow
SKILL.md — root cause is prompt/skill engineering, not DETM code.

## Files

- `MANIFEST.md` — this file.
- `ERRORS.log` — pre-filtered error lines across all sources.
- `detm/daemon.journal.log` — `journalctl -u detm-daemon` slice.
   Contains HTTP access logs, outbound httpx calls, internal module
   logs (live_ui, gui_agent, wait engine, etc).
- `detm/debug.log` — `~/.agentic-computer-use/logs/debug.log` (whole
   file; only populated when ACU_DEBUG=1).
- `detm/tasks.json` — DETM tasks in window, each with plan_items,
   actions, and action logs.
- `detm/actions-flat.json` — flat list of every action in window,
   for timestamp-based cross-reference.
- `detm/task-messages.json` — `task_update(message=...)` narration.
- `detm/wait-jobs.json` — smart_wait jobs active in window.
- `detm/usage-events.csv` — API costs + token counts per call.
- `detm/screenshots-index.json` — per-action screenshot filenames,
   with a boolean for whether the file is still on disk (older ones
   get pruned by storage-cleanup).
- `detm/rejected-calls.log` — 4xx/5xx HTTP access lines (the "agent
   forgot to register task" signal, see above).
- `detm/live_sessions/<id>/` — events.jsonl + frames + audio for any
   live_ui session referenced by a task in this window.
- `openclaw/gateway.journal.log` — `journalctl --user -u openclaw-
   gateway` slice.
- `openclaw/commands.log` — commands the OpenClaw gateway has
   invoked.
- `openclaw/config-audit.jsonl` — config change audit trail.
- `openclaw/config-health.json` — last config health snapshot.
- `openclaw/sessions/<agent>/<uuid>.jsonl` — OpenClaw agent
   conversation log. Full model calls, tool calls, tool responses,
   thinking events, user messages. This is the single richest source
   of "what was the model thinking".

## Privacy note

This dump may contain API keys, access tokens, passwords, and private
conversation content. Obvious secret patterns (OpenRouter, Anthropic,
GitHub PAT, Google API key) are redacted as `***REDACTED***` but
redaction is best-effort, not exhaustive. Share only with trusted
parties.
"""
