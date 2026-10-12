# 构建多智能体系统

本教程将带你构建一个多智能体协作系统，让多个专业智能体协同工作完成复杂任务。

---

## 目标

我们将构建一个内容创作团队，包含以下角色：

1. **项目经理** - 任务分配、进度协调、质量把控
2. **研究员** - 信息收集、资料整理
3. **撰稿人** - 内容撰写、文案创作
4. **编辑** - 内容审校、润色优化
5. **设计师** - 视觉设计、配图建议

---

## 前置条件

- LiuHao AI OS 服务运行中
- Python SDK 已安装
- 对单个智能体的使用有基本了解

---

## 第一步：设计团队结构

### 1.1 协作模式选择

我们将使用**主从模式 + 流水线模式**的混合架构：

```
        ┌──────────────┐
        │   项目经理    │ ← 主智能体，协调全局
        └──────┬───────┘
       ┌───────┼───────┐
       ▼       ▼       ▼
    研究员   撰稿人   设计师
       │       │
       └───┬───┘
           ▼
         编辑
           │
           ▼
       最终输出
```

工作流程：
1. 项目经理接收任务，制定计划
2. 研究员收集信息，整理资料
3. 撰稿人根据资料撰写初稿
4. 设计师提供视觉建议
5. 编辑审核润色，输出最终版本
6. 项目经理验收交付

---

## 第二步：创建各个智能体

### 2.1 创建团队脚本

创建 `create_team.py`：

```python
"""
创建内容创作团队
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

team = {}


def create_agent(name, role, description, system_prompt, tools=None):
    """辅助函数：创建智能体"""
    agent = client.agents.create(
        name=name,
        role=role,
        description=description,
        config={
            "model": "qwen2.5:7b",
            "temperature": 0.7,
            "max_tokens": 4096,
            "system_prompt": system_prompt,
            "tools": tools or [],
            "memory": {
                "enabled": True,
                "semantic_memory_enabled": True
            }
        }
    )
    print(f"✅ 创建 {name} ({role}): {agent.id}")
    return agent


# 1. 项目经理
pm_prompt = """你是一名资深项目经理，名叫"项目管家"。

你的职责：
1. 理解项目需求，制定执行计划
2. 分配任务给团队成员
3. 跟踪进度，协调资源
4. 质量把控，验收成果
5. 向客户汇报进展

你的工作方式：
- 先明确目标和要求
- 再分解任务，分配给合适的成员
- 过程中监控进度，及时调整
- 最后汇总成果，确保质量

沟通风格：
- 清晰明确，指令具体
- 及时反馈，主动沟通
- 对事不对人，专业客观"""

team["project_manager"] = create_agent(
    "项目管家", "manager",
    "负责项目管理和团队协调",
    pm_prompt,
    tools=["file_writer.write", "datetime.now"]
)


# 2. 研究员
researcher_prompt = """你是一名专业研究员，名叫"探路者"。

你的专长：
- 信息检索和收集
- 资料整理和分析
- 事实核查和验证
- 结构化信息输出

你的工作原则：
- 信息来源可靠，多方交叉验证
- 区分事实和观点
- 标注信息来源
- 如实说明信息不足的地方

输出格式：
- 要点式列出关键信息
- 按重要性排序
- 每个信息标注来源和置信度"""

team["researcher"] = create_agent(
    "探路者", "researcher",
    "负责信息收集和研究分析",
    researcher_prompt,
    tools=["web_search.search", "web_search.summarize_url"]
)


# 3. 撰稿人
writer_prompt = """你是一名资深内容创作者，名叫"墨客"。

你的专长：
- 文章撰写和内容创作
- 不同文体的驾驭能力
- 读者视角的内容设计
- 结构和逻辑把控

你的写作原则：
- 读者导向，换位思考
- 结构清晰，逻辑通顺
- 语言生动，避免枯燥
- 真实可信，不夸大

写作流程：
1. 理解目标读者和写作目的
2. 搭建文章结构框架
3. 填充内容，注意过渡衔接
4. 润色语言，提升可读性"""

team["writer"] = create_agent(
    "墨客", "creator",
    "负责内容撰写和文案创作",
    writer_prompt,
    tools=["file_reader.read"]
)


# 4. 编辑
editor_prompt = """你是一名资深编辑，名叫"品叔"。

你的职责：
- 内容审核和质量把控
- 文字润色和优化
- 事实核查和纠错
- 风格统一和规范

你的审核标准：
1. 内容准确性：事实是否正确，数据是否可靠
2. 结构逻辑性：结构是否清晰，逻辑是否通顺
3. 语言表达：用词是否准确，表达是否流畅
4. 风格一致性：是否符合目标风格
5. 可读性：是否易于理解，节奏是否合适

你的修改原则：
- 保留作者原意，只优化表达
- 重要修改说明理由
- 小问题直接修改，大问题提出建议"""

team["editor"] = create_agent(
    "品叔", "critic",
    "负责内容审核和润色",
    editor_prompt,
    tools=["file_reader.read", "file_writer.write"]
)


# 5. 设计师
designer_prompt = """你是一名视觉设计师，名叫"画影"。

你的专长：
- 视觉设计建议
- 配图方案推荐
- 排版布局建议
- 色彩搭配指导

你的工作内容：
- 根据内容提供视觉呈现建议
- 推荐配图类型和风格
- 给出排版和格式建议
- 提供色彩方案

输出要求：
- 具体可执行的建议
- 描述清晰，便于理解
- 考虑目标受众和使用场景"""

team["designer"] = create_agent(
    "画影", "creator",
    "负责视觉设计和配图建议",
    designer_prompt,
    tools=[]
)

# 保存团队成员信息
import json
team_ids = {name: agent.id for name, agent in team.items()}
with open("team_ids.json", "w") as f:
    json.dump(team_ids, f, indent=2)

print(f"\n🎉 团队创建完成！共 {len(team)} 名成员")
print("团队成员已保存到 team_ids.json")
```

---

## 第三步：实现协作工作流

### 3.1 内容创作工作流

创建 `content_creation_team.py`：

```python
"""
内容创作团队工作流
"""
import json
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 加载团队成员 ID
with open("team_ids.json", "r") as f:
    team = json.load(f)


def create_content(topic: str, content_type: str = "article", 
                   target_audience: str = "general", 
                   output_file: str = None):
    """
    团队协作创建内容
    
    Args:
        topic: 主题
        content_type: 内容类型（article/blog/newsletter）
        target_audience: 目标读者
        output_file: 输出文件
    """
    print(f"📝 开始创作：{topic}")
    print(f"🎯 类型：{content_type}，受众：{target_audience}")
    print("=" * 60)
    
    # Step 1: 项目经理制定计划
    print("\n[1/5] 📋 项目经理制定计划...")
    
    plan_result = client.agents.execute(
        agent_id=team["project_manager"],
        task=f"""我们需要创作一篇关于"{topic}"的{content_type}，
目标读者是{target_audience}。

请制定执行计划，包括：
1. 研究方向和关键词
2. 文章结构大纲
3. 各成员的任务分配
4. 预期时间安排

输出格式：项目执行计划""",
        options={"max_steps": 5}
    )
    
    print(f"   ✅ 计划已制定")
    plan = plan_result.output
    
    # Step 2: 研究员收集资料
    print("\n[2/5] 🔍 研究员收集资料...")
    
    research_result = client.agents.execute(
        agent_id=team["researcher"],
        task=f"""请为"{topic}"这个主题收集相关信息。

参考以下研究方向：
{plan}

要求：
1. 收集至少 5 个方面的信息
2. 标注信息来源和可信度
3. 按重要性排序
4. 输出结构化的研究笔记

这将用于后续的内容创作。""",
        options={"max_steps": 15, "timeout_seconds": 300}
    )
    
    print(f"   ✅ 资料收集完成")
    research_notes = research_result.output
    
    # Step 3: 撰稿人撰写初稿
    print("\n[3/5] ✍️  撰稿人撰写初稿...")
    
    draft_result = client.agents.execute(
        agent_id=team["writer"],
        task=f"""请根据以下资料，撰写一篇关于"{topic}"的{content_type}。

目标读者：{target_audience}

文章结构参考：
{plan}

参考资料：
{research_notes}

要求：
- 字数约 1500 字
- 结构清晰，逻辑通顺
- 语言生动，有吸引力
- 可以适当补充合理的内容
- 输出完整的 Markdown 格式文章""",
        options={"max_steps": 10, "timeout_seconds": 300}
    )
    
    print(f"   ✅ 初稿完成")
    draft = draft_result.output
    
    # Step 4: 设计师提供视觉建议
    print("\n[4/5] 🎨 设计师提供视觉建议...")
    
    design_result = client.agents.execute(
        agent_id=team["designer"],
        task=f"""请为以下{content_type}提供视觉设计建议。

文章主题：{topic}
目标读者：{target_audience}

文章内容：
{draft[:2000]}...

请提供：
1. 配图建议（类型、风格、数量）
2. 排版和格式建议
3. 色彩方案建议
4. 重点内容的视觉突出方式""",
        options={"max_steps": 5}
    )
    
    print(f"   ✅ 设计建议完成")
    design_notes = design_result.output
    
    # Step 5: 编辑审核润色
    print("\n[5/5] ✅ 编辑审核润色...")
    
    final_result = client.agents.execute(
        agent_id=team["editor"],
        task=f"""请审核并润色以下{content_type}。

主题：{topic}
目标读者：{target_audience}

原稿：
{draft}

设计建议：
{design_notes}

请完成以下工作：
1. 审核内容准确性和逻辑性
2. 优化语言表达
3. 调整结构，提升可读性
4. 修正错别字和语法错误
5. 根据设计建议调整格式

输出最终版本的完整文章，以及修改说明。""",
        options={"max_steps": 10, "timeout_seconds": 300}
    )
    
    print(f"   ✅ 最终版本完成")
    final_content = final_result.output
    
    # 输出结果
    print("\n" + "=" * 60)
    print("🎉 内容创作完成！")
    print("=" * 60)
    
    # 保存到文件
    if output_file is None:
        safe_topic = topic.replace(" ", "_").replace("/", "_")[:30]
        output_file = f"article_{safe_topic}.md"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# {topic}\n\n")
        f.write(f"**类型**: {content_type}  \n")
        f.write(f"**目标读者**: {target_audience}  \n")
        f.write(f"**创作团队**: LiuHao AI OS 内容团队  \n\n")
        f.write("---\n\n")
        f.write(final_content)
        f.write("\n\n---\n\n")
        f.write("## 附录\n\n")
        f.write("### 研究资料\n\n")
        f.write(research_notes)
        f.write("\n\n### 设计建议\n\n")
        f.write(design_notes)
    
    print(f"\n💾 文章已保存：{output_file}")
    
    # 统计
    print(f"\n📊 团队统计：")
    print(f"   参与成员: 5 人")
    print(f"   研究阶段: {research_result.metrics['duration_seconds']:.0f}s")
    print(f"   写作阶段: {draft_result.metrics['duration_seconds']:.0f}s")
    print(f"   编辑阶段: {final_result.metrics['duration_seconds']:.0f}s")
    
    return final_content


# 主函数
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python content_creation_team.py <主题> [类型] [受众]")
        print("\n示例:")
        print('  python content_creation_team.py "人工智能的未来" article "技术爱好者"')
        sys.exit(1)
    
    topic = sys.argv[1]
    content_type = sys.argv[2] if len(sys.argv) > 2 else "article"
    target_audience = sys.argv[3] if len(sys.argv) > 3 else "general"
    
    create_content(topic, content_type, target_audience)
```

### 3.2 运行团队

```bash
python content_creation_team.py "大语言模型的应用前景" article "技术从业者"
```

---

## 第四步：协作模式进阶

### 4.1 委员会模式：多视角评审

对于重要决策，可以使用委员会模式：

```python
def committee_review(question: str, agent_ids: list, 
                     decision_mode: str = "majority"):
    """
    委员会评审：多个智能体共同决策
    """
    results = []
    
    # 每个成员独立给出意见
    for agent_id in agent_ids:
        result = client.agents.execute(
            agent_id=agent_id,
            task=f"""请就以下问题给出你的专业意见和投票：
问题：{question}

请给出：
1. 你的观点（支持/反对/中立）
2. 你的理由（至少 3 点）
3. 置信度（0-100%）
4. 风险提示"""
        )
        results.append(result)
    
    # 汇总意见
    summary = client.agents.execute(
        agent_id=team["project_manager"],
        task=f"""请汇总以下委员会成员对问题的意见：

问题：{question}

各成员意见：
{chr(10).join(f"成员{i+1}:\n{r.output}" for i, r in enumerate(results))}

请给出：
1. 投票结果统计
2. 主要共识点
3. 主要分歧点
4. 综合建议"""
    )
    
    return summary
```

### 4.2 黑板模式：开放式问题求解

对于复杂的、探索性的问题，使用黑板模式：

```python
from liuhao.ai.blackboard import Blackboard, KnowledgeSource

# 创建黑板
bb = Blackboard(
    problem="如何提升用户留存率？",
    initial_state={"industry": "SaaS", "current_retention": "60%"}
)

# 添加知识源（智能体）
bb.add_knowledge_source(
    agent_id=team["researcher"],
    name="数据分析专家",
    trigger="数据不足"
)

bb.add_knowledge_source(
    agent_id=team["writer"],
    name="用户体验专家",
    trigger="需要方案"
)

bb.add_knowledge_source(
    agent_id=team["editor"],
    name="方案评估专家",
    trigger="有方案待评估"
)

# 运行黑板求解
result = bb.run(max_iterations=10)
print(f"最终方案：{result.final_solution}")
```

---

## 第五步：团队管理最佳实践

### 5.1 角色设计原则

1. **职责清晰**：每个角色有明确的边界，避免重叠
2. **能力互补**：不同角色的能力相互补充
3. **接口明确**：角色之间的输入输出有明确定义
4. **可替换性**：同一角色的不同实现可以互换

### 5.2 协调机制

1. **明确的项目经理**：有一个总协调者，避免混乱
2. **标准化输出格式**：每个角色的输出格式标准化，便于下游使用
3. **进度同步**：定期同步进度，及时发现问题
4. **升级机制**：问题无法解决时及时升级

### 5.3 质量控制

1. **多轮审查**：重要内容经过多轮审核
2. **交叉验证**：不同角色互相验证
3. **最终验收**：项目经理最终验收
4. **反馈循环**：收集反馈，持续优化

### 5.4 成本优化

1. **任务分层**：简单任务用小模型，复杂任务用大模型
2. **并行执行**：独立任务并行执行，节省时间
3. **结果复用**：研究成果可以被多个项目复用
4. **合理分工**：让合适的角色做合适的事

---

## 下一步

- [构建你的第一个智能体](build_your_first_agent.md) - 智能体基础
- [构建研究机器人](build_a_research_bot.md) - 研究员智能体
- [部署到生产环境](deploy_to_production.md) - 生产部署
- [多智能体核心概念](../knowledge_base/core-concepts/multi_agent.md) - 理论详解
