#!/usr/bin/env python3
"""LoadPath 演示：跑一遍引擎的关键能力，把结果打印成报告。

用法::

    python run_demos.py          # 全部
    python run_demos.py 1 4      # 只跑指定编号
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loadpath import cases  # noqa: E402
from loadpath.catalog import CATALOG  # noqa: E402
from loadpath.check import check  # noqa: E402
from loadpath.console import force_utf8  # noqa: E402
from loadpath.model import Structure, chs, get_material  # noqa: E402
from loadpath.optimize import optimize, redundancy_scan, size_members  # noqa: E402
from loadpath.solver import System, analyse  # noqa: E402

force_utf8()

BAR = "=" * 76
THIN = "-" * 76
DEMOS = []


def demo(n: int, title: str):
    def wrap(fn):
        DEMOS.append((n, title, fn))
        return fn
    return wrap


def title(text: str) -> None:
    print()
    print(BAR)
    print(f"  {text}")
    print(BAR)


# --------------------------------------------------------------------------


@demo(1, "求解器精度：两杆桁架的手算对照")
def demo_analytic() -> None:
    steel = get_material("q235")
    sec = chs(88.9, 4.0)
    st = Structure("two-bar")
    a = st.add_node(0, 0, "A")
    b = st.add_node(2000, 0, "B")
    c = st.add_node(1000, 1000, "C")
    st.add_member(a, c, sec, steel)
    st.add_member(b, c, sec, steel)
    st.add_support(a, True, True)
    st.add_support(b, True, True)
    st.add_case("P").add(c, 0, -10000)

    sol = analyse(st).solutions[0]
    n = 10000.0 / (2 * math.sin(math.pi / 4))
    length = math.hypot(1000.0, 1000.0)
    delta = n * length / (steel.e * sec.area * math.sin(math.pi / 4))

    print("  节点 C 受 10 kN 竖向荷载，两根 45° 斜杆")
    print(f"  {'量':<14}{'LoadPath':>16}{'解析解':>16}{'相对误差':>14}")
    print(THIN)
    for label, got, want in (
        ("杆件轴力 / N", sol.forces[0], -n),
        ("支座反力 Ry / N", sol.reactions[1], 5000.0),
        ("C 点竖向位移 / mm", sol.u[5], -delta),
        ("应变能 / N·mm", sol.energy,
         sum(f * f * st.length(m) / (2 * m.section.area * m.material.e)
             for f, m in zip(sol.forces, st.members))),
    ):
        err = abs(got - want) / max(abs(want), 1e-9)
        print(f"  {label:<12}{got:>18.6f}{want:>16.6f}{err:>14.1e}")

    # Betti 互等定理：换一个有 2 个自由节点的面板桁架，才有非平凡的互等
    panel = Structure("panel")
    p0 = panel.add_node(0, 0)
    p1 = panel.add_node(1000, 0)
    p2 = panel.add_node(1000, 1000)
    p3 = panel.add_node(0, 1000)
    for i, j in ((p0, p1), (p1, p2), (p2, p3), (p3, p0), (p0, p2)):
        panel.add_member(i, j, sec, steel)
    panel.add_support(p0, True, True)
    panel.add_support(p1, False, True)
    ca = panel.add_case("P").add(p2, 0, -10000)
    cb = panel.add_case("Q").add(p3, 7000, 0)
    sysm = System(panel)
    ua, ub = sysm.solve(ca).u, sysm.solve(cb).u
    fa, fb = panel.load_vector(ca), panel.load_vector(cb)
    w12 = sum(x * y for x, y in zip(fa, ub))
    w21 = sum(x * y for x, y in zip(fb, ua))
    print(THIN)
    print("  Betti 互等定理（单面板桁架，c 点竖向 vs d 点水平）：")
    print(f"    F₁·u₂₁ = {w12:.6f}   F₂·u₁₂ = {w21:.6f}   差 {abs(w12 - w21):.2e}")


@demo(2, "稀疏性：RCM 重排序把半带宽压下来")
def demo_bandwidth() -> None:
    print(f"  {'算例':<12}{'自由度':>8}{'原始带宽':>10}{'RCM 后':>9}{'分解+求解':>12}")
    print(THIN)
    for name in cases.names():
        st = cases.get(name)
        wide = System(st, reorder=False)
        narrow = System(st, reorder=True)
        t0 = time.perf_counter()
        narrow.solve_all()
        dt = (time.perf_counter() - t0) * 1000
        print(f"  {name:<12}{narrow.n:>8}{wide.bandwidth:>10}{narrow.bandwidth:>9}{dt:>10.2f} ms")
    print(THIN)
    st = cases.get("mbb")
    print(f"  说明：MBB 地面结构有 {len(st.members)} 根杆、{len(st.nodes)} 个节点，"
          f"按原编号带宽 {System(st, reorder=False).bandwidth}，RCM 后降到 "
          f"{System(st, reorder=True).bandwidth}。")
    print("  LDLᵀ 的复杂度是 O(n·b²)：带宽越小，同样的自由度就越便宜。")


@demo(3, "五个算例一览")
def demo_cases() -> None:
    print(f"  {'算例':<12}{'节点':>5}{'杆件':>5}{'质量/kg':>9}{'利用率':>8}"
          f"{'挠度/mm':>9}{'求解/ms':>9}  结论")
    print(THIN)
    for name in cases.names():
        st = cases.get(name)
        t0 = time.perf_counter()
        an = analyse(st)
        dt = (time.perf_counter() - t0) * 1000
        rep = check(st, an)
        print(f"  {name:<12}{len(st.nodes):>5}{len(st.members):>5}{st.total_mass():>9.1f}"
              f"{rep.max_ratio:>8.3f}{rep.max_disp:>9.2f}{dt:>9.2f}  {rep.verdict}")
    print(THIN)
    print("  全部算例的平衡残差都在 1e-14 量级（见 tests/test_loadpath.py）。")


@demo(4, "截面选型：离散目录上的满应力迭代")
def demo_sizing() -> None:
    st = cases.get("pratt")
    print(f"  普拉特桁架桥原始质量 {st.total_mass():.1f} kg，"
          f"{len(set(m.section.name for m in st.members))} 种截面")
    print(f"  {'轮次':>4}{'质量/kg':>10}{'最大利用率':>12}{'最大挠度/mm':>13}{'改动':>6}")
    print(THIN)
    res = size_members(st)
    for step in res.history:
        print(f"  {step.iteration:>4}{step.mass:>10.1f}{step.max_ratio:>12.3f}"
              f"{step.max_disp:>13.2f}{step.changes:>6}")
    print(THIN)
    used = sorted({m.section.label for m in res.structure.members})
    print(f"  收敛：{res.converged}，质量 {res.mass_before:.1f} → {res.mass_after:.1f} kg"
          f"（{(1 - res.mass_after / res.mass_before) * 100:.1f}%）")
    print(f"  最终用到 {len(used)} 种截面：{', '.join(used)}")
    rep = check(res.structure, analyse(res.structure))
    print(f"  校核：{rep.verdict}，最大利用率 {rep.max_ratio:.3f}，挠度 {rep.max_disp:.1f} mm")


@demo(5, "拓扑精简：MBB 地面结构")
def demo_topology() -> None:
    st = cases.get("mbb")
    res = optimize(st, prune_threshold=0.2, rounds=3)
    print(f"  初始 {len(st.members)} 根杆、{st.total_mass():.1f} kg"
          f"（地面结构全连接，绝大多数杆件几乎不受力）")
    for k, pr in enumerate(res.prunes, 1):
        print(f"  第 {k} 轮精简：剔除 {len(pr.removed)} 根，"
              f"质量 {pr.mass_before:.1f} → {pr.mass_after:.1f} kg")
    print(THIN)
    print(f"  最终 {len(res.structure.members)} 根、{res.after['mass']:.1f} kg，"
          f"减重 {res.mass_saving * 100:.1f}%")
    print(f"  最大利用率 {res.after['max_ratio']:.3f}，最大挠度 {res.after['max_disp']:.1f} mm，"
          f"用时 {res.elapsed * 1000:.0f} ms")
    rep = check(res.structure, analyse(res.structure))
    print(f"  校核：{rep.verdict}")


@demo(6, "杆件重要度：静定 vs 超静定")
def demo_redundancy() -> None:
    for name in ("warren", "tower"):
        st = cases.get(name)
        rr = redundancy_scan(st)
        det = "静定" if len(st.members) + sum(
            int(s.fix_x) + int(s.fix_y) for s in st.supports) == 2 * len(st.nodes) else "超静定"
        print(f"  {name}（{det}，{len(st.members)} 根杆）")
        print(f"    冗余度 {rr.degree * 100:.1f}%   致命 {len(rr.fatal)} 根   "
              f"关键 {len(rr.critical)} 根   用时 {rr.elapsed * 1000:.0f} ms")
        worst = rr.worst(3)
        for m in worst:
            if m.stable:
                print(f"    · #{m.member:>2} 拆除后最大利用率 {m.ratio_after:.2f}"
                      f"（×{m.severity:.2f}），等级 {m.level}")
            else:
                print(f"    · #{m.member:>2} 拆除后成为机构（致命）")
        print()

    print("  结论：静定桁架 m + r = 2n，任意一根杆失效都会变成机构——冗余度为 0。")
    print("  超静定结构才有冗余，但优化会把它吃掉：")
    st = cases.get("tower")
    before = redundancy_scan(st)
    res = optimize(st)
    after = redundancy_scan(res.structure)
    print(f"    优化前：{len(st.members)} 根 / {st.total_mass():.1f} kg / "
          f"冗余度 {before.degree * 100:.1f}%")
    print(f"    优化后：{len(res.structure.members)} 根 / {res.after['mass']:.1f} kg / "
          f"冗余度 {after.degree * 100:.1f}%")


@demo(7, "批量优化汇总")
def demo_summary() -> None:
    print(f"  {'算例':<12}{'质量/kg':>10}{'优化后':>9}{'减重':>8}"
          f"{'杆件':>10}{'利用率':>8}{'用时/ms':>9}")
    print(THIN)
    total_before = total_after = 0.0
    for name in cases.names():
        st = cases.get(name)
        res = optimize(st, prune_threshold=0.2)
        total_before += res.before["mass"]
        total_after += res.after["mass"]
        print(f"  {name:<12}{res.before['mass']:>10.1f}{res.after['mass']:>9.1f}"
              f"{res.mass_saving * 100:>7.1f}%"
              f"{res.before['members']:>5}→{res.after['members']:<4}"
              f"{res.after['max_ratio']:>8.3f}{res.elapsed * 1000:>9.0f}")
    print(THIN)
    print(f"  合计 {total_before:.0f} → {total_after:.0f} kg，"
          f"平均减重 {(1 - total_after / total_before) * 100:.1f}%")


# --------------------------------------------------------------------------


def main(argv) -> int:
    want = {int(a) for a in argv if a.isdigit()}
    for n, text, fn in DEMOS:
        if want and n not in want:
            continue
        title(f"{n}. {text}")
        fn()
    print()
    print(BAR)
    print("  演示结束。启动交互式工作台： python run.py serve")
    print(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
