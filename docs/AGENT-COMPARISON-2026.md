# AI Agent Landscape Comparison — February 2026

Quick reference for the team. Covers six systems across the current agent landscape with a focus on operating level, browser control architecture, failure resilience, proactiveness, and what each can and cannot do.

---

## TL;DR Table

| | **Perplexity Comet** | **OpenClaw + Browser Plugin** | **ChatGPT Atlas** | **Claude Cowork** | **Manus AI** | **Our System** |
|---|---|---|---|---|---|---|
| **Made by** | Perplexity AI | Community / Peter Steinberger (ex-PSPDFKit) | OpenAI | Anthropic | Monica / Meta (acquired Dec 2025) | Us — OpenClaw + agentic-computer-use |
| **Operating level** | Browser only | Browser (DOM) | Browser only | Local desktop + VM sandbox | Cloud VM (system-level) | Full desktop OS — headless Linux + Xvfb |
| **Browser control** | Accessibility tree (primary), pixel coords (fallback) | DOM / HTML / JS execution via MCP | Visual/screenshots (CUA) + tight IPC page context | Accessibility tree + text extraction + screenshots | DOM / HTML parsing via Browser Use, screenshot available | Visual grounding (UI-TARS) + iterative narrowing. Browser MCP gives DOM access too. |
| **Desktop apps** | No | No | No | Limited (within VM, no native apps) | Via cloud VM shell | Yes — any X11 app via xdotool |
| **Proactiveness** | High (background assistant, schedules) | Medium (heartbeat, messaging) | Low (reactive sidebar) | Low–medium (runs to completion, no scheduler) | Very high (offline cloud execution, 20 concurrent, event triggers) | High (heartbeat, stuck detection, messaging integrations, smart_wait) |
| **Failure recovery** | Pauses for user; no auto-retry | Limited — no task state | Pauses on sensitive sites; no auto-retry | Sub-agent isolation; hard stop on 5-hr rate limit | 3-attempt retry + strategy change + intermediate saves; hard stop on credit depletion | Task hierarchy + stuck detection + resume packets + iterative narrowing fallback |
| **Price** | Free–$200/mo | Free (bring API keys) | Free browser; agent at $20/mo | $20–$200/mo | $20–$200/mo (credit-metered) | Self-hosted (API key costs only) |

---

## 1. Perplexity Comet

**What it is:** An AI-native Chromium browser where the URL bar is secondary to a conversational prompt box. The browser IS the agent. Comet handles multi-step web workflows natively without switching to a chat interface.

**Made by:** Perplexity AI. Launched mid-2025. Initially $200/mo, relaunched as free browser + tiered upsell October 2025.

### Operating Level

Browser only. No access to local files, local apps, or the desktop OS outside the browser window. Agent scope is strictly the web.

### How It Controls the Browser

**Primarily DOM / accessibility tree — NOT screenshots.**

Under the hood (per reverse engineering by Zenity Labs):
- Calls Chrome's `Accessibility.getFullAXTree` API via Chrome DevTools Protocol
- Converts the accessibility tree to compact YAML: roles, labels, states of interactive elements
- Actions reference element IDs from the tree (`Click ref_32`) — not pixel coordinates
- Screenshots + `ComputerBatch` coordinate-click exist as a fallback for elements outside the tree
- Uses a dual-channel architecture: SSE stream for navigation / tab management, WebSocket for high-frequency RPC actions

**Why this matters:** Accessibility-tree-based control is faster and cheaper than vision (no image encoding, no screenshot latency) and more reliable on standard HTML. The tradeoff: elements without ARIA labels, canvas-rendered content, SVG-only buttons, and heavy JavaScript-generated UIs are invisible to it.

### What It Can Do

- Multi-tab, multi-site research and form-filling workflows
- Gmail, Google Calendar integration
- Summarize any page (text, video, PDFs)
- Background Assistant (Max tier): run multiple autonomous tasks in parallel while you do something else — mission control dashboard shows all running jobs
- Scheduled and recurring tasks (Max tier)
- Underlying model: Claude Sonnet 4.6 (Pro), Claude Opus 4.6 (Max)

### What It Cannot Do

- Access local files or local desktop applications
- Control non-web UI (no DaVinci Resolve, no terminal, no Electron apps)
- Operate on elements with no accessibility labels (image-only buttons, canvas, unlabeled SVGs)
- Guarantee confirmation before sensitive sends (known case: sent an email to the wrong contact without confirming)

### Speed

Fast for structured web tasks. Accessibility tree parsing is orders of magnitude faster than screenshot-round-trip. Practical latency per action: ~200–600 ms (tree fetch + model call + action). Background Assistant parallelism means multiple tasks don't block each other.

### Failure Recovery

The agent pauses and checks with the user before "important or sensitive actions." No documented automatic retry mechanism. If the accessibility tree fails to resolve an element, it falls back to coordinate clicking. No persistent task state — if your session ends, the task does not resume.

### Proactiveness

High relative to this peer group. Background Assistant (Max, $200/mo) enables fully autonomous parallel execution while the user is away. Scheduled tasks enable time-triggered automation. One of the few products here explicitly designed for "run and walk away."

### Pricing

| Tier | Price | Agent access |
|---|---|---|
| Free | $0 | ~5 Pro searches/day, no agent features |
| Pro | $20/mo | 300+ searches/day, core agent mode |
| Max | $200/mo | Unlimited, Background Assistant, schedules |

---

## 2. OpenClaw + Browser Plugin (Basic Setup)

**What it is:** OpenClaw is an open-source AI agent gateway built by Peter Steinberger (ex-PSPDFKit, now joining OpenAI). It connects any LLM (Claude, GPT, Gemini, local models) to your machine and to messaging platforms (WhatsApp, Telegram, Slack, Discord, iMessage, 12+ total). A long-lived server daemon that can wake itself, respond to messages, run scheduled tasks, and act on your behalf.

The "browser plugin" referred to here is typically a browser MCP server (e.g., Playwright MCP, puppeteer-mcp, or a CDP-based tool) that gives OpenClaw DOM-level access to the browser.

**Made by:** Peter Steinberger / open-source community. MIT license.

### Operating Level

Browser (via browser MCP) + limited file/shell access via standard MCP tools. Without agentic-computer-use, it has no visual grounding, no GUI control of desktop apps, and no smart waiting.

### How It Controls the Browser

**DOM / HTML / JavaScript execution via MCP.**

The browser MCP exposes Chrome DevTools Protocol tools to the LLM:
- Navigate to URL, click elements by selector or text, fill forms, read page HTML
- Execute arbitrary JavaScript in page context
- Take screenshots (available but not the primary mechanism)
- No vision-based grounding — the LLM reasons over raw HTML/selectors, which can break on obfuscated or dynamic pages

### What It Can Do

- Multi-channel messaging: receive and send messages across WhatsApp, Telegram, Slack, Discord, iMessage, etc.
- Proactive heartbeat: wakes every 30 minutes to check tasks, news, or conditions — no human prompt required
- Web browsing via browser MCP (form fills, research, data extraction)
- File and shell operations via shell/file MCPs
- Highly extensible: the agent writes its own skill extensions (LLM-authored tools in Python/JS)
- Runs on a personal server — self-hosted, no SaaS dependency

### What It Cannot Do (basic setup, no DETM)

- GUI control of desktop applications (no DaVinci Resolve, no native UI)
- Smart visual waiting — no way to delegate "wait until the render finishes" to a background watcher
- Hierarchical task tracking across context resets — no built-in task state persistence
- Context compaction recovery — if the LLM context fills, there's no resume packet mechanism

### Speed

Good for browser/shell tasks. Browser MCP operations via CDP are fast (~50–200 ms per action). Heartbeat polling adds minimal overhead. No vision round-trips by default.

### Failure Recovery

Minimal in the base configuration. OpenClaw has some retry logic, but there's no persistent hierarchical task state. If the LLM context window fills or the session resets, the task state is lost. No visual waiting means the agent must poll itself or rely on explicit signals.

### Proactiveness

Medium-high. The heartbeat daemon is a genuine differentiator — very few agents in this list wake themselves without a user prompt. OpenClaw will check in, send you a message, or act based on scheduled conditions without needing you to be present.

### Pricing

Free. Self-hosted. Bring your own API keys. Costs = LLM API calls only.

---

## 3. ChatGPT Atlas

**What it is:** A standalone Chromium-based browser built by OpenAI, launched October 21, 2025 (macOS only; Windows announced). ChatGPT is embedded as a persistent sidebar with full page awareness. Agent mode activates within Atlas to take multi-step actions across the web.

This is distinct from — and should not be confused with — **OpenAI Operator** (launched January 2025, shut down August 31, 2025) or **ChatGPT Agent Mode** (Operator's successor, integrated into chatgpt.com in July 2025). Atlas is a browser product; Agent Mode is a chat product.

**Made by:** OpenAI. OWL (OpenAI's Web Layer) is the underlying browser architecture.

### Operating Level

Browser only. Atlas operates entirely within the browser window. No local file system access, no desktop app control, no code execution.

### How It Controls the Browser

**Primarily visual (CUA model) with tighter page integration via OWL IPC.**

The OWL architecture runs Chromium as a separate process (OWL Host) with Atlas as the OWL Client, communicating over Mojo IPC (Chromium's native message-passing system) with GPU compositor memory sharing for pixel frames. This tight integration allows richer page context than the standalone ChatGPT Agent browser. However, the underlying agent model (CUA lineage) is trained to perceive pages visually:

1. Capture screenshot of current browser state
2. CUA model reasons over screenshot + chain of thought
3. Emits `computer_call` actions: `click(x, y)`, `scroll(x, y, direction)`, `type(text)`, `key(combo)`
4. Execute, capture new screenshot, repeat

**Effectively: pure visual control.** The OWL IPC channel provides the capability for DOM access, but CUA is not a DOM-reading model — it sees pixels. This means it works on any website regardless of DOM structure but is slower and degrades on anti-automation measures (CAPTCHAs, rate limits, obfuscated UIs).

Reviewers consistently describe Atlas's agent mode as "very slow."

### What It Can Do

- Sidebar assistant always available — summarize the current page, compare tabs, extract data, without switching apps
- Agent mode for end-to-end web tasks: research → synthesize → act (e.g., "research a meal plan, add ingredients to Amazon Fresh cart")
- Tab groups (January 2026)
- Auto search mode (routes to Google Search or ChatGPT-generated answers based on query type)
- Memory and file recall (paid tiers)
- Works on any website — pure visual means no site-specific integration required

### What It Cannot Do

- Access local files or local applications outside the browser
- Execute JavaScript in page context (no DOM read, no custom scripts)
- Run background/offline tasks (reactive only — you must be present)
- Install browser extensions within Atlas
- Access other tabs' content without switching to them
- Work efficiently on sites with heavy animation or visual noise (CUA accuracy degrades)

### Speed

Slow. Screenshot → model inference → action loop introduces 1–3 seconds of latency per action step. Reviewers specifically flag "very slow" agent mode in practice.

### Failure Recovery

Pauses on sensitive sites (financial, healthcare) and requires user confirmation. Same safety posture as ChatGPT Agent Mode. No documented automatic retry or error recovery. If the visual model cannot locate an element, it may loop or stall — no DOM fallback.

### Proactiveness

Low. Atlas is a reactive browser. The sidebar responds when you engage it. Agent mode activates when you give it a task. No background execution, no scheduled tasks.

### Pricing

| | Price | Agent access |
|---|---|---|
| Atlas browser | Free | Sidebar only (no agent) |
| ChatGPT Plus | $20/mo | Agent mode included |
| ChatGPT Pro | $200/mo | Unlimited agent mode |

---

## 4. Claude Cowork + Chrome Extension

**What it is:** Anthropic's desktop-level agentic product — "Claude Code for knowledge workers." A tab in the Claude Desktop app that gives Claude access to your local machine: files, browser, and external services via MCP connectors. Runs in an isolated Linux VM on your device. Launched as macOS research preview January 2026; expanded to Claude Pro ($20/mo) January 16, 2026; Windows February 10, 2026.

**Made by:** Anthropic.

### Operating Level

Local desktop with VM sandbox. This is the key architectural differentiator: Claude Cowork runs a lightweight Linux VM (Apple VZVirtualMachine on macOS) **on your device** — not in the cloud. The VM has access to a user-granted local folder (read, write, create), a controlled browser via the Claude in Chrome extension, and external services via MCP connectors. This is system-level access sandboxed to a user-defined scope.

### How It Controls the Browser

**Hybrid: accessibility tree (primary) + text extraction + screenshots (fallback).**

The Claude in Chrome extension uses Chrome DevTools Protocol to expose three perception tools:
- `read_page` — reads the full accessibility tree as structured YAML/JSON (same approach as Comet)
- `get_page_text` — raw text extraction for articles, documents, data-heavy pages
- `screenshot` — visual capture for spatial context, images without alt text, fallback verification

And action tools:
- Click elements (by accessibility ID), fill forms, navigate, scroll
- Capture screenshots, monitor network requests, read console logs
- Execute JavaScript in page context (unlike CUA-based systems)

This is the most capable browser control approach in this comparison — the accessibility tree gives semantic precision, text extraction handles content-heavy pages, screenshot handles visual ambiguity, and JS execution gives direct DOM manipulation when needed.

### What It Can Do

- Read, create, and edit local files (including Excel with working formulas, PowerPoint, code)
- Sub-agent coordination: decomposes complex tasks and runs parallel sub-agents
- MCP connectors: Google Drive, Notion, GitHub, Slack, and the broader MCP ecosystem
- Full browser automation via Chrome extension (research + act in one session)
- Long-horizon knowledge work: research → summarize → draft deliverable → save locally
- 1M token context for very large document/file work
- Works on any website without site-specific integration

### What It Cannot Do

- Control non-browser desktop apps (no DaVinci Resolve, Premiere Pro, native UI automation)
- Run indefinitely — rate limits on rolling 5-hour windows; tasks exceeding this are interrupted with no auto-resume
- Access the full file system (scoped to user-granted folder only)
- Share sessions with regular Claude.ai (no Projects, Artifacts, or Memory sync within Cowork)
- Scheduled or background tasks — reactive only; must be present to start a task

### Speed

Moderate. VM start-up adds some overhead on first use but stays warm during a session. Accessibility tree + CDP actions are fast (~100–400 ms per action). Sub-agent parallelism helps on multi-step tasks. Slower than pure DOM-only systems due to VM IPC overhead.

### Failure Recovery

Sub-agent architecture means individual sub-tasks fail in isolation — the top-level task can retry a failed sub-agent without restarting everything. However, the 5-hour rate limit window is a hard stop with no automatic resume. Anthropic explicitly flags prompt injection as a risk: malicious content in files or web pages can hijack Claude's instructions within the VM scope.

### Proactiveness

Low-moderate. Once given a task, Cowork runs autonomously to completion with minimal check-ins (pausing only when intent is ambiguous or actions are potentially destructive). But it does not wake itself, does not have a heartbeat, and does not schedule tasks. You must initiate. More "autonomous executor" than "proactive assistant."

### Pricing

| Tier | Price | Notes |
|---|---|---|
| Pro | $20/mo | Cowork included (since Jan 16, 2026) |
| Max 5x | $100/mo | 5× Pro rate limits |
| Max 20x | $200/mo | 20× Pro rate limits — best for heavy use |

Rate limits are rolling 5-hour windows, not daily. Very long tasks may be cut off mid-execution.

---

## 5. Manus AI

**What it is:** A fully autonomous cloud-based computer-use agent running in a Linux VM. The highest operating scope of any product in this comparison — full shell access, file system, code execution, web browser, and software installation, all running in the cloud. Manus runs long-horizon tasks (hours) while you're offline. Meta acquired Manus (Monica) in December 2025 for a reported $2–3B.

**Made by:** Monica (Chinese AI company). Launched March 6, 2025. Acquired by Meta.

### Operating Level

Cloud VM / system-level — but in the cloud, not your local machine. Manus runs Ubuntu in its own infrastructure with: web browser (Playwright-controlled), Python/Node.js interpreters, shell with sudo, persistent file system, software installation. Additionally, the **Browser Operator Chrome extension** lets Manus control your local browser using your real IP and authenticated session — bypassing CAPTCHAs entirely since the site sees your real identity, not a cloud VM.

### How It Controls the Browser

**DOM / HTML parsing via Browser Use framework — NOT visual.**

Manus uses the open-source Browser Use library for web interaction:
- **Primary**: Parsed HTML and DOM structure — the agent reads actual element attributes, link text, form fields
- **Code-native actions**: Writes Playwright/Selenium Python code to execute browser actions — more reliable than emitted coordinate clicks
- **Screenshots**: Available and captured, but secondary to DOM for page interaction
- **Browser Operator extension**: Operates your local browser directly, using your cookies and session — this is the most reliable approach for authenticated sites (banking, Gmail, SaaS tools with rate limits)

The DOM-first approach makes Manus more reliable than vision-only systems on standard HTML pages. The CodeAct paradigm (actions as executable Python) gives much greater flexibility — actions aren't limited to a fixed set of tool calls.

### What It Can Do

- Full task autonomy with zero supervision needed — designed for "give it a goal, walk away"
- Runs while you're offline; cloud-side execution continues asynchronously
- Persistent `todo.md` checklist: tracks its own progress across the task lifecycle
- Scheduled tasks and event triggers (paid tiers)
- Up to 20 concurrent tasks running simultaneously (paid tiers)
- Complex long-horizon tasks: research → analysis → code → deploy → report
- Browser Operator extension for authenticated local-browser operations
- Writes and executes code, installs packages, creates files, manages a full project

### What It Cannot Do

- Access your local desktop outside the browser extension scope (cloud VM = separate machine)
- Control native desktop applications visually (no video editing software, no Electron apps outside the browser)
- Guarantee completion when credits run out — tasks stop hard mid-execution with no auto-resume
- Handle very dynamic or heavily JS-obfuscated sites reliably (same DOM-parse weakness as Comet)
- Operate cost-predictably — credits are consumed at variable rates with no upfront estimate

### Speed

Moderate to slow for complex tasks. Cloud VM overhead + code execution + browser automation adds up for multi-step tasks. Best framing: Manus is optimized for throughput over speed — it will complete difficult long-horizon tasks autonomously, just not quickly.

### Failure Recovery

The most structured retry logic in this comparison:
1. Diagnoses failure from error messages before retrying
2. Tries alternative selectors, tools, or approaches — not just a blind retry
3. Caps retries at ~3 per failure, then changes strategy rather than looping
4. Saves intermediate results to files — partial work is not lost if a step fails
5. `todo.md` checklist tracks completed items so recovery can resume from last checkpoint

**Hard stop**: tasks terminate when credits are exhausted. No automatic resume, no rollover.

### Proactiveness

Very high — the highest of any product here in terms of raw autonomy. Tasks run fully offline in the background. Scheduled and event-triggered tasks. 20 concurrent tasks. The product is explicitly designed for delegating a full project and not checking back until it's done. The weakness is cost unpredictability — a complex task can silently drain credits.

### Pricing

| Tier | Price | Credits/mo | Concurrent tasks |
|---|---|---|---|
| Free | $0 | Very limited | 1 |
| Standard | $20/mo | 4,000 + 300/day refresh | 20 |
| Customizable | $40/mo | 8,000 | 20 |
| Extended | $200/mo | 40,000 | 20 |

Credits do not roll over. Simple tasks ~10–20 credits; complex applications ~900+ credits.

---

## 6. Our System: OpenClaw + agentic-computer-use (DETM)

**What it is:** OpenClaw (the gateway/agent runtime) augmented with our Desktop Environment Task Manager (DETM) — an MCP server that gives OpenClaw full visual desktop control, hierarchical task memory, smart visual waiting, and pluggable vision backends.

Stack:
```
OpenClaw (gateway, heartbeat, messaging) → DETM (task tracking + GUI skills) → UI-TARS + xdotool (visual grounding + execution)
```

**Made by:** Us.

### Operating Level

Full desktop OS — headless Linux server running Xvfb (virtual X11 framebuffer). Per-task isolated virtual displays (`:100`, `:101`, ...). Any native application that runs under X11 is controllable: DaVinci Resolve, Premiere Pro, LibreOffice, terminals, Electron apps, browsers. This is the widest operating scope of any product in this comparison that runs on your own hardware.

### How It Controls the Browser

**Two modes, both available:**

**1. Browser MCP (DOM-based):** If a Playwright or CDP browser MCP is installed, OpenClaw uses HTML/DOM access — same approach as Comet and Manus. Fast, reliable on standard HTML.

**2. Visual grounding via gui_do (screenshot-based, iterative narrowing):** For browser UIs and all other applications, `gui_do` uses UI-TARS (7B grounding model, OpenRouter-hosted, no GPU needed) with a 3-pass iterative narrowing pipeline:
- Pass 0: Full screenshot (960px) → initial (x, y) prediction
- Pass 1: 300px crop around prediction → refined coordinates (~5× zoom)
- Pass 2: 150px crop → final coordinates (~10× zoom)

This is the most accurate vision-based grounding approach of any system here. It handles any visual UI regardless of DOM structure — including DaVinci Resolve's timeline handles (2–4px wide targets).

For JavaScript execution within browser pages, OpenClaw uses the browser MCP; `gui_do` handles everything else visually.

### What It Can Do

- **Full desktop app control**: DaVinci Resolve, Premiere Pro, LibreOffice, any X11 app
- **Browser automation**: DOM via browser MCP or visual grounding via `gui_do` / `desktop_action`
- **Smart visual waiting**: delegates "wait until the export finishes" to a background daemon with 1s polling + Gemini Flash Lite binary YES/NO evaluation. LLM context is freed during waits.
- **Hierarchical task memory**: Task → Plan Items → Actions → Logs. Survives context resets. Compact-with-expansion view for context compaction recovery.
- **Stuck detection**: background loop wakes OpenClaw with a full resume packet if the agent goes quiet for 5+ minutes
- **Proactive heartbeat** (from OpenClaw): wakes every 30 minutes, checks tasks, acts without user prompting
- **Multi-channel messaging**: WhatsApp, Telegram, Slack, Discord, iMessage — same 12+ platforms as base OpenClaw
- **Per-task display isolation**: each task gets its own Xvfb display; tasks don't interfere with each other
- **Web dashboard**: real-time task tree, live screen MJPEG stream, replay mode, recording controls, download MP4 of completed tasks
- **Video recording**: ffmpeg captures screen clips per task; H.264 MP4 on completion

### What It Cannot Do

- GUI control of cloud-hosted apps (cannot remote-control a different user's machine)
- Voice interaction (no microphone/speaker pipeline yet)
- Confident drag-and-drop trajectories for sub-pixel precision (e.g., DaVinci Resolve trim handles — iterative narrowing helps locate the target but true drag trajectories need ShowUI-π, which has no released weights yet)
- Video comprehension / procedural memory from recordings (Phase 4 — Memories AI integration not yet built)

### Speed

| Operation | Typical latency |
|---|---|
| `smart_wait` poll cycle | 1 s (fixed) + ~200–400 ms vision call |
| `gui_do` (3-pass narrowing) | 3 × ~400 ms vision calls ≈ 1.2 s total |
| `desktop_action` (explicit coords) | ~10–50 ms (pure xdotool) |
| `desktop_look` (screenshot + description) | ~500–800 ms |
| Task DB operations | <5 ms (SQLite local) |

The 3-pass narrowing is the main latency cost for `gui_do`. For precision-critical targets (timeline handles, small buttons), this is the right tradeoff. For bulk DOM-based browser work, use the browser MCP directly to skip visual grounding entirely.

### Failure Recovery

The most sophisticated recovery architecture in this comparison:

1. **Task hierarchy**: each action is logged under a plan item. If a step fails, the task state shows exactly which item was active — no re-derivation needed on resume.
2. **Stuck detection**: if the agent goes quiet for 5 minutes (not in an active `smart_wait`), the daemon injects a resume packet into OpenClaw with: task state, active item expanded with recent actions and logs, last 5 messages. LLM can pick up where it left off.
3. **Iterative narrowing fallback**: if pass 2 (150px) returns None, falls back to pass 1 result. If pass 1 returns None, falls back to initial full-frame prediction. Never hard-fails on a grounding miss — always returns the best result available.
4. **Context compaction**: `detail_level="focused"` gives a compact task view with only the active item expanded — exactly the right amount of context for resumption after a compaction event.
5. **Per-task display isolation**: one task's crashed GUI actions don't affect other tasks' Xvfb displays.

**Not covered yet:** Memories AI integration (Phase 4) will add video-based procedural memory so the agent can recall past workflows and avoid re-learning the same UI patterns.

### Proactiveness

High. OpenClaw's heartbeat daemon wakes every 30 minutes without user prompting, checks active tasks, sends status updates, or acts on scheduled conditions. `smart_wait` delegates visual monitoring so the agent can truly step back — it is woken only when a condition is visibly met. Stuck detection means a stalled agent recovers without user intervention. Messaging integrations mean tasks can be initiated from WhatsApp or Telegram, not just a keyboard.

### Pricing

Self-hosted. All costs are API calls:
| Call | Cost |
|---|---|
| `smart_wait` poll | ~$0.000045 (Gemini Flash Lite via OpenRouter) |
| `gui_do` (3-pass) | ~$0.0009 (3 × UI-TARS via OpenRouter ~$0.0003/call) |
| `desktop_look` | ~$0.0003 (one vision call) |
| LLM (OpenClaw) | Model-dependent |

---

## Dimension Comparison

### Operating Level

```
System-level (cloud)  │  Manus AI
──────────────────────┤
System-level (local)  │  Our System (headless Linux, any X11 app)
                      │  Claude Cowork (VM sandbox, local files + browser)
──────────────────────┤
Browser + desktop     │  (nothing here yet)
──────────────────────┤
Browser only          │  Perplexity Comet
                      │  ChatGPT Atlas
                      │  OpenClaw + browser plugin (without DETM)
```

### Browser Control Architecture

| System | Primary mechanism | Fallback | JS execution |
|---|---|---|---|
| Perplexity Comet | Accessibility tree (CDP `getFullAXTree`) | Pixel coordinate click | No |
| OpenClaw + browser MCP | HTML / DOM selectors | Screenshot | Yes |
| ChatGPT Atlas | Visual / screenshots (CUA) | — | No |
| Claude Cowork | Accessibility tree (`read_page`) | Screenshots | Yes (via extension) |
| Manus AI | DOM / HTML parsing (Browser Use) | Screenshots | Yes (Playwright code) |
| Our System (`gui_do`) | Visual grounding (UI-TARS, 3-pass) | Falls back through passes | No (use browser MCP for JS) |

**Rule of thumb:**
- Accessibility tree = fast, reliable on standard HTML, breaks on unlabeled/canvas elements
- DOM/HTML = most robust for well-structured pages, breaks on heavily obfuscated or canvas-heavy UIs
- Visual (CUA / UI-TARS) = works on anything visual regardless of DOM structure, slower, best for native apps

### Failure Recovery

| System | Strategy | Hard limit |
|---|---|---|
| Perplexity Comet | Pause for user confirmation | Session end |
| OpenClaw + browser MCP | Minimal | Context window |
| ChatGPT Atlas | Pause on sensitive sites | Session end |
| Claude Cowork | Sub-agent isolation | 5-hour rate limit window |
| Manus AI | 3-attempt retry + strategy change + file saves | Credit depletion |
| Our System | Task hierarchy + stuck detection + resume packets + narrowing fallback | None (self-resumes) |

### Proactiveness

| System | Wakes itself | Background tasks | Schedules | Multi-concurrent |
|---|---|---|---|---|
| Perplexity Comet | No | Yes (Max tier) | Yes (Max tier) | Yes (background assistant) |
| OpenClaw + browser MCP | Yes (heartbeat) | No | No | No |
| ChatGPT Atlas | No | No | No | No |
| Claude Cowork | No | No | No | No |
| Manus AI | No (initiated by user) | Yes (runs offline) | Yes | Yes (up to 20) |
| Our System | Yes (heartbeat + stuck detection) | Yes (smart_wait in background) | Via OpenClaw | Yes (per-task displays) |

### Task Scope

| | Web research | Form filling | Local files | Desktop apps | Native video editing | Code execution | Messaging |
|---|---|---|---|---|---|---|---|
| Perplexity Comet | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| OpenClaw + browser MCP | ✅ | ✅ | Partial | ❌ | ❌ | ✅ (shell) | ✅ (12+ platforms) |
| ChatGPT Atlas | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Claude Cowork | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ (in VM) | ❌ |
| Manus AI | ✅ | ✅ | Cloud only | Cloud shell | ❌ | ✅ (cloud VM) | ❌ |
| **Our System** | ✅ | ✅ | ✅ | **✅** | **✅** | ✅ (shell) | ✅ (12+ platforms) |

---

## Where We Sit

Our system is the only one in this list that can:
1. Control native desktop applications visually (DaVinci Resolve, Premiere Pro, LibreOffice) via UI-TARS grounding
2. Wake itself proactively AND recover from stalls automatically (heartbeat + stuck detection + resume packets)
3. Delegate visual waiting without holding the LLM context (smart_wait)
4. Maintain hierarchical task state across context resets and compaction events
5. Run entirely self-hosted with bring-your-own-keys pricing

The main gap vs the commercial products is proactive concurrency (Manus runs 20 tasks simultaneously in the cloud; we run per-task displays but the bottleneck is OpenClaw's single agent loop) and video comprehension (Phase 4 — not yet built).

---

## Product Genealogy

```
Jan 2025  OpenAI Operator launched (standalone browser agent, visual control)
Mar 2025  Manus AI launched (cloud VM, DOM + code-native)
Mid 2025  Perplexity Comet launched (AI-native browser, accessibility tree)
Jul 2025  ChatGPT Agent Mode launched (Operator successor, integrated into ChatGPT)
Aug 2025  OpenAI Operator shut down (functionality absorbed into Agent Mode)
Oct 2025  ChatGPT Atlas launched (standalone Chromium browser, OWL architecture)
Oct 2025  Comet relaunched as free browser + tiered upsell
Dec 2025  Meta acquires Manus/Monica ($2–3B)
Jan 2026  Claude Cowork launched (local desktop VM, hybrid browser control)
Feb 2026  This document
```

---

*Research date: February 2026. Sources: Perplexity blog, Zenity Labs reverse engineering, OpenAI OWL architecture post, Anthropic Cowork docs, Manus documentation, Wikipedia (Manus, Operator, Atlas), multiple product reviews and technical analyses.*
