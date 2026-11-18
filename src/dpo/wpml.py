"""DJI 경로 파일(WPML, .kmz) 내보내기.

계획 결과(ArduPilot 미션 항목과 같은 자료)를 DJI Pilot 2 · FlightHub 2 · MSDK V5 가 읽는 경로 파일로 바꾼다.

규격의 출처 (이 모듈은 여기서 확인한 요소와 값만 쓴다):
  DJI Cloud API 문서 > DJI WPML (overview, template.kml, waylines.wpml, common element).
  공개 저장소 dji-sdk/Cloud-API-Doc 의 2024-11-07 판을 읽었다.

  kmz (ZIP)
   └ wpmz/
      ├ template.kml    편집용 (DJI Pilot 2 · FlightHub 2 가 읽는다)
      └ waylines.wpml   실행용 (MSDK V5 가 읽는다)

정한 것
  - 고도는 '이륙점 기준 상대 고도'(relativeToStartPoint)로 쓴다. 지도의 고도는 해발(지오이드 기준)이고 WPML 의 절대 고도는
    타원체고라서, 절대 고도로 쓰면 지오이드고(한국 약 25–30 m)만큼 틀릴 수 있기 때문이다.
  - wpml:waypointSpeed 는 '그 경로점에서 다음 경로점까지의 속도'다 (waylines.wpml 문서). 계획의 구간 대지속도를 그대로 넣는다.
    기종의 최대 속도를 넘으면 깎고 보고한다.
  - 호버 대기는 그 경로점의 hover 동작(hoverTime)으로 넣는다. 그 경로점은 '정지' 방식으로 지난다.
  - 꺾이는 경로점은 멈추지 않고 미리 도는 방식으로 지난다 (계획기는 정지를 가정하지 않는다).
      M30·M3E·M3D 계열: toPointAndPassWithContinuityCurvature + useStraightLine 1  (Pilot 2 의 '미리 돌고 통과')
      M300·M350 RTK   : coordinateTurn  (useStraightLine 은 이 기종의 지원 목록에 없다)
    첫 경로점과 마지막 경로점은 정지 방식이다.

확인하지 못한 것 – DJI 기체나 DJI 소프트웨어에서 이 파일을 열어 보지 못했다
  - template.kml 의 wpml:ellipsoidHeight: 타원체고를 계산할 지오이드 자료가 없어 상대 고도와 같은 값을 넣는다.
    Pilot 2 에서 불러온 뒤 경로점 고도를 반드시 눈으로 확인해야 한다.
  - 기종별 최대 경로 속도 (기본 15 m/s 로 깎는다), 경로점 사이 최소 거리, 경로점 개수 한계.
  - 문서의 이름공간 판(1.0.2)과 최신 Pilot 2 가 쓰는 판의 차이.
"""
from __future__ import annotations

import io
import math
import time
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

KML_NS = "http://www.opengis.net/kml/2.2"
WPML_NS = "http://www.dji.com/wpmz/1.0.2"
MLAT = 111320.0

# 기종: (droneEnumValue, droneSubEnumValue, 기본 payloadEnumValue, 이름, useStraightLine 지원)  – common element 문서의 표
MODELS = {
    "m350": (89, None, 42, "Matrice 350 RTK", False),
    "m300": (60, None, 42, "Matrice 300 RTK", False),
    "m30": (67, 0, 52, "Matrice 30", True),
    "m30t": (67, 1, 53, "Matrice 30T", True),
    "m3e": (77, 0, 66, "Mavic 3E", True),
    "m3t": (77, 1, 67, "Mavic 3T", True),
    "m3m": (77, 2, 68, "Mavic 3M", True),
    "m3d": (91, 0, 80, "Matrice 3D", True),
    "m3td": (91, 1, 81, "Matrice 3TD", True),
}
FINISH = ("goHome", "noAction", "autoLand", "gotoFirstWaypoint")
RC_LOST = ("goContinue", "goBack", "landing", "hover")
TURN_STOP = "toPointAndStopWithDiscontinuityCurvature"
TURN_PASS = "toPointAndPassWithContinuityCurvature"
TURN_COORD = "coordinateTurn"
CMD_WP, CMD_LOITER, CMD_LAND, CMD_TAKEOFF, CMD_DELAY, CMD_SPEED = 16, 19, 21, 22, 93, 178


def _dist(a, b):
    """두 경로점 사이의 수평 거리와 3차원 거리 [m]."""
    mlon = MLAT * math.cos(math.radians(0.5 * (a["lat"] + b["lat"])))
    h = math.hypot((b["lat"] - a["lat"]) * MLAT, (b["lon"] - a["lon"]) * mlon)
    return h, math.hypot(h, b["height"] - a["height"])


def waypoints_from_items(items, v_default=10.0):
    """ArduPilot 미션 항목(dpo.ardupilot.leg_items 의 items) → 경로점 목록.

    반환: (waypoints, info). waypoints: [dict(lat, lon, height(이륙점 기준 [m]), speed(다음 경로점까지 [m/s]), hover [s])]
    info: dict(home=(lat, lon), ground_delay [s])"""
    wps, v, delay, home = [], None, 0.0, None
    for it in items:
        c = it["cmd"]
        if it["seq"] == 0:
            home = (it["lat"], it["lon"])
        elif c == CMD_DELAY:
            delay += float(it["p"][0])
        elif c == CMD_TAKEOFF:
            wps.append(dict(lat=it["lat"], lon=it["lon"], height=float(it["alt"]), speed=None, hover=0.0))
        elif c == CMD_SPEED:
            v = float(it["p"][1])
        elif c == CMD_WP:
            if not wps:
                raise ValueError("이륙 항목이 없는 미션은 WPML 로 바꿀 수 없습니다 (비행 중 남은 구간 미션)")
            if wps[-1]["speed"] is None:
                wps[-1]["speed"] = v
            wps.append(dict(lat=it["lat"], lon=it["lon"], height=float(it["alt"]), speed=None, hover=0.0))
        elif c == CMD_LOITER:
            if not wps:
                raise ValueError("이륙 항목이 없는 미션은 WPML 로 바꿀 수 없습니다")
            last = wps[-1]
            same = abs(last["lat"] - it["lat"]) < 1e-7 and abs(last["lon"] - it["lon"]) < 1e-7 and abs(last["height"] - it["alt"]) < 0.05
            if not same:                                  # 대기 지점이 직전 경로점과 다르면 경로점을 하나 넣는다
                if last["speed"] is None:
                    last["speed"] = v
                wps.append(dict(lat=it["lat"], lon=it["lon"], height=float(it["alt"]), speed=None, hover=0.0))
            wps[-1]["hover"] += float(it["p"][0])
    if len(wps) < 2:
        raise ValueError("경로점이 2개 미만입니다")
    last_v = v_default
    for w in wps:                                         # 속도가 없는 구간(순수 수직 이동, 마지막 점)은 앞 구간의 값을 쓴다
        if w["speed"] is None:
            w["speed"] = last_v
        last_v = w["speed"]
    return wps, dict(home=home, ground_delay=delay)


def _el(tag, text=None, children=(), ind=0):
    """들여쓴 XML 조각 (값은 이스케이프한다)."""
    pad = "  " * ind
    if text is not None:
        return f"{pad}<{tag}>{escape(str(text))}</{tag}>\n"
    return f"{pad}<{tag}>\n" + "".join(children) + f"{pad}</{tag}>\n"


def _num(v, n=2):
    s = f"{float(v):.{n}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def build(waypoints, model="m30", finish="autoLand", rc_lost="goBack", v_max=15.0, turn="pass", damping_max=5.0,
          rth_height=None, takeoff_security=None, dock=False, payload_enum=None, payload_position=0, author="drone-path-opt",
          created_ms=None):
    """경로점 목록 → dict(template, waylines, kmz, waypoints, summary).

    finish   : 경로를 마친 뒤의 동작 (goHome | noAction | autoLand | gotoFirstWaypoint)
    rc_lost  : 조종기 신호가 끊겼을 때 (goContinue = 경로 계속 | goBack | landing | hover)
    v_max    : 기종의 최대 경로 속도 [m/s]. 넘는 구간 속도는 이 값으로 깎는다.
    turn     : 'pass' = 미리 돌고 통과 (기본) | 'stop' = 모든 경로점에서 정지
    rth_height : 복귀 고도 (이륙점 기준 [m]). None 이면 경로의 최고 높이 + 20 m.
    dock     : 도크 운용이면 True (안전 이륙 고도의 하한이 8 m)"""
    if model not in MODELS:
        raise ValueError(f"model 은 {', '.join(MODELS)} 중 하나")
    if finish not in FINISH:
        raise ValueError(f"finish 는 {', '.join(FINISH)} 중 하나")
    if rc_lost not in RC_LOST:
        raise ValueError(f"rc_lost 는 {', '.join(RC_LOST)} 중 하나")
    if turn not in ("pass", "stop"):
        raise ValueError("turn 은 pass 또는 stop")
    if not (0 < v_max <= 30):
        raise ValueError("v_max 는 0 보다 크고 30 이하")
    d_enum, d_sub, p_enum, label, straight_ok = MODELS[model]
    p_enum = p_enum if payload_enum is None else int(payload_enum)
    wps = [dict(w) for w in waypoints]
    n = len(wps)
    if n < 2:
        raise ValueError("경로점이 2개 미만입니다")
    if n > 65535:
        raise ValueError("경로점이 너무 많습니다")
    warn, clamped = [], 0
    for q, w in enumerate(wps):
        if not (-90 <= w["lat"] <= 90 and -180 <= w["lon"] <= 180):
            raise ValueError(f"경로점 {q} 의 좌표가 범위 밖입니다")
        w["speed_plan"] = float(w["speed"])
        if w["speed"] > v_max + 1e-9:
            w["speed"], clamped = float(v_max), clamped + (1 if q < n - 1 else 0)
        if w["speed"] <= 0:
            raise ValueError(f"경로점 {q} 의 속도가 0 이하입니다")
        if w["hover"] < 0:
            raise ValueError(f"경로점 {q} 의 대기 시간이 음수입니다")
    seg = [_dist(wps[q], wps[q + 1]) for q in range(n - 1)]
    for q, (h, d3) in enumerate(seg):
        if h < 1.0:
            warn.append(f"경로점 {q}→{q + 1}: 수평 거리가 {h:.1f} m 입니다 (수직 이동). 기체가 받아들이는지 확인하세요.")
    # 꺾임 방식
    pass_mode = TURN_PASS if straight_ok else TURN_COORD
    for q, w in enumerate(wps):
        vertical = (q > 0 and seg[q - 1][0] < 1.0) or (q < n - 1 and seg[q][0] < 1.0)
        if turn == "stop" or q in (0, n - 1) or w["hover"] > 0 or vertical:
            w["turn"], w["damping"] = TURN_STOP, 0.0
        else:
            # 구간 길이는 양 끝 경로점의 선회 거리 합보다 커야 한다 → 이웃 구간의 0.4 배 이하
            w["turn"], w["damping"] = pass_mode, max(0.2, min(damping_max, 0.4 * min(seg[q - 1][1], seg[q][1])))
    if clamped:
        warn.append(f"{clamped}개 구간의 속도를 {v_max:g} m/s 로 깎았습니다. 그 구간은 계획보다 느리게 날아 SOC 예상이 달라집니다.")
    h_max = max(w["height"] for w in wps)
    h0 = wps[0]["height"]
    lo = 8.0 if dock else 1.2
    sec = float(takeoff_security) if takeoff_security is not None else max(lo, min(h0, 20.0))
    if not (lo <= sec <= 1500):
        raise ValueError(f"안전 이륙 고도는 {lo:g}–1500 m")
    rth = float(rth_height) if rth_height is not None else h_max + 20.0
    if rth < h_max:
        warn.append(f"복귀 고도({rth:.0f} m)가 경로의 최고 높이({h_max:.0f} m)보다 낮습니다.")
    v_auto = wps[0]["speed"]
    v_trans = min(v_auto, 15.0)                           # globalTransitionalSpeed 의 범위는 [0, 15]
    now = int(time.time() * 1000) if created_ms is None else int(created_ms)

    def mission(ind, execute):
        ch = [_el("wpml:flyToWaylineMode", "safely", ind=ind + 1), _el("wpml:finishAction", finish, ind=ind + 1),
              _el("wpml:exitOnRCLost", "goContinue" if rc_lost == "goContinue" else "executeLostAction", ind=ind + 1)]
        if rc_lost != "goContinue":
            ch.append(_el("wpml:executeRCLostAction", rc_lost, ind=ind + 1))
        ch += [_el("wpml:takeOffSecurityHeight", _num(sec), ind=ind + 1), _el("wpml:globalTransitionalSpeed", _num(v_trans), ind=ind + 1)]
        if execute:
            ch.append(_el("wpml:globalRTHHeight", _num(rth), ind=ind + 1))
        di = [_el("wpml:droneEnumValue", d_enum, ind=ind + 2)] + ([_el("wpml:droneSubEnumValue", d_sub, ind=ind + 2)] if d_sub is not None else [])
        ch.append(_el("wpml:droneInfo", children=di, ind=ind + 1))
        ch.append(_el("wpml:payloadInfo", children=[_el("wpml:payloadEnumValue", p_enum, ind=ind + 2),
                                                    _el("wpml:payloadPositionIndex", payload_position, ind=ind + 2)], ind=ind + 1))
        return _el("wpml:missionConfig", children=ch, ind=ind)

    def heading(tag, ind):
        return _el(tag, children=[_el("wpml:waypointHeadingMode", "followWayline", ind=ind + 1),
                                  _el("wpml:waypointHeadingPathMode", "followBadArc", ind=ind + 1)], ind=ind)

    def turn_param(w, ind):
        return _el("wpml:waypointTurnParam", children=[_el("wpml:waypointTurnMode", w["turn"], ind=ind + 1),
                                                       _el("wpml:waypointTurnDampingDist", _num(w["damping"]), ind=ind + 1)], ind=ind)

    gid = [0]

    def hover_group(q, w, ind):
        g = gid[0]
        gid[0] += 1
        act = _el("wpml:action", children=[
            _el("wpml:actionId", 0, ind=ind + 2), _el("wpml:actionActuatorFunc", "hover", ind=ind + 2),
            _el("wpml:actionActuatorFuncParam", children=[_el("wpml:hoverTime", _num(w["hover"], 1), ind=ind + 3)], ind=ind + 2)], ind=ind + 1)
        return _el("wpml:actionGroup", children=[
            _el("wpml:actionGroupId", g, ind=ind + 1), _el("wpml:actionGroupStartIndex", q, ind=ind + 1),
            _el("wpml:actionGroupEndIndex", q, ind=ind + 1), _el("wpml:actionGroupMode", "sequence", ind=ind + 1),
            _el("wpml:actionTrigger", children=[_el("wpml:actionTriggerType", "reachPoint", ind=ind + 2)], ind=ind + 1), act], ind=ind)

    def point(w, ind):
        return _el("Point", children=[_el("coordinates", f"{w['lon']:.8f},{w['lat']:.8f}", ind=ind + 1)], ind=ind)

    # ---- template.kml
    gid[0] = 0
    pm = []
    for q, w in enumerate(wps):
        ch = [point(w, 4), _el("wpml:index", q, ind=4), _el("wpml:ellipsoidHeight", _num(w["height"], 1), ind=4),
              _el("wpml:height", _num(w["height"], 1), ind=4), _el("wpml:useGlobalHeight", 0, ind=4), _el("wpml:useGlobalSpeed", 0, ind=4),
              _el("wpml:waypointSpeed", _num(w["speed"], 1), ind=4), _el("wpml:useGlobalHeadingParam", 1, ind=4),
              _el("wpml:useGlobalTurnParam", 0, ind=4), turn_param(w, 4)]
        if w["turn"] == TURN_PASS:
            ch.append(_el("wpml:useStraightLine", 1, ind=4))
        if w["hover"] > 0:
            ch.append(hover_group(q, w, 4))
        pm.append(_el("Placemark", children=ch, ind=3))
    folder = [_el("wpml:templateType", "waypoint", ind=3), _el("wpml:templateId", 0, ind=3),
              _el("wpml:waylineCoordinateSysParam", children=[
                  _el("wpml:coordinateMode", "WGS84", ind=4), _el("wpml:heightMode", "relativeToStartPoint", ind=4),
                  _el("wpml:positioningType", "GPS", ind=4), _el("wpml:globalShootHeight", _num(h0, 1), ind=4),
                  _el("wpml:surfaceFollowModeEnable", 0, ind=4)], ind=3),
              _el("wpml:autoFlightSpeed", _num(v_auto, 1), ind=3), _el("wpml:globalHeight", _num(h0, 1), ind=3),
              _el("wpml:gimbalPitchMode", "manual", ind=3), heading("wpml:globalWaypointHeadingParam", 3),
              _el("wpml:globalWaypointTurnMode", TURN_STOP if turn == "stop" else pass_mode, ind=3)]
    if turn != "stop" and straight_ok:
        folder.append(_el("wpml:globalUseStraightLine", 1, ind=3))
    head = f'<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="{KML_NS}" xmlns:wpml="{WPML_NS}">\n'
    template = head + _el("Document", children=[
        _el("wpml:author", author, ind=2), _el("wpml:createTime", now, ind=2), _el("wpml:updateTime", now, ind=2),
        mission(2, False), _el("Folder", children=folder + pm, ind=2)], ind=1) + "</kml>\n"

    # ---- waylines.wpml
    gid[0] = 0
    pm = []
    for q, w in enumerate(wps):
        ch = [point(w, 4), _el("wpml:index", q, ind=4), _el("wpml:executeHeight", _num(w["height"], 1), ind=4),
              _el("wpml:waypointSpeed", _num(w["speed"], 1), ind=4), heading("wpml:waypointHeadingParam", 4), turn_param(w, 4)]
        if w["turn"] == TURN_PASS:
            ch.append(_el("wpml:useStraightLine", 1, ind=4))
        if w["hover"] > 0:
            ch.append(hover_group(q, w, 4))
        pm.append(_el("Placemark", children=ch, ind=3))
    folder = [_el("wpml:templateId", 0, ind=3), _el("wpml:executeHeightMode", "relativeToStartPoint", ind=3),
              _el("wpml:waylineId", 0, ind=3), _el("wpml:autoFlightSpeed", _num(v_auto, 1), ind=3)]
    waylines = head + _el("Document", children=[mission(2, True), _el("Folder", children=folder + pm, ind=2)], ind=1) + "</kml>\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in (("wpmz/template.kml", template), ("wpmz/waylines.wpml", waylines)):
            zi = zipfile.ZipInfo(name, date_time=time.gmtime(now / 1000)[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, text.encode("utf-8"))
    dist = sum(d3 for _, d3 in seg)
    summary = dict(model=model, model_label=label, drone_enum=d_enum, drone_sub_enum=d_sub, payload_enum=p_enum,
                   n_waypoints=n, n_hover=sum(1 for w in wps if w["hover"] > 0), hover_s=float(sum(w["hover"] for w in wps)),
                   n_pass=sum(1 for w in wps if w["turn"] != TURN_STOP), turn_mode=TURN_STOP if turn == "stop" else pass_mode,
                   height_mode="relativeToStartPoint", height_min=float(min(w["height"] for w in wps)), height_max=float(h_max),
                   speed_min=float(min(w["speed"] for w in wps[:-1])), speed_max=float(max(w["speed"] for w in wps[:-1])),
                   v_max=float(v_max), n_clamped=clamped, distance_m=float(dist), takeoff_security_height=sec, rth_height=rth,
                   finish=finish, rc_lost=rc_lost, warnings=warn)
    return dict(template=template, waylines=waylines, kmz=buf.getvalue(), waypoints=wps, summary=summary)


def from_items(items, **opt):
    """ArduPilot 미션 항목 → WPML. 이륙 전 지상 대기가 있으면 요약에 적는다 (WPML 에는 넣을 곳이 없다)."""
    wps, info = waypoints_from_items(items)
    out = build(wps, **opt)
    out["summary"]["home"] = dict(lat=info["home"][0], lon=info["home"][1]) if info["home"] else None
    out["summary"]["ground_delay_s"] = info["ground_delay"]
    if info["ground_delay"] > 0.5:
        out["summary"]["warnings"].append(f"이륙 전 지상 대기 {info['ground_delay']:.0f}초는 파일에 넣지 못했습니다. 그만큼 기다렸다가 시작하세요.")
    return out


# ------------------------------------------------------------------ 되읽기와 검사
def _t(el, tag, ns):
    e = el.find(tag, ns)
    return None if e is None or e.text is None else e.text.strip()


def read_kmz(data):
    """kmz(bytes) → dict(template=[경로점], waylines=[경로점], mission={...}). 내보낸 파일을 다시 읽어 확인하는 데 쓴다."""
    ns = dict(k=KML_NS, w=WPML_NS)
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        for key, name in (("template", "wpmz/template.kml"), ("waylines", "wpmz/waylines.wpml")):
            if name not in names:
                raise ValueError(f"{name} 이 없습니다")
            root = ET.fromstring(z.read(name))
            doc = root.find("k:Document", ns)
            folder = doc.find("k:Folder", ns)
            mc = doc.find("w:missionConfig", ns)
            pts = []
            for pm in folder.findall("k:Placemark", ns):
                lon, lat = (float(v) for v in pm.find("k:Point/k:coordinates", ns).text.strip().split(","))
                h = _t(pm, "w:executeHeight", ns) if key == "waylines" else _t(pm, "w:height", ns)
                hv = pm.find("w:actionGroup/w:action/w:actionActuatorFuncParam/w:hoverTime", ns)
                pts.append(dict(index=int(_t(pm, "w:index", ns)), lat=lat, lon=lon, height=float(h), speed=float(_t(pm, "w:waypointSpeed", ns)),
                                hover=0.0 if hv is None else float(hv.text), turn=_t(pm, "w:waypointTurnParam/w:waypointTurnMode", ns),
                                damping=float(_t(pm, "w:waypointTurnParam/w:waypointTurnDampingDist", ns)),
                                straight=_t(pm, "w:useStraightLine", ns)))
            out[key] = pts
            out[key + "_mission"] = {c.tag.split("}")[1]: (c.text or "").strip() for c in mc if len(c) == 0}
            out[key + "_drone"] = (int(_t(mc, "w:droneInfo/w:droneEnumValue", ns)), _t(mc, "w:droneInfo/w:droneSubEnumValue", ns))
            out[key + "_height_mode"] = (_t(folder, "w:executeHeightMode", ns) if key == "waylines"
                                         else _t(folder, "w:waylineCoordinateSysParam/w:heightMode", ns))
    return out


def check(data, v_max=15.0):
    """내보낸 kmz 가 문서의 필수 요소와 값 범위를 지키는지 검사한다. 반환: 문제 목록 (비어 있으면 통과).
    DJI 소프트웨어의 검사를 대신하지는 못한다."""
    bad = []
    try:
        r = read_kmz(data)
    except Exception as e:  # noqa
        return [f"읽기 실패: {e}"]
    for key in ("template", "waylines"):
        pts, mc = r[key], r[key + "_mission"]
        if [p["index"] for p in pts] != list(range(len(pts))):
            bad.append(f"{key}: wpml:index 가 0부터 이어지지 않습니다")
        if len(pts) < 2:
            bad.append(f"{key}: 경로점이 2개 미만")
        for tag, allowed in (("flyToWaylineMode", ("safely", "pointToPoint")), ("finishAction", FINISH), ("exitOnRCLost", ("goContinue", "executeLostAction"))):
            if mc.get(tag) not in allowed:
                bad.append(f"{key}: wpml:{tag} = {mc.get(tag)!r}")
        if mc.get("exitOnRCLost") == "executeLostAction" and mc.get("executeRCLostAction") not in ("goBack", "landing", "hover"):
            bad.append(f"{key}: wpml:executeRCLostAction 이 필요합니다")
        try:
            if not (1.2 <= float(mc.get("takeOffSecurityHeight", "nan")) <= 1500):
                bad.append(f"{key}: wpml:takeOffSecurityHeight 범위 밖")
            if not (0 < float(mc.get("globalTransitionalSpeed", "nan")) <= 15):
                bad.append(f"{key}: wpml:globalTransitionalSpeed 범위 밖")
        except ValueError:
            bad.append(f"{key}: missionConfig 의 숫자를 읽을 수 없습니다")
        if key == "waylines" and "globalRTHHeight" not in mc:
            bad.append("waylines: wpml:globalRTHHeight 가 없습니다")
        if r[key + "_drone"][0] not in {m[0] for m in MODELS.values()}:
            bad.append(f"{key}: wpml:droneEnumValue = {r[key + '_drone'][0]}")
        if r[key + "_height_mode"] not in ("relativeToStartPoint", "WGS84", "EGM96", "aboveGroundLevel", "realTimeFollowSurface"):
            bad.append(f"{key}: 고도 방식 = {r[key + '_height_mode']!r}")
        for q, p in enumerate(pts):
            if not (-90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180):
                bad.append(f"{key}: 경로점 {q} 좌표 범위 밖")
            if not (0 < p["speed"] <= v_max + 1e-9):
                bad.append(f"{key}: 경로점 {q} 속도 {p['speed']} m/s")
            if p["turn"] not in (TURN_STOP, TURN_PASS, TURN_COORD, "toPointAndStopWithContinuityCurvature"):
                bad.append(f"{key}: 경로점 {q} 꺾임 방식 {p['turn']!r}")
            if p["turn"] in (TURN_PASS, TURN_COORD) and not p["damping"] > 0:
                bad.append(f"{key}: 경로점 {q} 의 선회 거리가 0 입니다")
            if p["turn"] == TURN_PASS and p["straight"] != "1":
                bad.append(f"{key}: 경로점 {q} 에 wpml:useStraightLine 이 필요합니다")
            if p["hover"] < 0:
                bad.append(f"{key}: 경로점 {q} 대기 시간이 음수")
        for q in range(len(pts) - 1):                     # 구간 길이 > 양 끝 선회 거리의 합
            d3 = _dist(pts[q], pts[q + 1])[1]
            if d3 <= pts[q]["damping"] + pts[q + 1]["damping"]:
                bad.append(f"{key}: 구간 {q}→{q + 1} 길이 {d3:.1f} m 가 선회 거리의 합보다 짧습니다")
    a, b = r["template"], r["waylines"]
    if len(a) != len(b) or any(abs(p["lat"] - q["lat"]) > 1e-9 or abs(p["lon"] - q["lon"]) > 1e-9 or abs(p["height"] - q["height"]) > 1e-9
                               or abs(p["speed"] - q["speed"]) > 1e-9 or abs(p["hover"] - q["hover"]) > 1e-9 for p, q in zip(a, b)):
        bad.append("template.kml 과 waylines.wpml 의 경로점이 다릅니다")
    return bad
