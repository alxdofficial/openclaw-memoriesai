#!/usr/bin/env python3
"""Dump task summary, messages, and live session references for offline inspection."""
from __future__ import annotations

import argparse
import json

from acu_harness import print_banner, read_messages, summarize_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--detail", default="full", choices=["items", "actions", "full", "focused"])
    parser.add_argument("--messages", type=int, default=100)
    args = parser.parse_args()

    summary = summarize_task(args.task_id, detail=args.detail)
    messages = read_messages(args.task_id, limit=args.messages)

    print_banner("Task Summary")
    print(json.dumps(summary, indent=2))
    print_banner("Task Messages")
    print(json.dumps(messages, indent=2))


if __name__ == "__main__":
    main()
