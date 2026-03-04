"""Near-realtime UI provider — screenshot → OpenRouter VLM → tool call → observe → repeat.

One action per turn: the model calls a single tool, sees a fresh screenshot of the result,
then decides the next action. No streaming audio. Works with any OpenRouter vision model
that supports function calling (default: google/gemini-3.1-flash-lite-preview).
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

_SYSTEM_PROMPT = """\
You are a UI automation agent controlling a Linux desktop ({width}x{height} pixels).
Complete the given instruction by observing screenshots and calling tools.

COORDINATES: x and y are pixel coordinates.
  Top-left is (0, 0). Bottom-right is ({width}, {height}).
  The cursor appears as a RED CIRCLE with crosshair in every screenshot.

Rules:
- Call EXACTLY ONE tool per turn. After each action you will receive a fresh screenshot
  showing what actually happened — use it to decide your next action.
- NEVER call done() unless you have seen a screenshot confirming the task is complete.
- NEVER assume an action succeeded — always verify in the next screenshot.
- Use move_mouse first to see where the cursor lands; adjust x/y and repeat until
  the red circle sits on the target, then click.
- Click a text field before typing into it.
- Call escalate() for: login walls, CAPTCHAs, missing credentials, unexpected blockers.
- Act immediately — do not narrate or explain.
"""

_COORD_PROPS = {
    "x": {"type": "integer", "description": "X pixel coordinate"},
    "y": {"type": "integer", "description": "Y pixel coordinate"},
}

# OpenAI-format tool definitions (pixel coordinates — model sees screenshots directly)
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_mouse",
            "description": (
                "Move cursor to (x, y) WITHOUT clicking. Cursor appears as a red circle "
                "in the next screenshot. Repeat with adjusted x/y to home in on the target."
            ),
            "parameters": {
                "type": "object",
                "properties": _COORD_PROPS,
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Move mouse and click at (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    **_COORD_PROPS,
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
                "required": ["x", "y"],
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
                "properties": _COORD_PROPS,
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text at the focused input field. Click the field first.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key_press",
            "description": "Press a key or key combination: Return, Tab, Escape, BackSpace, ctrl+c, etc.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll at (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    **_COORD_PROPS,
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Scroll steps (default 3)"},
                },
                "required": ["x", "y", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the instruction has been completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What was accomplished"},
                    "success": {"type": "boolean"},
                },
                "required": ["summary", "success"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": (
                "Signal that you cannot proceed — human intervention needed. "
                "Use for: login walls, CAPTCHAs, missing credentials, unexpected blockers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
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


def _capture_jpeg_b64(display: str, session=None) -> str:
    """Capture screenshot, record to session, return base64-encoded JPEG."""
    from ..capture.screen import capture_screen_with_cursor, frame_to_jpeg
    try:
        frame = capture_screen_with_cursor(display=display)
        if frame is not None:
            jpeg = frame_to_jpeg(frame, max_dim=1280, quality=72)
            if session:
                session.record_frame(jpeg)
            return base64.b64encode(jpeg).decode()
    except Exception as e:
        log.debug(f"Screenshot error: {e}")
    return ""



class OpenRouterVLMProvider(LiveUIProvider):
    """
    Near-realtime UI automation via OpenRouter VLM API.

    Loop: screenshot → chat completion with tools → execute tool calls → repeat.
    No streaming audio; fastest round-trip is ~0.5–1.5s per turn.
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

        system_text = _SYSTEM_PROMPT.format(width=width, height=height)
        if context:
            system_text += f"\n\nAdditional context:\n{context}"

        _dbg.log("LIVE", f"[{sid}] OpenRouter near-realtime: model={model} task={task_id} timeout={timeout}s")

        MAX_TURNS = 40
        # Keep a sliding window of messages to prevent context overflow.
        # Each turn adds ~3 messages (assistant+tool+user with screenshot).
        # Keep system + first instruction + last CONTEXT_WINDOW messages.
        CONTEXT_WINDOW = 30  # ~10 turns of history
        actions_taken = 0
        last_usage_data = None  # track last API response for usage recording
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
                    for turn in range(MAX_TURNS):
                        t0 = asyncio.get_running_loop().time()
                        _dbg.log("LIVE", f"[{sid}] turn={turn + 1} → {model}")

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
                                "tool_choice": "auto",
                                "parallel_tool_calls": False,  # one action at a time
                            },
                        )

                        elapsed = (asyncio.get_running_loop().time() - t0) * 1000
                        if resp.status_code != 200:
                            err = f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}"
                            _dbg.log("LIVE", f"[{sid}] API error: {err}")
                            if session:
                                session.record_error(err)
                            return {"error": err, "success": False, "actions_taken": actions_taken}

                        data = resp.json()
                        last_usage_data = data
                        choice = data["choices"][0]
                        msg = choice["message"]
                        tool_calls = msg.get("tool_calls") or []

                        _dbg.log("LIVE", f"[{sid}] turn={turn + 1} {elapsed:.0f}ms {len(tool_calls)} tool call(s)")

                        # Capture reasoning/thinking if present
                        reasoning = msg.get("reasoning") or msg.get("thinking") or ""
                        if reasoning and session:
                            session.record_model_text(reasoning)
                            _dbg.log("LIVE", f"[{sid}] reasoning: {reasoning[:120]}")

                        if not tool_calls:
                            text = msg.get("content") or ""
                            _dbg.log("LIVE", f"[{sid}] text-only: {text[:200]}")
                            if text and session:
                                session.record_model_text(text)
                            messages.append({"role": "assistant", "content": text or ""})
                            messages.append({"role": "user", "content": "You must call a tool now."})
                            continue

                        # Enforce one tool call (parallel_tool_calls:false should guarantee this,
                        # but guard in case the model or proxy ignores it)
                        tc = tool_calls[0]
                        messages.append(msg)

                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except Exception:
                            fn_args = {}
                        tc_id = tc["id"]

                        _dbg.log("LIVE", f"[{sid}] tool_call: {fn_name}({', '.join(f'{k}={v}' for k, v in fn_args.items())})")

                        if session:
                            session.record_tool_call(fn_name, fn_args, tc_id)

                        # ── Terminal actions ──────────────────────────────────
                        if fn_name == "done":
                            success = bool(fn_args.get("success", True))
                            summary = str(fn_args.get("summary", ""))
                            if session:
                                session.record_done(success, summary)
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": "acknowledged"})
                            _dbg.log("LIVE", f"[{sid}] done: success={success} actions={actions_taken} — {summary[:80]}")
                            _record_usage(model, task_id, data)
                            return {
                                "success": success,
                                "summary": summary,
                                "escalated": False,
                                "escalation_reason": "",
                                "actions_taken": actions_taken,
                            }

                        if fn_name == "escalate":
                            reason = str(fn_args.get("reason", ""))
                            if session:
                                session.record_escalate(reason)
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": "acknowledged"})
                            _dbg.log("LIVE", f"[{sid}] escalate: {reason[:120]}")
                            _record_usage(model, task_id, data)
                            return {
                                "success": False,
                                "escalated": True,
                                "escalation_reason": reason,
                                "summary": f"Escalated: {reason}",
                                "actions_taken": actions_taken,
                            }

                        # ── GUI action — execute, then take a fresh screenshot ─
                        action_result = await asyncio.get_running_loop().run_in_executor(
                            None, execute_action, fn_name, fn_args, display
                        )
                        actions_taken += 1
                        if session:
                            session.record_tool_response(fn_name, tc_id, action_result)

                        # Tool result
                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

                        # Fresh screenshot so the model sees what actually happened
                        screenshot_b64 = await asyncio.get_running_loop().run_in_executor(
                            None, _capture_jpeg_b64, display, session
                        )
                        observe_content: list[dict] = [
                            {"type": "text", "text": f"Result: {action_result}. Here is the current screen. Take the next action."},
                        ]
                        if screenshot_b64:
                            observe_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                            })
                        messages.append({"role": "user", "content": observe_content})

                        # Sliding window: keep system msg + first instruction + last N messages
                        if len(messages) > CONTEXT_WINDOW + 2:
                            messages = messages[:2] + messages[-(CONTEXT_WINDOW):]

                    _dbg.log("LIVE", f"[{sid}] Max turns ({MAX_TURNS}) reached — actions={actions_taken}")
                    if last_usage_data:
                        _record_usage(model, task_id, last_usage_data)
                    return {
                        "success": False,
                        "summary": f"Reached max {MAX_TURNS} turns without completion",
                        "actions_taken": actions_taken,
                    }

            except asyncio.TimeoutError:
                _dbg.log("LIVE", f"[{sid}] Timeout after {timeout}s — actions={actions_taken}")
                if session:
                    session.record_error(f"timeout after {timeout}s")
                if last_usage_data:
                    _record_usage(model, task_id, last_usage_data)
                return {
                    "success": False,
                    "summary": f"Timed out after {timeout}s",
                    "error": f"timeout after {timeout}s",
                    "actions_taken": actions_taken,
                }
            except Exception as e:
                _dbg.log("LIVE", f"[{sid}] Exception: {e}")
                log.error(f"OpenRouter live error: {e}", exc_info=True)
                if session:
                    session.record_error(str(e))
                if last_usage_data:
                    _record_usage(model, task_id, last_usage_data)
                return {"error": str(e), "success": False, "actions_taken": actions_taken}


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
