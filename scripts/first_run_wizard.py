#!/usr/bin/env python
"""首次运行向导（自用版分发）。

目标
----
自用版默认 fail-closed：没有注册人类身份 → 没人能登录；没有配置模型 → 聊天
路由无可用 provider。双击启动后用户面对一个"进不去"的界面，体验是断的。

这个向导在启动器拉起网关**之前**跑一遍，把最小闭环补齐：

1. 检测是否已有可登录的人类身份；没有就引导创建一个管理员（复用
   ``scripts/register_human_identity.py`` 的注册 + 凭据写入 + 登录校验逻辑）。
2. 引导选择 provider（Ollama 本地 / OpenAI 兼容云），填模型名；API Key 写入
   模型配置存储的**加密** SQLite（AES-256-GCM，``src.ai.model_config``），
   **不**写进 ``.env`` 明文。
3. 每一步都可跳过；跳过向导会明确打印"现在无法登录 / 聊天不可用"，而不是
   装作一切就绪。

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
    """True 表示首启向导还有必须由人完成的事（账号或模型）。"""
    return not (_has_loginable_human() and _has_enabled_model())


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
    print("鎏灏首次运行向导")
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
        print("     在「系统设置 → 模型管理」里添加，或重跑本向导。")
        return 0

    # 已配置完整（有可登录身份 + 有启用模型）→ 不打扰，直接退出。
    # 这样双击 LiuHao.bat 时向导只在真正需要时弹出来。
    if _has_loginable_human() and _has_enabled_model():
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


def _run_interactive_setup() -> int:
    print()
    print("=" * 62)
    print("数据与联网说明")
    print(f"  数据目录：{resolve_data_dir()}")
    print("  账号、对话、知识库、记忆、审计都写在这里。")
    print("  只有 API Key 用 AES-256-GCM 加密；对话、知识库、审计是本地明文 SQLite。")
    print(
        "  备份=退出后复制整个目录（含 data\\model_config.key 与 "
        "data\\model_registry.sqlite3）。"
    )
    print("  卸载默认保留数据，连续两次确认才会删除。")
    print("  默认不联网；只有你配置云端模型后，对话内容与检索到的知识片段才会发到")
    print("  你填写的 base_url。本机 Ollama 的模型推理只连 127.0.0.1，不出网。")
    print("  「联网检索」默认关闭；只有你在「设置 → 隐私与联网」显式开启后，才会把")
    print("  提炼后的检索关键词（最多 48 字）发到 hn.algolia.com、api.github.com、")
    print("  export.arxiv.org 三个公开接口。")
    print("  只发送提炼后的关键词；不发送你的对话内容、对话历史、个人画像、账号信息或 API Key。")
    print("  关闭后立即生效，无需重启。不做统计上报，也不自动检查更新。")
    print("=" * 62)

    # ---- 1. 人类身份 ----
    if _has_loginable_human():
        print()
        print("[1/2] 已有可登录的人类身份，跳过身份创建。")
    else:
        print()
        print("[1/2] 未发现可登录的人类身份 —— 没有它就登不进去。")
        principal = _prompt("管理员账号（英文，如 liuhao）", default="liuhao")
        display = _prompt("显示名（中文可）", default=principal)
        print("  登录密码（输入时不显示）：")
        password = _prompt_secret("  密码: ")
        if not password:
            print("  密码不能为空，跳过身份创建。", file=sys.stderr)
        else:
            ok = register_admin(principal, display, password)
            if ok != 0:
                print("  [FAIL] 身份创建未通过校验（见上方输出）。", file=sys.stderr)

    # ---- 2. 模型 provider ----
    print()
    print("[2/2] 选择模型服务商（决定聊天用哪个模型）。")
    print("  1) Ollama 本地（需先安装 Ollama 并执行 ollama pull <model>）")
    print("  2) OpenAI 兼容云（接口地址 + API Key）")
    print("  0) 跳过（没 Key 也能先装；之后在「系统设置 → 模型管理」添加）")
    print("     DeepSeek Key 申请：https://platform.deepseek.com →「API Keys」")
    choice = _prompt("选择", default="0")
    if choice == "1":
        model = _prompt("模型名（如 qwen2.5:7b）", default="qwen2.5:7b")
        base_url = _prompt("Ollama 地址", default=OLLAMA_DEFAULT_BASE_URL)
        try:
            result = configure_ollama(model, base_url)
            print(f"  [ok] 已登记 Ollama 模型 {result['id']}（{result['model']}）")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 登记模型失败: {exc}", file=sys.stderr)
    elif choice == "2":
        base_url = _prompt("接口地址 base_url（以 /v1 结尾，如 https://api.deepseek.com/v1）")
        model = _prompt("模型名（如 deepseek-chat；OpenAI 填 gpt-4o-mini）")
        api_key = _prompt_secret("  API Key: ")
        if not base_url or not model or not api_key:
            print("  接口地址 / 模型名 / API Key 缺一不可，跳过。", file=sys.stderr)
        else:
            try:
                result = configure_openai_compatible(base_url, model, api_key)
                print(f"  [ok] 已登记模型 {result['id']}（{result['model']}），"
                      "API Key 已加密存储。")
                print("       Key 保存在数据目录的 data\\model_registry.sqlite3；"
                      "解密密钥 data\\model_config.key 在同目录，仅当前 Windows 用户可读。")
                print("       删除：系统设置 → 模型管理 → 删除该模型（Key 会一起删除）。")
                print("       联网：对话内容会直接发送到你填写的 base_url，"
                      "不经过鎏灏服务器。")
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] 登记模型失败: {exc}", file=sys.stderr)
    else:
        print("  [skip] 未配置模型；安装与启动不受影响，"
              "之后可在「系统设置 → 模型管理」添加。")

    print()
    print("=" * 62)
    print("向导完成。接着会自动启动服务；服务真正就绪后才会打印可访问地址")
    print("并尝试打开浏览器。请以窗口里给出的地址为准（不要沿用旧书签）。")
    print("下一步：用刚创建的账号登录，聊天入口在左侧「工作台」→「直接对话」。")
    print("=" * 62)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="鎏灏首次运行向导")
    parser.add_argument("--skip", action="store_true", help="跳过向导，打印当前缺口后退出")
    args = parser.parse_args(argv)
    return run_wizard(skip=args.skip)


if __name__ == "__main__":
    raise SystemExit(main())
