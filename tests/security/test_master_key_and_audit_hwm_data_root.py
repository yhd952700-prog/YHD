"""主密钥 / 审计高水位跟随统一数据根，且迁移不丢数据、不换密钥。

覆盖用户要求的四种组合（旧位置 / 新位置 / 两边都有 / 两边都没有），
外加迁移失败时不得生成新密钥。
"""

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.common import windows_acl
from src.common.fs_permissions import ensure_restricted_to_current_user
from src.security import encryption
from src.security.encryption import EncryptionManager


def _write_key(path: Path, key: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.urlsafe_b64encode(key))
    return key


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def key_env(monkeypatch, tmp_path):
    """数据根 + 隔离后的旧主密钥位置（不碰真实 ~/.liuhao）。"""
    monkeypatch.delenv("LIUHAO_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("LIUHAO_SECRET_DEV_EPHEMERAL", raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    legacy = tmp_path / "home" / ".liuhao" / "master.key"
    monkeypatch.setattr(encryption, "_legacy_master_key_file", lambda: legacy)
    return SimpleNamespace(
        data_dir=data_dir,
        legacy=legacy,
        target=data_dir / "master.key",
        tmp_path=tmp_path,
    )


class TestMasterKeyResolution:
    def test_only_legacy_migrates_and_keeps_the_old_file(self, key_env):
        old_key = _write_key(key_env.legacy, os.urandom(32))
        old_hash = _sha256(key_env.legacy)

        manager = EncryptionManager()

        assert manager.master_key == old_key, "迁移后必须仍是同一把密钥"
        assert key_env.target.exists()
        assert key_env.target.read_bytes() == key_env.legacy.read_bytes()
        assert _sha256(key_env.legacy) == old_hash, "旧文件必须保留且不被改写"
        assert not list(key_env.data_dir.glob("*.migrating"))

    def test_only_new_path_is_used_as_is(self, key_env):
        new_key = _write_key(key_env.target, os.urandom(32))

        manager = EncryptionManager()

        assert manager.master_key == new_key
        assert not key_env.legacy.exists(), "不存在的旧位置不应被创建"

    def test_both_exist_new_wins_and_old_is_not_deleted(self, key_env):
        legacy_key = _write_key(key_env.legacy, os.urandom(32))
        new_key = _write_key(key_env.target, os.urandom(32))
        legacy_hash = _sha256(key_env.legacy)

        manager = EncryptionManager()

        assert manager.master_key == new_key, "两边都有时以新路径为准"
        assert manager.master_key != legacy_key
        assert key_env.legacy.exists(), "不得静默删除旧文件"
        assert _sha256(key_env.legacy) == legacy_hash

    def test_neither_exists_generates_into_the_data_root(self, key_env):
        manager = EncryptionManager()

        assert len(manager.master_key) == 32
        assert key_env.target.exists()
        stored = base64.urlsafe_b64decode(key_env.target.read_bytes())
        assert stored == manager.master_key
        assert not key_env.legacy.exists()

    def test_explicit_variable_wins_over_everything(self, key_env, monkeypatch):
        explicit_key = _write_key(key_env.tmp_path / "explicit.key", os.urandom(32))
        _write_key(key_env.legacy, os.urandom(32))
        _write_key(key_env.target, os.urandom(32))
        monkeypatch.setenv("LIUHAO_MASTER_KEY_FILE", str(key_env.tmp_path / "explicit.key"))

        manager = EncryptionManager()

        assert manager.master_key == explicit_key

    def test_migration_failure_uses_legacy_and_never_generates_a_new_key(
        self, key_env, monkeypatch
    ):
        old_key = _write_key(key_env.legacy, os.urandom(32))
        old_hash = _sha256(key_env.legacy)

        def boom(source, target):
            raise OSError("simulated copy failure")

        monkeypatch.setattr(encryption, "_copy_with_verification", boom)

        manager = EncryptionManager()

        assert manager.master_key == old_key, "迁移失败也必须继续用旧密钥"
        assert not key_env.target.exists(), "迁移失败不得留下半个新密钥"
        assert _sha256(key_env.legacy) == old_hash

    def test_source_changed_during_migration_aborts_and_keeps_legacy(
        self, key_env, monkeypatch
    ):
        """锁内复读发现旧密钥已被改写：放弃迁移，继续用旧位置，绝不用过期快照。"""
        old_key = _write_key(key_env.legacy, os.urandom(32))
        original_read = Path.read_bytes
        calls = {"n": 0}

        def flaky_read(self):
            data = original_read(self)
            if self == key_env.legacy and calls["n"] < 2:
                calls["n"] += 1
                if calls["n"] == 2:
                    return data + b"tampered"
            return data

        monkeypatch.setattr(Path, "read_bytes", flaky_read)

        manager = EncryptionManager()

        assert calls["n"] == 2, "迁移必须在锁内重新读一次旧密钥"
        assert manager.master_key == old_key
        assert not key_env.target.exists(), "源文件变了就不能落位新密钥"
        assert not list(key_env.data_dir.glob("*.migrating.*"))

    def test_without_a_data_root_the_legacy_location_is_unchanged(
        self, key_env, monkeypatch
    ):
        monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
        old_key = _write_key(key_env.legacy, os.urandom(32))

        manager = EncryptionManager()

        assert manager.master_key == old_key
        assert not key_env.target.exists()


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_KEY_CHILD = r"""
import json, logging, os, sys, time
from pathlib import Path
from src.security.encryption import resolve_master_key_file

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
ready = Path(os.environ["KEY_READY"])
go = Path(os.environ["KEY_GO"])
ready.write_text("ready", encoding="utf-8")
deadline = time.time() + 20
while not go.exists():
    if time.time() > deadline:
        print("barrier timeout", file=sys.stderr)
        raise SystemExit(2)
    time.sleep(0.002)
print(json.dumps({"path": str(resolve_master_key_file())}, ensure_ascii=False))
"""


def _wait_until(path: Path, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while not path.exists():
        if time.time() > deadline:
            raise AssertionError(f"子进程未在 {timeout}s 内就绪：{path}")
        time.sleep(0.005)


class TestMasterKeyConcurrency:
    def test_two_processes_migrate_the_key_exactly_once(self, key_env):
        """两个真实子进程同时解析：只许一方迁移，两方拿到同一把密钥。"""
        old_key = _write_key(key_env.legacy, os.urandom(32))
        legacy_hash = _sha256(key_env.legacy)

        env = os.environ.copy()
        env.update(
            {
                "LIUHAO_DATA_DIR": str(key_env.data_dir),
                "USERPROFILE": str(key_env.tmp_path / "home"),
                "HOME": str(key_env.tmp_path / "home"),
                "PYTHONPATH": str(_PROJECT_ROOT),
            }
        )
        env.pop("LIUHAO_MASTER_KEY_FILE", None)

        go = key_env.tmp_path / "key-go"
        procs = []
        for index in range(2):
            child_env = dict(env)
            child_env["KEY_READY"] = str(key_env.tmp_path / f"key-ready-{index}")
            child_env["KEY_GO"] = str(go)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", _KEY_CHILD],
                    cwd=_PROJECT_ROOT,
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        try:
            for index in range(2):
                _wait_until(key_env.tmp_path / f"key-ready-{index}")
            go.touch()
            outputs = [proc.communicate(timeout=60) for proc in procs]
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)

        for proc, (stdout, stderr) in zip(procs, outputs):
            assert proc.returncode == 0, f"子进程失败：{stderr}"
            assert json.loads(stdout.strip().splitlines()[-1])["path"] == str(
                key_env.target
            )

        migration_logs = sum(
            stderr.count("主密钥已迁移") for _stdout, stderr in outputs
        )
        assert migration_logs == 1, f"只允许一个进程执行迁移，实际日志：{outputs}"
        assert key_env.target.read_bytes() == key_env.legacy.read_bytes()
        assert _sha256(key_env.legacy) == legacy_hash
        assert not list(key_env.data_dir.glob("*.migrating.*"))
        assert EncryptionManager().master_key == old_key


class TestAuditHwmPaths:
    def test_audit_kernel_hwm_follows_the_data_root(self, monkeypatch, tmp_path):
        import src.security.audit_policy as audit_policy

        monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AUDIT_KERNEL_HWM_PATH", raising=False)
        monkeypatch.setattr(audit_policy, "_global_audit_kernel", None)

        kernel = audit_policy.get_audit_kernel()

        assert Path(kernel._hwm_path) == tmp_path / "data" / "audit_kernel_hwm.json"

    def test_crypto_audit_hwm_follows_the_data_root(self, monkeypatch, tmp_path):
        import src.security.audit_logger as audit_logger

        monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AUDIT_HWM_PATH", raising=False)
        monkeypatch.setattr(audit_logger, "_default_logger", None)

        logger = audit_logger.get_crypto_audit_logger()

        assert Path(logger._hwm_path) == tmp_path / "data" / "audit_hwm.json"

    def test_explicit_hwm_variables_still_win(self, monkeypatch, tmp_path):
        import src.security.audit_policy as audit_policy
        import src.security.audit_logger as audit_logger

        monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("AUDIT_KERNEL_HWM_PATH", str(tmp_path / "kernel.json"))
        monkeypatch.setenv("AUDIT_HWM_PATH", str(tmp_path / "crypto.json"))
        monkeypatch.setattr(audit_policy, "_global_audit_kernel", None)
        monkeypatch.setattr(audit_logger, "_default_logger", None)

        kernel = audit_policy.get_audit_kernel()
        logger = audit_logger.get_crypto_audit_logger()

        assert Path(kernel._hwm_path) == tmp_path / "kernel.json"
        assert Path(logger._hwm_path) == tmp_path / "crypto.json"


def _acl_state(path: Path):
    """返回 (是否受保护, ACE 列表)，供断言与失败信息使用。"""
    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        return (mode & 0o077) == 0, [{"mode": oct(mode)}]
    import win32security

    descriptor = win32security.GetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION
    )
    protected = bool(
        descriptor.GetSecurityDescriptorControl()[0]
        & win32security.SE_DACL_PROTECTED
    )
    return protected, windows_acl.dacl_entries(
        descriptor.GetSecurityDescriptorDacl()
    )


def _loosen_to_inherited(path: Path) -> None:
    """把文件还原成「继承父目录 ACL」的历史状态（模拟老装机用户）。"""
    if os.name != "nt":
        os.chmod(path, 0o644)
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:e", "/grant", "*S-1-5-32-545:(RX)"],
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_restricted(path: Path) -> None:
    state = _acl_state(path)
    assert windows_acl.acl_is_current_user_only(path), f"ACL 未收紧：{state}"
    if os.name != "nt":
        return
    import win32security

    protected, entries = state
    assert protected is True, f"DACL 仍可继承：{state}"
    inherited = getattr(win32security, "INHERITED_ACE", 0x10)
    assert len(entries) == 1, f"应收紧为单条 ACE：{state}"
    assert entries[0]["sid"] == windows_acl.current_user_sid_string(), state
    assert not entries[0]["flags"] & inherited, f"仍是继承 ACE：{state}"


class TestMasterKeyFilePermissions:
    """主密钥文件只能被当前用户访问（Windows 用显式 DACL，非 chmod）。"""

    def test_new_master_key_file_is_restricted(self, key_env):
        manager = EncryptionManager()

        stored = key_env.target.read_bytes()
        assert len(stored) == 44, "明文格式不变：仍是 base64(urlsafe) 的 44 字节"
        assert base64.urlsafe_b64decode(stored) == manager.master_key
        _assert_restricted(key_env.target)

    def test_migration_target_and_source_are_restricted(self, key_env):
        old_key = _write_key(key_env.legacy, os.urandom(32))
        _loosen_to_inherited(key_env.legacy)
        assert not windows_acl.acl_is_current_user_only(key_env.legacy)

        manager = EncryptionManager()

        assert manager.master_key == old_key
        assert key_env.target.read_bytes() == key_env.legacy.read_bytes()
        _assert_restricted(key_env.target)
        _assert_restricted(key_env.legacy), "旧位置保留的文件也不该继续裸露"

    def test_legacy_inherited_file_is_tightened_on_read(self, key_env):
        key = _write_key(key_env.target, os.urandom(32))
        _loosen_to_inherited(key_env.target)
        protected_before, entries_before = _acl_state(key_env.target)
        assert not windows_acl.acl_is_current_user_only(key_env.target)
        if os.name == "nt":
            assert protected_before is False, f"前置条件：应为继承 ACL：{entries_before}"
            inherited = __import__("win32security").INHERITED_ACE
            assert any(
                entry["flags"] & inherited for entry in entries_before
            ), f"前置条件：应存在继承 ACE：{entries_before}"

        manager = EncryptionManager()

        assert manager.master_key == key, "收紧 ACL 不得改变密钥内容"
        staged = key_env.target.read_bytes()
        assert staged == base64.urlsafe_b64encode(key)
        protected_after, entries_after = _acl_state(key_env.target)
        _assert_restricted(key_env.target)
        if os.name == "nt":
            assert protected_after is True
            assert len(entries_after) == 1, f"生效后应只剩当前用户：{entries_after}"

    def test_auto_tighten_is_idempotent(self, key_env):
        _write_key(key_env.target, os.urandom(32))
        _loosen_to_inherited(key_env.target)

        assert ensure_restricted_to_current_user(key_env.target) is True
        first = key_env.target.read_bytes()
        assert ensure_restricted_to_current_user(key_env.target) is True
        assert ensure_restricted_to_current_user(key_env.target) is True

        assert key_env.target.read_bytes() == first, "重复收紧不得改写内容"
        _assert_restricted(key_env.target)
