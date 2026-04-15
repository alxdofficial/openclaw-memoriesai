"""Global pytest fixtures for DETM.

Humanization is default-ON in production, but tests assert deterministic
behavior — so we flip it OFF for every test unless the test opts back in
via `humanize_on` fixture.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def humanize_off(monkeypatch):
    """Disable GUI humanization for tests — must be autouse so existing tests
    that assert exact xdotool calls / timings stay deterministic."""
    monkeypatch.setenv("ACU_HUMANIZE", "0")
    from src.agentic_computer_use import humanize
    humanize._reset_for_tests()
    yield
    # After test: leave state as the module default (env says off, so off).
    humanize._reset_for_tests()


@pytest.fixture
def humanize_on(monkeypatch):
    """Opt-in fixture: turn humanization on for a single test."""
    monkeypatch.setenv("ACU_HUMANIZE", "1")
    from src.agentic_computer_use import humanize
    humanize._reset_for_tests()
    yield humanize
    humanize._reset_for_tests()
