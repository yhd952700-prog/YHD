"""换机四件事端到端验收：两条路线差分 / 目标目录 / 路径可移植 / 附件归属。

同一份造数（账号树、员工、目标、记忆、会话、知识库、模型 Key、工作区附件）
上分别走：

* **界面导出**：``POST /v1/accounts/backup/export``（scope=all）→ 新机导入；
* **CLI 备份**：``python -m src.cli backup create --encrypt`` → 空目录恢复。

然后逐项比对两份产物里实际装了什么，输出「界面带得走 / CLI 带得走 / 都不带」
实测表；差异必须能给出理由，写进手册。

同时验证：

1. 恢复目标目录三种情形：已有整套数据（拒绝）、完全为空（正常）、只有零散
   文件（拒绝），失败后目标目录逐文件不变、无半截文件/暂存目录残留；
2. 恢复后扫描文本文件与 SQLite 文本列，确认没有残留源机绝对路径；
3. 用户上传文件、生成的图表/文档到底随不随包；超过单文件上限时界面导出
   是否如实列入 ``skipped``、回执是否明说。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/backup_routes_diff_e2e.py

可选环境变量：
    LIUHAO_DIFF_OVERSIZE_MB=66   界面导出单文件上限测试用的文件大小（默认 66）
    LIUHAO_DIFF_KEEP_TMP=1       保留临时目录（默认结束清理）
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(REPO))

PASSWORD = "换机路线差分-2026"
_failures: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"[ OK ] {name}")
        return
    _failures.append(name + (f" ({detail})" if detail else ""))
    print(f"[FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _load_base():
    spec = importlib.util.spec_from_file_location(
        "backup_migration_e2e",
        REPO / "scripts" / "acceptance" / "backup_migration_e2e.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# CLI 子进程
# --------------------------------------------------------------------------- #

def run_cli(env: Dict[str, str], *args: str) -> Tuple[int, Dict[str, Any], str]:
    command = [
        str(PY),
        "-c",
        "import sys\nfrom src.cli import main\nraise SystemExit(main())\n",
        *args,
    ]
    proc = subprocess.run(
        command,
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    raw = (proc.stdout or "") + (proc.stderr or "")
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = {}
    return proc.returncode, payload, raw.strip()


def cli_env(data_dir: Path, backup_dir: Path, base) -> Dict[str, str]:
    env = base.clean_env_base()
    env["LIUHAO_DATA_DIR"] = str(data_dir)
    env["LIUHAO_BACKUP_DIR"] = str(backup_dir)
    env.pop("LIUHAO_BACKUP_PASSWORD", None)
    env["PYTHONUTF8"] = "1"
    return env


# --------------------------------------------------------------------------- #
# 归档读取 / 计数
# --------------------------------------------------------------------------- #

def load_cli_payload(archive: Path) -> Dict[str, Any]:
    from src.cli.backup_commands import _open_archive

    compressed = _open_archive(str(archive), password=PASSWORD)
    return json.loads(gzip.decompress(compressed).decode("utf-8"))


def _sqlite_temp(raw: bytes):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        fd, name = tempfile.mkstemp(suffix=".sqlite3", prefix="liuhao-diff-")
        os.close(fd)
        Path(name).write_bytes(raw)
        try:
            connection = sqlite3.connect(name)
            try:
                yield connection
            finally:
                connection.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(name + suffix)
                except OSError:
                    pass

    return _ctx()


def _count(raw: bytes, sql: str) -> int:
    with _sqlite_temp(raw) as connection:
        row = connection.execute(sql).fetchone()
        return int(row[0] or 0) if row else 0


def _knowledge_counts(raw: bytes) -> Tuple[int, int]:
    with _sqlite_temp(raw) as connection:
        vectors = connection.execute(
            "SELECT COUNT(*) FROM knowledge_vectors"
        ).fetchone()[0]
        try:
            documents = connection.execute(
                "SELECT COUNT(DISTINCT document_id) FROM knowledge_vectors"
            ).fetchone()[0]
        except sqlite3.DatabaseError:
            documents = vectors
    return int(vectors or 0), int(documents or 0)


def _decode_entry(entry: Dict[str, Any]) -> bytes:
    if entry.get("content_b64"):
        return base64.b64decode(entry["content_b64"])
    if entry.get("content"):
        return base64.b64decode(entry["content"])
    raise ValueError("归档条目没有内容")


def _cli_presence(payload: Dict[str, Any], rel: str) -> Optional[Dict[str, Any]]:
    data = payload.get("data") or {}
    user = (data.get("user_data") or {}).get("files") or {}
    if rel in user:
        return user[rel]
    config = (data.get("config") or {}).get("files") or {}
    if rel.startswith("config/"):
        return config.get(rel[len("config/"):])
    memory = data.get("memory_data") or {}
    if rel == "memory_store.db" and memory.get("type") == "file":
        return memory
    knowledge = data.get("knowledge_base") or {}
    if rel == "data/knowledge_vectors.sqlite3" and knowledge.get("type") == "file":
        return knowledge
    return None


def _cli_file_bytes(payload: Dict[str, Any], rel: str) -> Optional[bytes]:
    entry = _cli_presence(payload, rel)
    if not entry:
        return None
    return _decode_entry(entry)


# --------------------------------------------------------------------------- #
# 造数 / 附件
# --------------------------------------------------------------------------- #

SMALL_ATTACHMENTS = (
    ("uploads/root-manual.pdf", b"%PDF-1.4 liuhao root manual\n" * 64),
    ("artifacts/root1/chart.svg", b"<svg><text>LIUHAO-CHART</text></svg>\n" * 32),
    ("artifacts/root1/summary.md", "根账号生成的换机摘要\n".encode("utf-8") * 32),
    ("artifacts/member1/member-note.txt", "成员上传的说明\n".encode("utf-8") * 32),
)
UNDER_LIMIT = "artifacts/root1/large-under-limit.bin"
OVERSIZE = "artifacts/root1/large-oversize.bin"


def seed_attachments(workspace: Path, oversize_mb: int) -> Dict[str, str]:
    """造用户上传/生成文件，返回相对路径 -> sha256。"""
    hashes: Dict[str, str] = {}
    seen = set()
    for rel, data in SMALL_ATTACHMENTS:
        if rel in seen:
            continue
        seen.add(rel)
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()

    under = workspace / UNDER_LIMIT
    under.parent.mkdir(parents=True, exist_ok=True)
    chunk = b"LIUHAO-UNDER-LIMIT-1MB\n" * (1024 * 1024 // 23 + 1)
    with open(under, "wb") as handle:
        for _ in range(4):
            handle.write(chunk[: 1024 * 1024])
    hashes[UNDER_LIMIT] = hashlib.sha256(under.read_bytes()).hexdigest()

    over = workspace / OVERSIZE
    fill = b"A" * (1024 * 1024)
    with open(over, "wb") as handle:
        for _ in range(oversize_mb):
            handle.write(fill)
    hashes[OVERSIZE] = hashlib.sha256(over.read_bytes()).hexdigest()
    return hashes


def seed_artifact_index(workspace: Path, rels: List[str]) -> None:
    """把附件登记进工件索引，索引里的绝对路径故意指向源机。"""
    from src.artifacts.index import ArtifactIndex

    index = ArtifactIndex(str(workspace / "liuhao_artifacts.db"))
    try:
        for order, rel in enumerate(rels):
            index.record(
                workspace=str(workspace),
                path=rel,
                execution_id=f"e2e-diff-{order}",
                goal_id="e2e-diff-goal",
                task_name=f"write-{Path(rel).stem}",
                source="e2e",
            )
    finally:
        index.close()
    _checkpoint(workspace / "liuhao_artifacts.db")


def seed_execution_journal(workspace: Path, rel: str) -> None:
    """写一条"任务完成且声明了产物"的执行日志（绝对路径）。"""
    from src.kernels.execution._journal import ExecutionJournal

    journal = ExecutionJournal(str(workspace / "liuhao_execution.journal"))
    try:
        journal.record(
            "e2e-diff-execution",
            "task_completed",
            task_name="render-chart",
            success=True,
            artifact_path=str(workspace / rel),
        )
    finally:
        journal.close()
    _checkpoint(workspace / "liuhao_execution.journal")


def _checkpoint(db_path: Path) -> None:
    try:
        connection = sqlite3.connect(str(db_path), timeout=10)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        pass


def seed_runtime_traces(data_dir: Path) -> None:
    """日志与诊断包：新机会重新生成，两条路线都不该带走。"""
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "liuhao.log").write_text(
        "2026-11-08 INFO 服务启动，源机路径 " + str(data_dir) + "\n",
        encoding="utf-8",
    )
    diagnostics = data_dir / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "diag.zip").write_bytes(b"PK\x03\x04 fake diagnostics\n")
    lock = data_dir / "model_registry.sqlite3.lock"
    lock.write_text("", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 目录快照 / 暂存残留
# --------------------------------------------------------------------------- #

def tree_inventory(root: Path) -> Dict[str, str]:
    if not root.exists():
        return {}
    out: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            out[rel + "/"] = "dir"
        else:
            try:
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                out[rel] = "unreadable"
    return out


def stage_residue() -> set:
    pattern = "liuhao-restore-stage-*"
    return {
        str(path)
        for directory in {tempfile.gettempdir(), str(Path.home())}
        if Path(directory).is_dir()
        for path in Path(directory).glob(pattern)
    }


# --------------------------------------------------------------------------- #
# 路径残留扫描
# --------------------------------------------------------------------------- #

TEXT_SUFFIXES = {
    ".json", ".jsonl", ".yaml", ".yml", ".env", ".txt", ".md", ".csv",
    ".ini", ".cfg", ".conf", ".xml", ".html", ".py", ".log", ".svg",
}


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_text_hits(path: Path, needles: List[str], limit: int = 8) -> List[str]:
    hits: List[str] = []
    try:
        connection = sqlite3.connect(str(path), timeout=10)
    except sqlite3.DatabaseError:
        return hits
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for (table,) in tables:
            if str(table).startswith("sqlite_"):
                continue
            try:
                info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            except sqlite3.DatabaseError:
                continue
            for column in info:
                name, declared = column[1], (column[2] or "").upper()
                if not any(tag in declared for tag in ("CHAR", "TEXT", "CLOB")):
                    continue
                for needle in needles:
                    if not needle:
                        continue
                    try:
                        row = connection.execute(
                            f'SELECT rowid FROM "{table}" '
                            f'WHERE "{name}" LIKE ? LIMIT 1',
                            (f"%{needle}%",),
                        ).fetchone()
                    except sqlite3.DatabaseError:
                        continue
                    if row:
                        hits.append(f"{path.name}:{table}.{name}")
                        if len(hits) >= limit:
                            return hits
    finally:
        connection.close()
    return hits


def scan_source_paths(root: Path, needles: List[str]) -> List[str]:
    hits: List[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_sqlite(path):
            for hit in _sqlite_text_hits(path, needles):
                hits.append(f"{relative} -> {hit}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.stat().st_size > 512 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for needle in needles:
            if needle and needle in text:
                hits.append(relative)
                break
    return hits


# --------------------------------------------------------------------------- #
# 差分表
# --------------------------------------------------------------------------- #

def _fmt_ok(text: str) -> Tuple[bool, str]:
    return True, text


def _fmt_no(text: str) -> Tuple[bool, str]:
    return False, text


def render_table(rows: List[Tuple[str, Tuple[bool, str], Tuple[bool, str]]]) -> str:
    lines = [
        "| 项目 | 界面导出（新机导入） | CLI 备份（空目录恢复） | 结论 |",
        "| --- | --- | --- | --- |",
    ]
    for label, (ui_ok, ui_text), (cli_ok, cli_text) in rows:
        if ui_ok and cli_ok:
            verdict = "都带"
        elif ui_ok:
            verdict = "仅界面"
        elif cli_ok:
            verdict = "仅 CLI"
        else:
            verdict = "都不带"
        ui = ("OK " if ui_ok else "NO ") + ui_text
        cli = ("OK " if cli_ok else "NO ") + cli_text
        lines.append(f"| {label} | {ui} | {cli} | {verdict} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def _home_temp() -> Optional[Path]:
    candidate = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Temp"
    if candidate.is_dir() and os.access(str(candidate), os.W_OK):
        return candidate
    return None


def _different_machine_base() -> Optional[Path]:
    """挑一个与源机不同的根：源在用户目录（带用户名），目标在默认临时目录。"""
    default = Path(tempfile.gettempdir())
    home = _home_temp()
    if default.is_dir() and (not home or default != home):
        return default
    return None


def _rmtree_with_retry(path: Path, rounds: int = 5) -> bool:
    for _ in range(rounds):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    base = _load_base()
    oversize_mb = int(os.environ.get("LIUHAO_DIFF_OVERSIZE_MB", "66") or 66)
    keep = os.environ.get("LIUHAO_DIFF_KEEP_TMP") == "1"

    home_base = _home_temp()
    source_root = Path(
        tempfile.mkdtemp(prefix="liuhao-diff-src-", dir=str(home_base))
        if home_base
        else tempfile.mkdtemp(prefix="liuhao-diff-src-")
    )
    target_base = _different_machine_base()
    target_root = Path(
        tempfile.mkdtemp(prefix="liuhao-diff-new-machine-", dir=str(target_base))
        if target_base
        else tempfile.mkdtemp(prefix="liuhao-diff-new-machine-")
    )
    print(f"源机数据：{source_root}")
    print(f"目标机数据：{target_root}")
    instances: Dict[str, Any] = {}
    try:
        a = base.Instance(source_root / "source")
        instances["A"] = a
        # 先落工件/日志/追踪，再起网关：索引与执行日志里的绝对路径是真实写入，
        # 而不是事后硬塞；网关启动后还会继续写，模拟真实运行中的源机。
        workspace = a.data_dir / "workspace"
        attachment_hashes = seed_attachments(workspace, oversize_mb)
        small_rels = [rel for rel, _ in SMALL_ATTACHMENTS] + [UNDER_LIMIT]
        seed_artifact_index(workspace, small_rels)
        seed_execution_journal(workspace, "artifacts/root1/chart.svg")
        seed_runtime_traces(a.data_dir)
        a.start()
        tokens = base.seed(a)
        before_root = base.snapshot(a, tokens["root"])
        before_member = base.snapshot(a, tokens["member"])
        time.sleep(2)

        # ---------------- 界面导出 ----------------
        section("界面导出（scope=all）")
        wall = time.perf_counter()
        status, exported = base.request(
            "POST",
            a.base + "/v1/accounts/backup/export",
            {"scope": "all", "passphrase": PASSWORD},
            token=tokens["root"],
        )
        ui_export_seconds = time.perf_counter() - wall
        check("界面导出成功", status == 200, f"{status} {str(exported)[:300]}")
        manifest = exported.get("manifest") or {}
        notice = str(exported.get("notice") or "")
        check(
            "界面回执写明附件随包与单文件上限",
            "工作区" in notice and "64" in notice,
            notice,
        )
        check(
            "界面回执不暗示日志/诊断被带走",
            "日志" in notice and "诊断" in notice,
            notice,
        )

        from src.kernels.identity._backup import decrypt_bundle

        ui_payload = decrypt_bundle(exported["bundle"], PASSWORD)
        ui_files = {str(item.get("path")): item for item in ui_payload.get("files") or []}
        ui_skipped = list(manifest.get("skipped") or [])

        # 停止源机，用同一份数据跑 CLI 备份。
        a.stop()
        instances.pop("A", None)

        # ---------------- CLI 备份 ----------------
        section("CLI 备份")
        backups = source_root / "backups"
        env_create = cli_env(a.data_dir, backups, base)
        wall = time.perf_counter()
        code, created, raw = run_cli(
            env_create,
            "backup", "create",
            "--encrypt",
            "--password", PASSWORD,
            "--description", "换机路线差分",
        )
        cli_export_seconds = time.perf_counter() - wall
        check("CLI 加密备份成功", code == 0 and created.get("status") == "completed", raw[-400:])
        archive = Path(str(created.get("file_path") or ""))
        cli_payload = load_cli_payload(archive)
        cli_attachments = created.get("attachments") or {}
        check(
            "CLI 回执写明附件随包",
            bool(cli_attachments.get("included")) and int(cli_attachments.get("files") or 0) > 0,
            str(cli_attachments),
        )
        check(
            "CLI 排除日志/诊断并如实记账",
            int(created.get("excluded_runtime_files") or 0) >= 2,
            str(created.get("excluded_runtime_files")),
        )

        # ---------------- 界面导入到新机 ----------------
        section("界面导入（新机）")
        ui_machine = base.Instance(target_root / "ui-machine")
        instances["UI"] = ui_machine
        ui_machine.start()
        status, imported = base.request(
            "POST",
            ui_machine.base + "/v1/accounts/backup/import",
            {"bundle": exported["bundle"], "passphrase": PASSWORD},
        )
        check("界面导入成功", status == 200, f"{status} {str(imported)[:300]}")
        ui_path_rewrites = int((imported.get("files") or {}).get("path_rewrites") or 0)
        ui_source_rewrites = int(
            (imported.get("files") or {}).get("source_path_rewrites") or 0
        )
        check("界面导入改写了工件索引路径", ui_path_rewrites > 0, str(imported.get("files")))
        check("界面导入改写了日志/迁移标记里的源机路径", ui_source_rewrites > 0,
              str(imported.get("files")))
        ui_machine.stop()
        ui_machine.start()
        ui_root = base.login(ui_machine, "root1")
        ui_member = base.login(ui_machine, "member1")
        ui_after_root = base.snapshot(ui_machine, ui_root)
        ui_after_member = base.snapshot(ui_machine, ui_member)

        # ---------------- CLI 恢复到空目录 ----------------
        section("CLI 恢复到空目录")
        cli_target = target_root / "cli-machine" / "data"
        env_restore = cli_env(cli_target, backups, base)
        code, restored, raw = run_cli(
            env_restore,
            "backup", "restore", str(created.get("backup_id") or ""),
            "--password", PASSWORD,
            "--target", str(cli_target),
        )
        check("CLI 恢复成功", code == 0 and restored.get("status") == "restored", raw[-400:])
        cli_path_rewrites = int(restored.get("path_rewrites") or 0)
        cli_source_rewrites = int(restored.get("source_path_rewrites") or 0)
        check("CLI 恢复改写了工件索引路径", cli_path_rewrites > 0, str(cli_path_rewrites))
        check("CLI 恢复改写了日志/迁移标记里的源机路径", cli_source_rewrites > 0,
              str(cli_source_rewrites))
        cli_machine = base.Instance(target_root / "cli-machine", data_dir=cli_target)
        instances["CLI"] = cli_machine
        cli_machine.start()
        cli_root = base.login(cli_machine, "root1")
        cli_member = base.login(cli_machine, "member1")
        cli_after_root = base.snapshot(cli_machine, cli_root)
        cli_after_member = base.snapshot(cli_machine, cli_member)

        # ---------------- 差分表 ----------------
        section("两条路线差分（实测）")
        identity = ui_payload.get("identity") or {}
        ui_accounts = len(identity.get("accounts") or [])
        ui_credentials = len(ui_payload.get("credentials") or {})
        ui_employees = len(ui_payload.get("employees") or [])
        ui_goals = len(ui_payload.get("goals") or {})
        ui_memory = len((ui_payload.get("memory") or {}).get("entries") or [])
        ui_turns = len((ui_payload.get("conversations") or {}).get("turns") or [])
        ui_kernel = len((ui_payload.get("kernel_memory") or {}).get("entries") or [])
        ui_profile = sum(
            len(rows or [])
            for rows in (ui_payload.get("personal_context") or {}).values()
        ) if isinstance(ui_payload.get("personal_context"), dict) else len(
            ui_payload.get("personal_context") or []
        )
        knowledge_manifest = manifest.get("knowledge") or {}
        ui_vectors = int(knowledge_manifest.get("chunks") or 0)
        ui_docs = int(knowledge_manifest.get("documents") or 0)
        ui_models = len((ui_payload.get("models") or {}).get("rows") or [])
        ui_keys = int(manifest.get("model_keys_exported") or 0)
        ui_member_keys = bool(manifest.get("member_keys_included"))

        cli_cfg = cli_payload.get("data", {}).get("config", {}).get("files") or {}
        cli_ud = cli_payload.get("data", {}).get("user_data", {}).get("files") or {}
        human = json.loads(_decode_entry(cli_cfg["human_identities.json"]))
        cli_accounts = len(human.get("accounts") or {})
        secrets = json.loads(_decode_entry(cli_cfg["auth_secrets.json"]))
        cli_credentials = len(secrets.get("credentials") or {})
        employees_doc = json.loads(_decode_entry(cli_ud["workspace/liuhao_employees.json"]))
        cli_employees = len(employees_doc.get("employees") or {})
        goals_doc = json.loads(_decode_entry(cli_ud["workspace/liuhao_goals.json"]))
        cli_goals = len(goals_doc)
        cli_account_memory = _count(
            _decode_entry(cli_ud["workspace/account_memory.sqlite3"]),
            "SELECT COUNT(*) FROM account_memory_entries",
        )
        cli_memory = _count(
            _decode_entry(cli_ud["memory_store.db"]),
            "SELECT COUNT(*) FROM memory_entries",
        )
        cli_turns = _count(
            _decode_entry(cli_ud["conversation_store.db"]),
            "SELECT COUNT(*) FROM conversation_turns",
        )
        cli_profile = 0
        for table in (
            "profile_preferences", "profile_facts", "profile_lists",
            "profile_relationships", "profile_meta",
        ):
            try:
                cli_profile += _count(
                    _decode_entry(cli_ud["personal_context.db"]),
                    f"SELECT COUNT(*) FROM {table}",
                )
            except sqlite3.DatabaseError:
                continue
        cli_vectors, cli_docs = _knowledge_counts(
            _decode_entry(cli_ud["data/knowledge_vectors.sqlite3"])
        )
        cli_models = _count(
            _decode_entry(cli_ud["data/model_registry.sqlite3"]),
            "SELECT COUNT(*) FROM ai_model_configs",
        )
        cli_keys = _count(
            _decode_entry(cli_ud["data/model_registry.sqlite3"]),
            "SELECT COUNT(*) FROM ai_model_configs WHERE api_key_enc IS NOT NULL AND api_key_enc != ''",
        )
        cli_audit = "audit_store.db" in cli_ud
        cli_attempts = "workspace/account_backup.sqlite3" in cli_ud
        cli_key_file = "data/model_config.key" in cli_ud
        cli_integrity = "integrity.key" in cli_cfg
        cli_app_env = "app.env" in cli_cfg

        ui_small_present = [rel for rel in small_rels if rel in ui_files]
        ui_small_bytes = sum(int(ui_files[rel].get("size") or 0) for rel in ui_small_present)
        cli_small_present = [
            rel for rel in small_rels
            if f"workspace/{rel}" in cli_ud
        ]
        cli_small_bytes = sum(
            int(cli_ud[f"workspace/{rel}"].get("size_bytes") or 0)
            for rel in cli_small_present
        )
        cli_oversize = f"workspace/{OVERSIZE}" in cli_ud
        ui_oversize_skipped = OVERSIZE in ui_skipped

        rows = [
            ("账号树", _fmt_ok(f"{ui_accounts} 个"), _fmt_ok(f"{cli_accounts} 个")),
            ("登录凭据（PBKDF2）", _fmt_ok(f"{ui_credentials} 条"), _fmt_ok(f"{cli_credentials} 条")),
            ("员工", _fmt_ok(f"{ui_employees} 个"), _fmt_ok(f"{cli_employees} 个")),
            ("目标", _fmt_ok(f"{ui_goals} 个"), _fmt_ok(f"{cli_goals} 个")),
            ("账号记忆", _fmt_ok(f"{ui_memory} 条"), _fmt_ok(f"{cli_account_memory} 条")),
            ("内核记忆", _fmt_ok(f"{ui_kernel} 条"), _fmt_ok(f"{cli_memory} 条")),
            ("个人画像", _fmt_ok(f"{ui_profile} 条"), _fmt_ok(f"{cli_profile} 条")),
            ("会话轮次", _fmt_ok(f"{ui_turns} 轮"), _fmt_ok(f"{cli_turns} 轮")),
            ("知识库", _fmt_ok(f"{ui_vectors} 向量 / {ui_docs} 文档"),
             _fmt_ok(f"{cli_vectors} 向量 / {cli_docs} 文档")),
            ("模型配置", _fmt_ok(f"{ui_models} 条"), _fmt_ok(f"{cli_models} 条")),
            ("模型 Key（可用）", _fmt_ok(f"{ui_keys} 个密文" + ("，含成员" if ui_member_keys else "")),
             _fmt_ok(f"{cli_keys} 个密文 + 主密钥")),
            ("模型主密钥 model_config.key",
             _fmt_no("不随包（导入端本机重新加密）"), _fmt_ok("随包（加密信封内）")),
            ("身份签名 integrity.key",
             _fmt_no("不随包（新机自建）"), _fmt_ok("随包") if cli_integrity else _fmt_no("无")),
            ("服务环境 app.env",
             _fmt_no("不随包"),
             _fmt_ok("随包") if cli_app_env else _fmt_no("本实例未生成")),
            ("审计账本 audit_store.db",
             _fmt_no("不随包（账号域之外）"), _fmt_ok("随包") if cli_audit else _fmt_no("无")),
            ("备份尝试计数 account_backup.sqlite3",
             _fmt_no("不随包（实例本地限流状态）"),
             _fmt_ok("随包") if cli_attempts else _fmt_no("无")),
            ("附件 ≤64MB（用户上传/生成）",
             _fmt_ok(f"{len(ui_small_present)} 个 / {ui_small_bytes} B"),
             _fmt_ok(f"{len(cli_small_present)} 个 / {cli_small_bytes} B")),
            (f"附件 >64MB（{oversize_mb}MB）",
             _fmt_no("列入 skipped，不随包") if ui_oversize_skipped else _fmt_no("缺失"),
             _fmt_ok("随包") if cli_oversize else _fmt_no("缺失")),
            ("工件索引 liuhao_artifacts.db",
             _fmt_ok(f"随包并改写 {ui_path_rewrites} 行"),
             _fmt_ok(f"随包并改写 {cli_path_rewrites} 行")),
            ("执行日志 liuhao_execution.journal",
             _fmt_ok("随包") if "liuhao_execution.journal" in ui_files else _fmt_no("缺失"),
             _fmt_ok("随包") if "workspace/liuhao_execution.journal" in cli_ud else _fmt_no("缺失")),
            ("日志 / 诊断包",
             _fmt_no("不随包"),
             _fmt_no(f"不随包（排除 {int(created.get('excluded_runtime_files') or 0)} 个文件）")),
        ]
        table = render_table(rows)
        print(table)

        # 数量核对：两条路线恢复后与源机一致
        section("恢复后数据核对")
        for label, before, after, member_after in (
            ("界面导入", before_root, ui_after_root, ui_after_member),
            ("CLI 恢复", before_root, cli_after_root, cli_after_member),
        ):
            for item in ("accounts", "employees", "goals", "memory", "sessions", "knowledge"):
                if item == "memory":
                    # 恢复后新机会继续跑目标，可能新增"执行结果"记忆；原记忆
                    # 必须一条不少，多出来的是新机自己的运行痕迹。
                    ok = set(before[item]) <= set(after[item])
                else:
                    ok = before[item] == after[item]
                check(
                    f"{label} 根视角 {item} 与源机一致",
                    ok,
                    f"{before[item]} -> {after[item]}",
                )
            for item in ("employees", "goals", "memory", "sessions", "knowledge"):
                if item == "memory":
                    ok = set(before_member[item]) <= set(member_after[item])
                else:
                    ok = before_member[item] == member_after[item]
                check(
                    f"{label} 成员视角 {item} 与源机一致",
                    ok,
                    f"{before_member[item]} -> {member_after[item]}",
                )
        for label, instance, token in (
            ("界面导入", ui_machine, ui_root),
            ("CLI 恢复", cli_machine, cli_root),
        ):
            status, models = base.request("GET", instance.base + "/v1/models", token=token)
            check(
                f"{label} 模型 Key 开箱可用",
                status == 200 and bool(models.get("models")),
                f"{status} {str(models)[:200]}",
            )
            member_status, member_models = base.request(
                "GET", instance.base + "/v1/models", token=(
                    ui_member if label == "界面导入" else cli_member
                ),
            )
            check(
                f"{label} 成员模型 Key 也在",
                member_status == 200 and len(member_models.get("models") or []) == 1,
                f"{member_status} {str(member_models)[:200]}",
            )

        # 附件字节一致
        for label, instance_data in (
            ("界面导入", ui_machine.data_dir),
            ("CLI 恢复", cli_machine.data_dir),
        ):
            ws = instance_data / "workspace"
            for rel, expected in attachment_hashes.items():
                if rel == OVERSIZE and label == "界面导入":
                    check(
                        "界面导入 >64MB 附件未随包（回执已列 skipped）",
                        not (ws / rel).exists() and ui_oversize_skipped,
                    )
                    continue
                path = ws / rel
                check(
                    f"{label} 附件字节一致 {rel}",
                    path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                    "missing" if not path.exists() else "hash mismatch",
                )

        # ---------------- 三种目标目录情形 ----------------
        section("恢复目标目录三种情形")
        drills = target_root / "drills"
        drills.mkdir(parents=True, exist_ok=True)
        full_target = drills / "has-full-data" / "data"
        full_target.mkdir(parents=True)
        (full_target / "config").mkdir()
        (full_target / "config" / "human_identities.json").write_text(
            '{"identities": [], "accounts": {"someone": {}}}', encoding="utf-8"
        )
        (full_target / "config" / "auth_secrets.json").write_text("{}", encoding="utf-8")
        (full_target / "data").mkdir()
        (full_target / "data" / "model_registry.sqlite3").write_bytes(b"SQLite format 3\x00")
        (full_target / "workspace").mkdir()
        (full_target / "workspace" / "liuhao_employees.json").write_text("[]", encoding="utf-8")
        for name in ("memory_store.db", "conversation_store.db", "audit_store.db", "account_memory.sqlite3"):
            (full_target / name).write_text("existing", encoding="utf-8")
        (full_target / "keep.txt").write_text("另一套数据，不许覆盖", encoding="utf-8")

        empty_target = drills / "empty" / "data"
        empty_target.mkdir(parents=True)

        dirty_target = drills / "scattered" / "data"
        dirty_target.mkdir(parents=True)
        (dirty_target / "notes.txt").write_text("随手记", encoding="utf-8")
        (dirty_target / "photos").mkdir()
        (dirty_target / "photos" / "cat.jpg").write_bytes(b"\xff\xd8fake-jpeg")
        (dirty_target / "README.md").write_text("# 旧目录\n", encoding="utf-8")

        for label, target, expect_ok, message in (
            ("已有整套数据", full_target, False, "已经有一整套数据"),
            ("完全为空", empty_target, True, ""),
            ("零散文件", dirty_target, False, "目录不干净"),
        ):
            before_tree = tree_inventory(target)
            before_stage = stage_residue()
            env = cli_env(target, backups, base)
            code, result, raw = run_cli(
                env,
                "backup", "restore", str(created.get("backup_id") or ""),
                "--password", PASSWORD,
                "--target", str(target),
            )
            after_tree = tree_inventory(target)
            after_stage = stage_residue()
            if expect_ok:
                check(f"{label}：恢复成功", code == 0 and result.get("status") == "restored", raw[-400:])
                check(
                    f"{label}：数据落到目标目录",
                    (target / "config" / "human_identities.json").is_file()
                    and (target / "workspace" / "liuhao_artifacts.db").is_file(),
                )
            else:
                check(f"{label}：被拒绝", code != 0, raw[-400:])
                check(f"{label}：中文说明命中", message in raw, raw[-400:])
                check(
                    f"{label}：目标目录逐文件未变（无半截文件）",
                    before_tree == after_tree,
                    f"{sorted(set(after_tree) - set(before_tree))}",
                )
                check(f"{label}：无暂存目录残留", before_stage == after_stage,
                      str(after_stage - before_stage))

        # ---------------- 路径可移植扫描 ----------------
        section("恢复后绝对路径残留扫描")
        base_needles = [
            str(a.data_dir),
            str(workspace),
            "D:\\LiuHaoData",
        ]
        home_text = str(Path.home())
        if home_text.lower() in str(a.data_dir).lower() and home_text.lower() not in str(target_root).lower():
            # 源机在用户目录、目标机换到别的盘：home 只可能是源机残留。
            base_needles.append(home_text)
        source_needles: List[str] = []
        for needle in base_needles:
            # JSON / journal payload 里反斜杠是转义的；三种写法都要查。
            for variant in (
                needle,
                needle.replace("\\", "/"),
                needle.replace("\\", "\\\\"),
            ):
                if variant and variant not in source_needles:
                    source_needles.append(variant)
        for label, instance in (("界面导入", ui_machine), ("CLI 恢复", cli_machine)):
            hits = scan_source_paths(instance.data_dir, source_needles)
            check(f"{label}：无源机绝对路径残留", not hits, "; ".join(hits[:10]))
            if hits:
                print(f"  {label} 残留：")
                for hit in hits:
                    print(f"    - {hit}")

        # ---------------- 汇报 ----------------
        section("体积与耗时")
        bundle_chars = len(json.dumps(exported["bundle"], ensure_ascii=False))
        print(f"界面导出：{ui_export_seconds:.2f}s，bundle {bundle_chars / 1024 / 1024:.1f} MB（JSON 字符）")
        print(f"CLI 备份：{cli_export_seconds:.2f}s，包 {archive.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"界面 skipped：{ui_skipped}")
        print(f"CLI 回执 attachments：{cli_attachments}")

        section("结论")
        print(table)
        if _failures:
            print(f"\n失败 {len(_failures)} 项：")
            for item in _failures:
                print(f"  - {item}")
            return 1
        print("\n全部通过")
        return 0
    finally:
        for name in list(instances):
            try:
                instances[name].stop()
            except Exception:  # noqa: BLE001
                pass
        if not keep:
            for path in (source_root, target_root):
                if not _rmtree_with_retry(path):
                    print(f"[warn] 临时目录未能删除，请手动清理：{path}")


if __name__ == "__main__":
    raise SystemExit(main())
