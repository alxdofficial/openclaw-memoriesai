#!/usr/bin/env python3
"""Test-only portal for DETM local harness scenarios."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = pathlib.Path(os.environ.get("ACU_DATA_DIR", pathlib.Path.home() / ".agentic-computer-use"))
RUNS_DIR = pathlib.Path(os.environ.get("ACU_TEST_RUNS_DIR", "/tmp/acu-test-runs"))
DAEMON_BASE = os.environ.get("ACU_DAEMON_BASE", "http://127.0.0.1:18790")
PORTAL_HTML = REPO_ROOT / "scripts" / "fixtures" / "acu_test_portal.html"


def _load_run(run_id: str) -> dict[str, Any]:
    path = RUNS_DIR / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"run {run_id} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _list_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    if not RUNS_DIR.exists():
        return runs
    for path in sorted(RUNS_DIR.glob("run-*/run.json"), reverse=True):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    runs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return runs


def _load_events(run_id: str) -> list[dict[str, Any]]:
    path = RUNS_DIR / run_id / "events.jsonl"
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events[-300:]


def _list_artifacts(run_id: str) -> list[dict[str, Any]]:
    artifacts_dir = RUNS_DIR / run_id / "artifacts"
    if not artifacts_dir.exists():
        return []
    items = []
    for path in sorted(artifacts_dir.iterdir()):
        if path.is_file():
            items.append({"name": path.name, "size": path.stat().st_size})
    return items


def _daemon_get(path: str) -> tuple[int, bytes, str]:
    url = f"{DAEMON_BASE}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.status, resp.read(), content_type
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "text/plain")
    except urllib.error.URLError as exc:
        return 502, str(exc).encode("utf-8"), "text/plain"


def _daemon_delete(path: str) -> tuple[int, bytes, str]:
    url = f"{DAEMON_BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.status, resp.read(), content_type
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "text/plain")
    except urllib.error.URLError as exc:
        return 502, str(exc).encode("utf-8"), "text/plain"


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "AcuTestPortal/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/",) or path.startswith("/runs/"):
                self._serve_file(PORTAL_HTML, "text/html; charset=utf-8")
                return
            if path == "/api/health":
                self._send_json({"ok": True})
                return
            if path == "/api/runs":
                self._send_json({"runs": _list_runs()})
                return
            if path.startswith("/api/runs/"):
                self._handle_run_api_get(path, query)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
        except FileNotFoundError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/runs/") and path.endswith("/cleanup"):
                parts = path.strip("/").split("/")
                run_id = parts[2]
                self._send_json(_cleanup_run(run_id))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
        except FileNotFoundError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _handle_run_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        run_id = parts[2]

        if len(parts) == 3:
            run = _load_run(run_id)
            self._send_json({
                "run": run,
                "events": _load_events(run_id),
                "artifacts": _list_artifacts(run_id),
            })
            return

        tail = parts[3]
        if tail == "snapshot":
            run = _load_run(run_id)
            task_id = run.get("task_id")
            if task_id:
                status, body, content_type = _daemon_get(f"/api/tasks/{task_id}/snapshot")
            else:
                status, body, content_type = _daemon_get("/api/snapshot")
            self._send_bytes(body, content_type, status)
            return

        if tail == "task_summary":
            run = _load_run(run_id)
            task_id = run.get("task_id")
            if not task_id:
                self._send_json({"error": "run has no task_id"}, status=404)
                return
            detail = (query.get("detail") or ["full"])[0]
            status, body, _ = _daemon_get(f"/api/tasks/{task_id}?detail={detail}")
            self._send_bytes(body, "application/json", status)
            return

        if tail == "messages":
            run = _load_run(run_id)
            task_id = run.get("task_id")
            if not task_id:
                self._send_json({"error": "run has no task_id"}, status=404)
                return
            limit = int((query.get("limit") or ["100"])[0])
            status, body, _ = _daemon_get(f"/api/tasks/{task_id}/messages?limit={limit}")
            self._send_bytes(body, "application/json", status)
            return

        if tail == "debug_tail":
            self._send_json(_debug_tail(run_id, int((query.get("lines") or ["200"])[0])))
            return

        if tail == "artifacts" and len(parts) >= 5:
            filename = parts[4]
            if "/" in filename or ".." in filename or "\\" in filename:
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid filename")
                return
            artifact = RUNS_DIR / run_id / "artifacts" / filename
            if not artifact.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
                return
            self._serve_file(artifact, _guess_content_type(artifact.name))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: pathlib.Path, content_type: str) -> None:
        body = path.read_bytes()
        self._send_bytes(body, content_type, 200)


def _debug_tail(run_id: str, lines: int) -> dict[str, Any]:
    run = _load_run(run_id)
    debug_log = DATA_DIR / "logs" / "debug.log"
    text = ""
    if debug_log.exists():
        all_lines = debug_log.read_text(encoding="utf-8", errors="replace").splitlines()
        tokens = [str(run.get("task_id") or "")]
        extra = run.get("extra") or {}
        for key in ("wait_id", "session_id"):
            if extra.get(key):
                tokens.append(str(extra[key]))
        tokens = [t for t in tokens if t]
        if tokens:
            filtered = [line for line in all_lines if any(token in line for token in tokens)]
            text = "\n".join(filtered[-lines:])
        else:
            text = "\n".join(all_lines[-lines:])
    return {"text": text}


def _cleanup_run(run_id: str) -> dict[str, Any]:
    run = _load_run(run_id)
    result: dict[str, Any] = {"run_id": run_id}

    pgid = run.get("browser_pgid")
    if pgid:
        try:
            os.killpg(int(pgid), signal.SIGTERM)
            result["browser"] = f"terminated pgid {pgid}"
        except Exception as exc:
            result["browser"] = f"failed: {exc}"

    task_id = run.get("task_id")
    if task_id:
        status, body, _ = _daemon_delete(f"/api/tasks/{task_id}")
        try:
            result["task_delete"] = json.loads(body.decode("utf-8"))
        except Exception:
            result["task_delete"] = {"status": status, "body": body.decode("utf-8", errors="replace")}

    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
        result["run_dir_deleted"] = True
    else:
        result["run_dir_deleted"] = False
    return result


def _guess_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".html"):
        return "text/html; charset=utf-8"
    if lower.endswith(".json"):
        return "application/json; charset=utf-8"
    if lower.endswith(".txt") or lower.endswith(".log"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    return "application/octet-stream"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18891)
    args = parser.parse_args()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PortalHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
