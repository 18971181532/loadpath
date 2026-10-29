"""校核：允许应力法（拉/压 + 欧拉屈曲）、长细比、挠度、整体平衡残差。

这是"结构能不能用"的判据层。所有结论都可以被手算复核：单根杆的轴力来自
求解器，承载力来自 π²EI/(KL)² 与 fy·A/γ 的较小值，两者相除就是利用率。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import Structure
from .solver import Analysis

__all__ = ["CheckOptions", "MemberCheck", "CheckReport", "check"]

GAMMA = 1.5          # 允许应力法安全系数
LAM_COMP = 200.0     # 压杆容许长细比
LAM_TENS = 400.0     # 拉杆容许长细比


@dataclass
class CheckOptions:
    gamma: float = GAMMA
    lam_comp: float = LAM_COMP
    lam_tens: float = LAM_TENS
    deflection_ratio: float = 250.0   # 容许挠度 = 跨度 / ratio


@dataclass
class MemberCheck:
    member: int
    i: int
    j: int
    length: float
    section: str
    area: float
    radius: float
    slenderness: float
    force: float           # 控制工况下的轴力，拉为正
    force_case: str
    stress: float          # MPa
    capacity: float        # 容许轴力 N（幅值）
    ratio: float           # 控制利用率 = max(强度比, 长细比)，>1 表示超限
    strength_ratio: float  # 仅强度/屈曲的利用率
    buckling: bool         # 是否由屈曲控制
    lam_ratio: float
    status: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "member": self.member, "i": self.i, "j": self.j,
            "length": self.length, "section": self.section,
            "area": self.area, "radius": self.radius,
            "slenderness": self.slenderness, "force": self.force,
            "force_case": self.force_case, "stress": self.stress,
            "capacity": self.capacity, "ratio": self.ratio,
            "strength_ratio": self.strength_ratio,
            "buckling": self.buckling, "lam_ratio": self.lam_ratio,
            "status": self.status,
        }


@dataclass
class CheckReport:
    members: List[MemberCheck] = field(default_factory=list)
    max_ratio: float = 0.0
    critical_member: int = -1
    max_disp: float = 0.0
    max_disp_case: str = ""
    max_disp_node: int = -1
    disp_limit: float = 0.0
    disp_ratio: float = 0.0
    equilibrium: Dict[str, float] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    options: CheckOptions = field(default_factory=CheckOptions)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def verdict(self) -> str:
        if any("机构" in i for i in self.issues):
            return "不稳定"
        return "通过" if self.ok else "超限"

    def governing(self) -> List[MemberCheck]:
        return sorted(self.members, key=lambda m: -m.ratio)


def check(structure: Structure, analysis: Analysis,
          options: Optional[CheckOptions] = None) -> CheckReport:
    opt = options or CheckOptions()
    report = CheckReport(options=opt)

    # 每个工况每根杆的容许轴力只和几何/截面/材料有关
    allow: List[float] = []
    buckling_allow: List[float] = []
    for m in structure.members:
        length = structure.length(m)
        lam = m.k_factor * length / m.section.radius
        sigma_cr = math.pi ** 2 * m.material.e / (lam * lam) if lam > 0 else float("inf")
        allow.append(min(m.material.fy, sigma_cr) / opt.gamma * m.section.area)
        buckling_allow.append(sigma_cr < m.material.fy)

    for idx, m in enumerate(structure.members):
        length = structure.length(m)
        lam = m.k_factor * length / m.section.radius
        worst = 0.0
        worst_case = analysis.solutions[0].case if analysis.solutions else ""
        worst_force = 0.0
        for sol in analysis.solutions:
            n = sol.forces[idx]
            ratio = abs(n) / allow[idx] if allow[idx] > 0 else float("inf")
            if ratio > worst:
                worst, worst_case, worst_force = ratio, sol.case, n
        lam_limit = opt.lam_comp if worst_force < 0 else opt.lam_tens
        lam_ratio = lam / lam_limit if lam_limit > 0 else 0.0
        ratio = max(worst, lam_ratio)
        is_comp = worst_force < 0
        if ratio > 1.0:
            if lam_ratio > worst:
                status = "长细比超限"
            elif is_comp and buckling_allow[idx]:
                status = "屈曲超限"
            else:
                status = "应力超限"
        else:
            status = "通过"
        report.members.append(MemberCheck(
            member=idx, i=m.i, j=m.j, length=length,
            section=m.section.label, area=m.section.area, radius=m.section.radius,
            slenderness=lam, force=worst_force, force_case=worst_case,
            stress=worst_force / m.section.area, capacity=allow[idx],
            ratio=ratio, strength_ratio=worst,
            buckling=bool(is_comp and buckling_allow[idx]),
            lam_ratio=lam_ratio, status=status,
        ))

    if report.members:
        crit = max(report.members, key=lambda c: c.ratio)
        report.max_ratio = crit.ratio
        report.critical_member = crit.member
        if crit.ratio > 1.0:
            report.issues.append(
                f"杆件 #{crit.member}（{crit.i}-{crit.j}）{crit.status}：利用率 {crit.ratio:.2f}"
            )

    # 挠度
    span = structure.span()
    report.disp_limit = span / opt.deflection_ratio if span > 0 else 0.0
    gov = analysis.governing()
    if gov:
        report.max_disp_case, report.max_disp_node, report.max_disp = gov[0], gov[1], gov[3]
        if report.disp_limit > 0:
            report.disp_ratio = report.max_disp / report.disp_limit
            if report.disp_ratio > 1.0:
                report.issues.append(
                    f"挠度超限：{report.max_disp:.1f} mm > {report.disp_limit:.1f} mm"
                    f"（跨度/{opt.deflection_ratio:.0f}）"
                )

    # 整体平衡残差：ΣR + ΣF = 0，ΣM = 0（关于原点取矩 M = Σ(x·Fy − y·Fx)）
    worst_force = worst_moment = 0.0
    scale = 1.0
    for sol in analysis.solutions:
        case = next((c for c in structure.cases if c.name == sol.case), None)
        if case is None:
            continue
        rx = sum(v for d, v in sol.reactions.items() if d % 2 == 0)
        ry = sum(v for d, v in sol.reactions.items() if d % 2 == 1)
        fx, fy = case.resultant
        worst_force = max(worst_force, abs(rx + fx), abs(ry + fy))
        moment = 0.0
        for node, lfx, lfy in case.loads:
            nd = structure.nodes[node]
            moment += nd.x * lfy - nd.y * lfx
        for d, v in sol.reactions.items():
            nd = structure.nodes[d // 2]
            if d % 2 == 0:
                moment -= nd.y * v
            else:
                moment += nd.x * v
        worst_moment = max(worst_moment, abs(moment))
        scale = max(scale, abs(fx) + abs(fy) + abs(rx) + abs(ry))

    span_ref = max(span, 1.0)
    report.equilibrium = {
        "force_residual": worst_force,
        "moment_residual": worst_moment,
        "relative": worst_force / scale,
        "moment_relative": worst_moment / (scale * span_ref),
    }
    if report.equilibrium["relative"] > 1e-6:
        report.issues.append(
            f"平衡残差过大：{report.equilibrium['relative']:.2e}（求解器可能出错）"
        )
    return report
