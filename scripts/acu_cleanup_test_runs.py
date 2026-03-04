#!/usr/bin/env python3
"""Delete all local test-run directories created by the ACU harness."""
from __future__ import annotations

import shutil

from acu_harness import TEST_RUNS_DIR


def main() -> None:
    if TEST_RUNS_DIR.exists():
        shutil.rmtree(TEST_RUNS_DIR, ignore_errors=True)
        print(f"Deleted {TEST_RUNS_DIR}")
    else:
        print(f"No test run directory at {TEST_RUNS_DIR}")


if __name__ == "__main__":
    main()
