# 后端 OpenAPI 契约 × 前端请求 对账审计（2026-11-08）

> **模式**：只读。未修改 `src/`、`apps/`、`installer/`、`packaging/` 下任何文件。
> **快照说明**：仓库有多条工作线并行改代码，本报告对账的是当前工作树快照。发现的
> 半成品只记录、不修复（见 §6）。

## 0. 审计范围与方法

对 `apps/console/console/src` 内所有出网调用点做了全量枚举，并按调用方式分组核对：

| 调用方式 | 代表文件 |
|---|---|
| `apiFetch` / `apiFetchWithStatus` / `useApi` | `lib/api.ts`、`lib/operator.ts`、`lib/knowledge.ts`、`lib/diagnostics.ts`、`lib/upgrade.ts`、各 `pages/*` |
| 裸 `fetch` | `lib/auth.ts`（登录/登出/自助注册/恢复）、`lib/chatClient.ts`（SSE）、`lib/api.ts`（就绪探测） |
| `XMLHttpRequest` | `lib/knowledge.ts`（文档上传进度） |
| 独立错误解析 | `lib/accounts.ts`（`AccountApiError`）、`lib/errorMessage.ts`（`ApiError` 共用） |

核对对象：`src/gateway/*.py` 中挂载到 `/v1` 的 FastAPI 路由、请求模型（Pydantic）、
响应字典、异常处理器，以及 `src/kernels/_enforcement.py` 等被直接透出的结构。

---

## 1. 结论

**真断裂：4 个独立根因，5 处用户可见故障。** 其中 2 处是"功能直接坏掉"，不是提示文案问题。

| 编号 | 严重度 | 现象 | 根因 | 用户能否直接碰到 |
|---|---|---|---|---|
| H-1 | 高 | 登录页「创建账户」**100% 失败**（422） | 前端发 `secret`，后端必填 `password` | 能。新用户完全无法自助开户 |
| H-2 | 高 | **含目标的项目无法删除**（强制确认弹窗永不出现） | 前端 `ApiError.code` 恒为 `null`，判断 `project_has_goals` 永不成立 | 能。删除流程卡在 409 |
| M-1 | 中 | 所有 422 表单校验**没有可读提示**，只显示「操作没有完成…」 | 后端 422 的 `detail` 是 `[{loc,msg,type}]`，前端只认 `message`/中文字符串 | 能。填错表单时不知道错在哪 |
| M-2 | 中 | 管家派活失败提示**退化成泛化文案** | 与 H-2 同根因：稳定错误码取不到 | 能（失败路径），成功路径不受影响 |
| M-3 | 中 | 运营页「生效等级」**恒显示 `—`** | 前端读 `enforced_tiers`，后端返回 `enforced_actions` | 能。信息缺失但不阻断操作 |

**没有发现的问题**（同样重要）：

- **认证头没有漏带**：所有前端请求都经 `tokenHeaders()` 双写令牌；XHR 上传手动补了
  `Authorization`；聊天 SSE 也补上了（曾是历史缺口，现已修复）。
- **没有调用后端不存在的路径**，也没有方法错配（GET/POST/PUT/PATCH/DELETE 全部吻合）。
- **没有前端仍在使用的废弃接口**：非 `/v1` 的 `/knowledge`、`/api/*` 旧路由均未被控制台调用。
- 除上述 4 个根因外，其余端点的路径、请求体字段、响应读取路径均一一对上（见 §5 附录）。

---

## 2. 高危（P0）

### H-1 自助注册：前端发 `secret`，后端要 `password` → 每次都是 422

**前端位置**

- [auth.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/auth.ts#L423-L434)：`registerSelf()` 组装请求体
  ```ts
  body: JSON.stringify({ principal, secret, client: options.client })
  ```
- [Login.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/components/Login.tsx#L901-L905)：创建账户表单调用
  `registerSelf(principal.trim(), secret, { remember, client: mode })`。
- 表单变量名叫 `secret`（用户输入的是「密码」），语义上也错位。

**后端位置**

- [accounts.py](file:///d:/LiuHao-AI-OS/src/gateway/accounts.py#L117-L125)：`SelfRegisterRequest`
  ```python
  principal: str = Field(..., min_length=1, max_length=64)
  password:  str = Field(..., min_length=8, max_length=256)
  display_name: Optional[str] = Field(None, max_length=80)
  model_config = {"extra": "ignore"}
  ```
- [accounts.py](file:///d:/LiuHao-AI-OS/src/gateway/accounts.py#L591-L617)：`POST /self-register` 只读
  `payload.password` 与 `payload.display_name`。

**具体差异**

1. 前端字段名 `secret`，后端字段名 `password`。
2. 后端 `extra="ignore"`：多发的 `secret`、`client` 被静默丢弃，不报"未知字段"。
3. 后端 `password` 是必填项 → 缺字段直接 422 `Field required`。
4. 前端 `selfRegistrationMessage()` 不处理 422（只特判 403/404/501/409），于是叠加
   M-1，用户看到的是通用兜底文案，连"缺密码"都提示不出来。
5. 成功响应字段是对的（`token` / `access_token` / `account` / `notice`），只是永远走不到。

**影响**：登录页「创建账户」标签页对所有用户失效；这也是唯一不依赖邀请码的开户入口。

**旁注**：`auth.ts` 该处注释写着「契约（后端实现中，前端先按此写）」——这是并行开发
留下的半成品：前端按计划稿写了 `secret`，后端落地时改成了 `password`，前端没跟上。
`403 已关闭自助注册` 这一分支靠 HTTP 状态兜住了，所以关闭提示仍然正常；坏的只是主流程。

---

### H-2 删除含目标的项目：`ApiError.code` 恒为 `null`，强制确认不可达

**前端位置**

- [Projects.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Projects.tsx#L182-L200)：删除流程
  ```ts
  if (err instanceof ApiError && err.status === 409 && err.code === 'project_has_goals') {
    const ok = window.confirm(...)   // 永不进入
    await deleteProject(id, true)    // 永不执行
  }
  ```
- [errorMessage.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/errorMessage.ts#L101-L105)：
  ```ts
  if (record.detail && typeof record.detail === 'object' && !Array.isArray(record.detail)) {
    const raw = (record.detail as Record<string, unknown>).code
    if (typeof raw === 'string' && raw.trim()) code = raw.trim()
  }
  ```
  只从 **`detail.code`** 取码。
- [api.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/api.ts#L155-L158)：`apiFetchWithStatus`
  用 `readError` 的 `parts.code` 构造 `ApiError`。

**后端位置**

- [projects.py](file:///d:/LiuHao-AI-OS/src/gateway/projects.py#L694-L710)：抛结构化 409
  `deny(409, "project_has_goals", "...", goal_count=..., goals=...)`。
- [main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1601-L1625)：`structured_http_error_handler`
  调用 `structured_error_body()`，把结构化 detail **摊平到顶层**。
- [account_context.py](file:///d:/LiuHao-AI-OS/src/gateway/account_context.py#L125-L135)：
  ```python
  body = dict(detail)                        # {code, message, goal_count, goals}
  body.setdefault("detail", body.get("message"))   # detail 变成字符串
  ```
  最终响应是 `{code: "project_has_goals", message: "...", detail: "...", goal_count, goals}`。

**具体差异**

- 后端把 `code` 放在**顶层**；前端 `describeFailure` 仍从 **`detail.code`** 取值。
- 此刻 `detail` 已被改成中文字符串，`typeof record.detail === 'object'` 为 false → `code = null`。
- 于是 `err.code === 'project_has_goals'` 永远不成立，`else` 分支只显示后端 message，
  用户拿不到"确认影响面后强制删除"的入口。
- 中文字符串本身能正常上屏（`describeDetail` 对字符串有效），所以用户能看到"还有 N 个目标"，
  但**没有下一步按钮**——功能是断的，不是文案问题。

**反证（同一仓库内两套解析互不一致）**：

- [accounts.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/accounts.ts#L150-L172) 读的是
  **顶层 `body.code`**，与新的后端形状一致 → 账号页的稳定错误码是正常的。
- 说明 `errorMessage.describeFailure` 是**漏改的一方**，而不是后端契约有问题。
- 附带影响：`isRouteMissing` 判定 `404 && code === null`；账号路由若返回结构化 404（有 code），
  不会误落桩，逻辑仍正确。此判定不受本根因影响。

---

## 3. 中危（P1）

### M-1 422 校验错误：后端给 `[{loc,msg,type}]`，前端只认 `message`

**前端位置**

- [errorMessage.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/errorMessage.ts#L45-L67)：`describeDetail`
  - 字符串：只保留含中文的；
  - 数组：逐项递归；
  - 对象：**只读 `record.message`**，且要求含中文。
- [errorMessage.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/errorMessage.ts#L125-L142)：无文案时按状态码兜底。

**后端位置**

- [main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1629-L1638)：统一 422 处理器
  ```python
  {"detail": public_validation_errors(exc.errors())}
  ```
- [main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1358-L1375)：`public_validation_errors`
  返回 `{"loc": [...], "msg": "...", "type": "..."}`（敏感字段只保留这三项）。

**具体差异**

- 422 数组项使用 `msg`，前端只认 `message` → 每项都映射成 `''`。
- `msg` 是英文（`Field required` / `String should have at least 8 characters`），
  即便改读 `msg` 也会被"仅保留中文"过滤掉。
- 结果：所有 422 都落到 `操作没有完成，请稍后重试。…`，用户不知道哪个字段错了。
- 典型复现：H-1 的自助注册（缺 `password`）、模型表单超时越界、口令少于 8 位等。

**建议方向（供后续实现方参考，本审计不修）**：后端在 422 里同时给出中文可读 `message`
（或前端把 `loc` 转成字段名 + 中文规则说明），而不是让两层都退化成"通用失败"。

---

### M-2 管家派活/目标操作：稳定错误码分支永不触发

**前端位置**

- [Butler.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Butler.tsx#L154-L178)：`goalWorkMessage`
  ```ts
  const code = error instanceof ApiError ? error.code : null
  switch (code) { case 'agent_not_found': ... }
  ```
  覆盖了 `agent_not_found` / `employee_not_found` / `employee_disabled` /
  `agent_unavailable` / `employee_unavailable` / `assign_target_required` /
  `task_not_retryable` / `goal_running` / `task_not_found` / `goal_not_found`。

**后端位置**

- [ai_management.py](file:///d:/LiuHao-AI-OS/src/gateway/ai_management.py#L254-L281)：`GoalWorkError`
  与 `_http_from_goal_error()` 输出 `{"code", "message"}`；
- 经 [main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1601-L1625) 摊平后，`code` 在顶层。

**具体差异**

- 与 H-2 完全同一根因（`describeFailure` 读 `detail.code`）。
- 后果：`switch` 全部落 `default`，返回 `${fallback}：${errorText(error)}`。
- 用户仍能看到后端的中文 message（`describeDetail` 认字符串），所以**不是黑屏**；
  但"已停用请先去启用 / 暂停中请先恢复 / 不是失败态"这些**可执行指引**全部丢失，
  只剩"派活失败：这位员工已停用，不能接受指派"这类原始句子。
- 因为没有独立 UI 行为依赖 `code`（只是文案选择），所以定为中危；H-2 有行为分支，定为高危。

---

### M-3 运营页「生效等级」：前端读 `enforced_tiers`，后端返回 `enforced_actions`

**前端位置**

- [Operations.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Operations.tsx#L370-L375)：
  ```tsx
  {Array.isArray(data.enforced_tiers) && data.enforced_tiers.length > 0
    ? data.enforced_tiers.join(' / ') : '—'}
  ```
- [contracts.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/contracts.ts#L252-L258)：
  ```ts
  export interface EnforcementPayload {
    enabled?: boolean
    env_var?: string
    intercepting?: boolean
    enforced_tiers?: string[]
  }
  ```

**后端位置**

- [policy.py](file:///d:/LiuHao-AI-OS/src/gateway/policy.py#L522-L525)：`GET /v1/policy/enforcement`
  直接 `return describe_enforcement()`。
- [_enforcement.py](file:///d:/LiuHao-AI-OS/src/kernels/_enforcement.py#L201-L218)：`describe()` 返回
  ```python
  {"env_var", "spec", "enabled", "enforced_actions", "count",
   "exempt_actions", "config_error"}
  ```

**具体差异**

1. 字段名不匹配：`enforced_tiers`（前端） vs `enforced_actions`（后端）→ 恒为 `—`。
2. 语义也不匹配：后端给的是**动作名列表**（如 `fs.write`），不是风险**等级**；
   即使只把字段名改对，"生效等级"这个标签下的内容仍是动作串。
3. `intercepting` 在类型里声明但页面未使用，属冗余声明（后端也没有该字段）。
4. 同一响应的 `enabled`、`env_var` 读取正确，所以"拦截开关 / 环境变量"两行是正常的。

---

## 4. 低危（P2）与提示

### L-1 聊天 `user_id`：前端仍在发，后端明确忽略

- 前端：[chatClient.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/chatClient.ts#L72-L78) 把
  `userId` 写进 `POST /v1/chat/stream` 请求体；[chatClient.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/chatClient.ts#L195-L203)
  把它拼进 `/v1/chat/history` 查询串。
- 后端：[chat.py](file:///d:/LiuHao-AI-OS/src/gateway/chat.py#L219-L225) 注释与实现都写明
  「主体只来自身份令牌；请求体里的 `user_id` 一律不采信」。
- 判断：**废弃参数**，不造成越权、不造成 403。建议后续清理 localStorage 里的
  `liuhao.user_id` 与调用方传参；当前保留也无害。响应里的 `user_id` 恒等于令牌 `principal`，
  前端读取 `SessionHistory.user_id` 不会出错。

### L-2 知识库上传失败：错误码丢失

- [knowledge.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/knowledge.ts#L513-L545)：XHR 分支用
  `new ApiError(xhr.status, parts.message)`，未传 `parts.code`。
- 影响：上传失败的调用方若按 `code` 分支（如低质量提取 `low_quality_extraction`），
  会拿不到码。目前上传主流程只展示 `message`，未触发可见故障。
- 另：`parseJobRow` 读 `row.error_code`（见 [knowledge.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/knowledge.ts#L602-L676)），
  这是**作业轮询接口**的字段，与上传 XHR 无关，那条路径是对的。

### L-3 全局只读横幅文案与已具备的 `view_account` 能力不一致

- [AccountChrome.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/components/AccountChrome.tsx#L65-L73) 写
  「视角端点尚未上线；当前页面仍显示你自己的数据」。
- 事实：[projects.py](file:///d:/LiuHao-AI-OS/src/gateway/projects.py#L582-L617) 已支持
  `GET /v1/projects?view_account=`，且 [Projects.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Projects.tsx#L84-L92)
  已在用。
- 判定：App 级只读模式（`viewAs`，见 [App.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/App.tsx#L208-L215)）
  确实不切数据源，所以文案方向没错；但"端点未上线"已过时，属**文案滞后**，不是契约断裂。

### L-4 `GET /v1/accounts` 返回 `members` 而非 `accounts`

- 后端 [accounts.py](file:///d:/LiuHao-AI-OS/src/gateway/accounts.py#L665-L676) 返回
  `{"members": [...], "count": n}`；前端 [accounts.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/accounts.ts#L34-L37)
  也读 `members` → **一致**。仅提醒：字段名不直观，后续不要有人"顺手改名"。

---

## 5. 认证头 / 作用域头专项

| 场景 | 实现 | 结论 |
|---|---|---|
| `apiFetch` 全部请求 | `tokenHeaders(init.headers)`，双写私有头 + `Authorization` | 齐 |
| 聊天 SSE / 历史 | `authHeaders()` 委托 `tokenHeaders` | 齐（历史缺口已修） |
| 知识库上传 XHR | `tokenHeaders().forEach(... xhr.setRequestHeader)`（见 [knowledge.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/knowledge.ts#L487-L490)） | 齐 |
| 登录 / 自助注册 / 恢复 | 裸 `fetch`，**故意不带**令牌（此时还没有令牌） | 正确 |
| `/v1/ready` 探测 | 裸 `fetch`，公开端点 | 正确 |
| 诊断包下载 | 额外 `X-LiuHao-Diagnostics-Filename`，令牌仍齐 | 齐 |
| 本地证明 / 重置证明 | `X-LiuHao-Local-Proof`、`X-LiuHao-Reset-Proof` 与后端共享常量一致 | 齐 |

**租户/账号作用域**：后端没有要求前端额外发"租户头"——作用域一律由令牌 `sub` +
`AccountContext` 推导；前端"以某成员视角查看"用的是查询参数 `view_account`
（仅 `/v1/projects`），后端对越权请求返回中性 404。未发现因漏带作用域头导致的 403/越权路径。

---

## 6. 并行工作留下的半成品（仅记录，不修）

1. [auth.ts](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/auth.ts#L409-L421) 注释
   「契约（后端实现中，前端先按此写）」——正是 H-1 的来源。后端已落地为 `password`，
   前端仍是计划稿的 `secret`。
2. App 级 `viewAs` 只读模式：写操作禁用，但数据源不切换；Projects 页另有一套
   `view_account` 选择器。两套"视角"尚未统一（L-3）。
3. [nonRootSurface.test.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/nonRootSurface.test.tsx#L70)
   注释称用量/备份/记忆端点"尚未挂载"——后端实际已有（`account_usage.py`、`account_backup.py`、
   `account_memory.py`）。属测试夹具滞后，不影响运行时。
4. [Operations.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Operations.tsx#L473-L476)
   注明健康规则因"指标发射器尚未接线"而全部停用——功能未完成，非契约断裂。

---

## 7. 附录：已逐条核对且一致的端点族

以下均核对了 **路径 / 方法 / 请求体字段 / 响应读取路径**，未发现差异：

- 认证：`POST /v1/auth/login|logout|refresh`、`GET /v1/auth/me|config`、`POST /v1/accounts/register`、
  `POST /v1/accounts/recovery/reset|rotate`、`POST /v1/accounts/self-register`（**字段名除外**，见 H-1）。
- 账号：`GET /v1/accounts`、`GET /v1/accounts/tree`、`GET /v1/accounts/roles`、
  `POST /v1/accounts`、`/{p}/activation-code|status|password|permissions|key-sharing|usage`、
  `/backup/export|import`、`/export/all`。
- 记忆：`GET/POST /v1/memory/entries`、`GET /v1/memory/custody`、`POST/GET /v1/memory/export|import`。
- 模型：`GET/POST /v1/models`、`PUT/DELETE /v1/models/{id}`、`POST /v1/models/test`、
  `POST /v1/models/{id}/test`、`POST /v1/models/discover`、`GET /v1/models/presets`、
  `POST /v1/models/{id}/default`；请求体 `model_id` / `base_url` / `api_key` 等字段一致。
- 员工 / 目标 / 项目：`/v1/employees*`（含 batch/adopt/pause/resume/update/delete/capabilities/memories）、
  `/v1/goals*`（CRUD/assign/stop/replan/retry）、`/v1/projects*`（含归档、恢复、`force` 删除参数）。
- 文件 / 插件 / 信任 / 网络：`/v1/files*`、`/v1/plugins*`、`/v1/trust/{id}`、`/v1/network/messages`。
- 观测：`/v1/events`、`/v1/audit/events|verify|summary`、`/v1/dashboard/{summary,roster,activity,analytics}`、
  `/v1/metrics`、`/v1/alerts` + `/alerts/rules`、`/v1/ready`、`/v1/health`。
- 审批：`/v1/policy/approvals` + `/pending`、`POST/DELETE /v1/policy/approvals/{id}`、
  `/v1/policy/enforcement`（**字段名除外**，见 M-3）。
- 知识库：`/v1/knowledge/status|search|ingest|ingest/jobs|ingest/text|ingest/file|documents|documents/upload|documents/upload/jobs`、
  作业取消与轮询字段（`cancel_accepted` / `reason` / `error_code`）一致。
- 数据可携带：`/v1/accounts/export/all`、`/v1/chat/export`、`/v1/knowledge/import`、`/v1/memory/import`。
- 运维：`/v1/diagnostics/bundle`、`/v1/upgrade/status|check|apply|rollback`。

**废弃接口**：控制台未调用任何非 `/v1` 旧路径（`/knowledge`、`/api/*`），无"前端还在用废弃接口"的情况。
