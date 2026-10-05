"""`python -m imgcopy <命令>` 的入口。

工作目录：环境变量 IMGCOPY_HOME（默认当前目录）。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
