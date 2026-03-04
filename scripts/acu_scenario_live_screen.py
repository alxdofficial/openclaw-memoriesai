#!/usr/bin/env python3
"""Create a self-contained live-screen latency scenario on an isolated display."""
from __future__ import annotations

import argparse
import time

from acu_harness import (
    build_fixture_url,
    capture_task_snapshot_artifact,
    create_run,
    ensure_daemon,
    launch_fixture_browser,
    print_banner,
    print_kv,
    register_task,
    write_run_artifact,
    wait_for_task_display,
    wait_for_windows,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Live Screen Latency Fixture")
    args = parser.parse_args()

    ensure_daemon()
    task = register_task(
        args.name,
        ["Open ticking fixture", "Inspect dashboard live screen latency"],
        isolated_display=True,
    )
    task_id = task["task_id"]
    display = wait_for_task_display(task_id)

    url = build_fixture_url("clock")
    proc = launch_fixture_browser(display, url)
    windows = wait_for_windows(task_id)
    time.sleep(1.0)
    run = create_run(
        args.name,
        "live_screen",
        task_id=task_id,
        display=display,
        fixture_url=url,
        browser_proc=proc,
        notes="Compare the fixture clock window to the test portal snapshot and the DETM dashboard snapshot.",
    )
    write_run_artifact(
        run["run_id"],
        "scenario_notes.txt",
        "Clock fixture updates every 100ms. The portal snapshot polls every 500ms through the daemon task snapshot endpoint.\n",
    )
    capture_task_snapshot_artifact(run["run_id"], task_id, "initial_snapshot.jpg")

    print_banner("Live Screen Fixture Ready")
    print_kv(
        task_id=task_id,
        run_id=run["run_id"],
        portal_url=run["page_url"],
        display=display,
        browser_pid=proc.pid,
        windows=len(windows),
        fixture_url=url,
    )
    print("\nOpen the portal URL to inspect the test-only page with live snapshot, task summary, messages, and cleanup controls.")


if __name__ == "__main__":
    main()
