"""批次导出：把通过质检的子款整理成下游能直接吃的目录。

交付契约见 系统设计.md 第 1 节：images/ + metadata.json + manifest.csv + report.json + review.html
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from .. import context as ctx
from .. import db


def _batch_id(parent_id: str) -> str:
    return f"{parent_id}_{ctx.today_str()}"


def build(parent_id: str, copy_images: bool = True,
          include_rejected: bool = False) -> dict:
    pdir = ctx.parent_dir(parent_id)
    parent = ctx.read_json(pdir / "parent.json") or {}
    dna = ctx.read_json(pdir / "dna.json") or {}
    plan = ctx.read_json(pdir / "plan.json") or {}
    specs = {s["child_id"]: s for s in plan.get("specs", [])}
    analysis = ctx.read_json(pdir / "analysis.json") or {}

    kids = db.children_of(parent_id)
    chosen = [k for k in kids if k.get("status") in ("passed", "selected")]
    forced: list[str] = []
    if include_rejected:
        # 用户明确要求"全部交付"：把质检淘汰的也一起交，但要在元数据里留痕
        for k in kids:
            if k.get("status") == "rejected" and k.get("image_path"):
                chosen.append(k)
                forced.append(k["child_id"])
    if not chosen:
        ctx.warn("没有通过质检的子款可导出")
        return {"batch": None, "count": 0}

    batch = ctx.ensure_dir(ctx.out_dir() / "batches" / _batch_id(parent_id))
    img_dir = ctx.ensure_dir(batch / "images")
    # 重新导出时先清空 images/：否则上一版被淘汰的图会一直留在批次目录里，
    # 下游按目录取图就会拿到不该发的款（实测踩过）
    for stale in img_dir.iterdir():
        if stale.is_file():
            stale.unlink()

    records, rows = [], []
    for i, k in enumerate(chosen, 1):
        spec = specs.get(k["child_id"], {})
        src = ctx.resolve(k["image_path"])
        name = f"{i:02d}_{k.get('child_name') or k['child_id']}"
        dst = img_dir / f"{name}.png"
        if copy_images and src.is_file():
            shutil.copyfile(src, dst)
        raw_verdicts = db.verdicts_of(k["child_id"])
        # 可移植版的 db 直接把 reasons/detail 存成结构（JSON 文件后端）；
        # 原版 SQLite 存的是 reasons_json / detail_json 两列。两种都认。
        def _reasons(v: dict) -> list:
            if "reason_codes" in v:
                return list(v.get("reason_codes") or [])
            return json.loads(v.get("reasons_json") or "[]")

        def _detail(v: dict) -> dict:
            if "detail" in v:
                return dict(v.get("detail") or {})
            return json.loads(v.get("detail_json") or "{}")

        verdicts = [{"gate": v["gate"], "passed": bool(v["passed"]),
                     "reason_codes": _reasons(v),
                     "score": v["score"]} for v in raw_verdicts]
        sim_scores = next((_detail(v) for v in raw_verdicts if v["gate"] == "G3"), {})
        record = {
            "child_id": k["child_id"],
            "child_name": k.get("child_name"),
            "parent_id": parent_id,
            "parent_design": parent.get("design_name"),
            "generation": k.get("generation") or 1,
            "mutation_operator": k.get("operator"),
            "operator_parts": spec.get("operator_parts"),
            "dose": k.get("dose"),
            "mutation_strength": k.get("strength"),
            "variant_axis": k.get("variant_axis"),
            "intent": spec.get("intent"),
            "changed_genes": spec.get("target_gene"),
            "preserved_genes": spec.get("preserved_gene"),
            "prompt": (ctx.resolve(k["prompt_path"]).read_text(encoding="utf-8")
                       if k.get("prompt_path") and ctx.resolve(k["prompt_path"]).is_file() else ""),
            "prompt_hash": k.get("prompt_hash"),
            "reference_image": str((pdir / "parent.png").relative_to(ctx.ROOT)),
            "generation_model": k.get("gen_model"),
            "image": f"images/{dst.name}",
            "image_hash": k.get("image_hash"),
            "similarity": sim_scores,
            "verdicts": verdicts,
            "ip_risk": dna.get("ip_risk"),
            "pattern_mode": dna.get("pattern_mode"),
            "status": "selected",
            "source_image_online": (parent.get("upstream") or {}).get("url"),
        }
        records.append(record)
        rows.append({
            "序号": i, "子款名": k.get("child_name"), "child_id": k["child_id"],
            "算子": k.get("operator"), "强度": k.get("strength"),
            "与母款相似度": (sim_scores or {}).get("to_parent"),
            "IP风险": dna.get("ip_risk"), "图片": dst.name,
        })
        db.set_child_status(k["child_id"], "selected")

    meta = {
        "batch_id": _batch_id(parent_id),
        "created_at": ctx.now_iso(),
        "parent": {
            "parent_id": parent_id, "design_name": parent.get("design_name"),
            "source_image": parent.get("source_image"),
            "data_trust": parent.get("data_trust"),
            "upstream": parent.get("upstream"),
            "ip_risk": dna.get("ip_risk"),
        },
        "dna_summary": dna.get("summary"),
        "core_gene": [g["human"] for g in dna.get("core_gene", [])],
        "analysis_facts": {k: v for k, v in (analysis.get("facts") or {}).items()
                           if k in ("subject_area_ratio", "whitespace_ratio", "palette",
                                    "color_temperature", "motif_count", "fill_ratio")},
        "children": records,
        "note": ("子款是「受控变异」的结果：保留核心基因，只改一个变量。"
                 "下一步要交给套图/印刷工序产出上架素材，本系统只出图案与元数据。"),
        "delivered_against_qc": forced,
        "delivered_against_qc_note": ("这些子款被质检判过不合格，但用户要求一并交付："
                                      "请重点看它们的 verdicts（例如 CAMERA_BLOCKED 的，"
                                      "印刷前确认摄像头挖孔不会切到主体；SIZE_MISMATCH 的，"
                                      "印刷工具会按画布自动缩放适配）。") if forced else None,
    }
    ctx.write_json(batch / "metadata.json", meta)

    with open(batch / "manifest.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "batch_id": meta["batch_id"],
        "count": len(records),
        "operators": {r["mutation_operator"]: sum(
            1 for x in records if x["mutation_operator"] == r["mutation_operator"])
            for r in records},
        "similarity": {
            "min": min((r["similarity"] or {}).get("to_parent", 0) for r in records),
            "max": max((r["similarity"] or {}).get("to_parent", 0) for r in records),
            "calibrated": bool(ctx.thresholds().get("calibrated")),
        },
        "rejected_total": sum(1 for k in kids if k.get("status") == "rejected"),
        "warning": ("相似度阈值未标定，先用着，别据此下结论"
                    if not ctx.thresholds().get("calibrated") else None),
    }
    ctx.write_json(batch / "report.json", report)
    _html(batch, meta, rows)
    db.set_parent_status(parent_id, "exported")
    ctx.info(f"批次导出：{batch}（{len(records)} 张）")
    return {"batch": str(batch), "count": len(records)}


def _html(batch: Path, meta: dict, rows: list[dict]) -> None:
    cards = []
    for r in rows:
        cards.append(f"""
  <figure>
    <img src="images/{r['图片']}" alt="{r['子款名']}">
    <figcaption><b>{r['子款名']}</b><br>{r['算子']} · 强度 {r['强度']}
    · 相似度 {r['与母款相似度']}</figcaption>
  </figure>""")
    html = f"""<!doctype html><meta charset="utf-8">
<title>{meta['batch_id']} · 裂变批次</title>
<style>
 body{{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:24px;background:#fafafa}}
 h1{{font-size:20px}} .meta{{color:#555;font-size:14px;margin-bottom:16px}}
 .grid{{display:flex;flex-wrap:wrap;gap:16px}}
 figure{{margin:0;background:#fff;padding:8px;border-radius:8px;box-shadow:0 1px 4px #0001;width:220px}}
 img{{width:100%;display:block;border-radius:4px}}
 figcaption{{font-size:12px;color:#333;margin-top:6px;line-height:1.5}}
</style>
<h1>{meta['batch_id']}</h1>
<div class="meta">母款 {meta['parent']['design_name']}（{meta['parent']['parent_id']}）
 · {meta['dna_summary']} · 共 {len(rows)} 张</div>
<div class="grid">{''.join(cards)}</div>
"""
    (batch / "review.html").write_text(html, encoding="utf-8")
