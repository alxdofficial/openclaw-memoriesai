/* task-tree.js — Collapsible hierarchical task viewer with unified activity panel */
"use strict";

const TaskTree = (() => {
  let _container = null;
  let _taskData = null;
  let _expandedItems = new Set();
  let _expandedActions = new Set();
  let _autoExpanded = false;
  let _verbose = false;
  let _messages = [];

  function init(container) {
    _container = container;
  }

  function setTask(data) {
    const isNewTask = !_taskData || _taskData.task_id !== data.task_id;
    const prevScroll = isNewTask ? 0 : (_container ? _container.scrollTop : 0);
    _taskData = data;

    if (isNewTask) {
      _expandedItems.clear();
      _expandedActions.clear();
      _autoExpanded = false;
    }

    if (!_autoExpanded || isNewTask) {
      _autoExpand(data.items);
      _autoExpanded = true;
    }

    render();
    if (_container) _container.scrollTop = prevScroll;
  }

  function setMessages(messages) {
    _messages = messages || [];
    render();
  }

  function clear() {
    _taskData = null;
    _messages = [];
    _expandedItems.clear();
    _expandedActions.clear();
    _autoExpanded = false;
    if (_container) _container.innerHTML = "";
  }

  function _autoExpand(items) {
    if (!items || items.length === 0) return;

    // Find most recent active item
    const active = items.filter(i => i.status === "active");
    if (active.length > 0) {
      _expandedItems.add(active[active.length - 1].ordinal);
      return;
    }

    // Otherwise expand most recent completed
    const completed = items.filter(i => i.status === "completed");
    if (completed.length > 0) {
      _expandedItems.add(completed[completed.length - 1].ordinal);
    }
  }

  function render() {
    if (!_container || !_taskData) return;
    const d = _taskData;

    let controls = "";
    if (d.status === "active") {
      controls = `
        <button class="task-ctrl-btn ctrl-pause" data-action="paused" title="Pause task">Pause</button>
        <button class="task-ctrl-btn ctrl-cancel" data-action="cancelled" title="Cancel task — frees the LLM">Cancel</button>
      `;
    } else if (d.status === "paused") {
      controls = `
        <button class="task-ctrl-btn ctrl-resume" data-action="active" title="Resume task">Resume</button>
        <button class="task-ctrl-btn ctrl-cancel" data-action="cancelled" title="Cancel task">Cancel</button>
      `;
    }

    let html = `
      <div class="tree-task-header">
        <span>${_esc(d.name)}</span>
        <span class="badge badge-${d.status}">${d.status}</span>
        <div class="progress-bar">
          <div class="progress-fill" style="width:${d.progress_pct || 0}%"></div>
        </div>
        <span style="color:var(--text-dim);font-size:11px">${d.progress_pct || 0}%</span>
        <span class="task-controls">
          ${controls}
          <button class="task-ctrl-btn verbose-btn" id="verbose-toggle">${_verbose ? "Verbose ▲" : "Verbose ▼"}</button>
        </span>
      </div>
    `;

    const items = d.items || [];
    for (const item of items) {
      const isExpanded = _expandedItems.has(item.ordinal);
      const icon = _itemIcon(item.status, isExpanded);
      const dur = item.duration_s != null ? `${Math.round(item.duration_s)}s` : "";
      const actCount = item.actions > 0 ? `${item.actions} action${item.actions !== 1 ? "s" : ""}` : "";

      html += `<div class="tree-item${isExpanded ? " expanded" : ""}" data-ordinal="${item.ordinal}">`;
      html += `
        <div class="tree-item-row" data-ordinal="${item.ordinal}">
          <span class="tree-item-icon">${icon}</span>
          <span class="tree-item-ordinal">${item.ordinal}:</span>
          <span class="tree-item-title">${_esc(item.title)}</span>
          <span class="badge badge-${item.status}">${item.status}</span>
          <span class="tree-item-duration">${dur}</span>
          <span class="tree-item-actions-count">${actCount}</span>
        </div>
      `;

      // Actions (shown when expanded)
      html += `<div class="tree-actions">`;
      if (isExpanded && item.action_details && item.action_details.length > 0) {
        for (const act of item.action_details) {
          const actExpanded = _expandedActions.has(act.id);
          const typeClass = `type-${act.action_type}`;
          html += `
            <div class="tree-action${actExpanded ? " expanded" : ""}" data-action-id="${act.id}">
              <span class="tree-action-type ${typeClass}">${_esc(act.action_type)}</span>
              <span>${_esc(act.summary)}</span>
              <span class="tree-action-status">— ${act.status}</span>
              <div class="tree-action-detail">
          `;
          if (act.input_data) {
            html += _renderStructuredData("Input", act.input_data, act.action_type);
          }
          if (act.output_data) {
            html += _renderStructuredData("Output", act.output_data, act.action_type);
          }
          if (act.logs && act.logs.length > 0) {
            const visibleLogs = _verbose
              ? act.logs
              : act.logs.filter(l => l.log_type !== "verdict");
            const hiddenCount = act.logs.length - visibleLogs.length;

            if (visibleLogs.length > 0) {
              html += `<div class="detail-label">Logs</div>`;
              for (const entry of visibleLogs) {
                const isVerdict = entry.log_type === "verdict";
                html += `<div class="action-log-entry${isVerdict ? " log-verdict" : ""}">${_esc(entry.content)}</div>`;
              }
            }
            if (!_verbose && hiddenCount > 0) {
              html += `<div class="action-log-hidden">(${hiddenCount} vision check${hiddenCount !== 1 ? "s" : ""} — enable Verbose to expand)</div>`;
            }
          }
          if (!act.input_data && !act.output_data && (!act.logs || act.logs.length === 0)) {
            html += `<pre style="color:var(--text-dim)">No additional data</pre>`;
          }
          html += `</div></div>`;
        }
      } else if (isExpanded) {
        html += `<div style="color:var(--text-dim);font-size:11px;padding:4px 0">Loading actions...</div>`;
      }
      html += `</div></div>`;
    }

    // Messages section at the bottom of the activity panel
    if (_messages && _messages.length > 0) {
      html += `<div class="task-messages">`;
      html += `<div class="task-messages-header">Messages</div>`;
      for (const msg of _messages) {
        const info = _classifyMsg(msg);
        html += `<div class="msg-entry ${info.css}">`;
        html += `<div class="msg-header">`;
        html += `<span class="msg-direction">${_esc(info.dir)}</span>`;
        html += `<span class="msg-time">${_formatTime(msg.created_at)}</span>`;
        html += `</div>`;
        html += `<div class="msg-content">${_esc(msg.content)}</div>`;
        html += `</div>`;
      }
      html += `</div>`;
    }

    _container.innerHTML = html;

    // Bind verbose toggle
    const verboseBtn = _container.querySelector("#verbose-toggle");
    if (verboseBtn) {
      verboseBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        _verbose = !_verbose;
        render();
      });
    }

    // Bind click handlers
    _container.querySelectorAll(".tree-item-row").forEach(row => {
      row.addEventListener("click", (e) => {
        const ordinal = parseInt(row.dataset.ordinal);
        if (_expandedItems.has(ordinal)) {
          _expandedItems.delete(ordinal);
        } else {
          _expandedItems.add(ordinal);
        }
        render();
        // Notify app to fetch action details if expanding
        if (_expandedItems.has(ordinal) && _onExpandItem) {
          _onExpandItem(_taskData.task_id, ordinal);
        }
      });
    });

    _container.querySelectorAll(".tree-action").forEach(el => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const actionId = el.dataset.actionId;
        if (_expandedActions.has(actionId)) {
          _expandedActions.delete(actionId);
        } else {
          _expandedActions.add(actionId);
        }
        render();
      });
    });

    // Thumbnail click → lightbox
    _container.querySelectorAll(".action-thumb").forEach(img => {
      img.addEventListener("click", (e) => {
        e.stopPropagation();
        _openLightbox(img.dataset.full || img.src);
      });
    });

    // Task control buttons
    _container.querySelectorAll(".task-ctrl-btn").forEach(btn => {
      if (btn.id === "verbose-toggle") return; // already bound above
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const newStatus = btn.dataset.action;
        if (_onStatusChange && _taskData) {
          _onStatusChange(_taskData.task_id, newStatus);
        }
      });
    });
  }

  const MSG_DIR_MAP = {
    "text:agent":       { css: "msg-agent",     dir: "LLM \u2192 task" },
    "progress:agent":   { css: "msg-agent",     dir: "LLM \u2192 task" },
    "text:user":        { css: "msg-user",      dir: "User \u2192 LLM" },
    "wait:*":           { css: "msg-wait",      dir: "SmartWait \u2192 task" },
    "lifecycle:*":      { css: "msg-lifecycle", dir: "System \u2192 task" },
    "progress:system":  { css: "msg-progress",  dir: "System \u2192 task" },
    "plan:*":           { css: "msg-plan",      dir: "LLM \u2192 plan" },
    "stuck:*":          { css: "msg-error",     dir: "System \u2192 alert" },
  };

  function _classifyMsg(msg) {
    const key1 = `${msg.msg_type}:${msg.role}`;
    if (MSG_DIR_MAP[key1]) return MSG_DIR_MAP[key1];
    const key2 = `${msg.msg_type}:*`;
    if (MSG_DIR_MAP[key2]) return MSG_DIR_MAP[key2];
    return { css: "msg-lifecycle", dir: "System \u2192 task" };
  }

  function _formatTime(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return "";
    }
  }

  let _onExpandItem = null;
  function onExpandItem(cb) {
    _onExpandItem = cb;
  }

  let _onStatusChange = null;
  function onStatusChange(cb) {
    _onStatusChange = cb;
  }

  function updateItemActions(ordinal, actionDetails) {
    if (!_taskData) return;
    const item = _taskData.items.find(i => i.ordinal === ordinal);
    if (item) {
      item.action_details = actionDetails;
      render();
    }
  }

  function _itemIcon(status, expanded) {
    if (status === "completed") return "✓";
    if (status === "failed")    return "✗";
    if (status === "skipped")   return "⊘";
    if (status === "active")    return "▶";
    if (status === "pending")   return "○";
    return expanded ? "▼" : "▷";
  }

  function _esc(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  function _renderStructuredData(label, raw, actionType) {
    let obj;
    try {
      obj = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch {
      // Not valid JSON — fall back to plain text
      return `<div class="detail-label">${_esc(label)}</div><pre>${_esc(raw)}</pre>`;
    }
    if (!obj || typeof obj !== "object") {
      return `<div class="detail-label">${_esc(label)}</div><pre>${_esc(String(raw))}</pre>`;
    }

    let html = `<div class="detail-label">${_esc(label)}</div>`;

    // Screenshot thumbnail
    if (obj.screenshot && obj.screenshot.thumb) {
      const thumbSrc = `/api/screenshots/${encodeURIComponent(obj.screenshot.thumb)}`;
      const fullSrc = obj.screenshot.full
        ? `/api/screenshots/${encodeURIComponent(obj.screenshot.full)}`
        : thumbSrc;
      html += `<img class="action-thumb" src="${thumbSrc}" loading="lazy" data-full="${fullSrc}" alt="${_esc(label)} screenshot">`;
    }

    // Click coordinates (gui actions)
    if (obj.x != null && obj.y != null) {
      html += `<div class="action-field"><span class="action-coords">click (${obj.x}, ${obj.y})</span></div>`;
    }

    // Confidence
    if (obj.confidence != null) {
      html += `<div class="action-field"><span class="field-label">Confidence</span><span class="action-confidence">${(obj.confidence * 100).toFixed(0)}%</span></div>`;
    }

    // Element text
    if (obj.element) {
      html += `<div class="action-field"><span class="field-label">Element</span><span class="field-value">${_esc(obj.element)}</span></div>`;
    }

    // Instruction / criteria
    if (obj.instruction) {
      html += `<div class="action-field"><span class="field-label">Instruction</span><span class="field-value">${_esc(obj.instruction)}</span></div>`;
    }
    if (obj.criteria) {
      html += `<div class="action-field"><span class="field-label">Criteria</span><span class="field-value">${_esc(obj.criteria)}</span></div>`;
    }

    // Wait-specific fields
    if (obj.target) {
      html += `<div class="action-field"><span class="field-label">Target</span><span class="field-value">${_esc(obj.target)}</span></div>`;
    }
    if (obj.state) {
      html += `<div class="action-field"><span class="field-label">State</span><span class="badge badge-${obj.state === "resolved" ? "completed" : obj.state === "timeout" ? "failed" : "pending"}">${_esc(obj.state)}</span></div>`;
    }
    if (obj.detail) {
      html += `<div class="action-field"><span class="field-label">Detail</span><span class="field-value">${_esc(obj.detail)}</span></div>`;
    }
    if (obj.elapsed_seconds != null) {
      html += `<div class="action-field"><span class="field-label">Elapsed</span><span class="field-value">${obj.elapsed_seconds}s</span></div>`;
    }
    if (obj.timeout != null) {
      html += `<div class="action-field"><span class="field-label">Timeout</span><span class="field-value">${obj.timeout}s</span></div>`;
    }

    // Action result
    if (obj.action && obj.ok != null) {
      html += `<div class="action-field"><span class="field-label">Action</span><span class="field-value">${_esc(obj.action)} — ${obj.ok ? "ok" : "failed"}</span></div>`;
    }

    return html;
  }

  function _openLightbox(src) {
    const overlay = document.createElement("div");
    overlay.className = "lightbox-overlay";
    overlay.innerHTML = `
      <button class="lightbox-close">&times;</button>
      <div class="lightbox-content"><img src="${src}" alt="Full screenshot"></div>
    `;
    const close = () => overlay.remove();
    overlay.addEventListener("click", close);
    overlay.querySelector(".lightbox-close").addEventListener("click", (e) => { e.stopPropagation(); close(); });
    document.addEventListener("keydown", function handler(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", handler); }
    });
    document.body.appendChild(overlay);
  }

  return { init, setTask, setMessages, clear, render, onExpandItem, onStatusChange, updateItemActions };
})();
