# DETM Benchmarking Plan

## Objective

Establish where DETM stands relative to the state-of-the-art in computer use
agents. Identify the right benchmarks, collect published baselines (so we don't
re-run what's already been run), and define exactly what we need to execute
ourselves.

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

#### OpenAI CUA / Operator

| Version | OSWorld | WebArena | ScreenSpot-Pro | Source |
|---|---|---|---|---|
| OpenAI CUA (computer-use-preview) | 38.1% | 58.1% | 23.4% | OpenAI; ByteDance comparison |

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
| ScreenSpot-Pro | 49.6% | ByteDance GitHub |
| WebVoyager | 84.8-87.0% | ByteDance GitHub |
| Online-Mind2Web | 75.8% | ByteDance GitHub |

**How to run:** Via OpenRouter (`bytedance/ui-tars-1.5-7b`) or local
HuggingFace deployment. OSWorld has an existing UI-TARS integration.

**Decision:** Use published ScreenSpot-Pro number (49.6%). Re-run on OSWorld
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
- Write evaluation script that runs each sample through our grounding pipeline
- Compare: UI-TARS-1.5-7B standalone (49.6%) vs DETM iterative narrowing
- Expected time: 2-4 hours

### Phase 2: OSWorld Setup + Baseline Run
- Clone OSWorld, build Docker environments
- Write DETM adapter (bridges OSWorld harness to our daemon HTTP API)
- Run UI-TARS-1.5-7B standalone as baseline (verify we reproduce ~42.5%)
- Run Agent S3 with GPT-4o as baseline
- Expected time: 1-2 days for setup, 4-8 hours per agent run

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
| Human | 72.36% | -- | 78.24% |
| Claude Opus 4.6 | 72.7% | -- | 68.0% |
| Claude Sonnet 4.5 | 61.4% | -- | -- |
| Agent S3 (Pass@1, GPT-5) | 62.6% | -- | -- |
| Agent S3 (bBoN, GPT-5) | 69.9% | -- | -- |
| OpenAI CUA | 38.1% | 23.4% | 58.1% |
| UI-TARS-1.5-7B | 42.5% | 49.6% | -- |
| OS-Atlas-7B | -- | 18.9% | -- |
| GPT-4o (bare) | 5.0% | 0.8% | 42.8% |

### Need to Run Ourselves:

| What | Why | Priority |
|---|---|---|
| DETM on ScreenSpot-Pro | Measure our grounding pipeline | P0 |
| DETM on OSWorld (15 + 50 steps) | Primary benchmark number | P0 |
| Agent S3 w/ GPT-4o on OSWorld | Controlled baseline (same infra) | P1 |
| UI-TARS-1.5-7B standalone on OSWorld (15 steps) | Ablation baseline | P1 |
| DETM on WebArena Verified Hard | Web task performance | P2 |
| Yutori N1 on WebArena via API | Cross-compare browser agent | P3 |

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
