"""dpo-plan – 명령줄에서 경로를 계획하고 ArduPilot 미션 파일을 만든다.

예)
  dpo-plan --drone x650 --payload 1.0 --dep-hour 15 --out missions/
  dpo-plan --site changwon --drone x650 --tour "2600,2300;1900,2700;1200,2100" --dwell 10 --out missions/
  dpo-plan --hub 127.1055,37.4020 --dst 127.1271,37.3886 --lonlat --one-way --obstacles obstacles.json --out missions/
  dpo-plan --drone x650 --confidence 0.95 --forecast-age 30      # 30시간 전 예보로 계획할 때의 출발 판정
  dpo-plan --check

좌표는 지역 좌표 (x 동쪽, y 북쪽 [m], 지도 남서쪽 모서리 기준) 또는 --lonlat 을 주면 경도,위도다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _pt(text):
    a, b = (float(v) for v in text.split(","))
    return (a, b)


def build_parser():
    from . import __version__, config as C
    ap = argparse.ArgumentParser(prog="dpo-plan", description="SoC 최대화 경로 계획 → ArduPilot 미션 파일(.waypoints)·경로표(.csv)")
    ap.add_argument("--version", action="version", version=f"drone-path-opt {__version__}")
    ap.add_argument("--site", default=C.SITE_KEY, choices=list(C.SITES), help="대상 지역 (기본: 환경변수 DPO_SITE)")
    ap.add_argument("--drone", default="x650", choices=["m100", "x650", "m4t"])
    ap.add_argument("--payload", type=float, default=None, help="탑재중량 [kg] (기본: m4t 0.2, m100 0.5, x650 1.0)")
    ap.add_argument("--kind", default="delivery", choices=["delivery", "pickup"], help="배송(갈 때 적재) / 회수(올 때 적재)")
    ap.add_argument("--dep-hour", type=int, default=15, help="출발 시각 [시, KST] (9–20)")
    ap.add_argument("--hub", type=_pt, default=None, metavar="X,Y", help="출발지 (기본: 지역 기본값)")
    ap.add_argument("--dst", type=_pt, default=None, metavar="X,Y", help="목적지")
    ap.add_argument("--tour", default=None, metavar="X,Y;X,Y;...", help="순회 경유점 (주면 출발지로 돌아오는 순회 임무)")
    ap.add_argument("--dwell", type=float, default=0.0, help="경유점 체류 시간 [s]")
    ap.add_argument("--lonlat", action="store_true", help="--hub/--dst/--tour 를 경도,위도로 해석")
    ap.add_argument("--one-way", action="store_true", help="편도 (기본: 왕복)")
    ap.add_argument("--obstacles", default=None, metavar="FILE", help="장애물 JSON ({\"obstacles\": [...]} 또는 목록)")
    ap.add_argument("--eps", type=float, default=None, help="장애물 위치 불확실성의 위험 한도 ε (예: 0.05)")
    ap.add_argument("--reserve", type=float, default=20.0, help="예비 SOC [%%]")
    ap.add_argument("--no-safeguard", action="store_true", help="안전장치(최단 경로 후보 비교)를 끈다")
    ap.add_argument("--speed-mode", default="file", choices=["file", "command"],
                    help="file: 구간 속도를 미션 항목으로 넣음 / command: 속도 항목은 첫 구간만 (dpo-fly 가 비행 중 속도 명령을 보낼 때)")
    ap.add_argument("--confidence", type=float, default=None, metavar="LEVEL",
                    help="신뢰수준 (0.9 | 0.95 | 0.99). 주면 예보 오차를 넣은 SOC 분포로 출발 판정을 낸다")
    ap.add_argument("--forecast-age", type=float, default=6.0, metavar="H", help="계획에 쓴 예보가 발표된 뒤 지난 시간 [h]")
    ap.add_argument("--vg-max", type=float, default=None, metavar="V",
                    help="계획에 쓰는 대지속도 상한 [m/s] (기체 기본값보다 낮출 때). --wpml 을 주면 기본으로 --wpml-vmax 와 같게 둔다")
    ap.add_argument("--wpml", default=None, metavar="MODEL",
                    help="DJI 경로 파일(.kmz)도 함께 쓴다. MODEL: m30 | m30t | m300 | m350 | m3e | m3t | m3m | m3d | m3td (--out 필요)")
    ap.add_argument("--wpml-vmax", type=float, default=15.0, metavar="V", help="DJI 기종의 최대 경로 속도 [m/s]. 넘는 구간 속도는 깎는다")
    ap.add_argument("--wpml-finish", default="autoLand", choices=["autoLand", "goHome", "noAction", "gotoFirstWaypoint"],
                    help="DJI 경로를 마친 뒤의 동작")
    ap.add_argument("--wpml-rc-lost", default="goBack", choices=["goBack", "goContinue", "landing", "hover"],
                    help="DJI 조종기 신호가 끊겼을 때의 동작")
    ap.add_argument("--out", default=None, metavar="DIR", help="미션 파일을 쓸 폴더 (없으면 요약만 출력)")
    ap.add_argument("--json", action="store_true", help="요약을 JSON 으로 출력")
    ap.add_argument("--check", action="store_true", help="자료와 계획기를 점검하고 끝낸다")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.wpml and not a.out:
        print("--wpml 은 --out 과 함께 써야 합니다", file=sys.stderr)
        return 2
    from . import config as C
    from . import drones as DR
    C.use_site(a.site)
    missing = C.check_data()
    if missing:
        print("필수 자료가 없습니다:\n  " + "\n  ".join(missing) + f"\n자료 폴더(DPO_HOME): {C.ROOT}\n"
              "자료 묶음(dpo_data_<판>.zip)을 풀어 생긴 dpo_data 폴더 옆에서 실행하거나, 환경변수 DPO_HOME 을 그 폴더로 지정하십시오 (사용자 매뉴얼 1장).", file=sys.stderr)
        return 2
    from .webapp.server import App
    app = App()
    if a.check:
        h = app.health()
        print(json.dumps(h, ensure_ascii=False, indent=1) if a.json else
              f"정상: v{h['version']} · {h['site_label']} · 격자 {h['grid']} · 바람 {h['wind_hours']}시각 · 기체 {', '.join(h['drones'])}")
        return 0

    def conv(p):
        if p is None or not a.lonlat:
            return p
        from .data_terrain import ll2xy
        x, y = ll2xy(p[0], p[1])
        return (float(x), float(y))

    req = dict(drone=a.drone, payload=a.payload if a.payload is not None else DR.PAYLOAD_DEFAULT[a.drone],
               kind=a.kind, dep_hour=a.dep_hour, round_trip=not a.one_way, reserve=a.reserve, safeguard=not a.no_safeguard,
               eps=a.eps, dwell=a.dwell, obstacles=[])
    vg = a.vg_max if a.vg_max is not None else (a.wpml_vmax if a.wpml else None)
    if vg is not None:
        req["vg_max"] = vg
    if a.hub:
        req["hub"] = conv(a.hub)
    if a.dst:
        req["dst"] = conv(a.dst)
    if a.tour:
        req["tour"] = [conv(_pt(t)) for t in a.tour.split(";") if t.strip()]
    if a.obstacles:
        o = json.loads(Path(a.obstacles).read_text(encoding="utf-8"))
        req["obstacles"] = o["obstacles"] if isinstance(o, dict) else o
    try:
        r = app.plan(req)
    except ValueError as ex:
        print(f"입력 오류: {ex}", file=sys.stderr)
        return 2
    legs = [dict(name=L["name"], feasible=L["feasible"], soc=L.get("soc_leg"), T=L.get("T"), L=L.get("L"), wait=L.get("wait"),
                 chosen=L.get("chosen")) for L in r["legs"]]
    out = dict(feasible=r["feasible"], soc_used=r["soc_used"], soc_end=r["soc_end"], legs=legs, errors=r["errors"],
               safeguard=r["safeguard"], drone=r["drone"]["label"], site=C.SITE["label"], dep_hour=a.dep_hour, files=[])
    conf = None
    if a.confidence is not None:
        try:
            conf = app.confidence(dict(level=a.confidence, forecast_age_h=a.forecast_age))
        except ValueError as ex:
            print(f"입력 오류: {ex}", file=sys.stderr)
            return 2
        out["confidence"] = conf
    if r["feasible"] and a.out:
        m = app.mission(dict(speed_mode=a.speed_mode))
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        for M in m["legs"]:
            stem = f"{a.site}_{a.drone}_{a.dep_hour:02d}h_{M['name']}" + ("_cmd" if a.speed_mode == "command" else "")
            (d / f"{stem}.waypoints").write_text(M["wpl"], encoding="utf-8")
            (d / f"{stem}.csv").write_text(M["csv"], encoding="utf-8")
            out["files"] += [str(d / f"{stem}.waypoints"), str(d / f"{stem}.csv")]
        if a.wpml:
            import base64
            try:
                w = app.wpml(dict(model=a.wpml, v_max=a.wpml_vmax, finish=a.wpml_finish, rc_lost=a.wpml_rc_lost))
            except ValueError as ex:
                print(f"입력 오류: {ex}", file=sys.stderr)
                return 2
            out["wpml"] = dict(notes=w["notes"], legs=[dict(name=L["name"], **L["summary"]) for L in w["legs"]])
            for L in w["legs"]:
                f = d / f"{a.site}_{a.drone}_{a.dep_hour:02d}h_{L['name']}_{a.wpml}.kmz"
                f.write_bytes(base64.b64decode(L["kmz_b64"]))
                out["files"].append(str(f))
        (d / f"{a.site}_{a.drone}_{a.dep_hour:02d}h_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"{out['site']} · {out['drone']} · {a.dep_hour}시 출발 · 안전장치 {'켬' if r['safeguard']['on'] else '끔'}")
        for L in legs:
            if not L["feasible"]:
                print(f"  {L['name']:>5}: 경로 없음")
                continue
            ch = {"shortest": " (최단 경로 채택)", "energy": "", None: ""}[L["chosen"]]
            print(f"  {L['name']:>5}: SOC {L['soc']:5.1f} %p · {L['T']:5.0f} s · {L['L'] / 1000:5.2f} km · 대기 {L['wait']:.0f} s{ch}")
        print(f"  합계 SOC 소모 {out['soc_used']:.1f} %p → 도착 SOC {out['soc_end']:.1f} % " +
              ("(예비 확보)" if r["feasible"] else "(임무 불가: 예비 SOC 미달 또는 경로 없음)"))
        if conf is not None:
            word = {"go": "가능", "caution": "주의", "no-go": "불가"}[conf["decision"]]
            print(f"  신뢰도 판정: {word} – {conf['reason']}")
            print(f"    예보 시점 {conf['forecast_age_h']:g} 시간 전 → 바람 오차 σ {conf['sigma']:.2f} m/s (성분별), "
                  + ("여유 계산 불가" if conf["margin_pp"] is None else f"신뢰수준에 필요한 여유 {conf['margin_pp']:.1f} %p"))
            sn = conf["season"]
            print(f"    {conf['month']}월 권고: {sn.get('note', '')}")
        for e in r["errors"]:
            print(f"  경고: {e}")
        for f in out["files"]:
            print(f"  → {f}")
        if out.get("wpml"):
            print("  DJI 경로 파일(WPML) – DJI 기체·소프트웨어에서 확인하지 못한 파일입니다. 불러온 뒤 고도·속도·복귀 고도를 확인하세요.")
            for L in out["wpml"]["legs"]:
                print(f"    {L['name']:>5}: {L['model_label']} · 경로점 {L['n_waypoints']}개 · 상대 고도 {L['height_min']:.0f}–{L['height_max']:.0f} m · "
                      f"속도 {L['speed_min']:g}–{L['speed_max']:g} m/s · 복귀 고도 {L['rth_height']:.0f} m · 검사 {'통과' if not L['check'] else L['check']}")
                for w in L["warnings"]:
                    print(f"           ! {w}")
            print(f"    ! {out['wpml']['notes'][0]}")
    if not r["feasible"]:
        return 1
    return 4 if (conf is not None and conf["decision"] != "go") else 0


if __name__ == "__main__":
    sys.exit(main())
