"""命令行界面：算例列表、静力分析、优化、重要度扫描、报告导出、启动工作台。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import cases
from .catalog import CATALOG
from .check import CheckOptions, check
from .console import force_utf8
from .model import MATERIALS
from .optimize import optimize, redundancy_scan
from .report import markdown_report, text_report
from .solver import analyse

__all__ = ["main", "build_parser", "BAR"]

BAR = "=" * 78


def _hr(title: str = "") -> str:
    if not title:
        return "-" * 78
    pad = max(0, 76 - len(title))
    return f"-- {title} " + "-" * pad


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------


def cmd_cases(args: argparse.Namespace) -> int:
    from .solver import analyse as _an
    print(BAR)
    print("  LoadPath 内置算例")
    print(BAR)
    print(f"  {'名称':<12}{'说明':<34}{'节点':>4}{'杆件':>5}{'质量/kg':>9}")
    print(_hr())
    for name in cases.names():
        st = cases.get(name)
        label, _, _note = cases.CASES[name]
        print(f"  {name:<12}{label:<34}{len(st.nodes):>4}{len(st.members):>5}"
              f"{st.total_mass():>9.1f}")
    print(_hr())
    print("  用法： python run.py analyze warren")
    return 0


def cmd_materials(args: argparse.Namespace) -> int:
    print(BAR)
    print("  材料（E / fy 单位 MPa，ρ 单位 kg/mm³）")
    print(BAR)
    for m in MATERIALS.values():
        print(f"  {m.name:<9}{m.label:<16}E={m.e:>7.0f}  fy={m.fy:>5.0f}  rho={m.rho:.2e}  {m.note}")
    return 0


def cmd_sections(args: argparse.Namespace) -> int:
    print(BAR)
    print("  截面目录（圆钢管 CHS，单位 mm）")
    print(BAR)
    print(f"  {'名称':<14}{'D':>7}{'t':>6}{'A/mm²':>9}{'I/mm⁴':>12}{'r/mm':>7}{'kg/m':>8}")
    print(_hr())
    for s in CATALOG:
        print(f"  {s.name:<14}{s.d:>7.1f}{s.t:>6.1f}{s.area:>9.1f}{s.inertia:>12.0f}"
              f"{s.radius:>7.1f}{s.area * 7.85e-6 * 1000:>8.1f}")
    return 0


def _options(args: argparse.Namespace) -> CheckOptions:
    return CheckOptions(gamma=args.gamma, deflection_ratio=args.defl)


def cmd_analyze(args: argparse.Namespace) -> int:
    st = cases.get(args.case)
    analysis = analyse(st)
    report = check(st, analysis, _options(args))
    print(text_report(st, analysis, report, limit=args.limit))
    for sol in analysis.solutions:
        print(f"  工况 {sol.case:<10} 最大位移 {sol.max_disp:8.2f} mm（节点 "
              f"{sol.max_disp_node}）  应变能 {sol.energy / 1e6:8.3f} J")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    st = cases.get(args.case)
    result = optimize(st, options=_options(args), rounds=args.rounds,
                      prune_threshold=args.prune, do_prune=not args.no_prune)
    print(BAR)
    print(f"  优化 {args.case}")
    print(BAR)
    print(f"  {'迭代':>4}{'质量/kg':>10}{'最大利用率':>12}{'最大挠度/mm':>13}{'改动杆件':>9}")
    print(_hr())
    for step in result.history:
        print(f"  {step.iteration:>4}{step.mass:>10.1f}{step.max_ratio:>12.3f}"
              f"{step.max_disp:>13.2f}{step.changes:>9}")
    print(_hr())
    print(f"  质量 {result.before['mass']:.1f} → {result.after['mass']:.1f} kg"
          f"（减重 {result.mass_saving * 100:.1f}%）")
    print(f"  杆件 {result.before['members']} → {result.after['members']}"
          f"（剔除 {len(result.removed)} 根）")
    print(f"  利用率 {result.before['max_ratio']:.2f} → {result.after['max_ratio']:.2f}，"
          f"用时 {result.elapsed * 1000:.0f} ms")
    final = check(result.structure, analyse(result.structure), _options(args))
    print(f"  校核：{final.verdict}")
    for issue in final.issues:
        print(f"    · {issue}")
    if args.json:
        payload = {
            "before": result.before, "after": result.after,
            "removed": result.removed,
            "history": [h.__dict__ for h in result.history],
            "model": result.structure.to_dict(),
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"  已写入 {args.json}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    st = cases.get(args.case)
    rr = redundancy_scan(st, _options(args))
    print(BAR)
    print(f"  杆件重要度扫描：{args.case}")
    print(BAR)
    print(f"  整体冗余度 {rr.degree * 100:5.1f}%   "
          f"致命 {len(rr.fatal)} 根 / 共 {len(rr.members)} 根   "
          f"用时 {rr.elapsed * 1000:.0f} ms")
    print(f"  基准：最大利用率 {rr.base_ratio:.3f}，最大挠度 {rr.base_disp:.2f} mm")
    print(_hr())
    print(f"  {'#':>4} {'节点':>8} {'拆除后利用率':>12} {'重要度':>8}  等级   说明")
    print(_hr())
    for m in rr.worst(args.top):
        ratio = "—" if not m.stable else f"{m.ratio_after:.3f}"
        sev = "∞" if not m.stable else f"{m.severity:.2f}"
        print(f"  {m.member:>4} {f'{m.i}-{m.j}':>8} {ratio:>12} {sev:>8}  {m.level:<6}{m.warning}")
    print(_hr())
    print("  说明：重要度 = 拆除后最大利用率 / 原最大利用率；失稳记 ∞。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    st = cases.get(args.case)
    analysis = analyse(st)
    report = check(st, analysis, _options(args))
    result = None
    rr = None
    if args.with_opt:
        result = optimize(st, options=_options(args), rounds=args.rounds,
                          prune_threshold=args.prune)
    if args.with_scan:
        rr = redundancy_scan(st, _options(args))
    md = markdown_report(st, analysis, report, result, rr, limit=args.limit)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"已写入 {args.out}（{len(md.splitlines())} 行）")
    else:
        print(md)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve
    serve(host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loadpath",
        description="LoadPath —— 二维桁架受力分析、截面选型与杆件重要度工作台",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python run.py analyze warren\n"
               "  python run.py optimize mbb --prune 0.2\n"
               "  python run.py scan cantilever\n"
               "  python run.py serve\n",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("cases", help="列出内置算例")
    p.set_defaults(func=cmd_cases)

    p = sub.add_parser("materials", help="列出材料")
    p.set_defaults(func=cmd_materials)

    p = sub.add_parser("sections", help="列出截面目录")
    p.set_defaults(func=cmd_sections)

    def add_case(p):
        p.add_argument("case", choices=cases.names(), help="算例名称")

    def add_common(p):
        p.add_argument("--gamma", type=float, default=1.5, help="安全系数（默认 1.5）")
        p.add_argument("--defl", type=float, default=250.0,
                       help="挠度限值取跨度 / N（默认 250）")

    p = sub.add_parser("analyze", help="静力分析 + 校核")
    add_case(p)
    add_common(p)
    p.add_argument("--limit", type=int, default=15, help="表格列出的杆件数")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("optimize", help="截面选型 + 拓扑精简")
    add_case(p)
    add_common(p)
    p.add_argument("--prune", type=float, default=0.2, help="精简阈值（利用率低于此值才考虑拆除）")
    p.add_argument("--rounds", type=int, default=3, help="精简轮数")
    p.add_argument("--no-prune", action="store_true", help="只选型，不动拓扑")
    p.add_argument("--json", default="", help="把优化结果写入 JSON")
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("scan", help="杆件重要度（逐根拆除）")
    add_case(p)
    add_common(p)
    p.add_argument("--top", type=int, default=12, help="列出最敏感的 N 根")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report", help="导出 Markdown 报告")
    add_case(p)
    add_common(p)
    p.add_argument("--out", default="", help="输出文件；留空则打印到终端")
    p.add_argument("--limit", type=int, default=20, help="表格列出的杆件数")
    p.add_argument("--with-opt", action="store_true", help="附带优化结果")
    p.add_argument("--with-scan", action="store_true", help="附带重要度扫描")
    p.add_argument("--prune", type=float, default=0.2)
    p.add_argument("--rounds", type=int, default=3)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("serve", help="启动浏览器工作台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
