"""UI-TARS backend — dual-mode grounding via OpenRouter (cloud) or Ollama (local).

Uses the official GROUNDING_DOUBAO prompt from bytedance/UI-TARS.
With this prompt, UI-TARS outputs coordinates as pixel values in the
image space it receives.  To map back to original image coordinates:
    pixel_x = raw_x * (orig_w / jpeg_w)
    pixel_y = raw_y * (orig_h / jpeg_h)
"""
import base64
import io
import re
import httpx
import logging
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = config.UITARS_OPENROUTER_MODEL

# Official GROUNDING_DOUBAO prompt — exact copy from bytedance/UI-TARS prompt.py.
# This prompt makes the model output structured `Action: click(point='(x,y)')` with
# pixel coordinates in the image space.  No Thought/reasoning, just the action.
_GROUNDING_PROMPT = (
    "You are a GUI agent. You are given a task and your action history, with "
    "screenshots. You need to perform the next action to complete the task. "
    "\n\n## Output Format\n\nAction: ...\n\n\n## Action Space\n"
    "click(point='<point>x1 y1</point>'')\n\n## User Instruction\n{instruction}"
)

# Refinement prompt — used when cursor overlay is visible on the screenshot.
# Tells UI-TARS what the red circle is and asks it to correct the position.
_REFINE_PROMPT = (
    "You are a GUI agent. You are given a task and your action history, with "
    "screenshots. You need to perform the next action to complete the task. "
    "\n\nThe red circle with crosshair at ({cursor_x}, {cursor_y}) is the "
    "current cursor position. It may not be exactly on the target element. "
    "Click on the precise location of the target element.{hint_text}"
    "\n\n## Output Format\n\nAction: ...\n\n\n## Action Space\n"
    "click(point='<point>x1 y1</point>'')\n\n## User Instruction\n{instruction}"
)

# Persistent HTTP clients — reused across calls to avoid TLS handshake overhead
_or_client: httpx.AsyncClient | None = None  # OpenRouter
_ol_client: httpx.AsyncClient | None = None  # Ollama


def _get_or_client() -> httpx.AsyncClient:
    global _or_client
    if _or_client is None or _or_client.is_closed:
        _or_client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
        )
    return _or_client


def _get_ol_client() -> httpx.AsyncClient:
    global _ol_client
    if _ol_client is None or _ol_client.is_closed:
        _ol_client = httpx.AsyncClient(timeout=120.0)
    return _ol_client


class UITARSBackend(GUIAgentBackend):
    """UI-TARS-1.5-7B grounding via OpenRouter (cloud) or Ollama (local)."""

    @property
    def _use_openrouter(self) -> bool:
        return bool(config.OPENROUTER_API_KEY)

    @property
    def provider(self) -> str:
        return "openrouter" if self._use_openrouter else "ollama"

    async def ground(
        self,
        description: str,
        screenshot: bytes,
        image_size: tuple[int, int] = (1920, 1080),
        cursor_pos: tuple[int, int] | None = None,
        hint: str | None = None,
    ) -> GroundingResult | None:
        if self._use_openrouter:
            return await self._ground_openrouter(description, screenshot, image_size, cursor_pos, hint)
        return await self._ground_ollama(description, screenshot, image_size, cursor_pos, hint)

    async def _ground_openrouter(
        self, description: str, screenshot: bytes, image_size: tuple[int, int],
        cursor_pos: tuple[int, int] | None = None,
        hint: str | None = None,
    ) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()
        if cursor_pos:
            # Convert cursor_pos from original screen space to JPEG pixel space
            # so the coordinates in the prompt match what UI-TARS sees visually.
            from PIL import Image
            jpeg_w, jpeg_h = Image.open(io.BytesIO(screenshot)).size
            orig_w, orig_h = image_size
            cx = int(cursor_pos[0] * jpeg_w / orig_w)
            cy = int(cursor_pos[1] * jpeg_h / orig_h)
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
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
            "max_tokens": 100,
            "temperature": 0.0,
        }

        try:
            resp = await _get_or_client().post(OPENROUTER_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            log.debug(f"OpenRouter UI-TARS response: {text}")

            from ... import usage as _usage
            _u = data.get("usage", {})
            _usage.record_nowait(
                provider="openrouter", model=OPENROUTER_MODEL,
                input_tokens=_u.get("prompt_tokens", 0),
                output_tokens=_u.get("completion_tokens", 0),
            )

            coords = _parse_coordinates(text, screenshot, image_size)
            if coords:
                return GroundingResult(
                    x=coords[0], y=coords[1],
                    confidence=0.8, description=description,
                    element_text=text,
                )
            return None
        except Exception as e:
            log.error(f"UI-TARS grounding failed (openrouter): {e}")
            return None

    async def _ground_ollama(
        self, description: str, screenshot: bytes, image_size: tuple[int, int],
        cursor_pos: tuple[int, int] | None = None,
        hint: str | None = None,
    ) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()
        model = config.UITARS_OLLAMA_MODEL
        if cursor_pos:
            from PIL import Image
            jpeg_w, jpeg_h = Image.open(io.BytesIO(screenshot)).size
            orig_w, orig_h = image_size
            cx = int(cursor_pos[0] * jpeg_w / orig_w)
            cy = int(cursor_pos[1] * jpeg_h / orig_h)
            hint_text = f" Hint: {hint}" if hint else ""
            prompt_text = _REFINE_PROMPT.format(
                instruction=description, cursor_x=cx, cursor_y=cy, hint_text=hint_text,
            )
        else:
            prompt_text = _GROUNDING_PROMPT.format(instruction=description)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt_text,
                    "images": [b64],
                }
            ],
            "stream": False,
            "keep_alive": config.UITARS_KEEP_ALIVE,
        }

        try:
            resp = await _get_ol_client().post(f"{config.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["message"]["content"].strip()
            log.debug(f"Ollama UI-TARS response: {text}")

            coords = _parse_coordinates(text, screenshot, image_size)
            if coords:
                return GroundingResult(
                    x=coords[0], y=coords[1],
                    confidence=0.8, description=description,
                    element_text=text,
                )
            return None
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response else ""
            if "not found" in body.lower() or e.response.status_code == 404:
                log.error(f"UI-TARS model not found. Run: ollama pull {model}")
            else:
                log.error(f"UI-TARS grounding failed (ollama): {e}")
            return None
        except Exception as e:
            log.error(f"UI-TARS grounding failed (ollama): {e}")
            return None

    async def check_health(self) -> dict:
        if self._use_openrouter:
            return await self._check_health_openrouter()
        return await self._check_health_ollama()

    async def _check_health_openrouter(self) -> dict:
        try:
            resp = await _get_or_client().get(
                "https://openrouter.ai/api/v1/models",
                timeout=5.0,
            )
            resp.raise_for_status()
            return {"ok": True, "backend": "uitars", "provider": "openrouter", "model": OPENROUTER_MODEL}
        except Exception as e:
            return {"ok": False, "backend": "uitars", "provider": "openrouter", "error": str(e)}

    async def _check_health_ollama(self) -> dict:
        model = config.UITARS_OLLAMA_MODEL
        try:
            resp = await _get_ol_client().get(f"{config.OLLAMA_URL}/api/tags", timeout=5.0)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            has_model = any(model in m for m in models)
            result = {
                "ok": has_model, "backend": "uitars", "provider": "ollama",
                "model": model, "has_model": has_model,
            }
            if not has_model:
                result["hint"] = f"Run: ollama pull {model}"
            return result
        except Exception as e:
            return {"ok": False, "backend": "uitars", "provider": "ollama", "error": str(e)}


def _parse_coordinates(
    text: str,
    jpeg_bytes: bytes,
    orig_size: tuple[int, int] = (1920, 1080),
) -> tuple[int, int] | None:
    """Parse coordinates from UI-TARS output and convert to original image pixels.

    With the GROUNDING_DOUBAO prompt, UI-TARS outputs pixel coordinates in
    the image space it received (the JPEG we sent).  We scale proportionally
    from JPEG dimensions to the original image dimensions.

    Supported output formats:
      - point='(x,y)' or point='<point>x y</point>' — official GROUNDING format
      - start_box='(x,y)' — COMPUTER_USE format (also handled)
      - (x, y) — fallback
    """
    from PIL import Image
    jpeg_w, jpeg_h = Image.open(io.BytesIO(jpeg_bytes)).size
    orig_w, orig_h = orig_size

    raw = _extract_raw_coords(text)
    if raw is None:
        return None

    raw_x, raw_y = raw

    # Scale from JPEG pixel space to original image space
    scale_x = orig_w / jpeg_w
    scale_y = orig_h / jpeg_h
    pixel_x = int(raw_x * scale_x)
    pixel_y = int(raw_y * scale_y)

    # Clamp to image bounds
    pixel_x = max(0, min(pixel_x, orig_w - 1))
    pixel_y = max(0, min(pixel_y, orig_h - 1))

    log.debug(f"UI-TARS coords: raw=({raw_x},{raw_y}) jpeg=({jpeg_w}x{jpeg_h}) "
              f"scale=({scale_x:.2f},{scale_y:.2f}) "
              f"pixel=({pixel_x},{pixel_y}) orig=({orig_w}x{orig_h})")

    return (pixel_x, pixel_y)


def _extract_raw_coords(text: str) -> tuple[float, float] | None:
    """Extract raw coordinate values from UI-TARS output text."""

    # start_box='(x,y)' or start_box='(x, y)' — official GROUNDING format
    box_match = re.search(r"start_box='?\((\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\)'?", text)
    if box_match:
        return float(box_match.group(1)), float(box_match.group(2))

    # <point>x y</point> — official point format (space-separated)
    point_match = re.search(r'<point>\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*</point>', text)
    if point_match:
        return float(point_match.group(1)), float(point_match.group(2))

    # <point>x, y</point> — point format with comma
    point_match2 = re.search(r'<point>\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*</point>', text)
    if point_match2:
        return float(point_match2.group(1)), float(point_match2.group(2))

    # (x, y) — generic fallback
    paren_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)', text)
    if paren_match:
        return float(paren_match.group(1)), float(paren_match.group(2))

    return None
