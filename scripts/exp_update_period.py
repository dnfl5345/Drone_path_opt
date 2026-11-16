"""E14: 기상 정보 갱신 주기의 설계 – 속도 명령을 계획 때 정한 대로 따를 때, 몇 분마다 갱신해야 하는가

E13 의 평가는 '기체가 실제 바람에 맞춰 대지속도를 매 순간 다시 고른다'고 가정했다. 실제 미션 파일(ArduPilot)은
구간 대지속도를 계획 때 정해 내려보내므로, 여기서는 그 가정을 풀고 다음을 비교한다.

  FROZEN   출발 전 예보로 경로와 구간 속도를 정하고, 끝까지 그대로 비행 (갱신 없음)
  S_Δ      경로는 그대로 두고, Δ초마다 기체가 잰 바람으로 예보를 보정해 남은 구간의 속도만 다시 정함
  R_Δ      Δ초마다 보정한 바람으로 남은 경로와 속도를 모두 다시 계획 (새 경로를 무조건 채택)
  K_Δ      R_Δ 와 같되, 보정한 바람에서 새 경로의 예상 에너지가 지금 경로보다 KEEP_MARGIN 넘게 적을 때만 바꿈 (제안)
  ADAPT    속도를 매 순간 실제 바람에 맞춤 (Δ → 0 의 극한)
  ORACLE   실제 바람을 미리 알고 계획 (상한)

예보 오차는 두 가지로 준다.
  real     계획 00Z 예보, 실제 06Z 예보 (7일)
  rot/spd  예보의 풍향을 θ 만큼 돌리거나 풍속을 배율로 바꾼 것을 실제로 둠 (예보가 크게 틀린 경우)

사용: PYTHONPATH=src python scripts/exp_update_period.py run changwon [x650]
      PYTHONPATH=src python scripts/exp_update_period.py analyze
"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
from exp_superiority import DAYS, KST, N_PAIRS_ROLL, PAYLOAD, _cluster_ci, _load_day, od_pairs  # noqa: E402

ERR_CASES = [("real", None, None), ("rot30", 30.0, 1.0), ("rot60", 60.0, 1.0), ("rot90", 90.0, 1.0), ("rot180", 180.0, 1.0),
             ("spd0.6", 0.0, 0.6), ("spd1.4", 0.0, 1.4)]
STRESS_DAYS = ["20261027", "20261031", "20261001"]
HOURS = [15, 18]
PERIODS = [30.0, 60.0, 120.0]
STEP = 12.5
KEEP_MARGIN = 0.01      # 새 경로가 남은 구간 예상 에너지를 1 % 넘게 줄일 때만 채택
INF = 1e17


def fly_frozen(site, d, path, t0, m, vg_cmd):
    """경로를 '명령 대지속도'대로 실제 바람에서 비행. 명령 속도를 못 내면(대기속도·상승률 한계) 낼 수 있는 속도까지 낮춘다.
    반환: 부분 구간 끝마다의 기록 (t, e, k, j, i, seg, vg). 비행 불가면 None."""
    from dpo.energy import edge_energy_per_m
    from dpo.planner import _wind
    rho, prm = site.rho_layers(), d.params()
    pts = np.array(path, float)
    t, e, q, rec = t0, 0.0, 0, []
    for n in range(len(pts) - 1):
        k0, j0, i0 = pts[n]
        k1, j1, i1 = pts[n + 1]
        Dx, Dy, Dz = (i1 - i0) * site.dx, (j1 - j0) * site.dx, (k1 - k0) * site.dz
        L = math.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
        if L < 1e-9:
            continue
        lh = math.hypot(Dx, Dy)
        ns = max(int(math.ceil(L / STEP)), 1)
        for s in range(ns):
            f = (s + .5) / ns
            kk = int(np.clip(round(k0 + (k1 - k0) * f), 0, site.nz - 1))
            jj = int(np.clip(round(j0 + (j1 - j0) * f), 0, site.ny - 1))
            ii = int(np.clip(round(i0 + (i1 - i0) * f), 0, site.nx - 1))
            wu, wv = _wind(site.U, site.V, site.t_axis, t, kk, jj, ii)
            v = float(vg_cmd[q]) if q < len(vg_cmd) else float(vg_cmd[-1])
            q += 1
            epm, vg = INF, 0.0
            for vv in np.arange(max(v, 3.0), 2.9, -0.5):
                epm, vg = edge_energy_per_m(Dx, Dy, Dz, wu, wv, m, rho[kk], prm, d.va_max, d.vz_up, d.vz_dn, np.array([vv]))
                if epm < INF:
                    break
            if epm >= INF:
                return None
            lp = L / ns
            v3 = vg if lh < 1e-6 else vg * L / lh
            e += epm * lp
            t += lp / v3
            g = (s + 1) / ns
            rec.append((t, e, k0 + (k1 - k0) * g, j0 + (j1 - j0) * g, i0 + (i1 - i0) * g, n, vg))
    return np.array(rec)


def speed_plan(site, d, path, t0, m):
    """(보정한) 예보 바람에서 부분 구간별 최적 대지속도."""
    from dpo.planner import simulate
    sim = simulate(site, d, path, np.zeros(len(path)), t0, m)
    return None if sim is None else sim["vg"]


def _dedupe(path, sp):
    """갱신 지점이 다음 꼭짓점과 겹치면 길이 0 인 구간이 생기므로 뺀다."""
    while len(path) > 1 and np.abs((path[1] - path[0]) * np.array([sp.dz, sp.dx, sp.dx])).max() < 1e-3:
        path = path[1:]
    return path


def fly(P, tr, d, a, b, t0, m, dt, mode, bias=True):
    """mode: 'frozen' 갱신 없음 / 'speed' 속도만 갱신 / 'replan' 경로와 속도 갱신 / 'keep' 더 나을 때만 경로 교체."""
    from dpo.energy import soc_drop
    from dpo.planner import _wind, simulate, vertical_energy
    sp = P.site
    U0, V0 = sp.U.copy(), sp.V.copy()
    st, gl = sp.lowest_free_k(*a), sp.lowest_free_k(*b)
    e_to, t_to = vertical_energy(tr, d, a[0], a[1], st[0], t0, m, up=True)
    t, e, du, dv, n_upd, n_adopt = t0 + t_to, e_to, 0.0, 0.0, 0, 0
    r = P.plan(st, gl, t_start=t, mass=m, mode=1)
    if r is None:
        return None
    path = np.array(r["path"], float)
    ok = True
    try:
        for _ in range(200):
            vg = speed_plan(sp, d, path, t, m)
            if vg is None:
                ok = False
                break
            rec = fly_frozen(tr, d, path, t, m, vg)
            if rec is None:
                ok = False
                break
            t_next = t + dt
            idx = int(np.searchsorted(rec[:, 0], t_next))
            if mode in ("replan", "keep"):         # 경로를 다시 짤 때는 다음 꼭짓점에 닿은 순간에 갱신 (격자점에서 출발하도록)
                while idx < len(rec) - 1 and rec[idx, 5] == rec[idx + 1, 5]:
                    idx += 1
            if mode == "frozen" or idx >= len(rec) - 1:
                e += rec[-1, 1]
                t = rec[-1, 0]
                break
            e += rec[idx, 1]
            t = rec[idx, 0]
            pos, seg = rec[idx, 2:5], int(rec[idx, 5])
            n_upd += 1
            if bias:                               # 기체가 잰 현재 위치의 바람과 예보의 차이를 예보 전체에 더한다
                kk = int(np.clip(round(pos[0]), 0, sp.nz - 1)); jj = int(np.clip(round(pos[1]), 0, sp.ny - 1))
                ii = int(np.clip(round(pos[2]), 0, sp.nx - 1))
                wt = _wind(tr.U, tr.V, tr.t_axis, t, kk, jj, ii)
                wf = _wind(U0, V0, sp.t_axis, t, kk, jj, ii)
                du = 0.4 * du + 0.6 * (wt[0] - wf[0])
                dv = 0.4 * dv + 0.6 * (wt[1] - wf[1])
                sp.U[:] = U0 + du
                sp.V[:] = V0 + dv
            old = _dedupe(np.vstack([pos[None, :], path[seg + 1:]]), sp)
            if mode in ("replan", "keep"):
                cur = tuple(int(round(v)) for v in pos)          # 꼭짓점에서 갱신하므로 격자점이다
                r2 = P.plan(cur, gl, t_start=t, mass=m, mode=1) if sp.free[cur] else None
                if mode == "replan":
                    if r2 is None:
                        ok = False
                        break
                    path = _dedupe(np.array(r2["path"], float), sp)
                else:                              # 보정한 예보에서 예상 에너지를 비교해 더 나을 때만 바꾼다
                    path = old
                    if r2 is not None and len(old) >= 2:
                        new = _dedupe(np.array(r2["path"], float), sp)
                        so = simulate(sp, d, old, np.zeros(len(old)), t, m)
                        sn = simulate(sp, d, new, np.zeros(len(new)), t, m) if len(new) >= 2 else None
                        if sn is not None and (so is None or sn["e"][-1] < so["e"][-1] * (1 - KEEP_MARGIN)):
                            path = new
                            n_adopt += 1
            else:
                path = old
            if len(path) < 2:
                break
    finally:
        sp.U[:] = U0
        sp.V[:] = V0
    if not ok:
        return None
    e_ld, t_ld = vertical_energy(tr, d, b[0], b[1], gl[0], t, m, up=False)
    return dict(soc=float(soc_drop(e + e_ld, d)), T=float(t + t_ld - t0), n_upd=n_upd, n_adopt=n_adopt)


def worker(args):
    site_key, ymd, dkey = args
    from dpo.energy import soc_drop
    from dpo.planner import Planner, simulate, vertical_energy
    fc, tr06, d, P = _load_day(site_key, ymd, dkey)
    m = d.m_empty + PAYLOAD
    pairs = od_pairs(fc, N_PAIRS_ROLL)
    rows = []
    t_begin = time.time()
    for name, rot, scale in ERR_CASES:
        if name != "real" and ymd not in STRESS_DAYS:
            continue
        if name == "real":
            tr = tr06
        else:
            tr = copy.copy(fc)
            th = math.radians(rot)
            tr.U = ((fc.U * math.cos(th) - fc.V * math.sin(th)) * scale).astype(fc.U.dtype)
            tr.V = ((fc.U * math.sin(th) + fc.V * math.cos(th)) * scale).astype(fc.V.dtype)
        PO = Planner(tr, d)
        for h in (HOURS if name == "real" else HOURS[:1]):
            t0 = (h - KST) * 3600.0
            it, ifc = int(np.argmin(np.abs(tr.t_axis - t0))), int(np.argmin(np.abs(fc.t_axis - t0)))
            free2 = fc.free.any(axis=0)
            err = float(np.sqrt(((fc.U[ifc] - tr.U[it]) ** 2 + (fc.V[ifc] - tr.V[it]) ** 2)[:, free2].mean()))
            ws = float(np.hypot(tr.U[it], tr.V[it])[:, free2].mean())
            for q, (a, b) in enumerate(pairs):
                row = dict(site=site_key, day=ymd, hour=h, pair=q, case=name, err=err, wind=ws, drone=dkey)
                row["FROZEN"] = fly(P, tr, d, a, b, t0, m, 1e9, "frozen")
                for dt in PERIODS:
                    row[f"S_{dt:.0f}"] = fly(P, tr, d, a, b, t0, m, dt, "speed")
                for dt in PERIODS[1:]:
                    row[f"R_{dt:.0f}"] = fly(P, tr, d, a, b, t0, m, dt, "replan")
                    row[f"K_{dt:.0f}"] = fly(P, tr, d, a, b, t0, m, dt, "keep")
                # 속도를 매 순간 실제 바람에 맞춤 / 실제 바람을 미리 앎
                st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
                e_to, t_to = vertical_energy(tr, d, a[0], a[1], st[0], t0, m, up=True)
                for key, pl in (("ADAPT", P), ("ORACLE", PO)):
                    r = pl.plan(st, gl, t_start=t0 + t_to, mass=m, mode=1)
                    sim = None if r is None else simulate(tr, d, r["path"], r["wait_before"], t0 + t_to, m, "opt", U=tr.U, V=tr.V,
                                                          tax=tr.t_axis)
                    if sim is None:
                        row[key] = None
                        continue
                    e_ld, t_ld = vertical_energy(tr, d, b[0], b[1], gl[0], sim["t"][-1], m, up=False)
                    row[key] = dict(soc=float(soc_drop(e_to + sim["e"][-1] + e_ld, d)), T=float(sim["t"][-1] + t_ld - t0))
                rows.append(row)
    print(f"[E14 {site_key} {ymd}] {len(rows)}건, {time.time() - t_begin:.0f} s", flush=True)
    return rows


def run(site_key, dkey="x650"):
    from dpo import config as C
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(worker, [(site_key, d_, dkey) for d_ in DAYS]))
    rows = [r for day in res for r in day]
    (C.RES / f"E14_{site_key}_{dkey}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows))


def plus_worker(args):
    """이미 계산한 결과에 K_Δ(더 나을 때만 교체)만 덧붙인다."""
    site_key, ymd, dkey, rows = args
    fc, tr06, d, P = _load_day(site_key, ymd, dkey)
    m = d.m_empty + PAYLOAD
    pairs = od_pairs(fc, N_PAIRS_ROLL)
    cases = {n: (r_, s_) for n, r_, s_ in ERR_CASES}
    trs = {}
    for row in rows:
        name = row["case"]
        if name not in trs:
            if name == "real":
                trs[name] = tr06
            else:
                rot, scale = cases[name]
                tr = copy.copy(fc)
                th = math.radians(rot)
                tr.U = ((fc.U * math.cos(th) - fc.V * math.sin(th)) * scale).astype(fc.U.dtype)
                tr.V = ((fc.U * math.sin(th) + fc.V * math.cos(th)) * scale).astype(fc.V.dtype)
                trs[name] = tr
        a, b = pairs[row["pair"]]
        t0 = (row["hour"] - KST) * 3600.0
        for dt in PERIODS[1:]:
            row[f"K_{dt:.0f}"] = fly(P, trs[name], d, a, b, t0, m, dt, "keep")
    print(f"[E14+ {site_key} {ymd}] {len(rows)}건", flush=True)
    return rows


def plus(site_key, dkey="x650"):
    from dpo import config as C
    f = C.RES / f"E14_{site_key}_{dkey}.json"
    rows = json.loads(f.read_text())
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(plus_worker, [(site_key, d_, dkey, [r for r in rows if r["day"] == d_]) for d_ in DAYS]))
    rows = [r for day in res for r in day]
    f.write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows))


POLS = ["S_120", "S_60", "S_30", "R_120", "R_60", "K_120", "K_60", "ADAPT", "ORACLE"]


def analyze():
    from dpo import config as C
    R = {}
    for f in sorted(C.RES.glob("E14_*_*.json")):
        if f.name == "E14_stats.json":
            continue
        rows = json.loads(f.read_text())
        S = {}
        for name, _, _ in ERR_CASES:
            rr = [r for r in rows if r["case"] == name]
            ok = [r for r in rr if r.get("FROZEN") and r.get("ORACLE")]
            if not ok:
                continue
            e = dict(n=len(rr), n_ok=len(ok), frozen_fail=int(sum(1 for r in rr if not r.get("FROZEN"))),
                     err=float(np.mean([r["err"] for r in ok])), wind=float(np.mean([r["wind"] for r in ok])),
                     T=float(np.mean([r["FROZEN"]["T"] for r in ok])),
                     penalty_pct=float(np.mean([(r["FROZEN"]["soc"] / r["ORACLE"]["soc"] - 1) * 100 for r in ok])))
            days = np.array([r["day"] for r in ok])
            e["penalty_ci"] = list(_cluster_ci(np.array([(r["FROZEN"]["soc"] / r["ORACLE"]["soc"] - 1) * 100 for r in ok]), days))
            for k in POLS:
                o2 = [r for r in ok if r.get(k)]
                if not o2:
                    continue
                pct = np.array([(r[k]["soc"] / r["FROZEN"]["soc"] - 1) * 100 for r in o2])
                gap = sum(r["FROZEN"]["soc"] - r["ORACLE"]["soc"] for r in o2)
                got = sum(r["FROZEN"]["soc"] - r[k]["soc"] for r in o2)
                e[k] = dict(n=len(o2), mean_pct=float(pct.mean()), ci=list(_cluster_ci(pct, np.array([r["day"] for r in o2]))),
                            win=float((pct < -0.05).mean()), lose=float((pct > 0.05).mean()),
                            recovered=float(got / gap) if gap > 1e-9 else None,
                            n_upd=float(np.mean([r[k].get("n_upd", 0) for r in o2])))
            S[name] = e
            print(f"[{f.stem[4:]} {name}] 예보 오차 {e['err']:.1f} m/s, 갱신 없으면 상한 대비 {e['penalty_pct']:+.1f} % (n={e['n_ok']}, 실패 {e['frozen_fail']}) | " +
                  " | ".join(f"{k} {e[k]['mean_pct']:+.1f}%" + (f"({e[k]['recovered'] * 100:.0f}%)" if e[k]["recovered"] is not None else "")
                             for k in POLS if k in e))
        R[f.stem[4:]] = S
    (C.RES / "E14_stats.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R)


def figure(R):
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
    SETLAB = {"changwon_x650": "창원 · X650", "pangyo_x650": "판교 · X650", "pangyo_m100": "판교 · M100"}
    lab = {"real": "실제 예보 오차\n(00Z ↔ 06Z)", "rot30": "풍향 30°", "rot60": "풍향 60°", "rot90": "풍향 90°", "rot180": "풍향 180°",
           "spd0.6": "풍속 0.6배", "spd1.4": "풍속 1.4배"}
    pol = [("S_120", "속도만, 2분마다", "#9CC3E4"), ("S_60", "속도만, 1분마다", "#4F94CD"), ("S_30", "속도만, 30초마다", "#1B6CA8"),
           ("R_60", "경로+속도, 1분마다 (무조건 교체)", "#E46C0A"), ("K_60", "경로+속도, 1분마다 (더 나을 때만 교체)", "#7B3FA0"),
           ("ADAPT", "속도를 매 순간 맞춤", "#2CA58D"), ("ORACLE", "실제 바람을 미리 앎 (상한)", "#555555")]
    fig, axs = plt.subplots(len(R), 2, figsize=(16.5, 4.9 * len(R)), squeeze=False,
                            gridspec_kw=dict(width_ratios=[1.0, 1.9], wspace=0.16, hspace=0.42))
    for row, (sname, S) in enumerate(R.items()):
        names = [n for n, _, _ in ERR_CASES if n in S]
        x = np.arange(len(names))
        ax = axs[row, 0]
        pen = np.array([S[n]["penalty_pct"] for n in names])
        lo = np.array([S[n]["penalty_ci"][0] for n in names])
        hi = np.array([S[n]["penalty_ci"][1] for n in names])
        ax.bar(x, pen, 0.62, color=["#9E9E9E"] + ["#D7263D"] * (len(names) - 1),
               yerr=[np.maximum(pen - lo, 0), np.maximum(hi - pen, 0)], error_kw=dict(ecolor="#444", lw=1, capsize=3))
        top = max(float(hi.max()), 1.0)
        for xx, v, h, n in zip(x, pen, hi, names):
            ax.text(xx, max(h, 0) + top * 0.04, f"{v:+.1f} %\n({S[n]['err']:.1f} m/s)", ha="center", va="bottom", fontsize=8)
        ax.axhline(0, color="#555", lw=0.8)
        ax.set_xticks(x); ax.set_xticklabels([lab[n] for n in names], fontsize=8)
        ax.set_ylabel("실제 바람을 미리 알 때 대비 SOC 증가 [%]")
        ax.set_ylim(min(0.0, float(lo.min())) - top * 0.05, top * 1.42)
        ax.set_title(f"{SETLAB.get(sname, sname)} (a) 갱신 없이 계획대로 날 때의 손해\n괄호: 예보와 실제 바람의 차이", fontsize=10.5)
        ax = axs[row, 1]
        w = 0.84 / len(pol)
        for q, (k, lb, c) in enumerate(pol):
            v = np.array([S[n][k]["mean_pct"] if k in S[n] else np.nan for n in names])
            l2 = np.array([S[n][k]["ci"][0] if k in S[n] else np.nan for n in names])
            h2 = np.array([S[n][k]["ci"][1] if k in S[n] else np.nan for n in names])
            ax.bar(x + (q - (len(pol) - 1) / 2) * w, v, w * 0.92, color=c, label=lb,
                   yerr=[np.maximum(v - l2, 0), np.maximum(h2 - v, 0)], error_kw=dict(ecolor="#333", lw=0.7, capsize=1.5))
        ax.axhline(0, color="#555", lw=0.8)
        ax.set_xticks(x); ax.set_xticklabels([lab[n] for n in names], fontsize=8.5)
        ax.set_ylabel("갱신 없는 비행 대비 SOC 변화 [%] (음수 = 절감)")
        ax.set_title(f"{SETLAB.get(sname, sname)} (b) 비행 중 갱신 방식별 효과 (평균, 95 % 구간)", fontsize=10.5)
        if row == 0:
            ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="lower center", bbox_to_anchor=(0.36, 1.12), columnspacing=1.2)
    fig.savefig(C.FIG / "F29_update_period.png")
    plt.close(fig)


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    elif sys.argv[1] == "plus":
        plus(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    else:
        analyze()
