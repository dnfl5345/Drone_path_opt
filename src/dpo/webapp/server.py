"""동적 장애물 편집·경로계획 플랫폼 (로컬 웹 서버, 표준 라이브러리만 사용).

실행:  PYTHONPATH=src python -m dpo.webapp [--port 8765]
브라우저: http://localhost:8765

API
  GET  /api/site                 지도 범위·레이어·거점·출발시각별 풍속 요약
  GET  /basemap.png              지형+건물 배경 이미지
  GET  /api/example              예시 시나리오(E8)
  POST /api/validate {obstacles} 유효성 검사 ("가능한 범위에서만 허용")
  POST /api/plan  {obstacles, hub, dst, dep_hour, payload, kind, round_trip, allow_wait, max_wait, wind, reserve}
  POST /api/replan {obstacles, t_now}   비행 중(t_now) 장애물 변경 발표 → 남은 경로 검사 → 충돌 시 재계획
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
                    date="2026-10-27 (GFS 00Z 예보)", adsb=ADSB)

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

    # ---------------------------------------------------------------- 계획 (세션 저장)
    def _ctx(self, req):
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
        eps = req.get("eps")
        eps = float(eps) if eps not in (None, "", 0, "0") else None
        if eps is not None and not (1e-4 <= eps <= 0.5):
            raise ValueError("위험 한도 ε 는 0.0001~0.5")
        shape = "adsb" if req.get("tube_shape") == "adsb" else "gauss"
        if shape == "adsb" and eps is not None:
            if ADSB is None:
                raise ValueError("실측 ADS-B 오차 모델(results/adsb_error_model.json)이 없습니다")
            if str(eps) not in ADSB["q"]:
                raise ValueError("실측 분위수 모양은 ε = " + " / ".join(ADSB["q"]) + " 만 지원")
        d = self.drone
        m0 = d.m_empty
        m_out, m_back = (m0 + payload, m0) if kind == "delivery" else (m0, m0 + payload)
        legs_req = [("out", hub, dst, m_out)]
        if req.get("round_trip", True):
            legs_req.append(("back", dst, hub, m_back))
        if wind not in self.planners:
            self.planners[wind] = STPlanner(site, d)
        return dict(site=site, wind=wind, d=d, t_dep=(dep_hour - KST) * 3600.0, dep_hour=dep_hour, legs_req=legs_req,
                    E_usable=d.battery_wh * 3600 * (1 - reserve / 100.0), reserve=reserve, max_wait=max_wait,
                    weight=weight, eps=eps, shape=shape, SP=self.planners[wind])

    def _fly_legs(self, cx, legs_req, t, e_used, first=None, reuse=None):
        """legs_req 를 순서대로 계획·시뮬레이션. first=(시작노드, 시작시각, 실행된 앞부분 sim, 이륙에너지, 이륙시간)
        이면 첫 구간은 비행 중 재계획 (실행된 부분 뒤에 이어 붙임)."""
        site, d, SP = cx["site"], cx["d"], cx["SP"]
        legs = []
        for q, (name, a, b, m) in enumerate(legs_req):
            st, gl = site.lowest_free_k(*a), site.lowest_free_k(*b)
            if q == 0 and first is not None:
                start, t0, prefix, e_to, t_to = first
                e_pre = float(prefix["e"][-1]) if len(prefix["e"]) else 0.0
            else:
                e_to, t_to = vertical_energy(site, d, a[0], a[1], st[0], t, m, up=True)
                start, t0, prefix, e_pre = st, t + t_to, None, 0.0
            budget = cx["E_usable"] - e_used - e_to - e_pre
            r = None
            if reuse and name in reuse and reuse[name] is not None and prefix is None:
                # 뒤 구간: 출발 시각만 바뀐 기존 경로를 새 장애물로 재검사 → 충돌 없고 예산 안이면 탐색 없이 재사용
                from ..st_rolling import remaining_conflict
                ro = reuse[name]
                sim_try = simulate(site, d, ro["path"], ro["wait_before"], t0, m)
                if remaining_conflict(site, SP.table, sim_try, t0) is None and sim_try["e"][-1] <= budget:
                    r = dict(ro, cpu_s=0.0, reused=True)
            if r is None:
                r = SP.plan(start, gl, t_start=t0, mass=m, budget=budget, max_wait=cx["max_wait"], weight=cx["weight"])
            # t_leg0: 이 구간의 이륙 시작 시각 (재계획 구간은 호출 측에서 원래 값으로 덮어씀)
            leg = dict(name=name, a=a, b=b, m=m, st=st, gl=gl, e_to=e_to, t_to=t_to, e_before=e_used,
                       t_leg0=t0 - t_to, r=r)
            if r is None:
                leg["feasible"] = False
                legs.append(leg)
                break
            sim = simulate(site, d, r["path"], r["wait_before"], t0 + r.get("ground_delay", 0.0), m)
            if prefix is not None and len(prefix["t"]):
                sim = {k: np.concatenate([prefix[k], sim[k] + (e_pre if k == "e" else 0.0)]) for k in sim}
            e_ld, t_ld = vertical_energy(site, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
            leg.update(feasible=True, sim=sim, e_ld=e_ld, t_ld=t_ld, E=e_to + float(sim["e"][-1]) + e_ld)
            legs.append(leg)
            e_used += leg["E"]
            t = sim["t"][-1] + t_ld + 30.0
        return legs

    def _render(self, cx, legs, obs, errs, extra=None, base_sims=None):
        """base_sims: 비교용 '장애물 무시' 경로 시뮬레이션 [(name, sim0)] – 재계획 시에는 최초 계획 것을 재사용."""
        site, d, t_dep = cx["site"], cx["d"], cx["t_dep"]
        out = dict(errors=errs, legs=[], baseline=[], obstacles=[o.to_dict() for o in obs], t_dep=t_dep,
                   dep_hour=cx["dep_hour"])
        e_used = 0.0
        if base_sims is None:
            base_sims = []
            for L in legs:
                name, a, b, m, st, gl = L["name"], L["a"], L["b"], L["m"], L["st"], L["gl"]
                t_c0 = L["t_leg0"] + L["t_to"]
                bkey = (cx["wind"], name, tuple(a), tuple(b), round(m, 3), round(t_c0, 1), cx["weight"])
                if bkey not in self.base_cache:      # 장애물 무시 기준 경로는 장애물과 무관 → 캐시
                    self.base_cache[bkey] = cx["SP"].plan(st, gl, t_start=t_c0, mass=m, ignore_obstacles=True,
                                                          weight=cx["weight"])
                r0 = self.base_cache[bkey]
                if r0 is not None:
                    base_sims.append((name, simulate(site, d, r0["path"], r0["wait_before"], t_c0, m)))
        self._last_base = base_sims
        for name, sim0 in base_sims:
            out["baseline"].append(dict(name=name, samples=_samples(sim0, t_dep),
                                        conflicts=check_path_conflicts(site, sim0, obs, t_dep),
                                        E_kJ=float(sim0["e"][-1] / 1000)))
        for L in legs:
            name = L["name"]
            leg = dict(name=name, feasible=L["feasible"])
            if not L["feasible"]:
                leg["reason"] = "허용 범위(배터리 예비 SOC·대기시간·대기속도 한계) 안에서 충돌 없는 경로가 없습니다"
                out["legs"].append(leg)
                break
            r, sim = L["r"], L["sim"]
            wp = [dict(x=float((p[2] + .5) * site.dx), y=float((p[1] + .5) * site.dx), z=float(site.z[p[0]]),
                       wait=float(w)) for p, w in zip(r["path"], r["wait_before"])]
            leg.update(samples=_samples(sim, t_dep, e_offset=e_used + L["e_to"], drone=d), waypoints=wp,
                       E_kJ=float(L["E"] / 1000), soc_leg=float(soc_drop(L["E"], d)),
                       T=float(sim["t"][-1] + L["t_ld"] - L["t_leg0"]), wait=float(r["wait_before"].sum()),
                       L=float(np.sum(np.hypot(np.diff(sim["x"]), np.diff(sim["y"])))), cpu=float(r["cpu_s"]),
                       conflicts=check_path_conflicts(site, sim, obs, t_dep), ground_delay=float(r["ground_delay"]))
            out["legs"].append(leg)
            e_used += L["E"]
        out["soc_used"] = float(soc_drop(e_used, d))
        out["soc_end"] = float(100 - out["soc_used"])
        out["feasible"] = all(l["feasible"] for l in out["legs"]) and len(out["legs"]) == len(cx["legs_req"]) \
            and out["soc_end"] >= cx["reserve"] - 1e-6
        out["table_cells"] = int(cx["SP"].table["n_cells"])
        if extra:
            out.update(extra)
        return out

    def plan(self, req):
        with self.lock:
            t_all = time.time()
            obs, errs = self._parse(req)
            cx = self._ctx(req)
            apply_shape(obs, cx)
            cx["SP"].set_obstacles(obs, cx["t_dep"], eps=cx["eps"])
            legs = self._fly_legs(cx, cx["legs_req"], cx["t_dep"], 0.0)
            out = self._render(cx, legs, obs, errs, dict(revisions=[dict(t=0.0, obstacles=[o.to_dict() for o in obs])],
                                                         replans=[]))
            self.session = dict(req=req, cx=cx, legs=legs, revisions=[(0.0, obs)], replans=[],
                                base_sims=self._last_base)
            out["elapsed"] = time.time() - t_all
            return out

    # ---------------------------------------------------------------- 비행 중 장애물 변경 → 재계획
    def replan(self, req, latency=3.0):
        """t_now(출발 기준 s)에 새 장애물 집합이 발표됨 → 남은 경로 검사 → 충돌 시에만 재계획."""
        from ..obstacles import build_interval_table
        from ..st_rolling import remaining_conflict, _snap, _cut
        with self.lock:
            t_all = time.time()
            ss = getattr(self, "session", None)
            if ss is None:
                raise ValueError("먼저 '경로 계획 실행'으로 초기 계획을 만드세요")
            cx = ss["cx"]
            site, t_dep = cx["site"], cx["t_dep"]
            t_now = float(req.get("t_now", 0.0))
            if t_now < ss["revisions"][-1][0]:
                raise ValueError(f"변경 시각은 직전 변경(t={ss['revisions'][-1][0]:.0f} s) 이후여야 합니다")
            obs, errs = self._parse(req)
            for o in obs:                       # 발표 시점에 새로 관측 → 불확실성은 이 시각부터 증가
                o.t_obs = max(o.t_obs, t_now)
            apply_shape(obs, cx)
            ta = t_dep + t_now
            legs = ss["legs"]
            # 활성 구간 찾기
            act = None
            for q, L in enumerate(legs):
                if L.get("feasible") and L["t_leg0"] <= ta <= L["sim"]["t"][-1] + L["t_ld"]:
                    act = q
                    break
            if act is None:
                if ta < legs[0]["t_leg0"] + 1e-6:
                    act = 0
                else:
                    raise ValueError("해당 시각에는 비행 중인 구간이 없습니다 (이미 도착)")
            tc = time.perf_counter()
            table = build_interval_table(site, obs, t_offset=t_dep, eps=cx["eps"])
            hits = []
            for L in legs[act:]:
                if L.get("feasible"):
                    h = remaining_conflict(site, table, L["sim"], ta + latency)
                    if h is not None:
                        hits.append((L["name"], h - t_dep))
            check_ms = (time.perf_counter() - tc) * 1000
            ev = dict(t=t_now, check_ms=check_ms, conflict_at=hits[0][1] if hits else None,
                      conflict_leg=hits[0][0] if hits else None, replanned=False)
            ss["revisions"].append((t_now, obs))
            cx["SP"].set_obstacles(obs, t_dep, eps=cx["eps"])
            if not hits:
                ev["reason"] = "남은 경로와 충돌 없음 → 기존 경로 유지"
            else:
                L = legs[act]
                t_sw = ta + latency
                sim = L["sim"]
                if t_sw <= sim["t"][0]:            # 아직 순항 전(이륙 중) → 해당 구간 처음부터
                    first = None
                    t_leg_start = L["t_leg0"]
                else:
                    qi = int(np.searchsorted(sim["t"], t_sw))
                    qi = min(qi, len(sim["t"]) - 1)
                    t_sw = float(sim["t"][qi])
                    cur = _snap(site, table, sim["x"][qi], sim["y"][qi], sim["z"][qi], t_sw)
                    if cur is None:
                        raise ValueError("현재 위치가 이미 장애물 위험구간 안입니다 – 재계획 불가")
                    first = (cur, t_sw, _cut(sim, t_sw), L["e_to"], L["t_to"])
                    t_leg_start = L["t_leg0"]
                t0 = time.perf_counter()
                new = self._fly_legs(cx, cx["legs_req"][act:], t_leg_start, L["e_before"], first=first,
                                     reuse={X["name"]: X.get("r") for X in legs[act + 1:]})
                if first is not None and new:
                    new[0]["t_leg0"] = L["t_leg0"]
                ev.update(cpu=time.perf_counter() - t0, t_switch=t_sw - t_dep, replanned=True,
                          reason=f"{'갈 때' if hits[0][0] == 'out' else '올 때'} t={hits[0][1]:.0f} s 충돌 예상 → 재계획")
                if all(n.get("feasible") for n in new):
                    ss["legs"] = legs[:act] + new
                else:
                    ev["reason"] += " (허용 범위 내 대안 없음 – 기존 경로 유지, 위험)"
                    ev["replanned"] = False
            ss["replans"].append(ev)
            revs = [dict(t=t, obstacles=[o.to_dict() for o in ob]) for t, ob in ss["revisions"]]
            out = self._render(cx, ss["legs"], self._truth(ss), errs, dict(revisions=revs, replans=ss["replans"]),
                               base_sims=ss["base_sims"])
            out["elapsed"] = time.time() - t_all
            return out

    def _truth(self, ss):
        from ..st_rolling import ObstacleTimeline
        return ObstacleTimeline(list(ss["revisions"])).truth()


def _load_adsb():
    from .. import config as C
    f = C.RES / "adsb_error_model.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    m = d["model_all"]
    return dict(knots=d["knots"], q=m["q"], n_aircraft=d["n_aircraft"], n_obs=d["n_obs"])


ADSB = _load_adsb()


def apply_shape(obs, cx):
    """'실측 ADS-B 분위수' 모양: 이동 장애물마다 팽창 반경 = (관측 시각의 속도) × (속도 정규화 1−ε 분위수 곡선)."""
    if cx.get("shape") != "adsb" or cx.get("eps") is None:
        return
    q = ADSB["q"][str(cx["eps"])]
    for o in obs:
        if len(o.path) < 2:
            continue                                     # 정지 장애물은 σ 입력(가우시안) 사용
        ts = o.times()
        k = int(np.clip(np.searchsorted(ts, o.t_obs, side="right") - 1, 0, len(ts) - 2))
        p0, p1 = o.path[k], o.path[k + 1]
        V = math.hypot(p1["x"] - p0["x"], p1["y"] - p0["y"]) / max(p1["t"] - p0["t"], 1e-9)
        o.tube = [[float(t), float(V * r)] for t, r in zip(ADSB["knots"], q)]
        o.sigma_t = 0.0


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
                if self.path == "/api/replan":
                    return self._send(200, app.replan(req))
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
