"""MAVLink 연결 – 속도 명령 송신, 기체 추정 바람 수신, 미션 올리기 (ArduPilot ArduCopter 기준).

쓰는 메시지 (mavlink/mavlink common.xml · ardupilotmega.xml)
  COMMAND_LONG  MAV_CMD_DO_CHANGE_SPEED (178)   p1 = 1 (대지속도), p2 = 속도 [m/s], p3 = -1 (스로틀 유지)
  COMMAND_ACK                                   명령 수락 여부 (result 0 = ACCEPTED)
  WIND (ardupilotmega 168)                      direction = 바람이 불어오는 방향 [deg], speed [m/s]
                                                ArduCopter 는 EK3_DRAG_BCOEF_X/Y 를 설정해야 바람을 추정한다 (기본 0)
  GLOBAL_POSITION_INT · BATTERY_STATUS          위치, 배터리 잔량
  MISSION_COUNT · MISSION_REQUEST(_INT) · MISSION_ITEM_INT · MISSION_ACK   미션 올리기

검증 범위: 같은 컴퓨터 안의 UDP 루프백에서 모의 기체(FakeVehicle)와 주고받는 것까지 확인했다.
ArduPilot SITL 이나 실기체에서는 시험하지 않았다 (docs/deployment.md '검증 범위').

pymavlink 는 선택 의존성이다: pip install "drone-path-opt[mavlink]"
"""
from __future__ import annotations

import math
import threading
import time

MAV_CMD_DO_CHANGE_SPEED = 178
SPEED_TYPE_GROUND = 1


def _mavutil():
    try:
        from pymavlink import mavutil
    except ImportError as ex:  # pragma: no cover
        raise RuntimeError('pymavlink 가 필요합니다: pip install "drone-path-opt[mavlink]"') from ex
    return mavutil


def wind_to_uv(direction_deg, speed):
    """WIND 메시지 (불어오는 방향, 풍속) → (u 동쪽, v 북쪽) [m/s]."""
    th = math.radians(direction_deg)
    return (-speed * math.sin(th), -speed * math.cos(th))


def uv_to_wind(u, v):
    """(u, v) → (불어오는 방향 [deg], 풍속 [m/s])."""
    return (math.degrees(math.atan2(-u, -v)) % 360.0, math.hypot(u, v))


class MavBridge:
    """지상국·탑재 컴퓨터 쪽 연결. url 예: 'udpin:0.0.0.0:14550', 'udpout:127.0.0.1:14551', 'tcp:127.0.0.1:5760'."""

    def __init__(self, url, source_system=255, timeout=5.0):
        mavutil = _mavutil()
        self.url, self.timeout = url, float(timeout)
        self.m = mavutil.mavlink_connection(url, source_system=source_system, dialect="ardupilotmega")
        self.mavlink = mavutil.mavlink
        self.lock = threading.Lock()
        self.connected = False
        self.sent, self.acked = 0, 0
        self.last_error = None

    def connect(self):
        """기체의 HEARTBEAT 를 기다린다. udpout 은 먼저 말을 걸어야 하므로 우리 HEARTBEAT 를 보낸다."""
        t_end = time.time() + self.timeout
        while time.time() < t_end:
            self.m.mav.heartbeat_send(self.mavlink.MAV_TYPE_GCS, self.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            hb = self.m.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if hb is not None and hb.get_srcSystem() != self.m.source_system:
                self.m.target_system, self.m.target_component = hb.get_srcSystem(), hb.get_srcComponent()
                self.connected = True
                return True
        self.last_error = "HEARTBEAT 없음"
        return False

    def send_change_speed(self, v, wait_ack=True):
        """대지속도 명령. 반환: 수락되면 True."""
        with self.lock:
            self.m.mav.command_long_send(self.m.target_system, self.m.target_component, MAV_CMD_DO_CHANGE_SPEED, 0,
                                         SPEED_TYPE_GROUND, float(v), -1, 0, 0, 0, 0)
            self.sent += 1
            if not wait_ack:
                return True
            t_end = time.time() + self.timeout
            while time.time() < t_end:
                a = self.m.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.5)
                if a is not None and a.command == MAV_CMD_DO_CHANGE_SPEED:
                    ok = a.result == self.mavlink.MAV_RESULT_ACCEPTED
                    self.acked += int(ok)
                    if not ok:
                        self.last_error = f"DO_CHANGE_SPEED 거부 (result {a.result})"
                    return ok
            self.last_error = "COMMAND_ACK 없음"
            return False

    def read_wind(self, timeout=None):
        """기체가 추정한 바람 (u, v) [m/s]. 없으면 None."""
        with self.lock:
            w = self.m.recv_match(type="WIND", blocking=True, timeout=self.timeout if timeout is None else timeout)
        return None if w is None else wind_to_uv(w.direction, w.speed)

    def read_state(self, timeout=None):
        """위치와 배터리. 반환: dict(lat, lon, alt_rel, vx, vy, soc) – 받지 못한 값은 빠진다."""
        out, t_end = {}, time.time() + (self.timeout if timeout is None else timeout)
        with self.lock:
            while time.time() < t_end and not ("lat" in out and "soc" in out):
                msg = self.m.recv_match(type=["GLOBAL_POSITION_INT", "BATTERY_STATUS"], blocking=True, timeout=0.3)
                if msg is None:
                    continue
                if msg.get_type() == "GLOBAL_POSITION_INT":
                    out.update(lat=msg.lat / 1e7, lon=msg.lon / 1e7, alt_rel=msg.relative_alt / 1000.0,
                               vx=msg.vx / 100.0, vy=msg.vy / 100.0)
                else:
                    out["soc"] = float(msg.battery_remaining)
        return out

    def upload_mission(self, items):
        """미션 올리기. items: ardupilot.leg_items / remaining_mission 의 항목 목록. 반환: 수락되면 True."""
        mv = self.mavlink
        with self.lock:
            self.m.mav.mission_count_send(self.m.target_system, self.m.target_component, len(items), mv.MAV_MISSION_TYPE_MISSION)
            t_end = time.time() + self.timeout + 0.5 * len(items)
            while time.time() < t_end:
                msg = self.m.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"], blocking=True, timeout=0.5)
                if msg is None:
                    continue
                if msg.get_type() == "MISSION_ACK":
                    ok = msg.type == mv.MAV_MISSION_ACCEPTED
                    if not ok:
                        self.last_error = f"미션 거부 (type {msg.type})"
                    return ok
                it = items[msg.seq]
                p = it["p"]
                self.m.mav.mission_item_int_send(
                    self.m.target_system, self.m.target_component, msg.seq, it["frame"], it["cmd"],
                    1 if msg.seq == 0 else 0, 1, float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                    int(round(it["lat"] * 1e7)), int(round(it["lon"] * 1e7)), float(it["alt"]), mv.MAV_MISSION_TYPE_MISSION)
            self.last_error = "미션 올리기 시간 초과"
            return False

    def status(self):
        return dict(url=self.url, connected=self.connected, sent=self.sent, acked=self.acked, error=self.last_error)

    def close(self):
        try:
            self.m.close()
        except Exception:  # noqa
            pass


class FakeVehicle:
    """모의 기체 (시험·시연용): HEARTBEAT·WIND·위치·배터리를 내보내고, 속도 명령과 미션 올리기에 응답한다.

    실제 비행 제어는 없다. 프로토콜 왕복을 같은 컴퓨터에서 확인하는 용도다."""

    def __init__(self, url="udpin:127.0.0.1:14601", wind_uv=(3.0, -4.0), sysid=1):
        mavutil = _mavutil()
        self.m = mavutil.mavlink_connection(url, source_system=sysid, dialect="ardupilotmega")
        self.mavlink = mavutil.mavlink
        self.wind_uv = wind_uv
        self.speeds = []            # 받은 대지속도 명령
        self.mission = []           # 받은 미션 항목 (seq, frame, command, params, lat, lon, alt)
        self.reject_speed = False
        self._want = None
        self._run = False
        self._th = None

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

    def _loop(self):
        mv, m = self.mavlink, self.m
        t_hb = 0.0
        while self._run:
            now = time.time()
            if now - t_hb > 0.2:
                t_hb = now
                try:
                    m.mav.heartbeat_send(mv.MAV_TYPE_QUADROTOR, mv.MAV_AUTOPILOT_ARDUPILOTMEGA, 0, 0, mv.MAV_STATE_ACTIVE)
                    d, s = uv_to_wind(*self.wind_uv)
                    m.mav.wind_send(d, s, 0.0)
                    m.mav.global_position_int_send(int(now * 1000) % 2 ** 32, int(37.4 * 1e7), int(127.1 * 1e7), 150000, 100000,
                                                   500, -300, 0, 9000)
                    m.mav.battery_status_send(0, mv.MAV_BATTERY_FUNCTION_ALL, mv.MAV_BATTERY_TYPE_LIPO, 250, [22200] + [65535] * 9,
                                              1500, 1000, -1, 87)
                except Exception:  # noqa – 아직 상대 주소를 모름 (udpin 은 첫 수신 뒤에 보낼 수 있다)
                    pass
            msg = m.recv_match(blocking=True, timeout=0.05)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "COMMAND_LONG" and msg.command == MAV_CMD_DO_CHANGE_SPEED:
                ok = not self.reject_speed and msg.param1 == SPEED_TYPE_GROUND and 0 < msg.param2 <= 30
                if ok:
                    self.speeds.append(float(msg.param2))
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
