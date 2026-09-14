# 配额记账闭环设计（QUOTA-ACCOUNTING-DESIGN）

> 状态：**设计稿，待 boss 拍板后实现**。本轮只做调研与设计，未改生产代码。
> 关联：`docs/POLICY-RULE-OBSERVABILITY-DESIGN.md` §6.5 边界②、§6.6。

## 1. 要解决什么

`quota_enforcement` 规则已在 `30df706b` 接线成真控制，并在 `d964231a` 加固。但它现在
**只在配额被人为设得很紧时才咬**，因为预算那一侧是死的：

- `ResourceQuotaManager` 的 `used` / `reserved` **没有任何生产调用点在记账**；
- 系统级 `COST` 配额恒为 `available = 100.0`（`limit=100, used=0, reserved=0`）；
- 于是"剩余预算"永远等于"总额"，护栏形同虚设——**规则是对的，数据是死的**。

本设计要让配额真正被消耗，使护栏在默认配置下也有意义。

## 2. 实测现状（证据）

### 2.1 记账相关的内核动作（`src/kernels/resource/__init__.py`）

| 方法 | 行 | 语义 | `@kernel_action` | 白名单 |
|---|---|---|---|---|
| `create_quota` | 139 | 建配额 | `resource.create_quota` | ✗ DENY |
| `allocate` | 185 | `reserved += amount`（:212），返回 Allocation | `resource.allocate` | ✗ DENY |
| `commit` | 251 | `reserved -=` / `used +=`（:267-268） | `resource.commit` | ✗ DENY |
| `release` | 273 | `used -=`（:294-295/:311-312） | `resource.release` | ✓ **ALLOW** |

模块级 `commit_allocation`（:475）→ `manager.commit`，**零调用点**。
另有一套**影子预算** `src/ai/economy.py:54 BudgetEngine.reserve/consume`，与 Quota **不连通**。

### 2.2 白名单（`src/kernels/policy/__init__.py:83`，14 项）

`context.compress/process`、`evaluation.evaluate`、
`event.publish/retry_dead_letter/subscribe/unsubscribe`、
`execution.create_checkpoint/execute`、`memory.compress/store`、`network.route`、
**`resource.release`**、`security.decide_access`。
黑名单在 :118（29 项）。规则 `internal_service_allow`（:407-421，precedence 900）用它**放行**。

实测：`resource.release → ALLOW(internal_service_allow)`；
`resource.commit / allocate / create_quota → DENY(default_deny)`。

### 2.3 `resource.commit` 不在白名单是**有意的**（不是遗漏）

- `policy/__init__.py:71-72` 注释：「create/allocate/commit move quota -> denied」；:66-73 的分类
  规则写明"改权限/破坏类不进白名单，新动作**默认拒绝、须显式登记**"。
- 引入提交 `6f7a3922`（2026-09-11）：同一次 diff **同时**加入 ALLOWED 的 `resource.release`
  与 DENIED 的 `allocate/commit/create_quota`。
- 守护测试 `tests/kernels/policy/test_internal_service_policy.py:226`（完备性）+ :88-92。
- `src/kernels/_risk_classification.py:206`：`resource.commit` = **MEDIUM / 需人工确认**。

⇒ **把 `resource.commit` 塞进白名单 = 推翻一个有意的安全决定**，必须 boss 明确拍板，我不擅自改。

### 2.4 provider **没有真实 token usage**（实测）

```
chat() 返回类型 = str    has .usage? False
stream chunk[0] 类型 = str（整串，仅 1 个）
```

- `BaseProvider.chat:121` 标注 `-> str`；
- `OllamaProvider.chat:407-408` 只取 `result["message"]["content"]`，**丢弃**
  `prompt_eval_count` / `eval_count`；
- `OllamaProvider.chat_stream:441-446` 见 `done:true` 即 break —— **usage 就在被丢弃的末帧**。

⇒ **今天只能记"估算花费"，不能记"实际花费"。** 这是必须写在文档里的诚实边界（见 §7）。

### 2.5 记账点与幂等（`src/ai/liuhao.py`）

- `chat()` 唯一收尾 `_commit_turn`(:425)，仅调用一次 ⇒ 天然幂等；
- `chat_stream` 在 :388 同款收尾，但位于**生成器末端** —— SSE 客户端中断则**永不执行**
  ⇒ **漏记**（失败方向安全：少扣不多扣）；
- 工具多轮 loop（:352-378）每轮覆盖 `final_reply`(:360)，故放在收尾处即覆盖全部轮次。

## 3. 设计

### 3.1 记什么：先估算，同时把真实 usage 打通（推荐）

| 方案 | 数据源 | 代价 | 诚实度 |
|---|---|---|---|
| A 估算 | 复用 `_quota_authorize_kwargs` 的 `tokens_in/tokens_out` × 单价 | 零改动 | 记的是**预估**花费 |
| B 真实 | provider 暴露 usage（ollama 的 `prompt_eval_count/eval_count` 已在响应里，只是被丢） | 改 `providers.py` 让 `chat` 返回 `(text, usage)` 或在末帧带出 | 记的是**实际**花费 |

**推荐：A 先落地（让护栏立刻活），B 的 provider 改动同轮一起做（数据源可切换）。**
A→B 切换只是换数据源，不改记账接口。

### 3.2 记在哪一步：`_commit_turn`

`liuhao.py:425`（`chat`）与 `:388`（`chat_stream`）的既有收尾点。理由：唯一、幂等、
已有审计上下文（`_commit_turn` 内 :451 已记 `chars_in/chars_out`，天然带得动 cost）。

### 3.3 走哪条 API —— **关键决策点**

语义正确的两步是 `allocate()` → `commit(allocation_id)`（`reserved → used`）。
但两者都在 DENIED 名单。三个选项：

| 选项 | 做法 | 评估 |
|---|---|---|
| 1 | 把 `resource.commit` + `allocate` 加进 `INTERNAL_SERVICE_ALLOWED_ACTIONS` | ❌ 推翻有意的安全决定；`commit` 是通用"reserved 转 used"，放开后可挪用到别的配额 |
| 2 | 记账由**已注册的服务/人类主体**发起，不走 internal-service 通配 | 中：需先确认 agent 主体在该动作上的判决（本轮未实测） |
| 3 | **新增单一语义动作 `resource.account_spend`** | ✅ 推荐：只做"记录一笔已发生花费"，边界清晰、可单独登记与审计、可单独回滚 |

推荐 **选项 3**。它不动既有白名单语义，且把"记账"与"配额生命周期"分开——
后者正是当初刻意拒绝的理由。

### 3.4 降级语义

记账失败 ⇒ **WARNING 出声 + 不阻断对话**。

理由与 `economy_inputs` 一致：这是收尾路径，记账失败不该让助手整体倒下；
但**绝不能静默吞**——静默漏记 = 配额永不消耗 = 护栏假活，比不记更危险。

## 4. 验收判据

1. 连跑 N 轮 `chat`，`ResourceQuotaManager` 的 `COST` 配额 `used` **单调增长**；
2. 把某主体 `COST` 配额 `limit` 设小，跑几轮后 `available` 归零 ⇒
   下一轮 `chat` 被 `quota_enforcement` 拒（`status="denied"`）——**端到端证明护栏活了**；
3. 让记账路径故意抛异常 ⇒ 对话仍 `completed`，且日志出现 WARNING；
4. 反橡皮图章：把记账调用删掉，(1)(2) 必须失败。

## 5. 实现清单（拍板后）

| 文件 | 改动 |
|---|---|
| `src/kernels/policy/__init__.py` | 白名单加 `resource.account_spend`（:83 附近），同步黑名单分类注释 |
| `src/kernels/resource/__init__.py` | 新增 `account_spend(...)`（:251 `commit` 附近），`@kernel_action("resource.account_spend")` |
| `src/ai/liuhao.py` | `_commit_turn`(:425) 与流式收尾(:388) 调记账；成本复用 `_quota_authorize_kwargs` 的量 |
| `src/ai/providers.py`（B 方案） | `OllamaProvider.chat:407` 与 `chat_stream:441` 带出 usage |
| `tests/kernels/policy/` + `tests/` | 新增记账闭环测试 + 降级不阻断测试 + 反橡皮图章 |

## 6. 需 boss 拍板

1. **选项 1 / 2 / 3 选哪个**（我推荐 3）。
2. **是否同轮把 provider 真实 usage 打通**（我推荐是，但会改 `providers.py` 返回结构）。
3. **模型单价** —— 见 §7 与定价议题：没有价格，记账金额只能是 0 或省略。

## 7. 诚实的边界

- **A 方案记的是"预估花费"，不是"实际花费"。** 在真实 usage 打通前，文档与日志都应这么写，
  不得把估算说成实测。
- **流式中断会漏记**（少扣）。这是安全方向的偏差，可接受；若要兜底需额外的收尾钩子。
- **本轮未实测**：agent 主体（非 internal-service）在 `resource.*` 动作上的实际判决；
  B 方案下 openai 兼容端点是否同样返回 usage（只实测了 ollama 路径）。
- **不解决**：配额初始值/额度该设多少（属运营决策）。

## 8. 实现状态（Round 98，已落地）

按 §5 清单实现，**采用选项 3**（`resource.account_spend`），未动 `allocate` / `commit`
的白名单决定。实测结果：

| 清单项 | 状态 |
|---|---|
| `src/kernels/policy/__init__.py` 白名单加 `resource.account_spend` | ✅ 已加（注释同步） |
| `src/kernels/_risk_classification.py` 登记为 LOW | ✅ 已加（LOW 与白名单强交叉校验通过） |
| `src/kernels/resource/__init__.py` 新增 `account_spend` | ✅ 已加（只把 `used` 往上推；`amount <= 0` 直接返回 0） |
| `src/ai/liuhao.py` `_commit_turn` 记账 | ✅ 已完成轮次按**放行时评估的同一个数**记账 |
| `src/ai/providers.py` 真实 usage（B 方案） | ❌ **未做** —— 仍记预估成本，见 §7 |
| 测试 + 反橡皮图章 | ✅ `tests/test_quota_accounting.py`（14 项） |

同步更新的既有计数（新增第 44 个内核动作带来的连锁）：
`tests/kernels/test_risk_classification.py`（43→44、LOW 14→15）、
`scripts/verify_d8_risk_classification.py`（同两处）。

**验收判据的达成情况（对照 §4）**：

1. ✅ 完成的轮次让 `COST` 配额 `used` 单调增长（`TestAccountSpend`）。
2. ✅ 端到端：`available` 不足 ⇒ `chat` 返回 `status="denied"`（早前 Round 94 已证；
   本轮补上"被拒的轮次不计费"）。
3. ✅ 记账路径抛异常 ⇒ 对话仍 `completed` 且 WARNING 出声
   （`test_a_bookkeeping_failure_cannot_break_the_turn`）。
4. ✅ 反橡皮图章：关掉 `_commit_turn` 的调用 ⇒ 3 项失败；把 `account_spend` 的
   `used += amount` 改成 0 ⇒ 6 项失败。

**未做（诚实记录）**：真实 token usage 回传（§7 第一条）；`runtime_loop` 与
`network_gateway.delegate` 的成本接入。
