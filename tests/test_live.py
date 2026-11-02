"""실시간 운용 엔진(dpo.live.LiveEngine) – 동기(결정적) 모드 시험. 실제 지형·바람 격자를 쓰므로 수십 초 걸린다."""
import json

import numpy as np
import pytest

from dpo import config as C


@pytest.fixture(scope="module")
def env():
    if not (C.PROC / "site_grid.npz").exists():
        pytest.skip("site grid not available")
    from dpo.energy import Drone
    from dpo.wind_field import Site3D
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    site.add_static_nfz_circle(3650.0, 2250.0, 250.0)
    dd = json.loads((C.RES / "drone_params.json").read_text())
    drone = Drone(**{k: v for k, v in dd.items() if k in Drone.__dataclass_fields__})
    m = json.loads((C.RES / "adsb_error_model.json").read_text())
    model = dict(knots=m["knots"], q=m["model_all"]["q"], s1=m["model_all"]["s1"])
    return site, drone, model


def _engine(env, **cfg):
    from dpo.live import LiveEngine
    site, drone, model = env
    E = LiveEngine(site, drone, model, sync=True)
    E.start(dict(dict(dep_hour=20, round_trip=False, eps=0.05, shape="adsb"), **cfg))
    return E


def test_live_completes_one_way_mission(env):
    E = _engine(env)
    res = E.run_sync(t_max=900, dt=0.5)
    assert res["state"] == "done" and res["collisions"] == 0
    assert 10 < res["soc_used"] < 60
    phases = [e["msg"] for e in E.events if e["kind"] == "phase"]
    assert any("이륙" in p for p in phases) and any("착륙" in p for p in phases)


def test_live_intruder_triggers_replan_without_intrusion(env):
    E = _engine(env)
    res = E.run_sync(t_max=900, dt=0.5, hooks={60.0: lambda e: e.inject_intruder(dict(seed=1, lead=25, speed=20,
                                                                                      radius=80))})
    assert res["state"] == "done"
    assert res["replans"] >= 1 and res["collisions"] == 0


def test_live_emergency_hold_for_obstacle_placed_ahead(env):
    def hook(e):   # 드론 바로 앞(약 4 s 뒤 위치)에 정지 장애물 → 재계획할 시간이 없으므로 비상 정지
        x, y, _ = e._drone_at(e.t + 12.0)
        e.upsert_obstacle(dict(name="앞", kind="cylinder", radius=60, margin=15, z_min=0, z_max=1000,
                               path=[dict(x=x, y=y, t=0)], t_start=e.t, t_end=None))
    E = _engine(env, latency=8.0)
    res = E.run_sync(t_max=1200, dt=0.5, hooks={70.0: hook})
    assert res["holds"] >= 1 and res["collisions"] == 0 and res["state"] == "done"


def test_live_reserve_triggers_emergency_landing(env):
    E = _engine(env, reserve=97.0, eps=None)      # 쓸 수 있는 배터리 3 % → 순항 중 예비 도달 (또는 출발 불가)
    res = E.run_sync(t_max=900, dt=0.5)
    assert res["state"] == "aborted"
    assert any("비상 착륙" in e["msg"] or "갈 수 없음" in e["msg"] for e in E.events)


def test_live_external_report_becomes_obstacle(env):
    def hook(e):
        x, y, _ = e._drone_at(e.t + 30.0)
        e.report(dict(id="7", x=x + 300, y=y, vx=-10.0, vy=0.0, radius=50))
    E = _engine(env)
    E.run_sync(t_max=80, dt=0.5, hooks={50.0: hook})
    assert "X7" in E.intruders and any(o.name == "X7" for o in E.known)
    with pytest.raises(ValueError):
        E.report(dict(id="8", x=0, y=0, vx=200.0, vy=0.0))
