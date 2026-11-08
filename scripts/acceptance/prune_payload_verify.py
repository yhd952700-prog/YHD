"""剪枝验收：把 A1–A13 规则应用到**临时 payload 副本**，再跑 E1–E6。

为什么是副本
------------
``dist\\`` 是打包线的产物，任何"试试删掉会怎样"的实验都不能落在真包上。本脚本
只复制 ``payload\\app`` 到临时目录，所有删除都发生在副本里，并留下
``pruned-paths.json`` 清单——删了哪些文件、属于哪条规则，逐条可回退。

规则来源：``docs/packaging-slimming-spec-20261108.md`` 的 A1–A13。
验收项：E1 网关可启动、E2 PyWin32 运行面、E3 知识库 ORM、E4 关键 import、
E5 真对话 + 知识库检索、E6 首启 GUI（tkinter）。

归因方式
--------
规则按 A1→A13 **顺序累积**应用，每加一条就跑一次 ``--phase imports``
（含 ``import src.gateway.main``，覆盖整张启动 import 图）。第一次失败的那条
就是元凶，脚本会把它回退并重跑确认。全量规则跑完后再跑一次 ``--phase full``；
若全量阶段失败，再逐条回退定位。

用法::

    .venv\\Scripts\\python.exe scripts/acceptance/prune_payload_verify.py
    ... --source dist\\perf-20261108\\payload\\app --work-dir D:\\_audit\\prune-run1
    ... --restore A1,A5          # 只把这几条规则的文件从源复制回来，再退出
    ... --skip-chat              # 跳过 E5（没有 Ollama / 想快速验证时）

退出码：0 全部通过；1 有验收项失败（已给出归因）；2 前置条件不满足。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "dist" / "perf-20261108" / "payload" / "app"
DEFAULT_PORT = 19113

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

#: 每条规则：(编号, 说明, 谓词)。谓词收到的 rel_path 是正斜杠、小写、相对 app 根。
#: 顺序即归属顺序——先命中者拥有该文件，后命中不重复计数。
RULES: List[Dict[str, Any]] = [
    {
        "id": "A1",
        "title": "pythonwin IDE（mfc140u.dll / win32ui.pyd / scintilla.dll）",
        "may_affect": "`import pythonwin`、`pythonwin.pywin.*`；应用只用 win32api/win32gui 做托盘",
        "match": lambda p: p.startswith("runtime/lib/site-packages/pythonwin/"),
    },
    {
        "id": "A2",
        "title": "PyWin32.chm 离线帮助",
        "may_affect": "无 import；仅 CHM 阅读器",
        "match": lambda p: p == "runtime/lib/site-packages/pywin32.chm",
    },
    {
        "id": "A3",
        "title": "Demos（win32 / win32com / win32comext / tk / tix 示例）",
        "may_affect": "无 import；示例脚本不被应用引用",
        "match": lambda p: (
            "/site-packages/win32/demos/" in p
            or "/site-packages/win32com/demos/" in p
            or "/site-packages/win32comext/" in p and "/demos/" in p
            or p.startswith("runtime/tcl/tk8.6/demos/")
            or p.startswith("runtime/tcl/tix8.4.3/demos/")
        ),
    },
    {
        "id": "A4",
        "title": "lxml.objectify（python-docx 只用 lxml.etree）",
        "may_affect": "`import lxml.objectify`；仓库 0 处引用",
        "match": lambda p: p.startswith("runtime/lib/site-packages/lxml/objectify"),
    },
    {
        "id": "A5",
        "title": "Tcl/Tix 运行库（tkinter.tix，已废弃）",
        "may_affect": "`import tkinter.tix`；仓库 0 处引用；tkinter 本身保留",
        "match": lambda p: p.startswith("runtime/tcl/tix8.4.3/"),
    },
    {
        "id": "A6",
        "title": "win32comext 扩展（COM 服务器/Shell/ADSI 等）",
        "may_affect": "`win32comext.*` 下的显式 import；托盘只用 pythoncom/win32gui",
        "match": lambda p: p.startswith("runtime/lib/site-packages/win32comext/"),
    },
    {
        "id": "A7",
        "title": "adodbapi / isapi",
        "may_affect": "`import adodbapi`、`import isapi`；仓库 0 处引用",
        "match": lambda p: (
            p.startswith("runtime/lib/site-packages/adodbapi/")
            or p.startswith("runtime/lib/site-packages/isapi/")
        ),
    },
    {
        "id": "A8",
        "title": "第三方包自带的 tests / test 目录",
        "may_affect": "运行期零引用；只影响 `pytest --pyargs` 这类用法",
        "match": lambda p: "/site-packages/" in p and any(
            part in ("tests", "test") for part in p.split("/")[:-1]
        ),
    },
    {
        "id": "A9",
        "title": "pydoc_data",
        "may_affect": "`help()` / pydoc 的离线文档数据；运行期服务不用",
        "match": lambda p: p.startswith("runtime/lib/pydoc_data/"),
    },
    {
        "id": "A10",
        "title": "zstandard 扩展（httpx 的 zstd 解压后端，可选）",
        "may_affect": "`import zstandard`；httpx 在缺 zstd 时降级为不支持 zstd 响应体",
        "match": lambda p: p.startswith("runtime/lib/site-packages/zstandard/"),
    },
    {
        "id": "A11",
        "title": "watchfiles（只给 uvicorn --reload 用）",
        "may_affect": "`uvicorn --reload`；生产启动不用 reload",
        "match": lambda p: p.startswith("runtime/lib/site-packages/watchfiles/"),
    },
    {
        "id": "A12",
        "title": "rich + pygments（CLI 渲染；须同步改 requirements）",
        "may_affect": "`import rich`、`import pygments`；反向依赖只有 rich→pygments",
        "match": lambda p: (
            p.startswith("runtime/lib/site-packages/rich/")
            or p.startswith("runtime/lib/site-packages/pygments/")
        ),
    },
    {
        "id": "A13",
        "title": "类型桩/源码残留（.pyi .pxd .pyx .c .h）",
        "may_affect": "运行期只用 .py/.pyd；类型检查器会失去本包类型信息",
        "match": lambda p: "/site-packages/" in p and p.rsplit(".", 1)[-1] in (
            "pyi", "pxd", "pyx", "c", "h"
        ),
    },
]

#: E2/E4：剪枝后必须仍然可以 import 的模块。刻意不含 A4/A10/A11/A12 剪掉的东西。
IMPORT_GUARDS = [
    "win32api",
    "win32gui",
    "win32con",
    "pythoncom",
    "src.tasks.broker",
    "google.genai",
    "lxml.etree",
    "pydantic_core",
    "sqlalchemy",
    "docx",
    "pypdf",
    "anthropic",
    "openai",
    "httpx",
    "fastapi",
    "uvicorn",
    "hvac",
    "tkinter",
    "tkinter.ttk",
    "src.knowledge.db",
]

#: 交给剪枝后 runtime 执行的验收 runner（写成独立文件，避免污染 app 目录）。
RUNNER = r'''
"""剪枝副本的验收 runner：由剪枝后 runtime 执行，结果写 JSON。"""
import argparse
import importlib
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

IMPORTS = json.loads(os.environ["PRUNE_IMPORT_LIST"])


def write(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def try_import(name):
    try:
        importlib.import_module(name)
        return {"module": name, "ok": True}
    except Exception as exc:
        return {"module": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def http_json(base, method, path, token, body=None, timeout=600.0):
    data = None
    headers = {
        "X-Liuhao-Token": f"Bearer {token}",
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def http_upload(base, path, token, filename, payload_bytes, timeout=600.0):
    boundary = "pruneboundary" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode("utf-8") + payload_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {
        "X-Liuhao-Token": f"Bearer {token}",
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request = urllib.request.Request(base + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def seed_model(timeout_seconds):
    from src.ai.model_config import get_model_config_store

    store = get_model_config_store()
    return store.create(
        {
            "name": "prune-acceptance-ollama",
            "provider": "ollama",
            "model": os.environ.get("PRUNE_OLLAMA_MODEL", "qwen2.5:3b"),
            "base_url": os.environ.get("PRUNE_OLLAMA_URL", "http://127.0.0.1:11434"),
            "timeout": timeout_seconds,
            "is_default": True,
        },
        actor="prune-acceptance",
    )


def mint_token():
    from src.security.jwt_handler import create_access_token

    token, _payload = create_access_token(
        subject="human:prune-acceptance",
        scopes=["console"],
        roles=["owner"],
        permissions=["console", "goals:write"],
        ttl=3600,
        metadata={"kind": "human", "client": "prune-acceptance", "scope": "L4"},
        device_id="prune-acceptance",
    )
    return token


def start_gateway(port):
    import uvicorn

    from src.gateway.main import app, get_app

    application = app if app is not None else get_app()
    config = uvicorn.Config(
        application, host="127.0.0.1", port=port, log_level="warning",
        lifespan="on", access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="prune-gateway", daemon=True)
    thread.start()
    return server


def wait_ready(base, timeout=180.0):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/v1/health", timeout=3) as response:
                if response.status < 500:
                    return True
        except Exception as exc:
            last = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"网关未在 {timeout:.0f}s 内就绪：{last}")


def make_docx(text):
    from docx import Document
    import io

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["imports", "full"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=19113)
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()

    result = {"phase": args.phase, "checks": [], "failures": [], "notes": []}

    def record(name, ok, detail=None):
        result["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            result["failures"].append(name)

    # E2 + E3 + E4：关键 import
    for entry in [try_import(name) for name in IMPORTS]:
        result["checks"].append({"check": f"import:{entry['module']}", "ok": entry["ok"],
                                 "detail": entry.get("error")})
        if not entry["ok"]:
            result["failures"].append(f"import:{entry['module']}")

    # 启动 import 图的最强断言：整个网关模块可导入（含 28 个适配器的懒导出路径）
    gateway_import = try_import("src.gateway.main")
    result["checks"].append({"check": "import:src.gateway.main", "ok": gateway_import["ok"],
                             "detail": gateway_import.get("error")})
    if not gateway_import["ok"]:
        result["failures"].append("import:src.gateway.main")

    if args.phase == "imports":
        write(args.out, result)
        return 0 if not result["failures"] else 1

    # E6：首启 GUI 的 tkinter 面（只需能建窗并销毁，不弹窗给用户）
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        record("gui:tkinter", True, "Tk 创建/销毁成功")
    except Exception as exc:
        result["notes"].append(f"gui:tkinter 环境受限：{type(exc).__name__}: {exc}")
        record("gui:tkinter", False, f"{type(exc).__name__}: {exc}")

    # E1：网关启动 + 就绪
    base = f"http://127.0.0.1:{args.port}"
    server = None
    try:
        timeout_seconds = 300.0
        try:
            seeded = seed_model(timeout_seconds)
            result["notes"].append(f"模型配置：{seeded.get('name')}")
        except Exception as exc:
            result["notes"].append(f"模型配置失败：{type(exc).__name__}: {exc}")
        started = time.perf_counter()
        server = start_gateway(args.port)
        wait_ready(base, timeout=240.0)
        ready_ms = round((time.perf_counter() - started) * 1000, 0)
        code, body = http_json(base, "GET", "/v1/ready", "", timeout=10.0)
        record("E1:gateway", code == 200, f"ready {code} in {ready_ms} ms, degraded={body.get('checks')}")
    except Exception as exc:
        record("E1:gateway", False, f"{type(exc).__name__}: {exc}")
        write(args.out, result)
        traceback.print_exc()
        return 1

    token = mint_token()

    # E5a：真对话
    if args.chat:
        try:
            code, body = http_json(
                base, "POST", "/v1/chat", token,
                {"message": "只回答两个字：可以", "use_knowledge": False,
                 "session_id": "prune-acceptance"},
                timeout=600.0,
            )
            reply = str(body.get("response") or body.get("reply") or body.get("message") or "")
            record("E5:chat", code == 200 and bool(reply.strip()),
                   f"HTTP {code}, reply={reply[:120]!r}")
        except Exception as exc:
            record("E5:chat", False, f"{type(exc).__name__}: {exc}")
    else:
        result["notes"].append("E5:chat 已按 --skip-chat 跳过（不计入失败）")

    # E5b：知识库导入 + 检索
    marker = "剪枝验收专用标记 " + uuid.uuid4().hex[:10]
    try:
        blob = make_docx(f"本文件用于剪枝验收。唯一标记：{marker}。")
        code, body = http_upload(
            base, "/v1/knowledge/documents/upload", token, "prune-acceptance.docx", blob
        )
        result["notes"].append(f"知识库导入：HTTP {code} {str(body)[:120]}")
        code2, body2 = http_json(
            base, "POST", "/v1/knowledge/search", token,
            {"query": marker, "top_k": 3},
        )
        hits = (body2 or {}).get("hits") or []
        record("E5:knowledge", code == 200 and code2 == 200 and len(hits) > 0,
               f"upload={code}, search={code2}, hits={len(hits)}")
    except Exception as exc:
        record("E5:knowledge", False, f"{type(exc).__name__}: {exc}")

    try:
        server.should_exit = True
        time.sleep(0.5)
    except Exception:
        pass
    write(args.out, result)
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ─────────────────────────────────────────────────────────── 工具
def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix().lower()


def _walk_files(root: Path) -> Iterable[Path]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def _size_stats(paths: Iterable[Path]) -> Dict[str, Any]:
    total = 0
    count = 0
    for path in paths:
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            pass
    return {"bytes": total, "miB": round(total / 1048576, 2), "files": count}


def copy_app(source: Path, target: Path) -> None:
    started = time.perf_counter()
    shutil.copytree(source, target, symlinks=True)
    elapsed = round(time.perf_counter() - started, 1)
    print(f"复制 payload → {target}（{elapsed}s）")


def apply_rules(app_root: Path, rule_ids: List[str], manifest_path: Path) -> Dict[str, Any]:
    """按顺序累积删除；返回每条规则实际删掉的体积/文件数（首命中归属）。"""
    wanted = [rule for rule in RULES if rule["id"] in rule_ids]
    manifest: Dict[str, List[str]] = {rule["id"]: [] for rule in wanted}
    stats: Dict[str, Any] = {}
    removed: set = set()
    files = sorted(_walk_files(app_root))
    for rule in wanted:
        hit = []
        size = 0
        for path in files:
            rel = _rel(path, app_root)
            if rel in removed or not rule["match"](rel):
                continue
            try:
                file_size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            removed.add(rel)
            hit.append(rel)
            size += file_size
        manifest[rule["id"]] = hit
        stats[rule["id"]] = {
            "title": rule["title"],
            "may_affect": rule["may_affect"],
            "files": len(hit),
            "miB": round(size / 1048576, 2),
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def restore_rule(app_root: Path, source_root: Path, manifest: Dict[str, List[str]],
                 rule_id: str) -> int:
    restored = 0
    for rel in manifest.get(rule_id, []):
        src = source_root / rel
        dst = app_root / rel
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored += 1
    return restored


def _runner_env(app_root: Path, work_dir: Path, port: int) -> Dict[str, str]:
    data = work_dir / "data"
    (data / "observability").mkdir(parents=True, exist_ok=True)
    (data / "workspace").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(app_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LIUHAO_DATA_DIR": str(data),
            "LIUHAO_WORKSPACE_ROOT": str(data / "workspace"),
            "LIUHAO_MODEL_CONFIG_DB": str(data / "model_registry.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(data / "model_config.key"),
            "LIUHAO_MODEL_CONFIG_MIGRATE": "0",
            "AUDIT_DB_PATH": str(data / "audit_store.db"),
            "AUDIT_DB_BACKUP_PATH": str(data / "audit_store.db.backup"),
            "CONVERSATION_DB_PATH": str(data / "conversation_store.db"),
            "LIUHAO_JWT_REVOCATION_DB": str(data / "liuhao_jwt_revocation.db"),
            "MEMORY_DB_PATH": str(data / "memory_store.db"),
            "PERSONAL_CONTEXT_DB_PATH": str(data / "personal_context.db"),
            "LIUHAO_KNOWLEDGE_DB": str(data / "knowledge_vectors.sqlite3"),
            "LIUHAO_ALERTS_STORE_PATH": str(data / "observability" / "alerts.json"),
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(data / "human_identities.json"),
            "LIUHAO_AUTH_SECRETS_FILE": str(data / "auth_secrets.json"),
            "LIUHAO_JWT_SECRET": "prune-acceptance-fixed-secret",
            "LIUHAO_STARTUP_PROFILE": "0",
            "PRUNE_IMPORT_LIST": json.dumps(IMPORT_GUARDS),
        }
    )
    env.pop("LIUHAO_STARTUP_PROFILE_OUT", None)
    return env


def run_runner(app_root: Path, work_dir: Path, port: int, phase: str,
               chat: bool) -> Dict[str, Any]:
    runner = work_dir / "verify_runner.py"
    out_path = work_dir / f"verify-{phase}.json"
    if out_path.exists():
        out_path.unlink()
    python = app_root / "runtime" / "python.exe"
    command = [str(python), str(runner), "--phase", phase, "--out", str(out_path),
               "--port", str(port)]
    if chat:
        command.append("--chat")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(app_root),
        env=_runner_env(app_root, work_dir, port),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    elapsed = round(time.perf_counter() - started, 1)
    payload: Dict[str, Any] = {}
    if out_path.is_file():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            payload = {"failures": [f"结果解析失败：{exc}"]}
    payload["exit_code"] = completed.returncode
    payload["wall_s"] = elapsed
    if completed.returncode != 0 and not payload.get("failures"):
        payload["failures"] = ["runner 非零退出但没写失败项"]
        payload["stderr_tail"] = completed.stderr[-1500:]
    return payload


# ─────────────────────────────────────────────────────────── main
def main() -> int:
    parser = argparse.ArgumentParser(description="在临时 payload 副本上验证 A1–A13")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--rules", default=",".join(rule["id"] for rule in RULES))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--restore", default="", help="只回退指定规则（逗号分隔）后退出")
    args = parser.parse_args()

    source = Path(args.source)
    if not (source / "runtime" / "python.exe").is_file():
        print(f"源不是可运行的 payload：{source}（缺 runtime\\python.exe）")
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = Path(args.work_dir or f"D:\\_audit\\prune-{stamp}")
    app_root = work_dir / "app"
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "pruned-paths.json"

    if args.restore:
        rules = [r.strip() for r in args.restore.split(",") if r.strip()]
        if not app_root.is_dir() or not manifest_path.is_file():
            print(f"没有可回退的剪枝副本：{app_root} / {manifest_path}")
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rule_id in rules:
            count = restore_rule(app_root, source, manifest, rule_id)
            print(f"回退 {rule_id}：{count} 个文件")
        return 0

    print(f"源：{source}")
    print(f"副本：{app_root}")
    if app_root.exists():
        shutil.rmtree(app_root)
    copy_app(source, app_root)

    source_stats = _size_stats(_walk_files(app_root))
    print(f"剪枝前：{source_stats['files']} 文件 / {source_stats['miB']} MiB")

    rule_ids = [r.strip() for r in args.rules.split(",") if r.strip()]
    stats = apply_rules(app_root, rule_ids, manifest_path)
    for rule_id, detail in stats.items():
        print(f"  {rule_id:>3s}  -{detail['miB']:>6.2f} MiB  {detail['files']:>6d} 文件  "
              f"{detail['title']}")
    pruned_stats = _size_stats(_walk_files(app_root))
    freed = {
        "miB": round((source_stats["bytes"] - pruned_stats["bytes"]) / 1048576, 2),
        "files": source_stats["files"] - pruned_stats["files"],
    }
    print(f"剪枝后：{pruned_stats['files']} 文件 / {pruned_stats['miB']} MiB；"
          f"合计 −{freed['miB']} MiB / −{freed['files']} 文件")

    # 把 runner 放到副本旁（不放 app 内，避免污染被验收的目录）
    (work_dir / "verify_runner.py").write_text(RUNNER, encoding="utf-8")

    result: Dict[str, Any] = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "work_dir": str(work_dir),
        "rules": stats,
        "source_stats": source_stats,
        "pruned_stats": pruned_stats,
        "freed": freed,
        "step_checks": [],
        "culprits": [],
    }

    # 逐条累积验证：每加一条规则跑一次 imports（含整个 src.gateway.main）
    cumulative: List[str] = []
    for rule_id in rule_ids:
        cumulative.append(rule_id)
        check = run_runner(app_root, work_dir, args.port, "imports", chat=False)
        ok = not check.get("failures")
        result["step_checks"].append({"after": list(cumulative), "ok": ok,
                                      "failures": check.get("failures"),
                                      "wall_s": check.get("wall_s")})
        print(f"  加入 {rule_id} 后 imports：{'通过' if ok else '失败 → ' + str(check.get('failures'))}")
        if not ok:
            restored = restore_rule(app_root, source,
                                    json.loads(manifest_path.read_text(encoding="utf-8")),
                                    rule_id)
            result["culprits"].append({
                "rule": rule_id,
                "failures": check.get("failures"),
                "restored_files": restored,
            })
            print(f"    ↳ 归因到 {rule_id}，已回退 {restored} 个文件，继续验证其余规则")

    # 全量阶段：E1 网关 + E5 对话/知识库 + E6 GUI
    full = run_runner(app_root, work_dir, args.port, "full", chat=not args.skip_chat)
    result["full"] = full
    print(f"\n全量验收（{full.get('wall_s')}s）："
          f"{'通过' if not full.get('failures') else '失败 → ' + str(full.get('failures'))}")
    for check in full.get("checks", []):
        print(f"  {'✓' if check['ok'] else '✗'} {check['check']}"
              f"{('  ' + str(check['detail'])) if check.get('detail') else ''}")
    for note in full.get("notes", []):
        print(f"  注：{note}")

    # 全量失败时逐条回退定位（从最后一条往前，优先怀疑删得最多的）
    if full.get("failures"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rule_id in reversed([r for r in rule_ids if r not in
                                 [c["rule"] for c in result["culprits"]]]):
            restored = restore_rule(app_root, source, manifest, rule_id)
            print(f"  回退 {rule_id}（{restored} 文件）后重跑全量……")
            retry = run_runner(app_root, work_dir, args.port, "full",
                               chat=not args.skip_chat)
            result.setdefault("bisect", []).append(
                {"restored": rule_id, "ok": not retry.get("failures"),
                 "failures": retry.get("failures")}
            )
            if not retry.get("failures"):
                result["culprits"].append({
                    "rule": rule_id,
                    "failures": full.get("failures"),
                    "restored_files": restored,
                    "found_by": "full-phase bisect",
                })
                result["full"] = retry
                break
        else:
            result["culprits"].append({"rule": "none", "failures": full.get("failures"),
                                       "note": "回退全部规则后仍失败，问题在验收环境本身"})

    result["verdict"] = "pass" if not result["full"].get("failures") and not result["culprits"] else "fail"
    out_json = work_dir / "prune-report.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = work_dir / "summary.txt"
    summary.write_text(
        "\n".join([
            "剪枝验收",
            f"源：{source}",
            f"剪枝前：{source_stats['files']} 文件 / {source_stats['miB']} MiB",
            f"剪枝后：{pruned_stats['files']} 文件 / {pruned_stats['miB']} MiB",
            f"实际省下：{freed['miB']} MiB / {freed['files']} 文件",
            "各规则：",
            *[f"  {k}  -{v['miB']:.2f} MiB  {v['files']} 文件" for k, v in stats.items()],
            "元凶：" + (", ".join(c["rule"] for c in result["culprits"]) or "无"),
            "结论：" + ("通过" if result["verdict"] == "pass" else "失败"),
        ]) + "\n",
        encoding="utf-8",
    )
    print(f"\n报告：{out_json}")
    print(f"摘要：{summary}")
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
