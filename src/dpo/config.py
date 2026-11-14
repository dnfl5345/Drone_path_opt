"""공통 설정: 대상 지역, 격자, 데이터 경로."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
FIG = ROOT / "results" / "figures"
RES = ROOT / "results"
for _p in (RAW, PROC, FIG, RES):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 대상 지역
# 지역을 바꾸려면 환경변수 DPO_SITE=changwon 또는 use_site("changwon") (플랫폼: python -m dpo.webapp --site changwon)
# hub/dst/tour 는 지역 좌표 (x 동쪽, y 북쪽 [m], 원점 = 남서쪽 모서리). nfz = [(x, y, 반경)] 예시 금지구역.
SITES = {
    "pangyo": dict(
        label="성남 판교·분당", name="Seongnam Pangyo-Yatap (KR)",
        lon=(127.075, 127.145), lat=(37.375, 37.425),             # 약 6.2 km x 5.6 km
        dsm_tile="N37_00_E127_00", dsm_file="cop_dsm_N37_E127.tif", grid="site_grid.npz",
        buildings="overture_buildings.json", gfs="gfs_20261027_00z.npz", gfs_truth="gfs_20261027_06z.npz",
        t0="2026-10-27T00:00", date="2026-10-27 (GFS 00Z 예보)",
        hub=(2700.0, 3000.0), dst=(4600.0, 1500.0), tour=[], nfz=[(3650.0, 2250.0, 250.0)], hub_name="물류 거점"),
    "changwon": dict(
        # 창원시 의창구 창이대로 71 (창원시농업기술센터, 명서동) – Overture Places 좌표 128.64658E, 35.23923N 을 중심으로 4.4 x 4.0 km
        label="창원 창이대로 71 주변", name="Changwon Changi-daero 71 (KR)",
        lon=(128.6224, 128.6708), lat=(35.2212, 35.2572),
        dsm_tile="N35_00_E128_00", dsm_file="cop_dsm_N35_E128.tif", grid="site_grid_changwon.npz",
        buildings="overture_buildings_changwon.json", gfs="gfs_changwon_20261027_00z.npz",
        gfs_truth="gfs_changwon_20261027_06z.npz", t0="2026-10-27T00:00", date="2026-10-27 (GFS 00Z 예보)",
        anchor=(128.64658, 35.23923), hub=None, dst=None, tour=None, nfz=[], hub_name="창이대로 71"),
}
SITE_KEY = None
SITE = None
SITE_NAME = None
LON_MIN = LON_MAX = LAT_MIN = LAT_MAX = None
COP_URL = None


def use_site(key: str):
    """대상 지역을 바꾼다. 지형·바람 격자를 만들기 전에 불러야 한다."""
    global SITE_KEY, SITE, SITE_NAME, LON_MIN, LON_MAX, LAT_MIN, LAT_MAX, COP_URL
    if key not in SITES:
        raise ValueError(f"알 수 없는 지역: {key} (가능: {', '.join(SITES)})")
    SITE_KEY, SITE = key, SITES[key]
    SITE_NAME = SITE["name"]
    (LON_MIN, LON_MAX), (LAT_MIN, LAT_MAX) = SITE["lon"], SITE["lat"]
    t = SITE["dsm_tile"]
    COP_URL = f"https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{t}_DEM/Copernicus_DSM_COG_10_{t}_DEM.tif"
    return SITE


# 수평 격자 해상도 [m]
DX = 25.0

# 비행 고도 레이어 (지면 기준 AGL이 아닌 '평균해발 MSL' 레이어) [m]
Z_STEP = 20.0
AGL_MIN = 30.0        # 최소 이격 (지면/건물 위)  [m]
AGL_MAX = 150.0       # 항공안전법 상 150 m AGL 제한 (승인 없이)  [m]
BUILDING_CLEARANCE = 20.0  # 건물 최상단 이격 [m]

# GFS (NOAA Global Forecast System, 0.25 deg) – 실제 예보자료
GFS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GFS_LEVELS_AGL = [10, 20, 30, 40, 50, 80, 100]
GFS_LEVELS_P = [975, 950]   # hPa (~300 m, ~500 m)

# Overture Maps buildings
OVERTURE_RELEASE = "2026-10-21.0"
OVERTURE_BASE = "https://overturemaps-us-west-2.s3.amazonaws.com"

import os as _os  # noqa: E402

use_site(_os.environ.get("DPO_SITE", "pangyo"))
