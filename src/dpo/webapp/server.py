"""동적 장애물 편집·경로계획 플랫폼 (로컬 웹 서버, 표준 라이브러리만 사용).

실행:  PYTHONPATH=src python -m dpo.webapp [--port 8765]
브라우저: http://localhost:8765

API
  GET  /api/site                 지도 범위·레이어·거점·출발시각별 풍속 요약
  GET  /basemap.png              지형+건물 배경 이미지
  GET  /api/example              예시 시나리오(E8)
  POST /api/validate {obstacles} 유효성 검사 ("가능한 범위에서만 허용")
  POST /api/plan  {obstacles, hub, dst, dep_hour, payload, kind, round_trip, allow_wait, max_wait, wind, reserve}
"""
from __future__ import annotations

import io
import json
import math
import sys
import threading
import time
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import numpy as np

from .. import config as C
from ..energy import Drone, soc_drop
from ..obstacles import DynObstacle, validate, LIMITS
from ..planner import simulate, path_length, vertical_energy
from ..st_planner import STPlanner, check_path_conflicts
from ..wind_field import Site3D

STATIC = Path(__file__).parent / "static"
T0 = np.datetime64("2026-10-27T00:00")
KST = 9
DEFAULT_HUB = (2700.0, 3000.0)
DEFAULT_DST = (4600.0, 1500.0)
NFZ = (3650.0, 2250.0, 250.0)


class App:
    def __init__(self):
        g = dict(np.load(C.PROC / "site_grid.npz"))
        f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
        self.g = g
        self.site = Site3D(g, f0, T0)
        self.site.add_static_nfz_circle(*NFZ)
        p = C.RES / "drone_params.json"
        dd = json.loads(p.read_text()) if p.exists() else {}
        self.drone = Drone(**{k: v for k, v in dd.items() if k in Drone.__dataclass_fields__})
        self.lock = threading.Lock()
        self._site_lin = None
        self.planners = {}
        self.base_cache = {}
        self.basemap = self._render_basemap()
        # JIT 예열
        t = time.time()
        self.plan(dict(obstacles=[], dep_hour=15, payload=0.5, kind="delivery", round_trip=False))
        print(f"[platform] 준비 완료 ({time.time() - t:.1f}s)")

    def site_lin(self):
        if self._site_lin is None:
            from ..layered import fit_linear_profile, linear_wind_site
            f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
            prof = fit_linear_profile(f0, 0.5 * (C.LAT_MIN + C.LAT_MAX), 0.5 * (C.LON_MIN + C.LON_MAX),
                                      float(self.g["ground"].mean()))
            self._site_lin = linear_wind_site(self.site, prof)
        return self._site_lin

    def _render_basemap(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LightSource, LinearSegmentedColormap
        g = self.g
        ls = LightSource(azdeg=315, altdeg=45)
        rgb = ls.shade(g["ground"], cmap=plt.cm.gist_earth, vert_exag=3, blend_mode="soft", vmin=-50,
                       vmax=g["ground"].max() + 60)
        ny, nx = g["ground"].shape
        fig = plt.figure(figsize=(nx / 50, ny / 50), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
        ax.imshow(rgb, origin="lower", extent=[0, nx, 0, ny], alpha=0.85)
        bh = np.ma.masked_where(g["bldg_h"] <= 0, g["bldg_h"])
        ax.imshow(bh, origin="lower", extent=[0, nx, 0, ny],
                  cmap=LinearSegmentedColormap.from_list("b", ["#C9C9C9", "#2E2E2E"]), vmin=0, vmax=90, alpha=0.9)
        ax.set_xlim(0, nx); ax.set_ylim(0, ny)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()

    # ---------------------------------------------------------------- API
    def site_info(self):
        s = self.site
        ws = []
        for h in range(len(s.t_axis)):
            agl = s.agl
            kk = np.argmin(np.abs(agl - 100.0), axis=0)
            J, I = np.indices(kk.shape)
            u, v = s.U[h][kk, J, I].mean(), s.V[h][kk, J, I].mean()
            ws.append(dict(hour=int(h + KST), speed=float(math.hypot(u, v)),
                           dir_from=float((math.degrees(math.atan2(-u, -v))) % 360)))
        return dict(W=s.nx * s.dx, H=s.ny * s.dx, dx=s.dx, z=[float(z) for z in s.z],
                    ground=[float(s.ground.min()), float(s.ground.max())], hub=DEFAULT_HUB, dst=DEFAULT_DST,
                    nfz=NFZ, wind=ws, limits=LIMITS, drone=dict(name=self.drone.name, battery_wh=self.drone.battery_wh,
                                                                m_empty=self.drone.m_empty),
                    date="2026-10-27 (GFS 00Z 예보)")

    def _parse(self, req):
        obs = [DynObstacle.from_dict(o) for o in req.get("obstacles", [])]
        return validate(obs, self.site)

    def _valid_point(self, xy, name):
        s = self.site
        x, y = float(xy[0]), float(xy[1])
        if not (0 <= x < s.nx * s.dx and 0 <= y < s.ny * s.dx):
            raise ValueError(f"{name} 위치가 지도 밖입니다")
        i, j = int(x // s.dx), int(y // s.dx)
        if not s.free[:, j, i].any():
            raise ValueError(f"{name} 위치 상공에 비행 가능한 고도가 없습니다 (건물/금지구역)")
        return (x, y)

    def plan(self, req):
        with self.lock:
            t_all = time.time()
            obs, errs = self._parse(req)
            hub = self._valid_point(req.get("hub", DEFAULT_HUB), "물류거점")
            dst = self._valid_point(req.get("dst", DEFAULT_DST), "목적지")
            dep_hour = int(req.get("dep_hour", 15))
            if not (KST <= dep_hour <= KST + len(self.site.t_axis) - 2):
                raise ValueError("출발 시각은 9~20시")
            payload = float(req.get("payload", 0.5))
            if not (0 <= payload <= 0.75):
                raise ValueError("탑재중량은 0~0.75 kg (실측 보정 범위)")
            kind = req.get("kind", "delivery")
            reserve = float(req.get("reserve", 20.0))
            max_wait = float(req.get("max_wait", 180.0)) if req.get("allow_wait", True) else 0.0
            wind = "linear" if req.get("wind") == "linear" else "full"
            site = self.site_lin() if wind == "linear" else self.site
            weight = float(req.get("weight", 1.2))
            if not (1.0 <= weight <= 2.0):
                raise ValueError("탐색 가중치는 1.0~2.0")
            d = self.drone
            t_dep = (dep_hour - KST) * 3600.0
            m0 = d.m_empty
            m_out, m_back = (m0 + payload, m0) if kind == "delivery" else (m0, m0 + payload)
            legs_req = [("out", hub, dst, m_out)]
            if req.get("round_trip", True):
                legs_req.append(("back", dst, hub, m_back))
            E_usable = d.battery_wh * 3600 * (1 - reserve / 100.0)
            if wind not in self.planners:
                self.planners[wind] = STPlanner(site, d)
            SP = self.planners[wind]
            SP.set_obstacles(obs, t_dep)
            out = dict(errors=errs, legs=[], baseline=[], obstacles=[o.to_dict() for o in obs], t_dep=t_dep,
                       dep_hour=dep_hour)
            t = t_dep
            e_used = 0.0
            for name, a, b, m in legs_req:
                st, gl = site.lowest_free_k(*a), site.lowest_free_k(*b)
                e_to, t_to = vertical_energy(site, d, a[0], a[1], st[0], t, m, up=True)
                budget = E_usable - e_used - e_to
                r = SP.plan(st, gl, t_start=t + t_to, mass=m, budget=budget, max_wait=max_wait, weight=weight)
                bkey = (wind, name, tuple(a), tuple(b), round(m, 3), round(t + t_to, 1), weight)
                if bkey not in self.base_cache:      # 장애물 무시 기준 경로는 장애물과 무관 → 캐시
                    self.base_cache[bkey] = SP.plan(st, gl, t_start=t + t_to, mass=m, ignore_obstacles=True,
                                                    weight=weight)
                r0 = self.base_cache[bkey]
                leg = dict(name=name, feasible=r is not None)
                if r0 is not None:
                    sim0 = simulate(site, d, r0["path"], r0["wait_before"], t + t_to, m)
                    cf0 = check_path_conflicts(site, sim0, obs, t_dep)
                    out["baseline"].append(dict(name=name, samples=_samples(sim0, t_dep), conflicts=cf0,
                                                E_kJ=float(sim0["e"][-1] / 1000)))
                if r is None:
                    leg["reason"] = ("허용 범위(배터리 예비 SOC·대기시간·대기속도 한계) 안에서 충돌 없는 경로가 없습니다")
                    out["legs"].append(leg)
                    break
                sim = simulate(site, d, r["path"], r["wait_before"], t + t_to, m)
                e_ld, t_ld = vertical_energy(site, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
                E = e_to + sim["e"][-1] + e_ld
                cf = check_path_conflicts(site, sim, obs, t_dep)
                wp = [dict(x=float((p[2] + .5) * site.dx), y=float((p[1] + .5) * site.dx), z=float(site.z[p[0]]),
                           wait=float(w)) for p, w in zip(r["path"], r["wait_before"])]
                leg.update(samples=_samples(sim, t_dep, e_offset=e_used + e_to, drone=d), waypoints=wp,
                           E_kJ=float(E / 1000), soc_leg=float(soc_drop(E, d)), T=float(sim["t"][-1] + t_ld - t),
                           wait=float(r["wait_before"].sum()), L=float(path_length(site, r["path"])),
                           cpu=float(r["cpu_s"]), conflicts=cf, ground_delay=float(r["ground_delay"]))
                out["legs"].append(leg)
                e_used += E
                t = sim["t"][-1] + t_ld + 30.0
            out["soc_used"] = float(soc_drop(e_used, d))
            out["soc_end"] = float(100 - out["soc_used"])
            out["feasible"] = all(l["feasible"] for l in out["legs"]) and out["soc_end"] >= reserve - 1e-6
            out["elapsed"] = time.time() - t_all
            out["table_cells"] = int(SP.table["n_cells"])
            return out


def _samples(sim, t_dep, e_offset=0.0, drone=None, every=2.0):
    keep = [0]
    for q in range(1, len(sim["t"])):
        if sim["t"][q] - sim["t"][keep[-1]] >= every:
            keep.append(q)
    if keep[-1] != len(sim["t"]) - 1:
        keep.append(len(sim["t"]) - 1)
    res = []
    for q in keep:
        row = dict(t=float(sim["t"][q] - t_dep), x=float(sim["x"][q]), y=float(sim["y"][q]), z=float(sim["z"][q]),
                   v=float(sim["vg"][q]), ws=float(sim["ws"][q]))
        if drone is not None:
            row["soc"] = float(100 - soc_drop(sim["e"][q] + e_offset, drone))
        res.append(row)
    return res


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(_jsonable(body), ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write("[platform] " + fmt % args + "\n")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send(200, (STATIC / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            if path == "/basemap.png":
                return self._send(200, app.basemap, "image/png")
            if path == "/api/site":
                return self._send(200, app.site_info())
            if path == "/api/example":
                p = C.ROOT / "scenarios" / "e8_dynamic.json"
                return self._send(200, json.loads(p.read_text(encoding="utf-8")) if p.exists() else dict(obstacles=[]))
            self._send(404, dict(error="not found"))

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/validate":
                    ok, errs = app._parse(req)
                    return self._send(200, dict(valid=[o.to_dict() for o in ok], errors=errs))
                if self.path == "/api/plan":
                    return self._send(200, app.plan(req))
                self._send(404, dict(error="not found"))
            except ValueError as ex:
                self._send(400, dict(error=str(ex)))
            except Exception as ex:  # noqa
                traceback.print_exc()
                self._send(500, dict(error=f"{type(ex).__name__}: {ex}"))
    return H


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    app = App()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    print(f"[platform] http://{a.host}:{a.port}  (Ctrl+C 종료)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
