#!/usr/bin/env python
"""首次运行向导（控制台简化版，一键安装版用）。

只做三件事（都是大白话，不问技术细节）：

1. 建一个管理员账号（用户名 + 密码）。密码必须输两次，两次不一样就
   重输，输空不许通过；建号失败就大声失败并停住，不许返回成功继续走。
2. 给工作台起个名字：直接回车就用默认「贾维斯」。
3. 选模型：默认跳过。跳过后在数据目录写一个标记文件，以后每次启动
   不再追问；登录后在控制台「系统设置 → 模型管理」里再配。没配模型
   时聊天只会老实提示「未配置模型，请先配置模型密钥」，不编假回复。

不问 Ollama 安装，不问 API Key / base_url——配模型留到控制台里做。

可跳过（``--skip``），适合 CI / 已知已配置的环境。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.ai.model_config import get_model_config_store  # noqa: E402
from src.logging_config import resolve_data_dir  # noqa: E402
from src.kernels.identity import (  # noqa: E402
    HUMAN_IDENTITIES_FILE_ENV,
    IdentityManager,
    is_human_identity,
)
from src.kernels.identity._persistence import (  # noqa: E402
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    resolve_human_identity_store,
)

#: 登录凭据存储（与 register_human_identity 一致）。
AUTH_SECRETS_FILE_ENV = "LIUHAO_AUTH_SECRETS_FILE"
DEFAULT_SECRETS_PATH = REPO_ROOT / "config" / "auth_secrets.json"
DEFAULT_IDENTITY_FILE = REPO_ROOT / "config" / "human_identities.json"

#: Ollama 默认地址（本机常见端口）。
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# 环境/存储探测
# ---------------------------------------------------------------------------


def _point_at_local_stores() -> None:
    """把本机 config/ 下的登记表/凭据指给内核（与启动器行为一致）。"""
    if not os.environ.get(HUMAN_IDENTITIES_FILE_ENV):
        os.environ[HUMAN_IDENTITIES_FILE_ENV] = str(DEFAULT_IDENTITY_FILE)


class WizardAborted(RuntimeError):
    """向导需要人输入，但当前终端不可交互（或输入流已关闭）。"""


def stdin_is_interactive() -> bool:
    """当前进程能不能真的等人敲键盘（pythonw/隐藏窗口/管道都不行）。"""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def needs_setup() -> bool:
    """True 表示首启向导还有必须由人完成的事（账号或模型）。

    模型跳过也算「做完了」：用户跳过一次后写了标记文件，以后启动
    不再追问（登录后在控制台设置里再配）。
    """
    return not (_has_loginable_human() and _has_model_or_skip())


#: 模型跳过标记：放在数据目录里（``%LOCALAPPDATA%\\LiuHao\\data`` 或
#: ``LIUHAO_DATA_DIR``）。用户在向导里跳过选模型后写下这个文件，
#: 以后每次启动都不再追问；登录后在控制台「系统设置 → 模型管理」里再配。
MODEL_SKIP_MARKER_NAME = "model_setup_skipped"


def _model_skip_marker() -> Path:
    return resolve_data_dir() / MODEL_SKIP_MARKER_NAME


def _model_setup_skipped() -> bool:
    """True 表示用户之前已在向导里跳过选模型（标记文件还在）。"""
    try:
        return _model_skip_marker().is_file()
    except OSError:  # noqa: BLE001 - 标记读不了就当没跳过，下次再问一次
        return False


def _mark_model_skipped() -> bool:
    """写下「已跳过选模型」标记；写不进去时返回 False（下次启动再问一次）。"""
    try:
        marker = _model_skip_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "skipped by first-run wizard; configure a model in console settings.\n",
            encoding="utf-8",
        )
        return True
    except OSError as exc:  # noqa: BLE001 - 标记写不进去不是致命错
        print(f"  [!!] 跳过标记写不进去（{exc}），下次启动可能还会问一次选模型。", file=sys.stderr)
        return False


def _has_model_or_skip() -> bool:
    """True 表示已有启用模型，或者用户已明确跳过选模型。"""
    return _has_enabled_model() or _model_setup_skipped()


def needs_account_setup() -> bool:
    """True 表示还没有可登录的人类身份，首次初始化必须由人完成。

    图形化首启只关心这一件事：模型 Key 是可选项（可跳过），不能因为它把
    用户拉回命令行。账号判定本身仍复用 ``_has_loginable_human``，与 CLI
    向导和启动器完全一致。
    """
    return not _has_loginable_human()


def _has_loginable_human() -> bool:
    """True 当且仅当至少有一个**真的能登录**的人类身份。

    "登记表里有记录"不等于"能登录"：登录还要在凭据库里查到这个 principal。
    现场踩过的坑是登记表里有 smoke 行、凭据库里却没有对应密码，向导据此
    跳过建号，用户最后拿到一个永远登不进去的登录页。
    """
    _point_at_local_stores()
    try:
        manager = IdentityManager()
    except Exception:  # noqa: BLE001 - 探测失败就当没有
        return False
    identities = manager.list_identities() if hasattr(manager, "list_identities") else []
    principals = {
        str(getattr(item, "principal", "") or "").strip()
        for item in identities
        if is_human_identity(item)
    }
    principals.discard("")
    if not principals:
        return False
    # nosemgrep: liuhao-swallow-exception-return-success
    try:
        from src.gateway.auth import resolve_secret_store

        credentials = resolve_secret_store().load()
    except Exception:  # noqa: BLE001 - 凭据库读不了时不把配置误判成"没人"
        # 结辩：凭据库读不了（损坏/权限/后端异常）时按「已配置过」处理，
        # 避免把现有安装误判成首启而重复弹向导、覆盖既有凭据；
        # True 语义是保守默认，不是谎报成功。
        return True
    return any(principal in credentials for principal in principals)


def _has_enabled_model() -> bool:
    """True 当且仅当模型配置存储里至少有一个启用中的模型。"""
    try:
        store = get_model_config_store()
        return any(m.get("enabled") for m in store.list_models())
    except Exception:  # noqa: BLE001 - 探测失败就当没有
        return False


def _ensure_integrity_key() -> bool:
    """确保 ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` 有值。

    没有这个 key 时，注册表的每一行在加载时都会被内核拒绝（fail-closed，
    HC-11 / U6）—— 也就是说"注册成功"但内核一个人都不承认。

    密钥由 ``src/common/integrity_key`` 统一生成，落在**数据目录**
    （``config/integrity.key``，run 时权限 0600/仅当前用户），不再写进程序
    目录的 ``.env``；密钥值不会打印。返回 True 表示已有或已生成。
    """
    from src.common.integrity_key import ensure_integrity_key, resolve_data_dir

    try:
        key = ensure_integrity_key(
            data_dir=resolve_data_dir(),
            registry_path=DEFAULT_IDENTITY_FILE if DEFAULT_IDENTITY_FILE.is_file() else None,
        )
    except OSError as exc:
        print(f"  [FAIL] 无法生成/写入身份完整性密钥: {exc}", file=sys.stderr)
        return False
    os.environ[HUMAN_IDENTITIES_INTEGRITY_KEY_ENV] = key
    return True


# ---------------------------------------------------------------------------
# 身份注册（复用 register_human_identity 的逻辑）
# ---------------------------------------------------------------------------


def register_admin(
    principal: str,
    display_name: Optional[str],
    password: str,
    recovery_out: Optional[List[str]] = None,
) -> int:
    """注册一个管理员人类身份并写入登录凭据。

    复用 ``register_human_identity.cmd_register`` 的完整逻辑（注册表、凭据、
    内核往返校验、真实登录校验），不重造轮子。``recovery_out`` 用于把
    「只展示一次」的恢复码回传给调用方（图形向导）以便当场展示。
    """
    import scripts.register_human_identity as rhi

    store = resolve_human_identity_store()
    from src.gateway.auth import resolve_secret_store

    secret_store = resolve_secret_store()
    args = argparse.Namespace(
        file=None, backend=None, db=None, secrets_file=None,
    )
    return rhi.cmd_register(
        store, secret_store, args, principal, display_name, ["admin"], password,
        recovery_out=recovery_out,
    )


# ---------------------------------------------------------------------------
# 模型配置
# ---------------------------------------------------------------------------


def _save_model(payload: dict[str, Any]) -> dict[str, Any]:
    """保存模型配置；同名时更新而不是报错（首启向导可能被重新提交）。

    不带 ``is_default``：新建模型必须先用「测试连接」验过，才允许占用默认位
    （见 ``model_config.create`` 的 409）；向导完成页会做这次真实测试，
    通过后再设为默认。
    """
    store = get_model_config_store()
    name = str(payload["name"])
    existing = next(
        (item for item in store.list_models() if item.get("name") == name), None
    )
    if existing:
        return store.update(existing["id"], payload, actor="first-run-wizard")
    return store.create(payload, actor="first-run-wizard")


def configure_ollama(model_name: str, base_url: str = OLLAMA_DEFAULT_BASE_URL) -> dict[str, Any]:
    """登记一个 Ollama 模型（本地，无密钥）。"""
    return _save_model(
        {
            "name": f"ollama:{model_name}",
            "provider": "ollama",
            "model": model_name,
            "base_url": base_url,
            "enabled": True,
        }
    )


def configure_openai_compatible(
    base_url: str, model_name: str, api_key: str,
) -> dict[str, Any]:
    """登记一个 OpenAI 兼容模型，API Key 经 AES-256-GCM 加密后落盘。"""
    return _save_model(
        {
            "name": model_name,
            "provider": "openai",
            "model": model_name,
            "base_url": base_url,
            "api_key": api_key,
            "enabled": True,
        }
    )


# ---------------------------------------------------------------------------
# 交互向导
# ---------------------------------------------------------------------------


def _prompt(label: str, default: Optional[str] = None) -> str:
    if not stdin_is_interactive():
        raise WizardAborted("首启向导需要可交互的终端才能输入。")
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{label}{suffix}: ").strip()
    except EOFError as exc:  # 窗口被关掉 / stdin 被重定向
        raise WizardAborted("输入流已关闭（命令行窗口被关掉或没有控制台）。") from exc
    return value or (default or "")


def run_wizard(skip: bool = False) -> int:
    print("=" * 62)
    print("贾维斯首次运行向导")
    print("=" * 62)

    # 先补完整性密钥：没有它，注册表每一行在加载时都会被内核拒绝，
    # 既读不到已有身份，新建的也通不过 round-trip 校验。
    if not _ensure_integrity_key():
        print("[FAIL] 无法生成/写入人类身份完整性密钥，向导终止。", file=sys.stderr)
        return 1

    if skip:
        print()
        print("[skip] 已跳过向导。")
        print()
        if not _has_loginable_human():
            print("  !! 当前没有可登录的人类身份，登录页将无法通过。")
            print("     补建一个管理员身份（在程序目录下执行）：")
            print("       runtime\\python.exe scripts\\register_human_identity.py "
                  "--principal <name> --password-stdin")
        print("  !! 若未配置模型，聊天功能不可用。")
        print("     登录后在「系统设置 → 模型管理」里添加。")
        return 0

    # 已配置完整（有可登录身份 + 有启用模型或已明确跳过选模型）→ 不打扰，
    # 直接退出。这样双击启动时向导只在真正需要时弹出来。
    if _has_loginable_human() and _has_model_or_skip():
        print("[ok] 已检测到管理员身份和模型配置，跳过首次运行向导。")
        return 0

    # 到这里必须有真人输入：无控制台就直接给出可执行的下一步，而不是抛
    # EOFError 后留一个一闪而过的黑框。
    if not stdin_is_interactive():
        print()
        print("[FAIL] 首次运行需要在命令行窗口里完成向导，但当前没有可交互的终端。")
        print("       请从开始菜单/桌面快捷方式启动（会打开命令行窗口）后重试。")
        return 2

    try:
        return _run_interactive_setup()
    except WizardAborted as exc:
        print()
        print(f"[FAIL] {exc}")
        print("       请从开始菜单/桌面快捷方式启动（会打开命令行窗口）后重试。")
        return 2
    except (EOFError, KeyboardInterrupt):
        print()
        print("[FAIL] 向导被中断，首启未完成；请重新双击启动。")
        return 2


def _prompt_secret(label: str) -> str:
    if not stdin_is_interactive():
        raise WizardAborted("首启向导需要可交互的终端才能输入密码。")
    try:
        return getpass.getpass(label)
    except EOFError as exc:
        raise WizardAborted("输入流已关闭（命令行窗口被关掉或没有控制台）。") from exc


def _prompt_new_password() -> str:
    """让用户输两次新密码：输空不许通过，两次不一样就重输，直到通过为止。"""
    while True:
        print("  登录密码（输入时不显示）：")
        first = _prompt_secret("  密码: ")
        if not first:
            print("  密码不能为空，请重新输入。")
            continue
        second = _prompt_secret("  再输一次密码: ")
        if first != second:
            print("  两次输入的密码不一样，请重新输入。")
            continue
        return first


def _run_interactive_setup() -> int:
    print()
    print("=" * 62)
    print("贾维斯首次设置：只做三件事，做完就自动启动。")
    print(f"  数据目录：{resolve_data_dir()}")
    print("  账号、对话、知识库都存在这里。")
    print("=" * 62)

    # ---- 1. 管理员账号 ----
    # 建号失败必须停住（返回 1），不许装作成功继续启动：否则用户拿到
    # 一个能打开但永远登不进去的登录页。
    if _has_loginable_human():
        print()
        print("[1/3] 已有可登录的账号，跳过建账号。")
    else:
        print()
        print("[1/3] 先建一个管理员账号（以后就用它登录）。")
        principal = _prompt("管理员账号（英文，如 liuhao）", default="liuhao")
        display = _prompt("显示名（中文可）", default=principal)
        password = _prompt_new_password()
        try:
            ok = register_admin(principal, display, password)
        except Exception as exc:  # noqa: BLE001 - 建号异常必须停住
            print(f"  [FAIL] 建账号失败：{exc}", file=sys.stderr)
            print("  启动已停住。请重新双击启动，再试一次。", file=sys.stderr)
            return 1
        if ok != 0:
            print("  [FAIL] 建账号没有通过校验（见上方输出）。", file=sys.stderr)
            print("  启动已停住。请重新双击启动，再试一次。", file=sys.stderr)
            return 1
        print("  [ok] 管理员账号建好了。")

    # ---- 2. 工作台名字（可改，不是写死）----
    # 顶栏、登录页和助手都显示这个名字；默认贾维斯，别人用时自己起一个。
    print()
    print("[2/3] 给工作台起个名字（顶栏、登录页和助手都显示这个名字）。")
    print("  直接回车就用默认「贾维斯」；以后随时在控制台「系统设置 → 工作台名字」里改。")
    name_zh = _prompt("工作台名字（中文）", default="贾维斯")
    name_en = _prompt("英文名（可空，留空则与中文名相同）")
    from src.brand import BRAND_NAME_EN, BRAND_NAME_ZH, set_workspace_name

    if name_zh.strip() and (
        name_zh.strip() != BRAND_NAME_ZH
        or (name_en.strip() and name_en.strip() != BRAND_NAME_EN)
    ):
        try:
            # 名字存在本次数据目录的 workspace_settings.json 里：显式路径
            # 未设置时就按向导本次的数据目录算，与身份/模型配置落点一致。
            os.environ.setdefault(
                "LIUHAO_WORKSPACE_SETTINGS_FILE",
                str(Path(resolve_data_dir()) / "workspace_settings.json"),
            )
            result = set_workspace_name(name_zh, name_en or None)
            print(f"  [ok] 工作台名字已设为 {result['lockup']}。")
        except ValueError as exc:
            print(
                f"  [!!] 名字没保存（{exc}），仍用默认「贾维斯」；之后可在设置里改。",
                file=sys.stderr,
            )

    # ---- 3. 选模型（默认跳过）----
    # 跳过就是跳过：不问 Ollama 安装，不问 API Key / base_url，配模型
    # 留到登录后在控制台「系统设置 → 模型管理」里做。跳过后写标记文件，
    # 以后每次启动不再重新弹出追问。
    print()
    if _has_enabled_model():
        print("[3/3] 已有启用的模型，跳过选模型。")
    elif _model_setup_skipped():
        print("[3/3] 上次已跳过选模型，不再问。")
    else:
        print("[3/3] 选模型：这一步可以跳过。")
        print("  直接回车就跳过；登录后在控制台「系统设置 → 模型管理」里再配。")
        _prompt("直接回车就跳过选模型")
        if _mark_model_skipped():
            print("  [ok] 已跳过选模型；以后启动不再问。")

    print()
    print("=" * 62)
    print("向导完成。接着会自动启动服务；服务真正就绪后才会打印可访问地址")
    print("并尝试打开浏览器。请以窗口里给出的地址为准（不要沿用旧书签）。")
    print("下一步：用刚创建的账号登录，聊天入口在左侧「自由对话」→「直接对话」。")
    # 收尾显式检查（不新增向导步骤）：模型是可选项，但"没配"必须在完成时
    # 说清楚 —— 未配置时聊天不会编造回答，只会返回"未配置模型，请先配置
    # 模型密钥"；不要让用户在完成页以为"向导走完了 = 聊天就能用了"。
    if not _has_enabled_model():
        print()
        print("[!!] 未配置模型：聊天目前不可用。")
        print("     发送消息只会收到「未配置模型，请先配置模型密钥」的提示，")
        print("     不会生成模拟回答。配好后无需重装：")
        print("       登录后在「系统设置 → 模型管理」填写密钥并测试连通。")
    print("=" * 62)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="贾维斯首次运行向导")
    parser.add_argument("--skip", action="store_true", help="跳过向导，打印当前缺口后退出")
    args = parser.parse_args(argv)
    return run_wizard(skip=args.skip)


if __name__ == "__main__":
    raise SystemExit(main())
