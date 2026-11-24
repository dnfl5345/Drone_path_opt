"""모의 기체 (리허설·시험용) – 올린 미션을 따라 움직이며 MAVLink 로 위치·바람·배터리를 내보낸다.

ArduPilot 이 아니다. 미션 올리기, AUTO 시작, 경로점 추종, 속도 명령(미션 항목·COMMAND 모두 '나중 것이 이김'),
호버 대기, 착륙을 운동학적으로 흉내 낸다. 자세·가감속·돌풍·제어 오차는 없다. 실행기(dpo.flight)의 논리와
통신 절차를 기체 없이 확인하는 용도다. 실제 펌웨어 검증은 SITL 로 해야 한다 (docs/field_demo_x650.md).
"""
from __future__ import annotations

import math
import threading
import time

MLAT = 111320.0
CMD_WP, CMD_LOITER, CMD_LAND, CMD_TAKEOFF, CMD_SPEED, CMD_START = 16, 19, 21, 22, 178, 300
MODE_AUTO, MODE_STAB = 3, 0


class SimVehicle:
    def __init__(self, url="udpin:127.0.0.1:14650", rate=20.0, wind_uv=(0.0, 0.0), power_fn=None, battery_wh=222.0,
                 wp_spd=10.0, vz_up=2.5, vz_dn=1.5, sysid=1, va_limit=None, wind_report=None):
        """va_limit: 낼 수 있는 최대 대기속도 [m/s] – 넘는 명령도 수락하지만 느리게 난다 (SITL 에서 본 ArduCopter 의 동작).
        wind_report: WIND 로 내보낼 값 (u, v). 주면 실제 바람과 다른 '틀린 추정'을 흉내 낸다."""
        from pymavlink import mavutil
        self.m = mavutil.mavlink_connection(url, source_system=sysid, dialect="ardupilotmega")
        self.mv = mavutil.mavlink
        self.rate, self.wind_uv, self.power_fn, self.battery_wh = float(rate), wind_uv, power_fn, float(battery_wh)
        self.vz_up, self.vz_dn = vz_up, vz_dn
        self.va_limit, self.wind_report = va_limit, wind_report
        self.speed = float(wp_spd)                 # 지금 쓰는 대지속도 [m/s] (WP_SPD → 미션 항목·명령이 덮어씀)
        self.mission, self._want = [], None
        self.auto = self.armed = self.done = False
        self.seq = 0
        self.lat = self.lon = self.alt = 0.0       # alt: 홈 기준 상대 고도 [m]
        self.vx = self.vy = self.vz = 0.0          # 북·동·위 [m/s]
        self.energy = 0.0
        self.t_sim = 0.0
        self.speed_log = []                        # (t_sim, v, 출처)
        self._hold = 0.0
        self._run, self._th = False, None

    # ------------------------------------------------------------ 운용자 동작 (시험에서 호출)
    def start_auto(self):
        """조종사가 시동 후 AUTO 로 바꾼 것에 해당."""
        if len(self.mission) < 3:
            raise RuntimeError("미션이 없습니다")
        h = self.mission[0]
        self.lat, self.lon, self.alt = h["lat"], h["lon"], 0.0
        self.seq, self.armed, self.auto = 1, True, True

    def start(self):
        self._run = True
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        return self

    def stop(self):
        self._run = False
        if self._th is not None:
            self._th.join(timeout=2.0)
        try:
            self.m.close()
        except Exception:  # noqa
            pass

    @property
    def soc(self):
        return max(0.0, 100.0 - self.energy / (self.battery_wh * 3600.0) * 100.0)

    # ------------------------------------------------------------ 운동
    def _advance(self, dt):
        if not self.auto or self.done:
            self.vx = self.vy = self.vz = 0.0
            return
        while dt > 1e-9 and self.seq < len(self.mission):
            it = self.mission[self.seq]
            c = it["cmd"]
            if c == CMD_SPEED:
                if it["p"][1] > 0:
                    self.speed = float(it["p"][1]); self.speed_log.append((self.t_sim, self.speed, "mission"))
                self.seq += 1
                continue
            if c == CMD_LOITER:
                if self._hold <= 0:
                    self._hold = float(it["p"][0])
                use = min(dt, self._hold)
                self._hold -= use; dt -= use
                self.vx = self.vy = self.vz = 0.0
                self._burn(use)
                if self._hold <= 1e-9:
                    self._hold = 0.0; self.seq += 1
                continue
            tgt_alt = 0.0 if c == CMD_LAND else float(it["alt"])
            if c in (CMD_TAKEOFF, CMD_LAND):
                tlat, tlon = self.lat, self.lon
            else:
                tlat, tlon = it["lat"], it["lon"]
            mlon = MLAT * math.cos(math.radians(self.lat))
            dn, de, dz = (tlat - self.lat) * MLAT, (tlon - self.lon) * mlon, tgt_alt - self.alt
            dh = math.hypot(dn, de)
            vz_lim = self.vz_up if dz > 0 else self.vz_dn
            spd = self.speed
            if self.va_limit is not None and dh > 1e-6:          # 대기속도 한계에서 낼 수 있는 대지속도
                un, ue = dn / dh, de / dh
                w_al = self.wind_uv[1] * un + self.wind_uv[0] * ue
                w_cr = -self.wind_uv[1] * ue + self.wind_uv[0] * un
                spd = min(spd, max(0.5, w_al + math.sqrt(max(self.va_limit ** 2 - w_cr ** 2, 0.0))))
            t_h = dh / max(spd, 0.1)
            t_v = abs(dz) / vz_lim
            T = max(t_h, t_v)                       # 수직 한계가 걸리면 그만큼 천천히 간다
            if T < 1e-6:
                self.seq += 1
                continue
            use = min(dt, T)
            f = use / T
            self.vx, self.vy, self.vz = dn / T, de / T, dz / T
            self.lat += dn * f / MLAT; self.lon += de * f / mlon; self.alt += dz * f
            self._burn(use)
            dt -= use
            if use >= T - 1e-9:
                self.seq += 1
                if c == CMD_LAND:
                    self.done, self.auto, self.armed = True, False, False
                    self.vx = self.vy = self.vz = 0.0
                    return

    def _burn(self, dt):
        P = 500.0 if self.power_fn is None else float(self.power_fn(self.vy - self.wind_uv[0], self.vx - self.wind_uv[1], self.vz))
        self.energy += P * dt
        self.t_sim += dt

    # ------------------------------------------------------------ 통신
    def _loop(self):
        mv, m = self.mv, self.m
        last, t_tx = time.time(), 0.0
        while self._run:
            now = time.time()
            self._advance((now - last) * self.rate)
            last = now
            if now - t_tx > 0.05:
                t_tx = now
                try:
                    m.mav.heartbeat_send(mv.MAV_TYPE_QUADROTOR, mv.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                         (mv.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0) | mv.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                                         MODE_AUTO if self.auto else MODE_STAB, mv.MAV_STATE_ACTIVE)
                    m.mav.global_position_int_send(int(self.t_sim * 1000) % 2 ** 32, int(round(self.lat * 1e7)), int(round(self.lon * 1e7)),
                                                   0, int(round(self.alt * 1000)), int(round(self.vx * 100)), int(round(self.vy * 100)),
                                                   int(round(-self.vz * 100)), 0)
                    wr = self.wind_report if self.wind_report is not None else self.wind_uv
                    d = math.degrees(math.atan2(-wr[0], -wr[1])) % 360.0
                    m.mav.wind_send(d, math.hypot(*wr), 0.0)
                    m.mav.battery_status_send(0, mv.MAV_BATTERY_FUNCTION_ALL, mv.MAV_BATTERY_TYPE_LIPO, 250, [22200] + [65535] * 9,
                                              1500, int(self.energy / 79.92), -1, int(round(self.soc)))
                    m.mav.mission_current_send(self.seq)
                except Exception:  # noqa – 상대 주소를 아직 모름
                    pass
            for _ in range(20):
                msg = m.recv_match(blocking=False)
                if msg is None:
                    break
                t = msg.get_type()
                if t == "COMMAND_LONG":
                    ok = False
                    if msg.command == CMD_SPEED and msg.param2 > 0 and msg.param1 in (0, 1):
                        self.speed = float(msg.param2); self.speed_log.append((self.t_sim, self.speed, "command")); ok = True
                    elif msg.command == CMD_START and len(self.mission) >= 3:
                        self.start_auto(); ok = True
                    m.mav.command_ack_send(msg.command, mv.MAV_RESULT_ACCEPTED if ok else mv.MAV_RESULT_DENIED)
                elif t == "MISSION_COUNT":
                    self._want, self.mission = int(msg.count), []
                    m.mav.mission_request_int_send(msg.get_srcSystem(), msg.get_srcComponent(), 0, mv.MAV_MISSION_TYPE_MISSION)
                elif t == "MISSION_ITEM_INT" and self._want is not None:
                    self.mission.append(dict(seq=msg.seq, frame=msg.frame, cmd=msg.command, p=[msg.param1, msg.param2, msg.param3, msg.param4],
                                             lat=msg.x / 1e7, lon=msg.y / 1e7, alt=msg.z))
                    if len(self.mission) < self._want:
                        m.mav.mission_request_int_send(msg.get_srcSystem(), msg.get_srcComponent(), len(self.mission),
                                                       mv.MAV_MISSION_TYPE_MISSION)
                    else:
                        self._want = None
                        m.mav.mission_ack_send(msg.get_srcSystem(), msg.get_srcComponent(), mv.MAV_MISSION_ACCEPTED,
                                               mv.MAV_MISSION_TYPE_MISSION)
                elif t == "MISSION_REQUEST_LIST":
                    m.mav.mission_count_send(msg.get_srcSystem(), msg.get_srcComponent(), len(self.mission), mv.MAV_MISSION_TYPE_MISSION)
            time.sleep(0.004)
