"""ImageGenerator 接口 + 工厂。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .. import context as ctx


@dataclass
class GenRequest:
    child_id: str
    prompt: str
    images: list[dict]                 # [{"path": ..., "role": ...}]，第 0 张是母款
    out_path: Path
    params: dict = field(default_factory=dict)
    cache_key: str = ""
    intent: str = ""
    force: bool = False          # True = 忽略本地已完成的任务，重新出图


@dataclass
class GenResult:
    path: str
    model: str
    job: str = ""
    elapsed: float | None = None
    cached: bool = False
    cost: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class ImageGenerator(Protocol):
    name: str

    def available(self) -> bool: ...

    def describe(self) -> list[tuple[str, str]]: ...

    def generate(self, req: GenRequest) -> GenResult: ...


def build(kind: str | None = None) -> ImageGenerator:
    kind = (kind or ctx.get("generator.type", "replay")).strip().lower()
    if kind == "o1key":
        from .o1key import O1KeyGenerator
        return O1KeyGenerator()
    if kind == "replay":
        from .replay import ReplayGenerator
        return ReplayGenerator()
    if kind == "comfyui":
        from .comfyui import ComfyUIGenerator
        return ComfyUIGenerator()
    raise ValueError(f"未知出图后端 type={kind}（可选 o1key / replay / comfyui）")
