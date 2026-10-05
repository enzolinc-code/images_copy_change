"""局部变异的后处理：把母款的主体原样贴回去。

为什么需要它：实测 O1Key 即使拿到「绿区底稿」，改底色时仍会把主体重画一遍
（猪变扁、耳朵变形）。而"主体位置、大小、形状必须一字不动"是这套系统的核心承诺，
不能靠模型自觉。做法是纯数学的：按主体掩膜把母款的像素合成回生成图，
边缘做几像素羽化，避免出现硬边。

只对 region_lock 的算子做（配色 / 道具 / 组合）；表情、姿态、主体替换、
风格、构图、语义这些本来就是"整体重画"，不做合成。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np

from .. import context as ctx
from ..analyze import cv


def composite_subject(parent_png: Path | str, generated_png: Path | str,
                      out_path: Path | str | None = None,
                      erode_px: int | None = None,
                      feather_px: int | None = None,
                      extra_boxes_norm: list | None = None) -> Path:
    parent = cv.load_image(parent_png)
    gen = cv.load_image(generated_png)
    geo = ctx.geometry()
    h, w = parent.shape[:2]
    if gen.shape[:2] != (h, w):
        gen = cv2.resize(gen, (w, h), interpolation=cv2.INTER_LANCZOS4)

    if erode_px is None:
        erode_px = int(ctx.get("limits.composite_erode_px", 2))
    if feather_px is None:
        feather_px = float(ctx.get("limits.composite_feather_px", 4))

    mask = cv.subject_mask_canvas(parent, geo, work_max=1024)
    m = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    m = ((m > 0.5).astype(np.uint8)) * 255

    # 文字也要贴回（模型会重画文字区，中文/英文都容易糊），但只贴"笔画"，
    # 不能贴整个矩形 —— 否则改完底色会在文字周围留一块旧底色方块。
    text_layer = _text_layer(parent, extra_boxes_norm, geo, (w, h))

    if erode_px > 0:
        k = 2 * int(erode_px) + 1
        # 只对主体做腐蚀（文字块太细，腐蚀会把它啃掉）
        keep = (m > 0).astype(np.uint8)
        eroded = cv2.erode((keep * 255), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        m = np.maximum(eroded, text_layer)
    alpha = cv2.GaussianBlur(m, (0, 0), feather_px).astype(np.float32)[:, :, None] / 255.0

    out = (gen.astype(np.float32) * (1 - alpha) + parent.astype(np.float32) * alpha)
    out = np.clip(out, 0, 255).astype(np.uint8)
    target = ctx.resolve(out_path) if out_path else ctx.resolve(generated_png)
    return cv.save_image(target, out)


def _box_face_to_canvas(box, geo: dict, wh: tuple[int, int], pad: float = 0.03):
    """labels 里的 region 是「壳面区」内的归一化坐标，换算到整张画布像素框。"""
    cw, ch = wh
    fx1, fy1, fx2, fy2 = geo.get("face_rect_in_artwork_norm", [0, 0, 1, 1])
    fw, fh = (fx2 - fx1), (fy2 - fy1)
    x, y, bw, bh = box
    x1 = max(0.0, fx1 + (x - pad) * fw)
    y1 = max(0.0, fy1 + (y - pad) * fh)
    x2 = min(1.0, fx1 + (x + bw + pad) * fw)
    y2 = min(1.0, fy1 + (y + bh + pad) * fh)
    return (int(x1 * cw), int(y1 * ch), int(np.ceil(x2 * cw)), int(np.ceil(y2 * ch)))


def _text_layer(parent: np.ndarray, boxes, geo, wh) -> np.ndarray:
    """文字笔画掩膜：框内「明显不是背景色」的像素，再稍微膨胀一下。"""
    h, w = parent.shape[:2]
    layer = np.zeros((h, w), np.uint8)
    if not boxes:
        return layer
    bg = np.array(cv._border_color(parent), dtype=np.float32)
    for box in boxes:
        x1, y1, x2, y2 = _box_face_to_canvas(box, geo, wh, pad=0.03)
        sub = parent[y1:y2, x1:x2]
        if sub.size == 0:
            continue
        d = np.linalg.norm(sub.astype(np.float32) - bg, axis=2)
        glyph = (d > 30.0).astype(np.uint8) * 255
        glyph = cv2.morphologyEx(glyph, cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        pad = int(ctx.get("limits.composite_text_dilate_px", 3))
        glyph = cv2.dilate(glyph, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1)))
        layer[y1:y2, x1:x2] = np.maximum(layer[y1:y2, x1:x2], glyph)
    return layer


def text_boxes(analysis: dict) -> list:
    """从 labels 里挑出「文字所在的那块区域」：钩子描述里含文字内容的那些。"""
    labels = (analysis or {}).get("labels") or {}
    content = str(((labels.get("text") or {}).get("content")) or "").strip()
    boxes = []
    for h in labels.get("visual_hooks") or []:
        desc = str(h.get("desc") or "")
        region = h.get("region")
        if not region or len(region) < 4:
            continue
        if content and len(content) >= 3 and content.lower() in desc.lower():
            boxes.append(region)
    return boxes


def raw_copy(generated_png: Path | str, raw_dir: Path | str, overwrite: bool = False) -> Path:
    """把模型原图留档到 _raw/，方便对比"贴回前/后"。"""
    src = ctx.resolve(generated_png)
    dst = ctx.ensure_dir(ctx.resolve(raw_dir)) / src.name
    if overwrite or not dst.is_file():
        shutil.copyfile(src, dst)
    return dst
