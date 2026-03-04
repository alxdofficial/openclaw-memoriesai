/**
 * detm-tool-logger — OpenClaw plugin
 *
 * After every non-DETM tool call, find the active DETM task and POST a
 * log_action entry so it appears in the DETM dashboard. Desktop and task
 * management tools are skipped because the DETM daemon already logs those
 * internally.
 */

const DETM_BASE = "http://127.0.0.1:18790";
const CACHE_TTL_MS = 3000;

// DETM-internal tools — already logged by the daemon, or not user-visible
const SKIP_TOOLS = new Set([
  "desktop_look", "desktop_action", "gui_do", "gui_find",
  "task_register", "task_update", "task_log_action", "task_plan_append",
  "task_item_update", "task_summary", "task_drill_down", "task_thread",
  "task_list", "smart_wait", "wait_status", "wait_update", "wait_cancel",
  "memory_search", "memory_read", "memory_append",
  "video_record", "mavi_understand", "live_ui",
]);

let cachedTaskId = null;
let cacheTs = 0;

async function getActiveTaskId() {
  const now = Date.now();
  if (cachedTaskId !== null && now - cacheTs < CACHE_TTL_MS) return cachedTaskId;
  try {
    const res = await fetch(`${DETM_BASE}/api/tasks?status=active&limit=1`);
    if (!res.ok) { cachedTaskId = null; cacheTs = now; return null; }
    const data = await res.json();
    cachedTaskId = data?.tasks?.[0]?.task_id ?? null;
    cacheTs = now;
    return cachedTaskId;
  } catch {
    cachedTaskId = null;
    cacheTs = now;
    return null;
  }
}

function summarize(toolName, params) {
  switch (toolName) {
    case "web_search":
      return `web_search: ${String(params.query ?? "").slice(0, 110)}`;
    case "WebFetch":
      return `WebFetch: ${String(params.url ?? "").slice(0, 110)}`;
    case "Bash":
      return `bash: ${String(params.command ?? "").replace(/\n/g, " ").slice(0, 100)}`;
    case "Read":
      return `read: ${params.file_path ?? ""}`;
    case "Write":
      return `write: ${params.file_path ?? ""}`;
    case "Edit":
      return `edit: ${params.file_path ?? ""}`;
    case "Glob":
      return `glob: ${params.pattern ?? ""}`;
    case "Grep":
      return `grep: ${params.pattern ?? ""}`;
    case "Agent":
      return `agent(${params.subagent_type ?? ""}): ${String(params.description ?? "").slice(0, 70)}`;
    case "NotebookEdit":
      return `notebook_edit: ${params.notebook_path ?? ""}`;
    case "WebSearch":
      return `web_search: ${String(params.query ?? "").slice(0, 110)}`;
    default:
      return String(toolName).slice(0, 80);
  }
}

export default {
  id: "detm-tool-logger",
  name: "DETM Tool Logger",
  description: "Logs every non-DETM tool call to the active DETM task in the dashboard",
  register(api) {
    // Inject agent_id into task_register so the dashboard can badge tasks per agent
    api.on("before_tool_call", (event) => {
      if (event.toolName !== "task_register") return;
      const agentId = event.ctx?.agentId;
      if (!agentId) return;
      return { params: { ...event.params, agent_id: agentId } };
    });

    api.on("after_tool_call", async (event) => {
      const { toolName, params = {}, error } = event;
      if (SKIP_TOOLS.has(toolName)) return;

      const taskId = await getActiveTaskId();
      if (!taskId) return;

      const summary = summarize(toolName, params);
      const status = error ? "failed" : "completed";

      try {
        await fetch(`${DETM_BASE}/task_log_action`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task_id: taskId,
            action_type: "cli",
            summary,
            status,
          }),
        });
      } catch {
        // DETM daemon not running — ignore silently
      }
    });
  },
};
