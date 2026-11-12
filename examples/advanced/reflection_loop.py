"""
反思循环 (Reflection Loop) - LiuHao AI OS 高级示例

本示例演示反思与自我改进机制：
1. 迭代式精炼
2. 自我批评
3. 改进追踪
4. 收敛检测

运行方式：
    python examples/advanced/reflection_loop.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.reasoning import (
    ReflectionEngine,
    ReflectionResult,
    ReflectionEntry,
    ReflectionPhase,
    SelfCritique,
    CritiqueScore,
    CritiqueDimension,
    IterationDelta,
)


# ======================================================================
# 示例 1: 基础反思循环
# ======================================================================

def demo_basic_reflection():
    """
    演示基础的反思循环。

    反思循环的四个阶段：
    1. ACTION (行动) - 生成初始输出
    2. OBSERVATION (观察) - 评估输出质量
    3. REFLECTION (反思) - 分析问题和不足
    4. IMPROVEMENT (改进) - 生成改进后的版本

    这个循环可以迭代多次，直到质量达标或达到迭代上限。
    """
    print("=" * 60)
    print("  示例 1：基础反思循环")
    print("=" * 60)
    print()

    print("任务: 写一段关于'时间管理'的建议文字")
    print()
    print("反思循环过程:")
    print("-" * 50)

    engine = ReflectionEngine(max_iterations=3, quality_threshold=0.8)

    # ---- 第 1 轮：初始版本 ----
    print("\n🔄 第 1 轮")
    print("   [Action] 生成初始版本...")

    initial_version = (
        "时间管理很重要。你应该制定计划，合理安排时间。"
        "要优先处理重要的事情，不要浪费时间。"
        "保持专注，提高效率。"
    )

    entry1 = engine.add_iteration(
        phase=ReflectionPhase.ACTION,
        content=initial_version,
        iteration=1,
    )
    print(f"     初始版本长度: {len(initial_version)} 字符")
    print(f"     内容: {initial_version[:60]}...")

    # 观察评估
    print("   [Observation] 评估质量...")
    critique1 = SelfCritique(
        overall_score=0.5,
        scores=[
            CritiqueScore(dimension=CritiqueDimension.ACCURACY, score=0.6,
                          feedback="内容正确但太笼统"),
            CritiqueScore(dimension=CritiqueDimension.COMPLETENESS, score=0.4,
                          feedback="缺少具体的方法和技巧"),
            CritiqueScore(dimension=CritiqueDimension.CLARITY, score=0.7,
                          feedback="表达清晰但不够生动"),
            CritiqueScore(dimension=CritiqueDimension.RELEVANCE, score=0.8,
                          feedback="主题相关"),
            CritiqueScore(dimension=CritiqueDimension.COHERENCE, score=0.6,
                          feedback="结构松散，缺乏逻辑层次"),
        ],
        summary="初始版本过于笼统，缺乏具体内容和结构化的建议。",
        strengths=["主题明确", "语言简洁"],
        weaknesses=["内容空洞", "缺乏具体方法", "结构不清晰"],
        improvement_suggestions=[
            "添加具体的时间管理方法（如番茄工作法、四象限法）",
            "增加结构化的要点组织",
            "提供可操作的建议而不是空泛的道理",
        ],
    )

    obs_entry1 = engine.add_iteration(
        phase=ReflectionPhase.OBSERVATION,
        content="质量评估完成",
        iteration=1,
        critique=critique1,
    )
    print(f"     整体评分: {critique1.overall_score:.0%}")
    print(f"     主要问题: {critique1.weaknesses[0]}")

    # 反思分析
    print("   [Reflection] 分析改进方向...")
    refl_entry1 = engine.add_iteration(
        phase=ReflectionPhase.REFLECTION,
        content="需要增加具体方法、改善结构、提升实用性",
        iteration=1,
    )
    print(f"     改进方向: 增加具体方法，优化结构")

    # ---- 第 2 轮：改进版本 ----
    print("\n🔄 第 2 轮")
    print("   [Improvement/Action] 生成改进版本...")

    improved_version = (
        "【时间管理的三个实用方法】\n\n"
        "一、四象限法：按'重要'和'紧急'两个维度将任务分为四类：\n"
        "   1. 重要且紧急 - 立即做\n"
        "   2. 重要不紧急 - 计划做（重点关注）\n"
        "   3. 紧急不重要 - 授权做\n"
        "   4. 不紧急不重要 - 减少做\n\n"
        "二、番茄工作法：25分钟专注工作 + 5分钟休息，\n"
        "   每4个番茄钟后休息15-30分钟。\n\n"
        "三、每日三件事：每天早上确定当天最重要的三件事，\n"
        "   优先完成它们，确保核心目标达成。\n\n"
        "记住：时间管理的关键不是做更多事，而是做正确的事。"
    )

    entry2 = engine.add_iteration(
        phase=ReflectionPhase.ACTION,
        content=improved_version,
        iteration=2,
        previous_version=initial_version,
    )
    print(f"     改进版本长度: {len(improved_version)} 字符")
    print(f"     增加了: {len(improved_version) - len(initial_version)} 字符")

    # 第 2 轮评估
    print("   [Observation] 重新评估...")
    critique2 = SelfCritique(
        overall_score=0.75,
        scores=[
            CritiqueScore(dimension=CritiqueDimension.ACCURACY, score=0.8,
                          feedback="方法准确可靠"),
            CritiqueScore(dimension=CritiqueDimension.COMPLETENESS, score=0.7,
                          feedback="涵盖了主要方法，但可以更深入"),
            CritiqueScore(dimension=CritiqueDimension.CLARITY, score=0.85,
                          feedback="结构清晰，易于理解"),
            CritiqueScore(dimension=CritiqueDimension.RELEVANCE, score=0.9,
                          feedback="非常切题"),
            CritiqueScore(dimension=CritiqueDimension.COHERENCE, score=0.8,
                          feedback="逻辑清晰，层次分明"),
        ],
        summary="改进后的版本结构清晰，内容充实，但仍有提升空间。",
        strengths=["结构清晰", "方法具体", "实用性强"],
        weaknesses=["缺少实例", "可以增加常见误区提醒"],
        improvement_suggestions=[
            "增加具体的应用实例",
            "提醒常见的时间管理误区",
            "可以加入工具推荐",
        ],
    )

    obs_entry2 = engine.add_iteration(
        phase=ReflectionPhase.OBSERVATION,
        content="第二轮评估完成",
        iteration=2,
        critique=critique2,
    )
    print(f"     整体评分: {critique2.overall_score:.0%} (提升了 {(critique2.overall_score - critique1.overall_score):.0%})")

    # 计算改进量
    delta = IterationDelta(
        iteration_from=1,
        iteration_to=2,
        score_improvement=critique2.overall_score - critique1.overall_score,
        key_changes=["增加了三种具体方法", "优化了结构层次", "提升了实用性"],
    )
    print(f"     分数提升: {delta.score_improvement:+.0%}")

    # ---- 检查是否收敛 ----
    print()
    has_converged = engine.has_converged(improvement_threshold=0.1)
    print(f"是否收敛: {'是' if has_converged else '否'}")
    print(f"（改进幅度 < 10% 视为收敛）")

    print()
    return engine


# ======================================================================
# 示例 2: 多维度质量评估
# ======================================================================

def demo_multi_dimension_critique():
    """
    演示多维度的自我批评和质量评估。

    评估维度包括：
    - ACCURACY (准确性) - 事实是否正确
    - COMPLETENESS (完整性) - 是否覆盖全面
    - CLARITY (清晰度) - 是否易于理解
    - RELEVANCE (相关性) - 是否切题
    - COHERENCE (连贯性) - 逻辑是否一致
    - ORIGINALITY (原创性) - 是否有新意
    """
    print("=" * 60)
    print("  示例 2：多维度质量评估")
    print("=" * 60)
    print()

    print("对一篇产品介绍文案进行多维度质量评估:")
    print()

    # 待评估的文案版本
    versions = [
        ("V1 (初稿)",
         "我们的产品很好用，功能很多，价格也不贵，大家都应该买来试试。",
         ),
        ("V2 (改进版)",
         "【SmartTask - 智能任务管理工具】\n\n"
         "SmartTask 是一款基于 AI 的智能任务管理工具，\n"
         "帮助您高效管理日常工作。\n\n"
         "核心功能：\n"
         "• 智能优先级排序 - AI 自动分析任务重要性\n"
         "• 时间预估 - 基于历史数据预测完成时间\n"
         "• 团队协作 - 实时同步，高效协作\n\n"
         "立即体验，提升您的工作效率！"),
    ]

    engine = ReflectionEngine()

    for version_name, content in versions:
        print(f"\n{'─' * 50}")
        print(f"  {version_name}")
        print(f"{'─' * 50}")
        print(f"  内容预览: {content[:60]}...")

        # 模拟各维度评分
        if "V1" in version_name:
            critique = SelfCritique(
                overall_score=0.35,
                scores=[
                    CritiqueScore(CritiqueDimension.ACCURACY, 0.4,
                                  "描述太模糊，缺乏具体信息"),
                    CritiqueScore(CritiqueDimension.COMPLETENESS, 0.2,
                                  "缺少产品名称、功能、价格等关键信息"),
                    CritiqueScore(CritiqueDimension.CLARITY, 0.5,
                                  "表达清楚但信息量不足"),
                    CritiqueScore(CritiqueDimension.RELEVANCE, 0.7,
                                  "主题相关但不够聚焦"),
                    CritiqueScore(CritiqueDimension.COHERENCE, 0.4,
                                  "缺乏结构和逻辑层次"),
                    CritiqueScore(CritiqueDimension.ORIGINALITY, 0.2,
                                  "过于空泛，没有差异化"),
                ],
                summary="初稿过于简单，缺乏产品的核心信息和说服力。",
                strengths=["语言简洁"],
                weaknesses=["信息不足", "没有卖点", "缺乏说服力", "没有结构"],
                improvement_suggestions=[],
            )
        else:
            critique = SelfCritique(
                overall_score=0.82,
                scores=[
                    CritiqueScore(CritiqueDimension.ACCURACY, 0.85,
                                  "描述准确，信息具体"),
                    CritiqueScore(CritiqueDimension.COMPLETENESS, 0.75,
                                  "涵盖了主要功能和卖点，但缺少价格和案例"),
                    CritiqueScore(CritiqueDimension.CLARITY, 0.9,
                                  "结构清晰，重点突出"),
                    CritiqueScore(CritiqueDimension.RELEVANCE, 0.95,
                                  "紧密围绕产品介绍"),
                    CritiqueScore(CritiqueDimension.COHERENCE, 0.85,
                                  "逻辑清晰，层次分明"),
                    CritiqueScore(CritiqueDimension.ORIGINALITY, 0.7,
                                  "有一定特色但可以更突出差异化"),
                ],
                summary="改进版结构清晰，信息充实，是一篇合格的产品介绍。",
                strengths=["结构清晰", "信息具体", "重点突出", "有说服力"],
                weaknesses=["缺少具体价格", "没有用户案例", "可以增加CTA"],
                improvement_suggestions=[],
            )

        # 可视化雷达图（文本版）
        print(f"\n  多维度评分:")
        for score in critique.scores:
            bar_len = int(score.score * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            print(f"    {score.dimension.value:15s} [{bar}] {score.score:.0%}")
            print(f"      {score.feedback}")

        print(f"\n  整体评分: {critique.overall_score:.0%}")
        print(f"  评价: {critique.summary}")

        print(f"\n  ✅ 优点: {', '.join(critique.strengths)}")
        print(f"  ❌ 不足: {', '.join(critique.weaknesses)}")

    print()


# ======================================================================
# 示例 3: 迭代精炼与收敛检测
# ======================================================================

def demo_iterative_refinement():
    """
    演示迭代式精炼和收敛检测。

    当连续迭代的改进幅度小于阈值时，
    认为已经收敛，可以停止迭代。
    """
    print("=" * 60)
    print("  示例 3：迭代精炼与收敛检测")
    print("=" * 60)
    print()

    print("场景: 迭代优化一段代码，直到质量收敛")
    print()

    engine = ReflectionEngine(max_iterations=5, quality_threshold=0.9)

    # 模拟 4 轮迭代的分数变化
    iteration_scores = [0.45, 0.7, 0.85, 0.90, 0.91]
    iteration_descriptions = [
        "初始版本 - 功能实现但代码粗糙",
        "第2轮 - 添加注释，重构函数",
        "第3轮 - 优化性能，添加错误处理",
        "第4轮 - 完善测试，优化边界情况",
        "第5轮 - 微调细节，代码审查修复",
    ]

    print("迭代过程:")
    print("-" * 50)

    prev_score = 0
    for i, (score, desc) in enumerate(zip(iteration_scores, iteration_descriptions), 1):
        improvement = score - prev_score
        bar_len = int(score * 40)
        bar = "█" * bar_len + "░" * (40 - bar_len)

        print(f"\n  第 {i} 轮: [{bar}] {score:.0%}")
        print(f"         {desc}")

        if i > 1:
            if improvement > 0.1:
                trend = "🚀 大幅提升"
            elif improvement > 0.05:
                trend = "📈 明显改进"
            elif improvement > 0.01:
                trend = "📊 小幅优化"
            else:
                trend = "➡️  基本持平"
            print(f"         改进: {improvement:+.0%} {trend}")

        # 记录到引擎
        critique = SelfCritique(
            overall_score=score,
            scores=[CritiqueScore(CritiqueDimension.ACCURACY, score, "")],
            summary=desc,
            strengths=[],
            weaknesses=[],
        )
        engine.add_iteration(
            phase=ReflectionPhase.OBSERVATION,
            content=desc,
            iteration=i,
            critique=critique,
        )

        prev_score = score

    # 检测收敛
    print()
    print("-" * 50)
    print("收敛分析:")

    # 检查最近几轮的改进幅度
    recent_improvements = []
    for i in range(1, len(iteration_scores)):
        recent_improvements.append(iteration_scores[i] - iteration_scores[i-1])

    print(f"  各轮改进: {[f'{v:+.0%}' for v in recent_improvements]}")

    # 最后两轮的改进
    if len(recent_improvements) >= 2:
        last_improvement = recent_improvements[-1]
        threshold = 0.05  # 5% 阈值
        is_converging = last_improvement < threshold

        print(f"  最后一轮改进: {last_improvement:+.0%}")
        print(f"  收敛阈值: {threshold:.0%}")
        print(f"  是否收敛: {'✅ 是' if is_converging else '❌ 否'}")

        if is_converging:
            print()
            print("  💡 收敛意味着：")
            print("     • 继续迭代的边际收益递减")
            print("     • 已经接近最优解")
            print("     • 可以停止迭代，投入产出比最高")

    # 质量趋势图（文本版）
    print()
    print("📈 质量趋势:")
    max_score = max(iteration_scores)
    for i, score in enumerate(iteration_scores, 1):
        height = int(score / max_score * 10)
        line = "  " + " " * (10 - height) + "█" * height
        print(f"  {i}轮 {line} {score:.0%}")

    print()
    return engine


# ======================================================================
# 示例 4: 反思日志
# ======================================================================

def demo_reflection_journal():
    """
    演示反思日志功能。

    每次反思都会被记录下来，形成反思日志，
    可以用于：
    - 追踪改进历程
    - 总结经验教训
    - 发现系统性问题
    """
    print("=" * 60)
    print("  示例 4：反思日志与经验积累")
    print("=" * 60)
    print()

    engine = ReflectionEngine()

    # 模拟多次反思记录
    print("添加反思记录...")

    reflections = [
        {
            "task": "撰写产品方案",
            "lesson": "在动手写之前，应该先列大纲，避免返工。",
            "category": "工作方法",
            "impact": "high",
        },
        {
            "task": "代码审查",
            "lesson": "命名规范很重要，清晰的命名胜过冗长的注释。",
            "category": "编码规范",
            "impact": "medium",
        },
        {
            "task": "数据分析报告",
            "lesson": "报告的开头应该先给结论，再展开细节。",
            "category": "沟通表达",
            "impact": "high",
        },
        {
            "task": "客户沟通",
            "lesson": "倾听比表达更重要，先理解需求再给出方案。",
            "category": "沟通技巧",
            "impact": "high",
        },
        {
            "task": "项目排期",
            lesson": "要给风险预留缓冲时间，乐观估计往往会延期。",
            "category": "项目管理",
            "impact": "high",
        },
    ]

    for i, ref in enumerate(reflections, 1):
        entry = ReflectionEntry(
            entry_id=f"ref-{i:03d}",
            task_name=ref["task"],
            phase=ReflectionPhase.REFLECTION,
            content=ref["lesson"],
            iteration=i,
            metadata={
                "category": ref["category"],
                "impact": ref["impact"],
            },
        )
        engine.reflection_journal.append(entry)
        impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(ref["impact"], "⚪")
        print(f"  {impact_icon} [{ref['category']}] {ref['task']}: {ref['lesson'][:40]}...")

    print()
    print(f"反思日志总数: {len(engine.reflection_journal)} 条")

    # 按类别统计
    categories = {}
    for entry in engine.reflection_journal:
        cat = entry.metadata.get("category", "其他")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"类别分布:")
    for cat, count in categories.items():
        print(f"  • {cat}: {count} 条")

    # 按影响程度统计
    high_impact = sum(
        1 for e in engine.reflection_journal
        if e.metadata.get("impact") == "high"
    )
    print(f"高影响经验: {high_impact} 条")

    print()
    print("💡 反思日志的价值:")
    print("   1. 避免重复犯同样的错误")
    print("   2. 积累可复用的经验")
    print("   3. 发现系统性的改进方向")
    print("   4. 衡量成长和进步")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有反思循环示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 反思循环与自我改进")
    print("█" * 60)

    try:
        # 示例 1: 基础反思循环
        demo_basic_reflection()

        # 示例 2: 多维度评估
        demo_multi_dimension_critique()

        # 示例 3: 迭代精炼与收敛
        demo_iterative_refinement()

        # 示例 4: 反思日志
        demo_reflection_journal()

        print("=" * 60)
        print("  反思循环功能总结")
        print("=" * 60)
        print("""
  ✓ 四阶段反思循环 (Action-Observation-Reflection-Improvement)
  ✓ 六维度质量评估
  ✓ 迭代式质量改进
  ✓ 自动收敛检测
  ✓ 改进幅度量化追踪
  ✓ 反思日志与经验积累
  ✓ 多版本对比分析
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
