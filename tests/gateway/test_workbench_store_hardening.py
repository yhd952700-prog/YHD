"""工作台存储专属硬化测试（补缺口轮，2026-11-17）。

``tests/gateway/test_workbench_groups_backend.py`` 已覆盖分组的网关
CRUD / 排序 / 账号隔离 / 迁移幂等，本文件只补它没覆盖的存储层路径
（直接调 ``src/workbench/store.py`` 的 WorkbenchStore）：

* Bot CRUD 边角：jarvis 固定 id 改名、置顶排序、routines JSON 往返、
  PATCH 的 None/未知字段语义、跨账号隔离、重复 id 的现状；
* 头像：魔数校验四格式与大小上限、set/clear 的先验权后删文件；
* 群聊：成员去重与 jarvis 隐式成员、增删成员、archived/left 映射、
  删 Bot/删群聊的级联；
* 贾维斯本人例程：按账号隔离、去空白、上限 100；
* 迁移边角：jarvis 跳过、旧快照不覆盖新数据、残缺快照不抹已有字段。

全部用 tmp_path 独立 SQLite，不走网关、不触碰真实 workbench.db。
"""

from __future__ import annotations

import sqlite3

import pytest

from src.workbench.store import (
    JARVIS_ID,
    MAX_AVATAR_BYTES,
    WorkbenchStore,
    avatar_media_type,
    validate_avatar_bytes,
)

ALICE = ["alice"]
BOB = ["bob"]


@pytest.fixture()
def store(tmp_path):
    return WorkbenchStore(str(tmp_path / "workbench.db"))


def _bot(store, owner="alice", **fields):
    fields.setdefault("name", "测试 Bot")
    return store.create_bot(owner, fields)


# --------------------------------------------------------------------------- #
# Bot CRUD 边角
# --------------------------------------------------------------------------- #


def test_create_bot_jarvis_id_is_remapped_and_fields_roundtrip(store):
    bot = _bot(store, id=JARVIS_ID, name="冒充贾维斯", pinned=True,
               routines=["晨报", "巡检"], order_index=3)
    assert bot["id"] != JARVIS_ID
    assert bot["id"].startswith("bot-")
    assert bot["pinned"] is True
    assert bot["hidden"] is False
    assert bot["routines"] == ["晨报", "巡检"]
    assert bot["avatar_url"] == ""

    custom = _bot(store, id="my-custom-id", name="自定义 id")
    assert custom["id"] == "my-custom-id"  # 迁移来的旧 id 原样保留


def test_list_bots_ordering_pinned_then_order_index(store):
    a = _bot(store, name="A", order_index=2)
    b = _bot(store, name="B", order_index=1)
    c = _bot(store, name="C", order_index=9, pinned=True)
    listing = store.list_bots(ALICE)
    assert [x["id"] for x in listing] == [c["id"], b["id"], a["id"]]
    assert store.list_bots(BOB) == []
    assert store.list_bots([]) == []  # 空主体集合安全返回空


def test_bot_cross_account_isolation(store):
    bot = _bot(store)
    assert store.get_bot(BOB, bot["id"]) is None
    assert store.update_bot(BOB, bot["id"], {"name": "改名"}) is None
    assert store.delete_bot(BOB, bot["id"]) is False
    assert store.get_bot(ALICE, bot["id"])["name"] == "测试 Bot"
    # 根账号口径：principals 含 owner 时可见
    assert store.get_bot(["root", "alice"], bot["id"]) is not None


def test_update_bot_patch_semantics(store):
    bot = _bot(store, job="会计", color="red")
    updated = store.update_bot(
        ALICE, bot["id"],
        {"name": "新名字", "job": None, "evil_field": "x", "pinned": True,
         "routines": ["日报"], "order_index": 5},
    )
    assert updated["name"] == "新名字"
    assert updated["job"] == "会计"  # None 表示不改，不是清空
    assert updated["color"] == "red"  # 没提到的字段不动
    assert updated["pinned"] is True
    assert updated["routines"] == ["日报"]
    assert updated["order_index"] == 5
    assert "evil_field" not in updated  # 未知字段不落库

    # 空 patch：原样返回当前行；不存在的 Bot 返回 None
    assert store.update_bot(ALICE, bot["id"], {})["id"] == bot["id"]
    assert store.update_bot(ALICE, "bot-nope", {"name": "x"}) is None
    assert store.update_bot(ALICE, "bot-nope", {}) is None


def test_create_bot_duplicate_id_raises_integrity_error(store):
    # 现状记录：同 id 二次 create 没有友好错误，直接抛 sqlite3
    # IntegrityError（网关层未捕获，会变 500）——见交回报告。
    bot = _bot(store, id="dup-id")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_bot("alice", {"id": "dup-id", "name": "重复"})
    assert store.get_bot(ALICE, bot["id"])["name"] == "测试 Bot"


def test_reorder_bots_scoped_to_owner(store):
    a = _bot(store, name="A")
    b = _bot(store, name="B")
    store.reorder_bots(ALICE, [b["id"], a["id"]])
    assert [x["id"] for x in store.list_bots(ALICE)] == [b["id"], a["id"]]
    # 别人的 reorder 碰不到 Alice 的 Bot；未知 id 静默跳过
    store.reorder_bots(BOB, [a["id"], "bot-ghost"])
    assert [x["id"] for x in store.list_bots(ALICE)] == [b["id"], a["id"]]


def test_delete_bot_removes_chat_membership(store):
    bot = _bot(store)
    chat = store.create_group_chat("alice", "群", [bot["id"]])
    assert store.delete_bot(ALICE, bot["id"]) is True
    assert store.get_bot(ALICE, bot["id"]) is None
    assert store.get_group_chat(ALICE, chat["id"])["member_bot_ids"] == []
    assert store.delete_bot(ALICE, bot["id"]) is False


# --------------------------------------------------------------------------- #
# 头像校验与 set/clear
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "data,ext",
    [
        (b"\x89PNG\r\n\x1a\n" + b"0" * 8, "png"),
        (b"\xff\xd8\xff\xe0" + b"0" * 8, "jpg"),
        (b"GIF87a" + b"0" * 8, "gif"),
        (b"GIF89a" + b"0" * 8, "gif"),
        (b"RIFF\x04\x00\x00\x00WEBP" + b"0" * 4, "webp"),
    ],
)
def test_validate_avatar_bytes_accepts_real_formats(data, ext):
    assert validate_avatar_bytes(data) == ext
    assert avatar_media_type(ext).startswith("image/")


def test_validate_avatar_bytes_rejects_bad_inputs():
    with pytest.raises(ValueError):
        validate_avatar_bytes(b"")
    with pytest.raises(ValueError):
        validate_avatar_bytes(b"this is not an image")
    with pytest.raises(ValueError):
        validate_avatar_bytes(b"RIFF\x04\x00\x00\x00WAVE")  # RIFF 但不是 WEBP
    with pytest.raises(ValueError):
        validate_avatar_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * MAX_AVATAR_BYTES)
    assert avatar_media_type("bmp") == "application/octet-stream"


def test_bot_avatar_set_clear_and_ownership_order(store, tmp_path):
    bot = _bot(store)
    assert store.find_bot_avatar_file(bot["id"]) is None
    assert store.set_bot_avatar(BOB, bot["id"]) is None  # 非 owner 设不了

    set_result = store.set_bot_avatar(ALICE, bot["id"])
    assert set_result["avatar_url"] == f"/v1/workbench/bots/{bot['id']}/avatar"

    # 放一个真实头像文件后，clear 必须先验权：Bob 清不掉且文件还在
    avatar_dir = store.avatar_dir()
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_file = avatar_dir / f"{bot['id']}.png"
    avatar_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert store.clear_bot_avatar(BOB, bot["id"]) is None
    assert avatar_file.is_file()

    cleared = store.clear_bot_avatar(ALICE, bot["id"])
    assert cleared["avatar_url"] == ""
    assert not avatar_file.exists()
    assert store.clear_bot_avatar(ALICE, "bot-nope") is None


# --------------------------------------------------------------------------- #
# 群聊
# --------------------------------------------------------------------------- #


def test_group_chat_members_dedup_and_jarvis_implicit(store):
    a = _bot(store, name="A")
    b = _bot(store, name="B")
    chat = store.create_group_chat(
        "alice", "作战群", [a["id"], a["id"], JARVIS_ID, b["id"]]
    )
    # 重复成员去重，jarvis 是隐式成员不入表
    assert chat["member_bot_ids"] == [a["id"], b["id"]]
    assert chat["archived"] is False
    assert chat["left"] is False

    # 重复添加幂等；添加 jarvis 是 no-op 但返回当前群聊
    again = store.add_group_chat_member(ALICE, chat["id"], a["id"])
    assert again["member_bot_ids"] == [a["id"], b["id"]]
    assert store.add_group_chat_member(ALICE, chat["id"], JARVIS_ID) is not None
    assert store.add_group_chat_member(ALICE, "chat-nope", a["id"]) is None
    assert store.add_group_chat_member(BOB, chat["id"], a["id"]) is None

    removed = store.remove_group_chat_member(ALICE, chat["id"], a["id"])
    assert removed["member_bot_ids"] == [b["id"]]
    assert store.remove_group_chat_member(BOB, chat["id"], b["id"]) is None


def test_group_chat_update_flags_and_delete_cascade(store):
    a = _bot(store)
    chat = store.create_group_chat("alice", "旧名", [a["id"]])
    updated = store.update_group_chat(
        ALICE, chat["id"], {"name": "新名", "archived": True, "left": True}
    )
    assert updated["name"] == "新名"
    assert updated["archived"] is True
    assert updated["left"] is True  # left 映射到 left_group 列
    assert store.update_group_chat(BOB, chat["id"], {"name": "x"}) is None
    assert store.update_group_chat(ALICE, chat["id"], {})["name"] == "新名"

    assert store.delete_group_chat(BOB, chat["id"]) is False
    assert store.delete_group_chat(ALICE, chat["id"]) is True
    assert store.get_group_chat(ALICE, chat["id"]) is None
    assert store.delete_group_chat(ALICE, chat["id"]) is False
    # Bot 本体不随群聊删除
    assert store.get_bot(ALICE, a["id"]) is not None


# --------------------------------------------------------------------------- #
# 贾维斯本人例程
# --------------------------------------------------------------------------- #


def test_jarvis_routines_per_owner_and_cleaning(store):
    assert store.get_jarvis_routines("alice") == []
    saved = store.set_jarvis_routines("alice", ["  晨报 ", "", "   ", "巡检"])
    assert saved == ["晨报", "巡检"]  # 去空白、丢空项
    assert store.get_jarvis_routines("alice") == ["晨报", "巡检"]
    assert store.get_jarvis_routines("bob") == []  # 按账号隔离

    store.set_jarvis_routines("alice", ["新例程"])  # 整体替换
    assert store.get_jarvis_routines("alice") == ["新例程"]

    capped = store.set_jarvis_routines("alice", [f"r{i}" for i in range(105)])
    assert len(capped) == 100  # 上限 100 条


# --------------------------------------------------------------------------- #
# 迁移边角（与 test_workbench_groups_backend 的幂等用例互补）
# --------------------------------------------------------------------------- #


def test_migrate_skips_jarvis_and_empty_ids(store):
    counts = store.migrate_snapshot(
        "alice",
        {
            "bots": [
                {"id": JARVIS_ID, "name": "贾维斯本人"},
                {"id": "", "name": "无 id"},
                {"name": "也没 id"},
                {"id": "real-1", "name": "真 Bot"},
            ],
            "groups": [{"id": "", "name": "无 id 组"}, {"id": "g1", "name": "组一"}],
            "group_chats": [{"id": "", "name": "无 id 群"}],
        },
    )
    assert counts == {"bots": 1, "groups": 1, "group_chats": 0}
    assert [b["id"] for b in store.list_bots(ALICE)] == ["real-1"]
    assert store.get_group(ALICE, "g1")["name"] == "组一"


def test_migrate_stale_snapshot_does_not_overwrite_newer(store):
    store.migrate_snapshot(
        "alice",
        {"bots": [{"id": "m1", "name": "新名字",
                   "updated_at": "2999-01-02T00:00:00+00:00"}]},
    )
    store.migrate_snapshot(
        "alice",
        {"bots": [{"id": "m1", "name": "旧名字",
                   "updated_at": "2999-01-01T00:00:00+00:00"}]},
    )
    assert store.get_bot(ALICE, "m1")["name"] == "新名字"


def test_migrate_partial_snapshot_preserves_existing_fields(store):
    bot = _bot(store, name="原名", job="会计", color="blue")
    # 快照只给 name（且时间更新）：其余已有字段不能被抹掉
    store.migrate_snapshot(
        "alice",
        {"bots": [{"id": bot["id"], "name": "改名",
                   "updated_at": "2999-01-01T00:00:00+00:00"}]},
    )
    merged = store.get_bot(ALICE, bot["id"])
    assert merged["name"] == "改名"
    assert merged["job"] == "会计"
    assert merged["color"] == "blue"


def test_migrate_group_chat_members_skip_jarvis(store):
    a = _bot(store)
    counts = store.migrate_snapshot(
        "alice",
        {"group_chats": [
            {"id": "chat-legacy", "name": "旧群",
             "member_bot_ids": [a["id"], JARVIS_ID, a["id"]]},
        ]},
    )
    assert counts["group_chats"] == 1
    chat = store.get_group_chat(ALICE, "chat-legacy")
    assert chat["member_bot_ids"] == [a["id"]]
