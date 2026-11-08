"""数据 schema 版本标记与降级守卫。

为什么需要它
------------
数据库文件本身不带「写它的代码支持到哪一代」的信息。0.1.0 直接打开 0.1.1
的数据目录不会报错，只会把新表/新字段当不存在（会话列表显示为空），看起来
是「数据丢了」，实际是旧代码在静默读新数据。这类问题必须显式化：

* 新版本在升级成功后往数据目录写 ``data-schema.json``，记录 schema 版本
  与写入者版本；
* 旧版本（或任何 ``DATA_SCHEMA_VERSION`` 更低的代码）读到更高版本的数据时，
  升级/降级入口一律拒绝，必须显式确认 ``--allow-data-downgrade`` 才继续。

标记只增不减：降级确认后仍保留较高版本号，避免「降级一次就把证据抹掉」。
标记本身是普通 JSON，会随数据目录一起被升级前快照与回滚覆盖。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from packaging.version import InvalidVersion, Version

from src.common.atomic_write import atomic_write_text

from .errors import DataSchemaError

#: 当前代码能安全读写的数据 schema 版本。改数据库结构时必须同步 +1。
DATA_SCHEMA_VERSION = 2
DATA_SCHEMA_FILENAME = "data-schema.json"
_SCHEMA_NAME = "liuhao.data-schema/1"


@dataclass(frozen=True)
class DataSchemaState:
    schema: str
    version: int
    written_by: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "written_by": self.written_by,
            "updated_at": self.updated_at,
        }


def marker_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / DATA_SCHEMA_FILENAME


def read_data_schema(data_dir: str | Path) -> Optional[DataSchemaState]:
    """读取数据 schema 标记；没有标记时返回 ``None``（0.1.0 及更早的数据）。

    标记损坏时不静默放行：损坏本身可能是写到一半或被人为改动，继续升级会
    把问题带进备份，所以直接报错让用户先处理数据目录。
    """
    path = marker_path(data_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataSchemaError(
            f"数据版本标记无法读取或解析（{path}）：{exc}。"
            "请先恢复数据目录备份，或人工确认该文件后删除。"
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA_NAME:
        raise DataSchemaError(
            f"数据版本标记格式不正确（{path}）：schema 必须是 {_SCHEMA_NAME}"
        )
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DataSchemaError(f"数据版本标记中的 version 无效（{path}）：{version!r}")
    return DataSchemaState(
        schema=_SCHEMA_NAME,
        version=version,
        written_by=str(raw.get("written_by") or ""),
        updated_at=str(raw.get("updated_at") or ""),
    )


def stamp_data_schema(
    data_dir: str | Path,
    *,
    app_version: str,
    schema_version: int = DATA_SCHEMA_VERSION,
) -> DataSchemaState:
    """升级成功后打标；版本号只增不减。"""
    current = read_data_schema(data_dir)
    version = max(current.version if current else 0, int(schema_version))
    state = DataSchemaState(
        schema=_SCHEMA_NAME,
        version=version,
        written_by=str(app_version),
        updated_at=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )
    path = marker_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    return state


def target_data_schema(
    release_schema: Optional[int],
    *,
    release_version: str,
    current_version: str,
) -> int:
    """目标版本声明的数据 schema 版本。

    旧发布清单没有 ``data_schema_version``：升级/同版本沿用当前代码的版本；
    降级到这种旧包时保守按 schema 1 处理（守卫之前的数据格式）。
    """
    if release_schema is not None:
        return int(release_schema)
    try:
        if Version(current_version) > Version(release_version):
            return 1
    except InvalidVersion:
        return 1
    return DATA_SCHEMA_VERSION


def ensure_data_readable(
    data_dir: str | Path,
    *,
    target_schema: int,
    allow_downgrade: bool = False,
) -> Optional[DataSchemaState]:
    """目标版本能否读取现有数据；不能则抛出需要显式确认的错误。"""
    state = read_data_schema(data_dir)
    if state is None or state.version <= target_schema:
        return state
    if allow_downgrade:
        return state
    writer = state.written_by or "更高版本"
    raise DataSchemaError(
        f"数据目录已由更高版本写入（schema v{state.version}，写入者 {writer}），"
        f"目标版本只支持 schema v{target_schema}。继续会让旧代码静默读取新数据"
        "（典型表现：会话列表为空、新字段不可见），已拒绝升级。"
        "确认要降级请加 --allow-data-downgrade，并先备份数据目录。"
    )
