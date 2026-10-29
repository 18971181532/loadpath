"""内置算例：5 个可直接分析的二维桁架。

每个算例都是**稳定**的（m + r ≥ 2n 且无局部机构），支座约束恰好消除刚体位移。
尺寸取真实工程量级：屋架跨 12 m、桁架桥跨 24 m、塔高 12 m、荷载 kN 级。
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

from .model import Material, Section, Structure, chs, get_material

__all__ = ["CASES", "names", "get", "info"]


def _mat(key: str = "q235") -> Material:
    return get_material(key)


def _sec(d: float = 114.3, t: float = 4.5) -> Section:
    return chs(d, t)


# --------------------------------------------------------------------------
# 1. 华伦式屋架（平行弦）
# --------------------------------------------------------------------------


def warren() -> Structure:
    """12 m 跨平行弦屋架，8 个节间，上弦节点受均布竖向荷载 10 kN。"""
    st = Structure("warren", "12 m 跨平行弦屋架，上弦均布节点荷载")
    span, depth, panels = 12000.0, 1500.0, 8
    step = span / panels
    mat, sec = _mat(), _sec(114.3, 4.5)

    bottom = [st.add_node(i * step, 0.0, f"B{i}") for i in range(panels + 1)]
    top = [st.add_node(i * step, depth, f"T{i}") for i in range(panels + 1)]

    for i in range(panels):
        st.add_member(bottom[i], bottom[i + 1], sec, mat, group="下弦")
        st.add_member(top[i], top[i + 1], sec, mat, group="上弦")
    for i in range(panels + 1):
        st.add_member(bottom[i], top[i], sec, mat, group="竖杆")
    # 斜腹杆：朝跨中下倾
    for i in range(panels):
        if i < panels // 2:
            st.add_member(bottom[i], top[i + 1], sec, mat, group="斜腹杆")
        else:
            st.add_member(top[i], bottom[i + 1], sec, mat, group="斜腹杆")

    st.add_support(bottom[0], True, True)
    st.add_support(bottom[-1], False, True)

    case = st.add_case("恒+活", "上弦节点竖向 10 kN")
    for n in top[1:-1]:
        case.add(n, 0.0, -10000.0)
    case.add(top[0], 0.0, -5000.0)
    case.add(top[-1], 0.0, -5000.0)
    return st


# --------------------------------------------------------------------------
# 2. 普拉特桁架桥
# --------------------------------------------------------------------------


def pratt() -> Structure:
    """24 m 跨简支桁架桥，8 节间，桥面下弦节点受 20 kN 车辆荷载。"""
    st = Structure("pratt", "24 m 跨简支桁架桥，下弦节点荷载")
    span, depth, panels = 24000.0, 3000.0, 8
    step = span / panels
    mat, sec = _mat(), _sec(168.3, 5.6)

    bottom = [st.add_node(i * step, 0.0, f"B{i}") for i in range(panels + 1)]
    top = [st.add_node(i * step, depth, f"T{i}") for i in range(panels + 1)]

    for i in range(panels):
        st.add_member(bottom[i], bottom[i + 1], sec, mat, group="下弦")
        st.add_member(top[i], top[i + 1], sec, mat, group="上弦")
    for i in range(panels + 1):
        st.add_member(bottom[i], top[i], sec, mat, group="竖杆")
    # 普拉特式：斜杆向跨中上倾（受拉）
    half = panels // 2
    for i in range(panels):
        if i < half:
            st.add_member(bottom[i], top[i + 1], sec, mat, group="斜腹杆")
        else:
            st.add_member(bottom[i + 1], top[i], sec, mat, group="斜腹杆")

    st.add_support(bottom[0], True, True)
    st.add_support(bottom[-1], False, True)

    case = st.add_case("车辆", "下弦节点竖向 20 kN")
    for n in bottom[1:-1]:
        case.add(n, 0.0, -20000.0)
    return st


# --------------------------------------------------------------------------
# 3. 悬臂桁架
# --------------------------------------------------------------------------


def cantilever() -> Structure:
    """6 m 悬臂桁架，根部两节点铰接，端部受 30 kN 竖向荷载。"""
    st = Structure("cantilever", "6 m 悬臂桁架，端部集中荷载")
    length, depth, panels = 6000.0, 1200.0, 4
    step = length / panels
    mat, sec = _mat(), _sec(139.7, 5.0)

    bottom = [st.add_node(i * step, 0.0, f"B{i}") for i in range(panels + 1)]
    top = [st.add_node(i * step, depth, f"T{i}") for i in range(panels + 1)]

    for i in range(panels):
        st.add_member(bottom[i], bottom[i + 1], sec, mat, group="下弦")
        st.add_member(top[i], top[i + 1], sec, mat, group="上弦")
    for i in range(panels + 1):
        st.add_member(bottom[i], top[i], sec, mat, group="竖杆")
    for i in range(panels):
        st.add_member(bottom[i], top[i + 1], sec, mat, group="斜腹杆")

    st.add_support(bottom[0], True, True)
    st.add_support(top[0], True, True)

    case = st.add_case("端部", "自由端竖杆中点节点 30 kN 向下")
    case.add(top[-1], 0.0, -30000.0)
    return st


# --------------------------------------------------------------------------
# 4. 输电塔架
# --------------------------------------------------------------------------


def tower() -> Structure:
    """12 m 高变截面塔架，底部固结，顶部受 15 kN 水平风荷载。"""
    st = Structure("tower", "12 m 变截面塔架，顶部水平风荷载")
    levels = 4
    height = 12000.0
    widths = [4000.0, 3300.0, 2600.0, 2000.0, 1500.0]
    mat, sec = _mat(), _sec(139.7, 5.0)

    left: List[int] = []
    right: List[int] = []
    for k in range(levels + 1):
        y = height * k / levels
        hw = widths[k] / 2.0
        left.append(st.add_node(-hw, y, f"L{k}"))
        right.append(st.add_node(hw, y, f"R{k}"))

    for k in range(levels):
        st.add_member(left[k], left[k + 1], sec, mat, group="柱")
        st.add_member(right[k], right[k + 1], sec, mat, group="柱")
        st.add_member(left[k], right[k + 1], sec, mat, group="斜撑")
    for k in range(levels + 1):
        st.add_member(left[k], right[k], sec, mat, group="横杆")

    st.add_support(left[0], True, True)
    st.add_support(right[0], True, True)

    case = st.add_case("风", "顶部水平 15 kN")
    case.add(left[-1], 15000.0, 0.0)
    case.add(right[-1], 15000.0, 0.0)
    return st


# --------------------------------------------------------------------------
# 5. MBB 半梁：紧凑地面结构，用于演示拓扑精简
# --------------------------------------------------------------------------


def mbb(nx: int = 6, ny: int = 2, cell: float = 1000.0,
        radius: float = 1.55) -> Structure:
    """MBB 半梁的设计域地面结构：节点全连（距离 ≤ radius·cell）。

    这是拓扑优化的经典算例：左端下弦铰支、上弦 x 向滚动，右端中点受集中力。
    地面结构给得越密，精简后得到的杆系越接近理论最优。
    """
    st = Structure("mbb", "MBB 半梁地面结构，用于拓扑精简演示")
    mat, sec = _mat(), _sec(88.9, 4.0)

    ids: Dict[Tuple[int, int], int] = {}
    for j in range(ny + 1):
        for i in range(nx + 1):
            ids[(i, j)] = st.add_node(i * cell, j * cell, f"N{i}{j}")

    limit = radius * cell
    keys = sorted(ids)
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            (i1, j1), (i2, j2) = keys[a], keys[b]
            dx, dy = (i2 - i1) * cell, (j2 - j1) * cell
            if math.hypot(dx, dy) <= limit + 1e-9:
                st.add_member(ids[(i1, j1)], ids[(i2, j2)], sec, mat, group="地面结构")

    st.add_support(ids[(0, 0)], True, True)
    st.add_support(ids[(0, ny)], True, False)

    case = st.add_case("P", "右端中点 40 kN 向下")
    case.add(ids[(nx, 0)], 0.0, -40000.0)
    return st


# --------------------------------------------------------------------------


CASES: Dict[str, Tuple[str, Callable[[], Structure], str]] = {
    "warren": ("华伦式屋架", warren, "12 m 跨、8 节间平行弦屋架，上弦均布荷载"),
    "pratt": ("普拉特桁架桥", pratt, "24 m 跨简支桁架桥，下弦承受车辆荷载"),
    "cantilever": ("悬臂桁架", cantilever, "6 m 悬臂，根部双铰，端部集中力"),
    "tower": ("输电塔架", tower, "12 m 变截面塔，顶部水平风荷载"),
    "mbb": ("MBB 半梁地面结构", mbb, "稠密地面结构，适合演示拓扑精简"),
}


def names() -> List[str]:
    return list(CASES)


def get(name: str) -> Structure:
    try:
        return CASES[name][1]()
    except KeyError:
        raise KeyError(f"未知算例 {name!r}，可选：{', '.join(CASES)}") from None


def info(name: str) -> Dict[str, str]:
    label, _, note = CASES[name]
    return {"name": name, "label": label, "note": note}


def label(name: str) -> str:
    return CASES[name][0]
