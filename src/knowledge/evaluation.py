"""Reproducible retrieval evaluation for the knowledge base (baseline guard).

This module is deliberately self-contained and deterministic so *anyone* can
re-run the same numbers before and after a retrieval change::

    python -m src.knowledge.evaluation

It builds a small in-memory corpus in a temporary SQLite file, ingests it
through the real :class:`KnowledgeDocumentService` (structured chunking + FTS5
hybrid retrieval), and reports ``recall@5``, ``recall@10`` and ``MRR``.

The evaluation set is deliberately **hard** (see ``QUERIES`` for the intent of
each case): zero-token-overlap paraphrases, near-miss distractor documents
(same topic, different answer), adjacent version/model identifiers
(``v1.2.3`` vs ``v1.2.4``, ``XG-9001`` vs ``XG-9002``), one long document with
three similar sections (graded with an answer marker), and an unanswerable
query that the relevance gate (``abstention``) must reject with an empty
result.

The report deliberately carries **three groups of numbers**, because any one
of them alone is misleading:

* ``answerable_recall@5/@10`` and ``answerable_mrr`` -- recall over the 29
  queries that do have an answer;
* ``no_answer_abstained`` -- fraction of the 1 unanswerable query that
  correctly came back empty;
* ``false_abstention_rate`` -- fraction of answerable queries the gate wrongly
  rejected. A high recall is worthless if it is bought by letting garbage
  through, and a high abstention rate is worthless if it rejects real answers.

The legacy mixed averages (``recall@5``/``recall@10``/``mrr``) are kept for
continuity with earlier commits; they count an abstained unanswerable query as
1.0 and remain comparable to the previous baseline.

The default embedder is a dependency-free hashing embedder (signed SHA-256 hashing
of the same CJK-bigram/ASCII tokens the FTS side uses). It is *not* a semantic
model: zero-overlap synonym queries are expected to fail. ``BASELINE`` therefore
describes **lexical retrieval only** and must never be reported as a semantic
quality claim.

Re-running on a real provider is one command, and it is mandatory whenever the
embedding model changes (the gate thresholds are tied to the score distribution
of that model, not to the retrieval code)::

    python -m src.knowledge.evaluation                     # hashing baseline + sweep
    python -m src.knowledge.evaluation --provider ollama   # local Ollama
    python -m src.knowledge.evaluation --provider all      # hashing + every detected real provider

``--provider`` accepts ``hashing`` / ``ollama`` / ``openai`` / ``auto`` / ``all``
(comma-separated allowed), plus ``--model`` / ``--base-url`` / ``--api-key``
overrides. The comparison table goes to stderr and the JSON report to stdout:
per provider it carries the three metric groups, the effective thresholds, and
a ``recommended`` threshold set produced by replaying the *same*
``assess_confidence`` gate over every observed top/runner-up pair (no second
embedding pass, and no re-implementation of the decision rule).

Measured on 2026-11-06 with a real semantic model (Ollama ``nomic-embed-text``,
768-dim cosine, same 34 documents / 30 queries), **after** the BM25-weighted
fusion fix described below:

* ``answerable_recall@5`` **0.8966**, ``answerable_recall@10`` **0.8966**,
  ``answerable_mrr`` **0.8534**;
* ``no_answer_abstained`` **1.0**, ``false_abstention_rate`` **0.0**
  (``0/29``);
* ``section_hit@5`` **1.0** -- the long document's correct section
  (``pool_size``) is now first instead of absent from the top 5.

Before the fix the same setup reported 0.8793 / 0.8966 / 0.7023, abstention
1.0, no false abstentions, and ``section_hit@5`` **0.0**. The defect was not
candidate coverage (all 36 chunks fit in the 40-candidate window), not chunk
size, and not ``context_header`` placement; both of those were measured and
reverted (see ``vector_store``). The cause was plain RRF discarding BM25
magnitude: the correct chunk was lexical rank 1 (BM25 ``-22.6``) but two
sibling sections at lexical ranks 3/4 (BM25 ``-5.9``) each collected vector
rank contributions and outvoted it. Fusing the lexical list as
``bm25_strength/(k+rank)`` puts the correct section first.

The alternative fusion variants were measured one at a time on the same data
(offline probe, document names not deduplicated, so its absolute MRR differs
slightly from ``run_evaluation``): flat RRF -> section 0.0 / rank 6;
``rrf + 0.01*bm25`` -> section 1.0 / rank 2; ``rrf + 0.02..0.10*bm25``,
``bm25_strength/(k+rank)`` and ``0.5*cos + 0.5*bm25`` -> section 1.0 / rank 1
with the best recall/MRR. The strength-weighted form was chosen because it
adds no extra tuning constant; the alpha variants need one.

The hashing (lexical) baseline deliberately keeps its own numbers rather than
inheriting this model's result: recall 0.8621, no-answer abstention 1.0, false
abstention 0.0, section 1.0, but ``answerable_mrr`` 0.8621 -> 0.8448 because on
two queries BM25 prefers a distractor document that the lexical embedder had
already ranked correctly. That is a recorded, accepted trade-off, not a
threshold change.

The sweep's recommended gate for that model is **min_top 0.65 / min_gap 0.05 /
strong 0.85**, with ``holds_abstention=true``. The decisive rule is the 0.65
absolute floor; the gap keeps its lexical default because the measured data
cannot distinguish alternatives (metric-identical sets prefer the least change).
The margin is narrow and is
reported in ``recommended.observed_scores``: lowest answerable top score 0.6759
vs highest unanswerable top score 0.6280. The lexical baseline's 0.08 floor is
meaningless for this model (almost every cosine score clears it); the gate only
abstained because the gap rule fired. That is exactly why thresholds must be
re-tuned per model -- copying 0.08 onto a semantic model weakens the absolute
floor to a no-op.

Chinese tokenization limitations (bigram, no segmentation/synonyms) also apply
here; see ``vector_store`` module documentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .abstention import (
    ENV_CALIBRATED_FOR,
    ENV_MIN_GAP,
    ENV_MIN_SIMILARITY,
    ENV_STRONG_MATCH,
    NO_RELEVANT,
    STATUS_OK,
    AbstentionConfig,
    assess_confidence,
    embedder_identity,
)
from .documents import KnowledgeDocumentService
from .embedding import (
    EmbeddingUnavailableError,
    OllamaEmbedder,
    OpenAICompatibleEmbedder,
)
from .vector_store import PersistentVectorStore, tokenize_for_index

# ---------------------------------------------------------------------------
# Evaluation set (20-30 queries over four categories)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalDocument:
    doc_id: str
    text: str


@dataclass(frozen=True)
class EvalQuery:
    query: str
    relevant_doc_ids: Tuple[str, ...]
    category: str
    # Optional string that must appear in a top-5 hit to prove the *correct
    # section* of a long document was retrieved (doc-level recall cannot see it).
    answer_marker: Optional[str] = None


CORPUS: Tuple[EvalDocument, ...] = (
    EvalDocument(
        "kb-001",
        "# 部署与安装\n\n贾维斯控制台通过局域网部署，默认端口 8700，"
        "启动脚本为 scripts/launch_gateway.py。\n\n"
        "安装完成后访问 /healthz 检查服务状态；升级前先停止旧进程。",
    ),
    EvalDocument(
        "kb-002",
        "# 认证\n\n访问令牌使用 JWT，过期时间 30 分钟；刷新令牌有效期 7 天。"
        "权限等级分为 L0 与 L1。\n\n令牌丢失时应重新登录，不要复用旧令牌。",
    ),
    EvalDocument(
        "kb-003",
        "# 型号适配\n\n适配器型号 XG-9000，支持 802.11ac，电源规格 12V/2A。\n\n"
        "固件版本必须为 2.1.0 才能启用 802.11ac。",
    ),
    EvalDocument(
        "kb-004",
        "# 工单流程\n\n故障单 TICKET-8842 的处理步骤：受理、分派、复现、修复、回访。\n\n"
        "工单关闭后 3 天内必须完成回访记录。",
    ),
    EvalDocument(
        "kb-005",
        "# 备份策略\n\n每日 02:30 执行全量备份，备份保留 14 天，并保存异地副本。\n\n"
        "恢复演练每季度一次，恢复窗口不超过 4 小时。",
    ),
    EvalDocument(
        "kb-006",
        "# 日志排查\n\n日志目录为 data/logs，日志级别 INFO，错误关键字 traceback，"
        "日志轮转保留 7 天。\n\n排查时优先搜索错误关键字，再结合 traceback 定位。",
    ),
    EvalDocument(
        "kb-007",
        "# 限流\n\n网关限流每分钟 600 次，超限返回 429，白名单配置在 "
        "config/rate_limits.yaml。\n\n超过 600 次后按分钟窗口重置，不会永久封禁。",
    ),
    EvalDocument(
        "kb-008",
        "# 数据库\n\n知识库使用 SQLite 文件 data/knowledge_vectors.sqlite3，"
        "启用 WAL 模式，单进程写入。\n\n备份数据库前先执行 checkpoint，避免 WAL 未落盘。",
    ),
    EvalDocument(
        "kb-009",
        "# 权限\n\n管理员角色 admin 拥有全部权限；审计角色 auditor 只读；"
        "权限变更需要审批。\n\n审批记录写入审计日志，保留 180 天。",
    ),
    EvalDocument(
        "kb-010",
        "# 回滚\n\n发布失败时执行 rollback.sh，回滚到上一版本，并保留现场日志。\n\n"
        "回滚后必须验证核心接口，确认没有 5xx。",
    ),
    EvalDocument(
        "kb-011",
        "# 机器要求\n\n最低配置 4 核 8GB 内存，操作系统 Windows 10/11，Python 3.12。\n\n"
        "推荐 8 核 16GB，磁盘至少 100GB。",
    ),
    EvalDocument(
        "kb-012",
        "# 网络\n\n需要放通 443 与 8700 端口，离线环境使用本地镜像源。\n\n"
        "离线镜像源地址在 config/mirror.yaml，无需外网。",
    ),
    EvalDocument(
        "kb-013",
        "# 通知\n\n告警通过飞书机器人推送，静默窗口为 22:00-08:00。\n\n"
        "夜间静默期间只推送 P1 级告警。",
    ),
    EvalDocument(
        "kb-014",
        "# 升级\n\n升级前停止服务，执行 upgrade.py，保留旧版本目录以便回滚。\n\n"
        "升级完成后核对版本号与健康检查结果。",
    ),
    EvalDocument(
        "kb-015",
        "# 安全\n\n密钥存放在环境变量，禁止提交到仓库，泄露后立即轮换。\n\n"
        "环境变量由部署系统注入，禁止写入配置文件。",
    ),
    # 干扰文档：与上面主题共享关键词，但不回答对应查询
    EvalDocument(
        "kb-016",
        "# 端口与防火墙\n\n除了 443，测试环境还使用 8080；防火墙规则由运维统一维护。\n\n"
        "生产环境禁止直接暴露管理端口。",
    ),
    EvalDocument(
        "kb-017",
        "# 备份恢复演练\n\n每季度做一次恢复演练，演练记录归档到 data/drills。\n\n"
        "演练失败需在 24 小时内复盘。",
    ),
    EvalDocument(
        "kb-018",
        "# 日志级别调整\n\n默认 INFO，排查问题时临时调为 DEBUG，问题解决后改回 INFO。\n\n"
        "DEBUG 日志体积大，禁止长期开启。",
    ),
    EvalDocument(
        "kb-019",
        "# 令牌与密钥轮换\n\n访问令牌每 30 分钟过期；密钥泄露后立即轮换并通知安全团队。\n\n"
        "轮换窗口安排在工作日白天。",
    ),
    EvalDocument(
        "kb-020",
        "# 发布检查清单\n\n发布前确认测试通过、变更单审批完成、回滚脚本可用。\n\n"
        "发布后观察 30 分钟错误率。",
    ),
    EvalDocument(
        "kb-021",
        "# 数据库迁移\n\n迁移前备份 SQLite 文件，使用 alembic 生成迁移脚本。\n\n"
        "迁移失败时按备份恢复，不手工改表。",
    ),
    EvalDocument(
        "kb-022",
        "# 硬件扩容\n\n内存不足时优先扩容到 16GB，CPU 核数按并发调整。\n\n"
        "扩容后重新压测。",
    ),
    EvalDocument(
        "kb-023",
        "# 权限审计\n\n每季度审计 admin 账号，auditor 只读权限不得提升。\n\n"
        "离职账号 24 小时内禁用。",
    ),
    EvalDocument(
        "kb-024",
        "# 网络排障\n\n使用 ping 与 curl 检查连通性，注意 443 是否被拦截。\n\n"
        "抓包文件保存到 data/pcap。",
    ),
    # --- 加难用文档 ---------------------------------------------------------
    # 零字面重合同义词目标：全文没有“备份/如何/做备”等查询词
    EvalDocument(
        "kb-025",
        "# 灾备与数据保全\n\n每周生成一次快照，保留 8 份异地副本，可回退到任意时点。\n\n"
        "恢复演练由值班工程师执行。",
    ),
    # 零字面重合同义词目标：全文没有“磁盘/快满/怎么办”
    EvalDocument(
        "kb-026",
        "# 容量与存储水位\n\n存储水位达到 85% 时触发扩容评估，由运维确认后再执行。\n\n"
        "水位低于 60% 时解除告警。",
    ),
    # 零字面重合同义词目标：全文没有“登录/掉线”
    EvalDocument(
        "kb-027",
        "# 会话与自动重连\n\n空闲 15 分钟后触发自动重连，粘滞会话保持 30 分钟。\n\n"
        "重连失败三次后提示重新认证。",
    ),
    # 相邻版本号：只差最后一位
    EvalDocument(
        "kb-028",
        "# 版本 v1.2.3 发布说明\n\n修复登录超时问题，升级前请阅读兼容性说明。",
    ),
    EvalDocument(
        "kb-029",
        "# 版本 v1.2.4 发布说明\n\n修复报表导出乱码，兼容 v1.2.3 的配置。",
    ),
    # 相邻型号：只差最后一位
    EvalDocument(
        "kb-030",
        "# 型号 XG-9001\n\n支持 802.11ax 协议，电源 12V/3A。",
    ),
    EvalDocument(
        "kb-031",
        "# 型号 XG-9002\n\n支持 802.11ax 协议，电源 12V/4A。",
    ),
    # 长文档，三段相似章节；正确答案只在“数据库连接超时”一节
    EvalDocument(
        "kb-032",
        "# 连接故障手册\n\n"
        "## 数据库连接超时\n\n"
        "数据库连接池耗尽时接口返回 504，需要调大 pool_size 后重启。\n\n"
        "## 缓存连接超时\n\n"
        "缓存连接超时通常是网络抖动，等待重试即可恢复。\n\n"
        "## 消息队列连接超时\n\n"
        "消息队列连接超时请检查 broker 地址与鉴权配置。",
    ),
    # 近似干扰文档：主题与 kb-032 的数据库超时高度相似，但答案不同
    EvalDocument(
        "kb-033",
        "# 数据库性能优化\n\n连接池耗尽也会导致 504，可通过增加 max_connections 缓解。\n\n"
        "慢查询请先看执行计划。",
    ),
    # 零字面重合同义词目标：全文没有“服务器/重启/原因”
    EvalDocument(
        "kb-034",
        "# 节点自愈\n\n检测到进程假死时自动拉起，连续三次失败则隔离节点。\n\n"
        "隔离后由值班工程师介入。",
    ),
)

QUERIES: Tuple[EvalQuery, ...] = (
    # 事实查询
    EvalQuery("默认端口是多少", ("kb-001",), "fact"),
    EvalQuery("访问令牌过期时间", ("kb-002",), "fact"),
    EvalQuery("每日备份几点执行", ("kb-005",), "fact"),
    EvalQuery("日志放在哪个目录", ("kb-006",), "fact"),
    EvalQuery("告警通过什么方式推送", ("kb-013",), "fact"),
    EvalQuery("密钥应该存放在哪里", ("kb-015",), "fact"),
    # 编号 / 型号 / 标识符查询（含相邻版本、相邻型号）
    EvalQuery("XG-9000", ("kb-003",), "identifier"),
    EvalQuery("TICKET-8842 怎么处理", ("kb-004",), "identifier"),
    EvalQuery("返回 429 是什么含义", ("kb-007",), "identifier"),
    EvalQuery("knowledge_vectors.sqlite3 文件位置", ("kb-008",), "identifier"),
    EvalQuery("admin 和 auditor 的权限区别", ("kb-009",), "identifier"),
    EvalQuery("rollback.sh 的作用", ("kb-010",), "identifier"),
    EvalQuery("v1.2.4 修复了什么", ("kb-029",), "identifier"),
    EvalQuery("XG-9001 支持什么协议", ("kb-030",), "identifier"),
    # 同义改写（前 6 条保留部分词面重叠）
    EvalQuery("发布失败时如何撤销", ("kb-010",), "synonym"),
    EvalQuery("备份多久执行一次", ("kb-005",), "synonym"),
    EvalQuery("超限之后返回什么状态", ("kb-007",), "synonym"),
    EvalQuery("哪些端口需要放通", ("kb-012",), "synonym"),
    EvalQuery("权限变更要走什么流程", ("kb-009",), "synonym"),
    EvalQuery("旧版本目录为什么保留", ("kb-014",), "synonym"),
    # 同义改写（后 4 条与目标文档零 token 重合，测语义检索缺口）
    EvalQuery("如何做备份", ("kb-025",), "synonym"),
    EvalQuery("磁盘快满了怎么办", ("kb-026",), "synonym"),
    EvalQuery("登录后老是掉线", ("kb-027",), "synonym"),
    EvalQuery("服务器频繁重启是什么原因", ("kb-034",), "synonym"),
    # 跨段落 / 跨文档
    EvalQuery("部署端口和网络放通端口", ("kb-001", "kb-012"), "cross_paragraph"),
    EvalQuery("备份和日志分别保留多久", ("kb-005", "kb-006"), "cross_paragraph"),
    EvalQuery("升级失败如何回滚", ("kb-014", "kb-010"), "cross_paragraph"),
    EvalQuery("数据库文件和日志目录在哪里", ("kb-008", "kb-006"), "cross_paragraph"),
    # 长文档内相似章节：必须命中“数据库连接超时”这一节的答案标记
    EvalQuery(
        "数据库连接池耗尽导致超时怎么处理",
        ("kb-032",),
        "section",
        answer_marker="pool_size",
    ),
    # 库内无答案：弃答闸必须返回空结果；返回任何内容都算硬凑
    EvalQuery("员工年假怎么申请", (), "unanswerable"),
)

# Measured 2026-11-06 on the deterministic hard set (30 queries, 34 docs).
# Hard by design: 4 zero-overlap paraphrases + 1 unanswerable query are expected
# to fail with the lexical-only hashing embedder; see module docstring.
# Three groups are reported together: answerable recall, unanswerable
# abstention, and false abstention. A single good number would be self-deceiving.
# Any retrieval/gate change MUST re-run ``python -m src.knowledge.evaluation``
# and compare against these numbers; "feels better" is not evidence.
# This dict is **hashing-only**: real semantic models have their own score
# distribution and their own recommended thresholds (see module docstring).
# Measured 2026-11-06 after BM25-weighted fusion: ``mrr`` fell by 0.017
# (0.8667 -> 0.85 mixed, 0.8621 -> 0.8448 answerable) because the lexical
# embedder already ranked the answer first on two queries where BM25 prefers a
# distractor document. Recall, section hit and both abstention groups are
# unchanged, and the real semantic model gained on every group -- do not copy
# that model's numbers onto this baseline.
BASELINE: Dict[str, float] = {
    # legacy mixed averages (kept comparable with earlier commits)
    "recall@5": 0.8667,
    "recall@10": 0.8667,
    "mrr": 0.85,
    "section_hit@5": 1.0,
    # group 1: recall over the 29 answerable queries
    "answerable_recall@5": 0.8621,
    "answerable_recall@10": 0.8621,
    "answerable_mrr": 0.8448,
    # group 2: the 1 unanswerable query must be abstained (was 0.0 before the gate)
    "no_answer_abstained": 1.0,
    # group 3: answerable queries the gate must NOT reject
    "false_abstention_rate": 0.0,
}


# ---------------------------------------------------------------------------
# Deterministic embedder
# ---------------------------------------------------------------------------

class HashingEmbedder:
    """Signed hashing embedder over CJK bigrams / ASCII tokens (no deps)."""

    name = "hashing"
    model = "sha256-bigram-512"

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in tokenize_for_index(text).split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = sum(value * value for value in vector) ** 0.5
            if norm > 0:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


# ---------------------------------------------------------------------------
# Embedding provider selection (real-model re-runs)
# ---------------------------------------------------------------------------

#: Local Ollama defaults; ``nomic-embed-text`` is a real embedding model
#: (``qwen2.5`` and the chat models are NOT -- ``/api/embed`` answers HTTP 501).
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_TAGS_TIMEOUT = 3.0


@dataclass(frozen=True)
class ProviderSpec:
    """A resolved embedder plus the metadata the report prints."""

    key: str
    embedder: object
    model: str
    source: str
    note: Optional[str] = None


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = (os.environ.get(name) or "").strip()
    return value or default


def ollama_base_url(base_url: Optional[str] = None) -> str:
    """Resolve the Ollama base URL (explicit flag > env > localhost default)."""
    return (
        (base_url or "").strip()
        or _env("OLLAMA_BASE_URL")
        or _env("LIUHAO_OLLAMA_BASE_URL")
        or DEFAULT_OLLAMA_BASE_URL
    )


def probe_ollama(
    base_url: Optional[str] = None, timeout: float = OLLAMA_TAGS_TIMEOUT
) -> Optional[List[str]]:
    """Return installed Ollama model names, or ``None`` when unreachable."""
    url = f"{ollama_base_url(base_url).rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return sorted(
        str(item.get("name")) for item in models if isinstance(item, dict)
    )


def _model_present(models: Sequence[str], model: str) -> bool:
    return any(name == model or name.startswith(f"{model}:") for name in models)


def openai_api_key(api_key: Optional[str] = None) -> str:
    return (
        (api_key or "").strip()
        or (_env("LIUHAO_EMBEDDING_API_KEY") or "")
        or (_env("OPENAI_API_KEY") or "")
    )


def build_provider(
    provider: str = "hashing",
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ProviderSpec:
    """Resolve a provider name into a **real** embedder (never a fake one).

    ``hashing`` is the deterministic lexical baseline; ``ollama`` and
    ``openai`` are real HTTP providers; ``auto`` prefers a configured
    OpenAI-compatible key, then a reachable local Ollama. When nothing is
    available this raises :class:`EmbeddingUnavailableError` instead of
    silently substituting a fake embedder.
    """
    key = (provider or "hashing").strip().lower()

    if key in {"hashing", "lexical", "baseline"}:
        return ProviderSpec(
            "hashing", HashingEmbedder(), HashingEmbedder.model, "builtin"
        )

    if key == "ollama":
        base = ollama_base_url(base_url)
        resolved = model or _env("LIUHAO_EMBEDDING_MODEL") or DEFAULT_OLLAMA_MODEL
        return ProviderSpec(
            "ollama",
            OllamaEmbedder(model=resolved, base_url=base),
            resolved,
            "local",
            note=f"{base}/api/embed",
        )

    if key in {"openai", "openai_compatible"}:
        resolved = model or _env("LIUHAO_EMBEDDING_MODEL") or DEFAULT_OPENAI_MODEL
        base = (
            (base_url or "").strip()
            or _env("OPENAI_BASE_URL")
            or DEFAULT_OPENAI_BASE_URL
        )
        secret = openai_api_key(api_key)
        if not secret:
            raise EmbeddingUnavailableError(
                "provider 'openai' 需要 API key：设置 LIUHAO_EMBEDDING_API_KEY "
                "或 OPENAI_API_KEY，或用 --api-key 传入"
            )
        return ProviderSpec(
            "openai",
            OpenAICompatibleEmbedder(
                model=resolved, base_url=base, api_key=secret
            ),
            resolved,
            "env",
            note=f"{base.rstrip('/')}/embeddings",
        )

    if key == "auto":
        if openai_api_key(api_key):
            return build_provider(
                "openai", model=model, base_url=base_url, api_key=api_key
            )
        base = ollama_base_url(base_url)
        if probe_ollama(base) is not None:
            return build_provider("ollama", model=model, base_url=base)
        raise EmbeddingUnavailableError(
            "本机无可用 embedding 源：未配置 OpenAI 兼容服务，且 Ollama "
            f"({base}) 不可达"
        )

    raise EmbeddingUnavailableError(
        f"未知 embedding provider {provider!r}；可选 hashing / ollama / "
        "openai / auto / all"
    )


def detect_providers(base_url: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Probe what this machine can actually use (no fabricated availability)."""
    base = ollama_base_url(base_url)
    models = probe_ollama(base)
    ollama_model = _env("LIUHAO_EMBEDDING_MODEL") or DEFAULT_OLLAMA_MODEL
    key = openai_api_key()
    return {
        "hashing": {
            "available": True,
            "model": HashingEmbedder.model,
            "source": "builtin",
            "semantic": False,
        },
        "ollama": {
            "available": models is not None,
            "model": ollama_model,
            "model_present": bool(models and _model_present(models, ollama_model)),
            "base_url": base,
            "models": models or [],
            "source": "local",
            "semantic": True,
            "reason": (
                None
                if models is not None
                else f"Ollama 不可达：{base}"
            ),
        },
        "openai": {
            "available": bool(key),
            "model": _env("LIUHAO_EMBEDDING_MODEL") or DEFAULT_OPENAI_MODEL,
            "base_url": _env("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL,
            "source": "env",
            "semantic": True,
            "reason": None if key else "未配置 LIUHAO_EMBEDDING_API_KEY / OPENAI_API_KEY",
        },
    }


def _embedder_identity(embedder: object) -> Dict[str, Any]:
    return {
        "name": getattr(embedder, "name", type(embedder).__name__),
        "model": getattr(embedder, "model", None),
        "dimensions": getattr(embedder, "dimensions", None),
        "semantic": not isinstance(embedder, HashingEmbedder),
    }


# ---------------------------------------------------------------------------
# Runner + metrics
# ---------------------------------------------------------------------------

def _ranked_doc_ids(hits: Sequence[Dict]) -> List[str]:
    ranked: List[str] = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        doc_id = metadata.get("document_id") or str(hit.get("doc_id", "")).split("#")[0]
        if doc_id and doc_id not in ranked:
            ranked.append(doc_id)
    return ranked


def _recall_at(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        # 无答案查询：只有“什么都不返回”才算做对；返回内容即硬凑。
        return 1.0 if not ranked[:k] else 0.0
    return len(set(ranked[:k]) & set(relevant)) / len(set(relevant))


def _signals_from_retrieval(retrieval: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the two scores and lexical count the gate actually consumes."""
    top = retrieval.get("top_score")
    runner = retrieval.get("runner_up_score")
    lexical = retrieval.get("lexical_candidates")
    if top is None:
        confidence = retrieval.get("confidence") or {}
        top = confidence.get("top_score")
        runner = confidence.get("runner_up_score")
        if lexical is None:
            lexical = confidence.get("lexical_candidates")
    return {
        "top_score": None if top is None else float(top),
        "runner_up_score": None if runner is None else float(runner),
        "lexical_candidates": int(lexical or 0),
    }


def _replay_gate(raw_row: Dict[str, Any], config: AbstentionConfig) -> Dict[str, Any]:
    """Re-run the *same* gate function over the observed top/runner signals.

    Replaying is what makes a threshold sweep possible without re-embedding:
    ``assess_confidence`` is a pure function of the top score, the runner-up
    score and the lexical-candidate count. Scores are the 4-decimal values the
    retrieval response exposes, so a sweep is exact to that precision.
    """
    similarities: Dict[str, float] = {}
    top = raw_row.get("top_score")
    if top is not None:
        similarities["__top__"] = float(top)
        runner = raw_row.get("runner_up_score")
        if runner is not None:
            similarities["__runner_up__"] = float(runner)
    return assess_confidence(
        similarities,
        int(raw_row.get("lexical_candidates") or 0),
        config=config,
        empty_corpus=False,
        zero_score_allowed=False,
    )


def _derive_row(raw_row: Dict[str, Any], config: AbstentionConfig) -> Dict[str, Any]:
    """Apply a gate config to one raw observation and produce a graded row."""
    verdict = _replay_gate(raw_row, config)
    confident = bool(verdict["confident"])
    relevant = list(raw_row["relevant"])
    ranked = list(raw_row["raw_ranked"]) if confident else []
    first_rank = next(
        (
            rank
            for rank, doc_id in enumerate(ranked, start=1)
            if doc_id in relevant
        ),
        0,
    )
    if not relevant:
        reciprocal_rank = 1.0 if not ranked else 0.0
    else:
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0

    marker_hit: Optional[float] = None
    if raw_row.get("answer_marker"):
        marker_hit = (
            1.0
            if confident and bool(raw_row.get("marker_in_top5"))
            else 0.0
        )
    return {
        "query": raw_row["query"],
        "category": raw_row["category"],
        "relevant": relevant,
        "ranked": ranked,
        "recall@5": _recall_at(ranked, relevant, 5),
        "recall@10": _recall_at(ranked, relevant, 10),
        "reciprocal_rank": reciprocal_rank,
        "marker_hit@5": marker_hit,
        "returned": len(ranked),
        "mode": raw_row.get("mode"),
        "status": STATUS_OK if confident else NO_RELEVANT,
        "abstained": not confident,
        "reason": verdict.get("reason"),
        # Raw (pre-gate) signals; kept so a sweep can replay any threshold set.
        "top_score": raw_row.get("top_score"),
        "runner_up_score": raw_row.get("runner_up_score"),
        "gap": verdict.get("gap"),
        "lexical_candidates": int(raw_row.get("lexical_candidates") or 0),
        "raw_ranked": list(raw_row["raw_ranked"]),
        "raw_returned": len(raw_row["raw_ranked"]),
        "answer_marker": raw_row.get("answer_marker"),
        "marker_in_top5": raw_row.get("marker_in_top5"),
    }


def _summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the three metric groups (recall / abstention / false abst.)."""
    marker_rows = [row for row in rows if row["marker_hit@5"] is not None]
    answerable_rows = [row for row in rows if row["relevant"]]
    no_answer_rows = [row for row in rows if row["category"] == "unanswerable"]

    def _mean(subset: Sequence[Dict[str, Any]], key: str) -> float:
        if not subset:
            return 0.0
        return round(sum(float(row[key]) for row in subset) / len(subset), 4)

    metrics = {
        "queries": len(rows),
        "documents": len(CORPUS),
        # legacy mixed averages (unanswerable counts as 1.0 when abstained)
        "recall@5": _mean(rows, "recall@5"),
        "recall@10": _mean(rows, "recall@10"),
        "mrr": _mean(rows, "reciprocal_rank"),
        "section_hit@5": _mean(marker_rows, "marker_hit@5"),
        # group 1: answerable recall
        "answerable_queries": len(answerable_rows),
        "answerable_recall@5": _mean(answerable_rows, "recall@5"),
        "answerable_recall@10": _mean(answerable_rows, "recall@10"),
        "answerable_mrr": _mean(answerable_rows, "reciprocal_rank"),
        # group 2: unanswerable abstention
        "no_answer_queries": len(no_answer_rows),
        "no_answer_abstained": round(
            sum(
                1.0
                for row in no_answer_rows
                if int(row["returned"]) == 0 and bool(row["abstained"])
            )
            / len(no_answer_rows),
            4,
        )
        if no_answer_rows
        else 1.0,
        # group 3: false abstention (gate wrongly rejected a real answer)
        "false_abstention_count": sum(
            1 for row in answerable_rows if bool(row["abstained"])
        ),
        "false_abstention_rate": round(
            sum(1 for row in answerable_rows if bool(row["abstained"]))
            / len(answerable_rows),
            4,
        )
        if answerable_rows
        else 0.0,
    }

    per_category: Dict[str, Dict[str, object]] = {}
    for category in (
        "fact",
        "identifier",
        "synonym",
        "cross_paragraph",
        "section",
        "unanswerable",
    ):
        subset = [row for row in rows if row["category"] == category]
        if not subset:
            continue
        per_category[category] = {
            "queries": len(subset),
            "recall@5": round(
                sum(float(row["recall@5"]) for row in subset) / len(subset), 4
            ),
            "recall@10": round(
                sum(float(row["recall@10"]) for row in subset) / len(subset), 4
            ),
            "mrr": round(
                sum(float(row["reciprocal_rank"]) for row in subset) / len(subset), 4
            ),
            "abstained": sum(1 for row in subset if bool(row["abstained"])),
        }

    return {"metrics": metrics, "per_category": per_category}


def run_evaluation(
    embedder: object = None,
    config: Optional[AbstentionConfig] = None,
) -> Dict[str, object]:
    """Ingest ``CORPUS`` and score ``QUERIES`` with the given provider.

    Retrieval is executed **ungated** (``abstain=False``) so every row keeps its
    raw ranking and gate signals; the gate verdict is then derived here with
    ``assess_confidence``. That is what lets :func:`sweep_thresholds` re-score
    the same observations under other thresholds without paying for another
    embedding pass. With the default ``HashingEmbedder`` the run is
    deterministic and reproduces ``BASELINE``.
    """
    embedder = embedder if embedder is not None else HashingEmbedder()
    config = config or AbstentionConfig.from_env()
    raw_rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="liuhao-kb-eval-") as workdir:
        store = PersistentVectorStore(str(f"{workdir}/eval.sqlite3"))
        service = KnowledgeDocumentService(vector_store=store, embedder=embedder)
        try:
            for document in CORPUS:
                service.ingest(document.text, doc_id=document.doc_id)

            for query in QUERIES:
                response = service.search(query.query, top_k=10, abstain=False)
                retrieval = response.get("retrieval") or {}
                ranked = _ranked_doc_ids(response["hits"])
                hit_texts = [str(hit.get("text", "")) for hit in response["hits"]]
                marker_in_top5 = (
                    any(query.answer_marker in text for text in hit_texts[:5])
                    if query.answer_marker
                    else None
                )
                raw_rows.append(
                    {
                        "query": query.query,
                        "category": query.category,
                        "relevant": list(query.relevant_doc_ids),
                        "mode": retrieval.get("mode"),
                        "raw_ranked": ranked,
                        "answer_marker": query.answer_marker,
                        "marker_in_top5": marker_in_top5,
                        **_signals_from_retrieval(retrieval),
                    }
                )
        finally:
            store.close()

    rows = [_derive_row(raw, config) for raw in raw_rows]
    summary = _summarize(rows)
    return {
        "embedder": _embedder_identity(embedder),
        "config": {
            "min_top_similarity": config.min_top_similarity,
            "min_top_gap": config.min_top_gap,
            "strong_match_similarity": config.strong_match_similarity,
        },
        **summary,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Threshold sweep (same observations, re-scored under other thresholds)
# ---------------------------------------------------------------------------

# Wide enough to cover both the lexical baseline (0.08) and real semantic
# models whose cosine scores sit far higher. The sweep is cheap (pure replay).
DEFAULT_MIN_TOP_GRID: Tuple[float, ...] = (
    0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,
    0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80,
)
DEFAULT_MIN_GAP_GRID: Tuple[float, ...] = (
    0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25,
)
DEFAULT_STRONG_GRID: Tuple[float, ...] = (0.70, 0.75, 0.80, 0.85, 0.90)

_SWEEP_METRIC_KEYS = (
    "answerable_recall@5",
    "answerable_recall@10",
    "answerable_mrr",
    "no_answer_abstained",
    "false_abstention_count",
    "false_abstention_rate",
)


def _observed_scores(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Surface the raw score ranges so a recommendation is auditable.

    Thresholds are only as trustworthy as the margin between the lowest
    answerable top score and the highest unanswerable one; hiding that margin
    would make a 0.65 floor look safer than it is.
    """
    answerable = sorted(
        float(row["top_score"])
        for row in rows
        if row.get("relevant") and row.get("top_score") is not None
    )
    unanswerable = [
        row
        for row in rows
        if row.get("category") == "unanswerable"
        and row.get("top_score") is not None
    ]
    observed: Dict[str, Any] = {
        "answerable_top_min": answerable[0] if answerable else None,
        "answerable_top_median": (
            answerable[len(answerable) // 2] if answerable else None
        ),
        "unanswerable_top_max": (
            max(float(row["top_score"]) for row in unanswerable)
            if unanswerable
            else None
        ),
        "unanswerable_lexical_max": (
            max(int(row.get("lexical_candidates") or 0) for row in unanswerable)
            if unanswerable
            else None
        ),
    }
    return observed


def _sweep_sort_key(metrics: Dict[str, float], thresholds: Dict[str, float]):
    """Abstention first, then lowest false abstention, then highest recall.

    Tie-breaks avoid gratuitous change: a *higher* absolute floor is safer (it
    is the only rule that rejects high-scoring junk), while the gap and
    strong-match thresholds stay as close as possible to the documented
    lexical defaults (``0.05`` / ``0.85``) when the data cannot tell two
    candidates apart -- an unnecessarily large gap rejects valid answers whose
    neighbouring chunk is nearly identical.
    """
    return (
        1 if metrics["no_answer_abstained"] >= 1.0 else 0,
        -metrics["false_abstention_rate"],
        metrics["answerable_recall@5"],
        metrics["answerable_recall@10"],
        metrics["answerable_mrr"],
        thresholds["min_top_similarity"],
        -abs(thresholds["min_top_gap"] - 0.05),
        -abs(thresholds["strong_match_similarity"] - 0.85),
    )


def sweep_thresholds(
    rows: Sequence[Dict[str, Any]],
    *,
    min_top_values: Sequence[float] = DEFAULT_MIN_TOP_GRID,
    min_gap_values: Sequence[float] = DEFAULT_MIN_GAP_GRID,
    strong_match_values: Sequence[float] = DEFAULT_STRONG_GRID,
    calibrated_for: Optional[str] = None,
) -> Dict[str, object]:
    """Grid-search gate thresholds on already-collected observations.

    ``rows`` must be ``run_evaluation`` rows (they carry raw rankings and gate
    signals). The objective is exactly the one the product promises: the
    unanswerable query must stay abstained; among configurations that achieve
    that, keep false abstentions lowest, then answerable recall highest.
    """
    entries: List[Dict[str, Any]] = []
    for min_top in min_top_values:
        for min_gap in min_gap_values:
            for strong in strong_match_values:
                if strong < min_top:
                    continue
                config = AbstentionConfig(
                    min_top_similarity=min_top,
                    min_top_gap=min_gap,
                    strong_match_similarity=strong,
                )
                metrics = _summarize(
                    [_derive_row(row, config) for row in rows]
                )["metrics"]
                thresholds = {
                    "min_top_similarity": min_top,
                    "min_top_gap": min_gap,
                    "strong_match_similarity": strong,
                }
                entries.append(
                    {
                        "thresholds": thresholds,
                        "metrics": {
                            key: metrics[key] for key in _SWEEP_METRIC_KEYS
                        },
                    }
                )

    if not entries:  # pragma: no cover - grids are constants, not empty
        return {
            "objective": "",
            "evaluated": 0,
            "recommended": None,
            "frontier": [],
        }

    def key(entry: Dict[str, Any]):
        return _sweep_sort_key(entry["metrics"], entry["thresholds"])

    ranked = sorted(entries, key=key, reverse=True)
    best = ranked[0]
    best_thresholds = best["thresholds"]
    env = {
        ENV_MIN_SIMILARITY: f"{best_thresholds['min_top_similarity']:.2f}",
        ENV_MIN_GAP: f"{best_thresholds['min_top_gap']:.2f}",
        ENV_STRONG_MATCH: f"{best_thresholds['strong_match_similarity']:.2f}",
    }
    # The numeric values alone would be applied to any embedder that happens to
    # be configured next; the declaration binds them to the model they were
    # measured on (a mismatching declaration stays uncalibrated and lenient).
    if calibrated_for:
        env[ENV_CALIBRATED_FOR] = calibrated_for
    recommended = {
        "thresholds": dict(best_thresholds),
        "env": env,
        "metrics": dict(best["metrics"]),
        "holds_abstention": best["metrics"]["no_answer_abstained"] >= 1.0,
        "observed_scores": _observed_scores(rows),
    }
    return {
        "objective": (
            "无答案弃答率必须保持 1.0；在误弃答最少的前提下最大化有答案召回"
        ),
        "evaluated": len(entries),
        "recommended": recommended,
        "frontier": ranked[:5],
    }


# ---------------------------------------------------------------------------
# Multi-provider comparison
# ---------------------------------------------------------------------------

def _format_metric(value: Optional[float]) -> str:
    return "-" if value is None else f"{float(value):.4f}"


def comparison_table(rows: Sequence[Dict[str, Any]]) -> str:
    """Compact provider comparison (English headers: no CJK width guessing)."""
    header = (
        f"{'provider':<9} {'model':<22} {'recall@5':>9} {'recall@10':>9} "
        f"{'abstain':>8} {'FA':>6} {'min_top':>8} {'min_gap':>8} "
        f"{'strong':>7} {'status':>10}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        metrics = row.get("metrics") or {}
        recommended = row.get("recommended") or {}
        thresholds = recommended.get("thresholds") or {}
        lines.append(
            f"{str(row.get('provider') or '?'):<9} "
            f"{str(row.get('model') or '-'):<22} "
            f"{_format_metric(metrics.get('answerable_recall@5')):>9} "
            f"{_format_metric(metrics.get('answerable_recall@10')):>9} "
            f"{_format_metric(metrics.get('no_answer_abstained')):>8} "
            f"{_format_metric(metrics.get('false_abstention_rate')):>6} "
            f"{_format_metric(thresholds.get('min_top_similarity')):>8} "
            f"{_format_metric(thresholds.get('min_top_gap')):>8} "
            f"{_format_metric(thresholds.get('strong_match_similarity')):>7} "
            f"{str(row.get('status') or '?'):>10}"
        )
    return "\n".join(lines)


def _expand_providers(
    providers: Sequence[str], detection: Dict[str, Dict[str, Any]]
) -> List[str]:
    names: List[str] = []
    for raw in providers:
        for part in str(raw).split(","):
            name = part.strip().lower()
            if not name:
                continue
            if name == "all":
                candidates = ["hashing"] + [
                    key
                    for key in ("ollama", "openai")
                    if detection.get(key, {}).get("available")
                ]
            else:
                candidates = [name]
            for candidate in candidates:
                if candidate not in names:
                    names.append(candidate)
    return names or ["hashing"]


def compare_providers(
    providers: Sequence[str] = ("hashing",),
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    config: Optional[AbstentionConfig] = None,
) -> Dict[str, object]:
    """Run the same 34-doc / 30-query set per provider and compare them.

    A provider that cannot be built or reached is reported as
    ``unavailable`` / ``endpoint_failed`` with its reason; it is never replaced
    by a fake embedder and never contributes fabricated numbers.
    """
    config = config or AbstentionConfig.from_env()
    detection = detect_providers(base_url)
    rows: List[Dict[str, Any]] = []
    for name in _expand_providers(providers, detection):
        try:
            spec = build_provider(
                name, model=model, base_url=base_url, api_key=api_key
            )
        except EmbeddingUnavailableError as exc:
            rows.append(
                {"provider": name, "status": "unavailable", "reason": str(exc)}
            )
            continue

        started = time.perf_counter()
        try:
            run = run_evaluation(embedder=spec.embedder, config=config)
        except EmbeddingUnavailableError as exc:
            rows.append(
                {
                    "provider": spec.key,
                    "model": spec.model,
                    "status": "endpoint_failed",
                    "reason": str(exc),
                }
            )
            continue

        embedder_info = run.get("embedder") or {}
        sweep = sweep_thresholds(
            run["rows"],
            calibrated_for=embedder_identity(
                embedder_info.get("name"), embedder_info.get("model")
            ),
        )
        rows.append(
            {
                "provider": spec.key,
                "status": "ok",
                "source": spec.source,
                "endpoint": spec.note,
                "model": spec.model,
                "embedder": run["embedder"],
                "baseline_applicable": spec.key == "hashing",
                "current_thresholds": run["config"],
                "metrics": run["metrics"],
                "per_category": run["per_category"],
                "recommended": sweep["recommended"],
                "sweep": {
                    "objective": sweep["objective"],
                    "evaluated": sweep["evaluated"],
                    "frontier": sweep["frontier"],
                },
                "elapsed_seconds": round(time.perf_counter() - started, 2),
            }
        )

    openai_ready = bool(detection.get("openai", {}).get("available"))
    ollama_ready = bool(
        detection.get("ollama", {}).get("available")
        and detection.get("ollama", {}).get("model_present")
    )
    real_provider_available = openai_ready or ollama_ready
    real_model_verified = any(
        row.get("status") == "ok"
        and (row.get("embedder") or {}).get("semantic")
        and bool((row.get("recommended") or {}).get("holds_abstention"))
        for row in rows
    )
    if not real_provider_available:
        note = (
            "本机无可用 embedding 源，真实模型下阈值未验证；"
            "当前阈值仅适用于词面嵌入（hashing embedder）。"
        )
    elif not real_model_verified:
        note = (
            "检测到真实 embedding 源，但本次未成功在其上完成评测；"
            "真实模型下阈值仍未验证，请查看 providers 里的失败原因。"
        )
    else:
        note = (
            "真实 embedding 源已完成同一套 34 篇 / 30 条查询评测；"
            "建议阈值见各 provider 的 recommended 字段。"
        )

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "detected": detection,
        "providers": rows,
        "comparison_table": comparison_table(rows),
        "real_provider_available": real_provider_available,
        "real_model_verified": real_model_verified,
        "real_model_note": note,
        "baseline_note": (
            "BASELINE 数字仅对 hashing（词面）嵌入有效，"
            "不得用于真实语义模型。"
        ),
    }


def baseline_report(
    result: Dict[str, object] | None = None,
    config: Optional[AbstentionConfig] = None,
) -> Dict[str, object]:
    """Metrics plus the stored baseline and a pass/fail regression delta."""
    run = result or run_evaluation(config=config)
    metrics = dict(run["metrics"])  # type: ignore[arg-type]
    applicable = (run.get("embedder") or {}).get("name") == "hashing"
    delta = {
        key: round(float(metrics[key]) - BASELINE[key], 4)
        for key in BASELINE
    }
    embedder_info = run.get("embedder") or {}
    sweep = sweep_thresholds(  # type: ignore[arg-type]
        run["rows"],
        calibrated_for=embedder_identity(
            embedder_info.get("name"), embedder_info.get("model")
        ),
    )
    return {
        "embedder": run["embedder"],
        "metrics": metrics,
        "baseline": dict(BASELINE),
        "delta": delta,
        "regression": (
            any(value < 0 for value in delta.values()) if applicable else None
        ),
        "baseline_applicable": applicable,
        "current_thresholds": run["config"],
        "recommended_thresholds": sweep["recommended"],
        "per_category": run["per_category"],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.knowledge.evaluation",
        description=(
            "在指定 embedding provider 上重跑 34 篇 / 30 条评测集，"
            "输出三组数、阈值建议与对比表。"
        ),
    )
    parser.add_argument(
        "--provider",
        help=(
            "hashing / ollama / openai / auto / all，可用逗号分隔多个；"
            "不指定时只跑 hashing 基线"
        ),
    )
    parser.add_argument("--model", help="覆盖 embedding 模型名")
    parser.add_argument("--base-url", help="覆盖 provider base URL")
    parser.add_argument("--api-key", help="OpenAI 兼容服务的 API key")
    parser.add_argument(
        "--min-top-similarity", type=float, help="只改本次报告的绝对下限"
    )
    parser.add_argument("--min-gap", type=float, help="只改本次报告的分数差下限")
    parser.add_argument(
        "--strong-match", type=float, help="只改本次报告的强匹配阈值"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    config = None
    if any(
        value is not None
        for value in (args.min_top_similarity, args.min_gap, args.strong_match)
    ):
        current = AbstentionConfig.from_env()
        config = AbstentionConfig(
            min_top_similarity=(
                args.min_top_similarity
                if args.min_top_similarity is not None
                else current.min_top_similarity
            ),
            min_top_gap=(
                args.min_gap
                if args.min_gap is not None
                else current.min_top_gap
            ),
            strong_match_similarity=(
                args.strong_match
                if args.strong_match is not None
                else current.strong_match_similarity
            ),
        )

    if not args.provider:
        print(
            json.dumps(
                baseline_report(config=config), ensure_ascii=False, indent=2
            )
        )
        return

    report = compare_providers(
        [args.provider],
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        config=config,
    )
    print(report["comparison_table"], file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
