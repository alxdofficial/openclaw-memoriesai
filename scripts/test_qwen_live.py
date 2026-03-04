#!/usr/bin/env python3
"""
Minimal probe for the Qwen3-Omni realtime WebSocket protocol.
Connects, sends a text message, and prints all raw events.

Usage:
  cd /home/alex/openclaw-memoriesai
  DASHSCOPE_API_KEY=sk-... PYTHONPATH=src .venv/bin/python3 scripts/test_qwen_live.py
"""
import asyncio
import base64
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

import aiohttp

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
URL = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=qwen3-omni-flash-realtime"
DISPLAY = os.environ.get("DISPLAY", ":99")

log = logging.getLogger("qwen-probe")


async def main():
    if not API_KEY:
        print("Set DASHSCOPE_API_KEY env var")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {API_KEY}"}
    print(f"Connecting to: {URL}")

    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(URL, headers=headers, heartbeat=20.0) as ws:
            print("Connected!")

            # ── 1. Session update — set system prompt + tools ──
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": (
                        "You are a UI automation agent. When given a screenshot, "
                        "describe what you see and call done() when finished."
                    ),
                    "tools": [
                        {
                            "type": "function",
                            "name": "done",
                            "description": "Call when the task is complete.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "success": {"type": "boolean"},
                                },
                                "required": ["summary", "success"],
                            },
                        }
                    ],
                    "tool_choice": "auto",
                },
            }
            print(f"\n→ session.update")
            await ws.send_str(json.dumps(session_update))

            # ── 2. Send a conversation item with image ──
            try:
                from agentic_computer_use.capture.screen import capture_screen, frame_to_jpeg
                frame = capture_screen(display=DISPLAY)
                if frame is not None:
                    jpeg = frame_to_jpeg(frame, max_dim=1280, quality=72)
                    b64 = base64.b64encode(jpeg).decode()
                    item = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image": b64,
                                },
                                {
                                    "type": "input_text",
                                    "text": "Look at this screenshot and call done() with a one-sentence description.",
                                },
                            ],
                        },
                    }
                    print(f"→ conversation.item.create (with image, {len(jpeg)} bytes JPEG)")
                    await ws.send_str(json.dumps(item))
            except Exception as e:
                print(f"(skipping image: {e})")
                # Send text-only fallback
                await ws.send_str(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Say hello and call done()."}],
                    },
                }))

            # ── 3. Trigger response ──
            await ws.send_str(json.dumps({"type": "response.create"}))

            # ── 3. Print all incoming events for 15 seconds ──
            print("\n← Listening for events (15s)...\n")
            try:
                async with asyncio.timeout(15):
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                event_type = data.get("type", "?")
                                print(f"EVENT [{event_type}]: {json.dumps(data, ensure_ascii=False)[:300]}")
                            except Exception:
                                print(f"RAW TEXT: {msg.data[:300]}")
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            print(f"BINARY: {len(msg.data)} bytes — hex: {msg.data[:16].hex()}")
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            print(f"CLOSE: code={ws.close_code} data={msg.data!r}")
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"ERROR: {ws.exception()}")
                            break
            except asyncio.TimeoutError:
                print("\n(15s timeout — done)")


if __name__ == "__main__":
    asyncio.run(main())
