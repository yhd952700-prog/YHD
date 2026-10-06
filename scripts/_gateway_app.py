"""Shared helper for scripts that need a live gateway app.

Why this exists
==============
Several one-off verify scripts instantiate the gateway with
``get_app()`` and then either (a) never tear it down, or (b) build a
*second* bare ``get_app().openapi()`` just to read the schema while the
first instance is still around. Two concrete hazards:

  * **Frozen-evidence corruption.** ``get_app()``'s lifespan initialises
    kernel instances and the audit subsystem. If ``AUDIT_DB_PATH`` is left
    at its default, that lifecycle can write to the repository-root
    ``audit_store.db`` / ``-wal`` / ``-shm`` -- the frozen HC-01 evidence
    the team has explicitly forbidden anyone to touch.
  * **Leaked app instances.** ``get_app()`` builds a FastAPI object that
    holds route tables, middleware and (after a real boot) live resources.
    Returning ``get_app().openapi()`` without a ``with TestClient(...)``
    block leaves it unreclaimed; repeated invocations leak.

The single pattern this module provides
========================================
* ``gateway_openapi_spec()`` -- read the OpenAPI schema through a
  ``TestClient`` context manager, so the lifespan runs and tears down, and
  audit persistence is redirected to a throwaway path first.
* ``gateway_client()`` -- a context manager yielding a ``TestClient`` for
  scripts that want to make real HTTP calls against the app.

Both redirect ``AUDIT_DB_PATH`` to a temp file **only if the caller has not
already set it**, so a script that manages its own isolation keeps control.

This module is importable with the standard library plus ``fastapi`` (a
runtime dependency of the project). It never imports ``src`` at module load
time, so importing it in another script's top level is cheap.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _ensure_audit_redirect() -> str:
    """Point audit persistence at a throwaway db, unless already isolated.

    Returns the path used. Callers that need the repo's frozen evidence
    untouched MUST keep AUDIT_DB_PATH pointing somewhere safe; this only
    acts when the variable is unset, so a script that already redirected
    keeps its own path.
    """
    existing = os.environ.get("AUDIT_DB_PATH")
    if existing:
        return existing
    tmp = Path(tempfile.mkdtemp(prefix="lh_sa_audit_")).joinpath("audit_store.db")
    os.environ["AUDIT_DB_PATH"] = str(tmp)
    return str(tmp)


@contextmanager
def gateway_client(raise_server_exceptions: bool = False) -> Iterator["object"]:
    """Yield a ``TestClient`` bound to a fresh gateway app.

    The client is used as a context manager, so FastAPI's lifespan starts and
    stops cleanly and no app object is left unreclaimed. ``get_app()`` is called
    *inside* the ``with TestClient(...)`` expression below so a static hygiene
    gate can see the app instance is torn down (no bare ``get_app()`` leak).
    """
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app

    _ensure_audit_redirect()
    with TestClient(get_app(), raise_server_exceptions=raise_server_exceptions) as client:
        yield client


def gateway_openapi_spec() -> dict:
    """Return the live OpenAPI spec without leaking an app instance.

    Uses :func:`gateway_client` so the schema is derived from a fully
    started-and-stopped app (lifespan included) rather than a bare
    ``get_app().openapi()`` that is never torn down.
    """
    with gateway_client() as client:
        return dict(client.app.openapi())
