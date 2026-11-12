"""Regression tests for the ONE logging path that is actually wired.

Why this file exists
--------------------
The repository previously carried *two* logging configuration modules
(``src/logging_config.py`` and ``src/observability/logging_utils.py``) plus two
unused YAML files. None of them was called from any entry point, so the running
product wrote logs through a bare ``FileHandler`` (desktop shell) or an
append-only stdout capture (gateway) -- both unbounded. Two configurations that
look usable but are never loaded are worse than none: they are two wrong
answers for the next person who needs to add a log line.

The fix is a single real path -- ``config/logging.yaml``, loaded by every
gateway entry point -- with size-bounded rotation. These tests assert:

1. the configuration declares the documented rotation (10 MiB × 5, no
   compression) and keeps ``disable_existing_loggers: false``;
2. applying that configuration for real rotates files at the boundary;
3. every production entry point actually loads it.

If someone "simplifies" the YAML back to a bare ``FileHandler``, or removes
``--log-config`` from a launcher, one of these fails.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "logging.yaml"

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_config_declares_bounded_rotation() -> None:
    config = _load_config()
    assert config["disable_existing_loggers"] is False, (
        "Must stay False: uvicorn applies this config in-process, and the "
        "default would silence every already-created logger."
    )
    for name in ("app_file", "error_file"):
        handler = config["handlers"][name]
        assert (
            handler["class"]
            == "src.logging_config.MultiProcessRotatingFileHandler"
        ), "must be the multi-process-safe handler (see P1-7)"
        assert handler["maxBytes"] == MAX_BYTES
        assert handler["backupCount"] == BACKUP_COUNT
    assert config["root"]["handlers"] == ["console", "app_file", "error_file"]


def test_wired_config_really_rotates(tmp_path: Path) -> None:
    """Apply the real config (paths/limits redirected) and cross the boundary.

    Runs in a subprocess so ``dictConfig`` cannot reconfigure this pytest
    process's loggers and leak into unrelated tests.
    """
    config = _load_config()
    for name in ("app_file", "error_file"):
        config["handlers"][name]["filename"] = str(tmp_path / f"{name}.log")
        config["handlers"][name]["maxBytes"] = 8 * 1024  # 400 lines cross this
    config["handlers"]["console"]["level"] = "CRITICAL"  # keep stdout quiet

    config_file = tmp_path / "logging.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")

    script = (
        "import logging, logging.config, sys, yaml\n"
        "cfg = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))\n"
        "logging.config.dictConfig(cfg)\n"
        "log = logging.getLogger('rotation.probe')\n"
        "for i in range(400):\n"
        "    log.info('line %05d %s', i, 'x' * 120)\n"
        "logging.shutdown()\n"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(config_file)],
        check=True,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )

    current = tmp_path / "app_file.log"
    assert current.is_file(), "the INFO handler never wrote anything"
    assert current.stat().st_size <= 8 * 1024 + 1024
    assert (tmp_path / "app_file.log.1").is_file(), "rotation did not happen"
    rotated = sorted(
        path
        for path in tmp_path.glob("app_file.log.*")
        if not path.name.endswith(".lock")  # 多进程轮转用的锁文件，不是备份
    )
    assert len(rotated) <= BACKUP_COUNT

    # ``delay: true`` + level ERROR and no error record -> no empty error file.
    assert not (tmp_path / "error_file.log").exists()


def test_every_gateway_entry_point_loads_the_config() -> None:
    """A config nobody loads is the defect this change removed.

    读取路径已从「把 YAML 路径交给 uvicorn」改成「按 UTF-8 读成映射后交给
    uvicorn」：uvicorn 的路径分支用 ``open()`` 不带 encoding，中文 Windows
    的 cp936 会在首启时解码 UTF-8 文件失败。因此这里断言每个入口要么引用
    配置文件并自带 UTF-8 读取，要么确实是安装版的路径参数生产者。
    """
    entry_points = {
        "scripts/start_liuhao.py": ("logging.yaml", "LIUHAO_LOG_CONFIG"),
        "src/desktop/service.py": ("--log-config", "logging.yaml"),
        "src/desktop/_runner.py": ("load_uvicorn_log_config", "log_config"),
        "src/gateway/__main__.py": ("load_uvicorn_log_config", "logging.yaml"),
    }
    for relative, markers in entry_points.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text, f"{relative} does not reference {marker}"
    # 启动器不再把路径交给 uvicorn 的 CLI：那会按 cp936 读 UTF-8 YAML。
    launcher = (REPO_ROOT / "scripts" / "start_liuhao.py").read_text(encoding="utf-8")
    child_block = launcher.split("gateway_env = dict(os.environ)")[1].split(
        "_wait_gateway_ready"
    )[0]
    assert '"-m", "uvicorn"' not in child_block
    assert "--log-config" not in child_block


def test_launcher_log_is_bounded_and_redacted(tmp_path: Path) -> None:
    """launcher.log 长期无上限：stdout 镜像必须轮转、且不能带出本机身份。

    真实缺陷：安装版启动器把 stdout/stderr 直接 ``open(..., "a")`` 写进
    ``launcher.log``，每次启动都会打印 argv、数据目录和子进程输出；既没有
    上限，也不走网关日志的脱敏。
    """
    from src.logging_config import RotatingTextWriter

    log_path = tmp_path / "launcher.log"
    writer = RotatingTextWriter(log_path, maxBytes=4096, backupCount=3)
    for index in range(300):
        writer.write(
            f"LINE {index:04d} | data=C:\\Users\\ZhangSan\\AppData\\Local\\LiuHao\\data "
            f"LIUHAO_DATA_DIR=x api_key=sk-abcdefghijklmnop | END\n"
        )
    writer.flush()

    files = sorted(
        path
        for path in tmp_path.glob("launcher.log*")
        if not path.name.endswith(".lock")
    )
    assert (tmp_path / "launcher.log").is_file()
    assert len(files) <= 1 + 3, f"backupCount=3 must keep at most 4 files: {files}"

    seen: list[str] = []
    for path in files:
        assert path.stat().st_size <= 4096, f"{path.name} exceeded maxBytes"
        text = path.read_text(encoding="utf-8")
        assert "ZhangSan" not in text, f"{path.name} leaked a user path"
        assert "LIUHAO_" not in text, f"{path.name} leaked an env var name"
        assert "sk-abcdefghijklmnop" not in text, f"{path.name} leaked a key"
        for line in text.splitlines():
            # 轮转只发生在行首：每条记录都完整，不会被拆到两个文件里。
            assert line.startswith("LINE "), line
            assert line.endswith("| END"), line
            seen.append(line[:9])

    # backupCount=3 只保留最后 4 份文件，更早的记录按设计被丢弃（不是"丢失"）；
    # 保留区间必须是连续后缀，且没有重复/空洞。
    indices = sorted(int(line.split()[1]) for line in seen)
    assert indices[-1] == 299, "the newest record was lost"
    assert indices == list(range(indices[0], 300)), (
        f"retained records are not a contiguous suffix: {len(indices)} records, "
        f"first={indices[0]}"
    )
    assert len(indices) == len(set(indices)), "records duplicated across rotation"


def test_launcher_writer_never_splits_a_line_at_rotation(tmp_path: Path) -> None:
    """行未写完时不得轮转：半行日志比超限更糟（排障会误读）。"""
    from src.logging_config import RotatingTextWriter

    log_path = tmp_path / "launcher.log"
    writer = RotatingTextWriter(log_path, maxBytes=512, backupCount=2, redact=False)
    for index in range(40):
        writer.write(f"FILL {index:04d} " + "x" * 40 + "\n")
    # 当前文件已接近上限：分两段写同一行，第二段不能触发轮转。
    writer.write("HALF-A")
    writer.write("HALF-B\n")

    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            tmp_path.glob("launcher.log*"), key=lambda item: item.name
        )
        if not path.name.endswith(".lock")
    )
    assert "HALF-AHALF-B" in combined, "a record was split across rotation"


def test_stale_backups_beyond_backup_count_are_removed(tmp_path: Path) -> None:
    """配置收窄后，旧版本留下的高序号备份必须被清掉，否则占用没有上限。"""
    from src.logging_config import RotatingTextWriter

    log_path = tmp_path / "liuhao.log"
    log_path.write_text("x" * 200 + "\n", encoding="utf-8")
    for index in range(1, 9):
        (tmp_path / f"liuhao.log.{index}").write_text(
            f"backup {index}\n" * 400, encoding="utf-8"
        )
    writer = RotatingTextWriter(log_path, maxBytes=64, backupCount=2, redact=False)
    writer.write("trigger rotation\n")

    kept = sorted(
        path.name
        for path in tmp_path.glob("liuhao.log*")
        if not path.name.endswith(".lock")
    )
    assert kept == ["liuhao.log", "liuhao.log.1", "liuhao.log.2"], kept


def test_desktop_shell_log_redacts_local_identity(tmp_path: Path, monkeypatch) -> None:
    """托盘壳的 desktop.log 也必须脱敏（它同样会打印数据目录）。"""
    import logging

    from src.desktop import app as desktop_app

    monkeypatch.setattr(desktop_app, "LOG_DIR", tmp_path)
    pkg_logger = logging.getLogger("src.desktop")
    had_handlers = list(pkg_logger.handlers)
    pkg_logger.handlers.clear()
    try:
        desktop_app._configure_logging()
        pkg_logger.info(
            "gateway log at C:\\Users\\ZhangSan\\AppData\\Local\\LiuHao\\data "
            "LIUHAO_DATA_DIR=x"
        )
        for handler in pkg_logger.handlers:
            handler.flush()
        text = (tmp_path / "desktop.log").read_text(encoding="utf-8")
    finally:
        for handler in pkg_logger.handlers:
            handler.close()
        pkg_logger.handlers[:] = had_handlers

    assert "ZhangSan" not in text
    assert "LIUHAO_" not in text
    assert "gateway log at" in text
