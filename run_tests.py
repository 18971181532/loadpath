#!/usr/bin/env python3
"""运行 LoadPath 全部单元测试，输出汇总。

用法::

    python run_tests.py              # 全部
    python run_tests.py -v           # 详细
    python run_tests.py TestSolver   # 只跑某个用例类
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def main(argv: list) -> int:
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))

    from loadpath.console import force_utf8

    force_utf8()

    verbosity = 2 if ("-v" in argv or "--verbose" in argv) else 1
    loader = unittest.TestLoader()
    names = [a for a in argv if not a.startswith("-")]
    if names:
        suite = unittest.TestSuite(
            loader.loadTestsFromName(f"test_loadpath.{n}") for n in names
        )
    else:
        suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py")

    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print()
    print("─" * 60)
    print(f"共 {total} 项：通过 {total - bad}，失败 {len(result.failures)}，错误 {len(result.errors)}")
    print("─" * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
