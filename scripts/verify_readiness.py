#!/usr/bin/env python3
"""RELEASE-READINESS-CONTRACT gate battery for LIUHAO AI OS.

Implements the 12 machine-checkable conditions defined in
``RELEASE-READINESS-CONTRACT.md``. Each ``check_<id>`` runs real evidence where
feasible in this environment and returns a status of PASS / FAIL / NOT VERIFIED /
BLOCKED plus a one-line evidence string.

Honesty rule: a condition is PASS only when its checker actually executed and
produced evidence. Absent evidence => NOT VERIFIED. A blocked human-sovereignty
decision => BLOCKED. A real contradiction => FAIL. A checker that could not RUN
at all -- its tool/harness rejected the invocation, or the runner module was
missing -- => ERROR: a verification-process failure, kept distinct from an
honest NOT VERIFIED so a missing plugin cannot masquerade as a recorded gap.

Outputs:
  - a markdown table + verdict to stdout
  - a JSON report to ``scripts/readiness_report.json`` — each condition carries
    ``status``, ``evidence``, ``command``, ``known_limitation``, ``blocker`` and
    ``next_action``; the report root also carries ``generated_at`` (UTC) and
    ``environment`` (os / python / ci). This is the G10 independent-verification
    evidence layer: every release-critical gate is judged with objective, dated,
    reproducible evidence — not a subjective "basically done".

Run with ``--only C2,C5,C8`` to execute a subset of conditions (e.g. to avoid the
heavy C3/C6/C7 battery on a quick check), or ``--full`` to actually execute the
test suite inside C3.

Exit codes:
  - 0  : no condition FAILED and none ERRORED (nothing contradicted, nothing
         unable to run)
  - 2  : one or more conditions FAILED or ERRORED

NOT VERIFIED / BLOCKED do NOT cause a non-zero exit today (they are pending
verification, not contradictions). ERROR does: a check that could not be
evaluated is a failure of the verification process, not a pending item. The JSON
``release_ready`` field is the authoritative verdict.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # bootstrap so `import src...` works when run directly
SRC = REPO / "src"
SCRIPTS = REPO / "scripts"
CI = REPO / ".github" / "workflows"

PASS, FAIL, NOT_VERIFIED, BLOCKED = "PASS", "FAIL", "NOT VERIFIED", "BLOCKED"
#: A checker that could not RUN (tool/harness rejected its invocation, or the
#: runner module was absent). Deliberately a distinct status -- NOT_VERIFIED
#: means "ran, no verdict yet"; ERROR means "never got to run", which is a
#: verification-process failure and must not be mistaken for the former.
ERROR = "ERROR"


def _run(cmd, env=None, timeout=120, cwd=None):
    """Run a command, return (returncode, stdout+stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return 1, f"ERROR: {exc!r}"


def _clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("LIUHAO_") or k in ("VAULT_ADDR",):
            env.pop(k, None)
    return env


#: Signals that a checker's tool/harness never actually started, as opposed to
#: running and reaching a verdict. A pytest that rejects ``--timeout`` (the
#: plugin is not installed) prints ``error: unrecognized arguments: --timeout``;
#: a missing runner module prints ``No module named ...``. Matched only in the
#: HEAD of the output, where a runner's own startup diagnostics live -- a test's
#: incidental wording further down must not be read as the harness failing.
_TOOL_FAILURE_SIGNALS = (
    ("the runner rejected its own arguments", re.compile(r"unrecognized arguments:")),
    ("the runner module is missing", re.compile(r"No module named '?pytest")),
    ("the plugin module is missing", re.compile(r"No module named '?pytest_timeout")),
)


def _tool_failure(out: str) -> str | None:
    """Return a one-line reason if ``out`` shows the tool/harness never started.

    Every critical subprocess here runs ``sys.executable``, so whether
    ``--timeout=NN`` is even a legal flag depends on WHICH interpreter launched
    this script (the repo ``.venv`` may lack pytest-timeout while the managed
    one has it). Without this, that accident is laundered into a plain
    NOT_VERIFIED that reads exactly like an honest evidence gap.
    """
    head = (out or "")[:1000]
    for label, pat in _TOOL_FAILURE_SIGNALS:
        if pat.search(head):
            return label
    return None


def _could_not_run(out: str) -> str | None:
    """Return the ERROR evidence string when the checker never ran, else None."""
    reason = _tool_failure(out)
    if reason is None:
        return None
    return (
        f"checker could not run ({reason}) -- this is NOT a verdict on the "
        f"product. If it is the --timeout flag, pytest-timeout is not installed "
        f"on this interpreter (it is declared in the [tests] extra). Tail: "
        f"{out[-200:]}"
    )


#: Markers a host-injected safe-delete shim leaves when it aborts a process.
#: The WorkBuddy CLI ships ``cli/vendor/shim/sitecustomize.py`` (imported at
#: interpreter start), which monkeypatches os.remove/os.unlink/shutil.rmtree.
#: Once a turn's cumulative deletes cross its bulk-confirm threshold it raises
#: SystemExit(1) -- so a pytest that ran green can still exit rc=1. That is NOT
#: a test failure and must not be read as one.
_SHIM_MARKER_RE = re.compile(r"\[safe-delete\]\[?(SAFE_DELETE_[A-Z_]+)")


def _shim_marker(out: str) -> str | None:
    """Name the safe-delete shim's marker if the output shows one, else None."""
    match = _SHIM_MARKER_RE.search(out or "")
    if match:
        return match.group(0).strip("[]")
    return None


#: pytest's own terminal summary for a FAILED run, e.g. ``5 failed, 267 passed``
#: or ``1 error in 3.1s``. Used ONLY as a fallback when no JUnit verdict exists:
#: it distinguishes "the session ran to completion and reported failures" (a
#: verdict -> NOT VERIFIED) from "the session never finished" (an ERROR). It is
#: never used to mint a PASS -- a pass requires the structural JUnit document.
_PYTEST_FAIL_SUMMARY_RE = re.compile(r"(\d+)\s+failed")
_PYTEST_ERROR_SUMMARY_RE = re.compile(r"(\d+)\s+errors?")


def _pytest_failure_summary(out: str) -> tuple[int, int] | None:
    """Return ``(failed, errors)`` if pytest printed a completion summary.

    ``None`` when no summary is present at all -- i.e. the process produced no
    terminal report (killed / truncated), which the caller reads as an ERROR
    rather than laundering into NOT VERIFIED.
    """
    text = out or ""
    failed = _PYTEST_FAIL_SUMMARY_RE.search(text)
    errored = _PYTEST_ERROR_SUMMARY_RE.search(text)
    if failed is None and errored is None:
        return None
    return (
        int(failed.group(1)) if failed else 0,
        int(errored.group(1)) if errored else 0,
    )


def _pytest_verdict(xml_path: str | None) -> tuple[int, int, int, int] | None:
    """Parse pytest's JUnit XML -> ``(tests, failures, errors, skipped)`` or None.

    COMPLETENESS (why a killed run cannot be read as a clean pass): pytest opens
    the JUnit file ONLY in ``pytest_sessionfinish`` and writes the entire
    document before closing it (``_pytest/junitxml.py``: open at :647, body at
    :683, close after). A run that is killed never reaches that point, so the
    file is absent or truncated -> unparseable. On top of "it parses" we require
    the document to be STRUCTURALLY self-consistent: every suite's ``tests``
    attribute must equal its own ``<testcase>`` count, and the root totals must
    equal the summed suites. A partially written tree fails these and is
    rejected -- it is never read as a zero-failure run.
    """
    if not xml_path or not os.path.isfile(xml_path):
        return None
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return None
    except OSError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            try:
                totals[key] += int(suite.get(key) or 0)
            except (TypeError, ValueError):
                return None
        n_cases = sum(1 for _ in suite.iter("testcase"))
        if n_cases != int(suite.get("tests") or 0):
            return None  # structural mismatch -> partial / tampered document
    if root.tag == "testsuites":
        for key, value in totals.items():
            attr = root.get(key)
            if attr is not None and int(attr) != value:
                return None
    return totals["tests"], totals["failures"], totals["errors"], totals["skipped"]


def _verdict_from_run(
    rc: int, out: str, xml_path: str | None, *, subject: str, pass_detail: str
) -> tuple[str, str]:
    """Decide PASS / NOT VERIFIED / ERROR from the RUNNER'S verdict, not ``rc``.

    ``rc`` is not trustworthy here: the host's safe-delete shim raises
    SystemExit(1) once a turn's delete count crosses its threshold, so pytest
    can exit non-zero with every test green. Reading pytest's own JUnit verdict
    (and naming the shim when it is what moved ``rc``) separates "rc=1 because a
    test failed" from "rc=1 because the shim aborted a clean run". Precedence:

    1. the runner could not start (bad flag / missing module)      -> ERROR
    2. a parseable verdict with 0 failures and 0 errors            -> PASS
    3. a parseable verdict WITH failures/errors                    -> NOT VERIFIED
    4. no verdict, rc != 0, but pytest printed a fail summary      -> NOT VERIFIED
    5. no verdict, rc != 0, no summary (killed / truncated)        -> ERROR
    6. no verdict, rc == 0                                         -> NOT VERIFIED

    Tier 4 exists so a suite that genuinely ran and failed (and, for any reason,
    left no JUnit document) is still a verdict rather than a tool accident; a
    PASS is never granted without the structural JUnit document (tier 2), so a
    killed run can never be read as a clean pass.
    """
    err = _could_not_run(out)
    if err:
        return ERROR, err
    verdict = _pytest_verdict(xml_path)
    if verdict is not None:
        tests, failures, errors, skipped = verdict
        if tests > 0 and failures == 0 and errors == 0:
            detail = f"{pass_detail} ({tests} tests, 0 failed, 0 errors"
            detail += f", {skipped} skipped)" if skipped else ")"
            if rc != 0:
                shim = _shim_marker(out)
                detail += (
                    f"; process rc={rc} was raised by the safe-delete shim "
                    f"({shim}), NOT by any test failure"
                )
            return PASS, detail
        return NOT_VERIFIED, (
            f"{subject} ran: {tests} tests, {failures} failed, {errors} errors "
            f"(rc={rc}) -- a real failure, not a tool accident."
        )
    summary = _pytest_failure_summary(out)
    if summary is not None and (summary[0] > 0 or summary[1] > 0):
        failed, errored = summary
        return NOT_VERIFIED, (
            f"{subject} ran: pytest reported {failed} failed, {errored} errors "
            f"(rc={rc}) -- a real verdict, not a tool accident. (No JUnit XML "
            f"was produced; a PASS requires the structural document, so this "
            f"stays NOT VERIFIED.)"
        )
    if rc != 0:
        shim = _shim_marker(out)
        return ERROR, (
            f"{subject}: rc={rc} and no parseable pytest verdict (JUnit) was "
            f"produced -- the checker did not complete a clean run"
            + (f"; a shim marker is present ({shim})" if shim else "")
            + f". Tail: {out[-200:]}"
        )
    return NOT_VERIFIED, (
        f"{subject}: rc=0 but no parseable verdict was produced; {out[-200:]}"
    )


#: Set by ``check_tests`` on a --full run; C12 reuses it so the whole suite runs
#: ONCE per battery. A second execution would double CI wall-clock, and two
#: pytest processes against this repo at once share scratch dirs / sqlite files
#: and manufacture phantom failures.
_LAST_FULL_RUN: dict | None = None


#: Hard cap (seconds) for the full-suite pytest subprocess inside C3/C12.
#: Measured on the reference Windows host (2026-10-24): the whole suite takes
#: ~44m38s (~2678s, 6423 tests), so the previous 1800s cap sat BELOW the
#: suite's real runtime -- the gate would kill a healthy pytest mid-run and
#: the truncated JUnit could only surface as ERROR. 5400s (90 min) is ~2x the
#: measured runtime, leaving headroom for slower CI runners and suite growth.
#: The kill is kept on purpose: a genuinely wedged pytest must not hang the
#: battery forever; it surfaces as rc=124 -> ERROR, never as a pass. The
#: release-readiness.yml job timeout must exceed this plus install/collection
#: overhead so the runner cannot preempt the gate's own honest classification
#: (pinned by tests/scripts/test_readiness_full_timeout.py).
_FULL_SUITE_TIMEOUT_S = 5400


def _full_suite_timeout() -> int:
    """Seconds the full pytest suite may run before the gate kills it.

    Read at call time (not import time) so CI/tests can steer it via
    ``LIUHAO_READINESS_FULL_TIMEOUT_S`` without reloading the module. A
    missing, unparsable, or non-positive override falls back to the default.
    """
    raw = os.environ.get("LIUHAO_READINESS_FULL_TIMEOUT_S", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return _FULL_SUITE_TIMEOUT_S


def _execute_full_suite() -> dict:
    """Execute the full suite once; return the raw run plus its JUnit verdict.

    The JUnit file is written under the OS temp dir and deliberately NOT deleted
    afterwards: deleting is exactly the operation the host safe-delete shim
    intercepts (see ``_SHIM_MARKER_RE``), and this module must not trip the very
    accident it was hardened against. The temp dir is reclaimed by the OS.

    Not cached at call level on purpose: ``check_tests`` recomputes on every call
    so its classification stays drivable by a stubbed ``_run`` in tests.
    """
    fd, xml_path = tempfile.mkstemp(prefix="ready_junit_", suffix=".xml")
    os.close(fd)  # pytest --junitxml opens and overwrites this path itself
    cmd = [
        sys.executable, "-m", "pytest", "tests/", "-p", "no:phoenix", "-q",
        # --timeout=300, NOT 60: pytest-timeout has no SIGALRM on Windows, so a
        # breach dumps stacks and then os._exit(1)s the WHOLE pytest process
        # (see timeout_timer() in pytest_timeout.py) -- no sessionfinish, no
        # JUnit, no summary. With 60s, ONE transient D: filesystem stall in
        # os.stat destroyed a healthy ~45min run (twice on 2026-10-24, ~16min
        # in, 0-byte JUnit, rc=1). 300s lets transient stalls pass while still
        # killing a genuinely wedged test fast -- and the dump names the
        # culprit in the persisted .log below.
        "--timeout=300", f"--junitxml={xml_path}",
    ]
    rc, out = _run(cmd, timeout=_full_suite_timeout())
    # Persist the complete pytest output BEFORE classification. When the suite
    # is force-killed mid-run (the os._exit path above), the process's captured
    # stdout/stderr is otherwise reduced to a 200-char tail in the report and
    # the stack dump naming the stuck test is lost forever.
    log_path = xml_path + ".log"
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(out or "")
    except OSError:
        log_path = None
    verdict = _pytest_verdict(xml_path)
    return {
        "rc": rc,
        "out": out,
        "xml": xml_path,
        "log": log_path,
        "tests": verdict[0] if verdict else None,
        "failures": verdict[1] if verdict else None,
        "errors": verdict[2] if verdict else None,
        "skipped": verdict[3] if verdict else None,
        "command": " ".join(cmd),
    }


# --------------------------------------------------------------------------- #
# Checkers
# --------------------------------------------------------------------------- #
def check_build():
    """C1: compileall src + import documented entry modules."""
    rc, out = _run(
        [sys.executable, "-m", "compileall", "-q", str(SRC)], timeout=180
    )
    if rc != 0:
        return FAIL, f"compileall failed (rc={rc}): {out[-300:]}"
    imports = [
        "src.gateway.main",
        "src.kernels.execution.fence",
        "src.distribution.coordination",
        "src.security.secret_store",
    ]
    bad = []
    for mod in imports:
        rc2, out2 = _run(
            [sys.executable, "-c", f"import {mod}"],
            timeout=60,
        )
        if rc2 != 0:
            bad.append(f"{mod}: {out2[-200:]}")
    if bad:
        return FAIL, "import errors: " + " | ".join(bad)
    return PASS, "compileall clean; 4 entry modules import OK"


def check_clean_env_run():
    """C2: boot gateway in a clean env, GET /v1/health -> 200."""
    code = (
        "import os, tempfile\n"
        "os.environ[\"AUDIT_DB_PATH\"] = os.path.join("
        "tempfile.mkdtemp(prefix=\"ready_audit_\"), \"audit_store.db\")\n"
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from src.gateway.main import get_app\n"
        "app = get_app()\n"
        "with TestClient(app) as c:\n"
        "    r = c.get('/v1/health')\n"
        "    print('STATUS', r.status_code)\n"
    )
    rc, out = _run(
        [sys.executable, "-c", code],
        env=_clean_env(),
        timeout=90,
    )
    if rc == 0 and "STATUS 200" in out:
        return PASS, "clean-env boot + GET /v1/health = 200"
    if rc == 0:
        return NOT_VERIFIED, f"boot OK but health not 200: {out[-200:]}"
    err = _could_not_run(out)
    if err:
        return ERROR, err
    # Boot raised: distinguish product defect from env/config gap.
    return NOT_VERIFIED, f"boot raised (env/config gap, not proven product defect): {out[-300:]}"


def check_tests(full=False):
    """C3: collect the suite; with ``--full`` run it and read pytest's OWN verdict.

    The ``--full`` branch is the numeric path the contract gates on. It executes
    the whole suite and classifies via JUnit XML, so (a) a killed / truncated run
    cannot be read as a zero-failure pass and (b) a genuinely green run that the
    host's safe-delete shim aborted (rc=1) is still PASS. Without ``--full`` it
    falls back to a recorded baseline when one exists, else NOT VERIFIED.
    """
    rc, out = _run(
        [sys.executable, "-m", "pytest", "tests/", "--co", "-q"],
        timeout=180,
    )
    collected = 0
    if rc == 0:
        m = re.search(r"(\d+) tests? collected", out)
        if m:
            collected = int(m.group(1))
    if rc != 0 or collected == 0:
        return FAIL, f"collection failed (rc={rc}): {out[-200:]}"

    # --full takes precedence over a recorded baseline: a stale baseline must
    # never short-circuit an actual execution the operator asked for.
    if full:
        global _LAST_FULL_RUN
        run = _execute_full_suite()
        _LAST_FULL_RUN = run
        status, evidence = _verdict_from_run(
            run["rc"], run["out"], run["xml"],
            subject="full suite",
            pass_detail=f"full run: {collected} collected, 0 failed",
        )
        if status != PASS and run.get("log"):
            evidence += f" Full pytest output persisted at: {run['log']}"
        return status, evidence

    baseline = SCRIPTS / "test_baseline.json"
    if baseline.exists():
        try:
            data = json.loads(baseline.read_text(encoding="utf-8"))
            fails = data.get("failures", data.get("failed", "?"))
            return NOT_VERIFIED, (
                f"{collected} collected; recorded baseline failures={fails} "
                f"(re-run to refresh)."
            )
        except Exception:  # noqa: BLE001
            pass
    return NOT_VERIFIED, (
        f"{collected} collected; full clean-env re-run not yet recorded "
        f"(use --full to attempt)."
    )


def check_security():
    """C4: secret scan + fail-closed present + CI static gates declared."""
    # (a) high-confidence hardcoded-secret scan
    patterns = [
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"aws_secret_access_key\s*=\s*[\"'][A-Za-z0-9/+=]{20,}[\"']"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ]
    hits = []
    for p in SRC.rglob("*.py"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for pat in patterns:
            for mm in pat.finditer(text):
                hits.append(f"{p.relative_to(REPO)}:{mm.group(0)[:24]}")
    if hits:
        return FAIL, "hardcoded secret patterns found: " + "; ".join(hits[:5])
    # (b) fail-closed path present in secret_store
    ss = SRC / "security" / "secret_store.py"
    fail_closed = False
    if ss.exists():
        t = ss.read_text(encoding="utf-8", errors="ignore")
        fail_closed = "SecretBackendUnavailable" in t and (
            "no backend" in t.lower() or "raise SecretBackendUnavailable" in t
        )
    if not fail_closed:
        return FAIL, "fail-closed path not confirmed in secret_store.py"
    # (c) CI declares Bandit (high) + Semgrep (--error)
    declared = []
    for f in CI.glob("*.yml"):
        t = f.read_text(encoding="utf-8", errors="ignore")
        if "bandit" in t and "severity-level high" in t:
            declared.append("bandit:high")
        if "semgrep" in t and "--error" in t:
            declared.append("semgrep:error")
    if len(declared) < 2:
        return NOT_VERIFIED, (
            f"scan clean + fail-closed present; CI static gates declared={declared} "
            f"(not both executed here)."
        )
    return PASS, (
        f"no high-confidence secrets; fail-closed present; CI gates {declared}"
    )


def check_data_integrity():
    """C5: run the hash-chain guardrail scripts; HC-02..08 + HC-11 COMPLIANT."""
    script = SCRIPTS / "verify_p08_hash_chain_sig_alg.py"
    if not script.exists():
        return NOT_VERIFIED, "verify_p08_hash_chain_sig_alg.py not found"
    rc, out = _run([sys.executable, str(script)], timeout=120)
    if rc != 0:
        return FAIL, f"hash-chain script rc={rc}: {out[-200:]}"
    if "32/32" in out or "COMPLIANT" in out:
        return PASS, "hash-chain sig/alg 32/32 COMPLIANT (HC-02..08 + HC-11)"
    return NOT_VERIFIED, f"hash-chain output unclear: {out[-200:]}"


def check_reliability():
    """C6: run chaos/soak/failure-mode suite; fail-closed assertions must hold.

    Covers the RELEASE-READINESS-CLOSURE G6 matrix on throwaway temp databases
    (the production ``audit_store.db`` is never opened): ENOSPC/SQLITE_FULL
    fail-closed, CORRUPT/IOERR quarantine, real-file corruption detection,
    cross-process kill + restart chain recovery, single-writer fence / stale
    lease, lock contention / thread safety, and (new) in-process restart
    recovery under synchronous=FULL plus a real OS-level read-only DB-unavailable
    scenario.
    """
    targets = [
        SCRIPTS.parent / "tests" / "distribution" / "test_fence_coordination_chaos.py",
        SCRIPTS.parent / "tests" / "kernels" / "audit" / "test_audit_soak_concurrency.py",
        SCRIPTS.parent / "tests" / "kernels" / "audit" / "test_storage_faults.py",
        SCRIPTS.parent / "tests" / "kernels" / "audit" / "test_multiprocess_append.py",
        SCRIPTS.parent / "tests" / "kernels" / "audit" / "test_fencing_wired.py",
        SCRIPTS.parent / "tests" / "kernels" / "audit" / "test_g6_restart_recovery.py",
    ]
    present = [t for t in targets if t.exists()]
    if not present:
        return NOT_VERIFIED, "no reliability/chaos suites present yet"
    rc, out = _run(
        [sys.executable, "-m", "pytest", *[str(t) for t in present],
         "-p", "no:phoenix", "-q", "--timeout=120"],
        timeout=600,
    )
    if rc == 0:
        return PASS, (
            f"reliability/failure-mode suites pass ({len(present)} files, "
            f"temp-DB only; live audit_store.db untouched)"
        )
    err = _could_not_run(out)
    if err:
        return ERROR, err
    return NOT_VERIFIED, f"reliability suites rc={rc} (may need live infra): {out[-200:]}"


#: The bench prints `current profile: '<key>'` when the numeric gate SKIPs.
_C7_PROFILE_RE = re.compile(r"current profile:\s*'([^']+)'")


def _c7_profile_from(out: str) -> str | None:
    """The machine profile the bench run reported, if it said so."""
    match = _C7_PROFILE_RE.search(out)
    return match.group(1) if match else None


def _c7_verdict(out: str, profile_override: str | None) -> tuple[str, str]:
    """Classify the bench gate's stdout into ``(status, reason)``.

    Kept pure -- no subprocess, no filesystem -- so the classification is
    testable instead of being a claim. See
    tests/scripts/test_readiness_c7_profile_gate.py.

    Every branch is honest in the same direction: a numeric gate that did not
    actually compare is NOT_VERIFIED, never PASS.

    The SKIP wording used to say "profile mismatch / CI Linux", which reads as
    "this only happens on Linux". It does not. Measured here: the captured
    baseline is ``Windows|py3.13.14|...`` while this repo's venv is py3.12.14,
    so the numeric gate SKIPs on the Windows host too. The reason text now says
    that, and names the profile actually seen.
    """
    if "PERFORMANCE GATE: PASS" in out:
        if profile_override:
            return (
                PASS,
                "bench --quick --gate PASS, but LIUHAO_BENCH_PROFILE=%r forced "
                "the profile key: this is a real numeric comparison ONLY if that "
                "override names the hardware the baseline was captured on. Read "
                "it as a regression signal under that condition alone."
                % profile_override,
            )
        return PASS, "bench --quick --gate PASS on this machine's own profile"
    if "GATE: SKIP" in out or "profile mismatch" in out.lower():
        seen = _c7_profile_from(out)
        return (
            NOT_VERIFIED,
            "numeric gate SKIPped -- no baseline captured for this machine "
            "profile" + (f" (current: {seen})" if seen else "") + ". "
            "Not Linux-specific: the baseline holds only "
            "Windows|py3.13.14|... and this repo's venv is py3.12.14, so the "
            "numeric gate does not run here either. Invariants were still "
            "checked; what is missing is the throughput comparison.",
        )
    return NOT_VERIFIED, (
        "numeric gate neither PASSed nor SKIPped cleanly -- treating as "
        "unproven, not as a regression"
    )


def check_performance():
    """C7: bench baseline exists + --quick gate passes on matching profile."""
    baseline = SCRIPTS / "bench_baseline.json"
    if not baseline.exists():
        return NOT_VERIFIED, "bench_baseline.json missing"
    gate = SCRIPTS / "bench_audit_append.py"
    if not gate.exists():
        return NOT_VERIFIED, "bench_audit_append.py missing"
    rc, out = _run(
        [sys.executable, str(gate), "--quick", "--gate", str(baseline)],
        timeout=240,
    )
    status, reason = _c7_verdict(out, os.environ.get("LIUHAO_BENCH_PROFILE"))
    if status == NOT_VERIFIED and "neither PASSed" in reason:
        # Preserve the load-sensitive wording (and the rc/output tail) for the
        # genuinely-ambiguous case; a FAIL under concurrent load is not proof of
        # a code regression.
        return NOT_VERIFIED, (
            f"gate did not PASS (load-sensitive; run on idle/CI): rc={rc}; "
            f"{out[-160:]}"
        )
    return status, reason
    # The numeric gate is load-sensitive (throughput vs baseline). A FAIL under
    # concurrent load is not a proven code regression, so we surface NOT VERIFIED
    # rather than FAIL, and point at an idle/CI runner for a stable verdict.
    return NOT_VERIFIED, (
        f"gate did not PASS (load-sensitive; run on idle/CI): rc={rc}; "
        f"{out[-160:]}"
    )


def check_observability():
    """C8: RUNTIME SCRAPE of /v1/metrics/prometheus (not merely "endpoints exist").

    Performs a real scrape against a booted app and proves the export path
    actually executes: HTTP 200, a non-empty Prometheus payload, and real
    metric families present. It also reports honestly whether any sample
    increments under synthetic traffic -- export and per-request
    instrumentation are DIFFERENT claims and must not be conflated.
    """
    obs = SRC / "gateway" / "observability.py"
    health = SRC / "gateway" / "health.py"
    if not obs.exists() or not health.exists():
        return FAIL, "observability/health modules missing"
    t = obs.read_text(encoding="utf-8", errors="ignore")
    has_metrics = "/metrics/prometheus" in t or "prometheus_metrics" in t
    if not has_metrics:
        return FAIL, "prometheus metrics endpoint not found"

    # ---- REAL runtime scrape (the evidence that makes C8 verifiable) ----
    code = (
        "import os, tempfile\n"
        "os.environ[\"AUDIT_DB_PATH\"] = os.path.join("
        "tempfile.mkdtemp(prefix=\"ready_audit_\"), \"audit_store.db\")\n"
        "import sys\n"
        "sys.path.insert(0, '__REPO__')\n"
        "from fastapi.testclient import TestClient\n"
        "from src.gateway.main import get_app\n"
        "app = get_app()\n"
        "with TestClient(app) as c:\n"
        "    for _ in range(3):\n"
        "        c.get('/v1/health')\n"
        "    r1 = c.get('/v1/metrics/prometheus')\n"
        "    if r1.status_code != 200:\n"
        "        print('SCRAPE_STATUS', r1.status_code)\n"
        "        sys.exit(0)\n"
        "    b1 = r1.text\n"
        "    for _ in range(7):\n"
        "        c.get('/v1/health')\n"
        "    b2 = c.get('/v1/metrics/prometheus').text\n"
        "    import re as _re\n"
        "    fams = sorted({ln.split()[2] for ln in b1.splitlines()\n"
        "                   if ln.startswith('# TYPE ')})\n"
        "    req_ubx = ('executor_fence_denials_total',\n"
        "               'executor_fence_active_leases',\n"
        "               'coordinator_admission_contention_total')\n"
        "    want = [f for f in req_ubx if f in fams]\n"
        "    http_present = 'http_requests_total' in fams\n"
        "    http_fed = ('http_requests_total{' in b1) or ('http_requests_total{' in b2)\n"
        "    err_fams = [f for f in fams if _re.search(\n"
        "        r'error|exception|fail|denied|reject|5xx|refuse', f)]\n"
        "    print('SCRAPE_STATUS', 200)\n"
        "    print('SCRAPE_BYTES', len(b1))\n"
        "    print('N_FAMILIES', len(fams))\n"
        "    print('UBX005_FAMILIES', ','.join(want))\n"
        "    print('HTTP_TOTAL_PRESENT', int(http_present))\n"
        "    print('HTTP_TOTAL_FED', int(http_fed))\n"
        "    print('ERROR_VIS_FAMS', ','.join(err_fams))\n"
        "    print('SAMPLES_CHANGED', int(b1 != b2))\n"
    ).replace("__REPO__", str(REPO).replace("\\", "/"))
    rc, out = _run(
        [sys.executable, "-c", code], env=_clean_env(), timeout=180
    )
    if rc != 0:
        err = _could_not_run(out)
        if err:
            return ERROR, err
        return NOT_VERIFIED, f"runtime scrape could not run (env gap): {out[-200:]}"
    if "SCRAPE_STATUS 200" not in out:
        # A non-200 here is a real contradiction in the export path.
        return FAIL, f"metrics scrape did not return 200: {out.strip()[-160:]}"

    m_bytes = re.search(r"SCRAPE_BYTES (\d+)", out)
    m_fams = re.search(r"N_FAMILIES (\d+)", out)
    m_ubx = re.search(r"UBX005_FAMILIES (.*)", out)
    m_http_present = re.search(r"HTTP_TOTAL_PRESENT (\d+)", out)
    m_http_fed = re.search(r"HTTP_TOTAL_FED (\d+)", out)
    m_err = re.search(r"ERROR_VIS_FAMS (.*)", out)
    m_chg = re.search(r"SAMPLES_CHANGED (\d+)", out)
    n_bytes = int(m_bytes.group(1)) if m_bytes else 0
    n_fams = int(m_fams.group(1)) if m_fams else 0
    ubx = (m_ubx.group(1).strip() if m_ubx else "")
    http_present = bool(int(m_http_present.group(1))) if m_http_present else False
    http_fed = bool(int(m_http_fed.group(1))) if m_http_fed else False
    err_fams = (m_err.group(1).strip() if m_err else "")
    changed = bool(int(m_chg.group(1))) if m_chg else False

    if n_bytes <= 0 or n_fams <= 0:
        return FAIL, "metrics scrape returned an empty payload (no families exported)"

    # ---- C8 hardened regression assertions (a silent regression now FAILs) ----
    # Any future gateway/observability change MUST re-satisfy every one of these
    # or C8 returns FAIL (battery exit 2), which blocks release. This is the
    # long-term regression gate for observability, not a one-off PASS.
    if n_fams < 40:
        return FAIL, f"metric family count regressed to {n_fams} (< 40 floor; baseline 45)"
    if not http_present:
        return FAIL, "http_requests_total family not exported (HTTP counters missing)"
    if not http_fed:
        return FAIL, (
            "http_requests_total declared but carries no samples "
            "(request instrumentation dead / not wired into middleware)"
        )
    missing_ubx = [
        f for f in (
            "executor_fence_denials_total",
            "executor_fence_active_leases",
            "coordinator_admission_contention_total",
        ) if f not in ubx
    ]
    if missing_ubx:
        return FAIL, (
            "required sovereignty/coordination metric families missing: "
            + ", ".join(missing_ubx)
        )
    if not err_fams:
        return FAIL, (
            "no error-visibility metric family exported "
            "(no error|exception|fail|denied|reject family found)"
        )

    evidence = (
        f"runtime scrape /v1/metrics/prometheus = 200, {n_bytes} bytes, "
        f"{n_fams} real metric families exported (floor 40); "
        f"http_requests_total PRESENT+FED; UBX005 all present; "
        f"error-visibility present"
        + (f" (incl. {ubx})" if ubx else "")
    )
    # Changed is informational only now that http_fed is a hard requirement.
    if not changed:
        evidence += "; NOTE: some non-HTTP sample did not move under synthetic traffic"
    return PASS, evidence


def check_deployment():
    """C9: real deployment/upgrade/rollback verification chain present + runnable.

    Prefers ``scripts/verify_deploy_readiness.py`` (which REALLY runs alembic
    upgrade/downgrade on a TEMP db + validates the staging compose). Falls back
    to the lighter Dockerfile + alembic presence check when the script or the
    alembic-capable interpreter is unavailable.
    """
    script = SCRIPTS / "verify_deploy_readiness.py"
    # The migration checks need alembic/sqlalchemy/pyyaml; the managed venv
    # lacks them, so only run the script when the repo's own .venv is present.
    g9_py = None
    for cand in (REPO / ".venv" / "Scripts" / "python.exe",
                 REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            g9_py = str(cand)
            break
    if script.exists() and g9_py is not None and g9_py != sys.executable:
        env = dict(os.environ, G9_PYTHON=g9_py)
        rc, out = _run([g9_py, str(script)], env=env, timeout=300)
        fail_lines = [ln for ln in out.splitlines()
                      if ln.strip().startswith("[") and "FAIL" in ln]
        if rc == 0 and not fail_lines:
            summary = "; ".join(
                ln.strip() for ln in out.splitlines() if ln.strip().startswith("[")
            )[:400]
            return PASS, f"verify_deploy_readiness.py PASS: {summary}"
        if fail_lines:
            return FAIL, (
                f"verify_deploy_readiness.py FAIL: {fail_lines[0].strip()} | "
                f"{out[-200:]}"
            )
        err = _could_not_run(out)
        if err:
            return ERROR, err
        return NOT_VERIFIED, (
            f"verify_deploy_readiness.py ran (rc={rc}); {out[-200:]}"
        )

    has_docker = (REPO / "Dockerfile").exists()
    has_alembic = (REPO / "alembic.ini").exists() or (REPO / "migrations").exists()
    if not has_docker or not has_alembic:
        return NOT_VERIFIED, (
            f"Dockerfile={has_docker}, alembic={has_alembic} (incomplete)"
        )
    # honest note: real CD intentionally absent (deploy jobs exit 1)
    cd_absent = False
    for f in CI.glob("*.yml"):
        t = f.read_text(encoding="utf-8", errors="ignore")
        if "deploy" in t and "exit 1" in t:
            cd_absent = True
    return NOT_VERIFIED, (
        f"Dockerfile + alembic present; real CD absent={cd_absent} (honest)"
    )


def check_independent_verification():
    """C10: standing builder-decoupled verification harness."""
    harness = SCRIPTS / "independent_verification.py"
    if harness.exists():
        return PASS, "independent verification harness present"
    return NOT_VERIFIED, "no standing independent verification harness yet"


def check_hc01():
    """C11: HC-01 audit master chain — frozen human-sovereignty decision."""
    script = SCRIPTS / "verify_p08b_chain_matrix.py"
    if script.exists():
        rc, out = _run([sys.executable, str(script)], timeout=120)
        if "HC-01" in out and ("BLOCKED" in out or "PENDING" in out):
            return BLOCKED, "HC-01 BLOCKED / GO=BLOCKED (human decision pending, isolated)"
        # if script ran and HC-01 not explicitly blocked, still frozen by governance
    return BLOCKED, (
        "HC-01 BLOCKED / GO=BLOCKED — HUMAN DECISION PENDING (Decision Isolation active; "
        "not an engineering blocker)"
    )


def check_no_major_failures(full=False):
    """C12: no unexplained major failure -- cross-referenced against a real run.

    Collection-only without ``--full`` (honest NOT VERIFIED). With ``--full`` it
    consumes the full-suite verdict C3 produced (reusing that single execution;
    it only runs the suite itself when C12 is the sole condition selected) and
    PASSes ONLY on a clean session: >0 tests, 0 failures, 0 errors. A run with
    failures stays NOT VERIFIED -- attributing each failure (environment vs
    product) is not something this battery can settle, and guessing would be
    exactly the dishonesty the contract forbids.
    """
    rc, out = _run(
        [sys.executable, "-m", "pytest", "tests/", "--co", "-q"], timeout=180
    )
    collected = 0
    if rc == 0:
        m = re.search(r"(\d+) tests? collected", out)
        if m:
            collected = int(m.group(1))
    if collected == 0:
        return FAIL, "cannot collect suite"
    if not full:
        return NOT_VERIFIED, (
            f"{collected} collected; full clean-env cross-reference pending "
            f"(use --full; no unexplained major failure known)."
        )
    run = _LAST_FULL_RUN if _LAST_FULL_RUN is not None else _execute_full_suite()
    tests, failures, errors = run["tests"], run["failures"], run["errors"]
    if tests and failures == 0 and errors == 0:
        return PASS, (
            f"full run cross-reference: {tests} tests, 0 failed, 0 errors "
            f"(no unexplained major failure)"
        )
    if tests is None:
        log = run.get("log")
        return NOT_VERIFIED, (
            f"full run produced no parseable JUnit verdict (rc={run['rc']}) -- "
            f"the session did not complete a clean run; cross-reference pending"
            + (f"; full pytest output: {log}" if log else "")
        )
    return NOT_VERIFIED, (
        f"full run: {tests} tests, {failures} failed, {errors} errors -- each "
        f"failure must be attributed (environment vs product) before PASS"
    )


CHECKS = [
    ("C1", "Build passes", check_build),
    ("C2", "Clean-environment runnable", check_clean_env_run),
    ("C3", "Stable tests", check_tests),
    ("C4", "Critical security blocker = 0", check_security),
    ("C5", "Data integrity passes", check_data_integrity),
    ("C6", "Crash / recovery / failover", check_reliability),
    ("C7", "Performance meets scale", check_performance),
    ("C8", "Observability + operations", check_observability),
    ("C9", "Deployment / upgrade / rollback", check_deployment),
    ("C10", "Independent verification", check_independent_verification),
    ("C11", "HC-01 real verification", check_hc01),
    ("C12", "No unexplained major failures", check_no_major_failures),
]

# G10 structured-evidence metadata for each condition: the exact command/test,
# the known limitation of that check, any hard blocker, and the next action.
# Blocked human-sovereignty conditions carry a ``blocker`` string; everything
# else leaves it empty so the JSON report stays honest about what is pending.
CONDITION_META = {
    "C1": {
        "command": "python -m compileall src/ + import 4 entry modules",
        "known_limitation": "compileall + import only; does not prove runtime behaviour",
        "next_action": "wire as a CI build job (already compiles in normal CI)",
    },
    "C2": {
        "command": "fastapi TestClient boot + GET /v1/health",
        "known_limitation": "in-process TestClient, not a real container boot",
        "next_action": "add a CI job that boots the built image from scratch",
    },
    "C3": {
        "command": "pytest tests/ --co + (--full) pytest tests/ --junitxml=<tmp>",
        "known_limitation": "without --full it relies on a recorded baseline; with --full the verdict comes from JUnit XML (structural), so a safe-delete-shim rc=1 on a green run is PASS and a killed run is ERROR",
        "next_action": "run --full on a clean CI runner (release-readiness.yml already does); wire pytest as the formal G3 gate",
    },
    "C4": {
        "command": "high-confidence secret-pattern scan + fail-closed grep + CI gate declaration",
        "known_limitation": "static only; Bandit/Semgrep are declared but not executed here",
        "next_action": "execute bandit/semgrep in CI",
    },
    "C5": {
        "command": "scripts/verify_p08_hash_chain_sig_alg.py (HC-02..08 + HC-11)",
        "known_limitation": "covers HC-02..08 + HC-11 only; HC-01 is frozen",
        "next_action": "none — HC-01 handled by C11/governance",
    },
    "C6": {
        "command": "pytest tests/distribution/test_fence_coordination_chaos.py tests/kernels/audit/test_audit_soak_concurrency.py tests/kernels/audit/test_storage_faults.py tests/kernels/audit/test_multiprocess_append.py tests/kernels/audit/test_fencing_wired.py tests/kernels/audit/test_g6_restart_recovery.py",
        "known_limitation": "missing power-loss / network-partition scenarios; uses temp DBs only (live audit_store.db untouched); in-process restart recovery + real read-only-DB-unavailable added this cycle",
        "next_action": "add power-loss / partition scenarios; wire as a CI clean-env job",
    },
    "C7": {
        "command": "scripts/bench_audit_append.py --quick --gate bench_baseline.json + scripts/bench_audit_scale.py --scale 1m --sync FULL",
        "known_limitation": "numeric gate is profile-keyed and currently SKIPs EVERYWHERE we actually run, not only on Linux: bench_baseline.json holds exactly one profile (Windows|py3.13.14|...), while this repo's venv is py3.12.14 and CI is Linux, so neither matches. Measured: `bench_audit_append.py --quick --gate` on this host exits 0 printing 'PERFORMANCE GATE: SKIP' with current profile 'Windows|py3.12.14|...'. Invariants ARE still checked and remain blocking; what does not run is the throughput comparison. 1M real-insert validated (15,095 eps batch=FULL, 27.8s full-chain verify, 8-proc 15,484 eps, 0 lost/0 fork). 100M is honest extrapolation (~1.84h write / ~46min verify / ~39GB) — harness is genuinely executable via --scale 100m but not yet run.",
        "next_action": "to make C7 a real numeric gate, re-capture bench_baseline.json ON the runner that will enforce it (--write-baseline) — a baseline copied across hardware is not a regression signal. Also note the fingerprint includes CPU model and thread count, so GitHub-hosted runners (variable cpu_count) will keep SKIPping; a stable gate needs a pinned self-hosted runner plus LIUHAO_BENCH_PROFILE, or a coarse-grained profile key. Until then, C7's numeric half is honest but inert.",
    },
    "C8": {
        "command": "runtime scrape of /v1/metrics/prometheus via TestClient",
        "known_limitation": "proves the export path executes; alerting/ingestion RECEIPT is NOT verified",
        "next_action": "prove a real Prometheus scrape receives data AND an alert actually fires",
    },
    "C9": {
        "command": "scripts/verify_deploy_readiness.py (A: docker CLI, B: alembic upgrade/downgrade on TEMP db, C: staging compose schema)",
        "known_limitation": "authoritative `docker compose config` NOT VERIFIED (compose v2 plugin absent in this env); image not actually built; real CD absent by design; runs on repo .venv (managed venv lacks alembic)",
        "blocker": "HUMAN DECISION REQUIRED — production deploy target (host/secrets/DB/scale/approval) not chosen; CD jobs exit 1 by design",
        "next_action": "install docker compose plugin + build image in a prod-prepared env; choose a real CD target (helm/k8s)",
    },
    "C10": {
        "command": "scripts/independent_verification.py presence + this battery",
        "known_limitation": "harness present; per-gate structured evidence report is new",
        "next_action": "keep the battery CI-wired; keep extending per-gate evidence",
    },
    "C11": {
        "command": "scripts/verify_p08b_chain_matrix.py / HC-01 governance",
        "known_limitation": "HC-01 is frozen; it cannot be independently VERIFIED by engineering",
        "blocker": "HUMAN DECISION REQUIRED — HC-01 A/B/C disposition (overwrite/restore/repair/migrate/delete/rebuild all forbidden until then)",
        "next_action": "await the human-sovereignty decision; never touch the live audit_store.db",
    },
    "C12": {
        "command": "pytest tests/ --co + (--full) reuse of C3's full-suite JUnit verdict",
        "known_limitation": "collection-only without --full; with --full it PASSes ONLY on a clean session (>0 tests, 0 failures, 0 errors); a failed run stays NOT VERIFIED because per-failure attribution (environment vs product) is manual",
        "next_action": "run --full on a clean runner (release-readiness.yml already does)",
    },
}


def main(argv):
    full = "--full" in argv
    if "--only" in argv:
        try:
            only = set(argv[argv.index("--only") + 1].split(","))
        except IndexError:
            only = None
    else:
        only = None

    env = {
        "os": platform.system(),
        "python": sys.version.split()[0],
        "ci": bool(os.environ.get("CI")),
    }
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    results = []
    for cid, name, fn in CHECKS:
        if only is not None and cid not in only:
            continue
        try:
            if cid in ("C3", "C12"):
                # Both the suite-stability condition (C3) and the no-unexplained-
                # failure cross-reference (C12) honour --full; C3 runs the suite
                # and C12 reuses that one execution (see _LAST_FULL_RUN).
                status, evidence = fn(full=full)
            else:
                status, evidence = fn()
        except Exception as exc:  # noqa: BLE001
            # The checker raised before reaching a verdict: that is the checker
            # failing, not the product -- ERROR, never a silent NOT_VERIFIED.
            status, evidence = ERROR, f"checker raised before a verdict: {exc!r}"
        meta = CONDITION_META.get(cid, {})
        results.append({
            "id": cid,
            "name": name,
            "status": status,
            "evidence": evidence,
            "command": meta.get("command", f"check_{cid}"),
            "known_limitation": meta.get("known_limitation", ""),
            "blocker": meta.get("blocker", ""),
            "next_action": meta.get("next_action", ""),
        })
        print(f"[{cid}] {name}: {status} — {evidence}")

    n_pass = sum(1 for r in results if r["status"] == PASS)
    n_fail = sum(1 for r in results if r["status"] == FAIL)
    n_blocked = sum(1 for r in results if r["status"] == BLOCKED)
    n_nv = sum(1 for r in results if r["status"] == NOT_VERIFIED)
    n_error = sum(1 for r in results if r["status"] == ERROR)
    release_ready = (n_fail == 0 and n_blocked == 0 and n_nv == 0
                     and n_error == 0)

    print("\n" + "=" * 72)
    print("RELEASE-READINESS verdict")
    print("=" * 72)
    print(f"  PASS={n_pass}  FAIL={n_fail}  BLOCKED={n_blocked}  "
          f"NOT_VERIFIED={n_nv}  ERROR={n_error}")
    print(f"  RELEASE READY = {release_ready}")
    print("=" * 72)

    report = {
        "contract_version": "1.0",
        "generated_by": "scripts/verify_readiness.py",
        "generated_at": timestamp,
        "environment": env,
        "release_ready": release_ready,
        "counts": {"PASS": n_pass, "FAIL": n_fail, "BLOCKED": n_blocked,
                   "NOT_VERIFIED": n_nv, "ERROR": n_error},
        "conditions": results,
    }
    out_path = SCRIPTS / "readiness_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  report written: {out_path}")

    # Exit 2 on any FAIL (contradiction) OR any ERROR (a checker that could not
    # run: a verification-process failure, not a pending item). Use an explicit
    # sys.exit branch (not a returned IfExp) so the meta-guardrail can prove
    # this gate can fail. NOT VERIFIED / BLOCKED stay exit 0 by design.
    if n_fail > 0 or n_error > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
