"""대상 기체(X650) 프리셋과 ArduPilot 미션 내보내기 시험. 실행: PYTHONPATH=src pytest -q"""
import numpy as np
import pytest

from dpo import config as C, drones as DR
from dpo.ardupilot import CMD, FRAME_REL, leg_items, merge_collinear, to_csv, to_wpl
from dpo.energy import RHO0, power_w, soc_drop
from dpo.planner import Planner, simulate, vertical_energy
from dpo.wind_field import Site3D

HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()


def test_x650_matches_spec_and_hover_anchor():
    d = DR.x650()
    s = DR.X650_SPEC
    assert d.m_empty == s["empty_kg"] and d.battery_wh == s["battery_wh"]
    assert abs(d.rotor_r - 15 * 0.0254 / 2) < 1e-9
    assert DR.PAYLOAD_MAX["x650"] == pytest.approx(s["mtow_kg"] - s["empty_kg"])
    # 무탑재 호버 30분이면 SOC 100 %p (제조사 값에 맞춘 조건)
    p = power_w(0.0, 0.0, d.m_empty, RHO0, d.params())
    assert soc_drop(p * 30 * 60, d) == pytest.approx(100.0, abs=0.2)
    # 탑재가 늘면 호버 전력이 늘고, 상승·하강률은 ArduCopter 기본값
    assert power_w(0.0, 0.0, d.m_empty + 2.2, RHO0, d.params()) > p
    assert (d.vz_up, d.vz_dn) == (2.5, 1.5)
    assert d.speed_candidates().max() == pytest.approx(d.vg_max)


def test_m100_speed_candidates_unchanged():
    assert np.allclose(DR.m100().speed_candidates(), np.arange(3.0, 20.01, 0.5))


def test_merge_collinear_keeps_turns_and_waits():
    path = [(0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 1, 3), (0, 2, 4), (1, 2, 4)]
    p, w = merge_collinear(path, [0, 0, 0, 0, 0, 0])
    assert p == [(0, 0, 0), (0, 0, 2), (0, 2, 4), (1, 2, 4)] and w == [0.0, 0.0, 0.0, 0.0]
    # 중간 노드 (0,0,1) 에서 호버 대기가 있으면 그 노드는 남는다
    p, w = merge_collinear(path, [0, 0, 12.0, 0, 0, 0])
    assert (0, 0, 1) in p and w[p.index((0, 0, 1)) + 1] == 12.0


@pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
def test_mission_export_matches_simulation(tmp_path):
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    d = DR.x650()
    a, b = (2700.0, 3000.0), (4600.0, 1500.0)
    st, gl = site.lowest_free_k(*a), site.lowest_free_k(*b)
    t, m = 6 * 3600.0, d.m_empty + 1.0
    e_to, t_to = vertical_energy(site, d, a[0], a[1], st[0], t, m, up=True)
    r = Planner(site, d).plan(st, gl, t_start=t + t_to, mass=m, weight=2.0)
    assert r is not None
    waits = np.array(r["wait_before"], float)
    waits[3] = 20.0                                         # 호버 대기 한 번을 넣어 LOITER_TIME 도 확인
    sim = simulate(site, d, r["path"], waits, t + t_to, m)
    e_ld, t_ld = vertical_energy(site, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
    M = leg_items(site, d, r["path"], waits, t + t_to, m, e_takeoff=e_to, t_takeoff=t_to, t_land=t_ld, e_land=e_ld, t_ref=t)
    items, s = M["items"], M["summary"]
    cmds = [it["cmd"] for it in items]
    assert cmds[0] == CMD["WAYPOINT"] and items[0]["frame"] == 0          # 홈
    assert cmds[1] == CMD["TAKEOFF"] and cmds[-1] == CMD["LAND"]
    assert cmds.count(CMD["LOITER_TIME"]) == 1 and CMD["CHANGE_SPEED"] in cmds
    assert [it["seq"] for it in items] == list(range(len(items)))
    # 상대 고도 = 노드 해발고도 − 출발지 지면
    gh = site.ground[st[1], st[2]]
    assert items[1]["alt"] == pytest.approx(site.z[st[0]] - gh, abs=0.06)
    assert all(it["alt"] > 20 for it in items if it["frame"] == FRAME_REL and it["cmd"] != CMD["LAND"])
    # 대지속도 명령은 기체 상한 이하, 시간·SOC 는 전체 시뮬레이션과 일치
    v = [it["p"][1] for it in items if it["cmd"] == CMD["CHANGE_SPEED"]]
    assert max(v) <= d.vg_max + 1e-9 and min(v) >= 3.0
    T_sim = sim["t"][-1] - t + t_ld
    assert s["duration_s"] == pytest.approx(T_sim, rel=0.01)
    assert s["soc_end"] == pytest.approx(100 - soc_drop(e_to + sim["e"][-1] + e_ld, d), abs=0.3)
    assert s["n_waypoints"] < s["n_plan_nodes"] + s["n_speed_changes"] + 1
    # 파일 형식
    txt = to_wpl(items)
    lines = txt.strip().split("\n")
    assert lines[0] == "QGC WPL 110" and len(lines) == len(items) + 1
    assert all(len(ln.split("\t")) == 12 for ln in lines[1:])
    assert to_csv(M["rows"]).count("\n") == len(items) + 1
    mavwp = pytest.importorskip("pymavlink.mavwp")
    f = tmp_path / "m.waypoints"
    f.write_text(txt)
    L = mavwp.MAVWPLoader()
    assert L.load(str(f)) == len(items)
    assert L.wp(1).command == 22 and L.wp(1).frame == 3 and L.wp(L.count() - 1).command == 21


@pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
def test_platform_3d_scene_plan_and_mission():
    import json

    from dpo.webapp.server import App
    app = App()
    sc = json.loads(app.scene3d())
    n = sc["nx"] * sc["ny"]
    assert len(sc["ground"]) == n == len(sc["floor"]) == len(sc["bh"])
    assert len(sc["buildings"]) > 5000 and all(len(b["p"]) >= 3 and b["h"] > 0 for b in sc["buildings"][:200])
    assert {d["key"] for d in app.site_info()["drones"]} == {"m100", "x650"}
    w = app.wind3d(15)
    assert len(w["u"]) == len(sc["z"]) and len(w["u"][0]) > 5
    # 탑재중량 한계는 기체별
    with pytest.raises(ValueError):
        app.plan(dict(drone="m100", payload=1.0, round_trip=False))
    with pytest.raises(ValueError):
        app.plan(dict(drone="x650", payload=2.5, round_trip=False))
    res = app.plan(dict(drone="x650", payload=1.0, kind="delivery", round_trip=True, dep_hour=15, weight=2.0))
    assert res["drone"]["key"] == "x650" and res["feasible"] and len(res["legs"]) == 2
    zs = [p["z"] for L in res["legs"] for p in L["samples"]]
    assert min(zs) >= sc["z"][0] - 1e-6 and max(zs) <= sc["z"][-1] + 1e-6
    m = app.mission()
    assert [L["name"] for L in m["legs"]] == ["out", "back"]
    for L, P in zip(m["legs"], res["legs"]):
        assert L["wpl"].startswith("QGC WPL 110\n")
        assert L["summary"]["duration_s"] == pytest.approx(P["T"], rel=0.02)
    # 미션의 마지막 SOC 는 계획 결과의 도착 SOC 와 같아야 한다
    assert m["legs"][-1]["summary"]["soc_end"] == pytest.approx(res["soc_end"], abs=0.5)
