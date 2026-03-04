/* live-session-viewer.js — Modal viewer for live_ui session recordings */
"use strict";

const LiveSessionViewer = (() => {
  let _modal = null;
  let _frameTimestamps = [];   // [{n, ts}] sorted by n
  let _feedEventEls = [];      // [{ts, el}] sorted by ts — for frame→event sync
  let _sessionStartedAt = null; // ts of instruction event — for audio-frame sync
  let _scrubbing = false;       // true while user is dragging the scrubber

  // ── Color mapping for event types ────────────────────────────
  const _TYPE_STYLE = {
    model_text:     { cls: "lsv-sender sender-agent",  label: "llm:thought" },
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
    move_mouse:   "gui:move",
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
    _scrubbing = false;
    if (_modal) {
      if (_modal._es) { _modal._es.close(); _modal._es = null; }
      if (_modal._audioReader) { _modal._audioReader.cancel(); _modal._audioReader = null; }
      if (_modal._audioCtx) { _modal._audioCtx.close(); _modal._audioCtx = null; }
      _modal.remove();
      _modal = null;
    }
    document.removeEventListener("keydown", _escHandler);
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
    const { session_id, instruction, context, timeout, events, frame_count, has_audio } = data;

    // Filter to displayable events (exclude raw frame markers from feed)
    const feedEvents = events.filter(e => e.type !== "frame" && e.type !== "instruction");
    // Build frame index: ts → frame_n (for snapping)
    const frameTimestamps = events
      .filter(e => e.type === "frame")
      .map(e => ({ n: e.n, ts: e.ts }));

    // Store for bidirectional sync
    _frameTimestamps = frameTimestamps;
    _feedEventEls = [];
    const instrEv = events.find(e => e.type === "instruction");
    _sessionStartedAt = instrEv ? instrEv.ts : null;

    // ── Modal container ─────────────────────────────────────────
    _modal = document.createElement("div");
    _modal.className = "lsv-overlay";
    _modal.innerHTML = `
      <div class="lsv-modal">
        <div class="lsv-header">
          <div class="lsv-header-left">
            <span class="lsv-title">live_ui</span>
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
            ${has_audio ? _renderAudioPlayer(session_id) : ""}
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

    // ── Collect feed event elements for reverse sync ─────────────
    _modal.querySelectorAll(".lsv-event[data-ts]").forEach(el => {
      const ts = parseFloat(el.dataset.ts);
      if (!isNaN(ts)) _feedEventEls.push({ ts, el });
    });
    // Already sorted chronologically since events are in order

    // ── Event click → snap frame ────────────────────────────────
    if (frame_count > 0) {
      _modal.querySelectorAll(".lsv-event[data-ts]").forEach(el => {
        el.addEventListener("click", () => {
          const ts = parseFloat(el.dataset.ts);
          const n = _nearestFrame(ts, frameTimestamps);
          if (n >= 0) _setFrame(n, { skipEventSync: true });
          _highlightEvent(el);
        });
      });

      // Frame scrubber → highlight active event
      const scrubber = _modal.querySelector("#lsv-scrubber");
      const prevBtn = _modal.querySelector("#lsv-prev");
      const nextBtn = _modal.querySelector("#lsv-next");
      if (scrubber) {
        scrubber.addEventListener("pointerdown", () => { _scrubbing = true; });
        scrubber.addEventListener("pointerup",   () => { _scrubbing = false; });
        scrubber.addEventListener("input", () => _setFrame(parseInt(scrubber.value)));
      }
      if (prevBtn) prevBtn.addEventListener("click", () => _stepFrame(-1, frame_count));
      if (nextBtn) nextBtn.addEventListener("click", () => _stepFrame(1, frame_count));
    }

    document.body.appendChild(_modal);

    // Audio timeupdate → frame sync (suppressed while user is scrubbing)
    const audioEl = _modal.querySelector(".lsv-audio");
    if (audioEl && _sessionStartedAt && _frameTimestamps.length > 0) {
      audioEl.addEventListener("timeupdate", () => {
        if (_scrubbing) return;
        const targetTs = _sessionStartedAt + audioEl.currentTime;
        const n = _nearestFrame(targetTs, _frameTimestamps);
        if (n >= 0) _setFrame(n, { skipEventSync: false, skipAudioSync: true });
      });
    }
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

  function _fmtCoord(args) {
    // Support both legacy pixel coords and current percentage coords
    if (args.x_pct != null) {
      return `(${(args.x_pct * 100).toFixed(1)}%, ${(args.y_pct * 100).toFixed(1)}%)`;
    }
    return `(${args.x ?? "?"}, ${args.y ?? "?"})`;
  }

  function _fmtArgs(name, args) {
    if (name === "move_mouse") return _fmtCoord(args);
    if (name === "click" || name === "double_click") {
      const btn = args.button && args.button !== "left" ? ` ${args.button}` : "";
      return `${_fmtCoord(args)}${btn}`;
    }
    if (name === "type_text") return `"${(args.text || "").slice(0, 60)}"`;
    if (name === "key_press") return args.key || "";
    if (name === "scroll") {
      return `${_fmtCoord(args)} ${args.direction} ×${args.amount || 3}`;
    }
    return JSON.stringify(args).slice(0, 80);
  }

  // ── Audio player ─────────────────────────────────────────────

  function _renderAudioPlayer(sessionId) {
    return `
      <div class="lsv-audio-row">
        <span class="lsv-audio-label">Gemini audio</span>
        <audio class="lsv-audio" controls preload="none"
               src="/api/live_sessions/${_esc(sessionId)}/audio">
          Your browser does not support audio playback.
        </audio>
      </div>
    `;
  }

  // ── Frame viewer ─────────────────────────────────────────────

  function _renderFrameViewer(sessionId, frameCount) {
    return `
      <div class="lsv-frame-box">
        <img id="lsv-frame-img"
             src="/api/live_sessions/${_esc(sessionId)}/frames/0"
             data-session-id="${_esc(sessionId)}"
             alt="Frame">
      </div>
      <div class="lsv-scrubber-row">
        <button id="lsv-prev" class="lsv-nav-btn">◀</button>
        <input id="lsv-scrubber" type="range" min="0" max="${frameCount - 1}" value="0" class="lsv-scrubber">
        <button id="lsv-next" class="lsv-nav-btn">▶</button>
        <span id="lsv-frame-label" class="lsv-frame-label">1 / ${frameCount}</span>
      </div>
    `;
  }

  function _setFrame(n, opts = {}) {
    if (!_modal) return;
    const scrubber = _modal.querySelector("#lsv-scrubber");
    const img = _modal.querySelector("#lsv-frame-img");
    const label = _modal.querySelector("#lsv-frame-label");
    if (!img) return;
    const total = scrubber ? parseInt(scrubber.max) + 1 : 1;
    const clamped = Math.max(0, Math.min(n, total - 1));
    const sid = img.dataset.sessionId;
    img.src = `/api/live_sessions/${encodeURIComponent(sid)}/frames/${clamped}`;
    if (scrubber) scrubber.value = clamped;
    if (label) label.textContent = `${clamped + 1} / ${total}`;

    // Audio-frame sync: seek audio to match frame timestamp
    if (!opts.skipAudioSync && _sessionStartedAt && _frameTimestamps.length > 0) {
      const frameEntry = _frameTimestamps.find(f => f.n === clamped)
        || _frameTimestamps[Math.min(clamped, _frameTimestamps.length - 1)];
      if (frameEntry) {
        const audioEl = _modal.querySelector(".lsv-audio");
        if (audioEl) {
          const targetTime = Math.max(0, frameEntry.ts - _sessionStartedAt);
          if (Math.abs(audioEl.currentTime - targetTime) > 0.3) {
            audioEl.currentTime = targetTime;
          }
        }
      }
    }

    // Reverse sync: highlight the event active at this frame's timestamp
    if (!opts.skipEventSync && _frameTimestamps.length > 0) {
      const frameEntry = _frameTimestamps.find(f => f.n === clamped)
        || _frameTimestamps[Math.min(clamped, _frameTimestamps.length - 1)];
      if (frameEntry) {
        // Find the last feed event whose ts ≤ frame ts
        let activeEl = null;
        for (const { ts, el } of _feedEventEls) {
          if (ts <= frameEntry.ts) activeEl = el;
          else break;
        }
        if (activeEl) _highlightEvent(activeEl);
      }
    }
  }

  function _highlightEvent(el) {
    // Clear previous highlights
    _feedEventEls.forEach(({ el: e }) => e.classList.remove("lsv-event-active"));
    el.classList.add("lsv-event-active");
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
    }
  }

  // ── Live session (openLive) ──────────────────────────────────

  async function openLive(sessionId) {
    _close();
    _modal = _buildLiveModal(sessionId);
    document.body.appendChild(_modal);
    _modal.querySelector(".lsv-close").addEventListener("click", _close);
    _modal.addEventListener("click", e => { if (e.target === _modal) _close(); });
    document.addEventListener("keydown", _escHandler);

    // Pre-populate with existing events, then SSE for new ones
    let eventsFrom = 0, audioFrom = 0;
    try {
      const r = await fetch(`/api/live_sessions/${encodeURIComponent(sessionId)}`);
      if (r.ok) {
        const data = await r.json();
        eventsFrom = data.events_size || 0;
        audioFrom = data.audio_size || 0;
        // Show the latest frame immediately
        const frameImg = _modal.querySelector("#lsv-live-frame");
        if (frameImg && data.frame_count > 0) {
          frameImg.src = `/api/live_sessions/${encodeURIComponent(sessionId)}/frames/${data.frame_count - 1}`;
        }
        // Pre-populate feed with existing events so nothing is lost on reopen
        const feedEvents = (data.events || []).filter(e => e.type !== "frame" && e.type !== "instruction");
        if (feedEvents.length > 0) {
          const feedEl = _modal.querySelector("#lsv-feed");
          if (feedEl) {
            feedEl.innerHTML = _renderFeed(feedEvents, []);
            feedEl.lastElementChild?.scrollIntoView({ block: "nearest" });
          }
        }
      }
    } catch (e) { /* proceed with offset 0 */ }

    _startSSE(sessionId, eventsFrom);
    _startLiveAudio(sessionId, audioFrom);
  }

  function _buildLiveModal(sessionId) {
    const el = document.createElement("div");
    el.className = "lsv-overlay";
    el.innerHTML = `
      <div class="lsv-modal">
        <div class="lsv-header">
          <div class="lsv-header-left">
            <span class="lsv-title">live_ui</span>
            <span class="lsv-session-id">${_esc(sessionId.slice(0, 12))}</span>
            <span class="lsv-live-badge">&#9679; LIVE</span>
          </div>
          <button class="lsv-close">&times;</button>
        </div>
        <div class="lsv-body">
          <div class="lsv-feed" id="lsv-feed"></div>
          <div class="lsv-viewer" id="lsv-viewer">
            <div class="lsv-frame-box">
              <img id="lsv-live-frame" alt="Live frame" style="max-width:100%;display:block;">
            </div>
          </div>
        </div>
      </div>
    `;
    return el;
  }

  function _startSSE(sessionId, fromOffset) {
    const url = `/api/live_sessions/${encodeURIComponent(sessionId)}/events/stream`
      + (fromOffset ? `?from=${fromOffset}` : "");
    const es = new EventSource(url);
    _modal._es = es;
    _modal._sseLastId = fromOffset || 0;
    const feedEl = _modal.querySelector("#lsv-feed");
    const frameImg = _modal.querySelector("#lsv-live-frame");
    let latestFrameN = -1;

    es.onmessage = e => {
      // Track byte offset from SSE id field for reconnect
      if (e.lastEventId) _modal._sseLastId = parseInt(e.lastEventId) || 0;
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "frame") {
          latestFrameN = ev.n;
          if (frameImg) frameImg.src = `/api/live_sessions/${encodeURIComponent(sessionId)}/frames/${ev.n}`;
        } else if (ev.type !== "instruction") {
          const html = _renderFeed([ev], []);
          if (html && feedEl) {
            feedEl.insertAdjacentHTML("beforeend", html);
            feedEl.lastElementChild?.scrollIntoView({ block: "nearest" });
          }
        }
      } catch (e2) { /* ignore */ }
    };
    es.addEventListener("done", () => { es.close(); });
    es.onerror = () => {
      es.close();
      // Reconnect after 2s from last known offset (not from beginning)
      if (_modal && _modal._es === es) {
        const resumeFrom = _modal._sseLastId || 0;
        setTimeout(() => {
          if (_modal) _startSSE(sessionId, resumeFrom);
        }, 2000);
      }
    };
  }

  async function _startLiveAudio(sessionId, audioFrom) {
    let resp;
    try {
      const url = `/api/live_sessions/${encodeURIComponent(sessionId)}/audio/stream`
        + (audioFrom ? `?from=${audioFrom}` : "");
      resp = await fetch(url);
    } catch (e) { return; }
    if (!resp.ok || !resp.body) return;

    const ctx = new AudioContext({ sampleRate: 24000 });
    const reader = resp.body.getReader();
    _modal._audioCtx = ctx;
    _modal._audioReader = reader;

    let nextPlayAt = ctx.currentTime + 0.03;
    let leftover = new Uint8Array(0);

    (async () => {
      while (true) {
        let result;
        try { result = await reader.read(); } catch (e) { break; }
        if (result.done) break;
        const value = result.value;

        const combined = new Uint8Array(leftover.length + value.length);
        combined.set(leftover);
        combined.set(value, leftover.length);
        const samples = Math.floor(combined.length / 2);
        leftover = combined.slice(samples * 2);
        if (samples === 0) continue;

        const ab = ctx.createBuffer(1, samples, 24000);
        const ch = ab.getChannelData(0);
        const dv = new DataView(combined.buffer, 0, samples * 2);
        for (let i = 0; i < samples; i++) ch[i] = dv.getInt16(i * 2, true) / 32768;

        const src = ctx.createBufferSource();
        src.buffer = ab;
        src.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime, nextPlayAt);
        src.start(startAt);
        nextPlayAt = startAt + ab.duration;
      }
    })();
  }

  return { open, openLive };
})();
