"""
黑板协作 - LiuHao AI OS 高级示例

本示例演示黑板架构模式的多智能体协作：
1. 多个 Agent 共享黑板
2. 机会式问题求解
3. 条目发布与读取
4. 投票与订阅机制

运行方式：
    python examples/advanced/blackboard_collaboration.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional, Set, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.blackboard import (
    Blackboard,
    BlackboardEntry,
    EntryType,
    ConfidenceLevel,
)


# ======================================================================
# 示例 1: 黑板基础操作
# ======================================================================

def demo_basic_blackboard():
    """
    演示黑板的基本操作：发布、读取、搜索、投票。

    黑板模式的核心思想：
    - 所有 Agent 共享一个公共的"黑板"
    - 任何 Agent 都可以在黑板上写下发现
    - 任何 Agent 都可以读取黑板上的内容
    - Agent 之间不需要直接通信
    - 谁能贡献就贡献（机会式问题求解）
    """
    print("=" * 60)
    print("  示例 1：黑板基础操作")
    print("=" * 60)
    print()

    # 创建黑板
    board = Blackboard(name="问题求解黑板")
    print(f"创建黑板: {board.name}")
    print()

    # ---- 发布条目 ----
    print("[1/5] 各 Agent 发布发现到黑板...")
    print()

    # Agent 1: 发布一个事实
    entry1_id = board.post(
        author="researcher_1",
        topic="市场规模",
        content="全球 AI 市场规模约为 1000 亿美元",
        entry_type=EntryType.FACT,
        confidence=ConfidenceLevel.HIGH,
        tags=["市场", "AI", "规模"],
    )
    print(f"  📝 researcher_1 发布事实: 市场规模数据")
    print(f"     (ID: {entry1_id[:12]}...)")

    # Agent 2: 发布一个假设
    entry2_id = board.post(
        author="analyst_1",
        topic="增长预测",
        content="预计 AI 市场年增长率约为 20%",
        entry_type=EntryType.HYPOTHESIS,
        confidence=ConfidenceLevel.MEDIUM,
        tags=["预测", "增长", "AI"],
    )
    print(f"  💭 analyst_1 发布假设: 年增长率 20%")

    # Agent 3: 发布一个问题
    entry3_id = board.post(
        author="manager_1",
        topic="市场驱动因素",
        content="哪些因素是 AI 市场增长的主要驱动力？",
        entry_type=EntryType.QUESTION,
        confidence=ConfidenceLevel.CERTAIN,  # 问题本身是确定的
        tags=["驱动因素", "市场", "分析"],
    )
    print(f"  ❓ manager_1 发布问题: 增长驱动因素")

    # Agent 4: 发布一个结果
    entry4_id = board.post(
        author="researcher_2",
        topic="驱动因素",
        content="主要驱动因素包括：大模型技术进步、企业数字化转型、政策支持",
        entry_type=EntryType.RESULT,
        confidence=ConfidenceLevel.HIGH,
        tags=["驱动因素", "技术", "政策"],
        references=[entry3_id],  # 引用问题条目
    )
    print(f"  ✅ researcher_2 发布结果: 驱动因素分析")
    print(f"     (引用了问题条目)")

    # Agent 5: 发布一个决策
    entry5_id = board.post(
        author="manager_1",
        topic="行动方案",
        content="决定投入资源进入 AI 市场，重点关注企业级应用",
        entry_type=EntryType.DECISION,
        confidence=ConfidenceLevel.HIGH,
        tags=["决策", "战略", "AI"],
    )
    print(f"  🎯 manager_1 发布决策: 进入 AI 市场")

    print()
    print(f"  黑板上共有 {board.entry_count} 条记录")

    # ---- 读取条目 ----
    print("\n[2/5] 读取黑板内容...")
    print()

    # 按主题获取
    market_entries = board.get_by_topic("市场规模")
    print(f"  主题'市场规模'的条目: {len(market_entries)} 个")
    for entry in market_entries:
        print(f"    • [{entry.entry_type.value}] {entry.author}: {entry.content[:40]}...")

    # 按类型获取
    hypothesis_entries = board.get_by_type(EntryType.HYPOTHESIS)
    print(f"\n  假设类条目: {len(hypothesis_entries)} 个")
    for entry in hypothesis_entries:
        print(f"    • {entry.author}: {entry.content[:40]}...")

    # 按作者获取
    researcher_entries = board.get_by_author("researcher_1")
    print(f"\n  researcher_1 发布的条目: {len(researcher_entries)} 个")

    # ---- 搜索 ----
    print("\n[3/5] 搜索黑板内容...")
    print()

    # 按标签搜索
    ai_tag_entries = board.search_by_tag("AI")
    print(f"  搜索标签 'AI': 找到 {len(ai_tag_entries)} 条")
    for entry in ai_tag_entries[:3]:
        print(f"    • {entry.topic}: {entry.content[:30]}...")

    # 全文搜索
    growth_entries = board.search("增长")
    print(f"\n  搜索关键词 '增长': 找到 {len(growth_entries)} 条")
    for entry in growth_entries:
        print(f"    • {entry.author}: {entry.content[:40]}...")

    # ---- 投票 ----
    print("\n[4/5] 投票机制...")
    print()

    # 多个 Agent 对同一个条目标记有用/无用
    board.upvote(entry4_id, "analyst_1")
    board.upvote(entry4_id, "manager_1")
    board.upvote(entry4_id, "researcher_1")
    board.downvote(entry4_id, "critic_1")

    entry4 = board.get_entry(entry4_id)
    if entry4:
        print(f"  条目'驱动因素分析'的投票情况:")
        print(f"    👍 有用: {entry4.upvotes}")
        print(f"    👎 无用: {entry4.downvotes}")
        print(f"    📊 净得分: {entry4.score}")

    # ---- 按分数排序 ----
    print("\n[5/5] 按质量排序...")
    print()

    top_entries = board.get_top_entries(min_score=0, limit=3)
    print(f"  Top 3 条目:")
    for i, entry in enumerate(top_entries, 1):
        print(f"    {i}. [{entry.score:+d}] {entry.topic} "
              f"(by {entry.author})")

    print()


# ======================================================================
# 示例 2: 订阅与通知
# ======================================================================

def demo_subscriptions():
    """
    演示黑板的订阅机制。

    Agent 可以订阅特定主题，
    当有新条目发布到该主题时会收到通知。
    """
    print("=" * 60)
    print("  示例 2：订阅与通知机制")
    print("=" * 60)
    print()

    board = Blackboard(name="协作黑板")

    # 记录通知
    notifications = []

    def on_new_entry(entry: BlackboardEntry):
        """新条目回调。"""
        notifications.append(("new", entry))

    # 订阅特定主题
    print("Agent 订阅感兴趣的主题:")
    print()

    # 方式一：回调函数订阅
    board.subscribe("市场分析", "analyst_1", on_new_entry)
    print("  📩 analyst_1 订阅了 '市场分析' 主题")

    board.subscribe("技术评估", "engineer_1", on_new_entry)
    print("  📩 engineer_1 订阅了 '技术评估' 主题")

    board.subscribe("决策", "manager_1", on_new_entry)
    print("  📩 manager_1 订阅了 '决策' 主题")

    print()

    # 发布一些条目
    print("发布新条目...")
    print()

    board.post("researcher_1", "市场分析", "市场规模持续增长",
               entry_type=EntryType.FACT, confidence=ConfidenceLevel.HIGH)
    print(f"  📝 researcher_1 → 市场分析: 市场规模持续增长")

    board.post("engineer_1", "技术评估", "技术方案可行",
               entry_type=EntryType.RESULT, confidence=ConfidenceLevel.HIGH)
    print(f"  📝 engineer_1 → 技术评估: 技术方案可行")

    board.post("analyst_1", "市场分析", "竞争格局分析完成",
               entry_type=EntryType.RESULT, confidence=ConfidenceLevel.MEDIUM)
    print(f"  📝 analyst_1 → 市场分析: 竞争格局分析完成")

    board.post("manager_1", "决策", "决定推进项目",
               entry_type=EntryType.DECISION, confidence=ConfidenceLevel.HIGH)
    print(f"  📝 manager_1 → 决策: 决定推进项目")

    print()
    print(f"共收到 {len(notifications)} 条通知")
    print()

    # 按订阅者统计
    # （实际实现中会有更完善的订阅管理，这里简化演示）
    print("💡 订阅机制的优势:")
    print("   • 解耦：发布者不需要知道谁在关注")
    print("   • 高效：只接收感兴趣的更新")
    print("   • 实时：新内容立即推送到订阅者")
    print("   • 灵活：可以随时订阅/取消订阅")

    print()


# ======================================================================
# 示例 3: 机会式问题求解
# ======================================================================

def demo_opportunistic_problem_solving():
    """
    演示黑板模式下的机会式问题求解。

    经典的黑板架构应用：
    多个专家（Agent）观察黑板，
    当自己能贡献时就写下内容，
    直到问题被解决。

    场景：产品故障排查
    """
    print("=" * 60)
    print("  示例 3：机会式问题求解（故障排查场景）")
    print("=" * 60)
    print()

    board = Blackboard(name="故障排查黑板")

    print("场景：产品出现性能下降问题，多角色协作排查")
    print()

    # ---- 阶段 1: 问题提出 ----
    print("阶段 1: 问题上报")
    print("-" * 50)

    board.post(
        author="监控系统",
        topic="性能问题",
        content="系统响应时间从 100ms 增加到 500ms，持续 30 分钟",
        entry_type=EntryType.PROGRESS,
        confidence=ConfidenceLevel.CERTAIN,
        tags=["性能", "告警", "监控"],
    )
    print(f"  🚨 监控系统: 报告性能下降告警")

    board.post(
        author="运维工程师",
        topic="初步观察",
        content="CPU 使用率正常（30%），内存正常，网络流量正常",
        entry_type=EntryType.FACT,
        confidence=ConfidenceLevel.HIGH,
        tags=["运维", "资源", "排查"],
    )
    print(f"  🔍 运维工程师: 基础资源正常")

    print()

    # ---- 阶段 2: 各方贡献 ----
    print("阶段 2: 各方专家贡献分析")
    print("-" * 50)

    # 数据库专家
    board.post(
        author="DBA",
        topic="数据库分析",
        content="发现慢查询增多，某条 SQL 执行时间从 10ms 增加到 200ms",
        entry_type=EntryType.FACT,
        confidence=ConfidenceLevel.HIGH,
        tags=["数据库", "慢查询", "SQL"],
    )
    print(f"  💾 DBA: 发现慢查询")

    # 后端工程师
    board.post(
        author="后端工程师",
        topic="代码分析",
        content="最近上线了新功能，涉及到那条慢查询对应的接口",
        entry_type=EntryType.HYPOTHESIS,
        confidence=ConfidenceLevel.MEDIUM,
        tags=["代码", "发布", "回归"],
    )
    print(f"  💻 后端工程师: 怀疑与新功能上线有关")

    # QA 工程师
    board.post(
        author="QA工程师",
        topic="测试验证",
        content="确认新功能在测试环境没有做性能测试",
        entry_type=EntryType.FACT,
        confidence=ConfidenceLevel.HIGH,
        tags=["测试", "质量", "性能"],
    )
    print(f"  🧪 QA工程师: 确认缺少性能测试")

    print()

    # ---- 阶段 3: 定位根因 ----
    print("阶段 3: 定位根本原因")
    print("-" * 50)

    # 架构师综合分析
    board.post(
        author="架构师",
        topic="根因分析",
        content="根因确定：新功能引入的 SQL 查询缺少索引，"
                "导致全表扫描。数据量增长后性能急剧下降。",
        entry_type=EntryType.RESULT,
        confidence=ConfidenceLevel.HIGH,
        tags=["根因", "索引", "性能"],
    )
    print(f"  🏗️  架构师: 确定根因是缺少索引")

    # 投票确认
    entry_id = board.get_by_topic("根因分析")[0].id
    board.upvote(entry_id, "DBA")
    board.upvote(entry_id, "后端工程师")
    board.upvote(entry_id, "运维工程师")
    print(f"  👍 3 人投票确认根因分析")

    print()

    # ---- 阶段 4: 解决方案 ----
    print("阶段 4: 制定解决方案")
    print("-" * 50)

    board.post(
        author="DBA",
        topic="解决方案",
        content="方案：添加适当的索引。预计可将查询时间降回 10ms 以内。"
                "风险：低。执行时间：约 5 分钟。",
        entry_type=EntryType.RESULT,
        confidence=ConfidenceLevel.HIGH,
        tags=["解决方案", "索引", "优化"],
    )
    print(f"  💾 DBA: 提出加索引方案")

    board.post(
        author="运维工程师",
        topic="执行计划",
        content="建议在低峰期执行，先在备库验证，再操作主库。",
        entry_type=EntryType.CONSTRAINT,
        confidence=ConfidenceLevel.HIGH,
        tags=["执行", "安全", "运维"],
    )
    print(f"  🔧 运维工程师: 补充执行注意事项")

    board.post(
        author="技术负责人",
        topic="决策",
        content="批准方案，今晚 02:00 执行变更。DBA 操作，运维配合监控。",
        entry_type=EntryType.DECISION,
        confidence=ConfidenceLevel.HIGH,
        tags=["决策", "变更", "批准"],
    )
    print(f"  🎯 技术负责人: 批准方案，安排执行")

    print()

    # ---- 总结 ----
    print("=" * 50)
    print("  问题解决总结")
    print("=" * 50)
    print(f"  总条目数: {board.entry_count}")
    print(f"  参与角色: {len(board.get_all_authors())} 个")
    print(f"  解决时长: 4 个阶段")
    print()
    print("  参与角色:")
    for author in sorted(board.get_all_authors()):
        count = len(board.get_by_author(author))
        print(f"    • {author}: {count} 条贡献")

    print()
    print("💡 黑板协作的优势:")
    print("   1. 异步协作 - 不需要所有人同时在线")
    print("   2. 信息共享 - 所有发现都对所有人可见")
    print("   3. 灵活参与 - 谁能贡献就贡献")
    print("   4. 可追溯性 - 完整记录思考过程")
    print("   5. 质量评估 - 通过投票筛选优质内容")

    print()


# ======================================================================
# 示例 4: 版本控制与编辑
# ======================================================================

def demo_versioning_and_editing():
    """
    演示黑板条目的版本控制和协作编辑。
    """
    print("=" * 60)
    print("  示例 4：版本控制与协作编辑")
    print("=" * 60)
    print()

    board = Blackboard(name="文档协作黑板")

    # 初始版本
    entry_id = board.post(
        author="author_1",
        topic="项目计划",
        content="项目计划 V1：完成需求分析",
        entry_type=EntryType.RESULT,
        confidence=ConfidenceLevel.MEDIUM,
        tags=["计划", "项目"],
    )
    entry = board.get_entry(entry_id)
    print(f"初始版本 (v{entry.version}): {entry.content}")
    print(f"  作者: {entry.author}")

    # 其他人编辑
    board.edit_entry(
        entry_id=entry_id,
        editor="author_2",
        new_content="项目计划 V2：完成需求分析 + 技术方案设计",
        change_note="添加技术方案设计阶段",
    )
    entry = board.get_entry(entry_id)
    print(f"\n编辑后 (v{entry.version}): {entry.content}")
    print(f"  编辑者: {', '.join(entry.edited_by)}")

    # 再次编辑
    board.edit_entry(
        entry_id=entry_id,
        editor="author_3",
        new_content="项目计划 V3：完成需求分析 + 技术方案设计 + 测试计划",
        change_note="添加测试计划阶段",
    )
    entry = board.get_entry(entry_id)
    print(f"\n再次编辑 (v{entry.version}): {entry.content}")
    print(f"  所有编辑者: {', '.join(entry.edited_by)}")

    print()
    print("💡 协作编辑特性:")
    print("   • 版本追踪 - 每次编辑都增加版本号")
    print("   • 编辑记录 - 记录所有修改过的人")
    print("   • 修改说明 - 每次编辑都有变更说明")
    print("   • 多人协作 - 多人可以共同完善一个条目")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有黑板协作示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 黑板协作模式")
    print("█" * 60)

    try:
        # 示例 1: 基础操作
        demo_basic_blackboard()

        # 示例 2: 订阅通知
        demo_subscriptions()

        # 示例 3: 机会式问题求解
        demo_opportunistic_problem_solving()

        # 示例 4: 版本控制
        demo_versioning_and_editing()

        print("=" * 60)
        print("  黑板协作功能总结")
        print("=" * 60)
        print("""
  ✓ 共享知识空间
  ✓ 多种条目类型（事实/假设/结果/问题/决策等）
  ✓ 置信度分级
  ✓ 标签与引用
  ✓ 投票与评分
  ✓ 主题订阅
  ✓ 版本控制
  ✓ 机会式问题求解
  ✓ 多 Agent 异步协作
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
