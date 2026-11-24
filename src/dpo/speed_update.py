"""비행 중 속도 갱신 – 기체가 잰 바람으로 예보를 보정해 남은 구간의 대지속도 명령을 다시 정한다.

배경 (E14, docs/review_gaps.md): 미션 파일은 구간 대지속도를 계획 때 정해 내려보낸다. 예보가 실제 바람과 다르면
그 속도는 더 이상 최적이 아니고, 손해의 대부분은 경로가 아니라 속도를 다시 정하는 것으로 되찾을 수 있다.

구성
  WindBias          기체가 잰 바람 − 예보 바람의 지수평활 추정값 (du, dv). 예보 전체에 더하는 보정으로 쓴다.
  remaining_route   실행 중인 시뮬레이션의 한 기록 끝에서 남은 경로·대기를 잘라 낸다.
  propose           보정한 예보에서 남은 구간의 속도를 다시 정하고, 지금 명령을 유지할 때와 예상 에너지를 비교한다.
  speed_schedule    시뮬레이션 기록 → 속도 변경 지점 목록 (MAV_CMD_DO_CHANGE_SPEED 로 보낼 명령).

안전 규칙은 부르는 쪽(LiveEngine.speed_update)이 맡는다: 새 속도로 비행한 궤적이 위험 시간구간과 겹치면 버린다.
"""
from __future__ import annotations

import math

import numpy as np

from .planner import _wind, simulate

DEFAULTS = dict(period=30.0,      # 갱신 주기 [s]. 0 이면 끔 (E14: 3–5분 비행에서 30초 이하 권고)
                margin=0.003,     # 남은 구간 예상 에너지가 이 비율 넘게 줄 때만 새 속도를 채택
                alpha=0.6,        # 바람 편차 지수평활 계수 (새 측정의 비중)
                max_bias=15.0,    # 편차 크기 상한 [m/s] (센서 이상값 방지)
                min_remaining=20.0)   # 남은 비행 시간이 이보다 짧으면 갱신하지 않음 [s]


class WindBias:
    """기체가 잰 바람과 예보의 차이 (du, dv) 를 지수평활로 추정한다."""

    def __init__(self, alpha=DEFAULTS["alpha"], max_bias=DEFAULTS["max_bias"]):
        self.alpha, self.max_bias = float(alpha), float(max_bias)
        self.du = self.dv = 0.0
        self.n = 0
        self.last = None            # 마지막 측정 dict(t, meas, fcst)

    def update(self, meas, fcst, t=None):
        """meas, fcst: (u, v) [m/s]. 반환: 갱신된 (du, dv)."""
        eu, ev = float(meas[0]) - float(fcst[0]), float(meas[1]) - float(fcst[1])
        if not (math.isfinite(eu) and math.isfinite(ev)):
            return self.value
        a = self.alpha if self.n else 1.0          # 첫 측정은 그대로 받는다
        self.du = (1 - a) * self.du + a * eu
        self.dv = (1 - a) * self.dv + a * ev
        mag = math.hypot(self.du, self.dv)
        if mag > self.max_bias:
            self.du, self.dv = self.du * self.max_bias / mag, self.dv * self.max_bias / mag
        self.n += 1
        self.last = dict(t=t, meas=(float(meas[0]), float(meas[1])), fcst=(float(fcst[0]), float(fcst[1])))
        return self.value

    @property
    def value(self):
        return (self.du, self.dv)

    @property
    def magnitude(self):
        return math.hypot(self.du, self.dv)


def wind_at_xyz(site, t, x, y, z, U=None, V=None):
    """위치 (x, y, z) [m], 절대 시각 t 의 바람 (u, v)."""
    i = int(np.clip(x // site.dx, 0, site.nx - 1))
    j = int(np.clip(y // site.dx, 0, site.ny - 1))
    k = int(np.clip(round((z - site.z[0]) / site.dz), 0, site.nz - 1))
    return _wind(site.U if U is None else U, site.V if V is None else V, site.t_axis, t, k, j, i)


def remaining_route(path, waits, sim, q):
    """기록 q 가 끝난 순간부터 남은 경로. 반환: (pts, waits, t0) – pts[0] 은 그 순간의 위치(격자 좌표, 실수).

    simulate 의 기록은 '구간 n 의 부분 구간' 또는 '구간 n 출발 전 호버'다. 호버 기록이면 남은 대기 시간을 넘긴다."""
    pts = np.array(path, float)
    waits = np.asarray(waits, float)
    n, f = int(sim["seg"][q]), float(sim["frac"][q])
    t0 = float(sim["t"][q])
    if bool(sim["hov"][q]):                               # 구간 n 출발 전 호버 중: 남은 대기 = 전체 × frac
        rem = float(waits[n + 1]) * f
        out_p = pts[n:]
        out_w = np.concatenate([[0.0, rem], waits[n + 2:]])
        return out_p, out_w, t0
    if f >= 1.0 - 1e-9:                                   # 구간 n 을 다 지남 → 다음 꼭짓점에서 시작
        out_p = pts[n + 1:]
        out_w = np.concatenate([[0.0], waits[n + 2:]])
        return out_p, out_w, t0
    pos = pts[n] + (pts[n + 1] - pts[n]) * f
    out_p = np.vstack([pos[None, :], pts[n + 1:]])
    out_w = np.concatenate([[0.0, 0.0], waits[n + 2:]])
    return out_p, out_w, t0


def propose(site, drone, path, waits, sim, cmd, t_start, t_from, mass, bias, margin=DEFAULTS["margin"],
            min_remaining=DEFAULTS["min_remaining"]):
    """남은 구간의 속도 명령을 다시 정한다.

    path, waits : 지금 비행 중인 경로 (sim 과 같은 기준)
    sim         : 그 경로의 실행 시뮬레이션 (seg·frac·hov 포함)
    cmd         : 지금의 명령 대지속도 (sim 기록 순서)
    t_start     : sim 의 출발 시각 (절대 시각)
    t_from      : 이 시각 이후의 첫 기록 끝에서 갱신한다 (절대 시각)
    bias        : (du, dv) 예보 보정값
    반환: None (갱신 불필요·불가) 또는
          dict(q, t0, pts, waits, pred, cmd, e_new, e_keep, gain) – pred 는 보정한 예보에서의 예상 시뮬레이션.
    """
    q = int(np.searchsorted(sim["t"], t_from, side="left"))
    if q >= len(sim["t"]) - 2 or sim["t"][-1] - sim["t"][q] < min_remaining:
        return None
    pts, w, t0 = remaining_route(path, waits, sim, q)
    if len(pts) < 2:
        return None
    pred = simulate(site, drone, pts, w, t0, mass, bias=bias)
    if pred is None:
        return None
    # 지금 명령을 유지할 때: 같은 경로를 처음부터 보정한 예보로 다시 날려 남은 부분의 에너지를 뗀다
    keep = simulate(site, drone, path, waits, t_start, mass, vcmd=cmd, bias=bias)
    if keep is None or len(keep["e"]) != len(sim["e"]):
        e_keep = math.inf
    else:
        e_keep = float(keep["e"][-1] - keep["e"][q])
    e_new = float(pred["e"][-1])
    gain = 0.0 if not math.isfinite(e_keep) or e_keep <= 0 else 1.0 - e_new / e_keep
    if math.isfinite(e_keep) and gain < margin:
        return None
    return dict(q=q, t0=t0, pts=pts, waits=w, pred=pred, cmd=np.array(pred["vg"], float), e_new=e_new,
                e_keep=e_keep, gain=gain)


def execute(truth, drone, path, waits, t0, mass, cmd):
    """실제 바람(truth)에서 명령 대지속도 cmd 로 비행한 결과 (낼 수 없는 속도는 낮춰서 비행)."""
    return simulate(truth, drone, path, waits, t0, mass, vcmd=cmd)


def speed_schedule(sim, t_start, t_ref=0.0, dv=0.5, min_len=75.0, fr=None):
    """시뮬레이션 기록 → 속도 변경 지점 목록.

    t_start: sim 의 출발 시각. 명령은 0.5 m/s 단위로 반올림하고, min_len [m] 보다 짧은 묶음은 이웃과 합친다 (명령이 너무 잦지 않게).
    반환: [dict(t, x, y, z, v, lat?, lon?)] – 그 지점을 지날 때 그 속도로 바꾸라는 뜻.
    """
    vg, hov = np.asarray(sim["vg"], float), np.asarray(sim["hov"], bool)
    idx = np.where(~hov & (vg > 0))[0]
    if len(idx) == 0:
        return []
    t_prev = np.concatenate([[float(t_start)], sim["t"][:-1]])
    seglen = vg * (sim["t"] - t_prev)                    # 부분 구간의 수평 길이
    groups = []                                          # [첫 기록, 끝 기록(포함 안 함), 길이, 시간]
    a = idx[0]
    for u, b_ in zip(idx[:-1], idx[1:]):
        if abs(round(vg[b_] / dv) - round(vg[a] / dv)) >= 1 or b_ != u + 1:
            groups.append([a, u + 1])
            a = b_
    groups.append([a, idx[-1] + 1])
    k = 0
    while k < len(groups) and len(groups) > 1:
        g = groups[k]
        m = ~hov[g[0]:g[1]]
        if seglen[g[0]:g[1]][m].sum() < min_len:
            if k + 1 < len(groups):
                groups[k + 1][0] = g[0]
            else:
                groups[k - 1][1] = g[1]
            groups.pop(k)
        else:
            k += 1
    out, v_prev = [], None
    for a, b_ in groups:
        m = ~hov[a:b_]
        T = (sim["t"][a:b_] - t_prev[a:b_])[m].sum()
        v = round(float(seglen[a:b_][m].sum() / max(T, 1e-9)) / dv) * dv
        if v_prev is not None and abs(v - v_prev) < dv - 1e-9:
            continue
        row = dict(t=float(t_prev[a] - t_ref), x=float(sim["x"][a]), y=float(sim["y"][a]), z=float(sim["z"][a]), v=float(v))
        if fr is not None:
            from .data_terrain import xy2ll
            lon, lat = (float(c) for c in xy2ll(row["x"], row["y"], fr))
            row.update(lat=lat, lon=lon)
        out.append(row)
        v_prev = v
    return out
