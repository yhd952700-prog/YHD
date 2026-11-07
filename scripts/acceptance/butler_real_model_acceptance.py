#!/usr/bin/env python
"""鎏灏管家 · 真实模型验收（拆解+检索 / 并行 / 失败重试）。

一条命令跑完三类场景，输出结构化结果并给出 PASS / FAIL / SKIP 判定。

设计约束：
* **Key 只从环境变量读取**：不写进代码、不写进仓库；脚本只在系统临时目录里
  建一个一次性的模型配置库（Key 用临时密钥加密），退出时删除。
* 不依赖任何预置的临时数据目录，换一台机器、换一个工作区都能复跑。
* 缺 Key / 模型不可达时以可读原因 SKIP，绝不假装通过。
* 审计库重定向到临时目录，不污染仓库 ``data/``。

用法见 ``docs/ACCEPTANCE-BUTLER-REAL-MODEL.md``。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: provider -> 该 provider 默认使用的 Key 环境变量（按优先级）。
KEY_ENV_NAMES = {
    "deepseek": ("LIUHAO_ACCEPTANCE_API_KEY", "DEEPSEEK_API_KEY"),
    "openai": ("LIUHAO_ACCEPTANCE_API_KEY", "OPENAI_API_KEY"),
    "moonshot": ("LIUHAO_ACCEPTANCE_API_KEY", "MOONSHOT_API_KEY"),
    "anthropic": ("LIUHAO_ACCEPTANCE_API_KEY", "ANTHROPIC_API_KEY"),
    "google": ("LIUHAO_ACCEPTANCE_API_KEY", "GOOGLE_API_KEY"),
    "ollama": ("LIUHAO_ACCEPTANCE_API_KEY",),
    "mock": (),
}

DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "moonshot": "moonshot-v1-8k",
    "anthropic": "claude-3-5-sonnet-latest",
    "google": "gemini-2.5-flash",
    "ollama": "qwen2.5:3b",
    "mock": "mock",
}

GOAL_RESEARCH = "帮我查一下今天有什么值得关注的 AI 开源项目，然后整理成三条要点"
GOAL_PARALLEL = (
    "帮我分别查一下今天 AI 智能体（agent）和 AI 芯片这两个方向的最新开源项目，"
    "每个方向整理三条要点"
)


# ────────────────────────────────────────────────────────────── 环境准备
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="鎏灏管家真实模型验收：拆解+检索、并行、失败重试",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("LIUHAO_ACCEPTANCE_PROVIDER", "deepseek"),
        help="模型 provider（默认 deepseek）",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LIUHAO_ACCEPTANCE_MODEL", ""),
        help="模型名（默认按 provider 取 deepseek-chat）",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LIUHAO_ACCEPTANCE_BASE_URL", ""),
        help="可选：覆盖 provider 的 base_url",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("LIUHAO_ACCEPTANCE_TIMEOUT_SECONDS", "150")),
        help="单次模型调用超时（默认 150s）",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="关闭实时联网检索（验证回退到已有知识时的诚实标注）",
    )
    parser.add_argument(
        "--scenarios",
        default="1,2,3",
        help="要跑的场景编号，逗号分隔（默认 1,2,3）",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="可选：把结构化结果写入指定 JSON 文件",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="保留临时工作目录（调试用；默认跑完删除）",
    )
    return parser.parse_args()


def resolve_api_key(provider: str) -> str:
    for name in KEY_ENV_NAMES.get(provider, ("LIUHAO_ACCEPTANCE_API_KEY",)):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def skip(reason: str, *, workdir: str = "", json_out: str = "") -> int:
    """可读原因 + 结构化结果，退出码 2（不代表通过）。"""
    payload = {
        "verdict": "SKIP",
        "reason": reason,
        "workdir": workdir,
        "scenarios": [],
    }
    if json_out:
        Path(json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2


def setup_environment(workdir: str) -> None:
    """所有路径重定向到临时目录，避免污染仓库 data/。"""
    workspace = Path(workdir) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["LIUHAO_MODEL_CONFIG_DB"] = str(Path(workdir) / "model_registry.sqlite3")
    os.environ["LIUHAO_MODEL_CONFIG_KEY_FILE"] = str(Path(workdir) / "model_config.key")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = str(workspace)
    os.environ["AUDIT_DB_PATH"] = str(Path(workdir) / "audit_store.db")
    os.environ["LIUHAO_BUTLER_MODEL"] = "1"


def seed_model_config(args: argparse.Namespace, model: str, api_key: str) -> dict:
    """把环境变量里的凭据一次性写进临时模型中心（加密），走生产解析链路。"""
    from src.ai.model_config import get_model_config_store

    payload = {
        "name": f"acceptance-{args.provider}",
        "provider": args.provider,
        "model": model,
        "api_key": api_key,
        "timeout": max(30.0, float(args.timeout_seconds)),
        "is_default": True,
    }
    if args.base_url:
        payload["base_url"] = args.base_url
    return get_model_config_store().create(payload, actor="acceptance-script")


# ────────────────────────────────────────────────────────────── 结果采集
def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_dicts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from walk_dicts(item)


def state_value(value) -> str:
    """枚举/字符串统一成小写值，避免 ``str(Enum)`` 带类名前缀。"""
    return str(getattr(value, "value", value)).strip().lower()


def task_rows_from_result(result) -> list:
    ctx = getattr(result, "context", None)
    plan = getattr(ctx, "plan", None) if ctx is not None else None
    rows = []
    for task in (getattr(plan, "tasks", []) or []) if plan is not None else []:
        rows.append({
            "name": getattr(task, "name", ""),
            "capability": state_value(getattr(task, "capability_id", "")),
            "status": state_value(getattr(task, "status", "")),
        })
    return rows


def research_metrics(result) -> dict:
    ctx = getattr(result, "context", None)
    outputs = {}
    if ctx is not None:
        for task_id, action_result in (ctx.task_results or {}).items():
            outputs[task_id] = getattr(action_result, "output", None)

    urls, fetched_at, bases, tool_errors = set(), "", set(), []
    for value in walk_dicts(outputs):
        if isinstance(value.get("sources"), list):
            for item in value["sources"]:
                if isinstance(item, dict) and item.get("url"):
                    urls.add(str(item["url"]))
        if not fetched_at and isinstance(value.get("fetched_at"), str):
            fetched_at = value["fetched_at"]
        if value.get("basis"):
            bases.add(str(value["basis"]))
        for key in ("retrieval_error", "model_error"):
            if value.get(key):
                tool_errors.append(str(value[key]))
    return {
        "source_count": len(urls),
        "sources": sorted(urls),
        "fetched_at": fetched_at,
        "bases": sorted(bases),
        "live_web": "live_web" in bases,
        "tool_errors": tool_errors[:3],
    }


def max_concurrency(trace) -> int:
    events = []
    for entry in trace.entries:
        if entry.event_type == "task_started":
            events.append((entry.ts, 1))
        elif entry.event_type in ("task_completed", "task_failed"):
            events.append((entry.ts, -1))
    running = peak = 0
    for _ts, delta in sorted(events, key=lambda item: item[0]):
        running += delta
        peak = max(peak, running)
    return peak


def build_runtime():
    from src.ai.agent_runtime import AgentRuntime
    from src.ai.lcore import LCore

    lcore = LCore(scope="L1", register_local_tools=True)
    return AgentRuntime(
        scope="L1",
        capability_executor=lcore.capability_executor(),
        executable_capabilities=lcore.executable_capabilities(),
        enable_model_planning=True,
    )


def base_result(scenario: str, title: str) -> dict:
    return {
        "scenario": scenario,
        "title": title,
        "status": "FAIL",
        "elapsed_s": None,
        "goal_state": None,
        "task_count": 0,
        "max_concurrent_running": None,
        "source_count": 0,
        "live_web": False,
        "fetched_at": "",
        "artifacts": [],
        "tasks": [],
        "summary_excerpt": "",
        "detail": "",
        "retry_elapsed_s": None,
        "retry_state": None,
    }


def fail(item: dict, detail: str) -> dict:
    item["status"] = "FAIL"
    item["detail"] = detail
    return item


def pass_item(item: dict, detail: str = "") -> dict:
    item["status"] = "PASS"
    item["detail"] = detail
    return item


# ────────────────────────────────────────────────────────────── 场景 1 / 2
def run_research_scenario(web_enabled: bool) -> dict:
    item = base_result("decompose_research", "拆解 + 实时检索")
    runtime = build_runtime()
    started = time.monotonic()
    result = runtime.run_goal(GOAL_RESEARCH, persist=False)
    item["elapsed_s"] = round(time.monotonic() - started, 2)
    item["goal_state"] = state_value(result.state)
    item["tasks"] = task_rows_from_result(result)
    item["task_count"] = len(item["tasks"])
    item.update(research_metrics(result))
    item["max_concurrent_running"] = max_concurrency(result.trace)
    evaluation = getattr(result, "evaluation", None)
    full_summary = evaluation.summary if evaluation else ""
    item["summary_excerpt"] = full_summary[:400]

    if item["goal_state"] != "completed":
        return fail(item, f"goal 未 completed（{item['goal_state']}：{result.error}）")
    if item["task_count"] < 2:
        return fail(item, f"拆解任务数不足（{item['task_count']}）")
    if not web_enabled:
        if item["live_web"]:
            return fail(item, "关闭检索时不应出现 live_web 结果")
        return pass_item(item, "检索已关闭：确认未联网且未伪造实时结果")
    if not item["live_web"]:
        return fail(item, "未命中实时检索（未取得 live_web 结果）")
    if item["source_count"] < 1:
        return fail(item, "实时检索未返回任何来源")
    if not item["fetched_at"]:
        return fail(item, "来源缺少抓取时间")
    if "抓取时间" not in full_summary:
        return fail(item, "结论未标注抓取时间")
    if not any(url in full_summary for url in item["sources"]):
        return fail(item, "结论未保留任何真实来源链接")
    return pass_item(item, f"命中检索，{item['source_count']} 条来源，结论可追溯")


def run_parallel_scenario() -> dict:
    item = base_result("parallel", "并行执行")
    runtime = build_runtime()
    started = time.monotonic()
    result = runtime.run_goal(GOAL_PARALLEL, persist=False)
    item["elapsed_s"] = round(time.monotonic() - started, 2)
    item["goal_state"] = state_value(result.state)
    item["tasks"] = task_rows_from_result(result)
    item["task_count"] = len(item["tasks"])
    item.update(research_metrics(result))
    item["max_concurrent_running"] = max_concurrency(result.trace)
    evaluation = getattr(result, "evaluation", None)
    item["summary_excerpt"] = (evaluation.summary if evaluation else "")[:400]

    if item["goal_state"] != "completed":
        return fail(item, f"goal 未 completed（{item['goal_state']}：{result.error}）")
    if item["task_count"] < 2:
        return fail(item, f"拆解任务数不足（{item['task_count']}）")
    if item["max_concurrent_running"] < 2:
        return fail(item, f"并发峰值 {item['max_concurrent_running']}，未观察到并行")
    return pass_item(item, f"并发峰值 {item['max_concurrent_running']}，确认并行执行")


# ────────────────────────────────────────────────────────────── 场景 3
def run_retry_scenario() -> dict:
    from src.ai.lcore import LCore
    from src.gateway.ai_management import AIStateManager

    marker = uuid.uuid4().hex[:8]
    target = f"notes/acceptance-retry-{marker}.txt"
    goal = (
        "帮我查一下今天有什么值得关注的 AI 开源项目，整理三条要点，"
        f"并写入文件 {target}"
    )
    item = base_result("failure_retry", "写入失败 → 重试")
    blocker = {"remaining": 6}
    original = LCore.capability_executor

    def patched(self):
        inner = original(self)

        def wrapper(capability_id, inputs):
            if capability_id == "file_write" and blocker["remaining"] > 0:
                blocker["remaining"] -= 1
                raise RuntimeError("磁盘写入失败（acceptance 注入的临时故障）")
            return inner(capability_id, inputs)

        return wrapper

    LCore.capability_executor = patched
    try:
        manager = AIStateManager()
        started = time.monotonic()
        created = manager.create_and_execute_goal(goal)
        item["elapsed_s"] = round(time.monotonic() - started, 2)
    finally:
        LCore.capability_executor = original

    item["goal_state"] = created.get("state")
    item["tasks"] = [
        {
            "name": t.get("name", ""),
            "capability": t.get("capability_id") or t.get("capability") or "",
            "status": t.get("status", ""),
            "error": (t.get("error") or "")[:200],
        }
        for t in created.get("tasks", [])
    ]
    item["task_count"] = len(item["tasks"])
    item["artifacts"] = list(created.get("artifacts") or [])
    ids_by_name = {
        t.get("name"): t.get("id") for t in created.get("tasks", [])
    }

    failed = [t for t in item["tasks"] if t["status"] == "failed"]
    write_failed = [
        t for t in failed
        if "file_write" in str(t["capability"]) or "写" in str(t["name"])
    ]
    if created.get("state") != "failed":
        return fail(item, f"注入失败后 goal 应为 failed，实际 {created.get('state')}")
    if not write_failed:
        return fail(item, "注入失败后没有找到失败的 file_write 任务")
    if "磁盘写入失败" not in (write_failed[0].get("error") or ""):
        return fail(item, f"错误语义不可读：{write_failed[0].get('error')}")
    if item["artifacts"]:
        return fail(item, f"失败时 artifacts 应为空，实际 {item['artifacts']}")

    # 解除注入，走真实的单任务重试入口。
    blocker["remaining"] = 0
    failed_task_id = ids_by_name.get(write_failed[0].get("name"))
    if not failed_task_id:
        return fail(item, "无法定位失败任务的 id，重试未执行")
    started = time.monotonic()
    retried = manager.retry_task(created.get("goal_id"), failed_task_id)
    retry_elapsed = round(time.monotonic() - started, 2)

    item["retry_elapsed_s"] = retry_elapsed
    item["retry_state"] = retried.get("state")
    item["artifacts"] = list(retried.get("artifacts") or [])
    item["retry_error"] = retried.get("error")

    workspace_root = os.environ["LIUHAO_WORKSPACE_ROOT"]
    disk_verified = [
        artifact for artifact in item["artifacts"]
        if os.path.exists(os.path.join(workspace_root, artifact))
    ]
    item["disk_verified"] = disk_verified

    if item["retry_state"] != "completed":
        return fail(item, f"重试后 goal 未 completed（{item['retry_state']}）")
    if retried.get("error"):
        return fail(item, f"重试后仍带错误：{retried.get('error')}")
    if not item["artifacts"]:
        return fail(item, "重试后 artifacts 仍为空")
    if not disk_verified:
        return fail(item, f"artifacts {item['artifacts']} 在磁盘上不存在")
    return pass_item(item, f"失败语义正确；重试后 completed，artifacts={item['artifacts']}")


# ────────────────────────────────────────────────────────────── 主流程
def main() -> int:
    args = parse_args()
    provider_name = args.provider.strip().lower()
    model = args.model.strip() or DEFAULT_MODELS.get(provider_name, "")

    from src.ai.model_config import PROVIDERS_WITHOUT_KEY, SUPPORTED_PROVIDERS

    if provider_name not in SUPPORTED_PROVIDERS:
        return skip(
            f"不支持的 provider：{provider_name or '(空)'}；可选 {', '.join(SUPPORTED_PROVIDERS)}",
            json_out=args.json_out,
        )
    if provider_name == "mock":
        return skip("provider=mock 不能作为真实模型验收", json_out=args.json_out)
    if not model:
        return skip(f"provider={provider_name} 缺少模型名（--model）", json_out=args.json_out)

    api_key = resolve_api_key(provider_name)
    if provider_name not in PROVIDERS_WITHOUT_KEY and not api_key:
        envs = " / ".join(KEY_ENV_NAMES.get(provider_name, ("LIUHAO_ACCEPTANCE_API_KEY",)))
        return skip(
            f"缺少 API Key：请在环境变量中设置 {envs}（脚本不会把 Key 写进仓库）",
            json_out=args.json_out,
        )

    workdir = tempfile.mkdtemp(prefix="liuhao-acceptance-")
    setup_environment(workdir)
    if args.no_web:
        os.environ["LIUHAO_WEB_RESEARCH"] = "0"
    else:
        os.environ["LIUHAO_WEB_RESEARCH"] = "1"
    os.environ["LIUHAO_LLM_TIMEOUT_SECONDS"] = "60"
    os.environ["LIUHAO_LLM_TOOL_TIMEOUT_SECONDS"] = str(max(30.0, float(args.timeout_seconds)))

    try:
        seed_model_config(args, model, api_key)

        from src.ai.llm_support import resolve_execution_provider
        from src.ai.providers import MockProvider

        provider = resolve_execution_provider()
        identity = {
            "provider": provider_name,
            "provider_type": type(provider).__name__,
            "model": getattr(provider, "model", ""),
            "model_config_id": getattr(provider, "model_config_id", ""),
            "web_enabled": not args.no_web,
        }
        print("MODEL " + json.dumps(identity, ensure_ascii=False), flush=True)

        kept_workdir = workdir if args.keep_workdir else ""
        if isinstance(provider, MockProvider):
            return skip("模型中心解析到 MockProvider（没有可用模型）",
                        workdir=kept_workdir, json_out=args.json_out)

        # 预检：模型不可达时 SKIP，而不是让三个场景都报 FAIL。
        try:
            probe = provider.generate_with_retry("只回复两个字符：OK")
        except Exception as exc:  # noqa: BLE001
            return skip(
                f"模型不可达（{type(exc).__name__}: {str(exc)[:160]}），未执行验收",
                workdir=kept_workdir, json_out=args.json_out,
            )
        if not str(probe or "").strip():
            return skip("模型返回空内容，未执行验收", workdir=kept_workdir, json_out=args.json_out)

        selected = [s.strip() for s in args.scenarios.split(",") if s.strip()]
        results = []
        if "1" in selected:
            results.append(run_research_scenario(not args.no_web))
        if "2" in selected:
            results.append(run_parallel_scenario())
        if "3" in selected:
            results.append(run_retry_scenario())

        for item in results:
            print("RESULT " + json.dumps(item, ensure_ascii=False), flush=True)

        verdict = "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL"
        payload = {
            "verdict": verdict,
            "provider": provider_name,
            "model": identity["model"],
            "model_config_id": identity["model_config_id"],
            "web_enabled": not args.no_web,
            "workdir": workdir if args.keep_workdir else "",
            "scenarios": results,
        }
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
            )
        print("VERDICT " + json.dumps(
            {"verdict": verdict, "scenarios": [r["status"] for r in results]},
            ensure_ascii=False), flush=True)
        return 0 if verdict == "PASS" else 1
    finally:
        if not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
