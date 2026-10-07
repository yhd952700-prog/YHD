"""Defect D: "save" must be disambiguated between FILE and MEMORY, not both.

Measured before the fix (real decomposer, real capability registry)::

    "save a file named report.txt containing 'hello'"
        -> [Memory Store(content="save a file named report.txt containing
             'hello'"), WriteFile, WriteChecksum]
    "save 'hello' to the file c.txt"
        -> [Memory Store(content="save 'hello' to the file c.txt"),
            WriteFile, WriteChecksum]

``save`` is a file verb (``_FILE_WRITE_A`` / ``_FILE_WRITE_B`` match
"save a file named X") AND a memory verb ("save this to memory"), and the
memory branch fired on it unconditionally. Two things went wrong at once:

1. **Intent drift** -- a pure file-write goal ran a capability the user never
   asked for. The goal was not what the user said it was.
2. **Memory pollution** -- the memory task was fed the RAW sentence as its
   content, so memory recorded "save a file named report.txt containing
   'hello'" as something the user wanted to remember.

Same defect family as the earlier "plan"/"replan" substring drift: work the
user never asked for, invented by a matcher that cannot tell intent apart.

The disambiguation is one-directional and fail-closed on the side of NOT
inventing work: when the goal is recognised as a file write, "save" is spent
on the file task. Memory still fires on the unambiguous verbs
(remember / store / memory), so "save x.txt and remember it" still works.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="liuhao_defect_d_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

from src.kernels.execution import Goal, GoalDecomposer  # noqa: E402


def _caps(text: str) -> set:
    return {
        t.capability_id
        for t in GoalDecomposer().decompose(
            Goal(id="g", natural_language=text, scope="L1")
        )
    }


# ---------------------------------------------------------------------------
# 1. the defect: a file-save goal must NOT conjure a memory task
# ---------------------------------------------------------------------------
def test_saving_a_file_does_not_also_write_memory():
    """'save' spent on the file task -- no phantom Memory Store."""
    for text in (
        "save a file named report.txt containing 'hello'",
        "save 'hello' to the file c.txt",
        "save a file named notes.md containing 'x'",
    ):
        caps = _caps(text)
        assert "file_write" in caps, (text, caps)
        assert "multi_tier_memory" not in caps, (
            f"{text!r} must not invent a memory task the user never asked "
            f"for; got {caps}"
        )


# ---------------------------------------------------------------------------
# 2. controls: real memory intent still routes to memory
# ---------------------------------------------------------------------------
def test_unambiguous_memory_verbs_still_route_to_memory():
    """remember / store / memory are not ambiguous -- they must keep working."""
    for text in (
        "remember that the sky is blue",
        "store the answer in memory",
        "save this to memory: the sky is blue",
    ):
        caps = _caps(text)
        assert "multi_tier_memory" in caps, (text, caps)


def test_explicit_memory_mention_alongside_a_file_save_is_still_honoured():
    """Disambiguation must not swallow a memory request the user DID make."""
    caps = _caps("save a file named d.txt and remember it in memory")
    assert "file_write" in caps, caps
    assert "multi_tier_memory" in caps, caps


def test_plain_file_write_verbs_are_unaffected():
    """Control: 'write' was never in the memory keyword set, and still is not."""
    caps = _caps("write a file named a.txt containing 'hello'")
    assert caps == {"file_write"}, caps
