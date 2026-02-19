/* message-feed.js — Color-coded task message feed */
"use strict";

const MessageFeed = (() => {
  let _container = null;
  let _messages = [];

  const DIR_MAP = {
    "text:agent":       { css: "msg-agent",     dir: "LLM \u2192 task" },
    "progress:agent":   { css: "msg-agent",     dir: "LLM \u2192 task" },
    "text:user":        { css: "msg-user",      dir: "User \u2192 LLM" },
    "wait:*":           { css: "msg-wait",      dir: "SmartWait \u2192 task" },
    "lifecycle:*":      { css: "msg-lifecycle",  dir: "System \u2192 task" },
    "progress:system":  { css: "msg-progress",  dir: "System \u2192 task" },
    "plan:*":           { css: "msg-plan",      dir: "LLM \u2192 plan" },
    "stuck:*":          { css: "msg-error",     dir: "System \u2192 alert" },
  };

  function _classify(msg) {
    const key1 = `${msg.msg_type}:${msg.role}`;
    if (DIR_MAP[key1]) return DIR_MAP[key1];
    const key2 = `${msg.msg_type}:*`;
    if (DIR_MAP[key2]) return DIR_MAP[key2];
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

  function _esc(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  function init(container) {
    _container = container;
  }

  function setMessages(messages) {
    _messages = messages || [];
    render();
  }

  function clear() {
    _messages = [];
    if (_container) _container.innerHTML = "";
  }

  function render() {
    if (!_container) return;

    const atBottom = _container.scrollHeight - _container.scrollTop - _container.clientHeight < 60;

    let html = "";
    for (const msg of _messages) {
      const info = _classify(msg);
      html += `<div class="msg-entry ${info.css}">`;
      html += `<div class="msg-header">`;
      html += `<span class="msg-direction">${_esc(info.dir)}</span>`;
      html += `<span class="msg-time">${_formatTime(msg.created_at)}</span>`;
      html += `</div>`;
      html += `<div class="msg-content">${_esc(msg.content)}</div>`;
      html += `</div>`;
    }

    _container.innerHTML = html;

    // Only scroll to bottom if user was already near the bottom
    if (atBottom) _container.scrollTop = _container.scrollHeight;
  }

  return { init, setMessages, clear, render };
})();
