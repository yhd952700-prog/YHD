# 冻结前条件现况（2026-11-08）

> 本轮只做**冻结前准备**：固化一键打包脚本、采集现况证据、把三条历史坑做成硬失败。
> **没有写 `dist\`、没有构建前端、没有组 payload、没有跑冒烟、没有编译 exe。**
> 证据采集时间：2026-11-07 13:30–13:55（本机时钟），仓库 `d:\LiuHao-AI-OS`，分支 `p36`。

## 0.0 p36 构建阻塞已解除（给跑 CI 的线）

2026-11-08 复测，三条证据：

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 网关可导入且能构建 app | `python -c "import src.gateway.main as m; m.get_app()"` | OK，35 条路由 |
| 网关契约 | `python scripts/verify_openapi_contract.py` | 不再出现 `could not build the gateway app`；仅剩 7 条契约未登记（见下） |
| 测试可收集 | `python scripts/verify_test_collection.py` | **PASS**：8232 条可收集，0 error（此前 41 个 collection error） |

根因与处置：`src/gateway/data_portability.py` 与 `src/gateway/main.py` 里的
`from .data_portability import ...` 都已在提交 `f9d5eb12` 中
（`git cat-file -e HEAD:src/gateway/data_portability.py` 通过）。因此按「纳入提交」
处理，没有改成可选导入；`import src.gateway.main` 现在必定通过，构建阻塞解除。

仍需处理但不是构建阻塞：`verify_openapi_contract.py` 报 7 条运行时路径未写进
`openapi.yaml`：`/v1/about`、`/v1/accounts/export/all`、`/v1/chat/export`、
`/v1/knowledge/export`、`/v1/knowledge/import`、`/v1/memory/import`、
`/v1/models/discover`。`openapi.yaml` 当前正被其它会话修改（`git status` 显示 `M`），
本线**没有**执行 `--write`，以免覆盖在途改动。等该文件安静后按脚本提示执行：

```
python scripts/verify_openapi_contract.py --write
python scripts/verify_openapi_contract.py
```


## 0.0b 三件卡口修复（2026-11-08）

| 卡口 | 处置 | 证据 |
| --- | --- | --- |
| 桌面图标进控制台、无托盘 | `installer\liuhao.iss` 的开始菜单 / 桌面 / 安装完成三处入口全部改为 `runtime\pythonw.exe scripts\liuhao_desktop.py app --open-browser`：关窗 = 隐藏到托盘、网关继续跑，托盘可打开控制台 / 停止服务 / 退出。`scripts\liuhao_desktop.py` 在进入托盘壳前先跑首启向导（没有管理员账号不硬启动），向导改过目录后重新绑定数据目录 | ISCC 解析通过并进入 Compressing；`py_compile` 通过 |
| `LiuHao.bat` 成功也 pause | `packaging\stage_payload.py` 生成的 LiuHao.bat、仓库 `start-liuhao.bat`、`scripts\build_cloud_bundle.py` 三处统一：退出码 0（含「已有实例在运行」的重复启动）直接 `exit /b 0`；只有非 0 才打印中文失败原因并 pause | 三个文件 `py_compile` 通过 |
| 数据目录跑到 C 盘 | `liuhao.iss` 全新安装一律先取 `D:\LiuHaoData`（可写时），删除「只要 C 盘旧目录有内容就悄悄改 C」的分支；新增「数据目录」向导页（始终展示、可改选），解析失败或不可写时值为空并说明原因；`NextButtonClick` 与 `PrepareToInstall` 双重校验，没选可写目录不放行；静默安装用 `/DATA=`，没有可写目录直接报错，不回退 C 盘 | ISCC 解析通过；`packaging\stage_payload.py` 的 START-HERE 文案同步 |

未动 `dist\`、`D:\LiuHao`、`D:\LiuHaoData`、`%LOCALAPPDATA%\LiuHao`；冻结仍等指令。

## 0. 结论

**当前不具备冻结条件。** 5 条不满足，其中 3 条是阻塞项：

| 类别 | 结论 |
| --- | --- |
| 工作树 | 227 项未提交改动，且 180 秒内仍有 15 个产品文件被并发会话改写 → 阻塞 |
| 前端产物 | `apps/console/console/dist/index.html` 比最新源码旧（13:51:49 < `styles.a11y.test.ts` 13:52:37）→ 阻塞 |
| `stage_payload` | 品牌字面门禁不通过（`LIUHAO X` × 7 文件、`FastAPI 版本仍为 1.0.0` × 8 处）→ 阻塞 |
| Python 全量测试 | 第一次运行在 11% 处**进程级崩溃**；第二次仍在跑，尚无失败断言 → 不能判绿 → 阻塞 |
| console 单测 | 3 failed / 343 passed → 阻塞 |
| `dist\` | 23 项历史产物，且 `dist\installer` 里已有一个**同版本** `LiuHao-Setup-0.1.1.exe` → 需人工确认 |
| 环境变量三级 | 干净 → 通过 |
| 运行时依赖链 | 完整（68 个 import 图发行包 + 21 个显式兜底）→ 通过 |
| 版本号全链 | 0.1.1 一致（pyproject / src/version.py / liuhao.iss）→ 通过 |
| 解释器 / ISCC | Python 3.12.14、`C:\Program Files (x86)\Inno Setup 6\ISCC.exe` 均在位 → 通过 |

所以：**等你下冻结指令时，需要先让仓库进入干净可复现状态（提交 + 前端构建 + 品牌门禁修好 + 测试转绿），再用下面的脚本一次成包。**

## 1. 冻结后一键打包脚本

新增两个文件，均在 `scripts/release/`：

| 文件 | 作用 |
| --- | --- |
| [package-frozen.ps1](file:///d:/LiuHao-AI-OS/scripts/release/package-frozen.ps1) | 主编排脚本，`-DryRun` 只校验 |
| [freeze_checks.py](file:///d:/LiuHao-AI-OS/scripts/release/freeze_checks.py) | 四条硬门禁：`deps` / `env` / `frontend-source` / `frontend-payload` |

### 1.1 固定下来的参数

| 项 | 值 |
| --- | --- |
| 仓库 | `d:\LiuHao-AI-OS`（可用 `-RepoRoot` 指向干净 worktree） |
| 版本 | 从 `pyproject.toml` 读，可用 `-Version` 覆盖；必须与 `src/version.py`、`installer/liuhao.iss` 一致 |
| 构建 Python | 优先 `-PythonExe` → `$env:LIUHAO_RELEASE_PYTHON` → `D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe` → 仓库 `.venv`；**必须是 3.12.x** |
| ISCC | `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`（可用 `-IsccPath` 覆盖；缺失即停，不伪造 exe） |
| 输出根 | 默认 `dist\freeze-<版本>`；**必须不存在或为空**，拒绝复用旧 payload |
| 前端基线 | `%LOCALAPPDATA%\LiuHaoBuild\frontend-baseline.json`（构建机状态，不写仓库，避免构建后变脏；可用 `LIUHAO_FREEZE_BASELINE` 覆盖） |

### 1.2 执行顺序

```
前置门禁（干净 / 无并发写 / 版本 / 解释器 / ISCC / 输出目录 / dist 现状）
  → 环境闸 env_guard（进程 + 用户 + 机器三级）
  → 运行时依赖链 deps（真跑 _MEASURE_CODE 的 import 图）
  → 前端基线 frontend-source（指纹 + 模块数）
  → [build_windows_installer.ps1]
        环境闸（其内部第 0 步）→ 删 tsbuildinfo → npm run build（tsc -b && vite build）
        → stage_payload → 凭据检查 → smoke_payload → 清理运行残留
        → verify_payload_clean → make_release（zip + 清单）→ ISCC 编译
  → 环境复检 env（确认构建途中没人改环境）
  → 前端模块数基线（取构建日志里的 `N modules transformed` 与基线比对）
  → payload 前端同源（签章指纹 == 当前 `apps/console/console/dist` 指纹）
  → 产物与 SHA256 记录
```

说明一处**已知偏差**：`env_guard` 没有插到 `stage_payload → smoke_payload` 与 ISCC 之间，而是
「构建前一次 + 构建后一次」。原因是不改 `packaging/build_windows_installer.ps1`（它正被其它线
编辑）。补偿措施是脚本在调子进程前**主动剥离** `LIUHAO_*` / `AUDIT_DB_*` 等变量并打印剥离清单，
所以污染根本进不了 npm / uvicorn / 冒烟；编译后再复检一次，构建途中被设也会被抓住。

### 1.3 产物命名与校验记录

| 产物 | 路径 |
| --- | --- |
| 安装包 | `<OutRoot>\installer\LiuHao-Setup-<版本>.exe` |
| 升级包 | `<OutRoot>\release\liuhao-<版本>.zip` |
| 校验和 | `<OutRoot>\installer\SHA256SUMS.txt`（`<sha256>  <文件名>`，ASCII） |
| 构建记录 | `<OutRoot>\FREEZE-RECORD.json`（版本 / `SOURCE-COMMIT` / 构建时间 / 解释器及版本 / ISCC 及版本 / 前端模块数 / exe 与 zip 的体积与 SHA256 / 全部门禁结论） |
| 构建日志 | `<OutRoot>\build.log` |

真实构建结束会打印三要素（版本 / 体积 / SHA256）+ `SOURCE-COMMIT`。
**同名包提醒**：`dist\installer` 里已有一个 13:33 生成的 `LiuHao-Setup-0.1.1.exe`（34,800,976 字节）。
冻结包写到 `dist\freeze-0.1.1\installer\`，不会覆盖它，但两份同名不同内容必须只对外发冻结目录里那一份，
交付时以 `FREEZE-RECORD.json` 的 SHA256 为准。

### 1.4 Dry-run 语义（本轮已实跑）

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\package-frozen.ps1 -DryRun
```

只做只读检查：不写 `dist\`、不建 `dist\freeze-0.1.1`、不跑 `npm build`、不组 payload、不冒烟、不编译。
实跑后确认 `dist\freeze-0.1.1` 不存在（未被创建）。有 FAIL 时退出码 1。

## 2. 冻结前条件现况表

| # | 条件 | 判定 | 命令 | 实际输出摘要 |
| --- | --- | --- | --- | --- |
| 1 | 工作树没有未提交改动 | **FAIL** | `git status --porcelain \| Measure-Object -Line` | **227 项**未提交（会话期间 207 → 213 → 218 → 227 持续增长）；`HEAD` 从 `23429c62` 一路前移到 `8e40d985`，说明别的线在持续提交 |
| 2 | 没有会话正在写文件 | **FAIL** | 扫描 `src/apps/packaging/installer/config/tests/scripts` 近 180 秒 mtime | **15 个文件**在 180 秒内被改：`scripts\launch_installed.py`(13:52:21)、`tests\gateway\test_chat_reliability.py`(13:52:21)、`apps\console\console\src\App.css`(13:52:09)、`src\ai\liuhao.py`(13:51:52)、`apps\console\console\src\os.css`(13:51:44)；更早一次为 24 个 |
| 3 | Python 全量测试绿 | **不能判绿** | `.venv\Scripts\python.exe -m pytest -q` / `-m pytest -v` | 收集到 **8,074** 条。第一次：跑到 11% 处 `Windows fatal exception: page error` 进程级崩溃（非断言失败）。第二次：跑到 15%（约 1,300 行全 PASS，无 FAIL）后仍在运行，未出总结 |
| 4 | console `tsc` 通过 | **PASS** | `npx tsc -b --force`（`apps/console/console`） | 退出码 0，无诊断输出（用 `--force` 绕过 tsbuildinfo 假诊断） |
| 5 | console 单测通过 | **FAIL** | `npx vitest run` | `Test Files 2 failed \| 33 passed (35)`；`Tests 3 failed \| 343 passed (346)`，50.38s。失败：`MyEmployees.test.tsx > 执行个体行只展示序号与状态…`、`MyEmployees.test.tsx > 系统默认员工不显示内部标识…`、`nonRootSurface.test.tsx > 审计页不出现全站摘要卡`。对应 `MyEmployees.tsx` 在 13:28 仍被并发修改，属中间态 |
| 6 | `stage_payload` 能跑通 | **FAIL** | `python packaging\stage_payload.py --out D:\cache\temp\freeze-evidence\stage-probe2 --version 0.1.1` | 走到 `[5/5]` 后中止：`品牌字面门禁未通过` —— `LIUHAO X` 出现在 `capability-registry.yaml`、`scripts/start_liuhao.py`、`src/ai/chat.py`、`src/ai/liuhao.py`、`src/kernels/_base.py`、`src/kernels/_registry.py`、`src/kernels/plugin/__init__.py`；`FastAPI 版本仍为 1.0.0` 8 处（`src/ai/e2e_demo.py` 等） |
| 7 | `stage_payload`（第一次尝试） | 参考 | 同上，13:33 首次 | 卡在离线运行时自检：`src/logging_config.py` 的 `NameError: name 're' is not defined`。核对：该文件 13:35:57 被并发改过；**当前文件第 29 行有 `import re`，`import src.gateway.main` 已 OK**。说明第一次是撞上了别人的半成品快照，不是常驻缺陷 |
| 8 | `dist\` 干净 | **WARN/需确认** | `Get-ChildItem dist` | **23 项**历史产物（`payload`、`installer`、`release`、`perf-20261108`、`walkthrough-20261108`…）。`dist\installer` 里：`LiuHao-Setup-0.1.0.exe`(34.7MB, 11:14)、`LiuHao-Setup-0.1.1.exe`(34.8MB, 13:33)、`liuhao-0.1.0.zip`。`dist\payload` 已是 0.1.1，但签章指纹 `87e26da0…` ≠ 当前 `apps/console/console/dist` 的 `4103c771…` → **旧 payload，不能复用** |
| 9 | 环境变量三级干净 | **PASS** | `freeze_checks.py env` | `进程/用户/机器三级环境干净；冒烟隔离仍按 LIUHAO_* 前缀整体剥离` |
| 10 | 运行时依赖链完整 | **PASS** | `freeze_checks.py deps --python <发布 venv>` | `68 个 import 图发行包 + 21 个显式兜底`；`openai=3.13.0 httpx=0.28.1 httpcore=1.0.9 httpx2=2.12.0 httpcore2=2.12.0 truststore=0.10.4 anthropic=1.3.0 google-genai=2.23.0 …`；`pywin32` 子模块（`win32con`/`ntsecuritycon`）无发行包映射但由 `EXPLICIT_DISTS` 整包兜底，已显式放行并打印 |
| 11 | 版本号全链 0.1.1 | **PASS** | dry-run 门禁 | `pyproject=0.1.1，src/version.py=0.1.1，liuhao.iss=0.1.1` |
| 12 | 构建解释器 | **PASS** | `python -c "import sys;…"` | `3.12.14` @ `D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe`（base 为 uv 的 CPython 3.12） |
| 13 | ISCC | **PASS** | `Test-Path` + 版本资源 | `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`（该文件未标注版本资源） |
| 14 | 输出目录全新 | **PASS** | dry-run 门禁 | `dist\freeze-0.1.1` 尚不存在（dry-run 也没有创建它） |

### 2.1 `stage_payload` 组装实测量（第二次尝试，除品牌门禁外全过）

| 阶段 | 结果 |
| --- | --- |
| [1/5] 依赖测量 | 70 个发行包 |
| [2/5] 源码与配置 | `src` 552 文件；`config` 12 文件（已剔除凭据/私钥/容器配置）；`model_pricing.json` 移除 1 个开发机路径字段 |
| [3/5] 驾驶舱与脚本 | `console` 10 文件，契约签章 `api_contract=1`、指纹 `87e26da0…`；`scripts` 10 文件 |
| [4/5] 离线运行时 | stdlib 1,848 文件；deps 14,030 文件（70 个发行包）；自检通过（Python 3.12.14，含 tkinter 与 pywin32） |
| [5/5] 入口与许可 | `LiuHao.bat` / `START-HERE.txt` / `assets/liuhao.ico`；`THIRD-PARTY-NOTICES.txt` + `licenses` 17 文件 |
| 品牌字面门禁 | **中止**（见上表第 6 行） |

## 3. 三条历史坑 → 硬失败（含负例实测）

| 坑 | 拦截点 | 负例（本轮实跑） | 实际输出 |
| --- | --- | --- | --- |
| `_MEASURE_CODE` 传递依赖遗漏 | `freeze_checks.py deps`：①静态核对 `_MEASURE_CODE` 是否仍逐条导入 `openai/httpx/httpcore/httpx2/httpcore2/truststore/anthropic/google.genai/docx/pypdf/sqlalchemy/taskiq/uvicorn/dotenv/aiosqlite`；②真跑 import 图，要求 `REQUIRED_MEASURED_DISTS` + `REQUIRED_TRANSITIVE`（含 `google-auth`、`requests`、`urllib3`、`pydantic-core`…）全部命中；③残留模块必须能映射到发行包 | 复制 `stage_payload.py` 后删掉 `import google.genai` | `[gate-deps] FAIL: _MEASURE_CODE 缺少关键导入：google.genai`，退出码 **2** |
| 同上 | 同上 | 删掉 `import taskiq` | `[gate-deps] FAIL: _MEASURE_CODE 缺少关键导入：taskiq`，退出码 **2** |
| 机器级 `LIUHAO_*` 污染冒烟 | `freeze_checks.py env`：①`env_guard` 原有的「值指向仓库/临时目录」检查；②新增**注册表存在性**检查——用户级/机器级出现任何受监控变量即停（不是只看值）；③回归检查 `smoke_payload._ISOLATED_PREFIXES` 必须仍含 `LIUHAO_`、`_ISOLATED_EXACT` 必须含 5 个路径变量；④真实构建前把可疑变量从子进程环境剥离并打印清单 | 模拟机器级 `LIUHAO_AUTH_SECRETS_FILE`（monkeypatch 注册表读取，未真改本机） | `[gate-env] FAIL: 注册表里残留机器级/用户级配置… LIUHAO_AUTH_SECRETS_FILE [machine] = …`，退出码 **2** |
| 旧工作树打包 → 前端模块数不一致 | `frontend-source` + `frontend-payload`：源码指纹（`src/` + `public/` + `package*.json` + `vite/tsconfig`）与构建机基线比对；模块数取自构建日志 `N modules transformed` 与基线比对；payload 签章指纹必须等于当前 `apps/console/console/dist` 的指纹，且文件数一致 | 伪造基线指纹 | `[gate-frontend] FAIL: 前端源码与冻结基线不一致…`，退出码 **2** |
| 同上 | 同上 | 基线模块数 80，传 `--module-count 79` | `[gate-frontend] FAIL: 前端模块数与基线不一致：基线 80，本次构建 79`，退出码 **2** |
| 同上 | 同上 | 对现有 `dist\payload\app` 跑同源校验 | `[gate-frontend] FAIL: payload 里的控制台与当前工作树的构建产物不同源：签章 87e26da0… ≠ apps/console/console/dist 4103c771…`（**这不是构造的负例，是当前仓库真实状态**） |

基线策略：首次真实构建采集并写入 `%LOCALAPPDATA%\LiuHaoBuild\frontend-baseline.json`；
之后每次构建都比对，不一致即停。确实要更新时显式加 `-AcceptFrontendBaseline`（会在输出里写明是
「承认变更」而不是静默放行）。`scripts/release/frontend-baseline.json` 不再进仓库，避免构建后工作树变脏。

采集/复核链路已实测：`frontend-source --capture --module-count 80` 写入
`{module_count: 80, file_count: 122, source_fingerprint: 944ddb70…}`，紧接着不带参数复核
立即通过（提示「待本次构建确认模块数是否仍为 80」）。

## 4. 可以构建的勾选条件

冻结指令下达后，逐条确认再开跑：

- [ ] `git status --porcelain` 为空（或 `-RepoRoot` 指向一个干净 worktree，且该 worktree 的 HEAD 就是冻结提交）
- [ ] 180 秒内 `src/apps/packaging/installer/config/tests/scripts` 无改动（无并发会话在写）
- [ ] `pytest` 全量通过（8,074 条，0 failed / 0 error / 无进程崩溃）
- [ ] `npx tsc -b --force` 退出码 0
- [ ] `npx vitest run` 0 failed
- [ ] `python packaging\stage_payload.py` 能走完 `[5/5]`（品牌字面门禁通过）
- [ ] `freeze_checks.py env` PASS（进程/用户/机器三级干净）
- [ ] `freeze_checks.py deps` PASS
- [ ] `pyproject.toml` / `src/version.py` / `installer/liuhao.iss` 版本一致且已升到本版新号
- [ ] `dist\freeze-<版本>` 不存在或为空
- [ ] 确认 `dist\installer\LiuHao-Setup-0.1.1.exe` 这个旧同名包不会被当成新交付物（或先人工移走）
- [ ] `package-frozen.ps1 -DryRun` 退出码 0

## 5. 冻结当天的建议执行次序

1. 等所有并行线把改动提交完，打一个冻结提交（或从该提交起一个干净 worktree，用 `-RepoRoot` 指过去）。
2. 先跑 `-DryRun`，要 0 FAIL。
3. 真实构建：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\package-frozen.ps1 -Version <新版本>`
4. 从 `<OutRoot>\FREEZE-RECORD.json` 取三要素与 `SOURCE-COMMIT`，安装验收用独立临时目录 + 独立端口 + `/NOICONS`（避免再次污染真实桌面），不要动 `D:\LiuHao`、`D:\LiuHaoData`、`%LOCALAPPDATA%\LiuHao`。

## 6. 备注

- 本轮**没有**修改任何他人的文件；新增文件只有 `scripts/release/package-frozen.ps1`、`scripts/release/freeze_checks.py` 与本报告。
- 探针产物全部落在 `D:\cache\temp\freeze-evidence\`（`pytest.log`、`pytest-v.log`、`stage-probe*`、`dryrun*.log`），未写 `dist\`。
- 第 6 行的品牌门禁（`LIUHAO X` / `FastAPI 版本 1.0.0`）说明仓库里正有一轮品牌改写未收尾；这属于别人的线，我没有改，冻结前必须由对应线收口。
- 第 3 行的 `Windows fatal exception: page error` 发生在 pytest 的捕获/回收阶段，同机当时有约 60 个 python 进程与多条构建并行；不能据此断言是代码缺陷，但**在复现出一次干净的全绿之前，全量测试不能算绿**。
