"""동적 장애물 모델 → 격자 셀별 '점유(위험) 시간구간' 테이블.

장애물 정의 (사용자가 크기·위치·이동을 선택; 플랫폼/JSON 공용)
  kind     : "cylinder"(원기둥, 예: 헬기 운항 공역·다른 드론 보호구역) | "sphere"(구) | "box"(직육면체, 축 정렬)
  path     : [{"x","y","z"(구만), "t"}...]  m, s(출발 기준) – 1점이면 정지, 2점 이상이면 구간별 등속 이동
  radius   : 원기둥/구 반경 [m]       half_x, half_y : 박스 반폭 [m]
  z_min, z_max : 원기둥/박스 고도 범위 [m 해발]
  t_start, t_end : 존재 시간 (기본: path 의 첫/끝 시각, 정지형은 0~inf)
  margin   : 안전 이격 [m]

시공간 충돌 판정
  셀 중심 q 가 시각 t 에 위험  ⇔  ‖q_xy − p(t)‖ ≤ R + margin + c   (c = 셀 반대각선, 보수적)
  p(t) = p_a + v (t − t_a) 구간별 선형 → ‖q − p_a − v(t − t_a)‖² ≤ ρ² 는 t 에 대한 2차 부등식
  → 각 셀의 위험 시간구간을 해석적으로 구해 병합 → 안전구간(SIPP)은 그 여집합
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict

import numpy as np

KINDS = ("cylinder", "sphere", "box")
LIMITS = dict(radius=(5.0, 1500.0), half=(5.0, 1500.0), speed_max=80.0, t_max=7200.0, n_max=30, margin=(0.0, 200.0),
              sigma0=(0.0, 500.0), sigma_v=(0.0, 20.0), sigma_t=(0.0, 120.0))


@dataclass
class DynObstacle:
    name: str = "obstacle"
    kind: str = "cylinder"
    path: list = field(default_factory=list)      # [{"x":..,"y":..,"z":..,"t":..}]
    radius: float = 100.0
    half_x: float = 100.0
    half_y: float = 100.0
    z_min: float = 0.0
    z_max: float = 1000.0
    t_start: float | None = None
    t_end: float | None = None
    margin: float = 15.0
    # 궤적 불확실성 (축별 표준편차 σ(t) = sigma0 + sigma_v·(t − t_obs)⁺, 시각 오차 표준편차 sigma_t)
    sigma0: float = 0.0       # 관측 시점 위치 오차 [m]
    sigma_v: float = 0.0      # 예측 시간에 따른 위치 오차 증가율 [m/s] (속도 추정 오차)
    sigma_t: float = 0.0      # 도달 시각 오차 [s]
    t_obs: float = 0.0        # 마지막 관측(발표) 시각 [s, 출발 기준]
    # 데이터 기반 팽창 모양 (선택): [[τ_k, r_k], …]  τ=예측 시간[s], r=추가 팽창 반경[m] (구간별 선형, 끝은 마지막 기울기로 연장)
    # 지정하면 κ_ε·σ(t) 대신 사용 (예: 실측 항적 오차의 1−ε 분위수 곡선)
    tube: list | None = None

    def inflation(self, t, kap=0.0):
        """시각 t 의 추가 팽창 반경과 그 시점 이후의 증가율 (r, dr/dt)."""
        tau = t - self.t_obs
        if self.tube:
            k = np.asarray(self.tube, float)
            tk, rk = k[:, 0], k[:, 1]
            if len(tk) == 1 or tau < tk[0]:
                return float(rk[0]), 0.0
            q = int(np.clip(np.searchsorted(tk, tau, side="right") - 1, 0, len(tk) - 2))
            sl = _slope(tk, rk, q)
            return float(rk[q] + sl * (tau - tk[q])), float(sl)
        if kap <= 0:
            return 0.0, 0.0
        return kap * (self.sigma0 + self.sigma_v * max(tau, 0.0)), (kap * self.sigma_v if tau >= 0 else 0.0)

    def breakpoints(self):
        """팽창 증가율이 바뀌는 시각들 (관측 시각 + 꺾임점)."""
        if self.tube:
            return [self.t_obs + float(k[0]) for k in self.tube]
        return [self.t_obs]

    # -------------------------------------------------------------- 위치
    def times(self):
        return np.array([float(p["t"]) for p in self.path])

    def window(self):
        ts = self.times()
        t0 = self.t_start if self.t_start is not None else (ts[0] if len(ts) > 1 else 0.0)
        t1 = self.t_end if self.t_end is not None else (ts[-1] if len(ts) > 1 else math.inf)
        return float(t0), float(t1)

    def position(self, t):
        """시각 t 의 중심 (x, y, z) – 경로 밖 시각은 끝점 유지. 존재하지 않으면 None."""
        t0, t1 = self.window()
        if t < t0 or t > t1:
            return None
        ts = self.times()
        xs = np.array([p["x"] for p in self.path]); ys = np.array([p["y"] for p in self.path])
        zs = np.array([p.get("z", 0.5 * (self.z_min + self.z_max)) for p in self.path])
        if len(ts) == 1:
            return float(xs[0]), float(ys[0]), float(zs[0])
        return float(np.interp(t, ts, xs)), float(np.interp(t, ts, ys)), float(np.interp(t, ts, zs))

    def to_dict(self):
        d = asdict(self)
        for k in ("t_start", "t_end"):
            if d[k] is not None and not math.isfinite(d[k]):
                d[k] = None
        return d

    @staticmethod
    def from_dict(d):
        keys = DynObstacle.__dataclass_fields__
        return DynObstacle(**{k: v for k, v in d.items() if k in keys})


def _slope(tk, rk, q):
    return (rk[q + 1] - rk[q]) / max(tk[q + 1] - tk[q], 1e-9)


# ------------------------------------------------------------------ 유효성 검사 ("가능한 범위에서만 허용")
def validate(obs: list, site, t_horizon=LIMITS["t_max"]):
    """반환: (유효 장애물 목록, 오류 메시지 목록). 범위를 벗어난 장애물은 제외한다."""
    W, H = site.nx * site.dx, site.ny * site.dx
    zlo, zhi = float(site.z[0]), float(site.z[-1])
    ok, errs = [], []
    if len(obs) > LIMITS["n_max"]:
        errs.append(f"장애물은 최대 {LIMITS['n_max']}개까지 허용 (입력 {len(obs)}개)")
        obs = obs[:LIMITS["n_max"]]
    for o in obs:
        e = []
        if o.kind not in KINDS:
            e.append(f"종류 '{o.kind}' 미지원 ({'/'.join(KINDS)})")
        if not o.path:
            e.append("위치(path) 없음")
        else:
            for p in o.path:
                if not (0 <= p["x"] <= W and 0 <= p["y"] <= H):
                    e.append(f"위치 ({p['x']:.0f}, {p['y']:.0f}) m 가 지도 범위(0~{W:.0f}, 0~{H:.0f}) 밖")
                    break
            ts = o.times()
            if np.any(np.diff(ts) <= 0):
                e.append("경로 시각은 증가해야 함")
            if np.any(ts < 0) or np.any(ts > t_horizon):
                e.append(f"시각은 0~{t_horizon:.0f} s 범위")
            if len(ts) > 1:
                d = np.hypot(np.diff([p["x"] for p in o.path]), np.diff([p["y"] for p in o.path]))
                v = d / np.maximum(np.diff(ts), 1e-9)
                if v.max() > LIMITS["speed_max"]:
                    e.append(f"이동 속도 {v.max():.0f} m/s > 허용 {LIMITS['speed_max']:.0f} m/s")
        lo, hi = LIMITS["radius"]
        if o.kind in ("cylinder", "sphere") and not (lo <= o.radius <= hi):
            e.append(f"반경 {o.radius:.0f} m 는 {lo:.0f}~{hi:.0f} m 범위")
        if o.kind == "box" and not (LIMITS["half"][0] <= min(o.half_x, o.half_y) and max(o.half_x, o.half_y) <= LIMITS["half"][1]):
            e.append("박스 반폭은 5~1500 m 범위")
        if o.kind in ("cylinder", "box"):
            if o.z_min >= o.z_max:
                e.append("고도 범위: z_min < z_max 이어야 함")
            if o.z_max < zlo or o.z_min > zhi:
                e.append(f"고도 범위 {o.z_min:.0f}~{o.z_max:.0f} m 가 비행 레이어({zlo:.0f}~{zhi:.0f} m)와 겹치지 않음 (영향 없음)")
        if not (LIMITS["margin"][0] <= o.margin <= LIMITS["margin"][1]):
            e.append("안전 이격은 0~200 m")
        if o.tube:
            try:
                k = np.asarray(o.tube, float)
                if k.ndim != 2 or k.shape[1] != 2 or np.any(np.diff(k[:, 0]) <= 0) or k[0, 0] < 0 \
                        or np.any(k[:, 1] < 0) or np.any(k[:, 1] > 5000):
                    e.append("팽창 모양(tube)은 τ 오름차순 [[τ, r], …], 0 ≤ r ≤ 5000 m")
            except (TypeError, ValueError):
                e.append("팽창 모양(tube) 형식 오류")
        for f, unit in (("sigma0", "m"), ("sigma_v", "m/s"), ("sigma_t", "s")):
            lo_, hi_ = LIMITS[f]
            if not (lo_ <= getattr(o, f) <= hi_):
                e.append(f"불확실성 {f} 는 {lo_:g}~{hi_:g} {unit}")
        if e:
            errs += [f"[{o.name}] {m}" for m in e]
        else:
            ok.append(o)
    return ok, errs


# ------------------------------------------------------------------ 위험 시간구간 계산
def risk_kappa(eps):
    """기회 제약 팽창 계수: 2차원 등방 가우시안 위치오차(축별 σ)의 반경 꼬리
    P(‖e‖ > κσ) = exp(−κ²/2) = ε  →  κ = √(2 ln(1/ε))."""
    return math.sqrt(2.0 * math.log(1.0 / eps))


def timing_kappa(eps):
    """시각 오차(가우시안 σ_t)의 양측 1−ε 분위수 z_{1−ε/2}."""
    from statistics import NormalDist
    return NormalDist().inv_cdf(1.0 - eps / 2.0)


def _quad_le0(a, b, c, T):
    """a τ² + b τ + c ≤ 0 를 만족하는 τ ∈ [0, T] (a 스칼라, b·c 배열). 반환 [(lo, hi), …] (최대 2개, 빈 구간 lo>hi).
    a<0 (불확실성 반경 증가율 > 상대속도) 이면 두 근 바깥쪽(두 구간)이 해가 된다."""
    shp = np.shape(c)
    b = np.broadcast_to(b, shp).astype(float); c = np.asarray(c, float)
    E = lambda: (np.full(shp, np.inf), np.full(shp, -np.inf))
    if abs(a) < 1e-12:
        lo, hi = E()
        m0 = np.abs(b) < 1e-12
        lo[m0 & (c <= 0)], hi[m0 & (c <= 0)] = 0.0, T
        mp = b > 1e-12
        r = np.where(mp, -c / np.where(mp, b, 1), 0)
        lo[mp], hi[mp] = 0.0, np.minimum(r[mp], T)
        mn = b < -1e-12
        r = np.where(mn, -c / np.where(mn, b, 1), 0)
        lo[mn], hi[mn] = np.maximum(r[mn], 0.0), T
        return [(lo, hi)]
    disc = b * b - 4 * a * c
    ok = disc >= 0
    sq = np.sqrt(np.where(ok, disc, 0))
    r1 = (-b - sq) / (2 * a); r2 = (-b + sq) / (2 * a)
    ra, rb = np.minimum(r1, r2), np.maximum(r1, r2)
    if a > 0:
        lo, hi = E()
        lo[ok], hi[ok] = np.maximum(ra[ok], 0.0), np.minimum(rb[ok], T)
        return [(lo, hi)]
    lo1, hi1 = E(); lo2, hi2 = E()
    lo1[~ok], hi1[~ok] = 0.0, T                         # 근 없음 → 항상 ≤ 0
    lo1[ok], hi1[ok] = 0.0, np.minimum(ra[ok], T)
    lo2[ok], hi2[ok] = np.maximum(rb[ok], 0.0), T
    return [(lo1, hi1), (lo2, hi2)]


def _lin_box(r, v, h0, h1, T):
    """|r − vτ| ≤ h0 + h1 τ , τ∈[0,T] (배열 r) → 단일 구간 (lo, hi)."""
    lo = np.zeros(np.shape(r)); hi = np.full(np.shape(r), T, dtype=float)
    for alpha, beta in ((-v - h1, h0 - r), (v - h1, h0 + r)):   # α τ ≤ β
        if abs(alpha) < 1e-12:
            bad = beta < 0
            lo[bad], hi[bad] = np.inf, -np.inf
        elif alpha > 0:
            hi = np.minimum(hi, beta / alpha)
        else:
            lo = np.maximum(lo, beta / alpha)
    return lo, hi


def build_interval_table(site, obstacles, t_offset=0.0, extra=None, eps=None, horizon=1800.0,
                         inflate_const=None):
    """셀별 위험 시간구간 테이블 (site 시각 기준 = t_offset + 장애물 시각).

    eps          : 기회 제약 위험 한도 ε. 주어지면 불확실성 튜브 적용
                   반경 ρ(t) = R + margin + c_cell + κ_ε·σ(t),  σ(t) = σ0 + σ_v (t − t_obs)⁺  (예측 시간에 따라 증가)
                   위험구간 양끝을 z_{1−ε/2}·σ_t 만큼 확장 (도달 시각 오차)
    inflate_const: (고정 버퍼 비교용) 시간과 무관한 일정 팽창량 [m]
    extra        : 기존 시간제 금지구역 (site.tnfz_id / tnfz_win) 도 통합.
    반환 dict(off[N] int64(-1=없음), cnt[N] int32, lo[M], hi[M], maxi)
    """
    nz, ny, nx = site.free.shape
    dx = site.dx
    c_cell = 0.71 * dx
    X = (np.arange(nx) + 0.5) * dx
    Y = (np.arange(ny) + 0.5) * dx
    per = {}
    kap = risk_kappa(eps) if eps else 0.0
    kt = timing_kappa(eps) if eps else 0.0

    def add(ids, lo, hi):
        for n, a, b in zip(ids, lo, hi):
            per.setdefault(int(n), []).append((float(a), float(b)))

    for o in obstacles:
        t0w, t1w = o.window()
        pts = o.path
        ts = o.times()
        segs = [(pts[0], pts[0], t0w, t1w)] if len(pts) == 1 else \
            [(pts[q], pts[q + 1], ts[q], ts[q + 1]) for q in range(len(pts) - 1)]
        if len(pts) > 1:  # 경로 앞뒤 존재구간은 끝점에 정지
            if t0w < ts[0]:
                segs.insert(0, (pts[0], pts[0], t0w, ts[0]))
            if t1w > ts[-1]:
                segs.append((pts[-1], pts[-1], ts[-1], t1w))
        use = bool(o.tube) or bool(eps)
        kap_o = kap if eps else 0.0
        dil = kt * o.sigma_t if (eps and not o.tube) else 0.0
        const = (inflate_const or 0.0)
        # 팽창 증가율이 바뀌는 시각(관측 시각·꺾임점)에서 구간을 나눠 팽창 반경을 구간별 선형으로 만든다
        bps = sorted(o.breakpoints()) if use else []
        segs2 = []
        for pa, pb, ta0, tb0 in segs:
            cuts = [ta0] + [b for b in bps if ta0 < b < tb0] + [tb0]
            for ca, cb in zip(cuts[:-1], cuts[1:]):
                segs2.append((pa, pb, ta0, tb0, ca, cb))
        for pa, pb, ta0, tb0, ca, cb in segs2:
            ta, tb = max(ca, t0w), min(cb, t1w)
            if tb <= ta and not (ta == tb == 0):
                continue
            infl_a, g1 = o.inflation(ta + 1e-9, kap_o) if use else (0.0, 0.0)
            infl_a = o.inflation(ta, kap_o)[0] if use else 0.0
            if g1 > 0 and not math.isfinite(tb):
                tb = ta + horizon                         # 불확실성이 계속 커지는 경우 예측 지평 제한
            dt0 = tb0 - ta0
            if math.isfinite(dt0) and dt0 > 1e-9:
                ux, uy = (pb["x"] - pa["x"]) / dt0, (pb["y"] - pa["y"]) / dt0
            else:
                ux = uy = 0.0
            x0, y0 = pa["x"] + ux * (ta - ta0), pa["y"] + uy * (ta - ta0)
            if math.isfinite(tb):
                x1, y1 = pa["x"] + ux * (tb - ta0), pa["y"] + uy * (tb - ta0)
            else:
                x1, y1 = x0, y0
            T = tb - ta
            base = (o.radius if o.kind != "box" else 0.0) + o.margin + c_cell + const + infl_a
            grow_max = g1 * (T if math.isfinite(T) else 0.0)
            reach = (base if o.kind != "box" else math.hypot(o.half_x, o.half_y) + base) + grow_max
            i0 = max(int((min(x0, x1) - reach) // dx), 0); i1 = min(int((max(x0, x1) + reach) // dx) + 1, nx)
            j0 = max(int((min(y0, y1) - reach) // dx), 0); j1 = min(int((max(y0, y1) + reach) // dx) + 1, ny)
            if i0 >= i1 or j0 >= j1:
                continue
            QX, QY = np.meshgrid(X[i0:i1], Y[j0:j1])
            rx, ry = QX - x0, QY - y0                          # r(τ) = q − p(ta) − v τ
            if o.kind == "box":
                lx, hx_ = _lin_box(rx, ux, o.half_x + base, g1, T)
                ly, hy_ = _lin_box(ry, uy, o.half_y + base, g1, T)
                ivs = [(np.maximum(lx, ly), np.minimum(hx_, hy_))]
                zpad = o.margin + const + infl_a + grow_max
                zr = (o.z_min - zpad, o.z_max + zpad)
            else:
                rho0 = base
                a = ux * ux + uy * uy - g1 * g1
                b = -2 * (rx * ux + ry * uy) - 2 * rho0 * g1
                cc = rx * rx + ry * ry - rho0 * rho0
                ivs = _quad_le0(a, b, cc, T)
                if o.kind == "sphere":
                    zc0, zc1 = pa.get("z", 0.5 * (o.z_min + o.z_max)), pb.get("z", 0.5 * (o.z_min + o.z_max))
                    zr = (min(zc0, zc1) - rho0 - grow_max, max(zc0, zc1) + rho0 + grow_max)
                else:
                    zpad = o.margin + const + infl_a + grow_max
                    zr = (o.z_min - zpad, o.z_max + zpad)
            ks = np.where((site.z >= zr[0] - site.dz / 2) & (site.z <= zr[1] + site.dz / 2))[0]
            if len(ks) == 0:
                continue
            for lo, hi in ivs:
                lo = lo + ta - dil; hi = hi + ta + dil
                m = lo <= hi
                if not m.any():
                    continue
                jj, ii = np.nonzero(m)
                for k in ks:
                    ids = (k * ny + (jj + j0)) * nx + (ii + i0)
                    add(ids, lo[m] + t_offset, hi[m] + t_offset)
    # 기존 시간제 금지구역(2D 발자국 × 전 고도)
    if extra is not None and len(site.tnfz_win):
        for q, (a, b) in enumerate(site.tnfz_win):
            jj, ii = np.nonzero(site.tnfz_id == q)
            for k in range(nz):
                add((k * ny + jj) * nx + ii, np.full(len(jj), a), np.full(len(jj), b))
    N = nz * ny * nx
    off = -np.ones(N, np.int64); cnt = np.zeros(N, np.int32)
    LO, HI = [], []
    maxi = 1
    for n, ivs in per.items():
        ivs.sort()
        merged = []
        for a, b in ivs:
            if merged and a <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        off[n] = len(LO); cnt[n] = len(merged)
        for a, b in merged:
            LO.append(a); HI.append(b)
        maxi = max(maxi, len(merged) + 1)
    return dict(off=off, cnt=cnt, lo=np.array(LO + [0.0]), hi=np.array(HI + [0.0]), maxi=maxi, n_cells=len(per))


# ------------------------------------------------------------------ JSON
def save_scenario(path, obstacles, meta=None):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(meta=meta or {}, obstacles=[o.to_dict() for o in obstacles]), f, ensure_ascii=False, indent=1)


def load_scenario(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return [DynObstacle.from_dict(o) for o in d["obstacles"]], d.get("meta", {})
