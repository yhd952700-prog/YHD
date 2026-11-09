from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

from src.tasks._queue_paths import resolve_sqlite_store


def _app_root() -> Path:
    """程序根（安装树 / 源码仓库）；取不到时退回本文件所在仓库根。"""
    try:
        from src.upgrade.update_check import default_app_root

        return default_app_root()
    except Exception:  # noqa: BLE001 - 仅用于探测旧位置，失败不影响解析
        return Path(__file__).resolve().parents[2]


def _legacy_metrics_db() -> Path:
    """旧默认位置：返回**确实存在**的那一份，都没有时退回 cwd。

    历史默认 ``sqlite:///./verify_metrics.db`` 是相对 cwd 解析的，所以旧库一定
    躺在"当年启动进程的那个目录"里。只看固定一个位置会漏：用户可能换了个目录
    启动（例如从仓库根换成安装根）。因此按顺序探测：

    1. 当前 cwd —— 老版本最常写入的位置；
    2. 程序根（安装树 / 源码仓库）—— 从程序目录启动过的那台机器。

    两者同时存在时不猜，按上面的优先级只迁移 **cwd** 那一份（两份内容如果
    不同，没有任何依据判断哪份更权威），另一份留在原处、不删不改。
    """
    candidates: list[Path] = []
    for root in (Path.cwd(), _app_root()):
        candidate = root / "verify_metrics.db"
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return candidates[0]


def default_metrics_database_url() -> str:
    """未显式配置时的指标库地址：统一数据根，旧 ``<cwd>/verify_metrics.db`` 迁移。

    历史默认 ``sqlite:///./verify_metrics.db`` 是相对 **当前工作目录** 解析的：
    从用户主目录或系统目录启动时会悄悄把指标库写到 C 盘。改为数据根下的
    ``verify_metrics.db``；旧位置存在时按队列同款流程迁移（backup API + 校验 +
    原子落位 + 旧文件保留），失败继续用旧库。
    """
    path = resolve_sqlite_store(
        "verify_metrics.db",
        _legacy_metrics_db(),
        label="指标库",
    )
    return "sqlite:///" + Path(path).as_posix()


@dataclass(frozen=True)
class ProviderMetric:
    provider: str
    model: str
    timestamp: str
    latency_ms: float
    success_rate: float


class ProviderMetricsRepository:
    """Small SQLite-backed repository for provider metrics.

    Connection-surface consolidation (round 15): a single SQLite connection is
    created once per repository instance and reused for all operations, instead
    of opening (and closing) a fresh connection on every call. This (a) removes
    the per-call open/close overhead, and (b) makes ``:memory:`` URLs work
    correctly — previously each call opened a *separate* in-memory database, so
    the table created in ``_initialize`` was invisible to later calls.

    A reentrant lock serializes access to the shared connection, preserving the
    thread-safety guarantee of the old per-call design (``check_same_thread=False``
    is still set as a defense-in-depth measure).
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = (
            database_url
            or (os.environ.get("DATABASE_URL") or "").strip()
            or default_metrics_database_url()
        )
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._initialize()

    @staticmethod
    def _resolve_sqlite_path(database_url: str) -> Path:
        parsed = urlparse(database_url)
        if parsed.scheme != "sqlite":
            raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

        if parsed.path in ("", ":memory:"):
            return Path(":memory:")

        path_value = parsed.path
        if path_value.startswith("/") and not path_value.startswith("//"):
            path_value = path_value.lstrip("/")

        database_file = Path(path_value)
        if not database_file.is_absolute():
            database_file = (Path.cwd() / database_file).resolve()
        return database_file

    def _connect(self) -> sqlite3.Connection:
        parsed = urlparse(self.database_url)
        if parsed.scheme == "sqlite":
            database_path = self._resolve_sqlite_path(self.database_url)
            if database_path != Path(":memory:"):
                database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(database_path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            return connection

        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_metric_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    success_rate REAL NOT NULL
                )
                """
            )
            self._conn.commit()

    def record(self, metric: ProviderMetric) -> ProviderMetric:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO provider_metric_samples (provider, model, timestamp, latency_ms, success_rate)
                VALUES (?, ?, ?, ?, ?)
                """,
                (metric.provider, metric.model, metric.timestamp, metric.latency_ms, metric.success_rate),
            )
            self._conn.commit()
        return metric

    def list_recent(self, limit: int = 10) -> List[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT provider, model, timestamp, latency_ms, success_rate
                FROM provider_metric_samples
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM provider_metric_samples").fetchone()
        return int(row[0]) if row is not None else 0

    def is_ready(self) -> bool:
        """Real liveness probe for the readiness endpoint.

        Returns ``True`` only when a genuine query round-trip against the
        underlying store actually succeeds, and ``False`` -- rather than
        raising -- when the store is unreachable or broken. A readiness probe
        must be *able to report not-ready*; the previous caller check
        (``count >= 0``) was a tautology (SQL ``COUNT(*)`` is never negative),
        so it could never fail and reported healthy without probing anything.
        """
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM provider_metric_samples"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def close(self) -> None:
        """Close the underlying connection. Safe to call multiple times."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "ProviderMetricsRepository":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup during GC
            pass
