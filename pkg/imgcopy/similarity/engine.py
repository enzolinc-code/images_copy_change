"""相似度引擎：母子相似度 + 子子相似度（双区间）。

目标不是"去重"，而是保证每张图都有新的测试价值：
  · 太像母款（几乎是复制品）→ 没有测试价值
  · 离母款太远（看不出是同一系列）→ 丢了爆款基因

三个分区：
  zone_frame   整幅结构（灰度：1 - 归一化平均绝对差）
  zone_color   颜色分布（HSV 二维直方图相关性）
  zone_subject 主体裁剪区结构（通版图案没有主体，跳过）

**关键指标是 zone_change**：真正改动过的像素占比。
实测教训（2026-10-03）：只看整幅灰度差会误判 —— 手机壳画布上背景占大头，
连"猪换成兔子"这种大改动的整幅差值都很小，会被算成 0.95 相似而误杀。
"几乎复制母款"的正确含义是"几乎没动过任何地方"，所以用面积占比，不用平均差。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import cv2
import numpy as np

from .. import context as ctx
from ..analyze import cv


def _small(path: Path | str, max_edge: int = 256) -> np.ndarray:
    rgb = cv.load_image(path)
    h, w = rgb.shape[:2]
    s = max_edge / max(h, w)
    if s < 1:
        rgb = cv2.resize(rgb, (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_AREA)
    return rgb


def _gray(a: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)


def zone_frame(a: np.ndarray, b: np.ndarray) -> float:
    ga, gb = _gray(a).astype(np.float32), _gray(b).astype(np.float32)
    if ga.shape != gb.shape:
        gb = cv2.resize(gb, (ga.shape[1], ga.shape[0]), interpolation=cv2.INTER_AREA)
    return float(max(0.0, 1.0 - np.abs(ga - gb).mean() / 255.0))


def zone_color(a: np.ndarray, b: np.ndarray) -> float:
    def hist(img):
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [30, 16], [0, 180, 0, 256])
        return cv2.normalize(h, h).flatten()
    corr = float(cv2.compareHist(hist(a), hist(b), cv2.HISTCMP_CORREL))
    return float(max(0.0, min(1.0, (corr + 1) / 2)))


def zone_change(a: np.ndarray, b: np.ndarray, delta: float = 30.0) -> float:
    """真正改动的像素占比：逐像素颜色距离超过 delta 的像素比例。"""
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    d = np.linalg.norm(a.astype(np.float32) - b.astype(np.float32), axis=2)
    return float((d > delta).mean())


def zone_subject(child: np.ndarray, parent: np.ndarray, bbox) -> float | None:
    if not bbox or (bbox[2] - bbox[0]) < 0.05:
        return None
    ca = cv.subject_crop(child, bbox)
    cb = cv.subject_crop(parent, bbox)
    if ca.size == 0 or cb.size == 0:
        return None
    return zone_frame(ca, cb)


def weighted(frame: float, color: float, subject: float | None = None) -> float:
    if subject is None:
        return 0.45 * frame + 0.55 * color
    return 0.3 * frame + 0.4 * color + 0.3 * subject


def _bg_mask(img: np.ndarray, tol: float = 30.0) -> np.ndarray:
    """背景像素掩膜（离边框色近的算背景）。"""
    bg = np.array(cv._border_color(img), dtype=np.float32)
    d = np.linalg.norm(img.astype(np.float32) - bg, axis=2)
    return d <= tol


def _spectrum_corr(a: np.ndarray, b: np.ndarray, size: int = 192) -> float:
    """排布周期的相似度（FFT 频谱相关性）。斜向点阵 vs 正方点阵在这里能分开。"""
    def spec(img):
        small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32)
        g = g - g.mean()
        f = np.abs(np.fft.fftshift(np.fft.fft2(g)))
        f = np.log1p(f)
        c = size // 2
        k = size // 6
        patch = f[c - k:c + k, c - k:c + k]
        n = np.linalg.norm(patch)
        return (patch / n).flatten() if n > 0 else patch.flatten()
    x, y = spec(a), spec(b)
    if x.size != y.size:
        return 0.0
    return float(max(0.0, min(1.0, float(np.dot(x, y)))))


def pattern_metrics(child: np.ndarray, parent: np.ndarray) -> dict:
    """通版图案专用：母题变化 / 背景变化 / 排布周期，三个轴各看各的。

    像素面积法在通版图案上会失灵 —— 圆点只占几个百分点，换个母题也只动了很少的像素。
    """
    if child.shape != parent.shape:
        child = cv2.resize(child, (parent.shape[1], parent.shape[0]),
                           interpolation=cv2.INTER_AREA)
    cb, pb = _bg_mask(child), _bg_mask(parent)
    d = np.linalg.norm(child.astype(np.float32) - parent.astype(np.float32), axis=2)
    motif = ~(cb & pb)
    both_bg = cb & pb
    motif_delta = float(d[motif].mean() / 255.0) if motif.any() else 0.0
    bg_delta = float(d[both_bg].mean() / 255.0) if both_bg.any() else 0.0
    # 平均值会被"整条线稿"稀释：只换掉小天使这种局部改动，均值几乎没有变化。
    # 所以同时给"母题像素里真的变了多少"这个占比信号。
    motif_change = float((d[motif] > 30).mean()) if motif.any() else 0.0
    bg_change = float((d[both_bg] > 30).mean()) if both_bg.any() else 0.0
    return {"motif_delta": round(motif_delta, 4), "bg_delta": round(bg_delta, 4),
            "motif_change": round(motif_change, 4), "bg_change": round(bg_change, 4),
            "lattice": round(_spectrum_corr(child, parent), 4)}


def compare(child_path: Path | str, parent_path: Path | str, facts_parent: dict,
            pattern_mode: bool = False) -> dict:
    c, p = _small(child_path), _small(parent_path)
    frame = zone_frame(c, p)
    color = zone_color(c, p)
    change = zone_change(c, p)
    subj = None if pattern_mode else zone_subject(c, p, facts_parent.get("subject_bbox"))
    out = {
        "zone_frame": round(frame, 4),
        "zone_color": round(color, 4),
        "zone_subject": None if subj is None else round(subj, 4),
        "zone_change": round(change, 4),
        "to_parent": round(weighted(frame, color, subj), 4),
    }
    if pattern_mode:
        pm = pattern_metrics(c, p)
        out.update(pm)
        out["to_parent"] = round(1.0 - max(pm["motif_delta"], pm["bg_delta"],
                                           1 - pm["lattice"]), 4)
        out["pattern_mode"] = True
    return out


def judge_parent_child(scores: dict, thr: dict | None = None) -> list[str]:
    t = (thr or ctx.thresholds())["similarity"]
    reasons: list[str] = []
    if scores.get("pattern_mode"):
        motif = float(scores.get("motif_delta") or 0)
        bg = float(scores.get("bg_delta") or 0)
        motif_pct = float(scores.get("motif_change") or 0)
        bg_pct = float(scores.get("bg_change") or 0)
        lattice = float(scores.get("lattice") or 1)
        # "太像"看变了的像素占比（平均值会被整条线稿稀释）；
        # "太远"看母题区平均差值（换了个设计 → 母题区整体对不上）
        if max(motif_pct, bg_pct) < float(t.get("pattern_change_min", 0.14)) \
                and lattice > float(t.get("pattern_lattice_same_min", 0.97)):
            reasons.append("TOO_SIMILAR")
        if motif > float(t.get("pattern_motif_mean_max", 0.37)):
            reasons.append("TOO_DIFFERENT")
        return reasons
    change = float(scores.get("zone_change") or 0.0)
    if change < float(t.get("too_similar_change_max", 0.08)):
        reasons.append("TOO_SIMILAR")
    # "太远"用整幅灰度相关判：换了个设计 → 结构对不上；换底色 → 结构还在
    if float(scores.get("zone_frame") or 1.0) < float(t.get("too_different_frame_max", 0.86)):
        reasons.append("TOO_DIFFERENT")
    return reasons


def pairwise(children: list[dict], thr: dict | None = None,
             pattern_mode: bool = False) -> list[dict]:
    """批次内两两相似度，返回超过上限的那些对。"""
    t = (thr or ctx.thresholds())["similarity"]
    limit = float(t["child_child_max"])
    out: list[dict] = []
    cache: dict[str, np.ndarray] = {}
    for a, b in itertools.combinations(children, 2):
        pa, pb = a.get("image_path"), b.get("image_path")
        if not pa or not pb:
            continue
        for k in (pa, pb):
            if k not in cache:
                cache[k] = _small(k)
        if pattern_mode:
            pm = pattern_metrics(cache[pa], cache[pb])
            dup = (pm["motif_delta"] < float(t.get("pattern_motif_delta_min", 0.10))
                   and pm["bg_delta"] < float(t.get("pattern_bg_delta_min", 0.10))
                   and pm["lattice"] > float(t.get("pattern_lattice_same_min", 0.97)))
            if dup:
                out.append({"a": a["child_id"], "b": b["child_id"],
                            "a_name": a.get("child_name"), "b_name": b.get("child_name"),
                            "score": round(1 - max(pm["motif_delta"], pm["bg_delta"]), 4),
                            "detail": pm})
            continue
        # 子子之间"算不算重复"同样看实际差异面积：整幅相关会把
        # "猪换兔子"（主体只占 1/4 画布）误判成"困倦眼"的重复
        change = zone_change(cache[pa], cache[pb])
        if change < float(t.get("child_child_change_max", 0.06)):
            out.append({"a": a["child_id"], "b": b["child_id"],
                        "a_name": a.get("child_name"), "b_name": b.get("child_name"),
                        "score": round(change, 4)})
    return out
