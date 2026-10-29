"""控制台编码兜底（Windows 管道/重定向下强制 UTF-8）。"""

from __future__ import annotations

import sys

_UTF8 = {"utf8", "utf8mb4", "cp65001", "utf_8"}


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc in _UTF8:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
