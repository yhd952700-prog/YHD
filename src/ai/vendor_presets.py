"""厂商预设表：单一规范源（A3r F1 大脑能换）。

这是全仓库**唯一**一份厂商预设表。以前它住在 ``src/gateway/models.py``
里，同时 ``src/ai/model_config.py``（``SUPPORTED_PROVIDERS``）、
``src/ai/providers.py``（``ProviderFactory`` 适配器表）、
``src/ai/model_config.py``（``_VENDOR_BY_HOST`` 展示映射）各有一份
与厂商有关的表——加一家新厂商要在几处同步改，容易漏。

现在分工是：

* **本文件**：预设表本身（端点 + 默认模型名 + 提示，永不带 Key）。
  新厂商 = 在下面加一行，见 :func:`validate_vendor_presets` 的校验规则。
* ``src/gateway/models.py``：只从这里 re-export（``_PROVIDER_PRESETS``、
  ``_OPENAI_COMPATIBLE_PRESETS``），HTTP 层与旧测试的导入路径不变。
* ``src/ai/model_config.py``：``SUPPORTED_PROVIDERS`` 是**协议适配器**
  名单（openai/anthropic/...），不是厂商名单；OpenAI 兼容厂商
  （Grok / Muse Wonder / 杰夫AI / 通义 / 智谱 / 豆包）都走
  ``provider=openai`` 适配器 + 本表的 base_url，不用加适配器。
  只有全新协议（不走 OpenAI 兼容）才需要新写适配器代码——那是
  真正要写代码的情况，见 :func:`validate_vendor_presets` 的说明。
* ``src/ai/model_config.py`` 的 ``_VENDOR_BY_HOST`` 仍是展示映射
  （日志里把主机名翻成厂商名），与本表主机名保持同步，见
  ``tests/test_vendor_presets.py::test_vendor_host_lists_in_sync``。

预设收录原则（7-4，见 tests/test_vendor_presets.py）：只收实测通过
的端点；例外（Grok / Muse Wonder / 杰夫AI）只做预设、未拿真 Key
跑通 T1–T7，正式“已验证”仍需 Joseph 的真 Key 实测；预设永不带 Key、
端点必须是 https（本地 Ollama 除外）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

#: Built-in provider presets. Only defaults for base_url/model -- never keys.
#: The console fills the form from these; a preset is not a credential source.
_PROVIDER_PRESETS: Dict[str, Dict[str, Optional[str]]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "moonshot": {
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-latest",
    },
    "google": {
        "label": "Google",
        "base_url": None,
        # 1.5 系列已于 2025-09 下线；沿用旧默认会让 Gemini 选项填完 Key 也只会拿到 404。
        "model": "gemini-2.5-flash",
    },
    "ollama": {
        "label": "Ollama（本地）",
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.1:8b",
    },
}

#: 走 OpenAI 兼容协议的国内厂商：没有独立适配器，但都有 ``/chat/completions``
#: 兼容端点。与通用入口 ``openai_compatible`` 的唯一差别是"端点预先填好"，
#: 用户仍然可以改。方舟的 model 是推理接入点 ID，这里留空并在 hint 里说明，
#: 避免用户把它当成模型显示名去填。
#:
#: MiniMax 暂不加入预设：其 OpenAI 兼容入口的路径形态已有公开文档
#:（国际 ``https://api.minimax.io/v1``、国内 ``https://api.minimaxi.com/v1``，
#:``POST /v1/chat/completions``，Bearer 鉴权；2026-11-13 无 Key 探针
#:``GET /v1/models`` 回 401，证明端点存在），但还没拿真 Key 跑通
#: T1–T7。按 7-4 原则“没实测过的端点不写进预设”，等真 Key 实测通过
#: 再加；届时同步补 _VENDOR_BY_HOST 已有展示映射之外的三处
#:（TRUSTED_VENDOR_HOSTS、egress 登记表、DEFAULT_BASE_URLS 按需）。
#:
#: Grok（xAI）是例外：C-额外任务明确要求把 GPT/Claude/Grok 三家接通
#: 配置项。2026-11-13 无 Key 探针 ``GET https://api.x.ai/v1/models``
#: 回 401（端点存在、等 Bearer 鉴权），前后端厂商归属映射已有
#:（``api.x.ai`` -> ``xai`` / “xAI（Grok）”），所以先以 OpenAI 兼容
#: 预设进配置面。**这不是 T1–T7 通过**：没拿真 Key 跑过连接/回话/
#: 计费/泄露/安全网关，正式“已验证”仍需 Joseph 的真 Key 实测。
#: 另有两处故意没动：``TRUSTED_VENDOR_HOSTS`` 与 egress 登记表——
#: 后者在 ``src/security``（禁区，本轮不动），缺一处同步测试就会变红，
#: 所以这两处留给 D 组在禁区例外下一起补。在此之前 Grok 调用在本沙箱
#: 会被 net_guard 拦在鉴权之前（沙箱 DNS 把 api.x.ai 解析到
#: 198.18/15 保留段、域名又不在可信名单里）；在 Joseph 真实 Windows
#: 上解析是公网地址可正常走到鉴权，或由 Owner 在设置白名单里加
#: ``api.x.ai``（临时验证已证实该路能走到 401）。见
#: ``docs/vendors-gpt-claude-grok.md``。
#: Muse Wonder（Meta Model API）：只做预设，不填 Key、不调真实接口。
#: Key 由 Joseph 自己去 dev.meta.ai 申请来填。价目（任务书给定）：
#: 输入 $4/百万 tokens、输出 $20/百万 tokens、缓存输入 $0.20，
#: 比 DeepSeek 贵不少。base_url 以 Meta 官方文档为准，如不通请按文档改。
#: 未拿真 Key 实测过连接/回话/计费，正式“已验证”仍需 Joseph 的真 Key 实测。
_OPENAI_COMPATIBLE_PRESETS: List[Dict[str, Optional[str]]] = [
    {
        "id": "muse_wonder",
        "label": "Muse Wonder（Meta Model API）",
        "base_url": "https://api.meta.ai/v1",
        "model": "muse-wonder",
        "hint": (
            "Key 去 dev.meta.ai 申请，自己填进来；比 DeepSeek 贵不少："
            "输入 $4/百万 tokens、输出 $20/百万 tokens、缓存输入 $0.20。"
            "端点以 Meta 官方文档为准，如不通请按文档改 base_url。"
        ),
    },
#: 杰夫AI（jiefuai）聚合平台：OpenAI 格式，一个 Key 可调多家模型
#:（按它网站写的 42 个已启用模型，含 DeepSeek/GPT/Claude/Grok/Gemini）。
#: 只做预设，不填 Key、不调真实接口。base_url 用 https://jiefuai.cc/v1
#:（jiefuai.vip 在沙箱连不上，不用它）。**没验证可用**：2026-11-14
#: 沙箱实测 jiefuai.cc 返回 401 Invalid token，jiefuai.vip 连不上
#:（08:52 重测仍断开，连绕开假解析直连真 IP 38.76.161.209 后 TLS
#: 握手也被拦）；但两个域名在沙箱都解析到内网段 198.18.x（跟之前
#: DeepSeek 同样的问题），结论不一定准，需 Joseph 在真 Windows 电脑
#: 上再测一次，或在杰夫AI网站上查 Key 的状态（余额、是否启用、绑哪个
#: 分组）；也可能是 Key 抄错、过期或未启用。正式“已验证”仍需他真机实测。
#: 与 Grok / Muse Wonder 同例：只进预设 + 展示映射，不进
#: TRUSTED_VENDOR_HOSTS、不进 egress 登记表。
    {
        "id": "jiefuai",
        "label": "杰夫AI（聚合平台，未验证）",
        "base_url": "https://jiefuai.cc/v1",
        "model": "deepseek-chat",
        "hint": (
            "OpenAI 格式聚合平台，一个 Key 可调多家模型（按网站写的 42 个已启用模型，"
            "含 DeepSeek/GPT/Claude/Grok/Gemini）；Key 自己在杰夫AI网站上查状态后填进来。"
            "没验证可用：2026-11-14 沙箱实测 jiefuai.cc 返回 401 Invalid token，"
            "jiefuai.vip 连不上（08:52 重测仍断开）；两个域名在沙箱都解析到内网段 198.18.x，"
            "结论不一定准，需在真 Windows 电脑上再测一次，或在杰夫AI网站上查 Key 状态；"
            "也可能是 Key 抄错、过期或未启用。"
        ),
    },
    {
        "id": "xai_grok",
        "label": "Grok（xAI）",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "hint": "填 xAI 控制台（console.x.ai）的 API Key；模型名更新快，实测时以控制台列表为准（2026-11-13 探针时默认 grok-4.7）。",
    },
    {
        "id": "dashscope",
        "label": "通义千问（DashScope 兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "hint": "填阿里云百炼/DashScope 的 API Key；模型名可改成 qwen-max 等。",
    },
    {
        "id": "zhipu",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "hint": "填智谱开放平台的 API Key；模型名可改成 glm-4-plus 等。",
    },
    {
        "id": "volcengine_ark",
        "label": "豆包 / 火山方舟（模型填接入点 ID）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "",
        "hint": "模型名要填方舟的推理接入点 ID（ep-…），不是模型显示名；请先在方舟控制台创建接入点。",
    },
]


def find_preset(preset_id: str) -> Optional[Dict[str, Optional[str]]]:
    """按 id 找一条兼容预设；内置 6 家找不到时返回 None（不编一条）。"""
    for item in _OPENAI_COMPATIBLE_PRESETS:
        if item.get("id") == preset_id:
            return dict(item)
    return None


def validate_vendor_presets(
    provider_presets: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
    compat_presets: Optional[List[Dict[str, Optional[str]]]] = None,
    supported_providers: Optional[object] = None,
) -> List[str]:
    """校验预设表：返回问题清单（空 = 通过）。

    规则（新厂商加一行之前先对一遍）：

    1. 每条预设都没有 ``api_key`` 字段（预设永不带 Key）；
    2. ``base_url`` 是 https（本地 Ollama 的 ``http://127.0.0.1`` 除外）；
    3. 内置 6 家的 ``provider`` 必须在 ``SUPPORTED_PROVIDERS`` 里；
       兼容预设一律走 ``provider=openai`` 适配器（调用方在
       ``/models/presets`` 里写死），所以这里只校验它们有 id/label/
       base_url/model 四个键，不校验 provider 名；
    4. id 不重复；model 名非空（方舟是例外：模型名填接入点 ID，
       预设里留空并在 hint 里说明）。
    """
    provider_presets = (
        _PROVIDER_PRESETS if provider_presets is None else provider_presets
    )
    compat_presets = (
        _OPENAI_COMPATIBLE_PRESETS if compat_presets is None else compat_presets
    )
    if supported_providers is None:
        from .model_config import SUPPORTED_PROVIDERS

        supported_providers = SUPPORTED_PROVIDERS
    problems: List[str] = []
    seen: set = set()
    for name, preset in provider_presets.items():
        if name in seen:
            problems.append(f"内置预设 id 重复：{name}")
        seen.add(name)
        if "api_key" in preset:
            problems.append(f"预设 {name} 带 api_key（预设永不带 Key）")
        base_url = str(preset.get("base_url") or "")
        if base_url and not base_url.startswith("https://") and not base_url.startswith("http://127.0.0.1"):
            problems.append(f"预设 {name} 的 base_url 不是 https：{base_url}")
        if name not in supported_providers:  # type: ignore[operator]
            problems.append(f"预设 {name} 的 provider 不在 SUPPORTED_PROVIDERS 里")
        if not str(preset.get("model") or "").strip():
            problems.append(f"预设 {name} 没有默认模型名")
    for item in compat_presets:
        preset_id = str(item.get("id") or "")
        if not preset_id:
            problems.append("兼容预设有空 id")
            continue
        if preset_id in seen:
            problems.append(f"兼容预设 id 重复：{preset_id}")
        seen.add(preset_id)
        if "api_key" in item:
            problems.append(f"预设 {preset_id} 带 api_key（预设永不带 Key）")
        for key in ("label", "base_url", "model"):
            if key not in item:
                problems.append(f"预设 {preset_id} 缺少 {key}")
        base_url = str(item.get("base_url") or "")
        if base_url and not base_url.startswith("https://"):
            problems.append(f"预设 {preset_id} 的 base_url 不是 https：{base_url}")
        if preset_id != "volcengine_ark" and not str(item.get("model") or "").strip():
            problems.append(f"预设 {preset_id} 没有默认模型名")
    return problems


__all__ = [
    "_OPENAI_COMPATIBLE_PRESETS",
    "_PROVIDER_PRESETS",
    "find_preset",
    "validate_vendor_presets",
]
