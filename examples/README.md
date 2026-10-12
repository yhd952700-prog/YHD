# LiuHao AI OS 示例集

本目录包含 LiuHao AI OS 的完整示例代码，涵盖从入门到高级的各种使用场景。

## 目录结构

```
examples/
├── quickstart/          # 快速入门
│   ├── hello_agent.py       # 第一个 Agent 程序
│   ├── using_memory.py      # 记忆系统使用
│   └── multi_agent_team.py  # 多智能体团队
├── use_cases/           # 实际用例
│   ├── research_assistant.py    # 研究助手
│   ├── code_review_agent.py     # 代码审查
│   ├── data_analysis_pipeline.py # 数据分析管道
│   └── customer_support_bot.py  # 客户支持机器人
├── advanced/            # 高级主题
│   ├── chain_of_thought.py     # 链式思维推理
│   ├── react_pattern.py        # ReAct 模式
│   ├── reflection_loop.py      # 反思循环
│   ├── pipeline_orchestration.py # 管道编排
│   ├── plugin_usage.py         # 插件系统
│   ├── committee_decision.py   # 委员会决策
│   └── blackboard_collaboration.py # 黑板协作
├── integrations/        # 系统集成
│   ├── slack_bot.py          # Slack 机器人
│   └── github_helper.py      # GitHub 助手
├── sdk/                 # SDK 使用
│   ├── python_basic_usage.py   # Python SDK 基础
│   ├── python_advanced_usage.py # Python SDK 高级
│   ├── javascript_usage.js     # JavaScript SDK
│   └── go_usage.go             # Go SDK
├── README.md            # 本文件
├── requirements.txt     # Python 依赖
└── TUTORIAL.md          # 分步教程
```

## 如何运行示例

### 前置条件

- Python 3.10+
- LiuHao AI OS 项目源码
- （可选）AI 模型 API Key

### 安装依赖

```bash
# 进入项目根目录
cd LiuHao-AI-OS

# 安装项目依赖
pip install -r requirements.txt

# 安装示例额外依赖
pip install -r examples/requirements.txt
```

### 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的配置
# LLM 模型配置、数据库配置等
```

### 运行示例

```bash
# 快速入门示例
python examples/quickstart/hello_agent.py
python examples/quickstart/using_memory.py
python examples/quickstart/multi_agent_team.py

# 用例示例
python examples/use_cases/research_assistant.py
python examples/use_cases/code_review_agent.py

# 高级示例
python examples/advanced/chain_of_thought.py
python examples/advanced/react_pattern.py

# 集成示例
python examples/integrations/slack_bot.py

# SDK 示例
python examples/sdk/python_basic_usage.py
node examples/sdk/javascript_usage.js
go run examples/sdk/go_usage.go
```

> **注意**：部分示例在没有配置真实 AI 模型时会使用模拟模式运行，
> 便于理解代码结构和 API 使用方式。

## 按类别索引

### 快速入门 (quickstart)

| 示例 | 文件 | 内容 | 难度 |
|------|------|------|------|
| Hello Agent | `quickstart/hello_agent.py` | 创建 Agent、执行任务、获取结果 | ★☆☆ |
| 记忆系统 | `quickstart/using_memory.py` | 工作记忆、语义网络、记忆搜索 | ★★☆ |
| 多智能体团队 | `quickstart/multi_agent_team.py` | 消息总线、团队协作、工作流 | ★★☆ |

### 实际用例 (use_cases)

| 示例 | 文件 | 内容 | 难度 |
|------|------|------|------|
| 研究助手 | `use_cases/research_assistant.py` | 信息收集、来源评估、引用管理、报告生成 | ★★☆ |
| 代码审查 | `use_cases/code_review_agent.py` | 代码质量分析、Bug检测、安全扫描、最佳实践 | ★★★ |
| 数据分析 | `use_cases/data_analysis_pipeline.py` | 数据加载、EDA、洞察提取、可视化建议 | ★★★ |
| 客户支持 | `use_cases/customer_support_bot.py` | 工单分类、知识库检索、质量检查、升级流程 | ★★☆ |

### 高级主题 (advanced)

| 示例 | 文件 | 内容 | 难度 |
|------|------|------|------|
| 链式思维 | `advanced/chain_of_thought.py` | 逐步推理、自我纠正、置信度追踪、多策略 | ★★★ |
| ReAct 模式 | `advanced/react_pattern.py` | Thought-Action-Observation、工具使用、多步求解 | ★★★ |
| 反思循环 | `advanced/reflection_loop.py` | 迭代精炼、自我批评、改进追踪、收敛检测 | ★★★ |
| 管道编排 | `advanced/pipeline_orchestration.py` | 多阶段管道、分支合并、错误重试、DAG执行 | ★★★ |
| 插件系统 | `advanced/plugin_usage.py` | 插件加载、生命周期、多插件协同、注册表 | ★★☆ |
| 委员会决策 | `advanced/committee_decision.py` | 投票方式、冲突解决、结果汇总、共识评估 | ★★☆ |
| 黑板协作 | `advanced/blackboard_collaboration.py` | 共享黑板、机会式求解、投票订阅、版本控制 | ★★★ |

### 系统集成 (integrations)

| 示例 | 文件 | 内容 | 难度 |
|------|------|------|------|
| Slack 机器人 | `integrations/slack_bot.py` | 消息收发、斜杠命令、多轮对话、富文本 | ★★☆ |
| GitHub 助手 | `integrations/github_helper.py` | Issue管理、PR审查、Actions、Webhook | ★★★ |

### SDK 使用 (sdk)

| 示例 | 文件 | 内容 | 语言 |
|------|------|------|------|
| Python 基础 | `sdk/python_basic_usage.py` | 客户端、Agent、记忆、知识库 | Python |
| Python 高级 | `sdk/python_advanced_usage.py` | 异步、批量、重试、Webhook | Python |
| JavaScript | `sdk/javascript_usage.js` | Promise API、async/await、错误处理 | JavaScript |
| Go | `sdk/go_usage.go` | 选项模式、Context、类型安全 | Go |

## 推荐学习路径

### 初学者路径

1. `quickstart/hello_agent.py` - 了解基础概念
2. `quickstart/using_memory.py` - 理解记忆系统
3. `quickstart/multi_agent_team.py` - 多智能体协作
4. `use_cases/customer_support_bot.py` - 完整应用场景

### 进阶路径

1. `advanced/chain_of_thought.py` - 推理机制
2. `advanced/react_pattern.py` - 工具使用
3. `advanced/reflection_loop.py` - 自我改进
4. `advanced/pipeline_orchestration.py` - 流程编排

### 高级路径

1. `advanced/committee_decision.py` - 群体决策
2. `advanced/blackboard_collaboration.py` - 协作模式
3. `advanced/plugin_usage.py` - 扩展开发
4. `use_cases/research_assistant.py` - 综合应用

## 常见问题

**Q: 没有 API Key 可以运行示例吗？**

A: 可以。大部分示例在没有配置真实 AI 模型时会自动进入模拟模式，
使用模拟数据演示代码结构和 API 用法。

**Q: 示例可以直接用于生产环境吗？**

A: 示例代码旨在演示用法和最佳实践，生产使用前请根据实际需求进行调整，
特别是错误处理、日志记录和安全相关的部分。

**Q: 如何贡献新的示例？**

A: 欢迎贡献！请确保示例：
- 有清晰的注释说明
- 可以独立运行
- 包含错误处理
- 遵循项目代码风格
- 在本 README 中添加索引

## 相关资源

- [项目文档](../docs/) - 完整的项目文档
- [架构设计](../docs/ARCHITECTURE.md) - 系统架构说明
- [记忆系统设计](../docs/MEMORY-SYSTEM-DESIGN.md) - 记忆系统详细设计
- [快速开始](../docs/quickstart.md) - 项目快速开始指南
- [API 文档](../openapi.yaml) - OpenAPI 规范
