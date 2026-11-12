"""
多智能体团队 - LiuHao AI OS 快速入门示例

本示例演示如何创建和使用多智能体团队：
1. 创建不同角色的 Agent 组成团队
2. 设置消息总线（MessageBus）进行通信
3. 运行一个简单的协作工作流
4. 收集并整合结果

运行方式：
    python examples/quickstart/multi_agent_team.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.employee import Agent, AgentStatus
from ai.collaboration import (
    MultiAgentTeam,
    MessageBus,
    AgentMessage,
    MessageKind,
    Role,
)
from ai.agent_factory import AgentFactory, AgentRole
from ai.providers import get_provider


# ======================================================================
# 示例 1: 使用消息总线进行 Agent 间通信
# ======================================================================

def demo_message_bus():
    """
    演示消息总线（MessageBus）的基本用法。

    消息总线是多智能体协作的基础设施：
    - Agent 可以发送消息给特定的其他 Agent
    - Agent 可以发送广播消息给所有 Agent
    - 每个 Agent 有自己的邮箱（mailbox）
    - 支持多种消息类型：询问、告知、委托、汇报等
    """
    print("=" * 60)
    print("  示例 1：消息总线（MessageBus）")
    print("=" * 60)
    print()

    # ---- 创建消息总线 ----
    bus = MessageBus()
    print("[1/5] 创建消息总线")

    # ---- 注册 Agent ----
    # Agent 需要先注册到总线，才能收发消息
    bus.register("alice")  # 研究员
    bus.register("bob")    # 分析师
    bus.register("carol")  # 经理
    print("[2/5] 注册 3 个 Agent: alice, bob, carol")

    # ---- 发送点对点消息 ----
    print("\n[3/5] 发送点对点消息...")
    msg1 = bus.send_message(
        sender="carol",
        receiver="alice",
        kind=MessageKind.DELEGATE,
        payload={
            "task": "研究人工智能的发展历史",
            "deadline": "2024-01-15",
            "priority": "high",
        },
    )
    print(f"       carol -> alice (委托): 研究人工智能历史")
    print(f"       消息 ID: {msg1.id[:16]}...")

    msg2 = bus.send_message(
        sender="carol",
        receiver="bob",
        kind=MessageKind.DELEGATE,
        payload={
            "task": "分析 AI 市场数据",
            "deadline": "2024-01-15",
            "priority": "high",
        },
    )
    print(f"       carol -> bob (委托): 分析 AI 市场数据")

    # ---- 发送广播消息 ----
    print("\n[4/5] 发送广播消息...")
    broadcast = bus.send_message(
        sender="carol",
        receiver="*",  # "*" 表示广播给所有已注册的 Agent
        kind=MessageKind.TELL,
        payload={
            "announcement": "团队会议将于明天 10:00 举行",
            "meeting_id": "team-sync-001",
        },
    )
    print(f"       carol -> * (广播): 团队会议通知")

    # ---- 读取邮箱 ----
    print("\n[5/5] 各 Agent 读取邮箱...")

    # alice 的邮箱
    alice_messages = bus.poll("alice")
    print(f"\n       alice 收到 {len(alice_messages)} 条消息:")
    for msg in alice_messages:
        print(f"         - 来自 {msg.sender}, 类型: {msg.kind.value}")
        print(f"           内容: {list(msg.payload.keys())}")

    # bob 的邮箱
    bob_messages = bus.poll("bob")
    print(f"\n       bob 收到 {len(bob_messages)} 条消息:")
    for msg in bob_messages:
        print(f"         - 来自 {msg.sender}, 类型: {msg.kind.value}")

    # carol 的邮箱（应该只有广播消息）
    carol_messages = bus.poll("carol")
    print(f"\n       carol 收到 {len(carol_messages)} 条消息:")
    for msg in carol_messages:
        print(f"         - 来自 {msg.sender}, 类型: {msg.kind.value}")

    print()
    return bus


# ======================================================================
# 示例 2: 创建多智能体团队
# ======================================================================

def create_research_team() -> MultiAgentTeam:
    """
    创建一个研究团队，包含：
    - 1 名经理（协调者）
    - 2 名研究员（信息收集）
    - 1 名分析师（数据处理）
    - 1 名 QA（质量保证）
    """
    print("=" * 60)
    print("  示例 2：创建多智能体团队")
    print("=" * 60)
    print()

    # 获取 Provider
    try:
        provider = get_provider()
        print(f"[1/4] 已获取 Provider: {provider.__class__.__name__}")
    except Exception as e:
        print(f"[1/4] 警告: 无法获取真实 Provider ({e})")
        print("       将使用模拟模式创建团队")
        provider = None

    factory = AgentFactory(provider=provider) if provider else None

    # ---- 创建团队 ----
    team = MultiAgentTeam(name="research-team", description="市场研究团队")
    print("[2/4] 创建团队: research-team")

    # ---- 添加成员 ----
    # 方式一：通过 Agent 工厂创建
    if factory:
        manager_agent = factory.create_agent(AgentRole.MANAGER, name="manager-1")
        researcher_1 = factory.create_agent(AgentRole.RESEARCHER, name="researcher-1")
        researcher_2 = factory.create_agent(AgentRole.RESEARCHER, name="researcher-2")
        analyst_1 = factory.create_agent(AgentRole.ANALYST, name="analyst-1")
        qa_1 = factory.create_agent(AgentRole.QA, name="qa-1")
    else:
        # 模拟模式下，创建基础 Agent
        from ai.providers import BaseProvider
        class MockProvider(BaseProvider):
            def __init__(self):
                self.name = "mock"
            def generate(self, prompt, **kwargs):
                return {"output": f"Mock response for: {prompt[:50]}...",
                        "latency_ms": 100, "tokens_used": 50}

        mock_provider = MockProvider()
        manager_agent = Agent(
            id="mgr-001", agent_type="manager", name="manager-1",
            provider=mock_provider,
            system_prompt="你是一名经理，负责协调团队工作。",
        )
        researcher_1 = Agent(
            id="res-001", agent_type="researcher", name="researcher-1",
            provider=mock_provider,
            system_prompt="你是一名研究员，负责收集信息。",
        )
        researcher_2 = Agent(
            id="res-002", agent_type="researcher", name="researcher-2",
            provider=mock_provider,
            system_prompt="你是一名研究员，负责收集信息。",
        )
        analyst_1 = Agent(
            id="ana-001", agent_type="analyst", name="analyst-1",
            provider=mock_provider,
            system_prompt="你是一名分析师，负责分析数据。",
        )
        qa_1 = Agent(
            id="qa-001", agent_type="qa", name="qa-1",
            provider=mock_provider,
            system_prompt="你是一名质量保证工程师，负责检查结果质量。",
        )

    # 将 Agent 添加到团队
    team.add_member(manager_agent, role=Role.MANAGER)
    team.add_member(researcher_1, role=Role.RESEARCHER)
    team.add_member(researcher_2, role=Role.RESEARCHER)
    team.add_member(analyst_1, role=Role.ANALYST)
    team.add_member(qa_1, role=Role.QA)

    print(f"[3/4] 添加团队成员:")
    for role, members in team.members.items():
        print(f"       {role.value}: {len(members)} 人")

    # ---- 设置团队层级 ----
    # 经理是团队负责人
    team.set_lead(manager_agent.id)

    print(f"[4/4] 设置团队负责人: {manager_agent.name}")
    print()

    return team


# ======================================================================
# 示例 3: 运行团队工作流
# ======================================================================

def run_team_workflow(team: MultiAgentTeam, task: str):
    """
    运行一个简单的团队协作工作流。

    工作流：
    1. 经理接收任务，分解给研究员
    2. 研究员收集信息，提交给分析师
    3. 分析师处理数据，生成报告
    4. QA 检查报告质量
    5. 经理整合最终结果
    """
    print("=" * 60)
    print("  示例 3：运行团队工作流")
    print("=" * 60)
    print()
    print(f"任务: {task}")
    print()

    # 记录工作流步骤
    workflow_steps = []

    # ---- 步骤 1: 经理接收并分解任务 ----
    print("[1/5] 经理接收任务并分解...")
    manager = team.get_lead()
    if manager:
        print(f"       负责人: {manager.name}")
        print(f"       分解任务为子任务:")
        sub_tasks = [
            "收集 AI 行业发展历史资料",
            "收集 AI 市场规模数据",
            "收集主要 AI 公司信息",
        ]
        for i, st in enumerate(sub_tasks, 1):
            print(f"         {i}. {st}")
        workflow_steps.append(("manager_decompose", sub_tasks))
    time.sleep(0.1)

    # ---- 步骤 2: 研究员收集信息 ----
    print("\n[2/5] 研究员收集信息...")
    researchers = team.get_members_by_role(Role.RESEARCHER)
    research_results = []

    for i, researcher in enumerate(researchers):
        if i < len(sub_tasks):
            sub_task = sub_tasks[i]
            print(f"       {researcher.name} 执行: {sub_task}")

            try:
                result = researcher.execute(sub_task)
                output = result.get("output", "结果已生成")
                print(f"         -> 完成 (输出长度: {len(output)} 字符)")
                research_results.append({
                    "researcher": researcher.name,
                    "task": sub_task,
                    "result": output[:100] + "...",
                })
            except Exception as e:
                print(f"         -> 失败: {e}")
                research_results.append({
                    "researcher": researcher.name,
                    "task": sub_task,
                    "result": f"[ERROR] {e}",
                })

    workflow_steps.append(("research", research_results))

    # ---- 步骤 3: 分析师处理数据 ----
    print("\n[3/5] 分析师处理数据...")
    analysts = team.get_members_by_role(Role.ANALYST)
    if analysts:
        analyst = analysts[0]
        analysis_task = (
            f"根据以下研究结果进行数据分析:\n"
            + "\n".join([f"- {r['task']}: {r['result']}" for r in research_results])
        )
        print(f"       {analyst.name} 进行综合分析...")

        try:
            analysis_result = analyst.execute(analysis_task)
            output = analysis_result.get("output", "分析完成")
            print(f"         -> 分析报告已生成 ({len(output)} 字符)")
            workflow_steps.append(("analysis", output[:200] + "..."))
        except Exception as e:
            print(f"         -> 分析失败: {e}")
            workflow_steps.append(("analysis", f"[ERROR] {e}"))

    # ---- 步骤 4: QA 质量检查 ----
    print("\n[4/5] QA 质量检查...")
    qas = team.get_members_by_role(Role.QA)
    if qas:
        qa = qas[0]
        qa_task = "请检查这份分析报告的质量，包括准确性、完整性和清晰度。"
        print(f"       {qa.name} 执行质量检查...")

        try:
            qa_result = qa.execute(qa_task)
            output = qa_result.get("output", "质量检查完成")
            print(f"         -> 检查完成: {output[:100]}...")
            workflow_steps.append(("qa_review", output[:100] + "..."))
        except Exception as e:
            print(f"         -> 检查失败: {e}")
            workflow_steps.append(("qa_review", f"[ERROR] {e}"))

    # ---- 步骤 5: 经理整合最终结果 ----
    print("\n[5/5] 经理整合最终结果...")
    if manager:
        final_task = "请整合所有研究、分析和 QA 结果，生成最终报告摘要。"
        print(f"       {manager.name} 生成最终报告...")

        try:
            final_result = manager.execute(final_task)
            output = final_result.get("output", "最终报告已生成")
            print(f"         -> 最终报告已生成 ({len(output)} 字符)")
            workflow_steps.append(("final_report", output))
        except Exception as e:
            print(f"         -> 生成失败: {e}")
            workflow_steps.append(("final_report", f"[ERROR] {e}"))

    print()
    return workflow_steps


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有多智能体团队示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 多智能体团队快速入门")
    print("█" * 60)
    print()

    try:
        # 示例 1: 消息总线
        demo_message_bus()

        # 示例 2: 创建团队
        team = create_research_team()

        # 示例 3: 运行工作流
        task = "研究人工智能行业的发展现状和未来趋势"
        results = run_team_workflow(team, task)

        # ---- 总结 ----
        print("=" * 60)
        print("  团队协作总结")
        print("=" * 60)
        print()
        print(f"  团队名称: {team.name}")
        print(f"  团队描述: {team.description}")
        print(f"  总成员数: {team.get_member_count()}")
        print(f"  工作流步骤: {len(results)} 步")
        print()
        print("  工作流步骤:")
        for i, (step_name, _) in enumerate(results, 1):
            print(f"    {i}. {step_name}")
        print()
        print("  多智能体协作模式:")
        print("    - 角色分工：不同角色专注不同任务")
        print("    - 消息传递：通过 MessageBus 通信")
        print("    - 流水线：任务按顺序传递处理")
        print("    - 质量保障：QA 角色确保输出质量")
        print()

        print("✅ 多智能体团队示例运行完成！")
        print("   接下来可以查看 use_cases/ 目录了解实际应用场景。")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
