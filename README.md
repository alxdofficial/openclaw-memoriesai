# openclaw-memoriesai

An extension suite for [OpenClaw](https://github.com/openclaw/openclaw) that gives AI agents persistent task memory, smart visual waiting, and procedural recall from screen recordings.

## The Problem

Current AI agents (including OpenClaw) have three fundamental limitations when performing long, multi-step tasks on a computer:

1. **Amnesia across runs** — When a task spans multiple agent turns (because the context window fills up, the run times out, or the agent needs to wait), all progress tracking is lost. The agent has to re-derive where it was.

2. **Dumb waiting** — When the agent kicks off something slow (a download, a build, a deployment), it either burns tokens polling screenshots in a loop, uses hardcoded timeouts, or loses track entirely. There's no way to say "wake me when this finishes."

3. **No learning from observation** — Every time the agent encounters an unfamiliar UI, it figures it out from scratch using expensive vision model calls. It can't say "I've seen my user do this before" and replay those steps.

## The Solution

Three tools, one daemon:

### 🧠 Task Memory (`task_register`, `task_update`)
Persistent task tracking that lives outside the LLM's context window. The agent registers a task with a plan, reports progress as it goes, and can query "what have I done? what's next?" at any point — even after context compaction wipes the conversation history.

### ⏳ Smart Wait (`smart_wait`)
Delegate waiting to a local vision model (MiniCPM-o). The agent says "watch this window, wake me when the download finishes or an error appears." The daemon monitors the screen efficiently using pixel-diff gating and adaptive polling, and injects a wake event directly into the OpenClaw session when the condition is met.

### 🎥 Procedural Memory (`memory_recall`) *(Phase 2)*
Continuous screen recording indexed by Memories AI. The agent can search "how did the user deploy to production last time?" and get back timestamped video segments with extracted step-by-step actions. Learn from watching, not from scratch.

## Architecture

```
┌─────────────────────────────────────────────┐
│              OpenClaw (Main LLM)            │
│  Claude / GPT / any model                   │
│                                             │
│  Tools exposed via MCP:                     │
│  • task_register  • task_update             │
│  • smart_wait     • memory_recall           │
└──────────────┬──────────────────────────────┘
               │ MCP (stdio or HTTP)
               ▼
┌─────────────────────────────────────────────┐
│         openclaw-memoriesai daemon          │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │  Task    │  │  Wait    │  │ Procedural│ │
│  │  Store   │  │  Queue   │  │ Memory    │ │
│  │ (SQLite) │  │          │  │ (Mem. AI) │ │
│  └──────────┘  └──────────┘  └───────────┘ │
│                      │                      │
│              ┌───────▼────────┐             │
│              │  MiniCPM-o 4.5 │             │
│              │  (local VLM)   │             │
│              └────────────────┘             │
└─────────────────────────────────────────────┘
```

## Status

**Phase 1** (current): Architecture & spec  
**Phase 2**: Smart Wait daemon + Task Memory  
**Phase 3**: Procedural Memory via Memories AI  

See [docs/](docs/) for detailed specifications.

## License

MIT
