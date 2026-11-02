"""공통 설정: 대상 지역, 격자, 데이터 경로."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
FIG = ROOT / "results" / "figures"
RES = ROOT / "results"
for _p in (RAW, PROC, FIG, RES):
    _p.mkdir(parents=True, exist_ok=True)

# 대상 지역: 경기 성남시 분당구 판교·야탑 일대 (약 6.2 km x 5.6 km)
SITE_NAME = "Seongnam Pangyo-Yatap (KR)"
LON_MIN, LON_MAX = 127.075, 127.145
LAT_MIN, LAT_MAX = 37.375, 37.425

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

# Copernicus GLO-30 DSM
COP_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/"
           "Copernicus_DSM_COG_10_N37_00_E127_00_DEM/"
           "Copernicus_DSM_COG_10_N37_00_E127_00_DEM.tif")

# Overture Maps buildings
OVERTURE_RELEASE = "2026-10-21.0"
OVERTURE_BASE = "https://overturemaps-us-west-2.s3.amazonaws.com"
