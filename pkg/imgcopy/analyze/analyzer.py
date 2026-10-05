"""分析编排：CV 事实 + 语义标签 → analysis.json。"""

from __future__ import annotations

from pathlib import Path

from .. import context as ctx
from .. import db
from . import cv, vision


def _fill_mask_for(parent: dict) -> "object":
    path = parent.get("fill_mask")
    if not path:
        return None
    return cv.load_mask(path)


def run(parent_id: str, force: bool = False) -> dict:
    parent = ctx.read_json(ctx.parent_dir(parent_id) / "parent.json")
    if not parent:
        raise FileNotFoundError(f"没有这个母款：{parent_id}（先跑 ingest）")
    pdir = ctx.parent_dir(parent_id)
    out_path = pdir / "analysis.json"
    existing = ctx.read_json(out_path)
    if existing and not force and existing.get("facts") and existing.get("labels"):
        ctx.info(f"分析已存在，跳过：{out_path}")
        return existing

    geo = ctx.geometry()
    fill = _fill_mask_for(parent)
    ctx.info(f"跑 CV 事实：{parent['design_name']}")
    facts = cv.analyze_canvas(pdir / "parent.png", geo, fill_mask=fill)

    image_hash = parent.get("source_hash") or ctx.sha256_file(pdir / "parent.png")
    labels = None
    backend = ctx.get("vision.type", "agent")
    if backend == "agent":
        labels = vision.read_labels(pdir, parent, image_hash)
        analysis = {
            "parent_id": parent_id,
            "facts_source": f"cv/{ctx.__name__}",
            "labels_source": "agent" if labels else None,
            "facts": facts,
            "labels": labels or {},
        }
        ctx.write_json(out_path, analysis)
        if labels is None:
            req, ans = vision.request_labels(pdir, parent, facts)
            raise vision.VisionAwaiting(
                req, ans,
                f"CV 事实已算完并写入 {out_path}。\n"
                f"  请求：{req}\n"
                f"  请看图后把 JSON 写进：{ans}\n"
                f"  写完再跑一次同样的命令即可继续。",
            )
    elif backend == "gemini-web":
        req, _ = vision.request_labels(pdir, parent, facts)
        prompt = ctx.prompt_template("analyze.md") + "\n\n已知事实：\n" + \
                 ctx.read_json(req)["facts"].__repr__()
        text = vision._run_gemini(prompt, pdir / "preview_parent.jpg")
        labels = vision._normalize_labels(vision._extract_json(text))
        labels["_source"] = "gemini-web"
        ctx.write_json(vision._cache_path("labels", image_hash), labels)
        analysis = {"parent_id": parent_id, "facts_source": "cv", "labels_source": "gemini-web",
                    "facts": facts, "labels": labels}
        ctx.write_json(out_path, analysis)
    elif backend == "none":
        analysis = {"parent_id": parent_id, "facts_source": "cv", "labels_source": None,
                    "facts": facts, "labels": {}}
        ctx.write_json(out_path, analysis)
    else:
        raise ValueError(f"未知的 vision.type={backend}")

    db.set_parent_status(parent_id, "analyzed")
    ctx.info(f"分析完成：{out_path}")
    return analysis
