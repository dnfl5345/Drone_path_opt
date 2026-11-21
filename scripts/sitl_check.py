"""ArduPilot SITL 검증 – 실제 ArduCopter 펌웨어(모의 실행)에서 명령 방식 미션과 속도 갱신을 확인한다.

확인하는 것
  1. 명령 방식 미션이 수락되고 되읽은 항목 수가 같다
  2. AUTO 에서 끝까지 비행하고 착륙·시동 꺼짐까지 간다
  3. 비행 중 MAV_CMD_DO_CHANGE_SPEED 가 수락되고 실제 대지속도에 반영된다
  4. SIM_WIND_* 로 준 바람이 WIND 메시지(EKF 추정)로 돌아온다
  5. 펌웨어가 쓴 DataFlash 로그를 dpo-calibrate 의 읽기 함수가 읽는다

사용: PYTHONPATH=src python scripts/sitl_check.py --bin <arducopter> --defaults <copter.parm> [--site changwon --drone x650]
결과: results/sitl_check.json, results/missions/sitl_flight.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np


def set_param(m, name, value, timeout=5.0):
    mv = m.mav
    t_end = time.time() + timeout
    while time.time() < t_end:
        mv.param_set_send(m.target_system, m.target_component, name.encode(), float(value), 9)       # 9 = REAL32
        msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
        if msg is not None and msg.param_id.strip("\x00") == name and abs(msg.param_value - value) < 1e-3 * max(1.0, abs(value)):
            return True
    return False


def get_param(m, name, timeout=5.0):
    t_end = time.time() + timeout
    while time.time() < t_end:
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
        if msg is not None and msg.param_id.strip("\x00") == name:
            return float(msg.param_value)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--defaults", required=True)
    ap.add_argument("--site", default="changwon")
    ap.add_argument("--drone", default="x650")
    ap.add_argument("--payload", type=float, default=1.0)
    ap.add_argument("--speedup", type=float, default=5.0)
    ap.add_argument("--wind", default="6,250", help="SITL 바람: 풍속 [m/s], 불어오는 방향 [도]")
    ap.add_argument("--period", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=5760)
    ap.add_argument("--bcoef", type=float, default=10.0, help="EK3_DRAG_BCOEF_X/Y. SITL 기본 기체에서는 10 근처가 바람을 맞게 추정했다")
    a = ap.parse_args(argv)
    from dpo import config as C
    C.use_site(a.site)
    from dpo.data_terrain import site_frame, xy2ll
    from dpo.flight import FlightRunner
    from dpo.mavlink_bridge import MavBridge, wind_to_uv
    from dpo.webapp.server import App
    app = App()
    r = app.plan(dict(drone=a.drone, payload=a.payload, dep_hour=15, round_trip=False, obstacles=[]))
    assert r["feasible"]
    M = app.mission(dict(speed_mode="command"))["legs"][0]
    site, drone = app.site, app.drones[a.drone]
    ax, ay = M["plan"]["a"]
    lon, lat = (float(v) for v in xy2ll(ax, ay, site_frame()))
    gh = float(site.ground[int(ay // site.dx), int(ax // site.dx)])
    out = dict(site=a.site, drone=a.drone, firmware=None, checks={}, mission=M["summary"])
    wd = tempfile.mkdtemp(prefix="sitl_")
    log = open(os.path.join(wd, "sitl.out"), "w")
    proc = subprocess.Popen([os.path.abspath(a.bin), "--model", "quad", "--speedup", str(a.speedup), "--home", f"{lat:.7f},{lon:.7f},{gh:.1f},0",
                             "--defaults", os.path.abspath(a.defaults), "-I0"], cwd=wd, stdout=log, stderr=subprocess.STDOUT)
    link = None
    try:
        time.sleep(2.0)
        link = MavBridge(f"tcp:127.0.0.1:{a.port}", timeout=30.0)
        assert link.connect(), link.last_error
        m = link.m
        m.mav.request_data_stream_send(m.target_system, m.target_component, 0, 5, 1)                 # 모든 스트림 5 Hz
        ws, wdir = (float(v) for v in a.wind.split(","))
        mass = M["plan"]["mass"]
        params = dict(AUTO_OPTIONS=3, SIM_WIND_SPD=ws, SIM_WIND_DIR=wdir, SIM_BATT_CAP_AH=20.0,
                      EK3_DRAG_BCOEF_X=a.bcoef, EK3_DRAG_BCOEF_Y=a.bcoef, WP_SPD=10.0)
        out["params_set"] = {k: bool(set_param(m, k, v)) for k, v in params.items()}
        out["params_read"] = {k: get_param(m, k) for k in ("WP_SPD", "WP_SPD_UP", "WP_SPD_DN", "RTL_ALT_M", "EK3_DRAG_BCOEF_X")}
        t0 = time.time()
        while time.time() - t0 < 20:
            st = m.recv_match(type="STATUSTEXT", blocking=True, timeout=1.0)
            if st is not None and "ArduCopter" in st.text:
                out["firmware"] = st.text
                break
        if out["firmware"] is None:
            m.mav.command_long_send(m.target_system, m.target_component, 520, 0, 1, 0, 0, 0, 0, 0, 0)   # AUTOPILOT_VERSION 요청
            v = m.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=5.0)
            if v is not None:
                fv = v.flight_sw_version
                out["firmware"] = f"ArduCopter {fv >> 24 & 255}.{fv >> 16 & 255}.{fv >> 8 & 255}"
        # 1. 미션 올리기와 되읽기
        ok = False
        for _ in range(6):                       # 기동 직후에는 미션 저장소가 준비되지 않아 거부될 수 있다
            while m.recv_match(blocking=False) is not None:
                pass
            ok = link.upload_mission(M["items"])
            if ok:
                break
            time.sleep(6.0)
        m.mav.mission_request_list_send(m.target_system, m.target_component)
        cnt = m.recv_match(type="MISSION_COUNT", blocking=True, timeout=10.0)
        out["checks"]["mission_accepted"] = bool(ok)
        out["checks"]["mission_count_readback"] = None if cnt is None else int(cnt.count)
        out["checks"]["mission_count_sent"] = len(M["items"])
        # AUTO + 시동 (EKF 가 준비될 때까지 다시 시도). 실기체에서는 조종사가 하는 일이다.
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
        out["checks"]["armed_in_auto"] = armed
        assert armed, "시동 실패 (EKF·사전 점검)"
        fl = C.RES / "missions" / "sitl_flight.jsonl"
        run = FlightRunner(site, drone, link, M["plan"], period=a.period, log_path=str(fl))
        run.time_rate = a.speedup
        s = run.run(timeout=1500.0, dt=0.05)
        run.close()
        out["runner"] = {k: v for k, v in s.items() if k != "events"}
        out["v_cap"] = run.v_cap
        out["events"] = [e["msg"] for e in s["events"]][:40]
        rows = run.rows
        out["checks"]["flight_completed"] = s["phase"] == "완료"
        out["checks"]["speed_cmds_all_accepted"] = s["cmd_sent"] > 0 and s["cmd_acked"] == s["cmd_sent"]
        # 3. 속도 반영: 같은 명령이 이어진 구간(뒤쪽 60 %)의 실제 대지속도와 명령의 차이
        err, seg = [], []
        for rw in rows + [dict(v_sent=None)]:
            if seg and rw.get("v_sent") != seg[0]["v_sent"]:
                if len(seg) >= 15 and seg[0]["v_sent"] is not None:
                    tail = seg[int(len(seg) * 0.4):]
                    err.append((seg[0]["v_sent"], float(np.median([x["gs"] for x in tail]))))
                seg = []
            if rw.get("v_sent") is not None or "gs" in rw:
                seg.append(rw)
        out["speed_tracking"] = [dict(cmd=c, gs=round(g, 2)) for c, g in err]
        out["checks"]["speed_tracking_median_abs_err"] = float(np.median([abs(c - g) for c, g in err])) if err else None
        # 4. 바람 추정
        w = [rw["wind"] for rw in rows if "wind" in rw]
        if w:
            uv = np.array([wind_to_uv(d_, s_) for d_, s_ in w[len(w) // 3:]])
            tu, tv = wind_to_uv(wdir, ws)
            out["wind"] = dict(sim=[ws, wdir], est_speed=float(np.hypot(*uv.mean(0))),
                               est_dir=float(math.degrees(math.atan2(-uv[:, 0].mean(), -uv[:, 1].mean())) % 360),
                               vec_err=float(np.hypot(uv[:, 0].mean() - tu, uv[:, 1].mean() - tv)), n=len(w))
        out["checks"]["wind_received"] = bool(w)
        xt = [rw["xtrack"] for rw in rows]
        out["xtrack"] = dict(median=float(np.median(xt)), p95=float(np.percentile(xt, 95)), max=float(np.max(xt)))
    finally:
        if link is not None:
            link.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa
            proc.kill()
        log.close()
    # 5. 펌웨어가 쓴 로그 읽기
    bins = sorted(glob.glob(os.path.join(wd, "logs", "*.BIN")))
    if bins:
        from dpo import logcal as LC
        try:
            lg = LC.read_log(bins[-1])
            pts = LC.steady_points(lg)
            out["dataflash"] = dict(file=os.path.basename(bins[-1]), seconds=int(len(lg["t"])), has_wind=bool(lg["has_wind"]), steady_points=int(len(pts)),
                                    va_range=[float(pts[:, 0].min()), float(pts[:, 0].max())] if len(pts) else None)
            out["checks"]["dataflash_readable"] = True
        except Exception as ex:  # noqa
            out["dataflash"] = dict(error=f"{type(ex).__name__}: {ex}")
            out["checks"]["dataflash_readable"] = False
    else:
        out["checks"]["dataflash_readable"] = False
    out["workdir"] = wd
    (C.RES / "sitl_check.json").write_text(json.dumps(json.loads(json.dumps(out, default=float)), ensure_ascii=False, indent=1))
    print(json.dumps(out["checks"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
