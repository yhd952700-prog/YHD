# 多账号体系八项硬要求 —— 只读实测审计报告

- 审计日期：2026-11-08
- 审计对象：`d:\LiuHao-AI-OS` 当前工作树（未提交状态）
- 审计方式：起独立实例，用真实 HTTP 请求构造主账号 + 子账号 + 孙账号，逐项打正反例；不以读代码推断结论
- 实例 A（构造与验证）：端口 `19007`，数据目录 `D:\_audit\account-matrix`
- 实例 B（换机恢复验证）：端口 `19008`，数据目录 `D:\_audit\account-matrix-machineB`
- 隔离性：所有 DB、日志、工作区、密钥均落在审计目录；**未触碰 `D:\LiuHaoData`**
- 只读约束：**未修改 `src/`、`apps/`、`installer/`、`packaging/`**。审计驱动脚本仅置于 `.probe_tmp/`，审计结束后删除
- 原始证据：`D:\_audit\account-matrix\audit-result-20261108.json`（87 条探针结果 + 完整请求/响应转录）
- 测试账号：`boss`（主账号）/ `workerA`（manager，Key 共享开启）/ `workerB`（operator，Key 共享关闭）/ `workerA1`（workerA 的下级）/ `newbie`（自助注册）

> 说明：模型侧使用 mock provider，`/v1/chat` 的回复是演示文本；但账号用量账本（`by_key_owner.calls`）记录的是真实调用次数，可用于证明"某个 Key 是否真的被使用"。本报告所有"Key 被使用"的结论均以用量账本为准，不以回复文本为准。

---

## 一、八项判定表

| # | 要求 | 实测结果 | 关键证据 | 判定 |
|---|------|----------|----------|------|
| 1 | 主账号能看全部 | 通过 | 账号树 4 个节点；主账号可读 boss/workerA/workerB/workerA1 的资料、用量、审计事件；`GET /v1/accounts/tree` 200 | 🟢 |
| 2 | 子账号只能控自己的下级、不能越级 | 通过 | workerA 成员列表只含 workerA1；读/改 boss、workerB、workerA1 父级均 `404 ACCOUNT_SUBTREE_DENIED`（7 个正反例）；workerA 改 workerA1 权限 200，旧令牌随即 `401 CREDENTIAL_STALE` | 🟢 |
| 3 | 主账号能用自己的 Key 也能用子账号的 Key，子账号只能用自己那把 | 字面要求通过；授权门有额外缺陷 | 主账号自己的 Key 200；主账号用 workerA 已共享 Key 200；workerA 用自己 Key 200；workerB 用 workerA Key `403 ACCOUNT_KEY_NOT_OWNED`；workerA 用主账号 Key `403 ACCOUNT_KEY_NOT_OWNED`。**但**主账号使用 workerB 未共享 Key 仍 200（见附加发现 F1） | 🟢（附 F1 🔴） |
| 4 | 子账号的记忆归自己，主账号能看全部 | 通过 | workerA `scope=self` 只返回自己的记忆；workerB 读 workerA 记忆 `404 ACCOUNT_MEMORY_FOREIGN_DENIED`；workerA 请求 `scope=subtree` `404 ACCOUNT_SUBTREE_DENIED`；主账号 `scope=subtree` 返回 4 条，owners=`boss/workerA/workerA1/workerB` | 🟢 |
| 5 | 子账号不能知道自己是被创建出来的（界面不得出现"你由谁创建"这类暴露） | **失败（界面层通过、接口层失败）** | 浏览器实测 7 个页面，禁用词命中 0；但 `workerA` 的登录响应与 `GET /v1/auth/me` 都返回 `parent_account_id="boss"`、`path="/boss/workerA"`、`depth=1`，子账号可直接读出创建者与层级 | 🔴 |
| 6 | 主账号换机器忘密码有解（恢复码路径） | 通过 | B 机导入后用 A 机注册时拿到的恢复码重置成功 200；新密码登录 200；旧密码 401；旧令牌 `401 CREDENTIAL_STALE`；恢复码重复使用 `400 ACCOUNT_RECOVERY_CODE_INVALID`；重置后账号树完整 | 🟢 |
| 7 | 主账号能把全部打包带走（导出） | 通过 | 全量导出 200：members=4、记忆 4 条、会话 8 轮、文件 2 个、21981 字节；B 机导入 200：accounts=4、credentials=4、kernel_memory 4、conversations 8、文件恢复；导入后 `workerA-session` 可在工作台会话列表被发现 | 🟢 |
| 8 | 成员免邀请码自助注册 | **失败** | 直接调用 API（`password` 字段）200、可立即登录、默认不能建下级、开关关闭后 `403 ACCOUNT_SELF_REGISTER_DISABLED`；但控制台源码与构建包实际发送 `secret` 字段，真实请求返回 `422`（`password` 缺失），用户无法在控制台完成自助注册 | 🔴 |

判定统计：🟢 6 项、🔴 2 项、另附加发现 1 项（F1）。

---

## 二、逐项证据

### 第 1 项：主账号能看全部 —— 🟢

- `GET /v1/accounts/tree` → 200，树含 `boss → workerA/workerB → workerA1`，共 4 个账号。
- `GET /v1/accounts/workerA`、`/workerB`、`/workerA1` → 均 200。
- `GET /v1/accounts/{workerA,workerB,workerA1}/usage` → 均 200。
- `GET /v1/accounts` → 200，返回直接成员列表。
- `GET /v1/audit/events`（主账号身份）→ 200。

### 第 2 项：子账号只能控自己的下级、不能越级 —— 🟢

反向用例（全部被拒，均为 `404 ACCOUNT_SUBTREE_DENIED`）：

| 请求 | 结果 |
|------|------|
| workerA `GET /v1/accounts/boss` | 404 ACCOUNT_SUBTREE_DENIED |
| workerA `PATCH /v1/accounts/boss/permissions` | 404 ACCOUNT_SUBTREE_DENIED |
| workerA `GET /v1/accounts/workerB`（同级） | 404 ACCOUNT_SUBTREE_DENIED |
| workerA `PATCH /v1/accounts/workerB/permissions`（同级） | 404 ACCOUNT_SUBTREE_DENIED |
| workerB `GET /v1/accounts/workerA1`（别人的下级） | 404 ACCOUNT_SUBTREE_DENIED |
| workerA `GET /v1/accounts/tree` | 404 ACCOUNT_SUBTREE_DENIED |
| workerA1 `GET /v1/accounts/workerA`（自己的父账号） | 404 ACCOUNT_SUBTREE_DENIED |

正向用例：

- workerA `GET /v1/accounts` → 200，只返回 `workerA1`。
- workerA `PATCH /v1/accounts/workerA1/permissions` → 200。
- 权限变更后 workerA1 旧令牌立即 `401 CREDENTIAL_STALE`；重新登录后 200。

> 结论：子树边界按"直接下级"生效，同级、上级、祖先均不可见不可改；权限变更能即时失效旧令牌。

### 第 3 项：Key 归属与使用 —— 🟢（字面要求），附 F1 🔴

| 用例 | 结果 |
|------|------|
| 主账号用主账号自己的 Key | 200 |
| 主账号用 workerA 已共享的 Key（`allow_superior_key_use=true`） | 200 |
| workerA 用自己那把 Key | 200 |
| workerB 用 workerA 的 Key | 403 ACCOUNT_KEY_NOT_OWNED |
| workerA 用主账号的 Key | 403 ACCOUNT_KEY_NOT_OWNED |
| workerB 的模型列表 | 不包含主账号 Key |
| 主账号用 workerB 未共享的 Key（`allow_superior_key_use=false`） | **200（不应放行）** |

workerB 的 Key 授权状态确认为 `false`，但主账号以该 Key 发起 `/v1/chat` 后，`GET /v1/accounts/workerB/usage` 的 `by_key_owner` 出现 `{"key_owner":"workerB","calls":1}`，说明未共享的 Key 确实被主账号调用并计费。

### 第 4 项：记忆归属 —— 🟢

- workerA 写自己的记忆 → 201；`GET /v1/memory/entries?scope=self` → 200，仅含本账号条目。
- workerB 读 workerA 的单条记忆 → `404 ACCOUNT_MEMORY_FOREIGN_DENIED`。
- workerA 请求 `scope=subtree` → `404 ACCOUNT_SUBTREE_DENIED`。
- 主账号 `scope=subtree` → 200，`count=4`，条目 owner 覆盖 `boss`、`workerA`、`workerA1`、`workerB`。
- 主账号按单条读、按关键词搜 workerA 的记忆均 200。

### 第 5 项：不暴露"你由谁创建" —— 🔴（界面层通过，接口层失败）

界面层（Chrome DevTools 实测，workerA 登录 19007）：

- 页面：`overview`、`my-members`、`my-keys`、`my-data`、`identity`、`files`、`trust`。
- 禁用词命中：`主账号 / 子账号 / 上级 / 下级 / 层级 / 越级 / 创建者 / 创建人 / 由谁创建 / 你由 / 创建了你 / 被创建 / 父账号 / 根账号 / boss` → **全部 0 命中**。

接口层（同一会话直接读取响应）：

- `POST /v1/auth/login`（workerA）响应包含 `parent_account_id="boss"`、`path="/boss/workerA"`、`depth=1`。
- `GET /v1/auth/me`（workerA）响应同样包含上述三个字段。
- 浏览器在登录后本来就会调用 `/v1/auth/me`，因此子账号只要打开开发者工具网络面板即可直接看到创建者。控制台构建包静态扫描命中 0，只能证明"文案未渲染"，不能证明"信息不可得"。

### 第 6 项：换机 + 忘密码 → 恢复码 —— 🟢

流程：A 机注册主账号时获得恢复码 → 仅导出数据包（不含恢复码）→ B 机导入 → 用恢复码重置。

| 用例 | 结果 |
|------|------|
| B 机用 A 机恢复码重置 boss 密码 | 200 |
| 重置后新密码登录 | 200 |
| 重置后旧密码登录 | 401（账号或密码不正确） |
| 重置后旧 B 机令牌访问 | 401 CREDENTIAL_STALE |
| 重置后账号树/数据 | 完整 |
| 恢复码重复使用 | 400 ACCOUNT_RECOVERY_CODE_INVALID |

### 第 7 项：全量导出与迁移 —— 🟢

导出（主账号，`scope=all`，AES-256-GCM + PBKDF2-SHA256 200000 次）：

- `members=4`
- `account_memory.entries=4`（owners 覆盖 4 个账号）
- `conversation` 8 轮
- `files=2`，`bytes=21981`

B 机导入：

- `accounts=4`、`credentials=4`、`kernel_memory.imported=4`、`conversations.imported=8`、`files.restored=1 / skipped=1`
- 导入后 boss 原密码 200、workerA 原密码 200
- 导入后 workerA 记忆可搜；workerA 仍看不到他人记忆（404 ACCOUNT_MEMORY_FOREIGN_DENIED）
- `GET /v1/chat/sessions` → `{"count":1,"sessions":["workerA-session"]}`，工作台能发现导入的会话
- 文件内容可读

导出包按设计**不含**成员 Key 与模型主密钥：`member_keys=False`、`master_key=False`、`key_material_included=false`（导入响应 manifest）。即：账号、记忆、会话、文件可带走，凭据不随包迁移。

### 第 8 项：成员自助注册 —— 🔴

API 层（按后端真实字段 `password`）：

- `POST /v1/accounts/self-register {"principal":"newbie","password":"..."}` → 200
- 新账号立即 `POST /v1/auth/login` → 200，`depth=1`
- 新账号默认不能建下级：`GET /v1/accounts` → `404 ACCOUNT_SUBTREE_DENIED`
- 主账号关闭开关后：`403 ACCOUNT_SELF_REGISTER_DISABLED`（文案中性，未暴露层级）
- 重新开启后 200；新成员出现在主账号树中

控制台层（真实前端载荷）：

- `apps/console/console/src/lib/auth.ts` 的 `registerSelf()` 发送 `{principal, secret, client}`
- `apps/console/console/src/components/Login.tsx` 的"创建账户"流程同样发送 `secret`
- 构建产物 `apps/console/console/dist/assets/index-BslW_JJ5.js` 中同样发送 `secret`
- 真实请求结果：`422`，`{"detail":[{"loc":["body","password"],"msg":"Field required","type":"missing"}]}`

即：后端接口具备自助注册能力，但控制台用错字段名，普通用户从界面上无法完成注册。

---

## 三、红项复现请求与期望行为

### R1（第 5 项）子账号可从接口读出创建者与层级

复现：

1. 以主账号创建 `workerA` 并激活。
2. `workerA` 登录，例如：

```http
POST /v1/auth/login
Content-Type: application/json

{"principal":"workerA","password":"<workerA 密码>","client":"web"}
```

3. 用返回的令牌请求：

```http
GET /v1/auth/me
Authorization: Bearer <workerA token>
```

实际响应（节选）：

```json
{
  "principal": "workerA",
  "parent_account_id": "boss",
  "path": "/boss/workerA",
  "depth": 1,
  "permissions": ["account:audit_read", "account:grant", "account:manage_children", "..."],
  "client": "web"
}
```

期望行为：

- 子账号可见的 `/v1/auth/me` 与登录响应**不包含** `parent_account_id`、`path`、`depth` 等描述其被创建位置的字段（或对非根账号整体省略）。
- 若界面需要 `depth` 做入口裁剪，应由服务端下发一个与创建关系无关的能力布尔值（如 `can_manage_children`），而不是原始层级数据。
- 校验方式：workerA 令牌的 `/v1/auth/me` 响应体中不出现 `parent_account_id`、`path`、`depth` 三个键。

### R2（第 8 项）控制台自助注册字段不匹配

复现（模拟控制台真实请求）：

```http
POST /v1/accounts/self-register
Content-Type: application/json

{"principal":"newbie_ui","secret":"<密码>","client":"web"}
```

实际响应：

```http
HTTP/1.1 422 Unprocessable Entity

{"detail":[{"loc":["body","password"],"msg":"Field required","type":"missing"}]}
```

对照：同一意图把字段改为 `password` 时返回 200，说明服务端能力存在。

期望行为：

- 控制台发起自助注册后返回 200 与可用会话令牌，用户直接进入主界面。
- 前端与后端字段统一（二者选一：前端改发 `password`，或后端接受 `secret` 作为别名），并补一条真实前端载荷的回归测试，避免构建包与源码再次漂移。

### R3（附加发现 F1）主账号可绕过成员 Key 共享授权

复现：

1. `workerB` 创建自己的 Key，且其授权状态为 `allow_superior_key_use=false`。
2. 主账号（boss）用该 Key 的 `model_id` 发起对话：

```http
POST /v1/chat
Authorization: Bearer <boss token>
Content-Type: application/json

{"message":"root tries unshared key","model_id":"0251938e86fa","session_id":"root-try-b","use_knowledge":false}
```

实际响应：`200`，并生成回复（mock provider 演示文本）。

3. 查用量：

```http
GET /v1/accounts/workerB/usage
Authorization: Bearer <boss token>
```

实际响应片段：

```json
{"by_key_owner": [{"key_owner": "workerB", "calls": 1, "...": "..."}]}
```

期望行为：

- 未开启共享的成员 Key 不应被上级用于 `/v1/chat`，应返回 `403 ACCOUNT_KEY_CONSENT_REQUIRED`（或与现有 Key 拒绝语义一致的错误码），且用量账本不应新增该 owner 的调用。
- 根因方向（仅记录，未修改代码）：`src/ai/providers.py` 中显式 `model_id` 的解析调用 `store.resolve(model_id, visible_to, allow_unowned)` 时未传入 `allow_owners`，而其他路径（`src/gateway/account_context.py` 的 `key_scope_for`）是按 `allow_superior_key_use` 过滤的。两条路径的授权口径不一致。

---

## 四、旧问题复核（D1）

此前文档记录："导入后工作台会话列表为空，会话不可发现"。

本轮在 B 机导入后实测：

```http
GET /v1/chat/sessions
Authorization: Bearer <workerA token>
```

```json
{"count": 1, "sessions": ["workerA-session"]}
```

当前代码**未复现**该问题：导入的会话可按 `session_id` 读到，也能在工作台会话列表中被发现。该条历史结论应视为已修复或不再成立。

---

## 五、结论

- 账号树、子树管控、记忆隔离、换机恢复码、全量导出迁移这五项核心能力，在当前工作树上经真实请求验证成立。
- 两项硬要求未达成：
  - **第 5 项**：界面文案干净，但 `/v1/auth/login` 与 `/v1/auth/me` 直接下发 `parent_account_id` / `path` / `depth`，子账号可通过浏览器开发者工具知道自己由谁创建、处于第几层。
  - **第 8 项**：自助注册 API 可用，但控制台发送 `secret` 而后端只收 `password`，真实用户从界面注册会得到 422。
- 一项额外授权缺陷：**F1** —— 主账号可实际使用未共享的成员 Key，与 `allow_superior_key_use=false` 的语义冲突，且用量账本可证明调用真实发生。
- 导出包按设计排除成员 Key 与模型主密钥（`member_keys=false`、`master_key=false`、`key_material_included=false`），其余账号数据、记忆、会话、文件完整可迁移。

本次为只读审计：以上红项仅记录证据与复现方式，未修改 `src/`、`apps/`、`installer/`、`packaging/` 中的任何文件；19007、19008 实例均已停止，未触碰 `D:\LiuHaoData`。
