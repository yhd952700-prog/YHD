"""
代码审查 Agent - LiuHao AI OS 用例示例

本示例演示如何构建一个代码审查 Agent，功能包括：
1. 代码质量分析（可读性、可维护性、性能）
2. 最佳实践检查
3. 安全漏洞扫描
4. 审查报告生成
5. 改进建议格式化

运行方式：
    python examples/use_cases/code_review_agent.py
"""

import sys
import os
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.specialized_agents import (
    CodeAgent,
    CodeLanguage,
    CodeStyle,
    ComplexityLevel,
    CodeReviewFinding,
)
from ai.agent_factory import AgentFactory, AgentRole
from ai.providers import get_provider


# ======================================================================
# 数据模型
# ======================================================================

class ReviewCategory(str, Enum):
    """代码审查类别。"""
    STYLE = "style"           # 代码风格
    BUG = "bug"               # 潜在 Bug
    PERFORMANCE = "performance"  # 性能问题
    SECURITY = "security"     # 安全漏洞
    ARCHITECTURE = "architecture"  # 架构设计
    DOCUMENTATION = "documentation"  # 文档
    TESTING = "testing"       # 测试覆盖
    BEST_PRACTICE = "best_practice"  # 最佳实践


class Severity(str, Enum):
    """问题严重程度。"""
    INFO = "info"           # 建议
    WARNING = "warning"     # 需要注意
    ERROR = "error"         # 需要修复
    CRITICAL = "critical"   # 必须立即修复


@dataclass
class CodeSnippet:
    """待审查的代码片段。"""
    file_path: str
    language: CodeLanguage
    content: str
    line_count: int = 0

    def __post_init__(self):
        if not self.line_count:
            self.line_count = len(self.content.splitlines())


@dataclass
class ReviewIssue:
    """一个审查发现的问题。"""
    issue_id: str
    category: ReviewCategory
    severity: Severity
    title: str
    description: str
    file_path: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    code_snippet: str = ""
    suggestion: str = ""
    rationale: str = ""  # 为什么这是个问题
    affected_code_quality: List[str] = field(default_factory=list)


@dataclass
class CodeMetrics:
    """代码质量指标。"""
    total_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    code_lines: int = 0
    avg_line_length: float = 0.0
    max_nesting_depth: int = 0
    function_count: int = 0
    class_count: int = 0
    cyclomatic_complexity: float = 0.0
    maintainability_index: float = 0.0  # 0-100, 越高越好


@dataclass
class CodeReviewReport:
    """完整的代码审查报告。"""
    files_reviewed: List[str]
    total_issues: int
    issues_by_severity: Dict[str, int]
    issues_by_category: Dict[str, int]
    issues: List[ReviewIssue]
    metrics: CodeMetrics
    overall_score: float  # 0-100
    summary: str
    recommendations: List[str]
    positive_findings: List[str]
    review_duration_seconds: float


# ======================================================================
# 代码审查器
# ======================================================================

class CodeReviewer:
    """
    代码审查器 - 基于 CodeAgent 的代码审查工作流。

    审查流程：
    1. 静态分析 - 计算代码度量指标
    2. 风格检查 - 验证代码风格一致性
    3. Bug 检测 - 识别潜在错误
    4. 安全扫描 - 检查安全漏洞
    5. 最佳实践 - 检查最佳实践遵循情况
    6. 生成报告 - 汇总审查结果
    """

    def __init__(self, code_agent: CodeAgent):
        """
        初始化代码审查器。

        Args:
            code_agent: 底层 CodeAgent 实例
        """
        self.agent = code_agent
        self.issues: List[ReviewIssue] = []
        self.issue_counter = 0

    # ---- 阶段 1: 静态分析 ----

    def analyze_code(self, code: str, language: CodeLanguage) -> CodeMetrics:
        """
        对代码进行静态分析，计算质量指标。

        Args:
            code: 源代码字符串
            language: 编程语言

        Returns:
            代码度量指标
        """
        lines = code.splitlines()
        total_lines = len(lines)

        # 计算各种行数
        comment_lines = 0
        blank_lines = 0
        code_lines = 0

        comment_prefixes = {
            CodeLanguage.PYTHON: ["#"],
            CodeLanguage.JAVASCRIPT: ["//"],
            CodeLanguage.TYPESCRIPT: ["//"],
            CodeLanguage.JAVA: ["//"],
            CodeLanguage.GO: ["//"],
            CodeLanguage.RUST: ["//"],
            CodeLanguage.CPP: ["//"],
        }
        prefixes = comment_prefixes.get(language, ["#"])

        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_lines += 1
            elif any(stripped.startswith(p) for p in prefixes):
                comment_lines += 1
            else:
                code_lines += 1

        # 计算平均行长度
        non_empty_lines = [l for l in lines if l.strip()]
        avg_line_length = (
            sum(len(l) for l in non_empty_lines) / len(non_empty_lines)
            if non_empty_lines else 0
        )

        # 估算最大嵌套深度
        max_depth = 0
        current_depth = 0
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            current_depth = indent // 4  # 假设 4 空格缩进
            max_depth = max(max_depth, current_depth)

        # 估算函数和类数量
        function_count = 0
        class_count = 0
        for line in lines:
            stripped = line.strip()
            if language == CodeLanguage.PYTHON:
                if stripped.startswith("def "):
                    function_count += 1
                elif stripped.startswith("class "):
                    class_count += 1
            else:
                if "function " in stripped or stripped.endswith("() {"):
                    function_count += 1
                if stripped.startswith("class "):
                    class_count += 1

        # 估算圈复杂度
        complexity_keywords = ["if", "else", "elif", "for", "while", "case", "catch", "&&", "||"]
        complexity_count = 0
        for line in lines:
            for kw in complexity_keywords:
                complexity_count += line.count(kw)
        cyclomatic_complexity = max(1, complexity_count + 1)

        # 估算可维护性指数 (0-100)
        # 基于：代码行数、注释比例、复杂度、嵌套深度
        comment_ratio = comment_lines / max(code_lines, 1)
        maintainability = 100.0

        # 行数惩罚
        if code_lines > 500:
            maintainability -= 20
        elif code_lines > 200:
            maintainability -= 10

        # 复杂度惩罚
        if cyclomatic_complexity > 20:
            maintainability -= 25
        elif cyclomatic_complexity > 10:
            maintainability -= 10

        # 嵌套深度惩罚
        if max_depth > 5:
            maintainability -= 15
        elif max_depth > 3:
            maintainability -= 5

        # 注释奖励
        if 0.1 <= comment_ratio <= 0.3:
            maintainability += 5

        maintainability = max(0, min(100, maintainability))

        return CodeMetrics(
            total_lines=total_lines,
            comment_lines=comment_lines,
            blank_lines=blank_lines,
            code_lines=code_lines,
            avg_line_length=avg_line_length,
            max_nesting_depth=max_depth,
            function_count=function_count,
            class_count=class_count,
            cyclomatic_complexity=cyclomatic_complexity,
            maintainability_index=maintainability,
        )

    # ---- 阶段 2: 代码风格检查 ----

    def check_style(self, code: str, language: CodeLanguage,
                    style: CodeStyle, file_path: str) -> List[ReviewIssue]:
        """
        检查代码风格是否符合规范。

        Args:
            code: 源代码
            language: 编程语言
            style: 代码风格规范
            file_path: 文件路径（用于报告）

        Returns:
            风格问题列表
        """
        issues = []
        lines = code.splitlines()

        for line_num, line in enumerate(lines, 1):
            # 行长度检查
            if len(line.rstrip()) > 100:
                issues.append(self._create_issue(
                    category=ReviewCategory.STYLE,
                    severity=Severity.INFO,
                    title="行过长",
                    description=f"第 {line_num} 行长度为 {len(line.rstrip())} 字符，超过 100 字符限制",
                    file_path=file_path,
                    line_start=line_num,
                    line_end=line_num,
                    code_snippet=line[:100] + "...",
                    suggestion="考虑将长行拆分为多行，以提高可读性。",
                    rationale="过长的行在小屏幕上难以阅读，也不利于代码对比。",
                ))

            # 行尾空格检查
            if line != line.rstrip():
                issues.append(self._create_issue(
                    category=ReviewCategory.STYLE,
                    severity=Severity.INFO,
                    title="行尾空格",
                    description=f"第 {line_num} 行末尾有多余的空格",
                    file_path=file_path,
                    line_start=line_num,
                    line_end=line_num,
                    suggestion="删除行尾的空白字符。",
                    rationale="行尾空格是不必要的，可能导致 diff 噪音。",
                ))

        # Tab vs 空格检查
        has_tabs = any("\t" in line for line in lines)
        has_spaces = any(line.startswith("    ") for line in lines)
        if has_tabs and has_spaces:
            issues.append(self._create_issue(
                category=ReviewCategory.STYLE,
                severity=Severity.WARNING,
                title="缩进不一致",
                description="代码中混合使用了 Tab 和空格进行缩进",
                file_path=file_path,
                suggestion="统一使用一种缩进方式（推荐空格）。",
                rationale="混合缩进会导致代码显示不一致，影响可读性。",
            ))

        return issues

    # ---- 阶段 3: Bug 检测 ----

    def detect_bugs(self, code: str, language: CodeLanguage,
                    file_path: str) -> List[ReviewIssue]:
        """
        检测代码中的潜在 Bug。

        Args:
            code: 源代码
            language: 编程语言
            file_path: 文件路径

        Returns:
            Bug 问题列表
        """
        issues = []
        lines = code.splitlines()

        # Python 特定的 Bug 模式
        if language == CodeLanguage.PYTHON:
            for line_num, line in enumerate(lines, 1):
                stripped = line.strip()

                # 检查 bare except
                if stripped == "except:":
                    issues.append(self._create_issue(
                        category=ReviewCategory.BUG,
                        severity=Severity.WARNING,
                        title="裸 except 语句",
                        description="使用了裸 except 语句，会捕获包括 KeyboardInterrupt 在内的所有异常",
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        code_snippet=stripped,
                        suggestion="使用 except Exception: 或指定具体的异常类型。",
                        rationale="裸 except 可能隐藏严重错误，使调试变得困难。",
                    ))

                # 检查可变默认参数
                if "def " in stripped and "=[]" in stripped:
                    issues.append(self._create_issue(
                        category=ReviewCategory.BUG,
                        severity=Severity.ERROR,
                        title="可变默认参数",
                        description="函数使用了可变对象（列表）作为默认参数",
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        code_snippet=stripped,
                        suggestion="使用 None 作为默认值，在函数内部初始化可变对象。",
                        rationale="Python 的默认参数在函数定义时只计算一次，"
                                  "可变默认参数会在多次调用间共享状态。",
                    ))

                # 检查 == None 而不是 is None
                if "== None" in stripped:
                    issues.append(self._create_issue(
                        category=ReviewCategory.BUG,
                        severity=Severity.WARNING,
                        title="使用 == 比较 None",
                        description="使用 == 比较 None 而不是 is",
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        code_snippet=stripped,
                        suggestion="使用 'is None' 或 'is not None' 进行比较。",
                        rationale="'is' 检查身份（identity），'==' 检查相等性（equality）。"
                                  "对于 None，应该使用 'is'。",
                    ))

        return issues

    # ---- 阶段 4: 安全扫描 ----

    def scan_security(self, code: str, language: CodeLanguage,
                      file_path: str) -> List[ReviewIssue]:
        """
        扫描代码中的安全问题。

        Args:
            code: 源代码
            language: 编程语言
            file_path: 文件路径

        Returns:
            安全问题列表
        """
        issues = []
        lines = code.splitlines()

        # 通用安全问题
        security_patterns = [
            ("password", "硬编码密码", Severity.CRITICAL),
            ("secret", "可能的硬编码密钥", Severity.ERROR),
            ("api_key", "可能的硬编码 API Key", Severity.ERROR),
            ("token", "可能的硬编码令牌", Severity.WARNING),
        ]

        for line_num, line in enumerate(lines, 1):
            lowered = line.lower()
            for pattern, title, severity in security_patterns:
                if pattern in lowered and "=" in line:
                    # 简单检查是否在赋值
                    issues.append(self._create_issue(
                        category=ReviewCategory.SECURITY,
                        severity=severity,
                        title=title,
                        description=f"第 {line_num} 行可能包含硬编码的敏感信息",
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        code_snippet=line.strip()[:80],
                        suggestion="使用环境变量或密钥管理服务来存储敏感信息。",
                        rationale="硬编码的凭据可能被提交到版本控制系统，导致安全泄露。",
                    ))
                    break  # 每行只报一次

        # SQL 注入检查（简化版）
        sql_patterns = ["execute(", "query(", "SELECT ", "INSERT ", "UPDATE "]
        for line_num, line in enumerate(lines, 1):
            for pattern in sql_patterns:
                if pattern in line and "+" in line and "%" in line:
                    issues.append(self._create_issue(
                        category=ReviewCategory.SECURITY,
                        severity=Severity.CRITICAL,
                        title="可能的 SQL 注入漏洞",
                        description=f"第 {line_num} 行使用字符串拼接构建 SQL 查询",
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        code_snippet=line.strip()[:80],
                        suggestion="使用参数化查询（prepared statements）来防止 SQL 注入。",
                        rationale="字符串拼接的 SQL 查询容易受到 SQL 注入攻击。",
                    ))
                    break

        return issues

    # ---- 阶段 5: 最佳实践检查 ----

    def check_best_practices(self, code: str, language: CodeLanguage,
                             file_path: str, metrics: CodeMetrics) -> List[ReviewIssue]:
        """
        检查代码是否遵循最佳实践。

        Args:
            code: 源代码
            language: 编程语言
            file_path: 文件路径
            metrics: 代码度量指标

        Returns:
            最佳实践问题列表
        """
        issues = []

        # 函数过长检查
        if metrics.function_count > 0:
            avg_function_size = metrics.code_lines / metrics.function_count
            if avg_function_size > 50:
                issues.append(self._create_issue(
                    category=ReviewCategory.BEST_PRACTICE,
                    severity=Severity.WARNING,
                    title="函数平均过大",
                    description=f"平均每个函数约 {avg_function_size:.0f} 行代码，建议控制在 50 行以内",
                    file_path=file_path,
                    suggestion="将大函数拆分为更小的、职责单一的函数。",
                    rationale="小函数更易读、易测、易维护，遵循单一职责原则。",
                ))

        # 文件过大检查
        if metrics.code_lines > 500:
            issues.append(self._create_issue(
                category=ReviewCategory.BEST_PRACTICE,
                severity=Severity.WARNING,
                title="文件过大",
                description=f"文件有 {metrics.code_lines} 行代码，建议拆分为多个文件",
                file_path=file_path,
                suggestion="按功能或类将代码拆分到不同的文件中。",
                rationale="过大的文件难以导航和理解，增加维护成本。",
            ))

        # 复杂度过高检查
        if metrics.cyclomatic_complexity > 15:
            issues.append(self._create_issue(
                category=ReviewCategory.BEST_PRACTICE,
                severity=Severity.ERROR,
                title="圈复杂度过高",
                description=f"估算圈复杂度为 {metrics.cyclomatic_complexity:.0f}，超过推荐阈值 15",
                file_path=file_path,
                suggestion="重构代码，减少分支和循环嵌套，提取辅助函数。",
                rationale="高复杂度的代码难以理解和测试，出错概率更高。",
            ))

        # 嵌套过深检查
        if metrics.max_nesting_depth > 4:
            issues.append(self._create_issue(
                category=ReviewCategory.BEST_PRACTICE,
                severity=Severity.WARNING,
                title="嵌套过深",
                description=f"最大嵌套深度为 {metrics.max_nesting_depth} 层，建议不超过 4 层",
                file_path=file_path,
                suggestion="使用提前返回（early return）或提取函数来减少嵌套。",
                rationale="深层嵌套的代码难以理解和维护。",
            ))

        # 注释不足检查
        if metrics.code_lines > 100 and metrics.comment_lines / max(metrics.code_lines, 1) < 0.05:
            issues.append(self._create_issue(
                category=ReviewCategory.DOCUMENTATION,
                severity=Severity.INFO,
                title="注释不足",
                description=f"注释比例约为 {metrics.comment_lines / metrics.code_lines:.1%}，建议至少 5%",
                file_path=file_path,
                suggestion="为复杂的逻辑和公共 API 添加注释和文档字符串。",
                rationale="适当的注释有助于其他开发者理解代码意图和设计决策。",
            ))

        return issues

    # ---- 辅助方法 ----

    def _create_issue(self, **kwargs) -> ReviewIssue:
        """创建一个审查问题，自动生成 ID。"""
        self.issue_counter += 1
        return ReviewIssue(
            issue_id=f"issue-{self.issue_counter:04d}",
            **kwargs,
        )

    # ---- 完整审查流程 ----

    def review_code(self, snippets: List[CodeSnippet],
                    style: CodeStyle = CodeStyle.PEP8) -> CodeReviewReport:
        """
        执行完整的代码审查。

        Args:
            snippets: 待审查的代码片段列表
            style: 代码风格规范

        Returns:
            完整的审查报告
        """
        start_time = time.time()

        all_issues: List[ReviewIssue] = []
        all_metrics = CodeMetrics()
        files_reviewed = []
        positive_findings = []

        for snippet in snippets:
            print(f"\n  审查文件: {snippet.file_path}")
            print(f"    语言: {snippet.language.value}, 行数: {snippet.line_count}")
            files_reviewed.append(snippet.file_path)

            # 阶段 1: 静态分析
            print("    [1/5] 静态分析...")
            metrics = self.analyze_code(snippet.content, snippet.language)
            all_metrics.total_lines += metrics.total_lines
            all_metrics.code_lines += metrics.code_lines
            all_metrics.comment_lines += metrics.comment_lines
            all_metrics.function_count += metrics.function_count
            all_metrics.class_count += metrics.class_count

            # 阶段 2: 风格检查
            print("    [2/5] 风格检查...")
            style_issues = self.check_style(
                snippet.content, snippet.language, style, snippet.file_path
            )
            all_issues.extend(style_issues)
            print(f"      发现 {len(style_issues)} 个风格问题")

            # 阶段 3: Bug 检测
            print("    [3/5] Bug 检测...")
            bug_issues = self.detect_bugs(
                snippet.content, snippet.language, snippet.file_path
            )
            all_issues.extend(bug_issues)
            print(f"      发现 {len(bug_issues)} 个潜在 Bug")

            # 阶段 4: 安全扫描
            print("    [4/5] 安全扫描...")
            security_issues = self.scan_security(
                snippet.content, snippet.language, snippet.file_path
            )
            all_issues.extend(security_issues)
            print(f"      发现 {len(security_issues)} 个安全问题")

            # 阶段 5: 最佳实践
            print("    [5/5] 最佳实践检查...")
            bp_issues = self.check_best_practices(
                snippet.content, snippet.language, snippet.file_path, metrics
            )
            all_issues.extend(bp_issues)
            print(f"      发现 {len(bp_issues)} 个最佳实践问题")

            # 记录正面发现
            if metrics.maintainability_index >= 80:
                positive_findings.append(
                    f"{snippet.file_path}: 可维护性指数高 ({metrics.maintainability_index:.0f})"
                )
            if 0.1 <= metrics.comment_lines / max(metrics.code_lines, 1) <= 0.3:
                positive_findings.append(
                    f"{snippet.file_path}: 注释比例合理"
                )

        # 计算汇总统计
        issues_by_severity = {}
        issues_by_category = {}
        for issue in all_issues:
            sev = issue.severity.value
            issues_by_severity[sev] = issues_by_severity.get(sev, 0) + 1
            cat = issue.category.value
            issues_by_category[cat] = issues_by_category.get(cat, 0) + 1

        # 计算总体评分 (0-100)
        score = 100.0
        severity_weights = {
            Severity.CRITICAL: 10,
            Severity.ERROR: 5,
            Severity.WARNING: 2,
            Severity.INFO: 0.5,
        }
        for issue in all_issues:
            score -= severity_weights.get(issue.severity, 1)
        score = max(0, score)

        # 生成建议
        recommendations = self._generate_recommendations(all_issues, all_metrics)

        # 生成摘要
        summary = self._generate_summary(files_reviewed, all_issues, score)

        duration = time.time() - start_time

        return CodeReviewReport(
            files_reviewed=files_reviewed,
            total_issues=len(all_issues),
            issues_by_severity=issues_by_severity,
            issues_by_category=issues_by_category,
            issues=all_issues,
            metrics=all_metrics,
            overall_score=score,
            summary=summary,
            recommendations=recommendations,
            positive_findings=positive_findings,
            review_duration_seconds=duration,
        )

    def _generate_recommendations(self, issues: List[ReviewIssue],
                                   metrics: CodeMetrics) -> List[str]:
        """根据审查结果生成改进建议。"""
        recs = []

        # 按严重程度排序问题
        critical = [i for i in issues if i.severity == Severity.CRITICAL]
        errors = [i for i in issues if i.severity == Severity.ERROR]

        if critical:
            recs.append(f"🔴 立即修复 {len(critical)} 个严重问题（安全漏洞等）")
        if errors:
            recs.append(f"🟠 优先修复 {len(errors)} 个错误级问题")

        if metrics.cyclomatic_complexity > 10:
            recs.append("📊 考虑重构以降低代码复杂度")

        if metrics.max_nesting_depth > 3:
            recs.append("🔧 减少嵌套深度，提高可读性")

        if not recs:
            recs.append("✅ 代码质量良好，继续保持！")

        return recs

    def _generate_summary(self, files: List[str], issues: List[ReviewIssue],
                          score: float) -> str:
        """生成审查摘要。"""
        critical = sum(1 for i in issues if i.severity == Severity.CRITICAL)
        errors = sum(1 for i in issues if i.severity == Severity.ERROR)
        warnings = sum(1 for i in issues if i.severity == Severity.WARNING)

        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"

        return (
            f"本次审查覆盖 {len(files)} 个文件，共发现 {len(issues)} 个问题 "
            f"（严重: {critical}, 错误: {errors}, 警告: {warnings}）。"
            f"综合评分: {score:.1f}/100 (等级 {grade})。"
        )


# ======================================================================
# 示例代码
# ======================================================================

SAMPLE_PYTHON_CODE = '''
import os
import sys

password = "secret123"  # 硬编码密码

def process_data(data=[]):  # 可变默认参数
    """处理数据"""
    result = []
    for item in data:
        if item > 0:
            if item > 10:
                if item > 100:
                    if item > 1000:
                        result.append(item * 2)
                    else:
                        result.append(item * 1.5)
                else:
                    result.append(item * 1.2)
            else:
                result.append(item)
        else:
            try:
                result.append(abs(item))
            except:  # 裸 except
                pass
    return result

def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + str(user_id)  # SQL 注入风险
    return execute_query(query)

class DataProcessor:
    def __init__(self):
        self.data = []
    
    def add_data(self, item):
        self.data.append(item)
    
    def process_all(self):
        for item in self.data:
            if item == None:  # 应该用 is None
                continue
            print(item)
    # 后面还有很多代码...
''' * 10  # 让文件更大一些


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行代码审查示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 代码审查 Agent")
    print("█" * 60)

    # ---- 初始化 ----
    print("\n[初始化] 准备代码审查器...")

    try:
        provider = get_provider()
        print(f"       Provider: {provider.__class__.__name__}")
        factory = AgentFactory(provider=provider)
        base_agent = factory.create_agent(AgentRole.CRITIC, name="code-reviewer")
        code_agent = CodeAgent(
            agent=base_agent,
            language_preferences=[CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT],
            code_style=CodeStyle.PEP8,
        )
    except Exception as e:
        print(f"       警告: 无法获取真实 Provider ({e})")
        print("       使用模拟模式...")

        from ai.providers import BaseProvider
        from ai.employee import Agent

        class MockProvider(BaseProvider):
            def __init__(self):
                self.name = "mock"
            def generate(self, prompt, **kwargs):
                return {"output": "分析完成", "latency_ms": 100, "tokens_used": 50}

        base_agent = Agent(
            id="cr-001",
            agent_type="critic",
            name="code-reviewer",
            provider=MockProvider(),
            system_prompt="你是一名资深代码审查员。",
        )
        code_agent = CodeAgent(
            agent=base_agent,
            language_preferences=[CodeLanguage.PYTHON],
            code_style=CodeStyle.PEP8,
        )

    reviewer = CodeReviewer(code_agent)

    # ---- 准备待审查代码 ----
    print("\n[准备] 准备待审查的代码...")
    snippets = [
        CodeSnippet(
            file_path="src/utils/data_processor.py",
            language=CodeLanguage.PYTHON,
            content=SAMPLE_PYTHON_CODE,
        ),
    ]
    print(f"       {len(snippets)} 个文件待审查")

    # ---- 执行审查 ----
    print("\n[审查] 开始代码审查...")
    report = reviewer.review_code(snippets, style=CodeStyle.PEP8)

    # ---- 打印报告 ----
    print("\n" + "=" * 60)
    print("  📋 代码审查报告")
    print("=" * 60)

    print(f"\n📁 审查文件: {len(report.files_reviewed)} 个")
    for f in report.files_reviewed:
        print(f"   - {f}")

    print(f"\n📊 代码度量:")
    print(f"   总行数: {report.metrics.total_lines}")
    print(f"   代码行: {report.metrics.code_lines}")
    print(f"   注释行: {report.metrics.comment_lines}")
    print(f"   函数数: {report.metrics.function_count}")
    print(f"   类数量: {report.metrics.class_count}")
    print(f"   圈复杂度: {report.metrics.cyclomatic_complexity:.0f}")

    print(f"\n📈 综合评分: {report.overall_score:.1f}/100")
    grade = "A" if report.overall_score >= 90 else "B" if report.overall_score >= 75 else \
            "C" if report.overall_score >= 60 else "D" if report.overall_score >= 40 else "F"
    print(f"   等级: {grade}")

    print(f"\n🔍 问题统计 ({report.total_issues} 个):")
    print(f"   按严重程度:")
    for sev in ["critical", "error", "warning", "info"]:
        count = report.issues_by_severity.get(sev, 0)
        icon = {"critical": "🔴", "error": "🟠", "warning": "🟡", "info": "🔵"}.get(sev, "•")
        print(f"     {icon} {sev}: {count}")

    print(f"   按类别:")
    for cat, count in sorted(report.issues_by_category.items(), key=lambda x: -x[1]):
        print(f"     • {cat}: {count}")

    print(f"\n💡 关键问题 (Top 5):")
    sorted_issues = sorted(report.issues,
                           key=lambda i: ["critical", "error", "warning", "info"].index(i.severity.value))
    for issue in sorted_issues[:5]:
        icon = {"critical": "🔴", "error": "🟠", "warning": "🟡", "info": "🔵"}.get(
            issue.severity.value, "•")
        print(f"   {icon} [{issue.category.value}] {issue.title}")
        print(f"      {issue.file_path}:{issue.line_start}")
        print(f"      建议: {issue.suggestion[:60]}...")

    print(f"\n✅ 正面发现:")
    if report.positive_findings:
        for finding in report.positive_findings:
            print(f"   + {finding}")
    else:
        print("   (暂无)")

    print(f"\n📝 改进建议:")
    for rec in report.recommendations:
        print(f"   {rec}")

    print(f"\n⏱️  审查耗时: {report.review_duration_seconds:.2f} 秒")

    print("\n" + "=" * 60)
    print("  代码审查功能总结")
    print("=" * 60)
    print("""
  ✓ 静态代码分析
  ✓ 代码风格检查
  ✓ 潜在 Bug 检测
  ✓ 安全漏洞扫描
  ✓ 最佳实践验证
  ✓ 结构化审查报告
  ✓ 严重程度分级
  ✓ 可操作的改进建议
""")


if __name__ == "__main__":
    main()
