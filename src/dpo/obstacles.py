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
LIMITS = dict(radius=(5.0, 1500.0), half=(5.0, 1500.0), speed_max=80.0, t_max=7200.0, n_max=30, margin=(0.0, 200.0))


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
        if e:
            errs += [f"[{o.name}] {m}" for m in e]
        else:
            ok.append(o)
    return ok, errs


# ------------------------------------------------------------------ 위험 시간구간 계산
def _quad_interval(a, b, c, t0, t1):
    """a t² + b t + c ≤ 0 인 t ∈ [t0, t1] 구간 (배열 연산). 반환 lo, hi (없으면 lo > hi)."""
    lo = np.full(np.shape(c), np.inf); hi = np.full(np.shape(c), -np.inf)
    if a < 1e-12:  # 정지(속도 0): c ≤ 0 이면 전 구간
        m = c <= 0
        lo[m], hi[m] = t0, t1
        return lo, hi
    disc = b * b - 4 * a * c
    m = disc >= 0
    sq = np.sqrt(np.where(m, disc, 0))
    r1 = (-b - sq) / (2 * a); r2 = (-b + sq) / (2 * a)
    lo[m] = np.maximum(r1[m], t0); hi[m] = np.minimum(r2[m], t1)
    return lo, hi


def build_interval_table(site, obstacles, t_offset=0.0, extra=None):
    """셀별 위험 시간구간 테이블 (site 시각 기준 = t_offset + 장애물 시각).

    extra: 기존 시간제 금지구역 (site.tnfz_id / tnfz_win) 도 통합.
    반환 dict(off[N] int64(-1=없음), cnt[N] int32, lo[M], hi[M], maxi)
    """
    nz, ny, nx = site.free.shape
    dx = site.dx
    c_cell = 0.71 * dx
    X = (np.arange(nx) + 0.5) * dx
    Y = (np.arange(ny) + 0.5) * dx
    per = {}

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
        reach = (o.radius if o.kind != "box" else math.hypot(o.half_x, o.half_y)) + o.margin + c_cell
        for pa, pb, ta, tb in segs:
            ta, tb = max(ta, t0w), min(tb, t1w)
            if tb <= ta and not (ta == tb == 0):
                continue
            x0, y0, x1, y1 = pa["x"], pa["y"], pb["x"], pb["y"]
            i0 = max(int((min(x0, x1) - reach) // dx), 0); i1 = min(int((max(x0, x1) + reach) // dx) + 1, nx)
            j0 = max(int((min(y0, y1) - reach) // dx), 0); j1 = min(int((max(y0, y1) + reach) // dx) + 1, ny)
            if i0 >= i1 or j0 >= j1:
                continue
            QX, QY = np.meshgrid(X[i0:i1], Y[j0:j1])
            dt = max(tb - ta, 1e-9)
            vx, vy = (x1 - x0) / dt, (y1 - y0) / dt
            # 상대위치 r(t) = q − p_a − v (t − ta) ;  τ = t − ta
            rx, ry = QX - x0, QY - y0
            if o.kind == "box":
                hx, hy = o.half_x + o.margin + c_cell, o.half_y + o.margin + c_cell
                lo = np.full(QX.shape, ta); hi = np.full(QX.shape, tb)
                for r, v, h in ((rx, vx, hx), (ry, vy, hy)):
                    if abs(v) < 1e-9:
                        bad = np.abs(r) > h
                        lo[bad], hi[bad] = np.inf, -np.inf
                    else:
                        a1, a2 = (r - h) / v, (r + h) / v
                        lo = np.maximum(lo, ta + np.minimum(a1, a2)); hi = np.minimum(hi, ta + np.maximum(a1, a2))
                zr = [(o.z_min - o.margin, o.z_max + o.margin)]
                lo_l, hi_l = [lo], [hi]
            else:
                rho = o.radius + o.margin + c_cell
                a = vx * vx + vy * vy
                b = -2 * (rx * vx + ry * vy)
                cc = rx * rx + ry * ry - rho * rho
                lo, hi = _quad_interval(a, b, cc, 0.0, tb - ta)
                lo, hi = lo + ta, hi + ta
                if o.kind == "sphere":
                    zc0, zc1 = pa.get("z", 0.5 * (o.z_min + o.z_max)), pb.get("z", 0.5 * (o.z_min + o.z_max))
                    zr = [(min(zc0, zc1) - rho, max(zc0, zc1) + rho)]
                else:
                    zr = [(o.z_min - o.margin, o.z_max + o.margin)]
                lo_l, hi_l = [lo], [hi]
            for (za, zb), lo, hi in zip(zr, lo_l, hi_l):
                ks = np.where((site.z >= za - site.dz / 2) & (site.z <= zb + site.dz / 2))[0]
                if o.kind == "sphere":
                    pass  # 구: 수평 반경으로 보수적 처리(고도 범위 내 전 레이어)
                m = lo <= hi
                if not m.any() or len(ks) == 0:
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
