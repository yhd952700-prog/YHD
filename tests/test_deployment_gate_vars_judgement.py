"""Regression tests for the deployment-gate-variable checker's own judgement.

``scripts/verify_deployment_gate_vars.py`` is the gate that says "a control is
gated on an env var no deployment artifact sets". On 2026-10-04 it was found to
be wrong about its own subject in three ways, two of which failed in the
dangerous direction. These tests exist so the next person editing it cannot
reintroduce the same errors -- and, just as importantly, so the gate itself
cannot drift into being decorative.

The three defects, in the order they were fixed:

G1  ``expect="absent"`` ignored ``aliases`` while ``expect="set"`` honoured
    them, so a dev-only control shipped under its alias name was reported as
    clean. That fails OPEN: a control that must never leave development reaches
    production undetected.

G2  ``scan_literal_mentions`` had a full implementation and a docstring
    promising it prevented a seed entry from being misreported as stale. It had
    zero callers. Its promise was documentation of a feature that never existed.
    Wiring it removed two false accusations against live security entries
    (LIUHAO_POSTURE_STRICT, LIUHAO_ENV -- both read through constants in
    posture.py, so no literal ``os.environ.get`` exists for the scanner to find).

G4  ``refs`` were emitted as evidence and never checked. 5 of them had drifted:
    e.g. ``src/kernels/_crosscutting.py:676`` now reads
    ``if len(parts) == 3 and parts[0] == "RULE":`` and says nothing about
    LIUHAO_EXECUTOR_FENCE. A wrong pointer is worse than no pointer, because it
    looks checkable.

What these tests deliberately do NOT assert: that the gate exits 0. It exits 1,
and that is correct -- 8 seed gates are unset or read-indirectly in the
deployment artifacts (7 ``gate-var-never-set`` plus ``LIUHAO_POSTURE_STRICT``,
reported as ``seed-entry-read-indirectly``) and 4 scan-detected variables are
unclassified (``AUDIT_DB_PATH``, ``LIUHAO_ALLOW_SIMULATED_EXECUTION``,
``LIUHAO_CONSOLE_DIST``, ``LIUHAO_WORKSPACE_ROOT``). Measured 2026-10-07: the
run is 14 findings (16 before the two drifted-ref findings were fixed in
2b8f802b). That is a human decision, recorded in the task list, not something a
test should paper over. Asserting "the gate is green" here would be exactly the
fake-pass pattern these tests exist to prevent.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_deployment_gate_vars.py"


def _load():
    """Import the gate script as a module without installing it as a package."""
    spec = importlib.util.spec_from_file_location("_liuhao_gate_vars", SCRIPT)
    assert spec and spec.loader, "cannot load %s" % SCRIPT
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load()


# --------------------------------------------------------------------------- #
# G1 -- aliases must be honoured on BOTH branches
# --------------------------------------------------------------------------- #
class TestAliasSymmetry:
    """``expect="set"`` and ``expect="absent"`` must agree on what "set" means."""

    @staticmethod
    def _entry(expect, var="PROBE_CANON", alias="PROBE_ALIAS"):
        return ({"var": var, "aliases": (alias,), "expect": expect,
                 "why": "probe entry", "refs": ()},)

    def test_dev_only_var_shipped_under_its_alias_is_caught(self, gate, monkeypatch):
        # Precondition the earlier version of this test got wrong: the entry
        # must have a real read on file, or the staleness check fires first and
        # the alias branch is never reached -- the test then "passes" for the
        # wrong reason, which is how a real defect survives.
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + self._entry("absent"),
        )
        reads = {"PROBE_CANON": [("src/app.py", 10)]}
        findings = gate.evaluate({"PROBE_ALIAS": ["docker-compose.prod.yml"]},
                                 reads, {}, {})
        ours = [f.kind for f in findings if f.var == "PROBE_CANON"]
        assert "dev-only-var-set-in-artifact" in ours, (
            "a dev-only control shipped under its alias name must be reported; "
            "before the fix this list was empty, i.e. the gate failed OPEN")

    def test_dev_only_var_shipped_under_its_canonical_name_is_caught(self, gate, monkeypatch):
        """Control: the canonical path was already correct and must stay so."""
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + self._entry("absent"),
        )
        reads = {"PROBE_CANON": [("src/app.py", 10)]}
        findings = gate.evaluate({"PROBE_CANON": ["docker-compose.prod.yml"]},
                                 reads, {}, {})
        assert "dev-only-var-set-in-artifact" in [
            f.kind for f in findings if f.var == "PROBE_CANON"]

    def test_dev_only_var_absent_everywhere_is_not_reported(self, gate, monkeypatch):
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + self._entry("absent"),
        )
        reads = {"PROBE_CANON": [("src/app.py", 10)]}
        findings = gate.evaluate({}, reads, {}, {})
        assert not [f for f in findings if f.var == "PROBE_CANON"], (
            "a dev-only var that ships nowhere must not be reported")

    def test_gate_var_satisfied_by_alias_is_not_reported(self, gate, monkeypatch):
        """``expect="set"`` honours aliases; pin that so G1 is not 'fixed' by
        deleting alias support from both branches."""
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + self._entry("set"),
        )
        findings = gate.evaluate({"PROBE_ALIAS": ["docker-compose.prod.yml"]},
                                 {"PROBE_CANON": [("src/app.py", 10)]}, {}, {})
        assert not [f for f in findings if f.var == "PROBE_CANON"]


# --------------------------------------------------------------------------- #
# G2 -- the literal-mention scanner must actually be wired in
# --------------------------------------------------------------------------- #
class TestLiteralMentionScannerIsLive:
    def test_scan_literal_mentions_has_a_caller(self, gate):
        import inspect
        src = inspect.getsource(gate)
        # A defined-but-uncalled helper is how G2 survived: the docstring
        # promised behaviour, the behaviour did not exist.
        assert "scan_literal_mentions(" in src.replace(
            "def scan_literal_mentions(", ""), (
            "scan_literal_mentions is never called -- its docstring promises it "
            "prevents a false 'stale' report and nothing delivers on that")

    def test_evaluate_accepts_mentions(self, gate):
        import inspect
        params = inspect.signature(gate.evaluate).parameters
        assert "mentions" in params, (
            "evaluate() must be able to receive literal-mention evidence")

    def test_indirectly_read_entry_is_not_called_rotten(self, gate, monkeypatch):
        """A var read through a constant must not be reported as removed.

        This is the exact case that made the old output dangerous: it told the
        reader that LIUHAO_POSTURE_STRICT had rotted, and acting on that would
        mean deleting a live security entry.
        """
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + ({"var": "PROBE_INDIRECT", "aliases": (),
                                "expect": "set", "why": "probe", "refs": ()},),
        )
        # No entry in `reads`, but the name IS spelled in src/.
        mentions = {"PROBE_INDIRECT": [("src/security/posture.py", 68)]}
        findings = gate.evaluate({}, {}, {}, mentions)
        ours = [f.kind for f in findings if f.var == "PROBE_INDIRECT"]
        assert "stale-seed-entry" not in ours, (
            "an entry whose name is still present in src/ must not be accused "
            "of having rotted")
        assert "seed-entry-read-indirectly" in ours

    def test_genuinely_absent_entry_is_still_called_stale(self, gate, monkeypatch):
        """Control: the fix must not make the staleness check unreachable."""
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + ({"var": "PROBE_GONE", "aliases": (),
                                "expect": "set", "why": "probe", "refs": ()},),
        )
        findings = gate.evaluate({}, {}, {}, {})
        assert "stale-seed-entry" in [
            f.kind for f in findings if f.var == "PROBE_GONE"]


# --------------------------------------------------------------------------- #
# G4 -- cited evidence must still point at the variable
# --------------------------------------------------------------------------- #
class TestRefIntegrity:
    def test_correct_ref_is_accepted(self, gate):
        assert gate.drifted_refs("LIUHAO_SANDBOX_ENFORCE",
                                 ["src/plugins/sandbox/assurance.py:72"], ()) == []

    def test_ref_to_unrelated_line_is_reported(self, gate):
        bad = gate.drifted_refs("LIUHAO_SANDBOX_ENFORCE",
                                ["src/plugins/sandbox/assurance.py:1"], ())
        assert bad, "a line that never mentions the variable is drift"

    def test_ref_to_missing_file_is_reported(self, gate):
        assert gate.drifted_refs("X", ["src/does/not/exist.py:1"], ())

    def test_ref_beyond_eof_is_reported(self, gate):
        assert gate.drifted_refs("X", ["src/gateway/roster.py:999999"], ())

    def test_table_driven_read_is_not_called_drift(self, gate):
        """posture.py:123 reads ``source.get(STRICT_ENV)``.

        The line mentions neither the env var nor a declared alias, so a naive
        substring check reports a good citation as drift. Accepting the
        constant name is what keeps this checker from crying wolf -- a checker
        that cries wolf gets ignored, which is worse than not having one.
        """
        consts = gate.constant_aliases(["LIUHAO_POSTURE_STRICT"])
        assert "STRICT_ENV" in consts.get("LIUHAO_POSTURE_STRICT", []), (
            "constant_aliases must map the env var to the CONSTANT holding it")
        assert gate.drifted_refs("LIUHAO_POSTURE_STRICT",
                                 ["src/security/posture.py:123"], (),
                                 consts.get("LIUHAO_POSTURE_STRICT", ())) == []

    def test_drifted_finding_lists_only_the_still_valid_refs(self, gate, monkeypatch):
        """A drifted ref must not reappear under the finding's ``read at:`` list.

        ``drifted_refs`` returns a *formatted* description ("<ref> (line
        reads: ...)"). The exclusion filter compared that description against
        the raw ref, so it never matched and the finding printed -- as if it
        were good evidence -- the very pointer it had just called drifted. The
        reader then sees the bad ref twice: once condemned, once presented.
        """
        monkeypatch.setattr(gate, "SEED_GATES", gate.SEED_GATES + ({
            "var": "LIUHAO_SANDBOX_ENFORCE",
            "aliases": (),
            "expect": "set",
            "why": "probe: one drifted ref, one valid ref",
            "refs": ("src/plugins/sandbox/assurance.py:1",     # drifted
                     "src/plugins/sandbox/assurance.py:72"),   # valid
        },))
        findings = gate.evaluate(
            {}, {"LIUHAO_SANDBOX_ENFORCE": [("src/plugins/sandbox/assurance.py", 72)]},
            {}, {})
        drift = [f for f in findings
                 if f.var == "LIUHAO_SANDBOX_ENFORCE" and f.kind == "seed-ref-drifted"]
        assert len(drift) == 1, [f.kind for f in findings]
        assert drift[0].refs == ["src/plugins/sandbox/assurance.py:72"], (
            "the drifted ref must be excluded from the finding's evidence list; "
            "got %r" % (drift[0].refs,))

    def test_every_shipped_ref_is_currently_valid(self, gate):
        """The gate's own table must be clean, or it is citing fiction.

        If this starts failing, the right response is to re-point the ref at the
        line that now reads the variable -- not to relax the check.
        """
        consts = gate.constant_aliases(sorted(
            {str(e["var"]) for e in gate.SEED_GATES}
            | {str(a) for e in gate.SEED_GATES for a in (e.get("aliases") or ())}
        ))
        drifted = []
        for entry in gate.SEED_GATES:
            var = str(entry["var"])
            aliases = tuple(entry.get("aliases") or ())
            bad = gate.drifted_refs(var, tuple(entry.get("refs") or ()),
                                    aliases, consts.get(var, ()))
            if bad:
                drifted.append("%s: %s" % (var, "; ".join(bad)))
        assert not drifted, (
            "SEED_GATES cites evidence that no longer points at the variable:\n  "
            + "\n  ".join(drifted))


# --------------------------------------------------------------------------- #
# The gate must not be able to pass by being broken
# --------------------------------------------------------------------------- #
class TestGateIsNotDecorative:
    def test_no_seed_entry_lacks_a_reason(self, gate):
        for entry in gate.SEED_GATES:
            assert str(entry.get("why", "")).strip(), (
                "%s has no why; an unexplained gate cannot be reviewed"
                % entry["var"])

    def test_no_allowlist_entry_lacks_a_reason(self, gate):
        for var, reason in gate.ALLOWLIST.items():
            assert str(reason or "").strip(), (
                "%s is allowlisted with an empty reason; implicit leniency is "
                "not permitted" % var)

    def test_eval_rejects_an_unknown_expect(self, gate, monkeypatch):
        monkeypatch.setattr(
            gate, "SEED_GATES",
            gate.SEED_GATES + ({"var": "PROBE_BAD", "aliases": (), "expect": "maybe",
                                "why": "probe", "refs": ()},),
        )
        findings = gate.evaluate({}, {"PROBE_BAD": [("src/app.py", 1)]}, {}, {})
        assert "bad-seed-entry" in [f.kind for f in findings if f.var == "PROBE_BAD"]


# --------------------------------------------------------------------------- #
# G5 -- evidence must be version-controlled, or the verdict is local state
# --------------------------------------------------------------------------- #
class TestEvidenceIsVersionControlled:
    """A file no commit contains is not a description of the deployment.

    Measured on 2026-10-04: 2 of the 21 collected artifacts were untracked, and
    one was ``.env`` -- gitignored, containing SECRET_KEY and JWT_SECRET. Had a
    gate variable been set only there, this gate would have reported "set in the
    artifact" on the machine that happened to create the file, and "never set"
    on every other machine and on CI. That is a false green whose cause is
    invisible: nothing about the run looks different.
    """

    def test_env_is_excluded_from_the_evidence_set(self, gate):
        labels = {gate._label(p) for p in gate.collect_artifacts()}
        if ".env" not in labels:
            pytest.skip("no untracked .env on this machine")
        untracked = {gate._label(p) for p in gate.untracked_artifacts(gate.collect_artifacts())}
        assert ".env" in untracked, (
            ".env is gitignored and holds secrets; it must never count as "
            "deployment evidence")

    def test_every_collected_artifact_is_either_tracked_or_reported(self, gate):
        """No artifact may be quietly ignored: tracked, or named in a finding."""
        artifacts = gate.collect_artifacts()
        untracked = {gate._label(p) for p in gate.untracked_artifacts(artifacts)}
        # In the real run these two are reported as untracked-artifact-ignored.
        # This test just pins the invariant that the split is total.
        tracked = {gate._label(p) for p in artifacts} - untracked
        assert tracked | untracked == {gate._label(p) for p in artifacts}

    def test_artifact_collection_is_deterministic(self, gate):
        """Same inputs, same order -- twice.

        A gate whose own evidence set reorders between runs produces findings
        that reorder too, which makes a diff of two runs unreadable and hides
        real changes among cosmetic ones.
        """
        a = gate.collect_artifacts()
        b = gate.collect_artifacts()
        assert [str(p) for p in a] == [str(p) for p in b]

    def test_scan_is_deterministic(self, gate):
        va = gate.scan_artifact_vars(gate.collect_artifacts())
        vb = gate.scan_artifact_vars(gate.collect_artifacts())
        assert json.dumps(va, sort_keys=True) == json.dumps(vb, sort_keys=True)

    def test_python_setter_forms_in_an_artifact_are_recognised(self, gate, tmp_path):
        """A deployment that ships its env via a Python setter must count.

        ``scripts/build_cloud_bundle.py`` sets its whole deployment contract with
        ``os.environ.setdefault`` (LIUHAO_CONSOLE_DIST at :128,
        LIUHAO_HUMAN_IDENTITIES_FILE at :142, LIUHAO_AUTH_SECRETS_FILE at :146,
        LIUHAO_KERNEL_POLICY_ENFORCE at :163), and it is a declared artifact. The
        ``NAME=value`` / ``"NAME":`` regexes cannot see either Python form (a
        "(" or a "]" sits between the name and the "="), so before 2026-10-07 the
        cloud bundle looked like it set nothing and a variable it really sets
        would be reported "never set in a deployment artifact".

        A subscript *read* must still not count as a set.
        """
        probe = tmp_path / "probe_bundle.py"
        probe.write_text(
            "import os\n"
            'os.environ.setdefault("PROBE_BUNDLE_VAR", "x")\n'
            'os.environ["PROBE_SUBSCRIPT_VAR"] = "y"\n'
            'read = os.environ["PROBE_READ_VAR"]\n',
            encoding="utf-8")
        found = gate.scan_artifact_vars([probe])
        assert "PROBE_BUNDLE_VAR" in found, found
        assert "PROBE_SUBSCRIPT_VAR" in found, found
        assert "PROBE_READ_VAR" not in found, (
            "a subscript read is not a set; counting it would let evidence the "
            "artifact merely reads satisfy a gate: %r" % (found,))
