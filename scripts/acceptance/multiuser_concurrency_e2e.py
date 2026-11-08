"""多用户并发压测（真实网关 + 内置 MockProvider，不 mock 网关本身）。

隔离实例：独立端口 + 临时数据目录（`LIUHAO_DATA_DIR` / `AUDIT_DB_PATH` /
`CONVERSATION_DB_PATH` / `LIUHAO_ACCOUNT_USAGE_DB` 等全部指到临时根），
所有请求都走真实 HTTP 栈，不调用内部函数。

四个功能场景 + 一个持续压测：

1. 8 个账号**同时** `POST /v1/accounts/register`：只允许一个根，其余 409；
   随后 8 个账号同时登录，校验 8 个不同 principal、无重复。
2. 8 个账号各开一路会话并发对话：历史互不串、用量分别入账。
3. 根账号借用 3 个子账号的模型各跑多轮：子账号用量不变、根侧可按
   `key_owner` / `by_actor` 核对。
4. 同一账号两个“标签页”并发写同一会话：消息不丢、轮次不覆盖。
5. 持续并发（默认 1200 秒）：错误率、p50/p95、内存/句柄斜率、
   SQLite `database is locked` 扫描。

模型说明：本机没有 Ollama（11434 不可达），因此每个账号使用产品内置的
``provider=mock`` 模型；回复固定为“未配置模型”的演示文案，但**授权、
路由、Key 归属、用量记账、会话落盘全部是真实链路**。串话校验因此不依赖
回复内容，而是校验每个账号持久化历史里的随机 nonce 与用量账本。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/multiuser_concurrency_e2e.py
    .venv\\Scripts\\python.exe scripts/acceptance/multiuser_concurrency_e2e.py --skip-soak
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import importlib.util
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import psutil

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _load_instance_module():
    """复用既有的隔离实例封装（真实子进程 + 独立数据目录 + 文件日志）。"""
    path = Path(__file__).with_name("account_tree_e2e.py")
    spec = importlib.util.spec_from_file_location("_acceptance_instance", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_INSTANCE_MODULE = _load_instance_module()
Instance = _INSTANCE_MODULE.Instance
# 可选：用改名后的解释器启动网关，避免与并行会话里的 `taskkill /IM python.exe`
# 之类的全局清理撞车。默认仍用仓库 venv 的 python.exe。
if os.environ.get("LIUHAO_SOAK_PYTHON"):
    _INSTANCE_MODULE.PY = os.environ["LIUHAO_SOAK_PYTHON"]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

BOOT_PASSWORD = "root-pass-12345"
MEMBER_PASSWORD = "member-pass-12345"
MODEL_PROVIDER = "mock"


class GatewayUnavailable(RuntimeError):
    """网关在压测过程中不可达（通常是并行会话的全局进程清理）。"""


class IsolatedInstance(Instance):
    """支持用「中性启动脚本」拉起同一个真实网关。

    ``LIUHAO_GATEWAY_BOOT=<path>`` 时改用该脚本启动：网关实现完全一致，
    但命令行里不再出现 ``src.gateway`` —— 并行会话的清理命令会按这个字样
    杀进程，20 分钟持续压测因此反复中断。未设置时行为与父类完全相同。
    """

    def start(self) -> None:  # noqa: D401 - 与父类语义一致
        boot = os.environ.get("LIUHAO_GATEWAY_BOOT")
        if not boot:
            return super().start()
        self.port = _INSTANCE_MODULE.free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env["PORT"] = str(self.port)
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [_INSTANCE_MODULE.PY, boot],
            cwd=str(REPO),
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(self.tail())
            try:
                status, _ = _INSTANCE_MODULE.request("GET", self.base + "/v1/health")
                if status == 200:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("server did not become ready:\n" + self.tail())


def now() -> float:
    return time.monotonic()


def new_client(max_connections: int = 32) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        # uvicorn 默认 keep-alive 5s：压测的相位之间有空档，复用的空闲连接会被
        # 服务端关掉，下一个 POST 直接 ReadError —— 这是客户端连接池伪影，不是
        # 网关故障。关闭复用，让"失败"只代表网关真的失败。
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=0,
        ),
    )


def headers(token: Optional[str] = None) -> Dict[str, str]:
    merged = {"Content-Type": "application/json"}
    if token:
        merged["Authorization"] = f"Bearer {token}"
    return merged


#: 连接层瞬时故障：服务端 keep-alive 关闭与客户端复用撞车时会抛
#: ``ReadError(WinError 10054)`` / ``RemoteProtocolError``；这类错误在
#: GET 上等价于“请求可能已到达、也可能没到达”，重试不会改变服务端状态。
TRANSIENT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)
#: 非幂等方法只在“连接都没建立”时重试，避免把已执行的 POST /v1/chat 再发一次
#: （会多记账、多落历史，反而破坏用量断言）。
CONNECT_ONLY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5


def call(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    headers_out: Optional[Dict[str, str]] = None,
    retries: Optional[int] = None,
) -> Tuple[int, Any]:
    attempts = RETRY_ATTEMPTS if retries is None else max(1, retries)
    retryable = TRANSIENT_ERRORS if method.upper() in SAFE_METHODS else CONNECT_ONLY_ERRORS
    response: Optional[httpx.Response] = None
    for attempt in range(attempts):
        try:
            response = client.request(
                method, url, headers=headers(token), json=body if body is not None else None
            )
            break
        except retryable as exc:
            if attempt + 1 >= attempts:
                raise
            # 有限次重试 + 指数退避；最后一次仍失败时原样抛出，交给上层归类。
            time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
    assert response is not None
    if headers_out is not None:
        headers_out.update({key.lower(): value for key, value in response.headers.items()})
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:500]}
    return response.status_code, payload


def classify_exception(exc: BaseException) -> str:
    """把异常归类成稳定的错误类别，报告里一眼能看出是连接层还是业务层。"""
    if isinstance(exc, GatewayUnavailable):
        return f"gateway_unavailable: {exc}"
    if isinstance(exc, httpx.TransportError):
        return f"transport:{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"


def wait_ready(base: str, *, timeout: float = 60.0) -> None:
    """等服务真正就绪（``/v1/ready`` 而不是只回答 200 的 liveness）。"""
    deadline = time.time() + timeout
    last = "no attempt"
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                response = client.get(base + "/v1/ready")
            try:
                payload = response.json()
            except Exception:  # noqa: BLE001 - 非 JSON 也照常归类
                payload = {"raw": response.text[:120]}
            if response.status_code == 200 and (payload or {}).get("status") == "ready":
                return
            last = f"status={response.status_code} body={str(payload)[:120]}"
        except Exception as exc:  # noqa: BLE001 - 重试到超时后统一归类
            last = classify_exception(exc)
        time.sleep(1)
    raise GatewayUnavailable(f"服务在 {timeout:.0f}s 内未就绪：{last}")


def run_phase(ctx: "Context", name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """执行一个阶段；失败时归类为一条 FAIL 检查，而不是让整份报告 fatal。"""
    ctx.stage = name
    try:
        fn(ctx, *args, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 - 归类后继续出报告
        ctx.check(f"阶段「{name}」完整执行", False, classify_exception(exc))
        return False


@dataclass
class Sample:
    phase: str
    ok: bool
    status: int
    seconds: float
    error: str = ""
    code: str = ""


class Collector:
    """线程安全的并发结果与延迟采集器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.samples: List[Sample] = []

    def record(self, sample: Sample) -> None:
        with self._lock:
            self.samples.append(sample)

    def extend(self, samples: List[Sample]) -> None:
        with self._lock:
            self.samples.extend(samples)

    def by_phase(self) -> Dict[str, List[Sample]]:
        with self._lock:
            snapshot = list(self.samples)
        grouped: Dict[str, List[Sample]] = {}
        for sample in snapshot:
            grouped.setdefault(sample.phase, []).append(sample)
        return grouped


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Context:
    instance: Any
    base: str
    principals: List[str]
    tokens: Dict[str, str] = field(default_factory=dict)
    model_ids: Dict[str, str] = field(default_factory=dict)
    checks: List[Check] = field(default_factory=list)
    collector: Collector = field(default_factory=Collector)
    notes: List[str] = field(default_factory=list)
    rate_probe: Dict[str, str] = field(default_factory=dict)
    stage: str = "初始化"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name=name, ok=bool(ok), detail=str(detail)))
        return bool(ok)


def run_concurrent(count: int, fn: Callable[[int], Any]) -> List[Any]:
    """屏障同步后同时起跑，返回按下标排列的结果（异常按异常对象返回）。"""
    barrier = threading.Barrier(count)
    results: List[Any] = [None] * count

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = fn(index)
        except BaseException as exc:  # noqa: BLE001 - 汇总给调用方断言
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    return results


def error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("code") or "")
        if isinstance(payload.get("code"), str):
            return str(payload["code"])
    return ""


# --------------------------------------------------------------------------- #
# 场景 1：并发注册 + 并发登录
# --------------------------------------------------------------------------- #

def phase_registration(ctx: Context) -> None:
    principals = ctx.principals

    def register(index: int) -> Tuple[int, Any, float]:
        principal = principals[index]
        client = new_client(max_connections=4)
        started = now()
        try:
            status, payload = call(
                client,
                "POST",
                ctx.base + "/v1/accounts/register",
                body={
                    "principal": principal,
                    "password": BOOT_PASSWORD,
                    "display_name": f"并发账号 {index + 1}",
                },
            )
            return status, payload, now() - started
        finally:
            client.close()

    results = run_concurrent(len(principals), register)
    successes: List[str] = []
    conflicts = 0
    other: List[str] = []
    for principal, item in zip(principals, results):
        if isinstance(item, BaseException):
            other.append(f"{principal}: EXC {item!r}")
            ctx.collector.record(Sample("register", False, 0, 0.0, "exception"))
            continue
        status, payload, elapsed = item
        ctx.collector.record(
            Sample("register", 200 <= status < 300, status, elapsed, code=error_code(payload))
        )
        if 200 <= status < 300:
            successes.append(principal)
        elif status == 409:
            conflicts += 1
        else:
            other.append(f"{principal}: {status} {payload}")

    ctx.check(
        "并发注册只产生一个根",
        len(successes) == 1 and conflicts == len(principals) - 1,
        f"success={successes} conflict={conflicts} other={other}",
    )
    if successes:
        ctx.tokens["root"] = ""  # 根稍后统一登录
        ctx.principals = [successes[0]] + [p for p in principals if p != successes[0]]
        ctx.notes.append(f"根账号={successes[0]}")
    else:
        raise RuntimeError(f"没有账号注册成功：{other}")

    root_token = _login(ctx, successes[0], BOOT_PASSWORD)
    ctx.tokens[successes[0]] = root_token
    status, tree = call(new_client(), "GET", ctx.base + "/v1/accounts/tree", token=root_token)
    ctx.check("注册后账号树只有一个根", status == 200 and _flatten(tree.get("tree", {})).count(successes[0]) == 1, str(tree)[:300])


def _flatten(node: Dict[str, Any]) -> List[str]:
    if not isinstance(node, dict) or not node.get("principal"):
        return []
    result = [str(node["principal"])]
    for child in node.get("children") or []:
        result.extend(_flatten(child))
    return result


def _login(ctx: Context, principal: str, password: str) -> str:
    client = new_client(max_connections=4)
    try:
        status, payload = call(
            client,
            "POST",
            ctx.base + "/v1/auth/login",
            body={"principal": principal, "secret": password, "client": "web"},
        )
    finally:
        client.close()
    if status != 200 or not payload.get("access_token"):
        raise RuntimeError(f"登录失败 principal={principal} status={status} body={payload}")
    return str(payload["access_token"])


def phase_members(ctx: Context) -> None:
    root = ctx.principals[0]
    root_token = ctx.tokens[root]
    members = ctx.principals[1:]
    created: Dict[str, str] = {}
    client = new_client(max_connections=8)
    try:
        for index, principal in enumerate(members):
            # 前 3 个成员允许主账号借用 Key（用于场景 3）。
            status, payload = call(
                client,
                "POST",
                ctx.base + "/v1/accounts",
                token=root_token,
                body={
                    "principal": principal,
                    "display_name": f"成员 {principal}",
                    "allow_key_sharing": index < 3,
                },
            )
            if status not in (200, 201):
                raise RuntimeError(f"创建成员失败 {principal}: {status} {payload}")
            created[principal] = str(payload["activation_code"])

        activated: Dict[str, str] = {}
        for principal, code in created.items():
            status, payload = call(
                client,
                "POST",
                ctx.base + "/v1/accounts/activate",
                body={"principal": principal, "code": code, "password": MEMBER_PASSWORD},
            )
            if status != 200 or not payload.get("access_token"):
                raise RuntimeError(f"激活成员失败 {principal}: {status} {payload}")
            activated[principal] = str(payload["access_token"])
        ctx.tokens.update(activated)

        status, tree = call(client, "GET", ctx.base + "/v1/accounts/tree", token=root_token)
        flat = _flatten(tree.get("tree", {})) if status == 200 else []
        ctx.check(
            "账号树 8 个唯一 principal、无重复",
            status == 200 and len(flat) == len(ctx.principals) and len(set(flat)) == len(flat),
            f"tree={flat}",
        )
    finally:
        client.close()


def phase_login(ctx: Context) -> None:
    principals = ctx.principals

    def login(index: int) -> Tuple[int, Any, float]:
        principal = principals[index]
        client = new_client(max_connections=4)
        started = now()
        try:
            status, payload = call(
                client,
                "POST",
                ctx.base + "/v1/auth/login",
                body={"principal": principal, "secret": _password_for(ctx, principal), "client": "web"},
            )
            return status, payload, now() - started
        finally:
            client.close()

    results = run_concurrent(len(principals), login)
    fresh: Dict[str, str] = {}
    failures: List[str] = []
    for principal, item in zip(principals, results):
        if isinstance(item, BaseException):
            failures.append(f"{principal}: EXC {item!r}")
            continue
        status, payload, elapsed = item
        ctx.collector.record(
            Sample("login", status == 200, status, elapsed, code=error_code(payload))
        )
        if status == 200 and payload.get("access_token"):
            fresh[principal] = str(payload["access_token"])
        else:
            failures.append(f"{principal}: {status} {payload}")

    ctx.check(
        "8 个账号同时登录全部成功",
        len(fresh) == len(principals) and not failures,
        f"success={len(fresh)} failures={failures}",
    )
    ctx.check(
        "8 个令牌互不相同",
        len(set(fresh.values())) == len(fresh),
        f"unique_tokens={len(set(fresh.values()))}",
    )
    ctx.tokens.update(fresh)

    def whoami(index: int) -> Tuple[str, int, Any]:
        principal = principals[index]
        client = new_client(max_connections=4)
        try:
            status, payload = call(client, "GET", ctx.base + "/v1/auth/me", token=fresh[principal])
            return principal, status, payload
        finally:
            client.close()

    who = run_concurrent(len(principals), whoami)
    mismatches = [
        item for item in who if isinstance(item, BaseException) or item[1] != 200 or item[2].get("principal") != item[0]
    ]
    ctx.check("令牌主体与账号一一对应", not mismatches, f"mismatches={mismatches[:3]}")


def _password_for(ctx: Context, principal: str) -> str:
    return BOOT_PASSWORD if principal == ctx.principals[0] else MEMBER_PASSWORD


# --------------------------------------------------------------------------- #
# 模型配置
# --------------------------------------------------------------------------- #

def phase_models(ctx: Context) -> None:
    root_token = ctx.tokens[ctx.principals[0]]
    client = new_client(max_connections=8)
    try:
        for principal in ctx.principals:
            extra: Dict[str, str] = {}
            status, payload = call(
                client,
                "POST",
                ctx.base + "/v1/models",
                token=root_token,
                body={
                    "name": f"mock-{principal}",
                    "provider": MODEL_PROVIDER,
                    "model": f"mock-{principal}",
                    "enabled": True,
                    "is_default": principal == ctx.principals[0],
                    "owner_principal": principal,
                },
                headers_out=extra,
            )
            if status not in (200, 201) or not payload.get("id"):
                raise RuntimeError(f"创建模型失败 {principal}: {status} {payload}")
            ctx.model_ids[principal] = str(payload["id"])
            if "x-rate-limit-remaining" in extra:
                ctx.rate_probe["control_remaining"] = extra["x-rate-limit-remaining"]

        status, payload = call(
            client, "GET", ctx.base + "/v1/models", token=ctx.tokens[ctx.principals[1]]
        )
    finally:
        client.close()

    visible_owners = {
        str(item.get("owner_principal") or "") for item in (payload.get("models") or [])
    } if status == 200 else set()
    ctx.check(
        "成员只看到自己的模型",
        status == 200 and visible_owners == {ctx.principals[1]},
        f"owners={visible_owners}",
    )


# --------------------------------------------------------------------------- #
# 场景 2：8 路并发对话，互不串话、分别入账
# --------------------------------------------------------------------------- #

def _usage(ctx: Context, client: httpx.Client, principal: str) -> Dict[str, Any]:
    status, payload = call(
        client, "GET", ctx.base + f"/v1/accounts/{principal}/usage", token=ctx.tokens[principal]
    )
    if status != 200:
        raise RuntimeError(f"读取用量失败 {principal}: {status} {payload}")
    return payload


def _history(ctx: Context, client: httpx.Client, principal: str, session_id: str) -> List[Dict[str, Any]]:
    status, payload = call(
        client,
        "GET",
        ctx.base + f"/v1/chat/history?session_id={session_id}",
        token=ctx.tokens[principal],
    )
    if status != 200:
        raise RuntimeError(f"读取历史失败 {principal}/{session_id}: {status} {payload}")
    return list(payload.get("history") or [])


def phase_isolated_conversations(ctx: Context, rounds: int = 3) -> None:
    # 所有账号故意用**同一个** session_id：隔离必须来自 principal，而不是
    # 客户端恰好取了不同的会话名。
    session_id = "shared-default"
    before: Dict[str, int] = {}
    before_actor_calls: Dict[str, int] = {}
    probe: Dict[str, str] = {}
    with new_client(max_connections=16) as client:
        for principal in ctx.principals:
            payload = _usage(ctx, client, principal)
            before[principal] = int(payload.get("calls") or 0)
            if principal == ctx.principals[0]:
                before_actor_calls = {
                    str(row.get("actor")): int(row.get("calls") or 0)
                    for row in (payload.get("by_actor") or [])
                }

    def conversation(index: int) -> List[Sample]:
        principal = ctx.principals[index]
        client = new_client(max_connections=4)
        samples: List[Sample] = []
        try:
            for round_index in range(rounds):
                nonce = f"{principal}-nonce-{round_index}"
                started = now()
                extra: Dict[str, str] = {}
                try:
                    status, payload = call(
                        client,
                        "POST",
                        ctx.base + "/v1/chat",
                        token=ctx.tokens[principal],
                        body={
                            "message": nonce,
                            "session_id": session_id,
                            "model_id": ctx.model_ids[principal],
                            "use_knowledge": False,
                        },
                        headers_out=extra if round_index == 0 else None,
                    )
                    if round_index == 0 and "x-rate-limit-remaining" in extra:
                        probe[principal] = extra["x-rate-limit-remaining"]
                    ok = status == 200 and payload.get("status") == "completed"
                    samples.append(
                        Sample("chat-isolated", ok, status, now() - started, code=error_code(payload))
                    )
                except Exception as exc:  # noqa: BLE001
                    samples.append(Sample("chat-isolated", False, 0, now() - started, repr(exc)))
        finally:
            client.close()
        return samples

    results = run_concurrent(len(ctx.principals), conversation)
    failures = 0
    for item in results:
        if isinstance(item, BaseException):
            failures += 1
            continue
        ctx.collector.extend(item)
        failures += sum(1 for sample in item if not sample.ok)
    ctx.check("8 路并发对话全部成功", failures == 0, f"failures={failures}")
    # 共享桶（按 IP 退化）时，前面 register/login/models 已经消耗掉大半额度，
    # 首个 chat 的余量会掉到几十甚至更低；按 principal 分桶则每个人都接近满桶。
    floor = min((int(value) for value in probe.values()), default=-1)
    ctx.check(
        "限流按 principal 分桶（首轮余量均接近满桶）",
        len(probe) == len(ctx.principals) and floor >= 90,
        f"x-rate-limit-remaining={probe}",
    )
    root = ctx.principals[0]
    control_remaining = ctx.rate_probe.get("control_remaining")
    chat_remaining = probe.get(root)
    ctx.check(
        "控制面与聊天分桶（管理调用不挤占聊天额度）",
        bool(control_remaining)
        and bool(chat_remaining)
        and int(chat_remaining) - int(control_remaining) >= 5,
        f"root: chat={chat_remaining} control={control_remaining}",
    )

    crossed: List[str] = []
    with new_client(max_connections=16) as client:
        try:
            for principal in ctx.principals:
                history = _history(ctx, client, principal, session_id)
                own_nonces = [f"{principal}-nonce-{i}" for i in range(rounds)]
                contents = [str(row.get("content") or "") for row in history]
                expected = rounds * 2
                if len(history) != expected:
                    crossed.append(f"{principal}: history={len(history)} expected={expected}")
                if not all(nonce in contents for nonce in own_nonces):
                    crossed.append(f"{principal}: 自己的消息缺失")
                for other in ctx.principals:
                    if other == principal:
                        continue
                    leaked = [nonce for nonce in own_nonces if any(f"{other}-nonce" in content for content in contents)]
                    if leaked:
                        crossed.append(f"{principal}: 混入 {other} 的消息 {leaked}")
                after_payload = _usage(ctx, client, principal)
                if principal == ctx.principals[0]:
                    # 根的 calls 覆盖整棵子树，按 by_actor 校验根自己的调用增量。
                    actor_calls = {
                        str(row.get("actor")): int(row.get("calls") or 0)
                        for row in (after_payload.get("by_actor") or [])
                    }
                    delta = actor_calls.get(principal, 0) - before_actor_calls.get(principal, 0)
                else:
                    delta = int(after_payload.get("calls") or 0) - before[principal]
                if delta != rounds:
                    crossed.append(
                        f"{principal}: usage 增量 {delta}，预期 +{rounds}"
                    )
        except Exception as exc:  # noqa: BLE001 - 读取失败如实记 FAIL，不打断整份报告
            crossed.append(f"校验读取失败：{classify_exception(exc)}")
    ctx.check("会话历史互不串、用量分别入账", not crossed, " | ".join(crossed) or "ok")


# --------------------------------------------------------------------------- #
# 场景 3：根账号借用 3 个子账号模型
# --------------------------------------------------------------------------- #

def phase_borrow(ctx: Context, rounds: int = 4) -> None:
    root = ctx.principals[0]
    lenders = ctx.principals[1:4]
    with new_client(max_connections=8) as client:
        before_usage = {p: int(_usage(ctx, client, p).get("calls") or 0) for p in lenders}
        before_root = _usage(ctx, client, root)
        before_actor = {
            str(row.get("actor")): int(row.get("calls") or 0)
            for row in (before_root.get("by_actor") or [])
        }
        before_owner = {
            str(row.get("key_owner")): int(row.get("calls") or 0)
            for row in (before_root.get("by_key_owner") or [])
        }

    def worker(index: int) -> List[Sample]:
        lender = lenders[index]
        client = new_client(max_connections=4)
        samples: List[Sample] = []
        try:
            for round_index in range(rounds):
                started = now()
                try:
                    status, payload = call(
                        client,
                        "POST",
                        ctx.base + "/v1/chat",
                        token=ctx.tokens[root],
                        body={
                            "message": f"borrow-{lender}-{round_index}",
                            "session_id": f"root-borrow-{index}",
                            "model_id": ctx.model_ids[lender],
                            "use_knowledge": False,
                        },
                    )
                    ok = status == 200 and payload.get("status") == "completed"
                    samples.append(
                        Sample("borrow", ok, status, now() - started, code=error_code(payload))
                    )
                except Exception as exc:  # noqa: BLE001
                    samples.append(Sample("borrow", False, 0, now() - started, repr(exc)))
        finally:
            client.close()
        return samples

    results = run_concurrent(len(lenders), worker)
    failures = 0
    for item in results:
        if isinstance(item, BaseException):
            failures += 1
            continue
        ctx.collector.extend(item)
        failures += sum(1 for sample in item if not sample.ok)
    ctx.check("根账号借用 3 个成员模型全部成功", failures == 0, f"failures={failures}")

    problems: List[str] = []
    with new_client(max_connections=8) as client:
        try:
            for principal in lenders:
                after = int(_usage(ctx, client, principal).get("calls") or 0)
                if after != before_usage[principal]:
                    problems.append(
                        f"{principal}: 成员侧 calls 被改写 {before_usage[principal]}->{after}"
                    )
            after_root = _usage(ctx, client, root)
            after_actor = {
                str(row.get("actor")): int(row.get("calls") or 0)
                for row in (after_root.get("by_actor") or [])
            }
            after_owner = {
                str(row.get("key_owner")): int(row.get("calls") or 0)
                for row in (after_root.get("by_key_owner") or [])
            }
            actor_delta = after_actor.get(root, 0) - before_actor.get(root, 0)
            if actor_delta != rounds * len(lenders):
                problems.append(f"根 by_actor 增量 {actor_delta}，预期 {rounds * len(lenders)}")
            for lender in lenders:
                delta = after_owner.get(lender, 0) - before_owner.get(lender, 0)
                if delta != rounds:
                    problems.append(f"{lender}: by_key_owner 增量 {delta}，预期 {rounds}")
            for index, lender in enumerate(lenders):
                history = _history(ctx, client, root, f"root-borrow-{index}")
                if not any(lender in str(row.get("content") or "") for row in history):
                    problems.append(f"{lender}: 根会话历史里找不到借用记录")
        except Exception as exc:  # noqa: BLE001 - 读取失败如实记 FAIL，不打断整份报告
            problems.append(f"校验读取失败：{classify_exception(exc)}")
    ctx.check("借用记账：成员不变、根侧可核对", not problems, " | ".join(problems) or "ok")


# --------------------------------------------------------------------------- #
# 场景 4：同一账号两个标签页并发写同一会话
# --------------------------------------------------------------------------- #

def phase_two_tabs(ctx: Context, rounds: int = 12) -> None:
    principal = ctx.principals[4]
    session_id = "tab-shared"
    barrier = threading.Barrier(2)

    def tab(tab_index: int) -> List[Sample]:
        client = new_client(max_connections=2)
        samples: List[Sample] = []
        try:
            for round_index in range(rounds):
                barrier.wait(timeout=30)
                started = now()
                try:
                    status, payload = call(
                        client,
                        "POST",
                        ctx.base + "/v1/chat",
                        token=ctx.tokens[principal],
                        body={
                            "message": f"tab{tab_index}-msg-{round_index}",
                            "session_id": session_id,
                            "model_id": ctx.model_ids[principal],
                            "use_knowledge": False,
                        },
                    )
                    ok = status == 200 and payload.get("status") == "completed"
                    samples.append(
                        Sample("two-tabs", ok, status, now() - started, code=error_code(payload))
                    )
                except Exception as exc:  # noqa: BLE001
                    samples.append(Sample("two-tabs", False, 0, now() - started, repr(exc)))
        finally:
            client.close()
        return samples

    results = run_concurrent(2, tab)
    failures = 0
    for item in results:
        if isinstance(item, BaseException):
            failures += 1
            continue
        ctx.collector.extend(item)
        failures += sum(1 for sample in item if not sample.ok)

    problems: List[str] = []
    if failures:
        problems.append(f"{failures} 个请求失败")
    with new_client(max_connections=4) as client:
        try:
            history = _history(ctx, client, principal, session_id)
            contents = [str(row.get("content") or "") for row in history]
            expected = rounds * 2 * 2
            if len(history) != expected:
                problems.append(f"历史条数 {len(history)}，预期 {expected}")
            missing = [
                f"tab{tab}-msg-{index}"
                for tab in range(2)
                for index in range(rounds)
                if f"tab{tab}-msg-{index}" not in contents
            ]
            if missing:
                problems.append(f"丢失消息 {missing[:6]}")
        except Exception as exc:  # noqa: BLE001 - 读取失败如实记 FAIL，不打断整份报告
            problems.append(f"校验读取失败：{classify_exception(exc)}")

    db_path = Path(ctx.instance.root) / "conversations.sqlite3"
    duplicate_turns: List[Tuple[int, int]] = []
    if db_path.exists():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT turn, COUNT(*) FROM conversation_turns "
                "WHERE principal_id = ? GROUP BY turn HAVING COUNT(*) != 2 "
                "ORDER BY turn",
                (f"liuhao-{principal}-{session_id}",),
            ).fetchall()
            duplicate_turns = [(int(row[0]), int(row[1])) for row in rows]
        if duplicate_turns:
            problems.append(f"轮次号不唯一/不完整：{duplicate_turns[:8]}")
    ctx.check("两个标签页并发写同一会话不丢不覆盖", not problems, " | ".join(problems) or "ok")


# --------------------------------------------------------------------------- #
# 场景 5：持续并发压测
# --------------------------------------------------------------------------- #

class MetricsSampler(threading.Thread):
    def __init__(self, pid: int, root: str, interval: float = 5.0) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.root = Path(root)
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: List[Dict[str, Any]] = []
        self.lost_at: Optional[float] = None

    def _snapshot(self) -> Optional[Dict[str, Any]]:
        try:
            parent = psutil.Process(self.pid)
            processes = [parent] + parent.children(recursive=True)
        except psutil.Error:
            # 进程没了：不要记 0，否则斜率会被"假归零"污染。
            return None
        rss = handles = threads = 0
        for process in processes:
            try:
                rss += int(process.memory_info().rss)
            except psutil.Error:
                pass
            try:
                handles += int(process.num_handles())
            except (psutil.Error, AttributeError):
                try:
                    handles += int(process.num_fds())
                except (psutil.Error, AttributeError):
                    pass
            try:
                threads += int(process.num_threads())
            except psutil.Error:
                pass
        db_bytes = 0
        for path in self.root.rglob("*"):
            if path.is_file() and (
                path.suffix in {".db", ".sqlite3"} or path.name.endswith((".db-wal", ".sqlite3-wal"))
            ):
                try:
                    db_bytes += path.stat().st_size
                except OSError:
                    pass
        return {
            "t": time.time(),
            "rss": rss,
            "handles": handles,
            "threads": threads,
            "db_bytes": db_bytes,
            "processes": len(processes),
        }

    def run(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self._snapshot()
            if snapshot is not None:
                self.samples.append(snapshot)
            elif self.lost_at is None:
                self.lost_at = time.time()
            self.stop_event.wait(self.interval)

    def stop(self) -> List[Dict[str, Any]]:
        self.stop_event.set()
        self.join(timeout=10)
        return self.samples


def _slope(points: List[Tuple[float, float]]) -> float:
    """最小二乘斜率（单位/秒）。"""
    if len(points) < 2:
        return 0.0
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def phase_soak(
    ctx: Context,
    *,
    seconds: int,
    tick_seconds: float,
    burst: int,
) -> Dict[str, Any]:
    if seconds <= 0:
        return {"skipped": True}

    principals = ctx.principals
    rounds_per_account = 4
    sampler = MetricsSampler(ctx.instance.proc.pid, ctx.instance.root, interval=5.0)
    sampler.start()

    clients = {principal: new_client(max_connections=burst + 4) for principal in principals}
    stop_at = now() + seconds
    tick = 0
    in_flight_peak = 0
    failures = 0

    def job(principal: str, slot: int) -> Sample:
        index = principals.index(principal)
        session_id = f"soak-{index}-{slot % rounds_per_account}"
        started = now()
        extra: Dict[str, str] = {}
        try:
            status, payload = call(
                clients[principal],
                "POST",
                ctx.base + "/v1/chat",
                token=ctx.tokens[principal],
                body={
                    "message": f"soak-{tick}-{index}-{slot}-{int(started * 1000) % 100000}",
                    "session_id": session_id,
                    "model_id": ctx.model_ids[principal],
                    "use_knowledge": False,
                },
                headers_out=extra,
            )
            ok = status == 200 and payload.get("status") == "completed"
            code = error_code(payload)
            if status == 429 and not code:
                # 两个限流面都回 429，用 detail 文案 + 头里的 limit 区分来源。
                detail = str((payload or {}).get("detail") or "")
                face = "global" if "global rate limit" in detail else "business"
                code = f"{face}_rate_limit(limit={extra.get('x-rate-limit-limit', '?')})"
            return Sample("soak", ok, status, now() - started, code=code)
        except httpx.TransportError as exc:
            return Sample("soak", False, 0, now() - started, f"transport:{type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001
            return Sample("soak", False, 0, now() - started, repr(exc))

    try:
        with futures.ThreadPoolExecutor(max_workers=len(principals) * burst) as pool:
            while now() < stop_at:
                tick_started = now()
                tasks = [
                    pool.submit(job, principal, slot)
                    for principal in principals
                    for slot in range(burst)
                ]
                in_flight_peak = max(in_flight_peak, len(tasks))
                tick_samples: List[Sample] = []
                try:
                    completed = list(futures.as_completed(tasks, timeout=180))
                except futures.TimeoutError as exc:
                    raise GatewayUnavailable(
                        f"tick {tick} 等待响应超时（180s）：{exc}"
                    ) from exc
                for task in completed:
                    try:
                        sample = task.result()
                    except Exception as exc:  # noqa: BLE001 - 不让单条任务拖垮整份报告
                        sample = Sample("soak", False, 0, 0.0, classify_exception(exc))
                    tick_samples.append(sample)
                if len(tick_samples) == len(tasks) and all(
                    sample.error.startswith("transport:") for sample in tick_samples
                ):
                    # 整批都是连接层失败 → 网关进程已不在，继续跑没有意义。
                    raise GatewayUnavailable(
                        f"tick {tick} 全部 {len(tick_samples)} 个请求连接失败"
                    )
                if len(tick_samples) == len(tasks) and all(
                    sample.status == 503 for sample in tick_samples
                ):
                    # 端口还在、应用已进入关闭流程：这不是"负载下的失败率"，
                    # 计入矩阵会污染结论，直接判定网关不可用。
                    raise GatewayUnavailable(
                        f"tick {tick} 全部 {len(tick_samples)} 个请求返回 503"
                    )
                for sample in tick_samples:
                    ctx.collector.record(sample)
                    if not sample.ok:
                        failures += 1
                elapsed = now() - tick_started
                tick += 1
                remaining = tick_seconds - elapsed
                if remaining > 0:
                    time.sleep(min(remaining, max(0.0, stop_at - now())))
    finally:
        for client in clients.values():
            client.close()
        metrics = sampler.stop()

    rss_points = [(sample["t"], sample["rss"]) for sample in metrics]
    handle_points = [(sample["t"], sample["handles"]) for sample in metrics]
    db_points = [(sample["t"], sample["db_bytes"]) for sample in metrics]
    return {
        "skipped": False,
        "ticks": tick,
        "requests": tick * len(principals) * burst,
        "failures": failures,
        "peak_in_flight": in_flight_peak,
        "metrics": metrics,
        "rss_start": metrics[0]["rss"] if metrics else 0,
        "rss_end": metrics[-1]["rss"] if metrics else 0,
        "rss_peak": max((sample["rss"] for sample in metrics), default=0),
        "handles_start": metrics[0]["handles"] if metrics else 0,
        "handles_end": metrics[-1]["handles"] if metrics else 0,
        "handles_peak": max((sample["handles"] for sample in metrics), default=0),
        "db_bytes_start": metrics[0]["db_bytes"] if metrics else 0,
        "db_bytes_end": metrics[-1]["db_bytes"] if metrics else 0,
        "threads_peak": max((sample["threads"] for sample in metrics), default=0),
        "rss_slope_bytes_per_min": _slope(rss_points) * 60.0,
        "handles_slope_per_min": _slope(handle_points) * 60.0,
        "db_slope_bytes_per_min": _slope(db_points) * 60.0,
        "process_lost_at": sampler.lost_at,
    }


# --------------------------------------------------------------------------- #
# 统计与报告
# --------------------------------------------------------------------------- #

def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def summarize(collector: Collector) -> Dict[str, Any]:
    grouped = collector.by_phase()
    result: Dict[str, Any] = {}
    for phase, samples in grouped.items():
        durations = [sample.seconds for sample in samples if sample.seconds > 0]
        failures = [sample for sample in samples if not sample.ok]
        classes: Dict[str, int] = {}
        for sample in failures:
            if sample.error:
                key = f"exception:{sample.error.split('(')[0].strip()[:60]}"
            elif sample.code:
                key = f"http_{sample.status}:{sample.code}"
            elif sample.status >= 400:
                key = f"http_{sample.status}"
            else:
                key = "unknown"
            classes[key] = classes.get(key, 0) + 1
        result[phase] = {
            "requests": len(samples),
            "success": len(samples) - len(failures),
            "failure": len(failures),
            "error_classes": classes,
            "latency_ms": {
                "p50": round(percentile(durations, 0.50) * 1000, 1),
                "p95": round(percentile(durations, 0.95) * 1000, 1),
                "p99": round(percentile(durations, 0.99) * 1000, 1),
                "max": round(max(durations) * 1000, 1) if durations else 0.0,
                "mean": round(statistics.mean(durations) * 1000, 1) if durations else 0.0,
            },
        }
    return result


def scan_logs(ctx: Context) -> Dict[str, Any]:
    files: List[Path] = [Path(ctx.instance.log_path)]
    log_dir = Path(ctx.instance.root) / "logs"
    if log_dir.exists():
        files.extend(log_dir.glob("*.log"))
    locked = 0
    tracebacks = 0
    hits: List[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        locked += lowered.count("database is locked")
        tracebacks += text.count("Traceback (most recent call last)")
        if "database is locked" in lowered:
            hits.append(f"{path.name}: database is locked")
    return {
        "files": [str(path) for path in files],
        "database_locked": locked,
        "tracebacks": tracebacks,
        "hits": hits,
    }


def db_integrity(ctx: Context) -> Dict[str, Any]:
    root = Path(ctx.instance.root)
    results: Dict[str, Any] = {}
    for name in [
        "conversations.sqlite3",
        "usage.sqlite3",
        "audit_store.db",
        "models.sqlite3",
        "memory.sqlite3",
    ]:
        path = root / name
        if not path.exists():
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                status = connection.execute("PRAGMA quick_check").fetchone()
                results[name] = str(status[0]) if status else "unknown"
        except sqlite3.Error as exc:
            results[name] = f"error: {exc}"
    usage = root / "usage.sqlite3"
    if usage.exists():
        try:
            with sqlite3.connect(f"file:{usage}?mode=ro", uri=True) as connection:
                results["usage_rows"] = int(
                    connection.execute("SELECT COUNT(*) FROM account_usage_events").fetchone()[0]
                )
        except sqlite3.Error as exc:
            results["usage_rows"] = f"error: {exc}"
    return results


def print_report(report: Dict[str, Any]) -> None:
    print("\n## 并发矩阵\n")
    print("| 场景 | 并发 | 成功 | 失败 | 错误分类 |")
    print("| --- | --- | --- | --- | --- |")
    concurrency = {
        "register": report["options"]["accounts"],
        "login": report["options"]["accounts"],
        "chat-isolated": report["options"]["accounts"],
        "borrow": 3,
        "two-tabs": 2,
        "soak": report.get("soak", {}).get("peak_in_flight", 0),
    }
    for phase, data in (report.get("summary") or {}).items():
        classes = ", ".join(f"{key}×{value}" for key, value in data["error_classes"].items()) or "-"
        print(
            f"| {phase} | {concurrency.get(phase, '-')} | {data['success']} | "
            f"{data['failure']} | {classes} |"
        )

    print("\n## 延迟（毫秒）\n")
    print("| 场景 | 请求数 | p50 | p95 | p99 | max | mean |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for phase, data in (report.get("summary") or {}).items():
        latency = data["latency_ms"]
        print(
            f"| {phase} | {data['requests']} | {latency['p50']} | {latency['p95']} | "
            f"{latency['p99']} | {latency['max']} | {latency['mean']} |"
        )

    soak = report.get("soak") or {}
    if soak and not soak.get("skipped"):
        print("\n## 持续压测资源曲线\n")
        print(
            f"- 请求：{soak['requests']}（{soak['ticks']} tick，峰值在飞 {soak['peak_in_flight']}）"
        )
        print(
            f"- RSS：{soak['rss_start'] / 1e6:.1f} MB → {soak['rss_end'] / 1e6:.1f} MB"
            f"（峰值 {soak['rss_peak'] / 1e6:.1f} MB，斜率 {soak['rss_slope_bytes_per_min'] / 1e6:+.2f} MB/min）"
        )
        print(
            f"- 句柄：{soak['handles_start']} → {soak['handles_end']}"
            f"（峰值 {soak['handles_peak']}，斜率 {soak['handles_slope_per_min']:+.1f}/min）"
        )
        print(
            f"- 数据库体积：{soak['db_bytes_start'] / 1e6:.1f} MB → {soak['db_bytes_end'] / 1e6:.1f} MB"
            f"（斜率 {soak['db_slope_bytes_per_min'] / 1e6:+.2f} MB/min）"
        )

    print("\n## 检查项\n")
    for check in report.get("checks") or []:
        print(f"- [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}" + (f" — {check['detail']}" if check["detail"] else ""))

    print("\n## 存储与日志\n")
    print(f"- SQLite 完整性：{json.dumps(report.get('db_integrity') or {}, ensure_ascii=False)}")
    logs = report.get("logs") or {}
    print(
        f"- 日志 `database is locked` 命中：{logs.get('database_locked', 'n/a')}；"
        f"Traceback：{logs.get('tracebacks', 'n/a')}"
    )
    print(
        f"- 隔离实例数据目录：{report['instance_root']}（保留，便于复核）"
    )


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="鎏灏多用户并发压测（真实网关）")
    parser.add_argument("--accounts", type=int, default=8)
    parser.add_argument("--soak-seconds", type=int, default=1200)
    parser.add_argument("--tick-seconds", type=float, default=10.0)
    parser.add_argument("--burst", type=int, default=8)
    parser.add_argument("--skip-soak", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.accounts < 5:
        raise SystemExit("--accounts 至少 5（场景 3/4 需要根 + 4 个成员）")

    principals = [f"conc-user-{index + 1}" for index in range(args.accounts)]
    instance = IsolatedInstance(
        "liuhao-concurrency-",
        env_extra={
            "LIUHAO_CHAT_MAX_SESSIONS": "256",
            "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
        },
    )
    ctx = Context(instance=instance, base=instance.base, principals=principals)
    started = time.time()
    report: Dict[str, Any] = {
        "options": {
            "accounts": args.accounts,
            "soak_seconds": 0 if args.skip_soak else args.soak_seconds,
            "tick_seconds": args.tick_seconds,
            "burst": args.burst,
            "provider": MODEL_PROVIDER,
        },
        "instance_root": instance.root,
        "base": instance.base,
    }
    exit_code = 0
    try:
        ready = run_phase(ctx, "等待服务就绪", lambda _ctx: wait_ready(instance.base))
        phases = [
            ("并发注册", phase_registration),
            ("创建成员", phase_members),
            ("并发登录", phase_login),
            ("模型配置", phase_models),
            ("隔离对话", phase_isolated_conversations),
            ("借用记账", phase_borrow),
            ("双标签页", phase_two_tabs),
        ]
        # 后面每个阶段都依赖前一个阶段的账号/令牌，前置失败时不再空跑，
        # 但失败已经落成一条 FAIL 检查，报告仍会完整产出。
        aborted = not ready
        if ready:
            for name, fn in phases:
                if not run_phase(ctx, name, fn):
                    aborted = True
                    break
        if not aborted:
            ctx.stage = "持续压测"
            if args.skip_soak:
                report["soak"] = {"skipped": True}
            else:
                try:
                    report["soak"] = phase_soak(
                        ctx,
                        seconds=args.soak_seconds,
                        tick_seconds=args.tick_seconds,
                        burst=args.burst,
                    )
                except Exception as exc:  # noqa: BLE001 - 归类成 FAIL，不写 fatal
                    report["soak"] = {"skipped": False, "error": classify_exception(exc)}
                    ctx.check("阶段「持续压测」完整执行", False, classify_exception(exc))
        soak = report.get("soak") or {}
        if not soak.get("skipped") and "failures" in soak:
            ctx.check(
                "持续压测无失败请求",
                soak["failures"] == 0,
                f"failures={soak['failures']} requests={soak['requests']}",
            )
        report["stage"] = ctx.stage
        report["summary"] = summarize(ctx.collector)
        report["logs"] = scan_logs(ctx)
        report["db_integrity"] = db_integrity(ctx)
        usage_rows = report["db_integrity"].get("usage_rows")
        if isinstance(usage_rows, int):
            expected_usage = sum(
                report["summary"].get(phase, {}).get("success", 0)
                for phase in ("chat-isolated", "borrow", "two-tabs", "soak")
            )
            ctx.check(
                "用量账本行数 = 成功轮次数",
                usage_rows == expected_usage,
                f"rows={usage_rows} expected={expected_usage}",
            )
        report["checks"] = [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in ctx.checks
        ]
        report["notes"] = ctx.notes
        report["duration_seconds"] = round(time.time() - started, 1)
        report["ok"] = all(check.ok for check in ctx.checks)
        if not report["ok"]:
            exit_code = 1
        if report["logs"]["database_locked"]:
            exit_code = 1
        if report["logs"]["tracebacks"]:
            exit_code = 1
    except GatewayUnavailable as exc:
        print(f"\n[环境中断] 网关进程在压测中不可达：{exc}")
        report["fatal"] = f"gateway_unavailable: {exc}"
        report["stage"] = ctx.stage
        report["checks"] = [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in ctx.checks
        ]
        report["summary"] = summarize(ctx.collector)
        report["ok"] = False
        exit_code = 1
    except BaseException as exc:  # noqa: BLE001 - 打印现场再退出
        traceback.print_exc()
        report["fatal"] = f"{ctx.stage}: {classify_exception(exc)}"
        report["stage"] = ctx.stage
        report["checks"] = [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in ctx.checks
        ]
        report["summary"] = summarize(ctx.collector)
        report["ok"] = False
        exit_code = 1
    finally:
        report["stage"] = ctx.stage
        try:
            report["http_health"] = _health(instance)
        except Exception as exc:  # noqa: BLE001
            report["http_health"] = {"error": repr(exc)}
        instance.stop(cleanup=args.clean)

    out = Path(args.out) if args.out else Path(instance.root) / "report.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告 JSON：{out}")
    except OSError as exc:
        print(f"报告写入失败：{exc}")

    print_report(report)
    return exit_code


def _health(instance: Any) -> Dict[str, Any]:
    try:
        status, payload = call(new_client(max_connections=2), "GET", instance.base + "/v1/ready")
        return {"status": status, "ok": bool(payload.get("status") == "ready")}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


if __name__ == "__main__":
    raise SystemExit(main())
