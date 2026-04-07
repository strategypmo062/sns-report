from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    status: str = "running"  # running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def finish(self, result: dict) -> None:
        with self._lock:
            self.result = result
            self.status = "done"

    def fail(self, error: str) -> None:
        with self._lock:
            self.error = error
            self.status = "error"

    def cancel(self) -> None:
        with self._lock:
            self.status = "cancelled"

    @property
    def is_done(self) -> bool:
        return self.status in ("done", "error", "cancelled")


_jobs: dict[str, Job] = {}
_global_lock = threading.Lock()


def create_job() -> Job:
    jid = str(uuid.uuid4())
    job = Job(id=jid)
    with _global_lock:
        _jobs[jid] = job
    return job


def get_job(jid: str) -> Job | None:
    return _jobs.get(jid)
