# 账号层级体系规格（ACCOUNT HIERARCHY SPEC）

> 状态：**规格 / 仅文档**。本文件是唯一交付物，**不含任何代码改动**（不新增模块、不改前端、不迁移库、不提交、不 push）。
> 适用对象：鎏灏 LIUHAO AI OS 的主账号 / 子账号 / 孙账号体系。
> 本文所有"现状"均指本仓当前代码（路径 + 行号可核对）；"要求"是目标行为，供后续实现按此落地。

---

## 0. 口径（不可改写）

以下四条为产品定稿口径，本文全部设计只做展开，不做改写：

1. **主账号能控制所有子账号的权限。**
2. **子账号只能控制自己的子账号，不能越级。**
3. **主账号可以使用子账号的 Key；子账号只能用自己个人的 Key。**
4. **子账号的记忆属于该子账号；主账号拥有所有子账号的记忆与技能。**

补充定稿（登录与界面）：

5. **同一个登录页、同一个控制台，按身份裁权限；不做两套入口、不做两套客户端。**
6. **首次安装：第一个注册者自动成为主账号（根）；根一旦存在，永久关闭"注册根"入口（不允许再注册成根）。成员改为自助注册（本条第 22 项，覆盖早期"子账号只能由上级创建"的表述）：注册即以 `depth = 1` 挂到本机根，无需邀请码、无需批准。**
7. **成员开通（两条并存，权限等价）**：①**自助注册**（用户名 + 密码即建号，立即可登录）；②**主账号创建成员 + 激活码**（一次性 + 限时 + 强制改密后进控制台）。
8. **顶部固定"身份条"**：根账号显示"主账号 · 可管理 N 位成员"；**非根账号显示中性身份条，不含任何层级字样**（本条第 11 项修订，旧版"子账号 · 上级 XXX"作废）。
9. **"主账号使用子账号的 Key"是模型路由，不是身份切换**；审计记在主账号名下并标注所用 Key 的归属账号。
10. **"以成员视角只读查看"只读、带明显横幅、写审计，禁止静默以他人身份执行写操作。**

补充定稿（二）：

11. **界面区分方式 = 只做能力裁剪，不贴身份标签。** 不给子账号显示"子账号/下属/上级/层级/越级"等任何字样；子账号看到的是一个完整的、属于它自己的工作空间。
12. **主账号专属能力（核心能力）**：成员与权限管理、权限矩阵、整棵子树的记忆/用量/审计全量、跨机认领与备份导出、系统升级与受控自改代码、全部 Key 统一管理。**子账号保留完整日常体验**：自有对话、管家、知识库、文件、自动化、自有 Key。子账号界面**不出现**上述任何入口，也不出现解释性文案。
13. **跨机认领**：主账号可导出加密凭据包（身份、成员树、设置、Key 密文与**全部成员数据**）；新机器安装后走中性文案入口"从备份迁移到本机"，导入即认领。**真跨设备实时同步需要服务端，明确记为"待决策 / 不做"**，不得在任何文案里承诺"实时同步"。
14. **忘记密码**：主账号创建时一次性展示恢复码，并提供可离线保存的恢复文件；忘记密码用恢复码重置；**两者皆失**时进入"重建身份体系"引导（数据保留，但须重新认领）。不可逆边界必须写明（见 §9.11）。
15. **Key 使用的知情边界**：创建子账号时有一次"**允许团队共享我的 Key**"的授权勾选（中性文案），可随时撤销；子账号界面**不暴露调用细节**；但**供应商后台的额度消耗不可隐藏**——不得把"完全不可知"写成承诺（见 §5.6）。

补充定稿（三）：

16. **单机一棵树**：一台机器只有一个根账号，所有成员挂在这棵树下；**同机不允许多个根（不做森林）**。登录定位账号的方式与歧义处理见 §1.4。
17. **数据按账号分区**：会话、记忆、文件、任务、向量库、用量全部按账号隔离；各自的存储键与迁移方式见 §3.10。"**串数据**"（甲的数据出现在乙的列表/检索结果里）列为**最高优先级反例**（D0）。
18. **权限变更即时生效**：降权、停用、Key 归属变更后，已签发凭据在**下一次请求即被拒**，不等重新登录。沿用现有吊销预检 fail-closed 机制（`src/security/jwt_handler.py:576-601`，第 600 行按 `jti` 查吊销表；吊销表为 SQLite 持久化，`jwt_handler.py:162-205`）。
19. **层级上限默认 3 层**（根 → 成员 → 该成员的下级），可配置（`max_depth`）；超过上限给**可读拒绝理由**，且文案不得暴露结构（§4.4）。
20. **文案中性化**：子账号界面的 Key 授权开关写"**允许团队共享我的 Key**"，不得出现"上级/子账号/层级/越级"。
21. **备份隐私边界（覆盖上一版"默认不含成员记忆"）**：**主账号导出完整备份 = 整棵树全部数据**（所有成员的会话、记忆、文件、任务、Key 密文）；换机导入后成员身份与数据完整恢复。导出前**二次确认**，确认框必须列明"包含 N 位成员的个人记忆、N 个文件、总体积"；导出包用**口令加密**（口令不落盘、不写日志），每次全量导出写审计；**子账号导出仍只含自己的个人数据**；导入同样要口令，并防重放、限尝试次数（见 §9.12）。

补充定稿（四，2026-11-06）：

22. **成员自助注册（覆盖第 6 项中"子账号只能由上级创建"的旧口径）**：
    - **两条路径并存**：①登录页"创建账户"自助注册（用户名 + 密码即建号，立即 `active`）；②主账号在「成员管理」创建成员并发放激活码（保留，见 §9.4）。
    - **注册开关**：`allow_self_registration` **持久化、默认开启、仅根账号可改**；关闭后注册接口一律中性拒绝（`ACCOUNT_SELF_REGISTER_DISABLED`，HTTP 403，文案"当前无法创建账户。"，不提"管理员/开关/层级"）。
    - **硬边界**：自助注册**不能自选上级**（请求体中的 `parent_account_id` / `role_kind` / `depth` 一律**忽略且不参与建号**，不接受为上级或角色）、**不能注册成根**（根已存在；`role=root` 不允许）、**默认不能建下级**；新账号固定 `depth = 1`，层级与深度规则（第 16、19 项）不变。
    - **默认最小权限（模板"协作者"）**：不可使用他人/根账号的 Key；他人模型不可见；他人记忆、文件、会话、审计不可见；不可建下级（§3.12）。
    - **主账号可随时停用/删除**自助注册账号；停用后登录与请求均中性拒绝，且旧令牌**下一次请求即失效**（第 18 项）。
    - **登录页文案**：只出现"**登录 / 创建账户**"；不得出现"邀请码、加入、成员、子账号、上级、层级"（§9.9）。
    - **口径优先级**：第 22 项覆盖第 6 项的旧表述；与代码现状冲突时（后端 `POST /v1/accounts/self-register` 与 `GET/PATCH /v1/accounts/self-registration` 已实现，`/v1/auth/config` 尚未暴露开关、登录页仍把"创建账户"绑在"无根"上，见 §12）**以本文为准**。

---

## 1. 术语与账号树模型

### 1.1 三类"身份"必须分开

| 概念 | 字段（建议） | 含义 | 谁能改 |
|---|---|---|---|
| 真实操作者 | `actor_account_id` | 令牌 `sub` 对应的账号，唯一可信来源 | 谁都不能改（只来自令牌） |
| 生效视角 | `effective_account_id` | 实际被代表/被查看的账号；默认等于 actor | 只能由"视角"机制设置，且写操作禁止 |
| Key 归属 | `key_owner_account_id` | 本次模型调用所用 Key 的 owner | 由 Key 记录决定，不由请求体决定 |

**禁止把三者混成一个字段。** 现有 `api_key_owner`、`principal`、`scope` 都无法同时表达这三层，混用会产生"看起来是子账号、实际用主账号权限"的静默提权。

### 1.2 账号实体

| 字段 | 说明 |
|---|---|
| `account_id` | 稳定、不可变、全局唯一（建议沿用现有 `principal` 字符串规则） |
| `parent_account_id` | 上级账号；根账号为 `null` |
| `path` | 物化路径，形如 `/root/sub1/grand1`；**子树判定唯一依据** |
| `depth` | 根 = 0，子 = 1，孙 = 2，以此类推 |
| `status` | `pending_activation` / `active` / `suspended` / `deleting` / `deleted` |
| `role_kind` | `root` / `member`（孙及更深统一为 `member`，不新增第四种角色） |
| `credential_state` | `none` / `activation_pending` / `password_set`（自助注册直接为 `password_set`） |
| `origin` | `bootstrap_root` / `self_signup` / `created_by_admin`（仅审计与迁移用，**不展示给非根账号**） |

不变量：

- **单根**：全库恰好一个 `parent_account_id = null` 且 `status = active` 的账号；初始化前允许 0 个。
- **无环**：`path` 必须以 `parent.path + "/" + account_id` 构造；任何写入前校验父节点存在且 `status != deleted`。
- **单机单树**：一台机器只允许一个根；禁止"森林"（多根）。检测到多个根即 fail-closed：拒绝登录并记 `ACCOUNT_MULTI_ROOT_DETECTED`，不做"自动挑一个"的猜测。
- **深度上限**：**默认 3 层**（根 depth 0 → 成员 depth 1 → 成员的下级 depth 2），由 `max_depth` 配置；默认拒绝 depth 3 及更深。超过返回 `ACCOUNT_DEPTH_EXCEEDED`（默认配置下即"第 3 层成员再建下级"被拒）。
- **子树包含判定**：`subtree_contains(ancestor_path, target_path) := target_path.startswith(ancestor_path + "/") or target_path == ancestor_path`。**这是唯一判定式**，不靠 `scope`、不靠名字前缀、不靠请求体。
- **自助注册落点**：`origin = self_signup`（代码字段 `source = self_service`）的账号一律 `parent_account_id = <本机根>`、`depth = 1`、`status = active`、`credential_state = password_set`；请求体中的 `parent_account_id`/`role_kind`/`depth` **一律忽略且不生效**（现状 `SelfRegisterRequest.model_config = {"extra": "ignore"}`，不得把它们当作上级/角色/深度来源）。

### 1.3 四档关系（可见性矩阵的列）

| 档位 | 定义 |
|---|---|
| **自己** | `target == actor` |
| **直接下级** | `target.parent_account_id == actor` |
| **子树其它** | `subtree_contains(actor.path, target.path)` 且非自己、非直接下级 |
| **子树外** | 不满足上述任一（含上级、兄弟、兄弟的子树） |

> 注意：**上级属于"子树外"**。这正是"子账号不可向上借用/查看"的判定基础。

### 1.4 登录如何定位账号（单树下的消歧）

| 规则 | 内容 |
|---|---|
| 登录唯一键 | `account_id`（全局唯一，统一大小写/空白规范化）；**显示名不参与登录**，避免"同名不同人" |
| 定位算法 | 规范化后**精确匹配**：命中 1 条 → 正常鉴权；命中 0 条 → 统一失败（不区分"账号不存在"与"密码错误"，防枚举）；命中 >1 条 → 视为数据损坏，拒绝并提示"账号配置异常，请联系系统管理员"，审计 `ACCOUNT_AMBIGUOUS`（`outcome=denied`） |
| 单机单树约束 | 因同机只有一个根，`account_id` 天然唯一；若迁移导入与现有账号重名，**拒绝导入**（`ACCOUNT_MIGRATION_CONFLICT`），不得自动覆盖或改名 |
| 别名登录 | v1 不支持邮箱/手机号/工号别名登录；确需别名时由"账号别名表"显式映射，且仍解析到唯一 `account_id` |
| 歧义文案 | 一律中性："登录信息不正确。"（禁止"该账号是子账号/你的上级是谁"等任何结构信息） |

---

## 2. 角色与 scope 矩阵

### 2.1 能力矩阵（主 / 子 / 孙）

"孙"代表 `depth >= 2` 的账号。**默认 `max_depth = 2`（3 层：根 / 成员 / 成员的下级）**；配置放宽后，更深层账号仍按"孙账号"规则执行（不引入第四种角色）。

| 能力 | 主账号（depth 0） | 子账号（depth 1） | 孙账号（depth >= 2） |
|---|---|---|---|
| 创建下级账号 | 可（创建 depth 1 成员；更深层由各自直接上级创建） | 仅可创建自己的直接下级（需模板含成员管理；**自助注册默认不可**） | 不可（默认上限） |
| 停用 / 恢复下级 | 可（整棵子树） | 仅可直接下级 | 不可 |
| 删除下级账号 | 可（**仅叶子**；非叶子须先移交/级联停用） | 仅可直接下级且为叶子 | 不可 |
| 重置下级密码 | 可（整棵子树） | 仅可直接下级 | 不可 |
| 修改下级权限/角色 | 可（整棵子树） | 仅可直接下级，且**不得超过自己已有的能力** | 不可 |
| 查看下级账号列表 | 可（整棵子树） | 仅可直接下级（**自助注册默认无下级，只看自己**） | 不可 |
| 使用下级模型 Key | 可（整棵子树，按 §5） | **不可**（只能用自己个人的 Key） | **不可** |
| 新建 / 吊销自己的 Key | 可 | 可（仅自己） | 可（仅自己） |
| 读自己记忆 | 可读写 | 可读写 | 可读写 |
| 聚合读下级记忆 | 可（整棵子树，只读，见 §6） | 不可（下级的记忆属于下级） | 不可 |
| 授予技能给下级 | 可（整棵子树） | 仅可直接下级，且只能授予自己已持有的技能 | 不可 |
| 查看审计 | 可（整棵子树，只读） | 仅自己 + 直接下级 | 仅自己 |
| 查看用量/费用 | 可（整棵子树） | 仅自己 + 直接下级 | 仅自己 |
| 审批 HIGH/CRITICAL 动作 | 可（自身动作） | 可（自身动作，不可代下级签发全树授权） | 可（自身动作） |
| 以成员视角只读查看 | 可（只读，需横幅+审计） | 可（仅直接下级，只读） | 不可 |
| **核心能力：成员与权限管理** | 可 | **不可** | **不可** |
| **核心能力：权限矩阵** | 可（整棵子树） | **不可** | **不可** |
| **核心能力：子树记忆/用量/审计全量** | 可 | **不可**（仅自己 + 直接下级） | **不可** |
| **核心能力：跨机认领 / 备份导出** | 可 | **不可** | **不可** |
| **核心能力：系统升级 / 受控自改代码** | 可 | **不可** | **不可** |
| **核心能力：全部 Key 统一管理** | 可 | **不可**（仅自有 Key） | **不可**（仅自有 Key） |
| **日常能力：对话 / 管家 / 知识库 / 文件 / 自动化** | 可 | 可（自有范围，完整体验） | 可（自有范围，完整体验） |
| **日常能力：自有 Key 创建/轮换/吊销** | 可 | 可（仅自己） | 可（仅自己） |

两条硬约束：

- **向下不放大**：任何账号授予下级的权限，必须是它自己权限的子集（`grant ⊆ actor.effective_permissions`），否则 `ACCOUNT_PRIVILEGE_ESCALATION`。
- **向上不继承**：下级永远不自动获得上级的任何权限；只有显式授予才生效，且授予可撤销、可审计。
- **模板可收窄**：上表中子账号的"管理类"能力（创建/停用/删除/重置/查看下级、授予技能）以角色模板已包含该能力为前提（§7.5）；"协作者/只读访客"模板不含成员管理，此时对应行为一律不可用，但**不影响**其日常能力。
- **自助注册默认**：固定"协作者"模板——只拥有日常能力与自有 Key；**不可建下级、不可见任何他人资源**（§3.12）；若需管理下级，必须由主账号显式修改模板/权限（写审计 `ACCOUNT_PERMISSION_CHANGED`，`ACCOUNT_SELF_REGISTERED` 只记建号来源）。

### 2.2 与现有 RBAC / ABAC 的衔接

现有三套权威（详见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md` §1）：A = `src/kernels/security/__init__.py`（冒号权限 + 角色）、B = `src/kernels/policy/__init__.py`（点号动作白名单）、C = `src/security/rbac.py`（资源枚举，租户边界）。

| 权威 | 现状 | 本规格要求 | 是否需要改代码 |
|---|---|---|---|
| A（RBAC/ABAC） | `_principal_roles` / `_principal_attrs` 以 principal 为键；`set_principal_scope` 记 clearance 上限 | **角色绑定键改为 `account_id`**；ABAC 属性必须含 `actor.path`、`actor.depth`、`target.path`、`resource.owner_account_id`；**新增路径包含判定**，不能只用 scope | 是（仅新增判定与属性，不改已有判决语义） |
| B（policy 动作白名单） | `INTERNAL_SERVICE_ALLOWED_ACTIONS` 只按 `actor.type` + `action.name` 放行，不认识 principal（`_adjudicate` 无 principal 参数） | **不承担账号层级授权**；账户判定必须在 A/网关层完成，B 仅作动作级第二道防线 | 否（维持职责切分） |
| C（`src/security/rbac.py`） | `ResourceType` + `PermissionAction` 枚举 | **作为租户/账号边界**：每次 `check_access` 必须携带 `account_path`，同路径前缀才允许 | 是（补 tenant 维度，不改枚举） |

### 2.3 与 `require_human_principal` 的衔接

现状（`src/gateway/policy.py:158-171`）：只做**认证**——从令牌取 `sub`，要求 `metadata.kind == "human"`，不做任何业务授权。这个语义**保持不变**。

现状（可核对，勿误判为"尚不存在"）：`src/gateway/account_context.py` 已有 `AccountContext`（`:33`），账号树判定在 `src/kernels/identity/_accounts.py`（`subtree_of:261`、`can_manage:292`、`can_borrow_key:316`、`max_depth:371`）。但这些**尚未接入任何路由**（全仓仅 `src/kernels/identity/__init__.py:1072` 一条注释指向），**不能当作已生效的安全边界**。

仍需补：

- `require_account_principal`：把 `AccountContext` 接成正式依赖，在 `require_human_principal` 之上解析 `{account_id, path, depth, status, scope, permissions}`；账号 `suspended/deleted` 一律 403。
- `require_account_admin_of(target_account_id)`：把"是不是管理员"从**全局**改为**相对目标**。现状 `require_admin_principal`（`policy.py:174-187`）只要角色命中 `admin/administrator/owner`（`policy.py:116`）或 scope 为 `L0`（`policy.py:123`）就放行——**子账号带 admin 角色即可操作全树，这是必须修掉的越级面**。
- `require_model_admin`（`src/gateway/models.py:66`）同样需要改成 `require_account_admin_of(key_owner_account_id)`，否则子账号能改全站模型 Key。

### 2.4 与 L0 / L1 / L7 的衔接

现状事实：

- `src/gateway/auth.py:467-487` `_effective_scope`：`owner` → 存库 scope（L0）；`admin/administrator` → `L1`；其余 → `L7`。
- `src/gateway/policy.py:123` 把 `L0` 视为写权限；`policy.py:127` 认 L0–L7 为合法 scope。
- `src/kernels/security/__init__.py:238-253` `_enforce_principal_scope`：请求 scope 的层级数字 **大于** principal 上限即 DENY。
- `src/gateway/audit.py:91-100`：只有 `L0` 能跨主体读审计。
- 记忆内核 `MemoryScope{L0..L7}`（`src/kernels/memory/__init__.py:40-49`），`_scope_matches` 用"条目 scope 序号 >= 查询 scope 序号"放行（`__init__.py:366-369`）。

**映射规则（本规格定稿）：**

| 账号位置 | scope | 说明 |
|---|---|---|
| 根（depth 0） | `L0` | 唯一 L0。人类主权，可跨子树读审计 |
| 子（depth 1） | `L1` | 管理自己的直接下级，但**不因 L1 获得全局管理权** |
| 孙（depth 2，默认上限） | `L2` | 叶层 |
| 更深（depth d，仅配置放宽后） | `Ld`，封顶 `L7` | 受 `max_depth` 约束；默认不存在 |

三点必须钉死：

1. **`L` 是 clearance（权限高低），不是命名空间。** 同一层的两个兄弟都可能是 `L1`，仅凭 L 值无法区分"我的子树"和"别人的子树"。
2. **`_enforce_principal_scope` 只能当上限用，不能当隔离用。** 反例：A、B 都是 L1 兄弟；A 请求作用于 B（同为 L1），`1 <= 1` 不会被拒。因此**必须叠加 §1.2 的 `subtree_contains`**，缺一不可。
3. **`_effective_scope` 必须改为按 depth 派生**（现状"非 owner 一律 L7"会让子账号无法管理下级）。派生失败或 depth 越界时 fail-closed 到 `L7`，不得默认提升。

### 2.5 需要改的现有模块（只列清单，不落代码）

| 模块 | 现状位置 | 需要动的点 |
|---|---|---|
| `src/gateway/policy.py` | `require_human_principal:158`、`require_admin_principal:174`、`_is_admin_claim:148` | 新增 `AccountContext` 依赖与"相对目标的管理员"判定；保留原依赖以兼容既有路由 |
| `src/gateway/auth.py` | `_effective_scope:467`、`authenticate:408`、`/auth/login:519`、`/auth/me:528`、`/auth/config:728` | scope 按 depth 派生；登录返回值增加 `parent_account_id/path/depth`（现状 `_account_position:579` 已返回）；`/auth/config` 需拆成 `bootstrap_required`（无根）与 `self_registration_enabled`（根存在时的持久化开关，默认 true）——**后者尚未返回** |
| `src/gateway/accounts.py` | `register_root:134`、`self_register:200`、`get/set_self_registration:229/239` | **已实现**成员自助注册与开关（`GET/PATCH /v1/accounts/self-registration`，仅根可改）；保留激活码路径；需补 `/register` 对"已有根"语义的说明，避免与 `/self-register` 混淆 |
| `src/kernels/identity/__init__.py` | `IdentityScope:297`、`AgentIdentity:317`、`is_human_identity:274`、`_persist_human_identity:842` | 身份增加 `parent_account_id/path/depth/status` 持久化字段（现库无此列） |
| `src/gateway/account_context.py` | 已存在（未跟踪）：`AccountContext:33`、`NEUTRAL_DENIED:29` | 接到路由；补 `require_account_admin_of(target)`；`standalone` 兼容路径只允许"只有自己"，不得成为提权通道 |
| `src/kernels/identity/_accounts.py` | `DEFAULT_MAX_DEPTH=2:101`、`SELF_SERVICE_SCOPES:72`、`CODE_*:121-158`、`max_depth:371` | 默认 3 层已对齐本文；错误码已细分；**激活码 TTL 代码为 24h（`:107`），本文 §9.4 写 72h** → 以代码 24h 为当前口径，若产品要 72h 需改 `DEFAULT_ACTIVATION_TTL_SECONDS` |
| `src/kernels/identity/_backup.py` | 已存在（未跟踪）：PBKDF2-200k + AES-256-GCM、`bundle_id` 防重放 | 已满足 §9.12 的口令与防重放骨架；需接线导入尝试计数与 `BACKUP_EXPORTED/IMPORTED/REJECTED` 审计 |
| `src/kernels/security/__init__.py` | `set_principal_scope:226`、`_enforce_principal_scope:238`、`_principal_roles:189` | 角色/属性绑定加 `account_id` 维度；新增路径包含判定（与 scope 上限串联） |
| `src/kernels/security/_permission_map.py` | `PERMISSION_MAP:113-133` | 权限到动作的可见性映射补账户维度说明（不改映射语义） |
| `src/security/rbac.py` | `ResourceType:21`、`PermissionAction:35`、`check_access:566` | 调用方必须带 `account_path`；拒绝跨路径 |
| `src/gateway/identity.py` | `list_principals:261`、`get_principal:324` | 现状只要求"是 human"，任何人类可列全站主体 → 必须裁剪为"自己 + 允许的子树" |
| `src/gateway/models.py` | `require_model_admin:66`、`create_model:348` 等 | 管理员判定改为相对 Key 所属账号 |
| `src/ai/model_config.py` | 模型表 `ai_model_configs`（`alembic/versions/006_runtime_state_baseline.py:170-185`） | 增 `owner_account_id/created_by_account_id`；Key 明文永不出接口 |
| `src/security/api_keys.py` | `APIKey:67-96`（**无 owner 字段**） | 平台 Key 绑 `owner_account_id`；`list_keys:200` 按子树过滤 |
| `src/gateway/audit.py` | `_caller_identity:91`、`list_events:250`、`get_event:318`、`summary:444` | 跨主体读从"仅 L0"改为"子树根可读子树"；写事件补账户字段（见 §8） |
| `src/gateway/chat.py` | `_session_key:86`、`list_sessions:135`、`delete_session:151` | 会话已按 principal 隔离，保持；补"主账号只读下级会话"需另开接口，不放进现有写路径 |
| `src/kernels/memory/__init__.py` + `store.py` | `store:236`、`recall:286`、`key_hash` 主键（`store.py:65`） | 增 `owner_account_id`，**`key_hash` 必须改为 `H(account_id || key)`**，否则两个账号同名 key 互踩 |
| `src/cost/cost_tracker.py` | `record_cost:245`（无 principal/payer 参数） | 增 `payer_account_id`(=key_owner)、`usage_account_id`(=actor)、`key_owner_account_id` |
| `src/ai/employee_store.py` | `EmployeeStore:41`、单文件按 name 索引 | 增 `owner_account_id`；同 name 不同账号不得覆盖 |
| `apps/console/.../components/Login.tsx` | `notConfigured:143`、`noEligible:144`、`registrationOpen:147`、`register` 页签与 `SelfRegisterForm:255,395` | 已有"登录 / 创建账户"页签；需补 **S1c（开关关闭隐藏创建账户）**、读 `self_registration.enabled`，并把注册请求字段改为 `password`（§12） |
| `apps/console/.../lib/nav.ts` / `Shell.tsx` / `App.tsx` | `nav.ts:44-76`、`Shell.tsx:108-117`、`App.tsx:534-572` | 导航按身份裁剪；顶部身份条；视角横幅；成员管理/权限矩阵两个新入口（仅主账号可见） |
| `openapi.yaml` | 实测**无** `/v1/accounts`、`/v1/users`、`/v1/members`、`/v1/keys`、`/v1/tenants` 路径 | 后续需补账户域接口契约，含 `POST /v1/accounts/self-register`（`{principal, password, display_name}`）与 `GET/PATCH /v1/accounts/self-registration` |

---

## 3. 可见性矩阵

图例：**可读** = 可查看（含掩码/汇总）；**可写** = 可创建、修改、删除、授权；**不可见** = 列表不返回、详情返回 404、不可枚举。

### 3.1 账号与成员树

> **自助注册默认（协作者模板）**：下表"直接下级 / 子树其它 / 子树外"三列一律**不可见**；成员树只显示自己（§3.12）。

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 账号列表 / 成员树 | 可读 | 主：可读可写；子：可读可写；孙：不可见 | 主：可读可写；子：不可见；孙：不可见 | 不可见 |
| 账号详情（含权限） | 可读（不可写自己的权限） | 主：可读可写；子：可读可写（不得超出自身）；孙：不可见 | 主：可读可写；子/孙：不可见 | 不可见 |
| 密码 / 凭据 | 可写（仅自己） | 主：可写（重置）；子：可写（重置直接下级）；孙：不可见 | 主：可写；子/孙：不可见 | 不可见 |

### 3.2 模型 Key

> 明文 Key **永不**出现在任何 API 响应或界面；"使用"是服务端路由行为。

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| Key 列表 / 归属 / 掩码 | 可读 | 主：可读；子：不可见；孙：不可见 | 主：可读；子/孙：不可见 | 不可见 |
| 新建 / 轮换自己的 Key | 可写 | 不可见 | 不可见 | 不可见 |
| 吊销 / 停用 Key | 可写（仅自己） | 主：可写（整棵子树）；子：不可写（下级 Key 属下级）；孙：不可见 | 主：可写；子/孙：不可见 | 不可见 |
| **使用**该 Key 发起模型调用 | 可写 | 主：可写（**需存在有效共享授权**）；**子：不可写**；孙：不可写 | 主：可写（需授权）；子/孙：不可写 | 不可见 |

### 3.3 会话（对话历史）

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 会话列表 | 可读 | 主：可读（只读）；子：不可见；孙：不可见 | 主：可读（只读）；子/孙：不可见 | 不可见 |
| 会话内容 | 可读 | 主：可读（只读）；子：不可见；孙：不可见 | 主：可读（只读）；子/孙：不可见 | 不可见 |
| 新建 / 删除会话 | 可写 | 不可见 | 不可见 | 不可见 |

### 3.4 记忆

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 记忆条目 | 可读可写 | 主：可读（聚合只读）；子：不可见；孙：不可见 | 主：可读（聚合只读）；子/孙：不可见 | 不可见 |
| 记忆聚合视图 / 统计 | 可读 | 主：可读；子：不可读（无聚合权）；孙：不可读 | 主：可读；子/孙：不可读 | 不可见 |
| 删除记忆 | 可写 | 主：可写（随账号删除**归档**，30 天可恢复 + 审计）；子：不可写；孙：不可写 | 主：可写（随账号删除归档）；子/孙：不可写 | 不可见 |
| 导出记忆 | 可写（自身） | 主：可写（整棵子树，带来源标注）；子：不可写；孙：不可写 | 主：可写；子/孙：不可写 | 不可见 |

### 3.5 文件 / 工作区

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 工作区目录列表 | 可读 | 主：可读；子：不可见；孙：不可见 | 主：可读；子/孙：不可见 | 不可见 |
| 文件内容读 | 可读 | 主：可读；子：不可见；孙：不可见 | 主：可读；子/孙：不可见 | 不可见 |
| 写入 / 删除文件 | 可写（仅自己分区） | 主：**不可写**（只读）；子：不可写；孙：不可写 | 主：**不可写**（只读）；子/孙：不可写 | 不可见 |

> 现状 `src/gateway/files.py:45-57` 是**全局单一 workspace root**，无账户分区；`resolve_in_workspace` 只防目录穿越，不防横向。必须按 `account_path` 分区，否则矩阵无法成立。

### 3.6 审计

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 审计事件列表 | 可读 | 主：可读；子：可读；孙：不可见 | 主：可读；子：不可读；孙：不可读 | 不可见 |
| 单条事件详情 | 可读 | 主：可读；子：可读；孙：不可见 | 主：可读；子/孙：不可读 | 不可见 |
| 链校验 | 可读（仅自身范围的结果） | 主：可读；子：可读（限直接下级）；孙：不可读 | 主：可读；子/孙：不可读 | 不可见 |
| 修改/删除审计 | 不可写（全角色均不可） | 不可写 | 不可写 | 不可写 |

> 现状 `audit.py:91-100` 是"L0 可读全部，其余只能读自己"；目标改为"`subtree_contains` 通过才可读"，主账号读全树、子账号读自己 + 直接下级。

### 3.7 用量 / 费用

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 用量明细 | 可读 | 主：可读；子：可读；孙：不可见 | 主：可读；子/孙：不可读 | 不可见 |
| 费用汇总 | 可读 | 主：可读；子：可读（仅直接下级）；孙：不可见 | 主：可读；子/孙：不可读 | 不可见 |
| 预算设置 | 可写（自身） | 主：可写（子树）；子：不可写；孙：不可写 | 主：可写；子/孙：不可写 | 不可见 |

> **Key 用量双记（Q3 已裁决）**：账单按 Key 归属（`key_owner_account_id`）、用量按调用者（`actor_account_id`）分别入库。主账号可见全部；非根账号只能看**自己 Key** 的额度消耗，**看不到调用者与调用明细**。

### 3.8 技能

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 可用技能清单 | 可读 | 主：可读；子：可读（仅自己已授予的）；孙：不可见 | 主：可读；子/孙：不可读 | 不可见 |
| 授予 / 撤销技能 | 可写（不可授予超出自身） | 主：可写；子：可写（仅直接下级，且 ≤ 自身）；孙：不可写 | 主：可写；子/孙：不可写 | 不可见 |
| 技能产物 / 模板 | 可读（自己的） | 主：可读；子：不可见（除非下级显式共享）；孙：不可见 | 主：可读；子/孙：不可见 | 不可见 |

### 3.9 审批凭据 / AI 员工 / 目标

| 资源 | 自己 | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 审批凭据（列表/签发/撤销） | 可读可写（自身） | 主：可读（列表）；签发/撤销**仅自身动作**；子：不可读不可写；孙：不可读不可写 | 主：可读（列表）；子/孙：不可见 | 不可见 |
| AI 员工 | 可读可写 | 主：可读（只读）；子：不可见；孙：不可见 | 主：可读（只读）；子/孙：不可见 | 不可见 |
| 目标 / 任务 / 产出物 | 可读可写 | 主：可读（只读）；子：不可见；孙：不可见 | 主：可读（只读）；子/孙：不可见 | 不可见 |

> 现状按"全局单租户"实现：`src/gateway/ai_management.py:1517+`（目标）、`:1659-1757`（员工）均无 owner 过滤；`src/ai/employee_store.py:41` 用单文件按 name 索引。

### 3.10 数据按账号分区：存储键与迁移

**最高优先级原则**：任何列表、检索、聚合、导出，**第一步必须是账号过滤**；没有 owner 条件就不允许发查询（fail-closed 返回空，而不是返回全部）。"串数据"是 D0 级缺陷（见 §10.2）。

| 数据域 | 必须的存储键 | 现状（可核对） | 迁移方式 |
|---|---|---|---|
| 会话 | `chat._session_key = principal + sep + session_id`；`conversation_turns.principal_id` | 已有复合键（`src/gateway/chat.py:73-88`）与 principal 列（`alembic/006:146-156`） | 旧数据按 `principal_id` 保持，无需改键 |
| 记忆 | `memory_entries.owner_account_id`；`key_hash = H(account_id \|\| key)` | **无 owner 列**；`key_hash` 仅哈希 key（`src/kernels/memory/store.py:65`） | 加列；旧条目挂根账号并标 `legacy=true`；按 `account_id+key` **重算哈希**（重算前先备份） |
| 文件 / 工作区 | 物理分区 `<workspace>/accounts/<account_id>/…`；所有 API 先解析账号分区再 `resolve_in_workspace` | 全局单根（`src/gateway/files.py:45-57`） | 旧文件整体迁入 `<workspace>/accounts/<root>/legacy/`，仅根可读 |
| 任务 / 目标 | 目标与任务持久化记录每条加 `owner_account_id`；查询先过滤（`src/gateway/ai_management.py:1517+`） | 无 owner | 旧条目挂根并标 `legacy=true` |
| 向量库 | Qdrant 单集合 `liuhao_memories` + payload 必带 `account_id`，检索**强制** `filter(account_id)`；或每账号独立集合 `liuhao_memories_<account_id>` | 单集合全局（`src/knowledge/memory.py:198-201`），现有 `user_id` 参数未强制 | 按账号回填 payload；无主旧向量挂根 `legacy` |
| 用量 / 费用 | `payer_account_id`、`attribution_account_id`、`key_owner_account_id` | `record_cost` 无 principal（`src/cost/cost_tracker.py:245`） | 旧记录统一挂根 `payer`，`attribution=legacy` |
| 审计 | `principal_id`（= actor_account_id）+ `details.actor_path/target_path` | 已有 `principal_id`（`src/gateway/audit.py:105`） | 旧事件按 `principal_id` 兜底映射，不改写哈希链 |
| AI 员工 | `owner_account_id` + 复合键 `(owner_account_id, name)` | 单文件按 `name` 索引（`src/ai/employee_store.py:41`） | 旧员工挂根；重名者改键为 `<name>@legacy`，不覆盖 |
| 技能授权 | `owner_account_id`、`grant.target_path`、`visible_to_descendants` | 能力注册无 owner（`src/kernels/capability/__init__.py:116`） | 旧能力挂根，`grant_scope=global` 仅根可用 |

迁移完成的判据（必须写进验收）：**双账号种子数据下，A 的列表/检索/导出结果中 0 条 B 的记录**；反之亦然。

**v1 范围**：全资源账号隔离 + **根账号单向只读子树**（内容面只读；写只发生在控制面：账号、权限、Key、技能授权、预算/额度）。审批凭据的签发/撤销**不属于**可代管范围，只允许针对自身动作（§3.9）。不实现跨账号共同编辑，不实现成员间共享工作区（共享只做"只读查看"）。任何"多人协同编辑同一份文件/会话"的需求在 v1 明确不做。

### 3.11 主账号专属资源

| 资源 | 自己（根） | 直接下级 | 子树其它 | 子树外 |
|---|---|---|---|---|
| 完整备份导出 / 跨机认领 | 可写 | 不可见 | 不可见 | 不可见 |
| 备份口令 / 恢复码 / 恢复文件 | 可读可写（一次性展示/重新生成） | 不可见 | 不可见 | 不可见 |
| 系统升级 / 受控自改代码 | 可写 | 不可见 | 不可见 | 不可见 |
| 全部 Key 统一管理 | 可读可写 | 不可见 | 不可见 | 不可见 |
| 角色预设模板 | 可读可写 | 不可见 | 不可见 | 不可见 |
| 核心能力入口（成员/权限矩阵/全量子树视图） | 可读可写 | 不可见 | 不可见 | 不可见 |

> 非根账号的"自己"列中，这些资源一律**不可见**；它们在自己的工作空间里只看到日常能力（对话/管家/知识库/文件/自动化/自有 Key），且界面不解释"为什么没有"。

### 3.12 自助注册账号的默认最小权限

> 适用：`origin = self_signup`、模板"协作者"（§7.5）。**默认全隔离**；主账号的聚合只读是口径 4 的显式例外（§6.2），不随本条下放给其他成员。

| 资源域 | 自助注册默认 | 实现要点 / 错误码 |
|---|---|---|
| 账号列表 / 成员树 | 只自己 | 列表过滤；直连他人返回 404 `ACCOUNT_SUBTREE_DENIED` |
| 账号详情 / 权限 | 只自己（不可改自己权限） | 同上 |
| 模型 Key | 只自有 Key；他人/根 Key 不可见、不可用 | `ACCOUNT_KEY_NOT_OWNED` / `ACCOUNT_KEY_UPWARD_DENIED` |
| 模型目录 | 只显示自有 Key 可见的模型；他人私有模型不可见 | 404 `ACCOUNT_MODEL_FOREIGN_DENIED` |
| 会话 | 只自己 | 404 `ACCOUNT_SUBTREE_DENIED` |
| 记忆 | 只自己；不可聚合读 | 403 `ACCOUNT_MEMORY_FOREIGN_DENIED` |
| 文件 / 工作区 | 只自己分区 | 404 `ACCOUNT_SUBTREE_DENIED` |
| 审计 | 只自己 | 他人事件不返回（空列表，不区分"不存在"） |
| 用量 / 费用 | 只自己 Key 的额度消耗；不显示调用者 | §5.6 |
| 技能 | 只自己已安装；上级技能内容不可见 | 404 `SKILL_VISIBILITY_DENIED` |
| **创建下级** | **不可**（默认模板无成员管理） | 403 `CORE_CAPABILITY_DENIED` |
| 成员管理入口 | **不显示** | §9.6 导航裁剪 |

> 与"主账号拥有子树记忆/技能"（口径 4）的关系：**主账号**可读（§6.2/§7.3），**自助注册成员**不可读任何他人数据；两者不矛盾。

---

## 4. 越级判定

### 4.1 四类越级

| 编号 | 类型 | 定义 | 典型请求 |
|---|---|---|---|
| V1 | **跨层授权** | 对**非直接下级**（更深层）执行权限变更；或下级给上级授权 | 配置放宽到 4 层后，depth=1 成员改 depth=3 账号权限；成员给根账号授权 |
| V2 | **跨子树查询** | `subtree_contains(actor.path, target.path) == false` 时的读操作（含列表、详情、审计、用量、文件） | 子账号 A 查子账号 B 的会话 |
| V3 | **借用上层 Key** | 使用 `key_owner_account_id` 不属于自己子树的 Key；或提权使用上级 Key | 子账号请求主账号 Key 发起模型调用 |
| V4 | **读别人记忆** | 读取非自己、非自己子树（且非主账号聚合权）的记忆条目 | 孙账号读兄弟子树记忆；子账号读父记忆 |

### 4.2 判定顺序（固定，不可调换）

```
1) 认证：令牌有效且 kind=human，主体 ACTIVE        -> 否则 401
2) 账号态：actor.status == active                  -> 否则 403 ACCOUNT_INACTIVE
3) 身份一致：请求体不得指定 actor/owner/scope       -> 否则 400 ACTOR_FROM_BODY_FORBIDDEN
4) 子树包含：subtree_contains(actor.path, target)   -> 否则 404 或 403（见 4.3）
5) 直接下级约束：写越级仅允许 `target == 直接下级`（根账号可对整棵子树）    -> 否则 403 ACCOUNT_LEAPFROG_DENIED
6) 能力子集：granted ⊆ actor.effective_permissions  -> 否则 403 ACCOUNT_PRIVILEGE_ESCALATION
7) Key 归属：key_owner ∈ actor.subtree（或其自身）   -> 否则 403 ACCOUNT_KEY_UPWARD_DENIED
8) 只读查看：effective_account_id != actor 时禁写   -> 否则 403 ACCOUNT_VIEW_AS_WRITE_FORBIDDEN
```

第 4 步必须在第 5 步之前：先确认"在不在我树里"，再确认"是不是我能直接动的层级"。否则兄弟子树会被误判为"跨层"而不是"跨子树"，错误码与文案会误导用户。

### 4.3 错误码与用户可读文案

对外状态码约定：**不可见 → 404（不泄露存在性）**；**可见但无权 → 403**；**状态冲突 → 409**。

| 错误码 | HTTP | 用户可读提示（中文原文，全部中性） | 说明 |
|---|---|---|---|
| `ACCOUNT_SUBTREE_DENIED` | 404 | 资源不存在或无权访问。 | V2 普适；404 防枚举 |
| `ACCOUNT_LEAPFROG_DENIED` | 403 | 无权执行该操作。 | V1 跨层；真实原因只进审计 |
| `ACCOUNT_PRIVILEGE_ESCALATION` | 403 | 无权授予该权限。 | 向下放大 |
| `ACCOUNT_UPWARD_DENIED` | 404 | 资源不存在或无权访问。 | 向上操作；404 防枚举 |
| `ACCOUNT_CROSS_SUBTREE_DENIED` | 403 | 无权访问该资源。 | 兄弟子树 |
| `ACCOUNT_KEY_UPWARD_DENIED` | 403 | 该 Key 不可用于当前工作区。 | V3 |
| `ACCOUNT_KEY_NOT_OWNED` | 403 | 该 Key 不可用于当前工作区。 | Key 归属 |
| `ACCOUNT_KEY_CONSENT_REQUIRED` | 403 | 该 Key 未开启共享授权，无法统一调度。 | 仅主账号界面可见 |
| `ACCOUNT_KEY_PLAINTEXT_FORBIDDEN` | 403 | Key 仅支持在系统内调用，不提供明文。 | 防导出 |
| `ACCOUNT_MEMORY_FOREIGN_DENIED` | 403 | 无权访问该内容。 | V4 |
| `SKILL_VISIBILITY_DENIED` | 404 | 资源不存在或无权访问。 | 未授权技能；404 防枚举 |
| `ACCOUNT_MEMORY_WRITE_FOREIGN` | 403 | 该内容为只读。 | 聚合只读 |
| `ACCOUNT_DEPTH_EXCEEDED` | 409 | 当前工作区暂不能再添加成员。 | 可读理由，不暴露层数 |
| `ACCOUNT_DELETE_HAS_CHILDREN` | 409 | 该成员仍有归属对象，请先处理后再移除。 | 删除约束 |
| `ACCOUNT_DELETE_ROOT_FORBIDDEN` | 409 | 该账号为系统账号，不能移除。 | 根保护 |
| `ACCOUNT_ROOT_REGISTRATION_CLOSED` | 409 | 系统已完成初始化，请使用已有账号登录。 | 关闭"注册根"（已有根时再注册根）；**不等于**成员注册开关 |
| `ACCOUNT_SELF_REGISTER_DISABLED` | 403 | 当前无法创建账户。 | 成员自助注册开关关闭；不提管理员/开关/层级。**前端不得再按 `ACCOUNT_SELF_REGISTRATION_CLOSED` 匹配**（该名为早期草案） |
| `ACCOUNT_SELF_REGISTER_UNAVAILABLE` | 409 | 系统尚未初始化，请先设置主账号。 | 尚无根账号时调自助注册（不得绕过 S0 建号） |
| `ACCOUNT_MODEL_FOREIGN_DENIED` | 404 | 资源不存在或无权访问。 | 他人私有模型；404 防枚举 |
| `ACCOUNT_EXISTS` | 409 | 该登录账号不可用。 | 注册重名或保留名（代码原文案"该账号已存在"，按中性准则统一为本行文案）；不提示是谁占用 |
| `ACCOUNT_ACTIVATION_REQUIRED` | 403 | 该账号尚未开通，请使用激活码完成开通。 | 未激活（仅"主账号创建"路径） |
| `ACCOUNT_ACTIVATION_CODE_INVALID` | 400 | 激活码无效或已失效。 | 激活（仅"主账号创建"路径） |
| `ACCOUNT_ACTIVATION_LOCKED` | 429 | 尝试次数过多，请稍后再试。 | 失败锁定 |
| `ACCOUNT_PASSWORD_CHANGE_REQUIRED` | 403 | 请先设置新密码。 | 强制改密 |
| `ACCOUNT_INACTIVE` | 403 | 该账号暂不可用，请联系系统管理员。 | 状态 |
| `ACCOUNT_UNAVAILABLE` | 403 | 该账号当前不可用。 | 登录/请求期停用或删除（登录与鉴权同一文案） |
| `ACCOUNT_ALREADY_INITIALIZED` | 409 | 本机已存在账号树，备份包只能在全新实例上认领，不能用于覆盖现有账号 | 已有根账号后再调 `/v1/accounts/backup/import`（重放仍返回 `ACCOUNT_BACKUP_REPLAYED`） |
| `AUTH_UNAUTHORIZED` | 401 | 未提供访问令牌，请先登录。 | 无令牌；前端按 `code` 回登录页 |
| `AUTH_TOKEN_INVALID` | 401 | 访问令牌无效或已过期，请重新登录。 | 签名/格式/吊销失败；不暴露解析细节 |
| `ACCOUNT_VIEW_AS_WRITE_FORBIDDEN` | 403 | 当前为只读查看，无法执行该操作。 | 视角 |
| `ACTOR_FROM_BODY_FORBIDDEN` | 400 | 请求参数不合法。 | 防冒用 |
| `ACCOUNT_RECOVERY_CODE_INVALID` | 400 | 恢复码无效或已失效。 | 恢复 |
| `ACCOUNT_RECOVERY_LOCKED` | 429 | 尝试次数过多，请稍后再试。 | 恢复码失败锁定 |
| `ACCOUNT_RECOVERY_UNAVAILABLE` | 403 | 恢复凭据不可用，请按引导重建身份体系。 | 两者皆失；本机自救入口在已有恢复码时也用它（不得作废用户手上的码） |
| `ACCOUNT_RECOVERY_WRITE_FAILED` | 500 | 恢复码未能写入本机数据目录，请重试。 | 本机自救入口写盘/权限收紧失败；服务端同时撤销刚生成的码 |
| `ACCOUNT_RECOVERY_LOCAL_ONLY` | 403 | 恢复码只在本机交接/重置。 | 非回环来源；远程重置需显式 `LIUHAO_ALLOW_REMOTE_RECOVERY=1` |
| `ACCOUNT_MIGRATION_PACKAGE_INVALID` | 400 | 迁移包无效、损坏或不属于本系统。 | 认领 |
| `ACCOUNT_MIGRATION_PASSPHRASE_REQUIRED` | 400 | 请输入备份口令。 | 认领 |
| `ACCOUNT_MIGRATION_CONFLICT` | 409 | 本机已有身份数据，无法直接认领。 | 冲突 |
| `ACCOUNT_MIGRATION_REPLAY_DENIED` | 409 | 该备份已被使用过，不能重复导入。 | 防重放 |
| `ACCOUNT_MULTI_ROOT_DETECTED` | 409 | 身份数据异常，请联系系统管理员。 | 单根约束 |
| `ACCOUNT_AMBIGUOUS` | 409 | 账号配置异常，请联系系统管理员。 | 登录歧义 |
| `CORE_CAPABILITY_DENIED` | 403 | 无权执行该操作。 | 核心能力门禁；真实原因进审计 |

### 4.4 文案中性准则（硬性）

| 准则 | 内容 |
|---|---|
| 只描述"能不能做" | 对外文案只说"无权/不可用/请稍后再试"，**不描述"你是谁、谁管谁、在第几层、谁在上"** |
| 禁用词（非根用户可见的响应与界面） | 子账号、孙账号、上级、下级、层级、越级、主账号、根账号、root、parent、depth、子树、继承、被管理 |
| 允许词 | 当前工作区、账号、成员、资源、权限、Key、共享、只读、不可用 |
| **登录 / 注册页只允许** | **"登录"、"创建账户"**（以及"忘记密码？""从备份迁移到本机"两个中性链接） |
| **登录 / 注册页额外禁用词** | **邀请码、加入、成员、子账号、上级、层级、加入我们、组织**（"成员"仅在认证页禁用；管理页可正常使用） |
| 结构与真实原因 | 只写入审计 `reason_code` / `details`（如 `violation=V1`、`actor_path`、`target_path`），**不得出现在响应体或界面提示** |
| 响应结构 | 统一 `{code, message, request_id}`；`code` 可为机器码，`message` 必须中性 |
| 防枚举 | 无权访问他人资源一律 **404**（不返回 403 的"存在但拒绝"语义） |
| 日志边界 | 审计可含真实路径；但任何回显给无权者的字段都不得包含对方 `account_id/path/display_name` |


---

## 5. Key 归属规则

### 5.1 两类 Key 都要绑 owner

| Key 类型 | 现状 | 要求 |
|---|---|---|
| **模型 Key（上游供应商）** | `ai_model_configs.api_key_enc`（`alembic/006:170-185`），全站共享、无 owner | 增 `owner_account_id`、`created_by_account_id`、`status` |
| **平台 API Key（调用 /v1）** | `src/security/api_keys.py:67-96` 有 `id/name/scopes/status`，**无 owner** | 增 `owner_account_id`，且**一个 Key 只能映射一个账号**，不可代表子树 |

### 5.2 使用规则

| 场景 | 允许？ | 说明 |
|---|---|---|
| 账号用自己的 Key | 允许 | 常规 |
| 主账号用子/孙的 Key | 允许（需共享授权） | 仅主账号；须存在有效的 `allow_superior_key_use`；服务端路由，明文不下发 |
| 子账号用主账号的 Key | **拒绝** | `ACCOUNT_KEY_UPWARD_DENIED` |
| 子账号用孙账号的 Key | **拒绝** | 口径 3：子账号只能用自己个人的 Key |
| 孙账号用任何上级或兄弟 Key | **拒绝** | 同上 |
| 兄弟账号互相用 Key | **拒绝** | `ACCOUNT_KEY_NOT_OWNED` |
| 停用账号的 Key | **拒绝** | 账号 `suspended` 时其 Key 一并失效 |

**共享授权前置（定稿 15 + 20）**：主账号使用子账号 Key 的前提是存在有效授权——创建子账号时勾选"**允许团队共享我的 Key**"，子账号可在自己的 Key 设置中随时撤销。**开关只作用于本人 Key：本人可随时开启/撤销，无需管理者身份；管理者也可代为调整。** 授权撤销后，主账号对该 Key 的下一次调用即被拒（`ACCOUNT_KEY_CONSENT_REQUIRED`）。授权本身不展示"谁在用"，只表达"是否允许统一调度"。

**共享变更会吊销对方已签发的令牌（定稿）**：管理者代成员调整共享会 `+1 security_epoch`，以保证"权限变更即时生效"（§2.4 定稿 18）。因此该成员当前的登录凭据在下一次请求即失效，**需要重新登录**；接口在该情形返回 `requires_reauth=true` 与中性提示"共享设置已更新；该成员当前的登录凭据已失效，需要重新登录。"。本人改自己的开关只影响自己的共享状态，**不会吊销自己的令牌**（不做 epoch 变更）。

**即时生效**：Key 归属变更、吊销、共享撤销均**不等待令牌过期**；鉴权路径每次请求重新解析 Key 归属与授权状态（fail-closed）。

### 5.3 主账号替子账号调用：审计与费用记账

一次模型调用的四个归属字段：

| 字段 | 取值 | 用途 |
|---|---|---|
| `actor_account_id` | 主账号 | 谁发起了这次调用；**审计主体 + 用量归属** |
| `effective_account_id` | 子账号（若指定了目标）或主账号 | 这次调用"代表谁" |
| `key_owner_account_id` | 子账号（所用 Key 的 owner） | Key 归属；**账单归属**（供应商额度扣在该 Key 上） |
| `payer_account_id` | = `key_owner_account_id`（用自有 Key 时即自己） | 费用计入方，与账单同源 |

- **双记（定稿）**：账单按 `key_owner_account_id` 记；用量按 `actor_account_id` 记。两条独立入库，**不得只留一条**（Q3 已裁决）。
- **审计记在主账号名下**，`details` 额外记 `key_owner_account_id` 与 `effective_account_id`（口径 9）。
- **可见性**：主账号可见全部账单与用量；子账号只能看到**自己 Key** 的额度消耗，**不显示调用者**（§5.6）。
- 子账号用自己的 Key 调用时：`actor = payer = key_owner = 自己`，不产生代管标记。

> 现状 `src/cost/cost_tracker.py:245` `record_cost` 无任何 principal 参数，只有 `resource_id/metadata` 可塞；必须显式加 `payer_account_id` 等字段，**不能靠 metadata 约定**，否则报表口径会漂移。

### 5.4 Key 创建 / 轮换 / 吊销

- 创建：`owner_account_id` = 创建者（不允许代建后改归属）。
- 轮换：新 Key 继承 owner，旧 Key 立即 `revoked`，审计 `ACCOUNT_KEY_ROTATED`。
- 吊销：owner 本人可吊销；其所有祖先（含主账号）可吊销；**下级不能吊销上级 Key**。

### 5.5 子账号删除时其 Key 如何处理

固定顺序（软删，不物理删）：

1. 该账号 `status = deleting`，立即**吊销其全部 Key**（模型 Key + 平台 Key），并从模型中移除可用性；同时给操作者一条**Key 解绑提示**：列出被吊销的 Key 名称与数量，明确"如需继续使用该供应商凭据，请在当前工作区重新创建"。
2. **仅叶子可删**：非叶子账号（存在任一子节点）一律拒绝 `ACCOUNT_DELETE_HAS_CHILDREN`；必须先"移交"（改 `parent_account_id` 到删除者的上级）或"级联停用"（子树 `status=suspended`，可恢复，需二次确认；**不是**级联删除）。
3. Key **不可转移给上级**。如上级确实需要该上游供应商凭据，只能用自己的账号重新创建一份；旧 Key 永不复活。
4. **删除连锁**：审计**永不删除**（含 `key_owner_account_id`）；记忆**归档**（`archived_at`，默认保留 30 天，仅根可读、不可再写入）；会话与文件转入只读归档；用量与费用记录保留。归档期内可恢复，超期后物理清理需再次确认。
5. 账号标记 `deleted` 后，其个人数据仅根可读；原账号无法再登录（令牌立即失效）。
6. 主账号删除自身：禁止（`ACCOUNT_DELETE_ROOT_FORBIDDEN`）；"主账号转移"不在 v1 范围，换机走 §9.10 备份认领。

### 5.6 Key 使用的知情边界（不得过度承诺）

| 维度 | 子账号可知？ | 说明 |
|---|---|---|
| 共享授权开关 | 可知 | 自己的 Key 设置里有"允许团队共享我的 Key"，状态可见、可撤销；文案中性 |
| 调用细节（谁调、何时调、调什么） | **不可知** | 界面不展示；`ACCOUNT_KEY_USED` 的 `principal_id` 是主账号，按其子树过滤后子账号读不到该事件 |
| 自己的 Key 用量统计 | 可知（仅自己的额度消耗） | 这是自己的资源；**不显示调用者/调用明细**（Q3 已裁决） |
| 供应商后台额度消耗 | **可知且不可隐藏** | 上游账单/控制台的额度下降可被观察；系统**不能承诺"完全不可知"** |
| 外部监控/网络侧观察 | 不可控 | 企业网关、代理日志等不在本系统控制范围 |

结论：产品口径只承诺"**界面不暴露调用细节**"，**不承诺"技术上完全不可知"**。任何文案不得写"子账号绝对不会知道"。

### 5.7 停用连锁（与删除区分）

| 动作 | 连锁结果 |
|---|---|
| 停用 `suspended` | ① 立即吊销该账号全部会话（`ACCOUNT_SESSIONS_REVOKED`）；② Key 置为 `suspended`，**不删除、不改归属**；③ 审计不停、不删；④ 记忆与文件保持（该账号不可登录，根可只读）；⑤ 恢复后 Key 与会话需重新建立 |
| 恢复 `active` | Key 恢复可用（若未被吊销）；会话需重新登录；写 `ACCOUNT_STATE_CHANGED(from=suspended,to=active)` |
| 删除 `deleted` | 按 §5.5：吊销 Key + 解绑提示 + 记忆归档 + 审计保留 |

---

## 6. 记忆命名空间与聚合读取

### 6.1 分区

记忆条目唯一键 = **(owner_account_id, key)**。分区维度：

| 维度 | 作用 |
|---|---|
| `owner_account_id` | 归属账号（不可变；决定谁能写） |
| `scope` L0–L7 | clearance 档（与 §2.4 一致，用于层级过滤） |
| `tier` | 短/中/长/持久（现有 `MemoryTier`） |
| `tags` | 业务标签，**不可替代 owner 过滤** |

必须修改的现状：

- `memory_entries` 表（`alembic/006:127-143`）**没有 owner 列**。
- 主键是 `key_hash`，且哈希只由 key 派生（`src/kernels/memory/store.py:65`），两个账号用同名 key 会互相覆盖 → **必须改为 `H(account_id || key)`**。
- `recall`（`__init__.py:286`）/`scope_filter`（`:371`）只按 scope 过滤，不认账号 → 新检索路径必须"先账号分区、再 scope 过滤"。

**自助注册账号的分区落点**：建号即写入自己的 `owner_account_id`，默认作用域 `SELF_SERVICE_SCOPES`（`account:read` / `account:audit_read` / `account:usage_read` / `model:use` / `model:manage`，见 `src/kernels/identity/_accounts.py:72-78`）——**不含** `account:manage_children`、`account:grant`、`account:key_borrow`。因此它既不能聚合读他人记忆，也不能把自己的记忆命名空间共享给他人；主账号对它的只读聚合属于 §6.2 的显式例外。

### 6.2 聚合读取（主账号）

| 行为 | 规则 |
|---|---|
| 读取范围 | `subtree_contains(主.path, 条目.owner_account_id 对应 path)`；**检索只在该范围内扫描**，跨子树命中一律丢弃 |
| 返回形态 | **只读汇总视图**：每条带 `source_account_id`、`source_path`、`scope`；默认按账号分组 |
| 可编辑性 | **默认不可编辑**（`ACCOUNT_MEMORY_WRITE_FOREIGN`）。仅"**交接 / 子账号失联**"时可开**显式限时代管**（可撤销、必须写审计），其余场景一律拒绝（Q2 已裁决） |
| 删除 | 主账号**不得单条删除他人记忆**；仅可随账号删除**归档**（二次确认 + `MEMORY_ARCHIVED`，30 天可恢复）；单条删除仅限 owner 本人 |
| 导出 | 主账号可导出整棵子树（含原始条目 + 来源账号标注）；子账号不可导出下级记忆；任何导出写 `MEMORY_EXPORTED` 审计并记条目数、字节数、范围 |
| 子账号 | 无聚合权；只能读自己；`ACCOUNT_MEMORY_FOREIGN_DENIED` |
| **自助注册账号** | 只读写自己的命名空间；**无聚合权、不可导出他人、不可被其他成员聚合**；根账号可按本表"读取范围"只读聚合（§3.12） |
| 整机备份 | 与"记忆导出"不同：整机备份是**全量**（含所有成员会话/记忆/文件/任务/Key 密文），见 §9.12；仍受"口令加密 + 二次确认 + 审计"约束 |

### 6.3 写路径

- 写入必须显式携带 `owner_account_id`（默认 = actor），**不得由请求体指定**（防伪造）。
- 跨账号写入仅两种合法情形：主账号的"代管授权"（限时）、系统级迁移任务（`actor = system`，需审计）。**自助注册账号不享有任何一种**：既不能代管他人，也不能被授予写入他人命名空间的权限。
- 删除账号时的记忆**归档而非销毁**：`archived_at` 置位（默认保留 30 天、仅根可读、不可再写）；超期物理清理需再次确认并写 `MEMORY_ARCHIVE_PURGED`。归档期内主账号可恢复。

---

## 7. 技能继承

### 7.1 "主账号拥有子账号的技能"到底是什么

定义为三件事的集合，**不是**第四种"冒名执行"：

| 维度 | 含义 | 允许 |
|---|---|---|
| **能力授权聚合** | 主账号可查看子树内被授予/在用的技能清单，并可对子树授予或撤销 | 是 |
| **产物/模板共享** | 子账号产生的技能产物、提示模板、参数配置可被主账号读取、复用、复制到自己名下（复制产生新归属，不改变原归属） | 是 |
| **审计可见** | 技能调用事件（谁在何时用了哪个技能）在主账号审计范围内 | 是 |
| **以他人身份执行技能** | 用他人身份静默跑技能并记在对方账上 | **否**（必须走只读查看机制，见 §9.7） |

### 7.2 技能状态与归属

| 状态 | 含义 |
|---|---|
| `installed` | 安装在某账号的目录/能力集中 |
| `granted` | 由上级显式授予某账号或某子树 |
| `artifact` | 运行产出的模板/产物/配置 |

技能记录需增：`owner_account_id`（安装者）、`granted_by_account_id`、`grant_scope`（`self` / `subtree` / `global`）、`visible_to_descendants`（默认 `false`）、`visible_to_parents`（默认 `false`，**对主账号不生效**，见 §7.3）。

现状：能力登记在 `capability-registry.yaml`（`capabilities:` 段，`:42+`），注册入口 `src/kernels/capability/__init__.py:116` `register`，**均无 owner/grant 字段**。

### 7.3 子账号能否看到上级的技能

**默认不能。** 判据（满足任一即可见，否则不可见）：

1. 该技能在该账号的**自身 catalog** 中存在（`installed` 且 `owner_account_id == 自己`）；或
2. 存在 `grant` 记录，且 `grant.target_path` 覆盖该账号路径；或
3. 上级显式设置 `visible_to_descendants = true` 且该账号在其子树内。

反向规则：**非根上级默认看不到下级的技能产物内容**，除非下级显式 `visible_to_parents = true`（默认 `false`）；**主账号例外**——按 §7.1"产物/模板共享"可读整棵子树的技能产物与模板（只读或复制，复制产生新归属、不改变原归属），并可读清单级元数据（技能名、授予状态、调用次数）。

判定式：

```
can_see_skill(A, S) :=
    S.owner == A
    or (S.grant and subtree_contains(S.grant.target_path, A.path))
    or (S.visible_to_descendants and S.owner 是 A 的祖先)
    or (A.is_root and subtree_contains(A.path, S.owner 所在 path)
        and S.kind in {artifact, template})        # 口径 4 的显式例外
```

红线：非根上级不得因"我是在上级"自动看到下级技能内容；主账号的子树可读是**口径 4 的显式例外**，不是祖先关系的自动扩散；任何账号都不得以他人身份执行技能（§7.1 最后一行）。

### 7.4 核心能力与日常能力（技能分层）

技能/能力分两类，`capability_class` 决定是否可用：

| 类别 | 清单 | 门禁 |
|---|---|---|
| **核心能力（core）** | 成员与权限管理、权限矩阵、整棵子树记忆/用量/审计全量、跨机认领与备份导出、系统升级与受控自改代码、全部 Key 统一管理 | **仅 `depth == 0`**；非根请求返回 403 `CORE_CAPABILITY_DENIED`（中性文案） |
| **日常能力（daily）** | 对话、管家、知识库、文件、自动化、自有 Key、自有目标/任务 | 所有账号可用，**限自有范围** |

- 核心能力**不进入**非根账号的技能清单与导航，不做"置灰"处理（置灰会暴露结构）。
- 日常能力对子账号是**完整体验**，不降级、不加水印、不显示"受限"。
- 部署时 `capability-registry.yaml` 每条补 `capability_class` 与 `owner_account_id`；未标注的默认 `daily`，不得默认 `core`。

### 7.5 角色预设模板（2–3 个，供创建成员时选择）

| 模板 | 适用 | 包含能力 | 默认深度 |
|---|---|---|---|
| **团队负责人** | 需要带人 | 全部日常能力 + 管理自己的直接下级 + 查看自己/直接下级的审计与用量 + 向下授予"自己已持有"的技能 | depth 1 |
| **协作者**（默认） | 独立干活 | 全部日常能力（自有对话/管家/知识库/文件/自动化/自有 Key） | depth 1 |
| **只读访客** | 查看资料 | 日常只读：知识库/文件可读；不可写文件、不可建自动化、不可建 Key | depth 1 |

- 模板只是**初始权限集合**，创建后可细调；根账号不受模板影响（始终 core 全量）。
- 任何模板都**不得**包含核心能力（`capability_class=core`）；服务端对模板做子集校验，防止模板被改造成提权后门。
- 模板自身存于根的配置区（`role_templates`），仅根可读写，改动写审计 `ROLE_TEMPLATE_CHANGED`。

---

## 8. 审计事件表

现有审计表字段（`src/gateway/audit.py:105`）：`seq, event_id, event_type, principal_id, scope, timestamp, correlation_id, outcome, details, event_hash, prev_event_hash, hash_alg, link_hash`。

映射约定：`principal_id` 写 **actor_account_id**；`scope` 写 actor 的 L 档；其余账户维度全部进 `details`（保持现有表不破坏哈希链）。

### 8.1 必落字段（所有账户相关事件）

| 字段 | 必填 | 说明 |
|---|---|---|
| `actor_account_id` | 是 | 真实操作者（≠ 请求体） |
| `actor_path` / `actor_depth` | 是 | 判定依据快照 |
| `target_account_id` / `target_path` | 视事件 | 被操作账号 |
| `effective_account_id` | 视事件 | 视角/代管/代调用目标 |
| `key_owner_account_id` | Key 事件必填 | 所用 Key 归属 |
| `action` | 是 | 动作名（与网关路由一致的稳定枚举） |
| `outcome` | 是 | `allowed` / `denied` |
| `reason_code` | 拒绝必填 | §4.3 错误码 |
| `request_id` / `correlation_id` | 是 | 串联同一次请求 |
| `client` / `ip` | 建议 | 登录与敏感操作 |
| `before` / `after` | 视事件 | 权限/归属变更差异（**不得含 Key 明文**） |

### 8.2 事件清单

| 事件名 | actor | target | 结果 | 必须额外落字段 |
|---|---|---|---|---|
| `ROOT_BOOTSTRAP_COMPLETED` | 首个注册者 | 根账号 | allowed | `client`, `ip`, `source=first_run` |
| `ROOT_SELF_REGISTRATION_REJECTED` | 匿名/任意 | 拟注册名 | denied | `reason_code=ACCOUNT_ROOT_REGISTRATION_CLOSED`, `existing_root` |
| `ACCOUNT_SELF_REGISTERED` | 自助注册者 | 新账号 | allowed | `origin=self_signup`（代码 `source=self_service`）, `parent_account_id`(=根), `target_path`, `depth=1`, `template=collaborator`, `client`, `ip`；现状事件名 `account.self_registered` |
| `ACCOUNT_SELF_REGISTRATION_REJECTED` | 匿名 | 拟注册名 | denied | `reason_code`（`closed` / `duplicate` / `no_root`）, `client`, `ip`。**不带 `parent_forbidden`/`role_forbidden`**：这两类字段按 §1.2 被忽略而非拒绝 |
| `ACCOUNT_SELF_REGISTRATION_TOGGLED` | 根 | 设置项 | allowed | `enabled`, `before`, `after`, `effective_immediately=true`；现状事件名 `account.self_registration.toggle` |
| `ACCOUNT_CREATED` | 根/上级 | 新账号 | allowed | `origin=created_by_admin`, `target_path`, `depth`, `activation_code_id`（不含码明文） |
| `ACCOUNT_ACTIVATED` | 子账号本人 | 自己 | allowed | `activation_code_id`, `consumed_at` |
| `ACCOUNT_PASSWORD_CHANGED` | 子账号本人/上级 | 目标账号 | allowed | `forced=true/false`, `by_admin` |
| `ACCOUNT_LOGIN_SUCCESS` | 任意账号 | 自己 | allowed | `client`, `jti` |
| `ACCOUNT_LOGIN_FAILED` | 任意 | 自己 | denied | `reason`（不泄露账号是否存在） |
| `ACCOUNT_SUSPENDED` | 祖先 | 目标账号 | allowed | `target_path`, `cascade` |
| `ACCOUNT_DELETED` | 祖先 | 目标账号 | allowed | `target_path`, `mode=leaf_only`, `keys_revoked` |
| `ACCOUNT_TREE_VIOLATION` | 任意 | 目标 | denied | `violation=V1/V2/V3/V4`, `reason_code` |
| `ACCOUNT_PRIVILEGE_GRANTED` | 祖先 | 目标账号 | allowed | `before`, `after`, `subset_check=true` |
| `ACCOUNT_PRIVILEGE_REVOKED` | 祖先 | 目标账号 | allowed | `before`, `after` |
| `ACCOUNT_KEY_USED` | actor | Key 记录 | allowed | `key_owner_account_id`, `effective_account_id`, `payer_account_id`, `usage_account_id`(=actor), `billing_account_id`(=key_owner), `model_id` |
| `ACCOUNT_KEY_UPWARD_DENIED` | 子/孙 | 上级 Key | denied | `requested_key_owner`, `reason_code` |
| `ACCOUNT_KEY_CREATED` / `_ROTATED` / `_REVOKED` | owner/祖先 | Key | allowed | `key_id`, `owner_account_id`（**无明文**） |
| `ACCOUNT_VIEW_AS_STARTED` | 主/上级 | 子账号 | allowed | `effective_account_id`, `read_only=true` |
| `ACCOUNT_VIEW_AS_WRITE_BLOCKED` | 主/上级 | 目标资源 | denied | `action`, `reason_code=ACCOUNT_VIEW_AS_WRITE_FORBIDDEN` |
| `ACCOUNT_VIEW_AS_ENDED` | 主/上级 | 子账号 | allowed | `duration_ms` |
| `MEMORY_AGGREGATE_READ` | 主账号 | 子树 | allowed | `accounts_covered`, `entries`, `scope_filter` |
| `MEMORY_FOREIGN_READ_DENIED` | 子/孙 | 他人记忆 | denied | `owner_account_id`, `reason_code` |
| `MEMORY_ARCHIVED` | 主账号/上级 | 子树/目标账号记忆 | allowed | `entries`, `confirm_token`, `archived_at`, `recoverable_until`, `retention_days` |
| `MEMORY_EXPORTED` | 任意有权者 | 范围 | allowed | `accounts_covered`, `entries`, `bytes`, `format` |
| `SKILL_GRANTED` / `SKILL_REVOKED` | 祖先 | 技能 | allowed | `skill_id`, `grant_scope`, `target_path` |
| `SKILL_VISIBILITY_DENIED` | 下级 | 上级技能 | denied | `skill_id`, `reason_code` |
| `APPROVAL_GRANT_ISSUED` | 人类 | 动作/目标 | allowed | 沿用现有 `HUMAN_SOVEREIGNTY_OVERRIDE` 字段 + `actor_path` |
| `ACCOUNT_PASSWORD_RESET_BY_ADMIN` | 根/上级 | 目标账号 | allowed | `by_admin=true`, `forced_change=true`, `client` |
| `ACCOUNT_ACTIVATION_CODE_ISSUED` | 根/上级 | 目标账号 | allowed | `code_id`, `expires_at`（**不含码明文**） |
| `ACCOUNT_ACTIVATION_LOCKED` | 匿名 | 目标账号 | denied | `attempts`, `lock_until` |
| `ACCOUNT_PERMISSION_CHANGED` | 根/上级 | 目标账号 | allowed | `before`, `after`, `subset_check` |
| `ACCOUNT_STATE_CHANGED` | 根/上级 | 目标账号 | allowed | `from`, `to`, `effective_immediately=true` |
| `ACCOUNT_SESSIONS_REVOKED` | 根/上级 | 目标账号 | allowed | `reason`, `revoked_count`（降权/停用即时生效） |
| `KEY_CONSENT_GRANTED` | 子账号本人/创建者 | 自己的 Key | allowed | `consent=true`, `scope=team_shared` |
| `KEY_CONSENT_REVOKED` | 子账号本人 | 自己的 Key | allowed | `consent=false`, `effective_immediately=true` |
| `CORE_CAPABILITY_DENIED` | 任意非根 | 能力 | denied | `capability_id`, `required_class=core` |
| `DATA_ISOLATION_VIOLATION` | 任意 | 他人数据 | denied | `resource_domain`, `owner_account_id`, `reason_code`（D0 专用） |
| `MEMORY_ARCHIVE_PURGED` | 根 | 归档 | allowed | `entries`, `bytes`, `confirm_token` |
| `BACKUP_EXPORTED` | 根 | 整棵树 | allowed | `member_ids`, `member_count`, `memory_entries`, `files`, `total_bytes`, `passphrase_set=true`（**不含口令**） |
| `BACKUP_IMPORTED` | 本机操作者 | 整个备份 | allowed | `source_machine`, `member_count`, `records`, `package_digest`, `passphrase_verified=true` |
| `BACKUP_IMPORT_REJECTED` | 本机操作者 | 包 | denied | `reason`（`invalid/conflict/replay/passphrase`）, `attempts` |
| `RECOVERY_CODE_GENERATED` | 根 | 根账号 | allowed | `recovery_file=true`, `shown_once=true`（**不含码明文**） |
| `RECOVERY_CODE_USED` | 根账号本人 | 根账号 | allowed | `credential_reset=true`, `sessions_revoked=true` |
| `RECOVERY_FAILED_LOCKED` | 匿名 | 根账号 | denied | `attempts`, `lock_until` |
| `IDENTITY_REBUILT` | 运维/根 | 身份体系 | allowed | `data_retained=true`, `reason`, `confirm_token` |
| `ROLE_TEMPLATE_CHANGED` | 根 | 模板 | allowed | `template_id`, `before`, `after` |

拒绝事件必须与允许事件同强度落盘（现状审计链已有 `outcome` 字段，可直接承载）。

---

## 9. UI 信息架构

### 9.1 总原则

- **一套登录页、一套控制台**：桌面端 / 网页版 / 手机端复用同一组件（现状 `Login.tsx:1-13` 已如此）。
- **权限在服务端裁**：前端隐藏入口只是体验优化，**不是**安全边界。
- **三种"看到别人"的方式必须视觉可分**：① 使用共享 Key（模型路由，非身份切换）；② 只读查看（带横幅）；③ 真正的身份切换（重新登录）。

### 9.2 登录页状态机

| 状态 | 判定条件（建议由 `GET /v1/auth/config` 返回） | 界面 | 可用动作 |
|---|---|---|---|
| **S0 未初始化** | `registered_humans == 0` 或 `secret_store.configured == false` 或 `root.exists == false` | 不显示登录表单；显示"安装初始化"卡片与创建主账号入口 | 「创建主账号」 |
| **S1 已有账号 · 可自助注册** | `root.exists == true` 且 `root.status == active` 且 `self_registration.enabled == true` | 账号 + 密码表单；与"登录"并列显示"**创建账户**"；下方固定两个中性链接："忘记密码？"、"从备份迁移到本机" | 「登录」「创建账户」「重置密码」「迁移」 |
| **S1c 已有账号 · 注册关闭** | 同 S1，但 `self_registration.enabled == false` | 账号 + 密码表单；**不渲染"创建账户"**；旧链接进入统一中性提示（§9.5） | 「登录」「重置密码」「迁移」 |
| **S1b 身份不可用** | `root.exists == true` 且 `root.status != active`（`suspended` / `deleting`） | 不显示登录表单；只显示"暂不可用，请联系系统管理员。" | 无自助动作；不显示重建入口 |
| **S2 已登录** | 持有有效令牌 | 进入控制台；顶部身份条；导航按能力裁剪 | 正常业务操作 |
| **S2a 待激活** | 账号 `status == pending_activation` 且激活码有效 | 激活码页 → 强制改密页 | 「使用激活码」「设置新密码」 |
| **S2b 待改密** | `credential_state == activation_pending`（已激活未改密） | 登录成功后**只**显示改密页，无侧栏、无业务入口 | 「设置新密码」 |
| **S3 重置密码** | 用户点"忘记密码？" | 输入账号 + 恢复码（或上传恢复文件）；**不允许跳过验证** | 「重置密码」 |
| **S4 从备份迁移到本机** | 用户点"从备份迁移到本机" | 上传迁移包 + 输入备份口令 → 校验 → 认领 | 「导入并认领」 |
| **S5 重建身份体系** | 恢复码与恢复文件均不可用（S3 失败后引导） | 风险说明 + 二次确认 + 运维口令 → 重建 | 「重建」（默认不开放给普通用户） |

补充：
- **失败锁定**：激活码 / 恢复码 / 备份口令均限尝试次数（建议 5 次），超限锁定 15 分钟（`ACCOUNT_ACTIVATION_LOCKED` / `ACCOUNT_RECOVERY_LOCKED` / `BACKUP_IMPORT_REJECTED(attempts)`）；自助注册按 IP + 账号维度限流（不泄露账号是否存在，超限统一"尝试次数过多"）。
- S3、S4、S5 必须与登录页同域同组件，不做独立入口页。
- 所有状态页的标题、说明**不得出现层级词**（§9.9）；S1/S1c 的按钮只允许"登录 / 创建账户"两个主词。
- **页面文案冲突以本节为准**：登录页不得出现"邀请码、加入、成员、子账号、上级、层级"。

现状可复用：`/v1/auth/config`（`auth.py:728-798`）已返回 `secret_store.configured`、`registered_humans`、`login_eligible_humans`、`token.ttl_seconds`，以及 `root.exists` / `registration.open` / `recovery.available` / `migration.allowed`（扁平与嵌套双写）。**仍需新增** `self_registration.enabled`（现状未暴露，前端无法判定 S1c）。注意：`registration_open` 现状语义仍是"**是否还能注册根**"（`= not root_exists`，`auth.py:790-792`），本文要求改为"**根存在时是否允许成员自助注册**"；两套语义并存期，前端必须同时读开关字段，不能把"有根"直接当成"注册关闭"。

前端现状：`Login.tsx` 已有 ``login`` / ``register`` 两个页签与 `SelfRegisterForm`（`Login.tsx:56-63,255-282,395-405`），但页签显示条件仍是 `!noAccount`、`registrationOpen` 仍绑定 `!root.exists`（`Login.tsx:147-149`）→ **有根时不会出现成员自助注册，只会出现"首次使用，创建本机账号"**；S1c 尚未接线。另：`lib/auth.ts:330-341` 的 `registerSelf` 发送 `{principal, secret, client}`，而后端 `SelfRegisterRequest` 接收 `password` → 契约不一致（会 422）；本文以**后端字段 `password`** 为准（改名属前端实现，见 §12）。

### 9.3 首启创建主账号（S0）文案要点

- 标题：**首次初始化 · 创建主账号**
- 说明（三段，必须写全）：
  1. "这台设备还没有主账号。第一个完成注册的人将成为**主账号（根）**。"
  2. "主账号拥有全部子账号的权限、记忆与技能，可创建子账号、查看整棵子树。"
  3. "创建后**永久关闭"注册根"**；其他成员可在登录页点"创建账户"自助注册（默认开启），也可由主账号在「成员管理」中创建。"
- 表单：显示名、登录账号、密码（≥ 建议 10 位）、二次确认密码。
- 二次确认：勾选"我理解主账号拥有最高权限，且此操作不可撤销（可转移）"。
- 成功后：直接登录进入控制台。
- **紧接一次性恢复材料页（不可跳过）**：展示恢复码（仅此一次）并提供「下载恢复文件」；必须勾选"我已妥善保存"才能继续；文案与不可逆边界见 §9.11。
- 主账号身份条显示"主账号 · 可管理 0 位成员"。

### 9.4 成员开通与激活（两条路径）

**路径 A · 自助注册（默认开启，主路径）**

1. 登录页 S1 点"**创建账户**"→ 表单只收集：登录账号、显示名、密码、确认密码（**不询问任何组织/上级信息**，页面上不出现"邀请码/加入/成员/上级/层级"）。
2. 服务端判定：`root.exists && self_registration.enabled && account_id 未被占用`；请求体中的 `parent_account_id` / `role_kind` / `depth` **一律忽略且不参与建号**（现状 `extra=ignore`），不得据此改变上级或角色。
3. 建号：`origin=self_signup`、`parent_account_id=根`、`depth=1`、`status=active`、`credential_state=password_set`、模板"协作者"（§7.5）。
4. 直接签发令牌进入控制台；**无激活、无邀请码、无强制改密**；审计 `ACCOUNT_SELF_REGISTERED`。
5. 关闭开关后：S1 不渲染"创建账户"，接口返回 `403 ACCOUNT_SELF_REGISTER_DISABLED`（§9.5）。

**路径 B · 主账号创建 + 激活码（保留）**

1. 创建者在「成员管理」→「新建成员」：填登录账号、显示名、选择**角色预设模板**（§7.5）、初始权限（只能是创建者权限子集）、勾选"**允许团队共享我的 Key**"（建议默认勾选，可取消）。
2. 系统生成**一次性激活码**：默认 **24 小时**有效（代码 `DEFAULT_ACTIVATION_TTL_SECONDS`，可用 `LIUHAO_ACCOUNT_ACTIVATION_TTL_SECONDS` 调整）、一次性、只显示一次、可复制；同一账号重新生成时**旧码立即失效**。
3. 被创建人打开同一登录页 → S2a：输入账号 + 激活码。
4. 开通成功后跳转**强制改密页**（不能跳过、不能返回、不能进侧栏）；页面文案："首次登录必须设置你自己的密码，之后激活码作废。"
5. 改密成功 → 签发令牌 → 进入控制台（界面按能力裁剪，**不显示任何层级信息**）。
6. 失败锁定：同一账号连续 5 次激活码错误 → 锁定 15 分钟（`ACCOUNT_ACTIVATION_LOCKED`），锁定期间不再提示"正确/错误"，统一"尝试次数过多"。

界面要求：创建成功的弹层必须给出"复制激活码 / 复制激活说明"两个动作，并警告"关闭后无法再次查看，只能重新生成"。**激活说明不得出现层级词**；登录页本身不出现"激活码"字样（只出现在 S2a 激活页）。

### 9.5 两个开关：注册根（永久关闭）与成员自助注册（可改）

| 项 | 判定 | 服务端 | 前端 |
|---|---|---|---|
| **注册根** | `root.exists == true` | 任何"注册根"请求返回 `409 ACCOUNT_ROOT_REGISTRATION_CLOSED` | 登录页**永不**渲染"创建主账号"入口 |
| **成员自助注册** | `allow_self_registration`（持久化，默认 `true`） | 关闭时所有注册请求返回 `403 ACCOUNT_SELF_REGISTER_DISABLED` | S1 隐藏"创建账户"；S1c 只保留登录/重置/迁移 |

- 开关位置：根账号「成员管理」→「设置」；**仅根可改**；变更立即生效并写审计 `ACCOUNT_SELF_REGISTRATION_TOGGLED`；非根账号看不到该设置项。
- 关闭**不删除**已注册账号：已注册成员可正常登录，除非被停用（`ACCOUNT_UNAVAILABLE`）。
- **现状差距**：后端（`src/gateway/accounts.py:205-210`）关闭文案为"管理员已关闭自助注册"，非中性；按本文统一为下一行的唯一文案（仅改文案，不改状态码/错误码）。
- 关闭后的中性文案（不得提"管理员关闭/开关/层级"）：

> **当前无法创建账户。**

- 若用户通过旧链接直达注册接口，同样只显示上面这一句；真实原因只进审计 `reason_code=closed`。
- **口径冲突**：早期"根创建后关闭自助注册，成员只能由上级创建"与 2026-11-06 定稿冲突，**以 §0 第 22 项为准**。
- 唯一例外：`root.status == deleted` 的灾难恢复路径不开放给普通用户，只能由运维脚本处理（本文不定义该流程）。

### 9.6 同一控制台的导航裁剪

现状 `apps/console/console/src/lib/nav.ts:44-76` 为固定导航，无按权限裁剪。

| 导航项 | 根账号 | 非根账号 |
|---|---|---|
| 总览 / 鎏灏管家 / 我AI员工 / 业务中心 / 知识中心 / 工作台 | 可见 | 可见（自有范围，完整体验） |
| 目标与任务 / 项目 / 应用 / 数据中心 | 可见（全树） | 可见（自有） |
| 事件流 | 可见（全树） | 可见（自己） |
| 审批中心 | 可见 | 可见（自身动作） |
| 审计链 | 可见（全树，只读） | 可见（自己 + 直接下级，只读） |
| 信任 / 网络 | 可见 | 可见（只读） |
| 身份与权限（现有 `identity` 页） | 可见（全树） | 可见（只读自身） |
| 工作区文件 | 可见（全树） | 可见（仅自己） |
| **成员与权限管理**（核心） | 可见（全树） | **不可见** |
| **权限矩阵**（核心） | 可见（全树） | **不可见** |
| **子树记忆/用量/审计全量**（核心） | 可见 | **不可见**（仅自己 + 直接下级） |
| **跨机认领与备份导出**（核心） | 可见 | **不可见** |
| **系统升级 / 受控自改代码**（核心） | 可见 | **不可见** |
| **全部 Key 统一管理**（核心） | 可见 | **不可见**（只有"我的 Key"） |
| **角色预设模板**（核心） | 可见 | **不可见** |
| **恢复码 / 恢复文件**（核心） | 可见 | **不可见** |
| 我的成员（仅直接下级，限"团队负责人"模板） | — | 可见；**自助注册默认（协作者）不可见**，无可管理下级 |
| 我的 Key（创建/轮换/吊销/共享开关） | 可见 | 可见 |
| 系统状态 / 生产预检 / 系统设置 | 可见 | **不可见** |

服务端必须对隐藏入口对应的接口同样拒绝；前端隐藏只是"不迷路"，不是授权。**非根账号看不到"为什么没有这些入口"的解释**，也不显示任何"受限/降级"标记。

> **与口径 2 的衔接**：成员对自己**直接下级**的管理（邀请、停用、重置密码）是"日常能力的团队协作"最小面，**不属于核心能力**；它不提供权限矩阵、不提供全树视图、不提供跨层操作，也不出现"下级"字样（页面统一叫"我的成员"）。

### 9.7 身份条与只读查看横幅

**身份条（顶部固定，常驻）**

| 身份 | 文案 | 附加 |
|---|---|---|
| 根账号 | `主账号 · 可管理 N 位成员` | 悬浮展示自己的 `<display_name>`（仅自己可见） |
| 非根账号 | `<display_name> · 工作空间` | **不显示任何"层级/上级/被管理/子账号"信息**，不展示路径 |
| 只读查看模式 | 根：`主账号 · 只读查看中`；非根：`<display_name> · 只读查看中` | 与横幅联动；不显示"代表谁/层级" |

现状 `Shell.tsx:108-117` 只显示 `displayName/principal` 与 `${scope} · 人类主权`，必须替换：**移除"人类主权"字样**（对非根账号尤其会误导），改为上述身份条；非根账号不显示成员数量。

> 非根账号**既不标"子账号"，也不冒称"主账号"**，只声明"自己的工作空间"。前者会暴露结构，后者是欺骗式提示；两者都禁止。

**只读查看横幅（进入"以成员视角查看"时出现，不可关闭只能退出）**

- 位置：内容区顶部，固定，覆盖导航下方。
- 颜色：警示色（与常规主题区分），左侧图标 + 文案：

> **只读查看** · 你正在查看 `<display_name>` 的数据。真实操作者：`<主账号>`。此模式下所有写操作已禁用。

- 右侧按钮：「退出查看」。
- 行为约束：
  - 所有写按钮 `disabled`，hover 提示"只读查看不能写"。
  - 后端对 `effective_account_id != actor_account_id` 的写请求一律 403 `ACCOUNT_VIEW_AS_WRITE_FORBIDDEN`（前端禁用不算数）。
  - 进入与退出各写一条审计（`ACCOUNT_VIEW_AS_STARTED` / `_ENDED`），尝试写记 `ACCOUNT_VIEW_AS_WRITE_BLOCKED`。
- 范围：根账号可对整棵子树开启；非根账号仅可对直接下级开启（若为团队负责人）；其余不可用。

**Key 使用的视觉区分（关键，防止误判为身份切换）**

- "使用共享 Key 跑模型"时**不进入只读模式、不显示横幅**，只在根账号的模型/用量区域显示一枚 chip：
  `模型 Key：<Key 名称> · 归属 <成员显示名> · 账单记 Key 归属账号`
- 顶部身份条保持"主账号"，绝不变成成员。
- 审计中 `ACCOUNT_KEY_USED` **双记**：用量 `usage_account_id=主账号`、账单 `billing_account_id=<成员>`（`payer=<成员>`）；该事件的 `principal_id` 是主账号，**非根账号的审计视图读不到**。
- 非根账号界面**不展示任何"被使用"痕迹**（列表、通知、审计均无）；但供应商后台额度消耗可见（§5.6 边界，不得承诺"完全不可知"）。

### 9.8 页面清单（新增 / 改造）

| 页面 | 状态 | 要求 |
|---|---|---|
| 登录页 | 已有（`Login.tsx`） | 增加 S0/S1/S1b/S1c/S2/S2a/S2b/S3/S4/S5；S0 隐藏登录表单；S1 显示"登录 / 创建账户"两个页签 + "忘记密码？""从备份迁移到本机"两个中性链接；S1c 隐藏"创建账户"；S1b 只显示中性不可用提示 |
| 首启创建主账号 | 新增 | 仅 S0 可见；成功后进入恢复材料页；不可再进 |
| 备份导出 / 迁移认领 | 新增 | 仅根可见；二次确认框列明 N 位成员记忆 / N 个文件 / 总体积；口令输入 |
| 忘记密码 / 重建引导 | 新增 | 恢复码或恢复文件；S5 风险说明与二次确认 |
| 成员与权限管理（根） | 新增 | 树形列表；新建/停用/删除/重置密码/发激活码/移交/角色模板；**"允许自助注册"开关**（`GET/PATCH /v1/accounts/self-registration`，仅根可改） |
| 权限矩阵（根） | 新增 | 行=成员，列=能力；只显示"可授予"的项；不显示"越级项"（避免暴露结构） |
| 我的成员（非根，限团队负责人） | 新增 | 仅直接下级；无权限矩阵、无全树视图、无层级词 |
| 我的 Key | 改造 | 创建/轮换/吊销 + 共享开关"允许团队共享我的 Key" |
| 身份与权限（现有 `identity` 页） | 改造 | 从"全站主体列表"改为"我的范围"；`identity.py:261` 服务端同步裁剪 |
| 审计链（现有） | 改造 | 增加范围筛选：自己 / 直接下级 / 全树（仅根有"全树"） |
| 角色模板 | 新增 | 仅根可见；2–3 个预设，可编辑，禁止包含 core |
| 系统设置（现有） | 改造 | 仅根可见；初版不含"主账号转移"（换机走 §9.10 备份认领） |
| 身份条 / 只读横幅 | 新增 | 常驻组件；`Shell.tsx` 挂载 |

### 9.9 界面文案中性化（硬性自检）

| 规则 | 内容 |
|---|---|
| 禁用词 | 非根账号的任何界面（含标题、空状态、提示、tooltip、邮件/邀请说明）**不得出现**：子账号、孙账号、上级、下级、层级、越级、主账号、根账号、被管理、受限 |
| 禁止解释性文案 | 不写"该功能仅主账号可用""你的权限由上级分配""你属于某团队"；**缺少入口时保持沉默** |
| 身份条 | 非根账号只显示 `<display_name> · 工作空间`，不显示路径、不显示成员数、不显示"人类主权" |
| 空状态 | 列表为空时写"暂无内容"，不写"暂无可见成员/无权限查看" |
| 登录 / 注册页 | 只允许出现"**登录**"与"**创建账户**"两个主词；**禁止**"邀请码、加入、成员、子账号、上级、层级、加入我们、组织"；辅助说明、占位符、校验与错误文案一并计入；见 §4.4、D23 |
| 控制台（非认证页） | "成员"一词可在成员管理页正常使用；但不得出现层级/越级/上级等结构词 |
| 自检方式 | 控制台构建产物做**禁用词扫描**（grep 级），命中即失败；见验收 D23 |

### 9.10 跨机认领（换机迁移）

| 项 | 规则 |
|---|---|
| 入口 | 登录页固定链接"**从备份迁移到本机**"（中性，不写"主账号迁移"） |
| 谁能导出 | **仅根账号**；非根只能导出"我的数据"（§9.12 子账号边界） |
| 导出内容 | 整棵树全量：身份、成员树、设置、Key 密文、全部成员会话/记忆/文件/任务/用量；审计链随包保留只读副本 |
| 导入即认领 | 新机导入成功后，本机身份体系 = 包内身份体系；原机器仍可独立运行（**不做实时同步**） |
| 单根约束 | 目标机若已有根账号 → `ACCOUNT_MIGRATION_CONFLICT`，不得合并、不得覆盖 |
| 实时同步 | **待决策 / 不做**。任何文案不得出现"多设备实时同步/云端同步/自动漫游" |
| 导入后 | 所有旧会话令牌失效；根账号重新登录；成员保持原状态（active 可直接登录，pending 仍需激活码）；写 `BACKUP_IMPORTED` |

### 9.11 忘记密码与"重建身份体系"

**根账号创建时一次性产出两件恢复材料：**

| 材料 | 形态 | 规则 |
|---|---|---|
| 恢复码 | 一次性展示的随机串（建议 ≥ 24 位） | 只在创建/重新生成时展示一次；界面不再次显示；服务端只存哈希 |
| 恢复文件 | 可离线保存的加密文件 | 内含根账号标识、恢复码、创建时间；下载后本地保存，系统不保留副本 |

**找回流程（S3）**：输入账号 + 恢复码（或上传恢复文件）→ 校验（恒定时间比较 + 5 次失败锁定 15 分钟）→ 设置新密码 → **吊销该账号全部会话** → 登录。审计 `RECOVERY_CODE_USED`。重置密码**不改变**成员树、权限、记忆、Key 归属。

**S3b 没有恢复码时的本机自救（0.1.0 老数据升级 / 从未签发过恢复码）**

判定：根存在但 `recovery_hash` 为空串。此时 `GET /v1/auth/config` 返回
`recovery.available=false`、`recovery.self_service=true`，界面**不得**展示"用恢复码重置"的可用状态。

| 情况 | 入口 | 规则 |
|---|---|---|
| 还能登录 | `POST /v1/accounts/recovery/rotate`（需根令牌） | 生成/重发一枚恢复码，旧码立即作废；响应只含 `recovery_code`，**不含 `expires_at`** |
| 已经忘记密码 | `POST /v1/accounts/recovery/local`（无需令牌，**仅回环来源**） | 码以**非明文**形态写入 `<数据目录>/config/recovery_code.txt`（Windows 用 DPAPI 加密，其他平台/失败时用 ACL + 短 TTL）；本请求解密**一次**并在响应里交给本机用户，响应返回后交接文件立即删除、解密暂存清零；响应只给文件名，不回显绝对路径 |

**落盘保护（硬约束）**：恢复码**永不以明文长期落盘**。

- Windows 优先 DPAPI（`CryptProtectData`，CurrentUser 作用域）：进程重启后仍可解密，同机其他账户解不开；文件 DACL 显式去掉继承、只保留当前用户（`chmod` 在 Windows 上只是只读位，不算数），启动自检复核实际 ACL，不通过就告警。
- DPAPI 不可用（非 Windows / 调用失败）时降级为“ACL 收紧 + 短 TTL（默认 10 分钟）”，并记录降级原因；到期或取用即删，不允许长期驻留。
- 取用语义是**一次性**：`/recovery/local` 解密一次后立即删除文件，不缓存、不写第二份；响应里不回显任何绝对路径。

`/recovery/local` 的安全前提仍是**必须能访问本机数据目录**（同一目录里的凭据库与身份库本来就可被该用户改写）；现在额外要求**回环来源**，对端回环时整条 `X-Forwarded-For` 也必须回环。仅当根**确实没有恢复码**、或本机已有未取走的加密交接文件时开放（否则任何请求都能作废用户手上的有效码）；同一限次窗口内超过 5 次返回 429 `ACCOUNT_RECOVERY_LOCKED`；写盘/权限收紧失败返回 500 `ACCOUNT_RECOVERY_WRITE_FAILED`，并**撤销刚生成的码**，否则账号会停在"服务端有码、用户没有码"的死角。

`POST /v1/accounts/recovery/reset` 默认同样只接受回环来源；远程重置必须由管理员显式设置 `LIUHAO_ALLOW_REMOTE_RECOVERY=1`（默认关闭），打开时启动日志必须记录“已允许非本机恢复”。

**旧明文交接文件（0.1.0 及更早）**：升级/启动时识别 `<数据目录>/config/recovery_code.txt` 的明文格式，**保留码的有效性、不追溯失效**，原地转成加密格式（同一路径覆盖，不保留双份）；转换成功后旧明文文件内容随之消失。若加密写盘或 ACL 收紧失败，则保留原文件并记录错误、下次启动重试（此时启动自检持续告警）。迁移完成后 `/v1/auth/config` 与登录响应给出 `recovery.rotate_recommended=true`，提示用户登录后重新生成恢复码。

> **残余风险（必须如实告知）**：旧版本曾以明文保存恢复码，且同机其他用户可读；这段明文窗口期内恢复码**可能已被读取过**。转加密只能止损，不能证明未泄露。因此迁移后应尽快在登录状态下调用 `/recovery/rotate` 换新（旧码一次性失效）。
>
> 另需说明：`/recovery/local` 的“回环可达”对本机其他账户的进程同样成立。DPAPI 保护的是**磁盘副本**不被其他账户解开，不承诺阻止同机进程调用该接口；能在这台机器上运行进程并访问回环地址的主体，仍在信任边界之内。

**恢复码没有 TTL**：它不按时间失效，只在被使用（或被重新生成）时失效。接口与界面都不得展示"有效期至/expires_at"。

**非根账号的找回**：不签发恢复码/恢复文件。忘记密码只能由其管理范围内的管理者重置（主账号在「成员管理」、团队负责人在「我的成员」，`ACCOUNT_PASSWORD_RESET_BY_ADMIN`）。S3 对非根账号一律返回中性提示"无法自助找回，请联系系统管理员。"，**不说明任何层级关系**。

**不可逆边界（必须原文展示）：**

> 恢复码与恢复文件是找回凭据的唯一途径。
> - 重置密码不会恢复已删除的成员或数据；
> - 恢复码一经使用即失效，重新生成后旧码立即作废；
> - 若恢复码与恢复文件**同时丢失**，且无法访问本机数据目录（S3b），只能进入"重建身份体系"：**数据保留，但身份关系需重新认领**，旧凭据永久失效。

**S5 重建身份体系（两者皆失）**：

1. 展示上述不可逆说明 + 二次确认 + 输入运维口令（仅本机运维可执行）。
2. 重建后：根凭据重置；成员账号记录保留，但其登录凭据全部失效——**自助注册账号可在开关开启时重新自助注册同名账号**，其他成员需由根重新发激活码；恢复码重新生成；旧恢复码/文件永久失效。
3. 审计 `IDENTITY_REBUILT`（`data_retained=true`，记录成员数、保留条目数）。
4. **不承诺**"数据零丢失"之外的任何自动化恢复；不做"忘记密码自动找回"。

### 9.12 备份包规格与导入安全

**导出（仅根）**

1. 入口在系统设置；点击后**二次确认框**必须列明具体数量，不允许笼统措辞：
   > 本次备份将包含：**N 位成员的个人记忆（共 X 条）**、**N 个文件（共 Y MB）**、N 个会话、N 个任务、N 个 Key（密文）。总体积约 **Z MB**。
   > 备份包含他人个人数据，请妥善保管，禁止外传。
2. 用户设置**备份口令**（建议 ≥ 12 位）；口令**不落盘、不写日志、不进审计**；界面提示"口令丢失将无法打开备份"。
3. 包格式：`*.lhbak`；AES-256-GCM 加密；KDF 用 scrypt/PBKDF2；头部含版本、创建时间、manifest（成员清单、各域条目数、总体积、摘要）。
4. **永不打包**：`config/recovery_code.txt`（含 DPAPI 密文副本）、凭据文件 `auth_secrets.json`、密钥文件 `model_config_key.bin`。升级备份与 `export-snapshot` 在复制层过滤，并在打包后断言目标目录不含这些文件；已生成的旧备份不追溯。
5. 导出完成写 `BACKUP_EXPORTED`（actor、时间、`member_ids`、成员数、各域计数、总体积、结果）；**不含口令**。
6. 下载完成后提示"这是完整备份，含全部成员个人数据，请离线保存"。

**导入（S4）**

| 步骤 | 规则 |
|---|---|
| 口令 | 必须输入；错误 5 次锁定（`BACKUP_IMPORT_REJECTED(attempts)`） |
| 完整性 | 校验包签名/摘要/版本；失败 `ACCOUNT_MIGRATION_PACKAGE_INVALID` |
| 防重放 | 包内 `package_id` + `nonce` 记入本机导入登记表；已导入过的包再次导入 → `ACCOUNT_MIGRATION_REPLAY_DENIED` |
| 冲突 | 本机已有根 → `ACCOUNT_MIGRATION_CONFLICT`；不做合并/覆盖 |
| 成功后 | 导入整棵树；旧令牌全部失效；写 `BACKUP_IMPORTED`；界面提示"已认领，请使用原账号登录" |

**子账号导出边界**：非根账号只能导出"我的数据"（自己的会话/记忆/文件/任务/自有 Key），服务端强制按 `owner_account_id` 过滤，**不可能**包含其他成员数据；导出同样写审计。



---

## 10. 验收场景清单

判定口径：每条都要有**可观察结果**（HTTP 状态 + 错误码 + 审计事件），不接受"理论上可以"。

### 10.1 正常场景

| ID | 场景 | 操作 | 期望结果 |
|---|---|---|---|
| N1 | 首启创建根账号 | 全新库，S0 页面提交创建 | 创建成功；`ROOT_BOOTSTRAP_COMPLETED`；自动登录；展示恢复码与恢复文件下载；身份条"主账号 · 可管理 0 位成员" |
| N2 | 根账号创建成员（路径 B 保留） | 新建成员、选模板、勾选"允许团队共享我的 Key"、生成激活码 | 成员 `pending_activation`；返回一次性激活码；`ACCOUNT_CREATED` 不含码明文；成员树可见 |
| N3 | 成员激活并强制改密 | 用激活码登录 | 先开通，再强制改密；未改密访问业务接口 403 `ACCOUNT_PASSWORD_CHANGE_REQUIRED`；改密后进入控制台 |
| N4 | 成员创建自己的下级 | depth=1 成员新建直接下级（默认上限 depth 2） | 成功；新账号 depth=2；成员树可见；**成员界面不显示任何层级信息** |
| N5 | 成员管理自己的成员 | 成员把"自己已持有"的技能授予直接下级 | 成功；`ACCOUNT_PRIVILEGE_GRANTED`；下级权限生效；界面无层级词 |
| N6 | 根账号使用共享 Key | 根账号选择已授权共享的成员 Key 发起对话 | 调用成功；`ACCOUNT_KEY_USED` **双记**：用量 `actor=根`、账单 `key_owner=成员`、`payer=成员`；仅根界面显示 chip；成员侧只看到自己 Key 额度下降、不显示调用者；身份条仍为根 |
| N7 | 根账号聚合读子树记忆 | 打开记忆聚合 | 返回子树条目 + `source_account_id`；写被禁；`MEMORY_AGGREGATE_READ` |
| N8 | 根账号授予技能给子树 | 把技能授予成员的子树 | 成功；`SKILL_GRANTED`（`grant_scope=subtree`）；子树可见 |
| N9 | 根账号只读查看成员 | 开启只读查看 | 出现只读横幅；写按钮禁用；`ACCOUNT_VIEW_AS_STARTED`；退出后 `_ENDED` |
| N10 | 成员查看自己 + 直接下级审计 | 打开审计链 | 只返回自己 + 直接下级；不含兄弟子树；无"全树"筛选项 |
| N11 | 成员吊销自己的 Key | 吊销自有 Key | 成功；立即不可用；`ACCOUNT_KEY_REVOKED` |
| N12 | 正常登出 | 任意账号登出 | 令牌失效（`auth.py:549` 撤销机制）；再访问 401 |
| N13 | 非根账号的完整日常体验 | 成员登录后使用对话/管家/知识库/文件/自动化/自有 Key | 全部可用且限自有范围；**无核心入口**（成员与权限管理/权限矩阵/备份/系统升级/全部 Key 统一管理）；仅有最小"我的成员"协作面；无受限提示、无层级字样 |
| N14 | 权限变更即时生效 | 根账号把成员权限降级 | 该成员**已签发令牌在下一次请求即被拒**（不必重新登录）；`ACCOUNT_PERMISSION_CHANGED` + `ACCOUNT_SESSIONS_REVOKED` |
| N15 | 停用即时生效 | 根账号停用成员 | 旧令牌下一次请求 403 `ACCOUNT_INACTIVE`；`ACCOUNT_STATE_CHANGED(effective_immediately=true)` |
| N16 | 全量备份导出 | 根账号导出整棵树 | 二次确认框列明"包含 N 位成员的个人记忆（X 条）、N 个文件（Y MB）、总体积 Z MB"；设置口令；`BACKUP_EXPORTED` 记成员清单与计数，**不含口令** |
| N17 | 换机导入认领 | 新机 S4 上传包 + 口令 | 导入成功；成员身份与数据完整恢复；旧令牌失效；`BACKUP_IMPORTED`；根账号可登录 |
| N18 | 恢复码重置根密码 | S3 输入账号 + 恢复码 | 重置成功；根账号全部会话被吊销；`RECOVERY_CODE_USED`；恢复码立即失效 |
| N19 | 根账号重置成员密码 | 成员管理 → 重置密码 | 成功；成员被强制改密；`ACCOUNT_PASSWORD_RESET_BY_ADMIN`（含 `by_admin=true`） |
| N20 | 删除成员连锁 | 根账号删除一个无下级的成员 | Key 立即吊销 + 解绑提示；记忆归档（仅根可读）；审计保留；`ACCOUNT_DELETED`/`MEMORY_ARCHIVED` |
| N21 | 角色模板建成员 | 用"协作者/只读访客/团队负责人"模板创建 | 初始权限为模板子集；模板不含 core；`ROLE_TEMPLATE_CHANGED` 仅在模板被编辑时记 |
| N22 | 成员导出自己的数据 | 非根账号导出"我的数据" | 仅含自己的会话/记忆/文件/任务/自有 Key；`MEMORY_EXPORTED` 记范围为自己 |
| N23 | 成员自助注册（路径 A，主路径） | 已有根且开关开启；登录页「创建账户」提交账号 + 密码 | 建号成功：`origin=self_signup`、`parent=根`、`depth=1`、`status=active`、模板"协作者"；自动登录；`ACCOUNT_SELF_REGISTERED`；**界面零层级词** |
| N24 | 自助注册开关关闭 | 根在「成员管理」关闭"允许自助注册"；登录页再试 | 开关持久化（重启仍关闭）；S1 不渲染"创建账户"（S1c）；直连接口 403 `ACCOUNT_SELF_REGISTER_DISABLED`，文案"当前无法创建账户。"；`ACCOUNT_SELF_REGISTRATION_TOGGLED`（含 before/after） |
| N25 | 两条路径权限一致 | 同模板下，分别用自助注册与"根创建 + 激活码"建号 | 除 `origin` / `credential_state` 外权限集**完全相同**（均为模板子集）；无"自助注册少拿/多拿"差异 |
| N26 | 自助注册默认最小权限 | 新账号登录后依次打开会话、记忆、文件、Key、模型、审计、用量 | 全部只显示自己的；无他人/根资源；无成员管理入口；无聚合入口；无"受限"提示 |
| N27 | 根停用/删除自助注册账号 | 根在成员管理停用后删除（叶子） | 停用即时生效（旧令牌下一请求 403 `ACCOUNT_INACTIVE`）；删除后账号不可登录；`ACCOUNT_STATE_CHANGED` / `ACCOUNT_DELETED`（`mode=leaf_only`）；其 Key 吊销、记忆归档 |

### 10.2 反例（必须被拒）

| ID | 场景 | 操作 | 期望结果 |
|---|---|---|---|
| **D0** | **串数据（最高优先级）** | 种子两个账号 A、B，各写会话/记忆/文件/任务/向量条目；分别调用列表、检索、聚合、导出 | A 的结果中 **0 条** B 的记录，反之亦然；向量检索同样为 0；任何一次串数据即视为发布阻塞（`DATA_ISOLATION_VIOLATION`） |
| D1 | **越级授权**（V1） | （配置放宽到 4 层后）depth=1 成员修改 depth=3 账号权限 | 403 `ACCOUNT_LEAPFROG_DENIED`；文案"无权执行该操作。"；`ACCOUNT_TREE_VIOLATION(violation=V1)` |
| D2 | **向上授权**（V1） | 成员修改根账号权限 | **404** `ACCOUNT_UPWARD_DENIED`；响应不含上级/层级字样；审计记真实原因 |
| D3 | **横向查询**（V2） | 成员 A 查成员 B 的会话 | 404 `ACCOUNT_SUBTREE_DENIED`；`ACCOUNT_TREE_VIOLATION(V2)` |
| D4 | **横向列表**（V2） | 成员 A 调 `/v1/identity/principals` | 只返回自己 + 直接下级；不得出现 B 及其子树；无"全树"字段 |
| D5 | **借上层 Key**（V3） | 成员请求用根账号 Key | 403 `ACCOUNT_KEY_UPWARD_DENIED`；文案"该 Key 不可用于当前工作区。"；调用不发生 |
| D6 | **用下级 Key**（V3） | 成员请求用自己下级的 Key | 403 `ACCOUNT_KEY_NOT_OWNED` |
| D7 | **读他人记忆**（V4） | depth=2 账号读兄弟子树记忆 | 403 `ACCOUNT_MEMORY_FOREIGN_DENIED`；文案"无权访问该内容。" |
| D8 | **聚合越权** | 成员调记忆聚合接口 | 403 `ACCOUNT_MEMORY_FOREIGN_DENIED`（无聚合权） |
| D9 | **只读查看写操作** | 根账号在只读查看下提交写请求（绕过前端） | 403 `ACCOUNT_VIEW_AS_WRITE_FORBIDDEN`；`ACCOUNT_VIEW_AS_WRITE_BLOCKED` |
| D10 | **请求体冒用身份** | 请求体带 `principal=成员` 但令牌是根 | 400 `ACTOR_FROM_BODY_FORBIDDEN`（沿用 `policy.py:14-26`"主体只来自令牌"，扩展到账户字段） |
| D11 | **删除父账号** | 成员尝试删除根账号 | **404** `ACCOUNT_UPWARD_DENIED`；无任何删除；文案中性 |
| D12 | **删除有下级的账号** | 根删除仍有下级的成员 | 409 `ACCOUNT_DELETE_HAS_CHILDREN`；提示先移交或级联停用 |
| D13 | **删除根账号** | 根删除自己 | 409 `ACCOUNT_DELETE_ROOT_FORBIDDEN`；文案"该账号为系统账号，不能移除。" |
| D14 | **初始化后再注册根**（区别于成员自助注册） | 已有根时访问"注册根"入口 | 409 `ACCOUNT_ROOT_REGISTRATION_CLOSED`；文案"系统已完成初始化，请使用已有账号登录。"；**不显示根名/成员数**；成员自助注册不受此限（走 `/self-register`） |
| D15 | **未激活登录** | 未开通账号直接登录 | 403 `ACCOUNT_ACTIVATION_REQUIRED`；不签发令牌 |
| D16 | **激活码复用/过期** | 同一激活码第二次使用 / 超有效期（默认 24 小时，可配置） | 400 `ACCOUNT_ACTIVATION_CODE_INVALID`；文案"激活码无效或已失效。" |
| D17 | **停用账号旧令牌** | 已停用账号用旧令牌访问 | 403 `ACCOUNT_INACTIVE`；旧令牌**下一次请求即失效**（吊销预检 fail-closed） |
| D18 | **深度超限** | 在 depth=2（默认上限）账号下创建下级 | 409 `ACCOUNT_DEPTH_EXCEEDED`；文案"当前工作区暂不能再添加成员。"；不暴露层数 |
| D19 | **权限放大** | 成员把"管理成员"授予下级，但自己无该权限 | 403 `ACCOUNT_PRIVILEGE_ESCALATION`；文案"无权授予该权限。" |
| D20 | **迁移旧库** | 旧库（仅 `human_identities`，无 parent）启动 | 见 §10.3；不得把所有人类当主账号 |
| D21 | **看上级/兄弟技能** | 成员查看未授权技能 | 清单不出现；直接请求 404 `SKILL_VISIBILITY_DENIED`（中性文案） |
| D22 | **Key 明文导出** | 任意角色请求 Key 明文 | 403 `ACCOUNT_KEY_PLAINTEXT_FORBIDDEN`；响应永不含明文 |
| D23 | **界面禁用词** | 构建产物做禁用词扫描（§9.9） | 非根页面命中"子账号/上级/层级/越级/被管理/受限"等即失败；**登录/注册页另加"邀请码/加入/成员/子账号/上级/层级"零命中**（按钮、占位符、校验与错误文案、辅助说明全部计入） |
| D24 | **核心能力越权** | 非根直接调**全树**成员管理 / 权限矩阵 / 备份 / 系统升级 / 全部 Key 管理接口 | 403 `CORE_CAPABILITY_DENIED`；文案"无权执行该操作。"；最小"我的成员"接口不在其列 |
| D25 | **错误文案中性** | 抽查 D2/D5/D11/D18 响应与界面 | 响应体 `message` 无层级词；真实原因只在审计 |
| D26 | **激活码失败锁定** | 连续 5 次错误激活码 | 第 6 次起 429 `ACCOUNT_ACTIVATION_LOCKED`；锁定期间不区分对错 |
| D27 | **备份口令错误 / 重放** | 错误口令 5 次；已导入包再次导入 | 前者锁定 `BACKUP_IMPORT_REJECTED(attempts)`；后者 `ACCOUNT_MIGRATION_REPLAY_DENIED` |
| D28 | **迁移包损坏 / 冲突** | 上传损坏包；本机已有根时导入 | 400 `ACCOUNT_MIGRATION_PACKAGE_INVALID`；409 `ACCOUNT_MIGRATION_CONFLICT`；不合并、不覆盖 |
| D29 | **恢复码失效 / 两者皆失** | 使用已用过的恢复码；无码无文件点"重建" | 400 `ACCOUNT_RECOVERY_CODE_INVALID`；S5 展示不可逆说明并要求二次确认，默认不执行 |
| D30 | **撤销共享后仍用 Key** | 成员撤销"允许团队共享我的 Key"，根再发起调用 | 403 `ACCOUNT_KEY_CONSENT_REQUIRED`；调用不发生；`KEY_CONSENT_REVOKED` 已落盘 |
| D31 | **模板注入核心能力** | 把 core 能力写进角色模板 | 保存被拒（子集校验）；`ROLE_TEMPLATE_CHANGED` 不落 allowed |
| D32 | **多根检测** | 手工向库写入第二个根 | 启动/登录 fail-closed；409 `ACCOUNT_MULTI_ROOT_DETECTED`；不自动挑选 |
| D33 | **删除后数据仍可被搜到** | 删除成员后，其他成员检索记忆/向量 | 检索结果 0 条；归档仅根可读 |
| D34 | **子账号导出他人数据** | 成员构造导出参数指向他人 | 服务端强制按 `owner_account_id` 过滤；结果只含自己；越权字段被忽略而非报错泄露 |
| D35 | **自助注册自选上级** | 注册请求体带 `parent_account_id` / `role_kind` / `depth`（含指向兄弟或更上层） | 字段被**忽略且不参与建号**；新账号仍为 `parent=根`、`depth=1`、成员角色；响应不含父/深度；审计 `source=self_service` |
| D36 | **已有根再注册根** | 有根时调用自助注册并传 `role=root` / 复用注册根接口 | 409 `ACCOUNT_ROOT_REGISTRATION_CLOSED`（注册根接口）或按成员建号（自助注册）；**任何路径都不得产生第二个根**；审计 `ROOT_SELF_REGISTRATION_REJECTED` |
| D37 | **自助注册默认建下级** | 新自助注册账号直接调"新建成员"接口 | 403 `CORE_CAPABILITY_DENIED`；模板无 `account:manage_children`；不得因 `model:manage` 被误判为可管成员 |
| D38 | **自助注册使用根/他人 Key** | 新账号请求根账号或兄弟的 Key | 403 `ACCOUNT_KEY_UPWARD_DENIED` / `ACCOUNT_KEY_NOT_OWNED`；调用不发生；`SELF_SERVICE_SCOPES` 无 `account:key_borrow` |
| D39 | **自助注册读他人数据** | 新账号构造会话/记忆/文件/审计/模型查询指向他人或根 | 404 `ACCOUNT_SUBTREE_DENIED` 或 403 `ACCOUNT_MEMORY_FOREIGN_DENIED` / `ACCOUNT_MODEL_FOREIGN_DENIED`；结果 0 条；响应不泄露目标存在 |
| D40 | **停用/删除后仍访问** | 根停用该账号；账号用旧令牌请求；再用账号密码登录；根删除该账号 | 旧令牌下一次请求 403 `ACCOUNT_INACTIVE`；登录 403 `ACCOUNT_UNAVAILABLE`（中性文案）；删除仅叶子，非叶子 409 `ACCOUNT_DELETE_HAS_CHILDREN` |
| D41 | **自助注册重名** | 用已存在（含保留名）的登录账号注册 | 409 `ACCOUNT_EXISTS`；文案"该登录账号不可用。"；不提示占用者；不覆盖原账号 |
| D42 | **老根账号补发恢复码 / 本机自救** | 0.1.0 升级的老根（`recovery_hash` 为空）：登录后调 `/recovery/rotate`；未登录调 `/recovery/local`；已有恢复码时再调 `/recovery/local` | 前者 200 返回新码、立即可用、无 `expires_at`，二次使用 400 `ACCOUNT_RECOVERY_CODE_INVALID`；后者把码写入 `<数据目录>/config/recovery_code.txt` 且响应不含码，重置成功后文件删除；已有码时 403 `ACCOUNT_RECOVERY_UNAVAILABLE` 且旧码仍有效；`/v1/auth/config` 的 `recovery.available` 与实际 `recovery_hash` 一致 |

### 10.3 迁移旧库（D20 展开）

现状：`human_identities`（`alembic/006:157-169`）只有 `principal/display_name/permissions/scope/registered_at/source/trust_score/integrity`，**无 parent/path/depth**；`memory_entries` 无 owner；`ai_model_configs` 无 owner。

迁移要求（顺序固定）：

1. **选根**：优先 `permissions` 含 `owner` 的账号（与 `auth.py:467-487` 的 owner 判定一致）；若多个，取 `registered_at` 最早者；若为 0，迁移中止并提示"未找到 owner 账号，请先指定根账号"，**不得自动指定**。
2. 其余人类一律设为根的**直接下级**（depth=1），`status = active`（不打断现有登录），并标记 `migrated=true` 待人工确认；**不自动生成 depth>=2**。
3. 旧记忆、旧模型 Key、旧 API Key 统一挂 `owner_account_id = <根>`（保留 `legacy=true`）。
4. 旧审计**不改写**（哈希链不可破坏）；查询侧对无 `actor_account_id` 的旧事件按 `principal_id` 兜底映射，并在 UI 标注"迁移前记录"。
5. 迁移完成后写 `ACCOUNT_TREE_MIGRATED`（建议事件名），落根账号、成员数、迁移条目数；**默认 `max_depth=2`** 在迁移后立即生效。
6. **禁止**：把非 owner 账号提升为根；把旧记忆平均分配；静默删除无主数据；迁移后自动创建第二棵树。

可见性矩阵、越级判定、Key 规则、记忆规则在迁移后立即生效；`legacy` 数据默认仅根可读。

---

## 11. 开放问题（5 条，已全部裁决）

> 裁决日期：**2026-11-06**。各条下方保留"待拍板"历史记录与原始建议值；**冲突时以"已裁决"为准**。

### Q1. 层级上限默认 3 层已定，可配置的最大值是多少？

- **已裁决（2026-11-06）**：出厂 `max_depth = 2`（3 层：根 → 成员 → 成员的下级）；配置范围放宽为 **0–7**（复用 L0–L7 刻度）；调整必须在系统设置中显式修改并写审计。
- **已定（历史）**：默认 `max_depth = 2`，可配置。
- **待拍板（历史）**：配置上限是否允许到 `depth = 7`？
- **原建议默认（历史）**：允许 0–7，出厂固定 2。
- 影响：§1.2、§2.1、§2.4、§10.3；成员树展示深度、"可管理 N 位成员"计数口径（计整棵活跃子树）。

### Q2. 主账号对子账号记忆：只读聚合，还是可直接编辑/删除？

- **已裁决（2026-11-06）**：默认**只读聚合**——可**检索**、可**导出整棵树**，**不得静默改写**；仅"**交接 / 子账号失联**"场景可开**显式限时代管**（可撤销、必须写审计），其余场景一律拒绝。
- **待拍板（历史）**：编辑/删除是否允许？原建议为"编辑须开代管授权、删除仅随账号归档"。
- **原建议默认（历史）**：默认只读聚合；编辑必须开启"代管授权"（限时、显式、可撤销、写审计）；删除仅随"删除账号"归档（30 天可恢复），不做单条静默删除。
- 理由：满足口径 4"主账号拥有所有子账号的记忆"，同时不把"拥有"解释成"可静默篡改下级的个人记忆"。
- 影响：§3.4、§6.2、§6.3、§8 的 `MEMORY_AGGREGATE_READ` / `MEMORY_ARCHIVE_*`；整机备份按 §9.12（已定稿为全量）。

### Q3. 主账号使用子账号 Key 时，费用记谁？

- **已裁决（2026-11-06）**：**双记**——账单按 `key_owner_account_id` 记，用量按 `actor_account_id` 记，两者**分别入库**；主账号可见全部，子账号只看到**自己 Key** 的额度消耗且**不显示调用者**。
- **待拍板（历史）**："谁发起谁付费"还是"Key 归属付费"？原建议为 `payer = 主账号`。
- **原建议默认（历史）**：`payer_account_id = 主账号`，同时按 `key_owner_account_id` 做归属统计；子账号自带 Key 时 `payer = 自己`。**该建议已被本裁决覆盖**。
- 理由：供应商额度实际扣在 Key 上，账单必须归 Key owner；审计主体是用量来源，两套口径分开才不漂移。
- 影响：§5.2、§5.3、§5.6、§8 的 `ACCOUNT_KEY_USED`、§10 N6、`src/cost/cost_tracker.py:245` 字段设计。

### Q4. 删除子账号时，其下挂孙账号如何处理？

- **已裁决（2026-11-06）**：**非叶子账号不得直接删除**，必须先"移交"或"级联停用"；**删除仅限叶子**；非叶子删除返回**稳定错误码** `ACCOUNT_DELETE_HAS_CHILDREN`。
- **待拍板（历史）**："级联物理删除"是否允许？原建议为默认禁止，级联删除需主账号 + 二次确认。
- **原建议默认（历史）**：默认禁止删除有下级的账号，必须先移交或级联停用；级联物理删除需主账号 + 二次确认。**本裁决进一步收紧：任何级联删除都不做，只做级联停用**。
- 理由：避免一次误操作抹掉整棵子树；与口径 2"不越级"一致（删除者只能处置自己的直接下级）。
- 影响：§2.1、§5.5 第 2 条、成员管理页的删除流程、恢复入口、§10 D12/D13。

### Q5. 子账号能否看到上级的技能（内容级）？

- **已裁决（2026-11-06）**：子账号**不可见**上级技能（内容级）；**主账号可读子树产物/模板**（只读或复制，不改变归属）。
- **待拍板（历史）**：子账号是否可见上级技能？原建议为默认不可见。
- **原建议默认（历史）**：子账号默认不能；仅显式 `grant` 或 `visible_to_descendants=true` 时可见；主账号可读子树产物。
- 理由：技能常含提示模板、私有参数、业务口径，属于产物级资产；口径 4 的"主账号拥有"指授权聚合与复用权，不等于默认向下无差别开放。
- 影响：§3.8、§7.2、§7.3 判据、技能页的"可见范围"选项、`SKILL_VISIBILITY_DENIED`。

---

## 12. 附录：现状事实与差距（代码实测）

| 事实 | 位置 | 对本规格的影响 |
|---|---|---|
| 三套访问控制并存，实测零交叉 | `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md:11-40` | 账户授权必须新增一层，不能寄望任一现有权威自动覆盖 |
| `require_human_principal` 只认证、不授权 | `src/gateway/policy.py:158-171` | 可作为账户上下文的基础依赖，语义不变 |
| `require_admin_principal` 是**全局**管理员 | `src/gateway/policy.py:148-187` | 子账号带 admin 角色即可动全站 → 必须改相对判定 |
| `_effective_scope` 只认 owner/admin，其余 L7 | `src/gateway/auth.py:467-487` | 无法表达多层级 → 必须按 depth 派生 |
| `_enforce_principal_scope` 只是 clearance 上限 | `src/kernels/security/__init__.py:238-253` | 不能隔离同层兄弟 → 必须叠加路径包含 |
| `human_identities` 无 parent/path/depth | `alembic/006:157-169` | 需迁移与新增列 |
| `memory_entries` 无 owner；`key_hash` 只哈希 key | `alembic/006:127-143`、`src/kernels/memory/store.py:65` | 同名 key 跨账号互踩；必须改哈希与加列 |
| `ai_model_configs` 无 owner | `alembic/006:170-185`、`src/ai/model_config.py:349` | Key 归属规则无法落地 |
| `APIKey` 无 owner | `src/security/api_keys.py:67-96` | 平台 Key 无法绑定账号 |
| 会话按 principal 隔离（复合键） | `src/gateway/chat.py:73-88`、`conversation_turns.principal_id` | 已具备基础，只需补"根账号只读"接口 |
| 审计有 `principal_id + scope`，L0 可跨主体读 | `src/gateway/audit.py:91-100,105` | 是最接近账户模型的存量表，可承载 §8 字段 |
| JWT 吊销预检已存在（fail-closed） | `src/security/jwt_handler.py:576-601`（`_revocations.contains(jti)`）；吊销表 `jwt_handler.py:162-205` | 可直接支撑"降权/停用下一次请求即拒"（定稿 18），无需新增机制 |
| 向量库单集合全局、`user_id` 未强制 | `src/knowledge/memory.py:175-201`（`collection_name="liuhao_memories"`） | 检索必须强制 `account_id` 过滤，否则串数据 |
| 文件工作区全局单根 | `src/gateway/files.py:45-57` | 横向隔离无法成立 |
| 身份列表仅要求"是 human" | `src/gateway/identity.py:261-322` | 任何人类可列全站主体，属现状缺陷 |
| 费用记录无 principal/payer | `src/cost/cost_tracker.py:245` | 无法按账号结算 |
| AI 员工单文件按 name 索引 | `src/ai/employee_store.py:41,67` | 同名跨账号会覆盖 |
| 能力注册无 owner/grant | `src/kernels/capability/__init__.py:116`、`capability-registry.yaml:42+` | 技能继承无法判定 |
| 登录页已有 `login` / `register` 页签与 `SelfRegisterForm`，但入口仍绑"无根" | `Login.tsx:56-63,147-149,255-282,395-405` | 有根时"创建账户"不可达；需改为读 `self_registration.enabled`（§9.2 S1/S1c） |
| 自助注册前端请求体与后端契约不一致 | `apps/console/console/src/lib/auth.ts:330-341`（发 `secret`） vs `src/gateway/accounts.py:105-115`（收 `password`） | 现状注册会 422；**以本文 §9.4 的字段 `password` 为准**，前端改名属实现，不在本轮 |
| 自助注册开关后端已实现、持久化 | `src/gateway/accounts.py:229-257`（`GET/PATCH /v1/accounts/self-registration`，仅根）、`src/gateway/account_settings.py:20-75`（`data/account_settings.json`，默认 `true`） | 开关能力已具备；**控制台尚无设置项 UI**（§9.8 待新增） |
| 自助注册建号已实现，落点固定 | `src/gateway/accounts.py:199-222`、`src/kernels/identity/__init__.py:1604-1666` | `parent=根`、`depth=1`、`source=self_service`、`activation_hash=""`；无根时 409 `ACCOUNT_SELF_REGISTER_UNAVAILABLE`；重名 409 `ACCOUNT_EXISTS`；请求体父子/深度字段 `extra=ignore` |
| 关闭开关的错误码与文案 | `src/gateway/accounts.py:205-210`（`403 ACCOUNT_SELF_REGISTER_DISABLED`，文案"管理员已关闭自助注册"） | 状态码/错误码与本文一致；**文案非中性**，按 §4.3/§9.5 应为"当前无法创建账户。"（以本文为准） |
| 自助注册默认作用域 | `src/kernels/identity/_accounts.py:72-78`（`read/audit_read/usage_read/model_use/model_manage`） | 与 §3.12 一致（无 `manage_children`/`grant`/`key_borrow`）；`model:manage` 仅限自有 Key |
| 已有账号树/备份雏形 | `src/gateway/account_context.py`、`src/kernels/identity/_accounts.py`、`src/kernels/identity/_backup.py` | 账号面路由已接线（`accounts.py`）；**其余业务面尚未接 `require_account_principal`**，未接线处仍非安全边界 |
| 账号错误码已细化 | `src/kernels/identity/_accounts.py:121-158` | §4.3 的码已在核心里定义；网关仍需保证**文案中性**（现状个别文案含"管理员"） |
| `openapi.yaml` 无账户域路径 | 实测 `/v1/accounts|users|members|keys|tenants` 均无 | 契约层需新增 |

---

## 13. 明确不做的事（范围护栏）

- 不新增第二套登录页、第二套客户端、第二套主题。
- 不把层级实现成"另一套租户系统"或推翻现有 RBAC/ABAC。
- 不引入新的 scope 刻度（L0–L7 已够用）。
- 不提供 Key 明文导出接口。
- 不允许"只读查看"执行写操作，也不允许把它当成"身份切换"。
- **不做多根/森林**；**不做跨设备实时同步**（待决策，不写进任何承诺）。
- **不把"邀请码 / 上级批准"恢复成成员注册的前置条件**；成员默认自助注册，激活码只是保留的第二条路径。
- **不做"自助注册者可选上级/可选角色/可选深度"**；这些字段即使出现在请求体也不参与建号。
- **不在登录 / 注册页出现组织与层级词**（§0-22、§9.9）。
- **不承诺"子账号完全不知道 Key 被使用"**（只承诺界面不暴露调用细节）。
- 不修改任何代码、不迁移任何数据库、不提交、不 push（本轮只交付本文档）。
