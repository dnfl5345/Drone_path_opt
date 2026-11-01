"""전체 실험 실행: 보정 → 실제 지도 최단/최적 경로 → 출발시각·탑재중량·금지구역·재계획.

사용: PYTHONPATH=src python scripts/run_experiments.py [E0 E1 ...]
결과: results/*.pkl, results/summary.json
"""
from __future__ import annotations

import json
import pickle
import sys
import time

import numpy as np

from dpo import config as C
from dpo.energy import Drone, load_cmu, calibrate, predict_flight_energy, metrics, soc_drop
from dpo.wind_field import Site3D
from dpo.planner import Planner, simulate, path_length
from dpo.mission import Mission, run_round_trip, run_rolling, _leg

T0 = np.datetime64("2026-10-27T00:00")   # 00 UTC = 09:00 KST
KST = 9
HUB = (2700.0, 3000.0)                   # 물류 거점 (판교 테크노밸리 인근)
DST = (4600.0, 1500.0)                   # 배송지 (분당 정자·수내 방면, 탄천 건너편)
NFZ = (3650.0, 2250.0, 250.0)            # 예시 비행금지구역 (가상, 반경 250 m)
T_MAIN = 6 * 3600.0                      # 15:00 KST 출발 (강풍 시간대)
PAYLOAD = 0.5


def save(name, obj):
    with open(C.RES / f"{name}.pkl", "wb") as f:
        pickle.dump(obj, f)


def load_summary():
    p = C.RES / "summary.json"
    return json.loads(p.read_text()) if p.exists() else {}


def put_summary(key, val):
    s = load_summary()
    s[key] = val
    (C.RES / "summary.json").write_text(json.dumps(s, indent=1, ensure_ascii=False, default=float))


def sites():
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    f6 = dict(np.load(C.RAW / "gfs_20261027_06z.npz", allow_pickle=True))
    fc = Site3D(g, f0, T0)       # 계획용: 00Z 사이클 예보 (09~21 KST)
    tr = Site3D(g, f6, T0)       # 검증용 '실제': 06Z 사이클 분석/단기예보 (15~21 KST)
    for s in (fc, tr):
        s.add_static_nfz_circle(*NFZ)
    return g, fc, tr


def drone_calibrated():
    p = C.RES / "drone_params.json"
    if p.exists():
        return Drone(**{k: v for k, v in json.loads(p.read_text()).items() if k in Drone.__dataclass_fields__})
    return E0()


# ------------------------------------------------------------------ E0 에너지 모델 보정/검증
def E0():
    d = load_cmu()
    d = d[d.route.str.match(r"^(R\d|H)$") & (d.total_energy_j > 10000)].sort_values("flight")
    dates = sorted(d.date.unique())
    test_dates = dates[1::3]
    tr, te = d[~d.date.isin(test_dates)], d[d.date.isin(test_dates)]
    dr = calibrate(tr)
    yh_tr, yh_te = predict_flight_energy(tr, dr), predict_flight_energy(te, dr)
    # 기준 모델: 거리 비례 / 시간 비례
    A = np.c_[tr.dist_m, np.ones(len(tr))]
    cd = np.linalg.lstsq(A, tr.total_energy_j, rcond=None)[0]
    ct = np.linalg.lstsq(np.c_[tr.duration_s], tr.total_energy_j, rcond=None)[0]
    # 온라인 보정(당일 첫 2회 비행으로 배율 추정)
    te2 = te.assign(yh=yh_te)
    ys, yhs, yha = [], [], []
    for _, gg in te2.groupby("date"):
        k = gg.total_energy_j.values[:2].sum() / gg.yh.values[:2].sum()
        r = gg.iloc[2:]
        ys += list(r.total_energy_j); yhs += list(r.yh); yha += list(r.yh * k)
    res = dict(
        n_train=len(tr), n_test=len(te), train_dates=len(dates) - len(test_dates), test_dates=len(test_dates),
        params=dict(eta=dr.eta, p0=dr.p0, cda=dr.cda, p_av=dr.p_av, m_empty=dr.m_empty),
        train=metrics(tr.total_energy_j.values, yh_tr), test=metrics(te.total_energy_j.values, yh_te),
        base_dist=metrics(te.total_energy_j.values, np.c_[te.dist_m, np.ones(len(te))] @ cd),
        base_time=metrics(te.total_energy_j.values, np.c_[te.duration_s] @ ct),
        adaptive_static=metrics(np.array(ys), np.array(yhs)), adaptive=metrics(np.array(ys), np.array(yha)),
    )
    save("E0", dict(res=res, te=te.assign(yh=yh_te), tr=tr.assign(yh=yh_tr), drone=dr.to_dict()))
    (C.RES / "drone_params.json").write_text(json.dumps(dr.to_dict(), indent=1, ensure_ascii=False))
    put_summary("E0_calibration", res)
    print(json.dumps(res, indent=1, default=float))
    return dr


# ------------------------------------------------------------------ E1 실제 지도: 최단 vs 최적
METHODS = {
    "A_shortest_v10": dict(mode=0, speed_policy="const", vconst=10.0, label="최단(Lazy Theta*)+정속 10 m/s"),
    "B_shortest_vopt": dict(mode=0, speed_policy="opt", label="최단(Lazy Theta*)+속도최적화"),
    "C_energy_Astar": dict(mode=1, speed_policy="opt", any_angle=False, label="에너지 A*(26방향)"),
    "D_WE_LTstar": dict(mode=1, speed_policy="opt", label="제안 WE-LT*"),
}


def _strip(rt):
    """pickle 용량 축소용."""
    out = {}
    for leg in ("out", "back"):
        L = rt[leg]
        out[leg] = dict(path=L["plan"]["path"], wait=L["plan"]["wait_before"], sim=L["sim"],
                        e_total=L["e_total"], length=L["length"], cpu=L["plan"]["cpu_s"],
                        n_exp=L["plan"]["n_expanded"], t_end=L["t_end"])
    for k in ("E", "soc_used", "soc_end", "feasible", "t_total", "soc_out", "soc_back", "L"):
        out[k] = rt[k]
    return out


def E1():
    g, fc, tr = sites()
    dr = drone_calibrated()
    P = Planner(fc, dr)
    rows, store = [], {}
    for kind in ("delivery", "pickup"):
        for key, cfg in METHODS.items():
            cfg = dict(cfg)
            label = cfg.pop("label")
            t = time.time()
            rt = run_round_trip(P, Mission(HUB, DST, PAYLOAD, kind, T_MAIN), site_truth=tr, **cfg)
            # 계획 단계(예보) 예측 SOC
            rp = run_round_trip(P, Mission(HUB, DST, PAYLOAD, kind, T_MAIN), site_truth=fc, **cfg)
            store[(kind, key)] = _strip(rt)
            nwp = len(rt["out"]["plan"]["path"]) + len(rt["back"]["plan"]["path"])
            rows.append(dict(kind=kind, method=key, label=label, soc_used=rt["soc_used"], soc_pred=rp["soc_used"],
                             soc_out=rt["soc_out"], soc_back=rt["soc_back"], L=rt["L"], t_total=rt["t_total"],
                             feasible=bool(rt["feasible"]), waypoints=nwp,
                             cpu=rt["out"]["plan"]["cpu_s"] + rt["back"]["plan"]["cpu_s"], wall=time.time() - t))
            print(rows[-1])
    save("E1", dict(rows=rows, store=store))
    put_summary("E1_main", rows)


# ------------------------------------------------------------------ E2 출발시각에 따른 경로/SOC
def E2():
    g, fc, tr = sites()
    dr = drone_calibrated()
    P = Planner(fc, dr)
    rows, paths = [], {}
    for h in range(0, 12):
        for kind in ("delivery", "pickup"):
            for key in ("A_shortest_v10", "D_WE_LTstar"):
                cfg = dict(METHODS[key]); cfg.pop("label")
                rt = run_round_trip(P, Mission(HUB, DST, PAYLOAD, kind, h * 3600.0), site_truth=fc, **cfg)
                if rt is None:
                    rows.append(dict(hour=h + KST, kind=kind, method=key, soc_used=np.nan, feasible=False))
                    continue
                k = int(np.searchsorted(fc.t_axis, h * 3600.0))
                ws100 = float(np.hypot(fc.U[k], fc.V[k])[fc.free].mean())
                rows.append(dict(hour=h + KST, kind=kind, method=key, soc_used=rt["soc_used"],
                                 soc_out=rt["soc_out"], soc_back=rt["soc_back"], feasible=bool(rt["feasible"]),
                                 L=rt["L"], t_total=rt["t_total"], ws_mean=ws100))
                if key == "D_WE_LTstar":
                    paths[(h + KST, kind)] = (rt["out"]["plan"]["path"], rt["back"]["plan"]["path"])
                print(rows[-1])
    save("E2", dict(rows=rows, paths=paths))
    put_summary("E2_departure", rows)


# ------------------------------------------------------------------ E3 탑재중량 민감도
def E3():
    g, fc, tr = sites()
    dr = drone_calibrated()
    P = Planner(fc, dr)
    rows = []
    for pl in (0.0, 0.25, 0.5, 0.75):
        for kind in ("delivery", "pickup"):
            for key in ("A_shortest_v10", "D_WE_LTstar"):
                cfg = dict(METHODS[key]); cfg.pop("label")
                rt = run_round_trip(P, Mission(HUB, DST, pl, kind, T_MAIN), site_truth=tr, **cfg)
                rows.append(dict(payload=pl, kind=kind, method=key, soc_used=rt["soc_used"], soc_out=rt["soc_out"],
                                 soc_back=rt["soc_back"], feasible=bool(rt["feasible"])))
                print(rows[-1])
    save("E3", dict(rows=rows))
    put_summary("E3_payload", rows)


# ------------------------------------------------------------------ E4 호버링 vs 우회 (장애물 시간표)
def E4():
    g, fc, tr = sites()
    dr = drone_calibrated()
    P = Planner(fc, dr)
    m = dr.m_empty + PAYLOAD
    st, gl = fc.lowest_free_k(*HUB), fc.lowest_free_k(*DST)
    base = P.plan(st, gl, t_start=T_MAIN, mass=m, mode=1)
    sim = simulate(fc, dr, base["path"], base["wait_before"], T_MAIN, m)
    # 출발 후 ~80 s 지점을 가로지르는 띠형 임시 비행제한(폭 150 m × 길이 2.4 km) – 장애물 시간표
    i = int(np.searchsorted(sim["t"] - T_MAIN, 80.0))
    cx, cy = float(sim["x"][i]), float(sim["y"][i])
    hx, hy = np.gradient(sim["x"])[i], np.gradient(sim["y"])[i]
    nrm = np.hypot(hx, hy); ux, uy = hx / nrm, hy / nrm      # 진행방향
    px, py = -uy, ux                                         # 수직방향
    Lh, Wh = 1200.0, 75.0
    band = [(cx + px * Lh + ux * Wh, cy + py * Lh + uy * Wh), (cx - px * Lh + ux * Wh, cy - py * Lh + uy * Wh),
            (cx - px * Lh - ux * Wh, cy - py * Lh - uy * Wh), (cx + px * Lh - ux * Wh, cy + py * Lh - uy * Wh)]
    from matplotlib.path import Path as MPath
    inside = MPath(np.array(band)).contains_points(np.c_[sim["x"], sim["y"]])
    t_enter = float(sim["t"][np.argmax(inside)] - T_MAIN)
    out = dict(base=dict(path=base["path"], sim=sim, E=base["cost"]), band=band, t_enter=t_enter, cases={})
    for name, extra in (("short", 30.0), ("long", 300.0)):
        fc.clear_temporal_nfz()
        fc.add_temporal_nfz_polygon(band, T_MAIN, T_MAIN + t_enter + extra)
        res = {}
        for allow in (True, False):
            r = P.plan(st, gl, t_start=T_MAIN, mass=m, mode=1, allow_wait=allow)
            s2 = simulate(fc, dr, r["path"], r["wait_before"], T_MAIN, m)
            res["wait" if allow else "nowait"] = dict(path=r["path"], wait=r["wait_before"], sim=s2, E=r["cost"],
                                                      total_wait=float(r["wait_before"].sum()),
                                                      L=path_length(fc, r["path"]), T=float(s2["t"][-1] - T_MAIN))
            print(name, allow, r["cost"], r["wait_before"].sum(), path_length(fc, r["path"]))
        out["cases"][name] = dict(window=(T_MAIN, T_MAIN + t_enter + extra), **res)
    # 금지 시간창 길이에 따른 손익분기 곡선
    sweep = []
    for extra in (0.0, 3.0, 6.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 300.0):
        fc.clear_temporal_nfz()
        fc.add_temporal_nfz_polygon(band, T_MAIN, T_MAIN + t_enter + extra)
        rw = P.plan(st, gl, t_start=T_MAIN, mass=m, mode=1, allow_wait=True)
        rn = P.plan(st, gl, t_start=T_MAIN, mass=m, mode=1, allow_wait=False)
        sweep.append(dict(extra=extra, E_wait=rw["cost"], wait=float(rw["wait_before"].sum()), E_nowait=rn["cost"],
                          L_wait=path_length(fc, rw["path"]), L_nowait=path_length(fc, rn["path"])))
        print(sweep[-1])
    out["sweep"] = sweep
    fc.clear_temporal_nfz()
    save("E4", out)
    put_summary("E4_hover_vs_detour", {k: dict(window_s=v["window"][1] - v["window"][0],
                                              E_wait_option=v["wait"]["E"], wait_s=v["wait"]["total_wait"],
                                              E_detour_only=v["nowait"]["E"], L_wait=v["wait"]["L"],
                                              L_detour=v["nowait"]["L"]) for k, v in out["cases"].items()})


# ------------------------------------------------------------------ E5 롤링 호라이즌 재계획
def E5():
    g, fc, tr = sites()
    dr = drone_calibrated()
    P = Planner(fc, dr)
    m = dr.m_empty  # 회수 임무 귀환: 무거운 쪽은 별도 – 여기서는 배송 후 귀환(역풍) 구간
    a, b = DST, HUB
    t0 = T_MAIN + 300.0
    st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
    # (1) 정적 계획(1회) – 실제 바람에서 비행
    base = P.plan(st, gl, t_start=t0, mass=m, mode=1)
    sim_base = simulate(tr, dr, base["path"], base["wait_before"], t0, m, U=tr.U, V=tr.V, tax=tr.t_axis)
    # 돌발 금지구역: 출발 60 s 후 발표, 계획경로 전방 (출발 후 ~150 s 지점)
    i = int(np.searchsorted(sim_base["t"] - t0, 150.0))
    cx, cy = float(sim_base["x"][i]), float(sim_base["y"][i])

    def popup(site):
        site.add_temporal_nfz_circle(cx, cy, 300.0, t0 + 60.0, t0 + 1800.0)

    events = [(t0 + 60.0, popup)]
    roll = run_rolling(P, tr, dr, a, b, t0, m, dt_replan=60.0, events=events, bias_update=True)
    fc.clear_temporal_nfz()
    roll_nb = run_rolling(P, tr, dr, a, b, t0, m, dt_replan=60.0, events=events, bias_update=False)
    fc.clear_temporal_nfz()
    # 정적 계획이 돌발 금지구역을 침범하는지 판정
    inside = ((sim_base["x"] - cx) ** 2 + (sim_base["y"] - cy) ** 2 <= 300.0 ** 2) & (sim_base["t"] >= t0 + 60)
    out = dict(base=dict(path=base["path"], sim=sim_base, soc=soc_drop(sim_base["e"][-1], dr),
                         violates=bool(inside.any()), pred=soc_drop(base["cost"], dr)),
               roll=roll, roll_nobias=roll_nb, popup=(cx, cy, 300.0, t0 + 60.0), t0=t0)
    save("E5", out)
    put_summary("E5_rolling", dict(static_soc=out["base"]["soc"], static_pred_soc=out["base"]["pred"],
                                   static_violates_popup_nfz=out["base"]["violates"],
                                   rolling_soc=roll["soc"], rolling_nobias_soc=roll_nb["soc"],
                                   n_replans=len(roll["plans"]),
                                   replan_cpu_mean=float(np.mean([q["cpu"] for q in roll["plans"]])),
                                   replan_cpu_max=float(np.max([q["cpu"] for q in roll["plans"]]))))
    print(load_summary()["E5_rolling"])


# ------------------------------------------------------------------ E6 가정 축소 실험 (최신 연구 가정 재현)
ABLATIONS = [
    # key, 라벨, 비교 축 (문헌별 해당 축 처리 방식은 docs/algorithm_comparison.md §3.2 참조)
    ("full", "제안 WE-LT* (전체)", "-"),
    ("static_wind", "시간불변 바람 (출발시각 스냅샷)", "시간가변성 축"),
    ("grid", "격자 탐색 (any-angle 없음)", "탐색공간 축 ([5][6] A*/LPA* 계열)"),
    ("fixed_va14", "고정 대기속도 14 m/s (속도 최적화 없음)", "속도 결정 축"),
    ("fixed_va10", "고정 대기속도 10 m/s", "속도 결정 축"),
    ("fixed_mass", "탑재중량 변화 무시 (왕복 같은 질량으로 계획)", "질량 축"),
    ("terrain_2p5d", "고도 선택 없음 (지형추종 2.5D)", "고도 축"),
    ("combined", "결합: 시간불변 바람 + 격자 + 중량 고정", "3개 축 동시 축소"),
]


def E6():
    import dataclasses
    g, fc, tr = sites()
    dr = drone_calibrated()
    rows = []
    for key, label, ref in ABLATIONS:
        for kind in ("delivery", "pickup"):
            ms = Mission(HUB, DST, PAYLOAD, kind, T_MAIN)
            site, drone, kw = fc, dr, {}
            if key in ("static_wind", "combined"):
                site = fc.frozen_wind(T_MAIN)
            if key in ("grid", "combined"):
                kw["any_angle"] = False
            if key.startswith("fixed_va"):
                drone = dataclasses.replace(dr, va_fix=float(key[-2:]))
            if key in ("fixed_mass", "combined"):
                kw["plan_mass"] = dr.m_empty + PAYLOAD
            if key == "terrain_2p5d":
                site = fc.terrain_following(2)
            P = Planner(site, drone)
            t = time.time()
            rt = run_round_trip(P, ms, site_truth=tr, **kw)
            if rt is None:
                rows.append(dict(key=key, label=label, ref=ref, kind=kind, soc_used=None, feasible=False,
                                 note="계획 실패(바람이 대기속도 한계 초과 구간 존재)"))
            else:
                rows.append(dict(key=key, label=label, ref=ref, kind=kind, soc_used=rt["soc_used"],
                                 soc_out=rt["soc_out"], soc_back=rt["soc_back"], L=rt["L"],
                                 feasible=bool(rt["feasible"]), t_total=rt["t_total"],
                                 wp=len(rt["out"]["plan"]["path"]) + len(rt["back"]["plan"]["path"]),
                                 cpu=rt["out"]["plan"]["cpu_s"] + rt["back"]["plan"]["cpu_s"], note=""))
            print(rows[-1], f"{time.time() - t:.0f}s")
    base = {r["kind"]: r["soc_used"] for r in rows if r["key"] == "full"}
    for r in rows:
        r["d_soc"] = None if r["soc_used"] is None else r["soc_used"] - base[r["kind"]]
    save("E6", dict(rows=rows))
    put_summary("E6_ablation", rows)


# ------------------------------------------------------------------ E7 확장: 8방향 + 고도별 균일 선형 바람
def E7():
    from dpo.layered import fit_linear_profile, linear_wind_site, LayeredPlanner, unit_ball_convexity, breakeven_climb
    g, fc, tr = sites()
    dr = drone_calibrated()
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    lat_c, lon_c = 0.5 * (C.LAT_MIN + C.LAT_MAX), 0.5 * (C.LON_MIN + C.LON_MAX)
    prof = fit_linear_profile(f0, lat_c, lon_c, float(g["ground"].mean()))
    sl = linear_wind_site(fc, prof)
    LP = LayeredPlanner(sl, dr)
    variants = {
        "L8_Astar": (LP, dict(any_angle=False, diag_climb=False), "8방향+수직 A*"),
        "L8_LazyTheta": (LP, dict(any_angle=True, diag_climb=False, aa3d=False), "8방향+수직 + 레이어 내 지름길"),
        "L26_LazyTheta": (LP, dict(any_angle=True, diag_climb=True, aa3d=False), "8방향×상승/수평/하강 + 레이어 내 지름길"),
        "L26_LT3D": (LP, dict(any_angle=True, diag_climb=True, aa3d=True), "8방향×상승/수평/하강 + 3D 지름길 (경사 LUT)"),
        "WELT_linear": (Planner(sl, dr), {}, "WE-LT* 26방향 (선형 바람)"),
        "WELT_full": (Planner(fc, dr), {}, "WE-LT* 26방향 (3D 바람장, 기준)"),
    }
    rows, store = [], {}
    for kind in ("delivery", "pickup"):
        for key, (P, kw, label) in variants.items():
            ms = Mission(HUB, DST, PAYLOAD, kind, T_MAIN)
            t = time.time()
            rt = run_round_trip(P, ms, site_truth=tr, **kw)          # 실제 바람으로 평가
            rp = run_round_trip(P, ms, site_truth=P.site, **kw)      # 계획 모델 자체의 예측
            store[(kind, key)] = dict(out=rt["out"]["plan"]["path"], back=rt["back"]["plan"]["path"])
            rows.append(dict(kind=kind, key=key, label=label, soc_used=rt["soc_used"], soc_pred=rp["soc_used"],
                             feasible=bool(rt["feasible"]), L=rt["L"],
                             wp=len(rt["out"]["plan"]["path"]) + len(rt["back"]["plan"]["path"]),
                             cpu=rt["out"]["plan"]["cpu_s"] + rt["back"]["plan"]["cpu_s"],
                             n_exp=rt["out"]["plan"]["n_expanded"] + rt["back"]["plan"]["n_expanded"]))
            print(rows[-1], f"{time.time() - t:.0f}s")
    # 단위구 볼록성 (15시, 갈 때 질량) / 상승 손익분기
    m_out = dr.m_empty + PAYLOAD
    E, Gs, EV, GV, ED, GD, ES, GS = LP.lut(m_out)
    n6 = int(np.searchsorted(sl.t_axis, T_MAIN))
    conv = {int(k): unit_ball_convexity(E[n6, k]) for k in range(sl.nz) if np.isfinite(E[n6, k]).all()}
    wdir_to = (np.degrees(np.arctan2(sl.lin_U[n6, 4], sl.lin_V[n6, 4])) % 360)
    h_tail = int(round(wdir_to / 5)) % 72
    h_head = (h_tail + 36) % 72
    be = {f"{dzk * 20}m": dict(tail=breakeven_climb(E, EV, n6, 2, 2 + dzk, h_tail, sl.dz),
                               head=breakeven_climb(E, EV, n6, 2, 2 + dzk, h_head, sl.dz)) for dzk in (1, 2, 3, 4)}
    res = dict(rows=rows, r2_profile=prof["r2"].tolist(), shear_u=prof["b_u"].tolist(), shear_v=prof["b_v"].tolist(),
               lut_time=LP.lut_time, convexity=conv, breakeven=be, h_tail_deg=h_tail * 5, n_layers=sl.nz)
    save("E7", dict(**res, prof=prof, store=store, E=E, EV=EV, n6=n6, z=sl.z))
    put_summary("E7_layered", res)
    print({k: v for k, v in res.items() if k != "rows"})


# ------------------------------------------------------------------ E8 3D 시공간: 동적 장애물
def e8_scenario(fc, dr):
    """기준(장애물 무시) 경로를 가로지르도록 시각을 맞춘 예시 시나리오 (scenarios/e8_dynamic.json)."""
    from dpo.st_planner import STPlanner
    from dpo.obstacles import DynObstacle
    m = dr.m_empty + PAYLOAD
    SP = STPlanner(fc, dr)
    st, gl = fc.lowest_free_k(*HUB), fc.lowest_free_k(*DST)
    r0 = SP.plan(st, gl, t_start=T_MAIN, mass=m, ignore_obstacles=True)
    sim0 = simulate(fc, dr, r0["path"], r0["wait_before"], T_MAIN, m)
    at = lambda t: [float(v[int(np.searchsorted(sim0["t"] - T_MAIN, t))]) for v in (sim0["x"], sim0["y"], sim0["z"])]
    x1, y1, z1 = at(35.0); x2, y2, _ = at(70.0); x3, y3, _ = at(105.0)
    obs = [
        DynObstacle(name="타 드론 (같은 고도 횡단)", kind="cylinder", radius=30, z_min=z1 - 40, z_max=z1 + 40, margin=15,
                    path=[dict(x=x1 - 420, y=y1 - 420, t=0.0), dict(x=x1, y=y1, t=35.0), dict(x=x1 + 420, y=y1 + 420, t=70.0)]),
        DynObstacle(name="응급 헬기 (전 고도)", kind="cylinder", radius=150, z_min=0, z_max=1000, margin=30,
                    path=[dict(x=x2 + 1700, y=y2 - 1700, t=10.0), dict(x=x2, y=y2, t=70.0), dict(x=x2 - 1700, y=y2 + 1700, t=130.0)]),
        DynObstacle(name="행사 임시 비행제한 (정지)", kind="box", half_x=250, half_y=120, z_min=0, z_max=1000, margin=10,
                    path=[dict(x=x3, y=y3, t=0.0)], t_start=0.0, t_end=150.0),
    ]
    return obs


def E8():
    import dataclasses
    from dpo.st_planner import STPlanner, check_path_conflicts
    from dpo.obstacles import validate, save_scenario
    from dpo.layered import fit_linear_profile, linear_wind_site
    g, fc, tr = sites()
    dr = drone_calibrated()
    m = dr.m_empty + PAYLOAD
    obs = e8_scenario(fc, dr)
    obs, errs = validate(obs, fc)
    save_scenario(C.ROOT / "scenarios" / "e8_dynamic.json", obs,
                  meta=dict(t_dep_kst=T_MAIN / 3600 + KST, payload=PAYLOAD, note="E8 예시: 기준 경로를 가로지르도록 시각 설정"))
    st, gl = fc.lowest_free_k(*HUB), fc.lowest_free_k(*DST)
    usable = (dr.battery_wh * 3600) * (1 - 0.20)       # 예비 20% 제외 사용 가능 에너지
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    prof = fit_linear_profile(f0, 0.5 * (C.LAT_MIN + C.LAT_MAX), 0.5 * (C.LON_MIN + C.LON_MAX), float(g["ground"].mean()))
    sl = linear_wind_site(fc, prof)
    cases = {
        "static_WELT": dict(site=fc, kw=dict(ignore_obstacles=True), label="WE-LT* (동적 장애물 무시)"),
        "st_nowait": dict(site=fc, kw=dict(max_wait=0.0), label="시공간 (대기 없이 우회만)"),
        "st_full": dict(site=fc, kw=dict(), label="시공간 WE-SIPP-Theta* (대기+우회)"),
        "st_linear": dict(site=sl, kw=dict(), label="시공간 (선형 바람, 빠름)"),
    }
    rows, store = [], {}
    for key, cfg in cases.items():
        SP = STPlanner(cfg["site"], dr)
        SP.set_obstacles(obs, T_MAIN)
        t = time.time()
        r = SP.plan(st, gl, t_start=T_MAIN, mass=m, budget=usable, **cfg["kw"])
        if r is None:
            rows.append(dict(key=key, label=cfg["label"], feasible=False)); continue
        sim = simulate(tr, dr, r["path"], r["wait_before"], T_MAIN, m, U=tr.U, V=tr.V, tax=tr.t_axis)
        cf = check_path_conflicts(fc, sim, obs, T_MAIN)
        rows.append(dict(key=key, label=cfg["label"], feasible=True, E_kJ=sim["e"][-1] / 1000,
                         soc_leg=soc_drop(sim["e"][-1], dr), T=sim["t"][-1] - T_MAIN, wait=float(r["wait_before"].sum()),
                         L=path_length(fc, r["path"]), wp=len(r["path"]), cpu=r["cpu_s"], n_exp=r["n_expanded"],
                         min_clear=cf["min_clearance"], n_conf=cf["n_collision_samples"],
                         conflicts=sorted({c[0] for c in cf["collisions"]}), table_s=SP.table_time,
                         iv_cells=r["n_iv_cells"]))
        store[key] = dict(path=r["path"], wait=r["wait_before"], sim=sim)
        print(rows[-1], f"{time.time() - t:.1f}s")
    # 시나리오 B: 지도 전체 폭을 가로막는 띠형 임시 제한 (우회 불가 → 대기만 가능)
    from dpo.obstacles import DynObstacle
    sim_b = store["static_WELT"]["sim"]
    yb = float(sim_b["y"][int(np.searchsorted(sim_b["t"] - T_MAIN, 60.0))])
    Wm = fc.nx * fc.dx
    band = [DynObstacle(name=f"전면 통제 띠-{q + 1} (0~80 s)", kind="box", half_x=Wm / 6 + 30, half_y=60, z_min=0,
                        z_max=1000, margin=10, path=[dict(x=Wm * (2 * q + 1) / 6, y=yb, t=0.0)], t_start=0.0, t_end=80.0)
            for q in range(3)]                       # 박스 반폭 상한(1500 m) 때문에 3개로 지도 전체 폭을 덮음
    band, berr = validate(band, fc)
    assert not berr, berr
    for key, kw, label in (("B_nowait", dict(max_wait=0.0), "B: 대기 금지"), ("B_full", dict(), "B: 시공간 (대기 허용)")):
        SP = STPlanner(fc, dr); SP.set_obstacles(band, T_MAIN)
        r = SP.plan(st, gl, t_start=T_MAIN, mass=m, budget=usable, **kw)
        if r is None:
            rows.append(dict(key=key, label=label, feasible=False)); print(key, "infeasible"); continue
        sim = simulate(tr, dr, r["path"], r["wait_before"], T_MAIN, m, U=tr.U, V=tr.V, tax=tr.t_axis)
        cf = check_path_conflicts(fc, sim, band, T_MAIN)
        rows.append(dict(key=key, label=label, feasible=True, E_kJ=sim["e"][-1] / 1000, soc_leg=soc_drop(sim["e"][-1], dr),
                         T=sim["t"][-1] - T_MAIN, wait=float(r["wait_before"].sum()), L=path_length(fc, r["path"]),
                         wp=len(r["path"]), cpu=r["cpu_s"], min_clear=cf["min_clearance"],
                         n_conf=cf["n_collision_samples"], conflicts=[]))
        store[key] = dict(path=r["path"], wait=r["wait_before"], sim=sim, band=[o.to_dict() for o in band])
        print(rows[-1])
    # 허용 범위 검사 예: 배터리 예산을 줄이면 '계획 불가' 로 판정
    SP = STPlanner(fc, dr); SP.set_obstacles(obs, T_MAIN)
    tight = SP.plan(st, gl, t_start=T_MAIN, mass=m, budget=30000.0)
    res = dict(rows=rows, validation_errors=errs, tight_budget_kJ=30.0, tight_feasible=tight is not None,
               obstacles=[o.to_dict() for o in obs])
    save("E8", dict(**res, store=store))
    put_summary("E8_dynamic", res)


if __name__ == "__main__":
    todo = sys.argv[1:] or ["E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"]
    for name in todo:
        t = time.time()
        print(f"===== {name}")
        globals()[name]()
        print(f"===== {name} done {time.time() - t:.0f}s")
