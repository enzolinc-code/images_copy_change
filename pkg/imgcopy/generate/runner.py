"""生成编排：计划 → 每条 prompt → 出图 → 落盘 → 记账。

安全默认：dry_run_default=true 时只打印将要发生什么，必须显式 --go 才真的调付费通道。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import context as ctx
from .. import db, guard
from ..analyze import cv
from ..prompt import builder
from . import base as gen_base
from .guide import make_region_guide
from . import postprocess

PARENT_ROLE = "母款图案（构图、配色、笔触、情绪以它为准）"
GUIDE_ROLE_BG = "底稿：纯绿色区域是需要重做的背景区，绿色以外必须原样保留"
GUIDE_ROLE_SUBJECT = "底稿：纯绿色区域是需要重做的主体区，绿色以外必须原样保留"


def run(parent_id: str, go: bool = False, limit: int | None = None,
        only: list[str] | None = None, force: bool = False,
        generator=None) -> dict:
    pdir = ctx.parent_dir(parent_id)
    parent = ctx.read_json(pdir / "parent.json")
    dna = ctx.read_json(pdir / "dna.json")
    plan = ctx.read_json(pdir / "plan.json")
    if not parent or not dna or not plan:
        raise FileNotFoundError(f"{parent_id} 还没走完 ingest → analyze → dna → plan")

    gen = generator or gen_base.build()
    specs = plan["specs"]
    if only:
        keep = set(only)
        specs = [s for s in specs if s["child_id"] in keep or s.get("child_name") in keep]
    if limit:
        specs = specs[:int(limit)]

    todo = []
    for spec in specs:
        if not force:
            got = db.get_child(spec["child_id"])
            if got and got.get("status") in ("generated", "passed", "selected", "exported") \
                    and got.get("image_path") and ctx.resolve(got["image_path"]).is_file():
                continue
        todo.append(spec)

    if not todo:
        ctx.info("没有要生成的子款（都已存在）。要重做加 --force。")
        return {"generated": 0, "cached": 0, "skipped": len(specs)}

    if go:
        guard.check_budget(len(todo), go=True)
    else:
        ctx.info(f"[dry-run] 本次会生成 {len(todo)} 张（不花钱、不调用任何模型）")

    analysis = ctx.read_json(pdir / "analysis.json") or {}
    # 选了风格卡就用卡里的改字规则（优先于母款自带的 prompt_note）
    note = plan.get("prompt_note")
    parent_for_prompt = {**parent, "prompt_note": note} if note else parent
    ref = pdir / "parent.png"
    ref_hash = parent.get("source_hash") or ctx.sha256_file(ref)
    params = dict(ctx.get("generator.o1key", {}) or {})
    out_dir = ctx.ensure_dir(pdir / "children")
    prompt_dir = ctx.ensure_dir(out_dir / "prompts")
    guide_dir = ctx.ensure_dir(ctx.out_dir() / "_jobs" / "guides")

    ctx.hr(f"生成 {len(todo)} 张（后端 {gen.name}）")
    generated = cached = failed = 0
    results = []
    for spec in todo:
        prompt = builder.build_prompt(spec, dna, parent_for_prompt)
        prompt_hash = ctx.sha256_text(prompt)
        prompt_path = prompt_dir / f"{spec['child_id']}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        images = [{"path": str(ref), "role": PARENT_ROLE}]
        # 多参考图：母款定画风，附加参考图定人物/服装（风格迁移类算子用）
        for ex in (parent.get("extra_refs") or []):
            images.append({"path": str(ctx.resolve(ex["path"])),
                           "role": ex.get("role", "附加参考图")})
        # 通版重复图案不做区域锁定：整张都是重复小图形，涂绿反而会让模型以为整幅要重画
        use_lock = bool(spec.get("region_lock")) and not dna.get("pattern_mode")
        if use_lock:
            region = spec.get("target_region") or "background"
            guide_path = guide_dir / f"{spec['child_id']}_{region}.png"
            text_box = None
            if region == "text":
                new_text = ""
                for g in dna.get("core_gene", []) + dna.get("mutable_gene", []):
                    if g.get("gene_id") in ("text_slot", "text_content"):
                        new_text = str(g.get("value") or "")
                        break
                for g in dna.get("core_gene", []) + dna.get("mutable_gene", []):
                    if g.get("gene_type") == "text" and (g.get("evidence") or {}).get("replace_from"):
                        pass
                # 文字区域取自视觉钩子里提到该文字的那条（区域是壳面归一化坐标）
                labels = (analysis.get("labels") or {})
                for h in labels.get("visual_hooks") or []:
                    desc = str(h.get("desc") or "")
                    if new_text and new_text.lower() in desc.lower() and h.get("region"):
                        text_box = h["region"]
                        break
                if text_box is None:
                    for h in labels.get("visual_hooks") or []:
                        if any(k in str(h.get("desc") or "") for k in ("文字", "字样", "短词")):
                            text_box = h.get("region")
                            break
            make_region_guide(ref, analysis, region, guide_path, text_box=text_box)
            images.append({"path": str(guide_path),
                           "role": GUIDE_ROLE_SUBJECT if region == "subject" else GUIDE_ROLE_BG})

        key = guard.cache_key(prompt_hash, ref_hash, gen.name,
                              {"size": params.get("size"), "ar": params.get("ar"),
                               "quality": params.get("quality"), "n": params.get("n")})
        out_path = out_dir / f"{spec['child_id']}.png"

        if not go:
            ctx.info(f"[dry-run] {spec['child_id']}  {spec['child_name']}  "
                     f"[{spec['variant_axis']} {spec['dose']} {spec['mutation_strength']}]")
            ctx.info(f"   参考图 {len(images)} 张；prompt 存到 {prompt_path}")
            ctx.info("   " + prompt.replace("\n", "\n   ")[:1200])
            continue

        hit = guard.cached_image(key)
        if hit and not force:
            # 缓存命中的可能是同一个目标文件（上一轮跑到一半崩了，图已经在 out_path 上），
            # 这时候复制会报 SameFileError —— 直接认它就行。
            if ctx.resolve(hit) != ctx.resolve(out_path):
                shutil.copyfile(hit, out_path)
            cached += 1
            res = gen_base.GenResult(path=str(out_path), model=gen.name, cached=True)
        else:
            if out_path.is_file():
                prev = ctx.ensure_dir(out_dir / "_prev")
                shutil.copyfile(out_path, prev / out_path.name)
                ctx.info(f"  （旧图已备份到 children/_prev/{out_path.name}）")
            try:
                res = gen.generate(gen_base.GenRequest(
                    child_id=spec["child_id"], prompt=prompt, images=images,
                    out_path=out_path, params=params, cache_key=key,
                    intent=spec.get("intent", ""), force=force))
                if res.cached:
                    cached += 1
                else:
                    generated += 1
                if res.path and ctx.resolve(res.path).is_file():
                    guard.remember(key, str(out_path), res.job, res.cost)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                ctx.error(f"{spec['child_id']} 生成失败：{exc}")
                db.set_child_status(spec["child_id"], "planned", reasons=[f"GEN_FAILED: {exc}"])
                continue

        image_hash = ctx.sha256_file(out_path)
        # 统一画布尺寸：大改构图时模型会返回别的分辨率（实测 1132x2060 → 1024 档），
        # 尺寸不一致会让质检 G0 报 SIZE_MISMATCH，也让印刷/套图口径乱。
        # 这里按母款尺寸缩放对齐（等比拉伸到同尺寸，构图仍是模型给的构图）。
        try:
            import cv2 as _cv2
            ref_img = cv.load_image(ref)
            out_img = cv.load_image(out_path)
            if out_img.shape[:2] != ref_img.shape[:2]:
                fixed = _cv2.resize(out_img, (ref_img.shape[1], ref_img.shape[0]),
                                    interpolation=_cv2.INTER_LANCZOS4)
                cv.save_image(out_path, fixed)
                ctx.info(f"  （尺寸 {out_img.shape[1]}x{out_img.shape[0]} → "
                         f"{ref_img.shape[1]}x{ref_img.shape[0]} 已对齐母款）")
                image_hash = ctx.sha256_file(out_path)
        except Exception as exc:  # noqa: BLE001
            ctx.warn(f"尺寸对齐失败（不影响出图）：{exc}")
        # 局部变异：把母款主体原样贴回去（模型会重画主体，不能靠它自觉）
        if use_lock and (spec.get("target_region") in ("background", "accessory")):
            postprocess.raw_copy(out_path, pdir / "children" / "_raw", overwrite=True)
            postprocess.composite_subject(ref, out_path, out_path,
                                          extra_boxes_norm=postprocess.text_boxes(analysis))
            image_hash = ctx.sha256_file(out_path)
        db.upsert_child({
            "child_id": spec["child_id"], "parent_id": parent_id,
            "child_name": spec["child_name"], "generation": 1,
            "operator": spec["operator"], "dose": spec["dose"],
            "strength": spec["mutation_strength"], "variant_axis": spec["variant_axis"],
            "prompt_hash": prompt_hash, "prompt_path": str(prompt_path.relative_to(ctx.ROOT)),
            "reference_hash": ref_hash, "image_hash": image_hash,
            "image_path": str(out_path.relative_to(ctx.ROOT)),
            "gen_model": res.model, "status": "generated",
            "attempt": 1, "cost": res.cost,
        })
        results.append({"child_id": spec["child_id"], "path": str(out_path),
                        "cached": res.cached, "elapsed": res.elapsed})
        ctx.info(f"{'复用' if res.cached else '出图'} {spec['child_id']}  {spec['child_name']}"
                 f"  → {out_path.name}")

    if go:
        db.set_parent_status(parent_id, "generated")
        ctx.info(f"\n完成：新出 {generated} 张 / 复用缓存 {cached} 张 / 失败 {failed} 张")
    else:
        ctx.info("\n以上是 dry-run（没花钱）。确认没问题后加 --go 真出图。")
    return {"generated": generated, "cached": cached, "failed": failed, "results": results}
