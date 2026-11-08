"""一键诊断包：白名单、脱敏、无密钥三条底线。

这些测试只断言"发出去的文件里有什么"，不依赖具体实现细节；新增成员如果
忘了登记白名单，``test_members_are_exactly_the_whitelist`` 会先失败。
"""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

import pytest

from src.common.leak_scan import LOG_MEMBER_ALIAS_NOTE, SCAN_RULE_HIT_NAME
from src.gateway import diagnostic_bundle as bundle
from src.gateway.diagnostic_bundle import (
    BUNDLE_MEMBERS,
    LOG_MEMBER_PREFIX,
    _assert_whitelisted,
    build_bundle,
    mask_user_path,
)


@pytest.fixture()
def fake_probes(monkeypatch):
    """把真实探针换成固定内容：测的是打包与脱敏，不是健康检查本身。"""
    monkeypatch.setattr(
        bundle,
        "_health_payload",
        lambda: {"status": "ok", "service": "liuhao-gateway", "version": "1.0.0"},
    )
    monkeypatch.setattr(
        bundle,
        "_readiness_payload",
        lambda: {
            "status": "degraded",
            "user_message": "有 1 项检查没有通过",
            "checks": {
                "audit_store": {
                    "status": "broken",
                    "user_message": "审计存储不可写，请检查数据目录权限。",
                }
            },
        },
    )
    monkeypatch.setattr(
        bundle,
        "_selfcheck_payload",
        lambda: {"ok": True, "user_message": "自检通过", "findings": []},
    )


def _members(payload) -> dict[str, bytes]:
    data = payload.data if isinstance(payload, bundle.BundleBuild) else payload
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_members_are_exactly_the_whitelist(tmp_path, fake_probes):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text("hello\n", encoding="utf-8")
    (log_dir / "unexpected.exe").write_bytes(b"MZ")

    members = _members(build_bundle(root=tmp_path, log_dir=log_dir))

    assert set(members) == set(BUNDLE_MEMBERS) | {f"{LOG_MEMBER_PREFIX}gateway.log.txt"}
    assert not any(name.endswith(".exe") for name in members)
    # 没有剔除时也要有完整性自证：N 等于包内文件数，M=0。
    readme = members["README.txt"].decode("utf-8")
    assert f"本次包含 {len(members)} 个文件，未包含 0 个" in readme


def test_no_secret_and_no_username_anywhere(tmp_path, fake_probes):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text(
        "\n".join(
            [
                "api_key=sk-live-ABCDEF1234567890",
                "Authorization: Bearer abcdefghijklmnop",
                "password=hunter2",
                r"C:\Users\张三\AppData\Roaming\liuhao\data",
                "/home/alice/liuhao/logs",
            ]
        ),
        encoding="utf-8",
    )

    payload = build_bundle(root=tmp_path, log_dir=log_dir)
    text = "\n".join(data.decode("utf-8", "replace") for data in _members(payload).values())

    for leaked in ("sk-live-ABCDEF1234567890", "abcdefghijklmnop", "hunter2", "张三", "alice"):
        assert leaked not in text, f"诊断包泄漏了 {leaked!r}"
    assert "<用户>" in text
    assert "***REDACTED***" in text


def test_readiness_payload_drops_raw_error_and_technical_detail(monkeypatch):
    """就绪检查的英文异常只属于服务端日志，不属于用户要发出去的文件。"""
    from fastapi.responses import JSONResponse

    import src.gateway.health as health

    async def fake_probe(_request):
        return JSONResponse(
            {
                "status": "degraded",
                "checks": {
                    "audit_store": {
                        "status": "broken",
                        "status_label": "不可用",
                        "user_message": "审计存储不可写，请检查数据目录权限。",
                        "error": "PermissionError: [Errno 13] Permission denied",
                        "technical_detail": "Traceback (most recent call last): ...",
                    }
                },
            }
        )

    monkeypatch.setattr(health, "readiness_probe", fake_probe)
    payload = bundle._readiness_payload()
    check = payload["checks"]["audit_store"]

    assert check["user_message"] == "审计存储不可写，请检查数据目录权限。"
    assert "error" not in check
    assert "technical_detail" not in check


def test_manifest_hashes_match_members(tmp_path, fake_probes):
    members = _members(build_bundle(root=tmp_path, log_dir=tmp_path / "missing"))
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    hashes = {item["name"]: item["sha256"] for item in manifest["members"]}

    for name, data in members.items():
        if name == "manifest.json":
            continue
        assert hashes[name] == hashlib.sha256(data).hexdigest()


def test_readme_says_the_bundle_has_no_keys(tmp_path, fake_probes):
    texts = "\n".join(
        data.decode("utf-8", "replace")
        for data in _members(build_bundle(root=tmp_path, log_dir=tmp_path / "missing")).values()
    )
    assert "这个包不含你的密钥" in texts


def test_summary_reports_runtime_data_dir_not_the_repo_default(
    tmp_path, fake_probes, monkeypatch
):
    """``data_dir`` 必须取运行期生效的目录，并与 ``log_dir`` 同源。

    安装版的数据目录在用户目录下，仓库里的 ``data/`` 只是开发默认值；把开发机
    路径写进诊断包会把排查带偏。这里用 ``LIUHAO_DATA_DIR`` 模拟真实运行目录，
    顺便覆盖"路径含中文/空格时用户名仍被遮罩"。
    """
    data_dir = tmp_path / getpass.getuser() / "张三 数据"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("LIUHAO_LOG_DIR", raising=False)
    monkeypatch.delenv("LIUHAO_LOG_CONFIG", raising=False)

    members = _members(build_bundle(root=tmp_path))
    summary = json.loads(members["summary.json"].decode("utf-8"))

    # 等于运行期实际目录（按既有规则遮掉用户名后的写法）。
    assert summary["data_dir"] == bundle.sanitize_text(str(data_dir.resolve()))
    assert summary["data_dir"].endswith("张三 数据")
    assert summary["data_dir"] != bundle.sanitize_text(bundle.REPO_ROOT / "data")
    # 用户名所在路径段必须被遮；中文与空格目录名保持原样。
    assert "<用户>" in summary["data_dir"]
    assert f"\\{getpass.getuser()}\\" not in summary["data_dir"]
    # 同源：默认日志配置落在数据目录的 logs/ 下，父目录就是 data_dir。
    assert Path(summary["log_dir"]).parent == Path(summary["data_dir"])


def test_unknown_member_is_refused():
    with pytest.raises(ValueError):
        _assert_whitelisted(["summary.json", "../outside.json"])
    with pytest.raises(ValueError):
        _assert_whitelisted(["logs/nested/gateway.log.txt"])


def test_export_is_refused_when_no_member_can_survive(tmp_path, fake_probes, monkeypatch):
    """一个成员都留不下时才整体拒绝（正常只会剔除个别文件）。"""
    monkeypatch.setattr(bundle, "contains_secret", lambda _text: True)
    with pytest.raises(bundle.DiagnosticBundleBlocked) as excinfo:
        build_bundle(root=tmp_path, log_dir=tmp_path / "missing")

    assert excinfo.value.omitted
    assert "没有可安全转发的文件" in str(excinfo.value)


def test_leak_violations_members_are_omitted_not_fatal(tmp_path, fake_probes, monkeypatch):
    """脱敏规则被绕过时，独立的正向泄漏清单负责剔除该成员。

    这里把 ``sanitize_text`` 换成恒等函数（模拟"某条脱敏规则以后回退"），并让
    运行期数据目录落在含真实用户名的位置。含用户名的成员必须被剔除，其余成员
    照常导出——用户仍然拿得到能发给技术支持的包。
    """
    monkeypatch.setattr(bundle, "sanitize_text", lambda value: str(value))
    monkeypatch.setattr(bundle, "resolve_data_dir", lambda: Path("C:/Users/张三/data"))

    build = build_bundle(root=tmp_path, log_dir=tmp_path / "missing")

    assert "summary.json" in build.omitted_names
    text = "\n".join(data.decode("utf-8", "replace") for data in _members(build).values())
    assert "张三" not in text
    assert "REDACTED-MEMBERS.txt" in _members(build)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"C:\Users\alice\data", r"C:\Users\<用户>\data"),
        (r"D:/Users/bob/work", r"D:/Users/<用户>/work"),
        ("/home/carol/liuhao", "/home/<用户>/liuhao"),
        ("/Users/dave/Library", "/Users/<用户>/Library"),
        ("/root/liuhao", "/<用户>/liuhao"),
        # 中文与空格路径：用户名段照样要遮，空格不能被吃掉
        (r"C:\Users\张 三\我的 数据", r"C:\Users\<用户>\我的 数据"),
    ],
)
def test_mask_user_path(raw, expected):
    assert mask_user_path(raw) == expected


def test_bundle_filename_is_timestamped_and_ascii():
    name = bundle.bundle_filename()
    assert name.startswith("liuhao-diagnostics-")
    assert name.endswith(".zip")
    assert name.isascii()


def test_endpoint_returns_a_zip_with_declared_filename(fake_probes):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(bundle.router)
    with TestClient(app) as client:
        response = client.get("/v1/diagnostics/bundle")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-liuhao-diagnostics-filename"].endswith(".zip")
    assert response.content[:2] == b"PK"


def test_real_app_requires_login_before_export():
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        response = client.get("/v1/diagnostics/bundle")

    assert response.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# 导出前自检：脱敏残留（占位符后面的尾巴）必须拦住
# --------------------------------------------------------------------------- #


def test_half_redacted_residual_is_flagged():
    """``sk-***REDACTED***abc123`` 这种"只遮了一半"不能被当成已脱敏。"""
    from src.common.leak_scan import scan_text

    hits = scan_text(
        "Authorization: Bearer sk-***REDACTED***abc123\n"
        '"key": "***REDACTED***tail456"\n'
    )
    assert hits.get("脱敏残留", 0) >= 2


def test_placeholder_alone_is_not_a_leak():
    """完整占位符（后面没有尾巴）不能被误报，否则导出闸会永远红。"""
    from src.common.leak_scan import scan_text

    assert scan_text(
        "Authorization: Bearer sk-***REDACTED***\n"
        "GET /v1/health?key=***REDACTED*** HTTP/1.1\n"
        "Provider initialized: key=configured\n"
    ) == {}


def test_build_bundle_omits_half_redacted_log_and_keeps_the_rest(tmp_path, fake_probes):
    """降级导出：命中的日志被剔除，包仍然生成，且剔除清单准确。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text(
        "Authorization: Bearer sk-***REDACTED***abc123\n", encoding="utf-8"
    )
    (log_dir / "liuhao.log").write_text("hello\n", encoding="utf-8")

    build = build_bundle(root=tmp_path, log_dir=log_dir)
    members = _members(build)

    assert build.omitted_names == (f"{LOG_MEMBER_PREFIX}gateway.log.txt",)
    assert build.omitted[0]["total"] >= 1
    assert f"{LOG_MEMBER_PREFIX}gateway.log.txt" not in members
    assert f"{LOG_MEMBER_PREFIX}liuhao.log.txt" in members
    assert "REDACTED-MEMBERS.txt" in members

    redacted = members["REDACTED-MEMBERS.txt"].decode("utf-8")
    assert f"{LOG_MEMBER_PREFIX}gateway.log.txt：命中 1 处（脱敏残留=1）" in redacted
    readme = members["README.txt"].decode("utf-8")
    assert "为保护你的密钥，1 个日志文件未包含" in readme
    assert "不是病毒或损坏文件" in readme
    # 支持人员点名要 error.log 时，包内 README 必须写清改名映射。
    assert LOG_MEMBER_ALIAS_NOTE in readme
    # 完整性自证：N 等于包内文件数，N + M 就是计划收集总数。
    assert f"本次包含 {len(members)} 个文件，未包含 1 个" in readme

    # 规则命中分布：只有规则名 / 文件 / 次数，不含任何命中内容。
    assert SCAN_RULE_HIT_NAME in members
    report = json.loads(members[SCAN_RULE_HIT_NAME].decode("utf-8"))
    assert report["total_omitted"] == 1
    assert report["hits"] == [
        {"file": f"{LOG_MEMBER_PREFIX}gateway.log.txt", "rules": {"脱敏残留": 1}, "total": 1}
    ]
    assert "abc123" not in members[SCAN_RULE_HIT_NAME].decode("utf-8")

    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    assert f"{LOG_MEMBER_PREFIX}gateway.log.txt" not in {
        item["name"] for item in manifest["members"]
    }
    # 剔除清单里只有文件名与条数，不含任何命中内容。
    assert "abc123" not in redacted


def test_renamed_log_backup_is_not_recollected_or_omitted(tmp_path, fake_probes):
    """按提示改名成 ``.log.bak-*`` 后，复导不应再收它，也不该再出现在清单里。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    leaky = log_dir / "error.log"
    leaky.write_text("Authorization: Bearer sk-***REDACTED***abc123\n", encoding="utf-8")
    (log_dir / "liuhao.log").write_text("hello\n", encoding="utf-8")
    (log_dir / "liuhao.log.1").write_text("rotated\n", encoding="utf-8")

    first = build_bundle(root=tmp_path, log_dir=log_dir)

    # 包内不出现 "error"：成员名用 issue.log，避免用户以为软件坏了。
    assert first.omitted_names == ("logs/issue.log.txt",)
    assert not any(
        word in name.lower() for name in _members(first) for word in ("error", "blocked", "failed")
    )
    # 网关只收 *.log，轮转文件本来就不进包。
    assert "logs/liuhao.log.1.txt" not in _members(first)

    leaky.rename(leaky.with_name("error.log.bak-20261108"))

    second = build_bundle(root=tmp_path, log_dir=log_dir)
    members = _members(second)

    assert second.omitted_names == ()
    assert "logs/issue.log.txt" not in members
    assert "REDACTED-MEMBERS.txt" not in members
    assert not any(".bak-" in name for name in members)
    assert "logs/liuhao.log.txt" in members
    # 改名备份仍留在磁盘上，只是不再进包（证据没丢）。
    assert leaky.with_name("error.log.bak-20261108").is_file()


def test_endpoint_downgrades_and_reports_omitted_members(tmp_path, fake_probes, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text(
        "Authorization: Bearer sk-***REDACTED***abc123\n", encoding="utf-8"
    )
    monkeypatch.setattr(bundle, "_log_dir", lambda: log_dir)

    app = FastAPI()
    app.include_router(bundle.router)
    with TestClient(app) as client:
        response = client.get("/v1/diagnostics/bundle")

    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert json.loads(response.headers["x-liuhao-diagnostics-omitted-members"]) == [
        f"{LOG_MEMBER_PREFIX}gateway.log.txt"
    ]
    members = _members(response.content)
    assert "REDACTED-MEMBERS.txt" in members
    assert f"{LOG_MEMBER_PREFIX}gateway.log.txt" not in members


def test_endpoint_returns_409_only_when_nothing_can_be_saved(
    tmp_path, fake_probes, monkeypatch, caplog
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(bundle, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(bundle, "contains_secret", lambda _text: True)
    caplog.set_level(logging.ERROR, logger="liuhao.audit")

    app = FastAPI()
    app.include_router(bundle.router)
    with TestClient(app) as client:
        response = client.get("/v1/diagnostics/bundle")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "DIAGNOSTIC_BUNDLE_BLOCKED"
    assert "下一步" in detail["message"]
    assert "请重试" not in detail["message"]
    assert detail["omitted_members"]
    # 409 同时写一条内部审计事件：只记成员名、规则名与条数，不记内容。
    assert "evt=diagnostic.bundle.blocked source=gateway" in caplog.text
    assert "rules=疑似密钥=" in caplog.text
    assert "hello" not in caplog.text
