"""Meta-guardrail: every ``scripts/verify_*.py`` must be able to fail.

A script whose name starts with ``verify_`` but which can never exit non-zero is
a false safety signal: reviewers assume an invariant is enforced while in
reality nothing is checked. Three such defects were found in this repository
(Round 74): ``verify_orm_vs_db.py`` and ``verify_persistence.py`` could not even
start (``ModuleNotFoundError: No module named 'src'``), and neither of them -- nor
``verify_ai_layer_audit.py`` -- was ever invoked by CI.

This module makes that class of defect mechanically impossible. For every
``scripts/verify_*.py`` it enforces:

1. ``sys.path`` is bootstrapped, so ``import src...`` resolves when the script is
   invoked directly (``python scripts/foo.py`` from the repository root);
2. the script contains a construct that can yield a non-zero process status;
3. the script is invoked by at least one workflow under ``.github/workflows/``.

A script that is deliberately a pure report must be listed in
:data:`DIAGNOSTIC_REPORTERS` with a reason; it is then exempt from (2) and (3),
and the allowlist itself is checked so it cannot rot or be abused. The allowlist
is currently empty, which is the intended steady state.

"Wired" is not the same as "protecting". A gate invoked only from a timed or
manual workflow runs zero times on the next commit, so this module classifies
every gate by the narrowest trigger that invokes it:
``push-protected`` / ``scheduled-only`` / ``unwired``. The middle class is a
legitimate engineering trade-off, not a defect, but it must be *declared* in
:data:`SCHEDULED_ONLY_GATES` with a reason -- otherwise a zero-protection gate
carries the credibility of a real one, and the failure is invisible precisely
because the guardrail stays green.

"Invoked" is not the same as "runs for us" either. A gate can be named in a
workflow step and still execute zero times on the branch where work lands --
when every job that invokes it sits in a workflow whose ``on:`` excludes that
branch, or behind a job-level ``if:`` that pins another branch. So this module
reads ``on:`` (branch admission), ``if:`` (branch/event pins) and ``needs:``
(dangling upstream) as well, and exposes :func:`_protects_target_branch` as the
real bar, with :func:`_is_wired_into_ci` kept as the weaker "invoked anywhere"
floor. A gate that clears the floor but not the bar is its own visible state,
and must be declared in :data:`SCHEDULED_ONLY_GATES` or recorded in the
exemption ledger -- see :func:`test_gates_that_never_run_on_a_target_branch_
are_recorded`.

Limitation, stated honestly: (2) is a *necessary* condition, not a sufficient
one -- a script that raises only on an unreachable branch would still pass.
Sufficiency comes from the scripts actually running in CI (3) plus the negative
controls exercised in Round 74. Classification is likewise structural: it reads
declared triggers, so a workflow whose ``on:`` is dynamic cannot be classified
more precisely than its narrowest declared trigger.
"""

from __future__ import annotations

import ast
import functools
import os
import pathlib
import re
import subprocess
import textwrap

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

#: ``verify_*`` scripts that report rather than gate, mapped to the reason.
#:
#: An entry here is a *claim that the file has no pass/fail contract*; it is
#: verified in :func:`test_allowlist_entries_are_genuine_reporters`. Giving a
#: listed script a real failure path will break this module -- which is the
#: intended behaviour: remove it from the allowlist so the gate checks apply.
DIAGNOSTIC_REPORTERS: dict[str, str] = {}

#: ``verify_*`` scripts that are invoked ONLY from a timed / manual workflow,
#: mapped to the reason that trade-off is accepted.
#:
#: Same shape and same intent as :data:`DIAGNOSTIC_REPORTERS`, one layer up: a
#: listed reporter is a claim "this has no pass/fail contract", a listed gate
#: here is a claim "this runs on a schedule, not on your commit". Both must be
#: written down, because an unlisted one is a gate that looks wired and protects
#: nothing on the next push.
#:
#: Verified by :func:`test_scheduled_only_gates_are_declared_with_reasons`, so
#: this cannot rot: a gate that becomes push-protected, disappears, or gains a
#: reason-less entry breaks this module.
SCHEDULED_ONLY_GATES: dict[str, str] = {
    "verify_metrics_persist.py": (
        "Needs a real PostgreSQL URL to validate persistence end to end "
        "(STAGING_DATABASE_URL), so it runs on a schedule and by hand rather "
        "than on every push. Tracked as a real CD gate, not a CI gate: "
        "ci-cd.yml's deploy-staging/deploy-production deliberately exit 1 and "
        "name this workflow as the real verified CD path. Registration here "
        "records that 'we accept this gate does not run per-commit'."
    ),
}

_BOOTSTRAP_RE = re.compile(r"sys\.path\.(insert|append)")

#: Calls that terminate the process with a status. ``os._exit`` is included
#: because a gate that bypasses ``atexit``/flush handlers still sets the status
#: CI reads.
_EXIT_CALLS = {"sys.exit", "exit", "os._exit"}
_SYSTEM_EXIT_CALLS = {"SystemExit", "builtins.SystemExit"}


def _all_verify_scripts() -> list[pathlib.Path]:
    return sorted(SCRIPTS_DIR.glob("verify_*.py"))


def _gate_scripts() -> list[pathlib.Path]:
    return [p for p in _all_verify_scripts() if p.name not in DIAGNOSTIC_REPORTERS]


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


#: Step fields that carry a shell command, i.e. that can invoke a script.
_STEP_COMMAND_KEYS = ("run",)
#: ``with:`` entries that carry a command for a remote/composite action. None of
#: these is used in this repository today (every gate is invoked from ``run:``);
#: they are listed so adding one later does not silently unwire its script.
_WITH_COMMAND_KEYS = ("args", "command", "script", "entrypoint", "entrypoint_args")


def _workflow_files(workflows_dir: pathlib.Path = WORKFLOWS_DIR) -> list[pathlib.Path]:
    if not workflows_dir.is_dir():
        return []
    return sorted(
        p for p in workflows_dir.iterdir() if p.suffix in (".yml", ".yaml")
    )


@functools.lru_cache(maxsize=None)
def _load_workflows(workflows_dir: pathlib.Path = WORKFLOWS_DIR) -> tuple[object, ...]:
    """Parse every workflow file.

    A file that does not parse is raised as an :class:`AssertionError` naming the
    file and the error, because skipping an unreadable workflow is exactly how a
    wiring check quietly stops checking anything.
    """
    documents = []
    for path in _workflow_files(workflows_dir):
        try:
            documents.append(yaml.safe_load(_source(path)))
        except yaml.YAMLError as exc:
            raise AssertionError(
                "%s is not valid YAML (%s) -- a workflow this test cannot read is a "
                "workflow it cannot verify, so it fails instead of being skipped"
                % (path.name, exc)
            ) from exc
    return tuple(documents)


def _job_step_commands(job: object) -> list[str]:
    """Command-bearing strings from one job's ``steps``."""
    commands: list[str] = []
    if not isinstance(job, dict):
        return commands
    steps = job.get("steps")
    if not isinstance(steps, list):
        return commands
    for step in steps:
        if not isinstance(step, dict):
            continue
        for key in _STEP_COMMAND_KEYS:
            value = step.get(key)
            if isinstance(value, str):
                commands.append(value)
        with_block = step.get("with")
        if isinstance(with_block, dict):
            for key, value in with_block.items():
                if key in _WITH_COMMAND_KEYS and isinstance(value, str):
                    commands.append(value)
    return commands


def _step_commands(document: object) -> list[str]:
    """Command-bearing strings from ``jobs -> * -> steps -> *``.

    Deliberately narrow: a script named in a YAML comment, in a step ``name:``
    or in a job name is *not* invoked. Matching on the raw workflow text made a
    comment listing the deliberately-unwired scripts look like five real
    invocations, which turned this guardrail green while nothing was wired --
    a false green, worse than the false positive it replaced.
    """
    commands: list[str] = []
    if not isinstance(document, dict):
        return commands
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return commands
    for job in jobs.values():
        commands.extend(_job_step_commands(job))
    return commands


def _workflow_invokes(document: object, script_name: str) -> bool:
    return any(script_name in command for command in _step_commands(document))


#: Trigger keys that mean "this workflow runs on a commit/PR", i.e. a gate
#: invoked only from such a workflow actually protects every push.
_PUSH_TRIGGER_KEYS = frozenset({"push", "pull_request"})
#: Trigger keys that mean "this workflow runs on a timer or a human click".
#: A gate invoked ONLY from these protects nothing on the next commit.
_SIDED_TRIGGER_KEYS = frozenset(
    {"schedule", "workflow_dispatch", "workflow_call", "repository_dispatch"}
)


def _workflow_triggers(document: object) -> frozenset[str]:
    """The trigger keys a workflow declares under ``on:``.

    ``on:`` is parsed by PyYAML as the boolean ``True`` (YAML 1.1 bare ``on``),
    so both spellings are accepted. A workflow may also use the list form
    (``on: [push, schedule]``) instead of a mapping.
    """
    if not isinstance(document, dict):
        return frozenset()
    raw = document.get("on", document.get(True))
    if isinstance(raw, str):
        return frozenset({raw})
    if isinstance(raw, list):
        return frozenset(str(item) for item in raw)
    if isinstance(raw, dict):
        return frozenset(str(key) for key in raw)
    return frozenset()


#: Branches on which work actually lands. A gate that cannot run on any of them
#: protects nothing for us, however often it runs elsewhere. Overridable via
#: ``LIUHAO_TARGET_BRANCHES`` so the check survives a branch rename without
#: being silently weakened.
DEFAULT_TARGET_BRANCHES = ("p36",)


def _target_branches() -> frozenset[str]:
    raw = os.environ.get("LIUHAO_TARGET_BRANCHES")
    if raw is None:
        raw = ",".join(DEFAULT_TARGET_BRANCHES)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


#: Sentinel: the trigger admits every branch (``**``, ``*``, or no ``branches:``).
_ALL_BRANCHES = "__ALL_BRANCHES__"
#: Value carried by a ``github.ref_name == github.event.repository.default_branch``
#: pin whose default branch cannot be resolved. It never matches a target branch,
#: so an unprovable pin is never counted as protection.
_UNKNOWN_BRANCH = "__UNKNOWN_BRANCH__"

_REF_EXPRS = ("github.ref", "github.ref_name")
_CMP_RE = re.compile(
    r"""(['"][^'"]*['"]|[A-Za-z_][A-Za-z0-9_.]*)\s*==\s*"""
    r"""(['"][^'"]*['"]|[A-Za-z_][A-Za-z0-9_.]*)"""
)


def _unquote_token(token: str) -> str | None:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return None


@functools.lru_cache(maxsize=None)
def _default_branch() -> str | None:
    """The repository's default branch, or ``None`` when it cannot be resolved.

    Only needed to evaluate ``ref_name == github.event.repository.default_branch``
    pins. ``LIUHAO_DEFAULT_BRANCH`` wins, so an environment where ``origin/HEAD``
    is unset is explicit rather than quietly changing the verdict.
    """
    override = os.environ.get("LIUHAO_DEFAULT_BRANCH")
    if override:
        return override.strip()
    try:
        proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    if not ref:
        return None
    return ref.split("/", 1)[1] if ref.startswith("origin/") else ref


def _trigger_branch_map(document: object) -> dict[str, object]:
    """``{event: admitted branches}``; ``_ALL_BRANCHES`` means every branch."""
    if not isinstance(document, dict):
        return {}
    raw = document.get("on", document.get(True))
    if isinstance(raw, str):
        return {raw: _ALL_BRANCHES}
    if isinstance(raw, list):
        return {str(item): _ALL_BRANCHES for item in raw}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    for event, cfg in raw.items():
        if not isinstance(cfg, dict):
            out[str(event)] = _ALL_BRANCHES
            continue
        branches = cfg.get("branches")
        if branches is None:
            out[str(event)] = _ALL_BRANCHES
        elif isinstance(branches, str):
            out[str(event)] = frozenset({branches})
        elif any(str(b) in ("**", "*") for b in branches):
            out[str(event)] = _ALL_BRANCHES
        else:
            out[str(event)] = frozenset(str(b) for b in branches)
    return out


def _job_if_pins(if_expression: object) -> tuple[frozenset[str], frozenset[str]]:
    """``(events, branches)`` pinned by a job-level ``if:``.

    Only positive ``==`` qualifiers are collected: a ``!=`` widens the set and
    can never by itself make a job unreachable, so collecting it would
    manufacture false reds.
    """
    events: set[str] = set()
    branches: set[str] = set()
    for match in _CMP_RE.finditer(str(if_expression or "")):
        for expr, other in ((match.group(1), match.group(2)), (match.group(2), match.group(1))):
            if expr == "github.event_name":
                literal = _unquote_token(other)
                if literal is not None:
                    events.add(literal)
            elif expr in _REF_EXPRS:
                if "github.event.repository.default_branch" in other:
                    branches.add(_default_branch() or _UNKNOWN_BRANCH)
                    continue
                literal = _unquote_token(other)
                if literal is None:
                    continue
                if literal.startswith("refs/heads/"):
                    literal = literal[len("refs/heads/"):]
                branches.add(literal)
    return frozenset(events), frozenset(branches)


def _normalise_needs(needs: object) -> list[str]:
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, dict):
        return [str(key) for key in needs]
    if isinstance(needs, list):
        return [str(item) for item in needs]
    return [str(needs)]


def _job_runs_on_target(document: object, job: object) -> bool:
    """Can this job execute when work lands on a target branch?

    Two layers on top of "the script name appears in a command":

    1. TRIGGER ADMISSION -- the workflow's ``on:`` must admit a target branch
       for at least one event. ``ci-cd.yml`` triggers only on ``main``/``develop``,
       so nothing in it has ever run for work on ``p36``.
    2. IF PINS -- a job-level ``if:`` that pins an event or a branch must not
       exclude that combination. This is the class that silently disabled four
       ``ci.yml`` jobs (``ref_name == github.event.repository.default_branch``
       while the default branch was not the working branch).

    A ``needs:`` naming a job the workflow does not define makes the job
    unrunnable as well, so that is checked too. (Whether a satisfied ``needs:``
    chain survives a red upstream is CRITICAL-5's job, enforced by
    ``scripts/verify_critical5_watchdog.py``; it is deliberately not duplicated
    here, because two copies of that rule would drift.)
    """
    if not isinstance(document, dict) or not isinstance(job, dict):
        return False
    branch_map = _trigger_branch_map(document)
    if not branch_map:
        return False
    targets = _target_branches()
    # Only commit/PR events carry "work landed on a branch". A `schedule` or
    # `workflow_dispatch` trigger admits branches too, but it never fires for a
    # push, so it cannot make a gate protect one (that is SCHEDULED_ONLY's job).
    admissible = {
        event
        for event, branches in branch_map.items()
        if event in _PUSH_TRIGGER_KEYS
        and (
            branches == _ALL_BRANCHES
            or (isinstance(branches, frozenset) and branches & targets)
        )
    }
    if not admissible:
        return False
    pinned_events, pinned_branches = _job_if_pins(job.get("if"))
    if pinned_events and not (pinned_events & admissible):
        return False
    if pinned_branches and not (pinned_branches & targets):
        return False
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        for upstream in _normalise_needs(job.get("needs")):
            if upstream not in jobs:
                return False
    return True


def _invoking_jobs(script_name: str) -> list[tuple[str, str, bool]]:
    """``(workflow, job, runs_on_target)`` for every job invoking the script."""
    found: list[tuple[str, str, bool]] = []
    for path, document in zip(_workflow_files(), _load_workflows()):
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if not any(script_name in cmd for cmd in _job_step_commands(job)):
                continue
            found.append((path.name, str(job_name), _job_runs_on_target(document, job)))
    return found


def _protects_target_branch(script_name: str) -> bool:
    """True only if some job invoking the script can run on a target branch.

    ``_is_wired_into_ci`` answers "is it invoked anywhere"; this answers the
    question that actually matters, "does that invocation protect the branch we
    work on". A gate invoked solely from a workflow whose triggers exclude every
    target branch -- or solely from a job whose ``if:`` pins another branch --
    runs zero times for our work while its name still appears in CI.
    """
    return any(runs_on_target for _, _, runs_on_target in _invoking_jobs(script_name))


#: Classification of a gate by WHERE it is invoked. The distinction that matters:
#: a gate invoked only from a nightly/manual workflow runs zero times on the
#: next commit, so counting it as "wired" lets a zero-protection gate borrow
#: the credibility of a real one. That is a *declared trade-off*, not a defect --
#: hence :data:`SCHEDULED_ONLY_GATES`, which forces the trade-off to be written
#: down instead of being discovered later.
PUSH_PROTECTED = "push-protected"
SCHEDULED_ONLY = "scheduled-only"
UNWIRED = "unwired"


def _classify_wiring(script_name: str) -> str:
    """Classify a gate by the narrowest trigger that invokes it.

    Reading ``on:`` is necessary but not sufficient: a push-triggered workflow
    whose ``branches:`` exclude every target branch, or whose job ``if:`` pins
    another branch, still runs zero times for our work. So an invocation only
    counts toward ``push-protected`` when :func:`_protects_target_branch`
    confirms the job is actually reachable.
    """
    saw_push = False
    saw_sided = False
    for document in _load_workflows():
        if not _workflow_invokes(document, script_name):
            continue
        triggers = _workflow_triggers(document)
        if triggers & _PUSH_TRIGGER_KEYS:
            if _protects_target_branch(script_name):
                saw_push = True
            # else: declared as a push workflow, yet no invocation of this gate
            #       reaches a target branch -> contributes no protection.
        elif triggers & _SIDED_TRIGGER_KEYS:
            saw_sided = True
        else:
            # Invoked from a workflow with no recognisable trigger: treat it as
            # push-protected rather than inventing a new category, but never
            # let it *alone* excuse a scheduled-only gate.
            if _protects_target_branch(script_name):
                saw_push = True
    if saw_push:
        return PUSH_PROTECTED
    if saw_sided:
        return SCHEDULED_ONLY
    return UNWIRED


def _is_wired_into_ci(script_name: str) -> bool:
    """Is the script *invoked* by any workflow step at all?

    Deliberately the weaker primitive: it is the floor ("somebody runs it
    somewhere"), not the bar. The bar is :func:`_protects_target_branch`,
    which additionally proves the invoking job can run on a branch we work on.
    Keeping both means "invoked but unreachable" is its own visible state
    instead of being folded into either green or red.
    """
    return any(_workflow_invokes(doc, script_name) for doc in _load_workflows())


def _dotted(node: ast.AST) -> str:
    """Render ``sys.exit`` / ``main`` / ``builtins.SystemExit`` as a dotted name."""
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return "%s.%s" % (prefix, node.attr) if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_nonzero_int(node: ast.AST | None, constants: dict[str, int] | None = None) -> bool:
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return constants[node.id] != 0
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value != 0


def _module_constants(tree: ast.AST) -> dict[str, int]:
    """Module-level ``NAME = <int literal>`` bindings (``EXIT_FAIL = 1`` and friends).

    Needed because a gate that ends with ``return EXIT_FAIL`` is just as able to
    fail as one that ends with ``return 1`` -- a checker that only understood
    literals reported the GO gate as unable to fail, which is both wrong and
    corrosive: a meta-guard that cries wolf gets switched off.
    """
    out: dict[str, int] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)
            ):
                out[target.id] = value.value
    return out


def _name_bindings(scope: ast.AST) -> dict[str, list[ast.AST]]:
    """Every value bound to each simple name anywhere in ``scope``.

    ``try: rc = main() / except: rc = 2 / sys.exit(rc)`` is the idiomatic "run
    the gate, then propagate its status" shape. Without resolving the name the
    walk below concluded that such a script can never fail -- the same
    corrosive false positive this module exists to prevent.
    """
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
            value: ast.AST | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, []).append(value)
    return out


def _argument_can_be_nonzero(
    node: ast.AST | None,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """True for ``1``, ``EXIT_FAIL``, ``1 if bad else 0``, ``rc or 1``, ``rc``.

    Conditional expressions and boolean operators are how a gate turns a
    collected verdict into a status. Treating them as "unknown, therefore
    zero" was the root cause of four false positives: a script ending in
    ``return 0 if ok else 1`` -- the only construct in the file that can fail
    the build -- was reported as unable to fail.
    """
    if node is None:
        return False
    if _is_nonzero_int(node, constants):
        return True
    if isinstance(node, ast.IfExp):
        return (
            _argument_can_be_nonzero(node.body, constants, functions, bindings, seen)
            or _argument_can_be_nonzero(node.orelse, constants, functions, bindings, seen)
        )
    if isinstance(node, ast.BoolOp):
        # ``rc or 1``: any operand that can be non-zero can become the value.
        return any(
            _argument_can_be_nonzero(value, constants, functions, bindings, seen)
            for value in node.values
        )
    if isinstance(node, ast.Name) and bindings:
        if node.id in seen:
            return False  # cyclic rebinding: no new information
        inner = seen | {node.id}
        return any(
            _argument_can_be_nonzero(value, constants, functions, bindings, inner)
            or _call_returns_nonzero(value, constants, functions, bindings, inner)
            for value in bindings.get(node.id, ())
        )
    return False


def _call_returns_nonzero(
    node: ast.AST | None,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """True for ``main()`` when ``main`` has a non-zero return path.

    Follows the whole chain -- ``def main(): return run_checks()`` -- so a gate
    that delegates its verdict is no longer mistaken for one that cannot fail.
    ``seen`` guards against self- and mutual recursion.
    """
    if not isinstance(node, ast.Call) or not functions:
        return False
    name = _dotted(node.func)
    if name in seen:
        return False
    target = functions.get(name)
    if target is None:
        return False
    return _function_returns_nonzero(
        target, constants, functions, bindings, seen | {name}
    )


def _function_returns_nonzero(
    func: ast.AST,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        if _argument_can_be_nonzero(node.value, constants, functions, bindings, seen):
            return True
        if _call_returns_nonzero(node.value, constants, functions, bindings, seen):
            return True
    return False


def _tree_can_exit_nonzero(tree: ast.AST) -> bool:
    """The analysis proper: does ``tree`` hold a path to a non-zero status?"""
    functions = {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    constants = _module_constants(tree)
    bindings = _name_bindings(tree)

    def status_can_be_nonzero(node: ast.AST | None) -> bool:
        return _argument_can_be_nonzero(
            node, constants, functions, bindings
        ) or _call_returns_nonzero(node, constants, functions, bindings)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in _EXIT_CALLS:
            if status_can_be_nonzero(node.args[0] if node.args else None):
                return True
        elif isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call) and _dotted(exc.func) in _SYSTEM_EXIT_CALLS:
                if status_can_be_nonzero(exc.args[0] if exc.args else None):
                    return True
            elif isinstance(exc, ast.Call):
                return True  # raising any other exception aborts with a traceback
            else:
                return True
    return False


def _can_exit_nonzero(path: pathlib.Path) -> bool:
    """AST-accurate test for "this script has a path to a non-zero exit status.

    Regexes cannot tell a helper's ``return 2`` apart from a process exit status,
    which is exactly the mistake that produced a false positive in the first
    version of this module. Walking the tree and resolving ``sys.exit(main())``
    back to ``main()``'s own returns avoids it.
    """
    return _tree_can_exit_nonzero(ast.parse(_source(path)))


def test_guardrail_scripts_were_found() -> None:
    """Guards against this module silently passing on a wrong path."""
    names = [p.name for p in _all_verify_scripts()]
    assert "verify_c4_approval_channel.py" in names, names
    assert "verify_orm_vs_db.py" in names, names
    assert "verify_persistence.py" in names, names


#: Machine-readable counts kept in ``ci.yml`` comments, so the numbers a reader
#: sees and the numbers this module enforces cannot drift apart.
_CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
_VERIFY_SCRIPT_RE = re.compile(r"scripts/verify_[a-z0-9_]+\.py")


def _ci_job_verify_invocations(job_name: str) -> int:
    """How many verify_* invocations one ``ci.yml`` job actually contains.

    Derived from the PARSED YAML, counting every occurrence in every step's
    ``run:``. The first version of this number was obtained with
    ``sed -n '<line range>' | grep -c 'run: python scripts/verify_'`` and was
    wrong (39 vs the real 41): that only matches single-line ``run:`` steps and
    silently skips every multi-line ``run: |`` block. Counting text ranges is
    how a count rots; counting the structure is not.
    """
    for path, document in zip(_workflow_files(), _load_workflows()):
        if path.name != _CI_WORKFLOW.name or not isinstance(document, dict):
            continue
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            return 0
        job = jobs.get(job_name)
        if not isinstance(job, dict):
            return 0
        return sum(
            len(_VERIFY_SCRIPT_RE.findall(str(step.get("run", ""))))
            for step in (job.get("steps") or [])
            if isinstance(step, dict)
        )
    return 0


#: marker in ci.yml -> how the real value is derived
_DECLARED_COUNTS = {
    "GATE-COUNT-MARKER": lambda: len(_all_verify_scripts()),
    "GUARDRAILS-GATE-COUNT": lambda: _ci_job_verify_invocations("guardrails"),
    "GUARDRAILS-EXTENDED-GATE-COUNT": (
        lambda: _ci_job_verify_invocations("guardrails-extended")
    ),
}


def test_declared_gate_count_matches_reality() -> None:
    """Every gate count quoted in ``ci.yml`` must equal the real value.

    That comment said ``36`` while the directory held 61 and then 76 -- a number
    nobody re-derives is a number nobody can trust, and "we have N gates" is
    exactly the kind of claim a readiness review repeats. So each marker is
    parsed here and compared with a value derived from the filesystem or the
    parsed YAML: adding a gate without updating the count, or updating the count
    without adding the gate, turns CI red.

    Three markers are checked, not one: the total (files on disk) plus the two
    per-job totals. The per-job pair exists because a wrong per-job number is
    how a budget decision gets made on fiction -- "guardrails already carries N
    gates, so it cannot absorb one more" is an argument people act on.
    """
    assert _CI_WORKFLOW.is_file(), "missing %s" % _CI_WORKFLOW
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    for marker, derive in _DECLARED_COUNTS.items():
        declared = re.findall(r"%s:\s*(\d+)" % marker, text)
        assert len(declared) == 1, (
            "ci.yml must carry exactly one '%s: <n>' line, found %d -- two "
            "markers with the same name would let one rot unnoticed"
            % (marker, len(declared))
        )
        actual = derive()
        assert int(declared[0]) == actual, (
            "ci.yml declares %s = %s but the derived value is %d -- update the "
            "marker and the prose next to it, or wire/delete the gate that "
            "changed the count" % (marker, declared[0], actual)
        )


@pytest.mark.parametrize("script", _all_verify_scripts(), ids=lambda p: p.name)
def test_every_verify_script_bootstraps_sys_path(script: pathlib.Path) -> None:
    """``python scripts/x.py`` from the repo root must be able to import src."""
    assert _BOOTSTRAP_RE.search(_source(script)), (
        "%s does not bootstrap sys.path, so `import src...` fails when it is run "
        "directly from the repository root" % script.name
    )


@pytest.mark.parametrize("script", _gate_scripts(), ids=lambda p: p.name)
def test_every_gate_script_has_a_failure_exit(script: pathlib.Path) -> None:
    """A ``verify_*`` gate that cannot fail is worse than no gate at all."""
    assert _can_exit_nonzero(script), (
        "%s has no construct that can produce a non-zero exit status; it would "
        "report violations that CI can never notice" % script.name
    )


@pytest.mark.parametrize("script", _gate_scripts(), ids=lambda p: p.name)
def test_every_gate_script_is_wired_into_ci(script: pathlib.Path) -> None:
    """An unwired guardrail is decay: nobody runs it until someone remembers."""
    assert _workflow_files(), "no workflow files found under .github/workflows"
    assert _is_wired_into_ci(script.name), (
        "%s is never invoked by any workflow step, so it cannot protect anything; "
        "wire it into a job or list it in DIAGNOSTIC_REPORTERS with a reason. "
        "Note: a mention in a YAML comment or a step `name:` does not count -- "
        "only a command-bearing field (`run:`, or `with:` args/command/script/"
        "entrypoint) is an invocation" % script.name
    )


def test_allowlist_entries_exist_and_have_reasons() -> None:
    for name, reason in DIAGNOSTIC_REPORTERS.items():
        assert (SCRIPTS_DIR / name).is_file(), "allowlisted but missing: %s" % name
        assert reason.strip(), "allowlist entry %s has no reason" % name


# --------------------------------------------------------------------------- #
# Trigger classification: wired != protected
# --------------------------------------------------------------------------- #
def test_scheduled_only_gates_are_declared_with_reasons() -> None:
    """A gate that only runs on a timer must be a *declared* trade-off.

    Without this, ``_is_wired_into_ci`` scores a nightly-only gate exactly like
    a per-commit one, so a gate that runs zero times on the next push borrows
    the credibility of one that does. Declaring it keeps the trade-off visible.
    """
    existing = {p.name for p in _all_verify_scripts()}
    for name, reason in SCHEDULED_ONLY_GATES.items():
        assert name in existing, (
            "SCHEDULED_ONLY_GATES lists %s but no such verify_*.py exists -- "
            "remove the stale entry" % name
        )
        assert reason.strip(), (
            "scheduled-only gate %s has no reason; state what the trade-off is "
            "so the next reader does not have to re-derive it" % name
        )
        assert _classify_wiring(name) == SCHEDULED_ONLY, (
            "%s is registered as scheduled-only but is actually %s -- update the "
            "registration (or the workflow triggers) so the record matches reality"
            % (name, _classify_wiring(name))
        )


def test_no_gate_is_scheduled_only_without_a_declaration() -> None:
    """The anti-decay direction: a NEW scheduled-only gate must go red.

    This is the check that stops the category from quietly growing, which is how
    'wired' became 'looks wired' in the first place.
    """
    undeclared = [
        p.name for p in _gate_scripts()
        if _classify_wiring(p.name) == SCHEDULED_ONLY
        and p.name not in SCHEDULED_ONLY_GATES
    ]
    assert not undeclared, (
        "these gates run only on a schedule/dispatch, so they protect nothing on "
        "the next commit, but they are not declared in SCHEDULED_ONLY_GATES: %s "
        "-- add an entry with a reason, or wire them into a push/pull_request "
        "workflow" % undeclared
    )


def test_declared_scheduled_only_gates_are_not_counted_as_push_protected() -> None:
    """Belt and braces: the classification must not be a rubber stamp.

    If a listed gate ever becomes push-protected, test_scheduled_only_gates_
    are_declared_with_reasons already fails. This asserts the inverse directly:
    a declared gate is genuinely absent from the push-protected set.
    """
    push_protected = {
        p.name for p in _gate_scripts() if _classify_wiring(p.name) == PUSH_PROTECTED
    }
    for name in SCHEDULED_ONLY_GATES:
        assert name not in push_protected, (
            "%s is declared scheduled-only yet classifies as push-protected" % name
        )


def test_wiring_classification_covers_every_gate() -> None:
    """No gate may fall outside the three categories (guards future edits)."""
    allowed = {PUSH_PROTECTED, SCHEDULED_ONLY, UNWIRED}
    for script in _gate_scripts():
        assert _classify_wiring(script.name) in allowed, script.name


def test_gates_that_never_run_on_a_target_branch_are_recorded() -> None:
    """"Invoked" is not "protects": close the gap between the two.

    A gate can be named in a workflow step and still run zero times for our
    work -- when every job that invokes it sits in a workflow whose triggers
    exclude the target branch, or behind an ``if:`` that pins another branch.
    ``_is_wired_into_ci`` scores such a gate as wired, which is precisely the
    false assurance that let four ``ci.yml`` jobs stay dark.

    The concrete case today: ``verify_go_readiness.py`` is invoked only from
    ``ci-cd.yml:build``, and ``ci-cd.yml`` triggers on ``main``/``develop``
    only, so it has never run for work on p36.

    Escape hatches are the same as everywhere else in this module -- declare
    the trade-off (:data:`SCHEDULED_ONLY_GATES`) or record it in the exemption
    ledger with an owner and an unblock path. Silence is not one.
    """
    ledger = _load_ledger()
    unrecorded = [
        p.name
        for p in _gate_scripts()
        if _is_wired_into_ci(p.name)
        and not _protects_target_branch(p.name)
        and p.name not in SCHEDULED_ONLY_GATES
        and p.name not in ledger
    ]
    assert not unrecorded, (
        "these gates are named in a workflow but every job that invokes them is "
        "unreachable on %s, so they protect nothing while reading as wired: %s "
        "-- wire them into a job that actually runs, declare them in "
        "SCHEDULED_ONLY_GATES, or record them in %s with an owner"
        % (sorted(_target_branches()), unrecorded, EXEMPTION_LEDGER.name)
    )


def test_reachability_judgment_reads_triggers_and_if_pins() -> None:
    """Control: the two new layers must actually fire, not merely parse.

    Without this, a future edit could reduce ``_job_runs_on_target`` to a
    rubber stamp and the module would still be green -- the same way
    "the name appears in the file" was green while nothing ran.
    """
    unreachable = {
        "on": {"push": {"branches": ["main", "develop"]}},
        "jobs": {"build": {"if": "always()", "steps": [{"run": "python scripts/x.py"}]}},
    }
    assert not _job_runs_on_target(unreachable, unreachable["jobs"]["build"]), (
        "a workflow whose triggers exclude every target branch must not count "
        "as reachable"
    )

    pinned = {
        "on": {"push": {"branches": ["**"]}},
        "jobs": {
            "gate": {
                "if": "always() && github.ref == 'refs/heads/main'",
                "steps": [{"run": "python scripts/x.py"}],
            }
        },
    }
    assert not _job_runs_on_target(pinned, pinned["jobs"]["gate"]), (
        "a job pinning a branch that is not a target branch must not count as "
        "reachable"
    )

    reachable = {
        "on": {"push": {"branches": ["**"]}},
        "jobs": {"gate": {"if": "always()", "steps": [{"run": "python scripts/x.py"}]}},
    }
    assert _job_runs_on_target(reachable, reachable["jobs"]["gate"])

    dangling = {
        "on": {"push": {"branches": ["**"]}},
        "jobs": {
            "gate": {
                "needs": "does-not-exist",
                "if": "always()",
                "steps": [{"run": "python scripts/x.py"}],
            }
        },
    }
    assert not _job_runs_on_target(dangling, dangling["jobs"]["gate"]), (
        "a job whose `needs:` names a job the workflow does not define can "
        "never run"
    )


def test_verify_metrics_persist_is_classified_scheduled_only() -> None:
    """Pin the concrete case that motivated the category.

    Without this, someone could 'fix' a red by flipping the workflow to
    ``on: push`` and never learn that a real DB requirement was the reason.
    """
    assert _classify_wiring("verify_metrics_persist.py") == SCHEDULED_ONLY


def test_allowlist_entries_are_genuine_reporters() -> None:
    """A listed 'reporter' that gains a failure path must leave the allowlist."""
    offenders = [
        name for name in DIAGNOSTIC_REPORTERS
        if _can_exit_nonzero(SCRIPTS_DIR / name)
    ]
    assert not offenders, (
        "these files are allowlisted as report-only but can exit non-zero -- "
        "remove them from DIAGNOSTIC_REPORTERS so the gate checks apply: %s"
        % offenders
    )


def test_no_stale_allowlist_entries() -> None:
    existing = {p.name for p in _all_verify_scripts()}
    stale = sorted(set(DIAGNOSTIC_REPORTERS) - existing)
    assert not stale, "allowlist references scripts that no longer exist: %s" % stale


def _can_exit_nonzero_source(source: str) -> bool:
    """Run the analysis over inline source, so controls need no fixture file."""
    return _tree_can_exit_nonzero(ast.parse(textwrap.dedent(source)))


#: Gates that end in a conditional status. Four real scripts -- verify_deploy_readiness.py
#: (``return 1 if any_fail else 0``), verify_image_build.py (``return 1 if any_fail else
#: (2 if any_nv else 0)``), verify_p6_e2e_file_creation.py (``return 0 if ok else 1``)
#: and verify_real_execution_e2e.py (``sys.exit(rc)`` where ``rc`` comes from ``main()``)
#: -- were reported as unable to fail purely because of these shapes.
_CAN_FAIL_SHAPES = [
    pytest.param(
        "def main(ok):\n    return 0 if ok else 1\nsys.exit(main(ok))\n",
        id="return_zero_if_ok_else_one",
    ),
    pytest.param(
        "def main(bad):\n    return 1 if bad else 0\nsys.exit(main(bad))\n",
        id="return_one_if_bad_else_zero",
    ),
    pytest.param(
        "def main(a, b):\n    return 1 if a else (2 if b else 0)\nsys.exit(main(a, b))\n",
        id="return_nested_ifexp",
    ),
    pytest.param(
        "def main(rc):\n    return rc or 1\nsys.exit(main(rc))\n",
        id="return_boolop",
    ),
    pytest.param(
        "sys.exit(1 if bad else 0)\n",
        id="sys_exit_ifexp_argument",
    ),
    pytest.param(
        "def run_checks():\n    return 1\ndef main():\n    return run_checks()\nsys.exit(main())\n",
        id="transitive_return_chain",
    ),
    pytest.param(
        "def main():\n    return 1\ntry:\n    rc = main()\nexcept Exception:\n    rc = 2\n"
        "sys.exit(rc)\n",
        id="exit_through_local_variable",
    ),
    pytest.param(
        "def main():\n    return 1\nos._exit(main())\n",
        id="os_exit",
    ),
    pytest.param(
        "def inner():\n    raise SystemExit(1)\ndef main():\n    inner()\n    return 0\n"
        "sys.exit(main())\n",
        id="system_exit_in_nested_function",
    ),
]


@pytest.mark.parametrize("source", _CAN_FAIL_SHAPES)
def test_conditional_and_indirect_exit_paths_are_recognised(source: str) -> None:
    """These are all real failure exits; reporting them as unable to fail is a
    false positive, and a meta-guard that cries wolf gets switched off."""
    assert _can_exit_nonzero_source(source), "should be able to fail:\n%s" % source


#: Negative control. If any of these ever starts returning True the gate check has
#: become a rubber stamp and every ``verify_*`` script is effectively exempt.
_CANNOT_FAIL_SHAPES = [
    pytest.param(
        "def main():\n    return 0\nsys.exit(main())\n",
        id="main_returns_zero_literal",
    ),
    pytest.param(
        "def main(ok):\n    return 0 if ok else 0\nsys.exit(main(ok))\n",
        id="ifexp_zero_on_every_branch",
    ),
    pytest.param(
        "def main(ok):\n    return EXIT_OK\nEXIT_OK = 0\nsys.exit(main(ok))\n",
        id="named_zero_constant",
    ),
    pytest.param(
        "def main():\n    return 0\ntry:\n    rc = main()\nexcept Exception:\n    rc = 0\n"
        "sys.exit(rc)\n",
        id="local_variable_always_zero",
    ),
    pytest.param("print('diagnostic report only')\n", id="print_only"),
    pytest.param("sys.exit(0)\n", id="sys_exit_zero"),
    pytest.param("raise SystemExit(0)\n", id="system_exit_zero"),
    pytest.param("sys.exit()\n", id="sys_exit_no_argument"),
]


@pytest.mark.parametrize("source", _CANNOT_FAIL_SHAPES)
def test_scripts_that_cannot_fail_are_still_flagged(source: str) -> None:
    """Negative control: broadening the analysis must not make it vacuous."""
    assert not _can_exit_nonzero_source(source), "cannot fail, must stay flagged:\n%s" % source


def test_unparseable_workflow_fails_loudly(tmp_path: pathlib.Path) -> None:
    """A workflow this test cannot read must fail the test, not be skipped."""
    broken = tmp_path / "broken.yml"
    broken.write_text("jobs:\n  gates:\n    steps:\n      - run: [unclosed\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="broken.yml"):
        _load_workflows(tmp_path)


_WIRING_CONTROLS = [
    pytest.param(
        """
        jobs:
          gates:
            steps:
              # deliberately not wired: verify_deploy_readiness.py
              - name: run something else
                run: echo hi
        """,
        False,
        id="mentioned_only_in_a_yaml_comment",
    ),
    pytest.param(
        """
        jobs:
          gates:
            steps:
              - name: report verify_deploy_readiness.py status
                run: echo hi
        """,
        False,
        id="mentioned_only_in_a_step_name",
    ),
    pytest.param(
        """
        jobs:
          # verify_deploy_readiness.py is intentionally listed here
          gates:
            name: verify_deploy_readiness.py gate
            steps:
              - name: verify_deploy_readiness.py
                run: echo hi
        """,
        False,
        id="mentioned_only_in_comments_job_name_and_step_name",
    ),
    pytest.param(
        """
        jobs:
          gates:
            steps:
              - name: deploy readiness
                run: python scripts/verify_deploy_readiness.py
        """,
        True,
        id="invoked_from_run",
    ),
    pytest.param(
        """
        jobs:
          gates:
            steps:
              - name: deploy readiness
                run: |
                  set -e
                  python scripts/verify_deploy_readiness.py --strict
        """,
        True,
        id="invoked_from_multiline_run",
    ),
    pytest.param(
        """
        jobs:
          gates:
            steps:
              - name: deploy readiness
                uses: some/action@v1
                with:
                  script: scripts/verify_deploy_readiness.py
        """,
        True,
        id="invoked_from_with_script",
    ),
]


@pytest.mark.parametrize("source,expected", _WIRING_CONTROLS)
def test_wiring_is_read_from_commands_not_prose(source: str, expected: bool) -> None:
    """Negative control for the false green: a comment or a ``name:`` is not an
    invocation, and only ``run:`` / command-bearing ``with:`` entries are."""
    document = yaml.safe_load(textwrap.dedent(source))
    assert _workflow_invokes(document, "verify_deploy_readiness.py") is expected


def test_previously_misflagged_conditional_gates_can_fail() -> None:
    """Regression: these four were flagged as unable to fail while each has a
    reachable non-zero exit (see :data:`_CAN_FAIL_SHAPES`)."""
    for name in (
        "verify_deploy_readiness.py",
        "verify_image_build.py",
        "verify_p6_e2e_file_creation.py",
        "verify_real_execution_e2e.py",
    ):
        path = SCRIPTS_DIR / name
        assert path.is_file(), "expected gate script is gone: %s" % name
        assert _can_exit_nonzero(path), (
            "%s regressed: no non-zero exit path recognised" % name
        )


# --------------------------------------------------------------------------- #
# Exemption ledger: an honest record of gates this layer cannot verify green.
#
# This section does NOT make test_every_gate_script_is_wired_into_ci pass. Those
# four assertions stay red on purpose: the gates are real, and an unwired real
# gate protects nothing. What the ledger adds is (a) one visible place naming
# each red gate, its owner and its unblock path, and (b) checks that the ledger
# itself cannot rot or be used to launder a real gap into a fake green.
#
# The ledger lives in a Markdown file rather than here so it is reviewable as
# prose; :func:`_load_ledger` parses it, so the record and the test cannot drift.
# --------------------------------------------------------------------------- #

EXEMPTION_LEDGER = REPO_ROOT / "docs" / "ci" / "gate-exemptions.md"

#: Categories the ledger may use. ``pending-owner`` means "a human decision is
#: still open"; ``blocked-by-host`` means "the gate is correct but this runner
#: cannot produce its verdict". Anything else is rejected so a new category
#: cannot be invented to silence a gate.
_ALLOWED_EXEMPTION_CATEGORIES = {"pending-owner", "blocked-by-host"}


def _load_ledger() -> dict[str, dict[str, str]]:
    """Parse the exemption ledger into ``{script_name: {field: value}}``.

    Parsed from the Markdown on purpose: a hard-coded dict here would be a
    second copy that could disagree with the reviewed document.
    """
    assert EXEMPTION_LEDGER.is_file(), (
        "exemption ledger is missing: %s -- gates that cannot be verified green "
        "must be recorded in one visible place, not hidden in a workflow "
        "comment" % EXEMPTION_LEDGER
    )
    entries: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    last_key: str | None = None
    for line in EXEMPTION_LEDGER.read_text(encoding="utf-8").splitlines():
        heading = re.match(
            r"^###\s+\d+\.\s+`([^`]+)`\s+[-\u2013\u2014]+\s+`([^`]+)`", line
        )
        if heading:
            current = {"category": heading.group(2)}
            entries[heading.group(1)] = current
            last_key = None
            continue
        if current is None:
            continue
        # A field bullet starts with "- **Field:** value"; indented continuation
        # lines belong to the field above it. Markdown wraps long values, so a
        # single-line regex would capture only a fragment of the real value.
        # The field-name class must admit hyphens ("Re-run condition").
        field = re.match(
            r"^-\s+\*\*([A-Za-z][A-Za-z \-]*?):\*\*\s*(\S.*)$", line
        )
        if field:
            last_key = field.group(1).strip().lower()
            current.setdefault(last_key, field.group(2).strip())
            continue
        if last_key and re.match(r"^\s+\S", line):
            current[last_key] = (current[last_key] + " " + line.strip()).strip()
        else:
            last_key = None
    return entries


def test_exemption_ledger_is_wellformed() -> None:
    """Every ledger entry names an owner, a reason and an unblock condition."""
    entries = _load_ledger()
    assert entries, (
        "the exemption ledger parsed to zero entries -- if every gate is now "
        "wired, delete the ledger and this section; do not leave it empty"
    )
    for name, entry in entries.items():
        assert entry.get("category") in _ALLOWED_EXEMPTION_CATEGORIES, (
            "%s has category %r; allowed categories are %s -- a new category "
            "must not be invented to silence a gate"
            % (name, entry.get("category"), sorted(_ALLOWED_EXEMPTION_CATEGORIES))
        )
        assert (SCRIPTS_DIR / name).is_file(), (
            "ledger lists %s but no such verify_*.py exists -- remove the stale "
            "entry" % name
        )
        for required in ("owner", "why exempt", "re-run condition"):
            assert entry.get(required), (
                "%s is missing a '%s' in the ledger; an exemption with no owner "
                "or no unblock path is not an exemption, it is an abandonment"
                % (name, required)
            )


def test_gate_exemption_entries_are_still_needed() -> None:
    """Anti-rot: a gate that has become wired must leave the ledger.

    Without this the ledger would outlive its reason and quietly start
    describing gates that are in fact protected -- the exact decay this whole
    module exists to prevent, one level up.

    "Wired" here means *protecting a target branch*, not merely named in a
    command: a gate invoked only from an unreachable job is still exempt, so
    seeing its name in ci-cd.yml does not make its ledger entry stale.
    """
    stale = [
        name
        for name in _load_ledger()
        if _protects_target_branch(name)
    ]
    assert not stale, (
        "these ledger entries are now wired into a workflow, so the exemption "
        "no longer applies -- remove them from %s: %s"
        % (EXEMPTION_LEDGER.name, sorted(stale))
    )


def test_every_unwired_gate_is_accounted_for_in_the_ledger() -> None:
    """No gate may be silently unwired: wired, or in the ledger with a reason.

    This is the anti-silent-gap direction. It does not turn the red assertions
    green; it makes an *unrecorded* unwired gate impossible, so a new one cannot
    appear without someone writing down who owns it.
    """
    entries = _load_ledger()
    unaccounted = [
        p.name
        for p in _gate_scripts()
        if not _is_wired_into_ci(p.name) and p.name not in entries
    ]
    assert not unaccounted, (
        "these gates are neither wired nor recorded in %s, so they protect "
        "nothing and nobody owns them: %s" % (EXEMPTION_LEDGER.name, unaccounted)
    )


def test_pending_owner_exemptions_are_visible_to_ci() -> None:
    """Fail-closed: an open ``pending-owner`` entry must be loud in CI.

    A pending entry means a human decision is still outstanding. That must not
    be able to pass quietly: this test is designed to FAIL while any pending
    entry exists, so the ledger cannot become a place where work is shelved
    indefinitely. It turns green only when the owner wires the gate and the
    entry is deleted.
    """
    pending = sorted(
        name
        for name, entry in _load_ledger().items()
        if entry.get("category") == "pending-owner"
    )
    assert not pending, (
        "PENDING-OWNER gate exemptions are open -- these need an owner decision "
        "and must then be wired and removed from the ledger, not left to rot: %s"
        % pending
    )
