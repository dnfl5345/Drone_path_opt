"""기체 연결 실행기 – 계획한 임무를 기체에 올리고, 비행 중 위치를 따라가며 속도 명령을 보낸다 (dpo-fly).

실시간 엔진(dpo.live)은 기체를 모의한다. 이 실행기는 기체가 보내는 값을 쓴다.
  위치   GLOBAL_POSITION_INT  → 계획 경로 위의 진행 위치 (가장 가까운 기록) 와 경로 이탈 거리
  바람   WIND                 → 예보와의 차이를 지수평활 (speed_update.WindBias)
  배터리 BATTERY_STATUS       → 계획 대비 SOC 차이 기록 (자동 조치는 하지 않는다)
  명령   MAV_CMD_DO_CHANGE_SPEED (대지속도) – 지금 위치의 계획 속도가 마지막으로 보낸 값과 0.5 m/s 이상 다를 때

안전 원칙
  * 시동·이륙·모드 전환은 하지 않는다. 조종사가 AUTO 로 바꾼 뒤부터 따라간다.
  * 경로점은 건드리지 않는다. 보내는 것은 속도 명령뿐이고, 기체 한계(vg_max)와 3 m/s 사이로 자른다.
  * 경로 이탈이 max_xtrack 을 넘거나 위치가 끊기면 속도 갱신을 멈추고 마지막 속도를 유지한다 (기록에 남긴다).
  * 미션은 '명령 방식'(속도 항목 없음)으로 올려야 한다. 미션 안의 속도 항목은 실행기의 명령을 덮어쓴다.

검증 범위: 모의 기체(dpo.sim_vehicle)와의 UDP 루프백. ArduPilot SITL·실기체에서는 시험하지 않았다.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np

from . import speed_update as SU
from .energy import soc_drop
from .planner import simulate

MODE_AUTO = 3


class FlightRunner:
    def __init__(self, site, drone, link, plan, period=30.0, margin=SU.DEFAULTS["margin"], latency=3.0, max_xtrack=40.0,
                 first_after=5.0, log_path=None, fr=None, clock=time.time):
        """plan: ardupilot.mission_from_legs 가 주는 dict(path, waits, mass, t_cruise0, a, b)."""
        from .data_terrain import site_frame
        self.site, self.drone, self.link, self.plan = site, drone, link, plan
        self.period, self.margin, self.latency, self.max_xtrack, self.first_after = period, margin, latency, max_xtrack, first_after
        self.fr = fr or site_frame()
        self.clock = clock
        self.m = float(plan["mass"])
        path, waits = np.array(plan["path"], float), np.array(plan["waits"], float)
        pred = simulate(site, drone, path, waits, float(plan["t_cruise0"]), self.m)
        if pred is None:
            raise ValueError("계획 경로를 예보 바람에서 비행할 수 없습니다")
        self.cur = dict(path=path, waits=waits, sim=pred, cmd=np.array(pred["vg"], float), t0=float(plan["t_cruise0"]))
        self.e_done = 0.0                         # 지난 계획에서 이미 쓴 예상 에너지 [J]
        self.q = 0
        self.bias = SU.WindBias()
        self.phase = "대기"
        self.v_sent = None
        self.next_upd = math.inf
        self.t_cruise = None
        self.soc0 = None
        self.last = {}
        self.rows, self.events = [], []
        self.stats = dict(updates=0, checks=0, cmd_sent=0, cmd_acked=0, cmd_failed=0, max_xtrack=0.0, paused=0)
        self._log = open(log_path, "w", encoding="utf-8") if log_path else None

    # ------------------------------------------------------------ 통신
    def _poll(self):
        mv = self.link.m
        for _ in range(200):
            msg = mv.recv_match(blocking=False)
            if msg is None:
                break
            t = msg.get_type()
            if t in ("GLOBAL_POSITION_INT", "WIND", "BATTERY_STATUS", "HEARTBEAT", "MISSION_CURRENT"):
                if t == "HEARTBEAT" and msg.get_srcSystem() == mv.source_system:
                    continue
                self.last[t] = (self.clock(), msg)
            elif t == "COMMAND_ACK" and msg.command == 178:
                self.stats["cmd_acked" if msg.result == 0 else "cmd_failed"] += 1
                if msg.result != 0:
                    self._ev("warn", f"속도 명령 거부 (result {msg.result})")

    def _ev(self, kind, msg):
        self.events.append(dict(t=round(self.clock(), 2), kind=kind, msg=msg))

    def _send_speed(self, v):
        v = float(np.clip(round(v * 2) / 2.0, 3.0, self.drone.vg_max))
        if self.v_sent is not None and abs(v - self.v_sent) < 0.5 - 1e-9:
            return
        self.link.send_change_speed(v, wait_ack=False)
        self.v_sent = v
        self.stats["cmd_sent"] += 1
        self._ev("speed", f"속도 명령 {v:.1f} m/s")

    # ------------------------------------------------------------ 위치 → 진행
    def _xy(self, msg):
        from .data_terrain import ll2xy
        x, y = ll2xy(msg.lon / 1e7, msg.lat / 1e7, self.fr)
        return float(x), float(y)

    def _progress(self, x, y, z):
        """계획 궤적에서 지금 위치에 가장 가까운 기록 (뒤로 가지 않는다). 반환: (기록 번호, 거리 [m])."""
        sim = self.cur["sim"]
        a, b = self.q, min(self.q + 80, len(sim["t"]))
        d = np.sqrt((sim["x"][a:b] - x) ** 2 + (sim["y"][a:b] - y) ** 2 + (0.5 * (sim["z"][a:b] - z)) ** 2)
        k = int(np.argmin(d))
        return a + k, float(d[k])

    # ------------------------------------------------------------ 한 주기
    def step(self):
        self._poll()
        now = self.clock()
        hb, pos = self.last.get("HEARTBEAT"), self.last.get("GLOBAL_POSITION_INT")
        if hb is None or pos is None:
            return self.phase
        armed = bool(hb[1].base_mode & 128)
        auto = hb[1].custom_mode == MODE_AUTO
        p = pos[1]
        alt = p.relative_alt / 1000.0
        if self.phase == "대기":
            if armed and auto:
                self.phase = "이륙"
                self._ev("phase", "AUTO 확인 – 추종 시작")
            return self.phase
        if self.phase in ("이륙", "순항") and not armed:
            self.phase = "완료"
            self._ev("phase", "착륙·시동 꺼짐 – 종료")
            return self.phase
        s = self.site
        x, y = self._xy(p)
        z = float(s.ground[int(np.clip(self.plan["a"][1] // s.dx, 0, s.ny - 1)), int(np.clip(self.plan["a"][0] // s.dx, 0, s.nx - 1))]) + alt
        stale = now - pos[0] > 3.0
        if self.phase == "이륙":
            sim = self.cur["sim"]
            if math.hypot(sim["x"][0] - x, sim["y"][0] - y) < 60.0 and abs(sim["z"][0] - z) < 12.0:
                self.phase, self.t_cruise = "순항", now
                self.next_upd = now + self.first_after / self._rate()
                bs = self.last.get("BATTERY_STATUS")
                self.soc0 = None if bs is None else float(bs[1].battery_remaining)
                self._ev("phase", "순항 진입")
            return self.phase
        if self.phase != "순항":
            return self.phase
        q, dist = self._progress(x, y, z)
        self.q = q
        self.stats["max_xtrack"] = max(self.stats["max_xtrack"], dist)
        sim, cmd = self.cur["sim"], self.cur["cmd"]
        off = stale or dist > self.max_xtrack
        w = self.last.get("WIND")
        row = dict(t=round(now, 2), phase=self.phase, x=round(x, 1), y=round(y, 1), z=round(z, 1), q=q, xtrack=round(dist, 1),
                   bias=round(self.bias.magnitude, 2), v_sent=self.v_sent, gs=round(math.hypot(p.vx, p.vy) / 100.0, 2))
        if w is not None:
            row["wind"] = [round(float(w[1].direction), 1), round(float(w[1].speed), 2)]
        bs = self.last.get("BATTERY_STATUS")
        if bs is not None and self.soc0 is not None:
            row["soc_meas"] = float(bs[1].battery_remaining)
            row["soc_pred"] = round(self.soc0 - soc_drop(self.e_done + float(sim["e"][q]), self.drone), 2)
        if off:
            if not self.stats["paused"]:
                self._ev("warn", "위치 끊김 – 속도 갱신 중지" if stale else f"경로 이탈 {dist:.0f} m – 속도 갱신 중지")
            self.stats["paused"] += 1
        else:
            if now >= self.next_upd:
                self.next_upd = now + self.period / self._rate()
                self._update(q, x, y, z, w)
                sim, cmd = self.cur["sim"], self.cur["cmd"]
                q = self.q
            # 지연만큼 앞의 계획 속도를 보낸다 (호버 기록은 건너뜀)
            k = int(min(np.searchsorted(sim["t"], sim["t"][q] + self.latency), len(cmd) - 1))
            while k < len(cmd) - 1 and sim["hov"][k]:
                k += 1
            if not sim["hov"][k]:
                self._send_speed(cmd[k])
        self.rows.append(row)
        if self._log:
            self._log.write(json.dumps(row, ensure_ascii=False) + "\n")
        return self.phase

    def _rate(self):
        return getattr(self, "time_rate", 1.0)      # 리허설에서 모의 기체가 빨리 감기일 때의 배율

    def _update(self, q, x, y, z, w):
        """속도 갱신 1회 (speed_update.propose 를 실제 위치 기준으로)."""
        s, d, cur = self.site, self.drone, self.cur
        T = float(cur["sim"]["t"][q])
        self.stats["checks"] += 1
        if w is not None:
            from .mavlink_bridge import wind_to_uv
            self.bias.update(wind_to_uv(w[1].direction, w[1].speed), SU.wind_at_xyz(s, T, x, y, z), T)
        if self.bias.n == 0 or self.bias.magnitude < 0.05:
            return
        p = SU.propose(s, d, cur["path"], cur["waits"], cur["sim"], cur["cmd"], cur["t0"], T + self.latency, self.m,
                       self.bias.value, margin=self.margin)
        if p is None:
            return
        self.e_done += float(cur["sim"]["e"][p["q"]])
        self.cur = dict(path=p["pts"], waits=p["waits"], sim=p["pred"], cmd=p["cmd"], t0=p["t0"])
        self.q = 0
        self.stats["updates"] += 1
        self._ev("update", f"속도 갱신: 바람 편차 {self.bias.magnitude:.1f} m/s, 남은 구간 예상 에너지 −{p['gain'] * 100:.1f}%")

    def run(self, timeout=3600.0, dt=0.2):
        t_end = self.clock() + timeout
        while self.clock() < t_end and self.phase != "완료":
            self.step()
            time.sleep(dt)
        return self.summary()

    def summary(self):
        out = dict(phase=self.phase, **self.stats, bias=self.bias.magnitude, events=self.events)
        socs = [(r["soc_meas"], r["soc_pred"]) for r in self.rows if "soc_meas" in r]
        if socs:
            out.update(soc_meas_last=socs[-1][0], soc_pred_last=socs[-1][1])
        return out

    def close(self):
        if self._log:
            self._log.close()


# ---------------------------------------------------------------- 명령줄
def main(argv=None):
    import argparse
    import sys
    from . import __version__, config as C
    ap = argparse.ArgumentParser(prog="dpo-fly", description="계획한 임무를 기체에 올리고 비행 중 속도 명령을 보낸다 (시동·모드 전환은 조종사가 한다)")
    ap.add_argument("--version", action="version", version=f"drone-path-opt {__version__}")
    ap.add_argument("--url", default=None, help="기체 MAVLink 주소 (예: udpin:0.0.0.0:14550). --rehearse 면 필요 없음")
    ap.add_argument("--site", default=C.SITE_KEY, choices=list(C.SITES))
    ap.add_argument("--drone", default="x650", choices=["m100", "x650"])
    ap.add_argument("--payload", type=float, default=1.0)
    ap.add_argument("--dep-hour", type=int, default=15)
    ap.add_argument("--hub", default=None, metavar="X,Y")
    ap.add_argument("--dst", default=None, metavar="X,Y")
    ap.add_argument("--leg", default="out", help="올릴 구간 이름 (out | back | tour)")
    ap.add_argument("--period", type=float, default=30.0, help="속도 갱신 주기 [s], 0 이면 갱신 없이 계획 속도만 보냄")
    ap.add_argument("--upload", action="store_true", help="미션을 기체에 올린다 (없으면 연결·계획만 확인)")
    ap.add_argument("--log", default=None, metavar="FILE", help="비행 기록 (JSON Lines)")
    ap.add_argument("--rehearse", action="store_true", help="모의 기체로 전 과정을 연습한다 (기체 불필요)")
    ap.add_argument("--wind-rot", type=float, default=0.0, help="리허설: 실제 풍향을 예보에서 이만큼 돌림 [도]")
    ap.add_argument("--rate", type=float, default=20.0, help="리허설 빨리 감기 배율")
    a = ap.parse_args(argv)
    C.use_site(a.site)
    missing = C.check_data()
    if missing:
        print("필수 자료가 없습니다: " + ", ".join(missing), file=sys.stderr)
        return 2
    if not a.rehearse and not a.url:
        print("--url 또는 --rehearse 가 필요합니다", file=sys.stderr)
        return 2
    from .mavlink_bridge import MavBridge
    from .webapp.server import App
    app = App()
    req = dict(drone=a.drone, payload=a.payload, dep_hour=a.dep_hour, round_trip=a.leg != "out" and a.leg != "tour", obstacles=[])
    for k in ("hub", "dst"):
        if getattr(a, k):
            req[k] = tuple(float(v) for v in getattr(a, k).split(","))
    r = app.plan(req)
    if not r["feasible"]:
        print("임무 불가: 예비 SOC 미달 또는 경로 없음", file=sys.stderr)
        return 1
    ms = app.mission(dict(speed_mode="command"))
    M = next((x for x in ms["legs"] if x["name"] == a.leg), None)
    if M is None:
        print(f"구간 {a.leg} 이 없습니다 (가능: {[x['name'] for x in ms['legs']]})", file=sys.stderr)
        return 2
    site, drone = app.site, app.drones[a.drone]
    print(f"{C.SITE['label']} · {ms['drone']} · 구간 {a.leg} · 항목 {M['summary']['n_items']}개 · 예상 도착 SOC {M['summary']['soc_end']:.1f} %")
    veh = None
    if a.rehearse:
        from .live import perturbed_site
        from .energy import power_w
        from .sim_vehicle import SimVehicle
        tr = perturbed_site(site, rot=a.wind_rot)
        st = site.lowest_free_k(*M["plan"]["a"])
        wu, wv = tr.wind_at(M["plan"]["t_cruise0"], st[0], st[1], st[2])
        prm, mass = drone.params(), M["plan"]["mass"]
        veh = SimVehicle("udpin:127.0.0.1:14655", rate=a.rate, wind_uv=(float(wu), float(wv)), battery_wh=drone.battery_wh,
                         power_fn=lambda ae, an, vz: power_w(math.hypot(ae, an), vz, mass, 1.2, prm)).start()
        url = "udpout:127.0.0.1:14655"
    else:
        url = a.url
    link = MavBridge(url, timeout=10.0)
    try:
        if not link.connect():
            print(f"연결 실패: {link.last_error}", file=sys.stderr)
            return 3
        print(f"연결됨: {url}")
        if a.upload or a.rehearse:
            if not link.upload_mission(M["items"]):
                print(f"미션 올리기 실패: {link.last_error}", file=sys.stderr)
                return 3
            print("미션을 올렸습니다 (명령 방식: 속도 항목은 첫 구간만).")
        run = FlightRunner(site, drone, link, M["plan"], period=a.period if a.period > 0 else math.inf, log_path=a.log)
        if a.rehearse:
            run.time_rate = a.rate
            veh.start_auto()
            print(f"리허설: 모의 기체가 {a.rate:g}배속으로 비행합니다 (실제 풍향 = 예보 + {a.wind_rot:g}°)")
        else:
            print("조종사가 시동 후 AUTO 로 전환하면 추종을 시작합니다. 이 프로그램은 시동·이륙을 하지 않습니다. (Ctrl+C 종료)")
        try:
            s = run.run(timeout=7200.0, dt=0.05 if a.rehearse else 0.2)
        except KeyboardInterrupt:
            s = run.summary()
        run.close()
        print(f"종료: {s['phase']} · 속도 갱신 {s['updates']}회 / 측정 {s['checks']}회 · 명령 {s['cmd_sent']}회 (수락 {s['cmd_acked']}) · "
              f"최대 경로 이탈 {s['max_xtrack']:.0f} m" + (f" · SOC 실측 {s['soc_meas_last']:.0f} % / 계획 {s['soc_pred_last']:.1f} %"
                                                     if "soc_meas_last" in s else ""))
        return 0
    finally:
        link.close()
        if veh is not None:
            veh.stop()


if __name__ == "__main__":
    import sys
    sys.exit(main())
