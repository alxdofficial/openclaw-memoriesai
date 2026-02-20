# Roadmap

## Phase 1: Architecture & Research ← DONE

- [x] Problem definition
- [x] Research existing solutions (Claude Computer Use, UI-TARS, AgentRR, Kairos, MiniCPM-o)
- [x] Architecture design
- [x] Tool specifications
- [x] Smart Wait detailed design
- [x] Task Memory detailed design
- [x] Procedural Memory detailed design
- [ ] Alex review & feedback

## Phase 2: Smart Wait MVP ← DONE

**Goal**: Agent can call `smart_wait()` and get woken when a visual condition is met.

### 2a: Infrastructure
- [x] Set up Python project structure (MCP server skeleton)
- [x] MiniCPM-o deployment (Ollama, RTX 3070, 1.6s warm inference)
- [x] Xvfb setup (headless Ubuntu server — :99 with fluxbox WM)
- [x] Screen capture module (X11 via python-xlib, 67ms/frame)
- [x] Pixel-diff gate (numpy, 1% threshold)

### 2b: Core Wait Loop
- [x] Wait job queue (SQLite-backed)
- [x] Frame capture → diff gate → model evaluation pipeline
- [x] Adaptive polling (speed up on PARTIAL, slow down on static)
- [x] Timeout handling
- [x] Job context windows (rolling frame history + verdicts)

### 2c: OpenClaw Integration
- [x] MCP server exposing `smart_wait` tool (+ 6 more tools)
- [x] Wake mechanism (`openclaw system event --mode now`)
- [x] Register MCP server with OpenClaw via mcporter
- [x] End-to-end test: Chrome detection in 1.3-1.8s

### 2d: PTY Handling (vision-only)
- [x] Removed terminal regex/text fast path
- [x] Unified PTY and GUI waits under the same vision evaluator
- [ ] Optional future improvement: bind `pty:<session_id>` to a specific terminal window capture for cleaner frames

## Phase 3: Task Memory ← DONE

**Goal**: Agent can register tasks, report progress, and query state across context boundaries.

### 3a: Task CRUD
- [x] SQLite schema + migrations
- [x] task_register, task_update, task_list tools
- [x] Message history append + retrieval
- [x] Hierarchical model: Task → Plan Items → Actions → Logs

### 3b: Distillation
- [x] Running summary generation (MiniCPM-o)
- [x] On-demand query answering
- [x] Plan progress heuristic tracking

### 3c: Integration
- [x] Link smart_wait to tasks (auto-post on resolution)
- [x] SKILL.md narration guidance teaches agent to register tasks and narrate progress
- [x] Compact-with-focused-expansion context for task resumption (active item expanded, others compact)
- [ ] Test: multi-step deployment with context compaction mid-task

## Phase 4: Video Comprehension (Memories AI)

**Goal**: Agent can record and analyze video clips via Memories AI — both for immediate understanding and for building a library of procedural knowledge.

### 4a: Screen Recording Module ← DONE
- [x] On-demand video recording (ffmpeg + Xvfb, target window or full screen)
- [x] Configurable duration, FPS, resolution
- [x] Cleanup of ephemeral recordings

### 4b: Memories AI Integration
- [ ] API client (upload video + prompt, get analysis)
- [ ] Auth configuration (API key)
- [ ] Error handling + retry logic

### 4c: video_understand (sync mode)
- [ ] Record → upload → block until response → return as tool result
- [ ] Ephemeral: video discarded after analysis
- [ ] Timeout handling for slow API responses

### 4d: video_record (async mode)
- [ ] Record → upload → return immediately
- [ ] Background analysis → wake OpenClaw via `system event` when done
- [ ] Save analysis in Memories AI index with tags

### 4e: video_search
- [ ] Search previously saved video analyses
- [ ] Agent checks "have I recorded this before?" before re-learning workflows

## Phase 5: Visual OS Control ← DONE

**Goal**: Agent can interact with native desktop applications, not just browser and CLI.

- [x] xdotool integration for mouse/keyboard
- [x] Window management (focus, resize, move, close, list)
- [x] Desktop screenshot → action pipeline (`desktop_look`)
- [ ] Combine with procedural memory for efficient UI navigation

## Phase 6: Dashboard & Observability ← DONE

**Goal**: Human can observe and control tasks in real time.

- [x] Web dashboard served by daemon (no separate process)
- [x] Task list sidebar with status badges and progress bars
- [x] Expandable task tree with nested actions, screenshots, lightbox
- [x] Rich action logs: GUI screenshots, coordinates, confidence scores, backend/model used
- [x] Live polled JPEG screen view per task (2 fps)
- [x] Replay mode — scrub through recorded frames frame-by-frame
- [x] Color-coded message feed (agent narration, wait events, system messages)
- [x] Task control buttons (Pause, Resume, Cancel) — human can free up the LLM
- [x] Recording controls (start/stop per task, elapsed timer)
- [x] Video export — on task complete/cancel, frames encoded to H.264 MP4 via ffmpeg; download button in sidebar
- [x] Per-task virtual displays (Xvfb isolation via display/manager.py)
- [x] Stuck detection + automatic LLM wake with resume packets

## Phase 7.5: Pipeline Performance Optimization ← DONE

**Goal**: Reduce latency throughout the SmartWait and capture pipeline.

- [x] HTTP connection pooling — persistent `httpx.AsyncClient` in all vision backends (saves 100–300 ms/call)
- [x] OpenRouter vision backend — cloud alternative, no GPU required; Gemini 2.0 Flash 10× cheaper than Claude Haiku
- [x] Async frame capture — `capture_screen()` + `frame_to_jpeg()` off-thread via `run_in_executor`; event loop stays responsive
- [x] Parallel job evaluation — all overdue wait jobs evaluated concurrently via `asyncio.gather()`; Xlib serialized by `_CAPTURE_LOCK`
- [x] Ollama `keep_alive` default `"0"` → `"10m"` — eliminates 5–30 s cold-start between calls
- [x] Pixel-diff downsampling — diff computed on 320 px wide frame instead of full 1920 px (~30× faster)
- [x] Async subprocess for OpenClaw wake — `asyncio.create_subprocess_exec` replaces blocking `subprocess.run`
- [x] Dashboard frame buffer rate — 4 fps → 2 fps (halved Xlib load)

See `docs/PERFORMANCE-OPTIMIZATION.md` for full before/after measurements and cost analysis.

## Phase 7: Context Compaction & LLM Guidance ← DONE

**Goal**: Agent survives context loss and resumes effectively.

- [x] `build_resume_packet()` with active item expansion (actions + logs)
- [x] `detail_level="focused"` — compact view with only active item expanded
- [x] SKILL.md narration section — teaches agent to write reasoning to task history
- [x] Stuck detection loop (60s interval, 5min threshold, cooldown)
- [x] `[task_stuck_resume]` wake event with full resume context

## Future Ideas

- **Multi-machine federation**: Share procedural memory across devices
- **Community experience store**: AgentRR-style shared knowledge base
- **Voice narration**: Agent narrates what it's doing (MiniCPM-o supports speech)
- **Proactive suggestions**: "I noticed you do X every Monday — want me to automate it?"
- **Recording playback UI**: Web interface to browse and annotate screen recordings

## Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| MCP Server | Python (`mcp[cli]`) | Best MCP SDK support, rich ML ecosystem |
| Database | SQLite | Zero-config, single-file, sufficient scale |
| Vision Model | MiniCPM-o 4.5 via Ollama | Local, free, Gemini-level quality |
| Virtual Display | Xvfb | Headless server — no physical monitor |
| Screen Capture | ffmpeg + python-xlib | Reads from Xvfb virtual framebuffer |
| Video Indexing | Memories AI API | Alex's company, advanced video comprehension |
| Pixel Diff | NumPy | Fast, minimal dependency |
| Wake Mechanism | `openclaw system event --mode now` | Direct CLI injection, seconds latency |
| Web Dashboard | aiohttp static + vanilla JS | Zero dependencies, served by daemon |
| Display Isolation | Xvfb per task | Each task gets its own virtual screen |

## Hardware Requirements

**Minimum (CPU-only, quantized):**
- 8 GB RAM (5 GB for model, 3 GB for system)
- Any modern x86_64 CPU
- 500 MB disk for daemon + DB

**Recommended (GPU):**
- NVIDIA GPU with 8+ GB VRAM
- 16 GB system RAM
- For best performance: RTX 3060 or better

**Alex's server (alxdws2):**
- Ubuntu Server (headless) — will need X11/Xvfb for screen capture if no physical display
- Need to check GPU availability
