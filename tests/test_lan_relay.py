"""局域网中继的回归测试：配对一次性、开发接口收敛、健康探针裁剪、登录冷却。

不启动真实网关：用一个最小假后端验证「中继这一层」的边界。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELAY_PATH = ROOT / "scripts" / "ops" / "lan_relay.py"


def _load_relay():
    spec = importlib.util.spec_from_file_location("lan_relay_under_test", RELAY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_origin: list[str] = []
    seen_host: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _FakeBackend.seen_origin.append(self.headers.get("Origin", ""))
        _FakeBackend.seen_host.append(self.headers.get("Host", ""))
        if self.path == "/v1/health":
            self._json(
                200,
                {
                    "status": "ok",
                    "timestamp": 123,
                    "version": "9.9.9",
                    "ai_provider": {"type": "cloud", "mode": "live"},
                    "trace_id": "trace-abc",
                },
            )
            return
        if self.path == "/v1/ready":
            self._json(200, {"status": "ready", "api_key_manager": {"total_keys": 3}})
            return
        if self.path.startswith("/docs"):
            self._json(200, {"openapi": "leaked"})
            return
        self._json(200, {"ok": True})

    def do_POST(self) -> None:
        _FakeBackend.seen_origin.append(self.headers.get("Origin", ""))
        _FakeBackend.seen_host.append(self.headers.get("Host", ""))
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path == "/v1/auth/login":
            payload = json.loads(raw.decode("utf-8") or "{}")
            if payload.get("password") == "good":
                self._json(
                    200,
                    {"access_token": "token-ok", "expires_in": 3600, "expires_at": 999},
                )
            else:
                self._json(401, {"detail": "账号或密码不正确"})
            return
        if self.path == "/v1/auth/logout":
            self._json(200, {"revoked": True})
            return
        self._json(200, {"ok": True})


class LanRelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.relay_module = _load_relay()

        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), _FakeBackend)
        cls.backend.daemon_threads = True
        threading.Thread(target=cls.backend.serve_forever, daemon=True).start()

        cls.device_file = Path(cls._tmpfile())
        cls.state = cls.relay_module.RelayState(
            backend_host="127.0.0.1",
            backend_port=cls.backend.server_port,
            lan_host="127.0.0.1",
            lan_port=0,
            admin_port=0,
            devices=cls.relay_module.DeviceStore(cls.device_file),
            admin_token="test-token",
        )
        cls.lan = ThreadingHTTPServer(("127.0.0.1", 0), cls.relay_module.RelayHandler)
        cls.lan.daemon_threads = True
        cls.lan.relay = cls.state
        threading.Thread(target=cls.lan.serve_forever, daemon=True).start()

        cls.admin = ThreadingHTTPServer(("127.0.0.1", 0), cls.relay_module.AdminHandler)
        cls.admin.daemon_threads = True
        cls.admin.relay = cls.state
        threading.Thread(target=cls.admin.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        for server in (cls.lan, cls.admin, cls.backend):
            server.shutdown()
            server.server_close()
        cls.device_file.unlink(missing_ok=True)

    @staticmethod
    def _tmpfile() -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        handle.close()
        return handle.name

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        cookie: str = "",
        headers: dict | None = None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.lan.server_port, timeout=10)
        merged = dict(headers or {})
        if cookie:
            merged["Cookie"] = cookie
        if body is not None:
            merged["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        connection.close()
        return result

    def _pair(self) -> str:
        code = self.state.pair_snapshot()["pair_code"]
        status, headers, _ = self._request(
            "POST",
            "/__relay/pair",
            body=f"code={code}".encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        raw_cookie = headers.get("Set-Cookie", "")
        return raw_cookie.split(";", 1)[0]

    def test_unpaired_cannot_reach_api(self) -> None:
        status, _, raw = self._request("GET", "/v1/health")
        self.assertEqual(status, 401)
        self.assertIn("未配对", raw.decode("utf-8"))

    def test_dev_interfaces_are_404_before_pairing(self) -> None:
        # 未配对也不能靠 401/404 差异探测出开发接口：一律 404。
        for path in ("/docs", "/openapi.json", "/v1/ready"):
            status, _, _ = self._request("GET", path)
            self.assertEqual(status, 404, path)

    def test_pair_new_rotates_without_deadlock(self) -> None:
        # 回归：rotate_pair_code 持锁时又调 pair_snapshot，普通 Lock 会自锁死，
        # 表现为管理面 POST /pair/new 与随后 GET /status 一起超时。
        before = self.state.pair_snapshot()["pair_code"]
        connection = http.client.HTTPConnection("127.0.0.1", self.admin.server_port, timeout=5)
        connection.request(
            "POST", "/pair/new", headers={"Authorization": "Bearer test-token", "Content-Length": "0"}
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertNotEqual(payload["pair_code"], before)

        connection = http.client.HTTPConnection("127.0.0.1", self.admin.server_port, timeout=5)
        connection.request("GET", "/status", headers={"Authorization": "Bearer test-token"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()

    def test_pair_code_is_single_use(self) -> None:
        code = self.state.pair_snapshot()["pair_code"]
        first = self._request(
            "POST",
            "/__relay/pair",
            body=f"code={code}".encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(first[0], 303)
        second = self._request(
            "POST",
            "/__relay/pair",
            body=f"code={code}".encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(second[0], 200)
        self.assertIn("无效或已过期", second[2].decode("utf-8"))

    def test_dev_interfaces_are_404_and_health_is_trimmed(self) -> None:
        cookie = self._pair()
        status, _, _ = self._request("GET", "/docs", cookie=cookie)
        self.assertEqual(status, 404)
        status, _, _ = self._request("GET", "/v1/ready", cookie=cookie)
        self.assertEqual(status, 404)

        status, _, raw = self._request("GET", "/v1/health", cookie=cookie)
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(set(payload), {"status", "timestamp"})
        self.assertNotIn("version", raw.decode("utf-8"))
        self.assertNotIn("ai_provider", raw.decode("utf-8"))

    def test_origin_is_stripped_and_host_rewritten(self) -> None:
        cookie = self._pair()
        _FakeBackend.seen_origin.clear()
        _FakeBackend.seen_host.clear()
        self._request(
            "POST",
            "/v1/auth/login",
            body=json.dumps({"password": "good"}).encode("utf-8"),
            cookie=cookie,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.lan.server_port}",
            },
        )
        # 手机同源写请求：Origin 被剥掉（网关生产态的回环放行不一定可用），
        # Host 改写成回环，网关只看到本机来源。
        self.assertEqual(_FakeBackend.seen_origin[-1], "")
        self.assertEqual(_FakeBackend.seen_host[-1], f"127.0.0.1:{self.backend.server_port}")

    def test_foreign_origin_write_is_rejected(self) -> None:
        cookie = self._pair()
        status, _, raw = self._request(
            "POST",
            "/v1/auth/login",
            body=json.dumps({"password": "good"}).encode("utf-8"),
            cookie=cookie,
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn("跨站写请求", raw.decode("utf-8"))

    def test_login_failures_hit_cooldown(self) -> None:
        cookie = self._pair()
        body = json.dumps({"password": "bad"}).encode("utf-8")
        for _ in range(5):
            status, _, _ = self._request(
                "POST",
                "/v1/auth/login",
                body=body,
                cookie=cookie,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(status, 401)
        status, _, raw = self._request(
            "POST",
            "/v1/auth/login",
            body=body,
            cookie=cookie,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 429)
        self.assertIn("过于频繁", raw.decode("utf-8"))

    def test_device_list_and_revoke(self) -> None:
        cookie = self._pair()
        connection = http.client.HTTPConnection("127.0.0.1", self.admin.server_port, timeout=10)
        connection.request("GET", "/devices", headers={"Authorization": "Bearer test-token"})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        device_id = payload["devices"][0]["id"]

        connection = http.client.HTTPConnection("127.0.0.1", self.admin.server_port, timeout=10)
        connection.request(
            "POST",
            f"/devices/{device_id}/revoke",
            headers={"Authorization": "Bearer test-token", "Content-Length": "0"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()

        status, _, raw = self._request("GET", "/v1/health", cookie=cookie)
        self.assertEqual(status, 401)
        self.assertIn("未配对", raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
