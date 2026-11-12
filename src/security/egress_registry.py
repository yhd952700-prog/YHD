"""出站主机登记表：谁在什么时候把什么发到哪个域名。

为什么需要它
------------
一次性抓包只能证明"当时没发"；它拦不住下一个人在新代码里悄悄加一个
``urllib.request.urlopen("https://<某个未登记域名>/...")``。本表把"允许
出站的主机"变成一份**必须显式登记**的清单：

* 每条登记说明：哪个域名、什么功能触发、发什么字段、是否含个人信息、
  默认状态、在哪份用户可见文案里披露过；
* ``tests/security/test_egress_registry.py`` 扫描 ``src/`` 里出现的每个
  ``http(s)://`` 主机，逐个核对是否在本表里；**未登记即测试失败**；
* 不是真正发起请求的主机（结果链接、XML 命名空间、文档示例、清单占位符）
  也必须登记，只是 ``egress=False`` 并写清原因 —— 宁可多一条解释，也不留
  一个"看起来像出站但没人知道它是什么"的域名。

维护约定
--------
新增任何会发请求的域名时，先加一条 ``EgressEntry``，再写代码。新增只作为
链接展示、从不请求的域名时，同样加一条并设 ``egress=False``。本表只覆盖
随产品发布的 ``src/``；``scripts/`` 与前端构建产物不在扫描范围。

字段说明
--------
``feature``       功能名（人话，用于审计与客服解释）。
``trigger``       什么动作会触发（"用户发消息" / "用户开启联网检索" / ...）。
``fields``        实际发出去的字段（不要写"数据"这种模糊词）。
``personal_data`` 是否可能包含个人信息：``"否"`` / ``"是"`` / ``"视配置"``。
``default_state`` 默认状态：``"关闭"`` / ``"未配置即关闭"`` / ``"仅链接"``。
``disclosed_in``  用户可见的披露文案位置（仓库相对路径）；``egress=True`` 的
                  条目必须至少有一处，且文件必须真实存在。
``egress``        本进程是否真的会向该主机发起网络请求。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class EgressEntry:
    feature: str
    trigger: str
    fields: str
    personal_data: str
    default_state: str
    disclosed_in: Tuple[str, ...] = field(default_factory=tuple)
    egress: bool = True


_MODEL_DISCLOSURE = (
    "installer/assets/BEFORE-INSTALL.txt",
    "apps/console/console/src/components/ModelManager.tsx",
    "apps/console/console/src/pages/Settings.tsx",
    "apps/console/console/src/pages/About.tsx",
)

_RESEARCH_DISCLOSURE = (
    "installer/assets/BEFORE-INSTALL.txt",
    "scripts/first_run_wizard.py",
    "scripts/first_run_gui.py",
    "apps/console/console/src/pages/Settings.tsx",
    "apps/console/console/src/pages/About.tsx",
)

_INTEGRATION_DISCLOSURE = (
    "installer/assets/BEFORE-INSTALL.txt",
    "apps/console/console/src/pages/About.tsx",
)

_OPS_DISCLOSURE = (
    "installer/assets/BEFORE-INSTALL.txt",
    "apps/console/console/src/pages/About.tsx",
)

#: 域名 -> 登记。键必须是小写主机名（不含协议、路径、端口）。
EGRESS_REGISTRY: Dict[str, EgressEntry] = {
    # ── 云端模型 / 云端向量化（用户显式配置才会发生） ───────────────────
    "api.openai.com": EgressEntry(
        feature="云端模型（OpenAI）与云端向量化",
        trigger="用户配置 OpenAI 模型并发消息；或显式选择 openai embedding",
        fields="系统提示、最近对话、当前问题、知识片段、embedding 输入文本；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "api.anthropic.com": EgressEntry(
        feature="云端模型（Anthropic Claude）",
        trigger="用户配置 Anthropic 模型并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；x-api-key",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "generativelanguage.googleapis.com": EgressEntry(
        feature="云端模型（Google Gemini）",
        trigger="用户配置 Gemini 模型并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；x-goog-api-key",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "api.deepseek.com": EgressEntry(
        feature="云端模型（DeepSeek）",
        trigger="用户配置 DeepSeek 模型并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "api.moonshot.cn": EgressEntry(
        feature="云端模型（月之暗面 Kimi）",
        trigger="用户配置 Kimi 模型并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "api.moonshot.ai": EgressEntry(
        feature="云端模型（月之暗面 Kimi 海外端点）",
        trigger="用户配置 Kimi 模型并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "dashscope.aliyuncs.com": EgressEntry(
        feature="云端模型（阿里云百炼 / DashScope 兼容模式）",
        trigger="用户选择通义千问预设或自定义该地址并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "open.bigmodel.cn": EgressEntry(
        feature="云端模型（智谱 GLM）",
        trigger="用户选择智谱 GLM 预设或自定义该地址并发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    "ark.cn-beijing.volces.com": EgressEntry(
        feature="云端模型（豆包 / 火山方舟）",
        trigger="用户选择火山方舟预设并填入推理接入点 ID 后发消息",
        fields="系统提示、最近对话、当前问题、知识片段；Authorization: Bearer",
        personal_data="是",
        default_state="未配置即关闭",
        disclosed_in=_MODEL_DISCLOSURE,
    ),
    # ── 联网检索（默认关闭，开启需在设置页显式授权） ──────────────────────
    "hn.algolia.com": EgressEntry(
        feature="联网检索（Hacker News）",
        trigger="用户在设置页开启「联网检索」后，助手调用检索工具",
        fields="query=提炼后的关键词、tags=story、hitsPerPage；User-Agent",
        personal_data="否（关键词已清洗：去邮箱/电话/密钥/链接并截断）",
        default_state="关闭",
        disclosed_in=_RESEARCH_DISCLOSURE,
    ),
    "api.github.com": EgressEntry(
        feature="联网检索（GitHub）+ GitHub 集成 + 漏洞情报",
        trigger="开启联网检索后调用检索；用户主动连接 GitHub 集成；显式开启漏洞情报",
        fields="检索：q=提炼后的关键词；集成：仓库/Issue 操作与 OAuth Token；情报：仓库名与可选 GITHUB_TOKEN",
        personal_data="视配置（检索词已清洗；集成内容由用户动作决定）",
        default_state="检索关闭；集成未连接；情报关闭",
        disclosed_in=_RESEARCH_DISCLOSURE + _INTEGRATION_DISCLOSURE,
    ),
    "export.arxiv.org": EgressEntry(
        feature="联网检索（arXiv）",
        trigger="用户在设置页开启「联网检索」后，助手调用检索工具",
        fields="search_query=all:提炼后的关键词、start、max_results；User-Agent",
        personal_data="否（关键词已清洗：去邮箱/电话/密钥/链接并截断）",
        default_state="关闭",
        disclosed_in=_RESEARCH_DISCLOSURE,
    ),
    # ── 运维可选功能（默认全部关闭） ─────────────────────────────────────
    "api.osv.dev": EgressEntry(
        feature="依赖漏洞情报（OSV）",
        trigger="运维显式设置 LIUHAO_INTEL_ALLOW_NETWORK=1 后扫描依赖",
        fields="包名、生态、版本号",
        personal_data="否",
        default_state="关闭",
        disclosed_in=_OPS_DISCLOSURE,
    ),
    # ── 第三方集成（只有用户主动连接后才会请求） ─────────────────────────
    "api.airtable.com": EgressEntry(
        feature="Airtable 集成", trigger="用户连接 Airtable 并执行动作",
        fields="Base/Table/字段内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.box.com": EgressEntry(
        feature="Box 集成", trigger="用户连接 Box 并执行动作",
        fields="文件元数据/内容与 OAuth Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "upload.box.com": EgressEntry(
        feature="Box 集成（上传）", trigger="用户连接 Box 并上传文件",
        fields="文件内容与 OAuth Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.brevo.com": EgressEntry(
        feature="Brevo 集成", trigger="用户连接 Brevo 并发送邮件/联系人",
        fields="收件人、邮件内容、API Key", personal_data="是",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.clickup.com": EgressEntry(
        feature="ClickUp 集成", trigger="用户连接 ClickUp 并执行动作",
        fields="任务内容与 API Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.dropboxapi.com": EgressEntry(
        feature="Dropbox 集成", trigger="用户连接 Dropbox 并执行动作",
        fields="文件元数据/路径与 OAuth Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "content.dropboxapi.com": EgressEntry(
        feature="Dropbox 集成（内容传输）", trigger="用户连接 Dropbox 并上传/下载",
        fields="文件内容与 OAuth Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.figma.com": EgressEntry(
        feature="Figma 集成", trigger="用户连接 Figma 并执行动作",
        fields="文件/评论内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.hubapi.com": EgressEntry(
        feature="HubSpot 集成", trigger="用户连接 HubSpot 并执行动作",
        fields="联系人/交易内容与 Token", personal_data="是",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.linear.app": EgressEntry(
        feature="Linear 集成", trigger="用户连接 Linear 并执行动作",
        fields="Issue 内容与 API Key", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.medium.com": EgressEntry(
        feature="Medium 集成", trigger="用户连接 Medium 并发布文章",
        fields="文章内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.monday.com": EgressEntry(
        feature="Monday.com 集成", trigger="用户连接 Monday 并执行动作",
        fields="看板/条目内容与 API Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.notion.com": EgressEntry(
        feature="Notion 集成", trigger="用户连接 Notion 并执行动作",
        fields="页面内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.pagerduty.com": EgressEntry(
        feature="PagerDuty 集成", trigger="用户连接 PagerDuty 并触发事件",
        fields="事件内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "na1.salesforce.com": EgressEntry(
        feature="Salesforce 集成", trigger="用户连接 Salesforce 并执行动作",
        fields="对象字段内容与 OAuth Token", personal_data="是",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.stripe.com": EgressEntry(
        feature="Stripe 集成", trigger="用户连接 Stripe 并执行动作",
        fields="客户/订单字段与 Secret Key", personal_data="是",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.telegram.org": EgressEntry(
        feature="Telegram 集成", trigger="用户连接 Telegram 并发送消息",
        fields="消息文本、chat_id、Bot Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.trello.com": EgressEntry(
        feature="Trello 集成", trigger="用户连接 Trello 并执行动作",
        fields="看板/卡片内容与 API Key/Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "api.twilio.com": EgressEntry(
        feature="Twilio 集成", trigger="用户连接 Twilio 并发送短信",
        fields="手机号、短信内容、Account SID/Token", personal_data="是",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "app.asana.com": EgressEntry(
        feature="Asana 集成", trigger="用户连接 Asana 并执行动作",
        fields="任务内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "discord.com": EgressEntry(
        feature="Discord 集成", trigger="用户连接 Discord 并发送消息",
        fields="消息文本、频道/Webhook 与 Bot Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "gitlab.com": EgressEntry(
        feature="GitLab 集成", trigger="用户连接 GitLab 并执行动作",
        fields="项目/Issue 内容与 Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    "slack.com": EgressEntry(
        feature="Slack 集成", trigger="用户连接 Slack 并发送消息",
        fields="消息文本、频道、OAuth Token", personal_data="视配置",
        default_state="未连接", disclosed_in=_INTEGRATION_DISCLOSURE,
    ),
    # ── 只作为链接/占位符出现，本进程不会请求（登记以消除"看起来像出站"） ──
    "news.ycombinator.com": EgressEntry(
        feature="Hacker News 结果链接回退", trigger="检索结果缺少外链时拼出可点击链接",
        fields="无（仅展示）", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "github.com": EgressEntry(
        feature="GitHub 仓库/公告链接", trigger="展示解析后的仓库或安全公告链接",
        fields="无（仅展示）", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "osv.dev": EgressEntry(
        feature="OSV 公告链接", trigger="展示漏洞公告的可点击链接",
        fields="无（仅展示）", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "www.w3.org": EgressEntry(
        feature="XML 命名空间常量（Atom）", trigger="解析 arXiv 响应时使用，不发起请求",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "sqlite.org": EgressEntry(
        feature="SQLite 错误码文档链接", trigger="仅出现在源码注释里",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "liuhao.ai": EgressEntry(
        feature="Discord 请求头里的产品主页标识", trigger="仅作为 User-Agent 文本，不发起请求",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "example.com": EgressEntry(
        feature="文档/示例占位域名", trigger="仅出现在注释与 docstring",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "example": EgressEntry(
        feature="CLI 帮助示例占位", trigger="仅出现在 --manifest-url 示例",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "liuhao-ai-os.example.com": EgressEntry(
        feature="Helm 清单占位主页/图标", trigger="只写进生成的 chart 元数据，本进程不请求",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "charts.liuhao-ai-os.example.com": EgressEntry(
        feature="Helm 清单占位源地址", trigger="只写进生成的 chart 元数据，本进程不请求",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "charts.bitnami.com": EgressEntry(
        feature="Helm 依赖仓库地址", trigger="只写进生成的 chart 依赖声明，由 Helm 自行拉取",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "model-registry": EgressEntry(
        feature="集群内服务名", trigger="只写进生成的 Helm 环境变量，由部署环境解析",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "user": EgressEntry(
        feature="脱敏注释里的 URL userinfo 示例", trigger="仅出现在注释里",
        fields="无", personal_data="否", default_state="仅链接",
        egress=False,
    ),
    "your-domain.atlassian.net": EgressEntry(
        feature="Jira/Confluence 租户占位域名", trigger="用户需替换成自己的租户；实际请求发往用户配置的 base_url",
        fields="Issue/页面内容与 Token（发往用户自己的站点）", personal_data="视配置",
        default_state="仅占位符", disclosed_in=_INTEGRATION_DISCLOSURE,
        egress=False,
    ),
    # 调用链（为什么本进程不会请求它）：
    #   ``src/desktop/native_window.py`` 定义 ``WEBVIEW2_BOOTSTRAPPER_URL``
    #   （微软 Evergreen Bootstrapper 官方短链，约 1.6 MB 的联网安装器）；
    #   ``src/desktop/app.py::_run_native_window`` 在 ``webview2_version()``
    #   检测不到运行时时弹提示，用户选「去安装 WebView2」后执行
    #   ``webbrowser.open(WEBVIEW2_BOOTSTRAPPER_URL)`` —— 把 URL 交给**系统
    #   默认浏览器**打开，不经过本项目的 HTTP 客户端、``src/ai/net_guard.py``
    #   的运行时校验或任何出口拦截；``EGRESS_REGISTRY`` 本身只做登记与展示
    #   （``src/gateway/networks.py`` 读取），不拦截请求。
    #   登记依据：这是给用户安装缺失依赖用的微软官方地址，不是数据回传；
    #   ``egress=False`` 只说明"本进程不发请求"，不代表放宽任意域名。
    "go.microsoft.com": EgressEntry(
        feature="WebView2 Runtime 官方下载页（缺失提示里的「去安装 WebView2」按钮）",
        trigger="仅在检测不到 WebView2 时，用户点击「去安装 WebView2」；链接由系统默认浏览器打开",
        fields="无（本进程不发起请求，URL 交给系统浏览器）",
        personal_data="否",
        default_state="仅链接",
        egress=False,
    ),
}

#: 「关于与隐私」页的功能分组：组名 -> 主机清单（主机名仍是登记表的键，
#: 这里只负责归类，不复制任何说明字段）。新增真实出站主机时，要么加进某组，
#: 要么保留在 ``EGRESS_REGISTRY`` 里由 ``grouped_egress()`` 归到「其他」——
#: 页面不会因为漏归类而少显示一条。
EGRESS_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "云端模型与向量化（你配置 Key 后才会发生）",
        (
            "api.openai.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
            "api.deepseek.com",
            "api.moonshot.cn",
            "api.moonshot.ai",
        ),
    ),
    (
        "联网检索（默认关闭，开启后才发关键词）",
        (
            "hn.algolia.com",
            "api.github.com",
            "export.arxiv.org",
        ),
    ),
    (
        "依赖漏洞情报（运维显式开启）",
        ("api.osv.dev",),
    ),
    (
        "第三方集成（你主动连接后才会请求）",
        (
            "api.airtable.com",
            "api.box.com",
            "upload.box.com",
            "api.brevo.com",
            "api.clickup.com",
            "api.dropboxapi.com",
            "content.dropboxapi.com",
            "api.figma.com",
            "api.hubapi.com",
            "api.linear.app",
            "api.medium.com",
            "api.monday.com",
            "api.notion.com",
            "api.pagerduty.com",
            "na1.salesforce.com",
            "api.stripe.com",
            "api.telegram.org",
            "api.trello.com",
            "api.twilio.com",
            "app.asana.com",
            "discord.com",
            "gitlab.com",
            "slack.com",
        ),
    ),
)


def _entry_payload(host: str, entry: EgressEntry) -> Dict[str, Any]:
    return {
        "host": host,
        "feature": entry.feature,
        "trigger": entry.trigger,
        "fields": entry.fields,
        "personal_data": entry.personal_data,
        "default_state": entry.default_state,
        "egress": entry.egress,
    }


def grouped_egress() -> Dict[str, Any]:
    """登记表按功能分组的只读投影，供「关于与隐私」页直接展示。

    页面拿到的每条记录都来自 ``EGRESS_REGISTRY`` 本身，不在前端写第二份；
    只在 ``egress=True`` 的记录里分组，未归类的新主机自动落到「其他」。
    """
    groups: List[Dict[str, Any]] = []
    covered: set[str] = set()
    for name, hosts in EGRESS_GROUPS:
        entries = [
            _entry_payload(host, EGRESS_REGISTRY[host])
            for host in hosts
            if host in EGRESS_REGISTRY and EGRESS_REGISTRY[host].egress
        ]
        covered.update(item["host"] for item in entries)
        if entries:
            groups.append({"name": name, "entries": entries})

    rest = [
        _entry_payload(host, entry)
        for host, entry in EGRESS_REGISTRY.items()
        if entry.egress and host not in covered
    ]
    if rest:
        groups.append({"name": "其他真实出站", "entries": rest})

    egress_count = sum(1 for entry in EGRESS_REGISTRY.values() if entry.egress)
    return {
        "total": len(EGRESS_REGISTRY),
        "egress_count": egress_count,
        "placeholder_count": len(EGRESS_REGISTRY) - egress_count,
        "groups": groups,
    }


#: 回环地址：不属于"出站"，扫描时直接跳过。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"})


def is_loopback(host: str) -> bool:
    name = str(host or "").strip().lower().strip("[]")
    if not name:
        return True
    if name in {item.strip("[]") for item in LOOPBACK_HOSTS}:
        return True
    return name.startswith("127.") or name.endswith(".localhost")


def lookup(host: str) -> EgressEntry | None:
    return EGRESS_REGISTRY.get(str(host or "").strip().lower())


def missing_hosts(hosts: Iterable[str]) -> List[str]:
    """返回未登记的出站主机（已排序、去重）；回环地址不算出站。"""
    return sorted(
        {
            str(host).strip().lower()
            for host in hosts
            if str(host).strip() and not is_loopback(str(host))
            and str(host).strip().lower() not in EGRESS_REGISTRY
        }
    )


__all__ = [
    "EGRESS_GROUPS",
    "EGRESS_REGISTRY",
    "EgressEntry",
    "LOOPBACK_HOSTS",
    "grouped_egress",
    "is_loopback",
    "lookup",
    "missing_hosts",
]
