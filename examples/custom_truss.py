#!/usr/bin/env python3
"""示例：从零搭一个悬挑雨棚桁架，分析它、优化它，再看它有多"结实"。

全部 API 都在 ``loadpath`` 包里，不需要任何第三方库::

    python examples/custom_truss.py

流程：
  1. add_node / add_member / add_support / add_case 手工建模
  2. 静力分析 + 校核（会看到初版严重超设计）
  3. 只做截面选型：重量降下来，冗余度还在
  4. 选型 + 拓扑精简：更轻一点，但冗余度归零 —— 这笔账要算清楚
  5. 单杆失效扫描：哪根杆拆了以后结构会变成机构
  6. 导出 JSON（可直接 POST 回 /api/custom，或在网页里载入）

单位：长度 mm、力 N、应力 MPa、质量 kg。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from loadpath import analyse, get_material  # noqa: E402
from loadpath.check import check  # noqa: E402
from loadpath.console import force_utf8  # noqa: E402
from loadpath.model import Structure, chs  # noqa: E402
from loadpath.optimize import optimize, redundancy_scan  # noqa: E402

force_utf8()

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

SPAN = 5600.0        # 悬挑长度 mm
DEPTH = 1600.0       # 根部桁高 mm
TIP_DEPTH = 800.0    # 端部桁高 mm
PANELS = 4           # 节间数
TIP_LOAD = 9000.0    # 自由端检修荷载 N
SNOW_LOAD = 18000.0  # 上弦节点雪荷载 N（约 3 m 挂宽 × 3.0 kN/m²）


def build_canopy() -> Structure:
    """5.6 m 悬挑雨棚：根部两节点铰接，前端逐渐收窄，斜腹杆呈 W 形。

    根部节间再加一根反向斜杆（加强斜杆）——施工时常见的做法，
    也是这个桁架最初的冗余来源。
    """
    st = Structure("canopy", "5.6 m 悬挑雨棚桁架，根部双铰，前端收窄")
    steel = get_material("q235")
    sec = chs(139.7, 5.0)          # 初版一律用这一种，偏保守

    xs = [SPAN * k / PANELS for k in range(PANELS + 1)]
    top_y = [DEPTH + (TIP_DEPTH - DEPTH) * k / PANELS for k in range(PANELS + 1)]

    top = [st.add_node(x, y, f"T{i}") for i, (x, y) in enumerate(zip(xs, top_y))]
    bottom = [st.add_node(x, 0.0, f"B{i}") for i, x in enumerate(xs)]

    for i in range(PANELS):
        st.add_member(bottom[i], bottom[i + 1], sec, steel, group="下弦")
        st.add_member(top[i], top[i + 1], sec, steel, group="上弦")
        if i % 2 == 0:                                  # 斜腹杆交替，形成 W 形
            st.add_member(bottom[i], top[i + 1], sec, steel, group="斜腹杆")
        else:
            st.add_member(top[i], bottom[i + 1], sec, steel, group="斜腹杆")
    for i in range(PANELS + 1):
        st.add_member(bottom[i], top[i], sec, steel, group="竖杆")
    st.add_member(top[0], bottom[1], sec, steel, group="加强斜杆")

    st.add_support(bottom[0], True, True)
    st.add_support(top[0], True, True)

    tip = st.add_case("端部检修", "自由端下弦 9 kN 集中力")
    tip.add(bottom[-1], 0.0, -TIP_LOAD)
    snow = st.add_case("积雪", "上弦节点各 18 kN")
    for n in top[1:]:
        snow.add(n, 0.0, -SNOW_LOAD)
    return st


def show(tag: str, st: Structure) -> None:
    """打一行摘要：质量 / 利用率 / 挠度 / 截面种类 / 结论。"""
    rep = check(st, analyse(st))
    labels = Counter(m.section.label for m in st.members)
    print(f"  {tag:<10} {st.total_mass():6.1f} kg   "
          f"利用率 {rep.max_ratio:5.2f}   挠度 {rep.max_disp:6.2f} mm   "
          f"{len(labels)} 种截面   {rep.verdict}")
    for label, cnt in sorted(labels.items(), key=lambda kv: -kv[1]):
        print(f"  {'':<10} · {label:<14} × {cnt}")


def main() -> int:
    print("=" * 74)
    print("  LoadPath 示例：从零设计一个悬挑雨棚")
    print("=" * 74)

    # ---------------------------------------------------------------- 建模
    st = build_canopy()
    print(f"\n[1] 建模：{len(st.nodes)} 节点 / {len(st.members)} 杆件 / "
          f"{len(st.cases)} 个工况 / 悬挑 {st.span() / 1000:.2f} m / "
          f"桁高 {DEPTH:.0f}→{TIP_DEPTH:.0f}")
    show("初版", st)
    print(f"  {'':<10} 挠度限值 span/250 = {SPAN / 250:.1f} mm")

    # ------------------------------------------------------ 只选型，不精简
    print("\n[2] 截面选型（保留全部杆件）")
    lean = optimize(st, prune_threshold=0.0)
    show("选型后", lean.structure)
    scan_lean = redundancy_scan(lean.structure)
    print(f"  {'':<10} {lean.before['mass']:.0f} → {lean.after['mass']:.0f} kg，"
          f"减重 {lean.mass_saving * 100:.0f}%，"
          f"冗余度 {scan_lean.degree * 100:.0f}%，"
          f"优化耗时 {lean.elapsed * 1000:.0f} ms")

    # ------------------------------------------------- 选型 + 拓扑精简
    print("\n[3] 截面选型 + 拓扑精简（阈值 0.15）")
    tight = optimize(st, prune_threshold=0.15)
    show("精简后", tight.structure)
    scan_tight = redundancy_scan(tight.structure)
    saving = lean.after["mass"] - tight.after["mass"]
    print(f"  {'':<10} 剔除 {len(tight.removed)} 根杆件，只再多省 {saving:.1f} kg"
          f"（{saving / lean.after['mass'] * 100:.0f}%），"
          f"冗余度却从 {scan_lean.degree * 100:.0f}% 掉到 {scan_tight.degree * 100:.0f}%")
    print(f"  {'':<10} 悬挑结构的冗余度是拿重量换的：脚下是通道，就别省这 "
          f"{saving:.0f} kg。")

    # -------------------------------------------------------- 重要度扫描
    print("\n[4] 单杆失效扫描（对 [2] 的保守方案）")
    counts = Counter(m.level for m in scan_lean.members)
    order = ["致命", "关键", "重要", "一般", "冗余"]
    print("  " + "   ".join(f"{k} {counts.get(k, 0)}" for k in order)
          + f"    共 {len(scan_lean.members)} 根，"
            f"扫描耗时 {scan_lean.elapsed * 1000:.0f} ms")
    print("  最危险的几根：")
    for m in scan_lean.worst(4):
        if not m.stable:
            note = "拆除即成为机构"
        else:
            note = f"拆后利用率 {m.ratio_after:.2f}（×{m.severity:.2f}）"
        print(f"    · 第 {m.member:>2} 根  {m.section:<14} {m.level:<3} {note}")
    safe = [m for m in scan_lean.members if m.level == "冗余"]
    if safe:
        print(f"  可以放心拆的：{', '.join('第 %d 根' % m.member for m in safe)}")

    # ------------------------------------------------------------ 导出
    print("\n[5] 导出 JSON")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "canopy.json")
    payload = tight.structure.to_dict()
    payload["meta"] = {
        "note": "由 examples/custom_truss.py 生成，可直接 POST 到 /api/custom",
        "mass_before": round(tight.before["mass"], 2),
        "mass_after": round(tight.after["mass"], 2),
        "removed": tight.removed,
        "saving": round(tight.mass_saving, 4),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"  已写入 {os.path.relpath(path, ROOT)}"
          f"（{os.path.getsize(path) / 1024:.1f} KB）")

    # --------------------------------------------------------- 三行 API
    print("\n[6] 三行 API，随时复查")
    print("  >>> from loadpath import cases, analyse")
    print("  >>> from loadpath.check import check")
    print("  >>> rep = check(cases.get('warren'), analyse(cases.get('warren')))")
    print("  >>> rep.max_ratio, rep.verdict")
    from loadpath import cases as _cases
    rep = check(_cases.get("warren"), analyse(_cases.get("warren")))
    print(f"  ({rep.max_ratio:.4f}, {rep.verdict!r})")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
