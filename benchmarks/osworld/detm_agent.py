"""DETM agent adapter for OSWorld benchmark.

Bridges OSWorld's predict(instruction, obs) interface to our
Gemini supervisor + UI-TARS grounding pipeline.

Each predict() call:
1. Adds the new screenshot to the ongoing Gemini conversation
2. Makes ONE Gemini API call to get the next tool call
3. If Gemini calls move_to, runs UI-TARS grounding (with iterative narrowing)
4. Converts the action to a pyautogui string for OSWorld to execute

Conversation history persists across predict() calls within a task.
reset() clears history between tasks.

Usage from OSWorld's run script:
    from benchmarks.osworld.detm_agent import DETMAgent
    agent = DETMAgent()
    # ... plug into OSWorld's evaluation loop
"""
import asyncio
import base64
import io
import json
import logging
import os

import httpx
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# Default OSWorld VM resolution
_DEFAULT_WIDTH = 1920
_DEFAULT_HEIGHT = 1080


def _png_to_jpeg_b64(png_bytes: bytes, quality: int = 85) -> tuple[str, int, int]:
    """Convert PNG bytes to JPEG base64 string. Returns (b64, width, height)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode(), w, h


def _png_to_numpy(png_bytes: bytes) -> np.ndarray:
    """Decode raw PNG bytes to RGB numpy array."""
    return np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))


def _numpy_to_jpeg_b64(frame: np.ndarray, quality: int = 85) -> tuple[str, int, int]:
    """Convert numpy RGB array to JPEG base64 string. Returns (b64, width, height)."""
    img = Image.fromarray(frame)
    w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode(), w, h


class DETMAgent:
    """OSWorld-compatible agent wrapping DETM's Gemini + UI-TARS pipeline.

    Implements the OSWorld agent protocol:
        agent.reset()
        response, actions = agent.predict(instruction, obs)
        # actions = list of pyautogui code strings or "DONE"/"FAIL"/"WAIT"
    """

    action_space = "pyautogui"

    def __init__(self):
        self._messages: list[dict] = []
        self._instruction: str = ""
        self._cursor_x: int | None = None
        self._cursor_y: int | None = None
        self._screen_w: int = _DEFAULT_WIDTH
        self._screen_h: int = _DEFAULT_HEIGHT
        self._step: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        # Lazy imports to avoid circular deps at import time
        self._system_prompt = None
        self._tools = None
        self._api_key = None
        self._gemini_model = None

    def _ensure_init(self):
        """Lazy init — import DETM modules and config."""
        if self._system_prompt is not None:
            return
        from agentic_computer_use.live_ui.openrouter import _SYSTEM_PROMPT, _TOOLS
        from agentic_computer_use import config
        # Augment system prompt for autonomous benchmark mode (no OpenClaw planning layer)
        self._system_prompt = _SYSTEM_PROMPT + """
## Autonomous mode

You are operating autonomously — no human is guiding you. On your first action, outline a brief plan (3-5 steps) in your message text.

### Step costs

move_to is **free** — use it to position the cursor, then verify the overlay before acting.
click, type_text, key_press, and scroll each **cost one step**. You have a limited number of steps, so only commit an action when you're confident.

### Precision with zoom

When the cursor overlay lands close but not exactly on the target (e.g. wrong menu item, adjacent button), call move_to again with zoom=20 to zoom=50. This crops the screen around the cursor and re-grounds at higher resolution. Smaller zoom = more zoomed in = more precise.

### Prefer keyboard shortcuts and direct URLs

Keyboard shortcuts and URL bar navigation are faster and more reliable than clicking menus:
- **Browser address bar**: key_press(key="ctrl+l"), then type_text the URL and key_press(key="Return")
- **Browser settings pages**: navigate directly via URL (e.g. chrome://settings, about:preferences)
- **App launcher**: key_press(key="super"), then type_text the app name
- **Common shortcuts**: ctrl+a (select all), ctrl+c/v (copy/paste), ctrl+z (undo), ctrl+h (find/replace)

If clicking a UI element fails twice, switch to a keyboard-based approach.

### Text selection

To select a range of text, use drag(from_target="start of first paragraph", to_target="end of second paragraph"). This is the most reliable method.
For selecting all text: key_press(key="ctrl+a").
Note: type_text automatically selects and replaces existing text in the focused field.
"""
        self._tools = _TOOLS
        self._api_key = config.OPENROUTER_API_KEY
        self._gemini_model = config.OPENROUTER_LIVE_MODEL

    def reset(self, _logger=None, vm_ip=None, **kwargs):
        """Called before each new task. Clears conversation history."""
        self._ensure_init()
        self._messages = [{"role": "system", "content": self._system_prompt}]
        self._instruction = ""
        self._cursor_x = None
        self._cursor_y = None
        self._step = 0
        self._done_verified = False
        if self._loop is not None:
            # Force-close module-level httpx clients so they get recreated on new loop
            try:
                from agentic_computer_use.gui_agent.backends import uitars
                if uitars._or_client is not None:
                    self._loop.run_until_complete(uitars._or_client.aclose())
                    uitars._or_client = None
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def predict(self, instruction: str, obs: dict) -> tuple[str, list]:
        """Generate actions from a screenshot + instruction.

        Runs an internal loop: move_to calls are "free" (resolved internally
        with cursor overlay feedback to the supervisor). Only screen-changing
        actions (click, type, scroll, etc.) are returned to OSWorld as a step.

        Args:
            instruction: Natural language task description
            obs: {"screenshot": bytes (raw PNG), ...}

        Returns:
            (response_text, [pyautogui_code_string])
        """
        self._ensure_init()
        self._instruction = instruction
        self._step += 1

        # Decode screenshot
        screenshot_b64, self._screen_w, self._screen_h = _png_to_jpeg_b64(obs["screenshot"])

        # Build user message with screenshot
        if self._step == 1:
            user_content = [
                {"type": "text", "text": f"Instruction: {instruction} [current screenshot]"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
            ]
        else:
            user_content = [
                {"type": "text", "text": "[current screenshot]"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
            ]
        self._messages.append({"role": "user", "content": user_content})

        # Keep the raw frame for cursor overlay drawing
        current_frame = _png_to_numpy(obs["screenshot"])

        MAX_INTERNAL_ROUNDS = 8  # max internal rounds (move_to + redirected clicks) before forcing a return
        last_narration = ""
        internal_events = []  # capture internal loop for debug viewer

        for _internal_round in range(MAX_INTERNAL_ROUNDS + 1):
            # Context management: sliding window + strip old images
            self._trim_context()

            # Make one supervisor API call
            try:
                fn_name, fn_args, tc_id, narration = self._loop.run_until_complete(
                    self._gemini_call()
                )
            except Exception as e:
                log.error(f"Supervisor call failed: {e}")
                self._last_debug = {"tool": None, "args": {}, "thought": str(e), "result": "FAIL", "internal": internal_events}
                return str(e), ["FAIL"]

            last_narration = narration or last_narration

            if fn_name is None:
                # Add thinking as assistant message so context stays well-formed
                # (prevents consecutive user messages which confuse some models)
                if narration:
                    self._messages.append({"role": "assistant", "content": narration})
                self._last_debug = {"tool": None, "args": {}, "thought": narration, "result": "WAIT", "internal": internal_events}
                return narration or "", ["WAIT"]

            # Reject bad args — feed error back to model
            if fn_name == "__bad_args__":
                self._messages.append({
                    "role": "assistant", "content": narration or "",
                    "tool_calls": [{"id": tc_id, "function": {"name": "click", "arguments": "{}"}, "type": "function"}],
                })
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "error: could not parse tool arguments. Try again with valid JSON."})
                continue  # retry within internal loop

            # Log supervisor thought
            if narration:
                internal_events.append({"type": "supervisor", "thought": narration})

            # Append assistant message with tool call
            self._messages.append({
                "role": "assistant", "content": narration or "",
                "tool_calls": [{"id": tc_id, "function": {"name": fn_name, "arguments": json.dumps(fn_args)}, "type": "function"}],
            })

            # Terminal actions
            if fn_name == "done":
                if not fn_args.get("success"):
                    self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                    self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "FAIL", "internal": internal_events}
                    return last_narration, ["FAIL"]

                # Independent verification: fresh model with no shared context
                if not getattr(self, "_done_verified", False):
                    self._done_verified = True
                    verdict = self._loop.run_until_complete(
                        self._verify_done(obs["screenshot"])
                    )
                    if verdict["pass"]:
                        log.info(f"Verification PASSED: {verdict.get('reason', '')[:120]}")
                    else:
                        log.info(f"Verification FAILED: {verdict.get('reason', '')[:200]}")
                        # Inject rejection into task runner context so it knows what to fix
                        self._messages.append({"role": "tool", "tool_call_id": tc_id,
                            "content": f"Verification FAILED. The task is NOT complete. "
                                       f"Issue: {verdict.get('reason', 'unknown')}. "
                                       f"Continue working to fix this."})
                        continue  # let the task runner keep going

                self._done_verified = False
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "DONE", "internal": internal_events}
                return last_narration, ["DONE"]

            if fn_name == "escalate":
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "FAIL", "internal": internal_events}
                return last_narration, ["FAIL"]

            # move_to is resolved internally — cursor overlay fed back to supervisor
            if fn_name == "move_to":
                target = fn_args.get("target", "")
                hint = fn_args.get("hint")
                zoom = fn_args.get("zoom")
                internal_events.append({"type": "supervisor", "action": "move_to", "target": target, "hint": hint, "zoom": zoom})

                cursor_xy = (self._cursor_x, self._cursor_y) if self._cursor_x is not None else None
                result = self._loop.run_until_complete(
                    self._refine_cursor(target, current_frame, hint, zoom=zoom, cursor_xy=cursor_xy)
                )

                if result["ok"]:
                    self._cursor_x, self._cursor_y = result["x"], result["y"]
                    edge = result.get("edge_warning", "")
                    action_result = f"cursor moved to ({result['x']}, {result['y']}), ready to verify"
                    internal_events.append({"type": "grounding", "target": target, "x": result["x"], "y": result["y"], "edge": edge})

                    # Draw cursor overlay on the frame and feed back as a new screenshot
                    from agentic_computer_use.capture.screen import draw_cursor_overlay
                    annotated_frame = draw_cursor_overlay(current_frame.copy(), result["x"], result["y"])
                    annotated_b64, _, _ = _numpy_to_jpeg_b64(annotated_frame)

                    # Save annotated frame for debug viewer
                    if hasattr(self, '_result_dir') and self._result_dir:
                        overlay_path = os.path.join(self._result_dir, f"step_{self._step}_overlay_{_internal_round+1}.jpg")
                        Image.fromarray(annotated_frame).save(overlay_path, quality=80)
                        internal_events[-1]["overlay"] = os.path.basename(overlay_path)

                    self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})
                    self._messages.append({"role": "user", "content": [
                        {"type": "text", "text": "[screenshot with cursor overlay]"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{annotated_b64}"}},
                    ]})
                    log.info(f"Internal move_to round {_internal_round+1}: {target} -> ({result['x']}, {result['y']}){edge}")
                    continue  # let supervisor see the cursor and decide next action
                else:
                    action_result = f"error: {result['error']}"
                    internal_events.append({"type": "grounding", "target": target, "error": result["error"]})
                    self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})
                    continue

            # Intercept click/double_click with target — redirect to move_to internally
            if fn_name in ("click", "double_click") and fn_args.get("target"):
                target = fn_args.pop("target")
                hint = fn_args.pop("hint", None)
                internal_events.append({"type": "redirect", "action": fn_name, "target": target})
                error_msg = (
                    f"error: {fn_name}() has no 'target' parameter. "
                    f"I've moved the cursor to \"{target}\" for you — verify the cursor position in the screenshot, then call {fn_name}() without 'target'."
                )
                result = self._loop.run_until_complete(
                    self._refine_cursor(target, current_frame, hint)
                )
                if result["ok"]:
                    self._cursor_x, self._cursor_y = result["x"], result["y"]
                    internal_events.append({"type": "grounding", "target": target, "x": result["x"], "y": result["y"]})
                    from agentic_computer_use.capture.screen import draw_cursor_overlay
                    annotated_frame = draw_cursor_overlay(current_frame.copy(), result["x"], result["y"])
                    annotated_b64, _, _ = _numpy_to_jpeg_b64(annotated_frame)

                    # Save annotated frame for debug viewer
                    if hasattr(self, '_result_dir') and self._result_dir:
                        overlay_path = os.path.join(self._result_dir, f"step_{self._step}_overlay_{_internal_round+1}.jpg")
                        Image.fromarray(annotated_frame).save(overlay_path, quality=80)
                        internal_events[-1]["overlay"] = os.path.basename(overlay_path)

                    self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": error_msg})
                    self._messages.append({"role": "user", "content": [
                        {"type": "text", "text": "[screenshot with cursor overlay]"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{annotated_b64}"}},
                    ]})
                    log.info(f"Redirected {fn_name}(target=...) to internal move_to: {target} -> ({result['x']}, {result['y']})")
                    continue
                else:
                    internal_events.append({"type": "grounding", "target": target, "error": result["error"]})
                    self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": f"error: could not find '{target}'. Use move_to(target=...) to position cursor, then {fn_name}()."})
                    continue

            # Screen-changing actions — resolve and return to OSWorld
            action_result, pyautogui_actions = self._handle_action(fn_name, fn_args, obs)
            self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

            self._last_debug = {"tool": fn_name, "args": fn_args, "thought": last_narration, "result": action_result, "internal": internal_events}
            return last_narration, pyautogui_actions

        # Exhausted internal rounds without a screen-changing action
        log.warning(f"Exhausted {MAX_INTERNAL_ROUNDS} internal rounds without committing an action")
        self._last_debug = {"tool": "move_to", "args": {}, "thought": last_narration, "result": "WAIT", "internal": internal_events}
        return last_narration, ["WAIT"]

    def _trim_context(self):
        """Sliding window: keep system + first instruction + last N messages. Strip old images."""
        CONTEXT_WINDOW = 40  # increased to accommodate internal rounds
        if len(self._messages) > CONTEXT_WINDOW + 2:
            self._messages = self._messages[:2] + self._messages[-CONTEXT_WINDOW:]
        for m in self._messages[:-1]:
            if m.get("role") == "system":
                continue
            if isinstance(m.get("content"), list):
                m["content"] = [c for c in m["content"] if c.get("type") != "image_url"]

    async def _gemini_call(self) -> tuple[str | None, dict, str, str]:
        """Single-pass API call with native reasoning + tool call.

        Uses Gemini's native reasoning (``reasoning.effort=high``) so the model
        thinks internally before producing a structured tool call.  The reasoning
        text is returned via ``include_reasoning`` for logging/debugging.

        Returns (fn_name, fn_args, tool_call_id, narration).
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/openclaw/detm",
            "X-Title": "DETM OSWorld",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Retry up to 3 times if model returns text instead of tool call
            ACTION_RETRIES = 3
            action_messages = list(self._messages)  # shallow copy for retries
            for attempt in range(ACTION_RETRIES):
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self._gemini_model,
                        "messages": action_messages,
                        "tools": self._tools,
                        "tool_choice": "auto",
                        "reasoning": {"effort": "high"},
                        "include_reasoning": True,
                        "max_tokens": 2000,
                    },
                )

                if resp.status_code != 200:
                    raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")

                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]
                tool_calls = msg.get("tool_calls") or []

                # Extract reasoning from native reasoning field
                reasoning = (msg.get("reasoning") or "").strip()
                if reasoning:
                    log.info(f"THINKING: {reasoning}")

                if tool_calls:
                    break
                # No tool call — nudge and retry
                text_reply = (msg.get("content") or "").strip()
                log.info(f"Action pass returned text (attempt {attempt+1}/{ACTION_RETRIES}): {text_reply[:100]}")
                action_messages.append({"role": "assistant", "content": text_reply or ""})
                action_messages.append({"role": "user", "content": "You must call a tool. Choose the most appropriate tool for your next action."})

            narration = reasoning or (msg.get("content") or "").strip()

            if not tool_calls:
                log.warning(f"No tool call after {ACTION_RETRIES} retries")
                return None, {}, "", narration

            tc = tool_calls[0]
            fn_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]
            try:
                fn_args = json.loads(raw_args)
            except Exception:
                log.warning(f"Failed to parse tool arguments: {raw_args[:200]}")
                return "__bad_args__", {}, tc["id"], narration or f"bad arguments for {fn_name}"
            fn_args.pop("thought", None)
            log.debug(f"RAW RESPONSE: fn={fn_name} raw_args={raw_args[:200]}")

            return fn_name, fn_args, tc["id"], narration

    async def _verify_done(self, screenshot_png: bytes) -> dict:
        """Independent verification that the task is actually complete.

        Two-step process with a fresh model context (no shared history):
        1. Generate a checklist of visual criteria from the instruction.
        2. Check all criteria against the current screenshot.

        Returns {"pass": bool, "reason": str}.
        """
        screenshot_b64, _, _ = _png_to_jpeg_b64(screenshot_png)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/openclaw/detm",
            "X-Title": "DETM Verifier",
        }

        _VERIFIER_SYSTEM = (
            "You are an independent QA verifier for a desktop GUI automation agent. "
            "Your job is to check whether a task was actually completed correctly. "
            "You are skeptical by default — the agent often THINKS it succeeded but "
            "actually made subtle mistakes (wrong location, wrong value, incomplete action, "
            "dialog still open, etc). Look carefully for these common failure modes."
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            # --- Step 1: Generate checklist from instruction ---
            try:
                resp1 = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self._gemini_model,
                        "messages": [
                            {"role": "system", "content": _VERIFIER_SYSTEM},
                            {"role": "user", "content": (
                                f"The agent was given this task:\n\n"
                                f"\"{self._instruction}\"\n\n"
                                f"Generate a checklist of 3-5 specific, visually verifiable criteria "
                                f"that MUST be true on the screen for this task to be complete. "
                                f"Be concrete — reference specific UI elements, text, or states.\n\n"
                                f"Format: one criterion per line, numbered."
                            )},
                        ],
                        "max_tokens": 300,
                    },
                )
                if resp1.status_code != 200:
                    log.warning(f"Verifier checklist call failed: HTTP {resp1.status_code}")
                    return {"pass": True, "reason": "verifier checklist call failed, allowing"}

                checklist = (resp1.json()["choices"][0]["message"].get("content") or "").strip()
                log.info(f"VERIFY CHECKLIST: {checklist[:300]}")
            except Exception as e:
                log.warning(f"Verifier checklist error: {e}")
                return {"pass": True, "reason": f"verifier error: {e}"}

            # --- Step 2: Check criteria against screenshot ---
            try:
                resp2 = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self._gemini_model,
                        "messages": [
                            {"role": "system", "content": _VERIFIER_SYSTEM},
                            {"role": "user", "content": [
                                {"type": "text", "text": (
                                    f"Task: \"{self._instruction}\"\n\n"
                                    f"Checklist to verify:\n{checklist}\n\n"
                                    f"Examine the screenshot below and check each criterion. "
                                    f"For each, state PASS or FAIL with a brief reason.\n\n"
                                    f"Then give a final verdict on the LAST line:\n"
                                    f"VERDICT: PASS — if ALL criteria are met\n"
                                    f"VERDICT: FAIL — <reason> — if ANY criterion is not met"
                                )},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                            ]},
                        ],
                        "max_tokens": 500,
                    },
                )
                if resp2.status_code != 200:
                    log.warning(f"Verifier check call failed: HTTP {resp2.status_code}")
                    return {"pass": True, "reason": "verifier check call failed, allowing"}

                result = (resp2.json()["choices"][0]["message"].get("content") or "").strip()
                log.info(f"VERIFY RESULT: {result[:400]}")

                # Parse verdict from last line
                for line in reversed(result.split("\n")):
                    line = line.strip()
                    if line.upper().startswith("VERDICT:"):
                        verdict_text = line[len("VERDICT:"):].strip()
                        if verdict_text.upper().startswith("PASS"):
                            return {"pass": True, "reason": verdict_text}
                        else:
                            # Extract reason after "FAIL"
                            reason = verdict_text
                            if "—" in reason:
                                reason = reason.split("—", 1)[1].strip()
                            elif "-" in reason:
                                reason = reason.split("-", 1)[1].strip()
                            return {"pass": False, "reason": reason or "verification failed"}

                # No clear verdict found — be conservative, allow it
                log.warning(f"Verifier returned no clear verdict")
                return {"pass": True, "reason": "no clear verdict, allowing"}

            except Exception as e:
                log.warning(f"Verifier check error: {e}")
                return {"pass": True, "reason": f"verifier error: {e}"}

    def _handle_action(self, fn_name: str, fn_args: dict, obs: dict) -> tuple[str, list[str]]:
        """Convert a Gemini tool call to pyautogui action(s). Returns (result_text, [pyautogui_strings]).

        Note: move_to is handled in the internal loop inside predict(), not here.
        """

        if fn_name == "click":
            button = fn_args.get("button", "left")
            # Reject click with target — model must use move_to first
            if fn_args.get("target"):
                return "error: click() has no 'target' parameter. Use move_to(target=\"...\") first to position the cursor, verify the cursor overlay, then call click().", ["WAIT"]
            if self._cursor_x is None:
                return "error: no cursor position -- call move_to(target=...) first to position the cursor, then click()", ["WAIT"]
            # Combine moveTo + click into single action so it counts as one step
            x, y = self._cursor_x, self._cursor_y
            if button == "right":
                return "ok", [f"pyautogui.moveTo({x}, {y}); pyautogui.rightClick({x}, {y})"]
            return "ok", [f"pyautogui.moveTo({x}, {y}); pyautogui.click({x}, {y})"]

        if fn_name == "double_click":
            if self._cursor_x is None:
                return "error: no cursor position -- call move_to(target=...) first to position the cursor, then double_click()", ["WAIT"]
            x, y = self._cursor_x, self._cursor_y
            return "ok", [f"pyautogui.moveTo({x}, {y}); pyautogui.doubleClick({x}, {y})"]

        if fn_name == "type_text":
            text = fn_args.get("text", "")
            clear = fn_args.get("clear_first", True)
            if clear and self._cursor_x is not None:
                # Triple-click to select all text in the field, then type to replace
                x, y = self._cursor_x, self._cursor_y
                return "ok", [f"pyautogui.tripleClick({x}, {y}); import time; time.sleep(0.05); pyautogui.write({repr(text)}, interval=0.02)"]
            return "ok", [f"pyautogui.write({repr(text)}, interval=0.02)"]

        if fn_name == "key_press":
            key = fn_args.get("key", "Return")
            key_map = {
                "Return": "enter", "Enter": "enter",
                "Escape": "escape", "Tab": "tab",
                "Backspace": "backspace", "Delete": "delete",
                "Up": "up", "Down": "down", "Left": "left", "Right": "right",
                "Home": "home", "End": "end",
                "Page_Up": "pageup", "Page_Down": "pagedown",
            }
            if "+" in key:
                parts = [p.strip().lower() for p in key.split("+")]
                keys_str = ", ".join(f"'{p}'" for p in parts)
                return "ok", [f"pyautogui.hotkey({keys_str})"]
            mapped = key_map.get(key, key.lower())
            return "ok", [f"pyautogui.press('{mapped}')"]

        if fn_name == "scroll":
            direction = fn_args.get("direction", "down")
            amount = int(fn_args.get("amount", 3))
            clicks = -amount if direction == "down" else amount
            sx = self._cursor_x if self._cursor_x is not None else self._screen_w // 2
            sy = self._cursor_y if self._cursor_y is not None else self._screen_h // 2
            return "ok", [f"pyautogui.scroll({clicks}, {sx}, {sy})"]

        if fn_name == "drag":
            from_target = fn_args.get("from_target", "")
            to_target = fn_args.get("to_target", "")
            frame = _png_to_numpy(obs["screenshot"])
            from_result = self._loop.run_until_complete(self._refine_cursor(from_target, frame))
            if not from_result["ok"]:
                return f"error: could not find drag start: {from_result['error']}", ["WAIT"]
            to_result = self._loop.run_until_complete(self._refine_cursor(to_target, frame))
            if not to_result["ok"]:
                return f"error: could not find drag end: {to_result['error']}", ["WAIT"]
            return "ok", [f"pyautogui.moveTo({from_result['x']}, {from_result['y']}); pyautogui.dragTo({to_result['x']}, {to_result['y']}, duration=0.5)"]

        if fn_name == "mouse_down":
            button = fn_args.get("button", "left")
            if self._cursor_x is None:
                return "error: no cursor position -- call move_to first", ["WAIT"]
            return "ok", [f"pyautogui.mouseDown({self._cursor_x}, {self._cursor_y}, button='{button}')"]

        if fn_name == "mouse_up":
            button = fn_args.get("button", "left")
            return "ok", [f"pyautogui.mouseUp(button='{button}')"]

        log.warning(f"Unknown action: {fn_name}")
        return f"error: unknown action {fn_name}", ["WAIT"]

    async def _refine_cursor(
        self, target: str, frame: np.ndarray, hint: str | None = None,
        zoom: int | None = None, cursor_xy: tuple | None = None,
    ) -> dict:
        """Delegate to production _refine_cursor in benchmark mode.

        Passes frame= so it draws cursor overlay instead of capturing from X11.
        Full convergence loop runs: initial ground -> iterative narrow ->
        draw cursor -> re-ground with cursor_pos -> check convergence -> repeat.
        """
        from agentic_computer_use.live_ui.openrouter import _refine_cursor
        _save_dir = self._result_dir if hasattr(self, '_result_dir') and self._result_dir else None
        return await _refine_cursor(target, display="", frame=frame, hint=hint,
                                     zoom=zoom, cursor_xy=cursor_xy,
                                     save_dir=_save_dir, step_num=self._step)
