"""비행 로그 → 전력 모델 보정 (dpo-calibrate).

실증 기체(X650)의 전력 모델은 제원으로 추정한 것이다. 속도에서 나오는 이득의 크기가 이 모델(특히 항력 면적 CdA)에
좌우되므로, 정속 수평 비행 로그로 보정해야 한다 (docs/review_gaps.md 표 5, docs/field_demo_x650.md).

읽는 로그
  ArduPilot DataFlash (.bin · .BIN): BAT (Volt, Curr), GPS (Spd, GCrs, VZ), XKF2 (VWN, VWE: EKF 가 추정한 바람)
  MAVLink 텔레메트리 (.tlog)        : BATTERY_STATUS, GLOBAL_POSITION_INT, WIND
  메시지·필드 이름은 ArduPilot Copter-4.7.2 소스(LogStructure.h)에서 확인했다.

절차
  read_log        → 1 Hz 로 맞춘 전압·전류·대지속도 벡터·수직속도·바람
  steady_points   → 속도와 전력이 일정한 구간(기본 5 s)의 (대기속도, 수직속도, 전력) 점
  fit_drone       → η, P0, CdA 를 최소제곱으로 맞춤 (질량과 항전 전력은 고정: 항전 전력은 P0 와 구별되지 않는다)
                    개별 계수보다 전력 곡선 P(대기속도) 가 맞는지가 중요하다. 계획기는 곡선만 쓴다.
바람 추정값이 로그에 없으면 대기속도 = 대지속도로 둔다. 이때는 같은 구간을 왕복해 맞바람·뒷바람을 함께 넣어야 한다.
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np

from .energy import RHO0, Drone, power_w

# ------------------------------------------------------------------ 읽기


def read_log(path):
    """반환: dict(t, volt, curr, vn, ve, vz, wn, we, has_wind) – 1 Hz, vz 는 위쪽이 +."""
    from pymavlink import mavutil
    m = mavutil.mavlink_connection(str(path))
    is_df = str(path).lower().endswith((".bin", ".log"))
    B, G, Wd = [], [], []
    types = ["BAT", "GPS", "XKF2"] if is_df else ["BATTERY_STATUS", "GLOBAL_POSITION_INT", "WIND"]
    while True:
        msg = m.recv_match(type=types, blocking=False)
        if msg is None:
            break
        t = msg.get_type()
        if is_df:
            ts = msg.TimeUS * 1e-6
            if t == "BAT" and getattr(msg, "Inst", 0) == 0:
                B.append((ts, msg.Volt, msg.Curr))
            elif t == "GPS" and getattr(msg, "I", 0) == 0:
                c = math.radians(msg.GCrs)
                G.append((ts, msg.Spd * math.cos(c), msg.Spd * math.sin(c), -msg.VZ))
            elif t == "XKF2" and getattr(msg, "C", 0) == 0:
                Wd.append((ts, msg.VWN, msg.VWE))
        else:
            ts = getattr(msg, "_timestamp", 0.0)
            if t == "BATTERY_STATUS":
                B.append((ts, msg.voltages[0] / 1000.0, msg.current_battery / 100.0))
            elif t == "GLOBAL_POSITION_INT":
                G.append((ts, msg.vx / 100.0, msg.vy / 100.0, -msg.vz / 100.0))
            elif t == "WIND":
                th = math.radians(msg.direction)
                Wd.append((ts, -msg.speed * math.cos(th), -msg.speed * math.sin(th)))
    if len(B) < 5 or len(G) < 5:
        raise ValueError(f"{path}: 배터리·속도 기록이 부족합니다 (BAT {len(B)}, GPS {len(G)})")
    B, G = np.array(B, float), np.array(G, float)
    t0, t1 = max(B[0, 0], G[0, 0]), min(B[-1, 0], G[-1, 0])
    t = np.arange(t0, t1, 1.0)
    out = dict(t=t - t0, volt=np.interp(t, B[:, 0], B[:, 1]), curr=np.interp(t, B[:, 0], B[:, 2]),
               vn=np.interp(t, G[:, 0], G[:, 1]), ve=np.interp(t, G[:, 0], G[:, 2]), vz=np.interp(t, G[:, 0], G[:, 3]))
    if len(Wd) >= 5:
        Wd = np.array(Wd, float)
        out.update(wn=np.interp(t, Wd[:, 0], Wd[:, 1]), we=np.interp(t, Wd[:, 0], Wd[:, 2]), has_wind=True)
    else:
        out.update(wn=np.zeros(len(t)), we=np.zeros(len(t)), has_wind=False)
    return out


def steady_points(log, win=5, dv=0.7, dvz=0.3, min_curr=2.0, cv_p=0.12):
    """속도·전력이 일정한 구간의 평균값. 반환: 배열 (n, 4) = [대기속도, 수직속도, 전력, 대지속도]."""
    P = log["volt"] * log["curr"]
    vg = np.hypot(log["vn"], log["ve"])
    va = np.hypot(log["vn"] - log["wn"], log["ve"] - log["we"])
    pts = []
    for a in range(0, len(P) - win + 1, win):
        s = slice(a, a + win)
        if log["curr"][s].mean() < min_curr or vg[s].std() > dv or abs(log["vz"][s].mean()) > dvz or log["vz"][s].std() > dvz:
            continue
        if P[s].std() / max(P[s].mean(), 1e-9) > cv_p:
            continue
        pts.append((va[s].mean(), log["vz"][s].mean(), P[s].mean(), vg[s].mean()))
    return np.array(pts, float).reshape(-1, 4)


# ------------------------------------------------------------------ 맞춤
def fit_drone(points, drone: Drone, mass: float, rho: float = RHO0):
    """points: steady_points 의 결과 (여러 로그면 이어 붙인 것). 반환: (보정한 Drone, 통계 dict)."""
    from scipy.optimize import least_squares
    if len(points) < 8:
        raise ValueError(f"정상 구간이 {len(points)}개뿐입니다 (8개 이상 필요). 정속 구간을 더 길게 비행하십시오.")
    va, vz, P = points[:, 0], points[:, 1], points[:, 2]

    def mk(x):
        return Drone(**{**drone.__dict__, "eta": float(x[0]), "p0": float(x[1]), "cda": float(x[2])})

    def model(x):
        prm = mk(x).params()
        return np.array([power_w(a, z, mass, rho, prm) for a, z in zip(va, vz)])

    sol = least_squares(lambda x: (model(x) - P) / P, x0=[drone.eta, drone.p0, drone.cda],
                        bounds=([0.4, 5.0, 0.02], [0.9, 200.0, 0.5]))
    d = mk(sol.x)
    pred = model(sol.x)
    span = float(va.max() - va.min())
    v = np.arange(3.0, drone.va_max + 1e-9, 0.5)
    epm = np.array([power_w(x, 0.0, mass, rho, d.params()) / x for x in v])
    e10 = power_w(10.0, 0.0, mass, rho, d.params()) / 10.0
    warn = []
    if span < 6.0:
        warn.append(f"대기속도 범위가 {span:.1f} m/s 로 좁습니다. 항력 면적(CdA)이 잘 정해지지 않습니다 (5–15 m/s 권장).")
    if points[:, 3].min() > 1.0:
        warn.append("호버(정지) 구간이 없습니다. 효율 η 와 형상 전력 P0 가 서로 바뀌어 맞을 수 있습니다.")
    if va.max() < drone.va_max - 1.0:
        warn.append(f"가장 빠른 구간이 {va.max():.1f} m/s 입니다. 그보다 빠른 속도의 전력은 외삽입니다.")
    stats = dict(n=int(len(P)), va_min=float(va.min()), va_max=float(va.max()),
                 mape=float(np.mean(np.abs(pred - P) / P) * 100), r2=float(1 - ((pred - P) ** 2).sum() / ((P - P.mean()) ** 2).sum()),
                 params=dict(eta=d.eta, p0=d.p0, cda=d.cda, p_av=d.p_av),
                 before=dict(eta=drone.eta, p0=drone.p0, cda=drone.cda, p_av=drone.p_av),
                 hover_w=float(power_w(0.0, 0.0, mass, rho, d.params())), v_best=float(v[epm.argmin()]),
                 va_obs_max=float(np.percentile(va, 98)),
                 gain_vs_10=float((epm.min() / e10 - 1) * 100), mass=float(mass), warnings=warn)
    return d, stats


# ------------------------------------------------------------------ DataFlash 쓰기 (시험·리허설용)
_FMT = {"Q": "<Q", "B": "<B", "f": "<f", "c": "<h", "L": "<i", "e": "<i", "H": "<H", "I": "<I", "h": "<h"}
_MUL = {"c": 100.0, "e": 100.0, "L": 1e7}
DF_DEFS = {   # 이름: (번호, 형식, 열) – ArduPilot Copter-4.7.2 LogStructure.h 와 같은 정의
    "BAT": (130, "QBfffffcfBBB", "TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot,Temp,Res,RemPct,H,SH"),
    "GPS": (131, "QBBIHBcLLeffffB", "TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U"),
    "XKF2": (132, "QBccccchhhhhhfff", "TimeUS,C,AX,AY,AZ,VWN,VWE,MN,ME,MD,MX,MY,MZ,IDX,IDY,IS"),
}


def write_dflog(path, rows):
    """rows: [(이름, {열: 값})] 을 DataFlash 이진 로그로 쓴다. 실제 로그의 대용(시험·예시)이다."""
    def fmt_msg(tid, length, name, fmt, cols):
        return b"\xa3\x95\x80" + struct.pack("<BB4s16s64s", tid, length, name.encode(), fmt.encode(), cols.encode())
    with open(path, "wb") as f:
        f.write(fmt_msg(128, 89, "FMT", "BBnNZ", "Type,Length,Name,Format,Columns"))
        for name, (tid, fmt, cols) in DF_DEFS.items():
            f.write(fmt_msg(tid, 3 + sum(struct.calcsize(_FMT[c]) for c in fmt), name, fmt, cols))
        for name, vals in rows:
            tid, fmt, cols = DF_DEFS[name]
            b = b"\xa3\x95" + bytes([tid])
            for c, k in zip(fmt, cols.split(",")):
                v = vals.get(k, 0)
                v = v * _MUL[c] if c in _MUL else v
                b += struct.pack(_FMT[c], float(v) if c == "f" else int(round(v)))
            f.write(b)


def synth_flight(path, drone: Drone, mass, speeds=(0, 5, 7.5, 10, 12.5, 15), leg_s=40, wind=(2.0, -1.0), noise=0.02, seed=0,
                 volt=23.0):
    """정속 구간을 차례로 나는 가상의 비행 로그 (각 속도로 왕복). 보정 절차를 로그 없이 확인하는 용도."""
    rng = np.random.default_rng(seed)
    rows, t = [], 0.0
    prm = drone.params()
    for v in speeds:
        for hdg in ((0.0, 180.0) if v > 0 else (0.0,)):
            c = math.radians(hdg)
            vn, ve = v * math.cos(c), v * math.sin(c)
            va = math.hypot(vn - wind[0], ve - wind[1])
            P = power_w(va, 0.0, mass, RHO0, prm)
            for _ in range(int(leg_s * 5)):
                t += 0.2
                us = int(t * 1e6)
                p = P * (1 + noise * rng.standard_normal())
                rows.append(("BAT", dict(TimeUS=us, Volt=volt, Curr=p / volt)))
                rows.append(("GPS", dict(TimeUS=us, Status=3, Spd=v + 0.05 * rng.standard_normal(), GCrs=hdg, VZ=0.0)))
                rows.append(("XKF2", dict(TimeUS=us, VWN=wind[0], VWE=wind[1])))
    write_dflog(path, rows)
    return path


# ------------------------------------------------------------------ 명령줄
def main(argv=None):
    import argparse
    import sys
    from datetime import date
    from . import __version__, drones as DR
    ap = argparse.ArgumentParser(prog="dpo-calibrate", description="비행 로그(.bin/.tlog)로 기체 전력 모델을 보정한다")
    ap.add_argument("--version", action="version", version=f"drone-path-opt {__version__}")
    ap.add_argument("logs", nargs="+", help="정속 수평 비행 로그 (여러 개 가능)")
    ap.add_argument("--drone", default="x650", choices=["m100", "x650"])
    ap.add_argument("--mass", type=float, required=True, help="비행 때의 총중량 [kg] (기체 + 배터리 + 탑재)")
    ap.add_argument("--rho", type=float, default=RHO0, help="공기밀도 [kg/m³]")
    ap.add_argument("--save", action="store_true", help="보정값을 DPO_HOME/calibration/<기체>.json 에 저장 (이후 계획에 쓰임)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d0 = DR.get(a.drone, calibrated=False)
    pts, no_wind = [], []
    for f in a.logs:
        lg = read_log(f)
        p = steady_points(lg)
        pts.append(p)
        if not lg["has_wind"]:
            no_wind.append(f)
    pts = np.vstack(pts)
    try:
        d, st = fit_drone(pts, d0, a.mass, a.rho)
    except ValueError as ex:
        print(f"보정 불가: {ex}", file=sys.stderr)
        return 1
    if no_wind:
        st["warnings"].append(f"바람 추정값이 없는 로그 {len(no_wind)}개: 대기속도 = 대지속도로 두었습니다 (EK3_DRAG_BCOEF_X/Y 설정 필요).")
    st.update(drone=a.drone, date=str(date.today()), logs=[str(f) for f in a.logs])
    if a.save:
        f = DR.calibration_file(a.drone)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        st["saved"] = str(f)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    else:
        b, c = st["before"], st["params"]
        print(f"정상 구간 {st['n']}개 · 대기속도 {st['va_min']:.1f}–{st['va_max']:.1f} m/s · 오차 {st['mape']:.1f} % · R² {st['r2']:.3f}")
        print(f"  효율 η      {b['eta']:.3f} → {c['eta']:.3f}\n  형상 전력 P0 {b['p0']:.1f} → {c['p0']:.1f} W\n"
              f"  항력 면적 CdA {b['cda']:.3f} → {c['cda']:.3f} m²  (항전 전력 {c['p_av']:.0f} W 는 고정)")
        print(f"  호버 전력 {st['hover_w']:.0f} W · 미터당 에너지가 가장 적은 속도 {st['v_best']:.1f} m/s · 10 m/s 대비 {st['gain_vs_10']:+.1f} %")
        print(f"  로그에서 본 가장 빠른 정속 대기속도 {st['va_obs_max']:.1f} m/s (계획기의 최대 대기속도 {d0.va_max:g} m/s 와 비교하십시오. "
              "ArduCopter 는 낼 수 없는 속도 명령도 수락합니다)")
        for w in st["warnings"]:
            print(f"  주의: {w}")
        if a.save:
            print(f"  저장: {st['saved']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
