"""日志 / 诊断包泄漏修复的回归测试（2026-11-08 审计 P0）。

覆盖三件事：

1. 独立语料：``leak_violations(redact_text(corpus)) == []`` —— 脱敏与自检
   必须同时认识裸 ``key``、URL query 凭据、各家新前缀与多段 Cookie；
2. stdout 通道：观测性兜底 handler 必须走 ``RedactingFormatter``，且全局
   日志已配置时不再往 ``liuhao`` 上挂第二个 handler（消除明文副本）；
3. 桌面壳抓取：``desktop-gateway.log`` 逐行脱敏后落盘，轮转文件同样干净。
"""
from __future__ import annotations

import logging

import pytest

from src.common.redaction import leak_violations, redact_log_text, redact_text

CORPUS = "\n".join(
    [
        '127.0.0.1 - "GET /v1/health?key=LHQUERYSECRET-ABCDEF123456 HTTP/1.1" 200',
        "GET /v1/x?a=1&key=AMPERSAND-KEY-1234567890",
        '{"key": "JSON-KEY-1234567890"}',
        "key=PLAIN-KEY-1234567890",
        "azure_key=0123456789abcdef0123456789abcdef",
        "subscription_key=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "Authorization: Bearer sk-LHLEAK-1234567890abcdef",
        "openai.AuthenticationError: Incorrect API key provided: sk-LHLEAK-1234567890abcdef",
        "stripe sk_live_51H8xQ2abcdefghijklmnop",
        "huggingface hf_abcdefghijklmnopqrstuvwxyz012345",
        "gitlab glpat-abcdefghijklmnopqrst",
        "Cookie: sid=abc123; theme=dark; csrf=zzz999",
        "Set-Cookie: session=abc123; Path=/; HttpOnly",
        "https://example.invalid/cb?sig=deadbeef&code=oauthcode123",
        "passphrase=correct-horse-battery-staple",
    ]
)


@pytest.mark.parametrize("sanitizer", [redact_text, redact_log_text])
def test_corpus_is_clean_after_redaction(sanitizer):
    sanitized = sanitizer(CORPUS)
    assert leak_violations(sanitized) == [], sanitized


@pytest.mark.parametrize(
    "secret",
    [
        "?key=LHQUERYSECRET-ABCDEF123456",
        "&key=AMPERSAND-KEY-1234567890",
        '"key": "JSON-KEY-1234567890"',
        "key=PLAIN-KEY-1234567890",
        "azure_key=0123456789abcdef0123456789abcdef",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "sk_live_51H8xQ2abcdefghijklmnop",
        "hf_abcdefghijklmnopqrstuvwxyz012345",
        "glpat-abcdefghijklmnopqrst",
        "Cookie: sid=abc123; theme=dark; csrf=zzz999",
        "Set-Cookie: session=abc123; Path=/; HttpOnly",
        "?sig=deadbeef&code=oauthcode123",
    ],
)
def test_individual_patterns_are_redacted(secret):
    assert secret not in redact_text(secret)
    assert leak_violations(redact_text(secret)) == []


def test_non_secret_key_lookalikes_are_untouched():
    text = "key_fp=sha256:a851d032 monkey=banana hotkey=ctrl key_error=none"
    assert redact_text(text) == text


def _reset_observability(monkeypatch):
    import src.ai.observability as obs

    monkeypatch.setattr(obs, "_CONFIGURED", False)
    return obs


def test_observability_fallback_handler_redacts(monkeypatch, capsys):
    obs = _reset_observability(monkeypatch)
    root = logging.getLogger()
    liuhao = logging.getLogger("liuhao")
    saved_root = list(root.handlers)
    saved_liuhao = list(liuhao.handlers)
    for handler in saved_root:
        root.removeHandler(handler)
    for handler in saved_liuhao:
        liuhao.removeHandler(handler)
    try:
        capsys.readouterr()
        logger = obs.get_logger("leaktest")
        logger.error("auth failed Bearer sk-LHLEAK-1234567890abcdef ?key=SECRET-123456")
        err = capsys.readouterr().err
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_root:
            root.addHandler(handler)
        for handler in list(liuhao.handlers):
            liuhao.removeHandler(handler)
        for handler in saved_liuhao:
            liuhao.addHandler(handler)

    assert "sk-LHLEAK-1234567890abcdef" not in err
    assert "SECRET-123456" not in err
    assert "***REDACTED***" in err


def test_no_duplicate_handler_when_root_is_configured(monkeypatch):
    obs = _reset_observability(monkeypatch)
    root = logging.getLogger()
    liuhao = logging.getLogger("liuhao")
    sentinel = logging.NullHandler()
    saved_root = list(root.handlers)
    saved_liuhao = list(liuhao.handlers)
    for handler in saved_root:
        root.removeHandler(handler)
    for handler in saved_liuhao:
        liuhao.removeHandler(handler)
    root.addHandler(sentinel)
    try:
        obs.get_logger("dup-check")
        assert list(liuhao.handlers) == []
    finally:
        root.removeHandler(sentinel)
        for handler in saved_root:
            root.addHandler(handler)
        for handler in saved_liuhao:
            liuhao.addHandler(handler)


def test_gateway_capture_redacts_and_rotates(tmp_path):
    from src.desktop.service import _GatewayLogCapture

    path = tmp_path / "desktop-gateway.log"
    capture = _GatewayLogCapture(path, max_bytes=320)
    try:
        line = (
            b"ERROR openai.AuthenticationError: Bearer sk-LHLEAK-1234567890abcdef "
            b"https://example.invalid/keys?key=PLAINSECRET-ABCDEF123456\n"
        )
        for _ in range(12):
            capture.write(line)
    finally:
        capture.close()

    files = sorted(tmp_path.glob("desktop-gateway.log*"))
    assert path.exists()
    assert any(name.name.endswith(".1") for name in files)
    for item in files:
        text = item.read_text(encoding="utf-8", errors="replace")
        assert "sk-LHLEAK-1234567890abcdef" not in text
        assert "PLAINSECRET-ABCDEF123456" not in text
        assert leak_violations(text) == []


def test_gateway_capture_uses_the_shared_rotating_writer(tmp_path):
    """落盘必须复用 ``RotatingTextWriter``，而不是另写一套轮转。"""
    from src.desktop.service import _GatewayLogCapture
    from src.logging_config import RotatingTextWriter

    capture = _GatewayLogCapture(tmp_path / "desktop-gateway.log")
    try:
        assert isinstance(capture._writer, RotatingTextWriter)
    finally:
        capture.close()


def test_scrub_log_file_rewrites_legacy_plaintext(tmp_path):
    from src.logging_config import scrub_log_file

    path = tmp_path / "desktop-gateway.log"
    path.write_text(
        "auth Bearer sk-LEGACY-1234567890abcdef ?key=LEGACY-QUERY-123456\n",
        encoding="utf-8",
    )

    assert scrub_log_file(path) is True
    text = path.read_text(encoding="utf-8")
    assert "sk-LEGACY-1234567890abcdef" not in text
    assert "LEGACY-QUERY-123456" not in text
    assert leak_violations(text) == []
    # 已经干净的文件不应反复改写（启动清理的幂等性）。
    assert scrub_log_file(path) is False


def test_scrub_legacy_log_leaks_covers_backups_and_runs_once(tmp_path):
    from src.common.redaction import REDACTION_VERSION
    from src.logging_config import LEGACY_SCRUB_MARKER_PREFIX, scrub_legacy_log_leaks

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for name in ("liuhao.log", "liuhao.log.1", "desktop-gateway.log"):
        (log_dir / name).write_text("key=PLAINSECRET-ABCDEF123456\n", encoding="utf-8")
    outsider = log_dir / "keep.db"
    outsider.write_text("key=PLAINSECRET-ABCDEF123456\n", encoding="utf-8")

    rewritten = scrub_legacy_log_leaks(log_dir)

    assert {item.name for item in rewritten} == {
        "liuhao.log",
        "liuhao.log.1",
        "desktop-gateway.log",
    }
    for name in ("liuhao.log", "liuhao.log.1", "desktop-gateway.log"):
        text = (log_dir / name).read_text(encoding="utf-8")
        assert "PLAINSECRET-ABCDEF123456" not in text
        assert leak_violations(text) == []
    # 非日志文件（数据库/导出包）不能被改写。
    assert "PLAINSECRET-ABCDEF123456" in outsider.read_text(encoding="utf-8")
    assert (log_dir / f"{LEGACY_SCRUB_MARKER_PREFIX}{REDACTION_VERSION}").is_file()

    # marker 生效后不再扫第二轮。
    (log_dir / "liuhao.log").write_text("key=NEWLEAK-ABCDEF123456\n", encoding="utf-8")
    assert scrub_legacy_log_leaks(log_dir) == []
    assert "NEWLEAK-ABCDEF123456" in (log_dir / "liuhao.log").read_text(encoding="utf-8")


def test_log_config_load_scrubs_legacy_logs(tmp_path, monkeypatch):
    """网关入口读配置时就完成一次性清理，用户无需手动删日志。"""
    from src.logging_config import DEFAULT_LOG_CONFIG, load_uvicorn_log_config

    data_dir = tmp_path / "data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    leak = log_dir / "desktop-gateway.log"
    leak.write_text("?key=LOADTIME-SECRET-123456\n", encoding="utf-8")

    load_uvicorn_log_config(DEFAULT_LOG_CONFIG)

    assert "LOADTIME-SECRET-123456" not in leak.read_text(encoding="utf-8")
