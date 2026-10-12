# LiuHao AI OS 分步教程

从零开始，逐步掌握 LiuHao AI OS 的核心概念和使用方法。

---

## 目录

1. [准备工作](#1-准备工作)
2. [第一个 Agent](#2-第一个-agent)
3. [理解记忆系统](#3-理解记忆系统)
4. [构建多智能体团队](#4-构建多智能体团队)
5. [深入推理模式](#5-深入推理模式)
6. [搭建数据管道](#6-搭建数据管道)
7. [开发自定义 Agent](#7-开发自定义-agent)
8. [构建你的第一个管道](#8-构建你的第一个管道)

---

## 1. 准备工作

### 1.1 环境要求

- Python 3.10 或更高版本
- pip 包管理器
- （推荐）虚拟环境

### 1.2 安装项目

```bash
# 克隆项目
git clone <repository-url>
cd LiuHao-AI-OS

# 创建虚拟环境（推荐）
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 1.3 配置环境变量

```bash
# 复制配置模板
cp .env.example .env
```

编辑 `.env` 文件，配置必要的参数：

```
# LLM 配置
LLM_API_KEY=your-api-key-here
LLM_MODEL=gpt-4
LLM_BASE_URL=https://api.example.com/v1

# 数据库配置（可选）
DATABASE_URL=sqlite:///liuhao.db
```

> 如果没有 API Key，大部分示例会自动进入模拟模式，
> 你仍然可以学习代码结构和 API 用法。

---

## 2. 第一个 Agent

### 2.1 什么是 Agent

在 LiuHao AI OS 中，Agent 是执行任务的基本单元。每个 Agent 都有：
- **角色 (Role)**：定义 Agent 的身份和专业领域
- **系统提示 (System Prompt)**：指导 Agent 行为的指令
- **记忆 (Memory)**：Agent 的知识和经验存储
- **工具 (Tools)**：Agent 可以使用的外部能力

### 2.2 创建一个简单的 Agent

```python
# my_first_agent.py
from src.agents.agent_factory import AgentFactory

# 1. 获取 Agent 工厂实例
factory = AgentFactory()

# 2. 创建一个通用助手 Agent
agent = factory.create_agent(
    name="我的助手",
    role="general",
    system_prompt="你是一个友好、专业的 AI 助手。",
)

# 3. 执行任务
result = agent.execute("用一句话解释什么是人工智能")

# 4. 查看结果
print(f"回答: {result.output}")
print(f"状态: {result.status}")
print(f"Token 用量: {result.tokens_used}")
```

运行：
```bash
python my_first_agent.py
```

### 2.3 理解执行结果

Agent 执行任务后返回的结果包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `output` | str | Agent 的输出内容 |
| `status` | str | 执行状态 (completed/failed/running) |
| `latency_ms` | int | 执行耗时（毫秒） |
| `tokens_used` | int | 使用的 Token 数量 |
| `reasoning_trace` | list | 推理过程追踪 |

### 2.4 练习

尝试修改上面的代码：
1. 创建一个翻译专家 Agent
2. 让它翻译一段英文
3. 查看返回的结果信息

---

## 3. 理解记忆系统

### 3.1 记忆系统的层次

LiuHao AI OS 采用多层记忆架构：

```
┌─────────────────────────────────┐
│  工作记忆 (Working Memory)       │  短期，当前上下文
├─────────────────────────────────┤
│  情景记忆 (Episodic Memory)      │  事件经历
├─────────────────────────────────┤
│  语义记忆 (Semantic Memory)      │  知识事实，语义网络
├─────────────────────────────────┤
│  元记忆 (Meta Memory)            │  关于记忆的记忆
└─────────────────────────────────┘
```

### 3.2 使用工作记忆

```python
from src.memory.working_memory import WorkingMemory

# 创建工作记忆
wm = WorkingMemory(capacity=10)

# 添加信息
wm.add("用户喜欢蓝色", source="user_input", importance=0.7)
wm.add("项目截止日期是周五", source="conversation", importance=0.9)

# 获取信息
items = wm.get_recent(5)
for item in items:
    print(f"[{item.importance:.1f}] {item.content}")

# 按重要性排序
top_items = wm.get_top_by_importance(3)

# 搜索
results = wm.search("截止日期")
```

### 3.3 使用语义记忆网络

```python
from src.memory.semantic_network import SemanticMemoryNetwork

# 创建语义网络
sem_net = SemanticMemoryNetwork()

# 添加概念和关系
sem_net.add_node("Python", node_type="language")
sem_net.add_node("编程", node_type="concept")
sem_net.add_edge("Python", "编程", relation="是一种")

sem_net.add_node("AI", node_type="field")
sem_net.add_edge("Python", "AI", relation="常用于")

# 搜索相关概念
related = sem_net.get_related("Python", max_depth=2)
for node, depth, relation in related:
    print(f"{'  ' * depth}- {node} ({relation})")

# 路径查找
path = sem_net.find_path("Python", "AI")
print(" → ".join(path))
```

### 3.4 练习

1. 创建一个关于你的兴趣爱好的语义网络
2. 添加至少 5 个概念和它们之间的关系
3. 搜索某个概念的所有关联

---

## 4. 构建多智能体团队

### 4.1 多智能体协作模式

LiuHao AI OS 支持多种多智能体协作模式：
- **消息总线**：Agent 之间通过消息通信
- **管道**：任务按顺序流经多个 Agent
- **委员会**：多个 Agent 投票决策
- **黑板**：共享知识空间，机会式求解

### 4.2 消息总线基础

```python
from src.collaboration.message_bus import MessageBus, Message

# 创建消息总线
bus = MessageBus()

# 定义消息处理器
def on_research_result(message):
    print(f"收到研究结果: {message.content}")

# 订阅主题
bus.subscribe("research.result", on_research_result)

# 发布消息
bus.publish(Message(
    topic="research.result",
    content={"summary": "研究完成", "sources": 5},
    sender="researcher_agent",
))
```

### 4.3 创建多 Agent 团队

```python
from src.collaboration.multi_agent_team import MultiAgentTeam
from src.agents.agent_factory import AgentFactory

factory = AgentFactory()

# 创建团队
team = MultiAgentTeam(name="内容创作团队")

# 添加成员
team.add_agent(
    factory.create_agent(name="研究员", role="researcher"),
    role="信息收集"
)
team.add_agent(
    factory.create_agent(name="写手", role="writer"),
    role="内容撰写"
)
team.add_agent(
    factory.create_agent(name="编辑", role="editor"),
    role="质量审核"
)

# 设置工作流
team.set_workflow([
    ("研究员", "收集信息"),
    ("写手", "撰写初稿"),
    ("编辑", "审核修改"),
])

# 执行任务
result = team.execute("写一篇关于 AI 发展的短文")
print(f"最终输出:\n{result.final_output}")
```

### 4.4 练习

1. 创建一个软件开发团队（产品经理、设计师、开发、测试）
2. 设计一个简单的工作流
3. 模拟执行一个需求

---

## 5. 深入推理模式

### 5.1 链式思维 (Chain of Thought)

链式思维让 Agent 逐步推理，每一步都有明确的思考过程。

```python
from src.reasoning.chain_of_thought import ChainOfThoughtReasoner

reasoner = ChainOfThoughtReasoner()

# 设置推理策略
reasoner.set_strategy("step_by_step")  # 逐步推理
# reasoner.set_strategy("self_correcting")  # 自我纠正

# 执行推理
result = reasoner.reason(
    problem="一个水池有两个进水管和一个出水管。"
            "甲管6小时注满，乙管4小时注满，出水管8小时放完。"
            "三管同时开，几小时注满？",
    max_steps=10,
)

# 查看推理步骤
for i, step in enumerate(result.steps):
    print(f"步骤 {i+1}: {step.content}")
    print(f"  置信度: {step.confidence:.2f}")
    if step.correction:
        print(f"  修正: {step.correction}")

print(f"\n最终答案: {result.final_answer}")
print(f"总置信度: {result.overall_confidence:.2f}")
```

### 5.2 ReAct 模式

ReAct 模式结合推理和行动，让 Agent 在思考中使用工具。

```python
from src.reasoning.react import ReActReasoner
from src.tools.registry import ToolRegistry

# 注册工具
registry = ToolRegistry()
registry.register("search", lambda query: f"搜索 '{query}' 的结果...")
registry.register("calculate", lambda expr: f"计算 {expr} = 42")

# 创建 ReAct 推理器
reasoner = ReActReasoner(tool_registry=registry)

# 执行推理
result = result = reasoner.reason(
    question="2023年全球GDP排名前三的国家是哪些？它们的GDP总和是多少？",
    max_iterations=8,
)

# 查看 Thought-Action-Observation 循环
for step in result.steps:
    print(f"Thought: {step.thought}")
    if step.action:
        print(f"Action: {step.action.name}({step.action.input})")
    if step.observation:
        print(f"Observation: {step.observation[:100]}...")
    print()

print(f"最终答案: {result.final_answer}")
```

### 5.3 反思循环

反思循环让 Agent 对自己的输出进行批评和改进。

```python
from src.reasoning.reflection import ReflectionLoop

reflection = ReflectionLoop(max_iterations=3)

# 执行反思
result = reflection.reflect(
    task="写一首关于秋天的诗",
    initial_output="秋天来了，树叶黄了...",  # 初始输出
    criteria=["意境优美", "押韵", "意象丰富"],
)

# 查看改进过程
for i, iteration in enumerate(result.iterations):
    print(f"=== 第 {i+1} 轮 ===")
    print(f"输出: {iteration.output[:50]}...")
    print(f"评分: {iteration.score}/10")
    print(f"批评: {iteration.critique[:80]}...")
    print(f"改进: {iteration.improvements[:80]}...")
    print()

print(f"最终版本:\n{result.final_output}")
print(f"改进了 {len(result.iterations) - 1} 轮")
```

---

## 6. 搭建数据管道

### 6.1 管道概念

管道（Pipeline）将复杂任务分解为多个阶段，每个阶段执行特定操作，
数据在阶段间流动。

### 6.2 简单管道示例

```python
from src.pipeline.pipeline import Pipeline, PipelineStage

# 定义阶段
class ExtractStage(PipelineStage):
    """数据提取阶段"""
    name = "extract"

    def execute(self, data, context):
        print("  [提取] 从数据源加载数据...")
        return {"raw_data": [1, 2, 3, 4, 5]}

class TransformStage(PipelineStage):
    """数据转换阶段"""
    name = "transform"

    def execute(self, data, context):
        raw = data["raw_data"]
        print("  [转换] 处理数据...")
        return {"processed_data": [x * 2 for x in raw]}

class LoadStage(PipelineStage):
    """数据加载阶段"""
    name = "load"

    def execute(self, data, context):
        print("  [加载] 保存结果...")
        return {"result": data["processed_data"], "saved": True}

# 构建管道
pipeline = Pipeline(name="ETL 管道")
pipeline.add_stage(ExtractStage())
pipeline.add_stage(TransformStage())
pipeline.add_stage(LoadStage())

# 执行
result = pipeline.run({})
print(f"\n最终结果: {result['result']}")
```

### 6.3 带分支的管道

```python
from src.pipeline.pipeline import Pipeline, BranchStage, MergeStage

# 定义分支处理
class AnalysisBranch(BranchStage):
    """分析分支"""
    name = "analysis_branch"

    def branches(self, data, context):
        return {
            "statistical": StatisticalAnalysis(),
            "ml": MLAnalysis(),
        }

class StatisticalAnalysis(PipelineStage):
    name = "statistical_analysis"
    def execute(self, data, context):
        return {"stats": {"mean": 42, "std": 3.5}}

class MLAnalysis(PipelineStage):
    name = "ml_analysis"
    def execute(self, data, context):
        return {"predictions": {"class": "A", "confidence": 0.87}}

class ResultMerge(MergeStage):
    """结果合并"""
    name = "merge_results"

    def merge(self, branch_outputs, context):
        return {
            **branch_outputs["statistical"],
            **branch_outputs["ml"],
            "analysis_complete": True,
        }

# 构建管道
pipeline = Pipeline(name="分析管道")
pipeline.add_stage(DataLoadStage())
pipeline.add_stage(AnalysisBranch())
pipeline.add_stage(ResultMerge())
pipeline.add_stage(ReportStage())
```

### 6.4 错误处理与重试

```python
from src.pipeline.pipeline import Pipeline, RetryPolicy

# 配置重试策略
retry_policy = RetryPolicy(
    max_retries=3,
    backoff_factor=2,  # 指数退避
    retry_on=[TimeoutError, ConnectionError],
)

pipeline = Pipeline(name="可靠管道")
pipeline.add_stage(UnreliableStage(), retry_policy=retry_policy)

# 错误处理器
def on_error(stage_name, error, context):
    print(f"阶段 {stage_name} 出错: {error}")
    return "continue"  # 或 "stop", "retry"

pipeline.on_error = on_error
```

---

## 7. 开发自定义 Agent

### 7.1 继承 BaseAgent

创建自定义 Agent 的推荐方式是继承 `BaseAgent`：

```python
from src.agents.base_agent import BaseAgent, AgentConfig

class CustomAgent(BaseAgent):
    """
    自定义 Agent 示例。

    实现一个专门处理特定任务的 Agent。
    """

    def __init__(self, config: AgentConfig = None):
        super().__init__(config or AgentConfig(
            name="自定义助手",
            role="specialist",
        ))

    def _preprocess(self, task: str) -> str:
        """任务预处理钩子"""
        # 添加你的预处理逻辑
        print(f"  [预处理] 任务长度: {len(task)} 字符")
        return task

    def _execute_core(self, task: str) -> str:
        """核心执行逻辑"""
        # 实现你的 Agent 逻辑
        response = self._call_llm(self._build_prompt(task))
        return response

    def _postprocess(self, result: str) -> str:
        """结果后处理钩子"""
        # 添加你的后处理逻辑
        print(f"  [后处理] 结果长度: {len(result)} 字符")
        return result

    def _build_prompt(self, task: str) -> str:
        """构建提示词"""
        return f"""
{self.config.system_prompt}

任务: {task}

请以专业、清晰的方式回答。
"""
```

### 7.2 添加自定义能力

```python
from src.agents.base_agent import BaseAgent
from src.memory.working_memory import WorkingMemory

class EnhancedAgent(BaseAgent):
    """增强型 Agent，带有自定义记忆和工具"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 自定义记忆
        self.custom_memory = WorkingMemory(capacity=20)
        # 自定义工具列表
        self.custom_tools = {}

    def register_tool(self, name, func):
        """注册自定义工具"""
        self.custom_tools[name] = func

    def execute(self, task):
        # 检查记忆中是否有相关信息
        relevant = self.custom_memory.search(task)
        if relevant:
            print(f"  [记忆] 找到 {len(relevant)} 条相关信息")

        # 执行任务
        result = super().execute(task)

        # 保存结果到记忆
        self.custom_memory.add(
            content=f"任务: {task}\n结果: {result.output}",
            importance=0.8,
        )

        return result
```

### 7.3 使用 Agent 工厂注册自定义 Agent

```python
from src.agents.agent_factory import AgentFactory

# 注册自定义 Agent 类型
factory = AgentFactory()
factory.register_agent_type("custom", CustomAgent)

# 创建自定义 Agent
agent = factory.create_agent(
    name="我的自定义 Agent",
    agent_type="custom",  # 使用注册的类型
    role="specialist",
)

# 或者直接创建
agent = CustomAgent(config=AgentConfig(name="直接创建的 Agent"))
```

---

## 8. 构建你的第一个管道

让我们综合运用所学知识，构建一个完整的内容创作管道。

### 8.1 目标

创建一个 "文章创作管道"，能够：
1. 收集研究资料
2. 撰写文章大纲
3. 撰写正文
4. 审核润色
5. 输出最终版本

### 8.2 完整代码

```python
"""
文章创作管道 - 综合示例
"""

from src.pipeline.pipeline import Pipeline, PipelineStage
from src.agents.agent_factory import AgentFactory
from src.memory.working_memory import WorkingMemory
from src.collaboration.message_bus import MessageBus

# ==========================================
# 阶段 1：研究收集
# ==========================================
class ResearchStage(PipelineStage):
    name = "research"
    description = "收集研究资料"

    def __init__(self):
        super().__init__()
        factory = AgentFactory()
        self.researcher = factory.create_agent(
            name="研究员",
            role="researcher",
        )

    def execute(self, data, context):
        topic = data["topic"]
        print(f"  [研究] 正在收集关于 '{topic}' 的资料...")

        result = self.researcher.execute(
            f"收集关于 {topic} 的关键信息，包括：\n"
            f"1. 定义和背景\n"
            f"2. 最新发展\n"
            f"3. 主要应用领域\n"
            f"4. 未来趋势"
        )

        return {
            "topic": topic,
            "research_notes": result.output,
            "sources": 5,  # 模拟
        }

# ==========================================
# 阶段 2：大纲撰写
# ==========================================
class OutlineStage(PipelineStage):
    name = "outline"
    description = "撰写文章大纲"

    def __init__(self):
        super().__init__()
        factory = AgentFactory()
        self.writer = factory.create_agent(
            name="大纲写手",
            role="writer",
        )

    def execute(self, data, context):
        print(f"  [大纲] 撰写文章结构...")

        result = self.writer.execute(
            f"根据以下研究笔记，撰写一篇文章的大纲：\n\n"
            f"主题: {data['topic']}\n\n"
            f"研究笔记:\n{data['research_notes']}"
        )

        return {
            **data,
            "outline": result.output,
        }

# ==========================================
# 阶段 3：正文撰写
# ==========================================
class DraftingStage(PipelineStage):
    name = "drafting"
    description = "撰写正文"

    def __init__(self):
        super().__init__()
        factory = AgentFactory()
        self.writer = factory.create_agent(
            name="正文写手",
            role="writer",
        )

    def execute(self, data, context):
        print(f"  [撰写] 撰写文章正文...")

        result = self.writer.execute(
            f"根据以下大纲撰写一篇完整的文章：\n\n"
            f"大纲:\n{data['outline']}\n\n"
            f"参考研究笔记:\n{data['research_notes']}"
        )

        return {
            **data,
            "draft": result.output,
        }

# ==========================================
# 阶段 4：审核润色
# ==========================================
class EditingStage(PipelineStage):
    name = "editing"
    description = "审核润色"

    def __init__(self):
        super().__init__()
        factory = AgentFactory()
        self.editor = factory.create_agent(
            name="编辑",
            role="editor",
        )

    def execute(self, data, context):
        print(f"  [编辑] 审核和润色...")

        result = self.editor.execute(
            f"请审核并润色以下文章，检查：\n"
            f"1. 逻辑是否清晰\n"
            f"2. 语言是否流畅\n"
            f"3. 是否有错别字\n"
            f"4. 结构是否合理\n\n"
            f"文章:\n{data['draft']}"
        )

        return {
            **data,
            "final_article": result.output,
            "edit_notes": "已完成审核润色",
        }

# ==========================================
# 构建并运行管道
# ==========================================
def main():
    print("=" * 60)
    print("  文章创作管道")
    print("=" * 60)
    print()

    # 创建管道
    pipeline = Pipeline(name="文章创作管道")

    # 添加阶段
    pipeline.add_stage(ResearchStage())
    pipeline.add_stage(OutlineStage())
    pipeline.add_stage(DraftingStage())
    pipeline.add_stage(EditingStage())

    # 显示管道结构
    print("管道结构:")
    for i, stage in enumerate(pipeline.stages):
        print(f"  {i+1}. {stage.name} - {stage.description}")
    print()

    # 执行管道
    topic = "人工智能在医疗领域的应用"
    print(f"创作主题: {topic}")
    print()

    result = pipeline.run({"topic": topic})

    # 输出结果
    print()
    print("=" * 60)
    print("  最终结果")
    print("=" * 60)
    print()
    print(f"主题: {result['topic']}")
    print(f"研究资料: 约 {result['sources']} 个来源")
    print(f"编辑状态: {result['edit_notes']}")
    print()
    print("最终文章 (前 500 字):")
    print("-" * 40)
    print(result["final_article"][:500] + "...")
    print()
    print("  ✓ 管道执行完成！")

if __name__ == "__main__":
    main()
```

### 8.3 运行和扩展

```bash
# 保存为 article_pipeline.py 并运行
python article_pipeline.py
```

**可以尝试的扩展：**

1. 添加更多阶段（如图片生成、SEO 优化）
2. 添加分支（同时生成多个版本再选择最优）
3. 添加错误处理和重试
4. 接入真实的 LLM API
5. 添加进度监控和日志

---

## 下一步

恭喜你完成了基础教程！接下来可以：

- 探索 [examples/](.) 目录中的更多示例
- 阅读 [docs/](../docs/) 中的深入文档
- 查看 [src/](../src/) 中的源代码
- 尝试构建自己的应用

如有问题，欢迎参考示例代码和 API 文档。
