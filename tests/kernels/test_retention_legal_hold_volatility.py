"""HD-06 legal-hold durability — the disclosed limitation must stay disclosed.

WHY THIS TEST EXISTS
--------------------
`LegalHoldRegistry` keeps its holds in an in-process dict. A legal hold therefore
does NOT survive a restart: a redeploy, crash, or worker reschedule constructs an
empty registry and silently lifts every hold, with no error and no log line.

That is a latent evidence-preservation defect, documented on
``src/kernels/retention/legal_hold.py`` after a runtime probe (2026-10-05). The
defect is latent because nothing can place a hold today: the subsystem has no HTTP
route and no production caller of ``place_legal_hold``.

WHAT IS ASSERTED, AND WHY IT IS NOT A BEHAVIOUR CHANGE
------------------------------------------------------
This module deliberately does NOT change behaviour. It asserts two things:

1. **The limitation is real** — so the docstring's warning cannot be deleted on
   the grounds that "it isn't actually broken" without someone first making it
   durable. If a future change persists the registry, this test is SUPPOSED to
   fail, prompting the docstring to be corrected at the same time.
2. **The limitation stays disclosed** — the docstring must keep carrying the
   VOLATILE marker, so a reader deciding whether to rely on a hold is not
   misled. A durable future implementation must remove the warning in the same
   commit that makes it true; that is the intended workflow, not a bypass.

A test that merely asserted "holds are volatile" would be pinning a defect as if
it were a requirement. These assertions instead tie the WARNING to the BEHAVIOUR,
so documentation and reality cannot drift apart silently.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGAL_HOLD = REPO_ROOT / "src" / "kernels" / "retention" / "legal_hold.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------- #
# 1. The limitation is real (falsifiable, not asserted from the docstring).
# --------------------------------------------------------------------------- #

def test_a_legal_hold_does_not_survive_a_fresh_registry() -> None:
    """A restart (fresh registry) silently lifts the hold. This is the defect.

    If this ever starts failing, the registry became durable — which is GOOD.
    In that case `test_volatile_warning_matches_reality` fails and points at the
    docstring, which must then be corrected in the same commit.
    """
    from src.kernels.retention.legal_hold import LegalHoldRegistry

    before_restart = LegalHoldRegistry()
    hold = before_restart.place(
        reason="litigation hold ACME-2026-11",
        placed_by="counsel@liuhao",
        scope="audit-1",
    )
    assert hold.hold_id
    assert before_restart.is_held("audit-1") is True

    after_restart = LegalHoldRegistry()  # exactly what a restart constructs
    assert after_restart.active_holds() == []
    assert after_restart.is_held("audit-1") is False


def test_the_lift_is_silent() -> None:
    """No exception, no signal — which is why the disclosure matters.

    An operator has no way to learn protection was lifted, so the protection
    cannot be treated as reliable without the documented warning.
    """
    from src.kernels.retention.legal_hold import LegalHoldRegistry

    registry = LegalHoldRegistry()
    registry.place(reason="r", placed_by="p", scope="audit-1")
    # Constructing and querying a fresh registry raises nothing and warns
    # nothing; there is no API by which a lost hold could announce itself.
    fresh = LegalHoldRegistry()
    assert fresh.is_held("audit-1") is False
    assert not hasattr(fresh, "lost_holds")


# --------------------------------------------------------------------------- #
# 2. The disclosure must stay, and must stay true.
# --------------------------------------------------------------------------- #

def test_volatile_warning_matches_reality() -> None:
    """Guard the two directions of drift.

    - Registry volatile + warning present  -> correct, test passes.
    - Registry durable + warning present   -> FAILS: the warning is now wrong.
    - Registry volatile + warning removed -> FAILS below (undocumented defect).

    Both disclosure sites are required: the MODULE docstring (what an operator
    reads before relying on a hold) and the CLASS docstring (what an IDE shows
    at the call site). A whole-file substring search is deliberately NOT enough --
    that would pass on a stray "VOLATILE" in any comment, which is exactly the
    hollow disclosure this test exists to prevent.
    """
    from src.kernels.retention.legal_hold import LegalHoldRegistry

    # Is the behaviour still volatile? Ask the implementation, not the prose.
    probe = LegalHoldRegistry()
    probe.place(reason="r", placed_by="p", scope="x")
    behaviour_is_volatile = not LegalHoldRegistry().is_held("x")

    text = LEGAL_HOLD.read_text(encoding="utf-8")
    module_doc = text.split('"""', 2)[1]
    class_doc = inspect.getdoc(LegalHoldRegistry) or ""
    module_warned = "VOLATILE" in module_doc
    class_warned = "VOLATILE" in class_doc

    assert module_warned, (
        "module docstring must keep the VOLATILE marker: a hold can be lifted "
        "by a restart with no error and no log line"
    )
    assert class_warned, (
        "LegalHoldRegistry's class docstring must keep the VOLATILE marker so "
        "the limitation is visible at the call site, not only in the module "
        "header -- a whole-file substring match is not sufficient disclosure"
    )
    assert not (behaviour_is_volatile and not (module_warned and class_warned)), (
        "the registry is volatile but the VOLATILE warning is gone -- "
        "undocumented evidence-preservation defect"
    )
    assert not (not behaviour_is_volatile and module_warned), (
        "the registry now PERSISTS holds, so the VOLATILE warning is stale -- "
        "correct legal_hold.py's module docstring and LegalHoldRegistry's "
        "docstring to describe the durable behaviour"
    )


def test_module_docstring_names_the_restart_lift_explicitly() -> None:
    """The reader must be told WHAT breaks it, not just that it is 'in-memory'."""
    text = LEGAL_HOLD.read_text(encoding="utf-8")
    module_doc = text.split('"""', 2)[1]
    lowered = module_doc.lower()
    for token in ("restart", "redeploy", "silently"):
        assert token in lowered, (
            "module docstring must name %r so an operator knows the hold can be "
            "lifted without warning" % token
        )


def test_latent_not_active_is_stated_with_its_evidence() -> None:
    """The disclosure must not overstate the risk either.

    Saying "this destroys evidence today" would be as inaccurate as saying
    nothing: no production path can place a hold, so the defect cannot yet
    trigger. Overstating a finding is the same failure mode as hiding one.
    """
    from src.kernels.retention.legal_hold import LegalHoldRegistry

    # No production caller outside the package can place a hold.
    reachability = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "kernels/retention" in path.as_posix():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "place_legal_hold(" in body:
            reachability.append(path.relative_to(REPO_ROOT).as_posix())
    assert not reachability, (
        "production code can now place a legal hold: %s -- the legal-hold "
        "durability defect is NO LONGER latent and must be fixed before that "
        "surface is enabled" % (reachability,)
    )

    # And the docstring must say so.
    module_doc = LEGAL_HOLD.read_text(encoding="utf-8").split('"""', 2)[1]
    assert "latent" in module_doc.lower(), (
        "module docstring must record that the defect is currently LATENT "
        "(unreachable from any route) so the risk is not overstated"
    )
    assert LegalHoldRegistry is not None  # keep the import meaningful


# --------------------------------------------------------------------------- #
# 3. The no-destroy-original hard guard is NOT affected by the hold volatility.
# --------------------------------------------------------------------------- #

def test_immutable_original_is_still_never_deletable_without_any_hold() -> None:
    """The hold being volatile does not weaken the no-delete invariant.

    Even with every hold evaporated, the manager's hard guard must still refuse
    to delete an IMMUTABLE_ORIGINAL. That is the invariant that actually
    protects evidence today, and it must not be read as contingent on holds.
    """
    from src.kernels.retention.config import RetentionMode
    from src.kernels.retention.lifecycle import LifecycleState, RecordClass
    from src.kernels.retention.manager import RetentionManager
    from src.kernels.retention.policy import ConfigurableRetentionPolicy
    from src.kernels.retention.config import RetentionConfig

    mgr = RetentionManager(
        policy=ConfigurableRetentionPolicy(),
        config=RetentionConfig(mode=RetentionMode.DELETE_AFTER_DAYS,
                               delete_after_days=1),
    )
    mgr.register_original("o", b"evidence")
    meta = mgr.get("o")
    assert meta is not None
    meta.class_ = RecordClass.IMMUTABLE_ORIGINAL
    decision = mgr.evaluate("o")
    # Whatever the policy asked for, the original survives and says why.
    assert mgr.get("o") is not None
    assert decision.action != "delete_original"
    assert mgr.get("o").state in (
        LifecycleState.ACTIVE,
        LifecycleState.ARCHIVED,
        LifecycleState.HELD,
    )
