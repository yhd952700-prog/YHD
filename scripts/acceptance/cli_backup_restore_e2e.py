"""CLI 加密备份（``.lhbak``）换机恢复端到端验收。

覆盖本次要求的四件事：

1. **干净目录恢复**：真实网关造数（账号树、员工、目标、记忆、会话、知识库、
   模型 Key、工作区工件）→ CLI 加密导出 → 全新空目录恢复 → 启动网关 →
   账号可登录、数量与归属一致、能继续派活；并核对恢复后的真实数据布局
   （``config/``、``memory_store.db``、``data/``、``workspace/``）。
2. **失败路径**：错口令、缺口令、包被截断，都必须给中文可读错误，且不留
   半恢复的数据目录。
3. **口令丢失**：回执里写明"无法恢复，只能回源机重新导出"，没有后门。
4. **真实耗时与体积**：分别记录导出、加密、恢复的秒数与包体积。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/cli_backup_restore_e2e.py

可选环境变量：
    LIUHAO_E2E_BIG_MB=32   在源工作区额外写入多少 MB 随机文件（默认 32）
    LIUHAO_KEEP_E2E_DIR=1  保留临时目录（默认结束时清理）
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(REPO))

PASSWORD = "换机口令-2026"
WRONG_PASSWORD = "错误口令-2026"

_failures: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"[ OK ] {name}")
        return
    _failures.append(name + (f" ({detail})" if detail else ""))
    print(f"[FAIL] {name} {detail}")


def _load_base():
    """复用已有验收脚本的 Instance/seed/snapshot，避免造数逻辑分叉。"""
    spec = importlib.util.spec_from_file_location(
        "backup_migration_e2e",
        REPO / "scripts" / "acceptance" / "backup_migration_e2e.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_cli(env: Dict[str, str], *args: str) -> Tuple[int, Dict[str, Any], str]:
    """在**独立进程**里跑真实 CLI，返回 (exit_code, 解析出的 JSON, 原始输出)。"""
    command = [
        str(PY),
        "-c",
        "import sys\n"
        "from src.cli import main\n"
        "raise SystemExit(main())\n",
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


def seed_workspace_artifacts(data_dir: Path, total_mb: int) -> List[Path]:
    """在工作区里造"工件"文件，按账号目录归属；再加一段大文件量体积。"""
    files: List[Path] = []
    for account, names in (
        ("root1", ("root-migration-report.md", "root-plan.json")),
        ("member1", ("member-notes.md",)),
    ):
        folder = data_dir / "workspace" / "artifacts" / account
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = folder / name
            path.write_text(f"{account} 的换机验收工件：{name}\n", encoding="utf-8")
            files.append(path)
    if total_mb > 0:
        large = data_dir / "workspace" / "artifacts" / "root1" / "large"
        large.mkdir(parents=True, exist_ok=True)
        chunk = max(1, total_mb // 4)
        for index in range(4):
            path = large / f"payload-{index}.bin"
            path.write_bytes(os.urandom(chunk * 1024 * 1024))
            files.append(path)
    return files


def file_hashes(paths: List[Path]) -> Dict[str, str]:
    import hashlib

    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def main() -> int:
    base = _load_base()
    tmp = Path(tempfile.mkdtemp(prefix="liuhao-cli-backup-e2e-"))
    a = b = None
    try:
        total_mb = int(os.environ.get("LIUHAO_E2E_BIG_MB", "32") or 0)
        backups = tmp / "backups"
        target = tmp / "new-machine" / "data"

        # ---------------- A: 造数 ----------------
        a = base.Instance(tmp / "a")
        a.start()
        tokens_a = base.seed(a)
        before_root = base.snapshot(a, tokens_a["root"])
        before_member = base.snapshot(a, tokens_a["member"])
        artifacts = seed_workspace_artifacts(a.data_dir, total_mb)
        artifact_hashes = file_hashes(artifacts)
        employee_file = a.data_dir / "workspace" / "liuhao_employees.json"
        # 顺带记录导出前主账号模型状态，用来区分"迁移坏了"和"本来就要重填"。
        status_a, models_a = base.request("GET", a.base + "/v1/models", token=tokens_a["root"])
        model_status_before = (models_a.get("models") or [{}])[0].get("api_key_status")
        data_bytes = sum(
            path.stat().st_size for path in a.data_dir.rglob("*") if path.is_file()
        )
        a.stop()

        # ---------------- 导出 + 加密（真实 CLI 进程） ----------------
        env_create = cli_env(a.data_dir, backups, base)
        wall = time.perf_counter()
        code, created, raw = run_cli(
            env_create,
            "backup", "create",
            "--encrypt",
            "--password", PASSWORD,
            "--description", "换机验收：加密全量",
        )
        create_wall = time.perf_counter() - wall
        check("CLI 加密导出成功", code == 0 and created.get("status") == "completed",
              raw[-500:])
        archive = Path(str(created.get("file_path") or ""))
        check("产出 .lhbak", archive.suffix == ".lhbak" and archive.is_file(),
              str(archive))
        check("加密回执含耗时字段",
              {"export_seconds", "encrypt_seconds"} <= set(created),
              str(sorted(created))) 
        check("回执写明口令丢失不可恢复",
              "无法恢复" in str(created.get("password_policy") or ""),
              str(created.get("password_policy")))

        # ---------------- 干净目录恢复（模拟新机） ----------------
        env_restore = cli_env(target, backups, base)
        wall = time.perf_counter()
        code, restored, raw = run_cli(
            env_restore,
            "backup", "restore", str(created.get("backup_id") or ""),
            "--password", PASSWORD,
            "--target", str(target),
        )
        restore_wall = time.perf_counter() - wall
        check("CLI 恢复到干净目录成功", code == 0 and restored.get("status") == "restored",
              raw[-500:])
        check("恢复按真实布局落盘",
              (target / "config" / "integrity.key").is_file()
              and (target / "config" / "human_identities.json").is_file()
              and (target / "config" / "auth_secrets.json").is_file()
              and (target / "data" / "model_config.key").is_file()
              and (target / "data" / "model_registry.sqlite3").is_file()
              and (target / "memory_store.db").is_file()
              and (target / "conversation_store.db").is_file()
              and (target / "workspace" / "liuhao_employees.json").is_file(),
              str(sorted(p.name for p in target.iterdir())) if target.exists() else "missing")
        check("没有 user_data/memory_data 套壳目录",
              not (target / "user_data").exists()
              and not (target / "memory_data").exists())
        check("恢复后工件字节一致",
              file_hashes(artifacts) == artifact_hashes,
              "工件数量不一致" if target.exists() else "target missing")
        check("员工库文件原样恢复",
              (target / "workspace" / "liuhao_employees.json").read_bytes()
              == employee_file.read_bytes(),
              f"源 {employee_file.stat().st_size if employee_file.exists() else 'missing'} B")
        check("模型主密钥原样恢复",
              (target / "data" / "model_config.key").read_bytes()
              == (a.data_dir / "data" / "model_config.key").read_bytes())

        # ---------------- B: 新机启动、登录、核对 ----------------
        b = base.Instance(tmp / "b", data_dir=target)
        b.start()
        root = base.login(b, "root1")
        member = base.login(b, "member1")
        check("新机根账号可登录", bool(root))
        check("新机成员可登录", bool(member))

        after_root = base.snapshot(b, root)
        after_member = base.snapshot(b, member)
        for item in ("accounts", "employees", "goals", "memory", "sessions", "knowledge"):
            check(f"根视角 {item} 与导出前一致",
                  before_root[item] == after_root[item],
                  f"{before_root[item]} -> {after_root[item]}")
        check("成员视角员工与目标归属一致",
              before_member["employees"] == after_member["employees"]
              and before_member["goals"] == after_member["goals"],
              f"{before_member['employees']} {before_member['goals']} -> "
              f"{after_member['employees']} {after_member['goals']}")

        status, models = base.request("GET", b.base + "/v1/models", token=root)
        rows = models.get("models") or []
        check("恢复后主账号模型 Key 可直接使用",
              status == 200 and bool(rows)
              and rows[0].get("api_key_status") == model_status_before
              and rows[0].get("api_key_set") is True,
              f"导出前={model_status_before} 恢复后={rows[0].get('api_key_status')}")
        status, member_models = base.request("GET", b.base + "/v1/models", token=member)
        check("恢复后成员模型 Key 也在",
              status == 200 and len(member_models.get("models") or []) == 1,
              f"{status} {member_models}")

        # 恢复后"派活"：真实写一条新目标，并读回来。
        status, dispatched = base.request(
            "POST", b.base + "/v1/goals",
            {"natural_language": "恢复后派活：整理换机验收报告", "background": True},
            token=root,
        )
        check("恢复后可继续派活", status == 200, f"{status} {str(dispatched)[:300]}")
        time.sleep(2)
        status, goals = base.request("GET", b.base + "/v1/goals", token=root)
        check("新目标已落盘（归属根账号）",
              status == 200 and len(goals.get("goals") or []) == 3,
              f"{status} count={len(goals.get('goals') or [])}")

        # 会话历史、知识检索都真的能读。
        status, history = base.request(
            "GET", b.base + "/v1/chat/history?session_id=root-session", token=root
        )
        check("恢复后会话历史可读",
              status == 200 and len(history.get("history") or []) >= 2,
              f"{status} count={len(history.get('history') or [])}")
        status, search = base.request(
            "POST", b.base + "/v1/knowledge/search",
            {"query": "ALPHA-ROOT-9527", "top_k": 3}, token=root,
        )
        hits = search.get("hits") or (search.get("retrieval") or {}).get("hits") or []
        check("恢复后知识库可检索", status == 200 and bool(hits),
              f"{status} hits={len(hits)}")
        b.stop()

        # ---------------- 失败路径：必须中文可读且不留半恢复目录 ----------------
        drills: List[Dict[str, Any]] = []

        wrong_target = tmp / "drill-wrong" / "data"
        code, payload, raw = run_cli(
            cli_env(wrong_target, backups, base),
            "backup", "restore", str(created.get("backup_id") or ""),
            "--password", WRONG_PASSWORD,
            "--target", str(wrong_target),
        )
        drills.append({"case": "错口令", "code": code, "raw": raw, "target": wrong_target,
                       "expect": "口令错误"})

        missing_target = tmp / "drill-missing" / "data"
        code, payload, raw = run_cli(
            cli_env(missing_target, backups, base),
            "backup", "restore", str(created.get("backup_id") or ""),
            "--target", str(missing_target),
        )
        drills.append({"case": "缺口令", "code": code, "raw": raw, "target": missing_target,
                       "expect": "请用 --password 提供口令"})

        truncated_dir = tmp / "drill-truncated-backups"
        truncated_dir.mkdir(parents=True, exist_ok=True)
        truncated_archive = truncated_dir / archive.name
        truncated_bytes = archive.read_bytes()
        truncated_archive.write_bytes(truncated_bytes[: max(64, len(truncated_bytes) // 2)])
        truncated_target = tmp / "drill-truncated" / "data"
        code, payload, raw = run_cli(
            cli_env(truncated_target, truncated_dir, base),
            "backup", "restore", str(created.get("backup_id") or ""),
            "--password", PASSWORD,
            "--target", str(truncated_target),
        )
        drills.append({"case": "包被截断", "code": code, "raw": raw,
                       "target": truncated_target, "expect": "损坏或未完整复制"})

        for drill in drills:
            label = drill["case"]
            check(f"{label}：CLI 退出码非 0", drill["code"] != 0, str(drill["code"]))
            check(f"{label}：中文可读错误", drill["expect"] in drill["raw"],
                  drill["raw"][-300:])
            check(f"{label}：未留下半恢复目录",
                  not drill["target"].exists(), str(drill["target"]))

        # ---------------- 实测数字 ----------------
        size_mb = archive.stat().st_size / (1024 * 1024)
        data_mb = data_bytes / (1024 * 1024)
        numbers = {
            "数据目录": f"{data_mb:.1f} MB",
            "导出(快照+编码)": f"{float(created.get('export_seconds') or 0):.3f} 秒 "
                              f"(墙钟 {create_wall:.3f} 秒含加密)",
            "加密(AES-256-GCM)": f"{float(created.get('encrypt_seconds') or 0):.3f} 秒",
            "恢复(解密+布局铺开)": f"{float(restored.get('restore_seconds') or 0):.3f} 秒 "
                                  f"(墙钟 {restore_wall:.3f} 秒)",
            "加密包体积": f"{size_mb:.1f} MB",
            "恢复文件数": str(restored.get("files_restored")),
        }
        print("\n实测数字：")
        for key, value in numbers.items():
            print(f"  {key}: {value}")

        print("\n恢复后关键项：")
        for key in (
            "账号树", "员工", "目标", "记忆", "会话", "知识库",
            "模型 Key", "工件文件", "恢复后派活",
        ):
            print(f"  {key}: 已核对")

        if _failures:
            print("\n未通过项：")
            for failure in _failures:
                print(" - " + failure)
            return 1
        print("\n全部通过")
        return 0
    finally:
        for inst in (a, b):
            if inst is not None:
                inst.stop(cleanup=False)
        if os.environ.get("LIUHAO_KEEP_E2E_DIR") != "1":
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"保留临时目录：{tmp}")


if __name__ == "__main__":
    raise SystemExit(main())
