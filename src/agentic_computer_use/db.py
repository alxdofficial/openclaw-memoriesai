"""SQLite database for tasks, plan items, actions, and wait jobs."""
import aiosqlite
import uuid
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    plan_item_id TEXT REFERENCES plan_items(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    action_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    input_data TEXT,
    output_data TEXT,
    duration_ms REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_logs (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES actions(id),
    log_type TEXT NOT NULL DEFAULT 'info',
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    msg_type TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wait_jobs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    criteria TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    poll_interval REAL NOT NULL DEFAULT 2.0,
    status TEXT NOT NULL DEFAULT 'watching',
    result_message TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_items_task_id ON plan_items(task_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_actions_plan_item_id ON actions(plan_item_id);
CREATE INDEX IF NOT EXISTS idx_actions_task_id ON actions(task_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_action_id ON action_logs(action_id);
CREATE INDEX IF NOT EXISTS idx_task_messages_task_id_created_at ON task_messages(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wait_jobs_status ON wait_jobs(status);
"""


async def get_db() -> aiosqlite.Connection:
    config.ensure_data_dir()
    db_conn = await aiosqlite.connect(str(config.DB_PATH))
    db_conn.row_factory = aiosqlite.Row
    await db_conn.executescript(SCHEMA)
    return db_conn


def new_id() -> str:
    return str(uuid.uuid4())[:8]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
