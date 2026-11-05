"""Atomic whole-file replacement.

Regression: ``JSONFileBackend._save`` and ``ObservabilityStore._save`` both
reopened the live file with mode ``"w"``, which truncates it before a single
new byte is written. An interruption mid-write therefore destroyed the previous
(valid) store rather than leaving it intact. These tests force the failure
point and prove the old bytes are still there.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.observability.models import Span
from src.observability.store import ObservabilityStore
from src.storage.backends import JSONFileBackend, StorageEntry


def _fail_replace(*_args, **_kwargs):
    raise OSError("simulated power loss during os.replace")


def test_atomic_write_replaces_content_without_leaving_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "payload.json"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, '{"new": true}')

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_atomic_write_failure_keeps_the_previous_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"original")

    monkeypatch.setattr("src.common.atomic_write.os.replace", _fail_replace)
    with pytest.raises(OSError, match="simulated power loss"):
        atomic_write_bytes(target, b"replacement-that-must-not-land")

    assert target.read_bytes() == b"original"
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def _entry(key: str, value: str) -> StorageEntry:
    now = time.time()
    return StorageEntry(key=key, value=value, created_at=now, updated_at=now)


def test_json_backend_failed_save_leaves_the_previous_store_loadable(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "entries.json"
    backend = JSONFileBackend(str(path))
    backend.put("k1", _entry("k1", "one"))
    previous_bytes = path.read_bytes()

    monkeypatch.setattr("src.common.atomic_write.os.replace", _fail_replace)
    with pytest.raises(OSError, match="simulated power loss"):
        backend.put("k2", _entry("k2", "two"))

    assert path.read_bytes() == previous_bytes
    reloaded = JSONFileBackend(str(path))
    assert reloaded.get("k1") is not None
    assert reloaded.get("k2") is None
    assert reloaded.verify_integrity() is True


def test_observability_store_failed_save_keeps_the_previous_store_loadable(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "spans.json"
    store = ObservabilityStore(str(path))
    store.emit_span(Span(span_id="s1", trace_id="t1", name="first"))
    previous_bytes = path.read_bytes()

    monkeypatch.setattr("src.common.atomic_write.os.replace", _fail_replace)
    with pytest.raises(OSError, match="simulated power loss"):
        store.emit_span(Span(span_id="s2", trace_id="t1", name="second"))

    assert path.read_bytes() == previous_bytes
    reloaded = ObservabilityStore(str(path))
    assert reloaded.get_span("s1") is not None
    assert reloaded.get_span("s2") is None
    assert reloaded.verify_integrity() is True
