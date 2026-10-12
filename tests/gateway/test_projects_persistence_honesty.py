"""ProjectStore 持久化诚实性 —— 落盘失败时请求不失败，但必须如实标注 persisted。

背景
----
``ProjectStore._save()`` 吞掉持久化异常（**有意**：不打断请求，见源码注释）。
但 ``create`` / ``delete`` 此前无论是否真正落盘都报成功 —— 一个"已创建"却没能落盘
的项目，会在进程重启后**静默消失**，调用方无从得知。

最小修复（团队裁决）：保留"请求不失败"的可用性权衡，但响应里增量返回
``persisted`` 布尔字段，如实反映这次落盘结果；正常路径 ``persisted=true``，
落盘失败 ``persisted=false``。该字段向后兼容（console 不消费则不受影响）。

测试方式
--------
直接调用端点**可调用对象**（``create_project`` / ``delete_project``）以断言其返回
契约；HTTP 鉴权（401）与端到端路由已由 ``tests/gateway/test_projects_surface.py``
覆盖。此处刻意不经 ``TestClient``：本机沙箱下 gateway 生命周期的网络探测会挂起，
而本用例要验证的是响应契约本身，直接调用更聚焦、更稳定。

红→绿
-----
本文件先于实现提交：对**未修复**的实现，``persisted`` 字段不存在 → 断言 KeyError
（红）；修复后如实返回 true/false（绿）。
"""

from __future__ import annotations

import pytest

from src.gateway.projects import (
    ProjectCreateRequest,
    ProjectStore,
    create_project,
    delete_project,
)


@pytest.fixture(autouse=True)
def _reset_project_singleton():
    """ProjectStore 是模块级单例，用例前后各复位一次，避免跨用例污染。"""
    ProjectStore._instance = None
    yield
    ProjectStore._instance = None


def test_create_reports_persisted_true_on_healthy_store(tmp_path, monkeypatch):
    """正常路径：真实落盘成功 -> 响应 persisted=true（且原有字段不变）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(ws))

    body = create_project(
        ProjectCreateRequest(name="ok", description="demo"), principal="test-human"
    )

    # 诚实标注：这次落盘成功。
    assert body["persisted"] is True
    # 向后兼容：原有字段与语义不变。
    assert body["name"] == "ok"
    assert "project_id" in body
    # 确实落到了工作区内的 JSON（不是只改了内存）。
    assert (ws / "liuhao_projects.json").exists()


def test_create_survives_but_reports_persisted_false_when_disk_fails(
    tmp_path, monkeypatch
):
    """负向对照：注入**真实**落盘失败 -> 请求仍成功，但 persisted 如实为 false。

    注入方式：把 ``LIUHAO_WORKSPACE_ROOT`` 指向一个"是文件不是目录"的路径，
    使 ``_save()`` 的 ``os.makedirs`` 抛错（等价于磁盘/权限故障）。这证明
    ``persisted=false`` 分支**可达**，而不是又一个恒真字段。
    """
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")  # 占位文件：其下的路径无法建目录
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(blocker))

    # 可用性保留：调用不抛异常（等价于 HTTP 不 500）。
    body = create_project(
        ProjectCreateRequest(name="doomed", description=""), principal="test-human"
    )

    # 诚实性：落盘未成功，绝不报成功。
    assert body["persisted"] is False
    assert body["name"] == "doomed"


def test_delete_reports_persisted_true_on_healthy_store(tmp_path, monkeypatch):
    """删除路径同样如实标注 persisted。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(ws))

    created = create_project(
        ProjectCreateRequest(name="x", description=""), principal="test-human"
    )
    body = delete_project(created["project_id"], principal="test-human")

    assert body["ok"] is True
    assert body["persisted"] is True
