# 交付清单（实测版）

> 生成时间：2026-11-05（本机时间）；2026-11-06 更正安装包记录（见 0.1）；
> 2026-11-07 交付面审计后更新 §0 当前产物与体积口径。
> 判定口径：只有「本轮亲手跑出结果」才写「已完成」；只有代码/文档但没跑过
> 真实链路的写「部分完成」或「未验证」；完全没做的写「未做」。
> 未做与未验证项均已登记到 [known-gaps.md](known-gaps.md)，不在本清单里
> 只挂一个名字。

> **一眼可见的披露（2026-11-06，无需翻到 §5.7）**
>
> 1. **HC-01 保管链断裂**：冻结快照在取证之后被**未查明身份的进程**以读写方式打开
>    （`-wal` / `-shm` 的 mtime 晚于取证时刻）。可证的是主文件字节、`page_count`、
>    freelist 未变，`-wal` 0 字节，`integrity_check=ok`；**不可证的是行为人是谁**。
>    本清单及所有引用该快照的文档一律写作「**保管链断裂、行为人未查明**」。
>    裁决见 [ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)。
> 2. **C11 已裁决（ADR-HC01）**：5 项裁决由 owner 拍定并记录
>    [ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)。机器门 `check_hc01`
>    仍按冻结的人类主权门设计打印 `BLOCKED`——这是机器口径，不是「裁决未做」；工程侧
>    不改判定，也不为了好看去调阈值。
> 3. **`RELEASE READY = False` 的理由只剩 C9**：桌面分发形态没有真实 CD 环境可验，
>    属**形态差异**，不是产品缺陷。安装树的真实升级/回滚（`c9b-*` 证据）已覆盖交付需求，
>    但**不等于「真实 CD 已验过」**，本条不写成「已全部验证」。

## 0. 安装包

**当前状态（2026-11-08 实测）：`dist/installer/` 顶层 0 个 exe、0 个 zip。**
顶层只剩 `README-DELIVERY.txt` 与 `_archive\`；`_archive\20261107-mixed-0.1.0-and-0.1.1\`
里的 0.1.0 / 0.1.1 已按 `README-DELIVERY.txt` 判定为不合格成品，只作追溯，不得分发。
**本轮全部修复都还没进包。** 出包是下文 §0.3 的两个独立节点，不是「再打一次」这么简单。

| 项目 | 值 |
|---|---|
| 安装包路径 | 无（顶层 0 个 exe，等待重建） |
| 大小 / SHA256 | 无（尚未构建，不复用历史数值） |
| 校验文件 | 无（构建时由 `package-frozen.ps1` 生成 `SHA256SUMS.txt` / `DELIVERY.txt`） |
| 历史归档（只追溯） | `dist/installer/_archive/20261107-mixed-0.1.0-and-0.1.1/`：0.1.0 exe 34,731,647 字节、0.1.1 exe 34,830,483 字节，**均不得对外分发** |
| **这版安装包对应的源码提交** | 无；出包时由 `DELIVERY.txt` 写死 HEAD，并回填本表 |
| 安装后体积 | 仅 0.1.0 历史实测 10,502 个文件 / 170,666,366 字节（162.8 MiB），新包必须重测 |
| 构建命令 | `scripts/release/package-frozen.ps1`（唯一允许写 `dist/installer` 的入口） |
| 安装前必读 | `installer/assets/BEFORE-INSTALL.txt`（含 Key 申请与跳过说明） |
| 第三方许可证 | 由 `packaging/third_party_notices.py` 在构建时按 payload 实装生成，连同 `packaging/licenses/` 复制到程序根目录 |

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
（建号 8.7s、健康 8.4s、首条消息 2.4s、重启 8.8s 后历史仍在、端口 18130→18131 顺延；
最后一项是旧版行为，2026-11-08 已改为明确报错、不自动顺延）
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

### 0.2 安装冒烟验收清单（内部基线包专用，2026-11-08）

> **本包定性：安装冒烟包，不是候选发布版，不得对外。**
> 包内说明与 `DELIVERY.txt` 必须原样写上这句实话：
> 「本包验的是安装器 / 桌面 / 首启 / 回环写入链路。**已知问题：系统资源紧张时可能出现
> 请求全部挂起、登录转圈，需要重启。正式修复未入包。**」
> 宁可这段话难看，也不能让验收人以为装完就没有这个问题。

逐条照着做；每条都写「算过 / 算不过 / 留什么证据」。装到 **D 盘**，不要装 C 盘。

| # | 项目 | 怎么做 | 算过 | 算不过 | 证据 |
|---|---|---|---|---|---|
| S-01 | 安装位置 | 双击安装包，保持默认目录 `D:\LiuHao`，数据目录 `D:\LiuHaoData` | 程序与数据都在 D 盘；`D:\LiuHao\data-dir.txt` 指向 `D:\LiuHaoData` | 落到 `C:\Users\...` 或 `Program Files`；`data-dir.txt` 缺失/指向别处 | 两个目录的 `Get-ChildItem` 输出 + `data-dir.txt` 截图 |
| S-02 | 图标正确 | 看桌面与开始菜单快捷方式 | 显示鎏灏图标（`{app}\assets\liuhao.ico`），双击能启动；快捷方式目标为 `runtime\pythonw.exe` + `scripts\liuhao_desktop.py app --open-browser` | 空白/白纸/齿轮/默认 exe 图标；目标指向测试或走查目录；双击报找不到文件 | 快捷方式属性截图（目标+图标）+ 桌面截图 |
| S-03 | 首启向导 | 首次启动，逐字填完五步：数据目录 → 管理员账号 → 模型（含端口）→ 确认 → 自检 | 图形窗口（不是黑命令行），五步都能「下一步/上一步」；端口被占时出现「换一个端口」；自检结果如实显示 | 只弹黑窗口；卡在某一步；端口被占只报错无二选一；向导写不出数据目录 | 每步截图 + 向导日志路径 |
| S-04 | 浏览器自动打开 | 向导完成后不要手动点任何东西 | 默认浏览器自动打开 `http://127.0.0.1:<端口>`，登录页可见 | 没自动打开；打开的是空白页/旧缓存页；端口与向导填写不一致 | 浏览器地址栏截图 + 启动日志中的监听端口行 |
| S-05 | 能登录进去 | 用向导里建的账号登录 | 登录后落在「总览」，左侧导航可见；刷新后仍保持登录 | 一直转圈（见 S-11）、401、白屏、登录后又跳回登录页 | 登录请求的 Network 状态码 + 页面截图 |
| S-06 | 真实 DeepSeek Key，不重启即用 | 保持后端运行，进 **系统设置 → 模型管理**，填 `https://api.deepseek.com/v1`、`deepseek-chat`、真实 Key，保存后直接去对话 | 不重启后端，60 秒内收到真实回复；日志显示调用了真实 provider（不是 mock） | 必须重启才生效；仍走 mock；401/404/503 且换 Key 后依旧 | 回复截图 + 日志中的 provider 行 + Key 脱敏后的配置截图 |
| S-07 | 回环写入正常 | 在同一浏览器里做三次写操作：登录、发一条消息、建一个目标 | 全部成功；服务端日志 `SECURITY_REJECTED` 计数为 0（尤其无 `cross_site_write`） | 任一写入 403 `cross_site_write`；只有读能过、写全挂 | 三次请求的状态码 + 日志检索输出：`Select-String SECURITY_REJECTED` |
| S-08 | Key 不落明文 | 在 `D:\LiuHaoData` 下检索真实 Key 前缀 | 只命中脱敏形式（`sk-***`），无完整 Key | 任一处出现完整 Key | 检索命令与命中行（命中即打码） |
| S-09 | 关窗驻留 / 退出 | 关掉状态窗口，再发一条消息；然后从托盘「退出」 | 关窗后服务继续可用；托盘退出后进程消失、端口释放 | 关窗即断服；退出后仍有 `pythonw.exe` 占端口 | 进程列表与端口监听输出 |
| S-10 | 卸载干净 | 先用交互式卸载（默认保留数据），再用 `/VERYSILENT` 跑一次静默卸载 | 交互式卸载完成；静默卸载不挂住、退出码 0；程序目录只剩 `licenses\` / `THIRD-PARTY-NOTICES.txt` / `sbom-cyclonedx.json`（条款要求保留）；数据目录按默认保留；无残留进程、无自启动项、无快捷方式 | 静默卸载卡在「是否保留数据」；残留 `pythonw.exe`；自启动项仍在；快捷方式指向已删文件 | 卸载日志 + 卸载后目录树 + `Get-Process` / 注册表 Run 键输出 |
| S-11 | 已知问题：登录转圈（观测项，不是通过项） | 见下方取证流程；**撞上就记命中，不许重试到过为止** | 没撞上：全程无 60 秒以上转圈，记「本次未复现」 | 撞上：按取证流程留证，记「已知问题命中」，本条不判通过 | 见下方五样证据 |

**S-11 登录转圈取证流程（撞上时立刻做，先取证再决定是否重启）：**

1. 记下时刻、当前操作（登录 / 发消息）、页面是否一直转圈。
2. 浏览器开发者工具 → Network：`/v1/auth/login` 或 `/v1/chat` 是否长期 `pending`，记已等待秒数；Console 有无报错。
3. CPU 采样：`Get-Process pythonw | Select-Object Id,CPU` 间隔 5 秒连采两次；增量 <0.1 秒即记「CPU 接近 0」。
4. 探活：`Invoke-WebRequest http://127.0.0.1:<端口>/v1/health -TimeoutSec 5`；超时或非 200 记 `hung`。再试 `/v1/ready`。注意 `/v1/health` 是纯内存路径，即使它返回 200、业务请求仍挂起，也照样记命中。
5. 不重启，观察是否在 6 分钟内自愈；≥6 分钟仍不可用记「未自愈」。
6. 留证：`D:\LiuHaoData\logs` 原样复制、进程 PID、截图、上述命令输出。**这些就是这条已知问题的现场，不要清掉。**

### 0.3 两次出包节点（2026-11-08 起）

| 节点 | 什么时候能开打 | 包里有什么 | 明确缺什么 | 对外口径 |
|---|---|---|---|---|
| **A. 安装冒烟包**（内部） | 流式线 `a81d1ecb` 交回并逐文件对账无遗漏、其文件已提交；工作树达到「打包前置必须清零项已清零」 | 安装器 / 桌面 / 首启向导 / 回环写入链路；流式兜底修复 | **满载僵死的第二条根因修复**（限流中间件在事件循环上同步查 JWT 撤销库的 SQLite）；`health.py` / `test_gateway_health_wiring.py` / `soak-report` 三项；`tests/consistency/` 三连跑凭证；30 分钟满载窗口结论 | **只做安装冒烟，不是候选发布版**；包内必须带 §0.2 顶部那句已知问题说明 |
| **B. 终版** | 三条修复入库（`Thread.start` 非阻塞、限流中间件移出事件循环、探针/线程池相关）+ **30 分钟满载窗口达标** + `tests/consistency/` 连跑 3 次全过 + preflight 达标（15 PASS / 2 SKIP / 0 FAIL） | 全部修复；同源前端；`SHA256SUMS.txt` / `DELIVERY.txt` 绑定源码提交 | 无（未达标则不出终版） | 唯一可对外候选 |

> **红线：30 分钟满载窗口不达标，就只出安装冒烟包，不出终版。**
> 「满载僵死已修复」这句话在 2026-11-08 已作废：软测第三次跑到 21.8 分钟又僵死、
> 6 分 41 秒不自愈；8 小时数据不存在（`rss8h5` 已于 19:46 被终止，实际只有 22 分钟）。
> 真因两条：① `Thread.start` 无限等；② 限流中间件在事件循环上同步查 JWT 撤销库 SQLite。

### 0.3.1 打包前置预演结果（2026-11-08，临时 worktree，只读）

预演环境：`git worktree add --detach D:\cache\temp\liuhao-packrehearsal-aa881ca0 aa881ca0`，
解释器复用 `D:\LiuHao-AI-OS\.venv\Scripts\python.exe`。**未构建前端、未组 payload、未出包。**

`preflight.py`（17 项，84.3s）：**PASS 12 / SKIP 3 / FAIL 2**。

| 结论 | 项目 |
|---|---|
| PASS | 工作树干净；`import src.gateway.main`；测试收集 8,582 条；OpenAPI 140 paths 对账；应用实例化卫生；全量导入 2 passed；PowerShell 5.1 解析 3 个脚本；品牌门禁；SBOM/许可证登记；SBOM 无绝对路径；日志泄漏（新工作树无目标）；`dist\installer` 无候选 |
| SKIP | 安装副本同源（未提供 `--install-dir`）；无障碍验收（未启 `--a11y`）；payload SBOM（`sbom_audit.py` 随构建产出） |
| FAIL ① | **HEAD 没有上游分支**——这是 detached worktree 的假象；真实仓库实测 `origin/p36` 领先 **69 个提交**，出包前必须 push |
| FAIL ② | **缺 `apps/console/console/dist/index.html`**——干净工作树没有构建产物，属预期；出包前必须先构建前端 |

`freeze_checks.py` 子项（`package-frozen.ps1` 的硬门禁）：

| 子项 | 结果 | 处置 |
|---|---|---|
| `deps` | **FAIL：`requests`、`urllib3` 没有被 import 图带上** | **必须清零。** 真因：`packaging/stage_payload.py` 的 `_MEASURE_CODE` 没有 `import requests`，而 `src/ai/model_discovery.py` 顶层 `import requests`、`src/ai/providers.py` 多处延迟导入。修法：在 `_MEASURE_CODE` 里加一行 `import requests  # noqa: F401`（会顺带量到 `urllib3`），否则 `package-frozen.ps1` 第 3 步直接拒绝出包 |
| `env` | PASS | 本机 `LIUHAO_*` 无污染 |
| `frontend-source` | PASS（本机无基线，首次冻结会写入） | 出包时确认基线对应的就是冻结提交 |
| `runbook` | PASS（`dist` 副本尚不存在） | 出包后复跑 |
| `log-leak` | PASS（新工作树无日志目标） | 安装后用 `--data-dir` 对真实数据目录复扫 |

**分类结论（用户点名的两条已知红）：**

- **29 项未提交改动 → 必须清零。**（19:58 复核已增至 **36 项**，且流式线 / 知识库线 / 软测线仍在写。）`package-frozen.ps1` §1.1 要求工作树干净，冻结包必须能由一个提交完整重建；这批改动只能先按归属线提交，不能带进包里。
- **领先远端 69 个提交 → 必须清零。** 出包前 push；否则 `dist\installer` 的产物无法从远端提交复现，`DELIVERY.txt` 绑定的提交也无法被他人取到。

**除此之外只发现上面 `deps` 一条真实代码级门禁坑**；其余 FAIL 都是「工作树还没构建、还没 push」的预期状态。

### 0.4 远端与 git 元数据（已知项，2026-11-08 实测）

| 项目 | 实测 |
|---|---|
| 当前分支 / 上游 | `p36` → `origin/p36` |
| 领先 / 落后 | **不写死数字**，以 `git rev-list --count '@{u}..HEAD'`（领先）与 `git rev-list --count 'HEAD..@{u}'`（落后）的当次实测为准 |
| 安装包是否依赖 git 元数据 | **不依赖**（结论见下） |
| 安装包是否依赖远端可达性 | **不依赖**（结论见下） |

- **交付标识以本机提交号 + 安装包 SHA256 为准。** `DELIVERY.txt` 里的 `source_commit`
  由构建机在打包时写成静态文本（`package-frozen.ps1` 第 378 行 `git rev-parse HEAD`），
  安装后的程序不读 git。
- **不配置伪远端，也不改 preflight 让上游检查变绿。** push 之前 `preflight` 的
  「上游分支 / 领先提交」检查会保持 FAIL，这是真实状态，按已知项登记，不按缺陷掩盖。
- 领先 / 落后的**具体数字不写进文档**（写一次就漂一次）；要报数字时当场跑
  `git rev-list --count '@{u}..HEAD'` 与 `git rev-list --count 'HEAD..@{u}'`。
  push 前必须先合并远端新提交，不能直接强推。

**安装包不依赖 git / 远端的依据（逐项核过）：**

1. payload 不含 `.git`：`packaging/stage_payload.py` 只复制 `src/`、`config/`、
   `console/`（构建产物）、`scripts/`（`PAYLOAD_SCRIPTS` 白名单）、`runtime/` 与
   `pyproject.toml` / `requirements.txt` / `oss-registry.yaml`。
2. 运行期版本来自 `pyproject.toml` 与 `console-contract` 签章 JSON
   （`src/upgrade/console_contract.py`），不调用 `git`。
3. 构建期 `git` 只出现在 `scripts/release/preflight.py`、
   `scripts/release/package-frozen.ps1`、`scripts/independent_verification.py`；
   这三个脚本**不随包安装**，只在构建机上跑。
4. 升级检查只在显式配置 `--manifest-url` 时发 HTTP 请求，默认关闭（与
   `BEFORE-INSTALL.txt` 第 4 条一致）；`src/upgrade/manager.py` 只是把 `.git`
   列入安装排除名单，不读取它的内容。

## 1. 三步使用说明（最终用户只需要这三步）

1. **下载并安装**（最终包回填后生效）：校验 SHA256 后双击 `LiuHao-Setup-<版本号>.exe`。
   Windows 提示「未知发布者」时选「更多信息 → 仍要运行」。程序默认装到
   `D:\LiuHao`（数据 `D:\LiuHaoData`），不需要管理员权限；D 盘不可用时自动回退到
   `%LOCALAPPDATA%\Programs\LiuHao`，实际位置见程序目录 `data-dir.txt`。
   老版本用户覆盖安装保持原位置不变。
2. **配置模型（可跳过）**：首次启动会打开命令行向导。先建管理员账号，再选
   `1`（本机 Ollama）、`2`（OpenAI 兼容云）或 `0`（跳过）。
   选 2 时填 `base_url`（必须以 `/v1` 结尾，如 `https://api.deepseek.com/v1`）、
   模型名（如 `deepseek-chat`）、API Key（DeepSeek 到 <https://platform.deepseek.com>
   创建）。没有 Key 就选 `0`，之后在 **系统设置 → 模型管理** 补上。
   向导完成后服务继续启动，浏览器会尝试打开 `http://127.0.0.1:8080`；
   没自动打开就手动访问。端口默认 `8080`：**首启向导里可以换端口；日常启动如果端口被占，
   会告诉你占用原因并给出处理办法**（关掉占用程序再试，或改端口后重新启动）。
3. **发第一条消息**：用刚建的账号登录，登录后落在「总览」，点左侧
   **工作台 → 直接对话**，输入一句话发送。运行期间关掉状态窗口只是隐藏到托盘、网关继续运行；要结束程序用托盘菜单“退出”。

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
| 2 | 安装包构建 | 未完成（顶层无产物，等待按 §0.3 节点重建） | 2026-11-08 实测：`dist/installer` 顶层 0 个 exe、0 个 zip；历史产物已移入 `_archive\` 且判定不合格 | 待安装冒烟包 / 终版分别回填路径、字节、SHA256 与对应源码提交 |
| 3 | 真实安装（静默） | 部分完成（历史实测，产物不可复现） | 历史日志 `dist/obsolete/installer-20261105-1506/install.log`（该目录已随旧包清理，不在仓库内）：`Original Setup EXE` = `D:\LiuHao-AI-OS\dist\installer\LiuHao-Setup-0.1.0.exe`，12:59:46–13:00:22（约 34.7 秒），`-- File entry --` 8,478 条 | 该安装器已不存在、日志无字节/SHA；旧记录的“25.3 秒 / 8,599 个文件”无法复核，已删 |
| 4 | 安装版首启 E2E | 部分完成（结论保留；**产物不可复现**，本地桩、复用向导函数、未逐字键入） | [e2e-installed-2026-11-06.json](evidence/e2e-installed-2026-11-06.json)（1,553 字节）：建号+填 Key 8.7s、健康 8.4s、首条消息 2.4s 收到 `E2E-REPLY-OK from stub-model`、重启 8.8s 后历史仍在、总 33.2s。对应安装器为 `D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe`（34,199,800 / `314839ED…`），payload 含未提交文件 | 差额：没有在命令行窗口里逐条键入向导答案，也未复验「自动打开浏览器」（E-9）；且安装器无法从任何提交复现（E-13），不能作为「最终包已验证」 |
| 5 | 浏览器 UI 走查 | 已完成 | 登录 `e2e` → 左侧工作台 → 发「你好」，回复含 `E2E-REPLY-OK from stub-model`；3/3 判据 PASS | — |
| 6 | 端口占用明确报错（不自动顺延） | 已改定 | 2026-11-08 决定：不自动换端口，避免地址漂移；旧 E2E 日志里的 `18130→18131 自动顺延` 行为已作废 | — |
| 7 | 失败分支实测 | 部分完成 | [failure-modes-2026-11-06.json](evidence/failure-modes-2026-11-06.json)：错误 Key → `[生成失败] 401 Incorrect API key`；模型名错 → `404 model does not exist`；上游停掉 → `503`；服务没起 → `WinError 10061` 连接被拒 | 本机非 Ollama 端点走「测试连接」会被 SSRF 防护拒绝（聊天却可用），所以 **`invalid_key` 这类测试连接分类没能在这台机器上用桩复现**；该分类只有真实云 Key 场景与单测覆盖（见 E-3、E-5） |
| 8 | 打包延迟依赖修复 | 已完成 | payload 内已含 `openai/httpx2/httpcore2/truststore/jiter`；修复随 `b789d244` 入库，安装版聊天不再报 `httpcore2` 缺包 | 已解除（E-1）；该目录另有未提交的升级元数据改动，与依赖修复无关 |
| 9 | 真实厂商 Key 对话 | 未验证 | 网关流的可复现脚本 [verify_first_run_e2e.py](../scripts/verify_first_run_e2e.py)（commit `250456cd`，说明见 [first-run-e2e.md](first-run-e2e.md)）需要真实 Key；本流没有 Key，只用本地 OpenAI 兼容桩验证了真实 HTTP 请求路径 | 本流无真实厂商 Key 的原始输出（E-5）；网关流报称用 DeepSeek 跑过，只能算旁证 |
| 10 | 静默卸载 | 已完成（E-4 已修） | `installer/liuhao.iss` 的 `CurUninstallStepChanged` 在 `UninstallSilent` 时直接退出、按安全默认保留数据；`/VERYSILENT` 实测可正常结束。交互式“删除数据”分支已单独实测（连续两次确认后按 `data-dir.txt` 删） | — |
| 11 | 代码签名 | 未做 | 安装包未签名，Windows 显示「未知发布者」 | 需证书与签名流水线（E-8） |
| 12 | 前端重建 | 未做 | 安装包内是构建时的 `console/dist`；前端仍在重写 | 前端稳定后必须重建安装包（E-7） |
| 13 | 能力地图 | 已完成 | [capability-map.md](capability-map.md)，commit `1ffe055a` | — |
| 14 | HC-01 证据分级 | 已完成 | [hc01-status.md](hc01-status.md)，commit `408caee2`（后续 `ff408f15` 只改遥测标注） | — |
| 15 | 缺口登记 | 已完成 | [known-gaps.md](known-gaps.md) 的 E-1～E-16（E-9～E-16 为本轮追加） | — |
| 16 | 全量回归 | **基线已完成；最终 HEAD 未通过**（基线 `bb6b654c`，详见 5.1；交叉验证 `3fa88be0` 见 5.9；**两者都不是最终 HEAD 的交付凭证**）；C7/C9/C11 三格补证见 5.7 | `7458 tests, 0 failed, 0 errors, 58 skipped`；C1/C2/C3/C4/C5/C6/C8/C10/C12 PASS；C7/C9 NOT VERIFIED；C11 BLOCKED；`PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY=False`；日志 `verify-full-bb6b654c.log`（2,303 字节，exit 0）。补证：C7 已有本机基线并可判定（`--only C7` PASS，方差 80%～1594% 已如实登记）；C9「原口径 NOT_VERIFIED + 安装树分发形态已独立验证」两条并列，且**升级载荷缺前端的缺陷已修复**（新包 685 成员 / `console/` 12，旧包被拒且零改动，见 5.7）；C11 5 项裁决已由 owner 裁决并记录 [ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)，`check_hc01` 仍派生 `BLOCKED`（判定事实，非工程阻塞）。**交叉验证 `3fa88be0`**：`PASS=7 FAIL=2 BLOCKED=1 NOT_VERIFIED=2`，C3/C12 因账号切片未提交实现而 FAIL（E-15） | 差额：基线 `bb6b654c` 之后 HEAD 已推进；`3fa88be0` 只是过程凭证。最终 hash 冻结后需再跑一轮全量作为交付凭证。`RELEASE READY` 为 False，裁决后理由只剩 C9（桌面分发形态无真实 CD，属形态差异） |
| 17 | 多账号 8 项硬要求验收（脚本化，含 2 条附加边界） | 已完成 | `scripts/acceptance/account_matrix_acceptance.py` 实跑：8 项全 🟢 + B1/B2 全 🟢，**109 项检查 0 失败、退出码 0**（8 项主体 98 + B1 6 + B2 5）；固定端口 19008、独立数据目录 `D:\_audit\account-matrix-2`；含 `src/` 近期写入的防误跑保护（命中即拒绝、退出码 2，`--force` 显式跳过）；证据 `D:\_audit\account-matrix-2\report.json`、`ui\*.png`；发布前必跑项见 `dist/release-gate/RELEASE-RUNBOOK.md` §5.6 | 本次运行期间另一条线仍在写 `src/`（5 个 `.py`），属强制模式；正式发版前需在「`src/` 连续 2 分钟无写入」时去掉 `--force` 再跑一次，两次结果一致才算最终凭证 |
| 18 | 产品许可证条款（专有） | **待确认（发布前必须由人/律师定稿）** | 仓库根 `LICENSE` 现为短版声明（940 字节：著作权归属 + 保留一切权利 + 禁止未经书面许可再分发/转售/反向工程 + 第三方组件见 `licenses\` 与 `LGPL-SOURCE-OFFER.txt`）；完整 40 行草稿原样存于 [docs/legal/EULA-DRAFT-pending-review.md](legal/EULA-DRAFT-pending-review.md)，文件开头标注「AI 起草，未经法务确认，不得作为正式条款对外发布」 | 适用法律、违约终止、责任限制额度三类条款**故意未写进正式 LICENSE**，须由人/律师定稿后才能对外发布；在此之前产品界面只写「许可证与合规材料」并提供查看入口，不得声称许可协议已生效 |

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
| 安装包记录曾指向不存在的文件；现在 `dist/installer` 有 0.1.0 四项产物，但最终包仍待转正 | E-13（最终包回填路径/字节/SHA256，并写死对应源码提交后重跑 E2E） |

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

### 5.7 三格补证（2026-11-06，不重跑全量、不改判定口径）

> 只补 C7 / C9 / C11 的证据；§5.1 的 `PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2`、
> `RELEASE READY=False` 仍是 `bb6b654c` 那一轮的事实，不回填成 PASS。
> 详细版：[regression-baseline-coverage-2026-11-06.md](evidence/regression-baseline-coverage-2026-11-06.md) §7。

**C7 性能门禁（从 SKIP 变为可判定）**

- worktree `D:\copilot-worktrees\liuhao-final-bb6b654c`（temp 根之外，clean），仓库 venv py3.12.14，
  `bench_audit_append.py --quick` **6 次取中位数**；异常样本未丢弃。
- 基线（写入 `scripts/bench_baseline.json` 的新 profile `Windows|py3.12.14|...|t24|quick`）：
  单条 **644.25 事件/秒**、p95 **2.577 ms**、batch10 **4,639.1**、batch100 **11,156.4**、
  多进程不含启动 **8,015.85** / 含启动 **2,004.7**、恢复 **0.0445 s**；四个不变量全 true。
- 判定：`python scripts/verify_readiness.py --only C7` → **PASS**；独立验证样本
  `636.8 事件/秒 / p95 2.654 ms` → `=== PERFORMANCE GATE: PASS ===`。
- **方差如实**：单条吞吐极差/中位数 **80.0%**、p95 **1594.4%**、batch100 **90.1%**、
  多进程不含启动 **142.6%**、恢复 **76.4%**。基线代表安静窗口，繁忙时门禁会失败，**不是稳定 CI 门禁**。
- **定位（owner 2026-11-06 裁决）**：维持「**本机安静窗口可判定、非 CI 阻断门禁**」；
  方差数字保留原样；**不许为了让门禁好看去调阈值**（`±25%` 容差与 `scripts/bench_baseline.json`
  本轮均未改，只新增 py3.12.14 profile 作为可比基线）。
- 机器：Xeon E5-2673 v3 @2.40GHz，12 核/24 线程，34,205,548,544 字节内存（31.9 GiB），Windows 10.0.19045。
- 证据：[c7-performance-baseline-2026-11-06.md](evidence/c7-performance-baseline-2026-11-06.md)、
  [bench-baseline-py312-2026-11-06.json](evidence/bench-baseline-py312-2026-11-06.json)（6,249 字节）、
  [c7-gate-validation-2026-11-06.log](evidence/c7-gate-validation-2026-11-06.log)（1,785 字节）、
  [c7-readiness-only-2026-11-06.log](evidence/c7-readiness-only-2026-11-06.log)（493 字节）。

**C9 部署 / 升级 / 回滚（两条结论同时存在，不许合并成一条 PASS）**

| 口径 | 结论 |
|---|---|
| **原口径（Docker / CD）** | **`C9 = NOT_VERIFIED`，判定未改动**（compose v2 插件缺失、镜像未构建、真实 CD 缺失 `cd_absent=True`） |
| **分发形态（安装树）** | **已独立验证**：真实安装树副本 10,769 文件 / 204,834,804 字节（自带 py3.12.14 runtime）上两轮 `check → apply → 重启 → rollback` |

- 第 1 轮：check 0.71s → apply **12.50s**（备份 512 文件、安装 **662 文件**、自检真实启动 5.48s + `/v1/health` 200）→
  0.1.1 重启 **5.34s** health 200 → rollback **2.29s**（恢复 513 文件）→ 0.1.0 重启 9.87–9.90s health 200。
- 第 2 轮（带真实用户 SQLite）：apply **12.62s**（数据 3 文件 / 14,878 字节，`databases_checked=1`、
  `tables_checked=1`）→ rollback **2.45s**（恢复 515 文件）。
- 数据保全：哨兵 `c9-sentinel.txt` SHA256 `051F79B9…A4A9` 升级与回滚后不变；用户库行
  `(1, 'keep-across-upgrade-and-rollback')` 全程在。
- 披露：回滚是**覆盖恢复**而非字节级还原（备份后新建的 `audit_store.db*` 会保留）；
  旧载荷 `liuhao-0.1.1.zip`（662 文件 / 2,741,388 字节 / sha256 `1a56e7a8…9c10`）**不含前端 `console/`**
  ——该缺陷已单独修复，见下一条；升级只校验 SHA256、无签名。
- 证据：[c9-installed-upgrade-2026-11-06.md](evidence/c9-installed-upgrade-2026-11-06.md)、
  [c9-check.json](evidence/c9-check.json)、[c9-upgrade-1.json](evidence/c9-upgrade-1.json)、
  [c9-rollback-1.json](evidence/c9-rollback-1.json)、[c9-upgrade-2.json](evidence/c9-upgrade-2.json)、
  [c9-rollback-2.json](evidence/c9-rollback-2.json)、[c9-history.json](evidence/c9-history.json)（42,617 字节）、
  [c9-restart-0.1.1.err.log](evidence/c9-restart-0.1.1.err.log)（1,243 字节）、
  [c9-restart-0.1.0.err.log](evidence/c9-restart-0.1.0.err.log)（1,244 字节）、
  [c9-readiness-only-2026-11-06.log](evidence/c9-readiness-only-2026-11-06.log)（510 字节：
  `[C9] ... NOT VERIFIED ... real CD absent=True (honest)`）。

**C9 缺陷修复：升级载荷缺前端 `console/`（2026-11-06 补，已修）**

- 缺陷：旧载荷 **0 个 `console/` 成员**。升级器仍把后端换新、前端保持旧版；
  `/v1/health` 依旧 200，浏览器加载旧界面，接口一变就是随机 400 / 空面板——**静默失败**。
- 修复三层：① `console/` 与控制台签章 `console/console-contract.json` 随包；
  ② 升级前门禁（缺 `index.html` / 签章不符 / 后端契约不符 / 前端版本不符 → **拒绝升级并给出中文原因，安装目录零改动**）；
  ③ 自检把真实启动服务 `/v1/health` 的 `api_contract` 与签章比对；构建期缺前端直接拒绝产出。
- **包体数字**：新包 `liuhao-0.1.2.zip` —— **685 成员**（其中 `console/` **12** = 11 个产物 + 1 个签章）/
  **2,961,979 字节** / 解压 **10,047,927** / sha256 `ca46fc76…5D0A`；同参数复跑 SHA256 逐字节相同。
  旧包对照：662 成员 / 0 `console/` / 2,741,388 字节。
- **拒绝旧包**：退出码 **1**，`console/` 保持 **10 文件 / 496,026 字节 / `B6CCEB84…E5DAB8`**，
  版本仍 `0.1.0`，新增备份 **0**。
- **装新包**：**18.72s**、安装 **685 文件**、自检真实启动 **5.75s** + `/v1/health` 200（`api_contract="1"`）；
  `console/` 落盘后 **12 文件 / 496,344 字节**，`index.html` 由 `B6CCEB84…` 变为 **`6F4F0C53…F05F3C`**。
- **重启**：**5.07s**，`GET /`、`GET /dashboard` 都返回新 index（含 `<!-- frontend build 0.1.2 -->`），新增资源 **200**。
- **回滚**：**2.67s**、恢复 **516 文件**，`console/` 精确回到 **10 文件 / 496,026 字节 / `B6CCEB84…`**，
  新增文件残留 **0**；数据哨兵与用户库行不变。
- 边界：前端用**模拟构建标记**（非真实 `npm run build`）；未签名；端到端只跑同契约（`api_contract=1`），
  跨契约拒绝由单测覆盖。
- 证据：[c9-console-payload-2026-11-06.md](evidence/c9-console-payload-2026-11-06.md)、
  [c9b-console-contract-2026-11-06.json](evidence/c9b-console-contract-2026-11-06.json)、
  [c9b-build-0.1.2.txt](evidence/c9b-build-0.1.2.txt)、[c9b-reject-0.1.1.txt](evidence/c9b-reject-0.1.1.txt)、
  [c9b-upgrade-0.1.2.json](evidence/c9b-upgrade-0.1.2.json)、[c9b-rollback-0.1.0.json](evidence/c9b-rollback-0.1.0.json)、
  [c9b-history-0.1.2.json](evidence/c9b-history-0.1.2.json)、[c9b-health-0.1.2.json](evidence/c9b-health-0.1.2.json)、
  [c9b-restart-0.1.2.err.log](evidence/c9b-restart-0.1.2.err.log)、[c9b-restart-0.1.2.out.log](evidence/c9b-restart-0.1.2.out.log)。
- 单测：`tests/test_upgrade_console_contract.py` **10 passed**（合法包 / 缺 index / 缺签章 /
  后端契约不符 / 字节篡改 / 版本不符 / 健康契约 / 不适用布局 / 安装树回读 / manager 拒绝且零改动）。

**C11 HC-01：5 项裁决已由 owner 裁决并记录 ADR**

| # | 裁什么 | 裁决 |
|---|---|---|
| 1 | Baseline binding：A live / B 旧快照 / C 重采 | **A**（live 作运行源，旧快照仅作历史证据） |
| 2 | 快照保管断裂：接受并披露 / 隔离替换 / 标 UNRESOLVED | **接受并披露**（内容完整性有证，缺的是操作者归因） |
| 3 | I1–I10：固定/参数化拆分 / 维持现状 / 全部参数化 | **固定/参数化拆分**（否则 `live=331,611` 长期假红） |
| 4 | Schema：v1→v9 / 不做 / 双路 | **随第 1 项选 A ⇒ 不做 v1→v9** |
| 5 | 是否授权重采冻结基线 | **推迟，条件写死**：F1–F6 单写者/围栏上线并 soak 通过后按只读 ACL + operator + 前后 hash 重采 |

- 裁决组合：**Path A + accept-with-disclosure + 固定/参数化拆分 + 不做 v1→v9 + 暂不授权重采。**
- **已记录 ADR**：[ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)（含保管断裂事实与「行为人未查明」、
  5 项裁决 + 逐项「可推翻条件」、裁决前后口径差异、后果与引用规则）。
- **裁决后口径**：C11 = **已裁决（ADR-HC01）**；裁决前为 `BLOCKED — HUMAN DECISION PENDING`。
  机器门 `check_hc01` 仍按冻结人类主权门设计打印 `BLOCKED`（不改口径），但
  `RELEASE READY=False` 的理由已只剩 **C9**（见 §5.10）。
- 证据：[hc01-pending-decisions-2026-11-06.md](evidence/hc01-pending-decisions-2026-11-06.md)、
  [hc01-status.md](hc01-status.md) §4.1 指针、
  [c11-readiness-only-2026-11-06.log](evidence/c11-readiness-only-2026-11-06.log)（549 字节：
  `[C11] ... BLOCKED ... not an engineering blocker`，`RELEASE READY = False`）。

### 5.8 故障面补测（2026-11-06，单跑相关套件）

> 补的是「没人覆盖、用户一定会撞上」的五类故障面：单实例/多开、休眠断网、时间跳变、
> 磁盘满/只读/占用、并发一致性。**只报本轮亲跑数字**，不改 §5.1 的全量结论与
> `RELEASE READY=False`。详细版：[fault-surfaces-2026-11-06.md](evidence/fault-surfaces-2026-11-06.md)。

| 故障面 | 结论 | 实测要点 |
|---|---|---|
| 单实例/多开 | **修复** | 双进程同数据目录：第二个 `exit=3` + 可读提示（pid/URL/启动时间），第二端口未监听，第一个继续服务；杀第一个后端口/锁释放，残留 `instance.json` 不误报；托盘在 CLI 持锁时拒绝且不拉子进程 |
| 休眠唤醒/断网 | **无缺陷（未改代码）** | 正常 188ms；断开 `ConnectTimeout` 2000ms；恢复后 16ms 自愈；黑洞 `ReadTimeout` 2030ms；网关 suspend 8s 后 `/v1/health` 两次均 200（0ms） |
| 时间跳变 | **修复 2 处** | PyJWT 默认 `verify_iat` 会让回拨后的旧令牌被踢下线 → 显式关闭、`exp` 仍用稳定时钟；旧审计链第二次 emit 起 `verify_integrity=False` → 链长不一致即重建。前进/后退/吊销/真过期/审计回拨 5 条用例全绿 |
| 磁盘满/只读/占用 | **修复 4 处** | 6 个真实 case 全部 fail-closed：审计旧文件不变 + 内存事件回滚；模型配置 503 可读文案；启动器 `DataDirectoryUnavailable`；记忆 `MemoryStoreError` + 内存回滚 |
| 并发一致性 | **修复 2 处** | 模型配置读-改-写同锁（两个窗口改不同字段不再丢更新）；升级/回滚加线程+跨进程互斥（第二个得到可读拒绝）。对话并发 400/400 条、重复 0 |

- 单跑数字：**501 passed, 19 skipped in 230.93s**；新增故障面用例 **16 passed**（4.59s）；
  桌面真进程/真 TCP 子集 **34 passed in 49.48s**。
- 新增 16 条用例已纳入回归清单（逐条见下方，改动测试文件时必须一起跑）：

  | # | 测试文件::用例 | 覆盖 |
  |---|---|---|
  | 1 | `tests/test_single_instance.py::test_second_process_is_refused_with_readable_message` | 第二个实例退出码 3 + 可读提示 |
  | 2 | `tests/test_single_instance.py::test_lock_is_released_when_holder_dies` | 持锁进程死亡后锁释放 |
  | 3 | `tests/test_single_instance.py::test_gateway_leaves_when_launcher_is_killed` | 父进程消失后网关退出 |
  | 4 | `tests/test_single_instance.py::test_stale_metadata_without_lock_is_not_treated_as_running` | 残留 `instance.json` 不误报 |
  | 5 | `tests/test_single_instance.py::test_tray_refuses_when_cli_instance_holds_lock` | 托盘不空跑子进程 |
  | 6 | `tests/test_fault_hardening.py::test_stable_clock_ignores_forward_and_backward_jumps` | 时钟前进/后退 |
  | 7 | `tests/test_fault_hardening.py::test_jwt_survives_forward_and_backward_clock_jumps` | JWT 不被踢下线 |
  | 8 | `tests/test_fault_hardening.py::test_revocation_still_applies_after_forward_clock_jump` | 吊销仍生效 |
  | 9 | `tests/test_fault_hardening.py::test_expired_token_is_still_rejected` | 真过期仍拒绝 |
  | 10 | `tests/test_fault_hardening.py::test_audit_chain_survives_clock_rollback` | 审计链回拨完整 |
  | 11 | `tests/test_fault_hardening.py::test_audit_write_failure_keeps_previous_file_and_rolls_back` | 审计原子写 + 内存回滚 |
  | 12 | `tests/test_fault_hardening.py::test_memory_store_write_failure_is_readable_and_keeps_old_rows` | 记忆可读失败 + 旧行不变 |
  | 13 | `tests/test_fault_hardening.py::test_memory_kernel_rolls_back_memory_when_persist_fails` | 记忆内存态回滚 |
  | 14 | `tests/test_fault_hardening.py::test_concurrent_model_config_updates_do_not_lose_each_other` | 模型配置并发不丢更新 |
  | 15 | `tests/test_fault_hardening.py::test_upgrade_is_exclusive_across_threads` | 升级跨线程互斥 |
  | 16 | `tests/test_fault_hardening.py::test_upgrade_lock_is_visible_to_another_process` | 升级跨进程互斥 |

- 未执行边界（状态 **未验证**，已登记到 [known-gaps.md](known-gaps.md)）：
  真 OS 睡眠/真拔网线/真实 ENOSPC/真实托盘点击/两个浏览器窗口 E2E/真实全局时钟跳变。
- 未动账号树与前端在改文件（`apps/console/**`、`scripts/acceptance/account_tree_e2e.py`）。

### 5.9 交叉验证（`3fa88be0`，2026-11-06）——**过程凭证，不是交付凭证**

> 账号、管家、韧性、安全四路各自绿，但从没在同一 HEAD 上一起跑过。本轮在
> `D:\copilot-worktrees\` 下的干净 worktree 钉死 `3fa88be0`，用仓库 venv 跑
> `scripts/verify_readiness.py --full`，日志落 `D:\cache\temp\`。
> 报告：[cross-verify-3fa88be0.md](evidence/cross-verify-3fa88be0.md)。

| 项目 | 结果 |
|---|---|
| 基线 hash | `3fa88be0`（跑前 `git status` clean） |
| C1–C12 | PASS 7 / FAIL 2（C3、C12）/ BLOCKED 1（C11）/ NOT_VERIFIED 2（C9、C7 首轮瞬时后单跑 PASS） |
| 用例 | 7521 collected：31 failed / 7433 passed / 54 skipped / 3 xfailed；主跑 3965.78s + 补充 3535.59s |
| 归因 | 27 条失败 + 1 条采集错误同根因：**账号/知识切片提交了测试与调用方，把被调用实现留在工作区**（`model_router.visible_to`、`ai_management.GoalViewer`、结构化错误体）⇒ E-15；C7 为满负载瞬时 I/O，单跑 PASS |
| 真回归 | 无新增真回归；本轮另修的 4 条（`launch_installed.py` 宿主校验分支 + 3 个单测）已复核 66 passed / 1 skipped |
| 结论 | **不能当交付凭证**；`RELEASE READY=False` 不变。账号/知识切片提交实现后，必须在最终冻结 HEAD 上重跑（E-15、E-16） |

### 5.10 交付级全量凭证（冻结 hash `72fd9b17`，2026-11-06 18:41）——**这是最终凭证**

> 冻结 hash = `72fd9b178492b909396848a3214fabbce048151c`（`origin/p36` tip）。
> 干净 worktree `D:\copilot-worktrees\liuhao-delivery-72fd9b17`（跑前 `git status` clean，
> `git log -1` = 该 hash），解释器 `D:\LiuHao-AI-OS\.venv` py3.12.14，
> 命令 `scripts/verify_readiness.py --full`，日志 `D:\cache\temp\verify-full-72fd9b17.log`，
> JSON `scripts/readiness_report.json`（`generated_at=2026-11-06T10:41:23Z`）。

| 关 | 状态 | 关键数字 |
|---|---|---|
| C1 Build | PASS | compileall clean；4 个入口模块 import OK |
| C2 Clean-env | PASS | 干净环境启动 + `GET /v1/health`=200 |
| C3 Stable tests | PASS | **7639 collected / 0 failed / 0 errors / 61 skipped** |
| C4 Security | PASS | 无高置信密钥；fail-closed 在；CI gates `bandit:high`、`semgrep:error` |
| C5 Data integrity | PASS | hash-chain sig/alg **32/32 COMPLIANT**（HC-02..08 + HC-11） |
| C6 Crash/recovery | PASS | 可靠性/故障套件 6 文件（仅临时 DB，live `audit_store.db` 未动） |
| C7 Performance | PASS | `bench --quick --gate` 本机 profile PASS（**首跑即绿，未重跑**） |
| C8 Observability | PASS | `/v1/metrics/prometheus`=200、10035 bytes、**48 个真实指标族**（floor 40）；`http_requests_total` 有值；`executor_fence_*`、`coordinator_admission_contention_total` 在 |
| C9 Deployment/upgrade | NOT VERIFIED | Dockerfile + alembic 在；**真实 CD 环境缺失**（诚实口径，不改判定） |
| C10 Independent verification | PASS | 独立验证 harness 在 |
| C11 HC-01 | **已裁决（ADR-HC01）** | owner 5 项裁决已记录；机器 `check_hc01` 仍打印 `BLOCKED`（冻结门设计，不改口径），不计入未决理由 |
| C12 No unexplained failures | PASS | 7639 / 0 / 0，无未解释重大失败 |

- **verdict**：`PASS=10 FAIL=0 BLOCKED=1 NOT_VERIFIED=1 ERROR=0`，`RELEASE READY = False`。
- **False 的唯一理由 = C9**：桌面分发形态没有真实 CD 环境可验，属**形态差异**，不是缺陷；
  安装树真实升级/回滚（`c9b-build/upgrade/rollback/history/health/restart`）已覆盖交付需求，
  但**不等于「真实 CD 已验过」**，不写成「已全部验证」。
- **C11 口径差异**：裁决前 False 的第二个原因是 C11（人工裁决未定）；ADR-HC01 记录后
  C11 = **已裁决（ADR-HC01）**，机器 `check_hc01` 仍打印 `BLOCKED`（冻结门设计，不改口径）。
- **CI**：`72fd9b17` 两条 run 全绿 —— `42323536105`（Release Readiness Contract C1..C12、
  Independent Verification C10）与 `42323536110`（Build / Run Tests 3.11+3.12 /
  Architecture Gate / Security Scan / Guardrails / Console A11y / Smoke / Observability /
  P0 Gate，13 个 job 全部 success）。两腿一致 `7596 passed / 40 skipped / 3 xfailed`
  （=7639）。契约探针实跑 `PASS: contract matches runtime -- 118 paths (+1 documented
  schema-excluded), 0 ghosts, 0 missing, methods agree`。
- **失败归因**：**无失败**（FAIL=0、ERROR=0）。C9 见上；C11 已裁决，见 §5.7。
- **C7 并发说明**：本轮跑前 18:50 与跑中多次采样，CPU 负载 1%→9%，8080 端口**无监听**，
  无打包/安装进程；C7 首跑即 PASS，故**未触发**「单独 `--only C7` 重跑」规则。
- **过程 hash 作废说明**：`ae7935a7`（C7 首跑红 0.059 > 0.055，已披露方差带内；C3/C12 PASS
  7620/0/0）与 `b76762a7`（C7 吞吐 1476.6 < 1503.5 且 recovery 0.073，与分发流构建并发；
  C3 PASS 7620/0/0）都**只作参考**，产品代码已不同，不作为交付凭证。

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
| §2 #14 | `docs/hc01-status.md` | — | 存在，8,662 字节 | 一致（§4.1 已改为「已裁决 + ADR 指针 + C11 裁决后口径」） |
| §2 #15 | `docs/known-gaps.md` | — | 存在，36,817 字节 | 一致（E-16 已改为「已完成」，C11 已裁决） |
| §5.3 | `docs/evidence/regression-cc3af30d.txt` | — | 存在，2,430 字节 | 一致（上一轮全量原始输出） |
| §5.5 | `docs/evidence/delta-tests-de1b0d87.xml` | — | 存在，15,351 字节 | 一致 |
| §5.6 | `D:\cache\temp\verify-full-e18169e6-INVALID.log` | — | 存在，3,498 字节（仓库外，仅追溯） | 一致 |
| §5.1 | `docs/evidence/regression-bb6b654c.txt` | 2,303 字节 | 存在，2,303 字节 | 一致（`verify-full-bb6b654c.log` 的逐字节副本） |
| §5.2 | `docs/evidence/regression-baseline-coverage-2026-11-06.md` | — | 存在，14,202 字节 | 一致（覆盖表本体，已追加 §7 三格补证与 §7.2.1） |
| §5.2 | `docs/evidence/regression-delta-bb6b654c.xml` | 532 条 | 存在，67,855 字节 | 一致 |
| §5.2 | `docs/evidence/regression-delta-d2a62b0e.txt` | — | 存在，104 字节 | 一致 |
| §5.2 | `docs/evidence/regression-c1-d2a62b0e.txt` | — | 存在，8,356 字节 | 一致 |
| §0 / §2 #2 | `D:\cache\temp\liuhao-e2e\build\installer\manifest.json` | 8,596 文件 / 165.5 MB / 84 dist | 存在，数值一致 | 一致（非交付物，仅溯源） |
| §5.7 / §7.1 | `docs/evidence/c7-performance-baseline-2026-11-06.md` | — | 存在，8,464 字节 | 一致（C7 基线报告） |
| §5.7 / §7.1 | `docs/evidence/bench-baseline-py312-2026-11-06.json` | 6 次样本 + 方差 | 存在，6,249 字节 | 一致（可读证据副本） |
| §5.7 / §7.1 | `scripts/bench_baseline.json` | 新增 py3.12.14 profile | 存在，+70 行 | 一致（C7 实际读取的基线） |
| §5.7 / §7.1 | `docs/evidence/c7-gate-validation-2026-11-06.log` | — | 存在，1,785 字节 | 一致（`PERFORMANCE GATE: PASS`） |
| §5.7 / §7.1 | `docs/evidence/c7-readiness-only-2026-11-06.log` | — | 存在，493 字节 | 一致（`--only C7` PASS） |
| §5.7 / §7.2 | `docs/evidence/c9-installed-upgrade-2026-11-06.md` | — | 存在，10,967 字节 | 一致（C9 双口径报告，含 §2.6 缺陷修复指针） |
| §5.7 / §7.2 | `docs/evidence/c9-check.json` | — | 存在，678 字节 | 一致 |
| §5.7 / §7.2 | `docs/evidence/c9-upgrade-1.json` / `c9-rollback-1.json` | — | 存在，301 / 230 字节 | 一致 |
| §5.7 / §7.2 | `docs/evidence/c9-upgrade-2.json` / `c9-rollback-2.json` | — | 存在，301 / 230 字节 | 一致 |
| §5.7 / §7.2 | `docs/evidence/c9-history.json` | — | 存在，42,617 字节 | 一致（完整升级 ledger） |
| §5.7 / §7.2 | `docs/evidence/c9-restart-0.1.1.err.log` / `c9-restart-0.1.0.err.log` | — | 存在，1,243 / 1,244 字节 | 一致 |
| §5.7 / §7.3 | `docs/evidence/hc01-pending-decisions-2026-11-06.md` | — | 存在，6,594 字节 | 一致（裁决前问题陈述保留 + 顶部「已裁决」状态） |
| §5.7 / §7.3 / §0 披露 | `docs/adr/ADR-HC01-baseline-binding.md` | — | 存在，10,116 字节 | 一致（5 项裁决含逐项推翻条件 + 裁决前后口径 + 保管断裂/行为人未查明） |
| §5.7 C9 修复 | `docs/evidence/c9-console-payload-2026-11-06.md` | — | 存在，8,533 字节 | 一致（缺陷修复报告） |
| §5.7 C9 修复 | `docs/evidence/c9b-console-contract-2026-11-06.json` | — | 存在，3,325 字节 | 一致（机读汇总） |
| §5.7 C9 修复 | `docs/evidence/c9b-build-0.1.2.txt` | — | 存在，937 字节 | 一致（构建日志，含签章 11 文件 / 指纹） |
| §5.7 C9 修复 | `docs/evidence/c9b-check-old-0.1.1.json` | — | 存在，675 字节 | 一致（旧包 check） |
| §5.7 C9 修复 | `docs/evidence/c9b-reject-0.1.1.txt` | — | 存在，651 字节 | 一致（拒绝原文） |
| §5.7 C9 修复 | `docs/evidence/c9b-upgrade-0.1.2.json` / `c9b-rollback-0.1.0.json` | — | 存在，298 / 227 字节 | 一致 |
| §5.7 C9 修复 | `docs/evidence/c9b-history-0.1.2.json` | — | 存在，17,794 字节 | 一致（含前端契约自检细节） |
| §5.7 C9 修复 | `docs/evidence/c9b-health-0.1.2.json` | `api_contract="1"` | 存在，402 字节，字段一致 | 一致 |
| §5.7 C9 修复 | `docs/evidence/c9b-restart-0.1.2.err.log` / `.out.log` | — | 存在，1,599 / 62 字节 | 一致 |
| §5.7 / §7.3 | `docs/evidence/c11-readiness-only-2026-11-06.log` | `RELEASE READY = False` | 存在，549 字节 | 一致（`BLOCKED` + `not an engineering blocker`） |
| §5.7 / §7.2 | `docs/evidence/c9-readiness-only-2026-11-06.log` | `RELEASE READY = False` | 存在，510 字节 | 一致（`NOT VERIFIED`，`real CD absent=True`） |

