"""In-process asynchronous ingest jobs (progress + cancellation).

The knowledge store is a process-level SQLite singleton, so jobs are intentionally
in-process: no broker dependency, no new requirement. A job id is returned
immediately; progress is observable and cancellation is honoured at the next
**page boundary** (file import) or the next **chunk boundary** (raw text).

Two job shapes share one ledger:

* ``submit(text, ...)`` -- raw text, progress in chunks;
* ``submit_file(source, filename, ...)`` -- a real upload, progress in **pages**.
  The HTTP layer streams the body to a temporary file and passes its **path**
  (raw ``bytes`` stay accepted for in-process callers), so a queued job does
  not hold a 20MB copy. The worker deletes the temp file on success, failure
  and cancellation. Extraction reports one step per parsed PDF page (blank
  pages included) and embedding reports one step per source page, so
  ``progress_percent`` is computed from counted pages, never from a timer.
  DOCX / HTML / TXT have no pages and fall back to chunk progress.

Partial-ingest policy: **all-or-nothing (全回滚)**. Vectors are computed before
anything is written, and the store swap is one transaction, so a cancelled job
leaves zero chunks/vectors/FTS rows/hash rows behind. Cancelling during
*extraction* writes even less: nothing is written at all.

A garbled text layer (``\ufffd`` / tofu blocks above the ratio threshold) fails
the job with ``error_code="low_quality_extraction"`` unless the caller passed
``allow_low_quality=True`` -- the file is never silently stored as bad corpus.

Limitation (stated, not hidden): jobs live in memory. A process restart loses
job records (the ingested data itself is already durable, or absent). Jobs that
already finished are kept for inspection but are never resumed.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from .documents import (
    IngestCancelled,
    KnowledgeDocumentService,
    KnowledgeScope,
    get_knowledge_service,
    scoped_document_id,
)
from .extractors import ExtractionCancelled, ExtractionError, LowQualityText
from .extractors import extract_document

logger = logging.getLogger("liuhao.knowledge.ingest_jobs")

_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_MAX_RETAINED_JOBS = 200
# 队列上限：`max_workers=2` 但原来 ``ThreadPoolExecutor`` 的待执行队列无界，
# 每个上传（payload 最多 20MB）都被闭包捕获驻留内存，很容易把进程拖垮。
# 达到上限时**明确拒绝**（提交方收到 IngestQueueFull / HTTP 429），而不是继续
# 把任务塞进无界队列。
_MAX_QUEUED_JOBS = 32
_PARTIAL_POLICY = "all-or-nothing（取消后不写入任何分块，不会留下半成品）"


def _max_queued_jobs() -> int:
    raw = (os.environ.get("LIUHAO_INGEST_MAX_QUEUED") or "").strip()
    try:
        return max(1, int(raw)) if raw else _MAX_QUEUED_JOBS
    except ValueError:
        return _MAX_QUEUED_JOBS


class IngestQueueFull(RuntimeError):
    """入队被拒绝：未结束的入库任务已达到上限。"""

    code = "ingest_queue_full"

    def __init__(self, pending: int, limit: int) -> None:
        super().__init__(
            f"知识库入库队列已满（当前未结束任务 {pending} 个，上限 {limit} 个）；"
            "请等待已有任务完成或取消后重试。"
        )
        self.pending = pending
        self.limit = limit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cleanup_source(source: Any) -> None:
    """删除上传临时文件；bytes 输入没有文件可删。失败只记日志。"""
    if isinstance(source, (bytes, bytearray)) or source is None:
        return
    try:
        Path(source).unlink(missing_ok=True)
    except OSError:
        logger.warning("knowledge ingest temp file could not be removed: %s", source)


#: 入库失败对外的中文文案。异常原文（类名、路径、底层报错）只进服务端日志，
#: 绝不出现在 ``job.error``——它是直接上屏的字段。
_INGEST_ERROR_MESSAGES: Dict[str, str] = {
    "unsupported_file_type": (
        "这个文件格式暂不支持，文件没有入库。请转换成 PDF、Word 或纯文本后重试。"
    ),
    "file_too_large": "文件太大，文件没有入库。请拆分或压缩后重试。",
    "too_many_pages": "文件页数太多，文件没有入库。请拆分后重试。",
    "empty_document": "解析失败：文件里没有可用的文字，文件没有入库。请换一个文件后重试。",
    "pdf_no_text_layer": (
        "解析失败：这个 PDF 没有可提取的文字层（可能是扫描件）。"
        "请先做文字识别，或换一个文件后重试。"
    ),
    "pdf_encrypted": "解析失败：这个 PDF 已加密，文件没有入库。请解除密码后重试。",
    "corrupted_document": "解析失败：文件已损坏，文件没有入库。请换一个文件后重试。",
    "pdf_page_timeout": (
        "解析失败：PDF 里有一页内容异常复杂，超过单页解析时限，文件没有入库。"
        "请拆分或压缩该页后重试。"
    ),
    "extraction_failed": "解析失败：无法读取这个文件的内容，文件没有入库。请换一个文件后重试。",
    "low_quality_extraction": (
        "解析失败：提取出的文字太少或质量过低，文件没有入库。"
        "请换一个文件；确认内容可用时，可允许低质量入库后重试。"
    ),
    "extraction_cancelled": "任务已取消，没有写入任何内容。",
    "ingest_queue_full": "当前排队入库的文件太多，暂时无法接收新文件。请稍后重试。",
    "disk_full": "磁盘空间不足，文件没有入库。请清理空间后重试。",
    "service_unavailable": (
        "本地解析或向量服务暂时不可用，文件没有入库。请稍后重试；"
        "如果反复出现，请导出诊断包。"
    ),
    "ingest_failed": (
        "处理失败，本次没有入库。请稍后重试；如果反复出现，请导出诊断包。"
    ),
}


def _classify_ingest_error(exc: Exception) -> str:
    """把异常归到稳定的 ``error_code``：已知业务码优先，其次按类型兜底。"""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip():
        return code.strip()
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return "disk_full"
    name = type(exc).__name__.lower()
    if any(token in name for token in ("unavailable", "timeout", "connection")):
        return "service_unavailable"
    if isinstance(exc, ExtractionError):
        return "extraction_failed"
    if isinstance(exc, OSError):
        return "disk_full" if getattr(exc, "errno", None) == errno.ENOSPC else "service_unavailable"
    return "ingest_failed"


def _friendly_ingest_error(exc: Exception) -> Dict[str, str]:
    """返回 ``{"code","message"}``：message 是可直接上屏的中文，原文只进日志。"""
    code = _classify_ingest_error(exc)
    return {
        "code": code,
        "message": _INGEST_ERROR_MESSAGES.get(code, _INGEST_ERROR_MESSAGES["ingest_failed"]),
    }


@dataclass
class _Job:
    job_id: str
    document_id: str
    owner_account_id: str = ""
    source: str = "text"
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    status: str = "queued"
    phase: str = "queued"
    total_chunks: int = 0
    processed_chunks: int = 0
    #: Paged progress. ``total_pages`` is 0 for formats without pages.
    total_pages: int = 0
    extracted_pages: int = 0
    embedded_pages: int = 0
    quality: Optional[Dict[str, Any]] = None
    allow_low_quality: bool = False
    error_code: Optional[str] = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    note: Optional[str] = None

    def progress_percent(self) -> float:
        """Determinate progress from counted work, never from elapsed time.

        A paged job has two page-sized phases (extraction, embedding), so each
        counted page is worth 1/(2*total_pages). Until the job really succeeds
        the value is capped at 99.0: 100% means committed, not "almost there".
        """
        if self.status == "succeeded":
            return 100.0
        if self.total_pages > 0:
            done = self.extracted_pages + self.embedded_pages
            percent = done * 100.0 / (2 * self.total_pages)
            return round(min(percent, 99.0), 1)
        if self.total_chunks > 0:
            percent = self.processed_chunks * 100.0 / self.total_chunks
            return round(min(percent, 99.0), 1)
        return 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "document_id": self.document_id,
            "owner_account_id": self.owner_account_id,
            "source": self.source,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "status": self.status,
            "phase": self.phase,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "total_pages": self.total_pages,
            "extracted_pages": self.extracted_pages,
            "embedded_pages": self.embedded_pages,
            "processed_pages": self.extracted_pages + self.embedded_pages,
            "progress_percent": self.progress_percent(),
            "quality": self.quality,
            "allow_low_quality": self.allow_low_quality,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "error_code": self.error_code,
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
        max_queued: Optional[int] = None,
    ) -> None:
        self._service_factory = service_factory or get_knowledge_service
        self._lock = threading.RLock()
        self._jobs: Dict[str, _Job] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._done_events: Dict[str, threading.Event] = {}
        self._max_queued = max(1, int(max_queued)) if max_queued else _max_queued_jobs()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="kb-ingest"
        )

    # -- lifecycle ---------------------------------------------------------
    def submit(
        self,
        text: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        return self._submit(
            "text", text, None, doc_id, metadata, False, owner_account_id
        )

    def submit_file(
        self,
        source: Union[bytes, os.PathLike[str], str],
        filename: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allow_low_quality: bool = False,
        *,
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        """Queue an ingest job that starts from an upload source.

        ``source`` is preferably a **filesystem path** to the temporary upload:
        extraction runs inside the worker from that path, so the job never holds
        a full 20MB byte copy while queued, and the worker removes the file on
        success / failure / cancellation. Raw ``bytes`` remain accepted for
        in-process callers (tests); the HTTP layer always passes a path.

        Extraction runs inside the worker, so the HTTP request returns at once
        and a 300-page PDF reports real page progress instead of blocking.
        """
        return self._submit(
            "file",
            source,
            filename,
            doc_id,
            metadata,
            allow_low_quality,
            owner_account_id,
        )

    def _submit(
        self,
        source: str,
        payload: Any,
        filename: Optional[str],
        doc_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
        allow_low_quality: bool,
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        owner = str(owner_account_id or "").strip()
        job = _Job(
            job_id=uuid.uuid4().hex,
            document_id=scoped_document_id(owner, doc_id or filename),
            owner_account_id=owner,
            source=source,
            file_name=filename,
            allow_low_quality=allow_low_quality,
            phase="queued",
        )
        cancel_event = threading.Event()
        done_event = threading.Event()
        with self._lock:
            self._prune_locked()
            pending = sum(1 for j in self._jobs.values() if j.status not in _TERMINAL)
            if pending >= self._max_queued:
                raise IngestQueueFull(pending, self._max_queued)
            self._jobs[job.job_id] = job
            self._cancel_events[job.job_id] = cancel_event
            self._done_events[job.job_id] = done_event
        runner = self._run if source == "text" else self._run_file
        self._executor.submit(
            runner,
            job.job_id,
            payload,
            filename,
            dict(metadata or {}),
            allow_low_quality,
            owner,
            cancel_event,
            done_event,
        )
        return job.snapshot()

    def get(
        self, job_id: str, scope: Optional[KnowledgeScope] = None
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if scope is not None and not scope.covers(job.owner_account_id):
                return None
            return job.snapshot()

    def cancel(
        self, job_id: str, scope: Optional[KnowledgeScope] = None
    ) -> Optional[Dict[str, Any]]:
        """Request cancellation; it lands at the next chunk boundary."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if scope is not None and not scope.may_write(job.owner_account_id):
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
            if job.phase == "extracting":
                reason = "取消将在下一个页边界生效；尚未写入任何数据（全回滚）"
            else:
                reason = "取消将在下一个分块边界生效；已入库内容按全回滚处理"
            return {
                **job.snapshot(),
                "cancel_accepted": True,
                "reason": reason,
            }

    def wait(
        self,
        job_id: str,
        timeout: float = 30.0,
        scope: Optional[KnowledgeScope] = None,
    ) -> Optional[Dict[str, Any]]:
        """Block until the job leaves the running state (tests / callers)."""
        with self._lock:
            done = self._done_events.get(job_id)
        if done is not None:
            done.wait(timeout)
        return self.get(job_id, scope)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # -- worker ------------------------------------------------------------
    def _run(
        self,
        job_id: str,
        text: str,
        filename: Optional[str],
        metadata: Dict[str, Any],
        allow_low_quality: bool,
        owner_account_id: str,
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
                owner_account_id=owner_account_id,
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
            error = _friendly_ingest_error(exc)
            with self._lock:
                job.status = "failed"
                job.phase = "failed"
                job.finished_at = _now()
                job.error = error["message"]
                job.error_code = error["code"]
            logger.error(
                "knowledge ingest job %s failed: %s", job_id, exc, exc_info=True
            )
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

    def _run_file(
        self,
        job_id: str,
        source: Union[bytes, os.PathLike[str], str],
        filename: Optional[str],
        metadata: Dict[str, Any],
        allow_low_quality: bool,
        owner_account_id: str,
        cancel_event: threading.Event,
        done_event: threading.Event,
    ) -> None:
        """Worker wrapper: always removes a temp upload and signals completion."""
        try:
            self._process_file(
                job_id,
                source,
                filename,
                metadata,
                allow_low_quality,
                owner_account_id,
                cancel_event,
            )
        finally:
            _cleanup_source(source)
            done_event.set()

    def _process_file(
        self,
        job_id: str,
        source: Union[bytes, os.PathLike[str], str],
        filename: Optional[str],
        metadata: Dict[str, Any],
        allow_low_quality: bool,
        owner_account_id: str,
        cancel_event: threading.Event,
    ) -> None:
        """Extract -> embed -> commit, with page-counted progress throughout."""
        name = filename or ""
        with self._lock:
            job = self._jobs[job_id]
        if cancel_event.is_set():
            # 排队期间已请求取消：不解析、不嵌入，直接回滚。
            self._mark_cancelled(job, note="已取消（尚未开始解析；未写入任何数据）")
            logger.info("knowledge file job %s cancelled before extraction", job_id)
            return
        with self._lock:
            job.status = "running"
            job.phase = "extracting"
            job.started_at = _now()

        def on_page(processed: int, total: int) -> None:
            with self._lock:
                job.total_pages = total
                job.extracted_pages = processed
                job.phase = "extracting"

        def on_progress(processed: int, total: int) -> None:
            with self._lock:
                job.total_chunks = total
                job.processed_chunks = processed
                job.phase = "embedding" if processed < total else "committing"

        def on_page_progress(processed: int, total: int) -> None:
            with self._lock:
                job.total_pages = job.total_pages or total
                job.embedded_pages = processed
                job.phase = "embedding" if processed < total else "committing"

        try:
            extracted = extract_document(
                source,
                name,
                allow_low_quality=allow_low_quality,
                on_page=on_page,
                cancel_event=cancel_event,
            )
        except ExtractionCancelled as exc:
            self._mark_cancelled(
                job, processed_pages=exc.processed, total_pages=exc.total,
                note="已取消；尚未写入任何数据（全回滚）",
            )
            logger.info(
                "knowledge file job %s cancelled during extraction at page %d/%d",
                job_id, exc.processed, exc.total,
            )
            return
        except LowQualityText as exc:
            self._mark_failed(job, exc, quality=exc.quality.summary())
            logger.error("knowledge file job %s refused low-quality text", job_id)
            return
        except ExtractionError as exc:
            self._mark_failed(job, exc)
            logger.error("knowledge file job %s extraction failed: %s", job_id, exc)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            self._mark_failed(job, exc)
            logger.error("knowledge file job %s failed: %s", job_id, exc)
            return

        with self._lock:
            job.file_type = extracted.file_type
            job.quality = extracted.quality.summary() if extracted.quality else None
            job.extracted_pages = extracted.page_count or job.extracted_pages
            job.total_pages = extracted.page_count or job.total_pages
            job.phase = "embedding"

        document_metadata: Dict[str, Any] = {
            "source": name,
            "filename": name,
            "file_type": extracted.file_type,
            **extracted.metadata,
        }
        if extracted.title:
            document_metadata["title"] = extracted.title
        document_metadata.update(metadata)

        try:
            service = self._service_factory()
            outcome = service.ingest(
                extracted.text,
                doc_id=job.document_id,
                metadata=document_metadata,
                pages=extracted.pages or None,
                on_progress=on_progress,
                on_page_progress=on_page_progress,
                cancel_event=cancel_event,
                owner_account_id=owner_account_id,
            )
        except IngestCancelled as exc:
            self._mark_cancelled(
                job,
                processed_chunks=exc.processed,
                total_chunks=exc.total,
                note="已取消；未写入任何分块（全回滚）",
            )
            logger.info(
                "knowledge file job %s cancelled at chunk boundary %d/%d",
                job_id, exc.processed, exc.total,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            self._mark_failed(job, exc)
            logger.error("knowledge file job %s failed: %s", job_id, exc)
        else:
            with self._lock:
                job.status = "succeeded"
                job.phase = "done"
                job.finished_at = _now()
                job.total_chunks = int(outcome.get("chunks", 0))
                job.processed_chunks = job.total_chunks
                if job.total_pages:
                    job.extracted_pages = job.total_pages
                    job.embedded_pages = job.total_pages
                job.result = {**outcome, "extraction": extracted.summary()}
                if job.cancel_requested:
                    job.note = (
                        "取消请求到达时已越过最后一个边界，入库已完成；"
                        "如需撤销请调用删除接口"
                    )
            logger.info(
                "knowledge file job %s succeeded: document=%s chunks=%s pages=%s",
                job_id, job.document_id, outcome.get("chunks"), job.total_pages,
            )

    def _mark_cancelled(
        self,
        job: _Job,
        *,
        processed_pages: int = 0,
        total_pages: int = 0,
        processed_chunks: int = 0,
        total_chunks: int = 0,
        note: str,
    ) -> None:
        with self._lock:
            job.status = "cancelled"
            job.phase = "cancelled"
            job.extracted_pages = processed_pages or job.extracted_pages
            job.total_pages = total_pages or job.total_pages
            job.processed_chunks = processed_chunks
            job.total_chunks = total_chunks or job.total_chunks
            job.finished_at = _now()
            job.result = {
                "committed_chunks": 0,
                "partial_commit_policy": _PARTIAL_POLICY,
            }
            job.note = note

    def _mark_failed(
        self,
        job: _Job,
        exc: Exception,
        quality: Optional[Dict[str, Any]] = None,
    ) -> None:
        error = _friendly_ingest_error(exc)
        with self._lock:
            job.status = "failed"
            job.phase = "failed"
            job.finished_at = _now()
            # 上屏字段只放中文分类原因；异常类名/原文只进日志与诊断包。
            job.error = error["message"]
            job.error_code = error["code"]
            if quality is not None:
                job.quality = quality
                job.note = (
                    "提取质量差，未入库；确认要走这一步请用 allow_low_quality=true 重新提交"
                )

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
