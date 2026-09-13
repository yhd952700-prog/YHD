#!/usr/bin/env python
"""生成 PWA 图标 + apple-touch-icon（纯标准库，无第三方依赖）。

为什么要自己栅格化
------------------
本项目的构建环境没有 Pillow，而 PWA 需要真实存在的 PNG 文件（`manifest.webmanifest`
里的 `icons` 与 iOS 的 `apple-touch-icon` 都不接受 SVG）。与其为了四张图引入一个
图像库依赖，不如用标准库的 ``zlib`` 直接写 PNG —— 图形本身很简单（圆角渐变底 +
六边形描边 + 中心圆点），用超采样做抗锯齿足够。

图形与 ``src/components/Shell.tsx``、``Login.tsx`` 里的内联品牌 mark 同一个形状
（尖顶六边形 + 中心圆点），保证"装到手机上的图标"和"打开后左上角的 mark"是同一个。

用法::

    python scripts/generate_pwa_icons.py            # 生成到默认目录
    python scripts/generate_pwa_icons.py --out DIR  # 指定输出目录
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "apps" / "console" / "console" / "public"

# 渐变：顶部亮蓝 → 底部深蓝。与 os.css 的 --os-accent 同色系但不完全相同 ——
# 图标需要在浅色/深色两种系统主题下都保持可辨识的对比度。
GRADIENT_TOP = (62, 123, 240)
GRADIENT_BOTTOM = (16, 27, 62)
MARK = (255, 255, 255)

# 尖顶六边形的三条边法线（0°, 60°, 120°）。内积绝对值均 <= 内切半径即在内。
_N0 = (1.0, 0.0)
_N1 = (0.5, 0.8660254037844386)
_N2 = (-0.5, 0.8660254037844386)
_COS30 = 0.8660254037844386


def _write_png(path: Path, width: int, height: int, rows: list[bytearray]) -> None:
    """把 RGBA8 行写成 PNG（无隔行，无调色板）。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)  # 过滤器类型 0（None）
        raw.extend(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def render(
    size: int,
    *,
    corner_ratio: float = 0.22,
    mark_ratio: float = 0.30,
    stroke_ratio: float = 0.036,
    core_ratio: float = 0.085,
    supersample: int = 3,
) -> list[bytearray]:
    """渲染一张方形 RGBA 图标。

    ``corner_ratio`` 为 0 时是满幅方图（iOS 与 maskable 图标用；前者由系统切圆角，
    后者要求安全区内不被裁切）。
    """
    ss = supersample
    step = 1.0 / ss
    # 每个像素的子采样总数（不是图像总像素数 —— 后者会把 alpha 压到 0）。
    samples = ss * ss
    corner = corner_ratio * size
    r_out = mark_ratio * size
    apothem_out = r_out * _COS30
    stroke = stroke_ratio * size
    # 内六边形：内切半径减一个描边宽度（换算回外接半径）。
    r_in = max(0.0, r_out - stroke / _COS30)
    apothem_in = r_in * _COS30
    core = core_ratio * size
    center = size / 2.0
    grad_span = GRADIENT_BOTTOM[0] - GRADIENT_TOP[0]

    rows: list[bytearray] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc_r = acc_g = acc_b = 0.0
            inside = 0
            for sy in range(ss):
                y = py + (sy + 0.5) * step
                dy = y - center
                for sx in range(ss):
                    x = px + (sx + 0.5) * step
                    dx = x - center

                    # --- 底色：圆角矩形（corner==0 时退化为正方形） ---
                    if corner <= 0.0:
                        in_tile = True
                    else:
                        qx = min(max(x, corner), size - corner)
                        qy = min(max(y, corner), size - corner)
                        in_tile = (x - qx) ** 2 + (y - qy) ** 2 <= corner * corner
                    if not in_tile:
                        continue

                    inside += 1

                    # --- 前景：六边形描边 或 中心圆点 ---
                    is_mark = False
                    if abs(dx) <= apothem_out and abs(dx * _N1[0] + dy * _N1[1]) <= apothem_out \
                            and abs(dx * _N2[0] + dy * _N2[1]) <= apothem_out:
                        in_inner = (
                            apothem_in > 0.0
                            and abs(dx) <= apothem_in
                            and abs(dx * _N1[0] + dy * _N1[1]) <= apothem_in
                            and abs(dx * _N2[0] + dy * _N2[1]) <= apothem_in
                        )
                        is_mark = not in_inner
                    if not is_mark and dx * dx + dy * dy <= core * core:
                        is_mark = True

                    if is_mark:
                        acc_r += MARK[0]
                        acc_g += MARK[1]
                        acc_b += MARK[2]
                    else:
                        t = y / size
                        tp = GRADIENT_TOP[0] + grad_span * t
                        acc_r += tp
                        acc_g += GRADIENT_TOP[1] + (GRADIENT_BOTTOM[1] - GRADIENT_TOP[1]) * t
                        acc_b += GRADIENT_TOP[2] + (GRADIENT_BOTTOM[2] - GRADIENT_TOP[2]) * t

            if inside == 0:
                row.extend((0, 0, 0, 0))
            else:
                alpha = round(255 * inside / samples)
                row.extend(
                    (
                        min(255, round(acc_r / inside)),
                        min(255, round(acc_g / inside)),
                        min(255, round(acc_b / inside)),
                        alpha,
                    )
                )
        rows.append(row)
    return rows


# (文件名, 边长, 参数)
TARGETS = [
    # 标准图标：圆角底 + 完整 mark
    ("icon-192.png", 192, {"corner_ratio": 0.22, "mark_ratio": 0.30}),
    ("icon-512.png", 512, {"corner_ratio": 0.22, "mark_ratio": 0.30}),
    # maskable：满幅底 + mark 收进安全区（80% 直径圆内），被系统任意裁切都不缺角
    (
        "icon-512-maskable.png",
        512,
        {"corner_ratio": 0.0, "mark_ratio": 0.30 * 0.72, "stroke_ratio": 0.036 * 0.72, "core_ratio": 0.085 * 0.72},
    ),
    # iOS：满幅不透明（圆角由系统加），mark 略小以留出系统圆角空间
    (
        "apple-touch-icon.png",
        180,
        {"corner_ratio": 0.0, "mark_ratio": 0.27, "supersample": 4},
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 PWA 图标")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    for name, size, options in TARGETS:
        rows = render(size, **options)
        path = out / name
        _write_png(path, size, size, rows)
        print(f"wrote {path} ({size}x{size}, {path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
