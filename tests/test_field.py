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
        run = FlightRunner(app.site, d, link, M["plan"], period=30.0, max_wind_dev=15.0)    # 이 시험의 바람 차이는 약 10 m/s
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


@needs_data
def test_flight_runner_rejects_bad_wind_and_caps_unreachable_speed():
    """SITL 에서 본 두 가지: EKF 바람 추정이 크게 틀릴 수 있고, 낼 수 없는 속도 명령도 수락된다."""
    pytest.importorskip("pymavlink")
    from dpo.flight import FlightRunner
    from dpo.mavlink_bridge import MavBridge
    from dpo.sim_vehicle import SimVehicle
    from dpo.webapp.server import App
    app = App()
    d = app.drones["m100"]
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=False))
    M = app.mission(dict(speed_mode="command"))["legs"][0]
    st = app.site.lowest_free_k(*M["plan"]["a"])
    wu, wv = (float(v) for v in app.site.wind_at(M["plan"]["t_cruise0"], *st))
    port = 15000 + int(time.time() * 1000) % 200
    # 실제 바람은 예보와 같지만 기체가 3배로 부풀려 보고하고, 대기속도는 6 m/s 까지만 낸다
    veh = SimVehicle(f"udpin:127.0.0.1:{port}", rate=10.0, wind_uv=(wu, wv), wind_report=(3 * wu + 9.0, 3 * wv), va_limit=6.0).start()
    link = MavBridge(f"udpout:127.0.0.1:{port}", timeout=5.0)
    try:
        assert link.connect() and link.upload_mission(M["items"])
        run = FlightRunner(app.site, d, link, M["plan"], period=20.0)
        run.time_rate = 10.0
        veh.start_auto()
        s = run.run(timeout=180.0, dt=0.02)
        assert s["phase"] == "완료" and veh.done
        assert s["wind_rejected"] >= 1 and s["updates"] == 0 and s["bias"] == 0.0       # 틀린 바람 측정으로는 속도를 바꾸지 않는다
        assert s["speed_shortfall"] >= 1 and run.v_cap is not None and run.v_cap <= d.vg_max
        assert any("명령 속도 미달" in e["msg"] for e in s["events"]) and any("측정 제외" in e["msg"] for e in s["events"])
        assert any("착륙 중" in e["msg"] for e in s["events"]) and s["max_xtrack"] < 60.0
    finally:
        link.close()
        veh.stop()


@needs_data
def test_confidence_go_no_go_grows_with_forecast_age():
    from dpo import cli
    from dpo.webapp.server import App
    app = App()
    app.plan(dict(obstacles=[], dep_hour=15, payload=1.0, drone="x650", round_trip=True))
    c6, c30, c78 = (app.confidence(dict(level=0.95, forecast_age_h=h)) for h in (6, 30, 78))
    assert c6["decision"] == "go" and c6["sigma"] < c30["sigma"] < c78["sigma"]
    assert 0 <= c6["margin_pp"] < c30["margin_pp"]                    # 예보 시점이 멀수록 필요한 여유가 크다
    # 78시간 전 예보: 강풍일이라 비행할 수 없는 바람이 신뢰구간에 들어간다 → 여유를 숫자로 낼 수 없거나 더 크다
    assert c78["margin_pp"] is None or c78["margin_pp"] > c30["margin_pp"]
    assert c78["soc_end_q"] <= c30["soc_end_q"] <= c6["soc_end_q"]
    assert c6["soc_end_q"] <= c6["soc_end"] and len(c6["legs"]) == 2 and c6["season"]["month"] == 10
    assert app.confidence(dict(level=0.5, forecast_age_h=6))["soc_end_q"] >= c6["soc_end_q"] - 1e-9
    with pytest.raises(ValueError):
        app.confidence(dict(level=0.8))
    # 예비를 80 % 로 올리면 예보대로여도 못 지킨다 → 불가
    app.plan(dict(obstacles=[], dep_hour=15, payload=1.0, drone="x650", round_trip=True, reserve=80.0))
    assert app.confidence(dict(level=0.95))["decision"] == "no-go"
    # 명령줄: 판정이 '가능'이면 0, 아니면 4
    assert cli.main(["--drone", "x650", "--payload", "1.0", "--one-way", "--confidence", "0.95"]) == 0
    assert cli.main(["--drone", "m100", "--payload", "0.5", "--confidence", "0.95", "--forecast-age", "30"]) == 4


def test_compare_existing_vs_proposed():
    """기존 경로(최단 + 정속)와 제안 경로를 나란히: 세 방식의 수치·경로 표본·근거 문장이 나오고 몫이 서로 맞는다."""
    from dpo.webapp.server import App
    app = App()
    app.plan(dict(obstacles=[], dep_hour=15, payload=0.5, drone="m100", round_trip=False))
    c = app.compare()
    m = {x["key"]: x for x in c["methods"]}
    assert set(m) == {"existing", "existing_vopt", "proposed"}
    ex, exv, pr = m["existing"], m["existing_vopt"], m["proposed"]
    assert ex["legs"][0]["samples"] and pr["legs"][0]["samples"] and len(pr["per"]) == 1
    assert exv["soc_used"] <= ex["soc_used"] + 1e-6               # 같은 경로에서 속도 최적화는 정속보다 나쁘지 않다
    assert pr["soc_used"] <= exv["soc_used"] * 1.002 + 1e-6       # 안전장치: 제안은 최단 경로보다 나쁘지 않다 (허용 0.2 %)
    by = c["by"]
    assert abs((1 + by["speed_pct"] / 100) * (1 + by["path_pct"] / 100) - (1 + by["total_pct"] / 100)) < 1e-9
    assert by["total_pct"] < 0 and len(c["reasons"]) >= 3 and c["reasons"][0].startswith("배터리")
    assert abs(ex["L"] - exv["L"]) < 1.0 and abs(ex["per"][0]["tail"] - exv["per"][0]["tail"]) < 0.3    # 바람 성분은 경로의 성질 (지나는 시각만 조금 다름)
    c8 = app.compare(dict(speed=8.0))
    assert c8["speed"] == 8.0 and c8["methods"][0]["label"].endswith("8 m/s)")
    with pytest.raises(ValueError):
        app.compare(dict(speed=40.0))
