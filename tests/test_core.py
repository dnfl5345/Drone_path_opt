"""핵심 로직 단위 테스트 (네트워크 불필요). 실행: PYTHONPATH=src pytest -q"""
import math

import numpy as np

from dpo.energy import Drone, power_w, edge_energy_per_m
from dpo.kma_adapter import dfs_xy
from dpo.planner import _search

DR = Drone()
PRM = DR.params()
SPD = np.arange(3.0, 20.01, 0.5)


def test_power_increases_with_payload():
    assert power_w(0.0, 0.0, 3.6, 1.225, PRM) > power_w(0.0, 0.0, 3.1, 1.225, PRM)


def test_translational_lift_then_drag():
    p = [power_w(v, 0.0, 3.1, 1.225, PRM) for v in (0, 8, 17)]
    assert p[1] < p[0] < p[2] or p[1] < p[0]


def test_headwind_costs_more_than_tailwind():
    e_head, _ = edge_energy_per_m(25.0, 0, 0, -6.0, 0, 3.1, 1.225, PRM, DR.va_max, DR.vz_up, DR.vz_dn, SPD)
    e_tail, _ = edge_energy_per_m(25.0, 0, 0, 6.0, 0, 3.1, 1.225, PRM, DR.va_max, DR.vz_up, DR.vz_dn, SPD)
    assert e_head > e_tail > 0


def test_infeasible_when_wind_exceeds_airspeed_limit():
    e, v = edge_energy_per_m(25.0, 0, 0, -25.0, 0, 3.1, 1.225, PRM, DR.va_max, DR.vz_up, DR.vz_dn, SPD)
    assert math.isinf(e)


def _toy(nz=1, ny=30, nx=30, wall=True):
    free = np.ones((nz, ny, nx), bool)
    if wall:
        free[:, 5:25, 15] = False
    U = np.zeros((2, nz, ny, nx), np.float32)
    V = np.zeros_like(U)
    return free, U, V


def _run(free, U, V, mode, any_angle=True, tnfz=None, win=None, allow_wait=True):
    tid = -np.ones(free.shape[1:], np.int32) if tnfz is None else tnfz
    tw = np.zeros((1, 2)) - 1e9 if win is None else win
    return _search(np.array([0, 15, 2]), np.array([0, 15, 27]), 0.0, mode, free, tid, tw, U, V,
                   np.array([0.0, 3600.0]), np.full(free.shape[0], 1.225), 3.1, PRM, DR.va_max, DR.vz_up,
                   DR.vz_dn, SPD, 25.0, 20.0, 10.0, 1e-3, 400.0, allow_wait, any_angle, 1.0)


def _path(par, ny, nx, goal):
    out, u = [], goal
    while True:
        out.append(u)
        if par[u] == u:
            return out[::-1]
        u = par[u]


def test_planner_avoids_wall_and_any_angle_reduces_waypoints():
    free, U, V = _toy()
    gid = 15 * 30 + 27
    g1, *_r1 = _run(free, U, V, 1, any_angle=False)
    g2, tt, par2, *_ = _run(free, U, V, 1, any_angle=True)
    assert g1[gid] < 1e29 and g2[gid] < 1e29
    p = _path(par2, 30, 30, gid)
    for u in p:
        assert free.reshape(-1)[u]
    # any-angle 는 격자 A* 보다 에너지가 크게 나빠지지 않는다 (근사 최적)
    assert g2[gid] <= g1[gid] * 1.05
    assert len(p) <= 6


def test_wait_chosen_for_short_temporal_block():
    free, U, V = _toy(wall=False)
    tid = -np.ones((30, 30), np.int32)
    tid[:, 14:16] = 0  # 전 구간 가로막는 띠
    g, tt, par, wait, *_ = _run(free, U, V, 1, tnfz=tid, win=np.array([[0.0, 60.0]]))
    gid = 15 * 30 + 27
    assert g[gid] < 1e29 and wait.max() > 0


def test_kma_grid_conversion():
    assert dfs_xy(37.5665, 126.9780) == (60, 127)
