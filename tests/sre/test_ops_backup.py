"""The operational backup tool: restore must not be a write primitive.

Three things this file pins down, because each is a way a "recovery" path can
hurt rather than help:

1. Extraction cannot escape the target directory. A tar member named
   ``../../../../Windows/Temp/x`` used to be unpacked wherever the archive
   asked, so "restore this backup" meant "let whoever built the archive choose
   where we write".
2. Restore refuses an archive that does not verify, instead of unpacking
   whatever it turns out to contain.
3. Windows actually works: the scratch directory used to be hardcoded to
   ``/tmp``.

Every case below is executed, not asserted from reading the code.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "backup.py"


def _load_module():
    """Import scripts/ops/backup.py by path (it is a script, not a package)."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location("ops_backup", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ops_backup():
    return _load_module()


def _manager(ops_backup, tmp_path, **backup_kwargs):
    defaults = dict(
        storage_path=str(tmp_path / "backup-store"),
        encryption=False,
        encryption_key="",
        verify_after_backup=True,
    )
    defaults.update(backup_kwargs)
    return ops_backup.BackupManager(
        SimpleNamespace(backup=SimpleNamespace(**defaults))
    )


def _write_archive(path: Path, members: dict) -> Path:
    """Write a tar.gz whose member NAMES are the keys and file contents the values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in members.items():
            staging = path.parent / "_staging_member"
            staging.write_bytes(payload)
            tar.add(staging, arcname=name)
            staging.unlink()
    return path


class TestRestoreCannotEscapeTheTarget:
    def test_a_dot_dot_member_is_refused_and_writes_nothing(self, ops_backup, tmp_path):
        target = tmp_path / "restore-target"
        outside = tmp_path / "must-not-exist.txt"
        archive = _write_archive(
            tmp_path / "store" / "evil.tar.gz",
            {
                "backup/metadata.json": b"{}",
                # escapes target from inside \Windows\Temp style roots
                "../../../../../../../../must-not-exist.txt": b"pwned",
            },
        )
        manager = _manager(ops_backup, tmp_path)

        assert manager.restore_backup(archive, target) is False
        assert not outside.exists()

    def test_reject_traversal_flags_paths_outside_the_root(self, ops_backup, tmp_path):
        reject = ops_backup._reject_traversal
        root = tmp_path / "root"

        with pytest.raises(ValueError):
            reject(["../../../../escape.txt"], root)
        with pytest.raises(ValueError):
            reject(["/absolute/path.txt"], root)

    def test_reject_traversal_allows_paths_inside_the_root(self, ops_backup, tmp_path):
        root = tmp_path / "root"
        ops_backup._reject_traversal(["root/a.txt", "root/nested/b.txt"], root)


class TestRestoreRoundTrip:
    def test_a_good_backup_restores_into_the_target(self, ops_backup, tmp_path):
        target = tmp_path / "restore-target"
        archive = _write_archive(
            tmp_path / "store" / "good.tar.gz",
            {
                "good/metadata.json": json.dumps(
                    {"backup_name": "good", "version": "1.0.0"}
                ).encode(),
                "good/data/payload.txt": b"real data",
            },
        )
        manager = _manager(ops_backup, tmp_path)

        assert manager.restore_backup(archive, target) is True
        restored = target / "good" / "data" / "payload.txt"
        assert restored.exists()
        assert restored.read_bytes() == b"real data"

    def test_an_archive_without_metadata_is_refused(self, ops_backup, tmp_path):
        target = tmp_path / "restore-target"
        archive = _write_archive(
            tmp_path / "store" / "no-meta.tar.gz",
            {"backup/data.txt": b"orphan"},
        )
        manager = _manager(ops_backup, tmp_path)

        assert manager.restore_backup(archive, target) is False
        assert not (target / "backup" / "data.txt").exists()

    def test_a_truncated_archive_is_refused(self, ops_backup, tmp_path):
        target = tmp_path / "restore-target"
        archive = _write_archive(
            tmp_path / "store" / "good.tar.gz",
            {"good/metadata.json": b"{}", "good/data.txt": b"x" * 4096},
        )
        with open(archive, "rb") as handle:
            content = handle.read()
        with open(archive, "wb") as handle:
            handle.write(content[: len(content) // 2])
        manager = _manager(ops_backup, tmp_path)

        assert manager.restore_backup(archive, target) is False


class TestWindowsScratchDirectory:
    def test_create_backup_does_not_hardcode_posix_tmp(self, ops_backup):
        source = _SCRIPT.read_text(encoding="utf-8")

        assert '/tmp/liuhao_backup_' not in source
        assert "tempfile.mkdtemp" in source


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
