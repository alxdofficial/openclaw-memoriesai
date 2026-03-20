# Local Test Harness

These scripts exercise DETM directly against the local daemon. They do not go
through Discord, OpenClaw, or the MCP proxy.

Run the daemon first:

```bash
./dev.sh
```

Then use one of the focused scenarios:

```bash
.venv/bin/python3 scripts/acu_scenario_task_memory.py
.venv/bin/python3 scripts/acu_scenario_live_screen.py
.venv/bin/python3 scripts/acu_scenario_smart_wait.py
.venv/bin/python3 scripts/acu_scenario_live_ui.py
.venv/bin/python3 scripts/acu_dump_task.py <task_id>
.venv/bin/python3 scripts/acu_cleanup_test_runs.py
```

Each scenario prints a dedicated test portal URL such as:

```bash
http://127.0.0.1:18891/runs/run-abc123def4
```

That page is separate from the DETM dashboard. It is intended only for local
testing and inspection.

## Scenarios

`acu_scenario_task_memory.py`
- Seeds a task with plan-item transitions, synthetic actions, tool logs, messages, and a scrapped item.
- Good for inspecting dashboard rendering of task hierarchy and action details.

`acu_scenario_live_screen.py`
- Opens a deterministic clock fixture on the shared display in a browser app window.
- Registers a dedicated test page with live task snapshot, task summary, messages, debug tail, and cleanup.
- Good for visually checking snapshot freshness without opening the DETM dashboard.

`acu_scenario_smart_wait.py`
- Opens a fixture page on the shared display that flips from `STATE: WAITING` to `STATE: READY`.
- By default it also calls `smart_wait` directly so you can inspect wait linkage and logs.
- Writes request/result JSON and fixed before/after snapshots into the run artifact directory.

`acu_scenario_live_ui.py`
- Opens a deterministic form page on the shared display.
- By default it calls `gui_agent` with a precise instruction so you can inspect session replay, task actions, and logs.
- The test portal gives you a stable task snapshot and artifact list; the DETM dashboard still remains useful for replaying the recorded `gui_agent` session itself.

`acu_dump_task.py`
- Dumps task summary and messages as JSON.
- Useful for offline inspection or feeding the data back into another agent.

`acu_cleanup_test_runs.py`
- Deletes the dedicated harness artifact directory under `~/.agentic-computer-use/test_runs`.
- Useful when you want to wipe old run pages and captured artifacts in one step.

## Notes

- All test scenarios use the shared system display (`:99`).
- The fixture page is a local `file://` HTML page, so there is no extra server process.
- The scripts intentionally leave windows open after setup so you can inspect them in the portal or dashboard.
- The test portal is started automatically on `127.0.0.1:18891` when needed.
- Each run stores its metadata and artifacts in `/tmp/acu-test-runs/<run_id>/` by default.
- Override that location with `ACU_TEST_RUNS_DIR=/your/path` if you want a persistent test artifact directory.
- The run page has a **Cleanup Run** button that attempts to terminate the spawned browser, delete the linked task, and remove the run directory.
