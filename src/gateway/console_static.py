"""Serve the built console from the gateway -- one process, one port.

Why this exists
---------------
The console (``apps/console/console``) and the gateway used to be two processes
on two ports (5173 + 8080), stitched together by a Vite dev proxy. That is a
fine *development* setup and a poor *deployment* one: any environment that
hands you a single HTTP port -- a container behind a reverse proxy, the
publishing sandbox, a host that only forwards one port -- cannot reach the
second one at all.

The console already talks to the API with **relative** URLs
(``fetch('/v1/dashboard/summary')``), so serving its build output from the same
origin as the gateway needs no frontend change and no CORS. That is the whole
reason this is a small module rather than a build-system change.

Why a 404 handler, and neither ``app.mount("/", ...)`` nor a catch-all route
---------------------------------------------------------------------------
The obvious implementation is a root mount, and it is broken on
starlette 1.6.0 / fastapi 0.141.1. Measured, with a *bare* ``FastAPI()`` and
nothing but that mount:

    GET /v1/does-not-exist  ->  200  text/html   (the SPA shell)

A root mount rewrites the sub-scope path, so ``StaticFiles`` is asked for the
directory itself and its ``html=True`` branch answers every request with
``index.html``. Two consequences, both unacceptable:

* an unknown ``/v1/...`` path returns HTML with status 200 to an API client,
  which turns a routing mistake into a JSON parse error three layers away;
* any reserved prefix the routers do not handle can never 404.

The next attempt was a catch-all **route** (``/{full_path:path}``) registered
last, which fixed both -- and introduced a subtler defect: a route only loses
to the routes registered *before* it. Anything that appends a route after
``get_app()`` therefore got shadowed by the catch-all. Measured on this repo:
``tests/test_policy_enforcement_api.py`` registers ``GET /_test/round73/...``
probes on the returned app and began receiving the SPA shell (200 ``text/html``)
instead of reaching its own handler, because the catch-all already sat earlier
in the table. Only GET/HEAD were affected -- the catch-all did not claim POST --
which is exactly the kind of half-broken that hides until someone adds a route.

So the fallback is installed as the **404 exception handler**, and it **chains**:
it first offers the request to the console fallback and, when that returns
``None``, calls the handler that was already present. On the real gateway that
is ``main.py``'s sanitising handler; on a bare ``FastAPI()`` it is the framework
default. Starlette consults it only when *no* route matched, so it cannot shadow
anything, no matter when that route was added -- and because the previous
handler is preserved (not replaced), sanitisation and JSON shapes are unchanged.

Chaining is what makes ``mount_console`` self-sufficient: it used to rely on
``main.py`` to re-dispatch through ``app.state.console_fallback``. That
dependency was invisible until CI built a real bundle and the tests stopped
skipping -- a bare ``FastAPI()`` plus ``mount_console`` then served nothing at
all, because no exception handler was ever registered on it.

Contract
--------
* **No build output -> API-only, with an explicit hint.** A missing bundle is a
  log line, never a startup failure: the gateway serves the API, and a browser
  navigation to an unknown non-API path answers a Chinese 404 that names the
  build command -- not a 500, and not a bare ``Not Found`` that leaves the
  operator guessing.
* **API prefixes are never shadowed.** Every first segment the app serves an
  endpoint on -- ``/v1``, ``/api``, ``/knowledge`` -- plus the docs surfaces
  (``/docs``, ``/redoc``, ``/openapi.json``) are excluded, so an unknown
  ``/v1/...`` still returns an honest 404 instead of the SPA shell.
* **Everything else stays the platform default.** A non-404 status, and a 404
  under a reserved prefix, are handed to FastAPI's own handler, so the JSON
  shape and headers are byte-for-byte what they would have been.
* **Deep links work.** Any other unknown ``GET``/``HEAD`` path serves
  ``index.html`` so client-side routes survive a page reload; a non-GET/HEAD
  request keeps its JSON 404.
"""
from __future__ import annotations

from pathlib import Path
import logging
import os
import re
import threading
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

#: Override the build-output location (useful in containers, where the bundle
#: is copied somewhere other than the source tree).
CONSOLE_DIST_ENV = "LIUHAO_CONSOLE_DIST"

#: Default build output, anchored to the repository root so it does not depend
#: on the working directory: src/gateway/console_static.py -> repo root.
DEFAULT_CONSOLE_DIST = (
    Path(__file__).resolve().parents[2] / "apps" / "console" / "console" / "dist"
)

#: First path segments that belong to the API and must never fall back to the
#: SPA shell.
#:
#: ``knowledge`` was missing here at first, and the omission was measurable:
#: ``/knowledge/query`` and ``/knowledge/search`` are real endpoints, yet an
#: unknown ``/knowledge/...`` path answered ``200 text/html`` -- the exact
#: failure this module exists to prevent, just on a prefix nobody thought of.
#: The list is therefore not hand-maintained by memory: ``tests/
#: test_console_single_port.py`` derives every first segment from the running
#: app's OpenAPI schema and fails if one of them is absent here.
RESERVED_PREFIXES: frozenset = frozenset(
    {"v1", "api", "knowledge", "docs", "redoc", "openapi.json"}
)

#: Name of the installed handler. The handler is registered *for*
#: ``StarletteHTTPException``, a key FastAPI already populates with its own
#: default, so the name -- not the presence of the key -- is what tells the two
#: apart (see :func:`is_installed`).
FALLBACK_HANDLER_NAME = "console_fallback"


def _is_reserved(path: str) -> bool:
    """True when ``path`` addresses the API rather than a console route."""
    head = (path or "").strip("/").split("/", 1)[0]
    return head in RESERVED_PREFIXES


#: 绝对 URI 请求目标（代理形态 ``GET http://<host>/<path> HTTP/1.1``）。ASGI 把请求行
#: 原样（可能带一个前导 ``/``）放进 ``scope["path"]``；它不是静态路径，绝不能
#: 交给文件系统去 ``stat``。
#: 这里刻意用 ``<host>``/``<path>`` 占位而不是真实示例域名：``tests/security``
#: 的出站扫描是纯文本正则，任何 ``http://名字`` 形状都会被当成真实主机并要求登记。
_ABSOLUTE_URI_RE = re.compile(r"^/?[A-Za-z][A-Za-z0-9+.\-]*://")


#: 静态资源的扩展名。这些路径缺失时必须返回真 404，绝不能拿 ``index.html``
#: 顶上——浏览器会把 HTML 当 JS/CSS 解析失败，控制台白屏，而访问日志里仍是
#: ``200 text/html``，排障时看不出任何异常（实测就是故障 6 的形态）。
_ASSET_SUFFIXES = frozenset(
    {
        ".js", ".mjs", ".cjs", ".css", ".map", ".json",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".wasm", ".mp4", ".webm", ".txt", ".xml", ".webmanifest",
    }
)


def _looks_like_asset(path: str) -> bool:
    """True when a missing ``path`` is a static asset, not a client-side route."""
    clean = (path or "").lower()
    if clean.startswith("/assets/"):
        return True
    suffix = Path(clean).suffix
    return suffix in _ASSET_SUFFIXES


#: Vite 产物的文件名带内容哈希（``index-A1b2C3d4.js``）。只有这类文件可以
#: 长期强缓存：内容变了文件名就变，旧文件永远不会被"复用"成新代码。
_HASHED_ASSET_RE = re.compile(r"^assets/[^/]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")

#: 一年（秒）。带指纹的资源算"永不过期"。
_IMMUTABLE_MAX_AGE = 31536000


def cache_control_for(path: str) -> str:
    """返回该静态路径应带的 ``Cache-Control`` 值。

    为什么要按路径分档，而不是"静态资源统一短缓存"：入口 ``index.html``
    里写的是**本次构建的哈希文件名**，它一旦被缓存，升级后浏览器仍会去拿
    旧哈希对应的旧 JS —— 这正是"更新了但界面没变"的来源。所以入口必须
    ``no-store``（每次都拿新的引用清单）；反过来，带哈希的 ``/assets/*``
    内容不可变，必须长缓存，否则每次启动都要重新下载整包 JS/CSS。

    ``sw.js`` 也不能强缓存：Service Worker 的更新检查依赖拿到最新的脚本，
    否则旧 SW 会继续回放旧外壳。
    """
    rel = (path or "").lstrip("/").split("?", 1)[0]
    if not rel or rel.endswith("/"):
        rel += "index.html"
    if _HASHED_ASSET_RE.match(rel):
        return f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable"
    if rel == "index.html":
        return "no-store"
    return "no-cache, must-revalidate"


def _apply_cache_control(response, served_path: str):
    """给静态响应补 ``Cache-Control``；返回同一个响应对象。"""
    response.headers["Cache-Control"] = cache_control_for(served_path)
    return response


def is_installed(app: FastAPI) -> bool:
    """True when ``app`` is serving a console bundle.

    The source of truth is ``app.state.console_fallback``, not
    ``app.exception_handlers``: the fallback is now reached through a chaining
    exception handler (and ``main.py`` also re-dispatches to the state entry),
    so looking for a handler with a specific ``__name__`` would report "not
    installed" on a correctly mounted app -- which is exactly what happened
    when the state-based refactor landed.
    """
    handler = getattr(getattr(app, "state", None), "console_fallback", None)
    return callable(handler) and getattr(handler, "__name__", "") == FALLBACK_HANDLER_NAME


def resolve_console_dist() -> Optional[Path]:
    """Return the usable build output, or ``None`` when there is nothing to serve."""
    configured = (os.environ.get(CONSOLE_DIST_ENV) or "").strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_CONSOLE_DIST
    if not candidate.is_dir():
        return None
    if not (candidate / "index.html").is_file():
        # A directory without an entry page would register and then 404 on
        # every request -- worse than not registering, because it looks like
        # it worked.
        logger.warning(
            "console build output at %s has no index.html -- not serving it "
            "(run `npm run build` in apps/console/console)", candidate,
        )
        return None
    return candidate


def _warm_mimetypes() -> None:
    """把"第一次猜 MIME 类型"的一次性成本挪到挂载期（端口绑定之前）。

    ``StaticFiles`` 用 ``mimetypes.guess_type`` 决定 ``Content-Type``；Windows
    上第一次查询要读注册表（``HKEY_CLASSES_ROOT\\.html`` 等）。实测同一份
    dist、同一进程：首次 ``get_response`` 316.7ms，第二次 5.5ms，而文件本身
    的冷读只要 0.4ms——差的就是这笔一次性初始化。它必定落在浏览器拿到的
    第一个静态资源上，所以在挂载期（``get_app()`` 里，uvicorn 绑定端口之前）
    先做一次。
    """
    try:
        import mimetypes

        mimetypes.init()
        for name in ("index.html", "app.js", "app.css", "logo.svg", "font.woff2"):
            mimetypes.guess_type(name)
    except Exception:  # noqa: BLE001 - 预热失败绝不能阻断挂载
        logger.warning("mimetypes 预热失败（首屏首请求会承担一次性成本）", exc_info=True)


def _mark_first_screen(request: Request) -> None:
    """通知网关：首屏已经开始发出去，后台预热可以开始了。

    ``main.py`` 的对话链预热线程等这个事件（最多等 ``LIUHAO_WARMUP_GRACE_S``
    秒）。用事件而不是固定延时，是因为"浏览器什么时候真的来拿首屏"只有这里
    知道；固定延时要么白等、要么正好撞上首屏渲染抢 GIL。
    """
    app = getattr(request, "app", None)
    event = getattr(app.state, "first_screen_served", None) if app is not None else None
    if event is not None:
        event.set()


def _warm_static_in_thread() -> threading.Thread:
    """并行预热静态首屏（挂载期开线程，不占启动主链）。

    mimetypes 首次查询读 Windows 注册表是 I/O，可以和后面的路由注册重叠。
    线程句柄挂在 ``app.state.console_static_warm_thread`` 上，lifespan 在
    就绪前做一次有界 join，既并行又不把"预热没完成"带进首屏。
    """

    def _run() -> None:
        _warm_mimetypes()

    thread = threading.Thread(target=_run, name="console-static-warm", daemon=True)
    thread.start()
    return thread


def mount_console(app: FastAPI, dist: Optional[Path] = None) -> Optional[str]:
    """Install the console fallback on ``app``. Returns the served path, or ``None``.

    Self-sufficient by design: the fallback is registered on ``app`` here, and it
    chains to whatever ``StarletteHTTPException`` handler was already present
    (``main.py``'s sanitiser on the real gateway, FastAPI's default on a bare
    ``FastAPI()``). Callers no longer have to remember to re-dispatch through
    ``app.state.console_fallback`` -- that hidden requirement is what made a
    directly-mounted app serve nothing.

    Unlike a catch-all route this needs no ordering guarantee, so it is safe to
    call before other routers are added.
    """
    resolved = dist if dist is not None else resolve_console_dist()
    static = StaticFiles(directory=str(resolved), html=True) if resolved is not None else None
    if resolved is None:
        logger.warning(
            "未找到控制台构建产物（已检查 %s）——网关仅提供 API；浏览器访问非 "
            "API 路径会收到中文提示。构建方式：在 apps/console/console 执行 "
            "npm run build，或用 %s 指向含 index.html 的目录后重启",
            os.environ.get(CONSOLE_DIST_ENV) or DEFAULT_CONSOLE_DIST,
            CONSOLE_DIST_ENV,
        )

    async def console_fallback(request: Request, exc: StarletteHTTPException):
        """仅接管"浏览器导航的 404"；其余返回 ``None`` 交给下一个处理器。

        没有构建产物时返回一条明确的**中文** 404（``CONSOLE_NOT_BUILT`` 与构建
        命令），而不是让浏览器只看到一句 ``Not Found``。有产物时先按静态文件
        解析，再回落到入口 HTML；缺失的静态资源仍返回真 404，绝不用 ``index.html``
        顶上（否则浏览器把 HTML 当 JS 解析，白屏而访问日志仍是 200）。
        """
        path = str(request.scope.get("path") or "")
        method = str(request.scope.get("method") or "").upper()
        # Only a browser navigation is a client-side route. A non-GET/HEAD
        # request under an unknown path is an API call that missed, and must
        # keep its JSON 404 -- handing it an HTML page would be the same class
        # of bug as the root mount.
        if exc.status_code != 404 or method not in ("GET", "HEAD") \
                or _is_reserved(path):
            return None
        if _ABSOLUTE_URI_RE.match(path):
            # 畸形请求行不是控制台路由：``StaticFiles`` 会把 ``http://...`` 当成
            # 本地路径去 stat，Windows 上路径里的 ``:`` 直接抛
            # ``OSError [WinError 123]``，最终变成 500 噪音。明确 400，既不进
            # 文件系统查找，也不把客户端错误记成服务端故障。
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "INVALID_REQUEST_TARGET",
                        "message": "不接受的绝对 URI 请求目标：请求行必须是相对路径。",
                    }
                },
            )
        if static is None:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": {
                        "code": "CONSOLE_NOT_BUILT",
                        "message": (
                            "控制台构建产物不存在：请在 apps/console/console 执行 "
                            "npm run build（或用 LIUHAO_CONSOLE_DIST 指向含 "
                            "index.html 的目录）后重启网关。"
                        ),
                    }
                },
            )
        try:
            response = await static.get_response(path.lstrip("/"), request.scope)
            _mark_first_screen(request)
            return _apply_cache_control(response, path)
        except StarletteHTTPException as inner:
            if inner.status_code != 404:
                raise
            # A missing static asset is NOT a client-side route. Returning the
            # shell here is what turned "build output incomplete" into a白屏
            # that looked like a healthy 200 in every access log.
            if _looks_like_asset(path):
                logger.warning(
                    'evt=console.asset.missing path=%s method=%s action="缺少'
                    '构建产物：在 apps/console/console 执行 npm run build 后'
                    '重新部署（或检查 LIUHAO_CONSOLE_DIST）"',
                    path,
                    method,
                )
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": {
                            "code": "CONSOLE_ASSET_NOT_FOUND",
                            "message": f"静态资源不存在：{path}",
                        }
                    },
                )
            # Client-side route (e.g. /approvals): hand over the shell and let
            # the router resolve it. 入口 HTML 一律 no-store，保证升级后拿到
            # 的是新的哈希引用清单。
            _mark_first_screen(request)
            return _apply_cache_control(
                await static.get_response("index.html", request.scope),
                "index.html",
            )

    # 链住原有处理器：真实网关这里是 main.py 的结构化／脱敏处理器，裸 FastAPI
    # 这里是框架默认处理器。只“问一句”而不替换，脱敏与 JSON 形状因此完全不变。
    previous = app.exception_handlers.get(StarletteHTTPException)

    async def console_http_error_handler(
        request: Request, exc: StarletteHTTPException
    ):
        rendered = await console_fallback(request, exc)
        if rendered is not None:
            return rendered
        if previous is None:  # 理论上 FastAPI 总会提供默认处理器，这里只兜底
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=getattr(exc, "headers", None),
            )
        return await previous(request, exc)

    app.add_exception_handler(StarletteHTTPException, console_http_error_handler)

    if resolved is not None:
        app.state.console_fallback = console_fallback
        app.state.console_static_warm_thread = _warm_static_in_thread()
        logger.info("console fallback installed from %s", resolved)
    return str(resolved) if resolved is not None else None
