"""安装版「中文 / 空格 / 元字符 / 单引号路径」的落盘与清理回归测试。

这些用例覆盖两条最容易在真实机器上炸、又不需要真装一遍的层：

* ``scripts/launch_installed.py`` 读安装器写下的 ``data-dir.txt``（BOM/UTF-8/
  旧 ANSI 三种编码，路径含中文与空格）；
* ``scripts/purge_pycache.py`` 在不经过 shell 的前提下清理任意合法路径里的
  ``__pycache__``（单引号、``&``、``#``、``()`` 都不应被当成语法）。

真正的安装/启动/卸载矩阵由 E2E 驱动（脚本在仓库外），这里只钉住易回归的
解码与参数传递逻辑。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import launch_installed as entry
from scripts import purge_pycache


@pytest.fixture()
def marker(tmp_path, monkeypatch):
    path = tmp_path / "data-dir.txt"
    monkeypatch.setattr(entry, "DATA_DIR_MARKER", path)
    return path


def test_marker_utf8_bom_with_chinese_and_space(marker, tmp_path):
    target = tmp_path / "数据 目录" / "鎏灏 数据"
    marker.write_bytes(b"\xef\xbb\xbf" + str(target).encode("utf-8") + b"\r\n")
    assert entry._marker_data_dir() == target.resolve()


def test_marker_utf8_without_bom_is_accepted(marker, tmp_path):
    target = tmp_path / "LiuHao 测试" / "data"
    marker.write_bytes(str(target).encode("utf-8") + b"\r\n")
    assert entry._marker_data_dir() == target.resolve()


@pytest.mark.skipif(os.name != "nt", reason="旧版 marker 是 Windows 系统 ANSI 编码")
def test_marker_legacy_ansi_falls_back_to_mbcs(marker, tmp_path):
    target = tmp_path / "数据 目录" / "鎏灏 数据"
    marker.write_bytes(str(target).encode("mbcs") + b"\r\n")
    assert entry._marker_data_dir() == target.resolve()


def test_marker_garbage_returns_none_instead_of_wrong_dir(marker):
    marker.write_bytes(b"\x00\xff\xfe\x00trailing")
    assert entry._marker_data_dir() is None


def test_marker_missing_file_returns_none(marker):
    assert entry._marker_data_dir() is None


def test_purge_pycache_handles_shell_meta_and_quote_paths(tmp_path):
    root = tmp_path / "O'Brien 鎏灏 a+b&c(1)#2^z" / "app"
    cache = root / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"\x00")
    assert purge_pycache.main(["purge_pycache.py", "--root", str(root)]) == 0
    assert not cache.exists()
    assert root.is_dir()


def test_logging_yaml_quotes_absolute_path_with_hash_and_space(tmp_path, monkeypatch):
    """数据目录含 ``a #1`` 时，生成的 YAML 不能被 ``#`` 截成注释。"""
    import yaml

    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "config" / "logging.yaml").write_text(
        "version: 1\n"
        "handlers:\n"
        "  app_file:\n"
        "    filename: logs/liuhao.log\n"
        "  error_file:\n"
        "    filename: logs/error.log\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(entry, "APP_ROOT", app_root)
    data_dir = tmp_path / "数据 a #1 鎏灏"
    (data_dir / "config").mkdir(parents=True)
    conf = entry._ensure_logging_config(data_dir)
    loaded = yaml.safe_load(conf.read_text(encoding="utf-8"))
    expected = str(data_dir / "logs").replace("\\", "/") + "/liuhao.log"
    assert loaded["handlers"]["app_file"]["filename"] == expected
    assert loaded["handlers"]["error_file"]["filename"].endswith("/error.log")


@pytest.mark.parametrize(
    "path, expected",
    [
        (r"D:\CON\data", "保留设备名"),
        (r"D:\liuhao\NUL", "保留设备名"),
        (r"D:\liuhao\com1.v2\data", "保留设备名"),
        (r"D:\liuhao\data.", "以点或空格结尾"),
        (r"D:\liuhao\data ", "以点或空格结尾"),
        (r"D:\鎏灏 智能 中枢\data", None),
        (r"D:\LiuHao 测试\a+b&c(1)#2^z\data", None),
        (r"D:\emoji 😀🔐\O'Brien [x]\data", None),
    ],
)
def test_path_name_issue(path, expected):
    issue = entry._path_name_issue(Path(path))
    if expected is None:
        assert issue is None
    else:
        assert issue is not None and expected in issue


def test_prepare_environment_rejects_reserved_name_before_any_write(monkeypatch):
    monkeypatch.setattr(entry, "_data_dir", lambda: Path(r"D:\NUL\data"))
    with pytest.raises(entry.DataDirectoryUnavailable, match="保留设备名"):
        entry.prepare_environment()


def test_prepare_environment_rejects_overlong_data_dir_before_any_write(monkeypatch):
    # 260 字符的数据目录：Windows 单条路径上限 259（含最深文件名），
    # 启动器必须在建目录/写密钥之前就用中文拒绝。
    overlong = Path("D:\\" + "\\".join(["x" * 20] * 13))
    assert len(str(overlong)) > entry.MAX_PATH_LIMIT
    monkeypatch.setattr(entry, "_data_dir", lambda: overlong)
    with pytest.raises(entry.DataDirectoryUnavailable, match="路径过长"):
        entry.prepare_environment()

