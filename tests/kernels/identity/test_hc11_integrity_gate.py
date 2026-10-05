"""CI gate for HC-11: the identity-chain integrity mechanism must fail closed.

Runs ``scripts/verify_hc11_identity_chain_integrity.py`` as an ISOLATED
subprocess (so its integrity-key env mutations cannot leak into other tests) and
asserts it exits 0 -- i.e. a tampered or unverifiable registry row is rejected
whether or not the integrity key is configured, on BOTH the SQLite
``human_identities`` table and the JsonFileStore.

This is the measured, repeatable basis for HC-11's *mechanism* verdict. The
reason HC-11 stays UNVERIFIED on a key-less host (SOVEREIGNTY OUTAGE: 0 humans
admitted) is a deployment-posture fact, reported by the script separately and not
failing this gate.
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
SCRIPT = os.path.join(REPO_ROOT, "scripts", "verify_hc11_identity_chain_integrity.py")
KEY_ENV = "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"


def test_hc11_integrity_gate_passes():
    env = dict(os.environ)
    # Start from a clean slate for the integrity key so the script controls
    # both postures explicitly via its own probes.
    env.pop(KEY_ENV, None)
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"HC-11 mechanism gate failed (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "FAIL-CLOSED in BOTH key postures" in proc.stdout
