"""Shared helpers for section modules."""
from __future__ import annotations

from ..state import EffectiveConfig, SECTION_ENV
from .. import prompt as P


def show_env_section(section: str, eff: EffectiveConfig) -> None:
    P.header(section)
    keys = SECTION_ENV.get(section, [])
    if not keys:
        P.info(P.dim("  (no env vars in this section)"))
        return
    for env_key, desc in keys:
        val, src = eff.get(env_key)
        redacted = P.redact(val) if any(t in env_key.lower() for t in ("key", "token", "secret")) else val
        tag = {"env": P.yellow(" [proc env]"),
               "unit": P.green(" [systemd]"),
               "default": P.dim(" [default]"),
               "unset": P.dim(" [unset]")}[src]
        print(f"  {P.bold(env_key):<40} {redacted!s:<45} {tag}")
        if desc:
            print(f"    {P.dim(desc)}")


def parse_kv(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"expected KEY=VAL, got: {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def apply_env_updates(
    updates: dict[str, str | None],
    *,
    dry_run: bool = False,
) -> str:
    """Write `updates` to the systemd unit. Returns a human summary."""
    from .. import io as cfgio
    plan = cfgio.plan_unit_update(updates)
    if plan is None:
        return "  (no change — values already match)"
    if dry_run:
        cmds = "\n".join("    " + " ".join(c) for c in plan.apply_cmds)
        return "DRY RUN — would write:\n  " + "\n  ".join(plan.changes) + \
               "\nThen run:\n" + cmds
    applied = cfgio.apply_unit_plan(plan)
    if applied:
        return P.green("✓ applied to systemd unit (daemon-reload + restart done)")
    cmd_block = "\n".join("    " + " ".join(c) for c in plan.apply_cmds)
    return (
        P.yellow("⚠ sudo not available non-interactively — apply manually:") +
        "\n" +
        "  New unit content is in /tmp/detm-daemon.service.pending\n" +
        cmd_block
    )
