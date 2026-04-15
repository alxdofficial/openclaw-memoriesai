"""Atomic writers for openclaw.json and systemd unit.

Pattern (matches OpenClaw):
  • before every write, rotate `path.bak.{N-1} -> path.bak.N` up to a fixed ring
  • write new content to `path.tmp.<pid>`, fsync, then os.replace → atomic
  • chmod 0600 on both the main file and backups (they can hold API keys)

For the systemd unit the writer is two-stage: it stages a new file under
`/tmp/detm-daemon.service.<pid>` then offers three ways to apply it:
  • if already root: write directly and daemon-reload + restart
  • if sudo -n works: sudo tee / daemon-reload / restart
  • otherwise: print the exact commands the user needs to run
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import state

MAX_BAK = 4  # keeps .bak + .bak.1 .. .bak.4  (5 files total, same as OpenClaw)


# ── .bak ring rotation ─────────────────────────────────────────

def _rotate_bak(path: Path) -> None:
    """Shift .bak.(N-1) → .bak.N, then path → .bak. Silently tolerates missing files."""
    for i in range(MAX_BAK, 0, -1):
        src = path.with_suffix(path.suffix + (f".bak.{i-1}" if i > 1 else ".bak"))
        dst = path.with_suffix(path.suffix + f".bak.{i}")
        if src.exists():
            try:
                shutil.move(str(src), str(dst))
            except OSError:
                pass
    if path.exists():
        try:
            shutil.copy2(str(path), str(path.with_suffix(path.suffix + ".bak")))
        except OSError:
            pass


def _write_atomic(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── openclaw.json writer ───────────────────────────────────────

def update_openclaw_config(
    updater: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Read → mutate (via `updater`) → atomic write. Returns (new_cfg, diff_summary).

    `updater` must return the new config dict (may be the same object mutated in place).
    """
    path = state.OPENCLAW_CONFIG
    current = state.read_openclaw_config(path)
    updated = updater(json.loads(json.dumps(current)))  # deep copy guard

    if updated == current:
        return updated, None

    diff = _summarize_diff(current, updated)
    if dry_run:
        return updated, diff

    _rotate_bak(path)
    _write_atomic(path, json.dumps(updated, indent=2) + "\n", mode=0o600)
    return updated, diff


def _summarize_diff(before: dict, after: dict, prefix: str = "") -> str:
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        here = f"{prefix}{key}"
        if b == a:
            continue
        if isinstance(b, dict) and isinstance(a, dict):
            sub = _summarize_diff(b, a, prefix=here + ".")
            if sub:
                lines.append(sub)
        elif b is None:
            lines.append(f"+ {here} = {_redact(here, a)}")
        elif a is None:
            lines.append(f"- {here}  (was {_redact(here, b)})")
        else:
            lines.append(f"~ {here}: {_redact(here, b)} → {_redact(here, a)}")
    return "\n".join(lines)


def _redact(key: str, value: Any) -> str:
    s = str(value)
    if any(tok in key.lower() for tok in ("key", "token", "secret", "password")):
        if len(s) > 12:
            return f"{s[:6]}…{s[-4:]}  (len={len(s)})"
        return "***"
    if len(s) > 120:
        return s[:117] + "…"
    return s


# ── Systemd unit writer ────────────────────────────────────────

@dataclass
class UnitPlan:
    new_content: str
    changes: list[str]
    apply_cmds: list[list[str]]


def plan_unit_update(
    updates: dict[str, str | None],
    *,
    unit_path: Path = state.SYSTEMD_UNIT,
) -> UnitPlan | None:
    """Compute what the new unit file should look like.

    `updates`: {KEY: value}  — value=None means "remove this KEY".
    Returns None if no change is needed.
    """
    current_text = unit_path.read_text() if unit_path.exists() else _FALLBACK_UNIT
    current_env = state.read_unit_env(unit_path)

    effective = dict(current_env)
    changes: list[str] = []
    for k, v in updates.items():
        if v is None:
            if k in effective:
                changes.append(f"- Environment={k}")
                effective.pop(k)
        else:
            if effective.get(k) != v:
                if k in effective:
                    changes.append(f"~ Environment={k}")
                else:
                    changes.append(f"+ Environment={k}")
                effective[k] = v

    if not changes:
        return None

    new_text = _rewrite_unit_env(current_text, effective)
    apply_cmds = [
        ["sudo", "tee", str(unit_path)],           # stdin = new_text
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "restart", unit_path.stem],
    ]
    return UnitPlan(new_content=new_text, changes=changes, apply_cmds=apply_cmds)


def apply_unit_plan(plan: UnitPlan, *, unit_path: Path = state.SYSTEMD_UNIT) -> bool:
    """Write plan.new_content to the unit (via sudo) and restart the daemon.

    Returns True on success. On permission failure, returns False — caller is
    expected to fall back to printing apply_cmds for the user to run manually.
    """
    if os.geteuid() == 0:
        _write_atomic(unit_path, plan.new_content, mode=0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", unit_path.stem], check=True)
        return True

    if not _sudo_noninteractive_works():
        return False

    p = subprocess.run(
        ["sudo", "tee", str(unit_path)],
        input=plan.new_content, text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        return False
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", unit_path.stem], check=True)
    return True


def _sudo_noninteractive_works() -> bool:
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, timeout=2,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _rewrite_unit_env(unit_text: str, env: dict[str, str]) -> str:
    """Replace every `Environment=` block with a fresh one, preserving everything else."""
    lines = unit_text.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    in_service = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[Service]":
            in_service = True
            out.append(line)
            for k, v in env.items():
                out.append(f"Environment={k}={v}\n")
            inserted = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_service = False
        if in_service and stripped.startswith("Environment="):
            continue  # drop existing; already rewrote above
        out.append(line)
    if not inserted:
        # No [Service] section found — fall back to appending at top
        block = "[Service]\n" + "".join(f"Environment={k}={v}\n" for k, v in env.items())
        return block + unit_text
    return "".join(out)


_FALLBACK_UNIT = """[Unit]
Description=DETM Daemon
After=network.target detm-xvfb.service
Wants=detm-xvfb.service

[Service]
Type=simple
User=%i
WorkingDirectory=/home/%i/openclaw-memoriesai
ExecStart=/home/%i/openclaw-memoriesai/.venv/bin/python3 -m agentic_computer_use.daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
