"""기존 경로 대비 우수성(E13)과 갱신 규칙(E14)에 쓰는 도우미 시험. 실행: PYTHONPATH=src pytest -q"""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from dpo import config as C, drones as DR
from dpo.mission import make_flyable, plan_best_of_two
from dpo.planner import Planner, simulate
from dpo.wind_field import Site3D

sys.path.insert(0, "scripts")


def _site():
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    return Site3D(g, f0, np.datetime64("2026-10-27T00:00"))


HAVE_DATA = (C.PROC / "site_grid.npz").exists() and (C.RAW / "gfs_20261027_00z.npz").exists()


def test_make_flyable_splits_steep_segments():
    d = DR.x650()
    site = SimpleNamespace(dx=25.0, dz=20.0, free=np.ones((12, 20, 20), bool))
    # 완만한 구간(20 m 오르며 250 m 전진)은 그대로, 급한 하강(100 m 내리며 50 m 전진)은 '수평 먼저, 그다음 수직'으로 나눈다
    p = make_flyable(site, d, [(5, 0, 0), (6, 0, 10), (1, 0, 12)])
    assert [tuple(int(v) for v in q) for q in p] == [(5, 0, 0), (6, 0, 10), (6, 0, 12), (1, 0, 12)]
    # 급한 상승은 '수직 먼저'
    p = make_flyable(site, d, [(1, 0, 0), (6, 0, 2)])
    assert [tuple(int(v) for v in q) for q in p] == [(1, 0, 0), (6, 0, 0), (6, 0, 2)]
    # 꺾는 점이 막혀 있으면 고칠 수 없다
    site.free[6, 0, 0] = False
    assert make_flyable(site, d, [(1, 0, 0), (6, 0, 2)]) is None


def test_cluster_ci_and_compare():
    from exp_superiority import _cluster_ci, compare
    rows = []
    for day, gain in (("a", 0.8), ("b", 0.9), ("c", 0.85), ("d", 0.8), ("e", 0.9)):
        for q in range(4):
            rows.append(dict(site="s", day=day, A=dict(soc=20.0 + q, T=200.0), B=dict(soc=(20.0 + q) * gain, T=180.0)))
    rows.append(dict(site="s", day="a", A=None, B=dict(soc=1.0, T=1.0)))      # 한쪽이 비행 불가면 비교에서 뺀다
    c = compare(rows, "A", "B")
    assert c["n"] == 20 and c["n_days"] == 5 and c["win"] == 1.0 and c["lose"] == 0.0
    assert c["mean_pct"] == pytest.approx(-15.0, abs=1e-6) and c["time_pct"] == pytest.approx(-10.0)
    assert c["ci"][0] <= c["mean_pct"] <= c["ci"][1] and -20.0 - 1e-9 <= c["ci"][0] and c["ci"][1] <= -10.0 + 1e-9
    assert c["wilcoxon_days_p"] == pytest.approx(0.0625)                       # 5일이 모두 같은 방향일 때의 최소 p
    lo, hi = _cluster_ci([1.0, 1.0, 3.0, 3.0], ["x", "x", "y", "y"])
    assert lo == 1.0 and hi == 3.0                                             # 날짜 단위로 뽑으므로 구간이 날짜 평균의 범위


@pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
def test_best_of_two_never_worse_than_flyable_shortest():
    site = _site()
    d = DR.x650()
    P = Planner(site, d)
    m = d.m_empty + 1.0
    from exp_superiority import od_pairs
    t0 = 6 * 3600.0
    for a, b in od_pairs(site, 3):
        st, gl = site.lowest_free_k(*a), site.lowest_free_k(*b)
        r = plan_best_of_two(P, st, gl, t0, m)
        assert r is not None and r["chosen"] in r["cand"] and r["pred_e"] == pytest.approx(min(r["cand"].values()))
        sim = simulate(site, d, r["path"], r["wait_before"], t0, m)
        assert sim is not None and sim["e"][-1] == pytest.approx(r["pred_e"], rel=1e-9)
        if "shortest" in r["cand"]:
            assert r["pred_e"] <= r["cand"]["shortest"] + 1e-9


@pytest.mark.skipif(not HAVE_DATA, reason="지형·바람 자료 없음")
def test_update_rules_frozen_keep():
    import copy
    import math
    import exp_update_period as X
    from exp_superiority import od_pairs
    site = _site()
    d = DR.m100()
    P = Planner(site, d)
    m = d.m_empty + 0.5
    a, b = od_pairs(site, 2)[0]
    t0 = 6 * 3600.0
    # 예보 = 실제: 갱신해도 잴 오차가 없으므로 '더 나을 때만 교체'는 경로를 바꾸지 않고, 갱신 없는 비행과 같은 값을 낸다
    f0 = X.fly(P, site, d, a, b, t0, m, 1e9, "frozen")
    k0 = X.fly(P, site, d, a, b, t0, m, 60.0, "keep")
    assert f0 is not None and k0 is not None and k0["n_upd"] >= 1
    assert k0["soc"] == pytest.approx(f0["soc"], rel=0.01)
    # 실제 풍향이 예보와 90° 다르면: 계획대로 나는 쪽이 실제 바람을 미리 안 계획보다 나쁘지 않을 수 없다 (같은 평가)
    tr = copy.copy(site)
    th = math.radians(90.0)
    tr.U = (site.U * math.cos(th) - site.V * math.sin(th)).astype(site.U.dtype)
    tr.V = (site.U * math.sin(th) + site.V * math.cos(th)).astype(site.V.dtype)
    f1 = X.fly(P, tr, d, a, b, t0, m, 1e9, "frozen")
    assert f1 is not None and np.isfinite(f1["soc"]) and f1["n_upd"] == 0
    assert np.allclose(site.U, P.site.U)                   # 보정한 예보는 비행 뒤 원래대로 되돌린다
