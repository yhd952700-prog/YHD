# 构建研究机器人

本教程将带你构建一个专业的研究智能体，能够自动收集信息、分析资料并生成研究报告。

---

## 目标

我们将构建一个研究机器人，它可以：

1. 接收一个研究主题
2. 自动搜索和收集相关信息
3. 整理和分析资料
4. 生成结构化的研究报告
5. 引用来源，确保可追溯

---

## 前置条件

- LiuHao AI OS 服务运行中
- 已安装网页搜索插件（`liuhao-web-search-plugin`）
- Python SDK 已安装

---

## 第一步：设计研究智能体

### 1.1 角色定位

研究助手需要具备以下能力：
- 信息检索能力
- 信息整理和分析能力
- 结构化写作能力
- 事实核查意识

### 1.2 创建智能体配置

创建 `research_agent.py`：

```python
"""
研究助手智能体
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 研究助手系统提示词
system_prompt = """你是一名专业的研究分析师，名叫"研究员小研"。

你的工作流程：
1. 理解研究主题和要求
2. 制定研究计划（搜索关键词、信息来源）
3. 搜索和收集相关信息
4. 评估信息来源的可靠性
5. 整理和分析资料
6. 生成结构化的研究报告
7. 标注所有引用来源

你的原则：
- 基于事实，不臆测
- 多方验证，交叉比对
- 明确区分事实和观点
- 标注信息来源，便于追溯
- 客观中立，不带偏见
- 如实说明信息不足的地方

报告结构：
1. 执行摘要
2. 背景介绍
3. 主要发现（分点论述）
4. 数据分析（如有）
5. 结论与建议
6. 参考资料（列出所有来源）

请始终用中文回复，报告使用 Markdown 格式。"""

# 创建智能体
agent = client.agents.create(
    name="研究员小研",
    role="researcher",
    description="专业研究分析师，擅长信息收集、分析和报告生成",
    config={
        "model": "qwen2.5:7b",
        "temperature": 0.3,
        "max_tokens": 8192,
        "system_prompt": system_prompt,
        "tools": [
            "web_search.search",
            "web_search.summarize_url",
            "file_reader.read",
            "file_writer.write"
        ],
        "memory": {
            "enabled": True,
            "semantic_memory_enabled": True,
            "episodic_memory_enabled": True
        },
        "planning": {
            "enabled": True,
            "strategy": "hybrid",
            "max_plan_steps": 15
        }
    }
)

print(f"✅ 研究助手创建成功！")
print(f"   ID: {agent.id}")

with open("research_agent_id.txt", "w") as f:
    f.write(agent.id)
```

---

## 第二步：设置知识库

为了让研究助手有更好的知识基础，我们创建一个知识库。

### 2.1 创建知识库

```python
"""
创建研究知识库
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 创建知识库
kb = client.knowledge.create_base(
    name="研究资料库",
    description="收集的研究资料和参考文档",
    config={
        "embedding_model": "nomic-embed-text",
        "chunk_size": 1000,
        "chunk_overlap": 100,
        "language": "zh-CN"
    }
)

print(f"✅ 知识库创建成功：{kb.id}")

# 导入一些参考资料（示例）
# 你可以导入本地文件或网页
import_result = client.knowledge.import_url(
    base_id=kb.id,
    url="https://example.com/research-paper",
    options={"max_pages": 10}
)

print(f"📚 导入任务已启动：{import_result.import_id}")

with open("research_kb_id.txt", "w") as f:
    f.write(kb.id)
```

---

## 第三步：实现研究工作流

让我们创建一个完整的研究工作流，从选题到生成报告。

### 3.1 研究工作流脚本

创建 `research_workflow.py`：

```python
"""
研究工作流：从选题到生成报告
"""
import json
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

with open("research_agent_id.txt", "r") as f:
    agent_id = f.read().strip()

with open("research_kb_id.txt", "r") as f:
    kb_id = f.read().strip()


def do_research(topic: str, output_file: str = None, depth: str = "medium"):
    """
    执行研究任务
    
    Args:
        topic: 研究主题
        output_file: 输出文件路径
        depth: 研究深度（shallow/medium/deep）
    """
    print(f"🔍 开始研究：{topic}")
    print(f"📊 研究深度：{depth}")
    print("-" * 50)
    
    # 构建研究任务
    task = f"""请对以下主题进行深入研究，并生成一份完整的研究报告：

研究主题：{topic}

研究深度：{depth}

要求：
1. 先搜索相关信息，至少参考 5 个不同来源
2. 评估信息来源的可靠性
3. 整理主要观点和发现
4. 生成结构化的 Markdown 报告
5. 在报告末尾列出所有参考来源（标题 + URL）
6. 如实说明信息不足或有争议的地方

报告结构：
# {topic} 研究报告

## 执行摘要
（200 字以内的核心结论）

## 一、背景介绍
（主题的背景和重要性）

## 二、主要发现
（分点论述，每个发现有证据支撑）

## 三、不同观点
（列出不同立场的观点和论据）

## 四、趋势分析
（发展趋势和未来展望）

## 五、结论与建议
（总结核心结论，给出建议）

## 参考资料
（编号列表，包含标题和来源 URL）
"""
    
    # 执行研究任务
    print("⏳ 正在进行研究，请稍候...\n")
    
    result = client.agents.execute(
        agent_id=agent_id,
        task=task,
        context={
            "knowledge_base_id": kb_id,
            "research_depth": depth
        },
        options={
            "max_steps": 30 if depth == "deep" else 20,
            "timeout_seconds": 600
        }
    )
    
    print(f"✅ 研究完成！状态：{result.status}")
    print(f"⏱️  耗时：{result.metrics['duration_seconds']:.1f} 秒")
    print(f"🔍 搜索次数：{result.metrics.get('tool_calls', 0)} 次")
    print(f"📝 Token 消耗：{result.metrics.get('total_tokens', 0)}")
    
    # 输出报告
    report = result.output
    
    print("\n" + "=" * 50)
    print("📄 研究报告预览（前 500 字）：")
    print("-" * 50)
    print(report[:500] + "..." if len(report) > 500 else report)
    print("=" * 50)
    
    # 保存到文件
    if output_file is None:
        # 自动生成文件名
        safe_topic = topic.replace(" ", "_").replace("/", "_")[:30]
        output_file = f"research_report_{safe_topic}.md"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n💾 报告已保存到：{output_file}")
    
    # 存储到知识库（可选）
    try:
        client.knowledge.add_document(
            base_id=kb_id,
            title=f"{topic} 研究报告",
            content=report,
            metadata={
                "type": "research_report",
                "topic": topic,
                "depth": depth
            }
        )
        print("📚 报告已存入知识库")
    except Exception as e:
        print(f"⚠️  存入知识库失败：{e}")
    
    return report


# 主函数
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python research_workflow.py <研究主题> [深度]")
        print("深度选项: shallow, medium, deep")
        print("\n示例:")
        print("  python research_workflow.py '人工智能的发展趋势' medium")
        sys.exit(1)
    
    topic = sys.argv[1]
    depth = sys.argv[2] if len(sys.argv) > 2 else "medium"
    
    do_research(topic, depth=depth)
```

### 3.2 运行研究工作流

```bash
python research_workflow.py "大语言模型的发展现状和未来趋势" medium
```

你将看到研究过程的进度，最终生成一份 Markdown 格式的研究报告。

---

## 第四步：高级功能

### 4.1 多源信息对比

让研究助手对比不同来源的信息：

```python
task = """请对比以下三个来源关于"AI 安全"的观点：

来源 A: https://example.com/ai-safety-view-a
来源 B: https://example.com/ai-safety-view-b
来源 C: https://example.com/ai-safety-view-c

要求：
1. 分别总结每个来源的核心观点
2. 找出它们的共识点和分歧点
3. 分析各自的论据和证据质量
4. 给出你的综合评价
"""
```

### 4.2 定期研究报告

可以结合任务调度，生成定期研究报告：

```python
# 每周一自动生成行业动态报告
# 使用定时任务调度器
from liuhao.tasks import schedule

schedule.cron(
    func=do_research,
    args=["AI 行业每周动态", "reports/weekly/"],
    cron="0 9 * * 1"  # 每周一早上 9 点
)
```

### 4.3 研究记忆

研究助手会记住做过的研究，可以在此基础上深入：

```python
# 基于之前的研究深入某个方向
task = """之前我们研究了"大语言模型的发展现状"，
请在此基础上，深入研究"大语言模型的安全对齐问题"。

重点关注：
1. 什么是安全对齐
2. 主要的对齐技术
3. 当前的挑战和局限
4. 未来的研究方向

可以利用之前研究中收集的资料。"""
```

---

## 第五步：质量提升技巧

### 5.1 提升信息质量

1. **指定可信来源**：告诉智能体优先使用哪些来源
2. **交叉验证**：要求至少 N 个来源验证
3. **标注置信度**：让智能体对每个结论标注置信度
4. **事实核查步骤**：增加专门的事实核查步骤

### 5.2 提升报告质量

1. **明确结构**：给出清晰的报告结构要求
2. **提供示例**：给出你期望的报告格式示例
3. **字数要求**：明确报告的长度要求
4. **多轮迭代**：先生成初稿，再逐步优化

### 5.3 常见问题

| 问题 | 解决方案 |
|------|---------|
| 信息来源不可靠 | 要求优先使用权威来源，增加交叉验证 |
| 报告太浅 | 增加研究深度，要求更多来源和分析 |
| 结构混乱 | 明确报告结构，给出模板 |
| 缺少引用 | 强制要求标注来源，设置最少来源数 |
| 事实错误 | 增加事实核查步骤，多方验证 |

---

## 第六步：扩展你的研究助手

### 6.1 添加专业数据库接入

- 学术论文数据库（arXiv、IEEE Xplore 等）
- 新闻资讯 API
- 行业报告平台
- 社交媒体数据

### 6.2 添加数据分析能力

- 数据可视化
- 统计分析
- 趋势预测
- 对比分析

### 6.3 协作模式

- 主研究员 + 多个专题研究员
- 评论员负责质量审查
- 编辑负责最终报告润色

---

## 下一步

- [构建代码审查员](build_a_code_reviewer.md) - 构建代码审查智能体
- [构建多智能体系统](build_multi_agent_system.md) - 构建研究团队
- [部署到生产环境](deploy_to_production.md) - 生产部署指南
