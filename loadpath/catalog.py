"""截面目录：12 种圆钢管（CHS）。

真实工程里杆件必须从可采购的规格里挑，所以选型是**离散**的：给定需求
（轴力、计算长度、材料），在目录里找最轻的一个满足所有工况的截面。
这个函数是整个选型优化的基础。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from .model import Material, Section, chs

__all__ = ["CATALOG", "catalog_names", "get_section", "capacity", "select_section",
           "min_mass_section"]

# (外径 mm, 壁厚 mm) —— 取自常用 CHS 规格序列
_SPECS = [
    (48.3, 3.2), (60.3, 3.6), (76.1, 3.6), (88.9, 4.0),
    (114.3, 4.5), (139.7, 5.0), (168.3, 5.6), (193.7, 6.3),
    (219.1, 7.1), (244.5, 8.0), (273.0, 8.8), (323.9, 10.0),
]

CATALOG: List[Section] = [chs(d, t) for d, t in _SPECS]


def catalog_names() -> List[str]:
    return [s.name for s in CATALOG]


def get_section(key: str) -> Section:
    for s in CATALOG:
        if s.name == key:
            return s
    raise KeyError(f"未知截面 {key!r}，可选：{', '.join(catalog_names())}")


def capacity(material: Material, section: Section, length: float,
             k_factor: float = 1.0, gamma: float = 1.5) -> float:
    """允许轴力 N（拉力为正的幅值），已考虑屈曲。

    允许应力法：拉杆取 ``fy/γ``，压杆取 ``min(fy, σ_cr)/γ``，
    其中欧拉临界应力 ``σ_cr = π²E / λ²``，长细比 ``λ = K·L / r``。
    """
    lam = k_factor * length / section.radius
    sigma_cr = math.pi ** 2 * material.e / (lam * lam) if lam > 0 else float("inf")
    sigma_allow = min(material.fy, sigma_cr) / gamma
    return sigma_allow * section.area


def select_section(material: Material, length: float, demand: float,
                   k_factor: float = 1.0, gamma: float = 1.5,
                   lam_limit: float = 200.0,
                   pool: Optional[Sequence[Section]] = None) -> Section:
    """在目录里挑最轻且满足承载与长细比要求的截面。"""
    pool = pool or CATALOG
    for sec in sorted(pool, key=lambda s: (s.area, s.d)):
        lam = k_factor * length / sec.radius
        if lam > lam_limit and demand > 0:
            continue
        if capacity(material, sec, length, k_factor, gamma) >= demand:
            return sec
    # 全部不够：返回目录里最强的，并让校核去报告超限
    return max(pool, key=lambda s: capacity(material, s, length, k_factor, gamma))


def min_mass_section(material: Material, length: float, demand: float,
                     k_factor: float = 1.0, gamma: float = 1.5,
                     lam_limit: float = 200.0) -> Section:
    """与 :func:`select_section` 同义，语义更明确：按质量最小筛选。"""
    pool = [s for s in CATALOG]
    for sec in sorted(pool, key=lambda s: s.area):
        if k_factor * length / sec.radius > lam_limit and demand > 0:
            continue
        if capacity(material, sec, length, k_factor, gamma) >= demand:
            return sec
    return max(pool, key=lambda s: capacity(material, s, length, k_factor, gamma))
