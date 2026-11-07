"""一键诊断包：白名单、脱敏、无密钥三条底线。

这些测试只断言"发出去的文件里有什么"，不依赖具体实现细节；新增成员如果
忘了登记白名单，``test_members_are_exactly_the_whitelist`` 会先失败。
"""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

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


def _members(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_members_are_exactly_the_whitelist(tmp_path, fake_probes):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "gateway.log").write_text("hello\n", encoding="utf-8")
    (log_dir / "unexpected.exe").write_bytes(b"MZ")

    members = _members(build_bundle(root=tmp_path, log_dir=log_dir))

    assert set(members) == set(BUNDLE_MEMBERS) | {f"{LOG_MEMBER_PREFIX}gateway.log.txt"}
    assert not any(name.endswith(".exe") for name in members)


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


def test_export_is_refused_when_a_secret_survives_redaction(tmp_path, fake_probes, monkeypatch):
    monkeypatch.setattr(bundle, "contains_secret", lambda _text: True)
    with pytest.raises(ValueError):
        build_bundle(root=tmp_path, log_dir=tmp_path / "missing")


def test_export_is_refused_by_the_independent_leak_check(tmp_path, fake_probes, monkeypatch):
    """脱敏规则被绕过时，独立的正向泄漏清单仍必须拦住导出。

    这里把 ``sanitize_text`` 换成恒等函数（模拟"某条脱敏规则以后回退"），并让
    运行期数据目录落在含真实用户名的位置。如果自检谓词和脱敏共用一套规则，
    这个包会被放出去；独立清单则必须抛错。
    """
    monkeypatch.setattr(bundle, "sanitize_text", lambda value: str(value))
    monkeypatch.setattr(bundle, "resolve_data_dir", lambda: Path("C:/Users/张三/data"))

    with pytest.raises(ValueError) as excinfo:
        build_bundle(root=tmp_path, log_dir=tmp_path / "missing")

    message = str(excinfo.value)
    assert "泄漏特征" in message
    assert "用户名路径" in message


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
