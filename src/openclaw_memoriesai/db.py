"""SQLite database for tasks and wait jobs."""
import aiosqlite
import uuid
from datetime import datetime, timezone
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    plan TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
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
"""

async def get_db() -> aiosqlite.Connection:
    config.ensure_data_dir()
    db = await aiosqlite.connect(str(config.DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    return db

def new_id() -> str:
    return str(uuid.uuid4())[:8]

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
