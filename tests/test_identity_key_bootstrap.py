"""身份完整性密钥自举回归：全新安装 / 旧数据升级 / 密钥丢失但身份库仍在。

背景：``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` 未设置时，身份内核会拒绝
登记表里的每一行（fail-closed）。普通用户装好后会卡在「无人可登录」。
本文件锁住三条路径的行为与可读性，以及密钥落盘位置/权限。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from src.common import integrity_key as ik
from src.kernels.identity._persistence import (
    FIELD_INTEGRITY,
    JsonFileStore,
    compute_row_tag,
)


def _write_registry(path, rows, *, wrapped=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"humans": rows} if wrapped else rows
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _row(principal: str) -> dict:
    return {
        "principal": principal,
        "display_name": principal,
        "permissions": ["admin"],
        "scope": "L0",
        "registered_at": "2026-11-06T00:00:00Z",
    }


def test_fresh_install_generates_key_in_data_dir(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    key = ik.ensure_integrity_key(data_dir=tmp_path)

    assert len(key) == 64 and key.isalnum()
    key_file = tmp_path / "config" / "integrity.key"
    assert key_file.is_file()
    assert key_file.read_text(encoding="utf-8").strip() == key
    # 密钥值本身绝不打印到控制台。
    captured = capsys.readouterr()
    assert key not in captured.out and key not in captured.err
    if os.name != "nt":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_key_is_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    first = ik.ensure_integrity_key(data_dir=tmp_path)
    second = ik.ensure_integrity_key(data_dir=tmp_path)
    assert first == second


def test_env_key_is_persisted_so_it_survives_env_loss(tmp_path, monkeypatch):
    monkeypatch.setenv(ik.INTEGRITY_KEY_ENV, "operator-provided-key")
    key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert key == "operator-provided-key"
    assert (tmp_path / "config" / "integrity.key").read_text(
        encoding="utf-8"
    ).strip() == key


def test_env_and_file_conflict_uses_env_and_warns(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(ik.INTEGRITY_KEY_ENV, "env-key")
    key_file = tmp_path / "config" / "integrity.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text("file-key\n", encoding="utf-8")

    assert ik.ensure_integrity_key(data_dir=tmp_path) == "env-key"
    assert "不一致" in capsys.readouterr().err


def test_legacy_untagged_rows_are_signed_on_upgrade(tmp_path, capsys, monkeypatch):
    """已有数据升级：旧版无签名行补签后能真正被人身份内核接纳。"""
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    registry = _write_registry(
        tmp_path / "config" / "human_identities.json", [_row("liuhao")]
    )

    key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert "补签" in capsys.readouterr().err

    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["humans"][0][FIELD_INTEGRITY]

    monkeypatch.setenv(ik.INTEGRITY_KEY_ENV, key)
    store = JsonFileStore(str(registry))
    admitted = store.load_all()
    assert [row["principal"] for row in admitted] == ["liuhao"]
    assert store.last_load_report["rejected"] == []


def test_missing_key_with_signed_registry_is_explicit_not_silent(
    tmp_path, capsys, monkeypatch
):
    """密钥丢失但身份库仍有已签名行：给出可读恢复步骤，旧行仍被拒绝。"""
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    old_key = "lost-key"
    row = _row("liuhao")
    row[FIELD_INTEGRITY] = compute_row_tag(row, old_key)
    registry = _write_registry(
        tmp_path / "config" / "human_identities.json", [row]
    )

    new_key = ik.ensure_integrity_key(data_dir=tmp_path)
    err = capsys.readouterr().err
    assert "密钥缺失" in err
    assert "恢复" in err and "重新注册" in err
    assert new_key != old_key
    assert new_key not in err

    monkeypatch.setenv(ik.INTEGRITY_KEY_ENV, new_key)
    store = JsonFileStore(str(registry))
    assert store.load_all() == []
    assert store.last_load_report["rejected"] == ["liuhao"]


def test_unreadable_registry_warns_but_still_bootstraps(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    bad = tmp_path / "config" / "human_identities.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")

    key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert len(key) == 64
    assert "无法解析" in capsys.readouterr().err


def test_launcher_bootstraps_key_when_env_is_absent(tmp_path, monkeypatch, capsys):
    """源码/自用版直接跑 start_liuhao.py 时同样会自举密钥。"""
    from scripts import start_liuhao as sl

    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sl, "ROOT", tmp_path)

    assert sl._ensure_identity_integrity_key() is True
    assert (tmp_path / "data" / "config" / "integrity.key").is_file()
    assert os.environ[ik.INTEGRITY_KEY_ENV].strip()
    assert os.environ[ik.INTEGRITY_KEY_ENV] not in capsys.readouterr().err


def test_installed_launcher_helper_delegates(tmp_path, monkeypatch):
    """安装版入口的助手把数据目录交给统一实现（全新安装路径）。"""
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    from scripts import launch_installed as li

    key = li._ensure_integrity_key(tmp_path / "data" / "config" / "integrity.key")
    assert len(key) == 64
    assert (tmp_path / "data" / "config" / "integrity.key").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 断言仅在 Windows 生效")
def test_windows_key_acl_is_restricted(tmp_path, monkeypatch):
    """Windows 用 icacls 去掉继承、只授予当前用户（mode 位不构成访问控制）。"""
    calls = []
    real_run = ik.subprocess.run

    def _record(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(ik.subprocess, "run", _record)
    key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert len(key) == 64
    assert calls, "Windows 下必须调用 icacls 收紧密钥文件权限"
    assert "/inheritance:r" in calls[0]
