#!/usr/bin/env python
"""冻结前置清单自动化：一条命令回答「这个包到底能不能打、还差哪几条」。

用法::

    python scripts/release/preflight.py                 # 跑全部门禁
    python scripts/release/preflight.py --checklist     # 只打「本轮修复是否在源码树里」核对表
    python scripts/release/preflight.py --json           # 机器可读（给 CI 用）

门禁全部是**实测**，不是读注释：``git status`` 真读工作树、``import`` 真起子进程、
前端指纹真对字节取 SHA256。任一必检项不满足即退出码 1，并逐条打印证据与
「还差什么」。

覆盖范围（与冻结打包的踩坑一一对应）::

    工作树干净 / HEAD 已推送
    import src.gateway.main
    verify_test_collection / verify_openapi_contract / verify_app_instantiation_hygiene
    tests/test_import_integrity.py（全量导入）
    installed_copy_drift.py（安装副本同源，需要 --install-dir）
    a11y_console_acceptance.py（无障碍，需要 --a11y + playwright）
    品牌字面门禁（packaging/brand_guard.py）
    SBOM/许可证审计（verify_oss_licenses.py；payload 存在时再跑 sbom_audit.py）
    SBOM 不含构建机绝对路径（盘符 / UNC / 类 Unix 家目录）
    日志泄漏扫描（freeze_checks.py log-leak）
    前端构建产物模块数与指纹
    dist\\installer 是否只有一套候选

约束：本脚本只读检查，不写 dist\\，不编译、不运行 setup.exe。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import locale
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

#: 会进入安装包 / 决定安装包内容的路径前缀。工作树里这些路径有未提交改动，
#: 打出来的包就与提交号对不上，必须判 FAIL；其余（.tmp、截图、缓存）只是脏，
#: 不影响包内容，单独归类但同样要求清理。
PACKAGE_PATHS: Tuple[str, ...] = (
    "src/", "apps/", "packaging/", "installer/", "scripts/", "config/", "configs/",
    "docs/", "tests/", "alembic/", "migrations/", "core/", "libs/", "sdk/",
    "init-scripts/", "deploy/", "infra/",
)
PACKAGE_FILES: Tuple[str, ...] = (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "openapi.yaml",
    "package.json", "package-lock.json", "README.md", "LICENSE", "LICENSE.txt",
    "RELEASE-RUNBOOK.md", "AGENTS.md", "CLAUDE.md", "Makefile",
)

STATUS_LABELS = {
    "M ": "已修改(暂存)", " M": "已修改", "MM": "已修改(暂存+工作区)",
    "A ": "新增(暂存)", "AM": "新增(暂存+工作区)", " D": "已删除", "D ": "已删除(暂存)",
    "R ": "重命名", "RM": "重命名(暂存+工作区)", "C ": "复制", "UU": "冲突",
    "??": "未跟踪",
}


@dataclass
class Result:
    name: str
    status: str            # PASS / FAIL / SKIP
    evidence: str = ""
    details: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "SKIP")


# ------------------------------------------------------------------ 进程工具
def _decode(raw: bytes) -> str:
    """子进程输出按 UTF-8 解，失败再按 GBK（Windows 管道默认代码页）。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _run(
    cmd: Sequence[str],
    cwd: Path,
    timeout: int = 900,
    env: Optional[dict] = None,
) -> Tuple[int, str]:
    merged = os.environ.copy()
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        merged.update(env)
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd), capture_output=True, timeout=timeout, env=merged,
        )
    except FileNotFoundError as exc:
        return 127, f"找不到可执行文件：{exc}"
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）：{' '.join(cmd)}"
    return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)


def _tail(output: str, lines: int = 3) -> str:
    rows = [row.strip() for row in output.strip().splitlines() if row.strip()]
    return " | ".join(rows[-lines:]) if rows else "（无输出）"


_VERDICT_PREFIXES = ("RESULT:", "FAIL:", "PASS:", "== PASS", "== FAIL", "[GREEN]", "[RED]", "OK:")


def _verdict(output: str) -> str:
    """从混合了应用日志/JSON 的输出里挑出真正的结论行。"""
    rows = [row.strip() for row in output.strip().splitlines() if row.strip()]
    for row in reversed(rows):
        if row.startswith(_VERDICT_PREFIXES):
            return row
    for row in reversed(rows):
        if not row.startswith("{"):
            return row
    return "（无输出）"


def _fingerprint_tree(root: Path) -> Tuple[int, str]:
    """对目录下所有文件（相对路径 + 内容）取整体 SHA256 指纹。"""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "little"))
        digest.update(rel)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return count, digest.hexdigest()


def _git(repo: Path, *args: str) -> Tuple[int, str]:
    return _run(["git", "-C", str(repo), *args], cwd=repo)


# ---------------------------------------------------------------------- 门禁
def gate_worktree(repo: Path) -> Result:
    code, out = _git(repo, "status", "--porcelain=v1")
    if code != 0:
        return Result("工作树干净", "FAIL", "git status 执行失败（不是 git 仓库？）", [out.strip()])
    rows = [line for line in out.splitlines() if line.strip()]
    if not rows:
        return Result("工作树干净", "PASS", "0 项未提交")

    packaged: List[str] = []
    others: List[str] = []
    details: List[str] = []
    for line in rows:
        code2 = line[:2]
        path = line[3:].strip() if len(line) >= 4 else line.strip()
        label = STATUS_LABELS.get(code2, "变更")
        is_pkg = path.startswith(PACKAGE_PATHS) or path in PACKAGE_FILES
        (packaged if is_pkg else others).append(path)
        details.append(f"[{label}] {path}" + ("" if is_pkg else "  （不随包）"))
    evidence = (
        f"{len(rows)} 项未提交：随包 {len(packaged)} 项 / 不随包 {len(others)} 项；"
        "先提交或还原下列文件再出包"
    )
    return Result("工作树干净", "FAIL", evidence, details)


def gate_head_pushed(repo: Path) -> Result:
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
    ucode, upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if ucode != 0 or not upstream.strip():
        return Result("HEAD 已推送", "FAIL",
                      f"{branch} 没有上游分支（detached HEAD 或未设置 upstream）")
    upstream = upstream.strip()
    ahead = int((_git(repo, "rev-list", "--count", "@{u}..HEAD")[1] or "0").strip() or 0)
    behind = int((_git(repo, "rev-list", "--count", "HEAD..@{u}")[1] or "0").strip() or 0)
    if ahead > 0:
        _, log = _git(repo, "log", "--oneline", "@{u}..HEAD")
        commits = [row.strip() for row in log.splitlines() if row.strip()]
        return Result("HEAD 已推送", "FAIL",
                      f"{branch} 领先 {upstream} {ahead} 个提交", commits)
    if behind > 0:
        return Result("HEAD 已推送", "FAIL", f"{branch} 落后 {upstream} {behind} 个提交")
    short = _git(repo, "rev-parse", "--short", "HEAD")[1].strip()
    return Result("HEAD 已推送", "PASS", f"{branch} 与 {upstream} 同步（{short}）")


def gate_import_gateway(repo: Path, python: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="liuhao-preflight-") as tmp:
        env = {
            "AUDIT_DB_PATH": str(Path(tmp) / "audit_store.db"),
            "LIUHAO_DATA_DIR": tmp,
            "LIUHAO_LOG_DIR": str(Path(tmp) / "logs"),
            "LIUHAO_ENV": "preflight",
        }
        started = time.time()
        code, out = _run([python, "-c", "import src.gateway.main"], repo, timeout=300, env=env)
        elapsed = int((time.time() - started) * 1000)
    if code == 0:
        return Result("import src.gateway.main", "PASS", f"导入成功（{elapsed}ms）")
    return Result("import src.gateway.main", "FAIL", f"导入失败（exit {code}）", [out.strip()])


def gate_script(name: str, script: str, repo: Path, python: str, timeout: int = 900) -> Result:
    path = repo / script
    if not path.is_file():
        return Result(name, "FAIL", f"找不到 {script}")
    started = time.time()
    code, out = _run([python, "-u", str(path)], repo, timeout=timeout)
    elapsed = int((time.time() - started) * 1000)
    if code == 0:
        return Result(name, "PASS", f"{_verdict(out)}（{elapsed}ms）")
    detail = [row.strip() for row in out.splitlines()
              if row.strip().startswith(("+ ", "- ", "  + ", "FAIL", "RESULT:", "MISSING", "GHOST"))]
    return Result(name, "FAIL", f"exit {code}：{_verdict(out)}", ["\n".join(detail)] if detail else [])


def gate_import_integrity(repo: Path, python: str) -> Result:
    """全量导入：src/ 下每个业务模块真 import + 跨模块符号真实存在。"""
    path = repo / "tests" / "test_import_integrity.py"
    if not path.is_file():
        return Result("全量导入", "FAIL", "缺少 tests/test_import_integrity.py")
    started = time.time()
    code, out = _run(
        [python, "-m", "pytest", str(path), "-q", "-p", "no:cacheprovider"],
        repo, timeout=1800,
    )
    elapsed = int((time.time() - started) * 1000)
    if code == 0:
        return Result("全量导入", "PASS", f"{_tail(out, 1)}（{elapsed}ms）")
    return Result("全量导入", "FAIL", f"exit {code}：{_tail(out, 1)}", [out.strip()])


def gate_installed_copy(repo: Path, python: str, install_dir: str) -> Result:
    """安装副本同源：没有安装目录就没法比，明确标注待挂而不是假装通过。"""
    script = repo / "scripts" / "acceptance" / "installed_copy_drift.py"
    if not script.is_file():
        return Result("安装副本同源", "SKIP", "待挂：缺少 scripts/acceptance/installed_copy_drift.py")
    if not install_dir:
        return Result("安装副本同源", "SKIP",
                      "待挂：未提供 --install-dir（当前机器未安装，出包后统一跑）")
    target = Path(install_dir)
    if not target.is_dir():
        return Result("安装副本同源", "SKIP",
                      f"待挂：安装目录不存在（{target}），出包并安装后再跑")
    code, out = _run(
        [python, "-u", str(script), "--install-dir", str(target), "--repo", str(repo)],
        repo, timeout=600,
    )
    if code == 0:
        return Result("安装副本同源", "PASS", _verdict(out))
    return Result("安装副本同源", "FAIL", f"exit {code}：{_verdict(out)}", [out.strip()])


def gate_a11y(repo: Path, python: str, enabled: bool) -> Result:
    """无障碍验收：脚本已落地，但依赖 playwright + 控制台；默认不跑，避免假红/假绿。"""
    script = repo / "scripts" / "acceptance" / "a11y_console_acceptance.py"
    if not script.is_file():
        return Result("无障碍验收", "SKIP", "待挂：缺少 scripts/acceptance/a11y_console_acceptance.py")
    if not enabled:
        return Result("无障碍验收", "SKIP",
                      "待挂：未启用 --a11y（脚本在，需 playwright + 可启动的控制台）")
    code, out = _run([python, "-u", str(script)], repo, timeout=900)
    if code == 0:
        return Result("无障碍验收", "PASS", _verdict(out))
    if code == 2:
        return Result("无障碍验收", "SKIP", f"环境不具备：{_verdict(out)}")
    return Result("无障碍验收", "FAIL", f"exit {code}：{_verdict(out)}", [out.strip()])


def gate_ps51_parse(repo: Path) -> Result:
    """用 Windows PowerShell 5.1 的 Parser 逐个解析 .ps1。

    UTF-8 无 BOM 的脚本在 5.1 下会按 ANSI 解码，中文与引号被读坏，只有 PS7 能过；
    这类问题不该靠人肉发现，出包前必须全部可解析。
    """
    script = r"""
$files = Get-ChildItem -LiteralPath '__REPO__' -Recurse -Filter *.ps1 -File |
    Where-Object { $_.FullName -notmatch '\\node_modules\\|\\\.venv\\|\\dist' }
$bad = 0
foreach ($f in $files) {
    $e = $null
    $t = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$t, [ref]$e)
    if ($e -and $e.Count -gt 0) {
        $bad = $bad + 1
        foreach ($err in $e) {
            Write-Output ('BAD|' + $f.FullName + '|' + $err.Extent.StartLineNumber + '|' + $err.Message)
        }
    }
}
Write-Output ('COUNT|' + $files.Count + '|PS' + $PSVersionTable.PSVersion.ToString())
if ($bad -eq 0) { exit 0 } else { exit 1 }
""".replace("__REPO__", str(repo))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    code, out = _run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        repo, timeout=300,
    )
    rows = [row.strip() for row in out.splitlines() if row.strip()]
    count_line = next((row for row in rows if row.startswith("COUNT|")), "")
    bad = [row for row in rows if row.startswith("BAD|")]
    if code == 0 and count_line:
        _, total, version = count_line.split("|", 2)
        return Result("PowerShell 5.1 解析", "PASS", f"{total} 个 .ps1 全部可解析（{version}）")
    detail = [f"{Path(row.split('|')[1]).name}:{row.split('|')[2]} {row.split('|')[3]}"
              for row in bad if row.count("|") >= 3]
    return Result("PowerShell 5.1 解析", "FAIL",
                  f"{len(bad)} 处解析错误" if bad else f"powershell.exe 调用失败（exit {code}）",
                  detail[:20] or [out.strip()])


def gate_brand(repo: Path, python: str) -> Result:
    path = repo / "packaging" / "brand_guard.py"
    if not path.is_file():
        return Result("品牌门禁", "FAIL", "缺少 packaging/brand_guard.py")
    code, out = _run([python, "-u", str(path)], repo, timeout=300)
    if code == 0:
        return Result("品牌门禁", "PASS", _tail(out, 1))
    return Result("品牌门禁", "FAIL", _tail(out, 1), [out.strip()])


def gate_sbom(repo: Path, python: str) -> List[Result]:
    results: List[Result] = []
    lic = repo / "scripts" / "verify_oss_licenses.py"
    if lic.is_file():
        results.append(gate_script("SBOM/许可证登记", "scripts/verify_oss_licenses.py", repo, python))
    else:
        results.append(Result("SBOM/许可证登记", "FAIL", "缺少 scripts/verify_oss_licenses.py"))

    audit = next((p for p in (
        repo / "dist" / "release-gate" / "sbom_audit.py",
        repo / "packaging" / "sbom_audit.py",
    ) if p.is_file()), None)
    payload = repo / "dist" / "payload" / "app"
    if audit is None:
        results.append(Result("SBOM（本次 payload）", "SKIP", "找不到 sbom_audit.py（随构建门禁产出）"))
    elif not payload.is_dir():
        results.append(Result("SBOM（本次 payload）", "SKIP",
                              "payload 未组装；stage_payload 会在组装时强制生成"))
    else:
        with tempfile.TemporaryDirectory(prefix="liuhao-sbom-") as tmp:
            code, out = _run(
                [python, "-u", str(audit), "--app", str(payload),
                 "--out", tmp, "--report", str(Path(tmp) / "report.md")],
                repo, timeout=600,
            )
            sbom_path = Path(tmp) / "sbom-cyclonedx.json"
            components = 0
            if code == 0 and sbom_path.is_file():
                try:
                    components = len(json.loads(sbom_path.read_text(encoding="utf-8"))
                                     .get("components") or [])
                except (OSError, ValueError):
                    components = 0
        if code == 0:
            evidence = f"SBOM 生成成功（组件 {components} 个）/ 报告与清单写入临时目录"
        else:
            evidence = f"exit {code}：{_verdict(out)}"
        results.append(Result(
            "SBOM（本次 payload）", "PASS" if code == 0 else "FAIL",
            evidence, [] if code == 0 else [out.strip()],
        ))
    return results


#: 允许出现 SBOM 的固定位置（安装副本 / 发布闸 / 升级包合规材料）。
SBOM_CANDIDATES: Tuple[str, ...] = (
    "sbom-cyclonedx.json",
    "dist/release-gate/sbom-cyclonedx.json",
    "docs/compliance/sbom-cyclonedx.json",
    "dist/payload/app/sbom-cyclonedx.json",
)

#: SBOM 里不允许出现的构建机路径特征。SBOM 会随安装包/升级包分发，
#: ``liuhao:audit-source-tree`` 之类的字段一旦带上盘符，就把打包机目录结构泄露出去了。
#: 只看**解析后的字符串值**，不在原始 JSON 文本上匹配 —— 否则 ``https://`` 里的
#: ``s:/`` 和转义反斜杠 ``\\`` 会造成大量误报。
SBOM_ABS_PATH_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("Windows 盘符", r"(?<![A-Za-z0-9+.\-])[A-Za-z]:[\\/]"),
    ("UNC 路径", r"(?<![\w.\-])\\\\[^\\/\s]+\\"),
    ("Unix 家目录", r"(?<![\w.\-])/(?:home|Users|root)/"),
)


def _iter_json_strings(node) -> Iterable[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_json_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_strings(item)
    elif isinstance(node, str):
        yield node


def _audit_source_tree_values(node) -> Iterable[str]:
    """取所有 ``liuhao:audit-source-tree`` 属性的值（应为 ``.``）。"""
    if isinstance(node, dict):
        if node.get("name") == "liuhao:audit-source-tree" and isinstance(node.get("value"), str):
            yield node["value"]
        for value in node.values():
            yield from _audit_source_tree_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _audit_source_tree_values(item)


def gate_sbom_paths(repo: Path) -> Result:
    """SBOM 里不得出现绝对路径 / 盘符（构建机信息泄露）。

    只读扫描已存在的 SBOM 文件；没有 SBOM 时记 SKIP（由 ``stage_payload`` 生成）。
    命中即 FAIL：这个文件会进安装目录，也会进 ``docs/compliance`` 的升级包。
    """
    targets = [(rel, repo / rel) for rel in SBOM_CANDIDATES if (repo / rel).is_file()]
    if not targets:
        return Result("SBOM 绝对路径", "SKIP", "未找到已生成的 SBOM 文件（由构建线产出）")

    hits: List[str] = []
    for rel, path in targets:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            hits.append(f"[{rel}] 无法读取：{exc}")
            continue
        except ValueError as exc:
            hits.append(f"[{rel}] 不是合法 JSON：{exc}")
            continue
        for value in _audit_source_tree_values(payload):
            if value.strip() not in (".", ""):
                hits.append(f"[{rel}] liuhao:audit-source-tree 未脱敏：{value[:80]}")
        for value in _iter_json_strings(payload):
            for label, pattern in SBOM_ABS_PATH_PATTERNS:
                match = re.search(pattern, value)
                if match:
                    hits.append(f"[{rel}] {label}：{value[:80]}")

    scanned = "、".join(rel for rel, _ in targets)
    if hits:
        shown = hits[:20]
        more = f"（共 {len(hits)} 处，仅列前 20）" if len(hits) > len(shown) else ""
        return Result(
            "SBOM 绝对路径", "FAIL",
            f"{len(hits)} 处构建机路径{more}（{scanned}）；SBOM 会随包分发，必须先脱敏",
            shown,
        )
    return Result("SBOM 绝对路径", "PASS", f"已扫描 {len(targets)} 个 SBOM（{scanned}），无盘符/绝对路径")


def gate_log_leak(repo: Path, python: str) -> Result:
    script = repo / "scripts" / "release" / "freeze_checks.py"
    if not script.is_file():
        return Result("日志泄漏扫描", "FAIL", "缺少 scripts/release/freeze_checks.py")
    code, out = _run(
        [python, "-u", str(script), "log-leak", "--repo", str(repo)], repo, timeout=900,
    )
    if code == 0:
        return Result("日志泄漏扫描", "PASS", _tail(out, 1))
    return Result("日志泄漏扫描", "FAIL", _tail(out, 1), [out.strip()])


def gate_frontend(repo: Path) -> Result:
    dist = repo / "apps" / "console" / "console" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return Result("前端构建产物", "FAIL", "缺少 apps/console/console/dist/index.html（未构建）")
    js_chunks = sorted((dist / "assets").glob("*.js")) if (dist / "assets").is_dir() else []
    if not js_chunks:
        return Result("前端构建产物", "FAIL", "dist/assets 下没有 .js 分块")
    count, fingerprint = _fingerprint_tree(dist)
    hashed = [p.name for p in js_chunks if re.search(r"-[0-9a-zA-Z_]{8,}\.js$", p.name)]
    evidence = (
        f"模块 {len(js_chunks)} 个（带哈希 {len(hashed)} 个）/ dist 文件 {count} 个 / "
        f"指纹 {fingerprint[:16]}…"
    )
    return Result("前端构建产物", "PASS", evidence)


def gate_installer_candidates(repo: Path) -> Result:
    out_dir = repo / "dist" / "installer"
    if not out_dir.exists():
        return Result("dist\\installer 候选", "PASS", "目录不存在（尚未出包）")
    # 只把安装器根目录的 exe 当作「本次候选」；_archive 下是历史归档，不参与候选判定。
    exes = sorted(p for p in out_dir.glob("*.exe") if p.is_file())
    archived = sorted(p for p in (out_dir / "_archive").rglob("*.exe") if p.is_file()) \
        if (out_dir / "_archive").is_dir() else []
    archive_note = f"；_archive 历史归档 {len(archived)} 个（不计入候选）" if archived else ""
    if not exes:
        return Result("dist\\installer 候选", "PASS", f"0 个候选安装包{archive_note}")
    names = " | ".join(p.name for p in exes)
    if len(exes) > 1:
        return Result("dist\\installer 候选", "FAIL",
                      f"{len(exes)} 个候选安装包，无法确定哪套是本次成品{archive_note}",
                      [names])
    return Result("dist\\installer 候选", "PASS", f"1 个候选：{names}{archive_note}")


# ------------------------------------------------------------ 修复核对表
# (类别, 条目, 证据文件, 必需片段 / (片段, 最少次数), 说明)
ChecklistRow = Tuple[str, str, str, Optional[object], str]

CHECKLIST: Tuple[ChecklistRow, ...] = (
    ("桌面壳", "DPI 自适应", "src/desktop/dpi.py", None, "高 DPI 下界面不糊"),
    ("桌面壳", "首启图形向导", "scripts/first_run_gui.py", None, "非黑窗口的首启向导"),
    ("桌面壳", "托盘入口（iss 三处图标）", "installer/liuhao.iss", ("pythonw.exe", 3),
     "开始菜单/桌面/运行三处都指向 pythonw 托盘壳"),
    ("桌面壳", "端口被占二选一", "scripts/first_run_gui.py", ("换一个端口",),
     "首启向导给「重试 / 换一个端口」；日常启动仍只报 last_error（无二选一）"),
    ("网关", "回环同端口放行", "src/gateway/main.py", ("LIUHAO_LISTEN_PORT",),
     "启动器写入端口，网关按该端口监听"),
    ("网关", "回环地址一律放行", "src/gateway/models.py", ("allow_loopback",),
     "本地模型服务在回环任意端口可用"),
    ("网关", "serialized_turn 串行回合", "src/ai/liuhao.py", ("serialized_turn",),
     "同一助手并发回合串行化"),
    ("网关", "限流分桶", "src/gateway/rate_limiter.py", ("生成类路径单独成桶",),
     "控制面与聊天不共用 principal 桶"),
    ("网关", "数据可携带", "src/gateway/data_portability.py", ("export_knowledge",),
     "知识库/记忆/目标/凭据导出"),
    ("网关", "启动数据版本守卫", "src/upgrade/startup_guard.py", ("DATA_SCHEMA_VERSION",),
     "旧程序读到新数据时拒绝启动而非损坏"),
    ("数据", "备份排除集", "src/upgrade/backups.py", ("auth_secrets.json",),
     "备份不含凭据/身份库"),
    ("数据", "换机迁移", "src/gateway/data_portability.py", ("credentials",),
     "知识库/目标/凭据随迁移导出"),
    ("数据", "日志保留双上限", "src/gateway/log_settings.py", ("retention",),
     "天数 + 总量双上限，可界面调整"),
    ("数据", "日志单出口脱敏", "src/logging_config.py", ("RedactingFormatter",),
     "所有 formatter 统一脱敏"),
    ("前端", "错误码顶层兼容", "apps/console/console/src/lib/errorMessage.ts", ("detail",),
     "顶层 code + 422 字段提示"),
    ("前端", "422 字段提示", "apps/console/console/src/lib/errorMessage.ts", ("422",),
     "校验失败指出具体字段"),
    ("前端", "注册字段 password", "apps/console/console/src/lib/auth.ts", ("password",),
     "与后端 SelfRegisterRequest 一致"),
    ("前端", "静态缓存分档", "src/gateway/console_static.py", ("immutable",),
     "哈希资源 immutable / index no-cache"),
    ("前端", "登录首屏可达", "apps/console/console/src/components/Login.tsx", None,
     "首屏渲染登录表单"),
    ("合规", "docs/compliance 落位", "docs/compliance", None,
     "合规文档目录（许可证全文 / NOTICES / SBOM，升级包按 docs/compliance/ 原样打包）"),
    ("合规", "安装目录 licenses", "packaging/licenses", None,
     "第三方许可证全文随包"),
    ("合规", "品牌门禁", "packaging/brand_guard.py", None, "旧主名/错字/死域名硬失败"),
)


def checklist_rows(repo: Path) -> List[Tuple[str, str, str, str, str]]:
    rows = []
    for category, item, rel, needle, note in CHECKLIST:
        path = repo / rel
        if not path.exists():
            rows.append((category, item, rel, "缺", note + "（文件不存在）"))
            continue
        if path.is_dir():
            rows.append((category, item, rel, "有", note))
            continue
        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if needle is None:
            rows.append((category, item, rel, "有", note))
            continue
        if isinstance(needle, tuple) and len(needle) == 2:
            token, minimum = needle
            found = text.count(token)
            state = "有" if found >= minimum else "缺"
            extra = f"（命中 {found}/{minimum}）"
        else:
            token = needle[0] if isinstance(needle, tuple) else needle
            found = text.count(token)
            state = "有" if found > 0 else "缺"
            extra = f"（命中 {found}）"
        rows.append((category, item, rel, state, note + extra))
    return rows


def render_checklist(repo: Path) -> int:
    rows = checklist_rows(repo)
    missing = [row for row in rows if row[3] == "缺"]
    print(f"LiuHao 本轮修复核对表 · {repo}")
    print("-" * 100)
    print(f"{'类别':<6} {'条目':<22} {'证据文件':<46} {'状态':<4} 说明")
    for category, item, rel, state, note in rows:
        print(f"{category:<6} {item:<22} {rel:<46} {state:<4} {note}")
    print("-" * 100)
    if missing:
        print(f"缺 {len(missing)} 项：")
        for category, item, rel, _, note in missing:
            print(f"  - [{category}] {item} -> {rel}：{note}")
        return 1
    print(f"全部 {len(rows)} 项均已在源码树中。")
    return 0


# ---------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="冻结前置清单自动化（只读）")
    parser.add_argument("--repo", default="", help="仓库根（默认从脚本位置推断）")
    parser.add_argument("--python", default="", help="用于跑门禁的解释器（默认当前解释器）")
    parser.add_argument("--checklist", action="store_true", help="只输出修复核对表")
    parser.add_argument("--install-dir", default="", help="已安装的程序目录（跑安装副本同源检查）")
    parser.add_argument("--a11y", action="store_true", help="同时跑无障碍验收（需 playwright）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出门禁结果")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parents[2]
    python = args.python or sys.executable

    if args.checklist:
        return render_checklist(repo)

    started = time.time()
    results: List[Result] = [
        gate_worktree(repo),
        gate_head_pushed(repo),
        gate_import_gateway(repo, python),
        gate_script("测试收集", "scripts/verify_test_collection.py", repo, python, timeout=1800),
        gate_script("OpenAPI 契约", "scripts/verify_openapi_contract.py", repo, python),
        gate_script("应用实例化卫生", "scripts/verify_app_instantiation_hygiene.py", repo, python),
        gate_import_integrity(repo, python),
        gate_installed_copy(repo, python, args.install_dir),
        gate_a11y(repo, python, args.a11y),
        gate_ps51_parse(repo),
        gate_brand(repo, python),
    ]
    results.extend(gate_sbom(repo, python))
    results.append(gate_sbom_paths(repo))
    results.append(gate_log_leak(repo, python))
    results.append(gate_frontend(repo))
    results.append(gate_installer_candidates(repo))

    failed = [item for item in results if item.status == "FAIL"]
    elapsed = time.time() - started

    if args.json:
        payload = {
            "repo": str(repo),
            "python": python,
            "elapsed_seconds": round(elapsed, 1),
            "verdict": "FAIL" if failed else "PASS",
            "results": [item.__dict__ for item in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    print(f"LiuHao 冻结前置清单 · {repo}")
    print(f"解释器 {python}")
    print(f"结论：{'还差 %d 条，拒绝出包' % len(failed) if failed else '全部通过，可以出包'}"
          f"（{elapsed:.1f}s）")
    print("=" * 100)
    for item in results:
        print(f"[{item.status}] {item.name}：{item.evidence}")
        for line in item.details:
            for row in line.splitlines():
                print(f"       {row}")
    print("=" * 100)
    passed = sum(1 for i in results if i.status == "PASS")
    skipped = sum(1 for i in results if i.status == "SKIP")
    print(f"统计：PASS {passed} / SKIP {skipped} / FAIL {len(failed)} / 共 {len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
