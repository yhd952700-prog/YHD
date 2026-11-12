"""多账号 8 项验收 · CI 入口（一条命令）。

跑法与 CI 用法::

    .venv\\Scripts\\python.exe scripts/acceptance/run_multiuser_acceptance.py
    .venv\\Scripts\\python.exe scripts/acceptance/run_multiuser_acceptance.py \\
        --port-base 18080 --data-root .acceptance\\multiuser \\
        --json-report .acceptance\\multiuser\\report.json

输出固定为「8 行通过表 + 失败明细」：
* 每行对应《需求对齐表》§6.1–6.8，给出检查数、是否通过、失败项；
* 失败明细逐条列出，并标注是否为**已知、已转交**的问题；
* 退出码：0 = 全绿或只剩已知问题；1 = 出现新的失败（可直接挡 CI）。

已知问题白名单只用于“已转交其他线修复、本线不重复修”的项；新增失败必须
先修好或显式登记，不能靠白名单静默通过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import account_tree_e2e as e2e  # noqa: E402

#: 需求项标题（与控制台/交付文档一致）。
TITLES: Dict[str, str] = {
    "6.1": "主账号控制所有子账号权限",
    "6.2": "子账号只控自己的下级、不能越级",
    "6.3": "主账号可用子账号 Key，子账号只能用自己的",
    "6.4": "子账号记忆归自己、主账号可用全部",
    "6.5": "子账号不能知道自己是子账号",
    "6.6": "主账号换机/忘密有解法",
    "6.7": "主账号打包带走全部",
    "6.8": "成员无需邀请码自助注册",
}

#: 已转交其他线修复的问题：这里只登记“检查项 · 检查名”，不修、不隐瞒。
KNOWN_ISSUES: Dict[str, str] = {
    "备份导出排除 · 导出清单记录了排除项": (
        "已知、已转交：导出质量线修复 export_data_snapshot 的 excluded_protected 计数"
    ),
}


def _item_to_requirement() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for requirement, items in e2e.REQUIREMENT_ITEMS.items():
        for item in items:
            mapping[item] = requirement
    return mapping


def summarise(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """把逐项结果按 6.1–6.8 归并；返回 ``(分组, 未登记检查项)``。"""
    item_to_requirement = _item_to_requirement()
    groups: Dict[str, Dict[str, Any]] = {
        requirement: {"rows": [], "failed": [], "known": [], "unmapped": False}
        for requirement in e2e.REQUIREMENT_ITEMS
    }
    unmapped: List[str] = []
    for row in rows:
        requirement = item_to_requirement.get(str(row.get("item")))
        if requirement is None:
            unmapped.append(str(row.get("item")))
            continue
        group = groups[requirement]
        group["rows"].append(row)
        if not row.get("ok"):
            key = f"{row.get('item')} · {row.get('check')}"
            if key in KNOWN_ISSUES:
                group["known"].append(key)
            else:
                group["failed"].append(key)
    return groups, sorted(set(unmapped))


def _verdict(group: Dict[str, Any]) -> str:
    if not group["rows"]:
        return "未执行"
    if group["failed"]:
        return "不通过"
    if group["known"]:
        return "已知"
    return "通过"


def _mark(verdict: str) -> str:
    return {"通过": "🟢", "已知": "🟡", "不通过": "🔴", "未执行": "⚪"}.get(verdict, "⚪")


def print_table(groups: Dict[str, Dict[str, Any]]) -> None:
    print("\n=== 多账号 8 项验收 ===")
    print("| 项 | 结论 | 检查 | 失败 | 已知 | 需求 |")
    print("|---|---|---|---|---|---|")
    for requirement in sorted(TITLES):
        group = groups[requirement]
        verdict = _verdict(group)
        print(
            f"| {requirement} | {_mark(verdict)} {verdict} | "
            f"{len(group['rows'])} | {len(group['failed'])} | "
            f"{len(group['known'])} | {TITLES[requirement]} |"
        )


def print_failures(groups: Dict[str, Dict[str, Any]]) -> None:
    known = [(requirement, item) for requirement in sorted(groups)
             for item in groups[requirement]["known"]]
    failed = [(requirement, item) for requirement in sorted(groups)
              for item in groups[requirement]["failed"]]
    print("\n=== 失败明细 ===")
    if not known and not failed:
        print("无")
        return
    for requirement, item in known:
        print(f"[已知] {requirement} · {item}")
        print(f"       {KNOWN_ISSUES.get(item, '')}")
    for requirement, item in failed:
        print(f"[失败] {requirement} · {item}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多账号 8 项验收（CI 入口）")
    parser.add_argument("--port-base", type=int, default=18080,
                        help="固定端口基准（默认 18080；各实例顺序递增）")
    parser.add_argument("--data-root", default=os.path.join(".acceptance", "multiuser"),
                        help="独立数据根目录（默认 .acceptance/multiuser）")
    parser.add_argument("--json-report", default=None,
                        help="逐项结果 JSON 归档路径（默认 <data-root>/report.json）")
    parser.add_argument("--strict", action="store_true",
                        help="已知问题也视为失败（用于问题转交后的收口验证）")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    data_root = os.path.abspath(args.data_root)
    json_report = os.path.abspath(
        args.json_report or os.path.join(data_root, "report.json")
    )
    e2e.configure_harness(port_base=args.port_base, data_root=data_root)
    report = e2e.run()

    groups, unmapped = summarise(report.rows)
    print_table(groups)
    print_failures(groups)

    failed_total = sum(len(group["failed"]) for group in groups.values())
    known_total = sum(len(group["known"]) for group in groups.values())
    print(
        f"\n合计 {len(report.rows)} 项检查："
        f"{len(report.rows) - failed_total - known_total} 通过，"
        f"{known_total} 已知，{failed_total} 失败"
    )
    if unmapped:
        print(f"未登记到 6.1–6.8 的检查项：{unmapped}")

    os.makedirs(os.path.dirname(json_report) or ".", exist_ok=True)
    with open(json_report, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "total": len(report.rows),
                "failed": failed_total,
                "known": known_total,
                "requirements": {
                    requirement: {
                        "title": TITLES[requirement],
                        "verdict": _verdict(group),
                        "checks": len(group["rows"]),
                        "failed": group["failed"],
                        "known": group["known"],
                    }
                    for requirement, group in groups.items()
                },
                "rows": report.rows,
            },
            handle,
            ensure_ascii=False,
            indent=2,
            # 逐项结果里可能带 set（权限集合等），归档时按文本落盘即可。
            default=str,
        )
    print(f"逐项结果：{json_report}")

    broken = failed_total > 0 or bool(unmapped)
    if args.strict and known_total:
        broken = True
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
