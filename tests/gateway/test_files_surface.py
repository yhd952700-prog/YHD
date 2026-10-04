"""P1 产品缺口 #1 的最小真实闭合 —— 工作区文件浏览/读取面（严格只读）。

为什么这个测试存在
------------------
KERNEL-PRODUCT-CAPABILITY.md §19 把 "Files" 列为 **#1 产品缺口**：AI 员工通过执行
内核的 ``file_write`` 真实写出的产物，此前**没有任何人类能浏览或读取**。本测试
证明新加的 ``/v1/files``（列表）与 ``/v1/files/content``（读取）是**真的**：

- 列表/读取返回的是工作区里**真实存在**的文件（内容逐字节对拍，不是 mock）；
- 路径越界（即使目标文件真实存在）被 400 拒绝 —— 证明 ``resolve_in_workspace``
  的 fail-closed 约束真的兜住了，而不是 "看起来像相对路径就放行"；
- 未带令牌 → 401，证明端点真的走了 ``require_human_principal`` 闸门；
- 二进制文件 → 415，不假装成文本。

隔离约定
--------
``LIUHAO_WORKSPACE_ROOT`` 经 monkeypatch 指向 ``tmp_path``，绝不会碰真实工作区。
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="test-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每用例独立临时工作区；绝不会写真实 workspace。"""
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def _write(root, rel, content: bytes):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(content)
    return rel


def test_list_root_returns_real_file(client, tmp_path):
    c, headers = client
    expected = b"LIUHAO-FILES-PROOF\n"
    _write(str(tmp_path), "hello.txt", expected)

    r = c.get("/v1/files", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == ""
    names = {e["name"]: e for e in body["entries"]}
    assert "hello.txt" in names
    assert names["hello.txt"]["type"] == "file"
    assert names["hello.txt"]["size"] == len(expected)


def test_read_content_matches_byte_for_byte(client, tmp_path):
    c, headers = client
    expected = "LIUHAO-FILES-PROOF-内容对拍\n"
    _write(str(tmp_path), "proof.txt", expected.encode("utf-8"))

    r = c.get("/v1/files/content?path=proof.txt", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == expected
    assert body["encoding"] == "utf-8"
    assert body["size"] == len(expected.encode("utf-8"))


def test_nested_directory_list_and_read(client, tmp_path):
    c, headers = client
    expected = "nested-proof\n"
    _write(str(tmp_path), "sub/deep/nested.txt", expected.encode("utf-8"))

    r = c.get("/v1/files?path=sub/deep", headers=headers)
    assert r.status_code == 200, r.text
    names = {e["name"]: e for e in r.json()["entries"]}
    assert "nested.txt" in names

    r2 = c.get("/v1/files/content?path=sub/deep/nested.txt", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["content"] == expected


def test_list_missing_directory_is_404(client, tmp_path):
    c, headers = client
    r = c.get("/v1/files?path=does-not-exist", headers=headers)
    assert r.status_code == 404


def test_read_missing_file_is_404(client, tmp_path):
    c, headers = client
    r = c.get("/v1/files/content?path=ghost.txt", headers=headers)
    assert r.status_code == 404


def test_path_escape_is_rejected_even_if_target_exists(client, tmp_path):
    """越界访问：目标文件真实存在，但落在工作区外，必须 400 拒绝。"""
    c, headers = client
    # 在工作区**之外**放一个真实文件，证明拒绝不是 "文件不存在" 顺带的结果。
    outside = os.path.join(str(tmp_path.parent), "outside-root.txt")
    with open(outside, "wb") as fh:
        fh.write(b"secret-should-never-be-read\n")
    # 相对路径 ".." 一步跳出工作区根。
    r = c.get("/v1/files/content?path=../outside-root.txt", headers=headers)
    assert r.status_code == 400, r.text


def test_unauthenticated_is_rejected(client, tmp_path):
    c, _headers = client
    r = c.get("/v1/files")
    assert r.status_code == 401


def test_binary_file_is_415(client, tmp_path):
    c, headers = client
    _write(str(tmp_path), "blob.bin", bytes(range(256)) * 4)  # 非 UTF-8
    r = c.get("/v1/files/content?path=blob.bin", headers=headers)
    assert r.status_code == 415
