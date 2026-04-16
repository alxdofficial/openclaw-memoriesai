/* dump-dialog.js — modal with a horizontal timeline for picking a dump window.
 *
 * Shape:
 *   ┌─ Dump logs ────────────────────────────────────────────┐
 *   │ zoom:  1h  6h  [24h]  7d  30d  all       reset        │
 *   │                                                        │
 *   │  ┌──────────────────────────────────────────────────┐  │
 *   │  │ ══╪═══════◆═◆═════════════◆═══╪════════════════  │  │  ← timeline
 *   │  │   ▲                           ▲                   │  │    w/ task markers
 *   │  └──────────────────────────────────────────────────┘  │
 *   │   Apr 15 21:37                         Apr 16 21:37    │
 *   │                                                        │
 *   │  window:  Apr 16 01:27 → Apr 16 21:37  (20h 10m)       │
 *   │  tasks in window:  3 completed, 1 failed               │
 *   │                                                        │
 *   │                          [Cancel]  [Download 📥]        │
 *   └────────────────────────────────────────────────────────┘
 *
 * Markers are placed only for tasks with a known end timestamp
 * (completed|cancelled|failed|paused) — we never draw
 * never-ending markers for active tasks.
 */
"use strict";

const DumpDialog = (() => {
  // Named zoom levels (total visible range, in ms)
  const ZOOMS = [
    { label: "1h",  ms: 3600e3 },
    { label: "6h",  ms: 6 * 3600e3 },
    { label: "24h", ms: 24 * 3600e3, default: true },
    { label: "7d",  ms: 7 * 24 * 3600e3 },
    { label: "30d", ms: 30 * 24 * 3600e3 },
    { label: "all", ms: null },  // fit to tasks + window
  ];

  const STATUS_COLORS = {
    completed: "#4caf50",
    failed:    "#e53935",
    cancelled: "#9e9e9e",
    paused:    "#fbc02d",
  };

  let _overlay, _host;
  let _tasks = [];
  let _viewStart = 0, _viewEnd = 0;   // total visible range
  let _pickStart = 0, _pickEnd = 0;   // user's selection (subset of view)
  let _dragging = null;               // "start" | "end" | null

  // ── public ───────────────────────────────────────────────
  async function open() {
    if (_overlay) { close(); }
    _overlay = document.createElement("div");
    _overlay.className = "dump-dialog-overlay";
    _overlay.innerHTML = _tmpl();
    document.body.appendChild(_overlay);
    _host = _overlay.querySelector(".dump-dialog");
    _attachHandlers();
    _setZoom(ZOOMS.find(z => z.default));
    await _loadTasks();
    _render();
  }

  function close() {
    if (_overlay) {
      _overlay.remove();
      _overlay = null;
    }
  }

  // ── template ─────────────────────────────────────────────
  function _tmpl() {
    const zoomBtns = ZOOMS.map(z =>
      `<button class="dump-zoom-btn" data-zoom="${z.label}">${z.label}</button>`
    ).join("");

    return `
      <div class="dump-dialog">
        <div class="dump-dialog-header">
          <span>📥 Download logs dump</span>
          <button class="dump-close-btn" title="Close">&times;</button>
        </div>
        <div class="dump-dialog-body">
          <div class="dump-zoom-row">
            <span class="dump-label">zoom:</span>
            ${zoomBtns}
            <button class="dump-reset-btn" title="Reset handles to the full visible range">reset handles</button>
          </div>
          <div class="dump-timeline-wrap">
            <div class="dump-timeline">
              <div class="dump-timeline-track"></div>
              <div class="dump-timeline-selection"></div>
              <div class="dump-timeline-markers"></div>
              <div class="dump-timeline-handle" data-handle="start" title="drag to set start"></div>
              <div class="dump-timeline-handle" data-handle="end"   title="drag to set end"></div>
            </div>
            <div class="dump-timeline-labels">
              <span class="dump-label-start">…</span>
              <span class="dump-label-end">…</span>
            </div>
          </div>
          <div class="dump-summary">
            <div class="dump-window-text">…</div>
            <div class="dump-count-text">…</div>
          </div>
        </div>
        <div class="dump-dialog-footer">
          <button class="dump-cancel-btn">Cancel</button>
          <button class="dump-download-btn">Download 📥</button>
        </div>
      </div>
    `;
  }

  // ── event wiring ─────────────────────────────────────────
  function _attachHandlers() {
    _overlay.addEventListener("click", e => {
      if (e.target === _overlay) close();
    });
    _host.querySelector(".dump-close-btn").addEventListener("click", close);
    _host.querySelector(".dump-cancel-btn").addEventListener("click", close);
    _host.querySelector(".dump-download-btn").addEventListener("click", _download);

    _host.querySelectorAll(".dump-zoom-btn").forEach(btn =>
      btn.addEventListener("click", () => {
        const z = ZOOMS.find(z => z.label === btn.dataset.zoom);
        _setZoom(z);
        _render();
      })
    );

    _host.querySelector(".dump-reset-btn").addEventListener("click", () => {
      _pickStart = _viewStart;
      _pickEnd = _viewEnd;
      _render();
    });

    const timeline = _host.querySelector(".dump-timeline");
    _host.querySelectorAll(".dump-timeline-handle").forEach(h =>
      h.addEventListener("mousedown", e => {
        _dragging = h.dataset.handle;
        e.preventDefault();
      })
    );
    document.addEventListener("mousemove", _onDrag);
    document.addEventListener("mouseup", () => { _dragging = null; });
  }

  function _onDrag(e) {
    if (!_dragging) return;
    const tl = _host.querySelector(".dump-timeline");
    const rect = tl.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const ts = _viewStart + frac * (_viewEnd - _viewStart);

    if (_dragging === "start") {
      _pickStart = Math.min(ts, _pickEnd - 60e3);   // keep at least 1 min
    } else {
      _pickEnd = Math.max(ts, _pickStart + 60e3);
    }
    _render();
  }

  // ── data ─────────────────────────────────────────────────
  async function _loadTasks() {
    try {
      const resp = await fetch("/api/tasks?status=all&limit=200");
      const data = await resp.json();
      _tasks = data.tasks || [];
    } catch (e) {
      console.error("dump-dialog: could not load tasks", e);
      _tasks = [];
    }
  }

  // Closed tasks only — never draw a marker for still-running work
  function _closedTasks() {
    return _tasks.filter(t =>
      t.created_at && t.last_update &&
      ["completed", "failed", "cancelled", "paused"].includes(t.status)
    );
  }

  // ── zoom ────────────────────────────────────────────────
  function _setZoom(zoom) {
    const now = Date.now();
    if (zoom.label === "all") {
      const closed = _closedTasks();
      const starts = closed.map(t => new Date(t.created_at).getTime());
      const ends = closed.map(t => new Date(t.last_update).getTime());
      _viewStart = starts.length ? Math.min(...starts) : now - 24 * 3600e3;
      _viewEnd   = ends.length ? Math.max(...ends, now) : now;
      // pad 5%
      const pad = (_viewEnd - _viewStart) * 0.05;
      _viewStart -= pad;
      _viewEnd += pad;
    } else {
      _viewEnd = now;
      _viewStart = now - zoom.ms;
    }
    // Default selection = last 24h (or the full view if shorter)
    const defaultSpan = Math.min(24 * 3600e3, _viewEnd - _viewStart);
    _pickEnd = _viewEnd;
    _pickStart = _viewEnd - defaultSpan;
    if (_pickStart < _viewStart) _pickStart = _viewStart;

    _host.querySelectorAll(".dump-zoom-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.zoom === zoom.label)
    );
  }

  // ── render ──────────────────────────────────────────────
  function _render() {
    if (!_host) return;
    const tl = _host.querySelector(".dump-timeline");
    const sel = _host.querySelector(".dump-timeline-selection");
    const startH = _host.querySelector('.dump-timeline-handle[data-handle="start"]');
    const endH   = _host.querySelector('.dump-timeline-handle[data-handle="end"]');
    const markers = _host.querySelector(".dump-timeline-markers");
    const span = _viewEnd - _viewStart;

    function frac(ts) { return Math.max(0, Math.min(1, (ts - _viewStart) / span)); }

    // Selection band
    const a = frac(_pickStart), b = frac(_pickEnd);
    sel.style.left = (a * 100) + "%";
    sel.style.width = ((b - a) * 100) + "%";
    startH.style.left = (a * 100) + "%";
    endH.style.left   = (b * 100) + "%";

    // Task markers
    markers.innerHTML = "";
    const closed = _closedTasks();
    for (const t of closed) {
      const s = new Date(t.created_at).getTime();
      const e = new Date(t.last_update).getTime();
      // Skip tasks entirely outside the current view
      if (e < _viewStart || s > _viewEnd) continue;
      const fs = frac(Math.max(s, _viewStart));
      const fe = frac(Math.min(e, _viewEnd));
      const bar = document.createElement("div");
      bar.className = "dump-task-bar";
      bar.title = `${t.name}  [${t.status}]\n${t.created_at} → ${t.last_update}`;
      bar.style.left = (fs * 100) + "%";
      bar.style.width = Math.max(0.3, (fe - fs) * 100) + "%";
      bar.style.background = STATUS_COLORS[t.status] || "#90a4ae";
      markers.appendChild(bar);
    }

    // Labels
    _host.querySelector(".dump-label-start").textContent = _fmt(_viewStart);
    _host.querySelector(".dump-label-end").textContent = _fmt(_viewEnd);

    // Summary
    const picked = closed.filter(t => {
      const s = new Date(t.created_at).getTime();
      const e = new Date(t.last_update).getTime();
      return e >= _pickStart && s <= _pickEnd;
    });
    const byStatus = picked.reduce((m, t) => ((m[t.status] = (m[t.status] || 0) + 1), m), {});
    const windowMs = _pickEnd - _pickStart;
    _host.querySelector(".dump-window-text").textContent =
      `window:  ${_fmt(_pickStart)} → ${_fmt(_pickEnd)}  (${_humanDuration(windowMs)})`;
    _host.querySelector(".dump-count-text").textContent =
      `tasks in window:  ${picked.length === 0 ? "none" :
        Object.entries(byStatus).map(([k, v]) => `${v} ${k}`).join(", ")}`;
  }

  function _fmt(ms) {
    const d = new Date(ms);
    const mo = d.toLocaleString("en-US", { month: "short" });
    return `${mo} ${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function _humanDuration(ms) {
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60), mm = m % 60;
    if (h < 48) return `${h}h ${mm}m`;
    return `${Math.floor(h / 24)}d ${h % 24}h`;
  }

  function _download() {
    const since = Math.floor(_pickStart / 1000);   // epoch seconds
    const until = Math.floor(_pickEnd / 1000);
    const url = `/api/dump?since=${since}&until=${until}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    close();
  }

  return { open, close };
})();
