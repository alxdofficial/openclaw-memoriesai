/* usage-stats.js — AI API cost and token usage panel */
"use strict";

const UsageStats = (() => {
  let _container = null;
  let _pollTimer = null;
  let _since = "7";  // days
  let _taskId = null; // null = global view, string = per-task view

  const POLL_MS = 30000;  // refresh every 30s

  function init(container) {
    _container = container;
    _render({ rows: [], totals: { requests: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 }, daily: [] });
    _poll();
    _pollTimer = setInterval(_poll, POLL_MS);
  }

  function showTask(taskId) {
    _taskId = taskId || null;
    _poll();
  }

  async function _poll() {
    try {
      let url = `/api/usage/stats?since=${_since}`;
      if (_taskId) url += `&task_id=${encodeURIComponent(_taskId)}`;
      const r = await fetch(url);
      if (!r.ok) return;
      const data = await r.json();
      _render(data);
    } catch (e) {}
  }

  function _fmtNum(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
    return String(n);
  }

  function _fmtCost(usd) {
    if (usd === 0) return "$0.00";
    if (usd < 0.001) return "<$0.001";
    return "$" + usd.toFixed(usd < 0.01 ? 4 : usd < 1 ? 3 : 2);
  }

  function _esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function _render(data) {
    if (!_container) return;
    const { rows, totals, daily } = data;

    const titleText = _taskId ? `Task Usage (${_taskId.slice(0, 8)})` : "AI API Usage";
    const filterHtml = `
      <div class="usage-filter-row">
        <span class="usage-title">${titleText}</span>
        ${_taskId ? `<button class="usage-back-btn" title="Show global usage">← All</button>` : ""}
        <select class="usage-since-select"${_taskId ? ' style="display:none"' : ""}>
          <option value="1" ${_since==="1"?"selected":""}>Today</option>
          <option value="7" ${_since==="7"?"selected":""}>7 days</option>
          <option value="30" ${_since==="30"?"selected":""}>30 days</option>
          <option value="" ${_since===""?"selected":""}>All time</option>
        </select>
      </div>
    `;

    // Summary cards
    const cardsHtml = `
      <div class="usage-cards">
        <div class="usage-card">
          <div class="usage-card-val">${_fmtCost(totals.cost_usd)}</div>
          <div class="usage-card-label">Total cost</div>
        </div>
        <div class="usage-card">
          <div class="usage-card-val">${_fmtNum(totals.requests)}</div>
          <div class="usage-card-label">Requests</div>
        </div>
        <div class="usage-card">
          <div class="usage-card-val">${_fmtNum(totals.input_tokens)}</div>
          <div class="usage-card-label">Input tokens</div>
        </div>
        <div class="usage-card">
          <div class="usage-card-val">${_fmtNum(totals.output_tokens)}</div>
          <div class="usage-card-label">Output tokens</div>
        </div>
      </div>
    `;

    // Cost bar chart (daily) — only in global view
    const chartHtml = (!_taskId && daily.length > 0) ? _renderChart(daily) : "";

    // Per-model breakdown table
    const tableHtml = rows.length === 0
      ? '<div class="usage-empty">No usage recorded yet.</div>'
      : `<table class="usage-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th class="usage-num">Requests</th>
              <th class="usage-num">Input</th>
              <th class="usage-num">Output</th>
              <th class="usage-num">Cost</th>
              <th class="usage-bar-col"></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => _renderRow(r, totals.cost_usd)).join("")}
          </tbody>
        </table>`;

    _container.innerHTML = filterHtml + cardsHtml + chartHtml + tableHtml;

    const sinceSelect = _container.querySelector(".usage-since-select");
    if (sinceSelect) {
      sinceSelect.addEventListener("change", e => {
        _since = e.target.value;
        _poll();
      });
    }
    const backBtn = _container.querySelector(".usage-back-btn");
    if (backBtn) {
      backBtn.addEventListener("click", () => {
        _taskId = null;
        _poll();
      });
    }
  }

  function _renderRow(r, totalCost) {
    const pct = totalCost > 0 ? (r.cost_usd / totalCost * 100) : 0;
    const noTokens = r.input_tokens === 0 && r.output_tokens === 0;
    return `
      <tr>
        <td><span class="usage-provider usage-provider-${_esc(r.provider)}">${_esc(r.provider)}</span></td>
        <td class="usage-model">${_esc(r.model)}</td>
        <td class="usage-num">${_fmtNum(r.requests)}</td>
        <td class="usage-num">${noTokens ? "<span class='usage-dim'>—</span>" : _fmtNum(r.input_tokens)}</td>
        <td class="usage-num">${noTokens ? "<span class='usage-dim'>—</span>" : _fmtNum(r.output_tokens)}</td>
        <td class="usage-num usage-cost">${_fmtCost(r.cost_usd)}</td>
        <td class="usage-bar-col">
          <div class="usage-bar-track">
            <div class="usage-bar-fill" style="width:${pct.toFixed(1)}%"></div>
          </div>
        </td>
      </tr>
    `;
  }

  function _renderChart(daily) {
    const maxCost = Math.max(...daily.map(d => d.cost_usd), 0.000001);
    const bars = daily.map(d => {
      const h = Math.max(2, Math.round((d.cost_usd / maxCost) * 60));
      const label = d.date.slice(5); // MM-DD
      return `<div class="usage-day-col">
        <div class="usage-day-tip">${_esc(d.date)}<br>${_fmtCost(d.cost_usd)}<br>${_fmtNum(d.requests)} req</div>
        <div class="usage-day-bar" style="height:${h}px"></div>
        <div class="usage-day-label">${_esc(label)}</div>
      </div>`;
    }).join("");
    return `<div class="usage-chart">${bars}</div>`;
  }

  return { init, showTask };
})();
