#!/usr/bin/env python3
"""Check env-var and dependency drift (B组第二轮对账脚本).

Usage:
    python scripts/check_env_drift.py [--strict]

Checks (all read-only, exit 0 = pass, exit 1 = drift found):
  1. Every ``VAR=`` in ``config/docker.env`` and ``.env.example`` is
     referenced somewhere real: ``src/`` (``os.environ`` / ``os.getenv`` /
     pydantic ``alias=`` / ``env_prefix``), ``docker-compose*.yml``,
     ``scripts/``, or the other env file. A variable that appears in
     neither code nor compose is drift: either dead config (delete it)
     or a renamed variable (fix the side that is wrong).
  2. Every name in ``pyproject.toml`` ``[project] dependencies`` is
     pinned in ``requirements.txt`` (``requirements.txt`` is the
     ``uv pip compile pyproject.toml`` default-deps lockfile, so
     transitive pins may exist without a pyproject declaration --
     that direction is NOT drift). Optional-dependency extras are
     intentionally NOT in ``requirements.txt``: they install via
     ``pip install -e .[extra]`` (see pyproject comments); the script
     only reports them as info, never as failure.
  3. ``requires-python`` floor matches ``.python-version`` and the
     Docker/CI Python versions (3.12, single version, no matrix leg).

Known intentional exceptions (documented, not drift):
  - ``LHX_*``: read by ``LiuHao-O/packages/kernel/config.py``
    (``env_prefix="LHX_"`` + per-field ``alias=``), NOT by ``src/``.
  - ``*_DSN``/``*_URL`` values that only appear as compose ``${VAR}``
    interpolation are counted as referenced via compose.
  - ``SECRET_KEY`` / ``JWT_SECRET_KEY`` etc. are read through alias
    chains; the script matches the literal name in any ``.py`` file,
    which covers the alias lists in ``src/security/jwt_handler.py``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Where a variable name may legitimately be referenced.
CODE_GLOBS = ["src/**/*.py", "scripts/**/*.py", "main.py",
              "LiuHao-O/packages/**/*.py", "libs/**/*.py"]
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))
ENV_FILES = [REPO / "config" / "docker.env", REPO / ".env.example"]

VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
# os.environ["X"] / os.getenv("X") / alias="X" / 'X' in env-prefix contexts.
# A plain literal occurrence of the name in a .py file counts as a reference;
# the check is deliberately coarse (a rename that leaves a stale literal
# behind is caught by the compose/code cross-check in CI review, not here).


def _env_vars(path: Path) -> list[str]:
    out = []
    for m in VAR_RE.finditer(path.read_text(encoding="utf-8")):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _collect_code_text() -> str:
    parts = []
    for pat in CODE_GLOBS:
        for p in REPO.glob(pat):
            if ".venv" in p.parts or "node_modules" in p.parts:
                continue
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(parts)


def _collect_compose_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in COMPOSE_FILES)


def _norm(name: str) -> str:
    return re.split(r"[<>=!~\s;\[]", name, maxsplit=1)[0].strip().lower().replace("_", "-")


def check_env_vars() -> list[str]:
    code = _collect_code_text()
    compose = _collect_compose_text()
    problems = []
    for env_file in ENV_FILES:
        if not env_file.exists():
            problems.append(f"{env_file}: missing file")
            continue
        other = (REPO / ".env.example" if env_file.name == "docker.env"
                 else REPO / "config" / "docker.env")
        other_text = other.read_text(encoding="utf-8", errors="ignore") if other.exists() else ""
        for var in _env_vars(env_file):
            in_code = re.search(rf"\b{re.escape(var)}\b", code) is not None
            in_compose = (f"${{{var}" in compose or f"${var}" in compose
                          or re.search(rf"^\s*-?\s*{re.escape(var)}[:=]", compose, re.M) is not None)
            in_other_env = re.search(rf"^{re.escape(var)}\s*=", other_text, re.M) is not None
            if not (in_code or in_compose or in_other_env):
                problems.append(
                    f"{env_file.name}: {var} is not referenced by code, "
                    f"compose, or the other env file (dead config or renamed var)"
                )
    return problems


def check_requirements() -> list[str]:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {_norm(d) for d in pyproject["project"]["dependencies"]}
    req_text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    pinned = set()
    for line in req_text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        pinned.add(_norm(line))
    missing = sorted(declared - pinned)
    problems = [f"requirements.txt: pyproject dependency not pinned: {m}" for m in missing]
    # Info only: extras intentionally live outside requirements.txt.
    for extra, lst in pyproject["project"].get("optional-dependencies", {}).items():
        extra_missing = sorted({_norm(d) for d in lst} - pinned)
        if extra_missing:
            print(f"  [info] extra [{extra}] not in requirements.txt "
                  f"(by design, install via `pip install -e .[{extra}]`): "
                  f"{', '.join(extra_missing)}")
    return problems


def check_python_version() -> list[str]:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    requires = pyproject["project"].get("requires-python", "")
    pinned = (REPO / ".python-version").read_text(encoding="utf-8").strip() if (REPO / ".python-version").exists() else ""
    problems = []
    # 下界必须恰好等于 .python-version（单版本策略），不是“包含即可”：
    # "3.12" in ">=3.11,<3.13" 这种子串判断会把 3.11 的下界误判为通过。
    floor = re.search(r">=\s*([0-9]+\.[0-9]+)", requires)
    if pinned and (not floor or floor.group(1) != pinned):
        problems.append(f"requires-python floor is {floor.group(1) if floor else requires!r}, "
                        f"expected >={pinned} (must equal .python-version {pinned!r})")
    for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        text = wf.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"python-version:\s*['\"]?([0-9.]+)['\"]?", text):
            if m.group(1) != pinned:
                problems.append(f"{wf.name}: python-version {m.group(1)} != .python-version {pinned}")
        for m in re.finditer(r"python-version:\s*\[(.*?)\]", text, re.S):
            legs = re.findall(r"['\"]([0-9.]+)['\"]", m.group(1))
            if legs != [pinned]:
                problems.append(f"{wf.name}: python matrix {legs} != [{pinned}]")
    dockerfile = REPO / "Dockerfile"
    if dockerfile.exists():
        for i, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"FROM python:([0-9.]+)", line)
            if m and m.group(1) != pinned:
                problems.append(f"Dockerfile:{i}: python:{m.group(1)} != .python-version {pinned}")
    return problems


def main() -> int:
    problems: list[str] = []
    print("== env vars (config/docker.env, .env.example) ==")
    problems += check_env_vars()
    print("== requirements.txt vs pyproject [project] dependencies ==")
    problems += check_requirements()
    print("== python version pin (pyproject / .python-version / CI / Dockerfile) ==")
    problems += check_python_version()
    if problems:
        print(f"\nDRIFT: {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK: no drift found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
