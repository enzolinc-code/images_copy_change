"""区域底稿（局部变异用）。

沿用 pattern-extract 已验证的做法：把「需要重做的区域」涂成纯绿色，
模型只重画绿色区，其余原样保留。这是「受控变异」和「整图重画」的分界线。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import cv2

from .. import context as ctx
from ..analyze import cv

GREEN = np.array([0, 255, 0], dtype=np.uint8)


def bbox_face_to_canvas(bbox_face, geo: dict, canvas_wh: tuple[int, int],
                        pad: float = 0.02) -> tuple[int, int, int, int]:
    """facts 里的 subject_bbox 是「壳面区」内的归一化坐标，这里换算到整张画布。"""
    cw, ch = canvas_wh
    fx1, fy1, fx2, fy2 = geo.get("face_rect_in_artwork_norm", [0, 0, 1, 1])
    fw, fh = (fx2 - fx1), (fy2 - fy1)
    bx1, by1, bx2, by2 = bbox_face
    x1 = fx1 + (bx1 - pad) * fw
    y1 = fy1 + (by1 - pad) * fh
    x2 = fx1 + (bx2 + pad) * fw
    y2 = fy1 + (by2 + pad) * fh
    return (max(0, int(x1 * cw)), max(0, int(y1 * ch)),
            min(cw, int(np.ceil(x2 * cw))), min(ch, int(np.ceil(y2 * ch))))


def make_region_guide(parent_png: Path | str, analysis: dict, region: str,
                      out_path: Path | str, text_box=None) -> Path:
    rgb = cv.load_image(parent_png)
    h, w = rgb.shape[:2]
    geo = ctx.geometry()

    guide = rgb.copy()
    # 优先用主体掩码（按像素保留），掩码为空时退回 bbox
    mask = cv.subject_mask_canvas(rgb, geo)
    if mask.any():
        mask_full = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    else:
        bbox = (analysis.get("facts") or {}).get("subject_bbox") or [0, 0, 1, 1]
        x1, y1, x2, y2 = bbox_face_to_canvas(bbox, geo, (w, h))
        mask_full = np.zeros((h, w), dtype=bool)
        mask_full[y1:y2, x1:x2] = True

    if region == "text":
        # 改字：只把文字那一块涂绿，其余全部原样保留
        if not text_box:
            raise ValueError("region=text 需要给 text_box（文字所在区域）")
        x1, y1, x2, y2 = bbox_face_to_canvas(text_box, geo, (w, h), pad=0.02)
        guide[y1:y2, x1:x2] = GREEN
        return cv.save_image(out_path, guide)
    if region == "subject":
        guide[mask_full] = GREEN
    else:  # background / accessory：主体以外全绿
        guide[~mask_full] = GREEN
    return cv.save_image(out_path, guide)
