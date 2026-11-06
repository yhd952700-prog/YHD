# 管家真实模型验收（可复现）

一条命令跑完管家链路的三类关键场景，输出结构化结果并给出 PASS / FAIL / SKIP 判定。用于每轮迭代后的回归验收。

脚本：[scripts/acceptance/butler_real_model_acceptance.py](../scripts/acceptance/butler_real_model_acceptance.py)

## 前置条件

- 已安装依赖（`pip install -r requirements.txt`，Windows 也可直接用仓库 `.venv`）。
- 有一个**可用**的真实模型凭据（默认 DeepSeek）。Key 只从环境变量读取，脚本不会把它写进代码或仓库。

## 一条命令

```bash
# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-xxxx"
python scripts/acceptance/butler_real_model_acceptance.py
```

```bash
# 或用跨 provider 的统一变量
LIUHAO_ACCEPTANCE_API_KEY=sk-xxxx python scripts/acceptance/butler_real_model_acceptance.py
```

默认 `provider=deepseek`、`model=deepseek-chat`、三个场景全跑、联网检索开启、单次模型调用超时 150s。

## 指定模型

```bash
# 换 provider / 模型 / 自建网关
python scripts/acceptance/butler_real_model_acceptance.py \
  --provider openai --model gpt-4o-mini --base-url https://api.openai.com/v1

# 只跑部分场景
python scripts/acceptance/butler_real_model_acceptance.py --scenarios 1,3
```

Key 环境变量按 provider 读取（前一个优先）：

| provider | 环境变量 |
|---|---|
| deepseek | `LIUHAO_ACCEPTANCE_API_KEY` / `DEEPSEEK_API_KEY` |
| openai | `LIUHAO_ACCEPTANCE_API_KEY` / `OPENAI_API_KEY` |
| moonshot | `LIUHAO_ACCEPTANCE_API_KEY` / `MOONSHOT_API_KEY` |
| anthropic | `LIUHAO_ACCEPTANCE_API_KEY` / `ANTHROPIC_API_KEY` |
| google | `LIUHAO_ACCEPTANCE_API_KEY` / `GOOGLE_API_KEY` |
| ollama | 无需 Key（本地模型） |

## 关闭检索

```bash
python scripts/acceptance/butler_real_model_acceptance.py --no-web
```

此时场景 1 不联网，验收判定改为「确认未出现 live_web / 未伪造实时结果」，结论应回到「未接入实时联网检索」的诚实标注。

## 读结果

终端逐行输出三类记录：

- `MODEL {...}`：实际解析到的 provider 类型、模型名、模型配置 id、是否开启检索。
- `RESULT {...}`：每个场景一条结构化结果。
- `VERDICT {...}`：总判定（`PASS` / `FAIL`）。

`--json-out results.json` 可把完整结构写盘。单场景字段：

| 字段 | 含义 |
|---|---|
| `scenario` / `title` | 场景标识与中文标题 |
| `status` | `PASS` / `FAIL` |
| `elapsed_s` | 端到端耗时（秒） |
| `goal_state` | 目标最终状态（`completed` / `failed`） |
| `task_count` | 拆解出的任务数 |
| `max_concurrent_running` | 并发峰值（观察 `task_started` / 完成事件） |
| `source_count` / `sources` | 实时检索来源条数与 URL 列表 |
| `live_web` / `fetched_at` | 是否命中实时检索、抓取时间 |
| `artifacts` | 真实落盘产物（相对工作区） |
| `tasks` | 任务名 / 能力 / 状态 |
| `summary_excerpt` | 结论摘要（前 400 字） |
| `detail` | 判定理由 |
| `retry_elapsed_s` / `retry_state` | 场景 3 的重试耗时与重试后状态 |

退出码：`0` = PASS；`1` = FAIL；`2` = SKIP（缺 Key、缺模型名、模型不可达），SKIP 带可读的 `reason`，**不代表通过**。

## 三类场景与判定

1. **拆解 + 实时检索**：目标完成、任务数 ≥ 2；开启检索时要求命中 `live_web`、来源 ≥ 1、有抓取时间、结论含「抓取时间」且保留真实来源 URL。
2. **并行执行**：目标完成、任务数 ≥ 2、并发峰值 ≥ 2。
3. **失败 → 重试**：注入一次 `file_write` 临时故障，断言目标 `failed`、错误语义可读、artifacts 为空；解除注入后走真实单任务重试，断言 `completed`、artifacts 非空且磁盘上真实存在。

## 环境隔离

脚本把所有持久化状态指向系统临时目录（`liuhao-acceptance-*`），跑完自动删除；`--keep-workdir` 可保留用于排查。Key 只在该临时模型配置库里以加密形式短暂存在。

不会写入仓库 `data/`，也不依赖任何预置临时目录，换机器 / 换工作区可直接复跑。
