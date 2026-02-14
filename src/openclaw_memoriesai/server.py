"""MCP server entry point for openclaw-memoriesai."""
import asyncio
import json
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from . import config, db
from .wait.engine import WaitEngine, WaitJob
from .task import manager as task_mgr
from .vision import check_ollama_health

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
