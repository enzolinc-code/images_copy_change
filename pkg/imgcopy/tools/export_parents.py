"""导出母款名单（可移植版：全部路径走 config/settings.json）。

两级来源（settings.json → upstream）：

  ① `baokuan`：直接读「爆款打分系统」的裂变池 `fission_pool` + 最新 `score` 等级（A/B/C/D）。
     那边口径是命中假数据的那一天整行剔除；A = 7 天成交买家 ≥3，B = ≥1。
     本系统不重复判定，照抄结论。

  ② `cekuan`：用测款库自己按窗口筛「有干净流量」的款（可直接用 `upstream.source=cekuan`
     单独跑，也是 baokuan 池里没有图案时的补充来源）。

一条硬约束：**母款必须有图案**。没有图案的款进 `data/inputs/need_extraction.json`，
要先提取图案（另一道工序）再 ingest。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .. import context as ctx
from ..ingest import find_artwork, theme_word

LEDGER_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(\d{9,})\s*\|\s*([^|]*)\|")


def read_ledger(path: str | None = None) -> dict[str, str]:
    """上架台账：item_id → 设计名（素材目录名）。"""
    p = ctx.resolve(path or ctx.get("paths.ledger", "上架台账.md"))
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        m = LEDGER_ROW.match(line)
        if m:
            name = m.group(2).strip()
            if name:
                out[m.group(1)] = name
    return out


def _upstream_design_names() -> dict[str, str]:
    """cekuan.product：item_id → design_name。"""
    up = ctx.resolve(ctx.get("paths.upstream_db"))
    if not up.is_file():
        return {}
    con = sqlite3.connect(f"file:{up}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT item_id, design_name FROM product WHERE design_name IS NOT NULL").fetchall()
    con.close()
    return {str(a): str(b) for a, b in rows}


def _library_index() -> dict[str, Path]:
    root = ctx.resolve(ctx.get("paths.pattern_library"))
    idx: dict[str, Path] = {}
    if not root.is_dir():
        return idx
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            idx.setdefault(p.stem, p)
    return idx


def _match_by_theme(theme: str, library: dict[str, Path]) -> tuple[str, Path] | None:
    """图案库里有没有和题材词同名/近名的成图。"""
    if not theme:
        return None
    key = theme.replace(" ", "").replace("同款", "")
    if key in library:
        return key, library[key]
    for stem, path in library.items():
        core = re.sub(r"^[A-Za-z]\d{4}", "", stem)
        if core and (core in key or key in core) and len(core) >= 3:
            return stem, path
    return None


def _from_baokuan(limit: int) -> tuple[list[dict], list[dict]]:
    dbp = ctx.resolve(ctx.get("paths.baokuan_db"))
    if not dbp.is_file():
        raise FileNotFoundError(
            f"找不到爆款打分库：{dbp}\n"
            f"（把 settings.paths.baokuan_db 指到那边，或把 upstream.source 改成 cekuan）")
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    run_day = con.execute("SELECT MAX(run_day) FROM score").fetchone()[0]
    rows = con.execute(
        """
        SELECT i.item_id, i.title, i.theme, i.style, i.cluster,
               f.variants_planned, f.status AS pool_status,
               s.grade, s.score, s.window_days
          FROM fission_pool f
          JOIN item i ON i.item_id = f.item_id
          LEFT JOIN score s ON s.item_id = f.item_id
                           AND s.run_day = ? AND s.window_days = 7
         ORDER BY COALESCE(s.score, 0) DESC
        """,
        (run_day,),
    ).fetchall()
    con.close()

    ledger = read_ledger()
    upstream_names = _upstream_design_names()
    library = _library_index()
    min_grade = str(ctx.get("upstream.min_grade", "B"))
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    limit_rank = order.get(min_grade, 1)

    ready, blocked = [], []
    for r in rows:
        grade = r["grade"] or "?"
        if order.get(grade, 9) > limit_rank:
            continue
        item_id = str(r["item_id"])
        design = upstream_names.get(item_id) or ledger.get(item_id)
        artwork = None
        matched_from = None
        if design:
            artwork = find_artwork(design)
        if artwork is None:
            hit = _match_by_theme(r["theme"] or "", library)
            if hit:
                design, artwork = hit
                matched_from = "theme"
        entry = {
            "design_name": design or (r["theme"] or item_id),
            "item_id": item_id,
            "source": "baokuan.fission_pool",
            "pool": "fission_pool",
            "grade": grade,
            "score": r["score"],
            "theme": r["theme"],
            "style": r["style"],
            "cluster": r["cluster"],
            "variants_planned": r["variants_planned"],
            "data_trust": "trusted",
            "artwork": str(artwork) if artwork else None,
            "artwork_matched_from": matched_from,
            "title": r["title"],
            "image": None,
        }
        if artwork is None:
            entry["needs_extraction"] = True
            blocked.append(entry)
        else:
            ready.append(entry)
        if len(ready) >= limit:
            break
    return ready, blocked


def _from_cekuan(limit: int) -> tuple[list[dict], list[dict]]:
    up = ctx.resolve(ctx.get("paths.upstream_db"))
    if not up.is_file():
        raise FileNotFoundError(
            f"找不到上游测款库：{up}\n（settings.paths.upstream_db 指到你的 cekuan.db）")
    days = int(ctx.get("upstream.window_days", 7))
    min_byr = float(ctx.get("upstream.min_pay_byr", 1))
    min_intent = float(ctx.get("upstream.min_intent", 0))
    min_clean_days = int(ctx.get("upstream.min_clean_days", 1))
    skip_suspect = bool(ctx.get("upstream.skip_suspect", False))

    con = sqlite3.connect(f"file:{up}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    anchor = con.execute("SELECT MAX(day) FROM daily_metric").fetchone()[0]
    rows = con.execute(
        """
        SELECT p.design_name, p.item_id, p.title, p.url, p.publish_day,
               SUM(CASE WHEN m.quality = 'ok' THEN m.pay_byr  ELSE 0 END) AS clean_byr,
               SUM(CASE WHEN m.quality = 'ok' THEN m.pay_amt  ELSE 0 END) AS clean_amt,
               SUM(CASE WHEN m.quality = 'ok' THEN m.uv       ELSE 0 END) AS clean_uv,
               SUM(CASE WHEN m.quality = 'ok' THEN m.cart_cnt ELSE 0 END) AS clean_cart,
               SUM(CASE WHEN m.quality = 'ok' THEN m.clt_byr  ELSE 0 END) AS clean_clt,
               SUM(CASE WHEN m.quality = 'ok' THEN 1 ELSE 0 END)          AS clean_days,
               SUM(CASE WHEN m.quality != 'ok' THEN 1 ELSE 0 END)         AS dirty_days
          FROM product p
          JOIN daily_metric m ON m.item_id = p.item_id
         WHERE p.design_name IS NOT NULL
           AND m.day >= date(?, ?)
         GROUP BY p.item_id
        HAVING (clean_byr >= ? OR (clean_cart + clean_clt) >= ?) AND clean_days >= ?
         ORDER BY clean_byr DESC, clean_uv DESC
        """,
        (anchor, f"-{days - 1} day", min_byr, min_intent, min_clean_days),
    ).fetchall()
    con.close()

    ready, blocked = [], []
    for r in rows:
        dirty = int(r["dirty_days"] or 0)
        trust = "trusted" if dirty == 0 else "suspect"
        if trust == "suspect" and skip_suspect:
            continue
        artwork = find_artwork(r["design_name"])
        entry = {
            "design_name": r["design_name"],
            "item_id": str(r["item_id"]),
            "source": "cekuan",
            "pool": "clean_traffic",
            "grade": "?",
            "score": None,
            "theme": theme_word(r["design_name"]),
            "style": None,
            "cluster": None,
            "data_trust": trust,
            "clean_days": r["clean_days"],
            "dirty_days": dirty,
            "pay_byr": r["clean_byr"],
            "pay_amt": round(float(r["clean_amt"] or 0), 2),
            "uv": r["clean_uv"],
            "cart_cnt": r["clean_cart"],
            "clt_byr": r["clean_clt"],
            "title": r["title"],
            "url": r["url"],
            "publish_day": r["publish_day"],
            "artwork": str(artwork) if artwork else None,
        }
        if artwork is None:
            entry["needs_extraction"] = True
            blocked.append(entry)
        else:
            ready.append(entry)
        if len(ready) >= limit:
            break
    return ready, blocked


def run(out: str | None = None, allow_suspect: bool = False, limit: int = 50) -> dict:
    source = str(ctx.get("upstream.source", "cekuan")).lower()
    ready: list[dict] = []
    blocked: list[dict] = []
    used: set[str] = set()

    sources = [source]
    if source == "baokuan" and ctx.get("upstream.fallback_cekuan", True):
        sources.append("cekuan")

    errors: list[str] = []
    for src in sources:
        try:
            r, b = (_from_baokuan(limit) if src == "baokuan" else _from_cekuan(limit))
        except FileNotFoundError as exc:
            errors.append(f"{src}: {exc}")
            ctx.warn(f"跳过 {src}：{exc}")
            continue
        except sqlite3.Error as exc:
            errors.append(f"{src}: {exc}")
            ctx.warn(f"跳过 {src}（库读不了）：{exc}")
            continue
        for e in r:
            if e["item_id"] in used:
                continue
            used.add(e["item_id"])
            ready.append(e)
        for e in b:
            if e["item_id"] in used:
                continue
            used.add(e["item_id"])
            blocked.append(e)

    if not ready and not blocked and errors:
        raise FileNotFoundError(
            "一个来源都读不到：\n  " + "\n  ".join(errors) +
            "\n要么把 paths.upstream_db / paths.baokuan_db 指对，"
            "要么手写 data/inputs/parent_input.json 再 ingest。")

    payload = {
        "generated_at": ctx.now_iso(),
        "source": source,
        "criteria": {
            "note": "母款判定属于爆款打分系统；本系统只负责读结论 + 要求有图案。",
            "min_grade": ctx.get("upstream.min_grade"),
            "window_days": ctx.get("upstream.window_days"),
            "min_pay_byr": ctx.get("upstream.min_pay_byr"),
            "min_intent": ctx.get("upstream.min_intent"),
            "dirty_policy": ctx.get("upstream.dirty_policy"),
        },
        "parents": ready,
    }
    path = ctx.write_json(out or (ctx.data_dir() / "inputs" / "parent_input.json"), payload)
    need = ctx.write_json(ctx.data_dir() / "inputs" / "need_extraction.json",
                          {"generated_at": ctx.now_iso(),
                           "how": "先提取图案（另一道工序），产出后再跑 ingest。",
                           "items": blocked})

    ctx.info(f"可用母款 {len(ready)} 个 → {path}")
    for e in ready:
        tag = f"{e.get('grade', '?')}级" if e["source"].startswith("baokuan") else "干净流量"
        ctx.info(f"  {e['design_name']:<22} {tag:<6} 图案✓  {(e.get('theme') or '')[:14]}")
    ctx.info(f"\n缺图案（要先提取）{len(blocked)} 个 → {need}")
    for e in blocked[:15]:
        ctx.info(f"  {e['design_name']:<22} {e.get('grade', '?')}级  "
                 f"{(e.get('theme') or '')[:14]}  item {e['item_id']}")
    if len(blocked) > 15:
        ctx.info(f"  … 其余 {len(blocked) - 15} 个见文件")
    return payload
