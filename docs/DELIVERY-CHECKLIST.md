# 交付清单（实测版）

> 生成时间：2026-11-05（本机时间）；2026-11-06 更正安装包记录（见 0.1）。
> 判定口径：只有「本轮亲手跑出结果」才写「已完成」；只有代码/文档但没跑过
> 真实链路的写「部分完成」或「未验证」；完全没做的写「未做」。
> 未做与未验证项均已登记到 [known-gaps.md](known-gaps.md)，不在本清单里
> 只挂一个名字。

## 0. 安装包

**当前状态：无有效安装包（待最终包回填）。** `dist/installer/` 现在为空；
上一版写的 `dist/installer/LiuHao-Setup-0.1.0.exe` 在该路径下**从未存在**。
数值本身是真的，但取自一个临时构建，路径写错了——原记录已作废，见 0.1。

| 项目 | 值 |
|---|---|
| 安装包路径 | 待最终包回填（当前无有效安装包） |
| 大小 | 待最终包回填 |
| SHA256 | 待最终包回填 |
| 校验文件 | 待最终包回填 |
| **这版安装包对应的源码提交** | **待最终包回填** |
| 解包后体积 | 8,596 个文件 / 165.5 MB（84 个 dist 包）；来自历史 E2E 临时构建的 `manifest.json`，**仅作参考，不是交付物** |
| 构建命令 | `packaging/build_windows_installer.ps1 -OutRoot <dir> -SkipFrontend -SkipSmoke` |
| 安装前必读 | `installer/assets/BEFORE-INSTALL.txt`（存在，1,651 字节） |

### 0.1 历史记录更正与 E2E 产物溯源（2026-11-06）

**旧记录错在哪。** 上一版把
`D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe`
（34,199,800 字节 / `314839ED69F9B6D5F2C25B898C9BDFA5609E92B29B1D8DE11E79591D3B3125A2`）
的数值写成了 `dist/installer/LiuHao-Setup-0.1.0.exe`。数值是真的，路径是假的：
该文件从未被放进 `dist/installer`，`dist/installer` 现为空目录。

**“照着文档从零走一遍”实际装的是哪个文件。**

| 项目 | 事实 |
|---|---|
| 安装器路径 | `D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe` |
| 构建时间 | 2026-11-05 13:43:02（同目录 `SHA256SUMS.txt` 写于 13:43:04） |
| 字节数 / SHA256 | 34,199,800 字节 / `314839ED…B3125A2`（与 `SHA256SUMS.txt` 一致） |
| 安装目标 | `D:\cache\temp\liuhao-e2e\installed`（`unins000.exe` 13:43:15、`unins000.dat` 13:43:39 可证） |
| E2E 运行时间 | 2026-11-05 13:45:39（`driver-installed.log`），与证据 JSON 一致 |
| 现在还在不在 | **还在，但只在临时目录**；不在仓库、不在 `dist/`，不能作为交付物 |
| 能否从提交复现 | **不能。** payload 的 `scripts/launch_installed.py`（blob `a90272cf…`）不属于任何提交；`scripts/start_liuhao.py`（`e4df9332…`）直到 `797dbd16`（15:14）才入库，晚于构建时间 13:43 |

**结论：该 E2E 结果对应的产物已不可复现。** 证据 JSON 里的行为结论
（建号 8.7s、健康 8.4s、首条消息 2.4s、重启 8.8s 后历史仍在、端口 18130→18131 顺延）
可以保留，但它验证的是 **2026-11-05 13:43 的一份未提交工作区快照**，不是任何
可追溯的提交或最终安装包。交付时不能引用它作为“最终包已验证”的证据；最终包
必须重跑同一 E2E，并把结果绑定到「这版安装包对应的源码提交」。

**另一次安装（12:59，无 SHA）。** `dist/obsolete/installer-20261105-1506/install.log`
记录 `Original Setup EXE: D:\LiuHao-AI-OS\dist\installer\LiuHao-Setup-0.1.0.exe`，
12:59:46–13:00:22（约 34.7 秒），`-- File entry --` 8,478 条。该安装器**已不存在**，
日志里没有字节数与 SHA256，因此那次安装同样不可复现。上一版写的
“25.3 秒 / 落盘 8,599 个文件”无法从任何留存证据复核，本版不再保留这两个数。

**已作废包。** `dist/obsolete/installer-20261105-1506/LiuHao-Setup-0.1.0.exe`
（43,756,495 字节 / `D8FD0BC9FAC18092521284B93537520E2F7DBA8A45D4B9B6A2092D1C8A440461`，
构建于 2026-11-05 15:06:38）的 payload 三个文件均对不上任何提交
（`start_liuhao.py` / `manager.py` / `startup.py`，见同目录 `WHY-OBSOLETE.txt`），已作废。

## 1. 三步使用说明（最终用户只需要这三步）

1. **下载并安装**（最终包回填后生效）：校验 SHA256 后双击 `LiuHao-Setup-0.1.0.exe`。Windows 提示
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
| 2 | 安装包构建 | **已作废 / 待重出** | 上一版记录指向不存在的文件；当前 `dist/installer` 为空。构建脚本本身可用 | 见 0.1；待最终包回填路径/字节/SHA256 与对应源码提交 |
| 3 | 真实安装（静默） | 部分完成（历史实测，产物不可复现） | [install.log](../dist/obsolete/installer-20261105-1506/install.log)：`Original Setup EXE` = `D:\LiuHao-AI-OS\dist\installer\LiuHao-Setup-0.1.0.exe`，12:59:46–13:00:22（约 34.7 秒），`-- File entry --` 8,478 条 | 该安装器已不存在、日志无字节/SHA；旧记录的“25.3 秒 / 8,599 个文件”无法复核，已删 |
| 4 | 安装版首启 E2E | 已完成（结论保留；**产物不可复现**，本地桩、复用向导函数、未逐字键入） | [e2e-installed-2026-11-06.json](evidence/e2e-installed-2026-11-06.json)（1,553 字节）：建号+填 Key 8.7s、健康 8.4s、首条消息 2.4s 收到 `E2E-REPLY-OK from stub-model`、重启 8.8s 后历史仍在、总 33.2s。对应安装器为 `D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe`（34,199,800 / `314839ED…`），payload 含未提交文件 | 差额：没有在命令行窗口里逐条键入向导答案，也未复验「自动打开浏览器」（E-9）；且安装器无法从任何提交复现（E-13） |
| 5 | 浏览器 UI 走查 | 已完成 | 登录 `e2e` → 左侧工作台 → 发「你好」，回复含 `E2E-REPLY-OK from stub-model`；3/3 判据 PASS | — |
| 6 | 端口占用自动顺延 | 已完成 | 同一份 E2E 日志：`[warn] 网关 端口 18130 被占用，已自动改用 18131` | — |
| 7 | 失败分支实测 | 部分完成 | [failure-modes-2026-11-06.json](evidence/failure-modes-2026-11-06.json)：错误 Key → `[生成失败] 401 Incorrect API key`；模型名错 → `404 model does not exist`；上游停掉 → `503`；服务没起 → `WinError 10061` 连接被拒 | 本机非 Ollama 端点走「测试连接」会被 SSRF 防护拒绝（聊天却可用），所以 **`invalid_key` 这类测试连接分类没能在这台机器上用桩复现**；该分类只有真实云 Key 场景与单测覆盖（见 E-3、E-5） |
| 8 | 打包延迟依赖修复 | 已完成 | payload 内已含 `openai/httpx2/httpcore2/truststore/jiter`；修复随 `b789d244` 入库，安装版聊天不再报 `httpcore2` 缺包 | 已解除（E-1）；该目录另有未提交的升级元数据改动，与依赖修复无关 |
| 9 | 真实厂商 Key 对话 | 未验证 | 网关流的可复现脚本 [verify_first_run_e2e.py](../scripts/verify_first_run_e2e.py)（commit `250456cd`，说明见 [first-run-e2e.md](first-run-e2e.md)）需要真实 Key；本流没有 Key，只用本地 OpenAI 兼容桩验证了真实 HTTP 请求路径 | 本流无真实厂商 Key 的原始输出（E-5）；网关流报称用 DeepSeek 跑过，只能算旁证 |
| 10 | 静默卸载 | 未验证 | 本轮实测：`unins000.exe /VERYSILENT` 与 `/S` 都停在“是否保留数据”确认框，进程需强制结束 | 交互式卸载未测；自动化卸载会被确认框挂住（E-4） |
| 11 | 代码签名 | 未做 | 安装包未签名，Windows 显示「未知发布者」 | 需证书与签名流水线（E-8） |
| 12 | 前端重建 | 未做 | 安装包内是构建时的 `console/dist`；前端仍在重写 | 前端稳定后必须重建安装包（E-7） |
| 13 | 能力地图 | 已完成 | [capability-map.md](capability-map.md)，commit `1ffe055a` | — |
| 14 | HC-01 证据分级 | 已完成 | [hc01-status.md](hc01-status.md)，commit `408caee2`（后续 `ff408f15` 只改遥测标注） | — |
| 15 | 缺口登记 | 已完成 | [known-gaps.md](known-gaps.md) 的 E-1～E-13（E-9～E-13 为本轮追加） | — |
| 16 | 全量回归 | 已完成（基线 `bb6b654c`，详见 5.1；**不是最终 HEAD 的交付凭证**） | `7458 tests, 0 failed, 0 errors, 58 skipped`；C1/C2/C3/C4/C5/C6/C8/C10/C12 PASS；C7/C9 NOT VERIFIED；C11 BLOCKED；`PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY=False`；日志 `verify-full-bb6b654c.log`（2,303 字节，exit 0） | 差额：基线 `bb6b654c` 之后 HEAD 已推进到 `d2a62b0e`，最终 hash 冻结后需再跑一轮全量作为交付凭证；本轮只作基线覆盖 |

## 3. 「部分完成」的确切差额（汇总）

- **第 7 项 失败分支**：已实测 401 / 404 / 503 / 端口拒绝；**没有**在本机复现
  “测试连接 → `invalid_key` / `key_revoked` / `quota_exhausted`”三类，因为本机
  桩是回环地址，测试连接被 SSRF 防护拦下，而聊天不受影响。差额部分由
  `tests/test_gateway_models_api.py` 的归类单测与真实云 Key 场景覆盖。
- **第 8 项 打包修复**：已解除。产物包含全部缺失依赖，源码修复随 `b789d244`
  入库，用仓库脚本重建也不会再退回缺包。

## 4. 未做项去向

| 未做 / 未验证 | 去向 |
|---|---|
| 真实厂商 Key 对话 | E-5；补跑 `scripts/verify_first_run_e2e.py --api-key <key>` |
| 静默卸载 | E-4 |
| 代码签名 | E-8；依赖 D-1 的签名前提 |
| 前端重建 | E-7 |
| ~~打包修复提交~~ | E-1 已解除（`b789d244`） |
| 本机端点测试连接与聊天校验不一致 | E-3（安全相关，本轮未改） |
| 打包冒烟只接受 Mock | E-6 |
| 向导交互逐字键入 + 自动开浏览器 | E-9 |
| 回归工作树放在 temp 下会让删除护栏失效 | E-10（已改用 temp 之外的干净工作树重跑） |
| 回环网络测试在满负载下抖动 | E-11 |
| 全量基线落后交付 HEAD 12 个提交 | E-12（已补跑改动测试，未再全量） |
| 安装包记录曾指向不存在的文件，当前无有效安装包 | E-13（待最终包回填，并写死对应源码提交后重跑 E2E） |

## 5. 全量回归（独立报告）

### 5.1 本轮全量（基线 `bb6b654c`）

- 基线提交：`bb6b654c16265cb78a0f247253517d949990bbaf`。
  worktree：`D:\copilot-worktrees\liuhao-final-bb6b654c`（`git status` 为空，在 temp 根之外）。
- 解释器：`d:\LiuHao-AI-OS\.venv\Scripts\python.exe`（py3.12.14）；
  命令 `python -u scripts/verify_readiness.py --full`。
- 日志：`D:\cache\temp\verify-full-bb6b654c.log`（2,303 字节，进程退出码 0）；
  仓库内逐字节副本 [regression-bb6b654c.txt](evidence/regression-bb6b654c.txt)（2,303 字节）。
- 测试：`7458 tests` → `0 failed, 0 errors, 58 skipped`。
- 逐关结果：`PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY = False`。
  - PASS（9）：C1 编译/导入、C2 干净环境可启动、C3 稳定测试、C4 无高危密钥且失败关闭、
    C5 审计链 32/32、C6 崩溃/恢复、C8 指标 48 家族、C10 独立验证、C12 无未解释的重大失败。
  - NOT VERIFIED（2）：C7 数值性能门禁在本机 profile 无基线（SKIP，不变量仍检查）、
    C9 无真实 CD（Dockerfile + alembic 在）。
  - BLOCKED（1）：C11 HC-01 等待人类裁决（决策隔离，不是工程阻断）。
- **失败归因：无。** `FAIL=0`，C3/C12 均 0 failed，不存在需要区分“假失败/真红”的条目。
- **口径**：`bb6b654c` 不是最终 HEAD（当时 HEAD 已到 `d2a62b0e`，之后还有安全修复、
  管家链路、多账号体系提交）。本轮只是「基线覆盖」，**不是最终交付凭证**；
  最终 hash 冻结后必须再跑一轮全量。

### 5.2 覆盖表与增量（`bb6b654c`）

- 覆盖方式与逐提交状态：[regression-baseline-coverage-2026-11-06.md](evidence/regression-baseline-coverage-2026-11-06.md)。
- 增量核验（**只用于快速核验，不替代全量**）：`531 passed, 1 skipped, 0 failed`，
  332.36 秒；JUnit [regression-delta-bb6b654c.xml](evidence/regression-delta-bb6b654c.xml)（532 条）。
- 增量范围：upgrade 全部 10 个文件、desktop 启动门、UTF-8 日志、gate vars、
  tasks/reminders、automation、intel、selfmod、cli、gateway；覆盖
  `bc1d7b2b`、`747af1fd`、`bf062ee8`、`d7ee01b3` 与 7 条 lint 触及的模块。
- 增量与全量的关系：全量已包含上述全部提交，增量只是定向复核，**不能**把两者相加说成
  “全量覆盖最终 HEAD”。
- 基线之后的两个提交（`e0163d59`、`d2a62b0e`，合计只动 2 个文件）在 `d2a62b0e` 上做定向增量：
  `tests/artifacts` `30 passed in 5.15s`（退出码 0）、`scripts/verify_policy_c1.py` `RESULT: ALL GREEN`；
  日志 [regression-delta-d2a62b0e.txt](evidence/regression-delta-d2a62b0e.txt)（104 字节）、
  [regression-c1-d2a62b0e.txt](evidence/regression-c1-d2a62b0e.txt)（8,356 字节）。
  **这仍不是全量**：没有重跑 7458 条测试，也没有重跑 C2/C4/C5/C6/C8/C10 的端到端门禁。

### 5.3 上一轮全量（基线 `cc3af30d`，历史，已被 5.1 取代）

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

### 5.4 两个历史失败的归因（上一轮 `cc3af30d`）

| 失败 | 归因 | 证据 |
|---|---|---|
| `test_console_invariants.py::test_api_uses_the_shared_error_reader` | **测试断言过期，非产品问题**：前端在 `3510cab3` 把共享读取器拆成 `readError` + `readErrorParts`，断言仍只认 `readError`；上游 `26565ea2` 已把 `api.ts` 统一回 `readError` | 在 `de1b0d87` 上跑 `tests/test_console_invariants.py`：`11 passed in 1.92s` |
| `test_network.py::test_add_route_and_deliver` | **满负载下的回环 HTTP 抖动**，非产品回归 | 单跑该用例：`1 passed in 2.67s` |

### 5.5 上一轮基线之后的增量

`cc3af30d..de1b0d87` 共 12 个提交（desktop 启动门、升级 API、可观测性、
reminder 稳定性、console 错误读取器统一等）。这些提交改动的 14 个测试文件 +
1 个辅助文件补跑：`123 passed, 1 warning in 80.85s`。
JUnit：[delta-tests-de1b0d87.xml](evidence/delta-tests-de1b0d87.xml)。

### 5.6 作废的第一次尝试

同一 baseline 曾在 `D:\cache\temp\liuhao-regression` 跑过一次，因 E-10
（仓库位于 temp 根下、删除护栏整体失效）产生 `19 failed + 9 errors` 的
假失败并真的删掉仓库文件，已整轮作废；工作树已移到 temp 之外重跑。
作废日志：`D:\cache\temp\verify-full-e18169e6-INVALID.log`（存在，3,498 字节）。

## 6. 产物核对（2026-11-06 全篇扫描）

> 逐个核实清单里引用的每个产物：文件是否存在、字节是否对得上。
> `known-gaps.md` 同批扫描：未引用任何具体产物文件（无 `.exe` / `.zip` / `evidence/` 路径命中），无需更正。

| 引用位置 | 引用的产物 | 记录值 | 实测 | 结论 |
|---|---|---|---|---|
| §0 | `dist/installer/LiuHao-Setup-0.1.0.exe` | 34,199,800 / `314839ED…` | **不存在**（`dist/installer` 为 0 条目） | **已更正**：改为“待最终包回填”；真实数值属于 temp 构建（见 0.1） |
| §0 | `dist/installer/SHA256SUMS.txt` | — | **不存在** | **已更正**：改为“待最终包回填” |
| §0 | `installer/assets/BEFORE-INSTALL.txt` | — | 存在，1,651 字节 | 一致 |
| §2 #2 | 安装包 + payload manifest | 34,199,800 / 8,596 文件 / 165.5 MB | 仓库内无；temp `manifest.json` 与数值一致 | **已更正**：标“已作废 / 待重出” |
| §2 #3 | `install.log` | 25.3 秒 / 8,599 文件 | 存在于 `dist/obsolete/installer-20261105-1506/`，8,478 条 File entry，34.7 秒 | **已更正**：改按日志实测值，并注明不可复现 |
| §2 #4 / §1.1 / §5 | `docs/evidence/e2e-installed-2026-11-06.json` | — | 存在，1,553 字节 | 一致（产物溯源见 0.1） |
| §2 #7 | `docs/evidence/failure-modes-2026-11-06.json` | — | 存在，1,856 字节 | 一致 |
| §1.1 | `docs/evidence/secret-scan-2026-11-06.json` | — | 存在，346 字节 | 一致 |
| §2 #9 | `scripts/verify_first_run_e2e.py` / `docs/first-run-e2e.md` | — | 存在，20,237 / 3,559 字节 | 一致 |
| §2 #1 | `docs/END-USER-GUIDE.md` | — | 存在，9,107 字节 | 一致 |
| §2 #13 | `docs/capability-map.md` | — | 存在，18,738 字节 | 一致 |
| §2 #14 | `docs/hc01-status.md` | — | 存在，7,651 字节 | 一致 |
| §2 #15 | `docs/known-gaps.md` | — | 存在，27,684 字节 | 一致 |
| §5.3 | `docs/evidence/regression-cc3af30d.txt` | — | 存在，2,430 字节 | 一致（上一轮全量原始输出） |
| §5.5 | `docs/evidence/delta-tests-de1b0d87.xml` | — | 存在，15,351 字节 | 一致 |
| §5.6 | `D:\cache\temp\verify-full-e18169e6-INVALID.log` | — | 存在，3,498 字节（仓库外，仅追溯） | 一致 |
| §5.1 | `docs/evidence/regression-bb6b654c.txt` | 2,303 字节 | 存在，2,303 字节 | 一致（`verify-full-bb6b654c.log` 的逐字节副本） |
| §5.2 | `docs/evidence/regression-baseline-coverage-2026-11-06.md` | — | 存在，6,665 字节 | 一致（覆盖表本体） |
| §5.2 | `docs/evidence/regression-delta-bb6b654c.xml` | 532 条 | 存在，67,855 字节 | 一致 |
| §5.2 | `docs/evidence/regression-delta-d2a62b0e.txt` | — | 存在，104 字节 | 一致 |
| §5.2 | `docs/evidence/regression-c1-d2a62b0e.txt` | — | 存在，8,356 字节 | 一致 |
| §0 / §2 #2 | `D:\cache\temp\liuhao-e2e\build\installer\manifest.json` | 8,596 文件 / 165.5 MB / 84 dist | 存在，数值一致 | 一致（非交付物，仅溯源） |

