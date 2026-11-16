"""속도 갱신·안전장치의 플랫폼 연결과 미션·MAVLink 연결 시험. 실행: PYTHONPATH=src pytest -q"""
import math
import time

import numpy as np
import pytest

from dpo import config as C, drones as DR, speed_update as SU
from dpo.ardupilot import CMD
from dpo.live import LiveEngine, perturbed_site
from dpo.mission import st_safeguard
from dpo.obstacles import DynObstacle
from dpo.planner import Planner, simulate
from dpo.st_planner import STPlanner, check_path_conflicts
from dpo.st_rolling import remaining_conflict
from dpo.wind_field import Site3D

HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
A, B = (2700.0, 3000.0), (4600.0, 1500.0)


@pytest.fixture(scope="module")
def site():
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    return Site3D(g, f0, np.datetime64("2026-10-27T00:00"))


def test_wind_bias_ema_and_cap():
    b = SU.WindBias(alpha=0.6, max_bias=5.0)
    assert b.update((3.0, 0.0), (1.0, 0.0)) == (2.0, 0.0)            # 첫 측정은 그대로
    du, dv = b.update((1.0, 4.0), (1.0, 0.0))
    assert du == pytest.approx(0.8) and dv == pytest.approx(2.4)     # 0.4·이전 + 0.6·새 측정
    b.update((100.0, 0.0), (0.0, 0.0))
    assert b.magnitude == pytest.approx(5.0)                          # 이상값은 상한으로 자른다
    n = b.n
    b.update((float("nan"), 0.0), (0.0, 0.0))
    assert b.n == n                                                   # 잘못된 측정은 버린다


@needs_data
def test_commanded_speed_execution_and_route_cut(site):
    d = DR.m100()
    m, t0 = d.m_empty + 0.5, 6 * 3600.0
    st, gl = site.lowest_free_k(*A), site.lowest_free_k(*B)
    r = Planner(site, d).plan(st, gl, t_start=t0, mass=m, weight=2.0)
    w = np.array(r["wait_before"], float)
    w[2] = 12.0                                                       # 호버 대기도 한 번 넣는다
    pred = simulate(site, d, r["path"], w, t0, m)
    # 같은 바람에서 명령 속도대로 날면 계획과 같다
    ex = SU.execute(site, d, r["path"], w, t0, m, pred["vg"])
    assert np.allclose(ex["t"], pred["t"]) and ex["e"][-1] == pytest.approx(pred["e"][-1], rel=1e-12)
    # 예보 보정값 0 은 보정하지 않은 것과 같다
    assert simulate(site, d, r["path"], w, t0, m, bias=(0.0, 0.0))["e"][-1] == pytest.approx(pred["e"][-1], rel=1e-12)
    # 어느 기록에서 잘라도 '남은 경로'를 같은 바람에서 다시 날린 에너지·도착 시각이 원래와 같다 (호버 중간 포함)
    for q in (0, 3, int(np.where(pred["hov"])[0][0]), len(pred["t"]) // 2, len(pred["t"]) - 5):
        pts, wr, tq = SU.remaining_route(r["path"], w, pred, q)
        rem = simulate(site, d, pts, wr, tq, m)
        assert rem["t"][-1] == pytest.approx(pred["t"][-1], abs=0.5)
        assert rem["e"][-1] == pytest.approx(pred["e"][-1] - pred["e"][q], rel=0.01)
    # 실제 풍향이 90° 다르면 명령 속도를 못 내는 곳에서 낮춰 비행한다 (불가가 아니라 감속)
    tr = perturbed_site(site, rot=90.0)
    ex2 = SU.execute(tr, d, r["path"], w, t0, m, pred["vg"])
    assert ex2 is not None and (ex2["vg"][~ex2["hov"]] <= pred["vg"][~pred["hov"]] + 1e-9).all()
    sched = SU.speed_schedule(pred, t0, t0)
    assert sched and sched[0]["t"] >= 0 and all(3.0 <= c["v"] <= d.vg_max for c in sched)
    assert all(abs(a["v"] - b["v"]) >= 0.5 - 1e-9 for a, b in zip(sched[:-1], sched[1:]))


@needs_data
def test_st_safeguard_never_worse_and_collision_free(site):
    d = DR.x650()
    m, t0 = d.m_empty + 1.0, 6 * 3600.0
    SP = STPlanner(site, d)
    st, gl = site.lowest_free_k(*A), site.lowest_free_k(*B)
    # 직선 경로 한가운데를 막는 정지 장애물
    ob = DynObstacle.from_dict(dict(name="막힘", kind="cylinder", path=[dict(x=3650.0, y=2250.0, t=0.0)], radius=150.0, z_min=0.0,
                                    z_max=1000.0, t_start=0.0, t_end=3000.0, margin=15.0))
    for obs in ([], [ob]):
        SP.set_obstacles(obs, t0, eps=None)
        r = SP.plan(st, gl, t_start=t0, mass=m, weight=1.2)
        r2, info = st_safeguard(SP, st, gl, t0, m, r)
        assert info["chosen"] in ("energy", "shortest") and r2["chosen"] == info["chosen"]
        e = info["cand"]
        assert min(e.values()) <= e["energy"] + 1e-9
        sim = simulate(site, d, r2["path"], r2["wait_before"], t0 + r2.get("ground_delay", 0.0), m)
        assert sim is not None and sim["e"][-1] == pytest.approx(e[info["chosen"]], rel=1e-9)
        if info["chosen"] == "shortest":                    # 최단 후보는 더 싸고, 위험구간 표 검사를 통과한 것만 고른다
            assert e["shortest"] < e["energy"] and remaining_conflict(site, SP.table, sim, t0) is None
        cc = check_path_conflicts(site, sim, obs, t0)       # 실제 형상 기준으로도 충돌이 없다
        assert cc["n_collision_samples"] == 0 and (not obs or cc["min_clearance"] > 0)
    # 에너지 탐색 결과가 없으면 최단 후보만으로 답한다
    r3, info3 = st_safeguard(SP, st, gl, t0, m, None)
    assert r3 is not None and info3["chosen"] == "shortest" and "energy" not in info3["cand"]


@needs_data
def test_live_speed_update_and_safeguard(site):
    d = DR.m100()
    base = dict(dep_hour=15, payload=0.5, round_trip=False, refine=False)

    def run(**kw):
        E = LiveEngine(site, d, sync=True)
        E.start(dict(base, **kw))
        return E, E.run_sync(t_max=3000)

    # 예보 = 실제: 속도 갱신을 켜도 채택이 없고 결과가 같다
    _, s0 = run(speed_update=0)
    _, s1 = run(speed_update=30)
    assert s0["state"] == s1["state"] == "done" and s1["speed_updates"] == 0 and s1["speed_checks"] >= 3
    assert s1["soc_used"] == pytest.approx(s0["soc_used"], rel=0.005)
    # 실제 풍향이 60° 다름: 갱신을 켜면 SOC 소모가 준다. 편차 추정값은 실제 차이의 크기와 비슷하다
    _, f = run(speed_update=0, wind_truth=dict(rot=60), round_trip=True)
    E, u = run(speed_update=30, wind_truth=dict(rot=60), round_trip=True)
    assert f["state"] == u["state"] == "done" and u["speed_updates"] >= 1 and u["collisions"] == 0
    assert u["soc_used"] < f["soc_used"] * 0.98
    assert 5.0 < u["bias"] < 15.0 and E.speed_log and E.speed_log[0]["cmds"]
    assert any(e["kind"] == "speed" for e in E.events)
    # 안전장치: 끄면 '최단' 채택이 없고, 켜면 채택 횟수가 기록된다. 켠 쪽이 더 많이 쓰지 않는다
    _, a = run(speed_update=0, safeguard=False)
    _, b = run(speed_update=0, safeguard=True)
    assert a["sg_energy"] == a["sg_shortest"] == 0 and b["sg_energy"] + b["sg_shortest"] >= 1
    assert b["soc_used"] <= a["soc_used"] * 1.002


@needs_data
def test_live_speed_update_keeps_obstacle_safety(site):
    d = DR.m100()
    ob = dict(name="가로지름", kind="cylinder", radius=90.0, margin=15.0, z_min=0.0, z_max=1000.0, t_start=0.0, t_end=600.0,
              path=[dict(x=3300.0, y=1500.0, t=0.0), dict(x=4300.0, y=3000.0, t=140.0)])
    res = {}
    for su in (0, 15):
        E = LiveEngine(site, d, sync=True)
        E.start(dict(dep_hour=15, payload=0.5, round_trip=False, refine=False, eps=0.05, shape="gauss", speed_update=su,
                     wind_truth=dict(rot=60)), [ob])
        res[su] = E.run_sync(t_max=3000)
    assert all(r["state"] == "done" and r["collisions"] == 0 and r["min_sep"] > 0 for r in res.values())
    assert res[15]["soc_used"] <= res[0]["soc_used"] * 1.01


@needs_data
def test_remaining_mission_matches_flight(site, tmp_path):
    pytest.importorskip("pymavlink")
    from pymavlink import mavwp
    from dpo.energy import soc_drop
    d = DR.m100()
    got = {}

    def grab(E):
        got["m"], got["soc"] = E.mission(), 100 - soc_drop(E.energy(), E.drone)

    E = LiveEngine(site, d, sync=True)
    E.start(dict(dep_hour=15, payload=0.5, round_trip=False, refine=False, wind_truth=dict(rot=60), speed_update=30))
    assert not E.mission()["available"]                               # 지상에서는 낼 미션이 없다
    s = E.run_sync(t_max=3000, hooks={120.0: grab})
    m = got["m"]
    assert m["available"] and m["summary"]["airborne"]
    cmds = [it["cmd"] for it in m["items"]]
    assert cmds[0] == CMD["WAYPOINT"] and cmds[-1] == CMD["LAND"] and CMD["TAKEOFF"] not in cmds
    assert [it["seq"] for it in m["items"]] == list(range(len(m["items"])))
    assert m["summary"]["soc_now"] == pytest.approx(got["soc"], abs=0.05)
    assert m["summary"]["soc_end"] == pytest.approx(100 - s["soc_used"], abs=0.3)       # 미션의 예상 도착 SOC = 실제 도착 SOC
    assert len(m["speed_cmds"]) == m["summary"]["n_speed_changes"] >= 1
    assert all(3.0 <= c["v"] <= d.vg_max and c["t"] >= 120.0 - 1e-6 for c in m["speed_cmds"])
    f = tmp_path / "rem.waypoints"
    f.write_text(m["wpl"])
    w = mavwp.MAVWPLoader()
    assert w.load(str(f)) == len(m["items"])
    assert sum(1 for i in range(w.count()) if w.wp(i).command == CMD["CHANGE_SPEED"]) == m["summary"]["n_speed_changes"]


def test_mavlink_loopback_speed_wind_mission():
    pytest.importorskip("pymavlink")
    from dpo.mavlink_bridge import FakeVehicle, MavBridge, uv_to_wind, wind_to_uv
    u, v = wind_to_uv(*uv_to_wind(3.0, -4.0))
    assert (u, v) == pytest.approx((3.0, -4.0))
    assert uv_to_wind(0.0, -5.0) == pytest.approx((0.0, 5.0))        # 북풍(북에서 불어옴) = 남쪽으로 5 m/s
    port = 14600 + int(time.time() * 1000) % 300
    veh = FakeVehicle(f"udpin:127.0.0.1:{port}", wind_uv=(3.0, -4.0)).start()
    link = MavBridge(f"udpout:127.0.0.1:{port}", timeout=3.0)
    try:
        assert link.connect()
        assert link.send_change_speed(12.5) and link.send_change_speed(9.0)
        assert veh.speeds == [12.5, 9.0] and link.status()["acked"] == 2
        veh.reject_speed = True
        assert not link.send_change_speed(11.0) and "거부" in link.last_error
        veh.reject_speed = False
        w = link.read_wind(2.0)
        assert w == pytest.approx((3.0, -4.0), abs=1e-3)
        st = link.read_state(2.0)
        assert st["lat"] == pytest.approx(37.4) and st["soc"] == 87.0 and st["alt_rel"] == pytest.approx(100.0)
        items = [dict(seq=0, frame=0, cmd=16, p=[0, 0, 0, 0], lat=37.40203, lon=127.10550, alt=39.8),
                 dict(seq=1, frame=0, cmd=178, p=[1, 17.5, -1, 0], lat=0.0, lon=0.0, alt=0.0),
                 dict(seq=2, frame=3, cmd=16, p=[0, 0, 0, 0], lat=37.39223, lon=127.11863, alt=100.2),
                 dict(seq=3, frame=3, cmd=21, p=[0, 0, 0, 0], lat=37.38862, lon=127.12711, alt=0.0)]
        assert link.upload_mission(items)
        assert [x["cmd"] for x in veh.mission] == [16, 178, 16, 21]
        assert veh.mission[1]["p"][1] == pytest.approx(17.5) and veh.mission[2]["lat"] == pytest.approx(37.39223, abs=1e-6)
    finally:
        link.close()
        veh.stop()


@needs_data
def test_live_sends_speed_commands_over_link(site):
    pytest.importorskip("pymavlink")
    from dpo.mavlink_bridge import FakeVehicle, MavBridge
    port = 14900 + int(time.time() * 1000) % 90
    veh = FakeVehicle(f"udpin:127.0.0.1:{port}").start()
    link = MavBridge(f"udpout:127.0.0.1:{port}", timeout=3.0)
    try:
        assert link.connect()
        E = LiveEngine(site, DR.m100(), sync=True)
        E.attach_link(link)
        E.start(dict(dep_hour=15, payload=0.5, round_trip=False, refine=False, wind_truth=dict(rot=60), speed_update=30))
        s = E.run_sync(t_max=3000)
        assert s["state"] == "done" and s["cmd_sent"] >= 2 and s["cmd_acked"] == s["cmd_sent"]
        assert len(veh.speeds) == s["cmd_sent"] and all(3.0 <= v <= 20.0 for v in veh.speeds)
    finally:
        link.close()
        veh.stop()
