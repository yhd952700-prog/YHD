#!/usr/bin/env python3
"""文档 ↔ 控制台 UI 一致性检查。

用途：
  防止面向用户的文档里再次出现控制台根本不存在的页面名、按钮名、旧端口、
  旧路由或旧品牌。默认只做“契约式”检查（见下方常量），稳定、可进 CI；
  `--report-quoted` 会额外列出文档中 「...」 引号内、前端找不到的中文名词，供人工确认。

用法：
  python scripts/check_docs_ui_terms.py
  python scripts/check_docs_ui_terms.py --report-quoted
  python scripts/check_docs_ui_terms.py --verbose

退出码：0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "apps" / "console" / "console" / "src"
BACKEND_SRC = ROOT / "src"
QUICKSTART = ROOT / "docs" / "knowledge_base" / "getting-started" / "quickstart.md"

# 只检查面向用户的文档；规格稿、归档、API 规划稿不在此列。
USER_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "INSTALL-WINDOWS.md",
    ROOT / "docs" / "END-USER-GUIDE.md",
    ROOT / "docs" / "USER-GUIDE.md",
    ROOT / "docs" / "faq.md",
    ROOT / "docs" / "mobile-access.md",
    ROOT / "docs" / "release-announcement.md",
    ROOT / "docs" / "knowledge_base" / "README.md",
    ROOT / "docs" / "knowledge_base" / "getting-started" / "quickstart.md",
    ROOT / "docs" / "knowledge_base" / "getting-started" / "installation.md",
    ROOT / "docs" / "knowledge_base" / "user-guide" / "agent_guide.md",
    ROOT / "docs" / "knowledge_base" / "faq" / "faq.md",
    ROOT / "docs" / "tutorials" / "build_your_first_agent.md",
]

# 文档里允许提到的界面名词；必须能在 apps/console/console/src 找到。
REQUIRED_UI_TERMS = [
    "总览",
    "鎏灏管家",
    "我的员工",
    "创建员工",
    "工作台",
    "直接对话",
    "＋新会话",
    "单独指派一个任务",
    "派给它",
    "目标与任务",
    "系统设置",
    "模型管理",
    "测试连接",
    "设为默认",
    "恢复码",
    "生成新的恢复码",
    "下载恢复材料（未加密）",
    "备份与迁移",
    "成员与权限管理",
    "遇到问题",
    "一键诊断包",
    "导出诊断包",
    "工作区文件",
    "我的 Key",
]

# 由后端返回、但会直接显示在界面上的文案；允许在后端源码中找到。
REQUIRED_BACKEND_TERMS = {
    "账号或密码不正确": BACKEND_SRC,
}

# 面向用户文档中禁止再出现的旧名词/旧路径/旧端口。
# allow_line_contains：该行是在说明“旧称/已失效”，属于合法解释，跳过。
BANNED_TERMS = {
    "智能体管理": {"reason": "控制台没有这个页面，实际叫「我的员工」"},
    "新建智能体": {"reason": "控制台没有这个按钮，实际叫「创建员工」"},
    "左侧会话": {"reason": "侧栏没有「会话」，实际是「工作台/管家」"},
    "新建会话": {"reason": "实际按钮是「＋新会话」"},
    "配置中心": {"reason": "控制台没有「配置中心」，实际叫「系统设置」"},
    "只显示文件名": {"reason": "恢复码已在登录页直接显示"},
    "可能拿不到码": {"reason": "恢复码已在登录页直接显示"},
    "凭据无效": {"reason": "实际错误文案是「账号或密码不正确」"},
    "本地 LLM 推理，必需": {"reason": "Ollama 是可选项，也可走云端"},
    ":8000": {"reason": "默认端口是 8080"},
    "鎏灏 X": {
        "reason": "旧品牌已收敛为「鎏灏智能中枢」",
        "allow_line_contains": ["旧称", "历史名称"],
    },
    "/v1/agents": {
        "reason": "网关未挂载该路由，请用 /v1/employees",
        "allow_line_contains": ["已失效", "旧文档", "旧版", "未挂载"],
    },
    "/v1/tasks": {
        "reason": "网关未挂载该路由，请用 /v1/goals",
        "allow_line_contains": ["已失效", "旧文档", "旧版", "未挂载"],
    },
    "/v1/providers/models": {
        "reason": "实际路由是 /v1/models/providers",
        "allow_line_contains": ["已失效", "旧文档", "旧版"],
    },
    '"status": "healthy"': {"reason": '实际返回 status: "ok"'},
    "9/9 tests passed": {"reason": "冒烟用例数量会变化，不要写死"},
    '"version": "3.0.0"': {"reason": "当前版本是 0.1.1"},
}

# 快速开始表格里出现的路由，必须是网关真实存在的。
REQUIRED_ROUTES = {
    "/v1/health",
    "/v1/ready",
    "/v1/models",
    "/v1/models/providers",
    "/v1/employees",
    "/v1/employees/{employee_id}",
    "/v1/goals",
    "/v1/goals/assign",
}

# 引号内名词的已知非界面项（Windows 弹窗、概念、外部站点等）。
QUOTED_ALLOWLIST = {
    "更多信息 → 仍要运行",
    "把正在运行的程序关掉再继续",
    "是否同时删除你的数据",
    "API Keys",
    "API",
    "SDK",
    "OSS",
    "MCP",
    "OpenAI",
    "Ollama",
    "DeepSeek",
    "Kimi",
    "Anthropic",
    "Gemini",
    "Windows",
    "PowerShell",
    "开始菜单",
    "设置",
    "应用",
    "系统",
    "发送",
    "停止",
    "退出",
    "保存修改",
    "添加模型",
    "删除",
    "复制",
    "编辑",
    "详情",
    "刷新",
    "重试",
    "取消",
    "确认",
    "完成",
    "下一步",
    "返回",
    "关闭",
    "重启",
    "继续",
    "开始",
    "打开文件夹",
    "打开日志",
    "检查更新",
}

# 关键的“文档必须包含正确值”断言。
DOC_CONTENT_CHECKS = [
    (ROOT / "docs" / "mobile-access.md", "127.0.0.1:8080", "手机访问文档必须写默认 8080"),
    (ROOT / "docs" / "knowledge_base" / "getting-started" / "installation.md", '"version": "0.1.1"', "安装指南健康检查示例必须是 0.1.1"),
    (ROOT / "docs" / "END-USER-GUIDE.md", "仅根账号可见", "必须标注系统设置仅根账号可见"),
    (ROOT / "docs" / "END-USER-GUIDE.md", "手机端点底部 **管家**", "手机端聊天入口必须是管家"),
]

QUOTED_RE = re.compile(r"「([^」\n]{2,24})」")
ROUTE_RE = re.compile(r"`(/v1/[A-Za-z0-9_{}/-]+)`")

# 诊断字段门禁：用户会照着文档去日志/诊断包里搜字段名，字段一旦改名，
# 文档不改就会让用户搜到空气。这里只检查“用户照抄”的区段，命中即断言：
# 文档里的 snake_case 字段名必须能在产出它们的源码里找到。
DIAG_FIELD_SECTIONS = [
    (
        ROOT / "docs" / "END-USER-GUIDE.md",
        "## 我的软件打不开：六类故障对照",
        "## 忘记密码",
    ),
    (
        ROOT / "docs" / "TROUBLESHOOTING-USER-20261108.md",
        "## 0. 60 秒定位",
        "## 一、",
    ),
]
DIAG_FIELD_SOURCES = [
    BACKEND_SRC / "desktop" / "diagnostics.py",   # diagnosis.json / ports.json / installation.json / db_check.json / static_assets.json
    BACKEND_SRC / "ai" / "liuhao.py",             # error.log: error_class / errno / dns_host / key_fp / timeout_s
    BACKEND_SRC / "observability" / "boot_report.py",  # bootstrap.log: sqlite_msg / free_bytes / winerror
    BACKEND_SRC / "bootstrap_log.py",             # bootstrap.log: evt
]
# 无下划线、但同样是用户要搜的字段名。
DIAG_FIELD_EXTRA = {"errno", "winerror", "result", "missing"}
BACKTICK_SPAN_RE = re.compile(r"`([^`\n]*)`")
FIELD_ASSIGN_RE = re.compile(r"\b([a-z][a-z0-9_]*)=")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def diag_source_vocab() -> set[str]:
    """收集诊断/日志字段源码里出现过的标识符。"""
    text = "\n".join(read_text(path) for path in DIAG_FIELD_SOURCES)
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))


def iter_frontend_files():
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        name = path.name
        if ".test." in name or ".spec." in name or "__tests__" in path.parts:
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="检查文档与前端 UI 名词/路由/端口是否一致")
    parser.add_argument("--report-quoted", action="store_true", help="列出引号内未在前端找到的候选名词（仅提示，不判失败）")
    parser.add_argument("--verbose", action="store_true", help="打印通过项")
    args = parser.parse_args()

    failures: list[str] = []
    reports: list[str] = []

    frontend_text = "\n".join(read_text(p) for p in iter_frontend_files())
    if not frontend_text:
        failures.append(f"前端源码目录为空或不存在：{FRONTEND_SRC}")

    for term in REQUIRED_UI_TERMS:
        if term not in frontend_text:
            failures.append(f"界面名词在前端找不到：{term}")
        elif args.verbose:
            print(f"[OK] 前端存在：{term}")

    for term, root in REQUIRED_BACKEND_TERMS.items():
        backend_text = "\n".join(read_text(p) for p in root.rglob("*.py"))
        if term not in backend_text:
            failures.append(f"界面文案在后端找不到：{term}")

    for doc in USER_DOCS:
        text = read_text(doc)
        if not text:
            failures.append(f"用户文档缺失：{doc.relative_to(ROOT)}")
            continue
        lines = text.splitlines()
        for term, meta in BANNED_TERMS.items():
            for line_no, line in enumerate(lines, start=1):
                if term not in line:
                    continue
                allowed = any(token in line for token in meta.get("allow_line_contains", []))
                if allowed:
                    continue
                rel = doc.relative_to(ROOT)
                failures.append(f"{rel}:{line_no} 出现禁用词「{term}」：{meta['reason']}")
        if args.verbose:
            print(f"[OK] 已扫描：{doc.relative_to(ROOT)}")

        if args.report_quoted:
            for match in QUOTED_RE.finditer(text):
                term = match.group(1).strip()
                if term in QUOTED_ALLOWLIST or term in frontend_text:
                    continue
                if re.search(r"[A-Za-z0-9]", term):
                    continue
                line_no = text[: match.start()].count("\n") + 1
                rel = doc.relative_to(ROOT)
                reports.append(f"{rel}:{line_no} 引号内名词未在前端找到：「{term}」")

    quickstart_text = read_text(QUICKSTART)
    if quickstart_text:
        for line_no, line in enumerate(quickstart_text.splitlines(), start=1):
            if any(token in line for token in ("已失效", "旧文档", "旧版", "未挂载")):
                continue
            for route in ROUTE_RE.findall(line):
                if route not in REQUIRED_ROUTES:
                    failures.append(
                        f"{QUICKSTART.relative_to(ROOT)}:{line_no} 出现未登记路由：{route}"
                    )

    for doc, needle, why in DOC_CONTENT_CHECKS:
        text = read_text(doc)
        if needle not in text:
            failures.append(f"{doc.relative_to(ROOT)} 缺少断言：{why}（应包含 `{needle}`）")

    diag_vocab = diag_source_vocab()
    if not diag_vocab:
        failures.append("诊断字段源码不可读，无法校验文档里的字段名")
    diag_checked = 0
    for doc, start_marker, end_marker in DIAG_FIELD_SECTIONS:
        text = read_text(doc)
        if not text:
            failures.append(f"诊断字段区段所在文档缺失：{doc.relative_to(ROOT)}")
            continue
        start = text.find(start_marker)
        end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
        if start < 0 or end < 0:
            failures.append(
                f"{doc.relative_to(ROOT)} 找不到诊断字段区段"
                f"（`{start_marker}` … `{end_marker}`）"
            )
            continue
        section = text[start:end]
        base_line = text[:start].count("\n") + 1
        for offset, line in enumerate(section.splitlines()):
            tokens = set(FIELD_ASSIGN_RE.findall(line))
            for span in BACKTICK_SPAN_RE.findall(line):
                if re.fullmatch(r"[a-z][a-z0-9_]*", span.strip()):
                    tokens.add(span.strip())
            for token in tokens:
                if "_" not in token and token not in DIAG_FIELD_EXTRA:
                    continue
                diag_checked += 1
                if token not in diag_vocab:
                    failures.append(
                        f"{doc.relative_to(ROOT)}:{base_line + offset} 诊断字段 `{token}` "
                        f"在源码中找不到（字段可能已改名）"
                    )
    if args.verbose:
        print(f"[OK] 诊断字段已核对：{diag_checked} 个（来源 {len(DIAG_FIELD_SOURCES)} 个文件）")

    if reports:
        print(f"\n[INFO] 以下引号内名词未在前端找到，请人工确认是否为界面名词（{len(reports)} 条）：")
        for item in reports:
            print(f"  - {item}")

    if failures:
        print(f"\n[FAIL] 共 {len(failures)} 项不一致：")
        for item in failures:
            print(f"  - {item}")
        return 1

    print(
        f"\n[OK] 文档↔UI 检查通过（{len(USER_DOCS)} 份用户文档，"
        f"{len(REQUIRED_UI_TERMS)} 个界面名词，{diag_checked} 个诊断字段）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
