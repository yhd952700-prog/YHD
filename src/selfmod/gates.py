"""SE-Gate 门禁编排：把 ``docs/spec/SELF-EVOLUTION.md`` §3 的 G0–G14 变成可执行检查。

设计约束（写死，不提供绕过参数）：

* 门禁集合是本模块的常量，调用方不能选择子集，也没有 ``force`` / ``skip`` 开关；
* 门禁命令只能来自 :class:`~src.selfmod.commands.GateCommandPolicy`，不接受任意命令；
* 永久禁区（§2）由本模块独立复核一次，``PathPolicy`` 之外的任何调用路径都拦得住；
* 「工具缺失 / 命令跑不起来」记为 FAIL，不记为 SKIP；
* G0（硬边界）失败后其余门禁记 SKIP 并注明原因——SKIP 只会让证据更少，不会把 FAIL 变成 PASS。

阶段（stage）决定哪些门禁在此时可判定：

``copy``        副本测试阶段：G0–G8、G11 执行；G9/G10/G12/G13 需要真实工作区或合入票据，记 SKIP。
``finalize``    合入前：G9（快照）、G10（证据完整）、G13（双审批）执行。
``post-apply``  应用后：G12 在真实工作区执行。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .commands import GateCommandPolicy
from .evidence import validate_evidence
from .models import FileEdit, ResourceLimits
from .sandbox import run_isolated

SCHEMA = "liuhao.se-gate/1"

GATE_ORDER: Tuple[str, ...] = (
    "G0",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G7",
    "G8",
    "G9",
    "G10",
    "G11",
    "G12",
    "G13",
    "G14",
)

GATE_NAMES: Dict[str, str] = {
    "G0": "硬边界与禁区",
    "G1": "依赖面不变",
    "G2": "全量测试与采集",
    "G3": "静态与运行时契约",
    "G4": "文档门禁",
    "G5": "契约与实例化",
    "G6": "前端构建",
    "G7": "打包 dry-run",
    "G8": "diff 上限",
    "G9": "快照",
    "G10": "证据完整",
    "G11": "输入清单",
    "G12": "应用后验证",
    "G13": "审批记录",
    "G14": "资源约束",
}

#: 每个门禁的墙钟上限。``G2`` 是整套 pytest，300 秒不够；这是资源上限，不是跳过开关。
GATE_TIMEOUTS: Dict[str, float] = {
    "G2": 4 * 3600.0,
    "G3": 30 * 60.0,
    "G12": 30 * 60.0,
}

FRONTEND_PREFIX = "apps/console/console/"

#: §2 永久禁区：命中即 G0 FAIL，且不依赖 PathPolicy 是否被正确构造。
FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "src/security",
    "src/identity",
    "src/gateway/auth.py",
    "src/gateway/policy.py",
    "alembic",
    "migrations",
    "init-scripts",
    "installer",
    "packaging",
    "scripts",
    "dist",
    "tests",
    "src/selfmod",
    "src/upgrade",
    ".selfmod-state",
    ".github",
    "secrets",
    "config",
)

FORBIDDEN_NAMES: Tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "oss-registry.yaml",
    ".env",
    "conftest.py",
)

GOVERNANCE_DOCS: Tuple[str, ...] = (
    "docs/README.md",
    "docs/MASTER-TASK-LEDGER.md",
    "docs/known-gaps.md",
    "docs/spec/SELF-EVOLUTION.md",
    "RELEASE-READINESS-CONTRACT.md",
    "RELEASE-RUNBOOK.md",
)

#: 新增行里出现即 FAIL 的内容（T1 口径）。标签用于中文失败原因。
FORBIDDEN_CONTENT: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("数据库结构变更", re.compile(r"\b(CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+INDEX)\b", re.I)),
    ("私钥或令牌", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{16,}", re.I)),
    ("跳过或削弱测试", re.compile(r"pytest\.mark\.(skip|xfail)|--ignore\b|--deselect\b|@unittest\.skip")),
    ("关闭安全检查", re.compile(r"(verify\s*=\s*False|ssl\._create_unverified_context|InsecureSkipVerify)")),
)

#: T1 档位 diff 上限（spec §4.1）。
T1_MAX_FILES = 12
T1_MAX_LINES = 800
T1_MAX_FILE_LINES = 400

_COPY_MODE_MARKER = "docs/spec/SELF-EVOLUTION.md"


def is_repo_workspace(root: Path) -> bool:
    """是否为本仓库源码树（决定 SE-Gate 是否适用）。"""
    return (Path(root) / _COPY_MODE_MARKER).is_file()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


@dataclass
class CommandResult:
    command: List[str]
    exit_code: Optional[int]
    status: str
    reason: str
    duration_ms: int
    output_tail: str
    output_tail_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": " ".join(self.command),
            "exit_code": self.exit_code,
            "status": self.status,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "output_tail": self.output_tail,
            "output_tail_sha256": self.output_tail_sha256,
        }


@dataclass
class GateResult:
    id: str
    name: str
    status: str  # pass / fail / skip
    reason: str
    duration_ms: int = 0
    commands: List[CommandResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "commands": [c.to_dict() for c in self.commands],
        }


@dataclass
class GateReport:
    stage: str
    gates: List[GateResult]
    started_at: str
    duration_ms: int
    gate_mode: str = "repo"
    result: str = "PASS"

    def gate(self, gate_id: str) -> Optional[GateResult]:
        return next((g for g in self.gates if g.id == gate_id), None)

    def failures(self) -> List[GateResult]:
        return [g for g in self.gates if g.status == "fail"]

    def summary(self) -> Dict[str, int]:
        out = {"pass": 0, "fail": 0, "skip": 0}
        for gate in self.gates:
            out[gate.status] = out.get(gate.status, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "stage": self.stage,
            "gate_mode": self.gate_mode,
            "result": self.result,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "summary": self.summary(),
            "gates": [g.to_dict() for g in self.gates],
        }


# ----------------------------------------------------------------------
# 纯函数：路径 / diff / 内容扫描（可单元测试，不起进程）
# ----------------------------------------------------------------------
def forbidden_reason(path: str) -> Optional[str]:
    """命中 §2 永久禁区时返回中文原因，否则 None。"""
    rel = _norm(path)
    if not rel:
        return "路径为空"
    name = rel.rsplit("/", 1)[-1]
    if name in FORBIDDEN_NAMES or name.startswith(".env."):
        return f"{rel} 是依赖清单/密钥/测试配置，属于冻结资产"
    if rel in GOVERNANCE_DOCS:
        return f"{rel} 是治理台账，不允许自改"
    for prefix in FORBIDDEN_PREFIXES:
        if rel == prefix or rel.startswith(prefix + "/"):
            where = prefix if rel == prefix else prefix + "/"
            return f"{rel} 位于永久禁区 {where}（鉴权/安全/迁移/安装/发布/自改机制）"
    return None


def diff_stat(diff: str) -> Dict[str, Any]:
    """统计 unified diff：文件数、增删行、单文件增删行。"""
    files: Dict[str, Dict[str, int]] = {}
    current: Optional[str] = None
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            files.setdefault(current, {"added": 0, "removed": 0})
            continue
        if line.startswith("--- a/"):
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            files[current]["added"] += 1
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            files[current]["removed"] += 1
            removed += 1
    return {
        "files": len(files),
        "added": added,
        "removed": removed,
        "changed_lines": added + removed,
        "per_file": files,
    }


def scan_added_lines(diff: str) -> List[Tuple[str, int, str]]:
    """返回 (标签, 行号, 原文) 列表；只扫描新增行。"""
    hits: List[Tuple[str, int, str]] = []
    lineno = 0
    for line in diff.splitlines():
        lineno += 1
        if not line.startswith("+") or line.startswith("+++"):
            continue
        payload = line[1:]
        for label, pattern in FORBIDDEN_CONTENT:
            if pattern.search(payload):
                hits.append((label, lineno, payload.strip()[:200]))
    return hits


def validate_snapshot(snapshot_dir: Path, edits: Sequence[FileEdit]) -> List[str]:
    """校验回滚点：manifest 覆盖全部改动文件，且每个文件哈希可复算。"""
    problems: List[str] = []
    manifest_path = Path(snapshot_dir) / "backup.json"
    if not manifest_path.is_file():
        return [f"回滚点缺少 backup.json：{manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"回滚点 manifest 无法解析：{exc}"]
    entries = {str(e.get("path")): e for e in manifest.get("entries", [])}
    for edit in edits:
        rel = _norm(edit.path)
        entry = entries.get(rel)
        if entry is None:
            problems.append(f"回滚点未覆盖 {rel}")
            continue
        if entry.get("existed"):
            payload = Path(snapshot_dir) / "files" / rel
            if not payload.is_file():
                problems.append(f"回滚点缺少原文件 {rel}")
                continue
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            if entry.get("sha256") and digest != entry["sha256"]:
                problems.append(f"回滚点哈希不符 {rel}")
    return problems


def parse_test_counts(tail: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for label in ("passed", "failed", "error", "errors", "skipped"):
        m = re.search(rf"(\d+)\s+{label}\b", tail)
        if m:
            key = "errors" if label == "error" else label
            counts[key] = int(m.group(1))
    return counts


# ----------------------------------------------------------------------
# 命令执行
# ----------------------------------------------------------------------
def _execute(
    command: Sequence[str],
    *,
    cwd: Path,
    limits: ResourceLimits,
    timeout_seconds: float,
) -> CommandResult:
    started = time.monotonic()
    effective = replace(limits, timeout_seconds=max(1.0, float(timeout_seconds)))
    try:
        run = run_isolated(command, cwd=cwd, limits=effective)
    except Exception as exc:  # 跑不起来 = FAIL，不记 SKIP
        return CommandResult(
            command=list(command),
            exit_code=None,
            status="fail",
            reason=f"命令无法执行：{exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
            output_tail="",
            output_tail_sha256=hashlib.sha256(b"").hexdigest(),
        )
    elapsed = int((time.monotonic() - started) * 1000)
    tail = run.output_tail or ""
    digest = hashlib.sha256(tail.encode("utf-8", "replace")).hexdigest()
    if run.timed_out:
        return CommandResult(list(command), run.returncode, "fail", f"命令超时（>{int(timeout_seconds)}秒）", elapsed, tail, digest)
    if run.limit_reason == "memory":
        return CommandResult(list(command), run.returncode, "fail", "命令超出内存上限", elapsed, tail, digest)
    if run.limit_reason == "cpu":
        return CommandResult(list(command), run.returncode, "fail", "命令超出 CPU 上限", elapsed, tail, digest)
    if run.returncode != 0:
        return CommandResult(list(command), run.returncode, "fail", f"退出码 {run.returncode}", elapsed, tail, digest)
    return CommandResult(list(command), 0, "pass", "退出码 0", elapsed, tail, digest)


def _gate_from_commands(gate_id: str, commands: Sequence[CommandResult]) -> GateResult:
    failed = [c for c in commands if c.status != "pass"]
    if failed:
        detail = "；".join(
            f"{Path(c.command[-1] if len(c.command) > 1 else c.command[0]).name} {c.reason}" for c in failed
        )
        return GateResult(
            id=gate_id,
            name=GATE_NAMES[gate_id],
            status="fail",
            reason=f"门禁 {gate_id} 未通过：{detail}",
            duration_ms=sum(c.duration_ms for c in commands),
            commands=list(commands),
        )
    return GateResult(
        id=gate_id,
        name=GATE_NAMES[gate_id],
        status="pass",
        reason=f"门禁 {gate_id} 全部命令退出 0",
        duration_ms=sum(c.duration_ms for c in commands),
        commands=list(commands),
    )


def _skip(gate_id: str, reason: str) -> GateResult:
    return GateResult(id=gate_id, name=GATE_NAMES[gate_id], status="skip", reason=reason)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def run_gates(
    workspace_root: Path,
    *,
    edits: Sequence[FileEdit],
    diff: str,
    policy: Any,
    limits: ResourceLimits,
    stage: str = "copy",
    snapshot: Optional[Path] = None,
    evidence: Optional[Dict[str, Any]] = None,
    approvals: Optional[Dict[str, Any]] = None,
    input_manifest: Optional[Dict[str, Any]] = None,
) -> GateReport:
    """按 G0–G14 顺序执行门禁。没有任何参数可以跳过或强制通过。"""
    started_at = _utc_now()
    started = time.monotonic()
    root = Path(workspace_root).resolve()
    repo_mode = is_repo_workspace(root)
    changed = [_norm(e.path) for e in edits]
    stat = diff_stat(diff)

    def run_cmd(gate_id: str, command: Sequence[str]) -> CommandResult:
        timeout = GATE_TIMEOUTS.get(gate_id, limits.timeout_seconds)
        return _execute(command, cwd=root, limits=limits, timeout_seconds=timeout)

    gates: List[GateResult] = []
    hard_block = False

    if not repo_mode:
        for gate_id in GATE_ORDER:
            gates.append(
                _skip(
                    gate_id,
                    "工作区缺少 docs/spec/SELF-EVOLUTION.md：SE-Gate 只对鎏灏源码树生效；"
                    "演练工作区的全部门禁记为 skip（证据件保留该事实）。",
                )
            )
        return GateReport(
            stage=stage,
            gates=gates,
            started_at=started_at,
            duration_ms=int((time.monotonic() - started) * 1000),
            gate_mode="not-applicable",
            result="PASS",
        )

    for gate_id in GATE_ORDER:
        if hard_block:
            gates.append(_skip(gate_id, "G0 硬拦截：提案已判定不可执行，本门未运行"))
            continue

        if stage == "copy":
            gate = _run_copy_stage(
                gate_id,
                root=root,
                edits=edits,
                diff=diff,
                changed=changed,
                stat=stat,
                policy=policy,
                limits=limits,
                run_cmd=run_cmd,
                input_manifest=input_manifest,
            )
        elif stage == "finalize":
            gate = _run_finalize_stage(
                gate_id,
                root=root,
                edits=edits,
                snapshot=snapshot,
                evidence=evidence,
                approvals=approvals,
            )
        elif stage == "post-apply":
            gate = _run_post_apply_stage(gate_id, run_cmd=run_cmd)
        else:
            raise ValueError(f"未知门禁阶段：{stage}")

        gates.append(gate)
        if gate_id == "G0" and gate.status == "fail":
            hard_block = True

    report = GateReport(
        stage=stage,
        gates=gates,
        started_at=started_at,
        duration_ms=int((time.monotonic() - started) * 1000),
        gate_mode="repo",
    )
    report.result = "FAIL" if report.failures() else "PASS"
    return report


def _run_copy_stage(
    gate_id: str,
    *,
    root: Path,
    edits: Sequence[FileEdit],
    diff: str,
    changed: Sequence[str],
    stat: Dict[str, Any],
    policy: Any,
    limits: ResourceLimits,
    run_cmd: Any,
    input_manifest: Optional[Dict[str, Any]],
) -> GateResult:
    if gate_id == "G0":
        reasons: List[str] = []
        for path in changed:
            why = forbidden_reason(path)
            if why:
                reasons.append(why)
            try:
                policy.normalise(path)
            except Exception as exc:
                reasons.append(f"路径策略拒绝 {path}：{exc}")
        if any(e.content is None and _norm(e.path).startswith("tests/") for e in edits):
            reasons.append("T1 不允许删除测试文件")
        for label, lineno, text in scan_added_lines(diff):
            reasons.append(f"diff 第 {lineno} 行命中「{label}」：{text}")
        if reasons:
            return GateResult("G0", GATE_NAMES["G0"], "fail", "；".join(dict.fromkeys(reasons)))
        return GateResult("G0", GATE_NAMES["G0"], "pass", "改动路径与新增内容均未命中永久禁区")

    if gate_id == "G1":
        manifests = [p for p in changed if forbidden_reason(p)]
        if manifests:
            return GateResult(
                "G1",
                GATE_NAMES["G1"],
                "fail",
                "依赖清单 / 冻结资产出现在改动里：" + "、".join(manifests),
            )
        return _gate_from_commands("G1", [run_cmd("G1", c) for c in _DEFAULT_G1_A])

    if gate_id == "G2":
        cmds = [run_cmd("G2", _DEFAULT_G2_A[0]), run_cmd("G2", _DEFAULT_G2_A[1])]
        gate = _gate_from_commands("G2", cmds)
        if gate.status == "pass":
            counts = parse_test_counts(cmds[0].output_tail)
            if counts.get("failed") or counts.get("errors"):
                gate.status = "fail"
                gate.reason = "测试进程退出 0 但输出报告失败/错误：%s" % counts
            elif counts.get("skipped", 0) > 0:
                gate.reason += f"（跳过 {counts['skipped']} 例，已如实记录）"
        return gate

    if gate_id == "G3":
        cmds = [run_cmd("G3", c) for c in _DEFAULT_G3_A]
        return _gate_from_commands("G3", cmds)

    if gate_id == "G4":
        cmds = [run_cmd("G4", c) for c in _DEFAULT_G4_A]
        return _gate_from_commands("G4", cmds)

    if gate_id == "G5":
        cmds = [run_cmd("G5", c) for c in _DEFAULT_G5_A]
        return _gate_from_commands("G5", cmds)

    if gate_id == "G6":
        frontend = [p for p in changed if _norm(p).startswith(FRONTEND_PREFIX)]
        if not frontend:
            return _skip("G6", "本提案未改动 apps/console/console/，无需构建")
        return GateResult(
            "G6",
            GATE_NAMES["G6"],
            "fail",
            "改动了前端但 `npm` 类命令尚未纳入 GateCommandPolicy 白名单，G6 无法执行；"
            "在补齐白名单与构建产物同源校验前，前端改动一律不可自动合入",
        )

    if gate_id == "G7":
        packaging = [
            p
            for p in changed
            if _norm(p).startswith(FRONTEND_PREFIX) or _norm(p).startswith(("packaging/", "installer/"))
        ]
        if not packaging:
            return _skip("G7", "本提案未改动前端/打包面，无需 dry-run")
        return GateResult(
            "G7",
            GATE_NAMES["G7"],
            "fail",
            "改动了前端/打包面，但 PowerShell dry-run 尚未纳入 GateCommandPolicy 白名单，G7 无法执行",
        )

    if gate_id == "G8":
        reasons: List[str] = []
        if stat["files"] > T1_MAX_FILES:
            reasons.append(f"改动 {stat['files']} 个文件，超过 T1 上限 {T1_MAX_FILES}")
        if stat["changed_lines"] > T1_MAX_LINES:
            reasons.append(f"改动 {stat['changed_lines']} 行，超过 T1 上限 {T1_MAX_LINES}")
        for path, counts in stat["per_file"].items():
            total = counts["added"] + counts["removed"]
            if total > T1_MAX_FILE_LINES:
                reasons.append(f"{path} 改动 {total} 行，超过单文件上限 {T1_MAX_FILE_LINES}")
        if any(e.content is None and _norm(e.path).startswith("tests/") for e in edits):
            reasons.append("禁止删除测试文件")
        if reasons:
            return GateResult("G8", GATE_NAMES["G8"], "fail", "；".join(reasons))
        return GateResult(
            "G8",
            GATE_NAMES["G8"],
            "pass",
            f"diff 在 T1 上限内：{stat['files']} 文件 / {stat['changed_lines']} 行",
        )

    if gate_id == "G9":
        return _skip("G9", "副本阶段尚未生成回滚点；finalize 前必须通过本门")

    if gate_id == "G10":
        return _skip("G10", "副本阶段证据件尚未封存；finalize 前用 validate_evidence 校验")

    if gate_id == "G11":
        reasons: List[str] = []
        if input_manifest is None:
            return _skip(
                "G11",
                "本提案由人工直接给出 edits，没有模型输入清单（input_manifest）；"
                "生成端接入后本门才有判定对象，当前如实记 skip",
            )
        allowed_keys = {"prompt_template", "fingerprint", "sources", "model", "tier"}
        unknown = set(input_manifest) - allowed_keys
        if unknown:
            reasons.append(f"输入清单含未定义字段：{sorted(unknown)}")
        if not input_manifest.get("fingerprint"):
            reasons.append("输入清单缺少 fingerprint（同输入不得重复尝试）")
        if reasons:
            return GateResult("G11", GATE_NAMES["G11"], "fail", "；".join(reasons))
        return GateResult("G11", GATE_NAMES["G11"], "pass", "输入清单结构完整")

    if gate_id in ("G12", "G13"):
        return _skip(gate_id, "本门在应用后 / 合入阶段判定，副本阶段记 skip")

    if gate_id == "G14":
        return _skip("G14", "资源约束门仅 T3 适用；本提案档位为 T1")

    raise AssertionError(gate_id)


def _run_finalize_stage(
    gate_id: str,
    *,
    root: Path,
    edits: Sequence[FileEdit],
    snapshot: Optional[Path],
    evidence: Optional[Dict[str, Any]],
    approvals: Optional[Dict[str, Any]],
) -> GateResult:
    if gate_id == "G9":
        if snapshot is None:
            return GateResult("G9", GATE_NAMES["G9"], "fail", "合入前必须先生成回滚点，但未提供快照目录")
        problems = validate_snapshot(snapshot, edits)
        if problems:
            return GateResult("G9", GATE_NAMES["G9"], "fail", "；".join(problems))
        return GateResult("G9", GATE_NAMES["G9"], "pass", "回滚点覆盖全部改动文件且哈希一致")

    if gate_id == "G10":
        if evidence is None:
            return GateResult("G10", GATE_NAMES["G10"], "fail", "合入前必须存在证据件，但未提供")
        check = validate_evidence(evidence)
        if not check["ok"]:
            return GateResult("G10", GATE_NAMES["G10"], "fail", "证据件不合格：" + "；".join(check["problems"]))
        return GateResult("G10", GATE_NAMES["G10"], "pass", f"证据件校验通过（{check['checked_fields']} 个必填字段、哈希一致）")

    if gate_id == "G13":
        record = approvals or {}
        missing = [stage for stage in ("test", "promote") if not (record.get(stage) or {}).get("approver")]
        if missing:
            return GateResult("G13", GATE_NAMES["G13"], "fail", "缺少审批记录：" + "、".join(missing))
        return GateResult("G13", GATE_NAMES["G13"], "pass", "test / promote 两次人工审批记录齐全")

    return _skip(gate_id, "本门在副本阶段或应用后判定，合入前阶段记 skip")


def _run_post_apply_stage(gate_id: str, *, run_cmd: Any) -> GateResult:
    if gate_id == "G12":
        cmds = [run_cmd("G12", c) for c in _DEFAULT_G12_A]
        return _gate_from_commands("G12", cmds)
    return _skip(gate_id, "本门在副本或合入前阶段判定，应用后阶段记 skip")


# 固定命令常量：与 ``DEFAULT_GATE_COMMANDS`` 一一对应，集中在此便于审计。
_DEFAULT_G1_A = (
    (sys.executable, "scripts/verify_dependency_declaration_surface.py"),
    (sys.executable, "scripts/release/freeze_checks.py", "deps"),
)
_DEFAULT_G2_A = (
    (sys.executable, "-m", "pytest"),
    (sys.executable, "scripts/verify_test_collection.py"),
)
_DEFAULT_G3_A = (
    (sys.executable, "-m", "pytest", "tests/test_import_integrity.py", "-q"),
    (sys.executable, "-m", "compileall", "-q", "src"),
    (sys.executable, "scripts/verify_orm_vs_db.py"),
)
_DEFAULT_G4_A = (
    (sys.executable, "scripts/verify_doc_links.py"),
    (sys.executable, "scripts/verify_docs_index_coverage.py"),
    (sys.executable, "scripts/check_docs_commands.py"),
    (sys.executable, "scripts/verify_doc_count_claims.py"),
    (sys.executable, "scripts/lint_audit_claims.py", "--all"),
    (sys.executable, "scripts/lint_control_claims.py", "--all"),
)
_DEFAULT_G5_A = (
    (sys.executable, "scripts/verify_openapi_contract.py"),
    (sys.executable, "scripts/verify_app_instantiation_hygiene.py"),
)
_DEFAULT_G12_A = (
    (sys.executable, "-m", "pytest", "tests/test_import_integrity.py", "-q"),
    (sys.executable, "-m", "compileall", "-q", "src"),
)

# 导入期硬断言：门禁实际执行的每一条命令都必须在 GateCommandPolicy 白名单里。
# 任何新增门禁命令若忘记登记，模块导入即失败——不允许"先跑起来再说"。
for _command in (
    _DEFAULT_G1_A + _DEFAULT_G2_A + _DEFAULT_G3_A + _DEFAULT_G4_A + _DEFAULT_G5_A + _DEFAULT_G12_A
):
    GateCommandPolicy().validate(_command)
