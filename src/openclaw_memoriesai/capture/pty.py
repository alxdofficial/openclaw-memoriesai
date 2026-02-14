"""PTY capture — read terminal output from OpenClaw exec sessions."""
import subprocess
import re
import logging
from .. import config

log = logging.getLogger(__name__)

# Common patterns for terminal condition matching
PATTERNS = {
    "prompt_returned": re.compile(r"[\$#>]\s*$", re.MULTILINE),
    "error": re.compile(r"(?i)(error|fail(ed)?|fatal|exception|traceback|ENOENT|EACCES|panic)", re.MULTILINE),
    "success": re.compile(r"(?i)(success|complete[d]?|done|finish|built in|compiled|passed)", re.MULTILINE),
    "download_done": re.compile(r"(?i)(100%|download(ed)? complete|saved to|written to)", re.MULTILINE),
    "build_done": re.compile(r"(?i)(build success|compiled successfully|built in \d|webpack \d|✓ compiled)", re.MULTILINE),
    "build_fail": re.compile(r"(?i)(build fail|compilation error|make.*error|FAIL\b)", re.MULTILINE),
    "test_pass": re.compile(r"(?i)(tests? passed|all tests|✓|\d+ passing)", re.MULTILINE),
    "test_fail": re.compile(r"(?i)(tests? failed|\d+ failing|FAIL\b)", re.MULTILINE),
    "npm_done": re.compile(r"(?i)(added \d+ packages|up to date|npm warn|✓)", re.MULTILINE),
    "git_done": re.compile(r"(?i)(Everything up-to-date|->|create mode|Fast-forward)", re.MULTILINE),
    "process_exit": re.compile(r"Process exited with code (\d+)"),
}


def read_pty_buffer(session_id: str, last_n_lines: int = 50) -> str | None:
    """Read the last N lines from an OpenClaw exec session via process log."""
    try:
        # Use openclaw's process tool equivalent — read session log
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
        # Get last N entries
        output_parts = []
        for line in lines[-last_n_lines*2:]:
            try:
                entry = json.loads(line)
                if entry.get("role") == "tool" and "content" in entry:
                    output_parts.append(str(entry["content"]))
            except (json.JSONDecodeError, KeyError):
                pass
        return "\n".join(output_parts[-last_n_lines:]) if output_parts else None

    return None


def try_text_match(terminal_output: str, criteria: str) -> str | None:
    """Try to match criteria against terminal text using heuristics.
    
    Returns a description string if matched, None if vision fallback needed.
    """
    criteria_lower = criteria.lower()
    output_lines = terminal_output.strip().split("\n")
    last_lines = "\n".join(output_lines[-20:])  # focus on recent output

    # Strategy 1: Check for explicit pattern keywords in criteria
    for pattern_name, regex in PATTERNS.items():
        # Map pattern names to likely criteria keywords
        keywords = pattern_name.replace("_", " ").split()
        if any(kw in criteria_lower for kw in keywords):
            match = regex.search(last_lines)
            if match:
                context = _get_match_context(output_lines, match.group())
                return f"Pattern '{pattern_name}' matched: {match.group()} | Context: {context}"

    # Strategy 2: Direct substring search for quoted terms in criteria
    # e.g., criteria = "output contains 'BUILD SUCCESSFUL'"
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", criteria)
    for term in quoted:
        if term.lower() in last_lines.lower():
            return f"Found '{term}' in terminal output"

    # Strategy 3: Check for process exit
    exit_match = PATTERNS["process_exit"].search(terminal_output)
    if exit_match:
        code = exit_match.group(1)
        if "complet" in criteria_lower or "finish" in criteria_lower or "done" in criteria_lower:
            return f"Process exited with code {code}"
        if "error" in criteria_lower or "fail" in criteria_lower:
            if code != "0":
                return f"Process exited with error code {code}"

    # Strategy 4: If criteria mentions "finish", "complete", "done" — check for shell prompt
    if any(word in criteria_lower for word in ["finish", "complete", "done", "end"]):
        if PATTERNS["prompt_returned"].search(last_lines):
            # Only if there's meaningful output before the prompt
            if len(output_lines) > 2:
                return f"Command appears complete (shell prompt returned)"

    return None  # Fall back to vision


def _get_match_context(lines: list[str], match_text: str, context_lines: int = 2) -> str:
    """Get a few lines around a match for context."""
    for i, line in enumerate(lines):
        if match_text in line:
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            return " | ".join(lines[start:end])
    return match_text
