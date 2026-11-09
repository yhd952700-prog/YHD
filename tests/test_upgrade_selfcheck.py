"""Post-install self-check: real startup, failure classes, auto-rollback.

The gate must catch what users actually hit -- a taken port, a broken
migration, a missing dependency, an exception in the lifespan -- so these
tests start a real ASGI app in a child process and assert on the reported
failure class, not merely on a boolean.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.upgrade import ApplyError, LocalDirectorySource, UpgradeConfig, UpgradeManager
from src.upgrade.dataguard import capture_table_inventory
from src.upgrade.dataversion import DATA_SCHEMA_VERSION
from src.upgrade.filemanifest import build_file_manifest_for_zip
from src.upgrade.selfcheck import SelfCheckConfig, run_self_check
from src.upgrade.startup_probe import (
    _probe_health,
    _rewrite_app_root_paths,
    DEPENDENCY_IMPORT,
    LIFESPAN_FAILED,
    MIGRATION_FAILED,
    PORT_CONFLICT,
    STARTUP_FAILED,
    TIMEOUT,
    StartupCheckConfig,
    classify_failure,
    failure_label,
    redact_and_cap_log,
)

# The sandbox writes must stay in the sandbox: a relative write follows the
# sandbox cwd, and LIUHAO_DATA_DIR is redirected there.
HEALTHY_SERVICE = """
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app):
    Path("cwd_marker.txt").write_text("started", encoding="utf-8")
    Path(os.environ["LIUHAO_DATA_DIR"], "data_marker.txt").write_text(
        "started", encoding="utf-8"
    )
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}
"""

MIGRATION_FAILURE_SERVICE = """
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app):
    raise RuntimeError(
        "database migration failed: password=hunter2 could not run alembic"
    )
    yield


app = FastAPI(lifespan=lifespan)
"""

LIFESPAN_FAILURE_SERVICE = """
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app):
    raise ValueError("configuration missing: LIUHAO_MODE is not set")
    yield


app = FastAPI(lifespan=lifespan)
"""

MISSING_DEPENDENCY_SERVICE = """
import definitely_missing_module_xyz  # noqa: F401
"""

# Writes to an absolute path handed over through the environment, so the
# test can prove inherited absolute paths are relocated into the sandbox.
ENV_POINTER_SERVICE = """
import os
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()

_pointer = os.environ.get("LIUHAO_SELF_CHECK_PROBE_DB")
if _pointer:
    Path(_pointer).write_text("written-by-check", encoding="utf-8")


@app.get("/health")
def health():
    return {"ok": True}
"""

BLOCKING_LIFESPAN_SERVICE = """
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app):
    time.sleep(10)
    yield


app = FastAPI(lifespan=lifespan)
"""

# A cold first import: the first two health requests take longer than the
# per-request timeout (1.0s), the third answers immediately.  The probe must
# keep retrying the real endpoint and report /v1/health, not /openapi.json.
SLOW_THEN_HEALTHY_SERVICE = """
import threading
import time

from fastapi import FastAPI

app = FastAPI()
_lock = threading.Lock()
_calls = [0]


@app.get("/v1/health")
def health():
    with _lock:
        _calls[0] += 1
        attempt = _calls[0]
    if attempt <= 2:
        time.sleep(1.5)
    return {"ok": True}
"""

# No health endpoint at all: FastAPI still serves /openapi.json, which is the
# only evidence left.  It must be accepted *and* flagged as a fallback.
FALLBACK_ONLY_SERVICE = """
from fastapi import FastAPI

app = FastAPI()
"""

# 控制台 SPA 的兜底路由：任何未注册路径都返回 index.html 的 200。旧探针把
# 这种 200 当健康，于是正在运行旧服务时的自检报“通过”，升级后却拿不到真实
# /v1/health。这里必须只有 /v1/health 的 JSON 才算健康。
SPA_CATCHALL_WITH_REAL_HEALTH_SERVICE = """
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.get("/v1/health")
def health():
    return {"status": "ok"}


@app.get("/{full_path:path}")
def spa(full_path: str):
    return HTMLResponse("<!doctype html><html><body>console</body></html>")
"""

_SPA_HTML = b"<!doctype html><html><body>console</body></html>"


class _RouteHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, bytes, str]] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        status, body, content_type = self.routes.get(
            self.path, (404, b"", "text/plain")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _http_routes(routes: dict[str, tuple[int, bytes, str]]):
    handler = type("_Handler", (_RouteHandler,), {"routes": routes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_probe_rejects_spa_html_and_only_accepts_real_json_health() -> None:
    """2xx 不等于健康：SPA 的 index.html 200 与空 JSON 都不能算。"""
    paths = ("/health", "/healthz", "/v1/health")
    with _http_routes(
        {
            "/health": (200, _SPA_HTML, "text/html"),
            "/healthz": (200, b"{}", "application/json"),
            "/v1/health": (200, b'{"status": "ok"}', "application/json"),
        }
    ) as port:
        path, status, body, all_missing = _probe_health(
            "127.0.0.1", port, paths, 2.0
        )

    assert path == "/v1/health"
    assert status == 200
    assert body == {"status": "ok"}
    assert all_missing is False


def test_probe_treats_spa_200_html_as_absent_so_openapi_fallback_still_works() -> None:
    """所有首选路径都被 SPA 兜底成 200 时，仍允许 /openapi.json 兜底。"""
    preferred = ("/v1/health", "/health", "/healthz")
    with _http_routes(
        {
            "/v1/health": (200, _SPA_HTML, "text/html"),
            "/health": (200, _SPA_HTML, "text/html"),
            "/healthz": (200, b"{}", "application/json"),
            "/openapi.json": (200, b'{"openapi": "3.1.0"}', "application/json"),
        }
    ) as port:
        path, status, body, all_missing = _probe_health(
            "127.0.0.1", port, preferred, 2.0
        )
        assert (path, status, body, all_missing) == ("", 0, {}, True)
        fallback = _probe_health("127.0.0.1", port, ("/openapi.json",), 2.0)

    assert fallback[0] == "/openapi.json"
    assert fallback[2] == {"openapi": "3.1.0"}


def test_self_check_ignores_spa_catchall_and_reports_the_real_health_path(
    tmp_path: Path,
) -> None:
    app_root = _make_app(
        tmp_path / "app", service=SPA_CATCHALL_WITH_REAL_HEALTH_SERVICE
    )

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert report.ok, report.to_dict()
    assert startup.health_path == "/v1/health"
    assert startup.health_body == {"status": "ok"}
    assert startup.health_path_is_fallback is False


def _write_service(root: Path, body: str) -> None:
    gateway = root / "src" / "gateway"
    gateway.mkdir(parents=True, exist_ok=True)
    (gateway / "main.py").write_text(body, encoding="utf-8")


def _make_app(
    root: Path, *, version: str = "0.1.0", service: str = HEALTHY_SERVICE
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_service(root, service)
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    return root


def _release(
    release_dir: Path,
    *,
    version: str = "0.2.0",
    service: str | None = None,
) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/app.py": "VALUE = 2\n",
    }
    if service is not None:
        files["src/gateway/main.py"] = service
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
        "files": build_file_manifest_for_zip(zip_path),
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    (release_dir / "LIUHAO_RELEASE.json").write_text(
        json.dumps({"schema": "liuhao.release-manifest/1", "releases": [release]}),
        encoding="utf-8",
    )
    return release


def _rows(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Real startup: success and isolation
# ---------------------------------------------------------------------------


def test_real_startup_passes_health_check_and_exits_cleanly(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    data_dir = app_root / "data"
    expected = capture_table_inventory([data_dir])
    config = SelfCheckConfig()

    report = run_self_check(
        app_root, [data_dir], expected_tables=expected, config=config
    )

    assert report.ok, report.to_dict()
    assert report.service_checked and report.service_ok
    startup = report.startup
    assert startup is not None
    assert startup.ok
    assert startup.health_path in config.startup.health_paths
    assert startup.http_status == 200
    assert startup.health_path_is_fallback is False
    assert startup.port > 0
    assert startup.exit_code == 0
    assert startup.shutdown_ok
    assert startup.startup_seconds > 0
    assert "真实启动" in report.service_detail
    assert report.databases_checked == 1
    assert report.tables_checked == 1
    assert report.issues == []


def test_slow_first_health_requests_still_report_the_primary_path(
    tmp_path: Path,
) -> None:
    """Two timed-out cold requests must not push the probe to /openapi.json."""
    app_root = _make_app(tmp_path / "app", service=SLOW_THEN_HEALTHY_SERVICE)

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert report.ok, report.to_dict()
    assert startup.health_path == "/v1/health"
    assert startup.http_status == 200
    assert startup.health_path_is_fallback is False


def test_fallback_is_only_used_when_primary_paths_are_absent_and_is_flagged(
    tmp_path: Path,
) -> None:
    """A 404 on every preferred path is explicit absence: fallback is honest."""
    app_root = _make_app(tmp_path / "app", service=FALLBACK_ONLY_SERVICE)

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert report.ok, report.to_dict()
    assert startup.health_path == "/openapi.json"
    assert startup.http_status == 200
    assert startup.health_path_is_fallback is True
    assert "兜底路径" in report.service_detail
    assert "fallback" in startup.log_tail


def test_startup_never_touches_the_live_data_directory(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    data_dir = app_root / "data"
    before = sorted(path.name for path in data_dir.iterdir())

    report = run_self_check(app_root, [data_dir])
    assert report.ok, report.to_dict()

    # The service wrote its markers into the sandbox, not into the install.
    assert not (app_root / "cwd_marker.txt").exists()
    assert not (data_dir / "data_marker.txt").exists()
    assert sorted(path.name for path in data_dir.iterdir()) == before


def test_inherited_absolute_app_paths_are_redirected_to_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = _make_app(tmp_path / "app", service=ENV_POINTER_SERVICE)
    real_db = app_root / "data" / "inherited.sqlite3"
    # This is how deployments configure data: an absolute path into the install.
    monkeypatch.setenv("LIUHAO_SELF_CHECK_PROBE_DB", str(real_db))

    report = run_self_check(app_root, [app_root / "data"])

    assert report.ok, report.to_dict()
    assert not real_db.exists()


def test_rewrite_app_root_paths_keeps_unrelated_values(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    sandbox = tmp_path / "sandbox"
    env = {
        "LIUHAO_HUMAN_IDENTITIES_DB": str(app_root / "data" / "humans.sqlite3"),
        "LIUHAO_AUTH_SECRETS_FILE": (app_root / "config" / "auth_secrets.json").as_posix(),
        "UNRELATED": "C:/somewhere/else/file.db",
        "EMPTY": "",
    }

    _rewrite_app_root_paths(env, app_root, sandbox)

    assert env["LIUHAO_HUMAN_IDENTITIES_DB"].startswith(str(sandbox))
    assert env["LIUHAO_AUTH_SECRETS_FILE"].startswith(sandbox.as_posix())
    assert env["UNRELATED"] == "C:/somewhere/else/file.db"
    assert env["EMPTY"] == ""


# ---------------------------------------------------------------------------
# Failure classes
# ---------------------------------------------------------------------------


def test_port_conflict_is_named_as_port_conflict(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = int(occupied.getsockname()[1])

        report = run_self_check(
            app_root,
            [app_root / "data"],
            config=SelfCheckConfig(
                startup=StartupCheckConfig(port=port, startup_timeout_seconds=30.0)
            ),
        )

    startup = report.startup
    assert startup is not None
    assert report.ok is False
    assert startup.failure_kind == PORT_CONFLICT
    assert "端口冲突" in startup.reason
    assert str(port) in startup.reason
    assert startup.log_tail


def test_missing_dependency_is_named_as_dependency_import(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app", service=MISSING_DEPENDENCY_SERVICE)

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert startup.failure_kind == DEPENDENCY_IMPORT
    assert "依赖导入失败" in startup.reason
    assert "definitely_missing_module_xyz" in startup.reason
    assert report.ok is False
    assert [issue.kind for issue in report.issues] == ["service"]


def test_migration_failure_is_named_as_migration(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app", service=MIGRATION_FAILURE_SERVICE)

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert startup.failure_kind == MIGRATION_FAILED
    assert "数据库迁移失败" in startup.reason
    # The reason is built from the redacted tail, never from the raw log.
    assert "hunter2" not in startup.reason
    assert "<已脱敏>" in startup.reason
    assert "hunter2" not in startup.log_tail


def test_lifespan_exception_is_named_as_lifespan(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app", service=LIFESPAN_FAILURE_SERVICE)

    report = run_self_check(app_root, [app_root / "data"])

    startup = report.startup
    assert startup is not None
    assert startup.failure_kind == LIFESPAN_FAILED
    assert "lifespan 启动异常" in startup.reason
    # 环境变量名本身也是泄漏面：报告里只能出现占位符。
    assert "LIUHAO_MODE" not in startup.reason
    assert "<环境变量>" in startup.reason
    assert "LIUHAO_MODE" not in startup.log_tail


def test_service_that_never_becomes_ready_is_named_as_timeout(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app", service=BLOCKING_LIFESPAN_SERVICE)

    report = run_self_check(
        app_root,
        [app_root / "data"],
        config=SelfCheckConfig(
            startup=StartupCheckConfig(startup_timeout_seconds=2.0)
        ),
    )

    startup = report.startup
    assert startup is not None
    assert startup.failure_kind == TIMEOUT
    assert "启动超时" in startup.reason


@pytest.mark.parametrize(
    ("log", "timed_out", "exit_code", "expected"),
    [
        (
            "ERROR:    [Errno 10048] error while attempting to bind on address",
            False,
            1,
            PORT_CONFLICT,
        ),
        (
            "Traceback ...\nModuleNotFoundError: No module named 'fastapi_extra'",
            False,
            2,
            DEPENDENCY_IMPORT,
        ),
        (
            "RuntimeError: alembic migration failed on revision abc",
            False,
            0,
            MIGRATION_FAILED,
        ),
        (
            "ERROR:    Application startup failed. Exiting.",
            False,
            0,
            LIFESPAN_FAILED,
        ),
        ("INFO:     Waiting for application startup.", True, None, TIMEOUT),
        ("INFO:     Started server process [12]", False, 3, STARTUP_FAILED),
    ],
)
def test_classify_failure_maps_log_tail_to_one_class(
    log: str, timed_out: bool, exit_code: int | None, expected: str
) -> None:
    kind, reason = classify_failure(
        log, port=8123, timed_out=timed_out, exit_code=exit_code
    )
    assert kind == expected
    assert reason
    assert failure_label(kind)


def test_log_tail_is_redacted_before_being_kept() -> None:
    raw = (
        "Authorization: Bearer abc123secret\n"
        "password=hunter2\n"
        "api_key: sk-live-xyz\n"
        "cookie=session=deadbeef\n"
        "GET http://user:pw123@example.com/v1/health\n"
    )
    tail = redact_and_cap_log(raw)

    for secret in ("abc123secret", "hunter2", "sk-live-xyz", "deadbeef", "pw123"):
        assert secret not in tail
    assert tail.count("<已脱敏>") >= 4


def test_log_tail_is_capped() -> None:
    tail = redact_and_cap_log("\n".join(f"line-{index}" for index in range(400)))
    assert len(tail.splitlines()) <= 200
    assert "line-399" in tail


# ---------------------------------------------------------------------------
# Database checks (service startup disabled on purpose)
# ---------------------------------------------------------------------------


def test_missing_key_table_fails_the_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    data_dir = app_root / "data"
    expected = capture_table_inventory([data_dir])
    conn = sqlite3.connect(data_dir / "app.db")
    conn.execute("DROP TABLE t")
    conn.commit()
    conn.close()

    report = run_self_check(
        app_root,
        [data_dir],
        expected_tables=expected,
        config=SelfCheckConfig(check_service=False),
    )

    assert report.ok is False
    assert any(issue.kind == "table" and "关键表缺失" in issue.detail for issue in report.issues)


def test_unreadable_database_fails_the_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    db = app_root / "data" / "app.db"
    db.write_bytes(b"SQLite format 3\x00" + b"not really a database")

    report = run_self_check(
        app_root,
        [app_root / "data"],
        config=SelfCheckConfig(check_service=False),
    )

    assert report.ok is False
    assert any(issue.kind == "sqlite" and "数据库无法打开" in issue.detail for issue in report.issues)


# ---------------------------------------------------------------------------
# Integration with the manager: pass, and fail -> automatic rollback
# ---------------------------------------------------------------------------


def test_application_records_a_passed_startup_self_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            # 显式钉到应用自己的 data/：测试内的 LIUHAO_DATA_DIR 被隔离到别处，
            # 这里要断言的是"升级状态落在应用数据目录"，不是环境变量。
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
        )
    )
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        stale_log = app_root / "data" / "upgrade-state" / "last-selfcheck.log"
        stale_log.write_text("stale failure log", encoding="utf-8")
        result = manager.apply(download.path, check.release)

        assert result.self_check_passed is True
        assert result.self_check_issues == 0
        assert manager.status()["phase"] == "completed"

        passed = [
            item
            for item in manager.history(kind="selfcheck")
            if item["status"] == "passed"
        ]
        assert passed, manager.history()
        assert passed[0]["detail"]["startup"]["ok"] is True
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        # A stale failure log must not survive a successful check.
        assert not (app_root / "data" / "upgrade-state" / "last-selfcheck.log").exists()
    finally:
        manager.close()


def test_migration_failure_rolls_back_and_keeps_a_redacted_log(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, service=MIGRATION_FAILURE_SERVICE)
    manager = UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            # 显式钉到应用自己的 data/：测试内的 LIUHAO_DATA_DIR 被隔离到别处，
            # 这里要断言的是"升级状态落在应用数据目录"，不是环境变量。
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
        )
    )
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)

        with pytest.raises(ApplyError) as exc:
            manager.apply(download.path, check.release)
        message = str(exc.value)
        assert "自检" in message
        assert "已自动回滚" in message
        assert "数据库迁移失败" in message
        assert "hunter2" not in message
        assert "last-selfcheck.log" in message

        # Version, code bytes and the pre-upgrade database all come back.
        assert "0.1.0" in (app_root / "pyproject.toml").read_text(encoding="utf-8")
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        restored = (app_root / "src" / "gateway" / "main.py").read_text(encoding="utf-8")
        assert "FastAPI" in restored and "migration failed" not in restored
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        assert manager.status()["phase"] == "rolled_back"

        history = manager.history(limit=50)
        assert any(
            item["kind"] == "selfcheck" and item["status"] == "failed" for item in history
        )
        assert any(
            item["kind"] == "apply" and item["status"] == "failed_rolled_back"
            for item in history
        )

        log_path = app_root / "data" / "upgrade-state" / "last-selfcheck.log"
        assert log_path.is_file()
        persisted = log_path.read_text(encoding="utf-8")
        assert "failure_kind: migration_failed" in persisted
        assert "<已脱敏>" in persisted
        assert "hunter2" not in persisted
        # The persisted tail must come from the failed startup, not from data.
        assert "migration failed" in persisted
    finally:
        manager.close()
