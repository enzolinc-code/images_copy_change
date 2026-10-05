"""交付给下游「套图 / 印刷」工序（可移植版）。

本系统只出**图案母版 + 交接单**：不碰套图那 21 张上架素材，不替上架流水线写目录。

做四件事：
  1. 把本批通过质检的子款图案整理到 handoff/patterns/<子款名>.png
  2. 出「整幅版」handoff/final/<子款名>.png —— 满幅铺满印刷画布、**不叠任何定位图形**，
     就是能直接拿去印刷的最终成品（对应规矩文档第 4、12 条）
  3. 出印刷版（透明底 + 壳体裁形 + 摄像头镂空）与预览版：优先用配置里的模板工具
     （paths.template_tool，生产线上是 pattern-extract/tools/put-into-template.py）；
     没配工具时，只在 geometry.calibrated=true 的前提下用几何兜底；没标定就明确不生成，
     因为这会把摄像头挖在错误的位置（规矩文档第 5 条）。
  4. 写交接单 handoff.json（机器读）+ handoff.md（人读），带下一步的具体命令
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .. import context as ctx
from .. import db
from ..analyze import cv as imgcv

DEFAULT_PRINT_CANVAS = (2108, 3744)     # 印刷画布（与图案库的 1252×2232 同比例）


# ---------------------------------------------------------------- 整幅版

def _mirror_index(idx: np.ndarray, n: int) -> np.ndarray:
    """把任意整数位置映射进 [0, n)，用镜像延展（边界处连续，不会出现硬缝）。"""
    if n <= 1:
        return np.zeros_like(idx)
    period = 2 * n - 2
    i = np.abs(idx) % period
    return np.where(i < n, i, period - i)


def _contain_mirror_fill(rgb: np.ndarray, canvas_wh: tuple[int, int]) -> np.ndarray:
    """不裁剪（contain）+ 镜像补边铺满画布：满幅、无白边、无定位图形。"""
    import cv2
    cw, ch = int(canvas_wh[0]), int(canvas_wh[1])
    h, w = rgb.shape[:2]
    scale = min(cw / w, ch / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    core = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
    ox, oy = (cw - nw) // 2, (ch - nh) // 2
    ys = _mirror_index(np.arange(ch) - oy, nh)
    xs = _mirror_index(np.arange(cw) - ox, nw)
    return core[ys][:, xs]


def _template_tool() -> tuple[Path, Path] | None:
    py = ctx.get("paths.template_python") or ctx.get("runtime.python")
    tool = ctx.get("paths.template_tool")
    if not tool:
        return None
    top = ctx.resolve(tool)
    if not top.is_file():
        return None
    return Path(str(py or "python")), top


_TOOL_CACHE: dict = {}


def _tool_module():
    """把外部模板工具当模块用（不复制它的实现）。"""
    if _TOOL_CACHE:
        return _TOOL_CACHE["mod"]
    got = _template_tool()
    if not got:
        raise RuntimeError("没有配置 paths.template_tool")
    _, tool = got
    spec = importlib.util.spec_from_file_location("put_into_template", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _TOOL_CACHE["mod"] = mod
    return mod


def build_final_art(pattern: Path, out_png: Path,
                    canvas_wh: tuple[int, int] | None = None) -> Path:
    """整幅成品图：满幅铺满画布、不画壳体定位框、不叠摄像头。"""
    cw, ch = canvas_wh or DEFAULT_PRINT_CANVAS
    out = ctx.resolve(out_png)
    ctx.ensure_dir(out.parent)
    got = _template_tool()
    if got:
        # 生产线：复用已验证的模板工具。它返回的 canvas 是缩放居中后的图案本身，
        # 壳体轮廓是它 main() 里后面才画的，所以我们拿到的天然没有黑边。
        mod = _tool_module()
        tpl = mod.parse_template(mod.DEFAULT_TPL)
        canvas, _placed, _info = mod.build(str(pattern), tpl, 1.075, 0.662, "white", "canvas")
        import cv2
        # 两个坑：① cv2.imwrite 写不了 Windows 中文路径 → 用 imencode + tofile
        #        ② 上游工具内部是 BGR，不能拿 imgcv.save_image（它按 RGB 处理），
        #           否则粉色会变成紫色（实测踩过）
        ok, buf = cv2.imencode(".png", canvas)
        if not ok:
            raise RuntimeError(f"整幅成品图编码失败：{out}")
        buf.tofile(str(out))
        return out
    art = _contain_mirror_fill(imgcv.load_image(pattern), (cw, ch))
    imgcv.save_image(out, art)
    return out


# ---------------------------------------------------------------- 印刷版

def _rounded_rect_mask(shape, rect_px, radius: int) -> np.ndarray:
    import cv2
    h, w = shape
    x0, y0, x1, y1 = [int(v) for v in rect_px]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    r = max(0, min(radius, (x1 - x0) // 2, (y1 - y0) // 2))
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return mask


def _fallback_print(art_rgb: np.ndarray, geo: dict) -> tuple[np.ndarray, np.ndarray]:
    """几何兜底：按 geometry.json 的壳面/摄像头矩形做壳体裁形。只应在已标定时使用。"""
    import cv2
    h, w = art_rgb.shape[:2]
    face = geo.get("face_rect_in_artwork_norm")
    cam = geo.get("camera_rect_in_artwork_norm")
    if not face or not cam:
        raise RuntimeError("geometry.json 里没有 face_rect_in_artwork_norm / "
                           "camera_rect_in_artwork_norm，无法兜底出印刷版")
    fx0, fy0, fx1, fy1 = [int(round(v * s)) for v, s in
                          zip(face, (w, h, w, h))]
    radius = int(round(float(geo.get("corner_radius_norm", 0.075)) * (fx1 - fx0)))
    shell = _rounded_rect_mask((h, w), (fx0, fy0, fx1, fy1), radius)
    cx0, cy0, cx1, cy1 = [int(round(v * s)) for v, s in zip(cam, (w, h, w, h))]
    hole = _rounded_rect_mask((h, w), (cx0, cy0, cx1, cy1),
                              int(round(0.22 * (cx1 - cx0))))
    mask = cv2.bitwise_and(shell, cv2.bitwise_not(hole))
    alpha = mask
    rgba = np.dstack([art_rgb, alpha])
    white = np.full_like(art_rgb, 255)
    a = (alpha.astype(np.float32) / 255.0)[:, :, None]
    preview = (art_rgb.astype(np.float32) * a + white.astype(np.float32) * (1 - a)).astype(np.uint8)
    return rgba, preview


def _print_one(art: Path, out_dir: Path, geo: dict) -> dict:
    """出印刷版（透明底）与预览版（白底）。"""
    got = _template_tool()
    ctx.ensure_dir(out_dir)
    if got:
        py, tool = got
        proc = subprocess.run(
            [str(py), "-X", "utf8", str(tool), str(art), "--fit", "canvas",
             "--out", str(out_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        files = sorted(p.name for p in out_dir.rglob("*.png"))
        return {"ok": proc.returncode == 0, "files": files, "by": "template_tool",
                "stdout": (proc.stdout or "")[-500:], "stderr": (proc.stderr or "")[-300:]}
    if not geo.get("calibrated"):
        return {"ok": False, "files": [], "by": "none",
                "error": ("未配置模板工具、几何也没标定（geometry.calibrated=false）："
                          "不生成印刷版，避免把摄像头挖在错误位置。"
                          "标定完把 calibrated 改成 true 就能自动出。")}
    import cv2
    art_rgb = imgcv.load_image(art)
    rgba, preview = _fallback_print(art_rgb, geo)
    name = ctx.resolve(art).stem
    ok1, buf1 = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    ok2, buf2 = cv2.imencode(".png", cv2.cvtColor(preview, cv2.COLOR_RGB2BGR))
    if not (ok1 and ok2):
        return {"ok": False, "files": [], "by": "geometry", "error": "编码失败"}
    buf1.tofile(str(out_dir / f"{name}_印刷.png"))
    buf2.tofile(str(out_dir / f"{name}_预览.png"))
    return {"ok": True, "files": [f"{name}_印刷.png", f"{name}_预览.png"],
            "by": "geometry",
            "note": "几何兜底版：换了机型/壳体必须重出（规矩文档第 4、5 条）"}


# ---------------------------------------------------------------- 主流程

def _rel(path: Path) -> str:
    try:
        return str(Path(path).relative_to(ctx.WORK))
    except ValueError:
        return str(path)


def build(parent_id: str, drop_to_print_dir: bool = False) -> dict:
    batches = sorted((ctx.out_dir() / "batches").glob(f"{parent_id}_*"))
    if not batches:
        raise FileNotFoundError(f"没有这个母款的批次：{parent_id}（先 export）")
    batch = batches[-1]
    parent = ctx.read_json(ctx.parent_dir(parent_id) / "parent.json") or {}
    dna = ctx.read_json(ctx.parent_dir(parent_id) / "dna.json") or {}
    geo = ctx.geometry()
    naming = ctx.naming()

    hand = ctx.ensure_dir(batch / "handoff")
    pat_dir = ctx.ensure_dir(hand / "patterns")
    kids = [k for k in db.children_of(parent_id)
            if k.get("status") in ("selected", "exported") and k.get("image_path")]
    if not kids:
        raise FileNotFoundError("这个批次没有已选中的子款")

    items, print_results = [], []
    for k in kids:
        name = k.get("child_name") or k["child_id"]
        src = ctx.resolve(k["image_path"])
        dst = pat_dir / f"{name}.png"
        shutil.copyfile(src, dst)
        hanzi = sum(1 for ch in name if "\u4e00" <= ch <= "\u9fff")
        final = build_final_art(src, hand / "final" / f"{name}.png")
        pr = _print_one(final, hand / "print" / name, geo)
        pr["final_art"] = str(final)
        item = {
            "design_name": name,
            "child_id": k["child_id"],
            "parent_design": parent.get("design_name"),
            "parent_id": parent_id,
            "pattern_file": _rel(dst),
            "pattern_wh": [ctx.get("canvas.width"), ctx.get("canvas.height")],
            "operator": k.get("operator"),
            "mutation_strength": k.get("strength"),
            "variant_axis": k.get("variant_axis"),
            "prompt_file": k.get("prompt_path"),
            "ip_risk": dna.get("ip_risk"),
            "name_hanzi": hanzi,
            "name_ok_for_title": hanzi <= int(naming["max_hanzi"]),
            "final_art": _rel(final),
            "print": pr,
        }
        print_results.append({"name": name, **pr})
        items.append(item)

    dropped: list[str] = []
    if drop_to_print_dir:
        cfg = ctx.get("paths.handoff", {}) or {}
        mmdd = ctx.today_str()[4:]
        target = ctx.ensure_dir(ctx.resolve(ctx.get("paths.print_dir", "print")) / mmdd)
        for item in items:
            for f in item["print"].get("files") or []:
                kind = "印刷" if "印刷" in f else ("预览" if "预览" in f else "")
                if kind not in (cfg.get("print_kinds") or ["印刷", "预览", ""]):
                    continue
                src = item["print"]["final_art"] if kind == "" else \
                    (hand / "print" / item["design_name"] / f)
                src = Path(src)
                if not src.is_file():
                    continue
                tpl = cfg.get("print_name_template", "M{mmdd}-{design}_{kind}.png") if kind \
                    else cfg.get("print_flat_template", "M{mmdd}-{design}.png")
                name = tpl.format(mmdd=mmdd, design=item["design_name"], kind=kind)
                shutil.copyfile(src, target / name)
                dropped.append(str(target / name))
        # 整幅版始终往图案库送一份（命名不带 _整幅版 后缀），方便直接套图
        lib_root = ctx.resolve(ctx.get("paths.pattern_library", "patterns"))
        lib = ctx.ensure_dir(lib_root / mmdd)
        for item in items:
            src = Path(item["print"]["final_art"])
            if not src.is_file():
                continue
            lib_name = (cfg.get("library_name_template", "M{mmdd}{design}.png")
                        .format(mmdd=mmdd, design=item["design_name"]))
            shutil.copyfile(src, lib / lib_name)
            item["library_file"] = _rel(lib / lib_name)
            dropped.append(str(lib / lib_name))

    handoff = {
        "batch_id": batch.name,
        "created_at": ctx.now_iso(),
        "from": "images-copy-change pipeline（手机壳爆款裂变系统）",
        "parent": {"parent_id": parent_id, "design_name": parent.get("design_name"),
                   "source_image": parent.get("source_image"),
                   "upstream_url": (parent.get("upstream") or {}).get("url")},
        "items": items,
        "next_steps": {
            "套图": {
                "说明": "每个子款出上架素材（主图 / 三分之四 / SKU / 详情），"
                        "素材目录名必须等于这里的子款名。",
                "命名": "子款名已按 ≤%d 汉字校验（config/naming.json）" % int(naming["max_hanzi"]),
            },
            "印刷": {
                "工具": str(ctx.get("paths.template_tool") or
                            "（未配置：用几何兜底，见 handoff/print/<子款名>/）"),
                "已产出": "handoff/print/<子款名>/ 下是印刷版（透明底 + 壳体裁形 + 摄像头镂空）"
                          "与预览版；handoff/final/<子款名>.png 是**整幅成品图**"
                          "（满幅铺满画布、不带定位图形，可直接拿去印刷）",
                "暂存目录": str(target) if drop_to_print_dir else None,
                "注意": "摄像头挖孔位置必须人工确认不压主体；换机型/换壳体要重新出印刷版。",
            },
        },
        "notes": [
            "本系统只保证图案与参数，不产出上架素材（那是套图工序）。",
            "IP 风险：none 表示母款与子款都没命中品牌/IP 关键词。",
            "每张子款都有血缘：child_id 可回溯到母款、算子、剂量与完整提示词。",
        ],
    }
    ctx.write_json(hand / "handoff.json", handoff)
    (hand / "handoff.md").write_text(_markdown(handoff, dropped), encoding="utf-8")
    ctx.info(f"交接单：{hand / 'handoff.md'}")
    ctx.info(f"图案 {len(items)} 张 → {pat_dir}")
    ok = sum(1 for r in print_results if r["ok"])
    ctx.info(f"印刷版：{ok}/{len(items)} 张成功 → {hand / 'print'}")
    for r in print_results:
        ctx.info(f"  {'OK ' if r['ok'] else '跳过'} {r['name']}："
                 f"{', '.join(r.get('files') or []) or (r.get('error') or '')}")
    if dropped:
        ctx.info(f"已放入交付目录 {len(dropped)} 个文件")
    return handoff


def _markdown(handoff: dict, dropped: list[str]) -> str:
    rows = "\n".join(
        f"| {i['design_name']} | {i['operator']} | {i['mutation_strength']} | "
        f"{'✓' if i['name_ok_for_title'] else '✗'} | {i['print'].get('ok')} |"
        for i in handoff["items"])
    files = "\n".join(f"- `{p}`" for p in dropped) or "（未放入，用 `--drop` 可以自动放）"
    ns = handoff["next_steps"]
    return f"""# 裂变批次交接单 · {handoff['batch_id']}

来源：{handoff['from']}
母款：**{handoff['parent']['design_name']}**（{handoff['parent']['parent_id']}）
母款链接：{handoff['parent'].get('upstream_url') or '—'}

## 一、本批子款（{len(handoff['items'])} 张）

| 子款名（= 素材目录名 = 标题前缀） | 算子 | 强度 | 命名合规 | 印刷版 |
|---|---|---|---|---|
{rows}

- 图案母版：`handoff/patterns/<子款名>.png`（无壳体 / 无定位图形 / 无水印）
- **整幅成品图：`handoff/final/<子款名>.png`（满幅铺满画布、不带定位图形，直接拿去印刷）**
- 印刷版与预览版：`handoff/print/<子款名>/`

## 二、下一步：套图

{ns['套图']['说明']}
{ns['套图']['命名']}

## 三、下一步：印刷

- 工具：`{ns['印刷']['工具']}`
- 已产出：{ns['印刷']['已产出']}
- {ns['印刷']['注意']}

### 已放入交付目录的文件

{files}

## 四、注意事项

{chr(10).join('- ' + n for n in handoff['notes'])}
"""
