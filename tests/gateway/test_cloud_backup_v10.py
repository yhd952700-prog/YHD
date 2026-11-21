"""大升级第二季 V10：云备份快照（口令加密 + 滚动保留 + 恢复分发）。

闭环证据：
* 创建：快照落盘为凭据包同款加密信封——包体里找不到明文数据，
  口令不出现在快照目录任何文件里；元数据只有计数/时间/校验和；
* 保留：默认 5 份滚动，改保留份数后按新数修剪最旧的；
* 恢复演练（硬验收）：实例 A 做快照 → 全新实例 B 认领恢复 →
  账号树/工作台/目标逐项对回 → B 的数据目录真网关冷启 /v1/ready；
* 已初始化实例走合并恢复：只合不覆盖；同一份快照二次恢复被防重放拒绝；
* 口令错 / 包体损坏（校验和对不上）在进恢复路径之前就被拒绝；
* 云端上传默认关：只返回人话指引，不实际上传。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

PASSPHRASE = "cloud-backup-pass-123"
WRONG_PASSPHRASE = "cloud-backup-pass-999"


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    """每个"实例"一套独立路径；快照目录在实例之外（模拟把快照拷走）。"""
    import src.ai.conversation_store as conversation_module
    import src.kernels.identity as identity_module

    snapshots = tmp_path / "cloud-backups"
    monkeypatch.setenv("LIUHAO_CLOUD_BACKUP_DIR", str(snapshots))
    monkeypatch.delenv("LIUHAO_CLOUD_BACKUP_RETENTION", raising=False)

    def apply(prefix: str) -> dict:
        base = tmp_path / prefix
        base.mkdir(parents=True, exist_ok=True)
        values = {
            "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
            "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": f"{prefix}-integrity-key",
            "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
            "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
            "LIUHAO_ACCOUNT_BACKUP_DB": str(base / "account_backup.sqlite3"),
            "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
            "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
            "CONVERSATION_DB_PATH": str(base / "conversation_store.db"),
            "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
            "LIUHAO_DATA_DIR": str(base),
            "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
            "MEMORY_DB_PATH": str(base / "kernel_memory.db"),
            "PERSONAL_CONTEXT_DB_PATH": str(base / "personal_context.db"),
            "LIUHAO_KNOWLEDGE_DB": str(base / "knowledge_vectors.sqlite3"),
            "LIUHAO_WORKBENCH_DB": str(base / "workbench.sqlite3"),
            "LIUHAO_KNOWLEDGE_ENTERPRISE_DB": str(
                base / "knowledge_enterprise.sqlite3"
            ),
            "LIUHAO_KNOWLEDGE_MEMORY_DB": str(base / "knowledge_memory.sqlite3"),
            "LIUHAO_KNOWLEDGE_FILES_DIR": str(base / "knowledge_files"),
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(identity_module, "_global_manager", None)
        monkeypatch.setattr(
            conversation_module,
            "_conversation_store",
            conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
        )
        return values

    return apply


def _seed_accounts():
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")
    return manager


def _seed_workbench():
    from src.workbench.store import WorkbenchStore

    store = WorkbenchStore()
    group = store.create_group("root1", "根的分组")
    bot = store.create_bot(
        "root1", {"name": "根的Bot", "group_id": group["id"], "job": "管账"}
    )
    store.create_group_chat("root1", "根的群聊", [bot["id"]])
    store.set_jarvis_routines("root1", ["每天九点汇总"])
    store.create_bot("member1", {"name": "成员的Bot"})
    return {"group_id": group["id"], "bot_id": bot["id"]}


def _seed_goals(values: dict):
    workspace = Path(values["LIUHAO_WORKSPACE_ROOT"])
    workspace.mkdir(parents=True, exist_ok=True)
    goals = {
        "goal-a": {"owner_account_id": "root1", "title": "拿下首单", "status": "active"},
        "goal-b": {"owner_account_id": "root1", "title": "扩到十个客户", "status": "active"},
    }
    (workspace / "liuhao_goals.json").write_text(
        json.dumps(goals, ensure_ascii=False), encoding="utf-8"
    )


def _seed_all(values: dict):
    manager = _seed_accounts()
    workbench = _seed_workbench()
    _seed_goals(values)
    return manager, workbench


def _create(actor="root1", scope="all", is_root=True, label=""):
    from src.gateway import cloud_backup

    return cloud_backup.create_snapshot(
        actor=actor, scope=scope, passphrase=PASSPHRASE,
        is_root=is_root, label=label,
    )


# --------------------------------------------------------------------------- #
# 创建：加密落盘 + 口令零痕迹
# --------------------------------------------------------------------------- #


def test_create_snapshot_encrypted_and_passphrase_nowhere(_env, tmp_path):
    from src.gateway import cloud_backup

    values = _env("inst-a")
    _seed_all(values)
    out = _create(label="演练前")
    snap = out["snapshot"]
    assert snap["members"] == 2
    assert out["retention"] == cloud_backup.DEFAULT_RETENTION

    snap_dir = Path(tmp_path / "cloud-backups")
    files = sorted(p.name for p in snap_dir.iterdir())
    assert f"{snap['snapshot_id']}.bundle.json" in files
    assert f"{snap['snapshot_id']}.meta.json" in files

    bundle_text = (snap_dir / f"{snap['snapshot_id']}.bundle.json").read_text(
        encoding="utf-8"
    )
    # 信封本体是凭据包格式；内容是 AES-GCM 密文，明文数据一个字都找不到。
    bundle = json.loads(bundle_text)
    assert bundle["format"] == "liuhao.account.bundle.v1"
    assert bundle["cipher"] == "aes-256-gcm"
    for marker in ("根的Bot", "根的分组", "拿下首单", "member1"):
        assert marker not in bundle_text
    # 口令零痕迹：快照目录里任何文件都不许出现口令原文。
    for path in snap_dir.iterdir():
        assert PASSPHRASE not in path.read_text(encoding="utf-8")

    status = cloud_backup.cloud_backup_status()
    assert status["count"] == 1
    assert status["latest"]["snapshot_id"] == snap["snapshot_id"]
    assert status["cloud_upload"]["enabled"] is False


def test_member_scope_self_snapshot(_env):
    _seed_all(_env("inst-a"))
    out = _create(actor="member1", scope="self", is_root=False)
    assert out["snapshot"]["members"] == 1


# --------------------------------------------------------------------------- #
# 保留策略：滚动修剪
# --------------------------------------------------------------------------- #


def test_retention_prunes_oldest(_env):
    from src.gateway import cloud_backup

    _seed_all(_env("inst-a"))
    assert cloud_backup.set_retention(2) == 2
    ids = []
    for _ in range(3):
        ids.append(_create()["snapshot"]["snapshot_id"])
    metas = cloud_backup.list_snapshots()
    kept = [meta["snapshot_id"] for meta in metas]
    assert kept == [ids[2], ids[1]]  # 最新两份在，最旧一份被修剪
    snap_dir = cloud_backup.snapshots_dir()
    assert not (snap_dir / f"{ids[0]}.bundle.json").exists()
    assert not (snap_dir / f"{ids[0]}.meta.json").exists()


def test_retention_clamps_and_env_override(_env, monkeypatch):
    from src.gateway import cloud_backup

    _seed_all(_env("inst-a"))
    assert cloud_backup.set_retention(0) == 1
    assert cloud_backup.set_retention(9999) == 100
    monkeypatch.setenv("LIUHAO_CLOUD_BACKUP_RETENTION", "3")
    assert cloud_backup.effective_retention() == 3


# --------------------------------------------------------------------------- #
# 恢复：全新实例认领（硬验收的上半场）
# --------------------------------------------------------------------------- #


def test_fresh_instance_restore_drill(_env):
    from src.gateway import cloud_backup
    from src.kernels.identity import get_identity_manager

    values_a = _env("inst-a")
    _, workbench = _seed_all(values_a)
    snap = _create()["snapshot"]

    # 全新实例 B：除快照目录外什么都没有。
    values_b = _env("inst-b")
    assert get_identity_manager().account_root() is None

    result = cloud_backup.restore_snapshot(
        snapshot_id=snap["snapshot_id"], passphrase=PASSPHRASE
    )
    assert result["restore_mode"] == "fresh_claim"
    assert result["accounts"] == 2
    assert result["goals"]["restored"] == 2
    assert result["workbench"]["groups"] == 1
    assert result["workbench"]["bots"] == 2

    # 数据可读且逐项对得回 A。
    manager_b = get_identity_manager()
    assert manager_b.account_root().principal == "root1"
    assert manager_b.get_account("member1") is not None

    from src.workbench.store import WorkbenchStore

    store_b = WorkbenchStore()
    groups = store_b.list_groups(["root1", "member1"])
    assert [g["name"] for g in groups] == ["根的分组"]
    bots = store_b.list_bots(["root1"])
    assert [b["name"] for b in bots] == ["根的Bot"]
    assert bots[0]["id"] == workbench["bot_id"]
    assert [b["name"] for b in store_b.list_bots(["member1"])] == ["成员的Bot"]
    assert store_b.get_jarvis_routines("root1") == ["每天九点汇总"]

    goals_b = json.loads(
        Path(values_b["LIUHAO_WORKSPACE_ROOT"], "liuhao_goals.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(goals_b) == {"goal-a", "goal-b"}
    assert goals_b["goal-a"]["title"] == "拿下首单"


# --------------------------------------------------------------------------- #
# 恢复演练下半场：恢复后的全新目录真网关冷启 ready
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_restored_instance_gateway_cold_start_ready(_env, tmp_path):
    from src.gateway import cloud_backup

    values_a = _env("inst-a")
    _seed_all(values_a)
    snap = _create()["snapshot"]
    values_b = _env("inst-b")
    cloud_backup.restore_snapshot(
        snapshot_id=snap["snapshot_id"], passphrase=PASSPHRASE
    )

    repo = Path(__file__).resolve().parents[2]
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "LIUHAO_CONSOLE_DIST": str(repo / "console"),
        **values_b,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.gateway"],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 120
        ready = None
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                raise AssertionError(f"gateway exited early:\n{out[-3000:]}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/v1/ready", timeout=3
                ) as resp:
                    ready = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                time.sleep(1.5)
        assert ready is not None, "gateway did not become ready in 120s"
        assert ready["status"] == "ready"
        assert ready["checks"]["account_tree"]["has_root"] is True
        assert ready["checks"]["account_tree"]["total_accounts"] == 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


# --------------------------------------------------------------------------- #
# 已初始化实例：合并恢复 + 防重放
# --------------------------------------------------------------------------- #


def test_merge_restore_keeps_live_data_and_replay_rejected(_env):
    from src.gateway import cloud_backup
    from src.kernels.identity import _accounts

    values = _env("inst-a")
    _seed_all(values)
    snap = _create()["snapshot"]

    # 快照之后现场又新增了数据。
    from src.workbench.store import WorkbenchStore

    WorkbenchStore().create_bot("root1", {"name": "快照后的Bot"})

    result = cloud_backup.restore_snapshot(
        snapshot_id=snap["snapshot_id"], passphrase=PASSPHRASE, actor="root1"
    )
    assert result["restore_mode"] == "existing_account_merge"
    names = [b["name"] for b in WorkbenchStore().list_bots(["root1"])]
    assert "快照后的Bot" in names  # 现场数据没被动
    assert names.count("根的Bot") == 1  # 已存在的不重复合入

    # 同一份快照在同一实例只能成功恢复一次（凭据包防重放口径）。
    with pytest.raises(_accounts.AccountError) as excinfo:
        cloud_backup.restore_snapshot(
            snapshot_id=snap["snapshot_id"], passphrase=PASSPHRASE, actor="root1"
        )
    assert excinfo.value.code == "ACCOUNT_BACKUP_REPLAYED"


# --------------------------------------------------------------------------- #
# 失败面：口令错 / 包体损坏 / 上传默认关
# --------------------------------------------------------------------------- #


def test_wrong_passphrase_rejected(_env):
    from src.gateway import cloud_backup
    from src.kernels.identity import _accounts

    values = _env("inst-a")
    _seed_all(values)
    snap = _create()["snapshot"]
    _env("inst-b")
    with pytest.raises(_accounts.AccountError):
        cloud_backup.restore_snapshot(
            snapshot_id=snap["snapshot_id"], passphrase=WRONG_PASSPHRASE
        )


def test_corrupt_snapshot_rejected_before_restore(_env):
    from src.gateway import cloud_backup
    from src.kernels.identity import _accounts

    values = _env("inst-a")
    _seed_all(values)
    snap = _create()["snapshot"]
    bundle_path = cloud_backup.snapshots_dir() / f"{snap['snapshot_id']}.bundle.json"
    raw = bundle_path.read_bytes()
    bundle_path.write_bytes(raw[:-16] + b"0" * 16)  # 改坏尾部，校验和必对不上
    with pytest.raises(_accounts.AccountError) as excinfo:
        cloud_backup.restore_snapshot(
            snapshot_id=snap["snapshot_id"], passphrase=PASSPHRASE
        )
    assert excinfo.value.code == "CLOUD_BACKUP_SNAPSHOT_CORRUPT"


def test_upload_disabled_by_default(_env):
    from src.gateway import cloud_backup

    values = _env("inst-a")
    _seed_all(values)
    snap = _create()["snapshot"]
    out = cloud_backup.upload_snapshot(snap["snapshot_id"])
    assert out["ok"] is False
    assert out["status"] == "disabled"
    assert "云盘" in out["message"]


def test_delete_snapshot(_env):
    from src.gateway import cloud_backup

    values = _env("inst-a")
    _seed_all(values)
    snap = _create()["snapshot"]
    out = cloud_backup.delete_snapshot(actor="root1", snapshot_id=snap["snapshot_id"])
    assert out["ok"] is True
    assert cloud_backup.list_snapshots() == []
    assert cloud_backup.cloud_backup_status()["count"] == 0
