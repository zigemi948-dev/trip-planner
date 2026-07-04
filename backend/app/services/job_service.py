from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import RLock
import logging

from datetime import datetime, timedelta, timezone

from app.core.config import resolve_backend_path, settings
from app.graph.state import IntentConstraints, JobStatus, PlanningJob, PlanningJobEvents, PlanningJobSummary
from app.graph.workflow import iter_trip_workflow

logger = logging.getLogger(__name__)


class JobStore:
    """Planning job store with in-memory access and JSONL persistence."""

    def __init__(self, storage_path: str | Path | None = None, max_workers: int = 2) -> None:
        self.storage_path = resolve_backend_path(storage_path or settings.job_store_path)
        self._jobs: dict[str, PlanningJob] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trip-job")
        self.persistence_error = ""
        self._load()

    def submit(self, intent: IntentConstraints) -> PlanningJob:
        job = PlanningJob(intent=intent, status=JobStatus.queued)
        with self._lock:
            self._jobs[job.id] = job
            self._persist(job)
            snapshot = job.model_copy(deep=True)
        self._executor.submit(self._run_job, job.id)
        return snapshot

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.running
            job.events.append({"event": "job_started", "payload": {"job_id": job.id}})
            self._persist(job)

        seen_state_events = 0
        try:
            for stage, state in iter_trip_workflow(job.intent):
                event = {
                    "event": "stage_complete",
                    "payload": {
                        "stage": stage,
                        "status": state.graph_controls.current_status.value,
                    },
                }
                with self._lock:
                    job.events.append(event)
                    new_state_events = state.graph_controls.events[seen_state_events:]
                    job.events.extend(new_state_events)
                    seen_state_events = len(state.graph_controls.events)
                    job.state = state
                    self._persist(job)
            with self._lock:
                job.status = JobStatus.complete
                job.events.append({"event": "job_complete", "payload": {"job_id": job.id}})
                self._persist(job)
        except Exception as exc:
            with self._lock:
                job.status = JobStatus.failed
                job.error = str(exc)
                job.events.append(
                    {
                        "event": "job_failed",
                        "payload": {"job_id": job.id, "reason": job.error},
                    }
                )
                self._persist(job)

    def get(self, job_id: str) -> PlanningJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job is not None else None

    def list(self) -> list[PlanningJobSummary]:
        with self._lock:
            return [
                PlanningJobSummary(
                    id=job.id,
                    status=job.status,
                    destination=job.intent.destination,
                    days=job.intent.days,
                    event_count=len(job.events),
                )
                for job in self._jobs.values()
            ]

    def events_since(self, job_id: str, offset: int = 0) -> PlanningJobEvents | None:
        """Return a stable event slice for polling clients."""
        job = self.get(job_id)
        if job is None:
            return None
        normalized_offset = max(0, min(offset, len(job.events)))
        return PlanningJobEvents(
            job_id=job.id,
            status=job.status,
            events=job.events[normalized_offset:],
            next_offset=len(job.events),
            state=job.state if job.status in {JobStatus.complete, JobStatus.failed} else None,
        )

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        now = datetime.now(timezone.utc)
        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            # Backward compat: pre-created_at records get epoch-0 timestamp
            # so cleanup can purge them on first run.
            if "created_at" not in payload:
                payload["created_at"] = datetime.min.replace(tzinfo=timezone.utc).isoformat()
            job = PlanningJob.model_validate(payload)
            self._jobs[job.id] = job

    def _persist(self, job: PlanningJob) -> None:
        """Persist job to JSONL file. Falls back to in-memory-only on write failure."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._jobs[job.id] = job
            snapshots = {**self._jobs, job.id: job}
            with self.storage_path.open("w", encoding="utf-8") as handle:
                for item in snapshots.values():
                    handle.write(item.model_dump_json() + "\n")
            self.persistence_error = ""
        except OSError as exc:
            self.persistence_error = str(exc)
            # Graceful degradation: jobs remain in memory only
            logger.warning(
                "Job store persistence failed (path=%s, error=%s). "
                "Jobs will remain in memory and will not survive restart.",
                self.storage_path, exc,
            )

    # ------------------------------------------------------------------
    # Data lifecycle management
    # ------------------------------------------------------------------
    def cleanup_old_jobs(
        self,
        max_age_hours: int | None = None,
        max_jobs: int | None = None,
    ) -> int:
        """Remove jobs exceeding age or count limits.

        Returns the number of removed jobs.
        """
        age_limit = max_age_hours or settings.job_cleanup_max_age_hours
        count_limit = max_jobs or settings.job_cleanup_max_jobs
        cutoff = datetime.now(timezone.utc) - timedelta(hours=age_limit)
        removed = 0
        with self._lock:
            # 1. Remove by age (regardless of status)
            job_ids_to_remove = set()
            for job_id, job in list(self._jobs.items()):
                if job.created_at.replace(tzinfo=timezone.utc) < cutoff:
                    job_ids_to_remove.add(job_id)

            # 2. If still over count limit, remove oldest
            remaining = sorted(
                [(jid, self._jobs[jid]) for jid in self._jobs if jid not in job_ids_to_remove],
                key=lambda item: item[1].created_at,
                reverse=False,
            )
            overflow = len(remaining) - count_limit
            if overflow > 0:
                for jid in remaining[:overflow]:
                    job_ids_to_remove.add(jid)

            for jid in job_ids_to_remove:
                del self._jobs[jid]
                removed += 1

            # Repersist if anything was removed
            if removed > 0:
                self._persist_all()

        logger.info("Job store cleanup: removed %d jobs (age>%dh or count>%d)", removed, age_limit, count_limit)
        return removed

    def _persist_all(self) -> None:
        """Overwrite the JSONL file with the current in-memory snapshot."""
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("w", encoding="utf-8") as handle:
                for job in self._jobs.values():
                    handle.write(job.model_dump_json() + "\n")
            self.persistence_error = ""
        except OSError as exc:
            self.persistence_error = str(exc)
            logger.warning("Job store full-persist failed (path=%s, error=%s)", self.storage_path, exc)


job_store = JobStore()
