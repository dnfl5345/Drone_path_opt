"""확장안: 수평 8방향 + 수직 이동, '고도별 수평 균일·고도에 선형' 바람 가정.

가정 (A1) 수평 균일:  w(x, y, z, t) = w(z, t)          (같은 고도에서는 바람이 같음)
가정 (A2) 선형 시어:  w(z, t) = a(t) + b(t)·(z − h̄)    (h̄: 지역 평균 지면고, 성분별 최소제곱)

결과 1) 간선 비용이 위치와 무관 → (시간 슬라이스, 고도 레이어, 방향)의 조회표(LUT)로 미리 계산
          e*[n, k, θ] = min_Vg P(|Vg·ê(θ) − w(z_k, t_n)|, 0, m) / Vg
        탐색 중에는 연산 대신 표 조회만 하므로 수십 배 빨라짐.
결과 2) 한 레이어 안의 비용은 F(d) = |d|·e*(θ) (방향에만 의존하는 비등방 노름).
        F가 볼록하면 삼각부등식이 성립 → 표준 Lazy Theta*의 'LOS면 직결' 규칙이 다시 정당화됨.
        (F의 단위구 {d : F(d) ≤ 1} 의 볼록성을 수치로 확인)
결과 3) 고도 선택은 1차원 비교로 환원:
          상승 Δz 이득 조건  L·(e*(z0,θ) − e*(z1,θ)) > E_up(Δz) + E_dn(Δz)   → 손익분기 거리 L*
"""
from __future__ import annotations

import math
import time as _time

import heapq
import numpy as np
from numba import njit

from .energy import edge_energy_per_m

INF = 1e30
N_HEAD = 72                       # LUT 방위 해상도 5°


# ------------------------------------------------------------------ (A1)(A2) 선형 시어 바람
def fit_linear_profile(gfs, lat, lon, h_bar, zmax=320.0):
    """GFS 고도별 바람(사이트 지점 쌍선형 보간)을 시간마다 u = a + b·z_agl 로 최소제곱 적합."""
    from .wind_field import _bilinear_weights, _interp2
    from . import config as C

    iy, ix, wy, wx = _bilinear_weights(gfs["lat"], gfs["lon"], np.array([lat]), np.array([lon]))
    nt = gfs["UGRD_agl10"].shape[0]
    out = dict(a_u=np.zeros(nt), b_u=np.zeros(nt), a_v=np.zeros(nt), b_v=np.zeros(nt), r2=np.zeros(nt),
               levels=[], u=[], v=[])
    for n in range(nt):
        zs, us, vs = [], [], []
        for lv in C.GFS_LEVELS_AGL:
            zs.append(lv)
            us.append(_interp2(gfs[f"UGRD_agl{lv}"][n], gfs["lat"], iy, ix, wy, wx)[0])
            vs.append(_interp2(gfs[f"VGRD_agl{lv}"][n], gfs["lat"], iy, ix, wy, wx)[0])
        for p in C.GFS_LEVELS_P:
            z = (_interp2(gfs[f"HGT_p{p}"][n], gfs["lat"], iy, ix, wy, wx)
                 - _interp2(gfs["HGT_sfc"][n], gfs["lat"], iy, ix, wy, wx))[0]
            if z <= zmax:
                zs.append(z)
                us.append(_interp2(gfs[f"UGRD_p{p}"][n], gfs["lat"], iy, ix, wy, wx)[0])
                vs.append(_interp2(gfs[f"VGRD_p{p}"][n], gfs["lat"], iy, ix, wy, wx)[0])
        zs, us, vs = map(np.asarray, (zs, us, vs))
        m = zs >= 20  # 운용고도대 중심으로 적합(지표 10 m 제외)
        A = np.c_[np.ones(m.sum()), zs[m]]
        cu = np.linalg.lstsq(A, us[m], rcond=None)[0]
        cv = np.linalg.lstsq(A, vs[m], rcond=None)[0]
        res = np.r_[us[m] - A @ cu, vs[m] - A @ cv]
        tot = np.r_[us[m] - us[m].mean(), vs[m] - vs[m].mean()]
        out["a_u"][n], out["b_u"][n], out["a_v"][n], out["b_v"][n] = cu[0], cu[1], cv[0], cv[1]
        out["r2"][n] = 1 - (res ** 2).sum() / (tot ** 2).sum()
        out["levels"].append(zs); out["u"].append(us); out["v"].append(vs)
    out["h_bar"] = h_bar
    return out


def linear_wind_site(site, prof):
    """Site3D 복사본: U,V 를 레이어별 수평 균일·선형 시어 값으로 교체 (계획용 모델)."""
    import copy
    s = copy.copy(site)
    zagl = np.maximum(site.z - prof["h_bar"], 10.0)
    U = prof["a_u"][:, None] + prof["b_u"][:, None] * zagl[None]
    V = prof["a_v"][:, None] + prof["b_v"][:, None] * zagl[None]
    nt = len(site.t_axis)
    s.U = np.broadcast_to(U[:nt, :, None, None], site.U.shape).astype(np.float32).copy()
    s.V = np.broadcast_to(V[:nt, :, None, None], site.V.shape).astype(np.float32).copy()
    s.lin_U, s.lin_V = U[:nt].astype(np.float64), V[:nt].astype(np.float64)
    return s


# ------------------------------------------------------------------ LUT
SLOPES = np.linspace(-0.6, 0.6, 25)   # 3D 지름길용 경사(tan) 구간, 0.05 간격


@njit(cache=True)
def _build_slope_lut(lin_U, lin_V, rho, m, prm, va_max, vz_up, vz_dn, speeds, slopes):
    nt, nz = lin_U.shape
    ns = slopes.shape[0]
    ES = np.full((nt, nz, 72, ns), np.inf)
    GS = np.zeros((nt, nz, 72, ns))
    for n in range(nt):
        for k in range(nz):
            for h in range(72):
                th = 2 * math.pi * h / 72
                for q in range(ns):
                    e, v = edge_energy_per_m(math.sin(th), math.cos(th), slopes[q], lin_U[n, k], lin_V[n, k], m,
                                             rho[k], prm, va_max, vz_up, vz_dn, speeds)
                    ES[n, k, h, q] = e
                    GS[n, k, h, q] = v
    return ES, GS


def build_lut(lin_U, lin_V, rho, m, drone, speeds=None, dz=20.0, dx_h=25.0):
    """e*[n,k,h] (수평, 방위 h·5°), 상승/하강 [n,k,2] 미터당 에너지와 대지속도."""
    speeds = np.arange(3.0, 20.01, 0.5) if speeds is None else speeds
    prm = drone.params()
    nt, nz = lin_U.shape
    E = np.full((nt, nz, N_HEAD), np.inf); G = np.zeros((nt, nz, N_HEAD))
    EV = np.zeros((nt, nz, 2)); GV = np.zeros((nt, nz, 2))
    ED = np.full((nt, nz, 8, 2), np.inf); GD = np.zeros((nt, nz, 8, 2))   # 8방위 × (상승, 하강) 대각 이동
    for n in range(nt):
        for k in range(nz):
            wu, wv = lin_U[n, k], lin_V[n, k]
            for h in range(N_HEAD):
                th = 2 * math.pi * h / N_HEAD          # 0 = 북쪽(+y), 시계방향
                e, v = edge_energy_per_m(math.sin(th), math.cos(th), 0.0, wu, wv, m, rho[k], prm,
                                         drone.va_max, drone.vz_up, drone.vz_dn, speeds)
                E[n, k, h], G[n, k, h] = e, v
            for s, sg in enumerate((1.0, -1.0)):
                e, v = edge_energy_per_m(0.0, 0.0, sg * dz, wu, wv, m, rho[k], prm, drone.va_max,
                                         drone.vz_up, drone.vz_dn, speeds)
                EV[n, k, s], GV[n, k, s] = e, v
                k2 = min(max(k + int(sg), 0), nz - 1)          # 대각 이동: 두 레이어 평균 바람
                wu2, wv2 = 0.5 * (wu + lin_U[n, k2]), 0.5 * (wv + lin_V[n, k2])
                for d8 in range(8):
                    th = 2 * math.pi * d8 / 8
                    step = dx_h if d8 % 2 == 0 else dx_h * math.sqrt(2)
                    e, v = edge_energy_per_m(math.sin(th) * step, math.cos(th) * step, sg * dz, wu2, wv2, m,
                                             rho[k], prm, drone.va_max, drone.vz_up, drone.vz_dn, speeds)
                    ED[n, k, d8, s], GD[n, k, d8, s] = e, v
    ES, GS = _build_slope_lut(lin_U.astype(np.float64), lin_V.astype(np.float64), np.asarray(rho, np.float64), m,
                              prm, drone.va_max, drone.vz_up, drone.vz_dn, speeds, SLOPES)
    return E, G, EV, GV, ED, GD, ES, GS


def unit_ball_convexity(E_layer):
    """F(d)=|d|·e*(θ) 의 단위구 경계 r(θ)=1/e*(θ) 가 볼록인지 (볼록 껍질과의 최대 오차) 검사."""
    from scipy.spatial import ConvexHull
    th = 2 * np.pi * np.arange(N_HEAD) / N_HEAD
    r = 1.0 / E_layer
    pts = np.c_[r * np.sin(th), r * np.cos(th)]
    hull = ConvexHull(pts)
    on_hull = np.zeros(N_HEAD, bool)
    on_hull[hull.vertices] = True
    # 껍질 위에 없는 점의 반경 결손 비율
    from matplotlib.path import Path as MPath
    hp = MPath(pts[hull.vertices])
    deficit = 0.0
    for p, oh in zip(pts, on_hull):
        if not oh:
            # 원점에서 같은 방향으로 껍질 경계까지 반경 → 이분탐색
            lo, hi = 1.0, 2.0
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                if hp.contains_point(p * mid):
                    lo = mid
                else:
                    hi = mid
            deficit = max(deficit, lo - 1.0)
    return dict(convex=deficit < 1e-3, max_deficit=deficit, n_off_hull=int((~on_hull).sum()))


# ------------------------------------------------------------------ 탐색 (LUT 기반)
@njit(cache=True)
def _lut_cost(E, G, tax, t, k, h):
    nt = tax.shape[0]
    if nt == 1 or t <= tax[0]:
        return E[0, k, h], G[0, k, h]
    if t >= tax[nt - 1]:
        return E[nt - 1, k, h], G[nt - 1, k, h]
    a = 0
    while tax[a + 1] < t:
        a += 1
    w = (t - tax[a]) / (tax[a + 1] - tax[a])
    return (1 - w) * E[a, k, h] + w * E[a + 1, k, h], (1 - w) * G[a, k, h] + w * G[a + 1, k, h]


@njit(cache=True)
def _heading_idx(dj, di):
    th = math.atan2(di, dj)            # 북쪽 기준 시계방향
    if th < 0:
        th += 2 * math.pi
    return int(round(th / (2 * math.pi) * 72)) % 72


@njit(cache=True)
def _layer_seg(k, j0, i0, j1, i1, t0, free, E, G, tax, dx, check):
    """같은 레이어 안의 직선 구간: 비용 = L·e*(θ) (위치 무관), LOS 는 격자 샘플링."""
    L = math.sqrt(((j1 - j0) * dx) ** 2 + ((i1 - i0) * dx) ** 2)
    if L < 1e-9:
        return 0.0, 0.0, True
    if check:
        n = int(math.ceil(L / (0.5 * dx)))
        for s in range(n + 1):
            f = s / n
            jj = int(round(j0 + (j1 - j0) * f)); ii = int(round(i0 + (i1 - i0) * f))
            if not free[k, jj, ii]:
                return INF, 0.0, False
    h = _heading_idx(j1 - j0, i1 - i0)
    e, v = _lut_cost(E, G, tax, t0, k, h)
    if e >= INF or v <= 0:
        return INF, 0.0, False
    return e * L, L / v, True


@njit(cache=True)
def _seg3d(k0, j0, i0, k1, j1, i1, t0, free, ES, GS, tax, dx, dz, slopes, check):
    """레이어를 가로지르는 3D 직선: 비용 = L·e*(θ, 경사, 중간 레이어) – LUT 조회 + (선택) 3D LOS."""
    Lh = math.sqrt(((j1 - j0) * dx) ** 2 + ((i1 - i0) * dx) ** 2)
    if Lh < 1e-9:
        return INF, 0.0, False
    sl = (k1 - k0) * dz / Lh
    ds = slopes[1] - slopes[0]
    q = int(round((sl - slopes[0]) / ds))
    if q < 0 or q >= slopes.shape[0]:
        return INF, 0.0, False
    L = math.sqrt(Lh * Lh + ((k1 - k0) * dz) ** 2)
    if check:
        n = int(math.ceil(L / (0.5 * dx)))
        for s in range(n + 1):
            f = s / n
            kk = int(round(k0 + (k1 - k0) * f)); jj = int(round(j0 + (j1 - j0) * f)); ii = int(round(i0 + (i1 - i0) * f))
            if not free[kk, jj, ii]:
                return INF, 0.0, False
    h = _heading_idx(j1 - j0, i1 - i0)
    km = int(round(0.5 * (k0 + k1)))
    nt = tax.shape[0]
    if nt == 1 or t0 <= tax[0]:
        e = ES[0, km, h, q]; v = GS[0, km, h, q]
    elif t0 >= tax[nt - 1]:
        e = ES[nt - 1, km, h, q]; v = GS[nt - 1, km, h, q]
    else:
        a = 0
        while tax[a + 1] < t0:
            a += 1
        w = (t0 - tax[a]) / (tax[a + 1] - tax[a])
        e = (1 - w) * ES[a, km, h, q] + w * ES[a + 1, km, h, q]
        v = (1 - w) * GS[a, km, h, q] + w * GS[a + 1, km, h, q]
    if e >= INF or v <= 0:
        return INF, 0.0, False
    return e * L, Lh / v, True


@njit(cache=True)
def _step(vk, vj, vi, uk, uj, ui, t, free, E, G, EV, GV, ED, GD, tax, dx, dz):
    """26-이웃 단위 이동 비용 (LUT 조회). 반환 (cost, dur, ok)."""
    if not free[uk, uj, ui]:
        return INF, 0.0, False
    dj = uj - vj; di = ui - vi; dk = uk - vk
    if dk == 0:
        return _layer_seg(vk, vj, vi, uj, ui, t, free, E, G, tax, dx, False)
    s = 0 if dk > 0 else 1
    if dj == 0 and di == 0:
        e, v = _lut_cost(EV, GV, tax, t, vk, s)
        return e * dz, dz / v, e < INF
    d8 = _heading_idx(dj, di) // 9
    L = math.sqrt((dj * dx) ** 2 + (di * dx) ** 2 + dz * dz)
    nt = tax.shape[0]
    if nt == 1 or t <= tax[0]:
        e = ED[0, vk, d8, s]; v = GD[0, vk, d8, s]
    elif t >= tax[nt - 1]:
        e = ED[nt - 1, vk, d8, s]; v = GD[nt - 1, vk, d8, s]
    else:
        a = 0
        while tax[a + 1] < t:
            a += 1
        w = (t - tax[a]) / (tax[a + 1] - tax[a])
        e = (1 - w) * ED[a, vk, d8, s] + w * ED[a + 1, vk, d8, s]
        v = (1 - w) * GD[a, vk, d8, s] + w * GD[a + 1, vk, d8, s]
    if e >= INF or v <= 0:
        return INF, 0.0, False
    lh = math.sqrt((dj * dx) ** 2 + (di * dx) ** 2)
    return e * L, lh / v, True


@njit(cache=True)
def _search_layered(start, goal, t_start, free, E, G, EV, GV, ED, GD, ES, GS, slopes, tax, dx, dz, e_lb, any_angle,
                    max_seg, diag_climb, aa3d):
    nz, ny, nx = free.shape
    N = nz * ny * nx
    g = np.full(N, INF); tt = np.zeros(N); par = -np.ones(N, np.int64)
    closed = np.zeros(N, np.bool_); lazy = np.zeros(N, np.bool_)
    nb = np.empty((26, 3), np.int64)
    c = 0
    for a in (0, 1, -1):
        for b in (-1, 0, 1):
            for d in (-1, 0, 1):
                if a == 0 and b == 0 and d == 0:
                    continue
                nb[c, 0], nb[c, 1], nb[c, 2] = a, b, d
                c += 1
    sk, sj, si = start; gk, gj, gi = goal
    s_id = (sk * ny + sj) * nx + si; g_id = (gk * ny + gj) * nx + gi
    g[s_id] = 0.0; tt[s_id] = t_start; par[s_id] = s_id
    heap = [(0.0, s_id)]
    n_exp = 0
    while len(heap) > 0:
        f, u = heapq.heappop(heap)
        if closed[u]:
            continue
        uk = u // (ny * nx); uj = (u // nx) % ny; ui = u % nx
        if lazy[u]:
            p = par[u]
            pk = p // (ny * nx); pj = (p // nx) % ny; pi = p % nx
            if pk == uk:
                c2, d2, ok = _layer_seg(uk, pj, pi, uj, ui, tt[p], free, E, G, tax, dx, True)
            else:
                c2, d2, ok = _seg3d(pk, pj, pi, uk, uj, ui, tt[p], free, ES, GS, tax, dx, dz, slopes, True)
            if not ok:
                best = INF; bp = -1; bt = 0.0
                for c in range(26):
                    vk = uk + nb[c, 0]; vj = uj + nb[c, 1]; vi = ui + nb[c, 2]
                    if vk < 0 or vk >= nz or vj < 0 or vj >= ny or vi < 0 or vi >= nx:
                        continue
                    if not diag_climb and nb[c, 0] != 0 and (nb[c, 1] != 0 or nb[c, 2] != 0):
                        continue
                    v = (vk * ny + vj) * nx + vi
                    if not closed[v]:
                        continue
                    c1, d1, ok1 = _step(vk, vj, vi, uk, uj, ui, tt[v], free, E, G, EV, GV, ED, GD, tax, dx, dz)
                    if ok1 and g[v] + c1 < best:
                        best = g[v] + c1; bp = v; bt = tt[v] + d1
                if bp < 0:
                    g[u] = INF
                    continue
                g[u] = best; par[u] = bp; tt[u] = bt
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
            if not diag_climb and nb[c, 0] != 0 and (nb[c, 1] != 0 or nb[c, 2] != 0):
                continue
            if not free[vk, vj, vi]:
                continue
            v = (vk * ny + vj) * nx + vi
            if closed[v]:
                continue
            c1, d1, ok1 = _step(uk, uj, ui, vk, vj, vi, tt[u], free, E, G, EV, GV, ED, GD, tax, dx, dz)
            if not ok1:
                continue
            cand_g = g[u] + c1; cand_p = u; cand_t = tt[u] + d1; cand_lazy = False
            # 표준 Lazy Theta* (같은 레이어 수평 이동): 볼록 비등방 노름이면 직결이 항상 유리, LOS 지연 검사
            if any_angle and p != u and (pj != vj or pi != vi):
                L2 = math.sqrt(((vj - pj) * dx) ** 2 + ((vi - pi) * dx) ** 2)
                if L2 <= max_seg:
                    if pk == vk:
                        c2, d2, ok2 = _layer_seg(vk, pj, pi, vj, vi, tt[p], free, E, G, tax, dx, False)
                    elif aa3d:
                        c2, d2, ok2 = _seg3d(pk, pj, pi, vk, vj, vi, tt[p], free, ES, GS, tax, dx, dz, slopes, False)
                    else:
                        ok2 = False
                    # 비볼록 비용 노름 → 직결은 에너지가 작을 때만 채택 (WE-LT* 규칙)
                    if ok2 and g[p] + c2 <= cand_g:
                        cand_g = g[p] + c2; cand_p = p; cand_t = tt[p] + d2; cand_lazy = True
            if cand_g < g[v]:
                g[v] = cand_g; par[v] = cand_p; tt[v] = cand_t; lazy[v] = cand_lazy
                h = math.sqrt(((gi - vi) * dx) ** 2 + ((gj - vj) * dx) ** 2 + ((gk - vk) * dz) ** 2) * e_lb
                heapq.heappush(heap, (cand_g + h, v))
    return g, tt, par, n_exp


class LayeredPlanner:
    """8방향+수직, 선형 시어 바람 LUT 기반 계획기."""

    def __init__(self, site_lin, drone):
        self.site = site_lin
        self.drone = drone
        self.rho = site_lin.rho_layers()
        self._lut = {}

    def lut(self, m):
        key = round(m, 3)
        if key not in self._lut:
            t0 = _time.perf_counter()
            self._lut[key] = build_lut(self.site.lin_U, self.site.lin_V, self.rho, m, self.drone, dz=self.site.dz,
                                       dx_h=self.site.dx)
            self.lut_time = _time.perf_counter() - t0
        return self._lut[key]

    def plan(self, start, goal, t_start=0.0, mass=None, any_angle=True, max_seg=400.0, diag_climb=True, aa3d=True,
             **_):
        m = self.drone.m_empty if mass is None else mass
        E, G, EV, GV, ED, GD, ES, GS = self.lut(m)
        e_lb = float(min(np.nanmin(E[np.isfinite(E)]), np.nanmin(ED[np.isfinite(ED)])))
        s = self.site
        t0 = _time.perf_counter()
        g, tt, par, n_exp = _search_layered(np.array(start, np.int64), np.array(goal, np.int64), float(t_start),
                                            s.free, E, G, EV, GV, ED, GD, ES, GS, SLOPES, s.t_axis, s.dx, s.dz,
                                            e_lb, any_angle, max_seg, diag_climb, aa3d)
        cpu = _time.perf_counter() - t0
        nz, ny, nx = s.free.shape
        gid = (goal[0] * ny + goal[1]) * nx + goal[2]
        if g[gid] >= INF:
            return None
        path = []
        u = gid
        while True:
            path.append((u // (ny * nx), (u // nx) % ny, u % nx))
            if par[u] == u:
                break
            u = par[u]
        path = np.array(path[::-1])
        return dict(path=path, wait_before=np.zeros(len(path)), cost=g[gid], t_arrive=tt[gid], n_expanded=int(n_exp),
                    n_los=0, cpu_s=cpu, mass=m, mode=1)


def breakeven_climb(E, EV, n, k0, k1, h, dz):
    """레이어 k0→k1 상승(왕복 하강 포함) 손익분기 수평거리 L* [m] (방위 h)."""
    de = E[n, k0, h] - E[n, k1, h]
    up = sum(EV[n, k, 0] * dz for k in range(k0, k1))
    dn = sum(EV[n, k, 1] * dz for k in range(k0 + 1, k1 + 1))
    return (up + dn) / de if de > 0 else np.inf
