# 网关冷启动：测量、优化与前端契约

本文记录"双击快捷方式 → `/v1/health` 可用"这条路径的真实耗时分解、优化依据，
以及启动期间前端可用的只读状态接口。所有数字都可用下方命令重跑，不靠估算。

## 结论（本机复测）

| 指标 | 优化前 | 优化后 |
| --- | --- | --- |
| `/v1/health` 返回 200（空数据目录，`python -m src.gateway`） | **8.7 s** | **3.7 s** |

> 安装版实测为 14.77 s（空目录 15.42 s）。本机与安装版环境的绝对耗时不同
> （磁盘 / 杀软 / 首次运行），但**同一台机器上前后对比**是可靠的。按本机
> 前后比例（3.7 / 8.7 ≈ 0.42），安装版预计落到 **≈ 6.2 s**；必须以打包产物
> 实测为准（见"如何复现"）。

## 如何复现（含安装版）

```powershell
# 1) 进程内阶段表（推荐：无 -X importtime 的额外开销）
.\.venv\Scripts\python.exe -m src.gateway --profile-startup

# 2) 落 JSON（便于对比两次构建）
$env:LIUHAO_STARTUP_PROFILE = "1"
$env:LIUHAO_STARTUP_PROFILE_OUT = "D:\cache\startup.json"
.\.venv\Scripts\python.exe -m src.gateway

# 3) 安装版 / 桌面入口：桌面外壳（src/desktop/_runner.py）不是用 __main__，
#    因此用环境变量开启，而不是命令行参数：
#    LIUHAO_STARTUP_PROFILE=1 启动网关子进程即可（环境变量会被继承）。
```

阶段表同时写入 `logs/` 下日志（logger `liuhao.startup`，INFO）。

## 阶段分解（本机，空数据目录，单位 ms）

| 阶段 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 进入 `src.gateway` 包 | 0.1 | 0.1 |
| `src.gateway.main` 顶层导入（fastapi / security / prometheus） | 2182.8 | 1805.5 |
| 运行配置加载（`config/default.yaml` + `APP_ENV`） | 92.2 | 87.1 |
| 中间件注册 | 12.7 | 7.8 |
| 路由：health / observability | 116.4 | 103.0 |
| 路由：knowledge | 130.7 | 148.7 |
| **路由：chat（导入 `src.ai.liuhao`）** | **4389.1** | **21.4** |
| 路由：ai_management / dashboard / roster / profile / kernels | 117.7 | 238.0 |
| 路由：models | 105.0 | 102.3 |
| 全部路由 + 静态驾驶舱挂载完成 | 12.4 | 15.3 |
| 入口模块导入（uvicorn） | 216.6 | 104.1 |
| 入口配置解析（`server.timeouts`） | 101.9 | 52.9 |
| lifespan 启动 | 86.8 | 84.1 |
| 密钥 / JWT / RBAC 初始化 | 92.3 | 142.0 |
| 执行器围栏 / 执法快照 / 身份存储 / 内核 lifecycle | ~21 | ~22 |
| 执行→告警绑定 / 审计 checkpointer / 告警规则 / 内置插件 / 指标循环 | ~78 | ~78 |
| **进程内总计时** | **8366** | **3026** |
| 外部实测 `/v1/health` 200 | **8740** | **3690** |

差额（约 0.66 s）是 uvicorn 绑定端口并进入 accept 循环的时间。

## 做了什么，依据是什么

### 1. `chat` 路由不再在启动期导入 AI 重依赖（最大单项，−4.4 s）

`src/gateway/chat.py` 顶层曾 `from ..ai.liuhao import LiuHaoAssistant`，而
`src.ai.liuhao` → `src.ai.employee` → `src/knowledge/memory.py` → **mem0**，
mem0 又连带导入 `qdrant_client` / `openai` / `opentelemetry` / `grpc` / `numpy`。
这些只在"真的要对话"时才需要，与登录 / 健康检查 / 模型配置无关。

现在 `chat.py` 保留同名模块级函数（`LiuHaoAssistant` / `provider_for_task` /
`get_conversation_store`）作为**惰性接缝**：只有真正调用时才导入实现。保留
同名可替换对象是为了不破坏把 `chat_module.LiuHaoAssistant` 当 monkeypatch
接缝的既有测试。

### 2. 对话链与追踪改到后台预热（不阻塞就绪，失败必须可见）

lifespan 在标记核心就绪后启动一个 daemon 线程：

* `初始化追踪（OpenTelemetry SDK）`（诚实记录耗时，约 0.3 s）
* `预热对话链（LLM/provider/会话存储）`（本机 4.4 s）

失败会：写 `ERROR` 日志（含 traceback）→ 记入 `/v1/startup` 的
`background.errors` → 该状态 `state="degraded"`。**没有删除任何初始化**：
如果预热失败，第一次真实对话会同步重试同样的导入，功能不会失真，只是更慢。

### 3. 追踪（OpenTelemetry）移出模块顶层导入（−0.3 s）

`src.gateway.main` 顶层不再 `from ..observability.tracing import get_tracer`。
追踪属于可观测性，不是"登录 / 对话 / 模型配置"的就绪条件，初始化移入上述
后台线程；失败同样上报。

### 4. 为什么剩下的 1.8 s 顶层导入没有再砍

`fastapi`（约 0.92 s）与 `src.security`（约 0.81 s，JWT / RBAC / 密钥管理）
是**登录与鉴权的硬依赖**。把它们挪到后台会直接违反"`/v1/health` 200 时用户
能登录"的语义。这是刻意的成本，不是漏掉的优化点。

### 5. 明确未搬走、且实测不值得搬的初始化

告警绑定、审计 checkpointer、生产告警规则、内置插件、指标评估循环合计约
78 ms（本机）。挪到后台只会引入并发注册风险与额外状态机，收益不足以抵消
风险，因此保留在启动路径并如实计时。

## 就绪语义（未改变）

`/v1/health` 返回 200 时，以下均真实可用：

* 登录（JWT / API Key / RBAC / 身份库已初始化）
* 发消息（`/v1/chat` 路由已注册；重依赖惰性导入，必要时同步补课）
* 配模型（`/v1/models` 路由与模型配置存储已初始化）

后台预热只影响"第一次发消息快不快"，不影响"能不能"。

## 给前端的契约：`GET /v1/startup`

* 免鉴权、只读、无副作用，可任意轮询；响应头 `Cache-Control: no-store`。
* 始终 HTTP 200（状态在 body 里，而不是用 5xx 让前端猜）。
* 结构：

```json
{
  "state": "starting | ready | degraded",
  "core_ready": true,
  "elapsed_ms": 3690.1,
  "started_at_unix": 1730000000.0,
  "stages": [
    {"name": "进入 src.gateway 包", "elapsed_ms": 0.1, "delta_ms": 0.1},
    {"name": "路由：chat 完成", "elapsed_ms": 2183.0, "delta_ms": 21.4}
  ],
  "background": {
    "status": "pending | running | done | failed",
    "started_at_ms": 3025.6,
    "finished_at_ms": 7436.1,
    "steps": [
      {"name": "预热对话链（LLM/provider/会话存储）", "ok": true, "elapsed_ms": 4411.0, "error": ""}
    ],
    "errors": []
  }
}
```

前端建议行为：

* `state="starting"`：显示"正在启动"，继续轮询。
* `state="ready"`：正常进入控制台；`background.status="running"` 时可选提示
  "正在预热对话能力"（不影响登录 / 配模型）。
* `state="degraded"`：核心可用，但后台有组件失败；用
  `background.errors[*].error` 展示真实原因，不要笼统成"启动失败"。

### 交接给桌面外壳 / 前端的部分（本流未改这些文件）

网关进程在导入 FastAPI 与路由之后才会绑定端口（uvicorn 的生命周期顺序），
这段时间内浏览器会得到**连接失败**而不是 503。因此：

* 桌面外壳（`src/desktop/app.py` 的 `webbrowser.open`）在 `service.start()`
  成功后立刻打开浏览器，用户在端口绑定前会看到错误页。
* 建议（契约即可，无需改网关）：启动页在连接失败时视为
  `state="starting"`，用 250~500 ms 退避轮询 `GET /v1/startup`，拿到
  `state="ready"` 后再渲染控制台；或由外壳先展示"正在启动"占位页。
* 若希望"端口一开就能返回 starting"，需要外壳/打包侧在拉起网关前先提供一个
  极小的启动页（或让网关以 bootstrap 服务器先占端口）。本流未做，因为这会
  引入端口移交竞态，且涉及 `src/desktop/*` 与打包脚本（不在本流范围）。

## 回归测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_startup_status_api.py tests\test_chat_gateway.py tests\gateway\test_chat_knowledge.py -q
```

覆盖：阶段打点真实存在、端点免鉴权、后台预热完成被如实上报、
**后台失败会变成 `degraded` 且错误可见**（不会被吞掉）。
