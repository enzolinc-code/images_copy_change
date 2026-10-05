"""语义标签与质检判断的通道适配器。

本机现状：没有现成的无人值守视觉 API。所以默认走 `agent`：
程序把「小预览图 + 词表 + 提示词 + 期望的输出文件名」写成一个请求文件，
由 Codex（或人）读图回答，答案文件落盘后程序继续。

好处：判断质量高、不额外花钱、结果留档可复查；
代价：需要一个"人/agent 在场"的动作。要无人值守时把 vision.type 换成
gemini-web（本机有反解通道，需要先登录 Google）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import context as ctx
from .. import validate
from . import cv


class VisionAwaiting(RuntimeError):
    """请求已经写好，等答案文件。"""

    def __init__(self, request_path: Path, answer_path: Path, message: str):
        super().__init__(message)
        self.request_path = request_path
        self.answer_path = answer_path


def _cache_path(task: str, image_hash: str) -> Path:
    return ctx.ensure_dir(ctx.data_dir() / "cache" / "vision") / f"{task}_{image_hash[:32]}.json"


def _question(task: str) -> tuple[str, str]:
    if task == "labels":
        return "analyze.md", "标签"
    raise ValueError(f"未知的视觉任务：{task}")


def request_labels(parent_dir: Path, parent: dict, facts: dict) -> tuple[Path, Path]:
    """写请求文件：预览图 + 提示词 + 词表 + 已知事实。"""
    image = parent_dir / "parent.png"
    preview = cv.make_preview(image, parent_dir / "preview_parent.jpg")
    template = ctx.prompt_template("analyze.md")
    tax = ctx.taxonomy()
    req = {
        "task": "labels",
        "how_to_answer": {
            "说明": "把 JSON 答案写进 answer_path（只写 JSON 对象，不要代码块、不要解释）。",
            "answer_path": str((parent_dir / "vision_answer_labels.json").resolve()),
            "image_to_look_at": str(preview.resolve()),
            "prompt": str((ctx.CONFIG_DIR / "prompts" / "analyze.md").resolve()),
        },
        "instructions": template,
        "vocabulary": {
            "subject": tax["subject"],
            "composition": tax["composition"],
            "style_buckets": list(tax["style"].keys()),
            "style_examples": tax["style"],
            "emotion": tax["emotion"],
            "hook_examples": tax.get("hook_examples", []),
        },
        "facts": {k: v for k, v in facts.items() if not k.startswith("_")},
        "parent": {
            "parent_id": parent["parent_id"],
            "design_name": parent["design_name"],
            "theme_word": parent.get("theme_word"),
            "artwork_wh": parent.get("artwork_wh"),
        },
    }
    req_path = ctx.write_json(parent_dir / "vision_request_labels.json", req)
    return req_path, parent_dir / "vision_answer_labels.json"


def read_labels(parent_dir: Path, parent: dict, image_hash: str) -> dict | None:
    """读答案（优先缓存）。没有答案就返回 None。"""
    cached = _cache_path("labels", image_hash)
    if ctx.get("vision.cache", True) and cached.is_file():
        return ctx.read_json(cached)

    answer = parent_dir / "vision_answer_labels.json"
    if not answer.is_file():
        return None
    obj = ctx.read_json(answer)
    if not isinstance(obj, dict):
        raise ValueError(f"{answer} 不是 JSON 对象")
    obj = _normalize_labels(obj)
    errors = validate.check({"subject": obj.get("subject"), "composition": obj.get("composition"),
                             "style": obj.get("style"), "emotion": obj.get("emotion"),
                             "visual_hooks": obj.get("visual_hooks")},
                            {
                                "type": "object",
                                "properties": {
                                    "subject": {"type": "object", "required": ["primary", "ip_like"]},
                                    "composition": {"type": "array", "items": {"type": "string"}},
                                    "style": {"type": "object"},
                                    "emotion": {"type": "object"},
                                    "visual_hooks": {"type": "array"},
                                },
                            },
                            where="labels")
    if errors:
        raise ValueError("标签格式不对：\n  - " + "\n  - ".join(errors))
    tagged = dict(obj)
    tagged["_source"] = "agent"
    ctx.write_json(cached, tagged)
    return tagged


def _normalize_labels(obj: dict) -> dict:
    """把模型爱犯的小毛病掰正：数字给成字符串、hook 列表给成单对象、缺字段。"""
    if isinstance(obj.get("visual_hooks"), dict):
        obj["visual_hooks"] = [obj["visual_hooks"]]
    for key in ("style", "emotion"):
        node = obj.get(key)
        if isinstance(node, list):
            obj[key] = {str(k): 0.6 for k in node}
        elif isinstance(node, dict):
            fixed = {}
            for k, v in node.items():
                try:
                    fixed[str(k)] = float(v)
                except (TypeError, ValueError):
                    fixed[str(k)] = 0.5
            obj[key] = fixed
    subj = obj.get("subject")
    if isinstance(subj, str):
        obj["subject"] = {"primary": subj, "detail": "", "ip_like": False, "ip_note": ""}
    elif isinstance(subj, dict):
        subj.setdefault("primary", "unknown")
        subj.setdefault("detail", "")
        subj["ip_like"] = bool(subj.get("ip_like", False))
        subj.setdefault("ip_note", "")
    else:
        obj["subject"] = {"primary": "unknown", "detail": "", "ip_like": False, "ip_note": ""}
    obj.setdefault("composition", [])
    obj.setdefault("style_detail", [])
    obj.setdefault("visual_hooks", [])
    obj.setdefault("notes", "")
    obj.setdefault("text", {"present": False, "content": "", "role": "none"})
    return obj


def _run_gemini(prompt: str, image: Path, timeout: int | None = None) -> str:
    cfg = ctx.get("vision.gemini_web", {}) or {}
    bun = cfg.get("bun", "bun")
    script = cfg.get("script")
    cmd = [bun, script, "--prompt", prompt, "--reference", str(image),
           "--model", cfg.get("model", "gemini-3-pro")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=int(timeout or cfg.get("timeout", 300)))
    if proc.returncode != 0:
        raise RuntimeError(f"gemini-web 调用失败：{(proc.stderr or '')[-400:]}")
    return proc.stdout or ""


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"模型没返回 JSON：{text[:300]}")
    return json.loads(text[start:end + 1])
