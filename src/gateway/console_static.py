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

So the fallback is installed as the **404 exception handler**. Starlette
consults it only when *no* route matched, so it cannot shadow anything, no
matter when that route was added. Static files are still resolved by
``StaticFiles`` (which already handles path traversal safely) rather than by
hand.

Contract
--------
* **No build output -> nothing is registered.** An API-only deployment (CI, a
  worker, a reviewer running the test suite) must behave exactly as before; a
  missing bundle is a log line, never a startup failure.
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
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
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


def is_installed(app: FastAPI) -> bool:
    """True when the console fallback is registered on ``app``."""
    handler = app.exception_handlers.get(StarletteHTTPException)
    return getattr(handler, "__name__", "") == FALLBACK_HANDLER_NAME


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


def mount_console(app: FastAPI, dist: Optional[Path] = None) -> Optional[str]:
    """Install the console fallback on ``app``. Returns the served path, or ``None``.

    Unlike a catch-all route this needs no ordering guarantee, so it is safe to
    call before other routers are added.
    """
    resolved = dist if dist is not None else resolve_console_dist()
    if resolved is None:
        logger.info(
            "no console build output found (checked %s) -- gateway serves the "
            "API only", os.environ.get(CONSOLE_DIST_ENV) or DEFAULT_CONSOLE_DIST,
        )
        return None

    static = StaticFiles(directory=str(resolved), html=True)

    @app.exception_handler(StarletteHTTPException)
    async def console_fallback(request: Request, exc: StarletteHTTPException):
        path = str(request.scope.get("path") or "")
        method = str(request.scope.get("method") or "").upper()
        # Only a browser navigation is a client-side route. A non-GET/HEAD
        # request under an unknown path is an API call that missed, and must
        # keep its JSON 404 -- handing it an HTML page would be the same class
        # of bug as the root mount.
        if exc.status_code != 404 or method not in ("GET", "HEAD") \
                or _is_reserved(path):
            # Not ours: keep the platform's JSON body and status exactly.
            return await http_exception_handler(request, exc)
        try:
            return await static.get_response(path.lstrip("/"), request.scope)
        except StarletteHTTPException as inner:
            if inner.status_code != 404:
                raise
            # Client-side route (e.g. /approvals): hand over the shell and let
            # the router resolve it.
            return await static.get_response("index.html", request.scope)

    logger.info("console fallback installed from %s (404 handler)", resolved)
    return str(resolved)
