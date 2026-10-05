"""不花钱的假后端。

存在的意义：整条流水线（计划→prompt→出图→质检→去重→导出）可以在 0 成本下跑通与回归。
有 fixtures/<cache_key>.png 就复制那张；没有就按母款生成一张带编号的占位图。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from .. import context as ctx
from ..analyze import cv
from .base import GenRequest, GenResult


class ReplayGenerator:
    name = "replay"

    def __init__(self) -> None:
        self.fixtures = ctx.resolve(ctx.get("generator.replay.dir", "tests/fixtures/replay"))

    def available(self) -> bool:
        return True

    def describe(self) -> list[tuple[str, str]]:
        return [("绘图后端", "replay（假后端：不花钱，用于测试）"),
                ("素材目录", str(self.fixtures))]

    def generate(self, req: GenRequest) -> GenResult:
        ctx.ensure_dir(Path(req.out_path).parent)
        preset = self.fixtures / f"{req.cache_key}.png"
        if req.cache_key and preset.is_file():
            shutil.copyfile(preset, req.out_path)
            return GenResult(path=str(req.out_path), model=self.name, job="fixture",
                             cost={"usd": 0.0, "images": 1}, raw={"fixture": str(preset)})

        if req.images:
            rgb = cv.load_image(req.images[0]["path"])
        else:
            wh = (int(ctx.get("canvas.width", 1252)), int(ctx.get("canvas.height", 2232)))
            rgb = np.full((wh[1], wh[0], 3), 245, dtype=np.uint8)
        h, w = rgb.shape[:2]
        band = max(12, h // 40)
        rgb[band:band * 2, :] = np.array([40, 40, 40], dtype=np.uint8)
        cv.save_image(req.out_path, rgb)
        import cv2
        img = cv.load_image(req.out_path)
        cv2.putText(img, req.child_id.encode("ascii", "ignore").decode()[:34],
                    (16, band * 3), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3,
                    cv2.LINE_AA)
        cv.save_image(req.out_path, img)
        return GenResult(path=str(req.out_path), model=self.name, job="placeholder",
                         cost={"usd": 0.0, "images": 0}, raw={"generated": True})
