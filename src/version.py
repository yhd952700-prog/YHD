"""产品版本号唯一来源。

安装器、payload、健康接口与启动日志都从这里取值；``pyproject.toml`` 与
安装器脚本在构建时由 ``packaging/build_windows_installer.ps1`` 校验一致性
（见 ``tests/test_version_consistency.py``）。
"""

APP_VERSION = "0.1.2"

__all__ = ["APP_VERSION"]
