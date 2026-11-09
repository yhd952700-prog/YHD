"""Real startup verification for the post-install self-check.

Importing the app only proves the module is syntactically importable. The
failures users actually hit happen *during startup*: a taken port, a broken
migration, a missing dependency, an exception in the lifespan. So this module
starts the installed ASGI app for real, on a temporary port, with the full
uvicorn lifespan, and waits for a health endpoint before declaring the release
healthy.

Isolation rules (the probe must never disturb the running installation):

* the code is copied from the app root into a throw-away sandbox, so
  ``Path(__file__).parents[...]``-derived data directories point into the
  sandbox; copies (never hardlinks) keep the probe from ever mutating the
  live install, even if the app rewrites a file it loaded;
* ``HOME``/``USERPROFILE`` and ``LIUHAO_DATA_DIR`` are redirected into the
  sandbox as well;
* the port is an ephemeral free port (unless a test pins one on purpose);
* the child writes its log to a sandbox file, and the sandbox is deleted
  afterwards.

Failure classification is deliberately keyword based and ordered: port conflict,
dependency import, migration, lifespan, timeout, shutdown. It is a diagnostic
aid, not a machine-readable contract: the readable reason and the (redacted)
log tail are always kept so a human can confirm.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from ..common.redaction import redact_text

# Failure kinds surfaced to the user and the ledger.
PORT_CONFLICT = "port_conflict"
DEPENDENCY_IMPORT = "dependency_import"
MIGRATION_FAILED = "migration_failed"
LIFESPAN_FAILED = "lifespan_failed"
TIMEOUT = "timeout"
SHUTDOWN_FAILED = "shutdown_failed"
STARTUP_FAILED = "startup_failed"

_FAILURE_LABELS = {
    PORT_CONFLICT: "端口冲突",
    DEPENDENCY_IMPORT: "依赖导入失败",
    MIGRATION_FAILED: "数据库迁移失败",
    LIFESPAN_FAILED: "lifespan 启动异常",
    TIMEOUT: "启动超时",
    SHUTDOWN_FAILED: "关闭异常",
    STARTUP_FAILED: "启动失败",
}

_MAX_LOG_CHARS = 20_000
_MAX_LOG_LINES = 200

_MIRROR_EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}

_RUNNER = r'''
import importlib
import os
import sys
import threading
import time
import traceback

import uvicorn

module_name, attr, host, port, stop_file = sys.argv[1:6]


def main() -> None:
    try:
        module = importlib.import_module(module_name)
    except BaseException as exc:
        traceback.print_exc()
        print(
            "selfcheck: dependency import failed: %s: %s" % (type(exc).__name__, exc),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)
    app = getattr(module, attr, None)
    if app is None:
        print(
            "selfcheck: service attribute %r missing on module %r"
            % (attr, module_name),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)

    config = uvicorn.Config(
        app,
        host=host,
        port=int(port),
        log_level="info",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def watch_stop_file() -> None:
        while not os.path.exists(stop_file):
            time.sleep(0.05)
        server.should_exit = True

    threading.Thread(target=watch_stop_file, daemon=True).start()
    try:
        server.run()
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
'''


@dataclass
class StartupCheckConfig:
    """How to start the service for the self-check.

    ``port`` is only for diagnostics/tests; production always uses a free
    ephemeral port so two installations never collide.
    """

    host: str = "127.0.0.1"
    port: Optional[int] = None
    # Order matters: every path except ``fallback_health_path`` is a
    # *preferred* health endpoint, retried round after round.  The fallback is
    # never used merely because startup is slow -- see
    # ``health_fallback_after_missing_rounds``.
    health_paths: Tuple[str, ...] = (
        "/v1/health",
        "/health",
        "/healthz",
        "/openapi.json",
    )
    fallback_health_path: str = "/openapi.json"
    startup_timeout_seconds: float = 90.0
    shutdown_timeout_seconds: float = 15.0
    # A single request must not be cut below a cold-start import: the first
    # ``/v1/health`` on a slow machine routinely takes (well) over one second.
    # The retry budget, not a tiny timeout, is what keeps the probe honest.
    health_timeout_seconds: float = 1.0
    # Preferred-health retry policy, all in one place:
    #   * one round = every preferred path once (3 shipped paths -> <=3.0s);
    #   * the preferred phase lasts at most this many seconds (further capped
    #     by ``startup_timeout_seconds``), i.e. roughly
    #     90 / (3 x 1.0 + 0.25) ~= 27 rounds of full timeouts;
    #   * the fallback (``/openapi.json``) is tried only after
    #     ``health_fallback_after_missing_rounds`` consecutive rounds in which
    #     *all* preferred paths explicitly answered 404/405.  A timeout,
    #     connection failure or 5xx means "not ready yet", never "missing".
    #     The fallback attempt itself does not latch either: a cold first
    #     ``/openapi.json`` can exceed the per-request timeout (FastAPI builds
    #     the schema lazily), so it is retried on later qualifying rounds
    #     instead of turning into a spurious startup timeout.
    health_probe_budget_seconds: float = 90.0
    health_probe_interval_seconds: float = 0.25
    health_fallback_after_missing_rounds: int = 2
    mirror_paths: Tuple[str, ...] = (
        "src",
        "config",
        "console",
        "pyproject.toml",
        "requirements.txt",
        "oss-registry.yaml",
        "LIUHAO_RELEASE.json",
    )
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class StartupReport:
    ok: bool
    failure_kind: str = ""
    reason: str = ""
    port: int = 0
    health_path: str = ""
    http_status: int = 0
    health_body: Dict[str, object] = field(default_factory=dict)
    # True only when the preferred health endpoints were explicitly absent and
    # the probe had to accept ``/openapi.json`` instead.  The service is
    # reachable, but a mounted route table does not prove the lifespan or the
    # databases finished starting -- callers must show this, not hide it.
    health_path_is_fallback: bool = False
    startup_seconds: float = 0.0
    exit_code: Optional[int] = None
    shutdown_ok: bool = False
    log_tail: str = ""

    @property
    def failure_label(self) -> str:
        return _FAILURE_LABELS.get(self.failure_kind, "")

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "failure_kind": self.failure_kind,
            "failure_label": self.failure_label,
            "reason": self.reason,
            "port": self.port,
            "health_path": self.health_path,
            "health_path_is_fallback": self.health_path_is_fallback,
            "http_status": self.http_status,
            "health_body": dict(self.health_body),
            "startup_seconds": round(self.startup_seconds, 3),
            "exit_code": self.exit_code,
            "shutdown_ok": self.shutdown_ok,
            "log_tail": self.log_tail,
        }


class _LogCapture:
    """Read the child log file as UTF-8 without failing on odd bytes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _redact(text: str) -> str:
    """与诊断包共用同一套脱敏：用户名 / 环境变量名 / 脚本路径 / 各类密钥。

    占位符统一成 ``<已脱敏>``，保持启动探针既有报告的可读性。
    """
    return redact_text(text or "").replace("***REDACTED***", "<已脱敏>")


def _cap(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > _MAX_LOG_LINES:
        lines = lines[-_MAX_LOG_LINES:]
        text = "\n".join(lines)
    if len(text) > _MAX_LOG_CHARS:
        text = text[-_MAX_LOG_CHARS:]
    return text.strip()


def redact_and_cap_log(text: str) -> str:
    """Public helper so the manager can persist the same redacted tail."""
    return _cap(_redact(text or ""))


def _copy_file(source: Path, target: Path) -> None:
    """Copy, never hardlink: a hardlinked config/database would be shared with
    the live install, and a single write in the sandbox could destroy it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _mirror_tree(source_root: Path, sandbox: Path, names: Iterable[str]) -> None:
    for name in names:
        source = source_root / name
        if not source.exists():
            continue
        if source.is_file():
            _copy_file(source, sandbox / name)
            continue
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames[:] = [
                item for item in dirnames if item not in _MIRROR_EXCLUDED_DIRS
            ]
            rel = Path(dirpath).relative_to(source_root)
            (sandbox / rel).mkdir(parents=True, exist_ok=True)
            for filename in filenames:
                if filename.endswith(".pyc"):
                    continue
                _copy_file(Path(dirpath) / filename, sandbox / rel / filename)


def _mirror_top_level_modules(source_root: Path, sandbox: Path) -> None:
    """Mirror top-level modules that are imported by their bare name.

    ``src`` modules sometimes import root-level helpers (``from error_types
    import ProviderError``), which resolve only because production runs with
    the app root as cwd/PYTHONPATH. Without them the sandbox startup fails
    where the real install succeeds.
    """
    for path in source_root.glob("*.py"):
        if path.is_file():
            _copy_file(path, sandbox / path.name)


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _rewrite_app_root_paths(env: Dict[str, str], app_root: Path, sandbox: Path) -> None:
    """Relocate absolute paths that point into the live installation.

    Deployments configure databases by absolute path (e.g.
    ``LIUHAO_HUMAN_IDENTITIES_DB=<app_root>/data/...sqlite3``). Inheriting
    those would make the self-check read and write the user's real data,
    which is exactly what the probe must never do. Any value mentioning the
    app root is rewritten to the sandbox root, keeping its relative shape.
    """
    pairs = [
        (str(app_root), str(sandbox)),
        (app_root.as_posix(), sandbox.as_posix()),
    ]
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    for key, value in list(env.items()):
        if not isinstance(value, str) or not value:
            continue
        replaced = value
        for needle, replacement in pairs:
            if needle and needle.lower() in replaced.lower():
                replaced = re.sub(
                    re.escape(needle),
                    lambda _match, _replacement=replacement: _replacement,
                    replaced,
                    flags=re.IGNORECASE,
                )
        if replaced != value:
            env[key] = replaced


def _child_env(
    sandbox: Path,
    config: StartupCheckConfig,
    data_dir: Path,
    home_dir: Path,
    app_root: Path,
) -> Dict[str, str]:
    env = os.environ.copy()
    _rewrite_app_root_paths(env, app_root, sandbox)
    parts = [str(sandbox), str(sandbox / "src")]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["LIUHAO_DATA_DIR"] = str(data_dir)
    env["LIUHAO_SELF_CHECK"] = "1"
    console_dir = sandbox / "console"
    if (console_dir / "index.html").is_file():
        # 安装树把前端放在 <app_root>/console；自检沙箱里也要让网关指向它，
        # 否则只会起 API，验证不到“升级后界面能打开”。
        env["LIUHAO_CONSOLE_DIST"] = str(console_dir)
    env.update({str(key): str(value) for key, value in config.env.items()})
    return env


_LEVEL_PREFIX = re.compile(r"^(?:ERROR|INFO|WARNING|DEBUG|CRITICAL):\s+")


def _tail_error_line(log_tail: str, preferred: Iterable[str] = ()) -> str:
    """Pick the line that best explains the failure.

    uvicorn prefixes its own chatter with a level, so the traceback lines
    (which carry the real cause) are the ones *without* such a prefix. The
    final unprefixed line of a traceback is the exception itself.
    """
    lines = [line.strip() for line in log_tail.splitlines() if line.strip()]
    unprefixed = [line for line in lines if not _LEVEL_PREFIX.match(line)]

    def matches(line: str, words: Iterable[str]) -> bool:
        lowered = line.lower()
        return any(str(word).lower() in lowered for word in words)

    if preferred:
        # The class keyword is the strongest signal, whether or not the line
        # carries a level prefix: uvicorn's own "ERROR: ... attempting to bind"
        # is prefixed, while a traceback's "RuntimeError: ..." is not.
        for line in reversed(lines):
            if matches(line, preferred):
                return line[:300]
    if unprefixed:
        return unprefixed[-1][:300]
    for line in reversed(lines):
        lowered = line.lower()
        if (
            "error:" in lowered
            or "exception" in lowered
            or "failed" in lowered
            or "失败" in line
        ):
            return line[:300]
    return lines[-1][:300] if lines else ""


def classify_failure(
    log_tail: str,
    *,
    port: int,
    timed_out: bool,
    exit_code: Optional[int],
    probe_rounds: int = 0,
) -> Tuple[str, str]:
    """Map the log tail onto one readable failure class."""
    lowered = log_tail.lower()
    error_line = _tail_error_line(log_tail)

    if (
        "error while attempting to bind" in lowered
        or "address already in use" in lowered
        or "10048" in lowered
        or "only one usage of each socket address" in lowered
        or "errno 98" in lowered
    ):
        bind_line = _tail_error_line(
            log_tail, ("attempting to bind", "address already in use", "10048")
        )
        detail = f"（{bind_line}）" if bind_line else ""
        return PORT_CONFLICT, f"端口冲突：端口 {port} 已被占用，服务无法绑定{detail}"

    if (
        "modulenotfounderror" in lowered
        or "no module named" in lowered
        or "importerror" in lowered
        or "cannot import name" in lowered
        or "dependency import failed" in lowered
    ):
        import_line = _tail_error_line(
            log_tail,
            ("modulenotfounderror", "no module named", "importerror", "dependency import failed"),
        )
        detail = f"：{import_line}" if import_line else ""
        return DEPENDENCY_IMPORT, f"依赖导入失败{detail}"

    if (
        "migrat" in lowered
        or "alembic" in lowered
        or "数据库迁移" in log_tail
        or "升级脚本" in log_tail
    ):
        migration_line = _tail_error_line(
            log_tail, ("migrat", "alembic", "数据库迁移", "升级脚本")
        )
        detail = f"：{migration_line}" if migration_line else ""
        return MIGRATION_FAILED, f"数据库迁移失败{detail}"

    if (
        "application startup failed" in lowered
        or "lifespan" in lowered
        or "startup failed" in lowered
    ):
        detail = f"：{error_line}" if error_line else ""
        return LIFESPAN_FAILED, f"lifespan 启动异常{detail}"

    if timed_out:
        retried = f"（健康检查已重试 {probe_rounds} 轮）" if probe_rounds else ""
        detail = f"（最后日志：{error_line}）" if error_line else ""
        return TIMEOUT, f"启动超时：未在限定时间内就绪{retried}{detail}"

    detail = f"：{error_line}" if error_line else ""
    return STARTUP_FAILED, f"服务启动失败（退出码 {exit_code}）{detail}"


_HEALTH_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _parse_json_object(raw: bytes) -> Dict[str, object]:
    """Best-effort JSON body parse; a non-object body is treated as empty."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _probe_health(
    host: str, port: int, paths: Tuple[str, ...], timeout: float
) -> Tuple[str, int, Dict[str, object], bool]:
    """Try every path once.

    Returns ``(path, status, body, all_missing)``.  ``path`` is non-empty only
    for a 2xx answer **whose body is a JSON object**; a 200 that serves an
    HTML page (the console SPA catch-all) is not health and is treated like an
    absent route.  ``all_missing`` is True only when *every* path answered
    404/405 (or a non-JSON 200), i.e. the endpoints are effectively absent; a
    timeout, a connection failure or a 5xx all mean "the service is not ready
    yet", and must not be mistaken for absence (that is how ``/openapi.json``
    used to be accepted while the app was still starting).
    """
    if not paths:
        return "", 0, {}, False
    missing = 0
    for path in paths:
        url = f"http://{host}:{port}{path}"
        raw = b""
        status = 0
        try:
            with _HEALTH_OPENER.open(url, timeout=timeout) as response:
                status = int(getattr(response, "status", 0) or 0)
                # Drain the body before closing: an abrupt close while the
                # server is still writing resets the TCP connection, and on
                # Windows that reset can make uvicorn's graceful shutdown hang
                # in `wait_closed()` (asyncio proactor). The bytes are also the
                # health payload used for the front-end contract check.
                try:
                    raw = response.read()
                except Exception:
                    raw = b""
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            finally:
                exc.close()
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if 200 <= status < 400:
            body = _parse_json_object(raw)
            # 2xx 不等于健康：控制台 SPA 的兜底路由会把 /health、/healthz
            # 这类未注册路径返回 index.html 的 200（text/html），body 也不是
            # JSON。如果把它当作健康，自检会拿着空 body 去跑前端契约门禁，
            # 在“旧服务仍在运行”时稳定地把一次正常升级判成失败。
            # 只接受可解析的 JSON 对象；空对象同样不算（没有任何健康字段），
            # 继续尝试下一个路径/下一轮，直到预算用完。这种 200 等价于
            # “该路由不存在”（SPA 兜底），因此计入 missing：若所有路径都
            # 这样，仍然允许 /openapi.json 兜底，而不是把 SPA 首页当健康。
            if body:
                return path, status, body, False
            missing += 1
            continue
        if status in (404, 405):
            missing += 1
    return "", 0, {}, missing == len(paths)


def run_startup_check(
    app_root: Path | str,
    *,
    module: str,
    attr: str,
    config: Optional[StartupCheckConfig] = None,
) -> StartupReport:
    """Start the installed service on a temp port and verify it becomes healthy."""
    config = config or StartupCheckConfig()
    root = Path(app_root)
    sandbox = Path(
        tempfile.mkdtemp(prefix=f"liuhao-selfcheck-{os.getpid()}-")
    )
    process: Optional[subprocess.Popen] = None
    log_file = sandbox / ".startup.log"
    stop_file = sandbox / ".stop"
    started = time.monotonic()

    try:
        _mirror_tree(root, sandbox, config.mirror_paths)
        _mirror_top_level_modules(root, sandbox)
        # A regular package always wins over namespace-package portions found
        # on a later sys.path entry, so a missing `src/__init__.py` in the
        # sandbox would let some other `src` package on PYTHONPATH shadow it.
        src_dir = sandbox / "src"
        if src_dir.is_dir() and not (src_dir / "__init__.py").is_file():
            (src_dir / "__init__.py").write_text("", encoding="utf-8")
        data_dir = sandbox / "data"
        home_dir = sandbox / "_home"
        data_dir.mkdir(parents=True, exist_ok=True)
        home_dir.mkdir(parents=True, exist_ok=True)
        stop_file.unlink(missing_ok=True)

        port = int(config.port) if config.port else _free_port(config.host)
        runner = sandbox / ".selfcheck_runner.py"
        runner.write_text(_RUNNER, encoding="utf-8")

        with log_file.open("wb") as log_handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(runner),
                    module,
                    attr,
                    config.host,
                    str(port),
                    str(stop_file),
                ],
                cwd=str(sandbox),
                env=_child_env(sandbox, config, data_dir, home_dir, root),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

            deadline = time.monotonic() + max(1.0, float(config.startup_timeout_seconds))
            allowed_paths = tuple(config.health_paths)
            fallback_path = (
                config.fallback_health_path
                if config.fallback_health_path in allowed_paths
                else ""
            )
            preferred_paths = tuple(
                path for path in allowed_paths if path != fallback_path
            )
            if not preferred_paths:
                # A caller that configured only the fallback still wants a
                # probe; there is nothing left to fall back *to*.
                preferred_paths, fallback_path = allowed_paths, ""

            # Preferred-health phase: retry round after round.  The phase is
            # capped by both the startup budget and the probe budget, so the
            # rounds are bounded no matter how fast a single round fails.
            probe_deadline = min(
                deadline,
                time.monotonic() + max(1.0, float(config.health_probe_budget_seconds)),
            )
            request_timeout = max(0.2, float(config.health_timeout_seconds))
            probe_interval = max(0.05, float(config.health_probe_interval_seconds))
            health_path = ""
            http_status = 0
            health_body: Dict[str, object] = {}
            health_path_is_fallback = False
            timed_out = False
            probe_rounds = 0
            missing_rounds = 0
            while time.monotonic() < probe_deadline:
                if process.poll() is not None:
                    break
                probe_rounds += 1
                path, status, body, all_missing = _probe_health(
                    config.host, port, preferred_paths, request_timeout
                )
                if path:
                    health_path, http_status, health_body = path, status, body
                    break
                if all_missing:
                    missing_rounds += 1
                    # Only an explicitly absent endpoint may trigger the
                    # fallback.  A timeout or 5xx keeps retrying the real
                    # health paths until the budget runs out.  The fallback
                    # probe is retried on every qualifying round as well: a
                    # cold first ``/openapi.json`` can exceed the per-request
                    # timeout, and latching that single attempt would turn a
                    # slow-but-healthy service into a spurious timeout.
                    if (
                        fallback_path
                        and missing_rounds
                        >= max(1, int(config.health_fallback_after_missing_rounds))
                    ):
                        path, status, body, _ = _probe_health(
                            config.host, port, (fallback_path,), request_timeout
                        )
                        if path:
                            health_path = path
                            http_status = status
                            health_body = body
                            health_path_is_fallback = True
                            break
                else:
                    missing_rounds = 0
                remaining = probe_deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(probe_interval, remaining))
            else:
                timed_out = True

        capture = _LogCapture(log_file)
        if not health_path:
            exit_code = process.poll()
            if exit_code is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            log_tail = redact_and_cap_log(capture.read())
            if exit_code is None and not timed_out:
                timed_out = True
            kind, reason = classify_failure(
                log_tail,
                port=port,
                timed_out=timed_out,
                exit_code=exit_code,
                probe_rounds=probe_rounds,
            )
            return StartupReport(
                ok=False,
                failure_kind=kind,
                reason=reason,
                port=port,
                startup_seconds=time.monotonic() - started,
                exit_code=exit_code,
                log_tail=log_tail,
            )

        stop_file.write_text("stop", encoding="utf-8")
        shutdown_ok = False
        exit_code: Optional[int] = None
        try:
            exit_code = process.wait(timeout=max(1.0, float(config.shutdown_timeout_seconds)))
            shutdown_ok = exit_code == 0
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if health_path_is_fallback:
            # Append only after the child has exited: it still owns the same
            # file through its stdout handle, and writing earlier would let a
            # later uvicorn line overwrite the marker.
            try:
                with log_file.open("a", encoding="utf-8") as marker:
                    marker.write(
                        "selfcheck: preferred health paths "
                        f"{'/'.join(preferred_paths)} were explicitly absent; "
                        f"accepted fallback {health_path} (route table only, "
                        "lifespan not proven)\n"
                    )
            except OSError:
                pass
        log_tail = redact_and_cap_log(capture.read())
        if shutdown_ok:
            return StartupReport(
                ok=True,
                port=port,
                health_path=health_path,
                http_status=http_status,
                health_body=health_body,
                health_path_is_fallback=health_path_is_fallback,
                startup_seconds=time.monotonic() - started,
                exit_code=exit_code,
                shutdown_ok=True,
                log_tail=log_tail,
            )
        # The service *did* become healthy; whatever the log says afterwards,
        # the failure is that it would not shut down cleanly.
        error_line = _tail_error_line(log_tail)
        detail = f"：{error_line}" if error_line else ""
        return StartupReport(
            ok=False,
            failure_kind=SHUTDOWN_FAILED,
            reason=f"关闭异常：服务未能正常关闭（退出码 {exit_code}）{detail}",
            port=port,
            health_path=health_path,
            http_status=http_status,
            health_body=health_body,
            health_path_is_fallback=health_path_is_fallback,
            startup_seconds=time.monotonic() - started,
            exit_code=exit_code,
            shutdown_ok=False,
            log_tail=log_tail,
        )
    except OSError as exc:
        return StartupReport(
            ok=False,
            failure_kind=STARTUP_FAILED,
            reason=f"无法启动自检子进程：{exc}",
            startup_seconds=time.monotonic() - started,
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(sandbox, ignore_errors=True)


def failure_label(kind: str) -> str:
    return _FAILURE_LABELS.get(kind, "")
