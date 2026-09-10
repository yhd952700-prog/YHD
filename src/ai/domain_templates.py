"""ZOON — Specialized Agent Framework (MASTER-SPEC §65-67).

ZOON 不实现为固定单 Agent。而是 Specialized Agent Framework：

    Domain → Agent Template → Skills → Knowledge → Tools → Memory
           → Policies → Evaluation Suite → Agent

本模块提供 §66 的「Agent Template」层与 §67 的「Specialized Domains」清单，
并把它桥接到已有的 ``AgentFactory``（Phase 3 Agent Runtime，扩展而非重写）：

    ``DomainTemplate`` 是纯声明（领域人设 + 能力/技能/知识/工具/策略/评测套件），
    ``SpecializedAgentFactory`` 把模板转成 ``AgentSpec`` 交给 ``AgentFactory.create``，
    产出带真实 identity / memory / policy 的领域 Agent。

诚实边界（NO-FAKE）：
    模板里的 ``capabilities``/``tools`` 是**声明式能力清单**——执行时由
    ``ToolRouter`` 按 capability_id 路由到 ACTIVE 工具。当前已由内置工具覆盖的
    能力为 ``memory.search`` / ``audit.query`` / ``system.status``（``make_tools``）
    与 World Interface 的 ``filesystem`` / ``shell`` adapter（``world_interface.py``）；
    其余为规划中的能力接入点，非「已存在但未实现」的伪装。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent_factory import AgentFactory, AgentSpec
from .observability import observe


@dataclass
class DomainTemplate:
    """领域 Agent 模板（§66 链中的 Agent Template 一环）。

    一个模板描述「某领域」的 Agent 如何配置，覆盖 §66 链的上游环
    （Domain → Template → Skills → Knowledge → Tools → Memory → Policies
    → Evaluation Suite）。它是纯声明，构造时不触发任何 provider / kernel 调用。
    """

    domain: str                       # 规范化领域名，如 "research"（注册表主键）
    display_name: str                 # 展示名，如 "Research"
    agent_type: str                   # 传给 AgentSpec 的 agent_type 标识
    system_prompt: str                # 领域专家人设（独立于具体任务 goal）
    capabilities: List[str] = field(default_factory=list)   # 能力清单（语义）
    skills: List[str] = field(default_factory=list)         # 技能清单
    knowledge_domains: List[str] = field(default_factory=list)  # 知识域
    tools: List[str] = field(default_factory=list)          # 工具 / capability 标识
    policies: List[str] = field(default_factory=list)       # 策略 / 权限（映射到 permissions）
    evaluation_suite: str = ""        # 评测套件标识（规划中，可扩展）
    memory_policy: Optional[str] = None
    model_policy: Optional[str] = None
    budget: Optional[float] = None
    resources: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可观测 / 可注册的字典形式。"""
        return {
            "domain": self.domain,
            "display_name": self.display_name,
            "agent_type": self.agent_type,
            "capabilities": list(self.capabilities),
            "skills": list(self.skills),
            "knowledge_domains": list(self.knowledge_domains),
            "tools": list(self.tools),
            "policies": list(self.policies),
            "evaluation_suite": self.evaluation_suite,
            "memory_policy": self.memory_policy,
            "model_policy": self.model_policy,
            "budget": self.budget,
            "resources": dict(self.resources),
        }


class DomainTemplateRegistry:
    """领域模板注册表（§67「模板可扩展」——内置 13 个 + 可注册自定义）。"""

    def __init__(self) -> None:
        self._templates: Dict[str, DomainTemplate] = {}

    @observe("domain_template.register")
    def register(self, template: DomainTemplate) -> None:
        """注册一个模板。重复 ``domain`` 视为错误（拒绝覆盖，避免静默篡改）。"""
        if template.domain in self._templates:
            raise ValueError(
                f"domain template '{template.domain}' is already registered"
            )
        self._templates[template.domain] = template

    def get(self, domain: str) -> Optional[DomainTemplate]:
        """按领域名取模板（不存在返回 None）。"""
        return self._templates.get(domain)

    def list(self) -> List[str]:
        """返回全部已注册领域名（字典序）。"""
        return sorted(self._templates.keys())

    def unregister(self, domain: str) -> bool:
        """移除模板。存在并移除返回 True，否则 False。"""
        return self._templates.pop(domain, None) is not None

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, domain: str) -> bool:
        return domain in self._templates


class SpecializedAgentFactory:
    """DomainTemplate → AgentSpec → Agent（复用 AgentFactory，扩展而非重写）。

    ``create_agent`` 把领域模板转成带独立人设（system_prompt）与任务目标（goal）
    的 ``AgentSpec``，交给 ``AgentFactory.create`` 产出领域 Agent；并在返回字典中
    附加 ``template`` / ``domain`` 以便追溯「这个 Agent 来自哪个领域模板」。
    """

    def __init__(
        self,
        registry: Optional[DomainTemplateRegistry] = None,
        agent_factory: Optional[AgentFactory] = None,
    ) -> None:
        self._registry = registry or get_domain_registry()
        self._agent_factory = agent_factory or AgentFactory()

    def to_agent_spec(
        self,
        template: DomainTemplate,
        goal: str,
        name: Optional[str] = None,
    ) -> AgentSpec:
        """把领域模板映射为 ``AgentSpec``（纯数据转换，不触发创建）。"""
        return AgentSpec(
            agent_type=template.agent_type,
            goal=goal,
            capabilities=list(template.capabilities),
            memory_policy=template.memory_policy,
            model_policy=template.model_policy,
            budget=template.budget,
            resources=dict(template.resources),
            permissions=list(template.policies),
            evaluation_suite=template.evaluation_suite or None,
            name=name,
            system_prompt=template.system_prompt,
        )

    @observe("specialized_agent_factory.create_agent")
    def create_agent(
        self,
        domain: str,
        goal: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按领域名创建 Agent。未知领域抛 ``ValueError``（附可用列表）。"""
        template = self._registry.get(domain)
        if template is None:
            raise ValueError(
                f"unknown domain '{domain}'. available domains: {self._registry.list()}"
            )
        spec = self.to_agent_spec(template, goal, name=name)
        out = self._agent_factory.create(spec)
        out["template"] = template
        out["domain"] = domain
        return out


# --------------------------------------------------------------------------- #
# §67 内置领域模板（13 个，模板可扩展）
# --------------------------------------------------------------------------- #
# 工具 / capability 标识说明：
#   memory.search / audit.query / system.status — 由 make_tools() 提供
#   filesystem.read|write|list / shell.run        — 由 World Interface adapter 提供
# 其余（如 web.search / code.exec / data.sql）为规划中的能力接入点。
BUILTIN_TEMPLATES: List[DomainTemplate] = [
    DomainTemplate(
        domain="research",
        display_name="Research",
        agent_type="research",
        system_prompt="你是一名严谨的研究分析师。多源求证、区分事实与推断，"
                      "给出可追溯的结论与引用，明确标注不确定性。",
        capabilities=["read", "analyze", "synthesize"],
        skills=["literature_review", "source_critique", "synthesis"],
        knowledge_domains=["general_knowledge", "methodology"],
        tools=["memory.search", "audit.query"],
        evaluation_suite="research.eval",
    ),
    DomainTemplate(
        domain="coding",
        display_name="Coding",
        agent_type="coding",
        system_prompt="你是一名软件工程师。写可读、可测试、可维护的代码，"
                      "优先复用现有实现而非重复造轮子。",
        capabilities=["read", "write", "execute", "test"],
        skills=["code_generation", "refactoring", "debugging", "testing"],
        knowledge_domains=["software_engineering"],
        tools=["filesystem.read", "filesystem.write", "shell.run", "memory.search"],
        evaluation_suite="coding.eval",
    ),
    DomainTemplate(
        domain="data",
        display_name="Data",
        agent_type="data",
        system_prompt="你是一名数据分析师。重视数据清洗、口径一致与可复现，"
                      "用数据说话，明确样本与方法局限。",
        capabilities=["read", "analyze", "visualize"],
        skills=["data_wrangling", "statistics", "visualization"],
        knowledge_domains=["statistics", "data_engineering"],
        tools=["memory.search", "filesystem.read"],
        evaluation_suite="data.eval",
    ),
    DomainTemplate(
        domain="security",
        display_name="Security",
        agent_type="security",
        system_prompt="你是一名安全专家。遵循最小权限与纵深防御，任何敏感操作"
                      "必须留痕、可审计，发现风险即上报而非自行处置。",
        capabilities=["read", "audit", "assess"],
        skills=["threat_modeling", "vulnerability_assessment", "audit"],
        knowledge_domains=["security", "compliance"],
        tools=["audit.query", "memory.search"],
        policies=["read:security", "audit:security"],
        evaluation_suite="security.eval",
    ),
    DomainTemplate(
        domain="finance",
        display_name="Finance",
        agent_type="finance",
        system_prompt="你是一名金融分析师。遵守合规与风险提示义务，区分事实与"
                      "预测，任何数字必须注明口径与来源。",
        capabilities=["read", "analyze", "report"],
        skills=["financial_modeling", "valuation", "risk_analysis"],
        knowledge_domains=["finance", "accounting"],
        tools=["memory.search", "audit.query"],
        policies=["read:finance"],
        evaluation_suite="finance.eval",
    ),
    DomainTemplate(
        domain="marketing",
        display_name="Marketing",
        agent_type="marketing",
        system_prompt="你是一名营销策划。以受众洞察为起点，输出可执行的创意与"
                      "转化路径，避免空洞口号。",
        capabilities=["read", "create", "analyze"],
        skills=["audience_research", "copywriting", "campaign_planning"],
        knowledge_domains=["marketing", "consumer_behavior"],
        tools=["memory.search"],
        evaluation_suite="marketing.eval",
    ),
    DomainTemplate(
        domain="devops",
        display_name="DevOps",
        agent_type="devops",
        system_prompt="你是一名 DevOps 工程师。追求自动化、可观测与可回滚，"
                      "变更前先想清楚如何验证与回退。",
        capabilities=["read", "execute", "monitor", "deploy"],
        skills=["ci_cd", "infrastructure_as_code", "observability"],
        knowledge_domains=["devops", "cloud"],
        tools=["shell.run", "system.status", "audit.query"],
        evaluation_suite="devops.eval",
    ),
    DomainTemplate(
        domain="operations",
        display_name="Operations",
        agent_type="operations",
        system_prompt="你是一名运营专员。以流程与 SOP 为准，关注效率、质量与"
                      "异常闭环，及时上报卡点。",
        capabilities=["read", "execute", "report"],
        skills=["process_management", "sop_execution", "reporting"],
        knowledge_domains=["operations"],
        tools=["memory.search", "audit.query"],
        evaluation_suite="operations.eval",
    ),
    DomainTemplate(
        domain="monitoring",
        display_name="Monitoring",
        agent_type="monitoring",
        system_prompt="你是一名监控值守。实时关注系统状态与告警，先判断影响面"
                      "再处置，重大事件立即上报。",
        capabilities=["read", "monitor", "alert"],
        skills=["metric_analysis", "alert_triage", "incident_response"],
        knowledge_domains=["observability", "sre"],
        tools=["system.status", "audit.query"],
        evaluation_suite="monitoring.eval",
    ),
    DomainTemplate(
        domain="science",
        display_name="Science",
        agent_type="science",
        system_prompt="你是一名科研人员。以假设—验证—可复现为核心，明确区分"
                      "相关与因果，报告阴性结果。",
        capabilities=["read", "analyze", "experiment"],
        skills=["hypothesis_testing", "experimental_design", "reproducibility"],
        knowledge_domains=["scientific_method"],
        tools=["memory.search", "filesystem.read"],
        evaluation_suite="science.eval",
    ),
    DomainTemplate(
        domain="legal_research",
        display_name="Legal Research",
        agent_type="legal_research",
        system_prompt="你是一名法律研究助理。只做条文检索与事实整理，逐条注明"
                      "法源与效力，不构成正式法律意见。",
        capabilities=["read", "analyze", "cite"],
        skills=["statute_retrieval", "case_analysis", "citation"],
        knowledge_domains=["law"],
        tools=["memory.search", "audit.query"],
        policies=["read:legal"],
        evaluation_suite="legal_research.eval",
    ),
    DomainTemplate(
        domain="product",
        display_name="Product",
        agent_type="product",
        system_prompt="你是一名产品经理。以用户价值与优先级为准，界定问题、"
                      "权衡取舍，输出可落地的需求。",
        capabilities=["read", "analyze", "prioritize"],
        skills=["requirement_analysis", "prioritization", "roadmapping"],
        knowledge_domains=["product_management"],
        tools=["memory.search"],
        evaluation_suite="product.eval",
    ),
    DomainTemplate(
        domain="qa",
        display_name="QA",
        agent_type="qa",
        system_prompt="你是一名测试工程师。以覆盖与回归为准，先写清验收标准，"
                      "缺陷只记录不遮掩。",
        capabilities=["read", "test", "report"],
        skills=["test_design", "regression", "defect_tracking"],
        knowledge_domains=["software_testing"],
        tools=["filesystem.read", "shell.run", "audit.query"],
        evaluation_suite="qa.eval",
    ),
]


def build_builtin_registry() -> DomainTemplateRegistry:
    """构建包含 13 个内置领域模板的注册表。"""
    registry = DomainTemplateRegistry()
    for template in BUILTIN_TEMPLATES:
        registry.register(template)
    return registry


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #
_domain_registry: Optional[DomainTemplateRegistry] = None


@observe("domain_template.get_domain_registry")
def get_domain_registry() -> DomainTemplateRegistry:
    """获取或创建全局领域模板注册表（内置 13 个模板）。"""
    global _domain_registry
    if _domain_registry is None:
        _domain_registry = build_builtin_registry()
    return _domain_registry
