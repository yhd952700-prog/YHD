"""Non-vacuity proof for the HOLD-ARMING rule in verify_critical5_watchdog.py.

``scripts/check_hold_gate.py`` treats an UNSET ``LIUHAO_HOLD`` as "HOLD
inactive -> exit 0". So a workflow that runs the HOLD gate without an armed
input is a control that looks present and enforces nothing -- and nothing in
the pipeline turns red, because the gate exits 0.

``verify_critical5_watchdog.py`` section [5] exists to make that state loud.
These tests are its falsification: without them, "the gate refuses an unarmed
HOLD control" would be a claim nobody can check, and the rule could rot into a
rubber stamp while still printing OK.

The script is driven as a subprocess through its documented test hook
``CRIT5_WORKFLOWS_DIR`` (pointing the scan at fixture workflows outside the
repo), which is exactly the hook that was put there for before-fix/after-fix
proofs. Nothing here writes inside the repository.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_critical5_watchdog.py"

_HOLD_CALL = "python scripts/check_hold_gate.py"
_STEP_NAME = "Release gate — HOLD control (C1)"


def _workflow(env_line: str | None, body: str) -> str:
    """A minimal one-job workflow that invokes the HOLD gate.

    ``env_line`` is placed as a JOB-level ``env:`` entry (or omitted); ``body``
    is the ``run:`` payload of the invoking step.
    """
    env_block = f"    env:\n      {env_line}\n" if env_line else ""
    return (
        "name: fixture\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "jobs:\n"
        "  release:\n"
        "    runs-on: ubuntu-latest\n"
        f"{env_block}"
        "    steps:\n"
        f"      - name: {_STEP_NAME}\n"
        f"{body}"
    )


def _run_fixture(tmp_path: pathlib.Path, content: str) -> tuple[int, str]:
    """Run the watchdog with its workflow scan pointed at a fixture directory."""
    (tmp_path / "fixture.yml").write_text(content, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env={
            **os.environ,
            "CRIT5_WORKFLOWS_DIR": str(tmp_path),
            # Pin the reference branch so this test does not depend on which
            # branch the checkout happens to be on.
            "LIUHAO_WORKING_BRANCH": "p36",
        },
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _section5(output: str) -> str:
    """The [5] HOLD-arming block only.

    Scoped deliberately: sections [2]-[4] depend on the host (a POSIX shell, a
    git checkout). This module grades the ARMING rule, and a host-dependent
    failure elsewhere must not make it look like the arming rule fired.
    """
    start = output.index("[5] HOLD-gate arming")
    end = output.index("RESULT:", start)
    return output[start:end]


def _arming_failures(output: str) -> list[str]:
    """The per-workflow HOLD-ARMING *failure* lines, not the section summary.

    Distinguished by shape: a failure cites ``<workflow>.yml:<job>`` whereas the
    section's tally line reads ``HOLD-ARMING: N/M job(s) ...``. Matching on the
    whole line and requiring the ``.yml:`` marker matters -- fixture paths are
    absolute, so a naive ``"HOLD-ARMING: fixture.yml"`` substring would silently
    never match, and the negative assertions below would then be vacuous.
    """
    return [
        line.strip()
        for line in output.splitlines()
        if "HOLD-ARMING:" in line and ".yml:" in line
    ]


def _run_body(run: str) -> str:
    return "        run: " + run + "\n"


# --------------------------------------------------------------------------- #
# The rule bites
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("env_line", "run", "expected_kind", "expected_reason"),
    [
        pytest.param(
            None,
            _HOLD_CALL,
            "UNARMED",
            "not defined in the job's effective env",
            id="unarmed_key_absent",
        ),
        pytest.param(
            'LIUHAO_HOLD: "1"',
            _HOLD_CALL,
            "HARDCODED",
            "hardcoded literal",
            id="hardcoded_literal",
        ),
        pytest.param(
            'LIUHAO_HOLD: "0"',
            _HOLD_CALL,
            "HARDCODED",
            "hardcoded literal",
            id="hardcoded_zero_is_also_a_constant_control",
        ),
        pytest.param(
            "LIUHAO_HOLD: ${{ '1' }}",
            _HOLD_CALL,
            "HARDCODED",
            "constant wearing expression syntax",
            id="constant_in_expression_clothes",
        ),
    ],
)
def test_unarmed_or_hardcoded_hold_control_is_a_failure(
    tmp_path: pathlib.Path,
    env_line: str | None,
    run: str,
    expected_kind: str,
    expected_reason: str,
) -> None:
    """A HOLD gate bound to nothing (or to a constant) must not read as OK."""
    code, output = _run_fixture(tmp_path, _workflow(env_line, _run_body(run)))
    section = _section5(output)
    assert expected_kind in section, section
    assert expected_reason in section, section
    assert "HOLD-ARMING: 0/1 job(s)" in section, section
    assert any("fixture.yml:release" in ln for ln in _arming_failures(output)), output
    assert code != 0, output


def test_step_level_override_to_a_literal_shadows_a_good_job_env(
    tmp_path: pathlib.Path,
) -> None:
    """The trap this rule exists for: a traceable job env, overridden per step.

    Grading only the job-level ``env`` would call this armed; the value the gate
    actually reads is the literal.
    """
    content = _workflow(
        "LIUHAO_HOLD: ${{ vars.LIUHAO_HOLD || '1' }}",
        "        env:\n"
        '          LIUHAO_HOLD: "1"\n'
        f"        run: {_HOLD_CALL}\n",
    )
    _, output = _run_fixture(tmp_path, content)
    section = _section5(output)
    assert "HARDCODED" in section, section
    assert "OK" not in section, section


# --------------------------------------------------------------------------- #
# The rule does not cry wolf
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "env_line",
    [
        pytest.param(
            "LIUHAO_HOLD: ${{ vars.LIUHAO_HOLD || '1' }}",
            id="repo_var_with_safe_default",
        ),
        pytest.param(
            "LIUHAO_HOLD: ${{ inputs.hold || vars.LIUHAO_HOLD || '1' }}",
            id="dispatch_input_or_repo_var",
        ),
        pytest.param(
            "LIUHAO_HOLD: ${{ secrets.LIUHAO_HOLD }}",
            id="secret",
        ),
    ],
)
def test_platform_bound_value_is_armed(tmp_path: pathlib.Path, env_line: str) -> None:
    """A value a human can set through the platform must not be flagged."""
    _, output = _run_fixture(tmp_path, _workflow(env_line, _run_body(_HOLD_CALL)))
    section = _section5(output)
    assert "bound to a platform-set variable" in section, section
    assert "HOLD-ARMING: 1/1 job(s)" in section, section
    assert not _arming_failures(output), output


def test_unresolvable_expression_degrades_to_a_declared_text_level_rule(
    tmp_path: pathlib.Path,
) -> None:
    """An expression the gate cannot resolve is graded by a DECLARED fallback.

    It must not fail (that would be a semantic guess), and it must not silently
    pass for a semantic reason it never proved -- the output has to say the
    criterion was degraded.
    """
    _, output = _run_fixture(
        tmp_path, _workflow("LIUHAO_HOLD: ${{ github.sha }}", _run_body(_HOLD_CALL))
    )
    section = _section5(output)
    assert "DEGRADED" in section, section
    assert "cannot resolve to a platform variable" in section, section
    assert "NOTE (DEGRADED criterion)" in section, section
    assert "NOT proven" in section, section
    # The critical half: a degraded value must NOT be counted as armed. Rolling
    # it into "1/1 resolve to a platform-set value" would be exactly the
    # overclaim this section exists to prevent.
    assert "HOLD-ARMING: 0/1 job(s)" in section, section
    assert "graded by the DEGRADED text-level rule only" in section, section
    assert "1/1 job(s) that run the HOLD gate" not in section, section
    assert not _arming_failures(output), output


def test_a_comment_mentioning_the_script_is_not_an_invocation(
    tmp_path: pathlib.Path,
) -> None:
    """The false-green class already fixed once in test_guardrail_scripts.

    A job that only names check_hold_gate.py inside a comment does not run it,
    so it has nothing to arm and must not be graded.
    """
    content = _workflow(
        "LIUHAO_HOLD: ${{ vars.LIUHAO_HOLD || '1' }}",
        "        run: |\n"
        f"          # {_HOLD_CALL} is mentioned only in this comment\n"
        '          echo "not a call"\n',
    )
    _, output = _run_fixture(tmp_path, content)
    section = _section5(output)
    assert "no workflow runs" in section, section
    assert "0/1 job(s)" not in section, section
    assert not _arming_failures(output), output


# --------------------------------------------------------------------------- #
# The real repo is graded honestly (not just the fixtures)
# --------------------------------------------------------------------------- #
def test_real_ci_cd_hold_job_is_armed_and_not_flagged() -> None:
    """The shipped release path must be armed, and said so out loud.

    If someone "simplifies" ``ci-cd.yml``'s ``LIUHAO_HOLD`` back to a constant
    (the tempting edit), this test is what notices.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    section = _section5(output)
    assert "ci-cd.yml:build" in section, section
    assert "bound to a platform-set variable" in section, section
    assert "HOLD-ARMING: 1/1 job(s)" in section, section
    assert not _arming_failures(output), output
