"""E13: 여러 날·여러 경로에서 '기존 경로' 대비 '제안 경로'의 SOC, 그리고 기상 정보를 비행 중 갱신하는 주기의 효과

질문 1 (우수성)  같은 출발·도착, 같은 실제 바람에서 제안 경로가 기존 경로보다 SOC 를 덜 쓰는가? 날·경로가 바뀌어도 그런가?
질문 2 (갱신 주기) 비행 중 몇 분마다 기상 정보를 갱신해 다시 계획하면 얼마나 더 줄어드는가? (최적 설계: 갱신 주기)

설정
- 바람: 계획은 00Z 예보(출발 6–10시간 전에 나온 정보), 평가는 06Z 예보를 '실제' 바람으로 본다 (관측 아님)
- 경로: 지역 안에서 직선거리 1.5–3.0 km 인 출발·도착 쌍 (난수 고정), 편도, 탑재 1 kg
- 방식
    EX    기존 경로: 최단 경로 + 정속 10 m/s
    EXv   기존 경로 + 구간 속도 최적화 (공정한 비교용)
    P0    제안: 출발 전 예보로 1회 계획
    PO    제안 + 실제 바람을 미리 안다고 가정 (정보 가치의 상한)
    R_inf / R_300 / R_120 / R_60 / R_mid   제안 + 비행 중 Δ초마다(또는 중간 1회) 기체가 잰 바람으로 예보를 보정해 다시 계획
    R_120_nobias  다시 계획만 하고 새 바람 정보는 쓰지 않음 (대조)
    R_oracle      R_inf 와 같은 계산 방식의 상한

사용: PYTHONPATH=src python scripts/exp_superiority.py run changwon [x650]
      PYTHONPATH=src python scripts/exp_superiority.py analyze
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import os
import sys
import time

import numpy as np

DAYS = ["20261019", "20261012", "20261029", "20261002", "20261001", "20261031", "20261027"]
HOURS = [15, 17, 19]            # 출발 시각 (KST). 06Z 예보가 15–21시를 덮는다
HOURS_ROLL = [15, 18]
N_PAIRS, N_PAIRS_ROLL = 10, 6
PAYLOAD = 1.0                    # X650·M100 (기본)
PAYLOAD_BY = dict(m4t=0.2)       # M4T: 최대 페이로드 200 g


def payload(dkey):
    return PAYLOAD_BY.get(dkey, PAYLOAD)
KST = 9


def od_pairs(site, n, seed=7):
    rng = np.random.default_rng(seed)
    W, H = site.nx * site.dx, site.ny * site.dx

    def ok(p):
        i, j = int(p[0] // site.dx), int(p[1] // site.dx)
        return bool(site.free[:, j, i].any()) and not site.nfz2d[j, i]

    out = []
    while len(out) < n:
        a = (float(rng.uniform(200, W - 200)), float(rng.uniform(200, H - 200)))
        b = (float(rng.uniform(200, W - 200)), float(rng.uniform(200, H - 200)))
        if 1500 <= math.hypot(a[0] - b[0], a[1] - b[1]) <= 3000 and ok(a) and ok(b):
            out.append((a, b))
    return out


from dpo.mission import make_flyable  # noqa: E402


def baseline_leg(P, tr, d, a, b, t0, m, policy):
    """기존 경로(최단 경로) 한 구간: 계획 → 비행 가능하게 보정 → 실제 바람에서 비행."""
    from dpo.energy import soc_drop
    from dpo.planner import path_length, simulate, vertical_energy
    sp = P.site
    st, gl = sp.lowest_free_k(*a), sp.lowest_free_k(*b)
    r = P.plan(st, gl, t_start=t0, mass=m, mode=0, vconst=10.0)
    if r is None:
        return None
    path = make_flyable(sp, d, r["path"])
    if path is None:
        return None
    sim = simulate(tr, d, path, np.zeros(len(path)), t0, m, policy, 10.0, U=tr.U, V=tr.V, tax=tr.t_axis)
    if sim is None:
        return None
    e_to, t_to = vertical_energy(tr, d, a[0], a[1], st[0], t0, m, up=True)
    e_ld, t_ld = vertical_energy(tr, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
    return dict(soc=float(soc_drop(e_to + sim["e"][-1] + e_ld, d)), T=float(sim["t"][-1] + t_ld + t_to - t0),
                L=float(path_length(sp, path)), fixed=bool(len(path) != len(r["path"])))


def _load_day(site_key, ymd, dkey):
    from dpo import config as C
    C.use_site(site_key)
    sys.path.insert(0, "scripts")
    from fetch_gfs_days import gfs_name
    from dpo import drones as DR
    from dpo.planner import Planner
    from dpo.wind_field import Site3D
    g = dict(np.load(C.PROC / C.SITE["grid"]))
    T0 = np.datetime64(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T00:00")
    fc = Site3D(g, dict(np.load(C.RAW / gfs_name(site_key, ymd, "00"), allow_pickle=True)), T0)
    tr = Site3D(g, dict(np.load(C.RAW / gfs_name(site_key, ymd, "06"), allow_pickle=True)), T0)
    for s_ in (fc, tr):
        for z in C.SITE["nfz"]:
            s_.add_static_nfz_circle(*z)
    d = DR.get(dkey)
    return fc, tr, d, Planner(fc, d)


def safeguard_worker(args):
    """P0s: 에너지 탐색 경로와 최단 경로(속도 최적화)를 예보에서 비교해 고른 뒤, 실제 바람에서 평가."""
    from dpo.energy import soc_drop
    from dpo.mission import plan_best_of_two
    from dpo.planner import simulate, vertical_energy
    site_key, ymd, dkey = args
    fc, tr, d, P = _load_day(site_key, ymd, dkey)
    pairs = od_pairs(fc, N_PAIRS)
    m = d.m_empty + payload(dkey)
    out = []
    for h in HOURS:
        t0 = (h - KST) * 3600.0
        for q, (a, b) in enumerate(pairs):
            st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
            r = plan_best_of_two(P, st, gl, t0, m)
            res = None
            if r is not None:
                sim = simulate(tr, d, r["path"], r["wait_before"], t0, m, "opt", U=tr.U, V=tr.V, tax=tr.t_axis)
                if sim is not None:
                    e_to, t_to = vertical_energy(tr, d, a[0], a[1], st[0], t0, m, up=True)
                    e_ld, t_ld = vertical_energy(tr, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
                    res = dict(soc=float(soc_drop(e_to + sim["e"][-1] + e_ld, d)), T=float(sim["t"][-1] + t_ld + t_to - t0),
                               chosen=r["chosen"])
            out.append((h, q, res))
    return ymd, out


def safeguard(site_key, dkey="x650"):
    from dpo import config as C
    f = C.RES / f"E13_{site_key}_{dkey}.json"
    rows = json.loads(f.read_text())
    with cf.ProcessPoolExecutor(4) as ex:
        for ymd, out in ex.map(safeguard_worker, [(site_key, d_, dkey) for d_ in DAYS]):
            for h, q, res in out:
                next(x for x in rows if x["day"] == ymd and x["hour"] == h and x["pair"] == q)["P0s"] = res
    f.write_text(json.dumps(rows, ensure_ascii=False))
    n = sum(1 for r in rows if r.get("P0s") and r["P0s"]["chosen"] == "shortest")
    print(f"[safeguard {site_key} {dkey}] {len(rows)}건 중 최단 경로를 고른 경우 {n}건", flush=True)


def repair_worker(args):
    """이미 계산한 결과에서 기존 경로가 비어 있는 경우만 보정해 다시 계산."""
    site_key, ymd, dkey, todo = args
    fc, tr, d, P = _load_day(site_key, ymd, dkey)
    pairs = od_pairs(fc, N_PAIRS)
    m = d.m_empty + payload(dkey)
    out = []
    for h, q in todo:
        a, b = pairs[q]
        t0 = (h - KST) * 3600.0
        out.append((h, q, baseline_leg(P, tr, d, a, b, t0, m, "const"), baseline_leg(P, tr, d, a, b, t0, m, "opt")))
    return ymd, out


def repair(site_key, dkey="x650"):
    from dpo import config as C
    f = C.RES / f"E13_{site_key}_{dkey}.json"
    rows = json.loads(f.read_text())
    jobs = {}
    for r in rows:
        if r["EX"] is None or r["EXv"] is None:
            jobs.setdefault(r["day"], []).append((r["hour"], r["pair"]))
    n = 0
    with cf.ProcessPoolExecutor(4) as ex:
        for ymd, out in ex.map(repair_worker, [(site_key, d_, dkey, t) for d_, t in jobs.items()]):
            for h, q, ex_, exv in out:
                r = next(x for x in rows if x["day"] == ymd and x["hour"] == h and x["pair"] == q)
                r["EX"], r["EXv"] = ex_, exv
                n += ex_ is not None
    f.write_text(json.dumps(rows, ensure_ascii=False))
    print(f"[repair {site_key} {dkey}] 대상 {sum(len(v) for v in jobs.values())}건 중 {n}건 보정", flush=True)


def day_worker(args):
    site_key, ymd, dkey = args
    from dpo import config as C
    C.use_site(site_key)
    sys.path.insert(0, "scripts")
    from fetch_gfs_days import gfs_name
    from dpo import drones as DR
    from dpo.energy import soc_drop
    from dpo.mission import _leg, run_rolling
    from dpo.planner import Planner
    from dpo.wind_field import Site3D
    g = dict(np.load(C.PROC / C.SITE["grid"]))
    T0 = np.datetime64(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T00:00")
    fc = Site3D(g, dict(np.load(C.RAW / gfs_name(site_key, ymd, "00"), allow_pickle=True)), T0)
    tr = Site3D(g, dict(np.load(C.RAW / gfs_name(site_key, ymd, "06"), allow_pickle=True)), T0)
    for s in (fc, tr):
        for z in C.SITE["nfz"]:
            s.add_static_nfz_circle(*z)
    d = DR.get(dkey)
    P, PO = Planner(fc, d), Planner(tr, d)
    m = d.m_empty + payload(dkey)
    pairs = od_pairs(fc, N_PAIRS)
    kk = np.argmin(np.abs(tr.agl - 100.0), axis=0)
    J, I = np.indices(kk.shape)
    rows = []
    t_begin = time.time()
    for h in HOURS:
        t0 = (h - KST) * 3600.0
        it = int(np.argmin(np.abs(tr.t_axis - t0)))
        ws = float(np.hypot(tr.U[it][kk, J, I], tr.V[it][kk, J, I]).mean())
        wdir = float(math.degrees(math.atan2(-tr.U[it][kk, J, I].mean(), -tr.V[it][kk, J, I].mean())) % 360)
        ifc = int(np.argmin(np.abs(fc.t_axis - t0)))
        dvec = float(np.sqrt(((fc.U[ifc] - tr.U[it]) ** 2 + (fc.V[ifc] - tr.V[it]) ** 2)[:, fc.free.any(axis=0)].mean()))
        for q, (a, b) in enumerate(pairs):
            row = dict(site=site_key, day=ymd, hour=h, pair=q, dist=math.hypot(a[0] - b[0], a[1] - b[1]), wind=ws, wdir=wdir,
                       fc_err=dvec, drone=dkey)

            def static(pl, mode, pol, v=10.0):
                r = _leg(pl, pl.site, tr, d, a, b, t0, m, mode, pol, v, U=tr.U, V=tr.V, tax=tr.t_axis)
                if r is None:
                    return None
                return dict(soc=float(soc_drop(r["e_total"], d)), T=float(r["t_end"] - t0), L=float(r["length"]))

            row["EX"] = baseline_leg(P, tr, d, a, b, t0, m, "const")
            row["EXv"] = baseline_leg(P, tr, d, a, b, t0, m, "opt")
            row["P0"] = static(P, 1, "opt")
            row["PO"] = static(PO, 1, "opt")
            if q < N_PAIRS_ROLL and h in HOURS_ROLL:
                def roll(pl, dt, bias=True):
                    r = run_rolling(pl, tr, d, a, b, t0, m, dt_replan=dt, bias_update=bias)
                    if not r["trace"] or len(r["trace"][-1]["t"]) == 0:
                        return None
                    # 도착했는지 확인 (마지막 표본이 목적지 근처)
                    x, y = r["trace"][-1]["x"][-1], r["trace"][-1]["y"][-1]
                    if math.hypot(x - b[0], y - b[1]) > 60:
                        return None
                    return dict(soc=float(r["soc"]), T=float(r["t_end"] - t0), n_plans=len(r["plans"]))

                ri = roll(P, 1e9)
                row["R_inf"] = ri
                if ri is not None:
                    for name, dt in (("R_300", 300.0), ("R_120", 120.0), ("R_60", 60.0), ("R_mid", max(ri["T"] / 2, 20.0))):
                        row[name] = roll(P, dt)
                    row["R_120_nobias"] = roll(P, 120.0, bias=False)
                    row["R_oracle"] = roll(PO, 1e9, bias=False)
            rows.append(row)
    print(f"[{site_key} {ymd} {dkey}] {len(rows)}건, {time.time() - t_begin:.0f} s", flush=True)
    return rows


def run(site_key, dkey="x650"):
    from dpo import config as C
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(day_worker, [(site_key, d, dkey) for d in DAYS]))
    rows = [r for day in res for r in day]
    out = C.RES / f"E13_{site_key}_{dkey}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", out, len(rows))


# ------------------------------------------------------------------ 예보가 틀렸을 때 (갱신 주기의 설계 지도)
ERR_CASES = [("rot30", 30.0, 1.0), ("rot60", 60.0, 1.0), ("rot90", 90.0, 1.0), ("rot180", 180.0, 1.0),
             ("spd0.6", 0.0, 0.6), ("spd1.4", 0.0, 1.4)]
ERR_DAYS = ["20261027", "20261031", "20261001"]


def err_worker(args):
    """예보 바람을 일부러 틀리게 만든 뒤(풍향을 θ 만큼 돌리거나 풍속을 배율로 바꾼 것을 '실제'로 둠) 갱신 주기의 효과를 본다."""
    import copy
    site_key, ymd, dkey = args
    from dpo import config as C
    C.use_site(site_key)
    sys.path.insert(0, "scripts")
    from fetch_gfs_days import gfs_name
    from dpo import drones as DR
    from dpo.mission import run_rolling
    from dpo.planner import Planner
    from dpo.wind_field import Site3D
    g = dict(np.load(C.PROC / C.SITE["grid"]))
    T0 = np.datetime64(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T00:00")
    fc = Site3D(g, dict(np.load(C.RAW / gfs_name(site_key, ymd, "00"), allow_pickle=True)), T0)
    for z in C.SITE["nfz"]:
        fc.add_static_nfz_circle(*z)
    d = DR.get(dkey)
    P = Planner(fc, d)
    m = d.m_empty + payload(dkey)
    pairs = od_pairs(fc, N_PAIRS_ROLL)
    t0 = (15 - KST) * 3600.0
    kk = np.argmin(np.abs(fc.agl - 100.0), axis=0)
    J, I = np.indices(kk.shape)
    it = int(np.argmin(np.abs(fc.t_axis - t0)))
    ws = float(np.hypot(fc.U[it][kk, J, I], fc.V[it][kk, J, I]).mean())
    rows = []
    t_begin = time.time()
    for name, rot, scale in ERR_CASES:
        tr = copy.copy(fc)
        th = math.radians(rot)
        tr.U = ((fc.U * math.cos(th) - fc.V * math.sin(th)) * scale).astype(fc.U.dtype)
        tr.V = ((fc.U * math.sin(th) + fc.V * math.cos(th)) * scale).astype(fc.V.dtype)
        PO = Planner(tr, d)
        err = float(np.sqrt(((fc.U[it] - tr.U[it]) ** 2 + (fc.V[it] - tr.V[it]) ** 2)[:, fc.free.any(axis=0)].mean()))
        for q, (a, b) in enumerate(pairs):
            row = dict(site=site_key, day=ymd, pair=q, case=name, rot=rot, scale=scale, wind=ws, err=err, drone=dkey)

            def roll(pl, dt, bias=True):
                r = run_rolling(pl, tr, d, a, b, t0, m, dt_replan=dt, bias_update=bias)
                if not r["trace"] or len(r["trace"][-1]["t"]) == 0:
                    return None
                x, y = r["trace"][-1]["x"][-1], r["trace"][-1]["y"][-1]
                if math.hypot(x - b[0], y - b[1]) > 60:
                    return None
                return dict(soc=float(r["soc"]), T=float(r["t_end"] - t0), n_plans=len(r["plans"]))

            ri = roll(P, 1e9)
            row["R_inf"] = ri
            if ri is not None:
                for nm, dt in (("R_120", 120.0), ("R_60", 60.0), ("R_mid", max(ri["T"] / 2, 20.0))):
                    row[nm] = roll(P, dt)
                row["R_oracle"] = roll(PO, 1e9, bias=False)
            rows.append(row)
    print(f"[err {site_key} {ymd}] {len(rows)}건, {time.time() - t_begin:.0f} s", flush=True)
    return rows


def run_err(site_key, dkey="x650"):
    from dpo import config as C
    with cf.ProcessPoolExecutor(3) as ex:
        res = list(ex.map(err_worker, [(site_key, d, dkey) for d in ERR_DAYS]))
    rows = [r for day in res for r in day]
    (C.RES / f"E13err_{site_key}_{dkey}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows))


def analyze_err(R):
    from dpo import config as C
    out = {}
    for f in sorted(C.RES.glob("E13err_*.json")):
        rows = json.loads(f.read_text())
        S = {}
        for name, rot, scale in ERR_CASES:
            rr = [r for r in rows if r["case"] == name and r.get("R_inf") and r.get("R_oracle")]
            if not rr:
                continue
            e = dict(n=len(rr), err=float(np.mean([r["err"] for r in rr])), wind=float(np.mean([r["wind"] for r in rr])),
                     n_fail=int(sum(1 for r in rows if r["case"] == name and not r.get("R_inf"))),
                     penalty_pct=float(np.mean([(r["R_inf"]["soc"] / r["R_oracle"]["soc"] - 1) * 100 for r in rr])))
            for k in ("R_mid", "R_120", "R_60"):
                ok = [r for r in rr if r.get(k)]
                if ok:
                    days = np.array([r["day"] for r in ok])
                    pct = np.array([(r[k]["soc"] / r["R_inf"]["soc"] - 1) * 100 for r in ok])
                    gap = np.array([r["R_inf"]["soc"] - r["R_oracle"]["soc"] for r in ok])
                    got = np.array([r["R_inf"]["soc"] - r[k]["soc"] for r in ok])
                    e[k] = dict(mean_pct=float(pct.mean()), ci=list(_cluster_ci(pct, days)), win=float((pct < -0.05).mean()),
                                lose=float((pct > 0.05).mean()), recovered=float(got.sum() / gap.sum()) if gap.sum() > 1e-9 else None,
                                n_plans=float(np.mean([r[k]["n_plans"] for r in ok])), n=len(ok))
            S[name] = e
            print(f"   [예보 오차 {name}] 오차 {e['err']:.1f} m/s, 갱신 없으면 상한 대비 {e['penalty_pct']:+.1f} % | " +
                  " | ".join(f"{k} {e[k]['mean_pct']:+.1f} % (회복 {0 if e[k]['recovered'] is None else e[k]['recovered'] * 100:.0f} %)" for k in ("R_mid", "R_120", "R_60") if k in e))
        out[f.stem[7:]] = S
    R["forecast_error"] = out
    return out


# ------------------------------------------------------------------ 분석
def _cluster_ci(vals, days, fn=np.mean, nboot=4000, seed=0):
    """날짜 단위로 다시 뽑는 부트스트랩 95 % 구간 (같은 날의 경우들은 바람이 같아 서로 독립이 아니다)."""
    rng = np.random.default_rng(seed)
    vals, days = np.asarray(vals, float), np.asarray(days)
    ud = np.unique(days)
    grp = [vals[days == d] for d in ud]
    bs = []
    for _ in range(nboot):
        pick = rng.integers(0, len(ud), len(ud))
        bs.append(fn(np.concatenate([grp[k] for k in pick])))
    return float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def compare(rows, a, b):
    """a → b 의 SOC 변화 [%] (음수 = b 가 덜 씀). 두 방식 모두 비행 가능한 경우만."""
    ok = [r for r in rows if r.get(a) and r.get(b)]
    if not ok:
        return None
    pct = np.array([(r[b]["soc"] / r[a]["soc"] - 1) * 100 for r in ok])
    dpp = np.array([r[b]["soc"] - r[a]["soc"] for r in ok])
    tpc = np.array([(r[b]["T"] / r[a]["T"] - 1) * 100 for r in ok])
    days = np.array([r["site"] + r["day"] for r in ok])
    lo, hi = _cluster_ci(pct, days)
    dm = np.array([pct[days == d].mean() for d in np.unique(days)])
    from scipy.stats import wilcoxon
    try:
        pw = float(wilcoxon(dm).pvalue) if len(dm) >= 5 and np.any(dm != 0) else None
    except ValueError:
        pw = None
    return dict(n=len(ok), n_days=int(len(np.unique(days))), mean_pct=float(pct.mean()), median_pct=float(np.median(pct)), ci=[lo, hi],
                mean_pp=float(dpp.mean()), win=float((pct < -0.05).mean()), lose=float((pct > 0.05).mean()),
                worst_pct=float(pct.max()), best_pct=float(pct.min()), time_pct=float(tpc.mean()),
                day_means=[float(v) for v in dm], wilcoxon_days_p=pw, a_soc=float(np.mean([r[a]["soc"] for r in ok])),
                b_soc=float(np.mean([r[b]["soc"] for r in ok])))


def analyze():
    from dpo import config as C
    files = sorted(C.RES.glob("E13_*_*.json"))
    files = [f for f in files if f.name not in ("E13_stats.json", "E13_speed_sensitivity.json") and not f.name.startswith("E13err")]
    sets = {f.stem[4:]: json.loads(f.read_text()) for f in files}
    R = dict(sets={}, days=DAYS, hours=HOURS, payload=PAYLOAD, payload_by=PAYLOAD_BY)
    for name, rows in sets.items():
        S = dict(n=len(rows), n_days=len({r["day"] for r in rows}), wind=[float(min(r["wind"] for r in rows)), float(max(r["wind"] for r in rows))],
                 infeasible={k: int(sum(1 for r in rows if r.get(k) is None)) for k in ("EX", "EXv", "P0", "PO")},
                 EX_P0=compare(rows, "EX", "P0"), EXv_P0=compare(rows, "EXv", "P0"), P0_PO=compare(rows, "P0", "PO"),
                 EX_EXv=compare(rows, "EX", "EXv"),
                 EX_P0s=compare(rows, "EX", "P0s") if any(r.get("P0s") for r in rows) else None,
                 EXv_P0s=compare(rows, "EXv", "P0s") if any(r.get("P0s") for r in rows) else None,
                 n_shortest_chosen=int(sum(1 for r in rows if r.get("P0s") and r["P0s"].get("chosen") == "shortest")))
        by_day = {}
        for d in DAYS:
            rr = [r for r in rows if r["day"] == d]
            c = compare(rr, "EX", "P0")
            c2 = compare(rr, "EXv", "P0")
            by_day[d] = dict(wind=float(np.mean([r["wind"] for r in rr])), fc_err=float(np.mean([r["fc_err"] for r in rr])),
                             EX_P0=None if c is None else c["mean_pct"], EXv_P0=None if c2 is None else c2["mean_pct"],
                             win=None if c is None else c["win"], n=len(rr),
                             ex_infeasible=int(sum(1 for r in rr if r["EX"] is None)))
        S["by_day"] = by_day
        roll = [r for r in rows if r.get("R_inf")]
        S["rolling"] = dict(n=len(roll))
        for k in ("R_300", "R_120", "R_60", "R_mid", "R_120_nobias", "R_oracle"):
            S["rolling"][k] = compare(roll, "R_inf", k)
            np_ = [r[k]["n_plans"] for r in roll if r.get(k) and "n_plans" in r[k]]
            if S["rolling"][k] is not None and np_:
                S["rolling"][k]["n_plans"] = float(np.mean(np_))
        S["rolling"]["T_mean"] = float(np.mean([r["R_inf"]["T"] for r in roll])) if roll else None
        R["sets"][name] = S
        print(f"== {name}: {S['n']}건, 바람 {S['wind'][0]:.1f}–{S['wind'][1]:.1f} m/s, 불가 {S['infeasible']}")
        for k in ("EX_P0", "EXv_P0", "EX_P0s", "EXv_P0s", "P0_PO"):
            c = S[k]
            if c is None:
                continue
            print(f"   {k}: 평균 {c['mean_pct']:+.1f} % [{c['ci'][0]:+.1f}, {c['ci'][1]:+.1f}], 중앙 {c['median_pct']:+.1f} %, 승 {c['win'] * 100:.0f} % 패 {c['lose'] * 100:.0f} %, "
                  f"최악 {c['worst_pct']:+.1f} %, 시간 {c['time_pct']:+.1f} %, 날짜별 Wilcoxon p={c['wilcoxon_days_p']}")
        for k in ("R_300", "R_120", "R_60", "R_mid", "R_120_nobias", "R_oracle"):
            c = S["rolling"][k]
            if c:
                print(f"   {k}: {c['mean_pct']:+.2f} % [{c['ci'][0]:+.2f}, {c['ci'][1]:+.2f}], 승 {c['win'] * 100:.0f} % 패 {c['lose'] * 100:.0f} %, 계획 {c.get('n_plans', 0):.1f}회 (n={c['n']})")
    analyze_err(R)
    (C.RES / "E13_stats.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R, sets)


def figure(R, sets):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from dpo import config as C
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "mathtext.fontset": "dejavusans", "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 200, "savefig.bbox": "tight", "axes.titleweight": "bold"})
    COL = {"changwon_x650": "#E46C0A", "pangyo_x650": "#1B6CA8", "pangyo_m100": "#2CA58D",
           "changwon_m4t": "#7A3E9D", "pangyo_m4t": "#C0392B"}
    LAB = {"changwon_x650": "창원 · X650", "pangyo_x650": "판교 · X650", "pangyo_m100": "판교 · M100",
           "changwon_m4t": "창원 · M4T", "pangyo_m4t": "판교 · M4T"}
    from matplotlib.lines import Line2D
    fig, axs = plt.subplots(2, 2, figsize=(13.6, 10.2), gridspec_kw=dict(wspace=0.26, hspace=0.34))
    names_all = list(sets)

    ax = axs[0, 0]
    lim = 0
    for name, rows in sets.items():
        ok = [r for r in rows if r["EX"] and r["P0"]]
        x, y = [r["EX"]["soc"] for r in ok], [r["P0"]["soc"] for r in ok]
        ax.scatter(x, y, s=14, alpha=0.55, color=COL.get(name, "#555"), label=f"{LAB.get(name, name)} ({len(ok)}건)", lw=0)
        lim = max(lim, max(x + y))
    ax.plot([0, lim * 1.05], [0, lim * 1.05], color="#555", lw=1, ls="--")
    ax.text(lim * 0.80, lim * 0.86, "같은 값", color="#555", fontsize=9, rotation=42, ha="center", va="center")
    ax.set_xlim(0, lim * 1.05); ax.set_ylim(0, lim * 1.05)
    ax.set_xlabel("기존 경로의 SOC 소모 [%p] (최단 경로 + 정속 10 m/s)"); ax.set_ylabel("제안 경로의 SOC 소모 [%p]")
    ax.set_title("(a) 경우별 비교: 점이 점선 아래면 제안이 덜 씀")
    ax.legend(frameon=False, fontsize=9, loc="upper left")

    ax = axs[0, 1]
    comps = [("EX_P0", "기존 대비\n제안 (전체)"), ("EX_EXv", "그중 속도만\n바꾼 효과"), ("EXv_P0", "그중 경로\n모양의 효과"),
             ("EXv_P0s", "경로 효과\n(안전장치 포함)"), ("P0_PO", "더 새 예보로\n계획했다면")]
    wbar = 0.8 / max(len(names_all), 1)
    for q, name in enumerate(names_all):
        S = R["sets"][name]
        v = np.array([S[k]["mean_pct"] if S.get(k) else np.nan for k, _ in comps])
        lo = np.array([S[k]["ci"][0] if S.get(k) else np.nan for k, _ in comps])
        hi = np.array([S[k]["ci"][1] if S.get(k) else np.nan for k, _ in comps])
        x = np.arange(len(comps)) + (q - (len(names_all) - 1) / 2) * wbar
        ax.bar(x, v, wbar * 0.92, color=COL.get(name, "#555"), label=LAB.get(name, name),
               yerr=[v - lo, hi - v], error_kw=dict(ecolor="#333", lw=1, capsize=2))
        for xx, vv in zip(x, v):
            if np.isfinite(vv):
                ax.text(xx, 0.35, f"{vv:+.1f}", ha="center", va="bottom", fontsize=7.5, color="#333", rotation=90)
    ax.axhline(0, color="#555", lw=0.8)
    ax.set_xticks(range(len(comps))); ax.set_xticklabels([c[1] for c in comps], fontsize=8.5)
    ax.set_ylim(top=4.2)
    ax.set_ylabel("SOC 소모 변화 [%] (음수 = 절감)")
    ax.set_title("(b) 절감이 어디서 나오나 (평균, 95 % 구간)")
    ax.legend(frameon=False, fontsize=9, loc="lower right")

    ax = axs[1, 0]
    for name in sets:
        bd = R["sets"][name]["by_day"]
        w = [bd[d]["wind"] for d in R["days"]]
        o = np.argsort(w)
        ax.plot(np.array(w)[o], np.array([bd[d]["EX_P0"] for d in R["days"]])[o], "o-", color=COL.get(name, "#555"), lw=2)
        ax.plot(np.array(w)[o], np.array([bd[d]["EXv_P0"] for d in R["days"]])[o], "s--", color=COL.get(name, "#555"), lw=1.2, alpha=0.85)
    ax.axhline(0, color="#555", lw=0.8)
    ax.set_xlabel("그날의 지면 위 100 m 평균 풍속 [m/s]"); ax.set_ylabel("제안 경로의 SOC 변화 [%] (음수 = 절감)")
    ax.set_title("(c) 날짜별 평균: 바람 세기에 따른 절감")
    h = [Line2D([], [], color=COL.get(n, "#555"), lw=2, label=LAB.get(n, n)) for n in sets]
    h += [Line2D([], [], color="#444", lw=2, marker="o", label="기존(최단 + 10 m/s) 대비"),
          Line2D([], [], color="#444", lw=1.2, ls="--", marker="s", label="속도까지 최적화한 최단 경로 대비")]
    ax.set_ylim(top=6)
    ax.legend(handles=h, frameon=False, fontsize=8.5, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.0))

    ax = axs[1, 1]
    keys = [("R_mid", "경로 중간에\n1회"), ("R_120", "2분마다"), ("R_60", "1분마다"), ("R_120_nobias", "2분마다,\n새 정보 없이\n(대조)"),
            ("R_oracle", "실제 바람을\n미리 안다면\n(상한)")]
    names = [n for n in sets if R["sets"][n]["rolling"]["n"]]
    wbar = 0.8 / max(len(names), 1)
    for q, name in enumerate(names):
        ro = R["sets"][name]["rolling"]
        v = [ro[k]["mean_pct"] if ro[k] else np.nan for k, _ in keys]
        lo = [ro[k]["ci"][0] if ro[k] else np.nan for k, _ in keys]
        hi = [ro[k]["ci"][1] if ro[k] else np.nan for k, _ in keys]
        x = np.arange(len(keys)) + (q - (len(names) - 1) / 2) * wbar
        ax.bar(x, v, wbar * 0.92, color=COL.get(name, "#555"), label=f"{LAB.get(name, name)} ({ro['n']}건)",
               yerr=[np.array(v) - np.array(lo), np.array(hi) - np.array(v)], error_kw=dict(ecolor="#444", lw=1, capsize=2))
    ax.axhline(0, color="#555", lw=0.8)
    ax.set_xticks(range(len(keys))); ax.set_xticklabels([k[1] for k in keys], fontsize=8.5)
    ax.set_ylabel("다시 계획하지 않을 때 대비 SOC 변화 [%]")
    ax.set_title("(d) 비행 중 다시 계획하는 주기의 효과 (양수 = 손해)")
    ax.set_ylim(top=1.55)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", ncol=3, columnspacing=1.0, handlelength=1.2)
    fig.savefig(C.FIG / "F28_superiority.png")
    plt.close(fig)


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    elif sys.argv[1] == "safeguard":
        safeguard(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    elif sys.argv[1] == "repair":
        repair(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    elif sys.argv[1] == "runerr":
        run_err(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    else:
        analyze()
