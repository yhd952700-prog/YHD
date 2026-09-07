"""鎏灏命令行对话入口。

用法：
    # 真实 LLM（本机 ollama，默认模型 qwen2.5:3b）
    python -m src.ai.chat

    # 指定模型 / provider
    python -m src.ai.chat --model qwen2.5:7b
    python -m src.ai.chat --provider mock

    # 会话内命令
    /stats   查看当前会话统计
    /reset   清空会话历史
    /quit    退出（或 Ctrl+C / Ctrl+D）

provider 由环境变量 AI_PROVIDER_TYPE / AI_PROVIDER_MODEL 或命令行参数控制，
显式构造后注入 LiuHaoAssistant（不依赖全局单例的 env 时序）。
"""

from __future__ import annotations

import argparse
import os

from .liuhao import LiuHaoAssistant
from .providers import ProviderFactory

DEFAULT_MODEL = "qwen2.5:3b"


def build_provider(provider_type: str, model: str):
    """构造 provider 实例（显式注入，避免全局单例 env 时序问题）。"""
    return ProviderFactory.create_provider(
        provider_type,
        name="liuhao-assistant",
        model=model,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="鎏灏（LIUHAO X）命令行对话")
    parser.add_argument(
        "--provider",
        default=os.environ.get("AI_PROVIDER_TYPE", "ollama"),
        help="provider 类型（ollama / mock / openai / deepseek / moonshot ...）",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AI_PROVIDER_MODEL", DEFAULT_MODEL),
        help="模型名（默认 qwen2.5:3b）",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="自定义系统提示词（可选，覆盖默认鎏灏身份）",
    )
    return parser


def repl(assistant: LiuHaoAssistant) -> int:
    print("=" * 60)
    print("  鎏灏（LIUHAO X）— AI 操作系统")
    st = assistant.stats()
    print(f"  provider={st['provider']}  model={st['model']}")
    print("  输入消息开始对话；/stats 状态 /reset 清史 /quit 退出")
    print("=" * 60)

    while True:
        try:
            line = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line in ("/quit", "/exit", "/bye", "quit", "exit"):
            break
        if line == "/reset":
            assistant.reset()
            print("（会话历史已清空）")
            continue
        if line == "/stats":
            print(assistant.stats())
            continue

        result = assistant.chat(line)
        tag = "鎏灏" if result["status"] == "completed" else "鎏灏[错误]"
        print(f"{tag}> {result['reply']}")

    print("\n再见。")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        provider = build_provider(args.provider, args.model)
    except Exception as exc:
        print(f"provider 初始化失败（{args.provider}/{args.model}）：{exc}")
        print("提示：本机 ollama 需先 `ollama serve` 并拉取模型；或用 --provider mock 兜底。")
        return 1

    assistant = LiuHaoAssistant(provider=provider, system_prompt=args.system_prompt)
    return repl(assistant)


if __name__ == "__main__":
    raise SystemExit(main())
