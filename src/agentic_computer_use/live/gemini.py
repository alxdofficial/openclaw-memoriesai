"""Gemini Live API provider — live UI vision and control via google-genai SDK."""
import asyncio
import logging
import subprocess
import time

from google import genai
from google.genai import types

from .. import config
from .. import debug as _dbg
from .actions import execute_action
from .base import LiveUIProvider

log = logging.getLogger(__name__)


def _get_project() -> str:
    """Return project from config or gcloud active config."""
    if config.GEMINI_PROJECT:
        return config.GEMINI_PROJECT
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=10,
        )
        project = result.stdout.strip()
        if not project:
            raise RuntimeError("No GCP project set — run: gcloud config set project PROJECT_ID")
        return project
    except FileNotFoundError:
        raise RuntimeError("gcloud not found — run: gcloud auth login")


_SYSTEM_INSTRUCTION = """\
You are a UI automation agent controlling a Linux desktop ({width}x{height} pixels).
Your job: complete the given instruction by observing screenshots and calling tools.

CRITICAL: You MUST respond by calling tools every turn. Never give a verbal response \
without a tool call. The ONLY valid responses are function calls. Do not narrate, \
describe, or explain — just call the appropriate tool and act.

COORDINATES: All position arguments (x_pct, y_pct) are fractions of the screen size:
  x_pct=0.0 is the left edge, x_pct=1.0 is the right edge ({width}px)
  y_pct=0.0 is the top edge,  y_pct=1.0 is the bottom edge ({height}px)
  Example: centre of screen is x_pct=0.5, y_pct=0.5

Available tools:
  move_mouse(x_pct, y_pct)                       — move cursor WITHOUT clicking
  click(x_pct, y_pct, button="left")             — move mouse and click
  double_click(x_pct, y_pct)                     — double-click
  type_text(text)                                 — type text (click the field first!)
  key_press(key)                                  — key combo: Return, Tab, Escape,
                                                    BackSpace, ctrl+c, ctrl+v, ctrl+a,
                                                    ctrl+z, alt+Tab, etc.
  scroll(x_pct, y_pct, direction, amount=3)      — scroll. direction: up/down/left/right
  done(summary, success)                          — call when the instruction is complete
  escalate(reason)                                — call when you cannot proceed

Screenshot workflow — you receive screenshots continuously:
- Each screenshot shows the current screen with the cursor as a RED CIRCLE with crosshair.
- After you call tools, you will receive a fresh screenshot showing the result.

Cursor precision workflow:
- Use move_mouse first to position the cursor, then verify in the screenshot, then click.
- It is FINE and EXPECTED to call move_mouse multiple times to inch toward the target.
- Do not give up after one miss — keep adjusting x_pct/y_pct until the cursor sits on
  the target, then call click at the same position.

General rules:
- Act immediately. Do not wait or narrate.
- Click a text field before typing into it.
- Call done() as soon as the instruction is fully complete.
- Call escalate() for: login/CAPTCHA prompts you can't solve, unexpected blocking dialogs,
  missing credentials, or anything that requires human intervention.
"""

_COORD_PROPS = {
    "x_pct": types.Schema(
        type="NUMBER",
        description="Horizontal position as a fraction of screen width (0.0=left, 1.0=right)",
    ),
    "y_pct": types.Schema(
        type="NUMBER",
        description="Vertical position as a fraction of screen height (0.0=top, 1.0=bottom)",
    ),
}

_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="move_mouse",
        description=(
            "Move the cursor to a screen position WITHOUT clicking. "
            "Call this to verify cursor placement — the cursor appears as a red circle "
            "in the next screenshot. Repeat with adjusted values to inch toward the target."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties=_COORD_PROPS,
            required=["x_pct", "y_pct"],
        ),
    ),
    types.FunctionDeclaration(
        name="click",
        description="Move mouse and click at a screen position.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                **_COORD_PROPS,
                "button": types.Schema(
                    type="STRING",
                    enum=["left", "right", "middle"],
                    description="Mouse button (default: left)",
                ),
            },
            required=["x_pct", "y_pct"],
        ),
    ),
    types.FunctionDeclaration(
        name="double_click",
        description="Double-click at a screen position.",
        parameters=types.Schema(
            type="OBJECT",
            properties=_COORD_PROPS,
            required=["x_pct", "y_pct"],
        ),
    ),
    types.FunctionDeclaration(
        name="type_text",
        description="Type text at the currently focused input field. Click the field first.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "text": types.Schema(type="STRING", description="Text to type"),
            },
            required=["text"],
        ),
    ),
    types.FunctionDeclaration(
        name="key_press",
        description="Press a key or key combination.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "key": types.Schema(
                    type="STRING",
                    description=(
                        "Key name: Return, Tab, Escape, BackSpace, "
                        "ctrl+c, ctrl+v, ctrl+a, ctrl+z, alt+Tab, etc."
                    ),
                ),
            },
            required=["key"],
        ),
    ),
    types.FunctionDeclaration(
        name="scroll",
        description="Scroll at a screen position.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                **_COORD_PROPS,
                "direction": types.Schema(
                    type="STRING",
                    enum=["up", "down", "left", "right"],
                ),
                "amount": types.Schema(
                    type="INTEGER",
                    description="Scroll steps (default 3)",
                ),
            },
            required=["x_pct", "y_pct", "direction"],
        ),
    ),
    types.FunctionDeclaration(
        name="done",
        description="Signal that the instruction has been completed.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "summary": types.Schema(type="STRING", description="What was accomplished"),
                "success": types.Schema(type="BOOLEAN"),
            },
            required=["summary", "success"],
        ),
    ),
    types.FunctionDeclaration(
        name="escalate",
        description=(
            "Signal that you cannot proceed and need human intervention. "
            "Use for: login walls, CAPTCHAs, missing credentials, unexpected blockers."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "reason": types.Schema(
                    type="STRING",
                    description="What happened and what is needed to proceed",
                ),
            },
            required=["reason"],
        ),
    ),
]


class GeminiLiveProvider(LiveUIProvider):
    """Gemini Live API provider using google-genai SDK (Vertex AI backend)."""

    async def run(
        self,
        instruction: str,
        timeout: int,
        task_id: str | None,
        display: str,
        context: str = "",
        session=None,  # LiveUISession | None
    ) -> dict:
        model = config.GEMINI_LIVE_MODEL
        width, height = _get_display_size(display)

        system_text = _SYSTEM_INSTRUCTION.format(width=width, height=height)
        if context:
            system_text += f"\n\nAdditional context:\n{context}"

        if config.GEMINI_API_KEY:
            log.info(f"Connecting to Gemini Live via Google AI Dev: model={model}")
            client = genai.Client(api_key=config.GEMINI_API_KEY)
        else:
            try:
                project = _get_project()
            except RuntimeError as e:
                return {"error": f"No GEMINI_API_KEY set and gcloud config failed: {e}"}
            location = config.GEMINI_LOCATION
            log.info(f"Connecting to Gemini Live via Vertex AI: project={project} location={location} model={model}")
            client = genai.Client(vertexai=True, project=project, location=location)

        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
            system_instruction=types.Content(parts=[types.Part(text=system_text)]),
            realtime_input_config=types.RealtimeInputConfig(
                activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            ),
        )

        actions_taken = 0
        terminal_result: dict | None = None
        sid = session.id[:8] if session else "--------"

        # Limits to prevent runaway sessions
        MAX_TURNS = 30
        MAX_EMPTY_TURNS = 3  # consecutive turns with no tool calls before giving up

        _dbg.log("LIVE", f"[{sid}] Connecting: model={model} task={task_id} timeout={timeout}s")
        try:
            async with asyncio.timeout(timeout):
                async with client.aio.live.connect(model=model, config=live_config) as live_session:
                    log.info("Gemini Live session established")
                    _dbg.log("LIVE", f"[{sid}] Session established")

                    # ── 1. Send initial instruction via realtime input ────
                    # Use send_realtime_input for all text to avoid mixing
                    # send_client_content and send_realtime_input (SDK warns
                    # against interleaving them).
                    await live_session.send_realtime_input(text=instruction)

                    # ── 2. Background frame sender ────────────────────────
                    frame_stop = asyncio.Event()

                    async def _send_frames():
                        while not frame_stop.is_set():
                            try:
                                jpeg = _capture_jpeg(display, session)
                                if jpeg:
                                    await live_session.send_realtime_input(
                                        video=types.Blob(
                                            data=jpeg, mime_type="image/jpeg"
                                        )
                                    )
                            except Exception as e:
                                log.debug(f"Frame send error: {e}")
                            await asyncio.sleep(0.5)

                    frame_task = asyncio.ensure_future(_send_frames())

                    # ── 3. Main receive loop ──────────────────────────────
                    try:
                        turn_count = 0
                        empty_turns = 0  # consecutive turns with no tool calls

                        while turn_count < MAX_TURNS:
                            turn_count += 1
                            got_tool_call = False
                            got_content = False  # model sent audio/text (narrating)

                            async for response in live_session.receive():
                                outcome = await _handle_response(
                                    response, live_session, display, session=session,
                                )
                                if outcome:
                                    if outcome.get("action_taken"):
                                        actions_taken += 1
                                        got_tool_call = True
                                    elif outcome.get("terminal"):
                                        terminal_result = outcome
                                        break
                                if response.server_content:
                                    if response.server_content.model_turn:
                                        got_content = True
                                    if response.server_content.turn_complete:
                                        break

                            if terminal_result:
                                break

                            if got_tool_call:
                                empty_turns = 0
                                _dbg.log("LIVE", f"[{sid}] turn={turn_count} tools_taken={actions_taken} → continuation")
                                await live_session.send_realtime_input(
                                    text="Continue with the next step of the task."
                                )
                            elif got_content:
                                # Model narrated but took no action — prompt it to use tools
                                empty_turns += 1
                                _dbg.log("LIVE", f"[{sid}] turn={turn_count} audio-only (no tools), empty_turns={empty_turns} — prompting for action")
                                if empty_turns >= MAX_EMPTY_TURNS:
                                    _dbg.log("LIVE", f"[{sid}] Too many empty turns ({empty_turns}) — ending session")
                                    break
                                await live_session.send_realtime_input(
                                    text="You must call a tool. Use click, type_text, key_press, or another tool to take the next action now."
                                )
                            else:
                                # Completely empty turn — nothing from model at all
                                empty_turns += 1
                                _dbg.log("LIVE", f"[{sid}] turn={turn_count} empty (no audio, no tools), empty_turns={empty_turns}")
                                if empty_turns >= MAX_EMPTY_TURNS:
                                    _dbg.log("LIVE", f"[{sid}] Too many empty turns ({empty_turns}) — ending session")
                                    break
                                await live_session.send_realtime_input(
                                    text="Please take the next action to complete the task."
                                )
                    finally:
                        frame_stop.set()
                        frame_task.cancel()
                        try:
                            await asyncio.wait_for(frame_task, timeout=2.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                        if session:
                            session.finalize_audio()

        except asyncio.TimeoutError:
            _dbg.log("LIVE", f"[{sid}] Timeout after {timeout}s — actions_taken={actions_taken}")
            if session:
                session.record_error(f"timeout after {timeout}s")
            return {
                "success": False,
                "summary": f"Timed out after {timeout}s",
                "error": f"timeout after {timeout}s",
                "actions_taken": actions_taken,
            }
        except Exception as e:
            _dbg.log("LIVE", f"[{sid}] Exception: {e}")
            log.error(f"Gemini Live error: {e}", exc_info=True)
            if session:
                session.record_error(str(e))
            return {
                "error": str(e),
                "success": False,
                "actions_taken": actions_taken,
            }

        # Track Gemini Live session (SDK does not expose token counts)
        from .. import usage as _usage
        _usage.record_nowait(
            provider="gemini_live", model=model, task_id=task_id,
            input_tokens=0, output_tokens=0, requests=1,
        )

        if terminal_result:
            result = {
                "success": terminal_result.get("success", False),
                "summary": terminal_result.get("summary", ""),
                "escalated": terminal_result.get("escalated", False),
                "escalation_reason": terminal_result.get("escalation_reason", ""),
                "actions_taken": actions_taken,
            }
            _dbg.log("LIVE", f"[{sid}] Session done: success={result['success']} actions={actions_taken} summary={result['summary'][:80]}")
            return result

        _dbg.log("LIVE", f"[{sid}] Session ended without completion signal: actions={actions_taken}")
        return {
            "success": False,
            "summary": "Session ended without completion signal",
            "actions_taken": actions_taken,
        }


# ─── Helpers ────────────────────────────────────────────────────────────────


def _capture_jpeg(display: str, session=None) -> bytes:
    """Capture current screen with cursor overlay, record to session, return JPEG bytes."""
    from ..capture.screen import capture_screen_with_cursor, frame_to_jpeg
    try:
        frame = capture_screen_with_cursor(display=display)
        if frame is not None:
            jpeg = frame_to_jpeg(frame, max_dim=1280, quality=72)
            if session:
                session.record_frame(jpeg)
            return jpeg
    except Exception as e:
        log.debug(f"Frame capture error: {e}")
    return b""


async def _handle_response(response, live_session, display: str, session=None) -> dict | None:
    """Process one LiveServerMessage from the SDK.

    Returns:
        {"terminal": True, ...}  — done or escalate called
        {"action_taken": True}   — GUI action executed
        None                     — text chunk or non-actionable message
    """
    sid = session.id[:8] if session else "--------"
    if response.tool_call:
        fc_list = response.tool_call.function_calls or []
        if not fc_list:
            return None

        fn_responses = []
        terminal: dict | None = None

        for fc in fc_list:
            name = fc.name
            args = dict(fc.args) if fc.args else {}
            call_id = fc.id or ""
            log.info(f"Gemini Live → {name}({args})")
            _dbg.log("LIVE", f"[{sid}] tool_call: {name}({', '.join(f'{k}={v}' for k, v in args.items())})")

            if session:
                session.record_tool_call(name, args, call_id)

            if name == "done":
                success = bool(args.get("success", True))
                summary = str(args.get("summary", ""))
                if session:
                    session.record_done(success, summary)
                terminal = {"terminal": True, "success": success, "summary": summary}
                fn_responses.append(
                    types.FunctionResponse(id=call_id, name=name, response={"result": "acknowledged"})
                )

            elif name == "escalate":
                reason = str(args.get("reason", ""))
                if session:
                    session.record_escalate(reason)
                terminal = {
                    "terminal": True, "success": False,
                    "escalated": True, "escalation_reason": reason,
                    "summary": f"Escalated: {reason}",
                }
                fn_responses.append(
                    types.FunctionResponse(id=call_id, name=name, response={"result": "acknowledged"})
                )

            else:
                # Convert percentage coords to pixels for xdotool
                if "x_pct" in args or "y_pct" in args:
                    w, h = _get_display_size(display)
                    args = dict(args)
                    if "x_pct" in args:
                        args["x"] = round(float(args.pop("x_pct")) * w)
                    if "y_pct" in args:
                        args["y"] = round(float(args.pop("y_pct")) * h)
                _t0 = time.monotonic()
                result = execute_action(name, args, display)
                _elapsed = (time.monotonic() - _t0) * 1000
                if result == "ok":
                    log.info(f"Action {name} — ok ({_elapsed:.0f}ms)")
                if session:
                    session.record_tool_response(name, call_id, result, duration_ms=_elapsed)
                fn_responses.append(
                    types.FunctionResponse(id=call_id, name=name, response={"result": result})
                )

        await live_session.send_tool_response(function_responses=fn_responses)

        if terminal:
            return terminal
        return {"action_taken": True}

    if response.server_content:
        model_turn = response.server_content.model_turn
        turn_complete = response.server_content.turn_complete
        if turn_complete:
            log.info("Gemini Live turn_complete received")
            _dbg.log("LIVE", f"[{sid}] turn_complete")
        for part in (model_turn.parts if model_turn else []):
            if part.text and part.text.strip():
                text = part.text.strip()
                log.info(f"Gemini Live text: {text[:200]}")
                if session:
                    session.record_model_text(text)
            elif part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                _dbg.log("LIVE", f"[{sid}] audio chunk: {len(part.inline_data.data)} bytes")
                if session:
                    session.record_audio_chunk(part.inline_data.data)

    if response.go_away:
        log.info(f"Gemini Live go_away received: {response.go_away}")
        _dbg.log("LIVE", f"[{sid}] go_away: {response.go_away}")

    return None


def _get_display_size(display: str) -> tuple[int, int]:
    """Return the pixel dimensions of an X11 display."""
    try:
        from ..display.manager import get_xlib_display
        xd = get_xlib_display(display)
        root = xd.screen().root
        g = root.get_geometry()
        return g.width, g.height
    except Exception:
        return (
            config.DEFAULT_TASK_DISPLAY_WIDTH,
            config.DEFAULT_TASK_DISPLAY_HEIGHT,
        )
