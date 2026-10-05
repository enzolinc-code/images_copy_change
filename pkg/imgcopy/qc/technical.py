"""G0：技术质检。图都打不开、尺寸不对、纯色一片，后面就不用看了。"""

from __future__ import annotations

from pathlib import Path

from .. import context as ctx
from ..analyze import cv


def check(child_path: Path | str, parent: dict) -> dict:
    reasons: list[str] = []
    detail: dict = {}
    p = ctx.resolve(child_path)
    if not p.is_file():
        return {"gate": "G0", "passed": False, "reason_codes": ["FILE_MISSING"],
                "detail": {"path": str(p)}, "score": None}

    size_kb = round(p.stat().st_size / 1024, 1)
    detail["file_kb"] = size_kb
    try:
        rgb = cv.load_image(p)
    except Exception as exc:  # noqa: BLE001
        return {"gate": "G0", "passed": False, "reason_codes": ["UNDECODABLE"],
                "detail": {"error": str(exc)}, "score": None}

    h, w = rgb.shape[:2]
    want = parent.get("artwork_wh") or [ctx.get("canvas.width"), ctx.get("canvas.height")]
    detail["wh"] = [w, h]
    if (w, h) != (int(want[0]), int(want[1])):
        reasons.append("SIZE_MISMATCH")

    std = float(rgb.std())
    detail["pixel_std"] = round(std, 2)
    if std < 2.0:
        reasons.append("FLAT_IMAGE")

    if size_kb < float(ctx.get("limits.min_child_kb", 40)):
        reasons.append("FILE_TOO_SMALL")

    return {"gate": "G0", "passed": not reasons, "reason_codes": reasons,
            "detail": detail, "score": None}
