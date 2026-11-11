"""自装与冒烟：只允许测试目录，复用仓库已有的 ``install_smoke_e2e.ps1``。

静默安装参数与已知坑都收敛在这里：

* 用 ``D:\\cache\\pwsh\\pwsh.exe``（7.x）；PowerShell 5.1 会把 UTF-8 无 BOM 当 ANSI。
* ``/VERYSILENT /SUPPRESSMSGBOXES /NORESTART``，``-SkipUninstall -KeepRunning`` 让装完的
  测试实例留在原地，供回滚/救援继续用。
* 静默安装不会自动拉起应用（``skipifsilent``），冒烟脚本内部会显式启动。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths
from .manifest import sha256_file
from .models import SmokeResult


def resolve_pwsh() -> str:
    for candidate in (
        os.environ.get("LIUHAO_PWSH", ""),
        r"D:\cache\pwsh\pwsh.exe",
        shutil.which("pwsh.exe") or "",
        shutil.which("powershell.exe") or "",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "powershell.exe"


def find_free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 40)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{preferred} 附近找不到空闲端口")


def installed_runtime(install_dir: Path) -> Path | None:
    runtime = Path(install_dir) / "runtime" / "python.exe"
    return runtime if runtime.is_file() else None


def installed_version(install_dir: Path) -> str:
    """读安装树里的 ``src/version.py``；读不到返回空串，由调用方判失败。"""
    path = Path(install_dir) / "src" / "version.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def _has_playwright(python: str) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", "import playwright"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _find_playwright_python(repo: Path) -> str | None:
    for candidate in (Path(repo) / ".venv" / "Scripts" / "python.exe", Path(sys.executable)):
        if candidate.is_file() and _has_playwright(str(candidate)):
            return str(candidate)
    return None


def _health_ok(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


def start_installed(
    install_dir: Path,
    data_dir: Path,
    port: int,
    *,
    timeout: int = 90,
) -> subprocess.Popen[bytes] | None:
    """冒烟脚本收尾后会停掉实例；六页面检查前按同一套启动口径再拉起来。"""
    runtime = installed_runtime(install_dir)
    launcher = Path(install_dir) / "scripts" / "launch_installed.py"
    if not runtime or not launcher.is_file():
        return None
    env = dict(os.environ)
    env["LIUHAO_ALLOW_ENV_OVERRIDE"] = "1"
    env["LIUHAO_DATA_DIR"] = str(data_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen(
        [str(runtime), "-u", str(launcher), "--skip-wizard", "--api-port", str(port)],
        cwd=str(install_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        if _health_ok(port):
            return proc
        time.sleep(0.5)
    proc.terminate()
    return None


def check_console_pages(
    repo: Path,
    port: int,
    principal: str,
    password: str,
    evidence_root: Path,
    *,
    install_dir: Path | None = None,
    data_dir: Path | None = None,
    timeout: int = 300,
) -> tuple[str, str]:
    """六个页面存在性检查。返回 ``(state, detail)``，state ∈ pass/fail/not_verified。"""
    python = _find_playwright_python(Path(repo))
    if python is None:
        return "not_verified", "未安装 playwright（无可用解释器），六页面未验证"
    started_here = None
    if not _health_ok(port):
        if install_dir is None or data_dir is None:
            return "not_verified", f"端口 {port} 无实例，六页面未验证"
        started_here = start_installed(install_dir, data_dir, port)
        if started_here is None:
            return "not_verified", f"端口 {port} 实例未能拉起，六页面未验证"
    env = dict(os.environ)
    env["LIUHAO_PAGES_PASSWORD"] = password
    try:
        proc = subprocess.run(
            [
                python,
                "-m",
                "src.selfupgrade.pages_check",
                "--base",
                f"http://127.0.0.1:{port}/",
                "--principal",
                principal,
            ],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "not_verified", f"六页面检查超时（{timeout}s）"
    finally:
        if started_here is not None:
            stop_installed(install_dir)
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    try:
        Path(evidence_root).mkdir(parents=True, exist_ok=True)
        (Path(evidence_root) / "pages_check.txt").write_text(output, encoding="utf-8")
    except OSError:
        pass
    if proc.returncode == 0:
        return "pass", output.splitlines()[-1] if output else "六个页面均可进入"
    if proc.returncode == 1:
        return "fail", output
    return "not_verified", output


def stop_installed(install_dir: Path, timeout: int = 60) -> None:
    runtime = installed_runtime(install_dir)
    if not runtime:
        return
    subprocess.run(
        [str(runtime), "-m", "src.desktop", "stop", "--root", str(install_dir), "--timeout", str(timeout)],
        cwd=str(install_dir),
        capture_output=True,
        check=False,
        timeout=timeout + 30,
    )


def uninstall(install_dir: Path) -> None:
    unins = Path(install_dir) / "unins000.exe"
    if not unins.is_file():
        return
    subprocess.run(
        [str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        capture_output=True,
        check=False,
        timeout=300,
    )


def install_and_smoke(
    repo: Path,
    installer: Path,
    install_dir: Path,
    data_dir: Path,
    port: int,
    evidence_root: Path,
    *,
    admin_password: str = "",
    timeout: int = 900,
) -> SmokeResult:
    install_dir, data_dir = paths.assert_safe_test_target(install_dir, data_dir)
    installer = Path(installer)
    if not installer.is_file():
        return SmokeResult(False, 2, False, f"安装包不存在：{installer}")

    script = Path(repo) / "scripts" / "acceptance" / "install_smoke_e2e.ps1"
    if not script.is_file():
        return SmokeResult(False, 2, False, f"缺少冒烟脚本：{script}")

    evidence_root = Path(evidence_root)
    evidence_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_pwsh(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-InstallerPath",
        str(installer),
        "-InstallDir",
        str(install_dir),
        "-DataDir",
        str(data_dir),
        "-Port",
        str(port),
        "-SkipUninstall",
        "-KeepRunning",
        "-EvidenceRoot",
        str(evidence_root),
    ]
    # 显式给测试实例一个固定口令：下一次升级要能用同一凭据登录做六页面检查，
    # 随机口令只活在本次进程里，升级后无法复用。只用于测试目录。
    if admin_password:
        cmd += ["-AdminPassword", admin_password]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        code = proc.returncode
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired as exc:
        code = 3
        output = f"安装/冒烟超时（{timeout}s）：{exc}"

    installed = installed_runtime(install_dir) is not None
    tail = "\n".join([line for line in output.splitlines() if line.strip()][-20:])
    ok = code == 0 and installed
    detail = (
        f"install={installer.name} sha256={sha256_file(installer)[:12]}… "
        f"exit={code} installed={installed} elapsed={time.monotonic() - started:.1f}s"
    )
    if not ok:
        detail += f"\n{tail}"
    return SmokeResult(ok, code, installed, detail, str(evidence_root))
