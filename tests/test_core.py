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


def test_fixed_airspeed_never_beats_speed_optimization():
    import dataclasses
    prm_fix = dataclasses.replace(DR, va_fix=12.0).params()
    for wu in (-6.0, 0.0, 6.0):
        e_opt, _ = edge_energy_per_m(25.0, 0, 0, wu, 2.0, 3.1, 1.225, PRM, DR.va_max, DR.vz_up, DR.vz_dn, SPD)
        e_fix, _ = edge_energy_per_m(25.0, 0, 0, wu, 2.0, 3.1, 1.225, prm_fix, DR.va_max, DR.vz_up, DR.vz_dn, SPD)
        assert e_opt <= e_fix * 1.001


def test_layered_lut_planner_avoids_wall_and_3d_shortcut_helps():
    from dpo.layered import build_lut, _search_layered, SLOPES
    nz, ny, nx = 3, 30, 30
    free = np.ones((nz, ny, nx), bool)
    free[:, 5:25, 15] = False
    lin_U = np.tile(np.array([[2.0, 3.0, 4.0]]), (2, 1))   # 고도별 균일·선형 바람
    lin_V = np.zeros_like(lin_U)
    E, G, EV, GV, ED, GD, ES, GS = build_lut(lin_U, lin_V, np.full(nz, 1.225), 3.1, DR, dz=20.0, dx_h=25.0)
    e_lb = float(min(E[np.isfinite(E)].min(), ED[np.isfinite(ED)].min()))
    tax = np.array([0.0, 3600.0])
    gid = (0 * ny + 15) * nx + 27
    res = {}
    for aa in (False, True):
        g, tt, par, n = _search_layered(np.array([0, 15, 2]), np.array([0, 15, 27]), 0.0, free, E, G, EV, GV, ED, GD,
                                        ES, GS, SLOPES, tax, 25.0, 20.0, e_lb, aa, 400.0, True, True)
        assert g[gid] < 1e29
        p = _path(par, ny, nx, gid)
        assert all(free.reshape(-1)[u] for u in p)
        res[aa] = (g[gid], len(p))
    assert res[True][1] < res[False][1]
    assert res[True][0] <= res[False][0] * 1.02


class _ToySite:
    """시공간 테스트용 최소 site."""
    def __init__(self, nz=1, ny=30, nx=40):
        self.free = np.ones((nz, ny, nx), bool)
        self.nz, self.ny, self.nx = nz, ny, nx
        self.dx, self.dz = 25.0, 20.0
        self.z = 100.0 + 20.0 * np.arange(nz)
        self.U = np.zeros((2, nz, ny, nx), np.float32); self.V = np.zeros_like(self.U)
        self.t_axis = np.array([0.0, 3600.0])
        self.tnfz_id = -np.ones((ny, nx), np.int32); self.tnfz_win = np.zeros((0, 2))
        self.agl = np.full((nz, ny, nx), 80.0)

    def rho_layers(self):
        return np.full(self.nz, 1.225)


def test_interval_table_moving_cylinder_times():
    from dpo.obstacles import DynObstacle, build_interval_table
    s = _ToySite()
    # x=0→1000 m 를 0→100 s 에 이동 (10 m/s), y=375 m 고정, 반경 50 m
    o = DynObstacle(kind="cylinder", radius=50, z_min=0, z_max=500, margin=0,
                    path=[dict(x=0, y=375, t=0), dict(x=1000, y=375, t=100)])
    T = build_interval_table(s, [o], t_offset=0.0)
    n = (0 * s.ny + 15) * s.nx + 20            # 셀 중심 (512.5, 387.5)
    q = T["off"][n]
    assert q >= 0 and T["cnt"][n] == 1
    lo, hi = T["lo"][q], T["hi"][q]
    # |512.5 - 10t| ≤ 50 + 0.71*25 (보수적) → t ≈ 44.5 ~ 58.0
    assert 43 < lo < 47 and 56 < hi < 60


def test_st_planner_waits_for_crossing_wall():
    from dpo.obstacles import DynObstacle
    from dpo.st_planner import STPlanner
    s = _ToySite()
    wall = DynObstacle(kind="box", half_x=40, half_y=1000, z_min=0, z_max=500, margin=0,
                       path=[dict(x=500, y=375, t=0)], t_start=0, t_end=120)   # 지도 전체 높이를 막는 벽 (0~120 s)
    sp = STPlanner(s, DR)
    sp.set_obstacles([wall], t_offset=0.0)
    r = sp.plan((0, 15, 2), (0, 15, 37), t_start=0.0, mass=3.1, max_wait=300.0, heading_heuristic=False)
    assert r is not None
    assert r["t_arrive"] > 120            # 벽이 사라진 뒤 통과
    r2 = sp.plan((0, 15, 2), (0, 15, 37), t_start=0.0, mass=3.1, max_wait=0.0, heading_heuristic=False)
    assert r2 is None                     # 대기 금지 + 우회 불가 → 허용 범위 내 해 없음


def _toy_full_site():
    s = _ToySite(nz=2, ny=30, nx=40)
    s.ground = np.zeros((s.ny, s.nx))
    s.wind_at = lambda t, k, j, i: (0.0, 0.0)
    s.lowest_free_k = lambda x, y: (0, int(y // s.dx), int(x // s.dx))
    return s


def test_timeline_truth_clips_revisions():
    from dpo.obstacles import DynObstacle
    from dpo.st_rolling import ObstacleTimeline
    a = DynObstacle(name="A", kind="cylinder", radius=50, path=[dict(x=100, y=100, t=0)])
    b = DynObstacle(name="A", kind="cylinder", radius=50, path=[dict(x=900, y=100, t=0)])
    tl = ObstacleTimeline([(0.0, [a]), (40.0, [b])])
    tr = tl.truth()
    assert len(tr) == 2
    assert tr[0].window() == (0.0, 40.0) and tr[1].window()[0] == 40.0
    assert tl.known_at(10.0)[0].path[0]["x"] == 100 and tl.known_at(41.0)[0].path[0]["x"] == 900


def test_event_replanning_avoids_announced_obstacle():
    from dpo.obstacles import DynObstacle
    from dpo.st_rolling import ObstacleTimeline, run_obstacle_replanning
    s = _toy_full_site()
    # 출발 10 s 후 경로 중앙(약 x=500~600 m)을 덮는 정지 원기둥이 '발표'되고 10~300 s 존재
    blk = DynObstacle(name="돌발", kind="cylinder", radius=120, z_min=0, z_max=1000, margin=0,
                      path=[dict(x=560, y=387.5, t=0)], t_start=10.0, t_end=300.0)
    tl = ObstacleTimeline([(0.0, []), (10.0, [blk])])
    a, b = (62.5, 387.5), (937.5, 387.5)
    once = run_obstacle_replanning(s, s, DR, a, b, 0.0, 3.1, tl, policy="once", latency=1.0, weight=1.0)
    ev = run_obstacle_replanning(s, s, DR, a, b, 0.0, 3.1, tl, policy="event", latency=1.0, weight=1.0)
    assert once["conflicts"]["n_collision_samples"] > 0
    assert ev["conflicts"]["n_collision_samples"] == 0
    assert ev["n_replans"] == 1
