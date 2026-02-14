"""MCP server entry point — thin proxy to the persistent daemon."""
import asyncio
import json
import logging
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DAEMON_URL = "http://127.0.0.1:18790"

app = Server("openclaw-memoriesai")

# ─── Tool definitions ───────────────────────────────────────────

TOOLS = [
    Tool(
        name="smart_wait",
        description="Delegate waiting to a local vision model. Monitors a screen/window/terminal and wakes you when a condition is met. Your current run can end — you'll get a system event when the condition is satisfied or times out.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "What to watch. Format: window:<name_or_id>, pty:<session_id>, or screen"},
                "wake_when": {"type": "string", "description": "Natural language condition to watch for (e.g., 'download completes or error dialog appears')"},
                "timeout": {"type": "integer", "description": "Seconds before giving up. Default: 300", "default": 300},
                "task_id": {"type": "string", "description": "Link this wait to a task (auto-posts update on resolution)"},
                "poll_interval": {"type": "number", "description": "Base polling interval in seconds. Default: 2.0", "default": 2.0},
                "match_patterns": {"type": "array", "items": {"type": "string"}, "description": "Regex patterns to fast-match against terminal output (<1ms). You know what command you launched — pass patterns for its success/failure signatures. E.g., ['added \\\\d+ packages', 'npm error'] for npm install. Only works with pty targets."},
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
                "plan": {"type": "array", "items": {"type": "string"}, "description": "Ordered checklist of steps"},
                "metadata": {"type": "object", "description": "Arbitrary key-value pairs"},
            },
            "required": ["name", "plan"],
        },
    ),
    Tool(
        name="task_update",
        description="Report progress on a task: post a message, mark a step done, change status, or query state. Use this throughout your work to narrate what you're doing.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task to update/query"},
                "message": {"type": "string", "description": "Progress update (added to task thread as agent message)"},
                "step_done": {"type": "integer", "description": "Mark a plan step as complete (0-indexed)"},
                "query": {"type": "string", "description": "Ask a question about the task (AI-answered from thread history)"},
                "status": {"type": "string", "enum": ["active", "paused", "completed", "failed"], "description": "Set task status"},
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_thread",
        description="Get the full chat thread for a task — all messages, progress, plan status. Use after context compaction to recall where you are.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task to read"},
                "limit": {"type": "integer", "description": "Max messages to return", "default": 50},
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_list",
        description="List active (or all) tasks with progress summaries.",
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
        description="Control the desktop: click, type, press keys, manage windows.",
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
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "text": {"type": "string"},
                "button": {"type": "integer", "default": 1},
                "window_id": {"type": "integer"},
                "window_name": {"type": "string"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="video_record",
        description="Record a short video clip of the screen or a window.",
        inputSchema={
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "default": 10},
                "target": {"type": "string", "default": "screen"},
                "fps": {"type": "integer", "default": 5},
            },
        },
    ),
    Tool(
        name="desktop_look",
        description="Take a screenshot and describe what's on the desktop using vision.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "default": "Describe everything visible on the screen."},
                "window_name": {"type": "string"},
            },
        },
    ),
]


# ─── Route map ──────────────────────────────────────────────────

ROUTE_MAP = {
    "smart_wait": ("POST", "/smart_wait"),
    "wait_status": ("POST", "/wait_status"),
    "wait_update": ("POST", "/wait_update"),
    "wait_cancel": ("POST", "/wait_cancel"),
    "task_register": ("POST", "/task_register"),
    "task_update": ("POST", "/task_update"),
    "task_thread": ("POST", "/task_thread"),
    "task_list": ("POST", "/task_list"),
    "health_check": ("GET", "/health"),
    "desktop_action": ("POST", "/desktop_action"),
    "video_record": ("POST", "/video_record"),
    "desktop_look": ("POST", "/desktop_look"),
}


# ─── Proxy handler ──────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name not in ROUTE_MAP:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    method, path = ROUTE_MAP[name]
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if method == "GET":
                resp = await client.get(f"{DAEMON_URL}{path}")
            else:
                resp = await client.post(f"{DAEMON_URL}{path}", json=arguments)

            data = resp.json()
            return [TextContent(type="text", text=json.dumps(data))]
    except httpx.ConnectError:
        return [TextContent(type="text", text=json.dumps({
            "error": "memoriesai daemon is not running. Start it with: memoriesai-daemon",
            "hint": "Run: cd /home/alex/openclaw-memoriesai && .venv/bin/python3 -m openclaw_memoriesai.daemon"
        }))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


def main():
    log.info("Starting openclaw-memoriesai MCP server (proxy mode)")
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
