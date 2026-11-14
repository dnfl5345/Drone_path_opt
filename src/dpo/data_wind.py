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
import requests

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
    import pygrib

    ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    url = f"{C.GFS_BUCKET}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fh:03d}"
    msgs = [(v, l, s, e) for v, l, s, e in _idx(url) if _want(v, l)]
    out = {}
    for var, lev, s, e in msgs:
        rng = f"bytes={s}-{e}" if e else f"bytes={s}-"
        b = _get(url, headers={"Range": rng}).content
        with tempfile.NamedTemporaryFile(suffix=".grb2") as tf:
            tf.write(b)
            tf.flush()
            g = pygrib.open(tf.name).message(1)
            data, lats, lons = g.data(lat1=C.LAT_MIN - PAD, lat2=C.LAT_MAX + PAD,
                                      lon1=C.LON_MIN - PAD, lon2=C.LON_MAX + PAD)
        kind, lv = _want(var, lev)
        key = f"{var}_{kind}{lv}"
        out[key] = data.astype(np.float32)
        out["lat"], out["lon"] = lats[:, 0].astype(np.float32), lons[0].astype(np.float32)
    # 기압면 고도(지오포텐셜) – 975/950 hPa 실제 고도 추정용
    for var, lev, s, e in _idx(url):
        if var == "HGT" and lev in [f"{p} mb" for p in C.GFS_LEVELS_P]:
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            with tempfile.NamedTemporaryFile(suffix=".grb2") as tf:
                tf.write(b)
                tf.flush()
                g = pygrib.open(tf.name).message(1)
                data, _, _ = g.data(lat1=C.LAT_MIN - PAD, lat2=C.LAT_MAX + PAD,
                                    lon1=C.LON_MIN - PAD, lon2=C.LON_MAX + PAD)
            out[f"HGT_p{lev.split()[0]}"] = data.astype(np.float32)
        if var == "HGT" and lev == "surface":
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            with tempfile.NamedTemporaryFile(suffix=".grb2") as tf:
                tf.write(b)
                tf.flush()
                g = pygrib.open(tf.name).message(1)
                data, _, _ = g.data(lat1=C.LAT_MIN - PAD, lat2=C.LAT_MAX + PAD,
                                    lon1=C.LON_MIN - PAD, lon2=C.LON_MAX + PAD)
            out["HGT_sfc"] = data.astype(np.float32)
    out["valid"] = np.datetime64(cycle + timedelta(hours=fh))
    return out


def fetch_gfs_series(cycle: datetime, fhs, tag: str):
    path = C.RAW / f"gfs_{tag}.npz"
    if path.exists():
        return dict(np.load(path, allow_pickle=True))
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
    import pygrib

    ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    url = f"{C.GFS_BUCKET}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fh:03d}"
    vals = {}
    for var, lev, s, e in _idx(url):
        if var in ("UGRD", "VGRD") and lev == "100 m above ground":
            b = _get(url, headers={"Range": f"bytes={s}-{e}"}).content
            with tempfile.NamedTemporaryFile(suffix=".grb2") as tf:
                tf.write(b)
                tf.flush()
                g = pygrib.open(tf.name).message(1)
                d, la, lo = g.data(lat1=37.25, lat2=37.5, lon1=127.0, lon2=127.25)
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
    with open(C.RAW / "gfs_scan_oct2026.csv", "w") as f:
        f.write("date,u100,v100,ws100\n")
        for d, u, v in rows:
            f.write(f"{d:%Y-%m-%d},{u:.2f},{v:.2f},{np.hypot(u, v):.2f}\n")
            print(f"{d:%Y-%m-%d} ws100={np.hypot(u, v):5.2f} m/s  dir_from={(np.degrees(np.arctan2(-u, -v)) % 360):5.0f}")
