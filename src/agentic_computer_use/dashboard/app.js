/* app.js — Main dashboard application: polling, fetch helpers, wiring */
"use strict";

const App = (() => {
  const API_BASE = "";
  const POLL_TASKS_MS = 1000;
  const POLL_DETAIL_MS = 500;

  let _selectedTaskId = null;
  let _taskPollTimer = null;
  let _detailPollTimer = null;
  let _connected = false;

  // ─── Init ─────────────────────────────────
  function init() {
    const taskListEl = document.getElementById("task-list");
    const taskTreeEl = document.getElementById("task-tree");
    const screenImg = document.getElementById("screen-img");
    const screenPlaceholder = document.getElementById("screen-placeholder");
    const filterEl = document.getElementById("task-filter");

    TaskList.init(taskListEl, onTaskSelected, onTaskDelete);
    TaskTree.init(taskTreeEl);
    ScreenViewer.init(screenImg, screenPlaceholder);
    ScreenViewer.initRecordControls(document.getElementById("record-controls"));

    // Wire up tree expand callback → fetches action details
    TaskTree.onExpandItem((taskId, ordinal) => {
      fetchItemDetail(taskId, ordinal);
    });

    // Wire up task control buttons (cancel, pause, resume)
    TaskTree.onStatusChange(async (taskId, newStatus) => {
      await apiPostJson(`/api/tasks/${encodeURIComponent(taskId)}/status`, { status: newStatus });
      pollSelectedTask();
      pollTasks();
    });

    // Filter change
    filterEl.addEventListener("change", () => {
      pollTasks();
    });

    // Start polling
    checkHealth();
    pollTasks();
    _taskPollTimer = setInterval(pollTasks, POLL_TASKS_MS);

    // Always show system display (no task selected)
    ScreenViewer.showFullScreen();
  }

  // ─── API helpers ──────────────────────────
  async function apiFetch(path) {
    try {
      const resp = await fetch(API_BASE + path);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setConnected(true);
      return await resp.json();
    } catch (err) {
      console.error(`API error: ${path}`, err);
      setConnected(false);
      return null;
    }
  }

  async function apiPost(path) {
    try {
      const resp = await fetch(API_BASE + path, { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setConnected(true);
      return await resp.json();
    } catch (err) {
      console.error(`API POST error: ${path}`, err);
      return null;
    }
  }

  async function apiPostJson(path, body) {
    try {
      const resp = await fetch(API_BASE + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setConnected(true);
      return await resp.json();
    } catch (err) {
      console.error(`API POST error: ${path}`, err);
      return null;
    }
  }

  async function apiDelete(path) {
    try {
      const resp = await fetch(API_BASE + path, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setConnected(true);
      return await resp.json();
    } catch (err) {
      console.error(`API DELETE error: ${path}`, err);
      return null;
    }
  }

  function setConnected(state) {
    if (_connected === state) return;
    _connected = state;
    const el = document.getElementById("conn-status");
    if (state) {
      el.textContent = "● Connected";
      el.className = "status-dot connected";
    } else {
      el.textContent = "● Disconnected";
      el.className = "status-dot disconnected";
    }
  }

  // ─── Polling ──────────────────────────────
  async function checkHealth() {
    const data = await apiFetch("/api/health");
    if (data) setConnected(true);
    else setConnected(false);
  }

  async function pollTasks() {
    const filter = document.getElementById("task-filter").value;
    const data = await apiFetch(`/api/tasks?status=${filter}&limit=100`);
    if (data && data.tasks) {
      TaskList.setTasks(data.tasks);
    }
  }

  async function pollSelectedTask() {
    if (!_selectedTaskId) return;
    const data = await apiFetch(`/api/tasks/${encodeURIComponent(_selectedTaskId)}?detail=focused`);
    if (data && !data.error) {
      TaskTree.setTask(data);
    }
  }

  async function fetchItemDetail(taskId, ordinal) {
    const data = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/items/${ordinal}`);
    if (data && data.actions) {
      TaskTree.updateItemActions(ordinal, data.actions);
    }
  }

  // ─── Selection ────────────────────────────
  function onTaskSelected(taskId) {
    _selectedTaskId = taskId;

    // Show sections
    document.getElementById("empty-state").classList.add("hidden");
    document.getElementById("tree-section").classList.remove("hidden");

    // Start screen stream
    ScreenViewer.showTask(taskId);

    // Fetch full detail immediately
    pollSelectedTask();

    // Start detail polling (task tree every 2s)
    if (_detailPollTimer) clearInterval(_detailPollTimer);
    _detailPollTimer = setInterval(pollSelectedTask, POLL_DETAIL_MS);
  }

  // ─── Delete ───────────────────────────────
  async function onTaskDelete(taskId, taskName) {
    if (!confirm(`Delete task "${taskName}"?\n\nThis cannot be undone.`)) return;
    const result = await apiDelete(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (result && result.ok) {
      if (_selectedTaskId === taskId) {
        _selectedTaskId = null;
        if (_detailPollTimer) { clearInterval(_detailPollTimer); _detailPollTimer = null; }
        TaskTree.clear();
        ScreenViewer.showFullScreen();
        document.getElementById("empty-state").classList.remove("hidden");
        document.getElementById("tree-section").classList.add("hidden");
      }
      pollTasks();
    }
  }

  return { init, apiPost };
})();

// Boot
document.addEventListener("DOMContentLoaded", App.init);
