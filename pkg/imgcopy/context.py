"""可移植版配置/路径/工具层（替代本机耦合的 context.py）。

与原版的差别只有两处：
  1. 配置随包走（pkg/imgcopy/config），不指向任何本机绝对路径；
  2. 工作目录由环境变量 `IMGCOPY_HOME` 决定（默认当前目录），parents/out/data 都在它下面。

这样同一份代码换机器、换目录都能跑；本机专属的路径（图案库、印刷目录、付费出图通道）
全部写在 config/settings.json 里由使用者改。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
CONFIG_DIR = PKG_DIR / "config"
SCHEMA_DIR = PKG_DIR / "schemas"
PROMPT_DIR = CONFIG_DIR / "prompts"

WORK = Path(os.environ.get("IMGCOPY_HOME") or Path.cwd()).resolve()
# 原版里 ROOT 指项目根目录（out/ data/ parents/ 都在它下面）；可移植版把它对齐到工作目录，
# 这样 parents/out/data 里的文件都能 relative_to(ctx.ROOT)。随包资源走 PKG_DIR / CONFIG_DIR。
ROOT = WORK
CN = timezone(timedelta(hours=8))

_cache: dict[str, dict] = {}


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8()


# ---------------------------------------------------------------- 配置

def _deep_merge(base, over):
    """把工作目录里的配置叠在随包默认值上：只覆盖同名项，其余保持默认。"""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(base.get(k), v) if k in base else v
    return out


def _load(name: str) -> dict:
    if name not in _cache:
        base_path = CONFIG_DIR / name
        if not base_path.is_file():
            raise FileNotFoundError(f"配置缺失：{base_path}")
        with open(base_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # 使用者可以把同名配置放到 <IMGCOPY_HOME>/config/ 里覆盖随包默认值（同名项生效）
        alt = WORK / "config" / name
        if alt.is_file():
            with open(alt, "r", encoding="utf-8") as fh:
                data = _deep_merge(data, json.load(fh))
        _cache[name] = data
    return _cache[name]


def settings() -> dict:
    return _load("settings.json")


def geometry() -> dict:
    return _load("geometry.json")


def taxonomy() -> dict:
    return _load("taxonomy.json")


def operators() -> dict:
    return _load("operators.json")


def matrix() -> dict:
    return _load("matrix.json")


def thresholds() -> dict:
    return _load("thresholds.json")


def naming() -> dict:
    return _load("naming.json")


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def prompt_template(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def get(path: str, default=None):
    """点号取配置：get("budget.max_images_per_run", 10)。"""
    node = settings()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ---------------------------------------------------------------- 路径

def resolve(path_str) -> Path:
    p = Path(str(path_str)).expanduser()
    return p if p.is_absolute() else (WORK / p)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def parents_dir() -> Path:
    return resolve(get("paths.parents_dir", "parents"))


def out_dir() -> Path:
    return resolve(get("paths.out_dir", "out"))


def data_dir() -> Path:
    return resolve(get("paths.data_dir", "data"))


def parent_dir(parent_id: str) -> Path:
    return parents_dir() / parent_id


# ---------------------------------------------------------------- 读写 JSON

def read_json(path, default=None):
    p = resolve(path)
    if not p.is_file():
        return default
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj, indent: int = 2) -> Path:
    p = resolve(path)
    ensure_dir(p.parent)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=indent)
            fh.write("\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p


def append_jsonl(path, obj) -> None:
    p = resolve(path)
    ensure_dir(p.parent)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 哈希 / 时间

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(resolve(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(CN).replace(microsecond=0).isoformat()


def today_str() -> str:
    return datetime.now(CN).strftime("%Y%m%d")


# ---------------------------------------------------------------- 输出

def info(*args) -> None:
    print(*args, flush=True)


def warn(*args) -> None:
    print("! ", *args, flush=True)


def error(*args) -> None:
    print("x ", *args, file=sys.stderr, flush=True)


def hr(title: str = "") -> None:
    print(("\n== " + title + " " + "=" * max(0, 60 - len(title))) if title else "-" * 64,
          flush=True)
