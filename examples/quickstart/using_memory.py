"""
使用记忆系统 - LiuHao AI OS 快速入门示例

本示例演示 LiuHao AI OS 的记忆系统基础用法：
1. 工作记忆（Working Memory）- 短期注意力缓冲区
2. 语义网络（Semantic Network）- 知识图谱式记忆
3. 如何添加、检索和搜索记忆

运行方式：
    python examples/quickstart/using_memory.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.memory import (
    # 工作记忆
    WorkingMemory,
    WorkingMemoryItem,
    MemoryPriority,
    # 语义网络
    SemanticMemoryNetwork,
    SemanticEntity,
    SemanticRelation,
    EntityType,
    RelationType,
)


# ======================================================================
# 第一部分：工作记忆（Working Memory）
# ======================================================================

def demo_working_memory():
    """
    演示工作记忆的基本用法。

    工作记忆就像"心理草稿本"，容量有限（默认 7 项），
    项目会随时间衰减，高优先级的项目会保留更久。

    类比人类记忆：
    - 工作记忆 ≈ 你现在正在想的事情
    - 容量限制 ≈ "7 ± 2" 法则（Miller's law）
    - 优先级 ≈ 注意力集中程度
    """
    print("=" * 60)
    print("  第一部分：工作记忆（Working Memory）")
    print("=" * 60)
    print()

    # ---- 创建工作记忆实例 ----
    # capacity 参数控制容量，默认 7
    wm = WorkingMemory(capacity=7)
    print(f"[1/5] 创建工作记忆，容量: {wm.capacity} 项")

    # ---- 添加记忆项 ----
    # 不同优先级的项目在工作记忆中保留的时间不同
    print("\n[2/5] 添加记忆项...")

    # 关键任务 - CRITICAL 优先级，不会被轻易清除
    wm.add(
        content="完成用户需求文档",
        item_type="goal",
        priority=MemoryPriority.CRITICAL,
        source="user",
        tags=["goal", "urgent"],
    )

    # 重要上下文 - HIGH 优先级
    wm.add(
        content="项目截止日期：本周五",
        item_type="fact",
        priority=MemoryPriority.HIGH,
        source="system",
        tags=["deadline", "project"],
    )

    # 普通信息 - MEDIUM 优先级
    wm.add(
        content="用户喜欢简洁的设计风格",
        item_type="fact",
        priority=MemoryPriority.MEDIUM,
        source="user_profile",
        tags=["preference", "design"],
    )

    # 临时信息 - LOW 优先级，可能会被挤出
    wm.add(
        content="上次会议提到了性能优化",
        item_type="thought",
        priority=MemoryPriority.LOW,
        source="meeting_notes",
        tags=["meeting", "performance"],
    )

    # 转瞬即逝的信息 - FLEETING 优先级
    wm.add(
        content="用户喝了一杯咖啡",
        item_type="sensory",
        priority=MemoryPriority.FLEETING,
        source="observation",
        tags=["trivial"],
    )

    print(f"       当前记忆项数: {wm.size} / {wm.capacity}")

    # ---- 读取记忆项 ----
    # 按类型获取
    print("\n[3/5] 读取记忆项...")
    goals = wm.get_by_type("goal")
    for item in goals:
        print(f"       目标: {item.content} (优先级: {item.priority.value})")

    # 按标签搜索
    project_items = wm.search_by_tag("project")
    print(f"\n       含 'project' 标签的项: {len(project_items)} 个")

    # ---- 访问记忆（会更新 last_accessed）----
    # 每次访问都会"刷新"记忆，延缓衰减
    print("\n[4/5] 访问记忆项（刷新激活度）...")
    all_items = wm.get_all()
    for item in all_items[:3]:
        accessed = wm.get(item.id)
        if accessed:
            print(f"       访问: {accessed.content[:30]}... "
                  f"(激活度: {accessed.activation:.2f})")

    # ---- 模拟时间衰减 ----
    # 工作记忆中的项目会随时间衰减激活度
    print("\n[5/5] 模拟时间衰减后...")
    wm.tick(decay_amount=0.2)  # 手动触发衰减

    remaining = wm.get_all()
    print(f"       剩余记忆项: {len(remaining)} 个")
    for item in remaining:
        print(f"         - {item.content[:30]}... "
              f"(激活度: {item.activation:.2f}, 优先级: {item.priority.value})")

    print()
    return wm


# ======================================================================
# 第二部分：语义网络（Semantic Network）
# ======================================================================

def demo_semantic_network():
    """
    演示语义记忆网络的基本用法。

    语义网络存储"概念"（实体）和它们之间的"关系"，
    就像知识图谱一样，可以进行关联推理。

    示例：
        Python --is_a--> ProgrammingLanguage
        Python --used_for--> WebDevelopment
        Python --created_by--> GuidoVanRossum
    """
    print("=" * 60)
    print("  第二部分：语义记忆网络（Semantic Network）")
    print("=" * 60)
    print()

    # ---- 创建语义网络 ----
    net = SemanticMemoryNetwork()
    print("[1/6] 创建语义记忆网络")

    # ---- 添加实体 ----
    # 实体是网络中的"节点"，代表概念、人、技术等
    print("\n[2/6] 添加实体...")

    python_id = net.add_entity(
        name="Python",
        entity_type=EntityType.TECHNOLOGY,
        description="一种高级编程语言，以简洁和可读性著称",
        tags=["programming", "scripting", "backend"],
    )

    js_id = net.add_entity(
        name="JavaScript",
        entity_type=EntityType.TECHNOLOGY,
        description="Web 前端的主要编程语言",
        tags=["programming", "frontend", "web"],
    )

    lang_id = net.add_entity(
        name="ProgrammingLanguage",
        entity_type=EntityType.CATEGORY,
        description="用于编写计算机程序的语言",
        tags=["category"],
    )

    guido_id = net.add_entity(
        name="Guido van Rossum",
        entity_type=EntityType.PERSON,
        description="Python 编程语言的创造者",
        tags=["creator", "python"],
    )

    web_dev_id = net.add_entity(
        name="WebDevelopment",
        entity_type=EntityType.SKILL,
        description="构建网站和 Web 应用的技术领域",
        tags=["web", "development"],
    )

    print(f"       已添加 {len(net.entities)} 个实体")

    # ---- 添加关系 ----
    # 关系是网络中的"边"，连接两个实体
    print("\n[3/6] 添加关系...")

    # Python IS_A 编程语言
    net.add_relation(
        source_id=python_id,
        target_id=lang_id,
        relation_type=RelationType.IS_A,
        confidence=0.99,
    )

    # JavaScript IS_A 编程语言
    net.add_relation(
        source_id=js_id,
        target_id=lang_id,
        relation_type=RelationType.IS_A,
        confidence=0.99,
    )

    # Python 用于 Web 开发
    net.add_relation(
        source_id=python_id,
        target_id=web_dev_id,
        relation_type=RelationType.USED_FOR,
        confidence=0.85,
    )

    # Python 由 Guido 创造
    net.add_relation(
        source_id=python_id,
        target_id=guido_id,
        relation_type=RelationType.CREATED_BY,
        confidence=0.95,
    )

    # Python 与 JavaScript 相似
    net.add_relation(
        source_id=python_id,
        target_id=js_id,
        relation_type=RelationType.SIMILAR_TO,
        confidence=0.6,
    )

    print(f"       已添加 {len(net.relations)} 条关系")

    # ---- 查询实体 ----
    print("\n[4/6] 查询实体信息...")
    python_entity = net.get_entity(python_id)
    if python_entity:
        print(f"       实体: {python_entity.name}")
        print(f"       类型: {python_entity.entity_type.value}")
        print(f"       描述: {python_entity.description}")

    # ---- 查找关系 ----
    print("\n[5/6] 查找 Python 的关系...")
    python_relations = net.get_relations(python_id)
    for rel in python_relations:
        target = net.get_entity(rel.target_id)
        target_name = target.name if target else "Unknown"
        print(f"       -> {rel.relation_type.value} -> {target_name} "
              f"(置信度: {rel.confidence:.2f})")

    # ---- 搜索 ----
    # 按名称模糊搜索实体
    print("\n[6/6] 搜索实体...")
    results = net.search_entities("web")
    print(f"       搜索 'web' 找到 {len(results)} 个实体:")
    for entity in results:
        print(f"         - {entity.name} ({entity.entity_type.value})")

    # ---- 关联查找（推理）----
    # 找出与 Python 相关的所有实体
    print("\n关联查找: 与 Python 相关的实体")
    related = net.get_related_entities(python_id, max_depth=2)
    for entity_id, depth in related.items():
        entity = net.get_entity(entity_id)
        if entity and entity_id != python_id:
            print(f"       深度 {depth}: {entity.name}")

    print()
    return net


# ======================================================================
# 第三部分：记忆系统的实际应用场景
# ======================================================================

def demo_memory_in_practice():
    """
    演示如何在实际场景中使用记忆系统。

    一个典型的 Agent 工作流：
    1. 接收任务，放入工作记忆
    2. 从语义网络中检索相关知识
    3. 推理过程中更新工作记忆
    4. 将重要结果存入语义网络
    """
    print("=" * 60)
    print("  第三部分：实际应用场景")
    print("=" * 60)
    print()

    # 初始化两个记忆系统
    wm = WorkingMemory(capacity=10)
    knowledge = SemanticMemoryNetwork()

    print("场景：用户询问关于 Python Web 开发的问题")
    print()

    # ---- 步骤 1: 将用户问题放入工作记忆 ----
    print("[1/4] 用户问题进入工作记忆")
    wm.add(
        content="Python 适合做 Web 后端开发吗？",
        item_type="task",
        priority=MemoryPriority.CRITICAL,
        source="user",
        tags=["question", "python", "web"],
    )

    # ---- 步骤 2: 从知识网络中检索相关信息 ----
    print("\n[2/4] 从语义网络检索相关知识...")

    # 先确保知识库中有相关内容
    python_id = knowledge.add_entity("Python", EntityType.TECHNOLOGY)
    web_id = knowledge.add_entity("WebDevelopment", EntityType.SKILL)
    django_id = knowledge.add_entity("Django", EntityType.TECHNOLOGY)
    flask_id = knowledge.add_entity("Flask", EntityType.TECHNOLOGY)

    knowledge.add_relation(python_id, web_id, RelationType.USED_FOR, 0.85)
    knowledge.add_relation(django_id, python_id, RelationType.IS_A, 0.9)
    knowledge.add_relation(flask_id, python_id, RelationType.IS_A, 0.9)
    knowledge.add_relation(django_id, web_id, RelationType.USED_FOR, 0.95)
    knowledge.add_relation(flask_id, web_id, RelationType.USED_FOR, 0.9)

    # 检索与 Web 开发相关的 Python 技术
    web_related = knowledge.get_related_entities(web_id, max_depth=2)
    print(f"       找到 {len(web_related) - 1} 个相关实体")

    # 将检索到的信息放入工作记忆作为上下文
    for eid in web_related:
        entity = knowledge.get_entity(eid)
        if entity and entity.name != "WebDevelopment":
            wm.add(
                content=f"{entity.name}: {entity.description or '相关技术'}",
                item_type="fact",
                priority=MemoryPriority.MEDIUM,
                source="knowledge_base",
                tags=[entity.entity_type.value],
            )

    print(f"       工作记忆中现在有 {wm.size} 项")

    # ---- 步骤 3: 模拟推理过程 ----
    print("\n[3/4] 推理过程中更新工作记忆...")

    # 添加推理中间结论
    wm.add(
        content="Python 有成熟的 Web 框架（Django, Flask）",
        item_type="thought",
        priority=MemoryPriority.HIGH,
        source="reasoning",
        tags=["conclusion", "framework"],
    )

    wm.add(
        content="Python 适合快速开发和原型验证",
        item_type="thought",
        priority=MemoryPriority.HIGH,
        source="reasoning",
        tags=["conclusion", "advantage"],
    )

    # ---- 步骤 4: 生成最终答案并将新知识存入网络 ----
    print("\n[4/4] 生成答案，更新长期记忆...")

    answer = ("是的，Python 非常适合 Web 后端开发。"
              "它拥有成熟的生态系统，包括 Django、Flask 等流行框架，"
              "开发效率高，社区支持好。")

    print(f"       答案: {answer}")

    # 将新学到的信息存入语义网络（长期记忆）
    conclusion_id = knowledge.add_entity(
        name="Python适合Web开发",
        entity_type=EntityType.CONCEPT,
        description=answer,
        tags=["conclusion", "python", "web"],
    )
    knowledge.add_relation(python_id, conclusion_id, RelationType.RELATED_TO, 0.8)

    print(f"       新结论已存入语义网络 (ID: {conclusion_id[:8]}...)")

    print()
    print("=" * 60)
    print("  记忆系统总结")
    print("=" * 60)
    print("""
  工作记忆 (Working Memory)   - 短期、容量有限、用于活跃思考
  语义网络 (Semantic Network) - 长期、结构化、知识图谱式存储

  典型流程：
    输入 → 工作记忆 → 检索语义网络 → 推理 → 输出
                        ↓
                   重要结果存入语义网络
""")


def main():
    """运行所有记忆系统示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 记忆系统快速入门")
    print("█" * 60)
    print()

    try:
        # 第一部分：工作记忆
        demo_working_memory()

        # 第二部分：语义网络
        demo_semantic_network()

        # 第三部分：实际应用
        demo_memory_in_practice()

        print("✅ 记忆系统示例运行完成！")
        print("   接下来可以查看 multi_agent_team.py 了解多智能体协作。")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
