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
    "wait":         0.0,
}

_SYSTEM_PROMPT = """\
You are the supervisor of a desktop GUI agent. You see screenshots, decide what to do, and call tools to interact. After each action you receive a fresh screenshot showing the result.

## Your role

You are the decision-maker. You decide WHAT to do based on what you see. A separate grounding model (UI-TARS) handles WHERE — it finds UI elements on screen and positions the cursor precisely. You never need to estimate coordinates.

## How it works

### Positioning the cursor: move_to

When you call move_to(target="..."), this happens behind the scenes:
1. Your target description is sent to the grounding model along with the current screenshot.
2. The grounding model predicts the pixel coordinates of the described element.
3. The cursor is drawn at that position on the screenshot.
4. You receive the updated screenshot showing a red crosshair where the grounding model placed the cursor.

Your job is then to **verify**: look at where the red crosshair landed. Is it on the element you intended?

- **If yes** — proceed with click(), double_click(), or another action.
- **If no** — call move_to again. The grounding model will re-predict from scratch. Use the **hint** parameter to help it correct: describe where the cursor landed relative to where it should be.

Examples of good hints:
- move_to(target="Save button", hint="cursor landed on Cancel, target is the button to its right")
- move_to(target="search bar", hint="cursor is on the bookmark bar, target is the wider text field below")

You can call move_to as many times as needed — each call gives the grounding model a fresh chance to find the target.

### Acting: click, type_text, key_press, scroll

These tools perform the actual interaction. They act at the **current cursor position** — they do not accept a target.

Always move_to first, verify the cursor position, then act:
1. move_to(target="File menu") → see cursor overlay
2. Check: crosshair is on "File" → click()
3. Observe the result in the next screenshot

### Writing good target descriptions

The grounding model finds elements by your description, so be specific:
- Include visible text: "button labeled 'Submit' in the bottom-right"
- Describe position: "the address bar at the top of the browser"
- Distinguish similar elements: "the second row's Edit button, next to 'Report Q3'"
- Only describe elements you can actually see in the current screenshot.

## When things go wrong

- If the same approach fails twice, try a different one — keyboard shortcut, different menu path, or a URL.
- Prefer keyboard shortcuts when clicking is unreliable: ctrl+l for address bar, ctrl+alt+t for terminal.
- To open applications: key_press(key="super") opens the XFCE Whisker Menu (app launcher). Type the app name and press Enter. Or right-click the desktop for a context menu.
- This is an XFCE4 desktop. The panel is at the bottom with an app menu, task list, and system tray.
- type_text automatically selects and replaces existing text in the focused field. No need to clear the field manually first.
- If an element is cut off at the screen edge, scroll it into view before clicking.
- If you can't reach an element after 3 attempts, try a completely different path to the goal.

## Completion

- done(success=true) only when the screenshot confirms the task is fully complete.
- done(success=false) after 3+ failed approaches.
- escalate() for login walls, CAPTCHAs, or anything outside your capability.

## Thinking

Before every tool call, explain what you see and what you'll do in the message text. 1-3 sentences. This is critical for debugging.
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_to",
            "description": "Move cursor to a UI element. Cursor appears as red crosshair overlay. Verify placement in the returned screenshot before clicking. If the cursor missed, call move_to again with 'hint' or 'zoom'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Natural language description of the element to move to"},
                    "hint": {"type": "string", "description": "Spatial correction when retrying. E.g. 'target is 50px above cursor', 'cursor hit the wrong button, target is next one to the left'"},
                    "zoom": {"type": "integer", "description": "Re-ground on a cropped region this % of screen width, centered on current cursor. Use 20-50 when the cursor overlay is close but not precise enough (e.g. wrong menu item, adjacent button). Smaller = more zoomed in = more precise."},
                },
                "required": ["target"],
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
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "double_click",
            "description": "Double-click at current cursor position.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text at current cursor position. Existing text in the field is automatically selected and replaced.",
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
            "description": "Press a key or combination: Return, Tab, Escape, BackSpace, ctrl+a, ctrl+c, etc.",
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
            "description": "Scroll at current cursor position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "description": "Scroll steps (default 3)"},
                },
                "required": ["direction"],
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
                    "from_target": {"type": "string", "description": "Element to drag from"},
                    "to_target": {"type": "string", "description": "Element to drag to"},
                },
                "required": ["from_target", "to_target"],
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
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
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
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default: left"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for the screen to update (page load, animation, async operation). Returns a fresh screenshot after the delay.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "Seconds to wait (default 2, max 10)"},
                },
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
            "description": "Cannot proceed, need help.",
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
    frame: "np.ndarray | None" = None,
    hint: str | None = None,
    zoom: int | None = None,
    cursor_xy: "tuple[int, int] | None" = None,
    save_dir: str | None = None,
    step_num: int | None = None,
) -> dict:
    """Use UI-TARS to position cursor on a target element with iterative refinement.

    Flow:
    1. Capture screenshot -> UI-TARS grounds target -> get initial (x, y)
    2. Iterative narrow (RegionFocus): crop 300px around prediction, re-ground -> crop 150px, re-ground
    3. Move cursor to refined position (smooth animation, no click)
    4. Capture new screenshot with cursor overlay visible
    5. Re-ground same target on new screenshot -- if prediction shifted >30px, follow it
    6. Repeat steps 3-5 up to max_rounds times

    When ``zoom`` is set (10-100) and ``cursor_xy`` is provided, skips full-frame
    grounding and instead crops to zoom% of screen width centered on cursor_xy,
    then grounds on the crop for higher precision on small UI elements.

    When ``frame`` is provided, operates in benchmark mode: no X11 capture,
    no xdotool.  Cursor overlay is drawn on the provided frame instead of
    re-capturing from a live display.

    Returns: {"ok": True, "x": final_x, "y": final_y} or {"ok": False, "error": "..."}
    """
    import numpy as np
    from ..gui_agent.backends.uitars import UITARSBackend
    from ..gui_agent.agent import _iterative_narrow
    from ..capture.screen import capture_screen, capture_screen_with_cursor, frame_to_jpeg, draw_cursor_overlay

    _benchmark = frame is not None   # benchmark mode — no display interaction

    # Build save_callback for _iterative_narrow debug images
    _narrow_save_cb = None
    if save_dir and step_num is not None:
        import os as _os
        from PIL import Image as _Image
        def _narrow_save_cb(round_idx, crop_frame, grounding_result):
            path = _os.path.join(save_dir, f"step_{step_num}_narrow_{round_idx}.jpg")
            _Image.fromarray(crop_frame).save(path, quality=80)

    backend = UITARSBackend()
    loop = asyncio.get_event_loop()

    # Step 1: Initial grounding on clean screenshot
    if not _benchmark:
        frame = await loop.run_in_executor(None, capture_screen, display)
    if frame is None:
        return {"ok": False, "error": "Failed to capture screen"}

    screen_h, screen_w = frame.shape[:2]
    grounding_model = config.UITARS_OPENROUTER_MODEL
    ground_desc = f"{target} (hint: {hint})" if hint else target
    grounding = None  # set by zoom path or full-frame path below

    # Supervisor-guided zoom: crop to zoom% of screen centered on current cursor
    if zoom and cursor_xy and cursor_xy[0] is not None:
        radius = int(screen_w * zoom / 100 / 2)
        cx, cy = cursor_xy
        x1 = max(0, cx - radius)
        y1 = max(0, cy - radius)
        x2 = min(screen_w, cx + radius)
        y2 = min(screen_h, cy + radius)
        crop_frame = frame[y1:y2, x1:x2]
        crop_w, crop_h = x2 - x1, y2 - y1
        if crop_w < 20 or crop_h < 20:
            log.warning(f"Zoom crop too small ({crop_w}x{crop_h}), falling back to full frame")
        else:
            crop_jpeg = await loop.run_in_executor(None, frame_to_jpeg, crop_frame, config.GROUNDING_MAX_DIM, config.GROUNDING_JPEG_QUALITY)
            grounding = await backend.ground(ground_desc, crop_jpeg, image_size=(crop_w, crop_h))
            if grounding is not None:
                from ..gui_agent.types import GroundingResult
                # Map crop-local coords back to screen coords
                grounding = GroundingResult(x=x1 + grounding.x, y=y1 + grounding.y)
                # Iterative narrow on the crop for further precision
                grounding_local = GroundingResult(x=grounding.x - x1, y=grounding.y - y1)
                grounding_local = await _iterative_narrow(backend, ground_desc, crop_frame, grounding_local, loop, save_callback=_narrow_save_cb)
                grounding = GroundingResult(x=x1 + grounding_local.x, y=y1 + grounding_local.y)
                log.info(f"Zoom refinement ({zoom}%): {target} -> ({grounding.x}, {grounding.y})")
                # Fall through to convergence loop below with this grounding
            else:
                log.warning(f"Zoom grounding returned None, falling back to full frame")
                zoom = None  # clear so we fall through to full-frame path

    # Full-frame grounding (skipped if zoom path already produced a grounding)
    if not (zoom and grounding is not None):
        if config.MVP_ENABLED:
            from ..gui_agent.agent import _multiview_ground
            grounding = await _multiview_ground(backend, ground_desc, frame, loop)
        else:
            jpeg = await loop.run_in_executor(None, frame_to_jpeg, frame, config.GROUNDING_MAX_DIM, config.GROUNDING_JPEG_QUALITY)
            grounding = await backend.ground(ground_desc, jpeg, image_size=(screen_w, screen_h))
        if grounding is None:
            if session:
                session.record_grounding(target, grounding_model, 0, 0, round_n=0, error=f"Could not find: {target}")
            return {"ok": False, "error": f"Could not find: {target}"}

        log.info(f"Initial grounding: {target} -> ({grounding.x}, {grounding.y})")
        # Step 2: Iterative narrow (RegionFocus-style crop zoom) on clean screenshot
        grounding = await _iterative_narrow(backend, ground_desc, frame, grounding, loop, save_callback=_narrow_save_cb)
        log.info(f"After narrowing: {target} -> ({grounding.x}, {grounding.y})")

    x, y = grounding.x, grounding.y
    if session:
        session.record_grounding(target, grounding_model, x, y, round_n=0)
    log.info(f"Final grounding: {target} -> ({x}, {y})")

    # Move cursor to final position (skip in benchmark mode)
    if not _benchmark:
        await loop.run_in_executor(None, _smooth_mousemove, x, y, display)

    # Warn if target is near screen edge (high risk of misclick onto taskbar/panel)
    EDGE_MARGIN = 30
    edge_warning = ""
    if y >= screen_h - EDGE_MARGIN:
        edge_warning = " WARNING: target is at the bottom edge of the screen — click may hit the taskbar. Consider scrolling down first or using a keyboard shortcut."
    elif y <= EDGE_MARGIN:
        edge_warning = " WARNING: target is at the top edge of the screen — click may hit the panel. Consider scrolling up first."
    elif x >= screen_w - EDGE_MARGIN:
        edge_warning = " WARNING: target is at the right edge of the screen."
    elif x <= EDGE_MARGIN:
        edge_warning = " WARNING: target is at the left edge of the screen."

    return {"ok": True, "x": x, "y": y, "edge_warning": edge_warning}


# ── Independent done-verification ────────────────────────────────

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

_VERDICT_TOOL = {
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
                            "criterion": {"type": "string"},
                            "pass": {"type": "boolean"},
                            "evidence": {"type": "string"},
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


async def _verify_done(
    instruction: str, screenshot_b64: str, model: str, api_key: str,
    client: httpx.AsyncClient,
) -> dict:
    """Independent verification that a task is complete. Returns {"pass": bool, "reason": str}."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/openclaw/detm",
        "X-Title": "DETM Verifier",
    }
    try:
        # Step 1: Generate checklist
        resp1 = await client.post(
            f"{_OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _VERIFIER_SYSTEM},
                    {"role": "user", "content": (
                        f"The agent was given this task:\n\n"
                        f"\"{instruction}\"\n\n"
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
            return {"pass": True, "reason": "verifier checklist call failed"}
        checklist = (resp1.json()["choices"][0]["message"].get("content") or "").strip()
        log.info(f"VERIFY CHECKLIST: {checklist[:300]}")

        # Step 2: Check criteria via structured tool call
        resp2 = await client.post(
            f"{_OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _VERIFIER_SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": (
                            f"Task: \"{instruction}\"\n\n"
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
                "tools": [_VERDICT_TOOL],
                "tool_choice": {"type": "function", "function": {"name": "verdict"}},
                "reasoning": {"effort": "high"},
                "max_tokens": 2000,
            },
        )
        if resp2.status_code != 200:
            return {"pass": True, "reason": "verifier check call failed"}

        msg2 = resp2.json()["choices"][0]["message"]
        tool_calls = msg2.get("tool_calls") or []
        if not tool_calls:
            return {"pass": True, "reason": "verifier returned no tool call"}

        verdict_args = json.loads(tool_calls[0]["function"]["arguments"])
        log.info(f"VERIFY RESULT: {json.dumps(verdict_args, indent=2)[:400]}")
        return {"pass": verdict_args.get("overall_pass", True), "reason": verdict_args.get("failure_reason", "")}

    except Exception as e:
        log.warning(f"Verifier error: {e}")
        return {"pass": True, "reason": f"verifier error: {e}"}


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
        # Benchmark mode callbacks (all None = production path unchanged):
        get_screenshot=None,         # () -> (jpeg_b64: str, cursor_pos: tuple|None)
        execute_override=None,       # (name: str, args: dict) -> str
        display_size_override=None,  # (width, height)
    ) -> dict:
        api_key = config.OPENROUTER_API_KEY
        if not api_key:
            return {"error": "OPENROUTER_API_KEY not set", "success": False, "actions_taken": 0}

        self._done_verified = False
        _benchmark = get_screenshot is not None
        _do_execute = execute_override or (lambda name, args: execute_action(name, args, display))

        model = config.OPENROUTER_LIVE_MODEL
        disp_w, disp_h = display_size_override or _get_display_size(display)
        sid = session.id[:8] if session else "--------"

        system_text = _SYSTEM_PROMPT
        if context:
            system_text += f"\n\nContext:\n{context}"

        _dbg.log("LIVE", f"[{sid}] gui_agent: model={model} display={display} {disp_w}x{disp_h} task={task_id} timeout={timeout}s")

        import time as _time
        t_start = _time.time()

        MAX_TURNS = 60
        MAX_FORMAT_RETRIES = 10
        CONTEXT_WINDOW = 40
        actions_taken = 0
        action_turns = 0
        format_retries = 0
        last_usage_data = None
        cursor_x: int | None = None
        cursor_y: int | None = None
        _current_frame = None  # numpy array of latest screenshot (benchmark mode only)
        messages: list[dict] = [{"role": "system", "content": system_text}]

        def _decode_frame_from_b64(b64: str):
            """Decode JPEG base64 to numpy array for _refine_cursor benchmark mode."""
            import io, numpy as np
            from PIL import Image
            raw = base64.b64decode(b64)
            return np.array(Image.open(io.BytesIO(raw)).convert("RGB"))

        # First user message: instruction + initial screenshot
        if _benchmark:
            screenshot_b64, init_cursor = get_screenshot()
        else:
            screenshot_b64, init_cursor = await asyncio.get_running_loop().run_in_executor(
                None, _capture_jpeg_b64, display, session
            )
        if _benchmark and screenshot_b64:
            _current_frame = _decode_frame_from_b64(screenshot_b64)
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

                        # Context management: sliding window + strip old images
                        if len(messages) > CONTEXT_WINDOW + 2:
                            messages = messages[:2] + messages[-CONTEXT_WINDOW:]
                        for m in messages[:-1]:
                            if m.get("role") == "system":
                                continue
                            if isinstance(m.get("content"), list):
                                m["content"] = [c for c in m["content"] if c.get("type") != "image_url"]

                        # ── Pass 1: Thinking (text-only, no tools) ──
                        think_messages = messages + [
                            {"role": "user", "content": (
                                "Before acting, think step by step:\n"
                                "1. What do you see on screen right now?\n"
                                "2. What is the current state relative to the task goal?\n"
                                "3. What specific action should you take next and why?\n"
                                "Be concise (2-4 sentences)."
                            )},
                        ]
                        think_resp = await client.post(
                            f"{_OPENROUTER_BASE}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                                "HTTP-Referer": "https://github.com/openclaw/detm",
                                "X-Title": "DETM gui_agent",
                            },
                            json={
                                "model": model,
                                "messages": think_messages,
                                "max_tokens": 300,
                            },
                        )
                        thinking = ""
                        if think_resp.status_code == 200:
                            think_data = think_resp.json()
                            thinking = (think_data["choices"][0]["message"].get("content") or "").strip()
                            if thinking:
                                _dbg.log("LIVE", f"[{sid}] thinking: {thinking[:200]}")

                        # ── Pass 2: Action (with tools, thinking as assistant message) ──
                        action_messages = list(messages)
                        if thinking:
                            action_messages.append({"role": "assistant", "content": thinking})

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
                                "messages": action_messages,
                                "tools": _TOOLS,
                                "tool_choice": "auto",
                                "max_tokens": 1000,
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
                        narration = thinking or _extract_model_text(msg)
                        messages.append({
                            "role": "assistant",
                            "content": _extract_model_text(msg) or "",
                            "tool_calls": [tc],
                        })

                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except Exception:
                            _dbg.log("LIVE", f"[{sid}] bad tool args, requesting retry")
                            messages.append({"role": "tool", "tool_call_id": tc["id"],
                                             "content": "error: could not parse tool arguments. Try again with valid JSON."})
                            format_retries += 1
                            continue
                        tc_id = tc["id"]

                        # Pop thought if model still sends it (backward compat), use narration as primary
                        fn_args.pop("thought", None)
                        thought = (narration or "").strip()
                        if thought:
                            _dbg.log("LIVE", f"[{sid}] thought: {thought[:120]}")
                        if session and thought:
                            session.record_model_text(thought)
                        _dbg.log("LIVE", f"[{sid}] {fn_name}({', '.join(f'{k}={v}' for k, v in fn_args.items())})")

                        if session:
                            session.record_tool_call(fn_name, fn_args, tc_id)

                        # ── Terminal actions ─────────────────────────────
                        if fn_name == "done":
                            success = bool(fn_args.get("success", True))
                            summary = str(fn_args.get("summary", ""))

                            # Independent verification on success claims
                            if success and not getattr(self, "_done_verified", False):
                                self._done_verified = True
                                # Capture fresh screenshot for verification
                                if _benchmark:
                                    verify_b64, _ = get_screenshot()
                                else:
                                    verify_b64, _ = await asyncio.get_running_loop().run_in_executor(
                                        None, _capture_jpeg_b64, display, session
                                    )
                                if verify_b64:
                                    verdict = await _verify_done(
                                        instruction, verify_b64, model, api_key, client
                                    )
                                    if verdict["pass"]:
                                        _dbg.log("LIVE", f"[{sid}] verification PASSED: {verdict.get('reason', '')[:120]}")
                                    else:
                                        _dbg.log("LIVE", f"[{sid}] verification FAILED: {verdict.get('reason', '')[:200]}")
                                        self._done_verified = False
                                        messages.append({"role": "tool", "tool_call_id": tc_id,
                                            "content": f"Verification FAILED. The task is NOT complete. "
                                                       f"Issue: {verdict.get('reason', 'unknown')}. "
                                                       f"Continue working to fix this."})
                                        continue

                            self._done_verified = False
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

                        # ── move_to: internal verification loop (free, not counted as action) ──
                        if fn_name == "move_to":
                            target_desc = fn_args.get("target", "")
                            move_hint = fn_args.get("hint")
                            move_zoom = fn_args.get("zoom")
                            _rc_frame = _current_frame if _benchmark else None
                            _cursor_xy = (cursor_x, cursor_y) if cursor_x is not None else None
                            result = await _refine_cursor(target_desc, display, session, frame=_rc_frame, hint=move_hint, zoom=move_zoom, cursor_xy=_cursor_xy)
                            if result["ok"]:
                                cursor_x, cursor_y = result["x"], result["y"]
                                action_result = f"cursor moved to ({result['x']}, {result['y']}), ready to verify"

                                # Capture overlay screenshot for verification
                                if _benchmark:
                                    from ..capture.screen import draw_cursor_overlay as _draw_overlay
                                    import numpy as np
                                    _overlay_frame = _draw_overlay(_current_frame.copy(), result["x"], result["y"])
                                    import io as _io
                                    _overlay_img = __import__('PIL').Image.fromarray(_overlay_frame)
                                    _buf = _io.BytesIO()
                                    _overlay_img.save(_buf, format="JPEG", quality=85)
                                    _overlay_b64 = base64.b64encode(_buf.getvalue()).decode()
                                else:
                                    # Live mode: capture real screenshot with cursor visible
                                    _overlay_b64, _ = await asyncio.get_running_loop().run_in_executor(
                                        None, _capture_jpeg_b64, display, session
                                    )
                            else:
                                action_result = f"error: {result['error']}"
                                _overlay_b64 = None

                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})
                            if _overlay_b64:
                                messages.append({"role": "user", "content": [
                                    {"type": "text", "text": "[screenshot with cursor overlay]"},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_overlay_b64}"}},
                                ]})

                            if session:
                                session.record_tool_response(fn_name, tc_id, action_result)
                            # Don't count as action_turn, don't take post-action screenshot — continue loop
                            continue

                        # ── wait: pause and re-capture (free, not counted) ──
                        if fn_name == "wait":
                            wait_secs = min(max(float(fn_args.get("seconds", 2)), 0.5), 10)
                            if not _benchmark:
                                await asyncio.sleep(wait_secs)
                            action_result = f"waited {wait_secs}s"
                            messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

                            if _benchmark:
                                screenshot_b64, shot_cursor = get_screenshot()
                            else:
                                screenshot_b64, shot_cursor = await asyncio.get_running_loop().run_in_executor(
                                    None, _capture_jpeg_b64, display, session
                                )
                            if _benchmark and screenshot_b64:
                                _current_frame = _decode_frame_from_b64(screenshot_b64)
                            if shot_cursor:
                                cursor_x, cursor_y = shot_cursor
                            messages.append({"role": "user", "content": [
                                {"type": "text", "text": "[current screenshot]"},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                            ]})
                            if session:
                                session.record_tool_response(fn_name, tc_id, action_result)
                            continue  # Don't count as action turn

                        # ── GUI actions (counted as steps) ──────────────────
                        action_turns += 1
                        action_result = "ok"

                        if fn_name == "click":
                            button = fn_args.get("button", "left")
                            if fn_args.get("target"):
                                action_result = "error: click() has no 'target' parameter. Use move_to(target=\"...\") first to position the cursor, verify the cursor overlay, then call click()."
                            elif cursor_x is not None:
                                action_result = _do_execute("click", {"x": cursor_x, "y": cursor_y, "button": button})
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "double_click":
                            if fn_args.get("target"):
                                action_result = "error: double_click() has no 'target' parameter. Use move_to(target=\"...\") first, then double_click()."
                            elif cursor_x is not None:
                                action_result = _do_execute("double_click", {"x": cursor_x, "y": cursor_y})
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "type_text":
                            clear = fn_args.get("clear_first", True)
                            if clear and cursor_x is not None:
                                # Triple-click to select all in field before typing
                                _do_execute("click", {"x": cursor_x, "y": cursor_y, "clicks": 3})
                                import time; time.sleep(0.05)
                            action_result = _do_execute("type_text", {"text": fn_args.get("text", "")})

                        elif fn_name == "key_press":
                            action_result = _do_execute("key_press", {"key": fn_args.get("key", "Return")})

                        elif fn_name == "scroll":
                            sx = cursor_x if cursor_x is not None else disp_w // 2
                            sy = cursor_y if cursor_y is not None else disp_h // 2
                            action_result = _do_execute("scroll", {
                                "x": sx, "y": sy,
                                "direction": fn_args.get("direction", "down"),
                                "amount": fn_args.get("amount", 3),
                            })

                        elif fn_name == "drag":
                            _rc_frame = _current_frame if _benchmark else None
                            from_result = await _refine_cursor(fn_args.get("from_target", ""), display, session, frame=_rc_frame)
                            if not from_result["ok"]:
                                action_result = f"error: could not find drag start: {from_result['error']}"
                            else:
                                to_result = await _refine_cursor(fn_args.get("to_target", ""), display, session, frame=_rc_frame)
                                if not to_result["ok"]:
                                    action_result = f"error: could not find drag end: {to_result['error']}"
                                else:
                                    action_result = _do_execute("drag", {
                                        "start_x": from_result["x"], "start_y": from_result["y"],
                                        "end_x": to_result["x"], "end_y": to_result["y"],
                                    })

                        elif fn_name == "mouse_down":
                            button = fn_args.get("button", "left")
                            if cursor_x is not None:
                                action_result = _do_execute("mouse_down", {"x": cursor_x, "y": cursor_y, "button": button})
                            else:
                                action_result = "error: no cursor position -- call move_to first"

                        elif fn_name == "mouse_up":
                            button = fn_args.get("button", "left")
                            action_result = _do_execute("mouse_up", {"button": button})

                        else:
                            action_result = f"error: unknown tool {fn_name}"

                        actions_taken += 1

                        if session:
                            session.record_tool_response(fn_name, tc_id, action_result)

                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": action_result})

                        # Settle + screenshot
                        settle = _SETTLE.get(fn_name, 0.08)
                        if not _benchmark:
                            await asyncio.sleep(settle)

                        t_shot0 = asyncio.get_running_loop().time()
                        if _benchmark:
                            screenshot_b64, shot_cursor = get_screenshot()
                        else:
                            screenshot_b64, shot_cursor = await asyncio.get_running_loop().run_in_executor(
                                None, _capture_jpeg_b64, display, session
                            )
                        if _benchmark and screenshot_b64:
                            _current_frame = _decode_frame_from_b64(screenshot_b64)
                        if shot_cursor:
                            cursor_x, cursor_y = shot_cursor
                        t_shot1 = asyncio.get_running_loop().time()
                        _dbg.log(
                            "LIVE",
                            f"[{sid}] timing: api={api_ms:.0f}ms settle={settle*1000:.0f}ms shot={1000*(t_shot1-t_shot0):.0f}ms",
                        )

                        observe_content: list[dict] = [
                            {"type": "text", "text": "[current screenshot]"},
                        ]
                        if screenshot_b64:
                            observe_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                            })
                        messages.append({"role": "user", "content": observe_content})

                        # Context trimming now happens at top of loop

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
