"""새 지역 만들기: 장소 검색 → 영역 → 지형(Copernicus)·건물(Overture)·최신 예보 바람(GFS) 자동 구축 → 등록.

    dpo-site search "강릉 경포대"                       장소 찾기 (위도, 경도를 바로 넣어도 됨: "37.795, 128.896")
    dpo-site create --name 강릉경포 --lat 37.795 --lon 128.896 [--size 5] [--date 2026-11-22]
    dpo-site wind <key> [--date …]                     그 지역의 예보 바람을 최신(또는 지정한 날)으로 바꿈
    dpo-site list / dpo-site delete <key>

자료는 모두 공개 자료이며 인터넷이 필요하다 (Copernicus GLO-30 DSM, Overture Maps 건물, NOAA GFS 0.25°).
장소 검색은 OpenStreetMap Nominatim 을 쓰고, 환경변수 DPO_VWORLD_KEY 가 있으면 국토교통부 브이월드 검색을 먼저 쓴다.
비행 금지·제한 구역(공역)은 이 자료에 없다 – 3D 화면의 장애물 편집으로 넣거나 운용 전에 따로 확인해야 한다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from . import config as C

SIZE_MIN, SIZE_MAX = 2.0, 8.0          # 지역 한 변 [km] – 격자 25 m 에서 80–320 칸

def _ua():
    from . import __version__
    return f"drone-path-opt/{__version__} (local route planning tool)"


# ------------------------------------------------------------------ 장소 검색
_LL = re.compile(r"^\s*([NS]?)\s*(-?\d+(?:\.\d+)?)\s*°?\s*([NS]?)\s*[,\s]\s*([EW]?)\s*(-?\d+(?:\.\d+)?)\s*°?\s*([EW]?)\s*$", re.I)


def parse_latlon(q: str):
    """'37.795, 128.896' · 'N37.795 E128.896' → (위도, 경도). 아니면 None."""
    m = _LL.match(q or "")
    if not m:
        return None
    lat, lon = float(m.group(2)), float(m.group(5))
    if (m.group(1) + m.group(3)).upper() == "S":
        lat = -abs(lat)
    if (m.group(4) + m.group(6)).upper() == "W":
        lon = -abs(lon)
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        return None
    return lat, lon


def _get_json(url, params):
    """표준 라이브러리만으로 GET → JSON (장소 검색은 '새 지역' 추가 라이브러리 없이도 되게)."""
    import urllib.parse
    import urllib.request
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": _ua()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _nominatim(q, limit):
    url = os.environ.get("DPO_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
    out = []
    for it in _get_json(url, dict(q=q, format="jsonv2", limit=limit, **{"accept-language": "ko"})):
        out.append(dict(name=it.get("display_name") or it.get("name") or q, lat=float(it["lat"]), lon=float(it["lon"]),
                        kind=it.get("type") or it.get("category") or "", source="OpenStreetMap"))
    return out


def _vworld(q, limit, key):
    out = []
    for typ, cat in (("PLACE", None), ("ADDRESS", "ROAD"), ("ADDRESS", "PARCEL")):
        p = dict(service="search", request="search", version="2.0", crs="EPSG:4326", size=limit, page=1, query=q, type=typ,
                 format="json", errorformat="json", key=key)
        if cat:
            p["category"] = cat
        res = _get_json("https://api.vworld.kr/req/search", p).get("response", {})
        if res.get("status") != "OK":
            continue
        for it in res.get("result", {}).get("items", []):
            ad = it.get("address") or {}
            name = it.get("title") or ad.get("road") or ad.get("parcel") or q
            if ad.get("road") or ad.get("parcel"):
                name += f" ({ad.get('road') or ad.get('parcel')})" if it.get("title") else ""
            out.append(dict(name=name, lat=float(it["point"]["y"]), lon=float(it["point"]["x"]), kind=typ.lower(), source="브이월드"))
        if len(out) >= limit:
            break
    return out[:limit]


def geocode(q: str, limit: int = 8):
    """장소·주소 검색. 반환: [dict(name, lat, lon, kind, source)]. 좌표를 넣으면 그대로 돌려준다."""
    q = (q or "").strip()
    if not q:
        raise ValueError("찾을 장소를 넣으십시오")
    ll = parse_latlon(q)
    if ll:
        return [dict(name=f"좌표 {ll[0]:.5f}, {ll[1]:.5f}", lat=ll[0], lon=ll[1], kind="좌표", source="입력")]
    errs = []
    key = os.environ.get("DPO_VWORLD_KEY")
    if key:
        try:
            r = _vworld(q, limit, key)
            if r:
                return r
        except Exception as ex:  # noqa
            errs.append(f"브이월드: {type(ex).__name__}")
    try:
        return _nominatim(q, limit)
    except Exception as ex:  # noqa
        errs.append(f"OpenStreetMap: {type(ex).__name__}")
    raise ConnectionError("장소 검색 서버에 연결하지 못했습니다 (" + ", ".join(errs) + "). 인터넷 연결을 확인하거나 "
                          "'37.795, 128.896' 처럼 위도, 경도를 바로 넣으십시오.")


# ------------------------------------------------------------------ 예보 시각
def gfs_available(day: datetime) -> bool:
    import requests
    ymd = day.strftime("%Y%m%d")
    url = f"{C.GFS_BUCKET}/gfs.{ymd}/00/atmos/gfs.t00z.pgrb2.0p25.f012.idx"
    try:
        return requests.head(url, timeout=20).status_code == 200
    except Exception:  # noqa
        return False


def latest_cycle(now=None) -> datetime:
    """받을 수 있는 가장 최근 00Z 예보 (00Z = 09시 KST, 12시간 뒤까지 = 21시). 보통 그날 13–14시(KST)부터 받을 수 있다."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    d = datetime(now.year, now.month, now.day)
    for k in range(4):
        if gfs_available(d - timedelta(days=k)):
            return d - timedelta(days=k)
    raise ConnectionError("최근 4일 안의 GFS 예보를 찾지 못했습니다 (인터넷 연결 확인)")


def _cycle(date: str | None):
    if not date:
        return latest_cycle()
    d = datetime.strptime(date, "%Y-%m-%d")
    if not gfs_available(d):
        raise ValueError(f"{date} 00Z 예보를 받을 수 없습니다 (아직 발표 전이거나 보관 기간 밖)")
    return d


# ------------------------------------------------------------------ 지역 만들기
def need_libs():
    """새 지역 만들기에 필요한 라이브러리 확인 (설치: pip install "drone_path_opt[region]")."""
    import importlib.util as u
    miss = [m for m in ("requests", "rasterio", "pyarrow", "fsspec", "aiohttp") if u.find_spec(m) is None]
    if u.find_spec("eccodes") is None and u.find_spec("pygrib") is None:
        miss.append("eccodes")
    if miss:
        raise ValueError("새 지역 만들기에 필요한 라이브러리가 없습니다: " + ", ".join(miss)
                         + ' – 설치: pip install "drone_path_opt[region]" (배포판의 설치 파일은 함께 설치함)')


def site_dict(key: str, name: str, lat: float, lon: float, size_km: float, cycle: datetime) -> dict:
    if not (SIZE_MIN <= size_km <= SIZE_MAX):
        raise ValueError(f"지역 크기는 {SIZE_MIN:g}–{SIZE_MAX:g} km")
    if not (-80 <= lat <= 80 and -180 <= lon <= 180):
        raise ValueError("위도·경도 범위를 벗어났습니다")
    half = size_km * 500.0
    mlat = 111_132.92 - 559.82 * math.cos(2 * math.radians(lat))
    mlon = 111_412.84 * math.cos(math.radians(lat))
    dla, dlo = half / mlat, half / mlon
    la0, la1, lo0, lo1 = round(lat - dla, 5), round(lat + dla, 5), round(lon - dlo, 5), round(lon + dlo, 5)
    tiles = [C.dsm_tile_name(a, b) for a in range(math.floor(la0), math.floor(la1) + 1) for b in range(math.floor(lo0), math.floor(lo1) + 1)]
    ymd = cycle.strftime("%Y%m%d")
    return dict(label=name, name=name, lon=(lo0, lo1), lat=(la0, la1), dsm_tiles=tiles,
                grid=f"site_grid_{key}.npz", buildings=f"overture_buildings_{key}.json",
                gfs=f"gfs_{key}_{ymd}_00z.npz", gfs_truth=None, t0=f"{cycle:%Y-%m-%d}T00:00",
                date=f"{cycle:%Y-%m-%d} (GFS 00Z 예보)", anchor=(lon, lat), hub=None, dst=None, tour=None, nfz=[],
                hub_name=name, center=(lat, lon), size_km=size_km, created=datetime.now().strftime("%Y-%m-%d %H:%M"))


def _say(progress, step, n, msg):
    if progress:
        progress(dict(step=step, n=n, msg=msg))


def _wind(S, key, cycle, progress, step, n):
    from .data_wind import fetch_gfs_series
    tag = S["gfs"][len("gfs_"):-len(".npz")]
    _say(progress, step, n, f"예보 바람 받기 (GFS {cycle:%Y-%m-%d} 00Z, 09–21시 13시각)")
    t0 = time.time()
    d = fetch_gfs_series(cycle, list(range(0, 13)), tag, workers=4)
    import numpy as np
    ws = np.hypot(d["UGRD_agl100"], d["VGRD_agl100"])
    _say(progress, step, n, f"예보 바람 완료 ({time.time() - t0:.0f} s, 100 m 풍속 {ws.min():.1f}–{ws.max():.1f} m/s)")


def create(name: str, lat: float, lon: float, size_km: float = 5.0, date: str | None = None, progress=print, key=None) -> str:
    """지역을 만들고 등록한다. 반환: 지역 key. 같은 프로세스의 현재 지역(C.use_site)이 바뀐다."""
    need_libs()
    from .data_terrain import build_site_grid, fetch_buildings, fetch_dsm, site_frame
    name = (name or "").strip() or f"{lat:.4f}, {lon:.4f}"
    if len(name) > 40:
        raise ValueError("지역 이름은 40자 이내")
    key = key or "r" + datetime.now().strftime("%Y%m%d%H%M%S")
    n = 5
    _say(progress, 1, n, "예보 확인")
    cycle = _cycle(date)
    S = site_dict(key, name, lat, lon, size_km, cycle)
    C.SITES[key] = dict(S)
    C.use_site(key)
    fr = site_frame()
    _say(progress, 2, n, f"지형 받기 (Copernicus GLO-30, 타일 {', '.join(S['dsm_tiles'])}) – 격자 {fr['nx']}×{fr['ny']}")
    t0 = time.time()
    got = [t for t in S["dsm_tiles"] if fetch_dsm(t) is not None]
    if not got:
        C.SITES.pop(key, None)
        raise ValueError("이 위치의 지형 자료가 없습니다 (바다 위 등)")
    _say(progress, 2, n, f"지형 완료 ({time.time() - t0:.0f} s)")
    _say(progress, 3, n, "건물 받기 (Overture Maps) – 처음에는 수 분 걸릴 수 있음")
    t0 = time.time()
    feats = fetch_buildings()
    _say(progress, 3, n, f"건물 {len(feats):,}동 ({time.time() - t0:.0f} s)")
    _say(progress, 4, n, "격자 만들기")
    g = build_site_grid(force=True)
    _say(progress, 4, n, f"지면 {g['ground'].min():.0f}–{g['ground'].max():.0f} m, 최고 건물 {g['bldg_h'].max():.0f} m")
    _wind(S, key, cycle, progress, 5, n)
    C.register_site(key, S)
    _say(progress, n, n, f"완료: {name} ({key})")
    return key


def refresh_wind(key: str, date: str | None = None, progress=print) -> str:
    """지역의 예보 바람을 최신(또는 지정한 날)으로 바꾼다."""
    if key not in C.SITES:
        raise ValueError(f"알 수 없는 지역: {key}")
    if not C.SITES[key].get("user"):
        raise ValueError("기본 지역(판교·창원)의 바람은 보고서 재현용으로 고정되어 있습니다")
    need_libs()
    _say(progress, 1, 2, "예보 확인")
    cycle = _cycle(date)
    S = dict(C.SITES[key])
    S.update(gfs=f"gfs_{key}_{cycle:%Y%m%d}_00z.npz", t0=f"{cycle:%Y-%m-%d}T00:00", date=f"{cycle:%Y-%m-%d} (GFS 00Z 예보)")
    C.SITES[key] = S
    C.use_site(key)
    _wind(S, key, cycle, progress, 2, 2)
    C.register_site(key, S)
    return S["date"]


def list_sites():
    out = []
    for k, S in C.SITES.items():
        lat = 0.5 * (S["lat"][0] + S["lat"][1])
        lon = 0.5 * (S["lon"][0] + S["lon"][1])
        out.append(dict(key=k, label=S["label"], user=bool(S.get("user")), lat=lat, lon=lon, lat_range=list(S["lat"]),
                        lon_range=list(S["lon"]), date=S.get("date"), ready=not C.check_data(k)))
    return out


def delete(key: str):
    S = C.SITES.get(key)
    if S is None or not S.get("user"):
        raise ValueError("사용자가 만든 지역만 지울 수 있습니다")
    for f in (C.PROC / S["grid"], C.RAW / S["buildings"]):
        if f.exists():
            f.unlink()
    for f in C.RAW.glob(f"gfs_{key}_*.npz"):
        f.unlink()
    jf = C.USER_SITES_DIR / f"{key}.json"
    if jf.exists():
        jf.unlink()
    C.SITES.pop(key, None)


# ------------------------------------------------------------------ 명령줄
def main(argv=None):
    ap = argparse.ArgumentParser(prog="dpo-site", description="새 지역 만들기 (장소 검색 → 지형·건물·예보 바람 자동 구축)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search", help="장소·주소 검색")
    p.add_argument("query")
    p = sub.add_parser("create", help="지역 만들기")
    p.add_argument("--name", required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--size", type=float, default=5.0, help=f"한 변 [km] ({SIZE_MIN:g}–{SIZE_MAX:g}, 기본 5)")
    p.add_argument("--date", help="예보 날짜 YYYY-MM-DD (기본: 받을 수 있는 최신)")
    p.add_argument("--key", help=argparse.SUPPRESS)
    p = sub.add_parser("wind", help="예보 바람을 최신으로")
    p.add_argument("key")
    p.add_argument("--date")
    sub.add_parser("list", help="지역 목록")
    p = sub.add_parser("delete", help="사용자 지역 지우기")
    p.add_argument("key")
    ap.add_argument("--json", action="store_true", help="진행 상황을 JSON 한 줄씩 출력 (3D 화면이 씀)")
    a = ap.parse_args(argv)

    def prog(d):
        print(json.dumps(d, ensure_ascii=False) if a.json else f"[{d['step']}/{d['n']}] {d['msg']}", flush=True)

    try:
        if a.cmd == "search":
            r = geocode(a.query)
            if a.json:
                print(json.dumps(r, ensure_ascii=False))
            for it in r if not a.json else []:
                print(f"{it['lat']:.5f}, {it['lon']:.5f}  {it['name']}  ({it['source']})")
        elif a.cmd == "create":
            k = create(a.name, a.lat, a.lon, a.size, a.date, prog, key=a.key)
            print(json.dumps(dict(done=True, key=k), ensure_ascii=False) if a.json else
                  f"만들었습니다: {k}  → dpo-server --site {k} --open  (명령줄: dpo-plan --site {k})")
        elif a.cmd == "wind":
            d = refresh_wind(a.key, a.date, prog)
            print(json.dumps(dict(done=True, key=a.key, date=d), ensure_ascii=False) if a.json else f"예보 바람: {d}")
        elif a.cmd == "list":
            for s in list_sites():
                print(f"{s['key']:18s} {'사용자' if s['user'] else '기본':4s} {'준비됨' if s['ready'] else '자료 없음':6s} "
                      f"{s['lat']:.4f}, {s['lon']:.4f}  {s['label']}  [{s['date']}]")
        elif a.cmd == "delete":
            delete(a.key)
            print(f"지웠습니다: {a.key}")
    except (ValueError, ConnectionError, OSError) as ex:
        if a.json:
            print(json.dumps(dict(error=str(ex)), ensure_ascii=False), flush=True)
        else:
            print(f"오류: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
