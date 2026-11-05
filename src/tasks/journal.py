"""worker 侧 journal 的路径解析与进程内单例。

两个不同的 journal，别搞混：

* **执行 journal**（`ExecutionJournal`，由 `ExecutionEngine` 写入）：记录
  goal 执行过程中的 execution/task 事件。恢复扫描读它。
* **任务 journal**（同一个 `ExecutionJournal` 类，另一份文件）：记录 taskiq
  任务的幂等键与结果，防止队列重投递把同一次副作用执行两遍。

两者都是 append-only SQLite + WAL，多进程共享同一文件是安全的。
"""

from __future__ import annotations

import os
import threading
from typing import Dict, Optional

from src.kernels.execution._journal import ExecutionJournal

_JOURNALS: Dict[str, ExecutionJournal] = {}
_LOCK = threading.Lock()


def execution_journal_path() -> str:
    """Mirror `src/gateway/ai_management.py` so worker scans the same file
    the API process writes."""
    root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    if root:
        return os.path.join(root, "liuhao_execution.journal")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo_root, ".liuhao_execution.journal")


def task_journal_path() -> str:
    explicit = os.environ.get("LIUHAO_TASK_JOURNAL")
    if explicit:
        return explicit
    root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    base = (
        os.path.join(root, "queue")
        if root
        else os.path.join(os.path.expanduser("~"), ".liuhao", "queue")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "tasks.journal")


def get_execution_journal(path: Optional[str] = None) -> ExecutionJournal:
    return _get(path or execution_journal_path())


def get_task_journal(path: Optional[str] = None) -> ExecutionJournal:
    return _get(path or task_journal_path())


def _get(path: str) -> ExecutionJournal:
    journal = _JOURNALS.get(path)
    if journal is None:
        with _LOCK:
            journal = _JOURNALS.get(path)
            if journal is None:
                journal = ExecutionJournal(path)
                _JOURNALS[path] = journal
    return journal
