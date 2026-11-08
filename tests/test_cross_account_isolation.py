"""跨账号越权回归：项目面 / 知识库面 / 产物文件面（真实 HTTP + 真实 JWT）。

要求逐条对应：

1. 成员不能列出 / 读取 / 删除主账号的项目，也不能把自己的目标挂到主账号
   项目上；越权与不存在**同为中性 404**（响应体逐字一致）。
2. 主账号可聚合读整棵子树（项目、文件），但写只落自己范围；**知识库是账号
   自有资产**，主账号也不聚合成员文档（`docs/account-hierarchy.md` 日常能力表），
   因此知识库面的跨账号读写对主账号同样中性 404。
3. 所有账号（含主账号）的知识库只读自己：列表、分块、检索（vector + FTS）、
   上传/入库都按账号隔离；跨账号分块/删除/任务查询一律中性 404；搜索 ``count``
   是命中数而不是全库向量数。
4. 产物文件下载按 ``accounts/<owner>/`` 分区；列表回填的 ``rel_path`` 是
   工作区相对路径（带账号前缀），前端原样回填后 list/read 仍然稳定。
5. 产物索引没有跨账号的 HTTP 列出/下载入口（只有本机 CLI/报表读全库）。

全部用例使用独立临时数据目录 + 真实 FastAPI 应用；不触碰正在压测的实例。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict

import pytest
from fastapi.testclient import TestClient

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


def _headers(subject: str, scope: str = "L0") -> Dict[str, str]:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


#: 真实成员令牌：没有 L0/admin 声明，用来验证"删自己是业务权限，不是管理员权限"。
def _member_headers(subject: str) -> Dict[str, str]:
    return _headers(subject, scope="L2")


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))

    import src.kernels.identity as identity_module
    from src.gateway.ai_management import AIStateManager
    from src.gateway.projects import ProjectStore
    from src.knowledge.documents import reset_knowledge_service
    from src.knowledge.ingest_jobs import reset_ingest_job_manager

    monkeypatch.setattr(identity_module, "_global_manager", None)
    ProjectStore._instance = None
    AIStateManager._instance = None
    reset_knowledge_service()
    reset_ingest_job_manager()
    _make_tree()

    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client

    ProjectStore._instance = None
    AIStateManager._instance = None
    reset_knowledge_service()
    reset_ingest_job_manager()
    monkeypatch.setattr(identity_module, "_global_manager", None)


def _assert_neutral_404(resp, missing_resp=None):
    assert resp.status_code == 404, resp.text
    body = resp.json()
    detail = body.get("detail", body)
    message = detail.get("message") if isinstance(detail, dict) else detail
    assert message == "资源不存在或无权访问", body
    if missing_resp is not None:
        assert missing_resp.status_code == 404
        assert resp.json() == missing_resp.json()


# ─── 1. 项目面 ────────────────────────────────────────────────


class TestProjectAccountIsolation:
    def test_member_cannot_list_read_or_delete_root_project(self, client):
        boss = _headers("boss")
        alice = _headers("alice")

        created = client.post(
            "/v1/projects", json={"name": "主账号项目", "description": ""}, headers=boss
        )
        assert created.status_code == 200, created.text
        project_id = created.json()["project_id"]

        # 成员列表里根本不应出现
        listed = client.get("/v1/projects", headers=alice).json()
        assert project_id not in {p["project_id"] for p in listed["projects"]}

        # 读取：越权与不存在同为中性 404，响应体逐字一致
        visible = client.get(f"/v1/projects/{project_id}", headers=alice)
        missing = client.get("/v1/projects/does-not-exist", headers=alice)
        _assert_neutral_404(visible, missing)

        # 删除同样越权 404
        _assert_neutral_404(
            client.delete(f"/v1/projects/{project_id}", headers=alice)
        )
        # 主账号自己的项目仍在
        assert client.get(f"/v1/projects/{project_id}", headers=boss).status_code == 200

    def test_root_aggregates_subtree_but_member_only_sees_own(self, client):
        boss = _headers("boss")
        alice = _headers("alice")
        bob = _headers("bob")

        alice_project = client.post(
            "/v1/projects", json={"name": "alice 的", "description": ""}, headers=alice
        ).json()["project_id"]
        bob_project = client.post(
            "/v1/projects", json={"name": "bob 的", "description": ""}, headers=bob
        ).json()["project_id"]

        alice_ids = {p["project_id"] for p in client.get("/v1/projects", headers=alice).json()["projects"]}
        assert alice_project in alice_ids and bob_project not in alice_ids

        boss_ids = {p["project_id"] for p in client.get("/v1/projects", headers=boss).json()["projects"]}
        assert {alice_project, bob_project} <= boss_ids
        assert client.get(f"/v1/projects/{bob_project}", headers=boss).status_code == 200

    def test_member_cannot_attach_goal_to_root_project(self, client):
        boss = _headers("boss")
        alice = _headers("alice")

        project_id = client.post(
            "/v1/projects", json={"name": "主账号项目", "description": ""}, headers=boss
        ).json()["project_id"]

        resp = client.post(
            "/v1/goals",
            json={"natural_language": "越权挂载测试", "project_id": project_id},
            headers=alice,
        )
        _assert_neutral_404(resp)

        # 目标没有被创建（否则就是把越权请求变成了"部分成功"）
        goals = client.get("/v1/goals", headers=alice).json()
        assert goals["count"] == 0


# ─── 2. 知识库面 ─────────────────────────────────────────────


class TestKnowledgeAccountIsolation:
    def test_member_cannot_list_read_or_search_root_documents(self, client):
        boss = _headers("boss")
        bob = _headers("bob")

        ingested = client.post(
            "/v1/knowledge/documents",
            json={"text": "ROOT-ONLY-MARKER 主账号机密文档。", "doc_id": "root-doc"},
            headers=boss,
        )
        assert ingested.status_code == 200, ingested.text
        doc_id = ingested.json()["document_id"]

        # 列表：成员看不到主账号文档
        listed = client.get("/v1/knowledge/documents", headers=bob).json()
        assert doc_id not in {d["document_id"] for d in listed["documents"]}
        assert listed["total"] == 0

        # 分块：越权与不存在同为中性 404
        chunks = client.get(f"/v1/knowledge/documents/{doc_id}/chunks", headers=bob)
        _assert_neutral_404(
            chunks, client.get("/v1/knowledge/documents/no-such/chunks", headers=bob)
        )

        # 搜索：命中数 0，绝不出现主账号内容
        hits = client.post(
            "/v1/knowledge/search",
            json={"query": "ROOT-ONLY-MARKER", "top_k": 3},
            headers=bob,
        ).json()
        assert hits["count"] == 0
        assert hits["hits"] == []

        # FTS 词面通路也必须按账号过滤：全局索引里有该词，但成员可见候选为 0，
        # 证明不是"先取全局 top-k 再丢弃"（那样别人的文档会挤掉自己的命中）。
        assert hits["retrieval"]["lexical_candidates"] == 0

        # 主账号读自己的文档正常（但不聚合成员知识库，见另一个用例）
        assert client.get(
            f"/v1/knowledge/documents/{doc_id}/chunks", headers=boss
        ).status_code == 200

    def test_writes_land_only_in_own_namespace(self, client):
        alice = _headers("alice")
        bob = _headers("bob")
        boss = _headers("boss")

        alice_ing = client.post(
            "/v1/knowledge/documents",
            json={"text": "ALICE-MARKER 内容。", "doc_id": "same-name"},
            headers=alice,
        ).json()
        bob_ing = client.post(
            "/v1/knowledge/documents",
            json={"text": "BOB-MARKER 完全不同的内容。", "doc_id": "same-name"},
            headers=bob,
        ).json()

        # 同名文档不会互相覆盖：两个账号各自有独立、可预期的命名空间 id
        assert alice_ing["document_id"] == "alice--same-name"
        assert bob_ing["document_id"] == "bob--same-name"
        assert alice_ing["document_id"] != bob_ing["document_id"]

        alice_hits = client.post(
            "/v1/knowledge/search",
            json={"query": "ALICE-MARKER", "top_k": 3},
            headers=alice,
        ).json()
        bob_hits = client.post(
            "/v1/knowledge/search",
            json={"query": "ALICE-MARKER", "top_k": 3},
            headers=bob,
        ).json()
        assert alice_hits["count"] >= 1
        assert alice_hits["count"] == len(alice_hits["hits"])
        # 成员搜索里绝不能出现别人的文档（哪怕相似度命中同名词也无所谓）
        assert bob_hits["count"] == len(bob_hits["hits"])
        assert alice_ing["document_id"] not in {
            hit["doc_id"] for hit in bob_hits["hits"]
        }
        assert all(
            "ALICE-MARKER" not in hit["text"] for hit in bob_hits["hits"]
        )

        # 知识库是自有资产：主账号也看不到成员文档（与成员看到的同形）
        boss_list = client.get("/v1/knowledge/documents", headers=boss).json()
        assert boss_list["total"] == 0
        assert alice_ing["document_id"] not in {
            d["document_id"] for d in boss_list["documents"]
        }
        assert bob_ing["document_id"] not in {
            d["document_id"] for d in boss_list["documents"]
        }
        _assert_neutral_404(
            client.get(
                f"/v1/knowledge/documents/{alice_ing['document_id']}/chunks",
                headers=boss,
            )
        )

        # 成员不能删别人的文档（中性 404），自己的可以
        _assert_neutral_404(
            client.delete(
                f"/v1/knowledge/documents/{alice_ing['document_id']}", headers=bob
            )
        )
        assert client.delete(
            f"/v1/knowledge/documents/{bob_ing['document_id']}", headers=bob
        ).status_code == 200

    def test_search_count_is_hits_not_store_size(self, client):
        boss = _headers("boss")
        for index in range(3):
            client.post(
                "/v1/knowledge/documents",
                json={"text": f"COUNT-MARKER 第 {index} 份。", "doc_id": f"count-{index}"},
                headers=boss,
            )
        result = client.post(
            "/v1/knowledge/search",
            json={"query": "COUNT-MARKER", "top_k": 1},
            headers=boss,
        ).json()
        assert len(result["hits"]) <= 1
        assert result["count"] == len(result["hits"])

    def test_ingest_job_is_scoped_to_owner(self, client):
        boss = _headers("boss")
        bob = _headers("bob")

        submitted = client.post(
            "/v1/knowledge/ingest/jobs",
            json={"text": "JOB-MARKER " + "长文本。" * 400, "doc_id": "job-doc"},
            headers=boss,
        )
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]

        _assert_neutral_404(
            client.get(f"/v1/knowledge/ingest/jobs/{job_id}", headers=bob)
        )
        _assert_neutral_404(
            client.post(f"/v1/knowledge/ingest/jobs/{job_id}/cancel", headers=bob)
        )

        from src.knowledge.ingest_jobs import get_ingest_job_manager

        get_ingest_job_manager().wait(job_id, timeout=10)
        assert client.get(
            f"/v1/knowledge/ingest/jobs/{job_id}", headers=boss
        ).status_code == 200

    def test_delete_is_scope_checked_not_admin_gated(self, client):
        """删除按归属判定，而不是先撞管理员门禁。

        真实成员令牌（scope L2，无 admin/L0 声明）：
        * 删别人的文档 = 中性 404，且与"删不存在"响应体逐字相同；
        * 删自己的文档 = 200（"删自己的东西"是业务权限，不该要管理员身份）；
        * 响应体里不得出现"管理员 / L0 / 权限不足"这类层级词；
        * 主账号可删子树内文档，同样不带层级词。
        """
        boss = _headers("boss")
        alice = _member_headers("alice")
        bob = _member_headers("bob")

        alice_doc = client.post(
            "/v1/knowledge/documents",
            json={"text": "ALICE-DELETE-MARKER 只属于 alice。", "doc_id": "secret"},
            headers=alice,
        ).json()["document_id"]
        assert alice_doc == "alice--secret"

        # 越权删除与不存在逐字同形，且不含层级词
        denied = client.delete(
            f"/v1/knowledge/documents/{alice_doc}", headers=bob
        )
        missing = client.delete(
            "/v1/knowledge/documents/no-such-doc", headers=bob
        )
        _assert_neutral_404(denied, missing)
        for word in ("管理员", "L0", "权限不足", "admin"):
            assert word not in denied.text
            assert word not in missing.text

        # 越权请求没有真的删掉别人的文档
        assert client.get(
            f"/v1/knowledge/documents/{alice_doc}/chunks", headers=alice
        ).status_code == 200

        # 成员删自己：200，删完不可见（中性 404）
        bob_doc = client.post(
            "/v1/knowledge/documents",
            json={"text": "BOB-DELETE-MARKER 自己的文档。", "doc_id": "own"},
            headers=bob,
        ).json()["document_id"]
        removed = client.delete(
            f"/v1/knowledge/documents/{bob_doc}", headers=bob
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["deleted"] is True
        forbidden = ("管理员", "L0", "权限不足", "admin")
        assert all(word not in removed.text for word in forbidden)
        assert client.get(
            f"/v1/knowledge/documents/{bob_doc}/chunks", headers=bob
        ).status_code == 404

        # 知识库是自有资产：主账号删成员文档同样是中性 404，不带层级词，
        # 且真正没有被删掉（与成员越权删除逐字同形）。
        root_denied = client.delete(
            f"/v1/knowledge/documents/{alice_doc}", headers=boss
        )
        _assert_neutral_404(root_denied, missing)
        assert all(word not in root_denied.text for word in forbidden)
        assert client.get(
            f"/v1/knowledge/documents/{alice_doc}/chunks", headers=alice
        ).status_code == 200

    def test_root_delete_missing_document_is_neutral_404(self, client):
        """主账号删不存在文档：中性 404，与成员看到的完全同形。"""
        boss = _headers("boss")
        bob = _member_headers("bob")
        root_missing = client.delete(
            "/v1/knowledge/documents/no-such-doc", headers=boss
        )
        member_missing = client.delete(
            "/v1/knowledge/documents/no-such-doc", headers=bob
        )
        _assert_neutral_404(root_missing, member_missing)
        assert "管理员" not in root_missing.text


# ─── 3. 产物文件 / 产物索引 ─────────────────────────────────


class TestArtifactAccountIsolation:
    def test_files_and_rel_path_are_account_scoped(self, client, tmp_path):
        boss = _headers("boss")
        bob = _headers("bob")

        boss_file = tmp_path / "accounts" / "boss" / "report.txt"
        boss_file.parent.mkdir(parents=True, exist_ok=True)
        boss_file.write_text("BOSS-ARTIFACT", encoding="utf-8")
        bob_file = tmp_path / "accounts" / "bob" / "notes.txt"
        bob_file.parent.mkdir(parents=True, exist_ok=True)
        bob_file.write_text("BOB-ARTIFACT", encoding="utf-8")

        # 成员读主账号产物：中性 404
        _assert_neutral_404(
            client.get(
                "/v1/files/content", params={"path": "accounts/boss/report.txt"}, headers=bob
            )
        )

        # 成员列自己的空间：rel_path 是工作区相对路径（带 accounts/bob 前缀），
        # 前端原样回填后再次读取仍然稳定。
        listed = client.get("/v1/files", headers=bob).json()
        entry = next(e for e in listed["entries"] if e["name"] == "notes.txt")
        assert entry["rel_path"] == "accounts/bob/notes.txt"
        read_back = client.get(
            "/v1/files/content", params={"path": entry["rel_path"]}, headers=bob
        )
        assert read_back.status_code == 200
        assert "BOB-ARTIFACT" in read_back.json()["content"]

        # 主账号显式 account= 浏览子树：rel_path 同样带前缀，回填可读
        boss_list = client.get(
            "/v1/files", params={"account": "bob"}, headers=boss
        ).json()
        boss_entry = next(e for e in boss_list["entries"] if e["name"] == "notes.txt")
        assert boss_entry["rel_path"] == "accounts/bob/notes.txt"
        assert client.get(
            "/v1/files/content",
            params={"path": boss_entry["rel_path"]},
            headers=boss,
        ).status_code == 200

        # 主账号读自己 / 子树正常，成员之间互不可见
        assert client.get(
            "/v1/files/content", params={"path": "accounts/boss/report.txt"}, headers=boss
        ).status_code == 200
        assert client.get(
            "/v1/files/content", params={"path": "accounts/boss/report.txt"}, headers=bob
        ).status_code == 404

    def test_artifact_index_has_no_cross_account_http_surface(self, client, tmp_path):
        """产物索引只有本机 CLI/报表入口，HTTP 上没有"列出别人产物"的路由。

        记录本身按工作区相对路径落库；真正的下载面是 /v1/files，已按账号分区。
        这里用真实索引写一条主账号产物，验证成员既不能通过 HTTP 列索引，
        也不能下载该文件。
        """
        from src.artifacts.index import ArtifactIndex

        boss = _headers("boss")
        bob = _headers("bob")

        artifact = tmp_path / "accounts" / "boss" / "secret.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("BOSS-SECRET", encoding="utf-8")

        index_path = str(tmp_path / "artifacts.db")
        with ArtifactIndex(index_path) as index:
            record = index.record(
                workspace=str(tmp_path),
                path="accounts/boss/secret.txt",
                goal_id="G-BOSS",
                source="test",
            )
            assert record.path == "accounts/boss/secret.txt"

        # 没有任何 /v1/artifacts 路由可被成员用来枚举全库
        assert client.get("/v1/artifacts", headers=bob).status_code == 404
        assert client.get("/v1/artifacts/G-BOSS", headers=bob).status_code == 404

        # 下载面按账号分区：成员无法读取主账号产物
        _assert_neutral_404(
            client.get(
                "/v1/files/content",
                params={"path": "accounts/boss/secret.txt"},
                headers=bob,
            )
        )


# ─── 4. 项目库并发写入（跨进程） ──────────────────────────────


def _spawn_project_writer(
    repo_root: str, workspace: str, count: int, tag: str
) -> subprocess.Popen:
    # 同一账号下项目名必须唯一（锁内判定），所以两个进程用各自的前缀，
    # 这样本条用例验证的是"跨进程不丢写"，而不是同名冲突。
    script = (
        "import sys\n"
        f"sys.path.insert(0, {repo_root!r})\n"
        "from src.gateway.projects import ProjectStore\n"
        "store = ProjectStore()\n"
        f"for i in range({count}):\n"
        f"    store.create(f'项目-{tag}-{{i}}', '', creator='proc', owner_account_id='proc')\n"
    )
    env = dict(os.environ)
    env["LIUHAO_WORKSPACE_ROOT"] = workspace
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_concurrent_multiprocess_project_creation_does_not_lose_records(tmp_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workspace = str(tmp_path / "concurrent")
    os.makedirs(workspace, exist_ok=True)
    per_process = 40

    first = _spawn_project_writer(repo_root, workspace, per_process, "a")
    second = _spawn_project_writer(repo_root, workspace, per_process, "b")
    for proc in (first, second):
        stdout, stderr = proc.communicate(timeout=120)
        assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")

    data_path = os.path.join(workspace, "liuhao_projects.json")
    with open(data_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    assert len(data) == per_process * 2, "跨进程并发建项目不能丢记录"
    assert all(entry.get("owner_account_id") == "proc" for entry in data.values())
