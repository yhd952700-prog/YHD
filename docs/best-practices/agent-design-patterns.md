# 智能体设计模式

设计一个好的智能体，本质是在"自主性"与"可控性"之间找到正确的位置。本文档总结 LiuHao AI OS 实践中沉淀的智能体设计模式：单一职责、能力建模、提示工程结构、工具设计与人类在环。面向准备构建生产级智能体的开发者。

---

## 目录

- [设计原则总览](#设计原则总览)
- [模式一：单一职责智能体](#模式一单一职责智能体)
- [模式二：能力声明式建模](#模式二能力声明式建模)
- [模式三：结构化系统提示](#模式三结构化系统提示)
- [模式四：工具契约设计](#模式四工具契约设计)
- [模式五：人类在环分级](#模式五人类在环分级)
- [反模式清单](#反模式清单)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 设计原则总览

1. **窄而深优于宽而浅**：一个做好三件事的智能体胜过一个什么都能聊的
2. **能力显式声明**：能做什么、不能做什么，写在配置里而非埋在提示词里
3. **决策可追溯**：每个动作能回答"为什么"（策略 + 上下文 + 审批）
4. **失败可预期**：定义清楚何时放弃、何时升级给人类
5. **成本有意识**：智能体知道自己的 token 预算与配额边界

---

## 模式一：单一职责智能体

### 问题

"万能助手"式智能体：提示词几千字，能写代码能查数据能订会议室——结果是每个领域都表现平庸，且行为难以预测。

### 方案

按**决策域**拆分：每个智能体有清晰的能力边界、专属记忆空间、独立的评估指标。

```python
# agents/registry/researcher.yaml 描述的智能体定义
agent:
  id: researcher-01
  name: 研究分析师
  mission: |
    给定研究主题，产出结构化研究报告：检索、筛选、综合、引用。
    不执行写操作，不代表用户对外发言。
  capabilities: [web_search, doc_read, memory_recall, report_write]
  non_goals:                          # 明确不做什么，与能力同等重要
    - 发送任何对外消息
    - 修改代码仓库
    - 访问财务数据
  memory_scope: agent_private          # 私有记忆空间
  budget:
    max_tokens_per_task: 100000
    max_cost_per_day_usd: 5
```

### 拆分判断标准

| 信号 | 动作 |
|------|------|
| 系统提示超过 1500 token | 拆 |
| 工具数超过 12 个 | 拆（LLM 工具选择准确率随数量下降） |
| 两类任务需要冲突的行为风格（谨慎 vs 果断） | 拆 |
| 不同任务的评估指标不同 | 拆 |

---

## 模式二：能力声明式建模

能力（Capability）是智能体可调用的功能单元，声明式定义让治理（权限、审批、配额）有了抓手：

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Capability:
    name: str
    description: str                  # 给 LLM 看的：何时用
    params_schema: dict               # JSON Schema：参数强校验
    risk_level: Literal["low", "medium", "high", "critical"]
    side_effects: bool                # 有无副作用（写操作）
    cost_hint: Literal["free", "cheap", "expensive"]
    required_approval: str | None = None   # high/critical 必填审批策略

WEB_SEARCH = Capability(
    name="web_search",
    description="搜索公开网页。用于获取最新公开信息；不可访问内网。",
    params_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 500},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"]},
    risk_level="low",
    side_effects=False,
    cost_hint="cheap",
)

DEPLOY_TO_PROD = Capability(
    name="deploy_to_prod",
    description="将指定版本部署到生产环境。不可逆，需人类批准。",
    params_schema={...},
    risk_level="critical",
    side_effects=True,
    cost_hint="free",
    required_approval="ops_two_person",
)
```

收益：

- **策略引擎可推理**：`risk_level` + `side_effects` 驱动自动门禁（见[安全加固](../knowledge_base/24-security/security-hardening.md)）
- **提示词更短**：能力描述即工具描述，不用在系统提示里重复说教
- **审计自动完整**：每次调用按 capability 维度记录

---

## 模式三：结构化系统提示

生产级系统提示的骨架（实测 600~1200 token 为佳）：

```markdown
# 角色
你是研究分析师智能体 researcher-01，服务于平台团队的调研需求。

# 任务流程
1. 理解问题 → 2. 检索（web_search / memory_recall）→ 3. 交叉验证
→ 4. 综合报告（引用来源）→ 5. 自我检查清单后输出

# 边界（硬性）
- 不执行任何写操作
- 不访问 financial/* 数据域
- 检索结果冲突时：并列呈现，标注分歧，不做裁决
- 信息不足时：明确说明"未知"，禁止编造

# 输出契约
- 固定结构：摘要 / 关键发现 / 证据与引用 / 未决问题
- 引用格式：[n] 标题, URL
- 长度 ≤ 1500 字

# 升级条件（何时停止并求助人类）
- 连续 3 次检索无有效结果
- 涉及法律合规判断（合规框架未建立，HUMAN DECISION REQUIRED，见 U36/U37）
- 任务超出能力声明范围
```

要点：

- **边界用肯定句写**："只做 X"优于"不要做 Y, Z, W..."（否定枚举永远不完整）
- **升级条件显式化**：智能体知道何时该停，比知道何时该做更重要
- **输出契约可机检**：固定结构让下游（或评估器）能程序化校验

---

## 模式四：工具契约设计

工具是智能体与现实世界的接口，设计质量直接决定可靠性。

### 好工具的特征

```python
# ✅ 好的工具：原子、幂等、自描述、错误可操作
async def search_memory(query: str, layer: str = "semantic",
                        top_k: int = 5) -> dict:
    """在记忆库中检索。
    Returns:
        {"results": [...], "truncated": bool, "next_cursor": str|None}
    Errors:
        rate_limited: 调用过频，retry_after 秒后重试
        layer_invalid: layer 必须是 working|episodic|semantic|procedural
    """

# ❌ 坏的工具：语义模糊、副作用隐藏、错误不可恢复
async def handle_stuff(input: str) -> str:
    """处理一些事情。"""
```

### 错误返回设计

LLM 会读错误信息并决定下一步——**错误信息是提示词的一部分**：

```python
# ❌ 差的错误：LLM 只能重试同样的错误
raise Exception("invalid input")

# ✅ 好的错误：告诉 LLM 怎么修正
return {"error": "date_format_invalid",
        "message": "日期格式必须是 ISO 8601（如 2026-10-12），收到的是 '10/12/26'",
        "recoverable": True}
```

### 工具数量控制

| 工具数 | 选择准确率（经验值） |
|--------|--------------------|
| ≤ 8 | >95% |
| 8~12 | ~90% |
| > 15 | 急剧下降，需要路由层 |

超过 12 个时加**工具路由**：先用小模型/规则选出 3~5 个候选工具再交给主模型。

---

## 模式五：人类在环分级

不是所有决策都需要人类。按影响分级：

```
L0 全自动     只读操作：检索、分析、摘要
L1 事后通知   低风险写操作：打标签、内部评论 → 执行 + 通知
L2 事前审批   中风险：对外发消息、创建资源 → 批准才执行
L3 双人审批   高风险：生产变更、支付、删除 → 两人批准
L4 禁止       不可恢复且无补偿：物理删除不可分区的数据 → 从能力中移除
```

```python
async def execute_with_hil(agent: Agent, call: ToolCall) -> Result:
    level = HIL_MATRIX[(call.capability.risk_level, agent.trust_tier)]
    if level >= 2:
        approval = await approval_service.request(
            action=call, requester=agent.id,
            channel="slack", quorum=2 if level == 3 else 1,
            timeout_s=1800)
        if not approval.granted:
            return Result.denied(approval.reason)
    return await call.execute()
```

信任分层（trust_tier）：新智能体默认 tier 0（一切 L2+ 审批），随运行记录良好逐步提升（L1 操作免审批范围扩大）——**信任是挣来的，不是配置出来的**。

---

## 反模式清单

| 反模式 | 症状 | 处方 |
|--------|------|------|
| 提示词圣经 | 系统提示 3000+ token，充满 "IMPORTANT!!!" | 结构化重写，边界进策略引擎 |
| 工具沼泽 | 30+ 工具互相重叠 | 合并、路由层、按职责拆智能体 |
| 无限重试循环 | 失败后反复尝试同一方法烧 token | 预算硬上限 + 升级条件 |
| 记忆垃圾桶 | 什么都写入长期记忆 | 写入门禁（见[记忆管理](memory-management-best-practices.md)） |
| 隐形副作用 | "查询"工具其实会写数据 | 能力声明 side_effects，策略拦截 |
| 上帝智能体 | 一个智能体管所有租户所有事 | 按租户/职责拆分 |

---

## 配置示例

```yaml
# config/agent_design.yaml
design_defaults:
  system_prompt_max_tokens: 1500
  max_tools_per_agent: 12
  tool_router_threshold: 8

budgets:
  default:
    max_tokens_per_task: 50000
    max_tool_calls_per_task: 30
    max_wall_time_per_task_s: 1800
    max_cost_per_day_usd: 5

hil:
  matrix:
    critical: always_approval
    high: approval_unless_trusted
    medium: notify_after
    low: autonomous
  trust_tiers:
    new: 0
    promotion_criteria:
      tasks_completed: 500
      approval_override_rate_below: 0.05     # 人类推翻率 <5%
      incident_free_days: 30

escalation:
  max_consecutive_failures: 3
  unknown_answer_policy: explicit_unknown    # 禁止编造
```

---

## 最佳实践

1. **先写 non_goals 再写 goals**：边界想清楚，能力自然清晰。
2. **用真实任务评测**：20 个代表性任务建评测集，提示词改动前后对比通过率（见[测试策略](testing-strategies.md)）。
3. **版本化智能体定义**：提示词、能力、预算作为一个整体版本化，回滚即回滚全部。
4. **观察真实使用**：每周抽查 10 条任务轨迹，设计意图与真实行为的差距都在这里。
5. **预算硬上限**：再聪明的智能体也可能陷入循环——token/调用次数/时间三重硬顶。
6. **新智能体从只读起步**：先证明判断质量，再逐步开放写能力。

---

## 常见问题

### Q1: 一个智能体 vs 多智能体，怎么选？

默认单智能体。只有当任务需要**并行探索**（研究多个方向）、**对抗验证**（生成者+批评者）或**领域隔离**（不同工具集/权限）时才上[多智能体](multi-agent-coordination.md)。多智能体的协调成本是真实的。

### Q2: 系统提示用英文还是中文？

跟随模型主力语言与团队维护语言。关键是**一致性**：混用语言会稀释指令遵循。LLM 理解多语没问题，但结构化程度（标题、列表、契约）比语种更重要。

### Q3: 智能体表现不稳定，同样输入有时好有时差？

按序排查：温度是否 >0.3（降）；上下文是否含随机检索结果（固定 top-k + 排序）；工具返回是否含时间戳/随机 ID（归一化）；最后才调提示词。评测集是前提——没有评测集就没有"变好还是变差"的判定。

### Q4: 该给智能体多少上下文历史？

不是越多越好。实测：超过 ~20 轮对话后指令遵循开始漂移。策略：滑动窗口 + 旧对话摘要进工作记忆；关键事实进语义记忆按需检索。

### Q5: 如何设计智能体的"性格"？

生产智能体不需要性格，需要**行为一致性**。输出契约（格式、长度、引用规范）比语气重要。面向 C 端的陪伴型除外——那时性格用少量示例（few-shot）定义比大段描述有效。

---

**相关文档**：[记忆管理最佳实践](memory-management-best-practices.md) · [多智能体协作](multi-agent-coordination.md) · [错误处理模式](error-handling-patterns.md) · [测试策略](testing-strategies.md)
