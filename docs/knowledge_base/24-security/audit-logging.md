# 审计日志

审计日志回答三个问题：**谁、在什么时候、对什么、做了什么、结果如何、为什么**。在智能体系统中还要加一问：**基于什么上下文做的决定**。LiuHao AI OS 的主权内核把审计作为一等公民：不可篡改、可回放、可问责。本文档覆盖审计事件设计、防篡改存储、查询分析与合规对接。

---

## 目录

- [审计 vs 日志 vs 监控](#审计-vs-日志-vs-监控)
- [审计事件模型](#审计事件模型)
- [防篡改存储](#防篡改存储)
- [Python 实现示例](#python-实现示例)
- [查询与回放](#查询与回放)
- [保留与归档](#保留与归档)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 审计 vs 日志 vs 监控

| 维度 | 应用日志 | 监控指标 | 审计日志 |
|------|---------|---------|---------|
| 目的 | 调试 | 健康度 | 问责与合规 |
| 读者 | 开发者 | 运维 | 安全/合规/审计员 |
| 保留期 | 天~周 | 周~月 | **年（1~7 年）** |
| 可变性 | 可被清理 | 聚合降采样 | **不可变、防篡改** |
| 内容 | 自由格式 | 数值 | 结构化事实 + 上下文 |

三者互补不互替。常见错误：把审计写在应用日志里，30 天后随日志轮转消失——合规审计时无据可查。

---

## 审计事件模型

### 必须审计的操作

| 类别 | 事件 |
|------|------|
| 认证授权 | 登录成功/失败、token 签发、API key 创建/吊销、权限变更 |
| 数据访问 | 记忆读取（含检索）、导出、删除、跨租户访问尝试 |
| 智能体行为 | 高风险工具调用、审批请求/批准/拒绝、策略违反 |
| 管理操作 | 智能体创建/删除、配额调整、策略变更、配置修改 |
| 密钥 | 密钥读取、轮换、访问拒绝 |
| 系统 | Saga 补偿、熔断跳闸、数据归档/销毁 |

### 事件结构

```json
{
  "audit_id": "aud_01HF9X...",
  "occurred_at": "2026-10-12T08:31:22.481Z",
  "actor": {
    "kind": "user|agent|system|integration",
    "id": "user_7",
    "impersonator": null,
    "session_id": "ses_abc"
  },
  "action": "memory.delete",
  "target": {
    "kind": "memory",
    "id": "mem_1234",
    "tenant_id": "tenant_9"
  },
  "result": "success|denied|error",
  "reason": "user_requested",
  "context": {
    "ip": "203.0.113.7",
    "user_agent": "console-ui/1.4.2",
    "correlation_id": "trace_abc123",
    "saga_id": null
  },
  "details": {
    "memory_layer": "semantic",
    "content_hash": "sha256:9f2c...",
    "approval_id": null
  },
  "prev_hash": "sha256:aa10...",
  "hash": "sha256:f4c2..."
}
```

设计要点：

- **`actor.kind` 区分人与智能体**：智能体行为必须能追溯到发起任务的人（`details.initiated_by`）
- **`correlation_id` 贯穿**：一次任务的所有审计事件可串联回放
- **`prev_hash`/`hash` 哈希链**：防篡改的核心（见下节）
- **内容存哈希不存明文**：`content_hash` 证明"删的是这份内容"而不在审计里复制敏感数据（GDPR 友好）

---

## 防篡改存储

### 哈希链（Hash Chain）

每条审计记录包含前一条的哈希，任何历史篡改都会使后续链条断裂：

```python
import hashlib, json

def canonical(event: dict) -> bytes:
    """规范化序列化：键排序、无空白，保证哈希稳定。"""
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()

class AuditChain:
    def __init__(self, store):
        self._store = store
        self._tail_hash = store.get_tail_hash() or "0" * 64

    async def append(self, event: dict) -> str:
        event["prev_hash"] = self._tail_hash
        event["hash"] = hashlib.sha256(canonical(event)).hexdigest()
        await self._store.insert(event)          # INSERT-only，禁 UPDATE/DELETE
        self._tail_hash = event["hash"]
        return event["hash"]

    async def verify(self, from_id: str | None = None) -> tuple[bool, str | None]:
        prev = "0" * 64
        async for event in self._store.stream(from_id):
            expected = event.pop("hash")
            actual_prev = event.get("prev_hash")
            if actual_prev != prev:
                return False, event["audit_id"]
            if hashlib.sha256(canonical(event)).hexdigest() != expected:
                return False, event["audit_id"]
            prev = expected
        return True, None
```

### 存储分层

| 层 | 介质 | 保留 | 说明 |
|----|------|------|------|
| 热层 | PostgreSQL（INSERT-only 表 + 分区） | 90 天 | 在线查询 |
| 温层 | 对象存储（S3，Object Lock 合规模式） | 1 年 | WORM，无法删除 |
| 冷层 | 归档存储（Glacier） | 7 年 | 合规兜底 |

**S3 Object Lock 合规模式**是关键：即使 root 账号也无法在保留期内删除对象——这是应对"攻击者拿到云账号后毁灭证据"的终极手段。

### 链头锚定

哈希链有一个弱点：攻击者若从某点全部重写并重算哈希则无法检测。对策——**定期把链头哈希发布到外部**：

```python
# 每日把当日链头哈希写入外部系统（另一个云账号的 S3、区块链、或邮件存档）
async def anchor_daily_head():
    head = await store.get_tail_hash()
    await external_worm.put(f"anchors/{today()}.txt", head)
# 攻击者需同时攻破两个系统才能无痕篡改
```

---

## Python 实现示例

### 审计中间件（自动记录 API 操作）

```python
AUDITABLE = {
    ("POST",   "/api/v1/agents"):         "agent.create",
    ("DELETE", "/api/v1/agents/*"):       "agent.delete",
    ("POST",   "/api/v1/memories/search"): "memory.search",
    ("DELETE", "/api/v1/memories/*"):     "memory.delete",
}

@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    action = AUDITABLE.get((request.method, route_template(request)))
    response = await call_next(request)
    if action:
        principal: Principal | None = getattr(request.state, "principal", None)
        await audit_chain.append({
            "audit_id": new_id("aud"),
            "occurred_at": time.time(),
            "actor": {"kind": "user" if principal else "anonymous",
                      "id": principal.id if principal else None},
            "action": action,
            "target": extract_target(request),
            "result": status_to_result(response.status_code),
            "context": {"ip": request.client.host,
                        "correlation_id": request.state.trace_id},
            "details": await extract_details_safely(request, response),
        })
    return response
```

### 智能体行为审计（含决策上下文）

```python
async def audited_tool_call(agent: Agent, call: ToolCall, ctx: Context):
    decision = await policy_kernel.evaluate(call, ctx)
    event = {
        "actor": {"kind": "agent", "id": agent.id},
        "action": f"tool.{call.name}",
        "target": {"kind": call.target_kind, "id": call.target_id},
        "details": {
            "initiated_by": ctx.initiating_user,     # 问责到人
            "policy_decision": decision.verdict,
            "risk_level": call.risk_level,
            "args_hash": sha256_of(call.args),        # 参数存哈希
            "prompt_excerpt_hash": sha256_of(ctx.prompt_tail),
            "approval_id": decision.approval_id,
        },
        ...
    }
    if decision.verdict == "deny":
        event["result"] = "denied"
        await audit_chain.append(event)
        raise PolicyDenied(decision.reason)
    result = await call.execute()
    event["result"] = "success"
    await audit_chain.append(event)
    return result
```

---

## 查询与回放

### 常用审计查询

```sql
-- 某智能体 24h 内所有高风险操作
SELECT occurred_at, action, result, details->>'approval_id'
FROM audit_log
WHERE actor->>'id' = 'agent_42'
  AND occurred_at > now() - interval '24 hours'
  AND details->>'risk_level' IN ('high', 'critical')
ORDER BY occurred_at;

-- 某用户发起的所有任务链（含智能体代理行为）
SELECT * FROM audit_log
WHERE context->>'correlation_id' IN (
    SELECT context->>'correlation_id' FROM audit_log
    WHERE actor->>'id' = 'user_7' AND occurred_at > $1
)
ORDER BY occurred_at;

-- 异常检测：非工作时间的高危操作
SELECT * FROM audit_log
WHERE action LIKE 'admin.%'
  AND EXTRACT(HOUR FROM occurred_at) NOT BETWEEN 8 AND 20;
```

### 决策回放

结合[事件溯源](../20-advanced-patterns/event-sourcing.md)：审计流的 `correlation_id` 关联事件流，可完整重建"智能体当时看到了什么、检索了什么记忆、策略引擎如何评估"——这是监管问询时的标准应答物。

---

## 保留与归档

| 法规/标准 | 最低保留 | 适用事件 |
|-----------|---------|---------|
| GDPR | 必要期限（建议 1 年） | 个人数据访问 |
| SOC 2 | 1 年 | 全部安全事件 |
| ISO 27001 | 3 年（建议） | 全部 |
| 金融（视辖区） | 5~7 年 | 交易与授权 |

自动化归档流水线：

```python
async def archive_old_partitions():
    """每月：PG 分区 detach → 导出压缩 → S3 Object Lock → 校验 → 删本地。"""
    partition = oldest_partition("audit_log")
    if partition.age_days < 90:
        return
    dump = await export_partition(partition)          # gzip + 行级哈希清单
    await s3.put_with_object_lock(
        f"audit/{partition.name}.jsonl.gz", dump,
        mode="COMPLIANCE", retain_years=7)
    await verify_archive(dump, partition)
    await drop_partition(partition)
```

---

## 配置示例

```yaml
# config/audit.yaml
audit:
  enabled: true
  hash_chain: true
  external_anchor:
    enabled: true
    schedule: "0 1 * * *"             # 每日 01:00
    backend: s3_cross_account
    bucket: liuhao-audit-anchors

  storage:
    hot:
      backend: postgres
      table: audit_log
      partition: monthly
      retain_days: 90
    warm:
      backend: s3
      bucket: liuhao-audit-worm
      object_lock: { mode: COMPLIANCE, retain_days: 365 }
    cold:
      backend: glacier
      retain_years: 7

  content_policy:
    store_payload_hashes: true        # 敏感内容只存哈希
    pii_fields: redact                # actor.ip 等按租户配置脱敏

  alerts:
    verify_failure: critical          # 哈希链验证失败 = 最高告警
    chain_gap: critical
    archive_failure: warning
```

---

## 最佳实践

1. **INSERT-only**：审计表/存储从权限上禁止 UPDATE/DELETE（PG 撤权、S3 Object Lock）。
2. **写审计不可旁路**：高风险操作在代码路径上强制审计（中间件 + 内核拦截），不允许"忘了加"。
3. **存哈希而非明文**：审计要证明事实，不需要复制敏感内容——同时满足 GDPR 数据最小化。
4. **定期验证哈希链**：每日自动 `verify()`，失败即安全事件。
5. **链头外部锚定**：防"全盘重写"的唯一手段。
6. **审计查询要演练**：合规问询通常要求 48h 内提供记录——冷数据恢复流程每季度演练。
7. **审计系统独立可用性**：主系统故障时审计写入要有降级缓冲（本地磁盘队列后补传），但绝不丢弃。

---

## 常见问题

### Q1: 审计写入影响性能怎么办？

异步化：审计事件先入内存队列 → 批量 INSERT（每批 500 条），热路径只多一次入队。极端场景可牺牲"崩溃时最近几秒审计"，用本地磁盘 WAL 缓冲兜底。

### Q2: 哈希链的尾哈希本身被改了怎么办？

外部锚定（每日链头发到独立系统）。验证时同时校验内部链 + 与最近锚点比对，攻击者改尾哈希会与锚点冲突。

### Q3: 审计日志里有个人数据，GDPR 删除请求怎么执行？

设计阶段规避：审计事件中的个人数据只存引用（user_id）与哈希，不存内容明文。删除请求执行时删用户主记录与内容，审计中的 user_id 变为不可解析的孤儿 ID——审计事实（"某 ID 在某时做了某事"）保留，符合 GDPR"为合规目的可保留"的豁免，但需咨询法务确认辖区解释。详见 [GDPR 合规指南](gdpr-compliance-guide.md)。

### Q4: 智能体自动决策的"理由"也要审计吗？

要。`details` 中存策略引擎的评估结果、命中规则 ID、上下文摘要哈希。监管问询"为什么智能体批准了这笔操作"时，这是标准应答材料。

### Q5: 多租户场景审计如何隔离？

审计记录带 `tenant_id`，查询接口强制租户过滤；跨租户审计（平台管理员）本身是 L3 高危操作，单独审计 + 双人授权。

---

**相关文档**：[安全加固](security-hardening.md) · [密钥管理](secrets-management.md) · [GDPR 合规](gdpr-compliance-guide.md) · [事件溯源](../20-advanced-patterns/event-sourcing.md)
