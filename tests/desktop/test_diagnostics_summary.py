"""``SUMMARY.txt`` 必须只从包内字段推出结论，且不拿旧证据误导读者。

这些用例直接喂 ``_build_summary`` 的最小 payload，覆盖四种结论形态：
目录不可写、静态资源缺失、磁盘满、全部正常（无法确定）；另有
``verify_bundle`` 对 SUMMARY 的三条断言（结论行、泄漏、与 headline 一致）。
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.desktop.diagnostics import _build_summary, verify_bundle  # noqa: E402


def _checks(*items: tuple[str, str]) -> dict:
    return {
        "headline": "未发现已知故障",
        "checks": [
            {
                "id": check_id,
                "title": check_id,
                "status": status,
                "evidence": [
                    {"file": "x.json", "field": "value", "value": check_id},
                ],
            }
            for check_id, status in items
        ],
    }


def _startup(**overrides) -> dict:
    payload = {
        "data_writable": True,
        "log_writable": True,
        "port": 8080,
        "port_bindable": True,
        "port_state": "free",
        "db_open": True,
        "console_index": True,
        "data_free_bytes": 5_000_000_000,
    }
    payload.update(overrides)
    return payload


def _installation(**overrides) -> dict:
    payload = {
        "data_dir_write_probe": {"writable": True, "error": None},
        "logs_dir_write_probe": {"writable": True, "error": None},
        "integrity": {"missing": []},
    }
    payload.update(overrides)
    return payload


def _summary(**overrides) -> str:
    payload = {
        "diagnosis": _checks(
            ("service_not_running", "clear"),
            ("directory_not_writable", "clear"),
            ("disk_space_low", "clear"),
        ),
        "startup_check": _startup(),
        "installation": _installation(),
        "ports": {"port_conflicts": []},
        "static_assets": {"missing": []},
        "db_check": {"databases": []},
        "bootstrap_text": "",
        "event_text": "没有发现 evt= 事件（旧版日志或日志级别过高）。",
    }
    payload.update(overrides)
    return _build_summary(**payload)


def test_first_line_and_bullets_shape() -> None:
    text = _summary()
    lines = text.splitlines()
    assert lines[0].strip(), "第一行必须是一句结论"
    body = text.split("依据：", 1)[1].split("下一步：", 1)[0]
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert 3 <= len(bullets) <= 5, f"依据应 3-5 条，实际 {len(bullets)}"


def test_unwritable_dirs_names_both_probes() -> None:
    text = _summary(
        diagnosis=_checks(("directory_not_writable", "detected")),
        startup_check=_startup(data_writable=False, log_writable=False),
        installation=_installation(
            data_dir_write_probe={
                "writable": False,
                "error": {"type": "PermissionError", "winerror": None, "errno": 13},
            },
            logs_dir_write_probe={
                "writable": False,
                "error": {"type": "PermissionError", "winerror": 5, "errno": 13},
            },
        ),
        bootstrap_text=(
            "2026-11-07 16:00:00 evt=boot.log_dir.unwritable "
            "path=<本地路径>/logs winerror=5 errno=13 action=\"授予写权限\""
        ),
    )
    first = text.splitlines()[0]
    assert "不可写" in first
    assert "PermissionError" in first
    assert "data_dir_write_probe.writable = False" in text
    assert "logs_dir_write_probe.writable = False" in text
    assert "bootstrap.log.txt" in text


def test_missing_assets_conclusion() -> None:
    text = _summary(
        diagnosis=_checks(("console_assets_missing", "detected")),
        static_assets={"missing": ["assets/app.js", "assets/app.css"]},
        startup_check=_startup(console_index=True),
    )
    first = text.splitlines()[0]
    assert "静态资源缺失" in first
    assert "assets/app.js" in first
    assert "assets/app.js" in text


def test_disk_full_conclusion_from_free_bytes() -> None:
    text = _summary(
        diagnosis=_checks(("disk_space_low", "detected")),
        startup_check=_startup(data_free_bytes=0),
    )
    first = text.splitlines()[0]
    assert "磁盘空间不足" in first
    assert "data_free_bytes=0" in text
    assert "清理磁盘" in text


def test_stale_bootstrap_event_is_not_cited() -> None:
    """引导日志跨运行共享：与当前结论无关的旧事件不能当成本次依据。"""
    text = _summary(
        diagnosis=_checks(("console_assets_missing", "detected")),
        static_assets={"missing": ["assets/app.js"]},
        bootstrap_text=(
            "2026-11-07 10:00:00 evt=boot.logdir.failed "
            "path=D:\\cache\\pytest-of-admin\\blocked\\logs winerror=183 errno=17"
        ),
    )
    assert "pytest-of-admin" not in text


def test_unknown_writes_unable_to_determine_and_points_at_logs() -> None:
    text = _summary()
    first = text.splitlines()[0]
    assert "无法确定" in first
    assert "logs/" in first, "判不出来时必须指名要看哪个日志目录"


def test_next_step_is_exactly_one_action() -> None:
    """「下一步」只能有一个动作；其余动作必须降级成依据。"""
    text = _summary(
        diagnosis=_checks(("directory_not_writable", "detected")),
        startup_check=_startup(data_writable=False, log_writable=False),
        installation=_installation(
            data_dir_write_probe={
                "writable": False,
                "error": {"type": "PermissionError", "winerror": None, "errno": 13},
            },
            logs_dir_write_probe={
                "writable": False,
                "error": {"type": "PermissionError", "winerror": 5, "errno": 13},
            },
        ),
    )
    next_step = text.split("下一步：", 1)[1].splitlines()[0]
    assert next_step.strip()
    for separator in ("；", "。 "):
        assert separator not in next_step, f"下一步里出现了多个动作：{next_step}"
    assert next_step.count("，") <= 1, f"下一步像是一串并列动作：{next_step}"
    assert "其余动作（按需再做）" in text, "多余动作必须降级到依据里"


def _zip(tmp_path: Path, members: dict[str, str], manifest: bool = True) -> Path:
    target = tmp_path / "bundle.zip"
    payload = dict(members)
    if manifest:
        payload.setdefault("manifest.json", '{"files": {}}')
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return target


_GOOD_SUMMARY = (
    "程序目录不完整：缺少 src 等必需项\n"
    "\n"
    "依据：\n"
    "- installation.json · integrity.missing = ['src']\n"
    "\n"
    "下一步：用同一个安装包覆盖安装一次。\n"
    "\n"
    "# 结论来源：installation_incomplete\n"
)


def _diagnosis(*items: tuple[str, str]) -> str:
    return json.dumps(
        {
            "headline": "发现 1 个可定位的问题",
            "checks": [{"id": check_id, "status": status} for check_id, status in items],
        },
        ensure_ascii=False,
    )


def test_bundle_accepts_consistent_summary(tmp_path: Path) -> None:
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": _GOOD_SUMMARY,
            "diagnosis.json": _diagnosis(("installation_incomplete", "detected")),
        },
    )
    assert verify_bundle(bundle) == []


def test_bundle_flags_summary_contradicting_headline(tmp_path: Path) -> None:
    """故意矛盾：SUMMARY 说是安装不完整，diagnosis 里它是 clear 且 headline 指向别的。"""
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": _GOOD_SUMMARY,
            "diagnosis.json": _diagnosis(
                ("service_not_running", "detected"),
                ("installation_incomplete", "clear"),
            ),
        },
    )
    violations = verify_bundle(bundle)
    assert any("互相矛盾" in item for item in violations), violations


def test_bundle_flags_summary_contradicting_first_detected(tmp_path: Path) -> None:
    """结论来源是 detected，但 headline 的首个 detected 是另一个检查。"""
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": _GOOD_SUMMARY,
            "diagnosis.json": _diagnosis(
                ("directory_not_writable", "detected"),
                ("installation_incomplete", "detected"),
            ),
        },
    )
    violations = verify_bundle(bundle)
    assert any("headline 建议先处理" in item for item in violations), violations


def test_bundle_flags_empty_summary_conclusion(tmp_path: Path) -> None:
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": "\n依据：\n- x.json · y = 1\n\n# 结论来源：none\n",
            "diagnosis.json": _diagnosis(),
        },
    )
    violations = verify_bundle(bundle)
    assert any("结论行缺失或为空" in item for item in violations), violations


def test_bundle_flags_summary_with_username_and_key(tmp_path: Path) -> None:
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": (
                r"数据目录不可写：C:\Users\张三\AppData\Local\LiuHao\data "
                "api_key=sk-abcdefgh12345678"
                "\n\n下一步：检查权限\n\n# 结论来源：none\n"
            ),
            "diagnosis.json": _diagnosis(),
        },
    )
    violations = verify_bundle(bundle)
    assert any("泄漏特征" in item for item in violations), violations


def test_bundle_flags_summary_with_bare_absolute_path(tmp_path: Path) -> None:
    """没有用户名、但仍是本机绝对路径（例如 D:\\LiuHaoData），也必须判失败。"""
    bundle = _zip(
        tmp_path,
        {
            "SUMMARY.txt": (
                "数据目录：D:\\LiuHaoData\\data\n\n下一步：检查权限\n\n# 结论来源：none\n"
            ),
            "diagnosis.json": _diagnosis(),
        },
    )
    violations = verify_bundle(bundle)
    assert any("绝对路径" in item for item in violations), violations


def test_bundle_requires_summary_member(tmp_path: Path) -> None:
    """本产品生成的包（有 manifest）缺 SUMMARY 就等于没带结论。"""
    bundle = _zip(tmp_path, {"diagnosis.json": _diagnosis()})
    violations = verify_bundle(bundle)
    assert any("缺少 SUMMARY.txt" in item for item in violations), violations
