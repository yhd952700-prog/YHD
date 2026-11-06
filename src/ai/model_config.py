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
from typing import Any, Dict, List, Optional, Sequence

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
    owner_principal TEXT NOT NULL DEFAULT '',
    timeout       REAL NOT NULL DEFAULT 30.0,
    is_default    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


class ModelConfigError(Exception):
    """配置层错误：带 HTTP 状态码与可直接展示的中文原因。

    ``code`` 是稳定的机器可读错误码（账号/Key 归属类错误必填）；普通校验错误
    可以留空，保持既有 ``detail`` 字符串契约不变。
    """

    def __init__(self, status_code: int, detail: str, code: Optional[str] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


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


def _row_owner(row: sqlite3.Row) -> str:
    """安全读取归属列：旧库/测试手工表可能还没有这一列。"""
    try:
        return str(row["owner_principal"] or "")
    except (IndexError, KeyError):
        return ""


def _visible(owner: str, visible_to: Optional[Sequence[str]], allow_unowned: bool) -> bool:
    """Key 可见性：``visible_to=None`` 表示进程内部调用，不设限。"""
    if visible_to is None:
        return True
    if not owner:
        # 空归属 = 账号体系上线前创建，归根账号所有；只有根视角可见。
        return allow_unowned
    return owner in visible_to


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
                try:
                    parent = os.path.dirname(os.path.abspath(self._db_path))
                    os.makedirs(parent, exist_ok=True)
                    conn = sqlite3.connect(self._db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.executescript(_SCHEMA)
                    self._migrate_schema(conn)
                    conn.commit()
                except (sqlite3.Error, OSError) as exc:
                    raise ModelConfigError(
                        self._storage_status,
                        f"无法打开模型配置库 {self._db_path}：{exc}。"
                        "磁盘可能已满、数据目录只读，或文件被其它程序独占；"
                        "为避免写坏已有配置，本次操作已拒绝。",
                    ) from exc
                self._conn = conn
            return self._conn

    #: 存储不可写（磁盘满/只读/被占用）时对外的状态码与文案前缀。
    _storage_status = 503

    def _storage_error(self, exc: Exception) -> ModelConfigError:
        return ModelConfigError(
            self._storage_status,
            f"无法写入模型配置（磁盘可能已满、数据目录只读或文件被占用）：{exc}。"
            "已有配置保持不变。",
        )

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """就地补列（幂等）：旧库没有 ``owner_principal`` 时加上，不重建表。

        用 ``ADD COLUMN ... DEFAULT ''`` 而不是回填：空归属表示"迁移前创建"，
        由可见性规则把它归给根账号，因此不会把历史模型划给错误的账号。
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_model_configs)")}
        if "owner_principal" not in columns:
            conn.execute(
                "ALTER TABLE ai_model_configs "
                "ADD COLUMN owner_principal TEXT NOT NULL DEFAULT ''"
            )

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
            "owner_principal": _row_owner(row),
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

    def list_models(
        self,
        *,
        visible_to: Optional[Sequence[str]] = None,
        allow_unowned: bool = False,
    ) -> List[Dict[str, Any]]:
        """列出配置；给定 ``visible_to`` 时只返回该视角可见的 Key 归属。

        默认（``visible_to=None``）与旧行为完全一致，供进程内部与旧调用方使用。
        """
        with self._lock:
            if not Path(self._db_path).exists():
                return []
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM ai_model_configs ORDER BY is_default DESC, created_at ASC"
            ).fetchall()
        out = []
        for row in rows:
            if not _visible(_row_owner(row), visible_to, allow_unowned):
                continue
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

    def default_id(
        self,
        *,
        visible_to: Optional[Sequence[str]] = None,
        allow_unowned: bool = False,
    ) -> Optional[str]:
        """当前视角下的默认模型 id。

        受限视角下不能直接返回全局 ``is_default`` 行 —— 那会把别人的 Key 当成
        "默认"用掉。若不可见，退而选择可见范围内最新的启用模型；全都不可见时
        返回 ``None``。
        """
        with self._lock:
            if not Path(self._db_path).exists():
                return None
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM ai_model_configs WHERE enabled = 1 "
                "ORDER BY is_default DESC, updated_at DESC"
            ).fetchall()
        for row in rows:
            if _visible(_row_owner(row), visible_to, allow_unowned):
                return row["id"]
        return None

    def resolve(
        self,
        model_id: Optional[str] = None,
        *,
        visible_to: Optional[Sequence[str]] = None,
        allow_unowned: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """解析出可直接构造 provider 的配置（含**解密后**密钥，仅供进程内使用）。

        * ``model_id`` 为空：用当前视角下的默认模型；没有可用模型时返回
          ``None``，调用方回退到 ``.env``（向后兼容）。
        * ``model_id`` 可传配置 id 或名称；找不到返回 ``None``。
        * 给定 ``visible_to`` 且命中**他人 Key** 时一律 403 ``KEY_OWNER_DENIED``。
          绝不静默改用它 Key —— 静默替换就是 Key 冒用。
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
                rows = conn.execute(
                    "SELECT * FROM ai_model_configs WHERE enabled = 1 "
                    "ORDER BY is_default DESC, updated_at DESC"
                ).fetchall()
                row = next(
                    (r for r in rows if _visible(_row_owner(r), visible_to, allow_unowned)),
                    None,
                )
        if row is None:
            return None
        owner = _row_owner(row)
        if not _visible(owner, visible_to, allow_unowned):
            raise ModelConfigError(
                403, "无权使用该模型配置", code="ACCOUNT_KEY_NOT_OWNED"
            )
        return {
            "id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "model": row["model"],
            "base_url": row["base_url"],
            "owner_principal": owner,
            "timeout": row["timeout"],
            "api_key": self._decrypt_secret(row["id"], row["api_key_enc"]),
        }

    # ------------------------------------------------------------------ #
    # 备份导出 / 导入（密文原样搬运，不在备份里解密）
    # ------------------------------------------------------------------ #
    _BACKUP_COLUMNS = (
        "id", "name", "provider", "model", "base_url", "api_key_enc",
        "owner_principal", "timeout", "is_default", "enabled",
        "created_at", "updated_at",
    )

    def export_encrypted_rows(
        self, owners: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        """导出模型配置行（``api_key_enc`` 保持密文）；``owners`` 限定归属。"""
        allowed = set(owners) if owners is not None else None
        with self._lock:
            rows = self._connect().execute(
                "SELECT * FROM ai_model_configs"
            ).fetchall()
        exported: List[Dict[str, Any]] = []
        for row in rows:
            owner = str(row["owner_principal"] or "")
            if allowed is not None and owner and owner not in allowed:
                continue
            exported.append({key: row[key] for key in self._BACKUP_COLUMNS})
        return exported

    def import_encrypted_rows(self, rows: List[Dict[str, Any]]) -> int:
        """恢复模型配置行；只写密文，不触碰明文。返回成功条数。"""
        written = 0
        with self._lock:
            connection = self._connect()
            for row in rows or []:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                values = [row.get(key) for key in self._BACKUP_COLUMNS]
                try:
                    connection.execute(
                        "INSERT OR REPLACE INTO ai_model_configs ("
                        + ", ".join(self._BACKUP_COLUMNS) + ") VALUES ("
                        + ", ".join(["?"] * len(self._BACKUP_COLUMNS)) + ")",
                        values,
                    )
                    written += 1
                except sqlite3.Error as exc:
                    logger.warning("could not restore model row %s: %s", row.get("id"), exc)
            connection.commit()
        return written

    def export_key_material(self) -> Optional[bytes]:
        """模型主密钥（用于让备份里的 ``api_key_enc`` 在目标机仍可解密）。"""
        try:
            return self._encryption_key()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not export model key material: %s", exc)
            return None

    def import_key_material(self, raw: bytes) -> bool:
        """写回模型主密钥；环境变量已显式配置时不覆盖。"""
        if os.environ.get(MODEL_CONFIG_KEY_ENV):
            return False
        if not raw or len(raw) != 32:
            return False
        path = Path(self._key_path)
        if path.exists():
            return True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(raw))
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not restore model key material: %s", exc)
            return False

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

    def create(
        self,
        payload: Dict[str, Any],
        actor: str,
        *,
        owner_principal: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._validate(payload, require_key=False)
        import uuid as _uuid

        model_id = _uuid.uuid4().hex[:12]
        now = _now_iso()
        owner = str(owner_principal or payload.get("owner_principal") or "")
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
                    "api_key_enc, owner_principal, timeout, is_default, enabled, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        model_id,
                        str(payload["name"]).strip(),
                        str(payload["provider"]).strip().lower(),
                        str(payload["model"]).strip(),
                        payload.get("base_url") or None,
                        encrypted,
                        owner,
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
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
        self._audit("ai.model_config.create", actor, model_id, {**payload, "owner": owner})
        return self.get_public(model_id)

    def update(self, model_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        # 读-改-写必须在同一把锁内完成：两个窗口同时改同一个模型的不同字段时，
        # 若在读与写之间插入另一次更新，后写者会把先写者的字段整行覆盖回去
        # （典型 lost update）。这里把读取挪进锁内，SQLite 层也只有一个写者。
        with self._lock:
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
            conn = self._connect()
            now = _now_iso()
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
            if "owner_principal" in payload and payload["owner_principal"] is not None:
                params.append(str(payload["owner_principal"]))
                sets.append("owner_principal = ?")
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
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
        self._audit("ai.model_config.update", actor, model_id, merged)
        return self.get_public(model_id)

    def delete(self, model_id: str, actor: str) -> Dict[str, Any]:
        self.get_public(model_id)
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT is_default FROM ai_model_configs WHERE id = ?", (model_id,)
            ).fetchone()
            try:
                conn.execute("DELETE FROM ai_model_configs WHERE id = ?", (model_id,))
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
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
            try:
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
        self._audit("ai.model_config.delete", actor, model_id, {})
        return {"deleted": model_id, "default_id": promoted}

    def set_default(
        self,
        model_id: str,
        actor: str,
        *,
        visible_to: Optional[Sequence[str]] = None,
        allow_unowned: bool = False,
    ) -> Dict[str, Any]:
        current = self.get_public(model_id)
        if not current["enabled"]:
            raise ModelConfigError(409, "禁用的模型不能设为默认")
        with self._lock:
            conn = self._connect()
            if visible_to is None:
                conn.execute("UPDATE ai_model_configs SET is_default = 0")
            else:
                # 受限视角只能在自己的可见集合内改默认，绝不重置别人的默认。
                ids = [
                    r["id"]
                    for r in conn.execute("SELECT id, owner_principal FROM ai_model_configs")
                    if _visible(_row_owner(r), visible_to, allow_unowned)
                ]
                if ids:
                    conn.execute(
                        "UPDATE ai_model_configs SET is_default = 0 WHERE id IN "
                        f"({', '.join('?' for _ in ids)})",
                        ids,
                    )
            try:
                conn.execute(
                    "UPDATE ai_model_configs SET is_default = 1, updated_at = ? WHERE id = ?",
                    (_now_iso(), model_id),
                )
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
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
