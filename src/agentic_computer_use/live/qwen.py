"""Qwen3-Omni Realtime provider — OpenAI Realtime-compatible WebSocket protocol."""
import asyncio
import base64
import json
import logging
import time

import aiohttp

from .. import config
from .actions import execute_action
from .base import LiveUIProvider

log = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = """\
You are a UI automation agent controlling a Linux desktop ({width}x{height} pixels).
Your job: complete the given instruction by observing screenshots and taking actions.
All coordinates must be integers in the range x=[0,{width}], y=[0,{height}].

Available tools:
  move_mouse(x, y)                    — move cursor WITHOUT clicking (for verification)
  click(x, y, button="left")          — move mouse and click
  double_click(x, y)                  — double-click
  type_text(text)                     — type text (click the field first!)
  key_press(key)                      — key combo: Return, Tab, Escape, BackSpace,
                                        ctrl+c, ctrl+v, ctrl+a, ctrl+z, alt+Tab, etc.
  scroll(x, y, direction, amount=3)   — scroll at position. direction: up/down/left/right
  done(summary, success)              — call when the instruction is complete
  escalate(reason)                    — call when you cannot proceed

Cursor precision — follow this workflow before EVERY click:
1. Call move_mouse(x, y) to position the cursor at your intended target.
2. Observe the next screenshot — the cursor is shown as a RED CIRCLE with crosshair.
3. Check: is the red circle centred on the correct element?
   - YES → call click(x, y) (same coordinates).
   - NO  → call move_mouse(x2, y2) with corrected coordinates, re-check, then click.
Repeat the move/check cycle up to 3 times until the cursor is precisely placed.

General rules:
- Study each screenshot carefully before acting.
- Click a text field before typing into it.
- After each action, wait for the next screenshot to verify the result.
- Call done() as soon as the instruction is fully complete.
- Call escalate() for: login/CAPTCHA prompts, unexpected blocking dialogs,
  missing credentials, or anything requiring human intervention.
"""

_TOOLS = [
    {
        "type": "function",
        "name": "move_mouse",
        "description": (
            "Move the cursor to screen coordinates WITHOUT clicking. "
            "Use this to verify cursor position before clicking — the cursor "
            "will appear as a red circle in the next screenshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X pixel coordinate"},
                "y": {"type": "integer", "description": "Y pixel coordinate"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "type": "function",
        "name": "click",
        "description": "Move mouse and click at screen coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X pixel coordinate"},
                "y": {"type": "integer", "description": "Y pixel coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
            },
            "required": ["x", "y"],
        },
    },
    {
        "type": "function",
        "name": "double_click",
        "description": "Double-click at screen coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "type": "function",
        "name": "type_text",
        "description": "Type text at the focused input field. Click the field first.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "type": "function",
        "name": "key_press",
        "description": "Press a key or key combination.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Return, Tab, Escape, BackSpace, ctrl+c, ctrl+v, etc.",
                },
            },
            "required": ["key"],
        },
    },
    {
        "type": "function",
        "name": "scroll",
        "description": "Scroll at screen coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "Scroll steps (default 3)"},
            },
            "required": ["x", "y", "direction"],
        },
    },
    {
        "type": "function",
        "name": "done",
        "description": "Signal that the instruction has been completed.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "success": {"type": "boolean"},
            },
            "required": ["summary", "success"],
        },
    },
    {
        "type": "function",
        "name": "escalate",
        "description": (
            "Signal you cannot proceed and need human intervention. "
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
]


class QwenOmniProvider(LiveUIProvider):
    """Qwen3-Omni Realtime provider via OpenAI Realtime-compatible WebSocket."""

    async def run(
        self,
        instruction: str,
        timeout: int,
        task_id: str | None,
        display: str,
        context: str = "",
        session=None,
    ) -> dict:
        api_key = config.DASHSCOPE_API_KEY
        if not api_key:
            return {"error": "DASHSCOPE_API_KEY not set — add it to your .env file"}

        model = config.QWEN_LIVE_MODEL
        url = f"{config.QWEN_LIVE_ENDPOINT}?model={model}"
        headers = {"Authorization": f"Bearer {api_key}"}

        width, height = _get_display_size(display)
        system_text = _SYSTEM_INSTRUCTION.format(width=width, height=height)
        if context:
            system_text += f"\n\nAdditional context:\n{context}"

        log.info(f"Connecting to Qwen Omni Realtime: {url}")

        actions_taken = 0
        terminal_result: dict | None = None

        try:
            async with asyncio.timeout(timeout):
                async with aiohttp.ClientSession() as http:
                    async with http.ws_connect(url, headers=headers, heartbeat=20.0) as ws:
                        log.info("Qwen Omni session connected")

                        # Wait for session.created
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("type") == "session.created":
                                    log.info("session.created received")
                                    break
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                return {"error": f"WS closed before session.created: {msg.data!r}"}

                        # ── 1. session.update — system prompt + tools ────
                        await ws.send_str(json.dumps({
                            "type": "session.update",
                            "session": {
                                "modalities": ["text", "audio"],
                                "instructions": system_text,
                                "tools": _TOOLS,
                                "tool_choice": "required",
                            },
                        }))

                        # Wait for session.updated before sending messages
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("type") == "session.updated":
                                    log.info("session.updated — ready")
                                    break
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                return {"error": "WS closed waiting for session.updated"}

                        # ── 2. Main loop: capture → send → act ──────────
                        MAX_TURNS = 40
                        MAX_EMPTY_TURNS = 5
                        empty_turns = 0
                        first_turn = True
                        for _turn_n in range(MAX_TURNS):
                            # Capture current screen state
                            frame_b64 = _capture_frame(display, session)

                            # Build user message content
                            content = []
                            if frame_b64:
                                content.append({
                                    "type": "input_image",
                                    "image": frame_b64,
                                })
                            if first_turn:
                                content.append({
                                    "type": "input_text",
                                    "text": (
                                        f"TASK: {instruction}\n\n"
                                        "You MUST call one of the provided tools. "
                                        "Do NOT respond in text — only use tool calls."
                                    ),
                                })
                                first_turn = False
                            else:
                                content.append({
                                    "type": "input_text",
                                    "text": (
                                        "Here is the updated screen after your last action. "
                                        "You MUST call the next tool. Do NOT respond in text."
                                    ),
                                })

                            await ws.send_str(json.dumps({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": content,
                                },
                            }))
                            await ws.send_str(json.dumps({
                                "type": "response.create",
                                "response": {"tool_choice": "required"},
                            }))

                            # ── Collect response ─────────────────────────
                            pending_calls: dict[str, dict] = {}  # call_id → {name, args_buf}
                            completed_calls: list[dict] = []

                            async for msg in ws:
                                if msg.type != aiohttp.WSMsgType.TEXT:
                                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                        log.warning(f"WS closed mid-response: {msg.data!r}")
                                    continue

                                data = json.loads(msg.data)
                                ev = data.get("type", "")

                                if ev == "response.output_item.added":
                                    item = data.get("item", {})
                                    if item.get("type") == "function_call":
                                        call_id = item.get("call_id", "")
                                        name = item.get("name", "")
                                        pending_calls[call_id] = {"name": name, "args_buf": ""}
                                        log.debug(f"function_call started: {name} ({call_id})")

                                elif ev == "response.function_call_arguments.delta":
                                    call_id = data.get("call_id", "")
                                    if call_id in pending_calls:
                                        pending_calls[call_id]["args_buf"] += data.get("delta", "")

                                elif ev == "response.function_call_arguments.done":
                                    call_id = data.get("call_id", "")
                                    if call_id in pending_calls:
                                        entry = pending_calls.pop(call_id)
                                        try:
                                            args = json.loads(data.get("arguments", entry["args_buf"]))
                                        except json.JSONDecodeError:
                                            args = {}
                                        completed_calls.append({
                                            "call_id": call_id,
                                            "name": entry["name"],
                                            "args": args,
                                        })

                                elif ev == "response.audio_transcript.delta":
                                    text = data.get("delta", "")
                                    if text.strip():
                                        log.debug(f"Qwen text: {text[:120]}")
                                        if session:
                                            session.record_model_text(text)

                                elif ev == "response.done":
                                    log.debug("response.done")
                                    break

                                elif ev == "error":
                                    err = data.get("error", {})
                                    log.error(f"Qwen error event: {err}")

                            # ── Execute completed function calls ─────────
                            if not completed_calls:
                                # No tool calls — model responded with text only, re-prompt
                                empty_turns += 1
                                log.info(f"No tool calls in response ({empty_turns}/{MAX_EMPTY_TURNS}) — re-prompting")
                                if empty_turns >= MAX_EMPTY_TURNS:
                                    if session:
                                        session.record_error(f"No tool calls after {MAX_EMPTY_TURNS} consecutive turns")
                                    return {
                                        "success": False,
                                        "summary": f"Model failed to produce tool calls after {MAX_EMPTY_TURNS} attempts",
                                        "actions_taken": actions_taken,
                                    }
                                continue
                            empty_turns = 0  # reset on successful tool call

                            fn_outputs = []
                            for call in completed_calls:
                                name = call["name"]
                                args = call["args"]
                                call_id = call["call_id"]
                                log.info(f"Qwen → {name}({args})")

                                if session:
                                    session.record_tool_call(name, args, call_id)

                                if name == "done":
                                    success = bool(args.get("success", True))
                                    summary = str(args.get("summary", ""))
                                    if session:
                                        session.record_done(success, summary)
                                    terminal_result = {
                                        "success": success,
                                        "summary": summary,
                                        "escalated": False,
                                        "escalation_reason": "",
                                    }
                                    fn_outputs.append((call_id, "acknowledged"))

                                elif name == "escalate":
                                    reason = str(args.get("reason", ""))
                                    if session:
                                        session.record_escalate(reason)
                                    terminal_result = {
                                        "success": False,
                                        "summary": f"Escalated: {reason}",
                                        "escalated": True,
                                        "escalation_reason": reason,
                                    }
                                    fn_outputs.append((call_id, "acknowledged"))

                                else:
                                    _t0 = time.monotonic()
                                    result = execute_action(name, args, display)
                                    _elapsed = (time.monotonic() - _t0) * 1000
                                    if result == "ok":
                                        log.info(f"Action {name} — ok ({_elapsed:.0f}ms)")
                                    if session:
                                        session.record_tool_response(name, call_id, result, duration_ms=_elapsed)
                                    fn_outputs.append((call_id, result))
                                    actions_taken += 1

                            # Send all function outputs back
                            for call_id, output in fn_outputs:
                                await ws.send_str(json.dumps({
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": str(output),
                                    },
                                }))

                            if terminal_result:
                                break

        except asyncio.TimeoutError:
            if session:
                session.record_error(f"timeout after {timeout}s")
            return {
                "success": False,
                "summary": f"Timed out after {timeout}s",
                "error": f"timeout after {timeout}s",
                "actions_taken": actions_taken,
            }
        except Exception as e:
            log.error(f"Qwen Omni error: {e}", exc_info=True)
            if session:
                session.record_error(str(e))
            return {"error": str(e), "success": False, "actions_taken": actions_taken}

        if terminal_result:
            return {**terminal_result, "actions_taken": actions_taken}

        return {
            "success": False,
            "summary": "Session ended without completion signal",
            "actions_taken": actions_taken,
        }


# ─── Helpers ────────────────────────────────────────────────────────────────


def _capture_frame(display: str, session=None) -> str | None:
    """Capture screen with cursor overlay, save to session, return base64 JPEG or None."""
    try:
        from ..capture.screen import capture_screen_with_cursor, frame_to_jpeg
        frame = capture_screen_with_cursor(display=display)
        if frame is None:
            return None
        jpeg = frame_to_jpeg(frame, max_dim=1280, quality=72)
        if session:
            session.record_frame(jpeg)
        return base64.b64encode(jpeg).decode()
    except Exception as e:
        log.debug(f"Frame capture error: {e}")
        return None


def _get_display_size(display: str) -> tuple[int, int]:
    try:
        from ..display.manager import get_xlib_display
        xd = get_xlib_display(display)
        root = xd.screen().root
        g = root.get_geometry()
        return g.width, g.height
    except Exception:
        return config.DEFAULT_TASK_DISPLAY_WIDTH, config.DEFAULT_TASK_DISPLAY_HEIGHT
