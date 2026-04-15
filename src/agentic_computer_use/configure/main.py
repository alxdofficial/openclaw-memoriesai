"""CLI dispatcher for `python -m agentic_computer_use.configure`."""
from __future__ import annotations

import argparse
import sys

from .state import load_effective
from . import prompt as P
from .sections import REGISTRY, ALL_SECTION_NAMES


def _print_intro() -> None:
    print(P.bold("DETM configure") + P.dim(" — interactive setup for every DETM subsystem"))
    print(P.dim("  Tip: `detm configure <section>` jumps to one section."))
    print(P.dim(f"       sections: {', '.join(ALL_SECTION_NAMES)}"))


def _run_section(name: str, args: argparse.Namespace) -> int:
    mod = REGISTRY.get(name)
    if mod is None:
        print(P.red(f"Unknown section: {name}"))
        print(f"Available: {', '.join(ALL_SECTION_NAMES)}")
        return 2

    eff = load_effective()

    if args.show:
        mod.show(eff)
        return 0

    if args.set:
        try:
            result = mod.apply_kv(eff, args.set, dry_run=args.dry_run)
        except ValueError as e:
            print(P.red(f"Error: {e}"))
            return 2
        if result:
            print(result)
        return 0

    # Interactive
    if not P.can_prompt():
        print(P.yellow("Not a TTY — use --show or --set KEY=VAL for non-interactive control."))
        mod.show(eff)
        return 1
    result = mod.interactive(eff, dry_run=args.dry_run)
    if result:
        print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="detm-configure",
        description="Interactive (and scriptable) configuration for DETM.",
    )
    p.add_argument("section", nargs="?", default=None,
                   help=f"One of: {', '.join(ALL_SECTION_NAMES)}. Omit to run full wizard.")
    p.add_argument("--show", action="store_true",
                   help="Only show current state; don't prompt or write.")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                   help="Non-interactive set (repeatable). Only valid with a section.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute changes and print them, but don't write.")
    args = p.parse_args(argv)

    if args.set and not args.section:
        print(P.red("--set requires a section (e.g. `detm configure keys --set OPENROUTER_API_KEY=sk-...`)"))
        return 2

    if args.section:
        return _run_section(args.section, args)

    # Full wizard: iterate every section
    _print_intro()
    if not P.can_prompt() and not args.show:
        print(P.yellow("Not a TTY — run with --show or pick a section."))
        args.show = True

    worst = 0
    for name in ALL_SECTION_NAMES:
        rc = _run_section(name, args)
        worst = max(worst, rc)
        if args.show:
            continue
        if not P.confirm(f"Continue to next section?", default=True):
            break
    return worst


if __name__ == "__main__":
    sys.exit(main())
