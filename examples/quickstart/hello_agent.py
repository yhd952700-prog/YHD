"""
Hello Agent - LiuHao AI OS 快速入门示例

这是你的第一个 LiuHao AI OS 程序。本示例演示了：
1. 如何创建一个基础的 Agent
2. 如何执行一个简单任务
3. 如何获取并打印结果
4. 基本的错误处理方式

运行方式：
    python examples/quickstart/hello_agent.py
"""

import sys
import os

# 确保可以导入 src 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.employee import Agent, AgentStatus
from ai.providers import get_provider, BaseProvider
from ai.agent_factory import AgentFactory, AgentRole


def create_simple_agent(name: str = "hello-agent") -> Agent:
    """
    创建一个基础的通用 Agent。

    步骤说明：
    1. 获取一个 AI 模型 Provider（负责实际的推理调用）
    2. 使用 AgentFactory 根据角色创建 Agent
    3. 返回配置好的 Agent 实例

    Args:
        name: Agent 的名称，用于标识和日志记录

    Returns:
        配置完成的 Agent 实例

    Raises:
        RuntimeError: 如果无法获取 Provider
    """
    # ---- 步骤 1: 获取 AI 模型 Provider ----
    # Provider 是底层 AI 模型的抽象层，负责实际的文本生成
    # 系统会根据配置自动选择可用的模型（OpenAI / Anthropic / 本地模型等）
    try:
        provider: BaseProvider = get_provider()
    except Exception as e:
        raise RuntimeError(f"无法获取 AI Provider: {e}") from e

    print(f"[1/3] 已获取 Provider: {provider.__class__.__name__}")

    # ---- 步骤 2: 使用工厂创建 Agent ----
    # AgentFactory 提供了按角色创建 Agent 的便捷方式
    # 每个角色都有预设的 system prompt、能力列表和温度参数
    factory = AgentFactory(provider=provider)

    # 创建一个通用角色的 Agent
    # 可选角色: MANAGER, RESEARCHER, ANALYST, CRITIC, CREATOR, EXECUTOR, QA, PLANNER, GENERAL
    agent = factory.create_agent(
        role=AgentRole.GENERAL,
        name=name,
        # 可选：覆盖默认温度（0=确定性，1=创造性）
        temperature=0.7,
    )

    print(f"[2/3] 已创建 Agent: {agent.name} (角色: {agent.agent_type})")
    print(f"       状态: {agent.status.value}")

    return agent


def run_simple_task(agent: Agent, task: str) -> dict:
    """
    让 Agent 执行一个简单任务并返回结果。

    Args:
        agent: 要执行任务的 Agent 实例
        task: 任务描述文本

    Returns:
        包含任务结果的字典，通常有 'output', 'status', 'metadata' 等字段
    """
    print(f"\n[3/3] 正在执行任务: {task[:60]}...")
    print("-" * 50)

    try:
        # execute() 是 Agent 的核心方法
        # 它会调用底层 Provider 来生成回答
        result = agent.execute(task)

        # 任务完成后，Agent 的状态会更新
        print(f"\n任务完成! Agent 状态: {agent.status.value}")
        print(f"已完成任务数: {agent.completed_tasks}")

        return result

    except Exception as e:
        # 实际使用中应该根据错误类型进行不同处理
        print(f"任务执行失败: {e}")
        agent.status = AgentStatus.FAILED
        agent.failed_tasks += 1
        raise


def main():
    """主函数：演示完整的 Agent 使用流程。"""
    print("=" * 60)
    print("  LiuHao AI OS - Hello Agent 快速入门示例")
    print("=" * 60)
    print()

    # ---- 1. 创建 Agent ----
    try:
        agent = create_simple_agent("my-first-agent")
    except RuntimeError as e:
        print(f"\n错误: {e}")
        print("提示: 请确保已配置 AI 模型 API Key")
        print("      可以通过环境变量或 .env 文件进行配置")
        sys.exit(1)

    # ---- 2. 执行任务 ----
    task_description = "用三句话介绍一下什么是人工智能操作系统（AI OS）。"

    try:
        result = run_simple_task(agent, task_description)

        # ---- 3. 打印结果 ----
        print("\n" + "=" * 60)
        print("  执行结果")
        print("=" * 60)

        # result 是一个字典，通常包含 output 字段
        output = result.get("output", str(result))
        print(output)

        # 打印一些元信息
        if "latency_ms" in result:
            print(f"\n耗时: {result['latency_ms']:.0f}ms")
        if "tokens_used" in result:
            print(f"Token 使用: {result['tokens_used']}")

        print("\n" + "=" * 60)
        print("  恭喜！你已经成功运行了第一个 LiuHao AI OS 程序！")
        print("  接下来可以查看 using_memory.py 了解记忆系统。")
        print("=" * 60)

    except Exception as e:
        print(f"\n程序执行出错: {e}")
        print("\n排错提示：")
        print("  1. 检查网络连接是否正常")
        print("  2. 检查 API Key 是否已正确配置")
        print("  3. 查看日志文件获取详细错误信息")
        sys.exit(1)


if __name__ == "__main__":
    main()
