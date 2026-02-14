"""Persistent daemon for the wait engine + task manager.

Runs as a background HTTP server so the MCP server (spawned per-call by mcporter)
can submit jobs and query state without losing the async event loop.
"""
import asyncio
import json
import logging
import os
from aiohttp import web

from . import config, db, debug
from .wait.engine import WaitEngine, WaitJob
from .wait.poller import AdaptivePoller
from .task import manager as task_mgr
from .vision import check_ollama_health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 18790

wait_engine = WaitEngine()


async def _parse_body(request: web.Request) -> dict:
    """Parse JSON body, normalizing keys that have trailing colons (mcporter :=  syntax artifact)."""
    if not request.can_read_body:
        return {}
    raw = await request.json()
    # mcporter sends key:=value as {"key:": value} — strip trailing colons
    return {k.rstrip(":"): v for k, v in raw.items()} if isinstance(raw, dict) else raw


# ─── HTTP handlers ──────────────────────────────────────────────

async def handle_smart_wait(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    target_str = args["target"]
    if ":" in target_str:
        target_type, target_id = target_str.split(":", 1)
    else:
        target_type, target_id = target_str, "full"

    if target_type not in ("window", "pty", "screen"):
        return web.json_response({"error": f"Invalid target type: {target_type}"}, status=400)

    job_id = db.new_id()
    job = WaitJob(
        id=job_id,
        target_type=target_type,
        target_id=target_id,
        criteria=args["wake_when"],
        timeout=args.get("timeout", config.DEFAULT_TIMEOUT),
        task_id=args.get("task_id"),
        poll_interval=args.get("poll_interval", config.DEFAULT_POLL_INTERVAL),
        match_patterns=args.get("match_patterns", []),
    )

    # Persist to DB
    conn = await db.get_db()
    try:
        await conn.execute(
            "INSERT INTO wait_jobs (id, task_id, target_type, target_id, criteria, timeout_seconds, poll_interval, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job.id, job.task_id, job.target_type, job.target_id, job.criteria, job.timeout, job.poll_interval, "watching", db.now_iso())
        )
        await conn.commit()
    finally:
        await conn.close()

    wait_engine.add_job(job)

    return web.json_response({
        "wait_id": job_id,
        "status": "watching",
        "target": f"{target_type}:{target_id}",
        "message": f"Monitoring. I'll wake you when: {job.criteria}. Timeout: {job.timeout}s.",
    })


async def handle_wait_status(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    wait_id = args.get("wait_id")
    import time
    if wait_id:
        if wait_id in wait_engine.jobs:
            job = wait_engine.jobs[wait_id]
            elapsed = time.time() - job.context.started_at
            return web.json_response({
                "wait_id": job.id, "status": job.status,
                "target": f"{job.target_type}:{job.target_id}",
                "criteria": job.criteria,
                "elapsed_seconds": round(elapsed),
                "poll_interval": job.poller.interval,
                "frames_captured": len(job.context.frames),
                "verdicts": len(job.context.verdicts),
            })
        return web.json_response({"error": f"Wait job {wait_id} not found"}, status=404)

    jobs = []
    for job in wait_engine.jobs.values():
        elapsed = time.time() - job.context.started_at
        jobs.append({
            "wait_id": job.id, "status": job.status,
            "target": f"{job.target_type}:{job.target_id}",
            "criteria": job.criteria, "elapsed_seconds": round(elapsed),
        })
    return web.json_response({"active_jobs": jobs, "count": len(jobs)})


async def handle_wait_update(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    wait_id = args["wait_id"]
    if wait_id not in wait_engine.jobs:
        return web.json_response({"error": f"Wait job {wait_id} not found"}, status=404)

    job = wait_engine.jobs[wait_id]
    import time
    if args.get("wake_when"):
        job.criteria = args["wake_when"]
    if args.get("timeout"):
        job.timeout = args["timeout"]
        job.context.started_at = time.time()
    if args.get("message"):
        log.info(f"Wait update note for {wait_id}: {args['message']}")
    job.diff_gate.reset()
    job.poller = AdaptivePoller(job.poll_interval)
    job.next_check_at = 0

    return web.json_response({
        "wait_id": wait_id, "status": "watching",
        "message": f"Resumed. Watching for: {job.criteria}. Timeout: {job.timeout}s.",
    })


async def handle_wait_cancel(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    success = wait_engine.cancel_job(args["wait_id"], args.get("reason", "cancelled by agent"))
    if success:
        return web.json_response({"wait_id": args["wait_id"], "status": "cancelled"})
    return web.json_response({"error": f"Wait job not found"}, status=404)


async def handle_task_register(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    result = await task_mgr.register_task(name=args["name"], plan=args["plan"], metadata=args.get("metadata"))
    return web.json_response(result)


async def handle_task_update(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    result = await task_mgr.update_task(
        task_id=args["task_id"], message=args.get("message"),
        query=args.get("query"), status=args.get("status"),
        step_done=args.get("step_done"),
    )
    return web.json_response(result)


async def handle_task_thread(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    result = await task_mgr.get_thread(args["task_id"], limit=args.get("limit", 50))
    return web.json_response(result)


async def handle_task_post(request: web.Request) -> web.Response:
    """Direct message post to a task thread."""
    args = await _parse_body(request)
    result = await task_mgr.post_message(
        task_id=args["task_id"],
        role=args.get("role", "agent"),
        content=args["content"],
        msg_type=args.get("msg_type", "text"),
    )
    return web.json_response(result)


async def handle_task_list(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    result = await task_mgr.list_tasks(status=args.get("status", "active"), limit=args.get("limit", 10))
    return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    ollama = await check_ollama_health()
    return web.json_response({
        "server": "ok", "daemon": True,
        "ollama": ollama,
        "active_wait_jobs": len(wait_engine.jobs),
        "data_dir": str(config.DATA_DIR),
        "display": config.DISPLAY,
    })


async def handle_desktop_look(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    from .capture.screen import capture_screen, frame_to_jpeg
    if args.get("window_name"):
        from .desktop import control as desktop
        wid = desktop.find_window(args["window_name"])
        if wid:
            desktop.focus_window(wid)
            await asyncio.sleep(0.3)
    frame = capture_screen()
    if frame is None:
        return web.json_response({"error": "Failed to capture screen"}, status=500)
    jpeg = frame_to_jpeg(frame)
    from .vision import evaluate_condition
    prompt = args.get("prompt", "Describe everything visible on the screen.")
    response = await evaluate_condition(prompt, [jpeg])
    return web.json_response({
        "description": response,
        "screen_size": {"width": frame.shape[1], "height": frame.shape[0]},
    })


async def handle_desktop_action(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    from .desktop import control as desktop
    action = args["action"]
    result = {}

    if action == "click":
        x, y = args.get("x"), args.get("y")
        btn = args.get("button", 1)
        if x is not None and y is not None:
            ok = desktop.mouse_click_at(x, y, btn)
            result = {"ok": ok, "action": "click", "x": x, "y": y}
        else:
            ok = desktop.mouse_click(btn)
            result = {"ok": ok, "action": "click"}
    elif action == "double_click":
        ok = desktop.mouse_double_click(args.get("x"), args.get("y"))
        result = {"ok": ok, "action": "double_click"}
    elif action == "type":
        ok = desktop.type_text(args.get("text", ""))
        result = {"ok": ok, "action": "type"}
    elif action == "press_key":
        ok = desktop.press_key(args.get("text", "Return"))
        result = {"ok": ok, "action": "press_key"}
    elif action == "mouse_move":
        ok = desktop.mouse_move(args["x"], args["y"])
        result = {"ok": ok, "action": "mouse_move"}
    elif action == "drag":
        ok = desktop.mouse_drag(args["x"], args["y"], args["x2"], args["y2"], args.get("button", 1))
        result = {"ok": ok, "action": "drag"}
    elif action == "screenshot":
        path = desktop.take_screenshot()
        result = {"ok": path is not None, "path": path}
    elif action == "list_windows":
        windows = desktop.list_windows()
        result = {"windows": windows, "count": len(windows)}
    elif action == "find_window":
        wid = desktop.find_window(args.get("window_name", ""))
        if wid:
            info = desktop.get_window_info(wid)
            result = {"found": True, "window": info}
        else:
            result = {"found": False}
    elif action == "focus_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        ok = desktop.focus_window(wid) if wid else False
        result = {"ok": ok}
    elif action == "resize_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        ok = desktop.resize_window(wid, args["width"], args["height"]) if wid else False
        result = {"ok": ok}
    elif action == "move_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        ok = desktop.move_window(wid, args["x"], args["y"]) if wid else False
        result = {"ok": ok}
    elif action == "close_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        ok = desktop.close_window(wid) if wid else False
        result = {"ok": ok}
    elif action == "get_mouse_position":
        pos = desktop.get_mouse_position()
        result = {"x": pos[0], "y": pos[1]} if pos else {"error": "Failed"}
    else:
        result = {"error": f"Unknown action: {action}"}

    return web.json_response(result)


async def handle_video_record(request: web.Request) -> web.Response:
    args = await _parse_body(request)
    from .video.recorder import record_screen
    from .desktop.control import find_window
    import os

    target = args.get("target", "screen")
    duration = min(max(args.get("duration", 10), 1), 120)
    fps = args.get("fps", 5)
    window_id = None

    if target.startswith("window:"):
        window_id = find_window(target[7:])
        if not window_id:
            return web.json_response({"error": f"Window not found"}, status=404)

    path = record_screen(duration=duration, fps=fps, window_id=window_id)
    if path:
        size_mb = os.path.getsize(path) / 1024 / 1024
        return web.json_response({"ok": True, "path": path, "duration": duration, "fps": fps, "size_mb": round(size_mb, 2)})
    return web.json_response({"error": "Recording failed"}, status=500)


# ─── App setup ──────────────────────────────────────────────────

@web.middleware
async def debug_middleware(request: web.Request, handler):
    """Log all HTTP requests when debug is enabled."""
    import time as _time
    start = _time.time()
    try:
        response = await handler(request)
        elapsed = (_time.time() - start) * 1000
        debug.log_http(request.method, request.path, response.status, elapsed)
        return response
    except Exception as e:
        elapsed = (_time.time() - start) * 1000
        debug.log_http(request.method, request.path, 500, elapsed)
        raise


def create_app() -> web.Application:
    app = web.Application(middlewares=[debug_middleware])
    app.router.add_post("/smart_wait", handle_smart_wait)
    app.router.add_post("/wait_status", handle_wait_status)
    app.router.add_post("/wait_update", handle_wait_update)
    app.router.add_post("/wait_cancel", handle_wait_cancel)
    app.router.add_post("/task_register", handle_task_register)
    app.router.add_post("/task_update", handle_task_update)
    app.router.add_post("/task_thread", handle_task_thread)
    app.router.add_post("/task_post", handle_task_post)
    app.router.add_post("/task_list", handle_task_list)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/desktop_look", handle_desktop_look)
    app.router.add_post("/desktop_action", handle_desktop_action)
    app.router.add_post("/video_record", handle_video_record)
    return app


async def stuck_detection_loop():
    """Background loop that checks for stuck tasks and wakes OpenClaw."""
    import subprocess
    debug.log("DAEMON", "Stuck detection loop started (checking every 60s)")
    while True:
        await asyncio.sleep(task_mgr.STUCK_CHECK_INTERVAL)
        try:
            alerts = await task_mgr.check_stuck_tasks()
            for alert in alerts:
                msg = (
                    f"[task_stuck] Task {alert['task_id']} ({alert['name']}) appears stuck: "
                    f"{alert['reason']}. Last activity: {alert['last_message']}"
                )
                debug.log_openclaw_event("stuck_alert", msg)
                try:
                    subprocess.run(
                        ["/home/alex/.npm-global/bin/openclaw", "system", "event",
                         "--text", msg, "--mode", "now"],
                        capture_output=True, text=True, timeout=10
                    )
                except Exception as e:
                    debug.log("ERROR", f"Failed to inject stuck alert: {e}")
        except Exception as e:
            debug.log("ERROR", f"Stuck detection error: {e}")


def main():
    import sys as _sys
    enable_debug = "--debug" in _sys.argv or os.environ.get("MEMORIESAI_DEBUG", "0") in ("1", "true", "yes")
    config.ensure_data_dir()
    debug.init(enabled=enable_debug)
    debug.log("DAEMON", f"Starting memoriesai daemon on {DAEMON_HOST}:{DAEMON_PORT}")
    debug.log("DAEMON", f"Display: {config.DISPLAY}, Model: {config.VISION_MODEL}")
    debug.log("DAEMON", f"Data dir: {config.DATA_DIR}")
    log.info(f"Starting memoriesai daemon on {DAEMON_HOST}:{DAEMON_PORT}")
    app = create_app()

    # Start stuck detection as a background task
    async def on_startup(app):
        app['stuck_detector'] = asyncio.create_task(stuck_detection_loop())

    async def on_cleanup(app):
        app['stuck_detector'].cancel()
        try:
            await app['stuck_detector']
        except asyncio.CancelledError:
            pass

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    web.run_app(app, host=DAEMON_HOST, port=DAEMON_PORT, print=lambda msg: log.info(msg))


if __name__ == "__main__":
    main()
