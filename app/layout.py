# -*- coding: utf-8 -*-
"""6 寸相纸排版：把证件照排满一张 4x6 英寸相纸，直接拿去冲印。

张数不是固定值（跟边距、间距、是否旋转都有关），所以这里实时算。
"""
from __future__ import annotations

import cv2
import numpy as np

from .specs import PAPER_6INCH_MM


def plan(photo_px: "tuple[int, int]", dpi: int = 300,
         margin_mm: float = 2.0, gap_mm: float = 2.0) -> dict:
    """返回最佳排版方案：{cols, rows, count, rotate}。photo_px=(宽,高)。"""
    pw, ph = photo_px
    paper_w = round(PAPER_6INCH_MM[0] / 25.4 * dpi)
    paper_h = round(PAPER_6INCH_MM[1] / 25.4 * dpi)
    m = round(margin_mm / 25.4 * dpi)
    g = round(gap_mm / 25.4 * dpi)

    best = None
    for rotate in (False, True):
        iw, ih = (ph, pw) if rotate else (pw, ph)
        cols = (paper_w - 2 * m + g) // (iw + g) if iw else 0
        rows = (paper_h - 2 * m + g) // (ih + g) if ih else 0
        if cols < 1 or rows < 1:
            continue
        count = int(cols * rows)
        if count < 1:
            continue
        used_w = cols * iw + (cols - 1) * g
        used_h = rows * ih + (rows - 1) * g
        # 张数相同时，比较「排版块的形状」和相纸形状，越接近说明留白越少、越像照相馆的版式
        ratio_gap = abs((used_w / used_h) - (paper_w / paper_h))
        cand = {"cols": int(cols), "rows": int(rows), "count": count, "rotate": rotate,
                "paper_px": (paper_w, paper_h), "margin": m, "gap": g, "ratio_gap": ratio_gap}
        if best is None or count > best["count"] or (
                count == best["count"] and ratio_gap < best.get("ratio_gap", 9e9)):
            best = cand
    if best is None:
        best = {"cols": 0, "rows": 0, "count": 0, "rotate": False,
                "paper_px": (paper_w, paper_h), "margin": m, "gap": g}
    return best


def render(photo_bgr: np.ndarray, dpi: int = 300,
           margin_mm: float = 2.0, gap_mm: float = 2.0,
           paper_color: int = 255) -> "tuple[np.ndarray, dict]":
    """把照片平铺到 6 寸相纸上，返回 (相纸图像, 排版参数)。"""
    ph, pw = photo_bgr.shape[:2]
    p = plan((pw, ph), dpi, margin_mm, gap_mm)
    W, H = p["paper_px"]
    canvas = np.full((H, W, 3), paper_color, dtype=np.uint8)
    tile = cv2.rotate(photo_bgr, cv2.ROTATE_90_CLOCKWISE) if p["rotate"] else photo_bgr
    th, tw = tile.shape[:2]
    m, g = p["margin"], p["gap"]

    used_w = p["cols"] * tw + (p["cols"] - 1) * g
    used_h = p["rows"] * th + (p["rows"] - 1) * g
    x0 = (W - used_w) // 2
    y0 = (H - used_h) // 2
    for r in range(p["rows"]):
        for c in range(p["cols"]):
            x = x0 + c * (tw + g)
            y = y0 + r * (th + g)
            canvas[y:y + th, x:x + tw] = tile
    return canvas, p
