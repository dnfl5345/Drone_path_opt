"""기상청(KMA) 바람 정보 어댑터 – GFS 로더와 동일한 dict 포맷으로 변환.

지원 API (공공데이터포털 data.go.kr, 서비스키 필요: 환경변수 KMA_SERVICE_KEY)
- 초단기예보  VilageFcstInfoService_2.0/getUltraSrtFcst : 1시간 간격, 6시간, 5 km 격자
- 단기예보    VilageFcstInfoService_2.0/getVilageFcst    : 1시간 간격, 3일,   5 km 격자
  사용 항목: UUU(동서성분, m/s), VVV(남북성분, m/s), VEC(풍향), WSD(풍속)  ※ 지상 10 m
- (참고) 기상청 API허브 apihub.kma.go.kr : LDAPS 국지예보모델(1.5 km, 연직 다층 바람),
  AWS 매분 관측, 윈드프로파일러(고도별 실측 바람) – 3D 바람장 고도화 시 권장

연직 확장: 10 m 바람만 제공되므로 멱법칙 U(z)=U10·(z/10)^α (도시 α≈0.25, 개활지 0.14)
           또는 GFS 프로파일 '형상'에 KMA 10 m 값을 맞추는 하이브리드 방식 지원.

※ 본 개발 환경은 네트워크 정책상 data.go.kr 접근이 차단되어 미검증 상태이며,
  좌표변환(dfs_xy) 등 오프라인 로직만 단위 테스트되었다.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta

import numpy as np

BASE = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"


def dfs_xy(lat, lon):
    """위경도 → 기상청 동네예보 격자(nx, ny) 변환 (Lambert Conformal Conic, 기상청 공식 파라미터)."""
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re = RE / GRID
    s1, s2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi * 0.25 + s2 * 0.5) /
                                                          math.tan(math.pi * 0.25 + s1 * 0.5))
    sf = math.tan(math.pi * 0.25 + s1 * 0.5) ** sn * math.cos(s1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re * sf / math.tan(math.pi * 0.25 + lat * D * 0.5) ** sn
    theta = lon * D - olon
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return int(math.floor(ra * math.sin(theta) + XO + 0.5)), int(math.floor(ro - ra * math.cos(theta) + YO + 0.5))


def _base_time_ultra(now: datetime):
    t = now - timedelta(minutes=45)  # 매시 30분 생성, 45분 이후 제공
    return t.strftime("%Y%m%d"), t.strftime("%H30")


def fetch_ultra_short(lat, lon, now: datetime | None = None, key: str | None = None):
    """초단기예보 6시간 UUU/VVV → [(valid_time, u10, v10)]."""
    import requests

    key = key or os.environ["KMA_SERVICE_KEY"]
    now = now or datetime.now()
    nx, ny = dfs_xy(lat, lon)
    d, t = _base_time_ultra(now)
    r = requests.get(f"{BASE}/getUltraSrtFcst", timeout=30, params=dict(
        serviceKey=key, pageNo=1, numOfRows=1000, dataType="JSON", base_date=d, base_time=t, nx=nx, ny=ny))
    r.raise_for_status()
    items = r.json()["response"]["body"]["items"]["item"]
    rows = {}
    for it in items:
        if it["category"] in ("UUU", "VVV"):
            vt = datetime.strptime(it["fcstDate"] + it["fcstTime"], "%Y%m%d%H%M")
            rows.setdefault(vt, {})[it["category"]] = float(it["fcstValue"])
    return [(vt, v["UUU"], v["VVV"]) for vt, v in sorted(rows.items()) if "UUU" in v and "VVV" in v]


def to_profile_pack(series, levels_agl=(10, 20, 30, 40, 50, 80, 100), alpha=0.25, top=(975, 300.0),
                    gfs_shape: dict | None = None, lat=37.4, lon=127.1):
    """KMA 10 m 바람 시계열 → wind_field.Site3D 가 읽는 GFS 형식 dict.

    gfs_shape 가 주어지면 같은 시각 GFS 프로파일의 (U(z)/U10) 비율을 사용(하이브리드),
    없으면 멱법칙 (z/10)^α 사용. 공간적으로는 균일(5 km 격자 1점)로 2x2 격자에 복제.
    """
    nt = len(series)
    glat = np.array([lat + 0.25, lat - 0.25], np.float32)
    glon = np.array([lon - 0.25, lon + 0.25], np.float32)
    pack = {"lat": glat, "lon": glon,
            "valid": np.array([np.datetime64(vt - timedelta(hours=9)) for vt, _, _ in series])}  # KST→UTC
    for lv in levels_agl:
        fac = (lv / 10.0) ** alpha
        pack[f"UGRD_agl{lv}"] = np.array([np.full((2, 2), u * fac, np.float32) for _, u, _ in series])
        pack[f"VGRD_agl{lv}"] = np.array([np.full((2, 2), v * fac, np.float32) for _, _, v in series])
    p, h = top
    fac = (h / 10.0) ** alpha
    for pp, hh in ((975, h), (950, h + 200.0)):
        f2 = (hh / 10.0) ** alpha
        pack[f"UGRD_p{pp}"] = np.array([np.full((2, 2), u * f2, np.float32) for _, u, _ in series])
        pack[f"VGRD_p{pp}"] = np.array([np.full((2, 2), v * f2, np.float32) for _, _, v in series])
        pack[f"HGT_p{pp}"] = np.full((nt, 2, 2), hh, np.float32)
    pack["HGT_sfc"] = np.zeros((nt, 2, 2), np.float32)
    if gfs_shape is not None:
        raise NotImplementedError("하이브리드 형상 보정: GFS U(z)/U10 비율 적용 – 운영 단계 구현 예정")
    return pack


if __name__ == "__main__":
    # 오프라인 단위 테스트: 서울시청 좌표 → 격자 (60,127) (기상청 격자 안내 예시값)
    assert dfs_xy(37.5665, 126.9780) == (60, 127), dfs_xy(37.5665, 126.9780)
    print("Pangyo grid:", dfs_xy(37.40, 127.11))
