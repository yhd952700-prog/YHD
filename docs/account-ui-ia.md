# 多账号界面 · 信息架构与页面清单（ACCOUNT UI IA）

> 依据：`docs/account-hierarchy.md` §9（UI 信息架构与文案，定稿）。
> 范围：仅 `apps/console/console`（前端）。后端不改，接口以实现中的账号路由为准。
> 状态：IA 定稿 + 实现登记。**本文同时是"桩登记表"与"接口冲突清单"**，任何桩都必须在 §6 出现，否则视为违规。

---

## 1. 三层视图（矩阵列定义）

| 列 | 判定 | 说明 |
|---|---|---|
| **根** | `parent_account_id = null` 且 `depth = 0` | 单机唯一；可管理整棵树 |
| **成员（第 1 层）** | `depth = 1` | 只管理自己的直接成员（若角色含 `account:manage_children`），日常体验完整 |
| **更深（第 2 层及以下）** | `depth >= 2` | 默认层级上限下为叶层；不管理任何成员 |

判定只使用 `/v1/auth/me` 与 `/v1/accounts` 返回的数据。前端不做任何授权决策，只做**入口裁剪**；拒绝始终由服务端返回中性文案。

---

## 2. 页面清单

路由键对应 `src/App.tsx` 的 `PAGE_META` 与 `src/lib/nav.ts` 的导航项。

| # | 路由键 | 页面 | 入口条件 | 主要数据源 |
|---|---|---|---|---|
| 1 | `login`（登录门，不在导航） | 登录 / 创建账户（自助注册，**无邀请码**） / 首启创建 / 忘记密码 / 迁移 | 未登录 | `GET /v1/auth/config`、`POST /v1/accounts/register`、`POST /v1/accounts/self-register`（后端实现中）、`POST /v1/accounts/recovery/reset`、`POST /v1/accounts/backup/import` |
| 2 | `members` | 成员与权限管理 | 根 | `GET /v1/accounts`、`GET /v1/accounts/tree`、`POST /v1/accounts`、`PATCH /v1/accounts/{p}/status`、`POST /v1/accounts/{p}/activation-code`、`POST /v1/accounts/{p}/password`、`PATCH /v1/accounts/{p}/key-sharing` |
| 3 | `matrix` | 权限矩阵 | 根 | `GET /v1/accounts`、`GET /v1/accounts/roles`、`PATCH /v1/accounts/{p}/permissions` |
| 4 | `my-members` | 我的成员 | 成员且含 `account:manage_children` | `GET /v1/accounts`（**只返回直接成员**，条目带 `is_direct: true`）、`POST /v1/accounts`、`PATCH .../status`、`POST .../activation-code` |
| 5 | `my-keys` | 我的 Key（根侧标题为"全部 Key"） | 所有人 | `PATCH /v1/accounts/{self}/key-sharing`、`GET /v1/accounts/{p}/usage`（根） |
| 6 | `memory` | 记忆 | 所有人 | `GET /v1/memory/entries`、`POST /v1/memory/export`（**前端拟定契约，后端未实现**） |
| 7 | `audit`（改造） | 审计链 + 范围筛选 | 所有人 | `GET /v1/audit/events`、`/v1/audit/summary` |
| 8 | `identity`（改造） | 身份与权限 | 所有人 | `GET /v1/accounts`、`GET /v1/identity/summary`（根）、`GET /v1/identity/principals`（根） |
| 9 | `templates` | 角色模板 | 根 | `GET /v1/accounts/roles`（只读） |
| 10 | `backup` | 备份导出 / 迁移认领 | 根 | `POST /v1/accounts/backup/export`、`POST /v1/accounts/backup/import` |
| 11 | `recovery` | 恢复码 | 根 | `POST /v1/accounts/recovery/rotate` |
| 12 | `settings`（改造） | 系统设置 | 根 | 既有设置页；非根不渲染入口 |
| 13 | 全局 | 身份条 / 只读横幅 | 已登录 | `GET /v1/auth/me`、`GET /v1/accounts` |

---

## 3. 可见性矩阵（看到什么 / 看不到什么）

"不渲染"= DOM 中不存在该入口，不是置灰、不是禁用提示。

| 页面 / 区域 | 根 | 成员（第 1 层） | 更深（第 2 层及以下） |
|---|---|---|---|
| 导航：成员与权限管理 | **看到** | 不渲染 | 不渲染 |
| 导航：权限矩阵 | **看到** | 不渲染 | 不渲染 |
| 导航：角色模板 | **看到** | 不渲染 | 不渲染 |
| 导航：备份导出 / 迁移认领 | **看到** | 不渲染 | 不渲染 |
| 导航：恢复码 | **看到** | 不渲染 | 不渲染 |
| 导航：全部 Key | **看到**（标题"全部 Key"） | 不渲染；改为"我的 Key" | 不渲染；改为"我的 Key" |
| 导航：我的成员 | 不渲染 | 仅当角色含 `account:manage_children` 时看到 | 不渲染 |
| 导航：系统设置 / 系统状态 / 生产预检 | **看到** | 不渲染 | 不渲染 |
| 导航：记忆 | **看到**（可切"整棵子树"） | **看到**（仅自己的记忆） | **看到**（仅自己的记忆） |
| 导航：审计链 | **看到**（全树 / 仅自己） | 看到（仅自己） | 看到（仅自己） |
| 导航：身份与权限 | **看到**（全树主体清单） | 看到（仅"我的范围"卡片） | 看到（仅"我的范围"卡片） |
| 成员列表 | 整棵子树 + 嵌套层级 | 平铺的直接成员列表（接口已按直接成员返回，C11 已解决） | 无页面入口 |
| 成员 Key 授权 | 每人"可代用 / 仅本人" + `key_auth_source` | 不可见（接口不返回该字段） | 不可见 |
| Key 归属展示 | 真实归属 + **主账号代用**标记 | 只写"Key 归属：我的账号" + 额度/用量，不写来源 | 同成员 |
| 权限矩阵 | 可勾选整棵子树的 scope | 无入口 | 无入口 |
| 记忆检索 | 自己的 + 整棵子树（只读） | 仅自己 | 仅自己 |
| 记忆导出 | 整棵子树 / 自己 | 仅自己 | 仅自己 |
| 审计范围 | 全树 / 仅自己 | 仅自己（后端强制） | 仅自己（后端强制） |
| 备份导出 | 整棵树 `scope=all` + 口令二次确认 | 无入口；若走接口只能 `scope=self` | 同成员 |
| 迁移认领 | 本机无根时在登录页出现 | 登录页无根时才出现（与身份无关） | 同成员 |
| 恢复码 | 可轮换、一次性展示 | 无入口 | 无入口 |
| 身份条 | `主账号 · 可管理 N 位成员` | `<display_name> · 工作空间` | 同成员 |
| 只读查看横幅 | 由成员页触发 | 由"我的成员"触发（若有管理权） | 无触发入口 |
| 权限拒绝文案 | 一律"无权执行该操作" | 同左 | 同左 |

### 3.1 非根界面的绝对禁令

非根账号的任何可见文案不得出现：`子账号`、`孙账号`、`上级`、`下级`、`层级`、`越级`、`主账号`、`根账号`、`被管理`、`受限`、`管理员`。

这条由自动化断言把关（见 §8），不是人工检查。

**唯一例外**：登录页"当前不允许自行创建账户，请联系设备管理员"是按需求指定的原文，含"管理员"；该句只出现在未登录页，不属于非根账号的可见界面。其余非根页面仍按上表执行。

---

## 4. 导航裁剪表

| 导航项 | 根 | 非根 |
|---|---|---|
| 总览 / 鎏灏管家 / 我AI员工 / 业务中心 / 知识中心 / 工作台 / 目标与任务 / 项目 / 应用 / 数据中心 / 事件流 | ✓ | ✓ |
| 审批中心 / 审计链 / 信任 / 网络 / 身份与权限 / 工作区文件 | ✓ | ✓ |
| 记忆 | ✓ | ✓ |
| 我的 Key | ✓（标题"全部 Key"） | ✓ |
| 我的成员 | ✗ | 仅有 `account:manage_children` 时 ✓ |
| 成员与权限管理 / 权限矩阵 / 角色模板 / 备份与迁移 / 恢复码 | ✓ | ✗ |
| 系统状态 / 生产预检 / 系统设置 | ✓ | ✗ |
| 手机端底栏第 5 项 | 系统设置（"我的"） | 我的 Key（"我的"） |

深链 `?p=` 只接受**当前身份可见**的页面；不可见页一律回落到总览，不渲染空壳。

---

## 5. 接口契约（以实现中的后端为准）

响应形状以 `src/gateway/accounts.py` 与 `src/gateway/auth.py` 现有代码为准；**不含** `parent_id/depth/root_id` 的成员条目是刻意的（非根不能感知层级）。

| 接口 | 方法 | 请求 | 响应要点 | 状态 |
|---|---|---|---|---|
| `/v1/auth/config` | GET | — | `root_account_exists`、`registration_open`、`recovery{available,self_service}` | 已实现；`recovery.available` = 根存在**且**确有恢复码，老数据（`recovery_hash` 为空）为 false |
| `/v1/auth/login` | POST | `principal, secret, client` | `access_token, principal, display_name, scope, permissions, expires_at, parent_account_id, path, depth` | 已实现（auth.py:454-465） |
| `/v1/auth/me` | GET | — | 同上 + `still_human` | 已实现（auth.py:607-626） |
| `/v1/accounts/roles` | GET | — | `roles[{id,permissions}]`、`scopes[]` | 已实现（accounts.py:122） |
| `/v1/accounts/register` | POST | `principal, password, display_name?` | `account`、`recovery_code`、`notice` | 已实现（accounts.py:134） |
| `/v1/accounts/activate` | POST | `principal, code, password` | `token`、`account` | 已实现（accounts.py:152）；**本轮起登录页不再提供入口**，仅管理页生成激活码 |
| `/v1/accounts/self-register` | POST | `principal, secret, client`（**只有用户名 + 密码**） | `token`（兼容 `access_token`）；层级/权限由服务端决定 | **前端拟定契约，后端实现中**；关闭时返回 403/409 + `*_CLOSED`，界面给中性文案 |
| `/v1/accounts/recovery/reset` | POST | `recovery_code, password` | `token`、`account` | 已实现（accounts.py:164） |
| `/v1/accounts` | GET | — | `members[]`、`count`；成员含 `principal/permissions/status/created_at`（根视图另含 `allow_superior_key_use/key_auth_source`） | 已实现（accounts.py:175） |
| `/v1/accounts/tree` | GET | — | `tree` 嵌套；仅根 | 已实现（accounts.py:188） |
| `/v1/accounts` | POST | `principal, display_name?, role?, permissions?, allow_key_sharing` | `account`、`activation_code`、`notice` | 已实现（accounts.py:242） |
| `/v1/accounts/{p}` | GET | — | `account` | 已实现（accounts.py:266） |
| `/v1/accounts/{p}/activation-code` | POST | — | `activation_code`、`expires_at` | 已实现（accounts.py:277） |
| `/v1/accounts/{p}/permissions` | PATCH | `grant[], revoke[]` | `account` | 已实现（accounts.py:288） |
| `/v1/accounts/{p}/key-sharing` | PATCH | `allow` | `account` | 已实现（accounts.py:305） |
| `/v1/accounts/{p}/status` | PATCH | `status, reason` | 内核结果 | 已实现（accounts.py:320） |
| `/v1/accounts/{p}/password` | POST | `password` | `ok` | 已实现（accounts.py:337） |
| `/v1/accounts/{p}/usage` | GET | — | `principal/scope/callers_visible/calls/tokens_in/tokens_out/total_tokens/by_key_owner[]`；根另含 `by_actor[]` | 已实现（account_usage.py:128-161；accounts.py:374-385） |
| `/v1/accounts/backup/export` | POST | `scope, passphrase` | `bundle`、`scope`、`members`、`manifest{members, member_count, files, bytes, models, credentials, skipped}` | **已实现**（`src/gateway/account_backup.py`；运行时 200） |
| `/v1/accounts/backup/import` | POST | `bundle, passphrase` | 导入结果；本机已有账号 → **409 `IMPORT_NOT_ALLOWED`** | **已实现**（运行时 409） |
| `/v1/accounts/migrate` | POST | — | 迁移结果；仅根 | 已实现（accounts.py:392-396）；本轮无页面 |
| `/v1/accounts/migrate/rollback` | POST | — | 回滚结果；仅根 | 已实现（accounts.py:399-405）；本轮无页面 |
| `/v1/accounts/recovery/rotate` | POST | — | `recovery_code`（**无 `expires_at`**：恢复码没有 TTL） | 已实现；老根账号补发入口，旧码立即作废 |
| `/v1/accounts/recovery/local` | POST | — | `written`、`file`、`notice`、`security_note`；**响应不含恢复码** | 已实现；忘记密码且无恢复码时，把码写到本机 `<数据目录>/config/recovery_code.txt`；已有恢复码 → 403 `ACCOUNT_RECOVERY_UNAVAILABLE` |
| `/v1/audit/events` | GET | `principal?` | 事件列表；非 L0 被强制为令牌主体（audit.py:270-280） | 已实现，但范围与规格 §3.6 冲突 |
| `/v1/memory/entries` | GET | `scope=self|subtree, q` | 记忆条目 | **前端拟定，后端无端点** |
| `/v1/memory/export` | POST | `scope, format` | 导出文件 | **前端拟定，后端无端点** |

---

## 6. 桩登记表（每个桩都必须在这里；换真实接口时逐条销账）

桩只在**开发环境（`import.meta.env.DEV`）**且请求返回"路由不存在"（404 且响应体没有业务 `code`）时启用；页面顶部恒显示 **"本地契约桩 · 待联调"** 徽标。生产构建不启用任何桩。

业务拒绝（403 / 404 + `code`）与 501（功能未启用）**一律不落桩**，页面显示真实原因 —— 这条由 `accounts.test.ts` 钉住。

| 桩 | 触发条件 | 形状来源 | 何时必须换真实接口 | 当前状态 |
|---|---|---|---|---|
| `stubAccounts` | `GET /v1/accounts` 路由不存在（404 无 code） | 严格按 `_accounts.public_member`（_accounts.py:570-585） | 路由已挂载；运行时一旦可用自动走真实接口 | **已联调（真实接口 200）** |
| `stubTree` | `GET /v1/accounts/tree` 路由不存在 | `tree_view`（_accounts.py:588-614） | 同上 | **已联调（真实接口 200）** |
| `stubRoles` | `GET /v1/accounts/roles` 路由不存在 | `ROLE_PRESETS`（_accounts.py:77-80） | 同上 | **已联调（真实接口 200）** |
| `stubUsage` | **已移除** | — | — | 用量接口已落地（account_usage.py），前端直接读真实字段；不再保留假计数 |
| `stubBackupExport/Import` | **已移除** | — | — | 备份模块已落地（`account_backup.py`）；前端直接读真实 `manifest`，不再伪造包或假成功 |
| `stubMemory` | `GET /v1/memory/entries` 路由不存在（404 无 code） | 前端拟定契约 | 后端确认契约并提供端点后 | **待联调（无端点，开发环境会落桩）** |

> 桩数据只用于把界面结构与可见性跑通；**任何报告不得把桩模式写成"已完成"。**

---

## 7. 与文档的冲突 / 缺口（按文档执行，冲突记录在此）

| # | 文档要求 | 后端现状 | 本轮处理 |
|---|---|---|---|
| C1 | §9.7 只读查看需要 `effective_account_id` 与进入/退出审计 | `accounts.py` 无视角端点；`/auth/me` 无生效视角字段 | 前端只做"只读横幅 + 禁用写操作"，不切换数据源；**不做假的视角切换**。待后端补端点 |
| C2 | §9.2 S2a/S2b：激活后跳到强制改密页 | `POST /activate` 一次调用同时校验码 + 设密码（accounts.py:152-161） | **登录页已按需求移除激活码入口**；管理页仍生成并展示激活码，成员改为登录页自助创建。激活端点保留但本轮无页面入口 |
| C3 | §9.8 我的 Key：创建 / 轮换 / 吊销 | 无 Key 增删改端点，只有 `key-sharing` 开关（accounts.py:305） | 页面只呈现归属、授权开关、用量；增删改标记"待联调" |
| C4 | §9.8 权限矩阵需可编辑角色模板 | `/roles` 只读，预设固定三个（accounts.py:122；_accounts.py:77） | 矩阵可改成员 scope；模板页只读，编辑待联调 |
| C5 | §9.12 备份包需"成员数 / 文件数 / 体积"二次确认 | 导出响应才带真实 `manifest`，导出前无统计端点 | 确认框用整棵树人数（运行时 4）；文件数与体积在导出完成后按 `manifest` 实际值回报 |
| C6 | §9.11 恢复材料应为可离线保存的**加密**文件 | `register` 只返回 `recovery_code`，无文件端点 | 前端本地生成 `.json` 保存文件（**不宣称加密**），文案只写"离线保存" |
| C7 | §9.4 非根可读自己 + 直接成员的审计 | `audit.py:270-280` 将非 L0 强制为自身 | 非根审计页只提供"仅自己"；待后端按子树放开 |
| C8 | §9.6 恢复码 / 迁移认领 / 系统升级根专属 | `/auth/config` 无 `recovery.available` / `migration.allowed`；迁移端点已补（`/v1/accounts/migrate`、`/migrate/rollback`），但无系统升级端点 | 恢复/迁移入口按"根存在与否"显示；系统升级与迁移执行本轮无页面（缺口登记，端点已就绪） |
| C9 | §6 记忆命名空间与导出 | 无记忆端点、无 `account_id` 维度 | 记忆页按前端拟定契约实现并全程打"待联调"徽标 |
| C10 | §9.9 "构建产物 grep 扫描" | — | 本轮改为**运行时断言**（渲染账号面页面后扫描 DOM 文本）+ 导航/身份条纯函数断言，覆盖本轮引入的非根可见文案 |
| C11 | §9.6 "我的成员：只看直接成员" | **已解决**：运行时 `GET /v1/accounts` 只返回直接成员，条目带 `is_direct: true` | 前端按直接成员平铺；根侧仍用 `/tree` 展示嵌套全树 |
| C12 | 登录页需两个平级入口：登录 / 创建账户；创建只要用户名 + 密码 | `/v1/accounts/self-register` 后端实现中；登录页原"激活码"入口已按需求移除 | 前端按拟定契约写 `POST /v1/accounts/self-register`，成功后与主账号同一界面、按会话位置裁剪能力；关闭/未启用一律中性文案。联调时换真接口 |

---

## 8. 自动化断言（禁用词与可见性）

1. `src/lib/accountCaps.test.ts`：根/成员/更深三层的 caps 与导航裁剪；深链白名单。
2. `src/lib/accounts.test.ts`：桩响应形状与后端一致（成员条目**不含** `parent_id/depth/root_id`）；404 分类正确。
3. `src/components/accountChrome.test.tsx`：非根身份条与导航文案不含禁用词；根身份条含"N 位成员"。
4. `src/pages/nonRootSurface.test.tsx`：以成员身份渲染**账号面全部页面**（含错误态与拒绝态），扫描 `document.body.textContent`，禁用词命中即失败。
5. `src/components/Login.account.test.tsx`：登录状态机（首启 / 登录 / **创建账户** / 忘记密码 / 迁移入口）；创建表单只有用户名 + 密码、**无邀请码字段**；创建成功后按成员位置裁剪导航；关闭注册时只显示中性文案；登录页不出现"邀请/加入/成员/子账号/上级"。

禁用词常量单一来源：`src/lib/accountCaps.ts` 的 `BANNED_ACCOUNT_TERMS`。

---

## 9. 验收场景映射（§10）

| 场景 | 界面证据 |
|---|---|
| 子账号看不到未授权入口 | 导航数组不含核心项；DOM 断言无这些 key 的按钮 |
| 子账号看不到层级字样 | 非根 DOM 扫描禁用词 |
| 子账号 Key 不显示来源 | 我的 Key 只显示"归属：我的账号" |
| 主账号看全树 | 成员页渲染嵌套树、每人 Key 授权、状态 |
| 拒绝文案中性 | 所有账号页错误态直接使用后端 `detail`；账号 404 统一"无权执行该操作" |
| 记忆隔离 | 记忆页 scope 固定 `self`（非根） |
| 只读查看留痕 | 横幅可见、写操作禁用；服务端留痕待 C1 |

---

## 10. 本轮实现状态：真实接口 / 桩（交付时必须区分）

验证口径：已于 **2026-11-06** 启动隔离后端（临时身份库 / 审计库 / 工作区 / 用量库，随机端口 65360 → 界面联调用 8080）完成**运行时联调**；下表"已联调"指真实请求跑通并逐字段核对，不是契约级对照。联调明细见 §11。

| §9.8 项 | 界面落点 | 数据来源 | 状态 |
|---|---|---|---|
| 登录门（共用 / 首启 / 创建账户 / 忘密 / 迁移） | `components/Login.tsx` | `/v1/auth/config`、`/v1/accounts/register`、`/v1/accounts/self-register`（拟定）、`recovery/reset`、`backup/import` | 除自助注册外均已联调（登录 200 / 重复注册 409）；自助注册待后端端点 |
| 导航裁剪 | `lib/nav.ts` + `components/Shell.tsx` | `/v1/accounts`、`/auth/me` | 真实路由；已联调（根 / 成员两套入口实测） |
| 身份条 | `lib/accountCaps.ts` + `Shell` | `/v1/accounts` + `/tree` | 真实路由；已联调（根 3 位成员 / 成员"工作空间"） |
| 成员与权限管理（根） | `pages/Members.tsx` | `/v1/accounts`、`/tree`、`/roles` + 写操作 | 真实路由；已联调（3 层嵌套 / 不列根自己） |
| 我的成员（成员） | `pages/Members.tsx`（`variant=member`） | `/v1/accounts`（直接成员） | 真实路由；已联调（成员只见 deep-one） |
| 权限矩阵 | `pages/PermissionMatrix.tsx` | `/v1/accounts`、`/roles`、`PATCH /permissions` | 真实路由；写操作已联调（grant 200） |
| 角色模板（只读） | `pages/RoleTemplates.tsx` | `/v1/accounts/roles` | 真实路由；编辑待联调（C4） |
| 我的 Key / 全部 Key | `pages/MyKeys.tsx` | `PATCH /key-sharing`；用量 `/usage` | 真实路由；已联调（根改成员 200 / 成员自改 403 中性文案） |
| 记忆 | `pages/Memory.tsx` | `/v1/memory/entries`、`/v1/memory/export` | **桩（无端点，开发环境落桩，页面常驻徽标）** |
| 备份与迁移 | `pages/Backup.tsx` | `/backup/export|import` | **真实路由**；导出 200 + 真实 `manifest`，已有账号导入 409 |
| 恢复码 | `pages/Recovery.tsx` | `POST /recovery/rotate` | 真实路由；已联调（200） |
| 审计范围 / 只读横幅 | `pages/Audit.tsx` + `components/AccountChrome.tsx` | `/v1/audit/events`、`/summary` | 真实路由；只读视角待 C1 |

结论：只有"记忆"一页仍是契约桩（后端无端点）；其余账号面全部走真实路由并已完成运行时联调。

---

## 11. 运行时联调记录（2026-11-06）

环境：`.venv` 启动 `python -m src.gateway`，`LIUHAO_INSTANCE_DIR/DATA_DIR/HUMAN_IDENTITIES_DB/AUDIT_DB_PATH/WORKSPACE_ROOT/ACCOUNT_USAGE_DB` 全部指向临时目录，随机端口；前端 `vite` 5173 → 代理 8080。账号树：rootia（depth 0）→ member-one / member-two（depth 1）→ deep-one（depth 2）。

| 链路 | 真实结果 | 前端偏差 | 处理 |
|---|---|---|---|
| `GET /v1/auth/config` | 200；`root_account_exists / registration_open / recovery / migration / message` | 无（只读两个布尔） | — |
| 首次注册根 | `POST /accounts/register` 200，返回 `recovery_code` | 无 | — |
| 登录 / 当前身份 | 200；登录与 `/me` 均含 `parent_account_id/path/depth` | 无 | — |
| 重复注册 | 409 `ACCOUNT_ROOT_REGISTRATION_CLOSED` | 无（显示服务端中文原因） | — |
| 建成员（operator / manager） | 200，返回 `activation_code` | 无 | — |
| 激活成员 | 200，返回 `token` + `access_token` | 无 | — |
| 根读列表 | **200 且只返回直接成员**（2 条），条目含 `is_direct: true`，无 parent/depth/children | **是**：IA/前端仍按"整棵子树"假设；`is_direct` 未入库类型 | 更新类型与 IA（C11 关闭）；成员页改用直接列表 |
| 根读树 | 200，嵌套 3 层（rootia → member-one → deep-one） | 无 | 根列表改用 `tree.children`，不再把根自己当成成员行（根对自身写操作实测 403） |
| 成员读列表 | 200，仅 deep-one（直接成员） | **是**：`buildProfile` 在列表里找不到自己 → `accountPermissions=[]` → "我的成员"入口消失 | 权限回落到登录令牌；复测通过 |
| 成员读树 | 403 `ACCOUNT_SUBTREE_DENIED` | 无（非根不发该请求） | — |
| 成员越权读 / 写他人 | 404 `ACCOUNT_SUBTREE_DENIED`（中性） | 无（页面显示"无权执行该操作"） | — |
| 成员自改 Key 共享 | **403 `ACCOUNT_LEAPFROG_DENIED`**（后端与自身注释/文档冲突） | 无（页面显示"无权执行该操作"，不伪装成功） | 记后端问题 B1 |
| 根改成员 Key 共享 | 200；`account` 含 `allow_superior_key_use / key_auth_source` | **是**：该成员既有令牌立即失效（`401 CREDENTIAL_STALE`），页面未提示"对方需重新登录" | 成功文案补"对方需要重新登录后生效" |
| 改成员权限 | 200 | 无 | — |
| 暂停成员 | 200；返回 `target/status/affected/audit_retained/memory_archived/key_unbound_hint` | 无（只提示"已完成"） | — |
| 暂停后成员访问 / 登录 | `/me` 403 `ACCOUNT_UNAVAILABLE`；登录 403 但 detail 是实现细节（无 code） | **是**：登录页会把实现细节原样展示 | `auth.ts` 403 → "该账号当前不可用，无法登录" |
| 恢复成员 | 200 | 无 | — |
| 读用量（根） | 200；`scope=subtree`、`by_key_owner[]` + `by_actor[]` | 无 | — |
| 读用量（成员） | 200；`scope=self`、`callers_visible=false`、**无 `by_actor`** | 无（页面只在存在时渲染） | — |
| 成员读根用量 | 404 `ACCOUNT_SUBTREE_DENIED` | 无（只查自己） | — |
| 备份导出 | 200；`members=4`，`manifest{member_count, files, bytes, models, credentials, skipped}` | **是**：文案仍称"后端未提供统计"；确认人数来自直接列表（会少算更深层） | 改用整棵树人数；导出后展示真实 manifest |
| 备份导入（已有账号） | 409 `IMPORT_NOT_ALLOWED` | 无（显示服务端中文原因） | — |
| 恢复码轮换 | 200 | 无 | — |
| 未带令牌 | 401 `{detail: "missing bearer token"}` | **是**：`neutralDenial` 未覆盖 401，会透出英文协议细节 | 401 统一"登录状态已失效，请重新登录"，并清会话回登录页 |
| 错误密码 | 401 `凭据无效` | 无 | — |
| 改密后旧令牌 | 401 `CREDENTIAL_STALE` | **是**（同上） | 同上；浏览器复测已回到登录页 |
| 迁移端点 | 200（本轮无页面） | 无 | — |
| 备份模块未启用（旧 501 分支） | 本轮不存在：模块已落地，不再返回 501 | — | 501 分支仍由单测钉住（不落桩、显示真实原因） |

### 11.1 联调发现、仍属后端的缺口（未改后端）

- **B1**：成员对自己 `PATCH /accounts/{self}/key-sharing` 返回 `403 ACCOUNT_LEAPFROG_DENIED`，与 `accounts.py` 中"自己可随时开启/撤回共享"的说明矛盾；前端只能如实显示"无权执行该操作"。
- **B2**：根修改某成员的 Key 共享后，该成员既有令牌立刻 `401 CREDENTIAL_STALE`；前端已在成功文案提示"需要重新登录"，但若这不是有意的吊销策略，请后端确认。
- **B3**：暂停账号后 `POST /auth/login` 返回 403 且只给实现细节（无业务 `code`），而 `/auth/me` 同状态返回带 `code` 的中性响应；建议登录侧统一为 `ACCOUNT_UNAVAILABLE`。
- **B4**：未带令牌的 401 文案为英文 `missing bearer token`（无 `code`）；前端已中性化，但接口层建议统一中文/带 code。
- **B5**：登录响应 `scope=L7` 与 `/auth/me` 的 `scope=L3` 不一致（前端未消费 `scope`，仅记录）。
- **B6**：`GET /accounts/tree` 节点缺 `created_at` / `key_auth_source`，而平铺列表条目有；前端按可选字段处理，不影响渲染。

### 11.2 隔离实例回收确认（2026-11-06）

| 项 | 结果 |
|---|---|
| 网关进程（8080） | 已停止 |
| 前端 Vite（5173） | 已停止 |
| 端口复核 | `Get-NetTCPConnection -LocalPort 8080,5173` → **无监听** |
| 临时实例目录 `D:\cache\temp\liuhao-ia-runtime`（identities / audit / usage / workspace / run / port.txt） | 已删除，`Test-Path` = **False** |
| 探针脚本与报告 `D:\cache\temp\liuhao-ia-probe` | 已删除，`Test-Path` = **False** |
| 探针种子临时目录 `D:\cache\temp\tmp67iatbnb`（`ids.json`） | 已删除，`Test-Path` = **False** |

> 回收后无遗留临时数据库、无遗留端口占用。B1–B6 待后端修复后再跑对应六条界面链路，本轮不重复执行。

