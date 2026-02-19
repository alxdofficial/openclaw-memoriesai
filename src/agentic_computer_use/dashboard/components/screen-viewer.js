/* screen-viewer.js — Live MJPEG screen viewer per task */
"use strict";

const ScreenViewer = (() => {
  let _img = null;
  let _placeholder = null;
  let _currentTaskId = null;
  let _streamActive = false;

  function init(imgElement, placeholderElement) {
    _img = imgElement;
    _placeholder = placeholderElement;

    if (_img) {
      _img.addEventListener("load", () => {
        _img.classList.add("active");
        _streamActive = true;
      });
      _img.addEventListener("error", () => {
        _img.classList.remove("active");
        _streamActive = false;
      });
    }
  }

  function showTask(taskId) {
    if (taskId === _currentTaskId) return;
    _currentTaskId = taskId;

    if (!_img) return;

    if (taskId) {
      _img.src = `/api/tasks/${encodeURIComponent(taskId)}/screen`;
    } else {
      _img.src = "/api/screen";
    }
  }

  function showFullScreen() {
    _currentTaskId = null;
    if (!_img) return;
    _img.src = "/api/screen";
  }

  function stop() {
    _currentTaskId = null;
    _streamActive = false;
    if (_img) {
      _img.src = "";
      _img.classList.remove("active");
    }
  }

  function isActive() {
    return _streamActive;
  }

  return { init, showTask, showFullScreen, stop, isActive };
})();
