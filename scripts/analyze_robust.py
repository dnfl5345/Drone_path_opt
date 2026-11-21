"""E10/E10b 통계 분석 + 논문용 그림 (results/robust_stats.json, F16, F17).

사용: PYTHONPATH=src python scripts/analyze_robust.py
"""
from __future__ import annotations

import json
import math
import pickle
import sys

import numpy as np
from scipy import stats

sys.path.insert(0, "scripts")
from dpo import config as C  # noqa: E402
from dpo.robust_eval import wilson  # noqa: E402

rng = np.random.default_rng(7)


def L(name):
    with open(C.RES / f"{name}.pkl", "rb") as f:
        return pickle.load(f)


def boot_ci(x, n=10000, f=np.mean):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return (np.nan, np.nan, np.nan)
    bs = [f(rng.choice(x, len(x))) for _ in range(n)]
    return (float(f(x)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)))


def open_loop(E10, keys, wind="plan"):
    rows = E10["rows"]
    out = {}
    for k in keys:
        n_hit = n_tot = 0
        feas = 0
        per_scen_p = []
        for r in rows:
            v = r.get(k)
            if v is None:
                continue
            if not v.get("feasible"):
                continue
            feas += 1
            n_hit += v[wind]["n_hit"]; n_tot += v[wind]["K"]
            per_scen_p.append(v[wind]["n_hit"] / v[wind]["K"])
        p, lo, hi = wilson(n_hit, n_tot)
        out[k] = dict(feasible=feas, n_scen=len(rows), p_coll=p, p_lo=lo, p_hi=hi, n_hit=n_hit, n=n_tot,
                      scen_any=int(np.sum(np.array(per_scen_p) > 0)),
                      p_scen_max=float(np.max(per_scen_p)) if per_scen_p else np.nan)
    return out


def energy_overhead(E10, k, ref="N", wind="plan"):
    """시나리오별 (E_k / E_ref − 1)·100 (둘 다 실행 가능한 시나리오만)."""
    ov = []
    for r in E10["rows"]:
        a, b = r.get(k), r.get(ref)
        if a and b and a.get("feasible") and b.get("feasible"):
            ov.append((a[wind]["E"] / b[wind]["E"] - 1) * 100)
    return np.array(ov)


def paired(E10, k1, k2, wind="plan"):
    x, y = [], []
    for r in E10["rows"]:
        a, b = r.get(k1), r.get(k2)
        if a and b and a.get("feasible") and b.get("feasible"):
            x.append(a[wind]["E"]); y.append(b[wind]["E"])
    x, y = np.array(x), np.array(y)
    d = (x / y - 1) * 100
    w = stats.wilcoxon(x, y) if len(x) > 5 and np.any(x != y) else None
    return dict(n=len(x), mean_diff_pct=boot_ci(d), median_diff_pct=float(np.median(d)) if len(d) else np.nan,
                wins=int(np.sum(x < y - 1e-6)), losses=int(np.sum(x > y + 1e-6)),
                wilcoxon_p=float(w.pvalue) if w is not None else np.nan)


TUBE_EPS = ["0.2", "0.1", "0.05", "0.01", "0.002", "0.001", "0.0001"]
BUF_EPS = ["0.2", "0.1", "0.05", "0.01"]
MIX_EPS = ["0.2", "0.05", "0.01", "0.001"]


def _unified(E10):
    """E10 + E10c → 시나리오별 {방법: (E, 가우시안 충돌수, Student-t 충돌수, K)}."""
    try:
        E10c = L("E10c")
    except FileNotFoundError:
        return None
    c = {r["id"]: r for r in E10c["rows"]}
    try:
        dd = {r["id"]: r for r in L("E10d")["rows"]}
    except FileNotFoundError:
        dd = {}
    U = []
    for r in E10["rows"]:
        d = {}
        for k, v in list(r.items()) + list(c.get(r["id"], {}).items()) + list(dd.get(r["id"], {}).items()):
            if not isinstance(v, dict) or not v.get("feasible") or "_sv" in k:
                continue
            key = "B0.05" if k == "B" else k
            E = v["plan"]["E"] if "E" in v["plan"] else v["E"]
            d[key] = (E, v["plan"]["n_hit"], v["t3"]["n_hit"], v["plan"]["K"])
        U.append(d)
    return U


def frontier(E10):
    """위험–에너지 곡선: 튜브(ε 7종) vs 고정 버퍼(ε 4종), 가우시안(모델 일치)·Student-t(불일치) 실측 충돌률."""
    U = _unified(E10)
    if U is None:
        return None
    keys = ["N"] + [f"R{e}" for e in TUBE_EPS] + [f"B{e}" for e in BUF_EPS]
    if any(f"M{MIX_EPS[0]}" in d for d in U):
        keys += [f"M{e}" for e in MIX_EPS]
    common = [d for d in U if all(k in d for k in keys)]          # 모든 방법이 실행 가능한 시나리오만
    out = dict(n_scen=len(common), methods={})
    for k in keys:
        E = np.array([d[k][0] for d in common]); EN = np.array([d["N"][0] for d in common])
        hg = sum(d[k][1] for d in common); ht = sum(d[k][2] for d in common); n = sum(d[k][3] for d in common)
        out["methods"][k] = dict(overhead=boot_ci((E / EN - 1) * 100), gauss=wilson(hg, n), t3=wilson(ht, n),
                                 n_hit_gauss=hg, n_hit_t3=ht, n=n)
    # 같은 에너지에서의 비교: 고정 버퍼 B(0.05) 보다 에너지가 적게 드는 튜브 중 Student-t 위험이 가장 작은 것
    eB = out["methods"]["B0.05"]["overhead"][0]
    cand = [k for k in keys if k.startswith("R") and out["methods"][k]["overhead"][0] <= eB]
    if cand:
        best = min(cand, key=lambda k: out["methods"][k]["t3"][0])
        x = np.array([d[best][0] for d in common]); y = np.array([d["B0.05"][0] for d in common])
        out["energy_matched"] = dict(tube=best, B="B0.05", t3_tube=out["methods"][best]["t3"],
                                     t3_B=out["methods"]["B0.05"]["t3"],
                                     energy_diff_pct=boot_ci((x / y - 1) * 100),
                                     wilcoxon_p=float(stats.wilcoxon(x, y).pvalue), wins=int(np.sum(x < y)))
    # 위험이 같은 수준에서의 비교: Student-t 위험이 B(0.05) 이하인 튜브 중 에너지 최소
    tB = out["methods"]["B0.05"]["t3"][0]
    cand = [k for k in keys if k.startswith("R") and out["methods"][k]["t3"][0] <= tB]
    if cand:
        best = min(cand, key=lambda k: out["methods"][k]["overhead"][0])
        x = np.array([d[best][0] for d in common]); y = np.array([d["B0.05"][0] for d in common])
        out["risk_matched"] = dict(tube=best, t3_tube=out["methods"][best]["t3"], t3_B=out["methods"]["B0.05"]["t3"],
                                   energy_diff_pct=boot_ci((x / y - 1) * 100),
                                   wilcoxon_p=float(stats.wilcoxon(x, y).pvalue), wins=int(np.sum(x < y)))
    # 혼합 튜브 vs 고정 버퍼 (같은 ε 끼리 짝 비교: 에너지, 두 오차 모델의 위험)
    if "M0.05" in keys:
        mm = {}
        for e in ("0.2", "0.05", "0.01"):
            x = np.array([d[f"M{e}"][0] for d in common]); y = np.array([d[f"B{e}"][0] for d in common])
            mm[e] = dict(energy_diff_pct=boot_ci((x / y - 1) * 100), wilcoxon_p=float(stats.wilcoxon(x, y).pvalue),
                         wins=int(np.sum(x < y)), gauss_M=out["methods"][f"M{e}"]["gauss"],
                         gauss_B=out["methods"][f"B{e}"]["gauss"], t3_M=out["methods"][f"M{e}"]["t3"],
                         t3_B=out["methods"][f"B{e}"]["t3"])
        out["mix_vs_B"] = mm
    return out


def fig_frontier(F):
    import os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200,
                         "savefig.bbox": "tight", "axes.titleweight": "bold"})
    M = F["methods"]
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.3), sharey=False)
    for ax, kind, title in ((axs[0], "gauss", "(a) 가우시안 오차 (계획 가정과 일치)"),
                            (axs[1], "t3", "(b) Student-t 오차, 자유도 3 (꼬리가 두꺼움)")):
        series = [("R", TUBE_EPS, "#1B6CA8", "시간 증가 튜브 (λ=0)", "o"),
                  ("B", BUF_EPS, "#E07B39", "고정 버퍼 σ(1.3T) (λ=1)", "s")]
        if "M0.05" in M:
            series.append(("M", MIX_EPS, "#2CA58D", "혼합 튜브 (λ=0.5)", "^"))
        for pre, eps_list, col, lab, mk in series:
            xs = [M[f"{pre}{e}"]["overhead"][0] for e in eps_list]
            ps = [M[f"{pre}{e}"][kind] for e in eps_list]
            ys = [p[0] * 100 for p in ps]
            lo = [max(p[0] - p[1], 0) * 100 for p in ps]; hi = [max(p[2] - p[0], 0) * 100 for p in ps]
            ax.errorbar(xs, ys, yerr=[lo, hi], color=col, marker=mk, ms=6, lw=1.8, capsize=3, label=lab)
            off = {"R": (-6, 7), "B": (4, -13), "M": (6, 2)}[pre]
            for x, y, e, p_ in zip(xs, ys, eps_list, ps):
                if kind == "gauss" and p_[0] == 0:
                    continue                      # 0건 점은 범례 설명으로 대체 (겹침 방지)
                ax.annotate(f"{float(e):g}", (x, y), textcoords="offset points", xytext=off, fontsize=7.5, color=col)
        ax.set_yscale("symlog", linthresh=0.01)
        ax.set_yticks([0, 0.01, 0.1, 1]); ax.set_yticklabels(["0", "0.01", "0.1", "1"])
        ax.set_ylim(-0.002, 3)
        ax.set_xlabel(f"공칭 대비 에너지 증가 [%] ({F['n_scen']} 시나리오 평균)")
        ax.set_ylabel("실측 충돌률 [%] (Wilson 95% CI)")
        ax.set_title(title, fontsize=10.5, loc="left")
        ax.grid(alpha=0.25)
    axs[0].legend(frameon=False, loc="upper right", title="숫자 = 위험 한도 ε", title_fontsize=8.5, fontsize=9)
    axs[1].text(0.02, 0.03, f"공칭(불확실성 무시): {M['N']['t3'][0] * 100:.1f}%", transform=axs[1].transAxes, fontsize=9,
                color="#555")
    axs[0].text(0.36, 0.56, f"공칭(불확실성 무시): {M['N']['gauss'][0] * 100:.1f}%\n"
                f"0 = {M['N']['n']:,}회 중 충돌 0건 (95% 상한 {M['B0.05']['gauss'][2] * 100:.3f}%)",
                transform=axs[0].transAxes, fontsize=8.5, color="#555")
    fig.suptitle("위험–에너지 곡선: 오차 분포가 가정과 다를 때 (모델 불일치)", fontweight="bold", y=1.02)
    fig.savefig(C.FIG / "F19_frontier_mismatch.png")
    plt.close(fig)


def main():
    E10 = L("E10")
    out = dict(params=E10["params"])
    keys = ["N", "B", "R0.2", "R0.1", "R0.05", "R0.01"]
    out["open_plan"] = open_loop(E10, keys, "plan")
    out["open_truth"] = open_loop(E10, keys, "truth")
    out["overhead_plan"] = {k: boot_ci(energy_overhead(E10, k)) for k in keys[1:]}
    out["overhead_truth"] = {k: boot_ci(energy_overhead(E10, k, wind="truth")) for k in keys[1:]}
    out["R005_vs_B"] = paired(E10, "R0.05", "B")
    out["R001_vs_B"] = paired(E10, "R0.01", "B")
    out["cpu"] = {k: boot_ci([r[k]["cpu"] for r in E10["rows"] if r.get(k)]) for k in keys}
    sens = {}
    for sv in (0.25, 0.5, 1.0):
        ks = {"N": "N", "B": "B", "R": "R0.05"} if sv == 0.5 else {"N": f"N_sv{sv}", "B": f"B_sv{sv}", "R": f"R0.05_sv{sv}"}
        ol = open_loop(E10, list(ks.values()), "plan")
        sens[str(sv)] = {m: dict(**ol[k], overhead=boot_ci(energy_overhead(E10, k, ref=ks["N"]))) for m, k in ks.items()}
        sens[str(sv)]["R_vs_B"] = paired(E10, ks["R"], ks["B"])
    out["sensitivity_sigma_v"] = sens
    try:
        E10b = L("E10b")
    except FileNotFoundError:
        E10b = L("E10b_partial")
    cl = {}
    for key in ("N+O", "R0.05+O"):
        n_hit = sum(r[key]["n_hit"] for r in E10b["closed"]); n = sum(r[key]["K"] for r in E10b["closed"])
        p, lo, hi = wilson(n_hit, n)
        cl[key] = dict(p_coll=p, p_lo=lo, p_hi=hi, n_hit=n_hit, n=n,
                       E_mean_kJ=boot_ci([e / 1000 for r in E10b["closed"] for e in r[key]["E_list"]]),
                       replans=float(np.mean([r[key]["replans"] for r in E10b["closed"]])),
                       cpu_per_flight=float(np.mean([r[key]["cpu"] for r in E10b["closed"]])))
    d = []
    for r in E10b["closed"]:
        a, b = r["R0.05+O"]["E_list"], r["N+O"]["E_list"]
        if a and b:
            d.append((np.mean(a) / np.mean(b) - 1) * 100)
    cl["R_vs_N_energy_pct"] = boot_ci(d)
    cl["n_scen"] = len(E10b["closed"])
    cl["params"] = E10b.get("params", {})
    out["closed"] = cl
    out["heavy_tail_t3"] = open_loop(E10, keys, "t3")
    out["delay"] = {k: dict(ground_delay=boot_ci([r[k].get("ground_delay", 0) for r in E10["rows"] if r[k].get("feasible")]),
                            wait=boot_ci([r[k].get("wait_total", 0) for r in E10["rows"] if r[k].get("feasible")]),
                            T=boot_ci([r[k]["plan"]["T"] for r in E10["rows"] if r[k].get("feasible")]))
                    for k in keys}
    out["frontier"] = frontier(E10)
    (C.RES / "robust_stats.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({k: out[k] for k in ("open_plan", "overhead_plan", "R005_vs_B", "closed")}, indent=1,
                     ensure_ascii=False, default=float)[:4000])
    figures(E10, E10b, out)
    if out["frontier"]:
        fig_frontier(out["frontier"])


def figures(E10, E10b, st):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        try:
            fm.fontManager.addfont(f)
        except Exception:
            pass
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 12,
                         "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200,
                         "savefig.bbox": "tight", "axes.titleweight": "bold"})
    COL = {"N": "#9E9E9E", "B": "#E07B39", "R0.2": "#8FB8DE", "R0.1": "#4F8FC0", "R0.05": "#1B6CA8", "R0.01": "#0B3954"}
    LAB = {"N": "공칭 (불확실성 무시)", "B": "고정 버퍼 (ε=0.05)", "R0.2": "튜브 ε=0.2", "R0.1": "튜브 ε=0.1",
           "R0.05": "튜브 ε=0.05", "R0.01": "튜브 ε=0.01"}
    fig, ax = plt.subplots(1, 3, figsize=(17, 5.2), gridspec_kw=dict(width_ratios=[1.15, 1, 1]))
    # (a) 위험-에너지 평면
    a = ax[0]
    for k in ["N", "B", "R0.2", "R0.1", "R0.05", "R0.01"]:
        p = st["open_plan"][k]
        ov = st["overhead_plan"].get(k, (0.0, 0.0, 0.0))
        x, xl, xh = ov
        y, yl, yh = p["p_coll"] * 100, p["p_lo"] * 100, p["p_hi"] * 100
        a.errorbar(x, y, xerr=[[max(x - xl, 0)], [max(xh - x, 0)]], yerr=[[max(y - yl, 0)], [max(yh - y, 0)]],
                   fmt="o", ms=10, color=COL[k],
                   capsize=4, label=LAB[k])
    a.set_xlabel("공칭 대비 에너지 증가 [%] (평균, 95% 부트스트랩 CI)")
    a.set_ylabel("충돌 확률 [%] (Wilson 95% CI)")
    a.set_yscale("symlog", linthresh=0.1)
    a.set_yticks([0, 0.1, 1, 10]); a.set_yticklabels(["0", "0.1", "1", "10"])
    a.set_title("(a) 위험–에너지 절충 (개루프, 30 시나리오 × 1000 샘플)", fontsize=12)
    a.legend(fontsize=9, frameon=False)
    # (b) 시나리오별 짝 비교: 튜브 vs 고정 버퍼
    b = ax[1]
    xs, ys = [], []
    for r in E10["rows"]:
        if r["B"].get("feasible") and r["R0.05"].get("feasible") and r["N"].get("feasible"):
            xs.append((r["B"]["plan"]["E"] / r["N"]["plan"]["E"] - 1) * 100)
            ys.append((r["R0.05"]["plan"]["E"] / r["N"]["plan"]["E"] - 1) * 100)
    lim = [min(xs + ys + [0]) - 1, max(xs + ys) + 2]
    b.plot(lim, lim, color="#333", lw=1)
    b.scatter(xs, ys, s=40, color="#1B6CA8")
    b.set_xlim(lim); b.set_ylim(lim)
    q = st["R005_vs_B"]
    b.set_xlabel("고정 버퍼 에너지 증가 [%]"); b.set_ylabel("튜브(ε=0.05) 에너지 증가 [%]")
    b.set_title(f"(b) 시나리오별 짝 비교 (대각선 아래 = 튜브 유리)\n튜브 우세 {q['wins']} / 열세 {q['losses']}, "
                f"Wilcoxon p={q['wilcoxon_p']:.3g}", fontsize=11)
    # (c) 폐루프
    c = ax[2]
    cl = st["closed"]
    keys = [("N+O", "#9E9E9E", "공칭 + 재관측 재계획"), ("R0.05+O", "#1B6CA8", "튜브 ε=0.05 + 재관측 재계획")]
    for i, (k, col, lab) in enumerate(keys):
        p = cl[k]
        c.bar(i, p["p_coll"] * 100, color=col, width=0.55)
        c.errorbar(i, p["p_coll"] * 100, yerr=[[max(p["p_coll"] - p["p_lo"], 0) * 100], [max(p["p_hi"] - p["p_coll"], 0) * 100]],
                   color="#222", capsize=5)
        c.text(i, p["p_hi"] * 100 + 0.5, f"{p['n_hit']}/{p['n']}\n재계획 {p['replans']:.1f}회/비행\n"
               f"E {p['E_mean_kJ'][0]:.1f} kJ", ha="center", fontsize=9.5)
    c.set_xticks([0, 1]); c.set_xticklabels([k[2] for k in keys], fontsize=10)
    c.set_ylabel("충돌 확률 [%] (Wilson 95% CI)")
    c.set_ylim(0, max(cl["N+O"]["p_hi"], cl["R0.05+O"]["p_hi"]) * 100 * 1.6 + 2)
    c.set_title(f"(c) 폐루프: {cl['params'].get('obs_period', 20):.0f} s 재관측 (시나리오 {cl['n_scen']}개 × "
                f"{cl['params'].get('K_closed', '')} 샘플)", fontsize=11)
    fig.tight_layout()
    fig.savefig(C.FIG / "F16_robust_mc.png")
    plt.close(fig)

    # 민감도
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    svs = [0.25, 0.5, 1.0]
    for m, col, lab in (("N", "#9E9E9E", "공칭"), ("B", "#E07B39", "고정 버퍼"), ("R", "#1B6CA8", "튜브 ε=0.05")):
        pc = [st["sensitivity_sigma_v"][str(s)][m]["p_coll"] * 100 for s in svs]
        ov = [st["sensitivity_sigma_v"][str(s)][m]["overhead"][0] if m != "N" else 0 for s in svs]
        ax[0].plot(svs, pc, "o-", color=col, lw=2, label=lab)
        ax[1].plot(svs, ov, "o-", color=col, lw=2, label=lab)
    ax[0].set_xlabel("불확실성 증가율 σ_v [m/s]"); ax[0].set_ylabel("충돌 확률 [%]")
    ax[0].set_yscale("symlog", linthresh=0.1); ax[0].legend(frameon=False)
    ax[0].set_yticks([0, 0.1, 1, 10]); ax[0].set_yticklabels(["0", "0.1", "1", "10"])
    ax[0].set_title("(a) 충돌 확률", fontsize=12)
    ax[1].set_xlabel("불확실성 증가율 σ_v [m/s]"); ax[1].set_ylabel("공칭 대비 에너지 증가 [%]")
    ax[1].set_title("(b) 에너지 비용", fontsize=12); ax[1].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.FIG / "F17_sensitivity_sigma.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
