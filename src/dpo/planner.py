"""WE-LT*: Wind-aware Energy Lazy Theta* (3D · 시공간 · 탑재중량 가변).

탐색 구조 (회의자료 개발순서 1→5 통합)
- 상태     : 3D 격자 노드 (고도 레이어 k, 행 j, 열 i) + 도착시각 t (라벨)
- 이웃     : 26방향 (수평 8 + 상승/하강 18)
- 비용     : 구간 소비에너지 [J] = 미터당 최소에너지(바람·질량·속도최적화) × 거리
             (time-dependent: 구간 진입 시각의 예보 바람을 시간 보간)
- any-angle: Lazy Theta* – 조부모(parent)에서 직접 연결(경로2)을 '먼저 가정'하고,
             노드 확장 시점에 가시선(LOS)·시간제 금지구역을 지연 검사.
             단, 바람장에서는 직선이 항상 최선이 아니므로(삼각부등식 불성립)
             경로1(격자 이웃)과 경로2 중 에너지가 작은 쪽을 택함 (energy-aware).
- 대기행동 : 시간제 비행금지구역(장애물 시간표)에 막히면, 직전 노드에서 호버링 대기 후
             통과하는 후보를 함께 생성 → '우회 vs 호버링'을 에너지로 자동 판단 (SIPP 개념)
- 휴리스틱 : h = 남은 3D 거리 × (최대 순풍 가정 시 미터당 최소에너지) → 허용적(admissible)

mode 0 = 최단거리(바람 무시, Lazy Theta*), mode 1 = 에너지 최적(WE-LT*)
"""
from __future__ import annotations

import heapq
import math
import time as _time

import numpy as np
from numba import njit

from .energy import power_w, edge_energy_per_m

INF = 1e30


# ---------------------------------------------------------------- 공통 유틸
@njit(cache=True)
def _wind(U, V, tax, t, k, j, i):
    nt = tax.shape[0]
    if nt == 1 or t <= tax[0]:
        return U[0, k, j, i], V[0, k, j, i]
    if t >= tax[nt - 1]:
        return U[nt - 1, k, j, i], V[nt - 1, k, j, i]
    a = 0
    while tax[a + 1] < t:
        a += 1
    w = (t - tax[a]) / (tax[a + 1] - tax[a])
    return (1 - w) * U[a, k, j, i] + w * U[a + 1, k, j, i], (1 - w) * V[a, k, j, i] + w * V[a + 1, k, j, i]


@njit(cache=True)
def seg_eval(k0, j0, i0, k1, j1, i1, t0, mode, check, free, tid, twin, U, V, tax, rho,
             m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst):
    """직선 구간 비용 평가.

    반환 (energy[J], duration[s], status, block_tnfz)
      status 0: 통과 가능, 1: 정적 장애물/금지구역 충돌, 2: 시간제 금지구역 충돌, 3: 비행불가(바람)
    mode 0: 최단거리 – energy 자리에 거리[m] 반환 (속도 vconst 가정하여 시간 계산)
    """
    Dx = (i1 - i0) * dx
    Dy = (j1 - j0) * dx
    Dz = (k1 - k0) * dz
    L = math.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
    if L < 1e-9:
        return 0.0, 0.0, 0, -1
    n = int(math.ceil(L / (0.5 * dx)))
    lp = L / n
    e = 0.0
    t = t0
    for s in range(n):
        f = (s + 0.5) / n
        kk = int(round(k0 + (k1 - k0) * f))
        jj = int(round(j0 + (j1 - j0) * f))
        ii = int(round(i0 + (i1 - i0) * f))
        if check:
            if not free[kk, jj, ii]:
                return INF, 0.0, 1, -1
            q = tid[jj, ii]
            if q >= 0 and t >= twin[q, 0] and t <= twin[q, 1]:
                return INF, 0.0, 2, q
        if mode == 0:
            e += lp
            t += lp / vconst
        else:
            wu, wv = _wind(U, V, tax, t, kk, jj, ii)
            epm, vg = edge_energy_per_m(Dx, Dy, Dz, wu, wv, m, rho[kk], prm, va_max, vz_up, vz_dn, speeds)
            if epm >= INF or vg <= 0:
                return INF, 0.0, 3, -1
            e += epm * lp
            lh = math.sqrt(Dx * Dx + Dy * Dy)
            if lh < 1e-6:
                t += lp / vg
            else:
                t += lp / (vg * L / lh)
    return e, t - t0, 0, -1


@njit(cache=True)
def _edge(k0, j0, i0, k1, j1, i1, t0, mode, allow_wait, free, tid, twin, U, V, tax, rho, m, prm, va_max, vz_up,
          vz_dn, speeds, dx, dz, vconst):
    """격자 이웃 간 이동 비용. 시간제 금지구역에 막히면 (허용 시) 출발 노드에서 호버링 대기 후 통과.
    반환 (cost, duration(대기 포함), wait, ok)"""
    e1, d1, st1, q1 = seg_eval(k0, j0, i0, k1, j1, i1, t0, mode, True, free, tid, twin, U, V, tax, rho, m, prm,
                               va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
    if st1 == 0:
        return e1, d1, 0.0, True
    if st1 == 2 and allow_wait:
        tw = twin[q1, 1] + 1.0 - t0
        e1w, d1w, st1w, q1w = seg_eval(k0, j0, i0, k1, j1, i1, t0 + tw, mode, True, free, tid, twin, U, V, tax,
                                       rho, m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
        if st1w == 0:
            if mode == 0:
                ew = tw * vconst  # 최단거리 모드: 대기를 '시간=거리 환산'으로 비교
            else:
                wu, wv = _wind(U, V, tax, t0, k0, j0, i0)
                ew = power_w(math.sqrt(wu * wu + wv * wv), 0.0, m, rho[k0], prm) * tw
            return ew + e1w, tw + d1w, tw, True
    return INF, 0.0, 0.0, False


@njit(cache=True)
def _search(start, goal, t_start, mode, free, tid, twin, U, V, tax, rho, m, prm, va_max, vz_up, vz_dn,
            speeds, dx, dz, vconst, e_lb, max_seg, allow_wait, any_angle, weight):
    nz, ny, nx = free.shape
    N = nz * ny * nx
    g = np.full(N, INF)
    tt = np.zeros(N)
    par = -np.ones(N, np.int64)
    wait = np.zeros(N)          # 이 노드로 가기 전 부모 노드에서 대기한 시간
    closed = np.zeros(N, np.bool_)
    lazy = np.zeros(N, np.bool_)
    nb = np.empty((26, 3), np.int64)
    c = 0
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            for d in (-1, 0, 1):
                if a == 0 and b == 0 and d == 0:
                    continue
                nb[c, 0], nb[c, 1], nb[c, 2] = a, b, d
                c += 1
    sk, sj, si = start
    gk, gj, gi = goal
    s_id = (sk * ny + sj) * nx + si
    g_id = (gk * ny + gj) * nx + gi
    g[s_id] = 0.0
    tt[s_id] = t_start
    par[s_id] = s_id
    heap = [(0.0, s_id)]
    n_exp = 0
    n_los = 0
    while len(heap) > 0:
        f, u = heapq.heappop(heap)
        if closed[u]:
            continue
        uk = u // (ny * nx)
        uj = (u // nx) % ny
        ui = u % nx
        # ---- Lazy Theta*: 지연 가시선 검사 (SetVertex)
        if lazy[u]:
            p = par[u]
            pk = p // (ny * nx); pj = (p // nx) % ny; pi = p % nx
            n_los += 1
            e2, d2, st, q = seg_eval(pk, pj, pi, uk, uj, ui, tt[p], mode, True, free, tid, twin, U, V, tax,
                                     rho, m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
            if st != 0:
                # 닫힌 이웃 중 최적 부모로 교체
                best = INF; bp = -1; bt = 0.0; bw = 0.0
                for c in range(26):
                    vk = uk + nb[c, 0]; vj = uj + nb[c, 1]; vi = ui + nb[c, 2]
                    if vk < 0 or vk >= nz or vj < 0 or vj >= ny or vi < 0 or vi >= nx:
                        continue
                    v = (vk * ny + vj) * nx + vi
                    if not closed[v]:
                        continue
                    e1, d1, w1, ok1 = _edge(vk, vj, vi, uk, uj, ui, tt[v], mode, allow_wait, free, tid, twin, U, V,
                                            tax, rho, m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
                    if ok1 and g[v] + e1 < best:
                        best = g[v] + e1; bp = v; bt = tt[v] + d1; bw = w1
                if bp < 0:
                    g[u] = INF
                    continue
                g[u] = best; par[u] = bp; tt[u] = bt; wait[u] = bw
            lazy[u] = False
        closed[u] = True
        n_exp += 1
        if u == g_id:
            break
        p = par[u]
        pk = p // (ny * nx); pj = (p // nx) % ny; pi = p % nx
        for c in range(26):
            vk = uk + nb[c, 0]; vj = uj + nb[c, 1]; vi = ui + nb[c, 2]
            if vk < 0 or vk >= nz or vj < 0 or vj >= ny or vi < 0 or vi >= nx:
                continue
            if not free[vk, vj, vi]:
                continue
            v = (vk * ny + vj) * nx + vi
            if closed[v]:
                continue
            # 경로1: u -> v (격자 이웃, 즉시 검사 / 막히면 호버링 대기 후보)
            e1, d1, w1, ok1 = _edge(uk, uj, ui, vk, vj, vi, tt[u], mode, allow_wait, free, tid, twin, U, V, tax,
                                    rho, m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
            cand_g = INF; cand_p = -1; cand_t = 0.0; cand_w = 0.0; cand_lazy = False
            if ok1:
                cand_g = g[u] + e1; cand_p = u; cand_t = tt[u] + d1; cand_w = w1
            # 경로2: parent(u) -> v (any-angle, 가시선은 지연 검사)
            if any_angle and p != u and wait[u] == 0.0:
                L2 = math.sqrt(((vi - pi) * dx) ** 2 + ((vj - pj) * dx) ** 2 + ((vk - pk) * dz) ** 2)
                if L2 <= max_seg:
                    e2, d2, st2, q2 = seg_eval(pk, pj, pi, vk, vj, vi, tt[p], mode, False, free, tid, twin, U, V,
                                               tax, rho, m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, vconst)
                    if st2 == 0 and g[p] + e2 <= cand_g:
                        cand_g = g[p] + e2; cand_p = p; cand_t = tt[p] + d2; cand_w = 0.0; cand_lazy = True
            if cand_p >= 0 and cand_g < g[v]:
                g[v] = cand_g; par[v] = cand_p; tt[v] = cand_t; wait[v] = cand_w; lazy[v] = cand_lazy
                dxg = (gi - vi) * dx; dyg = (gj - vj) * dx; dzg = (gk - vk) * dz
                h = math.sqrt(dxg * dxg + dyg * dyg + dzg * dzg) * e_lb
                heapq.heappush(heap, (cand_g + weight * h, v))
    return g, tt, par, wait, n_exp, n_los


# ---------------------------------------------------------------- 파이썬 래퍼
class Planner:
    def __init__(self, site, drone, speeds=None):
        self.site = site
        self.drone = drone
        self.speeds = np.asarray(speeds if speeds is not None else np.arange(3.0, 20.01, 0.5), float)
        self.rho = site.rho_layers()

    def energy_lower_bound(self, m):
        """허용적 휴리스틱 계수: 최대 풍속을 정면 순풍으로 받는다고 가정한 미터당 최소에너지."""
        wmax = float(np.sqrt(self.site.U ** 2 + self.site.V ** 2).max())
        prm = self.drone.params()
        best = INF
        for vg in self.speeds:
            for w in np.linspace(0, wmax, 25):
                va = abs(vg - w)
                if va > self.drone.va_max:
                    continue
                best = min(best, power_w(va, 0.0, m, float(self.rho.min()), prm) / vg)
        return best

    def plan(self, start, goal, t_start=0.0, mass=None, mode=1, any_angle=True, allow_wait=True,
             max_seg=400.0, weight=1.0, vconst=10.0):
        s = self.site
        d = self.drone
        m = d.m_empty if mass is None else mass
        e_lb = self.energy_lower_bound(m) if mode == 1 else 1.0
        tw = s.tnfz_win if len(s.tnfz_win) else np.zeros((1, 2)) - 1e9
        t0 = _time.perf_counter()
        g, tt, par, wait, n_exp, n_los = _search(
            np.array(start, np.int64), np.array(goal, np.int64), float(t_start), mode, s.free, s.tnfz_id, tw,
            s.U, s.V, s.t_axis, self.rho, m, d.params(), d.va_max, d.vz_up, d.vz_dn, self.speeds,
            s.dx, s.dz, vconst, e_lb, max_seg, allow_wait, any_angle, weight)
        cpu = _time.perf_counter() - t0
        nz, ny, nx = s.free.shape
        gid = (goal[0] * ny + goal[1]) * nx + goal[2]
        if g[gid] >= INF:
            return None
        path, waits = [], []
        u = gid
        while True:
            path.append((u // (ny * nx), (u // nx) % ny, u % nx))
            waits.append(wait[u])
            if par[u] == u:
                break
            u = par[u]
        path, waits = path[::-1], waits[::-1]
        return dict(path=np.array(path), wait_before=np.array(waits), cost=g[gid], t_arrive=tt[gid],
                    n_expanded=int(n_exp), n_los=int(n_los), cpu_s=cpu, mass=m, mode=mode)


# ---------------------------------------------------------------- 경로 평가(시뮬레이션)
def simulate(site, drone, path, waits, t_start, mass, speed_policy="opt", vconst=10.0, U=None, V=None,
             tax=None, step=12.5):
    """주어진 웨이포인트 경로를 '실제(또는 예보) 바람'에서 비행했을 때의 에너지/시간 이력.

    speed_policy: 'opt' 구간별 미터당 에너지 최소 대지속도 / 'const' 일정 대지속도(vconst)
    반환: dict(t, e, x, y, z, ws, vg, P)
    """
    U = site.U if U is None else U
    V = site.V if V is None else V
    tax = site.t_axis if tax is None else tax
    rho = site.rho_layers()
    prm = drone.params()
    spd = np.arange(3.0, 20.01, 0.5) if speed_policy == "opt" else np.array([vconst])
    t, e = t_start, 0.0
    rec = []
    pts = np.array(path, float)
    for n in range(len(pts) - 1):
        if waits[n + 1] > 0:  # 부모 노드에서 호버링 대기
            k, j, i = pts[n].astype(int)
            wu, wv = _wind(U, V, tax, t, k, j, i)
            P = power_w(math.hypot(wu, wv), 0.0, mass, rho[k], prm)
            ns = max(int(waits[n + 1] // 5), 1)
            for _ in range(ns):
                dt = waits[n + 1] / ns
                e += P * dt; t += dt
                rec.append((t, e, (i + .5) * site.dx, (j + .5) * site.dx, site.z[k], math.hypot(wu, wv), 0.0, P))
        k0, j0, i0 = pts[n]
        k1, j1, i1 = pts[n + 1]
        Dx, Dy, Dz = (i1 - i0) * site.dx, (j1 - j0) * site.dx, (k1 - k0) * site.dz
        L = math.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
        lh = math.hypot(Dx, Dy)
        ns = max(int(math.ceil(L / step)), 1)
        for s in range(ns):
            f = (s + .5) / ns
            kk, jj, ii = int(round(k0 + (k1 - k0) * f)), int(round(j0 + (j1 - j0) * f)), int(round(i0 + (i1 - i0) * f))
            wu, wv = _wind(U, V, tax, t, kk, jj, ii)
            sp = spd
            if speed_policy == "const":
                # 일정 대지속도 정책: 대기속도 한계/상승률 한계를 넘으면 가능한 최대 속도로 감속
                sp = np.arange(min(vconst, 20.0), 2.9, -0.5)
                for v in sp:
                    epm, vg = edge_energy_per_m(Dx, Dy, Dz, wu, wv, mass, rho[kk], prm, drone.va_max,
                                                drone.vz_up, drone.vz_dn, np.array([v]))
                    if epm < INF:
                        break
            else:
                epm, vg = edge_energy_per_m(Dx, Dy, Dz, wu, wv, mass, rho[kk], prm, drone.va_max,
                                            drone.vz_up, drone.vz_dn, sp)
            lp = L / ns
            if epm >= INF:
                return None
            v3 = vg if lh < 1e-6 else vg * L / lh
            dt = lp / v3
            e += epm * lp; t += dt
            rec.append((t, e, (i0 + (i1 - i0) * f + .5) * site.dx, (j0 + (j1 - j0) * f + .5) * site.dx,
                        site.z[0] + (k0 + (k1 - k0) * f) * site.dz, math.hypot(wu, wv), vg, epm * lp / dt))
    r = np.array(rec)
    return dict(t=r[:, 0], e=r[:, 1], x=r[:, 2], y=r[:, 3], z=r[:, 4], ws=r[:, 5], vg=r[:, 6], P=r[:, 7])


def vertical_energy(site, drone, x, y, k, t, mass, up=True):
    """이륙(지면→첫 레이어) / 착륙 에너지 [J], 소요시간 [s]."""
    i, j = int(x // site.dx), int(y // site.dx)
    h = site.z[k] - site.ground[j, i]
    wu, wv = site.wind_at(t, k, j, i)
    vz = drone.vz_up if up else -drone.vz_dn
    P = power_w(math.hypot(wu, wv), vz, mass, 1.2, drone.params())
    dt = h / abs(vz)
    return P * dt, dt


def path_length(site, path):
    p = np.array(path, float)
    d = np.diff(p, axis=0) * np.array([site.dz, site.dx, site.dx])
    return float(np.sqrt((d ** 2).sum(1)).sum())
