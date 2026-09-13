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
3. **Missing build output must not change anything.** CI runs the test suite
   with no console bundle on disk; the gateway has to stay API-only there.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
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
def client():
    """A default gateway app.

    Deliberately does **not** require a console bundle. The "API is not
    shadowed" assertions below are exactly what must keep working in an
    API-only deployment, and CI never builds the console -- so hanging this
    fixture off the bundle would silently skip that coverage in CI, which is
    precisely how a shadowing regression would slip through. Console-specific
    tests ask for ``console_dist`` explicitly instead.
    """
    # `with` runs lifespan; the health endpoints are registered inside it.
    with TestClient(get_app()) as test_client:
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

    def test_only_navigation_methods_fall_back_to_the_shell(self, client, console_dist):
        # A POST under an unknown path is an API call that missed, not a page
        # to render: it must keep its JSON 404.
        shell = client.get("/no-such-client-route")
        assert shell.status_code == 200
        assert shell.headers["content-type"].startswith("text/html")

        api_call = client.post("/no-such-client-route")
        assert api_call.status_code == 404
        assert api_call.headers["content-type"].startswith("application/json")


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
        # if the handler were never installed at all. Skips in CI, where the
        # console is never built -- the same API-only environment the check
        # above is protecting.
        assert is_installed(get_app()) is True

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
