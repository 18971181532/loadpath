"""LoadPath 单元测试：从线性代数到 HTTP 接口，逐层验证。

物理类测试尽量用**可手算的解析解**做基准（两杆桁架内力、卡氏定理应变能、
Betti 互等定理、E 的缩放关系），而不是"跑一遍看数字变没变"。这样一旦求解器
出了符号或组装错误，测试会立刻失败，而不是安静地给出一个错误的漂亮数字。
"""

from __future__ import annotations

import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from loadpath import cases
from loadpath.catalog import CATALOG, capacity, select_section
from loadpath.check import CheckOptions, check
from loadpath.linalg import BandedSPD, MechanismError, matrix_bandwidth, rcm_order, solve_dense
from loadpath.model import MATERIALS, Structure, chs, get_material
from loadpath.optimize import optimize, prune_members, redundancy_scan, size_members
from loadpath.report import markdown_report, text_report
from loadpath.server import Handler, structure_from_json
from loadpath.solver import System, analyse

STEEL = get_material("q235")
SEC = chs(88.9, 4.0)


def two_bar(pin_both: bool = True) -> Structure:
    """经典两杆桁架：跨度 2000，顶点高 1000，顶点受 10 kN 向下。"""
    st = Structure("two-bar")
    a = st.add_node(0, 0)
    b = st.add_node(2000, 0)
    c = st.add_node(1000, 1000)
    st.add_member(a, c, SEC, STEEL)
    st.add_member(b, c, SEC, STEEL)
    st.add_support(a, True, True)
    st.add_support(b, True, pin_both)
    st.add_case("P").add(c, 0, -10000)
    return st


def panel_truss() -> Structure:
    """单面板桁架：两个自由节点 c、d，便于验证互等定理等"跨节点"性质。"""
    st = Structure("panel")
    a = st.add_node(0, 0)
    b = st.add_node(1000, 0)
    c = st.add_node(1000, 1000)
    d = st.add_node(0, 1000)
    for i, j in ((a, b), (b, c), (c, d), (d, a), (a, c)):
        st.add_member(i, j, SEC, STEEL)
    st.add_support(a, True, True)
    st.add_support(b, False, True)
    st.add_case("P").add(c, 0, -10000)
    return st


def three_bar_indeterminate() -> Structure:
    """三杆超静定：中间竖直杆 + 两根斜杆，中间节点受竖载。"""
    st = Structure("three-bar")
    a = st.add_node(-1000, 0)
    b = st.add_node(1000, 0)
    c = st.add_node(0, 0)
    d = st.add_node(0, 1000)
    for n in (a, b, c):
        st.add_member(n, d, SEC, STEEL)
    for n in (a, b, c):
        st.add_support(n, True, True)
    st.add_case("P").add(d, 0, -30000)
    return st


# --------------------------------------------------------------------------
# 线性代数
# --------------------------------------------------------------------------


class TestLinalg(unittest.TestCase):
    def test_dense_solves_known_system(self):
        a = [[4.0, 1.0], [1.0, 3.0]]
        x = solve_dense(a, [1.0, 2.0])
        self.assertAlmostEqual(a[0][0] * x[0] + a[0][1] * x[1], 1.0, places=12)
        self.assertAlmostEqual(a[1][0] * x[0] + a[1][1] * x[1], 2.0, places=12)

    def test_dense_detects_singular(self):
        with self.assertRaises(MechanismError):
            solve_dense([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])

    def test_banded_matches_dense(self):
        # 构造对角占优的对称正定带状矩阵
        n = 30
        rows = []
        for i in range(n):
            row = {}
            row[i] = 40.0
            if i > 0:
                row[i - 1] = -12.0
            if i > 3:
                row[i - 4] = -3.0
            rows.append(row)
        dense = [[0.0] * n for _ in range(n)]
        for i, row in enumerate(rows):
            for j, v in row.items():
                dense[i][j] = v
                dense[j][i] = v
        rhs = [math.sin(i * 0.7) + 1.0 for i in range(n)]
        want = solve_dense(dense, rhs)
        got = BandedSPD(n, rows).factorize().solve(rhs)
        for a, b in zip(want, got):
            self.assertAlmostEqual(a, b, places=8)

    def test_banded_bandwidth(self):
        rows = [{0: 1.0}, {1: 1.0, 0: 2.0}, {2: 1.0}]
        self.assertEqual(matrix_bandwidth(rows), 1)

    def test_banded_reports_mechanism(self):
        # 奇异矩阵：第三行与第二行线性相关
        rows = [{0: 1.0}, {1: 1.0, 0: 1.0}, {2: 0.0, 1: 1.0}]
        with self.assertRaises(MechanismError):
            BandedSPD(3, rows).factorize()

    def test_rcm_reduces_bandwidth_on_grid(self):
        # 16×4 细长网格按行优先编号，带宽 16；RCM 应压到接近短边
        w, h = 16, 4
        idx = lambda i, j: j * w + i
        adj = [[] for _ in range(w * h)]
        for j in range(h):
            for i in range(w):
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nj = i + di, j + dj
                    if 0 <= ni < w and 0 <= nj < h:
                        adj[idx(i, j)].append(idx(ni, nj))
        before = max(abs(v - u) for u in range(len(adj)) for v in adj[u])
        order = rcm_order(adj)
        pos = {old: new for new, old in enumerate(order)}
        after = max(abs(pos[v] - pos[u]) for u in range(len(adj)) for v in adj[u])
        self.assertEqual(sorted(order), list(range(w * h)))
        self.assertLess(after, before)

    def test_rcm_covers_disconnected_nodes(self):
        adj = [[1], [0], [], [4], [3], []]
        self.assertEqual(sorted(rcm_order(adj)), [0, 1, 2, 3, 4, 5])


# --------------------------------------------------------------------------
# 截面与材料
# --------------------------------------------------------------------------


class TestSection(unittest.TestCase):
    def test_chs_properties(self):
        s = chs(100.0, 10.0)
        self.assertAlmostEqual(s.di, 80.0)
        self.assertAlmostEqual(s.area, math.pi / 4 * (100 ** 2 - 80 ** 2), places=9)
        self.assertAlmostEqual(s.inertia, math.pi / 64 * (100 ** 4 - 80 ** 4), places=6)
        self.assertAlmostEqual(s.radius, math.sqrt(s.inertia / s.area), places=9)
        self.assertAlmostEqual(s.radius, 32.02, places=2)

    def test_mass_per_length(self):
        s = chs(100.0, 10.0)
        # 28.27 cm² × 7.85 g/cm³ = 22.2 kg/m
        self.assertAlmostEqual(s.mass_per_length(7.85e-6) * 1000, 22.2, places=1)

    def test_catalog_is_sorted_by_area(self):
        areas = [s.area for s in CATALOG]
        self.assertEqual(areas, sorted(areas))
        self.assertEqual(len(CATALOG), 12)

    def test_capacity_drops_for_slender_bar(self):
        short = capacity(STEEL, SEC, 1000.0)
        long = capacity(STEEL, SEC, 8000.0)
        self.assertLess(long, short)

    def test_capacity_never_exceeds_yield(self):
        for s in CATALOG:
            cap = capacity(STEEL, s, 500.0)
            self.assertLessEqual(cap, STEEL.fy * s.area / 1.5 + 1e-6)

    def test_select_section_picks_lightest_adequate(self):
        chosen = select_section(STEEL, 2000.0, 50000.0)
        self.assertGreaterEqual(capacity(STEEL, chosen, 2000.0), 50000.0)
        lighter = [s for s in CATALOG if s.area < chosen.area]
        for s in lighter:
            self.assertLess(capacity(STEEL, s, 2000.0), 50000.0)

    def test_unknown_material_raises(self):
        with self.assertRaises(KeyError):
            get_material("unobtainium")


# --------------------------------------------------------------------------
# 求解器（物理正确性）
# --------------------------------------------------------------------------


class TestSolver(unittest.TestCase):
    def test_two_bar_forces_match_analytic(self):
        st = two_bar()
        sol = analyse(st).solutions[0]
        expected = -10000.0 / (2 * math.sin(math.pi / 4))
        for f in sol.forces:
            self.assertAlmostEqual(f, expected, places=6)

    def test_two_bar_reactions(self):
        st = two_bar()
        sol = analyse(st).solutions[0]
        self.assertAlmostEqual(sol.reactions[0], 5000.0, places=6)
        self.assertAlmostEqual(sol.reactions[1], 5000.0, places=6)
        self.assertAlmostEqual(sol.reactions[2], -5000.0, places=6)
        self.assertAlmostEqual(sol.reactions[3], 5000.0, places=6)

    def test_two_bar_displacement(self):
        st = two_bar()
        sol = analyse(st).solutions[0]
        # δ = N·L/(E·A·sin45)
        length = math.hypot(1000.0, 1000.0)
        n = 10000.0 / (2 * math.sin(math.pi / 4))
        expected = n * length / (STEEL.e * SEC.area * math.sin(math.pi / 4))
        self.assertAlmostEqual(sol.u[5], -expected, places=8)
        self.assertAlmostEqual(sol.u[4], 0.0, places=9)

    def test_strain_energy_consistency(self):
        st = two_bar()
        sol = analyse(st).solutions[0]
        manual = sum(f * f * st.length(m) / (2 * m.section.area * m.material.e)
                     for f, m in zip(sol.forces, st.members))
        self.assertAlmostEqual(sol.energy, manual, places=6)

    def test_betti_reciprocity(self):
        """F₁·u₂₁ = F₂·u₁₂：线性结构的基本互等定理。

        两个荷载必须作用在**不同节点**上，否则在双杆桁架里会因为正交而恒为 0，
        测不出任何东西。
        """
        st = panel_truss()
        sysm = System(st)
        case1 = st.cases[0]                       # 节点 c 竖向
        case2 = st.add_case("Q").add(3, 7000.0, 0.0)   # 节点 d 水平
        u1 = sysm.solve(case1).u
        u2 = sysm.solve(case2).u
        f1 = st.load_vector(case1)
        f2 = st.load_vector(case2)
        work12 = sum(a * b for a, b in zip(f1, u2))
        work21 = sum(a * b for a, b in zip(f2, u1))
        self.assertGreater(abs(work12), 1.0)      # 确认不是平凡相等
        self.assertAlmostEqual(work12, work21, places=4)

    def test_linear_superposition(self):
        st = two_bar()
        st.cases.clear()
        c1 = st.add_case("P").add(2, 0, -10000)
        c2 = st.add_case("Q").add(2, 4000, 0)
        sysm = System(st)
        u1 = sysm.solve(c1).u
        u2 = sysm.solve(c2).u
        combo = st.add_case("P+Q").add(2, 4000, -10000)
        u12 = sysm.solve(combo).u
        for a, b, c in zip(u1, u2, u12):
            self.assertAlmostEqual(a + b, c, places=9)

    def test_doubling_e_reduces_displacement(self):
        st = two_bar()
        u0 = analyse(st).solutions[0].u[5]
        st2 = two_bar()
        stiff = get_material("q235")
        from loadpath.model import Material
        stiffer = Material("x2", "double", stiff.e * 2, stiff.fy, stiff.rho)
        for m in st2.members:
            m.material = stiffer
        sol2 = analyse(st2).solutions[0]
        self.assertAlmostEqual(sol2.u[5], u0 / 2, places=8)
        # 静定结构内力与刚度无关
        self.assertAlmostEqual(sol2.forces[0], -10000.0 / (2 * math.sin(math.pi / 4)), places=6)

    def test_indeterminate_force_equilibrium(self):
        st = three_bar_indeterminate()
        sol = analyse(st).solutions[0]
        # 竖向平衡：中间杆 + 两根斜杆的竖向分量 = 30 kN
        sin45 = math.sin(math.pi / 4)
        total = sol.forces[2] + (sol.forces[0] + sol.forces[1]) * sin45
        self.assertAlmostEqual(total, -30000.0, places=6)
        # 对称：两根斜杆内力相等
        self.assertAlmostEqual(sol.forces[0], sol.forces[1], places=9)

    def test_symmetry_of_symmetric_structure(self):
        st = three_bar_indeterminate()
        sol = analyse(st).solutions[0]
        self.assertAlmostEqual(sol.u[2 * 3], 0.0, places=9)

    def test_prescribed_settlement(self):
        """支座沉降在超静定结构里会产生自内力（静定结构则不会）。"""
        st = three_bar_indeterminate()
        st.cases[0].loads.clear()               # 卸载，只剩沉降
        st.supports[0].dy = -2.0
        sol = analyse(st).solutions[0]
        self.assertAlmostEqual(sol.u[1], -2.0, places=12)
        self.assertGreater(max(abs(f) for f in sol.forces), 100.0)
        # 无外载时反力自平衡
        rx = sum(v for d, v in sol.reactions.items() if d % 2 == 0)
        ry = sum(v for d, v in sol.reactions.items() if d % 2 == 1)
        self.assertAlmostEqual(rx, 0.0, places=6)
        self.assertAlmostEqual(ry, 0.0, places=6)

    def test_determinate_truss_forces_ignore_settlement(self):
        """两杆桁架水平平衡强制两根杆内力相等，与支座位移无关。"""
        st = two_bar()
        base = analyse(st).solutions[0].forces
        st.supports[0].dy = -2.0
        moved = analyse(st).solutions[0]
        self.assertAlmostEqual(moved.forces[0], moved.forces[1], places=6)
        self.assertAlmostEqual(moved.forces[0], base[0], places=6)

    def test_missing_support_raises(self):
        st = two_bar()
        st.supports.pop()
        with self.assertRaises(MechanismError):
            analyse(st)

    def test_mechanism_detected_for_open_triangle(self):
        """两根杆 + 一端滚动支座 = 四连杆机构，必须被判出来。"""
        st = Structure("mech")
        a = st.add_node(0, 0)
        b = st.add_node(2000, 0)
        c = st.add_node(1000, 1000)
        st.add_member(a, c, SEC, STEEL)
        st.add_member(b, c, SEC, STEEL)
        st.add_support(a, True, True)
        st.add_support(b, False, True)
        st.add_case("P").add(c, 0, -1000)
        with self.assertRaises(MechanismError):
            analyse(st)

    def test_bandwidth_is_reduced_by_rcm(self):
        st = cases.get("warren")
        wide = System(st, reorder=False).bandwidth
        narrow = System(st, reorder=True).bandwidth
        self.assertLessEqual(narrow, wide)


# --------------------------------------------------------------------------
# 校核
# --------------------------------------------------------------------------


class TestCheck(unittest.TestCase):
    def test_utilisation_matches_hand_calc(self):
        st = two_bar()
        rep = check(st, analyse(st))
        m = rep.members[0]
        n = abs(m.force)
        lam = st.length(st.members[0]) / SEC.radius
        sigma_cr = math.pi ** 2 * STEEL.e / lam ** 2
        allow = min(STEEL.fy, sigma_cr) / 1.5 * SEC.area
        self.assertAlmostEqual(m.capacity, allow, places=6)
        self.assertAlmostEqual(m.strength_ratio, n / allow, places=9)
        # 控制利用率还要和长细比取大者
        self.assertAlmostEqual(m.ratio, max(n / allow, m.lam_ratio), places=9)

    def test_compression_flagged_as_buckling_when_slender(self):
        st = Structure("slender")
        a = st.add_node(0, 0)
        b = st.add_node(0, 6000)
        st.add_member(a, b, chs(48.3, 3.2), STEEL)
        st.add_support(a, True, True)
        st.add_support(b, True, False)          # 只约束水平，成为轴向受力杆
        st.add_case("P").add(b, 0, -1000)
        rep = check(st, analyse(st))
        self.assertTrue(rep.members[0].buckling)
        self.assertEqual(rep.members[0].force < 0, True)

    def test_deflection_limit_flagged(self):
        st = two_bar()
        rep = check(st, analyse(st), CheckOptions(deflection_ratio=100000))
        self.assertTrue(any("挠度" in i for i in rep.issues))

    def test_equilibrium_residual_is_tiny(self):
        st = cases.get("pratt")
        rep = check(st, analyse(st))
        self.assertLess(rep.equilibrium["relative"], 1e-9)
        self.assertLess(rep.equilibrium["moment_relative"], 1e-9)

    def test_verdict_text(self):
        st = cases.get("warren")
        rep = check(st, analyse(st))
        self.assertEqual(rep.verdict, "通过")
        self.assertTrue(rep.ok)


# --------------------------------------------------------------------------
# 优化与重要度
# --------------------------------------------------------------------------


class TestOptimize(unittest.TestCase):
    def test_sizing_reduces_mass_and_stays_feasible(self):
        st = cases.get("warren")
        res = size_members(st)
        self.assertLess(res.mass_after, res.mass_before)
        rep = check(res.structure, analyse(res.structure))
        self.assertLessEqual(rep.max_ratio, 1.0 + 1e-9)

    def test_sizing_does_not_mutate_original(self):
        st = cases.get("warren")
        before = st.total_mass()
        size_members(st)
        self.assertAlmostEqual(st.total_mass(), before, places=9)

    def test_worst_mass_saving_is_meaningful(self):
        res = optimize(cases.get("pratt"))
        self.assertGreater(res.mass_saving, 0.4)
        self.assertLess(res.mass_saving, 0.95)

    def test_optimize_output_is_stable_and_feasible(self):
        for name in ("warren", "cantilever", "tower", "mbb"):
            res = optimize(cases.get(name), prune_threshold=0.2)
            rep = check(res.structure, analyse(res.structure))
            self.assertLessEqual(rep.max_ratio, 1.0 + 1e-9, name)
            self.assertGreater(len(res.structure.members), 2)

    def test_prune_never_creates_mechanism(self):
        res = prune_members(cases.get("mbb"), threshold=0.2)
        self.assertGreater(len(res.removed), 0)
        analyse(res.structure)          # 不抛异常即稳定
        self.assertLess(len(res.structure.members), 56)

    def test_redundancy_determinate_truss_is_zero(self):
        rr = redundancy_scan(cases.get("warren"))
        self.assertEqual(len(rr.fatal), len(rr.members))
        self.assertAlmostEqual(rr.degree, 0.0)

    def test_redundancy_indeterminate_has_safe_members(self):
        rr = redundancy_scan(cases.get("tower"))
        self.assertGreater(rr.degree, 0.0)
        self.assertLess(rr.degree, 1.0)
        self.assertTrue(any(m.stable for m in rr.members))

    def test_redundancy_json_is_strict(self):
        """regression：失稳杆的 ratio_after 曾是 inf，会让 JSON 变成非法。"""
        rr = redundancy_scan(cases.get("cantilever"))
        payload = json.dumps([m.to_dict() for m in rr.members],
                             ensure_ascii=False, allow_nan=False)
        self.assertIn("null", payload)
        back = json.loads(payload)
        self.assertTrue(any(m["ratio_after"] is None for m in back))

    def test_severity_and_level(self):
        rr = redundancy_scan(cases.get("tower"))
        for m in rr.members:
            if not m.stable:
                self.assertEqual(m.level, "致命")
                self.assertAlmostEqual(m.severity, 99.0)
            else:
                self.assertAlmostEqual(m.severity, m.ratio_after / rr.base_ratio, places=9)


# --------------------------------------------------------------------------
# 内置算例
# --------------------------------------------------------------------------


class TestCases(unittest.TestCase):
    def test_names(self):
        self.assertEqual(cases.names(), ["warren", "pratt", "cantilever", "tower", "mbb"])

    def test_all_cases_are_valid_and_solvable(self):
        for name in cases.names():
            st = cases.get(name)
            self.assertEqual(st.validate(), [], name)
            rep = check(st, analyse(st))
            self.assertEqual(rep.verdict, "通过", f"{name}: {rep.issues}")

    def test_all_cases_statistically_stable(self):
        for name in cases.names():
            st = cases.get(name)
            r = sum(int(s.fix_x) + int(s.fix_y) for s in st.supports)
            self.assertGreaterEqual(len(st.members) + r, 2 * len(st.nodes), name)

    def test_cases_are_independent_instances(self):
        a = cases.get("warren")
        b = cases.get("warren")
        a.members[0].section = chs(300.0, 20.0)
        self.assertNotEqual(a.members[0].section.name, b.members[0].section.name)

    def test_unknown_case_raises(self):
        with self.assertRaises(KeyError):
            cases.get("nope")


# --------------------------------------------------------------------------
# 序列化 / 报告 / 接口
# --------------------------------------------------------------------------


class TestRoundtrip(unittest.TestCase):
    def test_model_roundtrip(self):
        st = cases.get("cantilever")
        data = st.to_dict()
        back = structure_from_json(data)
        self.assertEqual(len(back.nodes), len(st.nodes))
        self.assertEqual(len(back.members), len(st.members))
        self.assertAlmostEqual(back.total_mass(), st.total_mass(), places=6)
        sol_a = analyse(st).solutions[0]
        sol_b = analyse(back).solutions[0]
        for a, b in zip(sol_a.forces, sol_b.forces):
            self.assertAlmostEqual(a, b, places=6)

    def test_custom_section_roundtrip(self):
        data = {
            "name": "custom",
            "nodes": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}, {"x": 500, "y": 800}],
            "members": [
                {"i": 0, "j": 2, "section": "mychs", "d": 100, "t": 8, "material": "q355"},
                {"i": 1, "j": 2, "section": "mychs", "d": 100, "t": 8, "material": "q355"},
            ],
            "supports": [{"node": 0}, {"node": 1}],
            "cases": [{"name": "P", "loads": [{"node": 2, "fy": -5000}]}],
        }
        st = structure_from_json(data)
        self.assertAlmostEqual(st.members[0].section.area, chs(100, 8).area, places=9)
        self.assertEqual(st.validate(), [])

    def test_validate_catches_problems(self):
        st = Structure("bad")
        a = st.add_node(0, 0)
        b = st.add_node(1000, 0)
        st.add_member(a, b, SEC, STEEL)
        st.add_node(5000, 5000)             # 孤立节点
        issues = st.validate()
        self.assertTrue(any("孤立" in i for i in issues))
        self.assertTrue(any("支座" in i or "约束" in i for i in issues))

    def test_text_and_markdown_reports(self):
        st = cases.get("warren")
        an = analyse(st)
        rep = check(st, an)
        self.assertIn("最大利用率", text_report(st, an, rep))
        md = markdown_report(st, an, rep)
        self.assertTrue(md.startswith("# warren"))
        self.assertIn("| ---", md)


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, r.read()

    def test_cases_endpoint(self):
        status, body = self.get("/api/cases")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data), 5)
        self.assertIn("mass", data[0])

    def test_analyze_endpoint_is_strict_json(self):
        _, body = self.get("/api/analyze?case=warren")
        data = json.loads(body)                 # 严格解析，Infinity 会失败
        self.assertIn("model", data)
        self.assertIn("check", data)
        self.assertEqual(len(data["analysis"]["solutions"]), 1)

    def test_scan_endpoint_is_strict_json(self):
        _, body = self.get("/api/scan?case=cantilever")
        data = json.loads(body)
        self.assertIn("scan", data)
        self.assertAlmostEqual(data["scan"]["degree"], data["scan"]["degree"])

    def test_optimize_endpoint(self):
        _, body = self.get("/api/optimize?case=mbb&prune=0.2")
        data = json.loads(body)
        self.assertLess(data["optimize"]["after"]["mass"], data["optimize"]["before"]["mass"])

    def test_unknown_case_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/analyze?case=nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_path_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../loadpath/model.py")
        self.assertIn(ctx.exception.code, (403, 404))

    def test_index_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"LoadPath", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
