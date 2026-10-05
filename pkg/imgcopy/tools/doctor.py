"""自检：配置、解释器、依赖、路径、上游库、出图后端。跑不通就别往下走。

可移植版把「路径」与「上游库」两项降级为 WARN —— 没有图案库、模板、上游库的机器
照样能用手工名单 + replay 后端把整条流程跑通（用于学习与回归测试）。
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

from .. import context as ctx
from .. import db

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _line(status: str, title: str, detail: str = "") -> tuple[str, str, str]:
    mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}[status]
    return status, f"{mark} {title}", detail


def run() -> int:
    rows: list[tuple[str, str, str]] = []

    rows.append(_line(OK, "解释器", f"{sys.executable}（{sys.version.split()[0]}）"))
    for mod in ("numpy", "PIL", "cv2"):
        try:
            m = importlib.import_module(mod)
            rows.append(_line(OK, f"依赖 {mod}", str(getattr(m, "__version__", "?"))))
        except Exception as exc:  # noqa: BLE001
            rows.append(_line(FAIL, f"依赖 {mod} 缺失",
                              f"{exc}（pip install numpy pillow opencv-python）"))

    rows.append(_line(OK, "工作目录 IMGCOPY_HOME", str(ctx.WORK)))
    rows.append(_line(OK, "随包配置目录", str(ctx.CONFIG_DIR)))

    for name in ("settings.json", "geometry.json", "taxonomy.json", "operators.json",
                 "matrix.json", "thresholds.json", "naming.json", "dna_rules.json"):
        try:
            ctx._load(name)
            rows.append(_line(OK, f"配置 {name}", ""))
        except Exception as exc:  # noqa: BLE001
            rows.append(_line(FAIL, f"配置 {name}", str(exc)))

    for pname in ("pattern_library", "templates_dir", "styles_dir", "print_dir"):
        p = ctx.resolve(ctx.get(f"paths.{pname}"))
        if p.exists():
            extra = f"{len(list(p.iterdir()))} 个条目" if p.is_dir() else \
                    f"{p.stat().st_size / 1024:.0f} KB"
            rows.append(_line(OK, f"路径 {pname}", f"{p}（{extra}）"))
        else:
            rows.append(_line(WARN, f"路径 {pname} 不存在", f"{p}（可选，先手工放素材）"))

    up = ctx.resolve(ctx.get("paths.upstream_db"))
    if up.is_file():
        try:
            con = sqlite3.connect(f"file:{up}?mode=ro", uri=True)
            n = con.execute(
                "SELECT COUNT(*) FROM product WHERE design_name IS NOT NULL").fetchone()[0]
            m = con.execute("SELECT COUNT(*) FROM daily_metric").fetchone()[0]
            con.close()
            rows.append(_line(OK, "上游测款库", f"{n} 条带设计名 / {m} 行日指标"))
        except Exception as exc:  # noqa: BLE001
            rows.append(_line(WARN, "上游测款库读不了", str(exc)))
    else:
        rows.append(_line(WARN, "上游测款库未配置",
                          f"{up}（可选：手写 data/inputs/parent_input.json 也能跑）"))

    geo = ctx.geometry()
    rows.append(_line(WARN if not geo.get("calibrated") else OK,
                      "壳体几何标定",
                      "占位值（未标定）" if not geo.get("calibrated") else "已标定"))
    th = ctx.thresholds()
    rows.append(_line(WARN if not th.get("calibrated") else OK,
                      "相似度阈值",
                      "占位值，需要标定实验" if not th.get("calibrated") else "已标定"))

    from ..generate import base as gen_base
    try:
        gen = gen_base.build()
    except Exception as exc:  # noqa: BLE001
        gen = None
        rows.append(_line(FAIL, "出图后端构建失败", str(exc)))
    if gen is not None:
        for k, v in gen.describe():
            rows.append(_line(OK, f"出图后端 {k}", v))
        if gen.name == "o1key":
            script = str(ctx.get("generator.o1key.script") or "")
            ok_script = script and script != "path/to/o1key_image.py" and Path(script).is_file()
            rows.append(_line(OK if ok_script else WARN, "O1Key 脚本",
                              script if ok_script else
                              f"{script}（请在 config/settings.json 里填真实路径）"))
            try:
                avail = gen.available()
                rows.append(_line(OK if avail else WARN, "O1Key 可用性",
                                  "API Key 已配置" if avail else "没确认到 API Key"))
            except (OSError, PermissionError) as exc:
                rows.append(_line(WARN, "O1Key 可用性",
                                  f"当前环境无法执行脚本（{type(exc).__name__}）"))
            except Exception as exc:  # noqa: BLE001
                rows.append(_line(WARN, "O1Key 可用性检查失败", str(exc)[:200]))

    vtype = ctx.get("vision.type")
    detail = {"agent": "Codex 在环：写请求文件 → 读答案文件",
              "gemini-web": str(ctx.get("vision.gemini_web.script")),
              "none": "只算 CV 事实，不做语义标签"}.get(vtype, "未知")
    rows.append(_line(OK if vtype in ("agent", "gemini-web", "none") else FAIL,
                      f"视觉通道 {vtype}", detail))

    try:
        parents = db.list_parents()
        kids = sum(len(db.children_of(p["parent_id"])) for p in parents)
        rows.append(_line(OK, "本地状态库", f"{db.db_path()}（母款 {len(parents)} / 子款 {kids}）"))
    except Exception as exc:  # noqa: BLE001
        rows.append(_line(FAIL, "本地状态库打不开", str(exc)))

    ctx.hr("自检结果")
    for _, title, detail in rows:
        ctx.info(f"{title:<34} {detail}")
    fails = sum(1 for s, _, _ in rows if s == FAIL)
    warns = sum(1 for s, _, _ in rows if s == WARN)
    ctx.info(f"\n合计：{len(rows)} 项，FAIL {fails}，WARN {warns}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
