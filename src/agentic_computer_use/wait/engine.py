"""Wait engine — async event loop managing all active wait jobs."""
import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

from .. import config, debug, db
from ..capture.screen import capture_window, capture_screen, find_window_by_name, frame_to_jpeg, frame_to_thumbnail
from ..capture.diff import PixelDiffGate
from ..screenshots import save_screenshot_from_jpeg
from ..vision import evaluate_condition
from ..task import manager as task_mgr
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
    display: str | None = None
    poll_interval: float = 2.0
    status: str = "watching"
    result_message: str | None = None
    # runtime state (not persisted)
    context: JobContext = field(default_factory=JobContext)
    poller: AdaptivePoller = field(default=None)
    diff_gate: PixelDiffGate = field(default_factory=PixelDiffGate)
    next_check_at: float = 0.0
    _resolved_window_id: int | None = None
    _last_vision_at: float = 0.0  # timestamp of last vision evaluation
    _partial_streak: int = 0       # consecutive PARTIAL verdicts counter

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

            # Vision evaluation
            try:
                next_job._last_vision_at = time.time()
                prompt, images = next_job.context.build_prompt(next_job.criteria)
                response = await evaluate_condition(prompt, images, job_id=next_job.id)
                verdict, desc = self._parse_verdict(response)
                next_job.context.add_verdict(verdict, desc, now)
                log.info(f"Job {next_job.id}: {verdict} — {desc}")
                debug.log_wait_event(next_job.id, f"VERDICT: {verdict.upper()}", desc)
                if next_job.task_id:
                    asyncio.ensure_future(task_mgr.log_wait_verdict(next_job.task_id, next_job.id, verdict, desc))

                if verdict == "resolved":
                    next_job._partial_streak = 0
                    await self._resolve_job(next_job, desc)
                elif verdict == "partial":
                    next_job._partial_streak += 1
                    streak = next_job._partial_streak
                    if streak >= config.PARTIAL_STREAK_RESOLVE:
                        # N consecutive PARTIALs → treat as resolved (model is being overly conservative)
                        promote_desc = f"[promoted from {streak}x PARTIAL] {desc}"
                        debug.log_wait_event(next_job.id, "PARTIAL→RESOLVED", f"streak={streak} >= {config.PARTIAL_STREAK_RESOLVE}: {desc}")
                        log.info(f"Job {next_job.id}: promoting {streak}x PARTIAL streak to RESOLVED")
                        await self._resolve_job(next_job, promote_desc)
                    else:
                        next_job.poller.on_partial()
                        debug.log_wait_event(next_job.id, f"PARTIAL streak={streak}/{config.PARTIAL_STREAK_RESOLVE}", desc)
                else:
                    next_job._partial_streak = 0
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
            return capture_screen(display=job.display)
        elif job.target_type == "window":
            # Resolve window name to ID on first capture
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
        """Parse structured verdict first, then fall back to legacy text format."""
        text = (response or "").strip()
        if not text:
            return ("watching", "Empty response")

        # Preferred contract: FINAL_JSON: {...}
        json_match = re.search(r"FINAL_JSON\s*:\s*(\{.*\})", text, flags=re.IGNORECASE | re.DOTALL)
        if json_match:
            raw = json_match.group(1).strip()
            try:
                payload = json.loads(raw)
                decision = str(payload.get("decision", "")).strip().lower()
                if decision in {"yes", "resolved"}:
                    result = "resolved"
                elif decision in {"partial", "in_progress", "progress"}:
                    result = "partial"
                else:
                    result = "watching"

                summary = str(payload.get("summary", "")).strip()
                confidence = payload.get("confidence")
                evidence = payload.get("evidence", [])
                if isinstance(evidence, str):
                    evidence = [evidence]
                if not isinstance(evidence, list):
                    evidence = []

                evidence_text = "; ".join(str(x).strip() for x in evidence if str(x).strip())
                detail_parts = []
                if summary:
                    detail_parts.append(summary)
                if evidence_text:
                    detail_parts.append(f"evidence: {evidence_text}")
                if confidence is not None:
                    detail_parts.append(f"confidence={confidence}")
                detail = " | ".join(detail_parts) or "Structured verdict parsed"

                # Confidence-based promotion: if model says "watching" but confidence
                # is high and evidence is present, upgrade to "partial" so the streak
                # counter can eventually promote it to resolved.
                try:
                    conf_val = float(confidence) if confidence is not None else 0.0
                except (TypeError, ValueError):
                    conf_val = 0.0
                if result == "watching" and conf_val >= config.RESOLVE_CONFIDENCE_THRESHOLD and evidence_text:
                    result = "partial"
                    detail = f"[conf-promoted watching→partial @ {conf_val}] {detail}"

                return (result, detail[:500])
            except Exception:
                # Fall through to legacy parser if JSON is malformed
                pass

        # Legacy fallback parser (YES/NO/PARTIAL first token in any line)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines:
            upper = line.upper()
            if upper.startswith("YES:") or upper.startswith("YES "):
                detail = line[4:].strip()
                return ("resolved", detail or "Condition met")
            if upper == "YES":
                return ("resolved", "Condition met")
            if upper.startswith("PARTIAL:") or upper.startswith("PARTIAL "):
                detail = line[8:].strip()
                return ("partial", detail or "Partial progress")
            if upper.startswith("NO:") or upper.startswith("NO "):
                detail = line[3:].strip()
                return ("watching", detail or "Condition not yet met")
            if upper == "NO":
                return ("watching", "Condition not yet met")

        return ("watching", text[:500])

    async def _resolve_job(self, job: WaitJob, description: str):
        """Condition met — wake OpenClaw and update linked task."""
        job.status = "resolved"
        job.result_message = description
        del self.jobs[job.id]

        await self._persist_wait_terminal(job, "resolved", description)

        log.info(f"Job {job.id} RESOLVED: {description}")
        debug.log_wait_event(job.id, "✅ RESOLVED", description)

        # Auto-post to linked task
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
                log.info(f"Posted wait resolution to task {job.task_id}")
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
        """Save the last captured frame from the job context to disk."""
        if not job.context.frames:
            return None
        last = job.context.frames[-1]
        refs = save_screenshot_from_jpeg(job.id, role, last.jpeg, last.thumbnail)
        return refs or None

    async def _inject_system_event(self, message: str):
        """Inject a system event into OpenClaw to wake the agent."""
        try:
            from .. import config as _cfg
            result = subprocess.run(
                [_cfg.OPENCLAW_CLI, "system", "event", "--text", message, "--mode", "now"],
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
