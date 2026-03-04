"""Near-realtime UI provider — screenshot → OpenRouter VLM → tool call → observe → repeat.

One action per turn: the model calls a single tool, sees a fresh screenshot of the result,
then decides the next action. No streaming audio. Works with any OpenRouter vision model
that supports function calling.
"""
import asyncio
import base64
import json
import logging

import httpx

from .. import config
from .. import debug as _dbg
from .actions import execute_action
from .base import LiveUIProvider

log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Seconds to wait after each GUI action before capturing the next screenshot.
# Gives the browser/OS time to process events and repaint.
_SETTLE = {
    "move_mouse":   0.0,   # smooth animation already ~180ms
    "click":        0.08,
    "double_click": 0.08,
    "type_text":    0.05,
    "key_press":    0.05,
    "scroll":       0.08,
}

_SYSTEM_PROMPT = """\
You control a {width}x{height} Linux desktop. Complete the instruction by calling tools. One tool call per turn.

Screen:
- The screen is exactly {width} pixels wide and {height} pixels tall. Coordinates range from (0, 0) at top-left to ({max_x}, {max_y}) at bottom-right. Any coordinate outside this range is rejected.
- Red ruler marks with numbers along all four edges label pixel positions every 100px. Do NOT guess coordinates — always read the nearest ruler numbers on the top and left edges and interpolate to get precise x and y values.
- The red circle with crosshairs marks the current cursor position.
- Each response includes a fresh screenshot labeled [current screenshot] showing the live state of the screen.

Actions:
- Always move_mouse to the center of a target element first. Check the screenshot to confirm the red circle is on the target, then click. The cursor is often NOT where you expect — if it missed, adjust with another move_mouse before proceeding.
- After every action, check the screenshot to verify what actually happened.
- Only call done() after the screenshot confirms the task is complete.
"""

_THOUGHT_PROP = {
    "thought": {"type": "string", "description": "Brief reasoning about what you see and will do next."},
}
_COORD_PROPS = {
    "x": {"type": "integer", "description": "Horizontal pixel position (0=left edge)"},
    "y": {"type": "integer", "description": "Vertical pixel position (0=top edge)"},
}

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_mouse",
            "description": "Move cursor to (x, y) without clicking. Use to verify aim before clicking.",
            "parameters": {
                "type": "object",
                "properties": {**_THOUGHT_PROP, **_COORD_PROPS},
                "required": ["thought", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click at (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    **_COORD_PROPS,
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
                "required": ["thought", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "double_click",
            "description": "Double-click at (x, y).",
            "parameters": {
                "type": "object",
                "properties": {**_THOUGHT_PROP, **_COORD_PROPS},
                "required": ["thought", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the focused input field.",
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
            "description": "Scroll at (x, y) in a direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_THOUGHT_PROP,
                    **_COORD_PROPS,
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Scroll steps (default 3)"},
                },
                "required": ["thought", "x", "y", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal task completion.",
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
            "description": "Signal that you cannot proceed and need human help.",
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


def _draw_ruler(img):
    """Draw tick marks along edges every 100px to help the model estimate coordinates."""
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    tick_len = 18
    color = (200, 0, 0, 200)
    label_color = (200, 0, 0, 230)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    for x in range(0, w, 100):
        draw.line([(x, 0), (x, tick_len)], fill=color, width=2)
        draw.line([(x, h - tick_len), (x, h)], fill=color, width=2)
        if x > 0:
            draw.text((x + 3, 1), str(x), fill=label_color, font=font)

    for y in range(0, h, 100):
        draw.line([(0, y), (tick_len, y)], fill=color, width=2)
        draw.line([(w - tick_len, y), (w, y)], fill=color, width=2)
        if y > 0:
            draw.text((2, y + 1), str(y), fill=label_color, font=font)

    return img


def _capture_jpeg_b64(display: str, session=None) -> str:
    """Capture screenshot with ruler overlay, record to session, return base64."""
    import io
    from PIL import Image
    from ..capture.screen import capture_screen_with_cursor
    try:
        frame = capture_screen_with_cursor(display=display)
        if frame is not None:
            img = Image.fromarray(frame)
            ratio = 1280 / max(img.size)
            if ratio < 1:
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.BILINEAR)
            img = img.convert("RGBA")
            img = _draw_ruler(img)
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            jpeg = buf.getvalue()
            if session:
                session.record_frame(jpeg)
            return base64.b64encode(jpeg).decode()
    except Exception as e:
        log.debug(f"Screenshot error: {e}")
    return ""


def _bounds_check(name: str, args: dict, width: int, height: int) -> str | None:
    """Return an error string if coordinates are out of bounds, else None."""
    if name in ("move_mouse", "click", "double_click", "scroll"):
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        if x < 0 or x >= width or y < 0 or y >= height:
            return f"error: ({x}, {y}) is outside the {width}x{height} display"
    return None


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


class OpenRouterVLMProvider(LiveUIProvider):
    """
    Near-realtime UI automation via OpenRouter VLM API.

    Loop: screenshot → chat completion with tools → execute tool call → repeat.
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
        width, height = _get_display_size(display)
        sid = session.id[:8] if session else "--------"

        system_text = _SYSTEM_PROMPT.format(width=width, height=height, max_x=width - 1, max_y=height - 1)
        if context:
            system_text += f"\n\nContext:\n{context}"

        _dbg.log("LIVE", f"[{sid}] OpenRouter near-realtime: model={model} display={display} {width}x{height} task={task_id} timeout={timeout}s")

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
        screenshot_b64 = await asyncio.get_running_loop().run_in_executor(
            None, _capture_jpeg_b64, display, session
        )
        user_content: list[dict] = [
            {"type": "text", "text": f"Instruction: {instruction}"},
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
                                "X-Title": "DETM live_ui",
                            },
                            json={
                                "model": model,
                                "messages": messages,
                                "tools": _TOOLS,
                                "tool_choice": "required",
                                "parallel_tool_calls": False,
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
                            _dbg.log("LIVE", f"[{sid}] done: success={success} actions={actions_taken} — {summary[:80]}")
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

                        # ── GUI action ───────────────────────────────────
                        action_turns += 1

                        # Bounds check
                        bounds_err = _bounds_check(fn_name, fn_args, width, height)
                        if bounds_err:
                            _dbg.log("LIVE", f"[{sid}] {bounds_err}")
                            if session:
                                session.record_tool_response(fn_name, tc_id, bounds_err)
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": bounds_err})
                            # No screenshot needed — nothing changed on screen
                            continue

                        # Execute
                        t_exec0 = asyncio.get_running_loop().time()
                        action_result = execute_action(fn_name, fn_args, display)
                        t_exec1 = asyncio.get_running_loop().time()
                        actions_taken += 1

                        # Track cursor position
                        if fn_name in ("move_mouse", "click", "double_click", "scroll"):
                            cursor_x = int(fn_args.get("x", 0))
                            cursor_y = int(fn_args.get("y", 0))

                        if session:
                            session.record_tool_response(fn_name, tc_id, action_result)

                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

                        # Settle + screenshot
                        settle = _SETTLE.get(fn_name, 0.08)
                        await asyncio.sleep(settle)

                        t_shot0 = asyncio.get_running_loop().time()
                        screenshot_b64 = await asyncio.get_running_loop().run_in_executor(
                            None, _capture_jpeg_b64, display, session
                        )
                        t_shot1 = asyncio.get_running_loop().time()
                        _dbg.log(
                            "LIVE",
                            f"[{sid}] timing: api={api_ms:.0f}ms exec={1000*(t_exec1-t_exec0):.0f}ms settle={settle*1000:.0f}ms shot={1000*(t_shot1-t_shot0):.0f}ms",
                        )

                        cursor_info = f" Cursor at ({cursor_x}, {cursor_y})." if cursor_x is not None else ""
                        observe_content: list[dict] = [
                            {"type": "text", "text": f"{action_result}.{cursor_info} [current screenshot]"},
                        ]
                        if screenshot_b64:
                            observe_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                            })
                        messages.append({"role": "user", "content": observe_content})

                        # Sliding window — keep system + first instruction + last N messages.
                        # Strip images from all but the latest user message to save tokens.
                        if len(messages) > CONTEXT_WINDOW + 2:
                            messages = messages[:2] + messages[-(CONTEXT_WINDOW):]
                        for msg in messages[2:-1]:  # skip system, instruction, and latest
                            if isinstance(msg.get("content"), list):
                                msg["content"] = [c for c in msg["content"] if c.get("type") != "image_url"]

                    if format_retries >= MAX_FORMAT_RETRIES:
                        _dbg.log("LIVE", f"[{sid}] max format retries — actions={actions_taken}")
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

                    _dbg.log("LIVE", f"[{sid}] max turns ({MAX_TURNS}) — actions={actions_taken}")
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
                _dbg.log("LIVE", f"[{sid}] timeout {timeout}s — actions={actions_taken}")
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
                log.error(f"OpenRouter live error: {e}", exc_info=True)
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
