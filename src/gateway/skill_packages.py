"""技能包生命周期服务层（大升级第二季 V7：技能包生态）。

为什么有这一层
--------------
技能包的落盘安全在 ``skill_install``、清单解析在 ``skill_import``、注册表在
``skill_store``、运行在 ``skill_runtime``——这些在 V7 之前就都在，HTTP 面
（``skills.py``）也齐。但有三件事没有：

1. **安装前预览**：以前只有"装"和"不装"，没有"先把包的权限声明摊开看"。
2. **依赖核对**：清单能声明依赖别的技能（V7 包格式新增 ``author`` /
   ``dependencies``），但安装时没人核对，缺依赖也照装，运行时才炸。
3. **聊天可达**：HTTP 面齐全不等于聊天能管——聊天工具以前只有 ``run_skill``，
   装/卸/升级/列表都得开技能页。这一层就是给聊天工具与 HTTP 预览端点共用的
   服务面，生命周期语义只写一份。

安全口径（与既有层一致，不另起炉灶）
------------------------------------
* 安装的技能一律「未启用」落盘；脚本第一次运行仍须 Boss 在审批流里授权
  （``skill_runtime`` 的既有语义，本层不碰）。
* 包声明的权限只是**声明**：展示给 Boss 看、记进注册表，不产生任何实际
  权限；实际能跑什么仍由 ``runtime.entrypoints`` + 首次授权决定。包内代码
  没有任何路径能绕过审批提权——本层不新增执行入口。
* 依赖缺失/版本不足 → **拒绝安装并点名**，绝不半装；卸载被别的技能依赖的
  技能 → **拒绝并点名依赖方**，绝不静默把依赖方抽空（与删除时绑定 409 同理）。
* 本层不写日志记录任何 Key/令牌（它根本不接触凭据）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .skill_import import (
    ImportedSkill,
    McpConfigDetected,
    SkillImportError,
    parse_upload,
    version_satisfies,
)
from .skill_install import (
    SkillInstallError,
    StagedPackage,
    stage_path,
    stage_upload,
    uninstall as _uninstall_dir,
)
from .skill_runtime import SkillApprovalStore, SkillRuntime
from .skill_store import SkillConflict, SkillNotFound, SkillStore

logger = logging.getLogger(__name__)


class SkillPackageError(ValueError):
    """生命周期操作被拒绝。消息面向用户，必须能直接展示。"""


# ---------------------------------------------------------------------- #
# 依赖核对
# ---------------------------------------------------------------------- #
def check_dependencies(
    dependencies: List[Dict[str, str]],
    store: SkillStore,
    *,
    exclude_skill_id: str = "",
) -> List[Dict[str, Any]]:
    """逐条核对依赖声明，返回每条的状态（不抛错，调用方决定怎么呈现）。

    status: ``satisfied`` / ``missing``（没装）/ ``version_unmet``（装了但
    版本不足或版本无法比较）。核对口径是**已登记**的技能（含未启用）：
    依赖的是"装没装"，不是"启没启用"。
    """
    records = store.list_records()
    by_name: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if exclude_skill_id and str(record.get("id") or "") == exclude_skill_id:
            continue
        by_name[str(record.get("name") or "").casefold()] = record
    out: List[Dict[str, Any]] = []
    for dep in dependencies or []:
        name = str(dep.get("name") or "").strip()
        constraint = str(dep.get("version") or "").strip()
        row: Dict[str, Any] = {"name": name, "requires": constraint}
        found = by_name.get(name.casefold())
        if found is None:
            row["status"] = "missing"
            row["note"] = f"依赖的技能「{name}」还没有安装。"
        else:
            installed_version = str(found.get("version") or "")
            row["installed_version"] = installed_version
            if not constraint:
                row["status"] = "satisfied"
                row["note"] = f"已安装（版本 {installed_version or '未标版本'}）。"
            else:
                ok = version_satisfies(installed_version, constraint)
                if ok is True:
                    row["status"] = "satisfied"
                    row["note"] = (
                        f"已安装版本 {installed_version}，满足 {constraint}。"
                    )
                elif ok is False:
                    row["status"] = "version_unmet"
                    row["note"] = (
                        f"已安装版本 {installed_version or '未标版本'}，"
                        f"不满足要求的 {constraint}。"
                    )
                else:
                    row["status"] = "version_unmet"
                    row["note"] = (
                        f"已安装版本 {installed_version or '未标版本'} "
                        f"无法与要求 {constraint} 比较，按不满足处理。"
                    )
        out.append(row)
    return out


def dependency_problems(
    dependencies: List[Dict[str, str]],
    store: SkillStore,
    *,
    exclude_skill_id: str = "",
) -> List[str]:
    """依赖核对里不满足的条目说明；空列表 = 全部满足。"""
    return [
        row["note"]
        for row in check_dependencies(
            dependencies, store, exclude_skill_id=exclude_skill_id
        )
        if row["status"] != "satisfied"
    ]


def find_dependents(
    skill_name: str,
    store: SkillStore,
    *,
    exclude_skill_id: str = "",
) -> List[Dict[str, Any]]:
    """哪些已登记技能声明依赖 ``skill_name``（卸载守卫用）。"""
    target = str(skill_name or "").casefold()
    out: List[Dict[str, Any]] = []
    for record in store.list_records():
        if exclude_skill_id and str(record.get("id") or "") == exclude_skill_id:
            continue
        for dep in record.get("dependencies") or []:
            if str(dep.get("name") or "").casefold() == target:
                out.append(
                    {"skill_id": record.get("id"), "name": record.get("name")}
                )
                break
    return out


# ---------------------------------------------------------------------- #
# 预览（安装前展示权限声明）
# ---------------------------------------------------------------------- #
def declaration_summary(
    spec: ImportedSkill,
    staged: Optional[StagedPackage],
    store: SkillStore,
    *,
    exclude_skill_id: str = "",
) -> Dict[str, Any]:
    """把一个已解析的包收敛成"安装前给 Boss 看的声明页"。

    只读：不落盘、不建记录。``permissions`` 是包**声明**的权限面
    （declared permissions + capabilities + allowed-tools），展示口径与
    注册表一致；它不等于实际授权，实际执行仍走运行期审批。
    """
    entrypoints = list((spec.runtime or {}).get("entrypoints") or [])
    deps = check_dependencies(
        spec.dependencies, store, exclude_skill_id=exclude_skill_id
    )
    files = list(staged.files) if staged is not None else list(spec.files)
    return {
        "name": spec.name,
        "version": spec.version,
        "author": spec.author,
        "description": spec.description,
        "license": spec.license,
        "format": spec.format,
        "permissions": {
            "declared": list(spec.permissions),
            "capabilities": list(spec.capabilities),
            "allowed_tools": list(spec.allowed_tools),
            "declared_risk": spec.declared_risk,
        },
        "entrypoints": [
            {
                "id": str(entry.get("id") or ""),
                "script": str(entry.get("script") or ""),
                "interpreter": str(entry.get("interpreter") or ""),
            }
            for entry in entrypoints
            if isinstance(entry, dict)
        ],
        "dependencies": deps,
        "dependencies_satisfied": all(
            row["status"] == "satisfied" for row in deps
        ),
        "files": files,
        "package_hash": staged.package_hash if staged is not None else "",
        "has_scripts": bool(spec.has_scripts),
        "execution_note": (
            "安装后技能默认未启用；脚本第一次运行还要在审批里单独确认，"
            "声明的权限不会自动变成实际权限。"
            if entrypoints
            else "这个包没有声明可执行入口，安装后只作为资料使用，不会运行代码。"
        ),
        "import_report": list(spec.import_report),
    }


def _parse_staged(staged: StagedPackage, manifest_name: str) -> ImportedSkill:
    try:
        return parse_upload(manifest_name, staged.read_text())
    except McpConfigDetected:
        raise
    except SkillImportError:
        raise


def inspect_upload(filename: str, data: bytes) -> Dict[str, Any]:
    """预览一个上传的包（zip/md/json）：只解析与核对，不安装。"""
    store = SkillStore()
    try:
        with stage_upload(filename, data) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            return declaration_summary(spec, staged, store)
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


def inspect_path(path: str) -> Dict[str, Any]:
    """预览一个本机路径的包（目录/zip/md/json）：只解析与核对，不安装。"""
    store = SkillStore()
    try:
        with stage_path(path) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            return declaration_summary(spec, staged, store)
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


# ---------------------------------------------------------------------- #
# 安装 / 升级 / 卸载 / 列表
# ---------------------------------------------------------------------- #
def _install_parsed(
    spec: ImportedSkill,
    staged: Optional[StagedPackage],
    *,
    principal: str,
    origin: str,
) -> Dict[str, Any]:
    """与 HTTP ``_install_parsed`` 同语义的落盘（聊天面用，不抛 HTTP 异常）。

    顺序刻意与 HTTP 面一致：先核对依赖 → 建记录（未启用）→ 包落盘 →
    登记运行包；中途失败回滚记录，绝不留半装技能。
    """
    store = SkillStore()
    problems = dependency_problems(spec.dependencies, store)
    if problems:
        raise SkillPackageError(
            "依赖未满足，已拒绝安装：" + "；".join(problems)
        )
    record: Optional[Dict[str, Any]] = None
    try:
        record = store.create(
            name=spec.name,
            description=spec.description,
            source="imported",
            version=spec.version,
            author=spec.author,
            dependencies=spec.dependencies,
            capabilities=list(spec.capabilities),
            declared_permissions=list(spec.permissions),
            declared_risk=spec.declared_risk,
            content=spec.content,
            origin=origin,
            enabled=False,
            fmt=spec.format,
            license=spec.license,
            allowed_tools=list(spec.allowed_tools),
            metadata=spec.metadata,
            raw_frontmatter=spec.raw_frontmatter,
            has_scripts=spec.has_scripts,
            script_refs=spec.script_refs,
            files=list(staged.files) if staged is not None else list(spec.files),
            name_source=spec.name_source,
            import_report=spec.import_report,
            execution_allowed=spec.execution_allowed,
        )
        skill_id = str(record.get("id") or "")
        if staged is not None:
            entrypoints = list((spec.runtime or {}).get("entrypoints") or [])
            staged.finalize(entrypoints)
            install_dir = staged.commit(skill_id, overwrite=False)
            SkillRuntime().save_package(
                skill_id,
                {
                    "skill_id": skill_id,
                    "install_dir": install_dir,
                    "origin": origin,
                    "format": spec.format,
                    "package_hash": staged.package_hash,
                    "files": staged.hashes,
                    "runtime": {
                        "limits": dict((spec.runtime or {}).get("limits") or {}),
                        "entrypoints": entrypoints,
                    },
                    "installed_at": record.get("updated_at")
                    or record.get("created_at"),
                },
            )
    except SkillConflict as exc:
        raise SkillPackageError(str(exc)) from exc
    except (SkillInstallError, ValueError) as exc:
        if record is not None:
            _rollback_record(store, str(record.get("id") or ""))
        raise SkillPackageError(str(exc)) from exc
    logger.info(
        "skill_packages: installed %s from %s by %s",
        record.get("id"),
        origin,
        principal,
    )
    return {
        "skill_id": record.get("id"),
        "name": record.get("name"),
        "version": record.get("version"),
        "author": record.get("author"),
        "enabled": False,
        "note": (
            "安装完成，技能为「未启用」；启用后第一次运行脚本仍需单独确认。"
        ),
    }


def _rollback_record(store: SkillStore, skill_id: str) -> None:
    if not skill_id:
        return
    try:
        store.delete(skill_id)
    except Exception:  # noqa: BLE001 - 回滚尽力而为，但要留日志
        logger.exception("skill_packages: rollback record %s failed", skill_id)
    try:
        SkillRuntime().remove_package(skill_id)
    except Exception:  # noqa: BLE001
        logger.exception("skill_packages: rollback package %s failed", skill_id)


def install_from_path(path: str, *, principal: str = "chat") -> Dict[str, Any]:
    """从本机路径安装技能包（目录/zip/md/json），与 HTTP /install/local 同语义。"""
    try:
        with stage_path(path) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            return _install_parsed(
                spec,
                staged,
                principal=principal,
                origin=path,
            )
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


def install_from_upload(
    filename: str, data: bytes, *, principal: str = "chat"
) -> Dict[str, Any]:
    """从上传内容安装技能包（zip/md/json）。"""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        with stage_upload(filename, data) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            materialize = suffix == "zip" or bool(
                (spec.runtime or {}).get("entrypoints")
            )
            return _install_parsed(
                spec,
                staged if materialize else None,
                principal=principal,
                origin=filename,
            )
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


def _upgrade_parsed(
    skill_id: str,
    spec: ImportedSkill,
    staged: StagedPackage,
    *,
    principal: str,
    origin: str,
) -> Dict[str, Any]:
    """与 HTTP upgrade 同语义：覆盖落盘 + 同步注册表包级字段 + 授权失效。"""
    store = SkillStore()
    try:
        record = store.get(skill_id)
    except SkillNotFound as exc:
        raise SkillPackageError(f"没有找到技能「{skill_id}」。") from exc
    if str(spec.name or "").casefold() != str(record.get("name") or "").casefold():
        raise SkillPackageError(
            f"升级包的名字是「{spec.name}」，与技能「{record.get('name')}」不一致；"
            "升级只能换同一个技能的新版本，装新技能请用安装。"
        )
    problems = dependency_problems(
        spec.dependencies, store, exclude_skill_id=skill_id
    )
    if problems:
        raise SkillPackageError(
            "新版依赖未满足，已拒绝升级（旧版保持不变）：" + "；".join(problems)
        )
    entrypoints = list((spec.runtime or {}).get("entrypoints") or [])
    try:
        staged.finalize(entrypoints)
        install_dir = staged.commit(skill_id, overwrite=True)
        SkillRuntime().save_package(
            skill_id,
            {
                "skill_id": skill_id,
                "install_dir": install_dir,
                "origin": origin,
                "format": spec.format,
                "package_hash": staged.package_hash,
                "files": staged.hashes,
                "runtime": {
                    "limits": dict((spec.runtime or {}).get("limits") or {}),
                    "entrypoints": entrypoints,
                },
                "installed_at": record.get("updated_at")
                or record.get("created_at"),
            },
        )
        updated = store.update_package_fields(
            skill_id,
            version=spec.version,
            author=spec.author,
            dependencies=spec.dependencies,
            description=spec.description,
        )
    except SkillInstallError as exc:
        raise SkillPackageError(str(exc)) from exc
    logger.info(
        "skill_packages: upgraded %s from %s by %s", skill_id, origin, principal
    )
    return {
        "skill_id": skill_id,
        "name": updated.get("name"),
        "version": updated.get("version"),
        "author": updated.get("author"),
        "note": (
            "升级完成，包哈希已变化：之前记住的运行授权自动失效，"
            "下次运行需要重新确认。"
        ),
    }


def upgrade_from_path(
    skill_id: str, path: str, *, principal: str = "chat"
) -> Dict[str, Any]:
    """用本机路径的新包升级一个已装技能。"""
    try:
        with stage_path(path) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            return _upgrade_parsed(
                skill_id, spec, staged, principal=principal, origin=path
            )
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


def upgrade_from_upload(
    skill_id: str, filename: str, data: bytes, *, principal: str = "chat"
) -> Dict[str, Any]:
    """用上传的新包升级一个已装技能。"""
    try:
        with stage_upload(filename, data) as staged:
            spec = _parse_staged(staged, staged.manifest_name)
            return _upgrade_parsed(
                skill_id, spec, staged, principal=principal, origin=filename
            )
    except (SkillInstallError, SkillImportError) as exc:
        raise SkillPackageError(str(exc)) from exc


def uninstall_package(skill_id: str, *, principal: str = "chat") -> Dict[str, Any]:
    """卸载技能：员工绑定与技能依赖两道守卫都过了才删（与 HTTP DELETE 同理）。

    顺序与 HTTP 面一致：守卫 → 删记录 → 清绑定残留 → 删包目录 →
    清运行登记与全部已记住授权。
    """
    from .skill_bindings import SkillBindingError, SkillBindingStore

    store = SkillStore()
    try:
        record = store.get(skill_id)
    except SkillNotFound as exc:
        raise SkillPackageError(f"没有找到技能「{skill_id}」。") from exc
    name = str(record.get("name") or "")

    binding_store = SkillBindingStore()
    try:
        in_use = binding_store.employees_for_skill(skill_id)
    except SkillBindingError as exc:
        raise SkillPackageError(
            "读取员工绑定情况失败，为避免误删正在被员工使用的技能，"
            f"本次卸载没有执行（{exc}）。"
        ) from exc
    if in_use:
        raise SkillPackageError(
            f"技能「{name}」仍绑定 {len(in_use)} 位员工：{'、'.join(in_use)}。"
            "请先解除绑定，或先停用该技能，再卸载。"
        )
    dependents = find_dependents(name, store, exclude_skill_id=skill_id)
    if dependents:
        names = "、".join(str(row.get("name") or "") for row in dependents)
        raise SkillPackageError(
            f"技能「{name}」被这些技能声明为依赖：{names}。"
            "先卸载或升级它们，再卸载本技能，避免把依赖方抽空。"
        )
    removed = store.delete(skill_id)
    try:
        binding_store.remove_skill(skill_id)
    except SkillBindingError as exc:
        logger.error(
            "skill_packages: binding cleanup failed for %s: %s", skill_id, exc
        )
    if not _uninstall_dir(skill_id):
        logger.warning(
            "skill_packages: package dir for %s not removed (absent or locked)",
            skill_id,
        )
    try:
        SkillRuntime().remove_package(skill_id)
        SkillApprovalStore().revoke_all(skill_id=skill_id, revoked_by=principal)
    except Exception:  # noqa: BLE001 - 清理尽力而为
        logger.exception("skill_packages: runtime cleanup failed for %s", skill_id)
    logger.info("skill_packages: uninstalled %s by %s", skill_id, principal)
    return {"deleted": True, "skill_id": removed.get("id"), "name": name}


def list_packages() -> List[Dict[str, Any]]:
    """已登记技能 + 已装包状态（聊天列表与预览共用）。"""
    store = SkillStore()
    runtime = SkillRuntime()
    out: List[Dict[str, Any]] = []
    for record in store.list_records():
        skill_id = str(record.get("id") or "")
        package = runtime.package(skill_id) or {}
        out.append(
            {
                "skill_id": skill_id,
                "name": record.get("name"),
                "version": record.get("version") or "",
                "author": record.get("author") or "",
                "description": record.get("description") or "",
                "enabled": bool(record.get("enabled")),
                "source": record.get("source") or "",
                "package_installed": bool(package),
                "package_hash": package.get("package_hash") or "",
                "dependencies": list(record.get("dependencies") or []),
            }
        )
    return out


__all__ = [
    "SkillPackageError",
    "check_dependencies",
    "dependency_problems",
    "find_dependents",
    "declaration_summary",
    "inspect_upload",
    "inspect_path",
    "install_from_path",
    "install_from_upload",
    "upgrade_from_path",
    "upgrade_from_upload",
    "uninstall_package",
    "list_packages",
]
