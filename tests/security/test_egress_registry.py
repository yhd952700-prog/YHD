"""出站主机登记表回归：新代码偷偷连一个新域名，必须在这里变红。

为什么不是"再抓一次包"
----------------------
抓包只能证明**那一次运行**没有外发。真正要防的是下一个人加一行
``urlopen("https://collect.example.net")`` 而没人发现。本测试扫描 ``src/``
里所有 ``http(s)://`` 主机，逐个核对是否在 ``src/security/egress_registry.py``
登记；未登记即失败。

同时锁定"联网检索开启前"的四处文案：BEFORE-INSTALL、首启向导（CLI + GUI）、
关于页、设置页必须逐字包含同一句"只发关键词 / 不发对话与账号信息"。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Set

import pytest

from src.ai import web_research
from src.security.egress_registry import (
    EGRESS_REGISTRY,
    grouped_egress,
    is_loopback,
    missing_hosts,
)

REPO = Path(__file__).resolve().parents[2]
_HOST_RE = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9._-]*)")

RESEARCH_HOSTS = ("hn.algolia.com", "api.github.com", "export.arxiv.org")

DISCLOSURE_FILES = {
    "BEFORE-INSTALL": "installer/assets/BEFORE-INSTALL.txt",
    "首启向导 CLI": "scripts/first_run_wizard.py",
    "首启向导 GUI": "scripts/first_run_gui.py",
    "关于页": "apps/console/console/src/pages/About.tsx",
    "设置页": "apps/console/console/src/pages/Settings.tsx",
}


def _normalise(text: str) -> str:
    """去掉所有空白：代码换行/缩进不应影响"逐字"比对的结果。"""
    return re.sub(r"\s+", "", text)


def scanned_hosts() -> Dict[str, Set[str]]:
    """``src/`` 里出现的每个 http(s) 主机 -> 出现它的文件集合。"""
    found: Dict[str, Set[str]] = {}
    for path in (REPO / "src").rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for host in _HOST_RE.findall(text):
            name = host.lower()
            if is_loopback(name):
                continue
            found.setdefault(name, set()).add(str(path.relative_to(REPO)))
    return found


def test_scan_is_not_silently_empty() -> None:
    """扫描器自身必须真的在工作：否则"零未登记"只是因为它什么都没找到。"""
    hosts = scanned_hosts()
    assert len(hosts) >= 30, f"只扫描到 {len(hosts)} 个主机，扫描器可能失效"
    for host in RESEARCH_HOSTS:
        assert host in hosts, f"扫描器漏掉了已知出站主机 {host}"


def test_every_detected_host_is_registered() -> None:
    hosts = scanned_hosts()
    missing = missing_hosts(hosts)
    assert not missing, (
        "以下出站主机没有登记（新代码加域名前请先在 "
        "src/security/egress_registry.py 增加一条 EgressEntry）：\n"
        + "\n".join(
            f"  - {host}  出现在：{', '.join(sorted(hosts.get(host, [])))}"
            for host in missing
        )
    )


@pytest.mark.parametrize("host", sorted(EGRESS_REGISTRY))
def test_registry_entry_is_complete(host: str) -> None:
    entry = EGRESS_REGISTRY[host]
    for field in (
        "feature", "trigger", "fields", "personal_data", "default_state",
    ):
        assert str(getattr(entry, field)).strip(), f"{host} 的 {field} 为空"
    if entry.egress:
        assert entry.disclosed_in, f"{host} 会真实出站，但没登记披露位置"
        for rel in entry.disclosed_in:
            assert (REPO / rel).exists(), f"{host} 的披露文件不存在：{rel}"


def test_webview2_install_link_is_registered_without_egress() -> None:
    """钉住 WebView2 官方下载页：只交给系统浏览器，本进程不请求。

    ``go.microsoft.com`` 曾因"看起来像出站"而让本文件变红。结论是它不经过
    本进程的网络栈：``native_window.WEBVIEW2_BOOTSTRAPPER_URL`` 由
    ``app.py::_run_native_window`` 的 ``webbrowser.open(...)`` 交给系统默认
    浏览器。这里同时钉住"主机名"和"调用链"——哪一边改了都必须回来改登记。
    """
    from urllib.parse import urlparse

    from src.desktop.native_window import WEBVIEW2_BOOTSTRAPPER_URL
    from src.security.egress_registry import lookup

    host = urlparse(WEBVIEW2_BOOTSTRAPPER_URL).hostname
    assert host == "go.microsoft.com", f"下载页主机已变化：{host}，登记表需同步更新"
    entry = lookup(host)
    assert entry is not None, f"{host} 必须登记（这是给用户装 WebView2 的官方地址）"
    assert entry.egress is False, "下载页由系统浏览器打开，本进程不发起请求"
    assert "仅链接" in entry.default_state
    assert "系统" in entry.trigger, "必须写明由系统浏览器打开"

    app_source = (REPO / "src" / "desktop" / "app.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "webbrowser.open(WEBVIEW2_BOOTSTRAPPER_URL)" in app_source, (
        "调用链变了：下载页必须仍交给系统浏览器，而不是由本进程直接请求"
    )


def test_research_hosts_are_registered_off_by_default() -> None:
    for name, host in web_research.PROVIDER_HOSTS.items():
        entry = EGRESS_REGISTRY.get(host)
        assert entry is not None, f"检索源 {name} 的主机 {host} 未登记"
        assert entry.egress is True, f"检索源 {name} 必须标记为真实出站"
        assert "关闭" in entry.default_state, f"检索源 {name} 默认状态必须写明关闭"


def test_disclosure_copy_is_verbatim_in_four_places() -> None:
    """四处文案必须逐字包含同一句；不允许各处自己发挥。"""
    expected = _normalise(web_research.DISCLOSURE_CORE)
    assert expected, "DISCLOSURE_CORE 不能为空"
    for label, rel in DISCLOSURE_FILES.items():
        path = REPO / rel
        assert path.exists(), f"{label} 文案文件不存在：{rel}"
        body = _normalise(path.read_text(encoding="utf-8", errors="ignore"))
        assert expected in body, f"{label}（{rel}）缺少逐字一致的披露句"
        for host in RESEARCH_HOSTS:
            assert host in body, f"{label}（{rel}）缺少目标站点 {host}"


def test_settings_copy_states_immediate_effect() -> None:
    """关闭立即生效是对用户的承诺，不能只写在代码注释里。"""
    body = (REPO / DISCLOSURE_FILES["设置页"]).read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "关闭后立即生效" in body
    assert "无需重启" in body


def test_grouped_egress_covers_every_real_host_exactly_once() -> None:
    """关于页逐条清单必须覆盖全部真实出站主机，且一条不漏、一条不重。"""
    listing = grouped_egress()
    flat = [entry["host"] for group in listing["groups"] for entry in group["entries"]]
    expected = {
        host for host, entry in EGRESS_REGISTRY.items() if entry.egress
    }

    assert listing["total"] == len(EGRESS_REGISTRY)
    assert listing["egress_count"] == len(expected) == 36
    assert listing["placeholder_count"] == listing["total"] - listing["egress_count"]
    assert len(flat) == len(set(flat)), "同一主机不能在多个功能组里重复出现"
    assert set(flat) == expected, "分组后漏掉了真实出站主机"
    for group in listing["groups"]:
        assert group["name"].strip()
        for entry in group["entries"]:
            assert entry["feature"].strip()
            assert entry["trigger"].strip()
            assert entry["fields"].strip()
            assert entry["personal_data"].strip()
            assert entry["default_state"].strip()
