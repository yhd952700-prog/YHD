"""被父测试进程强杀的子进程：持续写入，并在每次"确认成功"后写一行 ack。

父进程用 ``TerminateProcess``（Windows）直接杀掉本进程，模拟任务管理器结束
进程/断电瞬间，然后检查重启后：已 ack 的写入是否还在、库是否完整、有没有
半截文件。

用法::

    python _crash_child.py <mode> <target> <ack_path>

mode: memory | conversation | audit | json | log
"""

from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def main() -> int:
    mode, target, ack_path = sys.argv[1], sys.argv[2], sys.argv[3]

    # 保持打开、逐行 flush：本测试强杀的是"进程"，OS 页缓存不受影响，
    # 只要 write+flush 返回就代表调用方已收到成功（不追求断电级 fsync）。
    _ack_handle = open(ack_path, "a", encoding="utf-8", buffering=1)

    def ack(index: int) -> None:
        # 双通道：文件留给崩溃后取证，stdout 给父进程实时计数。
        _ack_handle.write(f"{index}\n")
        _ack_handle.flush()
        print(f"ACK {index}", flush=True)

    if mode == "memory":
        from src.kernels.memory import MemoryKernel, MemoryTier

        kernel = MemoryKernel(db_path=target)
        index = 0
        while True:
            kernel.store(f"k{index}", "v" * 2048, tier=MemoryTier.PERSISTENT)
            ack(index)
            index += 1

    if mode == "conversation":
        from src.ai.conversation_store import ConversationStore

        store = ConversationStore(db_path=target)
        index = 0
        while True:
            store.append("crash-user", index, "user", "payload-" + "x" * 1024)
            ack(index)
            index += 1

    if mode == "audit":
        from src.kernels.audit import AuditEventType, AuditScope, AuditStore

        store = AuditStore(db_path=target)
        store.initialize()
        index = 0
        while True:
            store.log_event(
                AuditEventType.STATE_CHANGE,
                "crash-principal",
                AuditScope.L0,
                "ok",
                {"i": index, "blob": "x" * 512},
                f"crash-{index}",
            )
            ack(index)
            index += 1

    if mode == "json":
        from src.storage.backends import JSONFileBackend, StorageEntry

        backend = JSONFileBackend(target)
        index = 0
        while True:
            now = time.time()
            backend.put(
                f"k{index}",
                StorageEntry(
                    key=f"k{index}", value="v" * 2048, created_at=now, updated_at=now
                ),
            )
            ack(index)
            index += 1

    if mode == "log":
        import logging
        from src.logging_config import MultiProcessRotatingFileHandler

        handler = MultiProcessRotatingFileHandler(
            target, maxBytes=8192, backupCount=3
        )
        logger = logging.getLogger("crashlog")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers = [handler]
        index = 0
        while True:
            logger.info("seq=%d %s", index, "L" * 200)
            ack(index)
            index += 1

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
