"""Regression tests for the mirror-liveness gate's three-state judgement.

``scripts/verify_image_mirrors.py`` decides whether a pinned container image is
still pullable. That decision is a safety surface: a wrong ``CONFIRMED_GONE``
makes someone re-pin a working image, and a silently-green ``PROBE_INCONCLUSIVE``
hides exactly the failure the gate exists to find (a removed manifest makes a
service container fail to start, every ``needs:`` job SKIPs, and a whole
protection surface goes to zero with no red check).

So the tests are written against the RULES, not the happy path:

  R1  only a 404/410 that is *provably* a Docker-v2 registry answer is
      ``CONFIRMED_GONE``; an HTML/CDN/proxy 404 is not evidence.
  R2  a 429 / 5xx / timeout / transport error is ALWAYS ``PROBE_INCONCLUSIVE``
      -- a rate-limited anonymous pull must never read as "gone".
  R3  an unknown registry host is ``PROBE_INCONCLUSIVE`` and is never probed.

No network is touched: the HTTP layer is injected.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_image_mirrors.py"


def _load():
    spec = importlib.util.spec_from_file_location("_liuhao_image_mirrors", SCRIPT)
    assert spec and spec.loader, "cannot load %s" % SCRIPT
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load()


def _fake(routes):
    """A transport that maps a URL predicate -> HttpResponse, and records calls."""
    calls = []

    def transport(url, headers):
        calls.append((url, headers))
        for predicate, response in routes:
            if predicate(url, headers):
                return response
        return gate_error("no route")
    return transport, calls


def gate_error(msg):
    from scripts.verify_image_mirrors import HttpResponse  # noqa: F401  (unused guard)
    return HttpResponse(0, {}, "", msg)


# --------------------------------------------------------------------------- #
# parse_image_ref -- registry / repository / reference resolution
# --------------------------------------------------------------------------- #
class TestParseImageRef:
    def test_official_image_gets_library_prefix(self, gate):
        ref = gate.parse_image_ref("redis:7-alpine")
        assert (ref.registry, ref.host, ref.repository, ref.reference) == (
            "docker.io", "registry-1.docker.io", "library/redis", "7-alpine")

    def test_namespaced_dockerhub_image(self, gate):
        ref = gate.parse_image_ref("grafana/grafana:10.1.0")
        assert (ref.registry, ref.repository) == ("docker.io", "grafana/grafana")

    def test_explicit_host_is_recognised(self, gate):
        ref = gate.parse_image_ref("quay.io/coreos/etcd:v3.5.34")
        assert (ref.registry, ref.host, ref.repository, ref.reference) == (
            "quay.io", "quay.io", "coreos/etcd", "v3.5.34")

    def test_gcr_host(self, gate):
        ref = gate.parse_image_ref("gcr.io/cadvisor/cadvisor:v0.47.0")
        assert ref.registry == "gcr.io" and ref.repository == "cadvisor/cadvisor"

    def test_missing_tag_defaults_to_latest(self, gate):
        assert gate.parse_image_ref("redis").reference == "latest"

    def test_digest_reference_is_kept(self, gate):
        digest = "sha256:" + "a" * 64
        ref = gate.parse_image_ref("redis@%s" % digest)
        assert ref.reference == digest

    def test_unknown_host_is_not_given_a_probe_strategy(self, gate):
        """> R3: an unrecognised host must not be silently probed."""
        ref = gate.parse_image_ref("docker.redpanda.com/redpandadata/redpanda:v23.3.10")
        assert ref.registry == "docker.redpanda.com"
        assert ref.auth == "unknown"

    def test_empty_is_rejected(self, gate):
        assert gate.parse_image_ref("  ") is None


# --------------------------------------------------------------------------- #
# classify -- the safety surface
# --------------------------------------------------------------------------- #
class TestClassify:
    def test_200_is_alive(self, gate):
        assert gate.classify(200, {}, "").state == gate.ALIVE

    def test_404_from_a_v2_registry_is_confirmed_gone(self, gate):
        v = gate.classify(404, {"docker-distribution-api-version": "registry/2.0"},
                          "manifest unknown")
        assert v.state == gate.CONFIRMED_GONE

    def test_404_with_manifest_unknown_body_is_confirmed_gone(self, gate):
        v = gate.classify(404, {}, '{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
        assert v.state == gate.CONFIRMED_GONE

    def test_plain_404_is_inconclusive_not_gone(self, gate):
        """R1: an HTML / proxy 404 is not proof a manifest is gone."""
        v = gate.classify(404, {"content-type": "text/html"}, "<html>404</html>")
        assert v.state == gate.INCONCLUSIVE
        assert v.state != gate.CONFIRMED_GONE

    def test_429_is_inconclusive(self, gate):
        """R2: a rate limit must never read as 'gone'."""
        assert gate.classify(429, {}, "slow down").state == gate.INCONCLUSIVE

    def test_5xx_is_inconclusive(self, gate):
        for status in (500, 502, 503, 504):
            assert gate.classify(status, {}, "").state == gate.INCONCLUSIVE

    def test_transport_error_is_inconclusive(self, gate):
        v = gate.classify(0, {}, "", "URLError: [Errno 111] refused")
        assert v.state == gate.INCONCLUSIVE
        assert "transport error" in v.detail

    def test_auth_failure_is_inconclusive(self, gate):
        for status in (401, 403):
            assert gate.classify(status, {}, "").state == gate.INCONCLUSIVE

    def test_nothing_but_a_registry_answer_can_be_gone(self, gate):
        """Property: the only inputs that yield CONFIRMED_GONE are 404/410."""
        for status in (200, 201, 301, 302, 400, 401, 403, 405, 429, 500, 502):
            assert gate.classify(status, {}, "").state != gate.CONFIRMED_GONE


# --------------------------------------------------------------------------- #
# exit_code_for -- 0 / 1 / 2
# --------------------------------------------------------------------------- #
class TestExitCodes:
    def _v(self, gate, *states):
        ref = gate.parse_image_ref("redis:7-alpine")
        return [(ref, gate.Verdict(s, "")) for s in states]

    def test_all_alive_is_zero(self, gate):
        assert gate.exit_code_for(self._v(gate, gate.ALIVE)) == gate.EXIT_OK

    def test_gone_is_one(self, gate):
        assert gate.exit_code_for(self._v(gate, gate.CONFIRMED_GONE)) == gate.EXIT_FAIL

    def test_all_inconclusive_is_not_verified(self, gate):
        assert gate.exit_code_for(
            self._v(gate, gate.INCONCLUSIVE)) == gate.EXIT_NOT_VERIFIED

    def test_alive_plus_inconclusive_is_zero(self, gate):
        """A rate limit alongside a real answer is NOT a failure (R2)."""
        code = gate.exit_code_for(self._v(gate, gate.ALIVE, gate.INCONCLUSIVE))
        assert code == gate.EXIT_OK

    def test_gone_dominates(self, gate):
        code = gate.exit_code_for(
            self._v(gate, gate.ALIVE, gate.INCONCLUSIVE, gate.CONFIRMED_GONE))
        assert code == gate.EXIT_FAIL


# --------------------------------------------------------------------------- #
# Probe -- the injected transport, so the state machine is tested end to end
# --------------------------------------------------------------------------- #
class TestProbe:
    def test_dockerhub_success(self, gate):
        def transport(url, headers):
            if "auth.docker.io" in url:
                return gate.HttpResponse(200, {}, '{"token":"T"}')
            assert "Authorization" in headers
            return gate.HttpResponse(200, {"docker-distribution-api-version": "registry/2.0"}, "")
        probe = gate.Probe(transport=transport)
        assert probe.probe(gate.parse_image_ref("redis:7-alpine")).state == gate.ALIVE

    def test_success_is_alive_even_without_a_token(self, gate):
        """A missing anonymous token is not fatal -- the manifest call still runs."""
        def transport(url, headers):
            if "auth.docker.io" in url:
                return gate.HttpResponse(403, {}, "")
            return gate.HttpResponse(200, {}, "")
        probe = gate.Probe(transport=transport)
        assert probe.probe(gate.parse_image_ref("redis:7-alpine")).state == gate.ALIVE

    def test_rate_limited_is_inconclusive(self, gate):
        def transport(url, headers):
            if "auth.docker.io" in url:
                return gate.HttpResponse(200, {}, '{"token":"T"}')
            return gate.HttpResponse(429, {}, "too many requests")
        probe = gate.Probe(transport=transport)
        assert probe.probe(gate.parse_image_ref("redis:7-alpine")).state == gate.INCONCLUSIVE

    def test_bearer_challenge_is_resolved_then_retried(self, gate):
        def transport(url, headers):
            if "quay.io/v2/auth" in url:
                return gate.HttpResponse(200, {}, '{"token":"Q"}')
            if "Authorization" not in headers:
                return gate.HttpResponse(401, {
                    "www-authenticate": 'Bearer realm="https://quay.io/v2/auth",service="quay.io"',
                }, "")
            return gate.HttpResponse(200, {"docker-distribution-api-version": "registry/2.0"}, "")
        probe = gate.Probe(transport=transport)
        assert probe.probe(gate.parse_image_ref("quay.io/coreos/etcd:v3.5.34")).state == gate.ALIVE

    def test_unknown_registry_is_not_probed(self, gate):
        def transport(url, headers):
            raise AssertionError("an unknown registry must not be probed (R3)")
        probe = gate.Probe(transport=transport)
        v = probe.probe(gate.parse_image_ref("docker.redpanda.com/x/y:1"))
        assert v.state == gate.INCONCLUSIVE


# --------------------------------------------------------------------------- #
# extract_images / is_local_build -- discovery must not invent or miss refs
# --------------------------------------------------------------------------- #
class TestDiscovery:
    def test_image_lines_are_found_and_comments_skipped(self, gate):
        text = (
            "services:\n"
            "  a:\n"
            "    image: redis:7-alpine\n"
            "    # image: dead/image:1\n"
            "  b:\n"
            '    image: "quay.io/coreos/etcd:v3.5.34"\n'
        )
        assert gate.extract_images([text]) == ["redis:7-alpine", "quay.io/coreos/etcd:v3.5.34"]

    def test_dockerfile_from_lines_and_stage_aliases(self, gate):
        text = (
            "FROM python:3.12-slim AS base\n"
            "FROM base\n"
            "FROM quay.io/coreos/etcd:v3.5.10\n"
        )
        assert gate.extract_images([text]) == [
            "python:3.12-slim", "quay.io/coreos/etcd:v3.5.10"]

    def test_duplicates_are_collapsed_in_order(self, gate):
        text = "image: redis:7-alpine\nimage: redis:7-alpine\nimage: nginx:1.25-alpine\n"
        assert gate.extract_images([text]) == ["redis:7-alpine", "nginx:1.25-alpine"]

    def test_local_builds_are_recognised(self, gate):
        assert gate.is_local_build("liuhao-ai-os:latest")
        assert not gate.is_local_build("redis:7-alpine")

    def test_scratch_is_not_an_image(self, gate):
        assert gate.extract_images(["FROM scratch\n"]) == []


# --------------------------------------------------------------------------- #
# The gate must be able to fail
# --------------------------------------------------------------------------- #
class TestGateCanFail:
    def test_script_has_a_nonzero_exit_path(self, gate):
        src = SCRIPT.read_text(encoding="utf-8")
        assert "sys.exit(main())" in src
        assert gate.EXIT_FAIL == 1

    def test_render_marks_gone_and_not_verified_distinctly(self, gate):
        ref = gate.parse_image_ref("redis:7-alpine")
        gone = gate.render([(ref, gate.Verdict(gate.CONFIRMED_GONE, "HTTP 404"))], 1)
        assert "FAIL" in gone and "CONFIRMED_GONE" in gone
        blind = gate.render([(ref, gate.Verdict(gate.INCONCLUSIVE, "HTTP 429"))], 1)
        assert "NOT_VERIFIED" in blind
        assert "RESULT: PASS" not in blind
