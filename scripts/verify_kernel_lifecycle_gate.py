#!/usr/bin/env python
"""内核生命周期门禁：PAUSED / STOPPED 必须**真的**拒绝工作。

为什么这个门禁存在
------------------
``src/kernels/_base.py`` 定义了 ``KernelLifecycle`` 状态机，``_registry.py`` 驱动它，
``src/gateway/main.py`` 在 lifespan 里调 ``initialize_all()``，``/v1/kernels/{name}/pause``
让主权主体能把任一内核置为 ``PAUSED``。看上去齐备。

实测（本次修复前，见 ``docs/autonomous/`` 与提交说明）**整套状态机没有任何一处
被读取**：14 个内核的 ``pause()`` / ``shutdown()`` 只翻转一个字段，而
``publish()`` / ``check_rbac()`` / ``evaluate()`` 等业务方法**从不看它**。
探针实测（修复前）：``EventBus`` 置 ``PAUSED`` 后 ``publish()`` 仍被接受**并且
真的把事件投递给了订阅者**；``shutdown()`` 之后同样照常投递。

也就是说，主权主体在事故里按下「暂停」，屏幕诚实显示 ``paused``，而内核**照常
干活** —— 这是与「围栏未武装」同类的**指示灯撒谎**缺陷，而且更危险：它出现在
一条**已经建好、已经接 HTTP、已经过人类闸门**的控制路径上。

唯一一个真正兑现门禁的是 ``NetworkBus.route()``（``690273e0`` 修的），
本次修复把同一语义推广到 event / security / policy，并给 ``_base.py`` 加上
唯一真源 ``is_servable()`` / ``assert_servable()``，取代 14 份复制粘贴的
``pause``/``resume``。

四组检查
--------
A. **门禁真拒收**：``PAUSED`` / ``STOPPED`` 下，event ``publish`` 抛
   ``KernelNotServableError`` 且**订阅者零调用**（不是「记一条日志继续发」）。
B. **拒绝方向正确**：security / policy 在 ``PAUSED`` 下返回 **DENY**（值），
   不是抛异常。方向很重要 —— 这两个内核一旦把「拒绝」变成异常而调用方宽泛
   catch，失效方向会**恰好是 fail-open**。
C. **READY 基线是 ALLOW**（反证）：门禁生效必须表现为「READY→ALLOW、
   PAUSED→DENY」。若 READY 本来就 DENY，该检查证明不了任何东西 —— 所以
   这一组同时钉住基线。
D. **resume 真恢复**：``resume()`` 后投递与放行都回来。门禁不是单向闸刀。

反证（counter-proof）
--------------------
``--counter-proof`` 会**临时**把 ``is_servable`` 换成恒真并重跑 A 组。
门禁若为真，A 组此时**必须变红** —— 否则说明这些断言不依赖门禁，是空转。
本脚本不改仓库文件。

退出码：0 全绿 / 1 有检查失败 / 2 探针自身出错（不算证据）/ 3 反证未变红。

用法::

    python scripts/verify_kernel_lifecycle_gate.py
    python scripts/verify_kernel_lifecycle_gate.py --counter-proof
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 审计落盘必须离开工作树（conftest 同样的约束：工作树不能被测试写脏）。
_TMP = Path(tempfile.mkdtemp(prefix="lh_lifecycle_gate_"))
os.environ.setdefault("AUDIT_DB_PATH", str(_TMP / "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB", str(_TMP / "audit_store.db"))


def _cleanup() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    return bool(ok)


# ---------------------------------------------------------------- group A
def group_a_refuses() -> dict:
    """A/B/D：PAUSED 真拒收、拒绝方向是值、resume 真恢复。"""
    from src.kernels._base import KernelNotServableError
    from src.kernels.event import Event, EventBus

    out: dict = {}
    bus = EventBus()
    bus.initialize()
    seen: list = []
    bus.subscribe("probe.event", lambda ev: seen.append(ev))

    # READY baseline: one real delivery. Without it the PAUSED check below
    # could not distinguish "the gate suppressed delivery" from "this
    # subscription never receives anything".
    bus.publish(Event(type="probe.event", source="probe"))
    out["ready_delivered"] = len(seen) == 1

    # --- A: PAUSED 真拒收，且订阅者零新增调用 ---
    bus.pause()
    try:
        bus.publish(Event(type="probe.event", source="probe"))
        out["paused_publish"] = "ACCEPTED (gate missing)"
        out["paused_refused"] = False
    except KernelNotServableError as exc:
        out["paused_publish"] = f"REFUSED ({type(exc).__name__})"
        out["paused_refused"] = True
    out["paused_subscriber_calls"] = len(seen)  # 必须仍是 1
    out["paused_no_delivery"] = len(seen) == 1
    out["paused_dead_letter_recorded"] = len(bus._dead_letters) >= 1

    # --- D: resume 真恢复 ---
    bus.resume()
    bus.publish(Event(type="probe.event", source="probe"))
    out["resume_restores_delivery"] = len(seen) == 2
    return out


# ---------------------------------------------------------------- group C
def group_c_baseline() -> dict:
    """C：READY 基线必须是真 ALLOW，否则 A/B/D 证明不了「是门禁在拒绝」。"""
    from src.kernels.policy import PolicyEngine
    from src.kernels.security import AccessDecision, RBACRole, RBACRule, SecurityEngine

    out: dict = {}

    sec = SecurityEngine()
    sec.initialize()
    sec._rbac_rules["probe:read"] = RBACRule(
        id="probe-rule", role=RBACRole.ADMIN, permission="probe:read"
    )
    sec.grant_rbac_role("alice", RBACRole.ADMIN, reason="probe")
    out["security_ready_allows"] = (
        sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW
    )

    sec.pause()
    out["security_paused_decision"] = sec.check_rbac("alice", "probe:read", "L1").value
    out["security_paused_is_deny"] = out["security_paused_decision"] == "deny"
    # 拒绝必须是**返回值**而不是异常：fail-closed 方向。
    out["security_refusal_is_value_not_raise"] = isinstance(
        out["security_paused_decision"], str
    )
    out["security_decide_access_paused"] = sec.decide_access(
        "alice", "probe:read", "L1"
    )["decision"]
    sec.resume()
    out["security_resume_restores_allow"] = (
        sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW
    )

    pol = PolicyEngine()
    pol.initialize()
    ctx = {"actor": {"type": "human", "id": "probe", "verified": True}}
    out["policy_ready_decision"] = pol.evaluate(dict(ctx)).decision.value
    pol.pause()
    pd = pol.evaluate(dict(ctx))
    out["policy_paused_decision"] = pd.decision.value
    out["policy_paused_is_deny"] = pd.decision.value == "deny"
    out["policy_paused_traceable"] = any(
        "lifecycle-gate" in t for t in pd.traceability
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--counter-proof",
        action="store_true",
        help="把 is_servable 换成恒真重跑 A 组，门禁必须因此变红",
    )
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    probe_error = False
    try:
        if args.counter_proof:
            # 反证：让门禁恒真。若 A 组仍然全绿，说明这些断言与门禁无关。
            import src.kernels._base as base
            import src.kernels.event as ev_mod
            import src.kernels.policy as pol_mod
            import src.kernels.security as sec_mod

            base.is_servable = lambda kernel: True  # type: ignore[assignment]
            for mod in (ev_mod, pol_mod, sec_mod):
                mod.is_servable = lambda kernel: True  # type: ignore[assignment]

            a = group_a_refuses()
            still_green = bool(a.get("paused_refused")) and a.get("paused_no_delivery")
            _cleanup()
            if args.json:
                print(json.dumps({"counter_proof": a, "still_green": still_green}))
            else:
                print("反证组（is_servable 恒真）:")
                print(json.dumps(a, indent=2, ensure_ascii=False))
            if still_green:
                print(
                    "\nFAIL: 摘掉门禁后 A 组仍然全绿 —— 断言与门禁无关，是空转。"
                )
                return 3
            print("\nPASS: 摘掉门禁后 A 组变红 —— 断言确实依赖门禁。")
            return 0

        a = group_a_refuses()
        c = group_c_baseline()

        check(a["ready_delivered"], "A.ready 基线确实投递", str(a["ready_delivered"]))
        check(
            a["paused_refused"],
            "A.PAUSED 下 publish 被拒收",
            a["paused_publish"],
        )
        check(
            a["paused_no_delivery"],
            "A.PAUSED 下订阅者零调用（真拒收而非记日志）",
            f"calls={a['paused_subscriber_calls']}",
        )
        check(
            a["paused_dead_letter_recorded"],
            "A.拒收被记为 dead letter（拒绝可见）",
            f"dead_letters={a['paused_dead_letter_recorded']}",
        )
        check(
            a["resume_restores_delivery"],
            "D.resume 后投递恢复",
            f"calls={a['paused_subscriber_calls']}->2",
        )
        check(
            c["security_ready_allows"],
            "C.security READY 基线是 ALLOW（反证前提）",
            str(c["security_ready_allows"]),
        )
        check(
            c["security_paused_is_deny"],
            "B.security PAUSED 返回 DENY",
            c["security_paused_decision"],
        )
        check(
            c["security_refusal_is_value_not_raise"],
            "B.security 拒绝是返回值而非异常（避免 fail-open）",
            "",
        )
        check(
            c["security_decide_access_paused"] == "deny",
            "B.security decide_access PAUSED 也 DENY",
            c["security_decide_access_paused"],
        )
        check(
            c["security_resume_restores_allow"],
            "D.security resume 后放行恢复",
            "",
        )
        check(
            c["policy_paused_is_deny"],
            "B.policy PAUSED 返回 DENY",
            c["policy_paused_decision"],
        )
        check(
            c["policy_paused_traceable"],
            "B.policy 拒绝带 lifecycle-gate 溯源",
            "",
        )
    except Exception as exc:  # noqa: BLE001 - 探针自身错误不是证据
        probe_error = True
        if not args.json:
            print(f"PROBE ERROR: {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()
    finally:
        _cleanup()

    if probe_error:
        return 2

    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    if args.json:
        print(json.dumps(
            {
                "passed": passed,
                "total": total,
                "checks": [
                    {"ok": ok, "name": n, "detail": d} for ok, n, d in results
                ],
            },
            indent=2,
            ensure_ascii=False,
        ))
    else:
        for ok, name, detail in results:
            mark = "PASS" if ok else "FAIL"
            suffix = f"  [{detail}]" if detail else ""
            print(f"{mark}  {name}{suffix}")
        print(f"\n{passed}/{total} 检查通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
