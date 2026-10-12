"""
客户支持机器人 - LiuHao AI OS 用例示例

本示例演示如何构建一个客户支持机器人，功能包括：
1. 工单接收与分类 (Intake → Triage)
2. 知识库检索与自动回复
3. 回复质量检查
4. 升级流程 (Escalation)
5. 服务指标追踪

运行方式：
    python examples/use_cases/customer_support_bot.py
"""

import sys
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.agent_factory import AgentFactory, AgentRole
from ai.memory import SemanticMemoryNetwork, EntityType, RelationType
from ai.memory import WorkingMemory, MemoryPriority
from ai.employee import Agent
from ai.providers import get_provider


# ======================================================================
# 数据模型
# ======================================================================

class TicketStatus(str, Enum):
    """工单状态。"""
    NEW = "new"                    # 新建
    TRIAGED = "triaged"            # 已分类
    IN_PROGRESS = "in_progress"    # 处理中
    WAITING_CUSTOMER = "waiting_customer"  # 等待客户回复
    ESCALATED = "escalated"        # 已升级
    RESOLVED = "resolved"          # 已解决
    CLOSED = "closed"              # 已关闭


class TicketPriority(str, Enum):
    """工单优先级。"""
    LOW = "low"           # 低 - 一般咨询
    MEDIUM = "medium"     # 中 - 功能问题
    HIGH = "high"         # 高 - 影响使用
    URGENT = "urgent"     # 紧急 - 系统宕机


class TicketCategory(str, Enum):
    """工单类别。"""
    TECHNICAL = "technical"       # 技术问题
    BILLING = "billing"           # 账单问题
    ACCOUNT = "account"           # 账户问题
    FEATURE_REQUEST = "feature"   # 功能需求
    GENERAL_INQUIRY = "general"   # 一般咨询
    BUG_REPORT = "bug"            # Bug 报告


class ResponseQuality(str, Enum):
    """回复质量等级。"""
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


@dataclass
class SupportTicket:
    """客户支持工单。"""
    ticket_id: str
    customer_id: str
    customer_name: str
    subject: str
    description: str
    category: TicketCategory = TicketCategory.GENERAL_INQUIRY
    priority: TicketPriority = TicketPriority.MEDIUM
    status: TicketStatus = TicketStatus.NEW
    assigned_to: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    satisfaction_score: Optional[int] = None  # 1-5
    resolution_time_seconds: Optional[float] = None


@dataclass
class KnowledgeArticle:
    """知识库文章。"""
    article_id: str
    title: str
    content: str
    category: str
    tags: List[str] = field(default_factory=list)
    views: int = 0
    helpful_count: int = 0
    not_helpful_count: int = 0
    last_updated: float = field(default_factory=time.time)


@dataclass
class SupportMetrics:
    """支持服务指标。"""
    total_tickets: int = 0
    resolved_tickets: int = 0
    escalated_tickets: int = 0
    avg_resolution_time: float = 0.0
    avg_first_response_time: float = 0.0
    avg_satisfaction_score: float = 0.0
    tickets_by_category: Dict[str, int] = field(default_factory=dict)
    tickets_by_priority: Dict[str, int] = field(default_factory=dict)
    auto_resolved_count: int = 0
    human_resolved_count: int = 0


# ======================================================================
# 知识库
# ======================================================================

class KnowledgeBase:
    """
    支持知识库 - 存储常见问题和解决方案。

    使用语义网络进行结构化存储和智能检索。
    """

    def __init__(self):
        self.articles: Dict[str, KnowledgeArticle] = {}
        self.semantic_net = SemanticMemoryNetwork()
        self._load_sample_articles()

    def _load_sample_articles(self):
        """加载示例知识库文章。"""
        sample_articles = [
            KnowledgeArticle(
                article_id="kb-001",
                title="如何重置密码",
                content="1. 点击登录页面的'忘记密码'链接\n"
                        "2. 输入您的注册邮箱\n"
                        "3. 查收重置密码邮件\n"
                        "4. 点击邮件中的链接设置新密码\n\n"
                        "如果没有收到邮件，请检查垃圾邮件文件夹。",
                category="account",
                tags=["密码", "登录", "账户安全"],
            ),
            KnowledgeArticle(
                article_id="kb-002",
                title="如何升级套餐",
                content="您可以通过以下步骤升级套餐：\n"
                        "1. 登录后进入'设置' > '订阅管理'\n"
                        "2. 点击'升级套餐'\n"
                        "3. 选择您想要的套餐\n"
                        "4. 确认支付信息\n\n"
                        "升级立即生效，费用按比例计算。",
                category="billing",
                tags=["升级", "套餐", "订阅", "支付"],
            ),
            KnowledgeArticle(
                article_id="kb-003",
                title="系统无法登录怎么办",
                content="如果您无法登录系统，请尝试以下步骤：\n\n"
                        "1. 检查用户名和密码是否正确\n"
                        "2. 确认网络连接正常\n"
                        "3. 清除浏览器缓存和 Cookie\n"
                        "4. 尝试使用其他浏览器\n"
                        "5. 如果以上都不行，请联系技术支持。",
                category="technical",
                tags=["登录", "故障排除", "技术支持"],
            ),
            KnowledgeArticle(
                article_id="kb-004",
                title="数据导出功能说明",
                content="系统支持多种数据导出格式：\n\n"
                        "- CSV 格式：适合电子表格处理\n"
                        "- JSON 格式：适合程序接口使用\n"
                        "- Excel 格式：适合报表展示\n"
                        "- PDF 格式：适合打印和存档\n\n"
                        "导出路径：设置 > 数据管理 > 导出数据",
                category="technical",
                tags=["导出", "数据", "CSV", "JSON", "Excel"],
            ),
            KnowledgeArticle(
                article_id="kb-005",
                title="退款政策",
                content="我们的退款政策如下：\n\n"
                        "1. 7天无理由退款：购买后7天内可申请全额退款\n"
                        "2. 部分退款：使用不足一个月按比例退款\n"
                        "3. 不予退款：已使用超过30天或已消耗大量资源\n\n"
                        "退款申请请联系客服处理。",
                category="billing",
                tags=["退款", "支付", "政策"],
            ),
            KnowledgeArticle(
                article_id="kb-006",
                title="API 接口调用频率限制",
                content="为保证系统稳定性，API 调用有以下限制：\n\n"
                        "- 免费版：100 次/分钟\n"
                        "- 专业版：1000 次/分钟\n"
                        "- 企业版：10000 次/分钟\n\n"
                        "超过限制会返回 429 状态码。"
                        "如需提高限额，请联系销售团队。",
                category="technical",
                tags=["API", "限流", "接口"],
            ),
            KnowledgeArticle(
                article_id="kb-007",
                title="如何联系人工客服",
                content="您可以通过以下方式联系我们的客服团队：\n\n"
                        "1. 在线客服：工作日 9:00-18:00\n"
                        "2. 邮件支持：support@example.com\n"
                        "3. 电话支持：400-XXX-XXXX（工作日 9:00-18:00）\n"
                        "4. 社区论坛：随时可以提问\n\n"
                        "紧急问题请选择在线客服或电话支持。",
                category="general",
                tags=["客服", "联系方式", "支持"],
            ),
        ]

        for article in sample_articles:
            self.add_article(article)

    def add_article(self, article: KnowledgeArticle):
        """添加知识库文章。"""
        self.articles[article.article_id] = article

        # 同时存入语义网络
        entity_id = self.semantic_net.add_entity(
            name=article.title,
            entity_type=EntityType.DOCUMENT,
            description=article.content[:200],
            tags=article.tags + [article.category],
        )
        article.article_id = entity_id  # 使用语义网络的 ID

    def search(self, query: str, top_k: int = 3) -> List[KnowledgeArticle]:
        """
        搜索相关知识库文章。

        Args:
            query: 查询关键词
            top_k: 返回最相关的前 K 篇

        Returns:
            相关文章列表，按相关度排序
        """
        # 简单的关键词匹配搜索
        # 实际应用中会使用向量检索
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored_articles = []
        for article in self.articles.values():
            score = 0.0

            # 标题匹配
            title_lower = article.title.lower()
            for word in query_words:
                if word in title_lower:
                    score += 3.0

            # 内容匹配
            content_lower = article.content.lower()
            for word in query_words:
                if word in content_lower:
                    score += 1.0

            # 标签匹配
            for tag in article.tags:
                if tag.lower() in query_lower:
                    score += 2.0

            # 类别匹配
            if article.category.lower() in query_lower:
                score += 1.5

            if score > 0:
                scored_articles.append((score, article))

        # 按分数排序
        scored_articles.sort(key=lambda x: x[0], reverse=True)

        return [article for _, article in scored_articles[:top_k]]

    def record_feedback(self, article_id: str, helpful: bool):
        """记录用户对文章的反馈。"""
        if article_id in self.articles:
            if helpful:
                self.articles[article_id].helpful_count += 1
            else:
                self.articles[article_id].not_helpful_count += 1


# ======================================================================
# 客户支持机器人
# ======================================================================

class CustomerSupportBot:
    """
    客户支持机器人 - 自动化客户服务工作流。

    工作流程（Intake → Triage → Respond → Escalate）：
    1. 接收工单 - 解析用户问题
    2. 工单分类 - 判断类别和优先级
    3. 知识检索 - 查找相关解决方案
    4. 生成回复 - 基于知识库生成回答
    5. 质量检查 - 验证回复质量
    6. 发送回复 - 回复客户
    7. 升级处理 - 无法解决时升级人工

    同时追踪服务指标，持续优化服务质量。
    """

    def __init__(self, support_agent: Agent, quality_agent: Agent,
                 knowledge_base: KnowledgeBase = None):
        """
        初始化客户支持机器人。

        Args:
            support_agent: 负责回答问题的客服 Agent
            quality_agent: 负责质量检查的 QA Agent
            knowledge_base: 知识库
        """
        self.support_agent = support_agent
        self.quality_agent = quality_agent
        self.kb = knowledge_base or KnowledgeBase()
        self.tickets: Dict[str, SupportTicket] = {}
        self.metrics = SupportMetrics()
        self.work_memory = WorkingMemory(capacity=50)

        # 回调函数（可自定义）
        self.on_ticket_created: Optional[Callable] = None
        self.on_ticket_resolved: Optional[Callable] = None
        self.on_ticket_escalated: Optional[Callable] = None

    # ---- 阶段 1: 工单创建 (Intake) ----

    def create_ticket(self, customer_id: str, customer_name: str,
                      subject: str, description: str) -> SupportTicket:
        """
        创建一个新的支持工单。

        Args:
            customer_id: 客户 ID
            customer_name: 客户名称
            subject: 工单主题
            description: 问题描述

        Returns:
            新建的工单
        """
        ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"

        ticket = SupportTicket(
            ticket_id=ticket_id,
            customer_id=customer_id,
            customer_name=customer_name,
            subject=subject,
            description=description,
            status=TicketStatus.NEW,
            messages=[{
                "role": "customer",
                "content": description,
                "timestamp": time.time(),
            }],
        )

        self.tickets[ticket_id] = ticket
        self.metrics.total_tickets += 1

        # 存入工作记忆
        self.work_memory.add(
            content=f"工单 {ticket_id}: {subject}",
            item_type="task",
            priority=MemoryPriority.HIGH,
            source="customer",
            tags=["ticket", "new"],
        )

        # 触发回调
        if self.on_ticket_created:
            self.on_ticket_created(ticket)

        return ticket

    # ---- 阶段 2: 工单分类 (Triage) ----

    def triage_ticket(self, ticket: SupportTicket) -> SupportTicket:
        """
        对工单进行分类和优先级判定。

        根据问题内容自动判断：
        - 工单类别（技术、账单、账户等）
        - 优先级（低、中、高、紧急）

        Args:
            ticket: 待分类的工单

        Returns:
            更新后的工单
        """
        ticket.status = TicketStatus.TRIAGED
        ticket.updated_at = time.time()

        # 简单的关键词分类
        text = (ticket.subject + " " + ticket.description).lower()

        # 类别判断
        category_keywords = {
            TicketCategory.TECHNICAL: ["错误", "bug", "无法", "故障", "技术", "api", "接口", "报错"],
            TicketCategory.BILLING: ["支付", "账单", "费用", "退款", "套餐", "订阅", "价格"],
            TicketCategory.ACCOUNT: ["账户", "账号", "密码", "登录", "注册", "验证"],
            TicketCategory.FEATURE_REQUEST: ["建议", "希望", "功能", "增加", "添加"],
            TicketCategory.BUG_REPORT: ["bug", "缺陷", "问题", "异常", "崩溃"],
        }

        category_scores = {}
        for category, keywords in category_keywords.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                category_scores[category] = score

        if category_scores:
            ticket.category = max(category_scores, key=category_scores.get)
        else:
            ticket.category = TicketCategory.GENERAL_INQUIRY

        # 优先级判断
        priority_keywords = {
            TicketPriority.URGENT: ["紧急", "宕机", "无法使用", "崩溃", "全部", "所有"],
            TicketPriority.HIGH: ["重要", "严重", "影响", "无法工作", "尽快"],
            TicketPriority.LOW: ["咨询", "请问", "建议", "希望", "能不能"],
        }

        priority_score = TicketPriority.MEDIUM
        for priority, keywords in priority_keywords.items():
            if any(kw in text for kw in keywords):
                priority_score = priority
                break

        ticket.priority = priority_score

        # 更新统计
        cat = ticket.category.value
        pri = ticket.priority.value
        self.metrics.tickets_by_category[cat] = self.metrics.tickets_by_category.get(cat, 0) + 1
        self.metrics.tickets_by_priority[pri] = self.metrics.tickets_by_priority.get(pri, 0) + 1

        return ticket

    # ---- 阶段 3: 生成回复 (Respond) ----

    def generate_response(self, ticket: SupportTicket) -> str:
        """
        基于知识库生成回复。

        步骤：
        1. 搜索相关知识库文章
        2. 使用 Agent 生成个性化回复
        3. 进行质量检查

        Args:
            ticket: 工单

        Returns:
            生成的回复文本
        """
        # 搜索知识库
        query = ticket.subject + " " + ticket.description
        related_articles = self.kb.search(query, top_k=3)

        if not related_articles:
            # 没有找到相关文章，升级处理
            return self._generate_fallback_response(ticket)

        # 构建回复提示
        articles_text = "\n\n".join([
            f"【{article.title}】\n{article.content}"
            for article in related_articles
        ])

        prompt = f"""
        你是一名专业的客户支持代表。请根据知识库内容回答客户问题。

        客户问题：
        主题：{ticket.subject}
        描述：{ticket.description}

        相关知识库文章：
        {articles_text}

        请生成一个友好、专业的回复，要求：
        1. 直接回答客户的问题
        2. 引用相关的知识库内容
        3. 语气友好，表达同理心
        4. 如果有多种解决方案，按优先级列出
        5. 如果需要更多信息，请明确说明

        回复格式：
        - 问候
        - 解决方案（分步骤或分点说明）
        - 后续帮助提示
        - 结束语
        """

        try:
            result = self.support_agent.execute(prompt)
            response = result.get("output", "")
        except Exception:
            # 如果 Agent 调用失败，使用模板回复
            response = self._generate_template_response(ticket, related_articles)

        # 质量检查
        quality_check = self._check_response_quality(ticket, response, related_articles)

        if quality_check["passed"]:
            return response
        else:
            # 质量不达标，尝试改进
            improved = self._improve_response(ticket, response, quality_check)
            return improved

    def _generate_template_response(self, ticket: SupportTicket,
                                     articles: List[KnowledgeArticle]) -> str:
        """生成模板化回复（备用方案）。"""
        greeting = f"尊敬的 {ticket.customer_name}，您好！\n\n"
        body = "感谢您联系我们的客服团队。根据您的问题，我们找到了以下可能有帮助的信息：\n\n"

        for i, article in enumerate(articles, 1):
            body += f"{i}. {article.title}\n{article.content}\n\n"

        closing = (
            "如果以上信息不能解决您的问题，请回复此邮件提供更多细节，"
            "我们会尽快安排专人跟进。\n\n"
            "祝您生活愉快！\n"
            "客户支持团队"
        )

        return greeting + body + closing

    def _generate_fallback_response(self, ticket: SupportTicket) -> str:
        """生成兜底回复（未找到相关知识）。"""
        return (
            f"尊敬的 {ticket.customer_name}，您好！\n\n"
            f"感谢您联系我们。您的问题『{ticket.subject}』已收到，"
            f"我们正在为您查找解决方案。\n\n"
            f"由于您的问题较为特殊，我们已将您的工单升级给技术团队处理，"
            f"预计会在 1 个工作日内给您回复。\n\n"
            f"工单编号：{ticket.ticket_id}\n\n"
            f"感谢您的耐心等待！\n"
            f"客户支持团队"
        )

    # ---- 阶段 4: 质量检查 ----

    def _check_response_quality(self, ticket: SupportTicket, response: str,
                                 articles: List[KnowledgeArticle]) -> Dict[str, Any]:
        """
        检查回复质量。

        检查维度：
        1. 相关性 - 是否回答了客户的问题
        2. 完整性 - 是否提供了足够的信息
        3. 友好性 - 语气是否友好专业
        4. 准确性 - 信息是否准确

        Args:
            ticket: 工单
            response: 生成的回复
            articles: 引用的知识库文章

        Returns:
            质量检查结果
        """
        # 简化版质量检查
        # 实际应用中会使用 QA Agent 进行深度检查
        checks = {
            "has_greeting": any(
                word in response for word in ["您好", "你好", "尊敬", "hi", "hello"]
            ),
            "has_solution": len(response) > 100,  # 有一定长度
            "has_closing": any(
                word in response for word in ["感谢", "谢谢", "祝", "best", "regards"]
            ),
            "mentions_knowledge": any(
                article.title[:10] in response for article in articles
            ),
            "appropriate_length": 100 < len(response) < 2000,
        }

        passed_checks = sum(checks.values())
        total_checks = len(checks)
        score = passed_checks / total_checks

        # 计算质量等级
        if score >= 0.8:
            quality = ResponseQuality.EXCELLENT
        elif score >= 0.6:
            quality = ResponseQuality.GOOD
        elif score >= 0.4:
            quality = ResponseQuality.FAIR
        else:
            quality = ResponseQuality.POOR

        return {
            "passed": score >= 0.5,
            "score": score,
            "quality": quality,
            "checks": checks,
            "issues": [k for k, v in checks.items() if not v],
        }

    def _improve_response(self, ticket: SupportTicket, response: str,
                          quality_check: Dict[str, Any]) -> str:
        """尝试改进回复质量。"""
        # 简单的改进策略
        improved = response

        if "has_greeting" in quality_check["issues"]:
            improved = f"尊敬的 {ticket.customer_name}，您好！\n\n" + improved

        if "has_closing" in quality_check["issues"]:
            improved += (
                "\n\n如果您还有其他问题，请随时联系我们。\n"
                "祝您生活愉快！\n"
                "客户支持团队"
            )

        return improved

    # ---- 阶段 5: 处理工单 ----

    def process_ticket(self, ticket: SupportTicket) -> SupportTicket:
        """
        处理工单的完整流程。

        Args:
            ticket: 工单

        Returns:
            处理后的工单
        """
        print(f"\n  处理工单: {ticket.ticket_id}")
        print(f"    客户: {ticket.customer_name}")
        print(f"    主题: {ticket.subject}")

        # 步骤 1: 分类
        print("    [1/4] 工单分类...")
        self.triage_ticket(ticket)
        print(f"         类别: {ticket.category.value}")
        print(f"         优先级: {ticket.priority.value}")

        # 步骤 2: 搜索知识库
        print("    [2/4] 搜索知识库...")
        query = ticket.subject + " " + ticket.description
        articles = self.kb.search(query, top_k=3)
        print(f"         找到 {len(articles)} 篇相关文章")
        for art in articles:
            print(f"           - {art.title}")

        # 步骤 3: 生成回复
        print("    [3/4] 生成回复...")
        response = self.generate_response(ticket)
        first_response_time = time.time() - ticket.created_at

        # 添加消息
        ticket.messages.append({
            "role": "agent",
            "content": response,
            "timestamp": time.time(),
            "auto_generated": True,
        })

        # 更新首次响应时间
        if self.metrics.avg_first_response_time == 0:
            self.metrics.avg_first_response_time = first_response_time
        else:
            self.metrics.avg_first_response_time = (
                self.metrics.avg_first_response_time * 0.9 + first_response_time * 0.1
            )

        print(f"         回复长度: {len(response)} 字符")
        print(f"         响应时间: {first_response_time:.1f} 秒")

        # 步骤 4: 判断是否需要升级
        print("    [4/4] 判断处理方式...")
        if len(articles) == 0 or ticket.priority == TicketPriority.URGENT:
            self.escalate_ticket(ticket, "需要人工介入处理")
            print(f"         → 已升级人工处理")
        else:
            ticket.status = TicketStatus.RESOLVED
            ticket.resolved_at = time.time()
            ticket.resolution_time_seconds = time.time() - ticket.created_at
            self.metrics.resolved_tickets += 1
            self.metrics.auto_resolved_count += 1

            # 更新平均解决时间
            if self.metrics.avg_resolution_time == 0:
                self.metrics.avg_resolution_time = ticket.resolution_time_seconds
            else:
                self.metrics.avg_resolution_time = (
                    self.metrics.avg_resolution_time * 0.9
                    + ticket.resolution_time_seconds * 0.1
                )

            print(f"         → 已自动解决")

            if self.on_ticket_resolved:
                self.on_ticket_resolved(ticket)

        ticket.updated_at = time.time()
        return ticket

    # ---- 升级流程 (Escalation) ----

    def escalate_ticket(self, ticket: SupportTicket, reason: str):
        """
        将工单升级给人工处理。

        Args:
            ticket: 工单
            reason: 升级原因
        """
        ticket.status = TicketStatus.ESCALATED
        ticket.assigned_to = "human_agent"
        ticket.updated_at = time.time()
        self.metrics.escalated_tickets += 1

        ticket.messages.append({
            "role": "system",
            "content": f"工单已升级，原因: {reason}",
            "timestamp": time.time(),
            "escalation_reason": reason,
        })

        if self.on_ticket_escalated:
            self.on_ticket_escalated(ticket, reason)

    # ---- 客户反馈 ----

    def record_satisfaction(self, ticket_id: str, score: int):
        """
        记录客户满意度评分。

        Args:
            ticket_id: 工单 ID
            score: 满意度评分 (1-5)
        """
        if ticket_id in self.tickets:
            ticket = self.tickets[ticket_id]
            ticket.satisfaction_score = score

            # 更新平均满意度
            total = self.metrics.avg_satisfaction_score * (self.metrics.resolved_tickets - 1)
            total += score
            self.metrics.avg_satisfaction_score = total / max(self.metrics.resolved_tickets, 1)

    # ---- 指标报告 ----

    def get_metrics_report(self) -> Dict[str, Any]:
        """
        获取服务指标报告。

        Returns:
            指标数据字典
        """
        return {
            "total_tickets": self.metrics.total_tickets,
            "resolved_tickets": self.metrics.resolved_tickets,
            "escalated_tickets": self.metrics.escalated_tickets,
            "resolution_rate": (
                self.metrics.resolved_tickets / max(self.metrics.total_tickets, 1)
            ),
            "escalation_rate": (
                self.metrics.escalated_tickets / max(self.metrics.total_tickets, 1)
            ),
            "auto_resolve_rate": (
                self.metrics.auto_resolved_count / max(self.metrics.resolved_tickets, 1)
            ),
            "avg_resolution_time_seconds": self.metrics.avg_resolution_time,
            "avg_first_response_time_seconds": self.metrics.avg_first_response_time,
            "avg_satisfaction_score": self.metrics.avg_satisfaction_score,
            "tickets_by_category": self.metrics.tickets_by_category,
            "tickets_by_priority": self.metrics.tickets_by_priority,
        }


# ======================================================================
# 模拟客户工单
# ======================================================================

SAMPLE_TICKETS = [
    {
        "customer_id": "C001",
        "customer_name": "张三",
        "subject": "忘记密码怎么办",
        "description": "我忘记了登录密码，请问怎么重置？我找不到重置密码的入口。",
    },
    {
        "customer_id": "C002",
        "customer_name": "李四",
        "subject": "如何升级套餐",
        "description": "我现在是基础版，想升级到专业版，请问怎么操作？升级后费用怎么算？",
    },
    {
        "customer_id": "C003",
        "customer_name": "王五",
        "subject": "系统紧急故障",
        "description": "紧急！我们公司所有人都无法登录系统了，页面一直显示500错误，"
                      "严重影响工作，请尽快处理！",
    },
    {
        "customer_id": "C004",
        "customer_name": "赵六",
        "subject": "数据导出功能咨询",
        "description": "请问系统支持导出 Excel 格式的数据吗？我需要做报表分析用。",
    },
    {
        "customer_id": "C005",
        "customer_name": "孙七",
        "subject": "退款申请",
        "description": "我昨天刚买的专业版，发现功能不太符合需求，可以申请退款吗？",
    },
]


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行客户支持机器人示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 客户支持机器人")
    print("█" * 60)

    # ---- 初始化 ----
    print("\n[初始化] 准备客户支持机器人...")

    try:
        provider = get_provider()
        print(f"       Provider: {provider.__class__.__name__}")
        factory = AgentFactory(provider=provider)
        support_agent = factory.create_agent(AgentRole.GENERAL, name="support-bot")
        quality_agent = factory.create_agent(AgentRole.QA, name="quality-checker")
    except Exception as e:
        print(f"       警告: 无法获取真实 Provider ({e})")
        print("       使用模拟模式...")

        from ai.providers import BaseProvider

        class MockProvider(BaseProvider):
            def __init__(self):
                self.name = "mock"
            def generate(self, prompt, **kwargs):
                return {
                    "output": "尊敬的客户，您好！感谢您的咨询。根据您的问题...",
                    "latency_ms": 100,
                    "tokens_used": 50,
                }

        support_agent = Agent(
            id="sb-001",
            agent_type="general",
            name="support-bot",
            provider=MockProvider(),
            system_prompt="你是一名专业的客户支持代表。",
        )
        quality_agent = Agent(
            id="qc-001",
            agent_type="qa",
            name="quality-checker",
            provider=MockProvider(),
            system_prompt="你是一名质量检查员。",
        )

    # 创建知识库和机器人
    kb = KnowledgeBase()
    bot = CustomerSupportBot(support_agent, quality_agent, kb)
    print(f"       知识库文章: {len(kb.articles)} 篇")

    # 设置回调（演示用）
    def on_created(ticket):
        print(f"    [事件] 新工单创建: {ticket.ticket_id}")

    def on_resolved(ticket):
        print(f"    [事件] 工单已解决: {ticket.ticket_id}")

    def on_escalated(ticket, reason):
        print(f"    [事件] 工单已升级: {ticket.ticket_id} ({reason})")

    bot.on_ticket_created = on_created
    bot.on_ticket_resolved = on_resolved
    bot.on_ticket_escalated = on_escalated

    # ---- 处理示例工单 ----
    print(f"\n[处理] 处理 {len(SAMPLE_TICKETS)} 个示例工单...")

    for i, ticket_data in enumerate(SAMPLE_TICKETS, 1):
        print(f"\n--- 工单 {i}/{len(SAMPLE_TICKETS)} ---")

        # 创建工单
        ticket = bot.create_ticket(**ticket_data)

        # 处理工单
        bot.process_ticket(ticket)

        # 模拟满意度评分
        if ticket.status == TicketStatus.RESOLVED:
            bot.record_satisfaction(ticket.ticket_id, 4 if i % 2 == 0 else 5)

    # ---- 打印指标报告 ----
    print("\n" + "=" * 60)
    print("  📊 客服服务指标报告")
    print("=" * 60)

    metrics = bot.get_metrics_report()

    print(f"\n📈 总体指标:")
    print(f"   总工单数: {metrics['total_tickets']}")
    print(f"   已解决: {metrics['resolved_tickets']}")
    print(f"   已升级: {metrics['escalated_tickets']}")
    print(f"   解决率: {metrics['resolution_rate']:.1%}")
    print(f"   升级率: {metrics['escalation_rate']:.1%}")
    print(f"   自动解决率: {metrics['auto_resolve_rate']:.1%}")

    print(f"\n⏱️  响应时效:")
    print(f"   平均首次响应: {metrics['avg_first_response_time_seconds']:.1f} 秒")
    print(f"   平均解决时间: {metrics['avg_resolution_time_seconds']:.1f} 秒")

    print(f"\n😊 客户满意度:")
    print(f"   平均评分: {metrics['avg_satisfaction_score']:.1f} / 5")

    print(f"\n📂 按类别分布:")
    for cat, count in sorted(metrics['tickets_by_category'].items(),
                              key=lambda x: -x[1]):
        bar_len = int(count / max(metrics['total_tickets'], 1) * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"   [{bar}] {cat}: {count}")

    print(f"\n🚨 按优先级分布:")
    for pri in ["urgent", "high", "medium", "low"]:
        count = metrics['tickets_by_priority'].get(pri, 0)
        bar_len = int(count / max(metrics['total_tickets'], 1) * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"   [{bar}] {pri}: {count}")

    # ---- 演示知识库搜索 ----
    print(f"\n🔍 知识库搜索演示:")
    test_queries = ["密码重置", "退款政策", "API 限流"]
    for query in test_queries:
        results = kb.search(query)
        print(f"   查询 '{query}': 找到 {len(results)} 篇")
        for r in results[:2]:
            print(f"     - {r.title}")

    print("\n" + "=" * 60)
    print("  客户支持机器人功能总结")
    print("=" * 60)
    print("""
  ✓ 智能工单分类
  ✓ 知识库智能检索
  ✓ 自动生成回复
  ✓ 回复质量检查
  ✓ 智能升级流程
  ✓ 服务指标追踪
  ✓ 满意度统计
  ✓ 多维度分析报告
""")


if __name__ == "__main__":
    main()
