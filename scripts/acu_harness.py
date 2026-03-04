#!/usr/bin/env python3
"""Small local test harness for DETM daemon scenarios.

These helpers let us exercise DETM subsystems directly over HTTP and, when
needed, inspect the same SQLite metadata the daemon writes. The intent is to
keep scenario scripts short and deterministic.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

DAEMON_BASE = os.environ.get("ACU_DAEMON_BASE", "http://127.0.0.1:18790")
FIXTURE_HTML = REPO_ROOT / "scripts" / "fixtures" / "acu_fixture.html"
DATA_DIR = pathlib.Path(os.environ.get("ACU_DATA_DIR", pathlib.Path.home() / ".agentic-computer-use"))
DB_PATH = DATA_DIR / "data.db"
TEST_RUNS_DIR = pathlib.Path(os.environ.get("ACU_TEST_RUNS_DIR", "/tmp/acu-test-runs"))
TEST_PORTAL_PORT = int(os.environ.get("ACU_TEST_PORTAL_PORT", "18891"))
TEST_PORTAL_BASE = f"http://127.0.0.1:{TEST_PORTAL_PORT}"


class HarnessError(RuntimeError):
    pass


def daemon_get(path: str) -> dict[str, Any]:
    url = f"{DAEMON_BASE}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HarnessError(f"GET {path} failed: HTTP {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise HarnessError(f"Could not reach daemon at {DAEMON_BASE}: {exc}") from exc


def daemon_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{DAEMON_BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HarnessError(f"POST {path} failed: HTTP {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise HarnessError(f"Could not reach daemon at {DAEMON_BASE}: {exc}") from exc


def ensure_daemon() -> dict[str, Any]:
    return daemon_get("/health")


def ensure_test_runs_dir() -> pathlib.Path:
    TEST_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_RUNS_DIR


def ensure_portal(timeout_s: float = 8.0) -> str:
    ensure_test_runs_dir()
    if _portal_healthy():
        return TEST_PORTAL_BASE

    log_path = TEST_RUNS_DIR / "portal.log"
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "acu_test_portal.py"), "--port", str(TEST_PORTAL_PORT)]
    with open(log_path, "ab") as log_file:
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _portal_healthy():
            return TEST_PORTAL_BASE
        time.sleep(0.2)

    raise HarnessError(f"Test portal did not start within {timeout_s}s; see {log_path}")


def db_path() -> pathlib.Path:
    return DB_PATH


def get_task_metadata(task_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path()))
    try:
        row = conn.execute("SELECT metadata FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HarnessError(f"Task {task_id} not found in {db_path()}")
        return json.loads(row[0] or "{}")
    finally:
        conn.close()


def get_wait_job(wait_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM wait_jobs WHERE id = ?", (wait_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def wait_for_task_display(task_id: str, timeout_s: float = 10.0) -> str:
    deadline = time.time() + timeout_s
    last_display = None
    while time.time() < deadline:
        meta = get_task_metadata(task_id)
        last_display = meta.get("display")
        if last_display:
            return str(last_display)
        time.sleep(0.2)
    raise HarnessError(f"Timed out waiting for task {task_id} display metadata (last={last_display!r})")


def register_task(
    name: str,
    plan: list[str],
    *,
    isolated_display: bool = True,
    width: int | None = None,
    height: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    if isolated_display:
        meta["isolated_display"] = True
    if width:
        meta["display_width"] = width
    if height:
        meta["display_height"] = height
    result = daemon_post("/task_register", {"name": name, "plan": plan, "metadata": meta})
    if result.get("error"):
        raise HarnessError(result["error"])
    return result


def build_fixture_url(mode: str, **params: Any) -> str:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    path = FIXTURE_HTML.resolve().as_uri()
    return f"{path}?mode={urllib.parse.quote(mode)}&{query}" if query else f"{path}?mode={urllib.parse.quote(mode)}"


def launch_fixture_browser(display: str, url: str, *, width: int = 1280, height: int = 720) -> subprocess.Popen:
    profile_dir = tempfile.mkdtemp(prefix="acu-browser-")
    env = {**os.environ, "DISPLAY": display}
    browser = _pick_browser()

    if browser in ("google-chrome", "chromium", "chromium-browser"):
        cmd = [
            browser,
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-features=Translate,MediaRouter",
            f"--window-size={width},{height}",
            f"--app={url}",
        ]
    elif browser == "firefox":
        cmd = [
            browser,
            "--new-instance",
            "--profile",
            profile_dir,
            "--kiosk",
            url,
        ]
    else:
        raise HarnessError(f"No supported browser found on PATH (tried chrome/chromium/firefox)")

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc


def wait_for_windows(task_id: str, *, min_count: int = 1, timeout_s: float = 20.0) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_s
    last_windows: list[dict[str, Any]] = []
    while time.time() < deadline:
        result = daemon_post("/desktop_action", {"action": "list_windows", "task_id": task_id})
        last_windows = result.get("windows", [])
        if len(last_windows) >= min_count:
            return last_windows
        time.sleep(0.5)
    raise HarnessError(f"Timed out waiting for windows on task {task_id}; last={last_windows!r}")


def poll_wait(wait_id: str, *, timeout_s: float = 120.0, interval_s: float = 1.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            last = daemon_post("/wait_status", {"wait_id": wait_id})
        except HarnessError as exc:
            wait_row = get_wait_job(wait_id)
            if wait_row and wait_row.get("status") in ("resolved", "timeout", "cancelled", "error"):
                return {
                    "wait_id": wait_row["id"],
                    "status": wait_row["status"],
                    "target": f"{wait_row['target_type']}:{wait_row['target_id']}",
                    "criteria": wait_row["criteria"],
                    "detail": wait_row.get("result_message"),
                    "resolved_at": wait_row.get("resolved_at"),
                }
            last = {"status": "watching", "detail": str(exc)}
        if last and last.get("status") in ("resolved", "timeout", "cancelled", "error"):
            return last
        time.sleep(interval_s)
    raise HarnessError(f"Timed out polling wait {wait_id}; last={last!r}")


def complete_task(task_id: str, status: str = "completed") -> dict[str, Any]:
    return daemon_post("/task_update", {"task_id": task_id, "status": status})


def summarize_task(task_id: str, detail: str = "full") -> dict[str, Any]:
    return daemon_get(f"/api/tasks/{urllib.parse.quote(task_id)}?detail={urllib.parse.quote(detail)}")


def read_messages(task_id: str, limit: int = 100) -> dict[str, Any]:
    return daemon_get(f"/api/tasks/{urllib.parse.quote(task_id)}/messages?limit={int(limit)}")


def print_banner(title: str) -> None:
    print(f"\n== {title} ==")


def print_kv(**pairs: Any) -> None:
    for key, value in pairs.items():
        print(f"{key:16s} {value}")


def create_run(
    name: str,
    kind: str,
    *,
    task_id: str | None = None,
    display: str | None = None,
    fixture_url: str | None = None,
    browser_proc: subprocess.Popen | None = None,
    extra: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    ensure_portal()
    root = ensure_test_runs_dir()
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "name": name,
        "kind": kind,
        "created_at": time.time(),
        "status": "ready",
        "task_id": task_id,
        "display": display,
        "fixture_url": fixture_url,
        "notes": notes,
        "browser_pid": browser_proc.pid if browser_proc else None,
        "browser_pgid": _get_pgid(browser_proc.pid) if browser_proc else None,
        "extra": extra or {},
    }
    _write_json(run_dir / "run.json", meta)
    append_run_event(run_id, "run_created", {"task_id": task_id, "display": display})
    meta["page_url"] = f"{TEST_PORTAL_BASE}/runs/{run_id}"
    return meta


def update_run(run_id: str, **updates: Any) -> dict[str, Any]:
    meta_path = ensure_test_runs_dir() / run_id / "run.json"
    if not meta_path.exists():
        raise HarnessError(f"Run {run_id} not found")
    meta = json.loads(meta_path.read_text())
    for key, value in updates.items():
        if key == "extra" and isinstance(value, dict):
            merged = dict(meta.get("extra") or {})
            merged.update(value)
            meta["extra"] = merged
        else:
            meta[key] = value
    _write_json(meta_path, meta)
    return meta


def append_run_event(run_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
    run_dir = ensure_test_runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "events.jsonl"
    record = {"ts": time.time(), "event": event, "payload": payload or {}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def write_run_artifact(run_id: str, name: str, content: str, *, kind: str = "text") -> pathlib.Path:
    run_dir = ensure_test_runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_artifact_name(name)
    path = artifacts_dir / filename
    path.write_text(content, encoding="utf-8")
    append_run_event(run_id, "artifact_written", {"name": filename, "kind": kind})
    return path


def write_run_binary_artifact(run_id: str, name: str, content: bytes, *, kind: str = "binary") -> pathlib.Path:
    run_dir = ensure_test_runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_artifact_name(name)
    path = artifacts_dir / filename
    path.write_bytes(content)
    append_run_event(run_id, "artifact_written", {"name": filename, "kind": kind})
    return path


def capture_task_snapshot_artifact(run_id: str, task_id: str, name: str = "snapshot.jpg") -> pathlib.Path:
    status, body, _content_type = _daemon_get_raw(f"/api/tasks/{urllib.parse.quote(task_id)}/snapshot")
    if status != 200:
        raise HarnessError(f"Snapshot for task {task_id} failed with HTTP {status}")
    return write_run_binary_artifact(run_id, name, body, kind="snapshot")


def _pick_browser() -> str:
    import shutil

    for candidate in ("google-chrome", "chromium", "chromium-browser", "firefox"):
        if shutil.which(candidate):
            return candidate
    raise HarnessError("No supported browser found on PATH")


def _portal_healthy() -> bool:
    try:
        req = urllib.request.Request(f"{TEST_PORTAL_BASE}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:
        return False


def _get_pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except Exception:
        return None


def _safe_artifact_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in name)
    return cleaned or "artifact.txt"


def _write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _daemon_get_raw(path: str) -> tuple[int, bytes, str]:
    url = f"{DAEMON_BASE}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "text/plain")
    except urllib.error.URLError as exc:
        raise HarnessError(f"Could not reach daemon at {DAEMON_BASE}: {exc}") from exc
