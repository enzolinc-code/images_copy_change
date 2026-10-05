"""命令行入口。工作目录取环境变量 IMGCOPY_HOME（默认当前目录）。

  python -m imgcopy doctor                自检
  python -m imgcopy export-parents        从上游库导出母款名单（可选）
  python -m imgcopy ingest                摄取母款（只读快照）
  python -m imgcopy analyze --all         视觉分析
  python -m imgcopy dna     --all         提取爆款基因
  python -m imgcopy plan    --all [--count 10] [--operators color] [--style <风格id>]
  python -m imgcopy generate <parent_id> [--go] [--limit 3]
  python -m imgcopy qc      --all         质检
  python -m imgcopy export  --all         导出批次（只导通过质检的）
  python -m imgcopy handoff --all [--drop]
  python -m imgcopy status
"""

from __future__ import annotations

import argparse
import sys

from . import context as ctx
from . import db
from . import ingest as ingest_mod


def _parents_from_args(args) -> list[str]:
    ids = list(getattr(args, "parent_ids", []) or [])
    if getattr(args, "all", False):
        ids += [p["parent_id"] for p in db.list_parents()]
    return ids


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.replace("，", ",").split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="liebian", description="手机壳爆款裂变系统")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="自检")

    p = sub.add_parser("export-parents", help="从测款库导出母款名单")
    p.add_argument("--out")
    p.add_argument("--allow-suspect", action="store_true")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("ingest", help="摄取母款（只读快照）")
    p.add_argument("--input")
    p.add_argument("--allow-suspect", action="store_true")
    p.add_argument("--only")

    for name, help_text in (("analyze", "视觉分析（CV 事实 + 语义标签）"),
                            ("dna", "提取爆款基因"),
                            ("plan", "生成裂变计划")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("parent_ids", nargs="*")
        p.add_argument("--all", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--replan", action="store_true")
        p.add_argument("--count", type=int)
        p.add_argument("--operators", help="只排这几类算子，逗号分隔，例：color,composition,accessory")
        p.add_argument("--style", help="用哪张风格卡（<styles_dir>/<id>/style.json）")

    p = sub.add_parser("generate", help="出图（默认 dry-run）")
    p.add_argument("parent_id")
    p.add_argument("--go", action="store_true", help="真的调用付费出图通道")
    p.add_argument("--limit", type=int)
    p.add_argument("--only")
    p.add_argument("--force", action="store_true")

    for name, help_text in (("qc", "质检：G0 技术 + G1 像素 + G3 相似度"),
                            ("export", "导出批次（只导通过质检的）"),
                            ("handoff", "生成套图/印刷交接单（含印刷版）")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("parent_ids", nargs="*")
        p.add_argument("--all", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--drop", action="store_true",
                       help="把印刷版放进 K:\\自动套图 新\\印刷文件\\<日期>\\")
        p.add_argument("--include-rejected", action="store_true",
                       help="连质检淘汰的一起交付（用户明确要求时用）")

    sub.add_parser("status", help="看进度")

    args = ap.parse_args(argv)
    ctx.hr(f"liebian · {args.cmd}")

    if args.cmd == "doctor":
        from .tools import doctor
        return doctor.run()

    if args.cmd == "export-parents":
        from .tools import export_parents
        try:
            export_parents.run(args.out, args.allow_suspect, args.limit)
        except FileNotFoundError as exc:
            ctx.error(str(exc))
            return 2
        return 0

    if args.cmd == "ingest":
        res = ingest_mod.run(args.input, args.allow_suspect, _split(args.only))
        ctx.info(f"\n摄取完成：新增/复用 {len(res['done'])}，跳过 {len(res['skipped'])}")
        return 0

    if args.cmd == "analyze":
        from .analyze import analyzer, vision
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        awaiting = 0
        for pid in ids:
            try:
                analyzer.run(pid, force=args.force)
            except vision.VisionAwaiting as exc:
                awaiting += 1
                ctx.warn(f"{pid} 在等视觉标签：\n{exc}")
        return 3 if awaiting else 0

    if args.cmd == "dna":
        from .dna import extractor
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        for pid in ids:
            extractor.build(pid, force=args.force)
        return 0

    if args.cmd == "plan":
        from .plan import planner
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        for pid in ids:
            planner.build(pid, count=args.count, replan=args.replan,
                          only_operators=_split(getattr(args, "operators", None)),
                          style_id=getattr(args, "style", None))
        return 0

    if args.cmd == "generate":
        from .generate import runner
        res = runner.run(args.parent_id, go=args.go, limit=args.limit,
                         only=_split(args.only), force=args.force)
        return 0 if not res.get("failed") else 1

    if args.cmd == "qc":
        from .qc import runner as qc_runner
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        for pid in ids:
            try:
                qc_runner.run(pid, force=args.force)
            except FileNotFoundError as exc:
                ctx.warn(f"跳过 {pid}：{exc}")
        return 0

    if args.cmd == "export":
        from .select import exporter
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        for pid in ids:
            try:
                exporter.build(pid, include_rejected=bool(getattr(args, "include_rejected", False)))
            except FileNotFoundError as exc:
                ctx.warn(f"跳过 {pid}：{exc}")
        return 0

    if args.cmd == "handoff":
        from .select import handoff
        ids = _parents_from_args(args)
        if not ids:
            ctx.error("要给 parent_id，或用 --all")
            return 2
        for pid in ids:
            try:
                handoff.build(pid, drop_to_print_dir=bool(getattr(args, "drop", False)))
            except FileNotFoundError as exc:
                ctx.warn(f"跳过 {pid}：{exc}")
        return 0

    if args.cmd == "status":
        ctx.info(f"{'母款':<20} {'状态':<10} {'选题词':<12} {'IP':<6} 子款（计划/出图/通过）")
        for p in db.list_parents():
            kids = db.children_of(p["parent_id"])
            planned = sum(1 for k in kids if k["status"] == "planned")
            generated = sum(1 for k in kids if k["status"] in
                            ("generated", "passed", "selected", "exported"))
            passed = sum(1 for k in kids if k["status"] in ("passed", "selected", "exported"))
            ctx.info(f"{p['parent_id']:<20} {str(p.get('status')):<10}"
                     f" {(p.get('theme_word') or ''):<12} {str(p.get('ip_risk')):<6}"
                     f" {planned}/{generated}/{passed}")
        return 0

    ctx.error(f"未知命令：{args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
