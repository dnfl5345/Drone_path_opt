"""새 지역 만들기(dpo.regions)의 인터넷 없이 확인할 수 있는 부분."""
import json
import math
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from dpo import config as C
from dpo import regions as RG


def test_parse_latlon():
    assert RG.parse_latlon("37.795, 128.896") == (37.795, 128.896)
    assert RG.parse_latlon("N37.5 E127.1") == (37.5, 127.1)
    assert RG.parse_latlon("35.1 129.04") == (35.1, 129.04)
    assert RG.parse_latlon("S33.9, E151.2") == (-33.9, 151.2)
    assert RG.parse_latlon("강릉 경포대") is None
    assert RG.parse_latlon("95, 10") is None


def test_site_dict_size_and_tiles():
    S = RG.site_dict("rtest", "시험", 37.0, 127.0, 4.0, datetime(2026, 11, 22))
    (lo0, lo1), (la0, la1) = S["lon"], S["lat"]
    mlat = 111_132.92 - 559.82 * math.cos(2 * math.radians(37.0))
    mlon = 111_412.84 * math.cos(math.radians(37.0))
    assert abs((la1 - la0) * mlat - 4000) < 5 and abs((lo1 - lo0) * mlon - 4000) < 5
    # 1° 경계(37°N, 127°E)에 걸친 지역은 타일 4개
    assert sorted(S["dsm_tiles"]) == sorted(["N36_00_E126_00", "N36_00_E127_00", "N37_00_E126_00", "N37_00_E127_00"])
    assert S["gfs"] == "gfs_rtest_20261122_00z.npz" and S["t0"] == "2026-11-22T00:00" and S["gfs_truth"] is None
    with pytest.raises(ValueError):
        RG.site_dict("x", "x", 37.0, 127.0, 20.0, datetime(2026, 11, 22))


def test_tile_names():
    assert C.dsm_tile_name(37, 127) == "N37_00_E127_00"
    assert C.dsm_tile_name(-34, -59) == "S34_00_W059_00"
    assert C.dsm_file("N37_00_E127_00") == "cop_dsm_N37_E127.tif"


def test_register_and_reload_user_site(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "USER_SITES_DIR", tmp_path / "sites")
    S = RG.site_dict("rtest2", "시험2", 35.5, 128.5, 3.0, datetime(2026, 11, 22))
    try:
        C.register_site("rtest2", S)
        assert (tmp_path / "sites" / "rtest2.json").exists()
        C.SITES.pop("rtest2")
        C._load_user_sites()
        assert C.SITES["rtest2"]["label"] == "시험2" and C.SITES["rtest2"]["user"] is True
        assert isinstance(C.SITES["rtest2"]["lon"], tuple)
        with pytest.raises(ValueError):
            C.register_site("pangyo", S)
    finally:
        C.SITES.pop("rtest2", None)


def test_geocode_coordinates_without_network():
    r = RG.geocode("37.795, 128.896")
    assert r[0]["lat"] == 37.795 and r[0]["source"] == "입력"


def test_geocode_nominatim_format(monkeypatch):
    """Nominatim 형식 응답을 읽는다 (가짜 검색 서버)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps([{"display_name": "경포해변, 강릉시", "lat": "37.8055", "lon": "128.9083", "type": "beach"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DPO_GEOCODER_URL", f"http://127.0.0.1:{srv.server_port}/search")
    monkeypatch.delenv("DPO_VWORLD_KEY", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        r = RG.geocode("경포해변")
        assert r[0]["name"].startswith("경포해변") and abs(r[0]["lat"] - 37.8055) < 1e-9 and r[0]["source"] == "OpenStreetMap"
    finally:
        srv.shutdown()


def test_geocode_failure_message(monkeypatch):
    monkeypatch.setenv("DPO_GEOCODER_URL", "http://127.0.0.1:9/search")      # 닫힌 포트
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with pytest.raises(ConnectionError, match="위도, 경도"):
        RG.geocode("어딘가")


def test_dsm_multi_tile_matches_existing_grid():
    """여러 타일을 다루게 바꾼 지형 읽기가 기존 판교 격자와 같다 (타일 파일이 있을 때)."""
    C.use_site("pangyo")
    from dpo.data_terrain import dsm_on_grid, site_frame
    if not (C.RAW / C.SITE["dsm_file"]).exists():
        pytest.skip("지형 타일 없음")
    z, _, _ = dsm_on_grid(site_frame())
    g = np.load(C.PROC / C.SITE["grid"])
    assert float(np.abs(z - g["dsm"]).max()) == 0.0
