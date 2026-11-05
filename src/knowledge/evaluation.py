"""Reproducible retrieval evaluation for the knowledge base (baseline guard).

This module is deliberately self-contained and deterministic so *anyone* can
re-run the same numbers before and after a retrieval change::

    python -m src.knowledge.evaluation

It builds a small in-memory corpus in a temporary SQLite file, ingests it
through the real :class:`KnowledgeDocumentService` (structured chunking + FTS5
hybrid retrieval), and reports ``recall@5``, ``recall@10`` and ``MRR``.

The embedder is a dependency-free hashing embedder (signed SHA-256 hashing of
the same CJK-bigram/ASCII tokens the FTS side uses). It is *not* a semantic
model: synonym-heavy queries only score when they share surface tokens. That is
intentional -- the baseline measures the retrieval plumbing (chunking, hybrid
fusion, ranking), not a particular hosted embedding model. When a real provider
is wired in, the baseline must be re-measured and ``BASELINE`` updated in the
same change.

Chinese tokenization limitations (bigram, no segmentation/synonyms) also apply
here; see ``vector_store`` module documentation.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

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
)

QUERIES: Tuple[EvalQuery, ...] = (
    # 事实查询
    EvalQuery("默认端口是多少", ("kb-001",), "fact"),
    EvalQuery("访问令牌过期时间", ("kb-002",), "fact"),
    EvalQuery("每日备份几点执行", ("kb-005",), "fact"),
    EvalQuery("日志放在哪个目录", ("kb-006",), "fact"),
    EvalQuery("最低硬件配置要求", ("kb-011",), "fact"),
    EvalQuery("告警通过什么方式推送", ("kb-013",), "fact"),
    EvalQuery("升级前需要做什么", ("kb-014",), "fact"),
    EvalQuery("密钥应该存放在哪里", ("kb-015",), "fact"),
    # 编号 / 型号 / 标识符查询
    EvalQuery("XG-9000", ("kb-003",), "identifier"),
    EvalQuery("TICKET-8842 怎么处理", ("kb-004",), "identifier"),
    EvalQuery("返回 429 是什么含义", ("kb-007",), "identifier"),
    EvalQuery("knowledge_vectors.sqlite3 文件位置", ("kb-008",), "identifier"),
    EvalQuery("admin 和 auditor 的权限区别", ("kb-009",), "identifier"),
    EvalQuery("rollback.sh 的作用", ("kb-010",), "identifier"),
    EvalQuery("8700 端口属于哪个服务", ("kb-001", "kb-012"), "identifier"),
    # 同义改写（保留部分词面重叠，不含分词/同义词库）
    EvalQuery("发布失败时如何撤销", ("kb-010",), "synonym"),
    EvalQuery("备份多久执行一次", ("kb-005",), "synonym"),
    EvalQuery("超限之后返回什么状态", ("kb-007",), "synonym"),
    EvalQuery("哪些端口需要放通", ("kb-012",), "synonym"),
    EvalQuery("权限变更要走什么流程", ("kb-009",), "synonym"),
    EvalQuery("旧版本目录为什么保留", ("kb-014",), "synonym"),
    # 跨段落 / 跨文档
    EvalQuery("部署端口和网络放通端口", ("kb-001", "kb-012"), "cross_paragraph"),
    EvalQuery("备份和日志分别保留多久", ("kb-005", "kb-006"), "cross_paragraph"),
    EvalQuery("升级失败如何回滚", ("kb-014", "kb-010"), "cross_paragraph"),
    EvalQuery("数据库文件和日志目录在哪里", ("kb-008", "kb-006"), "cross_paragraph"),
)

# Measured 2026-11-06 on the deterministic corpus below (25 queries, 24 docs).
# Any retrieval change MUST re-run ``python -m src.knowledge.evaluation`` and
# compare against these numbers; "feels better" is not evidence.
BASELINE: Dict[str, float] = {
    "recall@5": 1.0,
    "recall@10": 1.0,
    "mrr": 1.0,
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
        return 0.0
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
                ranked = _ranked_doc_ids(response["hits"])
                first_rank = next(
                    (
                        rank
                        for rank, doc_id in enumerate(ranked, start=1)
                        if doc_id in query.relevant_doc_ids
                    ),
                    0,
                )
                rows.append(
                    {
                        "query": query.query,
                        "category": query.category,
                        "relevant": list(query.relevant_doc_ids),
                        "ranked": ranked,
                        "recall@5": _recall_at(ranked, query.relevant_doc_ids, 5),
                        "recall@10": _recall_at(ranked, query.relevant_doc_ids, 10),
                        "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
                        "mode": response.get("retrieval", {}).get("mode"),
                    }
                )
        finally:
            store.close()

    metrics = {
        "queries": len(rows),
        "documents": len(CORPUS),
        "recall@5": round(sum(float(row["recall@5"]) for row in rows) / len(rows), 4),
        "recall@10": round(sum(float(row["recall@10"]) for row in rows) / len(rows), 4),
        "mrr": round(sum(float(row["reciprocal_rank"]) for row in rows) / len(rows), 4),
    }

    per_category: Dict[str, Dict[str, float]] = {}
    for category in ("fact", "identifier", "synonym", "cross_paragraph"):
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
        }

    return {"metrics": metrics, "per_category": per_category, "rows": rows}


def baseline_report(result: Dict[str, object] | None = None) -> Dict[str, object]:
    """Metrics plus the stored baseline and a pass/fail regression delta."""
    run = result or run_evaluation()
    metrics = dict(run["metrics"])  # type: ignore[arg-type]
    delta = {
        key: round(float(metrics[key]) - BASELINE[key], 4)
        for key in ("recall@5", "recall@10", "mrr")
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
