"""실시간 운용 엔진 – 시계에 맞춰 비행하면서 감시·충돌 검사·재계획을 비동기로 수행한다.

구성 (스레드)
  ① 시계(tick, 20 Hz): 시뮬레이션 시각 T 를 배속만큼 전진, 비행 단계 전환, 예약된 경로 교체, 실제 이격 감시, 원격측정 기록
  ② 감시(monitor): 감시 주기마다 장애물 예측 갱신(사용자 장애물·실측 ADS-B 침입기·외부 보고)
     → 위험구간 표 재생성 → 남은 경로 검사(수십 ms) → 충돌 예상 시 재계획 요청
  ③ 재계획(replan): 교체 시각 T_sw = 지금 + 지연 + (예상 계산시간 × 배속) 에서의 위치로부터 계획
     → 완화 단계(ε → 0.2 → 팽창 없음) → T_sw 전에 끝나면 교체 예약, 늦으면 폐기하고 재시도
     드론은 계산하는 동안 기존 경로를 그대로 비행한다 (계산 지연이 실제로 비행에 반영됨)

시각 규약: T = site 절대 시각 [s], t = T − t_dep (출발 기준). sync=True 이면 스레드 없이 step() 으로 결정적 실행(테스트용).
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time

import numpy as np

from .energy import power_w, soc_drop
from .obstacles import DynObstacle, build_interval_table, validate
from .planner import simulate, vertical_energy
from .st_planner import STPlanner
from .st_rolling import remaining_conflict, _snap, _cut

KST = 9


# ------------------------------------------------------------------ 실측 ADS-B 팽창 모양
def adsb_knots(model, eps, V, horizon=20.0):
    """실측 분위수 곡선 × 속도, 지평 이후 일정 (재관측으로 갱신된다고 가정)."""
    q = model["q"][str(eps)]
    kn = [(float(t), float(V * r)) for t, r in zip(model["knots"], q) if t <= horizon]
    return [list(k) for k in kn] + [[7200.0, kn[-1][1]]]


def gauss_knots(s1, eps, V, horizon=20.0):
    """가우시안 튜브 κ_ε·σ(τ), σ = s1·V·τ (실측에서 적합한 속도 정규화 기울기), 지평 이후 일정."""
    from .obstacles import risk_kappa
    r = risk_kappa(eps) * s1 * V * horizon
    return [[0.0, 0.0], [horizon, r], [7200.0, r]]


def _seg_speed(o, t):
    ts = o.times()
    if len(ts) < 2:
        return 0.0
    k = int(np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2))
    p0, p1 = o.path[k], o.path[k + 1]
    return math.hypot(p1["x"] - p0["x"], p1["y"] - p0["y"]) / max(p1["t"] - p0["t"], 1e-9)


# ------------------------------------------------------------------ 침입기 (실측 항적 재생 / 외부 보고)
class ReplayIntruder:
    """실측 1 Hz 항적을 시간·선회 이력 그대로, 공간만 U/V0 배 축소해 충돌 코스로 재생 (E11 과 같은 규칙)."""

    def __init__(self, iid, tr, i0, t_pop, L, heading, aim, U, R, seg=150.0):
        from .adsb_errors import velocity
        self.id, self.tr, self.i0, self.t_pop, self.R, self.kind = iid, tr, i0, t_pop, R, "replay"
        self.name = f"ADS-B {tr.get('cs') or tr['icao']}"
        v0 = velocity(tr, i0)
        d = heading - math.atan2(v0[0], v0[1])
        self.k = U / max(math.hypot(*v0), 1e-6)
        self.c, self.s = math.cos(d) * self.k, math.sin(d) * self.k
        rv = self._rot(*v0)
        self.P0 = (aim[0] - L * rv[0], aim[1] - L * rv[1])
        self.s0 = tr["t"][i0]
        self.s_end = min(tr["t"][-1], self.s0 + seg)
        self.U = U

    def _rot(self, x, y):
        return (x * self.c + y * self.s, -x * self.s + y * self.c)

    def active(self, t):
        return t >= self.t_pop and self.s0 + (t - self.t_pop) <= self.s_end

    def true_pos(self, t):
        tr = self.tr
        s = self.s0 + (t - self.t_pop)
        dx = np.interp(s, tr["t"], tr["x"]) - tr["x"][self.i0]
        dy = np.interp(s, tr["t"], tr["y"]) - tr["y"][self.i0]
        x, y = self._rot(dx, dy)
        return self.P0[0] + float(x), self.P0[1] + float(y)

    def observe(self, t):
        """감시 관측: 실제 위치 + ADS-B 보고 속도 → (x, y, vx, vy)."""
        from .adsb_errors import velocity
        tr = self.tr
        s = self.s0 + (t - self.t_pop)
        i = int(np.searchsorted(tr["t"], s, side="right")) - 1
        v = velocity(tr, max(i, 0)) or (0.0, 0.0)
        vx, vy = self._rot(*v)
        x, y = self.true_pos(t)
        return x, y, vx, vy

    def t_end(self):
        return self.t_pop + (self.s_end - self.s0)


class ReportedTrack:
    """외부 감시 보고(ADS-B 수신기·UTM 등) – 마지막 보고로 등속 추측항법. 실제 위치는 알 수 없으므로 감시도 보고 기반."""

    def __init__(self, iid, R=60.0, name=None):
        self.id, self.R, self.kind = iid, R, "report"
        self.name = name or f"보고 {iid}"
        self.last = None            # (t, x, y, vx, vy)
        self.n = 0

    def report(self, t, x, y, vx, vy):
        self.last = (t, x, y, vx, vy); self.n += 1

    def active(self, t):
        return self.last is not None and t - self.last[0] <= 30.0

    def true_pos(self, t):
        t0, x, y, vx, vy = self.last
        return x + vx * (t - t0), y + vy * (t - t0)

    def observe(self, t):
        t0, x, y, vx, vy = self.last
        return x + vx * (t - t0), y + vy * (t - t0), vx, vy

    def t_end(self):
        return self.last[0] + 30.0 if self.last else -1


# ------------------------------------------------------------------ 엔진
class LiveEngine:
    TICK = 0.05

    def __init__(self, site, drone, adsb_model=None, sync=False):
        self.site, self.drone, self.adsb_model, self.sync = site, drone, adsb_model, sync
        self.lock = threading.RLock()
        self.SP = STPlanner(site, drone)          # 재계획(긴급) 전용
        self.SPr = STPlanner(site, drone)         # 백그라운드 개선 전용 (긴급 재계획을 막지 않도록 분리)
        self._tracks = None
        if not sync:                              # 실측 항적 미리 읽기 (첫 침입기 투입 지연 방지)
            threading.Thread(target=self._load_pool, daemon=True).start()
        self.state = "idle"
        self.session = 0
        self._threads = []

    # -------------------------------------------------------------- 시작/제어
    def start(self, cfg, user_obstacles=()):
        """cfg: hub, dst, dep_hour, payload, kind, round_trip, eps, shape, speed, period, latency, horizon,
        weight, replan_weight, reserve, service_s, max_wait."""
        self.stop()
        with self.lock:
            self.session += 1
            d = self.drone
            c = dict(hub=(2700.0, 3000.0), dst=(4600.0, 1500.0), dep_hour=15, payload=0.5, kind="delivery",
                     round_trip=True, eps=0.05, shape="gauss", speed=5.0, period=2.0, latency=3.0, horizon=20.0,
                     weight=2.0, replan_weight=2.0, refine_weight=1.2, refine=True, reserve=20.0, service_s=20.0,
                     max_wait=120.0)
            c.update({k: v for k, v in cfg.items() if v is not None})
            c["hub"], c["dst"] = tuple(c["hub"]), tuple(c["dst"])
            c["eps"] = float(c["eps"]) if c["eps"] not in (None, "", 0, "0") else None
            c["speed"] = float(np.clip(float(c["speed"]), 0.25, 30.0))
            self.cfg = c
            self.t_dep = (int(c["dep_hour"]) - KST) * 3600.0
            m0 = d.m_empty
            mo, mb = (m0 + c["payload"], m0) if c["kind"] == "delivery" else (m0, m0 + c["payload"])
            self.legs = [dict(name="out", a=c["hub"], b=c["dst"], m=mo)]
            if c["round_trip"]:
                self.legs.append(dict(name="back", a=c["dst"], b=c["hub"], m=mb))
            self.E_usable = d.battery_wh * 3600 * (1 - c["reserve"] / 100.0)
            ok, errs = validate([DynObstacle.from_dict(o) if isinstance(o, dict) else o for o in user_obstacles],
                                self.site)
            self.user_obs = {f"U{q + 1}": o for q, o in enumerate(ok)}
            self.user_seq = len(self.user_obs)
            self.intruders = {}
            self.int_seq = 0
            self.t = 0.0                      # 출발 기준 시각
            self.pieces = []                  # 비행 조각 (시간 함수)
            self.e_done = 0.0                 # 끝난 조각의 누적 에너지
            self.leg_i = -1
            self.phase = "준비"
            self.plan = None                  # 현재 순항 계획 dict(sim, ver)
            self.plan_ver = 0
            self.pending = None               # (T_sw, plan) 교체 예약
            self.replanning = False
            self.replan_req = None
            self.retry = None
            self.est_cpu = 1.0
            self.est_fine = 3.0                # 개선(정밀) 계획 계산시간 추정                # 재계획 계산시간 추정 (지수평활) [s, 실시간]
            self.events = []
            self.telemetry = []
            self.next_tel = 0.0
            self.next_obs = 0.0
            self.obs_dirty = True
            self.min_sep = math.inf
            self.alerts = 0
            self.collisions = 0
            self._last_alert = {}
            self.known = []
            self.table = None
            self.stats = dict(replans=0, late=0, degraded=0, checks=0, check_ms=[], cpu=[], fail=0, refined=0,
                              refine_gain=[], refine_cpu=[], tick_gap=[])
            self.state = "running"
            self.paused = False
            if errs:
                self._ev("warn", "허용 범위 밖 장애물 제외: " + "; ".join(errs[:3]))
            self._ev("info", f"임무 시작 – {'배송' if c['kind'] == 'delivery' else '회수'} "
                             f"{'왕복' if c['round_trip'] else '편도'}, {c['dep_hour']}시, ×{c['speed']:g}, "
                             f"ε={c['eps'] if c['eps'] else '무시'} ({'실측 ADS-B 모양' if c['shape'] == 'adsb' else '가우시안'})")
            self._start_leg(0)
        if not self.sync:
            sid = self.session
            for fn in (self._tick_loop, self._monitor_loop, self._replan_loop, self._refine_loop):
                th = threading.Thread(target=fn, args=(sid,), daemon=True)
                th.start()
                self._threads.append(th)
        return self.snapshot()

    def stop(self):
        with self.lock:
            if self.state == "running":
                self._ev("info", "운용 중지")
            self.state = "stopped" if self.state != "idle" else "idle"
            self.session += 1
        for th in self._threads:
            th.join(timeout=2.0)
        self._threads = []

    def control(self, req):
        with self.lock:
            if "speed" in req and req["speed"] is not None:
                self.cfg["speed"] = float(np.clip(float(req["speed"]), 0.25, 30.0))
                self._ev("info", f"배속 ×{self.cfg['speed']:g}")
            if req.get("action") == "pause":
                self.paused = True; self._ev("info", "일시정지")
            elif req.get("action") == "resume":
                self.paused = False; self._ev("info", "재개")
            elif req.get("action") == "stop":
                pass
        if req.get("action") == "stop":
            self.stop()
        return self.snapshot()

    # -------------------------------------------------------------- 실시간 입력
    def upsert_obstacle(self, od, oid=None):
        """사용자 장애물 추가/수정 – 지금 발표된 것으로 보고 즉시 검사."""
        o = DynObstacle.from_dict(od) if isinstance(od, dict) else od
        ok, errs = validate([o], self.site)
        if errs:
            raise ValueError("; ".join(errs))
        with self.lock:
            if oid is None:
                self.user_seq += 1
                oid = f"U{self.user_seq}"
            new = oid not in self.user_obs
            self.user_obs[oid] = ok[0]
            self.obs_dirty = True
            self._ev("obstacle", f"장애물 {'추가' if new else '변경'}: {ok[0].name} ({oid})")
        return oid

    def delete_obstacle(self, oid):
        with self.lock:
            if oid in self.user_obs:
                o = self.user_obs.pop(oid)
                self._ev("obstacle", f"장애물 삭제: {o.name} ({oid})")
            elif oid in self.intruders:
                o = self.intruders.pop(oid)
                self._ev("obstacle", f"침입기 제거: {o.name}")
            self.obs_dirty = True

    def _load_pool(self):
        from .adsb_errors import load_tracks, velocity
        if self._tracks is not None:
            return
        tracks = load_tracks(rate="1hz")
        pool = []
        for k, tr in enumerate(tracks):
            t = tr["t"]; nxt = t[0]
            for i in range(len(t)):
                if t[i] < nxt or t[-1] - t[i] < 150.0:
                    continue
                nxt = t[i] + 10.0
                v = velocity(tr, i)
                if v is not None and 10.0 <= math.hypot(*v) <= 75.0:
                    pool.append((k, i))
        self._pool = pool
        self._tracks = tracks

    def inject_intruder(self, req):
        """실측 ADS-B 항적을 드론의 L 초 뒤 예정 위치로 향하는 충돌 코스로 투입."""
        if self._tracks is None:
            self._load_pool()
        rng = np.random.default_rng(req.get("seed"))
        with self.lock:
            L = float(req.get("lead", rng.uniform(15.0, 35.0)))
            U = float(req.get("speed", rng.uniform(8.0, 30.0)))
            R = float(req.get("radius", 60.0))
            heading = math.radians(float(req["heading"])) if req.get("heading") is not None \
                else float(rng.uniform(0, 2 * math.pi))
            aim = self._drone_at(self.t + L)[:2]
            here = self._drone_at(self.t)[:2]
            fixed = req.get("heading") is not None
            for _ in range(60):          # 생성 지점이 드론에서 R + 300 m 이상 떨어지도록 (바로 옆 생성 방지)
                k, i0 = self._pool[int(rng.integers(len(self._pool)))]
                it = ReplayIntruder("tmp", self._tracks[k], i0, self.t, L, heading, aim, U, R)
                if math.hypot(it.P0[0] - here[0], it.P0[1] - here[1]) > R + 300.0:
                    break
                if not fixed:
                    heading = float(rng.uniform(0, 2 * math.pi))
            self.int_seq += 1
            iid = f"I{self.int_seq}"
            it.id = iid
            self.intruders[iid] = it
            self.obs_dirty = True
            self._ev("intruder", f"침입기 투입: {it.name} ({iid}) {U:.0f} m/s, 반경 {R:.0f} m, {L:.0f} s 뒤 드론 예정 위치로 향함")
        return iid

    def report(self, req):
        """외부 감시 보고 {id, x, y, vx, vy[, radius, name]} 또는 {id, lat, lon, gs, track}."""
        from . import config as C
        with self.lock:
            iid = "X" + str(req.get("id", "1"))
            if "lat" in req:
                W, H = self.site.nx * self.site.dx, self.site.ny * self.site.dx
                x = (float(req["lon"]) - C.LON_MIN) / (C.LON_MAX - C.LON_MIN) * W
                y = (float(req["lat"]) - C.LAT_MIN) / (C.LAT_MAX - C.LAT_MIN) * H
                gs, trk = float(req.get("gs", 0.0)), math.radians(float(req.get("track", 0.0)))
                vx, vy = gs * math.sin(trk), gs * math.cos(trk)
            else:
                x, y, vx, vy = (float(req[k]) for k in ("x", "y", "vx", "vy"))
            if not (math.hypot(vx, vy) <= 80.0):
                raise ValueError("속도 80 m/s 초과 보고는 거부")
            new = iid not in self.intruders
            if new:
                self.intruders[iid] = ReportedTrack(iid, float(req.get("radius", 60.0)), req.get("name"))
            self.intruders[iid].report(self.t, x, y, vx, vy)
            self.obs_dirty = True
            if new:
                self._ev("intruder", f"외부 보고 항적 시작: {self.intruders[iid].name}")
        return iid

    # -------------------------------------------------------------- 비행 단계
    def _ev(self, kind, msg, **kw):
        self.events.append(dict(id=len(self.events), t=round(self.t, 1), kind=kind, msg=msg, **kw))

    def _start_leg(self, q):
        s, d = self.site, self.drone
        self.leg_i = q
        L = self.legs[q]
        L["st"], L["gl"] = s.lowest_free_k(*L["a"]), s.lowest_free_k(*L["b"])
        T = self.t_dep + self.t
        e_to, t_to = vertical_energy(s, d, L["a"][0], L["a"][1], L["st"][0], T, L["m"], up=True)
        z0 = float(s.ground[int(L["a"][1] // s.dx), int(L["a"][0] // s.dx)])
        L.update(e_to=e_to, t_to=t_to, z0=z0, z1=float(s.z[L["st"][0]]))
        # 계획이 준비될 때까지 지상 대기 (공중 호버로 배터리를 쓰지 않음) → 준비되면 이륙
        self._push(dict(kind="hover", x=L["a"][0], y=L["a"][1], z=z0, t0=self.t, t1=math.inf, P=0.0))
        self.phase = "출발 준비"
        self.takeoff_at = None
        self.start_fail = 0
        self.plan = None
        self._ev("phase", f"{'갈 때' if L['name'] == 'out' else '올 때'} 구간 계획 중 (지상 대기)")
        self._request_replan("구간 초기 계획", t_sw=self.t + self._margin() + t_to, start_node=L["st"])

    def _push(self, piece):
        if self.pieces:
            self.e_done += self._piece_energy(self.pieces[-1], piece["t0"])
            self.pieces[-1]["t1"] = min(self.pieces[-1].get("t1", piece["t0"]), piece["t0"])
        piece["e0"] = self.e_done
        self.pieces.append(piece)

    def _piece_energy(self, p, t):
        """조각 p 의 시작~t 에너지."""
        if p["kind"] == "vert":
            f = np.clip((t - p["t0"]) / max(p["t1"] - p["t0"], 1e-9), 0, 1)
            return p["E"] * f
        if p["kind"] == "hover":
            return p["P"] * max(t - p["t0"], 0.0)
        sim = p["sim"]
        return float(np.interp(self.t_dep + t, sim["t"], sim["e"]))

    def _piece_pos(self, p, t):
        if p["kind"] == "vert":
            f = np.clip((t - p["t0"]) / max(p["t1"] - p["t0"], 1e-9), 0, 1)
            return p["x"], p["y"], p["z0"] + f * (p["z1"] - p["z0"])
        if p["kind"] == "hover":
            return p["x"], p["y"], p["z"]
        sim = p["sim"]
        T = self.t_dep + t
        return (float(np.interp(T, sim["t"], sim["x"])), float(np.interp(T, sim["t"], sim["y"])),
                float(np.interp(T, sim["t"], sim["z"])))

    def _drone_at(self, t):
        """시각 t 의 드론 위치 – 현재 조각(미래면 계획 연장)."""
        p = self.pieces[-1]
        return self._piece_pos(p, t)

    def _emergency_land(self, reason):
        x, y, z = self._drone_at(self.t)
        s = self.site
        i, j = int(x // s.dx), int(y // s.dx)
        k = int(np.clip(round((z - s.z[0]) / s.dz), 0, s.nz - 1))
        e_ld, t_ld = vertical_energy(s, self.drone, x, y, k, self.t_dep + self.t, self.legs[self.leg_i]["m"], up=False)
        self._push(dict(kind="vert", x=x, y=y, z0=z, z1=float(s.ground[j, i]), t0=self.t, t1=self.t + t_ld, E=e_ld))
        self.plan = None; self.pending = None; self.retry = None; self.plan_ver += 1
        self.phase = "비상 착륙"
        self._ev("alert", reason)

    def energy(self):
        return self.e_done + self._piece_energy(self.pieces[-1], self.t)

    def energy_at(self, t):
        return self.e_done + self._piece_energy(self.pieces[-1], t)

    def _t_reserve(self, t_new):
        """[self.t, t_new] 안에서 누적 에너지가 예비에 닿는 시각 (이분법)."""
        a, b = self.t, t_new
        for _ in range(30):
            mid = 0.5 * (a + b)
            if self.energy_at(mid) >= self.E_usable:
                b = mid
            else:
                a = mid
        return b

    def _hover_piece(self, x, y, z, k=None):
        s, L = self.site, self.legs[self.leg_i]
        T = self.t_dep + self.t
        i, j = int(x // s.dx), int(y // s.dx)
        k = k if k is not None else int(round((z - s.z[0]) / s.dz))
        wu, wv = s.wind_at(T, int(np.clip(k, 0, s.nz - 1)), j, i)
        return dict(kind="hover", x=x, y=y, z=z, t0=self.t, t1=math.inf,
                    P=power_w(math.hypot(wu, wv), 0.0, L["m"], 1.2, self.drone.params()))

    def _install(self, plan):
        """예약된 계획으로 교체 (지상 지연·출발 전 공백은 호버로 채움)."""
        sim = plan["sim"]
        t_sim0 = sim["t"][0] - self.t_dep
        if t_sim0 > self.t + 0.5:
            x, y, z = self._drone_at(self.t)
            self._push(self._hover_piece(x, y, z))
            plan["hover_until"] = t_sim0
        else:
            self._push(dict(kind="sim", sim=sim, t0=self.t, t1=sim["t"][-1] - self.t_dep))
        self.plan = plan
        self.plan_ver += 1
        if self.phase in ("이륙", "대기"):
            self.phase = "순항"

    def _advance(self, dt):
        """시각을 dt 만큼 전진하며 단계 전환·교체 예약 처리 (lock 보유 상태에서 호출)."""
        t_new = self.t + dt
        while True:
            # 교체 예약
            if self.pending is not None and self.pending[0] <= t_new:
                T_sw, plan = self.pending
                self.pending = None
                self.t = max(self.t, T_sw)
                self._install(plan)
                continue
            p = self.pieces[-1]
            # 지상 대기 → 계획 준비 완료 시각에 이륙
            if self.phase == "출발 준비" and self.takeoff_at is not None and self.takeoff_at <= t_new:
                L = self.legs[self.leg_i]
                self.t = max(self.t, self.takeoff_at)
                self.takeoff_at = None
                self._push(dict(kind="vert", x=L["a"][0], y=L["a"][1], z0=L["z0"], z1=L["z1"], t0=self.t,
                                t1=self.t + L["t_to"], E=L["e_to"]))
                self.phase = "이륙"
                self._ev("phase", f"이륙 (상승 {L['t_to']:.0f} s)")
                continue
            if p["kind"] == "hover" and self.plan is not None and self.plan.get("hover_until") is not None \
                    and self.plan["hover_until"] <= t_new:
                self.t = self.plan["hover_until"]
                self.plan["hover_until"] = None
                sim = self.plan["sim"]
                self._push(dict(kind="sim", sim=sim, t0=self.t, t1=sim["t"][-1] - self.t_dep))
                self.phase = "순항"
                continue
            # 이륙 끝 → 계획 없으면 호버 대기
            if p["kind"] == "vert" and self.phase == "이륙" and p["t1"] <= t_new:
                self.t = p["t1"]
                x, y, z = self._piece_pos(p, self.t)
                self._push(self._hover_piece(x, y, z, k=self.legs[self.leg_i]["st"][0]))
                self.phase = "대기"
                self._ev("phase", "이륙 완료 – 계획 대기(호버)")
                continue
            # 순항 끝 → 착륙
            if p["kind"] == "sim" and p["t1"] <= t_new:
                self.t = p["t1"]
                L = self.legs[self.leg_i]
                x, y, z = self._piece_pos(p, self.t)
                s = self.site
                e_ld, t_ld = vertical_energy(s, self.drone, L["b"][0], L["b"][1], L["gl"][0], self.t_dep + self.t,
                                             L["m"], up=False)
                zg = float(s.ground[int(L["b"][1] // s.dx), int(L["b"][0] // s.dx)])
                self._push(dict(kind="vert", x=x, y=y, z0=z, z1=zg, t0=self.t, t1=self.t + t_ld, E=e_ld))
                self.phase = "착륙"
                self.plan = None
                self.pending = None
                self._ev("phase", f"{'목적지' if L['name'] == 'out' else '거점'} 도착 – 착륙")
                continue
            # 비상 착륙 끝 → 임무 중단
            if p["kind"] == "vert" and self.phase == "비상 착륙" and p["t1"] <= t_new:
                self.t = p["t1"]
                self.phase = "중단"
                self.state = "aborted"
                E = self.energy()
                self._ev("alert", f"임무 중단 – 비상 착륙 완료 ({E / 1000:.1f} kJ, SOC 소모 {soc_drop(E, self.drone):.1f}%p)")
                return
            # 배터리 예비 도달 → 그 자리 비상 착륙
            if self.phase in ("순항", "대기") and self.energy_at(t_new) >= self.E_usable:
                self.t = max(self.t, min(t_new, self._t_reserve(t_new)))
                self._emergency_land("배터리 예비 SOC 도달 – 현재 위치 비상 착륙")
                continue
            # 착륙 끝 → 다음 구간 또는 완료
            if p["kind"] == "vert" and self.phase == "착륙" and p["t1"] <= t_new:
                self.t = p["t1"]
                if self.leg_i + 1 < len(self.legs):
                    x, y, z = self._piece_pos(p, self.t)
                    self._push(dict(kind="hover", x=x, y=y, z=z, t0=self.t, t1=self.t + self.cfg["service_s"], P=0.0))
                    self.phase = "지상 작업"
                    self._ev("phase", f"지상 작업 {self.cfg['service_s']:.0f} s (화물 {'하역' if self.cfg['kind'] == 'delivery' else '적재'})")
                    continue
                self.phase = "완료"
                self.state = "done"
                E = self.energy()
                self._ev("phase", f"임무 완료 – 총 {E / 1000:.1f} kJ, SOC 소모 {soc_drop(E, self.drone):.1f}%p, "
                                  f"최소 이격 {self.min_sep:.0f} m, 재계획 {self.stats['replans']}회")
                return
            if p["kind"] == "hover" and self.phase == "지상 작업" and p["t1"] <= t_new:
                self.t = p["t1"]
                self._start_leg(self.leg_i + 1)
                continue
            break
        self.t = t_new

    # -------------------------------------------------------------- 감시
    def _true_obstacles(self, t):
        """실제(진실) 위치 – 사용자 장애물은 정의 경로, 침입기는 실측 재생(또는 보고 기반 추측항법)."""
        out = []
        for oid, o in self.user_obs.items():
            p = o.position(t)
            if p is not None:
                out.append((oid, o, p))
        for iid, it in self.intruders.items():
            if it.active(t):
                x, y = it.true_pos(t)
                out.append((iid, it, (x, y, None)))
        return out

    def _separation(self):
        x, y, z = self._drone_at(self.t)
        best = (math.inf, None)
        for oid, o, p in self._true_obstacles(self.t):
            if isinstance(o, DynObstacle):
                if o.kind == "sphere":
                    d = math.sqrt((x - p[0]) ** 2 + (y - p[1]) ** 2 + (z - p[2]) ** 2) - o.radius
                else:
                    if not (o.z_min <= z <= o.z_max):
                        continue
                    if o.kind == "box":
                        dx, dy = max(abs(x - p[0]) - o.half_x, 0), max(abs(y - p[1]) - o.half_y, 0)
                        d = math.hypot(dx, dy) if (dx or dy) else -min(o.half_x - abs(x - p[0]), o.half_y - abs(y - p[1]))
                    else:
                        d = math.hypot(x - p[0], y - p[1]) - o.radius
            else:
                d = math.hypot(x - p[0], y - p[1]) - o.R
            if d < best[0]:
                best = (d, oid)
        return best

    def _known_obstacles(self, t):
        """계획기가 아는 장애물: 사용자 장애물(발표된 경로) + 침입기 관측으로 만든 등속 예측(+ 팽창)."""
        c = self.cfg
        out = []
        for oid, o in self.user_obs.items():
            o2 = copy.deepcopy(o)
            o2.name = oid
            if c["eps"] and c["shape"] == "adsb" and self.adsb_model and len(o2.path) > 1:
                o2._V = _seg_speed(o2, t)
                o2.tube = adsb_knots(self.adsb_model, c["eps"], o2._V, c["horizon"])
                o2.sigma_t = 0.0
            out.append(o2)
        for iid, it in self.intruders.items():
            if not it.active(t):
                continue
            x, y, vx, vy = it.observe(t)
            V = math.hypot(vx, vy)
            t_end = min(t + c["horizon"] * 3, it.t_end())
            if t_end <= t:
                continue
            o2 = DynObstacle(name=iid, kind="cylinder", radius=it.R, margin=15.0, z_min=0.0, z_max=5000.0,
                             path=[dict(x=x, y=y, t=t), dict(x=x + vx * (t_end - t), y=y + vy * (t_end - t), t=t_end)],
                             t_start=t, t_end=t_end, t_obs=t)
            o2._V = V
            if c["eps"]:
                o2.tube = self._tube(c["eps"], V)
            out.append(o2)
        return out

    def _tube(self, eps, V):
        c = self.cfg
        if c["shape"] == "adsb" and self.adsb_model:
            return adsb_knots(self.adsb_model, eps, V, c["horizon"])
        return gauss_knots((self.adsb_model or {}).get("s1", 0.36), eps, V, c["horizon"])

    def _relax(self, obs, level):
        """완화 단계: 0 = 설정 ε, 1 = ε 0.2, 2 = 팽창 없음."""
        if level == 0 or not self.cfg["eps"]:
            return obs, self.cfg["eps"]
        if level == 2:
            out = []
            for o in obs:
                o2 = copy.deepcopy(o); o2.tube = None; o2.sigma0 = o2.sigma_v = o2.sigma_t = 0.0
                out.append(o2)
            return out, None
        out = []
        for o in obs:
            o2 = copy.deepcopy(o)
            if o2.tube and getattr(o2, "_V", None) is not None:
                o2.tube = self._tube(0.2, o2._V)
            out.append(o2)
        return out, 0.2

    def surveil(self):
        """감시 1회: 예측 갱신 → 표 → 남은 경로 검사 → 필요 시 재계획 요청. (lock 밖에서 표 생성)"""
        with self.lock:
            t = self.t
            known = self._known_obstacles(t)
            plan = self.plan
            phase = self.phase
            eps = self.cfg["eps"]
        tc = time.perf_counter()
        table = build_interval_table(self.site, known, t_offset=self.t_dep, eps=eps)
        hit = None
        if plan is not None and phase == "순항":
            hit = remaining_conflict(self.site, table, plan["sim"], self.t_dep + t + 0.5)
        ms = (time.perf_counter() - tc) * 1000
        with self.lock:
            self.known, self.table = known, table
            self.stats["checks"] += 1
            self.stats["check_ms"].append(ms)
            if hit is not None and self.phase == "순항" and self.plan is plan:
                t_hit = hit - self.t_dep
                need = self._margin() + 2.0
                if t_hit - self.t < need:
                    # 계산이 끝나기 전에 충돌 → 즉시 그 자리 호버(비상 정지) 후 정지 위치에서 재계획
                    self._hold(f"비상 정지: {t_hit - self.t:.0f} s 뒤 충돌 예상 (재계획 소요 {need:.0f} s)")
                    self.pending = None
                    self.replanning = False
                    self._request_replan("비상 정지 위치에서 재계획", known=known)
                elif not self.replanning and self.pending is None:
                    self._request_replan(f"충돌 예상 (t={t_hit:.0f} s) – 재계획", known=known)

    # -------------------------------------------------------------- 재계획
    def _margin(self):
        """교체 시각 여유 = 지연 + 예상 계산시간 × 배속 × 1.5 (계산시간 변동 대비)."""
        c = self.cfg
        return c["latency"] + (0.0 if self.sync else 1.5 * self.est_cpu * c["speed"] + 0.5)

    def _hold(self, reason):
        """현재 위치 호버 유지 (계획 해제). 호버 중엔 위치가 고정이라 늦게 끝난 계산도 그대로 쓸 수 있다."""
        x, y, z = self._drone_at(self.t)
        self._push(self._hover_piece(x, y, z))
        self.plan = None
        self.plan_ver += 1
        self.phase = "대기"
        self.stats["holds"] = self.stats.get("holds", 0) + 1
        self._ev("alert", reason)

    def _request_replan(self, reason, t_sw=None, start_node=None, known=None):
        c = self.cfg
        margin = self._margin()
        self.replan_req = dict(reason=reason, t_sw=max(t_sw if t_sw is not None else self.t + margin, self.t + 0.5),
                               start_node=start_node, known=known, leg=self.leg_i, sess=self.session)
        self.replanning = True
        if self.sync:
            self._do_replan()

    def _do_replan(self):
        s, d = self.site, self.drone
        with self.lock:
            req = self.replan_req
            if req is None or req["sess"] != self.session:
                self.replanning = False
                return
            self.replan_req = None
            L = self.legs[req["leg"]]
            known = req["known"] if req["known"] is not None else self._known_obstacles(self.t)
            t_sw = req["t_sw"]
            x, y, z = self._drone_at(t_sw)
            used = self.e_done + self._piece_energy(self.pieces[-1], t_sw)
            m = L["m"]
            c = self.cfg
        T_sw = self.t_dep + t_sw
        t0 = time.perf_counter()
        r, level, cur = None, 0, None
        diag = []
        gx, gy = (L["gl"][2] + 0.5) * s.dx, (L["gl"][1] + 0.5) * s.dx
        lb = self.SP.energy_lower_bound(m) * math.hypot(gx - x, gy - y)
        for level in range(3 if c["eps"] else 1):
            if lb > self.E_usable - used:
                diag.append("배터리부족(하한)")
                break
            obs, eps = self._relax(known, level)
            self.SP.set_obstacles(obs, self.t_dep, eps=eps)
            cur = req["start_node"] if req["start_node"] is not None else _snap(s, self.SP.table, x, y, z, T_sw)
            if cur is None:
                diag.append(f"L{level}:위치위험")
                continue
            r = self.SP.plan(tuple(cur), L["gl"], t_start=T_sw, mass=m, budget=self.E_usable - used,
                             max_wait=c["max_wait"], weight=c["weight"] if req["start_node"] is not None else c["replan_weight"])
            if r is not None and len(r["path"]) >= 2:
                break
            diag.append(f"L{level}:경로없음")
            r = None
        if r is None and req["start_node"] is not None and not any("배터리" in x for x in diag):
            # 원인 구분: 배터리 제한 없이 한 번 더 – 찾으면 배터리 부족, 못 찾으면 장애물로 막힘
            obs, eps = self._relax(known, 2 if c["eps"] else 0)
            self.SP.set_obstacles(obs, self.t_dep, eps=eps)
            r_inf = self.SP.plan(tuple(req["start_node"]), L["gl"], t_start=T_sw, mass=m, budget=np.inf,
                                 max_wait=c["max_wait"], weight=2.5)
            diag.append("배터리부족(무제한이면 가능)" if r_inf is not None else "장애물로 막힘")
        sim = None
        if r is not None:
            sim = simulate(s, d, r["path"], r["wait_before"], T_sw + r.get("ground_delay", 0.0), m)
        cpu = time.perf_counter() - t0
        with self.lock:
            self.replanning = False
            if req["sess"] != self.session or req["leg"] != self.leg_i:
                return
            self.stats["cpu"].append(cpu)
            self.est_cpu = 0.7 * self.est_cpu + 0.3 * cpu
            if sim is None or len(sim["t"]) < 2:
                self.stats["fail"] += 1
                self._ev("warn", f"{req['reason']}: 허용 범위 내 경로 없음 [{', '.join(diag)}] – "
                                 f"{'기존 경로 유지' if self.plan else '호버 대기'} 후 재시도 ({cpu:.1f} s)")
                if self.phase == "출발 준비":
                    self.start_fail = getattr(self, "start_fail", 0) + 1
                if self.phase == "출발 준비" and (any("배터리" in x for x in diag) or self.start_fail >= 6):
                    self.phase = "중단"; self.state = "aborted"
                    self._ev("alert", "임무 중단 – " + ("남은 배터리로 갈 수 없음 (지상 대기, 충전 필요)"
                                                       if any("배터리" in x for x in diag) else "경로가 계속 막힘 (지상 대기)"))
                    return
                if self.phase == "대기":
                    self.hold_fail = getattr(self, "hold_fail", 0) + 1
                    if self.hold_fail >= 4:          # 비상 대기 중 연속 실패 → 배터리를 다 쓰기 전에 현 위치 비상 착륙
                        self._emergency_land("비상 대기 중 회피 경로를 4회 연속 찾지 못함 – 현 위치 비상 착륙")
                        return
                if self.plan is None and self.pending is None:
                    self.retry = (self.t + 5.0, "계획 재시도")
                return
            if req["start_node"] is not None and self.phase == "출발 준비":
                L_ = self.legs[self.leg_i]
                if self.t > t_sw - L_["t_to"]:          # 늦음 – 지상이므로 손해 없이 다시
                    self.stats["late"] += 1
                    self._ev("warn", f"초기 계획이 이륙 시각보다 늦게 끝남 ({cpu:.1f} s) – 지상에서 다시 계획")
                    self._request_replan("구간 초기 계획", t_sw=self.t + self._margin() + L_["t_to"],
                                         start_node=L_["st"])
                    return
                self.takeoff_at = t_sw - L_["t_to"]
            if not self.sync and self.t > t_sw and self.plan is None and self.pieces[-1]["kind"] == "hover" \
                    and req["start_node"] is None:
                # 호버 중: 위치가 그대로이므로 늦게 끝난 계획을 지금 시각으로 옮겨 쓴다
                dt_shift = self.t + 0.5 - t_sw
                sim = dict(sim); sim["t"] = sim["t"] + dt_shift
                t_sw = self.t + 0.5
            if not self.sync and self.t > t_sw:
                self.stats["late"] += 1
                self._ev("warn", f"재계획이 교체 시각보다 늦게 끝남 ({cpu:.1f} s) – 폐기 후 재시도")
                if self.plan is None and self.pending is None:
                    self.retry = (self.t, req["reason"])          # 호버 중 – 현재 위치에서 다시
                else:
                    self.obs_dirty = True
                return
            self.hold_fail = 0
            if level > 0:
                self.stats["degraded"] += 1
            plan = dict(sim=sim, path=r["path"], ver=self.plan_ver + 1, level=level, reason=req["reason"])
            self.pending = (t_sw, plan)
            if req["start_node"] is None:
                self.stats["replans"] += 1
            lv = ["", " (완화: ε 0.2)", " (완화: 팽창 없음)"][level]
            self._ev("replan", f"{req['reason']} → t={t_sw:.0f} s 에 경로 교체 예약{lv} · 계산 {cpu:.2f} s",
                     cpu=round(cpu, 3), level=level)

    def _refine(self):
        """백그라운드 개선: 현재 계획을 정밀 가중치로 다시 풀어, 교체 시각 전에 끝나고 더 싸면 교체 예약."""
        s, d = self.site, self.drone
        with self.lock:
            c = self.cfg
            plan = self.plan
            if (plan is None or plan.get("refined") or self.pending is not None or self.replan_req is not None
                    or self.replanning or self.phase != "순항" or not c["refine"]):
                return False
            plan["refined"] = True
            L = self.legs[self.leg_i]
            known = list(self.known)
            t_sw = self.t + c["latency"] + (0.0 if self.sync else self.est_fine * c["speed"])
            if t_sw > plan["sim"]["t"][-1] - self.t_dep - 20:
                return False
            x, y, z = self._drone_at(t_sw)
            used = self.e_done + self._piece_energy(self.pieces[-1], t_sw)
            e_old = float(plan["sim"]["e"][-1] - np.interp(self.t_dep + t_sw, plan["sim"]["t"], plan["sim"]["e"]))
            sess, leg, ver = self.session, self.leg_i, self.plan_ver
        T_sw = self.t_dep + t_sw
        t0 = time.perf_counter()
        SP = self.SPr
        SP.set_obstacles(known, self.t_dep, eps=c["eps"])
        cur = _snap(s, SP.table, x, y, z, T_sw)
        r = None if cur is None else SP.plan(tuple(cur), L["gl"], t_start=T_sw, mass=L["m"],
                                             budget=self.E_usable - used, max_wait=c["max_wait"],
                                             weight=c["refine_weight"])
        sim = None if r is None or len(r["path"]) < 2 else \
            simulate(s, d, r["path"], r["wait_before"], T_sw + r.get("ground_delay", 0.0), L["m"])
        cpu = time.perf_counter() - t0
        with self.lock:
            self.est_fine = 0.7 * self.est_fine + 0.3 * cpu
            self.stats["refine_cpu"].append(cpu)
            if sess != self.session or leg != self.leg_i or ver != self.plan_ver or self.pending is not None \
                    or sim is None or (not self.sync and self.t > t_sw):
                return True
            e_new = float(sim["e"][-1])
            if e_new < e_old * 0.995 and (self.table is None or
                                          remaining_conflict(s, self.table, sim, T_sw) is None):
                gain = (1 - e_new / e_old) * 100
                self.stats["refined"] += 1
                self.stats["refine_gain"].append(gain)
                self.pending = (t_sw, dict(sim=sim, path=r["path"], ver=self.plan_ver + 1, level=0,
                                           reason="백그라운드 개선", refined=True))
                self._ev("refine", f"백그라운드 개선: 남은 에너지 −{gain:.1f}% → t={t_sw:.0f} s 에 교체 예약 · 계산 {cpu:.2f} s",
                         cpu=round(cpu, 3))
        return True

    # -------------------------------------------------------------- 시계·감시 갱신
    def step(self, dt):
        """dt 초(시뮬레이션) 전진 + 감시 주기 처리 + 실제 이격 감시 (sync 모드 또는 tick 스레드)."""
        with self.lock:
            if self.state != "running" or self.paused:
                return
            self._advance(dt)
            if self.state != "running":
                return
            d, oid = self._separation()
            if d < self.min_sep:
                self.min_sep = d
            if oid is not None:
                if d < 0:
                    if self.t - self._last_alert.get(("col", oid), -1e9) > 10:
                        self.collisions += 1
                        self._ev("alert", f"침범! {oid} 와 이격 {d:.0f} m", sep=d)
                    self._last_alert[("col", oid)] = self.t          # 침범이 이어지는 동안은 1건
                elif d < 30 and self.t - self._last_alert.get(("near", oid), -1e9) > 10:
                    self.alerts += 1
                    self._ev("alert", f"근접 경보: {oid} 와 이격 {d:.0f} m", sep=d)
                    self._last_alert[("near", oid)] = self.t
            if self.t >= self.next_tel:
                x, y, z = self._drone_at(self.t)
                E = self.energy()
                self.telemetry.append(dict(t=round(self.t, 1), x=round(x, 1), y=round(y, 1), z=round(z, 1),
                                           E=round(E, 1), soc=round(100 - soc_drop(E, self.drone), 2),
                                           sep=None if not math.isfinite(d) else round(d, 1), phase=self.phase))
                self.next_tel = self.t + 1.0
            if self.retry is not None and self.t >= self.retry[0] and not self.replanning and self.pending is None:
                reason = self.retry[1]
                self.retry = None
                if self.plan is None and self.phase in ("대기", "이륙"):
                    self._request_replan(reason, known=None)
                elif self.phase == "출발 준비":
                    L_ = self.legs[self.leg_i]
                    self._request_replan("구간 초기 계획", t_sw=self.t + self._margin() + L_["t_to"], start_node=L_["st"])
            due = self.t >= self.next_obs or self.obs_dirty
            if due:
                self.next_obs = self.t + self.cfg["period"]
                self.obs_dirty = False
        if due and self.sync:
            self.surveil()
            self._refine()
        return due

    def _tick_loop(self, sid):
        last = time.perf_counter()
        while self.session == sid and self.state == "running":
            time.sleep(self.TICK)
            now = time.perf_counter()
            gap = now - last
            dt = gap * self.cfg["speed"]
            last = now
            g = self.stats["tick_gap"]
            g.append(gap * 1000)
            if len(g) > 2000:
                del g[:1000]
            due = self.step(dt)
            if due:
                self._mon_evt.set() if hasattr(self, "_mon_evt") else None

    def _monitor_loop(self, sid):
        self._mon_evt = threading.Event()
        while self.session == sid and self.state == "running":
            if not self._mon_evt.wait(0.5):
                continue
            self._mon_evt.clear()
            if self.session != sid or self.state != "running":
                break
            try:
                self.surveil()
            except Exception as ex:  # noqa
                with self.lock:
                    self._ev("warn", f"감시 오류: {type(ex).__name__}: {ex}")

    def _replan_loop(self, sid):
        while self.session == sid and self.state == "running":
            if self.replan_req is not None:
                try:
                    self._do_replan()
                except Exception as ex:  # noqa
                    with self.lock:
                        self.replanning = False
                        self.replan_req = None
                        self._ev("warn", f"재계획 오류: {type(ex).__name__}: {ex}")
            else:
                time.sleep(0.02)

    def _refine_loop(self, sid):
        while self.session == sid and self.state == "running":
            try:
                if not self._refine():
                    time.sleep(0.1)
            except Exception as ex:  # noqa
                with self.lock:
                    self._ev("warn", f"개선 계산 오류: {type(ex).__name__}: {ex}")

    def run_sync(self, t_max=2000.0, dt=0.5, hooks=None):
        """테스트용 결정적 실행: hooks = {t: fn(engine)} 를 해당 시각에 호출."""
        hooks = dict(hooks or {})
        while self.state == "running" and self.t < t_max:
            for th in sorted(list(hooks)):
                if self.t >= th:
                    hooks.pop(th)(self)
            self.step(dt)
        return self.summary()

    # -------------------------------------------------------------- 출력
    def snapshot(self, since=0, plan_ver=-1):
        with self.lock:
            if self.state == "idle":
                return dict(state="idle")
            x, y, z = self._drone_at(self.t)
            E = self.energy()
            sep, sep_id = self._separation()
            obs = []
            for oid, o, p in self._true_obstacles(self.t):
                if isinstance(o, DynObstacle):
                    obs.append(dict(id=oid, name=o.name, kind=o.kind, x=p[0], y=p[1], r=o.radius, hx=o.half_x,
                                    hy=o.half_y, margin=o.margin, src="user"))
                else:
                    obs.append(dict(id=oid, name=o.name, kind="cylinder", x=p[0], y=p[1], r=o.R, margin=15.0,
                                    src=o.kind))
            pred = []
            for o in self.known:
                if not o.path:
                    continue
                p = o.position(self.t) if o.position(self.t) is not None else None
                if p is None:
                    continue
                infl = o.inflation(self.t, 0.0)[0] if o.tube else (
                    (math.sqrt(2 * math.log(1 / self.cfg["eps"])) * (o.sigma0 + o.sigma_v * max(self.t - o.t_obs, 0)))
                    if self.cfg["eps"] else 0.0)
                pe = o.path[-1]
                pred.append(dict(id=o.name, x=p[0], y=p[1], r=(o.radius if o.kind != "box" else max(o.half_x, o.half_y))
                                 + o.margin + infl, x1=pe["x"], y1=pe["y"]))
            out = dict(state=self.state, paused=self.paused, session=self.session, t=round(self.t, 2),
                       clock=f"{int(self.cfg['dep_hour']) + int(self.t // 3600):02d}:{int(self.t % 3600 // 60):02d}:"
                             f"{int(self.t % 60):02d}",
                       phase=self.phase, speed=self.cfg["speed"], leg=self.legs[self.leg_i]["name"] if self.leg_i >= 0 else None,
                       drone=dict(x=x, y=y, z=z, E=E, soc=100 - soc_drop(E, self.drone),
                                  usable_soc=self.cfg["reserve"]),
                       sep=None if not math.isfinite(sep) else sep, sep_id=sep_id,
                       min_sep=None if not math.isfinite(self.min_sep) else self.min_sep,
                       obstacles=obs, predictions=pred, replanning=self.replanning,
                       pending=None if self.pending is None else round(self.pending[0], 1),
                       est_cpu=self.est_cpu, plan_ver=self.plan_ver,
                       stats=dict(replans=self.stats["replans"], late=self.stats["late"], degraded=self.stats["degraded"],
                                  refined=self.stats["refined"],
                                  tick_p95=float(np.percentile(self.stats["tick_gap"][-400:], 95)) if self.stats["tick_gap"] else None,
                                  tick_max=float(np.max(self.stats["tick_gap"][-400:])) if self.stats["tick_gap"] else None,
                                  fail=self.stats["fail"], checks=self.stats["checks"],
                                  check_ms=float(np.median(self.stats["check_ms"][-20:])) if self.stats["check_ms"] else None,
                                  cpu_last=self.stats["cpu"][-1] if self.stats["cpu"] else None,
                                  alerts=self.alerts, collisions=self.collisions),
                       events=[e for e in self.events if e["id"] >= since])
            if self.plan_ver != plan_ver:
                pts = []
                if self.plan is not None:
                    sim = self.plan["sim"]
                    st = max(1, len(sim["t"]) // 300)
                    pts = [[round(float(a), 1), round(float(b), 1)] for a, b in zip(sim["x"][::st], sim["y"][::st])]
                    pts.append([round(float(sim["x"][-1]), 1), round(float(sim["y"][-1]), 1)])
                pend = []
                if self.pending is not None:
                    sim = self.pending[1]["sim"]
                    st = max(1, len(sim["t"]) // 300)
                    pend = [[round(float(a), 1), round(float(b), 1)] for a, b in zip(sim["x"][::st], sim["y"][::st])]
                out["plan"] = pts
                out["pending_plan"] = pend
            return out

    def summary(self):
        with self.lock:
            E = self.energy() if self.pieces else 0.0
            cpu = self.stats["cpu"]
            return dict(state=self.state, t=self.t, E_kJ=E / 1000, soc_used=soc_drop(E, self.drone),
                        min_sep=self.min_sep, collisions=self.collisions, alerts=self.alerts,
                        replans=self.stats["replans"], late=self.stats["late"], degraded=self.stats["degraded"],
                        fail=self.stats["fail"], checks=self.stats["checks"], refined=self.stats["refined"],
                        holds=self.stats.get("holds", 0),
                        refine_gain=float(np.sum(self.stats["refine_gain"])) if self.stats["refine_gain"] else 0.0,
                        refine_cpu_median=float(np.median(self.stats["refine_cpu"])) if self.stats["refine_cpu"] else None,
                        tick_ms_p95=float(np.percentile(self.stats["tick_gap"], 95)) if self.stats["tick_gap"] else None,
                        tick_ms_p99=float(np.percentile(self.stats["tick_gap"], 99)) if self.stats["tick_gap"] else None,
                        tick_ms_max=float(np.max(self.stats["tick_gap"])) if self.stats["tick_gap"] else None,
                        check_ms_median=float(np.median(self.stats["check_ms"])) if self.stats["check_ms"] else None,
                        cpu_median=float(np.median(cpu)) if cpu else None, cpu_max=float(np.max(cpu)) if cpu else None)

    def log(self):
        with self.lock:
            return dict(config={k: v for k, v in self.cfg.items()}, summary=self.summary(), events=self.events,
                        telemetry=self.telemetry,
                        user_obstacles={k: o.to_dict() for k, o in self.user_obs.items()},
                        intruders={k: dict(name=v.name, kind=v.kind, R=v.R) for k, v in self.intruders.items()})
