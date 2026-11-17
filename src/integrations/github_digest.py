"""GitHub 只读动态摘要（A3 集成与会议组）。

复用已有的 ``src.integrations.github_adapter.GitHubAdapter``（官方
GitHub REST API，``https://api.github.com``），只做**读**：
仓库动态、issue / PR 状态能查、能报。**不做写操作**（不开 issue、
不合 PR、不推代码——写操作走 Joseph 自己在 GitHub 上的正常流程，
机器人代写要另立审批，本轮不做）。

凭证只认环境变量 ``LIUHAO_GITHUB_TOKEN``（Joseph 的个人访问令牌，
建议只给 ``read-only`` 权限；只判断“配了没有”，不读出、不记录、
不返回 token 本身）。没配时返回 ``not_configured``，不编仓库数据。

``github_digest`` 接受注入的 ``client``（测试用假对象，不碰真网）；
不注入时按环境变量建真适配器——但**构造时不联网**，只有调用
``list_repositories`` 等方法时才发请求。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

#: 凭证环境变量（只判断“配了没有”）。
GITHUB_TOKEN_ENV = "LIUHAO_GITHUB_TOKEN"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_github_configured() -> bool:
    return bool(_env(GITHUB_TOKEN_ENV))


def github_status() -> Dict[str, Any]:
    """GitHub 接通状态（给设置页与 ``/v1/connections`` 用）。"""
    configured = is_github_configured()
    return {
        "configured": configured,
        "status": "configured" if configured else "not_configured",
        "route": "official_api",
        "route_label": "GitHub 官方 REST API（api.github.com，个人访问令牌，只读）",
        "risk": "low",
        "missing": [] if configured else [GITHUB_TOKEN_ENV],
        "setup_steps": [
            "在 GitHub → Settings → Developer settings → Personal access tokens"
            " 建一个令牌，建议只勾读权限（repo:read / issues:read），不要给写权限。",
            "把令牌填进部署环境的 LIUHAO_GITHUB_TOKEN（不要发给任何人、不要写进代码）。",
            "在设置 → 连接里填要看的仓库（形如 owner/repo），不填就只看自己的公开动态。",
            "摘要是只读的：能查仓库、issue、PR 的状态，不能代你开 issue、合 PR、推代码。",
        ],
    }


def _default_client() -> Any:
    """按环境变量建真适配器（构造不联网；没配 token 时抛未接通）。"""
    if not is_github_configured():
        raise RuntimeError(
            "GitHub 还没接通：请先把个人访问令牌填进 LIUHAO_GITHUB_TOKEN。"
        )
    from .base_adapter import AuthType, IntegrationConfig
    from .github_adapter import GitHubAdapter

    return GitHubAdapter(
        IntegrationConfig(
            name="github",
            base_url="https://api.github.com",
            auth_type=AuthType.API_KEY,
            api_key=_env(GITHUB_TOKEN_ENV),
        )
    )


def github_digest(
    repos: Optional[List[str]] = None,
    *,
    client: Any = None,
    per_repo_issues: int = 5,
) -> Dict[str, Any]:
    """读指定仓库的动态摘要：open issue / PR 数与最近的几条。

    ``repos`` 为空时只列“自己的仓库”（``list_repositories``），
    每个仓库取前 ``per_repo_issues`` 条 open issue 与 open PR。
    返回里只有 API 真实返回的数字与标题；调不通时把上游错误如实
    带在 ``error`` 里，``repos`` 为空列表——不编数字。
    """
    adapter = client if client is not None else _default_client()
    targets = list(repos or [])
    if not targets:
        try:
            listed = adapter.list_repositories()
        except Exception as exc:  # noqa: BLE001 - 上游错误如实回，不炸
            return {"status": "error", "error": str(exc)[:300], "repos": []}
        targets = [str(getattr(r, "full_name", "") or "") for r in listed]
        targets = [t for t in targets if t][:10]
    out: List[Dict[str, Any]] = []
    error: Optional[str] = None
    from .github_adapter import IssueState, PullRequestState

    for repo in targets:
        try:
            issues = adapter.list_issues(repo, state=IssueState.OPEN)
            prs = adapter.list_pull_requests(repo, state=PullRequestState.OPEN)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:300]
            out.append({"repo": repo, "error": error, "open_issues": [], "open_prs": []})
            continue
        out.append(
            {
                "repo": repo,
                "open_issue_count": len(issues),
                "open_pr_count": len(prs),
                "open_issues": [
                    {
                        "number": int(getattr(i, "number", 0) or 0),
                        "title": str(getattr(i, "title", "") or ""),
                        "url": str(getattr(i, "html_url", "") or ""),
                    }
                    for i in list(issues)[:per_repo_issues]
                ],
                "open_prs": [
                    {
                        "number": int(getattr(p, "number", 0) or 0),
                        "title": str(getattr(p, "title", "") or ""),
                        "url": str(getattr(p, "html_url", "") or ""),
                    }
                    for p in list(prs)[:per_repo_issues]
                ],
            }
        )
    result: Dict[str, Any] = {
        "status": "ok" if error is None else "partial",
        "repos": out,
    }
    if error is not None:
        result["error"] = error
    return result
