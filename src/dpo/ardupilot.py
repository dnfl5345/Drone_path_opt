"""계획 경로 → ArduPilot(ArduCopter) AUTO 미션 파일.

출력 형식: Mission Planner / QGroundControl 이 읽는 'QGC WPL 110' 텍스트 (.waypoints)
  <index> <current> <frame> <command> <p1> <p2> <p3> <p4> <lat> <lon> <alt> <autocontinue>

쓰는 명령 (MAVLink common.xml, ArduCopter mode_auto.cpp 에서 지원 확인)
  16  NAV_WAYPOINT     경로 꼭짓점
  19  NAV_LOITER_TIME  호버 대기 (p1 = 초)
  21  NAV_LAND         도착지 착륙
  22  NAV_TAKEOFF      이륙 (alt = 홈 기준 고도)
  93  NAV_DELAY        이륙 전 지상 대기 (p1 = 초)
  178 DO_CHANGE_SPEED  구간 대지속도 (p1 = 1 대지속도, p2 = m/s)

고도 기준: frame 3 (MAV_FRAME_GLOBAL_RELATIVE_ALT) – 홈(시동 위치) 지면 기준 상대 고도.
  계획기의 고도는 DSM 해발고도이므로 '노드 고도 − 출발지 지면 고도' 로 바꾼다.
  (절대 고도는 DSM·기압계·지오이드 차이로 수 m 어긋날 수 있어 쓰지 않는다)

한 구간 = 한 번의 이륙~착륙. 왕복은 가는 편·오는 편 두 파일로 나눈다(오는 편의 홈은 배송지).
"""
from __future__ import annotations

import math

import numpy as np

from .data_terrain import site_frame, xy2ll
from .energy import soc_drop
from .planner import simulate

CMD = dict(WAYPOINT=16, LOITER_TIME=19, LAND=21, TAKEOFF=22, DELAY=93, CHANGE_SPEED=178)
FRAME_ABS, FRAME_REL = 0, 3


def _node_xyz(site, p):
    return (p[2] + .5) * site.dx, (p[1] + .5) * site.dx, float(site.z[0] + p[0] * site.dz)


def _speed_groups(sim, lh, t0, dv=1.0, min_len=75.0):
    """한 직선 구간 안에서 최적 대지속도가 바뀌는 지점을 찾아 구간을 나눈다.
    반환: [(끝 지점의 구간 내 비율 f, 평균 대지속도, 소요시간, 누적 에너지)]"""
    n = len(sim["t"])
    d = np.full(n, lh / n)                       # 부분 구간의 수평 길이 (simulate 는 등분할)
    dt = np.diff(np.concatenate([[t0], sim["t"]]))
    groups, s = [], 0
    for q in range(1, n + 1):
        if q == n or abs(sim["vg"][q] - sim["vg"][s]) >= dv:
            groups.append([s, q])
            s = q
    k = 0
    while k < len(groups) and len(groups) > 1:   # 너무 짧은 묶음은 이웃과 합친다
        a, b_ = groups[k]
        if d[a:b_].sum() < min_len:
            if k + 1 < len(groups):
                groups[k + 1][0] = a
            else:
                groups[k - 1][1] = b_
            groups.pop(k)
        else:
            k += 1
    out = []
    for a, b_ in groups:
        T = dt[a:b_].sum()
        out.append((b_ / n, float(d[a:b_].sum() / T), float(T), float(sim["e"][b_ - 1])))
    return out


def merge_collinear(path, waits):
    """일직선 위의 중간 노드를 없앤다 (그 노드에서 호버 대기가 없을 때만).
    ArduCopter 는 경로점 사이를 직선으로 비행하므로 중간 점은 필요 없다."""
    path = [tuple(int(v) for v in p) for p in path]
    waits = [float(w) for w in waits]
    keep = [0]
    for n in range(1, len(path) - 1):
        a, b_, c = np.array(path[keep[-1]]), np.array(path[n]), np.array(path[n + 1])
        u, v = b_ - a, c - b_
        collinear = not np.cross(u, v).any() and np.dot(u, v) > 0
        if not (collinear and waits[n + 1] == 0.0):
            keep.append(n)
    keep.append(len(path) - 1)
    new_w = [0.0] + [waits[keep[q - 1] + 1] for q in range(1, len(keep))]
    return [path[k] for k in keep], new_w


def leg_items(site, drone, path, waits, t_cruise0, mass, ground_delay=0.0, e_before=0.0, e_takeoff=0.0,
              t_takeoff=0.0, t_land=0.0, e_land=0.0, t_ref=0.0, fr=None, simplify=True):
    """경로(노드 목록) → 미션 항목과 경로표.

    t_cruise0 : 첫 노드(이륙을 마친 고도)에서 수평 비행을 시작하는 시각 [s]
    t_ref     : 경로표의 시각 기준 (보통 임무 출발 시각)
    반환: dict(items=[...], rows=[...], summary={...})
    """
    fr = fr or site_frame()
    path = [tuple(int(v) for v in p) for p in path]
    n_nodes = len(path)
    if simplify:
        path, waits = merge_collinear(path, waits)
    x0, y0, z0 = _node_xyz(site, path[0])
    gh = float(site.ground[path[0][1], path[0][2]])                     # 홈(출발지) 지면 고도
    lon0, lat0 = (float(v) for v in xy2ll(x0, y0, fr))
    items, rows = [], []

    def soc(e):
        return float(100.0 - soc_drop(e_before + e, drone))

    def add(cmd, frame, p1=0.0, p2=0.0, p3=0.0, p4=0.0, lat=0.0, lon=0.0, alt=0.0, xy=None, **row):
        items.append(dict(seq=len(items), frame=frame, cmd=cmd, p=[p1, p2, p3, p4], lat=lat, lon=lon, alt=alt))
        if xy is not None and row.get("alt_msl") is not None:      # 지형·건물 표면 위 높이 (참고용)
            i = min(max(int(xy[0] // site.dx), 0), site.nx - 1)
            j = min(max(int(xy[1] // site.dx), 0), site.ny - 1)
            row["agl"] = float(row["alt_msl"] - site.ground[j, i])
            row["clear"] = float(row["alt_msl"] - site.surface[j, i])
        rows.append(dict(seq=len(items) - 1, cmd=cmd, lat=lat, lon=lon, alt_rel=alt if frame == FRAME_REL else None,
                         **row))

    add(CMD["WAYPOINT"], FRAME_ABS, lat=lat0, lon=lon0, alt=gh, note="홈 (출발지 지면)", alt_msl=gh,
        t=t_cruise0 - t_takeoff - ground_delay - t_ref, soc=soc(0.0))
    if ground_delay > 0.5:
        add(CMD["DELAY"], FRAME_ABS, p1=round(ground_delay, 1), p2=-1, p3=-1, p4=-1, note="이륙 전 지상 대기",
            t=t_cruise0 - t_takeoff - t_ref, soc=soc(0.0))
    add(CMD["TAKEOFF"], FRAME_REL, lat=lat0, lon=lon0, alt=round(z0 - gh, 1), note="이륙", alt_msl=z0, xy=(x0, y0),
        t=t_cruise0 - t_ref, soc=soc(e_takeoff))
    t, e, v_prev, dist = t_cruise0, e_takeoff, None, 0.0
    v_used = []
    for n in range(len(path) - 1):
        xa, ya, za = _node_xyz(site, path[n])
        xb, yb, zb = _node_xyz(site, path[n + 1])
        if waits[n + 1] > 0:
            lon, lat = (float(v) for v in xy2ll(xa, ya, fr))
            s1 = simulate(site, drone, [path[n], path[n + 1]], [0.0, float(waits[n + 1])], t, mass)
            # 대기 구간의 에너지만 떼어 낸다 (simulate 는 대기 뒤 이동까지 계산)
            s0 = simulate(site, drone, [path[n], path[n + 1]], [0.0, 0.0], t + float(waits[n + 1]), mass)
            e_wait = float(s1["e"][-1] - s0["e"][-1])
            t += float(waits[n + 1]); e += e_wait
            add(CMD["LOITER_TIME"], FRAME_REL, p1=round(float(waits[n + 1]), 1), lat=lat, lon=lon,
                alt=round(za - gh, 1), note="호버 대기", alt_msl=za, xy=(xa, ya), t=t - t_ref, soc=soc(e))
        sim = simulate(site, drone, [path[n], path[n + 1]], [0.0, 0.0], t, mass)
        if sim is None:
            raise ValueError("미션 변환 중 비행 불가 구간")
        lh = math.hypot(xb - xa, yb - ya)
        dist += math.sqrt(lh * lh + (zb - za) ** 2)
        if lh < 1e-6:                                    # 순수 수직 이동
            groups = [(1.0, None, float(sim["t"][-1] - t), float(sim["e"][-1]))]
        else:
            groups = _speed_groups(sim, lh, t)
        for f, v, T, e_cum in groups:
            if v is not None:
                v_cmd = round(v * 2) / 2.0
                v_used.append(v_cmd)
                if v_prev is None or abs(v_cmd - v_prev) >= 0.5:
                    add(CMD["CHANGE_SPEED"], FRAME_ABS, p1=1, p2=v_cmd, p3=-1, note="구간 대지속도 [m/s]",
                        t=t - t_ref, soc=soc(e), speed=v_cmd)
                    v_prev = v_cmd
            x, y, z = xa + (xb - xa) * f, ya + (yb - ya) * f, za + (zb - za) * f
            lon, lat = (float(u) for u in xy2ll(x, y, fr))
            t += T
            add(CMD["WAYPOINT"], FRAME_REL, lat=lat, lon=lon, alt=round(z - gh, 1),
                note="경로점" if f >= 1.0 else "속도 변경점", alt_msl=z, xy=(x, y), t=t - t_ref, soc=soc(e + e_cum),
                speed=None if v is None else round(v * 2) / 2.0)
        e += float(sim["e"][-1])
    xe, ye, ze = _node_xyz(site, path[-1])
    lon, lat = (float(u) for u in xy2ll(xe, ye, fr))
    add(CMD["LAND"], FRAME_REL, lat=lat, lon=lon, alt=0.0, note="착륙", alt_msl=float(site.ground[path[-1][1], path[-1][2]]),
        t=t + t_land - t_ref, soc=soc(e + e_land))
    alts = [it["alt"] for it in items if it["frame"] == FRAME_REL and it["cmd"] != CMD["LAND"]]
    summary = dict(n_items=len(items), n_plan_nodes=n_nodes, n_waypoints=sum(1 for it in items if it["cmd"] == CMD["WAYPOINT"]) - 1,
                   n_speed_changes=sum(1 for it in items if it["cmd"] == CMD["CHANGE_SPEED"]),
                   n_loiter=sum(1 for it in items if it["cmd"] == CMD["LOITER_TIME"]),
                   home=dict(lat=lat0, lon=lon0, alt_msl=gh), alt_rel_min=float(min(alts)), alt_rel_max=float(max(alts)),
                   speed_min=float(min(v_used)) if v_used else None, speed_max=float(max(v_used)) if v_used else None,
                   distance_m=float(dist), duration_s=float(t + t_land - (t_cruise0 - t_takeoff - ground_delay)),
                   soc_end=soc(e + e_land),
                   params=dict(WP_SPD_UP=drone.vz_up, WP_SPD_DN=drone.vz_dn,
                               note="계획기는 이 상승·하강률을 가정했다. 구버전 이름: WPNAV_SPEED_UP/DN [cm/s]"))
    return dict(items=items, rows=rows, summary=summary)


def to_wpl(items) -> str:
    """QGC WPL 110 텍스트."""
    lines = ["QGC WPL 110"]
    for it in items:
        p = it["p"]
        lines.append("\t".join([str(it["seq"]), "1" if it["seq"] == 0 else "0", str(it["frame"]), str(it["cmd"]),
                                f"{p[0]:.6f}", f"{p[1]:.6f}", f"{p[2]:.6f}", f"{p[3]:.6f}",
                                f"{it['lat']:.8f}", f"{it['lon']:.8f}", f"{it['alt']:.6f}", "1"]))
    return "\n".join(lines) + "\n"


def to_csv(rows) -> str:
    """경로표: 항목별 예상 도착 시각·SOC (운용자 확인용)."""
    name = {16: "WAYPOINT", 19: "LOITER_TIME", 21: "LAND", 22: "TAKEOFF", 93: "DELAY", 178: "CHANGE_SPEED"}
    out = ["seq,command,lat,lon,alt_rel_m,alt_msl_m,above_ground_m,above_surface_m,speed_ms,t_s,soc_pct,note"]
    f = lambda v, n=1: "" if v is None else f"{v:.{n}f}"  # noqa: E731
    for r in rows:
        out.append(",".join([str(r["seq"]), name[r["cmd"]], f(r["lat"], 7) if r["lat"] else "", f(r["lon"], 7) if r["lon"] else "",
                             f(r.get("alt_rel")), f(r.get("alt_msl")), f(r.get("agl")), f(r.get("clear")), f(r.get("speed")),
                             f(r.get("t")), f(r.get("soc")), r.get("note", "")]))
    return "\n".join(out) + "\n"


def mission_from_legs(site, drone, legs, t_dep, fr=None):
    """webapp 의 leg 목록(App._fly_legs 결과) → 미션 목록.

    이륙~착륙 한 번이 미션 하나다. 왕복은 구간마다 하나씩, 순회(중간 착륙 없음)는 전체가 하나의 미션이 된다."""
    groups = []
    for L in legs:
        if not L.get("feasible"):
            break
        if L.get("air_start") and groups:
            groups[-1].append(L)
        else:
            groups.append([L])
    out = []
    for G in groups:
        if G[-1].get("air_end"):          # 착륙까지 이어지지 않은 묶음(중간에 계획 실패)은 내보내지 않는다
            continue
        L0, Ln = G[0], G[-1]
        gd = float(L0["r"].get("ground_delay", 0.0))
        path = [tuple(int(v) for v in p) for p in L0["r"]["path"]]
        waits = [float(w) for w in L0.get("waits", L0["r"]["wait_before"])]
        for L in G[1:]:                   # 이어 붙이기: 다음 구간의 첫 노드는 앞 구간의 끝 노드와 같다
            path += [tuple(int(v) for v in p) for p in L["r"]["path"]][1:]
            waits += [float(w) for w in L["waits"]][1:]
        m = leg_items(site, drone, path, waits, L0["t_leg0"] + L0["t_to"] + gd, L0["m"], ground_delay=gd,
                      e_before=L0["e_before"], e_takeoff=L0["e_to"], t_takeoff=L0["t_to"], t_land=Ln["t_ld"],
                      e_land=Ln["e_ld"], t_ref=t_dep, fr=fr)
        name = L0["name"] if len(G) == 1 else "tour"
        out.append(dict(name=name, legs=[L["name"] for L in G], wpl=to_wpl(m["items"]), csv=to_csv(m["rows"]), **m))
    return out
