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
from urllib.parse import urlparse
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..common.windows_acl import secure_delete_file, tighten_current_user_only

logger = logging.getLogger("liuhao.ai.model_config")

MODEL_CONFIG_DB_ENV = "LIUHAO_MODEL_CONFIG_DB"
MODEL_CONFIG_KEY_ENV = "LIUHAO_MODEL_CONFIG_KEY"
MODEL_CONFIG_KEY_FILE_ENV = "LIUHAO_MODEL_CONFIG_KEY_FILE"
#: 设为 0/off 时跳过"旧 .env 配置迁移"（测试用；生产保持默认开启）。
MODEL_CONFIG_MIGRATE_ENV = "LIUHAO_MODEL_CONFIG_MIGRATE"

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

#: 密文存在但当前密钥解不开时，对外展示的中性、可操作文案。
#: 绝不要把底层异常（InvalidTag / 解密失败 / 文件路径）直接透给用户。
NEEDS_REENTRY_TEXT = "凭据需重新填写"
NEEDS_REENTRY_CODE = "MODEL_KEY_NEEDS_REENTRY"

#: 协议适配器 -> 真实厂商。``provider`` 表达的是"用哪套协议构造请求"，
#: 不等于请求发往哪家厂商：``provider=openai`` + base_url 指向 DeepSeek 时，
#: 实际厂商是 DeepSeek。此映射只供日志/审计/界面展示，不参与路由。
_VENDOR_BY_PROVIDER = {
    "deepseek": "deepseek",
    "moonshot": "moonshot",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "google": "google",
    "gemini": "google",
    "ollama": "ollama",
    "azure": "azure",
    "mock": "mock",
}

#: base_url 主机名 -> 厂商。只收确定性映射，未知主机原样返回主机名。
_VENDOR_BY_HOST = {
    "api.deepseek.com": "deepseek",
    "deepseek.com": "deepseek",
    "api.openai.com": "openai",
    "openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.moonshot.cn": "moonshot",
    "api.moonshot.ai": "moonshot",
    "generativelanguage.googleapis.com": "google",
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def vendor_for(provider: Any, base_url: Any = None) -> str:
    """把 ``provider``（协议适配器名）换算成真实厂商。

    ``provider=openai`` 只是"按 OpenAI 兼容协议发请求"，base_url 指向
    ``api.deepseek.com`` 时请求实际发往 DeepSeek；日志里只写 ``provider=openai``
    会让排障与审计误以为是 OpenAI。显式厂商类型（deepseek/anthropic/...）直接
    返回自身；本地端点返回 ``local``；未知主机返回真实主机名，绝不冒用
    ``openai``。本函数是纯展示用途，**不改变路由与鉴权行为**。
    """
    name = str(provider or "").strip().lower()
    if name in _VENDOR_BY_PROVIDER:
        return _VENDOR_BY_PROVIDER[name]
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    if host in _VENDOR_BY_HOST:
        return _VENDOR_BY_HOST[host]
    if host in _LOCAL_HOSTS or host.startswith("127.") or host.endswith(".local"):
        return "local"
    if host:
        return host
    return "openai" if name in {"openai", "openai-compatible", "openai_compatible"} else (name or "unknown")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_model_configs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    base_url      TEXT,
    api_key_enc   TEXT,
    owner_principal TEXT NOT NULL DEFAULT '',
    timeout       REAL NOT NULL DEFAULT 120.0,
    is_default    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    -- 「测试连接」通过的时间；NULL = 没测过或最近一次测试失败。
    -- 只有非 NULL 的模型才允许被用户显式「设为默认」。
    last_verified_at TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 一次性迁移标记：记住"旧 .env 配置已处理过"。没有它的话，用户删掉迁移
-- 出来的那条模型后，下次启动会再次自动加回来 —— 那就不可删了。
CREATE TABLE IF NOT EXISTS ai_model_config_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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


def is_masked_placeholder(value: Any) -> bool:
    """是否是"回显用的掩码/占位"，而不是用户新输入的 Key。

    前端编辑时可能把 ``api_key_masked`` 原样回传；若把 ``****`` 或
    ``sk-a…mnop`` 当成新 Key 加密，就会覆盖真实凭据。写库前必须过滤。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text in {NEEDS_REENTRY_TEXT, "****", "******", "••••", "••••••"}:
        return True
    if "…" in text or "*" in text or "•" in text:
        return True
    return text.lower() in {
        "redacted",
        "[redacted]",
        "unchanged",
        "<unchanged>",
        "keep",
        "keep-existing",
        "existing",
    }


def _derive_key(passphrase: str) -> bytes:
    return hashlib.sha256(passphrase.encode("utf-8")).digest()


#: API Key 长度上限。真实 Key 远短于此；上限只用来把"整段粘贴错内容"挡在
#: 入库/拼头之前，而不是当作安全边界。
MAX_API_KEY_LENGTH = 4096


class ApiKeyFormatError(ValueError):
    """Key 文本形态不合法（夹空格/换行/超长）；消息可直接展示给用户。"""


def normalize_api_key(raw: Any) -> Optional[str]:
    """统一 Key 的文本形态：去首尾空白，并拒绝夹带空白/换行的 Key。

    为什么必须在写库、拼 ``Authorization``、测试连接**三处同源**处理：
    带首尾空格的 Key 会让 ``requests`` 直接抛
    ``Illegal header value b'Bearer   sk-...'``，请求根本没发出去，
    用户却以为"Key 填错了"。

    ``str.strip()`` 覆盖全角空格（U+3000）、制表符与 ``\\r\\n``；中间残留的
    空白/控制字符无法安全猜测，直接拒绝并给出可行动提示，避免把坏 Key
    拼进请求头。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > MAX_API_KEY_LENGTH:
        raise ApiKeyFormatError(
            f"API Key 过长（{len(text)} 字符，上限 {MAX_API_KEY_LENGTH}），"
            "看起来粘贴了多余内容，请只复制 Key 本身。"
        )
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ApiKeyFormatError(
            "API Key 中间出现了空格、换行或不可见字符：请重新复制 Key，"
            "不要把换行和空格带进来。"
        )
    return text


def _row_owner(row: sqlite3.Row) -> str:
    """安全读取归属列：旧库/测试手工表可能还没有这一列。"""
    try:
        return str(row["owner_principal"] or "")
    except (IndexError, KeyError):
        return ""


def _row_verified(row: sqlite3.Row) -> Optional[str]:
    """最近一次「测试连接」通过的时间；NULL/缺列都表示"没测通"。"""
    try:
        value = row["last_verified_at"]
    except (IndexError, KeyError):
        return None
    return str(value) if value else None


def _visible(owner: str, visible_to: Optional[Sequence[str]], allow_unowned: bool) -> bool:
    """Key 可见性：``visible_to=None`` 表示进程内部调用，不设限。"""
    if visible_to is None:
        return True
    if not owner:
        # 空归属 = 账号体系上线前创建，归根账号所有；只有根视角可见。
        return allow_unowned
    return owner in visible_to


def _usable(
    owner: str,
    visible_to: Optional[Sequence[str]],
    allow_unowned: bool,
    allow_owners: Optional[Sequence[str]],
) -> bool:
    """Key **可用性**：在可见的基础上，再要求知情授权（``allow_superior_key_use``）。

    ``allow_owners`` 是 ``KeyScope.allow_owners`` 的原样传入：根账号包含自己、
    空归属与"已开启共享"的子树成员；成员只有自己。``None`` 表示不追加收窄
    （进程内部 / 旧调用方）。
    """
    if not _visible(owner, visible_to, allow_unowned):
        return False
    if allow_owners is None:
        return True
    if not owner:
        return bool(allow_unowned)
    return owner in allow_owners


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
                    # 删除 Key 时把被释放的页就地覆写为零：否则 DELETE 只标记
                    # 空闲，密文仍留在库文件/WAL 里，拿到密钥文件即可离线解开。
                    conn.execute("PRAGMA secure_delete=ON")
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
        # 老库补"是否测通"：默认 NULL（没测过），于是升级后既有的默认模型
        # 不会被误当作"已验证"，用户点一次「测试连接」即可解锁。
        if "last_verified_at" not in columns:
            conn.execute(
                "ALTER TABLE ai_model_configs ADD COLUMN last_verified_at TEXT"
            )
        # 30 秒是旧版本的默认生成读超时，会把本地模型冷启动（实测约 36 秒）
        # 判成超时。只迁移**恰好等于旧默认值**的行，且只做一次：用户之后
        # 显式改回 30 秒（或任何更短的值）不会再被覆盖。
        #
        # 改值与写迁移标记必须原子：中途失败（磁盘满/只读）时若改值已提交而
        # 标记没写，下次启动会再改一次；反过来标记先落而改值失败，则旧库永远
        # 停在 30 秒。放进同一个事务，失败即整体回滚，下次还能重试。
        migrated = conn.execute(
            "SELECT value FROM ai_model_config_meta WHERE key = ?",
            ("timeout_floor_120",),
        ).fetchone()
        if migrated is None:
            with conn:
                conn.execute(
                    "UPDATE ai_model_configs SET timeout = 120.0 WHERE timeout = 30.0"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO ai_model_config_meta (key, value) "
                    "VALUES (?, ?)",
                    ("timeout_floor_120", "1"),
                )

    def _encryption_key(self) -> bytes:
        """解析/生成 32 字节密钥；无法获得密钥时抛 ``ModelConfigError``。

        密钥文件规定是 32 个原始字节，必须**原样**使用：早先版本先
        ``read_bytes().strip()`` 再判断长度，若密钥的首/尾字节恰好落在
        ASCII 空白（0x09-0x0d/0x20 等，随机密钥约 4-9% 概率），读回时会被
        裁短、退化成口令派生，得到一个与生成时不同的密钥 —— 已有的密文
        全部解不开，界面就显示"密文无法解密"。这里精确 32 字节直接使用；
        只有"32 字节 + 文本换行"才去掉换行；其余才按历史兼容当作口令。
        """
        configured = os.environ.get(MODEL_CONFIG_KEY_ENV)
        if configured:
            return _derive_key(configured)
        path = Path(self._key_path)
        if path.exists():
            # 老安装留下的密钥文件也要补收紧：Windows 的 0600 只是只读位，
            # 真正生效的是显式 DACL。失败只告警，不阻断已有密文的读取。
            if not tighten_current_user_only(path):
                logger.warning(
                    "模型密钥文件 ACL 未能收紧为仅当前用户：%s（建议手工修复）",
                    path,
                )
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise ModelConfigError(
                    503,
                    "无法读取模型密钥文件，已保存的凭据无法解密。"
                    "请检查数据目录权限，或从备份恢复该密钥文件后重启。"
                    f"（{path.name}）",
                ) from exc
            if len(raw) == 32:
                return raw
            trimmed = raw.rstrip(b"\r\n")
            if len(trimmed) == 32:
                # 文本方式保存的 32 字节密钥（多了一个行尾换行）。
                logger.warning(
                    "模型密钥文件带有行尾换行（%d 字节），按 32 字节密钥使用",
                    len(raw),
                )
                return trimmed
            # 兼容"文件里存的是口令"的历史写法。
            return _derive_key(raw.strip().decode("utf-8", "ignore"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = os.urandom(32)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                # 先落盘再收紧：Windows 上 0o600 不改变 DACL，必须显式设置。
                os.write(fd, key)
            finally:
                os.close(fd)
            if not tighten_current_user_only(path):
                # 主密钥可解全部模型凭据，宁可不生成也不能留在他人可读的位置。
                secure_delete_file(path)
                raise ModelConfigError(
                    503,
                    "模型密钥文件无法收紧为仅当前用户可读，已拒绝生成。"
                    "请检查数据目录权限后重试。",
                )
            logger.info("model config encryption key generated at %s", path)
            return key
        except ModelConfigError:
            raise
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
        try:
            return cipher.decrypt(
                EncryptedData(
                    ciphertext=ciphertext,
                    nonce=nonce,
                    algorithm="AES-256-GCM",
                    key_id="model-config",
                    associated_data=f"ai-model:{model_id}".encode(),
                )
            ).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - InvalidTag/解码失败统一收敛
            # 典型场景：换机器、恢复旧数据，或数据目录里的密钥文件被替换，
            # 导致密文与完整性密钥不匹配。给用户下一步动作，不抛内部异常。
            raise ModelConfigError(
                409,
                "该模型已保存的凭据无法在当前数据目录解密（常见于更换机器、"
                "恢复旧数据后密钥不匹配）。请重新填写该模型的 API Key；"
                "模型配置本身会保留，不会被清空。",
                code=NEEDS_REENTRY_CODE,
            ) from exc

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
            # 真实厂商（由 provider 协议名 + base_url 推断），只供展示/日志。
            "vendor": vendor_for(row["provider"], row["base_url"]),
            "owner_principal": _row_owner(row),
            "timeout": row["timeout"],
            "is_default": bool(row["is_default"]),
            "enabled": bool(row["enabled"]),
            #: 是否通过过「测试连接」：只有 True 才允许显式设为默认。
            "verified": bool(_row_verified(row)),
            "last_verified_at": _row_verified(row),
            "api_key_set": bool(row["api_key_enc"]),
            # missing: 未填写；ok: 可解密；needs_reentry: 密文在但当前密钥解不开。
            "api_key_status": "missing" if not row["api_key_enc"] else "ok",
            "api_key_masked": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _masked_from_cipher(
        store: "ModelConfigStore", row: sqlite3.Row
    ) -> tuple[Optional[str], str]:
        """返回 ``(脱敏串, api_key_status)``；解密失败绝不暴露内部错误。"""
        if not row["api_key_enc"]:
            return None, "missing"
        try:
            plain = store._decrypt_secret(row["id"], row["api_key_enc"])
        except Exception as exc:  # noqa: BLE001 - 用户可见面必须保持中性
            logger.warning(
                "model config %s credential cannot be decrypted (%s: %s)",
                row["id"],
                type(exc).__name__,
                exc,
            )
            return NEEDS_REENTRY_TEXT, "needs_reentry"
        return mask_secret(plain), "ok"

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
            item["api_key_masked"], item["api_key_status"] = self._masked_from_cipher(
                self, row
            )
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
        item["api_key_masked"], item["api_key_status"] = self._masked_from_cipher(self, row)
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
        allow_owners: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """解析出可直接构造 provider 的配置（含**解密后**密钥，仅供进程内使用）。

        * ``model_id`` 为空：用当前视角下的默认模型；没有可用模型时返回
          ``None``，调用方回退到 ``.env``（向后兼容）。
        * ``model_id`` 可传配置 id 或名称；找不到返回 ``None``。
        * 给定 ``visible_to`` 且命中**他人 Key** 时一律 403 ``KEY_OWNER_DENIED``。
          绝不静默改用它 Key —— 静默替换就是 Key 冒用。
        * 给定 ``allow_owners`` 时，命中可见但**未开启共享授权**的 Key 返回
          403 ``ACCOUNT_KEY_CONSENT_REQUIRED``：显式 ``model_id`` 与自动路由
          必须用同一套授权口径（此前显式选择绕过了 ``allow_superior_key_use``）。
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
                    (
                        r
                        for r in rows
                        if _usable(
                            _row_owner(r), visible_to, allow_unowned, allow_owners
                        )
                    ),
                    None,
                )
        if row is None:
            return None
        owner = _row_owner(row)
        if not _visible(owner, visible_to, allow_unowned):
            raise ModelConfigError(
                403, "无权使用该模型配置", code="ACCOUNT_KEY_NOT_OWNED"
            )
        if not _usable(owner, visible_to, allow_unowned, allow_owners):
            raise ModelConfigError(
                403,
                "该模型配置未开启共享授权，无法使用",
                code="ACCOUNT_KEY_CONSENT_REQUIRED",
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
                # 旧机的主密钥不在包里，密文在新机必然解不开。留下密文会让
                # 员工页/目标页任何一次"取默认模型"直接 500；这里降级为
                # "配置保留、Key 待重填"，既保住配置也不阻断其它功能。
                encrypted_key = row.get("api_key_enc")
                if encrypted_key:
                    try:
                        self._decrypt_secret(str(row.get("id")), encrypted_key)
                    except Exception:  # noqa: BLE001 - 解不开＝不是本机密钥
                        values[self._BACKUP_COLUMNS.index("api_key_enc")] = None
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

    def export_plaintext_secrets(
        self, rows: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """把待导出行的 Key 在本机解密，交给备份包用口令重新封装。

        主密钥不出机：备份包里存的是「口令派生 KEK 包裹的密文」，不是明文
        Key；目标机导入时用同一口令解封，再用**本机**主密钥重新加密，从而
        做到"原来的 Key 还能用"。
        """
        secrets: Dict[str, str] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id") or "")
            stored = row.get("api_key_enc")
            if not model_id or not stored:
                continue
            try:
                plain = self._decrypt_secret(model_id, stored)
            except Exception as exc:  # noqa: BLE001 - 单行失败不阻断整包
                logger.warning("model config %s credential not portable: %s", model_id, exc)
                continue
            if plain:
                secrets[model_id] = plain
        return secrets

    def import_plaintext_secrets(self, secrets: Any) -> int:
        """用本机主密钥重新加密导入的 Key；返回成功更新条数。"""
        if not isinstance(secrets, dict) or not secrets:
            return 0
        written = 0
        with self._lock:
            connection = self._connect()
            for model_id, secret in secrets.items():
                if not model_id or not isinstance(secret, str) or not secret:
                    continue
                try:
                    cipher = self._encrypt_secret(str(model_id), secret)
                    cursor = connection.execute(
                        "UPDATE ai_model_configs SET api_key_enc = ? WHERE id = ?",
                        (cipher, str(model_id)),
                    )
                    written += int(cursor.rowcount or 0)
                except Exception as exc:  # noqa: BLE001 - 单行失败不阻断整包
                    logger.warning("could not re-encrypt model row %s: %s", model_id, exc)
            connection.commit()
        return written

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
        timeout = payload.get("timeout", 120.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ModelConfigError(400, "timeout 必须是数字（秒）") from exc
        if not 1 <= timeout <= 600:
            raise ModelConfigError(400, "timeout 必须在 1–600 秒之间")
        # Key 形态在此统一收口：写库、拼 Authorization、测试连接都从这里过，
        # 避免"入库时带空格 → 请求头非法 → 用户以为 Key 填错"。
        raw_key = payload.get("api_key")
        if raw_key is not None and not is_masked_placeholder(raw_key):
            try:
                normalize_api_key(raw_key)
            except ApiKeyFormatError as exc:
                raise ModelConfigError(400, str(exc)) from exc

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
        if payload.get("is_default"):
            # 新建模型还没经过「测试连接」，不能直接占默认位；否则界面上写着
            # "默认"，实际一调用就失败。先保存、测通，再设为默认。
            raise ModelConfigError(
                409,
                "新建的模型还没有测试通过，不能直接设为默认："
                "请先保存，点「测试连接」通过后再设为默认。",
            )
        with self._lock:
            conn = self._connect()
            has_default = conn.execute(
                "SELECT 1 FROM ai_model_configs WHERE is_default = 1 AND enabled = 1 LIMIT 1"
            ).fetchone()
            is_default = 0 if has_default else 1
            encrypted = None
            raw_key = normalize_api_key(payload.get("api_key"))
            if raw_key and not is_masked_placeholder(raw_key):
                encrypted = self._encrypt_secret(model_id, raw_key)
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
                        float(payload.get("timeout", 120.0)),
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
            raw_key = None
            if payload.get("api_key") and not is_masked_placeholder(payload["api_key"]):
                raw_key = normalize_api_key(payload["api_key"])
            # 改了密钥/协议/模型名/地址，之前的"测通"就不再代表这套配置能用了。
            material_change = bool(
                raw_key
                or merged["provider"].strip().lower() != current["provider"]
                or merged["model"].strip() != current["model"]
                or (merged["base_url"] or None) != (current["base_url"] or None)
            )
            if wants_default and material_change:
                raise ModelConfigError(
                    409,
                    "这次修改了密钥、模型名或地址，需要重新点「测试连接」通过后"
                    "才能设为默认。",
                )
            if wants_default and not current.get("verified"):
                raise ModelConfigError(
                    409,
                    "该模型还没有测试通过，不能设为默认："
                    "请先点「测试连接」，通过后再设为默认。",
                )
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
            if material_change:
                sets.append("last_verified_at = NULL")
            # 只有"用户真的输入了新值"才更新；掩码/占位值一律视为未修改。
            if raw_key:
                params.append(self._encrypt_secret(model_id, raw_key))
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
            # 删除已提交后截断 WAL：否则旧的 -wal 帧里仍留着含密文的页副本
            # （secure_delete 管不到已经写进 WAL 的历史帧）。截断失败不影响
            # 删除结果，只记一条告警。
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                logger.warning("删除模型后截断 WAL 失败：%s", model_id, exc_info=True)
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
        if not current.get("last_verified_at"):
            # 「设为默认」必须与实际调用一致：没测通的模型一旦成为默认，
            # 界面写着"默认"，聊天却立刻失败，用户会以为系统坏了。
            raise ModelConfigError(
                409,
                "该模型还没有测试通过，不能设为默认："
                "请先点「测试连接」，通过后再设为默认。",
            )
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

    def mark_verified(
        self, model_id: str, ok: bool, *, actor: str = "model-probe"
    ) -> None:
        """记录一次「测试连接」结果。

        成功写入时间戳，失败**清空**：之前通过不代表现在还能用（Key 可能刚
        被吊销）。只有带时间戳的模型才允许被设为默认。
        """
        self.get_public(model_id)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE ai_model_configs SET last_verified_at = ? WHERE id = ?",
                    (_now_iso() if ok else None, model_id),
                )
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise self._storage_error(exc) from exc
        self._audit(
            "ai.model_config.probe", actor, model_id, {"ok": bool(ok)}
        )

    def _audit(self, action: str, actor: str, model_id: str, payload: Dict[str, Any]) -> None:
        """审计只记录元数据；**绝不**带 api_key / 密文。"""
        try:
            from src.ai.audit import emit

            emit(
                action,
                module="src.ai.model_config",
                actor=actor or "capability",
                # target 是"对什么做的"：审计列表要能直接显示模型 id。
                target=model_id,
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


# --------------------------------------------------------------------------- #
# 旧 .env 配置 -> 可编辑模型记录（一次性、幂等、可删）
# --------------------------------------------------------------------------- #
_ENV_MIGRATION_MARKER = "env_config_migrated"

#: 旧版通过 ``AI_PROVIDER_TYPE`` 选一套配置；这里只取那一套，不凭空造配置。
_LEGACY_BASE_URL_ENVS: Dict[str, tuple] = {
    "openai": ("OPENAI_BASE_URL",),
    "anthropic": ("ANTHROPIC_BASE_URL",),
    "google": ("GOOGLE_BASE_URL", "GEMINI_BASE_URL"),
    "ollama": ("OLLAMA_BASE_URL", "OLLAMA_HOST"),
    "moonshot": ("MOONSHOT_BASE_URL",),
    "deepseek": ("DEEPSEEK_BASE_URL",),
}

_LEGACY_PROVIDER_LABELS = {
    "openai": "OpenAI 兼容",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "ollama": "本地 Ollama",
    "moonshot": "Moonshot",
    "deepseek": "DeepSeek",
}


def _legacy_env(key: str, default: str = "") -> str:
    """读旧配置：``os.environ`` 优先，其次 ``.env``（经 ConfigManager）。"""
    value = os.environ.get(key)
    if value:
        return str(value).strip()
    try:
        from ..config_manager import get as _cfg_get

        value = _cfg_get(key)
        if value:
            return str(value).strip()
    except Exception:  # noqa: BLE001 - 配置源不可用按未配置处理
        pass
    return default


def _migration_skipped() -> bool:
    flag = (os.environ.get(MODEL_CONFIG_MIGRATE_ENV) or "").strip().lower()
    return flag in {"0", "off", "false", "no"}


def migrate_env_config_to_store(
    store: Optional[ModelConfigStore] = None,
) -> Optional[Dict[str, Any]]:
    """把旧版 ``AI_PROVIDER_*`` 那一套配置搬进可编辑列表（只搬一次）。

    设计约束（与"可改可删、兼容旧数据"直接相关）：

    * **不覆盖用户数据**：库里已有任何模型时只写迁移标记，绝不改写/删除；
    * **只迁移真实存在的一套配置**：``AI_PROVIDER_TYPE`` 缺失、为 ``mock``、
      未知类型或没有模型名时什么都不加；注册表保持为空，``get_provider()``
      继续按旧路径读 ``.env``（向后兼容）；
    * **可删**：迁移成功后写 ``env_config_migrated`` 标记；用户删除这条记录后，
      下次启动不会再自动加回来；
    * **密钥同样加密**：走 ``ModelConfigStore.create``，与界面添加完全同一条路径；
    * **失败不阻断启动**：任何存储异常都只记日志，网关照常启动。
    """
    if _migration_skipped():
        return None
    target = store or get_model_config_store()
    try:
        with target._lock:
            conn = target._connect()
            marker = conn.execute(
                "SELECT value FROM ai_model_config_meta WHERE key = ?",
                (_ENV_MIGRATION_MARKER,),
            ).fetchone()
            if marker is not None:
                return None
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_model_configs"
            ).fetchone()["n"]
            if existing:
                _set_migration_marker(conn)
                return {"migrated": False, "reason": "already_configured"}

            provider = _legacy_env("AI_PROVIDER_TYPE").lower()
            model = _legacy_env("AI_PROVIDER_MODEL")
            if provider == "claude":
                provider = "anthropic"
            elif provider in {"gemini", "google-genai"}:
                provider = "google"
            if (
                provider not in SUPPORTED_PROVIDERS
                or provider == "mock"
                or not model
            ):
                _set_migration_marker(conn)
                return {"migrated": False, "reason": "no_legacy_config"}

            api_key = _legacy_env("AI_PROVIDER_KEY")
            if not api_key:
                from .providers import PROVIDER_KEY_ALIASES

                for alias in PROVIDER_KEY_ALIASES.get(provider, ()):
                    api_key = _legacy_env(alias)
                    if api_key:
                        break

            base_url = ""
            for env_name in _LEGACY_BASE_URL_ENVS.get(provider, ()):
                base_url = _legacy_env(env_name)
                if base_url:
                    break
            if (
                provider == "ollama"
                and base_url
                and not base_url.startswith(("http://", "https://"))
            ):
                base_url = f"http://{base_url}"

            label = _LEGACY_PROVIDER_LABELS.get(provider, provider)
            payload: Dict[str, Any] = {
                "name": f"旧配置 · {label}",
                "provider": provider,
                "model": model,
                "base_url": base_url or None,
                "timeout": 120.0,
                "enabled": True,
                # 不显式传 is_default：新建的模型还没测试通过，不能直接占默认位。
                # 这里库里必为空（上面的 existing 检查），create 会让它成为首个
                # 默认模型，迁移结果与以前一致。
            }
            if api_key:
                payload["api_key"] = api_key
            created = target.create(payload, "env-migration", owner_principal="")
            _set_migration_marker(conn)
        logger.info(
            "legacy AI_PROVIDER_* config migrated into editable model %s (%s/%s)",
            created["id"],
            provider,
            model,
        )
        return {"migrated": True, "model": created}
    except Exception as exc:  # noqa: BLE001 - 迁移失败绝不影响网关启动
        logger.warning(
            "legacy model config migration skipped (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return {"migrated": False, "reason": "migration_failed"}


def _set_migration_marker(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ai_model_config_meta (key, value) VALUES (?, ?)",
        (_ENV_MIGRATION_MARKER, "1"),
    )
    conn.commit()


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
