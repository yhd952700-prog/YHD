# LIUHAO X v3.0 — 12 Kernel 接口定义

> ⚠️ **Kernel 计数漂移**：本文撰写时基于 **12 Kernel**。当前权威为 **14 Kernel**（新增 security / audit / plugin），以 [`spec/KERNEL-CANON.md`](../spec/KERNEL-CANON.md) 为准。文中出现的 "12 Kernel" 属历史快照，不作为现状依据。

> **依据**：Definition Lock §112, §122 Parallel Workstreams §96
> **状态**：FINALIZED — Interface Freeze Complete
> **创建日期**：2026-09-05
> **批准日期**：2026-09-05
> **批准依据**：三文档批准 — PM PRD + Architect Architecture + Designer UIUX

---

## 1. 概览

| # | Kernel | 核心职责 | 关键接口 |
|---|--------|----------|----------|
| 1 | **Context** | 12 inputs → compression → model context | `compress(inputs)`, `set_scope()`, `process()` |
| 2 | **Capability** | Registry + traceability + scoping | `register()`, `lookup()`, `get_traceability()` |
| 3 | **Event** | Unified event bus + correlation IDs | `publish()`, `subscribe()`, `correlation_id` |
| 4 | **Execution** | Goal→Task→Plan→Action→Verify | `decompose()`, `plan()`, `execute()`, `verify()` |
| 5 | **Resource** | CPU/Mem/Storage/Token/Time/$ Quotas | `allocate()`, `release()`, `query_quotas()` |
| 6 | **Policy** | Permission boundaries + policy engine | `define_rule()`, `evaluate()`, `decision()` |
| 7 | **Network** | Protocol adapters + comm bus | `route()`, `adapter()`, `correlation_id` |
| 8 | **Trust** | Trust scores + chain + revocation | `assign_score()`, `propagate()`, `revoke()` |
| 9 | **Evaluation** | Outcome evaluation + feedback | `evaluate()`, `feedback()`, `replan()` |
| 10 | **Identity** | Agent identity + permissions | `create_identity()`, `grant_permission()`, `audit()` |
| 11 | **Memory** | Multi-tier memory + scoping | `store()`, `recall()`, `scope_filter()` |
| 12 | **Audit** | Complete audit trail + integrity | `log()`, `verify_integrity()`, `query()` |

---

## 2. 详细接口规范

### 2.1 Context Kernel Interface

```python
from src.kernels.context import ContextKernel, ContextInputType, ContextInput

# 初始化
kernel = ContextKernel(
    mechanism=AttentionMechanism.HYBRID,
    scope="L0"
)

# 添加输入 (最多12种类型)
inputs = [
    ContextInput(type=ContextInputType.GOAL, source="orchestrator", data={"goal": "..."}),
    ContextInput(type=ContextInputType.TASK, source="task_graph", data={"tasks": [...]}),
    # ... 10 more input types
]

# 处理并压缩
result = kernel.process(inputs)

# 结果访问
compressed = result.compressed        # 模型就绪的上下文
attention = result.attention_weights  # 注意力权重
retained = result.retained_keys      # 保留的键
discarded = result.discarded_keys    # 剔除的键
ratio = result.compression_ratio     # 压缩比
scope = result.scope                 # 当前权限范围
```

### 2.2 Capability Kernel Interface

```python
from src.kernels.capability import CapabilityRegistry, CapabilityEntry

# 注册能力
cap = CapabilityEntry(
    id="search",
    version="1.0.0",
    namespace="core",
    description="Web search capability",
    scope="L3",
    owner="system"
)
registry = CapabilityRegistry()
registry.register(cap)

# 查找能力
entry = registry.lookup("search", version="1.0.0")
print(entry.traceability_chain)  # [kernel → capability → owner]

# 范围检查
allowed = registry.check_scope("search", requested_scope="L3")
```

### 2.3 Event Kernel Interface

```python
from src.kernels.event import EventBus, Event

# 发布事件
event = Event(
    type="goal_decomposed",
    source="execution_kernel",
    data={"goal_id": "g-123", "tasks": 5},
    correlation_id="corr-abc-123"
)
bus = EventBus()
bus.publish(event)

# 订阅事件
def handler(e: Event):
    print(f"Received: {e.type}, correlation: {e.correlation_id}")

bus.subscribe("goal_decomposed", handler, scope="L2")
```

### 2.4 Execution Kernel Interface

```python
from src.kernels.execution import ExecutionEngine, Goal, Task

# 目标分解
goal = Goal(
    id="g-001",
    natural_language="预订机票到上海",
    scope="L1"
)
tasks = engine.decompose(goal)

# 执行计划
plan = engine.plan(tasks, mode="parallel")

# 执行
results = engine.execute(plan)

# 验证
verified = engine.verify(results, criteria={"success_rate": 0.95})
```

### 2.5 Resource Kernel Interface

```python
from src.kernels.resource import ResourceQuotaManager

# 配额申请
result = manager.allocate(
    resource="token",
    amount=1000,
    scope="L2",
    owner="agent-42"
)

# 查询当前配额
current = manager.query_quotas(scope="L2")
print(current.tokens_available)

# 释放
manager.release(resource="token", amount=100, scope="L2")
```

### 2.6 Policy Kernel Interface

```python
from src.kernels.policy import PolicyEngine, PolicyRule

# 定义策略规则
rule = PolicyRule(
    id="p-001",
    condition="agent.has_permission('search')",
    action="ALLOW",
    scope="L3",
    precedence=10
)

engine = PolicyEngine()
engine.define_rule(rule)

# 评估决策
decision = engine.evaluate(
    condition={"agent": "agent-42", "action": "search"},
    scope="L3"
)
print(decision.result)  # ALLOW/DENY
print(decision.traceability)
```

### 2.7 Network Kernel Interface

```python
from src.kernels.network import NetworkBus, Message

# 路由消息
msg = Message(
    id="msg-001",
    content={"type": "search", "query": "AI OS"},
    destination="capability_kernel",
    protocol="A2A",
    correlation_id="corr-001"
)

routed = network_bus.route(msg)
print(f"Routed via: {routed.protocol}, hops: {routed.hops}")
```

### 2.8 Trust Kernel Interface

```python
from src.kernels.trust import TrustManager, TrustScore

# 分配信任分
manager = TrustManager()
score = manager.assign_score(
    entity="agent-42",
    initial_score=0.8,
    scope="L2",
    reasons=["onboard", "validation"]
)

# 信任传播
propagated = manager.propagate(
    source="agent-42",
    target="capability-123",
    scope="L2"
)

# 收回
manager.revoke(entity="agent-42", scope="L2", reason="violation")
```

### 2.9 Evaluation Kernel Interface

```python
from src.kernels.evaluation import Evaluator, EvaluationCriteria

# 定义评估标准
criteria = EvaluationCriteria(
    success_threshold=0.9,
    off_track_threshold=0.5,
    max_retries=3
)

evaluator = Evaluator(criteria=criteria)

# 评估结果
result = evaluator.evaluate(
    intended_goal={"action": "book_flight", "city": "SHA"},
    actual_outcome={"flight_booked": True, "city": "SHA", "cost": 800},
    scope="L1"
)

print(result.feedback)  # success/partial/off_track/failed
print(result.replan_triggered)  # True/False
print(result.escalation_required)  # True/False
```

### 2.10 Identity Kernel Interface

```python
from src.kernels.identity import IdentityManager, AgentIdentity

# 创建身份
manager = IdentityManager()
identity = manager.create_identity(
    principal="agent-42",
    permissions=["read", "write"],
    scope="L3",
    trust_score=0.85
)

# 授权
granted = manager.grant_permission(
    identity=identity,
    permission="search",
    scope="L2"
)

# 审计
audit_entries = manager.audit_trail(identity.id, scope="L3")
```

### 2.11 Memory Kernel Interface

```python
from src.kernels.memory import MemoryManager, MemoryTier

manager = MemoryManager()

# 存储
manager.store(
    tier=MemoryTier.WORKING,
    key="conversation-history-123",
    value={"messages": [...], "ttl": 3600},
    scope="L2"
)

# 召回
data = manager.recall(
    key="conversation-history-123",
    scope="L2"
)

# 范围过滤
results = manager.scope_filter("L3")
```

### 2.12 Audit Kernel Interface

```python
from src.kernels.audit import AuditLogger, AuditEvent

logger = AuditLogger()

# 记录操作
logger.log(
    event_type="permission_granted",
    actor="system",
    target="agent-42",
    permission="search",
    scope="L2",
    correlation_id="corr-001",
    payload={"granted_by": "admin", "timestamp": time.time()}
)

# 完整性验证
verified = logger.verify_integrity(
    start_time="2026-09-01",
    end_time="2026-09-05",
    scope="L3"
)
```

---

## 3. 并行 Workstream 规则 (§96)

### 允许并行的组合：

| 组合 | 原因 |
|------|------|
| Context vs Capability | 无共享不安全变更 |
| Context vs Event | 接口已定义，边界清晰 |
| Capability vs Policy | 权限模型独立 |
| Network vs Trust | 通信与信任边界分离 |
| Evaluation vs Execution | 结果评估 vs 执行流程 |

### 禁止并行的组合：

| 组合 | 冲突点 |
|------|--------|
| Context修改时 Network 修改 | 共享相关上下文传递 |
| Policy修改时 Memory修改 | 共享权限作用域 |
| Network修改时 Trust修改 | 共享 correlation ID 传递 |

---

## 4. Convergence Point 汇合

| 汇合点 | 关联 Workstream | 触发 Phase |
|--------|----------------|------------|
| **A: Secure Control Foundation** | A + B + G | Phase 0+1+2+3+7 |
| **B: Executable Intelligence Core** | A + B + G + F | Phase 4+5+6+8 |
| **C: Agent World Operating System** | A+B+C+D+E+F+G+H | Phase 9+10+12+13+14+15+16+19+20 |

---

## 5. 签字

**起草人**：LiuHao AI OS Development Team  
**日期**：2026-09-05  
**状态**：DRAFT - 待用户确认后进入 REVIEW  
**下一步**：用户确认 → 进入 REVIEW → 最终确认 → Integration

---