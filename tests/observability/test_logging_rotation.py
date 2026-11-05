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
        assert handler["class"] == "logging.handlers.RotatingFileHandler"
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
    )

    current = tmp_path / "app_file.log"
    assert current.is_file(), "the INFO handler never wrote anything"
    assert current.stat().st_size <= 8 * 1024 + 1024
    assert (tmp_path / "app_file.log.1").is_file(), "rotation did not happen"
    rotated = sorted(tmp_path.glob("app_file.log.*"))
    assert len(rotated) <= BACKUP_COUNT

    # ``delay: true`` + level ERROR and no error record -> no empty error file.
    assert not (tmp_path / "error_file.log").exists()


def test_every_gateway_entry_point_loads_the_config() -> None:
    """A config nobody loads is the defect this change removed."""
    entry_points = {
        "scripts/start_liuhao.py": "--log-config",
        "src/desktop/service.py": "--log-config",
        "src/gateway/__main__.py": "log_config",
    }
    for relative, flag in entry_points.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "logging.yaml" in text, f"{relative} does not reference config/logging.yaml"
        assert flag in text, f"{relative} does not pass {flag} to uvicorn"
