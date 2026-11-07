"""``data-dir.txt`` 必须是 UTF-8（带 BOM），中文/空格路径在非中文系统上也不能乱码。

安装器用 Inno 的 ``SaveStringsToUTF8File`` 写（写 BOM）；启动器按
``utf-8-sig`` 读，并对 0.1.0 的 ANSI marker 保留回退。
"""
from __future__ import annotations

from pathlib import Path


def _launcher(monkeypatch):
    from scripts import launch_installed

    monkeypatch.delenv(launch_installed.DATA_DIR_ENV, raising=False)
    return launch_installed


def test_marker_utf8_bom_with_chinese_and_space_path(monkeypatch, tmp_path):
    launcher = _launcher(monkeypatch)
    marker = tmp_path / "data-dir.txt"
    expected = Path("D:/验收 测试/LiuHaoData")
    marker.write_bytes("\ufeff".encode("utf-8") + str(expected).encode("utf-8") + b"\r\n")
    monkeypatch.setattr(launcher, "DATA_DIR_MARKER", marker)

    raw = marker.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "安装器必须带 BOM 写 marker"

    assert launcher._marker_data_dir() == expected.resolve()
    assert launcher._data_dir() == expected.resolve()


def test_marker_without_bom_falls_back_to_ansi(monkeypatch, tmp_path):
    launcher = _launcher(monkeypatch)
    marker = tmp_path / "data-dir.txt"
    expected = r"C:\Users\Test\AppData\Local\LiuHao\data"
    marker.write_bytes(expected.encode("mbcs") + b"\r\n")
    monkeypatch.setattr(launcher, "DATA_DIR_MARKER", marker)

    assert launcher._marker_data_dir() == Path(expected).resolve()


def test_env_override_beats_marker(monkeypatch, tmp_path):
    launcher = _launcher(monkeypatch)
    marker = tmp_path / "data-dir.txt"
    marker.write_bytes("\ufeffD:\\FromMarker".encode("utf-8"))
    monkeypatch.setattr(launcher, "DATA_DIR_MARKER", marker)
    monkeypatch.setenv(launcher.DATA_DIR_ENV, str(tmp_path / "FromEnv"))

    assert launcher._data_dir() == (tmp_path / "FromEnv").resolve()
