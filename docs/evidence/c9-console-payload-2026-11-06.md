# C9 缺陷修复：升级载荷缺前端 `console/`（2026-11-06）

> 缺陷：升级包不含控制台构建产物时，升级器仍会把后端替换成新版，前端保持旧版。
> `/v1/health` 依旧 200，浏览器加载的却是旧界面；接口形状一变就表现为随机 400、
> 空面板或按钮无反应——**静默失败**，健康检查永远抓不到。
>
> 本文件只记这一条缺陷的修复与真实数字。C9 原口径（Docker / CD）仍是
> `NOT_VERIFIED`，判定未改动；分发形态的完整记录见
> [c9-installed-upgrade-2026-11-06.md](c9-installed-upgrade-2026-11-06.md)。

## 1. 修了什么

| 层 | 改动 | 目的 |
|---|---|---|
| 单一事实来源 | 新增 `src/gateway/contract.py`：`API_CONTRACT_VERSION = "1"`；`/v1/health` 新增字段 `api_contract` | 前端、后端、自检三方引用同一个值，不再各写各的 |
| 打包 | `packaging/stage_payload.py` 复制 `console/` 后写入签章；`packaging/make_release.py` 在安装树模式下**缺 `console/index.html` 直接拒绝产出**，并在写 ZIP 前重算签章、写完再用同一套规则回读校验 | 坏包在构建期就出不来，而不是等到用户机器上才发现 |
| 升级前门禁 | `src/upgrade/console_contract.py` + `src/upgrade/manager.py`：在**任何备份/改动之前**校验包内 `console/index.html`、签章、后端契约、控制台指纹、前端版本 | 不一致就拒绝升级，安装目录一个字节都不动 |
| 自检 | `src/upgrade/selfcheck.py`：落盘后校验安装目录 console 与后端一致，并把**真实启动的服务** `/v1/health` 返回的 `api_contract` 与签章比对 | 装完才发现错配也能自动回滚，而不是“升级成功” |
| 采集 | `src/upgrade/startup_probe.py` 解析 `/v1/health` JSON 正文，把 `health_body` 带进报告与 ledger | 契约比对有据可查，不靠日志猜 |

签章文件 `console/console-contract.json`（schema `liuhao.console-contract/1`）内容：

```json
{
  "schema": "liuhao.console-contract/1",
  "api_contract": "1",
  "frontend_version": "0.1.2",
  "console_fingerprint": "7dc9741d12380940b2c1aca05d4cb7bf9bcb1fd57effbe05f8bede40413596f6",
  "console_files": 11,
  "built_at": "2026-11-06T06:30:00Z"
}
```

开发/便携布局（安装根没有 `console/`）不适用该门禁，但 ledger 会记
`{"applicable": false, "checked": false, "note": ...}`，不静默跳过。

## 2. 证据：包体数字

两个包都来自同一条本地发布目录流程，**同一份源码**，差别只在安装树有没有 `console/`。

| 指标 | 旧包 `liuhao-0.1.1.zip`（缺陷包） | 新包 `liuhao-0.1.2.zip`（修复后） |
|---|---|---|
| ZIP 成员数 | **662** | **685** |
| 其中 `console/` 成员 | **0** | **12**（11 个构建产物 + 1 个签章） |
| ZIP 字节 | 2,741,388 | **2,961,979** |
| 解压后字节 | 9,461,145 | **10,047,927** |
| SHA256 | `1a56e7a83991841921c8b3496344fa0d5ce9ac425744b4e1691f5d6ca3af9c10` | `ca46fc7664bcea137b787adb3d88cd72c5d0c53dfe1fb3ed8b480c0033945d0a` |
| 构建输入 | 662 文件 | 683 文件 / 9.6 MB（未压缩） |
| 可复现 | — | **同参数复跑一次，SHA256 逐字节相同** |

构建日志：[c9b-build-0.1.2.txt](c9b-build-0.1.2.txt)（937 字节）。

## 3. 证据：拒绝旧包（零改动）

安装树 `D:\copilot-worktrees\liuhao-c9-installed-20261106`（版本标记 `0.1.0`，
`console/` 10 文件 / 496,026 字节，`index.html` SHA256 `B6CCEB84…E5DAB8`）。

`check` 正常识别到 0.1.1（[c9b-check-old-0.1.1.json](c9b-check-old-0.1.1.json)，675 字节），
随后 `upgrade` 被门禁拒绝：

```
失败：升级包缺少控制台构建产物 console/index.html：安装后界面会停留在旧版本（或直接空白），
已拒绝继续。请用包含前端的安装树重新打包：packaging/stage_payload.py → packaging/make_release.py --app-root。
```

| 检查 | 结果 |
|---|---|
| 退出码 | **1**（拒绝，不是警告） |
| `console/` | **10 文件 / 496,026 字节 / `B6CCEB84…E5DAB8`，与升级前完全一致** |
| 版本标记 | 仍为 `0.1.0` |
| 新增备份 | **0**（`upgrade-backups/` 仍为原有 2 项） |
| ledger | `kind=apply, status=failed`，`detail.mutation_started=false` |

拒绝原文：[c9b-reject-0.1.1.txt](c9b-reject-0.1.1.txt)（651 字节）。

## 4. 证据：装新包（前端真的变了）

| 步骤 | 真实数字 |
|---|---|
| `upgrade` 墙钟 | **18.72 s**（含下载、备份、安装、自检） |
| 备份 | `backup_id=20261106T055113Z-53a124b5` |
| 安装 | **685 文件**（与 ZIP 成员数一致） |
| 数据预检 | `data_checked_files=4` |
| 自检 | `self_check_passed=true`，`issues=0`；**真实启动服务 5.75 s**，`/v1/health` **200** |
| 自检读到的契约 | `api_contract="1"`，`console_files=11`，`console_fingerprint="7dc9741d…96f6"`，`running_api_contract="1"` |
| 版本标记 | `0.1.2` |
| **console 落盘后** | **12 文件 / 496,344 字节**，`index.html` SHA256 `6F4F0C53…F05F3C`（升级前为 `B6CCEB84…E5DAB8`） |
| 数据保全 | 哨兵 `c9-sentinel.txt` SHA256 `051F79B9…A4A9` 不变；用户库仍为 `(1, 'keep-across-upgrade-and-rollback')` |

升级结果：[c9b-upgrade-0.1.2.json](c9b-upgrade-0.1.2.json)（298 字节）；
自检与完整 ledger：[c9b-history-0.1.2.json](c9b-history-0.1.2.json)（17,794 字节）。

**真实重启（用安装树自带 runtime，不是仓库 venv）**

| 检查 | 结果 |
|---|---|
| 就绪耗时 | **5.07 s** |
| `GET /v1/health` | **200**，正文含 `"api_contract": "1"` |
| `GET /` | **200**，正文含 `<!-- frontend build 0.1.2 -->` |
| `GET /dashboard`（SPA 回退） | **200**，同一份新 index |
| `GET /assets/c9b-build-marker.js`（新构建新增的资源） | **200** |

健康正文：[c9b-health-0.1.2.json](c9b-health-0.1.2.json)（402 字节）；
重启日志：[c9b-restart-0.1.2.err.log](c9b-restart-0.1.2.err.log)（1,599 字节）、
[c9b-restart-0.1.2.out.log](c9b-restart-0.1.2.out.log)（62 字节）。

> 说明：重启用 `LIUHAO_CONSOLE_DIST=<tree>\console` 指向安装树产物，等同于
> `scripts/launch_installed.py:263-264` 在真实启动器里做的事。不设该变量时
> 网关会回落到仓库路径 `apps/console/console/dist`，在安装树上自然找不到——
> 这是探测方式的差异，不是产品缺陷。

## 5. 证据：回滚把前端也退回去了

| 步骤 | 真实数字 |
|---|---|
| `rollback` 墙钟 | **2.67 s** |
| 结果 | `0.1.2 → 0.1.0`，`restored_files=516` |
| `console/` 回滚后 | **10 文件 / 496,026 字节**，`index.html` SHA256 回到 `B6CCEB84…E5DAB8` |
| 新增文件残留 | **0**：`c9b-build-marker.js`、`console-contract.json` 都不在了 |
| 数据 | 哨兵 SHA256 不变，用户库行仍在 |

回滚结果：[c9b-rollback-0.1.0.json](c9b-rollback-0.1.0.json)（227 字节）。

> 与数据回滚的区别：`restore_code` 会先把整个安装根（含 `console/`）移到
> trash 再解压备份，所以**代码/前端是精确还原**；`restore_data` 仍是覆盖恢复，
> 备份之后新建的文件会留下。这一点不变，仍然披露。

## 6. 边界与未覆盖

| 边界 | 状态 |
|---|---|
| 前端构建 | 用**模拟构建**（`index.html` 追加 `<!-- frontend build 0.1.2 -->` + 新增 `assets/c9b-build-marker.js`），不是真实 `npm run build` 产物 |
| 真实 CD / 生产部署 | 未覆盖，见 C9 原口径 `NOT_VERIFIED` |
| 代码签名 | 未做，仅 SHA256 |
| 跨大版本契约变更（`api_contract` 1→2） | 只验证了同契约（1）的升级；契约变更时的拒绝由单测覆盖（签章 `api_contract` 与后端不等即拒绝） |
| 回滚后的旧前端与旧后端 | 一起回滚，一致；未单独演练“只回滚前端” |

## 7. 复现命令

```powershell
# 构建（安装树模式；缺 console/index.html 会直接失败）
.venv\Scripts\python.exe packaging\make_release.py `
  --app-root D:\copilot-worktrees\liuhao-c9b-payloadsrc `
  --version 0.1.2 --out D:\copilot-worktrees\liuhao-c9b-release `
  --released-at 2026-11-06T06:30:00Z --no-cli-check

# 升级 / 回滚（仓库 venv 跑升级器，app-root 指向真实安装树）
$t = "D:\copilot-worktrees\liuhao-c9-installed-20261106"
$env:LIUHAO_DATA_DIR = "$t\data"
$env:AUDIT_DB_PATH   = "$t\data\audit_store.db"
.venv\Scripts\python.exe -m src.upgrade upgrade  --app-root $t --release-dir D:\copilot-worktrees\liuhao-c9b-release
.venv\Scripts\python.exe -m src.upgrade rollback --app-root $t
```
