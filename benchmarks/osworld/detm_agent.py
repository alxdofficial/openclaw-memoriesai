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
click, type_text, key_press, scroll, and wait each **cost one step**. You have a limited number of steps, so only commit an action when you're confident.
Use wait (1-5 seconds) when a page is loading or a spinner is visible — you'll receive a fresh screenshot after the delay. Don't wait unnecessarily.

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
                        self._done_verified = False  # reset so next done() also gets verified
                        continue  # let the task runner keep going

                self._done_verified = False
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "DONE", "internal": internal_events}
                return last_narration, ["DONE"]

            if fn_name == "escalate":
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "FAIL", "internal": internal_events}
                return last_narration, ["FAIL"]

            # wait — return to OSWorld which handles the actual delay + fresh screenshot
            if fn_name == "wait":
                wait_secs = min(max(float(fn_args.get("seconds", 2)), 0.5), 10)
                self._messages.append({"role": "tool", "tool_call_id": tc_id, "content": f"waited {wait_secs}s"})
                self._last_debug = {"tool": fn_name, "args": fn_args, "thought": narration, "result": "WAIT", "internal": internal_events}
                return last_narration, ["WAIT"]

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
        """Two-pass API call: thinking pass (text-only) then action pass (tool call).

        Pass 1: No tools — model freely analyzes the screenshot and reasons about next step.
        Pass 2: With tools — model's reasoning is injected as context, structured tool call requested.

        Returns (fn_name, fn_args, tool_call_id, narration).
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/openclaw/detm",
            "X-Title": "DETM OSWorld",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- Pass 1: Thinking (text-only, no tools) ---
            think_messages = self._messages + [
                {"role": "user", "content": (
                    "Before acting, think step by step:\n"
                    "1. What do you see on screen right now?\n"
                    "2. What is the current state relative to the task goal?\n"
                    "3. What specific action should you take next and why?\n"
                    "Be concise (2-4 sentences)."
                )},
            ]
            resp1 = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": self._gemini_model,
                    "messages": think_messages,
                    "max_tokens": 300,
                },
            )
            if resp1.status_code != 200:
                raise RuntimeError(f"OpenRouter HTTP {resp1.status_code} (thinking): {resp1.text[:300]}")

            think_data = resp1.json()
            thinking = (think_data["choices"][0]["message"].get("content") or "").strip()
            log.info(f"THINKING: {thinking[:200]}")

            # --- Pass 2: Action (with tools, thinking as assistant message) ---
            action_messages = list(self._messages)  # shallow copy
            if thinking:
                action_messages.append({"role": "assistant", "content": thinking})

            # Retry action pass up to 3 times if model returns text instead of tool call
            ACTION_RETRIES = 3
            for attempt in range(ACTION_RETRIES):
                resp2 = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self._gemini_model,
                        "messages": action_messages,
                        "tools": self._tools,
                        "tool_choice": "auto",
                        "max_tokens": 1000,
                    },
                )

                if resp2.status_code != 200:
                    raise RuntimeError(f"OpenRouter HTTP {resp2.status_code} (action): {resp2.text[:300]}")

                data = resp2.json()
                choice = data["choices"][0]
                msg = choice["message"]
                tool_calls = msg.get("tool_calls") or []

                if tool_calls:
                    break
                # No tool call — nudge and retry
                text_reply = (msg.get("content") or "").strip()
                log.info(f"Action pass returned text (attempt {attempt+1}/{ACTION_RETRIES}): {text_reply[:100]}")
                action_messages.append({"role": "assistant", "content": text_reply or ""})
                action_messages.append({"role": "user", "content": "You must call a tool. Choose the most appropriate tool for your next action."})

            action_narration = (msg.get("content") or "").strip()
            narration = thinking or action_narration

            if not tool_calls:
                log.warning(f"Action pass returned no tool call after {ACTION_RETRIES} retries")
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
            "Your job is to check whether a task was completed correctly based on "
            "what is visible on screen. You must be EVIDENCE-BASED: only fail a "
            "criterion when you see POSITIVE evidence of failure (wrong value, "
            "error message, wrong page, dialog still open, etc). "
            "If the expected outcome is not visible on screen (e.g. a file was saved, "
            "a setting was changed in a background process, a shortcut was created), "
            "and there is no evidence of failure, mark the criterion as PASSED. "
            "Absence of visual confirmation is NOT evidence of failure."
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
                                f"Generate a checklist of 2-4 specific criteria that should be true "
                                f"for this task to be complete. For each criterion, tag it:\n"
                                f"  [VISUAL] — can be confirmed by looking at the screen\n"
                                f"  [HIDDEN] — involves saved files, changed settings, or background state\n\n"
                                f"Be SPECIFIC: reference the exact values, names, or states from the "
                                f"instruction. Bad: 'a webpage with forms'. Good: 'a list of Civil "
                                f"Division forms is displayed'. Bad: 'correct settings'. Good: 'the "
                                f"search engine is set to Bing'.\n\n"
                                f"Only include criteria directly required by the instruction — do NOT "
                                f"add extra verification steps the instruction didn't ask for.\n\n"
                                f"Format: one criterion per line, numbered, with tag."
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

            # --- Step 2: Check criteria against screenshot via tool call ---
            _verdict_tool = {
                "type": "function",
                "function": {
                    "name": "verdict",
                    "description": "Submit your verification verdict after checking the criteria.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "criteria_results": {
                                "type": "array",
                                "description": "Result for each checklist criterion, in order.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "criterion": {"type": "string", "description": "The criterion being checked"},
                                        "pass": {"type": "boolean", "description": "true if criterion is met, false if not"},
                                        "evidence": {"type": "string", "description": "Brief visual evidence from the screenshot"},
                                    },
                                    "required": ["criterion", "pass", "evidence"],
                                },
                            },
                            "overall_pass": {"type": "boolean", "description": "true only if ALL criteria pass"},
                            "failure_reason": {"type": "string", "description": "If overall_pass is false, explain what failed. Empty string if pass."},
                        },
                        "required": ["criteria_results", "overall_pass", "failure_reason"],
                    },
                },
            }
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
                                    f"Examine the screenshot and check ONLY the numbered criteria above.\n"
                                    f"- For [VISUAL] criteria: check if the screen shows the expected state.\n"
                                    f"- For [HIDDEN] criteria: PASS unless you see positive evidence of "
                                    f"failure (error message, wrong state, command that clearly failed).\n"
                                    f"- Absence of visual confirmation is NOT a failure.\n"
                                    f"Call the verdict tool with your results."
                                )},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                            ]},
                        ],
                        "tools": [_verdict_tool],
                        "tool_choice": {"type": "function", "function": {"name": "verdict"}},
                        "reasoning": {"effort": "high"},
                        "max_tokens": 2000,
                    },
                )
                if resp2.status_code != 200:
                    log.warning(f"Verifier check call failed: HTTP {resp2.status_code}")
                    return {"pass": True, "reason": "verifier check call failed, allowing"}

                data2 = resp2.json()
                msg2 = data2["choices"][0]["message"]
                tool_calls = msg2.get("tool_calls") or []
                if not tool_calls:
                    # Retry once — nudge the model to use the tool
                    log.warning(f"Verifier returned no tool call, retrying")
                    retry_resp = await client.post(
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
                                        f"You must call the verdict tool with your results."
                                    )},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                                ]},
                            ],
                            "tools": [_verdict_tool],
                            "tool_choice": {"type": "function", "function": {"name": "verdict"}},
                            "max_tokens": 1000,
                        },
                    )
                    if retry_resp.status_code == 200:
                        retry_data = retry_resp.json()
                        retry_tc = retry_data["choices"][0]["message"].get("tool_calls") or []
                        if retry_tc:
                            tool_calls = retry_tc
                    if not tool_calls:
                        log.warning(f"Verifier returned no tool call after retry")
                        return {"pass": True, "reason": "verifier returned no tool call, allowing"}

                verdict_args = json.loads(tool_calls[0]["function"]["arguments"])
                log.info(f"VERIFY RESULT: {json.dumps(verdict_args, indent=2)[:400]}")

                overall = verdict_args.get("overall_pass", True)
                reason = verdict_args.get("failure_reason", "")
                return {"pass": overall, "reason": reason}

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
