#!/usr/bin/env python3
"""Create a deterministic smart-wait target and optionally run a wait against it."""
from __future__ import annotations

import argparse
import json

from acu_harness import (
    build_fixture_url,
    capture_task_snapshot_artifact,
    create_run,
    daemon_post,
    ensure_daemon,
    launch_fixture_browser,
    poll_wait,
    print_banner,
    print_kv,
    register_task,
    update_run,
    write_run_artifact,
    wait_for_task_display,
    wait_for_windows,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Smart Wait Fixture")
    parser.add_argument("--auto-ready-seconds", type=float, default=8.0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-run", action="store_true", help="Only set up the target window; do not call smart_wait.")
    args = parser.parse_args()

    ensure_daemon()
    task = register_task(
        args.name,
        ["Open wait fixture", "Run smart_wait against READY badge", "Inspect resulting task logs"],
        isolated_display=True,
    )
    task_id = task["task_id"]
    display = wait_for_task_display(task_id)

    daemon_post("/task_item_update", {"task_id": task_id, "ordinal": 0, "status": "completed"})
    daemon_post("/task_item_update", {"task_id": task_id, "ordinal": 1, "status": "active"})

    url = build_fixture_url("wait", auto_ready_seconds=args.auto_ready_seconds)
    proc = launch_fixture_browser(display, url)
    windows = wait_for_windows(task_id)
    run = create_run(
        args.name,
        "smart_wait",
        task_id=task_id,
        display=display,
        fixture_url=url,
        browser_proc=proc,
        extra={"auto_ready_seconds": args.auto_ready_seconds},
        notes="The fixture flips from WAITING to READY after a deterministic countdown.",
    )
    capture_task_snapshot_artifact(run["run_id"], task_id, "initial_snapshot.jpg")

    print_banner("Smart Wait Fixture Ready")
    print_kv(
        task_id=task_id,
        run_id=run["run_id"],
        portal_url=run["page_url"],
        display=display,
        browser_pid=proc.pid,
        windows=len(windows),
        fixture_url=url,
    )

    if args.no_run:
        print("\nFixture is live. You can now call smart_wait manually against the task display/window.")
        return

    wait_job = daemon_post(
        "/smart_wait",
        {
            "task_id": task_id,
            "target": "screen",
            "wake_when": "STATE: READY is clearly visible on the page",
            "timeout": args.timeout,
        },
    )
    wait_id = wait_job["wait_id"]
    update_run(run["run_id"], extra={"wait_id": wait_id})
    write_run_artifact(run["run_id"], "smart_wait_request.json", json.dumps(wait_job, indent=2))
    print_kv(wait_id=wait_id)

    try:
        result = poll_wait(wait_id, timeout_s=args.timeout + 10)
    except Exception as exc:
        result = {"status": "unknown", "detail": str(exc)}
    update_run(run["run_id"], status=result.get("status", "complete"))
    write_run_artifact(run["run_id"], "smart_wait_result.json", json.dumps(result, indent=2))
    capture_task_snapshot_artifact(run["run_id"], task_id, "final_snapshot.jpg")

    print_banner("Wait Result")
    print(json.dumps(result, indent=2))
    print("\nUse the portal URL to inspect task summary, messages, filtered debug log tail, and cleanup this run.")


if __name__ == "__main__":
    main()
