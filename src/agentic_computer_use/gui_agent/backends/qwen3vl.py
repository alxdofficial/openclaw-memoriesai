"""Qwen3-VL backend — grounding via OpenRouter.

Qwen3-VL outputs coordinates in a normalized 0-1000 space regardless of
input image size.  Conversion to pixel coords:
    pixel_x = coord_x / 1000 * screen_width
    pixel_y = coord_y / 1000 * screen_height
"""
import base64
import json
import re
import logging
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult
from .uitars import _get_or_client, OPENROUTER_URL

log = logging.getLogger(__name__)

OPENROUTER_MODEL = config.QWEN3VL_OPENROUTER_MODEL

_SYSTEM_PROMPT = "The screen's resolution is 1000x1000."

_GROUNDING_PROMPT = "Click on: {instruction}"

_REFINE_PROMPT = (
    "The red circle with crosshair at ({cursor_x}, {cursor_y}) is the "
    "current cursor position. Click on the precise location of: "
    "{instruction}{hint_text}"
)


class Qwen3VLBackend(GUIAgentBackend):
    """Qwen3-VL-32B grounding via OpenRouter."""

    @property
    def provider(self) -> str:
        return "openrouter"

    async def ground(
        self,
        description: str,
        screenshot: bytes,
        image_size: tuple[int, int] = (1920, 1080),
        cursor_pos: tuple[int, int] | None = None,
        hint: str | None = None,
    ) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()
        orig_w, orig_h = image_size

        if cursor_pos:
            # Convert cursor_pos from screen space to 0-1000 space for the prompt
            cx = int(cursor_pos[0] / orig_w * 1000)
            cy = int(cursor_pos[1] / orig_h * 1000)
            hint_text = f" Hint: {hint}" if hint else ""
            prompt_text = _REFINE_PROMPT.format(
                instruction=description, cursor_x=cx, cursor_y=cy, hint_text=hint_text,
            )
        else:
            prompt_text = _GROUNDING_PROMPT.format(instruction=description)

        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": prompt_text},
                    ],
                },
            ],
            "max_tokens": 200,
            "temperature": 0.0,
        }

        try:
            resp = await _get_or_client().post(OPENROUTER_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            log.debug(f"OpenRouter Qwen3-VL response: {text}")

            from ... import usage as _usage
            _u = data.get("usage", {})
            _usage.record_nowait(
                provider="openrouter", model=OPENROUTER_MODEL,
                input_tokens=_u.get("prompt_tokens", 0),
                output_tokens=_u.get("completion_tokens", 0),
            )

            coords = _parse_qwen3vl_coordinates(text, image_size)
            if coords:
                return GroundingResult(
                    x=coords[0], y=coords[1],
                    confidence=0.8, description=description,
                    element_text=text,
                )
            return None
        except Exception as e:
            log.error(f"Qwen3-VL grounding failed: {e}")
            return None

    async def check_health(self) -> dict:
        try:
            resp = await _get_or_client().get(
                "https://openrouter.ai/api/v1/models",
                timeout=5.0,
            )
            resp.raise_for_status()
            return {"ok": True, "backend": "qwen3vl", "provider": "openrouter", "model": OPENROUTER_MODEL}
        except Exception as e:
            return {"ok": False, "backend": "qwen3vl", "provider": "openrouter", "error": str(e)}


def _parse_qwen3vl_coordinates(
    text: str,
    orig_size: tuple[int, int] = (1920, 1080),
) -> tuple[int, int] | None:
    """Parse Qwen3-VL output and convert from 0-1000 normalized space to pixels.

    Supported formats:
      - <tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [x, y]}}</tool_call>
      - Bare JSON with "coordinate": [x, y]
      - (x, y) pattern — generic fallback
    """
    orig_w, orig_h = orig_size

    # Try <tool_call> block first
    tc_match = re.search(r'<tool_call>\s*(.+?)\s*</tool_call>', text, re.DOTALL)
    if tc_match:
        try:
            obj = json.loads(tc_match.group(1))
            args = obj.get("arguments", obj)
            coord = args.get("coordinate")
            if coord and len(coord) >= 2:
                px = int(float(coord[0]) / 1000 * orig_w)
                py = int(float(coord[1]) / 1000 * orig_h)
                px = max(0, min(px, orig_w - 1))
                py = max(0, min(py, orig_h - 1))
                log.debug(f"Qwen3-VL coords: norm=({coord[0]},{coord[1]}) pixel=({px},{py}) orig=({orig_w}x{orig_h})")
                return (px, py)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    # Try bare JSON with "coordinate" key
    json_match = re.search(r'\{[^{}]*"coordinate"\s*:\s*\[([^\]]+)\][^{}]*\}', text)
    if json_match:
        try:
            vals = [float(v.strip()) for v in json_match.group(1).split(",")]
            if len(vals) >= 2:
                px = int(vals[0] / 1000 * orig_w)
                py = int(vals[1] / 1000 * orig_h)
                px = max(0, min(px, orig_w - 1))
                py = max(0, min(py, orig_h - 1))
                log.debug(f"Qwen3-VL coords (bare JSON): norm=({vals[0]},{vals[1]}) pixel=({px},{py})")
                return (px, py)
        except (ValueError, IndexError):
            pass

    # Generic (x, y) fallback
    paren_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)', text)
    if paren_match:
        raw_x, raw_y = float(paren_match.group(1)), float(paren_match.group(2))
        px = int(raw_x / 1000 * orig_w)
        py = int(raw_y / 1000 * orig_h)
        px = max(0, min(px, orig_w - 1))
        py = max(0, min(py, orig_h - 1))
        log.debug(f"Qwen3-VL coords (fallback): norm=({raw_x},{raw_y}) pixel=({px},{py})")
        return (px, py)

    # Try [x, y] array pattern
    bracket_match = re.search(r'\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]', text)
    if bracket_match:
        raw_x, raw_y = float(bracket_match.group(1)), float(bracket_match.group(2))
        px = int(raw_x / 1000 * orig_w)
        py = int(raw_y / 1000 * orig_h)
        px = max(0, min(px, orig_w - 1))
        py = max(0, min(py, orig_h - 1))
        log.debug(f"Qwen3-VL coords (bracket fallback): norm=({raw_x},{raw_y}) pixel=({px},{py})")
        return (px, py)

    log.warning(f"Could not parse Qwen3-VL coordinates from: {text[:200]}")
    return None
