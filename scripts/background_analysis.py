"""연구 배경 근거 분석 → results/background.json, 그림 F22–F24

(A) 왜 SOC 를 목적 변수로 두는가 – 목적함수(거리·시간·SOC)별 계획을 같은 임무에서 비교
(B) 왜 바람(크기·방향)을 변인으로 두는가 – 전력 모델 민감도(바람·탑재중량·공기밀도), 실제 예보 바람의 시공간 변동
(C) 왜 경로를, 왜 비행 중 기체 쪽에서 결정하는가 – 오래된 바람 정보로 만든 경로의 손해, 결정 지연 실험(E12)

사용: PYTHONPATH=src python scripts/background_analysis.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, "scripts")
from run_experiments import sites, drone_calibrated, HUB, DST, PAYLOAD, KST, METHODS  # noqa: E402
from dpo import config as C  # noqa: E402
from dpo.energy import edge_energy_per_m, power_w, air_density, soc_drop  # noqa: E402
from dpo.mission import Mission, run_round_trip  # noqa: E402
from dpo.planner import Planner, simulate, vertical_energy  # noqa: E402

SPD = np.arange(3.0, 20.01, 0.25)


def e_star(dr, W, theta_deg, m, rho=1.2):
    """수평 비행 미터당 최소 에너지 [J/m]. theta = 진행 방향과 바람이 불어가는 방향의 각 (0 = 순풍, 180 = 역풍)."""
    th = math.radians(theta_deg)
    e, v = edge_energy_per_m(1.0, 0.0, 0.0, W * math.cos(th), W * math.sin(th), m, rho, dr.params(), dr.va_max,
                             dr.vz_up, dr.vz_dn, SPD)
    return float(e), float(v)


# ------------------------------------------------------------------ (A) 목적함수 비교
def objectives(fc, tr, dr):
    P = Planner(fc, dr)
    out = dict(headline={}, sweep=[])
    cfgs = {"distance": dict(mode=0, speed_policy="const", vconst=10.0),          # 최단 경로 + 정속 10 m/s
            "time": dict(mode=0, speed_policy="const", vconst=20.0),              # 최단 경로 + 낼 수 있는 최고 속도
            "soc": dict(mode=1, speed_policy="opt")}                              # 제안: SOC 최소
    for k, cfg in cfgs.items():
        rt = run_round_trip(P, Mission(HUB, DST, PAYLOAD, "delivery", 6 * 3600.0), site_truth=tr, **cfg)
        out["headline"][k] = None if rt is None else dict(L=rt["L"], T=rt["t_total"], E_kJ=rt["E"] / 1000,
                                                          soc=rt["soc_used"], feasible=bool(rt["feasible"]))
    for h in range(0, 12):
        row = dict(hour=h + KST)
        for k, cfg in cfgs.items():
            rt = run_round_trip(P, Mission(HUB, DST, PAYLOAD, "delivery", h * 3600.0), site_truth=fc, **cfg)
            row[k] = None if rt is None else dict(L=rt["L"], T=rt["t_total"], soc=rt["soc_used"],
                                                  feasible=bool(rt["feasible"]))
        out["sweep"].append(row)
        print("[A]", row["hour"], {k: (None if v is None else (round(v["L"]), round(v["T"]), round(v["soc"], 1)))
                                   for k, v in row.items() if k != "hour"}, flush=True)
    for k in cfgs:
        ok = [r[k] for r in out["sweep"] if r[k] is not None]
        out[f"{k}_feasible"] = int(sum(r["feasible"] for r in ok))
        out[f"{k}_soc_mean"] = float(np.mean([r["soc"] for r in ok]))
        out[f"{k}_T_mean"] = float(np.mean([r["T"] for r in ok]))
        out[f"{k}_L_mean"] = float(np.mean([r["L"] for r in ok]))
    return out


# ------------------------------------------------------------------ (B) 민감도·변동성
def sensitivity(fc, tr, dr):
    m0 = dr.m_empty + PAYLOAD
    base, vb = e_star(dr, 0.0, 0.0, m0)
    out = dict(base_J_per_m=base, base_speed=vb, mass_kg=m0)
    # 바람 크기 × 방향
    grid = {}
    for W in (0, 2, 4, 6, 8, 10, 12):
        grid[W] = {th: e_star(dr, W, th, m0)[0] for th in (0, 45, 90, 135, 180)}
    out["wind_grid"] = {str(W): {str(t): v for t, v in g.items()} for W, g in grid.items()}
    out["polar"] = {str(W): [e_star(dr, W, th, m0)[0] for th in range(0, 361, 10)] for W in (0, 5, 10)}
    out["polar_speed"] = {str(W): [e_star(dr, W, th, m0)[1] for th in range(0, 361, 10)] for W in (0, 5, 10)}
    # 요인별 범위 (기준 대비 %)
    rho_lo, rho_hi = air_density(450.0, 35.0), air_density(50.0, -10.0)
    fac = {
        "바람 방향 (10 m/s, 순풍↔역풍)": (e_star(dr, 10, 0, m0)[0], e_star(dr, 10, 180, m0)[0]),
        "바람 크기 (역풍 0→10 m/s)": (base, e_star(dr, 10, 180, m0)[0]),
        "바람 크기 (측풍 0→10 m/s)": (base, e_star(dr, 10, 90, m0)[0]),
        "바람 크기 (순풍 0→10 m/s)": (e_star(dr, 10, 0, m0)[0], base),
        "탑재중량 (0→0.75 kg)": (e_star(dr, 0, 0, dr.m_empty)[0], e_star(dr, 0, 0, dr.m_empty + 0.75)[0]),
        "공기밀도 (35 °C·450 m ↔ −10 °C·50 m)": tuple(sorted((e_star(dr, 0, 0, m0, rho_lo)[0],
                                                             e_star(dr, 0, 0, m0, rho_hi)[0]))),
        "속도 선택 (무풍, 최적↔10 m/s 고정)": (base, power_w(10.0, 0.0, m0, 1.2, dr.params()) / 10.0),
    }
    out["factors"] = {k: dict(lo=v[0], hi=v[1], lo_pct=(v[0] / base - 1) * 100, hi_pct=(v[1] / base - 1) * 100,
                              span_pct=(v[1] - v[0]) / base * 100) for k, v in fac.items()}
    out["rho_range"] = [rho_lo, rho_hi]
    # 왕복이면 상쇄되는가: 역풍 + 순풍 평균 vs 무풍
    out["round_trip"] = {str(W): (e_star(dr, W, 0, m0)[0] + e_star(dr, W, 180, m0)[0]) / 2 / base for W in (4, 6, 8, 10, 12)}
    # 실제 예보 바람의 변동 (비행 가능 셀)
    spd = np.hypot(fc.U, fc.V)                       # (T, nz, ny, nx)
    free = fc.free
    nT = min(13, spd.shape[0])
    # 지면 기준 고도(AGL) 구간별 통계 – 비행 가능 셀만
    bins = [(30, 50), (50, 70), (70, 90), (90, 110), (110, 130), (130, 150)]
    masks = [free & (fc.agl >= lo) & (fc.agl < hi) for lo, hi in bins]
    by_agl = np.array([[spd[t][mk].mean() for mk in masks] for t in range(nT)])
    dir_agl = np.array([[math.degrees(math.atan2(-fc.U[t][mk].mean(), -fc.V[t][mk].mean())) % 360 for mk in masks]
                        for t in range(nT)])
    std_agl = np.array([[spd[t][mk].std() for mk in masks] for t in range(nT)])
    out["wind_field"] = dict(hours=[KST + t for t in range(nT)], agl_bins=bins, speed=by_agl.tolist(),
                             direction=dir_agl.tolist(), spatial_std=std_agl.tolist(),
                             speed_min=float(by_agl.min()), speed_max=float(by_agl.max()),
                             layer_ratio_15h=float(by_agl[6].max() / by_agl[6].min()),
                             layer_15h=by_agl[6].tolist(),
                             hour_range_100m=[float(by_agl[:, 3].min()), float(by_agl[:, 3].max())],
                             dir_range=[float(dir_agl.min()), float(dir_agl.max())],
                             cell_min=float(spd[:nT][:, free].min()), cell_max=float(spd[:nT][:, free].max()))
    # 예보 갱신(00Z → 06Z)에 따른 바람 변화: 같은 시각·셀의 벡터 차이
    nt = min(fc.U.shape[0], tr.U.shape[0], 13)
    dv = np.hypot(fc.U[:nt] - tr.U[:nt], fc.V[:nt] - tr.V[:nt])
    out["forecast_update"] = dict(rms_vector_diff=float(np.sqrt((dv[:, free] ** 2).mean())),
                                  p95=float(np.percentile(dv[:, free], 95)),
                                  by_hour=[float(np.sqrt((dv[t][free] ** 2).mean())) for t in range(nt)],
                                  speed_mean_fc=float(np.hypot(fc.U[:nt], fc.V[:nt])[:, free].mean()))
    return out


# ------------------------------------------------------------------ (C) 경로·결정 시점
def stale_plans(fc, tr, dr):
    """오래된 바람 정보로 만든 경로를 나중의 바람에서 비행하면 얼마나 손해인가 (갈 때 구간, 순항 에너지)."""
    m = dr.m_empty + PAYLOAD
    P = Planner(fc, dr)
    st, gl = fc.lowest_free_k(*HUB), fc.lowest_free_k(*DST)
    plans = {}
    for h in range(0, 12):
        plans[h] = P.plan(st, gl, t_start=h * 3600.0, mass=m, mode=1)
    rows = []
    for lag in (0, 1, 2, 3, 6):
        pen = []
        for h in range(0, 12 - lag):
            fly = h + lag
            a = simulate(fc, dr, plans[h]["path"], plans[h]["wait_before"], fly * 3600.0, m)        # 오래된 계획
            b = simulate(fc, dr, plans[fly]["path"], plans[fly]["wait_before"], fly * 3600.0, m)    # 최신 계획
            if a is None or b is None:
                pen.append(np.nan)
                continue
            pen.append((a["e"][-1] / b["e"][-1] - 1) * 100)
        pen = np.array(pen)
        rows.append(dict(lag_h=lag, n=int(np.isfinite(pen).sum()), infeasible=int(np.isnan(pen).sum()),
                         mean_pct=float(np.nanmean(pen)), max_pct=float(np.nanmax(pen)),
                         min_pct=float(np.nanmin(pen))))
        print("[C] stale", rows[-1], flush=True)
    # 예보(00Z)로 계획 vs 갱신 예보(06Z)로 계획 – 둘 다 06Z 바람에서 비행
    Pt = Planner(tr, dr)
    stt, glt = tr.lowest_free_k(*HUB), tr.lowest_free_k(*DST)
    upd = []
    for h in range(6, 12):
        a = simulate(tr, dr, plans[h]["path"], plans[h]["wait_before"], h * 3600.0, m)
        pb = Pt.plan(stt, glt, t_start=h * 3600.0, mass=m, mode=1)
        b = simulate(tr, dr, pb["path"], pb["wait_before"], h * 3600.0, m)
        pred = simulate(fc, dr, plans[h]["path"], plans[h]["wait_before"], h * 3600.0, m)
        if a is None or b is None:
            continue
        upd.append(dict(hour=h + KST, old_plan_kJ=a["e"][-1] / 1000, new_plan_kJ=b["e"][-1] / 1000,
                        penalty_pct=(a["e"][-1] / b["e"][-1] - 1) * 100,
                        pred_error_pct=(a["e"][-1] / pred["e"][-1] - 1) * 100))
    return dict(stale=rows, forecast_update=upd,
                update_penalty_mean=float(np.mean([u["penalty_pct"] for u in upd])) if upd else None,
                update_pred_err_mean=float(np.mean([abs(u["pred_error_pct"]) for u in upd])) if upd else None)


def figures(B):
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
    GRAY, ORG, BLUE, NAVY, RED, GRN = "#9E9E9E", "#E07B39", "#1B6CA8", "#0B3954", "#D7263D", "#2CA58D"
    S = json.loads((C.RES / "summary.json").read_text())
    A = B["A"]
    # ---------------- F22 왜 SOC 인가
    fig, axs = plt.subplots(1, 3, figsize=(15.5, 4.4))
    ax = axs[0]
    names = [("distance", "거리 최소\n(최단 경로, 10 m/s)", GRAY), ("time", "시간 지향\n(최단 경로, 최고 속도)", ORG),
             ("soc", "SOC 최소\n(제안)", BLUE)]
    x = np.arange(3); w = 0.26
    base = A["distance_L_mean"], A["distance_T_mean"], A["distance_soc_mean"]
    for q, (k, lab, col) in enumerate(names):
        vals = [A[f"{k}_L_mean"] / base[0] * 100, A[f"{k}_T_mean"] / base[1] * 100, A[f"{k}_soc_mean"] / base[2] * 100]
        bars = ax.bar(x + (q - 1) * w, vals, w, color=col, label=lab.replace("\n", " "))
        for b_, v in zip(bars, vals):
            ax.text(b_.get_x() + b_.get_width() / 2, v + 1, f"{v:.0f}", ha="center", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(["비행 거리", "비행 시간", "SOC 소모"])
    ax.set_ylabel("거리 최소 계획 = 100"); ax.set_ylim(0, 150)
    ax.set_title("(a) 목적함수별 결과 (09–20시 12회 평균)", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8, loc="upper left"); ax.grid(alpha=0.25, axis="y")
    ax = axs[1]
    hrs = [r["hour"] for r in A["sweep"]]
    for k, lab, col in names:
        ax.plot(hrs, [r[k]["soc"] if r[k] else np.nan for r in A["sweep"]], color=col, marker="o", ms=4, lw=2,
                label=f"{lab.splitlines()[0]} – 예비 충족 {A[f'{k}_feasible']}/12")
    ax.axhline(80, color=RED, lw=1, ls="--")
    ax.text(hrs[0], 79.3, "예비 SOC 20% 한계 (이 선 위는 임무 불가)", color=RED, ha="left", va="top", fontsize=8)
    ax.set_xlabel("출발 시각 [시]"); ax.set_ylabel("왕복 SOC 소모 [%p]"); ax.set_ylim(62, 122)
    ax.set_title("(b) 임무 가능 여부는 SOC 로 판정된다", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8, loc="upper left"); ax.grid(alpha=0.25)
    ax = axs[2]
    e0 = S["E0_calibration"]
    labs = ["거리 비례", "시간 비례", "전력 모델\n(바람·중량·속도)"]
    r2 = [e0["base_dist"]["R2"], e0["base_time"]["R2"], e0["test"]["R2"]]
    mp = [e0["base_dist"]["MAPE"], e0["base_time"]["MAPE"], e0["test"]["MAPE"]]
    bars = ax.bar(labs, mp, color=[GRAY, ORG, BLUE], width=0.55)
    for b_, v, r in zip(bars, mp, r2):
        ax.text(b_.get_x() + b_.get_width() / 2, v + 0.6, f"{v:.1f}%\nR² {r:.2f}", ha="center", fontsize=8.5)
    ax.set_ylabel("실측 에너지 예측 오차 MAPE [%]"); ax.set_ylim(0, 33)
    ax.set_title(f"(c) 실측 비행 {e0['n_test']}회: 거리는 에너지를 설명 못 함", loc="left", fontsize=10.5)
    ax.grid(alpha=0.25, axis="y")
    fig.suptitle("왜 SOC 인가 – 거리·시간은 배터리 소모의 대리 지표가 되지 못한다", fontweight="bold", y=1.03)
    fig.savefig(C.FIG / "F22_why_soc.png"); plt.close(fig)

    # ---------------- F23 왜 바람(크기·방향) 인가
    Bs = B["B"]
    fig = plt.figure(figsize=(16.5, 4.8))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.15, 0.9, 1.15], wspace=0.42)
    ax = fig.add_subplot(gs[0])
    fac = Bs["factors"]
    order = ["바람 방향 (10 m/s, 순풍↔역풍)", "바람 크기 (역풍 0→10 m/s)", "바람 크기 (순풍 0→10 m/s)",
             "바람 크기 (측풍 0→10 m/s)", "속도 선택 (무풍, 최적↔10 m/s 고정)", "탑재중량 (0→0.75 kg)",
             "공기밀도 (35 °C·450 m ↔ −10 °C·50 m)"]
    ys = np.arange(len(order))[::-1]
    for y, k in zip(ys, order):
        f = fac[k]
        col = BLUE if k.startswith("바람") else GRAY
        ax.barh(y, f["hi_pct"] - f["lo_pct"], left=f["lo_pct"], color=col, height=0.62)
        ax.text(f["hi_pct"] + 3, y, f"{f['lo_pct']:+.0f} ~ {f['hi_pct']:+.0f}%", va="center", fontsize=8)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_yticks(ys); ax.set_yticklabels([k.replace(" (", "\n(") for k in order], fontsize=7.8)
    ax.set_xlim(-55, 215); ax.set_xlabel("미터당 에너지 변화 [%] (무풍·0.5 kg 기준)")
    ax.set_title("(a) 요인별 영향 범위", loc="left", fontsize=10.5); ax.grid(alpha=0.25, axis="x")
    ax = fig.add_subplot(gs[1], projection="polar")
    th = np.radians(np.arange(0, 361, 10))
    for W, col in (("0", GRAY), ("5", ORG), ("10", BLUE)):
        ax.plot(th, Bs["polar"][W], color=col, lw=2, label=f"바람 {W} m/s")
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_xticks(np.radians([0, 90, 180, 270])); ax.set_xticklabels(["순풍", "측풍", "역풍", "측풍"], fontsize=9)
    ax.set_rlabel_position(135); ax.tick_params(axis="y", labelsize=7)
    ax.set_title("(b) 진행 방향별 미터당 에너지 [J/m]", fontsize=10.5, pad=10, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3,
              columnspacing=1.0, handlelength=1.4)
    ax = fig.add_subplot(gs[2])
    wf = Bs["wind_field"]
    sp = np.array(wf["speed"]).T
    im = ax.imshow(sp, aspect="auto", origin="lower", cmap="viridis",
                   extent=[wf["hours"][0] - 0.5, wf["hours"][-1] + 0.5, 0, len(wf["agl_bins"])])
    ax.set_yticks(np.arange(len(wf["agl_bins"])) + 0.5)
    ax.set_yticklabels([f"{lo}–{hi}" for lo, hi in wf["agl_bins"]])
    ax.set_xlabel("시각 [시]"); ax.set_ylabel("지면 기준 고도 [m]")
    for (j, i), v in np.ndenumerate(sp):
        if i % 2 == 0:
            ax.text(wf["hours"][i], j + 0.5, f"{v:.1f}", ha="center", va="center", fontsize=7,
                    color="w" if v < sp.mean() else "k")
    fig.colorbar(im, ax=ax, label="평균 풍속 [m/s]", fraction=0.046)
    ax.set_title(f"(c) 실제 예보 바람: {wf['speed_min']:.1f}–{wf['speed_max']:.1f} m/s (고도·시각별)", loc="left",
                 fontsize=10.5)
    fig.suptitle("왜 바람(크기·방향)인가 – 영향이 가장 크고, 시공간으로 변하며, 경로에 따라 달라지는 유일한 요인",
                 fontweight="bold", y=1.06)
    fig.savefig(C.FIG / "F23_why_wind.png"); plt.close(fig)

    # ---------------- F24 왜 경로를, 왜 기체에서 결정하는가
    fig, axs = plt.subplots(1, 3, figsize=(15.5, 4.4))
    ax = axs[0]
    rowsD = {r["key"]: r for r in S["E6_ablation"] if r["kind"] == "delivery"}
    e1 = {r["method"]: r for r in S["E1_main"] if r["kind"] == "delivery"}
    steps = [("최단 경로\n+ 정속 10 m/s", e1["A_shortest_v10"]["soc_used"], GRAY),
             ("+ 구간 속도\n결정", e1["B_shortest_vopt"]["soc_used"], ORG),
             ("+ 바람 고려\n경로 (격자)", e1["C_energy_Astar"]["soc_used"], "#4F8FC0"),
             ("+ 에너지 비교\n지름길 (제안)", e1["D_WE_LTstar"]["soc_used"], BLUE)]
    bars = ax.bar([s_[0] for s_ in steps], [s_[1] for s_ in steps], color=[s_[2] for s_ in steps], width=0.6)
    for b_, s_ in zip(bars, steps):
        ax.text(b_.get_x() + b_.get_width() / 2, s_[1] + 1, f"{s_[1]:.1f}", ha="center", fontsize=9)
    ax.axhline(80, color=RED, lw=1, ls="--"); ax.text(3.4, 81, "예비 한계", color=RED, ha="right", fontsize=8)
    ax.set_ylim(60, 110); ax.set_ylabel("왕복 SOC 소모 [%p]"); ax.tick_params(axis="x", labelsize=8)
    ax.set_title("(a) 조절할 수 있는 것: 속도·경로·고도", loc="left", fontsize=10.5); ax.grid(alpha=0.25, axis="y")
    del rowsD
    ax = axs[1]
    f12 = C.RES / "E12_latency.json"
    if f12.exists():
        E12 = json.loads(f12.read_text())
        for mode, col, lab in (("remote", RED, "원격 판단만 (기체는 스스로 못 멈춤)"),
                               ("onboard", BLUE, "기체 탑재 판단 (즉시 비상 정지 가능)")):
            rr = [r for r in E12["rows"] if r["mode"] == mode]
            if not rr:
                continue
            xs = [r["latency"] for r in rr]; ps = [r["p"] * 100 for r in rr]
            ax.errorbar(xs, ps, yerr=[[max(r["p"] - r["lo"], 0) * 100 for r in rr],
                                      [max(r["hi"] - r["p"], 0) * 100 for r in rr]], color=col, marker="o", lw=2,
                        capsize=3, label=lab)
            for x_, p_, r in zip(xs, ps, rr):
                if r["latency"] <= 3:           # 두 방식 모두 0 – 한 번만 표기
                    if mode == "remote" and r["latency"] == 1:
                        ax.annotate("지연 1–3 s: 둘 다 0/16", (2, 0), xytext=(4, 22), fontsize=8, color="#333",
                                    arrowprops=dict(arrowstyle="-", color="#999", lw=0.8))
                    continue
                ax.annotate(f"{r['intrusions']}/{r['n']}", (x_, p_), textcoords="offset points",
                            xytext=(-16, 6) if mode == "remote" else (12, -14), fontsize=8.5, color=col)
        ax.axvline(E12["lead"], color="#999", lw=0.8, ls=":")
        ax.text(E12["lead"] - 0.5, 62, f"조우까지 {E12['lead']:.0f} s", ha="right", fontsize=8, color="#555")
        ax.set_ylim(-8, 105); ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_xlabel("결정 지연 [s] (탐지 → 새 경로 적용)"); ax.set_ylabel("침범 비율 [%] (Wilson 95% CI)")
    ax.set_title("(b) 결정이 늦으면 피할 수 없다", loc="left", fontsize=10.5); ax.grid(alpha=0.25)
    ax = axs[2]
    st = B["C"]["stale"]
    xs = [r["lag_h"] for r in st]
    ax.bar(xs, [r["max_pct"] for r in st], color="#C9D6E3", width=0.6, label="최대")
    ax.plot(xs, [r["mean_pct"] for r in st], color=NAVY, marker="o", lw=2, label="평균")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("바람 정보가 오래된 정도 [시간]"); ax.set_ylabel("최신 계획 대비 에너지 손해 [%]")
    ax.set_xticks(xs)
    ax.set_title("(c) 바람 정보가 몇 시간 오래돼도 평균 손해는 작다", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8); ax.grid(alpha=0.25, axis="y")
    fig.suptitle("왜 경로를, 왜 비행 중 기체에서 결정하는가", fontweight="bold", y=1.03)
    fig.savefig(C.FIG / "F24_why_path.png"); plt.close(fig)


def main():
    g, fc, tr = sites()
    dr = drone_calibrated()
    f = C.RES / "background.json"
    old = json.loads(f.read_text()) if f.exists() else {}
    only = set(sys.argv[1:])               # 예: "B" 만 다시 계산
    out = dict(A=objectives(fc, tr, dr) if (not only or "A" in only or "A" not in old) else old["A"],
               B=sensitivity(fc, tr, dr) if (not only or "B" in only or "B" not in old) else old["B"],
               C=stale_plans(fc, tr, dr) if (not only or "C" in only or "C" not in old) else old["C"])
    S = json.loads((C.RES / "summary.json").read_text())
    # 임무 수준: 출발 시각(바람)과 탑재중량에 따른 SOC 범위 (제안 방법, 배송)
    e2 = [r for r in S["E2_departure"] if r["method"] == "D_WE_LTstar" and r["kind"] == "delivery"]
    e3 = [r for r in S["E3_payload"] if r["method"] == "D_WE_LTstar" and r["kind"] == "delivery"]
    a2 = [r for r in S["E2_departure"] if r["method"] == "A_shortest_v10" and r["kind"] == "delivery"]
    out["mission"] = dict(soc_by_hour=[(r["hour"], r["soc_used"], r.get("ws_mean")) for r in e2],
                          soc_by_payload=[(r["payload"], r["soc_used"]) for r in e3],
                          soc_hour_range=[min(r["soc_used"] for r in e2), max(r["soc_used"] for r in e2)],
                          soc_payload_range=[min(r["soc_used"] for r in e3), max(r["soc_used"] for r in e3)],
                          base_hour_range=[min(r["soc_used"] for r in a2), max(r["soc_used"] for r in a2)],
                          ws_range=[min(r["ws_mean"] for r in e2), max(r["ws_mean"] for r in e2)])
    (C.RES / "background.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({k: out[k] for k in ("mission",)}, indent=1, ensure_ascii=False, default=float))
    print("factors", json.dumps(out["B"]["factors"], indent=1, ensure_ascii=False))
    print("round_trip", out["B"]["round_trip"])
    print("headline", out["A"]["headline"])
    print({k: v for k, v in out["A"].items() if k not in ("headline", "sweep")})
    print("wind_field", {k: v for k, v in out["B"]["wind_field"].items() if k not in ("speed", "direction", "spatial_std")})
    try:
        figures(out)
    except Exception as ex:  # noqa
        print("figure error", type(ex).__name__, ex)
    print("forecast_update", out["B"]["forecast_update"])
    print("C", json.dumps(out["C"], indent=1, default=float)[:1800])


if __name__ == "__main__":
    main()

