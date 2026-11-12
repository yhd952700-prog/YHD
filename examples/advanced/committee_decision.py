"""
委员会决策 - LiuHao AI OS 高级示例

本示例演示多智能体委员会决策模式：
1. 组建专家委员会
2. 多种投票方式
3. 冲突解决策略
4. 结果汇总与共识评估

运行方式：
    python examples/advanced/committee_decision.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.committee import (
    AgentCommittee,
    CommitteeResult,
    Vote,
    VotingMethod,
    ConflictStrategy,
)
from ai.agent_factory import AgentRole


# ======================================================================
# 示例委员会成员数据
# ======================================================================

@dataclass
class CommitteeMember:
    """委员会成员。"""
    member_id: str
    name: str
    role: str
    expertise: List[str]
    weight: float = 1.0  # 投票权重
    bias: str = "neutral"  # 倾向（用于模拟）


# ======================================================================
# 示例 1: 多数投票
# ======================================================================

def demo_majority_voting():
    """
    演示简单多数投票 (Majority Voting)。

    每个成员投一票，获得超过半数选票的选项获胜。
    这是最常见、最简单的投票方式。
    """
    print("=" * 60)
    print("  示例 1：多数投票 (Majority Voting)")
    print("=" * 60)
    print()

    print("问题: 我们应该采用哪种技术方案？")
    print("选项: A) 方案Alpha   B) 方案Beta   C) 方案Gamma")
    print()

    # 创建委员会
    committee = AgentCommittee(name="技术决策委员会", voting_method=VotingMethod.MAJORITY)

    # 添加成员（模拟）
    members = [
        CommitteeMember("m1", "张工", "架构师", ["系统设计", "性能"], weight=1.0),
        CommitteeMember("m2", "李工", "前端专家", ["UI", "用户体验"], weight=1.0),
        CommitteeMember("m3", "王工", "后端专家", ["API", "数据库"], weight=1.0),
        CommitteeMember("m4", "赵工", "运维工程师", ["部署", "稳定性"], weight=1.0),
        CommitteeMember("m5", "陈工", "产品经理", ["需求", "用户"], weight=1.0),
    ]

    # 模拟投票结果
    votes_data = [
        ("m1", "架构师", "A", "Alpha 技术栈更成熟，风险低"),
        ("m2", "前端专家", "B", "Beta 对前端开发更友好"),
        ("m3", "后端专家", "A", "Alpha 的后端生态更好"),
        ("m4", "运维工程师", "A", "Alpha 部署运维更简单"),
        ("m5", "产品经理", "C", "Gamma 功能最全面"),
    ]

    print("投票过程:")
    print("-" * 50)

    votes = []
    for voter_id, role, choice, reasoning in votes_data:
        vote = Vote(
            voter_id=voter_id,
            voter_role=role,
            choice=choice,
            weight=1.0,
            confidence=0.8,
            reasoning=reasoning,
        )
        votes.append(vote)
        print(f"  {role:10s} → {choice} ({reasoning[:30]}...)")

    # 计票
    vote_counts = {}
    for vote in votes:
        key = str(vote.choice)
        vote_counts[key] = vote_counts.get(key, 0) + 1

    print()
    print("投票统计:")
    for option, count in sorted(vote_counts.items(), key=lambda x: -x[1]):
        bar_len = int(count / len(votes) * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        percentage = count / len(votes) * 100
        print(f"  方案 {option}: [{bar}] {count}票 ({percentage:.0f}%)")

    # 确定获胜者
    winner = max(vote_counts, key=vote_counts.get)
    winner_count = vote_counts[winner]
    has_majority = winner_count > len(votes) / 2

    print()
    if has_majority:
        print(f"🏆 结果: 方案 {winner} 获胜（多数通过，{winner_count}/{len(votes)} 票）")
    else:
        print(f"⚠️  结果: 方案 {winner} 票数最多，但未达到多数（{winner_count}/{len(votes)}）")
        print("   可能需要第二轮投票或协商")

    # 共识水平
    consensus = winner_count / len(votes)
    print(f"📊 共识水平: {consensus:.0%}")
    print(f"   {'高共识' if consensus >= 0.7 else '中等共识' if consensus >= 0.5 else '低共识'}")

    print()


# ======================================================================
# 示例 2: 加权投票
# ======================================================================

def demo_weighted_voting():
    """
    演示加权投票 (Weighted Voting)。

    不同成员的投票权重不同，通常基于专业程度或职位。
    专家在其专业领域内的投票权重更高。
    """
    print("=" * 60)
    print("  示例 2：加权投票 (Weighted Voting)")
    print("=" * 60)
    print()

    print("问题: 我们应该在数据库性能优化上投入多少资源？")
    print("选项: A) 低投入   B) 中投入   C) 高投入")
    print()

    print("说明: 数据库专家的权重更高（2.0 vs 1.0）")
    print()

    committee = AgentCommittee(name="资源分配委员会", voting_method=VotingMethod.WEIGHTED)

    # 成员及其权重
    members = [
        CommitteeMember("dba1", "王DBA", "数据库专家",
                         ["数据库", "性能"], weight=2.0),
        CommitteeMember("dba2", "李DBA", "数据库专家",
                         ["数据库", "运维"], weight=1.8),
        CommitteeMember("dev1", "张开发", "后端开发",
                         ["开发", "API"], weight=1.0),
        CommitteeMember("dev2", "刘开发", "后端开发",
                         ["开发", "前端"], weight=1.0),
        CommitteeMember("pm", "陈产品", "产品经理",
                         ["产品", "需求"], weight=1.0),
    ]

    # 模拟投票
    votes_data = [
        ("dba1", "数据库专家", "C", "数据库是核心瓶颈，需要大力优化", 2.0),
        ("dba2", "数据库专家", "B", "需要优化，但资源有限，中等投入较合理", 1.8),
        ("dev1", "后端开发", "B", "中等投入比较平衡", 1.0),
        ("dev2", "后端开发", "A", "目前性能还可以接受", 1.0),
        ("pm", "产品经理", "A", "应该优先开发新功能", 1.0),
    ]

    print("投票过程:")
    print("-" * 50)

    weighted_counts = {}
    simple_counts = {}

    for voter_id, role, choice, reasoning, weight in votes_data:
        # 加权票数
        key = str(choice)
        weighted_counts[key] = weighted_counts.get(key, 0) + weight
        simple_counts[key] = simple_counts.get(key, 0) + 1

        weight_stars = "★" * int(weight * 2)
        print(f"  {role:10s} {weight_stars:4s} → {choice} "
              f"({reasoning[:25]}...)")

    total_weight = sum(weighted_counts.values())

    print()
    print("投票结果对比:")
    print("-" * 50)

    print(f"  {'选项':6s} {'简单票数':>8s} {'简单比例':>10s}  {'加权票数':>8s} {'加权比例':>10s}")
    print(f"  {'─'*6} {'─'*8} {'─'*10}  {'─'*8} {'─'*10}")

    for option in sorted(weighted_counts.keys()):
        simple = simple_counts.get(option, 0)
        weighted = weighted_counts[option]
        simple_pct = simple / len(votes_data) * 100
        weighted_pct = weighted / total_weight * 100
        print(f"  方案 {option:3s} {simple:>8d} {simple_pct:>9.1f}%  "
              f"{weighted:>8.1f} {weighted_pct:>9.1f}%")

    # 加权获胜者
    weighted_winner = max(weighted_counts, key=weighted_counts.get)
    simple_winner = max(simple_counts, key=simple_counts.get)

    print()
    print(f"简单多数获胜: 方案 {simple_winner}")
    print(f"加权投票获胜: 方案 {weighted_winner}")

    if simple_winner != weighted_winner:
        print()
        print("💡 结果不同的原因:")
        print("   数据库专家的权重更高，他们的意见对最终结果影响更大")
        print("   这体现了专家决策在专业领域的重要性")

    print()


# ======================================================================
# 示例 3: 共识决策
# ======================================================================

def demo_consensus_building():
    """
    演示共识构建 (Consensus Building)。

    共识不是简单的投票，而是通过讨论、协商，
    让所有成员都能接受最终结果。

    过程：
    1. 初始投票
    2. 识别分歧点
    3. 讨论和调整
    4. 再次投票
    5. 直到达成共识或确定无法达成
    """
    print("=" * 60)
    print("  示例 3：共识构建 (Consensus Building)")
    print("=" * 60)
    print()

    print("问题: 产品下季度的优先级应该是什么？")
    print()
    print("共识构建过程:")
    print("-" * 50)

    # 初始立场
    initial_positions = {
        "产品经理": "新功能开发",
        "技术负责人": "技术债务偿还",
        "设计师": "用户体验优化",
        "测试负责人": "质量提升",
        "运营负责人": "性能优化",
    }

    print("\n第一轮: 初始立场")
    print("  各成员初始立场差异很大:")
    for role, position in initial_positions.items():
        print(f"    • {role}: {position}")

    # 第一轮投票（多选项）
    print("\n第二轮: 多轮讨论后")
    print("  经过讨论，大家认识到各方向都有价值，需要排序:")

    # 模拟排名投票结果
    ranked_votes = {
        "产品经理": ["新功能开发", "用户体验优化", "性能优化", "技术债务", "质量提升"],
        "技术负责人": ["技术债务", "性能优化", "质量提升", "新功能开发", "用户体验优化"],
        "设计师": ["用户体验优化", "新功能开发", "质量提升", "性能优化", "技术债务"],
        "测试负责人": ["质量提升", "技术债务", "用户体验优化", "性能优化", "新功能开发"],
        "运营负责人": ["性能优化", "新功能开发", "质量提升", "用户体验优化", "技术债务"],
    }

    # 计算 Borda 计数（排名第一得5分，第二得4分...）
    borda_scores = {}
    for role, ranking in ranked_votes.items():
        for i, option in enumerate(ranking):
            score = len(ranking) - i
            borda_scores[option] = borda_scores.get(option, 0) + score

    print("  Borda 计数结果（排名越高分数越多）:")
    max_score = max(borda_scores.values())
    for option, score in sorted(borda_scores.items(), key=lambda x: -x[1]):
        bar_len = int(score / max_score * 25)
        bar = "█" * bar_len + "░" * (25 - bar_len)
        print(f"    {option:12s} [{bar}] {score} 分")

    # 共识水平
    winner_score = max(borda_scores.values())
    total_score = sum(borda_scores.values())
    consensus_ratio = winner_score / total_score
    print()
    print(f"  共识水平: {consensus_ratio:.1%}")
    print(f"  (获胜选项得分占总分的比例)")

    if consensus_ratio > 0.3:
        print("  ✅ 达成了相对共识")
    else:
        print("  ⚠️  共识不足，需要进一步讨论")

    print()
    print("第三轮: 综合方案")
    print("  基于讨论和投票，最终的优先级排序:")
    final_ranking = sorted(borda_scores.keys(),
                           key=lambda x: borda_scores[x], reverse=True)
    for i, option in enumerate(final_ranking, 1):
        print(f"    {i}. {option} ({borda_scores[option]} 分)")

    print()
    print("💡 共识决策的优势:")
    print("   1. 每个人的意见都被考虑")
    print("   2. 结果更容易被所有人接受")
    print("   3. 促进沟通和理解")
    print("   4. 减少决策后的阻力")

    print()


# ======================================================================
# 示例 4: 冲突解决
# ======================================================================

def demo_conflict_resolution():
    """
    演示冲突解决策略。

    当委员会成员意见不一致时，可以使用不同的解决策略：
    - VOTE: 投票决定
    - HIERARCHY: 上级决定
    - MEDIATOR: 第三方调解
    - COMPROMISE: 双方妥协
    - EVIDENCE: 看证据说话
    """
    print("=" * 60)
    print("  示例 4：冲突解决策略")
    print("=" * 60)
    print()

    print("场景: 技术选型出现分歧")
    print("  团队 A 主张: 使用新技术（性能更好，但学习成本高）")
    print("  团队 B 主张: 使用成熟技术（稳定可靠，但性能一般）")
    print()

    strategies = [
        (ConflictStrategy.VOTE, "投票决定",
         "简单直接，少数服从多数",
         "可能忽视少数派的合理意见"),
        (ConflictStrategy.HIERARCHY, "上级裁决",
         "决策效率高，责任明确",
         "依赖上级判断力，可能出错"),
        (ConflictStrategy.MEDIATOR, "第三方调解",
         "客观中立，促进沟通",
         "需要找到合适的调解人，耗时"),
        (ConflictStrategy.COMPROMISE, "双方妥协",
         "都有所得，维护关系",
         "结果可能不是最优的"),
        (ConflictStrategy.EVIDENCE, "证据优先",
         "基于事实，理性客观",
         "需要时间收集证据"),
    ]

    print("冲突解决策略对比:")
    print("-" * 50)

    for strategy, name, pros, cons in strategies:
        print(f"\n  🛠️  策略: {name} ({strategy.value})")
        print(f"     ✅ 优点: {pros}")
        print(f"     ❌ 缺点: {cons}")

    print()
    print("推荐策略选择流程:")
    print()
    print("  问题重要吗？")
    print("    ├─ 不重要 → 投票或随机决定")
    print("    └─ 重要")
    print("         ├─ 有客观标准吗？")
    print("         │   ├─ 有 → 基于证据决策")
    print("         │   └─ 没有")
    print("         │       ├─ 时间紧迫吗？")
    print("         │       │   ├─ 紧迫 → 上级裁决")
    print("         │       │   └─ 不紧迫 → 调解协商")
    print("         │       └─ 关系重要吗？")
    print("         │           ├─ 重要 → 妥协/调解")
    print("         │           └─ 不重要 → 投票")
    print("         └─ 有数据吗？")
    print("             ├─ 有 → 证据优先")
    print("             └─ 没有 → 收集证据再决策")

    print()


# ======================================================================
# 示例 5: 委员会结果汇总
# ======================================================================

def demo_result_aggregation():
    """
    演示委员会决策结果的汇总和分析。
    """
    print("=" * 60)
    print("  示例 5：决策结果汇总与分析")
    print("=" * 60)
    print()

    # 模拟一个完整的委员会决策
    question = "是否应该在 Q3 启动新项目 X？"
    options = ["强烈支持", "支持", "中立", "反对", "强烈反对"]

    votes = [
        {"member": "技术总监", "role": "executive", "vote": "支持",
         "confidence": 0.9, "weight": 2.0,
         "reason": "技术上可行，有战略价值"},
        {"member": "产品总监", "role": "executive", "vote": "强烈支持",
         "confidence": 0.95, "weight": 2.0,
         "reason": "市场需求强烈，竞争优势明显"},
        {"member": "财务总监", "role": "executive", "vote": "中立",
         "confidence": 0.7, "weight": 1.5,
         "reason": "投资回报周期较长，风险中等"},
        {"member": "技术专家A", "role": "expert", "vote": "支持",
         "confidence": 0.8, "weight": 1.2,
         "reason": "架构方案合理，技术风险可控"},
        {"member": "技术专家B", "role": "expert", "vote": "反对",
         "confidence": 0.85, "weight": 1.2,
         "reason": "团队能力不足，交付风险高"},
        {"member": "项目经理", "role": "manager", "vote": "支持",
         "confidence": 0.75, "weight": 1.0,
         "reason": "排期可行，资源基本到位"},
        {"member": "市场经理", "role": "manager", "vote": "强烈支持",
         "confidence": 0.9, "weight": 1.0,
         "reason": "客户反馈积极，销售有信心"},
    ]

    print(f"问题: {question}")
    print(f"委员会规模: {len(votes)} 人")
    print()

    # 计票
    vote_counts = {opt: 0 for opt in options}
    weighted_counts = {opt: 0.0 for opt in options}
    confidence_scores = []

    print("投票详情:")
    print("-" * 50)
    for v in votes:
        vote_counts[v["vote"]] += 1
        weighted_counts[v["vote"]] += v["weight"]
        confidence_scores.append(v["confidence"])

        bar_len = int(v["confidence"] * 20)
        conf_bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {v['member']:10s} → {v['vote']:6s} "
              f"[{conf_bar}] {v['confidence']:.0%}")
        print(f"    ({v['reason'][:40]})")

    print()
    print("投票统计:")
    print("-" * 50)

    total_simple = sum(vote_counts.values())
    total_weighted = sum(weighted_counts.values())

    for opt in options:
        simple = vote_counts[opt]
        weighted = weighted_counts[opt]
        if simple > 0 or weighted > 0:
            simple_pct = simple / total_simple * 100
            weighted_pct = weighted / total_weighted * 100
            bar_len = int(simple_pct / 2)
            bar = "█" * bar_len
            print(f"  {opt:8s} [{bar:30s}] {simple:2d}票 ({simple_pct:.0f}%) "
                  f"加权: {weighted:.1f} ({weighted_pct:.0f}%)")

    # 计算共识水平
    max_votes = max(vote_counts.values())
    consensus = max_votes / total_simple
    print()
    print(f"共识水平: {consensus:.1%}")

    # 计算平均置信度
    avg_confidence = sum(confidence_scores) / len(confidence_scores)
    print(f"平均置信度: {avg_confidence:.0%}")

    # 综合决策
    support_options = ["强烈支持", "支持"]
    support_count = sum(vote_counts[o] for o in support_options)
    support_pct = support_count / total_simple

    print()
    print("📋 决策总结:")
    print(f"  • 支持率: {support_pct:.0%} ({support_count}/{total_simple})")
    print(f"  • 共识水平: {consensus:.0%}")
    print(f"  • 平均置信度: {avg_confidence:.0%}")
    print(f"  • 加权支持率: "
          f"{sum(weighted_counts[o] for o in support_options) / total_weighted:.0%}")

    if support_pct >= 0.7 and consensus >= 0.5:
        print(f"\n  ✅ 决策结果: 通过（高支持度，共识良好）")
    elif support_pct >= 0.5:
        print(f"\n  ⚠️  决策结果: 有条件通过（需要进一步讨论分歧点）")
    else:
        print(f"\n  ❌ 决策结果: 未通过（支持度不足）")

    # 识别少数派意见
    minority_options = [opt for opt in options if vote_counts[opt] > 0
                        and opt not in support_options]
    if minority_options:
        print()
        print("少数派意见:")
        for opt in minority_options:
            if vote_counts[opt] > 0:
                members = [v["member"] for v in votes if v["vote"] == opt]
                print(f"  • {opt}: {', '.join(members)}")
                reasons = [v["reason"] for v in votes if v["vote"] == opt]
                for r in reasons:
                    print(f"    - {r}")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有委员会决策示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 委员会决策模式")
    print("█" * 60)

    try:
        # 示例 1: 多数投票
        demo_majority_voting()

        # 示例 2: 加权投票
        demo_weighted_voting()

        # 示例 3: 共识构建
        demo_consensus_building()

        # 示例 4: 冲突解决
        demo_conflict_resolution()

        # 示例 5: 结果汇总
        demo_result_aggregation()

        print("=" * 60)
        print("  委员会决策功能总结")
        print("=" * 60)
        print("""
  ✓ 多种投票方式（多数、加权、排名、共识）
  ✓ 冲突解决策略
  ✓ 共识水平评估
  ✓ 置信度追踪
  ✓ 少数派意见记录
  ✓ 完整决策审计
  ✓ 结果可视化
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
