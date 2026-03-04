"""Gemini Live API provider — live UI vision and control via google-genai SDK."""
import asyncio
import logging
import subprocess

from google import genai
from google.genai import types

from .. import config
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
Your job: complete the given instruction by observing screenshots and taking actions.
All coordinates must be integers in the range x=[0,{width}], y=[0,{height}].

Available tools:
  click(x, y, button="left")          — move mouse and click
  double_click(x, y)                  — double-click
  type_text(text)                     — type text (click the field first!)
  key_press(key)                      — key combo: Return, Tab, Escape, BackSpace,
                                        ctrl+c, ctrl+v, ctrl+a, ctrl+z, alt+Tab, etc.
  scroll(x, y, direction, amount=3)   — scroll at position. direction: up/down/left/right
  done(summary, success)              — call when the instruction is complete
  escalate(reason)                    — call when you cannot proceed

Rules:
- Study each screenshot carefully before acting.
- Click a text field before typing into it.
- After each action, wait for the next screenshot to verify the result.
- Call done() as soon as the instruction is fully complete.
- Call escalate() for: login/CAPTCHA prompts you can't solve, unexpected blocking dialogs,
  missing credentials, or anything that requires human intervention.
"""

_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="click",
        description="Move mouse and click at screen coordinates.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "x": types.Schema(type="INTEGER", description="X pixel coordinate"),
                "y": types.Schema(type="INTEGER", description="Y pixel coordinate"),
                "button": types.Schema(
                    type="STRING",
                    enum=["left", "right", "middle"],
                    description="Mouse button (default: left)",
                ),
            },
            required=["x", "y"],
        ),
    ),
    types.FunctionDeclaration(
        name="double_click",
        description="Double-click at screen coordinates.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "x": types.Schema(type="INTEGER"),
                "y": types.Schema(type="INTEGER"),
            },
            required=["x", "y"],
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
        description="Scroll at screen coordinates.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "x": types.Schema(type="INTEGER"),
                "y": types.Schema(type="INTEGER"),
                "direction": types.Schema(
                    type="STRING",
                    enum=["up", "down", "left", "right"],
                ),
                "amount": types.Schema(
                    type="INTEGER",
                    description="Scroll steps (default 3)",
                ),
            },
            required=["x", "y", "direction"],
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
        )

        actions_taken = 0
        terminal_result: dict | None = None

        try:
            async with asyncio.timeout(timeout):
                async with client.aio.live.connect(model=model, config=live_config) as live_session:
                    log.info("Gemini Live session established")

                    # ── 1. Send initial instruction ──────────────────────
                    await live_session.send(input=instruction, end_of_turn=True)

                    # ── 2. Frame sender (background) ─────────────────────
                    frame_stop = asyncio.Event()
                    frame_task = asyncio.create_task(
                        _send_frames(
                            live_session, display, frame_stop,
                            interval=config.LIVE_UI_FRAME_INTERVAL,
                            session=session,
                        )
                    )

                    try:
                        async for response in live_session.receive():
                            outcome = await _handle_response(
                                response, live_session, display, session=session,
                            )
                            if outcome:
                                if outcome.get("action_taken"):
                                    actions_taken += 1
                                elif outcome.get("terminal"):
                                    terminal_result = outcome
                                    break
                    finally:
                        frame_stop.set()
                        frame_task.cancel()
                        try:
                            await asyncio.wait_for(frame_task, timeout=2.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass

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
            log.error(f"Gemini Live error: {e}", exc_info=True)
            if session:
                session.record_error(str(e))
            return {
                "error": str(e),
                "success": False,
                "actions_taken": actions_taken,
            }

        if terminal_result:
            return {
                "success": terminal_result.get("success", False),
                "summary": terminal_result.get("summary", ""),
                "escalated": terminal_result.get("escalated", False),
                "escalation_reason": terminal_result.get("escalation_reason", ""),
                "actions_taken": actions_taken,
            }

        return {
            "success": False,
            "summary": "Session ended without completion signal",
            "actions_taken": actions_taken,
        }


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _send_frames(live_session, display: str, stop: asyncio.Event,
                       interval: float, session=None) -> None:
    """Capture and stream JPEG frames to Gemini until stop is set."""
    from ..capture.screen import capture_screen, frame_to_jpeg

    while not stop.is_set():
        try:
            frame = capture_screen(display=display)
            if frame is not None:
                jpeg = frame_to_jpeg(frame, max_dim=1280, quality=72)
                if session:
                    session.record_frame(jpeg)
                await live_session.send(
                    input=types.Blob(data=jpeg, mime_type="image/jpeg")
                )
        except Exception as e:
            log.debug(f"Frame send error: {e}")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass  # Normal — keep looping


async def _handle_response(response, live_session, display: str, session=None) -> dict | None:
    """
    Process one LiveServerMessage from the SDK.
    Returns:
        {"terminal": True, ...}  — done or escalate called
        {"action_taken": True}   — GUI action executed
        None                     — text chunk or non-actionable message
    """
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
                result = execute_action(name, args, display)
                if session:
                    session.record_tool_response(name, call_id, result)
                fn_responses.append(
                    types.FunctionResponse(id=call_id, name=name, response={"result": result})
                )

        await live_session.send(
            input=types.LiveClientToolResponse(function_responses=fn_responses)
        )

        if terminal:
            return terminal
        return {"action_taken": True}

    if response.server_content:
        model_turn = response.server_content.model_turn
        for part in (model_turn.parts if model_turn else []):
            if part.text and part.text.strip():
                text = part.text.strip()
                log.debug(f"Gemini Live text: {text[:200]}")
                if session:
                    session.record_model_text(text)

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
