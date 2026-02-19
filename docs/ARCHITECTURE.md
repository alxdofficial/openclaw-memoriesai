# Architecture

## Overview

**agentic-computer-use** is a Desktop Environment Task Manager (DETM) — an MCP server with a persistent HTTP daemon that provides hierarchical task tracking, smart visual waiting, GUI automation with natural language grounding, and pluggable vision backends.

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Four layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with pixel-diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, passthrough)

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                          │
│                                                              │
│  MCP tools available to the model:                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │ Task Mgmt    │ │ Smart Wait   │ │ GUI Agent            │ │
│  │ task_register│ │ smart_wait   │ │ gui_do (NL→click)    │ │
│  │ task_summary │ │ wait_status  │ │ gui_find (NL→coords) │ │
│  │ task_update  │ │ wait_update  │ │                      │ │
│  │ task_item_*  │ │ wait_cancel  │ │ Desktop              │ │
│  │ task_log_*   │ │              │ │ desktop_action       │ │
│  │ task_drill_* │ │              │ │ desktop_look         │ │
│  └──────┬───────┘ └──────┬──────┘ │ video_record         │ │
│         │                │        └──────────┬───────────┘ │
└─────────┼────────────────┼───────────────────┼─────────────┘
          │     MCP (stdio)                    │
          ▼                ▼                   ▼
┌──────────────────────────────────────────────────────────────┐
│            agentic-computer-use MCP server                   │
│            (thin proxy → daemon HTTP calls)                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP (127.0.0.1:18790)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   DETM Daemon (persistent)                   │
│                                                              │
│  ┌────────────────────┐  ┌───────────────────────────────┐  │
│  │  Task Manager       │  │  Wait Engine                  │  │
│  │                     │  │                               │  │
│  │  tasks              │  │  Wait job queue               │  │
│  │  ├── plan_items     │  │  Pixel-diff gate              │  │
│  │  │   ├── actions    │  │  Adaptive polling             │  │
│  │  │   │   └── logs   │  │  Condition eval (via Vision)  │  │
│  │  │   └── ...        │  │  Wake dispatch → OpenClaw     │  │
│  │  └── ...            │  │                               │  │
│  └─────────┬──────────┘  └──────────┬────────────────────┘  │
│            │                        │                        │
│  ┌─────────▼────────────────────────▼────────────────────┐  │
│  │              Vision Backend (pluggable)                │  │
│  │                                                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │  │
│  │  │ Ollama   │ │ vLLM     │ │ Claude │ │Passthrough│  │  │
│  │  │(default) │ │(UI-TARS) │ │ (API)  │ │ (no eval) │  │  │
│  │  └──────────┘ └──────────┘ └────────┘ └───────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │  GUI Agent        │  │  Desktop Control                 │ │
│  │                   │  │                                  │ │
│  │  NL → grounding   │  │  xdotool: click, type, keys     │ │
│  │  ┌────────────┐   │  │  X11/Xvfb screen capture        │ │
│  │  │ UI-TARS    │   │  │  Window management               │ │
│  │  │ Claude CU  │   │  │  Video recording                 │ │
│  │  │ Direct     │   │  │                                  │ │
│  │  └────────────┘   │  │                                  │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │  SQLite Database  │  │  Debug Log                       │ │
│  │  ~/.agentic-      │  │  ~/.agentic-computer-use/logs/   │ │
│  │  computer-use/    │  │  debug.log                       │ │
│  │  data.db          │  │  (tail -f for live monitoring)   │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## Component Details

### MCP Server Layer

The MCP server (`server.py`) is a thin proxy. It defines tool schemas and forwards all calls to the persistent daemon over HTTP. OpenClaw launches it via stdio transport.

The daemon (`daemon.py`) runs independently on port 18790 and maintains state across MCP server restarts.

### Task Manager (Hierarchical)

Data model:

```
Task
├── id, name, status, metadata, created_at, updated_at
└── PlanItem[] (ordered by ordinal)
    ├── ordinal, title, status, started_at, completed_at, duration_seconds
    └── Action[]
        ├── action_type (cli|gui|wait|vision|reasoning|other)
        ├── summary, status, input_data, output_data, duration_ms
        └── ActionLog[]
            └── log_type, content, created_at
```

**Default view** is item-level: plan items with status + action counts. Drill-down expands individual items to show actions and logs.

**Status flow**:
- Task: active → paused/completed/failed/cancelled
- Plan Item: pending → active → completed/failed/skipped

### Smart Wait Engine

Vision-based async monitoring. The LLM delegates a visual wait, the daemon monitors, and wakes the LLM when the condition is met.

**Frame capture pipeline**:
```
Target (window or screen)
  │
  ▼
Frame Grabber → Pixel-Diff Gate → Vision Backend Evaluation → Decision
                (skip if static)   (evaluate NL condition)
```

**Adaptive polling**:
- Static screen (no pixel diff): 5-10s intervals
- Changing screen: 1-2s intervals
- Partial match: 0.5-1s burst mode
- 30s+ static: forced re-evaluation

### Vision Backend (Pluggable)

Configured via `ACU_VISION_BACKEND`:

| Backend | Model | Use Case |
|---------|-------|----------|
| `ollama` (default) | Configurable (`ACU_VISION_MODEL`) | Local evaluation, general purpose |
| `vllm` | UI-TARS-1.5-7B, Qwen, etc. | Best accuracy for grounding + wait |
| `claude` | Claude via Anthropic API | Zero-GPU fallback, API cost |
| `passthrough` | None | No evaluation, returns raw screenshots |

All backends implement `evaluate_condition(prompt, images)` and `check_health()`.

### GUI Agent (NL Grounding)

Natural language → screen coordinates → xdotool execution.

```
"click the Export button"
  │
  ▼
Screenshot capture → Grounding model → (x, y) coordinates → xdotool click
```

Three backends (`ACU_GUI_AGENT_BACKEND`):

| Backend | Model | Accuracy | Cost |
|---------|-------|----------|------|
| `uitars` | UI-TARS-1.5-7B via vLLM | 61.6% ScreenSpot-Pro | Local GPU |
| `claude_cu` | Claude computer_use API | ~27.7% | API cost |
| `direct` | None (coords required) | N/A | Free |

`gui_do` accepts both NL ("click the Export button") and explicit coords ("click(847, 523)").

### Desktop Control

`xdotool`-based execution layer for X11/Xvfb:
- Mouse: click, double-click, right-click, move, drag
- Keyboard: type text, press keys
- Windows: list, find, focus, resize, move, close
- Screen: capture, record video clips

### Database

SQLite via `aiosqlite`. Single file at `~/.agentic-computer-use/data.db`.

Tables: `tasks`, `plan_items`, `actions`, `action_logs`, `wait_jobs`.

### Logging

Debug log at `~/.agentic-computer-use/logs/debug.log`. Enable with `ACU_DEBUG=1` or `--debug` flag. Both the human and Claude Code can tail the log:

```bash
./dev.sh logs          # live colored tail
tail -f ~/.agentic-computer-use/logs/debug.log
```

## Configuration

All environment variables use the `ACU_*` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `ACU_VISION_BACKEND` | `ollama` | Vision backend: ollama, vllm, claude, passthrough |
| `ACU_VISION_MODEL` | `minicpm-v` | Ollama model name |
| `ACU_VLLM_URL` | `http://localhost:8000` | vLLM API endpoint |
| `ACU_VLLM_MODEL` | `ui-tars-1.5-7b` | vLLM model name |
| `ACU_CLAUDE_VISION_MODEL` | `claude-sonnet-4-20250514` | Claude vision model |
| `ACU_GUI_AGENT_BACKEND` | `direct` | GUI grounding: direct, uitars, claude_cu |
| `ACU_DEBUG` | `0` | Enable verbose debug logging |
| `ACU_WORKSPACE` | (none) | Workspace directory for memory files |
| `DISPLAY` | `:1` | X11 display for screen capture |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API URL |
| `ANTHROPIC_API_KEY` | (none) | Required for Claude vision/GUI backends |
