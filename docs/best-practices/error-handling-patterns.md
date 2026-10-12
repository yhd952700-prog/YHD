# 错误处理模式

智能体系统的错误处理比传统软件更难：错误来源多（LLM 幻觉、工具失败、网络抖动、策略拒绝）、部分错误没有异常（答案错了但调用成功）、恢复需要语义判断（重试还是换方法）。本文档提供分层错误处理模式与恢复策略。

---

## 目录

- [错误分类体系](#错误分类体系)
- [分层防御模型](#分层防御模型)
- [模式一：分类重试](#模式一分类重试)
- [模式二：优雅降级链](#模式二优雅降级链)
- [模式三：输出验证与修复](#模式三输出验证与修复)
- [模式四：任务级补偿](#模式四任务级补偿)
- [错误预算与熔断](#错误预算与熔断)
- [Python 实现示例](#python-实现示例)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 错误分类体系

处理之前先分类——不同类别的错误，正确处置完全不同：

| 类别 | 示例 | 处置 |
|------|------|------|
| **瞬时错误** Transient | 网络超时、429、503 | 退避重试 |
| **永久错误** Permanent | 参数无效、权限不足、404 | 不重试，修正输入或失败 |
| **容量错误** Capacity | 配额耗尽、预算超限 | 降级、排队或延迟 |
| **语义错误** Semantic | LLM 输出格式错、答非所问、工具选错 | 重新生成、换方法、升级 |
| **策略拒绝** Policy | 高风险动作被门禁拦截 | 走审批，非错误 |
| **系统性故障** Systemic | 下游整体不可用 | 熔断 + 降级 |

关键认知：**语义错误是智能体系统特有的、也是占比最大的错误类别**——调用全部成功，但结果不对。它无法靠 try/except 捕获，必须靠验证层发现。

---

## 分层防御模型

```
L1 调用层    重试 / 超时 / 熔断            —— 处理瞬时与系统性错误
L2 验证层    输出 schema 校验 / 语义检查    —— 发现语义错误
L3 恢复层    重新生成 / 换方法 / 降级       —— 语义错误的恢复
L4 任务层    补偿 / Saga / 部分结果        —— 保证任务整体一致
L5 升级层    人类介入 / 明确失败           —— 最后的诚实
```

原则：**错误应该在尽可能低的层被处理，但绝不许在任何一层被吞掉**。升级到 L5 不是失败，假装成功才是。

---

## 模式一：分类重试

```python
class RetryPolicy:
    def __init__(self):
        self._rules: list[tuple[type[Exception], RetrySpec]] = [
            (RateLimitError,   RetrySpec(max=5, base=1.0,  jitter="full", respect_retry_after=True)),
            (TimeoutError,     RetrySpec(max=3, base=0.5,  jitter="full")),
            (ConnectionError,  RetrySpec(max=3, base=0.2,  jitter="decorrelated")),
            (ValidationError,  RetrySpec(max=0)),          # 永久错误不重试
            (PermissionDenied, RetrySpec(max=0)),
            (QuotaExhausted,   RetrySpec(max=0)),          # 走降级而非重试
        ]

    async def execute(self, fn, *args, **kwargs):
        attempt = 0
        while True:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                spec = self._match(exc)
                if spec is None or attempt >= spec.max:
                    raise
                delay = spec.delay(attempt, exc)
                RETRY_COUNTER.labels(type=type(exc).__name__).inc()
                await asyncio.sleep(delay)
                attempt += 1
```

配套规则：

- **重试必须幂等**：非幂等操作带幂等键（见[自定义集成](../knowledge_base/22-integrations/custom-integration-guide.md)）
- **总预算上限**：单任务重试总次数 ≤ 10，防重试风暴
- **熔断优先**：[熔断打开时不重试](../knowledge_base/20-advanced-patterns/circuit-breaker-advanced.md)，直接降级

---

## 模式二：优雅降级链

降级的本质：**用更便宜/更弱但可用的方式继续服务**，而非直接报错。

```python
# LLM 调用的降级链（详见 circuit-breaker-advanced.md）
CHAIN = [
    ("主模型",      lambda p: llm.complete("gpt-5", p)),
    ("替代提供商",  lambda p: llm.complete("claude", p)),
    ("小模型",      lambda p: llm.complete("gpt-5-mini", p)),
    ("语义缓存",    lambda p: semantic_cache.nearest(p)),      # 相似答案
    ("排队延后",    lambda p: queue.defer(p)),                  # 异步处理
    ("诚实失败",    None),                                      # L5
]

async def resilient_complete(prompt: str) -> Result:
    for name, fn in CHAIN:
        if fn is None:
            break
        try:
            result = await fn(prompt)
            if name != "主模型":
                result.degraded = True
                result.degradation_path = name       # 用户有权知道
            return result
        except (CircuitOpenError, CapacityError):
            continue
    raise AllPathsExhausted()
```

降级链设计准则：

1. **每一级都可独立验证**：演练时逐级切断验证
2. **降级要可见**：结果标记 `degraded`，UI 提示，指标上报
3. **降级不等于低质**：缓存命中、排队延后可能给出与主路径同等质量的结果

---

## 模式三：输出验证与修复

### 结构化输出验证

```python
from pydantic import BaseModel, ValidationError

class ResearchReport(BaseModel):
    summary: str
    findings: list[str]
    citations: list[Citation]
    open_questions: list[str]

async def generate_validated(prompt: str, max_repairs: int = 2) -> ResearchReport:
    for attempt in range(max_repairs + 1):
        raw = await llm.complete(prompt, response_format=ResearchReport)
        try:
            return ResearchReport.model_validate_json(raw)
        except ValidationError as ve:
            # 修复提示：把具体校验错误喂回去，让模型自我修正
            prompt = (f"上一次输出未通过校验，请修正后重新输出完整 JSON。\n"
                      f"校验错误：{ve.errors()[:3]}\n"
                      f"错误输出片段：{raw[:500]}")
    raise SemanticError("output validation failed after repairs")
```

### 语义验证（超越 schema）

```python
async def semantic_checks(output: ResearchReport, context: Context) -> list[Issue]:
    issues = []
    # 1. 引用真实性：引用的来源是否真的在检索结果里
    for cite in output.citations:
        if cite.url not in context.retrieved_urls:
            issues.append(Issue("hallucinated_citation", cite.url))
    # 2. 数字一致性：输出中的数字是否与工具返回一致
    for num in extract_numbers(output.summary):
        if not any(num in r for r in context.tool_results):
            issues.append(Issue("ungrounded_number", str(num)))
    # 3. 答非所问：输出与任务目标的相关性
    if await relevance_score(context.goal, output.summary) < 0.5:
        issues.append(Issue("off_topic", context.goal))
    return issues
```

发现语义错误后的恢复选项：**带错误反馈重新生成**（最便宜先试）→ **换方法**（换模型/换提示策略）→ **升级人类**。

---

## 模式四：任务级补偿

智能体执行了多个有副作用的步骤后中途失败，不能只抛异常——要恢复一致性：

```python
class AgentTask:
    def __init__(self):
        self._compensations: list[Callable] = []      # 补偿栈

    async def step(self, name: str, action, compensate=None):
        result = await action()
        if compensate:
            self._compensations.append((name, compensate))
        return result

    async def run(self):
        try:
            draft = await self.step("draft",
                lambda: writer.write(self.goal),
                compensate=lambda d: None)                    # 无副作用
            await self.step("publish_internal",
                lambda: cms.post(draft, channel="internal"),
                compensate=lambda _: cms.delete_latest("internal"))
            await self.step("notify",
                lambda: slack.send(f"已发布：{draft.title}"),
                compensate=None)                              # 枢纽：不可补偿
        except Exception as exc:
            await self._rollback()
            raise TaskFailed(self.goal, cause=exc,
                             rolled_back=True) from exc

    async def _rollback(self):
        for name, compensate in reversed(self._compensations):
            try:
                await compensate()
            except Exception:
                # 补偿失败：记录 + 告警 + 继续补偿其余（见 saga-pattern.md）
                await audit.compensation_failed(self.id, name)
```

这就是 [Saga 模式](../knowledge_base/20-advanced-patterns/saga-pattern.md)在智能体任务内的落地。

---

## 错误预算与熔断

为每个智能体定义**错误预算**：允许的错误率上限，耗尽即自动收紧行为。

```yaml
# 示例
error_budget:
  agent: researcher-01
  window: 1h
  thresholds:
    semantic_error_rate: 0.15      # 语义错误率 >15%
    tool_failure_rate: 0.10        # 工具失败率 >10%
  on_exhausted:
    - switch_to_conservative_mode   # 只读、高风险动作全审批
    - notify_owner
    - require_manual_reset
```

意义：智能体可能因提示词变更、记忆污染、上游数据变化而"变笨"——错误预算是自动发现这种漂移的机制，比人工巡检快得多。

---

## Python 实现示例

### 统一错误处理管道

```python
@dataclass
class Outcome:
    value: Any = None
    error: Exception | None = None
    degraded: bool = False
    degradation_path: str | None = None
    escalated: bool = False

async def execute_task_pipeline(task: Task) -> Outcome:
    retry = RetryPolicy()
    try:
        # L1: 调用层
        raw = await retry.execute(agent.run, task)
        # L2: 验证层
        issues = await semantic_checks(raw, task.context)
        if not issues:
            return Outcome(value=raw)
        # L3: 恢复层
        repaired = await repair_with_feedback(task, raw, issues)
        if repaired:
            return Outcome(value=repaired, degraded=True,
                           degradation_path="repair_loop")
        # L3 备选：降级模型/方法
        fallback = await fallback_method(task)
        return Outcome(value=fallback, degraded=True,
                       degradation_path="fallback_method")
    except CapacityError:
        queued = await queue.defer(task)
        return Outcome(value=queued, degraded=True,
                       degradation_path="deferred")
    except PolicyDenied as pd:
        approval = await request_approval(task, pd)
        return Outcome(value=approval, escalated=True)
    except Exception as exc:
        # L5: 诚实失败——带完整上下文，方便人工接手
        await audit.task_failed(task.id, exc)
        return Outcome(error=TaskFailed(
            task.id, cause=exc,
            partial_state=await task.capture_state(),
            suggested_action=suggest_next_action(exc)))
```

---

## 配置示例

```yaml
# config/error_handling.yaml
retry:
  total_budget_per_task: 10
  specs:
    rate_limit:    { max: 5, base_s: 1.0, jitter: full }
    timeout:       { max: 3, base_s: 0.5, jitter: full }
    connection:    { max: 3, base_s: 0.2, jitter: decorrelated }
    validation:    { max: 0 }
    permission:    { max: 0 }

validation:
  schema_repair_attempts: 2
  semantic_checks: [citation_grounding, number_grounding, relevance]
  relevance_threshold: 0.5

degradation:
  chains:
    llm: [primary, alt_provider, small_model, semantic_cache, defer, fail]
    search: [vector_db, keyword_fallback, cached_results, fail]
  mark_degraded: true

error_budget:
  window: 1h
  semantic_error_rate: 0.15
  tool_failure_rate: 0.10
  on_exhausted: conservative_mode

compensation:
  on_task_failure: auto_rollback
  compensation_failure_alert: critical
```

---

## 最佳实践

1. **错误分类先于处理**：没分类的 catch-all 重试是把永久错误变成延迟炸弹。
2. **验证层独立存在**：不要让生成者自己验证自己（与[多智能体](multi-agent-coordination.md)的 critic 异构同理）。
3. **降级可见**：静默降级 = 静默降质；用户和指标都必须知道。
4. **部分结果有价值**：任务失败时保存已完成的中间产物，人工接手或续跑时用得上。
5. **错误信息可操作**：给 LLM 的错误反馈要具体到"怎么改"，给人类的错误报告要带上下文与建议动作。
6. **失败也进记忆**：任务失败的教训（为什么败、怎么避免的）写入情景记忆 lessons——这是智能体变聪明的主要途径。

---

## 常见问题

### Q1: LLM 输出格式总是不稳定，修复循环也救不回来？

三板斧按序尝试：① 用模型原生的结构化输出能力（JSON mode / tool calling），可靠性远高于提示词约束；② schema 做减法（字段越多越容易错）；③ 示例驱动（few-shot 给一个完整正确示例）。仍不行则该任务不适合直接生成，拆成两步：先生成自由文本，再用小模型抽取结构。

### Q2: 重试后成功了，但用户看到了重复效果（消息发了两次）？

重试的前提永远是幂等。对外动作必须带幂等键；不支持幂等的下游（某些老系统），重试前先查状态（"刚才那个请求到底成没成"），或在设计层把它包成幂等服务。

### Q3: 智能体陷入"失败→重试→再失败"的循环烧光预算？

三重硬顶：单任务重试总预算、错误率熔断（错误预算）、以及最重要的——**恢复策略必须变化**（第二次尝试用不同方法/模型/参数，完全相同地重来不叫恢复叫碰运气）。

### Q4: 语义验证会不会误杀好结果（relevance 阈值设错）？

会。所有语义检查阈值都要在评测集上校准（见[测试策略](testing-strategies.md)），并保留人工复核通道：被拦截的输出进待审队列而非直接丢弃，误杀样本用于调阈值。

### Q5: 补偿执行时下游系统已经变了（别人也改了那条数据）？

补偿前做前置条件检查（版本号/哈希比对），不满足则转人工工单而非强行执行。这就是 Saga 模式里"补偿失败升级"的路径——自动化有边界，诚实面对。

---

**相关文档**：[智能体设计模式](agent-design-patterns.md) · [Saga 模式](../knowledge_base/20-advanced-patterns/saga-pattern.md) · [熔断策略](../knowledge_base/20-advanced-patterns/circuit-breaker-advanced.md) · [测试策略](testing-strategies.md)
