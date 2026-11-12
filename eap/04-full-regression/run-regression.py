#!/usr/bin/env python
"""
EAP-04 — Full-regression runner (standard library only).

Purpose
-------
Run the repository pytest suite and emit a structured, machine-readable report
that the EAP final readiness battery (`05-final-readiness-battery`) consumes.

This runner deliberately does NOT wrap or re-implement pytest. It *drives* it,
one top-level `tests/` directory at a time, and reads each chunk's JUnit XML so
the numbers come from pytest's own accounting rather than from scraping human
console text.

Why chunking
------------
Two hard-won facts from this repository:
  1. The suite is large (~3300 tests) and takes several hundred seconds whole.
  2. CONCURRENT pytest runs in this repo corrupt results. Earlier team members
     explicitly avoided running the suite while another process was doing so.
Chunking keeps each invocation bounded and isolates a single bad chunk instead
of losing the whole run. Use `--whole` only if you know no other pytest is
concurrently active.

Honesty / fail-closed contract
------------------------------
  exit 0 = PASS          every executed test passed
  exit 1 = FAIL          a real failure/error occurred, OR a chunk collected
                         zero tests (a vanished chunk is never silently green)
  exit 2 = NOT VERIFIED  pytest cannot even be invoked here (environment gap)

A missing/unrunnable interpreter is reported as NOT VERIFIED, never as PASS.
Absent execution must never be flipped to a pass downstream; see
`05-final-readiness-battery/consolidate_readiness.py`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Statuses pytest/JUnit uses that we treat as a real product failure.
FAILURE_TAGS = {"failure", "error"}
# Statuses that mean "the check did not actually happen here".
INCONCLUSIVE_TAGS = {"skipped"}

REPORT_VERSION = 1


def _default_tests_root(repo_root: Path) -> Path:
    return repo_root / "tests"


def discover_chunks(tests_root: Path) -> list[tuple[str, str]]:
    """Return [(chunk_name, path_as_str)] — one entry per top-level test area.

    Directories become their own chunk. Loose top-level `test_*.py` files are
    grouped into a single synthetic "root" chunk so nothing is silently left
    un-run (that would be a silent green).
    """
    if not tests_root.is_dir():
        return []

    chunks: list[tuple[str, str]] = []
    loose_files: list[Path] = []

    for entry in sorted(tests_root.iterdir()):
        if entry.is_dir():
            if entry.name.startswith(".") or entry.name in {"__pycache__"}:
                continue
            chunks.append((entry.name, str(entry)))
        elif entry.is_file() and entry.name.startswith("test_") and entry.suffix == ".py":
            loose_files.append(entry)

    if loose_files:
        chunks.append(("root", str(tests_root)))
    return chunks


def parse_junit(xml_path: Path) -> dict:
    """Parse one JUnit XML file into counters. Returns {} if unusable."""
    try:
        tree = ET.parse(str(xml_path))
    except (ET.ParseError, OSError):
        return {}

    root = tree.getroot()
    # pytest writes <testsuites><testsuite>...; some versions write <testsuite>
    # at the root. Collect <testcase> elements wherever they live.
    counts = {"tests": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0}
    failing: list[str] = []

    for case in root.iter("testcase"):
        counts["tests"] += 1
        bad = False
        for child in case:
            tag = child.tag.lower()
            if tag in FAILURE_TAGS:
                bad = True
                counts["failed" if tag == "failure" else "error"] += 1
                nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
                if len(failing) < 40:
                    failing.append(nodeid)
            elif tag in INCONCLUSIVE_TAGS:
                counts["skipped"] += 1
        if not bad:
            counts["passed"] += 1

    counts["failing_examples"] = failing
    return counts


def is_pytest_available(python_exe: str) -> bool:
    try:
        proc = subprocess.run(
            [python_exe, "-c", "import pytest, sys; sys.exit(0)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_chunk(
    python_exe: str,
    target: str,
    junit_path: Path,
    timeout: float,
    pytest_args: list[str],
) -> dict:
    """Run one pytest invocation for `target`, writing JUnit XML to junit_path."""
    cmd = [
        python_exe, "-m", "pytest", target,
        "-q",
        f"--junitxml={junit_path}",
        *pytest_args,
    ]
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout if timeout > 0 else None,
        )
    except subprocess.TimeoutExpired:
        return {
            "returncode": None,
            "timed_out": True,
            "counts": {},
            "tail": f"chunk exceeded timeout of {timeout}s",
            "started_utc": started.isoformat(),
        }

    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    tail = "\n".join(out.strip().splitlines()[-25:])
    counts = parse_junit(junit_path) if junit_path.exists() else {}

    return {
        "returncode": proc.returncode,
        "timed_out": False,
        "counts": counts,
        "tail": tail,
        "started_utc": started.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="EAP-04: run the LIUHAO pytest suite and emit a JSON report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]),
                    help="repository root (defaults to two levels up)")
    ap.add_argument("--tests-root", default="",
                    help="override the tests directory (default <repo-root>/tests)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter to run pytest with (needs pytest installed)")
    ap.add_argument("--json", dest="json_out", default="regression-report.json",
                    help="where to write the JSON report")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="per-chunk timeout in seconds; 0 = no timeout")
    ap.add_argument("--whole", action="store_true",
                    help="run the entire suite in ONE pytest invocation "
                         "(only safe when no other pytest is running)")
    ap.add_argument("--collect-only", action="store_true",
                    help="enumerate chunks and tests, but do not execute them")
    ap.add_argument("--pytest-arg", action="append", default=[],
                    help="extra argument passed through to pytest (repeatable), "
                         "e.g. --pytest-arg=-p --pytest-arg=no:randomly")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    tests_root = Path(args.tests_root).resolve() if args.tests_root else _default_tests_root(repo_root)

    report: dict = {
        "report_version": REPORT_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "tests_root": str(tests_root),
        "python": args.python,
        "mode": "collect-only" if args.collect_only else ("whole" if args.whole else "chunked"),
        "chunks": [],
        "totals": {"tests": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0},
        "failing_examples": [],
    }

    if not tests_root.is_dir():
        report["status"] = "NOT_VERIFIED"
        report["reason"] = f"tests root does not exist: {tests_root}"
        _write(report, args.json_out)
        print(report["reason"])
        return 2

    if not is_pytest_available(args.python):
        report["status"] = "NOT_VERIFIED"
        report["reason"] = (f"pytest is not importable by interpreter {args.python!r}. "
                            "This is an environment gap, recorded honestly as NOT "
                            "VERIFIED — never as a pass.")
        _write(report, args.json_out)
        print(report["reason"])
        return 2

    if args.whole:
        targets: list[tuple[str, str]] = [("whole-suite", str(tests_root))]
    else:
        targets = discover_chunks(tests_root)

    if not targets:
        report["status"] = "FAIL"
        report["reason"] = f"no test chunks discovered under {tests_root}"
        _write(report, args.json_out)
        print(report["reason"])
        return 1

    overall_ok = True
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, target in targets:
            entry: dict = {"chunk": name, "target": target}

            if args.collect_only:
                entry["executed"] = False
                entry["status"] = "NOT_VERIFIED"
                entry["note"] = "collect-only requested; not executed"
                report["chunks"].append(entry)
                continue

            junit = tmp / f"junit-{name.replace('/', '_')}.xml"
            res = run_chunk(args.python, target, junit, args.timeout, args.pytest_arg)
            entry["executed"] = True
            entry["returncode"] = res["returncode"]
            entry["timed_out"] = res["timed_out"]
            entry["counts"] = res["counts"]
            entry["tail"] = res["tail"]

            counts = res["counts"]
            if res["timed_out"]:
                entry["status"] = "FAIL"
                overall_ok = False
            elif not counts or counts.get("tests", 0) == 0:
                # A chunk that collected nothing is suspicious: tests vanished,
                # or pytest refused to collect. Never silently green.
                entry["status"] = "FAIL"
                entry["reason"] = "chunk collected 0 tests (or produced no JUnit XML)"
                overall_ok = False
            elif counts.get("failed", 0) or counts.get("error", 0):
                entry["status"] = "FAIL"
                overall_ok = False
            else:
                entry["status"] = "PASS"

            for key in ("tests", "passed", "failed", "error", "skipped"):
                report["totals"][key] += counts.get(key, 0)
            report["failing_examples"].extend(counts.get("failing_examples", []))
            report["chunks"].append(entry)

    if args.collect_only:
        report["status"] = "NOT_VERIFIED"
        report["reason"] = "collect-only run: nothing was executed"
        _write(report, args.json_out)
        print(f"collect-only: {len(targets)} chunk(s) discovered; nothing executed.")
        return 2

    report["status"] = "PASS" if overall_ok else "FAIL"
    _write(report, args.json_out)

    t = report["totals"]
    print(f"REGRESSION {report['status']}: "
          f"{t['tests']} tests, {t['passed']} passed, {t['failed']} failed, "
          f"{t['error']} error, {t['skipped']} skipped")
    print(f"JSON report written to: {args.json_out}")
    return 0 if overall_ok else 1


def _write(report: dict, json_out: str) -> None:
    out = Path(json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
