"""LoadPath —— 二维桁架受力分析、截面选型与杆件重要度工作台。

零第三方依赖：刚度矩阵组装、带状 LDLᵀ 分解、允许应力法校核、
离散截面选型、拓扑精简、单杆失效扫描，全部用 Python 标准库实现。

典型用法::

    from loadpath import cases
    from loadpath.solver import analyse
    from loadpath.check import check

    st = cases.get("warren")
    rep = check(st, analyse(st))
    print(rep.max_ratio, rep.verdict)
"""

from . import catalog, cases, check, model, optimize, report, solver  # noqa: F401
from .model import Material, Section, Structure, chs, get_material  # noqa: F401
from .solver import Analysis, Solution, System, analyse  # noqa: F401

__version__ = "1.0.0"
__all__ = [
    "catalog", "cases", "check", "model", "optimize", "report", "solver",
    "Material", "Section", "Structure", "chs", "get_material",
    "Analysis", "Solution", "System", "analyse",
    "__version__",
]
