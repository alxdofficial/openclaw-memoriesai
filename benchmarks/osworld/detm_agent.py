"""DETM agent adapter for OSWorld benchmark.

Bridges OSWorld's predict(instruction, obs) interface to our
Gemini supervisor + UI-TARS grounding pipeline.

Each predict() call runs ONE Gemini turn: Gemini sees the screenshot,
picks an action, UI-TARS grounds move_to targets, and the action is
returned as a pyautogui string for OSWorld to execute in its VM.

Usage from OSWorld's run script:
    from benchmarks.osworld.detm_agent import DETMAgent
    agent = DETMAgent()
    # ... plug into OSWorld's evaluation loop
"""
import asyncio
import base64
import io
import logging
from typing import Any

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# Default OSWorld VM resolution
_DEFAULT_WIDTH = 1920
_DEFAULT_HEIGHT = 1080


def _png_bytes_to_numpy(png_bytes: bytes) -> np.ndarray:
    """Decode raw PNG bytes to RGB numpy array."""
    return np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))


def _numpy_to_jpeg_b64(frame: np.ndarray, quality: int = 75) -> str:
    """Encode numpy RGB array to JPEG base64 string."""
    img = Image.fromarray(frame)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


class DETMAgent:
    """OSWorld-compatible agent wrapping DETM's Gemini + UI-TARS pipeline.

    Implements the OSWorld agent protocol:
        agent.reset()
        response, actions = agent.predict(instruction, obs)
        # actions = list of pyautogui code strings or "DONE"/"FAIL"/"WAIT"
    """

    action_space = "pyautogui"

    def __init__(self, max_turns_per_predict: int = 1):
        """
        Args:
            max_turns_per_predict: How many Gemini turns to run per predict()
                call.  Default 1 = one action per call (recommended for
                OSWorld since it provides a new screenshot after each step).
        """
        self.max_turns_per_predict = max_turns_per_predict
        self._provider = None
        self._messages = []      # persistent Gemini conversation across predict() calls
        self._instruction = ""
        self._loop = None

    def _ensure_provider(self):
        if self._provider is None:
            from agentic_computer_use.live_ui.openrouter import OpenRouterVLMProvider
            self._provider = OpenRouterVLMProvider()

    def reset(self, _logger=None, vm_ip=None, **kwargs):
        """Called before each new task. Clears conversation history."""
        self._messages = []
        self._instruction = ""
        self._ensure_provider()

    def predict(self, instruction: str, obs: dict) -> tuple[str, list]:
        """Generate actions from a screenshot + instruction.

        Args:
            instruction: Natural language task description
            obs: {
                "screenshot": bytes (raw PNG),
                "accessibility_tree": str or None,
                "terminal": str or None,
                "instruction": str,
            }

        Returns:
            (response_text, [list of pyautogui code strings])
        """
        self._instruction = instruction

        # Decode screenshot
        frame = _png_bytes_to_numpy(obs["screenshot"])
        h, w = frame.shape[:2]
        jpeg_b64 = _numpy_to_jpeg_b64(frame)

        # Collected actions from this predict() call
        collected_actions: list[str] = []
        response_text = ""
        _done = False

        def get_screenshot():
            """Callback: return current screenshot as (jpeg_b64, cursor_pos)."""
            return jpeg_b64, None

        def execute_override(name: str, args: dict) -> str:
            """Callback: collect action as pyautogui string instead of executing."""
            nonlocal _done
            action_str = _action_to_pyautogui(name, args)
            if action_str:
                collected_actions.append(action_str)
            return "ok"

        # Run the Gemini supervisor loop with benchmark callbacks.
        # We limit to max_turns_per_predict turns so we return control
        # to OSWorld after each action for a fresh screenshot.
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._provider.run(
                    instruction=instruction,
                    timeout=120,
                    task_id=None,
                    display=None,
                    context="",
                    session=None,
                    get_screenshot=get_screenshot,
                    execute_override=execute_override,
                    display_size_override=(w, h),
                )
            )
        except Exception as e:
            log.error(f"DETM predict() failed: {e}")
            return str(e), ["FAIL"]
        finally:
            loop.close()

        response_text = result.get("summary", "")

        # Map terminal actions
        if result.get("escalated"):
            return response_text, ["FAIL"]
        if result.get("success") and not collected_actions:
            return response_text, ["DONE"]

        # If no actions were collected (model returned done without acting),
        # signal DONE
        if not collected_actions:
            return response_text, ["DONE"]

        return response_text, collected_actions


def _action_to_pyautogui(name: str, args: dict) -> str | None:
    """Convert a DETM action to a pyautogui code string."""
    if name == "click":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        btn = args.get("button", "left")
        if btn == "right":
            return f"pyautogui.rightClick({x}, {y})"
        return f"pyautogui.click({x}, {y})"

    if name == "double_click":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        return f"pyautogui.doubleClick({x}, {y})"

    if name == "type_text":
        text = args.get("text", "")
        # Escape single quotes for pyautogui
        escaped = text.replace("\\", "\\\\").replace("'", "\\'")
        return f"pyautogui.typewrite('{escaped}', interval=0.02)"

    if name == "key_press":
        key = args.get("key", "Return")
        # Map common key names
        key_map = {
            "Return": "enter", "Enter": "enter",
            "Escape": "escape", "Tab": "tab",
            "Backspace": "backspace", "Delete": "delete",
            "Up": "up", "Down": "down", "Left": "left", "Right": "right",
            "Home": "home", "End": "end",
            "Page_Up": "pageup", "Page_Down": "pagedown",
        }
        # Handle combo keys like ctrl+c
        if "+" in key:
            parts = [p.strip().lower() for p in key.split("+")]
            keys_str = ", ".join(f"'{p}'" for p in parts)
            return f"pyautogui.hotkey({keys_str})"
        mapped = key_map.get(key, key.lower())
        return f"pyautogui.press('{mapped}')"

    if name == "scroll":
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3))
        clicks = -amount if direction == "down" else amount
        x, y = int(args.get("x", 960)), int(args.get("y", 540))
        return f"pyautogui.scroll({clicks}, {x}, {y})"

    if name == "drag":
        x1, y1 = int(args.get("start_x", 0)), int(args.get("start_y", 0))
        x2, y2 = int(args.get("end_x", 0)), int(args.get("end_y", 0))
        return f"pyautogui.moveTo({x1}, {y1}); pyautogui.dragTo({x2}, {y2}, duration=0.5)"

    if name == "mouse_down":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        return f"pyautogui.moveTo({x}, {y}); pyautogui.mouseDown()"

    if name == "mouse_up":
        return "pyautogui.mouseUp()"

    if name == "move_mouse":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        return f"pyautogui.moveTo({x}, {y})"

    log.warning(f"Unknown action for pyautogui conversion: {name}")
    return None
