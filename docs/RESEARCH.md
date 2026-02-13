# Research Notes

## Existing Visual OS Control Agents

### Claude Computer Use (Anthropic)
- **How**: Takes screenshots via a virtual X11 display (Xvfb), returns mouse/keyboard actions
- **API**: `computer_20250124` tool type, requires beta header
- **Quality**: Decent but not best-in-class for GUI grounding (27.7% on ScreenSpotPro)
- **Cost**: Every step = a Claude vision API call (expensive for long workflows)
- **Deployment**: Docker container with Xvfb + VNC
- **URL**: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool

### UI-TARS 1.5 (ByteDance, Open Source)
- **How**: 7B VLM trained specifically for GUI grounding and action prediction
- **Quality**: 61.6% on ScreenSpotPro (best in class, 2x Claude)
- **Cost**: Free, runs locally
- **Deployment**: HuggingFace, Ollama, llama.cpp, vLLM
- **Key advantage**: Trained on UI-specific data, much better at identifying clickable elements
- **URL**: https://github.com/bytedance/UI-TARS

### Agent S2 (Simular AI, Open Source)
- **How**: Modular framework for computer use agents, supports multiple model backends
- **Features**: Grounding via UI-TARS, supports OpenAI/Claude/local models
- **URL**: https://github.com/simular-ai/Agent-S

### OpenAI Operator / CUA
- **How**: Similar to Claude Computer Use — screenshot-based with action output
- **Quality**: 23.4% on ScreenSpotPro (below Claude)
- **Cost**: API-based

### Microsoft Copilot Studio Computer Use (April 2025)
- **How**: Enterprise-focused, works across desktop and browser apps
- **URL**: https://www.microsoft.com/en-us/microsoft-copilot/blog/copilot-studio/announcing-computer-use-microsoft-copilot-studio-ui-automation/

## Learning from User Actions

### Kairos (April 2025)
- **How**: Records user's screen + spoken explanation, then automates the task
- **Key insight**: Combines visual recording with natural language narration for richer understanding
- **Status**: New, not widely available
- **URL**: https://aiagent.marktechpost.com/post/meet-kairos-an-ai-agent-that-automates-workflows-by-recording-your-screen

### AgentRR — Record & Replay (Shanghai Jiao Tong, May 2025)
- **Paper**: https://arxiv.org/abs/2505.17716
- **How**: Records successful agent executions as reusable "experiences"
- **Key features**:
  - Multi-level abstraction (concrete actions → generalized procedures)
  - Check functions as safety boundaries for replay
  - Experience Store for sharing/reusing knowledge
  - Supports user-recorded demonstrations
  - Large-small model collaboration (record with big model, replay with small)
  - Privacy-aware: local replay minimizes data sent to remote servers
- **Most relevant to our project** — closest to what we're building

### "Watch and Learn" (Oct 2025 paper)
- **Paper**: https://arxiv.org/abs/2510.04673
- **How**: Trains an inverse dynamics model (IDM) on screenshots to recover user actions
- **Pipeline**: 
  1. Collect screenshots + action pairs
  2. Train IDM to predict actions from consecutive frames
  3. Apply to tutorial videos to extract trajectories
  4. Use as in-context examples or fine-tuning data
- **Key insight**: You can learn actions from unlabeled video by training a frame-pair → action model

### Reddit Projects
- Screen recording upload → AI agent generation platform (April 2025)
- Multiple people building "learn by watching" prototypes
- Common approach: upload video → extract steps → generate automation script

## MiniCPM-o 4.5

### Specs
- **Parameters**: 9B total (SigLip-400M vision encoder + Qwen2-7B language model)
- **Vision**: Up to 1344×1344 pixels, strong OCR, video understanding
- **Speech**: Full-duplex voice conversation (not needed for our use case)
- **Quality**: Approaches Gemini 2.5 Flash on vision benchmarks
- **Released**: February 2026

### Deployment Options
| Method | Quantization | RAM | Speed | Ease |
|--------|-------------|-----|-------|------|
| Ollama | Q4 | ~5 GB | Fast | Easiest |
| llama.cpp | Q4_K_M | ~5 GB | Fast | Medium |
| vLLM | FP16 | ~18 GB (GPU) | Fastest | Harder |
| HF Transformers | FP16 | ~18 GB (GPU) | Medium | Easy for Python |

### Why MiniCPM-o over alternatives?

| Model | Size | GUI Grounding | Local? | Video? | Cost |
|-------|------|---------------|--------|--------|------|
| Claude 3.5 | N/A | 27.7% ScreenSpot | No | No | $$$ |
| GPT-4o | N/A | ~25% | No | Yes | $$$ |
| UI-TARS 1.5 | 7B | 61.6% ScreenSpot | Yes | No | Free |
| MiniCPM-o 4.5 | 9B | Good (untested on ScreenSpot) | Yes | Yes | Free |
| Qwen2.5-VL 7B | 7B | Good | Yes | Yes | Free |

**Decision**: MiniCPM-o 4.5 as primary backbone because:
1. Runs locally (privacy, cost)
2. Video understanding (for smart wait's continuous monitoring)
3. Strong OCR (reading error messages, progress indicators)
4. Good general vision (not GUI-specialized but sufficient for condition evaluation)

For GUI grounding specifically (Phase 5: visual OS control), UI-TARS 1.5 would be better. We could run both — MiniCPM-o for monitoring/understanding, UI-TARS for action grounding.

## OpenClaw Internals (relevant to integration)

### Agent Loop
- Serialized per session: message → inference → tool calls → inference → reply
- Tools can chain: model calls tool, gets result, calls another tool, etc.
- Default timeout: 600 seconds
- Background exec `notifyOnExit`: when a background shell command completes, OpenClaw injects a system event and requests a heartbeat — this is the closest existing pattern to what smart_wait needs

### Wake Mechanisms
1. **Heartbeat**: Periodic poll (every ~30 min). Too slow for smart_wait.
2. **Cron**: Scheduled events. Too rigid for dynamic conditions.
3. **cron.wake (mode: "now")**: Pokes next heartbeat immediately. Better but still indirect.
4. **exec notifyOnExit**: Background process exit → system event → agent wakes. Best existing pattern.
5. **Gateway RPC**: Direct `POST /api/agent` with a message. Most direct but needs auth token.

### Browser Control
- CDP-based (Chrome DevTools Protocol)
- Snapshot (accessibility tree) + screenshot (image) + actions (click/type/etc.)
- Managed `openclaw` profile or Chrome extension relay
- Browser only — no desktop GUI control

### MCP Support
- OpenClaw supports MCP servers via the `mcporter` skill
- Can register stdio or HTTP MCP servers
- Tools from MCP servers appear in the agent's tool list
- This is our integration path
