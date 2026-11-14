"""대상 지역의 지도·바람 자료를 받아 격자를 만든다.

사용: PYTHONPATH=src python scripts/build_site.py changwon
- 지형: Copernicus GLO-30 DSM 타일 (data/raw/*.tif, 저장소에는 넣지 않음)
- 건물: Overture Maps buildings (data/raw/overture_buildings*.json, 저장소에는 넣지 않음)
- 바람: NOAA GFS 0.25° 00Z(계획용)·06Z(평가용) (data/raw/gfs_*.npz)
- 결과: data/processed/<grid>.npz
"""
from __future__ import annotations

import sys
from datetime import datetime

import numpy as np

from dpo import config as C


def main(key):
    S = C.use_site(key)
    from dpo.data_terrain import build_site_grid, site_frame
    from dpo.data_wind import fetch_gfs_series
    fr = site_frame()
    print(f"[{key}] {S['label']}  격자 {fr['nx']}×{fr['ny']} ({fr['nx'] * C.DX / 1000:.1f}×{fr['ny'] * C.DX / 1000:.1f} km)", flush=True)
    g = build_site_grid()
    print(f"  지면 {g['ground'].min():.0f}–{g['ground'].max():.0f} m, 건물 {int(g['n_buildings'])}동 (최고 {g['bldg_h'].max():.0f} m), "
          f"건물이 있는 셀 {(g['bldg_h'] > 0).mean() * 100:.1f} %", flush=True)
    t0 = datetime.fromisoformat(S["t0"])
    for name, cyc, fhs in ((S["gfs"], t0, range(0, 13)), (S["gfs_truth"], t0.replace(hour=6), range(0, 7))):
        tag = name[len("gfs_"):-len(".npz")]
        d = fetch_gfs_series(cyc, list(fhs), tag)
        ws = np.hypot(d["UGRD_agl100"], d["VGRD_agl100"])
        print(f"  바람 {name}: {len(d['valid'])}시각, 격자 lat {d['lat'].min()}–{d['lat'].max()} lon {d['lon'].min()}–{d['lon'].max()}, "
              f"100 m 풍속 {ws.min():.1f}–{ws.max():.1f} m/s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "changwon")
