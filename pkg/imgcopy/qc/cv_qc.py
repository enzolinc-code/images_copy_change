"""G1：像素级质检（无模型，可复现）。

判四件事：
  ① 画面干不干净（清晰度）
  ② 核心基因还在不在（主体大小/位置、色系、文字块）
  ③ 改动 vs 计划（别改多了，也别没改）
  ④ 能不能印（颜色数、渐变、线条密度）

阈值在 config/thresholds.json。多数用「相对母款的比值」，因为不同题材绝对值差很多。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import context as ctx
from ..analyze import cv
from ..generate import postprocess


def _text_ratio(rgb: np.ndarray, analysis: dict, geo: dict) -> float:
    """文字块里的「非背景像素」占比 —— 用来判文字是不是被删了/被改糊了。"""
    boxes = postprocess.text_boxes(analysis)
    if not boxes:
        return 0.0
    h, w = rgb.shape[:2]
    bg = np.array(cv._border_color(rgb), dtype=np.float32)
    ratios = []
    for box in boxes:
        x1, y1, x2, y2 = postprocess._box_face_to_canvas(box, geo, (w, h), pad=0.03)
        sub = rgb[y1:y2, x1:x2]
        if sub.size == 0:
            continue
        d = np.linalg.norm(sub.astype(np.float32) - bg, axis=2)
        ratios.append(float((d > 30.0).mean()))
    return float(np.mean(ratios)) if ratios else 0.0


def check(child_path: Path | str, parent_path: Path | str, parent: dict,
          analysis: dict, spec: dict) -> dict:
    q = ctx.thresholds()["quality_gate"]
    geo = ctx.geometry()
    reasons: list[str] = []
    warnings: list[str] = []
    detail: dict = {}
    # 构图变异本来就要重排主体（可能变小、变两个、位移）——"主体必须在原位原尺寸"
    # 和"摄像头不能压主体"这两类判定对它不适用，降级成提示，不淘汰。
    restructure = (spec.get("operator") == "composition"
                   or "composition" in (spec.get("operator_parts") or []))
    warn_only = warnings if restructure else reasons

    dna = ctx.read_json(ctx.parent_dir(parent["parent_id"]) / "dna.json") or {}
    pattern_mode = bool(dna.get("pattern_mode"))

    child = cv.load_image(child_path)
    mother = cv.load_image(parent_path)
    facts_c = cv.analyze_canvas(child_path, geo)
    facts_p = analysis.get("facts") or {}

    # ① 清晰度（相对母款，避免题材差异误伤）
    lap_ratio = float(facts_c.get("laplacian_var") or 0) / max(
        1.0, float(facts_p.get("laplacian_var") or 1))
    detail["laplacian_ratio"] = round(lap_ratio, 3)
    if lap_ratio < float(q.get("blur_ratio_min", 0.45)):
        reasons.append("BLURRY")

    # ② 主体
    if not pattern_mode:
        pa = float(facts_p.get("subject_area_ratio") or 0)
        ca = float(facts_c.get("subject_area_ratio") or 0)
        ratio = (ca / pa) if pa > 0.02 else 1.0
        detail["subject_area_ratio"] = round(ratio, 3)
        if pa > 0.02 and ratio < float(q.get("subject_missing_area_ratio", 0.35)):
            warn_only.append("SUBJECT_MISSING")
        if pa > 0.02 and ratio > float(q.get("subject_drift_area_ratio", 2.2)):
            warn_only.append("SUBJECT_DRIFT")
        pb = facts_p.get("subject_bbox") or [0, 0, 0, 0]
        cb = facts_c.get("subject_bbox") or [0, 0, 0, 0]
        detail["subject_shift"] = round(max(
            abs((pb[0] + pb[2] / 2) - (cb[0] + cb[2] / 2)),
            abs((pb[1] + pb[3] / 2) - (cb[1] + cb[3] / 2))), 3)
        # 位置判定用主体掩膜的 IoU：换底色会让 GrabCut 重算边界，bbox 中心会假漂移
        if spec.get("region_lock"):
            # 直接比对主体像素（掩膜取母款的）：换底色会让 GrabCut 重算边界，
            # bbox 与掩膜 IoU 都会假漂移，只有"像素本身变没变"是可信的
            delta = _subject_pixel_delta(mother, child, geo)
            detail["subject_pixel_delta"] = round(delta, 2)
            if delta > float(q.get("subject_pixel_delta_max", 12.0)):
                warn_only.append("SUBJECT_CHANGED")
        cam_p = float(facts_p.get("camera_overlap_ratio") or 0)
        cam_c = float(facts_c.get("camera_overlap_ratio") or 0)
        detail["camera_overlap"] = [round(cam_p, 3), round(cam_c, 3)]
        if cam_c - cam_p > float(q.get("camera_overlap_delta_max", 0.25)):
            warn_only.append("CAMERA_BLOCKED")

    # ③ 冷暖跳变（配色漂移的宏观信号）
    temp_delta = abs(float(facts_c.get("color_temperature") or 0)
                     - float(facts_p.get("color_temperature") or 0))
    detail["temperature_delta"] = round(temp_delta, 3)
    changes_palette = spec.get("operator") == "color" or \
        "color" in (spec.get("operator_parts") or [])
    detail["palette_change_allowed"] = changes_palette
    if temp_delta > float(q.get("temperature_delta_max", 0.35)) and not changes_palette:
        reasons.append("PALETTE_TEMPERATURE_JUMP")

    # ④ 文字是否被删
    t_p = _text_ratio(mother, analysis, geo)
    t_c = _text_ratio(child, analysis, geo)
    detail["text_ratio_parent"] = round(t_p, 4)
    detail["text_ratio_child"] = round(t_c, 4)
    if t_p > 0.02 and t_c < t_p * float(q.get("text_keep_min_ratio", 0.35)):
        reasons.append("TEXT_LOST")
    # 要求替换文字时（例如商标改字），必须确认文字区**真的变了**：
    # 只判"文字还在"不够 —— 实测模型会把商标原样保留下来（trademark 风险）
    if _replace_from(dna):
        same = _text_region_same(mother, child, analysis, geo)
        detail["text_region_unchanged"] = round(same, 4)
        if same > float(q.get("text_replace_max_similarity", 0.97)):
            reasons.append("TEXT_NOT_REPLACED")

    # ⑤ 可印性（相对母款）
    cc_ratio = float(facts_c.get("color_count") or 0) / max(
        1.0, float(facts_p.get("color_count") or 1))
    detail["color_count_ratio"] = round(cc_ratio, 3)
    if cc_ratio > float(q.get("color_count_ratio_max", 1.6)):
        reasons.append("TOO_MANY_COLORS")
    gr = float(facts_c.get("gradient_ratio") or 0)
    gr_p = float(facts_p.get("gradient_ratio") or 0)
    detail["gradient_ratio"] = round(gr, 4)
    if gr > max(float(q.get("gradient_ratio_max", 0.45)), gr_p + 0.25):
        reasons.append("GRADIENT_HEAVY")
    ed_ratio = float(facts_c.get("edge_density") or 0) / max(
        1e-6, float(facts_p.get("edge_density") or 1))
    detail["edge_density_ratio"] = round(ed_ratio, 3)
    if ed_ratio > float(q.get("edge_density_ratio_max", 2.2)):
        reasons.append("TOO_BUSY")

    return {"gate": "G1", "passed": not reasons, "reason_codes": reasons,
            "detail": {**detail, "warnings": warnings}, "score": None}


def _subject_pixel_delta(mother: np.ndarray, child: np.ndarray, geo: dict) -> float:
    """母款主体区内，子款与母款的平均像素差（贴回过主体的应接近 0）。"""
    import cv2
    mask = cv.subject_mask_canvas(mother, geo, work_max=640).astype(bool)
    if mask.sum() == 0:
        return 0.0
    if mother.shape != child.shape:
        child = cv2.resize(child, (mother.shape[1], mother.shape[0]),
                           interpolation=cv2.INTER_AREA)
    h, w = mother.shape[:2]
    if mask.shape != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    d = np.abs(mother.astype(np.int16) - child.astype(np.int16)).mean(axis=2)
    return float(d[mask].mean())


def _replace_from(dna: dict) -> str:
    for g in dna.get("core_gene", []) + dna.get("mutable_gene", []):
        rf = (g.get("evidence") or {}).get("replace_from")
        if rf:
            return str(rf)
    return ""


def _text_region_same(mother: np.ndarray, child: np.ndarray, analysis: dict,
                      geo: dict) -> float:
    """文字区域相似度（1 = 完全没变）。确认"要求改掉的字"真被改掉了。"""
    labels = (analysis.get("labels") or {})
    content = str((labels.get("text") or {}).get("content") or "")
    box = None
    for h in labels.get("visual_hooks") or []:
        desc = str(h.get("desc") or "")
        if content and content.lower() in desc.lower() and h.get("region"):
            box = h["region"]
            break
    if box is None:
        for h in labels.get("visual_hooks") or []:
            if any(k in str(h.get("desc") or "") for k in ("文字", "字样", "短词")):
                box = h.get("region")
                break
    if not box:
        return 0.0
    h_, w_ = mother.shape[:2]
    x1, y1, x2, y2 = postprocess._box_face_to_canvas(box, geo, (w_, h_), pad=0.02)
    if child.shape[:2] != (h_, w_):
        return 0.0
    a = mother[y1:y2, x1:x2]
    b = child[y1:y2, x1:x2]
    if a.size == 0:
        return 0.0
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
    return float(max(0.0, 1.0 - d / 255.0))
