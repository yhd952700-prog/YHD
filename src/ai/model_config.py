"""模型配置中心：持久化 + 密钥加密 + 运行时热加载。

为什么需要它
------------
``src.ai.providers.get_provider()`` 是进程级单例：provider 类型/模型名/密钥全部
来自 ``AI_PROVIDER_*``（``os.environ`` 或 ``.env``），改一次配置必须重启后端。
产品侧要求"在界面上添加模型，保存后立即生效"，这需要一份**可写、可读、免重启**
的配置源，而 ``.env`` 既不是数据库也不该被进程运行时改写。

本模块提供最小可用的模型配置存储：

* **SQLite 落盘**（复用 ``ConversationStore`` 同款约定：stdlib ``sqlite3`` + WAL +
  ``CREATE TABLE IF NOT EXISTS`` 就地建表，不需要额外迁移框架）；
* **密钥加密后落盘**：AES-256-GCM（``src.security.encryption.AesGcmCipher``），
  AAD 绑定配置 id，密文与行一一对应；接口层只返回脱敏串；
* **不改 ``.env``、不动仓库文件**：``.env`` 继续是初始默认值与兜底，注册表为空时
  ``get_provider()`` 的行为与之前完全一致（向后兼容）。

密钥来源（按优先级）：

1. ``LIUHAO_MODEL_CONFIG_KEY``：任意长度口令，内部用 SHA-256 派生 32 字节密钥；
2. ``LIUHAO_MODEL_CONFIG_KEY_FILE`` 指向的密钥文件（默认 ``data/model_config.key``）：
   首次保存密钥时自动生成，权限 0600；文件不存在且需要加密时创建。

没有密钥就无法加密，此时**拒绝保存带密钥的配置**（fail-closed），而不是把
明文写进数据库或静默丢弃密钥。

路径：

* ``LIUHAO_MODEL_CONFIG_DB``（默认 ``data/model_registry.sqlite3``，已被 ``data/``
  忽略规则覆盖）
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("liuhao.ai.model_config")

MODEL_CONFIG_DB_ENV = "LIUHAO_MODEL_CONFIG_DB"
MODEL_CONFIG_KEY_ENV = "LIUHAO_MODEL_CONFIG_KEY"
MODEL_CONFIG_KEY_FILE_ENV = "LIUHAO_MODEL_CONFIG_KEY_FILE"

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "model_registry.sqlite3"
DEFAULT_KEY_PATH = _REPO_ROOT / "data" / "model_config.key"

#: 支持的 provider 类型。与 ``ProviderFactory._providers`` 保持一致 —— 这里是
#: 写入口的校验名单，那里是实例化名单；两处必须同源，新增 provider 时同步。
SUPPORTED_PROVIDERS = (
    "mock",
    "openai",
    "anthropic",
    "google",
    "ollama",
    "moonshot",
    "deepseek",
)

#: 不需要 API Key 的类型（本地/模拟）。
PROVIDERS_WITHOUT_KEY = ("mock", "ollama")

_ENC_PREFIX = "enc:v1:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_model_configs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    base_url      TEXT,
    api_key_enc   TEXT,
    timeout       REAL NOT NULL DEFAULT 30.0,
    is_default    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


class ModelConfigError(Exception):
    """配置层错误：带 HTTP 状态码与可直接展示的中文原因。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask_secret(secret: Optional[str]) -> Optional[str]:
    """只留首尾各 4 位，其余打码。短串一律 ``****``。"""
    if not secret:
        return None
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}"


def _derive_key(passphrase: str) -> bytes:
    return hashlib.sha256(passphrase.encode("utf-8")).digest()


class ModelConfigStore:
    """模型配置的 SQLite 存储（单文件、线程安全、缺失时零配置）。"""

    def __init__(self, db_path: Optional[str] = None, key_path: Optional[str] = None) -> None:
        self._db_path = str(
            db_path or os.environ.get(MODEL_CONFIG_DB_ENV) or DEFAULT_DB_PATH
        )
        self._key_path = str(
            key_path
            or os.environ.get(MODEL_CONFIG_KEY_FILE_ENV)
            or DEFAULT_KEY_PATH
        )
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._cipher: Any = None

    # ------------------------------------------------------------------ #
    # 连接与加密
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                parent = os.path.dirname(os.path.abspath(self._db_path))
                os.makedirs(parent, exist_ok=True)
                self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            return self._conn

    def _encryption_key(self) -> bytes:
        """解析/生成 32 字节密钥；无法获得密钥时抛 ``ModelConfigError``。"""
        configured = os.environ.get(MODEL_CONFIG_KEY_ENV)
        if configured:
            return _derive_key(configured)
        path = Path(self._key_path)
        if path.exists():
            raw = path.read_bytes().strip()
            if len(raw) == 32:
                return raw
            # 兼容"文件里存的是口令"的写法。
            return _derive_key(raw.decode("utf-8", "ignore"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = os.urandom(32)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
            logger.info("model config encryption key generated at %s", path)
            return key
        except Exception as exc:  # pragma: no cover - 文件系统故障
            raise ModelConfigError(
                503,
                "无法创建模型密钥文件（缺少加密密钥，拒绝保存明文密钥）："
                f"{type(exc).__name__}",
            ) from exc

    def _get_cipher(self):
        with self._lock:
            if self._cipher is None:
                from src.security.encryption import AesGcmCipher

                self._cipher = AesGcmCipher(self._encryption_key())
            return self._cipher

    def _encrypt_secret(self, model_id: str, secret: str) -> str:
        cipher = self._get_cipher()
        encrypted = cipher.encrypt(
            secret.encode("utf-8"), associated_data=f"ai-model:{model_id}".encode()
        )
        nonce = base64.b64encode(encrypted.nonce).decode()
        body = base64.b64encode(encrypted.ciphertext).decode()
        return f"{_ENC_PREFIX}{nonce}:{body}"

    def _decrypt_secret(self, model_id: str, stored: Optional[str]) -> Optional[str]:
        if not stored:
            return None
        if not stored.startswith(_ENC_PREFIX):
            # 只可能来自手工改库；不猜测、不当作明文使用。
            raise ModelConfigError(
                500, f"模型 {model_id} 的密钥字段格式不合法（拒绝按明文使用）"
            )
        from src.security.encryption import EncryptedData

        payload = stored[len(_ENC_PREFIX):]
        nonce_b64, _, body_b64 = payload.partition(":")
        try:
            nonce = base64.b64decode(nonce_b64)
            ciphertext = base64.b64decode(body_b64)
        except Exception as exc:
            raise ModelConfigError(500, f"模型 {model_id} 的密钥密文损坏") from exc
        cipher = self._get_cipher()
        return cipher.decrypt(
            EncryptedData(
                ciphertext=ciphertext,
                nonce=nonce,
                algorithm="AES-256-GCM",
                key_id="model-config",
                associated_data=f"ai-model:{model_id}".encode(),
            )
        ).decode("utf-8")

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    def _row_to_public(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "model": row["model"],
            "base_url": row["base_url"],
            "timeout": row["timeout"],
            "is_default": bool(row["is_default"]),
            "enabled": bool(row["enabled"]),
            "api_key_set": bool(row["api_key_enc"]),
            "api_key_masked": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _masked_from_cipher(store: "ModelConfigStore", row: sqlite3.Row) -> Optional[str]:
        try:
            plain = store._decrypt_secret(row["id"], row["api_key_enc"])
        except Exception:
            return "（密文无法解密）"
        return mask_secret(plain)

    def list_models(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not Path(self._db_path).exists():
                return []
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM ai_model_configs ORDER BY is_default DESC, created_at ASC"
            ).fetchall()
        out = []
        for row in rows:
            item = self._row_to_public(row)
            item["api_key_masked"] = self._masked_from_cipher(self, row)
            out.append(item)
        return out

    def get_public(self, model_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM ai_model_configs WHERE id = ?", (model_id,)
            ).fetchone()
        if row is None:
            raise ModelConfigError(404, f"模型配置不存在：{model_id}")
        item = self._row_to_public(row)
        item["api_key_masked"] = self._masked_from_cipher(self, row)
        return item

    def default_id(self) -> Optional[str]:
        with self._lock:
            if not Path(self._db_path).exists():
                return None
            conn = self._connect()
            row = conn.execute(
                "SELECT id FROM ai_model_configs WHERE enabled = 1 AND is_default = 1 "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return row["id"] if row else None

    def resolve(self, model_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """解析出可直接构造 provider 的配置（含**解密后**密钥，仅供进程内使用）。

        * ``model_id`` 为空：用已启用的默认模型；没有默认时返回 ``None``，
          调用方回退到 ``.env``（向后兼容）。
        * ``model_id`` 可传配置 id 或名称；找不到返回 ``None``。
        禁用（``enabled=0``）的模型不参与解析。
        """
        with self._lock:
            if not Path(self._db_path).exists():
                return None
            conn = self._connect()
            if model_id:
                row = conn.execute(
                    "SELECT * FROM ai_model_configs WHERE (id = ? OR name = ?) AND enabled = 1",
                    (model_id, model_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM ai_model_configs WHERE enabled = 1 AND is_default = 1 "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "model": row["model"],
            "base_url": row["base_url"],
            "timeout": row["timeout"],
            "api_key": self._decrypt_secret(row["id"], row["api_key_enc"]),
        }

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate(payload: Dict[str, Any], *, require_key: bool) -> None:
        provider = str(payload.get("provider") or "").strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ModelConfigError(
                400,
                f"不支持的 provider 类型：{provider or '(空)'}；"
                f"可选：{', '.join(SUPPORTED_PROVIDERS)}",
            )
        if not str(payload.get("name") or "").strip():
            raise ModelConfigError(400, "名称不能为空")
        if len(str(payload["name"]).strip()) > 64:
            raise ModelConfigError(400, "名称最长 64 个字符")
        if not str(payload.get("model") or "").strip():
            raise ModelConfigError(400, "模型名不能为空")
        base_url = payload.get("base_url")
        if base_url and not str(base_url).startswith(("http://", "https://")):
            raise ModelConfigError(400, "base_url 必须以 http:// 或 https:// 开头")
        timeout = payload.get("timeout", 30.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ModelConfigError(400, "timeout 必须是数字（秒）") from exc
        if not 1 <= timeout <= 600:
            raise ModelConfigError(400, "timeout 必须在 1–600 秒之间")

    def create(self, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        self._validate(payload, require_key=False)
        import uuid as _uuid

        model_id = _uuid.uuid4().hex[:12]
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            has_default = conn.execute(
                "SELECT 1 FROM ai_model_configs WHERE is_default = 1 AND enabled = 1 LIMIT 1"
            ).fetchone()
            is_default = 1 if (payload.get("is_default") or not has_default) else 0
            encrypted = None
            if payload.get("api_key"):
                encrypted = self._encrypt_secret(model_id, str(payload["api_key"]))
            if is_default:
                conn.execute("UPDATE ai_model_configs SET is_default = 0")
            try:
                conn.execute(
                    "INSERT INTO ai_model_configs (id, name, provider, model, base_url, "
                    "api_key_enc, timeout, is_default, enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        model_id,
                        str(payload["name"]).strip(),
                        str(payload["provider"]).strip().lower(),
                        str(payload["model"]).strip(),
                        payload.get("base_url") or None,
                        encrypted,
                        float(payload.get("timeout", 30.0)),
                        is_default,
                        1 if payload.get("enabled", True) else 0,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ModelConfigError(409, "同名的模型配置已存在") from exc
        self._audit("ai.model_config.create", actor, model_id, payload)
        return self.get_public(model_id)

    def update(self, model_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        current = self.get_public(model_id)
        merged = {
            "name": payload.get("name", current["name"]),
            "provider": payload.get("provider", current["provider"]),
            "model": payload.get("model", current["model"]),
            "base_url": payload.get("base_url", current["base_url"]),
            "timeout": payload.get("timeout", current["timeout"]),
            "api_key": payload.get("api_key"),
        }
        self._validate(merged, require_key=False)
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            wants_default = payload.get("is_default")
            enabled = payload.get("enabled")
            sets = [
                "name = ?",
                "provider = ?",
                "model = ?",
                "base_url = ?",
                "timeout = ?",
                "updated_at = ?",
            ]
            params: List[Any] = [
                merged["name"].strip(),
                merged["provider"].strip().lower(),
                merged["model"].strip(),
                merged["base_url"] or None,
                float(merged["timeout"]),
                now,
            ]
            if payload.get("api_key"):
                params.append(self._encrypt_secret(model_id, str(payload["api_key"])))
                sets.append("api_key_enc = ?")
            if enabled is not None:
                params.append(1 if enabled else 0)
                sets.append("enabled = ?")
                if not enabled:
                    params.append(0)
                    sets.append("is_default = ?")
            if wants_default:
                conn.execute(
                    "UPDATE ai_model_configs SET is_default = 0 WHERE id != ?", (model_id,)
                )
                params.append(1)
                sets.append("is_default = ?")
            try:
                conn.execute(
                    f"UPDATE ai_model_configs SET {', '.join(sets)} WHERE id = ?",
                    (*params, model_id),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ModelConfigError(409, "同名的模型配置已存在") from exc
        self._audit("ai.model_config.update", actor, model_id, merged)
        return self.get_public(model_id)

    def delete(self, model_id: str, actor: str) -> Dict[str, Any]:
        self.get_public(model_id)
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT is_default FROM ai_model_configs WHERE id = ?", (model_id,)
            ).fetchone()
            conn.execute("DELETE FROM ai_model_configs WHERE id = ?", (model_id,))
            promoted = None
            if row and row["is_default"]:
                nxt = conn.execute(
                    "SELECT id FROM ai_model_configs WHERE enabled = 1 "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if nxt:
                    conn.execute(
                        "UPDATE ai_model_configs SET is_default = 1 WHERE id = ?",
                        (nxt["id"],),
                    )
                    promoted = nxt["id"]
            conn.commit()
        self._audit("ai.model_config.delete", actor, model_id, {})
        return {"deleted": model_id, "default_id": promoted}

    def set_default(self, model_id: str, actor: str) -> Dict[str, Any]:
        current = self.get_public(model_id)
        if not current["enabled"]:
            raise ModelConfigError(409, "禁用的模型不能设为默认")
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE ai_model_configs SET is_default = 0")
            conn.execute(
                "UPDATE ai_model_configs SET is_default = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), model_id),
            )
            conn.commit()
        self._audit("ai.model_config.set_default", actor, model_id, {})
        return self.get_public(model_id)

    def _audit(self, action: str, actor: str, model_id: str, payload: Dict[str, Any]) -> None:
        """审计只记录元数据；**绝不**带 api_key / 密文。"""
        try:
            from src.ai.audit import emit

            emit(
                action,
                module="src.ai.model_config",
                actor=actor or "capability",
                model_config_id=model_id,
                name=str(payload.get("name") or ""),
                provider=str(payload.get("provider") or ""),
                model=str(payload.get("model") or ""),
            )
        except Exception as exc:  # pragma: no cover - 审计不可用不阻断
            logger.warning("model config audit emit failed: %s", exc)

    def encryption_posture(self) -> Dict[str, Any]:
        source = "env" if os.environ.get(MODEL_CONFIG_KEY_ENV) else "file"
        return {"algorithm": "AES-256-GCM", "key_source": source}


_store: Optional[ModelConfigStore] = None
_store_lock = threading.Lock()


def get_model_config_store() -> ModelConfigStore:
    """进程级存储单例（只持有连接，不持有 provider）。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = ModelConfigStore()
        return _store


def reset_model_config_store() -> None:
    global _store
    with _store_lock:
        _store = None
