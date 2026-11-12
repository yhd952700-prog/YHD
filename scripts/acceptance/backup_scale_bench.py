"""备份大包场景基准（真实网关进程 + 真实临时数据目录）。

规模：3 个账号 / 1000 名员工 / 10000 条记忆 / 200 份知识库文档 / 3 个模型 Key。
流程：

1. 先起网关注册账号树、建 Key、造会话（这些必须走产品接口）；
2. 停网关，用真实存储格式批量落员工 / 记忆 / 知识库（避免 10k 次 HTTP）；
3. 重新起网关，``scope=all`` 导出到新数据目录导入，实测：
   导出 / 导入墙钟耗时、密文包体积、服务端进程树 RSS 峰值；
4. 导入后逐项核对（账号树、员工、记忆、知识库、会话、模型 Key 可用）；
5. 若某一步超过 5 分钟，打印瓶颈定位与改进方向（按当前实现路径）；
6. 结束即删除临时目录，不碰 ``dist/``、不跑安装器。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/backup_scale_bench.py
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

ACCEPTANCE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ACCEPTANCE_DIR))
import backup_migration_e2e as e2e  # noqa: E402

REPO = e2e.REPO

PASSPHRASE = "scale-passphrase-2026"
ROOT_PASSWORD = "rootpass123"
MEMBER_PASSWORDS = {"member1": "memberpass123", "member2": "member2pass123"}
PRINCIPALS = ("root1", "member1", "member2")

N_EMPLOYEES = 1000
N_MEMORIES = 10_000
N_DOCUMENTS = 200
N_SESSIONS = {"root1": 10, "member1": 3, "member2": 3}
ACCEPTABLE_SECONDS = 300.0

OWNER_SPLIT = {
    "root1": (400, 4000, 80),     # (员工, 记忆, 文档)
    "member1": (300, 3000, 60),
    "member2": (300, 3000, 60),
}
EMBED_DIMENSION = 1536


# --------------------------------------------------------------------------- #
# 计量工具
# --------------------------------------------------------------------------- #

class RssSampler(threading.Thread):
    """每 100ms 采一次网关进程树 RSS，记录峰值。"""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.peak = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            process = psutil.Process(self.pid)
            self.peak = process.memory_info().rss
        except psutil.Error:
            return
        while not self._stop_event.is_set():
            try:
                total = process.memory_info().rss
                for child in process.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except psutil.Error:
                        continue
                self.peak = max(self.peak, total)
            except psutil.Error:
                break
            self._stop_event.wait(0.1)

    def stop(self) -> int:
        self._stop_event.set()
        self.join(timeout=3)
        return self.peak


def mb(value: float) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


def login(inst: e2e.Instance, principal: str, secret: str) -> str:
    status, body = e2e.request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": principal, "secret": secret},
    )
    if status != 200 or not body.get("access_token"):
        raise RuntimeError(f"login {principal} failed: {status} {body}")
    return str(body["access_token"])


# --------------------------------------------------------------------------- #
# 阶段 1：走产品接口造账号 / Key / 会话
# --------------------------------------------------------------------------- #

def create_accounts(inst: e2e.Instance) -> Dict[str, str]:
    status, body = e2e.request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": ROOT_PASSWORD, "display_name": "规模测试主账号"},
    )
    if status != 200:
        raise RuntimeError(f"register root failed: {status} {body}")
    tokens = {"root1": login(inst, "root1", ROOT_PASSWORD)}

    for principal, secret in MEMBER_PASSWORDS.items():
        status, created = e2e.request(
            "POST", inst.base + "/v1/accounts",
            {"principal": principal, "role": "manager"},
            token=tokens["root1"],
        )
        if status != 200:
            raise RuntimeError(f"create {principal} failed: {status} {created}")
        status, activated = e2e.request(
            "POST", inst.base + "/v1/accounts/activate",
            {
                "principal": principal,
                "code": created.get("activation_code") or "",
                "password": secret,
            },
        )
        if status != 200:
            raise RuntimeError(f"activate {principal} failed: {status} {activated}")
        tokens[principal] = login(inst, principal, secret)
    return tokens


def create_models(inst: e2e.Instance, tokens: Dict[str, str]) -> Dict[str, str]:
    model_ids: Dict[str, str] = {}
    for index, principal in enumerate(PRINCIPALS, start=1):
        status, body = e2e.request(
            "POST", inst.base + "/v1/models",
            {
                "name": f"scale-{principal}-key",
                "provider": "mock",
                "model": "mock-1",
                "api_key": f"sk-scale-{index:02d}-{principal}-0002",
            },
            token=tokens[principal],
        )
        if status != 200:
            raise RuntimeError(f"create model for {principal} failed: {status} {body}")
        model_ids[principal] = str(body.get("id") or "")
    return model_ids


def seed_sessions(
    inst: e2e.Instance, tokens: Dict[str, str], model_ids: Dict[str, str]
) -> int:
    turns = 0
    for principal, count in N_SESSIONS.items():
        for index in range(count):
            status, body = e2e.request(
                "POST", inst.base + "/v1/chat",
                {
                    "message": f"规模测试会话 {principal}-{index:02d}：确认迁移后上下文完整。",
                    "session_id": f"scale-{principal}-{index:02d}",
                    "model_id": model_ids[principal],
                    "use_knowledge": False,
                },
                token=tokens[principal],
            )
            if status != 200:
                raise RuntimeError(f"chat {principal}-{index} failed: {status} {body}")
            turns += 2
    e2e._wait_kernel_memory(inst)
    return turns


# --------------------------------------------------------------------------- #
# 阶段 2：按真实存储格式批量落员工 / 记忆 / 知识库
# --------------------------------------------------------------------------- #

def apply_data_env(data_dir: Path) -> None:
    env = e2e.clean_env_base()
    env.update(e2e.data_path_env(data_dir))
    for key in list(os.environ):
        if key.startswith("LIUHAO_") or key in {
            "CONVERSATION_DB_PATH", "AUDIT_DB_PATH", "AUDIT_DB_BACKUP_PATH",
            "MEMORY_DB_PATH", "PERSONAL_CONTEXT_DB_PATH", "PORT",
        }:
            os.environ.pop(key, None)
    os.environ.update(env)


def _employee_record(index: int, name: str, owner: str) -> Dict[str, Any]:
    employee_id = hashlib.sha256(f"scale-employee-{index}".encode()).hexdigest()[:32]
    agent_types = ("planner", "executor", "reviewer")
    agents = []
    for slot, agent_type in enumerate(agent_types):
        agents.append({
            "id": f"{employee_id}-a{slot}",
            "agent_type": agent_type,
            "name": f"{name}-{agent_type}",
            "system_prompt": f"你是{agent_type}，负责规模验收中的第 {index:04d} 号员工。",
            "status": "idle",
            "completed_tasks": 0,
            "failed_tasks": 0,
            "total_latency_ms": 0.0,
            "metadata": {},
            "current_task": None,
        })
    created = 1_700_000_000.0 + index * 60
    return {
        "name": name,
        "agent_count": len(agents),
        "agent_types": list(agent_types),
        "system_prompts": {},
        "enable_observability": True,
        "role_description": f"规模验收员工 {index:04d}，归属 {owner} 的常规业务岗位。",
        "default_capabilities": [],
        "owner_account_id": owner,
        "employment_status": "active",
        "employee_id": employee_id,
        "last_activity": {"at": "2026-11-01T00:00:00+00:00", "summary": "完成季度复盘"},
        "agents": agents,
        "tasks": [
            {
                "id": f"{employee_id}-t0",
                "description": f"第 {index:04d} 号员工的季度复盘任务",
                "task_type": "general",
                "depends_on": [],
                "status": "completed",
                "assigned_agent": agents[0]["id"],
                "result": {"summary": "已输出复盘摘要", "artifacts": []},
                "error": None,
                "priority": 0,
                "metadata": {},
                "created_at": created,
                "started_at": created,
                "completed_at": created + 30,
            }
        ],
        "task_queue": [],
        "completed_tasks": [f"{employee_id}-t0"],
        "failed_tasks": [],
        "total_tasks_submitted": 1,
        "total_tasks_completed": 1,
        "total_tasks_failed": 0,
        "total_goals_submitted": 0,
        "total_goals_completed": 0,
        "total_goals_failed": 0,
        "goals": [],
    }


def seed_employees() -> int:
    from src.ai.employee_store import EmployeeStore

    employees: Dict[str, Any] = {}
    index = 0
    for owner, (count, _, _) in OWNER_SPLIT.items():
        for _ in range(count):
            name = f"规模员工-{index:04d}"
            employees[name] = _employee_record(index, name, owner)
            index += 1
    store = EmployeeStore()
    # 保留网关可能已经种下的基础设施员工（liuhao-default），只追加业务员工。
    existing = store._load_store(strict=True)  # noqa: SLF001
    existing.setdefault("employees", {}).update(employees)
    # 批量造数只需一次原子写；产品导入路径仍走 import_employees。
    store._write_store(existing)  # noqa: SLF001
    return len(existing.get("employees") or {})


def seed_memories() -> int:
    from src.gateway import account_memory

    base = datetime.now(timezone.utc)
    rows: List[Tuple[str, str, str, str, str, str]] = []
    index = 0
    for owner, (_, count, _) in OWNER_SPLIT.items():
        for _ in range(count):
            rows.append((
                f"scale-memory-{index:05d}",
                owner,
                f"规模记忆 {index:05d}：{owner} 的偏好与项目上下文，唯一标记 MEM-{index:05d}。",
                "user",
                json.dumps(["scale", f"batch-{index // 500:02d}"], ensure_ascii=False),
                (base + timedelta(seconds=index)).isoformat(),
            ))
            index += 1
    connection = account_memory._raw_connect()  # noqa: SLF001 - 验收脚本直接造真实行
    try:
        connection.executemany(
            "INSERT INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return len(rows)


def _document_text(index: int) -> str:
    header = (
        f"# 规模测试文档 {index:04d}\n\n"
        f"唯一标记 SCALE-DOC-{index:04d}。本文件用于验证大包备份的知识库迁移。\n"
    )
    paragraphs = []
    for section in range(1, 13):
        body = (
            f"## 第 {section} 节\n"
            f"第 {index:04d} 号文档的第 {section} 节描述备份策略、恢复演练与责任边界。"
            f"本节唯一标记 SCALE-DOC-{index:04d}-P{section:02d}。"
            + "数据需要可导出、可校验、可在新机器上原样恢复；"
            + "任何缺少校验的迁移都不算完成。"
            * 3
            + "\n"
        )
        paragraphs.append(body)
    return header + "\n".join(paragraphs)


class _DeterministicEmbedder:
    """确定性 1536 维向量：让每块向量都是真实量级的 JSON，而不是全零。"""

    name = "deterministic-scale"
    dimension = EMBED_DIMENSION

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
            rng = random.Random(seed)
            vectors.append(
                [round(rng.uniform(-1.0, 1.0), 6) for _ in range(EMBED_DIMENSION)]
            )
        return vectors


def seed_knowledge() -> Tuple[int, int]:
    from src.knowledge.documents import KnowledgeDocumentService
    from src.knowledge.vector_store import PersistentVectorStore

    store = PersistentVectorStore(os.environ["LIUHAO_KNOWLEDGE_DB"])
    service = KnowledgeDocumentService(
        vector_store=store, embedder=_DeterministicEmbedder()
    )
    documents = 0
    chunks = 0
    try:
        index = 0
        for owner, (_, _, count) in OWNER_SPLIT.items():
            for _ in range(count):
                result = service.ingest(
                    _document_text(index),
                    doc_id=f"scale-doc-{index:04d}",
                    metadata={"source": "scale-bench", "title": f"规模文档 {index:04d}"},
                    owner_account_id=owner,
                )
                documents += 1
                chunks += int(result.get("chunks") or result.get("count") or 0)
                index += 1
                if index % 50 == 0:
                    print(f"  知识库已入库 {index}/{N_DOCUMENTS} 份文档 …")
    finally:
        store.close()
    return documents, chunks


def seed_bulk(data_dir: Path) -> Dict[str, Any]:
    saved_env = dict(os.environ)
    apply_data_env(data_dir)
    started = time.monotonic()
    try:
        employees = seed_employees()
        memories = seed_memories()
        documents, chunks = seed_knowledge()
    finally:
        # 恢复脚本进程环境，避免后续 Instance 构造读到上一阶段的数据目录。
        os.environ.clear()
        os.environ.update(saved_env)
    return {
        "seconds": time.monotonic() - started,
        "employees": employees,
        "memories": memories,
        "documents": documents,
        "chunks": chunks,
    }


# --------------------------------------------------------------------------- #
# 阶段 3：快照 / 核对
# --------------------------------------------------------------------------- #

def _connect_with_retry(path: Path, attempts: int = 6) -> sqlite3.Connection:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            connection = sqlite3.connect(str(path), timeout=15)
            connection.execute("SELECT 1").fetchone()
            return connection
        except sqlite3.Error as exc:  # 刚停掉的网关 WAL 需要恢复
            last_error = exc
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"cannot open {path}: {last_error}")


def db_facts(data_dir: Path) -> Dict[str, Any]:
    """直接读存储层的真实行数（产品视图之外的第二份证据）。"""
    employees_all = 0
    employees_business = 0
    employees_path = data_dir / "workspace" / "liuhao_employees.json"
    if employees_path.is_file():
        names = (
            (json.loads(employees_path.read_text(encoding="utf-8")) or {}).get("employees")
            or {}
        )
        employees_all = len(names)
        # 网关自带的种子员工属于基础设施，不计入"我的员工"。
        employees_business = employees_all - (1 if "liuhao-default" in names else 0)

    memory: Dict[str, int] = {}
    memory_path = data_dir / "workspace" / "account_memory.sqlite3"
    if memory_path.is_file():
        connection = _connect_with_retry(memory_path)
        try:
            for owner, count in connection.execute(
                "SELECT owner, COUNT(*) FROM account_memory_entries GROUP BY owner"
            ).fetchall():
                memory[str(owner)] = int(count)
        finally:
            connection.close()

    knowledge: Dict[str, int] = {}
    knowledge_chunks = 0
    knowledge_path = data_dir / "data" / "knowledge_vectors.sqlite3"
    if knowledge_path.is_file():
        connection = _connect_with_retry(knowledge_path)
        try:
            rows = connection.execute(
                "SELECT metadata FROM knowledge_vectors"
            ).fetchall()
        finally:
            connection.close()
        distinct: Dict[str, set] = {}
        for (raw,) in rows:
            knowledge_chunks += 1
            try:
                metadata = json.loads(raw or "{}")
            except ValueError:
                metadata = {}
            owner = str(metadata.get("owner_account_id") or "")
            document_id = str(metadata.get("document_id") or "")
            if owner and document_id:
                distinct.setdefault(owner, set()).add(document_id)
        knowledge = {owner: len(ids) for owner, ids in distinct.items()}

    conversations = 0
    conversation_path = data_dir / "conversation_store.db"
    if conversation_path.is_file():
        connection = _connect_with_retry(conversation_path)
        try:
            conversations = int(
                connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
            )
        finally:
            connection.close()

    sizes = {}
    for label, path in (
        ("员工库", employees_path),
        ("记忆库", memory_path),
        ("知识库", knowledge_path),
    ):
        sizes[label] = path.stat().st_size if path.is_file() else 0

    return {
        "employees": employees_business,
        "employees_all": employees_all,
        "memory": memory,
        "knowledge": knowledge,
        "knowledge_chunks": knowledge_chunks,
        "conversations": conversations,
        "sizes": sizes,
    }


def http_view(
    inst: e2e.Instance, tokens: Dict[str, str], *, list_employees: bool = False
) -> Dict[str, Any]:
    """产品视图：账号树 / 记忆 / 知识库 / 模型 Key。"""
    view: Dict[str, Any] = {}

    status, body = e2e.request("GET", inst.base + "/v1/accounts/tree", token=tokens["root1"])
    view["accounts"] = sorted(e2e._flatten_tree(body.get("tree"))) if status == 200 else []

    view["memory"] = {}
    for principal in PRINCIPALS:
        status, body = e2e.request(
            "GET", inst.base + "/v1/memory/entries?scope=self", token=tokens[principal]
        )
        entries = body.get("entries") or []
        view["memory"][principal] = len(entries) if status == 200 else -1
        if principal == "root1" and entries:
            view["memory_sample"] = str(entries[0].get("text") or "")

    view["knowledge"] = {}
    for principal in PRINCIPALS:
        status, body = e2e.request(
            "GET",
            inst.base + "/v1/knowledge/documents?limit=200&offset=0",
            token=tokens[principal],
        )
        documents = body.get("documents") or []
        view["knowledge"][principal] = len(documents) if status == 200 else -1
        if principal == "root1" and documents:
            view["knowledge_sample"] = str(documents[0].get("document_id") or "")

    view["models"] = {}
    for principal in PRINCIPALS:
        status, body = e2e.request("GET", inst.base + "/v1/models", token=tokens[principal])
        view["models"][principal] = sorted(
            f"{item.get('name')}:{item.get('api_key_status')}"
            for item in body.get("models") or []
        ) if status == 200 else []

    if list_employees:
        started = time.monotonic()
        status, body = e2e.request(
            "GET", inst.base + "/v1/employees", token=tokens["root1"], timeout=600
        )
        view["employees_root"] = (
            len(body.get("employees") or []) if status == 200 else -1
        )
        view["employees_root_seconds"] = time.monotonic() - started
    return view


def login_all(inst: e2e.Instance) -> Dict[str, str]:
    tokens = {"root1": login(inst, "root1", ROOT_PASSWORD)}
    for principal, secret in MEMBER_PASSWORDS.items():
        tokens[principal] = login(inst, principal, secret)
    return tokens


# --------------------------------------------------------------------------- #
# 阶段 4：结果
# --------------------------------------------------------------------------- #

class Table:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str, str]] = []

    def add(self, item: str, before: Any, after: Any, consistent: bool) -> None:
        self.rows.append(
            (item, str(before), str(after), "✅ 一致" if consistent else "❌ 不一致")
        )

    def render(self) -> str:
        header = ("项目", "导出前", "导入后", "一致否")
        widths = [
            max(len(row[i]) for row in self.rows + [header]) for i in range(4)
        ]
        line = " | ".join(
            label.ljust(widths[i]) for i, label in enumerate(header)
        )
        sep = "-+-".join("-" * width for width in widths)
        body = "\n".join(
            " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))
            for row in self.rows
        )
        return f"{line}\n{sep}\n{body}"


def _bundle_bytes(exported: Dict[str, Any]) -> Tuple[int, int]:
    bundle = exported.get("bundle") or {}
    response_bytes = len(json.dumps(exported, ensure_ascii=False).encode("utf-8"))
    payload = str(bundle.get("payload") or "")
    cipher_bytes = len(payload) * 3 // 4
    return response_bytes, cipher_bytes


def _bottleneck_hint(step: str, seconds: float, manifest: Dict[str, Any]) -> None:
    if seconds <= ACCEPTABLE_SECONDS:
        return
    print()
    print(f"⚠ {step} 超过 {ACCEPTABLE_SECONDS:.0f}s（{seconds:.1f}s），瓶颈定位：")
    if step == "导入":
        print(
            "  主因在员工恢复：EmployeeStore.import_employees 对每位员工执行"
            "一次 load()（整库读）+ 一次 save()（整库读-改-写 + fsync），"
            "因此是 O(员工数²) 的字节量与 N 次落盘。"
            f"本包 {manifest.get('employees', 0)} 名员工 → 约 2×N 次整库读 + N 次整库写。"
        )
        print(
            "  改进方向：导入走批量合并——一次读库、一次性合并全部员工、"
            "一次原子写 + 一次 fsync（可把 N 次事务压成 1 次）；"
            "记忆/知识库恢复已经是单事务，保持现状即可。"
        )
    elif step == "导出":
        print(
            "  主因在快照 + 序列化 + 加密：知识库向量按 JSON 文本整份读出，"
            "随后 payload 整体 json.dumps + AES-GCM + base64。"
        )
        print(
            "  改进方向：向量编码改二进制（base64 单值而非 JSON 浮点数组）、"
            "流式导出分片、或对知识库单独走增量/分卷。"
        )


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="liuhao-scale-bench-"))
    source = tmp / "source"
    target = tmp / "target"
    a: Optional[e2e.Instance] = None
    b: Optional[e2e.Instance] = None
    table = Table()
    failures: List[str] = []
    checks: Dict[str, bool] = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = bool(ok)
        if not ok:
            failures.append(name + (f" ({detail})" if detail else ""))
            print(f"[FAIL] {name} {detail}")

    try:
        # ---------------- 阶段 1：账号 / Key / 会话 ----------------
        print("=" * 78)
        print("阶段 1/4：走产品接口造账号树、模型 Key、会话")
        print("=" * 78)
        a = e2e.Instance(source)
        a.start()
        tokens = create_accounts(a)
        model_ids = create_models(a, tokens)
        turns = seed_sessions(a, tokens, model_ids)
        a.stop()
        a = None

        # ---------------- 阶段 2：批量造大包数据 ----------------
        print("=" * 78)
        print("阶段 2/4：批量落员工 / 记忆 / 知识库（真实存储格式）")
        print("=" * 78)
        seeded = seed_bulk(source / "data")
        print(
            f"  造数完成：员工 {N_EMPLOYEES}（员工库共 {seeded['employees']}，含种子）/ "
            f"记忆 {seeded['memories']} / 文档 {seeded['documents']}"
            f"（{seeded['chunks']} 块）/ 会话 {turns} 轮；耗时 {seeded['seconds']:.1f}s"
        )

        # ---------------- 阶段 3：导出 ----------------
        print("=" * 78)
        print("阶段 3/4：当前代码打开大包数据 → scope=all 导出")
        print("=" * 78)
        a = e2e.Instance(source)
        a.start()
        tokens = login_all(a)
        # 先在源侧触发一次员工列表：网关会在此路径种下基础设施员工
        # ``liuhao-default``。先让它落库，导出/导入两边的"含种子"口径才可比。
        before = http_view(a, tokens, list_employees=True)

        sampler = RssSampler(a.proc.pid)
        sampler.start()
        started = time.monotonic()
        status, exported = e2e.request(
            "POST", a.base + "/v1/accounts/backup/export",
            {"scope": "all", "passphrase": PASSPHRASE},
            token=tokens["root1"],
            timeout=ACCEPTABLE_SECONDS * 3,
        )
        export_seconds = time.monotonic() - started
        export_peak = sampler.stop()
        check("导出 200", status == 200, str(exported)[:300])

        response_bytes, cipher_bytes = _bundle_bytes(exported)
        manifest = exported.get("manifest") or {}
        print(f"  导出耗时：{export_seconds:.2f}s")
        print(f"  响应包体积：{mb(response_bytes)}；密文 payload：{mb(cipher_bytes)}")
        print(f"  服务端 RSS 峰值：{mb(export_peak)}")
        print(
            "  manifest："
            f"员工 {manifest.get('employees')} / "
            f"记忆 {(manifest.get('stores') or {}).get('account_memory', {}).get('entries')} / "
            f"知识库文档 {(manifest.get('knowledge') or {}).get('documents')} / "
            f"模型 Key {manifest.get('models')} / "
            f"成员 Key 随包 {manifest.get('member_keys_included')}"
        )
        check("导出回执员工数=导出前可见员工数",
              manifest.get("employees") == before.get("employees_root"),
              f"manifest={manifest.get('employees')} 导出前={before.get('employees_root')}")
        check("导出回执记忆=10000",
              ((manifest.get("stores") or {}).get("account_memory") or {}).get("entries")
              == N_MEMORIES,
              str((manifest.get("stores") or {}).get("account_memory")))
        check("导出回执文档=200",
              (manifest.get("knowledge") or {}).get("documents") == N_DOCUMENTS,
              str(manifest.get("knowledge")))
        check("导出回执成员 Key 随包（密文）",
              manifest.get("member_keys_included") is True)
        check("导出回执主密钥未导出",
              manifest.get("key_material_included") is False)
        a.stop()
        a = None
        before_db = db_facts(source / "data")
        print(
            "  存储层事实（导出前）："
            f"业务员工 {before_db['employees']}（含种子 {before_db['employees_all']}）/ "
            f"记忆 {before_db['memory']} / 文档 {before_db['knowledge']} / "
            f"向量块 {before_db['knowledge_chunks']} / 会话轮次 {before_db['conversations']}"
        )
        print(
            "  数据文件："
            + "，".join(f"{label} {mb(size)}" for label, size in before_db["sizes"].items())
        )

        # ---------------- 阶段 4：导入 ----------------
        print("=" * 78)
        print("阶段 4/4：全新数据目录导入并逐项核对")
        print("=" * 78)
        b = e2e.Instance(target)
        b.start()
        sampler = RssSampler(b.proc.pid)
        sampler.start()
        started = time.monotonic()
        status, imported = e2e.request(
            "POST", b.base + "/v1/accounts/backup/import",
            {"bundle": exported.get("bundle"), "passphrase": PASSPHRASE},
            timeout=ACCEPTABLE_SECONDS * 3,
        )
        import_seconds = time.monotonic() - started
        import_peak = sampler.stop()
        check("导入 200", status == 200, str(imported)[:400])
        print(f"  导入耗时：{import_seconds:.2f}s")
        print(f"  服务端 RSS 峰值：{mb(import_peak)}")
        if status != 200:
            print("  网关日志尾部：")
            print(b.tail()[-4000:])
        if status == 200:
            print(f"  导入回执：{(imported.get('manifest') or {}).get('stores')}")

        tokens_b = login_all(b)
        after = http_view(b, tokens_b, list_employees=True)
        b.stop()
        b = None
        after_db = db_facts(target / "data")
        print(
            "  存储层事实（导入后）："
            f"业务员工 {after_db['employees']}（含种子 {after_db['employees_all']}）/ "
            f"记忆 {after_db['memory']} / 文档 {after_db['knowledge']} / "
            f"向量块 {after_db['knowledge_chunks']} / 会话轮次 {after_db['conversations']}"
        )

        # ---------------- 核对表 ----------------
        table.add("账号树", before["accounts"], after["accounts"],
                  before["accounts"] == after["accounts"])
        table.add("员工（业务，存储层）", before_db["employees"], after_db["employees"],
                  before_db["employees"] == after_db["employees"])
        table.add("员工（含种子，存储层）", before_db["employees_all"],
                  after_db["employees_all"],
                  before_db["employees_all"] == after_db["employees_all"])
        table.add("员工（根账号 HTTP 列表）", before.get("employees_root"),
                  after.get("employees_root"),
                  before.get("employees_root") == after.get("employees_root"))
        for principal in PRINCIPALS:
            table.add(
                f"记忆 {principal}",
                before_db["memory"].get(principal, 0),
                after_db["memory"].get(principal, 0),
                before_db["memory"].get(principal, 0)
                == after_db["memory"].get(principal, 0),
            )
        for principal in PRINCIPALS:
            table.add(
                f"知识库文档 {principal}",
                before_db["knowledge"].get(principal, 0),
                after_db["knowledge"].get(principal, 0),
                before_db["knowledge"].get(principal, 0)
                == after_db["knowledge"].get(principal, 0),
            )
        table.add("知识库向量块", before_db["knowledge_chunks"], after_db["knowledge_chunks"],
                  before_db["knowledge_chunks"] == after_db["knowledge_chunks"])
        table.add("会话轮次", before_db["conversations"], after_db["conversations"],
                  before_db["conversations"] == after_db["conversations"])
        for principal in PRINCIPALS:
            table.add(
                f"模型 Key {principal}",
                before["models"].get(principal),
                after["models"].get(principal),
                before["models"].get(principal) == after["models"].get(principal),
            )
        table.add("记忆样本可读", "有", after.get("memory_sample"), bool(after.get("memory_sample")))
        table.add("知识库样本可读", "有", after.get("knowledge_sample"),
                  bool(after.get("knowledge_sample")))
        print(table.render())

        check("账号树一致", before["accounts"] == after["accounts"])
        check("业务员工不丢", before_db["employees"] == after_db["employees"] == N_EMPLOYEES,
              f"{before_db['employees']} → {after_db['employees']}")
        check("员工（含种子）不丢",
              before_db["employees_all"] == after_db["employees_all"],
              f"{before_db['employees_all']} → {after_db['employees_all']}")
        check("根账号 HTTP 可见员工数一致",
              after.get("employees_root") == after_db["employees_all"],
              f"HTTP={after.get('employees_root')} 存储={after_db['employees_all']}；"
              f"耗时 {after.get('employees_root_seconds', 0):.1f}s")
        for principal in PRINCIPALS:
            check(
                f"记忆 {principal} 不丢",
                before_db["memory"].get(principal, 0) == after_db["memory"].get(principal, 0),
                f"{before_db['memory'].get(principal, 0)} → "
                f"{after_db['memory'].get(principal, 0)}",
            )
            check(
                f"知识库 {principal} 不丢",
                before_db["knowledge"].get(principal, 0)
                == after_db["knowledge"].get(principal, 0),
                f"{before_db['knowledge'].get(principal, 0)} → "
                f"{after_db['knowledge'].get(principal, 0)}",
            )
        check("向量块不丢", before_db["knowledge_chunks"] == after_db["knowledge_chunks"],
              f"{before_db['knowledge_chunks']} → {after_db['knowledge_chunks']}")
        check("会话轮次不丢", before_db["conversations"] == after_db["conversations"],
              f"{before_db['conversations']} → {after_db['conversations']}")
        for principal in PRINCIPALS:
            check(
                f"模型 Key {principal} 可用",
                all(item.endswith(":ok") for item in after["models"].get(principal) or []),
                str(after["models"].get(principal)),
            )
        check("成员原密码可登录", all(bool(token) for token in tokens_b.values()))
        check("记忆样本可读", bool(after.get("memory_sample")), str(after.get("memory_sample")))
        check("知识库样本可读", bool(after.get("knowledge_sample")))

        # ---------------- 阈值 ----------------
        print()
        print("=" * 78)
        print("阈值与结论")
        print("=" * 78)
        verdicts = [
            ("导出", export_seconds, response_bytes, export_peak),
            ("导入", import_seconds, response_bytes, import_peak),
        ]
        for step, seconds, size, peak in verdicts:
            passed = seconds <= ACCEPTABLE_SECONDS
            print(
                f"  {step}：{seconds:.2f}s（阈值 {ACCEPTABLE_SECONDS:.0f}s，"
                f"{'PASS' if passed else 'FAIL'}）；包 {mb(size)}；RSS 峰值 {mb(peak)}"
            )
            _bottleneck_hint(step, seconds, manifest)

        print()
        if failures:
            print(f"结果：{len(failures)} 项失败")
            for item in failures:
                print(f"  - {item}")
            return 1
        print(f"全部通过（{len(checks)} 项检查）")
        print(
            f"规模摘要：{len(PRINCIPALS)} 账号 / {N_EMPLOYEES} 员工 / {N_MEMORIES} 记忆 / "
            f"{N_DOCUMENTS} 文档（{after_db['knowledge_chunks']} 块）/ "
            f"{turns} 会话轮次"
        )
        return 0
    finally:
        for instance in (a, b):
            if instance is not None:
                try:
                    instance.stop()
                except Exception:  # noqa: BLE001
                    pass
        if os.environ.get("LIUHAO_KEEP_SCALE_DIR") == "1":
            print(f"按 LIUHAO_KEEP_SCALE_DIR=1 保留临时目录：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"已清理临时目录：{tmp}")


if __name__ == "__main__":
    sys.exit(main())
