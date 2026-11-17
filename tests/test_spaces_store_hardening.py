"""协作空间存储专属硬化测试（补缺口轮，2026-11-17）。

覆盖此前零专属测试的 ``src/spaces/store.py``（SpaceStore）：

* 空间 CRUD 与创建者自动成为 owner 成员；
* 可见范围隔离：非成员看不见（返回 None / 列表为空，不区分不存在），
  任务不能跨空间被读/改/删；
* 成员管理：role 校验、重复添加改角色、创建者不能被移出；
* 任务墙三态流转与字段校验；
* 删空间级联清成员与任务；落盘后重开仍在。

全部用 tmp_path 独立 SQLite，不触碰真实 spaces.db。
"""

from __future__ import annotations

import pytest

from src.spaces.store import SpaceStore


@pytest.fixture()
def store(tmp_path):
    return SpaceStore(str(tmp_path / "spaces.db"))


# --------------------------------------------------------------------------- #
# 空间 CRUD
# --------------------------------------------------------------------------- #


def test_create_space_roundtrip_and_owner_membership(store):
    space = store.create_space("alice", "  增长小队  ", intro="  一起搞增长 ")
    assert space["name"] == "增长小队"  # 名字/介绍去空白
    assert space["intro"] == "一起搞增长"
    assert space["owner"] == "alice"
    assert space["members"] == [
        {
            "space_id": space["space_id"],
            "principal": "alice",
            "role": "owner",
            "joined_at": pytest.approx(space["created_at"]),
        }
    ]
    assert space["tasks"] == []

    with pytest.raises(ValueError):
        store.create_space("alice", "   ")
    with pytest.raises(ValueError):
        store.create_space("alice", "")


def test_empty_store_and_unknown_space(store):
    assert store.list_spaces({"alice"}) == []
    assert store.list_spaces(set()) == []  # 无可见主体恒空，不报错
    assert store.get_space("space-nope") is None
    assert store.get_space("space-nope", principals={"alice"}) is None
    assert store.delete_space("space-nope") is False
    assert store.add_task("space-nope", "事", created_by="alice") is None
    assert store.add_member("space-nope", "bob") is None
    assert store.remove_member("space-nope", "bob") is False


def test_list_spaces_counts_and_newest_first(store):
    first = store.create_space("alice", "一号空间")
    second = store.create_space("alice", "二号空间")
    store.add_member(first["space_id"], "bob")
    store.add_task(first["space_id"], "任务一", created_by="alice")
    store.add_task(first["space_id"], "任务二", created_by="bob")

    listing = store.list_spaces({"alice"})
    assert [s["space_id"] for s in listing] == [second["space_id"], first["space_id"]]
    by_id = {s["space_id"]: s for s in listing}
    assert by_id[first["space_id"]]["member_count"] == 2
    assert by_id[first["space_id"]]["task_count"] == 2
    assert by_id[second["space_id"]]["member_count"] == 1
    assert by_id[second["space_id"]]["task_count"] == 0


# --------------------------------------------------------------------------- #
# 可见范围隔离
# --------------------------------------------------------------------------- #


def test_visibility_isolation_for_non_members(store):
    space = store.create_space("alice", "Alice 的空间")
    space_id = space["space_id"]

    # Bob 既不是 owner 也不是成员：列表没有、详情 None（不区分不存在）
    assert store.list_spaces({"bob"}) == []
    assert store.get_space(space_id, principals={"bob"}) is None
    # 不带 principals 时是内部读法，能读到（网关层负责先算可见集合）
    assert store.get_space(space_id) is not None
    # 根账号口径：principals 含 owner 时可见
    assert store.get_space(space_id, principals={"root", "alice"}) is not None

    # 成为成员后可见
    store.add_member(space_id, "bob")
    assert [s["space_id"] for s in store.list_spaces({"bob"})] == [space_id]
    assert store.get_space(space_id, principals={"bob"}) is not None

    # 被移出后立刻不可见
    assert store.remove_member(space_id, "bob") is True
    assert store.list_spaces({"bob"}) == []
    assert store.get_space(space_id, principals={"bob"}) is None


# --------------------------------------------------------------------------- #
# 成员管理
# --------------------------------------------------------------------------- #


def test_member_rules(store):
    space = store.create_space("alice", "成员规则")
    space_id = space["space_id"]

    with pytest.raises(ValueError):
        store.add_member(space_id, "  ")
    with pytest.raises(ValueError):
        store.add_member(space_id, "bob", role="superuser")

    added = store.add_member(space_id, "bob")
    assert added["role"] == "member"
    # 重复添加同一人：改角色，不新增一行
    promoted = store.add_member(space_id, "bob", role="admin")
    assert promoted["role"] == "admin"
    detail = store.get_space(space_id)
    assert len(detail["members"]) == 2  # owner + bob

    # 创建者不能被移出；移不存在的人返回 False
    assert store.remove_member(space_id, "alice") is False
    assert store.remove_member(space_id, "carol") is False
    assert store.remove_member(space_id, "bob") is True
    assert store.remove_member(space_id, "bob") is False


# --------------------------------------------------------------------------- #
# 任务墙
# --------------------------------------------------------------------------- #


def test_task_wall_lifecycle_and_validation(store):
    space = store.create_space("alice", "任务墙")
    space_id = space["space_id"]

    with pytest.raises(ValueError):
        store.add_task(space_id, "  ", created_by="alice")

    task = store.add_task(space_id, " 写方案 ", created_by="alice", assignee=" bob ")
    assert task["title"] == "写方案"
    # 注意：add_task 返回行的 status/assignee 两列当前被 src 写反
    # （见交回报告的 src bug 证据），这里不锁死错误值；三态流转从
    # update_task 起验证（update_task 的写入路径本身正确）。
    item_id = task["item_id"]

    doing = store.update_task(item_id, space_id, status="doing")
    assert doing["status"] == "doing"
    done = store.update_task(
        item_id, space_id, status="done", title="写方案 v2", assignee=""
    )
    assert done["status"] == "done"
    assert done["title"] == "写方案 v2"
    assert done["assignee"] == ""

    with pytest.raises(ValueError):
        store.update_task(item_id, space_id, status="blocked")  # 只有三态
    with pytest.raises(ValueError):
        store.update_task(item_id, space_id, title=" ")
    # 不带任何字段：原样返回当前任务，不报错
    assert store.update_task(item_id, space_id)["item_id"] == item_id
    # 未知字段被忽略，不会写进库
    assert store.update_task(item_id, space_id, hacker="x")["status"] == "done"


def test_tasks_cannot_cross_spaces(store):
    a = store.create_space("alice", "空间 A")
    b = store.create_space("alice", "空间 B")
    task = store.add_task(a["space_id"], "A 的任务", created_by="alice")
    item_id = task["item_id"]

    # 拿 B 的 space_id 读/改/删 A 的任务：一律落空
    assert store.get_task(item_id, b["space_id"]) is None
    assert store.update_task(item_id, b["space_id"], status="done") is None
    assert store.delete_task(item_id, b["space_id"]) is False
    # A 侧任务完好（status 初值受 add_task 写反 bug 影响，这里只验标题与可改性）
    assert store.get_task(item_id, a["space_id"])["title"] == "A 的任务"
    assert store.update_task(item_id, a["space_id"], status="doing")["status"] == "doing"

    assert store.delete_task(item_id, a["space_id"]) is True
    assert store.delete_task(item_id, a["space_id"]) is False
    assert store.get_task(item_id, a["space_id"]) is None


# --------------------------------------------------------------------------- #
# 删除级联与持久化
# --------------------------------------------------------------------------- #


def test_delete_space_cascades_members_and_tasks(store):
    space = store.create_space("alice", "待删空间")
    space_id = space["space_id"]
    store.add_member(space_id, "bob")
    store.add_task(space_id, "会一起消失", created_by="alice")

    assert store.delete_space(space_id) is True
    assert store.get_space(space_id) is None
    assert store.list_spaces({"alice", "bob"}) == []
    assert store.delete_space(space_id) is False  # 再删返回 False
    # 级联后成员/任务不残留：同名重建不会串旧数据
    fresh = store.create_space("alice", "待删空间")
    assert fresh["space_id"] != space_id
    assert fresh["tasks"] == []
    assert len(fresh["members"]) == 1


def test_spaces_survive_reopen(tmp_path):
    path = str(tmp_path / "spaces.db")
    first = SpaceStore(path)
    space = first.create_space("alice", "重启还在")
    first.add_task(space["space_id"], "还在的任务", created_by="alice")

    second = SpaceStore(path)
    reloaded = second.get_space(space["space_id"], principals={"alice"})
    assert reloaded is not None
    assert reloaded["name"] == "重启还在"
    assert [t["title"] for t in reloaded["tasks"]] == ["还在的任务"]
