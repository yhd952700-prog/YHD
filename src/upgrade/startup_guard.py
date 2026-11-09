"""运行期启动门禁：数据由更高 schema 写入时拒绝启动。

为什么需要
----------
升级/降级入口的守卫（``dataversion.ensure_data_readable``）只能拦住"通过
升级器换版本"这一条路。用户完全可以直接双击旧版 ``LiuHao.bat`` —— 旧代码
起来后照常打开更高版本写过的数据目录，把新表/新字段当成不存在（会话列表
为空），看起来像丢数据。启动门禁补的就是这一条：**真正启动时**先读
``data-schema.json``，数据版本高于本二进制支持的 ``DATA_SCHEMA_VERSION``
就拒绝启动，并给出可执行的下一步。

兼容优先
--------
标记是升级成功后才写的。首次启动（无标记）、旧版本数据（无标记）、标记
损坏或字段不全，一律按兼容处理并只打印一行可读诊断——绝不能因为一个
辅助文件把正常用户挡在门外。真正的数据损坏由启动后的数据自检负责暴露。

显式放行
--------
``--allow-data-downgrade``（启动器会转成 ``LIUHAO_ALLOW_DATA_DOWNGRADE=1``，
子进程继承）或直接设置该环境变量。放行只表示"允许本次运行"，不会回退
标记版本，也不会掩盖告警。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..version import APP_VERSION
from .dataversion import (
    DATA_SCHEMA_VERSION,
    DataSchemaState,
    read_data_schema,
)
from .errors import DataSchemaError
from .keyformat import check_startup_key_format

#: 显式放行开关。启动器把 ``--allow-data-downgrade`` 映射成它，网关子进程继承。
ALLOW_ENV = "LIUHAO_ALLOW_DATA_DOWNGRADE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class StartupDataGate:
    """一次启动门禁判断的结果（可序列化，便于日志与测试断言）。"""

    #: False 表示必须拒绝启动。
    ok: bool
    #: ``ok`` / ``missing`` / ``unreadable`` / ``allowed`` / ``blocked``
    action: str
    supported: int
    version: Optional[int] = None
    written_by: str = ""
    #: 给人看的诊断/告警；``ok=False`` 时是完整的拒绝说明。
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "action": self.action,
            "supported": self.supported,
            "version": self.version,
            "written_by": self.written_by,
            "message": self.message,
        }


class StartupDataBlocked(RuntimeError):
    """数据 schema 高于本二进制：拒绝启动（携带完整中文说明）。"""

    def __init__(self, gate: StartupDataGate) -> None:
        super().__init__(gate.message)
        self.gate = gate
        self.message = gate.message


def allow_downgrade_requested() -> bool:
    """环境变量是否显式允许降级运行。"""
    return (os.environ.get(ALLOW_ENV) or "").strip().lower() in _TRUTHY


def _launch_command(app_root: Optional[Union[str, Path]]) -> str:
    """给用户的启动命令；有安装目录时用绝对路径，避免"在哪个目录双击"的歧义。"""
    root = Path(app_root) if app_root else Path(__file__).resolve().parents[2]
    bat = root / "LiuHao.bat"
    return f'"{bat}"' if bat.is_file() else '"LiuHao.bat"'


def _blocked_message(
    data_dir: Path,
    state: DataSchemaState,
    *,
    app_root: Optional[Union[str, Path]],
) -> str:
    writer = state.written_by or "更新版本"
    command = _launch_command(app_root)
    return (
        "[FAIL] 数据目录由更新版本的 LiuHao 写入，已拒绝启动。\n"
        f"       数据 schema：v{state.version}（写入者 {writer}）；"
        f"当前程序只支持 v{DATA_SCHEMA_VERSION}。\n"
        "       继续运行会让旧代码静默读取新数据（典型表现：会话列表为空、"
        "新字段不可见），因此这里不做降级运行。\n"
        "       下一步（任选其一）：\n"
        f"       1) 升级到最新版本：运行 {command}，"
        "或在控制台「设置 → 软件升级」里检查更新；\n"
        f"       2) 确认要用当前版本降级：先完整备份数据目录 {data_dir}，"
        f"再用 {command} --allow-data-downgrade 启动\n"
        f"          （等价环境变量：{ALLOW_ENV}=1）。\n"
        f"       数据目录：{data_dir}"
    )


def check_startup_data(
    data_dir: Union[str, Path],
    *,
    allow_downgrade: Optional[bool] = None,
    app_root: Optional[Union[str, Path]] = None,
) -> StartupDataGate:
    """判断本二进制能否读取该数据目录。

    * 标记缺失 / 损坏 / 字段不全 → 放行（兼容优先），损坏时带一行诊断；
    * 数据版本 ≤ 本二进制 → 放行；
    * 数据版本 > 本二进制 → 拒绝，除非显式放行。

    ``allow_downgrade=None`` 时读环境变量 :data:`ALLOW_ENV`。
    """
    directory = Path(data_dir)
    # 密钥格式先于 schema：读不懂密钥不会导致"少几张表"，而是"一个账号都
    # 登不进"，必须在任何写入之前拦住。
    key_block = check_startup_key_format(directory, APP_VERSION)
    if key_block:
        return StartupDataGate(
            ok=False,
            action="key_format_blocked",
            supported=DATA_SCHEMA_VERSION,
            version=None,
            written_by="",
            message=key_block,
        )
    try:
        state = read_data_schema(directory)
    except DataSchemaError as exc:
        return StartupDataGate(
            ok=True,
            action="unreadable",
            supported=DATA_SCHEMA_VERSION,
            message=(
                "[warn] 数据版本标记不可读，按兼容处理继续启动；"
                f"若数据异常请先备份 {directory}。原因：{exc}"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 门禁自身故障绝不阻断正常启动
        return StartupDataGate(
            ok=True,
            action="unreadable",
            supported=DATA_SCHEMA_VERSION,
            message=(
                "[warn] 检查数据版本标记时出错，按兼容处理继续启动；"
                f"若数据异常请先备份 {directory}。原因：{type(exc).__name__}: {exc}"
            ),
        )

    if state is None:
        return StartupDataGate(
            ok=True, action="missing", supported=DATA_SCHEMA_VERSION
        )
    if state.version <= DATA_SCHEMA_VERSION:
        return StartupDataGate(
            ok=True,
            action="ok",
            supported=DATA_SCHEMA_VERSION,
            version=state.version,
            written_by=state.written_by,
        )

    allowed = (
        allow_downgrade_requested()
        if allow_downgrade is None
        else bool(allow_downgrade)
    )
    if allowed:
        writer = state.written_by or "更新版本"
        return StartupDataGate(
            ok=True,
            action="allowed",
            supported=DATA_SCHEMA_VERSION,
            version=state.version,
            written_by=state.written_by,
            message=(
                "[warn] 已按显式降级开关启动：数据 schema "
                f"v{state.version}（写入者 {writer}）高于当前程序支持的 "
                f"v{DATA_SCHEMA_VERSION}。可能出现会话列表为空或字段缺失；"
                "请尽快升级，并在降级期间避免写入关键数据。"
            ),
        )

    return StartupDataGate(
        ok=False,
        action="blocked",
        supported=DATA_SCHEMA_VERSION,
        version=state.version,
        written_by=state.written_by,
        message=_blocked_message(directory, state, app_root=app_root),
    )


def enforce_startup_data(
    data_dir: Union[str, Path],
    *,
    allow_downgrade: Optional[bool] = None,
    app_root: Optional[Union[str, Path]] = None,
) -> StartupDataGate:
    """:func:`check_startup_data` 的强制版本：不允许时抛
    :class:`StartupDataBlocked`（启动路径用这个）。"""
    gate = check_startup_data(
        data_dir, allow_downgrade=allow_downgrade, app_root=app_root
    )
    if not gate.ok:
        raise StartupDataBlocked(gate)
    return gate
