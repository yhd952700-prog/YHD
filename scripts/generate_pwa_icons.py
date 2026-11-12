#!/usr/bin/env python
"""生成 PWA 图标 + apple-touch-icon（纯标准库，无第三方依赖）。

为什么要自己栅格化
------------------
本项目的构建环境没有 Pillow，而 PWA 需要真实存在的 PNG 文件（`manifest.webmanifest`
里的 `icons` 与 iOS 的 `apple-touch-icon` 都不接受 SVG）。与其为了四张图引入一个
图像库依赖，不如用标准库的 ``zlib`` 直接写 PNG —— 图形本身很简单（方舟反应堆：
外环 + 10 个节点 + 线圈虚线环 + 内三角 + 两层发光核心），用超采样做抗锯齿足够。

图形与 ``favicon.svg``、``src/components/ReactorMark.tsx`` 是同一个造型
（原创，无字母），保证"装到手机上的图标"和"打开后左上角的标识"是同一个。
2026-11-12：品牌标识从旧六边形换成方舟反应堆后，本脚本同步改画反应堆；
旧的六边形画法已删掉，不要再恢复。

用法::

    python scripts/generate_pwa_icons.py            # 生成到默认目录
    python scripts/generate_pwa_icons.py --out DIR  # 指定输出目录
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "apps" / "console" / "console" / "public"

# 底色：深海军蓝渐变（顶部稍亮 → 底部近黑），与 manifest 的 theme_color #050912
# 同色系；钢蓝色反应堆在上面对比清晰，浅色/深色系统主题下都可辨识。
GRADIENT_TOP = (24, 42, 74)
GRADIENT_BOTTOM = (5, 9, 18)

# 反应堆各部件颜色（与 favicon.svg / ReactorMark.tsx 完全一致）。
RING = (91, 155, 213)       # #5b9bd5 外环、节点、线圈环
TRIANGLE = (143, 189, 232)   # #8fbde8 内三角
CORE_OUTER = (188, 217, 242) # #bcd9f2 核心外层
CORE_INNER = (234, 244, 252) # #eaf4fc 核心内层

# 反应堆几何（64×64 视口单位，与 favicon.svg / ReactorMark.tsx 逐一对应）。
_RING_R = 28.0
_NODE_R = 2.2
_COIL_R = 22.0
_COIL_DASH = 3.0   # 线圈虚线：3 单位实线 + 4 单位空白
_COIL_GAP = 4.0
_TRIANGLE = ((32.0, 20.0), (42.0, 40.0), (22.0, 40.0))
_CORE_OUTER_R = 7.5
_CORE_INNER_R = 4.0
_RING_W = 2.5
_COIL_W = 1.6
_TRI_W = 2.0


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


def _dist_to_segment(px: float, py: float, ax: float, ay: float,
                     bx: float, by: float) -> float:
    """点 (px, py) 到线段 AB 的距离。"""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render(
    size: int,
    *,
    corner_ratio: float = 0.22,
    mark_ratio: float = 0.30,
    supersample: int = 3,
) -> list[bytearray]:
    """渲染一张方形 RGBA 图标。

    ``corner_ratio`` 为 0 时是满幅方图（iOS 与 maskable 图标用；前者由系统切圆角，
    后者要求安全区内不被裁切）。

    ``mark_ratio`` 是标识的外接半径占边长的比例：反应堆在 64 视口里的外接
    半径是 28，即 0.4375；这里沿用旧参数名，0.30 表示"满幅画"（比例 1.0），
    maskable 与 apple-touch 按旧目标的比例相应缩小。
    """
    ss = supersample
    step = 1.0 / ss
    # 每个像素的子采样总数（不是图像总像素数 —— 后者会把 alpha 压到 0）。
    samples = ss * ss
    corner = corner_ratio * size
    center = size / 2.0
    # 64 视口单位换算成像素：mark_ratio 0.30 对应满幅。
    scale = (mark_ratio / 0.30) * (size / 64.0)

    ring_r = _RING_R * scale
    ring_half_w = _RING_W * scale / 2.0
    node_r = _NODE_R * scale
    coil_r = _COIL_R * scale
    coil_half_w = _COIL_W * scale / 2.0
    coil_period = (_COIL_DASH + _COIL_GAP) * scale
    coil_dash = _COIL_DASH * scale
    tri_half_w = _TRI_W * scale / 2.0
    core_outer_r = _CORE_OUTER_R * scale
    core_inner_r = _CORE_INNER_R * scale
    nodes = [
        (center + ring_r * math.cos(k * 36.0 * math.pi / 180.0),
         center + ring_r * math.sin(k * 36.0 * math.pi / 180.0))
        for k in range(10)
    ]
    tri = [((x - 32.0) * scale + center, (y - 32.0) * scale + center)
           for x, y in _TRIANGLE]
    tri_edges = [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]
    grad_span = (
        GRADIENT_BOTTOM[0] - GRADIENT_TOP[0],
        GRADIENT_BOTTOM[1] - GRADIENT_TOP[1],
        GRADIENT_BOTTOM[2] - GRADIENT_TOP[2],
    )

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

                    # --- 前景：反应堆各部件（由内向外判，核心最优先） ---
                    d_center = math.hypot(dx, dy)
                    if d_center <= core_inner_r:
                        color = CORE_INNER
                    elif d_center <= core_outer_r:
                        color = CORE_OUTER
                    elif any(
                        _dist_to_segment(x, y, ax, ay, bx, by) <= tri_half_w
                        for (ax, ay), (bx, by) in tri_edges
                    ):
                        color = TRIANGLE
                    elif (
                        abs(d_center - coil_r) <= coil_half_w
                        and (coil_r * (math.atan2(dy, dx) % (2.0 * math.pi))) % coil_period < coil_dash
                    ):
                        color = RING
                    elif any(
                        (x - nx) ** 2 + (y - ny) ** 2 <= node_r * node_r
                        for nx, ny in nodes
                    ):
                        color = RING
                    elif abs(d_center - ring_r) <= ring_half_w:
                        color = RING
                    else:
                        t = y / size
                        color = (
                            GRADIENT_TOP[0] + grad_span[0] * t,
                            GRADIENT_TOP[1] + grad_span[1] * t,
                            GRADIENT_TOP[2] + grad_span[2] * t,
                        )
                    acc_r += color[0]
                    acc_g += color[1]
                    acc_b += color[2]

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
    # 标准图标：圆角底 + 完整标识
    ("icon-192.png", 192, {"corner_ratio": 0.22, "mark_ratio": 0.30}),
    ("icon-512.png", 512, {"corner_ratio": 0.22, "mark_ratio": 0.30}),
    # maskable：满幅底 + 标识收进安全区（80% 直径圆内），被系统任意裁切都不缺角
    (
        "icon-512-maskable.png",
        512,
        {"corner_ratio": 0.0, "mark_ratio": 0.30 * 0.72},
    ),
    # iOS：满幅不透明（圆角由系统加），标识略小以留出系统圆角空间
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
