"""DJI 경로 파일(WPML .kmz) 내보내기: 문서의 구조·값 범위, 되읽기, 계획과의 일치."""
import base64
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from dpo import config as C, wpml as WP

HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()
NS = dict(k=WP.KML_NS, w=WP.WPML_NS)


def _wps():
    # 300 m 직진 → 90° 꺾어 200 m → 대기 12초 → 올라가며 250 m → 150 m
    lat0, lon0 = 37.40, 127.10
    dn = lambda m: m / WP.MLAT                                                # noqa: E731
    return [dict(lat=lat0, lon=lon0, height=60.0, speed=12.0, hover=0.0),
            dict(lat=lat0 + dn(300), lon=lon0, height=60.0, speed=18.0, hover=0.0),
            dict(lat=lat0 + dn(300), lon=lon0 + 0.00226, height=60.0, speed=8.0, hover=12.0),
            dict(lat=lat0 + dn(550), lon=lon0 + 0.00226, height=80.0, speed=9.5, hover=0.0),
            dict(lat=lat0 + dn(700), lon=lon0 + 0.00226, height=80.0, speed=9.5, hover=0.0)]


def test_kmz_structure_and_roundtrip():
    w = WP.build(_wps(), model="m30", created_ms=1_700_000_000_000)
    assert WP.check(w["kmz"]) == []
    z = zipfile.ZipFile(io.BytesIO(w["kmz"]))
    assert z.namelist() == ["wpmz/template.kml", "wpmz/waylines.wpml"]            # 문서가 정한 이름 그대로
    for name in z.namelist():
        root = ET.fromstring(z.read(name))                                        # XML 로 읽히고 이름공간이 맞다
        assert root.tag == f"{{{WP.KML_NS}}}kml" and root.find("k:Document/w:missionConfig", NS) is not None
    r = WP.read_kmz(w["kmz"])
    assert r["waylines_height_mode"] == r["template_height_mode"] == "relativeToStartPoint"
    assert r["waylines_drone"] == (67, "0") and r["waylines_mission"]["finishAction"] == "autoLand"
    assert r["waylines_mission"]["exitOnRCLost"] == "executeLostAction" and r["waylines_mission"]["executeRCLostAction"] == "goBack"
    src = _wps()
    for key in ("template", "waylines"):
        pts = r[key]
        assert [p["index"] for p in pts] == [0, 1, 2, 3, 4]
        for p, q in zip(pts, src):
            assert abs(p["lat"] - q["lat"]) < 1e-7 and abs(p["lon"] - q["lon"]) < 1e-7 and abs(p["height"] - q["height"]) < 0.051
            assert p["hover"] == q["hover"]
        assert [p["speed"] for p in pts] == [12.0, 15.0, 8.0, 9.5, 9.5]            # 18 m/s 는 기종 한계 15 로 깎인다
    assert w["summary"]["n_clamped"] == 1 and any("깎았습니다" in t for t in w["summary"]["warnings"])
    # 꺾임 방식: 처음·끝·대기 지점은 정지, 나머지는 미리 돌고 통과 (+ 직선 유지)
    t = [p["turn"] for p in r["waylines"]]
    assert t == [WP.TURN_STOP, WP.TURN_PASS, WP.TURN_STOP, WP.TURN_PASS, WP.TURN_STOP]
    assert [p["straight"] for p in r["waylines"]] == [None, "1", None, "1", None]
    assert 0 < r["waylines"][1]["damping"] <= 5.0 and r["waylines"][0]["damping"] == 0.0
    # 실행 파일의 요소 순서가 문서 예시와 같다
    pm = ET.fromstring(z.read("wpmz/waylines.wpml")).findall("k:Document/k:Folder/k:Placemark", NS)[2]
    assert [c.tag.split("}")[1] for c in pm] == ["Point", "index", "executeHeight", "waypointSpeed", "waypointHeadingParam", "waypointTurnParam", "actionGroup"]
    assert pm.find("w:actionGroup/w:action/w:actionActuatorFunc", NS).text == "hover"
    assert pm.find("w:actionGroup/w:actionTrigger/w:actionTriggerType", NS).text == "reachPoint"
    assert float(r["waylines_mission"]["globalRTHHeight"]) == 100.0 and float(r["waylines_mission"]["takeOffSecurityHeight"]) == 20.0


def test_models_options_and_errors():
    w = WP.build(_wps(), model="m350", finish="goHome", rc_lost="goContinue", turn="pass", rth_height=140)
    r = WP.read_kmz(w["kmz"])
    assert r["waylines_drone"] == (89, None)                                       # M350 은 하위 기종 번호가 없다
    assert r["waylines"][1]["turn"] == WP.TURN_COORD and r["waylines"][1]["straight"] is None   # useStraightLine 미지원 기종
    assert r["waylines_mission"]["exitOnRCLost"] == "goContinue" and "executeRCLostAction" not in r["waylines_mission"]
    assert float(r["waylines_mission"]["globalRTHHeight"]) == 140.0 and WP.check(w["kmz"]) == []
    s = WP.build(_wps(), model="m3e", turn="stop")
    assert all(p["turn"] == WP.TURN_STOP for p in WP.read_kmz(s["kmz"])["waylines"]) and WP.check(s["kmz"]) == []
    short = _wps()[:2] + [dict(lat=37.40 + 302 / WP.MLAT, lon=127.10 + 0.00002, height=60.0, speed=5.0, hover=0.0), _wps()[3]]
    k = WP.build(short, model="m30")                                               # 2 m 짜리 구간: 선회 거리를 줄여 규칙을 지킨다
    assert WP.check(k["kmz"]) == []
    with pytest.raises(ValueError):
        WP.build(_wps(), model="mavic9")
    with pytest.raises(ValueError):
        WP.build(_wps()[:1])
    with pytest.raises(ValueError):
        WP.build(_wps(), finish="explode")
    assert WP.check(b"not a zip")[0].startswith("읽기 실패")


def test_items_conversion_delay_and_hover():
    items = [dict(seq=0, frame=0, cmd=16, p=[0, 0, 0, 0], lat=37.4, lon=127.1, alt=40.0),
             dict(seq=1, frame=0, cmd=93, p=[25.0, -1, -1, -1], lat=0, lon=0, alt=0),
             dict(seq=2, frame=3, cmd=22, p=[0, 0, 0, 0], lat=37.4, lon=127.1, alt=50.0),
             dict(seq=3, frame=0, cmd=178, p=[1, 9.0, -1, 0], lat=0, lon=0, alt=0),
             dict(seq=4, frame=3, cmd=16, p=[0, 0, 0, 0], lat=37.402, lon=127.1, alt=50.0),
             dict(seq=5, frame=3, cmd=19, p=[8.0, 0, 0, 0], lat=37.402, lon=127.1, alt=50.0),
             dict(seq=6, frame=0, cmd=178, p=[1, 13.5, -1, 0], lat=0, lon=0, alt=0),
             dict(seq=7, frame=3, cmd=16, p=[0, 0, 0, 0], lat=37.404, lon=127.101, alt=70.0),
             dict(seq=8, frame=3, cmd=21, p=[0, 0, 0, 0], lat=37.404, lon=127.101, alt=0.0)]
    wps, info = WP.waypoints_from_items(items)
    assert [(w["height"], w["speed"], w["hover"]) for w in wps] == [(50.0, 9.0, 0.0), (50.0, 13.5, 8.0), (70.0, 13.5, 0.0)]
    assert info["ground_delay"] == 25.0 and info["home"] == (37.4, 127.1)
    out = WP.from_items(items, model="m30t")
    assert any("지상 대기 25초" in t for t in out["summary"]["warnings"]) and WP.check(out["kmz"]) == []
    with pytest.raises(ValueError):                                                # 이륙 항목이 없는 미션(비행 중 남은 구간)은 바꾸지 않는다
        WP.waypoints_from_items([items[0], items[4], items[7]])


@pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
def test_export_matches_plan_and_cli(tmp_path):
    """계획 → KMZ: 경로점·고도·속도가 ArduPilot 미션과 같고, 명령줄로도 파일이 나온다."""
    from dpo import cli
    from dpo.webapp.server import App
    app = App()
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=True))
    m, w = app.mission(dict(speed_mode="file")), app.wpml(dict(model="m30"))
    assert [L["name"] for L in w["legs"]] == [L["name"] for L in m["legs"]] == ["out", "back"]
    for Lm, Lw in zip(m["legs"], w["legs"]):
        s = Lw["summary"]
        assert s["check"] == [] and s["height_mode"] == "relativeToStartPoint"
        pts = WP.read_kmz(base64.b64decode(Lw["kmz_b64"]))["waylines"]
        ref = [it for it in Lm["items"] if it["cmd"] in (22, 16) and it["seq"] > 0]      # 이륙 + 경로점
        assert len(pts) == len(ref) == s["n_waypoints"]
        for p, it in zip(pts, ref):
            assert abs(p["lat"] - it["lat"]) < 1e-7 and abs(p["lon"] - it["lon"]) < 1e-7 and abs(p["height"] - it["alt"]) < 0.051
        sp = [it["p"][1] for it in Lm["items"] if it["cmd"] == 178]
        assert pts[0]["speed"] == min(sp[0], 15.0) and max(p["speed"] for p in pts) <= 15.0
        assert s["rth_height"] >= s["height_max"] and 1.2 <= s["takeoff_security_height"] <= 20.0
        assert (s["soc_end"] is None) == (s["n_clamped"] > 0)                            # 속도를 깎았으면 SOC 예상을 내지 않는다
    assert "전력 모델" in w["notes"][0]
    # 기종 속도 한계를 넣어 계획하면 깎이는 구간이 없고 SOC 예상이 남는다
    r15 = app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=True, vg_max=15))
    w15 = app.wpml(dict(model="m30"))
    assert all(L["summary"]["n_clamped"] == 0 and L["summary"]["soc_end"] is not None and L["summary"]["speed_max"] <= 15.0 for L in w15["legs"])
    assert abs(w15["legs"][-1]["summary"]["soc_end"] - r15["soc_end"]) < 0.05
    with pytest.raises(ValueError):
        app.plan(dict(obstacles=[], drone="m100", vg_max=2))
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=True))
    with pytest.raises(ValueError):
        app.wpml(dict(model="phantom"))
    assert cli.main(["--drone", "m100", "--payload", "0.5", "--one-way", "--wpml", "m30"]) == 2          # --out 없이는 쓸 곳이 없다
    assert cli.main(["--drone", "m100", "--payload", "0.5", "--one-way", "--wpml", "m350", "--wpml-finish", "goHome", "--out", str(tmp_path)]) == 0
    f = list(tmp_path.glob("*_m350.kmz"))
    assert len(f) == 1 and WP.check(f[0].read_bytes()) == []
    assert WP.read_kmz(f[0].read_bytes())["waylines_mission"]["finishAction"] == "goHome"


def test_drone_info_from_pilot2_template(tmp_path):
    """번호를 문서로 확인하지 못한 기종(예: M4T): DJI Pilot 2 가 내보낸 파일의 기종·카메라 번호와 선회 방식을 그대로 쓴다."""
    import io
    import zipfile
    wps = [dict(lat=37.40, lon=127.10, height=30.0, speed=10.0, hover=0.0),
           dict(lat=37.401, lon=127.101, height=40.0, speed=12.0, hover=0.0),
           dict(lat=37.402, lon=127.103, height=40.0, speed=12.0, hover=0.0)]
    # 'Pilot 2 가 내보낸 파일' 흉내: m30 으로 만든 뒤 번호를 99-1 / 89 로, 선회 방식을 coordinateTurn 으로 바꾼다
    src = WP.build(wps, model="m30")["kmz"]
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zi, zipfile.ZipFile(buf, "w") as zo:
        for n in zi.namelist():
            t = zi.read(n).decode("utf-8")
            t = (t.replace("<wpml:droneEnumValue>67<", "<wpml:droneEnumValue>99<").replace("<wpml:droneSubEnumValue>0<", "<wpml:droneSubEnumValue>1<")
                  .replace("<wpml:payloadEnumValue>52<", "<wpml:payloadEnumValue>89<")
                  .replace("toPointAndPassWithContinuityCurvature", "coordinateTurn").replace("<wpml:globalUseStraightLine>1</wpml:globalUseStraightLine>", ""))
            zo.writestr(n, t)
    info = WP.drone_info_from_kmz(buf.getvalue())
    assert (info["drone_enum"], info["drone_sub"], info["payload_enum"]) == (99, 1, 89)
    assert info["turn_mode"] == "coordinateTurn" and info["straight"] is False
    out = WP.build(wps, drone_info=info)
    r = WP.read_kmz(out["kmz"])
    assert r["waylines_drone"] == (99, "1") and r["template_drone"] == (99, "1")
    assert out["summary"]["model"] == "template" and out["summary"]["payload_enum"] == 89
    assert {p["turn"] for p in r["waylines"][1:-1]} == {"coordinateTurn"}
    assert WP.check(out["kmz"]) != [] and WP.check(out["kmz"], extra_drone_enums=(99,)) == []
    with pytest.raises(ValueError):
        WP.drone_info_from_kmz(_empty_zip())


def _empty_zip():
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("readme.txt", "x")
    return b.getvalue()
