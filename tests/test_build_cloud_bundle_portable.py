"""便携版打包（自用版分发）的行为测试。

核心断言：provider 配置绝不烘进 serve.py；向导/启动脚本随包附带。
"""
from __future__ import annotations

from scripts import build_cloud_bundle as bcb


def test_portable_strips_llm_block_even_with_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_TYPE", "openai")
    monkeypatch.setenv("AI_PROVIDER_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://jiefuai.vip/v1")
    monkeypatch.setenv("AI_PROVIDER_KEY", "sk-secret")

    assert bcb._render_llm_block(portable=True) == ""


def test_nonportable_bakes_llm_block_from_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_TYPE", "openai")
    monkeypatch.setenv("AI_PROVIDER_MODEL", "gpt-5.6-sol")

    block = bcb._render_llm_block(portable=False)
    assert "AI_PROVIDER_TYPE" in block
    assert "gpt-5.6-sol" in block


def test_nonportable_without_env_emits_nothing(monkeypatch):
    for key in bcb.LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert bcb._render_llm_block(portable=False) == ""


def test_portable_assets_written(tmp_path):
    bcb._write_portable_assets(tmp_path)

    for rel in bcb.PORTABLE_SCRIPTS:
        assert (tmp_path / rel).is_file(), rel
    for name in bcb.PORTABLE_ROOT_FILES:
        assert (tmp_path / name).is_file(), name
    assert (tmp_path / "start.bat").is_file()
    bat = (tmp_path / "start.bat").read_text(encoding="utf-8")
    assert "first_run_wizard.py" in bat
    assert "start_liuhao.py" in bat
