"""
链式思维 (Chain of Thought) - LiuHao AI OS 高级示例

本示例演示高级链式思维推理：
1. 逐步推理过程
2. 自我纠正（矛盾检测）
3. 置信度追踪
4. 不同推理策略（分析型、创造型、批判型）

运行方式：
    python examples/advanced/chain_of_thought.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.reasoning import (
    ChainOfThought,
    ChainOfThoughtResult,
    ReasoningStep,
    ReasoningStrategy,
    StepType,
    Contradiction,
)
from ai.employee import Agent
from ai.providers import get_provider


# ======================================================================
# 示例 1: 基础链式思维
# ======================================================================

def demo_basic_cot(problem: str = "一个水池有两个进水管和一个出水管。"):
    """
    演示基础的链式思维推理。

    链式思维 (CoT) 的核心思想是：
    不是直接给出答案，而是一步一步地推理，
    每一步都建立在前一步的基础上。

    这就像人类做数学题：先读题，再找已知条件，
    然后选择方法，逐步计算，最后验证答案。
    """
    print("=" * 60)
    print("  示例 1：基础链式思维推理")
    print("=" * 60)
    print()

    # 创建 CoT 引擎
    # strategy 参数决定推理风格：
    #   ANALYTICAL - 分析型：逻辑严密，逐步推导
    #   CREATIVE   - 创造型：发散思维，多方案探索
    #   CRITICAL   - 批判型：质疑假设，寻找漏洞
    cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)

    print(f"推理策略: {cot.strategy.value}")
    print(f"问题: {problem}")
    print()
    print("推理过程:")
    print("-" * 50)

    # ---- 手动构建推理链 ----
    # 步骤 1: 明确问题（前提）
    step1 = cot.add_step(
        step_type=StepType.PREMISE,
        content="水池中有两个进水管（A管和B管）和一个出水管（C管）。",
        confidence=1.0,
        justification="题目给出的已知条件。",
    )
    print(f"  步骤 1 [{step1.step_type.value}]: {step1.content[:50]}...")
    print(f"         置信度: {step1.confidence:.0%}")

    # 步骤 2: 添加更多前提
    step2 = cot.add_step(
        step_type=StepType.PREMISE,
        content="A管单独注水需要6小时，B管单独注水需要8小时，C管单独放水需要12小时。",
        confidence=1.0,
        justification="题目给出的已知条件。",
        premises=[step1.step_id],
    )
    print(f"  步骤 2 [{step2.step_type.value}]: {step2.content[:50]}...")
    print(f"         置信度: {step2.confidence:.0%}")

    # 步骤 3: 推理 - 计算每管的效率
    step3 = cot.add_step(
        step_type=StepType.INFERENCE,
        content="A管效率为1/6每小时，B管效率为1/8每小时，C管效率为-1/12每小时（放水）。",
        confidence=0.95,
        justification="将整池水看作单位1，效率 = 1 / 时间。",
        premises=[step2.step_id],
    )
    print(f"  步骤 3 [{step3.step_type.value}]: {step3.content[:50]}...")
    print(f"         置信度: {step3.confidence:.0%}")
    print(f"         依据: {step3.justification}")

    # 步骤 4: 推理 - 计算总效率
    step4 = cot.add_step(
        step_type=StepType.INFERENCE,
        content="三管同时开的总效率 = 1/6 + 1/8 - 1/12 = 4/24 + 3/24 - 2/24 = 5/24 每小时。",
        confidence=0.9,
        justification="进水管效率相加，出水管效率相减。",
        premises=[step3.step_id],
    )
    print(f"  步骤 4 [{step4.step_type.value}]: {step4.content[:50]}...")
    print(f"         置信度: {step4.confidence:.0%}")

    # 步骤 5: 中间结论
    step5 = cot.add_step(
        step_type=StepType.SUB_CONCLUSION,
        content="因此，总效率为5/24池每小时。",
        confidence=0.9,
        justification="通分计算结果正确。",
        premises=[step4.step_id],
    )
    print(f"  步骤 5 [{step5.step_type.value}]: {step5.content[:50]}...")

    # 步骤 6: 最终结论
    step6 = cot.add_step(
        step_type=StepType.FINAL_CONCLUSION,
        content="注满一池水需要 24/5 = 4.8 小时，即4小时48分钟。",
        confidence=0.85,
        justification="时间 = 总量 / 效率 = 1 / (5/24) = 24/5 = 4.8小时。",
        premises=[step5.step_id],
    )
    print(f"  步骤 6 [{step6.step_type.value}]: {step6.content}")
    print(f"         置信度: {step6.confidence:.0%}")

    # ---- 获取推理结果 ----
    result = cot.get_result()

    print()
    print("-" * 50)
    print(f"推理总步数: {result.total_steps}")
    print(f"整体置信度: {result.overall_confidence:.1%}")
    print(f"推理结论: {result.final_answer[:60]}...")

    print()
    return cot, result


# ======================================================================
# 示例 2: 自我纠正（矛盾检测）
# ======================================================================

def demo_self_correction():
    """
    演示链式思维的自我纠正功能。

    CoT 引擎可以检测推理链中的矛盾：
    - 如果后一步与前一步矛盾，会标记出来
    - 可以触发修正步骤
    - 这模拟了人类"啊，不对，应该是..."的思考过程
    """
    print("=" * 60)
    print("  示例 2：自我纠正与矛盾检测")
    print("=" * 60)
    print()

    cot = ChainOfThought(strategy=ReasoningStrategy.CRITICAL)

    print("问题: 一个班有30个学生，男生比女生多6人，问男生有多少人？")
    print()
    print("推理过程 (包含一个错误):")
    print("-" * 50)

    # 步骤 1: 前提
    s1 = cot.add_step(
        step_type=StepType.PREMISE,
        content="全班共30人，男生比女生多6人。",
        confidence=1.0,
    )
    print(f"  步骤 1: {s1.content}")

    # 步骤 2: 错误的推理（故意设置一个错误）
    s2 = cot.add_step(
        step_type=StepType.INFERENCE,
        content="假设女生有x人，那么男生有x-6人。",
        confidence=0.8,
        justification="男生比女生多6人，所以男生 = 女生 - 6。",
        premises=[s1.step_id],
    )
    print(f"  步骤 2: {s2.content}")
    print(f"         ⚠️  这里有个错误！")

    # 步骤 3: 继续推理
    s3 = cot.add_step(
        step_type=StepType.INFERENCE,
        content="总人数 = x + (x-6) = 2x - 6 = 30，所以 2x = 24，x = 12。",
        confidence=0.7,
        premises=[s2.step_id],
    )
    print(f"  步骤 3: {s3.content}")

    # 步骤 4: 得出错误结论
    s4 = cot.add_step(
        step_type=StepType.FINAL_CONCLUSION,
        content="因此女生有12人，男生有6人。",
        confidence=0.6,
        premises=[s3.step_id],
    )
    print(f"  步骤 4: {s4.content}")

    # ---- 检测矛盾 ----
    print()
    print("🔍 检测矛盾...")
    contradictions = cot.detect_contradictions()

    if contradictions:
        for c in contradictions:
            print(f"   发现矛盾: {c.description}")
            print(f"   涉及步骤: {c.step_ids}")
            print(f"   严重程度: {c.severity}")
    else:
        print("   未检测到明显矛盾")

    # ---- 自我修正 ----
    print()
    print("🔧 自我修正...")

    # 找到错误并修正
    correction = cot.add_step(
        step_type=StepType.CORRECTION,
        content="纠正：男生比女生多6人，所以男生人数 = 女生人数 + 6，而不是减6。",
        confidence=0.95,
        justification="对'多'和'少'的方向理解错误，需要纠正。",
        premises=[s1.step_id],
        corrects=s2.step_id,  # 标记这是对步骤2的纠正
    )
    print(f"  修正步骤: {correction.content}")
    print(f"         纠正的步骤: {correction.metadata.get('corrects', 'N/A')}")

    # 重新推理
    s5 = cot.add_step(
        step_type=StepType.INFERENCE,
        content="设女生x人，男生x+6人。x + (x+6) = 30，2x + 6 = 30，2x = 24，x = 12。",
        confidence=0.95,
        justification="基于修正后的假设重新计算。",
        premises=[correction.step_id],
    )
    print(f"  新步骤 5: {s5.content}")

    s6 = cot.add_step(
        step_type=StepType.FINAL_CONCLUSION,
        content="正确答案：女生12人，男生18人。验证：12+18=30，18-12=6，符合条件。",
        confidence=0.95,
        justification="验证：总人数正确，差值正确。",
        premises=[s5.step_id],
    )
    print(f"  新步骤 6: {s6.content}")

    result = cot.get_result()
    print()
    print(f"最终答案: {result.final_answer}")
    print(f"整体置信度: {result.overall_confidence:.1%}")
    print(f"修正后置信度提升了，因为纠正了错误。")

    print()
    return cot, contradictions


# ======================================================================
# 示例 3: 不同推理策略对比
# ======================================================================

def demo_reasoning_strategies():
    """
    演示不同推理策略的区别。

    同样的问题，使用不同的推理策略会得到不同风格的思考过程：
    - ANALYTICAL (分析型): 逻辑严密，逐步推导
    - CREATIVE (创造型): 发散思维，多种可能
    - CRITICAL (批判型): 质疑假设，寻找漏洞
    """
    print("=" * 60)
    print("  示例 3：不同推理策略对比")
    print("=" * 60)
    print()

    problem = "如何提高城市的公共交通使用率？"

    strategies = [
        (ReasoningStrategy.ANALYTICAL, "分析型", "系统分解问题，分析各因素影响"),
        (ReasoningStrategy.CREATIVE, "创造型", "发散思维，提出多种创新方案"),
        (ReasoningStrategy.CRITICAL, "批判型", "评估方案可行性，指出潜在问题"),
    ]

    for strategy, name, description in strategies:
        print(f"\n{'─' * 50}")
        print(f"  策略: {name} ({strategy.value})")
        print(f"  特点: {description}")
        print(f"{'─' * 50}")

        cot = ChainOfThought(strategy=strategy)

        if strategy == ReasoningStrategy.ANALYTICAL:
            # 分析型：分解问题，分析因素
            s1 = cot.add_step(StepType.PREMISE, "问题是提高公共交通使用率。", 1.0, "")
            s2 = cot.add_step(StepType.INFERENCE,
                "影响因素包括：票价、便利性、舒适度、准时性、覆盖率。",
                0.9, "从供需两方面分析影响因素。", [s1.step_id])
            s3 = cot.add_step(StepType.INFERENCE,
                "可以从降低成本、提高服务质量、增加覆盖三个方向入手。",
                0.85, "基于影响因素推导解决方案方向。", [s2.step_id])
            s4 = cot.add_step(StepType.SUB_CONCLUSION,
                "建议方案：1.早晚高峰优惠票价 2.增加公交专用道 3.优化线路布局",
                0.8, "综合各因素得出具体建议。", [s3.step_id])

        elif strategy == ReasoningStrategy.CREATIVE:
            # 创造型：发散思维，多种方案
            s1 = cot.add_step(StepType.PREMISE, "需要提高公共交通使用率。", 1.0, "")
            s2 = cot.add_step(StepType.INFERENCE,
                "可以考虑引入AI动态调度系统，根据实时客流调整班次。",
                0.7, "利用技术创新提升效率。", [s1.step_id])
            s3 = cot.add_step(StepType.INFERENCE,
                "可以推出'公交+骑行'联运套餐，解决最后一公里问题。",
                0.65, "跨界融合创新。", [s1.step_id])
            s4 = cot.add_step(StepType.INFERENCE,
                "可以设计游戏化的乘车奖励系统，积分兑换奖品。",
                0.6, "行为经济学思路。", [s1.step_id])
            s5 = cot.add_step(StepType.SUB_CONCLUSION,
                "创新方案：AI动态调度、联运套餐、游戏化奖励、社区定制公交。",
                0.7, "汇总各种创意方案。", [s2.step_id, s3.step_id, s4.step_id])

        else:  # CRITICAL
            # 批判型：质疑和评估
            s1 = cot.add_step(StepType.PREMISE, "有人建议通过降低票价提高公交使用率。", 1.0, "")
            s2 = cot.add_step(StepType.INFERENCE,
                "降价可能导致财政收入减少，反而降低服务质量。",
                0.8, "考虑经济可持续性。", [s1.step_id])
            s3 = cot.add_step(StepType.INFERENCE,
                "高峰期增加班次可能导致道路更拥堵，反而降低准点率。",
                0.75, "考虑道路容量限制。", [s1.step_id])
            s4 = cot.add_step(StepType.INFERENCE,
                "需要先研究当前使用率低的根本原因，不能盲目施策。",
                0.9, "强调问题诊断的重要性。", [s2.step_id, s3.step_id])
            s5 = cot.add_step(StepType.SUB_CONCLUSION,
                "建议：先进行乘客调研，找到核心痛点，再有针对性地改进。",
                0.85, "基于批判性分析得出审慎建议。", [s4.step_id])

        # 打印推理步骤
        for i, step in enumerate(cot.steps, 1):
            icon = {
                "premise": "📌",
                "inference": "➡️",
                "sub_conclusion": "🔹",
                "correction": "🔧",
                "final_conclusion": "🎯",
            }.get(step.step_type.value, "•")
            print(f"  {icon} {step.content[:60]}...")
            print(f"     置信度: {step.confidence:.0%}")

        result = cot.get_result()
        print(f"\n  整体置信度: {result.overall_confidence:.1%}")
        print(f"  推理步数: {result.total_steps}")

    print()


# ======================================================================
# 示例 4: 置信度追踪
# ======================================================================

def demo_confidence_tracking():
    """
    演示推理过程中的置信度追踪。

    每一步推理都有自己的置信度，最终结论的置信度
    会沿着推理链传播和衰减：
    - 前提的置信度高
    - 每一步推理会引入不确定性
    - 最终置信度是各步的综合
    """
    print("=" * 60)
    print("  示例 4：置信度追踪与传播")
    print("=" * 60)
    print()

    cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)

    print("问题: 如果明天下雨，小明就会带伞。如果小明带伞，")
    print("      他就不会淋雨。那么，如果明天下雨，小明会淋雨吗？")
    print()

    # 构建一个多步推理链，观察置信度变化
    steps_info = [
        (StepType.PREMISE, "前提1: 如果下雨，小明带伞。", 0.95),
        (StepType.PREMISE, "前提2: 如果带伞，不淋雨。", 0.95),
        (StepType.INFERENCE, "推理: 下雨 → 带伞 → 不淋雨", 0.9),
        (StepType.INFERENCE, "应用: 明天如果下雨", 0.85),
        (StepType.SUB_CONCLUSION, "中间结论: 小明会带伞", 0.8),
        (StepType.FINAL_CONCLUSION, "最终结论: 小明不会淋雨", 0.75),
    ]

    prev_ids = []
    for i, (step_type, content, conf) in enumerate(steps_info, 1):
        step = cot.add_step(
            step_type=step_type,
            content=content,
            confidence=conf,
            justification=f"第 {i} 步推理",
            premises=prev_ids[-1:] if prev_ids else [],
        )
        prev_ids.append(step.step_id)

        # 可视化置信度
        bar_len = int(conf * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        print(f"  步骤 {i:2d} [{bar}] {conf:.0%}")
        print(f"         {content[:50]}")

    result = cot.get_result()
    print()
    print(f"整体置信度: {result.overall_confidence:.1%}")
    print(f"置信度衰减: {(0.95 - result.overall_confidence) * 100:.1f}% "
          f"(经过 {result.total_steps} 步推理)")
    print()
    print("💡 观察:")
    print("   - 前提的置信度最高（0.95）")
    print("   - 每一步推理都会略微降低置信度")
    print("   - 最终结论的置信度是各步的综合结果")
    print("   - 这模拟了人类推理中'传话游戏'的效果")

    print()
    return result


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有链式思维示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 高级链式思维推理")
    print("█" * 60)

    try:
        # 示例 1: 基础 CoT
        demo_basic_cot()

        # 示例 2: 自我纠正
        demo_self_correction()

        # 示例 3: 推理策略对比
        demo_reasoning_strategies()

        # 示例 4: 置信度追踪
        demo_confidence_tracking()

        print("=" * 60)
        print("  链式思维功能总结")
        print("=" * 60)
        print("""
  ✓ 逐步推理过程可视化
  ✓ 多策略推理（分析/创造/批判）
  ✓ 自动矛盾检测
  ✓ 自我修正机制
  ✓ 置信度追踪与传播
  ✓ 推理链完整记录
  ✓ 前提-推理-结论结构
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
