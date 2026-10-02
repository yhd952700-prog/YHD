#!/usr/bin/env python3
"""G8/G9 gate: no control may be keyed to an environment variable that no
deployable artifact ever sets.

Why this gate exists
====================
Four independent defects of the same shape were found in this repository
(G8/G9 operational-readiness scan, 2026-09-30). In each one:

  * ``src/`` read an environment variable and took the **permissive** branch when
    it was absent;
  * no deployable artifact (compose / Dockerfile / bundle builder / CI) ever set
    that variable;
  * the test-suite passed anyway, because tests set the variable directly;
  * the readiness signal stayed green.

The four were: ``LIUHAO_ENV`` (``is_production()`` was always False in the
production container), ``LIUHAO_EXECUTOR_FENCE`` (the runtime default-deny gate
never executes), ``LIUHAO_REQUIRE_EXECUTOR_FENCE`` (fence-install failure stays
fail-open), and ``AUDIT_DB_BACKUP_PATH`` (corruption auto-restore is inert).
Fixing the variable fixes one instance; this gate makes the *class* detectable.

What it checks
==============
1. **Seed gates** (:data:`SEED_GATES`) — variables adjudicated by hand as gating
   a permissive branch. Each must be SET in a deployable artifact (or one of its
   declared aliases), or must be ABSENT when that is the safe state.
2. **Scanned gates** (:func:`scan_gate_candidates`) — an AST scan of ``src/`` for
   reads whose absence selects a branch: ``os.environ.get(NAME)`` /
   ``os.getenv(NAME)`` with a falsy-or-absent default, read under a conditional
   ancestor. These are the ones nobody has adjudicated yet; the gate refuses to
   let them sit unclassified.
3. **Anti-decay** — a seed or allowlist entry whose variable is no longer read
   anywhere in ``src/`` is a FAIL, not a silent pass. A registry that rots is
   the same decoration this gate exists to remove.

Every scanned candidate must be resolved by exactly one of: being SET in an
artifact, being in ``SEED_GATES``, or being in ``ALLOWLIST`` **with a written
reason**. There is no implicit leniency and no "warn only" path to green.

Scope, stated honestly
======================
The AST scan is a *necessary* detector, not a sufficient one. A variable whose
absence selects a permissive branch but whose read is not syntactically nested
under a conditional (for example ``is_production()``'s original
``return env in (...)``) is **not** detectable by scanning — which is precisely
why ``SEED_GATES`` exists and why entries there must carry a code reference.
Adding a new control to ``src/`` therefore requires a human to decide whether it
belongs in ``SEED_GATES``; the gate only enforces that the decision is recorded.

Exit status
===========
  * ``0`` — every gate variable is resolved.
  * ``1`` — at least one gate variable is unresolved (real finding).
  * ``2`` — the gate could not run (missing repo layout, unparsable tree).

Usage
=====
  python scripts/verify_deployment_gate_vars.py
  python scripts/verify_deployment_gate_vars.py --json
  python scripts/verify_deployment_gate_vars.py --extra-artifacts proposed.env

``--extra-artifacts`` adds a file to the artifact set **without modifying the
repository**. It exists so an owner can prove a proposed compose addition turns
this gate green before asking for the deployment semantics to be changed; it is
also how this gate's own negative control is demonstrated.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# sys.path bootstrap: `import src...` and direct invocation from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

SRC_DIR = REPO_ROOT / "src"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

#: Artifacts that actually ship. Anything that only exists on a developer laptop
#: or inside a test does not count as "the deployment sets this".
ARTIFACT_GLOBS = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.*.yml",
    "docker-compose.*.yaml",
    "Dockerfile",
    "Dockerfile.*",
    ".env",
    ".env.example",
)
ARTIFACT_EXTRA_FILES = (
    "scripts/build_cloud_bundle.py",
)
ARTIFACT_SKIP_PARTS = (".venv", "node_modules", ".git", "__pycache__")

_GETTERS = frozenset({"os.environ.get", "os.getenv", "environ.get", "getenv"})
_CONDITIONAL_NODES = (ast.If, ast.While, ast.IfExp, ast.BoolOp, ast.UnaryOp, ast.Compare)


# --------------------------------------------------------------------------- #
# Seed gates -- adjudicated by hand. Each entry MUST carry `why` and `refs`.
#   expect="set"    -> the variable (or one of `aliases`) must appear in an
#                      artifact; absence is the permissive branch.
#   expect="absent" -> the variable must NOT appear in any artifact; presence is
#                      the permissive branch (dev-only switches).
# --------------------------------------------------------------------------- #
SEED_GATES: Tuple[Dict[str, object], ...] = (
    {
        "var": "LIUHAO_EXECUTOR_FENCE",
        "expect": "set",
        "aliases": (),
        "why": (
            "the runtime default-deny autonomous-action gate is opt-in; unset "
            "means the gate NEVER executes even though boot logs the fence as "
            "installed, so every fenced action runs un-gated"
        ),
        "refs": ("src/kernels/_crosscutting.py:676", "src/kernels/_crosscutting.py:679"),
    },
    {
        "var": "LIUHAO_REQUIRE_EXECUTOR_FENCE",
        "expect": "set",
        "aliases": (),
        "why": (
            "converts an executor-fence install failure from fail-open (boot "
            "un-fenced, log ERROR, keep serving) to fail-closed (abort boot); "
            "unset means the deployment keeps the fail-open default"
        ),
        "refs": ("src/gateway/main.py:246",),
    },
    {
        "var": "LIUHAO_POSTURE_STRICT",
        "expect": "set",
        "aliases": (),
        "why": (
            "makes an undeterminable deployment posture a hard failure; unset "
            "means posture silently resolves to non-production (fail-open by "
            "compatibility) and every posture-gated branch takes the permissive path"
        ),
        "refs": ("src/security/posture.py:218", "src/security/posture.py:219"),
    },
    {
        "var": "LIUHAO_ENV",
        "expect": "set",
        "aliases": ("ENVIRONMENT", "APP_ENV"),
        "why": (
            "production-posture primary alias; satisfied when ANY alias is set "
            "because posture.py resolves LIUHAO_ENV > ENVIRONMENT > APP_ENV. "
            "Recorded here because the original single-variable read made "
            "is_production() permanently False in the production container"
        ),
        "refs": ("src/security/posture.py:58", "src/security/posture.py:150"),
    },
    {
        "var": "AUDIT_DB_BACKUP_PATH",
        "expect": "set",
        "aliases": (),
        "why": (
            "transaction-consistent backup consumed by audit corruption "
            "auto-restore; unset means the path defaults to <db>.backup, which no "
            "production code ever writes, so the restore branch is inert and a "
            "corruption event silently starts a new hash chain"
        ),
        "refs": ("src/kernels/audit/__init__.py:395",),
    },
    {
        "var": "LIUHAO_EXECUTOR_FENCE_LEASE_DIR",
        "expect": "set",
        "aliases": (),
        "why": (
            "with backend=file and this unset, attach_default_executor_fence() "
            "silently builds the single-node SqliteExecutorLease while the boot "
            "log prints backend=file; operators read cross-process fencing and "
            "get single-node"
        ),
        "refs": ("src/kernels/execution/fence.py:1180",),
    },
    {
        "var": "LIUHAO_SANDBOX_ENFORCE",
        "expect": "set",
        "aliases": (),
        "why": (
            "arms genuine-isolation enforcement for HIGH/CRITICAL sandbox "
            "actions; unset means enforcement is off"
        ),
        "refs": ("src/plugins/sandbox/assurance.py:72",),
    },
    {
        "var": "LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS",
        "expect": "set",
        "aliases": (),
        "why": (
            "the N+1 concurrent-executor cap; unset means no cap"
        ),
        "refs": ("src/kernels/execution/fence.py:1152",),
    },
    {
        "var": "LIUHAO_API_KEY_STORE",
        "expect": "set",
        "aliases": (),
        "why": (
            "persistent (hash-only) API-key registry path; unset means the "
            "registry stays in memory, so every issued key is lost on restart "
            "in a container deployment"
        ),
        "refs": ("src/security/api_keys.py:325",),
    },
    {
        "var": "LIUHAO_SECRET_DEV_EPHEMERAL",
        "expect": "absent",
        "aliases": (),
        "why": (
            "dev-only in-memory secret backend; it must NOT appear in any "
            "deployment artifact (production refuses it via is_production(), but "
            "any non-production deployment that sets it gets a non-durable, "
            "non-audited secret store)"
        ),
        "refs": ("src/security/encryption.py:98",),
    },
)


# --------------------------------------------------------------------------- #
# Allowlist -- scan-detected variables judged NOT to be gates.
#
# An entry here is a claim, so it must carry a reason and it is checked for
# rot: if the variable stops being read in src/ the gate FAILS. The reason must
# say what the unset branch does, not just "it is fine".
# --------------------------------------------------------------------------- #
ALLOWLIST: Dict[str, str] = {
    "AUDIT_HWM_PATH": (
        "path of the optional audit high-water-mark telemetry file; unset falls "
        "back to ~/.liuhao/audit_hwm.json or disables HWM file writing. It "
        "selects where optional telemetry is written, not whether a control "
        "applies."
    ),
    "AUDIT_KERNEL_HWM_PATH": (
        "same role as AUDIT_HWM_PATH for the kernel-side logger: an optional "
        "telemetry file path with a resolved default, not a control gate."
    ),
    "HOST": (
        "bind-address override for the dev entrypoint; unset falls back to a "
        "loopback default. The published port (PORT) IS set in "
        "docker-compose.prod.yml, so absence does not change reachability."
    ),
    "LIUHAO_EXECUTOR_FENCE_REDIS_URL": (
        "only consulted when LIUHAO_DISTRIBUTED_LEASE_BACKEND=redis, which no "
        "artifact sets today; unset yields a localhost default. Becomes a real "
        "gate the moment redis is selected as the backend -- reclassify then."
    ),
    "LIUHAO_MODEL_ROUTER": (
        "model-router strategy selection with a documented default strategy; "
        "absence changes routing behaviour, not whether a safety control "
        "applies."
    ),
    "LIUHAO_SECRET_STORE_DIR": (
        "directory override for the encrypted local secret store with a "
        "documented default (~/.liuhao/secrets). Durability of the store is "
        "governed by the fail-closed backend gate, which RAISES when no backend "
        "is configured, so this override is not the permissive path."
    ),
}


# --------------------------------------------------------------------------- #
# src/ scan
# --------------------------------------------------------------------------- #
def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return "%s.%s" % (prefix, node.attr) if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _ancestor_map(tree: ast.AST) -> Dict[int, List[ast.AST]]:
    """Map id(child) -> list of ancestors, so a read can be tested for nesting
    under a conditional without walking the tree once per candidate."""
    out: Dict[int, List[ast.AST]] = {}

    def walk(node: ast.AST, chain: List[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            out[id(child)] = chain
            walk(child, chain + [node])

    walk(tree, [])
    return out


def _module_string_constants(tree: ast.AST) -> Dict[str, str]:
    """Module-level ``NAME = "VALUE"`` bindings.

    Needed because a gate variable is often named by a constant rather than
    spelled inline -- ``src/security/posture.py`` reads
    ``source.get(STRICT_ENV)`` where ``STRICT_ENV = "LIUHAO_POSTURE_STRICT"``.
    A scanner that only understood inline literals reported those gates as
    "no longer read", which is exactly the kind of false negative that lets a
    control disappear unnoticed.
    """
    out: Dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                out[target.id] = value.value
    return out


def _resolve_name(node: ast.AST, consts: Dict[str, str]) -> Optional[str]:
    """Literal string of a getter argument, resolving module constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _falsy_default(call: ast.Call) -> bool:
    """True when the read yields a falsy value if the variable is absent."""
    if len(call.args) >= 2:
        default = call.args[1]
        if isinstance(default, ast.Constant):
            return not default.value
        return False  # a non-constant default is an explicit, reviewed choice
    for kw in call.keywords:
        if kw.arg in ("default", "fallback") and isinstance(kw.value, ast.Constant):
            return not kw.value.value
    return True  # no default at all -> None


def scan_env_reads(src_dir: Path = SRC_DIR) -> Dict[str, List[Tuple[str, int]]]:
    """Every literal environment-variable read under ``src/``."""
    reads: Dict[str, List[Tuple[str, int]]] = {}
    if not src_dir.is_dir():
        return reads
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        consts = _module_string_constants(tree)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _dotted(node.func) not in _GETTERS:
                continue
            if not node.args:
                continue
            name = _resolve_name(node.args[0], consts)
            if name:
                reads.setdefault(name, []).append((rel, node.lineno))
    return reads


def scan_literal_mentions(names: Sequence[str],
                          src_dir: Path = SRC_DIR) -> Dict[str, List[Tuple[str, int]]]:
    """Where a variable is spelled as a string literal under ``src/``.

    Secondary evidence for "this variable is still part of the code" when the
    read goes through a constant (``src/security/posture.py`` iterates
    ``POSTURE_ENV_VARS``, so no single ``os.environ.get("LIUHAO_ENV")`` call
    exists). It is deliberately weaker than :func:`scan_env_reads` -- it proves
    the name is present, not that it is read -- and is used only to keep a seed
    entry from being misreported as stale.
    """
    wanted = set(names)
    out: Dict[str, List[Tuple[str, int]]] = {}
    if not src_dir.is_dir() or not wanted:
        return out
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        seen: Dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in wanted and node.value not in seen:
                    seen[node.value] = node.lineno
        for name, lineno in seen.items():
            out.setdefault(name, []).append((rel, lineno))
    return out


def scan_gate_candidates(src_dir: Path = SRC_DIR) -> Dict[str, List[Tuple[str, int]]]:
    """Reads whose absence selects a branch: falsy default AND a conditional
    ancestor. See the module docstring for what this cannot see."""
    candidates: Dict[str, List[Tuple[str, int]]] = {}
    if not src_dir.is_dir():
        return candidates
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        ancestors = _ancestor_map(tree)
        consts = _module_string_constants(tree)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _dotted(node.func) not in _GETTERS:
                continue
            if not node.args:
                continue
            if not _resolve_name(node.args[0], consts):
                continue
            name = _resolve_name(node.args[0], consts)
            if not _falsy_default(node):
                continue
            chain = ancestors.get(id(node), [])
            if not any(isinstance(a, _CONDITIONAL_NODES) for a in chain):
                continue
            # `name` is already the *resolved* variable name (a str) -- see
            # `_resolve_name`. It used to be spelled `first.value` here, which is
            # an undefined name, so this scanner died with NameError the first
            # time it met a candidate. A gate that crashes on its own input is
            # indistinguishable from a gate that passes: neither produces a
            # finding. Fixed so the scan actually reports unclassified gates.
            candidates.setdefault(name, []).append((rel, node.lineno))
    return candidates


# --------------------------------------------------------------------------- #
# Deployable-artifact scan
# --------------------------------------------------------------------------- #
def collect_artifacts(extra: Sequence[str] = ()) -> List[Path]:
    found: List[Path] = []
    for pattern in ARTIFACT_GLOBS:
        for path in REPO_ROOT.rglob(pattern):
            if any(part in ARTIFACT_SKIP_PARTS for part in path.parts):
                continue
            if path.is_file():
                found.append(path)
    for rel in ARTIFACT_EXTRA_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            found.append(path)
    if WORKFLOWS_DIR.is_dir():
        for suffix in (".yml", ".yaml"):
            found.extend(sorted(WORKFLOWS_DIR.glob("*" + suffix)))
    for raw in extra:
        path = Path(raw)
        if path.is_file():
            found.append(path)
    # stable, de-duplicated, repo-relative where possible
    unique: List[Path] = []
    seen: Set[str] = set()
    for path in sorted(found, key=lambda p: str(p).lower()):
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def scan_artifact_vars(artifacts: Sequence[Path]) -> Dict[str, List[str]]:
    """Variable names any artifact sets, mapped to the artifacts that set them."""
    texts: List[Tuple[Path, str]] = []
    for path in artifacts:
        try:
            texts.append((path, path.read_text(encoding="utf-8", errors="replace")))
        except (UnicodeDecodeError, OSError):
            continue
    found: Dict[str, List[str]] = {}

    def label(path: Path) -> str:
        try:
            return path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return str(path)

    for path, text in texts:
        names: Set[str] = set()
        # `NAME=value` / `NAME: value` / `- NAME=value` (compose, Dockerfile ENV,
        # workflow env:, .env, `os.environ["NAME"] = ...` in the bundle builder)
        for match in re.finditer(r"(?m)^[ \t]*-?[ \t]*[\"']?([A-Z][A-Z0-9_]{1,})[\"']?[ \t]*(?:=|:)", text):
            names.add(match.group(1))
        # compose pass-through form: `- NAME` (value supplied by the host)
        for match in re.finditer(r"(?m)^[ \t]*-[ \t]*([A-Z][A-Z0-9_]{1,})[ \t]*$", text):
            names.add(match.group(1))
        # inline assignment forms inside python / yaml one-liners
        for match in re.finditer(r"[\"']([A-Z][A-Z0-9_]{1,})[\"'][ \t]*[:=]", text):
            names.add(match.group(1))
        for name in names:
            found.setdefault(name, []).append(label(path))
    return found


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
class Finding(object):
    def __init__(self, var: str, kind: str, detail: str,
                 refs: Sequence[str] = (), suggestion: str = "") -> None:
        self.var = var
        self.kind = kind
        self.detail = detail
        self.refs = list(refs)
        self.suggestion = suggestion

    def as_dict(self) -> Dict[str, object]:
        return {
            "var": self.var,
            "kind": self.kind,
            "detail": self.detail,
            "refs": self.refs,
            "suggestion": self.suggestion,
        }


def evaluate(artifact_vars: Dict[str, List[str]],
             reads: Dict[str, List[Tuple[str, int]]],
             candidates: Dict[str, List[Tuple[str, int]]]) -> List[Finding]:
    findings: List[Finding] = []
    seed_vars = {str(entry["var"]) for entry in SEED_GATES}

    # 1. seed gates
    for entry in SEED_GATES:
        var = str(entry["var"])
        expect = str(entry.get("expect", "set"))
        aliases: Sequence[str] = tuple(entry.get("aliases") or ())  # type: ignore[arg-type]
        refs: Sequence[str] = tuple(entry.get("refs") or ())  # type: ignore[arg-type]
        why = str(entry.get("why", "")).strip()

        if not why:
            findings.append(Finding(
                var, "seed-entry-has-no-reason",
                "SEED_GATES entry carries no `why`; an unexplained gate cannot "
                "be reviewed."))
        if var not in reads and not any(a in reads for a in aliases):
            findings.append(Finding(
                var, "stale-seed-entry",
                "listed as a gate but `%s` (nor any alias %s) is read anywhere "
                "under src/ -- either the control was removed or this entry "
                "rotted" % (var, list(aliases)),
                refs,
                "remove the entry or point it at the variable now in use"))
            continue

        set_in = artifact_vars.get(var, [])
        alias_hits = [a for a in aliases if a in artifact_vars]
        if expect == "set":
            if set_in or alias_hits:
                continue
            findings.append(Finding(
                var, "gate-var-never-set",
                "%s" % why,
                refs or ["%s:%d" % (r[0], r[1]) for r in reads.get(var, [])],
                "set %s in docker-compose.prod.yml (or the bundle builder), or "
                "move it to ALLOWLIST with a written reason" % var))
        elif expect == "absent":
            if not set_in:
                continue
            findings.append(Finding(
                var, "dev-only-var-set-in-artifact",
                "%s -- but it is set in %s" % (why, ", ".join(sorted(set(set_in)))),
                refs,
                "remove it from %s" % ", ".join(sorted(set(set_in)))))
        else:
            findings.append(Finding(
                var, "bad-seed-entry",
                "expect must be 'set' or 'absent', got %r" % expect))

    # 2. scan-detected candidates must be classified
    for var in sorted(candidates):
        if var in seed_vars:
            continue
        if var in ALLOWLIST:
            reason = (ALLOWLIST[var] or "").strip()
            if not reason:
                findings.append(Finding(
                    var, "allowlist-entry-has-no-reason",
                    "ALLOWLIST entry has an empty reason; implicit leniency is "
                    "not permitted."))
            continue
        findings.append(Finding(
            var, "unclassified-gate-candidate",
            "absence selects a branch at the site(s) below, and the variable is "
            "in neither SEED_GATES nor ALLOWLIST, so nobody has decided what the "
            "unset branch means in a deployment",
            ["%s:%d" % (r[0], r[1]) for r in candidates[var]],
            "add it to SEED_GATES (with why + refs) if absence is permissive, or "
            "to ALLOWLIST with a reason stating what the unset branch does"))

    # 3. allowlist rot
    for var in sorted(ALLOWLIST):
        if var not in reads:
            findings.append(Finding(
                var, "stale-allowlist-entry",
                "allowlisted, but `%s` is no longer read anywhere under src/ -- "
                "the entry must be removed so it cannot mask a future re-read"
                % var,
                [], "remove from ALLOWLIST"))

    return findings


def render(findings: Sequence[Finding],
           artifact_count: int,
           reads_count: int,
           candidate_count: int) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("Deployment gate-variable gate")
    lines.append("=" * 78)
    lines.append("  src/ env reads scanned : %d" % reads_count)
    lines.append("  conditional candidates : %d" % candidate_count)
    lines.append("  deployable artifacts   : %d" % artifact_count)
    lines.append("")
    if not findings:
        lines.append("RESULT: PASS -- every gate variable is resolved by an "
                     "artifact, a seed entry, or a reasoned allowlist entry.")
        return "\n".join(lines)
    lines.append("RESULT: FAIL -- %d unresolved gate variable(s):" % len(findings))
    lines.append("")
    for i, f in enumerate(findings, 1):
        lines.append("[%d] %s  (%s)" % (i, f.var, f.kind))
        lines.append("    %s" % f.detail)
        if f.refs:
            lines.append("    read at: %s" % ", ".join(f.refs))
        if f.suggestion:
            lines.append("    fix:     %s" % f.suggestion)
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a control is gated on an env var no artifact sets.")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings on stdout")
    parser.add_argument("--extra-artifacts", action="append", default=[],
                        metavar="PATH",
                        help="add a file to the artifact set WITHOUT modifying "
                             "the repo (for proving a proposed fix)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not SRC_DIR.is_dir():
        print("ERROR: %s not found -- run from the repository root" % SRC_DIR,
              file=sys.stderr)
        return EXIT_ERROR

    artifacts = collect_artifacts(args.extra_artifacts)
    artifact_vars = scan_artifact_vars(artifacts)
    reads = scan_env_reads()
    candidates = scan_gate_candidates()
    findings = evaluate(artifact_vars, reads, candidates)

    if args.json:
        print(json.dumps({
            "exit": EXIT_FAIL if findings else EXIT_OK,
            "artifacts": [str(p) for p in artifacts],
            "env_reads": len(reads),
            "candidates": len(candidates),
            "findings": [f.as_dict() for f in findings],
        }, indent=2))
    else:
        print(render(findings, len(artifacts), len(reads), len(candidates)))

    return EXIT_FAIL if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
