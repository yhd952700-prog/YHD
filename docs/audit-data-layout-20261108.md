# 数据目录只读审计（2026-11-08）

> 审计对象：安装版 `D:\LiuHao` 与其实际使用的数据目录。
> 审计方式：只读。未修改 `src/`、`apps/`、`installer/`、`packaging/`，未删除、未移动、未重命名任何真实数据；SQLite 一律以 `mode=ro` 打开。
> 标注约定：**实测** = 本机文件系统/只读 SQLite 直接观测；**代码** = 读实现得出的确定行为；**推断** = 依据代码路径推测、未做破坏性复现。

---

## 0. 结论摘要

1. **实际数据目录不是 `D:\LiuHaoData`**。本机 `D:\LiuHao\data-dir.txt` 记录的是 `C:\Users\Administrator\AppData\Local\LiuHao\data`；`D:\LiuHaoData` 只是一个仅含 3 个配置文件的空壳（282 B `app.env` + 65 B `integrity.key` + 4.2 KB `logging.yaml`，**无账号、无数据库、无工作区文件**）。若按文档里"默认 D:\LiuHaoData"去备份，会备份到一个几乎没有业务数据的目录。
2. 活跃数据目录共 **70 个文件 / 13.8 MB**，其中 **10.9 MB 是日志**；真正不可重建的状态约 1.5 MB。
3. 有 **6 个密钥/身份类文件族**绝不能外传：`config/app.env`、`config/auth_secrets.json`、`config/human_identities.json`、`config/integrity.key`、`data/model_config.key`、`data/model_registry.sqlite3`（密文字段）。
4. **UI「备份与迁移」的实际包内容与文档/界面文案不一致**：文档写"仅导出 Key 密文"，实现把主账号 API Key 的**明文**放进了备份包（外层再由口令 AES-GCM 加密）；文档写 `auth_secrets.json` 永不打包，实现把凭据记录（密码哈希+盐）放进了包里。
5. **自动升级备份把密钥打进了备份**：排除名单里写的是 `model_config_key.bin`，真实文件名是 `model_config.key`，因此升级备份里实际含有模型主密钥、`integrity.key` 与身份库；文档声称"打包后断言目标目录不含这些文件"，代码里没有这个断言。
6. **CLI `backup create` 写出的是明文归档**，默认包含整个数据目录（含上述全部密钥文件）；`--encrypt` 直接抛 `NotImplementedError`。同时 `--source config/memory/knowledge` 三个默认路径全部指错。
7. **自愈能力有明显缺口**：`data-dir.txt` 损坏会静默回退到默认目录（无任何提示）；`config/` 部分损坏不会补齐；`integrity.key` 内容损坏不做格式校验、直接当新密钥用，导致所有身份校验失败但只在登录时表现为"登不上"；`model_config.key` 丢失会静默生成新密钥，旧 API Key 全部无法解密。
8. 活跃数据目录之外，`%LOCALAPPDATA%\LiuHao` 下还有 **6 份 smoke/备份测试残留目录**，其中 **4 份同时含凭据库、身份库、模型主密钥**，全部未加密地躺在磁盘上。

---

## 1. 数据目录判定（先搞清"哪个才是真的"）

### 1.1 判定链

| 顺序 | 位置 | 实测值 | 说明 |
|---|---|---|---|
| 1 | 程序目录标记 `D:\LiuHao\data-dir.txt` | `C:\Users\Administrator\AppData\Local\LiuHao\data`（无 BOM，48 B） | 安装器写入；启动器优先读它 |
| 2 | 环境变量 `LIUHAO_DATA_DIR` | 当前审计进程未设置 | 安装版启动器会给子进程注入 |
| 3 | 默认值 | `%LOCALAPPDATA%\LiuHao\data` | 标记缺失/损坏时的回退目标 |

结论：**活跃数据目录 = `C:\Users\Administrator\AppData\Local\LiuHao\data`**。
`D:\LiuHaoData` 是历史/测试遗留的孤儿目录，见 §3。

### 1.2 为什么会有两个目录

- 安装器默认首选 `D:\LiuHaoData`，D 盘不可写/不可用时回退 `%LOCALAPPDATA%\LiuHao\data`（[liuhao.iss](../installer/liuhao.iss) 的数据目录解析段）。
- 本机 `D:\LiuHaoData` 的文件时间戳集中在 `2026-11-07 07:57`，只有布局骨架；之后真正跑验收的数据落在了 `%LOCALAPPDATA%`（活跃目录内可见 `upgrade-state.baseline*`、`workspace\FINAL-*.md` 等测试残留）。
- 这意味着**同一台机器上存在两套互相独立、互不兼容的密钥**：两边的 `app.env` / `integrity.key` 哈希均不同。指向哪套，哪套生效；另一套不会自动合并。

---

## 2. 活跃数据目录实盘清单

根：`C:\Users\Administrator\AppData\Local\LiuHao\data`（70 文件 / 13.8 MB）。
"含密钥"一列区分**明文密钥**、**哈希/密文**与**否**；"备份"指是否需要进入用户备份；"可删"指删除是否造成不可逆损失。

| 路径（相对数据根） | 用途 | 含密钥 | 备份 | 可删 |
|---|---|---|---|---|
| `.liuhao-startup-probe-*`（2 个，各 2 B） | 启动前端口/目录可写探测的临时探针 | 否 | 不需要 | 可（临时文件） |
| `config\app.env`（282 B） | `APP_ENV=staging`、`LOG_LEVEL`、`LIUHAO_JWT_SECRET`、`SECRET_KEY` | **是（明文）** | **必须** | **否**（删=全员登出且多进程互不认账） |
| `config\auth_secrets.json`（642 B） | 本地登录凭据库：PBKDF2 密码哈希 + 盐 + 参数；当前 3 个 principal | 是（哈希，非明文密码） | **必须** | **否**（删=无人能登录） |
| `config\human_identities.json`（6.4 KB） | 身份库：5 个人类身份 + 4 个账号节点（父/深度/根/状态）、`recovery_hash`、`activation_hash`、完整性标签 | 否（含恢复码哈希/身份树） | **必须** | **否**（删=账号树与恢复码哈希不可重建） |
| `config\integrity.key`（65 B） | 身份登记表行级 HMAC 签名密钥 | **是（明文签名密钥）** | **必须** | **否**（删/改=所有身份拒绝登录） |
| `config\logging.yaml`（3.5 KB） | 日志级别、轮转、文件路径（含本机绝对路径） | 否（含路径信息） | 不需要 | 可（缺失会自动重建） |
| `config\current_url.txt`（81 B） | 最近一次启动写入的 `url/port/pid/started_at` | 否 | 不需要 | 可（下次启动重写；但见 §6 陈旧问题） |
| `audit_store.db` + `-wal` + `-shm`（483 KB / 1.0 MB / 32 KB） | 审计哈希链：447 条事件、链状态、写者租约、校验点、30 天/50 万条保留策略 | 否（含操作者名/动作/目标） | 建议（合规证据） | 否（删=审计链断裂且不可恢复） |
| `conversation_store.db` + `-shm` + `-wal`（16 KB / 32 KB / 0 B） | 会话轮次（当前 8 条） | 否（含对话正文，敏感） | **必须** | 否 |
| `memory_store.db` + `-shm` + `-wal`（32 KB / 32 KB / 0 B） | 内核长期记忆（11 条） | 否（含记忆正文） | **必须** | 否 |
| `personal_context.db` + `-shm` + `-wal`（44 KB / 32 KB / 0 B） | 个人画像：偏好/事实/清单/关系（当前全空） | 否（个人资料） | **必须** | 否（一旦有数据不可重建） |
| `liuhao_jwt_revocation.db`（16 KB） | 已撤销 JWT 清单（当前 0 条） | 否 | 可选 | 可删但**不建议**：已撤销令牌会在有效期内重新可用 |
| `data\knowledge_vectors.sqlite3` + `-wal` + `-shm`（4 KB / 48 KB / 32 KB） | 知识库向量、文档哈希、FTS 索引（当前 0 文档） | 否 | 建议（否则需重新入库） | 否（有文档时等于丢知识库） |
| `data\model_config.key`（33 B） | 模型 API Key 的 AES 主密钥（32 字节文本 + 换行） | **是（明文主密钥）** | **必须（与 registry 成对）** | **否**（删=旧 API Key 永久解不开） |
| `data\model_registry.sqlite3` + `-shm` + `-wal`（16 KB / 32 KB / 0 B） | 模型配置（1 行本地模型；含 `api_key_enc` 密文字段，当前为空） | 是（密文；当前无 Key） | **必须（与 key 成对）** | 否 |
| `data\observability\alerts.json`（11.7 KB） | 告警状态/规则/哈希链（当前无活动告警） | 否 | 可选 | 可（可重建） |
| `logs\liuhao.log`、`liuhao.log.1`、`error.log`、`launcher.log`、`*.lock`（3.0 MB / 10.5 MB / 37 KB / 10 KB） | 运行/错误/启动器日志（10 MB 轮转） | 否（可能含提示词、标识符，属内部资料） | 不需要 | 可（丢排障线索） |
| `run\instance.json`（142 B） | 网关最近发布的地址元数据（pid/port/url/时间） | 否 | 不需要 | 可（停机时；见 §6） |
| `run\instance.lock`（1 B） | 单实例文件锁 | 否 | 不需要 | 仅在进程停止后可删；运行中删会破坏单实例保护 |
| `upgrade-state\status.json`、`last-check.json`、`upgrade-ledger.db(+wal/shm)` | 当前升级状态与升级台账（1 条记录） | 否 | 可选 | 可（丢升级历史） |
| `upgrade-state.baseline2\`、`upgrade-state.baseline3\`、`upgrade-state.cli-baseline\`（各 4 文件） | 验收测试留下的状态目录副本 | 否 | 不需要 | 可（测试残留） |
| `workspace\account_memory.sqlite3`（40 KB） | 账号记忆（11 条）及代管/迁移表 | 否（含记忆正文） | **必须** | 否 |
| `workspace\account_usage.sqlite3`（12 KB） | 账号用量事件（4 条，配额依据） | 否 | 建议 | 可（配额统计归零） |
| `workspace\liuhao_artifacts.db` + `-wal` + `-shm`（4 KB / 193 KB / 32 KB） | 产物索引（5 次写入；**实际数据几乎全在 WAL 里**） | 否 | 建议 | 可（索引可从文件重建）但**必须与 WAL 一起处理** |
| `workspace\liuhao_employees.json`（4.3 KB） | AI 员工实体（agent 池/任务/计数器） | 否 | **必须** | 否 |
| `workspace\liuhao_execution.journal` + `-wal` + `-shm`（4 KB / 511 KB / 32 KB） | 执行日志（37 条；**数据几乎全在 WAL 里**） | 否 | 建议 | 可（但丢执行回溯） |
| `workspace\liuhao_goals.json`（56 KB）+ `.lock` | Goal 定义/进度 | 否 | **必须** | 否 |
| `workspace\FINAL-COUNT-72FD.md` 等 5 个 `FINAL-*` | 验收测试写入的产物样本 | 否 | 不需要 | 可（测试残留） |
| `plugins\`（空目录） | 插件目录预留（实现中插件注册表另指一处，见 §7） | 否 | 不需要 | 可（空） |
| `upgrade-backups\`（空目录） | 自动升级备份落点（首次升级后才会出现内容） | 出现内容时**是**（整目录快照） | 建议 | 可删旧升级点（不要外发） |

> WAL 说明：`liuhao_artifacts.db` / `liuhao_execution.journal` / `audit_store.db` 的最新数据主要在 `-wal`（甚至比主库大两个数量级）。**只复制 `.db`、不带 `-wal` 会丢最近数据**；停止进程触发 checkpoint 后再复制，或用整目录复制，才是安全的。

### 2.1 数据目录之外、但属于同一套状态的路径

| 路径 | 用途 | 含密钥 | 备份 | 可删 |
|---|---|---|---|---|
| `D:\LiuHao\data-dir.txt` | 数据目录指针 | 否 | 建议留（48 B） | 否（丢失会静默回退到默认目录） |
| `D:\LiuHao\config\*` | 安装包模板配置（default/logging 等） | 否 | 不需要 | 升级会重装；缺失只影响默认模板 |
| `%LOCALAPPDATA%\LiuHao\logs\bootstrap.log` | 启动器引导日志 | 否（含路径） | 不需要 | 可 |
| `%LOCALAPPDATA%\LiuHao\diagnostics\*.zip` | 一键诊断包（端口探测、脱敏日志、依赖清单） | 否（白名单 + 脱敏） | 不需要 | 可（可重新生成） |
| `%LOCALAPPDATA%\LiuHao\data.after-smoke-*`、`data.bak-*`（6 份） | 验收/冒烟测试的数据目录副本 | **4 份含凭据/身份/主密钥** | 不需要 | 可（测试残留，但见 §5） |

`ports.json` 说明：**运行期不存在 `ports.json`**。它只是一键诊断 zip 里的一个成员（记录 8080/5173/8443 的占用情况，226 B），`python -m src.desktop diagnose` 每次重新生成，删掉毫无影响。运行期真正的"端口记录"是 `config\current_url.txt` 与 `run\instance.json`。

---

## 3. `D:\LiuHaoData`（孤儿目录）清单

| 路径 | 大小 | 用途 | 含密钥 | 可删 |
|---|---|---|---|---|
| `D:\LiuHaoData\config\app.env` | 282 B | 另一套 JWT/SECRET | 是（明文） | 可（该目录无账号） |
| `D:\LiuHaoData\config\integrity.key` | 65 B | 另一套身份签名密钥 | 是（明文） | 可 |
| `D:\LiuHaoData\config\logging.yaml` | 4.2 KB | 日志模板副本 | 否 | 可 |
| `D:\LiuHaoData\data\observability\`、`logs\`、`workspace\` | 空目录 | 布局骨架 | 否 | 可 |

**判定**：该目录没有 `human_identities.json`、`auth_secrets.json`、任何 `.db`、任何工作区文件，**没有任何业务数据可丢**。它与活跃目录使用不同的密钥；如果误把 `data-dir.txt` 改指回 `D:\LiuHaoData`，系统会像一个"全新空实例"启动，而活跃账号仍在 `%LOCALAPPDATA%`，表现为"数据全没了"（实际没删）。删除前请确认没有第二套正在使用的实例。

---

## 4. 三类归属：必须备份 / 可重建 / 绝对不能外传

### 4.1 必须备份（删了不可重建）

**第一优先级（身份与密钥，缺一个就登不上/解不开）**
- `config\app.env`（JWT/SECRET）
- `config\auth_secrets.json`（登录凭据）
- `config\human_identities.json`（身份库 + 账号树 + 恢复码/激活码哈希）
- `config\integrity.key`（身份签名密钥）
- `data\model_config.key` + `data\model_registry.sqlite3`（必须成对）

**第二优先级（用户内容）**
- `conversation_store.db`（+ WAL）
- `memory_store.db`
- `personal_context.db`
- `workspace\account_memory.sqlite3`
- `workspace\liuhao_employees.json`
- `workspace\liuhao_goals.json`
- `workspace\liuhao_artifacts.db`（+ WAL，或连同工作区文件一起）
- `workspace\liuhao_execution.journal`（+ WAL）
- `workspace\account_usage.sqlite3`（配额历史）
- `data\knowledge_vectors.sqlite3`（有知识库时）

**第三优先级（安全/合规证据，按需）**
- `audit_store.db`（+ WAL）
- `upgrade-state\`、`upgrade-backups\`（回溯升级点）

### 4.2 可重建 / 可丢弃

- `logs\*`：重新运行即产生（但排障线索不可复原）。
- `run\instance.json`、`instance.lock`、`.liuhao-startup-probe-*`、`logs\*.lock`：运行期产物。
- `config\current_url.txt`：下次启动重写。
- `config\logging.yaml`：启动器会从程序模板重建。
- `data\observability\alerts.json`：告警状态可重建。
- `plugins\`：空目录；插件可重装。
- `upgrade-state.baseline*`、`workspace\FINAL-*`：验收测试残留。
- `workspace\liuhao_artifacts.db`：索引可由工作区文件重建（`liuhao_execution.journal` 的执行历史不能重建）。
- `knowledge_vectors.sqlite3`：**仅在原始文档仍保留时可重建**；原始文档丢了就不可重建。

### 4.3 绝对不能外传

| 文件 | 原因 |
|---|---|
| `config\app.env` | JWT/SECRET 明文，拿到即可伪造会话 |
| `config\auth_secrets.json` | 密码哈希+盐，可离线爆破 |
| `config\integrity.key` | 身份签名密钥，可伪造/重签身份记录 |
| `config\human_identities.json` | 身份库、账号树、恢复码/激活码哈希（属"身份库"，不是普通配置） |
| `data\model_config.key` | 模型 API Key 主密钥 |
| `data\model_registry.sqlite3` | 含 `api_key_enc` 密文（单独泄露不足以解密，但属凭据派生数据） |
| `config\recovery_code.txt`（本机未生成） | 一旦生成即为恢复码明文/DPAPI 副本；本机不存在，但出现时按最高密级处理 |
| `*.lhbak` 备份包 | 外层 AES-GCM，但内部含明文 API Key（见 §5），口令即全部安全性 |
| `%LOCALAPPDATA%\LiuHao\data.*` 测试副本 | 4 份含凭据/身份/主密钥，未加密 |
| `upgrade-backups\` 内容 | 整目录快照，含 `model_config.key`、`integrity.key`、身份库 |

可以外发：`diagnostics\*.zip`（白名单成员、脱敏、无密钥/无 DB 原文/无 `.env` 原文；实测成员为 about/environment/ports/providers/service/logs 尾部）。

---

## 5. 备份/导出范围 vs 实际文件清单

### 5.1 四条备份路径的真实行为

| 方式 | 入口 | 实际包含 | 实际不含 | 加密 | 与文档是否一致 |
|---|---|---|---|---|---|
| A. 手动整目录复制 | 文档推荐；用户自己操作 | 整个数据目录（含全部密钥、WAL、日志、测试残留） | —— | 无（依赖用户介质） | 基本一致；但"默认 D:\LiuHaoData"在本机指向错目录 |
| B. UI「备份与迁移」`*.lhbak` | 根账号导出 | 身份+账号树、凭据记录、模型行 + **主账号 API Key 明文**、员工/任务、Goal、知识库向量、4 类记忆、会话、工作区文件 | `audit_store`、`app.env`、`integrity.key`、`model_config.key`、JWT 撤销表、升级状态、日志、插件；成员 Key 与成员私有文件 | 口令 AES-256-GCM（PBKDF2 200k） | **不一致**：文案说"Key 密文"，实际是包内明文；文档说凭据/身份库永不打包，实际打包了哈希记录 |
| C. CLI `backup create` | `src/cli` | 默认全量：整个数据目录（含 `app.env`/`auth_secrets`/`integrity.key`/`model_config.key`/日志），**gzip+base64 明文 JSON** | —— | `--encrypt` 直接抛未实现 | **不一致/高风险**：文档把它当"真实可用备份"，未提示明文含密钥 |
| D. 自动升级备份 | 升级流程 | 整个数据目录，排除 `auth_secrets.json` 与 `recovery_code*/recovery_handoff*` | 仅上述 5 类名字 | 无 | **不一致**：实际仍含 `model_config.key`、`integrity.key`、`human_identities.json`；文档称"断言不含" |

### 5.2 UI 备份包逐项核对（`src/gateway/account_backup.py`）

`payload` 实际键（[account_backup.py:742-752](../src/gateway/account_backup.py#L742-L752)）：
`identity / credentials / models{rows,key_material,key_vault} / files / employees / goals / knowledge / memory / conversations / kernel_memory / personal_context`。

- ✅ 已包含：账号树、员工与任务、Goal、知识库、四类记忆、会话、工作区文件（根导出）。
- ❌ 未包含：`audit_store`（审计链）、`app.env`、`integrity.key`、模型主密钥文件、JWT 撤销表、升级状态、日志、插件。
- ✅ Key 形态：`models.key_vault` 是「随机 DEK 加密 Key 清单 + 口令派生 KEK 包裹 DEK」的双层密文，包内**没有明文 API Key**；`key_material=None`（主密钥不在包内）。导入时用同一口令解封、再用目标机主密钥重新加密，因此原 Key 可继续用。
- ⚠️ 凭据：`credentials` 是 `auth_secrets.json` 的**内容**（哈希+盐），不是原文件；导入后登录可用。
- ⚠️ 成员自助导出：只导出"产物索引里有归属"的文件；**没有索引的手工文件会被静默跳过**（`_collect_files` 的 `if not owners ... continue`）。
- ⚠️ 根导出的"工作区文件"会原样带上 `account_usage.sqlite3`、`liuhao_artifacts.db(+wal)`、`liuhao_execution.journal(+wal)` 等内部 SQLite（它们不在排除集），与结构化恢复重复，且存在把撕裂 WAL 一起搬走的风险。
- ⚠️ "任务/设置"：任务只以"员工实体内部字段"存在，没有独立任务库；"设置"没有独立的设置对象进入包内。UI 文案 [Backup.tsx:83,120](../apps/console/console/src/pages/Backup.tsx#L83-L120) 属于过度承诺。
- ⚠️ 导入限制：只有"本机没有任何根账号"时才能导入（[identity/__init__.py:2213-2233](../src/kernels/identity/__init__.py#L2213-L2233)）；已有账号的机器不能"覆盖恢复"，只能手工整目录回滚。

### 5.3 自动升级备份（`src/upgrade/backups.py`）

排除集（[backups.py:40-58](../src/upgrade/backups.py#L40-L58)）：
`recovery_code*`、`recovery_handoff*`、`auth_secrets.json`、`model_config_key.bin`。

- `model_config_key.bin` 是**过期文件名**；实际是 `model_config.key` → 主密钥被复制进 `upgrade-backups\`。
- `integrity.key`、`human_identities.json` 不在排除集 → 身份库与签名密钥也被复制。
- 文档 [account-hierarchy.md:1007](account-hierarchy.md#L1007) 声称"打包后断言目标目录不含这些文件"；`verify_backup()` 只校验清单/哈希，没有保护文件断言。
- 回滚 `restore_data()` 只覆盖清单内文件，不会删除升级后新增文件；被排除的 `auth_secrets.json` 保持原样，因此"回滚后登不上"不成立——但"备份里含主密钥"成立。

### 5.4 CLI 备份的硬缺陷

[backup_commands.py:135-138](../src/cli/backup_commands.py#L135-L138)：

| 声明的 source | 实际路径 | 结果 |
|---|---|---|
| `config` | `<data>\..\config`（即 `%LOCALAPPDATA%\LiuHao\config`） | 正确位置是 `<data>\config`，**指错** |
| `memory` | `<data>\memory` | 不存在（真实是 `<data>\memory_store.db`） |
| `knowledge` | `<data>\knowledge` | 不存在（真实是 `<data>\data\knowledge_vectors.sqlite3`） |
| `user_data` | `<data>` | 正确，但**包含全部密钥与日志** |

归档默认落点 `<data>\..\backups`（[backup_commands.py:99](../src/cli/backup_commands.py#L99)）；归档本身是 gzip 后的 JSON，文件内容 base64 —— **base64 不是加密**。`--encrypt` 抛 `NotImplementedError`（[backup_commands.py:213-216](../src/cli/backup_commands.py#L213-L216)）。

### 5.5 "该备份的没备份"清单（按影响排序）

1. UI 包不含 `audit_store.db`：换机后审计链从零开始，旧证据只剩原机（若原机丢失即永久丢失）。
2. UI 包不含 `app.env`/`integrity.key`：换机后所有旧会话失效（可接受），但身份库依赖新签名密钥重新签发（导入实现会重新 `create_identity`，因此可用）。
3. UI 包不含 `upgrade-state`/`jwt_revocation`/`plugins`：可接受，但文档"全量"口径不成立。
4. 成员自助导出跳过无产物索引的文件：成员手工放入 `workspace` 的文件会丢。
5. 升级备份"该包含的"其实包含了（但包含了不该包含的主密钥）——问题在越界，不在缺失。

### 5.6 UI 包内密钥的真实暴露面（需要明确告知用户）

- 主账号自己的 API Key **明文**在包内；口令是唯一屏障，文档只"建议 ≥12 位"，代码最低 8 位。
- 成员 Key 与主密钥不在包内（`member_keys_included=false`、`key_material_included=false`），这一点实现与回执一致。
- 结论：**这个包不能当"密文包"随意转发**；它等价于"用口令加密的明文凭据包"。

---

## 6. 文档与实际不一致清单

> 级别：P0 = 会导致用户备份错数据；P1 = 安全/恢复语义错误；P2 = 行为描述不准；P3 = 清单不完整/过时。

| # | 级别 | 文档位置 | 文档说法 | 实际 | 证据 |
|---|---|---|---|---|---|
| D1 | **P0** | [END-USER-GUIDE.md:321,331](END-USER-GUIDE.md#L321) | "用户数据默认落在 `D:\LiuHaoData`…所有用户数据都在…" | 本机实际全部在 `%LOCALAPPDATA%\LiuHao\data`；`D:\LiuHaoData` 只有 3 个配置文件、无账号无库 | 实测 §1、§3 |
| D2 | P1 | [account-hierarchy.md:1006-1007](account-hierarchy.md#L1006-L1007) | 只导出"Key（密文）"；`auth_secrets.json` 永不打包；升级备份"断言"不含密钥文件 | 包内含 API Key 明文（口令加密）；包内含凭据哈希记录；升级备份含 `model_config.key`/`integrity.key`/身份库，无断言 | [account_backup.py:742-752](../src/gateway/account_backup.py#L742-L752)、[backups.py:40-58](../src/upgrade/backups.py#L40-L58) |
| D3 | P1 | [account-hierarchy.md:1007](account-hierarchy.md#L1007) | 密钥文件名 `model_config_key.bin` | 实际文件名 `data/model_config.key`；排除逻辑因此失效 | [model_config.py](../src/ai/model_config.py) 实际路径 |
| D4 | P1 | [faq.md:86](knowledge_base/faq/faq.md#L86) / [known-gaps.md:306-312](known-gaps.md#L306-L312) | 推荐 `liuhao backup create`，称"CLI 真了" | 归档是明文 gzip；默认含全部密钥；`--encrypt` 未实现；`config/memory/knowledge` 源路径错误 | [backup_commands.py:99,135-138,213-216](../src/cli/backup_commands.py#L99-L216) |
| D5 | P1 | [Backup.tsx:83,120](../apps/console/console/src/pages/Backup.tsx#L83-L120) / [account-hierarchy.md:586](account-hierarchy.md#L586) | "整棵树 = 成员身份、设置、Key 密文与全部成员数据" | 无独立"设置"对象；Key 是包内明文；审计/升级状态/撤销表不在包内 | §5.2 |
| D6 | P2 | [END-USER-GUIDE.md:339-340](END-USER-GUIDE.md#L339-L340) | `current_url.txt` 记录"本次实际访问地址、pid" | 文件在网关绑定前写入、之后不更新；本机实测 `port=8080 pid=22708`，而 8080 的实际监听进程是 26464；`instance.json` 又写 8090/18624，三者互不一致 | 实测 §6.1 |
| D7 | P2 | [WINDOWS-DESKTOP.md:286-291](operations/WINDOWS-DESKTOP.md#L286-L291) | 诊断包默认输出到 `<数据目录>\diagnostics\...`，默认跟随 `D:\LiuHaoData` | 实际固定为 `%LOCALAPPDATA%\LiuHao\diagnostics`；`diagnose` 用 `resolve_data_dir()/env`，**不读 `data-dir.txt`**；实测 zip 内 `data_dir` 显示的是仓库开发路径 | 实测 zip、[desktop/__init__.py:31-39](../src/desktop/__init__.py#L31-L39) |
| D8 | P2 | [USER-GUIDE.md:59-73](USER-GUIDE.md#L59-L73) | 只列出 `app.env/auth_secrets/human_identities/integrity.key/model_config.key/registry` 等 | 还缺 `memory_store.db`、`personal_context.db`、`liuhao_jwt_revocation.db`、`knowledge_vectors.sqlite3`、`workspace/account_*.sqlite3`、`liuhao_execution.journal`、`upgrade-state`、WAL/SHM | 实测 §2 |
| D9 | P2 | [END-USER-GUIDE.md:320,325](END-USER-GUIDE.md#L320-L325) | "程序目录里没有数据库/日志/app.env；首启后程序目录新增目录 0" | 安装目录当前确实没有，但桌面模块把 `DATA_DIR/LOG_DIR` 固定成程序目录下的 `data/logs`（[desktop/__init__.py:25-26](../src/desktop/__init__.py#L25-L26)），走托盘/桌面入口时会在程序目录建 `logs` 并写 `desktop.log`，且"数据目录"显示为 `D:\LiuHao\data`（不存在） | 代码；本机未走桌面入口，故未触发 |
| D10 | P3 | [known-gaps.md:306-316](known-gaps.md#L306-L316) | 没有设置页入口、没有恢复流程 | 已有「备份与迁移」页与 `.lhbak` 导入（限制为全新实例） | [Backup.tsx](../apps/console/console/src/pages/Backup.tsx) |
| D11 | P3 | [account-hierarchy.md:1001](account-hierarchy.md#L1001) | 入口在"系统设置" | 实际是左侧导航独立页「备份与迁移」 | 同上 |
| D12 | P3 | [installer/liuhao.iss](../installer/liuhao.iss) 注释 | `data-dir.txt` 为 UTF-8 带 BOM | 实测该文件无 BOM（启动器按 `utf-8-sig` 读，两种都能处理） | 实测字节头 |

---

## 7. 自愈能力矩阵（配置类文件损坏/丢失）

> 结论先行：**能"继续启动"不等于"能自愈数据"**。下列多数场景的行为是"静默重建 + 旧状态失效"，属于最危险的一类自愈。

| 场景 | 实际行为 | 自愈？ | 后果 | 人工恢复 |
|---|---|---|---|---|
| `data-dir.txt` 丢失 | 静默回退默认目录（本机为 `%LOCALAPPDATA%\LiuHao\data`）；无提示、无对话框 | ❌ | 若真实数据在 D 盘，会以"空实例"启动；用户误以为丢数据，可能新建账号，形成双树 | 写回标记，或设 `LIUHAO_DATA_DIR` |
| `data-dir.txt` 内容损坏/为空/乱码 | 同上；非法路径在创建布局时抛错，启动器报错退出（推断） | ❌ | 可能新建到意外位置（如 `C:\`） | 同上 |
| `data-dir.txt` 指向另一个合法但空的目录 | 直接使用该目录，创建布局 | ❌（误自愈） | 旧数据被"旁置"，UI 显示空 | 恢复正确路径 |
| `ports.json` 丢失/损坏 | 不存在运行期文件；诊断 zip 每次重新生成 | ✅（不存在依赖） | 无影响 | —— |
| 整个 `config\` 目录被删 | 启动器重建目录；`app.env` 新密钥、`integrity.key` 新密钥、身份库空骨架、`logging.yaml` 从模板复制；`auth_secrets.json` **不重建** | ❌（表面自愈） | 老 DB/工作区仍在，但**无账号可登录**；身份树、恢复码哈希、凭据全丢 | 从备份恢复 4 个文件；缺一不可 |
| `config\app.env` 丢失 | 重新生成 JWT/SECRET | ✅（会话层面） | 全员登出；模型 Key 不受影响 | —— |
| `config\app.env` 部分损坏（缺 JWT 行） | 不补齐（`_ensure_app_env` 只要文件存在就返回）；JWT 退化为每进程临时密钥（代码注释明确"重启即全员登出、多 worker 互不认账"） | ❌ | 每次重启登出；多进程鉴权不一致；无 UI 提示 | 手工补 `LIUHAO_JWT_SECRET` 或删除整个文件让它重建（会换新密钥） |
| `config\integrity.key` 丢失 | 检测到已签名记录 → stderr/日志警告；生成新密钥；旧记录 fail-closed；未签名旧记录会被**自动补签**（升级迁移路径） | ❌（有提示） | 所有旧身份登录被拒；需恢复密钥或重新注册 | 恢复原 `integrity.key` 后重启 |
| `config\integrity.key` 内容损坏（非空任意文本） | `read_key_file` 只判断"非空"，直接当密钥使用；**无格式校验、无警告** | ❌（无提示） | 所有签名校验失败 → 全员登不上，只表现为登录失败 | 恢复原文件；否则重建身份库 |
| `config\human_identities.json` 丢失 | 重建空骨架 `{"humans":[]}`，启动继续 | ❌ | 账号树/角色/恢复码哈希/完整性标签全丢；`auth_secrets.json` 变成孤立哈希；旧数据无主 | 从备份恢复（需配对的 `integrity.key`） |
| `config\human_identities.json` 损坏 | 加载报错返回 `[]`，启动继续（fail-closed） | ❌ | 同上；不会自动备份或隔离坏文件 | 同上 |
| `config\auth_secrets.json` 丢失/损坏 | 凭据库按 `{}` 处理（fail-closed），记录错误；启动继续 | ❌ | 无人能登录；不会自动重建、不会重置 | 恢复文件；或用恢复码/重新注册流程（本机无恢复码文件） |
| `config\logging.yaml` 丢失 | 启动器从程序模板重建 | ✅ | 无 | —— |
| `config\logging.yaml` 损坏（非法 YAML/顶层非映射） | `load_uvicorn_log_config` 抛 `RuntimeError`，网关启动失败（fail-fast，而非降级） | ❌ | 服务起不来；错误提示指向配置路径 | 删除该文件让启动器重建，或修 YAML |
| `config\current_url.txt` 丢失/截断 | 下次启动重写；无消费者依赖 | ✅ | 无 | —— |
| `config\current_url.txt` 陈旧 | 只在启动早期写一次，网关绑定后不更新；失败启动也会留下旧文件 | ❌ | 文档/支持人员可能拿到错误端口或死 pid（实测 8080 文件 vs 8090 instance vs 实际 26464） | 以托盘/服务实际地址为准；或删文件重启 |
| `run\instance.json` 丢失/损坏 | 网关重新发布；读取方容错（桌面端本就不读该路径） | ✅（文件层面） | 第二实例拿不到地址提示 | —— |
| `run\instance.lock` 运行中被删 | 文件锁失效，单实例保护可能被绕过（推断） | ❌ | 可能启动第二个网关，端口/数据竞争 | 停止进程后删；不要运行中删 |
| `data\model_config.key` 丢失 | **静默生成新 32 字节密钥**（DACL 收紧）；旧 `api_key_enc` 全部无法解密 | ❌（危险自愈） | 模型调用失败；需重新录入 API Key；不会在启动阶段报警 | 从备份恢复 key + registry |
| `data\model_config.key` 损坏（长度≠32） | 当作"历史口令"派生密钥；解不开旧密文，且新写入用派生密钥 | ❌ | 同上，且更容易误判为"Key 失效" | 同上 |
| `data\model_registry.sqlite3` 损坏 | SQLite 打开失败；模型配置/调用报错；无自动重建 | ❌ | 配置与密文丢失 | 从备份恢复（配 key） |
| 任一业务 `.db` 损坏 | 对应模块打开失败；审计模块有降级（启动继续），其余多为接口 500；不会自动修复/隔离 | ❌ | 该域数据不可用 | 停服后用备份替换（连同 `-wal/-shm` 一起） |
| `workspace\liuhao_employees.json` / `goals.json` 损坏 | 加载失败，员工/Goal 列表可能为空或报错（推断） | ❌ | 员工/目标丢失 | 从备份恢复 |
| `upgrade-state\` 损坏 | 升级检查状态重置（推断） | 🟡 | 丢升级历史/可能重复检查 | 可接受 |

### 7.1 自愈缺口汇总（按风险）

1. **指针类静默回退**：`data-dir.txt` 损坏没有用户可见警告，直接换目录（P1）。
2. **密钥类无格式校验**：`integrity.key` 任何非空文本都被接受；损坏与"正确"无法区分（P1）。
3. **主密钥静默换新**：`model_config.key` 丢失自动生成，旧密文永久不可解（P1）。
4. **配置只"整文件重建"、不"逐键修复"**：`app.env` 少一行就退化为临时 JWT（P2）。
5. **身份/凭据丢失后无引导式恢复**：只会 fail-closed，用户只看到"登录失败"（P2）。
6. **日志配置损坏是 fail-fast**，没有隔离坏文件并回退默认的路径（P2）。
7. **运行期地址文件无 liveness 校验**：`current_url.txt`/`instance.json` 可能是死 pid/旧端口（P2）。
8. **桌面层路径常量与安装布局不一致**：`DATA_DIR/LOG_DIR` 指向程序目录（P2，见 D9）。
9. **数据库无损坏检测/隔离**：只有诊断包里的 `db_check` 能事后发现（P2）。

---

## 8. 额外发现（与"数据到底有什么"直接相关）

1. **验收测试污染了真实数据目录**：活跃目录内含 5 个人类身份、4 个账号、3 组凭据、`upgrade-state.baseline2/3/cli-baseline`、`workspace\FINAL-*`、`workspace\liuhao_goals.json`（56 KB）。如果这是交付机，应先确认这些测试账号/Goal 是否允许保留。
2. **6 份测试副本**：`%LOCALAPPDATA%\LiuHao\data.after-smoke-*`、`data.bak-*` 等，共约 14 MB；其中 4 份含 `auth_secrets.json` + `human_identities.json` + `model_config.key`。这些不在产品数据布局内，也不受备份/清理功能管理，属于"谁拿到磁盘谁就能拿到凭据"的暴露面。
3. **WAL 主导的数据**：`liuhao_execution.journal` 主库只有 4 KB，WAL 511 KB；`liuhao_artifacts.db` 主库 4 KB，WAL 193 KB；`audit_store.db` WAL 1.0 MB。任何"只拷 `.db`"的备份都会丢最近数据。
4. **日志占总量 79%**：13.8 MB 中 10.9 MB 是日志；备份/迁移时日志无价值，却会显著拖慢复制。
5. **诊断包是安全的**：实测 zip 只含脱敏元数据与日志尾部，不含 DB/密钥/`.env`；但 `environment.json` 会暴露安装路径与 Python 路径（内部信息，不是密钥）。
6. **`plugins\` 目录存在但实现另指一处**：启动器在数据根建 `plugins/`；插件注册表默认取 `repo_root/data/plugins`（安装版即程序目录），两者可能不是同一个位置（推断，未深入插件加载器）。

---

## 9. 建议修复（本次仅审计，未改代码）

**P0**
1. 备份/迁移文档改为"先读 `D:\LiuHao\data-dir.txt`，不要假设 `D:\LiuHaoData`"；安装器可在首次启动时把"实际数据目录"写进一个用户可见的位置（如关于页/托盘提示）。
2. 对 `D:\LiuHaoData` 孤儿目录给出显式判定与清理指引（确认无实例使用后可删）。

**P1**
3. 修正升级备份排除名：`model_config_key.bin` → `model_config.key`，并把 `integrity.key`、`human_identities.json` 纳入"不入备份"或改为加密备份；补上文档声称的打包后断言。
4. 统一 UI/文档对 `.lhbak` 的描述：明确"主账号 API Key 以明文进入口令加密的包内"，并把口令下限提高到 12 位；文档不要再写"仅密文"。
5. CLI 备份：要么实现加密（或明确禁止使用），要么在归档 manifest 标记 `encrypted=false` 并在命令输出中警告"含密钥"；修正 `config/memory/knowledge` 三个源路径。
6. 清理 6 份测试副本（至少先移出本机/加密存放），并在测试脚本中强制使用临时数据根，禁止落到 `%LOCALAPPDATA%\LiuHao\data`。

**P2**
7. `data-dir.txt` 损坏时打印/弹窗告警，并在回退前检查默认目录是否已有账号；两者都有数据时要求用户选择。
8. `integrity.key` 增加格式/长度校验；损坏时告警并拒绝启动或进入明确恢复模式，而不是静默换 key。
9. `model_config.key` 丢失时在启动阶段明确告警"历史 API Key 将不可解密"，而不是首次调用时才报错。
10. `app.env` 改为"逐键补齐缺失项"（保留已有密钥），避免部分损坏导致临时 JWT。
11. `logging.yaml` 损坏时先备份为 `.bad` 再回退默认，避免直接启动失败。
12. `current_url.txt`/`instance.json` 增加 pid 存活与端口校验，或在网关就绪后统一更新；桌面端改用 `resolve_data_dir()` 而不是 `REPO_ROOT/data`。
13. 文档补齐真实文件清单（含 WAL/SHM、memory/personal context/jwt/workspace 库、upgrade-state）。

---

## 附录 A：本次审计用到的只读证据

- 文件清单：`Get-ChildItem -Force -Recurse`（数据根与 `D:\LiuHaoData`）。
- 结构与计数：SQLite `mode=ro` + `sqlite_master` + `count(*)`；JSON 只读取键名/形状，不打印密钥/哈希值。
- 配置内容：`app.env` 只打印键名；`auth_secrets.json`/`human_identities.json` 只打印字段名与计数。
- 关键代码：
  - [launch_installed.py:65-112](../scripts/launch_installed.py#L65-L112)（`data-dir.txt` 解析与静默回退）
  - [launch_installed.py:377-387](../scripts/launch_installed.py#L377-L387)（`app.env` 只在缺失时生成）
  - [integrity_key.py:228-285](../src/common/integrity_key.py#L228-L285)（密钥缺失/损坏行为）
  - [auth.py:294-296](../src/gateway/auth.py#L294-L296)（凭据库 fail-closed）
  - [_persistence.py:705-758](../src/kernels/identity/_persistence.py#L705-L758)（身份库损坏返回空）
  - [logging_config.py:59-90](../src/logging_config.py#L59-L90)（日志配置损坏 fail-fast）
  - [model_config.py:318-345](../src/ai/model_config.py#L318-L345)（主密钥读取/派生）
  - [backups.py:40-58](../src/upgrade/backups.py#L40-L58)（升级备份排除集）
  - [account_backup.py:742-752](../src/gateway/account_backup.py#L742-L752)（UI 备份包实际内容）
  - [backup_commands.py:99,135-138,213-216](../src/cli/backup_commands.py#L99-L216)（CLI 源路径/明文/加密未实现）

## 附录 B：本次审计未做的事

- 未删除/移动任何文件，未修改任何配置。
- 未对真实数据目录做"删 `config/`、改坏 `data-dir.txt`"等破坏性复现；相关行为均来自代码路径，已标注"推断"。
- 未验证 `.lhbak` 导入的端到端（本机已有根账号，按设计不允许导入）。
- 未检查日志正文是否包含敏感业务内容（只确认日志不属于密钥文件）。
