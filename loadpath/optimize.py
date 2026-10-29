"""优化与评估：

* :func:`size_members` —— 离散截面选型（满应力迭代，每次只跨一档，避免来回振荡）
* :func:`prune_members` —— 剔除低利用率杆件，逐步精简拓扑
* :func:`optimize` —— 上述两步交替的外层循环
* :func:`redundancy_scan` —— **逐根拆除**，看结构是否变成机构、剩余杆件有多吃紧

最后一项是这个工具的重点：常规分析只回答"现在安全吗"，冗余度扫描回答
"如果某根杆坏了，还安全吗"。对每一根杆做一次拆除后重分析，成本是 n 次
线性求解——因为刚度分解可复用于同一荷载，代价可以接受。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .catalog import CATALOG, capacity, select_section
from .check import CheckOptions, check
from .linalg import MechanismError
from .model import Member, Section, Structure
from .solver import System, analyse

__all__ = [
    "SizingStep", "SizingResult", "PruneResult", "OptimizeResult",
    "MemberRisk", "RedundancyReport",
    "size_members", "prune_members", "optimize", "redundancy_scan",
]


# --------------------------------------------------------------------------
# 离散选型
# --------------------------------------------------------------------------


@dataclass
class SizingStep:
    iteration: int
    mass: float
    max_ratio: float
    max_disp: float
    changes: int


@dataclass
class SizingResult:
    structure: Structure
    history: List[SizingStep] = field(default_factory=list)
    converged: bool = False
    mass_before: float = 0.0
    mass_after: float = 0.0


def _pool_index(pool: Sequence[Section], sec: Section) -> int:
    for k, s in enumerate(pool):
        if s.name == sec.name:
            return k
    return 0


def size_members(structure: Structure, pool: Optional[Sequence[Section]] = None,
                 options: Optional[CheckOptions] = None, max_iter: int = 18,
                 step_limit: int = 1) -> SizingResult:
    """把每根杆换成目录里最轻且够用的截面。

    力的大小依赖刚度分布，改截面会改变内力，所以要迭代。每次只允许跨
    ``step_limit`` 档，相当于对不动点迭代做阻尼——否则相邻两档之间会来回跳。

    迭代分两段：先让强度（应力 + 屈曲 + 长细比）收敛；强度稳定后再看挠度。
    桁架位移近似满足 ``δ = Σ NᵢnᵢLᵢ/(EᵢAᵢ)``，把全部截面按 ``δ/δ_lim`` 等比放大
    就能把挠度压到限值附近（静定结构是精确的，超静定是近似），所以这里给每根
    杆记一个**只升不降**的截面下界，避免放大后又被强度逻辑缩回去。
    """
    pool = list(pool or CATALOG)
    opt = options or CheckOptions()
    work = structure.copy()
    result = SizingResult(structure=work, mass_before=work.total_mass())
    floor = [0] * len(work.members)
    limit = work.span() / opt.deflection_ratio if opt.deflection_ratio > 0 else 0.0

    for it in range(max_iter):
        try:
            analysis = analyse(work)
        except MechanismError:
            break
        demand = [0.0] * len(work.members)
        for sol in analysis.solutions:
            for k, n in enumerate(sol.forces):
                if abs(n) > demand[k]:
                    demand[k] = abs(n)

        changes = 0
        for k, m in enumerate(work.members):
            target = select_section(m.material, work.length(m), demand[k],
                                    m.k_factor, opt.gamma, opt.lam_comp, pool)
            tgt = max(_pool_index(pool, target), floor[k])
            cur = _pool_index(pool, m.section)
            if tgt == cur:
                continue
            new_idx = cur + max(-step_limit, min(step_limit, tgt - cur))
            if new_idx != cur:
                m.section = pool[new_idx]
                changes += 1

        if changes == 0 and limit > 0:
            rep = check(work, analysis, opt)
            if rep.max_disp > limit * 1.001:
                scale = rep.max_disp / limit
                bumped = 0
                for k, m in enumerate(work.members):
                    need = m.section.area * scale
                    j = 0
                    while j < len(pool) - 1 and pool[j].area < need:
                        j += 1
                    if j > floor[k]:
                        floor[k] = j
                        bumped += 1
                changes = bumped

        rep = check(work, analysis, opt)
        result.history.append(SizingStep(it, work.total_mass(), rep.max_ratio,
                                         rep.max_disp, changes))
        if changes == 0:
            result.converged = True
            break

    result.mass_after = work.total_mass()
    return result


# --------------------------------------------------------------------------
# 拓扑精简
# --------------------------------------------------------------------------


@dataclass
class PruneResult:
    structure: Structure
    removed: List[int] = field(default_factory=list)       # 原始编号
    mass_before: float = 0.0
    mass_after: float = 0.0


def prune_members(structure: Structure, threshold: float = 0.08,
                  max_remove: Optional[int] = None,
                  options: Optional[CheckOptions] = None) -> PruneResult:
    """按利用率从低到高逐根试拆，拆完仍是稳定结构才真的删掉。

    ``threshold`` 是"值得保留的门槛"：利用率低于它的杆件对承载力贡献很小。
    拆除后若刚度矩阵奇异（变成机构），或者剩余杆件利用率越过 1.0，就放弃这一根。
    """
    opt = options or CheckOptions()
    work = structure.copy()
    original = list(work.members)
    try:
        base = check(work, analyse(work), opt)
    except MechanismError:
        return PruneResult(structure=work, removed=[],
                           mass_before=structure.total_mass(),
                           mass_after=structure.total_mass())
    order = sorted(range(len(work.members)), key=lambda k: base.members[k].ratio)
    keep = list(work.members)
    removed: List[int] = []
    limit = max_remove if max_remove is not None else int(0.4 * len(keep))

    for idx in order:
        if len(removed) >= limit:
            break
        if base.members[idx].ratio > threshold:
            continue
        victim = original[idx]
        trial_members = [m for m in keep if m is not victim]
        if len(trial_members) < 2:
            break
        trial = work.copy()
        trial.members = trial_members
        try:
            trial_analysis = analyse(trial)
        except MechanismError:
            continue          # 拆掉就成了机构，这根杆是必需的
        trial_report = check(trial, trial_analysis, opt)
        if trial_report.max_ratio > 1.0:
            continue          # 拆掉会让别的杆超载
        keep = trial_members
        work = trial
        removed.append(idx)

    return PruneResult(structure=work, removed=sorted(removed),
                       mass_before=structure.total_mass(), mass_after=work.total_mass())


# --------------------------------------------------------------------------
# 组合优化
# --------------------------------------------------------------------------


@dataclass
class OptimizeResult:
    structure: Structure
    sizing: SizingResult
    prunes: List[PruneResult] = field(default_factory=list)
    before: Dict[str, float] = field(default_factory=dict)
    after: Dict[str, float] = field(default_factory=dict)
    removed: List[int] = field(default_factory=list)
    history: List[SizingStep] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def mass_saving(self) -> float:
        mb = self.before.get("mass", 0.0)
        return 1.0 - self.after.get("mass", 0.0) / mb if mb > 0 else 0.0


def optimize(structure: Structure, pool: Optional[Sequence[Section]] = None,
             options: Optional[CheckOptions] = None, rounds: int = 3,
             prune_threshold: float = 0.08,
             do_prune: bool = True) -> OptimizeResult:
    """选型 → 精简 → 再选型，直到质量不再下降。"""
    t0 = time.time()
    opt = options or CheckOptions()
    work = structure.copy()

    base_analysis = analyse(work)
    base_report = check(work, base_analysis, opt)
    before = {"mass": work.total_mass(), "members": len(work.members),
              "max_ratio": base_report.max_ratio, "max_disp": base_report.max_disp}

    # 先减拓扑、后减截面：在杆件还比较粗的时候剔除低应力杆，
    # 剩余结构仍有余量吸收转移过来的内力；反过来先选型会把利用率顶到 1.0，
    # 此后任何一根杆都拆不掉。
    sizing = SizingResult(structure=work, mass_before=before["mass"], mass_after=before["mass"])
    prunes: List[PruneResult] = []
    history: List[SizingStep] = []

    if do_prune:
        for _ in range(rounds):
            pr = prune_members(work, prune_threshold, options=opt)
            prunes.append(pr)
            if not pr.removed:
                break
            work = pr.structure
            sizing = size_members(work, pool, opt)
            history.extend(sizing.history)
            work = sizing.structure

    sizing = size_members(work, pool, opt)
    history.extend(sizing.history)
    work = sizing.structure

    final_analysis = analyse(work)
    final_report = check(work, final_analysis, opt)
    after = {"mass": work.total_mass(), "members": len(work.members),
             "max_ratio": final_report.max_ratio, "max_disp": final_report.max_disp}
    removed = sorted({idx for pr in prunes for idx in pr.removed})
    return OptimizeResult(structure=work, sizing=sizing, prunes=prunes,
                          before=before, after=after, removed=removed,
                          history=history, elapsed=time.time() - t0)


# --------------------------------------------------------------------------
# 冗余度 / 杆件重要度
# --------------------------------------------------------------------------


@dataclass
class MemberRisk:
    member: int
    i: int
    j: int
    section: str
    ratio_before: float
    ratio_after: float
    disp_after: float
    stable: bool
    warning: str

    @property
    def severity(self) -> float:
        """重要度：拆除后最大利用率 / 原最大利用率；失稳记 99。"""
        if not self.stable:
            return 99.0
        return self.ratio_after / max(self.ratio_before, 1e-9)

    @property
    def level(self) -> str:
        if not self.stable:
            return "致命"
        s = self.severity
        if s >= 1.6:
            return "关键"
        if s >= 1.15:
            return "重要"
        if s >= 1.02:
            return "一般"
        return "冗余"

    def to_dict(self) -> Dict[str, object]:
        # 失稳时 ratio_after 是 inf，而 Infinity 不是合法 JSON，这里降为 None
        return {"member": self.member, "i": self.i, "j": self.j,
                "section": self.section, "ratio_before": self.ratio_before,
                "ratio_after": None if not self.stable else self.ratio_after,
                "disp_after": self.disp_after,
                "stable": self.stable, "severity": self.severity,
                "level": self.level, "warning": self.warning}


@dataclass
class RedundancyReport:
    base_ratio: float = 0.0
    base_disp: float = 0.0
    base_mass: float = 0.0
    members: List[MemberRisk] = field(default_factory=list)
    elapsed: float = 0.0
    truncated: bool = False

    @property
    def fatal(self) -> List[MemberRisk]:
        return [m for m in self.members if not m.stable]

    @property
    def critical(self) -> List[MemberRisk]:
        return [m for m in self.members if m.level == "关键"]

    @property
    def degree(self) -> float:
        """冗余度：拆除任意单根杆后仍不超限的比例。"""
        if not self.members:
            return 0.0
        ok = sum(1 for m in self.members if m.stable and m.ratio_after <= 1.0)
        return ok / len(self.members)

    def worst(self, n: int = 5) -> List[MemberRisk]:
        return sorted(self.members, key=lambda m: -m.severity)[:n]


def redundancy_scan(structure: Structure, options: Optional[CheckOptions] = None,
                    limit: int = 400) -> RedundancyReport:
    """逐根拆除扫描：对每根杆做一次"如果它断了"的重分析。

    返回的每一条包含：拆除后结构是否仍稳定、剩余杆件的最大利用率、
    最大挠度，以及由两者共同定义的**重要度**。重要度接近 1 说明这根杆
    基本不参与受力；失稳（s=99）说明它是承重链上的必经之路。
    """
    t0 = time.time()
    opt = options or CheckOptions()
    total = len(structure.members)
    truncated = total > limit
    base_analysis = analyse(structure)
    base_report = check(structure, base_analysis, opt)
    report = RedundancyReport(base_ratio=base_report.max_ratio,
                              base_disp=base_report.max_disp,
                              base_mass=structure.total_mass(),
                              truncated=truncated)

    indices = range(total) if not truncated else range(min(limit, total))
    for idx in indices:
        victim = structure.members[idx]
        trial = structure.copy()
        trial.members = [m for k, m in enumerate(structure.members) if k != idx]
        risk = MemberRisk(member=idx, i=victim.i, j=victim.j,
                          section=victim.section.label,
                          ratio_before=base_report.max_ratio,
                          ratio_after=0.0, disp_after=0.0, stable=True, warning="")
        if len(trial.members) < 2:
            risk.stable = False
            risk.warning = "杆件过少"
            report.members.append(risk)
            continue
        try:
            trial_analysis = analyse(trial)
        except MechanismError:
            risk.stable = False
            risk.ratio_after = float("inf")
            risk.warning = "拆除后成为机构"
            report.members.append(risk)
            continue
        trial_report = check(trial, trial_analysis, opt)
        risk.ratio_after = trial_report.max_ratio
        risk.disp_after = trial_report.max_disp
        if trial_report.max_ratio > 1.0:
            risk.warning = f"剩余杆件超限（{trial_report.max_ratio:.2f}）"
        report.members.append(risk)

    report.elapsed = time.time() - t0
    return report
