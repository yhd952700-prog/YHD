"""
研究助手 (Research Assistant) - LiuHao AI OS 用例示例

本示例演示如何构建一个研究助手 Agent，功能包括：
1. 信息收集工作流
2. 多源信息整合
3. 来源可信度评估
4. 引用管理
5. 研究报告生成

运行方式：
    python examples/use_cases/research_assistant.py
"""

import sys
import os
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.specialized_agents import ResearchAgent
from ai.agent_factory import AgentFactory, AgentRole
from ai.memory import (
    SemanticMemoryNetwork,
    EntityType,
    RelationType,
    WorkingMemory,
    MemoryPriority,
)
from ai.providers import get_provider, BaseProvider


# ======================================================================
# 数据模型
# ======================================================================

class SourceReliability(str, Enum):
    """来源可信度等级。"""
    HIGHLY_RELIABLE = "highly_reliable"    # 学术论文、官方文档
    RELIABLE = "reliable"                  # 权威媒体、知名机构
    MODERATE = "moderate"                  # 一般媒体、技术博客
    UNCERTAIN = "uncertain"                # 未知来源、论坛帖子
    UNRELIABLE = "unreliable"              # 疑似谣言、不可信来源


@dataclass
class ResearchSource:
    """一个信息来源。"""
    source_id: str
    title: str
    url: str
    author: str = ""
    publication_date: str = ""
    reliability: SourceReliability = SourceReliability.UNCERTAIN
    reliability_score: float = 0.5  # 0.0 - 1.0
    content_summary: str = ""
    tags: List[str] = field(default_factory=list)
    cited_by: List[str] = field(default_factory=list)  # 引用此来源的其他来源


@dataclass
class Citation:
    """引用条目。"""
    citation_id: str
    source_id: str
    quote: str  # 引用的原文片段
    context: str  # 引用的上下文
    page_number: Optional[int] = None


@dataclass
class ResearchFinding:
    """研究发现。"""
    finding_id: str
    statement: str  # 发现的陈述
    confidence: float  # 置信度 0.0 - 1.0
    supporting_sources: List[str] = field(default_factory=list)  # 支持的来源 ID
    contradicting_sources: List[str] = field(default_factory=list)  # 矛盾的来源 ID
    category: str = "general"


@dataclass
class ResearchReport:
    """完整的研究报告。"""
    topic: str
    summary: str
    findings: List[ResearchFinding]
    sources: List[ResearchSource]
    citations: List[Citation]
    knowledge_gaps: List[str]
    methodology: str
    total_sources_evaluated: int
    duration_seconds: float


# ======================================================================
# 研究助手类
# ======================================================================

class ResearchAssistant:
    """
    研究助手 - 专门负责信息收集和研究的 Agent 包装器。

    工作流程：
    1. 明确研究问题
    2. 规划研究方向
    3. 收集多源信息
    4. 评估来源可信度
    5. 交叉验证发现
    6. 生成结构化报告
    """

    def __init__(self, research_agent: ResearchAgent, knowledge_base: SemanticMemoryNetwork = None):
        """
        初始化研究助手。

        Args:
            research_agent: 底层的 ResearchAgent 实例
            knowledge_base: 语义记忆网络，用于存储研究知识
        """
        self.agent = research_agent
        self.kb = knowledge_base or SemanticMemoryNetwork()
        self.sources: Dict[str, ResearchSource] = {}
        self.findings: List[ResearchFinding] = []
        self.citations: List[Citation] = []
        self.work_memory = WorkingMemory(capacity=20)

    # ---- 阶段 1: 规划 ----

    def plan_research(self, topic: str) -> List[str]:
        """
        规划研究方向，将大话题分解为子问题。

        Args:
            topic: 研究主题

        Returns:
            子问题列表
        """
        print(f"  [规划] 分解研究主题: {topic}")

        # 将主题存入工作记忆
        self.work_memory.add(
            content=topic,
            item_type="goal",
            priority=MemoryPriority.CRITICAL,
            source="user",
            tags=["research_topic"],
        )

        # 使用 Agent 生成子问题
        prompt = f"""
        我需要研究以下主题：{topic}

        请将这个主题分解为 3-5 个具体的子问题，
        每个子问题应该是可以独立研究的具体方向。

        请以 JSON 列表格式返回，格式如下：
        ["子问题1", "子问题2", "子问题3"]
        """

        try:
            result = self.agent.agent.execute(prompt)
            output = result.get("output", "")
            # 简单解析（实际使用中应该用更健壮的解析）
            import json
            try:
                sub_questions = json.loads(output)
                if isinstance(sub_questions, list):
                    print(f"         分解为 {len(sub_questions)} 个子问题")
                    for i, q in enumerate(sub_questions, 1):
                        print(f"           {i}. {q[:60]}")
                    return sub_questions
            except json.JSONDecodeError:
                pass

            # 降级：按行分割
            lines = [l.strip("- ") for l in output.strip().split("\n") if l.strip()]
            print(f"         分解为 {len(lines)} 个子问题")
            return lines[:5]

        except Exception as e:
            print(f"         规划失败: {e}")
            return [topic]

    # ---- 阶段 2: 信息收集 ----

    def gather_information(self, sub_questions: List[str]) -> List[ResearchSource]:
        """
        针对每个子问题收集信息。

        Args:
            sub_questions: 子问题列表

        Returns:
            收集到的来源列表
        """
        print(f"\n  [收集] 针对 {len(sub_questions)} 个子问题收集信息...")

        sources = []
        for i, question in enumerate(sub_questions, 1):
            print(f"         子问题 {i}/{len(sub_questions)}: {question[:50]}...")

            # 模拟收集到多个来源
            # 实际应用中，这里会调用搜索工具、数据库查询等
            mock_sources = self._mock_collect_sources(question)
            sources.extend(mock_sources)

            for src in mock_sources:
                self.sources[src.source_id] = src

        print(f"         共收集到 {len(sources)} 个来源")
        return sources

    def _mock_collect_sources(self, question: str) -> List[ResearchSource]:
        """模拟收集信息来源（实际使用中替换为真实搜索）。"""
        import hashlib

        sources = []
        reliability_levels = list(SourceReliability)

        for i in range(3):
            src_id = hashlib.md5(f"{question}_{i}".encode()).hexdigest()[:12]
            reliability = reliability_levels[i % len(reliability_levels)]
            reliability_score = {
                SourceReliability.HIGHLY_RELIABLE: 0.95,
                SourceReliability.RELIABLE: 0.8,
                SourceReliability.MODERATE: 0.6,
                SourceReliability.UNCERTAIN: 0.4,
                SourceReliability.UNRELIABLE: 0.2,
            }[reliability]

            source = ResearchSource(
                source_id=src_id,
                title=f"关于 '{question[:30]}' 的来源文章 {i+1}",
                url=f"https://example.com/sources/{src_id}",
                author=f"作者{i+1}",
                publication_date="2024-01-15",
                reliability=reliability,
                reliability_score=reliability_score,
                content_summary=f"这是关于 {question[:20]} 的一份信息摘要...",
                tags=["research", question[:20]],
            )
            sources.append(source)

        return sources

    # ---- 阶段 3: 来源评估 ----

    def evaluate_sources(self, sources: List[ResearchSource]) -> List[ResearchSource]:
        """
        评估信息来源的可信度。

        评估维度：
        1. 发布者权威性
        2. 信息时效性
        3. 是否有引用支持
        4. 是否存在偏见迹象
        5. 与其他来源的一致性

        Args:
            sources: 待评估的来源列表

        Returns:
            按可信度排序的来源列表
        """
        print(f"\n  [评估] 评估 {len(sources)} 个来源的可信度...")

        # 使用 Agent 进行来源评估
        for source in sources:
            eval_prompt = f"""
            请评估以下信息来源的可信度：

            标题: {source.title}
            作者: {source.author}
            发布日期: {source.publication_date}
            URL: {source.url}
            摘要: {source.content_summary}

            请从以下维度评分（0-10）：
            1. 作者权威性
            2. 信息时效性
            3. 内容客观性
            4. 引用完整性
            5. 整体可信度

            返回 JSON 格式的评分。
            """

            try:
                # 实际使用中会调用 Agent 进行评估
                # 这里使用模拟评分
                scores = {
                    "author_authority": source.reliability_score * 10,
                    "timeliness": 8.0,
                    "objectivity": source.reliability_score * 9,
                    "citation_quality": source.reliability_score * 7,
                    "overall": source.reliability_score * 8.5,
                }

                # 更新来源的可信度评分
                source.reliability_score = scores["overall"] / 10.0

            except Exception:
                # 评估失败，保持原分数
                pass

        # 按可信度排序
        sources.sort(key=lambda s: s.reliability_score, reverse=True)

        print(f"         最高可信度: {sources[0].title[:40]}... ({sources[0].reliability_score:.2f})")
        print(f"         最低可信度: {sources[-1].title[:40]}... ({sources[-1].reliability_score:.2f})")

        return sources

    # ---- 阶段 4: 交叉验证与发现 ----

    def cross_validate_and_synthesize(self, sources: List[ResearchSource]) -> List[ResearchFinding]:
        """
        交叉验证不同来源的信息，综合得出研究发现。

        方法：
        1. 提取各来源的核心观点
        2. 比较不同来源的一致性
        3. 识别共识和分歧点
        4. 计算每个发现的置信度

        Args:
            sources: 已评估的来源列表

        Returns:
            研究发现列表
        """
        print(f"\n  [综合] 交叉验证 {len(sources)} 个来源，综合研究发现...")

        # 使用高可信度的来源进行综合
        high_quality_sources = [s for s in sources if s.reliability_score >= 0.6]
        print(f"         高可信度来源: {len(high_quality_sources)} 个")

        # 模拟生成研究发现
        # 实际应用中，这会调用 Agent 进行深度分析
        findings = []

        finding_1 = ResearchFinding(
            finding_id="finding-001",
            statement="AI 技术正在快速发展，应用场景不断扩大。",
            confidence=0.85,
            supporting_sources=[s.source_id for s in high_quality_sources[:3]],
            category="trend",
        )
        findings.append(finding_1)

        finding_2 = ResearchFinding(
            finding_id="finding-002",
            statement="大语言模型是当前 AI 领域的核心技术方向。",
            confidence=0.9,
            supporting_sources=[s.source_id for s in high_quality_sources[:2]],
            category="technology",
        )
        findings.append(finding_2)

        finding_3 = ResearchFinding(
            finding_id="finding-003",
            statement="AI 伦理和监管问题日益受到关注。",
            confidence=0.75,
            supporting_sources=[s.source_id for s in high_quality_sources[1:3]],
            contradicting_sources=[sources[-1].source_id] if sources else [],
            category="society",
        )
        findings.append(finding_3)

        self.findings = findings
        print(f"         生成 {len(findings)} 个研究发现")
        for i, f in enumerate(findings, 1):
            print(f"           {i}. [{f.confidence:.0%}] {f.statement[:50]}")

        return findings

    # ---- 阶段 5: 生成报告 ----

    def generate_report(self, topic: str) -> ResearchReport:
        """
        生成最终的研究报告。

        Args:
            topic: 研究主题

        Returns:
            完整的研究报告
        """
        print(f"\n  [报告] 生成研究报告...")

        # 识别知识缺口
        knowledge_gaps = self._identify_knowledge_gaps()

        # 生成报告摘要
        summary_prompt = f"""
        基于以下研究发现，为主题"{topic}"生成一个简洁的摘要：

        研究发现：
        {chr(10).join([f"- {f.statement} (置信度: {f.confidence:.0%})" for f in self.findings])}

        请提供一个 3-5 句话的综合摘要。
        """

        try:
            result = self.agent.agent.execute(summary_prompt)
            summary = result.get("output", "研究报告摘要生成中...")
        except Exception:
            summary = "基于多源信息的综合研究结果显示，该领域正在快速发展..."

        # 创建报告
        report = ResearchReport(
            topic=topic,
            summary=summary,
            findings=self.findings,
            sources=list(self.sources.values()),
            citations=self.citations,
            knowledge_gaps=knowledge_gaps,
            methodology=(
                "本研究采用多源信息收集方法，通过文献调研、"
                "网络搜索和专家访谈等方式收集数据，随后进行"
                "来源可信度评估和交叉验证，最终综合得出结论。"
            ),
            total_sources_evaluated=len(self.sources),
            duration_seconds=0.0,  # 会在调用处计算
        )

        # 将研究结果存入知识库
        self._save_to_knowledge_base(topic, report)

        print(f"         报告主题: {topic}")
        print(f"         发现数量: {len(report.findings)}")
        print(f"         来源数量: {report.total_sources_evaluated}")
        print(f"         知识缺口: {len(report.knowledge_gaps)} 个")

        return report

    def _identify_knowledge_gaps(self) -> List[str]:
        """识别研究中的知识缺口。"""
        # 模拟识别知识缺口
        gaps = [
            "长期影响数据不足，需要更多时间验证",
            "某些细分领域的研究较少",
            "不同地区的发展差异尚不明确",
        ]
        return gaps

    def _save_to_knowledge_base(self, topic: str, report: ResearchReport):
        """将研究结果存入语义知识库。"""
        # 创建主题实体
        topic_id = self.kb.add_entity(
            name=topic,
            entity_type=EntityType.CONCEPT,
            description=report.summary[:200],
            tags=["research_topic"],
        )

        # 为每个发现创建实体并关联
        for finding in report.findings:
            finding_id = self.kb.add_entity(
                name=f"发现: {finding.statement[:30]}",
                entity_type=EntityType.CONCEPT,
                description=finding.statement,
                tags=["research_finding", finding.category],
            )
            self.kb.add_relation(
                source_id=topic_id,
                target_id=finding_id,
                relation_type=RelationType.RELATED_TO,
                confidence=finding.confidence,
            )

    # ---- 完整研究流程 ----

    def conduct_research(self, topic: str) -> ResearchReport:
        """
        执行完整的研究流程。

        Args:
            topic: 研究主题

        Returns:
            完整的研究报告
        """
        start_time = time.time()

        print(f"\n🔬 开始研究: {topic}")
        print("=" * 60)

        # 阶段 1: 规划
        sub_questions = self.plan_research(topic)

        # 阶段 2: 收集
        sources = self.gather_information(sub_questions)

        # 阶段 3: 评估
        evaluated_sources = self.evaluate_sources(sources)

        # 阶段 4: 综合
        findings = self.cross_validate_and_synthesize(evaluated_sources)

        # 阶段 5: 报告
        report = self.generate_report(topic)
        report.duration_seconds = time.time() - start_time

        print(f"\n{'=' * 60}")
        print(f"✅ 研究完成！耗时: {report.duration_seconds:.1f} 秒")
        print(f"   评估来源: {report.total_sources_evaluated} 个")
        print(f"   研究发现: {len(report.findings)} 条")

        return report


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行研究助手示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 研究助手 (Research Assistant)")
    print("█" * 60)

    # ---- 初始化 ----
    print("\n[初始化] 准备研究助手...")

    try:
        provider = get_provider()
        print(f"       Provider: {provider.__class__.__name__}")
        factory = AgentFactory(provider=provider)
        base_agent = factory.create_agent(AgentRole.RESEARCHER, name="research-assistant")
        research_agent = ResearchAgent(agent=base_agent)
    except Exception as e:
        print(f"       警告: 无法获取真实 Provider ({e})")
        print("       使用模拟模式...")

        # 创建模拟 Provider
        from ai.providers import BaseProvider
        class MockProvider(BaseProvider):
            def __init__(self):
                self.name = "mock"
            def generate(self, prompt, **kwargs):
                return {
                    "output": '["子问题1", "子问题2", "子问题3"]',
                    "latency_ms": 100,
                    "tokens_used": 50,
                }

        from ai.employee import Agent
        mock_provider = MockProvider()
        base_agent = Agent(
            id="ra-001",
            agent_type="researcher",
            name="research-assistant",
            provider=mock_provider,
            system_prompt="你是一名专业的研究助理。",
        )
        research_agent = ResearchAgent(agent=base_agent)

    # 创建知识库
    knowledge_base = SemanticMemoryNetwork()

    # 创建研究助手
    assistant = ResearchAssistant(
        research_agent=research_agent,
        knowledge_base=knowledge_base,
    )

    # ---- 执行研究 ----
    topic = "人工智能在医疗领域的应用现状与未来趋势"
    report = assistant.conduct_research(topic)

    # ---- 打印报告摘要 ----
    print("\n" + "=" * 60)
    print("  📄 研究报告摘要")
    print("=" * 60)
    print(f"\n主题: {report.topic}")
    print(f"\n摘要:\n{report.summary[:300]}...")

    print(f"\n研究发现 ({len(report.findings)} 条):")
    for i, finding in enumerate(report.findings, 1):
        bar_len = int(finding.confidence * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {i}. [{bar}] {finding.confidence:.0%}")
        print(f"     {finding.statement}")
        print(f"     支持来源: {len(finding.supporting_sources)} 个")

    print(f"\n来源评估 ({report.total_sources_evaluated} 个):")
    reliable = sum(1 for s in report.sources if s.reliability_score >= 0.7)
    moderate = sum(1 for s in report.sources if 0.4 <= s.reliability_score < 0.7)
    low = sum(1 for s in report.sources if s.reliability_score < 0.4)
    print(f"  高可信度: {reliable} 个")
    print(f"  中等可信度: {moderate} 个")
    print(f"  低可信度: {low} 个")

    print(f"\n知识缺口 ({len(report.knowledge_gaps)} 个):")
    for i, gap in enumerate(report.knowledge_gaps, 1):
        print(f"  {i}. {gap}")

    print(f"\n研究方法: {report.methodology}")
    print(f"\n耗时: {report.duration_seconds:.1f} 秒")

    print("\n" + "=" * 60)
    print("  研究助手功能总结")
    print("=" * 60)
    print("""
  ✓ 智能话题分解
  ✓ 多源信息收集
  ✓ 来源可信度评估
  ✓ 交叉验证与综合
  ✓ 引用管理
  ✓ 知识缺口识别
  ✓ 结构化报告生成
  ✓ 知识库自动更新
""")


if __name__ == "__main__":
    main()
