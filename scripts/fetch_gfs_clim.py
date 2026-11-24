"""E16 용 GFS 표본: 12개월 × 3일(5·15·25일)을 두 지역에 대해 받는다.

날짜마다 받는 것
  ① 00Z f000–f012  계획용 예보 (KST 9–21시)          → data/raw/gfs_<site>_<날짜>_00z.npz
  ② 06Z f000–f006  나중에 발표된 '실제' 대용 (KST 15–21시) → data/raw/gfs_<site>_<날짜>_06z.npz
  ③ 같은 시각(그날 06Z = 15시 KST)을 맞히는 더 오래된 예보: (D−1) 00Z f030, (D−2) 00Z f054, (D−3) 00Z f078
                                                        → data/raw/gfsage_<site>_<날짜>_lead030.npz 등
①②는 scripts/fetch_gfs_days.py 와 같은 이름·형식이라 exp_superiority._load_day 가 그대로 읽는다.
GRIB 메시지는 한 번만 내려받아 두 지역을 각각 잘라 쓴다 (지역 좌표를 다른 서비스로 보내지 않는다).
예보 시각 하나를 받을 때마다 캐시(E16_CACHE)에 적어, 중간에 끊겨도 이어서 받는다.

사용: PYTHONPATH=src python scripts/fetch_gfs_clim.py            # 전체 표본
      PYTHONPATH=src python scripts/fetch_gfs_clim.py 20260115   # 특정 날짜만
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, "scripts")
from fetch_gfs_days import gfs_name  # noqa: E402

from dpo import config as C  # noqa: E402

SITES = ["changwon", "pangyo"]
PAD = 0.5
AGE_LEADS = [30, 54, 78]


def sample_days():
    """2025-11 … 2026-10 의 5·15·25일 (36일)."""
    out = []
    y, m = 2025, 11
    for _ in range(12):
        out += [f"{y}{m:02d}{d:02d}" for d in (5, 15, 25)]
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fetch_hour_multi(args):
    """한 예보 시각의 U/V(고도별)·HGT 를 내려받아 지역별로 잘라 돌려준다. 실패하면 None."""
    ymd, hh, fh = args
    try:
        import pygrib
        from dpo.data_wind import _get, _idx, _want
        url = f"{C.GFS_BUCKET}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fh:03d}"
        out = {s: {} for s in SITES}
        hgt = [f"{p} mb" for p in C.GFS_LEVELS_P]
        want = []
        for var, lev, s, e in _idx(url):
            w = _want(var, lev)
            if w is not None:
                key = f"{var}_{w[0]}{w[1]}"
            elif var == "HGT" and lev in hgt:
                key = f"HGT_p{lev.split()[0]}"
            elif var == "HGT" and lev == "surface":
                key = "HGT_sfc"
            else:
                continue
            want.append((key, f"bytes={s}-{e}" if e else f"bytes={s}-"))
        with cf.ThreadPoolExecutor(6) as tp:           # 메시지(약 21개)를 동시에 내려받는다
            blobs = list(tp.map(lambda kr: _get(url, headers={"Range": kr[1]}).content, want))
        for (key, _), b in zip(want, blobs):
            with tempfile.NamedTemporaryFile(suffix=".grb2") as tf:
                tf.write(b)
                tf.flush()
                g = pygrib.open(tf.name).message(1)
                for site in SITES:
                    S = C.SITES[site]
                    data, lats, lons = g.data(lat1=S["lat"][0] - PAD, lat2=S["lat"][1] + PAD,
                                              lon1=S["lon"][0] - PAD, lon2=S["lon"][1] + PAD)
                    out[site][key] = data.astype(np.float32)
                    out[site]["lat"], out[site]["lon"] = lats[:, 0].astype(np.float32), lons[0].astype(np.float32)
        cyc = datetime.strptime(ymd + hh, "%Y%m%d%H")
        for site in SITES:
            out[site]["valid"] = np.datetime64(cyc + timedelta(hours=fh))
        return (ymd, hh, fh), out
    except Exception as ex:  # noqa
        return (ymd, hh, fh), f"{type(ex).__name__}: {ex}"


def _pack(frames, cycle):
    keys = [k for k in frames[0] if k not in ("lat", "lon", "valid")]
    pack = {k: np.stack([f[k] for f in frames]) for k in keys}
    pack["lat"], pack["lon"] = frames[0]["lat"], frames[0]["lon"]
    pack["valid"] = np.array([f["valid"] for f in frames])
    pack["cycle"] = np.datetime64(cycle)
    return pack


def _cache_dir():
    """예보 시각 하나를 받을 때마다 여기에 적어 둔다 (중간에 끊겨도 이어서 받는다). 환경변수 E16_CACHE 로 바꿀 수 있다."""
    import os
    from pathlib import Path
    p = Path(os.environ.get("E16_CACHE") or (Path(tempfile.gettempdir()) / "dpo_e16_cache"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cpath(t):
    return _cache_dir() / f"{t[0]}_{t[1]}_f{t[2]:03d}.npz"


def fetch_cached(t):
    """fetch_hour_multi 를 부르고 결과를 캐시에 적는다. 반환: (작업, None | 오류 문자열)."""
    p = _cpath(t)
    if p.exists():
        return t, None
    key, res = fetch_hour_multi(t)
    if isinstance(res, str):
        return t, res
    flat = {f"{site}|{k}": v for site, d in res.items() for k, v in d.items()}
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **flat)
    tmp.rename(p)
    return t, None


def _load_cached(t, site):
    z = np.load(_cpath(t), allow_pickle=True)
    return {k.split("|", 1)[1]: z[k] for k in z.files if k.startswith(site + "|")}


def main(days, workers=4):
    t0 = time.time()
    groups = {}                                   # 파일 하나 = (종류, 날짜, ...) → 필요한 (ymd, hh, fh) 목록
    for d in days:
        D = datetime.strptime(d, "%Y%m%d")
        for cyc, fhs in (("00", range(0, 13)), ("06", range(0, 7))):
            if not all((C.RAW / gfs_name(s, d, cyc)).exists() for s in SITES):
                groups[("day", d, cyc)] = [(d, cyc, fh) for fh in fhs]
        for lead in AGE_LEADS:
            if not all((C.RAW / f"gfsage_{s}_{d}_lead{lead:03d}.npz").exists() for s in SITES):
                c0 = D + timedelta(hours=6) - timedelta(hours=lead)
                groups[("age", d, lead)] = [(c0.strftime("%Y%m%d"), c0.strftime("%H"), lead)]
    tasks = sorted({t for g in groups.values() for t in g})
    todo = [t for t in tasks if not _cpath(t).exists()]
    print(f"받을 파일 {len(groups)}개, 예보 시각 {len(tasks)}개 (캐시에 {len(tasks) - len(todo)}개)", flush=True)
    failed = {}
    for attempt in range(6):                      # 작업 프로세스가 죽으면(메모리 부족 등) 풀을 다시 만들어 이어 받는다
        todo = [t for t in tasks if not _cpath(t).exists()]
        if not todo:
            break
        failed = {}
        try:
            with cf.ProcessPoolExecutor(workers) as ex:
                for n, (t, err) in enumerate(ex.map(fetch_cached, todo, chunksize=1)):
                    if err:
                        failed["/".join(map(str, t))] = err
                    if (n + 1) % 100 == 0:
                        print(f"  {n + 1}/{len(todo)} ({time.time() - t0:.0f} s, 실패 {len(failed)})", flush=True)
        except cf.process.BrokenProcessPool:
            print(f"  작업 프로세스가 중단됨 – 다시 시도 ({attempt + 1})", flush=True)
            time.sleep(5)
    saved, skipped = [], []
    for g, need in groups.items():
        if not all(_cpath(t).exists() for t in need):
            skipped.append("/".join(map(str, g)))
            continue
        for site in SITES:
            if g[0] == "day":
                path = C.RAW / gfs_name(site, g[1], g[2])
                cycle = datetime.strptime(g[1] + g[2], "%Y%m%d%H")
            else:
                path = C.RAW / f"gfsage_{site}_{g[1]}_lead{g[2]:03d}.npz"
                cycle = datetime.strptime(need[0][0] + need[0][1], "%Y%m%d%H")
            if not path.exists():
                np.savez_compressed(path, **_pack([_load_cached(t, site) for t in need], cycle))
                saved.append(path.name)
    log = dict(days=days, n_files_saved=len(saved), skipped=skipped, failed=failed, seconds=round(time.time() - t0))
    (C.RES / "E16_fetch_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
    print(f"저장 {len(saved)}개, 건너뜀 {len(skipped)}개, 실패한 시각 {len(failed)}개, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    import os
    main(sys.argv[1:] or sample_days(), workers=int(os.environ.get("E16_WORKERS", 4)))
