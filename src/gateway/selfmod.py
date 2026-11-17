"""自己改代码一期网关（0.4.6，6-2）。

把已有的 :class:`src.selfmod.service.SelfModService` 接进网关，Owner 专属：

    提议 → 副本上跑测试 → Joseph 点确认 → 合入 → 备份 → 可回滚

* **一次只改一个小地方**：一个提案只含一个文件、内容 ≤ 20KB；
* **先只允许改前端文案/样式类非关键文件**（一期 allowlist，见
  ``is_phase1_allowed``）：``apps/console/console/src/`` 下的
  ``.css / .scss / .less`` 与文案 ``.json``；二档（7-6 Step 2，见
  ``is_phase2_allowed``）再放开前端展示层 A1–A3：``components/`` 与
  ``pages/`` 下的 ``.tsx``（凭据/密钥/权限门控文件除外）、``src/brand.ts``、
  ``lib/`` 下逐个审计过的纯函数模块；其它一律拒绝；
* **禁区永久不动**：``src/security``、``src/identity``、
  ``src/gateway/auth.py``、``src/selfmod`` 等由 ``PathPolicy`` 拦死，
  本网关的 allowlist 是第二道更窄的闸；
* **合入要两次确认**：确认测试（拿 test token）→ 副本测试通过 →
  确认合入（拿 promote token）→ 合入；缺任何一步都拒绝；
* **合入前 Git 快照备份**：``git stash create``（只产生一个悬空提交
  对象，不改分支、不提交）+ 记 HEAD；合入本身先由服务做文件级备份，
  改坏自动回滚（G12 应用后校验不过即恢复）；
* **默认状态是「等确认」**：Joseph 在睡觉，网关绝不自动合入 —— 没有
  任何定时/自动调用 finalize 的路径，合入只发生在他亲手点的那一次
  ``POST .../finalize`` 上。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..selfmod.errors import SelfModError
from ..selfmod.models import FileEdit
from ..selfmod.service import SelfModService
from .policy import require_human_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/selfmod", tags=["selfmod"])

#: 一期 allowlist：前端文案/样式类非关键文件。
PHASE1_ROOT = "apps/console/console/src/"
PHASE1_STYLE_EXTS = frozenset({".css", ".scss", ".less"})
PHASE1_COPY_DIRS = ("locales/", "i18n/", "copy/")
MAX_PHASE1_CONTENT_BYTES = 20 * 1024

#: 一期默认测试命令：控制台构建（在自改命令白名单里）。前端样式/文案
#: 改坏了构建会先失败，不会带着坏产物进合入。
#: 注（7-6 Step 2）：提案只能带一条测试命令，故默认仍为构建；
#: test 与 lint 由门禁 G6 在副本阶段一并跑（第 1 步已落地），
#: 三条命令全部通过才进入合入确认。
DEFAULT_TEST_COMMAND = "npm --prefix apps/console/console run build"


def is_phase1_allowed(path: str) -> bool:
    """一期是否允许自改改这个文件：前端样式/文案类，且在 allowlist 根下。"""
    rel = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not rel.startswith(PHASE1_ROOT):
        return False
    rest = rel[len(PHASE1_ROOT):]
    suffix = Path(rest).suffix.lower()
    if suffix in PHASE1_STYLE_EXTS:
        return True
    if suffix == ".json" and any(
        part in rest for part in PHASE1_COPY_DIRS
    ):
        return True
    return False


#: 二档 allowlist（7-6 Step 2：A1–A3 前端展示层）。放宽只能缩小禁区之外的
#: “可改集合”，禁区本身（``src/selfmod/paths.py`` 与 ``src/selfmod/gates.py``
#: 的两份禁区清单）一字不动；任何提案沾到禁区仍由服务层 PathPolicy 与门禁
#: G0 整份拒绝，不做拆分应用。diff 上限 T1（≤12 文件 / ≤800 行 / 单文件
#: ≤400 行）保持不变。
PHASE2_COMPONENTS_PREFIX = "apps/console/console/src/components/"
PHASE2_PAGES_PREFIX = "apps/console/console/src/pages/"

#: A1 排除项：components/ 与 pages/ 下含凭据/密钥/令牌/权限门控的文件，
#: 按设计 §3.1“任何含密钥、令牌、鉴权头的文件”逐个核对后排除——
#: Login.tsx（登录密码提交）、ModelManager.tsx（apiKey 录入）、
#: ApprovalCenter.tsx（审批密码确认字段）、AccountChrome.tsx
#: （OneTimeSecret 一次性密钥展示）、ui.tsx（Guard 权限门控组件）、
#: AccountSecurity.tsx（改密码）、MyAccountSecurity.tsx（凭据页守卫外壳）、
#: Backup.tsx（备份密码字段）、Members.tsx（建成员密码字段）、
#: MyKeys.tsx（模型 Key 管理）、Recovery.tsx（恢复码明文一次性展示）、
#: MembersPermissions.tsx / PermissionMatrix.tsx（权限矩阵与守卫）、
#: Allowlist.tsx（主机命令白名单，Owner 安全门）。
PHASE2_A1_EXCLUDED = frozenset(
    {
        "apps/console/console/src/components/Login.tsx",
        "apps/console/console/src/components/ModelManager.tsx",
        "apps/console/console/src/components/ApprovalCenter.tsx",
        "apps/console/console/src/components/AccountChrome.tsx",
        "apps/console/console/src/components/ui.tsx",
        "apps/console/console/src/pages/AccountSecurity.tsx",
        "apps/console/console/src/pages/MyAccountSecurity.tsx",
        "apps/console/console/src/pages/Backup.tsx",
        "apps/console/console/src/pages/Members.tsx",
        "apps/console/console/src/pages/MyKeys.tsx",
        "apps/console/console/src/pages/Recovery.tsx",
        "apps/console/console/src/pages/MembersPermissions.tsx",
        "apps/console/console/src/pages/PermissionMatrix.tsx",
        "apps/console/console/src/pages/Allowlist.tsx",
    }
)

#: A2：src 根下的展示用 .ts 常量/文案模块（逐个审计：只含常量/文案）。
#: components/ 与 pages/ 下目前没有非测试 .ts 文件，故 A2 只有这一项；
#: 以后新增的 .ts 文件默认拒绝，需人工审计后逐个加进白名单。
PHASE2_A2_TS = frozenset(
    {
        "apps/console/console/src/brand.ts",
    }
)

#: A3：lib/ 下纯函数模块白名单（逐个文件开档审计：不 import 鉴权/请求/
#: 权限模块，不联网、不写文件、不读写本地存储；只有 type-only 导入的
#: 标注在 tests/test_selfmod_phase2_allowlist.py 里）。
#: 未进白名单的 lib 文件一律拒绝——例如 lib/auth.ts、lib/api.ts、
#: lib/accountCaps.ts（设计点名排除）、lib/assistant.ts（读本地存储的
#: 名字）、lib/mobileRuntime.ts（装 DOM 监听与横幅）。
PHASE2_A3_LIB = frozenset(
    {
        "apps/console/console/src/lib/butler.ts",
        "apps/console/console/src/lib/chartUtils.ts",
        "apps/console/console/src/lib/confirmExpiry.ts",
        "apps/console/console/src/lib/connectionStatus.ts",
        "apps/console/console/src/lib/contracts.ts",
        "apps/console/console/src/lib/errorMessage.ts",
        "apps/console/console/src/lib/format.ts",
        "apps/console/console/src/lib/goalNotices.ts",
        "apps/console/console/src/lib/goalVerdict.ts",
        "apps/console/console/src/lib/honesty.ts",
        "apps/console/console/src/lib/ime.ts",
        "apps/console/console/src/lib/layerLabels.ts",
        "apps/console/console/src/lib/overviewModel.ts",
        "apps/console/console/src/lib/pageMeta.ts",
        "apps/console/console/src/lib/preflightLogic.ts",
        "apps/console/console/src/lib/providerStatus.ts",
        "apps/console/console/src/lib/qr.ts",
        "apps/console/console/src/lib/statusBand.ts",
        "apps/console/console/src/lib/todaySummary.ts",
    }
)

#: A1–A3 共同排除：入口与路由/挂载/权限门控（设计 §3.1 排除项）。
PHASE2_EXCLUDED_ROOT = frozenset(
    {
        "apps/console/console/src/main.tsx",
        "apps/console/console/src/App.tsx",
    }
)


def is_phase2_allowed(path: str) -> bool:
    """二档是否允许自改改这个文件：前端展示层 A1–A3，且不在排除项里。"""
    rel = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not rel.startswith(PHASE1_ROOT):
        return False
    if rel in PHASE2_EXCLUDED_ROOT:
        return False
    if rel in PHASE2_A2_TS or rel in PHASE2_A3_LIB:
        return True
    if rel in PHASE2_A1_EXCLUDED:
        return False
    for prefix in (PHASE2_COMPONENTS_PREFIX, PHASE2_PAGES_PREFIX):
        if rel.startswith(prefix):
            rest = rel[len(prefix):]
            # A1 只放单层目录下的 .tsx；测试文件（*.test.tsx）是测试，
            # 按禁区 §2.8 一律不放；子目录下的文件默认拒绝。
            if "/" in rest or ".test." in rest:
                return False
            return rest.endswith(".tsx")
    return False


def is_selfmod_allowed(path: str) -> bool:
    """自改是否允许改这个文件：一期或二档任一档放行即允许。"""
    return is_phase1_allowed(path) or is_phase2_allowed(path)


def _is_owner(principal: str) -> bool:
    from .auth import _account_is_root

    try:
        return bool(_account_is_root(principal).get("is_root"))
    except Exception:  # noqa: BLE001 - 查不到就按非 Owner 处理
        return False


def _require_owner(principal: str) -> None:
    if not _is_owner(principal):
        raise HTTPException(
            status_code=403,
            detail={"code": "selfmod_owner_only", "message": "自改代码仅最高权限账号（Owner）可用。"},
        )


def _service() -> SelfModService:
    """网关用的服务实例：工作区是仓库根，状态落数据目录。"""
    from src.common.data_paths import data_root

    workspace = Path(__file__).resolve().parents[2]
    root = data_root()
    state_dir = (root / ".selfmod-state") if root is not None else (workspace / ".selfmod-state")
    # allow_promote=True 只是「合入端点能工作」的前提；合入本身仍要
    # PASSED + promote token + 证据件三道关，且只由 Joseph 亲手触发。
    return SelfModService(workspace, state_dir=state_dir, allow_promote=True)


def _git_snapshot(workspace: Path) -> Dict[str, Any]:
    """改前 Git 快照：记 HEAD + ``git stash create`` 的悬空提交对象。

    ``stash create`` 不改分支、不提交、不动工作区，只产生一个可用来恢复
    的提交对象。是 git 仓库时快照哈希必填（拿不到就拒绝合入）；不是 git
    仓库时（如只解压了安装包的机器）返回 ``git: False``，改由服务的文件
    级备份兜底，并在响应里如实说明。
    """
    def _run(args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        head = _run(["rev-parse", "HEAD"])
        if head.returncode != 0:
            return {"git": False, "reason": "not_a_git_repo"}
        stash = _run(["stash", "create"])
        if stash.returncode != 0:
            return {
                "git": True,
                "head": head.stdout.strip(),
                "snapshot": None,
                "reason": "stash_create_failed",
            }
        snapshot = (stash.stdout or "").strip()
        if not snapshot:
            # 工作区干净时 stash create 返回空串：没有可快照的改动，HEAD
            # 本身就是快照。
            return {"git": True, "head": head.stdout.strip(), "snapshot": None,
                    "note": "工作区干净，HEAD 即快照"}
        return {"git": True, "head": head.stdout.strip(), "snapshot": snapshot}
    except Exception as exc:  # noqa: BLE001 - git 不可用时如实报，不编快照
        return {"git": False, "reason": f"git_unavailable:{type(exc).__name__}"}


def _public(proposal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "title": proposal.get("title"),
        "rationale": proposal.get("rationale"),
        "status": proposal.get("status"),
        "reason": proposal.get("reason"),
        "files": [edit.get("path") for edit in proposal.get("edits") or []],
        "base_version": proposal.get("base_version"),
        "backup_dir": proposal.get("backup_dir"),
        "applied_at": proposal.get("applied_at"),
        "rolled_back_at": proposal.get("rolled_back_at"),
        "approver": proposal.get("approver"),
        "approved_at": proposal.get("approved_at"),
    }


class ProposeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    rationale: str = Field("", max_length=2000)
    path: str = Field(..., description="要改的文件（相对路径，二档只允许前端展示层 A1–A3）")
    content: str = Field(..., description="改后文件的完整内容（一次只改一个文件）")
    test_command: str = Field(DEFAULT_TEST_COMMAND, description="副本测试命令（默认控制台构建）")


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=10, description="确认时发回的一次性令牌")


@router.get("/proposals", response_model=Dict[str, Any])
def list_proposals(
    limit: int = 20, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """自改提案列表（Owner 专属）。"""
    _require_owner(principal)
    service = _service()
    try:
        return {"proposals": [_public(item) for item in service.list(limit=max(1, min(100, limit)))]}
    finally:
        service.close()


@router.post("/proposals", response_model=Dict[str, Any])
def propose(
    req: ProposeRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """提议一次自改：只改一个前端样式/文案或展示层文件。状态从 pending 开始等确认。"""
    _require_owner(principal)
    if not is_selfmod_allowed(req.path):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "selfmod_phase1_not_allowed",
                "message": (
                    "自改只允许改前端样式/文案类（一期）与前端展示层 A1–A3（二档："
                    "components/ 与 pages/ 下的 .tsx、src/brand.ts、lib/ 下审计过的"
                    "纯函数模块）；禁区、凭据/密钥/权限门控文件与其它文件一律不接受："
                    f"{req.path}"
                ),
            },
        )
    if len(req.content.encode("utf-8")) > MAX_PHASE1_CONTENT_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"code": "selfmod_too_large", "message": "一期一次只改一个小地方（内容 ≤ 20KB）。"},
        )
    service = _service()
    try:
        proposal = service.propose(
            title=req.title,
            rationale=req.rationale,
            edits=[FileEdit(path=req.path, content=req.content)],
            test_command=req.test_command or DEFAULT_TEST_COMMAND,
        )
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {"proposal": _public(proposal), "status": "pending", "message": "提案已建，等你确认后才会在副本上跑测试。"}


@router.get("/proposals/{proposal_id}", response_model=Dict[str, Any])
def get_proposal(
    proposal_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """合入确认页读的数据：提案 + diff + 状态 + 事件（真实存在，等确认）。"""
    _require_owner(principal)
    service = _service()
    try:
        proposal = service.get(proposal_id)
        return {
            "proposal": _public(proposal),
            "diff": service.show_diff(proposal_id),
            "events": service.events(proposal_id),
        }
    except SelfModError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        service.close()


@router.post("/proposals/{proposal_id}/confirm-test", response_model=Dict[str, Any])
def confirm_test(
    proposal_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """第一次确认：同意在副本上跑测试。返回一次性 test token（只发一次）。"""
    _require_owner(principal)
    service = _service()
    try:
        token = service.confirm(proposal_id, approver=principal, stage="test")
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {"test_token": token, "message": "已确认在副本上跑测试。请用这个令牌调 run-on-copy。"}


@router.post("/proposals/{proposal_id}/run-on-copy", response_model=Dict[str, Any])
def run_on_copy(
    proposal_id: str, req: TokenRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """在副本上应用并跑测试：真实工作区一个字节都不动。"""
    _require_owner(principal)
    service = _service()
    try:
        result = service.run_on_copy(proposal_id, req.token)
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "test": result.get("test"),
        "gates": result.get("gates"),
        "evidence": result.get("evidence"),
        "real_workspace_modified": False,
    }


@router.post("/proposals/{proposal_id}/confirm-promote", response_model=Dict[str, Any])
def confirm_promote(
    proposal_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """第二次确认：副本测试通过后，同意合入真实工作区。返回一次性 promote token。"""
    _require_owner(principal)
    service = _service()
    try:
        token = service.confirm(proposal_id, approver=principal, stage="promote")
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {"promote_token": token, "message": "已确认合入。请用这个令牌调 finalize（合入前会先做 Git 快照备份）。"}


@router.post("/proposals/{proposal_id}/finalize", response_model=Dict[str, Any])
def finalize(
    proposal_id: str, req: TokenRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """合入：Git 快照 → 文件备份 → 应用 → G12 校验（不过自动回滚）。

    只有 Joseph 亲手调这一步才会合入；没有自动合入的路径。
    """
    _require_owner(principal)
    workspace = Path(__file__).resolve().parents[2]
    snapshot = _git_snapshot(workspace)
    if snapshot.get("git") and snapshot.get("reason") == "stash_create_failed":
        raise HTTPException(
            status_code=503,
            detail={"code": "git_snapshot_failed", "message": "Git 快照失败，拒绝合入。"},
        )
    service = _service()
    try:
        updated = service.finalize(proposal_id, req.token)
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {"proposal": _public(updated), "git_snapshot": snapshot}


@router.post("/proposals/{proposal_id}/rollback", response_model=Dict[str, Any])
def rollback(
    proposal_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """回滚到合入前的备份。"""
    _require_owner(principal)
    service = _service()
    try:
        updated = service.rollback(proposal_id)
    except SelfModError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        service.close()
    return {"proposal": _public(updated)}


__all__ = [
    "DEFAULT_TEST_COMMAND",
    "is_phase1_allowed",
    "is_phase2_allowed",
    "is_selfmod_allowed",
    "router",
]
