"""``app.env`` 里的 ``SECRET_KEY``：与 JWT 密钥同一套 DPAPI 包裹 + 旧值自动迁移。

为什么可以迁移
--------------
当前代码库里 ``SECRET_KEY`` **没有任何签名/加密消费者**：登录令牌由
``LIUHAO_JWT_SECRET`` 签发（见 ``src/security/jwt_handler.py``），下载与重置
链接走 Bearer/独立随机令牌（见 ``src/gateway/local_proof.py``）。因此把它
从明文换成机器绑定密文，不会让任何已签发的凭据失效。

运行时仍把**明文**注入进程环境（``os.environ``）：任何按环境变量读取
``SECRET_KEY`` 的代码（包括将来接入的插件）行为与以前完全一致；落盘时才
是 ``LIUHAO-DPAPI-1:`` 密文。换机器时解不开就**不注入**，只留中文告警，
原文件不覆盖——预检会因此显示「缺少会话加密密钥」，不会静默复用一份
别人的密钥。
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import MutableMapping, Optional

from .encryption import (
    MachineBoundKeyError,
    is_machine_bound_payload,
    unwrap_machine_bound,
    wrap_machine_bound,
)

logger = logging.getLogger(__name__)

SECRET_KEY_ENV = "SECRET_KEY"

#: DPAPI 熵：不同用途的密钥密文不可互换，即使同为本机用户可解。
SECRET_KEY_DPAPI_PURPOSE = b"secret-key-v1"

#: 换机器 / 换 Windows 用户时解不开的提示。必须说清"不影响什么"，
#: 否则用户会误以为登录态、下载链接、重置链接一起废了。
_CROSS_MACHINE_TEXT = (
    "本机密钥无法在此机器解密（SECRET_KEY），原文件未被覆盖。"
    "请用账号备份包恢复或重新生成。\n"
    "       说明：登录令牌、下载与重置链接不依赖该值，本次仍可正常使用；\n"
    "       本次不注入 SECRET_KEY，安全预检会显示「SECRET_KEY 未设置」。\n"
    "       可执行步骤：\n"
    "         1. 若这是从旧电脑拷过来的数据目录，请回到原电脑导出带口令的账号备份包；\n"
    "         2. 在新电脑导入后重启，SECRET_KEY 会用新电脑的 DPAPI 重新绑定；\n"
    "         3. 若原电脑已不可用，可删除 config/app.env 里的 SECRET_KEY 行后重启，"
    "系统会重新生成（旧值本就不参与任何签名，不会影响登录）。\n"
    "       文件：%s"
)


def _env_value_parts(body: str) -> tuple[str, str]:
    name, sep, value = body.partition("=")
    return name.strip(), (value.strip().strip('"').strip("'") if sep else "")


def _migrate_plain_line(path: Path, name: str, plain: str) -> bool:
    """把明文行原子替换成 ``LIUHAO-DPAPI-1:`` 形态；失败绝不覆盖原文件。"""
    if os.name != "nt":
        return False
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    target = None
    for index, line in enumerate(lines):
        line_name, value = _env_value_parts(line.rstrip("\r\n"))
        if line_name == name and value == plain:
            target = index
            break
    if target is None:
        return False
    tmp: Optional[Path] = None
    try:
        wrapped = wrap_machine_bound(
            plain.encode("utf-8"), purpose=SECRET_KEY_DPAPI_PURPOSE
        ).decode("ascii")
        lines[target] = f"{name}={wrapped}{newline}"
        tmp = path.with_name(
            f".{path.name}.migrate-{os.getpid()}-{secrets.token_hex(4)}.tmp"
        )
        tmp.write_text("".join(lines), encoding="utf-8", newline="")
        try:
            from ..common.windows_acl import tighten_current_user_only

            tighten_current_user_only(tmp)
        except Exception:  # noqa: BLE001 - ACL 加固失败不阻断迁移
            pass
        os.replace(tmp, path)
        tmp = None
        logger.info("SECRET_KEY 已迁移为 DPAPI 机器绑定格式：%s", path)
        return True
    except Exception as exc:  # noqa: BLE001 - 迁移失败不得阻断启动
        logger.warning(
            "SECRET_KEY 自动迁移失败（%s）：%s；本次继续使用明文密钥运行，"
            "原文件未被覆盖。下次启动会再次尝试。",
            path,
            exc,
        )
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def ensure_secret_key(
    path: Path, *, environ: Optional[MutableMapping[str, str]] = None
) -> Optional[str]:
    """读取并（必要时）迁移 ``SECRET_KEY``，返回本次运行应使用的明文。

    * 文件里是明文 → 注入环境变量，并原子迁移成 DPAPI 形态；
    * 文件里是密文 → 解封后注入明文（调用方/子进程看到的行为不变）；
    * 解不开（换机/换用户）→ 不注入、不覆盖，中文告警，返回 ``None``；
    * 调用方已显式注入一个**不同**的值 → 以调用方为准，不碰文件。

    返回 ``None`` 只表示"本次没有可用的 SECRET_KEY"，不代表启动失败。
    """
    env = os.environ if environ is None else environ
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return env.get(SECRET_KEY_ENV)

    value = ""
    for line in text.splitlines():
        name, parsed = _env_value_parts(line)
        if name == SECRET_KEY_ENV:
            value = parsed
            break
    if not value:
        return env.get(SECRET_KEY_ENV)

    explicit = env.get(SECRET_KEY_ENV)
    if explicit and explicit != value:
        return explicit

    if is_machine_bound_payload(value):
        try:
            plain_bytes, _mode = unwrap_machine_bound(
                value, purpose=SECRET_KEY_DPAPI_PURPOSE
            )
        except MachineBoundKeyError:
            env.pop(SECRET_KEY_ENV, None)
            logger.warning(_CROSS_MACHINE_TEXT, path)
            return None
        plain = plain_bytes.decode("utf-8", "ignore")
        env[SECRET_KEY_ENV] = plain
        return plain

    env[SECRET_KEY_ENV] = value
    _migrate_plain_line(path, SECRET_KEY_ENV, value)
    return value
