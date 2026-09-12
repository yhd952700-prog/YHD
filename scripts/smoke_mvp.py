"""端到端 MVP 冒烟验证（Round 64）。

把系统真正拉起来跑一遍主链路，产出可复现证据：

    对话 → 授权 → 真实 LLM 推理 → 记忆/落盘 → 审计 → 驾驶舱遥测 → 画像个性化

前置：
    1. 本机 Ollama 已运行且已拉取 `.env` 指定的模型（默认 ``qwen2.5:3b``）。
    2. gateway 已启动：
       ``.venv/Scripts/python.exe -m uvicorn src.gateway.main:app --port 8011``

用法：
    ``.venv/Scripts/python.exe scripts/smoke_mvp.py [--base http://127.0.0.1:8011]``

退出码：0 = 全部通过；1 = 有项失败。真实 LLM 推理较慢（CPU 上约 10–30s），
故 ``/v1/chat`` 使用较长超时。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple


def _req(method: str, base: str, path: str, body: Optional[dict] = None,
         timeout: float = 180.0) -> Tuple[Optional[int], Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        return exc.code, {"_error": exc.read().decode()[:300]}
    except Exception as exc:  # pragma: no cover - network path
        return None, {"_exc": repr(exc)[:300]}


def _wait_ready(base: str, attempts: int = 40) -> bool:
    for _ in range(attempts):
        status, _ = _req("GET", base, "/v1/ready", timeout=5)
        if status == 200:
            return True
        time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="LiuHao MVP end-to-end smoke")
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    checks: dict[str, bool] = {}

    if not _wait_ready(base):
        print("[FAIL] gateway 未在 40s 内就绪 —— 是否已启动 uvicorn？")
        return 1
    print("[ok] gateway ready")

    status, body = _req("GET", base, "/v1/health")
    checks["health"] = status == 200
    print(f"[{'ok' if status == 200 else 'FAIL'}] GET /v1/health -> {status} {json.dumps(body)[:120]}")

    status, body = _req("GET", base, "/v1/ready")
    checks["ready"] = status == 200
    print(f"[{'ok' if status == 200 else 'FAIL'}] GET /v1/ready -> {status}")

    # --- 真实 LLM 对话 ---
    started = time.time()
    status, body = _req("POST", base, "/v1/chat",
                        {"message": "用一句话介绍你自己，并说明你所在的层级。",
                         "session_id": "smoke-e2e"})
    elapsed = time.time() - started
    ok = status == 200 and isinstance(body, dict) and body.get("status") == "completed"
    checks["chat"] = ok
    print(f"[{'ok' if ok else 'FAIL'}] POST /v1/chat -> {status} in {elapsed:.1f}s")
    if isinstance(body, dict):
        print(f"       status={body.get('status')} agent={body.get('agent')} turn={body.get('turn')}")
        print(f"       reply={str(body.get('reply'))[:160]}")

    status, body = _req("GET", base, "/v1/chat/stats?session_id=smoke-e2e")
    checks["chat_stats"] = status == 200
    print(f"[{'ok' if status == 200 else 'FAIL'}] GET /v1/chat/stats -> {status} "
          f"{json.dumps(body, ensure_ascii=False)[:140] if isinstance(body, dict) else body}")

    # --- 驾驶舱遥测 ---
    status, body = _req("GET", base, "/v1/dashboard/summary")
    has_provider = isinstance(body, dict) and isinstance(body.get("provider"), dict)
    checks["dashboard_summary"] = status == 200 and has_provider
    print(f"[{'ok' if checks['dashboard_summary'] else 'FAIL'}] GET /v1/dashboard/summary -> {status} "
          f"{json.dumps(body, ensure_ascii=False)[:200] if isinstance(body, dict) else body}")

    status, body = _req("GET", base, "/v1/dashboard/activity?limit=3")
    checks["dashboard_activity"] = status == 200
    print(f"[{'ok' if status == 200 else 'FAIL'}] GET /v1/dashboard/activity -> {status}")

    # --- KAREN 画像闭环（写入与消费必须用同一 user_id：主体映射为 user-{id}）---
    user_id = "smoke-karen"
    status, body = _req("PUT", base, "/v1/profile",
                        {"user_id": user_id, "display_name": "靓仔",
                         "preference": {"key": "language", "value": "zh-CN"},
                         "fact": {"key": "timezone", "value": "Asia/Shanghai"}})
    written = body.get("written") if isinstance(body, dict) else None
    checks["profile_write"] = status == 200 and bool(written)
    print(f"[{'ok' if checks['profile_write'] else 'FAIL'}] PUT /v1/profile -> {status} written={written}")

    status, body = _req("GET", base, f"/v1/profile/summary?user_id={user_id}")
    summary = body.get("summary") if isinstance(body, dict) else None
    checks["profile_summary"] = status == 200 and bool(summary)
    print(f"[{'ok' if checks['profile_summary'] else 'FAIL'}] GET /v1/profile/summary -> {status} "
          f"summary={summary!r}")

    status, body = _req("POST", base, "/v1/chat",
                        {"message": "我的时区和常用语言是什么？请直接回答。",
                         "session_id": "smoke-karen-loop", "user_id": user_id})
    reply = str(body.get("reply")) if isinstance(body, dict) else ""
    # 闭环判据：回复里出现写入的画像值（时区/语言）。
    closed = status == 200 and ("Asia/Shanghai" in reply or "zh-CN" in reply)
    checks["karen_closed_loop"] = closed
    print(f"[{'ok' if closed else 'FAIL'}] KAREN 闭环 -> {status} reply={reply[:160]}")

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    print(f"\n=== SMOKE {passed}/{total} passed ===")
    print(json.dumps(checks, ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
