# -*- coding: utf-8 -*-
"""生成桌面版 exe 的图标（纯标准库，不需要 Pillow）。

画的是海克斯风格的金色六边形环：外圈金色、内圈深蓝、透明背景，
配色与看板一致，这样任务栏图标和界面是一个体系。

用法
----
    python 06_桌面版\\make_icon.py             # 生成 icon.ico
    python 06_桌面版\\make_icon.py --preview   # 导出单张 PNG 看效果

改图标
------
改下面 GOLD / DEEP 两个颜色，或 render() 的 inner 参数（内圈比例，
0.5 环更粗、0.75 环更细）。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ICO = HERE / "icon.ico"
OUT_PNG = HERE / "icon_preview.png"

GOLD = (242, 196, 93, 255)   # 与看板 --gold 一致
DEEP = (14, 26, 51, 255)     # 与看板 --panel 一致
CLEAR = (0, 0, 0, 0)

SQRT3_2 = 3 ** 0.5 / 2       # ≈ 0.8660
SIZES = (16, 32, 48, 64, 128, 256)


def in_hex(nx: float, ny: float, scale: float = 1.0) -> bool:
    """点 (nx, ny) 是否落在以原点为中心的尖顶正六边形内。

    scale=1.0 时顶点到中心距离为 1（外接圆半径）。
    与看板 logo 的 clip-path: polygon(50% 0,93% 25%,93% 75%,50% 100%,7% 75%,7% 25%)
    是同一个形状 —— 宽高比 0.866 正是 sqrt(3)/2。
    """
    ax, ay = abs(nx), abs(ny)
    return (ay <= scale
            and ax <= SQRT3_2 * scale
            and 0.5 * ax + SQRT3_2 * ay <= SQRT3_2 * scale)


def render(size: int, inner: float = 0.60, ss: int = 3):
    """渲染一张 size×size 的 RGBA 位图，ss 倍超采样抗锯齿。"""
    r = size / 2.0
    c = size / 2.0
    inv = 1.0 / (ss * ss)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            gold = deep = 0
            for sy in range(ss):
                for sx in range(ss):
                    nx = (x + (sx + 0.5) / ss - c) / r
                    ny = (y + (sy + 0.5) / ss - c) / r
                    if not in_hex(nx, ny, 1.0):
                        continue
                    if in_hex(nx, ny, inner):
                        deep += 1
                    else:
                        gold += 1
            hit = gold + deep
            if hit == 0:
                row.append(CLEAR)
                continue
            wg = gold / hit
            wd = 1.0 - wg
            row.append((
                round(GOLD[0] * wg + DEEP[0] * wd),
                round(GOLD[1] * wg + DEEP[1] * wd),
                round(GOLD[2] * wg + DEEP[2] * wd),
                round(hit * inv * 255),
            ))
        rows.append(row)
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def to_png(rows, size: int) -> bytes:
    raw = bytearray()
    for row in rows:
        raw.append(0)                       # filter type 0 (None)
        for px in row:
            raw += bytes(px)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def to_ico(images) -> bytes:
    """images = [(size, png_bytes), ...]  —— ICO 允许内嵌 PNG（Vista+）。"""
    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)       # reserved, type=1, count
    offset = 6 + 16 * n
    entries = bytearray()
    blob = bytearray()
    for size, png in images:
        dim = 0 if size >= 256 else size        # 256 在 ICO 里记作 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blob += png
    return bytes(header) + bytes(entries) + bytes(blob)


def main() -> int:
    if "--preview" in sys.argv:
        size = 256
        OUT_PNG.write_bytes(to_png(render(size), size))
        print("已导出预览图：%s" % OUT_PNG)
        return 0

    images = []
    for size in SIZES:
        images.append((size, to_png(render(size), size)))
        print("  渲染 %3d×%-3d" % (size, size))
    OUT_ICO.write_bytes(to_ico(images))
    print("\n✓ 已生成 %s  (%d 个尺寸, %.1f KB)"
          % (OUT_ICO, len(images), OUT_ICO.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
