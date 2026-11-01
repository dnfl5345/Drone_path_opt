"""3D 시공간 에너지 계획기: WE-SIPP-Θ* (동적 장애물 + 바람 + 에너지).

상태      s = (노드 n=(k,j,i), 안전구간 ι)   ← SIPP [Phillips & Likhachev 2011]
            안전구간 = 장애물 위험 시간구간의 여집합 (obstacles.build_interval_table)
이동      26방향 이웃 + (필요 시) 현재 노드에서 호버링 대기 후 출발
            · 막히면 대기: τ ∈ {Δ, 2Δ, …} ≤ min(최대대기, 현재 안전구간 끝)
            · 다음 안전구간 진입: 출발 = (이웃의 안전구간 시작 − 비행시간) 로 맞춰 대기   ← SIPP 핵심
지름길    any-angle (조부모 직결): 구간 전체를 '시각과 함께' 샘플링해 정적/동적 충돌 동시 검사
            (Any-angle SIPP 개념 [Yakovlev & Andreychuk, ICAPS 2017]) + 에너지 비교 채택(WE-LT*)
비용      구간 에너지(바람·질량·속도 최적화) + 대기 호버링 에너지
허용 범위 ("가능한 범위에서만 허용")
            · 공역: 지면+30~150 m, 건물 이격, 정적 금지구역      · 대기속도 ≤ Va,max, 상승/하강률 한계
            · 대기 1회 ≤ max_wait, 안전구간을 벗어나는 대기 금지   · 배터리: g + h ≤ 예산(예비 SOC 제외) 이면만 확장
"""
from __future__ import annotations

import heapq
import math
import time as _time

import numpy as np
from numba import njit

from .energy import power_w, edge_energy_per_m
from .planner import _wind

INF = 1e30


# ------------------------------------------------------------------ 안전구간 조회
@njit(cache=True)
def _unsafe(n, t, off, cnt, lo, hi):
    o = off[n]
    if o < 0:
        return False
    for q in range(cnt[n]):
        if lo[o + q] <= t <= hi[o + q]:
            return True
    return False


@njit(cache=True)
def _iv_index(n, t, off, cnt, lo, hi):
    """시각 t 가 속한 안전구간 번호 (위험구간 안이면 -1)."""
    o = off[n]
    if o < 0:
        return 0
    idx = 0
    for q in range(cnt[n]):
        if lo[o + q] <= t <= hi[o + q]:
            return -1
        if hi[o + q] < t:
            idx = q + 1
    return idx


@njit(cache=True)
def _iv_bounds(n, idx, off, cnt, lo, hi):
    o = off[n]
    if o < 0:
        return -INF, INF
    a = -INF if idx == 0 else hi[o + idx - 1]
    b = INF if idx >= cnt[n] else lo[o + idx]
    return a, b


# ------------------------------------------------------------------ 시공간 구간 평가
@njit(cache=True)
def seg_st(k0, j0, i0, k1, j1, i1, t0, free, off, cnt, lo, hi, U, V, tax, rho, m, prm, va_max, vz_up, vz_dn,
           speeds, dx, dz, mode, vconst):
    """직선 구간을 시각과 함께 샘플링: 정적 충돌(1), 동적 충돌(2), 비행불가(3), 통과(0).
    mode 0: 거리 비용(최단), 1: 에너지."""
    Dx = (i1 - i0) * dx; Dy = (j1 - j0) * dx; Dz = (k1 - k0) * dz
    L = math.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
    if L < 1e-9:
        return 0.0, 0.0, 0
    lh = math.sqrt(Dx * Dx + Dy * Dy)
    n = int(math.ceil(L / (0.5 * dx)))
    lp = L / n
    e = 0.0
    t = t0
    ny = free.shape[1]; nx = free.shape[2]
    for s in range(n):
        f = (s + 0.5) / n
        kk = int(round(k0 + (k1 - k0) * f)); jj = int(round(j0 + (j1 - j0) * f)); ii = int(round(i0 + (i1 - i0) * f))
        if not free[kk, jj, ii]:
            return INF, 0.0, 1
        if mode == 0:
            dt = lp / vconst
            de = lp
        else:
            wu, wv = _wind(U, V, tax, t, kk, jj, ii)
            epm, vg = edge_energy_per_m(Dx, Dy, Dz, wu, wv, m, rho[kk], prm, va_max, vz_up, vz_dn, speeds)
            if epm >= INF or vg <= 0:
                return INF, 0.0, 3
            dt = lp / vg if lh < 1e-6 else lp / (vg * L / lh)
            de = epm * lp
        if _unsafe((kk * ny + jj) * nx + ii, t + 0.5 * dt, off, cnt, lo, hi):
            return INF, 0.0, 2
        e += de
        t += dt
    if _unsafe((k1 * ny + j1) * nx + i1, t, off, cnt, lo, hi):
        return INF, 0.0, 2
    return e, t - t0, 0


@njit(cache=True)
def _hover_power(U, V, tax, t, k, j, i, m, rho, prm, mode):
    if mode == 0:
        return 1.0
    wu, wv = _wind(U, V, tax, t, k, j, i)
    return power_w(math.sqrt(wu * wu + wv * wv), 0.0, m, rho[k], prm)


# ------------------------------------------------------------------ 휴리스틱
@njit(cache=True)
def _heur(di, dj, dk, dx, dz, e_lb, Htab):
    """방위 의존 하한: 목표 방향 φ 로 1 m 전진하는 데 드는 최소 에너지 H(φ) × 수평거리.
    H(φ) = min_θ e_min(θ) / cos(θ − φ)  (cos>0) – 바람장 전체의 방위별 최소 에너지로 구성 → (근사)허용적."""
    Dh = math.sqrt((di * dx) ** 2 + (dj * dx) ** 2)
    if Htab.shape[0] == 1 or Dh < 1e-9:
        return math.sqrt(Dh * Dh + (dk * dz) ** 2) * e_lb
    phi = math.atan2(di, dj)
    if phi < 0:
        phi += 2 * math.pi
    q = int(round(phi / (2 * math.pi) * Htab.shape[0])) % Htab.shape[0]
    return Dh * Htab[q]


# ------------------------------------------------------------------ 탐색
@njit(cache=True)
def _search_st(start, goal, t_start, free, off, cnt, lo, hi, maxi, U, V, tax, rho, m, prm, va_max, vz_up, vz_dn,
               speeds, dx, dz, mode, vconst, e_lb, budget, max_wait, wait_step, any_angle, max_seg, Htab, weight):
    nz, ny, nx = free.shape
    N = nz * ny * nx
    S = N * maxi
    g = np.full(S, INF); tt = np.zeros(S); par = -np.ones(S, np.int64)
    wait = np.zeros(S); closed = np.zeros(S, np.bool_)
    nb = np.empty((26, 3), np.int64)
    c = 0
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            for d in (-1, 0, 1):
                if a == 0 and b == 0 and d == 0:
                    continue
                nb[c, 0], nb[c, 1], nb[c, 2] = a, b, d
                c += 1
    sk, sj, si = start; gk, gj, gi = goal
    s_node = (sk * ny + sj) * nx + si
    g_node = (gk * ny + gj) * nx + gi
    s_iv = _iv_index(s_node, t_start, off, cnt, lo, hi)
    if s_iv < 0:
        return g, tt, par, wait, -1, 0
    s0 = s_node * maxi + s_iv
    g[s0] = 0.0; tt[s0] = t_start; par[s0] = s0
    heap = [(0.0, s0)]
    n_exp = 0
    goal_state = -1
    while len(heap) > 0:
        f, s = heapq.heappop(heap)
        if closed[s]:
            continue
        closed[s] = True
        n_exp += 1
        u = s // maxi; ui_v = s % maxi
        if u == g_node:
            goal_state = s
            break
        uk = u // (ny * nx); uj = (u // nx) % ny; ui = u % nx
        tu = tt[s]
        a_u, b_u = _iv_bounds(u, ui_v, off, cnt, lo, hi)
        ph = _hover_power(U, V, tax, tu, uk, uj, ui, m, rho, prm, mode)
        if mode == 0:
            ph = vconst  # 최단거리 모드: 대기 1 s = vconst m 로 환산
        p = par[s]
        pn = p // maxi
        pk = pn // (ny * nx); pj = (pn // nx) % ny; pi = pn % nx
        for c in range(26):
            vk = uk + nb[c, 0]; vj = uj + nb[c, 1]; vi = ui + nb[c, 2]
            if vk < 0 or vk >= nz or vj < 0 or vj >= ny or vi < 0 or vi >= nx:
                continue
            if not free[vk, vj, vi]:
                continue
            v = (vk * ny + vj) * nx + vi
            hv = _heur(gi - vi, gj - vj, gk - vk, dx, dz, e_lb, Htab)
            # ---- 경로1: u → v (즉시 출발, 막히면 대기)
            e1, d1, st1 = seg_st(uk, uj, ui, vk, vj, vi, tu, free, off, cnt, lo, hi, U, V, tax, rho, m, prm,
                                 va_max, vz_up, vz_dn, speeds, dx, dz, mode, vconst)
            w1 = 0.0
            if st1 == 2:
                tw = wait_step
                while tw <= max_wait and tu + tw < b_u:
                    e1, d1, st1 = seg_st(uk, uj, ui, vk, vj, vi, tu + tw, free, off, cnt, lo, hi, U, V, tax, rho,
                                         m, prm, va_max, vz_up, vz_dn, speeds, dx, dz, mode, vconst)
                    if st1 == 0:
                        w1 = tw
                        break
                    if st1 != 2:
                        break
                    tw += wait_step
            if st1 == 0:
                ta = tu + w1 + d1
                iv = _iv_index(v, ta, off, cnt, lo, hi)
                if iv >= 0:
                    cg = g[s] + ph * w1 + e1
                    sv = v * maxi + iv
                    if cg + hv <= budget and not closed[sv] and (cg < g[sv] - 1e-9 or (abs(cg - g[sv]) < 1e-9 and ta < tt[sv])):
                        g[sv] = cg; tt[sv] = ta; par[sv] = s; wait[sv] = w1
                        heapq.heappush(heap, (cg + weight * hv, sv))
            # ---- 경로1': 이웃의 '다음 안전구간'으로 진입 (장애물이 지나간 뒤 출발) – SIPP
            if off[v] >= 0 and d1 > 0:
                for q in range(cnt[v]):
                    a_v = hi[off[v] + q] + 1.0          # 다음 안전구간 시작
                    t_dep = a_v - d1
                    if t_dep <= tu + w1 + 1e-6:
                        continue
                    tw = t_dep - tu
                    if tw > max_wait or t_dep >= b_u:
                        break
                    e3, d3, st3 = seg_st(uk, uj, ui, vk, vj, vi, t_dep, free, off, cnt, lo, hi, U, V, tax, rho, m,
                                         prm, va_max, vz_up, vz_dn, speeds, dx, dz, mode, vconst)
                    if st3 != 0:
                        continue
                    ta = t_dep + d3
                    iv = _iv_index(v, ta, off, cnt, lo, hi)
                    if iv < 0:
                        continue
                    cg = g[s] + ph * tw + e3
                    sv = v * maxi + iv
                    if cg + hv <= budget and not closed[sv] and (cg < g[sv] - 1e-9 or (abs(cg - g[sv]) < 1e-9 and ta < tt[sv])):
                        g[sv] = cg; tt[sv] = ta; par[sv] = s; wait[sv] = tw
                        heapq.heappush(heap, (cg + weight * hv, sv))
            # ---- 경로2: any-angle (조부모 직결, 대기 없이)
            if any_angle and p != s and wait[s] == 0.0:
                L2 = math.sqrt(((vi - pi) * dx) ** 2 + ((vj - pj) * dx) ** 2 + ((vk - pk) * dz) ** 2)
                if L2 <= max_seg:
                    e2, d2, st2 = seg_st(pk, pj, pi, vk, vj, vi, tt[p], free, off, cnt, lo, hi, U, V, tax, rho, m,
                                         prm, va_max, vz_up, vz_dn, speeds, dx, dz, mode, vconst)
                    if st2 == 0:
                        ta = tt[p] + d2
                        iv = _iv_index(v, ta, off, cnt, lo, hi)
                        if iv >= 0:
                            cg = g[p] + e2
                            sv = v * maxi + iv
                            if cg + hv <= budget and not closed[sv] and cg < g[sv] - 1e-9:
                                g[sv] = cg; tt[sv] = ta; par[sv] = p; wait[sv] = 0.0
                                heapq.heappush(heap, (cg + weight * hv, sv))
    return g, tt, par, wait, goal_state, n_exp


@njit(cache=True)
def _emin_table(w, th, m, rho, prm, va_max, vz_up, vz_dn, speeds):
    out = np.full(th.shape[0], INF)
    for q in range(th.shape[0]):
        ex, ey = math.sin(th[q]), math.cos(th[q])
        for r in range(w.shape[0]):
            e, v = edge_energy_per_m(ex, ey, 0.0, w[r, 0], w[r, 1], m, rho, prm, va_max, vz_up, vz_dn, speeds)
            if e < out[q]:
                out[q] = e
    return out


# ------------------------------------------------------------------ 파이썬 래퍼
class STPlanner:
    """3D 시공간 계획기. site 는 wind_field.Site3D (또는 선형 바람 변형)."""

    def __init__(self, site, drone, speeds=None):
        self.site = site
        self.drone = drone
        self.speeds = np.asarray(speeds if speeds is not None else np.arange(3.0, 20.01, 0.5), float)
        self.rho = site.rho_layers()
        self.table = None

    def set_obstacles(self, obstacles, t_offset, include_tnfz=True):
        from .obstacles import build_interval_table
        t0 = _time.perf_counter()
        self.table = build_interval_table(self.site, obstacles, t_offset=t_offset,
                                          extra=True if include_tnfz else None)
        self.table_time = _time.perf_counter() - t0
        return self.table

    def _empty_table(self):
        N = self.site.free.size
        return dict(off=-np.ones(N, np.int64), cnt=np.zeros(N, np.int32), lo=np.zeros(1), hi=np.zeros(1), maxi=1,
                    n_cells=0)

    def energy_lower_bound(self, m):
        from .planner import Planner
        return Planner.energy_lower_bound(self, m)

    def heading_table(self, m, n_head=72, stride=6, safety=0.97):
        """방위별 최소 미터당 에너지 e_min(θ) (바람장 표본 전체 최소) → H(φ) 표.
        표본(수평 stride 셀, 전 레이어·전 시각)의 최소에 안전계수를 곱해 근사 허용성 확보."""
        key = (round(m, 3), n_head, stride)
        if getattr(self, "_htab", None) is not None and self._htab[0] == key:
            return self._htab[1]
        from .layered import _build_slope_lut  # noqa: F401  (numba 예열 공유)
        s = self.site
        U = s.U[:, :, ::stride, ::stride]; V = s.V[:, :, ::stride, ::stride]
        free = s.free[:, ::stride, ::stride]
        uu = U[:, free].reshape(-1); vv = V[:, free].reshape(-1)
        # 바람 벡터 표본을 0.25 m/s 격자로 중복 제거
        w = np.unique(np.round(np.c_[uu, vv] * 4) / 4, axis=0)
        th = 2 * np.pi * np.arange(n_head) / n_head
        emin = _emin_table(w, th, m, float(self.rho.min()), self.drone.params(), self.drone.va_max,
                           self.drone.vz_up, self.drone.vz_dn, self.speeds)
        H = np.full(n_head, np.inf)
        for q, phi in enumerate(th):
            c = np.cos(th - phi)
            ok = c > 1e-3
            H[q] = np.min(emin[ok] / c[ok])
        H *= safety
        self._htab = (key, H)
        return H

    def plan(self, start, goal, t_start=0.0, mass=None, mode=1, budget=np.inf, max_wait=180.0, wait_step=3.0,
             any_angle=True, max_seg=400.0, vconst=10.0, ignore_obstacles=False, heading_heuristic=True, weight=1.0,
             **_):
        s = self.site
        d = self.drone
        m = d.m_empty if mass is None else mass
        T = self._empty_table() if (self.table is None or ignore_obstacles) else self.table
        e_lb = self.energy_lower_bound(m) if mode == 1 else 1.0
        Htab = self.heading_table(m) if (mode == 1 and heading_heuristic) else np.zeros(1)
        nz, ny, nx = s.free.shape
        # 출발 노드가 위험구간이면 지상 대기 후 이륙 (에너지 0, 시간만)
        n0 = (start[0] * ny + start[1]) * nx + start[2]
        ground_delay = 0.0
        tq = t_start
        for _ in range(200):
            if _iv_index(n0, tq, T["off"], T["cnt"], T["lo"], T["hi"]) >= 0:
                break
            tq += 5.0
        ground_delay = tq - t_start
        t0 = _time.perf_counter()
        g, tt, par, wait, gs, n_exp = _search_st(
            np.array(start, np.int64), np.array(goal, np.int64), float(tq), s.free, T["off"], T["cnt"], T["lo"],
            T["hi"], int(T["maxi"]), s.U, s.V, s.t_axis, self.rho, m, d.params(), d.va_max, d.vz_up, d.vz_dn,
            self.speeds, s.dx, s.dz, mode, vconst, e_lb, float(budget), float(max_wait), float(wait_step),
            any_angle, float(max_seg), Htab, float(weight))
        cpu = _time.perf_counter() - t0
        if gs < 0:
            return None
        maxi = int(T["maxi"])
        path, waits, times = [], [], []
        q = gs
        while True:
            n = q // maxi
            path.append((n // (ny * nx), (n // nx) % ny, n % nx))
            waits.append(wait[q]); times.append(tt[q])
            if par[q] == q:
                break
            q = par[q]
        return dict(path=np.array(path[::-1]), wait_before=np.array(waits[::-1]), t_nodes=np.array(times[::-1]),
                    cost=g[gs], t_arrive=tt[gs], n_expanded=int(n_exp), cpu_s=cpu, mass=m, mode=mode,
                    ground_delay=ground_delay, n_iv_cells=int(T["n_cells"]), maxi=maxi)


def check_path_conflicts(site, sim, obstacles, t_offset):
    """시뮬레이션 궤적과 장애물(실제 형상)의 최소 이격 [m] 및 충돌 여부 (마진 제외)."""
    worst = np.inf
    hit = []
    for o in obstacles:
        for t, x, y, z in zip(sim["t"], sim["x"], sim["y"], sim["z"]):
            p = o.position(t - t_offset)
            if p is None:
                continue
            if o.kind == "box":
                dxy = max(abs(x - p[0]) - o.half_x, abs(y - p[1]) - o.half_y, 0.0)
                inz = o.z_min <= z <= o.z_max
                dist = dxy if inz else max(dxy, min(abs(z - o.z_min), abs(z - o.z_max)))
            elif o.kind == "sphere":
                dist = math.sqrt((x - p[0]) ** 2 + (y - p[1]) ** 2 + (z - p[2]) ** 2) - o.radius
            else:
                dxy = math.hypot(x - p[0], y - p[1]) - o.radius
                inz = o.z_min <= z <= o.z_max
                dist = dxy if inz else max(dxy, min(abs(z - o.z_min), abs(z - o.z_max)))
            worst = min(worst, dist)
            if dist <= 0:
                hit.append((o.name, float(t - t_offset)))
    return dict(min_clearance=float(worst), collisions=hit[:20], n_collision_samples=len(hit))
