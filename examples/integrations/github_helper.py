"""
GitHub 助手集成 - LiuHao AI OS 集成示例

本示例演示如何将 LiuHao AI OS 集成到 GitHub：
1. GitHub API 操作
2. Issue 管理与自动回复
3. PR 审查工作流
4. Actions 触发与通知

运行方式：
    python examples/integrations/github_helper.py

注意：这是一个示例文件，演示如何使用 GitHub API 与 LiuHao AI OS 集成。
实际使用需要配置 GitHub Personal Access Token。
"""

import sys
import os
import time
import json
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.specialized_agents import CodeAgent, CodeLanguage, CodeReviewFinding
from ai.agent_factory import AgentFactory, AgentRole
from ai.employee import Agent
from ai.providers import get_provider


# ======================================================================
# GitHub 数据模型
# ======================================================================

class IssueState(str, Enum):
    """Issue 状态。"""
    OPEN = "open"
    CLOSED = "closed"


class PullRequestState(str, Enum):
    """PR 状态。"""
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class ReviewStatus(str, Enum):
    """审查状态。"""
    PENDING = "pending"
    COMMENTED = "commented"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


@dataclass
class GitHubIssue:
    """GitHub Issue。"""
    number: int
    title: str
    body: str
    state: IssueState
    author: str
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    comments_count: int = 0
    url: str = ""
    html_url: str = ""


@dataclass
class PullRequest:
    """GitHub Pull Request。"""
    number: int
    title: str
    body: str
    state: PullRequestState
    author: str
    base_branch: str
    head_branch: str
    labels: List[str] = field(default_factory=list)
    reviewers: List[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    created_at: str = ""
    merged: bool = False
    url: str = ""


@dataclass
class ReviewComment:
    """审查评论。"""
    comment_id: int
    path: str
    line: int
    body: str
    author: str
    side: str = "RIGHT"  # LEFT / RIGHT
    position: int = 0


# ======================================================================
# GitHub 客户端
# ======================================================================

class GitHubHelper:
    """
    GitHub 助手 - 将 LiuHao AI OS 集成到 GitHub。

    功能：
    - Issue 自动分类和回复
    - PR 自动代码审查
    - 标签管理
    - 通知与提醒
    """

    def __init__(self, token: str = None, repo: str = None,
                 code_agent: CodeAgent = None):
        """
        初始化 GitHub 助手。

        Args:
            token: GitHub Personal Access Token
            repo: 仓库名 (owner/repo)
            code_agent: 用于代码审查的 AI Agent
        """
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repo = repo or "owner/repository"
        self.code_agent = code_agent
        self.api_base = "https://api.github.com"

        # 事件处理器
        self._event_handlers: Dict[str, List[Callable]] = {}

    # ---- Issue 管理 ----

    def get_issue(self, issue_number: int) -> Optional[GitHubIssue]:
        """
        获取 Issue 详情。

        Args:
            issue_number: Issue 编号

        Returns:
            Issue 对象，如果不存在返回 None
        """
        # 实际实现会调用 GitHub API
        # 这里返回模拟数据
        return GitHubIssue(
            number=issue_number,
            title="功能请求：添加深色模式",
            body="希望能够添加深色模式支持，方便夜间使用。\n\n"
                 "建议：\n"
                 "1. 跟随系统主题\n"
                 "2. 支持手动切换\n"
                 "3. 保存用户偏好",
            state=IssueState.OPEN,
            author="user123",
            labels=["enhancement", "ui"],
            assignees=["designer-a"],
            created_at="2024-01-15T10:30:00Z",
            comments_count=5,
            html_url=f"https://github.com/{self.repo}/issues/{issue_number}",
        )

    def create_issue(self, title: str, body: str,
                     labels: List[str] = None) -> GitHubIssue:
        """创建新 Issue。"""
        print(f"  [GitHub] 创建 Issue: {title}")
        print(f"    标签: {labels}")
        # 实际实现调用 API
        return GitHubIssue(
            number=999,
            title=title,
            body=body,
            state=IssueState.OPEN,
            author="liuhao-bot",
            labels=labels or [],
        )

    def add_comment(self, issue_number: int, body: str) -> bool:
        """
        在 Issue/PR 上添加评论。

        Args:
            issue_number: Issue/PR 编号
            body: 评论内容

        Returns:
            是否成功
        """
        print(f"  [GitHub] 在 #{issue_number} 添加评论:")
        print(f"    {body[:80]}...")
        return True

    def auto_label_issue(self, issue: GitHubIssue) -> List[str]:
        """
        自动为 Issue 添加标签。

        使用 AI 分析 Issue 内容，自动添加合适的标签。
        """
        print(f"  [AI] 自动分类 Issue #{issue.number}: {issue.title}")

        # 模拟 AI 分析
        content_lower = (issue.title + " " + issue.body).lower()

        auto_labels = []

        # 简单的关键词匹配（实际使用中用 AI 分析）
        label_rules = {
            "bug": ["bug", "错误", "问题", "崩溃", "异常", "error", "crash"],
            "enhancement": ["功能", "增强", "改进", "添加", "feature", "enhance"],
            "documentation": ["文档", "doc", "说明", "readme"],
            "performance": ["性能", "慢", "优化", "performance", "speed"],
            "security": ["安全", "漏洞", "security", "vulnerability"],
            "ui": ["界面", "ui", "显示", "样式", "设计"],
            "question": ["问题", "请问", "help", "question"],
        }

        for label, keywords in label_rules.items():
            if any(kw in content_lower for kw in keywords):
                auto_labels.append(label)

        # 类型推断
        if any(l in issue.labels for l in ["bug"]):
            auto_labels.append("needs-triage")
        elif any(l in issue.labels for l in ["enhancement"]):
            auto_labels.append("needs-design")

        print(f"    建议标签: {', '.join(auto_labels) if auto_labels else '无'}")
        return auto_labels

    def generate_issue_reply(self, issue: GitHubIssue) -> str:
        """
        自动生成 Issue 回复。

        对于新创建的 Issue，自动给出友好的回复，
        告诉用户已经收到，并给出预计处理时间。
        """
        # 模拟 AI 生成回复
        label_text = ", ".join(issue.labels) if issue.labels else "待分类"

        reply = f"""
👋 感谢您提交的 Issue！我们已经收到。

**Issue 信息：**
- 标题：{issue.title}
- 标签：{label_text}
- 提交者：@{issue.author}

**处理流程：**
1. 团队成员会尽快审查此 Issue
2. 如果需要更多信息，我们会在评论中提问
3. 处理完成后会更新状态

**预计响应时间：**
- Bug 报告：24 小时内初步回复
- 功能请求：48 小时内评估
- 一般问题：72 小时内回复

如果这是一个紧急问题，请在评论中注明。

---
*此回复由 LiuHao AI 助手自动生成*
""".strip()

        return reply

    # ---- PR 审查 ----

    def get_pull_request(self, pr_number: int) -> Optional[PullRequest]:
        """获取 PR 详情。"""
        return PullRequest(
            number=pr_number,
            title="feat: 添加用户认证模块",
            body="## 描述\n添加用户登录注册功能。\n\n"
                 "## 变更\n- 添加登录页面\n"
                 "- 添加注册接口\n- 添加 JWT 认证中间件",
            state=PullRequestState.OPEN,
            author="dev-username",
            base_branch="main",
            head_branch="feature/auth",
            labels=["feature", "backend"],
            reviewers=["senior-dev"],
            additions=523,
            deletions=45,
            changed_files=12,
            created_at="2024-01-15T09:00:00Z",
            html_url=f"https://github.com/{self.repo}/pull/{pr_number}",
        )

    def review_pull_request(self, pr: PullRequest,
                            diff_content: str = None) -> List[CodeReviewFinding]:
        """
        自动审查 Pull Request。

        使用 AI 对代码变更进行审查，发现潜在问题。

        Args:
            pr: Pull Request 信息
            diff_content: 代码变更内容

        Returns:
            审查发现列表
        """
        print(f"  [AI] 审查 PR #{pr.number}: {pr.title}")
        print(f"    变更: +{pr.additions} -{pr.deletions} 行, "
              f"{pr.changed_files} 个文件")

        findings = []

        if self.code_agent:
            # 实际使用中调用 CodeAgent 进行审查
            # 这里模拟一些审查发现
            findings = [
                CodeReviewFinding(
                    severity="warning",
                    category="security",
                    description="密码字段不应该以明文形式记录日志",
                    suggestion="使用哈希处理后再记录，或移除密码字段的日志",
                    line_start=42,
                ),
                CodeReviewFinding(
                    severity="info",
                    category="style",
                    description="函数过长，建议拆分",
                    suggestion="将验证逻辑提取为单独的函数",
                    line_start=78,
                ),
                CodeReviewFinding(
                    severity="error",
                    category="bug",
                    description="缺少空值检查，可能导致空指针异常",
                    suggestion="添加 None 检查或使用可选链",
                    line_start=115,
                ),
            ]
        else:
            # 简单的静态检查
            if diff_content and "password" in diff_content.lower():
                findings.append(CodeReviewFinding(
                    severity="warning",
                    category="security",
                    description="代码中包含 password，请注意安全处理",
                    suggestion="确保密码不会被明文存储或记录",
                ))

        print(f"    发现 {len(findings)} 个问题")
        for f in findings:
            print(f"      • [{f.severity}] {f.category}: {f.description[:50]}")

        return findings

    def format_review_comment(self, findings: List[CodeReviewFinding]) -> str:
        """将审查发现格式化为 GitHub 评论。"""
        if not findings:
            return """
✅ 代码审查通过！

没有发现明显的问题，代码质量良好。

---
*此评论由 LiuHao AI 助手自动生成*
""".strip()

        # 按严重程度分组
        by_severity = {}
        for f in findings:
            sev = f.severity
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(f)

        comment_parts = [
            "## 🤖 AI 代码审查结果\n",
            f"共发现 **{len(findings)}** 个问题：\n",
        ]

        severity_order = ["critical", "error", "warning", "info"]
        severity_labels = {
            "critical": "🔴 严重",
            "error": "🟠 错误",
            "warning": "🟡 警告",
            "info": "🔵 建议",
        }

        for sev in severity_order:
            if sev in by_severity:
                items = by_severity[sev]
                comment_parts.append(f"\n### {severity_labels.get(sev, sev)} ({len(items)})\n")
                for f in items:
                    line_info = f"第 {f.line_start} 行" if f.line_start else ""
                    comment_parts.append(
                        f"- **[{f.category}]** {f.description}"
                        f"{' (' + line_info + ')' if line_info else ''}\n"
                    )
                    if f.suggestion:
                        comment_parts.append(f"  💡 建议: {f.suggestion}\n")

        comment_parts.append("\n---\n*此评论由 LiuHao AI 助手自动生成，仅供参考*")

        return "".join(comment_parts)

    # ---- Actions 集成 ----

    def trigger_action(self, action: str, params: Dict[str, Any] = None) -> bool:
        """
        触发 GitHub Actions 工作流。

        Args:
            action: 工作流名称或 ID
            params: 输入参数

        Returns:
            是否成功触发
        """
        print(f"  [GitHub Actions] 触发工作流: {action}")
        if params:
            print(f"    参数: {json.dumps(params, ensure_ascii=False)}")
        return True

    def notify_on_new_pr(self, pr: PullRequest):
        """新 PR 通知处理。"""
        print(f"\n  📬 新 PR 通知: #{pr.number} {pr.title}")

        # 1. 自动分配审查者
        print(f"  [1/3] 自动分配审查者...")
        # 实际逻辑：根据变更文件、团队分工分配

        # 2. 自动添加标签
        print(f"  [2/3] 自动添加标签...")
        # 根据 PR 内容自动打标签

        # 3. 运行初步审查
        print(f"  [3/3] 运行 AI 初步审查...")
        findings = self.review_pull_request(pr)
        if findings:
            comment = self.format_review_comment(findings)
            self.add_comment(pr.number, comment)

    # ---- 事件处理 ----

    def on(self, event: str, handler: Callable):
        """注册事件处理器。"""
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def handle_webhook(self, event_type: str, payload: Dict[str, Any]):
        """处理 GitHub Webhook 事件。"""
        print(f"\n  [Webhook] 收到事件: {event_type}")

        handlers = self._event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                print(f"    事件处理器错误: {e}")


# ======================================================================
# 示例 1: Issue 自动管理
# ======================================================================

def demo_issue_management():
    """
    演示 Issue 自动管理。
    """
    print("=" * 60)
    print("  示例 1：Issue 自动管理")
    print("=" * 60)
    print()

    gh = GitHubHelper(repo="org/project")

    # 获取 Issue
    print("获取 Issue 详情:")
    issue = gh.get_issue(42)
    print(f"  #{issue.number}: {issue.title}")
    print(f"  状态: {issue.state.value}")
    print(f"  作者: {issue.author}")
    print(f"  标签: {', '.join(issue.labels)}")
    print(f"  评论: {issue.comments_count}")

    print()

    # 自动分类
    print("自动分类:")
    suggested_labels = gh.auto_label_issue(issue)
    print(f"  AI 建议标签: {', '.join(suggested_labels)}")

    print()

    # 自动回复
    print("自动回复:")
    reply = gh.generate_issue_reply(issue)
    gh.add_comment(issue.number, reply)
    print(f"  已生成自动回复 ({len(reply)} 字符)")

    print()


# ======================================================================
# 示例 2: PR 自动审查
# ======================================================================

def demo_pr_review():
    """
    演示 Pull Request 自动审查。
    """
    print("=" * 60)
    print("  示例 2：PR 自动审查")
    print("=" * 60)
    print()

    # 使用模拟 CodeAgent
    from ai.providers import BaseProvider

    class MockProvider(BaseProvider):
        def __init__(self):
            self.name = "mock"
        def generate(self, prompt, **kwargs):
            return {"output": "审查完成", "latency_ms": 500, "tokens_used": 200}

    agent = Agent(
        id="review-agent",
        agent_type="critic",
        name="code-reviewer",
        provider=MockProvider(),
        system_prompt="你是一名资深代码审查员。",
    )
    code_agent = CodeAgent(agent=agent)

    gh = GitHubHelper(repo="org/project", code_agent=code_agent)

    # 获取 PR
    print("获取 PR 详情:")
    pr = gh.get_pull_request(123)
    print(f"  #{pr.number}: {pr.title}")
    print(f"  分支: {pr.head_branch} → {pr.base_branch}")
    print(f"  变更: +{pr.additions} -{pr.deletions}")
    print(f"  文件: {pr.changed_files} 个")
    print(f"  标签: {', '.join(pr.labels)}")

    print()

    # AI 审查
    print("AI 代码审查:")
    findings = gh.review_pull_request(pr)

    print()

    # 格式化评论
    print("生成审查评论:")
    comment = gh.format_review_comment(findings)
    print("  " + "─" * 50)
    for line in comment.split("\n")[:15]:
        print(f"  {line}")
    print(f"  ... (共 {len(comment)} 字符)")
    print("  " + "─" * 50)

    print()


# ======================================================================
# 示例 3: GitHub Actions 集成
# ======================================================================

def demo_actions_integration():
    """
    演示与 GitHub Actions 的集成。
    """
    print("=" * 60)
    print("  示例 3：GitHub Actions 集成")
    print("=" * 60)
    print()

    gh = GitHubHelper(repo="org/project")

    print("可用的工作流触发场景:")
    print()

    workflows = [
        ("代码质量检查", "code-quality",
         "每次 PR 更新时自动运行代码质量检查"),
        ("安全扫描", "security-scan",
         "每日定时运行依赖安全扫描"),
        ("性能测试", "performance-test",
         "合并到主分支前运行性能基准测试"),
        ("部署预览", "deploy-preview",
         "PR 创建后自动部署预览环境"),
        ("文档生成", "docs-generate",
         "代码合并后自动生成 API 文档"),
    ]

    for i, (name, action, desc) in enumerate(workflows, 1):
        print(f"  {i}. {name}")
        print(f"     工作流: {action}")
        print(f"     说明: {desc}")
        print()

    # 演示触发
    print("触发工作流示例:")
    print()
    gh.trigger_action("code-quality", {
        "pr_number": 123,
        "branch": "feature/auth",
    })

    print()
    print("💡 CI/CD 集成建议:")
    print("   1. PR 创建 → 触发 AI 代码审查")
    print("   2. 审查通过 → 运行自动化测试")
    print("   3. 测试通过 → 部署预览环境")
    print("   4. 合并后 → 运行完整测试套件")
    print("   5. 发布时 → 生成变更日志和文档")

    print()


# ======================================================================
# 示例 4: Webhook 事件处理
# ======================================================================

def demo_webhook_handling():
    """
    演示 GitHub Webhook 事件处理。
    """
    print("=" * 60)
    print("  示例 4：Webhook 事件处理")
    print("=" * 60)
    print()

    gh = GitHubHelper(repo="org/project")

    # 注册事件处理器
    def on_issues_opened(payload):
        """新 Issue 创建时的处理。"""
        action = payload.get("action", "")
        issue_data = payload.get("issue", {})
        print(f"    新 Issue: #{issue_data.get('number')} {issue_data.get('title')}")
        # 自动分类和回复
        print("    → 自动分类 + 欢迎回复")

    def on_pull_request_opened(payload):
        """新 PR 创建时的处理。"""
        pr_data = payload.get("pull_request", {})
        print(f"    新 PR: #{pr_data.get('number')} {pr_data.get('title')}")
        # 自动审查
        print("    → 触发 AI 代码审查")

    def on_push(payload):
        """代码推送时的处理。"""
        ref = payload.get("ref", "")
        commits = len(payload.get("commits", []))
        print(f"    推送: {ref} ({commits} 个提交)")
        # 触发构建
        print("    → 触发 CI 构建")

    gh.on("issues", on_issues_opened)
    gh.on("pull_request", on_pull_request_opened)
    gh.on("push", on_push)

    print("注册的事件处理器:")
    for event, handlers in gh._event_handlers.items():
        print(f"  • {event}: {len(handlers)} 个处理器")

    print()
    print("模拟 Webhook 事件:")
    print()

    # 模拟各种事件
    events = [
        ("issues", {"action": "opened",
                    "issue": {"number": 100, "title": "Bug: 登录失败"}}),
        ("pull_request", {"action": "opened",
                          "pull_request": {"number": 42, "title": "新功能"}}),
        ("push", {"ref": "refs/heads/main", "commits": [{}, {}, {}]}),
    ]

    for event_type, payload in events:
        gh.handle_webhook(event_type, payload)

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有 GitHub 集成示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - GitHub 助手集成")
    print("█" * 60)

    try:
        # 示例 1: Issue 管理
        demo_issue_management()

        # 示例 2: PR 审查
        demo_pr_review()

        # 示例 3: Actions 集成
        demo_actions_integration()

        # 示例 4: Webhook 处理
        demo_webhook_handling()

        print("=" * 60)
        print("  GitHub 集成功能总结")
        print("=" * 60)
        print("""
  ✓ Issue 自动分类与回复
  ✓ PR 智能代码审查
  ✓ 标签自动管理
  ✓ GitHub Actions 触发
  ✓ Webhook 事件处理
  ✓ 审查评论自动生成
  ✓ 多事件类型支持
""")

        print("  配置说明:")
        print("    1. 创建 GitHub Personal Access Token")
        print("    2. 设置环境变量 GITHUB_TOKEN")
        print("    3. 配置 Webhook 指向服务器")
        print("    4. 设置所需的仓库权限")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
