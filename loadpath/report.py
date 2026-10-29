"""报告：把分析结果渲染成文本表格或 Markdown。

命令行默认输出固定宽度的等宽文本，方便直接贴进终端或邮件；``markdown=True``
时输出可粘进文档的表格。两种格式共用同一份数据结构，不重复计算。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .check import CheckReport
from .model import Structure
from .optimize import OptimizeResult, RedundancyReport
from .solver import Analysis

__all__ = ["text_report", "markdown_report", "summary_line"]

BAR = "─"


def _fmt(v: float, width: int = 9, digits: int = 1) -> str:
    if v != v or v in (float("inf"), float("-inf")):
        return f"{'—':>{width}}"
    return f"{v:>{width}.{digits}f}"


def summary_line(structure: Structure, report: CheckReport) -> str:
    return (f"{structure.name}: {len(structure.nodes)} 节点 / {len(structure.members)} 杆 / "
            f"{structure.total_mass():.1f} kg / 最大利用率 {report.max_ratio:.2f} / "
            f"最大挠度 {report.max_disp:.1f} mm / {report.verdict}")


def _member_rows(structure: Structure, report: CheckReport, limit: Optional[int]) -> List[List[str]]:
    rows: List[List[str]] = []
    members = structure.members
    checks = report.members
    order = sorted(range(len(checks)), key=lambda k: -checks[k].ratio)
    if limit is not None:
        order = order[:limit]
    for k in order:
        c = checks[k]
        m = members[c.member]
        rows.append([
            f"{c.member:>3d}",
            f"{c.i}–{c.j}",
            m.group or "—",
            c.section,
            f"{c.length:>7.0f}",
            f"{c.force / 1000.0:>7.2f}",
            f"{c.slenderness:>5.0f}",
            f"{c.ratio:>5.2f}",
            c.status,
        ])
    return rows


def text_report(structure: Structure, analysis: Analysis, report: CheckReport,
                limit: Optional[int] = 15) -> str:
    width = 78
    lines: List[str] = [BAR * width, f"  {structure.name}  —  {structure.note}", BAR * width]
    lines.append(f"  节点 {len(structure.nodes)}   杆件 {len(structure.members)}   "
                 f"自由度 {analysis.dof}（自由 {analysis.free_dof}）   半带宽 {analysis.bandwidth}")
    lines.append(f"  总长 {structure.total_length() / 1000:.1f} m   质量 {structure.total_mass():.1f} kg   "
                 f"跨度 {structure.span() / 1000:.1f} m   高 {structure.height() / 1000:.1f} m")
    lines.append("")
    header = (f"  {'#':>3} {'节点':>7} {'分组':<6} {'截面':<14} {'L/mm':>7} "
              f"{'N/kN':>7} {'λ':>5} {'利用率':>5}  状态")
    lines.append(header)
    lines.append("  " + BAR * (width - 4))
    for row in _member_rows(structure, report, limit):
        lines.append(f"  {row[0]} {row[1]:>7} {row[2]:<6} {row[3]:<14} {row[4]} "
                     f"{row[5]} {row[6]} {row[7]}  {row[8]}")
    if limit is not None and len(report.members) > limit:
        lines.append(f"  …… 其余 {len(report.members) - limit} 根未列出（按利用率降序取前 {limit}）")
    lines.append("")
    eq = report.equilibrium
    lines.append(f"  最大利用率 {report.max_ratio:.3f}（#{report.critical_member}）   "
                 f"最大挠度 {report.max_disp:.2f} mm / 限值 {report.disp_limit:.2f} mm"
                 f"（跨度/{report.options.deflection_ratio:.0f}）")
    lines.append(f"  平衡残差：力 {eq.get('relative', 0):.1e}  力矩 {eq.get('moment_relative', 0):.1e}")
    lines.append(f"  结论：{report.verdict}")
    for issue in report.issues:
        lines.append(f"    · {issue}")
    lines.append(BAR * width)
    return "\n".join(lines)


def markdown_report(structure: Structure, analysis: Analysis, report: CheckReport,
                    optimize_result: Optional[OptimizeResult] = None,
                    redundancy: Optional[RedundancyReport] = None,
                    limit: int = 20) -> str:
    out: List[str] = []
    out.append(f"# {structure.name}")
    out.append("")
    if structure.note:
        out.append(f"> {structure.note}")
        out.append("")
    out.append("## 概览")
    out.append("")
    out.append("| 指标 | 数值 |")
    out.append("| --- | --- |")
    out.append(f"| 节点 / 杆件 | {len(structure.nodes)} / {len(structure.members)} |")
    out.append(f"| 自由度（自由） | {analysis.dof}（{analysis.free_dof}） |")
    out.append(f"| 半带宽 | {analysis.bandwidth} |")
    out.append(f"| 总长 | {structure.total_length() / 1000:.2f} m |")
    out.append(f"| 质量 | {structure.total_mass():.1f} kg |")
    out.append(f"| 最大利用率 | {report.max_ratio:.3f}（杆 #{report.critical_member}） |")
    out.append(f"| 最大挠度 | {report.max_disp:.2f} mm（限值 {report.disp_limit:.2f}） |")
    out.append(f"| 结论 | **{report.verdict}** |")
    out.append("")

    out.append("## 杆件校核（按利用率降序）")
    out.append("")
    out.append("| # | 节点 | 分组 | 截面 | L/mm | N/kN | λ | 利用率 | 状态 |")
    out.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for row in _member_rows(structure, report, limit):
        out.append("| " + " | ".join(row) + " |")
    if len(report.members) > limit:
        out.append("")
        out.append(f"*其余 {len(report.members) - limit} 根略。*")
    out.append("")

    if optimize_result is not None:
        r = optimize_result
        out.append("## 优化结果")
        out.append("")
        out.append("| 项目 | 优化前 | 优化后 |")
        out.append("| --- | ---: | ---: |")
        out.append(f"| 质量 / kg | {r.before['mass']:.1f} | {r.after['mass']:.1f} |")
        out.append(f"| 杆件数 | {r.before['members']} | {r.after['members']} |")
        out.append(f"| 最大利用率 | {r.before['max_ratio']:.2f} | {r.after['max_ratio']:.2f} |")
        out.append(f"| 最大挠度 / mm | {r.before['max_disp']:.1f} | {r.after['max_disp']:.1f} |")
        out.append("")
        out.append(f"减重 **{r.mass_saving * 100:.1f}%**，剔除 {len(r.removed)} 根杆件，"
                   f"选型迭代 {len(r.history)} 轮。")
        out.append("")

    if redundancy is not None:
        out.append("## 杆件重要度（逐根拆除）")
        out.append("")
        out.append(f"整体冗余度 **{redundancy.degree * 100:.1f}%**："
                   f"{len(redundancy.members)} 根杆中有 {len(redundancy.fatal)} 根一旦失效会导致机构。")
        out.append("")
        out.append("| # | 节点 | 拆除后利用率 | 重要度 | 等级 | 说明 |")
        out.append("| ---: | --- | ---: | ---: | --- | --- |")
        for m in redundancy.worst(10):
            ratio = "—（机构）" if not m.stable else f"{m.ratio_after:.2f}"
            sev = "∞" if not m.stable else f"{m.severity:.2f}"
            out.append(f"| {m.member} | {m.i}–{m.j} | {ratio} | {sev} | {m.level} | {m.warning or '—'} |")
        out.append("")
    return "\n".join(out)
