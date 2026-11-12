# 产品完整性审计（只读）

- 审计对象：`d:\LiuHao-AI-OS`（鎏灏 AI OS，仓库版本 `0.1.1`）
- 审计时间：2026-11-08 00:00–00:45（Asia/Shanghai）
- 审计方式：隔离数据目录起真实网关进程 + HTTP 请求取证 + 浏览器 UI 走查 + 代码行号/调用链核对
- 只读边界：未改任何代码、未 commit、未 git stash；`src/desktop/**`、`packaging/**`、`installer/**`、`src/ai/providers.py` 只读；未运行 `setup.exe`
- 重要前提：审计期间有**并发会话**在修改 `src/gateway/main.py`（mtime `2026-11-08 00:39:01`）与 `src/tasks/scheduler_host.py`（`00:30:30`）。本报告以该时间点快照为准，运行态证据来自 `00:10–00:31` 启动的旧实例与 `00:41` 启动的当前工作树实例。

---

## 0. 证据来源（隔离实例）

| 端口 | 数据目录 | 用途 | 状态 |
|---|---|---|---|
| 18080 | `D:\cache\temp\prod-audit-20261108` | boss + alice、Ollama 真实模型、2 目标、知识库 ZEBRA-7788、备份包 | 运行中 |
| 18081 | `D:\cache\temp\prod-audit-fresh-20261108` | 全新实例导入 18080 备份包 | 运行中 |
| 18082 | `D:\cache\temp\prod-audit-firstrun-20261108` | 空实例 UI 首次创建账号 | 运行中 |
| 18083 | `D:\cache\temp\prod-audit-schedcheck-20261108` | 当前工作树启动复测 | **启动失败（历史快照）** |
| 18084 | `D:\cache\temp\prod-audit-current-20261108` | 当前工作树（00:39 之后）启动复测 | 运行中，`ready` |
| 8080 | `D:\LiuHao`（安装版） | 只读健康检查 | 运行中，`version=1.0.0` |

浏览器：page 16 = 18080 boss 已登录；page 17 = 18082 boss 已登录。

---

## 1. PROD 条目

### PROD-001 当前工作树网关启动失败（历史 P0，已被并发修复）

- 模块：网关启动 / 排程器托管
- 文件:行号：[main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1662)（当前）与历史失败点 [main.py:1642](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1642)
- 表现：`00:36` 启动 18083 时抛出 `NameError: name 'asyncio' is not defined`，随后 `ERROR: Application startup failed. Exiting.`，整个网关无法启动；前端登录框打不开。
- 是否复现：**已复现（历史快照）**。步骤：隔离数据目录执行 `.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --host 127.0.0.1 --port 18083`；结果：`D:\cache\temp\prod-audit-schedcheck-20261108\gateway.log` 完整 traceback，最后一行 `ERROR: Application startup failed. Exiting.`。`00:41` 用当前工作树启动 18084 后 `/v1/startup` 返回 `state=ready core_ready=true`，排程器步骤 `ok=true`，证明已被并发会话修复。
- 根因：`lifespan` 在 `_deferred_scheduler` 线程启动前调用 `asyncio.get_running_loop()`，但当时模块顶层未 `import asyncio`；并发会话先在 1642 行引入调用，后补了 `import asyncio`（当前第 11、36 行）。
- 影响面：100% 阻断——多用户/网页端/手机端全部不可用。
- 安全或数据风险：无数据损坏；但启动即退出，用户无任何界面可自救（只能在终端看 traceback）。
- 修复方案：已由并发会话补 `import asyncio`；建议同时加启动冒烟测试（空数据目录启动 → 断言 `/v1/health` 200），防止再次回归。
- 需要改的文件：`src/gateway/main.py`（已改）
- 验证方法：空目录启动后 `GET /v1/health` 200 且 `GET /v1/startup` 的 `state=ready`；本报告已用 18084 验证。
- 等级：P0
- 状态：CONFIRMED FIXED（00:43 实例验证通过；修复来自并发会话，非本次审计改动）

---

### PROD-002 手机 / 局域网写请求被 Origin 白名单拦截，手机端开箱不可用

- 模块：安全中间件 / 手机端 / 首次登录
- 文件:行号：[main.py:847-862](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L847-L862)（写方法 Origin 校验与 403 响应）、[main.py:198-221](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L198-L221)（回环同端口放行）、[main.py:224-246](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L224-L246)（开发态回环 Origin 名单）
- 表现：同一局域网用 `Origin: http://192.168.31.171:18084` 调用 `POST /v1/auth/login` 返回 `403 origin_not_allowed`；同源回环 `Origin: http://127.0.0.1:18084` 通过 Origin 守卫（进入参数校验返回 422）。手机浏览器登录、发消息、扫码接管全部失败。
- 是否复现：**已复现**。步骤：`curl -X POST http://127.0.0.1:18084/v1/auth/login -H "Origin: http://192.168.31.171:18084"` → 403；换回环 Origin → 422（说明是 Origin 判定而非账号问题）。旧实例 18080 亦同。
- 根因：写方法只接受「精确白名单」或「本机回环 + 实际监听端口」；`development_loopback_origins()` 只列 `127.0.0.1/localhost/[::1]`，不包含局域网 IP。安装版默认只绑 `127.0.0.1`，手机必须显式 `--host 0.0.0.0`，此时局域网 Origin 仍不在名单。
- 影响面：手机端/局域网协作全链路（登录、对话、派活、审批）；与「三端共用同一账号」的界面承诺直接冲突。
- 安全或数据风险：防护本身正确（防跨站写），但默认策略让宣称的移动端不可用；若用户为绕过去设 `LIUHAO_CORS_ALLOW_ORIGINS=*`，会引入真实 CSRF 风险。
- 修复方案：绑非回环地址时，自动把「本机实际局域网 IP + 当前端口」加入写 Origin 白名单（仅限同端口、且 `Host` 校验通过）；同步在设置页给出「允许的手机来源」可视化配置与风险说明；文档明确手机必须走该配置。
- 需要改的文件：`src/gateway/main.py`（Origin 推导）、`apps/console/console/src/pages/Settings.tsx`（入口/说明）、`docs/mobile-access.md`
- 验证方法：`--host 0.0.0.0` 启动后用 `Origin: http://<LAN-IP>:<port>` 调登录应 200；非本机来源仍 403；用真实手机浏览器完成「登录 → 发消息」。
- 等级：P1
- 状态：OPEN

---

### PROD-003 总览「系统健康度 100% / 模型状态无异常」与生产预检、告警中心直接矛盾

- 模块：总览 / 诊断 / 告警
- 文件:行号：[Overview.tsx:258-265](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Overview.tsx#L258-L265)（健康度取 `/v1/ready` 比例）、[Overview.tsx:151-155](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Overview.tsx#L151-L155)（模型状态 chip）
- 表现：同一时刻三处结论互斥：
  - `GET /v1/ready` → `status=ready`、`user_message="系统就绪，全部检查通过"`（总览据此显示 100%）；
  - `GET /v1/production/preflight` → `ok=false, critical_count=1`，`SECRET_KEY 未设置：会话/加密派生密钥为空`；
  - `GET /v1/alerts` → `count=77, firing_count=74`，含 `High Error Rate ... error_rate_percent = 50.0 exceeds threshold 5.0`（CRITICAL，`state=FIRING`）。
- 是否复现：**已复现**。步骤：用 18080 boss token 依次请求上述三个端点，响应原文如上；UI 总览显示「系统健康度 100%」与「模型状态 当前无异常」。
- 根因：`/v1/ready` 只检查组件可用性，不含生产配置预检与告警状态；总览没有把预检/告警纳入健康度聚合，也没有降级提示。
- 影响面：用户误以为系统完全健康，忽略 SECRET_KEY 缺失与 74 条正在告警；排障方向被误导。
- 安全或数据风险：`SECRET_KEY` 为空影响会话/加密派生密钥，属安全配置风险；界面隐藏该紧急项。
- 修复方案：总览健康度改为「就绪 + 预检阻断项 + CRITICAL 告警」的聚合态；存在 critical/preflight 失败或 firing ≥ 阈值时，必须降级为「需处理」并给跳转。
- 需要改的文件：`src/gateway/dashboard.py`、`apps/console/console/src/pages/Overview.tsx`
- 验证方法：构造 `SECRET_KEY` 缺失 + 告警 firing 的实例，总览不得显示 100%/无异常；修复后同实例应显示「存在阻断项」并链接到预检/告警页。
- 等级：P1
- 状态：OPEN

---

### PROD-004 总览/接口把真实 Ollama 报成 mock（provider 状态双源）

- 模块：总览 / 模型配置 / 健康检查
- 文件:行号：[dashboard.py:51-53](file:///d:/LiuHao-AI-OS/src/gateway/dashboard.py#L51-L53)（读 `AI_PROVIDER_TYPE` 默认 `mock`，不读已保存模型配置）
- 表现：`GET /v1/health` 返回 `ai_provider.type=ollama, mode=real, source=model_config, display_name=本地 Ollama`；同一实例 `GET /v1/dashboard/summary` 返回 `provider.type=mock, mode=mock`，但同响应里 `model=qwen2.5:3b, name=audit-ollama`。总览与能力注册表据此显示「模拟/需要配置密钥」。
- 是否复现：**已复现**。步骤：18080 用 boss token 分别请求 `/v1/health` 与 `/v1/dashboard/summary`，响应矛盾如上。
- 根因：dashboard 的 `_provider_info` 走环境变量 `AI_PROVIDER_TYPE`（未设置时默认 mock），而真实生效 provider 在模型配置库中；两套解析源未统一。
- 影响面：用户已配置真实模型却被告知是模拟；能力注册表/总览/健康页三处口径不一致。
- 安全或数据风险：低；但会诱导用户重复配置或误判模型输出为假。
- 修复方案：`_provider_info` 复用 `/v1/health` 的 provider 快照（model_config 优先，env 兜底），单一数据源。
- 需要改的文件：`src/gateway/dashboard.py`
- 验证方法：保存 Ollama 配置后，`/v1/dashboard/summary.provider.type` 必须等于 `/v1/health.ai_provider.type`；再切云端 provider 复测。
- 等级：P1
- 状态：OPEN

---

### PROD-005 会话轮次恒为 0；「审计事件」三处数字互不一致

- 模块：总览 / 数据中心
- 文件:行号：[dashboard.py:157](file:///d:/LiuHao-AI-OS/src/gateway/dashboard.py#L157)（`store.get_last_turn(f"liuhao-{sid}")` 前缀错误）
- 表现：
  - `GET /v1/chat/history?session_id=audit-s1` 返回 `count=2, total=2`（2 条真实消息）；
  - `GET /v1/dashboard/summary` 的 `sessions.detail[audit-s1].turns=0`；
  - 审计事件：`/v1/dashboard/summary.audit.total_events=24`、`/v1/ready.audit_store.total_events=372`、`/v1/dashboard/analytics?days=14` 当日 `total=9`（近 14 天 11 条）。
- 是否复现：**已复现**。步骤：18080 同 token 请求三个端点，数字如上；UI 总览「审计事件 24」与「近 14 天真实事件数」图不一致。
- 根因：会话键实际为 `liuhao-{principal}-{session}`，dashboard 只拼 `liuhao-{sid}`；审计计数分别来自子树过滤、全库就绪检查、分析窗口，未做口径说明。
- 影响面：总览「运行中任务/会话轮次/审计事件」KPI 失真，用户无法据此判断活跃度。
- 安全或数据风险：低。
- 修复方案：用正确会话键或从会话存储直接取轮次；审计 KPI 统一口径并在脚注写清「当前账号子树 / 近 14 天 / 全库」。
- 需要改的文件：`src/gateway/dashboard.py`、`apps/console/console/src/pages/Overview.tsx`
- 验证方法：发 1 条消息后 `history.count` 与 `summary.sessions.detail.turns` 必须一致；三处审计数字脚注口径自洽。
- 等级：P1
- 状态：OPEN

---

### PROD-006 首次创建账号后「随后显示的恢复码」不即时显示，刷新才出现

- 模块：首次设置 / 登录 / 恢复
- 文件:行号：[Login.tsx:737](file:///d:/LiuHao-AI-OS/apps/console/console/src/components/Login.tsx#L737)（写入 localStorage）、[App.tsx:251](file:///d:/LiuHao-AI-OS/apps/console/console/src/App.tsx#L251)（state 只在挂载时读一次）、[App.tsx:846-863](file:///d:/LiuHao-AI-OS/apps/console/console/src/App.tsx#L846-L863)（恢复码横幅）、[Login.tsx:759](file:///d:/LiuHao-AI-OS/apps/console/console/src/components/Login.tsx#L759)（文案承诺「随后显示」）
- 表现：18082 空实例首次创建 boss 成功后直接进入总览，页面**没有**恢复码；手动刷新后顶部出现 `RC-ZB6J-8CU3-UWRK-W93T-7RJ3-GR7M`。用户按页面提示「请务必妥善保存随后显示的恢复材料」时看不到任何材料。
- 是否复现：**已复现**。步骤：空数据目录启动 18082 → UI 首次设置创建 boss/FirstRunPass123! → 进入总览无横幅 → 刷新后出现恢复码。
- 根因：`savePendingRecovery` 只写 localStorage，React 的 `pendingRecovery` 由 `useState` 初始化器在挂载时读取，后续写入不触发状态更新。
- 影响面：唯一一次恢复材料的暴露窗口被跳过；用户后续忘记密码只能走「本机取恢复码」或无法自助。
- 安全或数据风险：恢复能力实质上丢失，账号锁死风险升高。
- 修复方案：注册成功回调里同时 `setPendingRecovery(...)`（或让 App 订阅 storage 事件/context）；横幅在关闭前阻塞进入主界面。
- 需要改的文件：`apps/console/console/src/components/Login.tsx`、`apps/console/console/src/App.tsx`
- 验证方法：空实例首次创建后不刷新即可看到恢复码；关闭横幅后 localStorage 清除；再次登录不再显示。
- 等级：P1
- 状态：OPEN

---

### PROD-007 浏览器/手机无法自助重置密码（需桌面端一次性证明）

- 模块：账号恢复
- 文件:行号：[accounts.py:549-588](file:///d:/LiuHao-AI-OS/src/gateway/accounts.py#L549-L588)（`/v1/accounts/recovery/reset` 必须携带桌面命名管道发放的 proof；远程需 `LIUHAO_ALLOW_REMOTE_RECOVERY`）
- 表现：浏览器登录页「忘记密码 → 重置」请求返回 `ACCOUNT_RECOVERY_PROOF_REQUIRED`；桌面端可完成。手机端无桌面壳，等于永远无法自助重置。
- 是否复现：**已复现**。步骤：18082 浏览器调用 `/v1/accounts/recovery/reset`，响应码 `ACCOUNT_RECOVERY_PROOF_REQUIRED`；同机桌面端流程可用（文档描述）。
- 根因：安全设计把「本机证明」绑定到 DACL 命名管道，网页/手机形态没有该通道；且界面未提前告知「此路只在桌面端可用」。
- 影响面：「用户出问题能不能自己救自己」在网页/手机形态不成立；忘记密码必须联系管理员或回到本机。
- 安全或数据风险：设计本身降低被盗重置风险，但缺少替代恢复路径导致账号锁死。
- 修复方案：在网页/手机明确标注「恢复需在桌面端完成」，并提供「生成一次性转移链接/管理员审批」的可选路径；桌面端增加「导出恢复凭据」到本地文件。
- 需要改的文件：`src/gateway/accounts.py`（新增受控路径时）、`apps/console/console/src/pages/Recovery.tsx`、`apps/console/console/src/components/Login.tsx`、`docs/END-USER-GUIDE.md`
- 验证方法：网页端发起重置时提示明确且不产生误导性失败；桌面端完整走通；若启用远程开关，UI 必须显示高风险确认。
- 等级：P1
- 状态：OPEN

---

### PROD-008 开箱内置升级不可用；仓库版本与安装版不一致

- 模块：升级 / 版本
- 文件:行号：[manager.py](file:///d:/LiuHao-AI-OS/src/upgrade/manager.py)（需 `LIUHAO_UPGRADE_MANIFEST_URL` 或 `LIUHAO_UPGRADE_RELEASE_DIR`）、[version.py](file:///d:/LiuHao-AI-OS/src/version.py)（`APP_VERSION=0.1.1`）
- 表现：
  - `GET /v1/upgrade/status` → `source_configured=false`，`message="未配置更新源..."`，`latest_version=null`；
  - `GET /v1/upgrade/check` → 405；`POST /v1/upgrade/check` → `409 source_not_configured`；
  - 设置页「检查更新」按钮不可用；
  - 仓库 `/v1/health` 返回 `version=0.1.1`，安装版 8080 `/v1/health` 返回 `version=1.0.0`。
- 是否复现：**已复现**。步骤：18080/18084 请求 upgrade 端点，响应如上；8080 安装版 health 只读对比。
- 根因：安装器/启动器未写入任何更新源；升级管理器只能读环境变量。仓库与已安装包不是同一产物。
- 影响面：8 条剧本「升级一次」阻塞；用户无法在界面完成版本升级，也无法确认当前运行的是否为所装版本。
- 安全或数据风险：无更新通道意味着安全补丁无法下发；版本号不一致会误导排障与备份兼容判断。
- 修复方案：安装器写入默认 release 目录或 manifest 地址；未配置时按钮应给出「如何配置更新源」的可执行说明而不是灰死；统一版本号来源。
- 需要改的文件：`src/upgrade/manager.py`、`installer/**`（只读约束下需由安装器负责人修改）、`apps/console/console/src/pages/Settings.tsx`、`src/version.py`
- 验证方法：全新安装后 `/v1/upgrade/status.source_configured=true`；`POST /v1/upgrade/check` 返回 200 与 available 状态；安装版 health 版本号与安装包一致。
- 等级：P1
- 状态：OPEN

---

### PROD-009 本地排程器历史启动失败（提醒/定时不触发），当前已修复

- 模块：后台任务 / 提醒
- 文件:行号：[scheduler_host.py:171](file:///d:/LiuHao-AI-OS/src/tasks/scheduler_host.py#L171)（历史 `asyncio.create_task` 在无事件循环线程执行）；当前 [main.py:1594-1609](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1594-L1609)（显式传 loop 并上报失败）
- 表现：旧实例日志：
  - 18081/18080：`本地排程器启动失败：RuntimeError: no running event loop`，栈帧 `scheduler_host.py:171`；
  - 18082：`Exception in thread local-scheduler-host ... NameError: name 'scheduler_loop' is not defined`。
  当前 18084：`/v1/startup` 出现 `本地排程器：运行中`，后台步骤 `ok=true`，并落盘 `data/run/local_scheduler.lock` 与 `workspace/queue/local_scheduler.db`。
- 是否复现：**已复现（历史失败）+ 已修复验证**。步骤：查三份 `gateway.log`；再看 18084 启动结果与锁文件。
- 根因：排程器协程必须在网关事件循环里 `create_task`，早期把启动放在独立线程且未传 loop；并发修复后显式传 loop。
- 影响面：修复前提醒、定时任务、失败重试全部静默不触发。
- 安全或数据风险：任务不触发属功能缺失，无数据损坏。
- 修复方案：已完成；建议补「创建一条 1 分钟后提醒 → 断言到点写入/触发」的端到端回归。
- 需要改的文件：`src/tasks/scheduler_host.py`、`src/gateway/main.py`（已改）
- 验证方法：启动后 `/v1/startup` 排程器步骤 ok；创建提醒并等待触发。
- 等级：P1
- 状态：CONFIRMED FIXED（00:43 验证）

---

### PROD-010 知识中心同页文案自相矛盾（支持格式与大小上限）

- 模块：知识库
- 文件:行号：[Knowledge.tsx:769](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Knowledge.tsx#L769)、[Knowledge.tsx:808](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Knowledge.tsx#L808)（称支持 PDF/Word/网页/纯文本）、[Knowledge.tsx:1431](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Knowledge.tsx#L1431)（称 PDF/Word/图片没有解析器，只收 UTF-8 ≤5MB）、[knowledge.ts:39-43](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/knowledge.ts#L39-L43)（`UPLOAD_MAX_BYTES=20MB`、`SYNC_UPLOAD_MAX_BYTES=5MB`、后缀含 pdf/docx/html/htm/txt/md）
- 表现：同一页面一处说支持 PDF/Word，另一处说「没有解析器，不会假装解析」；一处 5MB 上限，另一处 20MB。
- 是否复现：**已复现（实测能力 + 文案对照）**。步骤：18080 上传真实 `real.docx`、`test.html`、文本 md 均成功入库并可检索；空白 PDF 返回「无文本层」；无效 DOCX/PDF 被拒。说明 PDF/Word/HTML 支持真实存在，1431 行是过时错误文案。
- 根因：上传能力升级后未清理旧文案；同步/异步两个阈值未在页面区分说明。
- 影响面：用户可能因「不支持 PDF/Word」文案而不上传，或以为 5MB 是硬上限。
- 安全或数据风险：低。
- 修复方案：统一为「支持 PDF/DOCX/HTML/TXT/MD；单文件 ≤20MB，≤5MB 同步、更大走后台任务」；删除「没有解析器」旧段落。
- 需要改的文件：`apps/console/console/src/pages/Knowledge.tsx`
- 验证方法：页面文案与 `lib/knowledge.ts` 常量、后端 `SUPPORTED_SUFFIXES` 一致；上传 6MB DOCX 触发异步任务。
- 等级：P2
- 状态：OPEN

---

### PROD-011 「导出备份再导入」同机被拒，只有全新实例可导入

- 模块：备份与迁移
- 文件:行号：[Backup.tsx](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Backup.tsx)（页面限制）、账号导入接口（仅全新实例）
- 表现：18080 已创建账号后，同机 `POST` 备份导入被拒；18081 全新实例导入同一 726KB 备份包成功恢复 2 账号 / 4 员工 / 2 目标 / 32 chunks / 4 会话，原密码可登录。
- 是否复现：**已复现**。步骤：18080 导出 → 18080 导入（拒）→ 18081 空实例导入（成功）。
- 根因：导入流程要求「本机没有任何可登录身份」，且界面只在首次启动的登录页暴露入口；文档也明确此限制。
- 影响面：剧本 6「导出再导入」在常用同机场景走不通；普通用户不做 CLI 就无法完成完整换机。
- 安全或数据风险：限制本身防覆盖，但恢复路径缺失会在误删/迁移时卡死。
- 修复方案：提供「恢复到新命名空间（不改当前账号）」或「先导出当前状态再覆盖」的显式二次确认路径；普通用户可见的「一键换机」向导。
- 需要改的文件：`src/gateway/accounts.py`（导入端点）、`apps/console/console/src/pages/Backup.tsx`、`docs/END-USER-GUIDE.md`
- 验证方法：已有账号实例能按显式确认完成导入/回滚；全新实例导入保持现状。
- 等级：P2
- 状态：OPEN

---

### PROD-012 工作区根目录直接暴露内部数据库文件

- 模块：工作区 / 文件
- 文件:行号：`workspace/` 目录内容（`account_memory.sqlite3`、`account_usage.sqlite3`、`liuhao_artifacts.db`、`employees.json`、`goals.json`、`execution.journal`、`queue/`）
- 表现：文件页/工作区根目录能看到并访问内部实现库，而不是只看到用户产物。
- 是否复现：**已复现（目录枚举）**。步骤：枚举隔离实例 `workspace/`，上述文件与 `queue/local_scheduler.db*` 均在根目录。
- 根因：工作区与运行时数据目录未分离；内部状态直接落在用户可见区。
- 影响面：用户误删/误改会破坏运行态；文件页噪音大，产物难以定位。
- 安全或数据风险：内部库可能含账号记忆/用量/执行日志，暴露给有工作区读权限的成员。
- 修复方案：内部库移入 `data/`（与安装版一致），工作区只保留产物；文件页加白名单。
- 需要改的文件：`src/gateway/files.py`、`src/employees/store.py`、`src/common/paths.py`
- 验证方法：工作区根目录不再出现 `*.sqlite3/*.db/execution.journal`；内部库在 `data/`。
- 等级：P2
- 状态：OPEN

---

### PROD-013 审批中心只有记录能力，无实际拦截

- 模块：审批 / 治理
- 文件:行号：[ApprovalCenter 页面与后端审批记录]
- 表现：审批中心默认「记录型」、拦截数为 0；没有任何执行路径在等待审批时真正被阻断。
- 是否复现：静态判断（接口与页面均显示拦截 0；无「待审批 → 执行」链路）。
- 根因：审批作为审计记录旁路接入，未成为执行前置门。
- 影响面：用户以为高风险动作会被审批拦住，实际照常执行。
- 安全或数据风险：高风险动作（批量删除/外部调用）缺少人工闸门。
- 修复方案：明确标注「当前仅记录，不拦截」，或把审批接入执行前置检查（executor fence 同层）。
- 需要改的文件：`src/gateway/approvals.py`（或对应模块）、`apps/console/console/src/pages/ApprovalCenter.tsx`
- 验证方法：构造需审批动作，执行必须挂起并有可批准/拒绝入口。
- 等级：P2
- 状态：OPEN

---

### PROD-014 「网络网关已具备」与 MCP/A2A/gRPC 无实现

- 模块：业务中心 / 能力注册表 / 网络
- 文件:行号：[mcp_adapter.py:51](file:///d:/LiuHao-AI-OS/src/adapters/mcp/mcp_adapter.py#L51)（`MCP_WIRING_STATUS = "adapter-only"`，默认关闭）、[capability-map.md:63](file:///d:/LiuHao-AI-OS/docs/capability-map.md#L63)（MCP 无运行时入口）
- 表现：能力注册表/业务页把「已具备」用于网络网关；实际 MCP 默认关闭且无调用方，A2A/gRPC 无实现，网络页只能看到 LLM 调用总线。
- 是否复现：**代码行号 + 调用链证明**：`initialize_mcp()` 在全仓库除适配器自身外无调用；`MCP_WIRING_STATUS="adapter-only"`。
- 根因：能力注册表以「代码存在」判定「已具备」，未区分「有实现 / 有入口 / 有消费方」。
- 影响面：用户按「已具备」配置后无任何可见效果；网络/信任页面长期空白。
- 安全或数据风险：低（默认关闭反而安全），但误导能力评估。
- 修复方案：注册表增加「可达性」维度；无消费方的能力标注「仅适配层」；业务页文案同步。
- 需要改的文件：能力注册表模块、`apps/console/console/src/lib/layerLabels.ts`、`apps/console/console/src/pages/Network.tsx`、`Trust.tsx`
- 验证方法：任选「已具备」能力，必须能在 UI 找到入口并产生一次可观测调用；否则显示「仅适配层」。
- 等级：P2
- 状态：OPEN

---

### PROD-015 能力注册表与健康检查对 provider 的结论相反

- 模块：能力注册表 / 模型
- 文件:行号：能力注册表 provider 判定（`AI_PROVIDER_TYPE` 同源问题，见 PROD-004）
- 表现：健康页显示 Ollama「真实模型」，能力注册表显示 Mock「当前生效」、Ollama「需要配置密钥」。
- 是否复现：**已复现**（18080 同实例 UI 对照 + `/v1/health` 响应）。
- 根因：与 PROD-004 同一双源问题，多处各自解析 env/模型配置。
- 影响面：用户无法判断哪个是当前真实模型。
- 安全或数据风险：低。
- 修复方案：统一 provider 快照服务，所有页面只读一份。
- 需要改的文件：`src/gateway/dashboard.py`、能力注册表路由
- 验证方法：三处 provider 字段完全一致。
- 等级：P2
- 状态：OPEN

---

### PROD-016 员工卡「提交 0/完成 0」与「目标 1/1 完成」同页矛盾

- 模块：我的员工 / 目标
- 文件:行号：`MyEmployees.tsx`、`Goals.tsx` 统计来源
- 表现：同一页面员工卡显示提交 0、完成 0，但目标显示 1/1 已完成。
- 是否复现：**已复现（UI 观察）**。步骤：18080 派活后看员工卡与目标卡。
- 根因：员工执行产物写入目标/产物库，未回写员工提交/完成计数。
- 影响面：员工绩效面板失真，用户无法评估谁做了事。
- 安全或数据风险：低。
- 修复方案：以执行日志单一来源统计员工提交/完成，或明确标注「仅统计某类任务」。
- 需要改的文件：员工统计接口、`apps/console/console/src/pages/MyEmployees.tsx`
- 验证方法：派活并完成 1 个目标后，员工提交/完成均 +1。
- 等级：P2
- 状态：OPEN

---

### PROD-017 提醒/日程无界面入口

- 模块：提醒 / 日程
- 文件:行号：[kernel_tools.py:387-390](file:///d:/LiuHao-AI-OS/src/ai/tools/kernel_tools.py#L387-L390)（仅对话工具）、[reminders.py](file:///d:/LiuHao-AI-OS/src/reminders.py)（仅 CLI `python -m src.reminders`）
- 表现：控制台无「提醒」或「日程」页面；只有 AI 对话里能创建提醒，查看/取消只能走 CLI。
- 是否复现：**已复现（全 UI 页面枚举 + 代码调用链）**：`apps/console/console/src/pages` 39 个页面中无提醒/日程页；`grep 提醒|日程` 仅命中测试与 Preflight 文案。
- 根因：后端具备提醒工具与排程器，前端从未接入。
- 影响面：用户创建提醒后无法管理；日程能力完全缺失。
- 安全或数据风险：低。
- 修复方案：新增「提醒/日程」列表页（读 reminders 库、可取消、显示失败重试）；或在对话回复里给出可点击管理入口。
- 需要改的文件：新增 `apps/console/console/src/pages/Reminders.tsx`、路由、`src/gateway/reminders.py` 路由
- 验证方法：UI 创建提醒 → 列表中可见 → 到点有可观测触发 → 可取消。
- 等级：P2
- 状态：OPEN

---

### PROD-018 手机端无扫码接管，三端共用会话承诺不成立

- 模块：手机端 / 登录
- 文件:行号：[App.tsx:4](file:///d:/LiuHao-AI-OS/apps/console/console/src/App.tsx#L4)（三端文案）、[Login.tsx:597](file:///d:/LiuHao-AI-OS/apps/console/console/src/components/Login.tsx#L597)（三端共用同一会话）、[mobile-access.md:16-35](file:///d:/LiuHao-AI-OS/docs/mobile-access.md#L16-L35)（只写手动 IP，手机侧未验证）
- 表现：全仓库控制台无二维码/扫码组件；`grep qrcode|二维码|扫码` 在 `apps/console` 0 命中（仅插件与文档字符串）。文档只给 `http://192.168.31.171:8080` 手动输入，且明确「手机侧未验证」。
- 是否复现：**已复现（检索 + 实测）**：无二维码入口；LAN 写请求 403（见 PROD-002）。
- 根因：移动端从未实现配对/扫码；文案先于能力上线。
- 影响面：剧本 5「手机扫码接管」无法开始。
- 安全或数据风险：若用户手动粘贴地址并放宽 Origin，反而引入风险。
- 修复方案：实现局域网配对二维码（含一次性令牌 + 短时效）或删除「扫码」相关表述并把手动 IP 流程做到可用（见 PROD-002）。
- 需要改的文件：`apps/console/console/src/components/Login.tsx`、`src/gateway/accounts.py`、`docs/mobile-access.md`
- 验证方法：手机扫码 → 登录 → 发消息全链路可用；或文案不再承诺扫码。
- 等级：P1
- 状态：OPEN

---

### PROD-019 登录 403 一律显示「该账号当前不可用，无法登录」

- 模块：登录 / 错误提示
- 文件:行号：[auth.ts:426](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/auth.ts#L426)、[auth.ts:519](file:///d:/LiuHao-AI-OS/apps/console/console/src/lib/auth.ts#L519)
- 表现：Origin 不在白名单导致的 403 被显示为「该账号当前不可用，无法登录」，用户会反复检查密码/账号状态，永远找不到真正原因。
- 是否复现：**已复现**：18080 浏览器 LAN 打开登录返回 403，前端显示该文案；服务端响应为 `origin_not_allowed`。
- 根因：前端只按 HTTP 403 做账号可用性映射，未读错误码。
- 影响面：排障误导，把网络/白名单问题归因到账号。
- 安全或数据风险：低。
- 修复方案：按 `error` 码分支：`origin_not_allowed` → 「当前访问来源未被允许，请用本机地址或配置允许来源」。
- 需要改的文件：`apps/console/console/src/lib/auth.ts`、`apps/console/console/src/lib/errorMessage.ts`
- 验证方法：构造 403 origin_not_allowed，前端显示来源错误而非账号不可用。
- 等级：P2
- 状态：OPEN

---

### PROD-020 总览「运行中任务」实际绑定活跃会话数

- 模块：总览
- 文件:行号：[Overview.tsx:245-248](file:///d:/LiuHao-AI-OS/apps/console/console/src/pages/Overview.tsx#L245-L248)
- 表现：标签「运行中任务」，值取 `summary.sessions.count`，脚注写「当前活跃会话」；2 条已完成目标仍显示 2。
- 是否复现：**已复现（代码 + 响应）**：`/v1/dashboard/summary.sessions.count=2`，目标全部完成。
- 根因：KPI 文案与数据源不一致，历史设计图遗留。
- 影响面：用户误判仍有任务在跑。
- 安全或数据风险：低。
- 修复方案：改名为「活跃会话」，或改绑真实运行中任务数。
- 需要改的文件：`apps/console/console/src/pages/Overview.tsx`
- 验证方法：完成所有目标后 KPI 不再宣称「运行中任务」。
- 等级：P3
- 状态：OPEN

---

### PROD-021 启动自检报告的端口与实际监听不一致

- 模块：诊断 / 启动自检
- 文件:行号：[main.py:1502-1507](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L1502-L1507)（只读 `PORT`，默认 8080）对比 [main.py:162-190](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L162-L190)（`resolve_listen_port()` 支持 `LIUHAO_LISTEN_PORT`/`LIUHAO_API_PORT`）
- 表现：18084 实际监听 `127.0.0.1:18084`，`/v1/startup.startup_check.host=127.0.0.1, port=8080, port_bindable=false, port_state=serving`。
- 是否复现：**已复现**。步骤：18084 启动后读 `/v1/startup`。
- 根因：自检与 Origin 放行使用两套端口解析；自检漏读 `LIUHAO_LISTEN_PORT`/`LIUHAO_API_PORT`。
- 影响面：诊断包给出错误端口，排障时误导。
- 安全或数据风险：低。
- 修复方案：自检改用 `resolve_listen_port()`。
- 需要改的文件：`src/gateway/main.py`
- 验证方法：任意端口启动，`startup_check.port` 等于实际监听端口。
- 等级：P3
- 状态：OPEN

---

### PROD-022 源码模式告警库/向量库落在仓库目录（安装版已修正）

- 模块：数据布局 / 测试隔离
- 文件:行号：[store.py](file:///d:/LiuHao-AI-OS/src/observability/alerts/store.py)（`DEFAULT_ALERTS_STORE_PATH="data/observability/alerts.json"` 相对 CWD）、[launch_installed.py:553](file:///d:/LiuHao-AI-OS/scripts/launch_installed.py#L553)（安装版重定向到数据目录）
- 表现：源码模式实例告警写到 `d:\LiuHao-AI-OS\data\observability\alerts.json`；未设 `LIUHAO_KNOWLEDGE_DB` 时向量库共享仓库 `data/knowledge_vectors.sqlite3`，导致「文档 1 / 分块 1 / 向量 26」残留数。
- 是否复现：**已复现**。步骤：源码模式不设对应 env 启动 → 告警与向量写仓库；设置 env 后正常。
- 根因：默认路径相对 CWD；安装版启动器会设 env，源码模式不会。
- 影响面：审计/测试互相污染；用户源码运行时数据与仓库混放。
- 安全或数据风险：低（无跨用户泄露），但会误导统计与备份范围。
- 修复方案：默认路径改为数据目录派生（与安装版一致），或源码启动器默认导出这些 env。
- 需要改的文件：`src/observability/alerts/store.py`、`src/knowledge/vector_store.py`、`scripts/start_liuhao.py`
- 验证方法：不设 env 启动，告警/向量都落在 `LIUHAO_DATA_DIR`。
- 等级：P3
- 状态：ACCEPTED RISK（安装版已修正；源码模式为测试隔离缺口）

---

### PROD-023 安装版 1.0.0 与仓库 0.1.1 无法互相验证

- 模块：版本 / 发布
- 文件:行号：[version.py](file:///d:/LiuHao-AI-OS/src/version.py)（`APP_VERSION=0.1.1`）；8080 安装版 `/v1/health` → `version=1.0.0`
- 表现：仓库审计结论不能直接外推到用户真正运行的安装版；安装版行为可能有差异（如数据目录重定向已修正）。
- 是否复现：**已复现**（只读 health 对比）。
- 根因：仓库未按安装包版本打 tag/同步版本常量。
- 影响面：所有「安装版是否可用」的结论证据不足。
- 安全或数据风险：低，但发布可追溯性缺失。
- 修复方案：版本号单一来源（构建时注入），发布包与仓库 tag 对应。
- 需要改的文件：`src/version.py`、`packaging/**`（只读约束下由发布负责人改）
- 验证方法：安装包 `/v1/health.version` 与仓库 `APP_VERSION` 一致。
- 等级：P3
- 状态：OPEN

---

### PROD-024 PWA「已安装」误报

- 模块：系统设置 / 桌面形态
- 文件:行号：`matchMedia('(display-mode: standalone)')` 判定
- 表现：系统设置显示「已在独立窗口运行」，疑似误报。
- 是否复现：**未复现**。步骤：实测 `matchMedia('(display-mode: standalone)').matches === true`，与界面一致，不成立。
- 根因：—
- 影响面：—
- 安全或数据风险：—
- 修复方案：无需修复。
- 需要改的文件：无
- 验证方法：在普通浏览器标签打开，`matches` 应为 false。
- 等级：P4
- 状态：NOT REPRODUCED

---

### PROD-025 提醒/图像理解/知识图谱/反思链/委员会/自进化/反馈学习/PII/归档/GitHub 宣称与实现不符

- 模块：能力注册表 / 全产品
- 文件:行号：[capability-map.md:32-111](file:///d:/LiuHao-AI-OS/docs/capability-map.md#L32-L111)（逐项 grep 调用链证据）、[perception.py:165-171](file:///d:/LiuHao-AI-OS/src/ai/perception.py#L165-L171)（`NOT_IMPLEMENTED`）
- 表现：注册表把它们列为能力项，实际无生产调用方或仅测试导入。
- 是否复现：**代码行号 + 调用链证明**（capability-map 内附 `git grep` 结果）；图像理解另经 `perception.py` 复核。
- 根因：能力注册以「模块存在」判定，不以「可达/有消费方」判定。
- 影响面：用户为不存在的能力付费/配置时间。
- 安全或数据风险：PII/归档未实现意味着合规承诺不成立（若对外宣称）。
- 修复方案：注册表加「可达性」列并在 UI 显示；对外文档删除或标注「规划中」。
- 需要改的文件：能力注册表模块、`docs/capability-map.md`、相应页面
- 验证方法：每项能力在 UI 可触发一次真实调用，否则标「不可达」。
- 等级：P2
- 状态：OPEN

---

## 2. 宣称 vs 实现对照表（53 条）

判定口径：**真能用** = 本审计实测成功；**部分可用** = 有实现但受限/需前置条件/文案不符；**只有壳** = 代码存在但无入口/无消费方；**根本没有** = 无实现或无入口。

| # | 宣称（摘要） | 出处 | 判定 | 证据 |
|---|---|---|---|---|
| 1 | 六家模型 Provider（OpenAI/DeepSeek/Kimi/Anthropic/Gemini/Ollama） | `docs/USER-GUIDE.md` | 部分可用 | 仅 Ollama 实测；云 Key 未测 |
| 2 | Key 保存后立即生效 | 设置页文案 | 真能用 | 18080 保存后下一条为真实 Ollama 回复 |
| 3 | Key 加密存储 | 设置页/文档 | 真能用 | `data/model_config.key` DPAPI，无明文 |
| 4 | 三端共用同一会话 | `App.tsx:4`、`Login.tsx:597` | 部分可用 | 界面同源；手机写请求 403 |
| 5 | 手机同一 WiFi 打开控制台 | `mobile-access.md:16` | 部分可用 | 读 200，写 403 |
| 6 | 手机扫码接管 | `start_liuhao.py:1216` 注释 | 根本没有 | 控制台 0 处二维码 |
| 7 | 首次设置创建管理员 | 首启页 | 真能用 | 18082 创建 boss 成功 |
| 8 | 首登「随后显示恢复材料」 | `Login.tsx:759` | 部分可用 | 需刷新才出现 |
| 9 | 首次设置不发恢复码 | `USER-GUIDE.md:29` | 部分可用 | 实际发了码，文案矛盾 |
| 10 | 恢复码重置密码 | 登录页 | 部分可用 | 桌面可用；网页 `PROOF_REQUIRED` |
| 11 | 恢复码页生成/轮换新码 | `Recovery.tsx` | 真能用 | 根账号可生成并显示 |
| 12 | 备份导出（整棵树/仅我） | `Backup.tsx` | 真能用 | 726KB 包生成 |
| 13 | 备份导入到本机 | `Backup.tsx` | 部分可用 | 仅全新实例 |
| 14 | 换机迁移（带口令包） | `END-USER-GUIDE.md:481` | 部分可用 | 新实例成功；同机被拒 |
| 15 | CLI 加密备份 | `END-USER-GUIDE.md:514` | 部分可用 | 文档差分完整；本次未执行 |
| 16 | 一键诊断包 | 设置页 | 真能用 | `diag.bin` 3989 bytes |
| 17 | 检查更新/应用升级 | 设置页 | 根本没有 | `source_configured=false`，409 |
| 18 | 版本号与安装包一致 | `version.py` | 部分可用 | 仓库 0.1.1 vs 安装版 1.0.0 |
| 19 | 知识库上传 PDF/Word/网页/文本 | `Knowledge.tsx:769` | 真能用 | docx/html/md/pdf 实测 |
| 20 | 知识库单文件 ≤20MB | `knowledge.ts:39` | 真能用 | 后端常量一致 |
| 21 | 大文件自动后台任务/可取消/全回滚 | `Knowledge.tsx:769` | 部分可用 | 接口存在，取消未实测 |
| 22 | 「PDF/Word/图片没有解析器」 | `Knowledge.tsx:1431` | 部分可用 | 与实测矛盾（过时文案） |
| 23 | 同步上传 ≤5MB | `knowledge.ts:41` | 真能用 | 前端/后端一致 |
| 24 | 检索命中并给 sources | 知识页 | 真能用 | 命中 `ZEBRA-7788` + sources |
| 25 | 文档删除（分块+向量+哈希） | 知识页 | 真能用 | `chunks_removed=1`，向量下降 |
| 26 | AI 员工创建/派活/写产物 | 员工页 | 真能用 | `alice-proof.txt` 写出 |
| 27 | 员工提交/完成统计 | `MyEmployees.tsx` | 部分可用 | 0/0 与目标 1/1 矛盾 |
| 28 | 目标分配/分解/执行 | 目标页 | 真能用 | 2 目标完成 |
| 29 | 提醒/定时任务 | 排程器/工具 | 部分可用 | 工具+CLI，无界面 |
| 30 | 日程/待办 | `capability-map.md:93` | 根本没有 | 插件未加载 |
| 31 | MCP 外部工具接入 | 能力注册表 | 只有壳 | `MCP_WIRING_STATUS=adapter-only` |
| 32 | A2A / gRPC 跨进程 | 能力注册表 | 根本没有 | 无实现 |
| 33 | 联网/网络抓取 | 网络页 | 部分可用 | 总线仅 LLM 调用 |
| 34 | 图像理解 | `perception.py:170` | 只有壳 | `NOT_IMPLEMENTED` |
| 35 | 桌面键鼠自动化 | 桌面能力 | 只有壳 | 仅 fake |
| 36 | 知识图谱 | `capability-map.md:48` | 只有壳 | 无持久化调用链 |
| 37 | 推理/反思链 | `capability-map.md:78` | 只有壳 | 仅测试导入 |
| 38 | 多智能体委员会/黑板 | `capability-map.md:79` | 只有壳 | 无编排消费方 |
| 39 | 自进化 | `capability-map.md:81` | 只有壳 | 仅测试 |
| 40 | 用户反馈学习 | `capability-map.md:82` | 根本没有 | 未挂载网关 |
| 41 | PII 检测/脱敏接入 | `capability-map.md:110` | 根本没有 | 无写入/检索接入 |
| 42 | 保留/归档策略 | `capability-map.md:111` | 根本没有 | `NotImplementedError` |
| 43 | GitHub / 第三方 SaaS 集成 | `capability-map.md:65` | 根本没有 | 无消费方 |
| 44 | 审批中心实际拦截 | 审批页 | 只有壳 | 拦截 0，记录型 |
| 45 | 审计链完整性校验 | `/v1/ready` | 真能用 | 372 事件、coverage 1.0 |
| 46 | 告警规则触发 | `/v1/alerts` | 真能用 | 77 条 / 74 firing |
| 47 | 总览「系统健康度 100%」 | `Overview.tsx:259` | 部分可用 | 与 preflight/告警矛盾 |
| 48 | 总览「运行中任务」 | `Overview.tsx:245` | 部分可用 | 绑定活跃会话数 |
| 49 | 会话轮次统计 | `/v1/dashboard/summary` | 部分可用 | turns 恒 0 |
| 50 | 能力注册表 provider 状态 | 能力页 | 部分可用 | Mock 生效 vs Ollama 真实 |
| 51 | 跨站写请求防护 | `main.py:847` | 真能用 | LAN Origin 403 |
| 52 | 登录错误提示准确性 | `auth.ts:426` | 部分可用 | 403 一律说账号不可用 |
| 53 | PWA「已安装」 | 系统设置 | 未复现 | 实测 standalone=true，不成立 |

**统计（53 条）**：真能用 **16** 条；部分可用 **20** 条；只有壳 **8** 条；根本没有 **8** 条；未复现 **1** 条。

---

## 3. 8 条端到端剧本

| # | 剧本 | 走到第几步 | 结果/响应 | 状态 |
|---|---|---|---|---|
| 1 | 新用户安装后首次登录 | 第 3 步（创建账号后取恢复材料） | 18082 创建 boss 成功并进入总览；恢复码横幅**刷新后**才出现（PROD-006）。`setup.exe` 安装向导未执行（HKCU 争用约束），安装态首启未验证 | PARTIAL |
| 2 | 改 Key 后立刻对话 | 全程 | 18080 保存 `audit-ollama`（`qwen2.5:3b`），下一条对话返回真实模型回复；`/v1/health.ai_provider=ollama/real/model_config` | PASS（本地 Ollama） |
| 3 | 建第二个账号并派活 | 全程 | 创建 alice → 激活码 → `alice-helper` hire → `assign` → 产物 `alice-proof.txt` 写出；账户树权限含 `model:manage`、`account:manage_children` | PASS |
| 4 | 上传知识库后提问命中 | 全程 | 上传 md/docx/html → 检索命中 → 对话答 `ZEBRA-7788` 并带 `sources`；删除后 `chunks_removed=1`、向量下降 | PASS |
| 5 | 手机扫码接管 | 第 0 步（无扫码入口） | 全 UI 无二维码；手动 IP 访问后 `POST /v1/auth/login` 带 LAN Origin → `403 origin_not_allowed`；`docs/mobile-access.md` 自述手机侧未验证 | BLOCKED |
| 6 | 导出备份再导入 | 第 2 步（导回本机） | 18080 导出 726KB 成功；同机导入被拒（已有账号）；18081 全新实例导入成功恢复 2 账号/4 员工/2 目标/32 chunks/4 会话，原密码可登录 | PARTIAL |
| 7 | 升级一次 | 第 1 步（检查更新） | `GET /v1/upgrade/check` 405；`POST` 409 `source_not_configured`；`/v1/upgrade/status.source_configured=false`；UI 按钮禁用 | BLOCKED |
| 8 | 卸载后重装 | 第 0 步（未执行安装器） | 未运行 `setup.exe`（只读/环境约束）；仅有安装版 8080 只读健康证据（`version=1.0.0`） | BLOCKED（静态） |

---

## 4. 功能断层（模块之间靠人工搬运）

1. **提醒**：对话里能创建，控制台**没有**列表/取消页，只能 `python -m src.reminders`（PROD-017）。
2. **知识库产物**：入库/检索结果只存在于知识页；目标/自动化/员工页无法直接引用某条知识命中，靠人工复制。
3. **员工产物**：产物写在工作区，但工作区根目录混入内部库（PROD-012），文件页难以区分用户产物与运行态。
4. **备份两条路线**：界面备份包与 CLI 加密备份不是同一格式（文档明确差分），且界面导入只认全新实例，普通用户无法自助完成「备份→回滚」闭环（PROD-011）。
5. **诊断结论 → 修复动作**：预检报告 `SECRET_KEY` 缺失，但设置页没有「生成并写入」入口，用户需手工改 `config/app.env`（PROD-003）。
6. **手机 → 桌面**：手机无扫码、无桌面证明通道，遇到密码问题必须回到桌面机（PROD-007/018）。

---

## 5. 空入口与死链

| 入口 | 表现 | 证据 |
|---|---|---|
| 设置页「检查更新」 | 按钮禁用；即使能点也是 409 | `/v1/upgrade/status`、`POST /v1/upgrade/check` |
| 网络页「刷新」 | 有入口，但总线只承载 LLM 调用，普通用户永远看不到网络流量 | 网络页注释与 `network_bus` 使用面 |
| 信任页「查询」 | 有入口，但无实体可查时长期空 | `Trust.tsx` 空态 |
| 审批中心 | 有页面，无待审批对象、拦截恒 0 | PROD-013 |
| 手机端登录 | 无扫码入口；手输 IP 后写请求 403 | PROD-002/018 |
| 浏览器「忘记密码」 | 提交后 `ACCOUNT_RECOVERY_PROOF_REQUIRED` | PROD-007 |
| 业务中心「网络网关已具备」 | 点进去无 MCP/A2A/gRPC 任何可用入口 | PROD-014 |

---

## 6. 一致性

| 概念/操作 | 页面 A | 页面 B | 冲突 |
|---|---|---|---|
| 当前模型 | 健康页 Ollama 真实 | 总览/能力注册表 Mock 模拟 | PROD-004/015 |
| 系统健康 | 总览 100% 无异常 | 预检 critical；告警 74 firing | PROD-003 |
| 审计事件数 | 总览 24 | 就绪检查 372；14 天分析 11 | PROD-005 |
| 会话消息数 | 聊天历史 2 条 | 总览 `turns=0` | PROD-005 |
| 运行中任务 | 总览「运行中任务 2」 | 目标全部完成 | PROD-020 |
| 员工产出 | 目标 1/1 完成 | 员工卡提交/完成 0/0 | PROD-016 |
| 知识库支持格式 | 769/808 行「PDF/Word/网页」 | 1431 行「没有解析器」 | PROD-010 |
| 恢复码 | 文档「首次不发」 | UI「随后显示恢复材料」 | PROD-006/009 |
| 版本 | 仓库 0.1.1 | 安装版 1.0.0 | PROD-008/023 |
| 端口 | 实际 18084 | 自检 8080 | PROD-021 |

---

## 7. 文案与承诺（提示成功但实际没生效）

| 文案 | 承诺 | 实际 |
|---|---|---|
| 「已保存」模型 Key | 立即生效 | 本地 Ollama 真实生效（成立）；总览仍显示 mock（显示层不成立） |
| 「系统就绪，全部检查通过」 | 全部健康 | `SECRET_KEY` 缺失 + 74 条告警 firing（不成立） |
| 「恢复码只显示这一次」 | 立即可见 | 需刷新（不成立） |
| 「三端共用同一会话」 | 手机可登录 | LAN 写请求 403（不成立） |
| 「支持 PDF/Word」同页又说「没有解析器」 | 能力明确 | 实测支持，但文案自否（部分不成立） |
| 「检查更新」 | 可升级 | 无更新源 409（不成立） |
| 「已同步/已生效」（网络/信任/审批） | 有实际动作 | 审批仅记录、网络仅 LLM 总线（不成立） |

---

## 8. P0 / P1 清单

### P0（1 条）
- **PROD-001** 当前工作树网关启动失败 —— 历史 `Application startup failed`；已被并发会话修复，`00:43` 用 18084 验证 `ready`。状态 CONFIRMED FIXED。

### P1（7 条）
1. **PROD-002** 手机/局域网写请求 403，手机端开箱不可用。
2. **PROD-003** 总览健康度 100% 掩盖 `SECRET_KEY` critical 与 74 条 firing 告警。
3. **PROD-004** 真实 Ollama 被总览/注册表报成 mock。
4. **PROD-005** 会话 turns 恒 0、审计事件数三处不一致。
5. **PROD-006** 首登恢复码不即时显示。
6. **PROD-007** 浏览器/手机无法自助重置密码（需桌面一次性证明）。
7. **PROD-008** 开箱内置升级不可用 + 版本号不一致。
- **PROD-009** 排程器历史不启动（提醒/定时全失效）—— 已修复验证。

---

## 9. 最该先修的 5 条

1. **PROD-002 手机/局域网 Origin 放行**：这是「三端 / 手机端」全部承诺的前置；不做，手机登录、扫码、接管全部卡在 403。修复只需在绑非回环地址时把本机 LAN IP + 实际端口加入写白名单，并保留非本机来源拒绝。
2. **PROD-006 + PROD-007 恢复闭环**：首登恢复码即时显示（一处 React 状态同步即可），并在网页/手机明确「恢复需桌面端」或提供受控替代路径；否则用户一旦忘记密码就锁死。
3. **PROD-003 + PROD-004 + PROD-005 总览说真话**：健康度聚合预检与告警、provider 统一快照、会话 turns 用正确 key；这是用户判断「系统能不能用」的唯一入口，当前三处数字互相打架。
4. **PROD-008 升级通道**：安装器写入更新源或明确「离线升级」入口；版本号单一来源。否则安全补丁与版本升级无法下发。
5. **PROD-010 + PROD-011 + PROD-018 文案/流程对齐**：知识库格式与大小、备份导入限制、手机扫码承诺，全部改成「与实现一致」；这是最低成本、最高信任收益的一组修复。

---

## 10. 密钥与敏感材料（只报位置与类型）

| 位置 | 类型 | 说明 |
|---|---|---|
| `D:\cache\temp\prod-audit-20261108\secrets.txt` | JWT secret + 身份完整性密钥（明文） | **审计实例自生成**的隔离凭据，非产品仓库泄漏 |
| `d:\LiuHao-AI-OS\data\model_config.key` | DPAPI 包裹的主密钥（二进制） | 用于解密已保存模型 Key；不得随意外发 |
| 模型配置库 `model_registry.sqlite3` | 加密后的 API Key 密文 | 未发现明文 API Key |
| `config\app.env` | `SECRET_KEY`（当前为空/未设置） | 生产预检判为 critical |

未在仓库中发现明文 API Key；未读取或输出任何真实用户密钥内容。

---

## 11. 证据边界与未覆盖

1. 当前工作树在 `00:39:01` 被并发会话修改；`PROD-001` 的 P0 是 `00:36` 旧快照的真实失败，修复后于 `00:43` 由 18084 验证，**不代表本次审计改代码**。
2. 运行态功能证据（多账号、知识库、备份、告警、dashboard）来自 `00:10–00:31` 启动的 18080/18081/18082；这些实例的排程器日志是修复前状态。
3. 未执行 `setup.exe` 安装/卸载重装（环境与 HKCU 争用约束），剧本 8 仅静态判断。
4. 未使用真实手机；手机结论来自 Origin 请求与文档/代码检索。
5. 云端 Provider（OpenAI/DeepSeek/Kimi/Anthropic/Gemini）未用真实 Key 验证，仅 Ollama 实测。
6. 安装版 8080 仅做只读 health；其 `1.0.0` 与仓库 `0.1.1` 不同，安装版行为结论需单独回归。
7. 并发会话若继续修改 `main.py`/`scheduler_host.py`，以本报告快照时间为准。
