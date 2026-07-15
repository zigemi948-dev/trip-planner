from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import RLock
import logging
from typing import Callable
from uuid import UUID

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import resolve_backend_path, settings
from app.core.database import get_session_factory
from app.graph.state import IntentConstraints, JobStatus, PlanningJob, PlanningJobEvents, PlanningJobSummary, TripState
from app.graph.workflow import iter_trip_workflow
from app.persistence.models import PlanningJob as PlanningJobRecord, PlanningJobEvent as PlanningJobEventRecord
from app.services.trip_service import trip_service

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
            saved_trip_id=job.saved_trip_id,
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


class DatabaseJobStore:
    """PostgreSQL-backed planning job store with the existing polling contract."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        max_workers: int = 2,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trip-job")
        self.persistence_error = ""

    def submit(self, intent: IntentConstraints) -> PlanningJob:
        job = PlanningJob(intent=intent, status=JobStatus.queued)
        try:
            with self._session_factory() as session:
                session.add(
                    PlanningJobRecord(
                        id=UUID(job.id),
                        status=job.status.value,
                        intent_json=intent.model_dump(mode="json"),
                    )
                )
                session.commit()
            self.persistence_error = ""
        except SQLAlchemyError as exc:
            self.persistence_error = str(exc)
            logger.exception("Database job creation failed")
            raise RuntimeError("Planning task could not be persisted") from exc
        self._executor.submit(self._run_job, job.id)
        return job

    def _run_job(self, job_id: str) -> None:
        self._update(job_id, status=JobStatus.running, event={"event": "job_started", "payload": {"job_id": job_id}})
        seen_state_events = 0
        try:
            for stage, state in iter_trip_workflow(self._load_intent(job_id)):
                events = [
                    {
                        "event": "stage_complete",
                        "payload": {"stage": stage, "status": state.graph_controls.current_status.value},
                    },
                    *state.graph_controls.events[seen_state_events:],
                ]
                seen_state_events = len(state.graph_controls.events)
                self._update(job_id, state=state, events=events)
            trip_id = trip_service.create_from_completed_job(state, UUID(job_id))
            self._update(
                job_id,
                status=JobStatus.complete,
                event={"event": "job_complete", "payload": {"job_id": job_id}},
                completed=True,
                trip_id=trip_id,
            )
        except Exception as exc:
            logger.exception("Planning job %s failed", job_id)
            self._update(
                job_id,
                status=JobStatus.failed,
                error=str(exc),
                event={"event": "job_failed", "payload": {"job_id": job_id, "reason": str(exc)}},
                completed=True,
            )

    def _load_intent(self, job_id: str) -> IntentConstraints:
        with self._session_factory() as session:
            record = session.get(PlanningJobRecord, UUID(job_id))
            if record is None:
                raise KeyError(f"job not found: {job_id}")
            return IntentConstraints.model_validate(record.intent_json)

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        state=None,
        error: str | None = None,
        event: dict | None = None,
        events: list[dict] | None = None,
        completed: bool = False,
        trip_id: UUID | None = None,
    ) -> None:
        try:
            with self._session_factory() as session:
                record = session.get(PlanningJobRecord, UUID(job_id))
                if record is None:
                    return
                if status is not None:
                    record.status = status.value
                if state is not None:
                    record.state_json = state.model_dump(mode="json")
                if error is not None:
                    record.error = error
                if trip_id is not None:
                    record.trip_id = trip_id
                for item in ([event] if event else []) + (events or []):
                    payload = item.get("payload", {})
                    session.add(
                        PlanningJobEventRecord(
                            job_id=record.id,
                            sequence=record.event_count,
                            event_type=item.get("event", "unknown"),
                            payload_json=payload,
                        )
                    )
                    record.event_count += 1
                if completed:
                    record.completed_at = datetime.now(timezone.utc)
                session.commit()
            self.persistence_error = ""
        except SQLAlchemyError as exc:
            self.persistence_error = str(exc)
            logger.exception("Database job update failed for %s", job_id)

    def get(self, job_id: str) -> PlanningJob | None:
        try:
            with self._session_factory() as session:
                record = session.get(PlanningJobRecord, UUID(job_id))
                return self._to_schema(record, include_events=True) if record else None
        except (SQLAlchemyError, ValueError) as exc:
            self.persistence_error = str(exc)
            return None

    def list(self) -> list[PlanningJobSummary]:
        try:
            with self._session_factory() as session:
                records = session.scalars(select(PlanningJobRecord).order_by(PlanningJobRecord.created_at.desc())).all()
                return [
                    PlanningJobSummary(
                        id=record.id.hex,
                        status=record.status,
                        destination=record.intent_json.get("destination", ""),
                        days=record.intent_json.get("days", 1),
                        event_count=record.event_count,
                    )
                    for record in records
                ]
        except SQLAlchemyError as exc:
            self.persistence_error = str(exc)
            return []

    def events_since(self, job_id: str, offset: int = 0) -> PlanningJobEvents | None:
        try:
            with self._session_factory() as session:
                record = session.get(PlanningJobRecord, UUID(job_id))
                if record is None:
                    return None
                start = max(0, min(offset, record.event_count))
                event_rows = session.scalars(
                    select(PlanningJobEventRecord)
                    .where(PlanningJobEventRecord.job_id == record.id, PlanningJobEventRecord.sequence >= start)
                    .order_by(PlanningJobEventRecord.sequence)
                ).all()
                return PlanningJobEvents(
                    job_id=record.id.hex,
                    status=record.status,
                    events=[{"event": row.event_type, "payload": row.payload_json} for row in event_rows],
                    next_offset=record.event_count,
                    state=TripState.model_validate(record.state_json) if record.status in {JobStatus.complete, JobStatus.failed} and record.state_json else None,
                    saved_trip_id=record.trip_id.hex if record.trip_id else None,
                )
        except (SQLAlchemyError, ValueError) as exc:
            self.persistence_error = str(exc)
            return None

    def cleanup_old_jobs(self, max_age_hours: int | None = None, max_jobs: int | None = None) -> int:
        age_limit = max_age_hours or settings.job_cleanup_max_age_hours
        count_limit = max_jobs or settings.job_cleanup_max_jobs
        cutoff = datetime.now(timezone.utc) - timedelta(hours=age_limit)
        try:
            with self._session_factory() as session:
                by_age = session.execute(delete(PlanningJobRecord).where(PlanningJobRecord.created_at < cutoff))
                remaining = session.scalars(select(PlanningJobRecord.id).order_by(PlanningJobRecord.created_at.desc()).offset(count_limit)).all()
                by_count = session.execute(delete(PlanningJobRecord).where(PlanningJobRecord.id.in_(remaining))) if remaining else None
                session.commit()
                return by_age.rowcount + (by_count.rowcount if by_count else 0)
        except SQLAlchemyError as exc:
            self.persistence_error = str(exc)
            logger.exception("Database job cleanup failed")
            return 0

    @staticmethod
    def _to_schema(record: PlanningJobRecord, *, include_events: bool) -> PlanningJob:
        events = []
        if include_events:
            events = [{"event": item.event_type, "payload": item.payload_json} for item in record.events]
        return PlanningJob(
            id=record.id.hex,
            status=record.status,
            intent=IntentConstraints.model_validate(record.intent_json),
            state=TripState.model_validate(record.state_json) if record.state_json else None,
            events=events,
            error=record.error,
            saved_trip_id=record.trip_id.hex if record.trip_id else None,
            created_at=record.created_at,
        )


job_store = DatabaseJobStore() if settings.persistence_backend == "database" else JobStore()
