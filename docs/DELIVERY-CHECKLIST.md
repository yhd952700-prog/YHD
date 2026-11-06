# 交付清单（实测版）

> 生成时间：2026-11-05（本机时间）。
> 判定口径：只有「本轮亲手跑出结果」才写「已完成」；只有代码/文档但没跑过
> 真实链路的写「部分完成」或「未验证」；完全没做的写「未做」。
> 未做与未验证项均已登记到 [known-gaps.md](known-gaps.md)，不在本清单里
> 只挂一个名字。

## 0. 安装包

| 项目 | 值 |
|---|---|
| 安装包路径 | `dist/installer/LiuHao-Setup-0.1.0.exe` |
| 大小 | 34,199,800 字节（约 32.6 MiB） |
| SHA256 | `314839ED69F9B6D5F2C25B898C9BDFA5609E92B29B1D8DE11E79591D3B3125A2` |
| 校验文件 | `dist/installer/SHA256SUMS.txt` |
| 解包后体积 | 8,596 个文件 / 165.5 MB（84 个 dist 包） |
| 构建命令 | `packaging/build_windows_installer.ps1 -OutRoot <dir> -SkipFrontend -SkipSmoke` |
| 安装前必读 | `installer/assets/BEFORE-INSTALL.txt` |

已核对包内确实包含本轮修复：`scripts/first_run_wizard.py` 含
`deepseek-chat` 示例；`runtime/Lib/site-packages` 下
`openai / httpx2 / httpcore2 / truststore / jiter` 均存在。

**注意**：包内源码快照比当前 HEAD 早 12 个提交（不含 `/v1/startup` 启动门、
统一错误读取器、升级 API 等），交付前必须用当前脚本重建并复验（E-13）。

## 1. 三步使用说明（最终用户只需要这三步）

1. **下载并安装**：校验 SHA256 后双击 `LiuHao-Setup-0.1.0.exe`。Windows 提示
   「未知发布者」时选「更多信息 → 仍要运行」。装到 `%LOCALAPPDATA%\LiuHao\app`，
   不需要管理员权限。
2. **填 Key**：首次启动会打开命令行向导。先建管理员账号，再选 `1`（本机
   Ollama）或 `2`（OpenAI 兼容云）。选 2 时填 `base_url`（必须以 `/v1` 结尾，
   如 `https://api.deepseek.com/v1`）、模型名（如 `deepseek-chat`）、API Key。
   向导完成后浏览器自动打开 `http://127.0.0.1:8080`；端口被占用会自动顺延，
   以命令行窗口打印的地址为准。
3. **发第一条消息**：用刚建的账号登录，点左侧 **工作台**，输入一句话发送。

每一步失败时怎么办（端口被占、防火墙、Key 无效、模型名不对、服务没起来）
逐条写在 [END-USER-GUIDE.md](END-USER-GUIDE.md)，本文不重复。

## 1.1 与网关流「空数据目录、只用界面填 Key」演练的交叉核对

对照资产：[verify_first_run_e2e.py](../scripts/verify_first_run_e2e.py) +
[first-run-e2e.md](first-run-e2e.md)（commit `250456cd`）。

| 核对点 | 网关流说法 | 本流实测 | 结论 |
|---|---|---|---|
| 空数据目录、不预置模型 | 临时目录、`GET /v1/models`=0 | 安装版全新数据目录；bootstrap 用向导函数从零建身份、再登记模型 | 一致（入口不同：网关流走 HTTP/界面，本流走向导函数） |
| 运行中填 Key、不重启即生效 | 断言「立即生效」 | 本流是在网关启动**之前**由向导写入，没覆盖“运行中热添加” | 该项由网关流脚本覆盖，本流未复验 |
| 重启后配置还在 | 杀进程重启再对话成功 | 重启 8.8s，历史仍有用户消息与回复 | 一致 |
| 明文 Key 不落库 | 断言 SQLite 内无明文 | [secret-scan-2026-11-06.json](evidence/secret-scan-2026-11-06.json)：0 命中 | 一致 |
| 「测试连接」成功率 | 真实云 Key 时 `ok=true` | 本机回环桩被 SSRF 拒绝，但聊天正常 | **不是矛盾**：云端点走 SSRF 放行路径，本机非 Ollama 端点走拒绝路径（E-3） |
| 错误分类 | 断言 `invalid_key` 等 6 类 | 聊天侧 401/404/503 全部可见；测试连接分类在本机桩上触达不到 | 差一部分（见第 7 项） |
| 真实厂商 Key | 有脚本，需要 Key | 本流无 Key | 未验证（E-5） |

## 2. 逐项状态

| # | 项目 | 状态 | 证据 | 部分完成差哪一部分 |
|---|---|---|---|---|
| 1 | 四步用户文档 | 已完成 | [END-USER-GUIDE.md](END-USER-GUIDE.md)，commit `a97d5862` | — |
| 2 | 安装包构建 | 已完成 | 34,199,800 字节；SHA256 `314839…25A2`；payload 自检通过（8,596 文件 / 165.5 MB） | — |
| 3 | 真实安装（静默） | 已完成 | 本轮实测：安装 25.3 秒，落盘 8,599 个文件到安装目录 | — |
| 4 | 安装版首启 E2E | 已完成（本地桩；复用向导函数，未逐字键入） | [e2e-installed-2026-11-06.json](evidence/e2e-installed-2026-11-06.json)：建号+填 Key 8.7s、健康 8.4s、首条消息 2.4s 收到 `E2E-REPLY-OK from stub-model`、重启 8.8s 后历史仍在、总 33.2s | 差额：没有在命令行窗口里逐条键入向导答案，也未复验「自动打开浏览器」（E-9）；链路本身的所有组件函数与页面可达性都已验证 |
| 5 | 浏览器 UI 走查 | 已完成 | 登录 `e2e` → 左侧工作台 → 发「你好」，回复含 `E2E-REPLY-OK from stub-model`；3/3 判据 PASS | — |
| 6 | 端口占用自动顺延 | 已完成 | 同一份 E2E 日志：`[warn] 网关 端口 18130 被占用，已自动改用 18131` | — |
| 7 | 失败分支实测 | 部分完成 | [failure-modes-2026-11-06.json](evidence/failure-modes-2026-11-06.json)：错误 Key → `[生成失败] 401 Incorrect API key`；模型名错 → `404 model does not exist`；上游停掉 → `503`；服务没起 → `WinError 10061` 连接被拒 | 本机非 Ollama 端点走「测试连接」会被 SSRF 防护拒绝（聊天却可用），所以 **`invalid_key` 这类测试连接分类没能在这台机器上用桩复现**；该分类只有真实云 Key 场景与单测覆盖（见 E-3、E-5） |
| 8 | 打包延迟依赖修复 | 部分完成 | payload 内已含 `openai/httpx2/httpcore2/truststore/jiter`，安装版聊天报 `httpcore2` 缺包的问题已消失 | 修复在 `packaging/stage_payload.py`，但 `packaging/` 整体仍未纳入版本控制（E-1）；用旧脚本重建会退回缺包 |
| 9 | 真实厂商 Key 对话 | 未验证 | 网关流的可复现脚本 [verify_first_run_e2e.py](../scripts/verify_first_run_e2e.py)（commit `250456cd`，说明见 [first-run-e2e.md](first-run-e2e.md)）需要真实 Key；本流没有 Key，只用本地 OpenAI 兼容桩验证了真实 HTTP 请求路径 | 本流无真实厂商 Key 的原始输出（E-5）；网关流报称用 DeepSeek 跑过，只能算旁证 |
| 10 | 静默卸载 | 未验证 | 本轮实测：`unins000.exe /VERYSILENT` 与 `/S` 都停在“是否保留数据”确认框，进程需强制结束 | 交互式卸载未测；自动化卸载会被确认框挂住（E-4） |
| 11 | 代码签名 | 未做 | 安装包未签名，Windows 显示「未知发布者」 | 需证书与签名流水线（E-8） |
| 12 | 前端重建 | 未做 | 安装包内是构建时的 `console/dist`；前端仍在重写 | 前端稳定后必须重建安装包（E-7） |
| 13 | 能力地图 | 已完成 | [capability-map.md](capability-map.md)，commit `1ffe055a` | — |
| 14 | HC-01 证据分级 | 已完成 | [hc01-status.md](hc01-status.md)，commit `408caee2`（后续 `ff408f15` 只改遥测标注） | — |
| 15 | 缺口登记 | 已完成 | [known-gaps.md](known-gaps.md) 的 E-1～E-13（E-9～E-13 为本轮追加） | — |
| 16 | 全量回归 | 已完成（基线 `cc3af30d`，详见第 5 节） | C1/C2/C4/C5/C6/C8/C10 PASS；C3 `7334 passed / 46 skipped / 3 xfailed / 2 failed`；C7/C9 NOT VERIFIED；C11 BLOCKED；C12 NOT VERIFIED | 差额：基线落后报告时 HEAD 12 个提交，只补跑了那批提交改动的 15 个测试文件（`123 passed / 80.85s`），没有第二次全量（E-12） |

## 3. 「部分完成」的确切差额（汇总）

- **第 7 项 失败分支**：已实测 401 / 404 / 503 / 端口拒绝；**没有**在本机复现
  “测试连接 → `invalid_key` / `key_revoked` / `quota_exhausted`”三类，因为本机
  桩是回环地址，测试连接被 SSRF 防护拦下，而聊天不受影响。差额部分由
  `tests/test_gateway_models_api.py` 的归类单测与真实云 Key 场景覆盖。
- **第 8 项 打包修复**：产物已验证包含缺失依赖；差额只是**源码修复未提交**，
  不影响当前这个安装包，影响的是“下次用仓库脚本重建”。

## 4. 未做项去向

| 未做 / 未验证 | 去向 |
|---|---|
| 真实厂商 Key 对话 | E-5；补跑 `scripts/verify_first_run_e2e.py --api-key <key>` |
| 静默卸载 | E-4 |
| 代码签名 | E-8；依赖 D-1 的签名前提 |
| 前端重建 | E-7 |
| 打包修复提交 | E-1 |
| 本机端点测试连接与聊天校验不一致 | E-3（安全相关，本轮未改） |
| 打包冒烟只接受 Mock | E-6 |
| 向导交互逐字键入 + 自动开浏览器 | E-9 |
| 回归工作树放在 temp 下会让删除护栏失效 | E-10（已改用 temp 之外的干净工作树重跑） |
| 回环网络测试在满负载下抖动 | E-11 |
| 全量基线落后交付 HEAD 12 个提交 | E-12（已补跑改动测试，未再全量） |
| 安装包源码快照落后当前 HEAD | E-13（交付前必须重建） |

## 5. 全量回归（独立报告）

### 5.1 有效基线

- 工作树：`D:\copilot-worktrees\liuhao-regression`（**在 `tempfile.gettempdir()` 之外**，
  避免 E-10 的护栏失效），detached 到 `cc3af30d`。
- 命令：`python -u scripts/verify_readiness.py --full`。
- 测试：`7385 tests` → `7334 passed, 46 skipped, 3 xfailed, 2 failed, 0 errors`，
  耗时 `3834.71s (1:03:54)`。
- 逐关结果：`PASS=7 FAIL=0 BLOCKED=1 NOT_VERIFIED=4 ERROR=0`，
  `RELEASE READY = False`。
  - PASS：C1 编译/导入、C2 干净环境可启动、C4 无高危密钥且失败关闭、
    C5 审计链 32/32、C6 崩溃/恢复、C8 指标 48 家族、C10 独立验证。
  - NOT VERIFIED：C3（2 个失败未归零）、C7（本机 py3.12 无性能基线，数值门禁跳过）、
    C9（无真实 CD）、C12（2 个失败待归因）。
  - BLOCKED：C11（HC-01 等待人类裁决，属决策隔离，不是工程阻断）。
- 逐关原始输出：[regression-cc3af30d.txt](evidence/regression-cc3af30d.txt)。

### 5.2 两个失败的归因

| 失败 | 归因 | 证据 |
|---|---|---|
| `test_console_invariants.py::test_api_uses_the_shared_error_reader` | **测试断言过期，非产品问题**：前端在 `3510cab3` 把共享读取器拆成 `readError` + `readErrorParts`，断言仍只认 `readError`；上游 `26565ea2` 已把 `api.ts` 统一回 `readError` | 在 `de1b0d87` 上跑 `tests/test_console_invariants.py`：`11 passed in 1.92s` |
| `test_network.py::test_add_route_and_deliver` | **满负载下的回环 HTTP 抖动**，非产品回归 | 单跑该用例：`1 passed in 2.67s` |

### 5.3 基线之后的增量

`cc3af30d..de1b0d87` 共 12 个提交（desktop 启动门、升级 API、可观测性、
reminder 稳定性、console 错误读取器统一等）。这些提交改动的 14 个测试文件 +
1 个辅助文件补跑：`123 passed, 1 warning in 80.85s`。
JUnit：[delta-tests-de1b0d87.xml](evidence/delta-tests-de1b0d87.xml)。

### 5.4 作废的第一次尝试

同一 baseline 曾在 `D:\cache\temp\liuhao-regression` 跑过一次，因 E-10
（仓库位于 temp 根下、删除护栏整体失效）产生 `19 failed + 9 errors` 的
假失败并真的删掉仓库文件，已整轮作废；工作树已移到 temp 之外重跑。
作废日志：`D:\cache\temp\verify-full-e18169e6-INVALID.log`。

