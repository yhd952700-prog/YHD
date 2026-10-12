# 记忆管理最佳实践

记忆系统是智能体的长期竞争力所在——也是被滥用最多的组件。写入不设门禁的记忆库三个月就会变成噪声垃圾场，检索质量随之崩塌。本文档覆盖四层记忆模型的使用规范、写入门禁、检索调优、遗忘策略与隐私合规。

---

## 目录

- [四层记忆模型回顾](#四层记忆模型回顾)
- [写入门禁：什么值得记](#写入门禁什么值得记)
- [记忆结构设计](#记忆结构设计)
- [检索调优](#检索调优)
- [遗忘与生命周期](#遗忘与生命周期)
- [多智能体记忆共享](#多智能体记忆共享)
- [隐私与合规](#隐私与合规)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 四层记忆模型回顾

| 层 | 类比 | 内容 | 生命周期 | 检索方式 |
|----|------|------|---------|---------|
| 工作记忆 Working | 桌面 | 当前任务上下文 | 任务结束即清 | 直接读 |
| 情景记忆 Episodic | 日记 | 任务轨迹、事件序列 | 天~周 | 时间/任务索引 |
| 语义记忆 Semantic | 百科全书 | 提炼的事实与知识 | 月~年 | 向量检索 |
| 程序记忆 Procedural | 肌肉记忆 | 成功的操作序列/技能 | 永久 | 按触发条件匹配 |

**最常见的错误**：把所有内容都塞进语义记忆。正确的流动方向是：

```
工作记忆 ──(任务结束)──▶ 情景记忆 ──(提炼)──▶ 语义记忆
                              └──(成功模式)──▶ 程序记忆
```

---

## 写入门禁：什么值得记

### 价值密度检验

写入语义记忆前过四问：

```python
def worth_remembering(candidate: MemoryCandidate) -> tuple[bool, str]:
    # 1. 未来还会用到吗？（复用性）
    if candidate.one_off:                        # 一次性任务的中间产物
        return False, "one_off"
    # 2. 是事实而非对话碎片吗？
    if not candidate.is_atomic_fact:
        return False, "not_atomic"               # "好的" "谢谢" 类碎片
    # 3. 能从原始来源重新获取吗？（可再生性）
    if candidate.recomputable:                   # 重新查一次更准
        return False, "recomputable"
    # 4. 置信度够吗？
    if candidate.confidence < 0.7:
        return False, "low_confidence"
    return True, "ok"
```

### 提炼而非搬运

```python
# ❌ 情景→语义：直接搬运对话
semantic.store("用户问了密码重置流程，我回答了步骤，用户说谢谢")

# ✅ 提炼为可复用事实
semantic.store(
    subject="password_reset",
    content="本系统的密码重置流程：设置→安全→重置链接，链接有效期 30 分钟",
    source="conversation:conv_123",
    confidence=0.95,
    valid_until=None,                            # 流程变更时需更新
)
```

### 去重与冲突处理

```python
async def store_with_dedup(fact: Fact) -> StoreResult:
    similar = await semantic.search(fact.content, top_k=3)
    for existing in similar:
        if existing.similarity > 0.92:
            relation = await judge_relation(existing, fact)
            if relation == "duplicate":
                await semantic.touch(existing.id)      # 刷新新鲜度
                return StoreResult.MERGED
            if relation == "contradiction":
                # 冲突不覆盖：标记竞争关系，新事实先低置信度并存
                await semantic.mark_conflict(existing.id, fact)
                return StoreResult.CONFLICT_FLAGGED
            if relation == "supersede":
                await semantic.archive(existing.id)    # 旧版归档可查
                return StoreResult.SUPERSEDED
    return await semantic.store(fact)
```

---

## 记忆结构设计

### 语义记忆的字段规范

```python
@dataclass
class SemanticMemory:
    id: str
    subject: str                    # 主题键：检索与更新的锚点
    content: str                    # 原子事实，一句到三句话
    source: str                     # 来源：conversation:x / doc:y / human
    confidence: float               # 0~1，检索排序因子
    trust: str                      # system|user|external（防投毒）
    purpose: str                    # GDPR 目的标签
    created_at: float
    last_confirmed_at: float        # 新鲜度：越久未确认权重越低
    valid_until: float | None       # 时效性事实（价格、版本）
    access_count: int = 0           # 热度的被动信号
```

关键设计：

- **原子性**：一条记忆一个事实。复合事实检索匹配差、更新互相污染
- **subject 锚定**：同一主题的新事实通过 subject 找到旧版做 supersede
- **trust 分级**：`external` 来源（网页、文档）默认不进高信任检索结果

### 情景记忆的轨迹结构

```python
@dataclass
class Episode:
    task_id: str
    goal: str
    steps: list[Step]               # 动作 + 结果摘要（非完整日志）
    outcome: Literal["success", "failure", "partial"]
    lessons: list[str]              # 任务结束时的自评总结
    cost: dict                      # tokens、时长、工具调用数
```

`lessons` 字段是黄金：任务结束时让智能体自评"下次遇到类似任务应该注意什么"，这是后续检索复用时价值最高的部分。

---

## 检索调优

### 混合检索（向量 + 关键词 + 元数据）

```python
async def retrieve(query: str, agent_id: str, top_k: int = 8) -> list[Memory]:
    # 三路并行
    vec_results, kw_results, recent = await asyncio.gather(
        vector_search(query, agent_id, top_k=top_k * 2),
        keyword_search(query, agent_id, top_k=top_k * 2),   # BM25/倒排
        recent_high_value(agent_id, limit=5),                # 近期高频
    )
    # RRF 融合排序
    fused = reciprocal_rank_fusion([vec_results, kw_results], k=60)
    # 重排序：新鲜度 × 置信度 × 相关性
    reranked = sorted(fused, key=lambda m: (
        m.relevance * 0.6
        + freshness_score(m.last_confirmed_at) * 0.25
        + m.confidence * 0.15), reverse=True)
    return reranked[:top_k]
```

### 上下文预算分配

检索结果不是越多越好（见[性能调优](../knowledge_base/23-performance/performance-tuning.md)）：检索片段占上下文预算的 **30~40%** 是甜点区，再多会稀释指令遵循。

### 检索质量指标

| 指标 | 计算 | 目标 |
|------|------|------|
| 命中率 | 检索结果被最终回答引用的比例 | > 60% |
| 污染率 | 被引用但导致错误的比例 | < 5% |
| 空检索率 | 检索无结果的任务比例 | 监控趋势 |
| 延迟 P99 | 混合检索端到端 | < 200ms |

命中率低 = 检索策略问题；污染率高 = 记忆质量问题。两个指标要分开看。

---

## 遗忘与生命周期

遗忘不是缺陷，是记忆系统的核心功能。

### 分层生命周期策略

```python
async def lifecycle_maintenance():
    # 1. 工作记忆：任务结束 TTL 到期自动清
    # 2. 情景记忆：30 天后压缩为摘要，90 天后归档
    await episodic.compress_older_than(days=30, keep_lessons=True)
    await episodic.archive_older_than(days=90)
    # 3. 语义记忆：衰减 + 时效清理
    await semantic.expire_where("valid_until < now()")
    # 长期未确认且低访问的记忆降权（不删，可查）
    await semantic.decay_where(
        "last_confirmed_at < now() - interval '180 days' AND access_count < 3")
    # 4. 冲突巡检：每周抽查语义记忆矛盾对
    await weekly_conflict_review(sample_size=50)
```

### 主动确认（Re-confirmation）

高价值记忆需要定期"体检"：

```python
# 访问时顺带确认：被引用的记忆刷新 last_confirmed_at
async def on_memory_cited(memory_id: str):
    await semantic.touch(memory_id)

# 时效性记忆到期前验证：重新查原始来源
async def revalidate_expiring():
    for mem in await semantic.expiring_within(days=7):
        fresh = await fetch_from_source(mem.source)
        if fresh.matches(mem):
            await semantic.extend_validity(mem.id)
        else:
            await semantic.mark_stale(mem.id)   # 标记待人工/智能体复审
```

---

## 多智能体记忆共享

共享记忆是协作的基础，也是污染的通道。治理模型：

| 空间 | 写入 | 读取 | 用途 |
|------|------|------|------|
| `agent:{id}` 私有 | 仅该智能体 | 仅该智能体 | 默认 |
| `team:{name}` 团队 | 团队成员 + 写入门禁 | 团队成员 | 共享知识 |
| `org:canonical` 权威库 | 仅人工/治理流程 | 全体（高 trust） | 标准答案、政策 |

规则：

1. **默认私有**：跨空间共享是显式动作（`promote(memory_id, to="team:x")`）
2. **共享提升带评审**：团队空间的新记忆 24h 内可被成员挑战（challenge）
3. **权威库只增不改**：变更走 supersede 流程，历史可查

---

## 隐私与合规

- **PII 检测前置**：含 PII 的记忆写入时打 `pii_class` 标签，参与 GDPR 删除流水线（见 [GDPR 合规](../knowledge_base/24-security/gdpr-compliance-guide.md)）
- **purpose 标签强制**：profiling 目的的记忆可一键全删（反对权）
- **跨租户物理隔离**：向量索引按 tenant 分区，检索强制过滤
- **删除要级联**：用户删除 → 四层记忆 + 向量索引 + 缓存全清，审计留存哈希证据

---

## 配置示例

```yaml
# config/memory.yaml
memory:
  write_gate:
    min_confidence: 0.7
    reject_one_off: true
    reject_recomputable: true
    dedup_similarity: 0.92
    conflict_policy: coexist_flagged

  retrieval:
    hybrid:
      vector_weight: 0.6
      keyword_weight: 0.4
      rrf_k: 60
    rerank:
      relevance: 0.60
      freshness: 0.25
      confidence: 0.15
    context_budget_ratio: 0.35
    default_top_k: 8

  lifecycle:
    episodic_compress_days: 30
    episodic_archive_days: 90
    semantic_decay_days: 180
    semantic_decay_min_access: 3
    conflict_review_weekly: true

  sharing:
    default_scope: agent_private
    team_promote_challenge_hours: 24

  privacy:
    pii_scan_on_write: true
    purpose_required: true
```

---

## 最佳实践

1. **写入门禁是第一优先级**：宁缺毋滥；噪声记忆的伤害大于缺失记忆。
2. **每条记忆带来源与置信度**：没有来源的记忆在冲突时无法裁决。
3. **检索质量持续度量**：命中率/污染率上仪表盘，月度评审。
4. **时效性事实必须带 valid_until**：价格、版本、人员名单类事实没有保质期就是定时炸弹。
5. **冲突并存优于覆盖**：让下游看到分歧，比 silently 选一个更诚实。
6. **lessons 自评强制化**：任务结束花 2% 成本写教训，长期回报巨大。

---

## 常见问题

### Q1: 语义记忆检索总是返回不相关的旧知识？

两个典型原因：缺少新鲜度排序（旧记忆积累高向量相似度）——加 `freshness` 权重；或向量模型与领域不匹配——换领域微调 embedding 或加关键词混合检索纠偏。

### Q2: 记忆越来越多，检索变慢怎么办？

分层索引：热记忆（90 天内有访问）进 HNSW 主索引；冷记忆归档到独立索引，仅深度查询时检索；按 tenant/agent 分区裁剪搜索空间。

### Q3: 智能体把错误结论写进了记忆，后续任务都被带偏？

程序上：写入门禁要求 source + confidence；救济上：`semantic.mark_stale` + 冲突巡检；预防上：高影响决策检索时要求 ≥2 独立来源印证。

### Q4: 工作记忆（上下文）满了怎么办？

优先级淘汰：系统提示（永不淘汰）→ 最近工具结果 → 检索片段 → 旧对话（最旧先淘汰，淘汰前摘要进工作记忆尾部）。永远不要静默截断开头——系统提示丢失会让行为瞬间漂移。

### Q5: 不同智能体对同一事实的记忆不一致怎么办？

这正是团队空间与权威库要解决的。智能体私有记忆允许分歧（视角不同）；对外作答时以 `org:canonical` 为准。定期对账任务比对团队空间中的冲突 subject。

---

**相关文档**：[智能体设计模式](agent-design-patterns.md) · [多智能体协作](multi-agent-coordination.md) · [GDPR 合规](../knowledge_base/24-security/gdpr-compliance-guide.md) · [记忆用户指南](../knowledge_base/user-guide/memory_guide.md)
