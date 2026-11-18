"""SITL 속도 시험 – 실제 ArduCopter 가 속도 명령을 얼마나 따르는지, 꺾이는 경로에서 평균 속도가 얼마나 떨어지는지 잰다.

  A. 직선 2.5 km: 비행 중 속도 명령을 계단으로 바꿔 가며 실제 대지속도를 잰다 (무풍 / 맞바람)
  B. 꺾이는 경로: 구간 길이와 꺾임 각을 바꿔 가며, 같은 명령 속도에서의 평균 대지속도를 잰다

사용: PYTHONPATH=src python scripts/sitl_speed_test.py --bin <arducopter> --defaults <copter.parm>
결과: results/sitl_speed.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, "scripts")
from sitl_check import set_param  # noqa: E402

LAT0, LON0, ALT0 = 35.23923, 128.64658, 30.0
MLAT = 111320.0


def ll(n, e):
    return LAT0 + n / MLAT, LON0 + e / (MLAT * math.cos(math.radians(LAT0)))


def mission(points, alt=50.0, speed=None):
    it = [dict(seq=0, frame=0, cmd=16, p=[0, 0, 0, 0], lat=LAT0, lon=LON0, alt=ALT0),
          dict(seq=1, frame=3, cmd=22, p=[0, 0, 0, 0], lat=LAT0, lon=LON0, alt=alt)]
    if speed:
        it.append(dict(seq=0, frame=0, cmd=178, p=[1, speed, -1, 0], lat=0.0, lon=0.0, alt=0.0))
    for n, e in points:
        la, lo = ll(n, e)
        it.append(dict(seq=0, frame=3, cmd=16, p=[0, 0, 0, 0], lat=la, lon=lo, alt=alt))
    la, lo = ll(*points[-1])
    it.append(dict(seq=0, frame=3, cmd=21, p=[0, 0, 0, 0], lat=la, lon=lo, alt=0.0))
    for q, x in enumerate(it):
        x["seq"] = q
    return it


def fly(binp, defaults, items, params, steps=None, speedup=8.0, dwell=18.0, port=5760):
    """steps: 순항 중 차례로 보낼 속도 [m/s] (각 dwell 초). 반환: dict(samples, steps, wind, T, ok)."""
    from dpo.mavlink_bridge import MavBridge
    wd = tempfile.mkdtemp(prefix="sitl_")
    log = open(os.path.join(wd, "sitl.out"), "w")
    proc = subprocess.Popen([os.path.abspath(binp), "--model", "quad", "--speedup", str(speedup), "--home", f"{LAT0},{LON0},{ALT0},0",
                             "--defaults", os.path.abspath(defaults), "-I0"], cwd=wd, stdout=log, stderr=subprocess.STDOUT)
    link, out = None, dict(ok=False)
    try:
        time.sleep(2.0)
        link = MavBridge(f"tcp:127.0.0.1:{port}", timeout=30.0)
        assert link.connect(), link.last_error
        m = link.m
        m.mav.request_data_stream_send(m.target_system, m.target_component, 0, 10, 1)
        for k, v in dict(AUTO_OPTIONS=3, SIM_BATT_CAP_AH=50.0, **params).items():
            assert set_param(m, k, v), k
        up = False
        for _ in range(6):                       # 기동 직후에는 미션 저장소가 준비되지 않아 거부될 수 있다
            time.sleep(6.0)
            while m.recv_match(blocking=False) is not None:      # 앞선 시도의 남은 메시지를 비운다
                pass
            up = link.upload_mission(items)
            if up:
                break
        assert up, f"미션 올리기 실패: {link.last_error}"
        armed, t0 = False, time.time()
        while time.time() - t0 < 240 and not armed:
            m.set_mode("AUTO")
            m.mav.command_long_send(m.target_system, m.target_component, 400, 0, 1, 0, 0, 0, 0, 0, 0)
            t1 = time.time()
            while time.time() - t1 < 3.0:
                hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
                if hb is not None and hb.get_srcSystem() == m.target_system and hb.base_mode & 128 and hb.custom_mode == 3:
                    armed = True
                    break
        assert armed, "시동 실패"
        samples, winds, acks = [], [], []
        cruise0, step_i, step_t0, cur = None, -1, None, None
        t_end = time.time() + 900
        while time.time() < t_end:
            msg = m.recv_match(type=["GLOBAL_POSITION_INT", "WIND", "HEARTBEAT", "COMMAND_ACK"], blocking=True, timeout=2.0)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "HEARTBEAT" and msg.get_srcSystem() == m.target_system and not (msg.base_mode & 128) and cruise0 is not None:
                break
            if t == "WIND":
                winds.append((msg.direction, msg.speed))
            if t == "COMMAND_ACK" and msg.command == 178:
                acks.append(int(msg.result))
            if t != "GLOBAL_POSITION_INT":
                continue
            ts = msg.time_boot_ms / 1000.0
            gs, alt = math.hypot(msg.vx, msg.vy) / 100.0, msg.relative_alt / 1000.0
            if cruise0 is None and alt > 0.9 * items[1]["alt"] and gs > 1.0:
                cruise0 = ts
            if cruise0 is None:
                continue
            samples.append((ts - cruise0, gs, alt, cur))
            if steps and (step_t0 is None or ts - step_t0 >= dwell) and step_i + 1 < len(steps):
                step_i += 1
                step_t0, cur = ts, steps[step_i]
                m.mav.command_long_send(m.target_system, m.target_component, 178, 0, 1, float(cur), -1, 0, 0, 0, 0)
        S = np.array([(a, b, c) for a, b, c, _ in samples], float)
        cr = S[S[:, 2] > 0.9 * items[1]["alt"]]
        out = dict(ok=True, T=float(cr[-1, 0] - cr[0, 0]) if len(cr) else None, gs_mean=float(cr[:, 1].mean()) if len(cr) else None,
                   gs_p95=float(np.percentile(cr[:, 1], 95)) if len(cr) else None, acks=acks)
        if steps:
            res = []
            for v in steps:
                g = np.array([s[1] for s in samples if s[3] == v])
                if len(g) > 20:
                    res.append(dict(cmd=v, gs=float(np.median(g[int(len(g) * 0.55):])), n=int(len(g))))
            out["steps"] = res
        if winds:
            w = np.array(winds[len(winds) // 2:])
            u, v = -w[:, 1] * np.sin(np.radians(w[:, 0])), -w[:, 1] * np.cos(np.radians(w[:, 0]))
            out["wind_est"] = dict(speed=float(np.hypot(u.mean(), v.mean())), dir_from=float(math.degrees(math.atan2(-u.mean(), -v.mean())) % 360))
    finally:
        if link is not None:
            link.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa
            proc.kill()
        log.close()
    return out


def zigzag(leg, ang_deg, n=10):
    pts, x, y = [], 0.0, 0.0
    for k in range(n):
        a = math.radians(ang_deg / 2 * (1 if k % 2 == 0 else -1))
        x += leg * math.cos(a); y += leg * math.sin(a)
        pts.append((x, y))
    return pts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--defaults", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    from dpo import config as C
    f = C.RES / "sitl_speed.json"
    R = json.loads(f.read_text()) if f.exists() else {}
    straight = mission([(2500.0, 0.0)])
    cases = {
        "A_nowind": lambda: fly(a.bin, a.defaults, straight, dict(SIM_WIND_SPD=0.0), steps=[5, 8, 12, 15, 18, 10]),
        "A_headwind6": lambda: fly(a.bin, a.defaults, straight, dict(SIM_WIND_SPD=6.0, SIM_WIND_DIR=0.0, EK3_DRAG_BCOEF_X=60.0, EK3_DRAG_BCOEF_Y=60.0),
                                   steps=[5, 8, 12, 15, 10]),
    }
    for leg in (100, 200, 400):                  # 꺾이는 경로: 기체 한계(약 10 m/s)보다 낮은 8 m/s 로 잰다
        for ang in (30, 90):
            cases[f"B_leg{leg}_ang{ang}"] = (lambda leg=leg, ang=ang: fly(a.bin, a.defaults, mission(zigzag(leg, ang, 8), speed=8.0),
                                                                             dict(SIM_WIND_SPD=0.0, WP_SPD=8.0)))
    cases["B_straight"] = lambda: fly(a.bin, a.defaults, mission([(1600.0, 0.0)], speed=8.0), dict(SIM_WIND_SPD=0.0, WP_SPD=8.0))
    for bc in (10, 20, 30):                      # 바람 추정의 항력 계수 민감도 (맞바람 6 m/s, 대지속도 4 m/s)
        cases[f"C_bcoef{bc}"] = (lambda bc=bc: fly(a.bin, a.defaults, mission([(700.0, 0.0)], speed=4.0),
                                                   dict(SIM_WIND_SPD=6.0, SIM_WIND_DIR=0.0, EK3_DRAG_BCOEF_X=float(bc), EK3_DRAG_BCOEF_Y=float(bc), WP_SPD=4.0)))
    for k, fn in cases.items():
        if a.only and a.only not in k:
            continue
        try:
            R[k] = fn()
        except Exception as ex:  # noqa
            R[k] = dict(ok=False, error=f"{type(ex).__name__}: {ex}")
        f.write_text(json.dumps(R, indent=1))
        r = R[k]
        print(k, {q: (round(v, 2) if isinstance(v, float) else v) for q, v in r.items() if q != "acks"}, flush=True)


if __name__ == "__main__":
    main()
