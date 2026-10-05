"""Design DNA：把 analysis.json 变成 CORE / MUTABLE / DISPOSABLE 三类基因。

关键设计（和原始需求略有出入，见 系统设计.md 第 8 节）：
主体被拆成两条基因
  · subject_slot = 「超大/大/中尺寸的居中主体」→ 核心基因，永远不许动
  · subject_kind = 「猫 / 少女 / 短句 …」       → 可变基因，主体替换动的就是它
这样「猫→狗」才算**单变量变异**：换的是具体形象，保住的是视觉重量与构图骨架。
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import context as ctx
from .. import db


def _gene(gene_id: str, gene_type: str, value: str, weight: float, confidence: float,
          evidence: dict, in_fill: bool = False, human: str = "") -> dict:
    return {
        "gene_id": gene_id,
        "gene_type": gene_type,
        "value": value,
        "weight": round(float(weight), 3),
        "confidence": round(float(confidence), 3),
        "evidence": evidence,
        "in_fill_region": bool(in_fill),
        "human": human or value,
    }


def _size_bucket(area: float, buckets: list) -> str:
    for thr, name in buckets:
        if area >= float(thr):
            return name
    return buckets[-1][1]


_HUE_FAMILY = [(8, "红"), (20, "橙"), (35, "黄"), (85, "绿"), (100, "青"),
               (130, "蓝"), (155, "紫"), (170, "粉"), (180, "红")]


def _palette_family(facts: dict, p_rules: dict) -> dict | None:
    """把主导色归成一个「色系家族 + 档位」，这才是配色变异要保护的东西。"""
    palette = facts.get("palette") or []
    if not palette:
        return None
    main = max(palette, key=lambda p: float(p.get("ratio") or 0))
    ratio = float(main.get("ratio") or 0)
    if ratio < float(p_rules.get("family_min_ratio", 0.15)):
        return None
    rgb = np.array([[main["rgb"]]], dtype=np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0]
    h, s, v = float(hsv[0]), float(hsv[1]) / 255.0, float(hsv[2]) / 255.0
    if s < 0.12 or v > 0.92 and s < 0.2:
        family = "灰白"
    elif v < 0.22:
        family = "深色"
    else:
        family = next(name for edge, name in _HUE_FAMILY if h < edge)
    temp = float(facts.get("color_temperature") or 0)
    temp_desc = "冷调" if temp < -0.05 else ("暖调" if temp > 0.05 else "中性")
    sat = float(facts.get("saturation") or 0)
    sat_desc = "低饱和" if sat < 0.25 else ("中饱和" if sat < 0.55 else "高饱和")
    return {
        "value": f"{temp_desc}{family}系（{sat_desc}）",
        "human": (f"整体配色家族：{temp_desc}{family}系、{sat_desc}（主色 {main['hex']}"
                  f" 占 {round(ratio * 100)}%）：配色变异允许换色系，但要保持明度与饱和度档位；"
                  f"其它算子不许动它"),
    }


def build(parent_id: str, force: bool = False) -> dict:
    pdir = ctx.parent_dir(parent_id)
    parent = ctx.read_json(pdir / "parent.json")
    analysis = ctx.read_json(pdir / "analysis.json")
    if not parent:
        raise FileNotFoundError(f"没有母款 {parent_id}")
    if not analysis:
        raise FileNotFoundError(f"还没做视觉分析：{pdir / 'analysis.json'}")
    out_path = pdir / "dna.json"
    if out_path.is_file() and not force:
        ctx.info(f"基因已存在，跳过：{out_path}")
        return ctx.read_json(out_path)

    labels = analysis.get("labels") or {}
    facts = analysis.get("facts") or {}
    rules = ctx.read_json(ctx.CONFIG_DIR / "dna_rules.json")
    tax = ctx.taxonomy()

    core: list[dict] = []
    mutable: list[dict] = []
    disposable: list[dict] = []
    notes: list[str] = []

    fill_heavy = bool(facts.get("fill_ratio") and facts["fill_ratio"] > 0.5)

    # ---- 先判：这是"通版重复图案"还是"有单一主体的画面"
    pm = rules.get("pattern_mode", {})
    comps_all = list(labels.get("composition") or [])
    many_motifs = (int(facts.get("motif_count") or 0) >= int(pm.get("motif_count_gte", 8))
                   or int(facts.get("element_count") or 0) >= int(pm.get("elements_gte", 8)))
    is_pattern = (any(c in (pm.get("detect_composition") or []) for c in comps_all)
                  and many_motifs)
    if is_pattern:
        notes.append("判定为通版重复图案（没有单一主体）：表情/动作/换主体/换场景这些算子不适用，"
                     "改走「母题变异 + 构图 + 配色 + 风格」")

    # ---- 主体
    subj = labels.get("subject") or {}
    kind = str(subj.get("primary") or "unknown")
    detail = str(subj.get("detail") or "")
    area = float(facts.get("subject_area_ratio") or 0.0)
    s_rules = rules["subject"]
    if is_pattern:
        # 图案型：不产生 subject_slot/subject_kind，改成一条"图案家族"核心基因
        core.append(_gene("pattern_family", "subject",
                          f"通版重复图案：{kind}" + (f"（{detail}）" if detail else ""),
                          float(pm.get("core_gene_weight", 0.9)), 0.85,
                          {"src": "vision", "composition": comps_all},
                          human=f"「{kind}」这种通版重复图案本身（排列节奏、点距、密度不能变）"))
    slot_value = _size_bucket(area, s_rules["size_buckets"]) + "居中主体"
    subj_overlap = facts.get("subject_fill_overlap")
    subj_in_fill = bool(subj_overlap is not None and subj_overlap > 0.3)
    if not is_pattern:
        core.append(_gene("subject_slot", "subject", slot_value, s_rules["weight"], 0.85,
                      {"fact_ref": "subject_area_ratio", "bbox": facts.get("subject_bbox"),
                       "src": "cv"},
                      in_fill=False,
                      human=f"{slot_value}（占壳面 {round(area * 100)}%，位置与大小不能变）"))
    kind_gene = _gene("subject_kind", "subject", kind, s_rules["kind_weight"],
                      0.9 if kind != "unknown" else 0.4,
                      {"src": "vision", "detail": detail},
                      in_fill=subj_in_fill,
                      human=f"主体是{kind}" + (f"（{detail}）" if detail else ""))
    if is_pattern:
        pass
    elif area < float(s_rules["core_when_area_ratio_gte"]):
        notes.append(f"主体只占壳面 {round(area * 100)}%，视觉重量偏小，已降级为可变")
        mutable.append(kind_gene)
    else:
        mutable.append(kind_gene)  # 具体形象归可变（subject_slot 已经是核心了）
        kind_gene["human"] += "（可以换成同尺寸同情绪的另一形象）"
    if subj_in_fill:
        notes.append(f"主体有 {round((subj_overlap or 0) * 100)}% 落在 AI 补全区，换主体前要小心")

    # ---- 构图
    c_rules = rules["composition"]
    comps = list(labels.get("composition") or [])
    for c in comps:
        is_core = (c in c_rules["core_patterns"]
                   and area >= float(c_rules["core_when_subject_area_ratio_gte"]))
        g = _gene(f"composition_{c.replace('-', '_')}", "composition", c,
                  c_rules["core_weight"] if is_core else c_rules["mutable_weight"],
                  0.7, {"src": "vision", "fact_ref": "subject_area_ratio"}, in_fill=fill_heavy,
                  human=f"构图：{c}")
        (core if is_core else mutable).append(g)
    if facts.get("visual_center"):
        vx, vy = facts["visual_center"]
        pos = "偏上" if vy < 0.42 else ("偏下" if vy > 0.58 else "居中")
        core.append(_gene("composition_visual_weight", "composition", f"视觉重心{pos}",
                          0.6, 0.9, {"fact_ref": "visual_center", "src": "cv"},
                          human=f"视觉重心{pos}（不能翻个儿）"))

    # ---- 配色
    p_rules = rules["palette"]
    for i, p in enumerate(facts.get("palette") or []):
        role = p.get("role") or "accent"
        ratio = float(p.get("ratio") or 0)
        # 具体色值一律可变（配色变异就是改它们）；家族与档位由 palette_family 保护
        is_core = False
        w = p_rules["accent_weight"]
        gid = f"palette_{role}" if role in ("background", "dominant", "accent") and i < 4 \
            else f"palette_extra_{i}"
        g = _gene(gid, "palette", p["hex"], w, 0.95,
                  {"fact_ref": f"palette[{i}]", "ratio": ratio, "src": "cv"},
                  in_fill=fill_heavy,
                  human=f"{role} {p['hex']}（占 {round(ratio * 100)}%）")
        (core if is_core else mutable).append(g)

    fam = _palette_family(facts, p_rules)
    if fam:
        core.append(_gene("palette_family", "palette", fam["value"],
                          p_rules["family_core_weight"], 0.7,
                          {"fact_ref": "palette/dominant", "src": "cv"},
                          human=fam["human"]))

    # ---- 风格
    st_rules = rules["style"]
    for name, weight in (labels.get("style") or {}).items():
        is_core = float(weight) >= float(st_rules["core_when_weight_gte"])
        g = _gene(f"style_{name.replace(' ', '_')}", "style", str(name),
                  st_rules["core_weight"] if is_core else max(st_rules["mutable_weight_floor"],
                                                              float(weight)),
                  float(weight), {"src": "vision", "weight": weight}, in_fill=fill_heavy,
                  human=f"风格：{name}")
        (core if is_core else mutable).append(g)
    for s in (labels.get("style_detail") or [])[:3]:
        mutable.append(_gene(f"style_detail_{len(mutable)}", "style", str(s), 0.4, 0.6,
                             {"src": "vision"}, human=f"笔触细节：{s}"))

    # ---- 情绪（需求第 7-H：情绪价值必须保住）
    e_rules = rules["emotion"]
    emotions = sorted((labels.get("emotion") or {}).items(), key=lambda kv: -float(kv[1]))
    for i, (name, weight) in enumerate(emotions):
        is_core = (i == 0)
        zh = tax.get("emotion_zh", {}).get(name, name)
        g = _gene(f"emotion_{name}", "emotion", str(name),
                  e_rules["core_weight"] if is_core else max(e_rules["mutable_weight_floor"],
                                                             float(weight)),
                  float(weight), {"src": "vision"}, human=f"情绪基调：{zh}")
        (core if is_core else mutable).append(g)

    # ---- 视觉钩子
    h_rules = rules["hook"]
    for i, h in enumerate(labels.get("visual_hooks") or []):
        strength = float(h.get("strength") or 0)
        desc = str(h.get("desc") or "")
        # 钩子里若含品牌/商标词（例如画面正中的 "Barbie" 字样），
        # 不能当核心基因保护 —— 那等于要求模型原样复刻商标。
        if any(kw.lower() in desc.lower() for kw in tax.get("ip_like_keywords", [])):
            disposable.append(_gene(f"hook_{i}", "prop", desc, 0.2, strength,
                                    {"region": h.get("region"), "src": "vision",
                                     "ip_hit": True},
                                    human=f"含商标/品牌元素：{desc}（生成时应删除或替换）"))
            notes.append(f"钩子「{desc[:20]}」含品牌词，已按可丢弃处理，不要原样复刻")
            continue
        if strength >= float(h_rules["core_when_strength_gte"]):
            core.append(_gene(f"hook_{i}", "prop", desc, h_rules["core_weight"], strength,
                              {"region": h.get("region"), "src": "vision"},
                              human=f"靠它抓眼球：{desc}"))
        elif strength >= float(rules["disposable"]["hook_strength_lt"]):
            mutable.append(_gene(f"hook_{i}", "prop", desc, 0.4, strength,
                                 {"region": h.get("region"), "src": "vision"},
                                 human=f"次要元素：{desc}"))
        else:
            disposable.append(_gene(f"hook_{i}", "prop", desc, 0.2, strength,
                                    {"region": h.get("region"), "src": "vision"},
                                    human=f"可有可无：{desc}"))

    if int(facts.get("element_count") or 0) >= int(rules["disposable"]["many_elements_gte"]):
        disposable.append(_gene("scatter_elements", "prop",
                                f"散碎小元素（{facts.get('element_count')} 个）", 0.2, 0.5,
                                {"fact_ref": "element_count", "src": "cv"},
                                human="一堆碎小元素，删掉几个不影响辨识度"))

    # ---- 文字（最容易出乱码，单独成一条基因）
    text = labels.get("text") or {}
    replace_from = str(text.get("replace_from") or "").strip()
    if text.get("present"):
        role = text.get("role") or "decoration"
        is_core = role in rules["text"]["core_when_role"]
        content_txt = str(text.get("content") or "")
        # 商标字样不能当核心基因保护：命中品牌/IP 关键词时按"可丢弃"处理，
        # 并在提示里明确要求删掉或替换（合规），否则裂变等于批量放大侵权。
        ip_words = [kw for kw in tax.get("ip_like_keywords", [])
                    if kw.lower() in content_txt.lower()]
        if ip_words and is_core:
            is_core = False
            disposable.append(_gene("text_brand", "text", content_txt, 0.2, 0.8,
                                    {"src": "vision", "ip_hit": ip_words, "role": role},
                                    human=f"商标字样「{content_txt}」——建议删除或换成无商标短词"))
            notes.append(f"文字里命中品牌词 {'、'.join(ip_words)}：已按可丢弃基因处理，"
                         f"生成时应删除或替换（不要原样复刻）")
        text_is_disposable = bool(ip_words and not is_core)
        # 文字的"位置与视觉重量"永远算核心：实测不加这条，模型会把母款自带的文字整段删掉
        content = content_txt
        if replace_from:
            # value 直接放新文字本身：提示词里要写成「把 Barbie 换成 Goodluck」，
            # 不能带“画面文字块（…）”这种包装（实测提示词里会显示成怪句子）
            core.append(_gene("text_slot", "text", content,
                              rules["text"]["position_core_weight"], 0.9,
                              {"src": "vision", "role": role, "replace_from": replace_from},
                              human=f"画面里的文字必须是「{content}」（原图是「{replace_from}」，"
                                    f"必须替换掉，不要照抄原字样）——位置、大小、颜色、字体感觉不变"))
            notes.append(f"文字替换：原「{replace_from}」→ 新「{content}」（合规要求，禁止保留原字样）")
        elif not text_is_disposable:
            core.append(_gene("text_slot", "text", f"画面文字块（{content[:12]}）",
                              rules["text"]["position_core_weight"], 0.7,
                              {"src": "vision", "role": role},
                              human=f"底部/画面里的文字「{content}」要留在原位置，大小、颜色、字体感觉不变"))
        g = _gene("text_content", "text", str(text.get("content") or ""),
                  rules["text"]["core_weight"] if is_core else rules["text"]["mutable_weight"],
                  0.6, {"src": "vision", "role": role}, human=f"画面文字：{text.get('content')}")
        (core if is_core else mutable).append(g)
        notes.append("这张图带文字：生成时只保留它的位置与视觉重量，具体文字建议白名单化（见 R6）")

    # ---- IP 风险
    ip_like = bool((labels.get("subject") or {}).get("ip_like"))
    ip_note = str((labels.get("subject") or {}).get("ip_note") or "")
    ip_hit = [kw for kw in tax.get("ip_like_keywords", [])
              if kw.lower() in (kind + detail + str(parent.get("design_name", ""))).lower()]
    if ip_like or ip_hit:
        ip_risk = rules["ip"]["risk_when_ip_like"]
        notes.append(f"IP 风险：{ip_note or ('命中关键词 ' + '、'.join(ip_hit[:3]))}"
                     "→ 禁用主体替换，只裂配色/构图/道具（见 R10）")
    else:
        ip_risk = "none"

    # ---- 允许 / 禁止的算子
    ops = {k: v for k, v in ctx.operators().items() if not k.startswith("_")}
    types_present = {g["gene_type"] for g in core + mutable + disposable}
    allowed, blocked = [], []
    if is_pattern:
        allowed = [o for o in (pm.get("allowed_operators") or []) if o in ops]
        blocked = [{"operator": o, "why": "通版重复图案：这个算子不适用"}
                   for o in (pm.get("blocked_operators") or []) if o in ops]
    else:
        for name, cfg in ops.items():
            if name == "combo":
                continue
            if name == "subject" and ip_risk == "high":
                blocked.append({"operator": "subject",
                                "why": "IP 风险高：不换主体，避免把侵权风险放大"})
                continue
            requires = cfg.get("requires") or []
            if not requires or any(r in types_present for r in requires):
                allowed.append(name)
            else:
                blocked.append({"operator": name, "why": f"缺少所需基因：{requires}"})
        for o in (rules.get("single_subject_mode", {}).get("blocked_operators") or []):
            if o in allowed:
                allowed.remove(o)
                blocked.append({"operator": o, "why": "有单一主体的款：母题变异不适用"})
    if len(allowed) >= 2:
        allowed.append("combo")

    core_total = round(sum(g["weight"] for g in core), 3)
    dna = {
        "parent_id": parent_id,
        "pattern_mode": bool(is_pattern),
        "design_name": parent.get("design_name"),
        "summary": _summary(kind, slot_value, labels, facts),
        "core_gene": core,
        "mutable_gene": mutable,
        "disposable_gene": disposable,
        "protected_regions": ([{"bbox": facts.get("subject_bbox"), "reason": "IP 主体，禁止替换"}
                               ] if ip_risk == "high" and facts.get("subject_bbox") else []),
        "allowed_operators": allowed,
        "blocked_operators": blocked,
        "ip_risk": ip_risk,
        "weights": {"core_total": core_total,
                    "mutable_total": round(sum(g["weight"] for g in mutable), 3),
                    "disposable_total": round(sum(g["weight"] for g in disposable), 3)},
        "notes": "；".join(notes),
    }
    ctx.write_json(out_path, dna)
    db.set_parent_status(parent_id, "dna", ip_risk=ip_risk)
    ctx.info(f"基因落盘：{out_path}")
    ctx.info(f"  核心 {len(core)} 条 / 可变 {len(mutable)} 条 / 可删除 {len(disposable)} 条"
             f"；允许算子：{allowed}")
    return dna


def _summary(kind: str, slot: str, labels: dict, facts: dict) -> str:
    styles = labels.get("style") or {}
    top_style = max(styles, key=styles.get) if styles else "未知风格"
    emotions = labels.get("emotion") or {}
    top_emo = max(emotions, key=emotions.get) if emotions else "未知情绪"
    return (f"{slot}的{kind}，{top_style} 风格，{top_emo} 基调；"
            f"主体占壳面 {round(float(facts.get('subject_area_ratio') or 0) * 100)}%，"
            f"留白 {round(float(facts.get('whitespace_ratio') or 0) * 100)}%")
