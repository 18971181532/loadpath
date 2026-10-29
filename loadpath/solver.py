"""组装与求解：全局刚度矩阵、约束消元、多工况回代、内力与反力。

刚度矩阵只和几何、截面、材料有关，**与工况无关**，因此一次分解、多次回代：
多工况分析的总成本 ≈ 一次 O(n·b²) 分解 + 每工况 O(n·b)。选型迭代与冗余度
扫描都建立在这个特性上，否则几十次重复分析会慢到不可用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .linalg import BandedSPD, MechanismError, rcm_order
from .model import LoadCase, Member, Structure

__all__ = ["Solution", "System", "Analysis", "analyse", "member_stiffness_local"]


@dataclass
class Solution:
    """单个工况的求解结果。"""

    case: str
    u: List[float]                 # 节点位移，长度 2n，顺序 [ux0, uy0, ux1, ...]
    forces: List[float]            # 杆件轴力 N，拉为正
    reactions: Dict[int, float]    # 自由度编号 -> 反力 N
    energy: float                  # 应变能 N·mm
    max_disp: float
    max_disp_node: int
    max_disp_dof: int

    def disp(self, node: int) -> Tuple[float, float]:
        return self.u[2 * node], self.u[2 * node + 1]

    def magnitude(self, node: int) -> float:
        return math.hypot(self.u[2 * node], self.u[2 * node + 1])


@dataclass
class Analysis:
    """一次完整分析：系统信息 + 各工况解。"""

    structure: Structure
    solutions: List[Solution] = field(default_factory=list)
    bandwidth: int = 0
    dof: int = 0
    free_dof: int = 0
    min_pivot: float = 0.0

    def by_name(self, name: str) -> Optional[Solution]:
        for s in self.solutions:
            if s.case == name:
                return s
        return None

    @property
    def max_disp(self) -> float:
        return max((s.max_disp for s in self.solutions), default=0.0)

    def governing(self) -> Optional[Tuple[str, int, int, float]]:
        """返回位移最大的 (工况, 节点, 自由度, 位移)。"""
        best = None
        for s in self.solutions:
            if best is None or s.max_disp > best[3]:
                best = (s.case, s.max_disp_node, s.max_disp_dof, s.max_disp)
        return best


def member_stiffness_local(structure: Structure, m: Member) -> List[List[float]]:
    """杆件在整体坐标下的 4×4 刚度矩阵（自由度顺序 ui, vi, uj, vj）。"""
    length = structure.length(m)
    c, s = structure.direction(m)
    k = m.section.area * m.material.e / length
    cc, cs, ss = c * c, c * s, s * s
    return [
        [k * cc, k * cs, -k * cc, -k * cs],
        [k * cs, k * ss, -k * cs, -k * ss],
        [-k * cc, -k * cs, k * cc, k * cs],
        [-k * cs, -k * ss, k * cs, k * ss],
    ]


class System:
    """组装好的结构系统，可重复对不同工况求解。"""

    def __init__(self, structure: Structure, reorder: bool = True):
        self.structure = structure
        self.n = structure.dof_count
        self.order = rcm_order(structure.adjacency()) if reorder else list(range(len(structure.nodes)))
        self.pos = [0] * len(structure.nodes)
        for new, old in enumerate(self.order):
            self.pos[old] = new
        # 约束集必须换到重排后的自由度编号，否则与矩阵行号错位
        self.constrained: Dict[int, float] = {}
        for d, val in structure.constrained.items():
            node, comp = divmod(d, 2)
            self.constrained[2 * self.pos[node] + comp] = val
        self.free = [d for d in range(self.n) if d not in self.constrained]
        self.free_index = {d: i for i, d in enumerate(self.free)}
        self.rows: List[Dict[int, float]] = [dict() for _ in range(self.n)]
        self.bandwidth = 0
        self._matrix: Optional[BandedSPD] = None
        self._assemble()

    def to_original_dof(self, d: int) -> int:
        """重排编号 -> 原始自由度编号（2·节点 + 分量）。"""
        return 2 * self.order[d // 2] + d % 2

    # -- 组装 ------------------------------------------------------------
    def _dof(self, node: int, comp: int) -> int:
        return 2 * self.pos[node] + comp

    def _assemble(self) -> None:
        rows = self.rows
        for m in self.structure.members:
            ke = member_stiffness_local(self.structure, m)
            dofs = [self._dof(m.i, 0), self._dof(m.i, 1),
                    self._dof(m.j, 0), self._dof(m.j, 1)]
            for a in range(4):
                da = dofs[a]
                row = rows[da]
                for b in range(4):
                    db = dofs[b]
                    if db > da:
                        continue
                    row[db] = row.get(db, 0.0) + ke[a][b]
        self.bandwidth = max((d - min(r) for d, r in enumerate(rows) if r), default=0)

    def _get(self, i: int, j: int) -> float:
        if i >= j:
            return self.rows[i].get(j, 0.0)
        return self.rows[j].get(i, 0.0)

    # -- 分解 ------------------------------------------------------------
    def factorize(self) -> "System":
        if self._matrix is not None:
            return self
        nf = len(self.free)
        if nf == 0:
            self._matrix = BandedSPD(0, [])
            self._matrix.factorized = True
            self._matrix.bw = 0
            return self
        sub: List[Dict[int, float]] = []
        for gi in self.free:
            src = self.rows[gi]
            row: Dict[int, float] = {}
            for gj, val in src.items():
                k = self.free_index.get(gj)
                if k is not None:
                    row[k] = val
            sub.append(row)
        matrix = BandedSPD(nf, sub)
        try:
            matrix.factorize()
        except MechanismError as exc:
            raise MechanismError(
                f"结构不稳定：{exc}。检查支座约束是否足够，或是否有孤立/共线的节点"
            ) from None
        self._matrix = matrix
        return self

    # -- 求值 ------------------------------------------------------------
    def solve(self, case: LoadCase) -> Solution:
        if self._matrix is None:
            self.factorize()
        st = self.structure
        f = st.load_vector(case)
        nf = len(self.free)
        rhs = [0.0] * nf
        for i, d in enumerate(self.free):
            rhs[i] = f[self.to_original_dof(d)]
        # 强制位移（支座沉降）折算到右端项
        fixed = [(d, val) for d, val in self.constrained.items() if val != 0.0]
        if fixed:
            for i, d in enumerate(self.free):
                acc = rhs[i]
                for fd, val in fixed:
                    acc -= self._get(d, fd) * val
                rhs[i] = acc

        up = [0.0] * self.n                      # 重排编号下的位移
        for d, val in self.constrained.items():
            up[d] = val
        sol = self._matrix.solve(rhs) if nf else []
        for i, d in enumerate(self.free):
            up[d] = sol[i]

        # 还原到原始节点编号，后续全部用原始编号
        u = [0.0] * self.n
        for p in range(self.n):
            u[self.to_original_dof(p)] = up[p]

        # 杆件内力：N = EA/L * (u_j - u_i)·e
        forces: List[float] = []
        energy = 0.0
        ku = [0.0] * self.n
        for m in st.members:
            ke = member_stiffness_local(st, m)
            length = st.length(m)
            c, s = st.direction(m)
            ue = [u[2 * m.i], u[2 * m.i + 1], u[2 * m.j], u[2 * m.j + 1]]
            ea_l = m.section.area * m.material.e / length
            axial = (ue[2] - ue[0]) * c + (ue[3] - ue[1]) * s
            forces.append(ea_l * axial)
            fe = [sum(ke[a][b] * ue[b] for b in range(4)) for a in range(4)]
            for a, dof in enumerate((2 * m.i, 2 * m.i + 1, 2 * m.j, 2 * m.j + 1)):
                ku[dof] += fe[a]
            energy += 0.5 * (ue[0] * fe[0] + ue[1] * fe[1] + ue[2] * fe[2] + ue[3] * fe[3])

        reactions = {self.to_original_dof(d): ku[self.to_original_dof(d)] - f[self.to_original_dof(d)]
                     for d in self.constrained}

        mags = [math.hypot(u[2 * i], u[2 * i + 1]) for i in range(len(st.nodes))]
        peak = max(range(len(mags)), key=lambda i: mags[i]) if mags else 0
        peak_dof = 2 * peak if abs(u[2 * peak]) >= abs(u[2 * peak + 1]) else 2 * peak + 1
        return Solution(
            case=case.name, u=u, forces=forces, reactions=reactions,
            energy=energy, max_disp=mags[peak] if mags else 0.0,
            max_disp_node=peak, max_disp_dof=peak_dof,
        )

    def solve_all(self) -> Analysis:
        self.factorize()
        analysis = Analysis(structure=self.structure, bandwidth=self.bandwidth,
                            dof=self.n, free_dof=len(self.free))
        analysis.solutions = [self.solve(c) for c in self.structure.cases]
        analysis.min_pivot = self._matrix.min_pivot() if len(self.free) else 0.0
        return analysis


def analyse(structure: Structure, reorder: bool = True) -> Analysis:
    """便捷入口：组装 → 分解 → 全部工况求解。"""
    return System(structure, reorder=reorder).solve_all()
