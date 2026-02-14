"""PTY capture — read terminal output and match against agent-provided patterns."""
import subprocess
import re
import logging

from .. import debug

log = logging.getLogger(__name__)


def read_pty_buffer(session_id: str, last_n_lines: int = 50) -> str | None:
    """Read the last N lines from an OpenClaw exec session via process log."""
    try:
        result = subprocess.run(
            ["openclaw", "process", "log", session_id, "--limit", str(last_n_lines)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception) as e:
        log.debug(f"Failed to read PTY {session_id}: {e}")

    # Fallback: try reading the session transcript file
    import json
    from pathlib import Path
    transcript = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / f"{session_id}.jsonl"
    if transcript.exists():
        lines = transcript.read_text().strip().split("\n")
        output_parts = []
        for line in lines[-last_n_lines * 2:]:
            try:
                entry = json.loads(line)
                if entry.get("role") == "tool" and "content" in entry:
                    output_parts.append(str(entry["content"]))
            except (json.JSONDecodeError, KeyError):
                pass
        return "\n".join(output_parts[-last_n_lines:]) if output_parts else None

    return None


def try_regex_match(terminal_output: str, patterns: list[str]) -> str | None:
    """Try agent-provided regex patterns against terminal output.
    
    The agent knows what command it launched and provides patterns for
    expected success/failure signatures. Matches in <1ms.
    
    Args:
        terminal_output: Terminal text to search
        patterns: List of regex strings from the agent
    
    Returns:
        Description string if matched, None to fall through to vision.
    """
    if not patterns:
        return None

    last_lines = "\n".join(terminal_output.strip().split("\n")[-30:])

    for pattern_str in patterns:
        try:
            regex = re.compile(pattern_str, re.MULTILINE | re.IGNORECASE)
            match = regex.search(last_lines)
            if match:
                context = _get_match_context(terminal_output.strip().split("\n"), match.group())
                result = f"Regex matched '{pattern_str}': {match.group()}"
                if context != match.group():
                    result += f" | Context: {context}"
                debug.log_wait_event("pty", "REGEX HIT", result)
                return result
        except re.error as e:
            log.warning(f"Invalid regex pattern '{pattern_str}': {e}")
            continue

    return None


def _get_match_context(lines: list[str], match_text: str, context_lines: int = 2) -> str:
    """Get a few lines around a match for context."""
    for i, line in enumerate(lines):
        if match_text in line:
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            return " | ".join(lines[start:end])
    return match_text
