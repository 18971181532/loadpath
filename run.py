"""启动引导：把项目根目录加入 sys.path 后调用 CLI 主入口。"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from loadpath.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
