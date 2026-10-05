"""上游名单 → ParentDesign（只读快照，永不覆盖）。

输入 data/inputs/parent_input.json（由 tools/export_parents.py 从测款库导出，或手工写）。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import context as ctx
from . import db

DESIGN_RE = re.compile(r"^([A-Za-z])(\d{4})?(.*)$")


def theme_word(design_name: str) -> str:
    """M1002豹纹波点 → 豹纹波点；M0417粉波点冰淇淋 → 粉波点冰淇淋。"""
    m = DESIGN_RE.match(design_name.strip())
    if not m:
        return design_name.strip()
    tail = (m.group(3) or "").strip(" -_·")
    return tail or design_name.strip()


def find_artwork(design_name: str) -> Path | None:
    """在图案库里找这个设计名的成图（可能在日期子目录里）。"""
    root = ctx.resolve(ctx.get("paths.pattern_library"))
    if not root.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        direct = root / f"{design_name}{ext}"
        if direct.is_file():
            return direct
    for p in root.rglob(f"{design_name}.*"):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return p
    return None


def find_fill_mask(design_name: str) -> Path | None:
    """找 AI 补全区掩码：<设计名>_need.png（说明.txt 里的约定）。"""
    root = ctx.resolve(ctx.get("paths.pattern_process"))
    if not root.is_dir():
        return None
    for p in root.rglob(f"{design_name}*_need.png"):
        if p.is_file():
            return p
    return None


def _assign_parent_id(design_name: str) -> tuple[str, bool]:
    got = db.find_parent_by_design(design_name)
    if got:
        return got["parent_id"], True
    seq = db.next_parent_seq(ctx.today_str())
    return f"P_{ctx.today_str()}_{seq:04d}", False


def ingest_one(entry: dict, allow_suspect: bool = False) -> dict:
    design_name = (entry.get("design_name") or "").strip()
    if not design_name:
        raise ValueError(f"名单里有一条没有 design_name：{entry}")

    trust = entry.get("data_trust") or "unknown"
    skip_suspect = bool(ctx.get("upstream.skip_suspect", False)) and not allow_suspect
    if trust == "suspect" and skip_suspect:
        return {"design_name": design_name, "skipped": "suspect（settings.upstream.skip_suspect=true）"}

    # 名单里可以直接给 source_image（例如本地改字后的修正版，不在图案库里）
    artwork = None
    explicit = entry.get("source_image")
    if explicit:
        p = ctx.resolve(explicit)
        if p.is_file():
            artwork = p
        else:
            return {"design_name": design_name, "skipped": f"指定的 source_image 不存在：{p}"}
    else:
        artwork = find_artwork(design_name)
    if artwork is None:
        return {"design_name": design_name, "skipped": "图案库里找不到成图"}

    parent_id, existing = _assign_parent_id(design_name)
    pdir = ctx.ensure_dir(ctx.parent_dir(parent_id))
    target = pdir / "parent.png"
    src_hash = ctx.sha256_file(artwork)

    # 源文件可能是 jpg/webp，扩展名与实际格式不一致 —— 直接把 jpg 改名叫 parent.png
    # 会让出图通道拒收（实测："文件扩展名与实际图片格式不一致"）。
    # 所以这里解码后按真 PNG 存一份快照，并记录**图片真实尺寸**（不套用配置里的画布尺寸）。
    from .analyze import cv as imgcv
    snap = imgcv.load_image(artwork)
    snap_h, snap_w = snap.shape[:2]

    if target.is_file():
        old_snap = imgcv.load_image(target)
        if old_snap.shape[:2] != (snap_h, snap_w):
            raise RuntimeError(
                f"{parent_id} 的母图快照尺寸和图案库文件对不上，母款不允许被覆盖：\n"
                f"  快照 {target} {old_snap.shape[1]}x{old_snap.shape[0]}\n"
                f"  现在 {artwork} {snap_w}x{snap_h}\n"
                f"  要么图案库确实换过图（那就新建一条母款），要么先人工确认。")
    else:
        imgcv.save_image(target, snap)

    mask = find_fill_mask(design_name)
    parent = {
        "parent_id": parent_id,
        "design_name": design_name,
        "source_image": str(artwork),
        "source_hash": src_hash,
        "artwork_wh": [int(snap_w), int(snap_h)],
        "template": ctx.geometry().get("template"),
        "fill_mask": str(mask) if mask else None,
        "fill_ratio": None,
        "data_trust": trust,
        "theme_word": theme_word(design_name),
        "upstream": entry.get("upstream") or {
            k: entry.get(k) for k in
            ("item_id", "url", "shop_id", "window_days", "pay_byr", "pay_amt", "uv",
             "cart_cnt", "quality") if entry.get(k) is not None
        },
        "ip_risk": None,
        "created_at": ctx.now_iso(),
    }
    ctx.write_json(pdir / "parent.json", parent)
    db.upsert_parent({**parent, "status": "dna" if (pdir / "dna.json").is_file() else "ingested"})
    return {"design_name": design_name, "parent_id": parent_id,
            "existed": existing, "artwork": str(artwork),
            "fill_mask": str(mask) if mask else None}


def run(parent_input: str | None = None, allow_suspect: bool = False,
        only: list[str] | None = None) -> dict:
    path = ctx.resolve(parent_input or (ctx.data_dir() / "inputs" / "parent_input.json"))
    data = ctx.read_json(path)
    if data is None:
        raise FileNotFoundError(f"没有母款名单：{path}（先跑 export-parents，或手工写一份）")
    entries = data.get("parents") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"{path} 格式不对：应该是 {{parents:[...]}} 或 [...]")
    if only:
        keep = set(only)
        entries = [e for e in entries if e.get("design_name") in keep]

    done, skipped = [], []
    for entry in entries:
        res = ingest_one(entry, allow_suspect=allow_suspect)
        (skipped if res.get("skipped") else done).append(res)
        if res.get("skipped"):
            ctx.warn(f"跳过 {res['design_name']}：{res['skipped']}")
        else:
            ctx.info(f"摄取 {res['parent_id']}  {res['design_name']}"
                     f"{'（已存在，复用）' if res['existed'] else ''}")
    return {"done": done, "skipped": skipped}
