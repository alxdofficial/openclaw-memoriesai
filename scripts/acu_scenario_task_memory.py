#!/usr/bin/env python3
"""Seed a self-contained task with representative task-memory structure and logs."""
from __future__ import annotations

import argparse
import json
import time

from acu_harness import ensure_daemon, print_banner, print_kv, register_task, summarize_task
from acu_harness import create_run, write_run_artifact

import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_computer_use.task import manager as task_mgr


async def seed(task_id: str) -> None:
    await task_mgr.update_plan_item(task_id, 0, "active", note="Bootstrapping a deterministic memory fixture")
    await task_mgr.log_action(task_id, "cli", "prepared local fixture data", input_data="seed=true", status="completed", ordinal=0)
    await task_mgr.append_tool_log(task_id, "stdout", "fixture data prepared")
    await task_mgr.update_plan_item(task_id, 0, "completed", note="Initial setup completed")

    await task_mgr.update_plan_item(task_id, 1, "active", note="Running representative tool actions")
    started = await task_mgr.log_action(
        task_id,
        "wait",
        "Started synthetic wait on fixture page",
        input_data=json.dumps({"wait_id": "demo123", "target": "screen", "criteria": "READY badge visible", "timeout": 30}),
        status="started",
        ordinal=1,
    )
    await task_mgr.append_tool_log(task_id, "verdict", "[NO] READY badge not visible yet")
    await task_mgr.append_tool_log(task_id, "verdict", "[YES] READY badge visible in primary panel")

    await task_mgr.log_action(
        task_id,
        "gui",
        "click at (420, 260): clicked Enable Editing",
        input_data=json.dumps({"instruction": "click Enable Editing", "screenshot": {"thumb": "missing-thumb.jpg"}}),
        output_data=json.dumps({"ok": True, "action": "click", "x": 420, "y": 260, "confidence": 0.92, "provider": "fixture"}),
        status="completed",
        ordinal=1,
    )
    await task_mgr.append_tool_log(task_id, "info", "button visually confirmed after click")
    await task_mgr.update_plan_item(task_id, 1, "scrapped", note="Changed approach after synthetic exercise")

    await task_mgr.append_plan_items(task_id, ["Review resulting task tree", "Inspect dashboard messages"], note="Added review steps after scrapping the synthetic action item")
    await task_mgr.post_message(task_id, "system", "Task memory fixture seeded for dashboard inspection.", "progress")


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Task Memory Fixture")
    args = parser.parse_args()

    ensure_daemon()
    task = register_task(
        args.name,
        ["Set up memory fixture", "Run synthetic actions", "Review output"],
        isolated_display=False,
    )
    task_id = task["task_id"]

    asyncio.run(seed(task_id))
    summary = summarize_task(task_id, detail="full")
    run = create_run(
        args.name,
        "task_memory",
        task_id=task_id,
        notes="A seeded task-memory fixture with representative actions, logs, messages, and a scrapped item.",
    )
    write_run_artifact(run["run_id"], "task_summary.json", json.dumps(summary, indent=2))

    print_banner("Task Memory Fixture Ready")
    print_kv(task_id=task_id, run_id=run["run_id"], portal_url=run["page_url"])
    print("\nSummary excerpt:")
    print(json.dumps({"name": summary["name"], "status": summary["status"], "items": summary["items"]}, indent=2))


if __name__ == "__main__":
    main()
