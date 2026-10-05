"""端到端冒烟测试：在本地临时工作目录里造一张假母款，跑完整条流水线。

  python pkg/tests/smoke.py            # 全绿退出 0，有失败退出 1
  python pkg/tests/smoke.py --keep     # 跑完保留工作目录，方便看产物

用 replay 假后端（**不花钱**），只验证「流程能不能跑通、产物在不在、口径对不对」：
  doctor → ingest → analyze → dna → plan → generate → qc → export → handoff
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parent
RUN = PKG / "run.py"
TESTS = [0, 0]


def sh(home: Path, *cmd: str, expect: int = 0) -> subprocess.CompletedProcess:
    env = {**os.environ, "IMGCOPY_HOME": str(home), "PYTHONUTF8": "1"}
    p = subprocess.run([sys.executable, str(RUN), *cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    mark = "OK  " if p.returncode == expect else "FAIL"
    TESTS[0] += 1
    TESTS[1] += p.returncode != expect
    print(f"[{mark}] imgcopy {' '.join(cmd)}")
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != expect or os.environ.get("SMOKE_VERBOSE"):
        print("       " + out.replace("\n", "\n       "))
    return p


def make_fixture(home: Path) -> None:
    """造一张假母款图案（波点 + 一个"主体"块）和一份母款名单。"""
    import cv2
    import numpy as np

    pat = home / "patterns"
    pat.mkdir(parents=True, exist_ok=True)
    w, h = 1252, 2232
    img = np.full((h, w, 3), 255, np.uint8)
    for x in range(0, w, 90):
        img[:, x:x + 22] = (247, 206, 214)
    ys, xs = np.mgrid[0:h, 0:w]
    dots = ((xs % 120 - 60) ** 2 + (ys % 120 - 60) ** 2) < 20 ** 2
    img[dots] = (233, 138, 160)
    cv2.circle(img, (w // 2, int(h * 0.42)), 320, (120, 90, 200), -1)
    cv2.circle(img, (w // 2, int(h * 0.42)), 320, (60, 40, 120), 12)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    # 中文文件名必须用 write_bytes：cv2.imwrite 在中文路径上会静默失败
    (pat / "M1005波点.png").write_bytes(buf.tobytes())

    # 这台机器上跑测试就别打扰模型：语义标签走 none（只算 CV 事实）
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({
        "vision": {"type": "none"},
        "generator": {"type": "replay"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg / "geometry.json").write_text(
        json.dumps({"calibrated": True}, ensure_ascii=False), encoding="utf-8")

    inp = home / "data" / "inputs"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "parent_input.json").write_text(json.dumps({"parents": [{
        "design_name": "M1005波点", "item_id": "1064283891755", "source": "smoke",
        "data_trust": "trusted", "grade": "B", "theme": "波点",
    }]}, ensure_ascii=False, indent=2), encoding="utf-8")


def check(cond: bool, what: str) -> None:
    TESTS[0] += 1
    TESTS[1] += not cond
    print(f"[{'OK  ' if cond else 'FAIL'}] {what}")


def main() -> int:
    keep = "--keep" in sys.argv
    home = (Path(os.environ["IMGCOPY_SMOKE_HOME"]) if os.environ.get("IMGCOPY_SMOKE_HOME")
            else REPO / ".smoke" / "run")
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    print(f"工作目录：{home}\n")

    try:
        make_fixture(home)
        sh(home, "doctor")
        sh(home, "ingest")
        pid = next(iter(json.loads((home / "data" / "state.json").read_text(
            encoding="utf-8"))["parent"]))
        sh(home, "analyze", "--all")
        sh(home, "dna", "--all")
        sh(home, "plan", "--all", "--count", "3")
        sh(home, "generate", pid)                       # dry-run：不该出图
        dry_only = not list((home / "parents" / pid / "children").glob("*.png"))
        check(dry_only, "dry-run 不落图（默认不花钱）")
        sh(home, "generate", pid, "--go")               # replay 假后端
        kids = list((home / "parents" / pid / "children").glob("*.png"))
        check(len(kids) == 3, f"出图 3 张（实际 {len(kids)}）")
        sh(home, "qc", pid)
        sh(home, "export", pid)
        sh(home, "handoff", pid, "--drop")

        batch = sorted((home / "out" / "batches").glob(f"{pid}_*"))[-1]
        hand = batch / "handoff"
        check((hand / "handoff.json").is_file(), "交接单 handoff.json")
        check((hand / "handoff.md").is_file(), "交接单 handoff.md")
        finals = list((hand / "final").glob("*.png"))
        check(bool(finals), f"整幅成品图 {len(finals)} 张")
        if finals:
            import cv2
            import numpy as np
            im = cv2.imdecode(np.fromfile(str(finals[0]), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            check(im.shape[:2] == (3744, 2108), f"整幅成品图尺寸 {im.shape[1]}×{im.shape[0]}")
            check(int(im[:, :, :3].min()) < 250, "整幅成品图四周没有留白（镜像补边生效）")
        prints = list((hand / "print").rglob("*_印刷.png"))
        check(bool(prints), f"印刷版（透明底 + 摄像头镂空）{len(prints)} 张")
        if prints:
            import cv2
            import numpy as np
            p = cv2.imdecode(np.fromfile(str(prints[0]), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            check(p.ndim == 3 and p.shape[2] == 4 and int(p[:, :, 3].min()) == 0,
                  "印刷版有 alpha 透明区（挖孔是透明而不是黑边）")
        check(bool(list((home / "print").rglob("*.png"))), "交付目录里有印刷三件套")
        check(bool(list((home / "patterns").rglob(f"*{pid[0]}*"))),
              "图案库收到整幅成品图（命名不带 _整幅版 后缀）")
    finally:
        if not keep:
            shutil.rmtree(home, ignore_errors=True)
        else:
            print(f"\n保留工作目录：{home}")

    print(f"\n合计 {TESTS[0]} 项，失败 {TESTS[1]} 项")
    return 1 if TESTS[1] else 0


if __name__ == "__main__":
    raise SystemExit(main())
