"""신뢰도 평가(dpo.reliability) 시험. 실행: PYTHONPATH=src pytest -q tests/test_reliability.py"""
import json
import math

import numpy as np
import pytest

from dpo import config as C, drones as DR
from dpo import reliability as RL
from dpo.planner import Planner, simulate
from dpo.wind_field import Site3D

HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
A, B = (2700.0, 3000.0), (4600.0, 1500.0)


@pytest.fixture(scope="module")
def case():
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    d = DR.m100()
    m, t0 = d.m_empty + 0.5, 6 * 3600.0
    P = Planner(site, d)
    st, gl = site.lowest_free_k(*A), site.lowest_free_k(*B)
    r = P.plan(st, gl, t_start=t0, mass=m, weight=2.0)
    r0 = P.plan(st, gl, t_start=t0, mass=m, mode=0)
    return dict(site=site, d=d, m=m, t0=t0, path=r["path"], waits=np.asarray(r["wait_before"], float), short=r0["path"])


def test_sigma_model_interpolation_and_season():
    md = RL.WindErrorModel([6, 30, 54, 78], [1.0, 1.5, 1.4, 2.4], by_season={"DJF": [1.2, 2.0, 2.5, 3.0]}, scale=1.0)
    xs = [0, 3, 6, 12, 30, 40, 54, 78, 100, 200]
    ys = [md.sigma(x) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))            # 경과 시간이 길수록 줄지 않는다 (역전된 표본값은 눌러 둔다)
    assert md.sigma(0) == md.sigma(6) == 1.0 and md.sigma(18) == pytest.approx(1.25)
    assert md.sigma(54) == pytest.approx(1.5) and md.sigma(100) > md.sigma(78)
    assert md.sigma(30, season="DJF") == 2.0 and md.sigma(30, month=1) == 2.0 and md.sigma(30, season="JJA") == 1.5
    md2 = RL.WindErrorModel([6, 30], [1.0, 2.0], scale=1.5)
    assert md2.sigma(6) == 1.5 and md2.sigma(6, scaled=False) == 1.0
    assert [RL.season_of(m) for m in (12, 1, 4, 7, 10)] == ["DJF", "DJF", "MAM", "JJA", "SON"]
    with pytest.raises(ValueError):
        RL.season_of(13)


def test_sigma_model_load_default_and_file(tmp_path):
    d = RL.WindErrorModel.load(tmp_path / "none.json")
    assert d.sigma(6) > 0 and d.sigma(78) > d.sigma(6) and "default" in d.source
    p = tmp_path / "m.json"
    p.write_text(json.dumps(dict(leads_h=[6, 30], sigma=[0.7, 1.4], by_season={"SON": [0.5, 1.0]}, scale=2.0, source="t")))
    m = RL.WindErrorModel.load(p)
    assert m.sigma(6) == pytest.approx(1.4) and m.sigma(30, season="SON") == pytest.approx(2.0) and m.to_dict()["scale"] == 2.0


def test_quantile_treats_infeasible_as_worst():
    v = [10.0, 11.0, 12.0, 13.0]
    assert RL._quantile(v, 0, 50) == pytest.approx(11.5)
    assert RL._quantile(v, 4, 50) == pytest.approx(13.0) or math.isinf(RL._quantile(v, 4, 50))
    assert math.isinf(RL._quantile(v, 4, 95)) and math.isinf(RL._quantile([], 3, 50))


@needs_data
def test_confidence_zero_sigma_is_nominal_and_quantiles_ordered(case):
    c = case
    from dpo.energy import soc_drop
    nom = simulate(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"])
    z = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 0.0, n=10, e_fixed=5000.0)
    want = float(soc_drop(nom["e"][-1] + 5000.0, c["d"]))
    assert z["nominal"]["soc_used"] == pytest.approx(want) and z["infeasible"] == 0.0
    assert all(z["soc_used_q"][lv] == pytest.approx(want) for lv in RL.LEVELS) and z["mean_soc_used"] == pytest.approx(want)
    a = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 1.0, n=60, seed=3)
    b = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 3.0, n=60, seed=3)
    for x in (a, b):
        q = [x["soc_used_q"][lv] for lv in RL.LEVELS]
        assert all(v2 >= v1 for v1, v2 in zip(q, q[1:])) and 0.0 <= x["p_ok"] <= 1.0
        assert all(x["soc_end_q"][lv] == pytest.approx(100.0 - x["soc_used_q"][lv]) for lv in RL.LEVELS if math.isfinite(x["soc_used_q"][lv]))
    # σ 가 크면 분포가 넓어진다 (같은 난수)
    assert b["soc_used_q"][95] - b["soc_used_q"][50] > a["soc_used_q"][95] - a["soc_used_q"][50]
    assert b["soc_used_q"][95] > a["soc_used_q"][95] > a["nominal"]["soc_used"] - 1e-9
    # 같은 씨앗이면 결과가 같다
    a2 = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 1.0, n=60, seed=3)
    assert a2["soc_used_q"] == a["soc_used_q"]


@needs_data
def test_adapt_not_worse_than_fixed_commands(case):
    c = case
    f = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 3.0, n=60, seed=5, adapt=False, return_samples=True)
    a = RL.mission_confidence(c["site"], c["d"], c["path"], c["waits"], c["t0"], c["m"], 3.0, n=60, seed=5, adapt=True, return_samples=True)
    assert a["infeasible"] <= f["infeasible"] + 1e-12
    if a["infeasible"] == f["infeasible"] == 0.0:                     # 표본마다: 속도를 다시 정하면 같은 바람에서 더 쓰지 않는다
        assert (a["samples"] <= f["samples"] + 1e-6).all() and a["mean_soc_used"] <= f["mean_soc_used"] + 1e-9


def _conf(nominal_end, p_ok, q95_end, reserve=20.0):
    return dict(reserve=reserve, p_ok=p_ok, nominal=dict(soc_end=nominal_end, soc_used=100 - nominal_end, e=0.0),
                soc_end_q={50: nominal_end, 90: q95_end + 1, 95: q95_end, 99: q95_end - 2},
                soc_used_q={50: 100 - nominal_end, 90: 99 - q95_end, 95: 100 - q95_end, 99: 102 - q95_end})


def test_go_no_go_thresholds():
    assert RL.go_no_go(_conf(40.0, 1.0, 35.0))["decision"] == "go"
    assert RL.go_no_go(_conf(24.0, 0.80, 18.0), level=0.95)["decision"] == "caution"
    assert RL.go_no_go(_conf(24.0, 0.80, 18.0), level=0.75)["decision"] == "go"
    assert RL.go_no_go(_conf(18.0, 0.30, 10.0))["decision"] == "no-go"
    assert RL.go_no_go(_conf(21.0, 0.45, 12.0))["decision"] == "no-go"            # 성립 확률이 절반 미만
    # 예비를 바꿔 다시 따지면 분위수를 쓴다
    assert RL.go_no_go(_conf(40.0, 1.0, 35.0), reserve=36.0)["decision"] == "caution"
    assert RL.go_no_go(_conf(40.0, 1.0, 35.0), reserve=30.0)["decision"] == "go"
    r = RL.go_no_go(_conf(40.0, 1.0, 35.0))
    assert "95" in r["reason"] and r["level"] == 0.95


@needs_data
def test_robust_choice_picks_lowest_quantile(case):
    c = case
    confs = {"a": _conf(40.0, 1.0, 30.0), "b": _conf(42.0, 1.0, 25.0)}       # b 는 명목이 좋지만 95 % 분위는 a 가 낫다
    r = RL.robust_choice(None, level=0.95, confs=confs)
    assert r["chosen"] == "a" and r["by"]["a"] < r["by"]["b"] and r["nominal"]["b"] < r["nominal"]["a"]
    with pytest.raises(ValueError):
        RL.robust_choice(None, level=0.8, confs=confs)
    cand = {"energy": (c["path"], c["waits"]), "shortest": (c["short"], np.zeros(len(c["short"])))}
    r2 = RL.robust_choice(cand, level=0.9, site=c["site"], drone=c["d"], t_start=c["t0"], mass=c["m"], sigma=1.5, n=40, seed=2)
    assert r2["chosen"] == min(r2["by"], key=r2["by"].get) and set(r2["confs"]) == {"energy", "shortest"}


def test_seasonal_recommendation_all_months_and_hours(tmp_path):
    for mth in range(1, 13):
        for h in range(9, 21):
            r = RL.seasonal_recommendation(mth, h, table={})
            assert r["month"] == mth and r["season"] == RL.season_of(mth) and r["setting"] and "default" in r["source"]
    tab = dict(source="t", default_site="s", sites={"s": dict(months={str(m): dict(
        season=RL.season_of(m), wind_ms=3.0 + m, wind_class="중", setting="에너지 탐색 + 안전장치 + 속도 갱신 30 s", saving_vs_baseline_pct=-15.0,
        saving_vs_shortest_pct=-2.0, margin95_pct=1.0 + 0.1 * m, sigma6=0.8, best_hours=[9, 12],
        hours={"9": dict(wind_ms=2.0, wind_class="약"), "15": dict(wind_ms=9.0, wind_class="강")}, note="n") for m in range(1, 13)})})
    for mth in range(1, 13):
        for h in range(9, 21):
            r = RL.seasonal_recommendation(mth, h, table=tab)
            assert r["margin95_pct"] == pytest.approx(1.0 + 0.1 * mth) and r["best_hours"] == [9, 12]
            assert r["wind_class"] == ("약" if h <= 12 else "강") and "s" in r["source"]
    real = RL.load_seasonal_table()
    if real:                                                            # 실제 표가 있으면 모든 월·시각에 답한다
        for mth in range(1, 13):
            for h in (9, 12, 15, 18):
                r = RL.seasonal_recommendation(mth, h, table=real)
                assert r["setting"] and (r["margin95_pct"] is None or r["margin95_pct"] >= 0)
