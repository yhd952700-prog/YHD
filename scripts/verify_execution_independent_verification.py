"""Independent verification proof for Verifier.verify.

This script proves that ``Verifier.verify`` no longer trusts the executor's
returned ``output`` dict for file-side effects. It demonstrates:

  1. A capability claiming ``{"written": path}`` WITHOUT actually writing the
     file now FAILS verification (the core anti-self-proving fix).
  2. Writing the file for real makes verification SUCCEED.
  3. Writing the file with WRONG content makes verification FAIL with a
     content-mismatch reason.
  4. A non-file outcome is still verified exactly as before (behavior preserved).

Persistence (audit db / workspace) is redirected to temp so the production
HC-01 audit evidence is never touched.
"""

import os
import sys
import tempfile

# --- Redirect persistence to temp BEFORE importing anything that reads it. ---
os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", "/tmp/liuhao_indep_verify_ws")
os.environ.setdefault("AUDIT_DB_PATH", "/tmp/liuhao_indep_verify.db")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WORKSPACE = os.environ["LIUHAO_WORKSPACE_ROOT"]
os.makedirs(WORKSPACE, exist_ok=True)

from src.kernels.execution import (  # noqa: E402
    ActionResult,
    Task,
    Verifier,
    VerifyResult,
)


def make_task(name="T1", expected_outputs=None, action=None, type_=None):
    """Build a minimal Task for verification tests."""
    return Task(
        id=name,
        goal_id="G1",
        name=name,
        description=name,
        capability_id="filesystem",
        expected_outputs=expected_outputs or {},
    )


def make_result(success=True, output=None, error=None):
    return ActionResult(action_id="a1", success=success, output=output, error=error)


results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def main():
    verifier = Verifier()

    # -----------------------------------------------------------------
    # Test 1: capability CLAIMS a write but never writes the file.
    # This used to pass (self-proving); now it must FAIL.
    # -----------------------------------------------------------------
    missing_path = os.path.join(WORKSPACE, "claimed_but_missing.txt")
    if os.path.exists(missing_path):
        os.remove(missing_path)

    task1 = make_task(
        "T1",
        expected_outputs={"written": missing_path, "content": "hello world"},
    )
    result1 = verifier.verify(
        task1,
        make_result(success=True, output={"written": missing_path}),
    )
    check(
        "T1 claimed write with no artifact on disk => FAILED",
        result1.result is VerifyResult.FAILED and result1.replan_required,
        f"result={result1.result.value}, feedback='{result1.feedback}'",
    )

    # -----------------------------------------------------------------
    # Test 2: file is actually written with correct content => SUCCESS.
    # -----------------------------------------------------------------
    good_path = os.path.join(WORKSPACE, "real_write.txt")
    with open(good_path, "w", encoding="utf-8") as f:
        f.write("hello world")

    task2 = make_task(
        "T2",
        expected_outputs={"written": good_path, "content": "hello world"},
    )
    result2 = verifier.verify(
        task2,
        make_result(success=True, output={"written": good_path}),
    )
    check(
        "T2 real write with correct content => SUCCESS",
        result2.result is VerifyResult.SUCCESS and result2.score == 1.0,
        f"result={result2.result.value}, score={result2.score}",
    )

    # -----------------------------------------------------------------
    # Test 3: file written but WRONG content => FAILED (content mismatch).
    # -----------------------------------------------------------------
    wrong_path = os.path.join(WORKSPACE, "wrong_content.txt")
    with open(wrong_path, "w", encoding="utf-8") as f:
        f.write("WRONG CONTENT")

    task3 = make_task(
        "T3",
        expected_outputs={"written": wrong_path, "content": "hello world"},
    )
    result3 = verifier.verify(
        task3,
        make_result(success=True, output={"written": wrong_path}),
    )
    check(
        "T3 real write with wrong content => FAILED (content mismatch)",
        result3.result is VerifyResult.FAILED and "content mismatch" in result3.feedback,
        f"result={result3.result.value}, feedback='{result3.feedback}'",
    )

    # -----------------------------------------------------------------
    # Test 4: non-file outcome, unchanged behavior => SUCCESS.
    # -----------------------------------------------------------------
    task4 = make_task("T4", expected_outputs={"result": "ok"})
    result4 = verifier.verify(
        task4,
        make_result(success=True, output={"result": "ok"}),
    )
    check(
        "T4 non-file outcome (result=ok) => SUCCESS (behavior preserved)",
        result4.result is VerifyResult.SUCCESS and result4.score == 1.0,
        f"result={result4.result.value}, score={result4.score}",
    )

    # -----------------------------------------------------------------
    # Test 5 (extra): output under "path" key, file absent => FAILED.
    # -----------------------------------------------------------------
    path_key_missing = os.path.join(WORKSPACE, "via_path_key.txt")
    if os.path.exists(path_key_missing):
        os.remove(path_key_missing)
    task5 = make_task("T5", expected_outputs={"path": path_key_missing})
    result5 = verifier.verify(
        task5,
        make_result(success=True, output={"path": path_key_missing}),
    )
    check(
        "T5 claimed write via 'path' key with no artifact => FAILED",
        result5.result is VerifyResult.FAILED and result5.replan_required,
        f"result={result5.result.value}, feedback='{result5.feedback}'",
    )

    failed = [r for r in results if r[0] == "FAIL"]
    print()
    print(f"=== SUMMARY: {len(results) - len(failed)}/{len(results)} passed ===")
    if failed:
        for status, label, detail in failed:
            print(f"  FAILED: {label} {detail}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
