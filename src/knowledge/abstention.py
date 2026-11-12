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
2. relative gap between *documents*: the best chunk of each document counts
   once, and ``top_score - runner_up_document_score >= min_score_gap`` must
   hold, or ``top_score >= strong_match_similarity`` (a near-perfect match may
   have equally good neighbours);
3. lexical corroboration: the FTS5/BM25 path produced at least one candidate,
   and the top score clears the calibrated ``floor * (1 + min_top_gap)``
   margin.

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

What lexical corroboration is NOT (measured 2026-11-10, ollama
``nomic-embed-text``, 8 docs / 81 chunks): a "rare token" flag alone must not
overturn the gap band. The absolute ``document frequency <= 2`` definition of
a rare token is corpus-size-fragile -- on that corpus it fired for **19 of
20** probe queries (补贴/上限/支持/工程/2025 all have df <= 2 there), and the
three unanswerable queries it rescued were indistinguishable by score from
answerable ones (e.g. 氢燃料 question top ``0.6580`` vs 纯电动 question top
``0.6577``). Letting the flag decide turned the narrow band into "always
answer". The flag therefore stays computed and reported (``checks.lexical_rare``)
for the future evidence-check, but only the calibrated margin may rescue.

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
(abstention 1.0). This is a **lexical retrieval baseline**.

Hard rule -- thresholds belong to the embedding model, not to this module:

* changing ``LIUHAO_EMBEDDING_PROVIDER`` / ``LIUHAO_EMBEDDING_MODEL`` (or the
  model behind an OpenAI-compatible endpoint) **requires** re-running
  ``python -m src.knowledge.evaluation --provider <provider>`` and adopting its
  ``recommended`` thresholds;
* a real model's cosine scores sit in a different range, so a lexical floor
  such as ``0.08`` becomes a near no-op (almost every candidate clears it);
  the gate then only abstains via the gap rule, and recall/abstention can
  silently degrade on other corpora;
* do not copy the numbers below onto a semantic model, and do not report the
  hashing baseline as a semantic quality claim.

Measured on 2026-11-06 with a real semantic model (Ollama ``nomic-embed-text``,
768-dim, same 34 docs / 30 queries): answerable recall@5 ``0.8793``, @10
``0.8966``, MRR ``0.7023``; no-answer abstained ``1.0``; false abstention
``0/29``. The sweep recommends **min_top 0.65 / min_gap 0.05 / strong 0.85**
(``holds_abstention=true``); the 0.65 absolute floor is the decisive rule, while
the gap keeps its lexical default because the measured data cannot distinguish
alternatives. Its margin is narrow and visible in
``recommended.observed_scores``: lowest answerable top score ``0.6759`` vs
highest unanswerable top score ``0.6280``. With the default ``0.08`` floor the
same run still abstained, but only because the gap rule fired -- the absolute
floor itself did not discriminate. The numeric env defaults below remain
hashing-tuned; a semantic provider never inherits them implicitly -- its own
calibration profile (or an explicit declaration) decides, see the next
paragraph.

**Which thresholds apply?** Not "whatever is in the env" -- the *actual*
embedder decides (``resolve_abstention_config``):

* ``hashing:sha256-bigram-512`` -> ``0.08 / 0.05 / 0.85`` (the measured
  lexical baseline);
* ``ollama:nomic-embed-text`` -> ``0.65 / 0.05 / 0.85`` (the measured real
  model profile);
* ``mock`` (dev-only zero vectors) -> the hashing profile, but the degenerate
  branch below stays in charge;
* any other provider/model (for example an OpenAI-compatible embedding the
  user just configured) -> **uncalibrated**: a lenient gate
  (``-1.0 / 0.0 / 0.85``) that never abstains on a non-empty corpus. Copying
  another model's cosine thresholds here is forbidden: scores are a property
  of the embedding model, not of this module.

To enable strict abstention for a new provider: run
``python -m src.knowledge.evaluation --provider <provider>``, then set
``LIUHAO_KB_ABSTAIN_CALIBRATED_FOR=<provider>:<model>`` **and all three**
threshold env vars from the run's ``recommended.env``. A declaration whose
identity does not match the live embedder, or that omits any threshold, stays
uncalibrated -- a half-set calibration is worse than none. The retrieval
metadata always states where the effective thresholds came from:
``retrieval.abstention_calibration`` = ``{"embedder", "calibrated",
"thresholds_from", "reason"}``, with ``thresholds_from="uncalibrated"`` when
there is no evidence for this embedder.

Environment overrides (all optional, checked per call so tests can pin them):

* ``LIUHAO_KB_ABSTAIN_CALIBRATED_FOR`` (e.g. ``ollama:nomic-embed-text``)
* ``LIUHAO_KB_ABSTAIN_MIN_SIMILARITY`` (default ``0.08``)
* ``LIUHAO_KB_ABSTAIN_MIN_GAP`` (default ``0.05``)
* ``LIUHAO_KB_ABSTAIN_STRONG_MATCH`` (default ``0.85``)

The three numeric defaults only apply to the hashing profile (and to a
declared calibration that completes all three); they are **not** reused for an
unknown provider.

Known limits (stated, not hidden): the relative gap is computed over
*documents* (each document counts once, at its best chunk score) because two
chunks of the same document can tie at chunk level and look like a weak lead;
a real semantic model with high absolute scores still bypasses the gap via
``strong_match_similarity``; and an unanswerable query whose *asked fact*
lives outside the corpus (same-domain near-miss, e.g. a salary question
against a revenue report) still passes when its scores clear the floor and
the document gap -- score intervals of answerable and unanswerable questions
overlap there, so only a downstream evidence-check can close that class. The
gate is deliberately conservative: a false abstention is reported separately
in the evaluation so it can never be hidden behind a good recall number.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

STATUS_OK = "ok"
NO_RELEVANT = "no_relevant"

REASON_BELOW_THRESHOLD = "below_threshold"
REASON_AMBIGUOUS = "ambiguous"
REASON_EMPTY_CORPUS = "empty_corpus"

ENV_MIN_SIMILARITY = "LIUHAO_KB_ABSTAIN_MIN_SIMILARITY"
ENV_MIN_GAP = "LIUHAO_KB_ABSTAIN_MIN_GAP"
ENV_STRONG_MATCH = "LIUHAO_KB_ABSTAIN_STRONG_MATCH"
ENV_CALIBRATED_FOR = "LIUHAO_KB_ABSTAIN_CALIBRATED_FOR"


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


#: Gate for embedders with **no evaluation evidence**: a -1.0 floor and a zero
#: gap clear for every cosine pair, so a non-empty corpus is never judged
#: "not found". Losing recall is worse than answering from the best available
#: (possibly imperfect) chunk; strict abstention requires a calibration.
UNCALIBRATED_CONFIG = AbstentionConfig(
    min_top_similarity=-1.0,
    min_top_gap=0.0,
    strong_match_similarity=0.85,
)

#: Profiles with measured evidence in this repository, keyed by
#: ``provider:model`` (or the bare provider when it has no model name).
#: Add an entry only after running ``python -m src.knowledge.evaluation``.
CALIBRATED_PROFILES: Dict[str, AbstentionConfig] = {
    "hashing:sha256-bigram-512": AbstentionConfig(0.08, 0.05, 0.85),
    "mock": AbstentionConfig(0.08, 0.05, 0.85),
    "ollama:nomic-embed-text": AbstentionConfig(0.65, 0.05, 0.85),
}


def embedder_identity(provider: Optional[str], model: Optional[str]) -> str:
    """Normalized ``provider:model`` key used for calibration matching."""
    provider = (provider or "").strip().lower()
    model = (model or "").strip().lower()
    if provider and model:
        return f"{provider}:{model}"
    return provider or model or "unknown"


def _thresholds_env_complete() -> bool:
    return all(
        (os.environ.get(name) or "").strip()
        for name in (ENV_MIN_SIMILARITY, ENV_MIN_GAP, ENV_STRONG_MATCH)
    )


def resolve_abstention_config(
    provider: Optional[str],
    model: Optional[str],
    *,
    pinned: Optional[AbstentionConfig] = None,
) -> Tuple[AbstentionConfig, Dict[str, Any]]:
    """Pick the gate for the live embedder and explain where it came from.

    Order of precedence:

    1. ``pinned`` -- an explicit caller override (evaluation replay, tests).
       It is *not* evidence, so the metadata says ``thresholds_from="explicit"``.
    2. ``LIUHAO_KB_ABSTAIN_CALIBRATED_FOR`` -- an operator declaration. It only
       counts when it names the live embedder **and** all three threshold env
       vars are set; otherwise the embedder stays uncalibrated.
    3. A built-in profile with in-repo evidence (hashing, nomic).
    4. Anything else -- :data:`UNCALIBRATED_CONFIG`, never another model's
       thresholds, and the metadata says so.
    """
    identity = embedder_identity(provider, model)
    if pinned is not None:
        return pinned, {
            "embedder": identity,
            "calibrated": False,
            "thresholds_from": "explicit",
            "reason": "调用方显式固定阈值（评测重放或测试），不是该 provider 的评测证据",
        }

    declared = (os.environ.get(ENV_CALIBRATED_FOR) or "").strip().lower()
    if declared:
        if declared != identity:
            return UNCALIBRATED_CONFIG, {
                "embedder": identity,
                "calibrated": False,
                "thresholds_from": "uncalibrated",
                "reason": (
                    f"{ENV_CALIBRATED_FOR}={declared} 是另一个 embedding 的校准；"
                    f"当前是 {identity}，不沿用它的阈值"
                ),
            }
        if not _thresholds_env_complete():
            return UNCALIBRATED_CONFIG, {
                "embedder": identity,
                "calibrated": False,
                "thresholds_from": "uncalibrated",
                "reason": (
                    f"{ENV_CALIBRATED_FOR} 声明了 {identity}，但三个阈值环境变量"
                    "没有全部设置；半个校准不可用"
                ),
            }
        return AbstentionConfig.from_env(), {
            "embedder": identity,
            "calibrated": True,
            "thresholds_from": identity,
            "reason": None,
        }

    profile = CALIBRATED_PROFILES.get(identity)
    if profile is not None:
        return profile, {
            "embedder": identity,
            "calibrated": True,
            "thresholds_from": identity,
            "reason": None,
        }

    return UNCALIBRATED_CONFIG, {
        "embedder": identity,
        "calibrated": False,
        "thresholds_from": "uncalibrated",
        "reason": (
            "该 embedding provider/model 没有评测证据：使用宽松弃答（宁可少弃答，"
            "也不把有答案的问题判成没找到）；先运行 "
            "python -m src.knowledge.evaluation --provider <provider>，再设置 "
            f"{ENV_CALIBRATED_FOR}={identity} 和三个阈值环境变量以启用严格弃答"
        ),
    }


def assess_confidence(
    similarities: Mapping[str, float],
    lexical_candidates: int,
    config: Optional[AbstentionConfig] = None,
    distribution_size: int = 5,
    empty_corpus: bool = False,
    zero_score_allowed: bool = False,
    lexical_rare_match: bool = False,
) -> Dict[str, Any]:
    """Score a candidate set and return the finalized verdict fields.

    ``similarities`` maps chunk id -> cosine similarity (higher is better).
    ``empty_corpus`` says whether the library itself is empty; it is what
    separates ``empty_corpus`` from ``below_threshold`` when no candidate
    exists. ``zero_score_allowed`` marks a caller that explicitly accepted
    zero scores (``threshold <= 0``), so all-zero candidates are not re-gated.
    ``lexical_rare_match`` says the best BM25 chunk contains a *rare* query
    token (document frequency <= 2). The flag is reported in
    ``checks.lexical_rare`` for observability and for a future evidence-check,
    but it no longer rescues the gap band: measured on an 81-chunk corpus the
    flag fired for 19 of 20 queries, including three unanswerable ones whose
    scores were indistinguishable from answerable ones, so letting it decide
    turned the narrow band into "always answer". Lexical corroboration may
    rescue only through the calibrated ``floor * (1 + gap)`` margin.
    The verdict carries the thresholds and the observed distribution so a
    caller can explain *why* it abstained.
    """
    config = config or AbstentionConfig.from_env()
    # A non-positive floor means "don't gate on similarity": it is what the
    # uncalibrated default uses. Zero/degenerate vectors must then be accepted
    # too, otherwise an uncalibrated provider could still falsely abstain.
    zero_score_allowed = zero_score_allowed or config.min_top_similarity <= 0.0
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
    # The narrow band compares *documents*, not chunks: two chunks of the same
    # document routinely tie (same wording, sibling sections) and a chunk-level
    # gap then looks like a weak lead even though only one document is in
    # contention. Each document counts once, at its best chunk score. When the
    # candidates collapse to a single document there is no cross-document
    # ambiguity to resolve, so the chunk-level gap stays authoritative (a pile
    # of identical sibling chunks must remain "ambiguous").
    document_best: Dict[str, float] = {}
    for chunk_id, score in ordered:
        document = str(chunk_id).rsplit("#", 1)[0]
        if score > document_best.get(document, float("-inf")):
            document_best[document] = score
    document_scores = sorted(document_best.values(), reverse=True)
    if len(document_scores) > 1:
        doc_runner_up = document_scores[1]
        doc_gap: Optional[float] = top_score - doc_runner_up
    else:
        doc_runner_up = None
        doc_gap = None
    band_gap = doc_gap if doc_gap is not None else gap

    absolute = top_score >= config.min_top_similarity
    relative = band_gap >= config.min_top_gap
    strong_match = top_score >= config.strong_match_similarity
    lexical = lexical_candidates > 0
    # A lexical hit may rescue a low absolute vector score (exact part numbers,
    # ticket ids), but it must be a *clear* keyword win, not a common word:
    # with a near-tie top two, "知识库" matching several chunks made an
    # unanswerable question look confident (0.6632 vs 0.6547, gap 0.0085).
    # The margin is relative to the provider's own floor, because score units
    # are not comparable across embedders: hashing clears 0.08 by 0.019 while
    # nomic clears 0.65 by 0.013, and only the latter is the ambiguous case.
    # ``lexical_rare_match`` (df <= 2) deliberately does NOT rescue the band:
    # measured on an 81-chunk corpus it fired for 19/20 queries -- 上限/支持/工程
    # all have df <= 2 there -- so the flag cannot tell "pins one entity" from
    # "small corpus". It stays reported in ``checks.lexical_rare``.
    floor_margin = config.min_top_similarity * (1.0 + config.min_top_gap)
    lexical_rescue = lexical and top_score >= floor_margin
    degenerate_vectors = all(score == 0.0 for _, score in ordered)
    if degenerate_vectors:
        confident = zero_score_allowed or lexical
    else:
        confident = absolute and (relative or strong_match or lexical_rescue)

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
            "lexical_rare": lexical_rare_match,
            "doc_gap": round(doc_gap, 4) if doc_gap is not None else None,
            "doc_runner_up_score": (
                round(doc_runner_up, 4) if doc_runner_up is not None else None
            ),
        },
    }


def status_for(assessment: Mapping[str, Any]) -> str:
    """Return ``"ok"`` or ``"no_relevant"`` for a verdict."""
    if assessment.get("confident"):
        return STATUS_OK
    return NO_RELEVANT
