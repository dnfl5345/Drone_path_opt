"""공통 설정: 대상 지역, 격자, 데이터 경로."""
import os as _os
from pathlib import Path


def _find_root():
    """자료 폴더의 위치. 우선순위: 환경변수 DPO_HOME → 소스 저장소(개발 설치) → 현재 폴더의 dpo_data (자료 묶음을 푼 폴더)
    → 현재 폴더.

    그 아래에 data/processed (지형 격자), data/raw (예보 바람), results (실측 오차 모델 등), scenarios 가 있어야 한다."""
    env = _os.environ.get("DPO_HOME")
    if env:
        return Path(env).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if (repo / "data" / "processed").is_dir():
        return repo
    bundle = Path.cwd() / "dpo_data"
    if (bundle / "data" / "processed").is_dir():
        return bundle.resolve()
    return Path.cwd()


ROOT = _find_root()
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
FIG = ROOT / "results" / "figures"
RES = ROOT / "results"
if (ROOT / "data").is_dir():           # 자료 폴더가 아닌 곳(자료를 못 찾은 경우)에 빈 폴더를 만들지 않는다
    for _p in (RAW, PROC, FIG, RES):
        try:
            _p.mkdir(parents=True, exist_ok=True)
        except OSError:                # 읽기 전용 설치 위치 – 읽기만 하는 운용에는 문제없다
            pass


def check_data(site_key=None):
    """대상 지역의 필수 자료가 있는지 확인한다. 반환: 없는 파일 목록 (비어 있으면 정상)."""
    S = SITES[site_key or SITE_KEY]
    need = [PROC / S["grid"], RAW / S["gfs"]]
    return [str(p) for p in need if not p.exists()]

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
BUILTIN_SITES = tuple(SITES)
USER_SITES_DIR = ROOT / "sites"          # 사용자가 만든 지역 (dpo-site create, 3D 화면 '새 지역') – 지역마다 <key>.json


def _load_user_sites():
    import json as _json
    if not USER_SITES_DIR.is_dir():
        return
    for f in sorted(USER_SITES_DIR.glob("*.json")):
        try:
            d = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d["lon"], d["lat"] = tuple(d["lon"]), tuple(d["lat"])
        d.setdefault("nfz", [])
        d.setdefault("tour", None)
        d["user"] = True
        SITES[f.stem] = d


def register_site(key: str, site: dict):
    """사용자 지역을 등록(저장)한다. 같은 key 가 있으면 덮어쓴다 (기본 지역은 못 바꿈)."""
    import json as _json
    if key in BUILTIN_SITES:
        raise ValueError(f"기본 지역은 바꿀 수 없습니다: {key}")
    USER_SITES_DIR.mkdir(parents=True, exist_ok=True)
    d = {k: (list(v) if isinstance(v, tuple) else v) for k, v in site.items() if k != "user"}
    (USER_SITES_DIR / f"{key}.json").write_text(_json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    site = dict(site, user=True)
    site["lon"], site["lat"] = tuple(site["lon"]), tuple(site["lat"])
    SITES[key] = site
    return site


def dsm_tile_name(lat_floor: int, lon_floor: int) -> str:
    """Copernicus GLO-30 타일 이름 (1° × 1°, 남서쪽 모서리 기준). 예: N37_00_E127_00"""
    ns, ew = ("N" if lat_floor >= 0 else "S"), ("E" if lon_floor >= 0 else "W")
    return f"{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00"


def dsm_url(tile: str) -> str:
    return f"https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{tile}_DEM/Copernicus_DSM_COG_10_{tile}_DEM.tif"


def dsm_file(tile: str) -> str:
    a, b = tile.split("_00_")
    return f"cop_dsm_{a}_{b.replace('_00', '')}.tif"


_load_user_sites()
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
    COP_URL = dsm_url(SITE["dsm_tile"]) if SITE.get("dsm_tile") else None
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

use_site(_os.environ.get("DPO_SITE", "pangyo"))
