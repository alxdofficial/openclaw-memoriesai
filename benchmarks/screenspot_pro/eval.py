#!/usr/bin/env python3
"""ScreenSpot-Pro benchmark evaluation for DETM grounding pipeline.

Configs:
  A) UI-TARS-1.5-7B standalone (single API call per sample)
  B) DETM iterative narrowing (initial + 2 crop-zoom rounds)
  C) DETM full refinement (narrowing + convergence loop with cursor overlay)

Usage:
  PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config A
  PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config B
  PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config C
  PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config C --limit 10   # smoke test
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import io

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


def _frame_to_jpeg_fullres(frame: np.ndarray, quality: int = 85) -> bytes:
    """Encode frame to JPEG at full resolution (no downscaling).

    For benchmarking we must send the original resolution so the model's
    coordinate space matches the ground truth bounding boxes.
    """
    img = Image.fromarray(frame)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def load_dataset(limit: int | None = None):
    """Load ScreenSpot-Pro from HuggingFace."""
    from datasets import load_dataset as hf_load
    ds = hf_load("lmms-lab/ScreenSpot-Pro", split="train")
    if limit:
        ds = ds.select(range(min(limit, len(ds))))
    return ds


def point_in_box(pred_x: float, pred_y: float, bbox: list, img_size: list) -> bool:
    """Check if normalized (pred_x, pred_y) falls inside bbox (pixel coords)."""
    w, h = img_size
    x1, y1, x2, y2 = bbox[0] / w, bbox[1] / h, bbox[2] / w, bbox[3] / h
    return x1 <= pred_x <= x2 and y1 <= pred_y <= y2


async def eval_config_a(sample: dict) -> dict:
    """Config A: UI-TARS standalone -- single shot, no narrowing."""
    from agentic_computer_use.gui_agent.backends.uitars import UITARSBackend

    backend = UITARSBackend()
    frame = np.array(sample["image"])
    img_w, img_h = sample["img_size"]

    jpeg = _frame_to_jpeg_fullres(frame)
    result = await backend.ground(sample["instruction"], jpeg, image_size=(img_w, img_h))

    if result is None:
        return {"pred_x": None, "pred_y": None, "correct": False, "error": "no_prediction"}

    pred_x = result.x / img_w
    pred_y = result.y / img_h
    correct = point_in_box(pred_x, pred_y, sample["bbox"], sample["img_size"])
    return {"pred_x": pred_x, "pred_y": pred_y, "correct": correct}


async def eval_config_b(sample: dict) -> dict:
    """Config B: UI-TARS + iterative narrowing (crop-zoom, no convergence loop)."""
    from agentic_computer_use.gui_agent.backends.uitars import UITARSBackend
    from agentic_computer_use.gui_agent.agent import _iterative_narrow

    backend = UITARSBackend()
    loop = asyncio.get_event_loop()
    frame = np.array(sample["image"])
    img_w, img_h = sample["img_size"]

    jpeg = _frame_to_jpeg_fullres(frame)
    initial = await backend.ground(sample["instruction"], jpeg, image_size=(img_w, img_h))

    if initial is None:
        return {"pred_x": None, "pred_y": None, "correct": False, "error": "no_prediction"}

    narrowed = await _iterative_narrow(backend, sample["instruction"], frame, initial, loop)

    pred_x = narrowed.x / img_w
    pred_y = narrowed.y / img_h
    correct = point_in_box(pred_x, pred_y, sample["bbox"], sample["img_size"])
    return {"pred_x": pred_x, "pred_y": pred_y, "correct": correct}


async def eval_config_c(sample: dict) -> dict:
    """Config C: Full DETM refinement (narrowing + convergence with cursor overlay)."""
    from agentic_computer_use.live_ui.openrouter import _refine_cursor

    frame = np.array(sample["image"])
    img_w, img_h = sample["img_size"]

    result = await _refine_cursor(
        target=sample["instruction"],
        display=None,      # no live display
        session=None,
        max_rounds=3,
        frame=frame,       # benchmark mode: injected screenshot
    )

    if not result["ok"]:
        return {"pred_x": None, "pred_y": None, "correct": False, "error": result.get("error", "")}

    pred_x = result["x"] / img_w
    pred_y = result["y"] / img_h
    correct = point_in_box(pred_x, pred_y, sample["bbox"], sample["img_size"])
    return {"pred_x": pred_x, "pred_y": pred_y, "correct": correct}


CONFIGS = {"A": eval_config_a, "B": eval_config_b, "C": eval_config_c}


async def main():
    parser = argparse.ArgumentParser(description="ScreenSpot-Pro eval")
    parser.add_argument("--config", choices=["A", "B", "C"], required=True,
                        help="A=UI-TARS standalone, B=+narrowing, C=+convergence")
    parser.add_argument("--limit", type=int, default=None, help="Limit samples (for smoke test)")
    parser.add_argument("--resume", type=str, default=None, help="Resume from partial results JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print(f"Loading ScreenSpot-Pro dataset...")
    ds = load_dataset(args.limit)
    print(f"Loaded {len(ds)} samples")

    eval_fn = CONFIGS[args.config]

    # Resume support: skip already-evaluated sample IDs
    done_ids: set = set()
    results: list = []
    if args.resume and Path(args.resume).exists():
        with open(args.resume) as f:
            results = json.load(f)
        done_ids = {r["id"] for r in results}
        print(f"Resuming: {len(done_ids)} samples already done")

    t0 = time.time()
    for i, sample in enumerate(ds):
        sid = sample["id"]
        if sid in done_ids:
            continue

        try:
            out = await eval_fn(sample)
        except Exception as e:
            log.error(f"[{i}/{len(ds)}] {sid}: {e}")
            out = {"pred_x": None, "pred_y": None, "correct": False, "error": str(e)}

        out.update({
            "id": sid,
            "instruction": sample["instruction"],
            "application": sample["application"],
            "platform": sample["platform"],
            "group": sample["group"],
            "ui_type": sample["ui_type"],
            "bbox": sample["bbox"],
            "img_size": sample["img_size"],
        })
        results.append(out)

        # Progress
        n_done = len(results)
        n_correct = sum(1 for r in results if r["correct"])
        elapsed = time.time() - t0
        rate = n_done / elapsed if elapsed > 0 else 0
        eta = (len(ds) - n_done) / rate if rate > 0 else 0
        print(f"[{n_done}/{len(ds)}] acc={n_correct/n_done:.3f} "
              f"({n_correct}/{n_done}) "
              f"rate={rate:.1f}/s ETA={eta/60:.0f}min -- {sid}: {'OK' if out['correct'] else 'MISS'}")

        # Save periodically
        if n_done % 50 == 0:
            _save(results, args.config)

    _save(results, args.config)
    _report(results, args.config)


def _save(results: list, config: str):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"config_{config}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved {len(results)} results to {path}")


def _report(results: list, config: str):
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    errors = sum(1 for r in results if r.get("error"))

    print(f"\n{'='*60}")
    print(f"Config {config}: {correct}/{total} = {correct/total:.3f} ({correct/total*100:.1f}%)")
    print(f"Errors (no prediction): {errors}")
    print(f"{'='*60}")

    # Breakdown by group
    groups = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        g = r["group"]
        groups[g]["total"] += 1
        if r["correct"]:
            groups[g]["correct"] += 1

    print(f"\nBy group:")
    for g, v in sorted(groups.items()):
        acc = v["correct"] / v["total"] if v["total"] else 0
        print(f"  {g:15s}: {v['correct']:4d}/{v['total']:4d} = {acc:.3f}")

    # Breakdown by ui_type
    types = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        t = r["ui_type"]
        types[t]["total"] += 1
        if r["correct"]:
            types[t]["correct"] += 1

    print(f"\nBy ui_type:")
    for t, v in sorted(types.items()):
        acc = v["correct"] / v["total"] if v["total"] else 0
        print(f"  {t:15s}: {v['correct']:4d}/{v['total']:4d} = {acc:.3f}")

    # Breakdown by platform
    platforms = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        p = r["platform"]
        platforms[p]["total"] += 1
        if r["correct"]:
            platforms[p]["correct"] += 1

    print(f"\nBy platform:")
    for p, v in sorted(platforms.items()):
        acc = v["correct"] / v["total"] if v["total"] else 0
        print(f"  {p:15s}: {v['correct']:4d}/{v['total']:4d} = {acc:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
