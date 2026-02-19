# Tool Reference

## Task Management (Hierarchical)

### `task_register`

Create a task with plan items.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Task name |
| `plan` | string[] | yes | Ordered checklist of plan items |
| `metadata` | object | no | Arbitrary key-value pairs (e.g., `assigned_window_id`) |

Returns: `task_id`, plan items with ordinals.

### `task_update`

Post progress, change status, or query a task.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID |
| `message` | string | no | Logged as action under active plan item |
| `query` | string | no | AI-answered question about task state |
| `status` | string | no | active, paused, completed, failed, cancelled |

### `task_item_update`

Update a specific plan item's status.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID |
| `ordinal` | int | yes | Plan item index (0-based) |
| `status` | string | yes | pending, active, completed, failed, skipped |
| `note` | string | no | Note about the status change |

### `task_log_action`

Log a discrete action under a task's active plan item.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID |
| `action_type` | string | yes | cli, gui, wait, vision, reasoning, other |
| `summary` | string | yes | What was done |
| `input_data` | string | no | Command/input |
| `output_data` | string | no | Result/output |
| `status` | string | no | started, completed, failed (default: completed) |
| `ordinal` | int | no | Plan item index (defaults to active item) |

### `task_summary`

Get a summary of a task. Default view is item-level.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID |
| `detail_level` | string | no | items (default), actions, full |

### `task_drill_down`

Drill into a specific plan item to see its actions and logs.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID |
| `ordinal` | int | yes | Plan item index (0-based) |

### `task_thread`

Legacy message thread view. Use `task_summary` for the structured view.

### `task_list`

List tasks by status (default: active).

---

## Smart Wait

### `smart_wait`

Delegate visual monitoring to the daemon. Your run can end — you'll get a system event when the condition is met or times out.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | string | yes | `window:<name_or_id>` or `screen` |
| `wake_when` | string | yes | NL condition to watch for |
| `timeout` | int | no | Seconds before giving up (default: 300) |
| `task_id` | string | no | Link wait to a task |
| `poll_interval` | float | no | Base interval in seconds (default: 2.0) |

### `wait_status`

Check active wait jobs. Optionally filter by `wait_id`.

### `wait_update`

Refine an active wait — update the condition or extend the timeout.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `wait_id` | string | yes | Wait job to update |
| `wake_when` | string | no | Updated condition |
| `timeout` | int | no | New timeout (resets clock) |
| `message` | string | no | Note to attach |

### `wait_cancel`

Cancel an active wait job.

---

## GUI Agent (NL Grounding)

### `gui_do`

Execute a GUI action by natural language or explicit coordinates.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `instruction` | string | yes | NL description or `click(x, y)` |
| `task_id` | string | no | Link action to a task |
| `window_name` | string | no | Target window (auto-focused) |

NL instructions are grounded to coordinates by the vision model (UI-TARS or Claude CU), then executed via xdotool. Explicit coordinates bypass grounding.

### `gui_find`

Locate a UI element without acting. Returns coordinates.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | yes | What to find |
| `window_name` | string | no | Target window |

---

## Desktop Control

### `desktop_action`

Raw xdotool control.

| Action | Params | Description |
|--------|--------|-------------|
| `click` | x, y, button | Click at coordinates |
| `double_click` | x, y | Double-click |
| `type` | text | Type text |
| `press_key` | text | Press key (e.g., "Return", "ctrl+c") |
| `mouse_move` | x, y | Move mouse |
| `drag` | x, y, x2, y2, button | Drag from (x,y) to (x2,y2) |
| `screenshot` | — | Save screenshot, return path |
| `list_windows` | — | List all windows |
| `find_window` | window_name | Find window by name |
| `focus_window` | window_id/window_name | Focus a window |
| `resize_window` | window_id/window_name, width, height | Resize |
| `move_window` | window_id/window_name, x, y | Move |
| `close_window` | window_id/window_name | Close |
| `get_mouse_position` | — | Current mouse coordinates |

### `desktop_look`

Screenshot + vision description of the screen.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | no | What to describe (default: everything visible) |
| `window_name` | string | no | Focus a window first |

### `video_record`

Record a short screen clip.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | string | no | `screen` or `window:<name>` |
| `duration` | int | no | Seconds (1-120, default: 10) |
| `fps` | int | no | Frames per second (default: 5) |

Returns the file path of the recording.

---

## System

### `health_check`

Check daemon, vision backend, and system health.

### `memory_search`, `memory_read`, `memory_append`

Workspace memory files (MEMORY.md and memory/*.md).
