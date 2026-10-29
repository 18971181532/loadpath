"""数据模型：材料、截面、节点、杆件、支座、工况、结构。

单位制统一为 **N / mm / MPa**（1 MPa = 1 N/mm²），质量用 kg，
密度因此是 kg/mm³（钢材 7.85e-6）。全项目不做单位换算，读入即此单位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Material",
    "Section",
    "Node",
    "Member",
    "Support",
    "LoadCase",
    "Structure",
    "MATERIALS",
]


# --------------------------------------------------------------------------
# 材料
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Material:
    """理想弹塑性材料，做允许应力法校核时需要 E、fy、rho。"""

    name: str
    label: str
    e: float          # 弹性模量 MPa
    fy: float         # 屈服强度 MPa
    rho: float        # 密度 kg/mm^3
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "E": self.e,
                "fy": self.fy, "rho": self.rho, "note": self.note}


MATERIALS: Dict[str, Material] = {
    m.name: m
    for m in (
        Material("q235", "Q235 碳素钢", 210000.0, 235.0, 7.85e-6, "最常用的型钢牌号"),
        Material("q355", "Q355 低合金钢", 210000.0, 355.0, 7.85e-6, "强度更高，屈曲控制时优势有限"),
        Material("q460", "Q460 高强钢", 206000.0, 460.0, 7.85e-6, "重载构件，焊接要求高"),
        Material("al6061", "6061-T6 铝合金", 70000.0, 240.0, 2.70e-6, "轻质，刚度只有钢的 1/3"),
        Material("glulam", "胶合木 GL28h", 12600.0, 28.0, 4.70e-7, "大跨屋盖常用，自重极小"),
    )
}


def get_material(key: str) -> Material:
    try:
        return MATERIALS[key]
    except KeyError:
        raise KeyError(f"未知材料 {key!r}，可选：{', '.join(sorted(MATERIALS))}") from None


# --------------------------------------------------------------------------
# 截面（圆钢管）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """圆钢管 CHS：外径 d、壁厚 t。桁架杆件最常用的截面形式。"""

    name: str
    d: float          # 外径 mm
    t: float          # 壁厚 mm

    @property
    def di(self) -> float:
        return self.d - 2.0 * self.t

    @property
    def area(self) -> float:
        """截面积 mm²。"""
        return math.pi / 4.0 * (self.d ** 2 - self.di ** 2)

    @property
    def inertia(self) -> float:
        """惯性矩 mm⁴。"""
        return math.pi / 64.0 * (self.d ** 4 - self.di ** 4)

    @property
    def radius(self) -> float:
        """回转半径 mm。"""
        return math.sqrt(self.inertia / self.area)

    @property
    def label(self) -> str:
        return f"CHS {self.d:.1f}×{self.t:.1f}"

    def mass_per_length(self, rho: float) -> float:
        """线质量 kg/mm。"""
        return self.area * rho

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "d": self.d, "t": self.t,
                "A": self.area, "I": self.inertia, "r": self.radius,
                "label": self.label}


def chs(d: float, t: float, name: Optional[str] = None) -> Section:
    """自定义圆钢管，未给名字时按规格自动命名。"""
    key = name or f"chs{d:g}x{t:g}"
    return Section(key, float(d), float(t))


# --------------------------------------------------------------------------
# 节点 / 杆件 / 支座 / 工况
# --------------------------------------------------------------------------


@dataclass
class Node:
    x: float
    y: float
    tag: str = ""

    def to_dict(self, index: int) -> Dict[str, Any]:
        return {"id": index, "x": self.x, "y": self.y, "tag": self.tag}


@dataclass
class Member:
    """二力杆：只承受轴力。``k_factor`` 是计算长度系数（两端铰接取 1.0）。"""

    i: int
    j: int
    section: Section
    material: Material
    k_factor: float = 1.0
    group: str = ""
    tension_only: bool = False

    def to_dict(self, index: int) -> Dict[str, Any]:
        return {"id": index, "i": self.i, "j": self.j,
                "section": self.section.name, "section_label": self.section.label,
                "material": self.material.name, "k": self.k_factor,
                "group": self.group, "tension_only": self.tension_only}


@dataclass
class Support:
    """支座：``fix_x`` / ``fix_y`` 分别约束两个平动自由度；``dx``/``dy`` 为强制位移。"""

    node: int
    fix_x: bool = True
    fix_y: bool = True
    dx: float = 0.0
    dy: float = 0.0

    @property
    def kind(self) -> str:
        if self.fix_x and self.fix_y:
            return "pin"
        if self.fix_y:
            return "roller-y"
        if self.fix_x:
            return "roller-x"
        return "free"

    def to_dict(self) -> Dict[str, Any]:
        return {"node": self.node, "fix_x": self.fix_x, "fix_y": self.fix_y,
                "dx": self.dx, "dy": self.dy, "kind": self.kind}


@dataclass
class LoadCase:
    """单个工况：一组节点集中力（N）。"""

    name: str
    loads: List[Tuple[int, float, float]] = field(default_factory=list)
    note: str = ""

    def add(self, node: int, fx: float = 0.0, fy: float = 0.0) -> "LoadCase":
        self.loads.append((node, float(fx), float(fy)))
        return self

    @property
    def resultant(self) -> Tuple[float, float]:
        return (sum(l[1] for l in self.loads), sum(l[2] for l in self.loads))

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "note": self.note,
                "loads": [{"node": n, "fx": fx, "fy": fy} for n, fx, fy in self.loads],
                "resultant": {"fx": self.resultant[0], "fy": self.resultant[1]}}


# --------------------------------------------------------------------------
# 结构
# --------------------------------------------------------------------------


class Structure:
    """一个完整的二维桁架模型。"""

    def __init__(self, name: str = "model", note: str = ""):
        self.name = name
        self.note = note
        self.nodes: List[Node] = []
        self.members: List[Member] = []
        self.supports: List[Support] = []
        self.cases: List[LoadCase] = []

    # -- 构建 ------------------------------------------------------------
    def add_node(self, x: float, y: float, tag: str = "") -> int:
        self.nodes.append(Node(float(x), float(y), tag))
        return len(self.nodes) - 1

    def add_member(self, i: int, j: int, section: Section,
                   material: Material, k_factor: float = 1.0,
                   group: str = "", tension_only: bool = False) -> int:
        if i == j:
            raise ValueError("杆件的两个节点不能相同")
        n = len(self.nodes)
        for k in (i, j):
            if not 0 <= k < n:
                raise IndexError(f"节点编号 {k} 超出范围 0..{n - 1}")
        self.members.append(Member(i, j, section, material, k_factor, group, tension_only))
        return len(self.members) - 1

    def add_support(self, node: int, fix_x: bool = True, fix_y: bool = True,
                    dx: float = 0.0, dy: float = 0.0) -> int:
        self.supports.append(Support(node, fix_x, fix_y, dx, dy))
        return len(self.supports) - 1

    def add_case(self, name: str, note: str = "") -> LoadCase:
        case = LoadCase(name, note=note)
        self.cases.append(case)
        return case

    # -- 查询 ------------------------------------------------------------
    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def dof_count(self) -> int:
        return 2 * len(self.nodes)

    @property
    def constrained(self) -> Dict[int, float]:
        """约束自由度 -> 强制位移值。"""
        out: Dict[int, float] = {}
        for s in self.supports:
            if not 0 <= s.node < len(self.nodes):
                raise IndexError(f"支座节点 {s.node} 不存在")
            if s.fix_x:
                out[2 * s.node] = s.dx
            if s.fix_y:
                out[2 * s.node + 1] = s.dy
        return out

    def length(self, m: Member) -> float:
        a, b = self.nodes[m.i], self.nodes[m.j]
        return math.hypot(b.x - a.x, b.y - a.y)

    def direction(self, m: Member) -> Tuple[float, float]:
        a, b = self.nodes[m.i], self.nodes[m.j]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length <= 0.0:
            raise ValueError("零长度杆件")
        return dx / length, dy / length

    def total_mass(self) -> float:
        """结构总质量 kg。"""
        return sum(self.length(m) * m.section.mass_per_length(m.material.rho)
                   for m in self.members)

    def total_length(self) -> float:
        return sum(self.length(m) for m in self.members)

    def span(self) -> float:
        xs = [n.x for n in self.nodes]
        return max(xs) - min(xs) if xs else 0.0

    def height(self) -> float:
        ys = [n.y for n in self.nodes]
        return max(ys) - min(ys) if ys else 0.0

    def bounds(self) -> Tuple[float, float, float, float]:
        xs = [n.x for n in self.nodes]
        ys = [n.y for n in self.nodes]
        return (min(xs), min(ys), max(xs), max(ys))

    def adjacency(self) -> List[List[int]]:
        adj: List[List[int]] = [[] for _ in self.nodes]
        for m in self.members:
            adj[m.i].append(m.j)
            adj[m.j].append(m.i)
        return adj

    def load_vector(self, case: LoadCase) -> List[float]:
        f = [0.0] * self.dof_count
        for node, fx, fy in case.loads:
            if not 0 <= node < len(self.nodes):
                raise IndexError(f"荷载节点 {node} 不存在")
            f[2 * node] += fx
            f[2 * node + 1] += fy
        return f

    # -- 序列化 ----------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "note": self.note,
            "units": {"length": "mm", "force": "N", "stress": "MPa", "mass": "kg"},
            "nodes": [n.to_dict(i) for i, n in enumerate(self.nodes)],
            "members": [m.to_dict(i) for i, m in enumerate(self.members)],
            "supports": [s.to_dict() for s in self.supports],
            "cases": [c.to_dict() for c in self.cases],
            "stats": {
                "nodes": len(self.nodes),
                "members": len(self.members),
                "dof": self.dof_count,
                "mass": self.total_mass(),
                "length": self.total_length(),
                "span": self.span(),
                "height": self.height(),
            },
        }

    # -- 校验 ------------------------------------------------------------
    def validate(self) -> List[str]:
        """返回问题清单；空列表表示模型可用。"""
        issues: List[str] = []
        if not self.nodes:
            issues.append("模型没有节点")
        if not self.members:
            issues.append("模型没有杆件")
        used = set()
        for m in self.members:
            a, b = self.nodes[m.i], self.nodes[m.j]
            if math.hypot(b.x - a.x, b.y - a.y) <= 1e-9:
                issues.append(f"零长度杆件：{m.i}-{m.j}")
            used.update((m.i, m.j))
        lonely = [i for i in range(len(self.nodes)) if i not in used]
        if lonely:
            issues.append(f"孤立节点（未连接任何杆件）：{lonely[:8]}")
        if not self.supports:
            issues.append("模型没有支座，存在刚体位移")
        else:
            fix = self.constrained
            fx = sum(1 for d in fix if d % 2 == 0)
            fy = sum(1 for d in fix if d % 2 == 1)
            if fx < 1:
                issues.append("缺少 x 方向约束")
            if fy < 1:
                issues.append("缺少 y 方向约束")
            if fx + fy < 3:
                issues.append("约束数不足 3 个，平面结构会有转动自由度")
        if not self.cases:
            issues.append("模型没有荷载工况")
        for c in self.cases:
            if not c.loads:
                issues.append(f"工况 {c.name!r} 没有任何荷载")
        return issues

    def copy(self) -> "Structure":
        s = Structure(self.name, self.note)
        s.nodes = [Node(n.x, n.y, n.tag) for n in self.nodes]
        s.members = [Member(m.i, m.j, m.section, m.material, m.k_factor,
                            m.group, m.tension_only) for m in self.members]
        s.supports = [Support(sp.node, sp.fix_x, sp.fix_y, sp.dx, sp.dy)
                      for sp in self.supports]
        s.cases = [LoadCase(c.name, list(c.loads), c.note) for c in self.cases]
        return s
