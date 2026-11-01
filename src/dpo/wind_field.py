"""3D·시간가변 바람장 생성 (GFS 예보 → 대상지역 25 m 격자 다운스케일링).

1) 수평   : GFS 0.25° 격자점 4x4 → 격자 셀 경위도에서 쌍선형 보간
2) 연직   : 10/20/30/40/50/80/100 m AGL + 975/950 hPa 고도 → log(고도) 선형 보간
3) 시간   : 1시간 간격 예보 → 비행 중 임의 시각 선형 보간 (planner 내부)
4) 지형   : 능선 가속/계곡 감속 – 선형 지형가속 근사(Jackson–Hunt 계열)
            ΔS = α · (h − h̄₅₀₀ₘ)/L · exp(−AGL/ℓ)
5) 도시   : 건물 밀집지 저고도 감속(캐노피) – 건물피복률·평균높이 기반 지수감쇠

※ 4)·5)는 간이 물리 근사. 운영 단계에서는 기상청 LDAPS(1.5 km) 또는 CFD로 대체 가능.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter

from . import config as C


def _bilinear_weights(glat, glon, lat, lon):
    """GFS 격자(lat 내림차순)에서 쌍선형 보간 인덱스/가중치."""
    la = glat[::-1]
    iy = np.clip(np.searchsorted(la, lat) - 1, 0, len(la) - 2)
    ix = np.clip(np.searchsorted(glon, lon) - 1, 0, len(glon) - 2)
    wy = (lat - la[iy]) / (la[iy + 1] - la[iy])
    wx = (lon - glon[ix]) / (glon[ix + 1] - glon[ix])
    return iy, ix, wy, wx


def _interp2(field, glat, iy, ix, wy, wx):
    f = field[::-1]  # lat 오름차순
    return ((1 - wy) * (1 - wx) * f[iy, ix] + (1 - wy) * wx * f[iy, ix + 1]
            + wy * (1 - wx) * f[iy + 1, ix] + wy * wx * f[iy + 1, ix + 1])


class Site3D:
    """3D 점유격자 + 바람장 + 비행금지구역."""

    def __init__(self, grid: dict, gfs: dict, t_ref: np.datetime64, hours: int | None = None,
                 alpha_terrain=1.0, beta_urban=0.35):
        self.ground = grid["ground"]
        self.surface = grid["surface"]
        self.bldg_h = grid["bldg_h"]
        self.lon, self.lat = grid["lon"], grid["lat"]
        self.ny, self.nx = self.ground.shape
        self.dx = C.DX
        self.dz = C.Z_STEP
        zmin = np.floor((self.ground.min() + C.AGL_MIN) / self.dz) * self.dz
        zmax = np.ceil((self.ground.max() + C.AGL_MAX) / self.dz) * self.dz
        self.z = np.arange(zmin, zmax + 1e-6, self.dz)
        self.nz = len(self.z)

        # ---------------- 점유(비행 가능) 격자
        surf = maximum_filter(self.surface, size=3)  # 수평 1셀(25 m) 안전여유
        Z = self.z[:, None, None]
        agl = Z - self.ground[None]
        self.agl = agl
        self.free = ((Z >= surf[None] + C.BUILDING_CLEARANCE) & (agl >= C.AGL_MIN) & (agl <= C.AGL_MAX))
        self.nfz2d = np.zeros((self.ny, self.nx), bool)       # 정적 비행금지구역
        self.tnfz_id = -np.ones((self.ny, self.nx), np.int32)   # 시간제 비행금지구역 id
        self.tnfz_win = np.zeros((0, 2))                        # [시작, 종료] (출발기준 초)

        # ---------------- 바람장
        self.t_ref = t_ref
        valid = gfs["valid"].astype("datetime64[s]")
        sel = np.arange(len(valid)) if hours is None else np.arange(min(hours, len(valid)))
        self.t_axis = ((valid[sel] - np.datetime64(t_ref, "s")).astype(float))  # [s]
        self.valid = valid[sel]
        self.U, self.V = self._downscale(gfs, sel, alpha_terrain, beta_urban)

    # ------------------------------------------------------------------
    def _downscale(self, gfs, sel, alpha, beta):
        iy, ix, wy, wx = _bilinear_weights(gfs["lat"], gfs["lon"], self.lat, self.lon)
        lev_names = [f"agl{h}" for h in C.GFS_LEVELS_AGL] + [f"p{p}" for p in C.GFS_LEVELS_P]
        nt = len(sel)
        U = np.zeros((nt, self.nz, self.ny, self.nx), np.float32)
        V = np.zeros_like(U)
        # 지형·도시 보정 계수 (시간 불변)
        hbar = gaussian_filter(self.ground, 500 / self.dx)
        dh = self.ground - hbar
        bfrac = gaussian_filter((self.bldg_h > 0).astype(float), 4)
        hmean = gaussian_filter(self.bldg_h, 4) / np.maximum(bfrac, 1e-3)
        agl = np.maximum(self.agl, 1.0)
        f_ter = 1.0 + alpha * (dh / 500.0)[None] * np.exp(-agl / 200.0)
        f_urb = 1.0 - beta * bfrac[None] * np.exp(-np.maximum(agl - hmean[None], 0) / (hmean[None] + 10))
        self.factor = np.clip(f_ter * f_urb, 0.5, 1.6).astype(np.float32)
        for n, t in enumerate(sel):
            hs, us, vs = [], [], []
            for name in lev_names:
                u = _interp2(gfs[f"UGRD_{name}"][t], gfs["lat"], iy, ix, wy, wx)
                v = _interp2(gfs[f"VGRD_{name}"][t], gfs["lat"], iy, ix, wy, wx)
                if name.startswith("agl"):
                    h = np.full_like(u, float(name[3:]))
                else:
                    h = (_interp2(gfs[f"HGT_{name}"][t], gfs["lat"], iy, ix, wy, wx)
                         - _interp2(gfs["HGT_sfc"][t], gfs["lat"], iy, ix, wy, wx))
                hs.append(h); us.append(u); vs.append(v)
            hs, us, vs = np.array(hs), np.array(us), np.array(vs)  # (L, ny, nx)
            lh = np.log(hs)
            la = np.log(agl)  # (nz, ny, nx)
            # 열(column)별 log-높이 선형보간 (레벨 단조증가 가정)
            for k in range(self.nz):
                x = np.clip(la[k], lh[0], lh[-1])
                idx = np.clip((x[None] > lh).sum(0) - 1, 0, len(lh) - 2)
                l0 = np.take_along_axis(lh, idx[None], 0)[0]
                l1 = np.take_along_axis(lh, idx[None] + 1, 0)[0]
                w = (x - l0) / (l1 - l0)
                for arr, out in ((us, U), (vs, V)):
                    a0 = np.take_along_axis(arr, idx[None], 0)[0]
                    a1 = np.take_along_axis(arr, idx[None] + 1, 0)[0]
                    out[n, k] = ((1 - w) * a0 + w * a1) * self.factor[k]
        return U, V

    # ------------------------------------------------------------------
    def add_static_nfz_circle(self, cx, cy, r):
        X, Y = np.meshgrid((np.arange(self.nx) + .5) * self.dx, (np.arange(self.ny) + .5) * self.dx)
        m = (X - cx) ** 2 + (Y - cy) ** 2 <= r * r
        self.nfz2d |= m
        self.free &= ~m[None]
        return m

    def add_temporal_nfz_circle(self, cx, cy, r, t0, t1):
        X, Y = np.meshgrid((np.arange(self.nx) + .5) * self.dx, (np.arange(self.ny) + .5) * self.dx)
        m = (X - cx) ** 2 + (Y - cy) ** 2 <= r * r
        tid = len(self.tnfz_win)
        self.tnfz_id[m] = tid
        self.tnfz_win = np.vstack([self.tnfz_win, [t0, t1]])
        return m

    def add_temporal_nfz_polygon(self, xy, t0, t1):
        """임의 다각형(예: 띠 모양 임시 비행제한 구역) 시간제 금지구역."""
        from matplotlib.path import Path as MPath
        X, Y = np.meshgrid((np.arange(self.nx) + .5) * self.dx, (np.arange(self.ny) + .5) * self.dx)
        m = MPath(np.asarray(xy)).contains_points(np.c_[X.ravel(), Y.ravel()]).reshape(X.shape)
        tid = len(self.tnfz_win)
        self.tnfz_id[m] = tid
        self.tnfz_win = np.vstack([self.tnfz_win, [t0, t1]])
        return m

    def clear_temporal_nfz(self):
        self.tnfz_id[:] = -1
        self.tnfz_win = np.zeros((0, 2))

    def lowest_free_k(self, x, y):
        i, j = int(x // self.dx), int(y // self.dx)
        ks = np.where(self.free[:, j, i])[0]
        return int(ks[0]), j, i

    def rho_layers(self, t_c=15.0):
        from .energy import air_density
        return np.array([air_density(z, t_c) for z in self.z])

    def wind_at(self, t, k, j, i):
        a = np.interp(t, self.t_axis, np.arange(len(self.t_axis)))
        a0 = int(np.floor(min(a, len(self.t_axis) - 1.0001)))
        w = a - a0
        return ((1 - w) * self.U[a0, k, j, i] + w * self.U[a0 + 1, k, j, i],
                (1 - w) * self.V[a0, k, j, i] + w * self.V[a0 + 1, k, j, i])
