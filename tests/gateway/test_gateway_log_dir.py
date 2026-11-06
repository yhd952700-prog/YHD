"""网关入口的「程序目录只读」回归测试。

两个缺陷都来自真实首启取证：

  * 安装后程序目录多出一个空 ``logs/``：``src.gateway.__main__`` 曾无条件
    ``LOG_DIR.mkdir()``，而安装版把 handler.filename 改写成用户数据目录；
  * 控制台打印 ``http://0.0.0.0:8080``：打印值必须派生自实际绑定配置。

这里的断言锁住「只建配置里真正写日志的那个目录」和「展示地址由配置推导」。
"""
from __future__ import annotations

from pathlib import Path

from src.gateway import __main__ as entry


def test_display_host_maps_wildcards_but_keeps_real_addresses():
    assert entry.display_host("127.0.0.1") == "127.0.0.1"
    assert entry.display_host("0.0.0.0") == "127.0.0.1"
    assert entry.display_host("::") == "127.0.0.1"
    assert entry.display_host("[::]") == "127.0.0.1"
    assert entry.display_host("192.168.1.20") == "192.168.1.20"


def test_resolve_log_dir_reads_absolute_handler_filename(tmp_path):
    data_logs = tmp_path / "data" / "logs"
    config = {"handlers": {"file": {"filename": str(data_logs / "liuhao.log")}}}
    assert entry.resolve_log_dir(config) == data_logs


def test_resolve_log_dir_falls_back_to_program_logs_without_config():
    assert entry.resolve_log_dir(None) == entry.LOG_DIR
    assert entry.resolve_log_dir({}) == entry.LOG_DIR


def test_main_creates_only_the_configured_log_dir(monkeypatch, tmp_path):
    """安装版配置指向数据目录时，程序目录的 ``logs`` 一个都不能建。"""
    program_logs = tmp_path / "program" / "logs"
    data_logs = tmp_path / "data" / "logs"
    config_path = tmp_path / "logging.yaml"
    config_path.write_text(
        "version: 1\n"
        "handlers:\n"
        "  file:\n"
        "    class: logging.FileHandler\n"
        f"    filename: {data_logs / 'gateway.log'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(entry, "LOG_DIR", program_logs)
    monkeypatch.setattr(entry, "resolve_timeout_options", lambda: {})

    class _NoopConfig:
        """替代真实的 ``uvicorn.Config``：它的构造函数会立刻 ``dictConfig``。

        真实启动时这正是我们要的（把 logging.yaml 生效）；但在同一个 pytest
        进程里，它会把 root 上的 handler 整体换掉，后续用例的 ``caplog`` 就
        再也收不到记录。本用例只验证"启动前的目录/打印"，不需要真的配日志。
        """

        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs

    class _NoRunServer:
        """只验证启动前的目录/打印行为，不真的绑定端口。"""

        def __init__(self, config):
            self.config = config
            self.should_exit = False

        def run(self):
            return None

    monkeypatch.setattr(entry.uvicorn, "Config", _NoopConfig)
    monkeypatch.setattr(entry.uvicorn, "Server", _NoRunServer)
    monkeypatch.setenv("LIUHAO_LOG_CONFIG", str(config_path))
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "18080")
    # 单实例锁按数据目录生效：这里用临时目录，避免与同机真实/并行测试的实例抢锁。
    monkeypatch.setenv("LIUHAO_INSTANCE_ROOT", str(tmp_path / "instance"))
    # 记录原值：main() 会直接写这个变量，避免泄漏到同进程的其它用例。
    monkeypatch.setenv("LIUHAO_LISTEN_HOST", "sentinel")

    assert entry.main() == 0
    assert data_logs.is_dir()
    assert not program_logs.exists()


def test_gateway_entry_never_mkdirs_the_program_log_dir():
    source = Path(entry.__file__).read_text(encoding="utf-8")
    assert "LOG_DIR.mkdir(" not in source
