#!/usr/bin/env python3
"""本地 HTTPS 反向代理：把 8099 的明文 HTTP 控制台包一层 TLS。

目的：让 PWA 在手机端 / 局域网桌面端也能走「安全上下文」，从而弹出
「安装应用 / 添加到主屏幕」提示（PWA 可安装性要求 HTTPS 或 localhost）。

- 监听 0.0.0.0:<PORT>（默认 8443），TLS 终止。
- 反向代理到 http://127.0.0.1:<BACKEND_PORT>（默认 8099，即控制台）。
- 证书为自签（SAN 含局域网 IP / 127.0.0.1 / localhost）；
  手机端需先信任该证书（见交付说明），否则会报安全警告且安装提示不出现。

不会改动 8099 进程；本代理停了，8099 的原生 HTTP 仍可用。

证书路径解析顺序（跨平台，不再硬编码 Windows 路径）
-------------------------------------------------
1. ``LIUHAO_TLS_CERT`` / ``LIUHAO_TLS_KEY``：直接指定证书与私钥文件，优先级最高；
2. 否则落到 ``<证书目录>/liuhao-local.pem`` 与 ``<证书目录>/liuhao-local.key``，
   其中 ``<证书目录>`` 由 ``LIUHAO_TLS_CERT_DIR`` 指定；
3. 否则默认 ``<系统临时目录>/liuhao-ui/certs``（``tempfile.gettempdir()``）。
   此前默认值是硬编码的 ``D:/cache/temp/liuhao-ui/certs``，在 Ubuntu/CI 上必然
   指向不存在的目录。

未配置时的行为（fail-closed）
----------------------------
证书或私钥文件不存在时，``main()`` 打印缺失的具体路径并以退出码 **2** 结束，
**不会**静默降级为明文 HTTP，也不会退化为「起了但不可用」。要生成证书请显式
设置上述任一环境变量指向已签发的文件。
"""
from __future__ import annotations

import http.server
import os
import ssl
import socketserver
import sys
import tempfile
import urllib.error
import urllib.request

# Cross-platform default (see module docstring). Overridable via
# LIUHAO_TLS_CERT_DIR; individual files overridable via LIUHAO_TLS_CERT / _KEY.
DEFAULT_CERT_DIR = os.environ.get(
    "LIUHAO_TLS_CERT_DIR",
    os.path.join(tempfile.gettempdir(), "liuhao-ui", "certs"),
)
BACKEND_HOST = os.environ.get("LIUHAO_PROXY_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("LIUHAO_PROXY_BACKEND_PORT", "8099"))
LISTEN_PORT = int(os.environ.get("LIUHAO_TLS_PORT", "8443"))
CERT = os.environ.get("LIUHAO_TLS_CERT", os.path.join(DEFAULT_CERT_DIR, "liuhao-local.pem"))
KEY = os.environ.get("LIUHAO_TLS_KEY", os.path.join(DEFAULT_CERT_DIR, "liuhao-local.key"))

# 不应透传给后端的逐跳头
_HOP_BY_HOP = {"host", "connection", "content-length", "transfer-encoding", "upgrade", "proxy-authorization", "proxy-connection"}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        target = f"http://{BACKEND_HOST}:{BACKEND_PORT}{self.path}"
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(target, data=body, method=self.command)
        for name in self.headers.keys():
            lname = name.lower()
            if lname in _HOP_BY_HOP:
                continue
            req.add_header(name, self.headers[name])
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            status, resp_headers = resp.status, resp.headers
        except urllib.error.HTTPError as exc:  # 后端明确错误（4xx/5xx）
            status, resp_headers = exc.code, exc.headers
            resp = exc
        except Exception as exc:  # 连不上后端等
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(str(exc).encode())))
            self.end_headers()
            self.wfile.write(str(exc).encode())
            return

        self.send_response(status)
        for name in resp_headers.keys():
            lname = name.lower()
            if lname in _HOP_BY_HOP:
                continue
            self.send_header(name, resp_headers[name])
        if self.command == "HEAD" or status in (204, 304):
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # 流式转发（支持 SSE / 未知长度）：用 chunked 分块写回，避免整段缓冲
        # 导致手机端 HTTPS 下的流式对话在 EOF/超时前不出字。
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n" % len(chunk))
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
            try:
                self.wfile.flush()
            except Exception:
                break
        self.wfile.write(b"0\r\n\r\n")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _proxy
    do_OPTIONS = _proxy

    def log_message(self, *args) -> None:  # 静默
        pass


def main() -> int:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        print(f"[https_proxy] 证书缺失：{CERT} / {KEY}", file=sys.stderr)
        return 2
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", LISTEN_PORT), ProxyHandler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print(
        f"[https_proxy] HTTPS on 0.0.0.0:{LISTEN_PORT} -> "
        f"http://{BACKEND_HOST}:{BACKEND_PORT} (Ctrl+C 退出)",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[https_proxy] 退出", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
