/* live-session-viewer.js — Modal viewer for live_ui session recordings */
"use strict";

const LiveSessionViewer = (() => {
  let _modal = null;

  // ── Color mapping for event types ────────────────────────────
  const _TYPE_STYLE = {
    model_text:     { cls: "lsv-sender sender-agent",  label: "llm:narrate" },
    tool_call:      { cls: "lsv-sender sender-gui",    label: null },  // label set dynamically
    tool_response:  { cls: "lsv-sender sender-tool",   label: "result" },
    done:           { cls: "lsv-sender sender-vision",  label: "done" },
    escalate:       { cls: "lsv-sender sender-wait",    label: "escalate" },
    error:          { cls: "lsv-sender sender-error",   label: "error" },
    timeout:        { cls: "lsv-sender sender-error",   label: "timeout" },
    instruction:    { cls: "lsv-sender sender-cli",     label: "instruction" },
  };

  // GUI action sub-labels (matching task-tree style)
  const _ACTION_LABEL = {
    click:        "gui:click",
    double_click: "gui:double_click",
    type_text:    "gui:type",
    key_press:    "gui:key",
    scroll:       "gui:scroll",
    done:         "done",
    escalate:     "escalate",
  };

  function _esc(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  // ── Public API ───────────────────────────────────────────────

  async function open(sessionId) {
    _close();
    const data = await _fetch(`/api/live_sessions/${encodeURIComponent(sessionId)}`);
    if (!data || data.error) {
      alert(`Could not load session: ${data ? data.error : "network error"}`);
      return;
    }
    _render(data);
  }

  function _close() {
    if (_modal) { _modal.remove(); _modal = null; }
  }

  // ── Fetch ────────────────────────────────────────────────────

  async function _fetch(path) {
    try {
      const r = await fetch(path);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      console.error("LiveSessionViewer fetch error:", e);
      return null;
    }
  }

  // ── Render ───────────────────────────────────────────────────

  function _render(data) {
    const { session_id, instruction, context, timeout, events, frame_count } = data;

    // Filter to displayable events (exclude raw frame markers from feed)
    const feedEvents = events.filter(e => e.type !== "frame" && e.type !== "instruction");
    // Build frame index: ts → frame_n (for snapping)
    const frameTimestamps = events
      .filter(e => e.type === "frame")
      .map(e => ({ n: e.n, ts: e.ts }));

    // ── Modal container ─────────────────────────────────────────
    _modal = document.createElement("div");
    _modal.className = "lsv-overlay";
    _modal.innerHTML = `
      <div class="lsv-modal">
        <div class="lsv-header">
          <div class="lsv-header-left">
            <span class="lsv-title">live_ui session</span>
            <span class="lsv-session-id">${_esc(session_id.slice(0, 12))}</span>
          </div>
          <button class="lsv-close">&times;</button>
        </div>

        <div class="lsv-instruction-bar">
          <span class="lsv-instr-label">Instruction</span>
          <span class="lsv-instr-text">${_esc(instruction)}</span>
          ${context ? `<span class="lsv-instr-label" style="margin-left:12px">Context</span><span class="lsv-instr-text">${_esc(context)}</span>` : ""}
          <span class="lsv-instr-label" style="margin-left:12px">Timeout</span>
          <span class="lsv-instr-text">${timeout}s</span>
        </div>

        <div class="lsv-body">
          <div class="lsv-feed" id="lsv-feed">
            ${_renderFeed(feedEvents, frameTimestamps)}
          </div>
          <div class="lsv-viewer" id="lsv-viewer">
            ${frame_count > 0 ? _renderFrameViewer(session_id, frame_count) : '<div class="lsv-no-frames">No frames recorded</div>'}
          </div>
        </div>
      </div>
    `;

    // ── Close handlers ──────────────────────────────────────────
    _modal.querySelector(".lsv-close").addEventListener("click", _close);
    _modal.addEventListener("click", (e) => {
      if (e.target === _modal) _close();
    });
    document.addEventListener("keydown", _escHandler);

    // ── Event click → snap frame ────────────────────────────────
    if (frame_count > 0) {
      _modal.querySelectorAll(".lsv-event[data-ts]").forEach(el => {
        el.addEventListener("click", () => {
          const ts = parseFloat(el.dataset.ts);
          const n = _nearestFrame(ts, frameTimestamps);
          if (n >= 0) _setFrame(n);
          el.classList.toggle("lsv-event-selected");
        });
      });

      // Frame scrubber
      const scrubber = _modal.querySelector("#lsv-scrubber");
      const prevBtn = _modal.querySelector("#lsv-prev");
      const nextBtn = _modal.querySelector("#lsv-next");
      if (scrubber) {
        scrubber.addEventListener("input", () => _setFrame(parseInt(scrubber.value)));
      }
      if (prevBtn) prevBtn.addEventListener("click", () => _stepFrame(-1, frame_count));
      if (nextBtn) nextBtn.addEventListener("click", () => _stepFrame(1, frame_count));
    }

    document.body.appendChild(_modal);
  }

  // ── Feed ─────────────────────────────────────────────────────

  function _renderFeed(events, frameTimestamps) {
    if (events.length === 0) return '<div class="lsv-empty">No events recorded</div>';

    let html = "";
    for (const ev of events) {
      const style = _TYPE_STYLE[ev.type];
      if (!style) continue;

      let label = style.label;
      let bodyHtml = "";

      if (ev.type === "tool_call") {
        label = _ACTION_LABEL[ev.name] || `gui:${ev.name}`;
        const argsStr = _fmtArgs(ev.name, ev.args || {});
        bodyHtml = `<span class="lsv-event-text">${_esc(argsStr)}</span>`;

        // tool_response for non-terminal actions: shown inline as sub-line
      } else if (ev.type === "tool_response") {
        label = "result";
        bodyHtml = `<span class="lsv-event-text lsv-muted">${_esc(ev.result || "")}</span>`;

      } else if (ev.type === "model_text") {
        bodyHtml = `<span class="lsv-event-text">${_esc(ev.text || "")}</span>`;

      } else if (ev.type === "done") {
        const ok = ev.success ? "success" : "failed";
        bodyHtml = `<span class="lsv-event-text">${_esc(ev.summary || ok)}</span>`;

      } else if (ev.type === "escalate") {
        bodyHtml = `<span class="lsv-event-text">${_esc(ev.reason || "")}</span>`;

      } else if (ev.type === "error") {
        bodyHtml = `<span class="lsv-event-text">${_esc(ev.message || "")}</span>`;
      }

      const ts = ev.ts ? `data-ts="${ev.ts}"` : "";
      html += `
        <div class="lsv-event lsv-event-${_esc(ev.type)}" ${ts}>
          <span class="${style.cls}">${_esc(label)}</span>
          ${bodyHtml}
        </div>
      `;
    }
    return html;
  }

  function _fmtArgs(name, args) {
    if (name === "click" || name === "double_click") {
      return `(${args.x ?? "?"}, ${args.y ?? "?"})${args.button && args.button !== "left" ? ` ${args.button}` : ""}`;
    }
    if (name === "type_text") return `"${(args.text || "").slice(0, 60)}"`;
    if (name === "key_press") return args.key || "";
    if (name === "scroll") return `(${args.x}, ${args.y}) ${args.direction} ×${args.amount || 3}`;
    return JSON.stringify(args).slice(0, 80);
  }

  // ── Frame viewer ─────────────────────────────────────────────

  function _renderFrameViewer(sessionId, frameCount) {
    return `
      <div class="lsv-frame-box">
        <img id="lsv-frame-img" src="/api/live_sessions/${_esc(sessionId)}/frames/0" alt="Frame" loading="lazy">
      </div>
      <div class="lsv-scrubber-row">
        <button id="lsv-prev" class="lsv-nav-btn">◀</button>
        <input id="lsv-scrubber" type="range" min="0" max="${frameCount - 1}" value="0" class="lsv-scrubber">
        <button id="lsv-next" class="lsv-nav-btn">▶</button>
        <span id="lsv-frame-label" class="lsv-frame-label">1 / ${frameCount}</span>
      </div>
    `;
  }

  function _setFrame(n) {
    if (!_modal) return;
    const scrubber = _modal.querySelector("#lsv-scrubber");
    const img = _modal.querySelector("#lsv-frame-img");
    const label = _modal.querySelector("#lsv-frame-label");
    if (!img) return;
    const total = scrubber ? parseInt(scrubber.max) + 1 : 1;
    const clamped = Math.max(0, Math.min(n, total - 1));
    // Extract session id from current img src
    const src = img.src;
    const newSrc = src.replace(/\/frames\/\d+$/, `/frames/${clamped}`);
    img.src = newSrc;
    if (scrubber) scrubber.value = clamped;
    if (label) label.textContent = `${clamped + 1} / ${total}`;
  }

  function _stepFrame(delta, frameCount) {
    if (!_modal) return;
    const scrubber = _modal.querySelector("#lsv-scrubber");
    const cur = scrubber ? parseInt(scrubber.value) : 0;
    _setFrame(cur + delta);
  }

  function _nearestFrame(ts, frameTimestamps) {
    // Find the last frame recorded before or at ts
    let best = -1;
    for (const f of frameTimestamps) {
      if (f.ts <= ts) best = f.n;
      else break;
    }
    return best >= 0 ? best : (frameTimestamps.length > 0 ? frameTimestamps[0].n : -1);
  }

  function _escHandler(e) {
    if (e.key === "Escape") {
      _close();
      document.removeEventListener("keydown", _escHandler);
    }
  }

  return { open };
})();
