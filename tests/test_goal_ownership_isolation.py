"""阶段 6：任务归属与跨账号隔离回归。

覆盖（要求逐条对应）：

1. 跨账号不可见：子账号 A 的目标，子账号 B 的 list / get / replan / stop /
   retry 全部 404；且与"不存在的目标"响应**逐字相同**，不暴露存在与否。
2. 主账号（账号树根）能看到整棵树（list + 单条详情），但**不能**替子账号
   重试（只读汇总）。
3. 越权一律 404（不是 403）。
4. 并发提交不丢写、不串号：N 个账号同时 POST /v1/goals，全部落库，owner 正确。
5. 只重试自己的任务：别人的失败任务 404，自己的可以重试且归属不变。
6. 老数据（无 ``owner_account_id``）按本机默认账号加载，不报错。
7. 重启恢复按 owner 生效（scoped reconcile 只动自己的），且重启两次不重复执行。
8. 产物下载按账号分区（``accounts/<owner>/``）：他人产物 404；根账号显式
   ``account=`` 只读浏览；公共区只对根可见。

这些用例全部走真实 FastAPI 应用 + 真实 JWT，执行层用受控 stub（本阶段只验证
归属与持久化，不重复验证内核执行）。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from src.gateway.ai_management import (
    AIStateManager,
    GoalViewer,
    default_account_id,
    owner_of_entry,
)
from src.gateway.main import get_app


# ─── helpers ──────────────────────────────────────────────────

def _headers(subject: str) -> dict:
    """用网关同一套 JWT 签发器铸造真实令牌（sub = 账号名）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    """在身份内核里建一棵真实账号树：root 为主账号，members 为其下级。"""
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


def _viewer(principal: str, subtree=None, *, is_root=False, standalone=False) -> GoalViewer:
    return GoalViewer(
        principal=principal,
        subtree=tuple(subtree or (principal,)),
        is_root=is_root,
        standalone=standalone,
        source="test",
    )


class _StubRuntime:
    """受控执行层：只证明"提交-归属-落库"链路，不做真实任务编排。"""

    def run_goal(self, **kwargs):
        goal_id = kwargs.get("goal_id") or "stub"
        return SimpleNamespace(
            goal_id=goal_id,
            state=SimpleNamespace(value="completed"),
            correlation_id=f"stub-{goal_id}",
            error=None,
            replan_count=0,
            replan_suggested=False,
            memory_keys=[],
            persist_errors=[],
            trace=None,
            context=None,
            evaluation=None,
        )


@pytest.fixture
def client():
    return TestClient(get_app())


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("LIUHAO_DEFAULT_ACCOUNT_ID", raising=False)
    AIStateManager._instance = None
    yield
    AIStateManager._instance = None


@pytest.fixture
def goals_path(tmp_path):
    return tmp_path / "liuhao_goals.json"


def _seed_goal(
    manager: AIStateManager,
    goal_id: str,
    owner: str,
    *,
    state: str = "completed",
    tasks=None,
    artifacts=None,
    natural_language: str = "种子目标",
) -> dict:
    entry = {
        "goal_id": goal_id,
        "state": state,
        "natural_language": natural_language,
        "scope": "L1",
        "correlation_id": f"corr-{goal_id}",
        "error": None,
        "replan_count": 0,
        "replan_suggested": False,
        "trace": [],
        "tasks": list(tasks or []),
        "artifacts": list(artifacts or []),
        "evaluation": None,
        "created_at": 1.0,
        "owner_account_id": owner,
    }
    with manager._goal_lock:
        manager._goals[goal_id] = entry
    manager._save_goals()
    return entry


# ─── 1 & 3. 跨账号不可见 / 越权 404 ───────────────────────────

class TestCrossAccountInvisibility:
    def test_member_cannot_see_or_touch_another_members_goal(
        self, client, goals_path
    ):
        _make_tree()
        mgr = AIStateManager()
        _seed_goal(
            mgr, "G-ALICE", "alice",
            tasks=[{"id": "t1", "name": "写文件", "status": "failed"}],
        )

        bob = _headers("bob")
        ghost = _headers("ghost")  # 从未存在的账号（同样走 404 路径）

        # 别人的列表里连数量都不该出现
        resp = client.get("/v1/goals", headers=bob)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["goals"] == []

        # 单条详情：404，且与"根本不存在"的响应逐字相同
        for subject in ("bob", "ghost"):
            headers = _headers(subject)
            visible = client.get("/v1/goals/G-ALICE", headers=headers)
            missing = client.get("/v1/goals/G-NOBODY", headers=headers)
            assert visible.status_code == 404
            assert missing.status_code == 404
            assert visible.json()["code"] == missing.json()["code"] == "goal_not_found"
            # 文案模板必须完全一致：只有"用户自己请求的那个 id"不同，
            # 这样从响应上无法推断目标是存在还是不存在。
            assert visible.json()["message"].replace("G-ALICE", "<id>") == \
                missing.json()["message"].replace("G-NOBODY", "<id>")

        # 写操作同样 404（不是 403）
        for method, path in (
            ("post", "/v1/goals/G-ALICE/replan"),
            ("post", "/v1/goals/G-ALICE/stop"),
            ("post", "/v1/goals/G-ALICE/tasks/t1/retry"),
            ("get", "/v1/workflows/G-ALICE"),
        ):
            resp = client.request(method.upper(), path, headers=bob)
            assert resp.status_code == 404, path
            assert resp.status_code != 403
            assert resp.json()["code"] == "goal_not_found"

        # 工作流列表同样为空
        resp = client.get("/v1/workflows", headers=bob)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_owner_still_sees_own_goal(self, client):
        _make_tree()
        mgr = AIStateManager()
        _seed_goal(mgr, "G-ALICE", "alice")
        resp = client.get("/v1/goals/G-ALICE", headers=_headers("alice"))
        assert resp.status_code == 200
        assert resp.json()["owner_account_id"] == "alice"


# ─── 2. 主账号可见整树（只读） ──────────────────────────────────

class TestRootSeesWholeTree:
    def test_root_lists_and_reads_all_children_goals(self, client):
        _make_tree()
        mgr = AIStateManager()
        _seed_goal(mgr, "G-A", "alice")
        _seed_goal(mgr, "G-B", "bob")

        root_headers = _headers("boss")
        resp = client.get("/v1/goals", headers=root_headers)
        assert resp.status_code == 200
        goals = resp.json()["goals"]
        assert {g["goal_id"] for g in goals} == {"G-A", "G-B"}
        assert {g["owner_account_id"] for g in goals} == {"alice", "bob"}

        # 单条详情可读
        assert client.get("/v1/goals/G-A", headers=root_headers).status_code == 200
        # 工作流视图也可读
        assert client.get("/v1/workflows/G-B", headers=root_headers).status_code == 200

    def test_root_is_read_only_for_childrens_goals(self, client):
        _make_tree()
        mgr = AIStateManager()
        _seed_goal(
            mgr, "G-A", "alice",
            state="failed",
            tasks=[{"id": "t1", "name": "写文件", "status": "failed"}],
        )
        root_headers = _headers("boss")
        for path in (
            "/v1/goals/G-A/replan",
            "/v1/goals/G-A/stop",
            "/v1/goals/G-A/tasks/t1/retry",
        ):
            resp = client.post(path, headers=root_headers)
            assert resp.status_code == 404, path
            assert resp.json()["code"] == "goal_not_found"


# ─── 4. 并发提交不丢写、不串号 ─────────────────────────────────

class TestConcurrentSubmissions:
    def test_ten_accounts_submit_concurrently_all_persisted_with_owner(
        self, tmp_path, goals_path
    ):
        N = 10
        # 预建单例并注入受控执行层：并发只压持久化/归属，不压内核。
        AIStateManager._instance = None
        mgr = AIStateManager()
        mgr._runtime = _StubRuntime()
        mgr._ensure_employee()

        app = get_app()

        def submit(index: int) -> str:
            headers = _headers(f"acct-{index}")
            with TestClient(app) as local_client:
                resp = local_client.post(
                    "/v1/goals",
                    headers=headers,
                    json={"natural_language": f"并发目标 {index}"},
                )
                assert resp.status_code == 200, resp.text
                return resp.json()["goal_id"]

        with ThreadPoolExecutor(max_workers=N) as pool:
            goal_ids = list(pool.map(submit, range(N)))

        # 1) 内存里 N 个目标都在
        assert len(mgr._goals) == N
        assert len(set(goal_ids)) == N

        # 2) 文件里 N 个目标都在（没有任何一次写入把别人覆盖掉）
        persisted = json.loads(goals_path.read_text(encoding="utf-8"))
        assert set(persisted) == set(goal_ids), (
            f"落库数量 {len(persisted)} != 提交数量 {N}（发生丢写）"
        )

        # 3) 不串号：每个目标的主人就是提交它的账号
        for i, goal_id in enumerate(goal_ids):
            assert persisted[goal_id]["owner_account_id"] == f"acct-{i}", (
                f"{goal_id} 归属串号：{persisted[goal_id]['owner_account_id']}"
            )
            assert persisted[goal_id]["natural_language"] == f"并发目标 {i}"

        # 4) 每个账号的列表只看到自己的 1 条
        for i in range(N):
            headers = _headers(f"acct-{i}")
            with TestClient(app) as local_client:
                resp = local_client.get("/v1/goals", headers=headers)
            body = resp.json()
            assert body["count"] == 1, (i, body)
            assert body["goals"][0]["owner_account_id"] == f"acct-{i}"

    def test_save_merges_disk_entries_instead_of_overwriting_them(
        self, goals_path
    ):
        """跨进程场景的最小证据：磁盘上的别人的目标不能被本进程快照抹掉。"""
        mgr = AIStateManager()
        disk_entry = {
            "goal_id": "G-FROM-OTHER-PROCESS",
            "state": "completed",
            "natural_language": "另一个 worker 的目标",
            "scope": "L1",
            "trace": [],
            "tasks": [],
            "artifacts": [],
            "owner_account_id": "bob",
            "created_at": 1.0,
            "updated_at": 1.0,
        }
        goals_path.write_text(
            json.dumps({"G-FROM-OTHER-PROCESS": disk_entry}),
            encoding="utf-8",
        )
        _seed_goal(mgr, "G-LOCAL", "alice")
        mgr._save_goals()

        persisted = json.loads(goals_path.read_text(encoding="utf-8"))
        assert set(persisted) == {"G-FROM-OTHER-PROCESS", "G-LOCAL"}
        assert persisted["G-FROM-OTHER-PROCESS"]["owner_account_id"] == "bob"
        assert persisted["G-LOCAL"]["owner_account_id"] == "alice"


# ─── 5. 只重试自己的任务 ───────────────────────────────────────

class TestRetryOwnership:
    def test_bob_cannot_retry_alices_task_but_alice_can(self, client):
        _make_tree()
        AIStateManager._instance = None
        mgr = AIStateManager()
        mgr._runtime = _StubRuntime()

        _seed_goal(
            mgr, "G-RETRY", "alice",
            state="failed",
            tasks=[{
                "id": "t1",
                "name": "写报告",
                "status": "failed",
                "capability_id": "file_write",
                "inputs": {"path": "report.txt", "content": "x"},
                "dependencies": [],
                "assigned_agent": None,
            }],
        )

        audited: list = []
        mgr._audit_goal_action = lambda **kw: audited.append(kw)  # type: ignore[assignment]

        # 别人的任务：404（与不存在同一响应），且不会执行
        resp = client.post("/v1/goals/G-RETRY/tasks/t1/retry", headers=_headers("bob"))
        assert resp.status_code == 404
        assert resp.json()["code"] == "goal_not_found"
        assert audited == []

        # 自己的任务：允许，且重试后归属不变
        resp = client.post("/v1/goals/G-RETRY/tasks/t1/retry", headers=_headers("alice"))
        assert resp.status_code == 200
        entry = resp.json()
        assert entry["owner_account_id"] == "alice"
        for task in entry["tasks"]:
            assert task["owner_account_id"] == "alice"
        assert audited and audited[-1]["details"]["owner_account_id"] == "alice"
        assert audited[-1]["principal"] == "alice"


# ─── 6. 老数据兼容 ──────────────────────────────────────────────

class TestLegacyData:
    def test_goal_without_owner_loads_as_default_account(self, goals_path):
        _make_tree()
        default = default_account_id()
        legacy = {
            "goal_id": "G-LEGACY",
            "state": "completed",
            "natural_language": "老目标",
            "scope": "L1",
            "trace": [],
            "tasks": [{"id": "t1", "name": "老任务", "status": "completed"}],
            "artifacts": [],
            "created_at": 1.0,
        }
        goals_path.write_text(json.dumps({"G-LEGACY": legacy}), encoding="utf-8")

        mgr = AIStateManager()  # 构造即加载：不得因缺字段报错
        entry = mgr.get_goal("G-LEGACY")
        assert owner_of_entry(entry) == default
        assert entry["tasks"][0]["owner_account_id"] == default
        assert entry["owner_source"] == "legacy-default"

        # 归属已回写磁盘
        persisted = json.loads(goals_path.read_text(encoding="utf-8"))
        assert persisted["G-LEGACY"]["owner_account_id"] == default

        # 主账号可读；子账号不可见
        root = _viewer("boss", subtree=("boss", "alice", "bob"), is_root=True)
        alice = _viewer("alice")
        assert mgr.list_goals(root) and mgr.get_goal("G-LEGACY", root) is entry
        with pytest.raises(Exception) as excinfo:
            mgr.get_goal("G-LEGACY", alice)
        assert getattr(excinfo.value, "status_code", None) == 404

    def test_default_account_env_override_is_used_for_legacy_data(
        self, goals_path, monkeypatch
    ):
        monkeypatch.setenv("LIUHAO_DEFAULT_ACCOUNT_ID", "machine-owner")
        goals_path.write_text(
            json.dumps({"G-OLD": {"goal_id": "G-OLD", "state": "completed",
                                  "trace": [], "tasks": [], "artifacts": []}}),
            encoding="utf-8",
        )
        AIStateManager._instance = None
        mgr = AIStateManager()
        assert owner_of_entry(mgr.get_goal("G-OLD")) == "machine-owner"


# ─── 7. 重启恢复按 owner 生效 ──────────────────────────────────

class TestRestartRecoveryOwnership:
    def test_boot_recovery_preserves_owner_and_does_not_rerun(
        self, goals_path
    ):
        _make_tree()
        running = {
            "G-A-RUN": {
                "goal_id": "G-A-RUN",
                "state": "running",
                "natural_language": "alice 的目标",
                "scope": "L1",
                "trace": [],
                "tasks": [{"id": "t1", "name": "任务", "status": "running"}],
                "artifacts": [],
                "owner_account_id": "alice",
                "created_at": 1.0,
            },
            "G-B-RUN": {
                "goal_id": "G-B-RUN",
                "state": "running",
                "natural_language": "bob 的目标",
                "scope": "L1",
                "trace": [],
                "tasks": [{"id": "t2", "name": "任务", "status": "running"}],
                "artifacts": [],
                "owner_account_id": "bob",
                "created_at": 1.0,
            },
        }
        goals_path.write_text(json.dumps(running), encoding="utf-8")

        first_boot = AIStateManager()
        for gid, owner in (("G-A-RUN", "alice"), ("G-B-RUN", "bob")):
            entry = first_boot.get_goal(gid)
            assert entry["state"] == "interrupted"
            assert entry["owner_account_id"] == owner
            assert entry["tasks"][0]["status"] == "interrupted"

        # 第二次重启：仍然是 interrupted，没有被重复执行
        AIStateManager._instance = None
        second_boot = AIStateManager()
        assert second_boot.get_goal("G-A-RUN")["state"] == "interrupted"
        assert second_boot.get_goal("G-B-RUN")["state"] == "interrupted"
        assert second_boot._reconcile_interrupted_goals() == 0

    def test_scoped_reconcile_only_touches_given_owner(self):
        _make_tree()
        mgr = AIStateManager()
        for gid, owner in (("G-A", "alice"), ("G-B", "bob")):
            with mgr._goal_lock:
                mgr._goals[gid] = {
                    "goal_id": gid,
                    "state": "running",
                    "natural_language": f"{owner} 的目标",
                    "scope": "L1",
                    "trace": [],
                    "tasks": [{"id": "t", "name": "任务", "status": "running"}],
                    "artifacts": [],
                    "owner_account_id": owner,
                    "created_at": 1.0,
                }
        assert mgr._reconcile_interrupted_goals(owner="alice") == 1
        assert mgr.get_goal("G-A")["state"] == "interrupted"
        assert mgr.get_goal("G-B")["state"] == "running"

        assert mgr._reconcile_interrupted_goals(owner="bob") == 1
        assert mgr.get_goal("G-B")["state"] == "interrupted"


# ─── 8. 产物下载按 owner 过滤 ──────────────────────────────────

class TestArtifactOwnership:
    def test_artifact_download_partitioned_by_account(
        self, client, tmp_path
    ):
        """产物按 owner 落在 ``accounts/<owner>/``；跨账号读一律 404。"""
        _make_tree()
        reports = tmp_path / "accounts" / "alice" / "reports"
        reports.mkdir(parents=True)
        (reports / "only-alice.txt").write_text("alice 的产物", encoding="utf-8")
        (tmp_path / "public.txt").write_text("公共文件", encoding="utf-8")

        mgr = AIStateManager()
        _seed_goal(
            mgr, "G-ART", "alice",
            artifacts=["reports/only-alice.txt"],
        )

        def get(path: str, subject: str, account: Optional[str] = None):
            params = {"path": path}
            if account:
                params["account"] = account
            return client.get(
                "/v1/files/content", params=params, headers=_headers(subject)
            )

        # 别人的产物：404，且文案与"不存在"完全一致
        bob_resp = get("reports/only-alice.txt", "bob")
        ghost_resp = get("reports/does-not-exist.txt", "bob")
        assert bob_resp.status_code == 404
        assert ghost_resp.status_code == 404
        assert bob_resp.json()["detail"].replace("reports/only-alice.txt", "<path>") == \
            ghost_resp.json()["detail"].replace("reports/does-not-exist.txt", "<path>")

        # 显式指定别人的空间：结构性 404，中性文案
        cross = client.get(
            "/v1/files/content",
            params={"path": "reports/only-alice.txt", "account": "alice"},
            headers=_headers("bob"),
        )
        assert cross.status_code == 404
        assert cross.json()["code"] == "ACCOUNT_SUBTREE_DENIED"
        assert cross.json()["message"] == "资源不存在或无权访问"

        # 自己：逻辑根就是自己的空间；根账号需显式 account 才能只读浏览
        alice_resp = get("reports/only-alice.txt", "alice")
        assert alice_resp.status_code == 200
        assert alice_resp.json()["content"] == "alice 的产物"
        assert get("reports/only-alice.txt", "boss", account="alice").status_code == 200

        # 公共区（工作区根）只对根可见；成员读公共区按越权 404
        assert get("public.txt", "bob").status_code == 404
        assert get("public.txt", "boss").status_code == 200

        # 目录列表：各自空间互不可见
        listing = client.get(
            "/v1/files", params={"path": "reports"}, headers=_headers("bob")
        )
        assert listing.status_code == 200
        assert listing.json()["entries"] == []
        listing_alice = client.get(
            "/v1/files", params={"path": "reports"}, headers=_headers("alice")
        )
        assert any(
            e["name"] == "only-alice.txt" for e in listing_alice.json()["entries"]
        )


# ─── 归属基础语义 ──────────────────────────────────────────────

class TestOwnerSemantics:
    def test_missing_owner_is_readable_and_defaulted(self):
        viewer = _viewer("local-human", standalone=True)
        assert owner_of_entry({}) == default_account_id()
        assert viewer.sees({"goal_id": "g"}) is True
        # 已有归属的别人目标仍然不可见
        assert viewer.sees({"goal_id": "g", "owner_account_id": "someone-else"}) is False
        assert viewer.owns({"goal_id": "g", "owner_account_id": "someone-else"}) is False

    def test_standalone_viewer_is_not_a_root(self):
        """没有账号节点的老部署令牌不能因此看到"所有"目标。"""
        # standalone 只多认"本机默认账号"的历史数据，绝不等于 root。
        viewer = _viewer("solo", standalone=True, is_root=False)
        assert viewer.sees({"owner_account_id": "other"}) is False
