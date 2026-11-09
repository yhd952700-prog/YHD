# 升级 HTTP 接口契约（前端对接）

实现位置：`src/upgrade/api.py`。前端只需要这四个端点，不执行任何命令行。

## 1. 挂载与鉴权

已挂在生产单端口应用上（`src/gateway/main.py` 的 `get_app()`），路线：先过网关的 `require_human_principal`（人类身份），再由本 router 收敛到 L0/L7 主权或 admin 角色。`/openapi.json` 中可见 4 个端点：

```
/v1/upgrade/status   /v1/upgrade/check   /v1/upgrade/apply   /v1/upgrade/rollback
```

冷启动开销可忽略：api 模块只做轻量导入，manager/selfcheck/downloader 在请求内才导入（增量约 20ms）。若在其他应用里复用：

```python
from src.upgrade.api import router
app.include_router(router)   # 前缀固定为 /v1/upgrade
```

每个请求都必须带一个有效的访问令牌，两种头都接受，私有头优先：

```http
X-Liuhao-Token: Bearer <token>
Authorization: Bearer <token>
```

- 令牌主体必须是**人类**（`metadata.kind == "human"`）。
- 并且必须是管理员：`metadata.scope` 为 `L0` 或 `L7`（人类主权），或 `roles`/`permissions` 含 `admin` / `administrator` / `owner`。
- `401`：缺失或无效令牌，统一结构化 `{code, message}`：无令牌 `AUTH_UNAUTHORIZED`，签名/格式/吊销失败 `AUTH_TOKEN_INVALID`（中文中性文案，不暴露解析细节）。
- `403`：是合法令牌但非人类主体，或权限不足。`detail` 是字符串或 `{code, message}`。

所有业务错误（含 `401`）的响应体统一为：

```json
{"detail": {"code": "…", "message": "…", "...": "附加字段"}}
```

`message` 一律是可以直接展示给用户的中文文案。

## 2. 更新源

只需配置下面任一环境变量即可具备“检查/升级”能力：

| 变量 | 含义 |
| --- | --- |
| `LIUHAO_UPGRADE_RELEASE_DIR` | 本地发布目录（内含 `release-manifest.json` 与安装包） |
| `LIUHAO_UPGRADE_MANIFEST_URL` | HTTP(S) 发布清单地址 |

可选：`LIUHAO_UPGRADE_PUBLIC_KEY`（清单签名公钥）、`LIUHAO_UPGRADE_STATE_DIR`、`LIUHAO_UPGRADE_BACKUP_ROOT`、`LIUHAO_DATA_DIR`、`LIUHAO_APP_ROOT`。

**程序根与数据目录**与 CLI 使用同一套解析（`src.upgrade.manager.resolve_data_dir`）：

- 程序根：`LIUHAO_APP_ROOT` > 代码所在安装根（`src/upgrade/update_check.py` 的 `parents[2]`）。**不要**用 `LIUHAO_WORKSPACE_ROOT`——安装版启动器把它指向 `<data>/workspace`，不是程序目录。
- 数据目录：`LIUHAO_DATA_DIR` > 安装树（`<app-root>/runtime/python.exe` 存在）→ `%LOCALAPPDATA%\LiuHao\data` > 开发/便携版 `<app-root>/data`。
- 安装器会把实际数据目录写进程序根 `data-dir.txt`（UTF-8 带 BOM；新装默认 `D:\LiuHaoData`，D 盘不可用时回退 `%LOCALAPPDATA%\LiuHao\data`；老用户覆盖安装保留原位置）。启动器（`scripts/launch_installed.py`）自己读该文件（兼容旧版 ANSI），因此含中文/空格的路径不会经过 cmd 的代码页转换；显式设置的 `LIUHAO_DATA_DIR` 仍然优先。
- 因此安装版 API 检查/备份的是 `data-dir.txt` 记录的实际数据目录（默认 `D:\LiuHaoData`），不是不存在的 `<app>/data`；CLI 与 API 不会再各检查一个库。
- API 没有“允许重装/降级”入口：目标版本不高于当前版本时 `/apply` 仍返回 `409`（`code: "no_update_available"`），不会悄悄覆盖当前安装。需要重装/降级请用 CLI 的 `--allow-same-or-downgrade`。

没有配置更新源时**不会假装成功**：

| 端点 | 无更新源时的行为 |
| --- | --- |
| `GET /status` | `200`，但 `source_configured: false`，`message` 固定为“未配置更新源：…”，`update_available: false` |
| `POST /check` | `409`，`code: "source_not_configured"` |
| `POST /apply` | `409`，`code: "source_not_configured"` |
| `POST /rollback` | **允许执行**：回滚只使用本地备份，不需要联网。响应 `source_configured: false`，`notice: "未配置更新源（回滚使用本地备份，不需要联网）"` |

最后一条是刻意的例外：网络不通或发布源被撤下时，用户仍然必须能恢复到升级前版本；接口用 `notice` 明确说明这次操作没有使用更新源，不会把“本地回滚成功”伪装成“从更新源成功”。

## 3. 长任务语义（同步，不是 202）

`apply` / `rollback` 是**同步执行**：HTTP 响应返回时，操作已经有明确结果。

- 响应 `mode` 固定为 `"synchronous"`，并带 `warning`：
  “同步执行，可能耗时数分钟；请前端延长请求超时，并通过 GET /v1/upgrade/status 查看进度。”
- 前端请求超时应设为 **≥ 10 分钟**（`apply` 包含下载、备份、原子替换、真实启动服务自检，失败时还会自动回滚）。
- 如果 HTTP 连接被中断或进程被杀，结果不会丢失：下一次进程启动时升级器会自动恢复，并把 `/status` 落成一个确定状态（`failed` 或 `rolled_back`），前端下次打开页面轮询即可看到，不允许停留在“未知”。
- 失败一定会被持久化，`GET /status` 可查 `state: "failed"` + `error.category` + `error.message`。

## 4. 状态机

前端只消费 `status.state`，共 7 个值：

```
idle ──check──> checking ──┬──> available ──apply──> applying ──> applied
                           └──> idle（已是最新/检查失败时仍为 idle，细节看 last_check）
applying ──失败──> failed ──rollback──> rolled_back
applied  ──rollback──> rolled_back
failed   ──rollback──> rolled_back
```

| state | 含义 | 前端动作 |
| --- | --- | --- |
| `idle` | 无进行中的操作，也没有可用更新 | 显示“检查更新” |
| `checking` | 正在检查（含下载/校验阶段） | 显示进度，禁用按钮 |
| `available` | 有可用更新，尚未安装 | 显示版本号 + “立即升级” |
| `applying` | 正在备份/安装/自检/回滚中 | 显示进度，禁用按钮，不要重复提交 |
| `applied` | 上一次升级成功 | 显示“已是最新”，提供“回滚” |
| `rolled_back` | 已回滚到升级前版本 | 显示“已回滚”，可重新检查 |
| `failed` | 上一次升级失败（已自动尝试回滚） | 显示 `error.message`，提供“回滚”与“重试检查” |

`status.phase` 同时返回内部阶段原文（`checking/downloading/verifying/backing_up/applying/self_checking/rolling_back/completed/failed/rolled_back`），仅用于诊断展示，前端应以 `state` 为准。

因为 `apply` 是同步的，正常单客户端流程中很少在响应前观察到 `checking` / `applying`；这两个状态主要用于“另开页面轮询”和“上一次操作被中断后的恢复”。

## 5. 端点

### 5.1 `GET /v1/upgrade/status`

只读，无请求体。永远返回 `200`（未配置源也返回 200，但字段如实标注）。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | 恒为 `true` |
| `source_configured` | bool | 是否配置了更新源 |
| `source` | string | 更新源描述；未配置为 `""` |
| `state` | string | 状态机值（见上） |
| `phase` | string | 内部阶段原文 |
| `message` | string | 用户可读状态文案；未配置源时固定为“未配置更新源：…” |
| `current_version` | string \| null | 当前安装版本 |
| `latest_version` | string \| null | 上次检查得到的最新版本 |
| `update_available` | bool | 上次检查结果是否确实高于当前版本 |
| `last_check` | object \| null | 上次检查详情，见 5.2 的 `check` |
| `last_operation` | object \| null | 最近一次成功/失败操作（apply 或 rollback 中取时间最新的） |
| `last_apply` | object \| null | 最近一次 apply 记录 |
| `last_rollback` | object \| null | 最近一次 rollback 记录 |
| `backup` | object | 备份位置与跨盘状态，见 5.1.1 |
| `error` | object \| null | `{"category": "...", "message": "..."}`，失败时可查 |
| `updated_at` | string | 状态最后写入时间（ISO 8601）；从未升级过为 `""` |

`last_apply` / `last_rollback` / `last_operation` 的字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 操作记录 id |
| `kind` | `apply` 或 `rollback` |
| `status` | `completed` / `failed` / `rolled_back` / `in_progress` / `interrupted` 等持久化结果 |
| `created_at` | 记录时间 |
| `from_version` / `to_version` | 起始/目标版本 |
| `backup_id` | 本次操作使用的备份 id |
| `reason` | 失败原因原文（成功时为 `""`） |
| `error_category` | 由 `reason` 归类出的错误分类（见第 7 节），无失败为 `null` |

#### 5.1.1 `status.backup`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `root` | string | 备份根的真实绝对路径 |
| `data_dir` | string | 数据目录真实绝对路径 |
| `source` | string | `configured`（显式配置）/ `data_dir`（未配置，同盘）/ `portable`（便携/开发形态，数据目录在程序目录内） |
| `configured` | bool | 是否显式配置了 `LIUHAO_UPGRADE_BACKUP_ROOT` |
| `available` | bool | 显式配置的备份根是否真写探针通过；未配置时恒为 `true` |
| `error` | string | 备份根不可写时的中文原因（含盘符/路径），可用时为空串 |
| `label` | string | 一句话结论：`跨盘备份已启用` / `备份与数据在同一磁盘` / `备份盘不可写，备份未启用` |
| `warning` | string | 给用户的风险说明；未配置或同盘时为中文警告，跨盘可用时为空串 |
| `same_volume` | bool | 备份根与数据目录是否同一卷 |
| `cross_disk` | bool | `available && !same_volume`，即是否真正具备跨盘备份能力 |
| `data_drive` / `backup_drive` | string | 两条路径的卷锚点（如 `D:`） |
| `backup_count` / `last_backup_at` / `last_backup_id` | int / string \| null / string \| null | 备份目录里真实存在的备份数与最近一份 |
| `migration` | object \| null | 旧备份迁移记录（见 `backup-migration.json`） |

四种配置情形在 `status.backup` 上的表现：

| 情形 | `source` | `available` | `same_volume` | `cross_disk` | `label` / `warning` |
| --- | --- | --- | --- | --- | --- |
| 配了合法跨盘备份根 | `configured` | `true` | `false` | `true` | `跨盘备份已启用` / 空 |
| 配了不可写路径 | `configured` | `false` | 按路径计算 | `false` | `备份盘不可写，备份未启用` / 中文原因；`apply` 直接拒绝，不退回数据目录 |
| 配了同盘路径 | `configured` | `true` | `true` | `false` | `备份与数据在同一磁盘` / `备份与数据在同一磁盘，磁盘损坏时无法恢复。` |
| 没配 | `data_dir`（便携形态为 `portable`） | `true` | `true` | `false` | `备份与数据在同一磁盘` / 未启用跨盘备份的中文警告 |

备份根不可写或备份盘剩余空间不足（低于“代码+数据体积 × 2 倍余量”）时，`apply` 在创建备份、解压 staging、修改任何真实文件之前拒绝，`status.error.category` 为 `disk_space`，`message` 为中文原因；不会静默退回数据目录。

### 5.2 `POST /v1/upgrade/check`

请求体：空（或 `{}`）。**只检查，不下载、不安装、不改文件。**

响应：

```json
{
  "ok": true,
  "mode": "synchronous",
  "source_configured": true,
  "check": {
    "status": "update_available",
    "message": "发现新版本 0.2.0",
    "current_version": "0.1.0",
    "latest_version": "0.2.0",
    "release": {
      "version": "0.2.0",
      "artifact": "liuhao-0.2.0.zip",
      "sha256": "…",
      "size": 12345,
      "signature": true,
      "signature_algorithm": "ed25519",
      "notes": "",
      "released_at": null
    },
    "source": "本地发布目录 …",
    "offline": false,
    "checked_at": "2026-…"
  },
  "status": { "…": "同 5.1" }
}
```

`check.status` 取值：

| 值 | 含义 | HTTP |
| --- | --- | --- |
| `update_available` | 发现更高版本 | 200 |
| `up_to_date` | 已是最新 | 200 |
| `unreachable` | 更新源暂时不可达（网络问题，不代表已是最新） | 200 |
| `invalid_manifest` | 清单无效 | 200 |
| `version_unknown` | 无法确定当前版本 | 200 |
| `not_configured` | 未配置更新源 | 不会出现（该情况直接返回 409） |

检查失败不改变“是否有更新”的既有判断；不要因为 `unreachable` 就提示“已是最新”。

错误：`409 source_not_configured`。

### 5.3 `POST /v1/upgrade/apply`

请求体：

```json
{"confirm": true, "version": "0.2.0"}
```

- `confirm` 必须为 `true`，否则 `400 confirmation_required`。
- `version` 必须是用户在确认框里看到的那个版本，且必须等于本次检查得到的 `latest_version`，否则 `400 version_mismatch`（防止误装别的版本）。
- `version` 缺省 → `400 version_required`；不是合法版本号 → `400 invalid_version`。

处理顺序：确认 → 版本格式 → 更新源 → 重新检查 → 版本比对 → 下载 → 安装 → 自检。

成功响应：

```json
{
  "ok": true,
  "mode": "synchronous",
  "warning": "同步执行，可能耗时数分钟；…",
  "apply": {
    "record_id": "…",
    "backup_id": "…",
    "from_version": "0.1.0",
    "to_version": "0.2.0",
    "installed_files": 42,
    "data_checked_files": 3,
    "message": "升级完成",
    "self_check_passed": true,
    "self_check_issues": 0
  },
  "status": { "…": "同 5.1" }
}
```

失败响应（HTTP 409），`detail.code` 见第 7 节，并附带 `detail.status`（同 5.1 的结构），前端可直接渲染：

```json
{"detail": {"code": "selfcheck_failed", "message": "…", "status": {"state": "failed", …}}}
```

失败时升级器已尝试自动回滚；`/status` 会给出最终确定状态与可读原因。

### 5.4 `POST /v1/upgrade/rollback`

请求体：

```json
{"confirm": true, "backup_id": "可选"}
```

- `confirm` 必须为 `true`，否则 `400 confirmation_required`。
- 不传 `backup_id` 时回滚到最近一次升级前的备份；传了就回滚到指定备份。
- 回滚前会校验备份完整性（`code.zip` 与数据快照的 SHA256）；备份缺失或损坏时明确失败，不会用坏备份覆盖当前版本。

成功响应：

```json
{
  "ok": true,
  "mode": "synchronous",
  "warning": "同步执行，可能耗时数分钟；…",
  "notice": null,
  "source_configured": true,
  "rollback": {
    "record_id": "…",
    "backup_id": "…",
    "from_version": "0.2.0",
    "to_version": "0.1.0",
    "restored_files": 42,
    "message": "已回滚到升级前版本"
  },
  "status": { "…": "同 5.1" }
}
```

未配置更新源时仍可回滚，此时 `source_configured: false` 且 `notice` 为“未配置更新源（回滚使用本地备份，不需要联网）”。

失败响应：`409`，`detail.code` 见第 7 节，并附带 `detail.status`。

## 6. 错误码总表

| `code` | HTTP | 触发条件 | 前端建议 |
| --- | --- | --- | --- |
| `confirmation_required` | 400 | `confirm` 不是 `true` | 弹出二次确认后再发 |
| `version_required` | 400 | `apply` 没有传 `version` | 传用户在确认框看到的版本 |
| `invalid_version` | 400 | `version` 不是合法版本号 | 版本号来自 `/check`，不要手输 |
| `version_mismatch` | 400 | 请求版本 ≠ 当前可用版本 | 重新 `/check` 后让用户确认 |
| `source_not_configured` | 409 | 无 `RELEASE_DIR` / `MANIFEST_URL` | 引导配置更新源；不要提示“已是最新” |
| `no_update_available` | 409 | 检查成功且当前没有可安装的更新 | 刷新 `/status` 并提示已是最新 |
| `source_unreachable` | 409 | 更新源不可达，无法确认版本 | 提示“暂时无法检查更新”，不要提示已是最新 |
| `invalid_manifest` | 409 | 发布清单无效 | 提示更新源配置有误，联系维护者 |
| `version_unknown` | 409 | 无法确定当前安装版本 | 提示安装损坏，需人工检查 |
| `disk_space` | 409 | 磁盘空间不足（前置失败，未改任何文件） | 提示清理空间后重试 |
| `backup_corrupt` | 409 | 备份校验失败（哈希不符/不可用） | 明确告知备份不可用，不要重试回滚 |
| `backup_missing` | 409 | 备份或旧版本代码包已不存在 | 提示重新下载旧版本安装包 |
| `selfcheck_failed` | 409 | 安装后真实启动/自检未通过 | 已自动回滚，展示原因 |
| `preflight_failed` | 409 | 升级前数据预检失败 | 展示原因，升级未开始 |
| `signature_invalid` | 409 | 安装包/清单签名无效 | 不要重试，检查更新源可信性 |
| `checksum_mismatch` | 409 | SHA256 与清单不符 | 重新下载；持续失败则检查发布源 |
| `unknown` | 409 | 未归类的失败 | 展示 `message`，保留日志 |

`apply`/`rollback` 的失败响应都带 `detail.status`，前端不必再发一次 `GET /status` 就能展示最新状态；仍建议随后轮询一次以确认状态已持久化。

## 7. 前端流程建议

1. 打开页面：`GET /status`，按 `state` 渲染。
2. 用户点“检查更新”：`POST /check`。
3. `check.status == "update_available"`：展示 `latest_version`、`release.notes` 与大小。
4. 用户点“立即升级”：先弹二次确认，确认框里写明目标版本；用户确认后发
   `{"confirm": true, "version": <确认框里的版本>}`，并设置 ≥ 10 分钟超时。
5. 成功：展示 `apply.message`，刷新 `/status`。
6. 失败：展示 `detail.message`（或 `detail.status.error.message`），提供“回滚”按钮；
   回滚同样弹二次确认 → `{"confirm": true}`。
7. 任何时候都可以用 `GET /status` 轮询（建议 2 秒一次）来恢复进度展示，尤其是页面被刷新、请求被中断之后。

前端不做的事：

- 不要自动升级；`apply` / `rollback` 必须来自用户在确认框里的明确动作。
- 不要把 `version` 自动填成最新版本号；它必须是用户看到的那个版本。
- 不要把 `unreachable` 展示成“已是最新”。
- 不要在 `state == "applying"` 时重复提交升级或回滚。
