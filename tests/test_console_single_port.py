"""Single-port mode: the gateway serves the console on the same origin.

What these tests protect
------------------------
The console used to be a second process on a second port, reachable only
through a Vite dev proxy. Serving it from the gateway is what makes the whole
thing deployable behind one port -- and it is exactly the kind of change that
can quietly break every API route, because a catch-all sits in the same routing
table.

Two failure modes are pinned here, both measured rather than imagined:

1. **A root mount swallows the API.** ``app.mount("/", StaticFiles(html=True))``
   on starlette 1.6.0 answers *every* request with the SPA shell, so
   ``GET /v1/does-not-exist`` returns 200 ``text/html``. An API client then
   fails with a JSON parse error instead of a clean 404, and the reserved
   prefixes below can never be honoured. ``test_unknown_api_path_is_404_json``
   fails if that regresses.
2. **A reserved prefix can be forgotten.** ``/knowledge/query`` and
   ``/knowledge/search`` are real endpoints, but ``knowledge`` was initially
   absent from the reserved set, so an unknown ``/knowledge/...`` path answered
   200 ``text/html``. ``TestReservedPrefixesCoverTheWholeApi`` derives the
   prefixes from the live OpenAPI schema, so the list cannot rot silently.
3. **Missing build output must not change API behaviour -- and must explain
   itself.** A deployment without a bundle stays API-only, but a browser
   navigation gets an explicit Chinese 404 naming the build command, never a
   500 and never a bare ``Not Found``. CI builds the bundle before pytest, so
   this branch is exercised on every run.
4. **The PWA surface is served, and typed correctly.** The console installs on
   a desktop (independent window) and on a phone (home-screen icon) through the
   same manifest + service worker. A wrong ``Content-Type`` on ``sw.js``, or an
   icon the manifest promises but the bundle does not contain, makes the app
   silently non-installable -- with no error a user would ever see.
   ``TestPwaAssetsAreServable`` pins both, and assembles its bundle from the
   *committed* sources rather than from ``dist/`` so it cannot skip in CI.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway import console_static
from src.gateway.console_static import (
    CONSOLE_DIST_ENV,
    _is_reserved,
    is_installed,
    resolve_console_dist,
)
from src.gateway.main import get_app


@pytest.fixture
def console_dist() -> Path:
    resolved = resolve_console_dist()
    if resolved is None:
        pytest.skip("console build output absent (run `npm run build` in apps/console/console)")
    return resolved


@pytest.fixture
def client(auth_headers):
    """A default gateway app.

    Deliberately does **not** require a console bundle. The "API is not
    shadowed" assertions below are exactly what must keep working in an
    API-only deployment, so hanging this fixture off the bundle would skip
    that coverage on any source checkout without a build. Console-specific
    tests ask for ``console_dist`` explicitly instead. CI now builds the
    bundle before pytest, so those tests do run there as well.
    """
    # `with` runs lifespan; the health endpoints are registered inside it.
    # `auth_headers`: the API routers now sit behind the console auth gate, and
    # the point of these tests is that a real endpoint is *reached* (JSON, not
    # the console shell) -- so the request has to be a legitimate, signed one.
    with TestClient(get_app(), headers=auth_headers) as test_client:
        yield test_client


def _api_first_segments() -> set:
    """Every first path segment the running app actually exposes.

    Read from the OpenAPI schema rather than by walking ``app.routes``: on
    fastapi 0.141 ``include_router`` keeps its routes inside an
    ``_IncludedRouter`` wrapper that has no public ``.routes``, so ``app.routes``
    lists only the docs routes and silently under-reports (measured: 5 paths
    instead of 15). The schema is the app's own public statement of which
    endpoints exist.
    """
    with TestClient(get_app()) as client:
        return {
            path.strip("/").split("/", 1)[0]
            for path in client.get("/openapi.json").json().get("paths", {})
            if path.startswith("/")
        }


class TestReservedPrefixes:
    @pytest.mark.parametrize("path", [
        "v1", "v1/health", "/v1/does-not-exist",
        "api", "api/anything", "/api/anything",
        "knowledge", "/knowledge/search",
        "docs", "redoc", "openapi.json",
    ])
    def test_api_paths_are_reserved(self, path):
        assert _is_reserved(path) is True

    @pytest.mark.parametrize("path", ["", "/", "approvals", "console/v1", "v11"])
    def test_console_paths_are_not_reserved(self, path):
        # "v11" must not be mistaken for "v1": the check is on the whole
        # first segment, not a prefix of it.
        assert _is_reserved(path) is False


class TestReservedPrefixesCoverTheWholeApi:
    """The list must be derived from the app, not from memory.

    Measured defect this guards: ``knowledge`` was left out, so an unknown
    ``/knowledge/...`` path answered ``200 text/html`` -- an API client got the
    SPA shell where it expected a 404, on a prefix nobody thought to add.
    """

    def test_every_api_prefix_is_reserved(self):
        missing = sorted(
            prefix for prefix in _api_first_segments()
            if not console_static._is_reserved(prefix)
        )
        assert not missing, (
            f"API prefix(es) {missing} are absent from "
            f"{sorted(console_static.RESERVED_PREFIXES)}: an unknown path under "
            "them returns the SPA shell instead of 404 JSON"
        )

    def test_the_guard_is_not_vacuous(self):
        # Without this, an empty schema would make the test above pass for the
        # wrong reason.
        detected = _api_first_segments()
        assert {"v1", "knowledge"} <= detected, detected


class TestApiIsNotShadowed:
    def test_health_is_json(self, client):
        response = client.get("/v1/health")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["status"] == "ok"

    def test_openapi_schema_is_json(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "paths" in response.json()

    def test_swagger_ui_is_served(self, client):
        assert client.get("/docs").status_code == 200

    def test_a_real_api_endpoint_is_json(self, client):
        response = client.get("/v1/dashboard/summary")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_unknown_api_path_is_404_json(self, client):
        """The regression: this returned 200 text/html with a root mount."""
        for path in ("/v1/does-not-exist", "/api/does-not-exist",
                     "/knowledge/does-not-exist"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"].startswith("application/json"), path


class TestConsoleIsServed:
    def test_root_serves_the_shell(self, client, console_dist):
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<!doctype html>" in response.text.lower()

    def test_a_deep_link_falls_back_to_the_shell(self, client, console_dist):
        # Client-side routes must survive a reload; otherwise every shareable
        # console URL 404s.
        response = client.get("/approvals")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_built_assets_are_served(self, client, console_dist):
        html = (console_dist / "index.html").read_text(encoding="utf-8")
        match = re.search(r'(?:src|href)="(/assets/[^"]+)"', html)
        assert match, "index.html references no /assets/ bundle"
        response = client.get(match.group(1))
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_a_missing_asset_is_a_real_404_and_logs_a_warning(
        self, client, console_dist, caplog
    ):
        """故障 6 的回归：缺失 JS 曾被兜底成 200 text/html（白屏却日志全绿）。

        现在必须返回真 404（JSON，带 code），并留下一条 WARNING，说明缺的是
        哪个资源、下一步该做什么。
        """
        missing = "/assets/__definitely-missing__.js"
        with caplog.at_level("WARNING", logger="src.gateway.console_static"):
            response = client.get(missing)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"]["code"] == "CONSOLE_ASSET_NOT_FOUND"
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.name == "src.gateway.console_static"
        ]
        assert any("evt=console.asset.missing" in line for line in warnings), warnings
        assert any(missing in line for line in warnings), warnings

    def test_only_navigation_methods_fall_back_to_the_shell(self, client, console_dist):
        # A POST under an unknown path is an API call that missed, not a page
        # to render: it must keep its JSON 404.
        shell = client.get("/no-such-client-route")
        assert shell.status_code == 200
        assert shell.headers["content-type"].startswith("text/html")

        api_call = client.post("/no-such-client-route")
        assert api_call.status_code == 404
        assert api_call.headers["content-type"].startswith("application/json")


class TestStaticCacheControl:
    """静态资源缓存策略：带指纹的长缓存、入口 HTML 不缓存。

    "升级后界面没变"与"每次启动重新下载整包 JS"是同一对矛盾的两端：入口
    HTML 引用的哈希文件名必须每次重新拿，而哈希资源本身不可变。这组断言
    钉住这两端，避免以后有人为了"快"把 ``index.html`` 也套上 long cache。
    """

    @pytest.fixture
    def cache_client(self, tmp_path):
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            "<!doctype html><html><body>console</body></html>", encoding="utf-8"
        )
        (dist / "assets" / "index-AbCd1234.js").write_text(
            "console.log(1)", encoding="utf-8"
        )
        (dist / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        (dist / "sw.js").write_text(
            "self.addEventListener('install', () => {})", encoding="utf-8"
        )
        app = FastAPI()
        console_static.mount_console(app, dist)
        with TestClient(app) as client:
            yield client

    def test_hashed_assets_are_immutable_and_long_lived(self, cache_client):
        response = cache_client.get("/assets/index-AbCd1234.js")
        assert response.status_code == 200
        assert response.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )

    def test_the_entry_html_is_never_stored(self, cache_client):
        for path in ("/", "/approvals"):
            response = cache_client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store", path

    def test_the_service_worker_is_revalidated(self, cache_client):
        # 强缓存 sw.js 会让浏览器继续用旧 SW 回放旧外壳。
        response = cache_client.get("/sw.js")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, must-revalidate"

    def test_unhashed_assets_are_not_given_immutable(self, cache_client):
        response = cache_client.get("/assets/logo.svg")
        assert response.status_code == 200
        assert "immutable" not in response.headers["cache-control"]

    def test_cache_control_classification_is_not_vacuous(self):
        # 直接钉分类函数：正则/比较写错时上面的 HTTP 断言会一起"通过"。
        assert console_static.cache_control_for("") == "no-store"
        assert console_static.cache_control_for("/") == "no-store"
        assert console_static.cache_control_for("/index.html") == "no-store"
        assert console_static.cache_control_for("/sw.js") == (
            "no-cache, must-revalidate"
        )
        assert console_static.cache_control_for("/assets/logo.svg") == (
            "no-cache, must-revalidate"
        )
        assert console_static.cache_control_for("/assets/index-AbCd1234.js") == (
            "public, max-age=31536000, immutable"
        )
        assert console_static.cache_control_for("/favicon.svg") == (
            "no-cache, must-revalidate"
        )


class TestTheFallbackCannotShadowLaterRoutes:
    """The regression that forced the 404-handler design.

    A catch-all *route* beats anything registered after it. Measured: the policy
    suites register ``GET /_test/round73/...`` probes on the app returned by
    ``get_app()`` and started receiving the SPA shell (200 ``text/html``)
    instead of reaching their own handler -- only GET, because the catch-all
    did not claim POST. The 404 handler runs only when *no* route matched, so
    it cannot shadow anything regardless of when that route was added.
    """

    def test_a_get_route_added_after_get_app_still_wins(self, console_dist):
        app = get_app()

        @app.get("/_test/late-added-get")
        async def late_added_get():
            return {"reached": True}

        with TestClient(app) as client:
            response = client.get("/_test/late-added-get")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"reached": True}


class TestNoBuildOutputKeepsApiOnlyBehaviour:
    def test_no_fallback_is_installed_without_a_bundle(self, monkeypatch, tmp_path):
        monkeypatch.setenv(CONSOLE_DIST_ENV, str(tmp_path / "absent"))
        assert is_installed(get_app()) is False

    def test_the_fallback_is_installed_with_a_bundle(self, console_dist):
        # Guard the guard: the assertion above would pass for the wrong reason
        # if nothing were installed at all. CI builds the bundle before pytest,
        # so this runs there too; it only skips on a checkout with no build.
        assert is_installed(get_app()) is True

    def test_navigation_without_a_bundle_returns_a_chinese_hint(
        self, monkeypatch, tmp_path
    ):
        """没有构建产物时，浏览器导航必须拿到能照做的中文 404，而不是 500。"""
        monkeypatch.setenv(CONSOLE_DIST_ENV, str(tmp_path / "absent"))
        with TestClient(get_app()) as client:
            response = client.get("/goals")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        assert detail["code"] == "CONSOLE_NOT_BUILT"
        assert re.search(r"[\u4e00-\u9fff]", detail["message"])
        assert "npm run build" in detail["message"]

    def test_the_api_still_works_without_a_bundle(self, monkeypatch, tmp_path):
        monkeypatch.setenv(CONSOLE_DIST_ENV, str(tmp_path / "absent"))
        with TestClient(get_app()) as client:
            assert client.get("/v1/health").status_code == 200
            assert client.get("/").status_code == 404

    def test_a_directory_without_index_html_is_refused(self, monkeypatch, tmp_path):
        # Mounting a directory that has no entry page would 404 on every
        # request while looking like it was configured correctly.
        empty = tmp_path / "dist"
        empty.mkdir()
        (empty / "stray.txt").write_text("no entry page", encoding="utf-8")
        monkeypatch.setenv(CONSOLE_DIST_ENV, str(empty))
        assert resolve_console_dist() is None

    def test_a_nonexistent_directory_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setenv(CONSOLE_DIST_ENV, str(tmp_path / "nowhere"))
        assert resolve_console_dist() is None


class TestBuildOutputIsAValidBundle:
    def test_index_html_is_a_real_document(self, console_dist):
        html = (console_dist / "index.html").read_text(encoding="utf-8")
        assert "<!doctype html>" in html.lower()
        assert re.search(r'(?:src|href)="(/assets/[^"]+)"', html)

    def test_the_bundle_has_no_absolute_backend_url(self, console_dist):
        # The console must call the API relatively, or it breaks as soon as it
        # is served from any host other than localhost:8080.
        index = (console_dist / "index.html").read_text(encoding="utf-8")
        assert "127.0.0.1:8080" not in index
        assert "localhost:8080" not in index


class TestMalformedAbsoluteUriRequestTarget:
    """绝对 URI 请求行不能变成 500。

    实测：``GET http://127.0.0.1:8081/v1/health HTTP/1.1`` 命中控制台的 404
    回落，``StaticFiles`` 把 ``http://...`` 当成本地路径去 stat，Windows 上
    路径里的 ``:`` 抛 ``OSError [WinError 123]``，网关返回 500。请求行畸形是
    客户端错误，必须是 400，而不是让服务端故障日志收噪音。
    """

    def test_absolute_uri_request_target_is_400_not_500(self, pwa_client):
        # 前导 "/" 与 ASGI 里真实的 ``scope["path"]`` 形状一致（见模块内的
        # ``_ABSOLUTE_URI_RE``）；httpx 原样保留路径中的 ``http://``。
        response = pwa_client.get("/http://evil.example.com/v1/health")
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "INVALID_REQUEST_TARGET"


# ---------------------------------------------------------------------------
# PWA 表面（桌面端独立窗口 / 手机端主屏图标）
# ---------------------------------------------------------------------------

#: 驾驶舱工程根。``public/``（图标、manifest、SW）与 Vite 入口 ``index.html``
#: 都在版本库里，而 ``dist/`` 不在 —— 这正是下面的夹具从前者组装的原因。
CONSOLE_APP = Path(__file__).resolve().parents[1] / "apps" / "console" / "console"


@pytest.fixture
def pwa_bundle(tmp_path, monkeypatch) -> Path:
    """由**已提交的源文件**拼出一个最小发布包。

    刻意不用 `resolve_console_dist()`：CI 现在会跑 `npm run build`，但把 PWA
    断言挂在一个本地可能不存在的 `dist/` 上，会让源码检出的环境整组 skip ——
    而那恰恰是最需要它们的地方。`public/` 与入口 `index.html` 都在版本库中，
    用它们拼出的文件集与构建产物在 PWA 这个面上的文件集完全一致（构建只是
    额外产出 `assets/`）。
    """
    bundle = tmp_path / "dist"
    bundle.mkdir()
    shutil.copytree(CONSOLE_APP / "public", bundle, dirs_exist_ok=True)
    shutil.copy2(CONSOLE_APP / "index.html", bundle / "index.html")
    # Service Worker 构建产物不在 `public/` 里：vite 插件读取 `sw.template.js`
    # 并生成 `dist/sw.js`（见 vite.config.ts）。这里沿用同一条规则，否则
    # `/sw.js` 的 MIME 断言会因为文件根本不存在而失败。
    (bundle / "sw.js").write_text(
        (CONSOLE_APP / "sw.template.js")
        .read_text(encoding="utf-8")
        .replace("__BUILD_VERSION__", "test")
        .replace("__PRECACHE_ASSETS__", "[]"),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONSOLE_DIST_ENV, str(bundle))
    return bundle


@pytest.fixture
def pwa_client(pwa_bundle):
    # 依赖顺序即语义：`pwa_bundle` 先设好环境变量，`get_app()` 才读得到。
    with TestClient(get_app()) as test_client:
        yield test_client


def _manifest(bundle: Path) -> dict:
    return json.loads((bundle / "manifest.webmanifest").read_text(encoding="utf-8"))


class TestPwaAssetsAreServable:
    def test_manifest_is_served_with_the_manifest_mime(self, pwa_client, pwa_bundle):
        # 类型错了浏览器会直接忽略 manifest —— 应用仍然能开，但永远装不上。
        response = pwa_client.get("/manifest.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/manifest+json")

    def test_service_worker_is_served_with_a_javascript_mime(self, pwa_client, pwa_bundle):
        # 浏览器对 SW 的 MIME 是硬要求：``text/plain`` 会被拒绝注册，且只留一条
        # 控制台报错 —— 一个不会有人看到的中断。
        response = pwa_client.get("/sw.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_every_icon_the_manifest_promises_is_served_and_the_right_size(
        self, pwa_client, pwa_bundle
    ):
        icons = _manifest(pwa_bundle).get("icons") or []
        assert icons, "manifest 没有声明任何图标"
        for icon in icons:
            response = pwa_client.get(icon["src"])
            assert response.status_code == 200, icon["src"]
            assert response.headers["content-type"] == "image/png", icon["src"]
            assert response.content[:8] == b"\x89PNG\r\n\x1a\n", icon["src"]
            # 声明的尺寸必须与文件真实尺寸一致：对不上时浏览器会丢弃该图标，
            # 安装提示随之消失，而 manifest 本身看不出问题。
            declared = icon["sizes"].lower().split("x")
            width, height = struct.unpack(">II", response.content[16:24])
            assert [str(width), str(height)] == declared, (icon["src"], declared)

    def test_the_apple_touch_icon_exists(self, pwa_client, pwa_bundle):
        # iOS 不读 manifest 里的 icons，只认这个固定文件名。
        response = pwa_client.get("/apple-touch-icon.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_the_favicon_is_the_brand_mark_not_a_starter_template(
        self, pwa_client, pwa_bundle
    ):
        # 起始模板留下的紫色闪电标曾长期占着这个位置，与图标是两个牌子。
        response = pwa_client.get("/favicon.svg")
        assert response.status_code == 200
        assert "image/svg+xml" in response.headers["content-type"]
        assert "#863bff" not in response.text.lower(), "favicon 仍是模板遗留标"

    def test_a_maskable_icon_is_declared(self, pwa_bundle):
        # 没有 maskable 图标时，Android 会把方形图标裁成圆形，四个角被切掉。
        purposes = {
            purpose
            for icon in _manifest(pwa_bundle)["icons"]
            for purpose in str(icon.get("purpose") or "").split()
        }
        assert "maskable" in purposes, purposes

    def test_the_manifest_launches_standalone_from_the_root(self, pwa_bundle):
        manifest = _manifest(pwa_bundle)
        # standalone 才是"装成应用"；minimal-ui / browser 会连地址栏一起带进来。
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        assert manifest["scope"] == "/"

    def test_the_shell_declares_the_install_and_safe_area_metadata(self, pwa_bundle):
        html = (pwa_bundle / "index.html").read_text(encoding="utf-8")
        assert re.search(r'rel="manifest"', html), "入口未声明 manifest"
        assert re.search(r'name="theme-color"', html), "入口未声明 theme-color"
        assert re.search(r'rel="apple-touch-icon"', html), "入口未声明 apple-touch-icon"
        assert re.search(r"name=\"apple-mobile-web-app-capable\"", html)
        # 手机端底部导航依赖 env(safe-area-inset-*)，而它只在 viewport-fit=cover
        # 下才有非零值 —— 少了它，导航会被 iPhone 的手势条盖住。
        assert "viewport-fit=cover" in html, "缺少 viewport-fit=cover，安全区不生效"


class TestManifestCannotPointAtAPageThatDoesNotExist:
    """装成应用之后没有地址栏，shortcuts 是进入某一页的唯一方式。

    所以 `?p=` 的取值必须与导航的唯一真相（`src/lib/nav.ts`）对得上 —— 一个指向
    不存在页面的快捷方式，点开就是空白。
    """

    def _known_page_keys(self) -> set[str]:
        """页面 key 的登记处：导航表 + 各页面导出的 PAGE 常量。

        页面常量（如 ``ENTERPRISE_OVERVIEW_PAGE.key``）是计算属性，不能只扫
        nav.ts 的字符串字面量；否则会把合法的规范 key 误判成"不存在"。
        """
        sources = [CONSOLE_APP / "src" / "lib" / "nav.ts"]
        sources.extend(sorted((CONSOLE_APP / "src" / "pages").glob("*.tsx")))
        keys: set[str] = set()
        for source in sources:
            text = source.read_text(encoding="utf-8")
            keys.update(re.findall(r"key:\s*'([a-z0-9-]+)'", text))
        return keys

    def test_every_shortcut_page_exists(self, pwa_bundle):
        known = self._known_page_keys()
        assert known, "未能从 nav.ts / pages 解析出任何页面 key"

        urls = [str(shortcut.get("url", "")) for shortcut in _manifest(pwa_bundle).get("shortcuts") or []]
        assert urls, "manifest 没有声明任何 shortcuts"

        unknown = sorted(url for url in urls if not any(f"p={key}" in url for key in known))
        assert not unknown, f"shortcuts 指向了不存在的页面：{unknown}（已知：{sorted(known)}）"

    def test_the_parser_is_not_vacuous(self, pwa_bundle):
        # 没有这一条，上面的断言会在"一个 key 都没解析出来"时以另一种方式通过。
        known = self._known_page_keys()
        assert {"enterprise-overview", "settings"} <= known
        assert len(known) >= 20, f"页面 key 解析明显不完整：{sorted(known)}"


class TestClientModesAgreeAcrossTheWire:
    """``client`` 进令牌 metadata，是审计归因的依据 —— 两侧的词表必须一致。

    两侧各自写死了一份：前端在 TypeScript（`src/lib/auth.ts`），网关在 Python
    （`src/gateway/auth.py`）。若不比对，前端多发一个值不会有任何报错 —— 网关会
    安全地回落到 ``web``，代价是每一条手机端会话在审计里都被标成网页版。
    """

    def test_the_frontend_and_the_gateway_list_the_same_modes(self):
        from src.gateway import auth as gateway_auth

        source = (CONSOLE_APP / "src" / "lib" / "auth.ts").read_text(encoding="utf-8")
        match = re.search(r"CLIENT_MODES\s*:[^=]*=\s*\[([^\]]*)\]", source)
        assert match, "未能从 lib/auth.ts 解析出 CLIENT_MODES"

        frontend = tuple(re.findall(r"'([a-z]+)'", match.group(1)))
        assert frontend, "解析结果为空"
        assert frontend == tuple(gateway_auth.CLIENT_MODES), (
            frontend,
            gateway_auth.CLIENT_MODES,
        )

    def test_the_guard_is_not_vacuous(self):
        from src.gateway import auth as gateway_auth

        # 三个端就是这次的交付目标本身；数量变了应当先怀疑词表而不是先改断言。
        assert tuple(gateway_auth.CLIENT_MODES) == ("desktop", "web", "mobile")


def test_api_docs_are_closed_by_default_and_open_only_with_dev_switch(monkeypatch):
    """/docs 与 /openapi.json 默认按生产处理，只有显式开发开关才开放。"""
    monkeypatch.delenv("LIUHAO_HTTP_DOCS", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    with TestClient(
        get_app(), base_url="http://127.0.0.1:8080", client=("127.0.0.1", 50000)
    ) as default_app:
        assert default_app.get("/docs").status_code == 404
        assert default_app.get("/openapi.json").status_code == 404

    monkeypatch.setenv("LIUHAO_HTTP_DOCS", "1")
    with TestClient(
        get_app(), base_url="http://127.0.0.1:8080", client=("127.0.0.1", 50000)
    ) as dev_app:
        assert dev_app.get("/docs").status_code == 200
        assert dev_app.get("/openapi.json").status_code == 200

    # 显式生产仍强制关闭，即使开发开关还留在环境里。
    monkeypatch.setenv("APP_ENV", "production")
    with TestClient(
        get_app(), base_url="http://127.0.0.1:8080", client=("127.0.0.1", 50000)
    ) as prod_app:
        assert prod_app.get("/docs").status_code == 404
        assert prod_app.get("/openapi.json").status_code == 404
