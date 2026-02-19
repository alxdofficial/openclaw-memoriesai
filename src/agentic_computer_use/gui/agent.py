"""GUI agent orchestrator — NL instruction → ground → execute → log."""
import json
import re
import asyncio
import logging
from .. import config
from ..desktop import control as desktop
from ..capture.screen import capture_screen, capture_window, find_window_by_name, frame_to_jpeg
from ..screenshots import save_screenshot
from .base import GUIAgentBackend
from .types import GroundingResult

log = logging.getLogger(__name__)

_backend: GUIAgentBackend | None = None


def _get_backend() -> GUIAgentBackend:
    global _backend
    if _backend is not None:
        return _backend

    name = config.GUI_AGENT_BACKEND.lower()
    if name == "direct":
        from .backends.direct import DirectBackend
        _backend = DirectBackend()
    elif name == "uitars":
        from .backends.uitars import UITARSBackend
        _backend = UITARSBackend()
    elif name == "claude_cu":
        from .backends.claude_cu import ClaudeCUBackend
        _backend = ClaudeCUBackend()
    elif name == "omniparser":
        from .backends.omniparser import OmniParserBackend
        _backend = OmniParserBackend()
    else:
        raise ValueError(f"Unknown GUI agent backend: {name}. Use direct|uitars|claude_cu|omniparser")

    return _backend


def _parse_explicit_coords(instruction: str) -> tuple[str, int, int] | None:
    """Parse explicit coordinate instructions like 'click(847, 523)' or 'click at 847, 523'."""
    # click(x, y) or click(x,y)
    m = re.match(r'click\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', instruction, re.IGNORECASE)
    if m:
        return ("click", int(m.group(1)), int(m.group(2)))

    # type(x, y, "text")
    m = re.match(r'type\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*["\'](.+?)["\']\s*\)', instruction, re.IGNORECASE)
    if m:
        return ("type", int(m.group(1)), int(m.group(2)))

    return None


async def execute_gui_action(
    instruction: str,
    task_id: str = None,
    window_name: str = None,
    display: str = None,
) -> dict:
    """Execute a GUI action from NL or explicit coordinates.

    Flow: parse instruction → capture screen → ground (if NL) → execute → return result.
    """
    # Focus target window if specified
    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            desktop.focus_window(wid, display=display)
            await asyncio.sleep(0.3)
        else:
            return {"ok": False, "error": f"Window '{window_name}' not found"}

    # Try explicit coordinates first
    explicit = _parse_explicit_coords(instruction)
    if explicit:
        action_type, x, y = explicit
        # Auto-focus window at target coordinates if no window was pre-focused
        if not window_name:
            wid = desktop.find_window_at(x, y, display=display)
            if wid:
                desktop.focus_window(wid, display=display)
                await asyncio.sleep(0.15)
        if action_type == "click":
            ok = desktop.mouse_click_at(x, y, display=display)
            return {"ok": ok, "action": "click", "x": x, "y": y, "grounded": False}
        elif action_type == "type":
            desktop.mouse_click_at(x, y, display=display)
            await asyncio.sleep(0.1)
            # Extract text from instruction
            m = re.search(r'["\'](.+?)["\']', instruction)
            text = m.group(1) if m else ""
            ok = desktop.type_text(text, display=display)
            return {"ok": ok, "action": "type", "x": x, "y": y, "text": text, "grounded": False}

    # NL instruction — need grounding
    backend = _get_backend()

    # Capture screenshot for grounding
    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            frame = capture_window(wid, display=display)
        else:
            frame = capture_screen(display=display)
    else:
        frame = capture_screen(display=display)

    if frame is None:
        return {"ok": False, "error": "Failed to capture screen for grounding"}

    jpeg = frame_to_jpeg(frame)
    screen_h, screen_w = frame.shape[:2]

    # Ground the instruction
    grounding = await backend.ground(instruction, jpeg)

    if grounding is None:
        return {
            "ok": False,
            "error": f"Could not locate element: {instruction}",
            "hint": "Try explicit coordinates with click(x, y) or use a grounding-capable backend (uitars, claude_cu)",
            "backend": config.GUI_AGENT_BACKEND,
            "provider": backend.provider,
        }

    # Execute based on instruction intent
    action = _infer_action(instruction)
    x, y = grounding.x, grounding.y

    if action == "click":
        ok = desktop.mouse_click_at(x, y, display=display)
    elif action == "double_click":
        ok = desktop.mouse_double_click(x, y, display=display)
    elif action == "type":
        desktop.mouse_click_at(x, y, display=display)
        await asyncio.sleep(0.1)
        text = _extract_text(instruction)
        ok = desktop.type_text(text, display=display) if text else False
    elif action == "right_click":
        ok = desktop.mouse_click_at(x, y, button=3, display=display)
    else:
        ok = desktop.mouse_click_at(x, y, display=display)

    # Capture "after" screenshot (in executor — capture_screen is blocking Xlib)
    await asyncio.sleep(0.15)
    loop = asyncio.get_event_loop()
    after_frame = await loop.run_in_executor(None, capture_screen, display)

    # Log action to task if linked
    if task_id:
        try:
            from .. import db
            action_id = db.new_id()
            from ..task import manager as task_mgr

            input_data = {"instruction": instruction}
            before_refs = save_screenshot(action_id, "before", frame)
            if before_refs:
                input_data["screenshot"] = before_refs

            output_data = {
                "ok": ok, "action": action, "x": x, "y": y,
                "confidence": grounding.confidence,
                "element": grounding.element_text,
            }
            if after_frame is not None:
                after_refs = save_screenshot(action_id, "after", after_frame)
                if after_refs:
                    output_data["screenshot"] = after_refs

            await task_mgr.log_action(
                task_id=task_id,
                action_type="gui",
                summary=f"{action} at ({x}, {y}): {instruction}",
                input_data=json.dumps(input_data),
                output_data=json.dumps(output_data),
                status="completed" if ok else "failed",
            )
        except Exception as e:
            log.warning(f"Failed to log GUI action to task: {e}")

    return {
        "ok": ok,
        "action": action,
        "x": x, "y": y,
        "grounded": True,
        "confidence": grounding.confidence,
        "element": grounding.element_text,
        "backend": config.GUI_AGENT_BACKEND,
        "provider": backend.provider,
    }


async def find_gui_element(
    description: str,
    window_name: str = None,
    display: str = None,
) -> dict:
    """Locate a UI element without acting on it."""
    backend = _get_backend()

    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            desktop.focus_window(wid, display=display)
            await asyncio.sleep(0.2)
            frame = capture_window(wid, display=display)
        else:
            return {"found": False, "error": f"Window '{window_name}' not found"}
    else:
        frame = capture_screen(display=display)

    if frame is None:
        return {"found": False, "error": "Failed to capture screen"}

    jpeg = frame_to_jpeg(frame)
    grounding = await backend.ground(description, jpeg)

    if grounding is None:
        return {
            "found": False,
            "description": description,
            "backend": config.GUI_AGENT_BACKEND,
            "provider": backend.provider,
            "hint": "Element not found. Try a different description or use a grounding-capable backend.",
        }

    return {
        "found": True,
        "x": grounding.x,
        "y": grounding.y,
        "confidence": grounding.confidence,
        "element_text": grounding.element_text,
        "description": description,
        "backend": config.GUI_AGENT_BACKEND,
        "provider": backend.provider,
    }


def _infer_action(instruction: str) -> str:
    """Infer the action type from natural language instruction."""
    lower = instruction.lower()
    if any(w in lower for w in ["double click", "double-click", "doubleclick"]):
        return "double_click"
    if any(w in lower for w in ["right click", "right-click", "rightclick"]):
        return "right_click"
    if any(w in lower for w in ["type ", "enter ", "input ", "write "]):
        return "type"
    return "click"


def _extract_text(instruction: str) -> str:
    """Extract text to type from instruction like 'type hello in the search box'."""
    # Quoted text has highest priority — type "foo bar" or type 'foo bar'
    m = re.search(r'["\'](.+?)["\']', instruction)
    if m:
        return m.group(1)

    # "type X in/into/on ..." — stop at the preposition
    m = re.search(r'\btype\s+(.+?)\s+(?:in|into|on|the)\b', instruction, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # "enter X", "input X", "write X" — with optional trailing "in ..." clause
    for verb in ("enter", "input", "write"):
        m = re.search(
            rf'\b{verb}\s+(.+?)(?:\s+(?:in|into|on|the)\b|$)',
            instruction, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

    # Fallback: everything after "type " when nothing else matched
    m = re.search(r'\btype\s+(.+)$', instruction, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return ""
