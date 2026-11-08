# 错误码与提示语全表核对（只读审计 · 2026-11-08）

> 快照：**工作区当前状态**（`HEAD 8e40d985`，2026-11-07 13:46:41 -0800，另有并行线的未提交改动）。
> 方式：**只读**。未修改任何代码、配置、数据；核验请求全部走隔离数据目录 + 独立端口（18082），结束后已清理。
> 关联：`docs/audit-copywriting-20261108.md` 覆盖界面文案与术语；本报告只覆盖**错误路径**（状态码、错误码、错误文案、错误响应体）。
> 说明：审计期间仓库有多条线并行改代码，本报告所有行号对应**审计时的工作区文件**；若后续提交移动了行号，以关键词检索为准。

---

## 一、结论摘要

| # | 结论 | 严重度 |
|---|---|---|
| 1 | **错误响应体有 6 种形状**，前端只能稳定识别其中 2 种；同一客户端要写 6 套解析逻辑 | P0 |
| 2 | **纯英文直接上屏**：`Not Found`、`Method Not Allowed`、`Invalid host header`、`identity kernel is not READY (lifecycle=…)` | P0 |
| 3 | **422 校验错误未中文化**：`Field required`、`String should have at least 8 characters`、`Value error, 项目名称不能为空`；控制台又因只读 `message` 键而完全看不到，只剩泛化兜底 | P0 |
| 4 | **稳定错误码覆盖率低**：去重后 324 个错误构造点，仅 184 个带稳定 code；`HTTPException` 129 处里仅 11 处带 code，其余只有 HTTP 状态 | P1 |
| 5 | **同一件事多种说法**：重名 4 种、目标不存在 7 种、无权限 3 种；**同一句话跨场景复用**：`资源不存在或无权访问` 覆盖项目/目标/文件/知识库/记忆/审计等 | P1 |
| 6 | **内部术语泄漏**：`base_url`、`SSRF`、`DNS rebinding`、`employee_name`、`agent_id`、`employment_status`、`seed`、环境变量名、`str(exc)` 原始异常 | P1 |
| 7 | **两套错误体系并行**：`src/errors/error_codes.py`（英文注册表、`"An error occurred"`）与网关中文/`ACCOUNT_*` 体系互不相通，且前者未被网关使用 | P2 |

一句话结论：**文案质量和错误码纪律在"账号/目标/策略"这几条主链上已经不错，但边界（404/405/422/Host/身份内核）与"配置类"错误仍是面向开发者的语言；普通用户在最常见的首启注册、模型配置两处，最容易看到英文或泛化兜底。**

---

## 二、审计范围与方法

### 2.1 实际可达范围（已按挂载关系校正）

`src/api/__init__.py:3-8` 明确说明：`admin_api / agents_api / tasks_api / memory_api / plugins_api / cost_api` 属于 `src/api/app.py` 遗留宿主，**网关不挂载**。因此本报告分两层：

| 层 | 路径 | 是否产品可达 | 处理 |
|---|---|---|---|
| A | `src/gateway/**`、`src/api/routes/knowledge.py`、`src/kernels/**`、`src/ai/**` | 是（控制台/本地 API 直接命中） | 全量审计 |
| B | `src/api/admin_api.py`、`agents_api.py`、`cost_api.py` 等 | 否（仅测试/遗留） | 仅登记为技术债，不计入整改表 |

### 2.2 扫描方法与统计

方法：
1. AST 遍历 `src/**/*.py`（排除 `tests/`、`__pycache__`），定位 `raise HTTPException(...)`、`deny(...)`、`AccountError(...)`、`GoalWorkError(...)`、`ModelConfigError(...)`、`JSONResponse(...)`、`PlainTextResponse(...)`。
2. 去重（file,line）后按族统计；人工复核错误边界（`main.py` 异常处理器、`display.py` 清洗、`account_context.py` 响应体构造）。
3. 复核前端唯一过滤入口 `apps/console/console/src/lib/errorMessage.ts`，确认"用户实际看到什么"。
4. 起隔离网关，对 422/404/405/400/401/403/409/413 与聊天失败逐条发请求核验真实返回。

| 指标 | 数值 |
|---|---|
| 错误构造点（去重后） | **324** |
| `HTTPException` | 129 |
| `deny(...)` | 59 |
| `AccountError(...)` | 43 |
| `GoalWorkError(...)` | 32 |
| `JSONResponse(...)` | 22 |
| `ModelConfigError(...)` | 19 |
| `BundleError(...)` | 16 |
| `GoalPersistError(...)` | 3 |
| `PlainTextResponse(...)` | 1 |
| 带稳定错误码的构造点 | **184 / 324**（56.8%） |
| `HTTPException` 中带稳定 code | **11 / 129**（8.5%） |

> `deny/AccountError/GoalWorkError/ModelConfigError/BundleError` 天然带 code；未带 code 的几乎全是裸 `HTTPException`。

---

## 三、现状：6 种错误响应体 + 1 条前端过滤链

### 3.1 响应体形状

| # | 形状 | 典型来源 | 问题 |
|---|---|---|---|
| S1 | `{"detail": "…"}` | 裸 `HTTPException`、Starlette 默认 404/405 | 无错误码；英文默认文案会原样返回 |
| S2 | `{"code","message","detail": message}` | `deny()` / `structured_error_body()` | 最规范的一种；但 `detail` 与 `message` 重复 |
| S3 | `{"detail":[{loc,msg,type,input?,ctx?}]}` | 422（`public_validation_errors`） | `msg` 是 Pydantic 英文；`input/ctx` 已脱敏（这点正确） |
| S4 | `{"error","detail","trace_id"}` | 500 兜底 | 无 code 字段，监控只能靠 `error` 字符串 |
| S5 | `{"detail","error","limit_bytes"}` | 413（`http_limits.py:131`） | 同上，码在 `error` 而非 `code` |
| S6 | `text/plain` | Host 校验（`main.py:480`） | 非 JSON，前端 `response.json()` 直接失败 |

另有策略类：`{"error":"policy_denied","detail":…,"action","verdict","rule"}`（403）与 `{"error":"policy_approval_required",…}`（409）——形状接近 S4，但多了内部字段 `rule`、`action`、`verdict`。

### 3.2 前端只认 `message`，且只认中文

`errorMessage.ts` 的 `describeDetail`：

- 字符串：**含中文才返回**，纯英文返回空串；
- 对象：只读 `record.message`（同样要求含中文）；
- 数组：逐项递归，**每项仍只读 `message`**；
- 都拿不到 → `noDetailFallback(status)`。

直接后果：

| 后端返回 | 控制台看到 |
|---|---|
| `{"detail":"Not Found"}` | `要访问的内容不存在，可能已被删除或移动。请返回上一页后重试。`（兜底） |
| 422 `{"detail":[{"msg":"Field required"}]}` | `操作没有完成，请稍后重试。若持续出现，请导出诊断包并附上提示信息。`（422 落到最后一档） |
| `{"code":..., "message":"资源不存在或无权访问"}` | 原文（正常） |
| `{"detail":"知识库未启用：vector store unavailable"}` | 原文（含中文即放行，英文尾随上屏） |

**因此存在"双视角"问题**：控制台用户看到的是兜底/夹生中文，直连 API 或第三方客户端看到的是英文原文。审计必须两边都算。

---

## 四、问题清单

### 4.1 P0：英文与内部实现细节直出

| # | 位置 | 实际文案 | 问题 |
|---|---|---|---|
| E-1 | `src/gateway/main.py:480` | `Invalid host header`（`text/plain`） | 英文 + 非 JSON + 无下一步；反代/自定义域名用户首屏即撞 |
| E-2 | Starlette 默认 404 | `{"detail":"Not Found"}`（实测） | 英文；控制台靠兜底掩盖，API 客户端直读 |
| E-3 | Starlette 默认 405 | `{"detail":"Method Not Allowed"}`（实测） | 同上；且不说明允许的方法 |
| E-4 | `src/gateway/identity.py:118-125` | `identity kernel is not READY (lifecycle=READYING); the principal list would be indistinguishable from 'no principals exist', so it is not returned` | 整段英文内部推理；旁边的 503（`:113`）却是好中文，自相矛盾 |
| E-5 | `src/gateway/main.py:1256-1281` | `Field required` / `String should have at least 8 characters` / `Value error, 项目名称不能为空` | Pydantic 原文未本地化；首启注册最短口令就命中 |
| E-6 | `src/gateway/kernels.py:79,85,106,133,138,159` | `{"error":"unknown_kernel","name":…}` / `factory_only_no_canonical_instance` | 管理员诊断接口；若被控制台引用即不可读（建议限定受众） |
| E-7 | `src/gateway/observability.py:73-77,87,103` | `Prometheus 导出已禁用：未配置 LIUHAO_METRICS_TOKEN…Authorization: Bearer <token>` | 运维接口可接受，但需明确"仅部署者可见"，勿出现在普通用户路径 |
| E-8 | `src/api/routes/knowledge.py:118,200,218,242,293` | `知识库未启用：{exc}` / `向量库不可用：{exc}` | 直接拼原始异常；`{exc}` 常为英文堆栈摘要 |
| E-9 | `src/gateway/files.py:187,249` | `无法列举目录：%s` / `读取失败：%s` | 原始 `OSError` 文本（英文，可能含绝对路径） |
| E-10 | `src/gateway/chat.py:399,698,869` | `{"code":"storage_unavailable","message": str(exc)}` | `str(exc)` 可能是 SQLite/路径细节 |

> 边界清洗（`display.py:499-577`）只替换**治理词、政策编号、脚本路径**（`主权/主体/内核/L0/OD-xx/C-xx/scripts/…`）。**不认识英文异常、环境变量、字段名、IP、异常类名**，所以上述文案全部原样通过。

### 4.2 P1：用户知道"失败了"，但不知道"下一步做什么"

| # | 位置 | 文案 | 缺什么 |
|---|---|---|---|
| N-1 | `src/ai/model_config.py:791,860` | `同名的模型配置已存在` | 没说要改名还是覆盖，也没给 code |
| N-2 | `src/gateway/models.py:944` | `不支持的 provider 类型：xxx` | 没列出可选值（同文件 `:453` 的预设列表未复用） |
| N-3 | `src/gateway/ai_management.py:2069` 等 | `目标不存在：{goal_id}` | 没让用户刷新/返回 |
| N-4 | `src/gateway/events.py:186-189` | `查看其他账号的审计事件需要管理员权限` | 产品里没有"管理员"这一称谓（是主账号/子账号），也没说找谁 |
| N-5 | `src/gateway/files.py:99-101` | `路径越界，拒绝访问` | "越界"是开发词；没说要重新选择文件 |
| N-6 | `src/gateway/display.py:529-533` | 403 兜底说"联系管理员" | 前端 `noDetailFallback(403)` 说"联系安装者"——**同一件事两个称呼** |
| N-7 | `src/gateway/main.py:1538-1563` | 500：`…请稍后重试；如果反复出现，请导出诊断包…` + `trace_id` | 这条是好样板（发生了什么 + 下一步 + 可检索编号），建议作为模板 |

### 4.3 P1：同一件事多种说法

| 语义 | 现有说法（位置） |
|---|---|
| **重名** | `同名的模型配置已存在`（`model_config.py:791`）<br>`员工 {name!r} 已存在；请换一个名字再取用（不会覆盖已有员工）`（`ai_management.py:3234`）<br>`员工名已被占用：{candidate}`（`:3368`）<br>`已经有一个叫「X」的员工了。请换一个名字…`（`:3949`）<br>`同一账号下已有同名项目。请换一个名称，或先归档/删除旧项目。`（`projects.py:548`） |
| **目标不存在** | `目标不存在：{id}`（`ai_management.py:2070,2072,2371,2375,2449,2452,2472,2475`）<br>`要执行的目标不存在或已被删除。请刷新列表后重试。`（`:3685`）<br>`要查看的目标不存在，或不属于当前账号。请刷新列表后重试。`（`:3711`）<br>`要重新规划的目标不存在或已被删除。请刷新列表后重试。`（`:3731`）<br>`要停止的目标不存在，或不属于当前账号。请刷新列表后重试。`（`:3763`）<br>`要派发的目标或员工不存在。请刷新列表后重试。`（`:3806`）<br>`要查看的目标不存在。请刷新列表后重试。`（`:4187`） |
| **员工不存在** | `员工不存在或不在当前可见范围（不会改用其他员工）`（`:2832,3171,3208,3214,3221`）<br>`员工不存在或不在当前可见范围：{name}`（`:3372`）<br>`指定的员工不存在或不在当前可见范围：{agent_id}`（`:1918,1927`） |
| **无权限** | `无权执行该操作`（`_accounts.py:121`，经 `account_context.py:253`、`models.py:352,384,390`、`account_backup.py:646`）<br>`无权使用该模型配置`（`model_config.py:577`，**同一个 code**）<br>`资源不存在或无权访问`（`_accounts.py:122`）<br>`当前账号没有执行这个操作的权限…联系管理员`（`display.py:530`）<br>`当前账号没有执行这个操作的权限。请换用有权限的账号，或联系安装者。`（前端 403 兜底） |
| **模型不可用** | `当前工作区暂未配置可用模型，请先添加自己的模型`（`providers.py:1521`）<br>`这次没有拿到回复，请稍后再试。若持续失败，请到「系统设置 → 模型管理」检查密钥与网络连接。`（`providers.py:176`）<br>`请到「系统设置 → 模型管理」填写密钥并选择模型服务，然后重新发送这条消息。`（`:183`）<br>`base_url 指向私网/回环/链路本地地址，已拒绝（SSRF 防护）`（`models.py:921`） |
| **账号已存在** | `系统已完成初始化，请使用已有账号登录`（`accounts.py:433`）<br>`当前已关闭自助注册`（`:601`）<br>`已存在有效的恢复码；请使用已有的码…`（`:837`） |

### 4.4 P1：同一句话用在完全不同的场景

| 文案 / 错误码 | 被用在哪里 | 为什么有问题 |
|---|---|---|
| `资源不存在或无权访问`（`NEUTRAL_NOT_FOUND_MESSAGE`） | 项目、目标、文件、知识库、记忆、审计、聊天附件、账号子树…（`accounts.py`、`account_context.py:155/184`、`account_memory.py:144/261/349/355`、`files.py:51`、`projects.py:340`、`chat.py:952`、`data_portability.py:706`、`ai_management.py:3878`） | 用户不知道**到底哪个东西**没了；且"不存在"与"没权限"被合并，用户无法判断该重试还是该换账号 |
| `ACCOUNT_SUBTREE_DENIED` | ① `404` + `资源不存在或无权访问`（读别人的树、建子账号）<br>② `403` + `无权执行该操作`（`audit.py:147`） | **同一个 code 两种状态码、两种语义**；客户端按 code 处理必然误判 |
| `ACCOUNT_KEY_NOT_OWNED` | `403 无权执行该操作`（`account_context.py:253`、`models.py:384/390`）<br>`403 无权使用该模型配置`（`model_config.py:577`） | 同 code 不同文案；其中还混着"新建模型"与"使用模型"两种动作 |
| `ACCOUNT_KEY_NOT_CONFIGURED` | 未配置任何模型（合理）<br>**指定了不存在的 `model_id` 也返回它**（实测，`providers.py:1542-1544`） | 用户明明已有模型，却被告知"暂未配置"，会去做完全错误的操作 |
| `"ok"` 之外的 `status:"error"`（HTTP 200） | 聊天上游失败（`providers.py:176`） | 监控/网关只会看到 200；错误无法被状态码统计 |

### 4.5 P2：错误码与响应契约

| # | 问题 | 证据 |
|---|---|---|
| C-1 | 错误码命名混用三套风格：`ACCOUNT_*` 大写、`goal_not_found` 小写、`AUTH_*` 大写、`storage_unavailable` 小写 | `_accounts.py` / `ai_management.py` / `policy.py` / `chat.py` |
| C-2 | 同一错误既有 `code` 又有 `error` 字段 | S2 vs S4/S5 |
| C-3 | `detail` 有时是字符串、有时是对象、有时是数组 | S1/S2/S3 |
| C-4 | 422 的 `loc` 是英文字段路径（`["body","secret"]`），未映射中文标签 | `main.py:1268` |
| C-5 | `src/errors/error_codes.py` 保留英文默认文案 `"An error occurred"`，网关未使用 | 全文件；`src/api/__init__.py` 已声明遗留模块不可达 |

---

## 五、内部术语与泄漏清单

| 类别 | 具体词 | 出现位置 | 用户能否理解 |
|---|---|---|---|
| 内核/主体/主权 | `identity kernel`、`lifecycle`、`principal`、`sovereignty` | `identity.py:121`；`display.py:500-510` 会替换中文"主体/主权/内核"，但**英文原词不在替换范围** | 否 |
| 安全实现词 | `SSRF`、`DNS rebinding`、`base_url`、`私网/回环/链路本地`、IP 地址 | `models.py:767,775,894,898,914,921` | 否（且暴露攻击面思路） |
| 字段名/枚举 | `employee_name`、`agent_id`、`employment_status`、`active/disabled`、`seed`、`provider` | `ai_management.py:2069,3528,3533,3548,944` | 否 |
| 能力 slug | `web.search` 等英文 id | `ai_management.py` 能力校验分支 | 部分（中文名未给） |
| 原始异常 | `str(exc)`、`%s`、`type(exc).__name__` | `knowledge.py`、`files.py`、`chat.py`、`automation/*` | 否 |
| 环境变量/部署 | `LIUHAO_METRICS_TOKEN`、`LIUHAO_PROMETHEUS_METRICS`、`Authorization: Bearer` | `observability.py:74-76,103` | 仅部署者可以 |
| 诊断字段 | `target_class`、`factory-only`、`惰性单例`、`illegal_state_transition` | `kernels.py` | 仅开发者可以 |
| 错误类名 | `ValueError`、`AuditStore`、`SQLite` | 历史审计库错误（当前 `audit.py:188-200` 已收敛为中性中文） | 否 |

> 说明：`audit.py` 的审计库错误在早期运行中曾返回包含 `AUDIT_DB_PATH`、`/app/data/audit_store.db`、`AuditStore()` 的大段英文；**当前快照已改为中性中文**，本报告不把旧输出列为现存问题。

---

## 六、运行核验证据（隔离端口 18082，临时数据目录）

| # | 场景 | HTTP | 实际响应体（截断） | 判定 |
|---|---|---|---|---|
| R-1 | 注册缺 `secret` | 422 | `{"detail":[{"loc":["body","secret"],"msg":"Field required","type":"missing"}]}` | 英文 + 无中文下一步 |
| R-2 | 注册口令 3 位 | 422 | `{"detail":[{"loc":["body","password"],"msg":"String should have at least 8 characters","type":"string_too_short","input":"***"}]}` | 首启高频错误，控制台看不到这句话 |
| R-3 | 项目名为空 | 422 | `{"detail":[{"loc":["body","name"],"msg":"Value error, 项目名称不能为空","type":"value_error"}]}` | 中文可用，但被 `Value error, ` 前缀污染 |
| R-4 | 未知路由 | 404 | `{"detail":"Not Found"}` | 纯英文 |
| R-5 | GET 打了只支持 POST 的路由 | 405 | `{"detail":"Method Not Allowed"}` | 纯英文，且无 `Allow` 说明 |
| R-6 | Host 不在白名单 | 400 | `text/plain` → `Invalid host header` | 非 JSON，前端无法解析 |
| R-7 | 无令牌访问受保护接口 | 401 | `{"code":"AUTH_UNAUTHORIZED","message":"未提供访问令牌，请先登录","detail":"…"}` | **合格样板** |
| R-8 | 不存在的目标 | 404 | `{"code":"goal_not_found","message":"目标不存在：not-real","detail":"…"}` | 有码，但缺"刷新/返回" |
| R-9 | 重名模型 | 409 | `{"detail":"同名的模型配置已存在"}` | 无 code、无下一步 |
| R-10 | 指定不存在的 `model_id` 发消息（成员） | 409 | `{"code":"ACCOUNT_KEY_NOT_CONFIGURED","message":"当前工作区暂未配置可用模型，请先添加自己的模型"}` | **语义错误**：库里有模型，只是 id 不存在 |
| R-11 | 上游模型调用失败 | **200** | `{"status":"error","reply":"这次没有拿到回复，请稍后再试。若持续失败，请到「系统设置 → 模型管理」检查密钥与网络连接。","…"}` | 文案好，但 200 不利监控 |
| R-12 | 请求体超限 | 413 | `{"detail":"请求体超过大小限制","error":"request_entity_too_large","limit_bytes":10485760}` | 可读；建议直接给 MB |
| R-13 | 成员越权读其他子树 | 404 | `{"code":"ACCOUNT_SUBTREE_DENIED","message":"资源不存在或无权访问"}` | 权限失败伪装成"不存在" |
| R-14 | 成员使用根账号模型 | 403 | `{"code":"ACCOUNT_KEY_NOT_OWNED","message":"无权使用该模型配置"}` | 与 R-13 同族但文案不同 |

> R-3/R-10/R-14 与源码逐行比对一致；R-1/R-2 的 `input` 已按敏感字段规则脱敏（正确行为）。

---

## 七、整改表

约定：**建议文案统一为"发生了什么 + 影响 + 下一步"**；不出现内核/主体/主权/fail-closed/政策编号/脚本路径/环境变量/英文异常/堆栈；错误码风格建议统一为小写 `snake_case`（新码），旧 `ACCOUNT_*` 保留但不再新增。

### 7.1 边界与框架默认（P0，改动集中在边界层）

| # | 错误码（建议） | 当前文案 / 位置 | 问题 | 建议文案 |
|---|---|---|---|---|
| 1 | `route_not_found` | `Not Found`（Starlette 默认 404） | 英文，无下一步 | 这个地址不存在。请检查链接是否完整，或返回上一页重新进入。 |
| 2 | `method_not_allowed` | `Method Not Allowed`（默认 405） | 英文，不说允许什么 | 这个操作不支持当前的请求方式。请返回上一页重新操作。 |
| 3 | `host_not_allowed` | `Invalid host header`（`main.py:480`，`text/plain`） | 英文 + 非 JSON | 访问地址不被允许。请通过鎏灏启动窗口提供的地址打开，不要直接使用其他域名。 |
| 4 | `validation_failed` | `Field required`（422） | 英文，无字段中文名 | 有必填内容没有填写：{字段中文名}。请补充后再提交。 |
| 5 | `password_too_short` | `String should have at least 8 characters`（422） | 英文；首启注册高频 | 密码至少需要 8 位。请重新设置。 |
| 6 | `name_empty` | `Value error, 项目名称不能为空`（422） | 英文前缀污染中文 | 项目名称不能为空。 |
| 7 | `identity_not_ready` | `identity kernel is not READY (lifecycle=…)`（`identity.py:118-125`） | 整段英文内部推理 | 身份服务还没有准备好，本次读取没有完成。请稍后重试；持续出现请重启鎏灏。 |
| 8 | `payload_too_large` | `请求体超过大小限制` + `limit_bytes`（413） | 字节数不直观 | 这次提交的内容太大了（上限 10 MB）。请压缩或拆分后再试。 |
| 9 | `internal_error` | 现有 500 文案（`main.py:1557-1561`） | 已合格 | 保持；建议把 `error` 字段并入 `code`。 |

### 7.2 模型配置（P0/P1，用户最常自行操作）

| # | 错误码 | 当前文案 / 位置 | 问题 | 建议文案 |
|---|---|---|---|---|
| 10 | `model_name_taken` | `同名的模型配置已存在`（`model_config.py:791,860`） | 无 code、无下一步 | 已经有同名的模型配置了。请换一个名称，或先编辑/删除已有的那条。 |
| 11 | `model_provider_unsupported` | `不支持的 provider 类型：xxx`（`models.py:944`） | 英文枚举 | 不支持这个模型服务商。请从列表中选择：OpenAI、Anthropic、Google、Ollama、Moonshot、DeepSeek。 |
| 12 | `model_url_scheme_invalid` | `base_url 仅支持 http/https，收到：ftp`（`models.py:894`） | 字段名 + 协议名 | 模型地址必须以 http:// 或 https:// 开头。请检查后重新填写。 |
| 13 | `model_url_private` | `base_url 指向私网/回环/链路本地地址，已拒绝（SSRF 防护）：host -> ip`（`models.py:921`） | SSRF、回环、IP 全部泄漏 | 这个模型地址指向本机或内网，不能用于公网模型服务。请填写服务商提供的官方接口地址。 |
| 14 | `model_url_redirect` | `base_url 返回跨域重定向，已拒绝（防 SSRF 绕过）：…`（`models.py:767`） | 同上 | 这个模型地址会跳转到其他域名，出于安全考虑已拒绝。请填写服务商提供的官方接口地址。 |
| 15 | `model_url_unstable` | `base_url 解析结果在测试期间发生变化，疑似 DNS rebinding，已拒绝`（`models.py:775`） | 安全术语 | 这个模型地址解析不稳定，出于安全考虑已拒绝。请填写服务商提供的官方接口地址。 |
| 16 | `model_key_not_owned` | `无权使用该模型配置`（`model_config.py:577`）/ `无权执行该操作`（`account_context.py:253`、`models.py:384/390`） | 同码两文案 | 这个模型配置不属于当前账号，不能使用。请到「系统设置 → 模型管理」添加自己的模型。 |
| 17 | `model_not_found` | `当前工作区暂未配置可用模型，请先添加自己的模型`（`providers.py:1562`，被 `model_id` 不存在复用） | **语义错误**，会误导用户 | 找不到这个模型配置。请刷新模型列表后重新选择。 |
| 18 | `model_unavailable` | `这次没有拿到回复，请稍后再试…`（`providers.py:176`） | 文案已好；问题在 HTTP 200 | 保持文案；建议状态码改为 502/503，或在响应中加 `code` 便于统计。 |

### 7.3 账号 / 员工 / 目标（P1，统一同义说法）

| # | 错误码 | 当前文案 / 位置 | 问题 | 建议文案 |
|---|---|---|---|---|
| 19 | `employee_name_taken` | 4 种说法（`ai_management.py:3234/3368/3949`、`projects.py:548`） | 同一件事四种写法 | 已经有叫「{name}」的员工了。请换一个名字。（项目同理：已经有叫「{name}」的项目了。） |
| 20 | `goal_not_found` | 7 种说法（`ai_management.py:2069…4187`） | 同一件事七种写法 | 这个目标不存在，或已被删除。请刷新列表后重试。 |
| 21 | `employee_not_found` | 3 种说法（`:2832/3171/…`、`:3372`、`:1918`） | 同一件事三种写法 | 这位员工不存在，或不在当前账号可见范围内。请刷新列表后重新选择。 |
| 22 | `account_subtree_denied` | 404 `资源不存在或无权访问` / 403 `无权执行该操作`（`audit.py:147`） | 同码两状态两语义 | 拆成两码：`subtree_not_found`（404，读不到）与 `subtree_forbidden`（403，不能操作）。文案：这个账号不存在，或不在你的可见范围内。 / 当前账号不能执行这个操作。请换用主账号或申请权限。 |
| 23 | `resource_not_found` | `资源不存在或无权访问`（跨 8+ 对象） | 一句话覆盖所有对象 | 按对象补名词：这个项目/文件/记忆/知识库不存在，或你没有访问权限。 |
| 24 | `employee_unavailable` | `员工 X 的 agent Y 处于暂停状态，无法接受指派`（`:2104`、`:3021`） | `agent` 术语 | 「{员工}」目前处于暂停状态，不能接受新任务。请先恢复这位员工，或改派给其他人。 |
| 25 | `assignee_required` | `必须且只能指定一个执行人：员工名（employee_name）或执行个体（agent_id）`（`:2069`） | 字段名 | 请指定由谁执行：填员工名字，或选择一位具体员工。只能选一个。 |
| 26 | `employment_status_invalid` | `employment_status 只能是 active/disabled，收到：xxx`（`:3528`） | 字段名 + 英文枚举 | 员工状态只能是「在职」或「停用」。请重新选择。 |
| 27 | `seed_employee_protected` | `拒绝停用 seed 默认员工 'x'（网关依赖它）`（`:3533/3548`） | `seed`、`网关` | 这是系统内置的默认员工，不能停用或改名。你可以新建一位员工来代替它。 |
| 28 | `unknown_capability` | `未知的默认能力（未注册或未激活）：xxx；可用能力：web.search, …`（`ai_management.py:3142-3147`） | 英文 slug | 有暂不支持的能力：{名称}。可用能力包括：联网搜索、文件读写等，请从中选择。 |
| 29 | `audit_scope_forbidden` | `查看其他账号的审计事件需要管理员权限`（`events.py:188`） | "管理员"不是产品称谓 | 只有主账号可以查看其他账号的审计记录。请改用主账号登录。 |
| 30 | `path_out_of_scope` | `路径越界，拒绝访问`（`files.py:99-101`） | "越界"是开发词 | 这个位置不在可访问范围内。请从工作区里重新选择文件或文件夹。 |
| 31 | `directory_unreadable` / `file_unreadable` | `无法列举目录：%s` / `读取失败：%s`（`files.py:187,249`） | 原始异常 | 这个文件夹暂时打不开。请稍后重试；持续出现请确认数据目录可读。 / 这个文件暂时读不出来。请稍后重试，或换一个文件。 |
| 32 | `binary_not_text` | `不是 UTF-8 文本（二进制文件请在文件系统查看）`（`files.py:255`） | 半技术 | 这个文件不是文本文件，无法在这里预览。请在系统文件管理器中打开。 |
| 33 | `storage_write_failed` | `str(exc)`（`chat.py:399/698/869`、`accounts.py:643`） | 原始异常 | 这次内容没有保存成功。请检查磁盘空间后重试；持续出现请导出诊断包。 |

### 7.4 知识库 / 策略 / 运维（P1/P2）

| # | 错误码 | 当前文案 / 位置 | 问题 | 建议文案 |
|---|---|---|---|---|
| 34 | `knowledge_disabled` | `知识库未启用：{exc}`（`knowledge.py:118,475,512`） | 拼原始异常 | 知识库现在不可用，本次操作没有完成。请到「系统设置 → 知识库」确认已启用后重试。 |
| 35 | `vector_store_unavailable` | `向量库不可用：{exc}`（`knowledge.py:200/218/242/293`） | 同上 | 知识库的检索服务暂时不可用。请稍后重试；持续出现请导出诊断包。 |
| 36 | `policy_denied` | 现有文案（`main.py:1636-1639`） | 已合格（说明"重复尝试无效"） | 保持；把"联系管理员"统一为"联系主账号/安装者"（与前端一致）。 |
| 37 | `policy_approval_required` | 现有文案（`main.py:1611-1616`） | 已合格 | 保持。 |
| 38 | `metrics_disabled` | `Prometheus 导出已禁用：未配置 LIUHAO_METRICS_TOKEN…`（`observability.py:74-76`） | 环境变量名 | 监控数据导出未开启。请联系部署者配置访问令牌后再抓取。 |
| 39 | `kernel_unknown` | `{"error":"unknown_kernel","name":…}`（`kernels.py:79`） | 内部诊断 | 若仅管理员可见可保留；不要在控制台普通页面呈现。 |
| 40 | `validation_failed` | 422 的 `loc` 为英文字段路径 | 字段名 | 维护 `loc → 中文字段名` 映射（如 `password → 密码`），无映射时回落到"填写内容"。 |

---

## 八、建议的收口顺序（不改代码，仅排序）

1. **先补边界**：404/405/Host/422/身份 503（整改表 1-8）——改动集中在 `main.py` 异常处理器与 `display.py`，一次覆盖全部路由。
2. **再修模型配置**（9-18）——这是用户自行操作最多、术语最密的一段。
3. **统一同义说法**（19-33）——先统一"重名 / 不存在 / 无权限"三组，收益最大。
4. **最后处理受众分离**（34-40）——运维/诊断接口保留技术语言，但加"仅部署者"标注并确保不出现在普通用户路径。
5. **协议层建议**：新增错误统一 `{code, message, detail?}`；`message` 必须是中文可行动句；`code` 用 `snake_case`；禁止新增 `text/plain` 与 `{"detail":"英文"}` 形状。

---

## 九、边界与未覆盖

| 项 | 说明 |
|---|---|
| 遗留 API | `src/api/admin_api.py`、`agents_api.py`、`tasks_api.py`、`memory_api.py`、`plugins_api.py`、`cost_api.py` 按 `src/api/__init__.py:3-8` 声明**网关不可达**，其英文错误（`User not found: …` 等）未计入整改表 |
| 前端自有文案 | 只审计了 `errorMessage.ts` 的统一入口；各页面内联 `message.error('…')` 未逐页复核（属 `audit-copywriting` 范围） |
| 流式/WebSocket | SSE/WS 的中途错误帧未纳入（本次只核验 REST） |
| 安装器/CLI | `installer/`、`scripts/`、启动器的错误输出未纳入（非 HTTP 后端） |
| 日志 | 本次只审计"返回给用户"的内容；服务端日志中的英文/堆栈是预期行为，不在整改范围 |
| 动态拼接 | 部分 `detail` 由变量拼接（如能力列表、字段名），整改表给出的是模板，落地时需按参数替换 |

---

*本报告不修改任何代码；所有建议文案为评审输入，不代表已实施。*
