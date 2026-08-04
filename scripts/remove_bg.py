#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""remove_bg.py - desktop-pet-creator skill 的纯色背景抠图脚本。

针对 AI 生成的"纯色浅背景"状态图：从四角采样背景色，从图片边缘做洪水填充，
把与背景色相近且与边缘连通的区域置为透明，再做 1px 边缘羽化，最后按主体
包围盒裁剪并留出边距。主体内部与背景同色的区域（如白毛）不会被误伤——
只有与图像边缘连通的背景才会被移除。

用法:
  python remove_bg.py <图片1> [图片2 ...] [--tolerance 32] [--feather 1] [--margin 8] [--out-dir DIR]

参数:
  --tolerance  背景色容差（RGB 欧氏距离，默认 32；背景没抠干净调大，主体被误伤调小）
  --feather    边缘羽化像素数（默认 1，0 关闭）
  --margin     裁剪后四周保留的边距 px（默认 8）
  --out-dir    输出目录（默认与源文件同目录），文件名 <原名>-cutout.png

stdout 只输出一行 JSON：
  {"ok": true, "results": [{"src": ..., "out": ..., "size": [w,h], "bg": [r,g,b], "removed": 0.42}, ...]}
  任一文件失败整体 ok=false 并带 errors 数组（退出码 1）。

依赖 Pillow。推荐运行环境（已装好 Pillow 的 managed venv）：
  C:\\Users\\goodfather02\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print(json.dumps({
        "ok": False,
        "errors": ["缺少 Pillow。请先运行：managed Python -m venv 创建 envs\\default 后 pip install pillow"],
    }, ensure_ascii=False))
    sys.exit(1)


def sample_bg_color(px, w, h, patch=5):
    """四角 patch 的中位数作为背景色。"""
    samples = []
    for cx in (0, w - patch):
        for cy in (0, h - patch):
            for x in range(cx, cx + patch):
                for y in range(cy, cy + patch):
                    samples.append(px[x, y][:3])
    samples.sort()
    mid = len(samples) // 2
    # 逐通道中位数
    r = sorted(s[0] for s in samples)[mid]
    g = sorted(s[1] for s in samples)[mid]
    b = sorted(s[2] for s in samples)[mid]
    return (r, g, b)


def color_dist2(c1, c2):
    dr = c1[0] - c2[0]
    dg = c1[1] - c2[1]
    db = c1[2] - c2[2]
    return dr * dr + dg * dg + db * db


def flood_key(img, bg, tolerance, feather):
    """边缘洪水填充抠背景。返回 (处理后的 RGBA 图像, 移除比例)。"""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    tol2 = tolerance * tolerance

    is_bg = bytearray(w * h)
    q = deque()

    def try_seed(x, y):
        if color_dist2(px[x, y][:3], bg) <= tol2:
            idx = y * w + x
            if not is_bg[idx]:
                is_bg[idx] = 1
                q.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(h):
        try_seed(0, y)
        try_seed(w - 1, y)

    while q:
        x, y = q.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if not is_bg[idx] and color_dist2(px[nx, ny][:3], bg) <= tol2:
                    is_bg[idx] = 1
                    q.append((nx, ny))

    removed = sum(is_bg)
    total = w * h

    # 背景透明
    for y in range(h):
        base = y * w
        for x in range(w):
            if is_bg[base + x]:
                r, g, b, _ = px[x, y]
                px[x, y] = (r, g, b, 0)

    # 边缘羽化：与透明区相邻的主体像素降半透明，弱化锯齿/白边
    if feather > 0:
        for _ in range(feather):
            soft = []
            for y in range(h):
                base = y * w
                for x in range(w):
                    idx = base + x
                    if is_bg[idx]:
                        continue
                    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                        if 0 <= nx < w and 0 <= ny < h and is_bg[ny * w + nx]:
                            soft.append((x, y))
                            break
            for x, y in soft:
                r, g, b, a = px[x, y]
                px[x, y] = (r, g, b, a // 2)
                is_bg[y * w + x] = 1

    return img, removed / total


def trim_to_subject(img, margin):
    """按非透明区域包围盒裁剪，四周留 margin。"""
    bbox = img.getbbox()
    if bbox is None:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(img.size[0], right + margin)
    bottom = min(img.size[1], bottom + margin)
    return img.crop((left, top, right, bottom))


def main():
    parser = argparse.ArgumentParser(description="纯色背景抠图（边缘洪水填充）")
    parser.add_argument("images", nargs="+", help="图片路径")
    parser.add_argument("--tolerance", type=int, default=32)
    parser.add_argument("--feather", type=int, default=1)
    parser.add_argument("--margin", type=int, default=8)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    results = []
    errors = []
    for raw in args.images:
        src = Path(raw)
        try:
            if not src.is_file():
                raise ValueError("文件不存在：%s" % raw)
            with Image.open(str(src)) as im:
                img = im.convert("RGBA")
            w, h = img.size
            bg = sample_bg_color(img.load(), w, h)
            keyed, removed = flood_key(img, bg, args.tolerance, args.feather)
            out_img = trim_to_subject(keyed, args.margin)

            out_dir = Path(args.out_dir) if args.out_dir else src.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / ("%s-cutout.png" % src.stem)
            out_img.save(str(out), "PNG")

            note = None
            if removed > 0.95:
                note = "移除比例过高（%.0f%%），背景色可能与主体相近，建议调小 tolerance 重试" % (removed * 100)
            elif removed < 0.05:
                note = "移除比例过低（%.0f%%），背景可能不是纯色，建议调大 tolerance 重试" % (removed * 100)

            item = {
                "src": str(src),
                "out": str(out),
                "size": list(out_img.size),
                "bg": list(bg),
                "removed": round(removed, 3),
            }
            if note:
                item["note"] = note
            results.append(item)
        except Exception as exc:
            errors.append("%s: %s" % (raw, exc))

    ok = not errors
    payload = {"ok": ok, "results": results}
    if errors:
        payload["errors"] = errors
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
