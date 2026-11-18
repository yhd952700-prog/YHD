"""工作台 Bot / 分组 / 群聊存储（SQLite）。

工作台的 Bot、分组、群聊、置顶以前只活在浏览器 localStorage 里：单机
刷新还在，换设备就没了，备份带不走，子账号和多设备也对不上。本模块
把它们落到后端数据库，和协作空间（``src/spaces/store.py``）同一套
模式：按账号隔离，Owner（root）看到整棵子树，成员只看到自己的，
看不见的当不存在（接口返回 404，不区分“没有”和“无权”）。

表：
- ``bots``：Bot 本体（名字/职责/性格/配色/模型/置顶/隐藏/所属分组/例程）。
- ``bot_groups``：分组（名字/是否折叠/排序）。
- ``group_chats``：群聊（名字/是否归档/是否已退群）。
- ``group_chat_members``：群聊成员（Bot id 集合；贾维斯是隐式成员，不存表）。

``jarvis`` 是贾维斯本人的固定 id，不是 Bot 行：建 Bot、迁移时跳过它。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.common.data_paths import resolve_store_path

#: 贾维斯本人的固定 id：不是 Bot，不进 bots 表。
JARVIS_ID = "jarvis"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    job TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',
    idea TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    gender TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    accessory TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    group_id TEXT NOT NULL DEFAULT '',
    routines TEXT NOT NULL DEFAULT '[]',
    order_index INTEGER NOT NULL DEFAULT 0,
    avatar_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner);
CREATE TABLE IF NOT EXISTS bot_groups (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    folded INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_groups_owner ON bot_groups(owner);
CREATE TABLE IF NOT EXISTS group_chats (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    left_group INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_chats_owner ON group_chats(owner);
CREATE TABLE IF NOT EXISTS group_chat_members (
    chat_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, bot_id)
);
CREATE INDEX IF NOT EXISTS idx_group_chat_members_chat ON group_chat_members(chat_id);
CREATE TABLE IF NOT EXISTS jarvis_routines (
    owner TEXT PRIMARY KEY,
    routines TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    return resolve_store_path(
        "workbench.db",
        Path(__file__).resolve().parents[2] / "data" / "workbench.db",
        env_var="LIUHAO_WORKBENCH_DB",
    )


def _bot_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    try:
        routines = json.loads(data.get("routines") or "[]")
    except (ValueError, TypeError):
        routines = []
    data["routines"] = routines if isinstance(routines, list) else []
    data["pinned"] = bool(data["pinned"])
    data["hidden"] = bool(data["hidden"])
    # 老库（本轮之前建的）没有 avatar_url 列时，行里也没有这个键。
    data["avatar_url"] = str(data.get("avatar_url") or "")
    return data


#: 头像图片上限：2MB。头像是小图，超过这个多半是误传了原图/照片。
MAX_AVATAR_BYTES = 2 * 1024 * 1024

#: 允许的头像格式：扩展名 → （文件头魔数，媒体类型）。
#: 只认文件头，不认客户端报的 Content-Type / 文件名后缀——后缀能随便改，
#: 魔数改不了。
AVATAR_FORMATS = {
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "gif": (None, "image/gif"),  # GIF87a / GIF89a 两个版本，单独判
    "webp": (None, "image/webp"),  # RIFF....WEBP，单独判
}


def validate_avatar_bytes(data: bytes) -> str:
    """校验头像图片字节，返回扩展名（png/jpg/gif/webp）。

    不合规抛 ValueError（人话原因，网关原样返回 422）：
    空文件、超过 2MB、魔数对不上已知的四种图片格式。
    不依赖 Pillow——网关的依赖里没有它，魔数校验已够拦住错传的文件。
    """
    if not data:
        raise ValueError("这张图片是空的：请重新选一张图片。")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("图片太大了：头像最多 2MB，请换张小一点的图片。")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("这张图片打不开：头像只支持 PNG、JPG、GIF、WebP 四种格式。")


def avatar_media_type(extension: str) -> str:
    """头像扩展名 → 媒体类型（GET 头像时回给浏览器）。"""
    fmt = AVATAR_FORMATS.get(extension)
    return fmt[1] if fmt else "application/octet-stream"


def _safe_avatar_stem(bot_id: str) -> str:
    """Bot id → 头像文件名主干：只留字母数字、下划线、横线，其余变横线。

    Bot id 可能来自浏览器本地迁移的旧数据，不能直接拼进文件路径。
    """
    stem = "".join(ch if ch.isalnum() or ch in ("_", "-") else "-" for ch in str(bot_id))
    return stem.strip("-") or "bot"


def _group_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["folded"] = bool(data["folded"])
    return data


def _chat_row(row: sqlite3.Row, member_ids: Sequence[str]) -> Dict[str, Any]:
    data = dict(row)
    data["archived"] = bool(data["archived"])
    data["left"] = bool(data["left_group"])
    del data["left_group"]
    data["member_bot_ids"] = list(member_ids)
    return data


class WorkbenchStore:
    """工作台 Bot / 分组 / 群聊的 SQLite 存储。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            # 老库升级：本轮之前建的 bots 表没有 avatar_url 列，补上；
            # 已有行默认空串（没有传过图），不丢任何已有数据。
            columns = [row[1] for row in conn.execute("PRAGMA table_info(bots)")]
            if "avatar_url" not in columns:
                conn.execute("ALTER TABLE bots ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def avatar_dir(self) -> Path:
        """头像文件目录：跟 workbench.db 同一个数据目录下的 avatars/。

        备份走数据目录整份备份（auto_backup 的 USER_DATA 就是整个数据
        目录），头像文件跟着备份走，不会只剩数据库里的链接。
        """
        return Path(self._db_path).parent / "avatars"

    def find_bot_avatar_file(self, bot_id: str) -> Optional[Path]:
        """该 Bot 已存的头像文件（有就返回，没有返回 None）。"""
        stem = _safe_avatar_stem(bot_id)
        directory = self.avatar_dir()
        if not directory.is_dir():
            return None
        for extension in AVATAR_FORMATS:
            candidate = directory / f"{stem}.{extension}"
            if candidate.is_file():
                return candidate
        return None

    # -- Bot ---------------------------------------------------------

    def list_bots(self, principals: Sequence[str]) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bots WHERE owner IN (%s)"
                " ORDER BY pinned DESC, order_index ASC, created_at ASC"
                % ",".join("?" * len(principals)),
                list(principals),
            ).fetchall()
        return [_bot_row(row) for row in rows]

    def get_bot(
        self, principals: Sequence[str], bot_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bots WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [bot_id, *principals],
            ).fetchone()
        return _bot_row(row) if row is not None else None

    def create_bot(self, owner: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_iso()
        bot_id = str(fields.get("id") or f"bot-{uuid.uuid4().hex[:12]}")
        if bot_id == JARVIS_ID:
            bot_id = f"bot-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO bots (id, owner, name, job, personality, idea,"
                " direction, mood, gender, color, accessory, model_id,"
                " pinned, hidden, group_id, routines, order_index,"
                " avatar_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bot_id,
                    owner,
                    str(fields.get("name") or ""),
                    str(fields.get("job") or ""),
                    str(fields.get("personality") or ""),
                    str(fields.get("idea") or ""),
                    str(fields.get("direction") or ""),
                    str(fields.get("mood") or ""),
                    str(fields.get("gender") or ""),
                    str(fields.get("color") or ""),
                    str(fields.get("accessory") or ""),
                    str(fields.get("model_id") or ""),
                    1 if fields.get("pinned") else 0,
                    1 if fields.get("hidden") else 0,
                    str(fields.get("group_id") or ""),
                    json.dumps(list(fields.get("routines") or []), ensure_ascii=False),
                    int(fields.get("order_index") or 0),
                    "",
                    str(fields.get("created_at") or now),
                    str(fields.get("updated_at") or now),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot_row(row)

    def update_bot(
        self,
        principals: Sequence[str],
        bot_id: str,
        patch: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "name", "job", "personality", "idea", "direction", "mood",
            "gender", "color", "accessory", "model_id", "group_id",
        }
        sets: List[str] = []
        values: List[Any] = []
        for key in allowed:
            if key in patch and patch[key] is not None:
                sets.append(f"{key} = ?")
                values.append(str(patch[key]))
        for key in ("pinned", "hidden"):
            if key in patch and patch[key] is not None:
                sets.append(f"{key} = ?")
                values.append(1 if patch[key] else 0)
        if "routines" in patch and patch["routines"] is not None:
            sets.append("routines = ?")
            values.append(
                json.dumps(list(patch["routines"]), ensure_ascii=False)
            )
        if "order_index" in patch and patch["order_index"] is not None:
            sets.append("order_index = ?")
            values.append(int(patch["order_index"]))
        if not sets:
            return self.get_bot(principals, bot_id)
        sets.append("updated_at = ?")
        values.append(_now_iso())
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE bots SET %s WHERE id = ? AND owner IN (%s)"
                % (", ".join(sets), ",".join("?" * len(principals))),
                [*values, bot_id, *principals],
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot_row(row)

    def delete_bot(self, principals: Sequence[str], bot_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM bots WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [bot_id, *principals],
            )
            conn.execute("DELETE FROM group_chat_members WHERE bot_id = ?", (bot_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            # Bot 删了，头像文件一起删，不留孤儿文件占地方。
            try:
                avatar = self.find_bot_avatar_file(bot_id)
                if avatar is not None:
                    avatar.unlink()
            except OSError:
                pass
        return deleted

    def set_bot_avatar(
        self, principals: Sequence[str], bot_id: str
    ) -> Optional[Dict[str, Any]]:
        """头像文件已落盘后调用：把 avatar_url 指到稳定的头像接口。

        avatar_url 只由服务端写（上传接口），创建/修改 Bot 的接口不接受
        这个字段——不然谁都能往 Bot 行里写任意图片链接。
        """
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE bots SET avatar_url = ?, updated_at = ?"
                " WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [f"/v1/workbench/bots/{bot_id}/avatar", _now_iso(), bot_id, *principals],
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot_row(row)

    def clear_bot_avatar(
        self, principals: Sequence[str], bot_id: str
    ) -> Optional[Dict[str, Any]]:
        """删头像：文件删掉，avatar_url 清空，Bot 回到配色+饰件头像。

        先验权再删文件：之前先删文件后验权，成员对着根账号的 Bot 调
        删除会返回 404、却已经把根账号的头像文件删掉了。
        """
        if self.get_bot(principals, bot_id) is None:
            return None
        try:
            avatar = self.find_bot_avatar_file(bot_id)
            if avatar is not None:
                avatar.unlink()
        except OSError:
            pass
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE bots SET avatar_url = '', updated_at = ?"
                " WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [_now_iso(), bot_id, *principals],
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot_row(row)

    def reorder_bots(self, principals: Sequence[str], ordered_ids: List[str]) -> None:
        with self._lock, self._connect() as conn:
            for index, bot_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE bots SET order_index = ?, updated_at = ?"
                    " WHERE id = ? AND owner IN (%s)"
                    % ",".join("?" * len(principals)),
                    [index, _now_iso(), bot_id, *principals],
                )
            conn.commit()

    # -- 分组 --------------------------------------------------------

    def list_groups(self, principals: Sequence[str]) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bot_groups WHERE owner IN (%s)"
                " ORDER BY order_index ASC, created_at ASC"
                % ",".join("?" * len(principals)),
                list(principals),
            ).fetchall()
        return [_group_row(row) for row in rows]

    def get_group(
        self, principals: Sequence[str], group_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bot_groups WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [group_id, *principals],
            ).fetchone()
        return _group_row(row) if row is not None else None

    def create_group(self, owner: str, name: str) -> Dict[str, Any]:
        now = _now_iso()
        group_id = f"group-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO bot_groups (id, owner, name, folded, order_index,"
                " created_at, updated_at) VALUES (?, ?, ?, 0, 0, ?, ?)",
                (group_id, owner, name, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bot_groups WHERE id = ?", (group_id,)
            ).fetchone()
        return _group_row(row)

    def update_group(
        self,
        principals: Sequence[str],
        group_id: str,
        patch: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        sets: List[str] = []
        values: List[Any] = []
        if patch.get("name") is not None:
            sets.append("name = ?")
            values.append(str(patch["name"]))
        if patch.get("folded") is not None:
            sets.append("folded = ?")
            values.append(1 if patch["folded"] else 0)
        if patch.get("order_index") is not None:
            sets.append("order_index = ?")
            values.append(int(patch["order_index"]))
        if not sets:
            return self.get_group(principals, group_id)
        sets.append("updated_at = ?")
        values.append(_now_iso())
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE bot_groups SET %s WHERE id = ? AND owner IN (%s)"
                % (", ".join(sets), ",".join("?" * len(principals))),
                [*values, group_id, *principals],
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM bot_groups WHERE id = ?", (group_id,)
            ).fetchone()
        return _group_row(row)

    # -- U5 同步专用：整行 upsert，保留对端的时间戳（LWW 合并的前提） -------
    def upsert_bot_for_sync(self, owner: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """按 id 整行写入 Bot（存在则覆盖），created_at/updated_at 用行内值。

        只供多端同步引擎调用：普通编辑路径仍走 create/update（它们负责把
        updated_at  bump 到当前时间）。头像二进制不走同步（avatar_url 置空，
        与导出包口径一致）。
        """
        bot_id = str(row.get("id") or "")
        if not bot_id or bot_id == JARVIS_ID:
            raise ValueError("同步 Bot 缺少合法 id")
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO bots (id, owner, name, job, personality, idea,"
                " direction, mood, gender, color, accessory, model_id,"
                " pinned, hidden, group_id, routines, order_index,"
                " avatar_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,"
                " name=excluded.name, job=excluded.job, personality=excluded.personality,"
                " idea=excluded.idea, direction=excluded.direction, mood=excluded.mood,"
                " gender=excluded.gender, color=excluded.color,"
                " accessory=excluded.accessory, model_id=excluded.model_id,"
                " pinned=excluded.pinned, hidden=excluded.hidden,"
                " group_id=excluded.group_id, routines=excluded.routines,"
                " order_index=excluded.order_index, updated_at=excluded.updated_at",
                (
                    bot_id,
                    owner,
                    str(row.get("name") or ""),
                    str(row.get("job") or ""),
                    str(row.get("personality") or ""),
                    str(row.get("idea") or ""),
                    str(row.get("direction") or ""),
                    str(row.get("mood") or ""),
                    str(row.get("gender") or ""),
                    str(row.get("color") or ""),
                    str(row.get("accessory") or ""),
                    str(row.get("model_id") or ""),
                    1 if row.get("pinned") else 0,
                    1 if row.get("hidden") else 0,
                    str(row.get("group_id") or ""),
                    json.dumps(list(row.get("routines") or []), ensure_ascii=False),
                    int(row.get("order_index") or 0),
                    "",
                    str(row.get("created_at") or now),
                    str(row.get("updated_at") or now),
                ),
            )
            conn.commit()
            saved = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot_row(saved)

    def upsert_group_for_sync(self, owner: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """按 id 整行写入分组（存在则覆盖），时间戳用行内值。仅供同步引擎。"""
        group_id = str(row.get("id") or "")
        if not group_id:
            raise ValueError("同步分组缺少 id")
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO bot_groups (id, owner, name, folded, order_index,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,"
                " name=excluded.name, folded=excluded.folded,"
                " order_index=excluded.order_index, updated_at=excluded.updated_at",
                (
                    group_id,
                    owner,
                    str(row.get("name") or ""),
                    1 if row.get("folded") else 0,
                    int(row.get("order_index") or 0),
                    str(row.get("created_at") or now),
                    str(row.get("updated_at") or now),
                ),
            )
            conn.commit()
            saved = conn.execute(
                "SELECT * FROM bot_groups WHERE id = ?", (group_id,)
            ).fetchone()
        return _group_row(saved)

    def delete_group(self, principals: Sequence[str], group_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM bot_groups WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [group_id, *principals],
            )
            if cursor.rowcount > 0:
                # 组里的 Bot 回到未分组，不删 Bot。
                conn.execute(
                    "UPDATE bots SET group_id = '', updated_at = ?"
                    " WHERE group_id = ? AND owner IN (%s)"
                    % ",".join("?" * len(principals)),
                    [_now_iso(), group_id, *principals],
                )
            conn.commit()
            return cursor.rowcount > 0

    def reorder_groups(
        self, principals: Sequence[str], ordered_ids: List[str]
    ) -> None:
        with self._lock, self._connect() as conn:
            for index, group_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE bot_groups SET order_index = ?, updated_at = ?"
                    " WHERE id = ? AND owner IN (%s)"
                    % ",".join("?" * len(principals)),
                    [index, _now_iso(), group_id, *principals],
                )
            conn.commit()

    # -- 群聊 --------------------------------------------------------

    def list_group_chats(
        self, principals: Sequence[str]
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM group_chats WHERE owner IN (%s)"
                " ORDER BY created_at ASC" % ",".join("?" * len(principals)),
                list(principals),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                members = conn.execute(
                    "SELECT bot_id FROM group_chat_members WHERE chat_id = ?"
                    " ORDER BY joined_at ASC",
                    (row["id"],),
                ).fetchall()
                out.append(
                    _chat_row(row, [member["bot_id"] for member in members])
                )
        return out

    def get_group_chat(
        self, principals: Sequence[str], chat_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM group_chats WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [chat_id, *principals],
            ).fetchone()
            if row is None:
                return None
            members = conn.execute(
                "SELECT bot_id FROM group_chat_members WHERE chat_id = ?"
                " ORDER BY joined_at ASC",
                (chat_id,),
            ).fetchall()
        return _chat_row(row, [member["bot_id"] for member in members])

    def create_group_chat(
        self,
        owner: str,
        name: str,
        member_bot_ids: Sequence[str],
    ) -> Dict[str, Any]:
        now = _now_iso()
        chat_id = f"chat-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO group_chats (id, owner, name, archived, left_group,"
                " created_at, updated_at) VALUES (?, ?, ?, 0, 0, ?, ?)",
                (chat_id, owner, name, now, now),
            )
            for bot_id in dict.fromkeys(member_bot_ids):
                if bot_id == JARVIS_ID:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO group_chat_members"
                    " (chat_id, bot_id, joined_at) VALUES (?, ?, ?)",
                    (chat_id, bot_id, now),
                )
            conn.commit()
        result = self.get_group_chat([owner], chat_id)
        assert result is not None  # 刚创建的群聊一定能读到
        return result

    def update_group_chat(
        self,
        principals: Sequence[str],
        chat_id: str,
        patch: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        sets: List[str] = []
        values: List[Any] = []
        if patch.get("name") is not None:
            sets.append("name = ?")
            values.append(str(patch["name"]))
        if patch.get("archived") is not None:
            sets.append("archived = ?")
            values.append(1 if patch["archived"] else 0)
        if patch.get("left") is not None:
            sets.append("left_group = ?")
            values.append(1 if patch["left"] else 0)
        if not sets:
            return self.get_group_chat(principals, chat_id)
        sets.append("updated_at = ?")
        values.append(_now_iso())
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE group_chats SET %s WHERE id = ? AND owner IN (%s)"
                % (", ".join(sets), ",".join("?" * len(principals))),
                [*values, chat_id, *principals],
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_group_chat(principals, chat_id)

    def add_group_chat_member(
        self, principals: Sequence[str], chat_id: str, bot_id: str
    ) -> Optional[Dict[str, Any]]:
        if bot_id == JARVIS_ID:
            return self.get_group_chat(principals, chat_id)
        existing = self.get_group_chat(principals, chat_id)
        if existing is None:
            return None
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO group_chat_members"
                " (chat_id, bot_id, joined_at) VALUES (?, ?, ?)",
                (chat_id, bot_id, _now_iso()),
            )
            conn.commit()
        return self.get_group_chat(principals, chat_id)

    def remove_group_chat_member(
        self, principals: Sequence[str], chat_id: str, bot_id: str
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_group_chat(principals, chat_id)
        if existing is None:
            return None
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM group_chat_members WHERE chat_id = ? AND bot_id = ?",
                (chat_id, bot_id),
            )
            conn.commit()
        return self.get_group_chat(principals, chat_id)

    def delete_group_chat(self, principals: Sequence[str], chat_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM group_chats WHERE id = ? AND owner IN (%s)"
                % ",".join("?" * len(principals)),
                [chat_id, *principals],
            )
            conn.execute(
                "DELETE FROM group_chat_members WHERE chat_id = ?", (chat_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    # -- 贾维斯本人教过的例程：跟 Bot 的 routines 同一个东西，只是
    #    贾维斯不是 Bot 行，单开一张表按账号存（教一次变按钮，下次点一下就行）。
    def get_jarvis_routines(self, owner: str) -> List[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT routines FROM jarvis_routines WHERE owner = ?", (owner,)
            ).fetchone()
        if row is None:
            return []
        try:
            routines = json.loads(row["routines"] or "[]")
        except (ValueError, TypeError):
            return []
        return [str(r) for r in routines if isinstance(r, str) and str(r).strip()]

    def set_jarvis_routines(self, owner: str, routines: Sequence[str]) -> List[str]:
        cleaned = [str(r).strip() for r in routines if str(r).strip()][:100]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jarvis_routines (owner, routines, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(owner) DO UPDATE SET routines = excluded.routines,"
                " updated_at = excluded.updated_at",
                (owner, json.dumps(cleaned, ensure_ascii=False), _now_iso()),
            )
            conn.commit()
        return cleaned

    # -- 迁移：把浏览器里已有的本地数据一次性并进后端 ----------------

    def migrate_snapshot(
        self, owner: str, snapshot: Dict[str, Any]
    ) -> Dict[str, int]:
        """按 id 幂等并入本地快照；同 id 时 updated_at 新的赢。

        返回各表并入的条数。``jarvis`` 本人跳过；不存在的分组 id 引用
        在 Bot 行里保留原样（前端按“未分组”展示，不会因此丢 Bot）。
        """
        counts = {"bots": 0, "groups": 0, "group_chats": 0}
        now = _now_iso()
        with self._lock, self._connect() as conn:
            for raw in snapshot.get("groups") or []:
                group_id = str(raw.get("id") or "")
                if not group_id:
                    continue
                updated_at = str(raw.get("updated_at") or now)
                # 快照缺字段时不抹掉已有值：只覆盖快照里真的给出的字段，
                # 其余沿用已有行（旧版/手写快照常只给 id+name）。
                _existing = conn.execute(
                    "SELECT * FROM bot_groups WHERE id = ?", (group_id,)
                ).fetchone()
                merged = dict(_existing) if _existing is not None else {}
                for _key in ("name", "folded", "order_index", "created_at"):
                    if _key in raw:
                        merged[_key] = raw[_key]
                conn.execute(
                    "INSERT INTO bot_groups (id, owner, name, folded,"
                    " order_index, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                    " folded = excluded.folded, order_index = excluded.order_index,"
                    " updated_at = excluded.updated_at"
                    " WHERE excluded.updated_at >= bot_groups.updated_at",
                    (
                        group_id,
                        owner,
                        str(merged.get("name") or "未命名分组"),
                        1 if merged.get("folded") else 0,
                        int(merged.get("order_index") or 0),
                        str(merged.get("created_at") or now),
                        updated_at,
                    ),
                )
                counts["groups"] += 1
            for raw in snapshot.get("bots") or []:
                bot_id = str(raw.get("id") or "")
                if not bot_id or bot_id == JARVIS_ID:
                    continue
                updated_at = str(raw.get("updated_at") or now)
                # 快照缺字段时不抹掉已有值（见分组循环的注释）。
                _existing = conn.execute(
                    "SELECT * FROM bots WHERE id = ?", (bot_id,)
                ).fetchone()
                merged = _bot_row(_existing) if _existing is not None else {}
                for _key in (
                    "name", "job", "personality", "idea", "direction", "mood",
                    "gender", "color", "accessory", "model_id", "pinned",
                    "hidden", "group_id", "routines", "order_index", "avatar_url",
                    "created_at",
                ):
                    if _key in raw:
                        merged[_key] = raw[_key]
                conn.execute(
                    "INSERT INTO bots (id, owner, name, job, personality, idea,"
                    " direction, mood, gender, color, accessory, model_id,"
                    " pinned, hidden, group_id, routines, order_index,"
                    " avatar_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                    " job = excluded.job, personality = excluded.personality,"
                    " idea = excluded.idea, direction = excluded.direction,"
                    " mood = excluded.mood, gender = excluded.gender,"
                    " color = excluded.color, accessory = excluded.accessory,"
                    " model_id = excluded.model_id, pinned = excluded.pinned,"
                    " hidden = excluded.hidden, group_id = excluded.group_id,"
                    " routines = excluded.routines,"
                    " order_index = excluded.order_index,"
                    " avatar_url = excluded.avatar_url,"
                    " updated_at = excluded.updated_at"
                    " WHERE excluded.updated_at >= bots.updated_at",
                    (
                        bot_id,
                        owner,
                        str(merged.get("name") or "未命名 Bot"),
                        str(merged.get("job") or ""),
                        str(merged.get("personality") or ""),
                        str(merged.get("idea") or ""),
                        str(merged.get("direction") or ""),
                        str(merged.get("mood") or ""),
                        str(merged.get("gender") or ""),
                        str(merged.get("color") or ""),
                        str(merged.get("accessory") or ""),
                        str(merged.get("model_id") or ""),
                        1 if merged.get("pinned") else 0,
                        1 if merged.get("hidden") else 0,
                        str(merged.get("group_id") or ""),
                        json.dumps(
                            list(merged.get("routines") or []), ensure_ascii=False
                        ),
                        int(merged.get("order_index") or 0),
                        str(merged.get("avatar_url") or ""),
                        str(merged.get("created_at") or now),
                        updated_at,
                    ),
                )
                counts["bots"] += 1
            for raw in snapshot.get("group_chats") or []:
                chat_id = str(raw.get("id") or "")
                if not chat_id:
                    continue
                updated_at = str(raw.get("updated_at") or now)
                # 快照缺字段时不抹掉已有值（见分组循环的注释）。
                _existing = conn.execute(
                    "SELECT * FROM group_chats WHERE id = ?", (chat_id,)
                ).fetchone()
                merged = dict(_existing) if _existing is not None else {}
                merged["left"] = bool(merged.get("left_group"))
                for _key in ("name", "archived", "left", "created_at"):
                    if _key in raw:
                        merged[_key] = raw[_key]
                conn.execute(
                    "INSERT INTO group_chats (id, owner, name, archived,"
                    " left_group, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                    " archived = excluded.archived,"
                    " left_group = excluded.left_group,"
                    " updated_at = excluded.updated_at"
                    " WHERE excluded.updated_at >= group_chats.updated_at",
                    (
                        chat_id,
                        owner,
                        str(merged.get("name") or "未命名群聊"),
                        1 if merged.get("archived") else 0,
                        1 if merged.get("left") else 0,
                        str(merged.get("created_at") or now),
                        updated_at,
                    ),
                )
                for bot_id in dict.fromkeys(raw.get("member_bot_ids") or []):
                    if str(bot_id) == JARVIS_ID:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO group_chat_members"
                        " (chat_id, bot_id, joined_at) VALUES (?, ?, ?)",
                        (chat_id, str(bot_id), now),
                    )
                counts["group_chats"] += 1
            conn.commit()
        return counts


_store: Optional[WorkbenchStore] = None
_store_lock = threading.Lock()


def get_workbench_store() -> WorkbenchStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = WorkbenchStore()
        return _store


def _reset_workbench_store() -> None:
    """测试用：丢弃进程内单例，下次按当前环境变量重建。"""
    global _store
    with _store_lock:
        _store = None
