"""Two claims the gateway makes about itself, pinned so they cannot rot apart.

Why these tests exist
---------------------
Both are the same defect shape this repo keeps producing: **a claim that a
capability is in force, with no connective tissue to the execution**.

1. **The trace-id breadcrumb.** ``apps/console/console/src/lib/errorMessage.ts``
   tells the operator to take the ``trace_id`` off a 500 and search the server
   log with it, and falls back to the ``X-Trace-ID`` **response header**
   "cross-origin too" (``:32-33``, ``:45``). Measured at ``139698d1`` the two
   halves did not meet:

     * the 500 body read ``getattr(request.state, "trace_id", "unknown")`` and
       nothing ever assigned ``request.state.trace_id`` -- so the primary
       breadcrumb was the literal string ``"unknown"``, a value that matches no
       log line anywhere;
     * ``expose_headers`` was absent from the CORS middleware, so a browser JS
       client on another origin could not read ``X-Trace-ID`` at all -- the
       header *was* sent (``response.headers["X-Trace-ID"] = trace_id``), but
       CORS hides non-simple response headers by default.

   Net effect for a cross-origin deployment: **both** routes to the correlation
   id were dead, while the response still looked actionable. These tests assert
   the behaviour, not the source text, so a refactor that keeps the promise
   still passes.

2. **The boot's own failure claim.** ``src/gateway/main.py`` calls
   ``estimate_verify_seconds``. It is a MODULE-LEVEL function
   (``checkpointer.py:73``); ``BackgroundVerifier`` has no such method (its own
   ``start()`` calls the module function). The instance call raised
   AttributeError *after* ``start_background_verifier()`` had already launched
   the thread and mounted it on ``app.state`` -- so the handler logged
   "AUDIT CHECKPOINTER FAILED TO START ... evidence_status stays 'unverified'"
   while the checkpointer was in fact running. A false failure claim is not the
   safe direction: it sends an operator to debug a healthy component and trains
   everyone to ignore the one line that must never be ignored.

   Reachability is the reason this is pinned now: ``AUDIT_DB_PATH`` IS armed in
   ``docker-compose.prod.yml:69`` and ``infra/staging/docker-compose.yml:59``,
   so the ``else:`` branch (and therefore the AttributeError) is on the real
   boot path for compose deployments. (It is not armed in the ``deploy/cloud``
   bundle, which is a different artifact.)
"""
# NOTE: no ``from __future__ import annotations`` in this module, on purpose.
# FastAPI resolves parameter annotations at runtime to decide what to inject; a
# postponed annotation turns ``request: Request`` into the string ``"Request"``,
# which FastAPI reads as a *query parameter* and answers 422. That failure mode
# is silent until the route is called.

import logging

import pytest


class TestTheTraceIdBreadcrumbIsActuallyFollowable:
    """A correlation id the operator cannot read is not a correlation id."""

    def test_request_state_carries_a_real_trace_id(self):
        """The 500 handler's primary source must not be the fallback default.

        Asserted through behaviour: a probe route echoes whatever the logging
        middleware put on ``request.state``. At ``139698d1`` this is ``None``,
        which is exactly why the 500 body said ``"unknown"``.
        """
        from fastapi import Request
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        app = get_app()

        @app.get("/__probe_trace_state")
        def _probe(request: Request):  # pragma: no cover - test-only route
            return {"trace_id": getattr(request.state, "trace_id", None)}

        with TestClient(app) as c:
            r = c.get("/__probe_trace_state")

        assert r.status_code == 200, r.text
        seen = r.json()["trace_id"]
        assert seen, (
            "request.state.trace_id is falsy, so every 500 body would report "
            "trace_id='unknown' -- a breadcrumb that matches no log line"
        )
        assert seen != "unknown", (
            "request.state.trace_id is the literal 'unknown', so it is "
            "indistinguishable from 'no trace id was available'"
        )

    def test_response_header_matches_the_state_so_the_two_agree(self):
        """The header and the body must name the SAME request.

        The frontend treats the header as a fallback for the body value; if they
        disagree, an operator chasing the header lands on a different request's
        log lines than the one that failed.
        """
        from fastapi import Request
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        app = get_app()

        @app.get("/__probe_trace_state")
        def _probe(request: Request):  # pragma: no cover - test-only route
            return {"trace_id": getattr(request.state, "trace_id", None)}

        with TestClient(app) as c:
            r = c.get("/__probe_trace_state")

        assert r.headers.get("X-Trace-ID"), (
            "the X-Trace-ID response header is not sent at all"
        )
        assert r.headers["X-Trace-ID"] == r.json()["trace_id"], (
            "the response header and request.state disagree -- the fallback "
            "path would send the operator to the wrong request"
        )

    def test_cors_exposes_the_trace_header_cross_origin(self):
        """CORS hides non-simple response headers unless they are exposed.

        Without this, ``response.headers.get('X-Trace-ID')`` returns ``null`` in
        a cross-origin browser -- the exact case the frontend docstring promises
        works.
        """
        from src.gateway.main import get_app

        app = get_app()
        cors = [m for m in app.user_middleware if "CORS" in getattr(m, "cls", "").__name__]
        assert cors, "no CORS middleware on the app -- cannot judge exposure"

        expose = cors[0].kwargs.get("expose_headers") or []
        assert "X-Trace-ID" in expose, (
            "X-Trace-ID is not in CORS expose_headers, so cross-origin JS "
            "cannot read it; the frontend docstring's 'cross-origin too' claim "
            "is then false. expose_headers=%r" % (expose,)
        )


@pytest.fixture
def isolated_worker(monkeypatch, tmp_path):
    """Give a test a fresh verifier pointed OUTSIDE the repo.

    ``start_background_verifier`` is process-global and idempotent, so a worker
    left over from another test would make the assertion vacuous. The DB path is
    a tmp dir on purpose: the default project-root store is the frozen HC-01
    evidence file and a background thread must never append to it.

    Module-level so both boot-log classes (honest success and genuine failure)
    exercise the same isolation instead of drifting apart. Teardown stops the
    thread so a leaked daemon cannot outlive the test that started it.
    """
    from src.reliability import checkpointer as cp_mod

    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(cp_mod, "_worker", None)
    yield cp_mod
    cp_mod.stop_background_verifier(timeout=5.0)
    monkeypatch.setattr(cp_mod, "_worker", None)


class TestBootDoesNotFalselyClaimTheCheckpointerFailed:
    """The boot must not report a failure it did not have."""

    def test_no_false_failed_to_start_claim(self, isolated_worker, caplog):
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        caplog.set_level(logging.INFO)

        with TestClient(get_app()) as c:
            c.get("/openapi.json")

        text = caplog.text
        assert "AUDIT CHECKPOINTER FAILED TO START" not in text, (
            "the boot claimed the checkpointer failed to start. If it really "
            "did fail that is a finding; if it started and only the log "
            "statement broke, the claim is false and must not ship. Log:\n"
            + text
        )

    def test_the_started_line_is_actually_emitted(self, isolated_worker, caplog):
        """The positive control.

        A test that only asserts "no error" passes when the block never runs at
        all (e.g. if AUDIT_DB_PATH stops being honoured). So assert the success
        line too -- together the two pin the branch to its honest outcome.
        """
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        caplog.set_level(logging.INFO)

        # Build the app ONCE and hand the same instance to TestClient: a second
        # get_app() call would be a fresh app whose lifespan never ran, so
        # app.state would look unmounted for a reason that has nothing to do
        # with the fix under test.
        app = get_app()
        with TestClient(app) as c:
            c.get("/openapi.json")

        assert "Audit checkpointer started" in caplog.text, (
            "AUDIT_DB_PATH was set but the boot never logged that the "
            "checkpointer started -- the else: branch either did not run or "
            "died before its success log. Log:\n" + caplog.text
        )

        assert getattr(app.state, "audit_checkpointer", None) is not None, (
            "the checkpointer is not mounted on app.state, so /v1/ready cannot "
            "report its outcome"
        )


class TestAGenuineStartFailureIsStillReported:
    """The counter-control the two tests above cannot provide.

    "No FAILED TO START line" is satisfied just as well by deleting the error
    branch entirely -- which would trade a false alarm for a silent one, the
    exact failure mode this repo keeps re-learning. The positive control above
    rules out "the block never ran at all"; this one rules out "the error path
    was quietly neutered". A signal that cannot flip is not a signal.
    """

    def test_start_raising_produces_the_failed_to_start_line(
        self, isolated_worker, caplog, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        def _boom(*_a, **_kw):
            raise RuntimeError("audit engine unavailable")

        # The boot path does a deferred ``from ... import
        # start_background_verifier``, so patching the module attribute is what
        # the import resolves against.
        monkeypatch.setattr(isolated_worker, "start_background_verifier", _boom)
        caplog.set_level(logging.INFO)

        app = get_app()
        with TestClient(app):  # a broken checkpointer must not crash boot
            pass

        assert "AUDIT CHECKPOINTER FAILED TO START" in caplog.text, (
            "start() raised but the boot log stayed quiet -- a silent failure "
            "is worse than the false alarm this pair replaced. Log:\n"
            + caplog.text
        )
        assert getattr(app.state, "audit_checkpointer", None) is None, (
            "no checkpointer started, so app.state must not claim one"
        )
