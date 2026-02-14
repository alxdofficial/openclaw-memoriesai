"""Wait engine — async event loop managing all active wait jobs."""
import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field

from .. import config
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
    status: str = "watching"
    result_message: str | None = None
    # runtime state (not persisted)
    context: JobContext = field(default_factory=JobContext)
    poller: AdaptivePoller = field(default=None)
    diff_gate: PixelDiffGate = field(default_factory=PixelDiffGate)
    next_check_at: float = 0.0
    _resolved_window_id: int | None = None

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
                next_job.poller.on_no_change()
                next_job.next_check_at = now + next_job.poller.interval
                log.debug(f"Job {next_job.id}: no change, next in {next_job.poller.interval:.1f}s")
                continue

            # Add frame to context
            jpeg = frame_to_jpeg(frame)
            thumb = frame_to_thumbnail(frame)
            next_job.context.add_frame(jpeg, thumb, now)

            # PTY fast path
            if next_job.target_type == "pty":
                pty_match = self._try_pty_match(next_job)
                if pty_match:
                    await self._resolve_job(next_job, pty_match)
                    continue

            # Vision evaluation
            try:
                prompt, images = next_job.context.build_prompt(next_job.criteria)
                response = await evaluate_condition(prompt, images)
                verdict, desc = self._parse_verdict(response)
                next_job.context.add_verdict(verdict, desc, now)
                log.info(f"Job {next_job.id}: {verdict} — {desc}")

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

    def _try_pty_match(self, job: WaitJob) -> str | None:
        """Try regex matching on terminal output before vision."""
        from ..capture.pty import read_pty_buffer, try_text_match
        output = read_pty_buffer(job.target_id)
        if output:
            match = try_text_match(output, job.criteria)
            if match:
                log.info(f"Job {job.id}: PTY fast path matched: {match}")
                return match
        return None

    def _parse_verdict(self, response: str) -> tuple[str, str]:
        """Parse YES/NO/PARTIAL from model response."""
        line = response.strip().split("\n")[0]
        upper = line.upper()
        if upper.startswith("YES"):
            return ("resolved", line[4:].strip() if len(line) > 4 else "Condition met")
        elif upper.startswith("PARTIAL"):
            return ("partial", line[8:].strip() if len(line) > 8 else "Partial progress")
        else:
            desc = line[3:].strip() if upper.startswith("NO") and len(line) > 3 else line
            return ("watching", desc or "Condition not yet met")

    async def _resolve_job(self, job: WaitJob, description: str):
        """Condition met — wake OpenClaw."""
        job.status = "resolved"
        job.result_message = description
        del self.jobs[job.id]
        log.info(f"Job {job.id} RESOLVED: {description}")
        await self._inject_system_event(
            f"[smart_wait resolved] Job {job.id}: {job.criteria} → {description}"
        )

    async def _timeout_job(self, job: WaitJob):
        """Timeout — report last observation."""
        last_desc = ""
        if job.context.verdicts:
            last_desc = f" Last observation: {job.context.verdicts[-1].description}"
        job.status = "timeout"
        job.result_message = f"Timeout after {job.timeout}s.{last_desc}"
        del self.jobs[job.id]
        log.info(f"Job {job.id} TIMEOUT: {job.result_message}")
        await self._inject_system_event(
            f"[smart_wait timeout] Job {job.id}: {job.criteria} — {job.result_message}"
        )

    async def _inject_system_event(self, message: str):
        """Inject a system event into OpenClaw to wake the agent."""
        try:
            result = subprocess.run(
                ["openclaw", "system", "event", "--text", message, "--mode", "now"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log.info(f"System event injected: {message[:80]}...")
            else:
                log.error(f"System event failed: {result.stderr}")
        except Exception as e:
            log.error(f"System event injection error: {e}")
