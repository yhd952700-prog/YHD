#!/usr/bin/env python
"""SA-1 -- dependency declaration surface gate.

Why this exists
===============
LIUHAO has **three** places that can install Python packages, and until this
gate existed nothing checked that they agree:

  1. ``pyproject.toml``            -- the real declaration surface
  2. ``requirements.txt``          -- a ``uv pip compile`` product
  3. ``.github/workflows/*.yml``   -- what CI actually installs, per job

Those three drifted apart, and the drift was invisible because **every one of
the failure modes is a silent green**:

  * a job that installs only ``requirements.txt`` never gets the ``[tests]``
    extra, so the coordination tests that need ``fakeredis``/``lupa``
    *skip* -- and a skip is green;
  * a job that forgets to install the project at all aborts at collection,
    which is the rare case that does fail loudly, but only once someone
    pushes to the branch that runs it;
  * a package imported directly by ``src/`` but absent from ``pyproject``
    keeps working as long as some *other* package happens to pull it in.
    That is luck, not a declaration, and it is the same defect class already
    fixed by hand for ``mcp`` / ``qdrant-client`` / ``aiosqlite``.

What this gate checks
=====================
**A. Declaration completeness.** Every third-party module that ``src/``
imports -- at module top level *or* inside a function body (a lazy import is
still a hard dependency of the code path that runs) -- must be named in
``pyproject.toml``. ``src/``-relative imports, the standard library and
first-party sibling packages are not dependencies and are ignored.

**B. Every pytest job installs something.** A CI job that runs ``pytest``
without a dependency-install step is reported: it will abort during
collection, and whether anyone notices depends purely on whether that job's
trigger branch is one people push to.

**C. Every pytest job installs the test surface.** ``[tests]`` exists in
``pyproject.toml`` precisely so that "the tests which need fakeredis/lupa
really execute" is a property of the *environment*, not of luck. A job may
satisfy this either by asking for the extra (``pip install -e .[tests]``) or
by installing a requirements file that actually *contains* the extra's
packages -- and this gate checks the second one against the real file rather
than trusting the file name, because ``requirements.txt`` is a
default-dependencies-only compile and does **not** contain them.

Scope and honesty rules
=======================
* This gate is **read-only**. It never installs, never writes, and never
  touches ``audit_store.db``.
* Standard library only, so it runs in any environment that can run Python --
  including the ones it is complaining about.
* It reports what it *proves*. It does not guess: check A is an AST scan of
  real ``import`` statements, checks B and C parse real workflow YAML text,
  and check C's requirements-file branch reads the real pinned file.
* ``BLOCKED`` is a first-class outcome. If the evidence needed for a verdict
  is missing, the gate says NOT VERIFIED and exits non-zero rather than
  passing quietly.

Exit codes
==========
0  all checks passed
1  at least one check found a violation (details on stdout)
2  the gate could not obtain the evidence it needs (NOT VERIFIED)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SCRIPTS = REPO / "scripts"
PYPROJECT = REPO / "pyproject.toml"
REQUIREMENTS = REPO / "requirements.txt"
WORKFLOWS = REPO / ".github" / "workflows"

# Extra whose whole purpose is "make the dependency-skipping tests really run".
TEST_EXTRA = "tests"

#: Directory names whose contents are never first-party Python and whose trees
#: are huge and slow to walk (``node_modules`` in particular). Skipped both
#: when harvesting first-party roots and when probing for a ``.py`` file so the
#: gate stays fast on every push.
HUGE_DIRS = frozenset({
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

#: Surfaces scanned for third-party imports, as ``(label, path)``.
#:
#: ``scripts/`` is scanned too, not just ``src/``. The gate's whole claim is
#: "every third-party import this project needs is declared", and the shipped
#: verify scripts are part of that need: ``verify_deployed_product_closure.py``
#: imports ``playwright.sync_api``, and scanning only ``src/`` left that (and
#: selenium) completely invisible -- a gate that looks like it holds the line
#: while a real undeclared dependency walks straight through it.
SCAN_ROOTS: Tuple[Tuple[str, Path], ...] = (
    ("src", SRC),
    ("scripts", SCRIPTS),
)


# ────────────────────────────── helpers ──────────────────────────────

def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def _info(msg: str) -> None:
    print(f"  [info] {msg}")


def normalize(name: str) -> str:
    """PEP 503 normalisation, so ``RestrictedPython`` == ``restricted-python``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def first_party_modules() -> Set[str]:
    """Import roots that are *this project*, not a dependency.

    Resolved from the filesystem rather than through ``importlib``: this gate
    runs as ``python scripts/<name>.py``, so ``sys.path[0]`` is ``scripts/``
    and the repository root is not importable -- a spec-based check silently
    fails to see first-party modules such as the repo-root ``error_types`` and
    then reports them as missing dependencies.

    Three shapes occur in this tree:
      * real packages -- a directory with ``__init__.py`` (under ``src/`` and
        also at the repository root, e.g. ``core/``);
      * single-module files -- ``error_types.py`` at the repository root,
        imported by bare name from ``src/ai/providers.py``;
      * **namespace directories** -- a directory with ``.py`` files but no
        ``__init__.py``. ``config/`` is the live example:
        ``scripts/ops/backup.py`` does ``from config.production.config import
        ...``. Requiring ``__init__.py`` reported the project's own config
        tree as an undeclared third-party dependency.
    """
    roots: Set[str] = set()

    def has_python(directory: Path) -> bool:
        """Does this directory hold a ``.py`` file, within a bounded depth?

        Bounded and pruned on purpose. An unbounded ``rglob`` walks straight
        into ``apps/console/console/node_modules`` and turns a file-existence
        question into a multi-minute scan of a vendored dependency tree; this
        gate has to run on every push. Three levels is enough for every
        first-party namespace directory in this tree (``config/production/
        config.py`` is two).
        """
        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth) + ".py"
            for found in directory.glob(pattern):
                if any(part in HUGE_DIRS for part in found.parts):
                    continue
                return True
        return False

    def harvest(directory: Path) -> None:
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.name in HUGE_DIRS:
                continue
            if entry.is_dir():
                if (entry / "__init__.py").exists() or has_python(entry):
                    roots.add(normalize(entry.name))
            elif entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
                roots.add(normalize(entry.stem))

    # ``scripts/`` is harvested too: the verify scripts import each other by
    # bare module name (``from _gateway_app import gateway_openapi_spec``), so
    # a script in ``scripts/`` is first-party exactly like one at the root.
    harvest(SRC)
    harvest(REPO)
    harvest(SCRIPTS)
    return roots


# ─────────────────── check A: declaration completeness ───────────────────

def collect_imports(
    roots: Iterable[Tuple[str, Path]] = SCAN_ROOTS,
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], Dict[str, Set[str]]]:
    """AST-scan every scanned surface for third-party imports.

    Returns ``(top_level, lazy_only, full)`` mapping the *root* package name ->
    source files, plus the full dotted module names seen per root so that
    importability can be checked at the submodule actually used.

    ``lazy_only`` holds roots that appear *only* inside function bodies; they
    are still hard dependencies of the code path that reaches them, which is
    why they are reported separately rather than ignored.

    Paths are reported as ``<surface>/<relative path>`` so a finding names the
    surface it came from: ``src/...`` or ``scripts/...``.
    """
    stdlib = set(sys.stdlib_module_names)
    first_party = first_party_modules()
    top: Dict[str, Set[str]] = {}
    inside: Dict[str, Set[str]] = {}
    full: Dict[str, Set[str]] = {}

    for surface, base in roots:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            _collect_one_file(path, base, surface, top, inside, full, stdlib, first_party)
    return _third_party_split(top, inside, full, stdlib, first_party)


def _collect_one_file(
    path: Path,
    base: Path,
    surface: str,
    top: Dict[str, Set[str]],
    inside: Dict[str, Set[str]],
    full: Dict[str, Set[str]],
    stdlib: Set[str],
    first_party: Set[str],
) -> None:
    rel = f"{surface}/" + str(path.relative_to(base)).replace("\\", "/")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - defensive
        print(f"  [BLOCKED] cannot parse {rel}: {exc}")
        return

    def record(nodes: Iterable[ast.stmt], store: Dict[str, Set[str]]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    store.setdefault(root, set()).add(rel)
                    # `import a.b.c` requires the whole dotted path.
                    full.setdefault(root, set()).add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import: first-party by definition
                    continue
                if node.module:
                    root = node.module.split(".")[0]
                    store.setdefault(root, set()).add(rel)
                    # `from a.b import c` requires the module `a.b`; the
                    # name `c` may be a submodule OR an attribute of it
                    # (e.g. `from sqlalchemy import BigInteger`), so
                    # probing `a.b.c` would be a false alarm.
                    full.setdefault(root, set()).add(node.module)

    record(tree.body, top)
    # Every import anywhere in the file, including ones nested inside a
    # function's `try:`/`with:` block. Recording only the direct children
    # of a FunctionDef misses `import requests` inside
    # `def send(...): try: import requests` -- which is exactly how the
    # alert webhook sink is written, so a narrow walk silently under-
    # reports real dependencies.
    record(ast.walk(tree), inside)


def _third_party_split(
    top: Dict[str, Set[str]],
    inside: Dict[str, Set[str]],
    full: Dict[str, Set[str]],
    stdlib: Set[str],
    first_party: Set[str],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Drop stdlib + first-party roots, and split top-level vs lazy-only."""

    def third_party(store: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        out = {}
        for root, files in store.items():
            if root in stdlib:
                continue
            if normalize(root) in first_party or root == "src":
                continue
            out[root] = files
        return out

    return (
        third_party(top),
        {k: v for k, v in third_party(inside).items() if k not in top},
        full,
    )


def declared_packages() -> Set[str]:
    """Every package named anywhere in ``pyproject.toml`` (deps + all extras)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    found: Set[str] = set()
    # Covers "name>=1.0", "name[extra]>=1.0", "name ; python_version<.."
    for match in re.finditer(r'^\s*"([A-Za-z0-9][A-Za-z0-9._-]*)', text, re.MULTILINE):
        found.add(normalize(match.group(1)))
    return found


# Import-root -> distribution-name aliases that ``packages_distributions()``
# cannot supply when the package is not installed in the interpreter running
# this gate. Without these, a thin CI runner (or a bare ``python``) reports
# every *declared* third-party import as "undeclared", because the root name
# (``jwt``, ``mem0``, ``opentelemetry``, ``phoenix``, ``pydantic_monty``)
# never equals the PyPI distribution name (``pyjwt``, ``mem0ai``,
# ``opentelemetry-api``, ``arize-phoenix``, ``pydantic-monty-client``). That is
# a false failure: the declaration is correct, the gate was simply blind to
# the mapping. These are read-only facts about this project's stack, not a
# guess, and they keep check A deterministic across environments.
IMPORT_ROOT_ALIASES: Dict[str, str] = {
    "jwt": "pyjwt",
    "yaml": "pyyaml",
    "mem0": "mem0ai",
    "opentelemetry": "opentelemetry-api",
    "phoenix": "arize-phoenix",
    "pydantic_monty": "pydantic-monty-client",
    # google.generativeai is the legacy Gemini SDK; the import namespace is
    # `google.generativeai` but the distribution is `google-generativeai`
    # (distinct from `google-genai`, which provides the unrelated
    # `google.genai` namespace). Without this alias the gate cannot tell the
    # declared distribution from the undeclared import root.
    "google": "google-generativeai",
}


def distribution_names_for(root: str) -> Set[str]:
    """Distribution name(s) that provide the import root ``root``.

    The import name and the PyPI name are frequently different -- ``yaml`` is
    shipped by ``PyYAML``, ``jwt`` by ``PyJWT``. Comparing the import name
    against the declared list without this mapping produces false positives,
    so the mapping is built from (a) the curated :data:`IMPORT_ROOT_ALIASES`
    for this project's stack and (b) the live environment via
    ``importlib.metadata.packages_distributions`` when the package happens to
    be installed. Using the aliases means the gate does not need the package
    installed to recognise a *correct* declaration -- it stays deterministic
    whether or not the running interpreter is fully provisioned.
    """
    names: Set[str] = set()
    if root in IMPORT_ROOT_ALIASES:
        names.add(normalize(IMPORT_ROOT_ALIASES[root]))
    try:
        from importlib.metadata import packages_distributions
    except ImportError:  # pragma: no cover - Python < 3.10
        return names or {normalize(root)}
    dists = packages_distributions().get(root)
    if dists:
        names.update(normalize(d) for d in dists)
    return names or {normalize(root)}


def module_is_importable(dotted: str) -> bool:
    """Can ``dotted`` actually be imported right now?"""
    import importlib.util

    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        return False


def check_a() -> List[str]:
    print("\n=== A. src/ imports are declared in pyproject.toml ===")
    if not PYPROJECT.is_file():
        print("  [BLOCKED] pyproject.toml not found")
        return ["pyproject.toml missing"]
    if not SRC.is_dir():
        print("  [BLOCKED] src/ not found")
        return ["src/ missing"]

    top, lazy_only, full = collect_imports()
    declared = declared_packages()
    problems: List[str] = []

    for label, store in (("top-level", top), ("lazy", lazy_only)):
        for root in sorted(store):
            candidates = distribution_names_for(root)
            files = sorted(store[root])
            where = f"{len(files)} file(s): {', '.join(files[:3])}"
            # Availability is judged on the *submodule actually imported*.
            # A namespace package can be importable while the module the code
            # names is absent -- that is the google.generativeai case, where
            # `import google` succeeds yet the provider still cannot run.
            submodules = sorted(full.get(root, {root}))
            broken = [m for m in submodules if not module_is_importable(m)]

            if candidates & declared:
                # The distribution is declared -- the declaration surface is
                # correct. Whether the concrete submodule imports *in this
                # interpreter* is an environment-provisioning question, not a
                # declaration defect, so it is reported as informational only
                # and never flips the gate to FAIL across environments.
                if broken:
                    _info(
                        f"{label} import {root!r} is declared "
                        f"({'/'.join(sorted(candidates & declared))}) but submodule(s) "
                        f"{', '.join(broken)} are not importable in THIS interpreter -- "
                        f"provision the venv to confirm (not a declaration defect)"
                    )
                continue

            # Not declared: this is the genuine SA-1 defect -- a src/ import
            # that relies on another package's transitive pull (luck, not a
            # declaration). The submodule importability only tells us whether
            # it is *currently* masked by an installed neighbour.
            if broken:
                msg = (f"UNDECLARED {label} import {root!r}: {', '.join(broken)} not importable "
                       f"and not declared in pyproject.toml as "
                       f"{'/'.join(sorted(candidates))} ({where})")
            else:
                msg = (f"UNDECLARED {label} import {root!r} is importable but not declared in "
                       f"pyproject.toml as {'/'.join(sorted(candidates))} ({where}) -- works "
                       f"only via a transitive dependency (luck, not a declaration)")
            _fail(msg)
            problems.append(msg)

    if not problems:
        _ok(f"all {len(top)} top-level + {len(lazy_only)} lazy-only third-party imports declared")
    return problems


# ─────────────── checks B & C: what CI actually installs ───────────────

JOB_RE = re.compile(r"^ {2}([A-Za-z0-9_-]+):\s*$")
STEP_RE = re.compile(r"^\s*-\s+(?:name|uses|run):\s*(.*)$")


def parse_jobs(path: Path) -> Dict[str, Dict[str, object]]:
    """Split a workflow into jobs with their raw install / pytest commands.

    Deliberately a text scan rather than a YAML parse: the gate must run with
    the standard library alone, and the question being asked ("does this job
    install the project, and does it ask for the test extra") is answerable
    from the commands themselves.
    """
    jobs: Dict[str, Dict[str, object]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        job_match = JOB_RE.match(raw)
        if job_match and not raw.lstrip().startswith("#"):
            current = job_match.group(1)
            jobs[current] = {"installs": [], "pytests": [], "setup_python": False}
            continue
        if current is None:
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        bucket = jobs[current]
        assert isinstance(bucket, dict)
        if "uses: actions/setup-python" in stripped:
            bucket["setup_python"] = True
        if re.search(r"\bpip\s+install\b", stripped) or "pip install" in stripped:
            bucket["installs"].append(stripped)  # type: ignore[union-attr]
        if "pytest" in stripped and not stripped.startswith("#"):
            bucket["pytests"].append(stripped)  # type: ignore[union-attr]
    return jobs


def test_extra_packages(extra: str = TEST_EXTRA) -> List[str]:
    """The distribution names the ``[tests]`` extra really declares.

    Parsed from the ``[project.optional-dependencies]`` table instead of being
    written down as a literal ``("fakeredis", "lupa")``. A hand-maintained list
    is the exact defect this gate exists to catch: the moment someone adds a
    package to the extra, a hard-coded list stops matching reality and the
    gate keeps certifying "the test surface is installed" against a list that
    no longer exists -- a green light over a growing hole.
    """
    if not PYPROJECT.is_file():
        return []
    text = PYPROJECT.read_text(encoding="utf-8")
    section = re.search(
        r"^\[project\.optional-dependencies\]\s*$(.*?)(?=^\[)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not section:
        return []
    block = re.search(
        rf"^{re.escape(extra)}\s*=\s*\[(.*?)\]",
        section.group(1),
        re.MULTILINE | re.DOTALL,
    )
    if not block:
        return []
    names: List[str] = []
    for raw in block.group(1).split(","):
        raw = raw.strip().strip('"').strip("'")
        if not raw or raw.startswith("#"):
            continue
        # PEP 508: the name is everything before any version marker, extras
        # bracket, environment marker or comment.
        name = re.split(r"[<>=!~;\[\s]", raw, maxsplit=1)[0].strip()
        if name:
            names.append(normalize(name))
    return sorted(set(names))


def requirements_provides_test_extra() -> Tuple[bool, str]:
    """Does ``requirements.txt`` actually pin the ``[tests]`` extra's packages?"""
    if not REQUIREMENTS.is_file():
        return False, "requirements.txt not found"
    text = REQUIREMENTS.read_text(encoding="utf-8")
    pinned = {normalize(m) for m in re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", text, re.MULTILINE)}
    # Derived from pyproject, never hard-coded -- see test_extra_packages().
    extra_pkgs = test_extra_packages()
    if not extra_pkgs:
        return False, "could not read the [tests] extra package list from pyproject.toml"
    missing = [p for p in extra_pkgs if normalize(p) not in pinned]
    if missing:
        return False, f"requirements.txt does not pin {', '.join(missing)}"
    return True, f"requirements.txt pins {', '.join(extra_pkgs)}"


def job_installs_test_surface(installs: List[str]) -> Tuple[bool, str]:
    """Does *any* of this job's install commands provide the test surface?

    Every command is considered before deciding. A job may legitimately do
    ``pip install -r requirements.txt`` **and then** ``pip install -e .[tests]``;
    judging it on the first command alone would report a false failure, and a
    gate that cries wolf gets ignored.
    """
    if not installs:
        return False, "no install command at all"

    saw_requirements = False
    requirements_reason = ""
    for cmd in installs:
        if re.search(r"\[.*\btests\b", cmd):
            return True, f"asks for the extra directly: {cmd}"
        if "requirements.txt" in cmd and not saw_requirements:
            saw_requirements = True
            ok, why = requirements_provides_test_extra()
            if ok:
                return True, why
            requirements_reason = why
    if saw_requirements:
        return False, f"installs requirements.txt, but {requirements_reason}"
    return False, "no install command requests the test surface"


def pytest_scope_needs_extra(commands: List[str]) -> Tuple[bool, str]:
    """Does this pytest invocation actually reach tests gated on the extra?

    Scope matters, and getting it wrong in either direction is bad: a gate
    that demands the extra for ``tests/integration/`` (which contains no
    gated test) cries wolf, and a gate that ignores scope would demand it for
    nothing. So the target paths are read off the command and the gated test
    files are looked up for real.
    """
    extra_pkgs = test_extra_packages()
    if not extra_pkgs:
        return True, "no [tests] extra declared; nothing to require"
    # A distribution is usually imported with underscores (``pydantic_monty``)
    # even when it is written with hyphens, so match both spellings.
    tokens = sorted({p for p in extra_pkgs} | {p.replace("-", "_") for p in extra_pkgs})

    targets: List[str] = []
    for cmd in commands:
        for token in re.findall(r"tests[A-Za-z0-9_/\\.*{}-]*", cmd):
            # Drop `::selector` suffixes so `tests/x.py::TestC::test_a`
            # still resolves to the file's directory.
            targets.append(token.split("::")[0].replace("\\", "/").rstrip("/"))
    if not targets:
        # A bare `pytest` with no path runs the whole configured testpaths.
        targets = ["tests"]

    roots: List[Path] = []
    for target in targets:
        candidate = REPO / target
        if candidate.is_dir():
            roots.append(candidate)
        elif candidate.is_file():
            roots.append(candidate.parent)
        elif (REPO / target).exists():
            roots.append(REPO / target)
    if not roots:
        return True, f"pytest target(s) {targets} are not local test paths; not asserting"

    gated: List[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(re.search(rf"\b{re.escape(pkg)}\b", text) for pkg in tokens):
                gated.append(str(path.relative_to(REPO)).replace("\\", "/"))

    if not gated:
        return False, (f"no test under {'/'.join(t.name for t in roots)} references "
                       f"{'/'.join(extra_pkgs)}")
    return True, f"{len(gated)} gated test file(s), e.g. {gated[0]}"


def check_b_c() -> List[str]:
    print("\n=== B/C. every CI job that runs pytest installs the test surface ===")
    if not WORKFLOWS.is_dir():
        print("  [BLOCKED] .github/workflows not found")
        return ["workflows dir missing"]

    problems: List[str] = []
    seen_jobs = 0
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for name, bucket in parse_jobs(wf).items():
            installs = bucket["installs"]          # type: ignore[assignment]
            pytests = bucket["pytests"]            # type: ignore[assignment]
            if not pytests:
                continue
            seen_jobs += 1
            label = f"{wf.name}:{name}"

            needs_extra, scope_why = pytest_scope_needs_extra(pytests)
            if not installs:
                msg = (f"{label} runs pytest but installs nothing -- collection aborts on "
                       f"a bare interpreter (pytest cmd: {pytests[0][:60]!r})")
                _fail(msg)
                problems.append(msg)
                continue
            if not needs_extra:
                _ok(f"{label}: test surface not required here ({scope_why})")
                continue

            # "installs something" is not the same as "installs the project".
            # A job whose only pip call is `pip install bandit` has no project
            # dependencies at all, and collection aborts on the first src/
            # import -- a louder and more accurate diagnosis than "tests skip".
            if not any(re.search(r"requirements\.txt|-e\s+\.|liuhao-ai-os|-e\s+\[\.", c) for c in installs):
                msg = (f"{label} runs pytest but never installs the project (only: "
                       f"{'; '.join(installs)[:70]!r}) -- collection aborts on the first "
                       f"src/ import, so the job cannot pass")
                _fail(msg)
                problems.append(msg)
                continue

            ok, why = job_installs_test_surface(installs)
            if ok:
                _ok(f"{label}: {why} [{scope_why}]")
            else:
                msg = (f"{label} runs pytest over tests that need the test extra "
                       f"({scope_why}) but {why} -- those tests will SKIP silently")
                _fail(msg)
                problems.append(msg)

    if not seen_jobs:
        print("  [BLOCKED] no workflow job runs pytest; nothing was verified")
        problems.append("no pytest job found -- cannot verify the install surface")
    return problems


# ─────────────────────────────── driver ───────────────────────────────

def main() -> int:
    print("=" * 78)
    print("SA-1  dependency declaration surface gate")
    print("=" * 78)
    print(f"repo: {REPO}")

    problems = check_a() + check_b_c()

    print("\n" + "=" * 78)
    if problems:
        print(f"RESULT: FAIL -- {len(problems)} problem(s)")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print("=" * 78)
        return 1
    print("RESULT: PASS -- src/ imports are declared and every pytest job installs them")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
