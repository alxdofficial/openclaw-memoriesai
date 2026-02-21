"""Wait engine — async event loop managing all active wait jobs."""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from .. import config, debug, db
from ..capture.screen import capture_window, capture_screen, find_window_by_name, frame_to_jpeg
from ..vision import evaluate_condition
from ..task import manager as task_mgr
from .context import JobContext, build_prompt

log = logging.getLogger(__name__)

# Fixed poll interval — no adaptive logic.
POLL_INTERVAL = 1.0

# Per-display asyncio locks — serialize Xlib calls per display, not globally.
# Jobs on different Xvfb displays run captures in parallel.
_CAPTURE_LOCKS: dict[str, asyncio.Lock] = {}


def _get_capture_lock(display: str) -> asyncio.Lock:
    if display not in _CAPTURE_LOCKS:
        _CAPTURE_LOCKS[display] = asyncio.Lock()
    return _CAPTURE_LOCKS[display]


@dataclass
class WaitJob:
    id: str
    target_type: str      # "window" | "pty" | "screen"
    target_id: str        # window name/id, pty session, or "full"
    criteria: str
    timeout: int
    task_id: str | None = None
    display: str | None = None
    poll_interval: float = POLL_INTERVAL  # kept for API compat; engine uses POLL_INTERVAL constant
    status: str = "watching"
    result_message: str | None = None
    context: JobContext = field(default_factory=JobContext)
    next_check_at: float = 0.0
    _resolved_window_id: int | None = None
    _last_jpeg: bytes | None = None  # most recent frame, saved on resolve/timeout


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
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())
        else:
            log.debug("Loop already running, new job will be picked up")

    def get_job(self, job_id: str) -> WaitJob | None:
        return self.jobs.get(job_id)

    def cancel_job(self, job_id: str, reason: str = "cancelled") -> WaitJob | None:
        if job_id in self.jobs:
            job = self.jobs.pop(job_id)
            job.status = "cancelled"
            job.result_message = reason
            log.info(f"Cancelled wait job {job_id}: {reason}")
            return job
        return None

    async def _persist_wait_terminal(self, job: WaitJob, state: str, result_message: str):
        """Persist final state to wait_jobs table."""
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE wait_jobs SET status = ?, result_message = ?, resolved_at = ? WHERE id = ?",
                (state, result_message, db.now_iso(), job.id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _run_loop(self):
        if hasattr(self, '_loop_running') and self._loop_running:
            log.debug("Loop already running, skipping duplicate start")
            return
        self._loop_running = True
        log.info("Wait engine loop started")

        while self.jobs:
            now = time.time()

            overdue = [j for j in list(self.jobs.values()) if j.next_check_at <= now]

            if not overdue:
                next_job = min(self.jobs.values(), key=lambda j: j.next_check_at)
                wait_time = next_job.next_check_at - now
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=wait_time)
                except asyncio.TimeoutError:
                    pass
                continue

            # Evaluate all overdue jobs concurrently.
            # Vision calls run in parallel; frame captures are serialized per display.
            await asyncio.gather(*[self._evaluate_job(j) for j in overdue])

        self._loop_running = False
        log.info("Wait engine loop ended (no active jobs)")

    async def _evaluate_job(self, job: WaitJob):
        """Evaluate a single wait job: capture → encode → vision → YES/NO verdict."""
        # Guard: job may have been cancelled between overdue-list snapshot and now
        if job.id not in self.jobs:
            return

        now = time.time()
        loop = asyncio.get_event_loop()

        # Check timeout
        elapsed = now - job.context.started_at
        if elapsed > job.timeout:
            await self._timeout_job(job)
            return

        # Capture frame — serialize per display (Xlib not thread-safe per connection)
        display_key = job.display or config.DISPLAY
        async with _get_capture_lock(display_key):
            frame = await loop.run_in_executor(None, self._capture, job)

        if frame is None:
            log.warning(f"Job {job.id}: frame capture failed")
            job.next_check_at = now + POLL_INTERVAL
            return

        # Encode to JPEG off the main thread
        jpeg = await loop.run_in_executor(None, frame_to_jpeg, frame)
        job._last_jpeg = jpeg

        # Vision evaluation — pure async I/O, runs concurrently with other jobs
        try:
            prompt = build_prompt(job.criteria, elapsed)
            response = await evaluate_condition(prompt, [jpeg], job_id=job.id)
            verdict, desc = self._parse_verdict(response)
            log.info(f"Job {job.id}: {verdict} — {desc}")
            debug.log_wait_event(job.id, f"VERDICT: {verdict.upper()}", desc)

            if job.task_id:
                asyncio.ensure_future(task_mgr.log_wait_verdict(job.task_id, job.id, verdict, desc))

            if verdict == "resolved":
                await self._resolve_job(job, desc)
            else:
                job.next_check_at = time.time() + POLL_INTERVAL

        except Exception as e:
            log.error(f"Job {job.id}: evaluation error: {e}")
            job.next_check_at = time.time() + POLL_INTERVAL

    def _capture(self, job: WaitJob):
        """Capture frame based on target type. Called via run_in_executor."""
        if job.target_type == "screen":
            return capture_screen(display=job.display)
        elif job.target_type == "window":
            if job._resolved_window_id is None:
                try:
                    wid = int(job.target_id)
                    job._resolved_window_id = wid
                except ValueError:
                    wid = find_window_by_name(job.target_id, display=job.display)
                    if wid:
                        job._resolved_window_id = wid
                        log.info(f"Job {job.id}: resolved window '{job.target_id}' → {wid}")
                    else:
                        log.warning(f"Job {job.id}: window '{job.target_id}' not found")
                        return None
            return capture_window(job._resolved_window_id, display=job.display)
        elif job.target_type == "pty":
            return capture_screen(display=job.display)
        return None

    def _parse_verdict(self, response: str) -> tuple[str, str]:
        """Parse YES/NO verdict from model response."""
        text = (response or "").strip()
        if not text:
            return ("watching", "Empty response")
        if text.upper().startswith("YES"):
            detail = text[3:].lstrip(": ").strip()
            return ("resolved", detail or "Condition met")
        # Everything else is NO / watching
        detail = text.lstrip("NO").lstrip(": ").strip()
        return ("watching", detail[:200] or "Condition not yet met")

    async def _resolve_job(self, job: WaitJob, description: str):
        """Condition met — wake OpenClaw and update linked task."""
        if job.id not in self.jobs:
            return
        job.status = "resolved"
        job.result_message = description
        del self.jobs[job.id]

        await self._persist_wait_terminal(job, "resolved", description)
        log.info(f"Job {job.id} RESOLVED: {description}")
        debug.log_wait_event(job.id, "✅ RESOLVED", description)

        if job.task_id:
            try:
                screenshot_refs = self._save_last_frame(job, "after")
                elapsed = time.time() - job.context.started_at
                from ..task import manager
                await manager.on_wait_finished(
                    task_id=job.task_id,
                    wait_id=job.id,
                    state="resolved",
                    detail=description,
                    screenshot_refs=screenshot_refs,
                    elapsed_seconds=elapsed,
                )
            except Exception as e:
                log.warning(f"Failed to update task {job.task_id}: {e}")

        await self._inject_system_event(
            f"[smart_wait resolved] Job {job.id}: {job.criteria} → {description}"
        )

    async def _timeout_job(self, job: WaitJob):
        """Timeout — wake OpenClaw with last observation."""
        if job.id not in self.jobs:
            return
        job.status = "timeout"
        job.result_message = f"Timeout after {job.timeout}s."
        del self.jobs[job.id]

        await self._persist_wait_terminal(job, "timeout", job.result_message)
        log.info(f"Job {job.id} TIMEOUT: {job.result_message}")
        debug.log_wait_event(job.id, "⏰ TIMEOUT", job.result_message)

        if job.task_id:
            try:
                screenshot_refs = self._save_last_frame(job, "after")
                elapsed = time.time() - job.context.started_at
                from ..task import manager
                await manager.on_wait_finished(
                    task_id=job.task_id,
                    wait_id=job.id,
                    state="timeout",
                    detail=job.result_message,
                    screenshot_refs=screenshot_refs,
                    elapsed_seconds=elapsed,
                )
            except Exception as e:
                log.warning(f"Failed to update task {job.task_id}: {e}")

        await self._inject_system_event(
            f"[smart_wait timeout] Job {job.id}: {job.criteria} — {job.result_message}"
        )

    def _save_last_frame(self, job: WaitJob, role: str) -> dict | None:
        """Save the last captured JPEG to disk for task logging."""
        if not job._last_jpeg:
            return None
        from ..screenshots import save_screenshot_from_jpeg
        refs = save_screenshot_from_jpeg(job.id, role, job._last_jpeg, None)
        return refs or None

    async def _inject_system_event(self, message: str):
        """Inject a system event into OpenClaw to wake the agent (async subprocess)."""
        try:
            from .. import config as _cfg
            proc = await asyncio.create_subprocess_exec(
                _cfg.OPENCLAW_CLI, "system", "event", "--text", message, "--mode", "now",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                log.error("System event injection timed out")
                debug.log_openclaw_event("system_event", "TIMEOUT", success=False)
                return
            if proc.returncode == 0:
                log.info(f"System event injected: {message[:80]}...")
                debug.log_openclaw_event("system_event", message, success=True)
            else:
                err = stderr.decode(errors="replace").strip()
                log.error(f"System event failed: {err}")
                debug.log_openclaw_event("system_event", f"FAILED: {err}", success=False)
        except Exception as e:
            log.error(f"System event injection error: {e}")
