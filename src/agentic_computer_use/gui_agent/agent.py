"""GUI agent orchestrator — NL instruction → ground → iterative narrow → execute → log."""
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

# Iterative narrowing radii (screen px).  Each entry adds one refinement pass:
#   full frame → 300px crop → 150px crop
# Inspired by RegionFocus (ICCV 2025, arXiv:2505.00684).  Three passes give +28%
# accuracy over single-shot on UI-TARS; the 150px third pass is critical for
# 2-4px timeline handles in DaVinci Resolve.
_NARROW_RADII: list[int] = [300, 150]

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


async def _iterative_narrow(
    backend: GUIAgentBackend,
    instruction: str,
    frame,
    initial: GroundingResult,
    loop,
) -> GroundingResult:
    """Multi-round crop-and-reground for sub-element precision (RegionFocus style).

    Starting from the initial full-frame prediction, each round crops _NARROW_RADII[i]
    pixels around the current best estimate and re-grounds on the zoomed crop.
    Crop-local coordinates are accumulated back to full-screen space after each pass.

    Default: two refinement rounds — 300px then 150px.
      Pass 0 (full frame, 960px wide) → initial (x, y)
      Pass 1 (≤600×600px crop)        → refined to ~5× scale
      Pass 2 (≤300×300px crop)        → refined to ~10× scale
                                         critical for 2-4px timeline handles

    Falls back to the best result so far if any round returns None.
    """
    screen_h, screen_w = frame.shape[:2]
    current = initial

    for round_idx, radius in enumerate(_NARROW_RADII):
        x0, y0 = current.x, current.y

        # Crop bounds in full-screen space, clamped to screen
        x1 = max(0, x0 - radius)
        y1 = max(0, y0 - radius)
        x2 = min(screen_w, x0 + radius)
        y2 = min(screen_h, y0 + radius)
        crop_w = x2 - x1
        crop_h = y2 - y1

        if crop_w < 20 or crop_h < 20:
            log.debug("Iterative narrowing round %d: degenerate crop (%dx%d), stopping", round_idx + 1, crop_w, crop_h)
            break

        crop_frame = frame[y1:y2, x1:x2]
        crop_jpeg = await loop.run_in_executor(None, frame_to_jpeg, crop_frame)

        refined = await backend.ground(instruction, crop_jpeg, image_size=(crop_w, crop_h))
        if refined is None:
            log.debug("Iterative narrowing round %d: backend returned None, keeping previous prediction", round_idx + 1)
            break

        # Map crop-local coords → full-screen coords
        current = GroundingResult(
            x=x1 + refined.x,
            y=y1 + refined.y,
            confidence=refined.confidence,
            description=refined.description,
            element_text=refined.element_text,
        )
        log.debug("Iterative narrowing round %d (radius=%dpx): (%d,%d) → (%d,%d)",
                  round_idx + 1, radius, initial.x, initial.y, current.x, current.y)

    return current


async def execute_gui_action(
    instruction: str,
    task_id: str = None,
    window_name: str = None,
    display: str = None,
) -> dict:
    """Execute a GUI action from a natural language instruction.

    Flow: capture screen → ground (NL → coordinates) → iterative narrow → execute → log.
    All grounding goes through the configured backend (uitars, claude_cu, omniparser).
    Raw coordinate strings are no longer accepted — use desktop_action for that.
    """
    # Focus target window if specified
    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            desktop.focus_window(wid, display=display)
            await asyncio.sleep(0.1)
        else:
            return {"ok": False, "error": f"Window '{window_name}' not found"}

    backend = _get_backend()
    loop = asyncio.get_event_loop()

    # Capture screenshot for grounding (off-thread — Xlib/PIL are blocking)
    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            frame = await loop.run_in_executor(None, capture_window, wid, display)
        else:
            frame = await loop.run_in_executor(None, capture_screen, display)
    else:
        frame = await loop.run_in_executor(None, capture_screen, display)

    if frame is None:
        return {"ok": False, "error": "Failed to capture screen for grounding"}

    jpeg = await loop.run_in_executor(None, frame_to_jpeg, frame)
    screen_h, screen_w = frame.shape[:2]

    # Initial grounding — pass actual screen dimensions so normalized coords scale correctly
    grounding = await backend.ground(instruction, jpeg, image_size=(screen_w, screen_h))

    if grounding is None:
        return {
            "ok": False,
            "error": f"Could not locate element: {instruction}",
            "hint": "Rephrase the description, or use desktop_action with explicit coordinates",
            "backend": config.GUI_AGENT_BACKEND,
            "provider": backend.provider,
        }

    # Iterative narrowing — crop around initial prediction and re-ground for precision
    grounding = await _iterative_narrow(backend, instruction, frame, grounding, loop)

    # Execute based on instruction intent
    action = _infer_action(instruction)
    x, y = grounding.x, grounding.y

    if action == "click":
        ok = desktop.mouse_click_at(x, y, display=display)
    elif action == "double_click":
        ok = desktop.mouse_double_click(x, y, display=display)
    elif action == "type":
        desktop.mouse_click_at(x, y, display=display)
        await asyncio.sleep(0.05)
        text = _extract_text(instruction)
        ok = desktop.type_text(text, display=display) if text else False
    elif action == "right_click":
        ok = desktop.mouse_click_at(x, y, button=3, display=display)
    else:
        ok = desktop.mouse_click_at(x, y, display=display)

    # Capture "after" screenshot (in executor — capture_screen is blocking Xlib)
    await asyncio.sleep(0.05)
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
                "backend": config.GUI_AGENT_BACKEND,
                "provider": backend.provider,
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

    loop = asyncio.get_event_loop()

    if window_name:
        wid = desktop.find_window(window_name, display=display)
        if wid:
            desktop.focus_window(wid, display=display)
            await asyncio.sleep(0.05)
            frame = await loop.run_in_executor(None, capture_window, wid, display)
        else:
            return {"found": False, "error": f"Window '{window_name}' not found"}
    else:
        frame = await loop.run_in_executor(None, capture_screen, display)

    if frame is None:
        return {"found": False, "error": "Failed to capture screen"}

    jpeg = await loop.run_in_executor(None, frame_to_jpeg, frame)
    screen_h, screen_w = frame.shape[:2]
    grounding = await backend.ground(description, jpeg, image_size=(screen_w, screen_h))

    if grounding is None:
        return {
            "found": False,
            "description": description,
            "backend": config.GUI_AGENT_BACKEND,
            "provider": backend.provider,
            "hint": "Element not found. Try a different description.",
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
