"""G-10 建连层钉住强制：P4/P5 漂移复现与修复回归（socket 级）。

上一切片（2026-11-10 real-endpoint-e2e）实测确认：线程钉住对进程内直连有效，
但 httpx 客户端在 ``trust_env=True`` 时会把请求交给系统/环境代理——代理在
**自己的进程里**解析域名，线程钉住够不着，同名漂移（P4）与校验后翻转（P5）
场景下连接真实落到漂移地址（回环 TLS 记录器捕获到 ClientHello）。

本文件用进程内权威解析器（对 ``*.drift-e2e.test`` 逐次应答、模式实时可翻转，
语义等价于本地权威 DNS 响应器 TTL=0，全程不改动系统 DNS）复现该链路，并锁住
修复后的行为：

* P4：已校验并钉住的名字，flip 后建连不重新解析、不落到漂移地址；
* P5：新名字校验通过后翻转，建连仍用校验时的地址；
* fail-closed：未通过校验的目标零连接，报错中性、不泄露内网地址；
* TLS 不削弱：SNI/证书校验仍按原域名（``sni_hostname``），Host 头保持原主机名；
* 本地推理回环白名单路径行为不变（http+Key+白名单端口仍然可用）。
"""

from __future__ import annotations

import http.server
import json
import socket
import threading

import httpx
import pytest

from src.ai import net_guard as ng
from src.ai import providers as P

DRIFT_SUFFIX = "drift-e2e.test"
#: 公网应答——建连会被防火墙/远端拒绝，测试只关心"没有连到漂移地址"
PUBLIC_ANSWER = "1.1.1.1"
#: 漂移应答——等于 previous 切片里权威响应器 flip 后的回环答案
LOOPBACK_ANSWER = "127.0.0.1"


class _DriftAuthority:
    """进程内权威解析器：``*.drift-e2e.test`` 逐次应答，模式实时可翻转。

    安装方式：替换 ``net_guard._REAL_GETADDRINFO``（守卫钉住包装器的兜底）。
    这样无论 net_guard 何时被导入，漂移名字都只会从这里拿到答案，而其它
    名字走真实系统解析——与"本地权威 DNS 响应器"语义等价，但不需要碰系统
    DNS 适配器。
    """

    def __init__(self, real_getaddrinfo):
        self._real = real_getaddrinfo
        self.modes = {}      # label -> public / loopback
        self.answers = {}    # label -> 指定应答 IP（私有网段等特殊场景）
        self.wire_queries = 0

    def answer(self, host, port, family=0, type=0, proto=0, flags=0):
        name = str(host).lower().rstrip(".")
        if name.endswith(DRIFT_SUFFIX):
            self.wire_queries += 1
            label = name.split(".")[0]
            address = self.answers.get(label)
            if address is None:
                mode = self.modes.get(label, "public")
                address = LOOPBACK_ANSWER if mode == "loopback" else PUBLIC_ANSWER
            return [
                (
                    socket.AF_INET,
                    type or socket.SOCK_STREAM,
                    proto or socket.IPPROTO_TCP,
                    "",
                    (address, port),
                )
            ]
        return self._real(host, port, family, type, proto, flags)


@pytest.fixture()
def drift():
    """安装权威解析器并恢复钉住状态，测试互不污染。"""
    authority = _DriftAuthority(ng._REAL_GETADDRINFO)
    saved_real = ng._REAL_GETADDRINFO
    saved_getaddrinfo = socket.getaddrinfo
    saved_pins = getattr(ng._PIN_STATE, "pins", None)
    ng._REAL_GETADDRINFO = authority.answer
    socket.getaddrinfo = ng._pinned_getaddrinfo
    try:
        yield authority
    finally:
        ng._REAL_GETADDRINFO = saved_real
        socket.getaddrinfo = saved_getaddrinfo
        if saved_pins is None:
            if hasattr(ng._PIN_STATE, "pins"):
                del ng._PIN_STATE.pins
        else:
            ng._PIN_STATE.pins = saved_pins


class _PoolRecorder:
    """替换传输层内的 httpcore 连接池：只记录"交给建连层的请求"，不真正建连。"""

    def __init__(self):
        self.requests = []

    def handle_request(self, request):
        import httpcore

        self.requests.append(request)
        return httpcore.Response(
            status=200,
            headers=[(b"content-type", b"application/json")],
            content=b"{}",
        )


def _recording_transport(**kwargs):
    transport = P._guarded_transport_cls(httpx)(**kwargs)
    transport._pool = _PoolRecorder()
    return transport


class _TlsHitLogger:
    """回环 TLS 记录器：任何到 127.0.0.1:<port> 的连接都会被记录。"""

    def __init__(self, port=8443):
        self.hits = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, addr = self._srv.accept()
            except OSError:
                return
            hit = {"peer": addr[0], "port": addr[1]}
            try:
                conn.settimeout(2.0)
                data = conn.recv(64)
                # 0x16 = TLS handshake 首字节（ClientHello）
                hit["is_client_hello"] = bool(data) and data[0] == 0x16
            except OSError:
                hit["is_client_hello"] = False
            self.hits.append(hit)
            conn.close()

    def close(self):
        self._srv.close()


@pytest.fixture()
def drift_tls_logger():
    logger = _TlsHitLogger(port=8443)
    try:
        yield logger
    finally:
        logger.close()


class _OpenAIStubHandler(http.server.BaseHTTPRequestHandler):
    """极小 openai 兼容桩：/chat/completions 固定回 "ok"。"""

    def do_POST(self):  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "model": "stub",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 静音
        pass


class _OpenAIStubServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture()
def openai_stub():
    """回环白名单端口上的 openai 兼容桩（8000 优先，被占用则顺延白名单端口）。"""
    last_error = None
    for port in (8000, 8080, 1234, 11434):
        try:
            server = _OpenAIStubServer(("127.0.0.1", port), _OpenAIStubHandler)
            break
        except OSError as exc:  # 端口被占用
            last_error = exc
    else:
        raise RuntimeError(f"回环白名单端口全部不可用: {last_error}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# resolve_connection_target：钉住优先、未钉住重校验、失败 fail-closed
# ---------------------------------------------------------------------------


def test_resolve_connection_target_pinned_does_not_requery(drift):
    """已钉住的主机：直接返回钉住条目，flip 后也不重新查询（P4 语义）。"""
    drift.modes["p8"] = "public"
    url = "https://p8.drift-e2e.test:8443/v1"
    assert ng.resolve_connection_target(
        url, context="测试", has_credential=True
    ) == [(socket.AF_INET, PUBLIC_ANSWER)]
    drift.modes["p8"] = "loopback"
    assert ng.resolve_connection_target(
        url, context="测试", has_credential=True
    ) == [(socket.AF_INET, PUBLIC_ANSWER)]
    assert drift.wire_queries == 1  # 只有校验那一次；钉住期间零重查


def test_resolve_connection_target_revalidates_when_unpinned(drift):
    """未钉住：按同一策略重新校验并钉住（P5 语义的前半段）。"""
    drift.modes["p9"] = "public"
    url = "https://p9.drift-e2e.test:8443/v1"
    entries = ng.resolve_connection_target(url, context="测试", has_credential=True)
    assert entries == [(socket.AF_INET, PUBLIC_ANSWER)]
    assert drift.wire_queries == 1
    # 校验成功即钉住：第二次调用零线上查询
    ng.resolve_connection_target(url, context="测试", has_credential=True)
    assert drift.wire_queries == 1


def test_resolve_connection_target_rejects_private_with_neutral_message(drift):
    """解析结果掉进私网：拒绝、消息中性、不泄露内网地址。"""
    drift.answers["p10"] = "10.9.8.7"
    url = "https://p10.drift-e2e.test:8443/v1"
    with pytest.raises(ng.RuntimeTargetRejected) as exc_info:
        ng.resolve_connection_target(url, context="测试", has_credential=True)
    message = str(exc_info.value)
    assert "内网" in message
    assert "10.9.8.7" not in message and "p10" not in message


def test_resolve_connection_target_rejects_http_with_key(drift):
    """保证③（守卫侧）：带 Key 的 http 公网目标仍被拒绝，逻辑不因传输层改变。"""
    with pytest.raises(ng.RuntimeTargetRejected) as exc_info:
        ng.resolve_connection_target(
            "http://plain.drift-e2e.test:80/v1",
            context="测试",
            has_credential=True,
        )
    assert "https" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 传输层单元：建连请求必须以已校验 IP 为目标、SNI/Host 保留原域名
# ---------------------------------------------------------------------------


def test_p4_same_name_drift_connects_to_validated_ip(drift):
    """P4：已校验并钉住的名字，flip 后建连目标是校验时的地址，不重查 DNS。"""
    drift.modes["p4"] = "public"
    ng.assert_safe_runtime_target(
        "https://p4.drift-e2e.test:8443/v1", context="测试", has_credential=True
    )
    drift.modes["p4"] = "loopback"
    transport = _recording_transport(context="测试", has_credential=True)
    request = httpx.Request(
        "POST", "https://p4.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    response = transport.handle_request(request)
    assert response.status_code == 200
    pool = transport._pool
    core = pool.requests[0]
    # 建连目标 = 已校验地址，不是漂移地址，也不是原主机名
    assert core.url.host == PUBLIC_ANSWER.encode()
    # TLS 不削弱：SNI/证书校验仍按原域名
    assert core.extensions.get("sni_hostname") == "p4.drift-e2e.test"
    # Host 头保持原主机名（含非常规端口）；httpx 原始头保留原大小写
    headers_map = {key.lower(): value for key, value in core.headers}
    assert headers_map[b"host"] == b"p4.drift-e2e.test:8443"
    # 建连层零重查：守卫校验 1 次，之后 flip 不触发任何线上解析
    assert drift.wire_queries == 1


def test_p5_fresh_name_flip_after_validation(drift):
    """P5：新名字校验通过后翻转，建连仍用校验时的地址。"""
    drift.modes["p5"] = "public"
    ng.assert_safe_runtime_target(
        "https://p5.drift-e2e.test:8443/v1", context="测试", has_credential=True
    )
    drift.modes["p5"] = "loopback"
    transport = _recording_transport(context="测试", has_credential=True)
    request = httpx.Request(
        "POST", "https://p5.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    transport.handle_request(request)
    core = transport._pool.requests[0]
    assert core.url.host == PUBLIC_ANSWER.encode()
    assert core.extensions.get("sni_hostname") == "p5.drift-e2e.test"
    assert drift.wire_queries == 1


def test_transport_fail_closed_when_target_now_loopback(drift):
    """无钉住且目标已漂移到回环：零连接、报错中性（fail-closed）。"""
    drift.modes["p6"] = "loopback"
    transport = _recording_transport(context="测试", has_credential=True)
    request = httpx.Request(
        "POST", "https://p6.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    with pytest.raises(httpx.ConnectError) as exc_info:
        transport.handle_request(request)
    message = str(exc_info.value)
    assert "本机回环" in message
    assert LOOPBACK_ANSWER not in message  # 不泄露漂移地址
    assert transport._pool.requests == []  # 零连接


def test_transport_fail_closed_when_target_now_private(drift):
    """无钉住且目标漂移到私网：零连接、报错中性。"""
    drift.answers["p7"] = "10.9.8.7"
    transport = _recording_transport(context="测试", has_credential=True)
    request = httpx.Request(
        "POST", "https://p7.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    with pytest.raises(httpx.ConnectError) as exc_info:
        transport.handle_request(request)
    assert "内网" in str(exc_info.value)
    assert "10.9.8.7" not in str(exc_info.value)
    assert transport._pool.requests == []


def test_transport_uses_validated_address_without_prior_guard(drift):
    """无钉住且目标正常（public）：传输层自行走同一套校验并按结果建连。"""
    drift.modes["p11"] = "public"
    transport = _recording_transport(context="测试", has_credential=True)
    request = httpx.Request(
        "POST", "https://p11.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    transport.handle_request(request)
    core = transport._pool.requests[0]
    assert core.url.host == PUBLIC_ANSWER.encode()
    assert core.extensions.get("sni_hostname") == "p11.drift-e2e.test"
    assert drift.wire_queries == 1  # 传输层重新校验的那一次


def test_explicit_proxy_keeps_hostname_but_still_validates(drift):
    """显式代理：保留主机名交由代理转发（隧道 SNI 语义），守卫校验仍生效。

    显式代理属于运营者显式信任决策；其自身 DNS 解析在代理进程内进行，
    属于代理的信任边界（known-gaps 残余面如实披露）。
    """
    drift.modes["p12"] = "public"
    ng.assert_safe_runtime_target(
        "https://p12.drift-e2e.test:8443/v1", context="测试", has_credential=True
    )
    drift.modes["p12"] = "loopback"
    transport = _recording_transport(
        context="测试", has_credential=True, proxy="http://127.0.0.1:18999"
    )
    request = httpx.Request(
        "POST", "https://p12.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    transport.handle_request(request)
    core = transport._pool.requests[0]
    assert core.url.host == b"p12.drift-e2e.test"  # 主机名未改写
    assert drift.wire_queries == 1                  # 建连层零重查（钉住生效）


def test_multi_address_targets_tried_in_order(drift):
    """已校验地址列表按序尝试：首个连接失败轮到下一个（与 create_connection 一致）。

    注意：包含回环地址的解析结果在守卫校验阶段就会被整体拒绝（P2 语义），
    所以这里用两个公网地址模拟多地址解析。
    """

    class _FirstRefusesPool(_PoolRecorder):
        def __init__(self):
            super().__init__()
            self.seen_hosts = []

        def handle_request(self, request):
            self.seen_hosts.append(bytes(request.url.host))
            if len(self.seen_hosts) == 1:
                raise httpx.ConnectError("refused")
            return super().handle_request(request)

    transport = P._guarded_transport_cls(httpx)(context="测试", has_credential=True)
    transport._pool = _FirstRefusesPool()
    # 两个公网应答（1.1.1.1 在前、8.8.8.8 在后）
    original_answer = drift.answer

    def two_answers(host, port, family=0, type=0, proto=0, flags=0):
        name = str(host).lower().rstrip(".")
        if name.endswith(DRIFT_SUFFIX):
            drift.wire_queries += 1
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_ANSWER, port)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            ]
        return original_answer(host, port, family, type, proto, flags)

    ng._REAL_GETADDRINFO = two_answers
    request = httpx.Request(
        "POST", "https://p13.drift-e2e.test:8443/v1/chat/completions", json={"m": 1}
    )
    response = transport.handle_request(request)
    assert response.status_code == 200
    pool = transport._pool
    assert pool.seen_hosts == [
        PUBLIC_ANSWER.encode(),
        b"8.8.8.8",
    ]
    # SNI/证书校验仍按原域名
    assert pool.requests[0].extensions.get("sni_hostname") == "p13.drift-e2e.test"


# ---------------------------------------------------------------------------
# provider 装配：守卫传输层就位、不再继承环境/系统代理
# ---------------------------------------------------------------------------


def test_openai_provider_wires_guarded_transport_and_no_env_proxy(
    drift, monkeypatch
):
    """保证②③的装配面：public 目标也绝不把请求交给环境/系统代理。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    provider = P.OpenAIProvider(
        "openai", "m", api_key="sk-test", base_url="https://wiring.example/v1"
    )
    sdk_client = provider._get_client()
    httpx_client = sdk_client._client
    transport = httpx_client._transport
    assert type(transport).__name__ == "GuardedHTTPTransport"
    assert transport._proxied is False
    # 环境代理不再生成挂载（挂载表全空/全 None），请求一律走守卫传输层
    assert all(value is None for value in httpx_client._mounts.values())


def test_anthropic_provider_wires_guarded_transport(drift):
    """Anthropic（httpx2 内部栈）同样走守卫传输层。"""
    provider = P.AnthropicProvider(
        "anthropic", "m", api_key="sk-test", base_url="https://wiring2.example/v1"
    )
    sdk_client = provider._get_client()
    transport = sdk_client._client._transport
    assert type(transport).__name__ == "GuardedHTTPTransport"


# ---------------------------------------------------------------------------
# socket 级端到端：真实 provider.chat 链路，漂移地址零连接
# ---------------------------------------------------------------------------


def test_p4_end_to_end_zero_drift_connections(drift, drift_tls_logger):
    """P4 端到端：钉住后同名翻转，真实 chat 链路对漂移地址零连接、零重解析。"""
    drift.modes["p4e2e"] = "public"
    provider = P.OpenAIProvider(
        "openai", "m",
        api_key="sk-test",
        base_url="https://p4e2e.drift-e2e.test:8443/v1",
        timeout=3,
    )
    provider._guard_target()          # 校验并钉住（public → 1.1.1.1）
    drift.modes["p4e2e"] = "loopback"  # 同名漂移
    wire0 = drift.wire_queries
    with pytest.raises(Exception):  # noqa: B017 - 连接失败以异常收场（超时/拒绝皆可）
        provider.chat([{"role": "user", "content": "hi"}])
    assert drift_tls_logger.hits == []       # 漂移地址零连接
    assert drift.wire_queries == wire0       # 建连层零重解析


def test_p5_end_to_end_zero_drift_connections(drift, drift_tls_logger):
    """P5 端到端：新名字校验后翻转，真实 chat 链路对漂移地址零连接。"""
    drift.modes["p5e2e"] = "public"
    provider = P.OpenAIProvider(
        "openai", "m",
        api_key="sk-test",
        base_url="https://p5e2e.drift-e2e.test:8443/v1",
        timeout=3,
    )
    provider._guard_target()
    drift.modes["p5e2e"] = "loopback"
    wire0 = drift.wire_queries
    with pytest.raises(Exception):  # noqa: B017 - 同上
        provider.chat([{"role": "user", "content": "hi"}])
    assert drift_tls_logger.hits == []
    assert drift.wire_queries == wire0


def test_fail_closed_end_to_end_zero_connections(drift, drift_tls_logger):
    """无钉住且目标已漂移到回环：整个 chat 链路零连接、错误中性。"""
    drift.modes["p6e2e"] = "loopback"
    provider = P.OpenAIProvider(
        "openai", "m",
        api_key="sk-test",
        base_url="https://p6e2e.drift-e2e.test:8443/v1",
        timeout=3,
    )
    wire0 = drift.wire_queries
    with pytest.raises(Exception) as exc_info:  # noqa: B017
        provider.chat([{"role": "user", "content": "hi"}])
    assert drift_tls_logger.hits == []
    # 恰好一次线上解析：守卫校验时查到 127.0.0.1 即拒绝，建连从未发生
    assert drift.wire_queries == wire0 + 1
    assert "127.0.0.1" not in str(exc_info.value)


def test_local_inference_loopback_whitelist_unchanged(drift, openai_stub):
    """保证②：本地推理回环白名单路径行为不变（http+Key+白名单端口可用）。"""
    provider = P.OpenAIProvider(
        "openai", "m",
        api_key="sk-test",
        base_url=f"http://127.0.0.1:{openai_stub.server_address[1]}/v1",
        timeout=5,
    )
    answer = provider.chat([{"role": "user", "content": "hi"}])
    assert answer == "ok"
