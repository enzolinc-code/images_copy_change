"""不安装也能跑的入口：把 pkg/ 加到 sys.path 后转给 imgcopy.cli。

  python pkg/run.py doctor
  python pkg/run.py plan P_20261003_0001 --count 3 --operators color

工作目录：环境变量 IMGCOPY_HOME（默认当前目录）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from imgcopy.cli import main  # noqa: E402

if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
