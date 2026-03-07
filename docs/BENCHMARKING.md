# DETM Benchmarking Plan

## TL;DR

DETM uses cheap models (Gemini Flash ~$0.10/M + UI-TARS-7B ~$0.15/M) instead of frontier models ($15-75/M). The question: can smart orchestration (iterative grounding, two-model split, edge detection) close the gap with agents that brute-force it with expensive models?

**Three benchmarks:** OSWorld (369 desktop tasks), ScreenSpot-Pro (1,581 grounding samples), WebArena (137 web tasks).

**Execution order:** ScreenSpot-Pro first (hours, no VMs), then OSWorld (days), then WebArena (stretch).

### Unified Comparison Table

All agents we compare against. **TBR** = to be run by us. **--** = unknown/not planned.

| Agent | Cost | OSWorld (Pass@1) | ScreenSpot-Pro | WebArena Hard | Notes |
|---|---|---|---|---|---|
| Human | -- | 72.4% | -- | 78.2% | Ceiling |
| GPT-5.4 (CU agent) | $$$$ | 75.0% | -- | 67.3% | New SOTA, screenshot-based CU |
| Claude Opus 4.6 | $$$$$ | 72.7% | -- | 68.0% | Top frontier, published |
| Claude Sonnet 4.6 | $$$$ | 72.5% | -- | 65.6% | Published |
| Agent S3 (GPT-5, Pass@1) | $$$$ | 62.6% | -- | -- | Closest arch to DETM, published |
| Agent S3 (GPT-4o) | $$$ | **TBR** | -- | -- | Controlled baseline, we run this |
| CoACT-1 | -- | 60.8% | -- | -- | Published |
| EvoCUA-32B | $$ | 56.7% | -- | -- | Published, 50 steps |
| UI-TARS-1.5-7B (standalone) | $ | 42.5% | 35.7% | -- | Our grounding model alone |
| UI-TARS-7B + RegionFocus | $ | -- | 41.2% | -- | Published (arXiv:2505.00684) |
| **DETM Config B (ours)** | **$** | **TBR** | **41.3%** | -- | **UI-TARS-7B + iterative narrowing** |
| OpenAI CUA | $$$$ | 38.1% | 23.4% | 58.1% | Published |
| GPT-4o (bare) | $$ | 5.0% | 0.8% | 42.8% | Floor for frontier models |
| **DETM @15 steps (ours)** | **$** | **TBR** | **TBR** | **TBR** | **Gemini Flash + UI-TARS-7B** |
| **DETM @50 steps (ours)** | **$** | **TBR** | -- | -- | **Extended step budget** |

Cost tiers: $ = <$0.01/task, $$ = $0.01-0.10, $$$ = $0.10-0.50, $$$$ = $0.50-2.00, $$$$$ = $2.00+

---

## Additional Technical Detail

Everything below is reference material: benchmark descriptions, agent breakdowns, implementation plans, and methodology.

---

## Benchmark Descriptions

| Benchmark | What It Measures | Tasks | How It Works | Metric |
|---|---|---|---|---|
| **OSWorld** | End-to-end desktop agent capability. Can the agent complete real multi-step tasks on a Linux desktop using mouse, keyboard, and apps? | 369 tasks: Chrome, LibreOffice, GIMP, VS Code, terminal, file mgmt, cross-app workflows | Agent gets dropped into a real Ubuntu VM. Sees only screenshots. Sends mouse/keyboard actions. Eval scripts inspect actual system state (files, configs, app state) after agent finishes. | Success rate (%). Binary pass/fail per task. No partial credit. |
| **ScreenSpot-Pro** | GUI element grounding accuracy. Given a screenshot and an instruction like "click the save button", can the model predict the right pixel? | 1,581 samples across 23 pro apps (VS Code, Photoshop, AutoCAD, Blender, etc.) on Win/Mac/Linux | Static dataset. Model receives screenshot + instruction, returns (x, y) coordinates. Point-in-box check against ground-truth bounding box. | Click accuracy (%). Binary correct/wrong per sample. |
| **WebArena Verified Hard** | Web agent task completion. Can the agent accomplish real tasks on self-hosted web applications? | 137 tasks (curated hard subset) across e-commerce, GitLab, Reddit-like forum, CMS, maps | Agent interacts with 5 real web apps running in Docker. Info-seeking tasks checked via string matching. State-changing tasks verified by querying backend DB/API. | Success rate (%). Binary pass/fail per task. |

---

## Per-Benchmark Score Tables

Detailed published scores with step counts, rollout methods, and sources.

### OSWorld (Desktop Agent -- Primary Benchmark)

| Agent | OSWorld % | Steps | Rollout | Cost Tier | Source |
|---|---|---|---|---|---|
| Human | 72.36 | -- | -- | -- | OSWorld paper |
| GPT-5.4 (CU agent) | 75.0 | -- | -- | $$$$ | OpenAI |
| Claude Opus 4.6 | 72.7 | -- | Pass@1 | $$$$$ | Anthropic |
| Claude Sonnet 4.6 | 72.5 | -- | Pass@1 | $$$$ | Anthropic |
| Agent S3 (GPT-5, bBoN N=10) | 69.9 | 100 | Best-of-10 | $$$$$ | Simular |
| Claude Opus 4.5 | 66.3 | -- | Pass@1 | $$$$$ | Anthropic |
| Agent S3 (GPT-5, single) | 62.6 | 100 | Pass@1 | $$$$ | Simular |
| Claude Sonnet 4.5 | 61.4 | -- | Pass@1 | $$$ | Anthropic |
| CoACT-1 | 60.76 | -- | -- | -- | OSWorld-Verified |
| EvoCUA-32B | 56.7 | 50 | Pass@1 | $$ | Meituan |
| UI-TARS-1.5-7B | 42.5 | 100 | Pass@1 | $ | ByteDance |
| OpenAI CUA | 38.1 | -- | -- | $$$$ | OpenAI |
| Claude 3.7 Sonnet | 35.8 | -- | -- | $$$ | Multiple |
| Agent S2 (Claude 3.7) | 34.5 | 50 | Pass@1 | $$$ | Simular |
| GPT-4o (bare) | 5.0 | 15 | Pass@1 | $$ | OSWorld paper |
| **DETM (ours)** | **TBR** | **15** | **Pass@1** | **$** | -- |
| **DETM (ours)** | **TBR** | **50** | **Pass@1** | **$** | -- |

### ScreenSpot-Pro (Grounding Accuracy)

| Agent | Accuracy % | Method | Source |
|---|---|---|---|
| UI-TARS-1.5 (72B) | 61.6 | End-to-end | ByteDance |
| UI-TARS-1.5-7B | 35.7 | End-to-end | ByteDance |
| UI-TARS-7B + RegionFocus | 41.2 | Iterative crop-zoom | arXiv:2505.00684 |
| **DETM Config B (ours)** | **41.3** | **Iterative narrowing (300px+150px)** | **This work** |
| UI-TARS-72B-DPO (v1) | 38.1 | End-to-end | ByteDance |
| Claude 3.7 Sonnet | 27.7 | End-to-end | ByteDance comparison |
| OpenAI CUA | 23.4 | End-to-end | ByteDance comparison |
| OS-Atlas-7B | 18.9 | End-to-end (baseline) | ScreenSpot-Pro paper |
| GPT-4o | 0.8 | Direct grounding | ScreenSpot-Pro paper |
| **DETM Config A: UI-TARS standalone (ours)** | **TBR** | **Single-shot** | -- |
| **DETM Config B: +iterative narrowing (ours)** | **TBR** | **3-pass crop-zoom** | -- |
| **DETM Config C: +convergence loop (ours)** | **TBR** | **Full refinement + cursor overlay** | -- |

### WebArena Verified Hard (Web Agent)

| Agent | Success % | Source |
|---|---|---|
| Human | 78.24 | WebArena paper |
| Claude Opus 4.6 | 68.0 | Anthropic |
| GPT-5.4 (CU agent) | 67.3 | OpenAI |
| Claude Sonnet 4.6 | 65.6 | Anthropic |
| Claude Opus 4.5 | 65.3 | Anthropic |
| OpenAI CUA | 58.1 | OpenAI |
| Claude 3.7 Sonnet | 52.0 | AgentOccam |
| GPT-4o (AgentOccam) | 42.8 | WebChoreArena paper |
| **DETM (ours)** | **TBR** | -- |

---

## Benchmarks We Will Run

### 1. OSWorld (Primary -- Desktop Agent Evaluation)

**What:** 369 real desktop tasks in Linux VMs. The industry gold standard for
computer use agents. Every serious agent reports an OSWorld number.

**Why this one:** DETM is a desktop agent. OSWorld tests exactly the skills DETM
has: browser navigation, LibreOffice, GIMP, VS Code, terminal, file management,
and cross-app workflows.

**Scoring:** Binary success rate. Evaluation scripts inspect actual system state
(files, app state, configs) after the agent finishes. No partial credit.

**Parameters:**
- Step budget: 15 (standard) and 50 (extended)
- Rollout: Pass@1 (single attempt -- no best-of-N)
- Observation: screenshot-only (matches our architecture)

**Human baseline: 72.36%**

**Infrastructure:** Docker with KVM. Each task gets a fresh VM snapshot. ~4 hours
with 8-16 parallel Docker envs, ~20 hours sequential.

**Repo:** `https://github.com/xlang-ai/OSWorld`

---

### 2. ScreenSpot-Pro (Grounding Accuracy)

**What:** 1,581 screenshot-instruction pairs from 23 professional applications
(VS Code, Photoshop, AutoCAD, Blender, etc.). Given a screenshot and an
instruction like "click the save button", predict the correct pixel coordinates.

**Why this one:** Directly measures our grounding pipeline (UI-TARS + iterative
narrowing). Static dataset, no VM needed -- fast to run, easy to compare.

**Scoring:** Click accuracy (point-in-box). Predicted (x,y) must fall within the
ground-truth bounding box. Binary per-sample.

**Infrastructure:** Just GPU inference. No VMs, no Docker environments. Can run
on the dataset in a few hours.

**Repo:** `https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding`
**Dataset:** `https://huggingface.co/datasets/likaixin/ScreenSpot-Pro`

---

### 3. WebArena Verified Hard (Web Agent Evaluation)

**What:** 137-task subset of WebArena (curated for difficulty and evaluation
reliability). Tests agents on self-hosted web apps: e-commerce (Magento),
GitLab, Reddit-like forum, CMS, maps.

**Why this one:** DETM handles browser tasks. WebArena Verified Hard is 83%
faster than the full 812-task suite while preserving agent ranking fidelity.

**Scoring:** Success rate. Info-seeking tasks use string matching; state-changing
tasks use programmatic backend verification.

**Human baseline: ~78%**

**Infrastructure:** Docker containers for 5 web apps. Needs ~50-100GB disk,
16GB+ RAM.

**Repo:** `https://github.com/web-arena-x/webarena`

---

## Benchmarks We Will NOT Run (and why)

| Benchmark | Reason |
|---|---|
| WindowsAgentArena | We run on Linux (display :99). Windows is out of scope. |
| AndroidWorld | Mobile. Not our target. |
| VisualWebArena | Interesting but 910 tasks is too many for initial eval. Revisit later. |
| MiniWoB++ | Saturated (96%+). Too easy to be informative. |
| Mind2Web | Static dataset, DOM-based. Doesn't test actual execution. |
| GAIA | General assistant benchmark, not specifically computer use. |
| Navi-Bench | Yutori's proprietary benchmark. Not independently reproducible. |

---

## Agents We Are Comparing Against

### Tier 1: Frontier Agents (Published Results Only -- Do Not Re-Run)

These are closed-source or require expensive API access. We use their published
numbers directly.

#### Claude Computer Use (Anthropic)

| Version | OSWorld | WebArena | ScreenSpot-Pro | Source |
|---|---|---|---|---|
| Claude 3.5 Sonnet (Oct 2024) | 14.9% | -- | -- | OSWorld paper |
| Claude 3.5 Sonnet v2 | 28.0% | -- | -- | UI-TARS-1.5 comparison |
| Claude 3.7 Sonnet | 35.8% | 52.0% | 27.7% | Multiple sources |
| Claude Sonnet 4.0 | 42.2-43.9% | -- | -- | Anthropic; OSWorld-Verified |
| Claude Sonnet 4.5 | 61.4% | -- | -- | Anthropic announcement |
| Claude Opus 4.5 | 66.3% | 65.3% | -- | Anthropic announcement |
| Claude Sonnet 4.6 | 72.5% | 65.6% | -- | Anthropic announcement |
| Claude Opus 4.6 | 72.7% | 68.0% | -- | Anthropic; Vellum benchmarks |

#### OpenAI GPT-5.4 / CUA

| Version | OSWorld | WebArena | ScreenSpot-Pro | Source |
|---|---|---|---|---|
| GPT-5.4 (native CU agent) | 75.0% | 67.3% | -- | OpenAI blog |
| OpenAI CUA (computer-use-preview) | 38.1% | 58.1% | 23.4% | OpenAI; ByteDance comparison |

GPT-5.4 pricing: $2.50/$15.00 per M tokens (input/output). Native computer use
via screenshot + mouse/keyboard -- same observation mode as DETM. Step budget
and rollout method (Pass@1 vs best-of-N) not published by OpenAI. Currently the
top OSWorld score at 75.0%, surpassing human baseline (72.4%).

#### Simular Agent S3

| Config | OSWorld | WindowsAgentArena | Source |
|---|---|---|---|
| Agent S3 (single rollout, GPT-5, 100 steps) | 62.6% | 50.2% | Simular blog |
| Agent S3 (bBoN N=10, GPT-5, 100 steps) | 69.9% | 56.6% | Simular blog |
| Agent S3 (bBoN, claimed peak) | 72.6% | -- | Simular blog |

Note: Best-of-N results are NOT directly comparable to single-rollout Pass@1.
Our evaluation will be Pass@1 only.

#### Yutori N1

| Benchmark | Score | Source |
|---|---|---|
| Navi-Bench v1 | 83.4% | Yutori blog |
| Online-Mind2Web (self-reported, human eval) | 78.7% | Yutori blog |
| OSWorld | NOT REPORTED | -- |
| WebArena | NOT REPORTED | -- |
| ScreenSpot-Pro | NOT REPORTED | -- |

N1 has no scores on any of our three benchmarks. We cannot directly compare
until Yutori publishes OSWorld/WebArena numbers or we run N1 via their API
(see Tier 2 below).

---

### Tier 2: Runnable Baselines (We Will Execute These)

These are open-source or API-accessible agents we can run ourselves on our
infrastructure to get fresh, controlled numbers under identical conditions.

#### A. UI-TARS-1.5-7B (our grounding model, standalone)

**Why:** This is the exact model we use for pixel grounding. Running it
standalone (without our Gemini supervisor) tells us exactly how much value the
DETM orchestration layer adds on top of raw UI-TARS.

**Published results (reference, but we re-run for controlled comparison):**

| Benchmark | Published Score | Source |
|---|---|---|
| OSWorld (100 steps) | 42.5% | ByteDance GitHub |
| ScreenSpot-Pro | 35.7% | ByteDance GitHub |
| WebVoyager | 84.8-87.0% | ByteDance GitHub |
| Online-Mind2Web | 75.8% | ByteDance GitHub |

**How to run:** Via OpenRouter (`bytedance/ui-tars-1.5-7b`) or local
HuggingFace deployment. OSWorld has an existing UI-TARS integration.

**Decision:** Use published ScreenSpot-Pro number (35.7%). Re-run on OSWorld
only if our adapter works cleanly, since ByteDance's number (42.5%) was at 100
steps and we want 15-step and 50-step numbers too.

#### B. Agent S3 (open-source, Simular)

**Why:** Closest architectural analog to DETM -- uses a frontier LLM supervisor
+ UI-TARS for grounding. Direct comparison shows how our orchestration layer
stacks up.

**How to run:**
```bash
git clone https://github.com/simular-ai/Agent-S.git
pip install .
```
Requires an LLM API key (GPT-4o for affordable runs, GPT-5 for peak).

**Decision:** Run Agent S3 with GPT-4o on OSWorld (our same step budgets) for a
controlled comparison. Use published GPT-5 numbers as the "best case" reference.

#### C. OpenCUA (open-source reproduction of OpenAI CUA)

**Why:** Open-source, well-documented, already integrated with OSWorld.

**Published results:**

| Version | OSWorld (100 steps) | Source |
|---|---|---|
| OpenCUA-72B | 45.0% | opencua.xlang.ai |
| OpenCUA-32B | 55.7% | opencua.xlang.ai |
| OpenCUA-7B | 44.3% | opencua.xlang.ai |

**How to run:** `https://opencua.xlang.ai`

**Decision:** Use published numbers. Only re-run if we need 15-step comparisons.

#### D. Yutori N1 (via API -- optional)

**Why:** They claim strong browser performance but have zero overlap with our
benchmark set. Running N1 on WebArena would be the only way to compare.

**How to run:** API at `docs.yutori.com`. $0.75/$3.00 per M tokens.

**Decision:** Low priority. Only run if we want browser-specific comparison.
Their API would need a WebArena adapter, and they have no desktop capability
(so no OSWorld).

---

### Tier 3: Published-Only References (No Re-Run Needed)

These have well-established published numbers. We just cite them.

| Agent | OSWorld | WebArena | ScreenSpot-Pro | Notes |
|---|---|---|---|---|
| GPT-5.4 (CU agent) | 75.0% | 67.3% | -- | New SOTA, screenshot-based CU |
| Human | 72.36% | 78.24% | N/A | Reference ceiling |
| GPT-4o (bare) | 5.0% | 42.8% | 0.8% | Floor for frontier models |
| GPT-4V | 11.77% | 14.41% | -- | Original WebArena paper |
| Gemini Pro 1.5 | 7.79% | -- | -- | OSWorld paper |
| CoACT-1 | 60.76% | -- | -- | OSWorld-Verified #1 (Jul 2025) |
| UiPath Screen Agent | 67.1% | -- | -- | Claude Opus 4.5 wrapper |
| EvoCUA-32B | 56.7% | -- | -- | Meituan, 50 steps |
| Agent S2 (Claude 3.7) | 34.5% | -- | -- | 50 steps |
| OS-Atlas-7B | -- | -- | 18.9% | ScreenSpot-Pro baseline |

---

## What We Measure for DETM

When we run DETM on these benchmarks, we report:

| Metric | Description |
|---|---|
| **Success Rate (Pass@1)** | % of tasks completed correctly. Single attempt. The primary metric. |
| **Success Rate @15 steps** | Performance under tight step budget (industry standard). |
| **Success Rate @50 steps** | Performance under relaxed step budget. |
| **Avg Steps to Success** | Mean steps for successfully completed tasks. Lower = more efficient. |
| **Avg Time per Task** | Wall-clock seconds per task (includes API latency). |
| **Avg Cost per Task** | Estimated API cost (OpenRouter tokens for UI-TARS + Gemini). |
| **Grounding Accuracy** | ScreenSpot-Pro click accuracy (tests our iterative narrowing pipeline). |

---

## Execution Order

### Phase 1: ScreenSpot-Pro (fastest, highest signal-to-noise)
- Download dataset from HuggingFace
- Run three configs through `benchmarks/screenspot_pro/eval.py`:
  - Config A: UI-TARS standalone (baseline, should reproduce ~35.7%)
  - Config B: UI-TARS + iterative narrowing (crop-zoom refinement only)
  - Config C: Full DETM refinement via `_refine_cursor()` with convergence loop + cursor overlay
- Expected time: 2-4 hours per config at OpenRouter speeds

### Phase 2: OSWorld Setup + Baseline Run (adapter done, Docker pending)
- Clone OSWorld, build Docker environments
- DETM adapter already built (`benchmarks/osworld/detm_agent.py`) via callback injection
- Run UI-TARS-1.5-7B standalone as baseline (verify we reproduce ~42.5%)
- Run Agent S3 with GPT-4o as baseline
- Expected time: 1-2 days for Docker setup, 4-8 hours per agent run

### Phase 3: DETM on OSWorld
- Run DETM through the adapter at 15-step and 50-step budgets
- Analyze results by category (browser, office, terminal, multi-app)
- Expected time: 4-8 hours

### Phase 4: WebArena Verified Hard (optional, if Phase 2-3 go well)
- Set up Docker web app environments
- Write WebArena adapter for DETM
- Run on 137-task subset
- Expected time: 1 day setup, 2-4 hours run

---

## Key Methodological Decisions

1. **Single rollout only (Pass@1).** Best-of-N inflates scores 10-15% and is
   not representative of real-world use. Agent S3's 72.6% drops to 62.6% at
   Pass@1. We report Pass@1 exclusively.

2. **Screenshot-only observation.** No accessibility tree. This matches DETM's
   actual architecture (UI-TARS sees pixels, not DOM).

3. **Step budgets: 15 and 50.** 15 is the original OSWorld standard. 50 gives
   more room for DETM's iterative narrowing and smart_wait to work. Some agents
   report at 100 steps, but we keep it tight.

4. **No cherry-picking.** Run the full benchmark. Report overall score and
   per-category breakdown. No subset selection after seeing results.

5. **Cost tracking.** API costs are a real differentiator. DETM uses UI-TARS-7B
   via OpenRouter (~cheap) + Gemini Flash (~cheap) vs agents that burn GPT-5 or
   Claude Opus 4.6 tokens.

---

## Summary: What We Already Know vs What We Need to Run

### Already Published (cite directly, no re-run):

| Agent | OSWorld | ScreenSpot-Pro | WebArena |
|---|---|---|---|
| GPT-5.4 (CU agent) | 75.0% | -- | 67.3% |
| Human | 72.36% | -- | 78.24% |
| Claude Opus 4.6 | 72.7% | -- | 68.0% |
| Claude Sonnet 4.5 | 61.4% | -- | -- |
| Agent S3 (Pass@1, GPT-5) | 62.6% | -- | -- |
| Agent S3 (bBoN, GPT-5) | 69.9% | -- | -- |
| OpenAI CUA | 38.1% | 23.4% | 58.1% |
| UI-TARS-1.5-7B | 42.5% | 35.7% | -- |
| OS-Atlas-7B | -- | 18.9% | -- |
| GPT-4o (bare) | 5.0% | 0.8% | 42.8% |

### Need to Run Ourselves:

| What | Why | Priority | Status |
|---|---|---|---|
| ScreenSpot-Pro Config A (UI-TARS standalone) | Reproduce 35.7% baseline | P0 | Not yet run |
| ScreenSpot-Pro Config B (+iterative narrowing) | Ablation: crop-zoom only | P0 | **Done: 41.3% (653/1581)** |
| ScreenSpot-Pro Config C (+convergence loop) | Full refinement pipeline | P0 | Not yet run |
| DETM on OSWorld (15 + 50 steps) | Primary benchmark number | P0 | Adapter ready |
| Agent S3 w/ GPT-4o on OSWorld | Controlled baseline (same infra) | P1 | Not started |
| UI-TARS-1.5-7B standalone on OSWorld (15 steps) | Ablation baseline | P1 | Not started |
| DETM on WebArena Verified Hard | Web task performance | P2 | Not started |
| Yutori N1 on WebArena via API | Cross-compare browser agent | P3 | Not started |

---

## Implementation Plan

### Phase 1: ScreenSpot-Pro Evaluation (code complete, runs pending)

No VMs, no Docker, just a static dataset + our grounding pipeline.

#### Dataset

- HuggingFace: `lmms-lab/ScreenSpot-Pro` (1,581 samples, single `train` split)
- Each sample: `image` (PIL), `instruction` (str), `bbox` [x1,y1,x2,y2] in
  pixels, `img_size` [width, height], `ui_type` (text/icon), `group`, `application`
- Resolutions range from 1920x1080 to 6016x3384 (many are 2560x1440+)
- Targets are tiny (~0.07% of image area on average)

#### Evaluation Logic

```python
# Ground truth: bbox [x1,y1,x2,y2] in pixel coords, normalize by img_size
# Prediction: (x, y) in normalized [0,1] coords
# Pass if: x1/w <= pred_x <= x2/w AND y1/h <= pred_y <= y2/h
```

#### What We Run

**Config A: UI-TARS-1.5-7B standalone (baseline)**
- Single API call: screenshot + instruction directly to UI-TARS via OpenRouter
- Uses `UITARSBackend.ground()` directly
- Expected: ~35.7% (published baseline for UI-TARS-1.5-7B)

**Config B: UI-TARS + iterative narrowing (crop-zoom only)**
- Initial grounding via UI-TARS, then two crop-zoom-reground passes via
  `_iterative_narrow()` (radii: 300px, 150px)
- 3 API calls total per sample
- Tests whether RegionFocus-style crop refinement helps on its own
- Note: RegionFocus paper already published iterative narrowing results, so
  this config is mainly for our own ablation rather than a novel claim

**Config C: Full DETM refinement (narrowing + convergence with cursor overlay)**
- Calls `_refine_cursor(target=instruction, display=None, frame=np_array)`
- Runs the full pipeline: initial grounding, iterative narrowing, then
  convergence loop where a cursor overlay is drawn on the static image
  (via `draw_cursor_overlay()`) and re-grounded to verify placement
- This is the interesting test: Gemini is NOT in the loop for ScreenSpot-Pro
  (it's a grounding-only benchmark), but the full convergence machinery
  (cursor overlay + stability check) is exercised

#### Script: `benchmarks/screenspot_pro/eval.py` (implemented)

```bash
PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config A          # UI-TARS standalone
PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config B          # +narrowing
PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config C          # +convergence
PYTHONPATH=src python3 benchmarks/screenspot_pro/eval.py --config C --limit 10  # smoke test
```

Features:
- Loads `lmms-lab/ScreenSpot-Pro` from HuggingFace (1,581 samples)
- Point-in-box accuracy check per sample
- Reports: overall accuracy, per-group, per-application, per-ui_type, per-platform
- Resume support (`--resume results/config_C.json`) for interrupted runs
- Periodic saves every 50 samples to `benchmarks/screenspot_pro/results/`

Expected runtime: ~2-3 hours at OpenRouter speeds (1,581 API calls for Config A,
~4,700 for Config B, variable for Config C depending on convergence rounds).

---

### Phase 2: OSWorld Setup (adapter complete, Docker setup pending)

#### OSWorld Agent Interface

OSWorld expects an agent class with:

```python
class MyAgent:
    action_space = "pyautogui"  # or "computer_13"

    def predict(self, instruction: str, obs: dict) -> tuple[str, list]:
        """
        obs = {
            "screenshot": bytes,        # raw PNG bytes
            "accessibility_tree": str,   # XML or None
            "terminal": str,             # or None
            "instruction": str
        }
        Returns: (response_text, [list of pyautogui code strings])
        Special actions: "WAIT", "DONE", "FAIL"
        """

    def reset(self, _logger=None, vm_ip=None, **kwargs):
        """Called before each task."""
```

The main loop calls `agent.predict()`, passes each returned action to
`env.step()`, which executes it on the VM via pyautogui over HTTP.

Coordinate space: 1920x1080 by default.

#### Action Format (pyautogui strings)

```python
"pyautogui.click(500, 300)"
"pyautogui.typewrite('hello world')"
"pyautogui.hotkey('ctrl', 'c')"
"pyautogui.scroll(-3)"
"pyautogui.moveTo(100, 200)"
"pyautogui.dragTo(400, 500, duration=1.0)"
"DONE"   # task complete
"FAIL"   # task impossible
"WAIT"   # pause
```

#### DETM Adapter Design: Callback Injection (implemented)

Rather than duplicating code or running a daemon, we added optional callback
parameters directly to our pipeline functions in `openrouter.py`. When
callbacks are provided, the system operates in "benchmark mode" -- no X11,
no xdotool. When they're `None`, the existing production code path is
unchanged.

**Changes to `OpenRouterVLMProvider.run()`:**

Three optional callback parameters:

```python
async def run(
    self,
    instruction, timeout, task_id, display, context="", session=None,
    # Benchmark mode (all None = production path unchanged):
    get_screenshot=None,         # () -> (jpeg_b64, cursor_pos | None)
    execute_override=None,       # (name, args) -> str
    display_size_override=None,  # (width, height)
) -> dict:
```

- `get_screenshot`: replaces `_capture_jpeg_b64()` -- caller provides the frame
- `execute_override`: replaces `execute_action()` -- caller collects actions
- `display_size_override`: replaces `_get_display_size()` -- caller sets resolution

**Changes to `_refine_cursor()`:**

```python
async def _refine_cursor(
    target, display, session=None, max_rounds=3,
    frame=None,  # injected screenshot for benchmark mode
) -> dict:
```

When `frame` is provided: skips `capture_screen(display)`, skips
`_smooth_mousemove()`, uses `draw_cursor_overlay(frame.copy(), x, y)` instead
of `capture_screen_with_cursor(display)`. The convergence loop still works
identically -- it just draws a synthetic cursor on the provided image instead
of moving a real one.

**OSWorld adapter (`benchmarks/osworld/detm_agent.py`):**

```python
class DETMAgent:
    action_space = "pyautogui"

    def predict(self, instruction, obs):
        frame = decode_png(obs["screenshot"])
        jpeg_b64 = encode_jpeg(frame)

        collected_actions = []

        def get_screenshot():
            return jpeg_b64, None

        def execute_override(name, args):
            collected_actions.append(_action_to_pyautogui(name, args))
            return "ok"

        result = provider.run(
            instruction=instruction,
            get_screenshot=get_screenshot,
            execute_override=execute_override,
            display_size_override=(w, h),
            ...
        )
        return response_text, collected_actions
```

This runs the full Gemini supervisor + UI-TARS grounding pipeline in-process,
with no X11, no xdotool, no daemon. The same code path that runs in
production is exercised -- the only difference is where screenshots come from
and where actions go.

#### Docker Environment

```bash
git clone https://github.com/xlang-ai/OSWorld
cd OSWorld
pip install -r requirements.txt
```

To plug DETM into OSWorld's evaluation loop:

```python
from benchmarks.osworld.detm_agent import DETMAgent
agent = DETMAgent()
# Use in OSWorld's run script with agent.predict() / agent.reset()
```

Minimum: 16GB RAM, Docker with KVM support. Each task gets a fresh container
from a base Ubuntu image (~25GB download once).

#### Baseline Runs on OSWorld

**Run 1: UI-TARS-1.5-7B standalone**
- Use OSWorld's existing `UITARSAgent` from `mm_agents/uitars_agent.py`
- Point it at OpenRouter's `bytedance/ui-tars-1.5-7b`
- Run at 15-step and 50-step
- Verify we reproduce ~42.5% (published at 100 steps)

**Run 2: Agent S3 with GPT-4o**
- Clone `github.com/simular-ai/Agent-S`, install
- Write OSWorld integration (Agent S3 has its own env loop, need to adapt)
- Use GPT-4o as backend (cheaper than GPT-5, gives controlled comparison)
- Run at 15-step and 50-step

#### File Structure (implemented)

```
benchmarks/
  screenspot_pro/
    eval.py              # ScreenSpot-Pro eval (configs A/B/C)
    results/             # output JSONs (config_A.json, config_B.json, config_C.json)
  osworld/
    detm_agent.py        # DETMAgent class with callback injection
    results/             # output dirs per step-budget

src/agentic_computer_use/live_ui/
    openrouter.py        # Modified: benchmark callbacks on run() and _refine_cursor()
```

Files NOT modified (confirmed display-independent):
- `gui_agent/agent.py` -- `_iterative_narrow()` is pure numpy + API
- `gui_agent/backends/uitars.py` -- `UITARSBackend.ground()` is pure API
- `capture/screen.py` -- `draw_cursor_overlay()` and `frame_to_jpeg()` are pure numpy/PIL
- `live_ui/actions.py` -- production xdotool code, bypassed via `execute_override` callback

---

### Phase 3: WebArena Verified Hard (Stretch Goal)

#### Setup

5 Docker containers:
- Shopping (Magento): port 7770
- Shopping Admin: port 7780
- Reddit (Postmill): port 9999
- GitLab: port 8023
- Maps (OpenStreetMap): port 3000

~50-100GB disk for Docker images. 16GB+ RAM.

#### Adapter

Similar to OSWorld adapter but for browser actions. WebArena agents interact
via Playwright. The DETM adapter would:
1. Capture browser screenshot
2. Send to Gemini supervisor
3. Get action (click, type, scroll)
4. Ground via UI-TARS
5. Execute via Playwright

Lower priority than OSWorld since WebArena is browser-only (doesn't test
DETM's full desktop capabilities).

---

## Implementation Timeline

| Week | What | Deliverable | Status |
|---|---|---|---|
| Week 1 | ScreenSpot-Pro eval script + run all configs | Grounding accuracy numbers | Config B done (41.3%), A+C pending |
| Week 1 | OSWorld adapter (callback injection) | DETMAgent class | Done |
| Week 2 | OSWorld Docker setup + UI-TARS baseline | Reproduced baseline, working env | Not started |
| Week 3 | DETM OSWorld run (15 + 50 steps) | DETM OSWorld score | Not started |
| Week 4 | Agent S3 baseline + analysis | Full comparison report | Not started |
ho
---

## References

- OSWorld: https://os-world.github.io / https://github.com/xlang-ai/OSWorld
- OSWorld-Verified: https://xlang.ai/blog/osworld-verified
- ScreenSpot-Pro: https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding
- ScreenSpot-Pro Leaderboard: https://gui-agent.github.io/grounding-leaderboard/
- WebArena: https://webarena.dev / https://github.com/web-arena-x/webarena
- Agent S3: https://github.com/simular-ai/Agent-S / https://www.simular.ai/articles/agent-s3
- OpenCUA: https://opencua.xlang.ai
- UI-TARS-1.5: https://github.com/bytedance/UI-TARS
- Yutori N1: https://yutori.com/blog/introducing-n1
- Anthropic CU: https://www.anthropic.com/news/claude-sonnet-4-5
- OpenAI CUA: https://openai.com/index/computer-using-agent/
