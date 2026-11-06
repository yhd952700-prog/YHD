"""Controlled self-modification service.

The runtime never edits its own source tree directly. The closed loop is:

    propose (whole-file edits) -> show unified diff -> human confirm
      -> copy the workspace -> apply there -> run tests there -> verdict
      -> (optional, separately confirmed) promote to the real workspace
      -> rollback record

``run_on_copy`` never touches the real workspace. ``finalize`` is disabled unless
the service was constructed with ``allow_promote=True`` and requires a second
human-issued token.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import tempfile
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.common.atomic_write import atomic_write_bytes

from .errors import (
    ConfirmationRequired,
    InvalidEdit,
    ProposalNotFound,
    PromotionDisabled,
    SandboxError,
    SelfModError,
    StaleProposal,
    TestCommandMissing,
)
from .ledger import SelfModLedger
from .models import FileEdit, ProposalStatus, ResourceLimits
from .patcher import apply_edits, generate_diff, hash_text, read_text, render
from .paths import PathPolicy
from .sandbox import command_from, run_isolated

_BACKUP_SCHEMA = "liuhao.selfmod-backup/1"
_COPY_IGNORE = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "upgrade-backups",
    "upgrade-state",
    ".selfmod-state",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "dist",
    "build",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.log",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_project_version(workspace: Path) -> Optional[str]:
    pyproject = workspace / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project") if isinstance(data, dict) else None
    version = project.get("version") if isinstance(project, dict) else None
    return version.strip() if isinstance(version, str) and version.strip() else None


def _normalise_edits(edits: Iterable[FileEdit | Dict[str, Any]]) -> List[FileEdit]:
    result: List[FileEdit] = []
    for item in edits:
        if isinstance(item, FileEdit):
            result.append(item)
            continue
        if not isinstance(item, dict):
            raise InvalidEdit(f"无法识别的编辑项：{item!r}")
        content = item.get("content")
        if content is not None and not isinstance(content, str):
            raise InvalidEdit("编辑项 content 必须是字符串或 null（删除）")
        result.append(FileEdit(path=str(item.get("path", "")), content=content))
    if not result:
        raise InvalidEdit("提案至少需要一项文件编辑")
    return result


class SelfModService:
    def __init__(
        self,
        workspace: str | Path,
        *,
        state_dir: str | Path | None = None,
        allow_promote: bool = False,
        default_test_command: str | Sequence[str] | None = None,
        limits: Optional[ResourceLimits] = None,
        max_file_bytes: int = 1024 * 1024,
        max_diff_bytes: int = 4 * 1024 * 1024,
        blocked_prefixes: Optional[Sequence[str]] = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.state_dir = (
            Path(state_dir).resolve()
            if state_dir
            else self.workspace / ".selfmod-state"
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "backups").mkdir(parents=True, exist_ok=True)
        self.ledger = SelfModLedger(self.state_dir / "selfmod.db")
        self.allow_promote = allow_promote
        self.limits = limits or ResourceLimits()
        self.max_file_bytes = max_file_bytes
        self.max_diff_bytes = max_diff_bytes
        self.policy = PathPolicy(
            self.workspace,
            blocked_prefixes=blocked_prefixes or PathPolicy(self.workspace).blocked_prefixes,
        )
        self.default_test_command = (
            command_from(default_test_command) if default_test_command else None
        )

    def close(self) -> None:
        self.ledger.close()

    # ------------------------------------------------------------------
    # Proposal / diff
    # ------------------------------------------------------------------
    def propose(
        self,
        *,
        title: str,
        rationale: str,
        edits: Iterable[FileEdit | Dict[str, Any]],
        test_command: str | Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise InvalidEdit("提案标题不能为空")
        normalised = _normalise_edits(edits)
        command = (
            command_from(test_command)
            if test_command is not None
            else list(self.default_test_command or [])
        )
        diff, base_hashes = generate_diff(
            normalised,
            self.policy,
            max_file_bytes=self.max_file_bytes,
            max_diff_bytes=self.max_diff_bytes,
        )
        proposal_id = uuid.uuid4().hex
        proposal = self.ledger.create(
            proposal_id=proposal_id,
            title=title,
            rationale=rationale or "",
            status=ProposalStatus.PENDING.value,
            base_version=_read_project_version(self.workspace),
            diff_text=diff,
            edits=[edit.to_dict() for edit in normalised],
            base_hashes=base_hashes,
            test_command=command or None,
        )
        self.ledger.add_event(
            proposal_id,
            "propose",
            ProposalStatus.PENDING.value,
            {"files": [edit.path for edit in normalised]},
        )
        return proposal

    def get(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self.ledger.get(proposal_id)
        if not proposal:
            raise ProposalNotFound(f"提案不存在：{proposal_id}")
        return proposal

    def list(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return self.ledger.list(limit=limit)

    def show_diff(self, proposal_id: str) -> str:
        return render(self.get(proposal_id))

    def events(self, proposal_id: str) -> List[Dict[str, Any]]:
        return self.ledger.events(proposal_id)

    # ------------------------------------------------------------------
    # Human confirmation
    # ------------------------------------------------------------------
    def confirm(self, proposal_id: str, *, approver: str, stage: str = "test") -> str:
        if stage not in ("test", "promote"):
            raise ValueError("stage 只能是 test 或 promote")
        approver = (approver or "").strip()
        if not approver:
            raise ConfirmationRequired("确认人不能为空")
        proposal = self.get(proposal_id)
        status = proposal["status"]
        if stage == "test" and status not in (
            ProposalStatus.PENDING.value,
            ProposalStatus.CONFIRMED.value,
        ):
            raise ConfirmationRequired(f"当前状态 {status} 不允许再确认测试")
        if stage == "promote" and status != ProposalStatus.PASSED.value:
            raise ConfirmationRequired(
                f"当前状态 {status} 尚未通过副本测试，不能确认应用到真实工作区"
            )
        token = secrets.token_urlsafe(32)
        fields: Dict[str, Any] = {
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "token_stage": stage,
            "approver": approver,
            "approved_at": _utc_now(),
        }
        if stage == "test":
            fields["status"] = ProposalStatus.CONFIRMED.value
        self.ledger.update(proposal_id, **fields)
        self.ledger.add_event(proposal_id, "confirm", stage, {"approver": approver})
        return token

    def _consume_token(self, proposal_id: str, token: str, stage: str) -> None:
        proposal = self.get(proposal_id)
        stored = proposal.get("token_hash")
        if not stored or not token:
            raise ConfirmationRequired("缺少人工确认令牌，拒绝执行")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if proposal.get("token_stage") != stage or not secrets.compare_digest(
            digest, str(stored)
        ):
            raise ConfirmationRequired("确认令牌无效或已过期，拒绝执行")
        self.ledger.update(
            proposal_id, token_hash=None, token_stage=None
        )

    # ------------------------------------------------------------------
    # Copy + test
    # ------------------------------------------------------------------
    def _verify_base_hashes(self, proposal: Dict[str, Any]) -> None:
        for edit in proposal["edits"]:
            rel = self.policy.normalise(str(edit["path"]))
            current = read_text(self.workspace / rel, max_bytes=self.max_file_bytes)
            expected = proposal["base_hashes"].get(rel)
            if hash_text(current) != expected:
                raise StaleProposal(
                    f"工作区文件在提案后已变更：{rel}；为防止覆盖他人修改，拒绝执行"
                )

    def _copy_workspace(self) -> tuple[Path, Path]:
        temp_root = Path(tempfile.mkdtemp(prefix="liuhao-selfmod-copy-"))
        copy_root = temp_root / "workspace"
        shutil.copytree(
            self.workspace,
            copy_root,
            ignore=shutil.ignore_patterns(*_COPY_IGNORE),
            symlinks=False,
        )
        return temp_root, copy_root

    def run_on_copy(self, proposal_id: str, token: str) -> Dict[str, Any]:
        proposal = self.get(proposal_id)
        if proposal["status"] != ProposalStatus.CONFIRMED.value:
            raise ConfirmationRequired(
                f"当前状态 {proposal['status']} 不允许执行副本测试"
            )
        self._consume_token(proposal_id, token, "test")
        command = proposal.get("test_command")
        if not command:
            reason = "未配置测试命令，无法判定结果；拒绝应用"
            self.ledger.update(
                proposal_id,
                status=ProposalStatus.REJECTED.value,
                reason=reason,
            )
            self.ledger.add_event(proposal_id, "run", "rejected", {"reason": reason})
            raise TestCommandMissing(reason)

        try:
            self._verify_base_hashes(proposal)
            temp_root, copy_root = self._copy_workspace()
        except (OSError, SelfModError) as exc:
            reason = f"副本准备失败：{exc}"
            self.ledger.update(
                proposal_id,
                status=ProposalStatus.REJECTED.value,
                reason=reason,
            )
            raise

        try:
            self.ledger.update(proposal_id, status=ProposalStatus.TESTING.value)
            edits = [FileEdit(path=edit["path"], content=edit["content"]) for edit in proposal["edits"]]
            try:
                apply_edits(copy_root, edits, self.policy)
                run = run_isolated(command, cwd=copy_root, limits=self.limits)
            except SandboxError as exc:
                self._reject(proposal_id, f"测试命令执行失败：{exc}")
                raise
            except Exception as exc:
                self._reject(proposal_id, f"副本应用或测试执行异常：{exc}")
                raise

            if run.timed_out:
                reason = f"测试超时（>{self.limits.timeout_seconds}秒），进程树已终止"
            elif run.limit_reason == "memory":
                reason = f"测试内存超过上限（{self.limits.memory_mb}MB），进程树已终止"
            elif run.limit_reason == "cpu":
                reason = f"测试 CPU 时间超过上限（{self.limits.cpu_seconds}秒），进程树已终止"
            elif run.returncode != 0:
                reason = f"测试失败（退出码 {run.returncode}）"
            else:
                reason = "副本测试通过"

            status = (
                ProposalStatus.PASSED.value
                if run.passed
                else ProposalStatus.REJECTED.value
            )
            updated = self.ledger.update(
                proposal_id,
                status=status,
                reason=reason,
                test_summary_json=run.to_dict(),
            )
            self.ledger.add_event(proposal_id, "run", status, run.to_dict())
            return {
                "proposal": updated,
                "test": run.to_dict(),
                "status": status,
                "reason": reason,
                "real_workspace_modified": False,
            }
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _reject(self, proposal_id: str, reason: str) -> None:
        self.ledger.update(
            proposal_id,
            status=ProposalStatus.REJECTED.value,
            reason=reason,
        )
        self.ledger.add_event(proposal_id, "run", "rejected", {"reason": reason})

    # ------------------------------------------------------------------
    # Promote / rollback (real workspace)
    # ------------------------------------------------------------------
    def finalize(self, proposal_id: str, token: str) -> Dict[str, Any]:
        if not self.allow_promote:
            raise PromotionDisabled(
                "当前为副本验证模式（allow_promote=False），不会修改真实工作区"
            )
        proposal = self.get(proposal_id)
        if proposal["status"] != ProposalStatus.PASSED.value:
            raise ConfirmationRequired(
                f"当前状态 {proposal['status']} 未通过副本测试，拒绝应用真实工作区"
            )
        self._consume_token(proposal_id, token, "promote")
        self._verify_base_hashes(proposal)
        edits = [
            FileEdit(path=edit["path"], content=edit["content"])
            for edit in proposal["edits"]
        ]
        backup_dir = self._backup_for_promotion(proposal_id, edits)
        try:
            apply_edits(self.workspace, edits, self.policy)
        except Exception as exc:
            try:
                self._restore_backup(backup_dir)
            except Exception as restore_exc:
                self.ledger.update(
                    proposal_id,
                    status=ProposalStatus.FAILED.value,
                    reason=f"应用失败：{exc}；回滚也失败：{restore_exc}",
                )
                raise SelfModError(
                    f"应用到真实工作区失败且回滚失败：{exc} / {restore_exc}"
                ) from exc
            self.ledger.update(
                proposal_id,
                status=ProposalStatus.REJECTED.value,
                reason=f"应用失败，已恢复原状：{exc}",
            )
            self.ledger.add_event(proposal_id, "finalize", "rejected", {"reason": str(exc)})
            raise SelfModError(f"应用到真实工作区失败，已恢复原状：{exc}") from exc

        updated = self.ledger.update(
            proposal_id,
            status=ProposalStatus.APPLIED.value,
            applied_at=_utc_now(),
            backup_dir=str(backup_dir),
            reason="已应用到真实工作区（带可回滚备份）",
        )
        self.ledger.add_event(
            proposal_id, "finalize", ProposalStatus.APPLIED.value, {"backup_dir": str(backup_dir)}
        )
        return updated

    def rollback(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self.get(proposal_id)
        if proposal["status"] != ProposalStatus.APPLIED.value:
            raise SelfModError(f"当前状态 {proposal['status']} 没有可回滚的应用")
        backup_dir = proposal.get("backup_dir")
        if not backup_dir:
            raise SelfModError("提案没有备份记录，无法回滚")
        self._restore_backup(Path(backup_dir))
        updated = self.ledger.update(
            proposal_id,
            status=ProposalStatus.ROLLED_BACK.value,
            rolled_back_at=_utc_now(),
            reason="已回滚到应用前状态",
        )
        self.ledger.add_event(proposal_id, "rollback", ProposalStatus.ROLLED_BACK.value)
        return updated

    def _backup_for_promotion(self, proposal_id: str, edits: Sequence[FileEdit]) -> Path:
        backup_dir = self.state_dir / "backups" / f"{proposal_id}-{uuid.uuid4().hex[:8]}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        entries: List[Dict[str, Any]] = []
        for edit in edits:
            rel = self.policy.normalise(edit.path)
            source = self.workspace / rel
            existed = source.is_file()
            if existed:
                payload = source.read_bytes()
                target = backup_dir / "files" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, payload)
                digest = hashlib.sha256(payload).hexdigest()
            else:
                digest = None
            entries.append(
                {"path": rel, "existed": existed, "sha256": digest}
            )
        manifest = {
            "schema": _BACKUP_SCHEMA,
            "proposal_id": proposal_id,
            "created_at": _utc_now(),
            "entries": entries,
        }
        atomic_write_bytes(
            backup_dir / "backup.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return backup_dir

    def _restore_backup(self, backup_dir: Path) -> None:
        manifest_path = backup_dir / "backup.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SelfModError(f"自改备份无法读取：{exc}") from exc
        if manifest.get("schema") != _BACKUP_SCHEMA:
            raise SelfModError("自改备份 schema 不匹配")
        for entry in manifest.get("entries", []):
            rel = self.policy.normalise(str(entry.get("path", "")))
            target = self.workspace / rel
            if entry.get("existed"):
                source = backup_dir / "files" / rel
                if not source.is_file():
                    raise SelfModError(f"自改备份缺少文件：{source}")
                expected = entry.get("sha256")
                if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    raise SelfModError(f"自改备份哈希不符：{source}")
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, source.read_bytes())
            else:
                target.unlink(missing_ok=True)
