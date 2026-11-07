"""Reproduce the metric-cardinality leak and prove the fix is bounded.

Defect (22-minute soak): labelling HTTP metrics by the RAW request path meant
every new dynamic id created a new ``(method, endpoint)`` label set. 18 -> 23
combinations in 22 minutes, all of them id paths; each new combination added
~16 exposition lines (11 histogram buckets + 5 summaries) and ~8.4 KB of text,
and process handles rose in lockstep (869 -> 912).

This script drives the SAME production instrumentation entry point the gateway
middleware uses (``src.observability.metrics.record_http_request``) and asserts:

  1. 200 distinct ids on two dynamic routes add ZERO new
     ``(method, endpoint)`` combinations;
  2. the Prometheus exposition text does not grow with the number of ids;
  3. process handles do not grow with the number of ids;
  4. 100 arbitrary unknown paths converge into exactly ONE sentinel label.

Run:
    .venv\\Scripts\\python.exe scripts\\repro_metrics_cardinality.py
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import generate_latest  # noqa: E402

from src.observability.metrics import (  # noqa: E402
    REGISTRY,
    UNMATCHED_ENDPOINT_LABEL,
    record_http_request,
)

REQUESTS_PER_ROUTE = 200
UNKNOWN_PATHS = 100

app = FastAPI()


@app.get("/v1/goals/{goal_id}")
async def get_goal(goal_id: str):
    return {"goal_id": goal_id}


@app.post("/v1/goals/{goal_id}/tasks/{task_id}/retry")
async def retry_task(goal_id: str, task_id: str):
    return {"goal_id": goal_id, "task_id": task_id}


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    # The exact production path: bounded endpoint label + metric emission.
    record_http_request(
        request.method, request.scope, response.status_code, time.time() - started
    )
    return response


def endpoint_combinations() -> set:
    """Distinct ``(method, endpoint)`` label sets on ``http_requests_total``."""
    combos = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            # prometheus_client names the Counter family ``http_requests``
            # while the sample itself is ``http_requests_total``.
            if sample.name != "http_requests_total":
                continue
            combos.add((sample.labels.get("method"), sample.labels.get("endpoint")))
    return combos


def exposition_bytes() -> int:
    return len(generate_latest(REGISTRY))


def process_handles() -> int:
    """Best-effort OS handle count (never raises; -1 = unavailable)."""
    try:
        import psutil  # available in the project venv

        return int(psutil.Process().num_handles())
    except Exception:
        pass
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            count = ctypes.c_ulong(0)
            ok = kernel32.GetProcessHandleCount(
                ctypes.c_void_p(kernel32.GetCurrentProcess()),
                ctypes.byref(count),
            )
            return int(count.value) if ok else -1
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return -1


def main() -> int:
    client = TestClient(app)

    # Warm-up: materialise exactly one label set per dynamic route.
    client.get("/v1/goals/warmup")
    client.post("/v1/goals/warmup/tasks/warmup/retry")

    before_combos = endpoint_combinations()
    before_bytes = exposition_bytes()
    before_handles = process_handles()

    for i in range(REQUESTS_PER_ROUTE):
        client.get(f"/v1/goals/goal-{i:04d}")
        client.post(f"/v1/goals/goal-{i:04d}/tasks/task-{i:04d}/retry")

    after_combos = endpoint_combinations()
    after_bytes = exposition_bytes()
    after_handles = process_handles()

    print("[metrics] dynamic-id workload: "
          f"{2 * REQUESTS_PER_ROUTE} requests over {REQUESTS_PER_ROUTE} distinct ids")
    print(f"[metrics] (method, endpoint) combinations: "
          f"{len(before_combos)} -> {len(after_combos)}")
    print(f"[metrics] exposition bytes: {before_bytes} -> {after_bytes} "
          f"(delta {after_bytes - before_bytes})")
    print(f"[metrics] process handles: {before_handles} -> {after_handles} "
          f"(delta {after_handles - before_handles})")
    print(f"[metrics] labels: {sorted(after_combos)}")

    grown = after_combos - before_combos
    if grown:
        print(f"[metrics] FAIL: id requests created new label sets: {sorted(grown)}")
        return 1
    if after_bytes - before_bytes > 128:
        print("[metrics] FAIL: exposition text grew with the number of ids")
        return 1
    if before_handles >= 0 and after_handles - before_handles > 32:
        print("[metrics] FAIL: process handles grew with the number of ids")
        return 1

    # Unknown paths: arbitrary probes must converge, not each become a label.
    for i in range(UNKNOWN_PATHS):
        client.get(f"/v1/unknown/{i:04d}/{i * 7:04d}")
    unknown_combos = endpoint_combinations() - after_combos
    print(f"[metrics] {UNKNOWN_PATHS} unknown paths -> new label sets: "
          f"{len(unknown_combos)} {sorted(unknown_combos)}")
    if unknown_combos != {("GET", UNMATCHED_ENDPOINT_LABEL)}:
        print("[metrics] FAIL: unknown paths did not converge to one sentinel")
        return 1

    print("[metrics] PASS: 200 distinct ids added 0 label combinations; "
          "unknown paths converge to 1 sentinel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
