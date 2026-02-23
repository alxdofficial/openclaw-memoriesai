# Cursor Control — Research, Architecture & Roadmap

## Problem

Single-shot coordinate prediction is brittle. There are two failure modes:

1. **Main LLM guessing coordinates** — OpenClaw has no ground-truth pixel knowledge. Any `click(x, y)` passed directly from the LLM is a guess from memory. Eliminated: `gui_do` now requires natural language only.

2. **UI-TARS single-shot error** — Even with a purpose-trained grounding model, predicting a precise click target from a 960px-wide JPEG in one shot fails on small targets (trim handles, sliders, tiny checkboxes, dense timelines). The model's first guess is usually in the right region but can be off by 5–30px.

---

## Current Implementation: Iterative Narrowing

**arXiv: 2411.13591 — "Improved GUI Grounding via Iterative Narrowing"**
Training-free. Works as a wrapper around any existing grounding model. 23%+ accuracy improvement on mobile ScreenSpot.

### How it works

```
1. Capture full screen (960px JPEG, screen-resolution coords)
2. UI-TARS ground(instruction, full_jpeg, image_size=(screen_w, screen_h))
   → initial (x, y) in screen space
3. Crop 300px radius around (x, y), clamped to screen bounds
4. UI-TARS ground(instruction, crop_jpeg, image_size=(crop_w, crop_h))
   → (x_local, y_local) in crop-local space
5. x_final = crop_x1 + x_local
   y_final = crop_y1 + y_local
6. Execute click/drag/type at (x_final, y_final)
```

The second pass sees a ~600×600px zoomed crop instead of the full 960px screen. A 30px button that occupied 1.5% of the full image now occupies 5% of the crop — much easier to localize precisely.

### Coordinate space correctness

`_parse_coordinates()` in uitars.py previously hardcoded 1920×1080 when scaling normalized [0,1] model output. It now accepts `image_size=(w, h)` so:
- Full-frame pass: `image_size=(screen_w, screen_h)` — correct for any resolution
- Crop pass: `image_size=(crop_w, crop_h)` — correct mapping within the crop

### Configuration

```python
_NARROW_CROP_RADIUS = 300  # px in screen space (gui/agent.py)
```

Increase for larger targets (e.g., panels, windows). Decrease for dense UIs where the first pass is usually accurate (reduces false narrowing). A value of 300px means the crop is at most 600×600px, well under the 960px max_dim so no JPEG resize occurs.

---

## Tool Split: gui_do vs desktop_action

| Tool | Input | Use when |
|---|---|---|
| `gui_do` | Natural language only | You know *what* to click, not *where* |
| `desktop_action` | Explicit pixel coordinates | You have exact coords (from `desktop_look` or `gui_find`) |

**`gui_do` no longer accepts `click(x, y)` syntax.** Any raw coordinates passed to it will be treated as NL and sent to the grounding model (which will fail to parse them). Use `desktop_action` for pixel-exact operations.

This eliminates the most common source of incorrect clicks: the main LLM hallucinating coordinates that were never visible on the current screen.

---

## Research Landscape: What Exists

### Off-the-shelf (usable now, no training)

| Model | What it does | Available |
|---|---|---|
| **Iterative Narrowing** (arXiv:2411.13591) | Zoom-in wrapper around any grounding model; +23% accuracy | Training-free, implemented here |
| **GUI-Actor-7B + Verifier-2B** (Microsoft, NeurIPS 2025) | Attention-based grounding + re-ranking verifier | [HuggingFace: microsoft/GUI-Actor-7B-Qwen2.5-VL](https://huggingface.co/microsoft/GUI-Actor-7B-Qwen2.5-VL) |
| **UGround-V1** (OSU, ICLR 2025 Oral) | Click-only grounding, 2B/7B/72B | [HuggingFace: osunlp/UGround](https://huggingface.co/osunlp/UGround) |
| **RegionFocus** (ICCV 2025, arXiv:2505.00684) | Iterative zoom with bounding box map; +28% on UI-TARS | Paper only (no weights) |
| **ShowUI-2B** (CVPR 2025 Best Paper) | Click grounding, Qwen2-VL based | [HuggingFace: showlab/ShowUI-2B](https://huggingface.co/showlab/ShowUI-2B) |

### Requires training (continuous cursor / drag trajectories)

| Model | What it does | Status |
|---|---|---|
| **ShowUI-π** (arXiv:2512.24965) | Flow-matching VLA: screenshot + instruction + cursor state → (Δx, Δy, button). True continuous cursor control. | Code released Jan 2026, **weights pending** |
| **GUI-Spotlight** (arXiv:2510.04039) | RL-trained iterative crop-and-zoom; 52.8% on ScreenSpot-Pro | Weights not released |

**ShowUI-π is the target architecture** for the long-term cursor control model. It mirrors pi0 (Physical Intelligence robotics VLA): SmolVLM-2 450M encoder + 16-layer flow-matching action expert. When weights release, fine-tune from that checkpoint.

---

## Roadmap: Toward Continuous Cursor Control

### Phase 1 — Done

- [x] Remove raw coordinate input from `gui_do`
- [x] Implement iterative narrowing (two-pass zoom) in `execute_gui_action`
- [x] Fix `_parse_coordinates` to use actual image dimensions

### Phase 2 — Next

- [ ] **GUI-Actor Verifier integration**: After iterative narrowing, score the final (x, y) with `microsoft/GUI-Actor-Verifier-2B`. If score < threshold, retry with a rephrased description.
- [ ] **RegionFocus multi-round**: Extend to 3 rounds instead of 2 — full frame → 300px crop → 150px crop. Especially valuable for DaVinci Resolve timeline handles (2-4px wide).

### Phase 3 — Training required

**Goal**: Replace the two-pass zoom approach with a true visuomotor policy that:
- Outputs continuous cursor deltas (Δx, Δy) at 10–30 Hz
- Closes the control loop at each frame — model sees where cursor actually is
- Handles drag trajectories natively (not just start+end endpoints)
- Works for video editing operations: J-cut, trim handle, keyframe drag, clip move

**Architecture**: ShowUI-π style (SmolVLM-2 + flow-matching head). Start from ShowUI-π weights when released.

---

## Training Data for Phase 3

### Available datasets

| Dataset | Content | Location |
|---|---|---|
| **ScreenDrag** | 20K dense drag trajectories, dense (x,y,m) sequences, DaVinci/Premiere domains | [showlab/ScreenDrag](https://huggingface.co/showlab/ScreenDrag) |
| **PSAI Computer Use** | 3,167 tasks with timestamped mouse/click events JSON + screen video | [anaisleila/computer-use-data-psai](https://huggingface.co/datasets/anaisleila/computer-use-data-psai) |
| **VideoGUI** | 86 tasks in DaVinci Resolve, Premiere Pro, After Effects, CapCut with action annotations | [showlab/VideoGUI](https://github.com/showlab/videogui) |
| **GUI-360°** | 1.2M steps, office apps, Windows | [vyokky/GUI-360](https://huggingface.co/datasets/vyokky/GUI-360) |

### Custom data generation (the user's idea — validated approach)

**Record → VLM caption → package as training examples**

```
1. Record DaVinci Resolve session at OS level
   ├── mss: screen video at 30fps
   ├── pynput: mouse events at 100Hz (move, down, up)
   └── Common timestamp via time.perf_counter()

2. VLM captioning (Gemini 2.0 Flash or GPT-4o)
   ├── Feed screenshot sequence at sub-task boundaries
   ├── Detect boundaries: tool changes, panel focus, timeline zoom
   └── Generate NL label: "Drag right edge of clip at 0:23 leftward 12 frames"

3. Package as (NL instruction, screen frames, dense (x,y,m) sequence)
```

This approach is validated by:
- **PSAI dataset**: does exactly this — pynput events + screen recording + VLM-generated reasoning_steps
- **AgentTrek** (arXiv:2412.09605): VLM auto-captions screen recordings into step-level annotations
- **OS-Genesis** (arXiv:2412.19723): Reverse synthesis — free exploration → VLM derives task labels

**For generating realistic drag paths between known endpoints** (e.g., when you know the trim handle start and target frame but need realistic intermediate waypoints for training data augmentation):
- `pip install human-mouse` — Bezier + spline, algorithmically realistic
- **DMTG** (arXiv:2410.18233) — diffusion model for human-like cursor trajectories

### DaVinci Resolve specific challenges

| Operation | Why single-shot fails | What continuous control needs |
|---|---|---|
| Clip trim handle | Handle is 2-4px wide; 1px error = wrong frame | See timecode update, adjust until target frame shown |
| J-cut / L-cut | Audio/video trim handles overlap | Distinguish by visual context while dragging |
| Keyframe drag | Sub-pixel precision on Bezier handles | Track cursor relative to handle while dragging |
| Clip move on timeline | Drop zone changes during drag | Continuous frame context to see snapping indicators |
| Speed ramp point | Tiny dot on speed curve | Two-round zoom + drag |

---

## PSAI Events JSON Format

For building the custom data pipeline, the PSAI `events` field schema:

```json
[
  {"time_stamp": 1245483.5, "action": "move", "x": 1467, "y": 47},
  {"time_stamp": 1245484.2, "action": "click", "x": 1467, "y": 47},
  {"time_stamp": 1245500.1, "action": "window_focus", "app_name": "chrome.exe", "window_name": "..."}
]
```

- `time_stamp`: float milliseconds (use for aligning with video frames)
- `action`: `"move"` | `"click"` | `"window_focus"` | others
- `x`, `y`: integer pixel coordinates in screen space

To reconstruct drag trajectories: find sequences of `"move"` events between `"click"` (mousedown) and the next `"click"` (mouseup) at a different position.

---

*Last updated: 2026-02-23*
