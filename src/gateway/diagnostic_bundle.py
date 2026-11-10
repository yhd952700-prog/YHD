"""一键诊断包：控制台可导出的、**白名单生成**的排障压缩包。

设计约束（为什么不是"打包整个目录再排除密钥"）
------------------------------------------------
排除法永远会漏：新加一个配置文件、日志里多打印一个字段，密钥就跟着出去了。
这里反过来——先写死允许出现在包里的成员与字段，任何未登记内容都不会进包：

* ``README.txt`` / ``manifest.json``
* ``summary.json``（版本、提交号、系统与架构、安装方式、数据目录脱敏路径）
* ``health.json`` / ``readiness.json``（健康检查与就绪自检）
* ``selfcheck.json``（生产配置预检的**代码**与级别，不含配置值）
* ``logs/<file>.txt``（最近日志尾部）

三条硬规则：

1. 所有文本先做用户名脱敏（``C:\\Users\\<name>`` / ``/home/<name>`` /
   ``/Users/<name>``），再做密钥脱敏（复用 ``src.desktop.diagnostics``）；
2. 生成时逐成员跑一遍密钥自检，命中的成员**从包里剔除**（其余照常导出），
   并写 ``REDACTED-MEMBERS.txt``；只有真实诊断成员一个都留不下才整体拒绝；
3. 就绪检查里的原始异常文本 ``error`` / ``technical_detail`` **不写入**
   诊断包——它们是机器串，属于服务端日志，不属于用户要发出去的文件。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import platform
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .account_context import require_account_principal
from .policy import require_admin_principal

from ..brand import BRAND_LOCKUP as APP_NAME
from ..common.redaction import (
    contains_secret,
    leak_violations,
    mask_user_path,
    sanitize_text,
)
from ..common.leak_scan import (
    LOG_MEMBER_ALIAS_NOTE,
    OMITTED_MEMBERS_NAME,
    SCAN_RULE_HIT_NAME,
    completeness_line,
    format_hits,
    log_bundle_blocked,
    log_member_name,
    omitted_members_text,
    omitted_readme,
    scan_rule_hit_report,
    scan_text,
)
from ..logging_config import (
    load_uvicorn_log_config,
    prune_log_retention,
    read_retention_report,
    resolve_data_dir,
    resolve_log_config_path,
)

logger = logging.getLogger(__name__)


def collect_about(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """延迟导入：``src.desktop.about`` 会连带 ``platform``/``importlib.metadata``。

    诊断包只有用户点"导出诊断包"时才需要它，不该让每次网关启动都付这笔导入。
    """
    from ..desktop.about import collect_about as _collect_about

    return _collect_about(*args, **kwargs)


class DiagnosticBundleBlocked(ValueError):
    """诊断包**整体**无法导出（每个成员都命中明文凭据）。

    单个成员命中时不再走这里：那些成员会被剔除，其余成员照常打包（降级导出），
    由 :class:`BundleBuild` 带着剔除清单返回。只有"一个成员都留不下"才整体拒绝。

    ``member`` 是命中的包内成员名，``hits`` 是 ``{特征: 条数}``——两者都**不含
    明文值**，可以安全写进日志与界面提示。
    """

    def __init__(
        self,
        message: str,
        *,
        member: str = "全部成员",
        hits: Dict[str, int] | None = None,
        omitted: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.member = member
        self.hits = dict(hits or {})
        self.omitted: Tuple[Dict[str, Any], ...] = tuple(dict(item) for item in omitted or ())

    @property
    def total(self) -> int:
        return sum(self.hits.values())


@dataclass(frozen=True)
class BundleBuild:
    """一次诊断包构建的结果。

    ``omitted`` 里的每一项都是 ``{"name", "hits", "total"}``：命中条数来自
    正向扫描器，**不含明文值**，可以安全写进日志、响应头与界面文案。
    """

    data: bytes
    omitted: Tuple[Dict[str, Any], ...] = ()

    @property
    def omitted_names(self) -> Tuple[str, ...]:
        return tuple(str(item["name"]) for item in self.omitted)


REPO_ROOT = Path(__file__).resolve().parents[2]

#: 包内允许出现的固定成员。新增成员必须先改这里，白名单之外一律拒绝。
BUNDLE_MEMBERS: Tuple[str, ...] = (
    "README.txt",
    "summary.json",
    "health.json",
    "readiness.json",
    "selfcheck.json",
    "log_retention.json",
    "manifest.json",
)

#: 允许的日志成员前缀。文件名仍需再清洗（只保留 ``*.log`` 的安全基名）。
LOG_MEMBER_PREFIX = "logs/"

#: 降级导出时才会出现的成员：列出被剔除的文件与命中条数（不含明文值）。
REDACTED_MEMBERS_MEMBER = OMITTED_MEMBERS_NAME

#: 可选成员：不进 ``BUNDLE_MEMBERS`` 固定清单，但白名单允许出现。
OPTIONAL_MEMBERS: Tuple[str, ...] = (REDACTED_MEMBERS_MEMBER, SCAN_RULE_HIT_NAME)

_MAX_LOG_FILES = 5
_MAX_LOG_LINES = 200
_MAX_LOG_BYTES = 200_000

#: 就绪检查里允许进包的字段。``error`` / ``technical_detail`` 故意不在其中。
_READINESS_CHECK_FIELDS: Tuple[str, ...] = (
    "status",
    "status_label",
    "user_message",
    "availability",
    "evidence_status",
)


def _safe_mapping(payload: Mapping[str, Any], allowed: Iterable[str]) -> Dict[str, Any]:
    """按白名单取字段；缺失的键不补，未登记的键丢弃。"""
    return {key: payload[key] for key in allowed if key in payload}


def _install_method(root: Path) -> str:
    """安装方式只给中性描述，不暴露安装目录。"""
    if getattr(sys, "frozen", False):
        return "安装包（独立程序）"
    if (root / "scripts" / "launch_installed.py").is_file():
        return "安装版"
    if (root / ".venv").is_dir():
        return "源码运行（虚拟环境）"
    return "源码运行"


def _log_dir() -> Path:
    """按真实生效的日志配置推导目录（安装版会把它改到用户数据目录）。"""
    try:
        config = load_uvicorn_log_config(resolve_log_config_path()) or {}
    except Exception:  # noqa: BLE001 - 配置损坏时退回程序目录
        config = {}
    handlers = config.get("handlers", {}) if isinstance(config, dict) else {}
    for handler in handlers.values():
        filename = handler.get("filename") if isinstance(handler, dict) else None
        if filename:
            return Path(str(filename)).expanduser().parent
    return root_logs_dir()


def root_logs_dir() -> Path:
    return REPO_ROOT / "logs"


def _summary(root: Path, log_dir: Path) -> Dict[str, Any]:
    about = collect_about(root)
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "app": str(about.get("app") or APP_NAME),
        "version": about.get("version"),
        "commit": about.get("commit"),
        "build_time": about.get("build_time"),
        "build_time_source": about.get("build_time_source"),
        "operating_system": {
            "system": platform.system() or "未知",
            "release": platform.release() or "未知",
            "machine": platform.machine() or "未知",
        },
        "python_version": sys.version.split()[0],
        "install_method": _install_method(root),
        # 必须取运行期实际生效的数据目录（LIUHAO_DATA_DIR / 安装版用户目录），
        # 不能用仓库里的默认值：安装版跑起来后程序目录是只读的，拿它去排查
        # 会把人带偏。日志目录也由同一处契约推导，两者天然同源。
        "data_dir": sanitize_text(resolve_data_dir()),
        "log_dir": sanitize_text(log_dir),
        "note": "路径中的用户名已替换为 <用户>；本包不含任何密钥或令牌。",
    }


def _health_payload() -> Dict[str, Any]:
    import asyncio

    from .health import liveness_probe

    result = asyncio.run(liveness_probe(_HeadersOnlyRequest("diagnostics-health")))
    body = json.loads(result.body.decode("utf-8"))
    return _safe_mapping(
        body,
        (
            "status",
            "status_label",
            "user_message",
            "service",
            "version",
            "api_contract",
            "timestamp",
        ),
    ) | {"ai_provider": _safe_mapping(body.get("ai_provider") or {}, ("type", "mode", "simulated"))}


def _readiness_payload() -> Dict[str, Any]:
    import asyncio

    from .health import readiness_probe

    result = asyncio.run(readiness_probe(_HeadersOnlyRequest("diagnostics-ready")))
    body = json.loads(result.body.decode("utf-8"))
    checks: Dict[str, Any] = {}
    for name, check in (body.get("checks") or {}).items():
        if not isinstance(check, dict):
            continue
        # 只保留人话字段；原始 error / technical_detail 不进包。
        checks[str(name)] = _safe_mapping(check, _READINESS_CHECK_FIELDS)
    return {
        "status": body.get("status"),
        "status_label": body.get("status_label"),
        "user_message": body.get("user_message"),
        "http_status": result.status_code,
        "checks": checks,
    }


def _selfcheck_payload() -> Dict[str, Any]:
    """生产配置预检：只记录"哪条规则没过"，不记录任何配置值。"""
    from ..observability.production import validate_production_config
    from .display import decorate_finding

    report = validate_production_config()
    body = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    findings = []
    for item in body.get("findings") or []:
        if not isinstance(item, dict):
            continue
        decorated = decorate_finding(dict(item))
        findings.append(
            _safe_mapping(
                decorated,
                ("code", "severity", "severity_label", "display_title", "user_message"),
            )
        )
    critical = int(body.get("critical_count") or 0)
    warning = int(body.get("warning_count") or 0)
    if critical:
        message = f"自检发现 {critical} 个严重配置问题，请先处理后使用"
    elif warning:
        message = f"自检发现 {warning} 个提醒项，不影响启动，建议查看"
    else:
        message = "自检通过，未发现需要处理的配置问题"
    return {
        "ok": bool(body.get("ok")),
        "critical_count": critical,
        "warning_count": warning,
        "user_message": message,
        "findings": findings,
    }


def _log_name(path: Path) -> str | None:
    """只接受 ``*.log`` 的安全基名，其余一律拒绝（白名单之外的入口）。"""
    name = path.name
    if not name.lower().endswith(".log"):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    return name


def _tail_log(path: Path) -> str:
    data = path.read_bytes()[-_MAX_LOG_BYTES:]
    text = data.decode("utf-8", "replace").splitlines()[-_MAX_LOG_LINES:]
    return sanitize_text("\n".join(text))


def _collect_logs(log_dir: Path) -> Dict[str, str]:
    if not log_dir.is_dir():
        return {}
    try:
        files = sorted(
            (p for p in log_dir.glob("*.log") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {}
    members: Dict[str, str] = {}
    for path in files:
        name = _log_name(path)
        if not name:
            continue
        # 包内成员名对用户友好：error.log 以 issue.log 的名字出现，别把人吓到。
        member = f"{LOG_MEMBER_PREFIX}{log_member_name(name)}.txt"
        try:
            members[member] = _tail_log(path)
        except OSError as exc:
            members[member] = (
                f"[日志读取失败] {type(exc).__name__}；请确认日志文件未被占用。"
            )
        if len(members) >= _MAX_LOG_FILES:
            break
    return members


def _assert_whitelisted(members: Iterable[str]) -> None:
    allowed = set(BUNDLE_MEMBERS) | set(OPTIONAL_MEMBERS)
    for name in members:
        if name in allowed:
            continue
        if name.startswith(LOG_MEMBER_PREFIX) and name.count("/") == 1:
            continue
        raise ValueError(f"诊断包含未登记成员：{name}")


def _manifest(members: Mapping[str, bytes]) -> Dict[str, Any]:
    return {
        "schema": 1,
        "redaction": "username-path+secret",
        "members": [
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for name, data in sorted(members.items())
        ],
    }


_README = APP_NAME + """ · 一键诊断包
=====================

这个包用于排查启动、模型连接或运行异常，可以安全地发给技术支持。

这个包不含你的密钥、令牌或密码
------------------------------
* 模型 API Key、登录令牌、密码、口令哈希、完整性密钥都不会写入；
* 路径里的用户名已替换为 <用户>；
* 内容按白名单生成，只有下面这些文件。

包含哪些文件
------------
* summary.json    版本号、提交号、操作系统与架构、安装方式、数据目录（已脱敏）
* health.json     网关存活检查结果
* readiness.json  依赖就绪检查结果（只有结论与人话说明）
* selfcheck.json  生产配置预检结论（只有规则代码与级别，不含配置值）
* logs/           最近的日志尾部
  """ + LOG_MEMBER_ALIAS_NOTE + """
* scan_rule_hit.json 未包含的文件上命中了哪条规则、几次（只在剔除时出现）
* manifest.json   每个文件的校验值

本次是否完整
------------
__COMPLETENESS__
N 是包内文件数，M 是被剔除的数量；N + M 就是本次计划收集的文件总数。

怎么用
------
把这个 zip 发给技术支持，或在反馈问题时附上。日志只保留最近一段，
不会包含历史会话全文。
"""


def _readme_text(omitted: Iterable[Mapping[str, Any]], *, included: int) -> str:
    """README 首页：剔除说明（有的话）+ 完整性自证那一行。

    ``included`` 是最终包内成员数（含 manifest.json 自身）；``included + len(omitted)``
    就是本次计划收集的文件总数，用户数一遍文件即可核对。
    """
    items = list(omitted)
    base = (
        omitted_readme(
            _README,
            items,
            marker="这个包用于排查启动、模型连接或运行异常，可以安全发给技术支持。",
            log_prefix=LOG_MEMBER_PREFIX,
            member_name=REDACTED_MEMBERS_MEMBER,
        )
        if items
        else _README
    )
    return base.replace("__COMPLETENESS__", completeness_line(included, len(items)), 1)


def _redacted_members_text(omitted: Iterable[Mapping[str, Any]]) -> str:
    """被剔除成员清单：只有文件名与命中条数，**不写任何命中内容**。"""
    return omitted_members_text(omitted)


class _HeadersOnlyRequest:
    """只提供 ``headers`` 的最小请求对象，供探针函数在进程内复用。"""

    def __init__(self, trace_id: str) -> None:
        self.headers = {"X-Trace-ID": trace_id}


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def _retention_payload(log_dir: Path) -> dict[str, Any]:
    """最近一次日志保留清理的结果（保留 / 删除清单），供诊断包展示。

    导出前主动清一轮，保证包里的「保留 / 删除」是本次导出时的真实状态，
    而不是上一轮巡检留下的旧报告；清理失败不拦住导出。
    """
    try:
        prune_log_retention(log_dir)
    except Exception:  # noqa: BLE001 - 清理失败比导出失败轻
        logger.warning("诊断包导出前的日志保留清理失败", exc_info=True)
    report = read_retention_report(log_dir)
    if report is None:
        return {"status": "没有清理报告"}
    report["status"] = "ok"
    return report


def build_bundle(root: Path | None = None, log_dir: Path | None = None) -> BundleBuild:
    """生成诊断包（降级导出）。

    逐成员做两道扫描（``leak_violations``/``contains_secret`` + 正向 ``scan_text``）：

    * 命中的成员**从包里剔除**，其余成员照常打包——用户最需要求助时不能无路可走；
    * 包内补 ``REDACTED-MEMBERS.txt`` 与被剔除条数，README 首页写明少了几个文件；
    * 只有"一个成员都留不下"时才抛 :class:`DiagnosticBundleBlocked` 整体拒绝。
    """
    base = Path(root or REPO_ROOT)
    effective_log_dir = Path(log_dir) if log_dir else _log_dir()
    logs = _collect_logs(effective_log_dir)

    payload: Dict[str, Any] = {
        "README.txt": _README,
        "summary.json": _summary(base, effective_log_dir),
        "health.json": _health_payload(),
        "readiness.json": _readiness_payload(),
        "selfcheck.json": _selfcheck_payload(),
        "log_retention.json": _retention_payload(effective_log_dir),
    }
    payload.update(logs)

    files: Dict[str, bytes] = {}
    omitted: list[Dict[str, Any]] = []
    for name, content in payload.items():
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        else:
            text = str(content)
        text = sanitize_text(text)
        reasons: Dict[str, int] = {}
        # 双保险：``leak_violations`` 是独立于脱敏规则的正向清单；即使某条脱敏
        # 规则以后回退，这里仍能认出来（现在只剔除该成员，不再整体失败）。
        leaked = leak_violations(text)
        if leaked or contains_secret(text):
            for label in leaked or ["疑似密钥"]:
                reasons[label] = reasons.get(label, 0) + 1
        # 第二道独立扫描（与验收脚本 / 发布闸同源）：专抓"脱敏只遮了一半"，
        # 例如 ``Bearer sk-***REDACTED***abc123`` 这种占位符后面还挂尾巴的样本。
        for label, count in scan_text(text).items():
            reasons[label] = reasons.get(label, 0) + count
        if reasons:
            total = sum(reasons.values())
            omitted.append({"name": name, "hits": reasons, "total": total})
            logger.warning(
                "diagnostic_bundle.omitted member=%s hits=%d detail=%s",
                name,
                total,
                format_hits(reasons),
            )
            continue
        files[name] = text.encode("utf-8")

    if not files:
        merged: Dict[str, int] = {}
        for item in omitted:
            for label, count in item["hits"].items():
                merged[label] = merged.get(label, 0) + int(count)
        names = "、".join(str(item["name"]) for item in omitted) or "全部成员"
        raise DiagnosticBundleBlocked(
            f"诊断包内每个成员都含明文凭据（{names}），没有可安全转发的文件。",
            member="、".join(str(item["name"]) for item in omitted) or "全部成员",
            hits=merged,
            omitted=omitted,
        )

    if omitted:
        files[REDACTED_MEMBERS_MEMBER] = _redacted_members_text(omitted).encode("utf-8")
        files[SCAN_RULE_HIT_NAME] = json.dumps(
            scan_rule_hit_report(omitted), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        # 这几份是本次现生成的说明文本，仍然过一遍扫描，避免"说明文件自己泄密"。
        for generated in (REDACTED_MEMBERS_MEMBER, SCAN_RULE_HIT_NAME):
            generated_hits = scan_text(files[generated].decode("utf-8"))
            if generated_hits:
                log_bundle_blocked("gateway", (), hits=generated_hits)
                raise DiagnosticBundleBlocked(
                    f"说明文件 {generated} 仍含明文凭据（{format_hits(generated_hits)}），"
                    "已停止导出。",
                    member=generated,
                    hits=generated_hits,
                )

    # 完整性自证：N 是最终成员数（含下面要写的 manifest.json），
    # N + len(omitted) 就是计划收集的总数；README 最后生成，避免数字对不上。
    files["README.txt"] = _readme_text(omitted, included=len(files) + 1).encode("utf-8")
    readme_hits = scan_text(files["README.txt"].decode("utf-8"))
    if readme_hits:
        log_bundle_blocked("gateway", (), hits=readme_hits)
        raise DiagnosticBundleBlocked(
            f"说明文件 README.txt 仍含明文凭据（{format_hits(readme_hits)}），已停止导出。",
            member="README.txt",
            hits=readme_hits,
        )

    _assert_whitelisted(files)

    manifest = _manifest(files)
    if omitted:
        # 支持工具只读 manifest 也能知道少了什么；命中内容不在其中。
        manifest["omitted_members"] = [
            {"name": str(item["name"]), "total": int(item["total"])} for item in omitted
        ]
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            _write_member(archive, name, data)
    return BundleBuild(data=buffer.getvalue(), omitted=tuple(omitted))


def bundle_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"liuhao-diagnostics-{stamp}.zip"


router = APIRouter(prefix="/v1", tags=["diagnostics"])


@router.get("/diagnostics/bundle", dependencies=[Depends(require_admin_principal)])
def download_diagnostics_bundle() -> Response:
    """下载一键诊断包（登录闸门由 ``main.py`` 统一挂载）。

    诊断包是整机全局资源（日志、环境、进程快照），不按会话/项目切分，因此只允许
    管理员导出；成员的自有数据走 ``/v1/chat/export`` 等独立通道。内容见
    :data:`BUNDLE_MEMBERS`。

    命中明文凭据的成员会被**剔除**而不是整体失败：用户最需要求助时不能无路可走。
    剔除清单同时写在响应头（``X-LiuHao-Diagnostics-Omitted-Members``）与包内
    ``REDACTED-MEMBERS.txt``；只有"一个成员都留不下"才返回 409。
    """
    name = bundle_filename()
    try:
        build = build_bundle()
    except DiagnosticBundleBlocked as exc:
        # 内部审计事件：只记成员名、规则名与条数，不记任何命中内容。正常路径下
        # 不会走到这里；一旦发生，多半是我们的脱敏/扫描规则误判，需要可检索的证据。
        log_bundle_blocked("gateway", exc.omitted, hits=exc.hits)
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIAGNOSTIC_BUNDLE_BLOCKED",
                "message": (
                    "诊断包内所有文件都未通过密钥自检，已停止导出（罕见兜底：通常是"
                    "扫描规则误判，或日志里残留了无法自动脱敏的片段；这不是常见故障，"
                    "也不是软件坏了）。下一步：先退出鎏灏，把数据目录 logs 下的 "
                    "liuhao.log、error.log、desktop-gateway.log（以及 .log.1 轮转文件）"
                    "改名备份——把 .log 改成 .log.bak-20261108，不要直接删除，它们是"
                    "排查证据；再重新打开并导出一次。如果仍被拦，请把这条提示截图发给"
                    "技术支持，由他们指导你手动提供日志。"
                ),
                "omitted_members": [str(item["name"]) for item in exc.omitted],
            },
        ) from exc
    except ValueError as exc:
        # 自检失败时不导出半成品，也不把内部细节回显给用户。
        from fastapi import HTTPException

        raise HTTPException(
            status_code=500,
            detail="诊断包自检未通过，已停止导出。请重试；若仍失败，请改为手动收集日志。",
        ) from exc

    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-LiuHao-Diagnostics-Filename": name,
        "Cache-Control": "no-store",
    }
    if build.omitted:
        logger.warning(
            "diagnostic_bundle.downgraded members=%d names=%s",
            len(build.omitted),
            ",".join(build.omitted_names),
        )
        headers["X-LiuHao-Diagnostics-Omitted"] = str(len(build.omitted))
        # 纯 ASCII（ensure_ascii=True），前端直接 JSON.parse 得到列表。
        headers["X-LiuHao-Diagnostics-Omitted-Members"] = json.dumps(
            list(build.omitted_names), ensure_ascii=True
        )
    return Response(
        content=build.data,
        media_type="application/zip",
        headers=headers,
    )


def _first_existing(*paths: Path) -> Path:
    """按顺序取第一个存在的路径；都不存在时返回第一个（由调用方按 exists 显示）。"""
    return next((item for item in paths if item.exists()), paths[0])


def _read_local_text(path: Path) -> str:
    """只读本机文件；不存在或不可读时返回空串（界面据 exists 决定怎么显示）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _sbom_summary() -> Dict[str, Any]:
    """本机 SBOM 摘要（组件数 + 生成时间），全部读本地文件，不联网。

    安装态优先读 ``{app}\\sbom-cyclonedx.json``；源码树依次回退到
    ``dist/release-gate`` 与 ``docs/compliance``，保证开发机也能看到同一份摘要。
    """
    candidates = (
        REPO_ROOT / "sbom-cyclonedx.json",
        REPO_ROOT / "dist" / "release-gate" / "sbom-cyclonedx.json",
        REPO_ROOT / "docs" / "compliance" / "sbom-cyclonedx.json",
    )
    path = next((item for item in candidates if item.is_file()), None)
    summary: Dict[str, Any] = {
        "path": str(path) if path else None,
        "exists": bool(path),
        "spec_version": None,
        "component_count": 0,
        "generated_at": None,
        "product_version": None,
        "parse_error": None,
    }
    if path is None:
        return summary
    try:
        payload = json.loads(_read_local_text(path))
    except ValueError as exc:
        summary["parse_error"] = str(exc)
        return summary
    metadata = payload.get("metadata") or {}
    component = metadata.get("component") or {}
    summary["spec_version"] = payload.get("specVersion")
    summary["generated_at"] = metadata.get("timestamp")
    summary["component_count"] = len(payload.get("components") or [])
    summary["product_version"] = component.get("version")
    return summary


@router.get("/about")
def about(context=Depends(require_account_principal)) -> Dict[str, Any]:
    """控制台「关于与隐私」页数据：版本、目录、许可证与第三方声明（只读、离线）。

    许可证全文直接随响应下发：用户在断网的机器上也能逐份点开，不依赖任何外部链接。
    成员视角不下发本机绝对路径：路径字段换成角色占位符，名称/大小/全文保持不变。
    """
    from ..version import APP_VERSION

    info = collect_about()
    notices = _first_existing(
        REPO_ROOT / "THIRD-PARTY-NOTICES.txt",
        REPO_ROOT / "docs" / "compliance" / "THIRD-PARTY-NOTICES.txt",
    )
    licenses_dir = _first_existing(
        REPO_ROOT / "licenses",
        REPO_ROOT / "docs" / "compliance" / "licenses",
    )
    license_entries = []
    if licenses_dir.is_dir():
        for candidate in sorted(licenses_dir.iterdir()):
            if not candidate.is_file():
                continue
            license_entries.append(
                {
                    "name": candidate.name,
                    "bytes": candidate.stat().st_size,
                    "text": _read_local_text(candidate),
                }
            )
    license_files = [entry["name"] for entry in license_entries]
    notices_text = _read_local_text(notices) if notices.is_file() else ""
    product_license = None
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt"):
        candidate = REPO_ROOT / name
        if candidate.is_file():
            product_license = candidate
            break
    payload: Dict[str, Any] = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "commit": info.get("commit"),
        "build_time": info.get("build_time"),
        "app_dir": str(REPO_ROOT),
        "data_dir": str(resolve_data_dir()),
        "log_dir": info.get("log_dir"),
        "product_license": {
            "path": str(product_license) if product_license else None,
            "exists": bool(product_license),
            "bytes": product_license.stat().st_size if product_license else 0,
            "text": _read_local_text(product_license) if product_license else "",
        },
        "third_party": {
            "notices_path": str(notices),
            "notices_exists": notices.is_file(),
            "notices_text": notices_text,
            "licenses_dir": str(licenses_dir),
            "license_files": license_files,
            # 逐份全文：前端用 <details> 展开，断网也能读。
            "license_entries": license_entries,
        },
        "sbom": _sbom_summary(),
        # 「关于与隐私」页的联网事实必须来自后端状态，而不是前端写死的文案。
        "network": _network_disclosure(),
    }
    if not context.is_root:
        # 绝对路径会把本机目录结构（含用户名）带出去；成员只需要知道"这是哪类目录"。
        payload["app_dir"] = "<程序目录>"
        payload["data_dir"] = "<数据目录>"
        if payload["log_dir"]:
            payload["log_dir"] = "<日志目录>"
        if payload["product_license"]["path"]:
            payload["product_license"]["path"] = "<随包文件>"
        payload["third_party"]["notices_path"] = "<随包文件>"
        payload["third_party"]["licenses_dir"] = "<随包目录>"
        if payload["sbom"]["path"]:
            payload["sbom"]["path"] = "<随包文件>"
    return payload


def _network_disclosure() -> Dict[str, Any]:
    """当前进程真实的联网面：默认值 + 实时开关 + 会发到哪。"""
    from ..ai import web_research

    providers = web_research.provider_names()
    return {
        "web_research": {
            "enabled": web_research.enabled(),
            "default_enabled": False,
            "providers": providers,
            "hosts": [web_research.PROVIDER_HOSTS.get(name, name) for name in providers],
            "disclosure": web_research.disclosure_text(providers),
            "max_phrase_chars": web_research.MAX_PHRASE_CHARS,
        },
        "embedding": {
            "cloud_by_default": False,
            "note": (
                "知识库向量化默认只在本机 Ollama 上运行；只有部署时显式设置 "
                "LIUHAO_EMBEDDING_PROVIDER 与对应凭据/地址，文档分片才会发到该服务商；"
                "界面不提供该选择。"
            ),
        },
        "telemetry": False,
        "auto_update_check": False,
    }


__all__ = [
    "BUNDLE_MEMBERS",
    "BundleBuild",
    "DiagnosticBundleBlocked",
    "LOG_MEMBER_PREFIX",
    "OPTIONAL_MEMBERS",
    "REDACTED_MEMBERS_MEMBER",
    "build_bundle",
    "bundle_filename",
    "mask_user_path",
    "router",
    "sanitize_text",
]
