"""质检编排：G0 → G1 → G3（相似度）→ 定状态。

判定落进 verdict 表；淘汰的还会在 parents/<id>/rejected/ 写一份 .verdict.json，
理由码固定，方便统计"哪类问题最多"。
"""

from __future__ import annotations

from pathlib import Path

from .. import context as ctx
from .. import db
from ..similarity import engine as sim
from . import cv_qc, technical


def _save_verdict_json(pdir: Path, child_id: str, verdicts: list[dict], status: str) -> None:
    out_dir = ctx.ensure_dir(pdir / ("rejected" if status == "rejected" else "logs"))
    ctx.write_json(out_dir / f"{child_id}.verdict.json",
                   {"child_id": child_id, "status": status, "verdicts": verdicts,
                    "checked_at": ctx.now_iso()})


def run(parent_id: str, force: bool = False) -> dict:
    pdir = ctx.parent_dir(parent_id)
    parent = ctx.read_json(pdir / "parent.json")
    analysis = ctx.read_json(pdir / "analysis.json") or {}
    plan = ctx.read_json(pdir / "plan.json") or {}
    dna = ctx.read_json(pdir / "dna.json") or {}
    if not parent or not analysis:
        raise FileNotFoundError(f"{parent_id} 还没做完分析")
    specs = {s["child_id"]: s for s in plan.get("specs", [])}
    pattern_mode = bool(dna.get("pattern_mode"))

    children = [c for c in db.children_of(parent_id)
                if c.get("image_path")
                and c["status"] in ("generated", "passed", "rejected", "selected", "exported")]
    if not children:
        ctx.info("没有可质检的子款（先出图）")
        return {"checked": 0}

    ctx.hr(f"质检 {len(children)} 张")
    info: dict[str, dict] = {}
    approved: list[dict] = []
    for ch in children:
        cid = ch["child_id"]
        spec = specs.get(cid, {})
        if not spec:
            # 重排计划时可能只排了某几类算子，plan.json 里就没有这些老子款的 spec。
            # 质检需要知道"这张属于哪个算子"（配色允许跳色系、构图允许重排主体），
            # 所以缺 spec 时从数据库子款记录里补一份。
            axis = str(ch.get("variant_axis") or "")
            parts = axis.split(":", 1)[1].split("+") if ":" in axis else ([axis] if axis else [])
            spec = {"operator": ch.get("operator") or (parts[0] if parts else ""),
                    "operator_parts": parts or None,
                    "dose": ch.get("dose"),
                    "target_region": "global",
                    "region_lock": False}
        img = ctx.resolve(ch["image_path"])
        verdicts = [technical.check(img, parent)]
        if verdicts[0]["passed"]:
            verdicts.append(cv_qc.check(img, pdir / "parent.png", parent, analysis, spec))

        scores = sim.compare(img, pdir / "parent.png", analysis.get("facts") or {},
                             pattern_mode=pattern_mode)
        reasons = sim.judge_parent_child(scores)
        verdicts.append({"gate": "G3", "passed": not reasons, "reason_codes": reasons,
                         "detail": scores, "score": scores["to_parent"]})

        all_reasons = [r for v in verdicts for r in v["reason_codes"]]
        passed = all(v["passed"] for v in verdicts)
        status = "passed" if passed else "rejected"
        for v in verdicts:
            db.upsert_verdict(cid, v["gate"], v["passed"], v.get("score"),
                              v["reason_codes"], v.get("detail") or {})
        db.set_child_status(cid, status, reasons=all_reasons)
        _save_verdict_json(pdir, cid, verdicts, status)
        info[cid] = {**ch, "similarity": scores, "reasons": all_reasons}
        ctx.info(f"  {'通过' if passed else '淘汰'} {cid[-26:]:<26}{ch.get('child_name')}  "
                 f"相似度 {scores['to_parent']}  分区 {scores['zone_frame']}/{scores['zone_color']}"
                 f"{'  ｜' + '、'.join(all_reasons) if all_reasons else ''}")
        if passed:
            approved.append(info[cid])

    dups = sim.pairwise(approved, pattern_mode=pattern_mode)
    if dups:
        ctx.hr("批次内近似重复")
        dropped: set[str] = set()
        for d in dups:
            if d["a"] in dropped or d["b"] in dropped:
                continue
            sa = float((db.get_child(d["a"]) or {}).get("strength") or 0)
            sb = float((db.get_child(d["b"]) or {}).get("strength") or 0)
            loser = d["b"] if sa <= sb else d["a"]
            dropped.add(loser)
            db.set_child_status(loser, "rejected", reasons=["DUPLICATE_IN_BATCH"])
            ctx.info(f"  {d.get('a_name')} ≈ {d.get('b_name')}（{d['score']}）→ 淘汰 {loser[-20:]}")
        approved = [c for c in approved if c["child_id"] not in dropped]

    db.set_parent_status(parent_id, "qc")
    summary = {"checked": len(children), "passed": len(approved),
               "rejected": len(children) - len(approved),
               "calibrated": bool(ctx.thresholds().get("calibrated")),
               "reason_histogram": _hist(info)}
    ctx.info(f"\n质检完成：通过 {summary['passed']} / 共 {summary['checked']}；"
             f"原因分布 {summary['reason_histogram']}")
    if not summary["calibrated"]:
        ctx.warn("相似度阈值还是占位值（未标定）：现在只当粗筛，标定后再据此下结论。")
    return summary


def _hist(info: dict[str, dict]) -> dict:
    out: dict[str, int] = {}
    for item in info.values():
        for r in item.get("reasons") or []:
            out[r] = out.get(r, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
