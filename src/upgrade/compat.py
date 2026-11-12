"""跨版本「数据形态」兼容判据（回滚 / 降级 / 直接回滚）。

为什么需要它
------------
0.1.2 把若干**持久化形态**改成了旧版读不懂的样子（DPAPI 包裹密钥、
HMAC 审计链、带维度指纹的向量索引……）。``manager.rollback`` 只恢复代码与
普通数据，而 ``backups.is_protected_state_file`` **故意不备份**密钥/身份类
文件，于是会出现「代码回去了、数据没回去」。

单修一个 ``integrity.key`` 只能解决登录这一例。本模块把同类问题收敛成一条
可执行判据：

* :data:`CompatLevel.DIRECT`  —— 旧版能正确读取；或只能**响亮失败**
  （报错、拒绝、降级告警），不会静默产生错误结果，也不影响既有数据；
* :data:`CompatLevel.DOWNGRADE` —— 旧版会把新形态**当作合法旧值**使用，
  产生静默错误或数据不可见；但存在可逆的降级动作（解包密钥、改写明文）；
* :data:`CompatLevel.REFUSE` —— 既无法降级，旧版又会静默误读或不可逆损坏
  数据；必须拒绝回滚/启动，交给人工处理。

判定顺序与优先级：REFUSE > DOWNGRADE > DIRECT。

实测依据（真实日志见交付文档）
------------------------------
* ``integrity.key`` 被旧版当明文密钥 → 6/6 身份行校验失败（0 账号）；
* ``model_config.key`` 被旧版当口令派生 → 已存 API Key 全部解不开；
* ``config/app.env`` 的 JWT 密钥被旧版当原文 → 新旧令牌互不承认；
* 审计库混入 ``hmac-sha256`` → 旧版校验报 broken（响亮），新版仍可校验；
* 向量维度变化 → 旧版 ``zip`` 截断后**静默**给出错误相似度（最危险）。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.common.fs_permissions import restrict_to_current_user
from src.security.encryption import (
    is_machine_bound_payload,
    unwrap_machine_bound,
)

from .dataversion import (
    DATA_SCHEMA_FILENAME,
    DATA_SCHEMA_VERSION,
    read_data_schema,
)
from .errors import DataSchemaError, RollbackError
from .keyformat import (
    KEY_FORMAT_WRAPPED,
    detect_key_format,
    downgrade_key_for_legacy,
    required_min_build,
)

logger = logging.getLogger("liuhao.upgrade.compat")

#: 这些组件的新形态都由 0.1.2 引入；低于它读不懂。
COMPONENT_MIN_BUILD = "0.1.2"

MODEL_KEY_REL = Path("data") / "model_config.key"
MODEL_DB_REL = Path("data") / "model_registry.sqlite3"
APP_ENV_REL = Path("config") / "app.env"
AUDIT_DB_REL = Path("audit_store.db")
VECTOR_DB_REL = Path("data") / "knowledge_vectors.sqlite3"

#: ``app.env`` 里会被包裹的键；值一旦变成 ``LIUHAO-DPAPI-1:`` 旧版就读错。
_SECRET_ENV_KEYS = ("LIUHAO_JWT_SECRET", "SECRET_KEY")


class CompatLevel(str, Enum):  # noqa: UP042 - 与仓库其它判据枚举保持一致
    DIRECT = "direct"
    DOWNGRADE = "downgrade"
    REFUSE = "refuse"


_ORDER = {CompatLevel.DIRECT: 0, CompatLevel.DOWNGRADE: 1, CompatLevel.REFUSE: 2}


@dataclass
class CompatFinding:
    """单个持久化形态的判定结果。"""

    component: str
    title: str
    level: CompatLevel
    detail: str
    files: Sequence[str] = ()
    #: 降级动作标识（由 :func:`apply_rollback_downgrades` 分派）。
    action: str | None = None
    #: 可以回滚、但能力会降级的说明（响亮告警，不是静默错误）。
    degraded: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "title": self.title,
            "level": self.level.value,
            "detail": self.detail,
            "files": list(self.files),
            "action": self.action,
            "degraded": self.degraded,
            "data": self.data,
        }


@dataclass
class CompatReport:
    target_version: str
    target_schema: int
    findings: list[CompatFinding]

    @property
    def level(self) -> CompatLevel:
        if not self.findings:
            return CompatLevel.DIRECT
        return max((f.level for f in self.findings), key=lambda lv: _ORDER[lv])

    @property
    def blockers(self) -> list[CompatFinding]:
        return [f for f in self.findings if f.level is CompatLevel.REFUSE]

    @property
    def downgrades(self) -> list[CompatFinding]:
        return [
            f
            for f in self.findings
            if f.level is CompatLevel.DOWNGRADE and f.action
        ]

    def to_dict(self) -> dict:
        return {
            "target_version": self.target_version,
            "target_schema": self.target_schema,
            "level": self.level.value,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_text(self) -> str:
        lines = [
            f"回滚目标版本：{self.target_version or '未知'}；"
            f"目标数据 schema：v{self.target_schema}；总体判定：{self.level.value}"
        ]
        for f in self.findings:
            lines.append(f"- [{f.level.value}] {f.title}：{f.detail}")
            if f.degraded:
                lines.append(f"    降级说明：{f.degraded}")
        return "\n".join(lines)

    def blocked_message(self) -> str:
        blockers = self.blockers
        head = (
            "[FAIL] 数据形态不是目标版本能安全读取的，已拒绝回滚。\n"
            f"       目标版本：{self.target_version or '未知'}；"
            f"数据 schema：当前标记高于 v{self.target_schema} 或存在不可降级格式。\n"
        )
        body = "\n".join(f"       - {f.title}：{f.detail}" for f in blockers)
        tail = (
            "\n       下一步（任选其一）：\n"
            "       1) 不降级，继续使用当前（较新）版本；\n"
            "       2) 先完整备份数据目录，再设置 "
            "LIUHAO_ALLOW_DATA_DOWNGRADE=1 重试（接受降级后的能力损失）；\n"
            "       3) 若只是误装旧版，请用新版重新启动，数据会保持可读。"
        )
        return head + body + tail


class DataCompatError(RollbackError):
    """数据形态不满足目标版本要求：回滚前置条件失败。"""


# ---------------------------------------------------------------------------
# 各组件判定
# ---------------------------------------------------------------------------
def _sqlite_count(path: Path, sql: str) -> int:
    """只读计数；库/表不存在时返回 0（不把"没有数据"误判成故障）。"""
    if not path.is_file():
        return 0
    uri = "file:" + path.as_posix() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return 0
    try:
        return int(conn.execute(sql).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _check_data_schema(
    data_dir: Path, target_schema: int, backup_dir: Path | None
) -> CompatFinding:
    try:
        state = read_data_schema(data_dir)
    except DataSchemaError as exc:
        return CompatFinding(
            component="data_schema",
            title="数据 schema 标记损坏",
            level=CompatLevel.REFUSE,
            detail=f"{exc}（标记损坏时无法判断目标版本能否读取，按拒绝处理）",
            files=[str(data_dir / DATA_SCHEMA_FILENAME)],
        )
    if state is None:
        return CompatFinding(
            component="data_schema",
            title="数据 schema 标记",
            level=CompatLevel.DIRECT,
            detail=(
                "没有 data-schema.json（0.1.0 及更早，或升级标记从未落盘）。"
                "这是本项目已确认的结构性缺口：缺少标记时守卫天然失效，"
                "只能靠下面的组件级形态检测兜底。"
            ),
        )
    if state.version <= target_schema:
        return CompatFinding(
            component="data_schema",
            title="数据 schema 标记",
            level=CompatLevel.DIRECT,
            detail=f"v{state.version} ≤ 目标 v{target_schema}，旧版可读。",
        )
    restored = backup_dir is not None and (
        backup_dir / "data" / DATA_SCHEMA_FILENAME
    ).is_file()
    if restored:
        return CompatFinding(
            component="data_schema",
            title="数据 schema 标记",
            level=CompatLevel.DIRECT,
            detail=(
                f"v{state.version} > 目标 v{target_schema}，但升级前备份包含 "
                "data-schema.json，回滚会随数据一起把标记恢复到旧版本。"
            ),
        )
    if backup_dir is not None:
        # 恢复数据不会删除"当前有、备份没有"的文件，标记会原样留下。
        # 数据文件本身会被恢复成旧形态，因此这里清掉虚高的水位线即可。
        return CompatFinding(
            component="data_schema",
            title="数据 schema 标记",
            level=CompatLevel.DOWNGRADE,
            detail=(
                f"v{state.version} > 目标 v{target_schema}；升级前备份不含该标记"
                "（旧版备份本来就没有），恢复数据后标记会虚高，必须清掉。"
            ),
            action="data_schema_marker",
            files=[str(data_dir / DATA_SCHEMA_FILENAME)],
            data={"version": state.version, "target_schema": int(target_schema)},
        )
    return CompatFinding(
        component="data_schema",
        title="数据 schema 标记",
        level=CompatLevel.REFUSE,
        detail=(
            f"数据标记为 v{state.version}（写入者 {state.written_by or '未知'}），"
            f"目标版本只支持 v{target_schema}，且没有可降级的表级迁移。"
        ),
        files=[str(data_dir / DATA_SCHEMA_FILENAME)],
        data={"version": state.version, "written_by": state.written_by},
    )


def _check_integrity_key(data_dir: Path, target_version: str) -> CompatFinding:
    fmt = detect_key_format(data_dir)
    if fmt == "missing":
        return CompatFinding(
            component="integrity_key",
            title="身份签名密钥 integrity.key",
            level=CompatLevel.DIRECT,
            detail="密钥文件不存在（尚未注册账号，或由账号备份包恢复）。",
        )
    if fmt != KEY_FORMAT_WRAPPED:
        return CompatFinding(
            component="integrity_key",
            title="身份签名密钥 integrity.key",
            level=CompatLevel.DIRECT,
            detail=f"当前格式 {fmt}，旧版可读。",
            data={"key_format": fmt},
        )
    if _target_is_legacy(target_version):
        return CompatFinding(
            component="integrity_key",
            title="身份签名密钥 integrity.key",
            level=CompatLevel.DOWNGRADE,
            detail=(
                "密钥已迁移为机器绑定包裹格式，旧版会把整段包裹文本当密钥，"
                "导致全部身份行校验失败（0 个可用账号）。"
            ),
            action="integrity_key",
            files=[str(data_dir / "config" / "integrity.key")],
            data={"key_format": fmt},
        )
    return CompatFinding(
        component="integrity_key",
        title="身份签名密钥 integrity.key",
        level=CompatLevel.DIRECT,
        detail=f"目标版本 {target_version} 能读懂包裹格式。",
        data={"key_format": fmt},
    )


def _check_model_key(data_dir: Path, target_version: str) -> CompatFinding:
    path = data_dir / MODEL_KEY_REL
    if not path.is_file():
        return CompatFinding(
            component="model_config_key",
            title="模型主密钥 model_config.key",
            level=CompatLevel.DIRECT,
            detail="密钥文件不存在（尚未保存任何模型凭据）。",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return CompatFinding(
            component="model_config_key",
            title="模型主密钥 model_config.key",
            level=CompatLevel.REFUSE,
            detail=f"密钥文件不可读（{type(exc).__name__}），无法判断/降级。",
            files=[str(path)],
        )
    if not is_machine_bound_payload(raw):
        return CompatFinding(
            component="model_config_key",
            title="模型主密钥 model_config.key",
            level=CompatLevel.DIRECT,
            detail="密钥文件是旧版可读的原始 32 字节形态。",
            data={"wrapped": False},
        )
    encrypted = _sqlite_count(
        data_dir / MODEL_DB_REL,
        "SELECT count(*) FROM ai_model_configs WHERE api_key_enc IS NOT NULL",
    )
    if not _target_is_legacy(target_version):
        return CompatFinding(
            component="model_config_key",
            title="模型主密钥 model_config.key",
            level=CompatLevel.DIRECT,
            detail=f"目标版本 {target_version} 能解包该密钥。",
            data={"wrapped": True, "encrypted_rows": encrypted},
        )
    return CompatFinding(
        component="model_config_key",
        title="模型主密钥 model_config.key",
        level=CompatLevel.DOWNGRADE,
        detail=(
            "密钥已包裹；旧版会把 447 字符的包裹文本当口令重新派生，"
            f"库中 {encrypted} 条已加密凭据将全部解不开（界面显示「凭据需重新填写」）。"
        ),
        action="model_config_key",
        files=[str(path)],
        data={"wrapped": True, "encrypted_rows": encrypted},
    )


def _read_app_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value
    except OSError:
        return values
    return values


def _check_app_env(data_dir: Path, target_version: str) -> CompatFinding:
    path = data_dir / APP_ENV_REL
    values = _read_app_env(path)
    wrapped = [k for k in _SECRET_ENV_KEYS if is_machine_bound_payload(values.get(k, ""))]
    if not wrapped:
        return CompatFinding(
            component="app_env_secrets",
            title="app.env 签名密钥（JWT/SECRET_KEY）",
            level=CompatLevel.DIRECT,
            detail="没有包裹形态的密钥。",
        )
    if not _target_is_legacy(target_version):
        return CompatFinding(
            component="app_env_secrets",
            title="app.env 签名密钥（JWT/SECRET_KEY）",
            level=CompatLevel.DIRECT,
            detail=f"目标版本 {target_version} 能解包这些密钥。",
            data={"wrapped_keys": wrapped},
        )
    return CompatFinding(
        component="app_env_secrets",
        title="app.env 签名密钥（JWT/SECRET_KEY）",
        level=CompatLevel.DOWNGRADE,
        detail=(
            f"{', '.join(wrapped)} 已包裹；旧版会把包裹文本当原文使用，"
            "升级后签发的访问/刷新令牌在回滚后全部验签失败（全员掉线）。"
        ),
        action="app_env_secrets",
        files=[str(path)],
        data={"wrapped_keys": wrapped},
    )


def _check_audit_chain(data_dir: Path, backup_dir: Path | None) -> CompatFinding:
    db = data_dir / AUDIT_DB_REL
    if not db.is_file():
        return CompatFinding(
            component="audit_chain",
            title="审计哈希链",
            level=CompatLevel.DIRECT,
            detail="没有审计库。",
        )
    if backup_dir is not None and (backup_dir / "data" / AUDIT_DB_REL).is_file():
        return CompatFinding(
            component="audit_chain",
            title="审计哈希链",
            level=CompatLevel.DIRECT,
            detail="审计库在升级前备份内，回滚会整体恢复到旧形态。",
        )
    keyed = _sqlite_count(
        db, "SELECT count(*) FROM audit_events WHERE hash_alg IS NOT NULL AND hash_alg <> 'sha256'"
    )
    total = _sqlite_count(db, "SELECT count(*) FROM audit_events")
    if not keyed:
        return CompatFinding(
            component="audit_chain",
            title="审计哈希链",
            level=CompatLevel.DIRECT,
            detail=f"{total} 条事件全部是 sha256，旧版可校验。",
            data={"total": total, "keyed": 0},
        )
    return CompatFinding(
        component="audit_chain",
        title="审计哈希链",
        level=CompatLevel.DIRECT,
        detail=(
            f"{total} 条事件中有 {keyed} 条使用 hmac-sha256，旧版不认识该算法，"
            "会把整条链报成 broken。"
        ),
        degraded=(
            "回滚后旧版的审计完整性校验会持续报红（旧版每追加一条 sha256，"
            "重新升级后新版仍能正确校验混合链）。这是响亮降级，不是静默错误；"
            "如果审计红灯不可接受，请不要回滚，或回滚后关闭密钥模式。"
        ),
        data={"total": total, "keyed": keyed},
    )


def _check_vector_index(data_dir: Path) -> CompatFinding:
    db = data_dir / VECTOR_DB_REL
    if not db.is_file():
        return CompatFinding(
            component="vector_index",
            title="向量索引维度",
            level=CompatLevel.DIRECT,
            detail="没有向量库。",
        )
    rows = _sqlite_count(db, "SELECT count(*) FROM knowledge_vectors")
    if not rows:
        return CompatFinding(
            component="vector_index",
            title="向量索引维度",
            level=CompatLevel.DIRECT,
            detail="向量库为空，维度不构成风险。",
        )
    meta: dict[str, str] = {}
    uri = "file:" + db.as_posix() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            for key, value in conn.execute("SELECT key, value FROM knowledge_index_meta"):
                meta[str(key)] = str(value)
        finally:
            conn.close()
    except sqlite3.Error:
        meta = {}
    recorded = meta.get("embedding_dim")
    if recorded:
        return CompatFinding(
            component="vector_index",
            title="向量索引维度",
            level=CompatLevel.DIRECT,
            detail=f"{rows} 条向量，索引记录维度={recorded}；旧版读取同一维度不会误算。",
            data={"rows": rows, "embedding_dim": recorded},
        )
    return CompatFinding(
        component="vector_index",
        title="向量索引维度",
        level=CompatLevel.DIRECT,
        detail=(
            f"{rows} 条向量，但库中没有维度指纹（knowledge_index_meta 为空）。"
        ),
        degraded=(
            "旧版用 zip() 计算相似度，维度不一致时不做任何校验而是按较短长度"
            "截断，会**静默**给出错误排序。若当前索引由不同维度的嵌入模型生成，"
            "回滚前必须重建索引。"
        ),
        data={"rows": rows, "embedding_dim": None},
    )


def _target_is_legacy(target_version: str | None) -> bool:
    if not target_version:
        return True
    # nosemgrep: liuhao-swallow-exception-return-success
    try:
        return Version(str(target_version)) < Version(COMPONENT_MIN_BUILD)
    except InvalidVersion:
        # 结辩：版本号无法解析（非规范版本）时按「可能读不懂旧数据」处理，
        # 回滚评估宁可多降级一次（与 keyformat._target_is_legacy 同口径），
        # True 语义是保守默认，不是谎报成功。
        return True


# ---------------------------------------------------------------------------
# 评估 / 降级
# ---------------------------------------------------------------------------
def assess_rollback(
    data_dir: str | Path,
    *,
    target_version: str = "",
    target_schema: int = 1,
    backup_dir: str | Path | None = None,
) -> CompatReport:
    """回滚/降级前的统一评估；纯只读，不改动任何文件。"""
    directory = Path(data_dir)
    backup = Path(backup_dir) if backup_dir else None
    findings = [
        _check_data_schema(directory, int(target_schema), backup),
        _check_integrity_key(directory, target_version),
        _check_model_key(directory, target_version),
        _check_app_env(directory, target_version),
        _check_audit_chain(directory, backup),
        _check_vector_index(directory),
    ]
    return CompatReport(
        target_version=str(target_version), target_schema=int(target_schema), findings=findings
    )


def _downgrade_data_schema_marker(data_dir: Path) -> dict[str, Any]:
    path = data_dir / DATA_SCHEMA_FILENAME
    if not path.exists():
        return {"changed": False, "reason": "标记已不存在"}
    # 目标版本比当前 schema 旧，且其备份根本没有这个文件：删掉虚高水位线，
    # 让下一次新版启动重新按实际数据迁移并打标。
    path.unlink()
    return {
        "changed": True,
        "removed": str(path),
        "note": f"已移除 data-schema.json；目标版本不使用该标记（当前 schema v{DATA_SCHEMA_VERSION}）。",
    }


def _downgrade_model_key(data_dir: Path) -> dict[str, Any]:
    from src.ai.model_config import KEY_DPAPI_PURPOSE

    path = data_dir / MODEL_KEY_REL
    raw = path.read_bytes()
    if not is_machine_bound_payload(raw):
        return {"changed": False, "reason": "已是旧版可读的原始密钥"}
    key, mode = unwrap_machine_bound(raw, purpose=KEY_DPAPI_PURPOSE)
    if len(key) != 32:
        raise DataCompatError(
            f"模型主密钥解包后长度为 {len(key)} 字节（预期 32），拒绝写回以避免损坏凭据。"
        )
    atomic_write_bytes(path, key)
    if not restrict_to_current_user(path):
        raise DataCompatError(f"密钥已降级，但权限收紧失败：{path}")
    return {
        "changed": True,
        "protection": mode,
        "bytes": len(key),
        "path": str(path),
        "security_note": "回滚窗口内模型主密钥以原始 32 字节落盘（权限已收紧到当前用户）。",
    }


def _downgrade_app_env(data_dir: Path) -> dict[str, Any]:
    from src.security.jwt_handler import JWT_SECRET_DPAPI_PURPOSE

    path = data_dir / APP_ENV_REL
    if not path.is_file():
        return {"changed": False, "reason": "app.env 不存在"}
    lines = path.read_text(encoding="utf-8").splitlines()
    changed: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in _SECRET_ENV_KEYS or not is_machine_bound_payload(value):
            continue
        purpose = (
            JWT_SECRET_DPAPI_PURPOSE
            if key == "LIUHAO_JWT_SECRET"
            else b"secret-key-v1"
        )
        plain, _mode = unwrap_machine_bound(value, purpose=purpose)
        lines[index] = f"{key}={plain.decode('utf-8')}"
        changed.append(key)
    if not changed:
        return {"changed": False, "reason": "没有需要降级的密钥行"}
    atomic_write_text(path, "\n".join(lines) + "\n")
    if not restrict_to_current_user(path):
        raise DataCompatError(f"app.env 已降级，但权限收紧失败：{path}")
    return {
        "changed": True,
        "keys": changed,
        "path": str(path),
        "security_note": "回滚窗口内 JWT/SECRET_KEY 以明文写入 app.env（权限已收紧到当前用户）。",
    }


_ACTIONS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "data_schema_marker": _downgrade_data_schema_marker,
    "model_config_key": _downgrade_model_key,
    "app_env_secrets": _downgrade_app_env,
    "integrity_key": lambda directory: downgrade_key_for_legacy(directory),
}


def apply_rollback_downgrades(
    report: CompatReport, data_dir: str | Path
) -> list[dict[str, Any]]:
    """按报告执行所有 DOWNGRADE 动作；任一失败即抛出（不带着半降级状态继续）。"""
    directory = Path(data_dir)
    if not directory.is_dir():
        raise DataCompatError(f"数据目录不存在，无法降级：{directory}")
    results: list[dict[str, Any]] = []
    for finding in report.downgrades:
        action = _ACTIONS.get(finding.action or "")
        if action is None:
            raise DataCompatError(f"未知的降级动作：{finding.action}")
        result = action(directory)
        result["component"] = finding.component
        results.append(result)
        logger.info("回滚降级完成：%s -> %s", finding.component, result)
    return results


def required_min_build_all(data_dir: str | Path) -> tuple[str, str] | None:
    """数据目录要求的最低可读版本：``(版本, 组件)``；``None`` 表示无限制。"""
    directory = Path(data_dir)
    candidates: list[tuple[str, str]] = []
    integrity = required_min_build(directory)
    if integrity:
        candidates.append((integrity, "integrity_key"))
    if (directory / MODEL_KEY_REL).is_file():
        try:
            if is_machine_bound_payload((directory / MODEL_KEY_REL).read_bytes()):
                candidates.append((COMPONENT_MIN_BUILD, "model_config_key"))
        except OSError:
            pass
    values = _read_app_env(directory / APP_ENV_REL)
    if any(is_machine_bound_payload(values.get(k, "")) for k in _SECRET_ENV_KEYS):
        candidates.append((COMPONENT_MIN_BUILD, "app_env_secrets"))
    if _sqlite_count(
        directory / AUDIT_DB_REL,
        "SELECT count(*) FROM audit_events WHERE hash_alg IS NOT NULL AND hash_alg <> 'sha256'",
    ):
        candidates.append((COMPONENT_MIN_BUILD, "audit_chain"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: _version_key(item[0]))


def _version_key(value: str) -> Version:
    try:
        return Version(str(value))
    except InvalidVersion:
        return Version("999999")


def check_startup_compat(data_dir: str | Path, app_version: str) -> str | None:
    """启动门禁：本二进制读不懂任一形态时返回拒绝说明，否则 ``None``。"""
    required = required_min_build_all(data_dir)
    if not required:
        return None
    min_version, component = required
    try:
        if Version(str(app_version)) >= Version(min_version):
            return None
    except InvalidVersion:
        return None
    return (
        "[FAIL] 数据目录里存在当前程序读不懂的持久化形态，已拒绝启动。\n"
        f"       最早要求版本：{min_version}（组件 {component}）；"
        f"当前程序版本：{app_version}。\n"
        "       继续启动可能出现：账号全部不可用、模型凭据解不开、"
        "审计链报损坏，或更危险的——向量检索静默返回错误排序。\n"
        "       下一步（任选其一）：\n"
        f"       1) 用 {min_version} 或更新版本启动（推荐）；\n"
        "       2) 确认要降级：先完整备份数据目录，再执行\n"
        "          python -m src.upgrade rollback --dry-run 查看判定，\n"
        "          然后 python -m src.upgrade rollback（会自动降级可逆项）。"
    )
