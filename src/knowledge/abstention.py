"""Relevance gate: decide whether retrieved chunks are relevant enough to answer.

Top-k retrieval always returns *something*, so "I searched and found 10 chunks"
is not evidence that the library contains an answer. This gate turns a ranked
list into an explicit verdict.

**Field contract (final; do not rename -- the gateway parses these keys):**

* ``status`` -- exactly ``"ok"`` or ``"no_relevant"``.
* ``reason`` -- ``null`` when ``status`` is ``"ok"``; otherwise one of
  ``"below_threshold"`` / ``"ambiguous"`` / ``"empty_corpus"``.
* ``top_score`` / ``runner_up_score`` -- highest and second-highest cosine
  score, ``null`` when there is no such candidate.
* ``thresholds`` -- the effective absolute floor and gap threshold for this
  call: ``{"min_top_score": float, "min_score_gap": float}``.
* ``hits`` -- the segment list. When ``status`` is ``"no_relevant"`` it MUST be
  an empty array; padding the list out to ``top_k`` is forbidden.

``empty_corpus`` (the library holds zero documents) and ``below_threshold`` /
``ambiguous`` (documents exist but none is relevant) are deliberately different
reasons: the caller has to tell the user "nothing has been ingested yet" versus
"nothing matches your question".

Decision rule (all comparisons use ``>=``: a value exactly on the threshold
passes, and this is covered by tests):

1. absolute floor: ``top_score >= min_top_score``;
2. relative gap: ``top_score - runner_up_score >= min_score_gap``, or
   ``top_score >= strong_match_similarity`` (a near-perfect match may have
   equally good neighbours);
3. lexical corroboration: the FTS5/BM25 path produced at least one candidate.

``confident = absolute and (relative or strong_match or lexical)``. When nothing
is confident the reason is ``below_threshold`` if the absolute floor failed,
else ``ambiguous`` (the floor passed but the lead is not distinguishable).

Degenerate-vector exception (dev-only mock provider returns all-zero vectors,
so cosine scores carry no information at all): when every candidate scores
``0.0`` the absolute floor is meaningless, and the keyword path is the only
signal. In that case ``confident = lexical``; a zero-vector query with no
lexical match is still rejected as ``below_threshold``. This is why the mock
provider's keyword retrieval keeps working without lowering the real floor.

Zero-score opt-in: a caller that explicitly passes ``threshold <= 0.0`` (the
gateway test harness does this for the zero-vector mock provider) has already
declared zero scores acceptable, so all-zero candidates are not re-gated; the
store forwards ``zero_score_allowed=True`` for that call. Production callers
use a positive threshold, so this escape hatch does not weaken the real gate.

Why lexical corroboration is part of the gate (and why it is documented as a
compensator, not a quality claim): thresholds were tuned on the 34-document
hard evaluation set with the deterministic hashing embedder. Measured top-1
cosine similarity for the unanswerable query ("员工年假怎么申请") was **0.145**,
while genuinely answerable queries ranged **0.099-0.501** -- cosine alone
cannot separate the two populations. Keyword-heavy queries score low on that
embedder but are found by FTS5; requiring either a clear gap or lexical
corroboration restores them without letting the no-answer query through
(it produced zero lexical candidates).

Measured sweep (2026-11-06, hashing embedder, 34 docs / 30 queries: 29
answerable + 1 unanswerable). ``strong_match_similarity`` was fixed at 0.85.
"FA" is the false-abstention count over the 29 answerable queries:

    min_top  min_gap  answerable_recall@5  no_answer_abstained  FA
    0.05     0.03     0.8621               1.0                  0
    0.08     0.05     0.8621               1.0                  0
    0.10     0.05     0.8276               1.0                  1
    0.15     0.05     0.7931               1.0                  4
    0.20     0.10     0.6552               1.0                  9

The chosen point is ``0.08 / 0.05``: it keeps the highest answerable recall
(0.8621) with zero false abstentions while rejecting the unanswerable query
(abstention 1.0). This is a **lexical retrieval baseline** -- real
semantic-model numbers are unmeasured, and these thresholds MUST be re-tuned
for any real provider via the environment variables below.

Environment overrides (all optional, checked per call so tests can pin them):

* ``LIUHAO_KB_ABSTAIN_MIN_SIMILARITY`` (default ``0.08``)
* ``LIUHAO_KB_ABSTAIN_MIN_GAP`` (default ``0.05``)
* ``LIUHAO_KB_ABSTAIN_STRONG_MATCH`` (default ``0.85``)

Known limits (stated, not hidden): the gap is computed over chunk-level scores,
so two chunks of the same document can tie and look like a weak lead; a real
semantic model with high absolute scores bypasses the gap via
``strong_match_similarity``; and an unanswerable query that happens to share a
keyword with some document will pass on lexical corroboration. The gate is
deliberately conservative: a false abstention is reported separately in the
evaluation so it can never be hidden behind a good recall number.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

STATUS_OK = "ok"
NO_RELEVANT = "no_relevant"

REASON_BELOW_THRESHOLD = "below_threshold"
REASON_AMBIGUOUS = "ambiguous"
REASON_EMPTY_CORPUS = "empty_corpus"

ENV_MIN_SIMILARITY = "LIUHAO_KB_ABSTAIN_MIN_SIMILARITY"
ENV_MIN_GAP = "LIUHAO_KB_ABSTAIN_MIN_GAP"
ENV_STRONG_MATCH = "LIUHAO_KB_ABSTAIN_STRONG_MATCH"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AbstentionConfig:
    """Thresholds for the relevance gate (see module docstring for tuning)."""

    min_top_similarity: float = 0.08
    min_top_gap: float = 0.05
    strong_match_similarity: float = 0.85

    @classmethod
    def from_env(cls) -> "AbstentionConfig":
        return cls(
            min_top_similarity=_env_float(ENV_MIN_SIMILARITY, 0.08),
            min_top_gap=_env_float(ENV_MIN_GAP, 0.05),
            strong_match_similarity=_env_float(ENV_STRONG_MATCH, 0.85),
        )

    def as_dict(self) -> Dict[str, float]:
        """The two thresholds exposed on ``retrieval.thresholds``."""
        return {
            "min_top_score": self.min_top_similarity,
            "min_score_gap": self.min_top_gap,
        }


def assess_confidence(
    similarities: Mapping[str, float],
    lexical_candidates: int,
    config: Optional[AbstentionConfig] = None,
    distribution_size: int = 5,
    empty_corpus: bool = False,
    zero_score_allowed: bool = False,
) -> Dict[str, Any]:
    """Score a candidate set and return the finalized verdict fields.

    ``similarities`` maps chunk id -> cosine similarity (higher is better).
    ``empty_corpus`` says whether the library itself is empty; it is what
    separates ``empty_corpus`` from ``below_threshold`` when no candidate
    exists. ``zero_score_allowed`` marks a caller that explicitly accepted
    zero scores (``threshold <= 0``), so all-zero candidates are not re-gated.
    The verdict carries the thresholds and the observed distribution so a
    caller can explain *why* it abstained.
    """
    config = config or AbstentionConfig.from_env()
    ordered = sorted(
        ((chunk_id, float(score)) for chunk_id, score in similarities.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    candidate_count = len(ordered)
    if candidate_count == 0:
        return {
            "confident": False,
            "status": NO_RELEVANT,
            "reason": (
                REASON_EMPTY_CORPUS if empty_corpus else REASON_BELOW_THRESHOLD
            ),
            "top_score": None,
            "runner_up_score": None,
            "gap": None,
            "candidate_count": 0,
            "lexical_candidates": lexical_candidates,
            "distribution": [],
            "thresholds": config.as_dict(),
            "checks": {
                "absolute": False,
                "relative": False,
                "strong_match": False,
                "lexical": False,
            },
        }

    top_score = ordered[0][1]
    runner_up_score = ordered[1][1] if candidate_count > 1 else None
    gap = (
        top_score - runner_up_score
        if runner_up_score is not None
        else top_score
    )

    absolute = top_score >= config.min_top_similarity
    relative = gap >= config.min_top_gap
    strong_match = top_score >= config.strong_match_similarity
    lexical = lexical_candidates > 0
    degenerate_vectors = all(score == 0.0 for _, score in ordered)
    if degenerate_vectors:
        confident = zero_score_allowed or lexical
    else:
        confident = absolute and (relative or strong_match or lexical)

    if confident:
        reason = None
    elif not absolute:
        reason = REASON_BELOW_THRESHOLD
    else:
        reason = REASON_AMBIGUOUS

    return {
        "confident": confident,
        "status": STATUS_OK if confident else NO_RELEVANT,
        "reason": reason,
        "top_score": round(top_score, 4),
        "runner_up_score": (
            round(runner_up_score, 4) if runner_up_score is not None else None
        ),
        "gap": round(gap, 4),
        "candidate_count": candidate_count,
        "lexical_candidates": lexical_candidates,
        "distribution": [
            {"chunk_id": chunk_id, "score": round(score, 4)}
            for chunk_id, score in ordered[:distribution_size]
        ],
        "thresholds": config.as_dict(),
        "checks": {
            "absolute": absolute,
            "relative": relative,
            "strong_match": strong_match,
            "lexical": lexical,
        },
    }


def status_for(assessment: Mapping[str, Any]) -> str:
    """Return ``"ok"`` or ``"no_relevant"`` for a verdict."""
    if assessment.get("confident"):
        return STATUS_OK
    return NO_RELEVANT
