#!/usr/bin/env python3
"""
Standalone test for the Gemini Live UI provider.
Runs without the DETM daemon — just connects to Gemini Live and executes one instruction.

Usage:
  cd /home/alex/openclaw-memoriesai
  PYTHONPATH=src python3 scripts/test_live_ui.py

Optional env vars:
  ACU_GEMINI_LIVE_MODEL  — override model (default: gemini-2.0-flash-live-001)
  DISPLAY                — X display to capture (default: :99)
  INSTRUCTION            — override the test instruction
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
    "Take one look at the screen and call done() with a one-sentence description of what you see.",
)
TIMEOUT = int(os.environ.get("TIMEOUT", "30"))


async def main():
    print(f"Display  : {DISPLAY}")
    print(f"Instruction: {INSTRUCTION}")
    print(f"Timeout  : {TIMEOUT}s")
    print()

    import uuid
    session = LiveUISession(
        session_id=str(uuid.uuid4()),
        task_id=None,
        instruction=INSTRUCTION,
        context="",
        timeout=TIMEOUT,
    )
    from agentic_computer_use import config
    provider = get_provider()
    print(f"Provider  : {config.LIVE_UI_PROVIDER}")

    result = await provider.run(
        instruction=INSTRUCTION,
        timeout=TIMEOUT,
        task_id=None,
        display=DISPLAY,
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
