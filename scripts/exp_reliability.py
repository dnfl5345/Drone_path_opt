"""E16: 과거 시점 자료로 계획한 경로의 신뢰도 – 자료의 나이·계절에 따른 예보 오차, 신뢰수준, 계절 권고

우리가 받는 바람은 언제나 과거에 발표된 예보다. 이 실험은 GFS 표본(2025-11 … 2026-10, 매달 5·15·25일 = 36일,
scripts/fetch_gfs_clim.py)으로 다음을 잰다. 모두 예보끼리의 비교이고 관측은 쓰지 않았다.

  A. 지면 위 100 m 바람의 월별·시각별 분포 (한 해의 36일 표본 – 여러 해 기후값이 아니다)
  B. 예보 오차와 자료의 나이: 선행 6·9·12·30·54·78 시간, 전체와 계절별. '몇 시간 전 바람을 그대로 쓸 때'의 오차도.
  C. 임무 수준의 계절성: 날짜 × 출발 시각(9·12·15·18시) × 경로 3개. 기존(최단 + 정속 10 m/s), 최단 + 속도 최적화,
     제안(에너지 탐색 + 안전장치). 15·18시는 06Z 예보에서 평가하고, 9·12시는 계획에 쓴 예보에서 평가한다.
  D. 신뢰수준 검증: mission_confidence 가 낸 90·95 % 상한 아래로 실제(06Z) SOC 소모가 들어온 비율(덮임률).

사용: PYTHONPATH=src python scripts/exp_reliability.py model                 # B → results/wind_error_model.json
      PYTHONPATH=src python scripts/exp_reliability.py run changwon x650     # C·D 의 계산
      PYTHONPATH=src python scripts/exp_reliability.py analyze               # 통계, 계절표, 그림
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
from exp_superiority import _cluster_ci, _load_day, baseline_leg, od_pairs  # noqa: E402
from fetch_gfs_clim import AGE_LEADS, sample_days  # noqa: E402
from fetch_gfs_days import gfs_name  # noqa: E402

KST = 9
HOURS = [9, 12, 15, 18]
N_PAIRS = 3
PAYLOAD = dict(x650=1.0, m100=0.5)
LEVS = [30, 40, 50, 80, 100]                # 비행 고도대의 GFS 고도 [m AGL]
SIG_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.5, 6.0]   # 신뢰도 계산을 미리 해 두는 σ [m/s]
SCALES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
SITES = ["changwon", "pangyo"]
SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
SEASON_KO = {"DJF": "겨울 (12–2월)", "MAM": "봄 (3–5월)", "JJA": "여름 (6–8월)", "SON": "가을 (9–11월)"}


def season_of(m):
    return next(k for k, v in SEASONS.items() if int(m) in v)


def days_available(site):
    """자료가 있는 표본 날짜. 환경변수 E16_DAYS (쉼표로 구분) 를 주면 그 날짜만 쓴다 (점검용)."""
    from dpo import config as C
    days = os.environ["E16_DAYS"].split(",") if os.environ.get("E16_DAYS") else sample_days()
    return [d for d in days if (C.RAW / gfs_name(site, d, "00")).exists() and (C.RAW / gfs_name(site, d, "06")).exists()]


# ------------------------------------------------------------------ B. 예보 오차와 자료의 나이 (GFS 원격자)
def _center(a, lat, lon, latc, lonc):
    """GFS 격자값을 지역 중심으로 쌍선형 보간. a: (..., nlat, nlon), lat 내림차순."""
    fi = np.interp(latc, lat[::-1], np.arange(len(lat))[::-1])
    fj = np.interp(lonc, lon, np.arange(len(lon)))
    i0, j0 = int(min(math.floor(fi), len(lat) - 2)), int(min(math.floor(fj), len(lon) - 2))
    wi, wj = fi - i0, fj - j0
    return ((1 - wi) * (1 - wj) * a[..., i0, j0] + (1 - wi) * wj * a[..., i0, j0 + 1]
            + wi * (1 - wj) * a[..., i0 + 1, j0] + wi * wj * a[..., i0 + 1, j0 + 1])


def _uv(pack, site):
    """(시각, 고도, 2) 의 지역 중심 바람."""
    from dpo import config as C
    S = C.SITES[site]
    latc, lonc = 0.5 * sum(S["lat"]), 0.5 * sum(S["lon"])
    u = np.stack([_center(pack[f"UGRD_agl{h}"], pack["lat"], pack["lon"], latc, lonc) for h in LEVS], axis=-1)
    v = np.stack([_center(pack[f"VGRD_agl{h}"], pack["lat"], pack["lon"], latc, lonc) for h in LEVS], axis=-1)
    return np.stack([u, v], axis=-1)


def error_table():
    """날짜별 오차 벡터를 모은다. 반환: rows (site, day, month, kind, lead, du², dv² 평균 …), 바람 기록."""
    from dpo import config as C
    rows, wind = [], []
    for site in SITES:
        for d in days_available(site):
            f0 = dict(np.load(C.RAW / gfs_name(site, d, "00"), allow_pickle=True))
            f6 = dict(np.load(C.RAW / gfs_name(site, d, "06"), allow_pickle=True))
            a, b = _uv(f0, site), _uv(f6, site)            # (13, 5, 2), (7, 5, 2)
            mth = int(d[4:6])

            def add(kind, lead, e):
                rows.append(dict(site=site, day=d, month=mth, kind=kind, lead=float(lead), ms=float((e ** 2).sum(-1).mean()),
                                 bu=float(e[..., 0].mean()), bv=float(e[..., 1].mean())))

            for k in range(0, 7):                           # 예보: 00Z f(6+k) 대 06Z f(k) → 선행 6–12 h
                add("forecast", 6 + k, a[6 + k] - b[k])
            for lead in AGE_LEADS:                          # 더 오래된 예보 대 06Z f000
                p = C.RAW / f"gfsage_{site}_{d}_lead{lead:03d}.npz"
                if p.exists():
                    add("forecast", lead, _uv(dict(np.load(p, allow_pickle=True)), site)[0] - b[0])
            for n in range(1, 13):                          # 지속성(대용): 00Z f000 을 N 시간 뒤에도 그대로 씀
                add("persist_proxy", n, a[0] - a[n])
            add("persist", 6, a[0] - b[0])                  # 분석값끼리: 00Z f000 대 06Z f000
            i100 = LEVS.index(100)
            for k in range(0, 12):                          # 기후: KST 9–20시의 100 m 바람
                u, v = a[k, i100]
                wind.append(dict(site=site, day=d, month=mth, hour=KST + k, ws=float(math.hypot(u, v)), u=float(u), v=float(v)))
    return rows, wind


def _sig(rows):
    """성분별 σ = sqrt(평균 제곱 벡터 오차 / 2), 벡터 RMSE, 날짜 수."""
    if not rows:
        return None
    ms = float(np.mean([r["ms"] for r in rows]))
    return dict(sigma=math.sqrt(ms / 2), rmse=math.sqrt(ms), n=len(rows))


def build_model():
    from dpo import config as C
    rows, wind = error_table()
    leads = [6.0, 9.0, 12.0] + [float(v) for v in AGE_LEADS]
    out = dict(leads_h=leads, sites={}, persist={}, n_days={})
    fc = [r for r in rows if r["kind"] == "forecast"]
    def tab(sel):
        t = {}
        for ld in leads:
            s = _sig([r for r in sel if r["lead"] == ld])
            t[str(int(ld))] = s
        return t
    out["overall"] = tab(fc)
    out["by_season"] = {k: tab([r for r in fc if r["month"] in ms]) for k, ms in SEASONS.items()}
    for site in SITES:
        out["sites"][site] = tab([r for r in fc if r["site"] == site])
        out["n_days"][site] = len({r["day"] for r in fc if r["site"] == site})
    pp = [r for r in rows if r["kind"] == "persist_proxy"]
    out["persist"]["proxy"] = {str(n): _sig([r for r in pp if r["lead"] == n]) for n in range(1, 13)}
    out["persist"]["analysis_6h"] = _sig([r for r in rows if r["kind"] == "persist"])
    out["persist"]["analysis_6h_by_season"] = {k: _sig([r for r in rows if r["kind"] == "persist" and r["month"] in ms]) for k, ms in SEASONS.items()}
    ok = [ld for ld in leads if out["overall"][str(int(ld))] is not None]
    model = dict(leads_h=ok, sigma=[out["overall"][str(int(ld))]["sigma"] for ld in ok],
                 by_season={k: [(out["by_season"][k][str(int(ld))] or out["overall"][str(int(ld))])["sigma"] for ld in ok] for k in SEASONS},
                 scale=1.0, source="E16: GFS 표본 (2025-11–2026-10, 매달 3일, 두 지역), 예보끼리의 차이",
                 n_days=out["n_days"], note="σ 는 성분별 표준편차 [m/s]. 선행 6 h 미만은 6 h 값을 쓴다 (분석값과 관측의 차이는 재지 못했다).")
    old = C.RES / "wind_error_model.json"
    if old.exists():                                        # 덮임률 보정 배율은 analyze 가 정한다 – 다시 만들 때 유지
        model["scale"] = json.loads(old.read_text()).get("scale", 1.0)
    old.write_text(json.dumps(model, ensure_ascii=False, indent=1))
    (C.RES / "E16_errors.json").write_text(json.dumps(dict(stats=out, wind=wind), ensure_ascii=False))
    print("선행 [h] :", ok)
    print("σ 전체   :", [round(v, 2) for v in model["sigma"]], "날짜", out["n_days"])
    for k in SEASONS:
        print(f"σ {k}    :", [round(v, 2) for v in model["by_season"][k]])
    print("지속성(대용) 1·3·6·12 h σ:", [round(out["persist"]["proxy"][str(n)]["sigma"], 2) for n in (1, 3, 6, 12)],
          "| 분석값끼리 6 h:", round(out["persist"]["analysis_6h"]["sigma"], 2))
    return out


# ------------------------------------------------------------------ C·D. 임무 수준
def worker(args):
    site_key, ymd, dkey = args
    from dpo.energy import soc_drop
    from dpo.mission import make_flyable
    from dpo.planner import simulate, vertical_energy
    from dpo.reliability import mission_confidence
    fc, tr, d, P = _load_day(site_key, ymd, dkey)
    m = d.m_empty + PAYLOAD[dkey]
    pairs = od_pairs(fc, N_PAIRS)
    kk = np.argmin(np.abs(fc.agl - 100.0), axis=0)
    J, I = np.indices(kk.shape)
    free2 = fc.free.any(axis=0)
    rows, t_begin = [], time.time()
    for h in HOURS:
        t0 = (h - KST) * 3600.0
        has_truth = h >= 15                         # 06Z 예보가 15–21시를 덮는다
        ev = tr if has_truth else fc
        it = int(np.argmin(np.abs(ev.t_axis - t0)))
        ws = float(np.hypot(ev.U[it][kk, J, I], ev.V[it][kk, J, I]).mean())
        uni = None
        if has_truth:                               # 오차장이 지도 안에서 얼마나 고른가 (한 가지 편차 모형의 근거)
            ifc = int(np.argmin(np.abs(fc.t_axis - t0)))
            eu, evv = (fc.U[ifc] - tr.U[it])[:, free2], (fc.V[ifc] - tr.V[it])[:, free2]
            mean_mag = float(math.hypot(eu.mean(), evv.mean()))
            dev = float(np.sqrt(((eu - eu.mean()) ** 2 + (evv - evv.mean()) ** 2).mean()))
            uni = dict(mean=mean_mag, dev=dev, rms=float(np.sqrt((eu ** 2 + evv ** 2).mean())))
        for q, (a, b) in enumerate(pairs):
            row = dict(site=site_key, drone=dkey, day=ymd, month=int(ymd[4:6]), hour=h, pair=q, truth=has_truth, wind=ws, uni=uni,
                       dist=float(math.hypot(a[0] - b[0], a[1] - b[1])))
            row["EX"] = baseline_leg(P, ev, d, a, b, t0, m, "const")
            row["EXv"] = baseline_leg(P, ev, d, a, b, t0, m, "opt")
            st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
            e_to_f, t_to = vertical_energy(fc, d, a[0], a[1], st[0], t0, m, up=True)
            # 두 후보를 직접 만든다 (dpo.mission.plan_best_of_two 와 같은 규칙: 예보 기준 에너지가 적은 쪽) – 후보별 신뢰도를 보려고
            cands = {}
            r1 = P.plan(st, gl, t_start=t0 + t_to, mass=m, mode=1)
            if r1 is not None:
                s1 = simulate(fc, d, r1["path"], r1["wait_before"], t0 + t_to, m)
                if s1 is not None:
                    cands["energy"] = (np.asarray(r1["path"]), np.asarray(r1["wait_before"], float), s1)
            r0 = P.plan(st, gl, t_start=t0 + t_to, mass=m, mode=0)
            p0 = None if r0 is None else make_flyable(fc, d, r0["path"])
            if p0 is not None:
                s0 = simulate(fc, d, p0, np.zeros(len(p0)), t0 + t_to, m)
                if s0 is not None:
                    cands["shortest"] = (np.asarray(p0), np.zeros(len(p0)), s0)
            if not cands:
                row["P0s"] = None
                rows.append(row)
                continue
            chosen = min(cands, key=lambda k: float(cands[k][2]["e"][-1]))
            r = dict(chosen=chosen)
            path, waits, nom = cands[chosen]
            e_ld_f, _ = vertical_energy(fc, d, b[0], b[1], gl[0], nom["t"][-1], m, up=False)
            e_fix_f = e_to_f + e_ld_f
            e_to, _ = vertical_energy(ev, d, a[0], a[1], st[0], t0, m, up=True)
            fro = simulate(ev, d, path, waits, t0 + t_to, m, vcmd=nom["vg"])
            ada = simulate(ev, d, path, waits, t0 + t_to, m)

            def soc(sim):
                if sim is None:
                    return None
                e_ld, _ = vertical_energy(ev, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
                return float(soc_drop(e_to + sim["e"][-1] + e_ld, d))

            row["P0s"] = dict(chosen=r["chosen"], nominal=float(soc_drop(e_fix_f + nom["e"][-1], d)), frozen=soc(fro), adapt=soc(ada),
                              T=float(nom["t"][-1] - t0), path=[[int(v) for v in p] for p in path], waits=[float(w) for w in waits])
            if has_truth:
                conf = {}
                for sg in SIG_GRID:
                    for ad in (False, True):
                        c = mission_confidence(fc, d, path, waits, t0 + t_to, m, sg, n=100, seed=1000 * int(ymd[4:]) % 9973 + 10 * h + q,
                                               adapt=ad, e_fixed=e_fix_f)
                        conf[f"{sg}|{int(ad)}"] = dict(q=[c["soc_used_q"][lv] if math.isfinite(c["soc_used_q"][lv]) else None for lv in (50, 90, 95, 99)],
                                                       mean=c["mean_soc_used"] if math.isfinite(c["mean_soc_used"]) else None, bad=c["infeasible"])
                row["conf"] = conf
                if len(cands) == 2:                 # 신뢰도 기준 선택: 두 후보의 90 % 분위수를 견줘 본다 (σ 1·2 m/s)
                    seed = 1000 * int(ymd[4:]) % 9973 + 10 * h + q
                    rc = {}
                    for k, (pk, wk, sk) in cands.items():
                        fk = simulate(ev, d, pk, wk, t0 + t_to, m, vcmd=sk["vg"])
                        e = dict(nominal=float(soc_drop(e_fix_f + sk["e"][-1], d)), actual=soc(fk))
                        for sg in (1.0, 2.0):
                            c = mission_confidence(fc, d, pk, wk, t0 + t_to, m, sg, n=100, seed=seed, adapt=False, e_fixed=e_fix_f)
                            e[f"q90_{sg:g}"] = c["soc_used_q"][90] if math.isfinite(c["soc_used_q"][90]) else None
                        rc[k] = e
                    row["robust"] = rc
            rows.append(row)
    print(f"[E16 {site_key} {dkey} {ymd}] {len(rows)}건, {time.time() - t_begin:.0f} s", flush=True)
    return rows


def run(site_key, dkey):
    from dpo import config as C
    days = days_available(site_key)
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(worker, [(site_key, d_, dkey) for d_ in days]))
    rows = [r for day in res for r in day]
    (C.RES / f"E16_{site_key}_{dkey}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows), "건,", len(days), "일")


# ------------------------------------------------------------------ 분석
def _bound(row, sigma, level, adapt):
    """미리 계산한 σ 격자에서 σ 의 분위수 상한을 보간. 격자 밖은 끝값. 비행 불가가 섞이면 inf."""
    li = {50: 0, 90: 1, 95: 2, 99: 3}[level]
    xs, ys = [], []
    for sg in SIG_GRID:
        v = row["conf"][f"{sg}|{int(adapt)}"]["q"][li]
        xs.append(sg)
        ys.append(math.inf if v is None else v)
    if sigma <= xs[0]:                               # σ → 0 이면 명목값으로 수렴
        return row["P0s"]["nominal"] + (ys[0] - row["P0s"]["nominal"]) * sigma / xs[0]
    return float(np.interp(sigma, xs, ys))


def coverage(rows, model, scale, level, adapt, seasonal=True, days=None):
    key = "adapt" if adapt else "frozen"
    ok = [r for r in rows if r["truth"] and r.get("P0s") and r["P0s"][key] is not None and (days is None or r["day"] in days)]
    hit = 0
    for r in ok:
        lead = 6.0 + (r["hour"] - 15)                 # 15시 = 00Z 예보의 선행 6 h, 18시 = 9 h
        sg = model.sigma(lead, season=season_of(r["month"]) if seasonal else None, scaled=False) * scale
        hit += r["P0s"][key] <= _bound(r, sg, level, adapt) + 1e-9
    return dict(n=len(ok), hit=int(hit), cov=float(hit / len(ok)) if ok else None)


def analyze():
    from dpo import config as C
    from dpo.reliability import WindErrorModel
    E = build_model()
    mp = C.RES / "wind_error_model.json"
    md = json.loads(mp.read_text())
    md["scale"] = 1.0
    model = WindErrorModel(md["leads_h"], md["sigma"], md["by_season"], 1.0, md["source"])
    errs = json.loads((C.RES / "E16_errors.json").read_text())
    wind = errs["wind"]
    R = dict(errors=E, climate={}, sets={}, coverage={}, margin={}, uniform={})
    # A. 기후
    for site in SITES:
        w = [r for r in wind if r["site"] == site]
        if not w:
            continue
        mon = {}
        for mth in range(1, 13):
            x = [r for r in w if r["month"] == mth]
            if not x:
                continue
            ws = np.array([r["ws"] for r in x])
            um, vm = np.mean([r["u"] for r in x]), np.mean([r["v"] for r in x])
            mon[str(mth)] = dict(mean=float(ws.mean()), q50=float(np.median(ws)), q90=float(np.quantile(ws, 0.9)), q10=float(np.quantile(ws, 0.1)),
                                 dir_from=float(math.degrees(math.atan2(-um, -vm)) % 360), steadiness=float(math.hypot(um, vm) / ws.mean()),
                                 n_days=len({r["day"] for r in x}),
                                 by_hour={str(h): float(np.mean([r["ws"] for r in x if r["hour"] == h])) for h in range(9, 21)})
        hour = {k: {str(h): float(np.mean([r["ws"] for r in w if r["hour"] == h and r["month"] in ms])) for h in range(9, 21)} for k, ms in SEASONS.items()}
        R["climate"][site] = dict(months=mon, season_hour=hour, n_days=len({r["day"] for r in w}),
                                  overall=dict(mean=float(np.mean([r["ws"] for r in w])), q90=float(np.quantile([r["ws"] for r in w], 0.9))))
    # C·D
    sets = {}
    for f in sorted(C.RES.glob("E16_*_*.json")):
        if f.stem in ("E16_stats", "E16_errors", "E16_fetch_log"):
            continue
        sets[f.stem[4:]] = json.loads(f.read_text())
    for name, rows in sets.items():
        S = dict(n=len(rows), n_days=len({r["day"] for r in rows}), months={}, seasons={}, hours={})

        def agg(sel):
            ok = [r for r in sel if r.get("EX") and r.get("EXv") and r.get("P0s") and r["P0s"]["adapt"] is not None]
            if not ok:
                return None
            ex = np.array([r["EX"]["soc"] for r in ok]); exv = np.array([r["EXv"]["soc"] for r in ok])
            p = np.array([r["P0s"]["adapt"] for r in ok])
            days = np.array([r["day"] for r in ok])
            s1, s2 = (p / ex - 1) * 100, (p / exv - 1) * 100
            return dict(n=len(ok), n_all=len(sel), wind=float(np.mean([r["wind"] for r in ok])), soc_EX=float(ex.mean()), soc_EXv=float(exv.mean()),
                        soc_P=float(p.mean()), soc_P_q90=float(np.quantile(p, 0.9)), save_EX=float(s1.mean()), save_EX_ci=list(_cluster_ci(s1, days)),
                        save_EXv=float(s2.mean()), save_EXv_ci=list(_cluster_ci(s2, days)), lose_EXv=float((s2 > 0.05).mean()),
                        shortest=float(np.mean([r["P0s"]["chosen"] == "shortest" for r in ok])),
                        infeasible=int(sum(1 for r in sel if not r.get("P0s") or r["P0s"]["adapt"] is None)),
                        over_reserve=float(np.mean(p > 80.0)))

        for mth in range(1, 13):
            S["months"][str(mth)] = agg([r for r in rows if r["month"] == mth])
            if S["months"][str(mth)]:
                S["months"][str(mth)]["by_hour"] = {str(h): (lambda a: None if a is None else dict(wind=a["wind"], soc_P=a["soc_P"], save_EX=a["save_EX"]))(
                    agg([r for r in rows if r["month"] == mth and r["hour"] == h])) for h in HOURS}
        for k, ms in SEASONS.items():
            S["seasons"][k] = agg([r for r in rows if r["month"] in ms])
        for h in HOURS:
            S["hours"][str(h)] = agg([r for r in rows if r["hour"] == h])
        S["all"] = agg(rows)
        # 바람 세기별 (경로 모양의 이득이 바람에 따라 달라지는가)
        S["by_wind"] = {lab: agg([r for r in rows if lo <= r["wind"] < hi]) for lab, lo, hi in (("약 (<4)", 0, 4), ("중 (4–8)", 4, 8), ("강 (≥8)", 8, 99))}
        R["sets"][name] = S
        tr = [r for r in rows if r["truth"] and r.get("uni")]
        if tr:
            R["uniform"][name] = dict(n=len(tr), mean=float(np.mean([r["uni"]["mean"] for r in tr])), dev=float(np.mean([r["uni"]["dev"] for r in tr])),
                                      ratio=float(np.sum([r["uni"]["dev"] for r in tr]) / max(np.sum([r["uni"]["rms"] for r in tr]), 1e-9)))
        # 덮임률: 보정 전, 날짜를 반으로 나눠 보정 배율을 정하고 나머지에서 확인
        days = sorted({r["day"] for r in rows if r["truth"]})
        train, test = set(days[0::2]), set(days[1::2])
        cv = dict(raw={}, by_scale={}, n_train_days=len(train), n_test_days=len(test))
        for ad in (False, True):
            for lv in (90, 95):
                cv["raw"][f"{lv}|{int(ad)}"] = coverage(rows, model, 1.0, lv, ad)
                cv["by_scale"][f"{lv}|{int(ad)}"] = {str(k): coverage(rows, model, k, lv, ad)["cov"] for k in SCALES}
        k_star = next((k for k in SCALES if all(coverage(rows, model, k, lv, False, days=train)["cov"] >= lv / 100 for lv in (90, 95))), SCALES[-1])
        cv["scale"] = k_star
        cv["train"] = {f"{lv}|{int(ad)}": coverage(rows, model, k_star, lv, ad, days=train) for ad in (False, True) for lv in (90, 95)}
        cv["test"] = {f"{lv}|{int(ad)}": coverage(rows, model, k_star, lv, ad, days=test) for ad in (False, True) for lv in (90, 95)}
        cv["all_scaled"] = {f"{lv}|{int(ad)}": coverage(rows, model, k_star, lv, ad) for ad in (False, True) for lv in (90, 95)}
        tru = [r for r in rows if r["truth"] and r.get("P0s") and r["P0s"]["frozen"] is not None]
        cv["nominal_below"] = float(np.mean([r["P0s"]["frozen"] <= r["P0s"]["nominal"] for r in tru])) if tru else None
        cv["actual_minus_nominal_pct"] = dict(mean=float(np.mean([(r["P0s"]["frozen"] / r["P0s"]["nominal"] - 1) * 100 for r in tru])),
                                              q95=float(np.quantile([(r["P0s"]["frozen"] / r["P0s"]["nominal"] - 1) * 100 for r in tru], 0.95)),
                                              max=float(np.max([(r["P0s"]["frozen"] / r["P0s"]["nominal"] - 1) * 100 for r in tru]))) if tru else None
        R["coverage"][name] = cv
    # 보정 배율: 조합들 가운데 가장 큰 값을 쓴다 (보수적)
    scale = max([c["scale"] for c in R["coverage"].values()], default=1.0)
    md["scale"] = scale
    mp.write_text(json.dumps(md, ensure_ascii=False, indent=1))
    model = WindErrorModel(md["leads_h"], md["sigma"], md["by_season"], scale, md["source"])
    # 자료 나이별 SOC 여유 (95 % 상한 / 명목 − 1), 15시 출발 경우
    for name, rows in sets.items():
        ok = [r for r in rows if r["truth"] and r["hour"] == 15 and r.get("P0s") and r.get("conf")]
        M = {}
        for sk in list(SEASONS) + ["ALL"]:
            sel = ok if sk == "ALL" else [r for r in ok if r["month"] in SEASONS[sk]]
            if not sel:
                continue
            M[sk] = {}
            for lead in md["leads_h"]:
                sg = model.sigma(lead, season=None if sk == "ALL" else sk)
                e = dict(sigma=sg)
                for ad in (False, True):
                    b = np.array([_bound(r, sg, 95, ad) / r["P0s"]["nominal"] - 1 for r in sel]) * 100
                    e["adapt" if ad else "fixed"] = float(np.mean(b[np.isfinite(b)])) if np.isfinite(b).any() else None
                    e[("adapt" if ad else "fixed") + "_inf"] = float(np.mean(~np.isfinite(b)))
                M[sk][str(int(lead))] = e
        R["margin"][name] = M
    R["scale"] = scale
    seasonal_table(R, md)
    (C.RES / "E16_stats.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    report(R, md)
    figures(R, md, sets, model)


def seasonal_table(R, md):
    """계절표: 결과에서 읽어 만든다. 알고리즘을 계절별로 바꿀 근거가 있는지도 여기서 판정한다."""
    from dpo import config as C
    from dpo.reliability import WindErrorModel
    model = WindErrorModel(md["leads_h"], md["sigma"], md["by_season"], md["scale"], md["source"])
    T = dict(source="E16 (GFS 표본 36일, 2025-11–2026-10) – results/E16_stats.json", default_site=None, sites={}, rule={})
    for name, S in R["sets"].items():
        site = name.split("_")[0]
        if T["default_site"] is None or site == "changwon":
            T["default_site"] = site
        bw = S["by_wind"]
        weak = bw.get("약 (<4)")
        # 판정: 경로 모양의 이득(최단 + 속도 최적화 대비)이 바람 세기 구간마다 0.5 % 넘게 다른가
        gains = {k: v["save_EXv"] for k, v in bw.items() if v}
        differ = (max(gains.values()) - min(gains.values())) > 0.5 if len(gains) > 1 else False
        T["rule"][name] = dict(path_gain_by_wind=gains, differ=bool(differ), threshold_pct=0.5,
                               text=("경로 모양의 이득이 바람 세기에 따라 달라진다 – 다만 안전장치가 두 후보 중 더 싼 쪽을 고르므로 설정을 바꿀 필요는 없다"
                                     if differ else "경로 모양의 이득이 바람 세기에 따라 0.5 % 넘게 달라지지 않는다 – 계절별로 알고리즘을 바꿀 근거가 없다"))
        months = {}
        clim = R["climate"].get(site, {}).get("months", {})
        marg = R["margin"].get(name, {})
        for mth in range(1, 13):
            a = S["months"].get(str(mth))
            if not a:
                continue
            sk = season_of(mth)
            wm = clim.get(str(mth), {}).get("mean", a["wind"])
            cls = "약" if wm < 4 else ("중" if wm < 8 else "강")
            mg = marg.get(sk, marg.get("ALL", {}))
            m6 = mg.get("6", {})
            hrs = {str(h): dict(wind_ms=round(a["by_hour"][str(h)]["wind"], 1), wind_class="약" if a["by_hour"][str(h)]["wind"] < 4 else ("중" if a["by_hour"][str(h)]["wind"] < 8 else "강"),
                                soc_used=round(a["by_hour"][str(h)]["soc_P"], 1)) for h in HOURS if a["by_hour"].get(str(h))}
            best = sorted(hrs, key=lambda h: hrs[h]["soc_used"])[:2]
            margin = None if m6.get("fixed") is None else round(m6["fixed"], 1)
            note = (f"{mth}월 표본 3일: 100 m 평균 풍속 {wm:.1f} m/s ({cls}), 기존 대비 {a['save_EX']:+.1f} %, 최단 + 속도 최적화 대비 {a['save_EXv']:+.1f} %, "
                    f"최단 경로 채택 {a['shortest'] * 100:.0f} %. 6시간 전 예보로 계획할 때 95 % 신뢰수준의 SOC 여유는 명목값의 "
                    + (f"{margin:.1f} %" if margin is not None else "(계산 불가)") + f" (σ {model.sigma(6, season=sk):.2f} m/s).")
            # 설정: 그 달의 바람 세기 구간에서 경로 모양의 이득이 0.5 % 에 못 미치면 에너지 탐색을 생략해도 된다고 적는다
            gcls = bw.get({"약": "약 (<4)", "중": "중 (4–8)", "강": "강 (≥8)"}[cls])
            light = gcls is not None and gcls["save_EXv"] > -0.5
            setting = ("최단 경로 + 속도 최적화 + 속도 갱신 30 s (에너지 탐색은 생략 가능 – 켜 두면 안전장치가 같은 경로를 고른다)" if light
                       else "에너지 탐색 + 안전장치 + 속도 갱신 30 s")
            months[str(mth)] = dict(season=sk, wind_ms=round(wm, 1), wind_class=cls, setting=setting, energy_search_needed=not light,
                                    saving_vs_baseline_pct=round(a["save_EX"], 1), saving_vs_shortest_pct=round(a["save_EXv"], 1),
                                    shortest_share=round(a["shortest"], 2), soc_used=round(a["soc_P"], 1), sigma6=round(model.sigma(6, season=sk), 2),
                                    margin95_pct=margin, margin95_adapt_pct=None if m6.get("adapt") is None else round(m6["adapt"], 1),
                                    margin95_30h_pct=None if mg.get("30", {}).get("fixed") is None else round(mg["30"]["fixed"], 1),
                                    best_hours=[int(h) for h in best], hours=hrs, n_cases=a["n"], note=note)
        T["sites"].setdefault(site, dict(set=name, months=months))
    (C.RES / "seasonal_table.json").write_text(json.dumps(T, ensure_ascii=False, indent=1))
    R["seasonal_rule"] = T["rule"]


def report(R, md):
    print("보정 배율", R["scale"])
    for name, S in R["sets"].items():
        a = S["all"]
        print(f"== {name}: {S['n']}건 {S['n_days']}일 | 기존 대비 {a['save_EX']:+.1f} % [{a['save_EX_ci'][0]:+.1f}, {a['save_EX_ci'][1]:+.1f}] | "
              f"최단+속도 대비 {a['save_EXv']:+.1f} % | 최단 채택 {a['shortest'] * 100:.0f} % | 불가 {a['infeasible']}")
        for k in SEASONS:
            s = S["seasons"][k]
            if s:
                print(f"   {k}: 바람 {s['wind']:.1f} m/s, SOC {s['soc_EX']:.1f} → {s['soc_P']:.1f} %p, 기존 대비 {s['save_EX']:+.1f} %, 최단+속도 대비 {s['save_EXv']:+.1f} %, 최단 채택 {s['shortest'] * 100:.0f} %")
        print("   바람 세기별 경로 이득:", {k: (None if v is None else (round(v["save_EXv"], 2), v["n"])) for k, v in S["by_wind"].items()})
        c = R["coverage"][name]
        print("   덮임률(보정 전) 90/95 고정:", c["raw"]["90|0"], c["raw"]["95|0"], "| 적응:", c["raw"]["90|1"]["cov"], c["raw"]["95|1"]["cov"])
        print("   배율별 95 % 고정:", c["by_scale"]["95|0"])
        print(f"   배율 {c['scale']} → 시험 날짜 90/95 고정: {c['test']['90|0']}, {c['test']['95|0']} | 실제−명목 [%]: {c['actual_minus_nominal_pct']}")
        if name in R["uniform"]:
            print("   오차장의 고른 정도:", R["uniform"][name])
        print("   나이별 여유(전체):", {k: (round(v["fixed"], 2) if v["fixed"] is not None else None, round(v["adapt"], 2) if v["adapt"] is not None else None)
                                  for k, v in R["margin"][name].get("ALL", {}).items()})


def figures(R, md, sets, model):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from dpo import config as C
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200, "savefig.bbox": "tight",
                         "axes.titleweight": "bold"})
    SC = {"DJF": "#1B6CA8", "MAM": "#2CA58D", "JJA": "#E46C0A", "SON": "#7B3FA0"}
    SK = {"DJF": "겨울", "MAM": "봄", "JJA": "여름", "SON": "가을"}
    E = R["errors"]
    # ---- F34 예보 오차와 자료의 나이
    fig, axs = plt.subplots(1, 2, figsize=(13.2, 4.6), gridspec_kw=dict(wspace=0.24))
    ax = axs[0]
    leads = [ld for ld in E["leads_h"] if E["overall"][str(int(ld))]]
    for k in SEASONS:
        y = [E["by_season"][k][str(int(ld))]["sigma"] if E["by_season"][k][str(int(ld))] else np.nan for ld in leads]
        ax.plot(leads, y, "o-", color=SC[k], lw=1.5, ms=4, label=SK[k])
    ax.plot(leads, [E["overall"][str(int(ld))]["sigma"] for ld in leads], "s-", color="#16202A", lw=2.4, ms=6, label="전체")
    ax.set_xticks(leads); ax.set_xlabel("예보의 나이 (발표 뒤 지난 시간) [h]"); ax.set_ylabel("바람 오차 σ (성분별) [m/s]")
    ax.set_ylim(bottom=0); ax.set_title("(a) 예보가 오래될수록 실제와 더 다르다")
    ax.legend(frameon=False, fontsize=9, ncol=5, loc="upper left")
    ax = axs[1]
    n = list(range(1, 13))
    ax.plot(n, [E["persist"]["proxy"][str(k)]["sigma"] for k in n], "o-", color="#9E9E9E", lw=2, label="몇 시간 전 바람을 그대로 쓸 때 (예보 계열로 대용)")
    ax.plot([6], [E["persist"]["analysis_6h"]["sigma"]], "D", color="#D7263D", ms=9, label="6시간 전 분석값을 그대로 쓸 때")
    fl = [ld for ld in leads if ld <= 12]
    ax.plot(fl, [E["overall"][str(int(ld))]["sigma"] for ld in fl], "s-", color="#16202A", lw=2.4, ms=6, label="같은 나이의 예보를 쓸 때")
    ax.set_xlabel("자료의 나이 [h]"); ax.set_ylabel("바람 오차 σ (성분별) [m/s]"); ax.set_ylim(bottom=0)
    ax.set_title("(b) 과거 바람을 그대로 쓰는 것과 예보를 쓰는 것")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.savefig(C.FIG / "F34_forecast_age.png"); plt.close(fig)
    # ---- F35 계절성
    name = "changwon_x650" if "changwon_x650" in R["sets"] else next(iter(R["sets"]))
    S = R["sets"][name]
    LAB = {"changwon": "창원", "pangyo": "판교"}
    fig, axs = plt.subplots(2, 2, figsize=(13.4, 8.6), gridspec_kw=dict(wspace=0.24, hspace=0.36))
    order = [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    xt = [f"{m}월" for m in order]
    ax = axs[0, 0]
    for site, col in (("changwon", "#E46C0A"), ("pangyo", "#1B6CA8")):
        mon = R["climate"].get(site, {}).get("months", {})
        if not mon:
            continue
        y = [mon[str(m)]["mean"] if str(m) in mon else np.nan for m in order]
        lo = [mon[str(m)]["q10"] if str(m) in mon else np.nan for m in order]
        hi = [mon[str(m)]["q90"] if str(m) in mon else np.nan for m in order]
        ax.fill_between(range(12), lo, hi, color=col, alpha=0.15, lw=0)
        ax.plot(range(12), y, "o-", color=col, lw=2, label=f"{LAB[site]} (띠: 10–90 % 분위)")
    ax.set_xticks(range(12)); ax.set_xticklabels(xt, fontsize=8.5); ax.set_ylabel("지면 위 100 m 풍속 [m/s]"); ax.set_ylim(bottom=0)
    ax.set_title("(a) 월별 바람 (9–20시, 매달 3일 표본)"); ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax = axs[0, 1]
    site0 = name.split("_")[0]
    sh = R["climate"][site0]["season_hour"]
    for k in SEASONS:
        ax.plot(range(9, 21), [sh[k][str(h)] for h in range(9, 21)], "o-", color=SC[k], lw=1.8, ms=4, label=SK[k])
    ax.set_xticks(range(9, 21)); ax.set_xlabel("시각 [시, KST]"); ax.set_ylabel("지면 위 100 m 풍속 [m/s]"); ax.set_ylim(bottom=0)
    ax.set_title(f"(b) 시각별 바람 ({LAB[site0]})"); ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper left")
    ax = axs[1, 0]
    ex = [S["months"][str(m)]["soc_EX"] if S["months"].get(str(m)) else np.nan for m in order]
    exv = [S["months"][str(m)]["soc_EXv"] if S["months"].get(str(m)) else np.nan for m in order]
    pp = [S["months"][str(m)]["soc_P"] if S["months"].get(str(m)) else np.nan for m in order]
    w = 0.27
    ax.bar(np.arange(12) - w, ex, w * 0.95, color="#9E9E9E", label="기존: 최단 + 정속 10 m/s")
    ax.bar(np.arange(12), exv, w * 0.95, color="#9CC3E4", label="최단 + 속도 최적화")
    ax.bar(np.arange(12) + w, pp, w * 0.95, color="#1B6CA8", label="제안: 에너지 탐색 + 안전장치")
    ax.set_xticks(range(12)); ax.set_xticklabels(xt, fontsize=8.5); ax.set_ylabel("임무 SOC 소모 [%p]")
    ax.set_title(f"(c) 월별 임무 SOC 소모 ({LAB[site0]} · {name.split('_')[1].upper()}, 편도 1.5–3 km)")
    ax.set_ylim(top=np.nanmax(ex) * 1.28); ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=3, columnspacing=1.0, handlelength=1.4)
    ax = axs[1, 1]
    s1 = [S["months"][str(m)]["save_EX"] if S["months"].get(str(m)) else np.nan for m in order]
    s2 = [S["months"][str(m)]["save_EXv"] if S["months"].get(str(m)) else np.nan for m in order]
    ax.plot(range(12), s1, "o-", color="#1B6CA8", lw=2, label="기존 대비")
    ax.plot(range(12), s2, "s--", color="#E46C0A", lw=1.8, label="최단 + 속도 최적화 대비 (경로 모양의 몫)")
    ax.axhline(0, color="#555", lw=0.8)
    ax.set_xticks(range(12)); ax.set_xticklabels(xt, fontsize=8.5); ax.set_ylabel("제안의 SOC 소모 변화 [%] (음수 = 절감)")
    ax.set_ylim(top=6); ax.set_title("(d) 월별 절감"); ax.legend(frameon=False, fontsize=9, loc="upper left", ncol=2)
    fig.savefig(C.FIG / "F35_seasonality.png"); plt.close(fig)
    # ---- F36 신뢰수준
    from dpo.reliability import mission_confidence
    from dpo.energy import soc_drop
    from dpo.planner import vertical_energy
    rows = sets[name]
    cand = [r for r in rows if r["truth"] and r["hour"] == 15 and r.get("P0s") and r["P0s"]["frozen"] is not None]
    r0 = max(cand, key=lambda r: r["wind"])                # 바람이 가장 센 표본 날짜의 경우를 예로 든다
    fc, tr, d, P = _load_day(r0["site"], r0["day"], r0["drone"])
    a, b = od_pairs(fc, N_PAIRS)[r0["pair"]]
    m = d.m_empty + PAYLOAD[r0["drone"]]
    t0 = (r0["hour"] - KST) * 3600.0
    st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
    e_to, t_to = vertical_energy(fc, d, a[0], a[1], st[0], t0, m, up=True)
    e_ld, _ = vertical_energy(fc, d, b[0], b[1], gl[0], t0 + t_to + r0["P0s"]["T"], m, up=False)
    sg30 = model.sigma(30, season=season_of(r0["month"]))
    c0 = mission_confidence(fc, d, r0["P0s"]["path"], r0["P0s"]["waits"], t0 + t_to, m, sg30, n=400, seed=1, adapt=False, e_fixed=e_to + e_ld, return_samples=True)
    c1 = mission_confidence(fc, d, r0["P0s"]["path"], r0["P0s"]["waits"], t0 + t_to, m, sg30, n=400, seed=1, adapt=True, e_fixed=e_to + e_ld, return_samples=True)
    fig, axs = plt.subplots(1, 3, figsize=(16.2, 4.7), gridspec_kw=dict(wspace=0.27, width_ratios=[1.15, 1.0, 1.0]))
    ax = axs[0]
    lo, hi = min(c0["samples"].min(), c1["samples"].min()), max(c0["samples"].max(), c1["samples"].max())
    bins = np.linspace(lo, hi, 36)
    ax.hist(c0["samples"], bins=bins, color="#9E9E9E", alpha=0.75, label="계획한 속도 명령 그대로")
    ax.hist(c1["samples"], bins=bins, color="#1B6CA8", alpha=0.6, label="비행 중 속도 갱신")
    ymax = ax.get_ylim()[1]
    for x, lb, col, yy in ((c0["nominal"]["soc_used"], "명목 (예보대로)", "#16202A", 0.97), (c0["soc_used_q"][95], "95 % 상한 (명령 그대로)", "#D7263D", 0.86),
                           (c1["soc_used_q"][95], "95 % 상한 (속도 갱신)", "#1B6CA8", 0.75)):
        ax.axvline(x, color=col, lw=1.6, ls="--")
        ax.text(x, ymax * yy, f" {lb} {x:.1f}", color=col, fontsize=8.5, va="top", ha="left")
    ax.set_xlim(lo - (hi - lo) * 0.02, hi + (hi - lo) * 0.42)
    ax.set_xlabel("임무 SOC 소모 [%p]"); ax.set_ylabel("표본 수 (400개 중)")
    ax.set_title(f"(a) 한 임무의 SOC 소모 분포\n{r0['day'][4:6]}월 {r0['day'][6:]}일 15시, 30시간 전 예보 (σ {sg30:.1f} m/s)", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8.5, loc="center right")
    ax = axs[1]
    cv = R["coverage"][name]
    labs = ["90 %\n명령 그대로", "95 %\n명령 그대로", "90 %\n속도 갱신", "95 %\n속도 갱신"]
    keys = ["90|0", "95|0", "90|1", "95|1"]
    raw = [cv["raw"][k]["cov"] * 100 for k in keys]
    tst = [cv["test"][k]["cov"] * 100 for k in keys]
    x = np.arange(4)
    ax.bar(x - 0.2, raw, 0.38, color="#9CC3E4", label="보정 전 (전체 날짜)")
    ax.bar(x + 0.2, tst, 0.38, color="#1B6CA8", label=f"σ × {cv['scale']:g} 보정 뒤 (보정에 안 쓴 날짜)")
    for xx, nominal in zip(x, (90, 95, 90, 95)):
        ax.plot([xx - 0.42, xx + 0.42], [nominal, nominal], color="#D7263D", lw=1.6)
    for xx, v in zip(x - 0.2, raw):
        ax.text(xx, v + 1, f"{v:.0f}", ha="center", fontsize=8.5)
    for xx, v in zip(x + 0.2, tst):
        ax.text(xx, v + 1, f"{v:.0f}", ha="center", fontsize=8.5)
    ax.set_xticks(x); ax.set_xticklabels(labs, fontsize=8.5); ax.set_ylim(0, 122); ax.set_ylabel("실제 SOC 소모가 상한 안에 든 비율 [%]")
    ax.set_title(f"(b) 신뢰수준의 검증 (빨간 선 = 목표)\n{cv['raw']['95|0']['n']}건, 실제 = 6시간 뒤 발표된 예보", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", ncol=1)
    ax = axs[2]
    M = R["margin"][name]
    lds = [int(v) for v in md["leads_h"]]
    for k in SEASONS:
        if k in M:
            ax.plot(lds, [M[k][str(v)]["fixed"] for v in lds], "o-", color=SC[k], lw=1.5, ms=4, label=SK[k])
    if "ALL" in M:
        ax.plot(lds, [M["ALL"][str(v)]["fixed"] for v in lds], "s-", color="#16202A", lw=2.4, ms=6, label="전체 (명령 그대로)")
        ax.plot(lds, [M["ALL"][str(v)]["adapt"] for v in lds], "s--", color="#16202A", lw=1.6, ms=5, mfc="white", label="전체 (속도 갱신)")
    ax.set_xticks(lds); ax.set_xlabel("예보의 나이 [h]"); ax.set_ylabel("95 % 신뢰수준에 필요한 SOC 여유 [명목값의 %]"); ax.set_ylim(bottom=0)
    ax.set_title("(c) 자료가 오래될수록 필요한 여유", fontsize=10.5); ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=2)
    fig.savefig(C.FIG / "F36_confidence.png"); plt.close(fig)
    R["example"] = dict(day=r0["day"], hour=r0["hour"], wind=r0["wind"], sigma=sg30, nominal=c0["nominal"]["soc_used"], q95_fixed=c0["soc_used_q"][95],
                        q95_adapt=c1["soc_used_q"][95], actual_frozen=r0["P0s"]["frozen"], actual_adapt=r0["P0s"]["adapt"])
    (C.RES / "E16_stats.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "model":
        build_model()
    elif cmd == "run":
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    else:
        analyze()
