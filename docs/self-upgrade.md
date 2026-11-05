# 鎏灏自我升级与受控自改代码

本文档对应两个新模块：

| 模块 | 路径 | 作用 |
| --- | --- | --- |
| 自我升级 | `src/upgrade/` | 检查更新、下载校验、备份、应用、自动/手动回滚 |
| 受控自改代码 | `src/selfmod/` | 生成补丁、展示 diff、人工确认、副本测试、通过后才允许应用、可回滚 |

设计前提：**Windows 上没有可用的真实沙箱**（gVisor 永久不可用，单机双击安装场景也没有容器运行时）。因此这两个模块都不承诺“自主改代码不留痕”或“测试进程绝对碰不到真实文件”；它们提供的是**可验证、可回滚、用户看得见**的受控流程，边界在本文第 8 节逐条写清。

---

## 1. 快速使用

### 1.1 自我升级

```powershell
# 检查更新（本地发布目录；离线时明确报“无法检查更新”，不会说“已是最新”）
python -m src.upgrade check --app-root . --release-dir D:\releases

# 或使用 HTTP 清单
python -m src.upgrade check --app-root . --manifest-url https://example.com/release-manifest.json

# 检查 → 下载 → 校验 → 备份 → 应用（失败自动回滚）
python -m src.upgrade upgrade --app-root . --release-dir D:\releases

# 查看当前状态（检查中/下载中/校验中/备份中/应用中/完成/失败/已回滚）
python -m src.upgrade status --app-root .

# 查看历史记录（时间、从哪版到哪版、成功/失败原因、备份 ID）
python -m src.upgrade history --app-root . --limit 20

# 一键回滚到最近一次成功升级之前的版本
python -m src.upgrade rollback --app-root .

# 回滚到指定备份 / 指定升级记录
python -m src.upgrade rollback --app-root . --backup-id 20261106T010203Z-abcd1234
python -m src.upgrade rollback --app-root . --record-id <升级记录 ID>
```

退出码：`0` 表示检查成功（有更新或已是最新）或操作成功；`2` 表示“无法检查更新/清单无效/未配置源”；`1` 表示执行失败（下载、校验、应用、回滚失败）。

### 1.2 受控自改代码

`edits.json` 是整文件编辑列表，`content: null` 表示删除文件：

```json
[
  {"path": "src/example.py", "content": "…完整的新文件内容…"},
  {"path": "src/old.py", "content": null}
]
```

```powershell
# 1) 生成提案 + 展示 diff（绝不修改真实工作区）
python -m src.selfmod propose --edits-file edits.json --title "修复 X" `
  --rationale "原因" --test-command "python -m pytest -q tests/test_x.py"

# 2) 再次查看 diff
python -m src.selfmod show --proposal-id <id>

# 3) 人工确认，签发一次性测试令牌
python -m src.selfmod confirm --proposal-id <id> --approver owner

# 4) 在临时副本上应用并跑测试（真实工作区不变）
python -m src.selfmod run --proposal-id <id> --token <token>

# 5) 只有测试通过，且再次人工确认后，才允许写入真实工作区
python -m src.selfmod confirm --proposal-id <id> --approver owner --stage promote
python -m src.selfmod finalize --proposal-id <id> --token <promote-token> --allow-promote

# 6) 回滚已应用的提案
python -m src.selfmod rollback --proposal-id <id>

# 查看提案列表 / 审计事件
python -m src.selfmod list
```

默认 `allow_promote=False`：即使测试通过、令牌正确，`finalize` 也会抛 `PromotionDisabled`，真实工作区不会被写入。要启用写入必须显式传 `--allow-promote`。

---

## 2. 升级能力与来源

### 2.1 当前版本读取

`src/upgrade/versioning.py` 按以下顺序读取，任一成功即返回：

1. `pyproject.toml` 的 `[project].version`；
2. 应用根目录的 `LIUHAO_RELEASE.json`（`{"version": "..."}`）；
3. 安装元数据 `importlib.metadata.version("liuhao-ai-os")`。

全部失败时抛 `VersionReadError`，检查结果状态为 `version_unknown`，消息为“无法确定当前版本，无法检查更新”。**不会猜测版本，也不会误报已是最新。**

### 2.2 发布清单格式

```json
{
  "schema": "liuhao.release-manifest/1",
  "releases": [
    {
      "version": "0.2.0",
      "artifact": "liuhao-0.2.0.zip",
      "sha256": "<发布包 ZIP 的 64 位十六进制 SHA256>",
      "size": 123456,
      "signature": "<对发布包完整字节的 Ed25519 签名，base64>",
      "signature_algorithm": "ed25519",
      "notes": "可选",
      "released_at": "2026-11-06T00:00:00Z"
    }
  ]
}
```

约束：

- `schema` 可省略；出现时必须是 `liuhao.release-manifest/1`；
- `releases` 必须非空，版本号必须能被 `packaging.version.Version` 解析，重复版本拒绝；
- `sha256` 必须 64 位十六进制；`size` 若存在必须是整数；
- `artifact` 在本地源中必须位于发布目录内（路径穿越/越界拒绝）；在 HTTP 源中按相对 URL 解析，仅允许 `http/https`；
- 签名是**对发布包完整字节**的签名，不是对清单的签名。签名可选。

### 2.3 离线语义（重要）

| 情况 | 状态 | 用户看到的文字 |
| --- | --- | --- |
| 未配置任何更新源 | `not_configured` | 未配置更新源（本地发布目录或 HTTP 清单），无法检查更新 |
| HTTP 网络不可用 / 超时 | `unreachable`，`offline=true` | 无法检查更新：网络不可用（…） |
| HTTP 返回非 200 | `unreachable` | 无法检查更新：更新源返回 HTTP 404 |
| 本地发布目录不存在 / 缺少清单 | `unreachable` | 无法检查更新：发布目录不存在 / 发布目录中没有 release-manifest.json |
| 清单 JSON/schema/字段非法 | `invalid_manifest` | 无法检查更新：发布清单无效（…） |

**只有真正加载到合法清单并且版本不高于当前版本时，才返回“已是最新版本”。** `tests/test_upgrade_version_source.py` 用真实的本地 HTTP server 和 monkeypatch 断网覆盖了这些分支。

---

## 3. 下载与校验

`src/upgrade/downloader.py`：

1. 下载到临时目录（HTTP 流式写入 `.part`，本地源流式复制），完成后 `os.replace` 原子改名；
2. 强制校验大小上限（默认 2 GiB）与 SHA256；
3. 校验失败时删除 `.part`/目标文件，HTTP 临时目录整体清理，不留下半包；
4. 签名结论按实际情况如实返回：

| 清单签名 | 配置公钥 | 结果 |
| --- | --- | --- |
| 无 | 任意 | `仅哈希校验，无签名` |
| 有 | 无 | `发布清单包含签名，但未配置可信公钥，未验证签名（仅哈希校验）` |
| 有 | 有 | 真实 Ed25519 验签；通过则 `已使用配置的 Ed25519 公钥验证签名`，失败抛 `SignatureError` 并中止 |
| 有，非 ed25519 | 有 | 明确拒绝：“不支持的签名算法” |

签名超过 512 MiB 的发布包会明确拒绝“无法在内存中完成签名验证”，**不会假装验证通过**。

应用前会再次对磁盘上的发布包复核 SHA256、大小和签名，防止“下载后被替换”。

---

## 4. 应用与备份

### 4.1 应用前检查（任何一项失败都不修改文件）

1. 当前版本可读，且目标版本必须更新（除非显式 `allow_same_or_downgrade=True`）；
2. 发布包 SHA256/大小/签名复核；
3. 数据预检：`data/` 及 `extra_state_files` 中每个 SQLite 执行 `PRAGMA integrity_check`，JSON/YAML 必须可解析，符号链接报问题；
4. ZIP 安全校验：只允许安装根（默认 `src`、`docs`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`），拒绝 `data/`、`.git`、`.venv`、`.env`、`secrets`、`*.pem`、`*.key`、`*.crt`，拒绝绝对路径/`..`/盘符、符号链接、重复路径、超量文件（10 万）与超量解压（2 GiB）；
5. 解压到 staging 后读取包内版本，必须与清单版本一致。

### 4.2 备份（代码与数据分开）

`src/upgrade/backups.py`：

- **代码**：把安装根打包成 `code.zip`，排除 `__pycache__`、`.pyc`、SQLite 侧车文件，`testzip()` 校验；元数据记录 SHA256 与文件数。
- **数据**：每个 SQLite 用 `src.kernels.audit.durability.vacuum_into()`（`VACUUM INTO`）做事务一致快照，快照后再做一次 `integrity_check`；非 SQLite 文件按字节流复制，**不做 UTF-8 文本读写**；`-wal`/`-shm`/`-journal` 不复制（避免把旧 WAL 重放进恢复后的主文件）。
- 备份目录 `upgrade-backups/<backup-id>/` 内含 `backup.json`、`code.zip`、`data/...`；账本另外记录 `backup` 事件。

> 复用说明：`vacuum_into` 要求目标文件不存在，内部执行 `VACUUM INTO`；不是文件拷贝，也不是文本读取。`tests/test_upgrade_data_guard.py` 已验证 WAL 中已提交的数据会进入快照，且快照旁不会出现 `-wal`。

### 4.3 安装

- 先解压到临时 staging，校验通过后逐文件原子写入（临时文件 + `os.replace`）；
- 安装完成后重新读取版本，必须等于目标版本；
- 再执行一次数据预检，必须通过；
- 成功后写账本 `apply/completed`，状态文件写 `completed`。

### 4.4 失败自动回滚

安装开始后任何异常（包括“安装后版本不对”“安装后数据校验失败”）都会：

1. 把状态置为 `rolling_back`；
2. 用**本次升级前的备份**恢复代码与数据；
3. 校验恢复后的版本；
4. 账本记录 `failed_rolled_back`（成功回滚）或 `failed`（回滚也失败，附两个原因）；
5. 状态文件写 `rolled_back` 或 `failed`，消息包含失败原因。

`tests/test_upgrade_apply_rollback.py::test_failure_after_install_triggers_automatic_rollback` 通过注入“安装完成后抛异常”真实跑通了自动回滚，断言版本、源码字节、SQLite 内容都回到升级前。

### 4.5 显式一键回滚

```powershell
python -m src.upgrade rollback --app-root .
```

- 不指定 ID 时取**最近一次 `apply/completed` 记录**的备份；
- 指定 `--record-id` 时用该记录的 `backup_id`；指定 `--backup-id` 时直接使用；
- 恢复前校验代码备份 ZIP 哈希；
- 恢复代码：先把当前安装根移到 `rollback-trash/`（失败可移回），再解压备份；
- 恢复数据：先删除目标路径上的 SQLite 侧车文件，再原子复制备份；恢复后逐个 `integrity_check`；
- 校验恢复后的版本等于备份中的 `from_version`；
- 写账本 `rollback/rolled_back`，状态 `rolled_back`。

**回滚会恢复升级前的数据快照，因此升级后新写入的数据会丢失。** 升级前的数据不会丢；这是“回到上一版本”的定义。需要保留升级后数据时，应先手动导出（该能力未实现，见第 8 节）。

**没有可回滚记录时**：清空状态是 `failed`，账本记录 `rollback/failed`，消息“没有可回滚的升级记录”，不会卡在 `rolling_back`（有回归测试）。

---

## 5. 用户可见状态

状态文件：`upgrade-state/status.json`（可用 `--state-dir` 改）。字段：

```json
{
  "phase": "applying",
  "phase_text": "应用中",
  "message": "应用中：正在安装 12 个文件",
  "updated_at": "2026-11-06T…",
  "current_version": "0.1.0",
  "target_version": "0.2.0",
  "backup_id": "20261106T…-abcd1234",
  "last_error": null,
  "record_id": "…"
}
```

阶段：`checking`（检查中）、`downloading`（下载中）、`verifying`（校验中）、`backing_up`（备份中）、`applying`（应用中）、`rolling_back`（回滚中）、`completed`（完成）、`failed`（失败）、`rolled_back`（已回滚），空闲为 `idle`。

账本：`upgrade-state/upgrade-ledger.db`（SQLite WAL），表 `upgrade_records` 持久保存 `check/download/preflight/backup/apply/rollback` 的 `created_at/kind/status/from_version/to_version/reason/detail_json/backup_id/artifact_path`。进程重启后仍可查。

---

## 6. 受控自改代码（最小闭环）

### 6.1 流程

```
propose（生成 diff，不改工作区）
  → show（人看 diff）
  → confirm(test)（签发一次性测试令牌）
  → run_on_copy（临时副本应用 + 测试，真实工作区零修改）
  → passed / rejected（超时、CPU、内存、非零退出都算 rejected）
  → confirm(promote)（第二次人工确认，签发一次性 promote 令牌）
  → finalize（默认禁用；启用后先备份受影响文件，再原子应用）
  → rollback（从文件级备份恢复）
```

### 6.2 安全约束（已实现并有测试）

- **路径**：拒绝绝对路径、盘符、`..`、受保护前缀（`.git`、`.venv`、`node_modules`、`data`、`upgrade-backups`、`upgrade-state`、`.selfmod-state`、`secrets`、`dist`、`build`、`__pycache__`）、`.env`/`.env.*`、`.pem/.key/.crt/.pfx`；`resolve()` 后仍必须在工作区内。
- **提案防陈旧**：记录每个目标文件的 SHA256；执行前重新读取真实工作区，若文件已被别人改动则拒绝执行（防止覆盖他人的修改）。
- **令牌**：只存 SHA256；一次性；区分 `test`/`promote` 阶段；promote 令牌必须提案已 `passed` 才能签发。
- **副本隔离**：`shutil.copytree` 到临时目录，忽略 `.git`、`.venv`、`node_modules`、`data`、`upgrade-*`、`.selfmod-state`、缓存目录、`*.db*`、`*.log`；测试 `cwd` 在副本；测试子进程写出的 `marker.txt` 只出现在副本，真实工作区没有（有断言）。
- **测试判定**：退出码 0 且未超时、未触发 CPU/内存上限、输出限流未触发，才算 `passed`。
- **真实写入默认关闭**：`allow_promote=False` 时 `finalize` 抛 `PromotionDisabled`；即使启用也必须 `passed` + promote 令牌。
- **应用失败自动恢复**：`finalize` 先把受影响文件（含“原本不存在”的信息）备份到 `state_dir/backups/<proposal>-<uuid>/` 并记录哈希，再原子写入；异常时从备份恢复，状态 `rejected`；恢复也失败则 `failed` 并报告两个原因。
- **可回滚记录**：SQLite 账本 `selfmod_proposals` 保存 diff、编辑、基准哈希、测试摘要、备份目录、时间；`selfmod_events` 保存 propose/confirm/run/finalize/rollback 审计事件。

### 6.3 进程级隔离：能防什么、防不住什么

实现：`src/selfmod/sandbox.py`。

**能防（已用真实进程验证）：**

- 无 `shell=True`，命令按参数列表启动，避免 shell 注入；
- 墙钟超时后杀掉进程树（`psutil` 递归 children + `kill`）——测试 `test_timeout_kills_test_and_rejects` 用 `time.sleep(30)` 在 2 秒上限下被终止；
- 轮询进程树 RSS/CPU，超限杀进程树——`test_cpu_limit_is_enforced` 用 `while True: pass` 在 0.5 秒 CPU 上限下被终止；
- POSIX 上额外设置 `RLIMIT_AS`/`RLIMIT_CPU`；
- Windows 上尝试 Job Object 单进程内存上限（`JOB_OBJECT_LIMIT_PROCESS_MEMORY | KILL_ON_JOB_CLOSE`），失败时静默降级并在文档中标注为 best-effort；
- 子进程环境变量中**名字含 `KEY/TOKEN/SECRET/PASSWORD/PASSWD/CREDENTIAL/PASSPHRASE/AUTH` 的变量被移除**（`test_credential_shaped_env_vars_are_not_inherited` 断言子进程读不到 `LIUHAO_SELFTEST_API_KEY`）；
- 只保留测试输出末尾（默认 200 KB），避免无界内存占用。

**防不住（必须如实说明）：**

- 不是沙箱。子进程仍以当前用户身份运行，**可以读写该用户能读写的任意文件**（包括副本之外的真实工作区、`data/`、用户文档）；
- **可以发起网络请求**（没有网络命名空间/防火墙规则）；
- 可以读取系统钥匙串、浏览器 Cookie、其他进程内存（在权限允许范围内）；
- 环境变量剥离只降低“直接通过 `os.environ` 泄露”的概率，挡不住从配置文件/注册表/磁盘读取密钥；
- Windows Job Object 是在进程启动后才尝试绑定，存在极短竞态；Job Object 也不能保证孙进程绝对无法逃逸；
- 无法限制磁盘写入量、文件句柄数、GPU 等资源；
- 测试通过只代表“这条命令退出码为 0”，**不代表补丁语义正确、没有引入漏洞**。

---

## 7. 失败场景与处理

### 7.1 升级

| 场景 | 行为 | 数据/代码影响 |
| --- | --- | --- |
| 未配置更新源 | `not_configured`，退出码 2 | 无 |
| 断网 / 超时 | `unreachable` + `offline=true`，明确“无法检查更新” | 无 |
| 本地发布目录或清单缺失 | `unreachable`，明确缺失项 | 无 |
| 清单 JSON/schema/字段非法、重复版本 | `invalid_manifest` | 无 |
| 当前版本读不到 | `version_unknown`，拒绝继续 | 无 |
| 下载超过大小上限 | 失败，删除临时文件 | 无 |
| SHA256/大小不符 | `ChecksumMismatch`，删除临时文件 | 无 |
| 有签名但无公钥 | 继续，但明确“未验证签名（仅哈希校验）” | 无 |
| 有公钥且签名不匹配 | `SignatureError`，中止 | 无 |
| 应用前数据预检失败 | `PreflightError`，不备份、不改文件 | 无 |
| 发布包试图覆盖 `data/` 或受保护路径 | ZIP 校验拒绝（`ApplyError`） | 无 |
| 包内版本与清单不符 / 包内无版本 | 拒绝 | 无 |
| 备份创建或元数据读取失败 | 拒绝，未开始安装 | 无 |
| 安装过程中/后异常 | 自动回滚到升级前代码与数据 | 代码/数据恢复原状 |
| 安装后版本或数据复核失败 | 自动回滚 | 恢复原状 |
| 自动回滚也失败 | 状态 `failed`，账本记录两个原因，需人工介入 | 可能不一致（明确告知） |
| 无可用回滚记录 | `RollbackError`，状态 `failed`，账本 `rollback/failed` | 无 |
| 回滚备份 ZIP 哈希不符 | 拒绝回滚 | 保持当前状态 |

### 7.2 受控自改

| 场景 | 行为 | 真实工作区 |
| --- | --- | --- |
| 路径越界/受保护/密钥文件 | `PathNotAllowed`，提案不创建 | 不变 |
| 提案后目标文件被改动 | `StaleProposal`，拒绝执行 | 不变 |
| 未确认直接 run/finalize | `ConfirmationRequired` | 不变 |
| 令牌错误/已使用/阶段不符 | `ConfirmationRequired` | 不变 |
| 未配置测试命令 | `TestCommandMissing`，状态 `rejected` | 不变 |
| 测试非零退出 | `rejected`，记录退出码与输出尾部 | 不变 |
| 测试超时 | 杀进程树，`rejected`，`limit_reason=timeout` | 不变 |
| 测试 CPU 超限 | 杀进程树，`rejected`，`limit_reason=cpu` | 不变 |
| 测试内存超限 | 杀进程树，`rejected`，`limit_reason=memory` | 不变 |
| 测试命令无法启动 | `SandboxError`，`rejected` | 不变 |
| 未通过测试却尝试 finalize | `ConfirmationRequired` | 不变 |
| 默认模式 finalize | `PromotionDisabled` | 不变 |
| 启用写入但应用异常 | 从文件级备份恢复，状态 `rejected` | 恢复原状 |
| 恢复也失败 | 状态 `failed`，报告两个原因 | 可能不一致（明确告知） |
| 回滚非 `applied` 提案 | `SelfModError` | 不变 |

---

## 8. 明确“做不到什么”（未实现 / 未验证）

以下内容**没有实现或没有验证**，不允许对外宣称已经具备：

1. **没有真实沙箱**：Windows 上无 gVisor/容器级别的文件系统、网络、进程隔离。自改测试进程可以访问真实文件系统与网络。
2. **没有自动补丁生成**：`propose` 接收的是“整文件内容或删除”列表，不接受 unified diff/hunk 文本，也没有“由大模型自动写代码”的接线。自然语言 → 代码这一步不在本模块内。
3. **没有前端/网关接线**：按任务约束未修改 `apps/console/**`、`src/gateway/**`、`src/ai/**`、`installer/**`；目前用户可见面是 CLI、`status.json` 和 SQLite 账本，**没有网页按钮/API 端点**。
4. **没有升级并发锁**：两个升级进程同时运行可能竞争；未实现跨进程互斥锁/租约。请勿并发执行升级。
5. **没有保留升级后新数据**：回滚恢复升级前数据快照，升级后写入的数据会丢失；没有“导出升级后差异再回滚”的能力。
6. **没有增量/差量升级**：只支持完整 ZIP 发布包，不支持 delta 补丁、断点续传（失败会删除临时包重新下载）。
7. **没有公钥分发/信任引导**：签名验证要求调用方显式配置可信公钥（PEM 文件或 base64）。没有密钥轮换、过期、吊销机制。
8. **签名只支持 Ed25519**，且验证时整包读入内存，>512 MiB 会被拒绝验证（仅哈希路径仍可用）。
9. **没有数据库/状态迁移框架**：升级只保证数据文件被备份与恢复，不会执行 schema 迁移脚本；发布包也不应包含 `data/`。
10. **Windows 内存上限是 best-effort**：Job Object 绑定发生在进程启动后，且未在测试中断言“内存超限确实被杀”；医疗级/对抗级资源隔离不做承诺。
11. **没有网络隔离、注册表隔离、剪贴板/屏幕隔离**。
12. **没有升级过程的 UI 进度条**：状态是文本/JSON+SQLite，不是实时推送；CLI 只在阶段切换时打印。
13. **未做真实 HTTPS/代理/证书固定测试**：HTTP 清单路径只用本地 HTTP server 测过；TLS、代理、企业证书场景未验证。
14. **未做多版本连续升级的长时间浸泡测试**：已有测试覆盖单次升级/回滚和一次失败自动回滚；未验证连续数十次升级的备份清理与磁盘增长。备份不会自动清理，长期使用需人工清理 `upgrade-backups/`。

---

## 9. 证据（真实执行结果）

测试命令（仅本模块，按任务纪律不跑全量）：

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_upgrade_version_source.py `
  tests/test_upgrade_download_verify.py `
  tests/test_upgrade_data_guard.py `
  tests/test_upgrade_apply_rollback.py `
  tests/test_selfmod_diff_confirm.py `
  tests/test_selfmod_copy_test.py -q
```

最近一次实际结果：

```
40 passed in 10.32s
```

覆盖的关键真实行为（不是“方法返回了字典”的断言）：

- `test_upgrade_version_source.py`：读取版本、未配置源、有更新/已是最新、目录缺失、无效清单、`urlopen` 抛 `URLError` 的离线语义、真实本地 HTTP server 的清单与相对 artifact 下载。
- `test_upgrade_download_verify.py`：SHA256 通过；校验失败清理临时文件；有签名无公钥的如实文案；真实生成 Ed25519 密钥并验签；签名篡改失败；artifact 路径越界拒绝。
- `test_upgrade_data_guard.py`：非法 JSON、损坏 SQLite；WAL 中已提交数据进入 `VACUUM INTO` 快照且不产生 `-wal`；恢复前删除 stale WAL；额外状态文件；按文件头识别 SQLite。
- `test_upgrade_apply_rollback.py`：成功应用并生成代码/数据分离备份；显式回滚后源码与 SQLite 字节回到升级前；注入“安装后异常”自动回滚；数据损坏预检中止且不产生备份；发布包不得覆盖 `data/`；包内无版本拒绝；下载后篡改 artifact 拒绝；无回滚记录时状态落在 `failed` 而不是卡住。
- `test_selfmod_diff_confirm.py`：diff 生成但工作区不变；路径拒绝；一次性令牌；陈旧提案拒绝；缺测试命令拒绝。
- `test_selfmod_copy_test.py`：副本测试通过且真实工作区无 `marker.txt`；测试失败拒绝；超时杀进程；CPU 上限杀进程；密钥形状环境变量不进子进程；默认禁止 promote；必须 `passed` + 第二次令牌；`finalize`/`rollback` 后文件字节回到原状。

依赖与许可证：

- 本模块使用 `cryptography`（Ed25519 验签）、`packaging`（版本比较）、`psutil`（进程树监控），均为项目已有依赖；
- `pyproject.toml` 已声明（`cryptography>=42.0.0`、`packaging>=23.0`、`psutil>=5.9.0`），`requirements.txt` 已锁定（`cryptography==50.0.1`、`packaging==26.3`、`psutil==7.2.2`）；
- `oss-registry.yaml` 已登记并写明宽松许可证：`cryptography`（Apache-2.0 OR BSD-3-Clause）、`packaging`（Apache-2.0 OR BSD-2-Clause）、`psutil`（BSD-3-Clause），均无 copyleft 传染。
- 未引入新的第三方依赖。

---

## 10. 发布一个版本的维护者步骤

1. 修改 `pyproject.toml` 的 `[project].version` 为目标版本；
2. 打包发布 ZIP，**只包含安装根**（`src/`、`docs/`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`），不要包含 `data/`、`.env`、密钥、`.git`；
3. 计算 `sha256` 与字节数；
4. （可选但推荐）用 Ed25519 私钥对 ZIP 完整字节签名，填入 `signature`（base64，`signature_algorithm` 为 `ed25519`）；
5. 写入 `release-manifest.json`，与 ZIP 放在同一发布目录（HTTP 源同理，`artifact` 可为相对路径）；
6. 在客户端配置可信公钥：`--public-key <PEM 文件或 base64>`。

未配置公钥时，升级仍可进行，但界面/CLI 会明确显示“仅哈希校验，无签名”或“未验证签名（仅哈希校验）”，不会冒充签名已验证。
