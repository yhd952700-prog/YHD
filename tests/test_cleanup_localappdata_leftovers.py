"""The one-off LOCALAPPDATA cleanup script must be explicit and narrow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "cleanup_localappdata_test_leftovers.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup_leftovers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "LiuHao"
    (base / "data.after-smoke-abc").mkdir(parents=True)
    (base / "data.after-smoke-abc" / "auth_secrets.json").write_text(
        "{}", encoding="utf-8"
    )
    (base / "data.bak-def").mkdir()
    (base / "data.bak-def" / "model_config.key").write_text("k", encoding="utf-8")
    # Product data and unrelated dirs must survive.
    (base / "data").mkdir()
    (base / "data" / "notes.txt").write_text("keep", encoding="utf-8")
    (base / "logs").mkdir()
    (base / "logs" / "app.log").write_text("keep", encoding="utf-8")
    return base


def test_dry_run_lists_but_never_deletes(root, capsys):
    script = _load_script()
    assert script.main(["--root", str(root), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "data.after-smoke-abc" in output
    assert "data.bak-def" in output
    assert "auth_secrets.json" in output
    assert (root / "data.after-smoke-abc").is_dir()
    assert (root / "data.bak-def").is_dir()


def test_explicit_yes_deletes_only_test_copies(root, capsys):
    script = _load_script()
    assert script.main(["--root", str(root), "--yes"]) == 0
    assert not (root / "data.after-smoke-abc").exists()
    assert not (root / "data.bak-def").exists()
    assert (root / "data" / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert (root / "logs" / "app.log").read_text(encoding="utf-8") == "keep"


def test_prompt_abort_keeps_everything(root, monkeypatch, capsys):
    script = _load_script()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")
    assert script.main(["--root", str(root)]) == 1
    assert (root / "data.after-smoke-abc").is_dir()
    assert (root / "data.bak-def").is_dir()
    assert "Aborted" in capsys.readouterr().out
