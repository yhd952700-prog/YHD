"""批次 A：标记落盘 + 守卫 fail-closed。

覆盖两种必须区分的情形：

* **从未有过标记**（0.1.0/0.1.1 老库）→ 放行。否则所有老用户升级后第一次
  启动就被拦在门外；
* **有标记但读不懂 / 版本超前** → 拒绝。此时无法确认数据是否已被更高版本
  写过，继续启动等于赌「没有」。
"""

from __future__ import annotations

import json

import pytest

from src.upgrade import startup_guard
from src.upgrade.dataversion import (
    DATA_SCHEMA_FILENAME,
    DATA_SCHEMA_VERSION,
    stamp_data_schema,
)
from src.upgrade.keyformat import (
    KEY_FORMAT_FILENAME,
    KEY_FORMAT_SCHEMA,
    KEY_FORMAT_WRAPPED,
    stamp_key_format,
)
from src.upgrade.startup_guard import check_startup_data


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )


def _schema_marker(version: int) -> dict:
    return {
        "schema": "liuhao.data-schema/1",
        "version": version,
        "written_by": "0.1.2",
        "updated_at": "2026-11-09T00:00:00Z",
    }


def _format_marker(fmt: str) -> dict:
    return {
        "schema": KEY_FORMAT_SCHEMA,
        "integrity_key": fmt,
        "min_version": "0.1.2" if fmt == KEY_FORMAT_WRAPPED else "0.0.0",
        "written_by": "0.1.2",
        "updated_at": "2026-11-09T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# 情形一：从未有过标记 → 放行
# ---------------------------------------------------------------------------
def test_never_marked_data_is_allowed(tmp_path):
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is True
    assert gate.action == "missing"


def test_old_data_with_plaintext_key_is_allowed(tmp_path):
    # 0.1.1 形态：有明文密钥、没有任何标记。
    key = tmp_path / "config" / "integrity.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("0123456789abcdef0123456789abcdef", encoding="utf-8")

    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is True
    assert gate.action == "missing"


# ---------------------------------------------------------------------------
# 情形二：有标记但读不懂 → 拒绝（fail-closed）
# ---------------------------------------------------------------------------
def test_corrupt_schema_marker_refuses(tmp_path):
    _write(tmp_path / DATA_SCHEMA_FILENAME, "{ this is not json")
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is False
    assert gate.action == "marker_unreadable"
    assert "data-schema.json" in gate.message


def test_schema_marker_with_wrong_shape_refuses(tmp_path):
    _write(tmp_path / DATA_SCHEMA_FILENAME, {"schema": "other/1", "version": 2})
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is False
    assert gate.action == "marker_unreadable"


def test_corrupt_format_marker_refuses(tmp_path):
    _write(tmp_path / KEY_FORMAT_FILENAME, "not json at all")
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is False
    assert gate.action == "marker_unreadable"
    assert "data-format.json" in gate.message


def test_format_marker_with_invalid_value_refuses(tmp_path):
    _write(
        tmp_path / KEY_FORMAT_FILENAME,
        {"schema": KEY_FORMAT_SCHEMA, "integrity_key": "future-v9"},
    )
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is False
    assert gate.action == "marker_unreadable"


# ---------------------------------------------------------------------------
# 情形三：标记可读但版本超前 → 拒绝，显式开关才放行
# ---------------------------------------------------------------------------
def test_schema_ahead_refuses_and_override_allows(tmp_path):
    _write(tmp_path / DATA_SCHEMA_FILENAME, _schema_marker(DATA_SCHEMA_VERSION + 1))

    blocked = check_startup_data(tmp_path, allow_downgrade=False)
    assert blocked.ok is False
    assert blocked.action == "blocked"

    allowed = check_startup_data(tmp_path, allow_downgrade=True)
    assert allowed.ok is True
    assert allowed.action == "allowed"


def test_schema_equal_is_ok(tmp_path):
    _write(tmp_path / DATA_SCHEMA_FILENAME, _schema_marker(DATA_SCHEMA_VERSION))
    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is True
    assert gate.action == "ok"


def test_wrapped_key_blocks_legacy_binary(tmp_path, monkeypatch):
    # 物理格式优先于标记：即使 data-format.json 缺失，包裹密钥也必须拦住旧版。
    key = tmp_path / "config" / "integrity.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("LIUHAO-DPAPI-1:AAAA", encoding="utf-8")
    monkeypatch.setattr(startup_guard, "APP_VERSION", "0.1.1")

    gate = check_startup_data(tmp_path, allow_downgrade=False)
    assert gate.ok is False
    assert gate.action == "key_format_blocked"


# ---------------------------------------------------------------------------
# 标记落盘：两个文件成对写入，字段齐全，原子写不留临时文件
# ---------------------------------------------------------------------------
def test_stamp_writes_both_markers_with_provenance(tmp_path):
    key = tmp_path / "config" / "integrity.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("LIUHAO-DPAPI-1:AAAA", encoding="utf-8")

    format_marker = stamp_key_format(tmp_path, "0.1.2")
    schema_marker = stamp_data_schema(tmp_path, app_version="0.1.2")

    assert format_marker["schema"] == KEY_FORMAT_SCHEMA
    assert format_marker["integrity_key"] == KEY_FORMAT_WRAPPED
    assert format_marker["written_by"] == "0.1.2"
    assert format_marker["updated_at"]
    assert schema_marker.version == DATA_SCHEMA_VERSION
    assert schema_marker.written_by == "0.1.2"
    assert schema_marker.updated_at

    on_disk = json.loads((tmp_path / KEY_FORMAT_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["integrity_key"] == KEY_FORMAT_WRAPPED
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


@pytest.mark.parametrize("version", [1, DATA_SCHEMA_VERSION])
def test_marker_roundtrip_is_monotonic(tmp_path, version):
    _write(tmp_path / DATA_SCHEMA_FILENAME, _schema_marker(version))
    stamped = stamp_data_schema(tmp_path, app_version="0.1.2")
    # 只增不减：即使传入较低版本，也不会把已写入的高水位降下来。
    assert stamped.version >= version
