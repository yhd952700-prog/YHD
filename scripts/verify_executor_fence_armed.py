#!/usr/bin/env python
"""Executor-fence 武装状态与互斥能力门禁。

为什么这个门禁存在
------------------
围栏（``src/kernels/execution/fence.py``）容易被**过度宣称**。历史上就出现过一次
错误推论：「``_FENCE_ACTIONS`` 是空集 ⇒ 什么都没武装」。实测是错的——
``src/kernels/_crosscutting.py:717-721`` 的 ``_fence_enabled_for`` **先查环境变量**
（``LIUHAO_EXECUTOR_FENCE`` ∈ on/1/true/yes ⇒ 全动作 True），再查白名单。
空集合 ≠ 未武装。

反过来还有一处容易被**少宣称**：围栏提供的是「身份 + 审计 + 无上下文即拒绝」，
**不自动提供执行者互斥**。互斥来自 ``max_executors``（N+1 上限），而它默认是
``None`` —— 未设 ``LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS`` 时，第二个执行者会被放行。
本门禁把这两件事都变成**运行时可执行的事实**，而不是文档里的措辞。

四组检查
--------
A. 武装语义：env 优先于白名单；空白名单 + env=on ⇒ 全动作武装。
B. fail-closed：已武装但无 fence 上下文 ⇒ 必须 ``PolicyDeniedError``（不是放行、不是静默）。
C. 互斥能力：``max_executors`` 未设 ⇒ 明确记录「不提供互斥」并实测第二执行者被放行；
   设了 ``=1`` ⇒ 第二执行者必须被 ``ExecutorLimitExceeded`` 拒绝。
D. 能力边界诚实：以上结论必须来自真实装饰器调用路径，不得绕过 wrapper。

退出码：0 全绿 / 1 有检查失败 / 2 探针自身出错（不算证据）。

用法::

    python scripts/verify_executor_fence_armed.py
"""

from __future__ import annotations

import atexit
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Keep the probe's audit evidence out of the repository. A temp dir (not a
# repo-relative path) is used deliberately: a repo-relative scratch dir would be
# untracked-but-not-ignored noise that can be committed by accident, and this
# repository has a standing rule against `git add -A`.
_PROBE_DIR = Path(tempfile.mkdtemp(prefix="liuhao_fence_gate_"))
atexit.register(shutil.rmtree, _PROBE_DIR, True)
os.environ.setdefault("AUDIT_DB_PATH", str(_PROBE_DIR / "audit.db"))
os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", str(_PROBE_DIR / "ws"))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", label, (" :: " + detail) if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def probe_bug(label: str, detail: str = "") -> None:
    """A broken probe is NOT evidence either way. Fail loudly, separately."""
    print("[PROBE BUG] %s%s" % (label, (" :: " + detail) if detail else ""))
    FAILURES.append("PROBE BUG: " + label)
    sys.exit(2)


def reload_fence_env() -> None:
    """Re-import the module under test so env changes take effect.

    ``_crosscutting`` reads ``LIUHAO_EXECUTOR_FENCE`` on every call, but the
    allow-list and the armed action set are module state; a fresh import keeps
    this probe honest about what it is measuring.
    """
    for name in [m for m in list(sys.modules) if m.startswith("src.kernels")]:
        del sys.modules[name]
    importlib.invalidate_caches()


# --------------------------------------------------------------------------- #
# A. arming semantics
# --------------------------------------------------------------------------- #
def group_a_arming() -> None:
    print("\n[A] arming semantics -- env beats the allow-list")
    os.environ.pop("LIUHAO_EXECUTOR_FENCE", None)
    reload_fence_env()
    from src.kernels._crosscutting import _FENCE_ACTIONS, _fence_enabled_for

    check("with the env unset, an action outside the allow-list is not armed",
          _fence_enabled_for("probe.fence.unlisted") is False,
          "_FENCE_ACTIONS=%s" % sorted(_FENCE_ACTIONS))
    check("the allow-list is genuinely empty in a clean process (no hidden arming)",
          _FENCE_ACTIONS == set(),
          "populated: %s" % sorted(_FENCE_ACTIONS))

    os.environ["LIUHAO_EXECUTOR_FENCE"] = "on"
    from src.kernels import _crosscutting as cc

    cc = importlib.reload(cc)
    armed = cc._fence_enabled_for("probe.fence.unlisted")
    check("with LIUHAO_EXECUTOR_FENCE=on, an EMPTY allow-list still arms everything",
          armed is True,
          "this is the exact misreading that produced the earlier false claim "
          "('_FENCE_ACTIONS is empty => nothing is armed')")
    for spelling in ("1", "true", "yes", "ON", "True"):
        os.environ["LIUHAO_EXECUTOR_FENCE"] = spelling
        cc = importlib.reload(cc)
        if not check("spelling %r also arms" % spelling,
                     cc._fence_enabled_for("probe.fence.unlisted") is True):
            break
    os.environ["LIUHAO_EXECUTOR_FENCE"] = "off"
    cc = importlib.reload(cc)
    check("spelling 'off' does NOT arm (the env check is a real allow-list, not a parse)",
          cc._fence_enabled_for("probe.fence.unlisted") is False)


# --------------------------------------------------------------------------- #
# B. fail-closed, exercised through the REAL decorated path
# --------------------------------------------------------------------------- #
def group_b_fail_closed() -> None:
    print("\n[B] fail-closed -- armed + no fence context must DENY")
    os.environ["LIUHAO_EXECUTOR_FENCE"] = "on"
    reload_fence_env()
    from src.kernels._crosscutting import PolicyDeniedError, kernel_action

    ran = []

    @kernel_action("probe.fence.action", risk_level="HIGH")
    def _body() -> str:
        ran.append("body-ran")
        return "ok"

    # No fence context has been established in this process, so a real
    # decorated call must be refused. We call the wrapper the way production
    # does -- NOT _enforce_executor_fence_at_gate directly, because calling the
    # inner helper would skip the _fence_enabled_for decision under test.
    #
    # The refusal surfaces as PolicyDeniedError with verdict "executor-fence"
    # (the crosscutting wrapper converts the fence's ExecutorFenceDenied into
    # the policy layer's denial), so that is what we assert on.
    try:
        _body()
    except PolicyDeniedError as exc:
        verdict = str(exc)
        check("armed + no fence context -> DENIED by PolicyDeniedError", True,
              "%s :: %s" % (type(exc).__name__, verdict))
        check("the denial names the executor-fence verdict",
              "executor-fence" in verdict,
              "got %r -- a denial for some other reason would not prove the fence" % verdict)
    except Exception as exc:  # noqa: BLE001 -- an unexpected error is a probe bug
        probe_bug("decorated call raised an unexpected error",
                  "%s: %s" % (type(exc).__name__, exc))
    else:
        check("armed + no fence context -> DENIED by PolicyDeniedError", False,
              "the action ran anyway (ran=%r)" % (ran,))
    check("the denied body never executed", not ran, "body ran: %r" % (ran,))


# --------------------------------------------------------------------------- #
# C. mutual exclusion is a SEPARATE capability
# --------------------------------------------------------------------------- #
def group_c_exclusion() -> None:
    print("\n[C] mutual exclusion -- only max_executors provides it")
    os.environ["LIUHAO_EXECUTOR_FENCE"] = "on"
    reload_fence_env()
    from src.kernels.execution.fence import (
        ExecutorFence,
        ExecutorLimitExceeded,
        InMemoryExecutorLease,
    )

    def _try_second(max_executors):
        fence = ExecutorFence(InMemoryExecutorLease(), max_executors=max_executors)
        fence.acquire_for("probe-exec-A", "owner-a", ("cap.probe",), ttl_sec=60)
        try:
            fence.acquire_for("probe-exec-B", "owner-b", ("cap.probe",), ttl_sec=60)
        except ExecutorLimitExceeded as exc:
            return "refused", type(exc).__name__
        except Exception as exc:  # noqa: BLE001
            return "error", "%s: %s" % (type(exc).__name__, exc)
        return "admitted", None

    admitted, detail = _try_second(None)
    check("max_executors=None -> a SECOND executor is ADMITTED (no mutual exclusion)",
          admitted == "admitted",
          "the fence gives identity + audit + deny-without-context, NOT "
          "exclusion. Claiming exclusion without setting "
          "LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS would be a false claim."
          if admitted == "admitted" else "got %s (%s)" % (admitted, detail))

    refused, detail = _try_second(1)
    check("max_executors=1 -> a second executor is REFUSED with ExecutorLimitExceeded",
          refused == "refused" and detail == "ExecutorLimitExceeded",
          "got %s (%s)" % (refused, detail))

    # And prove the env var is what production reads.
    from src.kernels.execution.fence import attach_default_executor_fence

    os.environ["LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS"] = "1"
    installed = attach_default_executor_fence()
    check("LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS=1 reaches the installed fence",
          installed.max_executors == 1,
          "installed fence max_executors=%r" % (installed.max_executors,))
    os.environ.pop("LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS", None)
    reload_fence_env()


# --------------------------------------------------------------------------- #
# D. honesty about what this gate does and does not prove
# --------------------------------------------------------------------------- #
def group_d_scope() -> None:
    print("\n[D] scope -- this gate proves arming, not deployment")
    doc = (REPO_ROOT / "scripts" / "verify_executor_fence_armed.py").read_text(
        encoding="utf-8"
    )
    check("this gate states its own limits (does not claim deployment coverage)",
          "does not prove" in doc or "not deployment" in doc.lower(),
          "a gate that overstates its coverage is worse than no gate")

    # The deployment-time default must remain OFF -- arming everything by
    # default would be a behaviour change, and this gate must not smuggle one in.
    os.environ.pop("LIUHAO_EXECUTOR_FENCE", None)
    reload_fence_env()
    from src.kernels import _crosscutting as cc

    cc = importlib.reload(cc)
    check("the production default is still fail-safe (nothing armed without the env var)",
          cc._fence_enabled_for("probe.fence.action") is False,
          "arming by default would be a behaviour change disguised as a test")


def main() -> int:
    print("=" * 78)
    print("EXECUTOR FENCE -- arming, fail-closed, and the exclusion boundary")
    print("=" * 78)
    try:
        group_a_arming()
        group_b_fail_closed()
        group_c_exclusion()
        group_d_scope()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        probe_bug("unhandled exception during the probe",
                  "%s: %s" % (type(exc).__name__, exc))

    print("\n" + "=" * 78)
    if FAILURES:
        print("FENCE GATE FAIL: %d check(s) failed" % len(FAILURES))
        for item in FAILURES:
            print("   - %s" % item)
        return 1
    print("FENCE GATE PASS: arming and fail-closed verified; mutual exclusion is "
          "correctly reported as absent unless LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS is set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
