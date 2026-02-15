"""Wait engine — async event loop managing all active wait jobs."""
import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field

from .. import config, debug
from ..capture.screen import capture_window, capture_screen, find_window_by_name, frame_to_jpeg, frame_to_thumbnail
from ..capture.diff import PixelDiffGate
from ..vision import evaluate_condition
from .context import JobContext
from .poller import AdaptivePoller

log = logging.getLogger(__name__)


@dataclass
class WaitJob:
    id: str
    target_type: str      # "window" | "pty" | "screen"
    target_id: str        # window name/id, pty session, or "full"
    criteria: str
    timeout: int
    task_id: str | None = None
    poll_interval: float = 2.0
    match_patterns: list[str] = field(default_factory=list)    # agent-provided regex patterns for text fast path
    status: str = "watching"
    result_message: str | None = None
    # runtime state (not persisted)
    context: JobContext = field(default_factory=JobContext)
    poller: AdaptivePoller = field(default=None)
    diff_gate: PixelDiffGate = field(default_factory=PixelDiffGate)
    next_check_at: float = 0.0
    _resolved_window_id: int | None = None
    _last_vision_at: float = 0.0  # timestamp of last vision evaluation

    def __post_init__(self):
        if self.poller is None:
            self.poller = AdaptivePoller(self.poll_interval)


class WaitEngine:
    """Manages all active wait jobs in a single async loop."""

    def __init__(self):
        self.jobs: dict[str, WaitJob] = {}
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()

    def add_job(self, job: WaitJob):
        self.jobs[job.id] = job
        log.info(f"Added wait job {job.id}: target={job.target_type}:{job.target_id}, criteria={job.criteria!r}")
        debug.log_wait_event(job.id, "CREATED", f"target={job.target_type}:{job.target_id}, criteria={job.criteria!r}, timeout={job.timeout}s")
        self._wake_event.set()
        # Start the loop if not already running
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())
        else:
            log.debug("Loop already running, new job will be picked up")

    def cancel_job(self, job_id: str, reason: str = "cancelled") -> bool:
        if job_id in self.jobs:
            job = self.jobs.pop(job_id)
            job.status = "cancelled"
            job.result_message = reason
            log.info(f"Cancelled wait job {job_id}: {reason}")
            return True
        return False

    async def _run_loop(self):
        if hasattr(self, '_loop_running') and self._loop_running:
            log.debug("Loop already running, skipping duplicate start")
            return
        self._loop_running = True
        log.info("Wait engine loop started")
        while self.jobs:
            now = time.time()

            # Find the most overdue job
            next_job = min(self.jobs.values(), key=lambda j: j.next_check_at)

            if next_job.next_check_at > now:
                wait_time = next_job.next_check_at - now
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=wait_time)
                except asyncio.TimeoutError:
                    pass
                continue

            # Check timeout
            elapsed = now - next_job.context.started_at
            if elapsed > next_job.timeout:
                await self._timeout_job(next_job)
                continue

            # Capture frame
            frame = self._capture(next_job)
            if frame is None:
                log.warning(f"Job {next_job.id}: frame capture failed")
                next_job.next_check_at = now + next_job.poller.interval
                continue

            # Pixel-diff gate
            if not next_job.diff_gate.should_evaluate(frame):
                # Force re-eval if static for too long (catches subtle changes diff misses)
                since_last_vision = now - next_job._last_vision_at if next_job._last_vision_at else now - next_job.context.started_at
                if since_last_vision >= config.MAX_STATIC_SECONDS:
                    debug.log_wait_event(next_job.id, "FORCE RE-EVAL", f"static for {since_last_vision:.0f}s (>{config.MAX_STATIC_SECONDS}s), forcing vision check")
                    # Fall through to vision evaluation below
                else:
                    next_job.poller.on_no_change()
                    next_job.next_check_at = now + next_job.poller.interval
                    debug.log_diff_gate(next_job.id, False, next_job.diff_gate.last_diff_pct)
                    log.debug(f"Job {next_job.id}: no change, next in {next_job.poller.interval:.1f}s")
                    continue
            else:
                debug.log_diff_gate(next_job.id, True, next_job.diff_gate.last_diff_pct)

            # Add frame to context
            jpeg = frame_to_jpeg(frame)
            thumb = frame_to_thumbnail(frame)
            next_job.context.add_frame(jpeg, thumb, now)

            # Text fast path — agent-provided regex patterns
            if next_job.match_patterns:
                text_match = await self._try_text_match(next_job)
                if text_match:
                    await self._resolve_job(next_job, text_match)
                    continue

            # Vision evaluation
            try:
                next_job._last_vision_at = time.time()
                prompt, images = next_job.context.build_prompt(next_job.criteria)
                response = await evaluate_condition(prompt, images, job_id=next_job.id)
                verdict, desc = self._parse_verdict(response)
                next_job.context.add_verdict(verdict, desc, now)
                log.info(f"Job {next_job.id}: {verdict} — {desc}")
                debug.log_wait_event(next_job.id, f"VERDICT: {verdict.upper()}", desc)

                if verdict == "resolved":
                    await self._resolve_job(next_job, desc)
                elif verdict == "partial":
                    next_job.poller.on_partial()
                else:
                    next_job.poller.on_change_no_match()
            except Exception as e:
                log.error(f"Job {next_job.id}: evaluation error: {e}")
                next_job.poller.on_change_no_match()

            next_job.next_check_at = time.time() + next_job.poller.interval

        self._loop_running = False
        log.info("Wait engine loop ended (no active jobs)")

    def _capture(self, job: WaitJob):
        """Capture frame based on target type."""
        if job.target_type == "screen":
            return capture_screen()
        elif job.target_type == "window":
            # Resolve window name to ID on first capture
            if job._resolved_window_id is None:
                try:
                    wid = int(job.target_id)
                    job._resolved_window_id = wid
                except ValueError:
                    wid = find_window_by_name(job.target_id)
                    if wid:
                        job._resolved_window_id = wid
                        log.info(f"Job {job.id}: resolved window '{job.target_id}' → {wid}")
                    else:
                        log.warning(f"Job {job.id}: window '{job.target_id}' not found")
                        return None
            return capture_window(job._resolved_window_id)
        elif job.target_type == "pty":
            # For PTY, capture full screen as fallback visual
            return capture_screen()
        return None

    async def _try_text_match(self, job: WaitJob) -> str | None:
        """Try agent-provided regex patterns against terminal text before vision.
        
        The agent knows what command it launched — it provides regex patterns
        for expected success/failure signatures. Matches in <1ms.
        Falls through to full MiniCPM vision if no patterns match.
        """
        from ..capture.pty import read_pty_buffer, try_regex_match

        # Get terminal text (for PTY targets, read buffer)
        if job.target_type == "pty":
            output = read_pty_buffer(job.target_id)
        else:
            # For screen/window targets, can't easily get text — fall through to vision
            return None

        if not output:
            return None

        regex_result = try_regex_match(output, job.match_patterns)
        if regex_result:
            log.info(f"Job {job.id}: regex fast path: {regex_result}")
            return regex_result

        return None

    def _parse_verdict(self, response: str) -> tuple[str, str]:
        """Parse YES/NO/PARTIAL from model response.
        
        Scans all lines for verdict patterns. Takes the FIRST match
        (ignores echoed template lines like '<evidence...>').
        """
        lines = [l.strip() for l in response.strip().split("\n") if l.strip()]
        if not lines:
            return ("watching", "Empty response")

        for line in lines:
            upper = line.upper()
            # Skip lines that look like template echoes
            if "<" in line and ">" in line and ("evidence" in line.lower() or "what you see" in line.lower()):
                continue
            
            if upper.startswith("YES:") or upper.startswith("YES "):
                detail = line[4:].strip()
                return ("resolved", detail or "Condition met")
            elif upper == "YES":
                return ("resolved", "Condition met")
            elif upper.startswith("PARTIAL:") or upper.startswith("PARTIAL "):
                detail = line[8:].strip()
                return ("partial", detail or "Partial progress")
            elif upper.startswith("NO:") or upper.startswith("NO "):
                detail = line[3:].strip()
                return ("watching", detail or "Condition not yet met")
            elif upper == "NO":
                return ("watching", "Condition not yet met")

        # No clear verdict — default to watching with full response
        return ("watching", response.strip()[:500])

    async def _resolve_job(self, job: WaitJob, description: str):
        """Condition met — wake OpenClaw and update linked task."""
        job.status = "resolved"
        job.result_message = description
        del self.jobs[job.id]
        log.info(f"Job {job.id} RESOLVED: {description}")
        debug.log_wait_event(job.id, "✅ RESOLVED", description)

        # Auto-post to linked task
        if job.task_id:
            try:
                from ..task import manager
                await manager.update_task(
                    job.task_id,
                    message=f"[smart_wait] Wait completed: {description}"
                )
                log.info(f"Auto-posted resolution to task {job.task_id}")
            except Exception as e:
                log.warning(f"Failed to update task {job.task_id}: {e}")

        await self._inject_system_event(
            f"[smart_wait resolved] Job {job.id}: {job.criteria} → {description}"
        )

    async def _timeout_job(self, job: WaitJob):
        """Timeout — report last observation and update linked task."""
        last_desc = ""
        if job.context.verdicts:
            last_desc = f" Last observation: {job.context.verdicts[-1].description}"
        job.status = "timeout"
        job.result_message = f"Timeout after {job.timeout}s.{last_desc}"
        del self.jobs[job.id]
        log.info(f"Job {job.id} TIMEOUT: {job.result_message}")
        debug.log_wait_event(job.id, "⏰ TIMEOUT", job.result_message)

        if job.task_id:
            try:
                from ..task import manager
                await manager.update_task(
                    job.task_id,
                    message=f"[smart_wait] Wait timed out: {job.result_message}"
                )
            except Exception as e:
                log.warning(f"Failed to update task {job.task_id}: {e}")

        await self._inject_system_event(
            f"[smart_wait timeout] Job {job.id}: {job.criteria} — {job.result_message}"
        )

    async def _inject_system_event(self, message: str):
        """Inject a system event into OpenClaw to wake the agent."""
        try:
            result = subprocess.run(
                ["/home/alex/.npm-global/bin/openclaw", "system", "event", "--text", message, "--mode", "now"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log.info(f"System event injected: {message[:80]}...")
                debug.log_openclaw_event("system_event", message, success=True)
            else:
                log.error(f"System event failed: {result.stderr}")
                debug.log_openclaw_event("system_event", f"FAILED: {result.stderr}", success=False)
        except Exception as e:
            log.error(f"System event injection error: {e}")
