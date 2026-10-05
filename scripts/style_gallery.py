#!/usr/bin/env python3
"""扫描风格卡目录，生成一个离线的卡片式选择器页面（index.html）。

只用标准库，不依赖任何第三方包。

    python scripts/style_gallery.py --root /path/to/styles [--out index.html]

目录约定：<root>/<style_id>/style.json（可选 refs/、examples/）。
页面：卡片网格 → 点开看示例、可用算子与取值池、命名规则、风险；带「复制命令」与「导出选择」。
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def collect(root: Path) -> list[dict]:
    styles = []
    if not root.is_dir():
        return styles
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        f = d / "style.json"
        if not f.is_file():
            continue
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"跳过 {d.name}：style.json 读不了（{exc}）")
            continue
        s["_dir"] = d.name
        styles.append(s)
    return styles


def _rel(style_dir: str, p: str) -> str:
    return f"{style_dir}/{p}".replace("\\", "/")


def build_html(styles: list[dict]) -> str:
    cards, details = [], []
    for s in styles:
        sid = html.escape(s.get("style_id") or s["_dir"])
        name = html.escape(s.get("name") or sid)
        refs = s.get("refs") or []
        exs = s.get("examples") or []
        cover = _rel(s["_dir"], refs[0]) if refs else (_rel(s["_dir"], exs[0]) if exs else "")
        tags = "".join(f'<span class="tag">{html.escape(str(t))}</span>' for t in (s.get("tags") or []))
        cards.append(f"""
  <article class="card" data-id="{sid}">
    <div class="cover">{f'<img src="{cover}" alt="{name}">' if cover else '<div class="nocover">无封面</div>'}</div>
    <div class="body"><h3>{name}</h3><code>{sid}</code>
      <div class="tags">{tags}</div><div class="count">{len(exs)} 张示例</div></div>
  </article>""")

        ex_html = "".join(
            f'<figure><img src="{_rel(s["_dir"], e)}" alt=""><figcaption>{html.escape(Path(e).stem)}</figcaption></figure>'
            for e in exs)
        op_rows = []
        for op, cfg in (s.get("operators") or {}).items():
            if op.startswith("_") or not isinstance(cfg, dict):
                continue
            vals = cfg.get("values") or []
            names = "、".join(
                str(v.get("name_word") or v.get("text", "")) if isinstance(v, dict) else str(v)
                for v in vals[:12])
            op_rows.append(f"<tr><td>{html.escape(op)}</td><td>{len(vals)}</td><td>{html.escape(names)}</td></tr>")
        parent = s.get("source_parent") or {}
        cmd = (s.get("how_to_use") or {}).get("命令行", "")
        risks = "".join(f"<li>{html.escape(str(r))}</li>" for r in (s.get("qc") or {}).get("known_risk", []))
        details.append(f"""
<section class="detail" id="d-{sid}" hidden>
  <h2>{name} <code>{sid}</code></h2>
  <p class="desc">{html.escape(str(s.get('说明') or ''))}</p>
  <div class="cols">
    <div><h4>示例</h4><div class="grid">{ex_html}</div></div>
    <aside>
      <h4>母款</h4><p>{html.escape(str(parent.get('design_name') or ''))}<br>
        <code>{html.escape(str(parent.get('parent_id') or ''))}</code></p>
      <h4>可用算子</h4><table><tr><th>算子</th><th>取值</th><th>要素</th></tr>{''.join(op_rows)}</table>
      <h4>命名规则</h4><p><code>{html.escape(str((s.get('naming') or {}).get('pattern') or ''))}</code>
        （最多 {(s.get('naming') or {}).get('max_hanzi', 7)} 个汉字）</p>
      <h4>已知风险</h4><ul>{risks}</ul>
      <h4>怎么用</h4><pre id="cmd-{sid}">{html.escape(cmd)}</pre>
      <button class="copy" data-target="cmd-{sid}">复制命令</button>
      <button class="export" data-id="{sid}">导出选择</button>
    </aside>
  </div>
</section>""")

    data = json.dumps({s["_dir"]: s for s in styles}, ensure_ascii=False)
    return f"""<!doctype html><meta charset="utf-8">
<title>风格卡库 · 选卡</title>
<style>
 :root{{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--ink:#1f2328;--muted:#6b7280;--accent:#2f6fed}}
 *{{box-sizing:border-box}}
 body{{margin:0;font-family:system-ui,'Microsoft YaHei',sans-serif;background:var(--bg);color:var(--ink)}}
 header{{padding:20px 24px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:5}}
 header h1{{margin:0;font-size:19px}} header p{{margin:6px 0 0;color:var(--muted);font-size:13px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;padding:20px 24px}}
 .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer}}
 .card:hover{{transform:translateY(-2px);box-shadow:0 6px 18px #0001}}
 .cover{{aspect-ratio:3/4;background:#eceff3;display:flex;align-items:center;justify-content:center;overflow:hidden}}
 .cover img{{width:100%;height:100%;object-fit:cover}} .nocover{{color:var(--muted);font-size:13px}}
 .body{{padding:10px 12px 12px}} h3{{margin:0 0 4px;font-size:15px}} code{{font-size:11px;color:var(--muted)}}
 .tags{{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}}
 .tag{{font-size:11px;background:#eef2f8;color:#3b4b63;border-radius:999px;padding:1px 7px}}
 .count{{margin-top:6px;font-size:11px;color:var(--muted)}}
 .detail{{padding:20px 24px}} .detail h2{{font-size:18px;margin:0 0 6px}}
 .desc{{color:#404852;font-size:13px;max-width:900px}}
 .cols{{display:grid;grid-template-columns:1fr 340px;gap:22px;align-items:start}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}}
 .grid figure{{margin:0;background:#fff;border:1px solid var(--line);border-radius:8px;padding:6px}}
 .grid img{{width:100%;border-radius:4px;display:block}}
 .grid figcaption{{font-size:11px;color:var(--muted);margin-top:4px;text-align:center}}
 aside{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px}}
 aside h4{{margin:12px 0 6px;font-size:13px}} aside h4:first-child{{margin-top:0}}
 table{{width:100%;border-collapse:collapse;font-size:12px}}
 th,td{{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left}}
 pre{{background:#0f172a;color:#e6edf7;padding:10px;border-radius:8px;font-size:11px;white-space:pre-wrap;word-break:break-all}}
 button{{margin:8px 6px 0 0;padding:6px 12px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;font-size:12px}}
 button.export{{background:#fff;color:var(--accent)}}
 ul{{margin:6px 0;padding-left:18px;font-size:12px;color:#404852}}
 .back{{margin:0 24px 6px;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer}}
</style>
<header><h1>风格卡库 · 选卡</h1>
<p>共 {len(styles)} 张卡。点卡片看示例与用法；「复制命令」拿到跑批命令，「导出选择」给流水线读。</p></header>
<div class="cards" id="cards">{''.join(cards)}</div>
<button class="back" id="back" hidden>← 返回全部卡片</button>
{''.join(details)}
<script>
const DATA = {data};
const cards = document.getElementById('cards'), back = document.getElementById('back');
function show(id){{cards.hidden=true;back.hidden=false;
  document.querySelectorAll('.detail').forEach(d=>d.hidden=true);
  const d=document.getElementById('d-'+id); if(d) d.hidden=false; window.scrollTo(0,0);}}
function hideAll(){{document.querySelectorAll('.detail').forEach(d=>d.hidden=true);cards.hidden=false;back.hidden=true;}}
document.querySelectorAll('.card').forEach(c=>c.onclick=()=>show(c.dataset.id));
back.onclick=hideAll;
document.querySelectorAll('.copy').forEach(b=>b.onclick=async()=>{{
  const t=document.getElementById(b.dataset.target).innerText;
  try{{await navigator.clipboard.writeText(t);b.textContent='已复制 ✓';}}catch(e){{b.textContent='请手动选中复制';}}
  setTimeout(()=>b.textContent='复制命令',1500);}});
document.querySelectorAll('.export').forEach(b=>b.onclick=()=>{{
  const s=DATA[b.dataset.id];
  const payload={{style_id:s.style_id,name:s.name,source_parent:s.source_parent,operators:s.operators,
                 text_rule:s.text_rule,naming:s.naming,canvas:s.canvas,chosen_at:new Date().toISOString()}};
  const blob=new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='style_choice_'+s.style_id+'.json';a.click();}});
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成风格卡选择器页面")
    ap.add_argument("--root", required=True, help="风格库目录（里面每个子目录是一张卡）")
    ap.add_argument("--out", default=None, help="输出 html（默认 <root>/index.html）")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    styles = collect(root)
    target = Path(a.out).expanduser() if a.out else root / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_html(styles), encoding="utf-8")
    print(f"风格库：{root}  卡片 {len(styles)} 张")
    for s in styles:
        print(f"  · {s.get('style_id')} {s.get('name')}（示例 {len(s.get('examples') or [])} 张）")
    print(f"页面：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
