"""Unified GUI agent — Gemini supervisor + UI-TARS grounding.

Gemini Flash decides WHAT to do (reasoning, monitoring progress via screenshots).
UI-TARS decides WHERE on screen the target is (precise coordinate grounding).
UI-TARS only moves the cursor — Gemini controls all interactions (click, type, scroll)
after visually confirming cursor placement.

One action per turn: the model calls a single tool, sees a fresh screenshot,
then decides the next action.
"""
import asyncio
import base64
import json
import logging

import httpx

from .. import config
from .. import debug as _dbg
from .actions import execute_action, _smooth_mousemove
from .base import LiveUIProvider

log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Seconds to wait after each GUI action before capturing the next screenshot.
_SETTLE = {
    "move_to":      0.05,   # smooth animation already ~180ms + refinement
    "click":        0.08,
    "double_click": 0.08,
    "type_text":    0.05,
    "key_press":    0.05,
    "scroll":       0.08,
    "drag":         0.15,
    "mouse_down":   0.0,
    "mouse_up":     0.08,
}

_SYSTEM_PROMPT = """\
You control a Linux desktop. You see screenshots and call tools to interact.

CRITICAL — only describe what you ACTUALLY SEE:
- Before calling move_to, confirm the element EXISTS in the current screenshot.
- Do NOT assume UI elements are present based on what a typical desktop looks like.
- If the element you need is NOT visible on screen, say so in your thought and find an alternative (e.g., use the Applications menu, or key_press to open something).
- NEVER fabricate or guess element descriptions. If you can't see a browser icon, don't say "I see a browser icon."

Workflow:
- Use move_to(target="...") to position the cursor on a UI element. A specialized vision model finds and moves to the element precisely.
- After move_to, CHECK the screenshot: the red circle should be ON the target element.
- If the cursor is on the target, call click(), double_click(), or type_text() to interact.
- If the cursor missed, call move_to again with a more specific description.
- Use type_text after clicking an input field. Use key_press for keyboard shortcuts.
- Use scroll at the current cursor position. Move cursor to the scrollable area first if needed.

Writing good move_to targets (IMPORTANT — vague descriptions cause misclicks):
- ONLY describe elements you can see in the screenshot right now.
- Include the visible text label: move_to(target="button labeled 'Message' next to Chi-Hao Wu")
- Describe position relative to landmarks: move_to(target="the address bar at the top of the browser window")
- Describe appearance: move_to(target="the blue Send button in the bottom-right of the compose window")
- For repeated elements, distinguish them: move_to(target="the first 'Message' button, in the row with Chi-Hao Wu's profile photo")
- BAD: "the Firefox icon" (assuming it exists), "the browser icon" (when you can't see one)
- GOOD: "the folder icon labeled 'Home' in the taskbar" (describing what you actually see)

Screen:
- Red crosshair = current cursor position.
- You do NOT need to estimate coordinates. Just describe UI elements by their visual appearance and text labels.

If the target app is not open and you can't find its icon:
- Try: key_press(key="super") to open the app launcher, then type_text the app name
- Or: move_to(target="'Applications' menu in the top-left corner") and navigate the menu
- Do NOT repeatedly click random icons hoping one is the right app.

Completion rules:
- Call done(success=true) ONLY when the screenshot confirms the task is fully complete.
- Call done(success=false) if you've tried 3+ different approaches and none work.
- Call escalate() if you encounter: login walls, CAPTCHAs, unexpected errors, popups blocking progress, or anything outside your capability.
- If the same approach fails twice, try a different one.

Efficiency:
- "thought" field: 1-3 sentences max. What you see, what you'll do next.
- Don't repeat the same move_to description if it already worked.
"""

_THOUGHT_PROP = {
    "thought": {"type": "string", "description": "1-3 sentences: what you see, what you'll do. Keep it short."},
}

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_to",
            "description": "Move cursor to a UI element. Cursor appears as red circle. Verify placement in next screenshot before clicking.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "target": {"type": "string", "description": "Natural language description of the element to move to"},
                },
                "required": ["thought", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click at current cursor position.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "double_click",
            "description": "Double-click at current cursor position.",
            "parameters": {
                "type": "object",
                "properties": {**_THOUGHT_PROP},
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text. Click/move_to input field first.",
            "parameters": {
                "type": "object",
                "properties": {**_THOUGHT_PROP, "text": {"type": "string"}},
                "required": ["thought", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key_press",
            "description": "Press a key or combination: Return, Tab, Escape, BackSpace, ctrl+a, ctrl+c, etc.",
            "parameters": {
                "type": "object",
                "properties": {**_THOUGHT_PROP, "key": {"type": "string"}},
                "required": ["thought", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll at current cursor position.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Scroll steps (default 3)"},
                },
                "required": ["thought", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "description": "Drag from one element to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "from_target": {"type": "string", "description": "Element to drag from"},
                    "to_target": {"type": "string", "description": "Element to drag to"},
                },
                "required": ["thought", "from_target", "to_target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_down",
            "description": "Press and hold mouse button at current position.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_up",
            "description": "Release mouse button.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Task complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "summary": {"type": "string", "description": "What was accomplished"},
                    "success": {"type": "boolean"},
                },
                "required": ["thought", "summary", "success"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": "Cannot proceed, need help.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    "reason": {"type": "string"},
                },
                "required": ["thought", "reason"],
            },
        },
    },
]


# ── Display helpers ──────────────────────────────────────────────

def _get_display_size(display: str) -> tuple[int, int]:
    """Return (width, height) of the given display."""
    try:
        from ..display.manager import get_xlib_display
        xd = get_xlib_display(display)
        g = xd.screen().root.get_geometry()
        return g.width, g.height
    except Exception:
        from .. import config as _cfg
        return _cfg.DEFAULT_TASK_DISPLAY_WIDTH, _cfg.DEFAULT_TASK_DISPLAY_HEIGHT


def _capture_jpeg_b64(display: str, session=None) -> tuple[str, tuple[int, int] | None]:
    """Capture screenshot with cursor overlay. No resizing, no ruler.

    Returns (base64_jpeg, cursor_pos_in_display_pixels).
    Gemini doesn't need coordinates — UI-TARS handles grounding.
    The screenshot is sent at native resolution for Gemini to reason about visually.
    """
    import io
    from PIL import Image
    from ..capture.screen import capture_screen, get_mouse_position, draw_cursor_overlay
    try:
        frame = capture_screen(display=display)
        if frame is None:
            return "", None
        cursor_pos = get_mouse_position(display)
        if cursor_pos:
            frame = draw_cursor_overlay(frame, cursor_pos[0], cursor_pos[1])
        img = Image.fromarray(frame)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        jpeg = buf.getvalue()
        if session:
            session.record_frame(jpeg)
        return base64.b64encode(jpeg).decode(), cursor_pos
    except Exception as e:
        log.debug(f"Screenshot error: {e}")
    return "", None


def _extract_model_text(msg: dict) -> str:
    """Extract concise assistant text from an OpenRouter message."""
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    for key in ("reasoning", "thinking"):
        text = str(msg.get(key) or "").strip()
        if text:
            return text
    return ""


# ── UI-TARS grounding with iterative refinement ─────────────────

async def _refine_cursor(
    target: str,
    display: str,
    session=None,
    max_rounds: int = 3,
) -> dict:
    """Use UI-TARS to position cursor on a target element with iterative refinement.

    Flow:
    1. Capture screenshot -> UI-TARS grounds target -> get initial (x, y)
    2. Iterative narrow (RegionFocus): crop 300px around prediction, re-ground -> crop 150px, re-ground
    3. Move cursor to refined position (smooth animation, no click)
    4. Capture new screenshot with cursor overlay visible
    5. Re-ground same target on new screenshot -- if prediction shifted >30px, follow it
    6. Repeat steps 3-5 up to max_rounds times

    Returns: {"ok": True, "x": final_x, "y": final_y} or {"ok": False, "error": "..."}
    """
    from ..gui_agent.backends.uitars import UITARSBackend
    from ..gui_agent.agent import _iterative_narrow
    from ..capture.screen import capture_screen, capture_screen_with_cursor, frame_to_jpeg

    backend = UITARSBackend()
    loop = asyncio.get_event_loop()

    # Step 1: Initial grounding on clean screenshot
    frame = await loop.run_in_executor(None, capture_screen, display)
    if frame is None:
        return {"ok": False, "error": "Failed to capture screen"}

    jpeg = await loop.run_in_executor(None, frame_to_jpeg, frame)
    screen_h, screen_w = frame.shape[:2]

    grounding_model = config.UITARS_OPENROUTER_MODEL
    grounding = await backend.ground(target, jpeg, image_size=(screen_w, screen_h))
    if grounding is None:
        if session:
            session.record_grounding(target, grounding_model, 0, 0, round_n=0, error=f"Could not find: {target}")
        return {"ok": False, "error": f"Could not find: {target}"}

    # Step 2: Iterative narrow (RegionFocus-style crop zoom)
    grounding = await _iterative_narrow(backend, target, frame, grounding, loop)

    x, y = grounding.x, grounding.y
    if session:
        session.record_grounding(target, grounding_model, x, y, round_n=0)
    CONVERGE_THRESHOLD = 30  # pixels

    for round_n in range(max_rounds):
        # Step 3: Move cursor to predicted position
        await loop.run_in_executor(None, _smooth_mousemove, x, y, display)
        await asyncio.sleep(0.05)

        # Step 4: Capture screenshot with cursor overlay visible
        frame2 = await loop.run_in_executor(None, capture_screen_with_cursor, display)
        if frame2 is None:
            break
        jpeg2 = await loop.run_in_executor(None, frame_to_jpeg, frame2)

        # Step 5: Re-ground same target -- does UI-TARS agree with cursor placement?
        refined = await backend.ground(target, jpeg2, image_size=(screen_w, screen_h))
        if refined is None:
            break  # keep current position

        # Apply iterative narrow to the refinement too
        refined = await _iterative_narrow(backend, target, frame2, refined, loop)

        dx = abs(refined.x - x)
        dy = abs(refined.y - y)

        if dx < CONVERGE_THRESHOLD and dy < CONVERGE_THRESHOLD:
            log.debug(f"Cursor refinement converged after {round_n+1} rounds: ({x},{y}) delta=({dx},{dy})")
            if session:
                session.record_grounding(target, grounding_model, x, y, round_n=round_n+1, converged=True)
            break

        # Prediction shifted -- follow it
        log.debug(f"Cursor refinement round {round_n+1}: ({x},{y}) -> ({refined.x},{refined.y})")
        if session:
            session.record_grounding(target, grounding_model, refined.x, refined.y, round_n=round_n+1)
        x, y = refined.x, refined.y

    # Final move to ensure cursor is at the last position
    await loop.run_in_executor(None, _smooth_mousemove, x, y, display)

    return {"ok": True, "x": x, "y": y}


# ── Main provider ───────────────────────────────────────────────

class OpenRouterVLMProvider(LiveUIProvider):
    """
    Unified GUI agent: Gemini supervisor + UI-TARS grounding via OpenRouter.

    Loop: screenshot -> Gemini tool call -> execute (with UI-TARS for move_to) -> repeat.
    """

    async def run(
        self,
        instruction: str,
        timeout: int,
        task_id: str | None,
        display: str,
        context: str = "",
        session=None,
    ) -> dict:
        api_key = config.OPENROUTER_API_KEY
        if not api_key:
            return {"error": "OPENROUTER_API_KEY not set", "success": False, "actions_taken": 0}

        model = config.OPENROUTER_LIVE_MODEL
        disp_w, disp_h = _get_display_size(display)
        sid = session.id[:8] if session else "--------"

        system_text = _SYSTEM_PROMPT
        if context:
            system_text += f"\n\nContext:\n{context}"

        _dbg.log("LIVE", f"[{sid}] gui_agent: model={model} display={display} {disp_w}x{disp_h} task={task_id} timeout={timeout}s")

        import time as _time
        t_start = _time.time()

        MAX_TURNS = 60
        MAX_FORMAT_RETRIES = 10
        CONTEXT_WINDOW = 30
        actions_taken = 0
        action_turns = 0
        format_retries = 0
        last_usage_data = None
        cursor_x: int | None = None
        cursor_y: int | None = None
        messages: list[dict] = [{"role": "system", "content": system_text}]

        # First user message: instruction + initial screenshot
        screenshot_b64, init_cursor = await asyncio.get_running_loop().run_in_executor(
            None, _capture_jpeg_b64, display, session
        )
        if init_cursor:
            cursor_x, cursor_y = init_cursor
        user_content: list[dict] = [
            {"type": "text", "text": f"Instruction: {instruction} [current screenshot]"},
        ]
        if screenshot_b64:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
            })
        messages.append({"role": "user", "content": user_content})

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            try:
                async with asyncio.timeout(timeout):
                    while action_turns < MAX_TURNS and format_retries < MAX_FORMAT_RETRIES:
                        t0 = asyncio.get_running_loop().time()
                        turn_no = action_turns + format_retries + 1
                        _dbg.log("LIVE", f"[{sid}] turn={turn_no} actions={action_turns} retries={format_retries}")

                        resp = await client.post(
                            f"{_OPENROUTER_BASE}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                                "HTTP-Referer": "https://github.com/openclaw/detm",
                                "X-Title": "DETM gui_agent",
                            },
                            json={
                                "model": model,
                                "messages": messages,
                                "tools": _TOOLS,
                                "tool_choice": "required",
                                "parallel_tool_calls": False,
                                "max_tokens": 300,
                            },
                        )

                        t_api = asyncio.get_running_loop().time()
                        api_ms = (t_api - t0) * 1000
                        if resp.status_code != 200:
                            err = f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}"
                            _dbg.log("LIVE", f"[{sid}] API error: {err}")
                            if session:
                                session.record_error(err)
                            return {"error": err, "success": False, "actions_taken": actions_taken}

                        data = resp.json()
                        last_usage_data = data
                        _dbg.log("LIVE", f"[{sid}] raw_response: {json.dumps(data)[:2000]}")
                        choice = data["choices"][0]
                        msg = choice["message"]
                        tool_calls = msg.get("tool_calls") or []

                        if not tool_calls:
                            _dbg.log("LIVE", f"[{sid}] no tool call ({api_ms:.0f}ms)")
                            messages.append({"role": "assistant", "content": _extract_model_text(msg) or ""})
                            messages.append({"role": "user", "content": "Call a tool."})
                            format_retries += 1
                            continue

                        tc = tool_calls[0]
                        format_retries = 0
                        narration = _extract_model_text(msg)
                        messages.append({
                            "role": "assistant",
                            "content": narration or "",
                            "tool_calls": [tc],
                        })

                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except Exception:
                            fn_args = {}
                        tc_id = tc["id"]

                        thought = fn_args.pop("thought", "") or narration
                        if thought:
                            _dbg.log("LIVE", f"[{sid}] thought: {thought[:120]}")
                            if session:
                                session.record_model_text(thought)
                        _dbg.log("LIVE", f"[{sid}] {fn_name}({', '.join(f'{k}={v}' for k, v in fn_args.items())})")

                        if session:
                            session.record_tool_call(fn_name, fn_args, tc_id)

                        # ── Terminal actions ─────────────────────────────
                        if fn_name == "done":
                            success = bool(fn_args.get("success", True))
                            summary = str(fn_args.get("summary", ""))
                            if session:
                                session.record_done(success, summary)
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                            _dbg.log("LIVE", f"[{sid}] done: success={success} actions={actions_taken} -- {summary[:80]}")
                            _record_usage(model, task_id, data)
                            return {
                                "success": success,
                                "summary": summary,
                                "escalated": False,
                                "escalation_reason": "",
                                "actions_taken": actions_taken,
                                "session_id": session.id if session else "",
                                "elapsed_s": round(_time.time() - t_start, 1),
                            }

                        if fn_name == "escalate":
                            reason = str(fn_args.get("reason", ""))
                            if session:
                                session.record_escalate(reason)
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": "ok"})
                            _dbg.log("LIVE", f"[{sid}] escalate: {reason[:120]}")
                            _record_usage(model, task_id, data)
                            return {
                                "success": False,
                                "escalated": True,
                                "escalation_reason": reason,
                                "summary": f"Escalated: {reason}",
                                "actions_taken": actions_taken,
                                "session_id": session.id if session else "",
                                "elapsed_s": round(_time.time() - t_start, 1),
                            }

                        # ── GUI actions ──────────────────────────────────
                        action_turns += 1
                        action_result = "ok"

                        if fn_name == "move_to":
                            target_desc = fn_args.get("target", "")
                            result = await _refine_cursor(target_desc, display, session)
                            if result["ok"]:
                                action_result = f"ok -- cursor moved to ({result['x']}, {result['y']})"
                                cursor_x, cursor_y = result["x"], result["y"]
                            else:
                                action_result = f"error: {result['error']}"

                        elif fn_name == "click":
                            button = fn_args.get("button", "left")
                            if cursor_x is not None:
                                action_result = execute_action("click", {"x": cursor_x, "y": cursor_y, "button": button}, display)
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "double_click":
                            if cursor_x is not None:
                                action_result = execute_action("double_click", {"x": cursor_x, "y": cursor_y}, display)
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "type_text":
                            action_result = execute_action("type_text", {"text": fn_args.get("text", "")}, display)

                        elif fn_name == "key_press":
                            action_result = execute_action("key_press", {"key": fn_args.get("key", "Return")}, display)

                        elif fn_name == "scroll":
                            sx = cursor_x if cursor_x is not None else disp_w // 2
                            sy = cursor_y if cursor_y is not None else disp_h // 2
                            action_result = execute_action("scroll", {
                                "x": sx, "y": sy,
                                "direction": fn_args.get("direction", "down"),
                                "amount": fn_args.get("amount", 3),
                            }, display)

                        elif fn_name == "drag":
                            from_result = await _refine_cursor(fn_args.get("from_target", ""), display, session)
                            if not from_result["ok"]:
                                action_result = f"error: could not find drag start: {from_result['error']}"
                            else:
                                to_result = await _refine_cursor(fn_args.get("to_target", ""), display, session)
                                if not to_result["ok"]:
                                    action_result = f"error: could not find drag end: {to_result['error']}"
                                else:
                                    action_result = execute_action("drag", {
                                        "start_x": from_result["x"], "start_y": from_result["y"],
                                        "end_x": to_result["x"], "end_y": to_result["y"],
                                    }, display)

                        elif fn_name == "mouse_down":
                            button = fn_args.get("button", "left")
                            if cursor_x is not None:
                                action_result = execute_action("mouse_down", {"x": cursor_x, "y": cursor_y, "button": button}, display)
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "mouse_up":
                            button = fn_args.get("button", "left")
                            action_result = execute_action("mouse_up", {"button": button}, display)

                        else:
                            action_result = f"error: unknown tool {fn_name}"

                        actions_taken += 1

                        if session:
                            session.record_tool_response(fn_name, tc_id, action_result)

                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

                        # Settle + screenshot
                        settle = _SETTLE.get(fn_name, 0.08)
                        await asyncio.sleep(settle)

                        t_shot0 = asyncio.get_running_loop().time()
                        screenshot_b64, shot_cursor = await asyncio.get_running_loop().run_in_executor(
                            None, _capture_jpeg_b64, display, session
                        )
                        if shot_cursor:
                            cursor_x, cursor_y = shot_cursor
                        t_shot1 = asyncio.get_running_loop().time()
                        _dbg.log(
                            "LIVE",
                            f"[{sid}] timing: api={api_ms:.0f}ms settle={settle*1000:.0f}ms shot={1000*(t_shot1-t_shot0):.0f}ms",
                        )

                        observe_content: list[dict] = [
                            {"type": "text", "text": f"{action_result}. [current screenshot]"},
                        ]
                        if screenshot_b64:
                            observe_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                            })
                        messages.append({"role": "user", "content": observe_content})

                        # Sliding window -- keep system + first instruction + last N messages.
                        # Strip images from all but the latest user message to save tokens.
                        if len(messages) > CONTEXT_WINDOW + 2:
                            messages = messages[:2] + messages[-(CONTEXT_WINDOW):]
                        for m in messages[2:-1]:
                            if isinstance(m.get("content"), list):
                                m["content"] = [c for c in m["content"] if c.get("type") != "image_url"]

                    if format_retries >= MAX_FORMAT_RETRIES:
                        _dbg.log("LIVE", f"[{sid}] max format retries -- actions={actions_taken}")
                        if session:
                            session.record_error("max format retries")
                        if last_usage_data:
                            _record_usage(model, task_id, last_usage_data)
                        return {
                            "success": False,
                            "summary": "Model failed to produce tool calls",
                            "error": "max format retries",
                            "actions_taken": actions_taken,
                            "session_id": session.id if session else "",
                            "elapsed_s": round(_time.time() - t_start, 1),
                        }

                    _dbg.log("LIVE", f"[{sid}] max turns ({MAX_TURNS}) -- actions={actions_taken}")
                    if last_usage_data:
                        _record_usage(model, task_id, last_usage_data)
                    return {
                        "success": False,
                        "summary": f"Reached max {MAX_TURNS} turns without completion",
                        "actions_taken": actions_taken,
                        "session_id": session.id if session else "",
                        "elapsed_s": round(_time.time() - t_start, 1),
                    }

            except asyncio.TimeoutError:
                _dbg.log("LIVE", f"[{sid}] timeout {timeout}s -- actions={actions_taken}")
                if session:
                    session.record_error(f"timeout after {timeout}s")
                if last_usage_data:
                    _record_usage(model, task_id, last_usage_data)
                return {
                    "success": False,
                    "summary": f"Timed out after {timeout}s",
                    "error": f"timeout after {timeout}s",
                    "actions_taken": actions_taken,
                    "session_id": session.id if session else "",
                    "elapsed_s": round(_time.time() - t_start, 1),
                }
            except Exception as e:
                _dbg.log("LIVE", f"[{sid}] exception: {e}")
                log.error(f"gui_agent error: {e}", exc_info=True)
                if session:
                    session.record_error(str(e))
                if last_usage_data:
                    _record_usage(model, task_id, last_usage_data)
                return {"error": str(e), "success": False, "actions_taken": actions_taken, "session_id": session.id if session else "", "elapsed_s": round(_time.time() - t_start, 1)}


def _record_usage(model: str, task_id: str | None, data: dict) -> None:
    """Record token usage from OpenRouter response."""
    try:
        from .. import usage as _usage
        usage = data.get("usage", {})
        _usage.record_nowait(
            provider="openrouter",
            model=model,
            task_id=task_id,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            requests=1,
        )
    except Exception:
        pass
