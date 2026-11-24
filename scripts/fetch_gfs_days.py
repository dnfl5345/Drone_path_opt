"""여러 날짜의 GFS 바람(00Z 계획용, 06Z 평가용)을 한 지역에 대해 받는다.

사용: PYTHONPATH=src python scripts/fetch_gfs_days.py <site> 20261001 20261002 ...
저장: data/raw/gfs_<site>_<날짜>_00z.npz, ..._06z.npz  (판교의 2026-10-27 은 기존 파일 이름을 그대로 쓴다)
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
from datetime import datetime

import numpy as np

from dpo import config as C


def gfs_name(site, ymd, cyc):
    if site == "pangyo" and ymd == "20261027":
        return f"gfs_20261027_{cyc}z.npz"
    return f"gfs_{site}_{ymd}_{cyc}z.npz"


def _hour(args):
    site, cycle, fh = args
    C.use_site(site)
    from dpo.data_wind import fetch_gfs_hour
    return fetch_gfs_hour(cycle, fh)


def fetch(site, ymd, ex):
    C.use_site(site)
    for cyc, fhs in (("00", range(0, 13)), ("06", range(0, 7))):
        path = C.RAW / gfs_name(site, ymd, cyc)
        if path.exists():
            continue
        cycle = datetime.strptime(ymd + cyc, "%Y%m%d%H")
        frames = list(ex.map(_hour, [(site, cycle, fh) for fh in fhs]))
        keys = [k for k in frames[0] if k not in ("lat", "lon", "valid")]
        pack = {k: np.stack([f[k] for f in frames]) for k in keys}
        pack["lat"], pack["lon"] = frames[0]["lat"], frames[0]["lon"]
        pack["valid"] = np.array([f["valid"] for f in frames])
        pack["cycle"] = np.datetime64(cycle)
        np.savez_compressed(path, **pack)
        ws = np.hypot(pack["UGRD_agl100"], pack["VGRD_agl100"])
        print(f"{path.name}: {len(frames)}시각, 100 m 풍속 {ws.min():.1f}–{ws.max():.1f} m/s", flush=True)


if __name__ == "__main__":
    site, days = sys.argv[1], sys.argv[2:]
    with cf.ProcessPoolExecutor(10) as ex:
        for d in days:
            fetch(site, d, ex)
