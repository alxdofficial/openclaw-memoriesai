"""OmniParser V2 backend — YOLO + Florence-2 + Claude Haiku element picker (SoM grounding).

Flow:
  screenshot → YOLO (detect elements) → Florence-2 (caption each)
  → numbered overlay → Claude Haiku (pick element N) → center coordinates

Models auto-downloaded from microsoft/OmniParser-v2.0 on first use.
Cached at ~/.agentic-computer-use/models/omniparser/.
"""
import io, re, base64, asyncio, logging, threading
import httpx
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult

log = logging.getLogger(__name__)
_BOX_COLORS = ["#FF3366", "#33AAFF", "#33FF99", "#FFAA33", "#AA33FF",
               "#FF9933", "#FF6633", "#33FFEE", "#FF33AA", "#AAFFAA"]

# Persistent HTTP client for Claude picker — reused to avoid TLS handshake overhead
_picker_client: httpx.AsyncClient | None = None


def _get_picker_client() -> httpx.AsyncClient:
    global _picker_client
    if _picker_client is None or _picker_client.is_closed:
        _picker_client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
    return _picker_client


class OmniParserBackend(GUIAgentBackend):
    _yolo = None
    _florence_model = None
    _florence_processor = None
    _load_lock = threading.Lock()

    def _ensure_models(self):
        with OmniParserBackend._load_lock:
            if OmniParserBackend._yolo is not None:
                return
            import torch
            from ultralytics import YOLO
            from transformers import AutoProcessor, AutoModelForCausalLM
            from huggingface_hub import snapshot_download

            model_dir = config.DATA_DIR / "models" / "omniparser"
            yolo_path = model_dir / "icon_detect" / "model.pt"
            caption_dir = model_dir / "icon_caption"

            if not yolo_path.exists() or not caption_dir.exists():
                log.info("Downloading OmniParser V2 from microsoft/OmniParser-v2.0 (~1.1GB)...")
                snapshot_download(
                    repo_id="microsoft/OmniParser-v2.0",
                    allow_patterns=["icon_detect/**", "icon_caption/**"],
                    local_dir=str(model_dir),
                    local_dir_use_symlinks=False,
                )

            device = "cuda" if torch.cuda.is_available() else "cpu"
            log.info(f"Loading OmniParser models on {device}...")
            OmniParserBackend._yolo = YOLO(str(yolo_path))
            dtype = torch.float16 if device == "cuda" else torch.float32
            OmniParserBackend._florence_model = (
                AutoModelForCausalLM.from_pretrained(
                    str(caption_dir), trust_remote_code=True, torch_dtype=dtype
                ).to(device).eval()
            )
            OmniParserBackend._florence_processor = AutoProcessor.from_pretrained(
                str(caption_dir), trust_remote_code=True
            )
            log.info("OmniParser models loaded.")

    def _detect_and_caption(self, screenshot: bytes) -> tuple[list[dict], bytes]:
        import torch
        from PIL import Image, ImageDraw
        self._ensure_models()

        img = Image.open(io.BytesIO(screenshot)).convert("RGB")
        W, H = img.size
        results = OmniParserBackend._yolo(
            img, conf=config.OMNIPARSER_BBOX_THRESHOLD,
            iou=config.OMNIPARSER_IOU_THRESHOLD, verbose=False,
        )
        boxes = results[0].boxes.xyxy.cpu().numpy().tolist() if results and results[0].boxes else []

        device = next(OmniParserBackend._florence_model.parameters()).device
        dtype = next(OmniParserBackend._florence_model.parameters()).dtype

        # Collect valid boxes and crops for batched Florence-2 inference
        valid_boxes = []
        crops = []
        for (x1, y1, x2, y2) in boxes:
            x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(W, int(x2)), min(H, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            valid_boxes.append((x1, y1, x2, y2))
            crops.append(img.crop((x1, y1, x2, y2)))

        elements = []
        if crops:
            # Single batched forward pass instead of N serial passes — much faster on GPU
            inputs = OmniParserBackend._florence_processor(
                text=["<CAPTION>"] * len(crops),
                images=crops,
                return_tensors="pt",
                padding="longest",
            )
            inputs = {k: (v.to(device, dtype) if v.is_floating_point() else v.to(device))
                      for k, v in inputs.items()}
            with torch.no_grad():
                ids = OmniParserBackend._florence_model.generate(
                    **inputs, max_new_tokens=40, num_beams=1
                )
            captions = OmniParserBackend._florence_processor.batch_decode(
                ids, skip_special_tokens=True
            )
            for i, ((x1, y1, x2, y2), caption) in enumerate(zip(valid_boxes, captions)):
                elements.append({
                    "bbox": (x1, y1, x2, y2),
                    "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                    "label": caption.strip() or f"element {i + 1}",
                })

        annotated = img.copy()
        draw = ImageDraw.Draw(annotated)
        for i, el in enumerate(elements):
            x1, y1, x2, y2 = el["bbox"]
            color = _BOX_COLORS[i % len(_BOX_COLORS)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
            num = str(i + 1)
            draw.rectangle([x1, y1 - 18, x1 + len(num) * 9 + 6, y1], fill=color)
            draw.text((x1 + 3, y1 - 15), num, fill="white")

        buf = io.BytesIO()
        annotated.save(buf, "JPEG", quality=82)
        return elements, buf.getvalue()

    async def _pick_element(self, description: str, annotated: bytes, elements: list[dict]) -> int | None:
        api_key = config.CLAUDE_API_KEY
        if not api_key:
            return _label_match(description, elements)

        element_list = "\n".join(f"[{i + 1}] {el['label']}" for i, el in enumerate(elements))
        payload = {
            "model": config.OMNIPARSER_PICKER_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(annotated).decode()}},
                {"type": "text", "text": (
                    f"Elements detected:\n{element_list}\n\n"
                    f"Which numbered element to: {description}\n"
                    "Reply with ONLY the number."
                )},
            ]}],
        }
        try:
            resp = await _get_picker_client().post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={"x-api-key": api_key},
            )
            resp.raise_for_status()
            m = re.search(r'\d+', resp.json()["content"][0]["text"])
            return int(m.group()) if m else None
        except Exception as e:
            log.error(f"OmniParser picker failed: {e}")
            return _label_match(description, elements)

    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        loop = asyncio.get_event_loop()
        try:
            elements, annotated = await loop.run_in_executor(
                None, self._detect_and_caption, screenshot
            )
        except Exception as e:
            log.error(f"OmniParser detection failed: {e}")
            return None

        if not elements:
            return None

        num = await self._pick_element(description, annotated, elements)
        if num is None or num < 1 or num > len(elements):
            return None

        el = elements[num - 1]
        log.debug(f"OmniParser: '{description}' -> [{num}] '{el['label']}' at ({el['cx']},{el['cy']})")
        return GroundingResult(x=el["cx"], y=el["cy"], confidence=0.88,
                               description=description, element_text=f"[{num}] {el['label']}")

    async def check_health(self) -> dict:
        model_dir = config.DATA_DIR / "models" / "omniparser"
        loaded = OmniParserBackend._yolo is not None
        downloaded = ((model_dir / "icon_detect" / "model.pt").exists()
                      and (model_dir / "icon_caption").exists())
        return {
            "ok": loaded or downloaded, "backend": "omniparser",
            "models": "loaded" if loaded else ("downloaded" if downloaded else "not_downloaded"),
            "picker_model": config.OMNIPARSER_PICKER_MODEL,
            "picker_api_key": bool(config.CLAUDE_API_KEY),
        }


def _label_match(description: str, elements: list[dict]) -> int | None:
    """Fuzzy fallback: element with most word overlap with description."""
    desc_words = set(description.lower().split())
    best, best_idx = 0, None
    for i, el in enumerate(elements):
        score = len(desc_words & set(el["label"].lower().split()))
        if score > best:
            best, best_idx = score, i + 1
    return best_idx
