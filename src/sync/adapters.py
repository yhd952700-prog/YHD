"""同步适配器：把四类核心状态映射成引擎的实体模型。

- ``workbench_bots`` / ``workbench_groups``：工作台存储，id + updated_at
  天然支持 LWW；写入走存储的同步专用 upsert（保留对端时间戳）。
- ``conversations``：append-only 逐条合并（见引擎说明）。
- ``knowledge_docs``：走与备份导出/导入同一套知识服务接口
  （list_documents / document_chunks / ingest），实体 id 用去归属前缀的
  document_id；服务不可用（无 embedding）时整类降级为报错上报，不拖垮
  其它类同步。
- ``account_settings``：设置文件整体一个实体，版本戳 = 状态戳与文件
  mtime 的较新者。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import state as sync_state
from .engine import entity_sha, make_entity

KINDS = ("workbench_groups", "workbench_bots", "conversations", "knowledge_docs", "account_settings")


def _iso_from_epoch(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _bot_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload["avatar_url"] = ""  # 头像二进制不同步（与导出包口径一致）
    payload.pop("owner", None)
    return payload


def _group_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload.pop("owner", None)
    return payload


class SyncAdapter:
    def __init__(self, owner: str, *, workbench=None, conversations=None, knowledge_service=None):
        self.owner = owner
        self._workbench = workbench
        self._conversations = conversations
        self._knowledge_service = knowledge_service

    # -- 懒加载真实存储 -------------------------------------------------
    @property
    def workbench(self):
        if self._workbench is None:
            from ..workbench.store import WorkbenchStore

            self._workbench = WorkbenchStore()
        return self._workbench

    @property
    def conversations(self):
        if self._conversations is None:
            from ..ai.conversation_store import ConversationStore

            self._conversations = ConversationStore()
        return self._conversations

    def _knowledge(self):
        if self._knowledge_service is None:
            from ..knowledge.documents import get_knowledge_service

            self._knowledge_service = get_knowledge_service()
        return self._knowledge_service

    def _knowledge_scope(self):
        from ..knowledge.documents import KnowledgeScope

        return KnowledgeScope(
            principal=self.owner,
            owners=(self.owner,),
            include_unowned=True,
            owns_legacy=True,
            unowned_writes=True,
        )

    # -- collect ---------------------------------------------------------
    def collect(self, kind: str, since: str = "") -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        if kind == "workbench_bots":
            for row in self.workbench.list_bots([self.owner]):
                entities.append(make_entity(kind, row["id"], row.get("updated_at") or "", _bot_payload(row)))
        elif kind == "workbench_groups":
            for row in self.workbench.list_groups([self.owner]):
                entities.append(make_entity(kind, row["id"], row.get("updated_at") or "", _group_payload(row)))
        elif kind == "conversations":
            store = self.conversations
            for name in store.list_session_ids(self.owner, allow_legacy=True):
                # list_session_ids 返回会话名；实体 id 用完整存储键，两台
                # 设备才能对上同一会话（新键 liuhao-{owner}-{name}，
                # 旧键 liuhao-{name}，按有数据的那个为准）。
                for key in (f"liuhao-{self.owner}-{name}", f"liuhao-{name}"):
                    entity = self._session_entity(key)
                    if entity is not None:
                        entities.append(entity)
                        break
        elif kind == "knowledge_docs":
            entities = self._collect_knowledge()
        elif kind == "account_settings":
            entity = self._settings_entity()
            if entity is not None:
                entities.append(entity)
        else:
            return []

        for entity_id, deleted_at in sync_state.tombstones(kind).items():
            entities.append(make_entity(kind, entity_id, deleted_at, {}, deleted=True))
        if since:
            entities = [e for e in entities if str(e.get("updated_at") or "") > since]
        return entities

    def _session_entity(self, session_id: str) -> Optional[Dict[str, Any]]:
        turns = self.conversations.load_turns(session_id)
        if not turns:
            return None
        updated = _iso_from_epoch(max(float(t.get("created_at") or 0) for t in turns))
        payload = {
            "turns": [
                {
                    "turn": int(t.get("turn") or 0),
                    "role": str(t.get("role") or ""),
                    "content": str(t.get("content") or ""),
                    "created_at": float(t.get("created_at") or 0),
                }
                for t in turns
            ]
        }
        return make_entity("conversations", session_id, updated, payload)

    def _collect_knowledge(self) -> List[Dict[str, Any]]:
        service = self._knowledge()
        scope = self._knowledge_scope()
        from ..gateway.data_portability import _strip_owner_prefix

        entities: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = service.list_documents(limit=200, offset=offset, scope=scope)
            rows = page.get("documents") or []
            total = int(page.get("total") or 0)
            for row in rows:
                full_id = str(row.get("document_id") or "")
                if not full_id:
                    continue
                chunks = service.document_chunks(full_id, scope=scope).get("chunks") or []
                text = "\n\n".join(
                    str(c.get("text") or "") for c in chunks if str(c.get("text") or "").strip()
                )
                payload = {
                    "title": str(row.get("title") or ""),
                    "source": str(row.get("source") or ""),
                    "text": text,
                }
                entities.append(
                    make_entity(
                        "knowledge_docs",
                        _strip_owner_prefix(full_id, self.owner),
                        str(row.get("updated_at") or row.get("modified_at") or ""),
                        payload,
                    )
                )
            offset += len(rows)
            if not rows or offset >= total:
                break
        return entities

    def _settings_entity(self) -> Optional[Dict[str, Any]]:
        from ..gateway.account_settings import _settings_path, load_settings

        payload = load_settings()
        path = _settings_path()
        mtime_iso = ""
        try:
            mtime_iso = _iso_from_epoch(os.path.getmtime(path))
        except OSError:
            pass
        stamp = max(sync_state.load_state().get("settings_stamp") or "", mtime_iso)
        if not payload and not stamp:
            return None
        return make_entity("account_settings", "account_settings", stamp, payload)

    # -- get -------------------------------------------------------------
    def get(self, kind: str, entity_id: str) -> Optional[Dict[str, Any]]:
        if kind == "workbench_bots":
            row = self.workbench.get_bot([self.owner], entity_id)
            if row is None:
                return None
            return make_entity(kind, entity_id, row.get("updated_at") or "", _bot_payload(row))
        if kind == "workbench_groups":
            row = self.workbench.get_group([self.owner], entity_id)
            if row is None:
                return None
            return make_entity(kind, entity_id, row.get("updated_at") or "", _group_payload(row))
        if kind == "knowledge_docs":
            for entity in self._collect_knowledge():
                if entity["id"] == entity_id:
                    return entity
            return None
        if kind == "account_settings":
            entity = self._settings_entity()
            return entity if entity and entity["id"] == entity_id else None
        return None

    # -- put / delete ------------------------------------------------------
    def put(self, kind: str, entity: Dict[str, Any]) -> None:
        payload = dict(entity.get("payload") or {})
        if kind == "workbench_bots":
            payload["id"] = entity["id"]
            payload["updated_at"] = entity.get("updated_at") or payload.get("updated_at")
            self.workbench.upsert_bot_for_sync(self.owner, payload)
        elif kind == "workbench_groups":
            payload["id"] = entity["id"]
            payload["updated_at"] = entity.get("updated_at") or payload.get("updated_at")
            self.workbench.upsert_group_for_sync(self.owner, payload)
        elif kind == "knowledge_docs":
            service = self._knowledge()
            scope = self._knowledge_scope()
            service.ingest(
                str(payload.get("text") or ""),
                doc_id=entity["id"],
                metadata={
                    "source": str(payload.get("source") or "sync"),
                    "title": str(payload.get("title") or ""),
                    "imported_from": "liuhao.sync.v1",
                },
                owner_account_id=scope.write_owner,
            )
        elif kind == "account_settings":
            from ..gateway.account_settings import _settings_path

            path = _settings_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            stamp = str(entity.get("updated_at") or "")
            state = sync_state.load_state()
            state["settings_stamp"] = stamp
            sync_state.save_state(state)
            try:
                ts = datetime.fromisoformat(stamp).timestamp() if stamp else None
                if ts:
                    os.utime(path, (ts, ts))
            except (ValueError, OSError):
                pass

    def delete(self, kind: str, entity_id: str) -> None:
        if kind == "workbench_bots":
            self.workbench.delete_bot([self.owner], entity_id)
        elif kind == "workbench_groups":
            self.workbench.delete_group([self.owner], entity_id)
        elif kind == "knowledge_docs":
            service = self._knowledge()
            scope = self._knowledge_scope()
            local = self.get(kind, entity_id)
            if local is not None:
                # get 用的 id 是去前缀的；删除需要全 id——从列表里找回全 id。
                from ..gateway.data_portability import _strip_owner_prefix

                offset = 0
                while True:
                    page = service.list_documents(limit=200, offset=offset, scope=scope)
                    rows = page.get("documents") or []
                    total = int(page.get("total") or 0)
                    for row in rows:
                        full_id = str(row.get("document_id") or "")
                        if _strip_owner_prefix(full_id, self.owner) == entity_id:
                            service.delete_document(full_id, scope=scope)
                            return
                    offset += len(rows)
                    if not rows or offset >= total:
                        break
        elif kind == "account_settings":
            from ..gateway.account_settings import _settings_path

            try:
                os.remove(_settings_path())
            except OSError:
                pass

    # -- conversations: append-only ---------------------------------------
    def merge_append_only(self, kind: str, entity: Dict[str, Any]):
        """逐条合并聊天记录：缺的补上，同（turn, role）内容不同只留痕。"""
        session_id = str(entity.get("id") or "")
        store = self.conversations
        local_turns = {}
        try:
            for turn in store.load_turns(session_id):
                local_turns[(int(turn.get("turn") or 0), str(turn.get("role") or ""))] = str(
                    turn.get("content") or ""
                )
        except Exception:  # noqa: BLE001 - 新会话读不到按空处理
            local_turns = {}
        conflicts: List[Dict[str, Any]] = []
        appended = 0
        for turn in (entity.get("payload") or {}).get("turns") or []:
            key = (int(turn.get("turn") or 0), str(turn.get("role") or ""))
            content = str(turn.get("content") or "")
            if key not in local_turns:
                store.append(session_id, key[0], key[1], content, owner_principal=self.owner)
                appended += 1
            elif local_turns[key] != content:
                conflicts.append(
                    {
                        "kind": kind,
                        "id": session_id,
                        "reason": "append_only_turn_differs",
                        "kept": "local",
                        "turn": key[0],
                        "role": key[1],
                        "local_sha256": entity_sha(local_turns[key]),
                        "remote_sha256": entity_sha(content),
                    }
                )
        outcome = "applied" if appended else ("kept" if conflicts else "identical")
        return outcome, conflicts
