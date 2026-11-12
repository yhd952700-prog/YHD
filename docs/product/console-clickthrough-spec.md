# 控制台点击贯通规格（7 字段可追溯）

> **归属**：product-architect（产品面 / UX / 信息架构）
> **分支**：p36 · **执行入口**：EAP 包 `eap/01-browser-click-through`
> （`npx playwright test --reporter=json,list`；门槛定义见该目录 `expected-results.md`）
>
> **本文件不替代 EAP 01**：EAP 01 是**执行载体**（Playwright 规格 + 证据采集），
> 本文件是**规格与判据的单一真相**。两者冲突时以本文件的判据为准，并回头修 EAP 01。
> 之所以分文件：`eap/01-browser-click-through/expected-results.md` 被
> `eap/05-final-readiness-battery/consolidate_readiness.py` 读取，
> 扩写它会污染那份汇总器的 schema 与判定逻辑。
>
> **每条规格 7 格**：`Route` / `DOM 入口断言` / `Endpoint` / `Auth+读写` /
> `Gate 断言` / `owner` / `status`。
> `owner` 与 `status` **刻意分成两格**：归属（这是谁的活）与验证状态
> （验没验过、验到哪一步）是两种维度；混在一格会让 `UNKNOWN-TO-OWNER`
> 这类**归属缺口**无法与 `NOT VERIFIED` 这类**环境缺口**区分开。

---

## 0. 控制台事实（写任何一条规格前必须成立，否则写错会回落 overview）

| 事实 | 值 |
|---|---|
| 路由 | query-string `?p=<key>`，默认 `overview`，**无 `/console/` 前缀** |
| 单端口 base | `http://<host>:8080`，根路径返回 `index.html`，API 前缀 `/v1` |
| 登录元素 | `#os-principal` / `#os-secret` / `button.os-btn` |
| 令牌头 | `X-Liuhao-Token: Bearer <jwt>`（同时写 `Authorization`） |
| 页面标题 | `.os-page-title`（由 `App.tsx` 渲染，文本取自 `PAGE_META[page].title`） |

**已核验（2026-10-27，静态源码）**：以上五条与当前源码一致 —— `nav.ts` /
`App.tsx` 无任何 `/console/` 字面量；`auth.ts:214` `TOKEN_HEADER='X-Liuhao-Token'`；
`Login.tsx` 三个选择器齐全。由 `tests/test_console_invariants.py` 长期看守。

---

## P1 — 控制台登录与员工生命周期

| 格 | 内容 |
|---|---|
| **Route** | `?p=overview`（登录门）→ `?p=employees` |
| **DOM 入口断言** | 登录态：`.os-page-title` 文案 `总览`；员工页：`我 AI 员工`，且「雇佣员工」按钮可见（`components/OperatorControls.tsx`，挂在 Directory 的 runtime 标签页） |
| **Endpoint** | `POST /v1/employees`、`GET /v1/employees`、`POST /v1/employees/{agent_id}/pause`、`POST /v1/employees/{agent_id}/resume`、`DELETE /v1/employees/{name}`（路径已按 OpenAPI 逐条核对） |
| **Auth+读写** | 需人类令牌；**写**（雇佣/暂停/恢复/移除） |
| **Gate 断言** | ① 登录用 UI 三元素可达 shell；② 雇佣后名字出现在 `employees[].name`；③ 暂停/恢复翻转 `status` 为 `paused` 再还原；④ 移除后重载不再回来（持久化） |
| **owner** | 后端 `src/gateway` + `src/ai`；前端 `pages/Directory.tsx`（product-architect） |
| **status** | **BLOCKED** — UI 已建并接线；待浏览器点击贯通（本机无浏览器） |

## P5 — 主权 / 审批中心（**记录授权 ≠ 放行**）

| 格 | 内容 |
|---|---|
| **Route** | `?p=approval` |
| **DOM 入口断言** | `.os-page-title` = `审批中心`；`.approval-posture` 可见；`.approval-chip` 计数 == `enforced_actions.length` |
| **Endpoint** | `GET /v1/policy/enforcement`、`GET /v1/policy/approvals`、`POST /v1/policy/approvals`（签发）、`DELETE`（撤销） |
| **Auth+读写** | 需人类令牌；**写**（签发/撤销凭据记录） |
| **Gate 断言** | ① 签发后 grant 出现在 `.approval-grants li` 且 `approvals` 返回 active；② **诚实边界：签发后 `enforcement.enabled` 仍为 `true`、该动作仍在 `enforced_actions` 里 —— 记录授权没有解锁内核拦截**；③ 按钮文案必须是「记录授权」，不得写成「放行」；④ 撤销后 grant 从列表与 `approvals` 同时消失 |
| **owner** | 后端 `src/gateway/policy.py` + policy 内核；前端 `components/ApprovalCenter.tsx`（product-architect） |
| **status** | **BLOCKED** — 边界断言 ②③ 已由静态门禁看守（`test_console_invariants.py`）；**运行时**点击贯通待浏览器 |

> **为什么 ② 是本规格的核心而不是附带**：它是整个产品最容易被误读的一处。
> 「记录一次授权」与「放行一次执行」在按钮上只差几个字，在用户的理解里却差一整条
> 主权边界。后端刻意不提供「用凭据在 HTTP 上执行内核动作」的路由，所以这条边界
> **只能**靠「内核是否仍处武装」这个不变式来证明 —— 不能靠接口报错来证明。

## P9 — 身份与权限（严格只读）

| 格 | 内容 |
|---|---|
| **Route** | `?p=identity` |
| **DOM 入口断言** | `.os-page-title` = `身份与权限`；`主体清单` 表 ≥1 `tbody tr`；`权限（只读）` 卡片存在 |
| **Endpoint** | `GET /v1/identity/summary`、`GET /v1/identity/principals`、`GET /v1/identity/principals/{id}` |
| **Auth+读写** | 需令牌；**严格只读** |
| **Gate 断言** | ① 查未知 id → `.os-error` 含 `主体不存在或已被清除`，且 API 返回 **404**；② **全页不得出现任何写动作按钮**（`授权|创建|删除|保存|grant`） |
| **owner** | 后端 `src/security/identity`（security-identity）；前端 `pages/Identity.tsx`（product-architect） |
| **status** | **BLOCKED（运行时）** / 断言 ② 已静态看守 —— 只读性由 `test_console_invariants.py::TestIdentityIsStrictlyReadOnly` 长期钉住 |

> 赋权面（`grant_permission`）尚无人类复核闸门，所以这里**任何**能点的写按钮
> 都是越权入口，不只是"功能没做完"。

## P11 — 生产预检（新增，commit `fe3d23fc`）

| 格 | 内容 |
|---|---|
| **Route** | `?p=preflight` |
| **DOM 入口断言** | `.os-page-title` = `生产预检`；四项 KPI（预检结论 / 严重问题 / 警告 / APP_ENV）渲染；`配置发现` 卡片按 `severity` 分色 |
| **Endpoint** | `GET /v1/production/preflight` |
| **Auth+读写** | `require_human_principal`（令牌须带 `sub`）；**只读** |
| **Gate 断言** | ① 结论先行：`ok=false` 时结论区为 danger 且文案含 `未通过`，不得渲染成平静清单；② 计数来自后端 `critical_count`/`warning_count`，非前端自算；③ `app_env` 原样显示且**不用于推断运行姿态**；④ 页面不得出现任何写请求或配置值 |
| **owner** | 后端 `src/observability/production.py` + `src/gateway/observability.py`（**非我域**）；前端 `pages/Preflight.tsx`（product-architect） |
| **status** | **NOT VERIFIED（运行时）** — 端点形状与 401 边界已用真令牌活探针核实；页面由 `tests/test_preflight_surface.py` 静态看守；**浏览器渲染未验** |

## P8b — 目标 ↔ 项目归属（**写路径接线**，本次闭环）

> **为什么单列**：这不是"再加一个视图"，而是**请求方向的漂移门禁** ——
> 与 `test_contracts_drift_gate.py`（响应方向）互为镜像。它拦的缺陷形状是：
> 前端类型声明里**有**某字段、`tsc -b` 全绿，但 `JSON.stringify` 的请求体里
> **没有**它 —— 调用方传了值，后端收到的却是默认值，能力被**静默丢弃**。

| 格 | 内容 |
|---|---|
| **Route** | `?p=employees`（我 AI 员工）→ 运行时标签页的「创建 Goal」表单 |
| **DOM 入口断言** | 表单含「归属项目（可选）」`<select>`，首项为「（不归属任何项目）」，其余选项来自真实 `GET /v1/projects` |
| **Endpoint** | `POST /v1/goals`（请求体必须带 `project_id`）、`GET /v1/projects` |
| **Auth+读写** | 需人类令牌；**写**（创建目标并归属） |
| **Gate 断言** | ① `createGoal` 的请求体键集 == 后端 `GoalCreateRequest` 字段集（含 `project_id`）；② 未选项目时显式发 `project_id: null`（独立目标），而不是丢键；③ 未知 `project_id` → 后端真实 404，前端原样展示 |
| **owner** | 前端 `lib/operator.ts` + `components/OperatorControls.tsx` + `pages/Projects.tsx`（product-architect）；后端 `src/gateway/ai_management.py`（本就真实，见 `tests/gateway/test_projects_surface.py`） |
| **status** | **PASS（接线层）** — 由 `tests/test_console_request_drift_gate.py`（5/5，静态）+ `src/lib/operator.test.ts`（6/6，运行时 stub fetch）看守；浏览器点击贯通仍待 EAP 01 |

**真身缺陷（本次修掉）**：`operator.ts::createGoal` 声明了 `project_id` 却从不序列化它，
于是整条「目标 ↔ 项目」归属链在控制台**不可达**，而 `Projects.tsx` 还在对用户宣称
"创建目标时可选归属项目"——**一句做不到的承诺**。三处一并修：①`createGoal` 序列化
`project_id`；②`CreateGoalForm` 增加真实项目选择器（走 `/v1/projects`）；③
`Projects.tsx` 的空态文案与说明改为指向真正的创建入口（「我 AI 员工 → 运行时 →
创建 Goal」），不再把用户引向只读的「目标与任务」页（`pages/Goals.tsx` 严格只读，
根本没有创建表单）。

---


## 归属缺口登记（`UNKNOWN-TO-OWNER`）

按纪律：**不确定归属的，标 UNKNOWN-TO-OWNER，不猜、不冒领、不因它阻塞其它条目。**

| 事实 | 为什么是 UNKNOWN-TO-OWNER |
|---|---|
| `/v1/kernels`、`/v1/observability/metrics`、`/v1/workflows`、`/v1/profile`、`/v1/chat/stats` 均**真实返回 200 但无产品视图** | 后端 owner 明确（各自 kernel/gateway 模块），但**"是否该给它们建视图"是产品裁决**，主理人尚未逐个裁定。已记入 `PRODUCT-READINESS.md`。**不是缺陷，是待裁决项。** |
| `contracts.ts` 声明 `WorkflowsPayload`/`WorkflowSummary` 但无页面 import | 类型正确、无渲染，不构成用户可见的谎；是否为「契约债」需产品裁决，故不自行删除。 |

## 已知环境缺口（非缺陷，不降低判据）

| 缺口 | 影响 | 处置 |
|---|---|---|
| 本机 / CI 无浏览器 | P1/P5/P9/P11 的**运行时**点击贯通无法取证 | 保持 **BLOCKED / NOT VERIFIED**；由 EAP 01 在有浏览器的宿主执行 |
| 无 Docker daemon | G9 镜像构建/启动、staging compose | 不在本规格范围 |

> **NOT VERIFIED ≠ FAIL**，且**环境缺失 ≠ RELEASE READY**。两句必须同时出现。

---

## 复核方式（任何人都能独立重跑）

```bash
# 静态门禁（本文件所有"已看守"断言的来源，无需浏览器）
CODEBUDDY_SAFE_DELETE_ENABLED=0 .venv/Scripts/python.exe -m pytest \
  tests/test_console_invariants.py tests/test_preflight_surface.py \
  tests/test_provider_honesty_surface.py tests/test_contracts_drift_gate.py \
  tests/test_console_request_drift_gate.py \
  -p no:cacheprovider --basetemp="D:/cache/temp/pytest_<你的目录>" -q

# 前端类型（任何 UI 改动后必须 EXIT=0 且输出为空）
cd apps/console/console && npx tsc -b

# 前端运行时单测（写请求体接线 + 展示层总函数）
cd apps/console/console && npx vitest run

# 运行时点击贯通（需要浏览器宿主）
cd eap/01-browser-click-through && npx playwright test --reporter=json,list
```
