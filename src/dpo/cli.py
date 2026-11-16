"""dpo-plan – 명령줄에서 경로를 계획하고 ArduPilot 미션 파일을 만든다.

예)
  dpo-plan --drone x650 --payload 1.0 --dep-hour 15 --out missions/
  dpo-plan --site changwon --drone x650 --tour "2600,2300;1900,2700;1200,2100" --dwell 10 --out missions/
  dpo-plan --hub 127.1055,37.4020 --dst 127.1271,37.3886 --lonlat --one-way --obstacles obstacles.json --out missions/
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
    ap.add_argument("--drone", default="x650", choices=["m100", "x650"])
    ap.add_argument("--payload", type=float, default=None, help="탑재중량 [kg] (기본: m100 0.5, x650 1.0)")
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
    ap.add_argument("--out", default=None, metavar="DIR", help="미션 파일을 쓸 폴더 (없으면 요약만 출력)")
    ap.add_argument("--json", action="store_true", help="요약을 JSON 으로 출력")
    ap.add_argument("--check", action="store_true", help="자료와 계획기를 점검하고 끝낸다")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    from . import config as C
    C.use_site(a.site)
    missing = C.check_data()
    if missing:
        print("필수 자료가 없습니다:\n  " + "\n  ".join(missing) + f"\n자료 폴더(DPO_HOME): {C.ROOT}", file=sys.stderr)
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

    req = dict(drone=a.drone, payload=a.payload if a.payload is not None else (0.5 if a.drone == "m100" else 1.0),
               kind=a.kind, dep_hour=a.dep_hour, round_trip=not a.one_way, reserve=a.reserve, safeguard=not a.no_safeguard,
               eps=a.eps, dwell=a.dwell, obstacles=[])
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
    if r["feasible"] and a.out:
        m = app.mission()
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        for M in m["legs"]:
            stem = f"{a.site}_{a.drone}_{a.dep_hour:02d}h_{M['name']}"
            (d / f"{stem}.waypoints").write_text(M["wpl"], encoding="utf-8")
            (d / f"{stem}.csv").write_text(M["csv"], encoding="utf-8")
            out["files"] += [str(d / f"{stem}.waypoints"), str(d / f"{stem}.csv")]
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
        for e in r["errors"]:
            print(f"  경고: {e}")
        for f in out["files"]:
            print(f"  → {f}")
    return 0 if r["feasible"] else 1


if __name__ == "__main__":
    sys.exit(main())
