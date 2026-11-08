"""跨版本迁移验收：真实 0.1.0 数据目录 → 当前代码。

做法
----
1. 把仓库里的 0.1.0 交付包解压到临时目录（只读源包，不动 ``dist/``）；
2. 用 **0.1.0 自己的网关** 造真实旧版数据：账号树、登录凭据、员工、目标、
   记忆、会话、知识库、模型 Key；
3. 停掉 0.1.0，用当前代码在同一数据目录上启动，逐项核对不丢数据、不报错；
4. 用 ``data-schema.json`` 机制打标 v2，确认当前代码可读且降级守卫生效；
5. 结束即删除临时目录，不留 ``%LOCALAPPDATA%`` 残留。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import backup_migration_e2e as e2e

REPO = e2e.REPO
ARCHIVE = (
    REPO
    / "dist"
    / "installer"
    / "_archive"
    / "20261107-mixed-0.1.0-and-0.1.1"
    / "liuhao-0.1.0.zip"
)


def _owned(documents: List[str], principal: str) -> List[str]:
    """知识库文档 id 形如 ``<principal>--<hash>``；按 owner 前缀取自有文档。"""
    return sorted(
        str(item) for item in documents
        if str(item).startswith(f"{principal}--")
    )


def _db_facts(data_dir: Path) -> Dict[str, Any]:
    """记录 0.1.0 数据目录的真实形态（表、列、行数、schema 标记）。"""
    facts: Dict[str, Any] = {}
    for rel in (
        "workspace/account_memory.sqlite3",
        "memory_store.db",
        "conversation_store.db",
        "data/model_registry.sqlite3",
        "data/knowledge_vectors.sqlite3",
    ):
        path = data_dir / rel
        if not path.is_file():
            facts[rel] = {"exists": False}
            continue
        last_error: Optional[Exception] = None
        for attempt in range(5):
            try:
                connection = sqlite3.connect(str(path), timeout=10)
                try:
                    tables = [
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                        )
                    ]
                    counts = {
                        table: int(
                            connection.execute(
                                f'SELECT COUNT(*) FROM "{table}"'
                            ).fetchone()[0]
                        )
                        for table in tables
                    }
                finally:
                    connection.close()
                facts[rel] = {"exists": True, "tables": tables, "counts": counts}
                break
            except sqlite3.Error as exc:  # noqa: PERF203 - 刚被杀进程的 WAL 需要恢复时间
                last_error = exc
                time.sleep(0.6 * (attempt + 1))
        else:
            facts[rel] = {"exists": True, "error": str(last_error)}
    marker = data_dir / "data-schema.json"
    facts["data-schema.json"] = (
        json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
    )
    for rel in (
        "workspace/liuhao_employees.json",
        "workspace/liuhao_goals.json",
    ):
        path = data_dir / rel
        facts[rel] = {"exists": path.is_file()}
    return facts


def _legacy_kernel_rows(data_dir: Path) -> Dict[str, int]:
    """0.1.0 内核记忆行分类：可归因、会话轮次、无账号归属。"""
    path = data_dir / "memory_store.db"
    stats = {"total": 0, "attributable": 0, "conversation": 0, "unowned": 0}
    if not path.is_file():
        return stats
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        rows = connection.execute("SELECT tags FROM memory_entries").fetchall()
    except sqlite3.Error:
        return stats
    finally:
        connection.close()
    for (tags,) in rows:
        stats["total"] += 1
        try:
            parsed = json.loads(tags or "[]")
        except Exception:  # noqa: BLE001
            parsed = []
        values = [str(item) for item in parsed if isinstance(item, str)]
        if "conversation" in values:
            stats["conversation"] += 1
        elif any(value.startswith("agent:liuhao-") for value in values):
            stats["attributable"] += 1
        else:
            stats["unowned"] += 1
    return stats


def _migration_marker(data_dir: Path) -> Optional[Dict[str, Any]]:
    path = data_dir / "workspace" / "account_memory.sqlite3"
    if not path.is_file():
        return None
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        row = connection.execute(
            "SELECT name, completed_at, details FROM account_memory_migrations "
            "WHERE name = 'legacy_kernel_memory_v1'"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if not row:
        return None
    try:
        details = json.loads(row[2] or "{}")
    except Exception:  # noqa: BLE001
        details = {}
    return {"name": row[0], "completed_at": row[1], "details": details}


def main() -> int:
    if not ARCHIVE.is_file():
        print(f"缺少 0.1.0 交付包：{ARCHIVE}")
        return 1
    temp_root = Path(tempfile.mkdtemp(prefix="liuhao-cross-version-"))
    old: Optional[e2e.Instance] = None
    current: Optional[e2e.Instance] = None
    checks: List[Dict[str, Any]] = []
    try:
        v010_dir = temp_root / "v010"
        shutil.unpack_archive(str(ARCHIVE), str(v010_dir), "zip")
        data_dir = temp_root / "data"

        print("=" * 78)
        print("阶段 1/3：用 0.1.0 自己的网关造旧版数据")
        print("=" * 78)
        old = e2e.Instance(temp_root / "v010-instance", data_dir=data_dir, cwd=v010_dir)
        old.start()
        tokens = e2e.seed(old)
        old_root = e2e.snapshot(old, tokens["root"])
        old_member = e2e.snapshot(old, tokens["member"])
        old.stop()
        old = None
        facts = _db_facts(data_dir)
        kernel_rows = _legacy_kernel_rows(data_dir)
        old_root_memory_count = len(old_root["memory"])
        old_member_memory_count = len(old_member["memory"])
        print(f"0.1.0 数据目录：{data_dir}")
        print(f"  账号：{old_root['accounts']}")
        print(f"  记忆（根/成员）：{old_root_memory_count}/{old_member_memory_count}")
        print(f"  内核记忆分类：{kernel_rows}")
        print(f"  会话：{old_root['sessions']} / {old_member['sessions']}")
        print(f"  模型 Key：{old_root['models']} / {old_member['models']}")
        print(f"  知识库：{old_root['knowledge']}")
        print(f"  员工：{old_root['employees']}")
        print(f"  目标：{old_root['goals']}")
        print(f"  schema 标记（0.1.0）：{facts['data-schema.json']}")

        print("=" * 78)
        print("阶段 2/3：用当前代码打开同一数据目录")
        print("=" * 78)
        current = e2e.Instance(temp_root / "current-instance", data_dir=data_dir, cwd=REPO)
        current.start()
        root_token = e2e.login(current, "root1")
        member_token = e2e.login(current, "member1")
        now_root = e2e.snapshot(current, root_token)
        now_member = e2e.snapshot(current, member_token)

        def check(item: str, ok: bool, detail: str) -> None:
            checks.append({"item": item, "ok": ok, "detail": detail})

        check("账号树不丢", old_root["accounts"] == now_root["accounts"],
              f"{old_root['accounts']} → {now_root['accounts']}")
        check("根账号原密码可登录", bool(root_token), "POST /v1/auth/login 200")
        check("成员原密码可登录", bool(member_token), "POST /v1/auth/login 200")
        check("根记忆一条不少",
              set(old_root["memory"]) <= set(now_root["memory"]),
              f"{old_root_memory_count} → {len(now_root['memory'])}")
        check("成员记忆一条不少",
              set(old_member["memory"]) <= set(now_member["memory"]),
              f"{old_member_memory_count} → {len(now_member['memory'])}")
        marker = _migration_marker(data_dir)
        attributable = int(kernel_rows["attributable"])
        skipped = int(kernel_rows["conversation"]) + int(kernel_rows["unowned"])
        check("可归因内核记忆已迁移",
              len(now_root["memory"]) + len(now_member["memory"])
              >= old_root_memory_count + old_member_memory_count + attributable,
              f"{old_root_memory_count}+{old_member_memory_count}+{attributable} → "
              f"{len(now_root['memory'])}+{len(now_member['memory'])}")
        check("无非账号归属误迁移（会话轮次/无归属按设计跳过）",
              bool(marker)
              and int(marker["details"].get("entries", -1)) == attributable
              and int(marker["details"].get("skipped", -1)) == skipped,
              f"entries={marker['details'].get('entries') if marker else '无标记'} "
              f"skipped={marker['details'].get('skipped') if marker else '无标记'} "
              f"(conversation {kernel_rows['conversation']} + unowned {kernel_rows['unowned']})")
        check("记忆迁移标记已落库", bool(marker),
              "legacy_kernel_memory_v1" if marker else "account_memory_migrations 无记录")
        check("会话列表不丢", old_root["sessions"] == now_root["sessions"],
              f"{old_root['sessions']} → {now_root['sessions']}")
        check("成员会话不丢", old_member["sessions"] == now_member["sessions"],
              f"{old_member['sessions']} → {now_member['sessions']}")
        check("员工不丢", old_root["employees"] == now_root["employees"],
              f"{old_root['employees']} → {now_root['employees']}")
        check("目标不丢", old_root["goals"] == now_root["goals"],
              f"{old_root['goals']} → {now_root['goals']}")
        check("根自有知识库不丢",
              _owned(old_root["knowledge"], "root1") == _owned(now_root["knowledge"], "root1"),
              f"{_owned(old_root['knowledge'], 'root1')} → "
              f"{_owned(now_root['knowledge'], 'root1')}")
        check("成员知识库不丢",
              _owned(old_member["knowledge"], "member1")
              == _owned(now_member["knowledge"], "member1"),
              f"{_owned(old_member['knowledge'], 'member1')} → "
              f"{_owned(now_member['knowledge'], 'member1')}")
        check("根模型 Key 可用",
              any(item.startswith("root-migration-key:ok") for item in now_root["models"]),
              f"{old_root['models']} → {now_root['models']}")
        check("成员模型 Key 可用",
              any(item.startswith("member-migration-key:ok") for item in now_member["models"]),
              f"{old_member['models']} → {now_member['models']}")
        log_tail = current.tail()
        bad_log = "Traceback (most recent call last)" in log_tail
        check("当前网关启动/访问无未捕获异常", not bad_log,
              "server.log 无 Traceback" if not bad_log else log_tail[-3000:])

        print("=" * 78)
        print("阶段 3/3：data-schema.json 打标与降级守卫")
        print("=" * 78)
        from src.upgrade.dataversion import (
            DATA_SCHEMA_VERSION,
            ensure_data_readable,
            read_data_schema,
            stamp_data_schema,
        )

        assert read_data_schema(data_dir) is None, "0.1.0 数据不应已有 schema 标记"
        state = stamp_data_schema(
            data_dir, app_version="0.1.0-to-current-acceptance"
        )
        readable = ensure_data_readable(
            data_dir, target_schema=DATA_SCHEMA_VERSION
        )
        check("打标后当前代码可读", readable is not None and readable.version == DATA_SCHEMA_VERSION,
              f"v{state.version} by {state.written_by}")
        try:
            ensure_data_readable(data_dir, target_schema=1)
            downgrade_guard = False
        except Exception as exc:  # noqa: BLE001
            downgrade_guard = "更高版本" in str(exc)
        check("低版本读取被拒绝（降级守卫）", downgrade_guard,
              "schema v2 对 target=1 抛 DataSchemaError")

        table = e2e.Table()
        for item in checks:
            table.add(item["item"], "0.1.0", "当前", item["ok"])
        print()
        print(table.render())
        print()
        for item in checks:
            mark = "✅" if item["ok"] else "❌"
            print(f"  {mark} {item['item']}：{item['detail']}")
        print()
        ok = all(item["ok"] for item in checks)
        print("全部通过" if ok else "存在失败项")
        return 0 if ok else 1
    finally:
        if current is not None:
            current.stop()
        if old is not None:
            old.stop()
        if os.environ.get("LIUHAO_KEEP_CROSS_VERSION_DIR"):
            print(f"保留临时目录（调试）：{temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)
            print(f"已清理临时目录：{temp_root}")


if __name__ == "__main__":
    raise SystemExit(main())
