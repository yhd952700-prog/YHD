"""worker 侧 journal 的路径解析与进程内单例。

两个不同的 journal，别搞混：

* **执行 journal**（`ExecutionJournal`，由 `ExecutionEngine` 写入）：记录
  goal 执行过程中的 execution/task 事件。恢复扫描读它。
* **任务 journal**（同一个 `ExecutionJournal` 类，另一份文件）：记录 taskiq
  任务的幂等键与结果，防止队列重投递把同一次副作用执行两遍。

两者都是 append-only SQLite + WAL，多进程共享同一文件是安全的。

执行 journal 的落点现在跟随统一数据根（``<data>/execution.journal``），
并且三个读取方（worker、gateway、artifacts CLI）共用 :func:`execution_journal_path`
——任何一侧另拼路径都会造成日志分裂。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, Optional

from src.common.data_paths import data_root, repo_root, resolve_store_path
from src.kernels.execution._journal import ExecutionJournal

_JOURNALS: Dict[str, ExecutionJournal] = {}
_LOCK = threading.Lock()

#: 执行 journal 的显式路径覆盖（最高优先级）。
EXECUTION_JOURNAL_ENV = "LIUHAO_EXECUTION_JOURNAL_PATH"


def legacy_execution_journal_path() -> str:
    """历史落点：安装版 ``<workspace>/liuhao_execution.journal``，源码模式仓库根。"""
    root = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if root:
        return os.path.join(root, "liuhao_execution.journal")
    return str(repo_root() / ".liuhao_execution.journal")


def execution_journal_path() -> str:
    """执行 journal 的落点（调用时求值；旧位置有数据时先迁移）。

    解析顺序：显式变量 → 数据根（``<data>/execution.journal``）→ 旧位置。
    ``LIUHAO_DATA_DIR`` 优先于 ``LIUHAO_WORKSPACE_ROOT``：数据根是整套隔离的
    单一根，安装版启动器本来就把 workspace 放在 ``<data>/workspace`` 下。

    迁移复用队列那份临界区（跨进程锁 + 双重检查 + staging 带 pid/随机 +
    校验 + fsync + 原子落位）：旧位置有文件、新位置没有时复制过去并校验
    （表集合 + 逐表行数）；失败记 ERROR 并**继续用旧位置**，绝不新建空日志。
    旧文件一律保留不删。
    """
    explicit = (os.environ.get(EXECUTION_JOURNAL_ENV) or "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    legacy = legacy_execution_journal_path()
    if data_root() is None:
        # 源码模式（未设数据根）：维持历史位置，不迁移、不新建目录。
        return legacy
    from src.tasks._queue_paths import _migrate_if_needed

    target = Path(resolve_store_path("execution.journal", legacy))
    return str(_migrate_if_needed(target, Path(legacy), kind="sqlite"))


def task_journal_path() -> str:
    """幂等 journal 的落点（与队列同一目录，旧位置有数据时先迁移）。

    journal 本身是 append-only SQLite + WAL，所以迁移校验用「表集合 + 逐表行数」
    而不是逐字节比对：WAL 里未 checkpoint 的行逐字节比对会误判。
    """
    from src.tasks._queue_paths import resolve_queue_file

    return resolve_queue_file("tasks.journal", env_var="LIUHAO_TASK_JOURNAL")


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
