#!/usr/bin/env python3
"""把一张「风格卡」发布到风格库：拷过去 + 生成示例缩略图 + 刷新选择器页面。

    python scripts/style_pack_publish.py --pack <卡目录> --root <风格库目录> [--thumb-edge 700]

卡目录里要有 style.json；示例图用卡里的 `example_sources` 指定（相对路径按当前工作目录解析）：

    "example_sources": { "01_母款.png": "path/to/parent.png",
                         "02_变体.png": "path/to/child.png" }

缩略图用 Pillow 生成（缺 Pillow 就直接拷原图，只是页面会重一些）。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:  # 缩略图可选
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None


def make_thumb(src: Path, dst: Path, edge: int) -> bool:
    """生成缩略图；返回 True 表示压过了，False 表示原图直拷。"""
    if Image is None:
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        return False
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((edge, edge))
        im.save(dst, quality=88)
    return True


def publish(pack: Path, root: Path, thumb_edge: int = 700) -> Path:
    style_file = pack / "style.json"
    if not style_file.is_file():
        raise SystemExit(f"卡目录里没有 style.json：{pack}")
    style = json.loads(style_file.read_text(encoding="utf-8"))
    style_id = style.get("style_id")
    if not style_id:
        raise SystemExit("style.json 缺 style_id")

    dst = root / style_id
    (dst / "refs").mkdir(parents=True, exist_ok=True)
    (dst / "examples").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(style_file, dst / "style.json")

    made, missing, copied_raw = 0, [], 0
    for name, rel in (style.get("example_sources") or {}).items():
        src = Path(rel).expanduser()
        if not src.is_file():
            missing.append(name)
            continue
        ok = make_thumb(src, dst / "examples" / name, thumb_edge)
        copied_raw += 0 if ok else 1
        made += 1
    # 母款参考图（可选）：把第一张示例同时作为 refs
    refs = style.get("refs") or []
    if refs:
        first_example = next(iter((style.get("example_sources") or {}).items()), None)
        if first_example:
            src = Path(first_example[1]).expanduser()
            if src.is_file():
                make_thumb(src, dst / refs[0], thumb_edge)

    print(f"发布完成：{dst}（示例 {made} 张" + (f"，缺 {missing}" if missing else "") +
          (f"，{copied_raw} 张未压缩（没装 Pillow）" if copied_raw else "") + "）")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="发布风格卡并刷新选择器页面")
    ap.add_argument("--pack", required=True, help="卡目录（含 style.json）")
    ap.add_argument("--root", required=True, help="风格库目录")
    ap.add_argument("--thumb-edge", type=int, default=700)
    ap.add_argument("--no-gallery", action="store_true", help="只发布，不刷新页面")
    a = ap.parse_args()

    root = Path(a.root).expanduser()
    publish(Path(a.pack).expanduser(), root, a.thumb_edge)
    if not a.no_gallery:
        import subprocess
        import sys
        gallery = Path(__file__).with_name("style_gallery.py")
        subprocess.run([sys.executable, str(gallery), "--root", str(root)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
