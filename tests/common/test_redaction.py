"""统一的脱敏规则与独立泄漏自检。"""
from __future__ import annotations

import zipfile

import pytest

from src.common.redaction import leak_violations, redact_text
from src.desktop.diagnostics import verify_bundle


def test_clean_chinese_text_has_no_violations():
    assert leak_violations("模型调用成功，耗时 120ms") == []


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\张三\AppData\Local\LiuHao\data",
        "D:/Users/zhangsan/LiuHao/data",
        "/home/zhangsan/.local/share/liuhao",
        "/Users/zhangsan/Library/Logs/liuhao.log",
        "https://example.com/home/zhangsan/data",
    ],
)
def test_username_paths_are_masked_and_detected(raw: str):
    cleaned = redact_text(raw)
    assert "<用户>" in cleaned
    assert "张三" not in cleaned
    assert "zhangsan" not in cleaned
    assert leak_violations(cleaned) == []


@pytest.mark.parametrize(
    "raw",
    [
        "LIUHAO_AUTH_TTL_SECONDS=43200",
        "LIUHAO_MODEL_ROUTER=fast",
        "APP_ENV=production",
        "AI_PROVIDER_KEY=value",
    ],
)
def test_environment_variable_names_are_removed(raw: str):
    cleaned = redact_text(raw)
    assert "<环境变量>" in cleaned
    assert "LIUHAO_" not in cleaned
    assert "APP_ENV" not in cleaned
    assert leak_violations(cleaned) == []


@pytest.mark.parametrize(
    "raw",
    [
        "python scripts/register_human_identity.py --principal root",
        "运行 packaging/make_release.py 打包",
        r"安装目录 D:\LiuHao-AI-OS\installer\liuhao.iss",
        r"C:\Program Files\LiuHao\liuhao.exe",
    ],
)
def test_script_and_install_paths_are_removed(raw: str):
    cleaned = redact_text(raw)
    assert "scripts/" not in cleaned
    assert "packaging/" not in cleaned
    assert "installer" not in cleaned.lower()
    assert "Program Files" not in cleaned
    assert leak_violations(cleaned) == []


@pytest.mark.parametrize(
    "raw",
    [
        "parent_id=42",
        '"root_id": "acct-root"',
        "depth=3",
        "'ancestor': 'acct-1'",
    ],
)
def test_hierarchy_fields_are_removed(raw: str):
    cleaned = redact_text(raw)
    for token in ("parent_id", "root_id", "depth", "ancestor"):
        assert token not in cleaned
    assert leak_violations(cleaned) == []


@pytest.mark.parametrize(
    "raw",
    [
        "recovery_code=RC-8F2K-9Q1M-7Z",
        "passphrase=correct horse battery staple",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "token=abc123def456",
        "api_key=sk-live-0123456789abcdef",
        "AIzaSyD-0123456789abcdefghijklmnop",
        "gsk_0123456789abcdefghijklmnop",
        "ghp_0123456789abcdefghijklmnopqrst",
        "https://user:hunter2@example.com/v1",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl",
    ],
)
def test_secret_families_are_redacted(raw: str):
    cleaned = redact_text(raw)
    assert leak_violations(cleaned) == []


def test_leak_check_is_not_the_same_rules_as_redaction():
    """自检必须能在"脱敏规则失效"时独立命中。"""
    raw = "Authorization: Basic dXNlcjpwYXNzd29yZA== recovery_code=RC-1234-5678"
    assert "密钥或令牌" in leak_violations(raw)


def test_blank_text_is_safe():
    assert redact_text("") == ""
    assert leak_violations("") == []


def test_verify_bundle_rejects_injected_leaks(tmp_path):
    """人为把泄漏塞进包内成员，自检必须判失败。"""
    target = tmp_path / "injected.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "about.json",
            '{"api_key": "sk-live-0123456789abcdef", '
            '"data_dir": "C:\\\\Users\\\\张三\\\\AppData\\\\Local\\\\LiuHao"}',
        )
        archive.writestr(
            "environment.json",
            '{"APP_ENV": "production", "script": "scripts/register_human_identity.py"}',
        )
        archive.writestr("ports.json", "{}")
        archive.writestr("README.txt", "普通说明")

    violations = verify_bundle(target)
    joined = "\n".join(violations)
    assert "泄漏特征" in joined
    assert "密钥或令牌" in joined
    assert "环境变量名" in joined
    assert "用户名路径" in joined
