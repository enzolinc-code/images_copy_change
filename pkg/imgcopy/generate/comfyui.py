"""ComfyUI 后端占位（Phase 1 不启用）。

要启用时照 pattern-extract/generators.py 的 ComfyUIGenerator 接：
本机便携版在 D:\\o1key_windows_portable，起服务后走 HTTP。
这里先明确报错，避免"以为在用免费通道、其实没配好"。
"""

from __future__ import annotations

from .. import context as ctx
from .base import GenRequest, GenResult


class ComfyUIGenerator:
    name = "comfyui"

    def __init__(self) -> None:
        self.root = ctx.get("generator.comfyui.root")

    def available(self) -> bool:
        return False

    def describe(self) -> list[tuple[str, str]]:
        return [("绘图后端", "comfyui（Phase 1 未启用）"), ("根目录", str(self.root))]

    def generate(self, req: GenRequest) -> GenResult:
        raise NotImplementedError(
            "ComfyUI 通道 Phase 1 未启用。要用免费通道，照 pattern-extract/generators.py "
            "里的 ComfyUIGenerator 接进来（本机 ComfyUI 在 D:\\o1key_windows_portable）。"
        )
