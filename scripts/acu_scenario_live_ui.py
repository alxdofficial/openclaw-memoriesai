#!/usr/bin/env python3
"""Create a deterministic live_ui target and optionally run live_ui against it."""
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
    parser.add_argument("--name", default="Live UI Fixture")
    parser.add_argument("--expected-text", default="open sesame")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--instruction", default=None)
    parser.add_argument("--context", default=None)
    parser.add_argument("--no-run", action="store_true", help="Only set up the page; do not invoke live_ui.")
    args = parser.parse_args()

    ensure_daemon()
    task = register_task(
        args.name,
        ["Open deterministic live_ui form", "Run live_ui against the form", "Inspect live session replay and logs"],
        isolated_display=True,
    )
    task_id = task["task_id"]
    display = wait_for_task_display(task_id)

    daemon_post("/task_item_update", {"task_id": task_id, "ordinal": 0, "status": "completed"})
    daemon_post("/task_item_update", {"task_id": task_id, "ordinal": 1, "status": "active"})

    url = build_fixture_url("form", expected_text=args.expected_text)
    proc = launch_fixture_browser(display, url)
    windows = wait_for_windows(task_id)

    instruction = args.instruction or (
        "Use the visible UI to unlock the workflow and finish it. "
        f"Enter the passphrase {args.expected_text} exactly once in the passphrase field, then complete the workflow. "
        "Only call done after you can see WORKFLOW COMPLETE on screen."
    )
    context = args.context or (
        "Test fixture notes: stay inside the page that is already open. "
        "Ignore the inspection notes sidebar on the right. "
        "The main workflow is in the central panel. "
        "To make the passphrase input editable, click the large dark button labeled Enable Editing. "
        "The passphrase field has the placeholder 'type passphrase here'. "
        "After the correct text is entered, the status changes to READY TO COMPLETE and the Complete Workflow button becomes actionable."
    )
    run = create_run(
        args.name,
        "live_ui",
        task_id=task_id,
        display=display,
        fixture_url=url,
        browser_proc=proc,
        extra={"expected_text": args.expected_text, "instruction": instruction, "context": context, "timeout": args.timeout},
        notes="The portal page shows the live task snapshot plus task metadata and artifacts. The live_ui session replay remains available from the DETM dashboard.",
    )
    write_run_artifact(run["run_id"], "live_ui_instruction.txt", instruction + "\n")
    write_run_artifact(run["run_id"], "live_ui_context.txt", context + "\n")
    write_run_artifact(
        run["run_id"],
        "live_ui_request.json",
        json.dumps(
            {
                "task_id": task_id,
                "instruction": instruction,
                "context": context,
                "timeout": args.timeout,
            },
            indent=2,
        ),
    )
    capture_task_snapshot_artifact(run["run_id"], task_id, "initial_snapshot.jpg")

    print_banner("Live UI Fixture Ready")
    print_kv(
        task_id=task_id,
        run_id=run["run_id"],
        portal_url=run["page_url"],
        display=display,
        browser_pid=proc.pid,
        windows=len(windows),
        fixture_url=url,
        instruction=instruction,
        timeout=f"{args.timeout}s",
    )

    if args.no_run:
        print("\nFixture is live. You can now invoke live_ui manually or inspect with desktop_look/gui_do.")
        return

    result = daemon_post(
        "/live_ui",
        {
            "task_id": task_id,
            "instruction": instruction,
            "timeout": args.timeout,
            "context": context,
        },
    )
    task_status = "completed" if result.get("success") else "failed"
    extra = {"session_id": result.get("session_id"), "success": result.get("success")}
    update_run(run["run_id"], status=task_status, extra=extra)
    daemon_post("/task_update", {"task_id": task_id, "status": task_status, "message": result.get("summary", "")})
    write_run_artifact(run["run_id"], "live_ui_result.json", json.dumps(result, indent=2))
    capture_task_snapshot_artifact(run["run_id"], task_id, "final_snapshot.jpg")

    print_banner("live_ui Result")
    print(json.dumps(result, indent=2))
    print("\nUse the portal URL for test-run inspection, and the DETM dashboard for the live_ui session replay popup.")


if __name__ == "__main__":
    main()
