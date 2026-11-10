# LiuHao AI OS 分步教程（设计示意版）

> ⚠️ **本文是设计示意，不是可运行教程。**
>
> 全篇**不提供** `from src.agents...`、`src.memory...`、`src.collaboration...`、
> `src.reasoning...`、`src.pipeline...`、`src.tools.registry` 这类示例代码。
> 这些包路径在当前仓库**不存在**（对应能力在下面 §0.2 的真实位置），照抄 import 必然失败。
> 历史上本文曾按上述路径给出“看起来能跑”的代码，已整体移除，避免继续误导。
>
> 想真的把系统跑起来，只看 §0.1 的三条真实入口；各章只讲设计意图与当前对应实现，
> **不承诺示例可直接执行**。

## 0. 先看这里

### 0.1 真实可运行入口

| 目的 | 入口 |
| --- | --- |
| 源码一键启动（网关 8080 + 控制台 5173） | `start-liuhao.bat`，或 `.venv\Scripts\python.exe scripts\start_liuhao.py` |
| 只起后端 | `.venv\Scripts\python.exe scripts\start_liuhao.py --backend-only` |
| 端到端冒烟 | `.venv\Scripts\python.exe scripts\smoke_mvp.py` |
| 真实对话（HTTP） | `curl --noproxy '*' -X POST http://127.0.0.1:8080/v1/chat -H "Content-Type: application/json" -d '{"message":"你好","session_id":"demo"}'` |
| 健康探针 | `curl --noproxy '*' http://127.0.0.1:8080/v1/health` |

完整安装、登录与排障见 [`docs/END-USER-GUIDE.md`](docs/END-USER-GUIDE.md)；
运行手册见 [`docs/RUN.md`](docs/RUN.md)。

> **源码直调（已实测）**：`LiuHaoAssistant.chat()` 需要显式审计库路径，否则会拒绝启动：
>
> ```powershell
> $env:LIUHAO_DATA_DIR="$env:TEMP\liuhao-demo"
> $env:AUDIT_DB_PATH="$env:LIUHAO_DATA_DIR\audit_store.db"
> .venv\Scripts\python.exe -c "from src.ai.liuhao import LiuHaoAssistant; a=LiuHaoAssistant(name='demo', profile_principal='demo', audit_principal='demo'); print(a.chat('你好')['status'])"
> ```
>
> 2026-11-08 实测输出 `completed`；未配置模型时正文是
> `[演示数据] 还没有配置模型，因此无法生成真实回答。…`。

### 0.2 旧示例路径 → 当前真实位置

| 旧教程里的路径（不存在） | 当前真实位置 |
| --- | --- |
| `src.agents.agent_factory` | [`src/ai/agent_factory.py`](src/ai/agent_factory.py)（助手主控见 [`src/ai/liuhao.py`](src/ai/liuhao.py)；HTTP 入口 [`src/gateway/chat.py`](src/gateway/chat.py)） |
| `src.memory.working_memory` | [`src/ai/memory/working_memory.py`](src/ai/memory/working_memory.py) |
| `src.memory.semantic_network` | [`src/ai/memory/semantic_network.py`](src/ai/memory/semantic_network.py) |
| `src.collaboration.message_bus` | [`src/workflow/event_bus.py`](src/workflow/event_bus.py) |
| `src.collaboration.multi_agent_team` | 无同名类；任务分派看 [`src/ai/agent_runtime.py`](src/ai/agent_runtime.py) 与 [`src/ai/goal_task_graph.py`](src/ai/goal_task_graph.py) |
| `src.reasoning.*` | [`src/ai/reasoning/`](src/ai/reasoning/)（`chain_of_thought.py`、`react.py`、`reflection.py`） |
| `src.pipeline.*` | [`src/pipelines/`](src/pipelines/)（`base.py`、`builder.py`、`etl.py`、`sources.py`、`transforms.py`、`sinks.py`） |
| `src.tools.registry` | [`src/ai/tool_registry.py`](src/ai/tool_registry.py) 与 [`src/ai/tools/base.py`](src/ai/tools/base.py) |

真实模块的**函数签名以源码为准**，本文不再抄写可能过期的调用示例。

---

## 1. 准备工作

### 1.1 环境要求

- Windows 10 / 11 x64；源码运行需要 Python 3.11+（仓库自带 `.venv`）。
- 控制台前端需要 Node.js；仓库内已带 `node_modules` 时可直接起。
- 模型可选：本机 Ollama，或在控制台 **系统设置 → 模型管理** 填云 API Key。

### 1.2 安装依赖

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 1.3 配置

不要手抄密钥到代码里。真实配置入口是控制台 **系统设置 → 模型管理**，
或启动进程的环境变量（如 `LIUHAO_CONTEXT_WINDOW`）。安装版的数据目录、
日志与 `app.env` 位置见 [`docs/INSTALL-WINDOWS.md`](docs/INSTALL-WINDOWS.md)。

---

## 2. 第一个助手（设计说明）

**设计意图**：一条消息进来，依次经过「授权 → 生成 → 记忆 → 审计」四步，
任何一步失败都如实登记，不伪造回答。

**当前实现**：`src/ai/liuhao.py` 的 `LiuHaoAssistant` 是主控；
HTTP 面是 `src/gateway/chat.py`；未配置模型时回落演示 Provider，
回复以 `[演示数据] 还没有配置模型，因此无法生成真实回答。` 开头。

**怎么体验**：按 §0.1 启动后，在控制台自由对话发一条消息，或用 `/v1/chat`。
本文不给 `AgentFactory().create_agent(...)` 之类的示例——该 API 形态与当前代码不一致。

---

## 3. 记忆系统（设计说明）

**设计意图**：分三层——工作记忆（当前轮次）、语义网络（概念与关系）、
情节/元记忆（历史与自省）。

**当前实现**：
- 工作记忆：[`src/ai/memory/working_memory.py`](src/ai/memory/working_memory.py)
- 语义网络：[`src/ai/memory/semantic_network.py`](src/ai/memory/semantic_network.py)
- 情节记忆：[`src/ai/memory/episodic.py`](src/ai/memory/episodic.py)
- 元记忆：[`src/ai/memory/meta_memory.py`](src/ai/memory/meta_memory.py)

**怎么体验**：控制台左侧「记忆」页查看真实落盘内容；记忆的读写由对话链路自动完成，
不需要在代码里手工 `add_memory()`。具体方法名以源码为准。

---

## 4. 多智能体团队（设计说明）

**设计意图**：多个 AI 员工通过事件总线协作，任务可拆解、分派、暂停与重规划。

**当前实现**：
- 事件总线：[`src/workflow/event_bus.py`](src/workflow/event_bus.py)
- 工作流状态机：[`src/workflow/state_machine.py`](src/workflow/state_machine.py)
- 任务图与执行：[`src/ai/goal_task_graph.py`](src/ai/goal_task_graph.py)、[`src/ai/agent_runtime.py`](src/ai/agent_runtime.py)
- 目标/员工管理接口：[`src/gateway/ai_management.py`](src/gateway/ai_management.py)

**怎么体验**：控制台「鎏灏管家」「我的员工」「目标与任务」三页就是它的产品面。
本文不再给 `MultiAgentTeam(...)` 示例——当前没有同名团队类。

---

## 5. 推理模式（设计说明）

**设计意图**：链式思维、ReAct（Thought-Action-Observation）、反思循环。

**当前实现**：
- [`src/ai/reasoning/chain_of_thought.py`](src/ai/reasoning/chain_of_thought.py)
- [`src/ai/reasoning/react.py`](src/ai/reasoning/react.py)
- [`src/ai/reasoning/reflection.py`](src/ai/reasoning/reflection.py)

这些模块是能力层实现，**不是**教程可直接调用的稳定 API；请以源码与测试为准。

---

## 6. 数据管道（设计说明）

**设计意图**：把抓取 → 解析 → 转换 → 校验 → 聚合 → 落库拆成可组合阶段。

**当前实现**：[`src/pipelines/`](src/pipelines/)：
`base.py`（阶段协议）、`builder.py`（组装）、`sources.py` / `parser.py` /
`transforms.py` / `validation.py` / `aggregation.py` / `sinks.py`。

旧的 `src.pipeline.pipeline.Pipeline`、`PipelineStage`、`BranchStage`、`RetryPolicy`
在当前仓库没有同名实现；本文不再给出这些类的示例代码。

---

## 7. 开发自定义能力（设计说明）

**设计意图**：能力以工具/内核的形式注册，经授权后才可执行。

**当前实现**：
- 工具基类与执行器：[`src/ai/tools/base.py`](src/ai/tools/base.py)、[`src/ai/tools/executor.py`](src/ai/tools/executor.py)
- 内核工具：[`src/ai/tools/kernel_tools.py`](src/ai/tools/kernel_tools.py)
- 注册与激活：[`src/ai/tool_registry.py`](src/ai/tool_registry.py)
- 能力内核：[`src/kernels/capability/`](src/kernels/capability/)

扩展前先读 [`docs/capability-map.md`](docs/capability-map.md) 的“真接线 / 假实现”判定，
不要只看类是否存在。

---

## 8. 做一个端到端小项目（设计说明）

**设计意图**：从目标 → 任务图 → AI 员工执行 → 产物落盘 → 结果可查。

**当前实现**：目标/任务图（`src/ai/goal_task_graph.py`）、执行围栏
（`src/kernels/execution/`）、产物索引（`src/artifacts/index.py`）、
控制台「目标与任务」「项目」「工作区文件」三页。

**验收方式**：用控制台完成一次真实目标，检查产物文件与事件流；
不以“代码里存在某个类”作为完成证据。

---

## 下一步

- 安装与使用：[`docs/END-USER-GUIDE.md`](docs/END-USER-GUIDE.md)
- 运行与门禁：[`docs/RUN.md`](docs/RUN.md)
- 能力真实状态：[`docs/capability-map.md`](docs/capability-map.md)
- 架构与内核：[`docs/spec/KERNEL-CANON.md`](docs/spec/KERNEL-CANON.md)
