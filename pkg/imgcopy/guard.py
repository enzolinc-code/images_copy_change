"""预算闸 + 幂等键 + dry-run。

三道闸（需求第 16 条 + 系统设计.md R7）：
  ① 单次张数上限           settings.budget.max_images_per_run
  ② 超上限要人工确认        settings.budget.require_confirm_above
  ③ 按 prompt_hash+参考图+模型 缓存，命中就不重新花钱
"""

from __future__ import annotations

from . import context as ctx
from . import db


class BudgetError(RuntimeError):
    pass


def cache_key(prompt_hash: str, reference_hash: str, model: str, params: dict | None = None) -> str:
    extra = ""
    if params:
        extra = "|".join(f"{k}={params[k]}" for k in sorted(params) if k in ("size", "ar", "quality", "n"))
    return ctx.sha256_text(f"{prompt_hash}|{reference_hash}|{model}|{extra}")


def check_budget(n_images: int, go: bool = False) -> None:
    limit = int(ctx.get("budget.max_images_per_run", 10))
    need_confirm = int(ctx.get("budget.require_confirm_above", limit))
    if n_images > limit:
        raise BudgetError(
            f"本次要生成 {n_images} 张，超过单次上限 {limit} 张。"
            f"改 settings.json 的 budget.max_images_per_run，或分批跑。"
        )
    if n_images >= need_confirm and not go:
        raise BudgetError(
            f"本次要生成 {n_images} 张（≥ 确认线 {need_confirm}）。"
            f"确认要花钱就跑 `--go`；只看看会生成什么，跑 `--dry-run`。"
        )


def dry_run_default() -> bool:
    return bool(ctx.get("budget.dry_run_default", True))


def cached_image(key: str) -> str | None:
    if not ctx.get("budget.cache_by_prompt_hash", True):
        return None
    hit = db.cache_get(key)
    if not hit:
        return None
    path = ctx.resolve(hit["image_path"])
    if not path.is_file():
        ctx.warn(f"缓存记录存在但文件没了：{path}")
        return None
    return str(path)


def remember(key: str, image_path: str, job: str = "", cost: dict | None = None) -> None:
    db.cache_put(key, str(image_path), job, cost)
