/* task-list.js — Sidebar task list component */
"use strict";

const TaskList = (() => {
  let _container = null;
  let _tasks = [];
  let _selectedId = null;
  let _onSelect = null;

  function init(container, onSelect) {
    _container = container;
    _onSelect = onSelect;
  }

  function setTasks(tasks) {
    _tasks = tasks || [];
    render();
  }

  function getSelectedId() {
    return _selectedId;
  }

  function render() {
    if (!_container) return;
    _container.innerHTML = "";

    if (_tasks.length === 0) {
      const li = document.createElement("li");
      li.style.color = "var(--text-dim)";
      li.style.padding = "16px 12px";
      li.style.fontSize = "12px";
      li.textContent = "No tasks found.";
      _container.appendChild(li);
      return;
    }

    for (const task of _tasks) {
      const li = document.createElement("li");
      if (task.task_id === _selectedId) li.classList.add("selected");

      const statusIcon = _statusIcon(task.status);

      li.innerHTML = `
        <span class="task-name">${statusIcon} ${_esc(task.name)}</span>
        <div class="task-meta">
          <span class="badge badge-${task.status}">${task.status}</span>
          <span>${task.progress_pct || 0}%</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" style="width:${task.progress_pct || 0}%"></div>
        </div>
      `;

      li.addEventListener("click", () => {
        _selectedId = task.task_id;
        render();
        if (_onSelect) _onSelect(task.task_id);
      });

      _container.appendChild(li);
    }
  }

  function _statusIcon(status) {
    const icons = {
      active: "▶",
      completed: "✓",
      failed: "✗",
      paused: "⏸",
      pending: "○",
      cancelled: "⊘",
    };
    return icons[status] || "•";
  }

  function _esc(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  return { init, setTasks, getSelectedId, render };
})();
