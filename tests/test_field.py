"""실증 준비: 명령 방식 미션, 기체 연결 실행기(모의 기체), 비행 로그 보정. 실행: PYTHONPATH=src pytest -q"""
import math
import time

import numpy as np
import pytest

from dpo import config as C, drones as DR, logcal as LC
from dpo.energy import RHO0, power_w

HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")


def test_log_calibration_recovers_power_curve(tmp_path):
    pytest.importorskip("pymavlink")
    d_true = DR.x650(cda=0.14, p0=45.0)                     # 실제 기체가 추정 모델과 다르다고 가정
    f = LC.synth_flight(tmp_path / "sweep.bin", d_true, 5.1)
    lg = LC.read_log(f)
    assert lg["has_wind"] and len(lg["t"]) > 400
    pts = LC.steady_points(lg)
    assert len(pts) >= 60 and pts[:, 0].max() > 14.0
    d0 = DR.x650()
    d, st = LC.fit_drone(pts, d0, 5.1)
    assert st["mape"] < 2.0 and st["r2"] > 0.99 and d.cda == pytest.approx(0.14, abs=0.02)
    v = np.arange(0.0, 15.01, 1.0)
    p_true = np.array([power_w(x, 0.0, 5.1, RHO0, d_true.params()) for x in v])
    p_fit = np.array([power_w(x, 0.0, 5.1, RHO0, d.params()) for x in v])
    p_old = np.array([power_w(x, 0.0, 5.1, RHO0, d0.params()) for x in v])
    assert np.abs(p_fit / p_true - 1).max() < 0.03          # 보정한 전력 곡선은 3 % 안
    assert np.abs(p_old / p_true - 1).max() > 0.08          # 보정 전 추정 모델은 그보다 크게 어긋나 있었다
    # 좁은 속도 범위·호버 없음이면 경고한다. 구간이 너무 적으면 거부한다
    f2 = LC.synth_flight(tmp_path / "narrow.bin", d_true, 5.1, speeds=(9, 10, 11))
    _, st2 = LC.fit_drone(LC.steady_points(LC.read_log(f2)), d0, 5.1)
    assert len(st2["warnings"]) >= 2
    with pytest.raises(ValueError):
        LC.fit_drone(pts[:5], d0, 5.1)


def test_calibration_file_is_used_by_planner_model(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(C, "ROOT", tmp_path)
    assert DR.get("x650").cda == pytest.approx(0.08)
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "x650.json").write_text(json.dumps(dict(params=dict(eta=0.7, p0=40.0, cda=0.13, p_av=10.0), date="2026-11-18")))
    d = DR.get("x650")
    assert (d.eta, d.p0, d.cda) == (0.7, 40.0, 0.13) and "보정" in d.name
    assert DR.get("x650", calibrated=False).cda == pytest.approx(0.08)


@needs_data
def test_command_mode_mission_has_single_speed_item():
    from dpo.ardupilot import CMD
    from dpo.webapp.server import App
    app = App()
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=False))
    f, c = app.mission()["legs"][0], app.mission(dict(speed_mode="command"))["legs"][0]
    n = lambda M: sum(1 for it in M["items"] if it["cmd"] == CMD["CHANGE_SPEED"])  # noqa: E731
    assert n(f) >= 2 and n(c) == 1 and c["summary"]["speed_mode"] == "command"
    assert len(c["items"]) < len(f["items"])                               # 속도 변경점으로 경로를 나누지 않는다
    assert c["summary"]["soc_end"] == pytest.approx(f["summary"]["soc_end"], abs=0.05)
    assert len(c["plan"]["path"]) == len(c["plan"]["waits"]) and c["plan"]["mass"] > 3.0
    with pytest.raises(ValueError):
        app.mission(dict(speed_mode="x"))


@needs_data
def test_flight_runner_follows_simulated_vehicle():
    pytest.importorskip("pymavlink")
    from dpo.flight import FlightRunner
    from dpo.live import perturbed_site
    from dpo.mavlink_bridge import MavBridge
    from dpo.sim_vehicle import SimVehicle
    from dpo.webapp.server import App
    app = App()
    d = app.drones["m100"]
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=False))
    M = app.mission(dict(speed_mode="command"))["legs"][0]
    tr = perturbed_site(app.site, rot=60.0)
    st = app.site.lowest_free_k(*M["plan"]["a"])
    wu, wv = tr.wind_at(M["plan"]["t_cruise0"], *st)
    prm, mass = d.params(), M["plan"]["mass"]
    port = 14700 + int(time.time() * 1000) % 200
    veh = SimVehicle(f"udpin:127.0.0.1:{port}", rate=10.0, wind_uv=(float(wu), float(wv)), battery_wh=d.battery_wh,
                     power_fn=lambda ae, an, vz: power_w(math.hypot(ae, an), vz, mass, 1.2, prm)).start()
    link = MavBridge(f"udpout:127.0.0.1:{port}", timeout=5.0)
    try:
        assert link.connect() and link.upload_mission(M["items"])
        assert len(veh.mission) == len(M["items"])
        run = FlightRunner(app.site, d, link, M["plan"], period=30.0)
        run.time_rate = 10.0
        assert run.step() == "대기" or run.phase == "대기"          # 조종사가 AUTO 로 바꾸기 전에는 아무것도 보내지 않는다
        time.sleep(0.3); run.step()
        assert run.phase == "대기" and run.stats["cmd_sent"] == 0
        veh.start_auto()
        s = run.run(timeout=120.0, dt=0.02)
        assert s["phase"] == "완료" and veh.done
        assert s["checks"] >= 3 and s["updates"] >= 1 and 5.0 < s["bias"] < 15.0        # 실제 풍향이 60° 다른 것을 잰다
        assert s["cmd_sent"] >= 2 and s["cmd_acked"] == s["cmd_sent"] and s["cmd_failed"] == 0
        cmds = [v for _, v, src in veh.speed_log if src == "command"]
        assert len(cmds) == s["cmd_sent"] and all(3.0 <= v <= d.vg_max for v in cmds)
        assert sum(1 for *_, src in veh.speed_log if src == "mission") == 1               # 미션 안의 속도 항목은 첫 구간 하나
        assert "soc_meas_last" in s and abs(s["soc_meas_last"] - s["soc_pred_last"]) < 15.0
        assert any(e["kind"] == "update" for e in s["events"])
    finally:
        link.close()
        veh.stop()
