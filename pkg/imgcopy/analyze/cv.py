"""像素级事实（无模型、可复现）。

为什么必须有这一层：需求第 5 条要的留白比例、主体大小、元素数量这类东西，
让模型"看一眼"报个数是不稳定的（同一张图两次跑可能给出不同答案），
而 QC 阈值必须建立在稳定数字上。这里全部用 cv2/numpy 算出来。

主体检测不用深度学习模型，用的是「离背景色有多远 + 连通域」这套经典做法：
对手机壳这种图（主体大、背景干净、元素成套）足够稳，而且没有模型依赖。
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .. import context as ctx

WORK_MAX_EDGE = 768


# ---------------------------------------------------------------- 读写

def load_image(path: Path | str) -> np.ndarray:
    """读图返回 RGB。用 imdecode 绕开 Windows 中文路径。"""
    p = ctx.resolve(path)
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"读不了这张图：{p}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    elif img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        white = np.full_like(rgb, 255.0)
        img = (rgb * alpha + white * (1 - alpha)).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def save_image(path: Path | str, rgb: np.ndarray) -> Path:
    p = ctx.resolve(path)
    ctx.ensure_dir(p.parent)
    ok, buf = cv2.imencode(p.suffix or ".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError(f"编码失败：{p}")
    buf.tofile(str(p))
    return p


def make_preview(src: Path | str, dst: Path | str, max_edge: int | None = None,
                 quality: int | None = None) -> Path:
    """生成小预览图。看大图会撑爆会话（见 demo-skill/AGENTS.md）。"""
    max_edge = int(max_edge or ctx.get("vision.preview_max_edge", 1280))
    quality = int(quality or ctx.get("vision.preview_jpeg_quality", 80))
    rgb = load_image(src)
    h, w = rgb.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    p = ctx.resolve(dst)
    ctx.ensure_dir(p.parent)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("预览图编码失败")
    buf.tofile(str(p))
    return p


def load_mask(path: Path | str) -> np.ndarray | None:
    p = ctx.resolve(path)
    if not p.is_file():
        return None
    data = np.fromfile(str(p), dtype=np.uint8)
    m = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if m is None:
        return None
    return (m > 127).astype(np.uint8)


# ---------------------------------------------------------------- 小工具

def _to_work(rgb: np.ndarray, max_edge: int = WORK_MAX_EDGE) -> tuple[np.ndarray, float]:
    h, w = rgb.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale < 1.0:
        return cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA), scale
    return rgb, 1.0


def _rect_px(rect_norm, w: int, h: int) -> tuple[int, int, int, int]:
    x, y, x2, y2 = rect_norm
    return (max(0, int(x * w)), max(0, int(y * h)),
            min(w, int(math.ceil(x2 * w))), min(h, int(math.ceil(y2 * h))))


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _border_color(rgb: np.ndarray, ring: float = 0.03) -> np.ndarray:
    h, w = rgb.shape[:2]
    t = max(1, int(round(min(h, w) * ring)))
    parts = [rgb[:t, :, :].reshape(-1, 3), rgb[-t:, :, :].reshape(-1, 3),
             rgb[:, :t, :].reshape(-1, 3), rgb[:, -t:, :].reshape(-1, 3)]
    px = np.concatenate(parts, axis=0).astype(np.float32)
    return np.median(px, axis=0)


def _palette(rgb: np.ndarray, k: int = 6) -> list[dict]:
    small, _ = _to_work(rgb, 320)
    px = small.reshape(-1, 3).astype(np.float32)
    if len(px) > 60000:
        idx = np.linspace(0, len(px) - 1, 60000).astype(int)
        px = px[idx]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, labels, centers = cv2.kmeans(px, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    labels = labels.ravel()
    counts = np.bincount(labels, minlength=len(centers)).astype(np.float64)
    ratios = counts / counts.sum()
    order = np.argsort(-ratios)
    border = _border_color(small)

    entries = []
    for i in order:
        c = centers[i]
        entries.append({
            "hex": _hex(c),
            "rgb": [int(round(v)) for v in c],
            "ratio": round(float(ratios[i]), 4),
            "lab": [round(float(v), 2) for v in cv2.cvtColor(
                np.uint8([[c]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)],
            "_center": c,
        })

    # 角色：背景 = 最接近边缘环的；主色 = 占比最大；次色 = 第二个不同的；点缀 = 剩余里最鲜艳的
    def dist(a, b):
        return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))

    for e in entries:
        e["dist_to_border"] = dist(e["_center"], border)
    background = min(entries, key=lambda e: e["dist_to_border"])
    background["role"] = "background"
    rest = [e for e in entries if e is not background]
    if rest:
        dominant = max(rest, key=lambda e: e["ratio"])
        dominant["role"] = "dominant"
        rest = [e for e in rest if e is not dominant]
    else:
        dominant = background
    if rest:
        secondary = max(rest, key=lambda e: e["ratio"])
        secondary["role"] = "secondary"
        rest = [e for e in rest if e is not secondary]
    else:
        secondary = dominant
    saturation = []
    for e in rest:
        sat = max(e["rgb"]) - min(e["rgb"])
        saturation.append(sat * e["ratio"])
    accent = rest[int(np.argmax(saturation))] if rest else secondary
    for e in rest:
        e.setdefault("role", "accent")
    accent["role"] = "accent"

    for e in entries:
        e.pop("_center", None)
        e.pop("dist_to_border", None)
    entries.sort(key=lambda e: -e["ratio"])
    return entries


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """把主体内部的空洞填实（浅色嘴唇、同色内区这类）。"""
    h, w = mask.shape
    inv = (1 - mask).astype(np.uint8)
    ff = inv.copy()
    m2 = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, m2, (0, 0), 2)
    holes = (ff == 1).astype(np.uint8)
    return np.clip(mask + holes, 0, 1).astype(np.uint8)


def _subject_mask(rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    """离背景色的距离图 → 阈值 → 连通域 → 主体掩码。"""
    h, w = rgb.shape[:2]
    border = _border_color(rgb).astype(np.float32)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    border_lab = cv2.cvtColor(np.uint8([[border]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
    dist = np.linalg.norm(lab - border_lab, axis=2)
    # 两个判据取并集，光靠"离背景色的距离"分不开「粉色猪鼻子」和「蓝色底纹」：
    #   ① 距离明显远（主体、深色描边）
    #   ② 色相明显不同且有彩度（粉色鼻子、红色文字）
    thr = max(30.0, float(np.percentile(dist, 99)) * 0.45)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    bh = float(cv2.cvtColor(np.uint8([[border]]), cv2.COLOR_RGB2HSV)[0, 0, 0])
    dh = np.abs(hsv[:, :, 0] - bh)
    dh = np.minimum(dh, 180.0 - dh)
    hue_far = (dh > 25.0) & (hsv[:, :, 1] > 60.0)
    raw = ((dist > thr) | hue_far).astype(np.uint8)

    area = float(h * w)
    min_area = max(1.0, area * 0.003)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(raw, connectivity=8)
    comps = []
    for i in range(1, n):
        a = float(stats[i, cv2.CC_STAT_AREA])
        if a >= min_area:
            comps.append({
                "area": a,
                "bbox": (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                         int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])),
                "centroid": (float(cents[i][0]), float(cents[i][1])),
                "ratio": a / area,
            })
    comps.sort(key=lambda c: -c["area"])

    # 小母题计数：波点/星星/小花这类"反复出现的小图形"。它们的单个面积很小，
    # 会被上面的 min_area 过滤掉，所以单独数一遍（图案型款的判定靠它）。
    small = []
    for i in range(1, n):
        a = float(stats[i, cv2.CC_STAT_AREA]) / area
        if 0.00005 <= a <= 0.02:
            small.append({"area": a, "centroid": (float(cents[i][0]), float(cents[i][1]))})
    motif_count = len(small)

    # 粗定位（上面的阈值掩码）→ GrabCut 精修（阈值法分不开「同色系底纹」和主体内部浅色）
    subject = np.zeros((h, w), np.uint8)
    if comps:
        big = [c for c in comps if c["area"] >= max(area * 0.01, comps[0]["area"] * 0.12)] \
            or [comps[0]]
        x1 = min(c["bbox"][0] for c in big)
        y1 = min(c["bbox"][1] for c in big)
        x2 = max(c["bbox"][0] + c["bbox"][2] for c in big)
        y2 = max(c["bbox"][1] + c["bbox"][3] for c in big)
        pad_x, pad_y = int(w * 0.04), int(h * 0.04)
        rx1, ry1 = max(1, x1 - pad_x), max(1, y1 - pad_y)
        rx2, ry2 = min(w - 2, x2 + pad_x), min(h - 2, y2 + pad_y)
        if rx2 - rx1 > 12 and ry2 - ry1 > 12:
            try:
                gc = np.zeros((h, w), np.uint8)
                bgd = np.zeros((1, 65), np.float64)
                fgd = np.zeros((1, 65), np.float64)
                cv2.grabCut(rgb, gc, (rx1, ry1, rx2 - rx1, ry2 - ry1), bgd, fgd,
                            5, cv2.GC_INIT_WITH_RECT)
                subject = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
            except cv2.error:
                subject = raw
        if not subject.any():
            keep_ids = set()
            for c in big:
                bx, by, bw, bh = c["bbox"]
                sub = labels[by:by + bh, bx:bx + bw]
                keep_ids.update(np.unique(sub[sub > 0]).tolist())
            subject = np.isin(labels, list(keep_ids)).astype(np.uint8)
        # 去掉零碎区域，再把主体内部填实
        n2, lab2, st2, _ = cv2.connectedComponentsWithStats(subject, connectivity=8)
        areas2 = sorted((float(st2[i, cv2.CC_STAT_AREA]) for i in range(1, n2)), reverse=True)
        if areas2:
            keep2 = {i for i in range(1, n2)
                     if float(st2[i, cv2.CC_STAT_AREA]) >= max(area * 0.004, areas2[0] * 0.10)}
            subject = np.isin(lab2, list(keep2)).astype(np.uint8)
            subject = _fill_holes(subject)

    xs = np.where(subject.any(axis=0))[0]
    ys = np.where(subject.any(axis=1))[0]
    bbox = ((float(xs[0]) / w, float(ys[0]) / h, float(xs[-1] + 1) / w, float(ys[-1] + 1) / h)
            if len(xs) and len(ys) else (0.0, 0.0, 0.0, 0.0))
    sal = dist * subject if subject.any() else dist * 0
    if sal.sum() > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        cx = float((sal * xx).sum() / sal.sum()) / w
        cy = float((sal * yy).sum() / sal.sum()) / h
    else:
        cx = cy = 0.5
    return subject, {
        "components": comps,
        "motif_count": motif_count,
        "bbox": bbox,
        "area_ratio": float(subject.mean()),
        "visual_center": (round(cx, 4), round(cy, 4)),
        "border_color": [int(round(v)) for v in border],
        "dist_map": dist,
    }


def _gradient_ratio(gray: np.ndarray) -> float:
    """照片级平滑渐变占比：缓慢变化（blur 后仍有梯度）但局部无纹理。"""
    blur = cv2.GaussianBlur(gray, (0, 0), 9)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=5)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=5)
    slow = np.sqrt(gx * gx + gy * gy)
    mean = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 5)
    local_std = np.sqrt(np.maximum(0.0, cv2.GaussianBlur(
        (gray.astype(np.float32) - mean) ** 2, (0, 0), 5)))
    hit = (slow > 1.5) & (local_std < 10.0)
    return float(hit.mean())


def _color_count(rgb: np.ndarray) -> int:
    small, _ = _to_work(rgb, 256)
    q = (small >> 3).astype(np.uint8)  # 5 bit/通道
    flat = q.reshape(-1, 3)
    uniq, counts = np.unique(flat, axis=0, return_counts=True)
    ratios = counts / counts.sum()
    return int((ratios >= 0.001).sum())


def _symmetry(gray: np.ndarray) -> float:
    h, w = gray.shape
    half = w // 2
    a = gray[:, :half].astype(np.float32)
    b = cv2.flip(gray[:, w - half:], 1).astype(np.float32)
    if a.size == 0:
        return 0.0
    return float(max(0.0, 1.0 - np.abs(a - b).mean() / 255.0))


# ---------------------------------------------------------------- 主入口

def facts_for_region(rgb: np.ndarray, rect_px: tuple[int, int, int, int],
                     geo: dict, fill_mask: np.ndarray | None = None,
                     rect_norm: tuple[float, float, float, float] | None = None) -> dict:
    """对指定像素区域算一套事实。

    rect_px  —— 这块区域在工作图里的像素框
    rect_norm—— 同一块区域在整张画布里的归一化框（摄像头/补全掩码都按这个换算）
    """
    x, y, x2, y2 = rect_px
    crop = rgb[y:y2, x:x2]
    if crop.size == 0:
        raise ValueError("裁剪区域为空")
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    subject, info = _subject_mask(crop)

    border = np.array(info["border_color"], dtype=np.float32)
    dist = info["dist_map"]
    near_bg = float((dist < 12.0).mean())

    edges = cv2.Canny(gray, 50, 150)
    palette = _palette(crop)
    sat = float(hsv[:, :, 1].mean() / 255.0)
    temp = float((crop[:, :, 0].astype(np.float32) - crop[:, :, 2].astype(np.float32)).mean() / 255.0)
    contrast = float(min(1.0, gray.std() / 96.0))

    comps = info["components"]
    if len(comps) >= 2:
        pts = np.array([c["centroid"] for c in comps], dtype=np.float32)
        c0 = pts.mean(axis=0)
        spread = float(np.linalg.norm(pts - c0, axis=1).mean() / max(1.0, math.hypot(w, h)))
    else:
        spread = 0.0

    # 摄像头遮挡：主体有多少落在挖孔区里
    cam = geo.get("camera_rect_in_artwork_norm")
    cam_overlap = 0.0
    content_in_cam = 0.0
    if cam and rect_norm:
        rx1, ry1, rx2, ry2 = rect_norm
        ix1, iy1 = max(rx1, cam[0]), max(ry1, cam[1])
        ix2, iy2 = min(rx2, cam[2]), min(ry2, cam[3])
        if ix2 > ix1 and iy2 > iy1:
            cw, ch = w, h
            cx1 = int((ix1 - rx1) / max(1e-9, rx2 - rx1) * cw)
            cx2 = int((ix2 - rx1) / max(1e-9, rx2 - rx1) * cw)
            cy1 = int((iy1 - ry1) / max(1e-9, ry2 - ry1) * ch)
            cy2 = int((iy2 - ry1) / max(1e-9, ry2 - ry1) * ch)
            cx2 = max(cx1 + 1, min(cw, cx2))
            cy2 = max(cy1 + 1, min(ch, cy2))
            subj_area = float(subject.sum())
            if subj_area > 0:
                cam_overlap = float(subject[cy1:cy2, cx1:cx2].sum() / subj_area)
            cam_area = float(max(1, (cy2 - cy1) * (cx2 - cx1)))
            content = (dist[cy1:cy2, cx1:cx2] > 16.0).astype(np.float32)
            content_in_cam = float(content.sum() / cam_area)

    bx1, by1, bx2, by2 = info["bbox"]
    margin_x = min(bx1, max(0.0, 1.0 - bx2))
    margin_y = min(by1, max(0.0, 1.0 - by2))
    edge_margin = float(min(margin_x, margin_y))

    fill_ratio = None
    subject_fill_overlap = None
    if fill_mask is not None:
        fm = fill_mask
        if fm.shape != (h, w):
            fm = cv2.resize(fm, (w, h), interpolation=cv2.INTER_NEAREST)
        sub = fm[y:y2, x:x2]
        if sub.size:
            fill_ratio = float(sub.mean())
            subj_area = float(subject.sum())
            if subj_area > 0:
                subject_fill_overlap = float((subject & (sub > 0)).sum() / subj_area)

    return {
        "region_wh": [w, h],
        "palette": palette,
        "color_temperature": round(temp, 4),
        "saturation": round(sat, 4),
        "contrast": round(contrast, 4),
        "subject_bbox": [round(v, 4) for v in info["bbox"]],
        "subject_area_ratio": round(info["area_ratio"], 4),
        "whitespace_ratio": round(near_bg, 4),
        "element_count": len(comps),
        "motif_count": int(info.get("motif_count") or 0),
        "element_spread": round(spread, 4),
        "visual_center": [round(v, 4) for v in info["visual_center"]],
        "edge_density": round(float(edges.mean() / 255.0), 4),
        "symmetry_score": round(_symmetry(gray), 4),
        "gradient_ratio": round(_gradient_ratio(gray), 4),
        "color_count": _color_count(crop),
        "camera_overlap_ratio": round(cam_overlap, 4),
        "content_in_camera_ratio": round(content_in_cam, 4),
        "edge_margin_ratio": round(edge_margin, 4),
        "laplacian_var": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
        "fill_ratio": None if fill_ratio is None else round(fill_ratio, 4),
        "subject_fill_overlap": (None if subject_fill_overlap is None
                                 else round(subject_fill_overlap, 4)),
        "background_color": _hex(border),
    }


def analyze_canvas(path: Path | str, geo: dict, fill_mask: np.ndarray | None = None) -> dict:
    """对一张手机壳图案算全套事实。主事实基于「壳面区」（客户真正看到的那块），
    另外给一份整画布的事实，用于平铺/延展判断。"""
    rgb_full = load_image(path)
    oh, ow = rgb_full.shape[:2]
    rgb, _ = _to_work(rgb_full)
    h, w = rgb.shape[:2]

    face = geo.get("face_rect_in_artwork_norm", [0.0, 0.0, 1.0, 1.0])
    rect_face = _rect_px(face, w, h)
    rect_full = (0, 0, w, h)

    fill_full = None
    if fill_mask is not None:
        fill_full = (fill_mask if fill_mask.shape == (oh, ow)
                     else cv2.resize(fill_mask, (ow, oh), interpolation=cv2.INTER_NEAREST))
    fill_work = None
    if fill_full is not None:
        fill_work = cv2.resize(fill_full, (w, h), interpolation=cv2.INTER_NEAREST)

    face_norm = tuple(float(v) for v in face)
    facts = facts_for_region(rgb, rect_face, geo, fill_mask=fill_work,
                             rect_norm=face_norm)
    facts["full_canvas"] = {
        k: v for k, v in facts_for_region(rgb, rect_full, geo,
                                          rect_norm=(0.0, 0.0, 1.0, 1.0)).items()
        if k in ("palette", "subject_bbox", "subject_area_ratio", "whitespace_ratio",
                 "element_count", "color_count", "gradient_ratio", "edge_density")
    }
    facts["artwork_wh"] = [ow, oh]
    facts["face_rect_norm"] = list(face_norm)
    facts["region_norm"] = list(face_norm)
    return facts


def subject_crop(rgb: np.ndarray, bbox_norm, pad: float = 0.05) -> np.ndarray:
    """按归一化 bbox 裁主体（相似度算分区用）。"""
    h, w = rgb.shape[:2]
    x, y, x2, y2 = bbox_norm
    if x2 <= x or y2 <= y:
        return rgb
    pw, ph = (x2 - x) * pad, (y2 - y) * pad
    x1 = max(0, int((x - pw) * w))
    y1 = max(0, int((y - ph) * h))
    x2p = min(w, int((x2 + pw) * w))
    y2p = min(h, int((y2 + ph) * h))
    return rgb[y1:y2p, x1:x2p]


def subject_mask_canvas(rgb: np.ndarray, geo: dict, work_max: int = WORK_MAX_EDGE,
                        dilate_ratio: float | None = None) -> np.ndarray:
    """整张画布（工作分辨率）的主体掩码 0/1。

    用途：区域底稿里「非绿区」要按像素保留。用掩码而不是 bbox —— 否则主体四周
    会留一块矩形的旧底色，改背景色之后画面里会出现一块色块。
    """
    if dilate_ratio is None:
        dilate_ratio = float(ctx.get("limits.guide_protect_dilate_ratio", 0.008))
    work, _ = _to_work(rgb, work_max)
    h, w = work.shape[:2]
    x, y, x2, y2 = _rect_px(geo.get("face_rect_in_artwork_norm", [0, 0, 1, 1]), w, h)
    crop = work[y:y2, x:x2]
    full = np.zeros((h, w), np.uint8)
    if crop.size:
        m, _ = _subject_mask(crop)
        full[y:y2, x:x2] = m
    if dilate_ratio > 0:
        k = max(3, int(round(min(h, w) * dilate_ratio)) | 1)
        full = cv2.dilate(full, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return full
