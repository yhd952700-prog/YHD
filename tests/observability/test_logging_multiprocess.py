"""日志多进程安全与数据目录隔离的回归测试（P1-7）。

真实缺陷：``config/logging.yaml`` 用相对路径 ``logs/liuhao.log``，两个网关从
同一仓库根启动时写同一个文件；stdlib ``RotatingFileHandler`` 一直持有句柄，
Windows 上另一进程持有该文件时 ``doRollover`` 的改名必然
``PermissionError [WinError 32]``，该条记录被丢弃，文件继续超过 10 MiB
（实测单实例 314 次 ``--- Logging error ---``，``.2/.3/.4`` 缺失）。

这里用**两个真实子进程**同时写同一个日志文件、用很小的 ``maxBytes`` 强制
轮转，断言：记录一条不少、没有重复、每个文件都不超过上限、``.1/.2/.3`` 都
真实存在、stderr 里没有 ``Logging error``。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from src.logging_config import DEFAULT_LOG_CONFIG, load_uvicorn_log_config

REPO_ROOT = Path(__file__).resolve().parents[2]

WRITER_SCRIPT = """
import logging, logging.config, sys, yaml
config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
logging.config.dictConfig(config)
log = logging.getLogger("mp.probe." + sys.argv[2])
for index in range(int(sys.argv[3])):
    log.info("P%s %06d", sys.argv[2], index)
logging.shutdown()
"""


def _spawn_writer(config_file: Path, tag: str, count: int):
    return subprocess.Popen(
        [sys.executable, "-c", WRITER_SCRIPT, str(config_file), tag, str(count)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )


def _write_runtime_config(tmp_path: Path, log_path: Path, max_bytes: int, backups: int) -> Path:
    """用仓库里真实的 logging.yaml，只把路径/上限改到 tmp，走真实 dictConfig。"""
    config = load_uvicorn_log_config(DEFAULT_LOG_CONFIG)
    for name in ("app_file", "error_file"):
        config["handlers"][name]["filename"] = str(log_path.with_name(f"{name}.log"))
        config["handlers"][name]["maxBytes"] = max_bytes
        config["handlers"][name]["backupCount"] = backups
    config["handlers"]["console"]["level"] = "CRITICAL"
    config_file = tmp_path / "logging.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_file


def test_two_processes_share_one_file_without_losing_records(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    log_path = logs / "liuhao.log"
    max_bytes = 32_768
    backups = 12
    per_process = 1_500

    config_file = _write_runtime_config(tmp_path, log_path, max_bytes, backups)

    # 两个独立的 Python 进程**同时**写同一个文件。
    first = _spawn_writer(config_file, "1", per_process)
    second = _spawn_writer(config_file, "2", per_process)
    _, first_err = first.communicate(timeout=180)
    _, second_err = second.communicate(timeout=180)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    for stderr in (first_err, second_err):
        assert "Logging error" not in stderr, stderr
        assert "PermissionError" not in stderr, stderr
        assert "WinError 32" not in stderr, stderr

    rotated = sorted(
        path
        for path in logs.glob("app_file.log*")
        if not path.name.endswith(".lock")
    )
    assert len(rotated) >= 4, f"rotation never produced backups: {rotated}"

    messages = []
    for path in rotated:
        for line in path.read_text(encoding="utf-8").splitlines():
            for tag in ("1", "2"):
                marker = line.rfind(f"P{tag} ")
                if marker >= 0:
                    messages.append(line[marker:].strip())
    expected = {f"P1 {index:06d}" for index in range(per_process)} | {
        f"P2 {index:06d}" for index in range(per_process)
    }
    assert len(messages) == 2 * per_process, (
        f"expected {2 * per_process} records, got {len(messages)}"
    )
    assert set(messages) == expected, "records were lost or duplicated across rotation"

    for path in rotated:
        assert path.stat().st_size <= max_bytes, f"{path.name} exceeded maxBytes"
    for suffix in ("1", "2", "3"):
        assert (logs / f"app_file.log.{suffix}").is_file(), (
            f"rotation skipped app_file.log.{suffix} (the old WinError 32 symptom)"
        )


def test_three_processes_with_background_task_keep_every_record(tmp_path):
    """网关 + 桌面壳 + 后台任务同时写一个文件：不丢、不重、不留半行。

    这是用户现场的真实组合：网关进程写 ``liuhao.log``，桌面壳与后台任务
    也会写同一个数据目录下的日志。断言与双进程用例一致，但并发度更高。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    log_path = logs / "liuhao.log"
    max_bytes = 32_768
    backups = 12
    per_process = 1_000

    config_file = _write_runtime_config(tmp_path, log_path, max_bytes, backups)
    writers = [_spawn_writer(config_file, tag, per_process) for tag in ("1", "2", "3")]
    stderrs = [proc.communicate(timeout=240)[1] for proc in writers]
    for proc, stderr in zip(writers, stderrs, strict=True):
        assert proc.returncode == 0, stderr
        assert "Logging error" not in stderr, stderr
        assert "PermissionError" not in stderr, stderr

    rotated = sorted(
        path
        for path in logs.glob("app_file.log*")
        if not path.name.endswith(".lock")
    )
    messages = []
    half_lines = 0
    for path in rotated:
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            half_lines += 1
        for line in text.splitlines():
            assert " | " in line, f"half-written record: {line!r}"
            marker = line.rfind("P")
            if marker >= 0:
                messages.append(line[marker:].strip())
    assert half_lines == 0, "rotated file ends with a partial record"

    expected = {
        f"P{tag} {index:06d}" for tag in ("1", "2", "3") for index in range(per_process)
    }
    assert len(messages) == 3 * per_process, (
        f"expected {3 * per_process} records, got {len(messages)}"
    )
    assert set(messages) == expected, "records were lost or duplicated across rotation"
    for path in rotated:
        assert path.stat().st_size <= max_bytes, f"{path.name} exceeded maxBytes"


def test_error_channel_rotates_without_mixing_info(tmp_path):
    """``error.log`` 单独轮转：多进程并发时 ERROR 不丢，也不能混进 INFO。

    ``error.log`` 是用户排障的"第一站"，但它和 ``liuhao.log`` 是两个独立的
    handler；只测 INFO 通道不能证明 ERROR 通道的上限与级别过滤也成立。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    log_path = logs / "liuhao.log"
    max_bytes = 16_384
    backups = 12
    per_process = 800
    config_file = _write_runtime_config(tmp_path, log_path, max_bytes, backups)

    script = WRITER_SCRIPT.replace(
        'log.info("P%s %06d", sys.argv[2], index)',
        'log.info("I%s %06d", sys.argv[2], index)\n'
        '    log.error("E%s %06d", sys.argv[2], index)',
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(config_file), tag, str(per_process)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        for tag in ("1", "2")
    ]
    for proc in procs:
        _, stderr = proc.communicate(timeout=240)
        assert proc.returncode == 0, stderr
        assert "Logging error" not in stderr, stderr

    rotated = sorted(logs.glob("error_file.log*"))
    rotated = [path for path in rotated if not path.name.endswith(".lock")]
    assert len(rotated) >= 2, f"error.log never rotated: {rotated}"
    messages = []
    for path in rotated:
        for line in path.read_text(encoding="utf-8").splitlines():
            assert "| ERROR" in line, f"non-ERROR record leaked into error.log: {line!r}"
            for tag in ("1", "2"):
                marker = line.rfind(f"E{tag} ")
                if marker >= 0:
                    messages.append(line[marker:].strip())
    expected = {f"E1 {index:06d}" for index in range(per_process)} | {
        f"E2 {index:06d}" for index in range(per_process)
    }
    assert len(messages) == 2 * per_process, f"error records lost: {len(messages)}"
    assert set(messages) == expected, "error records lost or duplicated across rotation"
    for path in rotated:
        assert path.stat().st_size <= max_bytes, f"{path.name} exceeded maxBytes"


def test_data_dir_relative_filenames_never_resolve_to_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_LOG_DIR", raising=False)
    config = load_uvicorn_log_config(DEFAULT_LOG_CONFIG)

    for name in ("app_file", "error_file"):
        filename = Path(config["handlers"][name]["filename"])
        assert filename.is_absolute()
        assert filename.parent == (tmp_path / "logs").resolve()
        assert REPO_ROOT not in filename.parents, "log path leaked into the repo"

    assert (
        config["handlers"]["app_file"]["class"]
        == "src.logging_config.MultiProcessRotatingFileHandler"
    )


def test_log_dir_env_isolates_instances(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    instance = tmp_path / "instance-a"
    monkeypatch.setenv("LIUHAO_LOG_DIR", str(instance))
    config = load_uvicorn_log_config(DEFAULT_LOG_CONFIG)
    filename = Path(config["handlers"]["app_file"]["filename"])
    assert filename == (instance / "liuhao.log").resolve()


def test_absolute_filename_is_rebased_into_isolated_data_dir(monkeypatch, tmp_path):
    """安装包的绝对日志路径不得让隔离实例污染真实数据目录（回归断言）。

    soak 证据里的缺陷：``filename`` 写死为真实数据目录绝对路径时，
    ``resolve_log_file`` 直接原样返回，即使设置了 ``LIUHAO_DATA_DIR``。
    """
    real_data = tmp_path / "real-data"
    config_path = tmp_path / "logging.yaml"
    config_path.write_text(
        "version: 1\n"
        "disable_existing_loggers: false\n"
        "handlers:\n"
        "  app_file:\n"
        "    class: src.logging_config.MultiProcessRotatingFileHandler\n"
        f"    filename: {real_data / 'logs' / 'liuhao.log'}\n"
        "    maxBytes: 10485760\n"
        "    backupCount: 5\n"
        "root:\n"
        "  level: INFO\n"
        "  handlers: [app_file]\n",
        encoding="utf-8",
    )
    isolated = tmp_path / "isolated"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(isolated))
    monkeypatch.delenv("LIUHAO_LOG_DIR", raising=False)

    filename = Path(
        load_uvicorn_log_config(config_path)["handlers"]["app_file"]["filename"]
    )
    assert filename == (isolated / "logs" / "liuhao.log").resolve()
    assert real_data not in filename.parents

    # 显式 LIUHAO_LOG_DIR 优先级更高，同样必须压过配置里的绝对路径。
    instance = tmp_path / "instance"
    monkeypatch.setenv("LIUHAO_LOG_DIR", str(instance))
    filename = Path(
        load_uvicorn_log_config(config_path)["handlers"]["app_file"]["filename"]
    )
    assert filename == (instance / "liuhao.log").resolve()
    assert real_data not in filename.parents


def test_old_installed_config_is_upgraded_at_load_time(monkeypatch, tmp_path):
    """旧安装版数据目录里的 ``RotatingFileHandler`` 配置也必须被换掉。"""
    old = tmp_path / "logging.yaml"
    old.write_text(
        "version: 1\n"
        "disable_existing_loggers: false\n"
        "handlers:\n"
        "  app_file:\n"
        "    class: logging.handlers.RotatingFileHandler\n"
        "    filename: logs/liuhao.log\n"
        "    maxBytes: 10485760\n"
        "    backupCount: 5\n"
        "root:\n"
        "  level: INFO\n"
        "  handlers: [app_file]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_LOG_DIR", raising=False)

    config = load_uvicorn_log_config(old)
    handler = config["handlers"]["app_file"]
    assert handler["class"] == "src.logging_config.MultiProcessRotatingFileHandler"
    assert Path(handler["filename"]) == (tmp_path / "logs" / "liuhao.log").resolve()
