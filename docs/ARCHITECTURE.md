# Architecture

## Overview

openclaw-memoriesai is an **MCP (Model Context Protocol) server** that exposes tools to OpenClaw (or any MCP-compatible client). It runs as a local daemon alongside the OpenClaw gateway.

The MCP approach was chosen because:
- OpenClaw already supports MCP servers via the `mcporter` skill
- Tools appear natively in the agent's tool list — no custom plugin code needed
- Any MCP client can use it (Claude Desktop, Cursor, etc.), not just OpenClaw
- Clean separation of concerns: the daemon manages state, the LLM calls tools

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                          │
│                                                              │
│  Agent Loop:                                                 │
│  message → inference → tool call → inference → reply         │
│                                                              │
│  MCP tools available to the model:                           │
│  ┌────────────────┐ ┌────────────────┐ ┌──────────────────┐ │
│  │ task_register   │ │ smart_wait     │ │ memory_recall    │ │
│  │ task_update     │ │                │ │ (phase 2)        │ │
│  │ task_list       │ │                │ │                  │ │
│  │ task_complete   │ │                │ │                  │ │
│  └────────┬───────┘ └───────┬────────┘ └────────┬─────────┘ │
└───────────┼─────────────────┼───────────────────┼────────────┘
            │     MCP (stdio or SSE)              │
            ▼                 ▼                   ▼
┌──────────────────────────────────────────────────────────────┐
│                openclaw-memoriesai daemon                     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   MCP Server                         │    │
│  │  (Python: mcp[cli] or TypeScript: @modelcontextprotocol/sdk) │
│  │  Transport: stdio (launched by OpenClaw)              │    │
│  └─────────────┬──────────────────┬────────────────────┘    │
│                │                  │                          │
│  ┌─────────────▼────┐  ┌─────────▼──────────┐              │
│  │   Task Manager    │  │   Wait Manager      │              │
│  │                   │  │                     │              │
│  │ - CRUD tasks      │  │ - Wait job queue    │              │
│  │ - Message history │  │ - Frame capture     │              │
│  │ - Plan tracking   │  │ - Pixel-diff gate   │              │
│  │ - Distillation    │  │ - Adaptive polling  │              │
│  │   (on query)      │  │ - Condition eval    │              │
│  │                   │  │ - Wake dispatch     │              │
│  └────────┬──────────┘  └──────────┬──────────┘              │
│           │                        │                          │
│  ┌────────▼────────────────────────▼──────────┐              │
│  │              Model Backend                  │              │
│  │                                             │              │
│  │  Local: MiniCPM-o 4.5 via llama.cpp/vLLM   │              │
│  │  - Vision: evaluate wait conditions          │              │
│  │  - Text: distill task histories              │              │
│  │                                             │              │
│  │  Remote (optional fallback):                 │              │
│  │  - OpenRouter API for complex reasoning      │              │
│  └─────────────────────────────────────────────┘              │
│                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐         │
│  │   SQLite Database     │  │   Screen Capture     │         │
│  │   - tasks             │  │   - X11: xdotool +   │         │
│  │   - task_messages     │  │     XShm / import    │         │
│  │   - wait_jobs         │  │   - Wayland: grim /  │         │
│  │   - wait_results      │  │     PipeWire         │         │
│  └──────────────────────┘  │   - PTY: buffer read │         │
│                             └──────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
                              │
                              │ Wake event (HTTP POST
                              │ to OpenClaw gateway)
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  OpenClaw Gateway — receives system event                    │
│  "smart_wait resolved: Download completed (file: report.pdf)"│
│  → Agent wakes up, continues task                            │
└──────────────────────────────────────────────────────────────┘
```

## Component Details

### MCP Server Layer

The MCP server handles tool registration and request routing. It exposes tools via the Model Context Protocol, which OpenClaw discovers and makes available to the LLM.

**Transport**: stdio (recommended). OpenClaw launches the daemon as a child process and communicates over stdin/stdout. This is the simplest deployment — no ports, no auth.

**Alternative**: HTTP+SSE for remote deployment or debugging.

### Task Manager

Handles persistent task tracking. Core data model:

```
Task {
  id: string (uuid)
  name: string
  status: "active" | "paused" | "completed" | "failed"
  plan: string[]           // original checklist
  created_at: datetime
  updated_at: datetime
}

TaskMessage {
  id: string (uuid)
  task_id: string (FK)
  role: "agent" | "system" | "distilled"
  content: string
  created_at: datetime
}
```

**Distillation**: When the agent queries task state, the Task Manager reads the full message history and produces a distilled summary. Two strategies:

1. **Local (MiniCPM-o)**: Fast, free, good for simple queries ("what step am I on?")
2. **Remote (API)**: For complex reasoning ("should I change the plan given this error?")

**Running summary**: Updated every N messages (configurable, default 5). Stored as a `distilled` role message. Queries first check the running summary; if it's fresh enough, skip re-distillation.

### Wait Manager

Handles the smart waiting system. Core data model:

```
WaitJob {
  id: string (uuid)
  task_id: string? (optional FK — links wait to a task)
  target_type: "window" | "pty" | "screen"
  target_id: string        // X11 window ID, PTY session, or "full"
  criteria: string         // natural language condition
  timeout_seconds: int
  status: "watching" | "resolved" | "timeout" | "error"
  result_message: string?  // why it woke up
  created_at: datetime
  resolved_at: datetime?
}
```

**Frame capture pipeline**:

```
Target (window/pty/screen)
  │
  ▼
Frame Grabber (1-10 fps configurable)
  │ raw frame
  ▼
Pixel-Diff Gate
  │ compares with previous frame
  │ if <threshold% pixels changed → skip
  │ if ≥threshold% → pass through
  ▼
Resize + Compress (720p, JPEG q=80)
  │ ~50-100KB per frame
  ▼
MiniCPM-o Evaluation
  │ "Has this condition been met: '{criteria}'?"
  │ → YES (with description) / NO / PARTIAL
  ▼
Decision
  │ YES → resolve job, dispatch wake event
  │ NO → continue watching
  │ PARTIAL → increase polling frequency
```

**Adaptive polling rates**:
- Static screen (no pixel diff): check every 5-10 seconds
- Changing screen (pixel diff detected): check every 1-2 seconds
- Partial match from model: check every 0.5-1 second (burst mode)
- After 30s of static: drop to every 15 seconds

**Multiplexing**: One MiniCPM-o instance serves all wait jobs. Jobs are processed round-robin from a priority queue (priority = time since last check × urgency). Frame batching: if multiple jobs have pending frames, batch them into a single inference call.

### Screen Capture

Platform-specific frame grabbing:

| Platform | Method | Notes |
|----------|--------|-------|
| X11 | `python-xlib` + XShm or `import -window {id}` | Fast shared-memory capture for specific windows |
| Wayland | `grim` or PipeWire screen capture | Requires compositor support |
| PTY | Direct buffer read via `process` tool | No vision needed — text matching first, vision fallback |

**PTY fast path**: For terminal targets, the daemon first tries regex/string matching on the terminal buffer. Vision is only used if the criteria is too complex for text matching (e.g., "the progress bar looks complete").

### Wake Dispatch

When a wait condition is met, the daemon needs to wake the OpenClaw agent. Options explored:

1. **`cron.wake` with mode "now"**: Wakes the agent on the next opportunity. Simple but indirect.
2. **System event injection**: POST to OpenClaw's gateway API to inject a message directly into the session. Most direct.
3. **exec notifyOnExit pattern**: OpenClaw already wakes the agent when background exec sessions complete. We could model wait resolution as a virtual "process exit."

**Recommended**: System event injection via the gateway API. This is the most direct path and doesn't depend on heartbeat timing.

```
POST /api/agent
{
  "sessionKey": "agent:main:main",
  "message": "[smart_wait] Condition met for job {id}: {result_message}",
  "source": "system"
}
```

### Model Backend

**Primary: MiniCPM-o 4.5 (local)**

- 9B parameters, runs on CPU via llama.cpp (quantized: Q4_K_M ~5GB RAM)
- Also runs on GPU via vLLM/SGLang for faster inference
- Capabilities: vision (screenshots up to 1344×1344), text understanding, OCR
- Available on Ollama: `ollama run minicpm-v`
- Full-duplex streaming for real-time video understanding

Deployment options for this project:
- **llama.cpp server**: `./llama-server -m minicpm-o-4.5-Q4_K_M.gguf --port 8090`
- **Ollama**: `ollama serve` + `ollama run minicpm-v` (simplest)
- **vLLM**: `vllm serve openbmb/MiniCPM-o-4_5` (highest throughput, needs GPU)

**Secondary: Remote API (optional)**

For complex task distillation or when local model quality isn't sufficient:
- OpenRouter API (configurable model, default: deepseek-chat for cost efficiency)
- Only used for `task_update` queries that require deep reasoning
- Disabled by default — local-first philosophy

### Database

SQLite via the `sqlite3` Python module (or `better-sqlite3` for TypeScript). Single file at `~/.openclaw-memoriesai/data.db`.

Tables: `tasks`, `task_messages`, `wait_jobs`.

SQLite was chosen for:
- Zero-config, single-file deployment
- Good enough for the expected load (tens of tasks, dozens of wait jobs)
- Easy backup (copy one file)
- Consistent with OpenClaw's own approach (it uses JSON files, but SQLite is more appropriate for our relational data)
