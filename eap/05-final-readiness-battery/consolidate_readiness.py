#!/usr/bin/env python
"""
EAP-05 — Final readiness battery (standard library only).

Consolidates every EAP evidence artifact into ONE honest verdict:

    eap/01-browser-click-through/playwright-report.json  -> P1, P5, P9, P10
    g9b_image_build_report.json                          -> G9 image build/boot/health
    g9_deploy_readiness_report.json                      -> G9 upgrade/rollback + compose
    obs_loop_*_report.json                               -> G9 real alert firing
    regression-report.json                               -> full regression

------------------------------------------------------------------------------
THE ONE RULE THIS SCRIPT EXISTS TO ENFORCE
------------------------------------------------------------------------------
A gate flips to PASS **only** when an evidence file produced on a real host
says the check passed. Missing file / unreadable file / unrecognised shape /
"executed but skipped" are all reported as NOT VERIFIED or BLOCKED-on-host —
**never** as PASS. There is no "default to green" path anywhere below.

This is the counterweight to a very real failure mode in this project: the
temptation to treat "the runbook exists" or "the test was written" as if the
product had been proven. It has not. NOT VERIFIED is a recorded gap.

Exit codes follow the repo's established gate convention:
  0 = every EAP gate is evidence-backed PASS  (PRODUCT ACCEPTANCE = PASS)
  1 = at least one real FAIL
  2 = no FAIL, but something is BLOCKED-on-host / NOT VERIFIED
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_VERSION = 1

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED-on-host"
UNVERIFIED = "NOT VERIFIED"

# The four product gates whose remaining blocker was the browser click-through.
# Each maps to the Playwright spec file that exercises it.
BROWSER_GATES = {
    "P1": "p1_console.spec.ts",
    "P5": "p5_sovereignty.spec.ts",
    "P9": "p9_identity.spec.ts",
    "P10": "p10_workflow.spec.ts",
}

GATE_NAMES = {
    "P1": "AI Employee (hire/pause/resume/remove via UI)",
    "P5": "Human Sovereignty UX (issue/revoke approval via UI)",
    "P9": "Permissions / Identity UX (read-only identity surface)",
    "P10": "Real User Workflow (create goal -> delivered artifact)",
}


# --------------------------------------------------------------------------
# generic evidence classifier
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return str(text).strip().upper().replace(" ", "_")


def _classify_status(raw: str) -> str:
    """Map an arbitrary status string onto PASS / FAIL / UNVERIFIED."""
    s = _norm(raw)
    if s in {"PASS", "PASSED", "OK", "GREEN", "SUCCESS", "EXPECTED"}:
        return PASS
    if "FAIL" in s or s in {"ERROR", "RED"}:
        return FAIL
    if s in {"NOT_VERIFIED", "NOTVERIFIED", "BLOCKED", "BLOCKED-ON-HOST",
             "SKIPPED", "SKIP", "UNKNOWN", "CANNOT_RUN"}:
        return UNVERIFIED
    return UNVERIFIED  # anything unrecognised is treated as unproven


def load_evidence(path: Path | None) -> tuple[dict | None, str]:
    """Load a JSON evidence file. Returns (data, reason_if_unusable)."""
    if path is None:
        return None, "no evidence file was supplied"
    p = Path(path)
    if not p.is_file():
        return None, f"evidence file does not exist: {p}"
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"evidence file unreadable ({exc.__class__.__name__}: {exc})"


#: Fields that identify WHICH script produced an evidence document.
_PRODUCER_KEYS = ("generated_by", "produced_by", "producer", "gate", "script",
                  "source")
#: Fields carrying the producer's process exit status.
_EXIT_KEYS = ("exit_code", "exitcode", "returncode", "rc")


def _provenance_attested(data: dict) -> tuple[bool, str]:
    """Is this document provably written by a gate script that exited 0?

    A bare ``{"status": "PASS"}`` is exactly the shape a CI wrapper produces if
    it swallows a gate's non-zero exit and writes a placeholder, and it is also
    the shape anyone can hand-edit. Trusting it would let a stub mint a
    ``RELEASE READY`` -- the same "trust the script's own report" failure that
    CRITICAL-5 exists to prevent, one layer up.

    So a scalar PASS is accepted only when the document names the script that
    produced it AND records that the script exited 0.
    """
    producer = None
    for key in _PRODUCER_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            producer = value.strip()
            break
    if producer is None:
        return False, (
            "no producer field (%s) -- the document does not say which script "
            "wrote it" % "/".join(_PRODUCER_KEYS)
        )
    if not producer.endswith(".py"):
        return False, "producer %r is not a .py script" % producer

    exit_code = None
    for key in _EXIT_KEYS:
        if key in data:
            exit_code = data[key]
            break
    if exit_code is None:
        return False, (
            "producer %r is named but no exit status is recorded (%s) -- a gate "
            "that does not report how it exited cannot prove it passed"
            % (producer, "/".join(_EXIT_KEYS))
        )
    if isinstance(exit_code, bool) or not isinstance(exit_code, (int, float)):
        return False, "exit status %r is not numeric" % (exit_code,)
    if int(exit_code) != 0:
        return False, "producer %r reported non-zero exit %s" % (producer, exit_code)
    return True, "produced by %s with exit 0" % producer


def classify_check(data: dict | None, reason: str) -> tuple[str, str, list[str]]:
    """Return (status, human_reason, detail_lines) for one evidence document."""
    if data is None:
        return UNVERIFIED, reason, []

    details: list[str] = []

    # 1) document with a list of named checks -> aggregate them
    for key in ("checks", "links", "results", "steps"):
        block = data.get(key)
        if isinstance(block, list) and block:
            worst = PASS
            inconclusive = False
            for item in block:
                if not isinstance(item, dict):
                    continue
                label = item.get("name") or item.get("id") or item.get("title") or "?"
                raw = item.get("status", item.get("result", ""))
                st = _classify_status(raw)
                details.append(f"  - {label}: {raw} -> {st}")
                if st == FAIL:
                    worst = FAIL
                elif st == UNVERIFIED and worst != FAIL:
                    inconclusive = True
            if worst == FAIL:
                return FAIL, "at least one check reported FAIL", details
            if inconclusive:
                return (UNVERIFIED,
                        "some checks did not actually execute (environment gap)",
                        details)
            return PASS, "all checks reported PASS", details

    # 2) document with a scalar verdict field
    for key in ("status", "verdict", "result", "overall", "summary_status"):
        if key in data and isinstance(data[key], (str, int, float)):
            st = _classify_status(data[key])
            details.append(f"  - {key} = {data[key]!r} -> {st}")
            # Only a PASS can mint trust, so only a PASS has to prove its
            # provenance. A document reporting FAIL or NOT_VERIFIED is already
            # withholding trust; demanding attestation there would turn an
            # honest failure into an argument about metadata.
            if st == PASS:
                ok, why = _provenance_attested(data)
                if not ok:
                    details.append(f"  - provenance: REJECTED -- {why}")
                    return (UNVERIFIED,
                            "evidence claims PASS but its provenance is not "
                            "attested (" + why + ") -- an unattributed scalar "
                            "verdict cannot mint a pass",
                            details)
                details.append(f"  - provenance: {why}")
            msg = "evidence reports " + st
            return st, msg, details

    return (UNVERIFIED,
            "evidence file recognised as JSON but its verdict shape is unrecognised "
            "-- refusing to assume a pass",
            details)


# --------------------------------------------------------------------------
# Playwright (browser click-through) evidence
# --------------------------------------------------------------------------
def collect_playwright_statuses(data: dict) -> dict[str, list[str]]:
    """Walk a Playwright JSON report and group test statuses by spec file.

    Playwright JSON layout differs slightly across versions, so walk it
    recursively looking for `specs` -> `tests` rather than assuming one shape.
    Returns {spec_filename: [test_status, ...]}.
    """
    out: dict[str, list[str]] = {}

    def visit(node) -> None:
        if isinstance(node, dict):
            specs = node.get("specs")
            if isinstance(specs, list):
                for spec in specs:
                    if not isinstance(spec, dict):
                        continue
                    fname = spec.get("file") or spec.get("title") or "?"
                    tests = spec.get("tests")
                    if isinstance(tests, list):
                        for t in tests:
                            if isinstance(t, dict):
                                out.setdefault(str(fname), []).append(
                                    str(t.get("status", "")))
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(data)
    return out


def gate_from_browser(data: dict | None, reason: str, spec_name: str) -> tuple[str, str, list[str]]:
    if data is None:
        return BLOCKED, f"{reason} (browser click-through never ran here)", []
    statuses_by_spec = collect_playwright_statuses(data)
    # match on basename so either "specs/p1_console.spec.ts" or "./p1...spec.ts" hits
    target = next((k for k in statuses_by_spec
                   if Path(k).name.replace("\\", "/").split("/")[-1] == spec_name), None)
    if target is None:
        return BLOCKED, f"no Playwright results found for {spec_name}", []

    sts = statuses_by_spec[target]
    executed = [s for s in sts if s not in ("skipped",)]
    failed = [s for s in executed if s != "expected"]
    details = [f"  - {spec_name}: {len(sts)} test(s), statuses={sorted(set(sts))}"]

    if failed:
        return FAIL, f"{len(failed)} Playwright test(s) did not pass", details
    if not executed:
        return UNVERIFIED, f"all tests in {spec_name} were SKIPPED (nothing executed)", details
    skipped = len(sts) - len(executed)
    if skipped:
        details.append(f"  - note: {skipped} intentionally skipped (documented seed requirement)")
    return PASS, f"{len(executed)} Playwright test(s) passed", details


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def read_product_readiness(path: Path | None) -> list[str]:
    if path is None or not Path(path).is_file():
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = []
    for line in text.splitlines():
        if re.search(r"PRODUCT ACCEPTANCE|RELEASE READINESS", line):
            lines.append(line.strip())
    return lines[:12]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EAP-05 final readiness battery.")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--g9-report", default="", help="verify_image_build.py JSON (G9B_REPORT)")
    ap.add_argument("--deploy-report", default="", help="verify_deploy_readiness.py JSON (G9_REPORT)")
    ap.add_argument("--obs-up-report", default="", help="verify_observability_loop.py --force 404 JSON")
    ap.add_argument("--obs-down-report", default="", help="verify_observability_loop.py --force down JSON")
    ap.add_argument("--regression-report", default="", help="eap/04 run-regression.py --json output")
    ap.add_argument("--browser-report", default="", help="eap/01 Playwright JSON report")
    ap.add_argument("--product-readiness", default="docs/product/PRODUCT-READINESS.md")
    ap.add_argument("--out-dir", default=".", help="where to write the consolidated report")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def maybe(x: str) -> Path | None:
        if not x:
            return None
        p = Path(x)
        return p if p.is_absolute() else repo_root / p

    browser_data, browser_reason = load_evidence(maybe(args.browser_report))
    g9_data, g9_reason = load_evidence(maybe(args.g9_report))
    deploy_data, deploy_reason = load_evidence(maybe(args.deploy_report))
    obs_up_data, obs_up_reason = load_evidence(maybe(args.obs_up_report))
    obs_down_data, obs_down_reason = load_evidence(maybe(args.obs_down_report))
    reg_data, reg_reason = load_evidence(maybe(args.regression_report))

    results: dict[str, dict] = {}

    # --- product gates from the browser click-through ----------------------
    for gate, spec in BROWSER_GATES.items():
        status, reason, details = gate_from_browser(browser_data, browser_reason, spec)
        results[gate] = {
            "gate": gate, "name": GATE_NAMES[gate], "status": status,
            "reason": reason, "details": details,
            "evidence": str(maybe(args.browser_report) or "(none)"),
        }

    # --- support checks ----------------------------------------------------
    support_specs = [
        ("G9-image", "Image build + boot + health (real container)",
         classify_check(g9_data, g9_reason), str(maybe(args.g9_report) or "(none)")),
        ("G9-upgrade-rollback", "Alembic upgrade/downgrade + staging compose",
         classify_check(deploy_data, deploy_reason), str(maybe(args.deploy_report) or "(none)")),
        ("OBS-firing-up", "Alert actually fires end-to-end (app UP, 404 burst)",
         classify_check(obs_up_data, obs_up_reason), str(maybe(args.obs_up_report) or "(none)")),
        ("OBS-firing-down", "ServiceDown alert fires when the app stops",
         classify_check(obs_down_data, obs_down_reason), str(maybe(args.obs_down_report) or "(none)")),
        ("REGRESSION", "Full pytest regression (fail-closed)",
         classify_check(reg_data, reg_reason), str(maybe(args.regression_report) or "(none)")),
    ]
    for name, desc, (status, reason, details), ev in support_specs:
        results[name] = {"check": name, "name": desc, "status": status,
                         "reason": reason, "details": details, "evidence": ev}

    # --- roll-up -----------------------------------------------------------
    gate_statuses = [results[g]["status"] for g in BROWSER_GATES]
    if any(s == FAIL for s in gate_statuses):
        product = "FAIL"
    elif all(s == PASS for s in gate_statuses):
        product = "PASS"
    else:
        product = "NOT READY"

    g9_keys = ["G9-image", "G9-upgrade-rollback", "OBS-firing-up"]
    g9_statuses = [results[k]["status"] for k in g9_keys]
    if any(s == FAIL for s in g9_statuses) or product == "FAIL":
        release = "NOT RELEASE READY (a gate FAILED)"
    elif product == "PASS" and all(s == PASS for s in g9_statuses):
        release = "RELEASE READY"
    else:
        release = ("NOT RELEASE READY "
                   "(environment-limited: gates BLOCKED-on-host / NOT VERIFIED)")

    carry = read_product_readiness(maybe(args.product_readiness))

    generated = datetime.now(timezone.utc).isoformat()
    md = _render_markdown(generated, results, product, release, carry, args)
    (out_dir / "EAP-EVIDENCE-REPORT.md").write_text(md, encoding="utf-8")

    json_out = {
        "report_version": REPORT_VERSION,
        "generated_utc": generated,
        "product_acceptance": product,
        "release_readiness": release,
        "gates": results,
        "carry_over_from_product_readiness": carry,
    }
    (out_dir / "EAP-EVIDENCE-REPORT.json").write_text(
        json.dumps(json_out, indent=2), encoding="utf-8")

    print("=" * 74)
    print("EAP FINAL READINESS BATTERY")
    print("=" * 74)
    for key in list(BROWSER_GATES) + g9_keys + ["OBS-firing-down", "REGRESSION"]:
        r = results[key]
        print(f"  {r['status']:<16} {key:<22} {r['reason']}")
    print("-" * 74)
    print(f"  PRODUCT ACCEPTANCE : {product}")
    print(f"  RELEASE READINESS  : {release}")
    print(f"  reports -> {out_dir / 'EAP-EVIDENCE-REPORT.md'}"
          f" (+ .json)")
    print("=" * 74)

    if any(r["status"] == FAIL for r in results.values()):
        return 1
    if all(r["status"] == PASS for r in results.values()):
        return 0
    return 2


def _render_markdown(generated, results, product, release, carry, args) -> str:
    rows = []
    order = list(BROWSER_GATES) + ["G9-image", "G9-upgrade-rollback",
                                   "OBS-firing-up", "OBS-firing-down", "REGRESSION"]
    for key in order:
        r = results[key]
        rows.append(f"| `{key}` | {r['name']} | **{r['status']}** | {r['reason']} |")
    table = "\n".join(rows)

    carry_block = "\n".join(f"- `{line}`" for line in carry) if carry else \
        "- (PRODUCT-READINESS.md not readable at the supplied path)"

    return f"""# External Acceptance Package — Consolidated Evidence Report

Generated (UTC): `{generated}`
Branch: `p36`

> **The rule this report obeys:** a gate shows `PASS` **only** if an evidence
> file produced on a real host says the check passed. Missing / unreadable /
> unrecognised evidence is recorded as `NOT VERIFIED` or `BLOCKED-on-host` —
> **never** upgraded to a pass. `NOT VERIFIED != FAIL`, but equally
> `environment missing != RELEASE READY`.

---

## Verdict

| Item | Value |
|------|-------|
| **PRODUCT ACCEPTANCE** | **{product}** |
| **RELEASE READINESS** | **{release}** |

---

## Per-gate evidence

| Check | Description | Status | Reason |
|-------|-------------|--------|--------|
{table}

### Evidence sources supplied

| Evidence | Path |
|----------|------|
| Browser click-through (P1/P5/P9/P10) | `{args.browser_report or '(none)'}` |
| G9 image build/boot/health | `{args.g9_report or '(none)'}` |
| G9 upgrade/rollback | `{args.deploy_report or '(none)'}` |
| Observability firing (up) | `{args.obs_up_report or '(none)'}` |
| Observability firing (down) | `{args.obs_down_report or '(none)'}` |
| Full regression | `{args.regression_report or '(none)'}` |

---

## Carry-over from `docs/product/PRODUCT-READINESS.md`

These lines are context only; they do **not** flip any gate in this report.

{carry_block}

---

## How to interpret a non-PASS status

- `BLOCKED-on-host` — the check cannot run in this environment (no Docker
  daemon / no headless browser / no prometheus binaries). Portable to a capable
  host. **Not** a product defect.
- `NOT VERIFIED` — the check could run but has not been executed yet, or the
  evidence file's shape could not be recognised.
- `FAIL` — a check executed and reported a real failure. Investigate before any
  further promotion.
"""


if __name__ == "__main__":
    sys.exit(main())
