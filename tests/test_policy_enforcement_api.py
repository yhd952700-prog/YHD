"""Policy C-5/C-6 — the enforcement decision, pinned at the HTTP boundary.

The C-2/C-3 gate raises ``PolicyDeferredError`` (needs a verified human's
approval) or ``PolicyDeniedError`` (fail-closed hard deny). Both subclass
``PermissionError`` and nothing handled them, so before this round they fell
through to the gateway's catch-all handler and surfaced as
**500 internal_server_error** -- the one status that tells an operator nothing.

These tests pin two things:

* the boundary contract -- deferred is **409** (not forbidden, awaiting
  approval), hard-denied is **403**, and never 500;
* the deployment decision itself -- exactly three surfaces may arm the switch
  (production, the deployed staging contract, the cloud-bundle launcher), and
  each must arm the whole gated surface **minus the audited exemptions** (C-6)
  at exactly ``CRITICAL``: arming nothing extra, silently dropping nothing, and
  no other file arming at all. The allow-list and the arming parsers are read
  from ``scripts/verify_c4_approval_channel.py`` rather than restated here --
  see the module header for why.

Reachability of an armed action cannot be asserted from here (it is a runtime
property, and the dangerous call sites live inside the defining module where
they are textually indistinguishable from inert ones). That half is measured by
``scripts/verify_armed_actions_are_inert.py``.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.kernels import _enforcement as enf
from src.kernels._crosscutting import (
    PolicyDeferredError,
    PolicyDeniedError,
    kernel_action,
)
from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier
from src.kernels._sovereignty import clear_grants
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# The arming truth has exactly ONE owner: scripts/verify_c4_approval_channel.py
# (the C4 approval-channel gate, wired into CI at .github/workflows/ci.yml:247).
#
# This file used to carry a SECOND, hand-rolled copy of the rule -- a single-point
# `assert offenders == [PROD_MANIFEST]` that grepped the variable NAME and never
# looked at the value. That copy silently went stale: commit a9122b05 (2026-10-02)
# ruled the staging contract a legitimate third arming surface and updated the C4
# gate, but `git show a9122b05 --stat` = 1 file changed, so this test was left
# behind and has been RED ever since. Two copies of one rule is the defect, not
# the strictness of either copy.
#
# So rather than re-declaring the allow-list here, we LOAD the gate's module and
# reuse its constants and its arming parsers. `_armed_specs_c4()` below is a thin
# discovery shell around the gate's own `_spec_from_line` / `_python_arming_for_file`:
#
#   * ALLOWED_ARMING is literally the gate's own three constants -- a fourth site
#     added to the gate shows up here with no edit to this file;
#   * "which spec does this site arm" is answered by the gate's parsers, not by a
#     second regex, so "arms CRITICAL" cannot drift from "arms *something*";
#   * `test_allowed_arming_matches_the_c4_gate_source` additionally re-reads the
#     gate's `allowed_arming = {...}` literal out of its source, so even the
#     hand-off between the gate's constants and its allow-list is pinned.
#
# The gate module is `if __name__ == "__main__"`-guarded (line 783) and its module
# body only computes paths, so exec_module runs no gate and touches no database --
# measured at ~40 ms. The existing tests/kernels/test_build_cloud_bundle_arming.py
# already loads this exact module the same way.
# ---------------------------------------------------------------------------
_C4_PATH = REPO_ROOT / "scripts" / "verify_c4_approval_channel.py"
_c4_spec = importlib.util.spec_from_file_location("verify_c4_approval_channel", _C4_PATH)
c4 = importlib.util.module_from_spec(_c4_spec)
_c4_spec.loader.exec_module(c4)

#: The three -- and only three -- surfaces allowed to arm the switch.
ALLOWED_ARMING = frozenset({c4.PROD_MANIFEST, c4.STAGING_MANIFEST, c4.BUNDLE_LAUNCHER})
PROD_MANIFEST = c4.PROD_MANIFEST

#: Directories the arming scan never descends into. Mirrors the gate's
#: `excluded_roots`, with build caches added. ``scripts/`` is deliberately NOT
#: here: the cloud-bundle launcher is a real arming site (gate comment at
#: :456-457 says so explicitly), and excluding it -- as this file used to -- makes
#: any value assertion on it vacuous.
_ARMING_SCAN_PRUNE = frozenset({
    ".venv", ".git", "node_modules", "tests", "__pycache__", ".pytest_cache",
})
#: Config-shaped suffixes, matching the gate's `config_suffixes` (gate :453).
_ARMING_CONFIG_SUFFIXES = frozenset({
    ".yml", ".yaml", ".env", ".toml", ".ini", ".cfg", ".sh", ".json",
})


def _armed_specs_c4() -> dict:
    """Map every arming site in the WORKING TREE to the spec it arms.

    Same discovery *intent* as the gate's ``_armed_specs()`` but deliberately a
    different file set, because the gate reads ``git ls-files`` (tracked only)
    while this walks the tree:

    * the gate's own docstring (:322-326) scopes itself to "the committed,
      shippable deployment artifacts" -- correct for a release gate;
    * a test wants the stronger property. Arming is a *deployment* fact, and an
      untracked ``.env`` or a stray compose file sitting in the tree is exactly
      the thing that would arm a developer's or a CI-runner's instance without
      ever appearing in a commit. ``.env`` is gitignored here, so a
      ``git ls-files`` scan is structurally blind to it.

    So this is a SUPERSET of the gate's view: it can only ever find more arming
    sites, never fewer. The parsers are the gate's, so "arming" and "resolves to
    spec X" mean exactly what they mean to the gate. ``test_arming_scan_sees_at
    least_everything_the_c4_gate_sees`` pins the superset property so the two can
    never silently diverge in the other direction.
    """
    armed: dict = {}
    for root, dirs, files in os.walk(REPO_ROOT, topdown=True):
        dirs[:] = [d for d in dirs if d not in _ARMING_SCAN_PRUNE]
        for name in files:
            suffix = pathlib.Path(name).suffix.lower()
            is_config = (
                suffix in _ARMING_CONFIG_SUFFIXES or name.startswith("Dockerfile")
            )
            if not is_config and suffix != ".py":
                continue
            path = pathlib.Path(root) / name
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if enf.ENV_VAR not in text:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if is_config:
                for line in text.splitlines():
                    if enf.ENV_VAR not in line:
                        continue
                    spec = c4._spec_from_line(line, enf.ENV_VAR)
                    if spec is not None:
                        armed[rel] = spec
                        break
            else:
                spec = c4._python_arming_for_file(rel, text, enf.ENV_VAR)
                if spec is not None:
                    armed[rel] = spec
    return armed


CRITICAL_ACTIONS = frozenset(
    a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.CRITICAL
)
HIGH_ACTIONS = frozenset(
    a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.HIGH
)


@pytest.fixture(autouse=True)
def clean_switch(monkeypatch):
    """Every test starts unarmed, with no leftover approval grants."""
    monkeypatch.delenv(enf.ENV_VAR, raising=False)
    enf.reload()
    clear_grants()
    yield
    enf.reload()
    clear_grants()


@pytest.fixture
def human_env(monkeypatch):
    """Isolated IdentityManager holding one ACTIVE human identity."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c5-http", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert human.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return mgr, human


@pytest.fixture
def app():
    """A real gateway app -- the exception handlers live inside get_app()."""
    return get_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False so an *unhandled* error shows up as a 500
    # response we can assert against, rather than escaping as an exception.
    return TestClient(app, raise_server_exceptions=False)


class TestEnforcementStatusIsHonestOverHttp:
    """409 = awaiting approval. 403 = denied. Never 500."""

    def test_deferred_action_is_409_not_500(self, client, app):
        @app.get("/_test/round73/deferred")
        async def _deferred():
            raise PolicyDeferredError("capability.retire", "default_deny")

        resp = client.get("/_test/round73/deferred")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error"] == "policy_approval_required"
        assert body["action"] == "capability.retire"
        assert body["verdict"] == "defer"
        assert body["rule"] == "default_deny"

    def test_hard_denied_action_is_403_not_500(self, client, app):
        @app.get("/_test/round73/denied")
        async def _denied():
            raise PolicyDeniedError("capability.retire", "error", "default_deny")

        resp = client.get("/_test/round73/denied")
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"] == "policy_denied"
        assert body["action"] == "capability.retire"
        assert body["verdict"] == "error"

    def test_409_tells_the_caller_how_to_get_approved(self, client, app):
        @app.get("/_test/round73/hint")
        async def _hint():
            raise PolicyDeferredError("security.set_abac_rule", "default_deny")

        detail = client.get("/_test/round73/hint").json()["detail"]
        assert "/v1/policy/approvals" in detail

    def test_deferred_is_a_permission_error_so_legacy_guards_still_catch_it(self):
        # The 409 mapping must not cost the property the C-2 design relied on:
        # existing ``except PermissionError`` guards keep working.
        assert issubclass(PolicyDeferredError, PolicyDeniedError)
        assert issubclass(PolicyDeniedError, PermissionError)


class TestArmedGateEndToEndOverHttp:
    """The switch really arms a *production-shaped* call site (enforce=False)."""

    def test_armed_critical_action_defers_over_http(self, human_env, monkeypatch, app, client):
        monkeypatch.setenv(enf.ENV_VAR, "CRITICAL")
        enf.reload()

        @kernel_action("capability.retire")  # NOTE: enforce NOT set
        def retire():
            return {"retired": True}

        @app.post("/_test/round73/retire")
        async def _retire():
            return retire()

        resp = client.post("/_test/round73/retire")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "policy_approval_required"

    def test_unarmed_critical_action_still_runs(self, human_env, app, client):
        # Switch off (the autouse fixture guarantees it): the gate stays inert,
        # so the C-1 record-only (L1) contract is preserved.
        @kernel_action("capability.retire")  # NOTE: enforce NOT set
        def retire():
            return {"retired": True}

        @app.post("/_test/round73/retire-open")
        async def _retire_open():
            return retire()

        resp = client.post("/_test/round73/retire-open")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"retired": True}

    def test_armed_switch_does_not_leak_to_unselected_critical_action(
        self, human_env, monkeypatch, app, client
    ):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()

        @kernel_action("security.set_abac_rule")  # CRITICAL but NOT selected
        def set_rule():
            return {"set": True}

        @app.post("/_test/round73/set-rule")
        async def _set_rule():
            return set_rule()

        assert client.post("/_test/round73/set-rule").status_code == 200


class TestProductionArmingDecision:
    """C-6: three surfaces may arm the switch, and all three arm the audited
    surface at exactly CRITICAL.

    D24 narrowed the deployment from "HIGH,CRITICAL" to "CRITICAL" only --
    CRITICAL actions are blocked by default, everything else is record-only, and
    the switch is NOT globally ON.

    The three arming surfaces, and why each is legitimate rather than a leak:

    * ``docker-compose.prod.yml`` -- production;
    * ``infra/staging/docker-compose.yml`` -- the G9 pre-prod *deployed* staging
      contract. Staging is not a local dev or CI run; mirroring production's
      CRITICAL there exercises the real gate before prod, which is the
      fail-closed direction (``parse_spec`` *adds* a tier's actions to the
      enforced set, and an unknown tier raises -- so arming is always more
      restrictive, never less);
    * ``scripts/build_cloud_bundle.py`` -- the launcher template that renders the
      variable into a shipped bundle's ``serve.py``.

    These same three are the gate's allow-list at
    ``scripts/verify_c4_approval_channel.py:722``. This class reads that list
    rather than restating it; see the module header.
    """

    def test_allowed_arming_matches_the_c4_gate_source(self):
        """Pin the hand-off: the gate's own ``allowed_arming`` literal is these
        three constants, and this file's allow-list is built from those same
        constants. Without this, someone could edit the gate's allow-list to add
        or drop a site and leave this file asserting a stale set."""
        tree = ast.parse(_C4_PATH.read_text(encoding="utf-8"))
        literals = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if not any(t.id == "allowed_arming" for t in targets):
                continue
            assert isinstance(node.value, ast.Set), ast.dump(node.value)
            names = {e.id for e in node.value.elts if isinstance(e, ast.Name)}
            literals = names
            break
        assert literals is not None, (
            "the C4 gate no longer defines an `allowed_arming = {...}` literal; "
            "reconcile it with ALLOWED_ARMING here"
        )
        assert literals == {"PROD_MANIFEST", "STAGING_MANIFEST", "BUNDLE_LAUNCHER"}
        assert ALLOWED_ARMING == {
            c4.PROD_MANIFEST, c4.STAGING_MANIFEST, c4.BUNDLE_LAUNCHER,
        }
        # The gate's constants must still name the files we think they do --
        # a rename that moved the manifest would otherwise silently re-point the
        # allow-list at a path that no longer arms anything.
        assert c4.PROD_MANIFEST == "docker-compose.prod.yml"
        assert c4.STAGING_MANIFEST == "infra/staging/docker-compose.yml"
        assert c4.BUNDLE_LAUNCHER == "scripts/build_cloud_bundle.py"
        for site in sorted(ALLOWED_ARMING):
            assert (REPO_ROOT / site).is_file(), f"allowed arming site missing: {site}"

    def test_production_manifest_arms_the_audited_surface(self):
        text = (REPO_ROOT / PROD_MANIFEST).read_text(encoding="utf-8")
        match = re.search(r"LIUHAO_KERNEL_POLICY_ENFORCE=[^\n]*:-([^}]*)\}", text)
        assert match, "the production manifest does not arm the switch"
        spec = match.group(1).strip()
        # D24: the deployment arms CRITICAL only.
        assert spec == "CRITICAL", spec

        expected = CRITICAL_ACTIONS - set(enf.EXEMPT_ACTIONS)
        armed = enf.parse_spec(spec)
        assert armed == expected, (
            "missing=%s extra=%s"
            % (sorted(expected - armed), sorted(armed - expected))
        )
        assert len(CRITICAL_ACTIONS) == 2
        assert len(HIGH_ACTIONS) == 15
        # D24: the armed surface is now the CRITICAL tier only (2 actions).
        assert len(expected) == 2

    def test_no_other_file_arms_the_switch(self):
        """Exactly the allow-list arms, and every one of them arms CRITICAL.

        Two distinct failure modes are asserted here, and the old version of
        this test could only see the first:

        1. **Membership** -- a site outside ``ALLOWED_ARMING`` arms the switch.
           A fourth site is a red. (The old assertion was the same idea, but
           pinned to a one-element list, so it also went red when a *legitimate*
           site was added -- which is exactly the stale-copy failure that
           produced commit a9122b05's regression.)
        2. **Value** -- an allow-listed site resolves to something other than
           CRITICAL. The old test only grepped the variable *name*
           (``re.search(r"LIUHAO_KERNEL_POLICY_ENFORCE\\s*=", text)``) and never
           read the right-hand side, so changing prod from CRITICAL to HIGH (a
           *tightening*) or to OFF (a *loosening*) produced byte-identical test
           output -- the assertion had no teeth on the value at all.

        Both are needed. Membership alone cannot tell "arms CRITICAL" from
        "arms OFF"; value alone cannot tell a legitimate surface from a
        smuggled fourth one.
        """
        armed = _armed_specs_c4()

        unexpected = sorted(k for k in armed if k not in ALLOWED_ARMING)
        assert not unexpected, (
            "these files arm the switch but are not in the allow-list %s: %s "
            "(armed specs: %s)"
            % (sorted(ALLOWED_ARMING), unexpected,
               {k: armed[k] for k in unexpected})
        )

        # Set-equality, not subset: a *missing* site must be red too, otherwise
        # deleting staging's arming line (or the whole staging contract) would
        # pass this test and quietly downgrade a deployed environment to
        # record-only.
        assert set(armed) == ALLOWED_ARMING, (
            "the arming surfaces drifted from the allow-list: "
            "missing=%s unexpected=%s"
            % (sorted(ALLOWED_ARMING - set(armed)), sorted(set(armed) - ALLOWED_ARMING))
        )

        wrong = {
            site: armed[site]
            for site in sorted(ALLOWED_ARMING)
            if armed[site] != "CRITICAL"
        }
        assert not wrong, (
            "these surfaces must arm exactly CRITICAL (D24 deployment truth); "
            "got %s -- note OFF/empty is a *loosening* (record-only) and any "
            "tier below CRITICAL silently drops actions from the armed set"
            % (wrong,)
        )

    def test_arming_scan_sees_at_least_everything_the_c4_gate_sees(self):
        """The working-tree scan is a SUPERSET of the gate's ``git ls-files``
        scan. If the gate ever learns about an arming site this scan cannot see,
        this test goes red rather than the two quietly covering different sets."""
        gate = c4._armed_specs()
        mine = _armed_specs_c4()
        missing = {k: v for k, v in gate.items() if mine.get(k) != v}
        assert not missing, (
            "the C4 gate sees arming sites this scan does not reproduce "
            "(gate view is a subset by design -- report the divergence): %s"
            % (missing,)
        )
        extra = sorted(set(mine) - set(gate))
        # Untracked-only surfaces are EXPECTED (that is the point of the wider
        # scan: `.env` is gitignored, so the gate structurally cannot see it).
        # Today the tree is clean, so the views coincide.
        assert not extra, (
            "untracked arming surfaces present: %s -- these arm a local or CI "
            "instance without ever appearing in a commit"
            % ([(k, mine[k]) for k in extra],)
        )

    def test_ci_never_arms_the_switch(self):
        workflows = REPO_ROOT / ".github" / "workflows"
        # Both suffixes: the gate is about *what arms the switch*, and a workflow
        # is a deployment surface. `.yml`-only scanning would let a new
        # `*.yaml` workflow arm enforcement with no test failure.
        seen = 0
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(workflows.glob(pattern)):
                seen += 1
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert enf.ENV_VAR not in text, path.name
        assert seen, "no CI workflows found -- the scan would be vacuous"

    def test_no_exempt_action_is_armed(self):
        # An exemption is a recorded call-site measurement, not a policy taste:
        # the action is reached by production during normal operation, so arming
        # it would convert a working flow into "awaiting approval". This test
        # fails the day someone arms one without first removing the call site.
        armed = enf.parse_spec("HIGH,CRITICAL")
        assert not (armed & set(enf.EXEMPT_ACTIONS))
        assert enf.EXEMPT_ACTIONS, "an empty exemption set would make this vacuous"

    def test_the_inertness_guard_is_wired_into_ci(self):
        # The static decision above is only half the invariant; the other half
        # (is an armed action actually reachable?) is measured, not asserted.
        # If that guard is ever dropped from CI, the decision loses its teeth.
        guard = REPO_ROOT / "scripts" / "verify_armed_actions_are_inert.py"
        assert guard.is_file(), "the inertness guard is missing"
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "verify_armed_actions_are_inert.py" in ci

    def test_arming_is_a_deployment_decision_not_a_code_edit(self):
        # The whole point of the single switch: all 43 production call sites
        # keep enforce=False, so arming never requires editing them.
        text = (REPO_ROOT / "src" / "kernels" / "_crosscutting.py").read_text(
            encoding="utf-8"
        )
        assert "is_enforced(action, effective_risk)" in text
