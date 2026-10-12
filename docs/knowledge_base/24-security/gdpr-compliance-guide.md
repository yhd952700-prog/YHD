# GDPR 合规指南

GDPR（欧盟《通用数据保护条例》）对处理欧盟居民个人数据的系统提出严格要求。智能体系统有特殊挑战：记忆系统长期保存对话内容、LLM 调用涉及跨境数据传输、自动化决策受第 22 条约束。本文档将 GDPR 条款映射为 LiuHao AI OS 的具体工程实践。（本文档为设计指引；LiuHao AI OS 的合规框架未建立，HUMAN DECISION REQUIRED，见 U36/U37/HD-06。）

> ⚠️ 本文档是工程指引而非法律意见。正式上线前请咨询数据保护律师。

---

## 目录

- [适用范围速判](#适用范围速判)
- [GDPR 原则到工程的映射](#gdpr-原则到工程的映射)
- [数据主体权利实现](#数据主体权利实现)
- [智能体系统的特殊议题](#智能体系统的特殊议题)
- [跨境传输](#跨境传输)
- [数据保护影响评估（DPIA）](#数据保护影响评估dpia)
- [泄露通知](#泄露通知)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 适用范围速判

满足任一即适用 GDPR：

- 你的组织在欧盟境内设立机构并处理个人数据
- 向欧盟境内的数据主体**提供商品/服务**（含免费服务）
- **监测**欧盟境内数据主体的行为（如行为分析、画像）

智能体系统注意：用户在欧盟、服务器在美国，照样适用。

---

## GDPR 原则到工程的映射

| GDPR 原则 | 工程落地 |
|-----------|---------|
| 合法性基础（Art.6） | 每类处理活动登记合法性基础（同意/合同必要/合法利益）；同意可撤回且与给予同等便利 |
| 目的限制 | 数据采集时声明用途；记忆系统打 `purpose` 标签，跨用途使用前重新评估 |
| 数据最小化 | 审计只存哈希（见[审计日志](audit-logging.md)）；表单只收必需字段；记忆 TTL |
| 准确性 | 提供数据更正入口；记忆支持用户修订 |
| 存储限制 | 每类数据定义保留期，到期自动删除或匿名化（下文配置） |
| 完整性与保密 | 加密（静态+传输）、访问控制、[密钥管理](secrets-management.md) |
| 问责制 | 处理活动记录（RoPA）、DPIA、审计日志 |

### 合法性与同意管理

```python
class ConsentService:
    """同意记录也是个人数据，需要审计与可撤回。"""

    async def grant(self, user_id: str, purpose: str, evidence: dict) -> None:
        await self._db.insert("consents", {
            "user_id": user_id, "purpose": purpose,
            "granted_at": now(), "evidence": evidence,   # 同意时的 UI 快照/文案版本
            "withdrawn_at": None})

    async def withdraw(self, user_id: str, purpose: str) -> None:
        await self._db.execute(
            "UPDATE consents SET withdrawn_at = now() "
            "WHERE user_id=$1 AND purpose=$2 AND withdrawn_at IS NULL",
            user_id, purpose)
        # 撤回即触发下游清理（事件驱动）
        await bus.publish("consent.withdrawn", {"user_id": user_id,
                                                "purpose": purpose})

@bus.subscribe("consent.withdrawn")
async def on_withdraw(event: dict) -> None:
    # 营销目的撤回：停止画像类记忆写入，删除相关画像
    if event["purpose"] == "profiling":
        await memory_kernel.delete_by_purpose(event["user_id"], "profiling")
```

---

## 数据主体权利实现

| 权利 | 条款 | SLA | 实现 |
|------|------|-----|------|
| 访问权 | Art.15 | 1 个月 | 数据导出入口（JSON/可读格式） |
| 更正权 | Art.16 | 1 个月 | 资料编辑 + 记忆修订 API |
| 删除权（被遗忘权） | Art.17 | 1 个月 | 级联删除流水线（下文） |
| 限制处理权 | Art.18 | — | 账号冻结标记：数据只存不用 |
| 数据可携带权 | Art.20 | 1 个月 | 结构化导出（机器可读） |
| 反对权 | Art.21 | — | 一键退出画像/营销 |
| 不受纯自动化决策约束 | Art.22 | — | 重大决策人类在环（下文详述） |

### 删除权：级联删除流水线

个人数据散落在多处：主库、记忆系统、缓存、备份、审计（豁免项）、下游 LLM 提供商。删除必须是**可证明的流水线**：

```python
ERASURE_STEPS = [
    # (步骤, 说明, 是否可补偿)
    ("verify_identity",   "验证请求者身份（防冒删）", True),
    ("snapshot_inventory","生成数据清单快照（删了什么要有记录）", True),
    ("freeze_account",    "冻结账号，阻止新数据产生", True),
    ("delete_profile",    "删除主库用户记录", True),
    ("delete_memories",   "四层记忆全量删除", True),
    ("purge_caches",      "Redis 键空间清理", True),
    ("request_subprocessors", "通知 LLM 提供商等分处理者删除（DPA 义务）", False),
    ("anonymize_audit",   "审计记录匿名化（user_id 打码，保留事实）", False),
    ("schedule_backup_expiry", "备份中的数据随轮转自然过期（≤90天），登记例外", False),
    ("confirm",           "向用户发送删除完成确认", False),
]

# 用 Saga 执行（见 saga-pattern.md）：每步幂等、可审计、失败可续跑
async def execute_erasure(user_id: str, request_id: str) -> None:
    ctx = await saga_orchestrator.run(
        steps=build_erasure_steps(user_id),
        initial_data={"user_id": user_id, "request_id": request_id})
    await audit.append(action="gdpr.erasure.completed",
                       actor={"kind": "system"}, target={"id": user_id},
                       details={"request_id": request_id, "saga_id": ctx.saga_id})
```

备份豁免说明：从备份中精确删除单条数据技术上不可行（且破坏备份完整性）。通行做法：备份随轮转自然过期（保留 ≤90 天），在 RoPA 与隐私政策中明示，恢复备份后需重放删除清单。

### 访问权：数据导出

```python
async def export_user_data(user_id: str) -> str:
    """生成完整数据包，返回限时下载链接。"""
    bundle = {
        "profile": await db.get_user(user_id),
        "consents": await consent_service.list(user_id),
        "memories": await memory_kernel.export_all(user_id),     # 各层记忆
        "agents": await agents.list_owned(user_id),
        "tasks": await tasks.list_by_user(user_id, limit=10000),
        "audit_trail": await audit.query(actor=user_id, limit=10000),
        "exported_at": now_iso(),
        "format_version": "1.0",
    }
    path = await encrypt_and_upload(user_id, bundle)   # 用户密钥加密
    await notify_download_link(user_id, path, ttl_hours=72)
    return path
```

---

## 智能体系统的特殊议题

### 第 22 条：自动化决策

> 数据主体有权不受**仅基于自动化处理、且对其产生法律效力或类似重大影响的决定**的约束。

落入范围的例子：自动审批贷款、自动筛选简历、自动定价歧视。**不落入**：拼写建议、内容摘要、信息检索。

工程合规三选一路径：

| 路径 | 实现 |
|------|------|
| 人类在环（推荐默认） | 重大决策智能体只出建议，人类审批后生效（[Slack 审批流](../22-integrations/slack-integration-guide.md)） |
| 合同必要 + 保障措施 | 明示逻辑、提供人工复核渠道、定期偏差审计 |
| 明示同意 | 高风险场景单独勾选同意，可随时退出 |

```python
DECISION_CLASSIFICATION = {
    # 决策类型 -> (是否重大, 合规路径)
    "loan_approval":      ("significant", "human_in_loop"),
    "resume_screening":   ("significant", "human_in_loop"),
    "content_moderation": ("significant", "human_review_channel"),
    "search_ranking":     ("not_significant", None),
    "doc_summarization":  ("not_significant", None),
}

async def check_article22(action: str, ctx: Context) -> None:
    significant, path = DECISION_CLASSIFICATION.get(action, (None, None))
    if significant == "significant" and path == "human_in_loop":
        if not ctx.human_approved:
            raise HumanApprovalRequired(action)
```

### LLM 训练数据问题

向 LLM 提供商发送的提示可能含个人数据。要求：

1. **DPA（数据处理协议）**：与提供商签署，明确其为"处理者"且**不得用 API 数据训练**（主流提供商均有企业级零训练承诺，务必确认合同条款）
2. **数据最小化**：提示组装时剥离不必要PII（`pii_redactor` 中间件）
3. **区域端点**：优先选择提供商的欧盟区域端点

```python
async def pii_redact(prompt: str, policy: str = "mask") -> str:
    """发送 LLM 前的 PII 剥离：可逆掩码（占位符映射存本地）。"""
    entities = await ner.detect_pii(prompt)          # EMAIL/PHONE/NAME/IBAN...
    mapping = {}
    for e in entities:
        placeholder = f"<{e.type}_{len(mapping)}>"
        mapping[placeholder] = e.text
        prompt = prompt.replace(e.text, placeholder)
    await pii_map_store.put(current_request_id(), mapping, ttl=3600)
    return prompt
```

### 记忆系统与画像（Profiling）

语义记忆天然构成"画像"。合规要点：

- 画像类记忆打 `purpose: profiling` 标签，与功能性记忆分离
- 用户提供"查看我的画像"入口（访问权）
- 反对权一键开关：关闭后停止画像写入并删除存量

---

## 跨境传输

数据出欧盟的合法机制（按推荐序）：

| 机制 | 说明 |
|------|------|
| 充分性认定 | 传输到欧盟认定的国家（如英国、瑞士、日本、欧盟-美国数据隐私框架认证企业） |
| SCC 标准合同条款 | 与境外接收方签署 2021 版 SCC，并做传输影响评估（TIA） |
| BCR 约束性公司规则 | 集团内部传输 |

工程落地：**默认数据驻留欧盟**（EU region 部署 + EU 端点 LLM），跨境路径逐条登记。

---

## 数据保护影响评估（DPIA）

智能体系统大概率触发 DPIA 义务（Art.35：系统性监测、大规模画像、自动化决策）。模板要点：

1. **处理活动描述**：数据流图（用户输入 → 记忆 → LLM → 输出 → 存储）
2. **必要性与比例性**：能否用更少数据达到目的？
3. **风险评估**：泄露、画像歧视、自动化决策错误的概率与影响
4. **缓解措施**：本文档中的技术控制清单
5. **咨询记录**：DPO（数据保护官）意见

DPIA 是活文档：架构重大变更（新增记忆层、新 LLM 提供商）时重审。

---

## 泄露通知

Art.33：**72 小时**内向监管机构通报（除非不太可能危害个人权益）；高风险时还需通知受影响个人。

工程准备：

```python
# 泄露检测即触发计时
@bus.subscribe("security.breach_detected")
async def breach_response(event: dict) -> None:
    await create_incident(event, severity="breach")
    await notify(dpo_channel, deadline=now() + timedelta(hours=72))
    # 影响评估所需数据随手可得是关键：
    # 受影响用户数、数据类别、是否加密、泄露途径
    report = await assess_blast_radius(event)
    await dpo.review_and_file(report)          # DPO 决定是否通报
```

加密是重要减责因素：泄露数据若已强加密且密钥未泄露，可能不构成"风险"。

---

## 配置示例

```yaml
# config/gdpr.yaml
gdpr:
  enabled: true
  dpo_contact: dpo@example.com
  supervisory_authority: "CNIL (France)"        # 主机构所在地

  retention:
    conversation_memories: 365d
    profiling_memories: 180d
    audit_logs: 365d                            # 哈希化
    backups: 90d
    erasure_requests: 3y                        # 删除行为本身要留证

  rights_sla:
    access_days: 30
    erasure_days: 30
    export_link_ttl_hours: 72

  article22:
    default_policy: human_in_loop
    significant_decisions: [loan_approval, resume_screening]

  llm:
    zero_training_confirmed: true               # DPA 条款已确认
    pii_redaction: mask
    eu_endpoints_only: true

  transfers:
    register:
      - { to: "LLM Provider US", mechanism: "SCC+TIA", data: "prompts(redacted)" }

  breach:
    notify_deadline_hours: 72
    auto_assess_blast_radius: true
```

---

## 最佳实践

1. **Privacy by Design 落地为标签体系**：数据从入库起带 `purpose`/`retention`/`pii_class` 标签，后续一切自动化（删除、导出、留存）由标签驱动。
2. **删除权做成流水线而非脚本**：Saga 执行、每步审计、完成有证明——监管问询时这是核心证据。
3. **同意要具体到目的**："我同意使用服务"不等于"我同意画像"；目的粒度同意 + 同等便利撤回。
4. **DPA 清单常更新**：每接一个 SaaS/LLM/集成，更新分处理者清单与隐私政策。
5. **备份删除走"轮转 + 恢复重放"模式**，并在 RoPA 中文档化。
6. **年度演练**：访问请求、删除请求、泄露响应各演一次，计时并复盘。

---

## 常见问题

### Q1: 我们用 LLM API，用户对话会被提供商用来训练吗？

取决于合同而非技术。企业级 API（Azure OpenAI、OpenAI Enterprise、Anthropic 商业版、Bedrock）默认不训练，但必须在 DPA 中确认"zero retention / no training"条款。免费/消费级 API 通常保留训练权利——生产禁止用。

### Q2: 审计日志要保留一年，算不算违反"存储限制"原则？

不冲突。Art.17(3)(e) 允许为"法律请求的成立、行使或抗辩"保留必要数据。关键是**目的限定**：审计数据不得用于画像等其他目的，且内容最小化（哈希化）。

### Q3: 用户要求删除，但他的数据已进入语义缓存怎么办？

语义缓存键含内容哈希而非用户 ID，无法按用户定位。对策：缓存 TTL 短（≤24h）+ 删除请求触发相关命名空间整体失效（版本号键 bump）+ 在 RoPA 中说明该机制。

### Q4: 智能体生成的内容里包含他人 PII（如总结会议纪要），算处理吗？

算。生成内容入库前过 PII 检测：他人 PII 按"代表数据主体处理"登记；提供内容主体的访问/删除通道（按 source 文档关联）。

### Q5: 只有几个欧盟用户，也要全套合规吗？

GDPR 没有小微豁免（记录义务对 <250 人组织有有限豁免，但高风险处理不适用）。务实路径：核心三件套先做——DPA 签署、隐私政策更新、删除/访问请求通道——其余按风险渐进补齐。

---

**相关文档**：[安全加固](security-hardening.md) · [审计日志](audit-logging.md) · [密钥管理](secrets-management.md) · [Saga 模式](../20-advanced-patterns/saga-pattern.md)
