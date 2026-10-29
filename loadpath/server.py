"""零依赖 HTTP 服务：静态页面 + JSON 分析接口。

接口一览
--------
``GET  /api/cases``                     算例清单
``GET  /api/analyze?case=&defl=&gamma=`` 模型、位移、内力、校核明细
``GET  /api/optimize?case=&prune=``      选型 + 精简结果与迭代历史
``GET  /api/scan?case=``                 杆件重要度（逐根拆除）
``GET  /api/sections``                   截面目录
``POST /api/custom``                     传入自定义模型 JSON 直接分析

返回的所有数值单位：长度 mm、力 N、应力 MPa、质量 kg。
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import posixpath
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from . import cases
from .catalog import CATALOG
from .check import CheckOptions, check
from .console import force_utf8
from .linalg import MechanismError
from .model import MATERIALS, Structure, chs, get_material
from .optimize import optimize, redundancy_scan
from .solver import analyse

__all__ = ["serve", "Handler", "build_payload"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# 数据组装
# --------------------------------------------------------------------------


def _bbox(st: Structure) -> List[float]:
    x0, y0, x1, y1 = st.bounds()
    return [x0, y0, x1, y1]


def _analysis_payload(st: Structure, options: CheckOptions) -> Dict[str, Any]:
    analysis = analyse(st)
    report = check(st, analysis, options)
    return {
        "model": st.to_dict(),
        "bbox": _bbox(st),
        "analysis": {
            "dof": analysis.dof,
            "free": analysis.free_dof,
            "bandwidth": analysis.bandwidth,
            "solutions": [
                {
                    "case": sol.case,
                    "forces": sol.forces,
                    "disp": [[sol.u[2 * i], sol.u[2 * i + 1]] for i in range(len(st.nodes))],
                    "max_disp": sol.max_disp,
                    "max_disp_node": sol.max_disp_node,
                    "energy": sol.energy,
                    "reactions": {str(k): v for k, v in sol.reactions.items()},
                }
                for sol in analysis.solutions
            ],
        },
        "check": {
            "max_ratio": report.max_ratio,
            "critical_member": report.critical_member,
            "max_disp": report.max_disp,
            "max_disp_case": report.max_disp_case,
            "max_disp_node": report.max_disp_node,
            "disp_limit": report.disp_limit,
            "disp_ratio": report.disp_ratio,
            "verdict": report.verdict,
            "issues": report.issues,
            "equilibrium": report.equilibrium,
            "members": [m.to_dict() for m in report.members],
        },
    }


def build_payload(case: str, kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
    options = CheckOptions(gamma=float(params.get("gamma", 1.5)),
                           deflection_ratio=float(params.get("defl", 250.0)))
    st = cases.get(case)

    if kind == "analyze":
        payload = _analysis_payload(st, options)
        payload["case"] = {"name": case, "label": cases.label(case),
                           "note": st.note, "kind": "analyze"}
        return payload

    if kind == "optimize":
        prune = float(params.get("prune", 0.2))
        result = optimize(st, options=options, rounds=int(params.get("rounds", 3)),
                          prune_threshold=prune)
        payload = _analysis_payload(result.structure, options)
        payload["case"] = {"name": case, "label": cases.label(case),
                           "note": st.note, "kind": "optimize"}
        payload["optimize"] = {
            "before": result.before,
            "after": result.after,
            "removed": result.removed,
            "saving": result.mass_saving,
            "elapsed": result.elapsed,
            "history": [h.__dict__ for h in result.history],
            "prune_rounds": [len(p.removed) for p in result.prunes],
        }
        return payload

    if kind == "scan":
        rr = redundancy_scan(st, options)
        payload = _analysis_payload(st, options)
        payload["case"] = {"name": case, "label": cases.label(case),
                           "note": st.note, "kind": "scan"}
        payload["scan"] = {
            "degree": rr.degree,
            "base_ratio": rr.base_ratio,
            "base_disp": rr.base_disp,
            "elapsed": rr.elapsed,
            "fatal": len(rr.fatal),
            "critical": len(rr.critical),
            "members": [m.to_dict() for m in rr.members],
        }
        return payload

    raise ValueError(f"未知类型 {kind}")


def structure_from_json(data: Dict[str, Any]) -> Structure:
    """从 JSON 重建模型：与 :meth:`Structure.to_dict` 对称。"""
    st = Structure(data.get("name", "custom"), data.get("note", ""))
    for n in data.get("nodes", []):
        st.add_node(float(n["x"]), float(n["y"]), n.get("tag", ""))
    by_name = {s.name: s for s in CATALOG}
    for m in data.get("members", []):
        sec_key = m.get("section")
        if sec_key in by_name:
            sec = by_name[sec_key]
        else:
            sp = m.get("d"), m.get("t")
            if sp[0] is None or sp[1] is None:
                raise ValueError(f"杆件截面 {sec_key!r} 未知，且未给出 d/t")
            sec = chs(float(sp[0]), float(sp[1]), sec_key)
        mat = get_material(m.get("material", "q235"))
        st.add_member(int(m["i"]), int(m["j"]), sec, mat,
                      float(m.get("k", 1.0)), m.get("group", ""))
    for s in data.get("supports", []):
        st.add_support(int(s["node"]), bool(s.get("fix_x", True)),
                       bool(s.get("fix_y", True)),
                       float(s.get("dx", 0.0)), float(s.get("dy", 0.0)))
    for c in data.get("cases", []):
        case = st.add_case(c.get("name", "case"), c.get("note", ""))
        for l in c.get("loads", []):
            case.add(int(l["node"]), float(l.get("fx", 0.0)), float(l.get("fy", 0.0)))
    return st


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "LoadPath/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默访问日志
        pass

    # -- 工具 ------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, status: int = 200) -> None:
        # allow_nan=False：Infinity/NaN 不是合法 JSON，宁可在这里炸掉也不要
        # 让浏览器拿到解析失败的响应（曾经真的踩过这个坑）
        self._send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _static(self, rel: str) -> None:
        rel = rel.lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(WEB, rel))
        if not target.startswith(WEB):          # 路径穿越防护
            self._error("forbidden", 403)
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self._error("not found", 404)
            return
        ctype, _ = mimetypes.guess_type(target)
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype or "application/octet-stream")

    # -- 路由 ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = posixpath.normpath(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                self._static("index.html")
                return
            if path == "/api/cases":
                self._json([
                    {
                        "name": name,
                        "label": cases.label(name),
                        "note": cases.CASES[name][2],
                        "nodes": len(cases.get(name).nodes),
                        "members": len(cases.get(name).members),
                        "mass": cases.get(name).total_mass(),
                    }
                    for name in cases.names()
                ])
                return
            if path == "/api/sections":
                self._json([s.to_dict() for s in CATALOG])
                return
            if path == "/api/materials":
                self._json([m.to_dict() for m in MATERIALS.values()])
                return
            if path.startswith("/api/"):
                kind = path[5:]
                if kind not in ("analyze", "optimize", "scan"):
                    self._error("unknown endpoint", 404)
                    return
                case = (query.get("case") or [""])[0]
                if case not in cases.CASES:
                    self._error(f"未知算例 {case!r}", 404)
                    return
                params = {k: v[0] for k, v in query.items() if k != "case"}
                with _LOCK:
                    payload = build_payload(case, kind, params)
                self._json(payload)
                return
            self._static(path)
        except MechanismError as exc:
            self._error(f"结构不稳定：{exc}", 422)
        except (KeyError, ValueError) as exc:
            self._error(str(exc), 400)
        except Exception as exc:                 # noqa: BLE001
            self._error(f"内部错误：{exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/custom":
            self._error("not found", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8"))
            st = structure_from_json(data)
            issues = st.validate()
            if issues:
                self._json({"error": "模型不完整", "issues": issues}, 400)
                return
            options = CheckOptions(gamma=float(data.get("gamma", 1.5)),
                                   deflection_ratio=float(data.get("defl", 250.0)))
            payload = _analysis_payload(st, options)
            payload["case"] = {"name": st.name, "label": "自定义模型",
                               "note": st.note, "kind": "analyze"}
            payload["issues"] = issues
            self._json(payload)
        except MechanismError as exc:
            self._error(f"结构不稳定：{exc}", 422)
        except (KeyError, ValueError, IndexError) as exc:
            self._error(f"模型解析失败：{exc}", 400)


def serve(host: str = "127.0.0.1", port: int = 8790) -> None:
    force_utf8()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"LoadPath 工作台已启动： http://{host}:{port}")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
