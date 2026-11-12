"""救援：在失败路径结束后，保证机器上还能启动一个 known-good。

生成的 ``Rescue-LiuHao.cmd`` 只做三件事：停当前实例、静默重装 known-good、拉起应用。
它落在 ``D:\\LiuHaoSelfUpgrade\\rescue\\``（测试期）或 ``D:\\LiuHaoData\\upgrade\\rescue\\``
（真机期，需 Boss 授权后由安装流程写）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import paths
from .manifest import sha256_file


def write_rescue(
    known_good: dict[str, Any],
    install_dir: Path,
    data_dir: Path,
    *,
    target_dir: Path | None = None,
) -> Path:
    target_dir = Path(target_dir or paths.rescue_dir())
    target_dir.mkdir(parents=True, exist_ok=True)
    installer = Path(str(known_good.get("installer", "")))
    if not installer.is_file():
        raise FileNotFoundError(f"known-good 安装包不存在：{installer}")

    runtime = Path(install_dir) / "runtime" / "python.exe"
    desktop = Path(install_dir) / "scripts" / "liuhao_desktop.py"
    cmd_path = target_dir / "Rescue-LiuHao.cmd"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "echo [LiuHao rescue] 安装 known-good ...",
        f'if exist "{runtime}" "{runtime}" -m src.desktop stop --root "{install_dir}" --timeout 30',
        f'"{installer}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="{install_dir}" /DATA="{data_dir}"',
        f'if exist "{runtime}" start "" "{runtime}" "{desktop}" app',
        "echo [LiuHao rescue] 完成。",
    ]
    cmd_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
    (target_dir / "known-good.json").write_text(
        f'{{"installer":"{installer}","sha256":"{sha256_file(installer)}"}}\n',
        encoding="utf-8",
    )
    return cmd_path


def launch(cmd_path: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(["cmd", "/c", str(cmd_path)])
