"""首次运行向导（自用版分发）的行为测试。

只断言行为：密钥写进 .env、空注册表无人、模型配置以密文落库、跳过模式明确提示、
已配置时自动退出不打断用户。不与真实 Ollama/云 provider 通信。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from scripts import first_run_wizard as frw
from src.ai.model_config import get_model_config_store, reset_model_config_store
from src.kernels.identity._persistence import HUMAN_IDENTITIES_INTEGRITY_KEY_ENV


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """把身份/凭据/模型配置都指到临时目录，避免污染本机 config/。"""
    identity_file = tmp_path / "human_identities.json"
    secrets_file = tmp_path / "auth_secrets.json"
    model_db = tmp_path / "model_registry.sqlite3"
    model_key = tmp_path / "model_config.key"

    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(identity_file))
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(model_db))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY_FILE", str(model_key))
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, "test-integrity-key-0123456789")
    # 重置模型配置单例，让它指向临时 db。
    reset_model_config_store()
    yield tmp_path
    reset_model_config_store()


# ---------------------------------------------------------------------------
# 完整性密钥
# ---------------------------------------------------------------------------


def test_ensure_integrity_key_writes_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
    monkeypatch.setattr(frw, "REPO_ROOT", tmp_path)

    assert frw._ensure_integrity_key() is True

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"{HUMAN_IDENTITIES_INTEGRITY_KEY_ENV}=" in env_text
    # 进程内也已设置，后续 IdentityManager 能加载行。
    assert os.environ.get(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV)


def test_ensure_integrity_key_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, "already-set")
    monkeypatch.setattr(frw, "REPO_ROOT", tmp_path)

    assert frw._ensure_integrity_key() is True
    assert not (tmp_path / ".env").exists()


# ---------------------------------------------------------------------------
# 身份探测
# ---------------------------------------------------------------------------


def test_has_loginable_human_false_on_empty_store(isolated_env):
    # 临时身份文件不存在 → IdentityManager 加载零个人类。
    assert frw._has_loginable_human() is False


# ---------------------------------------------------------------------------
# 模型配置
# ---------------------------------------------------------------------------


def test_configure_ollama_creates_record(isolated_env):
    result = frw.configure_ollama("qwen2.5:7b")

    store = get_model_config_store()
    models = store.list_models()
    assert len(models) == 1
    assert models[0]["id"] == result["id"]
    assert models[0]["provider"] == "ollama"
    assert models[0]["model"] == "qwen2.5:7b"
    assert models[0]["enabled"] is True
    assert models[0]["is_default"] is True


def test_configure_openai_compatible_encrypts_key(isolated_env):
    plaintext = "sk-secret-test-key-12345"
    frw.configure_openai_compatible("https://api.example.com/v1", "gpt-test", plaintext)

    # 直接读 SQLite 行：api_key_enc 非空，且明文 key 不出现在库文件里。
    db_path = os.environ["LIUHAO_MODEL_CONFIG_DB"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT api_key_enc FROM ai_model_configs").fetchone()
    conn.close()

    assert row["api_key_enc"]
    raw = Path(db_path).read_bytes()
    assert plaintext.encode("utf-8") not in raw


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------


def test_skip_mode_prints_gaps(capsys, isolated_env, monkeypatch):
    monkeypatch.setattr(frw, "_has_loginable_human", lambda: False)
    monkeypatch.setattr(frw, "_has_enabled_model", lambda: False)

    rc = frw.run_wizard(skip=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "跳过向导" in out
    assert "无法通过" in out or "登录" in out
    assert "聊天" in out


def test_wizard_auto_skips_when_fully_configured(capsys, isolated_env, monkeypatch):
    # 已有身份 + 已有模型 → 不弹任何 input，直接返回 0。
    monkeypatch.setattr(frw, "_has_loginable_human", lambda: True)
    monkeypatch.setattr(frw, "_has_enabled_model", lambda: True)

    rc = frw.run_wizard(skip=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert "跳过首次运行向导" in out


def test_wizard_stops_when_integrity_key_unwritable(isolated_env, monkeypatch, tmp_path):
    monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
    # 指向一个无法创建文件的路径（用文件当目录）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(frw, "REPO_ROOT", blocker)

    rc = frw.run_wizard(skip=False)

    assert rc == 1
