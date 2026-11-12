"""
Slack 机器人集成 - LiuHao AI OS 集成示例

本示例演示如何将 LiuHao AI OS 集成到 Slack：
1. 设置 Slack 连接
2. 接收消息并响应
3. 交互式命令（斜杠命令）
4. 消息格式化与附件

运行方式：
    python examples/integrations/slack_bot.py

注意：这是一个示例文件，演示如何使用 Slack API 与 LiuHao AI OS 集成。
实际使用需要配置 Slack App 和 Bot Token。
"""

import sys
import os
import time
import json
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.employee import Agent
from ai.agent_factory import AgentFactory, AgentRole
from ai.providers import get_provider


# ======================================================================
# Slack 消息模型
# ======================================================================

class SlackMessageType(str, Enum):
    """Slack 消息类型。"""
    MESSAGE = "message"
    APP_MENTION = "app_mention"
    COMMAND = "command"
    BLOCK_ACTION = "block_action"
    VIEW_SUBMISSION = "view_submission"


@dataclass
class SlackMessage:
    """Slack 消息。"""
    message_id: str
    channel_id: str
    user_id: str
    user_name: str
    text: str
    message_type: SlackMessageType = SlackMessageType.MESSAGE
    timestamp: float = field(default_factory=time.time)
    thread_ts: Optional[str] = None
    channel_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlackAttachment:
    """Slack 消息附件。"""
    title: str
    text: str
    color: str = "#36a64f"  # good/green
    fields: List[Dict[str, Any]] = field(default_factory=list)
    footer: str = ""
    image_url: str = ""


# ======================================================================
# Slack 机器人客户端
# ======================================================================

class SlackBot:
    """
    Slack 机器人 - 将 LiuHao AI OS 集成到 Slack。

    功能：
    - 接收 Slack 消息
    - 调用 AI Agent 生成回复
    - 发送格式化消息
    - 支持斜杠命令
    - 支持交互式按钮

    使用前需要：
    1. 在 Slack API 平台创建 App
    2. 获取 Bot Token (xoxb-...)
    3. 配置事件订阅和权限
    """

    def __init__(self, bot_token: str = None, app_token: str = None,
                 agent: Agent = None):
        """
        初始化 Slack 机器人。

        Args:
            bot_token: Slack Bot Token (xoxb-...)
            app_token: Slack App-Level Token (xapp-...)，用于 Socket Mode
            agent: 用于生成回复的 AI Agent
        """
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self.agent = agent
        self.bot_user_id = None
        self.bot_name = "LiuHao AI Assistant"

        # 命令处理器
        self._command_handlers: Dict[str, Callable] = {}
        self._event_handlers: Dict[str, Callable] = {}

        # 会话历史（用于上下文）
        self._conversation_history: Dict[str, List[Dict[str, Any]]] = {}

        # 注册默认命令
        self._register_default_commands()

    # ---- 命令注册 ----

    def register_command(self, command: str, handler: Callable):
        """
        注册斜杠命令处理器。

        Args:
            command: 命令名称（如 "/ask"）
            handler: 处理函数，接收 (command, text, message) 参数
        """
        self._command_handlers[command] = handler

    def register_event_handler(self, event_type: str, handler: Callable):
        """注册事件处理器。"""
        self._event_handlers[event_type] = handler

    def _register_default_commands(self):
        """注册默认的斜杠命令。"""
        self.register_command("/ask", self._cmd_ask)
        self.register_command("/help", self._cmd_help)
        self.register_command("/status", self._cmd_status)
        self.register_command("/research", self._cmd_research)

    # ---- 消息发送 ----

    def send_message(self, channel_id: str, text: str,
                     attachments: List[SlackAttachment] = None,
                     thread_ts: str = None) -> bool:
        """
        发送消息到 Slack 频道。

        Args:
            channel_id: 频道 ID
            text: 消息文本
            attachments: 附件列表
            thread_ts: 线程时间戳（用于回复线程）

        Returns:
            是否成功
        """
        # 实际实现会调用 Slack API
        # 这里只是演示
        print(f"  [Slack → #{channel_id}]")
        print(f"    {text[:80]}...")
        if attachments:
            print(f"    附件: {len(attachments)} 个")
        return True

    def send_ephemeral(self, channel_id: str, user_id: str, text: str) -> bool:
        """发送仅特定用户可见的临时消息。"""
        print(f"  [Slack → @{user_id} (仅本人可见)]")
        print(f"    {text[:80]}...")
        return True

    def format_agent_response(self, response_text: str,
                              source: str = "AI 助手") -> SlackAttachment:
        """将 AI 回复格式化为 Slack 附件。"""
        return SlackAttachment(
            title=f"🤖 {source}",
            text=response_text,
            color="#439FE0",  # blue
            footer="LiuHao AI OS",
        )

    # ---- 消息处理 ----

    def handle_message(self, message: SlackMessage) -> Optional[str]:
        """
        处理收到的 Slack 消息。

        Args:
            message: Slack 消息

        Returns:
            回复文本（如果需要回复）
        """
        print(f"\n  [Slack ← #{message.channel_name}] @{message.user_name}:")
        print(f"    {message.text[:100]}...")

        # 保存到会话历史
        if message.channel_id not in self._conversation_history:
            self._conversation_history[message.channel_id] = []
        self._conversation_history[message.channel_id].append({
            "role": "user",
            "text": message.text,
            "user": message.user_name,
            "ts": message.timestamp,
        })

        # 判断是否需要回复
        if not self._should_respond(message):
            return None

        # 使用 AI Agent 生成回复
        if self.agent:
            try:
                # 构建上下文
                context = self._build_context(message)

                # 调用 Agent
                result = self.agent.execute(
                    f"上下文:\n{context}\n\n用户问题:\n{message.text}"
                )
                response = result.get("output", "抱歉，我暂时无法回答这个问题。")

                # 保存回复到历史
                self._conversation_history[message.channel_id].append({
                    "role": "assistant",
                    "text": response,
                    "ts": time.time(),
                })

                # 发送回复
                self.send_message(
                    message.channel_id,
                    response,
                    thread_ts=message.thread_ts,
                )

                return response

            except Exception as e:
                error_msg = f"抱歉，处理您的请求时出错了：{e}"
                self.send_message(message.channel_id, error_msg)
                return error_msg

        return None

    def _should_respond(self, message: SlackMessage) -> bool:
        """判断是否应该回复这条消息。"""
        # 直接提及机器人
        if message.message_type == SlackMessageType.APP_MENTION:
            return True

        # 私信
        if message.channel_id.startswith("D"):  # DM channel
            return True

        # 包含触发词
        trigger_words = ["@ai", "@assistant", "liuhao", "鎏灏"]
        if any(word.lower() in message.text.lower() for word in trigger_words):
            return True

        return False

    def _build_context(self, message: SlackMessage, max_history: int = 5) -> str:
        """构建对话上下文。"""
        history = self._conversation_history.get(message.channel_id, [])
        recent = history[-max_history:]

        context_lines = []
        for msg in recent[:-1]:  # 排除当前消息
            role = msg.get("role", "user")
            text = msg.get("text", "")
            if role == "user":
                context_lines.append(f"用户: {text}")
            else:
                context_lines.append(f"助手: {text}")

        return "\n".join(context_lines) if context_lines else "（无历史对话）"

    # ---- 默认命令处理 ----

    def _cmd_ask(self, command: str, text: str, message: SlackMessage) -> str:
        """/ask 命令 - 向 AI 提问。"""
        if not text:
            return "请输入您的问题，例如：/ask 什么是机器学习？"

        if self.agent:
            try:
                result = self.agent.execute(text)
                return result.get("output", "抱歉，无法生成回答。")
            except Exception as e:
                return f"出错了：{e}"

        return "AI 助手未配置。"

    def _cmd_help(self, command: str, text: str, message: SlackMessage) -> str:
        """/help 命令 - 显示帮助信息。"""
        return """
🤖 LiuHao AI Assistant - 帮助

可用命令：
  /ask <问题>     - 向 AI 提问
  /status         - 查看系统状态
  /research <主题> - 进行快速研究
  /help           - 显示此帮助

直接使用：
  • 在频道中 @我 提问
  • 私信我直接对话
  • 支持多轮对话

示例：
  /ask 解释一下量子计算
  @助手 帮我写一封邮件
""".strip()

    def _cmd_status(self, command: str, text: str, message: SlackMessage) -> str:
        """/status 命令 - 查看状态。"""
        active_channels = len(self._conversation_history)
        total_messages = sum(
            len(h) for h in self._conversation_history.values()
        )

        return f"""
📊 系统状态

  状态: ✅ 在线
  活跃对话: {active_channels} 个
  消息总数: {total_messages} 条
  AI 模型: 已连接
  响应时间: < 3s (平均)
""".strip()

    def _cmd_research(self, command: str, text: str, message: SlackMessage) -> str:
        """/research 命令 - 快速研究。"""
        if not text:
            return "请输入研究主题，例如：/research 可再生能源趋势"

        if self.agent:
            try:
                prompt = f"请简要研究以下主题，给出3-5个要点：{text}"
                result = self.agent.execute(prompt)
                response = result.get("output", "")
                return f"🔍 关于 '{text}' 的研究摘要：\n\n{response}"
            except Exception as e:
                return f"研究失败：{e}"

        return "研究功能暂不可用。"

    # ---- 启动机器人 ----

    def start(self):
        """
        启动 Slack 机器人。

        在实际使用中，会启动 WebSocket 连接（Socket Mode）
        或 HTTP 服务器（Events API）来接收 Slack 事件。
        """
        print("=" * 60)
        print("  🚀 启动 Slack 机器人")
        print("=" * 60)
        print()

        if not self.bot_token:
            print("  ⚠️  未配置 SLACK_BOT_TOKEN，使用演示模式")
            print()
            return

        print(f"  Bot 名称: {self.bot_name}")
        print(f"  已注册命令: {len(self._command_handlers)} 个")
        print()

        # 实际实现会连接到 Slack
        # 这里只是演示
        print("  正在连接 Slack...")
        time.sleep(0.5)
        print("  ✅ 已连接")
        print()
        print("  监听事件中...")

    def handle_command(self, command: str, text: str,
                       message: SlackMessage) -> str:
        """处理斜杠命令。"""
        handler = self._command_handlers.get(command)
        if handler:
            return handler(command, text, message)
        else:
            return f"未知命令：{command}\n输入 /help 查看可用命令。"


# ======================================================================
# 示例 1: 基本消息交互
# ======================================================================

def demo_basic_interaction():
    """
    演示基本的 Slack 消息交互。
    """
    print("=" * 60)
    print("  示例 1：基本消息交互")
    print("=" * 60)
    print()

    # 创建机器人（演示模式）
    bot = SlackBot(bot_token="", agent=None)

    # 模拟收到消息
    print("模拟用户发送消息...")
    print()

    messages = [
        SlackMessage(
            message_id="msg-001",
            channel_id="C12345",
            channel_name="general",
            user_id="U123",
            user_name="alice",
            text="你好，能介绍一下你自己吗？",
            message_type=SlackMessageType.APP_MENTION,
        ),
        SlackMessage(
            message_id="msg-002",
            channel_id="D67890",  # DM
            channel_name="direct_message",
            user_id="U456",
            user_name="bob",
            text="帮我写一段 Python 代码来计算斐波那契数列",
            message_type=SlackMessageType.MESSAGE,
        ),
    ]

    for msg in messages:
        bot.handle_message(msg)
        print()


# ======================================================================
# 示例 2: 斜杠命令
# ======================================================================

def demo_slash_commands():
    """
    演示斜杠命令的使用。
    """
    print("=" * 60)
    print("  示例 2：斜杠命令")
    print("=" * 60)
    print()

    bot = SlackBot()

    commands_to_test = [
        ("/help", "", "显示帮助"),
        ("/status", "", "查看状态"),
        ("/ask", "什么是大语言模型？", "向 AI 提问"),
        ("/research", "AI 在医疗领域的应用", "快速研究"),
    ]

    for command, text, description in commands_to_test:
        print(f"  💬 {command} {text}")
        print(f"     ({description})")

        message = SlackMessage(
            message_id=f"cmd-{command}",
            channel_id="C12345",
            channel_name="general",
            user_id="U123",
            user_name="tester",
            text=text,
            message_type=SlackMessageType.COMMAND,
        )

        response = bot.handle_command(command, text, message)
        print(f"     ↓")
        # 只显示前几行
        lines = response.strip().split("\n")[:5]
        for line in lines:
            print(f"     {line}")
        if len(response.strip().split("\n")) > 5:
            print(f"     ...")
        print()


# ======================================================================
# 示例 3: 多轮对话
# ======================================================================

def demo_multi_turn_conversation():
    """
    演示多轮对话上下文保持。
    """
    print("=" * 60)
    print("  示例 3：多轮对话与上下文")
    print("=" * 60)
    print()

    # 使用模拟 Agent
    from ai.providers import BaseProvider

    class MockProvider(BaseProvider):
        def __init__(self):
            self.name = "mock"
            self.count = 0

        def generate(self, prompt, **kwargs):
            self.count += 1
            responses = [
                "我是 LiuHao AI 助手，有什么可以帮助您的吗？",
                "当然可以！以下是一个简单的 Python 列表排序示例：\n\n"
                "```python\nnums = [3, 1, 4, 1, 5, 9, 2, 6]\nnums.sort()\nprint(nums)\n```",
                "如果想按降序排序，可以添加 reverse=True 参数：\n\n"
                "```python\nnums.sort(reverse=True)\n```\n\n"
                "或者使用 sorted() 函数返回新列表。",
            ]
            return {
                "output": responses[self.count - 1] if self.count <= len(responses)
                          else "好的，我明白了。",
                "latency_ms": 500,
                "tokens_used": 100,
            }

    agent = Agent(
        id="slack-agent",
        agent_type="general",
        name="SlackBot",
        provider=MockProvider(),
        system_prompt="你是一个友好的 AI 助手。",
    )

    bot = SlackBot(agent=agent)

    conversation = [
        ("你好，你是谁？", "初次问候"),
        ("能教我 Python 列表排序吗？", "技术问题"),
        ("那倒序呢？", "追问（依赖上下文）"),
    ]

    print("对话场景：")
    print("-" * 50)

    for i, (text, description) in enumerate(conversation, 1):
        print(f"\n  第 {i} 轮 ({description}):")
        print(f"  👤 用户: {text}")

        message = SlackMessage(
            message_id=f"msg-{i}",
            channel_id="D001",
            channel_name="dm",
            user_id="U001",
            user_name="user",
            text=text,
            message_type=SlackMessageType.MESSAGE,
        )

        response = bot.handle_message(message)
        if response:
            # 显示前两行
            resp_lines = response.strip().split("\n")
            print(f"  🤖 助手: {resp_lines[0][:60]}...")
            if len(resp_lines) > 1:
                print(f"         ({len(resp_lines)} 行内容)")

    print()
    print("💡 多轮对话特性:")
    print("   • 自动维护对话历史")
    print("   • 上下文理解追问")
    print("   • 按频道/用户隔离会话")
    print("   • 可配置历史消息数量")

    print()


# ======================================================================
# 示例 4: 富文本消息
# ======================================================================

def demo_rich_messages():
    """
    演示富文本消息和附件。
    """
    print("=" * 60)
    print("  示例 4：富文本消息与附件")
    print("=" * 60)
    print()

    bot = SlackBot()

    print("发送不同类型的富文本消息:")
    print()

    # 成功消息
    success_attachment = SlackAttachment(
        title="✅ 任务完成",
        text="您的数据分析任务已完成，共处理 1,234 条记录。",
        color="good",  # green
        fields=[
            {"title": "处理记录", "value": "1,234", "short": True},
            {"title": "耗时", "value": "2.3s", "short": True},
            {"title": "成功率", "value": "99.8%", "short": True},
        ],
        footer="LiuHao AI OS",
    )

    print("  1. 成功通知 (绿色)")
    bot.send_message("C123", "任务执行结果：", [success_attachment])

    # 警告消息
    warning_attachment = SlackAttachment(
        title="⚠️ 性能警告",
        text="API 响应时间最近有上升趋势，建议关注。",
        color="warning",  # orange/yellow
        fields=[
            {"title": "当前延迟", "value": "450ms", "short": True},
            {"title": "基线", "value": "200ms", "short": True},
            {"title": "增幅", "value": "+125%", "short": True},
        ],
    )

    print("\n  2. 警告通知 (黄色)")
    bot.send_message("C123", "性能监控告警：", [warning_attachment])

    # 错误消息
    error_attachment = SlackAttachment(
        title="❌ 执行失败",
        text="任务执行过程中遇到错误：数据库连接超时。",
        color="danger",  # red
        fields=[
            {"title": "错误类型", "value": "Timeout", "short": True},
            {"title": "重试次数", "value": "3/3", "short": True},
        ],
        footer="请检查数据库连接配置",
    )

    print("\n  3. 错误通知 (红色)")
    bot.send_message("C123", "任务失败：", [error_attachment])

    # 信息消息
    info_attachment = SlackAttachment(
        title="ℹ️ 每日摘要",
        text="今天系统运行正常，以下是关键指标：",
        color="#439FE0",  # blue
        fields=[
            {"title": "活跃用户", "value": "1,234", "short": True},
            {"title": "API 调用", "value": "45,678", "short": True},
            {"title": "平均响应", "value": "120ms", "short": True},
            {"title": "错误率", "value": "0.1%", "short": True},
        ],
    )

    print("\n  4. 信息摘要 (蓝色)")
    bot.send_message("C123", "每日报告：", [info_attachment])

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有 Slack 集成示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - Slack 机器人集成")
    print("█" * 60)

    try:
        # 示例 1: 基本交互
        demo_basic_interaction()

        # 示例 2: 斜杠命令
        demo_slash_commands()

        # 示例 3: 多轮对话
        demo_multi_turn_conversation()

        # 示例 4: 富文本消息
        demo_rich_messages()

        print("=" * 60)
        print("  Slack 集成功能总结")
        print("=" * 60)
        print("""
  ✓ 消息接收与响应
  ✓ @提及和私信检测
  ✓ 斜杠命令支持
  ✓ 多轮对话上下文
  ✓ 富文本消息附件
  ✓ 按频道会话隔离
  ✓ 状态监控命令
  ✓ 研究助手命令
""")

        print("  配置说明:")
        print("    1. 在 api.slack.com 创建 App")
        print("    2. 添加 Bot Token 权限")
        print("    3. 设置环境变量 SLACK_BOT_TOKEN")
        print("    4. （可选）配置 Socket Mode 或 Events API")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
