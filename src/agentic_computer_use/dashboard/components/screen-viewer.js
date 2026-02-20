/* screen-viewer.js — Polled JPEG screen viewer per task */
"use strict";

const ScreenViewer = (() => {
  let _img = null;
  let _placeholder = null;
  let _currentTaskId = null;
  let _pollTimer = null;
  let _prevLiveBlobUrl = null;
  let _prevReplayBlobUrl = null;
  let _fetching = false;

  // Recording state
  let _recordBtn = null;
  let _recordTimer = null;
  let _recording = false;
  let _recordStart = 0;

  const POLL_MS = 500;

  // Replay state
  let _replayBtn = null;
  let _replayBar = null;
  let _replayMode = false;
  let _replayFrames = [];
  let _replayIndex = 0;

  function init(imgElement, placeholderElement) {
    _img = imgElement;
    _placeholder = placeholderElement;
  }

  function initRecordControls(containerEl) {
    if (!containerEl) return;

    _recordBtn = document.createElement("button");
    _recordBtn.className = "record-btn";
    _recordBtn.textContent = "Record";
    _recordBtn.addEventListener("click", _onRecordClick);
    containerEl.appendChild(_recordBtn);

    // Replay button
    _replayBtn = document.createElement("button");
    _replayBtn.className = "record-btn";
    _replayBtn.textContent = "Replay";
    _replayBtn.addEventListener("click", _onReplayClick);
    containerEl.appendChild(_replayBtn);

    // Replay scrubber (hidden until replay mode)
    _replayBar = document.createElement("div");
    _replayBar.className = "replay-bar hidden";
    _replayBar.innerHTML = `
      <button class="replay-nav" id="replay-prev">‹</button>
      <input type="range" id="replay-slider" min="0" value="0" step="1">
      <button class="replay-nav" id="replay-next">›</button>
      <span id="replay-label" class="replay-label">0 / 0</span>
      <button class="replay-nav" id="replay-close">✕ Live</button>
    `;
    containerEl.appendChild(_replayBar);

    _replayBar.querySelector("#replay-slider").addEventListener("input", (e) => {
      _replayIndex = parseInt(e.target.value);
      _showReplayFrame(_replayIndex);
      _updateReplayLabel();
    });
    _replayBar.querySelector("#replay-prev").addEventListener("click", () => _stepReplay(-1));
    _replayBar.querySelector("#replay-next").addEventListener("click", () => _stepReplay(1));
    _replayBar.querySelector("#replay-close").addEventListener("click", _exitReplay);
  }

  async function _onRecordClick() {
    if (!_currentTaskId) return;
    _recordBtn.disabled = true;
    if (_recording) {
      await App.apiPost(`/api/tasks/${encodeURIComponent(_currentTaskId)}/record/stop`);
      _setIdle();
    } else {
      const data = await App.apiPost(`/api/tasks/${encodeURIComponent(_currentTaskId)}/record/start`);
      if (data && data.ok) _setRecording();
    }
    _recordBtn.disabled = false;
  }

  function _setRecording() {
    _recording = true;
    _recordStart = Date.now();
    _recordBtn.className = "record-btn recording";
    _recordBtn.innerHTML = '<span class="record-dot"></span> Stop <span class="record-elapsed">0:00</span>';
    _recordTimer = setInterval(_updateElapsed, 1000);
  }

  function _setIdle() {
    _recording = false;
    if (_recordTimer) { clearInterval(_recordTimer); _recordTimer = null; }
    if (_recordBtn) {
      _recordBtn.className = "record-btn";
      _recordBtn.textContent = "Record";
    }
  }

  function _updateElapsed() {
    const secs = Math.floor((Date.now() - _recordStart) / 1000);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    const el = _recordBtn && _recordBtn.querySelector(".record-elapsed");
    if (el) el.textContent = `${m}:${s.toString().padStart(2, "0")}`;
  }

  async function _checkRecordStatus(taskId) {
    try {
      const resp = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/record/status`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.recording) {
        _recordStart = Date.now() - data.elapsed * 1000;
        _setRecording();
      } else {
        _setIdle();
      }
    } catch (e) {
      _setIdle();
    }
  }

  async function _onReplayClick() {
    if (!_currentTaskId) return;
    const resp = await fetch(`/api/tasks/${encodeURIComponent(_currentTaskId)}/frames`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.frames || data.frames.length === 0) {
      alert("No recorded frames yet for this task.");
      return;
    }
    _replayFrames = data.frames;
    _replayIndex = _replayFrames.length - 1;
    _enterReplay();
  }

  function _enterReplay() {
    _replayMode = true;
    _stopPoll();
    if (_replayBar) _replayBar.classList.remove("hidden");
    if (_replayBtn) _replayBtn.classList.add("active");
    const slider = _replayBar && _replayBar.querySelector("#replay-slider");
    if (slider) {
      slider.max = _replayFrames.length - 1;
      slider.value = _replayIndex;
    }
    _updateReplayLabel();
    _showReplayFrame(_replayIndex);
  }

  function _exitReplay() {
    _replayMode = false;
    if (_replayBar) _replayBar.classList.add("hidden");
    if (_replayBtn) _replayBtn.classList.remove("active");
    if (_prevReplayBlobUrl) { URL.revokeObjectURL(_prevReplayBlobUrl); _prevReplayBlobUrl = null; }
    if (_currentTaskId) {
      _fetchFrame();
      _pollTimer = setInterval(_fetchFrame, POLL_MS);
    }
  }

  function _stepReplay(delta) {
    _replayIndex = Math.max(0, Math.min(_replayFrames.length - 1, _replayIndex + delta));
    const slider = _replayBar && _replayBar.querySelector("#replay-slider");
    if (slider) slider.value = _replayIndex;
    _showReplayFrame(_replayIndex);
    _updateReplayLabel();
  }

  async function _showReplayFrame(idx) {
    if (!_currentTaskId || _replayFrames[idx] === undefined) return;
    const frameN = _replayFrames[idx];
    const url = `/api/tasks/${encodeURIComponent(_currentTaskId)}/frames/${frameN}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      if (_img) { _img.src = blobUrl; _img.classList.add("active"); }
      if (_prevReplayBlobUrl) URL.revokeObjectURL(_prevReplayBlobUrl);
      _prevReplayBlobUrl = blobUrl;
    } catch (e) { /* ignore */ }
  }

  function _updateReplayLabel() {
    const label = _replayBar && _replayBar.querySelector("#replay-label");
    if (label) label.textContent = `${_replayIndex + 1} / ${_replayFrames.length}`;
  }

  function _snapshotUrl(taskId) {
    return taskId
      ? `/api/tasks/${encodeURIComponent(taskId)}/snapshot`
      : `/api/snapshot`;
  }

  async function _fetchFrame() {
    if (_fetching || _replayMode) return; // skip if in-flight or replaying
    _fetching = true;
    const taskId = _currentTaskId;
    try {
      const resp = await fetch(_snapshotUrl(taskId));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);

      // Swap via offscreen Image to avoid flicker
      const tmp = new Image();
      tmp.onload = () => {
        // Discard if task changed or replay started while this was in flight
        if (_replayMode) { URL.revokeObjectURL(blobUrl); return; }
        if (_currentTaskId !== taskId && !(_currentTaskId === null && taskId === null)) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        if (_img) {
          _img.src = blobUrl;
          _img.classList.add("active");
        }
        if (_placeholder) _placeholder.textContent = "";
        if (_prevLiveBlobUrl) URL.revokeObjectURL(_prevLiveBlobUrl);
        _prevLiveBlobUrl = blobUrl;
      };
      tmp.onerror = () => URL.revokeObjectURL(blobUrl);
      tmp.src = blobUrl;
    } catch (e) {
      if (_placeholder) _placeholder.textContent = "Display unavailable";
      if (_img) _img.classList.remove("active");
    } finally {
      _fetching = false;
    }
  }

  function showTask(taskId) {
    _stopPoll();
    if (_replayMode) _exitReplay();
    _currentTaskId = taskId;
    _setIdle();
    if (taskId && _recordBtn) _checkRecordStatus(taskId);

    if (!_img) return;
    // Keep the previous frame visible until the new one arrives (no black flash).
    // Only show placeholder if there was nothing displayed before.
    if (!_img.classList.contains("active") && _placeholder) {
      _placeholder.textContent = "Loading…";
    }

    // null = system display (/api/snapshot), string = task display — both poll
    if (taskId !== undefined) {
      _fetchFrame();
      _pollTimer = setInterval(_fetchFrame, POLL_MS);
    }
  }

  function showFullScreen() {
    showTask(null);
  }

  function _stopPoll() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  function stop() {
    _stopPoll();
    _currentTaskId = null;
    _setIdle();
    if (_prevLiveBlobUrl) { URL.revokeObjectURL(_prevLiveBlobUrl); _prevLiveBlobUrl = null; }
    if (_prevReplayBlobUrl) { URL.revokeObjectURL(_prevReplayBlobUrl); _prevReplayBlobUrl = null; }
    if (_img) { _img.src = ""; _img.classList.remove("active"); }
  }

  function isActive() {
    return _img && _img.classList.contains("active");
  }

  return { init, initRecordControls, showTask, showFullScreen, stop, isActive };
})();
