"""물류 왕복 임무: 배송(갈 때 무겁고 올 때 가벼움) / 회수(갈 때 가볍고 올 때 무거움).

수식 (회의자료 ①~⑤)
 ① 구간 비행시간 t_i = d_i / V_g,i          (V_g: 바람을 고려한 지면속도)
 ② 구간 소비에너지 E_i = P_i · t_i           (P_i: 해당 구간 예상 소비전력)
 ③ 경로 전체 에너지 E = Σ E_i (+ 이륙·착륙·호버링)
 ④ SOC 감소량 ΔSOC ≈ E / E_batt × 100 [%p]
 ⑤ 에너지 최적 경로 = argmin ΔSOC  s.t. 장애물·금지구역 비충돌, SOC_end ≥ 예비 20 %
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .energy import soc_drop, power_w
from .planner import Planner, simulate, vertical_energy, path_length, _wind


@dataclass
class Mission:
    hub: tuple
    dst: tuple
    payload: float = 0.5          # [kg]
    kind: str = "delivery"        # delivery | pickup
    t_dep: float = 0.0            # 출발시각 (site.t_ref 기준 초)
    ground_time: float = 30.0     # 목적지 하역/적재 시간 [s] (모터 정지)
    reserve: float = 20.0         # 예비 SOC [%]

    def masses(self, m0):
        if self.kind == "delivery":
            return m0 + self.payload, m0
        return m0, m0 + self.payload


def _leg(planner, site_plan, site_truth, drone, a, b, t0, mass, mode, speed_policy, vconst, U=None, V=None, tax=None,
         **pkw):
    st = site_plan.lowest_free_k(*a)
    gl = site_plan.lowest_free_k(*b)
    r = planner.plan(st, gl, t_start=t0, mass=mass, mode=mode, vconst=vconst, **pkw)
    if r is None:
        return None
    sim = simulate(site_truth, drone, r["path"], r["wait_before"], t0, mass, speed_policy, vconst,
                   U=U, V=V, tax=tax)
    if sim is None:
        return None
    e_to, t_to = vertical_energy(site_truth, drone, a[0], a[1], st[0], t0, mass, up=True)
    t_arr = sim["t"][-1]
    e_ld, t_ld = vertical_energy(site_truth, drone, b[0], b[1], gl[0], t_arr, mass, up=False)
    return dict(plan=r, sim=sim, e_takeoff=e_to, e_land=e_ld, t_takeoff=t_to, t_land=t_ld,
                e_total=e_to + sim["e"][-1] + e_ld, t_end=t_arr + t_ld + t_to,
                length=path_length(site_plan, r["path"]))


def run_round_trip(planner, mission: Mission, mode=1, speed_policy="opt", vconst=10.0, site_truth=None, **pkw):
    """계획(planner.site, 예보) → 실제(site_truth) 바람에서 평가."""
    sp = planner.site
    st = site_truth or sp
    d = planner.drone
    m_out, m_back = mission.masses(d.m_empty)
    out = _leg(planner, sp, st, d, mission.hub, mission.dst, mission.t_dep, m_out, mode, speed_policy, vconst,
               U=st.U, V=st.V, tax=st.t_axis, **pkw)
    if out is None:
        return None
    t_back = out["t_end"] + mission.ground_time
    back = _leg(planner, sp, st, d, mission.dst, mission.hub, t_back, m_back, mode, speed_policy, vconst,
                U=st.U, V=st.V, tax=st.t_axis, **pkw)
    if back is None:
        return None
    E = out["e_total"] + back["e_total"]
    soc = soc_drop(E, d)
    return dict(out=out, back=back, E=E, soc_used=soc, soc_end=100 - soc,
                feasible=(100 - soc) >= mission.reserve,
                t_total=back["t_end"] - mission.t_dep,
                soc_out=soc_drop(out["e_total"], d), soc_back=soc_drop(back["e_total"], d),
                L=out["length"] + back["length"])


# ------------------------------------------------------------------ 롤링 호라이즌 재계획
def run_rolling(planner_fcst, site_truth, drone, a, b, t0, mass, dt_replan=60.0, events=(), bias_update=True,
                max_iter=60):
    """비행 중 주기적(Δt)/이벤트 기반 재계획 (Receding-horizon, MPC형).

    - 예보장으로 계획 → 실제 바람에서 Δt 만큼 비행
    - 기체가 추정한 현재 위치 바람(실측 대용)과 예보 차이(bias)를 전 영역 예보에 보정
      (온보드 바람 추정 기반 간이 자료동화)
    - events: [(t_announce, fn(site))] 시간제 금지구역 발표 등 이벤트 → 즉시 재계획
    """
    sp = planner_fcst.site
    U0, V0 = sp.U.copy(), sp.V.copy()
    cur = sp.lowest_free_k(*a)
    goal = sp.lowest_free_k(*b)
    t = t0
    e_total, t_to = vertical_energy(site_truth, drone, a[0], a[1], cur[0], t0, mass, up=True)
    t += t_to
    trace, plans = [], []
    ev = sorted(events, key=lambda x: x[0])
    applied = set()
    du = dv = 0.0
    for it in range(max_iter):
        for n, (ta, fn) in enumerate(ev):
            if n not in applied and ta <= t:
                fn(sp)
                applied.add(n)
        if bias_update:
            sp.U[:] = U0 + du
            sp.V[:] = V0 + dv
        r = planner_fcst.plan(cur, goal, t_start=t, mass=mass, mode=1)
        if r is None:
            break
        plans.append(dict(t=t, path=r["path"].copy(), cur=cur, cpu=r["cpu_s"], pred_e=r["cost"]))
        # 실제 바람에서 비행
        sim = simulate(site_truth, drone, r["path"], r["wait_before"], t, mass, "opt",
                       U=site_truth.U, V=site_truth.V, tax=site_truth.t_axis)
        # 다음 재계획 시각: 주기 or 다음 이벤트 발표 시각
        t_next = t + dt_replan
        for n, (ta, fn) in enumerate(ev):
            if n not in applied and t < ta < t_next:
                t_next = ta
        idx = np.searchsorted(sim["t"], t_next)
        done = idx >= len(sim["t"]) - 1
        idx = min(idx, len(sim["t"]) - 1)
        seg = {k: v[: idx + 1] for k, v in sim.items()}
        seg["e"] = seg["e"] + e_total
        trace.append(seg)
        e_total = float(seg["e"][-1])
        t = float(seg["t"][-1])
        if done:
            break
        # 현재 위치를 가장 가까운 비행가능 노드로 스냅
        x, y, z = seg["x"][-1], seg["y"][-1], seg["z"][-1]
        i, j = int(x // sp.dx), int(y // sp.dx)
        k = int(round((z - sp.z[0]) / sp.dz))
        ks = np.where(sp.free[:, j, i])[0]
        if len(ks) == 0:
            break
        k = int(ks[np.argmin(abs(ks - k))])
        cur = (k, j, i)
        if bias_update:
            wt = _wind(site_truth.U, site_truth.V, site_truth.t_axis, t, k, j, i)
            wf = _wind(U0, V0, sp.t_axis, t, k, j, i)
            # 관측-예보 편차의 지수평활 (α=0.6) → 전 영역 예보장에 균일 보정
            du = 0.4 * du + 0.6 * (wt[0] - wf[0])
            dv = 0.4 * dv + 0.6 * (wt[1] - wf[1])
    if bias_update:
        sp.U[:] = U0
        sp.V[:] = V0
    e_ld, t_ld = vertical_energy(site_truth, drone, b[0], b[1], goal[0], t, mass, up=False)
    e_total += e_ld
    return dict(trace=trace, plans=plans, E=e_total, soc=soc_drop(e_total, drone), t_end=t + t_ld)
