"""The battery must not write its report into the repo root by default.

The consolidated EAP report is a root-level-looking markdown document. When
``--out-dir`` defaulted to ``"."`` it landed in whatever directory the script
was invoked from -- i.e. the repo root in the documented invocation -- which
tripped the root-markdown scan-surface check in
``tests/scripts/test_lint_audit_claims_surface.py``. That check is right, and
the fix belongs in the writer: a default output location must not be able to
turn a gate red, and must not depend on the CWD.

These tests hold the line on both halves: the report still gets written (with
the honest "no evidence supplied" verdict), and it never lands at the root.
"""

from __future__ import annotations

import importlib.util
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = (
    _REPO_ROOT / "eap" / "05-final-readiness-battery" / "consolidate_readiness.py"
)


def _load():
    """Import the battery script by path (its directory is not an identifier)."""
    assert _SCRIPT.is_file(), "missing %s" % _SCRIPT
    spec = importlib.util.spec_from_file_location(
        "consolidate_readiness_out_dir_under_test", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_default_out_dir_is_this_batterys_own_directory() -> None:
    """The declared default must track where the script actually lives.

    Hard-coding a path is acceptable only if it cannot silently drift away from
    the directory it describes; this asserts the two agree.
    """
    declared = (_REPO_ROOT / MODULE._BATTERY_DIR_REL).resolve()
    assert declared == _SCRIPT.parent.resolve(), (
        "default --out-dir %r no longer matches the script's own directory %s"
        % (MODULE._BATTERY_DIR_REL, _SCRIPT.parent)
    )


def test_default_run_writes_below_repo_root_and_not_at_it(tmp_path) -> None:
    """Run with no ``--out-dir``: the report must appear in the battery dir."""
    rc = MODULE.main(["--repo-root", str(tmp_path)])

    # No evidence in a bare temp repo -> nothing can be a PASS. Verdict 2
    # ("no FAIL, but BLOCKED-on-host / NOT VERIFIED") is the honest outcome and
    # is asserted so this test cannot pass by the script crashing early.
    assert rc == 2, rc

    assert not (tmp_path / "EAP-EVIDENCE-REPORT.md").exists(), (
        "the default wrote a root-level markdown file -- the exact regression "
        "this change removes"
    )
    assert not (tmp_path / "EAP-EVIDENCE-REPORT.json").exists()

    written = tmp_path / MODULE._BATTERY_DIR_REL / "EAP-EVIDENCE-REPORT.md"
    assert written.is_file(), sorted(p.name for p in tmp_path.iterdir())
    assert (written.parent / "EAP-EVIDENCE-REPORT.json").is_file()
    assert "PRODUCT ACCEPTANCE" in written.read_text(encoding="utf-8")


def test_explicit_relative_out_dir_resolves_against_repo_root(tmp_path) -> None:
    """A relative ``--out-dir`` means "under --repo-root", not "under the CWD".

    Same convention as every evidence path the battery takes; without it the
    documented invocation (``--out-dir eap/05-final-readiness-battery``) would
    write somewhere different depending on where it was run from.
    """
    rc = MODULE.main(
        ["--repo-root", str(tmp_path), "--out-dir", "nested/evidence"]
    )
    assert rc == 2, rc
    assert (tmp_path / "nested" / "evidence" / "EAP-EVIDENCE-REPORT.md").is_file()
    assert not (tmp_path / "EAP-EVIDENCE-REPORT.md").exists()


def test_absolute_out_dir_is_still_honoured(tmp_path) -> None:
    """An absolute destination must keep working (no over-eager re-anchoring)."""
    target = tmp_path / "elsewhere" / "abs"
    rc = MODULE.main(["--repo-root", str(tmp_path), "--out-dir", str(target)])
    assert rc == 2, rc
    assert (target / "EAP-EVIDENCE-REPORT.md").is_file()
