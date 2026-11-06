"""安装版「程序目录首启零残留」的回归测试。

首启取证发现程序目录多出空 ``logs/`` 与若干 ``__pycache__``。修复分两处：

  * ``scripts/launch_installed.py`` 在导入任何应用模块之前就地关闭
    ``sys.dont_write_bytecode``（环境变量只对子进程生效）；
  * 安装包生成的 ``LiuHao.bat`` 设置 ``PYTHONDONTWRITEBYTECODE=1``，
    让 runtime 里的 Python 子进程也不落字节码。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_importing_installed_launcher_disables_bytecode(monkeypatch):
    """导入入口即关闭字节码写入，而不是等到 prepare_environment 才设。"""
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    from scripts import launch_installed

    importlib.reload(launch_installed)
    assert sys.dont_write_bytecode is True


def test_staged_windows_launcher_sets_no_bytecode(tmp_path):
    """安装包里的 LiuHao.bat 必须设置 PYTHONDONTWRITEBYTECODE=1（且仍 ASCII）。"""
    spec = importlib.util.spec_from_file_location(
        "liuhao_stage_payload_under_test", ROOT / "packaging" / "stage_payload.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._write_start_bat(tmp_path)
    raw = (tmp_path / "LiuHao.bat").read_bytes()
    text = raw.decode("ascii")  # 写盘用 encoding="ascii"，这里同时守住约定
    assert "set PYTHONDONTWRITEBYTECODE=1" in text.replace("\r\n", "\n")
