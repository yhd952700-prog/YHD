"""In-process asynchronous ingest jobs (progress + cancellation).

The knowledge store is a process-level SQLite singleton, so jobs are intentionally
in-process: no broker dependency, no new requirement. A job id is returned
immediately; progress is observable chunk by chunk and cancellation is honoured
at the **next chunk boundary**.

Partial-ingest policy: **all-or-nothing (全回滚)**. Vectors are computed before
anything is written, and the store swap is one transaction, so a cancelled job
leaves zero chunks/vectors/FTS rows/hash rows behind. There is never a
half-written document to clean up.

Limitation (stated, not hidden): jobs live in memory. A process restart loses
job records (the ingested data itself is already durable, or absent). Jobs that
already finished are kept for inspection but are never resumed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .documents import (
    IngestCancelled,
    KnowledgeDocumentService,
    _safe_document_id,
    get_knowledge_service,
)

logger = logging.getLogger("liuhao.knowledge.ingest_jobs")

_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_MAX_RETAINED_JOBS = 200
_PARTIAL_POLICY = "all-or-nothing（取消后不写入任何分块，不会留下半成品）"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Job:
    job_id: str
    document_id: str
    status: str = "queued"
    phase: str = "queued"
    total_chunks: int = 0
    processed_chunks: int = 0
    cancel_requested: bool = False
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    note: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        if self.total_chunks > 0:
            percent = round(self.processed_chunks * 100.0 / self.total_chunks, 1)
        else:
            percent = 100.0 if self.status == "succeeded" else 0.0
        return {
            "job_id": self.job_id,
            "document_id": self.document_id,
            "status": self.status,
            "phase": self.phase,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "progress_percent": percent,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "note": self.note,
            "partial_commit_policy": _PARTIAL_POLICY,
        }


class IngestJobManager:
    """Submit / inspect / cancel asynchronous ingest jobs."""

    def __init__(
        self,
        service_factory: Optional[Callable[[], KnowledgeDocumentService]] = None,
        max_workers: int = 2,
    ) -> None:
        self._service_factory = service_factory or get_knowledge_service
        self._lock = threading.RLock()
        self._jobs: Dict[str, _Job] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._done_events: Dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="kb-ingest"
        )

    # -- lifecycle ---------------------------------------------------------
    def submit(
        self,
        text: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job = _Job(
            job_id=uuid.uuid4().hex,
            document_id=_safe_document_id(doc_id),
        )
        cancel_event = threading.Event()
        done_event = threading.Event()
        with self._lock:
            self._prune_locked()
            self._jobs[job.job_id] = job
            self._cancel_events[job.job_id] = cancel_event
            self._done_events[job.job_id] = done_event
        self._executor.submit(
            self._run, job.job_id, text, dict(metadata or {}), cancel_event, done_event
        )
        return job.snapshot()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job is not None else None

    def cancel(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Request cancellation; it lands at the next chunk boundary."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in _TERMINAL:
                return {
                    **job.snapshot(),
                    "cancel_accepted": False,
                    "reason": "任务已结束，取消请求未生效",
                }
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
            job.cancel_requested = True
            return {
                **job.snapshot(),
                "cancel_accepted": True,
                "reason": "取消将在下一个分块边界生效；已入库内容按全回滚处理",
            }

    def wait(self, job_id: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """Block until the job leaves the running state (tests / callers)."""
        with self._lock:
            done = self._done_events.get(job_id)
        if done is not None:
            done.wait(timeout)
        return self.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # -- worker ------------------------------------------------------------
    def _run(
        self,
        job_id: str,
        text: str,
        metadata: Dict[str, Any],
        cancel_event: threading.Event,
        done_event: threading.Event,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.phase = "embedding"
            job.started_at = _now()

        def on_progress(processed: int, total: int) -> None:
            with self._lock:
                job.total_chunks = total
                job.processed_chunks = processed
                job.phase = "embedding" if processed < total else "committing"

        try:
            service = self._service_factory()
            outcome = service.ingest(
                text,
                doc_id=job.document_id,
                metadata=metadata,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        except IngestCancelled as exc:
            with self._lock:
                job.status = "cancelled"
                job.phase = "cancelled"
                job.total_chunks = exc.total
                job.processed_chunks = exc.processed
                job.finished_at = _now()
                job.result = {
                    "committed_chunks": 0,
                    "partial_commit_policy": _PARTIAL_POLICY,
                }
                job.note = "已取消；未写入任何分块（全回滚）"
            logger.info(
                "knowledge ingest job %s cancelled at chunk boundary %d/%d",
                job_id,
                exc.processed,
                exc.total,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            with self._lock:
                job.status = "failed"
                job.phase = "failed"
                job.finished_at = _now()
                job.error = f"{type(exc).__name__}: {exc}"
            logger.error("knowledge ingest job %s failed: %s", job_id, exc)
        else:
            with self._lock:
                job.status = "succeeded"
                job.phase = "done"
                job.finished_at = _now()
                job.result = outcome
                if job.total_chunks:
                    job.processed_chunks = job.total_chunks
                    job.total_chunks = max(job.total_chunks, int(outcome.get("chunks", 0)))
                else:
                    job.total_chunks = int(outcome.get("chunks", 0))
                    job.processed_chunks = job.total_chunks
                if job.cancel_requested:
                    job.note = (
                        "取消请求到达时已越过最后一个分块边界，入库已完成；"
                        "如需撤销请调用删除接口"
                    )
            logger.info(
                "knowledge ingest job %s succeeded: document=%s chunks=%s",
                job_id,
                job.document_id,
                outcome.get("chunks"),
            )
        finally:
            done_event.set()

    def _prune_locked(self) -> None:
        """Drop the oldest finished job record when the ledger grows too large."""
        if len(self._jobs) < _MAX_RETAINED_JOBS:
            return
        for job_id in list(self._jobs):
            job = self._jobs[job_id]
            if job.status in _TERMINAL:
                del self._jobs[job_id]
                self._cancel_events.pop(job_id, None)
                self._done_events.pop(job_id, None)
                break


_MANAGER: Optional[IngestJobManager] = None
_MANAGER_LOCK = threading.Lock()


def get_ingest_job_manager() -> IngestJobManager:
    global _MANAGER
    if _MANAGER is not None:
        return _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = IngestJobManager()
    return _MANAGER


def reset_ingest_job_manager() -> None:
    """Test helper: stop the worker pool and forget every job record."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            _MANAGER.shutdown()
        _MANAGER = None
