"""首次运行向导（自用版分发）的行为测试。

只断言行为：密钥落进数据目录（不是程序目录 .env）、空注册表无人、模型配置以
密文落库、跳过模式明确提示、已配置时自动退出不打断用户。不与真实 Ollama/云
provider 通信。
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


def test_ensure_integrity_key_writes_data_dir_file(tmp_path, monkeypatch):
    monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(frw, "REPO_ROOT", tmp_path)

    assert frw._ensure_integrity_key() is True

    key_file = tmp_path / "data" / "config" / "integrity.key"
    assert key_file.is_file()
    from src.common import integrity_key as _ik

    assert _ik.read_key_file(key_file) == os.environ.get(
        HUMAN_IDENTITIES_INTEGRITY_KEY_ENV
    )
    if os.name == "nt":
        assert key_file.read_bytes().startswith(b"LIUHAO-DPAPI-1:")
    else:
        # POSIX 无 DPAPI：按设计降级为 RAW 形态落盘（读回校验已在上方断言）。
        assert key_file.read_bytes().startswith(b"LIUHAO-RAW-1:")
    # 不再写进程序目录。
    assert not (tmp_path / ".env").exists()


def test_ensure_integrity_key_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, "already-set")
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
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


def test_wizard_completion_warns_when_model_skipped(capsys, isolated_env, monkeypatch):
    """向导完成时模型仍未配：必须明说聊天只会回未配置提示、不编模拟回答。"""
    monkeypatch.setattr(frw, "_has_loginable_human", lambda: True)
    monkeypatch.setattr(frw, "_has_enabled_model", lambda: False)
    # 名字提示用默认值（回车 = 贾维斯）：只给模型选择返回 "0"，不把 "0"
    # 当成工作台名字存下来。
    monkeypatch.setattr(
        frw, "_prompt", lambda label, default=None: "0" if label == "选择" else (default or "")
    )

    rc = frw._run_interactive_setup()

    assert rc == 0
    out = capsys.readouterr().out
    assert "向导完成" in out
    assert "未配置模型" in out
    assert "请先配置模型密钥" in out
    assert "不会生成模拟回答" in out


def test_wizard_saves_custom_workspace_name(isolated_env, monkeypatch):
    """向导里起了新名字：存在隔离的 workspace_settings.json，之后全站读它。"""
    import json

    from src.brand import resolve_brand

    settings_file = isolated_env / "workspace_settings.json"
    monkeypatch.setenv("LIUHAO_WORKSPACE_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(frw, "_has_loginable_human", lambda: True)
    monkeypatch.setattr(frw, "_has_enabled_model", lambda: False)
    answers = {
        "选择": "0",
        "工作台名字（中文）": "阿强",
        "英文名（可空，留空则与中文名相同）": "",
    }
    monkeypatch.setattr(
        frw, "_prompt", lambda label, default=None: answers.get(label, default or "")
    )

    rc = frw._run_interactive_setup()

    assert rc == 0
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        "name_en": "阿强",
        "name_zh": "阿强",
    }
    assert resolve_brand()["name_zh"] == "阿强"
    assert resolve_brand()["lockup"] == "阿强"


def test_wizard_default_name_writes_no_settings_file(isolated_env, monkeypatch):
    """名字直接回车（默认贾维斯）：不写配置文件，出厂默认继续生效。"""
    from src.brand import resolve_brand

    settings_file = isolated_env / "workspace_settings.json"
    monkeypatch.setenv("LIUHAO_WORKSPACE_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(frw, "_has_loginable_human", lambda: True)
    monkeypatch.setattr(frw, "_has_enabled_model", lambda: False)
    monkeypatch.setattr(
        frw, "_prompt", lambda label, default=None: "0" if label == "选择" else (default or "")
    )

    rc = frw._run_interactive_setup()

    assert rc == 0
    assert not settings_file.exists()
    assert resolve_brand()["name_zh"] == "贾维斯"
    assert resolve_brand()["customized"] is False


def test_wizard_stops_when_integrity_key_unwritable(isolated_env, monkeypatch, tmp_path):
    monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
    # 指向一个无法创建目录的位置（用文件当目录），密钥落盘必然失败。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(blocker / "data"))

    rc = frw.run_wizard(skip=False)

    assert rc == 1
