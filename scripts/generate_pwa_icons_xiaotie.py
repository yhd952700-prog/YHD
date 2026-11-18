#!/usr/bin/env python3
"""生成 PWA 图标（小铁头像版）+ iOS 启动图。

为什么换成小铁头像
------------------
旧图标是方舟反应堆标识（``scripts/generate_pwa_icons.py``，与 favicon /
ReactorMark 同造型）。多端线（2026-11-17）按用户定调「贾维斯的脸就是小铁」
把**主屏图标**换成小铁头像：装到手机桌面上的那张脸，应当是用户每天打交道
的吉祥物本体，而不是抽象标识。favicon 与应用内 ReactorMark 不动；运行旧
脚本即可一键回退到反应堆图标（输出路径相同）。

素材与做法
----------
源图 ``console/xiaotie.webp``（1600×1600，奶油色底，就是聊天面板里用的
小铁本体）。奶油底与深蓝主题不搭，所以先从四边 flood-fill 抠掉近底色
背景，只取头部（含天线与耳罩，下巴以上）在深海军蓝渐变上合成——渐变
取值与旧图标 / manifest theme_color #050912 同色系。

输出（同时写构建产物 ``console/`` 与前端源 ``apps/console/console/public/``，
保证下次构建不回退）：
- icon-512.png / icon-192.png（purpose=any，头像占宽 88%）
- icon-512-maskable.png（头像占宽 64%，落在 maskable 安全区内）
- apple-touch-icon.png（180，满幅深蓝底，iOS 自己切圆角）
- splash-<w>x<h>.png × 10（iOS 启动图，头像占宽 30%）

依赖 Pillow（构建机系统 Python 自带；产品运行时不依赖）。
用法::  python3 scripts/generate_pwa_icons_xiaotie.py
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "console" / "xiaotie.webp"
OUT_DIRS = [
    REPO_ROOT / "console",
    REPO_ROOT / "apps" / "console" / "console" / "public",
]

GRADIENT_TOP = (24, 42, 74)     # #182a4a，与旧图标一致
GRADIENT_BOTTOM = (5, 9, 18)    # #050912 = manifest theme_color

SPLASH_SIZES = [
    (1290, 2796), (1179, 2556), (1170, 2532), (1284, 2778),
    (828, 1792), (750, 1334),
    (2048, 2732), (1668, 2388), (1620, 2160), (1488, 2266),
]


def cutout_head() -> Image.Image:
    """flood-fill 抠掉奶油底，返回只含头部的 RGBA 裁剪图。"""
    src = Image.open(SOURCE).convert("RGB")
    w, h = src.size
    px = src.load()
    # 以四角像素均值作底色基准。
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    tol2 = 30 * 30 * 3  # RGB 距离平方阈值

    def is_bg(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        return (r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2 < tol2

    seen = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()
    for x in range(w):
        queue.append((x, 0))
        queue.append((x, h - 1))
    for y in range(h):
        queue.append((0, y))
        queue.append((w - 1, y))
    while queue:
        x, y = queue.popleft()
        idx = y * w + x
        if seen[idx]:
            continue
        seen[idx] = 1
        if not is_bg(x, y):
            continue
        px[x, y] = bg  # 标记已吞（避免重复入队）
        if x > 0:
            queue.append((x - 1, y))
        if x < w - 1:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y < h - 1:
            queue.append((x, y + 1))

    rgba = Image.open(SOURCE).convert("RGBA")
    alpha = Image.new("L", (w, h), 255)
    apx = alpha.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            if seen[row + x] and is_bg(x, y):
                apx[x, y] = 0
    # 上面的双重判断里 flood 区域即背景；身体下方的阴影也是近底色会被抠掉，
    # 但我们只取头部行段，不受影响。
    rgba.putalpha(alpha)
    # 头部行段：天线顶 ~ 下巴（源图实测头部约在 y < 0.60h）。
    head = rgba.crop((0, 0, w, int(h * 0.60)))
    bbox = head.getbbox()
    if bbox is None:
        raise SystemExit("抠图失败：头部区域为空")
    head = head.crop(bbox)
    # 边缘羽化 1.5px，消除锯齿硬边。
    a = head.getchannel("A").filter(ImageFilter.GaussianBlur(1.5))
    head.putalpha(a)
    return head


def gradient_canvas(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(
                int(GRADIENT_TOP[i] + (GRADIENT_BOTTOM[i] - GRADIENT_TOP[i]) * t)
                for i in range(3)
            ),
        )
    return img


def compose(size: int, head: Image.Image, head_ratio: float,
            center_y: float = 0.48) -> Image.Image:
    canvas = gradient_canvas(size, size).convert("RGBA")
    target_w = int(size * head_ratio)
    target_h = int(head.height * target_w / head.width)
    sprite = head.resize((target_w, target_h), Image.LANCZOS)
    x = (size - target_w) // 2
    y = int(size * center_y - target_h / 2)
    canvas.alpha_composite(sprite, (x, y))
    return canvas.convert("RGB")


def splash(w: int, h: int, head: Image.Image) -> Image.Image:
    canvas = gradient_canvas(w, h).convert("RGBA")
    target_w = int(w * 0.30)
    target_h = int(head.height * target_w / head.width)
    sprite = head.resize((target_w, target_h), Image.LANCZOS)
    canvas.alpha_composite(
        sprite, ((w - target_w) // 2, int(h * 0.42 - target_h / 2))
    )
    return canvas.convert("RGB")


def main() -> None:
    head = cutout_head()
    print(f"head sprite: {head.size}")
    icon512 = compose(512, head, 0.88)
    maskable = compose(512, head, 0.64)
    apple = compose(512, head, 0.86).resize((180, 180), Image.LANCZOS)
    icon192 = icon512.resize((192, 192), Image.LANCZOS)
    for out in OUT_DIRS:
        out.mkdir(parents=True, exist_ok=True)
        icon512.save(out / "icon-512.png", optimize=True)
        icon192.save(out / "icon-192.png", optimize=True)
        maskable.save(out / "icon-512-maskable.png", optimize=True)
        apple.save(out / "apple-touch-icon.png", optimize=True)
        for w, h in SPLASH_SIZES:
            splash(w, h, head).save(out / f"splash-{w}x{h}.png", optimize=True)
        print(f"wrote icons + {len(SPLASH_SIZES)} splashes -> {out}")


if __name__ == "__main__":
    main()
