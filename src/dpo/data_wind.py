"""실제 바람 정보 수집: NOAA GFS 0.25° 예보 (AWS Open Data).

- 고도별 바람(U,V): 10/20/30/40/50/80/100 m AGL + 975/950 hPa
- 시간별 예보(f000~f0xx), 사이클(00/06/12/18Z)
- .idx 파일로 필요한 GRIB 메시지 byte-range만 다운로드 (전 지구 파일 ~500 MB 중 ~14 MB)

※ 기상청(KMA) API허브/공공데이터포털은 이 개발환경 네트워크 정책상 접근 불가 →
  동일 인터페이스의 KMA 어댑터(kma_adapter.py)를 별도 제공, 키만 넣으면 교체 가능.
"""
from __future__ import annotations

import io
import re
import tempfile
from datetime import datetime, timedelta

import numpy as np
try:                                   # 자료를 내려받을 때만 필요 (pip install "drone-path-opt[data]")
    import requests
except ImportError:                    # pragma: no cover
    requests = None

from . import config as C

PAD = 0.5  # 대상 지역 주변 0.5° 만큼 잘라 저장


def _get(url, **kw):
    """네트워크 일시 오류 대비 지수 백오프 재시도."""
    import time
    for k in range(6):
        try:
            r = requests.get(url, timeout=120, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if k == 5:
                raise
            time.sleep(2 ** k)


def _decode(b: bytes, lat1, lat2, lon1, lon2):
    """GRIB2 메시지 하나를 풀어 위경도 범위(경계 포함)만 잘라 (값, 위도 2차원, 경도 2차원) 으로 돌려준다.

    ecCodes(eccodes 패키지 – Windows·macOS·Linux 모두 설치 파일 있음)를 먼저 쓰고, 없으면 pygrib 을 쓴다.
    정규 위경도 격자(GFS 0.25°)만 다룬다. 행은 파일의 주사 순서(북 → 남), 열은 경도 오름차순 (pygrib 의 data() 와 같음)."""
    try:
        import eccodes as ec
    except ImportError:
        ec = None
    if ec is None:
        import pygrib
        with tempfile.NamedTemporaryFile(suffix=".grb2", delete=False) as tf:
            tf.write(b)
            name = tf.name
        try:
            g = pygrib.open(name).message(1)
            return g.data(lat1=lat1, lat2=lat2, lon1=lon1, lon2=lon2)
        finally:
            import os
            os.unlink(name)
    h = ec.codes_new_from_message(b)
    try:
        if ec.codes_get(h, "gridType") != "regular_ll":
            raise ValueError("정규 위경도 격자가 아닙니다")
        ni, nj = ec.codes_get(h, "Ni"), ec.codes_get(h, "Nj")
        la0, la1 = ec.codes_get_double(h, "latitudeOfFirstGridPointInDegrees"), ec.codes_get_double(h, "latitudeOfLastGridPointInDegrees")
        lo0 = ec.codes_get_double(h, "longitudeOfFirstGridPointInDegrees")
        dlo = ec.codes_get_double(h, "iDirectionIncrementInDegrees")
        vals = ec.codes_get_values(h).reshape(nj, ni)
    finally:
        ec.codes_release(h)
    lats = np.linspace(la0, la1, nj)
    lons = (lo0 + dlo * np.arange(ni)) % 360.0
    lo_a, lo_b = lon1 % 360.0, lon2 % 360.0
    eps = 1e-6
    rj = np.where((lats >= lat1 - eps) & (lats <= lat2 + eps))[0]
    ci = np.where((lons >= lo_a - eps) & (lons <= lo_b + eps))[0] if lo_a <= lo_b else \
        np.where((lons >= lo_a - eps) | (lons <= lo_b + eps))[0]
    sub = vals[np.ix_(rj, ci)]
    LO, LA = np.meshgrid(lons[ci], lats[rj])
    return sub, LA, LO


def _idx(url):
    t = _get(url + ".idx")
    lines = t.text.strip().splitlines()
    out = []
    for i, ln in enumerate(lines):
        p = ln.split(":")
        start = int(p[1])
        end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
        out.append((p[3], p[4], start, end))
    return out


def _want(var, lev):
    if var not in ("UGRD", "VGRD"):
        return None
    m = re.fullmatch(r"(\d+) m above ground", lev)
    if m and int(m.group(1)) in C.GFS_LEVELS_AGL:
        return ("agl", int(m.group(1)))
    m = re.fullmatch(r"(\d+) mb", lev)
    if m and int(m.group(1)) in C.GFS_LEVELS_P:
        return ("p", int(m.group(1)))
    if lev == "surface":
        return None
    return None


def fetch_gfs_hour(cycle: datetime, fh: int):
    """한 예보시각의 U/V 고도별 필드를 대상지역 주변만 잘라 dict로 반환."""

    ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    url = f"{C.GFS_BUCKET}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fh:03d}"
    msgs = [(v, l, s, e) for v, l, s, e in _idx(url) if _want(v, l)]
    out = {}
    for var, lev, s, e in msgs:
        rng = f"bytes={s}-{e}" if e else f"bytes={s}-"
        b = _get(url, headers={"Range": rng}).content
        data, lats, lons = _decode(b, C.LAT_MIN - PAD, C.LAT_MAX + PAD, C.LON_MIN - PAD, C.LON_MAX + PAD)
        kind, lv = _want(var, lev)
        key = f"{var}_{kind}{lv}"
        out[key] = data.astype(np.float32)
        out["lat"], out["lon"] = lats[:, 0].astype(np.float32), lons[0].astype(np.float32)
    # 기압면 고도(지오포텐셜) – 975/950 hPa 실제 고도 추정용
    for var, lev, s, e in _idx(url):
        if var == "HGT" and lev in [f"{p} mb" for p in C.GFS_LEVELS_P]:
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            data, _, _ = _decode(b, C.LAT_MIN - PAD, C.LAT_MAX + PAD, C.LON_MIN - PAD, C.LON_MAX + PAD)
            out[f"HGT_p{lev.split()[0]}"] = data.astype(np.float32)
        if var == "HGT" and lev == "surface":
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            data, _, _ = _decode(b, C.LAT_MIN - PAD, C.LAT_MAX + PAD, C.LON_MIN - PAD, C.LON_MAX + PAD)
            out["HGT_sfc"] = data.astype(np.float32)
    out["valid"] = np.datetime64(cycle + timedelta(hours=fh))
    return out


def fetch_gfs_series(cycle: datetime, fhs, tag: str, workers: int = 1):
    path = C.RAW / f"gfs_{tag}.npz"
    if path.exists():
        return dict(np.load(path, allow_pickle=True))
    if workers > 1:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(workers) as ex:
            frames = list(ex.map(lambda fh: fetch_gfs_hour(cycle, fh), fhs))
    else:
        frames = [fetch_gfs_hour(cycle, fh) for fh in fhs]
    keys = [k for k in frames[0] if k not in ("lat", "lon", "valid")]
    pack = {k: np.stack([f[k] for f in frames]) for k in keys}
    pack["lat"], pack["lon"] = frames[0]["lat"], frames[0]["lon"]
    pack["valid"] = np.array([f["valid"] for f in frames])
    pack["cycle"] = np.datetime64(cycle)
    np.savez_compressed(path, **pack)
    return dict(np.load(path, allow_pickle=True))


def site_wind_100m(cycle: datetime, fh: int):
    """사이트 중심 100 m 풍속만 빠르게 조회(강풍일 선정용)."""

    ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    url = f"{C.GFS_BUCKET}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fh:03d}"
    vals = {}
    for var, lev, s, e in _idx(url):
        if var in ("UGRD", "VGRD") and lev == "100 m above ground":
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            d, la, lo = _decode(b, 37.25, 37.5, 127.0, 127.25)
            vals[var] = float(d.mean())
    return vals["UGRD"], vals["VGRD"]


if __name__ == "__main__":
    import sys
    import concurrent.futures as cf

    # 1) 10월 한 달 + 11/1, 03Z(=12시 KST) 100 m 풍속 스캔 → 바람 강한 날 선정
    days = [datetime(2026, 10, 1) + timedelta(days=i) for i in range(31)]

    def one(d):
        try:
            u, v = site_wind_100m(d, 3)
            return d, u, v
        except Exception as ex:  # noqa
            return d, np.nan, np.nan

    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, days))
    with open(C.RAW / "gfs_scan_oct2026.csv", "w", encoding="utf-8") as f:
        f.write("date,u100,v100,ws100\n")
        for d, u, v in rows:
            f.write(f"{d:%Y-%m-%d},{u:.2f},{v:.2f},{np.hypot(u, v):.2f}\n")
            print(f"{d:%Y-%m-%d} ws100={np.hypot(u, v):5.2f} m/s  dir_from={(np.degrees(np.arctan2(-u, -v)) % 360):5.0f}")
