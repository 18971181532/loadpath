"""线性代数内核：带状 Cholesky（LDLᵀ）、RCM 带宽优化、稠密参考解。

设计约束
--------
不能用 NumPy。桁架刚度矩阵是对称正定的稀疏矩阵，天然是**带状**的：
按节点编号顺序组装后，半带宽约等于相邻节点的最大编号差。于是：

* 用 RCM（Reverse Cuthill–McKee）重排节点，把带宽压到接近理论下界；
* 用按行存下三角的 **LDLᵀ** 分解，复杂度 O(n·b²)，比稠密 O(n³) 快一到两个数量级；
* 分解过程中出现非正主元，说明刚度矩阵奇异——结构是机构（有刚体位移或
  局部可变），这正是杆件剔除与冗余度扫描需要的判据，直接抛 :class:`MechanismError`。

另外保留一个稠密高斯消元 :func:`solve_dense`，只用于单元测试里交叉验证
带状分解的正确性——它不是"备用实现"，而是**对照标准**。
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

__all__ = [
    "MechanismError",
    "BandedSPD",
    "solve_dense",
    "rcm_order",
    "matrix_bandwidth",
]

_EPS = 1e-12
_REL_TOL = 1e-10   # 主元相对容差：低于它视为 0，即结构存在机构


class MechanismError(ValueError):
    """刚度矩阵奇异：结构存在机构或约束不足。"""


# --------------------------------------------------------------------------
# 稠密参考解（仅用于测试与极小系统）
# --------------------------------------------------------------------------


def solve_dense(a: Sequence[Sequence[float]], b: Sequence[float]) -> List[float]:
    """带部分主元的高斯消元，对称或非对称都可用。"""
    n = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < _EPS:
            raise MechanismError(f"矩阵在列 {col} 上奇异")
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
        inv = 1.0 / m[col][col]
        row_c = m[col]
        for r in range(col + 1, n):
            factor = m[r][col] * inv
            if factor == 0.0:
                continue
            row_r = m[r]
            for c in range(col, n + 1):
                row_r[c] -= factor * row_c[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = m[r][n]
        for c in range(r + 1, n):
            acc -= m[r][c] * x[c]
        x[r] = acc / m[r][r]
    return x


# --------------------------------------------------------------------------
# RCM 带宽优化
# --------------------------------------------------------------------------


def rcm_order(adjacency: Sequence[Iterable[int]]) -> List[int]:
    """Reverse Cuthill–McKee：返回节点的新编号顺序（列表下标=新号，值=旧号）。

    从度最小的节点出发做 BFS，按层内度升序排列，最后整体反转。
    对规则网格能把半带宽压到接近列数；对不规则地面结构也有明显效果。
    """
    n = len(adjacency)
    nbrs = [sorted(set(a)) for a in adjacency]
    seen = [False] * n
    order: List[int] = []
    # 多个连通分量都要覆盖
    for start in sorted(range(n), key=lambda v: (len(nbrs[v]), v)):
        if seen[start]:
            continue
        seen[start] = True
        queue = [start]
        head = 0
        while head < len(queue):
            v = queue[head]
            head += 1
            order.append(v)
            frontier = [w for w in nbrs[v] if not seen[w]]
            frontier.sort(key=lambda w: (len(nbrs[w]), w))
            for w in frontier:
                seen[w] = True
                queue.append(w)
    order.reverse()
    return order


def matrix_bandwidth(rows: Sequence[Dict[int, float]]) -> int:
    """半带宽：max(i - j)，j 为第 i 行最左侧非零列。"""
    bw = 0
    for i, row in enumerate(rows):
        if not row:
            continue
        bw = max(bw, i - min(row))
    return bw


# --------------------------------------------------------------------------
# 带状对称正定矩阵
# --------------------------------------------------------------------------


class BandedSPD:
    """对称正定带状矩阵的 LDLᵀ 分解与回代。

    只存下三角，第 i 行保存 ``[i-lo_i, i]`` 区间（``lo_i = max(0, i-bw)``）。
    """

    def __init__(self, n: int, rows: Sequence[Dict[int, float]]):
        self.n = n
        self.rows_in = rows
        self.bw = matrix_bandwidth(rows)
        self.lo = [max(0, i - self.bw) for i in range(n)]
        self.data: List[List[float]] = []
        for i in range(n):
            lo = self.lo[i]
            src = rows[i]
            self.data.append([src.get(j, 0.0) for j in range(lo, i + 1)])
        # 奇异判据必须**相对**于矩阵量级：桁架刚度是 EA/L 量级（1e4~1e6），
        # 绝对阈值要么放过 1e-11 的伪零主元，要么把正常结构判死。
        self.scale = max((abs(v) for row in self.data for v in row), default=0.0) or 1.0
        self.tol = _REL_TOL * self.scale
        self._l: List[List[float]] = []
        self._d: List[float] = []
        self.factorized = False

    # -- 分解 ------------------------------------------------------------
    def factorize(self) -> "BandedSPD":
        n, bw, lo = self.n, self.bw, self.lo
        l_rows = [row[:] for row in self.data]
        d = [0.0] * n
        for i in range(n):
            li = lo[i]
            li_row = l_rows[i]
            ncol = len(li_row) - 1  # 非对角元个数
            for jj in range(ncol):
                j = li + jj
                lj = lo[j]
                j_row = l_rows[j]
                s = li_row[jj]
                k0 = li if li > lj else lj
                for k in range(k0, j):
                    s -= li_row[k - li] * d[k] * j_row[k - lj]
                diag_j = d[j]
                if diag_j <= self.tol:
                    raise MechanismError(f"主元 {j} 非正（{diag_j:.3e}），结构存在机构")
                li_row[jj] = s / diag_j
            s = li_row[ncol]
            for k in range(li, i):
                lk = li_row[k - li]
                s -= lk * lk * d[k]
            if s <= self.tol:
                raise MechanismError(f"主元 {i} 非正（{s:.3e}），结构存在机构")
            d[i] = s
        self._l = l_rows
        self._d = d
        self.factorized = True
        return self

    # -- 求解 ------------------------------------------------------------
    def solve(self, rhs: Sequence[float]) -> List[float]:
        if not self.factorized:
            self.factorize()
        n, lo = self.n, self.lo
        y = list(rhs)
        for i in range(n):
            li = lo[i]
            row = self._l[i]
            acc = y[i]
            for k in range(li, i):
                acc -= row[k - li] * y[k]
            y[i] = acc
        for i in range(n):
            y[i] /= self._d[i]
        for i in range(n - 1, -1, -1):
            li = lo[i]
            row = self._l[i]
            yi = y[i]
            for k in range(li, i):
                y[k] -= row[k - li] * yi
        return y

    # -- 诊断 ------------------------------------------------------------
    def log_det(self) -> float:
        if not self.factorized:
            self.factorize()
        return sum(math.log(v) for v in self._d)

    def min_pivot(self) -> float:
        if not self.factorized:
            self.factorize()
        return min(self._d)
