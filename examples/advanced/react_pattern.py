"""
ReAct 模式 - LiuHao AI OS 高级示例

本示例演示 ReAct (Reasoning + Acting) 模式：
1. Thought-Action-Observation 循环
2. 推理过程中使用工具
3. 多步骤问题求解
4. 进度追踪

运行方式：
    python examples/advanced/react_pattern.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.reasoning import (
    ReActAgent,
    ReActResult,
    ReActStep,
    ReActStepType,
    ReActDecision,
    ToolResult,
    StopReason,
)
from ai.tools.base import ToolCategory, ResultStatus


# ======================================================================
# 模拟工具集
# ======================================================================

class SimpleCalculator:
    """简单计算器工具。"""

    name = "calculator"
    description = "执行数学计算，支持加减乘除和基本函数"
    category = ToolCategory.COMPUTATION

    def execute(self, expression: str) -> Dict[str, Any]:
        """计算数学表达式。"""
        try:
            # 安全的简单计算（实际使用中应该用更安全的方式）
            allowed_chars = set("0123456789+-*/(). ")
            if not all(c in allowed_chars for c in expression):
                return {
                    "status": ResultStatus.ERROR,
                    "output": None,
                    "error": "表达式包含不允许的字符",
                }

            result = eval(expression, {"__builtins__": {}}, {})
            return {
                "status": ResultStatus.SUCCESS,
                "output": result,
            }
        except Exception as e:
            return {
                "status": ResultStatus.ERROR,
                "output": None,
                "error": str(e),
            }


class WebSearchSimulator:
    """模拟网络搜索工具。"""

    name = "web_search"
    description = "搜索互联网信息，返回相关结果摘要"
    category = ToolCategory.INFORMATION

    # 模拟的搜索结果数据库
    _knowledge_base = {
        "python": {
            "summary": "Python 是一种高级编程语言，以简洁的语法和强大的生态系统著称。",
            "creator": "Guido van Rossum",
            "first_release": "1991年",
            "paradigm": "多范式（面向对象、命令式、函数式）",
        },
        "ai": {
            "summary": "人工智能（AI）是计算机科学的一个分支，旨在创造能够执行通常需要人类智能的任务的机器。",
            "subfields": "机器学习、深度学习、自然语言处理、计算机视觉等",
            "applications": "自动驾驶、医疗诊断、游戏AI、推荐系统等",
        },
        "gdp": {
            "summary": "国内生产总值（GDP）是一个国家在特定时期内生产的所有最终商品和服务的货币价值。",
            "formula": "GDP = 消费 + 投资 + 政府支出 + 净出口",
            "types": "名义GDP、实际GDP、人均GDP",
        },
        "population": {
            "china": "约14亿",
            "usa": "约3.3亿",
            "india": "约14亿",
            "world": "约80亿",
        },
    }

    def execute(self, query: str, max_results: int = 3) -> Dict[str, Any]:
        """模拟搜索。"""
        query_lower = query.lower()
        results = []

        for keyword, info in self._knowledge_base.items():
            if keyword in query_lower or any(keyword in str(v).lower() for v in info.values()):
                results.append({
                    "title": f"关于 {keyword} 的信息",
                    "content": info.get("summary", str(info)),
                    "source": f"wiki/{keyword}",
                    "relevance": 0.8,
                })

        if not results:
            # 通用回复
            results.append({
                "title": f"搜索 '{query}' 的结果",
                "content": f"未找到关于 '{query}' 的详细信息。建议尝试更具体的关键词。",
                "source": "search_engine",
                "relevance": 0.3,
            })

        return {
            "status": ResultStatus.SUCCESS,
            "output": results[:max_results],
            "metadata": {
                "total_results": len(results),
                "query": query,
                "search_time_ms": 45.2,
            },
        }


class WeatherSimulator:
    """模拟天气查询工具。"""

    name = "weather"
    description = "查询指定城市的天气信息"
    category = ToolCategory.INFORMATION

    def execute(self, city: str, date: str = "today") -> Dict[str, Any]:
        """查询天气（模拟数据）。"""
        import random
        random.seed(hash(city + date) % 10000)

        conditions = ["晴", "多云", "阴", "小雨", "中雨", "雷阵雨"]
        condition = random.choice(conditions)
        temp_high = random.randint(20, 35)
        temp_low = random.randint(10, temp_high - 5)
        humidity = random.randint(40, 90)
        wind_speed = random.randint(0, 30)

        return {
            "status": ResultStatus.SUCCESS,
            "output": {
                "city": city,
                "date": date,
                "condition": condition,
                "temperature_high": temp_high,
                "temperature_low": temp_low,
                "humidity": humidity,
                "wind_speed_kmh": wind_speed,
                "uv_index": random.randint(1, 10),
            },
        }


# ======================================================================
# 示例 1: 基础 ReAct 循环
# ======================================================================

def demo_basic_react():
    """
    演示基础的 ReAct (Reasoning + Acting) 循环。

    ReAct 模式的核心是 Thought-Action-Observation 循环：
    1. Thought (思考): 我现在应该做什么？
    2. Action (行动): 调用工具执行操作
    3. Observation (观察): 获取工具执行结果
    4. 重复，直到得出最终答案

    这就像人类解决问题：先想想要怎么做，然后动手做，
    看看结果如何，再决定下一步。
    """
    print("=" * 60)
    print("  示例 1：基础 ReAct 循环")
    print("=" * 60)
    print()

    print("问题: 北京今天的天气怎么样？适合出门吗？")
    print()
    print("ReAct 循环过程:")
    print("-" * 50)

    # 创建 ReAct Agent
    react = ReActAgent(max_steps=10)

    # 注册工具
    tools = {
        "weather": WeatherSimulator(),
        "calculator": SimpleCalculator(),
        "web_search": WebSearchSimulator(),
    }

    # ---- 步骤 1: Thought ----
    thought1 = react.add_step(
        step_type=ReActStepType.THOUGHT,
        content="用户问北京今天的天气，还问是否适合出门。我需要先查询北京今天的天气信息，然后根据天气情况判断是否适合出门。我应该先调用 weather 工具。",
    )
    print("🤔 Thought 1:")
    print(f"   {thought1.content[:80]}...")

    # ---- 步骤 2: Action ----
    action1 = react.add_step(
        step_type=ReActStepType.ACTION,
        content="查询北京今天的天气",
        action_name="weather",
        action_input={"city": "北京", "date": "today"},
    )
    print(f"\n🛠️  Action 1:")
    print(f"   工具: {action1.action_name}")
    print(f"   参数: {action1.action_input}")

    # 执行工具
    weather_tool = tools["weather"]
    weather_result = weather_tool.execute(city="北京", date="today")

    # ---- 步骤 3: Observation ----
    obs1 = react.add_step(
        step_type=ReActStepType.OBSERVATION,
        content=f"北京今天天气: {weather_result['output']['condition']}，"
                f"气温 {weather_result['output']['temperature_low']}"
                f"~{weather_result['output']['temperature_high']}°C，"
                f"湿度 {weather_result['output']['humidity']}%",
        observation=str(weather_result["output"]),
    )
    print(f"\n👁️  Observation 1:")
    print(f"   {obs1.content[:100]}...")

    # ---- 步骤 4: Thought（分析观察结果）----
    thought2 = react.add_step(
        step_type=ReActStepType.THOUGHT,
        content="我已经获取了北京今天的天气信息。现在需要判断是否适合出门。"
                "我需要考虑天气状况、温度、湿度等因素。"
                "让我分析一下：如果是晴天或多云，温度适宜，就适合出门。",
    )
    print(f"\n🤔 Thought 2:")
    print(f"   {thought2.content[:80]}...")

    # ---- 步骤 5: Final Answer ----
    final = react.add_step(
        step_type=ReActStepType.FINAL_ANSWER,
        content=f"根据查询结果，北京今天{weather_result['output']['condition']}，"
                f"气温在 {weather_result['output']['temperature_low']}"
                f"~{weather_result['output']['temperature_high']}°C 之间。"
                f"{'天气不错，适合出门活动！' if weather_result['output']['condition'] in ['晴', '多云'] else '建议根据天气情况做好准备再出门。'}",
    )
    print(f"\n🎯 Final Answer:")
    print(f"   {final.content[:100]}...")

    # ---- 获取结果 ----
    result = react.get_result()

    print()
    print("-" * 50)
    print(f"总步数: {result.total_steps}")
    print(f"停止原因: {result.stop_reason.value}")
    print(f"工具调用次数: {result.tools_used}")

    print()
    return react, result


# ======================================================================
# 示例 2: 多步骤问题求解
# ======================================================================

def demo_multi_step_problem():
    """
    演示需要多步骤工具调用的复杂问题求解。

    有些问题不能一步回答，需要：
    1. 先搜索相关信息
    2. 再进行计算
    3. 综合得出答案

    这个过程就是 ReAct 的强项。
    """
    print("=" * 60)
    print("  示例 2：多步骤问题求解")
    print("=" * 60)
    print()

    question = "如果一个长方形的长是 15 米，宽是 8 米，它的面积是多少？" \
               "如果每平方米可以种 3 棵树，总共可以种多少棵树？"

    print(f"问题: {question}")
    print()
    print("ReAct 求解过程:")
    print("-" * 50)

    react = ReActAgent(max_steps=15)
    calc = SimpleCalculator()

    # 步骤 1: 理解问题
    t1 = react.add_step(
        ReActStepType.THOUGHT,
        "这个问题分两部分：先算长方形面积，再算可以种多少棵树。"
        "面积 = 长 × 宽，然后用面积 × 每平方米的树的数量。"
        "我需要用计算器来计算。",
    )
    print(f"🤔 Thought 1: 分解问题，确定需要计算...")

    # 步骤 2: 计算面积
    a1 = react.add_step(
        ReActStepType.ACTION,
        "计算长方形面积：长 × 宽 = 15 × 8",
        action_name="calculator",
        action_input={"expression": "15 * 8"},
    )
    print(f"🛠️  Action 1: 计算面积 (15 * 8)")

    result1 = calc.execute("15 * 8")
    o1 = react.add_step(
        ReActStepType.OBSERVATION,
        f"计算结果: 15 × 8 = {result1['output']} 平方米",
        observation=str(result1["output"]),
    )
    print(f"👁️  Observation 1: 面积 = {result1['output']} ㎡")

    # 步骤 3: 思考下一步
    t2 = react.add_step(
        ReActStepType.THOUGHT,
        f"面积是 {result1['output']} 平方米。现在需要计算总共可以种多少棵树："
        f"面积 × 每平方米 3 棵 = {result1['output']} × 3",
    )
    print(f"🤔 Thought 2: 面积已知，计算总树数...")

    # 步骤 4: 计算总树数
    a2 = react.add_step(
        ReActStepType.ACTION,
        f"计算总树数: {result1['output']} × 3",
        action_name="calculator",
        action_input={"expression": f"{result1['output']} * 3"},
    )
    print(f"🛠️  Action 2: 计算总树数 ({result1['output']} * 3)")

    result2 = calc.execute(f"{result1['output']} * 3")
    o2 = react.add_step(
        ReActStepType.OBSERVATION,
        f"计算结果: {result1['output']} × 3 = {result2['output']} 棵",
        observation=str(result2["output"]),
    )
    print(f"👁️  Observation 2: 总树数 = {result2['output']} 棵")

    # 步骤 5: 验证和总结
    t3 = react.add_step(
        ReActStepType.THOUGHT,
        f"让我验证一下：15 × 8 = {result1['output']}（面积正确），"
        f"{result1['output']} × 3 = {result2['output']}（总树数正确）。"
        f"计算过程没有问题，可以给出最终答案了。",
    )
    print(f"🤔 Thought 3: 验证计算结果...")

    # 步骤 6: 最终答案
    final = react.add_step(
        ReActStepType.FINAL_ANSWER,
        f"答案：\n"
        f"1. 长方形面积 = 长 × 宽 = 15m × 8m = {result1['output']} 平方米\n"
        f"2. 总树数 = 面积 × 每平方米树数 = {result1['output']} × 3 = {result2['output']} 棵\n\n"
        f"因此，这块地总共可以种 {result2['output']} 棵树。",
    )
    print(f"🎯 Final Answer: 得出最终答案")

    result = react.get_result()
    print()
    print("-" * 50)
    print(f"解题步骤: {result.total_steps} 步")
    print(f"工具调用: {result.tools_used} 次")
    print(f"停止原因: {result.stop_reason.value}")

    # 打印进度
    print()
    print("📊 进度追踪:")
    for i, step in enumerate(react.steps, 1):
        progress = i / result.total_steps * 100
        icon = {
            "thought": "🤔",
            "action": "🛠️",
            "observation": "👁️",
            "final_answer": "🎯",
        }.get(step.step_type.value, "•")
        print(f"  {i:2d}/{result.total_steps} {icon} {step.step_type.value:15s} {progress:.0f}%")

    print()
    return react, result


# ======================================================================
# 示例 3: 工具使用策略
# ======================================================================

def demo_tool_selection():
    """
    演示 ReAct Agent 如何选择和使用不同的工具。

    面对复杂问题时，Agent 需要：
    1. 判断需要什么工具
    2. 正确调用工具
    3. 解释工具返回的结果
    4. 决定是否需要更多工具调用
    """
    print("=" * 60)
    print("  示例 3：工具选择与组合使用")
    print("=" * 60)
    print()

    question = "Python 是什么时候发布的？到 2024 年有多少年了？"
    print(f"问题: {question}")
    print()
    print("解题过程（需要搜索 + 计算配合）:")
    print("-" * 50)

    react = ReActAgent(max_steps=10)
    search = WebSearchSimulator()
    calc = SimpleCalculator()

    # Thought 1: 分析需要什么工具
    t1 = react.add_step(
        ReActStepType.THOUGHT,
        "用户问 Python 发布时间和到 2024 年的年数。"
        "我需要先搜索 Python 的发布年份，然后用计算器计算年数差。"
        "先调用 web_search 工具。",
    )
    print(f"🤔 Thought 1: 需要先搜索，再计算...")

    # Action 1: 搜索
    a1 = react.add_step(
        ReActStepType.ACTION,
        "搜索 Python 的发布时间",
        action_name="web_search",
        action_input={"query": "Python 发布时间 first release"},
    )
    print(f"🛠️  Action 1: 搜索 Python 发布时间")

    search_result = search.execute("Python first release year")
    o1 = react.add_step(
        ReActStepType.OBSERVATION,
        f"搜索结果显示 Python 由 Guido van Rossum 创建，首次发布于 1991 年。",
        observation=str(search_result["output"]),
    )
    print(f"👁️  Observation 1: Python 首次发布于 1991 年")

    # Thought 2: 决定下一步
    t2 = react.add_step(
        ReActStepType.THOUGHT,
        "找到了 Python 发布于 1991 年。现在需要计算到 2024 年有多少年。"
        "这是一个简单的减法：2024 - 1991。我用计算器来算。",
    )
    print(f"🤔 Thought 2: 计算年数差...")

    # Action 2: 计算
    a2 = react.add_step(
        ReActStepType.ACTION,
        "计算 2024 - 1991",
        action_name="calculator",
        action_input={"expression": "2024 - 1991"},
    )
    print(f"🛠️  Action 2: 计算 2024 - 1991")

    calc_result = calc.execute("2024 - 1991")
    o2 = react.add_step(
        ReActStepType.OBSERVATION,
        f"计算结果: 2024 - 1991 = {calc_result['output']} 年",
        observation=str(calc_result["output"]),
    )
    print(f"👁️  Observation 2: 相差 {calc_result['output']} 年")

    # Final Answer
    final = react.add_step(
        ReActStepType.FINAL_ANSWER,
        f"Python 编程语言由 Guido van Rossum 创建，首次发布于 1991 年。\n\n"
        f"到 2024 年，Python 已经有 {calc_result['output']} 年的历史了。\n\n"
        f"在这 {calc_result['output']} 年间，Python 从一个简单的脚本语言"
        f"发展成为世界上最流行的编程语言之一。",
    )
    print(f"🎯 Final Answer: 综合搜索和计算结果")

    result = react.get_result()
    print()
    print("-" * 50)
    print(f"使用的工具: {[s.action_name for s in react.steps if s.action_name]}")
    print(f"总步数: {result.total_steps}")

    print()
    return react, result


# ======================================================================
# 示例 4: 进度追踪与停止条件
# ======================================================================

def demo_progress_tracking():
    """
    演示 ReAct 的进度追踪和各种停止条件。

    ReAct 循环可能因为以下原因停止：
    1. GOAL_REACHED - 找到了最终答案
    2. MAX_STEPS - 达到最大步数限制（防止无限循环）
    3. NO_PROGRESS - 长时间没有进展
    4. ERROR - 遇到不可恢复的错误
    5. USER_INTERRUPT - 用户中断
    """
    print("=" * 60)
    print("  示例 4：进度追踪与停止条件")
    print("=" * 60)
    print()

    print("演示达到最大步数的停止条件:")
    print("-" * 50)

    # 设置一个很小的 max_steps 来演示
    react = ReActAgent(max_steps=3)

    # 添加步骤直到达到限制
    react.add_step(ReActStepType.THOUGHT, "思考中...")
    print(f"  步骤 1: Thought (当前: {react.current_step}/{react.max_steps})")

    react.add_step(ReActStepType.ACTION, "调用工具", action_name="test", action_input={})
    print(f"  步骤 2: Action (当前: {react.current_step}/{react.max_steps})")

    react.add_step(ReActStepType.OBSERVATION, "观察结果")
    print(f"  步骤 3: Observation (当前: {react.current_step}/{react.max_steps})")

    # 检查是否应该停止
    should_stop, reason = react.should_stop()
    print(f"\n  达到最大步数限制！")
    print(f"  should_stop: {should_stop}")
    print(f"  stop_reason: {reason.value}")

    # 重置后演示正常停止
    print()
    print("演示找到答案后的正常停止:")
    print("-" * 50)

    react2 = ReActAgent(max_steps=10)
    react2.add_step(ReActStepType.THOUGHT, "我已经有足够的信息了")
    react2.add_step(ReActStepType.FINAL_ANSWER, "这是最终答案")

    should_stop2, reason2 = react2.should_stop()
    print(f"  添加 Final Answer 后")
    print(f"  should_stop: {should_stop2}")
    print(f"  stop_reason: {reason2.value}")

    # 进度百分比
    progress = react2.get_progress()
    print(f"  进度: {progress:.0%}")

    print()
    print("💡 停止条件说明:")
    print("   • GOAL_REACHED: 生成了 final_answer，目标达成")
    print("   • MAX_STEPS: 达到最大步数，防止无限循环")
    print("   • NO_PROGRESS: 连续多步没有实质进展")
    print("   • ERROR: 遇到严重错误，无法继续")
    print("   • USER_INTERRUPT: 用户主动中断")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有 ReAct 示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - ReAct (Reasoning + Acting) 模式")
    print("█" * 60)

    try:
        # 示例 1: 基础 ReAct
        demo_basic_react()

        # 示例 2: 多步骤问题
        demo_multi_step_problem()

        # 示例 3: 工具选择
        demo_tool_selection()

        # 示例 4: 进度追踪
        demo_progress_tracking()

        print("=" * 60)
        print("  ReAct 模式功能总结")
        print("=" * 60)
        print("""
  ✓ Thought-Action-Observation 循环
  ✓ 多工具集成与调用
  ✓ 多步骤问题求解
  ✓ 进度实时追踪
  ✓ 多种停止条件
  ✓ 完整推理记录
  ✓ 工具结果观察与分析
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
