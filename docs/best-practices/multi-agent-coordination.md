# 多智能体协作

多智能体系统（MAS）把复杂任务分给多个专业化智能体协作完成。它的收益真实（并行、专业化、对抗验证），代价也真实（协调开销、上下文失真、成本翻倍）。本文档提供协作模式选型、通信协议、冲突解决与成本控制的最佳实践。

---

## 目录

- [何时该用多智能体](#何时该用多智能体)
- [五种协作模式](#五种协作模式)
- [通信协议设计](#通信协议设计)
- [任务分解与结果合并](#任务分解与结果合并)
- [冲突解决](#冲突解决)
- [成本控制](#成本控制)
- [Python 实现示例](#python-实现示例)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 何时该用多智能体

**用多智能体的正当理由：**

| 场景 | 单智能体的痛点 |
|------|---------------|
| 并行探索多个方向（市场调研、方案对比） | 串行执行慢，且方向间互相污染上下文 |
| 生成者 + 批评者对抗（代码审查、红队测试） | 自我批评质量差（已证实的 LLM 弱点） |
| 领域/权限隔离（财务智能体 + 法务智能体） | 单一智能体权限过大违反最小权限 |
| 工具集差异巨大（浏览器操作 + 数据分析） | 工具过多选择准确率下降 |

**不要用多智能体的场景：**

- 任务本质串行（步骤间强依赖）——协作开销纯浪费
- 单智能体加长上下文就能解决——先试这个
- 为了"看起来先进"——多智能体调试难度是单体的 N²

---

## 五种协作模式

### 1. 主管-工人（Supervisor-Worker）

```
        ┌────────────┐
        │ Supervisor │  分解任务、分派、合并、裁决
        └─────┬──────┘
      ┌───────┼────────┐
      ▼       ▼        ▼
   Worker1 Worker2  Worker3   （各自独立上下文与记忆）
```

适用：任务可分解为弱依赖子任务。最常用，LiuHao AI OS 默认模式。

### 2. 流水线（Pipeline）

```
Researcher → Analyst → Writer → Reviewer
（每个环节输出是下一环节输入）
```

适用：阶段明确的加工链。注意环节间契约（schema）要显式定义。

### 3. 对抗（Generator-Critic）

```
Generator ──产出──▶ Critic ──批注──▶ Generator ──改进──▶ ...（≤3 轮）
```

适用：质量敏感任务。Critic 必须用**不同提示词、最好不同模型**——同源模型自我批评有系统性盲区。

### 4. 辩论（Debate）

```
Agent A（正方）──┐
                 ├──▶ Judge ──▶ 结论（含分歧记录）
Agent B（反方）──┘
```

适用：方案选型、风险评估。Judge 的职责是呈现权衡而非强制共识。

### 5. 蜂群（Swarm，慎用）

大量同构智能体各自探索、共享发现黑板。仅用于大规模并行检索类任务；协调与去重成本高，生产慎用。

---

## 通信协议设计

### 消息信封

```python
@dataclass
class AgentMessage:
    msg_id: str
    from_agent: str
    to_agent: str                    # 或 "broadcast:team-x"
    msg_type: Literal[
        "task_assign",     # 主管 → 工人：分派
        "task_result",     # 工人 → 主管：汇报
        "query",           # 平级：请求信息
        "inform",          # 平级：单向告知
        "challenge",       # 质疑：要求对方论证
        "escalate",        # → 主管/人类：无法解决
    ]
    correlation_id: str              # 同一任务链
    payload: dict                    # 业务内容
    context_refs: list[str] = field(default_factory=list)  # 记忆引用而非全文
    ttl_s: int = 3600                # 超时未消费视为失败
```

### 核心规则

1. **传引用不传全文**：`context_refs` 指向共享记忆，而非复制几千 token 的上下文——成本与一致性双赢
2. **每条消息可审计**：全量入审计流（多智能体系统的扯皮问题只能靠回放裁决）
3. **显式状态机**：任务状态（assigned/running/blocked/done）由消息驱动，禁隐式假设
4. **升级通道常备**：任何智能体可 `escalate`，升级不视为失败

---

## 任务分解与结果合并

### 分解原则（Supervisor 侧）

```python
DECOMPOSE_GUIDELINES = """
分解子任务时遵守：
1. 子任务间弱依赖：能并行的绝不串行
2. 每个子任务自带完整上下文：工人看不到全局，任务描述必须自足
3. 子任务粒度 5~20 分钟：太碎协调开销爆炸，太大失去并行意义
4. 每个子任务有明确产出契约（格式 + 长度 + 必需字段）
5. 预留合并策略：分解时就想好结果怎么拼
"""
```

### 合并策略

| 策略 | 适用 | 实现 |
|------|------|------|
| 拼接 | 并行研究不同主题 | 按主题章节合并 + 去重引用 |
| 投票 | 分类/判断任务 | 多数决 + 置信度加权，分歧大升级 |
| 择优 | 生成多个候选方案 | Judge 按评分细则选 + 记录落选理由 |
| 归约 | 数值/统计任务 | 程序化聚合（不进 LLM） |

```python
async def merge_results(sub_results: list[SubResult], strategy: str) -> Result:
    if strategy == "concat":
        return concat_with_dedup(sub_results)
    if strategy == "vote":
        votes = [r.verdict for r in sub_results]
        winner, margin = weighted_vote(votes, weights=[r.confidence for r in sub_results])
        if margin < 0.2:
            return await escalate_to_human(sub_results, reason="close_vote")
        return winner
    if strategy == "reduce":
        return programmatic_reduce(sub_results)      # 不用 LLM，零成本零幻觉
```

---

## 冲突解决

智能体间分歧是特性不是 bug——前提是分歧可见、可裁决。

### 冲突类型与处置

| 冲突 | 检测 | 处置 |
|------|------|------|
| 事实冲突（A 说版本是 2.0，B 说 3.0） | 合并时事实比对 | 并列呈现 + 来源标注 + 置信度排序 |
| 判断冲突（方案选型分歧） | 投票分散度 | 升级人类或 Judge 裁决，记录双方论据 |
| 资源冲突（同时申请同一资源） | 资源管理器 | 锁/排队，[语义锁](../knowledge_base/20-advanced-patterns/saga-pattern.md) |
| 记忆冲突（共享空间矛盾事实） | 定期巡检 | 见[记忆管理](memory-management-best-practices.md)冲突处理 |

### 死锁与活锁防护

```python
# 协商循环必须有轮次上限
MAX_NEGOTIATION_ROUNDS = 3

async def negotiate(a: Agent, b: Agent, topic: str) -> Agreement:
    for round_no in range(1, MAX_NEGOTIATION_ROUNDS + 1):
        resp_a = await a.argue(topic, round=round_no)
        resp_b = await b.argue(topic, round=round_no, opponent=resp_a)
        if agreement := check_consensus(resp_a, resp_b):
            return agreement
    # 轮次耗尽：升级主管裁决，而非无限循环
    return await supervisor.arbitrate(topic, positions=[resp_a, resp_b])
```

---

## 成本控制

多智能体的成本是乘法：N 个智能体 × 各自上下文 × 通信开销。

| 手段 | 节省 |
|------|------|
| 工人用小模型，仅主管用大模型 | 40~70% |
| 传引用不传全文 | 通信 token 降 80%+ |
| 对抗/辩论轮次硬上限（≤3） | 防失控 |
| 子任务预算包干（超出即终止并汇报部分结果） | 防单点失控 |
| 共享检索结果缓存（多工人查同一主题只查一次） | 去重 |

```python
async def run_with_budget(team: Team, task: Task, budget_usd: float):
    meter = CostMeter(budget_usd)
    async def tracked_run(agent, subtask):
        async with meter.charge(agent) as allowed:
            if not allowed:
                return PartialResult(reason="budget_exhausted")
            return await agent.run(subtask)
    results = await asyncio.gather(*(tracked_run(a, st) for a, st in team.split(task)))
    return merge_results(results, team.merge_strategy)
```

---

## Python 实现示例

### 主管-工人完整骨架

```python
class Supervisor:
    def __init__(self, workers: list[Worker], bus: MessageBus):
        self._workers = workers
        self._bus = bus

    async def execute(self, goal: str) -> Report:
        # 1. 分解
        subtasks = await self._decompose(goal)
        # 2. 并行分派（各工人独立上下文）
        pending = {
            asyncio.create_task(w.run(st.description, budget=st.budget)): st
            for w, st in zip(self._workers, subtasks)}
        results = []
        for fut in asyncio.as_completed(pending, timeout=600):
            st = pending[fut]
            try:
                results.append(await fut)
            except Exception as exc:
                results.append(PartialResult(subtask=st.id, error=str(exc)))
        # 3. 合并 + 质检
        merged = await merge_results(results, strategy="concat")
        if quality_check(merged) < QUALITY_BAR:
            merged = await self._revise(merged, results)   # 一轮修订
        return Report(goal=goal, content=merged,
                      trace=[r.trace_id for r in results])

    async def _decompose(self, goal: str) -> list[SubTask]:
        resp = await self._llm.complete(
            system=DECOMPOSE_GUIDELINES,
            user=goal,
            response_format=SUBTASK_SCHEMA)          # 结构化输出
        return [SubTask(**s, budget=self._budget_for(s)) for s in resp["subtasks"]]
```

---

## 配置示例

```yaml
# config/multi_agent.yaml
coordination:
  default_pattern: supervisor_worker
  max_team_size: 6
  max_parallel_workers: 4

  communication:
    context_by_reference: true
    full_context_threshold_tokens: 2000    # 超过则必须传引用
    message_ttl_s: 3600

  decomposition:
    subtask_min_minutes: 5
    subtask_max_minutes: 20
    require_output_contract: true

  adversarial:
    max_rounds: 3
    critic_different_model: true
    judge_records_dissent: true

  budget:
    team_multiplier: 3.0                   # 团队任务预算 = 单体 × 3
    subtask_hard_cap: true
    worker_model_tier: small               # 工人用小模型
    supervisor_model_tier: large

  escalation:
    close_vote_margin: 0.2
    negotiation_max_rounds: 3
```

---

## 最佳实践

1. **单体优先，多体补强**：先用单智能体 + 好工具做到极限，多智能体解决的是单体的特定瓶颈。
2. **Critic 必须异构**：同模型同提示词的自我批评是形式主义。
3. **通信从第一天就入审计**：多智能体系统的"谁说了什么导致了这个结果"只能靠回放回答。
4. **轮次与预算双硬顶**：协商、对抗、修订循环都必须有不可逾越的上限。
5. **结果合并优先程序化**：能不用 LLM 的合并就不用——又快又准又便宜。
6. **保留分歧**：合并输出不应假装共识；标注分歧的系统比虚假一致的系统可信十倍。

---

## 常见问题

### Q1: 多智能体系统的输出质量反而不如单智能体，为什么？

常见原因三选一：上下文在传递中失真（用引用 + 结构化契约解决）；主管分解不当（子任务不自足、粒度错配）；合并阶段把好结果平均化了（合并策略改为择优或保留分歧）。

### Q2: 智能体之间陷入互相"客气"的死循环（A 说听 B 的，B 说听 A 的）？

经典的协商活锁。轮次硬上限 + 主管仲裁是解法；提示词层面给每个智能体明确的主张义务（"你必须给出自己的独立判断及理由"）。

### Q3: 团队里某个智能体总是成为瓶颈？

看轨迹：是它慢（模型大/任务重——换小模型或拆任务）还是大家都在等它（依赖结构问题——重新设计分解，让它的产出不再在关键路径上）。

### Q4: 如何调试多智能体任务？

按 `correlation_id` 拉取全部消息与轨迹，画出时间线：谁分派、谁执行、谁等待、谁超时。90% 的协调问题在时间线上一眼可见。LiuHao AI OS 驾驶舱提供该视图。

### Q5: 人类应该参与多智能体协作的哪个环节？

三处：任务定义（目标与约束）、分歧裁决（close vote 升级）、最终验收（高风险任务）。中间的协调、分派、合并都应自动化——人类在环不等于人类在每一步。

---

**相关文档**：[智能体设计模式](agent-design-patterns.md) · [记忆管理最佳实践](memory-management-best-practices.md) · [错误处理模式](error-handling-patterns.md) · [多智能体核心概念](../knowledge_base/core-concepts/multi_agent.md)
