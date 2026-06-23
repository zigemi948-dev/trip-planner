from __future__ import annotations

import json
from pathlib import Path

from app.core.config import resolve_backend_path, settings
from app.graph.state import IntentConstraints, JobStatus, PlanningJob, PlanningJobEvents, PlanningJobSummary
from app.graph.workflow import iter_trip_workflow


class JobStore:
    """Planning job store with in-memory access and JSONL persistence."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = resolve_backend_path(storage_path or settings.job_store_path)
        self._jobs: dict[str, PlanningJob] = {}
        self._load()

    def submit(self, intent: IntentConstraints) -> PlanningJob:
        job = PlanningJob(intent=intent, status=JobStatus.running)
        self._jobs[job.id] = job
        self._persist(job)
        seen_state_events = 0
        try:
            for stage, state in iter_trip_workflow(intent):
                event = {
                    "event": "stage_complete",
                    "payload": {
                        "stage": stage,
                        "status": state.graph_controls.current_status.value,
                    },
                }
                job.events.append(event)
                new_state_events = state.graph_controls.events[seen_state_events:]
                job.events.extend(new_state_events)
                seen_state_events = len(state.graph_controls.events)
                job.state = state
            job.status = JobStatus.complete
        except Exception as exc:
            job.status = JobStatus.failed
            job.error = str(exc)
        self._jobs[job.id] = job
        self._persist(job)
        return job

    def get(self, job_id: str) -> PlanningJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[PlanningJobSummary]:
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
        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            job = PlanningJob.model_validate(payload)
            self._jobs[job.id] = job

    def _persist(self, job: PlanningJob) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        snapshots = {**self._jobs, job.id: job}
        with self.storage_path.open("w", encoding="utf-8") as handle:
            for item in snapshots.values():
                handle.write(item.model_dump_json() + "\n")


job_store = JobStore()
