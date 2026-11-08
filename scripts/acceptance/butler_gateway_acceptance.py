"""鎏灏管家 · 真实网关端到端验收（本机 Ollama，五项场景）。

与 ``butler_real_model_acceptance.py``（直接跑 AgentRuntime）不同，本脚本
**启动真实网关**（真实 FastAPI 应用 + 真实 TCP 端口），通过 HTTP 调用管家
业务面（``POST /v1/goals``、``POST /v1/goals/assign``、``GET /v1/goals/{id}``），
并在系统临时目录里准备一次性数据目录，跑完删除：

1. 一句话派活：自动拆解 → 真执行 → 给出结果（不是只说"已安排"）。
2. 单独指派：指名某位员工，任务归属与结果都回到该员工。
3. 多任务并发：一句话含两件事，两件都真的完成（并发峰值 ≥ 2）。
4. 失败如实：注定失败的任务 → goal failed、错误原因可读、无编造产物。
5. 结果格式：结论带真实链接与抓取时间；任何字段都不出现 Python dict repr。

用法::

    python scripts/acceptance/butler_gateway_acceptance.py --json-out acceptance.json

退出码：0 全部 PASS；1 有 FAIL；2 前置条件不满足（SKIP，绝不当作通过）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 老 Python / 重定向环境
    pass

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"

GOAL_ONE_SHOT = "帮我查一下今天有什么值得关注的 AI 开源项目，并整理成一页摘要"
GOAL_BOTH = (
    "帮我查一下今天有什么值得关注的 AI 开源项目，同时计算 128 + 64 的结果，"
    "两件事都要完成"
)
# 注定失败：工作区外的写入。file_write 会拒绝越界路径，且不会产生任何文件。
GOAL_DOOMED = 'write "hello acceptance" to ../escape-acceptance.txt'
ASSIGN_INSTRUCTION = "计算 128 + 64 的结果，并告诉我答案"

RAW_DICT_RE = re.compile(r"\{\s*'[^']{0,80}'\s*:")
URL_RE = re.compile(
    r"https?://[^\s，。；：、！？（）【】《》〈〉「」『』“”‘’\"'<>()\[\]{}]+"
)
STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
#: "128 + 64 = 192" / "128 + 64 的结果是 204" —— 同一目标里两个值不能并存。
ARITH_CLAIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*([+\-*/×÷])\s*(\d+(?:\.\d+)?)\s*"
    r"(?:[=＝]|的结果(?:是|为)|结果是)\s*(-?\d+(?:\.\d+)?)"
)
FILLER_ONLY = ("已安排", "已收到", "正在安排", "稍后为你处理")


# ────────────────────────────────────────────────────────────── 环境 / 服务
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="鎏灏管家真实网关端到端验收")
    parser.add_argument(
        "--model",
        default=os.environ.get("LIUHAO_ACCEPTANCE_MODEL", DEFAULT_MODEL),
        help=f"Ollama 模型名（默认 {DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("LIUHAO_ACCEPTANCE_OLLAMA_URL", DEFAULT_OLLAMA_URL),
        help="Ollama 地址",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("LIUHAO_ACCEPTANCE_TIMEOUT_SECONDS", "420")),
        help="单次模型调用超时（默认 420s）",
    )
    parser.add_argument("--keep-workdir", action="store_true", help="保留临时目录")
    parser.add_argument("--json-out", default="", help="把结构化结果写入该 JSON 文件")
    return parser.parse_args()


def skip(reason: str, *, json_out: str = "") -> int:
    payload = {"verdict": "SKIP", "reason": reason, "scenarios": []}
    if json_out:
        Path(json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prepare_environment(workdir: Path) -> None:
    data = workdir / "data"
    workspace = workdir / "workspace"
    data.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "LIUHAO_DATA_DIR": str(data),
            "LIUHAO_WORKSPACE_ROOT": str(workspace),
            "LIUHAO_MODEL_CONFIG_DB": str(data / "model_registry.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(data / "model_config.key"),
            "LIUHAO_MODEL_CONFIG_MIGRATE": "0",
            "AUDIT_DB_PATH": str(data / "audit_store.db"),
            "LIUHAO_BUTLER_MODEL": "1",
            "LIUHAO_JWT_SECRET": f"butler-acceptance-{uuid.uuid4().hex}",
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(data / "human_identities.json"),
            "LIUHAO_LLM_TIMEOUT_SECONDS": str(max(60.0, 420.0)),
            "LIUHAO_LLM_TOOL_TIMEOUT_SECONDS": str(max(60.0, 420.0)),
            "LIUHAO_WEB_RESEARCH": "1",
        }
    )


def seed_model_config(args: argparse.Namespace) -> Dict[str, Any]:
    from src.ai.model_config import get_model_config_store

    return get_model_config_store().create(
        {
            "name": f"acceptance-ollama-{args.model}",
            "provider": "ollama",
            "model": args.model,
            "base_url": args.ollama_url,
            "timeout": max(60.0, float(args.timeout_seconds)),
            "is_default": True,
        },
        actor="gateway-acceptance",
    )


def mint_token() -> str:
    from src.security.jwt_handler import create_access_token

    token, _payload = create_access_token(
        subject="human:acceptance",
        scopes=["console"],
        roles=["owner"],
        permissions=["console", "goals:write"],
        ttl=3600,
        metadata={"kind": "human", "client": "gateway-acceptance", "scope": "L4"},
        device_id="gateway-acceptance",
    )
    return token


def start_gateway(port: int) -> Tuple[Any, threading.Thread]:
    import uvicorn

    from src.gateway.main import app

    if app is None:  # pragma: no cover - 无 config 的极简环境
        from src.gateway.main import get_app

        app = get_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="acceptance-gateway", daemon=True)
    thread.start()
    return server, thread


def http_json(
    base: str,
    method: str,
    path: str,
    token: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 600.0,
) -> Tuple[int, Dict[str, Any]]:
    data = None
    headers = {
        "X-Liuhao-Token": f"Bearer {token}",
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def wait_ready(base: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/v1/health", timeout=3) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - 启动期连接拒绝是预期
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"网关在 {timeout:.0f}s 内没有就绪：{last}")


# ────────────────────────────────────────────────────────────── 证据提取
def state_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


def walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from walk_strings(item)


def _epoch(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def max_concurrency(detail: Dict[str, Any]) -> int:
    """用任务的真实起止时间算并发峰值（trace 字段名可能随实现变化）。"""
    events: List[Tuple[float, int]] = []
    for task in detail.get("tasks") or []:
        started = _epoch(task.get("started_at"))
        finished = _epoch(task.get("completed_at"))
        if started is not None and finished is not None and finished >= started:
            events.append((started, 1))
            events.append((finished, -1))
    if not events:
        for entry in detail.get("trace") or []:
            ts = _epoch(entry.get("ts"))
            if ts is None:
                continue
            kind = str(entry.get("event_type") or "")
            if kind == "task_started":
                events.append((ts, 1))
            elif kind in ("task_completed", "task_failed"):
                events.append((ts, -1))
    running = peak = 0
    # 同一时刻先结束后开始：首尾相接不算并发。
    for _ts, delta in sorted(events, key=lambda item: (item[0], item[1])):
        running += delta
        peak = max(peak, running)
    return peak


def _apply_operator(left: float, operator: str, right: float) -> Optional[float]:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator in ("*", "×"):
        return left * right
    if operator in ("/", "÷"):
        return None if right == 0 else left / right
    return None


def _trusted_compute_values(tasks: List[Dict[str, Any]]) -> set:
    values = set()
    for task in tasks:
        if str(task.get("capability_id") or "") != "python_compute":
            continue
        if str(task.get("status") or "") != "completed":
            continue
        for match in re.finditer(r"-?\d+(?:\.\d+)?", str(task.get("result_excerpt") or "")):
            values.add(round(float(match.group(0)), 9))
    return values


def contradictory_arithmetic(summary: str, tasks: List[Dict[str, Any]]) -> List[str]:
    """Summary values that contradict the real python_compute output."""
    trusted = _trusted_compute_values(tasks)
    offenders: List[str] = []
    for match in ARITH_CLAIM_RE.finditer(summary or ""):
        left, operator, right, stated = match.groups()
        stated_value = round(float(stated), 9)
        if trusted:
            if stated_value not in trusted:
                offenders.append(match.group(0))
            continue
        actual = _apply_operator(float(left), operator, float(right))
        if actual is not None and abs(actual - stated_value) > 1e-6:
            offenders.append(match.group(0))
    return offenders


def summarize_detail(detail: Dict[str, Any], elapsed: float) -> Dict[str, Any]:
    tasks = detail.get("tasks") or []
    evaluation = detail.get("evaluation") or {}
    summary = str(evaluation.get("summary") or "")
    urls = sorted({m.group(0) for m in URL_RE.finditer(summary)})
    return {
        "goal_id": detail.get("goal_id"),
        "state": detail.get("state"),
        "elapsed_s": round(elapsed, 2),
        "task_count": len(tasks),
        "tasks": [
            {
                "name": task.get("name"),
                "capability_id": task.get("capability_id"),
                "status": task.get("status"),
                "assigned_agent": task.get("assigned_agent"),
                "assigned_employee": task.get("assigned_employee"),
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "result_excerpt": (task.get("result") or "")[:400],
                "error": task.get("error"),
            }
            for task in tasks
        ],
        "max_concurrent_running": max_concurrency(detail),
        "employees": detail.get("employees") or [],
        "artifacts": detail.get("artifacts") or [],
        "summary": summary,
        "summary_excerpt": summary[:600],
        "summary_urls": urls,
        "summary_has_stamp": bool(STAMP_RE.search(summary)),
        "contradictory_arithmetic": contradictory_arithmetic(summary, tasks),
        "raw_dict_repr": sorted(
            {text for text in walk_strings(detail) if RAW_DICT_RE.search(text)}
        )[:5],
    }


def base_item(key: str, title: str, input_text: str) -> Dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "input": input_text,
        "status": "FAIL",
        "detail": "",
        "elapsed_s": None,
        "evidence": {},
    }


def fail(item: Dict[str, Any], detail: str) -> Dict[str, Any]:
    item["status"] = "FAIL"
    item["detail"] = detail
    return item


def passed(item: Dict[str, Any], detail: str) -> Dict[str, Any]:
    item["status"] = "PASS"
    item["detail"] = detail
    return item


# ────────────────────────────────────────────────────────────── 五项场景
def scenario_one(base_url: str, token: str) -> Dict[str, Any]:
    item = base_item("one_shot", "一句话派活（自动拆解 + 真执行）", GOAL_ONE_SHOT)
    started = time.monotonic()
    status, created = http_json(
        base_url, "POST", "/v1/goals", token, {"natural_language": GOAL_ONE_SHOT}
    )
    elapsed = time.monotonic() - started
    if status != 200:
        return fail(item, f"POST /v1/goals 返回 {status}：{created}")
    goal_id = created.get("goal_id")
    if not goal_id:
        return fail(item, f"响应缺少 goal_id：{created}")
    _, detail = http_json(base_url, "GET", f"/v1/goals/{goal_id}", token)
    evidence = summarize_detail(detail, elapsed)
    item["evidence"] = evidence
    item["elapsed_s"] = evidence["elapsed_s"]

    if evidence["state"] != "completed":
        return fail(item, f"goal 未 completed（{evidence['state']}）")
    if evidence["task_count"] < 2:
        return fail(item, f"只拆出 {evidence['task_count']} 个任务，未体现拆解")
    if not evidence["summary"]:
        return fail(item, "结论为空：只可能回复「已安排」，没有真实结果")
    if any(filler in evidence["summary"] for filler in FILLER_ONLY) and len(
        evidence["summary"]
    ) < 80:
        return fail(item, f"结论是占位话术：{evidence['summary'][:100]}")
    if not evidence["summary_urls"]:
        return fail(item, "结论里没有任何真实来源链接")
    if not evidence["summary_has_stamp"]:
        return fail(item, "结论里没有抓取时间戳")
    completed = [t for t in evidence["tasks"] if t["status"] == "completed"]
    if len(completed) != evidence["task_count"]:
        return fail(item, f"有任务未完成：{[(t['name'], t['status']) for t in evidence['tasks']]}")
    return passed(
        item,
        f"{evidence['task_count']} 个任务全部完成，结论 {len(evidence['summary'])} 字，"
        f"引用 {len(evidence['summary_urls'])} 条真实来源，带抓取时间",
    )


def scenario_assign(base_url: str, token: str, workdir: Path) -> Dict[str, Any]:
    item = base_item("assign", "单独指派（结果落到指定员工）", ASSIGN_INSTRUCTION)
    employee_name = f"验收专员-{uuid.uuid4().hex[:6]}"
    status, hired = http_json(
        base_url,
        "POST",
        "/v1/employees",
        token,
        {
            "name": employee_name,
            "agent_count": 1,
            "role_description": "负责验收场景中的计算与结果回报",
            "default_capabilities": ["python_compute"],
        },
    )
    if status not in (200, 201):
        return fail(item, f"创建员工失败（{status}）：{hired}")
    started = time.monotonic()
    status, assigned = http_json(
        base_url,
        "POST",
        "/v1/goals/assign",
        token,
        {
            "employee_name": employee_name,
            "instruction": ASSIGN_INSTRUCTION,
            "background": False,
        },
    )
    elapsed = time.monotonic() - started
    if status not in (200, 201):
        return fail(item, f"POST /v1/goals/assign 返回 {status}：{assigned}")
    goal_id = assigned.get("goal_id")
    _, detail = http_json(base_url, "GET", f"/v1/goals/{goal_id}", token)
    evidence = summarize_detail(detail, elapsed)
    evidence["employee_name"] = employee_name
    evidence["assign_response_employee"] = assigned.get("assigned_employee")
    item["evidence"] = evidence
    item["elapsed_s"] = evidence["elapsed_s"]

    if evidence["state"] != "completed":
        return fail(item, f"指派目标未完成（{evidence['state']}）")
    if evidence["task_count"] < 1:
        return fail(item, "指派没有产生任何任务")
    wrong = [
        t for t in evidence["tasks"]
        if t["assigned_employee"] != employee_name
    ]
    if wrong:
        return fail(
            item,
            f"有任务没有落到指定员工：{[(t['name'], t['assigned_employee']) for t in wrong]}",
        )
    results = " ".join(str(t.get("result_excerpt") or "") for t in evidence["tasks"])
    if not results.strip():
        return fail(item, "任务完成但没有任何结果内容")
    if "192" not in results:
        return fail(item, f"计算结果不对/未出现 192：{results[:200]}")
    if evidence["artifacts"]:
        return fail(item, f"纯计算任务不应产生文件：{evidence['artifacts']}")
    return passed(
        item,
        f"员工 {employee_name} 承接全部 {evidence['task_count']} 个任务，"
        f"结果含 128+64=192",
    )


def scenario_parallel(base_url: str, token: str) -> Dict[str, Any]:
    item = base_item("parallel", "多任务并发（两件事都真的做完）", GOAL_BOTH)
    started = time.monotonic()
    status, created = http_json(
        base_url, "POST", "/v1/goals", token, {"natural_language": GOAL_BOTH}
    )
    elapsed = time.monotonic() - started
    if status != 200:
        return fail(item, f"POST /v1/goals 返回 {status}：{created}")
    goal_id = created.get("goal_id")
    _, detail = http_json(base_url, "GET", f"/v1/goals/{goal_id}", token)
    evidence = summarize_detail(detail, elapsed)
    item["evidence"] = evidence
    item["elapsed_s"] = evidence["elapsed_s"]

    if evidence["state"] != "completed":
        return fail(item, f"多任务目标未 completed（{evidence['state']}）")
    if evidence["task_count"] < 2:
        return fail(item, f"只拆出 {evidence['task_count']} 个任务，未覆盖两件事")
    unfinished = [t for t in evidence["tasks"] if t["status"] != "completed"]
    if unfinished:
        return fail(
            item,
            f"有任务未完成却可能被当成成功：{[(t['name'], t['status']) for t in unfinished]}",
        )
    caps = {str(t.get("capability_id")) for t in evidence["tasks"]}
    if "network_bus" not in caps:
        return fail(item, f"缺少检索任务（能力集合 {sorted(caps)}）")
    if "python_compute" not in caps:
        return fail(item, f"缺少计算任务（能力集合 {sorted(caps)}）")
    if evidence["max_concurrent_running"] < 2:
        return fail(
            item,
            f"并发峰值 {evidence['max_concurrent_running']}，两件事没有并行执行",
        )
    results = " ".join(str(t.get("result_excerpt") or "") for t in evidence["tasks"])
    if "192" not in results:
        return fail(item, f"计算结果没有出现在任务结果里：{results[:200]}")
    return passed(
        item,
        f"{evidence['task_count']} 个任务全部完成，并发峰值 {evidence['max_concurrent_running']}，"
        f"检索与计算结果都在",
    )


def scenario_failure(base_url: str, token: str) -> Dict[str, Any]:
    item = base_item("failure", "失败如实（不编造结果）", GOAL_DOOMED)
    started = time.monotonic()
    status, created = http_json(
        base_url, "POST", "/v1/goals", token, {"natural_language": GOAL_DOOMED}
    )
    elapsed = time.monotonic() - started
    if status != 200:
        return fail(item, f"POST /v1/goals 返回 {status}：{created}")
    goal_id = created.get("goal_id")
    _, detail = http_json(base_url, "GET", f"/v1/goals/{goal_id}", token)
    evidence = summarize_detail(detail, elapsed)
    item["evidence"] = evidence
    item["elapsed_s"] = evidence["elapsed_s"]

    if evidence["state"] != "failed":
        return fail(item, f"注定失败的目标却报告为 {evidence['state']}")
    failed = [t for t in evidence["tasks"] if t["status"] == "failed"]
    if not failed:
        return fail(item, "goal 失败但没有失败任务")
    errors = [str(t.get("error") or "") for t in failed]
    if not any(err.strip() for err in errors):
        return fail(item, "任务失败但没有给出原因")
    readme = " ".join(errors)
    if not re.search(r"workspace|工作区|路径|outside|escape", readme, re.IGNORECASE):
        return fail(item, f"失败原因没有说明是路径越界：{errors}")
    if evidence["artifacts"]:
        return fail(item, f"失败任务却声称产出文件：{evidence['artifacts']}")
    success_like = [
        t for t in evidence["tasks"]
        if t["status"] == "failed" and str(t.get("result_excerpt") or "").strip()
    ]
    if success_like:
        return fail(item, "失败任务仍带结果内容，存在编造风险")
    return passed(item, f"goal=failed，失败原因可读：{errors[0][:120]}")


def scenario_format(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    item = base_item(
        "format",
        "结果格式（可点击链接 / 时间戳 / 无原始结构）",
        "对场景 1/2/3 的真实响应做格式检查",
    )
    offenders: List[str] = []
    contradictions: List[str] = []
    url_evidence: List[str] = []
    stamp_seen = False
    for scenario in items:
        if scenario["key"] not in ("one_shot", "assign", "parallel"):
            continue
        evidence = scenario.get("evidence") or {}
        for raw in evidence.get("raw_dict_repr") or []:
            offenders.append(f"{scenario['key']}: {raw[:80]}")
        for claim in evidence.get("contradictory_arithmetic") or []:
            contradictions.append(f"{scenario['key']}: {claim}")
        for url in evidence.get("summary_urls") or []:
            url_evidence.append(url)
        if evidence.get("summary_has_stamp"):
            stamp_seen = True
    item["evidence"] = {
        "raw_dict_repr": offenders,
        "contradictory_arithmetic": contradictions,
        "summary_urls": sorted(set(url_evidence))[:8],
        "summary_has_stamp": stamp_seen,
        "frontend_linkify": _frontend_linkify_wired(),
    }
    if offenders:
        return fail(item, f"响应里仍出现 Python dict repr：{offenders[:3]}")
    if contradictions:
        return fail(
            item,
            f"摘要中出现与真实计算矛盾的结果：{contradictions[:3]}",
        )
    if not url_evidence:
        return fail(item, "没有任何真实链接可用于点击验证")
    if not stamp_seen:
        return fail(item, "没有任何抓取时间戳")
    if not item["evidence"]["frontend_linkify"]:
        return fail(item, "管家页面没有把链接渲染成可点击元素")
    return passed(
        item,
        f"{len(set(url_evidence))} 条真实链接、抓取时间戳齐全，"
        f"页面已接入 linkify 渲染，响应无 dict repr",
    )


def _frontend_linkify_wired() -> bool:
    """源码级确认：管家页面的结论/任务结果走 linkify 渲染。"""
    page = REPO_ROOT / "apps" / "console" / "console" / "src" / "pages" / "Butler.tsx"
    try:
        source = page.read_text("utf-8")
    except OSError:
        return False
    return "LinkifiedText" in source and "os-butler-summary" in source


# ────────────────────────────────────────────────────────────── 主流程
def main() -> int:
    args = parse_args()
    workdir = Path(tempfile.mkdtemp(prefix="liuhao-butler-gateway-"))
    prepare_environment(workdir)

    try:
        seeded = seed_model_config(args)
    except Exception as exc:  # noqa: BLE001
        return skip(f"模型配置无法写入临时目录：{exc}", json_out=args.json_out)

    try:
        from src.ai.providers import get_provider

        provider = get_provider()
        reply = str(provider.chat([{"role": "user", "content": "只回复 OK"}]))
        if "OK" not in reply.upper():
            print(f"[probe] 模型回复异常：{reply[:120]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        return skip(f"本机 Ollama 模型不可用（{args.model}）：{exc}", json_out=args.json_out)

    token = mint_token()
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    server = None
    try:
        server, thread = start_gateway(port)
        wait_ready(base_url, timeout=180)
        print(f"[gateway] ready at {base_url}  (model={args.model}, temp={workdir})")

        results: List[Dict[str, Any]] = []
        results.append(scenario_one(base_url, token))
        results.append(scenario_assign(base_url, token, workdir))
        results.append(scenario_parallel(base_url, token))
        results.append(scenario_failure(base_url, token))
        results.append(scenario_format(results))

        verdict = "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL"
        payload = {
            "verdict": verdict,
            "gateway": base_url,
            "model": {"provider": "ollama", "name": args.model, "config_id": seeded.get("id")},
            "workdir": str(workdir),
            "scenarios": results,
        }
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if verdict == "PASS" else 1
    except Exception as exc:  # noqa: BLE001
        payload = {
            "verdict": "FAIL",
            "reason": f"{type(exc).__name__}: {exc}",
            "workdir": str(workdir),
            "scenarios": [],
        }
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        if not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
