"""이벤트 기반 롤링 재계획: 장애물 궤적이 '바뀌었을 때만' 다시 계획.

가정: 장애물 궤적은 자주 바뀌지 않는다 → 주기적 전체 재계획 대신
  ① 변경 발표 시각 t_a 에 새 장애물 집합 O_{k}를 받는다 (ObstacleTimeline)
  ② 남은 계획 궤적을 O_k 의 위험 시간구간 표로 '검사만' 한다 (표 조회, 수 ms)
  ③ 충돌이면 현재 위치·시각에서 3D 시공간 재계획 (WE-SIPP-Theta*), 아니면 기존 경로 유지
  ④ 재계획 계산 지연(latency) 동안은 기존 경로로 비행한 위치에서 새 계획을 시작
선택: 장애물이 사라지거나 줄어든 경우 '기회적 재계획'(opportunistic) – 더 싼 경로가 있으면 교체

정책 비교:  once(1회 계획) · event(변경 시 검사→필요 시 재계획) · periodic(주기마다 무조건 재계획)
            · oracle(처음부터 실제 미래 궤적을 앎 – 하한)
"""
from __future__ import annotations

import copy
import math
import time as _time
from dataclasses import dataclass, field

import numpy as np

from .energy import soc_drop
from .obstacles import DynObstacle, build_interval_table
from .planner import simulate, vertical_energy
from .st_planner import STPlanner, check_path_conflicts, _unsafe, _iv_index


# ------------------------------------------------------------------ 장애물 변경 이력
@dataclass
class ObstacleTimeline:
    """revisions: [(t_announce, [DynObstacle...]), ...]  (t 는 출발 기준 초, 첫 항목은 t=0)."""
    revisions: list = field(default_factory=list)

    def sorted(self):
        return sorted(self.revisions, key=lambda r: r[0])

    def known_at(self, t):
        """시각 t 에 알려진(가장 최근 발표된) 장애물 집합."""
        cur = []
        for ta, obs in self.sorted():
            if ta <= t + 1e-9:
                cur = obs
        return cur

    def announcements(self):
        return [ta for ta, _ in self.sorted()][1:]

    def truth(self):
        """실제 궤적 = 각 판(revision)을 '다음 발표 전까지'로 잘라 합친 집합."""
        revs = self.sorted()
        out = []
        for q, (ta, obs) in enumerate(revs):
            tb = revs[q + 1][0] if q + 1 < len(revs) else math.inf
            for o in obs:
                c = copy.deepcopy(o)
                w0, w1 = o.window()
                c.t_start, c.t_end = max(w0, ta), min(w1, tb)
                if c.t_end > c.t_start:
                    c.name = o.name
                    out.append(c)
        return out


# ------------------------------------------------------------------ 남은 경로 충돌 검사 (표 조회)
def remaining_conflict(site, table, sim, t_from):
    """시뮬레이션 궤적 중 t >= t_from 구간이 위험 시간구간과 겹치는지 (첫 충돌 시각 반환, 없으면 None)."""
    nz, ny, nx = site.free.shape
    for t, x, y, z in zip(sim["t"], sim["x"], sim["y"], sim["z"]):
        if t < t_from:
            continue
        i, j = int(x // site.dx), int(y // site.dx)
        k = int(round((z - site.z[0]) / site.dz))
        if not (0 <= k < nz and 0 <= j < ny and 0 <= i < nx):
            continue
        if _unsafe((k * ny + j) * nx + i, t, table["off"], table["cnt"], table["lo"], table["hi"]):
            return float(t)
    return None


def _cut(sim, t_end):
    m = sim["t"] <= t_end
    return {k: v[m] for k, v in sim.items()}


def _snap(site, table, x, y, z, t):
    """현재 위치를 비행 가능하고 시각 t 에 안전한 가장 가까운 격자 노드로 스냅."""
    nz, ny, nx = site.free.shape
    i0, j0 = int(x // site.dx), int(y // site.dx)
    k0 = int(round((z - site.z[0]) / site.dz))
    best = None
    for dk in (0, -1, 1, -2, 2):
        for dj in (0, -1, 1):
            for di in (0, -1, 1):
                k, j, i = k0 + dk, j0 + dj, i0 + di
                if not (0 <= k < nz and 0 <= j < ny and 0 <= i < nx) or not site.free[k, j, i]:
                    continue
                n = (k * ny + j) * nx + i
                if _iv_index(n, t, table["off"], table["cnt"], table["lo"], table["hi"]) < 0:
                    continue
                d = abs(dk) * site.dz + math.hypot(dj, di) * site.dx
                if best is None or d < best[0]:
                    best = (d, (k, j, i))
    return None if best is None else best[1]


# ------------------------------------------------------------------ 실행기
def run_obstacle_replanning(site_plan, site_truth, drone, a, b, t_dep, mass, timeline: ObstacleTimeline,
                            policy="event", period=30.0, latency=3.0, budget=np.inf, max_wait=180.0, weight=1.2,
                            opportunistic=False, opp_gain=0.02, max_replans=40, eps=None, inflate_const=None,
                            observe=None, initial_plan=None, replan_weight=None, fallbacks=()):
    """한 구간(a→b)을 비행하며 정책에 따라 재계획. 장애물 시각은 출발(t_dep) 기준.

    fallbacks: 재관측 모드에서 현재 위치가 이미 팽창 영역 안이거나 대안이 없을 때 차례로 시도할
               덜 보수적인 장애물 함수 목록 (t_rel → 장애물 목록). 예: [ε 를 키운 튜브, 팽창 없음].
               그대로 진행해 충돌하는 대신 위험 한도를 단계적으로 완화해 회피 경로를 찾는다.

    반환 dict(trace(실제 비행 궤적), events(재계획/검사 기록), E, soc, conflicts(실제 궤적 vs 실제 장애물), cpu_total)
    """
    SP = STPlanner(site_plan, drone)
    truth = timeline.truth()
    st, gl = site_plan.lowest_free_k(*a), site_plan.lowest_free_k(*b)
    e_to, t_to = vertical_energy(site_truth, drone, a[0], a[1], st[0], t_dep, mass, up=True)
    t = t_dep + t_to
    known = truth if policy == "oracle" else timeline.known_at(0.0)
    SP.set_obstacles(known, t_dep, eps=eps, inflate_const=inflate_const)
    t0 = _time.perf_counter()
    if initial_plan is not None:            # 같은 사전 정보로 만든 초기 계획 재사용 (몬테카를로용)
        r = initial_plan
    else:
        r = SP.plan(st, gl, t_start=t, mass=mass, budget=budget, max_wait=max_wait, weight=weight)
    cpu = _time.perf_counter() - t0
    events = [dict(t=0.0, kind="initial", replanned=True, cpu=cpu, reason="출발 전 계획")]
    if r is None:
        return dict(feasible=False, events=events)
    plans = [dict(t=t - t_dep, path=r["path"])]
    t += r.get("ground_delay", 0.0)          # 출발 노드가 위험구간이면 계획기가 지상 대기 후 이륙
    sim = simulate(site_truth, drone, r["path"], r["wait_before"], t, mass, U=site_truth.U, V=site_truth.V,
                   tax=site_truth.t_axis)
    pieces = []
    e_acc = e_to
    cpu_total = cpu
    # 결정 시각 목록
    if policy == "event":
        decisions = [t_dep + ta for ta in timeline.announcements()]
    elif policy == "observe":   # 주기적 재관측 → 검사 → 충돌 시에만 재계획 (이벤트 트리거)
        decisions = list(t + period * np.arange(1, 400))
    elif policy == "periodic":
        decisions = list(t + period * np.arange(1, 400))
    else:
        decisions = []
    di = 0
    n_rep = 0
    while True:
        t_end_plan = sim["t"][-1]
        if di >= len(decisions) or decisions[di] >= t_end_plan or n_rep >= max_replans:
            pieces.append(sim)
            break
        # 이륙 상승 중(순항 시작 전)이나 직전 재계획 지연 중에 발표된 변경은 지금 즉시 처리
        td = max(decisions[di], t)
        di += 1
        # ② 새로 알려진 장애물로 남은 경로 검사
        new_known = timeline.known_at(td - t_dep)
        changed = (policy != "event") or (new_known is not known)
        known = new_known
        tc = _time.perf_counter()
        if observe is not None:                      # 재관측: 실제 상태로 예측을 갱신하고 불확실성 재설정
            known = observe(td - t_dep)
        table = build_interval_table(site_plan, known, t_offset=t_dep, eps=eps, inflate_const=inflate_const)
        hit = remaining_conflict(site_plan, table, sim, td)
        check_cpu = _time.perf_counter() - tc
        need = policy == "periodic" or hit is not None
        ev = dict(t=td - t_dep, kind={"event": "announce", "observe": "observe"}.get(policy, "periodic"),
                  conflict_at=None if hit is None
                  else hit - t_dep, check_ms=check_cpu * 1000, replanned=False, changed=changed)
        if not need and not opportunistic:
            events.append(ev)
            continue
        # ④ 계산 지연 동안 기존 경로 비행 → 그 위치에서 재계획
        t_sw = td + (latency if td > t else 0.0)
        if t_sw >= t_end_plan:
            events.append(ev); continue
        q = int(np.searchsorted(sim["t"], t_sw))
        x, y, z = sim["x"][q], sim["y"][q], sim["z"][q]
        t_sw = float(sim["t"][q])
        used = sim["e"][q] + e_acc
        t0 = _time.perf_counter()
        r2, level = None, 0
        tables = [table] + [None] * len(fallbacks)
        for level in range(len(tables)):
            if tables[level] is None:
                tables[level] = build_interval_table(site_plan, fallbacks[level - 1](td - t_dep), t_offset=t_dep,
                                                     eps=eps, inflate_const=inflate_const)
            SP.table = tables[level]
            cur = _snap(site_plan, tables[level], x, y, z, t_sw)
            if cur is None:
                continue
            r2 = SP.plan(cur, gl, t_start=t_sw, mass=mass, budget=budget - used, max_wait=max_wait,
                         weight=replan_weight or weight)
            if r2 is not None:
                break
        SP.table = table
        rc = _time.perf_counter() - t0
        cpu_total += rc
        ev["cpu"] = rc
        ev["fallback"] = level if r2 is not None else None
        if r2 is None:
            ev["reason"] = ("현재 위치가 위험구간 – 재계획 불가" if cur is None else "허용 범위 내 대안 없음") + \
                " – 기존 경로 유지"
            events.append(ev); continue
        if len(r2["path"]) < 2:                     # 이미 목표 노드 – 재계획할 구간 없음
            ev["reason"] = "목표 노드 도달 – 기존 경로 유지"
            events.append(ev); continue
        sim2 = simulate(site_truth, drone, r2["path"], r2["wait_before"], t_sw + r2.get("ground_delay", 0.0), mass,
                        U=site_truth.U, V=site_truth.V,
                        tax=site_truth.t_axis)
        if not need:  # 기회적 재계획: 충분히 싸질 때만 교체
            if sim2["e"][-1] >= (sim["e"][-1] - sim["e"][q]) * (1 - opp_gain):
                ev["reason"] = "기회적 검토: 이득 작음 – 유지"
                events.append(ev); continue
        ev.update(replanned=True, reason="충돌 예상 → 재계획" if hit is not None else "주기 재계획", t_switch=t_sw - t_dep)
        events.append(ev)
        n_rep += 1
        cut = _cut(sim, t_sw)
        pieces.append(cut)
        e_acc += float(cut["e"][-1]) if len(cut["e"]) else 0.0
        sim = sim2
        t = t_sw
        plans.append(dict(t=t_sw - t_dep, path=r2["path"]))
    # 이어 붙이기 (에너지 누적)
    trace = {k: [] for k in pieces[0]}
    e_off = e_to
    for p in pieces:
        for k in trace:
            trace[k].append(p[k] + (e_off if k == "e" else 0.0))
        if len(p["e"]):
            e_off += float(p["e"][-1])
    trace = {k: np.concatenate(v) for k, v in trace.items()}
    e_ld, t_ld = vertical_energy(site_truth, drone, b[0], b[1], gl[0], float(trace["t"][-1]), mass, up=False)
    E = float(trace["e"][-1]) + e_ld
    cf = check_path_conflicts(site_plan, trace, truth, t_dep)
    return dict(feasible=True, trace=trace, plans=plans, events=events, E=E, soc=soc_drop(E, drone),
                T=float(trace["t"][-1] + t_ld - t_dep), conflicts=cf, cpu_total=cpu_total,
                n_replans=sum(1 for e in events if e.get("replanned") and e["kind"] != "initial"))
