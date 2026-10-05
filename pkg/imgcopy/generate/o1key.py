"""O1Key（GPT Image 2.5）后端 —— 本机已验证可用的付费通道。

调用契约（与 pattern-extract/generators.py 一致，不要另发明一套）：
  1. 写 request.json：{prompt, images:[{path, role}], primary_image, resolution, aspect_ratio, ...}
  2. `o1key_image.py prepare --request req.json` → {status: AWAITING_CONFIRMATION, job, confirmation_id}
  3. `o1key_image.py run --job <job> --confirm <id>`   → {status: SUCCESS, files:[{path}], ...}
  4. 失败（锁没清、上传断）→ `unlock --job` + `resume --job`，同一个任务接着跑，不会重复付费。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .. import context as ctx
from .base import GenRequest, GenResult


class O1KeyGenerator:
    name = "o1key"
    # 技能会在"数量和尺寸没完全对上"时返回 PARTIAL_SUCCESS（图已经出好、已下载），
    # 那不是失败：pattern-extract 那边也是按"有 files 就算成"处理。
    OK_STATUSES = ("SUCCESS", "PARTIAL_SUCCESS")

    def __init__(self) -> None:
        g = ctx.get("generator.o1key", {}) or {}
        self.python = g.get("python") or ctx.get("runtime.python")
        self.script = g.get("script")
        self.job_dir = ctx.resolve(g.get("job_dir", "out/_jobs/o1key"))
        self.size = g.get("size", "2K")
        self.ar = g.get("ar", "9:16")
        self.quality = g.get("quality", "auto")
        self.timeout = int(g.get("timeout", 900))
        self.n = int(g.get("n", 1))
        self.background = g.get("background", "auto")
        self.output_format = g.get("output_format", "png")
        self.last: dict = {}

    # ---- 与技能脚本对话
    def _call(self, args: list[str], timeout: int = 120):
        if not self.script or not Path(self.script).is_file():
            raise RuntimeError(f"找不到 O1Key 技能脚本：{self.script}")
        cmd = [self.python, "-B", str(self.script)] + args
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        payload = None
        out = (proc.stdout or "").strip()
        start, end = out.find("{"), out.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(out[start:end + 1])
            except json.JSONDecodeError:
                payload = None
        return payload, proc

    @staticmethod
    def _fix_extension(path: str, out_dir: Path) -> str:
        """扩展名与实际格式不一致时，复制一份正确后缀的（上游 pattern-extract 同款处理）。

        实测：源图是 jpg 却叫 .png 时，技能脚本会在提交前直接拒收。
        """
        import shutil
        p = Path(path)
        with open(p, "rb") as fh:
            head = fh.read(16)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            real = ".png"
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            real = ".webp"
        elif head[:2] == b"\xff\xd8":
            real = ".jpg"
        else:
            return path
        if p.suffix.lower() == real:
            return path
        fixed = Path(out_dir) / (p.stem + "_src" + real)
        if not fixed.is_file():
            shutil.copyfile(p, fixed)
        return str(fixed)

    def available(self) -> bool:
        try:
            payload, _ = self._call(["doctor"], timeout=90)
        except Exception:  # noqa: BLE001
            return False
        return bool(payload and payload.get("api_key_configured"))

    def describe(self) -> list[tuple[str, str]]:
        return [("绘图后端", "o1key（GPT Image 2.5，云端付费）"),
                ("技能脚本", str(self.script)),
                ("任务目录", str(self.job_dir)),
                ("分辨率 / 画幅", f"{self.size} / {self.ar}"),
                ("单张超时", f"{self.timeout} 秒")]

    # ---- 出图
    def _find_done_job(self, child_dir: Path) -> Path | None:
        """同一个子款之前已经出好的任务：直接复用，不重复付费。"""
        if not child_dir.is_dir():
            return None
        for p in sorted(child_dir.glob("*/job.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if data.get("status") in self.OK_STATUSES and data.get("files"):
                first = data["files"][0].get("path")
                if first and Path(first).is_file():
                    return p
        return None

    def generate(self, req: GenRequest) -> GenResult:
        ctx.ensure_dir(self.job_dir)
        out_dir = Path(req.out_path).parent
        ctx.ensure_dir(out_dir)
        job_name = f"{req.child_id}"
        job_path = ctx.ensure_dir(self.job_dir / job_name)

        done = None if req.force else self._find_done_job(job_path)
        if done is not None:
            data = json.loads(done.read_text(encoding="utf-8"))
            src = data["files"][0]["path"]
            target = Path(req.out_path)
            if Path(src).resolve() != target.resolve():
                import shutil
                shutil.copyfile(src, target)
            ctx.info(f"  （复用已完成的任务，没再花钱：{done.parent.name}）")
            return GenResult(path=str(target), model="o1key/gpt-image-2.5",
                             job=str(done), elapsed=None, cached=True,
                             cost={"usd": None, "images": 0},
                             raw={"reused_job": str(done),
                                  "warnings": data.get("result_warnings")})

        images = [{"path": str(Path(im["path"]).resolve()).replace("\\", "/"),
                   "role": im.get("role", "")} for im in req.images]
        images = [{"path": self._fix_extension(im["path"], job_path).replace("\\", "/"),
                   "role": im["role"]} for im in images]
        request = {
            "prompt": req.prompt,
            "intent": req.intent or "手机壳图案的受控变异",
            "primary_image": 1,
            "images": images,
            "quality": self.quality,
            "n": self.n,
            "background": self.background,
            "output_format": self.output_format,
            "output_dir": str(job_path).replace("\\", "/"),
            "resolution": self.size,
            "aspect_ratio": self.ar,
        }
        req_path = job_path / "_request.json"
        with open(req_path, "w", encoding="utf-8") as fh:
            json.dump(request, fh, ensure_ascii=False, indent=2)

        payload, proc = self._call(["prepare", "--request", str(req_path)], timeout=180)
        if not payload or payload.get("status") != "AWAITING_CONFIRMATION":
            err = (payload or {}).get("error") or {}
            why = err.get("message") or (proc.stderr or "")[-500:] or \
                  f"prepare 没有返回确认单（原始输出：{(proc.stdout or '')[:300]}）"
            raise RuntimeError(f"O1Key 提交前检查失败：{why}")
        job = payload["job"]
        confirm = payload.get("confirmation_id")

        payload, proc = self._call(["run", "--job", job, "--confirm", confirm],
                                  timeout=self.timeout + 300)
        if not payload or payload.get("status") not in self.OK_STATUSES:
            try:
                self._call(["unlock", "--job", job], timeout=60)
            except Exception:  # noqa: BLE001
                pass
            payload, proc = self._call(["resume", "--job", job], timeout=self.timeout + 300)
        if not payload or payload.get("status") not in self.OK_STATUSES:
            err = (payload or {}).get("error") or {}
            why = err.get("message") or (proc.stderr or "")[-500:] or "没有返回可用结果"
            raise RuntimeError(f"O1Key 没出图：{why}")

        files = payload.get("files") or []
        if not files:
            raise RuntimeError("O1Key 说成功但没给图片")
        src = files[0]["path"]
        # 图落到批次目录（不覆盖任务目录里的原始输出）
        target = Path(req.out_path)
        if Path(src).resolve() != target.resolve():
            import shutil
            shutil.copyfile(src, target)

        self.last = {"job": job, "task_id": payload.get("task_id"),
                     "elapsed_seconds": (payload.get("timings") or {}).get("elapsed_seconds")}
        return GenResult(path=str(target), model="o1key/gpt-image-2.5", job=job,
                         elapsed=self.last.get("elapsed_seconds"),
                         cost={"usd": None, "images": 1},
                         raw={"task_id": payload.get("task_id"), "files": files,
                              "warnings": payload.get("warnings")})
