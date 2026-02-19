/* screen-viewer.js — Live MJPEG screen viewer per task */
"use strict";

const ScreenViewer = (() => {
  let _img = null;
  let _placeholder = null;
  let _currentTaskId = null;
  let _streamActive = false;

  // Recording state
  let _recordBtn = null;
  let _recordTimer = null;
  let _recording = false;
  let _recordStart = 0;

  // Stream timeout state
  let _streamTimeout = null;

  function init(imgElement, placeholderElement) {
    _img = imgElement;
    _placeholder = placeholderElement;

    if (_img) {
      _img.addEventListener("load", () => {
        if (_streamTimeout) { clearTimeout(_streamTimeout); _streamTimeout = null; }
        _img.classList.add("active");
        _streamActive = true;
      });
      _img.addEventListener("error", () => {
        _img.classList.remove("active");
        _streamActive = false;
        if (_placeholder) _placeholder.textContent = "Display unavailable";
      });
    }
  }

  function initRecordControls(containerEl) {
    if (!containerEl) return;
    _recordBtn = document.createElement("button");
    _recordBtn.className = "record-btn";
    _recordBtn.textContent = "Record";
    _recordBtn.addEventListener("click", _onRecordClick);
    containerEl.appendChild(_recordBtn);
  }

  async function _onRecordClick() {
    if (!_currentTaskId) return;
    _recordBtn.disabled = true;

    if (_recording) {
      // Stop
      const data = await App.apiPost(`/api/tasks/${encodeURIComponent(_currentTaskId)}/record/stop`);
      _setIdle();
      _recordBtn.disabled = false;
    } else {
      // Start
      const data = await App.apiPost(`/api/tasks/${encodeURIComponent(_currentTaskId)}/record/start`);
      if (data && data.ok) {
        _setRecording();
      }
      _recordBtn.disabled = false;
    }
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

  function showTask(taskId) {
    _currentTaskId = taskId;

    // Update recording button state for new task
    _setIdle();
    if (taskId && _recordBtn) _checkRecordStatus(taskId);

    if (!_img) return;

    // Optimistically show the img and reset stream state
    _streamActive = false;
    _img.classList.remove("active");
    if (_placeholder) _placeholder.textContent = "Loading stream…";

    if (taskId) {
      _img.src = `/api/tasks/${encodeURIComponent(taskId)}/screen`;
    } else {
      _img.src = "/api/screen";
    }

    // 5-second timeout: show "Display unavailable" if no frame arrives
    if (_streamTimeout) clearTimeout(_streamTimeout);
    _streamTimeout = setTimeout(() => {
      _streamTimeout = null;
      if (!_streamActive && _placeholder) {
        _placeholder.textContent = "Display unavailable";
      }
    }, 5000);
  }

  function showFullScreen() {
    _currentTaskId = null;
    _setIdle();
    if (!_img) return;
    _img.src = "/api/screen";
  }

  function stop() {
    _currentTaskId = null;
    _streamActive = false;
    _setIdle();
    if (_img) {
      _img.src = "";
      _img.classList.remove("active");
    }
  }

  function isActive() {
    return _streamActive;
  }

  return { init, initRecordControls, showTask, showFullScreen, stop, isActive };
})();
