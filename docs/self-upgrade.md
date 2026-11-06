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

# 也可以直接指向清单文件（发布包与清单同目录即可）
python -m src.upgrade check --app-root . --manifest D:\releases\LIUHAO_RELEASE.json

# 或使用 HTTP 清单
python -m src.upgrade check --app-root . --manifest-url https://example.com/LIUHAO_RELEASE.json

# 检查 → 下载 → 校验 → 备份 → 应用（失败自动回滚）
python -m src.upgrade upgrade --app-root . --release-dir D:\releases

# 重装同一版本（修损坏）或显式降级；不带这个开关时同版本/低版本一律不安装
python -m src.upgrade upgrade --app-root . --release-dir D:\releases --allow-same-or-dowgrade

# 查看当前状态（检查中/下载中/校验中/备份中/应用中/完成/失败/已回滚）
python -m src.upgrade status --app-root .

# 查看历史记录（时间、从哪版到哪版、成功/失败原因、备份 ID）
python -m src.upgrade history --app-root . --limit 20

# 一键回滚到最近一次成功升级之前的版本
python -m src.upgrade rollback --app-root .

# 回滚到指定备份 / 指定升级记录
python -m src.upgrade rollback --app-root . --backup-id 20261106T010203Z-abcd1234
python -m src.upgrade rollback --app-root . --record-id <升级记录 ID>

# 列出升级前数据快照（每次升级都会自动做一份）
python -m src.upgrade snapshots --app-root . --limit 20

# 单独取出升级前的数据快照（目标目录必须不存在）
python -m src.upgrade export-snapshot --app-root . --dest D:\liuhao-snapshot-20261106
python -m src.upgrade export-snapshot --app-root . --dest D:\snap --backup-id <备份 ID>

# 对当前安装做一次自检（在临时端口真实启动服务并等健康检查，再查数据库与关键表）
python -m src.upgrade selfcheck --app-root .

# 查看“上次检查更新”的时间和结果（手动检查与每周自动检查共用）
python -m src.upgrade last-check --app-root .
```

退出码：`0` 表示检查成功（有更新或已是最新）或操作成功；`2` 表示“无法检查更新/清单无效/未配置源”；`1` 表示执行失败（下载、校验、应用、回滚失败）。

`--allow-same-or-dowgrade` 的真实行为：

| 场景 | 不带开关 | 带开关 |
| --- | --- | --- |
| 清单最高版本 > 当前 | 正常升级，退出码 0 | 同左 |
| 清单最高版本 = 当前 | `up_to_date`，退出码 0，**不写任何文件**（不会修复损坏） | `update_available`，真实下载/备份/应用/自检，退出码 0，版本不变 |
| 清单最高版本 < 当前 | `up_to_date`，退出码 0，消息“当前版本 X 高于发布清单中的最高版本 Y；如需降级请显式允许” | `update_available`，真实降级，退出码 0，版本回退 |

这个开关只放行“目标版本不高于当前版本”，不跳过任何校验：SHA256、版本可读、数据预检、磁盘空间、自检失败自动回滚都照旧执行。

### 1.2 数据目录解析（安装版与开发版）

升级器读写的是**用户数据目录**。CLI、HTTP API 与直接构造 `UpgradeConfig` 的调用方共用同一实现（`src.upgrade.manager.resolve_data_dir`），优先级：

1. 显式 `--data-dir`（CLI）或 `UpgradeConfig(data_dir=...)`；
2. 环境变量 `LIUHAO_DATA_DIR`；
3. **安装树**（`<app-root>/runtime/python.exe` 存在）→ `%LOCALAPPDATA%\LiuHao\data`（与 `installer/liuhao.iss`、`scripts/launch_installed.py` 的规则一致）；
4. 开发/便携版 → `<app-root>/data`。

`--app-root` 是**程序目录**（安装版为 `%LOCALAPPDATA%\LiuHao\app`）。不要把 `LIUHAO_WORKSPACE_ROOT` 当程序根：安装版启动器把它指向 `<data>/workspace`。周检查/HTTP API 的程序根默认取代码所在安装根，可用 `LIUHAO_APP_ROOT` 覆盖。

### 1.3 受控自改代码

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

### 2.2 发布清单规范 v1（定稿：2026-11-06）

> 本节是**交给打包流水线照做**的唯一规范。字段名、大小写、单位以此为准。升级器不读取的字段会在表中明确标注，打包侧可以写，但不要依赖它们产生任何升级行为。

#### 2.2.1 每次发布的产物

三个文件放在**同一个发布目录**（本地源就是该目录；HTTP 源就是同一 URL 前缀）：

| 文件 | 作用 | 必需 |
| --- | --- | --- |
| `LIUHAO_RELEASE.json` | 发布清单（本规范 2.2.2 定义的索引），升级器读它判断是否存在新版本 | 必需 |
| `liuhao-<version>.zip` | 完整安装包；文件名可自定义，但必须与清单里的 `artifact` 完全一致 | 必需 |
| （可选）签名 | 写在清单的 `signature` 字段里（见 2.2.6），不单独出文件 | 可选 |

- **文件名定稿为 `LIUHAO_RELEASE.json`。** `release-manifest.json` 仍被兼容读取（优先找 `LIUHAO_RELEASE.json`），但新流水线必须产出前者。
- 清单和安装包必须在同一目录/同一路径前缀下；HTTP 源的 `artifact` 按相对 URL 解析。

#### 2.2.2 清单字段（根节点）

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema` | string | 否（强烈建议写） | 必须为 `liuhao.release-manifest/1`；省略视为 v1；其它值直接拒绝 |
| `releases` | array | 是 | 非空数组，每项见 2.2.3；同一清单内版本不得重复 |
| `channel` / `generated_at` / 其它自定义字段 | 任意 | 否 | **升级器忽略**，仅供人看 |

#### 2.2.3 清单里每个 `release` 的字段

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `version` | string | 是 | PEP 440 版本号（见 2.2.4） |
| `artifact` | string | 是 | ZIP 文件名或相对 URL。本地源必须位于发布目录内（拒绝目录穿越/越界）；HTTP 源仅允许 `http`/`https` |
| `sha256` | string | 是 | **整包** SHA256，64 位小写十六进制（见 2.2.5） |
| `size` | int | 否（强烈建议） | ZIP 字节数；提供时下载与安装前都会复核 |
| `signature` | string | 否 | Ed25519 签名，base64（见 2.2.6） |
| `signature_algorithm` | string | `signature` 存在时 | 定稿只允许 `ed25519`（省略按 `ed25519` 处理，其它值明确拒绝） |
| `notes` | string | 否 | 展示用说明 |
| `released_at` | string | 否 | ISO 8601 时间 |
| `files` / `delta` / `min_version` / `max_version` | — | — | **v1 不读取**（见 2.2.5 / 2.2.7）；写入也不会改变升级行为 |

#### 2.2.4 版本号规则（定稿）

- 评判标准只有一个：字符串能交给 `packaging.version.Version` 解析（PEP 440）。代码不额外加“必须三段式”“不许带 v”之类的限制。
- 会被**接受并归一化**的写法（清单里可以这样写，比较时按归一化后的版本）：
  - `v0.2.0` → 归一化为 `0.2.0`（前导 `v` 被忽略）
  - `0.2.0-beta` → 归一化为 `0.2.0b0`
  - `0.2` → 归一化为 `0.2`
  - `1.0.0rc1`、`1.0.0.post1`、`2026.11.6` 原样可解析
- 真正会被判**非法**（`invalid_manifest`）的：`packaging.version.Version` 解析失败的字符串，例如 `not-a-version`、`完全中文化`、`0.2 .0`、空字符串。
- 注意：清单里的 `version` 原样保留在 `ReleaseInfo`，只在比较时归一化。因此 `v0.2.0` 能通过检查；安装时也会把包内版本与清单版本各自归一化后比较（`Version(a) != Version(b)`），`v0.2.0` 与 `0.2.0` 视为同一版。发布工具默认写出规范三段式，避免这种歧义。
- 同一清单内版本不得重复（**字面重复**直接判清单无效；`v0.2.0` 与 `0.2.0` 因字面不同不会被这条拦下，但归一化后相等，比较结果相同）。
- 比较按 PEP 440 全序；**只有目标版本严格大于当前版本才会提示升级**，相同版本不会重复提示。
- 安装包内的版本（`pyproject.toml` 的 `[project].version` 或包根 `LIUHAO_RELEASE.json` 的 `version`）必须与清单 `version` **归一化后相等**，否则拒绝安装。
- 推荐统一 `MAJOR.MINOR.PATCH` 语义化版本（不带 `v` 前缀），减少归一化带来的困惑。

#### 2.2.5 包内文件布局（定稿）

- ZIP 根目录**直接就是安装树**，不要多包一层 `liuhao-0.2.0/`。
- 允许写入（安装根，与 `stage_payload` 产出的安装树一致）：`src/`、`config/`、`scripts/`、`console/`、`assets/`、`docs/`（可选）、根级 `*.py`（如 `error_types.py`）、`capability-registry.yaml`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、`LiuHao.bat`、`START-HERE.txt`，以及根目录的 `LIUHAO_RELEASE.json`。**不要**按“源码仓库”只打 `src/`——根级模块被 `src/` 裸名导入，缺了会让干净机器 apply 后自检 `ModuleNotFoundError`。
- 明确禁止：`data/`、`.git/`、`.venv/`、`.env`、`secrets/`、`*.pem`/`*.key`/`*.crt`，以及 `upgrade-backups/`、`upgrade-state/` 所在的路径。
- 包内必须能确定版本：`pyproject.toml [project].version` 或根目录 `LIUHAO_RELEASE.json` 的 `version`；**推荐两者都写且一致**。
- 包根 `LIUHAO_RELEASE.json` 是**包内版本标记**（与发布目录里的清单同名但不同物，位置不同、结构不同）：

```json
{
  "schema": "liuhao.release-package/1",
  "product": "liuhao-ai-os",
  "version": "0.2.0",
  "released_at": "2026-11-06T00:00:00Z"
}
```

升级器只读其中的 `version`；`schema`/`product`/`released_at` 为保留字段，便于人工核对。安装后它会成为应用根目录的版本标记，`src/upgrade/versioning.py` 在没有 `pyproject.toml` 时靠它取版本。

- 安全限制（不满足直接拒绝，不会部分安装）：不允许符号链接、绝对路径、`..`、盘符、重复成员、空包；条目数 ≤ 100,000；解压总量 ≤ 2 GiB（可配置）。

#### 2.2.6 SHA256 计算方式（定稿：整包，不是逐文件）

**定稿采用“整包 SHA256”**：

1. 先打出最终的 `.zip`；
2. 对**该文件完整字节**计算 `hashlib.sha256`，取小写十六进制；
3. 把结果写进清单的 `sha256`；
4. 打包完成后**禁止再修改 ZIP**（任何重新压缩/改时间戳都会改变哈希）。

选择整包而不是逐文件的理由（哪个更抗篡改）：

- 逐文件哈希清单本身与安装包处在**同一个可替换通道**（同一个发布目录/HTTP 路径）。能替换安装包的人通常也能替换清单；只校验逐文件时，攻击者只要同步改清单就能通过。
- 逐文件校验天然漏掉“新增文件”：除非同时强制“文件集合完全一致”，多塞一个 `src/sitecustomize.py` 这类文件不会被发现。整包哈希把成员列表、顺序、ZIP 中央目录、压缩参数全部绑定，增/删/改任意字节都会失败。
- 整包哈希实现简单、只有一个值，不存在“清单里漏写某个文件”的问题。
- 升级器在**下载后**和**安装前**各复核一次整包 SHA256（外加 `size`），覆盖“下载后被替换”的窗口。

逐文件清单（`files[]`）在 v1 **不读取、不用于放行或拒绝**；打包侧若要产出，只能当作人工排障信息，不能依赖它通过校验。

#### 2.2.7 签名（可选，定稿结构）

- 算法：**仅 Ed25519**。
- 签名对象：**发布包 ZIP 的完整字节**（原始字节，不做预哈希）；不是清单，也不是逐文件哈希。
- 结构（写在清单对应 release 项里，扁平字段，不嵌套）：

```json
{
  "signature": "<base64( Ed25519_sign(private_key, zip_bytes) )>",
  "signature_algorithm": "ed25519"
}
```

- base64 使用标准字母表、`validate=True` 解码；非法 base64 直接拒绝。
- 客户端必须显式配置可信公钥（PEM 文件路径或 base64 原始公钥字节）才会真正验签：
  - 未配置公钥：升级仍可进行，但状态/CLI 必须显示“发布清单包含签名，但未配置可信公钥，未验证签名（仅哈希校验）”，绝不冒充“已验证”。
  - 已配置公钥：签名不匹配抛 `SignatureError` 并中止，不写任何文件。
  - 发布包 > 512 MiB 时拒绝在内存中验签（明确报错），不会假装通过。
- **签名只覆盖安装包，不覆盖清单本身**。清单的完整性依赖 HTTPS 和可选的 `--manifest-sha256` 固定值（把清单哈希提前钉在客户端配置里）。这是 v1 的真实边界，见第 8 节。

#### 2.2.8 增量：v1 不需要

- **v1 只支持完整 ZIP 包，不做增量/差量。** 清单里不需要 `delta`/`base`/`patch` 之类字段；写了也不读。
- 理由：增量包要额外定义“基线版本 + 逐文件补丁 + 删除清单 + 冲突处理”，任何一环缺失都会产生“升级后状态不可预测”；而当前安装/回滚本来就是整包原子替换，增量不会减少失败面，只会增加校验与回滚复杂度。
- 代价是每次升级要下载完整包（当前体量可接受）。若未来确需增量，必须同时升级 `schema`（例如 `liuhao.release-manifest/2`），旧客户端因 schema 不识别而拒绝，避免“新包被老逻辑误读”。

#### 2.2.9 打包流水线检查清单（照做即可）

1. 改 `pyproject.toml [project].version` 为目标版本；
2. 只打包安装根（`src/`、`config/`、`scripts/`、`console/`、`assets/`、根级 `*.py`、`capability-registry.yaml`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、`LiuHao.bat`、`START-HERE.txt`，可选 `docs/` 与根目录 `LIUHAO_RELEASE.json`），不要带 `runtime/`、`data/`、`.env`、密钥、`.git`、缓存；构建流水线用 `make_release.py --app-root <payload/app>`，直接从安装树收集，保证与安装后目录一致；
3. 生成 `liuhao-<version>.zip`；
4. 对 ZIP 完整字节算 SHA256 与字节数（**此时之后不再改 ZIP**）；
5. （可选）用 Ed25519 私钥对 ZIP 完整字节签名，base64 写入 `signature`，`signature_algorithm` 写 `ed25519`；
6. 生成 `LIUHAO_RELEASE.json`（`schema` + `releases[]`，字段见 2.2.2/2.2.3），与 ZIP 同目录；
7. 自检：把清单喂给 `python -m src.upgrade check --app-root . --manifest <目录>/LIUHAO_RELEASE.json`（或 `--release-dir <目录>`），确认返回 `update_available` 且退出码 0；若返回 `invalid_manifest`，按消息修字段；
8. 客户端首次部署时配置可信公钥（`--public-key`，或每周检查用的 `LIUHAO_UPGRADE_PUBLIC_KEY`）。

完整示例：

```json
{
  "schema": "liuhao.release-manifest/1",
  "channel": "stable",
  "releases": [
    {
      "version": "0.2.0",
      "artifact": "liuhao-0.2.0.zip",
      "sha256": "<发布包 ZIP 的 64 位十六进制 SHA256>",
      "size": 123456,
      "signature": "<对 ZIP 完整字节的 Ed25519 签名，base64>",
      "signature_algorithm": "ed25519",
      "notes": "可选",
      "released_at": "2026-11-06T00:00:00Z"
    }
  ]
}
```

约束回顾：`releases` 必须非空；版本号必须能被 `packaging.version.Version` 解析且不重复；`sha256` 必须 64 位十六进制；`size` 若存在必须是整数；`artifact` 在本地源中必须位于发布目录内，在 HTTP 源中按相对 URL 解析且仅允许 `http/https`。

### 2.3 离线语义（重要）

| 情况 | 状态 | 用户看到的文字 |
| --- | --- | --- |
| 未配置任何更新源 | `not_configured` | 未配置更新源（本地发布目录或 HTTP 清单），无法检查更新 |
| HTTP 网络不可用 / 超时 | `unreachable`，`offline=true` | 无法检查更新：网络不可用（…） |
| HTTP 返回非 200 | `unreachable` | 无法检查更新：更新源返回 HTTP 404 |
| 本地发布目录不存在 / 缺少清单 | `unreachable` | 无法检查更新：发布目录不存在 / 发布目录中没有 LIUHAO_RELEASE.json（或 release-manifest.json） |
| 清单 JSON/schema/字段非法 | `invalid_manifest` | 无法检查更新：发布清单无效（…） |

**只有真正加载到合法清单时，才可能返回“已是最新版本”。** 版本不高于当前时分三种情况：同版本且未允许重装 → `up_to_date`“已是最新版本”；同版本且 `--allow-same-or-dowgrade` → `update_available`（重装）；清单最高版本低于当前 → `up_to_date`，但消息明确写“当前版本 X 高于发布清单中的最高版本 Y；如需降级请显式允许”，不会伪装成“已是最新”。`tests/test_upgrade_version_source.py` 用真实的本地 HTTP server 和 monkeypatch 断网覆盖了这些分支，`tests/test_upgrade_cli.py` 覆盖重装/降级开关。

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
4. ZIP 安全校验：只允许安装根（默认 `src`、`config`、`scripts`、`console`、`assets`、`docs`、根级 `*.py`、`capability-registry.yaml`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、`LiuHao.bat`、`START-HERE.txt`、根目录 `LIUHAO_RELEASE.json`），拒绝 `data/`、`.git`、`.venv`、`.env`、`secrets`、`*.pem`、`*.key`、`*.crt`，拒绝绝对路径/`..`/盘符、符号链接、重复路径、超量文件（10 万）与超量解压（2 GiB）；
5. 解压到 staging 后读取包内版本，必须与清单版本一致；
6. **磁盘空间预检**：按“发布包解压后大小 + 当前安装根 + 当前数据目录”估算所需空间（另加 8 MiB 余量），检查应用目录、备份目录、临时目录所在磁盘；不足时**在创建备份、解压 staging、修改任何真实文件之前**失败退出；
7. 记录每个受保护 SQLite 的**表清单**（`capture_table_inventory`），随备份元数据落盘，供升级后自检对比。

### 4.2 备份（代码与数据分开）

`src/upgrade/backups.py`：

- **代码**：把安装根打包成 `code.zip`，排除 `__pycache__`、`.pyc`、SQLite 侧车文件，`testzip()` 校验；元数据记录 SHA256 与文件数。
- **数据**：每个 SQLite 用 `src.kernels.audit.durability.vacuum_into()`（`VACUUM INTO`）做事务一致快照，快照后再做一次 `integrity_check`；非 SQLite 文件按字节流复制，**不做 UTF-8 文本读写**；`-wal`/`-shm`/`-journal` 不复制（避免把旧 WAL 重放进恢复后的主文件）。
- 备份目录 `upgrade-backups/<backup-id>/` 内含 `backup.json`、`code.zip`、`data/...`；账本另外记录 `backup` 事件。`backup.json` 里的 `key_tables` 保存升级前每个库的表清单，是升级后自检的对照基准。

> 复用说明：`vacuum_into` 要求目标文件不存在，内部执行 `VACUUM INTO`；不是文件拷贝，也不是文本读取。`tests/test_upgrade_data_guard.py` 已验证 WAL 中已提交的数据会进入快照，且快照旁不会出现 `-wal`。

### 4.3 安装

- 先解压到临时 staging，校验通过后逐文件原子写入（临时文件 + `os.replace`）；
- 安装完成后重新读取版本，必须等于目标版本；
- 再执行一次数据预检，必须通过；
- **执行升级后自检**（见 4.4）；
- 成功后写账本 `apply/completed`，状态文件写 `completed`。

### 4.4 升级后自检（真实启动）与自动回滚

`src/upgrade/selfcheck.py` + `src/upgrade/startup_probe.py`，在安装完成、数据预检通过之后立即执行，**不需要用户确认**：

1. **真实启动服务**：在**独立子进程**里用当前安装的代码导入 `src.gateway.main:app`（模块/属性可配），以 `uvicorn` 在**临时空闲端口**上启动，`lifespan="on"`（完整执行应用的启动/关闭生命周期），然后轮询健康检查（默认依次试 `/v1/health`、`/health`、`/healthz`、`/openapi.json`，2xx/3xx 视为就绪），直到就绪或超时（默认 90 秒）。
2. **确认能正常退出**：就绪后写停止标记，让子进程走 uvicorn 的优雅关闭路径；必须自己退出且退出码为 `0`，才判定成功（Windows 上不依赖 CTRL_C/terminate，避免把“被强杀”当成“正常关闭”）。
3. **数据库可打开**：对升级前记录的每个 SQLite 执行 `SELECT count(*) FROM sqlite_master`（带 `busy_timeout`）。用读 schema 而不是 `SELECT 1`：后者是常数表达式，部分 SQLite 构建在准备阶段根本不读文件，损坏的库会被误判为健康。
4. **关键表可读**：与升级前 `capture_table_inventory` 的表清单逐库对比；缺失库文件、缺失表、`SELECT 1 FROM "<表>" LIMIT 1` 失败都记为问题。

**隔离（自检本身不能破坏线上）**：

- 安装树（`src`、`config`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、`LIUHAO_RELEASE.json`，以及根目录 `*.py` 顶层模块）会被**复制**（不是硬链接，避免沙箱内的写入改到真实文件）到系统临时目录，子进程的 `cwd` 就是沙箱；
- `HOME`/`USERPROFILE`、`LIUHAO_DATA_DIR` 重定向到沙箱；**环境变量里任何指向真实安装根的绝对路径**（例如部署时配置的 `LIUHAO_HUMAN_IDENTITIES_DB=<app_root>/data/...sqlite3`）都会被改写为沙箱内同构路径，避免自检读写用户真实数据；
- 端口由系统分配的空闲端口（诊断时才用 `--startup-port` 指定固定端口）；
- 结束后删除沙箱（`PYTHONDONTWRITEBYTECODE=1`，不往安装树写 `.pyc`）。

**失败分类**（每一类给可读原因，不是笼统“启动失败”）：

| `failure_kind` | 触发 | 用户看到的原因 |
| --- | --- | --- |
| `port_conflict` | `attempting to bind` / `10048` / `address already in use` | 端口冲突：端口 N 已被占用，服务无法绑定（附 uvicorn 原始行） |
| `dependency_import` | `ModuleNotFoundError` / `No module named` / `ImportError` | 依赖导入失败：具体缺哪个模块 |
| `migration_failed` | `migrat` / `alembic` / `数据库迁移` | 数据库迁移失败：附具体异常行 |
| `lifespan_failed` | `Application startup failed` / lifespan 异常 | lifespan 启动异常：附 traceback 最后一行 |
| `timeout` | 超时前未就绪 | 启动超时：未在限定时间内就绪（附最后日志） |
| `shutdown_failed` | 已就绪但未优雅退出/退出码非 0 | 关闭异常：服务未能正常关闭（退出码 N） |
| `startup_failed` | 其余情况 | 服务启动失败（退出码 N）：附最后错误行 |

判定顺序是“端口 → 依赖 → 迁移 → lifespan → 超时 → 关闭 → 其他”，所以端口占用不会被误判成 lifespan；uvicorn 在 lifespan 启动失败时可能以退出码 `0` 返回，因此**只看退出码不可靠**，未就绪前进程退出的一律按日志分类。

任一问题都会让自检失败：

- 账本记录 `selfcheck/failed`（附完整 `SelfCheckReport`，含 `startup` 子报告）；
- **启动日志尾部会脱敏后落盘**到 `upgrade-state/last-selfcheck.log`（默认保留最后 200 行/20000 字符；`authorization`、`bearer`、`password`、`token`、`api_key`、URL 中的用户名密码替换为 `<已脱敏>`），便于排查；
- 抛出 `SelfCheckError`，走与安装异常相同的自动回滚路径：恢复升级前代码与数据、校验恢复后的版本、账本记录 `apply/failed_rolled_back`、状态 `rolled_back`；
- 用户消息明确包含“升级后自检未通过：…”“已自动回滚”和日志文件路径，不需要用户手动点回滚。

通过时账本记录 `selfcheck/passed`，`ApplyResult` 带 `self_check_passed=true` / `self_check_issues=0`，状态继续到 `completed`。

也可以随时手动复检当前安装：

```powershell
python -m src.upgrade selfcheck --app-root .
python -m src.upgrade selfcheck --app-root . --no-service     # 只查数据库，不启动服务
python -m src.upgrade selfcheck --app-root . --health-path /custom/health
python -m src.upgrade selfcheck --app-root . --startup-port 8080   # 诊断：固定端口
```

退出码 `0` 通过、`1` 失败；`--json` 输出 `SelfCheckReport`（`service_ok/service_detail/startup{ok,failure_kind,failure_label,reason,port,health_path,http_status,startup_seconds,exit_code,shutdown_ok,log_tail}/databases_checked/tables_checked/issues`）。

`tests/test_upgrade_selfcheck.py` 覆盖：真实启动 + 健康检查 200 + 优雅退出（退出码 0）、临时目录/真实数据目录隔离、环境变量绝对路径改写、端口冲突、缺依赖、迁移失败、lifespan 异常、超时、关闭失败不影响启动判断的分类函数、日志脱敏与截断、关键表缺失、数据库损坏、**迁移失败导致自检失败 → 自动回滚且保留脱敏日志**、成功升级记录 `selfcheck/passed` 且 `startup.ok=true`。

### 4.5 失败自动回滚

安装开始后任何异常（包括“安装后版本不对”“安装后数据校验失败”“升级后自检未通过”）都会：

1. 把状态置为 `rolling_back`；
2. 用**本次升级前的备份**恢复代码与数据（恢复前先校验备份完整性）；
3. 校验恢复后的版本；
4. 账本记录 `failed_rolled_back`（成功回滚）或 `failed`（回滚也失败，附两个原因）；
5. 状态文件写 `rolled_back` 或 `failed`，消息包含失败原因。

**中途被杀（断电/任务管理器结束进程/蓝屏）**：在真实替换文件之前，账本会先落一条 `apply/in_progress` 记录，状态同时进入 `applying`。下一次启动（包括执行 `status`）时，管理器会识别这条未完成记录：先校验备份，再自动恢复升级前的代码与数据，把该记录改为 `interrupted_rolled_back`、补一条 `rollback/rolled_back`，状态写 `rolled_back` 并明确提示“上次升级在应用中阶段被中断，已自动恢复到 0.1.0（备份 …）”。因此重启后**不会**落成 `unknown`，也不会停在一个“升级了一半”的版本。若备份同时损坏/缺失，则状态写 `failed` 并给出重新下载旧版本安装包的命令（见 4.8）。

`tests/test_upgrade_apply_rollback.py::test_failure_after_install_triggers_automatic_rollback` 通过注入“安装完成后抛异常”真实跑通了自动回滚，断言版本、源码字节、SQLite 内容都回到升级前。`tests/test_upgrade_disaster_recovery.py::test_kill_during_file_replacement_is_recovered_on_restart` 则在**子进程真实替换两个文件后用 `os._exit(23)` 直接杀死自己**，再新建管理器模拟重启，断言自动恢复、账本与状态。


### 4.6 显式一键回滚

```powershell
python -m src.upgrade rollback --app-root .
```

- 不指定 ID 时取**最近一次 `apply/completed` 记录**的备份；
- 指定 `--record-id` 时用该记录的 `backup_id`；指定 `--backup-id` 时直接使用；
- **在移动/覆盖任何文件之前**，按 `code-backup.json` 与 `data-backup.json` 逐项校验 `code.zip`、数据快照的存在性与 SHA256；任何一项不符都直接拒绝回滚；
- 恢复代码：先把当前安装根移到 `rollback-trash/`（失败可移回），再解压备份；
- 恢复数据：先删除目标路径上的 SQLite 侧车文件，再原子复制备份；恢复后逐个 `integrity_check`。**服务正在运行、数据库仍被占用时**（Windows 上 WAL 库的 `-shm` 被映射，`os.replace` 会报 `WinError 5`），自动改用 SQLite 在线备份 API 把快照写回同一个库：不需要替换文件，写入在一个事务内完成，失败时旧库保持不变；此时不删除运行中服务正在使用的 `-wal`/`-shm`；
- 校验恢复后的版本等于备份中的 `from_version`；
- 写账本 `rollback/rolled_back`，状态 `rolled_back`。

**回滚会恢复升级前的数据快照，因此升级后新写入的数据会丢失。** 升级前的数据不会丢；这是“回到上一版本”的定义。如果你只是想要**升级前**的数据，用 4.7 的快照导出，不需要回滚。

**备份损坏时**：不会开始恢复，状态 `failed`，消息形如“无法回滚：0.1.0 的备份不可用（代码备份包 SHA256 与记录不符…）。当前版本 0.2.0 仍完整，未做任何修改，没有假装回滚成功。”账本记录 `rollback/failed`，磁盘上代码与数据一个字节都不会变。

**备份/旧版本包缺失时**：消息形如“无法回滚：0.1.0 的升级前备份已不存在…。当前版本 0.2.0 仍可正常使用，未做任何修改。需要回到 0.1.0，需要重新下载 0.1.0 版本安装包，放到发布目录后执行：`python -m src.upgrade upgrade --app-root . --release-dir <发布目录> --allow-same-or-downgrade`”。这是一个明确的“去下载”指引，不会把用户留在既不能升级也不能回滚的状态：下载旧包后可用 `--allow-same-or-downgrade` 重新安装，或直接安装更新的版本。

**没有可回滚记录时**：清空状态是 `failed`，账本记录 `rollback/failed`，消息“没有可回滚的升级记录”，不会卡在 `rolling_back`（有回归测试）。

### 4.7 升级前数据快照的单独取出

每次升级都会在 `upgrade-backups/<backup-id>/data/` 留下升级前的数据副本（`VACUUM INTO` 事务快照 + 原始文件），**升级完成后不会被删除**。用户发现数据不对时，可以把它导出成一个独立目录，不需要回滚、不会改动当前安装：

```powershell
# 先看有哪些快照（按时间倒序）
python -m src.upgrade snapshots --app-root . --limit 20

# 取出最近一次成功升级之前的快照
python -m src.upgrade export-snapshot --app-root . --dest D:\liuhao-snapshot-20261106

# 指定某次备份 / 某条升级记录
python -m src.upgrade export-snapshot --app-root . --dest D:\snap --backup-id <备份 ID>
python -m src.upgrade export-snapshot --app-root . --dest D:\snap --record-id <升级记录 ID>
```

实现（`src/upgrade/backups.py::export_data_snapshot`）保证：

- 目标是**必须不存在**的目录；已存在直接拒绝，绝不覆盖用户文件；
- 逐文件复制前后各算一次 SHA256，必须与 `backup.json` 记录一致，不一致抛出 `SnapshotError` 并删除半成品；
- 导出目录里生成 `snapshot.json`（schema `liuhao.snapshot-export/1`），记录 `backup_id`、`from_version`、`to_version`、每个文件的大小与 SHA256，便于事后核对；
- 导出是只读复制，**不改动** `upgrade-backups/` 与当前 `data/`；
- 没有任何备份时明确报“没有可回滚的升级记录”，不会创建一个空目录冒充成功。

`tests/test_upgrade_snapshot.py` 覆盖：升级后往线上库写入新数据 → 导出的库仍然只有升级前的数据；`snapshot.json` 的哈希与导出文件一致；导出不改动线上库；目标目录已存在被拒绝且首次导出不受影响；无备份时明确报错；可按 `--backup-id` 指定。

### 4.8 灾难演练与人工恢复（四种场景）

以下四项都在临时目录 + 临时端口/临时数据里真跑过，**没有碰真实安装目录或真实数据**，测试见 `tests/test_upgrade_disaster_recovery.py`。

| 场景 | 系统行为 | 用户看到的文案 | 人工恢复命令 |
| --- | --- | --- | --- |
| 升级替换文件时进程被杀 | 替换前先写 `apply/in_progress` 账本 + `applying` 状态；下次启动识别未完成记录，校验备份后**自动回滚**，账本改为 `interrupted_rolled_back` 并补 `rollback/rolled_back` | “已回滚：上次升级在应用中阶段被中断，已自动恢复到 0.1.0（备份 …）” | 通常无需人工。若想重试：`python -m src.upgrade upgrade --app-root . --release-dir <发布目录>`；查看：`python -m src.upgrade status --app-root .`、`python -m src.upgrade history --app-root .` |
| 磁盘空间不足 | 在**备份/解压 staging/改真实文件之前**做空间预检；若写入阶段仍遇到 ENOSPC（如并发占用），立刻中止并清理 staging，真实文件未改 | “失败：应用未开始（磁盘空间不足：升级至少需要约 120.0 MB，应用目录 … 仅剩 1.0 MB。尚未修改任何文件，当前版本 0.1.0 仍完整。请清理磁盘后重新执行…）” | 清理磁盘后原命令重跑：`python -m src.upgrade upgrade --app-root . --release-dir <发布目录>` |
| 备份 SHA256 与实际不符 | 回滚前逐项校验 `code.zip` 与数据快照；不符即拒绝，**不移动任何文件**，账本 `rollback/failed` | “无法回滚：0.1.0 的备份不可用（代码备份包 SHA256 与记录不符…）。当前版本 0.2.0 仍完整，未做任何修改，没有假装回滚成功。” | 换一个已知可用的备份：`python -m src.upgrade snapshots --app-root . --limit 20`，再 `python -m src.upgrade rollback --app-root . --backup-id <备份 ID>`；若没有可用备份，重新下载 0.1.0 安装包后执行 `python -m src.upgrade upgrade --app-root . --release-dir <发布目录> --allow-same-or-downgrade` |
| 回滚时旧版本包缺失 | 明确报错，说明需要重新下载；当前版本保持可用，不写“假回滚成功” | “无法回滚：0.1.0 的升级前备份已不存在…。当前版本 0.2.0 仍可正常使用，未做任何修改。需要回到 0.1.0，需要重新下载 0.1.0 版本安装包，放到发布目录后执行：`python -m src.upgrade upgrade --app-root . --release-dir <发布目录> --allow-same-or-downgrade`” | 下载 0.1.0 安装包 → 放到发布目录（含清单）→ `python -m src.upgrade upgrade --app-root . --release-dir <发布目录> --allow-same-or-downgrade`；或直接安装更新版本 |

> `--allow-same-or-downgrade` 只用于“重新下载旧版本安装包后恢复”这类人工恢复，平时升级保持默认（只允许更新版本）。

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

阶段：`checking`（检查中）、`downloading`（下载中）、`verifying`（校验中）、`backing_up`（备份中）、`applying`（应用中）、`self_checking`（自检中）、`rolling_back`（回滚中）、`completed`（完成）、`failed`（失败）、`rolled_back`（已回滚），空闲为 `idle`。

**重启后的状态是可判定的**：`applying/self_checking/rolling_back` 是“进行中”阶段；进程若在这些阶段消失，下一次管理器启动会依据账本里的 `apply/in_progress`（或状态里的 `backup_id`）自动收尾为 `rolled_back` 或 `failed`，并写入明确的中文消息，不会停在 `unknown`。`backing_up` 阶段被打断且尚未落 `apply/in_progress` 时，会明确说明“代码与数据未被修改，可直接重新执行升级”。

账本：`upgrade-state/upgrade-ledger.db`（SQLite WAL），表 `upgrade_records` 持久保存 `check/download/preflight/backup/apply/selfcheck/rollback` 的 `created_at/kind/status/from_version/to_version/reason/detail_json/backup_id/artifact_path`。`apply` 记录会在替换真实文件前先以 `in_progress` 落库，成功后原地更新为 `completed`，中断后更新为 `interrupted_rolled_back`/`failed`，因此账本能看出“这次升级到底有没有开始改文件”。进程重启后仍可查。

### 5.1 上次检查更新时间与结果（手动/每周共用）

`upgrade-state/last-check.json`（schema `liuhao.update-check/1`）由**手动 `check`** 和**每周自动检查**共同写入，用户随时可查：

```powershell
python -m src.upgrade last-check --app-root .
```

字段：

```json
{
  "schema": "liuhao.update-check/1",
  "checked_at": "2026-11-06T09:00:00+00:00",
  "trigger": "weekly",                 // manual | weekly
  "status": "update_available",        // update_available | up_to_date | not_configured | unreachable | invalid_manifest | version_unknown | error
  "message": "发现新版本 0.2.0（当前 0.1.0）",
  "current_version": "0.1.0",
  "latest_version": "0.2.0",
  "source": "本地发布目录 …",
  "offline": false,
  "notified": true,
  "notify_results": [{"channel": "windows_balloon", "ok": true, "detail": "…"}],
  "next_check_at": "2026-11-13T09:00:00",
  "job_id": 42,
  "record_id": "…"
}
```

**检查失败时 `status` 绝不会是 `up_to_date`**：网络/目录/清单失败分别记为 `unreachable`/`invalid_manifest`，消息以“无法检查更新”开头；周任务自身异常（例如管理器构造失败）记为 `error`，同样不谎报“已是最新”。

### 5.2 每周自动检查更新（用户不用主动点）

入口：`src/upgrade/update_check.py`，注册到已有的进程内排程器 `src/tasks/local_scheduler.py`（单实例独占库；`liuhao-scheduler` 进程启用，2 进程 worker 组禁用）。

- 作业名 `liuhao.upgrade.weekly-check`，默认间隔 7 天（`LIUHAO_UPDATE_CHECK_INTERVAL` 秒可调，最小 60 秒）；
- 每次执行后（成功或失败）都会按 `run_at = 现在 + 间隔` 重新入队，`job_key` 用下一次运行时刻，重启/重试不会重复入队；
- `max_attempts=1`：检查失败不重试，避免通知风暴，等下一周再试；
- 只调用 `manager.check(trigger="weekly")`，**绝不 download、绝不 apply**。发现新版本只发一条提示，文案明确“请打开鎏灏确认升级；不会自动安装”；
- 提示通道复用 `src/tasks/reminder_notify.build_default_notifier()`：Windows 托盘气泡 + 控制台 outbox；任一通道成功即视为用户已看到，逐通道结果写进 `last-check.json` 的 `notify_results`；
- 只有配置了更新源才会 seed 作业（`LIUHAO_UPGRADE_RELEASE_DIR` 或 `LIUHAO_UPGRADE_MANIFEST_URL`），避免未配置环境每次启动写库。

相关环境变量（周任务读取）：

| 变量 | 作用 |
| --- | --- |
| `LIUHAO_APP_ROOT` | 程序根目录（默认代码所在安装根；**不要**用 `LIUHAO_WORKSPACE_ROOT`，它指向用户工作区） |
| `LIUHAO_UPGRADE_RELEASE_DIR` | 本地发布目录 |
| `LIUHAO_UPGRADE_MANIFEST_URL` | HTTP(S) 清单地址 |
| `LIUHAO_UPGRADE_PUBLIC_KEY` | Ed25519 公钥（PEM 路径或 base64） |
| `LIUHAO_DATA_DIR` / `LIUHAO_UPGRADE_BACKUP_ROOT` / `LIUHAO_UPGRADE_STATE_DIR` | 数据/备份/状态目录 |
| `LIUHAO_UPDATE_CHECK_INTERVAL` | 检查间隔秒数（默认 604800） |
| `LIUHAO_REMINDER_OS_NOTIFY=0` | 测试/无桌面环境关掉托盘气泡，保留控制台通道 |

`tests/test_upgrade_weekly_check.py` 用**真实 `LocalScheduler` + 固定时钟**跑完整 tick，覆盖：

- 发现 `0.2.0` → 只发通知（标题含 `0.2.0`、正文含“不会自动安装”），版本仍是 `0.1.0`、源码未变、无备份、无 `apply`/`download` 账本记录，下一次运行精确等于“现在 + 7 天”；
- 发布目录缺失 → `unreachable`，消息含“无法检查更新”，**不含“已是最新”**，不通知，仍排下一周；
- 版本相同 → `up_to_date`，不通知（这才是允许说“已是最新”的唯一场景）；
- 管理器构造抛异常 → 记录 `error` + 异常原因，不谎报最新，仍排下一周；
- `seed` 幂等、未配置源时不入队；`register_local_jobs` 确实注册了周检查作业。

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
| 磁盘空间不足（预检或写入时 ENOSPC） | `DiskSpaceError`，在备份/解压/替换真实文件之前中止；消息含所需/剩余空间与重跑命令 | 无（staging 清理） |
| 发布包试图覆盖 `data/` 或受保护路径 | ZIP 校验拒绝（`ApplyError`） | 无 |
| 包内版本与清单不符 / 包内无版本 | 拒绝 | 无 |
| 备份创建或元数据读取失败 | 拒绝，未开始安装 | 无 |
| 安装过程中/后异常 | 自动回滚到升级前代码与数据 | 代码/数据恢复原状 |
| 应用文件替换阶段进程被杀 | 重启后识别 `apply/in_progress`，校验备份并自动回滚；账本记 `interrupted_rolled_back` + `rollback/rolled_back` | 恢复升级前状态 |
| 安装后版本或数据复核失败 | 自动回滚 | 恢复原状 |
| 升级后自检失败（真实启动失败：端口冲突/迁移失败/依赖缺失/lifespan 异常/超时/关闭异常；或库打不开/关键表缺失） | `SelfCheckError`，**自动回滚，无需用户点**；`upgrade-state/last-selfcheck.log` 保留脱敏日志尾部 | 代码/数据恢复原状 |
| 快照导出目标目录已存在 | `SnapshotError`，拒绝覆盖 | 无 |
| 快照导出时源文件哈希不符 | `SnapshotError`，删除半成品 | 无 |
| 周检查发现新版本 | 只发提示，不下载、不安装 | 无 |
| 周检查失败（断网/目录缺失/管理器异常） | 记为 `unreachable`/`error`，绝不写“已是最新” | 无 |
| 自动回滚也失败 | 状态 `failed`，账本记录两个原因，需人工介入 | 可能不一致（明确告知） |
| 无可用回滚记录 | `RollbackError`，状态 `failed`，账本 `rollback/failed` | 无 |
| 回滚前校验发现代码包或数据快照 SHA256 不符 | `BackupCorrupt`，拒绝回滚，消息“备份不可用、当前版本仍完整”，文件零改动 | 保持当前状态 |
| 回滚备份 / 旧版本包缺失 | `BackupNotFound`，消息“需要重新下载 X 版本安装包”并给出 `--allow-same-or-downgrade` 命令；当前版本仍可用 | 保持当前状态 |

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
5. **没有保留升级后新数据**：回滚恢复升级前数据快照，升级后写入的数据会丢失；没有“导出升级后差异再回滚”的能力。**升级前**的数据可以用 `export-snapshot` 单独取出（4.7，已实现），但那是升级前的副本，不是升级后的增量。
6. **没有增量/差量升级**：只支持完整 ZIP 发布包（见 2.2.8），不支持 delta 补丁、断点续传（失败会删除临时包重新下载）。
7. **没有公钥分发/信任引导**：签名验证要求调用方显式配置可信公钥（PEM 文件或 base64）。没有密钥轮换、过期、吊销机制。
8. **签名只支持 Ed25519**，且验证时整包读入内存，>512 MiB 会被拒绝验证（仅哈希路径仍可用）。
9. **没有数据库/状态迁移框架**：升级只保证数据文件被备份与恢复，不会执行 schema 迁移脚本；发布包也不应包含 `data/`。
10. **Windows 内存上限是 best-effort**：Job Object 绑定发生在进程启动后，且未在测试中断言“内存超限确实被杀”；医疗级/对抗级资源隔离不做承诺。
11. **没有网络隔离、注册表隔离、剪贴板/屏幕隔离**。
12. **没有升级过程的 UI 进度条**：状态是文本/JSON+SQLite，不是实时推送；CLI 只在阶段切换时打印。
13. **未做真实 HTTPS/代理/证书固定测试**：HTTP 清单路径只用本地 HTTP server 测过；TLS、代理、企业证书场景未验证。
14. **未做多版本连续升级的长时间浸泡测试**：已有测试覆盖单次升级/回滚和一次失败自动回滚；未验证连续数十次升级的备份清理与磁盘增长。备份不会自动清理，长期使用需人工清理 `upgrade-backups/`。
15. **自检是真实启动，但覆盖面有限**：只验证“能导入、能跑完整 lifespan、默认健康路径可访问、能优雅退出”，**不跑业务流程、不调用写接口、不做端到端测试**。健康端点不在默认四个路径（`/v1/health`、`/health`、`/healthz`、`/openapi.json`）时，需要显式 `--health-path`，否则会被判为超时。
16. **自检沙箱不是安全边界**：以当前用户身份执行；隔离的是**路径与端口**（临时目录 + 临时端口 + 改写指向安装根的绝对路径），不做网络/注册表/系统临时目录隔离。启动期仍可能访问外部服务（模型、HTTP 依赖等），失败会记为本次自检失败。镜像会把 `config/`（含 `auth_secrets.json` 等）复制到系统临时目录，结束后删除；这不是容器/gVisor 级隔离。
17. **启动期迁移只在沙箱空库上验证**：如果新版在 lifespan 里跑 schema 迁移，自检用的是沙箱里的空数据库，**不能证明**同样的迁移在用户真实数据上也能成功。真实数据的可读性由“数据库可打开/关键表可读”保证，不做迁移兼容性验证。
18. **自检失败会回滚到旧代码**：如果新版本来就依赖新的数据格式，而旧版读不了新数据，回滚后仍可能起不来——自检只能保证“回到升级前状态”，不保证“旧代码兼容新数据”。
19. **周检查只在单实例排程器进程内生效**：依赖 `liuhao-scheduler` 正常运行（`local_scheduler` 单实例独占库）。该进程没起来时不会自动检查；多进程 worker 组按约定禁用排程器。
20. **托盘提示依赖桌面会话**：无桌面/服务账户下只有控制台 outbox + `last-check.json`，用户需要主动查看；没有邮件/短信等离线外发通道。
21. **没有“检查结果已读/忽略”状态**：`last-check.json` 只记录最近一次检查，不保存历史列表，也不支持用户标记“已忽略此版本”。重复检查发现同一版本会再次提示（每周最多一次）。

---

## 9. 证据（真实执行结果）

测试命令（仅本模块，按任务纪律不跑全量）：

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_upgrade_version_source.py `
  tests/test_upgrade_download_verify.py `
  tests/test_upgrade_data_guard.py `
  tests/test_upgrade_apply_rollback.py `
  tests/test_upgrade_selfcheck.py `
  tests/test_upgrade_snapshot.py `
  tests/test_upgrade_weekly_check.py `
  tests/test_selfmod_diff_confirm.py `
  tests/test_selfmod_copy_test.py `
  tests/test_upgrade_disaster_recovery.py -q
```

最近一次实际结果（2026-11-06）：

```
79 passed in 66.55s
```

新增的灾难演练用例是**真跑**：杀进程用子进程 `os._exit(23)`；磁盘不足在真实 apply 过程中注入 `ENOSPC`；备份损坏是真的往 `code.zip` 里追加字节；旧包缺失是真的删除 `code.zip`，随后继续升级到 0.3.0 证明没有卡死。

耗时上升来自自检用例**真的启动 uvicorn 子进程**（成功/端口冲突/迁移失败/缺依赖/lifespan 异常/超时各跑一次），不是 mock。

覆盖的关键真实行为（不是“方法返回了字典”的断言）：

- `test_upgrade_version_source.py`：读取版本、未配置源、有更新/已是最新、目录缺失、无效清单、`urlopen` 抛 `URLError` 的离线语义、真实本地 HTTP server 的清单与相对 artifact 下载。
- `test_upgrade_download_verify.py`：SHA256 通过；校验失败清理临时文件；有签名无公钥的如实文案；真实生成 Ed25519 密钥并验签；签名篡改失败；artifact 路径越界拒绝。
- `test_upgrade_data_guard.py`：非法 JSON、损坏 SQLite；WAL 中已提交数据进入 `VACUUM INTO` 快照且不产生 `-wal`；恢复前删除 stale WAL；额外状态文件；按文件头识别 SQLite。
- `test_upgrade_apply_rollback.py`：成功应用并生成代码/数据分离备份；显式回滚后源码与 SQLite 字节回到升级前；注入“安装后异常”自动回滚；数据损坏预检中止且不产生备份；发布包不得覆盖 `data/`；包内无版本拒绝；下载后篡改 artifact 拒绝；无回滚记录时状态落在 `failed` 而不是卡住。
- `test_upgrade_selfcheck.py`：**真实 FastAPI 子进程启动**，健康检查 `/health` 返回 200、优雅关闭、退出码 0；沙箱隔离（相对路径写入与 `LIUHAO_DATA_DIR` 写入都留在临时目录，真实 `data/` 无新文件；环境变量里指向安装根的绝对路径被改写）；端口冲突、`ModuleNotFoundError`、迁移异常、lifespan 异常、超时分别映射到 `port_conflict/dependency_import/migration_failed/lifespan_failed/timeout` 并给出可读原因；日志尾部脱敏（`password=`、`Bearer`、`api_key`、URL 用户密码）与截断；关键表缺失、损坏数据库仍失败；**发布包 lifespan 迁移失败 → 自检失败 → 自动回滚**，断言版本、源码字节、SQLite 内容全部恢复，账本同时有 `selfcheck/failed` 与 `apply/failed_rolled_back`，`upgrade-state/last-selfcheck.log` 存在、含脱敏日志且不含明文密码；正常升级记录 `selfcheck/passed` 且 `startup.ok=true`。
- `test_upgrade_snapshot.py`：升级后往线上库写入新数据，再导出快照，导出的库仍然只有升级前的数据；`snapshot.json` 中每个文件的 SHA256 与实际导出文件一致；导出不改动线上库；目标目录已存在时拒绝且首次导出不受影响；无备份时明确报错；可按 `backup_id` 指定。
- `test_upgrade_weekly_check.py`：用真实 `LocalScheduler` + 固定时钟跑完整 tick；发现新版本只通知（正文含“不会自动安装”）、版本/源码/备份/账本均未变化，下一次运行精确等于 `now + 7 天`；发布目录缺失记为 `unreachable` 且不含“已是最新”；同版本才 `up_to_date` 且不通知；管理器异常记为 `error` 且不谎报最新；`seed` 幂等且未配置源不 seed；`register_local_jobs` 已注册作业。
- `test_selfmod_diff_confirm.py`：diff 生成但工作区不变；路径拒绝；一次性令牌；陈旧提案拒绝；缺测试命令拒绝。
- `test_selfmod_copy_test.py`：副本测试通过且真实工作区无 `marker.txt`；测试失败拒绝；超时杀进程；CPU 上限杀进程；密钥形状环境变量不进子进程；默认禁止 promote；必须 `passed` + 第二次令牌；`finalize`/`rollback` 后文件字节回到原状。
- `test_upgrade_disaster_recovery.py`：子进程在替换两个真实文件后 `os._exit(23)`，重启后状态从 `applying` 变成 `rolled_back`、版本/源码/SQLite 回到 0.1.0、账本有 `apply/interrupted_rolled_back` 与 `rollback/rolled_back`；磁盘预检与 staging 写入 `ENOSPC` 都在真实文件改动前中止，且没有生成备份；篡改 `code.zip` 或数据快照后回滚被拒，当前 0.2.0 的字节完全不变、状态 `failed`；删除 `code.zip` 后回滚提示“需要重新下载 0.1.0 版本安装包”，随后安装 0.3.0 成功，证明不会卡死；CLI 接受 `--allow-same-or-downgrade`。

用真实 CLI（不是测试）复跑的一次成功 + 两次失败证据（2026-11-06）：

```powershell
# 成功：对真实安装做自检（临时 data/state/backup 目录，未碰用户数据）
python -m src.upgrade selfcheck --app-root D:\LiuHao-AI-OS --data-dir <临时> --state-dir <临时> --selfcheck-timeout 90 --json
# ok=true service_ok=true startup.ok=true health=/v1/health http=200 seconds=10.05 exit_code=0 shutdown_ok=true port=56364

# 失败①：人为占用端口（真实安装）
# ok=false failure_kind=port_conflict exit_code=3
# reason: 端口冲突：端口 56444 已被占用，服务无法绑定（ERROR: [Errno 10048] error while attempting to bind ... ）

# 失败②：临时安装里让 lifespan 抛迁移异常
# ok=false failure_kind=migration_failed exit_code=3
# reason: 数据库迁移失败：RuntimeError: database migration failed: alembic upgrade head failed (password=<已脱敏>)
```

依赖与许可证：

- 本模块使用 `cryptography`（Ed25519 验签）、`packaging`（版本比较）、`psutil`（进程树监控），均为项目已有依赖；
- `pyproject.toml` 已声明（`cryptography>=42.0.0`、`packaging>=23.0`、`psutil>=5.9.0`），`requirements.txt` 已锁定（`cryptography==50.0.1`、`packaging==26.3`、`psutil==7.2.2`）；
- `oss-registry.yaml` 已登记并写明宽松许可证：`cryptography`（Apache-2.0 OR BSD-3-Clause）、`packaging`（Apache-2.0 OR BSD-2-Clause）、`psutil`（BSD-3-Clause），均无 copyleft 传染。
- 未引入新的第三方依赖。

---

## 10. 发布一个版本的维护者步骤

1. 修改 `pyproject.toml` 的 `[project].version` 为目标版本（推荐同时在包根写 `LIUHAO_RELEASE.json` 版本标记，两者一致，见 2.2.5）；
2. 打包发布 ZIP，**只包含安装根**（`src/`、`config/`、`scripts/`、`console/`、`assets/`、根级 `*.py`、`capability-registry.yaml`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、`LiuHao.bat`、`START-HERE.txt`，可选 `docs/` 与根目录 `LIUHAO_RELEASE.json`），不要包含 `runtime/`、`data/`、`.env`、密钥、`.git`；
3. 计算**整包** `sha256` 与字节数（算完后不要再改 ZIP，见 2.2.6）；
4. （可选但推荐）用 Ed25519 私钥对 ZIP 完整字节签名，填入 `signature`（base64，`signature_algorithm` 为 `ed25519`）；
5. 写入 `LIUHAO_RELEASE.json`（schema `liuhao.release-manifest/1` + `releases[]`），与 ZIP 放在同一发布目录（HTTP 源同理，`artifact` 可为相对路径）；旧名 `release-manifest.json` 仍兼容，但新流水线用前者；
6. 在客户端配置可信公钥：`--public-key <PEM 文件或 base64>`，或每周检查用的 `LIUHAO_UPGRADE_PUBLIC_KEY`。

未配置公钥时，升级仍可进行，但界面/CLI 会明确显示“仅哈希校验，无签名”或“未验证签名（仅哈希校验）”，不会冒充签名已验证。
