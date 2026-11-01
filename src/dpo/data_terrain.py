"""실제 지형(Copernicus GLO-30 DSM) + 건물(Overture Maps) 수집 및 격자화.

- DSM : Copernicus GLO-30 (30 m, 지표면 모델 = 지형 + 건물/수목 포함)
- 건물 : Overture Maps Foundation buildings (OSM 등 출처, footprint + height)
출력 : data/processed/site_grid.npz
   ground  : 추정 지면고(DTM) [m MSL]
   surface : 장애물 상면고 = max(DSM, 지면 + 건물높이) [m MSL]
   bldg_h  : 건물높이 [m]
   lon, lat: 격자 중심 좌표
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math

import numpy as np
import requests

from . import config as C


# ---------------------------------------------------------------- 좌표 변환
def site_frame():
    """국소 ENU 평면(m) <-> 경위도 변환 파라미터."""
    lat0 = 0.5 * (C.LAT_MIN + C.LAT_MAX)
    m_per_deg_lat = 111_132.92 - 559.82 * math.cos(2 * math.radians(lat0))
    m_per_deg_lon = 111_412.84 * math.cos(math.radians(lat0))
    W = (C.LON_MAX - C.LON_MIN) * m_per_deg_lon
    H = (C.LAT_MAX - C.LAT_MIN) * m_per_deg_lat
    nx = int(W // C.DX)
    ny = int(H // C.DX)
    return dict(lat0=lat0, mlat=m_per_deg_lat, mlon=m_per_deg_lon, nx=nx, ny=ny)


def ll2xy(lon, lat, fr=None):
    fr = fr or site_frame()
    return (np.asarray(lon) - C.LON_MIN) * fr["mlon"], (np.asarray(lat) - C.LAT_MIN) * fr["mlat"]


def xy2ll(x, y, fr=None):
    fr = fr or site_frame()
    return C.LON_MIN + np.asarray(x) / fr["mlon"], C.LAT_MIN + np.asarray(y) / fr["mlat"]


# ---------------------------------------------------------------- DSM
def fetch_dsm():
    out = C.RAW / "cop_dsm_N37_E127.tif"
    if not out.exists():
        r = requests.get(C.COP_URL, timeout=300)
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


def dsm_on_grid(fr):
    import rasterio
    from scipy.ndimage import map_coordinates

    path = fetch_dsm()
    with rasterio.open(path) as ds:
        inv = ~ds.transform
        xs = (np.arange(fr["nx"]) + 0.5) * C.DX
        ys = (np.arange(fr["ny"]) + 0.5) * C.DX
        X, Y = np.meshgrid(xs, ys)
        lon, lat = xy2ll(X, Y, fr)
        col, row = inv * (lon, lat)
        # 필요한 창만 읽기
        r0, r1 = int(np.floor(row.min())) - 2, int(np.ceil(row.max())) + 3
        c0, c1 = int(np.floor(col.min())) - 2, int(np.ceil(col.max())) + 3
        arr = ds.read(1, window=((r0, r1), (c0, c1))).astype(np.float64)
        z = map_coordinates(arr, [row - r0 - 0.5, col - c0 - 0.5], order=1, mode="nearest")
    return z, lon, lat


# ---------------------------------------------------------------- Overture buildings
def _overture_files():
    prefix = f"release/{C.OVERTURE_RELEASE}/theme=buildings/type=building/"
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        t = requests.get(C.OVERTURE_BASE + "/", params=params, timeout=60).text
        import re
        keys += re.findall(r"<Key>([^<]+\.parquet)</Key>", t)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", t)
        if not m:
            break
        token = m.group(1)
    return keys


def _row_groups_in_bbox(key, bbox):
    import fsspec
    import pyarrow.parquet as pq

    fs = fsspec.filesystem("http")
    url = f"{C.OVERTURE_BASE}/{key}"
    xmin, ymin, xmax, ymax = bbox
    hits = []
    with fs.open(url, block_size=2 ** 20) as f:
        md = pq.ParquetFile(f).metadata
        names = [md.row_group(0).column(i).path_in_schema for i in range(md.num_columns)]
        ix = {n: i for i, n in enumerate(names)}
        for g in range(md.num_row_groups):
            rg = md.row_group(g)
            sx0 = rg.column(ix["bbox.xmin"]).statistics
            sx1 = rg.column(ix["bbox.xmax"]).statistics
            sy0 = rg.column(ix["bbox.ymin"]).statistics
            sy1 = rg.column(ix["bbox.ymax"]).statistics
            if sx0.min <= xmax and sx1.max >= xmin and sy0.min <= ymax and sy1.max >= ymin:
                hits.append(g)
    return key, hits


def fetch_buildings():
    """bbox 안 건물 footprint/높이를 parquet 원격 range read로 추출."""
    out = C.RAW / "overture_buildings.json"
    if out.exists():
        return json.loads(out.read_text())
    import fsspec
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    from shapely import wkb

    bbox = (C.LON_MIN, C.LAT_MIN, C.LON_MAX, C.LAT_MAX)
    keys = _overture_files()
    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda k: _row_groups_in_bbox(k, bbox), keys))
    res = [(k, g) for k, g in res if g]
    feats = []
    fs = fsspec.filesystem("http")
    for key, groups in res:
        with fs.open(f"{C.OVERTURE_BASE}/{key}", block_size=8 * 2 ** 20) as f:
            pf = pq.ParquetFile(f)
            tb = pf.read_row_groups(groups, columns=["id", "height", "num_floors", "class", "geometry", "bbox"])
        bx = tb.column("bbox")
        m = pc.and_(pc.and_(pc.less_equal(pc.struct_field(bx, "xmin"), bbox[2]),
                            pc.greater_equal(pc.struct_field(bx, "xmax"), bbox[0])),
                    pc.and_(pc.less_equal(pc.struct_field(bx, "ymin"), bbox[3]),
                            pc.greater_equal(pc.struct_field(bx, "ymax"), bbox[1])))
        tb = tb.filter(m)
        for h, nf, cl, g in zip(tb.column("height").to_pylist(), tb.column("num_floors").to_pylist(),
                                tb.column("class").to_pylist(), tb.column("geometry").to_pylist()):
            geom = wkb.loads(g)
            feats.append(dict(h=h, nf=nf, cls=cl, wkt=geom.wkt))
    out.write_text(json.dumps(feats))
    return feats


def building_height_grid(feats, fr):
    """건물 높이 래스터화 (높이 결측 시: 층수 x 3.3 m, 그것도 없으면 클래스별 기본값)."""
    from shapely import wkt as swkt
    from matplotlib.path import Path as MPath

    H = np.zeros((fr["ny"], fr["nx"]))
    src = np.zeros((fr["ny"], fr["nx"]), dtype=np.int8)  # 0 없음 1 실측높이 2 층수추정 3 기본값
    xs = (np.arange(fr["nx"]) + 0.5) * C.DX
    ys = (np.arange(fr["ny"]) + 0.5) * C.DX
    default_h = {"apartments": 60.0, "residential": 12.0, "house": 8.0, "commercial": 30.0,
                 "office": 40.0, "industrial": 12.0, "school": 15.0}
    for f in feats:
        if f["h"]:
            h, s = float(f["h"]), 1
        elif f["nf"]:
            h, s = float(f["nf"]) * 3.3, 2
        else:
            h, s = default_h.get(f["cls"] or "", 10.0), 3
        g = swkt.loads(f["wkt"])
        polys = getattr(g, "geoms", [g])
        for p in polys:
            lon, lat = np.asarray(p.exterior.coords).T
            x, y = ll2xy(lon, lat, fr)
            i0 = max(int(np.floor(x.min() / C.DX)) - 1, 0)
            i1 = min(int(np.ceil(x.max() / C.DX)) + 1, fr["nx"])
            j0 = max(int(np.floor(y.min() / C.DX)) - 1, 0)
            j1 = min(int(np.ceil(y.max() / C.DX)) + 1, fr["ny"])
            if i0 >= i1 or j0 >= j1:
                continue
            XX, YY = np.meshgrid(xs[i0:i1], ys[j0:j1])
            mp = MPath(np.c_[x, y])
            inside = mp.contains_points(np.c_[XX.ravel(), YY.ravel()], radius=C.DX * 0.5).reshape(XX.shape)
            if not inside.any():  # 셀보다 작은 건물: 중심 셀에 할당
                cx, cy = int(x.mean() // C.DX), int(y.mean() // C.DX)
                if 0 <= cx < fr["nx"] and 0 <= cy < fr["ny"]:
                    if h > H[cy, cx]:
                        H[cy, cx], src[cy, cx] = h, s
                continue
            sub = H[j0:j1, i0:i1]
            upd = inside & (h > sub)
            sub[upd] = h
            src[j0:j1, i0:i1][upd] = s
    return H, src


def build_site_grid(force=False):
    out = C.PROC / "site_grid.npz"
    if out.exists() and not force:
        return dict(np.load(out))
    from scipy.ndimage import grey_opening, gaussian_filter

    fr = site_frame()
    dsm, lon, lat = dsm_on_grid(fr)
    feats = fetch_buildings()
    bh, bsrc = building_height_grid(feats, fr)
    # DSM -> 지면(DTM) 추정: 건물·수목 제거용 morphological opening (5x5 = 125 m)
    ground = grey_opening(dsm, size=(5, 5))
    ground = gaussian_filter(ground, 1.0)
    ground = np.minimum(ground, dsm)
    surface = np.maximum(dsm, ground + bh)
    np.savez_compressed(out, ground=ground, dsm=dsm, surface=surface, bldg_h=bh, bldg_src=bsrc,
                        lon=lon, lat=lat, dx=C.DX, n_buildings=len(feats))
    return dict(np.load(out))


if __name__ == "__main__":
    g = build_site_grid(force=True)
    print({k: (v.shape if hasattr(v, "shape") else v) for k, v in g.items()})
    print("ground", g["ground"].min(), g["ground"].max(), "bldg max", g["bldg_h"].max(),
          "n_bldg", g["n_buildings"])
