# Problems We're Solving

## Problem 1: Context Amnesia in Long Tasks

### What happens today

When an AI agent (OpenClaw, Claude, etc.) performs a multi-step task — deploying an app, navigating a complex UI workflow, or filling out a multi-page form — it maintains state purely in its context window. This breaks in several ways:

- **Context compaction**: OpenClaw compacts conversation history when the window fills up. The compacted summary loses granular step-by-step progress.
- **Run timeouts**: OpenClaw's agent loop has a 600-second default timeout. Complex tasks that require waiting or many steps can exceed this.
- **Session interruption**: If the user sends a message mid-task, or a heartbeat fires, the agent's focus shifts. When it returns to the task, the earlier steps may have been compacted away.
- **No checkpoint/resume**: If a 10-step workflow fails at step 7, there's no saved state to resume from. The agent starts over.

### What we want

A persistent task store that the agent explicitly writes to as it progresses. Like a human keeping a checklist on paper — even if they get distracted, the paper remembers where they left off.

The agent should be able to ask "what have I done on task X?" at any point and get a distilled, accurate answer, regardless of what's in (or missing from) the current context window.

---

## Problem 2: Wasteful and Dumb Waiting

### What happens today

When the agent starts a long-running operation (downloading a file, running a build, waiting for a page to load), it has limited options:

1. **Spin-poll**: Take a screenshot every few seconds, send it to the main LLM (Claude/GPT) for evaluation. Each screenshot = ~200KB base64 = hundreds of vision tokens. A 5-minute wait at 5-second intervals = 60 LLM calls. Extremely expensive.

2. **Hardcoded wait**: `act: wait, timeMs: 30000`. Dumb — what if it finishes in 2 seconds, or takes 2 minutes?

3. **Background exec + notifyOnExit**: For shell commands only. The agent kicks off a background process, the run ends, and OpenClaw injects a system event when the process exits. This works well but is limited to CLI — no GUI awareness.

4. **Give up**: End the run and hope a heartbeat or cron job picks up where it left off. Fragile.

### What we want

The agent calls `smart_wait({target: "window:Firefox", wake_when: "download complete"})` and its run ends cleanly. A lightweight local vision model (MiniCPM-o, 9B params, runs on CPU) monitors the target efficiently:

- Pixel-diff detection skips unchanged frames (near-zero CPU cost)
- Only sends changed frames to the vision model for semantic evaluation
- Adaptive polling: fast when the screen is changing, slow when static
- When the condition is met, directly wakes the OpenClaw agent with a descriptive message

Cost: essentially free (local model, no API calls). Speed: seconds to detect changes vs. minutes of polling. Intelligence: understands "download complete" semantically, not just pixel patterns.

---

## Problem 3: No Learning from Observation

### What happens today

Every time the agent encounters an unfamiliar UI (a new website, an OS dialog, a desktop application), it figures out what to do from scratch:

1. Take a screenshot
2. Send to Claude/GPT: "What do I see? What should I click?"
3. Execute the action
4. Repeat

This is expensive (every step = a vision model call), slow, and unreliable (the model may misidentify UI elements). Even if the user does the exact same task every day, the agent never learns from watching them.

### What we want

Continuous passive screen recording, indexed by Memories AI's video comprehension engine. When the agent needs to perform a task, it first checks: "Have I seen the user do this before?"

If yes, it gets back a timestamped video segment with extracted step-by-step actions: "On Feb 10 at 3:42 PM, the user: 1) Opened Firefox, 2) Navigated to github.com/settings/keys, 3) Clicked 'New SSH key', 4) Pasted a key from the clipboard, 5) Clicked 'Add SSH key'."

The agent can then replay these steps directly, only falling back to expensive vision reasoning when the UI doesn't match the recorded memory.

---

## Problem 4: No Native Desktop GUI Control

### What OpenClaw can do today

- **Browser**: Full control via CDP (Chrome DevTools Protocol). Can click, type, screenshot, navigate. Works well.
- **CLI**: Full control via exec/process tools. Can run commands, read output, interact with PTYs.
- **Desktop GUI**: Nothing. No ability to interact with native applications, OS dialogs, file managers, system settings, etc.

### What we want

Visual OS control using screenshot + mouse/keyboard injection. The agent takes a screenshot of the desktop (or a specific window), reasons about what it sees, and executes mouse/keyboard actions via `xdotool` (X11) or equivalent.

This is a harder problem and is addressed as a later phase, building on the Smart Wait infrastructure (which already handles screen capture and visual understanding).
