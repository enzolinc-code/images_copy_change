"""Prompt Builder：把计划 + 基因事实编译成一条完整提示词，并原样保存。

需求第 16.6 条：每条提示词必须保存。这里是唯一入口。
"""

from __future__ import annotations

from .. import context as ctx


def region_note(spec: dict) -> str:
    if not spec.get("region_lock"):
        return ("本次是整幅重绘：请保持整张图的排版骨架与画面比例与母款一致，"
                "只在指定变量上做变化。")
    region = spec.get("target_region")
    if region == "text":
        return ("本次只改文字：我另外给了一张底稿，底稿里**纯绿色区域就是那行文字所在的位置**。"
                "只把绿色区域里的字样换掉（字体感觉、颜色质感、大小位置照旧），"
                "绿色以外的所有像素必须原样保留，一个都不要重画。")
    if region == "subject":
        return ("本次是局部重绘：我另外给了一张底稿，底稿里**纯绿色区域就是需要重做的区域**"
                "（主体那一块），绿色以外必须原样保留，不要重画、不要改变笔触与颜色。")
    return ("本次是局部重绘：我另外给了一张底稿，底稿里**纯绿色区域（主体以外的背景区）"
            "是需要重做的区域**，主体本身必须原样保留，不要重画主体，"
            "不要改变主体的位置、大小、颜色与线条。")


def changed_text(spec: dict, dna: dict) -> str:
    ops = {k: v for k, v in ctx.operators().items() if not k.startswith("_")}
    cfg = ops.get(spec["operator"]) or {}
    hint = cfg.get("changed_hint_pattern") if dna.get("pattern_mode") else None
    hint = hint or cfg.get("changed_hint")
    if hint:
        return str(hint)
    ids = set(spec.get("target_gene") or [])
    genes = [g for g in (dna.get("core_gene", []) + dna.get("mutable_gene", []))
             if g["gene_id"] in ids]
    if not genes:
        return "（按变异说明里的方向做整体变化，但不要动主体）"
    return "；".join(g["human"] for g in genes)


def kept_text(spec: dict, dna: dict) -> str:
    ids = set(spec.get("preserved_gene") or [])
    core = [g for g in dna.get("core_gene", []) if g["gene_id"] in ids]
    return "；".join(g["human"] for g in core) or "母款的核心视觉特征"


def build_prompt(spec: dict, dna: dict, parent: dict) -> str:
    tmpl = ctx.prompt_template("generate.md")
    ops = {k: v for k, v in ctx.operators().items() if not k.startswith("_")}
    cfg = ops[spec["operator"]]
    op_zh = spec.get("operator_zh") or cfg["zh"]
    negative = "；".join(spec.get("negative") or []) or "不要改动母款的其它特征"

    out = tmpl
    value_text = _fill_placeholders(spec.get("value") or "（按算子默认方向）", dna)
    for key, value in {
        "{{operator_zh}}": op_zh,
        "{{operator}}": spec["operator"],
        "{{dose}}": spec["dose"],
        "{{mutation_strength}}": str(spec["mutation_strength"]),
        "{{changed}}": changed_text(spec, dna),
        "{{kept}}": kept_text(spec, dna),
        "{{value}}": value_text,
        "{{negative}}": negative,
        "{{region_note}}": region_note(spec),
    }.items():
        out = out.replace(key, value)

    header = (
        f"【母款信息】{parent.get('design_name')}｜{dna.get('summary', '')}\n"
        f"【本次编号】{spec['child_id']}（子款名建议：{spec.get('child_name')}）\n"
    )
    # 母款级的补充指令（例如"文字跟着主体改、字体保持母款那种"）——
    # 放在最前面，模型更容易遵守。
    note = str(parent.get("prompt_note") or "").strip()
    return header + _replace_note(dna) + (f"{note}\n" if note else "") + out


def _replace_note(dna: dict) -> str:
    """母款里要替换掉的文字（例如商标字样），必须在提示词里点名，否则模型会照抄原图。"""
    for g in dna.get("core_gene", []) + dna.get("mutable_gene", []):
        src = (g.get("evidence") or {}).get("replace_from")
        if src:
            return (f"【必须先做的一件事】把画面里的「{src}」字样换成「{g['value']}」——"
                    f"位置、大小、颜色、字体感觉照旧，只是文字内容换掉。"
                    f"原字样「{src}」绝对不要保留。\n")
    return ""


def _fill_placeholders(text: str, dna: dict) -> str:
    """把算子取值里的 {母题}/{骨架}/{点缀色}/{底色} 换成母款的实际形态。

    为什么需要：算子取值是写给"这一款"的 —— 波点款说"圆点"，菱形格款就得说"丘比特小天使"。
    写死成某一种图案，换个母款就驴唇不对马嘴（这个坑实测踩过）。
    """
    if "{" not in text:
        return text
    a = ctx.read_json(ctx.parent_dir(dna.get("parent_id") or "") / "analysis.json") or {}
    labels = a.get("labels") or {}
    pat = labels.get("pattern") or {}
    subj = labels.get("subject") or {}
    palette = (a.get("facts") or {}).get("palette") or []
    accent = next((p.get("hex", "") for p in palette if p.get("role") == "accent"), "")
    dominant = palette[0].get("hex", "") if palette else ""
    mapping = {
        "{母题}": pat.get("motif") or subj.get("detail") or subj.get("primary") or "这个母题",
        "{骨架}": pat.get("lattice") or "重复排布",
        "{点缀色}": pat.get("accent_color") or accent or "点缀色",
        "{底色}": pat.get("base_color") or dominant or "底色",
    }
    # {新文字}：改字算子用，取基因里的新文字
    new_text = ""
    for g in dna.get("core_gene", []) + dna.get("mutable_gene", []):
        if g.get("gene_id") in ("text_slot", "text_content") and g.get("value"):
            new_text = str(g["value"])
            break
    mapping["{新文字}"] = new_text or "新文字"
    for k, v in mapping.items():
        text = text.replace(k, str(v))
    return text
