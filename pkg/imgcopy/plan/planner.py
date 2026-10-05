"""Mutation Planner：先出计划，后出图。

计划里每一张图都是**单变量**（或显式标注的组合），这样测款结果回来才能回答
「到底是哪个变量带来了提升」（需求第 9 条）。
"""

from __future__ import annotations

from .. import context as ctx
from .. import db


def _ops() -> dict:
    return {k: v for k, v in ctx.operators().items() if not k.startswith("_")}


def _expand(theme_word: str, count: int, only_ops: list[str] | None = None) -> list[dict]:
    m = ctx.matrix()
    rows: list[dict] = []
    for r in m.get("single_variable", []):
        if only_ops and r["operator"] not in only_ops:
            continue
        for _ in range(int(r.get("n", 1))):
            rows.append({"operator": r["operator"], "dose": r["dose"],
                         "parts": [r["operator"]], "kind": "single"})
    for r in m.get("combos", []):
        if only_ops and "combo" not in only_ops:
            continue
        if only_ops and not set(r.get("parts") or []).issubset(set(only_ops)):
            continue
        for _ in range(int(r.get("n", 1))):
            rows.append({"operator": "combo", "dose": r["dose"],
                         "parts": list(r["parts"]), "kind": "combo"})

    adj = (m.get("by_theme") or {}).get(theme_word or "") or {}
    reduce_ops = list(adj.get("reduce") or [])
    boost_ops = list(adj.get("boost") or [])
    if reduce_ops and boost_ops:
        bi = 0
        for row in rows:
            if row["operator"] in reduce_ops:
                new_op = boost_ops[bi % len(boost_ops)]
                bi += 1
                row.update({"operator": new_op, "parts": [new_op], "dose": "L1",
                            "theme_swapped": True})

    if len(rows) > count:
        # 少出几张时（比如先试 5 张），要跨算子均匀取，不能顺着矩阵从头切
        picked: list[dict] = []
        seen_ops: set[str] = set()
        for row in rows:
            if row["operator"] not in seen_ops:
                picked.append(row)
                seen_ops.add(row["operator"])
            if len(picked) >= count:
                break
        if len(picked) < count:
            for row in rows:
                if row not in picked:
                    picked.append(row)
                if len(picked) >= count:
                    break
        rows = picked[:count]
    return rows


def _fill(rows: list[dict], allowed: list[str], count: int) -> list[dict]:
    """被禁用的算子换成后备算子，保证总数不变。"""
    m = ctx.matrix()
    order = [o for o in (m.get("fallback", {}).get("order") or []) if o in allowed]
    if not order:
        order = [o for o in allowed if o != "combo"] or allowed
    fixed: list[dict] = []
    fi = 0
    for row in rows:
        if row["operator"] in allowed:
            fixed.append(row)
            continue
        if not order:
            continue
        new_op = order[fi % len(order)]
        fi += 1
        fixed.append({"operator": new_op, "parts": [new_op],
                      "dose": ("L1" if new_op != "combo" else "L2"),
                      "kind": "combo" if new_op == "combo" else "single",
                      "swapped_from": row["operator"]})
    while len(fixed) < count and order:
        new_op = order[fi % len(order)]
        fi += 1
        fixed.append({"operator": new_op, "parts": [new_op],
                      "dose": ("L1" if new_op != "combo" else "L2"),
                      "kind": "combo" if new_op == "combo" else "single",
                      "swapped_from": "pad"})
    return fixed[:count]


def _pick(values: list, i: int) -> dict:
    """values 里每一项可以是字符串，也可以是 {text, target_gene_ids}。"""
    if not values:
        return {"text": "", "target_gene_ids": None}
    v = values[i % len(values)]
    if isinstance(v, str):
        return {"text": v, "target_gene_ids": None}
    return {"text": str(v.get("text") or ""),
            "target_gene_ids": v.get("target_gene_ids"),
            "name_word": v.get("name_word")}


def _child_name(theme_word: str, word: str, used: set[str]) -> str:
    cfg = ctx.naming()
    base = (theme_word or "子款").strip()
    max_h = int(cfg["max_hanzi"])
    cand = base + word
    if len(cand) > max_h:
        keep = max(1, max_h - len(word))
        cand = base[:keep] + word
    i = 2
    while cand in used:
        suffix = str(i)
        cand = (base[:max(1, max_h - len(word) - len(suffix))] + word + suffix)
        i += 1
    used.add(cand)
    return cand


def load_style(style_id: str) -> dict:
    """读风格包（选卡即复用）：卡片决定算子取值池与文字规则。"""
    root = ctx.resolve(ctx.get("paths.styles_dir", "styles"))
    f = root / style_id / "style.json"
    if not f.is_file():
        raise FileNotFoundError(f"没有这张风格卡：{f}")
    return ctx.read_json(f)


def render_text_note(style: dict) -> str:
    """把卡片里的改字规则渲染成提示词里那句补充指令。"""
    rule = style.get("text_rule") or {}
    if not rule.get("enabled"):
        return ""
    pairs = []
    for op in (style.get("operators") or {}).values():
        for v in op.get("values") or []:
            if isinstance(v, dict) and v.get("name_word") and v.get("en"):
                pairs.append(f"{v['name_word']}→Blessing {v['en']}")
    tmpl = str(rule.get("prompt_note") or "")
    return tmpl.replace("{map}", "、".join(pairs)) if tmpl else ""


def build(parent_id: str, count: int | None = None, replan: bool = False,
          only_operators: list[str] | None = None, style_id: str | None = None) -> dict:
    pdir = ctx.parent_dir(parent_id)
    parent = ctx.read_json(pdir / "parent.json")
    dna = ctx.read_json(pdir / "dna.json")
    if not dna:
        raise FileNotFoundError(f"还没提取基因：{pdir / 'dna.json'}")
    out_path = pdir / "plan.json"
    if out_path.is_file() and not replan:
        ctx.info(f"计划已存在，跳过：{out_path}（要重排加 --replan）")
        return ctx.read_json(out_path)

    target = int(count or ctx.matrix().get("target_count", 10))
    limit = int(ctx.get("budget.max_images_per_run", 10))
    if target > limit:
        ctx.warn(f"计划张数 {target} 超过单次上限 {limit}，按 {limit} 处理")
        target = limit

    allowed = list(dna.get("allowed_operators") or [])
    if only_operators:
        # 用户点名只用这几类算子（例如"只裂配色/构图/装饰"）
        allowed = [o for o in allowed if o in only_operators]
        if "combo" in allowed and not set(only_operators) >= {"color"}:
            allowed.remove("combo")
    rows = _fill(_expand(parent.get("theme_word") or "", target, only_operators),
                 allowed, target)

    ops = _ops()
    style = load_style(style_id) if style_id else None
    if style:
        # 卡片里的取值池覆盖内置取值（这就是"选卡即复用"的落点）
        for op_name, cfg in (style.get("operators") or {}).items():
            if op_name in ops and cfg.get("values"):
                ops[op_name]["values"] = cfg["values"]
        ctx.info(f"用风格卡 {style_id}：{style.get('name')}"
                 f"（覆盖算子取值：{'、'.join(k for k in (style.get('operators') or {}) if not k.startswith('_'))}）")
    is_pattern = bool(dna.get("pattern_mode"))
    core_ids = [g["gene_id"] for g in dna.get("core_gene", [])]
    mutable = dna.get("mutable_gene", [])
    used_names: set[str] = set()
    specs: list[dict] = []
    coverage: dict[str, int] = {}

    # 编号要接着已有的往下排：否则重排计划时子款 ID 会从 _01 重来，
    # 撞上这个母款已有的子款（实测会把旧子款的记录覆盖成新名字）。
    import re as _re
    seq_max = 0
    for ch in db.children_of(parent_id):
        m = _re.search(r"_(\d{2})$", ch["child_id"])
        if m:
            seq_max = max(seq_max, int(m.group(1)))

    for offset, row in enumerate(rows):
        # seq 只用于生成子款 ID（接着已有的往下排）；取值下标用 offset（从 0 开始），
        # 两者必须分开，否则重排一次取值就会错位（实测踩过）。
        seq = seq_max + offset + 1
        op = row["operator"]
        cfg = ops[op]
        dose = row["dose"]
        strength = float((cfg.get("dose") or {}).get(dose, 0.3))
        strength = min(strength, float(ctx.get("limits.max_strength", 0.8)))

        parts = row["parts"] if op == "combo" else [op]
        def _pool(cfg: dict) -> list:
            if is_pattern and cfg.get("values_pattern"):
                return cfg["values_pattern"]
            return cfg.get("values") or []

        picked = {"text": "", "target_gene_ids": None}
        target_types = []
        for p in parts:
            target_types.extend(ops[p].get("target_types") or [])
        target_genes = [g for g in mutable if g["gene_type"] in target_types]

        # 算子也可以直接点名要动哪些核心基因（例如配色变异动 palette_family）
        forced_ids: list[str] = []
        for p in parts:
            forced_ids.extend(ops[p].get("target_gene_ids") or [])
        if forced_ids:
            prefixes = tuple(forced_ids)
            extra = [g for g in (dna.get("core_gene", []) + mutable)
                     if g["gene_id"].startswith(prefixes) or g["gene_id"] in prefixes]
            seen = {g["gene_id"] for g in target_genes}
            target_genes.extend(g for g in extra if g["gene_id"] not in seen)
        target_ids = [g["gene_id"] for g in target_genes]

        # 值本身能指定更精确的目标基因（例如：配色变异里的「只动点缀色」）
        if op != "combo":
            # 第 1 个孩子用第 1 条取值（原来是 i，会从第二条开始，实测导致"狗"永远抽不到）
            picked = _pick(_pool(cfg), offset)
            if picked.get("target_gene_ids"):
                prefixes = tuple(picked["target_gene_ids"])
                target_genes = [g for g in (dna.get("core_gene", []) + mutable)
                                if g["gene_id"].startswith(prefixes) or g["gene_id"] in prefixes]
                target_ids = [g["gene_id"] for g in target_genes]
        target_set = set(target_ids)
        preserved = [gid for gid in core_ids if gid not in target_set] + \
                    [g["gene_id"] for g in mutable if g["gene_id"] not in target_set]

        if op == "combo":
            value = " + ".join(_pick(_pool(ops[p]), offset)["text"] for p in parts)
            zh = "组合变异（" + "+".join(ops[p]["zh"] for p in parts) + "）"
            negatives: list[str] = []
            keeps: list[str] = []
            for p in parts:
                negatives.extend(ops[p].get("negative") or [])
                keeps.extend(ops[p].get("keeps") or [])
        else:
            value = picked["text"]
            zh = cfg["zh"]
            negatives = list(cfg.get("negative") or [])
            keeps = list(cfg.get("keeps") or [])

        # 命名优先跟着实际改动走（例如配色改了「抹茶绿」就叫「绿」），不要从词池瞎取
        name_word = (picked.get("name_word") if isinstance(picked, dict) else None) \
            or _pick(cfg.get("name_words") or [], offset)["text"]
        child_name = _child_name(parent.get("theme_word") or "", name_word, used_names)

        spec = {
            "child_id": ctx.naming()["child_id_pattern"].format(
                parent_id=parent_id, operator=op.upper(), dose=dose, seq=seq),
            "parent_id": parent_id,
            "generation": 1,
            "operator": op,
            "operator_parts": parts,
            "operator_zh": zh,
            "dose": dose,
            "mutation_strength": round(strength, 3),
            "target_gene": target_ids,
            "preserved_gene": preserved,
            "target_region": cfg.get("target_region"),
            "region_lock": bool(cfg.get("region_lock")),
            "variant_axis": op if op != "combo" else "combo:" + "+".join(parts),
            "intent": (f"{zh}：{value}" if value else zh) + "；保留：" +
                      "、".join(g["human"] for g in dna.get("core_gene", [])[:3]),
            "value": value,
            "keep_types": sorted(set(keeps)),
            "negative": sorted(set(negatives)),
            "child_name": child_name,
            "swapped_from": row.get("swapped_from"),
            "theme_swapped": row.get("theme_swapped", False),
        }
        specs.append(spec)
        coverage[spec["variant_axis"]] = coverage.get(spec["variant_axis"], 0) + 1

    skeleton = float(ctx.thresholds().get("skeleton", {}).get("min_core_gene_keep", 0.85))
    core_weight = float((dna.get("weights") or {}).get("core_total") or 0)
    for spec in specs:
        targeted = set(spec.get("target_gene") or [])
        must = [g for g in dna.get("core_gene", []) if g["gene_id"] not in targeted]
        missing = [g["gene_id"] for g in must if g["gene_id"] not in set(spec["preserved_gene"])]
        if missing:
            raise AssertionError(
                f"{spec['child_id']} 漏了必须保留的核心基因：{missing}")
        kept_w = sum(g["weight"] for g in dna.get("core_gene", [])
                     if g["gene_id"] in set(spec["preserved_gene"]))
        spec["core_keep_ratio"] = round(kept_w / core_weight, 3) if core_weight else 1.0
        if spec["core_keep_ratio"] < skeleton and not targeted:
            raise AssertionError(
                f"{spec['child_id']} 保留的核心基因不足：{spec['core_keep_ratio']} < {skeleton}")

    plan = {
        "plan_id": f"{parent_id}_PLAN_V1",
        "parent_id": parent_id,
        "target_count": len(specs),
        "skeleton_ratio": skeleton,
        "created_by": f"planner/{ctx.__name__} matrix.json",
        "style_id": style_id,
        "prompt_note": render_text_note(style) if style else None,
        "coverage": coverage,
        "specs": specs,
    }
    ctx.write_json(out_path, plan)
    keep_ids = {s["child_id"] for s in specs}
    for spec in specs:
        prev = db.get_child(spec["child_id"]) or {}
        db.upsert_child({
            "child_id": spec["child_id"], "parent_id": parent_id,
            "child_name": spec["child_name"], "generation": 1,
            "operator": spec["operator"], "dose": spec["dose"],
            "strength": spec["mutation_strength"], "variant_axis": spec["variant_axis"],
            # 重排计划不能抹掉已经出好的图：已有记录的状态/图/成本照旧保留
            "status": prev.get("status") or "planned",
            "attempt": prev.get("attempt") or 0,
            "prompt_hash": prev.get("prompt_hash"),
            "prompt_path": prev.get("prompt_path"),
            "reference_hash": prev.get("reference_hash"),
            "image_hash": prev.get("image_hash"),
            "image_path": prev.get("image_path"),
            "gen_model": prev.get("gen_model"),
        })
    # 重排后不再属于本计划的、且还没出图的旧记录：标成 dropped，别让它一直挂在"计划中"
    for ch in db.children_of(parent_id):
        if ch["child_id"] in keep_ids:
            continue
        if ch.get("status") == "planned":
            db.set_child_status(ch["child_id"], "dropped",
                                reasons=["SUPERSEDED_BY_REPLAN"])
    db.set_parent_status(parent_id, "planned")
    ctx.info(f"计划落盘：{out_path}")
    ctx.info(f"  共 {len(specs)} 张；分布：{coverage}")
    for spec in specs:
        ctx.info(f"  - {spec['child_id']}  {spec['child_name']}  [{spec['variant_axis']}"
                 f" {spec['dose']} {spec['mutation_strength']}]  {spec['intent'][:60]}")
    return plan
