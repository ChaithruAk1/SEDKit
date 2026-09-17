"""Background jobs of the local API (report builds): one worker thread, results kept in memory for the server's life.

A POST starts a job and returns its id at once; the page polls GET /api/jobs/{id}. Jobs run the same Python functions
as the CLI (their own connections and write transactions), so a busy database fails the job with kind `busy` and the
user starts it again. Nothing about jobs is stored in the database; restarting `sed serve` forgets them.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sed import db
from sed.errors import SedError

MAX_JOBS = 50


@dataclass
class Job:
    job_id: str
    kind: str
    params: dict[str, Any]
    status: str = "queued"  # queued | running | done | failed
    created_at: str = field(default_factory=db.utc_now)
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(self) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sed-job")

    def submit(self, kind: str, params: dict[str, Any], work: Callable[[], dict[str, Any]]) -> Job:
        job = Job(job_id=f"job-{secrets.token_hex(6)}", kind=kind, params=params)
        with self._lock:
            self._jobs[job.job_id] = job
            while len(self._jobs) > MAX_JOBS:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest].status in ("queued", "running"):
                    break
                self._jobs.pop(oldest)
        self._pool.submit(self._run, job, work)
        return job

    def _run(self, job: Job, work: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job.status = "running"
        try:
            result = work()
        except SedError as exc:
            with self._lock:
                job.status, job.error = "failed", {"kind": exc.kind, "message": str(exc), "details": exc.details}
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = {"kind": "internal", "message": f"Internal error ({type(exc).__name__})", "details": None}
        else:
            with self._lock:
                job.status, job.result = "done", result
        finally:
            with self._lock:
                job.finished_at = db.utc_now()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout: float = 60.0) -> Job | None:
        """Block until the job has finished (tests)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if job is None or job.status in ("done", "failed"):
                return job
            time.sleep(0.05)
        return self.get(job_id)
