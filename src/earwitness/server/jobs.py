"""Background jobs for the UI: one analysis at a time, with progress lines kept for polling.

The pipelines report progress through a plain callable that takes a string (the CLI passes
`print`). A job hands them a callable that appends to its own line buffer, and the UI polls for
new lines by index, so the browser never holds a connection open across a 20 minute search.
Jobs run on one worker thread in submission order: an analysis already uses the whole machine,
and a second one at the same time would only slow both.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

Progress = Callable[[str], None]
JobFn = Callable[[Progress], dict[str, object]]


@dataclass
class Job:
    """One unit of background work and everything the UI asks about it."""

    id: str
    kind: str
    label: str
    status: str = "queued"
    """queued, running, done or failed."""
    lines: list[str] = field(default_factory=list)
    result: dict[str, object] | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str) -> None:
        """Append a progress line; this is the `progress` callable the pipelines receive."""
        with self._lock:
            self.lines.append(str(message))

    def snapshot(self, since: int = 0) -> dict[str, object]:
        """JSON-ready state with the lines from index `since` on and the next index to ask for."""
        with self._lock:
            lines = self.lines[since:]
            n = len(self.lines)
            status, result, error = self.status, self.result, self.error
            started, finished = self.started, self.finished
        end = finished if finished is not None else time.time()
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": status,
            "lines": lines,
            "next": n,
            "result": result,
            "error": error,
            "elapsed_s": None if started is None else end - started,
        }


class JobRunner:
    """Runs jobs one at a time on a daemon thread, in submission order."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._pending: list[tuple[Job, JobFn]] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.current: Job | None = None

    def submit(self, kind: str, label: str, fn: JobFn) -> Job:
        """Queue `fn`; it receives a progress callable and returns the job's result dict."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._pending.append((job, fn))
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run, name="earwitness-jobs", daemon=True
                )
                self._worker.start()
        return job

    def get(self, job_id: str) -> Job | None:
        """The job with this id, or None."""
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list[Job]:
        """All jobs, oldest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created)

    @property
    def busy(self) -> bool:
        """True while a job runs or waits."""
        with self._lock:
            return self.current is not None or bool(self._pending)

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """Block until the job finishes (or the timeout passes) and return it."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.done_event.wait(timeout)
        return job

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._worker = None
                    return
                job, fn = self._pending.pop(0)
                self.current = job
                job.status = "running"
                job.started = time.time()
            try:
                result = fn(job.log)
            except Exception as exc:  # the job's failure is reported to the UI, not raised
                job.log(traceback.format_exc())
                with self._lock:
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.status = "failed"
            else:
                with self._lock:
                    job.result = result
                    job.status = "done"
            finally:
                with self._lock:
                    job.finished = time.time()
                    self.current = None
                job.done_event.set()
