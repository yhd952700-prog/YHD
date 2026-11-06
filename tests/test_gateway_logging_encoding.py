"""回归：中文 Windows（cp936）下首启不得因读日志配置而崩溃。

背景
----
``config/logging.yaml`` 是 UTF-8（带中文注释）。uvicorn 自己的 YAML 分支用
``open(self.log_config)``，不带 ``encoding=`` —— 在中文 Windows 上默认编码是
cp936，于是导入 app 之前就抛 ``UnicodeDecodeError``。分发流程此前靠人工设
``PYTHONUTF8=1`` 绕过，但双击 exe 的真实用户不会设。

本文件用一个"敌意环境"模拟 cp936：任何**省略 encoding** 的文本读取都按
cp936 解码（``builtins.open`` + ``io.open`` + ``locale.getpreferredencoding``）。
只要有人把读取改回 ``open(path)`` 或把路径直接交给 uvicorn，这里就会失败。
"""
from __future__ import annotations

import builtins
import io
import locale
from pathlib import Path

import pytest
import yaml

from src.logging_config import (
    DEFAULT_LOG_CONFIG,
    load_uvicorn_log_config,
    resolve_log_config_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cp936_default(monkeypatch):
    """让所有省略 encoding 的文本读取表现得像中文 Windows。"""
    real_io_open = io.open

    def hostile_io_open(
        file,
        mode="r",
        buffering=-1,
        encoding=None,
        errors=None,
        newline=None,
        closefd=True,
        opener=None,
    ):
        if "b" not in mode and encoding is None:
            encoding = "cp936"
        return real_io_open(
            file, mode, buffering, encoding, errors, newline, closefd, opener
        )

    def hostile_builtin_open(file, mode="r", *args, **kwargs):
        return hostile_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", hostile_io_open)
    monkeypatch.setattr(builtins, "open", hostile_builtin_open)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp936")
    return hostile_builtin_open


def test_harness_reproduces_the_original_crash(cp936_default) -> None:
    """先证明这个模拟真的会复现故障，否则后面的通过没有意义。"""
    with pytest.raises(UnicodeDecodeError):
        yaml.safe_load(open(DEFAULT_LOG_CONFIG))


def test_loader_reads_utf8_under_cp936(cp936_default) -> None:
    config = load_uvicorn_log_config(DEFAULT_LOG_CONFIG)
    assert isinstance(config, dict)
    assert config["handlers"]["app_file"]["encoding"] == "utf-8"
    assert config["root"]["handlers"] == ["console", "app_file", "error_file"]


def test_loader_keeps_chinese_values_intact(cp936_default, tmp_path) -> None:
    """中文不只在注释里：即使将来配置里出现中文值，也必须按 UTF-8 读。"""
    path = tmp_path / "logging.yaml"
    path.write_text(
        "version: 1\n"
        "formatters:\n"
        "  text:\n"
        "    format: '%(message)s 中文日志'\n"
        "handlers: {}\n"
        "root:\n"
        "  level: INFO\n",
        encoding="utf-8",
    )
    config = load_uvicorn_log_config(path)
    assert "中文日志" in config["formatters"]["text"]["format"]


def test_desktop_runner_passes_a_mapping_to_uvicorn(cp936_default) -> None:
    """``_runner`` 是安装版真实入口：路径必须在进程内读成映射。"""
    from src.desktop._runner import build_config

    config = build_config("127.0.0.1", 8080, str(DEFAULT_LOG_CONFIG))
    assert isinstance(config.log_config, dict), (
        "把路径交给 uvicorn 会让它按 cp936 读 UTF-8 YAML（首启崩溃）"
    )


def test_gateway_entry_passes_a_mapping_to_uvicorn(
    cp936_default, monkeypatch, tmp_path
) -> None:
    """``python -m src.gateway`` 也必须传映射，而不是路径。"""
    import src.gateway.__main__ as entry

    captured: dict = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(entry, "resolve_timeout_options", lambda: {})
    monkeypatch.setattr(entry, "LOG_DIR", tmp_path)
    monkeypatch.setenv("LIUHAO_LOG_CONFIG", str(DEFAULT_LOG_CONFIG))
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8080")

    assert entry.main() == 0
    assert isinstance(captured.get("log_config"), dict)


def test_env_override_points_at_the_installed_copy(monkeypatch, tmp_path) -> None:
    installed = tmp_path / "config" / "logging.yaml"
    monkeypatch.setenv("LIUHAO_LOG_CONFIG", str(installed))
    assert resolve_log_config_path(DEFAULT_LOG_CONFIG) == installed


def test_launcher_does_not_hand_the_path_to_uvicorn_cli() -> None:
    """启动器不再调 ``python -m uvicorn ... --log-config``。

    那样做等于把 cp936 读取重新引回真实入口；改走 ``python -m src.gateway``。
    """
    source = (REPO_ROOT / "scripts" / "start_liuhao.py").read_text(encoding="utf-8")
    child_block = source.split("gateway_env = dict(os.environ)")[1].split(
        "_wait_gateway_ready"
    )[0]
    assert '"--log-config"' not in child_block
    assert '"uvicorn"' not in child_block
    assert '"src.gateway"' in child_block
