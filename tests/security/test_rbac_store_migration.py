"""RBAC 权限库跟随数据根，且旧位置迁移不丢数据、不以空库启动。

覆盖用户要求的四种组合：

* 只有旧 ``~/.liuhao/rbac.json`` → 迁移到新位置，3 个角色与 1 条角色授权都能读到，
  旧文件哈希不变；
* 只有新位置 → 正常使用，不回头建旧文件；
* 两边都有 → 以新位置为准，旧文件不删；
* 两边都没有 → 才允许初始化空库。

另加两条：迁移失败必须报错并继续读旧库；显式 ``store_path`` 完全信任、不做迁移。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import src.security.rbac as rbac_module
from src.security.rbac import (
    PermissionAction,
    RBACManager,
    ResourceType,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _permission_payload() -> dict:
    return {
        "memory:read:": {
            "resource_type": "memory",
            "action": "read",
            "target": None,
            "conditions": {},
            "description": "迁移必须保留这条授权",
        }
    }


def _role(
    role_id: str,
    name: str,
    permission: dict | None = None,
    description: str | None = None,
) -> dict:
    return {
        "id": role_id,
        "name": name,
        "description": description,
        "status": "active",
        "parent_ids": [],
        "child_ids": [],
        "custom_permissions": permission or {},
    }


def _three_roles() -> list[dict]:
    return [
        _role("role_alpha", "alpha", _permission_payload()),
        _role("role_beta", "beta"),
        _role("role_gamma", "gamma"),
    ]


def _write_store(path: Path, roles: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "saved_at": 1.0, "roles": roles}, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def store_env(monkeypatch, tmp_path):
    """数据根与旧位置都指到 tmp，绝不碰真实 ``~/.liuhao``。"""
    data = tmp_path / "data"
    legacy = tmp_path / "home" / ".liuhao" / "rbac.json"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))
    monkeypatch.setattr(rbac_module, "_legacy_store_path", lambda: legacy)
    return data, legacy


def test_only_legacy_is_migrated_with_roles_and_grants(store_env, caplog):
    data, legacy = store_env
    _write_store(legacy, _three_roles())
    legacy_hash = _sha256(legacy)
    target = data / "rbac.json"

    with caplog.at_level(logging.INFO, logger=rbac_module.__name__):
        manager = RBACManager()

    assert Path(manager.store_path) == target
    assert target.is_file()
    assert legacy.is_file(), "迁移是复制不是搬走，旧文件必须保留"
    assert _sha256(legacy) == legacy_hash, "旧文件不得被改写"
    assert not list(data.glob("*.migrating"))

    roles = sorted(role.name for role in manager.list_roles())
    assert roles == ["alpha", "beta", "gamma"]

    alpha = next(role for role in manager.list_roles() if role.name == "alpha")
    assert alpha.has_permission(ResourceType.MEMORY, PermissionAction.READ), (
        "角色授权必须随迁移保留"
    )
    assert any("已迁移" in record.getMessage() for record in caplog.records)


def test_only_new_location_is_used_without_recreating_legacy(store_env):
    data, legacy = store_env
    target = data / "rbac.json"
    _write_store(target, [_role("role_new", "new-only")])

    manager = RBACManager()

    assert Path(manager.store_path) == target
    assert [role.name for role in manager.list_roles()] == ["new-only"]
    assert not legacy.exists(), "新位置已有库时不应去创建旧位置"


def test_both_locations_prefer_new_and_keep_legacy(store_env, caplog):
    data, legacy = store_env
    _write_store(legacy, _three_roles())
    legacy_hash = _sha256(legacy)
    _write_store(data / "rbac.json", [_role("role_new", "new-only")])

    with caplog.at_level(logging.INFO, logger=rbac_module.__name__):
        manager = RBACManager()

    assert [role.name for role in manager.list_roles()] == ["new-only"]
    assert legacy.is_file(), "两边都有时不得静默删除旧文件"
    assert _sha256(legacy) == legacy_hash


def test_neither_location_starts_empty_then_initializes(store_env):
    data, legacy = store_env
    target = data / "rbac.json"

    manager = RBACManager()

    assert Path(manager.store_path) == target
    assert not target.exists(), "不该抢在首次保存之前建出空文件"
    assert not legacy.exists()

    manager.create_role(name="fresh")
    assert target.is_file()
    assert [role.name for role in manager.list_roles()] == ["fresh"]
    assert not legacy.exists()


def test_migration_failure_reports_and_reads_legacy(store_env, monkeypatch, caplog):
    data, legacy = store_env
    _write_store(legacy, _three_roles())
    legacy_hash = _sha256(legacy)
    target = data / "rbac.json"

    def _boom(*_args, **_kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(rbac_module, "_copy_and_verify", _boom)

    with caplog.at_level(logging.ERROR, logger=rbac_module.__name__):
        manager = RBACManager()

    assert Path(manager.store_path) == legacy, "迁移失败必须继续读旧库"
    assert sorted(role.name for role in manager.list_roles()) == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert not target.exists(), "迁移失败不得留下半个新库"
    assert not list(data.glob("*.migrating"))
    assert _sha256(legacy) == legacy_hash
    messages = [record.getMessage() for record in caplog.records]
    assert any("迁移失败" in message and "不会以空库启动" in message for message in messages)


def test_legacy_parent_links_load_without_reload_recursion(store_env, capsys):
    """旧库带父子关系时，加载不得重入 ``_load`` 触发 RecursionError。"""
    _data, legacy = store_env
    roles = _three_roles()
    roles[1]["parent_ids"] = ["role_alpha"]
    _write_store(legacy, roles)

    manager = RBACManager()
    manager.list_roles()

    assert "Failed to load RBAC data" not in capsys.readouterr().out
    beta = next(role for role in manager.list_roles() if role.name == "beta")
    assert beta.has_permission(ResourceType.MEMORY, PermissionAction.READ), (
        "父子关系必须保留，子角色应继承父角色授权"
    )


def test_parent_child_roles_load_without_reload_recursion(store_env, capsys, monkeypatch):
    """旧库真实父子形态的回归钉子：父 role 带子 role id、子角色继承。

    锁三件事：

    1. 构造/首次加载**不得抛 ``RecursionError``**，也不得打印
       ``Failed to load RBAC data``；
    2. ``_loaded`` 在 ``Role.from_dict`` **解析之前**就置位 —— 旧实现解析完才
       置位，而 ``Role.from_dict`` 为 ``parent_ids`` 回调 ``get_role()`` 会重入
       ``_load``，562 个带父子关系的真实角色会一路递归到栈溢出；
    3. 迁移后父子关系仍在：子角色仍继承父（及祖父）角色的授权。
    """
    data, legacy = store_env
    root = _role("role_root", "root", _permission_payload(), description="祖父角色")
    mid = _role("role_mid", "mid", description="父角色")
    leaf = _role("role_leaf", "leaf", description="子角色")
    # 真实旧库形态：父角色自带子角色 id，子角色用 parent_ids 声明继承。
    root["child_ids"] = ["role_mid"]
    mid["parent_ids"] = ["role_root"]
    mid["child_ids"] = ["role_leaf"]
    leaf["parent_ids"] = ["role_mid"]
    _write_store(legacy, [root, mid, leaf])
    legacy_hash = _sha256(legacy)

    observed: list[tuple[str, bool]] = []
    original = rbac_module.Role.from_dict.__func__

    def spy(cls, role_data, rbac_store=None):
        observed.append((role_data["id"], bool(getattr(rbac_store, "_loaded", False))))
        return original(cls, role_data, rbac_store)

    monkeypatch.setattr(rbac_module.Role, "from_dict", classmethod(spy))

    manager = RBACManager()  # 构造不得抛 RecursionError
    roles = manager.list_roles()  # 首次加载不得抛 RecursionError

    assert "Failed to load RBAC data" not in capsys.readouterr().out
    assert Path(manager.store_path) == data / "rbac.json"
    assert _sha256(legacy) == legacy_hash, "迁移只复制，旧文件不得被改写"
    assert {role.name for role in roles} == {"root", "mid", "leaf"}

    assert [role_id for role_id, _ in observed] == ["role_root", "role_mid", "role_leaf"]
    assert all(loaded for _, loaded in observed), (
        "``_loaded`` 必须在 Role.from_dict 解析之前置位，否则 parent_ids 回调"
        "会重入 _load 并递归到 RecursionError"
    )
    assert manager._loaded is True

    by_name = {role.name: role for role in roles}
    assert by_name["leaf"].parent_ids == ["role_mid"]
    assert "role_leaf" in by_name["mid"].child_ids
    assert "role_mid" in by_name["root"].child_ids
    assert by_name["mid"].has_permission(ResourceType.MEMORY, PermissionAction.READ)
    assert by_name["leaf"].has_permission(ResourceType.MEMORY, PermissionAction.READ), (
        "迁移后子角色仍应继承祖父角色的授权"
    )


def test_explicit_store_path_skips_migration(store_env, tmp_path):
    _data, legacy = store_env
    _write_store(legacy, _three_roles())
    explicit = tmp_path / "explicit" / "rbac.json"

    manager = RBACManager(store_path=str(explicit))

    assert manager.store_path == str(explicit)
    assert manager.list_roles() == []
    assert not explicit.exists()
    assert legacy.is_file()


def test_without_data_root_the_default_is_not_the_legacy_home(store_env, monkeypatch, tmp_path):
    _data, legacy = store_env
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
        expected = (tmp_path / "localapp" / "LiuHao" / "data" / "rbac.json").resolve()
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        expected = (tmp_path / "xdg" / "liuhao" / "rbac.json").resolve()

    manager = RBACManager()

    assert Path(manager.store_path) == expected
    assert Path(manager.store_path) != legacy
    assert not legacy.exists(), "未配置数据根时也不得回落到 ~/.liuhao 写入"


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_CHILD_RESOLVE = r"""
import json, logging, os, sys, time
from pathlib import Path
from src.security.rbac import RBACManager

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")

ready = Path(os.environ["RBAC_TEST_READY"])
go = Path(os.environ["RBAC_TEST_GO"])
ready.write_text("ready", encoding="utf-8")
deadline = time.time() + 20
while not go.exists():
    if time.time() > deadline:
        print("barrier timeout", file=sys.stderr)
        raise SystemExit(2)
    time.sleep(0.002)

manager = RBACManager()
roles = manager.list_roles()
print(json.dumps({
    "store_path": manager.store_path,
    "role_count": len(roles),
    "names": sorted(role.name for role in roles),
}, ensure_ascii=False))
"""

_CHILD_RESOLVE_ONCE = r"""
import json, logging, sys
from src.security.rbac import RBACManager

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
manager = RBACManager()
print(json.dumps({
    "store_path": manager.store_path,
    "role_count": len(manager.list_roles()),
}, ensure_ascii=False))
"""

_CHILD_HOLD_LOCK = r"""
import os, sys, time
from src.common.atomic_write import exclusive_file_lock

with exclusive_file_lock(os.environ["RBAC_TEST_LOCK"], description="RBAC 权限库迁移"):
    print("locked", flush=True)
    time.sleep(120)
"""


def _spawn(script: str, env: dict):
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=_PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _child_env(tmp_path: Path, data: Path, home: Path, **extra) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "LIUHAO_DATA_DIR": str(data),
            "USERPROFILE": str(home),
            "HOME": str(home),
            "PYTHONPATH": str(_PROJECT_ROOT),
        }
    )
    env.update(extra)
    return env


def _wait_for(path: Path, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while not path.exists():
        if time.time() > deadline:
            raise AssertionError(f"子进程未在 {timeout}s 内就绪：{path}")
        time.sleep(0.005)


def test_two_real_processes_migrate_exactly_once(tmp_path):
    """两个真实子进程同时解析：只许一方迁移，两方读到同一路径。"""
    home = tmp_path / "home"
    data = tmp_path / "data"
    legacy = home / ".liuhao" / "rbac.json"
    target = data / "rbac.json"

    # 旧库做大一点（~1.5MB），拉长复制窗口，确保两进程真的在锁上相遇。
    roles = [
        _role(f"role_{index}", f"role-{index}", description="x" * 1800)
        for index in range(800)
    ]
    _write_store(legacy, roles)
    legacy_hash = _sha256(legacy)

    ready_paths = [tmp_path / f"ready-{index}" for index in range(2)]
    go = tmp_path / "go"
    procs = [
        _spawn(
            _CHILD_RESOLVE,
            _child_env(
                tmp_path,
                data,
                home,
                RBAC_TEST_READY=str(ready_paths[index]),
                RBAC_TEST_GO=str(go),
            ),
        )
        for index in range(2)
    ]
    try:
        for ready in ready_paths:
            _wait_for(ready)
        go.touch()
        outputs = [proc.communicate(timeout=60) for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    for proc, (stdout, stderr) in zip(procs, outputs):
        assert proc.returncode == 0, f"子进程失败：{stderr}"
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result["store_path"] == str(target)
        assert result["role_count"] == len(roles)
        assert result["names"] == sorted(role["name"] for role in roles)

    migration_logs = sum(
        line.count("RBAC 权限库已迁移") for _stdout, err in outputs for line in err.splitlines()
    )
    assert migration_logs == 1, f"只允许一个进程执行迁移，实际日志：{outputs}"

    assert target.is_file()
    assert _sha256(target) == legacy_hash, "迁移后的库必须与旧库逐字节一致"
    assert _sha256(legacy) == legacy_hash, "旧文件不得被改动"
    assert not list(data.glob("*.migrating.*")), "不得留下任何迁移临时文件"
    assert not list(legacy.parent.glob("*.migrating.*"))


def test_lock_holder_killed_mid_migration_recovers_without_manual_cleanup(tmp_path):
    """持锁进程被强杀：操作系统释放锁，下一次迁移自动完成，无需人工删锁。"""
    home = tmp_path / "home"
    data = tmp_path / "data"
    legacy = home / ".liuhao" / "rbac.json"
    target = data / "rbac.json"
    _write_store(legacy, _three_roles())
    legacy_hash = _sha256(legacy)
    lock_path = Path(f"{target}.lock")

    holder = _spawn(
        _CHILD_HOLD_LOCK,
        _child_env(tmp_path, data, home, RBAC_TEST_LOCK=str(lock_path)),
    )
    try:
        assert holder.stdout is not None
        assert "locked" in holder.stdout.readline()
        holder.kill()
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)

    assert lock_path.exists(), "锁文件本身可以留下（内容无意义）"

    proc = _spawn(_CHILD_RESOLVE_ONCE, _child_env(tmp_path, data, home))
    stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 0, f"强杀后下一次迁移失败：{stderr}"
    result = json.loads(stdout.strip().splitlines()[-1])
    assert result["store_path"] == str(target)
    assert result["role_count"] == 3
    assert target.is_file()
    assert _sha256(target) == legacy_hash
    assert not list(data.glob("*.migrating.*"))


def test_orphan_staging_from_a_crash_is_cleaned_up(store_env, caplog):
    """模拟上次迁移在 replace 之前被强杀，遗留的 staging 必须被自动清掉。"""
    data, legacy = store_env
    _write_store(legacy, _three_roles())
    data.mkdir(parents=True, exist_ok=True)
    orphan = data / "rbac.json.migrating.99999.deadbeef"
    orphan.write_text("partial", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger=rbac_module.__name__):
        manager = RBACManager()

    assert Path(manager.store_path) == data / "rbac.json"
    assert not orphan.exists()
    assert any("已清理上次中断" in record.getMessage() for record in caplog.records)
