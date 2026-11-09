# 交付清单（实测版）

> 生成时间：2026-11-05（本机时间）；2026-11-06 更正安装包记录（见 0.1）；
> 2026-11-07 交付面审计后更新 §0 当前产物与体积口径；2026-11-07 收口补 §5.11 发布候选凭证（`b4406497`）；
> 2026-11-08 补 §5.12 新 tip 凭证（`416f7109`）与 §7 新一轮冻结前置清单、§7.1 宣布 True 还差什么、§7.2 自定义 Base URL 的 SSRF 口径。
> **历史快照提示（2026-11-08 起）**：**§0 已于 2026-11-09 更新为 0.1.1 实测**；本清单其余章节里的
> 安装包路径、体积与「dist/installer 仍是 0.1.0」一类描述，是 2026-11-05～11-07 的时点记录。
> 当前实际产物与校验值见 [`dist/installer/README-DELIVERY.txt`](../dist/installer/README-DELIVERY.txt)、
> `dist/installer/SHA256SUMS.txt` 与 `dist/payload/manifest.json`，不要拿旧数字当现状。
>
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
>    不改判定，也不为了好看去调阈值。2026-11-08 起 `check_hc01` 的**证据文案**与
>    `CONDITION_META["C11"]` 已改为明写「裁决已记录 / 门按设计冻结」（状态仍是 `BLOCKED`，
>    未改成 `PASS`）：措辞是关于世界的事实，状态是门的性质，两者分开。
> 3. **`RELEASE READY = False` 的理由只剩 C9**：桌面分发形态没有真实 CD 环境可验，
>    属**形态差异**，不是产品缺陷。安装树的真实升级/回滚（`c9b-*` 证据）已覆盖交付需求，
>    但**不等于「真实 CD 已验过」**，本条不写成「已全部验证」。
> 4. **`RELEASE READY=True` 当前结构上不可达**：机器要求 12 关全 `PASS`，而 C11 是冻结门、
>    恒为 `BLOCKED`；C9 在本轮 worktree 里也只能 `NOT VERIFIED`（无 `.venv` 走兜底分支）。
>    要变 True 必须先有新 ADR 改变 C11 的机器口径，详见 [§7.1](#71-宣布-release-readytrue-还差什么)。

## 0. 安装包

**当前状态（2026-11-09 实测）：`dist/installer/` 顶层有 1 个 exe、1 个 zip。**
- `LiuHao-Setup-0.1.1.exe`（2026-11-08 04:24 构建）
- `liuhao-0.1.1-upgrade.zip`（2026-11-08 04:23 构建）
- 另有 `SHA256SUMS.txt`、`README-DELIVERY.txt`、`LiuHao-CodeSigning.cer`、`trust-liuhao-signing.ps1`、`信任签名证书.cmd`；`_archive\` 只作追溯，不得分发。

**版本口径**：源码已是 `0.1.2`（`pyproject.toml:3`、`src/version.py` 的 `APP_VERSION`），但 **0.1.2 包尚未产出**——`dist/` 下按文件名搜索 `*0.1.2*` 实测为 0。所以本清单描述的是 **0.1.1 交付物**，不能当作 0.1.2 的凭证。

> ~~2026-11-08 时 `dist/installer/` 顶层为 0 个 exe、0 个 zip~~ —— 那是当天的状态；2026-11-08 04:23–04:24 重建后顶层已有上述 0.1.1 产物。原记录「本轮全部修复都还没进包」随之作废。
> 上游 `origin/p36` 侧曾记录「`dist/installer/` 内有 0.1.0 的四项正式产物（2026-11-07 11:14 构建，
> 绑定 `f74fa14e`）」——那批已整体移入 `_archive\`（`README-DELIVERY.txt` 内 SHA256 可对），只作追溯。

| 项目 | 值（2026-11-09 实测） |
|---|---|
| 安装包路径 | `dist/installer/LiuHao-Setup-0.1.1.exe`（2026-11-08 04:24 构建） |
| 大小 / SHA256 | 29,567,136 字节 / `49BC50C8…B4DF`（以 `SHA256SUMS.txt` 为准，本表不写死完整哈希） |
| 校验文件 | `dist/installer/SHA256SUMS.txt`：两行，exe 与 `liuhao-0.1.1-upgrade.zip`（32,178,761 字节 / `9EABA197…3224`） |
| 历史归档（只追溯） | `dist/installer/_archive/20261107-mixed-0.1.0-and-0.1.1/`：0.1.0 exe 34,731,647 字节（2026-11-07 11:14 构建，SHA256 `01BE18BB…64DA2`）、0.1.0 升级包 `liuhao-0.1.0.zip` 2,638,893 字节（SHA256 `0A04AC6A…A48512`）、0.1.1 exe 34,830,483 字节（SHA256 `0BF400D6…BBF45`）；**均不得对外分发** |
| **这版安装包对应的源码提交** | **未验证**：顶层没有 `DELIVERY.txt`，`README-DELIVERY.txt` 也未写 HEAD，产物侧无法读出源码提交。不要臆造。（归档的 0.1.0 绑定 `f74fa14e2c947385e8a3f61052393e733498726c`，与 0.1.1 包无关） |
| 安装后体积 | 未在本轮重测（0.1.0 历史实测 10,502 个文件 / 170,666,366 字节，**不可套用到 0.1.1**） |
| payload | `dist/payload/manifest.json`：`version=0.1.1`、`file_count=10,855`、`size_mb=150.9` |
| 构建命令 | `scripts/release/package-frozen.ps1`（唯一允许写 `dist/installer` 的入口）；0.1.1 这版由 `packaging/build_windows_installer.ps1` 产出（编译耗时 47.093 秒，见 `dist/release-gate/smoke/20261108-real-key/CONCLUSION.md`） |
| 安装前必读 | `installer/assets/BEFORE-INSTALL.txt`（含 Key 申请与跳过说明） |
| 第三方许可证 | 由 `packaging/third_party_notices.py` 在构建时按 payload 实装生成（模板 `packaging/third-party-notices.template.txt`），连同 `packaging/licenses/` 复制到程序根目录 |
| **结论** | 0.1.1 的 exe / 升级包 / `SHA256SUMS.txt` / `README-DELIVERY.txt` 齐；`LIUHAO_RELEASE.json` 的 `releases[0].signature=true`（ed25519）；`README-DELIVERY.txt` 自述「可用于内测分发，是否转正由产品线决定」。**不是 0.1.2，也未绑定源码提交** |

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
| S-02 | 图标正确 | 看桌面与开始菜单快捷方式 | 显示鎏灏图标（`{app}\assets\liuhao.ico`），双击能启动；快捷方式目标为 `runtime\pythonw.exe` + `scripts\liuhao_desktop.py app`；即便命令行里出现 `--open-browser`（历史参数）也不会切换到浏览器模式，网页模式一律用 `--web`；双击出现原生窗口而不是 Edge | 空白/白纸/齿轮/默认 exe 图标；目标指向测试或走查目录；双击报找不到文件 | 快捷方式属性截图（目标+图标）+ 桌面截图 |
| S-03 | 首启向导 | 首次启动，逐字填完五步：数据目录 → 管理员账号 → 模型（含端口）→ 确认 → 自检 | 图形窗口（不是黑命令行），五步都能「下一步/上一步」；端口被占时出现「换一个端口」；自检结果如实显示 | 只弹黑窗口；卡在某一步；端口被占只报错无二选一；向导写不出数据目录 | 每步截图 + 向导日志路径 |
| S-04 | 自动进入控制台 | 向导完成后不要手动点任何东西 | 原生窗口（WebView2）自动出现并加载 `http://127.0.0.1:<端口>` 的登录页；不弹出系统浏览器 | 没出现窗口；窗口是空白页/旧缓存页；弹出了 Edge/默认浏览器；端口与向导填写不一致 | 原生窗口截图 + 启动日志中的监听端口行；网页模式另验 `--web` |
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

## 0.5 数据目录下配置项的卸载归类（2026-11-08）

首次设置向导会把用户选的端口持久化到 `<数据目录>\config\desktop_port.json`
（`{"port": 8123, "updated_at": ...}`），由 `src.desktop.service.load_saved_port()`
读取、作为启动端口来源（优先级：`--port` > `LIUHAO_API_PORT` > 该文件 > 默认 8080）。
它记录的是**用户手动选过的端口偏好**，不是可再生成的机器状态——删掉后下次启动会
回落到 8080，可能与用户已放行的防火墙规则/书签不一致。

| 文件 | 位置 | 性质 | 卸载时 |
|---|---|---|---|
| `desktop_port.json` | `<数据目录>\config\` | 用户配置（端口偏好） | **默认保留**，与其它数据目录内容同口径；只有用户在卸载时二次确认「删除数据目录」才一并删除 |
| `desktop.json` | `%LOCALAPPDATA%\LiuHao\` | 机器状态（只存上次选中的数据目录指针） | **无条件删除**（`[UninstallDelete]`）；留着会让下次启动自动连回旧目录，表现为「账号数据丢了」 |
| `run\instance.json`、`config\current_url.txt`、`.liuhao-startup-probe-*`、`bootstrap.log` | 程序/数据目录 | 运行痕迹（pid、地址、堆栈） | **无条件删除**（同上，卸载不留能直接登录的残迹） |

判断依据（两者按「删掉会不会改变用户可见行为」区分，而不是按「看起来像不像配置」）：

1. `desktop_port.json` 有值时会实际改变启动端口，用户能感知；归入用户数据，
   随数据目录保留/删除。
2. `desktop.json` 只是上次选择的指针，删掉后首启重新探测即可恢复，但**不删会让
   安装版按旧路径找数据**，所以属于必须清的机器状态。
3. `scripts/purge_installed_config.py` 的 `CONFIG_EXCLUDES` 只清理**程序目录**
   `{app}\config` 里「新版不再随包」的旧配置，不遍历数据目录；因此
   `desktop_port.json` 不需要加入该清单，也未被误删（有测试保证该清单与
   `stage_payload.CONFIG_EXCLUDES` 的差集恰好是两个用户凭据）。

### 0.6 验收副本不得落在 `%LOCALAPPDATA%\LiuHao`（2026-11-08）

本轮在 `%LOCALAPPDATA%\LiuHao` 下发现 6 份 `data.after-smoke-*` / `data.bak-*`
整目录副本（187 个文件 / 13,779,798 字节），其中 4 份含账号凭据库与身份库、
3 份含模型主密钥与模型库。它们是**验收时手工整目录复制**产生的，不是产品行为，
但会让「用户现场取证」不可信 —— 谁也分不清哪份是用户真实数据。

约束（后续验收一律照此执行）：

1. **不复制到 `%LOCALAPPDATA%\LiuHao`。** 需要 before/after 对比时，副本放系统
   临时目录（`%TEMP%` 下的具名目录），验收结束随脚本一起删。
2. **数据目录用参数显式指定。** `install_smoke_e2e.ps1` 的 `-InstallDir` / `-DataDir`
   默认就是 `D:\LiuHaoSmoke\...`，不要为了“像真实用户”改成默认目录。
3. **开始菜单组用独立名字。** 脚本默认 `-StartMenuGroup LiuHaoSmoke`；沿用产品
   组名 `LiuHao` 会把用户的开始菜单项改成指向验收目录（已记入 §0.2 缺陷）。
4. **测试侧已强制隔离。** `tests/conftest.py` 现在同时把 `LIUHAO_DESKTOP_CONFIG`
   与 `LIUHAO_LOG_DIR` 指向 `tmp_path`，避免测试把真实 `desktop.json`
   （上次选中的数据目录指针）写坏。
5. **启动探针必须自清理。** `.liuhao-startup-probe-*` 由
   `src/observability/boot_report.py::_write_probe` 创建，成功路径必须删除；
   该行为由 `tests/test_boot_report.py::test_probe_writable_leaves_no_probe_file`
   钉住。历史残留（旧 `mkstemp` 实现留下 42 个 2 字节文件）已由安装器
   `[UninstallDelete]` 对 `{code:GetDataDir}` 与 `{#LegacyDataDir}` 两处
   一起覆盖，卸载即清。

## 1. 三步使用说明（最终用户只需要这三步）

> 与 `scripts/first_run_gui.py` 的真实界面对齐（页标题与按钮文字以该文件为准）：
> 第 1 步 · 数据目录 → 第 2 步 · 管理员账号 → 第 3 步 · 选择模型（可跳过）→
> 第 4 步 · 完成前请确认 → 第 5 步 · 自检结果；底部按钮「上一步 / 取消 / 下一步」，
> 其中第 4 页的「下一步」显示为「**完成并自检**」、第 5 页显示为「**进入控制台**」。

1. **下载并安装**：校验 SHA256 后双击 `LiuHao-Setup-<版本号>.exe`。
   Windows 提示「未知发布者」时选「更多信息 → 仍要运行」（本包是项目自签名：
   导入信任脚本后属性里 `Status` 为 `Valid` 且能看到签名者，但按微软口径自签名在
   SmartScreen 上等同未签名，仍可能提示）。程序默认装到
   `D:\LiuHao`（数据 `D:\LiuHaoData`、备份 `D:\LiuHaoBackups`），不需要管理员权限；
   D 盘不可用时自动回退到 `%LOCALAPPDATA%\Programs\LiuHao`，实际位置见程序目录
   `data-dir.txt`。**备份与数据在同一块盘，磁盘损坏时无法用来恢复**，要防灾请自行另存移动硬盘。
   升级信任与备份根的用户环境变量由安装器写入当前用户环境，装完不需要手工设置任何环境变量。
   老版本用户覆盖安装保持原位置不变。
   **成功标志**：桌面上出现「鎏灏」快捷方式，安装结束时状态窗口出现
   （关闭状态窗口只是隐藏到托盘、服务继续运行）。
2. **首次设置：五页向导**（不需要在命令行输入）。每页都按「填什么 → 点哪个按钮 → 什么算过」走：

   | 页 | 标题 | 这一页填什么 | 点哪个按钮 | 看到什么算过 |
   |---|---|---|---|---|
   | 1 | 第 1 步 · 数据目录 | 账号 / 对话 / 知识库 / 审计的存放目录：默认 `D:\LiuHaoData`，没有 D 盘时预填 `%LOCALAPPDATA%\LiuHao\data`；可点「浏览…」改到别的**可写**目录（不要放 Program Files 或程序安装目录） | 「下一步」 | 输入框里的路径存在且可写；选到 U 盘 / 移动硬盘 / 网络盘时会先弹「数据目录在外接设备上」确认框，选「是」才继续 |
   | 2 | 第 2 步 · 管理员账号 | 账号（英文）、显示名（可中文）、登录密码、确认密码；密码至少 8 位且同时包含字母和数字 | 「下一步」 | 四项都填了、两次密码一致；不满足会在窗口内用中文说明并停在本页 |
   | 3 | 第 3 步 · 选择模型（可跳过） | 三选一：「跳过，稍后在『系统设置 → 模型管理』里添加」／「本机 Ollama」（填模型名与地址，默认 `qwen2.5:7b`、`http://localhost:11434`）／「云端 API Key」（填 `base_url`、模型名、API Key，如 `https://api.deepseek.com/v1` + `deepseek-chat`）；同页下方「服务端口」默认 `8080` | 「下一步」 | 选「跳过」即为通过；选 Ollama / 云端则必填项不能为空 |
   | 4 | 第 4 步 · 完成前请确认 | 不用填；核对摘录：数据目录、加密边界、备份、卸载、联网范围 | **「完成并自检」** | 页面显示你刚填的数据目录与模型选择；点下后写入设置并开始自检 |
   | 5 | 第 5 步 · 自检结果 | 不用填；看四项**本机实测**结果：网关端口、数据目录、托盘驻留、默认模型 | **「进入控制台」** | 四项全部通过（失败项右侧有可点的「下一步」，按提示处理后点「重试」）；点「进入控制台」后向导关闭、服务启动并打开控制台 |

   **中途放弃**：任意一页点「取消」或直接关窗 = 放弃本次设置，**不创建账号、不启动控制台**；
   下次双击桌面快捷方式会重新从第 1 页开始。
   **端口被占用**：向导不会自动换端口；回第 3 页在「服务端口」里填一个空闲端口再继续。
3. **填 Key 并发第一条消息**：在自动打开的控制台登录页（没自动出现就检查托盘；网页模式才访问 `http://127.0.0.1:8080`，
   桌面端地址以原生窗口实际加载为准）用第 2 步建的账号登录，登录后落在
   「总览」，点左侧 **工作台 → 直接对话**，输入一句话发送。
   **成功标志**：几秒内出现模型回复，且**不是 `[演示数据]` 前缀**；若出现 `[演示数据]`，
   说明还没接真实模型（第 3 步选了「跳过」），到 **系统设置 → 模型管理** 补上 Key 再试。
   运行期间关掉状态窗口只是隐藏到托盘、网关继续运行；要结束程序用托盘菜单「退出」。

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
| 2 | 安装包构建 | 部分完成：0.1.1 已构建；0.1.2 未产出 | `dist/installer` 顶层有 `LiuHao-Setup-0.1.1.exe`（29,567,136 字节 / `49BC50C8…B4DF`）与 `liuhao-0.1.1-upgrade.zip`（32,178,761 / `9EABA197…3224`），见 §0 | 0.1.2 包未产出；这版产物未绑定源码提交（顶层无 `DELIVERY.txt`） |
| 3 | 真实安装（静默） | 部分完成（历史实测，产物不可复现） | 历史日志 `dist/obsolete/installer-20261105-1506/install.log`（该目录已随旧包清理，不在仓库内）：`Original Setup EXE` = `D:\LiuHao-AI-OS\dist\installer\LiuHao-Setup-0.1.0.exe`，12:59:46–13:00:22（约 34.7 秒），`-- File entry --` 8,478 条 | 该安装器已不存在、日志无字节/SHA；旧记录的“25.3 秒 / 8,599 个文件”无法复核，已删 |
| 4 | 安装版首启 E2E | 部分完成（结论保留；**产物不可复现**，本地桩、复用向导函数、未逐字键入） | [e2e-installed-2026-11-06.json](evidence/e2e-installed-2026-11-06.json)（1,553 字节）：建号+填 Key 8.7s、健康 8.4s、首条消息 2.4s 收到 `E2E-REPLY-OK from stub-model`、重启 8.8s 后历史仍在、总 33.2s。对应安装器为 `D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe`（34,199,800 / `314839ED…`），payload 含未提交文件 | 差额：没有在命令行窗口里逐条键入向导答案，也未复验「自动打开浏览器」（E-9）；且安装器无法从任何提交复现（E-13），不能作为「最终包已验证」 |
| 5 | 浏览器 UI 走查 | 已完成 | 登录 `e2e` → 左侧工作台 → 发「你好」，回复含 `E2E-REPLY-OK from stub-model`；3/3 判据 PASS | — |
| 6 | 端口占用明确报错（不自动顺延） | 已改定 | 2026-11-08 决定：不自动换端口，避免地址漂移；旧 E2E 日志里的 `18130→18131 自动顺延` 行为已作废 | — |
| 7 | 失败分支实测 | 部分完成 | [failure-modes-2026-11-06.json](evidence/failure-modes-2026-11-06.json)：错误 Key → `[生成失败] 401 Incorrect API key`；模型名错 → `404 model does not exist`；上游停掉 → `503`；服务没起 → `WinError 10061` 连接被拒 | `invalid_key` 的「测试连接」分类**已于 2026-11-09 在本机用桩复现**（[failure-invalidkey-2026-11-09.md](evidence/failure-invalidkey-2026-11-09.md)：回环白名单端口 401 上游桩，`reason=invalid_key`、中文提示与产品预设逐字一致、数据目录 21 文件 Key 明文 0 命中，三断言全过）。`technical_detail` 保留脱敏 `sk-***` 上游原文摘录属**产品显式设计**（主文案中性，上游原文脱敏后只进可展开的「技术详情」）；若验收口径要求任何可见字段均不回显上游原文，属**口径裁决项**，需产品侧裁决。仍未覆盖：其他 provider 的 `invalid_key` 归类、`key_revoked`（401+revoked）分支 |
| 8 | 打包延迟依赖修复 | 已完成 | payload 内已含 `openai/httpx2/httpcore2/truststore/jiter`；修复随 `b789d244` 入库，安装版聊天不再报 `httpcore2` 缺包 | 已解除（E-1）；该目录另有未提交的升级元数据改动，与依赖修复无关 |
| 9 | 真实厂商 Key 对话 | 已完成（安装版，2026-11-08） | `dist/release-gate/smoke/20261108-real-key/CONCLUSION.md`：真实 DeepSeek Key，未重启后端（PID 16404→16404）保存后 `POST /v1/chat` 200 / 1.90s；流式 12 帧、工具调用、Key 脱敏全 PASS；另有 `20261108-034245-real-key/live_summary.txt` 8 项 PASS | 本清单原记「本流无 Key」指的是写这一条时的取证范围，现已被上述安装级证据取代（E-5 同步更新） |
| 10 | 静默卸载 | 已完成（E-4 已修） | `installer/liuhao.iss` 的 `CurUninstallStepChanged` 在 `UninstallSilent` 时直接退出、按安全默认保留数据；`/VERYSILENT` 实测可正常结束。交互式“删除数据”分支已单独实测（连续两次确认后按 `data-dir.txt` 删） | — |
| 11 | 代码签名 | 部分完成（项目自签名） | `Get-AuthenticodeSignature dist\installer\LiuHao-Setup-0.1.1.exe` → `Status=Valid`，主体 `CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`（自签名） | 非公开权威 CA：未导入信任仍提示「未知发布者」，SmartScreen 可能因信誉不足提示；另外安装器首屏文案仍写「未做代码签名」（`installer/liuhao.iss:1054`/`:1060`），见 E-24 |
| 12 | 前端重建 | 未做 | 安装包内是构建时的 `console/dist`；前端仍在重写 | 前端稳定后必须重建安装包（E-7） |
| 13 | 能力地图 | 已完成 | [capability-map.md](capability-map.md)，commit `1ffe055a` | — |
| 14 | HC-01 证据分级 | 已完成 | [hc01-status.md](hc01-status.md)，commit `408caee2`（后续 `ff408f15` 只改遥测标注） | — |
| 15 | 缺口登记 | 已完成 | [known-gaps.md](known-gaps.md) 的 E-1～E-24（E-9 起为历次追加；2026-11-07 本轮关闭/仍开放清单另列） | — |
| 16 | 全量回归 | **基线已完成；最终 HEAD 未通过**（基线 `bb6b654c`，详见 5.1；交叉验证 `3fa88be0` 见 5.9；**两者都不是最终 HEAD 的交付凭证**）；C7/C9/C11 三格补证见 5.7 | `7458 tests, 0 failed, 0 errors, 58 skipped`；C1/C2/C3/C4/C5/C6/C8/C10/C12 PASS；C7/C9 NOT VERIFIED；C11 BLOCKED；`PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY=False`；日志 `verify-full-bb6b654c.log`（2,303 字节；当时的退出码 0；**现行契约**下 NOT_VERIFIED/BLOCKED 会以 3 收尾，见 `scripts/verify_readiness.py:1114-1124`）。补证：C7 已有本机基线并可判定（`--only C7` PASS，方差 80%～1594% 已如实登记）；C9「原口径 NOT_VERIFIED + 安装树分发形态已独立验证」两条并列，且**升级载荷缺前端的缺陷已修复**（新包 685 成员 / `console/` 12，旧包被拒且零改动，见 5.7）；C11 5 项裁决已由 owner 裁决并记录 [ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)，`check_hc01` 仍派生 `BLOCKED`（判定事实，非工程阻塞）。**交叉验证 `3fa88be0`**：`PASS=7 FAIL=2 BLOCKED=1 NOT_VERIFIED=2`，C3/C12 因账号切片未提交实现而 FAIL（E-15） | 差额：基线 `bb6b654c` 之后 HEAD 已推进；`3fa88be0` 只是过程凭证。最终 hash 冻结后需再跑一轮全量作为交付凭证。`RELEASE READY` 为 False，裁决后理由只剩 C9（桌面分发形态无真实 CD，属形态差异） |
| 17 | 多账号 8 项硬要求验收（脚本化，含 2 条附加边界） | 已完成 | `scripts/acceptance/account_matrix_acceptance.py` 实跑：8 项全 🟢 + B1/B2 全 🟢，**109 项检查 0 失败、退出码 0**（8 项主体 98 + B1 6 + B2 5）；固定端口 19008、独立数据目录 `D:\_audit\account-matrix-2`；含 `src/` 近期写入的防误跑保护（命中即拒绝、退出码 2，`--force` 显式跳过）；证据 `D:\_audit\account-matrix-2\report.json`、`ui\*.png`；发布前必跑项见 `dist/release-gate/RELEASE-RUNBOOK.md` §5.6 | 本次运行期间另一条线仍在写 `src/`（5 个 `.py`），属强制模式；正式发版前需在「`src/` 连续 2 分钟无写入」时去掉 `--force` 再跑一次，两次结果一致才算最终凭证 |
| 18 | 产品许可证条款（专有） | **待确认（发布前必须由人/律师定稿）** | 仓库根 `LICENSE` 现为短版声明（940 字节：著作权归属 + 保留一切权利 + 禁止未经书面许可再分发/转售/反向工程 + 第三方组件见 `licenses\` 与 `LGPL-SOURCE-OFFER.txt`）；完整 40 行草稿原样存于 [docs/legal/EULA-DRAFT-pending-review.md](legal/EULA-DRAFT-pending-review.md)，文件开头标注「AI 起草，未经法务确认，不得作为正式条款对外发布」 | 适用法律、违约终止、责任限制额度三类条款**故意未写进正式 LICENSE**，须由人/律师定稿后才能对外发布；在此之前产品界面只写「许可证与合规材料」并提供查看入口，不得声称许可协议已生效 |

## 3. 「部分完成」的确切差额（汇总）

- **第 7 项 失败分支**：已实测 401 / 404 / 503 / 端口拒绝；`invalid_key` 的
  “测试连接”分类已在 2026-11-09 用回环白名单端口的 401 桩本机复现
  （[evidence/failure-invalidkey-2026-11-09.md](evidence/failure-invalidkey-2026-11-09.md)）。
  仍**没有**在本机复现 `key_revoked` / `quota_exhausted` 两类，差额部分由
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
| 安装包记录曾指向不存在的文件；现在 `dist/installer` 顶层有 0.1.1 两项产物（exe + 升级包），但源码已到 0.1.2、0.1.2 包未产出，且产物未绑定源码提交 | E-13（0.1.2 包回填路径/字节/SHA256，并写死对应源码提交后重跑 E2E） |

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

### 5.11 本轮收口凭证（发布候选 `b4406497`，2026-11-07）

> 与 §5.10 的关系：§5.10 是上一冻结 `72fd9b17` 的历史凭证，保持原文不回写；本节是本轮 26 个提交后的新候选。
> 结论先行：**CI 红，`b4406497` 不可作为交付冻结候选**。

| 项 | 结果 |
|---|---|---|
| 源码提交 | `b4406497`（`origin/p36`；`2738e16a..b4406497` 26 个提交，快进推送） |
| CI run | `42386438527` — **failure** |
| 两腿回归 | **17 failed / 7705 passed / 40 skipped / 3 xfailed**（3.11 与 3.12 数字一致） |
| Guardrails / Step 21 | **未真跑**：上游 `test` 红，被 CRITICAL-5 看门狗显式判红（未静默跳过） |
| 文档三条门禁 | 本地 PASS：`verify_doc_count_claims`（15,418 / 25,093 / 15,552 / 26,287）、`verify_doc_links`、`verify_docs_index_coverage`；CI 内因 Guardrails 被上游阻断未执行 |
| Security Scan | **红**：被 CRITICAL-5 看门狗阻断（`security` 依赖 `test`） |
| 安装包 | **历史快照（2026-11-07 时点）**：当时 `dist/installer` 仍是 0.1.0（构建 11-06 17:29，早于 `9e537633`），不能作为本提交产物；2026-11-09 现状见 §0：已有 0.1.1 exe + 升级包，payload 冒烟全 PASS（`dist/smoke-report.json` 的 `login`/`chat` 均为 `PASS`），真实 Key 的 S-06/S-07/S-08 也已转 PASS；是否转正仍由产品线决定 |
| `--full` | **未在 `b4406497` 重跑**；§5.10 的 `PASS=10` 只对 `72fd9b17` 成立 |
| 2h soak | **未在修复后重跑**（known-gaps G-8） |

**失败归因：真回归，不是环境问题。** 本地同一 venv 复跑同批文件 16 failed（`test_chat_history` 单跑通过、全量顺序失败），五组根因见
[MASTER-TASK-LEDGER.md](MASTER-TASK-LEDGER.md#10-本轮修复登记2026-11-07) §10.2 与 [known-gaps.md](known-gaps.md)：

- F-01（9 个）：`tests/conftest.py` autouse fixture 全局 `LIUHAO_DATA_DIR`，打挂升级状态/备份/自检落点；
- F-02（3 个）：`src/gateway/chat.py` 直取 `assistant.provider`，测试 `FakeAssistant` 无该属性；
- F-03（1 个）：全量顺序下会话列表为空（单跑通过，用例间状态污染）；
- F-04（2 个）：新增 `PERSONAL_CONTEXT_DB_PATH` 未登记门禁变量，另有 4 处 `SEED_GATES` 引用行漂移；
- F-05（1 个）：`6a2e361e` 新增测试文件带 UTF-8 BOM。

**还差什么（按顺序，缺一不可）**：

1. 修 F-01～F-05（涉及 `tests/`、`src/`，超出本轮 docs-only 边界）。
2. 在新 hash 上两腿 + Guardrails（Step 21 真跑出 `PASS: contract matches runtime ...`）+ 文档三条 + Security Scan 全绿。
3. 用该 hash 重建安装包，回填 §0 的路径/字节/SHA256/源码提交。
4. 在该 hash 跑 `verify_readiness.py --full` 并重跑 2h soak。
5. E-5 / E-8 / E-14 / E-20 / E-21 / F-1～F-6 继续保持未验证。

**DELIVERY.txt 说明**：`dist/installer/DELIVERY.txt` 是构建产物，按本轮边界（不动 `dist/`）未改；
它仍描述 0.1.0 内部构建，不可对外发布；重建安装包时须由打包流回填源码提交与本表凭证。

### 5.12 新 tip `416f7109` 的就绪凭证（2026-11-08）

> 上一轮冻结凭证 `72fd9b17` 已作废。本节是新 tip 上的**实测**结果，命令、日志、JUnit 均可复核。
> 明细与并发干扰记录：[evidence/verify-full-416f7109-2026-11-08.md](evidence/verify-full-416f7109-2026-11-08.md)。

| 项 | 事实 |
|---|---|
| 判定对象 | `416f7109`（`origin/p36` tip；含 0.1.1 版本号 + 四个 ISCC 修复） |
| worktree | `D:\copilot-worktrees\liuhao-delivery-416f7109`，跑前 `git status --porcelain` 为空 |
| 解释器 | `.venv` Python 3.12.14（`pytest-timeout` 在位，无 `unrecognized arguments` 类 `ERROR`） |
| 命令 | `python -u scripts/verify_readiness.py --only C1,C2,C4,C5,C6,C7,C8,C9,C10,C11`，再单独 `--only C7` |
| 机器 verdict | `PASS=7  FAIL=0  BLOCKED=1  NOT_VERIFIED=2  ERROR=0` — **`RELEASE READY = False`** |

| 关 | 状态 | 实测数字 / 证据 |
|---|---|---|
| C1 Build | **PASS** | `compileall` 干净；4 个入口模块 import OK |
| C2 Clean-env runnable | **PASS** | 干净环境启动后 `GET /v1/health` = 200 |
| C4 Security blocker | **PASS** | 无高置信度密钥；fail-closed 在位；CI 门禁 `bandit:high`、`semgrep:error` |
| C5 Data integrity | **PASS** | hash-chain 签名/算法 **32/32 COMPLIANT**（HC-02..08 + HC-11） |
| C6 Crash/recovery | **PASS** | 6 个可靠性/故障面文件全绿（仅临时库；live `audit_store.db` 未触碰） |
| C7 Performance | **NOT VERIFIED** | 第 1 次：`aggregate_events_per_sec_including_startup` = **1057.0** < 1503.5（基线 2004.7，容差 25%）；按规则单独 `--only C7` 第 2 次：**1380.1** < 1503.5。两次都在 §2 所述并发负载下（8 个并发 pytest + 2 套 24h harness + 4 个 gateway）；`check_performance` 自身拒绝把这判成回归，故为 `NOT VERIFIED`，**不是 FAIL**。安静 runner（CI）才能给稳定判定 |
| C8 Observability | **PASS** | `/v1/metrics/prometheus` = 200、10,035 字节、**48** 个真实指标族（下限 40）；`http_requests_total` PRESENT+FED；UBX005 全在 |
| C9 Deploy/upgrade/rollback | **NOT VERIFIED** | worktree 内无 `.venv`，`verify_deploy_readiness.py` 无法执行，checker 只能落到 `Dockerfile + alembic` 兜底分支；`real CD absent=True`（如实） |
| C10 Independent verification | **PASS** | 独立验证 harness 在位 |
| C11 HC-01 | **BLOCKED** | 冻结人类主权门（状态不变）。**本轮已改证据文案**：明写「决定已记录在 ADR-HC01 / 门按设计冻结 / `BLOCKED` ≠ 裁决未做」，不再打印「HUMAN DECISION PENDING」 |
| C3 Test suite | **本轮未完成（如实记录）** | 全量在本机被并发清理多次杀掉（JUnit 落成 0 字节，不可当凭证）；上一可比凭证是 `b4406497` 的 CI：**17 failed / 7705 passed / 40 skipped / 3 xfailed**，失败集合 = F-01～F-05 |
| C12 No major failures | **与 C3 同源** | 同上；F-01～F-05 未修，因此**不可能转绿**——只能靠改 `tests/`/`src/`，本轮边界不允许 |

**结论（必须照此表述）**：C11 的 `BLOCKED` 是**门的设计**，不是「裁决未做」；`RELEASE READY=False`
的**机器原因**是 C9 与 C11 两条口径（C9 可达、C11 需新 ADR），而**交付原因**是 P-1～P-7 未完成、
C3/C12 仍红。任何一处都不得写成「已全部验证」。


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

---

## 7. 新一轮冻结前置清单（2026-11-07 起算）

> **作用**：在把某个 hash 定为「交付冻结 hash」之前，主干上必须先落地的项。每项写死
> 「什么算完成」——可复核、可判真伪；「基本可用」「应该没问题」「本地跑过」都不算完成。
> **当前候选 hash = `416f7109`**（本轮 `--full` 凭证见 §5.12）。下表未全部满足之前，
> `416f7109` **只是候选，不是交付冻结 hash**。

| # | 前置项 | 为什么必须 | 什么算完成（缺一条都不算） | 现状（2026-11-07 本机实测） |
|---|---|---|---|---|
| P-1 | **版本号 0.1.1 全链一致** | 版本号不一致会让升级器判成「同版本/旧版本」，覆盖安装行为不可预期；包名、后端自报版本、`DELIVERY.txt` 会互相矛盾 | ① `python -c "from src.version import APP_VERSION;print(APP_VERSION)"` = `0.1.1`；② `pyproject.toml`、`frontend/package.json`、`installer/liuhao.iss`（`#define AppVersion` 默认值）三处同为 `0.1.1`；③ `pytest tests/test_version_consistency.py -q` 在冻结 hash 上全绿；④ 构建出的文件名是 `LiuHao-Setup-0.1.1.exe`，`DELIVERY.txt` 首行版本号也是 0.1.1；⑤ 安装后 `GET /v1/health` 自报 `version=0.1.1` | ①②③ **已满足**（`src/version.py:8`、`pyproject.toml:3`、`installer/liuhao.iss:22`，`f7603a8e` 附 `tests/test_version_consistency.py`）；④⑤ **未满足**：`dist/installer` 仍是 `LiuHao-Setup-0.1.0.exe` |
| P-2 | **安装包重打并绑定源码提交** | 现包对应 `f74fa14e`，早于版本号提交与 5 个 ISCC 修复；包内前端/启动器都不是冻结 hash 的代码 | ① 在冻结 hash 的 clean worktree 上 `packaging/build_windows_installer.ps1` 退出码 0，产出 `LiuHao-Setup-0.1.1.exe` + `liuhao-0.1.1.zip` + `SHA256SUMS.txt` + `DELIVERY.txt`；② `DELIVERY.txt` 写明「对应提交 `<冻结 hash>`」，且与 `git rev-parse HEAD` 逐字一致；③ `SHA256SUMS.txt` 与 `Get-FileHash` 实测一致；④ 包内 `runtime/app/src/version.py` = 0.1.1；⑤ 用该包实测安装一次（临时数据目录 + 显式 `/DIR`），首启 ready 且 `/v1/health` 自报 0.1.1；⑥ 路径/字节/SHA256/源码提交回填本文 §0 | **未完成**。`dist/installer` 现有 `LiuHao-Setup-0.1.0.exe` 34,731,647 字节 / `01BE18BB…64DA2`，`DELIVERY.txt` 写「对应提交 `f74fa14e`」——早于 `f7603a8e`。`d57fa52e`/`6b47829b`/`7c0a252a`/`416f7109` 四个提交正是修 ISCC 编译失败，说明重打正在进行且尚未产出 0.1.1 包 |
| P-3 | **真 Key 对话验收** | 现有证据只有本地 OpenAI 兼容桩；「填真 Key 就能对话」是产品的第一性承诺，桩不能证明 | ① 用**真实厂商 Key**（DeepSeek 或 OpenAI）在安装版或干净启动实例上 `POST /v1/chat` 收到非空 assistant 回复，留存脱敏后的时间戳/耗时/回复摘要（不得含 Key 明文）；② 同一次验收里换一个**错误 Key** 复现 401，且文案是中性中文（无上游英文原文、无内部术语）；③ 原始输出入 `docs/evidence/`；④ `known-gaps.md` E-5 改为「已关闭 + 证据指针」 | **未完成**（E-5 仍开放；本轮无真实 Key） |
| P-4 | **任意模型接入**（自定义 Base URL + 任意 model id） | 「任意模型」是用户明确要求；现状只覆盖预置清单，接不上别的厂商就等于不能用 | ① 通过 `POST /v1/models` 或控制台新增一条**非预置**配置（自定义 `base_url` + 任意 `model`），返回 2xx；② **不重启网关**，`GET /v1/models` 能列出且能完成一次对话（200 + 非空回复）；③ 至少两类各一条：OpenAI 兼容 1 条、非 OpenAI 兼容（Anthropic 或 Gemini）1 条；④ 非法/不可达 `base_url` 给出可读错误（不是 500 堆栈、不是静默成功）；⑤ 回归用例锁住「运行中新增 → 立即生效」 | **在跑**：`src/ai/model_config.py`、`src/ai/providers.py`、`src/gateway/models.py` 在工作区有未提交改动；无提交、无 E2E 证据 |
| P-5 | **管家页可用化** | 「一句话吩咐 → 自行安排员工 → 汇总结果」是 B-01 的原始承诺；页面在但没跑通过真实链路，等于演示 | ① 真实网关下：输入一句话 → 创建 goal → 自动派工 → 轮询到终态 → 页面显示汇总；② 失败分支（上游 401/503/超时）显示中性中文原因与可重试入口；③ 留一条端到端证据（goal id + 事件序列 + HTTP/页面日志），绑定冻结 hash；④ 不依赖 `Butler.tsx` 里的演示数据 | **在跑**：`94003942`/`3510cab3` 已入库，`apps/console/console/src/pages/Butler.tsx` 仍在工作区改动；真实一句话 E2E 未跑 |
| P-6 | **两腿回归 + 全部 CI 门禁在冻结 hash 上绿** | 单机全量绿不等于 CI 绿；Guardrails 的契约探针与 Security Scan 曾被上游红阻断，没真跑过 | ① `release-readiness.yml` 与 `build.yml` 在同一 hash 上全 success；② 两腿（3.11/3.12）数字一致；③ Guardrails 真跑出 `PASS: contract matches runtime …`（不是被 CRITICAL-5 看门狗判红）；④ Security Scan 绿且不是被上游阻断；⑤ 文档三条门禁（count claims / links / index coverage）绿 | **待做**：上一候选 `b4406497` 的 CI `42386438527` 为 failure（17 failed） |
| P-7 | **`verify_readiness.py --full` 在冻结 hash 的 clean worktree 上重跑** | dirty 树或旧 hash 的结果不能当凭证；C3/C12 依赖同一次真实全量 | ① clean worktree（跑前 `git status` 干净）；② 逐关状态与数字入本文与证据文件；③ 日志与 JUnit XML 落盘可复核；④ C7 若判红，按既有规则单独 `--only C7` 跑一次并在证据里写清并发干扰 | **本轮进行中**（§5.12 为 `416f7109` 的凭证） |
| P-8 | **前端 dist 与源码同源** | 包内 `console/dist` 与源码不一致会出现「后端新、界面旧」的静默失败，且 Service Worker 版本哈希会错 | ① 在冻结 hash 上重建 `console/dist`；② 包内 `index.html` 引用的 hash 与构建输出一致；③ 安装后登录页首屏可见登录按钮（1280×720 与 1366×768） | **部分**：`1f9e6b37`、`2a80657c` 已入库；未在新包上复验 |

**冻结时的纯文档校验（每次冻结/文档提交都要贴原文）**

```powershell
git diff --name-only <上一冻结hash>..HEAD -- src/ apps/ contracts/ installer/ packaging/
```

输出必须为空 —— 证明该段提交只动文档/证据/就绪脚本口径，业务代码仍是上一冻结 hash。

## 7.1 宣布 `RELEASE READY=True` 还差什么

机器的 `release_ready` 要求 **12 关全 `PASS`**（无 `FAIL` / `NOT VERIFIED` / `BLOCKED` / `ERROR`）。
按当前事实分成两类。

**必须（机器口径，缺一项就永远是 False）**

| 关 | 现状 | 变成 PASS 的条件 | 是否可达 |
|---|---|---|---|
| C9 Deployment/upgrade/rollback | `NOT VERIFIED` | ① 在 `.venv` 存在的 checkout 里跑（本轮 worktree 无 `.venv`，checker 只能走 `Dockerfile + alembic` 兜底分支，结构上必然 `NOT VERIFIED`）；② `scripts/verify_deploy_readiness.py` 退出码 0 且无 `FAIL` 行（docker CLI、compose v2、alembic 齐备）；③ 真实升级/回滚链在该环境复跑 | **可达**（属环境/形态，不是产品缺陷） |
| C11 HC-01 | `BLOCKED` | 只有**新 ADR 改变 HC-01 的机器口径**才可能 | **当前不可达**：`check_hc01` 恒返回 `BLOCKED`（冻结人类主权门，ADR-HC01 §1.2/§4.3 禁止为变绿改口径） |

> **结论（必须写清）**：在 ADR-HC01 仍然有效、`check_hc01` 仍按冻结门设计打印 `BLOCKED` 的前提下，
> **`RELEASE READY=True` 在结构上不可达**。这不是缺陷、也不该靠改判定去消除；能改变它的只有
> owner 的新 ADR（把 HC-01 从冻结门集合里移出，或给 C11 一个可机器判定的替代口径）。

**必须（交付口径：机器 verdict 不变，但决定敢不敢对外发布）**

1. P-1 版本号全链一致：**未满足**——源码 `pyproject.toml:3` / `src/version.py` 为 `0.1.2`，而 `dist/payload/manifest.json`、`dist/installer/SHA256SUMS.txt`、`dist/release/LIUHAO_RELEASE.json` 与线上发布页为 `0.1.1`；`dist/` 下没有 0.1.2 产物。
2. P-2 安装包重打并绑定冻结 hash：**未满足**——现有 `LiuHao-Setup-0.1.1.exe`（2026-11-08 构建）顶层无 `DELIVERY.txt`，源码提交未写入产物；0.1.2 包未产出。
3. P-3 真 Key 对话验收（含 401 中性文案）。
4. P-4 任意模型接入（自定义 Base URL + 非预置 model id，运行中生效）。
5. P-5 管家页可用化（真实一句话 E2E）。
6. P-6 两腿回归 + 全部 CI 门禁在冻结 hash 上绿。
7. P-7 `--full` 凭证绑定冻结 hash。

**可以带缺口发布（必须登记 + 在交付说明里披露，不阻塞 True）**

| 缺口 | 为什么可以带 | 登记位置 |
|---|---|---|
| Gemini 真实成功调用未验证 | 失败路径诚实（`400 API_KEY_INVALID` 真实到达），解析有假客户端回归；缺的是「有 Key 成功一次」 | `known-gaps.md` §2 |
| 自定义 Base URL 的 SSRF 风险（DNS 重绑定仅代码级验证） | 书面结论与本机实测已出（私网/链路本地拦截、回环白名单放行、带 Key 必须 https 均真实 HTTP 实测；**DNS 重绑定只有线程钉住解析的代码级证据，真实 DNS 层未测**） | 本文 §7.2、[evidence/g10-ssrf-2026-11-09.md](evidence/g10-ssrf-2026-11-09.md) |
| 仅 Windows x64 | 交付形态就是 Windows 桌面安装树；无 macOS/Linux/arm64 产物 | `known-gaps.md` E-20 / 仅 Windows x64 行 |
| 无真实 CD | 桌面分发形态差异；安装树真实升级/回滚已验，但**不等于**真实 CD 已验 | §5.10 C9、`known-gaps.md` 真实远端升级源行 |
| 无真实远端升级源 | 升级源只有本机安装树；远端拉取/回滚无证据 | `known-gaps.md` 真实远端升级源行 |
| 手机未验证 | 服务侧 LAN 200 已验证，手机侧无设备 | `known-gaps.md` E-14 |
| 24h 浸泡进行中 | 长跑 harness 在跑，尚未收口；2h soak 已验 | `known-gaps.md` E-21 / G-8 |
| 安装包仅项目自签名（非公开权威 CA） | 未导入信任只有「未知发布者」提示，不影响功能；公开 CA 签名属明确延后 | `known-gaps.md` E-8 / E-24 |
| 8/16GB 低配未实测 | 本机 32GB；已给实测数字并明确不外推 | `known-gaps.md` E-21 |

## 7.2 自定义 Base URL 的 SSRF 风险（2026-11-09 已出书面结论 + 本机实测；DNS 重绑定仅代码级验证）

| 项 | 事实 |
|---|---|
| 书面结论与实测 | [evidence/g10-ssrf-2026-11-09.md](evidence/g10-ssrf-2026-11-09.md)（原始请求/响应见同名 `json`；隔离数据目录 + 独立端口 19077，`APP_ENV=production`，用完即杀）：保存/测试/更新/运行时四条路径落地同一套目标校验。 |
| 实测拦截（真实 HTTP，非 Ollama 回环白名单面） | RFC1918 私网 `http://10.0.0.1:8000/v1` → **400**（「SSRF 防护：10.0.0.1 -> 10.0.0.1」）；链路本地 `http://169.254.1.1/v1` → **400**；`localhost:9999`（非白名单端口）→ **400**（`localhost -> ::1`）；带 Key 的 http 私网地址 → **400**（带 Key 必须 https）。 |
| 实测放行（回环白名单内，有意设计，不是绕过） | `127.0.0.1:11434`、`localhost:11434`、`[::1]:8000` → **200**（回环 + 白名单推理端口 11434/1234/8000/8080 或命中本机 Ollama 地址集）；provider=ollama 时回环任意端口 → **200**（实测 `127.0.0.1:19099`）。残余风险：把配置类型改成 ollama 即可指向回环上任意端口；缓解因素是仅回环（到不了内网）+ 需模型管理员身份，已如实登记。 |
| 四问答案 | ① **DNS 重绑定**：**真实 DNS 层未测**（硬约束不允许改系统 DNS，无可控权威解析器）——现有证据是**代码级**：每次真实请求前重新解析校验、校验通过后按线程钉住解析结果消 TOCTOU（`src/ai/net_guard.py` `_pinned_getaddrinfo`），探测面二次比对解析结果、漂移即 400「疑似 DNS rebinding，已拒绝」（实测锚点 `src/gateway/models.py:1054`）。登记口径照写「真实 DNS 层未测」，**不得写成「已验证防住」**。② 「已保存放行」覆盖所有调用路径：运行时守卫在每类 provider 每次真实请求前调用（聊天/目标拆解/embedding 均经 provider 层），且放宽再收窄为「仅命中已知本机 Ollama 地址集」；已保存配置重验与首次保存同口径（C1/C2 实测无状态差异）。③ 白名单是代码级常量（本地推理端口集 `net_guard.py:48`、本机 Ollama 地址集 `models.py:285-312`），不落配置文件，唯一可配置入口 `OLLAMA_BASE_URL` 且仅对 ollama 生效。④ 不应只限定字面量：判定按解析后地址做（`localhost:9999` 实测 400 佐证）。 |
| 残余风险（诚实披露） | ① DNS 重绑定无真实 DNS 层实测，且探测面自认「不钉 SDK 自己的 socket」的局限；② ollama 类型的回环例外不限端口；③ 白名单为代码常量，扩端口需改代码发版。 |
| 什么算完成 | 书面结论（允许/拒绝清单 + 四问逐条答案 + 落地位置）已出；「任意模型接入」（P-4）验收要求的「一条私网地址被拒」实测已带（`10.0.0.1` → 400）；「一条 DNS 重绑定被拒」的**真实 DNS 层实测仍缺**，等有可控解析器/DNS 环境时补测。 |


