"""人可读导出 E2E：真机导出 → 解压成目录 → 检查「人能看懂、能拿走」。

验收的不是"能导出"，而是**用户视角**：

* 一个目录、有索引页（INDEX.md 逐份说明），会话是 Markdown 而不是 SQLite；
* 只有自己的数据；不含 API Key / 密码 / 恢复码；路径不含用户名或绝对路径；
* 能导回的（记忆、知识库）真的能导回且幂等，只读的（会话/目标/员工）写清楚。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/readable_export_e2e.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from scripts.acceptance.account_tree_e2e import (  # noqa: E402
    PASSWORDS,
    Instance,
    Report,
    configure_harness,
    request,
)

ROOT_KEY = "sk-readable-export-root-plain-000001"
MEMBER_KEY = "sk-readable-export-member-plain-0002"
GOAL_ID = "goal-readable-1"


def download_zip(url: str, token: str, payload: Optional[dict] = None) -> Tuple[int, bytes, Dict[str, str]]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            content_type = resp.headers.get_content_type()
            return resp.status, resp.read(), {
                "Content-Type": content_type,
                "Content-Disposition": resp.headers.get("Content-Disposition") or "",
            }
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), {}


def seed_goals(inst: Instance) -> None:
    """在服务启动前写入 goals 文件：目标创建会真的跑执行链，压测不需要那条路径。"""
    path = os.path.join(inst.env["LIUHAO_WORKSPACE_ROOT"], "liuhao_goals.json")
    tasks = [
        {"task_id": "t1", "status": "completed", "capability": "research",
         "owner_account_id": "root1"},
        {"task_id": "t2", "status": "completed", "capability": "write",
         "owner_account_id": "root1"},
    ]
    payload = {
        GOAL_ID: {
            "goal_id": GOAL_ID,
            "state": "completed",
            "natural_language": "把季度复盘整理成三条结论",
            "scope": "",
            "created_at": "2026-11-01T09:00:00+00:00",
            "updated_at": "2026-11-01T09:20:00+00:00",
            "owner_account_id": "root1",
            "tasks": tasks,
            "artifacts": [],
        }
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def username_fragments() -> List[str]:
    candidates = [
        os.environ.get("USERNAME") or "",
        os.environ.get("USER") or "",
    ]
    try:
        import getpass

        candidates.append(getpass.getuser())
    except Exception:  # noqa: BLE001
        pass
    return sorted({item.strip() for item in candidates if len(item.strip()) >= 3})


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="人可读导出 E2E")
    parser.add_argument("--port-base", type=int, default=27080)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    configure_harness(port_base=args.port_base)
    report = Report()
    inst = Instance("liuhao-readable-", start=False)
    seed_goals(inst)
    inst.start()
    workdir = os.path.join(inst.root, "exported")
    os.makedirs(workdir, exist_ok=True)
    try:
        # ---------------- 造数据（真实 HTTP） ----------------
        status, registered = request(
            "POST", inst.base + "/v1/accounts/register",
            {"principal": "root1", "password": PASSWORDS[0], "display_name": "主账号"},
        )
        recovery_code = str(registered.get("recovery_code") or "")
        _, login = request(
            "POST", inst.base + "/v1/auth/login",
            {"principal": "root1", "secret": PASSWORDS[0]},
        )
        root_token = login.get("access_token") or ""
        report.check("准备", "根账号就绪", (status, bool(root_token)), (200, True))

        _, created = request(
            "POST", inst.base + "/v1/accounts",
            {"principal": "member1", "role": "manager", "allow_key_sharing": True},
            token=root_token,
        )
        _, activated = request(
            "POST", inst.base + "/v1/accounts/activate",
            {"principal": "member1", "code": created.get("activation_code") or "",
             "password": PASSWORDS[1]},
        )
        member_token = activated.get("token") or ""

        _, root_model = request(
            "POST", inst.base + "/v1/models",
            {"name": "root-key", "provider": "mock", "model": "mock-1",
             "api_key": ROOT_KEY, "owner_principal": "root1"},
            token=root_token,
        )
        root_model_id = root_model.get("id") or ""
        _, member_model = request(
            "POST", inst.base + "/v1/models",
            {"name": "member-key", "provider": "mock", "model": "mock-1",
             "api_key": MEMBER_KEY, "owner_principal": "member1"},
            token=member_token,
        )
        member_model_id = member_model.get("id") or ""
        report.check("准备", "两个账号各配一个模型",
                     (bool(root_model_id), bool(member_model_id)), (True, True),
                     f"member 状态码={member_model.get('code') or member_model.get('detail')}")

        for session_id, message in (
            ("root-alpha", "帮我记下：客户 A 的续约窗口在 12 月。"),
            ("root-beta", "再记一条：本周五要交复盘。"),
        ):
            request("POST", inst.base + "/v1/chat",
                    {"message": message, "session_id": session_id,
                     "model_id": root_model_id, "use_knowledge": False},
                    token=root_token)
        request("POST", inst.base + "/v1/chat",
                {"message": "成员自己的私事", "session_id": "member-gamma",
                 "model_id": member_model_id, "use_knowledge": False},
                token=member_token)

        request("POST", inst.base + "/v1/memory/entries",
                {"text": "客户 A 续约窗口在 12 月", "source": "chat", "tags": ["客户"]},
                token=root_token)
        request("POST", inst.base + "/v1/memory/entries",
                {"text": "成员的私密记忆", "source": "chat", "tags": []},
                token=member_token)

        request("POST", inst.base + "/v1/employees",
                {"name": "研究助手", "agent_count": 1, "agent_types": ["research"],
                 "role_description": "负责资料检索与摘要"},
                token=root_token)
        request("POST", inst.base + "/v1/employees",
                {"name": "成员助手", "agent_count": 1, "agent_types": ["research"],
                 "role_description": "成员自己的员工"},
                token=member_token)

        # ---------------- 导出 ----------------
        export_started = time.perf_counter()
        status, blob, headers = download_zip(
            inst.base + "/v1/accounts/export/all", root_token, {"include_files": False}
        )
        export_ms = round((time.perf_counter() - export_started) * 1000, 1)
        filename = (headers.get("Content-Disposition") or "")
        report.check("导出", "导出返回 ZIP", (status, headers.get("Content-Type")), (200, "application/zip"),
                     f"bytes={len(blob)}，{export_ms} ms")
        report.check("导出", "文件名不含账号名",
                     any(name in filename for name in ("root1", "member1")), False,
                     filename)

        folder = os.path.join(workdir, "my-data")
        with zipfile.ZipFile(__import__("io").BytesIO(blob)) as archive:
            archive.extractall(folder)
            names = archive.namelist()
            texts = {}
            for name in names:
                if name.endswith(("/", ".md", ".json", ".csv")):
                    try:
                        texts[name] = archive.read(name).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        texts[name] = ""
        blob_scan = "\n".join(texts.values())

        # 1) 目录与索引页
        required = ["README.md", "INDEX.md", "manifest.json"]
        report.check("目录结构", "根目录有 README/INDEX/manifest",
                     [name for name in required if name in names], required,
                     f"条目数={len(names)}")
        conversation_files = [n for n in names if n.startswith("conversations/") and n.endswith(".md")]
        report.check("目录结构", "会话是 Markdown 而不是 SQLite",
                     (len(conversation_files) >= 2,
                      any(n.endswith((".sqlite", ".sqlite3", ".db")) for n in names)),
                     (True, False), f"会话文件={conversation_files}")
        index_text = texts.get("INDEX.md", "")
        listed = [
            token for token in ("conversations/", "memories.md", "memories.json",
                                "goals.md", "goals.json", "employees.md",
                                "employees.json", "manifest.json")
            if token in index_text
        ]
        report.check("索引页", "索引逐份说明文件用途", len(listed), 8, ", ".join(listed))
        report.check("索引页", "索引列出每个会话文件",
                     all(os.path.basename(name) in index_text for name in conversation_files),
                     True, f"会话={len(conversation_files)}")
        report.check("索引页", "索引写明是给人读的", "给人读" in index_text or "人可读" in index_text, True)

        # 2) 只有自己的数据
        root_md = "\n".join(texts[n] for n in conversation_files)
        report.check("数据边界", "根导出含自己的两个会话",
                     ("root-alpha" in root_md, "root-beta" in root_md), (True, True))
        report.check("数据边界", "根导出不含成员会话", "member-gamma" in blob_scan, False)
        report.check("数据边界", "根导出不含成员记忆", "成员的私密记忆" in blob_scan, False)
        report.check("数据边界", "根导出不含成员员工", "成员助手" in blob_scan, False)
        memories_doc = json.loads(texts["memories.json"])
        report.check("数据边界", "根记忆条数", len(memories_doc.get("entries") or []), 1,
                     (memories_doc.get("entries") or [{}])[0].get("text", "")[:40])
        goals_doc = json.loads(texts["goals.json"])
        report.check("数据边界", "目标随包导出",
                     [goal.get("goal_id") for goal in goals_doc.get("goals") or []], [GOAL_ID],
                     f"tasks={(goals_doc.get('goals') or [{}])[0].get('task_count')}")
        report.check("目标可读", "goals.md 写出目标原文",
                     "把季度复盘整理成三条结论" in texts.get("goals.md", ""), True)
        employees_doc = json.loads(texts["employees.json"])
        names_in_emp = sorted(
            str(row.get("name") or "") for row in employees_doc.get("employees") or []
        )
        report.check("员工可读", "employees.md/json 只有自己的员工",
                     (names_in_emp, "研究助手" in texts.get("employees.md", "")),
                     (["研究助手"], True))

        # 3) 不含凭据、不含用户名与绝对路径
        leaked = [
            label for label, needle in (
                ("根 Key", ROOT_KEY), ("成员 Key", MEMBER_KEY),
                ("根密码", PASSWORDS[0]), ("成员密码", PASSWORDS[1]),
                ('api_key 字段', '"api_key"'),
                ("恢复码明文", recovery_code),
            )
            if needle and needle in blob_scan
        ]
        report.check("隐私", "注册确实返回了恢复码（扫描才有意义）", bool(recovery_code), True,
                     f"长度={len(recovery_code)}")
        report.check("隐私", "不含 API Key / 密码 / 恢复码", leaked, [],
                     f"扫描 {len(blob_scan)} 字符 / {len(names)} 个条目")
        usernames = username_fragments()
        path_hits = [
            name for name in names
            if re.match(r"^[A-Za-z]:[\\/]", name)
            or name.startswith(("/", "\\"))
            or "\\users\\" in name.lower()
            or any(user.lower() in name.lower() for user in usernames)
        ]
        report.check("隐私", "条目路径不含用户名/绝对路径", path_hits, [],
                     f"本机用户名集合={usernames}")
        report.check("隐私", "内容里没有本机绝对路径",
                     any(re.search(r"[A-Za-z]:\\\\?[^\" ]+\\\\Users", text) for text in texts.values()),
                     False)

        # 4) 双向边界写清楚
        manifest = json.loads(texts["manifest.json"])
        report.check("回导口径", "manifest 隐私标志全为 false",
                     set(manifest["privacy"].values()), {False})
        report.check("回导口径", "只有记忆/知识库声明可回导",
                     sorted(manifest["import_supported"].keys()),
                     ["knowledge", "memories"])
        report.check("回导口径", "会话/目标/员工标为只读归档",
                     sorted(manifest["read_only_archive"]),
                     ["conversations", "employees", "files", "goals", "settings", "usage"])
        readme = texts.get("README.md", "")
        report.check(
            "回导口径",
            "README 明确「完整换机=命令行加密备份」",
            (
                "只读归档" in readme,
                "想完整搬到另一台机器" in readme,
                "命令行加密备份" in readme,
                "backup create --encrypt" in readme,
            ),
            (True, True, True, True),
            manifest.get("migration_hint", "")[:60],
        )
        report.check(
            "回导口径",
            "manifest.migration_hint 也指向命令行加密备份",
            "命令行加密备份" in str(manifest.get("migration_hint") or "")
            and "backup create --encrypt"
            in str(manifest.get("migration_hint") or ""),
            True,
            manifest.get("migration_hint", "")[:60],
        )

        # 5) 记忆真的能导回来，且二次导入幂等
        entries = memories_doc.get("entries") or []
        status, body = request(
            "POST", inst.base + "/v1/memory/import", {"entries": entries}, token=root_token
        )
        report.check("回导", "memories.json 可导入", status, 200, f"imported={body.get('imported')}")
        status, body = request(
            "POST", inst.base + "/v1/memory/import", {"entries": entries}, token=root_token
        )
        report.check("回导", "重复导入不产生副本",
                     (status, body.get("imported"), body.get("skipped")), (200, 0, len(entries)))
        status, listed_mem = request("GET", inst.base + "/v1/memory/entries", token=root_token)
        report.check("回导", "导入后记忆仍只有一条",
                     len(listed_mem.get("entries") or []), 1)

        # 6) 成员视角：导出只含自己
        status, member_blob, _ = download_zip(
            inst.base + "/v1/accounts/export/all", member_token, {"include_files": False}
        )
        with zipfile.ZipFile(__import__("io").BytesIO(member_blob)) as archive:
            member_names = archive.namelist()
            member_scan = "\n".join(
                archive.read(name).decode("utf-8", errors="replace")
                for name in member_names
                if name.endswith(("/", ".md", ".json", ".csv"))
            )
        report.check("成员视角", "成员导出不含根会话/记忆/员工",
                     ("root-alpha" in member_scan, "客户 A 续约" in member_scan,
                      "研究助手" in member_scan),
                     (False, False, False), f"条目={len(member_names)}")

        failed = report.failed()
        print("\n=== 人可读导出验收 ===")
        print(f"导出耗时：{export_ms} ms，ZIP {len(blob) / 1024:.1f} KB，{len(names)} 个条目")
        print(f"检查项：{len(report.rows)}，失败：{len(failed)}")
        out = os.path.join(REPO, ".acceptance", "readable_export", "report.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "zip_bytes": len(blob),
                    "entries": names,
                    "index": index_text,
                    "rows": report.rows,
                },
                handle, ensure_ascii=False, indent=2, default=str,
            )
        print(f"报告：{out}")
        return 0 if not failed else 1
    finally:
        inst.stop(cleanup=not args.keep)
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
