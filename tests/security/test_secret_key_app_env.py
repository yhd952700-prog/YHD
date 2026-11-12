"""``SECRET_KEY`` 的落盘形态：明文自动迁移为 DPAPI、运行时仍是明文。

验收口径
--------
1. 旧安装的明文 ``SECRET_KEY`` 在数据目录加载时自动迁移成 ``LIUHAO-DPAPI-1:``；
2. 迁移后进程环境里拿到的仍是**明文**（任何按环境变量读取的代码行为不变）；
3. 换机器/换用户解不开时：不覆盖原文件、不注入密文冒充密钥、中文告警；
4. 运维显式注入的不同值优先，不碰文件。
"""
from __future__ import annotations

import logging
import os

import pytest

import src.security.encryption as encryption
from src.security.app_env_secrets import (
    SECRET_KEY_DPAPI_PURPOSE,
    SECRET_KEY_ENV,
    ensure_secret_key,
)

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="DPAPI 只在 Windows 上可验证"
)


def _write_env(path, value: str) -> None:
    path.write_text(
        f"APP_ENV=production\nLIUHAO_JWT_SECRET=jwt-seed\n{SECRET_KEY_ENV}={value}\n",
        encoding="utf-8",
    )


def _read_value(path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{SECRET_KEY_ENV}="):
            return line.split("=", 1)[1]
    raise AssertionError("SECRET_KEY line missing")


@WINDOWS_ONLY
def test_legacy_plaintext_is_migrated_and_runtime_keeps_plaintext(tmp_path):
    path = tmp_path / "app.env"
    _write_env(path, "legacy-secret-001")
    env = {}

    assert ensure_secret_key(path, environ=env) == "legacy-secret-001"
    assert env[SECRET_KEY_ENV] == "legacy-secret-001"

    stored = _read_value(path)
    assert stored.startswith(encryption.DPAPI_PREFIX)
    plain, mode = encryption.unwrap_machine_bound(
        stored, purpose=SECRET_KEY_DPAPI_PURPOSE
    )
    assert mode == "dpapi"
    assert plain.decode("utf-8") == "legacy-secret-001"


@WINDOWS_ONLY
def test_wrapped_value_is_unwrapped_for_runtime(tmp_path):
    path = tmp_path / "app.env"
    wrapped = encryption.wrap_machine_bound(
        b"machine-bound-secret", purpose=SECRET_KEY_DPAPI_PURPOSE
    ).decode("ascii")
    _write_env(path, wrapped)
    before = path.read_bytes()
    env = {}

    assert ensure_secret_key(path, environ=env) == "machine-bound-secret"
    assert env[SECRET_KEY_ENV] == "machine-bound-secret"
    # 已经是目标形态，不重复改写。
    assert path.read_bytes() == before


@WINDOWS_ONLY
def test_cross_machine_failure_does_not_overwrite_nor_inject_ciphertext(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "app.env"
    wrapped = encryption.wrap_machine_bound(
        b"other-machine-secret", purpose=SECRET_KEY_DPAPI_PURPOSE
    ).decode("ascii")
    _write_env(path, wrapped)
    before = path.read_bytes()
    env = {SECRET_KEY_ENV: wrapped}

    monkeypatch.setattr(
        encryption, "dpapi_unprotect", lambda *args, **kwargs: None, raising=False
    )
    caplog.set_level(logging.WARNING)

    assert ensure_secret_key(path, environ=env) is None
    assert SECRET_KEY_ENV not in env
    assert path.read_bytes() == before
    assert "本机密钥无法在此机器解密" in caplog.text
    assert "登录令牌、下载与重置链接不依赖该值" in caplog.text


def test_explicit_operator_override_wins(tmp_path):
    path = tmp_path / "app.env"
    _write_env(path, "file-value")
    before = path.read_bytes()
    env = {SECRET_KEY_ENV: "operator-value"}

    assert ensure_secret_key(path, environ=env) == "operator-value"
    assert env[SECRET_KEY_ENV] == "operator-value"
    assert path.read_bytes() == before


def test_missing_secret_key_returns_none(tmp_path):
    path = tmp_path / "app.env"
    path.write_text("APP_ENV=production\n", encoding="utf-8")
    env = {}
    assert ensure_secret_key(path, environ=env) is None
    assert SECRET_KEY_ENV not in env
