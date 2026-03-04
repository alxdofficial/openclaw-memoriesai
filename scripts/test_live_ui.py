#!/usr/bin/env python3
"""
Standalone test for the OpenRouter live_ui provider.
Runs without the DETM daemon and executes one instruction against the current display.

Usage:
  cd /home/alex/openclaw-memoriesai
  PYTHONPATH=src python3 scripts/test_live_ui.py

Optional env vars:
  ACU_OPENROUTER_LIVE_MODEL  — override model (default: google/gemini-3.1-flash-lite-preview)
  DISPLAY                    — X display to capture (default: :99)
  INSTRUCTION                — override the test instruction
  CONTEXT                    — optional extra context passed separately from the instruction
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
# Quieten noisy libraries
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

from agentic_computer_use.live import get_provider
from agentic_computer_use.live.session import LiveUISession

DISPLAY = os.environ.get("DISPLAY", ":99")
INSTRUCTION = os.environ.get(
    "INSTRUCTION",
    "Use the visible UI to complete the requested task. Only call done after the requested completion state is visible on screen.",
)
CONTEXT = os.environ.get("CONTEXT", "")
TIMEOUT = int(os.environ.get("TIMEOUT", "120"))


async def main():
    print(f"Display  : {DISPLAY}")
    print(f"Instruction: {INSTRUCTION}")
    if CONTEXT:
        print(f"Context  : {CONTEXT}")
    print(f"Timeout  : {TIMEOUT}s")
    print()

    import uuid
    session = LiveUISession(
        session_id=str(uuid.uuid4()),
        task_id=None,
        instruction=INSTRUCTION,
        context=CONTEXT,
        timeout=TIMEOUT,
    )
    from agentic_computer_use import config
    provider = get_provider()
    print("Provider  : openrouter")
    print(f"Model     : {config.OPENROUTER_LIVE_MODEL}")

    result = await provider.run(
        instruction=INSTRUCTION,
        timeout=TIMEOUT,
        task_id=None,
        display=DISPLAY,
        context=CONTEXT,
        session=session,
    )

    import json
    print("── Result ──────────────────────────────────")
    print(json.dumps(result, indent=2))
    print()
    print(f"Session ID : {session.id}")
    print(f"Session dir: {session._dir}")
    print(f"Frames     : {session._frame_count}")


if __name__ == "__main__":
    asyncio.run(main())
