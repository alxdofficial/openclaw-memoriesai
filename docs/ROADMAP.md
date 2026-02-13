# Roadmap

## Phase 1: Architecture & Research ← WE ARE HERE

- [x] Problem definition
- [x] Research existing solutions (Claude Computer Use, UI-TARS, AgentRR, Kairos, MiniCPM-o)
- [x] Architecture design
- [x] Tool specifications
- [x] Smart Wait detailed design
- [x] Task Memory detailed design
- [x] Procedural Memory detailed design
- [ ] Alex review & feedback

## Phase 2: Smart Wait MVP

**Goal**: Agent can call `smart_wait()` and get woken when a visual condition is met.

### 2a: Infrastructure
- [ ] Set up Python project structure (MCP server skeleton)
- [ ] MiniCPM-o deployment (Ollama or llama.cpp)
- [ ] Xvfb setup (headless Ubuntu server — no physical display)
- [ ] Screen capture module (X11 window capture via Xvfb)
- [ ] Pixel-diff gate

### 2b: Core Wait Loop
- [ ] Wait job queue (SQLite-backed)
- [ ] Frame capture → diff gate → model evaluation pipeline
- [ ] Adaptive polling
- [ ] Timeout handling

### 2c: OpenClaw Integration
- [ ] MCP server exposing `smart_wait` tool
- [ ] Wake mechanism (file-based v1, gateway API v2)
- [ ] Register MCP server with OpenClaw via mcporter
- [ ] End-to-end test: agent kicks off download → smart_wait → wakes on completion

### 2d: PTY Fast Path
- [ ] Terminal buffer reading
- [ ] Text pattern matching
- [ ] Vision fallback for complex criteria

## Phase 3: Task Memory

**Goal**: Agent can register tasks, report progress, and query state across context boundaries.

### 3a: Task CRUD
- [ ] SQLite schema + migrations
- [ ] task_register, task_update, task_list, task_complete tools
- [ ] Message history append + retrieval

### 3b: Distillation
- [ ] Running summary generation (MiniCPM-o)
- [ ] On-demand query answering
- [ ] Plan progress heuristic tracking

### 3c: Integration
- [ ] Link smart_wait to tasks (auto-post on resolution)
- [ ] OpenClaw agent learns to register tasks for multi-step work
- [ ] Test: multi-step deployment with context compaction mid-task

## Phase 4: Video Comprehension (Memories AI)

**Goal**: Agent can record and analyze video clips via Memories AI — both for immediate understanding and for building a library of procedural knowledge.

### 4a: Screen Recording Module
- [ ] On-demand video recording (ffmpeg + Xvfb, target window or full screen)
- [ ] Configurable duration, FPS, resolution
- [ ] Cleanup of ephemeral recordings

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

## Phase 5: Visual OS Control

**Goal**: Agent can interact with native desktop applications, not just browser and CLI.

- [ ] xdotool integration for mouse/keyboard
- [ ] Window management (focus, resize, move)
- [ ] Desktop screenshot → action pipeline
- [ ] Combine with procedural memory for efficient UI navigation

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
| Wake Mechanism | File-based (v1) → Gateway API (v2) | Progressive complexity |

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
