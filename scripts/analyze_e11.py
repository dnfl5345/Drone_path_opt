"""E11 (실측 ADS-B 침입기 폐루프) 통계 + 그림 F21 → results/e11_stats.json

사용: PYTHONPATH=src python scripts/analyze_e11.py
- NMAC 확률 (Wilson 95% CI), 같은 조우끼리 짝지은 McNemar 정확 검정
- 최소 이격 분포, 에너지(같은 조우의 N 대비 %, 부트스트랩 CI), 재계획·완화(fallback)·회피 실패 비율
"""
from __future__ import annotations

import json
import os
import pickle
import sys

import numpy as np
from scipy import stats

sys.path.insert(0, "scripts")
from dpo import config as C  # noqa: E402
from dpo.robust_eval import wilson  # noqa: E402

KEYS = ["N", "G0.05", "B0.05", "Q0.05", "G0.01", "B0.01", "Q0.01"]
LAB = {"N": "공칭 (팽창 없음)", "G0.05": "가우시안 튜브 ε=0.05", "B0.05": "고정 버퍼 ε=0.05", "Q0.05": "데이터 튜브 ε=0.05",
       "G0.01": "가우시안 튜브 ε=0.01", "B0.01": "고정 버퍼 ε=0.01", "Q0.01": "데이터 튜브 ε=0.01"}
COL = {"N": "#9E9E9E", "G": "#1B6CA8", "B": "#E07B39", "Q": "#2CA58D"}
rng = np.random.default_rng(7)


def boot(x, n=10000):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if not len(x):
        return [np.nan] * 3
    b = [np.mean(rng.choice(x, len(x))) for _ in range(n)]
    return [float(np.mean(x)), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))]


def mcnemar(a, b):
    """짝지은 이진 결과 (a, b) – 불일치 쌍에 대한 정확 이항 검정."""
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    n01, n10 = int(np.sum(~a & b)), int(np.sum(a & ~b))
    p = float(stats.binomtest(n01, n01 + n10, 0.5).pvalue) if n01 + n10 else 1.0
    return dict(only_first=n10, only_second=n01, p=p)


def main(name="E11"):
    with open(C.RES / f"{name}.pkl", "rb") as f:
        D = pickle.load(f)
    rows = [r for r in D["rows"] if all(r.get(k, {}).get("feasible") for k in KEYS)]
    out = dict(params={k: v for k, v in D.get("params", {}).items() if k != "METHODS"}, n_jobs=len(D["rows"]),
               n_used=len(rows), V0=boot([r["V0"] for r in rows]))
    M = {}
    for k in KEYS:
        nm = np.array([r[k]["nmac"] for r in rows]); ms = np.array([r[k]["min_sep"] for r in rows])
        E = np.array([r[k]["E"] for r in rows]); EN = np.array([r["N"]["E"] for r in rows])
        p, lo, hi = wilson(int(nm.sum()), len(nm))
        M[k] = dict(nmac=int(nm.sum()), n=len(nm), p=p, lo=lo, hi=hi, min_sep_median=float(np.median(ms)),
                    p_sep_lt_300=float(np.mean(ms < 300)), energy_vs_N=boot((E / EN - 1) * 100),
                    E_kJ=boot(E / 1000), replans=float(np.mean([r[k]["replans"] for r in rows])),
                    degraded_frac=float(np.mean([r[k].get("degraded", 0) > 0 for r in rows])),
                    stuck_frac=float(np.mean([r[k].get("stuck", 0) > 0 for r in rows])))
    out["methods"] = M
    nm = {k: np.array([r[k]["nmac"] for r in rows]) for k in KEYS}
    out["paired"] = {f"{a}_vs_{b}": mcnemar(nm[a], nm[b]) for a, b in
                     [("N", "G0.05"), ("N", "Q0.05"), ("N", "B0.05"), ("G0.05", "Q0.05"), ("B0.05", "Q0.05"),
                      ("G0.01", "Q0.01"), ("B0.01", "Q0.01"), ("N", "Q0.01")]}
    E = {k: np.array([r[k]["E"] for r in rows]) for k in KEYS}
    out["energy_paired"] = {f"{a}_vs_{b}": dict(diff_pct=boot((E[a] / E[b] - 1) * 100),
                                                p=float(stats.wilcoxon(E[a], E[b]).pvalue) if np.any(E[a] != E[b]) else 1.0)
                            for a, b in [("Q0.05", "B0.05"), ("Q0.05", "G0.05"), ("Q0.01", "B0.01"), ("Q0.01", "G0.01")]}
    (C.RES / "e11_stats.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for k in KEYS:
        m = M[k]
        print(f"{k:6s} NMAC {m['nmac']:3d}/{m['n']} = {m['p'] * 100:5.1f}% [{m['lo'] * 100:.1f},{m['hi'] * 100:.1f}] "
              f"minsep med {m['min_sep_median']:5.0f} P(<300) {m['p_sep_lt_300']:.2f} E vs N {m['energy_vs_N'][0]:+5.1f}% "
              f"replans {m['replans']:.1f} degraded {m['degraded_frac']:.2f} stuck {m['stuck_frac']:.2f}")
    print(json.dumps(out["paired"], indent=0)); print(json.dumps(out["energy_paired"], indent=0))
    figure(out, rows)


def figure(out, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "mathtext.fontset": "dejavusans", "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 200, "savefig.bbox": "tight", "axes.titleweight": "bold"})
    M = out["methods"]
    fig, axs = plt.subplots(1, 3, figsize=(15.5, 4.5))
    ax = axs[0]
    xs = np.arange(len(KEYS))
    for i, k in enumerate(KEYS):
        m = M[k]
        ax.bar(i, m["p"] * 100, color=COL[k[0]], alpha=0.9 if "0.01" not in k else 0.6,
               yerr=[[max(m["p"] - m["lo"], 0) * 100], [max(m["hi"] - m["p"], 0) * 100]], capsize=3)
        ax.text(i, m["hi"] * 100 + 0.8, f"{m['nmac']}/{m['n']}", ha="center", fontsize=7.5)
    ax.set_xticks(xs); ax.set_xticklabels([LAB[k].replace(" ε", "\nε") for k in KEYS], fontsize=7.5)
    ax.set_ylabel("NMAC 확률 [%] (Wilson 95% CI)")
    ax.set_title("(a) 근접 공중 충돌 (수평 152 m 이내)", loc="left", fontsize=10.5)
    ax.grid(alpha=0.25, axis="y")
    ax = axs[1]
    for k in KEYS:
        m = M[k]
        ax.errorbar(m["energy_vs_N"][0], m["p"] * 100, xerr=[[m["energy_vs_N"][0] - m["energy_vs_N"][1]],
                                                              [m["energy_vs_N"][2] - m["energy_vs_N"][0]]],
                    yerr=[[max(m["p"] - m["lo"], 0) * 100], [max(m["hi"] - m["p"], 0) * 100]], fmt="o" if "0.01" not in k
                    else "s", color=COL[k[0]], ms=7, capsize=2, label=LAB[k])
    ax.set_xlabel("같은 조우의 공칭 대비 에너지 [%]"); ax.set_ylabel("NMAC 확률 [%]")
    ax.set_title("(b) 위험–에너지 (왼쪽 아래가 좋음)", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=7.5); ax.grid(alpha=0.25)
    ax = axs[2]
    for k in ("N", "G0.05", "B0.05", "Q0.05"):
        ms = np.sort([r[k]["min_sep"] for r in rows])
        ax.plot(ms, np.arange(1, len(ms) + 1) / len(ms) * 100, color=COL[k[0]], lw=2, label=LAB[k])
    ax.axvline(152.4, color="#D7263D", lw=1, ls="--"); ax.text(160, 3, "NMAC 152 m", color="#D7263D", fontsize=8)
    ax.set_xlim(0, 1500)
    ax.set_xlabel("실제 궤적과의 최소 이격 [m]"); ax.set_ylabel("누적 비율 [%]")
    ax.set_title("(c) 최소 이격 분포", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8, loc="lower right"); ax.grid(alpha=0.25)
    p = out["params"]
    fig.suptitle(f"실측 ADS-B 침입기 폐루프 시험 – 조우 {out['n_used']}회, 감시 {p.get('PERIOD', 5):g} s 주기, "
                 f"튜브 지평 {p.get('H', 20):g} s (교차 적합)", fontweight="bold", y=1.03)
    fig.savefig(C.FIG / "F21_adsb_closed_loop.png")
    plt.close(fig)


if __name__ == "__main__":
    main(*sys.argv[1:])
