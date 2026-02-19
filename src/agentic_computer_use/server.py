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

app = Server("agentic-computer-use")

# ─── Tool definitions ───────────────────────────────────────────

TOOLS = [
    # ── Smart Wait ─────────────────────────────────────────────
    Tool(
        name="smart_wait",
        description="Delegate waiting to a local vision model. Monitors one visual target and wakes you when a condition is met. Prefer a specific window target for precise waits (e.g., window:<id_or_name>). Your current run can end — you'll get a system event when the condition is satisfied or times out.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "What to watch. Prefer window:<name_or_id> so the model sees only one app (discover with desktop_action list_windows/find_window). Use screen to watch the entire desktop."},
                "wake_when": {"type": "string", "description": "Natural language condition to watch for (e.g., 'download completes or error dialog appears')"},
                "timeout": {"type": "integer", "description": "Seconds before giving up. Default: 300", "default": 300},
                "task_id": {"type": "string", "description": "Link this wait to a task (auto-posts update on resolution)"},
                "poll_interval": {"type": "number", "description": "Base polling interval in seconds. Default: 2.0", "default": 2.0},
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

    # ── Task Management (Hierarchical) ─────────────────────────
    Tool(
        name="task_register",
        description="Register a new task with a plan. Creates plan items from the checklist. Returns task_id and plan item details.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable task name"},
                "plan": {"type": "array", "items": {"type": "string"}, "description": "Ordered checklist of plan items"},
                "metadata": {"type": "object", "description": "Arbitrary key-value pairs (e.g., assigned_window_id)"},
            },
            "required": ["name", "plan"],
        },
    ),
    Tool(
        name="task_update",
        description="Report progress on a task: post a message, change status, or query state.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task to update/query"},
                "message": {"type": "string", "description": "Progress update (logged as an action under the active plan item)"},
                "query": {"type": "string", "description": "Ask a question about the task (AI-answered from history)"},
                "status": {"type": "string", "enum": ["active", "paused", "completed", "failed", "cancelled"], "description": "Set task status"},
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_item_update",
        description="Update a specific plan item's status. Automatically logs actions and durations.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "ordinal": {"type": "integer", "description": "Plan item index (0-based)"},
                "status": {"type": "string", "enum": ["pending", "active", "completed", "failed", "skipped"], "description": "New status for this plan item"},
                "note": {"type": "string", "description": "Optional note about this status change"},
            },
            "required": ["task_id", "ordinal", "status"],
        },
    ),
    Tool(
        name="task_log_action",
        description="Log a discrete action under a task's active plan item (e.g., 'ran ffmpeg', 'clicked Export').",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "action_type": {"type": "string", "description": "Action category: cli, gui, wait, vision, reasoning, other"},
                "summary": {"type": "string", "description": "What was done"},
                "input_data": {"type": "string", "description": "Command/input (optional)"},
                "output_data": {"type": "string", "description": "Result/output (optional)"},
                "status": {"type": "string", "enum": ["started", "completed", "failed"], "default": "completed"},
                "ordinal": {"type": "integer", "description": "Plan item index (optional — defaults to active item)"},
            },
            "required": ["task_id", "action_type", "summary"],
        },
    ),
    Tool(
        name="task_summary",
        description="Get a summary of a task at item level (default). Shows plan items with status and action counts.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "detail_level": {"type": "string", "enum": ["items", "actions", "full"], "default": "items", "description": "items=plan overview, actions=expand actions per item, full=include logs"},
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_drill_down",
        description="Drill into a specific plan item to see its actions and logs.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "ordinal": {"type": "integer", "description": "Plan item index (0-based)"},
            },
            "required": ["task_id", "ordinal"],
        },
    ),
    Tool(
        name="task_thread",
        description="Get the legacy chat thread for a task (messages, progress). Use task_summary for structured view.",
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
                "status": {"type": "string", "default": "active", "description": "Filter: active, completed, failed, cancelled, paused, all"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    ),

    # ── Desktop Control ────────────────────────────────────────
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

    # ── GUI Agent (grounding layer) ────────────────────────────
    Tool(
        name="gui_do",
        description="Execute a GUI action by natural language or coordinates. NL instructions are grounded to coordinates by the vision model, then executed via xdotool. Examples: 'click the Export button', 'type hello in the search box', 'click(847, 523)'.",
        inputSchema={
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "What to do — NL description or explicit coords like 'click(x, y)'"},
                "task_id": {"type": "string", "description": "Link action to a task (logged under active plan item)"},
                "window_name": {"type": "string", "description": "Target window (auto-focused before action)"},
            },
            "required": ["instruction"],
        },
    ),
    Tool(
        name="gui_find",
        description="Locate a UI element by natural language description. Returns coordinates without acting. Use to verify element positions before acting.",
        inputSchema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What to find — e.g., 'the Export button', 'the timeline scrubber'"},
                "window_name": {"type": "string", "description": "Target window to search in"},
            },
            "required": ["description"],
        },
    ),

    # ── Vision & Recording ─────────────────────────────────────
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
        name="health_check",
        description="Check if the DETM daemon, vision backends, and system are healthy.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Memory ─────────────────────────────────────────────────
    Tool(
        name="memory_search",
        description="Full-text search across MEMORY.md and memory/*.md files in the workspace.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms (space-separated, all must match)"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="memory_read",
        description="Read a memory file from the workspace.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "MEMORY.md"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 100},
            },
        },
    ),
    Tool(
        name="memory_append",
        description="Append a note to today's daily memory file or a specific file.",
        inputSchema={
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note to append"},
                "path": {"type": "string", "description": "Optional: specific file path"},
            },
            "required": ["note"],
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
    "task_item_update": ("POST", "/task_item_update"),
    "task_log_action": ("POST", "/task_log_action"),
    "task_summary": ("POST", "/task_summary"),
    "task_drill_down": ("POST", "/task_drill_down"),
    "task_thread": ("POST", "/task_thread"),
    "task_list": ("POST", "/task_list"),
    "health_check": ("GET", "/health"),
    "desktop_action": ("POST", "/desktop_action"),
    "gui_do": ("POST", "/gui_do"),
    "gui_find": ("POST", "/gui_find"),
    "desktop_look": ("POST", "/desktop_look"),
    "video_record": ("POST", "/video_record"),
    "memory_search": ("POST", "/memory_search"),
    "memory_read": ("POST", "/memory_read"),
    "memory_append": ("POST", "/memory_append"),
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
            "error": "DETM daemon is not running. Start it with: detm-daemon",
            "hint": "Run: detm-daemon  (or: python -m agentic_computer_use.daemon)"
        }))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


def main():
    log.info("Starting agentic-computer-use MCP server (proxy mode)")
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
