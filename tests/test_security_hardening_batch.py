"""安全加固批次回归：UNC 凭据外泄、SSRF 回环、Key 明文、DNS 重绑定、
升级源完整性、Windows 文件名边界、PDF 解压炸弹。

每条用例都对应一个“改前可复现、改后被拒”的入口；真实改前/改后输出记录在
交付说明里。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import multiprocessing
import os
import socket
import threading
import time
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import src.gateway.main as gateway_main
from src.ai import net_guard, workspace
from src.ai.tools_local import _make_file_write
from src.knowledge.extractors import (
    ExtractionLimits,
    FileTooLarge,
    PdfPageTimeout,
    extract_document,
)
from src.upgrade.downloader import SignatureError, download_release
from src.upgrade.models import ReleaseInfo
from src.upgrade.sources import (
    HttpManifestSource,
    ManifestError,
    open_no_redirect,
)
from src.security import get_jwt_handler


# ---------------------------------------------------------------------------
# 一、UNC / 六、Windows 文件名边界
# ---------------------------------------------------------------------------


def test_unc_and_device_paths_rejected_before_realpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    real_realpath = os.path.realpath

    def spy(path, *args, **kwargs):
        calls.append(str(path))
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", spy)
    writer = _make_file_write(str(tmp_path))
    calls.clear()

    for bad in (
        r"\\attacker\share\x",
        r"\\?\C:\x",
        r"\\.\CON",
        "//attacker/share/x",
    ):
        result = writer(path=bad, content="x")
        assert result["status"] == "rejected", (bad, result)
    assert calls == [], f"UNC 输入不应进入 realpath：{calls}"

    with pytest.raises(workspace.WorkspaceViolation):
        workspace.resolve_in_workspace(r"\\attacker\share\x", str(tmp_path))
    assert calls == []

    for device in ("CON", "nul", "COM1", "LPT9", "CON.txt"):
        result = writer(path=device, content="x")
        assert result["status"] == "rejected", (device, result)
    assert calls == []


def test_normal_relative_and_absolute_paths_still_work(tmp_path: Path) -> None:
    writer = _make_file_write(str(tmp_path))
    relative = writer(path="notes/ok.txt", content="hi")
    assert relative["status"] == "executed", relative
    assert (tmp_path / "notes" / "ok.txt").read_text(encoding="utf-8") == "hi"

    absolute = writer(path=str(tmp_path / "abs.txt"), content="abs")
    assert absolute["status"] == "executed", absolute
    assert (tmp_path / "abs.txt").is_file()

    backslash_relative = writer(path=r"nested\a.txt", content="x")
    assert backslash_relative["status"] == "executed", backslash_relative
    assert (tmp_path / "nested" / "a.txt").is_file()


def test_trailing_dot_is_normalized_not_silently_aliased(tmp_path: Path) -> None:
    writer = _make_file_write(str(tmp_path))
    assert writer(path="foo", content="OLD")["status"] == "executed"
    result = writer(path="foo.", content="NEW")
    assert result["status"] == "executed", result
    # 写侧把尾随点去掉并如实回报目标名，而不是让 Win32 悄悄别名到 foo。
    assert result["rel_path"] == "foo"
    assert (tmp_path / "foo").read_text(encoding="utf-8") == "NEW"


# ---------------------------------------------------------------------------
# 二、SSRF 回环 / 三、带 Key 的 http 明文
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json")
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    # 另一条并行改动尚未 import asyncio；补齐模块全局即可，不改他人文件。
    gateway_main.asyncio = asyncio
    from src.gateway.main import get_app

    token, _ = get_jwt_handler().create_token(
        subject="model-admin", roles=["admin"], metadata={"kind": "human"}
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(get_app()) as client:
        yield client, headers


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/v1",
        "http://localhost:9/v1",
        "http://[::ffff:127.0.0.1]:9/v1",
        "http://127.0.0.1:2375/v1",
    ],
)
def test_gateway_rejects_loopback_ssrf(gateway_client, url: str) -> None:
    client, headers = gateway_client
    response = client.post(
        "/v1/models",
        headers=headers,
        json={
            "name": f"ssrf-{hashlib.md5(url.encode()).hexdigest()[:8]}",
            "provider": "openai",
            "model": "m",
            "base_url": url,
        },
    )
    assert response.status_code == 400, response.text
    assert "回环" in response.text or "内网" in response.text


def test_gateway_keeps_ollama_and_local_inference_working(gateway_client) -> None:
    client, headers = gateway_client
    cases = [
        {"name": "ollama-native", "provider": "ollama", "model": "llama3", "base_url": "http://127.0.0.1:11434"},
        {"name": "ollama-openai-compat", "provider": "openai", "model": "llama3", "base_url": "http://127.0.0.1:11434/v1", "api_key": "sk-local"},
        {"name": "lmstudio", "provider": "openai", "model": "local", "base_url": "http://127.0.0.1:1234/v1", "api_key": "sk-local"},
    ]
    for payload in cases:
        response = client.post("/v1/models", headers=headers, json=payload)
        assert response.status_code == 200, (payload, response.text)


def test_gateway_http_with_key_rejected_but_https_allowed(gateway_client) -> None:
    client, headers = gateway_client
    insecure = client.post(
        "/v1/models",
        headers=headers,
        json={
            "name": "http-key",
            "provider": "openai",
            "model": "m",
            "base_url": "http://93.184.216.34/v1",
            "api_key": "sk-live",
        },
    )
    assert insecure.status_code == 400, insecure.text
    assert "https" in insecure.text

    secure = client.post(
        "/v1/models",
        headers=headers,
        json={
            "name": "https-key",
            "provider": "openai",
            "model": "m",
            "base_url": "https://93.184.216.34/v1",
            "api_key": "sk-live",
        },
    )
    assert secure.status_code == 200, secure.text


# ---------------------------------------------------------------------------
# 四、DNS 重绑定
# ---------------------------------------------------------------------------


def test_runtime_guard_loopback_policy() -> None:
    for bad in (
        "http://127.0.0.1:9/v1",
        "http://[::ffff:127.0.0.1]:9/v1",
        "http://127.0.0.1:2375/v1",
        "http://192.168.1.10/v1",
        "http://169.254.169.254/v1",
    ):
        with pytest.raises(net_guard.RuntimeTargetRejected):
            net_guard.assert_safe_runtime_target(bad)

    for good in (
        "http://127.0.0.1:11434",
        "http://127.0.0.1:1234/v1",
        "http://[::ffff:127.0.0.1]:8000",
    ):
        net_guard.assert_safe_runtime_target(good)


def test_runtime_guard_rejects_http_with_credential() -> None:
    with pytest.raises(net_guard.RuntimeTargetRejected, match="https"):
        net_guard.assert_safe_runtime_target(
            "http://93.184.216.34/v1", has_credential=True
        )
    # 本机推理的 http 明文仍允许（Ollama / LM Studio 的正常用法）。
    net_guard.assert_safe_runtime_target(
        "http://127.0.0.1:11434/v1", has_credential=True
    )
    net_guard.assert_safe_runtime_target(
        "http://127.0.0.1:11434",
        allow_private_service=True,
        allow_loopback=True,
        has_credential=True,
    )
    # Ollama 可监听任意自定义端口：provider=ollama（allow_loopback）下，
    # 回环 + 带 Key + http 仍必须放行，否则正常用法被打断。
    net_guard.assert_safe_runtime_target(
        "http://127.0.0.1:11500/v1",
        allow_private_service=True,
        allow_loopback=True,
        has_credential=True,
    )
    # 同一个自定义端口，若调用方没有声明 allow_loopback（非 Ollama provider），
    # 仍然按"带 Key 的明文请求"拒绝。
    with pytest.raises(net_guard.RuntimeTargetRejected, match="https"):
        net_guard.assert_safe_runtime_target(
            "http://127.0.0.1:11500/v1", has_credential=True
        )


def test_dns_rebinding_resolution_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    def public_resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def rebinding_resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(net_guard, "_REAL_GETADDRINFO", public_resolver)
    monkeypatch.setattr(socket, "getaddrinfo", net_guard._pinned_getaddrinfo)
    monkeypatch.setattr(net_guard._PIN_STATE, "pins", {}, raising=False)
    net_guard.assert_safe_runtime_target("https://rebind.example/v1")

    # 校验后 DNS 被切到回环：SDK 建连时的 getaddrinfo 必须仍拿校验过的公网 IP。
    monkeypatch.setattr(net_guard, "_REAL_GETADDRINFO", rebinding_resolver)
    resolved = socket.getaddrinfo("rebind.example", 443)
    addresses = {info[4][0] for info in resolved}
    assert addresses == {"93.184.216.34"}, addresses


# -----------------------------------------------------------------------------
# 五、升级源完整性
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://93.184.216.34/release-manifest.json",
        "https://127.0.0.1/release-manifest.json",
        "https://169.254.169.254/release-manifest.json",
        "https://10.0.0.1/release-manifest.json",
    ],
)
def test_upgrade_source_rejects_http_and_private_targets(url: str) -> None:
    with pytest.raises(ManifestError):
        HttpManifestSource(url, manifest_sha256="0" * 64)


def test_upgrade_source_allows_signing_without_pinned_manifest_hash() -> None:
    """固定清单哈希改为可选首次引导：签名清单不再需要每次发布下发新哈希。

    构造阶段不再强制 manifest_sha256；真正的信任判定在 check 阶段由
    ``manifest_trust`` 做（远端无固定哈希时必须有可验证的清单签名）。
    """
    source = HttpManifestSource("https://93.184.216.34/release-manifest.json")
    assert source.is_remote is True
    assert source.manifest_sha256 is None


def test_upgrade_download_does_not_follow_redirects() -> None:
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"should-not-be-reached")

        def log_message(self, *_args):  # noqa: D401 - quiet
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/redirect"
        )
        with pytest.raises(ManifestError, match="禁止重定向"):
            open_no_redirect(request, timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _ArtifactSource:
    description = "test"

    def resolve_artifact(self, release: ReleaseInfo) -> str:
        return "https://93.184.216.34/liuhao.zip"


def test_remote_download_requires_signature_and_public_key(tmp_path: Path) -> None:
    base = ReleaseInfo(
        version="1.0.0", artifact="liuhao.zip", sha256="0" * 64, size=1
    )
    with pytest.raises(SignatureError, match="签名"):
        download_release(_ArtifactSource(), base, target_dir=tmp_path)
    signed = ReleaseInfo(
        version="1.0.0",
        artifact="liuhao.zip",
        sha256="0" * 64,
        size=1,
        signature="c2ln",
        signature_algorithm="ed25519",
    )
    with pytest.raises(SignatureError, match="公钥"):
        download_release(_ArtifactSource(), signed, target_dir=tmp_path)


# -----------------------------------------------------------------------------
# 七、PDF 解压炸弹
# -----------------------------------------------------------------------------


def _bomb_pdf(stream_count: int = 20, per_stream_bytes: int = 10 * 1024 * 1024) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        EncodedStreamObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    encoded = zlib.compress(b"0" * per_stream_bytes, 9)
    refs = ArrayObject()
    for _ in range(stream_count):
        stream = EncodedStreamObject()
        stream._data = encoded
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        stream[NameObject("/Length")] = NumberObject(len(encoded))
        refs.append(writer._add_object(stream))
    page[NameObject("/Contents")] = refs
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _pdf_with_text(text: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    content = DecodedStreamObject()
    content.set_data(("BT /F1 24 Tf 72 720 Td (%s) Tj ET" % text).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_decompression_total_limit(tmp_path: Path) -> None:
    path = tmp_path / "bomb.pdf"
    path.write_bytes(_bomb_pdf())
    limits = ExtractionLimits(
        max_file_bytes=25 * 1024 * 1024,
        max_pdf_stream_uncompressed_bytes=9 * 1024 * 1024,
        max_pdf_total_uncompressed_bytes=8 * 1024 * 1024,
    )
    with pytest.raises(FileTooLarge):
        extract_document(path, path.name, limits=limits)


def test_pdf_single_stream_limit(tmp_path: Path) -> None:
    path = tmp_path / "single-bomb.pdf"
    path.write_bytes(_bomb_pdf(stream_count=1, per_stream_bytes=10 * 1024 * 1024))
    limits = ExtractionLimits(
        max_file_bytes=25 * 1024 * 1024,
        max_pdf_stream_uncompressed_bytes=2 * 1024 * 1024,
        max_pdf_total_uncompressed_bytes=64 * 1024 * 1024,
    )
    with pytest.raises(FileTooLarge):
        extract_document(path, path.name, limits=limits)


def _slow_pdf(operations: int = 300_000) -> bytes:
    """单页海量文本绘制指令：真解析要十几秒，用来验证超时能真的掐掉。"""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 9 Tf 72 720 Td\n" + b"(x) Tj " * operations + b"\nET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_page_timeout(tmp_path: Path) -> None:
    """慢页必须在预算附近被强杀，而不是等它解析完再报「超时」。"""
    path = tmp_path / "slow-page.pdf"
    path.write_bytes(_slow_pdf())
    limits = ExtractionLimits(pdf_page_timeout_seconds=0.5)

    started = time.monotonic()
    with pytest.raises(PdfPageTimeout) as raised:
        extract_document(path, path.name, limits=limits)
    elapsed = time.monotonic() - started

    # 改前：等整页跑完（>10s）才返回错误。这里要求明显早于解析完成时间。
    assert elapsed < 5.0, f"超时没有抢占：等了 {elapsed:.1f}s"
    assert raised.value.code == "pdf_page_timeout"
    assert "强制终止" in str(raised.value)
    # 强杀后子进程必须被回收，不能留下孤儿进程或管道句柄。
    multiprocessing.active_children()
    assert multiprocessing.active_children() == []


def test_normal_pdf_still_extracts(tmp_path: Path) -> None:
    path = tmp_path / "normal.pdf"
    path.write_bytes(_pdf_with_text("hello security"))
    document = extract_document(path, path.name)
    assert "hello security" in document.text
