"""MCP server entry point for openclaw-memoriesai."""
import asyncio
import json
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from . import config, db
import time
from .wait.engine import WaitEngine, WaitJob
from .wait.poller import AdaptivePoller
from .task import manager as task_mgr
from .vision import check_ollama_health, evaluate_condition

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Server("openclaw-memoriesai")
wait_engine = WaitEngine()


# ─── Tool definitions ───────────────────────────────────────────

TOOLS = [
    Tool(
        name="smart_wait",
        description="Delegate waiting to a local vision model. Monitors a screen/window/terminal and wakes you when a condition is met. Your current run can end — you'll get a system event when the condition is satisfied or times out.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "What to watch. Format: window:<name_or_id>, pty:<session_id>, or screen"
                },
                "wake_when": {
                    "type": "string",
                    "description": "Natural language condition to watch for (e.g., 'download completes or error dialog appears')"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before giving up. Default: 300",
                    "default": 300
                },
                "task_id": {
                    "type": "string",
                    "description": "Link this wait to a task (auto-posts update on resolution)"
                },
                "poll_interval": {
                    "type": "number",
                    "description": "Base polling interval in seconds. Default: 2.0",
                    "default": 2.0
                },
            },
            "required": ["target", "wake_when"],
        },
    ),
    Tool(
        name="wait_status",
        description="Check the status of active wait jobs.",
        inputSchema={
            "type": "object",
            "properties": {
                "wait_id": {"type": "string", "description": "Specific wait job to check (optional — omit for all)"},
            },
        },
    ),
    Tool(
        name="wait_update",
        description="Resume or refine an existing wait job. Use after being woken too early — update the condition or extend the timeout.",
        inputSchema={
            "type": "object",
            "properties": {
                "wait_id": {"type": "string", "description": "The wait job to update"},
                "wake_when": {"type": "string", "description": "Updated condition (replaces original)"},
                "timeout": {"type": "integer", "description": "New timeout in seconds (resets clock)"},
                "message": {"type": "string", "description": "Note to attach (logged in job history)"},
            },
            "required": ["wait_id"],
        },
    ),
    Tool(
        name="wait_cancel",
        description="Cancel an active wait job.",
        inputSchema={
            "type": "object",
            "properties": {
                "wait_id": {"type": "string", "description": "The wait job to cancel"},
                "reason": {"type": "string", "description": "Why it's being cancelled"},
            },
            "required": ["wait_id"],
        },
    ),
    Tool(
        name="task_register",
        description="Register a new long-running task with a plan. Use this to track multi-step work across context boundaries.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable task name"},
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered checklist of steps",
                },
                "metadata": {"type": "object", "description": "Arbitrary key-value pairs"},
            },
            "required": ["name", "plan"],
        },
    ),
    Tool(
        name="task_update",
        description="Report progress on a task or query its current state. Use after context compaction to recall where you are.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task to update/query"},
                "message": {"type": "string", "description": "Progress update"},
                "query": {"type": "string", "description": "Question about the task"},
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "failed"],
                    "description": "Set task status",
                },
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_list",
        description="List active (or all) tasks.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "active", "description": "Filter: active, completed, failed, paused, all"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    ),
    Tool(
        name="health_check",
        description="Check if the memoriesai daemon, Ollama, and vision model are healthy.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="desktop_action",
        description="Control the desktop: click, type, press keys, manage windows. For automating native GUI applications.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["click", "double_click", "type", "press_key", "mouse_move",
                             "drag", "screenshot", "list_windows", "find_window",
                             "focus_window", "resize_window", "move_window", "close_window",
                             "get_mouse_position"],
                    "description": "Action to perform"
                },
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "x2": {"type": "integer", "description": "End X for drag"},
                "y2": {"type": "integer", "description": "End Y for drag"},
                "text": {"type": "string", "description": "Text to type or key to press"},
                "button": {"type": "integer", "description": "Mouse button (1=left, 2=middle, 3=right)", "default": 1},
                "window_id": {"type": "integer", "description": "X11 window ID"},
                "window_name": {"type": "string", "description": "Window name/title to search for"},
                "width": {"type": "integer", "description": "Window width for resize"},
                "height": {"type": "integer", "description": "Window height for resize"},
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="video_record",
        description="Record a short video clip of the screen or a window. Returns the file path. Use for capturing procedural knowledge or debugging visual issues.",
        inputSchema={
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "description": "Seconds to record (5-120)", "default": 10},
                "target": {"type": "string", "description": "What to record: 'screen' or 'window:<name>'", "default": "screen"},
                "fps": {"type": "integer", "description": "Frames per second", "default": 5},
            },
        },
    ),
    Tool(
        name="desktop_look",
        description="Take a screenshot and describe what's on the desktop using vision. Use to understand current GUI state before acting.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What to look for or describe (e.g., 'what buttons are visible?', 'where is the save button?')",
                    "default": "Describe everything visible on the screen."
                },
                "window_name": {
                    "type": "string",
                    "description": "Focus on a specific window (optional)"
                },
            },
        },
    ),
]


# ─── Tool handlers ──────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "smart_wait":
            return await _handle_smart_wait(arguments)
        elif name == "wait_status":
            return await _handle_wait_status(arguments)
        elif name == "wait_update":
            return await _handle_wait_update(arguments)
        elif name == "wait_cancel":
            return await _handle_wait_cancel(arguments)
        elif name == "task_register":
            return await _handle_task_register(arguments)
        elif name == "task_update":
            return await _handle_task_update(arguments)
        elif name == "task_list":
            return await _handle_task_list(arguments)
        elif name == "health_check":
            return await _handle_health_check(arguments)
        elif name == "video_record":
            return await _handle_video_record(arguments)
        elif name == "desktop_action":
            return await _handle_desktop_action(arguments)
        elif name == "desktop_look":
            return await _handle_desktop_look(arguments)
        else:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    except Exception as e:
        log.exception(f"Tool {name} error")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def _handle_smart_wait(args: dict):
    target_str = args["target"]
    if ":" in target_str:
        target_type, target_id = target_str.split(":", 1)
    else:
        target_type, target_id = target_str, "full"

    if target_type not in ("window", "pty", "screen"):
        return [TextContent(type="text", text=json.dumps({"error": f"Invalid target type: {target_type}. Use window:<name>, pty:<session>, or screen"}))]

    job_id = db.new_id()
    job = WaitJob(
        id=job_id,
        target_type=target_type,
        target_id=target_id,
        criteria=args["wake_when"],
        timeout=args.get("timeout", config.DEFAULT_TIMEOUT),
        task_id=args.get("task_id"),
        poll_interval=args.get("poll_interval", config.DEFAULT_POLL_INTERVAL),
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

    return [TextContent(type="text", text=json.dumps({
        "wait_id": job_id,
        "status": "watching",
        "target": f"{target_type}:{target_id}",
        "message": f"Monitoring. I'll wake you when: {job.criteria}. Timeout: {job.timeout}s.",
    }))]


async def _handle_wait_status(args: dict):
    wait_id = args.get("wait_id")
    if wait_id:
        if wait_id in wait_engine.jobs:
            job = wait_engine.jobs[wait_id]
            import time
            elapsed = time.time() - job.context.started_at
            return [TextContent(type="text", text=json.dumps({
                "wait_id": job.id,
                "status": job.status,
                "target": f"{job.target_type}:{job.target_id}",
                "criteria": job.criteria,
                "elapsed_seconds": round(elapsed),
                "poll_interval": job.poller.interval,
                "frames_captured": len(job.context.frames),
                "verdicts": len(job.context.verdicts),
            }))]
        return [TextContent(type="text", text=json.dumps({"error": f"Wait job {wait_id} not found (may have resolved/timed out)"}))]

    jobs = []
    import time
    for job in wait_engine.jobs.values():
        elapsed = time.time() - job.context.started_at
        jobs.append({
            "wait_id": job.id, "status": job.status,
            "target": f"{job.target_type}:{job.target_id}",
            "criteria": job.criteria, "elapsed_seconds": round(elapsed),
        })
    return [TextContent(type="text", text=json.dumps({"active_jobs": jobs, "count": len(jobs)}))]


async def _handle_wait_update(args: dict):
    wait_id = args["wait_id"]
    if wait_id not in wait_engine.jobs:
        return [TextContent(type="text", text=json.dumps({"error": f"Wait job {wait_id} not found"}))]

    job = wait_engine.jobs[wait_id]
    if args.get("wake_when"):
        job.criteria = args["wake_when"]
    if args.get("timeout"):
        job.timeout = args["timeout"]
        job.context.started_at = time.time()  # reset clock
    if args.get("message"):
        log.info(f"Wait update note for {wait_id}: {args['message']}")
    # Reset diff gate and poller for fresh evaluation
    job.diff_gate.reset()
    job.poller = AdaptivePoller(job.poll_interval)
    job.next_check_at = 0  # check immediately

    return [TextContent(type="text", text=json.dumps({
        "wait_id": wait_id,
        "status": "watching",
        "message": f"Resumed. Watching for: {job.criteria}. Timeout: {job.timeout}s.",
    }))]


async def _handle_wait_cancel(args: dict):
    success = wait_engine.cancel_job(args["wait_id"], args.get("reason", "cancelled by agent"))
    if success:
        return [TextContent(type="text", text=json.dumps({"wait_id": args["wait_id"], "status": "cancelled"}))]
    return [TextContent(type="text", text=json.dumps({"error": f"Wait job {args['wait_id']} not found"}))]


async def _handle_task_register(args: dict):
    result = await task_mgr.register_task(
        name=args["name"],
        plan=args["plan"],
        metadata=args.get("metadata"),
    )
    return [TextContent(type="text", text=json.dumps(result))]


async def _handle_task_update(args: dict):
    result = await task_mgr.update_task(
        task_id=args["task_id"],
        message=args.get("message"),
        query=args.get("query"),
        status=args.get("status"),
    )
    return [TextContent(type="text", text=json.dumps(result))]


async def _handle_task_list(args: dict):
    result = await task_mgr.list_tasks(
        status=args.get("status", "active"),
        limit=args.get("limit", 10),
    )
    return [TextContent(type="text", text=json.dumps(result))]


async def _handle_health_check(args: dict):
    ollama = await check_ollama_health()
    active_waits = len(wait_engine.jobs)
    return [TextContent(type="text", text=json.dumps({
        "server": "ok",
        "ollama": ollama,
        "active_wait_jobs": active_waits,
        "data_dir": str(config.DATA_DIR),
        "display": config.DISPLAY,
    }))]


async def _handle_video_record(args: dict):
    from .video.recorder import record_screen
    from .desktop.control import find_window

    target = args.get("target", "screen")
    duration = min(max(args.get("duration", 10), 1), 120)
    fps = args.get("fps", 5)
    window_id = None

    if target.startswith("window:"):
        name = target[7:]
        window_id = find_window(name)
        if not window_id:
            return [TextContent(type="text", text=json.dumps({"error": f"Window '{name}' not found"}))]

    path = record_screen(duration=duration, fps=fps, window_id=window_id)
    if path:
        import os
        size_mb = os.path.getsize(path) / 1024 / 1024
        return [TextContent(type="text", text=json.dumps({
            "ok": True,
            "path": path,
            "duration": duration,
            "fps": fps,
            "size_mb": round(size_mb, 2),
        }))]
    return [TextContent(type="text", text=json.dumps({"error": "Recording failed. Is ffmpeg installed?"}))]


async def _handle_desktop_action(args: dict):
    from .desktop import control as desktop
    action = args["action"]
    result = {}

    if action == "click":
        x, y = args.get("x"), args.get("y")
        btn = args.get("button", 1)
        if x is not None and y is not None:
            ok = desktop.mouse_click_at(x, y, btn)
            result = {"ok": ok, "action": "click", "x": x, "y": y, "button": btn}
        else:
            ok = desktop.mouse_click(btn)
            result = {"ok": ok, "action": "click", "button": btn}

    elif action == "double_click":
        ok = desktop.mouse_double_click(args.get("x"), args.get("y"))
        result = {"ok": ok, "action": "double_click"}

    elif action == "type":
        ok = desktop.type_text(args.get("text", ""))
        result = {"ok": ok, "action": "type", "text": args.get("text", "")[:50]}

    elif action == "press_key":
        ok = desktop.press_key(args.get("text", "Return"))
        result = {"ok": ok, "action": "press_key", "key": args.get("text")}

    elif action == "mouse_move":
        ok = desktop.mouse_move(args["x"], args["y"])
        result = {"ok": ok, "action": "mouse_move", "x": args["x"], "y": args["y"]}

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
            result = {"found": False, "window_name": args.get("window_name")}

    elif action == "focus_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        if wid:
            ok = desktop.focus_window(wid)
            result = {"ok": ok, "window_id": wid}
        else:
            result = {"ok": False, "error": "Window not found"}

    elif action == "resize_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        if wid:
            ok = desktop.resize_window(wid, args["width"], args["height"])
            result = {"ok": ok, "window_id": wid}
        else:
            result = {"ok": False, "error": "Window not found"}

    elif action == "move_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        if wid:
            ok = desktop.move_window(wid, args["x"], args["y"])
            result = {"ok": ok, "window_id": wid}
        else:
            result = {"ok": False, "error": "Window not found"}

    elif action == "close_window":
        wid = args.get("window_id") or desktop.find_window(args.get("window_name", ""))
        if wid:
            ok = desktop.close_window(wid)
            result = {"ok": ok, "window_id": wid}
        else:
            result = {"ok": False, "error": "Window not found"}

    elif action == "get_mouse_position":
        pos = desktop.get_mouse_position()
        result = {"x": pos[0], "y": pos[1]} if pos else {"error": "Failed to get position"}

    else:
        result = {"error": f"Unknown action: {action}"}

    return [TextContent(type="text", text=json.dumps(result))]


async def _handle_desktop_look(args: dict):
    """Screenshot + vision analysis of the desktop."""
    from .capture.screen import capture_screen, frame_to_jpeg

    # Focus specific window if requested
    if args.get("window_name"):
        from .desktop import control as desktop
        wid = desktop.find_window(args["window_name"])
        if wid:
            desktop.focus_window(wid)
            import asyncio
            await asyncio.sleep(0.3)  # let it render

    frame = capture_screen()
    if frame is None:
        return [TextContent(type="text", text=json.dumps({"error": "Failed to capture screen"}))]

    jpeg = frame_to_jpeg(frame)
    prompt = args.get("prompt", "Describe everything visible on the screen.")

    response = await evaluate_condition(prompt, [jpeg])
    return [TextContent(type="text", text=json.dumps({
        "description": response,
        "screen_size": {"width": frame.shape[1], "height": frame.shape[0]},
    }))]


def main():
    """Entry point for the MCP server."""
    log.info("Starting openclaw-memoriesai MCP server")
    config.ensure_data_dir()
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
