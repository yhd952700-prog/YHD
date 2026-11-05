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

The embedder is a dependency-free hashing embedder (signed SHA-256 hashing of
the same CJK-bigram/ASCII tokens the FTS side uses). It is *not* a semantic
model: zero-overlap synonym queries are expected to fail. **This baseline
therefore covers lexical retrieval only; real semantic-model numbers are
unmeasured** until a hosted provider is wired in and the set is re-run.
The numbers below must be re-measured in the same change that touches
retrieval; never report them as a semantic-quality claim.

Chinese tokenization limitations (bigram, no segmentation/synonyms) also apply
here; see ``vector_store`` module documentation.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .documents import KnowledgeDocumentService
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
        "# 部署与安装\n\n鎏灏控制台通过局域网部署，默认端口 8700，"
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
        "# 机器要求\n\n最低配置 4 核 8GB 内存，操作系统 Windows 10/11，Python 3.11。\n\n"
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
BASELINE: Dict[str, float] = {
    # legacy mixed averages (kept comparable with earlier commits)
    "recall@5": 0.8667,
    "recall@10": 0.8667,
    "mrr": 0.8667,
    "section_hit@5": 1.0,
    # group 1: recall over the 29 answerable queries
    "answerable_recall@5": 0.8621,
    "answerable_recall@10": 0.8621,
    "answerable_mrr": 0.8621,
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


def run_evaluation() -> Dict[str, object]:
    """Ingest ``CORPUS`` and score ``QUERIES``; deterministic by construction."""
    with tempfile.TemporaryDirectory(prefix="liuhao-kb-eval-") as workdir:
        store = PersistentVectorStore(str(f"{workdir}/eval.sqlite3"))
        service = KnowledgeDocumentService(
            vector_store=store, embedder=HashingEmbedder()
        )
        try:
            for document in CORPUS:
                service.ingest(document.text, doc_id=document.doc_id)

            rows: List[Dict[str, object]] = []
            for query in QUERIES:
                response = service.search(query.query, top_k=10)
                retrieval = response.get("retrieval") or {}
                ranked = _ranked_doc_ids(response["hits"])
                hit_texts = [str(hit.get("text", "")) for hit in response["hits"]]
                first_rank = next(
                    (
                        rank
                        for rank, doc_id in enumerate(ranked, start=1)
                        if doc_id in query.relevant_doc_ids
                    ),
                    0,
                )
                if not query.relevant_doc_ids:
                    # 无答案：返回任何 chunk 都算硬凑
                    reciprocal_rank = 1.0 if not ranked else 0.0
                else:
                    reciprocal_rank = 1.0 / first_rank if first_rank else 0.0

                marker_hit = None
                if query.answer_marker:
                    marker_hit = (
                        1.0
                        if any(
                            query.answer_marker in text
                            for text in hit_texts[:5]
                        )
                        else 0.0
                    )
                rows.append(
                    {
                        "query": query.query,
                        "category": query.category,
                        "relevant": list(query.relevant_doc_ids),
                        "ranked": ranked,
                        "recall@5": _recall_at(ranked, query.relevant_doc_ids, 5),
                        "recall@10": _recall_at(ranked, query.relevant_doc_ids, 10),
                        "reciprocal_rank": reciprocal_rank,
                        "marker_hit@5": marker_hit,
                        "returned": len(hit_texts),
                        "mode": retrieval.get("mode"),
                        "status": retrieval.get("status", "ok"),
                        "abstained": bool(retrieval.get("abstained", False)),
                    }
                )
        finally:
            store.close()

    marker_rows = [row for row in rows if row["marker_hit@5"] is not None]
    answerable_rows = [row for row in rows if row["relevant"]]
    no_answer_rows = [row for row in rows if row["category"] == "unanswerable"]

    def _mean(subset: List[Dict[str, object]], key: str) -> float:
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

    return {"metrics": metrics, "per_category": per_category, "rows": rows}


def baseline_report(result: Dict[str, object] | None = None) -> Dict[str, object]:
    """Metrics plus the stored baseline and a pass/fail regression delta."""
    run = result or run_evaluation()
    metrics = dict(run["metrics"])  # type: ignore[arg-type]
    delta = {
        key: round(float(metrics[key]) - BASELINE[key], 4)
        for key in BASELINE
    }
    return {
        "metrics": metrics,
        "baseline": dict(BASELINE),
        "delta": delta,
        "regression": any(value < 0 for value in delta.values()),
        "per_category": run["per_category"],
    }


def main() -> None:
    print(json.dumps(baseline_report(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
