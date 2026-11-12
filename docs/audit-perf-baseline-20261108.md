# 性能基线实测（已安装版 0.1.0）— 2026-11-08

> 只读实测。未修改 `src/`、`apps/`、`installer/`、`packaging/`；实测服务使用端口 `19004` 与独立数据目录 `D:\_audit\perf-baseline`，未使用 8080，未触碰 `D:\LiuHaoData`。

## 0. 一句话结论

- 用户感受最差的是**冷启动：中位 20.9 秒**（最差 23.3 秒）。
- **最该先修的是第 2 段的首屏**：登录后首屏数据要等 **5.30 秒**，其中 `/v1/dashboard/roster` 单请求就占 **5.28 秒**；而这个接口的真实计算只要 **1.1 毫秒**——5.28 秒几乎全是一次性 Python 懒导入（`employee_store` → `mem0` → `openai` → `qdrant_client`）。
- 第 3 段在**没有配置真实 Key** 的情况下测的是 mock provider：首字中位 **1.14 秒**；服务端这一轮总耗时约 1.17 秒，其中 `tools.make_tools` 首次调用占 0.41 秒。真实模型的 TTFT 未测，见第 6 节。

---

## 1. 三段耗时总表（先看数字）

每段 5 次有效测量（另有失败重试，不计入）。

| # | 用户感受的一段 | 中位数 | 最差值 | 最好值 | 主要测量手段 |
|---|---|---:|---:|---:|---|
| 1 | 双击图标 → 登录框可用（冷启动） | **20 903.6 ms** | 23 331.7 ms | 18 419.2 ms | 进程启动时间戳 + 端口/HTTP 轮询 + `/v1/startup` + Playwright 页面 Performance API |
| 2 | 点登录 → 进入主界面（鉴权 + 首屏数据） | **5 300.9 ms** | 6 015.6 ms | 3 502.2 ms | 页面内注入的 fetch 时间线 + DOM 标记（`.os-kpi` 无骨架）+ 后端日志 |
| 3 | 发出第一条消息 → 第一个字出现 | **1 142.2 ms** | 2 782.0 ms | 1 088.7 ms | 页面内 fetch 时间线 + DOM（`.chat-bubble` 首次非空）+ 后端日志 `elapsed_ms` |
| 3b | （备用口径）发出消息 → 界面进入等待态 | **6.9 ms** | 12.2 ms | 6.1 ms | DOM 出现 `.chat-row.assistant[data-streaming="true"]` |

> 第 3 段本次**不是真实模型**：`provider=mock`、`model=mock-model`、模型管理里没有启用模型（后端日志：`model routing fallback: task=chat reason=model management has no enabled models -> default model`）。因此 1.14 秒是"本地链路 + 模拟回复"的首字时间，不是真实大模型首字时间；按任务要求，同时给出 3b「已发出、已进入等待态」= 6.9 ms。

---

## 2. 测量口径与手段

### 2.1 被测对象（重要）

- 被测的是**当前已安装版本**：`D:\LiuHao`，`pyproject.toml` `version = "0.1.0"`。
- 安装体时间戳（只读核对）：
  - `D:\LiuHao\src\gateway\main.py`：2026-11-07 10:16:06
  - `D:\LiuHao\console\index.html`：2026-11-07 11:11:30
- 同一时刻工作树（`d:\LiuHao-AI-OS`）已经更新：
  - `HEAD = 8e40d985`（`feat(models): 回环地址一律放行，填 Key 可直接拉取并选用模型`）
  - 工作树 `src/gateway/main.py`：2026-11-07 14:12:02
  - 工作树 `apps/console/console/dist/index.html`：2026-11-07 14:37:58
- 结论：**本报告所有数字属于安装版 0.1.0，不代表工作树最新代码。** 仓库里多条线并行改动，工作树当前版本的性能可能已经不同。

### 2.2 环境

| 项 | 值 |
|---|---|
| CPU | Intel Xeon E5-2673 v3，12 核 / 24 线程 |
| 内存 | 31.9 GB |
| 系统 | Windows 10 专业版 19045 |
| 运行时 | `.venv` 内 CPython 3.12 |
| 浏览器 | Microsoft Edge，Playwright 1.64.1，每轮全新 profile，headless |
| 服务端口 | 127.0.0.1:**19004** |
| 数据目录 | `D:\_audit\perf-baseline`（通过 `LIUHAO_DATA_DIR` 隔离） |

### 2.3 测量条件（会影响绝对值，请按"繁忙机器上界"理解）

- 测量窗口内机器同时在跑其他并行线的 pytest / soak / gateway 进程，`python` 进程数 41，CPU 总占用采样 59%–76%（测量期间曾达 49%–100%）。
- 同一时刻 `127.0.0.1:8080` 与 `127.0.0.1:19020` 都有别的进程在监听（非 `D:\LiuHao` 目录），属于噪声来源。这也正是本次不使用 8080 的原因之一。
- 冷启动前先清理：只杀本次测量自己拉起的进程树与 19004 端口占用者，并核对没有 `D:\LiuHao` 目录启动的残留网关。检测脚本自身命令行含 `D:\LiuHao\*`，被自检逻辑误计为 1 个"外部进程"；实际外部安装版进程为 **0**。

### 2.4 各段起止点定义

| 段 | 起点 | 终点 |
|---|---|---|
| 1 冷启动 | Python 侧记录拉起进程的 `perf_counter()` 时刻 | 页面同时满足：`#os-principal` 与 `#os-secret` 可见、提交按钮可用、`/v1/auth/config` 已 200、且不在首启 bootstrap 表单（`#boot-principal`） |
| 2 登录 | 页面内点击登录按钮的 `performance.now()` | 首屏 4 个 `.os-kpi` 全部渲染出数值、**不再有 `.os-skeleton`** |
| 3 首条消息 | 页面内点击发送的 `performance.now()` | 助手气泡 `.chat-bubble` 首次出现非空文本 |
| 3b 等待态 | 同上 | `.chat-row.assistant[data-streaming="true"]` 首次出现 |

手段组合：
- **进程启动时间**：拉起网关时记 `perf_counter()`，轮询 TCP/HTTP 首次可达、`/v1/startup` 就绪、launcher 写出 `current_url.txt`。
- **浏览器 Performance API / 页面内时间线**：在页面初始化时包装 `window.fetch`，记录每个请求的 start / response headers / body 完成；配合 `MutationObserver` 打 DOM 里程碑，全部相对同一 `performance.timeOrigin`。
- **后端日志时间戳**：`src/ai/observability.observe` 的 `elapsed_ms` 与 `chat_stream status=completed` 时间戳，用于把浏览器侧数字拆到函数级。
- **单函数基准**：在 `D:\LiuHao` 的独立进程内直接调用被测函数（只读，不写仓库），并用 `python -X importtime` / `cProfile` 归因导入与调用栈。

---

## 3. 第 1 段：冷启动（20.9 s）

### 3.1 分段

| 里程碑 | 中位数 | 最差 | 最好 |
|---|---:|---:|---:|
| 进程启动 → 首次 HTTP 可达 | 16 839.2 ms | 19 145.5 ms | 15 814.3 ms |
| → `/v1/startup` 就绪 | 18 337.1 ms | 20 796.1 ms | 16 984.4 ms |
| → launcher 写出 `current_url.txt` | 18 337.8 ms | 20 796.9 ms | 16 984.9 ms |
| Edge 冷启动（进程到可导航） | 1 075.7 ms | 1 490.5 ms | 699.1 ms |
| 导航 → 登录框可用 | 820.6 ms | 1 004.1 ms | 423.6 ms |
| **端到端合计** | **20 903.6 ms** | **23 331.7 ms** | **18 419.2 ms** |

结论：**82% 的时间（约 17 秒）花在后端起得来之前**，浏览器只占约 1.9 秒（Edge 启动 1.08 s + 页面到登录框可用 0.82 s）。

### 3.2 后端启动阶段（`/v1/startup` 的 `stages`，5 轮 delta 中位数）

| 阶段 | 中位 delta | 最大 delta |
|---|---:|---:|
| `main 顶层依赖导入完成（fastapi / security / prometheus metrics）` | **8 495.7 ms** | 11 110.7 ms |
| 路由：`ai_management / dashboard / roster / profile / kernels` 完成 | 1 313.4 ms | 1 321.5 ms |
| 中间件注册完成，开始注册路由 | 780.2 ms | 974.9 ms |
| 路由：`knowledge` 完成 | 563.9 ms | 611.6 ms |
| 入口模块导入完成（uvicorn 已导入） | 508.6 ms | 524.9 ms |
| 路由：`models`（模型配置中心）完成 | 410.0 ms | 446.1 ms |
| 运行配置加载完成（`config/default.yaml` + `APP_ENV`） | 304.1 ms | 381.4 ms |
| 路由：`accounts`（账号树）完成 | 261.3 ms | 332.3 ms |
| 路由：`chat` 完成 | 155.5 ms | 191.5 ms |
| 执行 → 告警绑定完成 | 120.4 ms | 328.0 ms |
| 路由：`health / observability` 完成 | 117.1 ms | 122.7 ms |
| 路由：`diagnostics` 完成 | 113.0 ms | 171.2 ms |
| **`/v1/startup` 总 elapsed** | **15 912.4 ms** | **19 327.5 ms** |

> `elapsed_ms` 以 `startup_profile` 的时钟为起点（略晚于进程 0 点），且 launcher 轮询粒度约 0.5 s，所以它比"进程启动 → 就绪"的 18.3 s 略小。

### 3.3 这 8.5 s 里到底是什么

在同一台机器、独立进程里重测该阶段涉及的导入（3 次）：`import src.gateway.main` = **3 237 / 3 790 / 4 448 ms**。用 `-X importtime` 拆：

| 导入 | 累计耗时 |
|---|---:|
| `fastapi` | 1 491 ms |
| `src.security` | 1 071–1 834 ms |
| └ `src.integrations.vault` | 1 321 ms |
| └ └ `hvac` | 248 ms |
| `prometheus_client` | 167–177 ms |

也就是说：该阶段的**固有下限约 3.2–4.4 秒**，实测 8.5 秒中另有约 4–5 秒来自高负载下的 CPU/磁盘争用（多线并行 pytest 正在跑）。这不是代码里某一行慢，而是"进程级 Python 依赖导入 × 机器争用"。

---

## 4. 第 2 段：登录 → 主界面（5.30 s）

### 4.1 分段

| 里程碑 | 中位数 | 最差 | 最好 |
|---|---:|---:|---:|
| 点击 → `POST /v1/auth/login` 响应头 | 1 293.2 ms | 1 581.6 ms | 971.6 ms |
| 点击 → 主界面壳体（`.os-app` + 导航） | 1 352.8 ms | 1 645.2 ms | 1 038.6 ms |
| 点击 → 首屏数据齐（4 个 KPI 非骨架） | **5 300.9 ms** | 6 015.6 ms | 3 502.2 ms |
| 点击 → 首屏请求包全部落地 | 5 281.3 ms | 5 994.2 ms | 3 479.0 ms |

逐轮原始值（点击登录 → 各里程碑）：

| 轮次 | 登录响应头 | 壳体 | 首屏数据齐 | 首屏包落地 |
|---|---:|---:|---:|---:|
| 1 | 1 401.9 | 1 468.0 | 5 184.5 | 5 164.7 |
| 2 | 971.6 | 1 038.6 | 3 502.2 | 3 479.0 |
| 3 | 1 293.2 | 1 352.8 | 5 992.7 | 5 972.1 |
| 4 | 1 581.6 | 1 645.2 | 6 015.6 | 5 994.2 |
| 5 | 1 204.5 | 1 280.8 | 5 300.9 | 5 281.3 |

### 4.2 登录本身（1.29 s）的构成

- `PBKDF2-HMAC-SHA256`，`600 000` 轮（`src/gateway/auth.py` 常量 `PBKDF2_ITERATIONS`），独立基准中位 **853.1 ms**（840.5 / 763.2 / 940.3 / 853.1 / 949.7）。
- 剩余约 0.4 秒是 JWT 签发、鉴权/配额策略、JSON 与 HTTP 往返（含机器争用）。
- 这是**固定成本**：每个用户每次登录都要付，且随迭代次数线性增长。

### 4.3 首屏各端点的完成时刻（相对点击登录，5 轮中位数）

| 端点 | 中位 | 最大 |
|---|---:|---:|
| `/v1/dashboard/roster` | **5 281.3 ms** | 5 994.2 ms |
| `/v1/health` | 2 667.6 ms | 3 234.3 ms |
| `/v1/auth/config` | 2 653.0 ms | 3 247.7 ms |
| `/v1/auth/me` | 2 585.3 ms | 3 181.3 ms |
| `/v1/policy/approvals` | 2 549.3 ms | 3 114.7 ms |
| `/v1/dashboard/summary` | 2 415.3 ms | 2 565.7 ms |
| `/v1/dashboard/analytics` | 2 366.5 ms | 2 565.6 ms |
| `/v1/accounts/tree` | 2 357.5 ms | 2 831.3 ms |
| `/v1/dashboard/activity` | 2 288.6 ms | 2 565.0 ms |
| `/v1/accounts` | 2 221.3 ms | 2 417.3 ms |
| `/v1/ready` | 1 918.4 ms | 2 080.6 ms |

**5 轮里，最慢的首屏端点 5/5 都是 `/v1/dashboard/roster`**，且它比第二名多约 2.6 秒。

---

## 5. 第 3 段：首条消息 → 首字（1.14 s，mock）

### 5.1 分段

| 里程碑 | 中位数 | 最差 | 最好 |
|---|---:|---:|---:|
| 点击发送 → 请求真正发出 | 2.1 ms | 2.6 ms | 1.3 ms |
| 点击 → `POST /v1/chat/stream` 响应头 | 1 139.0 ms | 2 776.6 ms | 1 082.7 ms |
| 点击 → 等待态 UI（`data-streaming="true"`） | **6.9 ms** | 12.2 ms | 6.1 ms |
| 点击 → 首字（`.chat-bubble` 非空） | **1 142.2 ms** | 2 782.0 ms | 1 088.7 ms |
| 点击 → 流结束 | 1 254.4 ms | 2 918.5 ms | 1 180.7 ms |

### 5.2 服务端 1.17 秒花在哪（`run-2.log` 时间戳）

| 环节 | 耗时 | 位置 |
|---|---:|---|
| 模型路由 fallback 决定 | 14:18:54.051 | `src/ai/model_router.py` |
| `personal.get_personal_context` | 7.2 ms | `src/ai/personal_context.py` |
| **`tools.make_tools`（首次）** | **414.6 ms** | `src/ai/tools/kernel_tools.py:39 make_tools` |
| `tool_registry.register` ×10 | ≈ 50 ms | `src/ai/tool_registry.py` |
| `policy.evaluate` ×8 | 4.0–13.3 ms / 次，合计 ≈ 78 ms | `src/kernels/crosscutting` |
| `resource.create_quota` ×8 | 13.6–22.4 ms / 次，合计 ≈ 160 ms | `src/kernels/crosscutting` |
| 会话 append ×2 + 记忆持久化 | ≈ 13 ms | `src/gateway/conversation_store` |
| 未打点的空档（上下文组装 / 框架开销） | ≈ 250 ms | — |
| **`chat_stream status=completed`** | **14:18:55.217（总计 1.166 s）** | `src/ai/liuhao.py` |

`make_tools` 首次 414.6 ms 的原因（独立进程实测：首调 **344.2 ms**，第 2 次起 **0.4 ms**；`cProfile` 归因）：

| 懒导入 | 耗时 |
|---|---:|
| `src/automation/__init__.py` | 60 ms |
| `src/tasks/reminder_tool.py` | 27 ms |
| `src/tasks/reminders.py` | 25 ms |
| `src/automation/audit.py` | 20 ms |
| `src/tasks/reminder_time.py` | 19 ms |
| `dataclasses` 类构造（22 个） | 43 ms |

对应代码位置：`src/ai/tools/kernel_tools.py` 第 369 行 `from ...automation.tools import build_automation_tools`、第 383 行 `from ...automation.observe import build_observation_tools`、第 388 行 `from ...tasks.reminder_tool import build_reminder_tool` —— 三处都在**每次对话轮次调用的函数体内**做 import。

---

## 6. 哪一段最该优化，瓶颈在哪个函数/请求

### 6.1 优先级建议

| 优先级 | 目标 | 理由 | 预期收益 |
|---|---|---|---|
| **P0** | 第 2 段 `/v1/dashboard/roster` | 首屏 5.30 s 里 5.28 s 是它；而它**真实计算只有 1.1 ms**，其余全是请求路径上的首次懒导入 | 首屏可望从 5.3 s 降到 1.5 s 以内（≈ −3.8 s），且同时释放被它拖住的其它 10 个首屏请求 |
| **P1** | 第 1 段 `main 顶层依赖导入` | 绝对数最大（20.9 s 的 82% 在服务起来之前）；8.5 s 中约 3.2–4.4 s 是固有导入，其余是争用 | 需要把 security/vault、prometheus、路由模块改为延迟导入；本机负载下不确定能省多少，但可减少启动抖动 |
| **P2** | 第 3 段 `make_tools` | 1.14 s 中 0.41 s 是首次懒导入；每用户首轮都付 | 首字可望 −0.3～0.4 s |
| **P3** | 登录 PBKDF2 600k | 0.85 s 固定成本；属安全参数，不建议为性能降低 | 除非改 KDF 参数或换算法，否则不建议动 |

**如果只能选一段：选第 2 段。** 它是"纯结构性浪费"——计算 1.1 ms、交付 5.3 s，改动点集中、风险低、收益直接落在用户点完登录后的那 5 秒。

### 6.2 P0 证据链（已定位到函数）

1. 浏览器侧：`/v1/dashboard/roster` 中位 5 281.3 ms，5/5 轮最慢（见 4.3）。
2. 服务端入口：
   - `src/gateway/roster.py:49` `dashboard_roster()` → `build_roster_payload()`（同步 `def`，跑在线程池）
   - `src/ai/roster_payload.py:374` `build_roster_payload()` → `load_registry()` + `provider_status()` + `_build_real_employees()`
3. 独立进程基准（同一台机器）：

| 调用 | 首次 | 之后 |
|---|---:|---:|
| `build_roster_payload()` | 5 674.5 / 6 058.8 / 6 954.1 ms | 0.9–1.2 ms |
| `load_registry()` | 148–151 ms（其中 `import yaml` 71–77 ms） | 0.2 ms（有 mtime 缓存） |
| `provider_status()` | 103.5 / 109.7 ms | 0.5–0.8 ms |
| **`_build_real_employees()`** | **7 526.8 / 8 280.2 ms** | **0.2 ms** |
| `EmployeeStore().list_employees()` | 0.2 ms（0 个员工） | — |

4. 继续下钻：`import src.ai.employee_store` = **7 832.5 ms**（全新进程）。`-X importtime` 累积：

| 导入链 | 累计 |
|---|---:|
| `src.ai.employee_store` | 6 388.6 ms |
| └ `src.ai.employee` | 6 287.1 ms |
| └ └ `src.knowledge.memory` | 5 786.7 ms |
| └ └ └ `mem0` | 2 964.7 ms |
| └ └ └ └ `openai` | 2 749.1 ms |
| └ └ └ └ └ `mem0.configs.vector_stores.qdrant` | 2 087.5 ms |
| └ └ └ └ └ └ `qdrant_client` | 1 998.0 ms |
| └ └ └ └ └ `openai.types` | 1 270.1 ms |
| └ └ └ └ └ └ `openai.types.responses` | 827.9 ms |

**根因一句话**：`_build_real_employees()`（`src/ai/roster_payload.py:306`）在函数体里 `from .employee_store import EmployeeStore`，把 `mem0 → openai → qdrant_client` 这条 6 秒级的依赖树拖进了**第一个首屏请求**。

**连带效应（推断，证据见下）**：该导入发生在线程池线程里，与其它 10 个首屏请求共享同一进程与 GIL，所以其余端点也被拖到 2.2–2.7 s（扣除登录的 1.3 s，实际约 0.9–1.4 s）——而这些端点本身大多是读内存/读小文件。修掉 roster 的懒导入后，首屏整包预计一起下降。

**建议改法（本次未实施，仅为报告结论）**：
- 在 lifespan 启动预热阶段显式 `import src.ai.employee_store`（放到后台预热线程，不阻塞 `/v1/startup` 就绪判定）；或
- 让 `_build_real_employees()` 走一个**不依赖 mem0/openai/qdrant** 的轻量读取路径（员工元数据是本地 JSON 目录，见 `list_employees()` 实测 0.2 ms）。

### 6.3 P1 证据链（第 1 段）

- 阶段：`main 顶层依赖导入完成（fastapi / security / prometheus metrics）`，中位 **8 495.7 ms**，最大 11 110.7 ms（见 3.2）。
- 独立进程下限：`import src.gateway.main` = 3 237 / 3 790 / 4 448 ms。
- 主要成员：`fastapi` 1 491 ms、`src.security` 1 071–1 834 ms（含 `src.integrations.vault` 1 321 ms、`hvac` 248 ms）、`prometheus_client` 167–177 ms。
- 位置：`src/gateway/main.py` 第 35–63 行的顶层 `from fastapi ...`、`from ..security import ...`、`from ..observability.metrics import ...`。
- 另有 `uvicorn` 导入 508.6 ms（`src/gateway/__main__.py`），路由注册合计约 3.3 s（kernels/dashboard/roster/profile 1 313 ms、knowledge 564 ms、models 410 ms、accounts 261 ms、chat 156 ms）。

### 6.4 P2 证据链（第 3 段）

见 5.2：`src/ai/tools/kernel_tools.py:39 make_tools` 首次 344–415 ms，第 2 次起 0.4 ms；懒导入点在第 369 / 383 / 388 行。另有 8 组 `policy.evaluate` + `resource.create_quota` 合计约 240 ms，随工具数/轮次增长。

---

## 7. 哪些数字因为"没配真实 Key"而不完整

必须明确：**第 3 段不是一个完整的真实模型首字测量。**

| 项 | 状态 | 说明 |
|---|---|---|
| 模型 provider | `mock` | `/v1/health` 返回 `provider=null, model=null, ok=null`；后端日志明确 fallback 到默认 mock |
| 第 3 段"首字" 1 142.2 ms | **不完整** | 只覆盖"点击 → 本地后端组流 → mock 吐第一个 token → 浏览器渲染"，**不含**上游模型的排队、网络 RTT、prompt 预处理与真实 TTFT |
| 第 3b 段"等待态" 6.9 ms | 完整 | 这是任务允许的替代口径：请求已发出、界面已进入等待态。它只反映前端响应性，不反映模型延迟 |
| 真实首字预估 | **不可由本次数据推出** | 真实值 ≈ 1.14 s（本地链路；其中 0.41 s 是 `make_tools` 懒导入）+ 真实模型 TTFT（未知，取决于 provider/区域/负载） |
| 真实 Key 下的失败路径 | **未测** | `429 rate_limited`、`quota_exhausted`、`key_revoked`、`unreachable` 的首包延迟未采样 |
| 真实 Key 下的多轮 | **未测** | 真实模型可能触发多轮工具调用，`policy.evaluate`/`create_quota`/`make_tools` 会按轮次重复计费；本次只测了单轮、0 次真实工具调用 |
| 首字之后的流式速度 | **未测** | 只测到"第一个字"，token/s、长回复总时长不在本次范围 |

另外，第 2 段的 `/v1/dashboard/roster` 与 provider 配置无关（`provider_status()` 在 mock 下同样执行），所以那一段的 5.3 s **不受 Key 影响**，是可信的。

---

## 8. 可信度与噪声

- **样本量**：每段 5 次有效；冷启动与首屏的波动较大（首屏 3.5–6.0 s），主要来自并行负载而非被测代码路径变化。
- **系统性偏置**：所有绝对值偏"繁忙机器上界"。空闲机器上冷启动与首屏都会明显更低，但**相对结论（roster 是首屏唯一大头、懒导入是根因）不受影响**——因为独立进程基准复现了同样的 5.7–7.0 s 首调 vs 1.1 ms 热调。
- **已修/未修的口径问题**：
  - 冷启动前"外部进程"检测有 1 个自匹配误报（检测脚本自身命令行含 `D:\LiuHao\*`），实际外部安装版进程 0。
  - `all_v1_settled` 会被 `/v1/auth/me` 的 200 ms 轮询无限拉长，已改用固定首屏端点列表 + 每路径首次请求。
  - py-spy 采样在 dump 时事件循环空闲，未能给出请求栈；函数级归因改用 `observe` 日志 + 独立进程基准 + `importtime`/`cProfile`。

---

## 9. 复现方式（脚本均在审计目录，未进仓库）

| 脚本 | 作用 |
|---|---|
| `D:\_audit\perf-baseline\_harness\measure.py` | 5 轮冷启动 + 登录 + 首条消息全流程（Playwright/Edge，端口 19004） |
| `D:\_audit\perf-baseline\_harness\analyze.py` | 汇总中位/最差、启动阶段、首屏端点 |
| `D:\_audit\perf-baseline\_harness\bench_first_call.py` | 验证 roster 首调 vs 热调 |
| `D:\_audit\perf-baseline\_harness\bench_first_sub.py` | 分测 `load_registry` / `provider_status` / `_build_real_employees` |
| `D:\_audit\perf-baseline\_harness\bench_employee.py` | 拆 `EmployeeStore` 的 import / 构造 / 列目录 |
| `D:\_audit\perf-baseline\_harness\bench_components.py` | 全组件基准 |
| `D:\_audit\perf-baseline\_harness\bench_hash.py` | PBKDF2 600k 基准 |
| `D:\_audit\perf-baseline\_harness\perf-results.json` | 5 轮原始数据 |
| `D:\_audit\perf-baseline\_harness\run-1..5.log` | 每轮服务端日志 |

合规声明：
- 未修改 `src/`、`apps/`、`installer/`、`packaging/`（全部改动仅落在 `D:\_audit\perf-baseline\_harness` 与本报告）。
- 实测使用 `127.0.0.1:19004` 与 `D:\_audit\perf-baseline`；未使用 8080；未读写 `D:\LiuHaoData`。
- `D:\LiuHao` 仅作为被测安装体被读取与启动。
