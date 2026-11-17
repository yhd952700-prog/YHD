"""连接路线总览（A3 集成与会议组）：六个平台各走哪条路、做到哪一步、
还差 Joseph 哪一步、封号风险高低。

本模块只做**状态聚合**（读各模块的 ``*_status()``，不联网、不读凭证
本身）；每条路线的文字版在 ``docs/connections-routes.md``，两边口径
一致：官方接口 → 官方企业版 → 操作官方客户端 → 人机接力。
红线（Joseph 2026-11-14 08:07）：冒充官方客户端、扒私有协议、
批量自动操作、绕登录和限流，全都不许用；宁可慢一点、绕远一点，
也不用有封号风险的捷径。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .github_digest import github_status
from .google_workspace import google_status
from .whatsapp_business import whatsapp_status

#: 杰夫AI 预设状态（只做预设、不填 Key、不调真实接口；见 models.py）。
#: 沙箱实测结论写进预设 hint，这里只汇总“还没验证”的状态。
JIEFUAI_BASE_URL = "https://jiefuai.cc/v1"


def jiefuai_status() -> Dict[str, Any]:
    """杰夫AI 聚合平台预设状态：预设已进、Key 没填、**没验证可用**。"""
    return {
        "configured": False,
        "status": "preset_only",
        "route": "official_api",
        "route_label": "OpenAI 兼容聚合平台（一个 Key 调多家模型，jiefuai.cc/v1）",
        "risk": "low",
        "missing": ["杰夫AI 的 Key（Joseph 自己在杰夫AI网站上查状态、自己填）"],
        "verified": False,
        "verification_note": (
            "没验证可用：2026-11-14 沙箱实测 jiefuai.cc 返回 401 Invalid token，"
            "jiefuai.vip 连不上（08:52 重测仍断开，连绕开假解析直连真 IP 也被拦）；"
            "两个域名在沙箱都解析到内网段 198.18.x，结论不一定准，"
            "需 Joseph 在真 Windows 电脑上再测一次，或在杰夫AI网站上查 Key 的状态"
            "（余额、是否启用、绑哪个分组）；也可能是 Key 抄错、过期或未启用。"
        ),
        "setup_steps": [
            "在杰夫AI网站上查 Key 的状态（余额、是否启用、绑哪个分组），"
            "确认 Key 本身没问题。",
            "在设置 → 模型与 Key → 预设里选“杰夫AI”，填 Key（不要发给任何人、不要写进代码）。",
            "点“测试连接”：在真 Windows 电脑上测才算数（沙箱测不出）。",
            "一个 Key 可调多家模型（按网站写的 42 个已启用模型，含 DeepSeek/GPT/Claude/Grok/Gemini），"
            "模型名在设置里改。",
        ],
    }


def meet_status() -> Dict[str, Any]:
    """视频会议（默认 Google Meet）的接通状态：会议从日历来，不另配账号。"""
    google = google_status()
    return {
        "configured": google["configured"],
        "status": "configured" if google["configured"] else "not_configured",
        "route": "official_api",
        "route_label": (
            "Google Calendar API 读会议（含 Meet 链接）+ 会后整理；"
            "Meet 没有官方 Bot 进会聊天的接口，Bot 不进会、不假装进了会"
        ),
        "risk": "low",
        "missing": [] if google["configured"] else [google["missing"][0] if google["missing"] else "LIUHAO_GOOGLE_ACCESS_TOKEN"],
        "bot_joins_meeting": False,
        "bot_joins_note": (
            "Google Meet 没有官方 Bot 进会聊天的接口：Bot 进不了会、"
            "不能在会里发言。替代路：会前给 Joseph 提醒 + 进会链接 + 议程；"
            "会后按他给的记录/纪要整理决策、落跟进任务。详见 docs/meeting-platform-limits.md。"
        ),
        "setup_steps": google["setup_steps"],
    }


def connection_statuses() -> List[Dict[str, Any]]:
    """六个平台的连接状态（固定顺序，供 ``GET /v1/connections`` 用）。"""
    google = google_status()
    entries: List[Dict[str, Any]] = [
        {
            "platform": "gmail",
            "display_name": "Gmail",
            **google,
            "capabilities": ["读新邮件", "发邮件", "按规则进待办草稿"],
            "not_connected_note": "没接通：未读邮件列表为空、发邮件会报 409，不编假邮件。",
        },
        {
            "platform": "calendar",
            "display_name": "Google 日历",
            **google,
            "capabilities": ["读日程", "新建/改期/删日程", "同步进本机日历", "与任务、会议打通"],
            "not_connected_note": "没接通：只显示本机日历的日程，Google 那边的不显示、不编。",
        },
        {
            "platform": "github",
            "display_name": "GitHub",
            **github_status(),
            "capabilities": ["仓库动态", "issue 状态", "PR 状态"],
            "not_connected_note": "没接通：摘要为空、报 not_configured，不编仓库数据。",
        },
        {
            "platform": "whatsapp",
            "display_name": "WhatsApp（Twilio Business）",
            **whatsapp_status(),
            "capabilities": ["收消息回消息", "Owner 说“任务：…”记待办草稿", "审批卡文字版"],
            "not_connected_note": "没接通：收不到、发不出，webhook 验签直接 401，不假装在线。",
        },
        {
            "platform": "meet",
            "display_name": "视频会议（Google Meet）",
            **meet_status(),
            "capabilities": ["会前提醒+进会入口", "会后决策记录+跟进任务"],
            "not_connected_note": "没接通 Google 日历时：只用本机日历里的会议，Meet 链接从日程里抠，Bot 仍不进会。",
        },
        {
            "platform": "jiefuai",
            "display_name": "杰夫AI（聚合平台）",
            **jiefuai_status(),
            "capabilities": ["一个 Key 调多家模型（预设，未验证）"],
            "not_connected_note": "预设而已：没填 Key、没验证可用，不要当成已接通。",
        },
    ]
    return entries


def connection_status(platform: str) -> Dict[str, Any]:
    """单个平台的状态；没有这个平台时抛 KeyError。"""
    key = (platform or "").strip().lower()
    for entry in connection_statuses():
        if entry["platform"] == key:
            return entry
    raise KeyError(f"没有这个连接平台：{platform}")
