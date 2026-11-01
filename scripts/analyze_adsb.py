"""실측 ADS-B 항적의 등속 예측 오차 → 꼬리 추정, 팽창 모양 적합, 교차 검증 (results/adsb_error_model.json, F20).

사용: PYTHONPATH=src python scripts/analyze_adsb.py
- 자료: 1 Hz 저고도 비항공사 항적(응급 헬기·일반 항공), 관측 간격 2 s, 예측 시간 τ = 0–180 s
- 속도 정규화: 오차 반경 ‖e(τ)‖ 를 침입기 속도 V 로 나눈 값(단위 s)으로 적합하고, 사용 시 V 를 곱한다
  (선회에 의한 위치 오차는 속도에 비례 – 속도 구간별 적합보다 시험셋 보정이 좋았음)
- 모델 (학습셋에서 적합):
    G(ε) : 가우시안 튜브   r(τ) = V·κ_ε·(s0 + s1 τ)          (축별 RMS/V 에 선형 최소제곱)
    B(ε) : 가우시안 고정 버퍼 r = V·κ_ε·(s0 + s1 T_h)
    Q(ε) : 데이터 분위수 튜브 r(τ) = V·q_{1−ε}(τ)             (구간별 선형 꺾임점, 단조 증가로 보정)
    QB(ε): 데이터 고정 버퍼  r = V·q_{1−ε}(T_h)
- 검증: 10분 블록(기체·시간) 단위 2분할을 시드 20개로 반복 → 시험셋 시점별 초과확률 P(‖e(τ)‖ > r(τ))
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys

import numpy as np
from scipy import stats

sys.path.insert(0, "scripts")
from dpo import config as C  # noqa: E402
from dpo.adsb_errors import load_tracks, prediction_errors, split_tracks, TAU  # noqa: E402
from dpo.obstacles import risk_kappa  # noqa: E402

EPS = [0.2, 0.1, 0.05, 0.01]
T_H = 60.0                                   # 팽창 모양을 적합·검증하는 예측 지평 [s]
KNOTS = [0.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0]
SEEDS = list(range(11, 31))
METHODS = ("G", "B", "Q", "QB")


def get_errors(rate="1hz"):
    path = C.RES / f"adsb_errors_{rate}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    err = prediction_errors(load_tracks(rate=rate), stride=2.0)
    with open(path, "wb") as f:
        pickle.dump(err, f)
    return err


def radius(E):
    return np.hypot(E[..., 0], E[..., 1])


def fit(E, V):
    """속도 정규화 오차로 가우시안(s0, s1) 과 분위수 꺾임점 적합."""
    En = E / V[:, None, None]
    jj = TAU <= T_H + 1e-9
    rms = np.sqrt(np.nanmean(En[:, jj, 0] ** 2 + En[:, jj, 1] ** 2, axis=0) / 2)
    A = np.c_[np.ones(jj.sum()), TAU[jj]]
    s0, s1 = np.linalg.lstsq(A, rms, rcond=None)[0]
    if s0 < 0:
        s0, s1 = 0.0, float(np.sum(TAU[jj] * rms) / np.sum(TAU[jj] ** 2))
    rn = radius(En)
    q = {}
    for eps in EPS:
        qq = np.array([np.nanquantile(rn[:, list(TAU).index(k)], 1 - eps) if k > 0 else 0.0 for k in KNOTS])
        q[str(eps)] = np.maximum.accumulate(qq).tolist()
    return dict(s0=float(s0), s1=float(s1), rms=rms.tolist(), q=q, n=int(len(E)))


def knots(model, kind, eps, V):
    """방법별 팽창 반경 꺾임점 [[τ, r], …] (침입기 속도 V 반영) – DynObstacle.tube 에 그대로 사용."""
    k = risk_kappa(eps)
    if kind == "G":
        return [[0.0, V * k * model["s0"]], [T_H, V * k * (model["s0"] + model["s1"] * T_H)]]
    if kind == "B":
        return [[0.0, V * k * (model["s0"] + model["s1"] * T_H)]]
    rk = model["q"][str(eps)]
    if kind == "Q":
        return [[t, V * r] for t, r in zip(KNOTS, rk)]
    if kind == "QB":
        return [[0.0, V * rk[-1]]]
    raise ValueError(kind)


def eval_profile(kn, tau):
    k = np.asarray(kn, float)
    if len(k) == 1:
        return np.full(np.shape(tau), k[0, 1])
    sl = (k[-1, 1] - k[-2, 1]) / (k[-1, 0] - k[-2, 0])
    tau = np.asarray(tau, float)
    return np.where(tau <= k[-1, 0], np.interp(tau, k[:, 0], k[:, 1]), k[-1, 1] + sl * (tau - k[-1, 0]))


def validate(err, tr, te, model):
    E, V = err["E"], err["V"]
    rn = radius(E[te]) / V[te][:, None]
    tv = TAU[(TAU >= 5) & (TAU <= T_H)]
    out = {}
    for kind in METHODS:
        for eps in EPS:
            prof = eval_profile(knots(model, kind, eps, 1.0), tv)
            ex = [float(np.nanmean(rn[:, list(TAU).index(t)] > p)) for t, p in zip(tv, prof)]
            out[f"{kind}{eps}"] = dict(exceed=ex, mean=float(np.mean(ex)), max=float(np.max(ex)),
                                       radius_per_V=float(np.mean(prof)))
    return out


def main():
    err = get_errors("1hz")
    E, V = err["E"], err["V"]
    r = radius(E)
    out = dict(source="traffic 2.13 samples (OpenSky Network ADS-B, MIT); non-airline, alt<=6000 ft, 10-75 m/s, 1 Hz",
               n_obs=int(len(E)), n_aircraft=int(len(set(err["src"]))), n_blocks=int(len(set(err["block"]))),
               categories={c: int(np.sum(err["cat"] == c)) for c in np.unique(err["cat"])},
               aircraft=sorted(set(err["src"])), V_quantiles=np.percentile(V, [10, 50, 90]).tolist(),
               tau=TAU.tolist(), knots=KNOTS, T_h=T_H, eps=EPS, seeds=SEEDS)
    # ---------------- 꼬리 (전체 자료, τ = 10–60 s, 속도 정규화 후 시점별 RMS 로 표준화)
    jj = (TAU >= 10) & (TAU <= T_H)
    En = E / V[:, None, None]
    rms = np.sqrt(np.nanmean(En[:, jj, 0] ** 2 + En[:, jj, 1] ** 2, axis=0) / 2)
    U = (radius(En)[:, jj] / rms).ravel(); U = U[np.isfinite(U)]
    ray = lambda p: math.sqrt(-2 * math.log(1 - p))
    us = np.sort(U)[::-1]; kq = int(0.05 * len(us))
    z = (En[:, jj, :] / rms[None, :, None]).reshape(-1); z = z[np.isfinite(z)]
    df, _, _ = stats.t.fit(z, floc=0.0)
    out["tail"] = dict(quantiles={str(p): dict(data=float(np.quantile(U, p)), gaussian=ray(p))
                                  for p in (0.5, 0.9, 0.95, 0.99, 0.999)},
                       hill_alpha_top5pct=float(1.0 / np.mean(np.log(us[:kq] / us[kq]))),
                       student_t_df_axis=float(df), excess_kurtosis_axis=float(stats.kurtosis(z)), n=int(len(U)))
    # 성장 모양 q(τ) ∝ τ^β (5–60 s)
    m = (TAU >= 5) & (TAU <= T_H)
    out["growth"] = {}
    for p in (0.5, 0.95, 0.99):
        qs = np.array([np.nanquantile(r[:, j] / V, p) for j in range(len(TAU))])
        out["growth"][str(p)] = dict(beta=float(np.polyfit(np.log(TAU[m]), np.log(qs[m]), 1)[0]), q_per_V=qs.tolist())
    # ---------------- 반복 교차 검증
    vals = []
    for sd in SEEDS:
        tr, te = split_tracks(err, seed=sd)
        vals.append(validate(err, tr, te, fit(E[tr], V[tr])))
    cv = {}
    for key in vals[0]:
        means = np.array([v[key]["mean"] for v in vals]); maxs = np.array([v[key]["max"] for v in vals])
        curves = np.array([v[key]["exceed"] for v in vals])
        cv[key] = dict(mean=float(means.mean()), mean_p10=float(np.percentile(means, 10)),
                       mean_p90=float(np.percentile(means, 90)), max_mean=float(maxs.mean()),
                       curve=curves.mean(0).tolist(), curve_p10=np.percentile(curves, 10, 0).tolist(),
                       curve_p90=np.percentile(curves, 90, 0).tolist(),
                       radius_per_V=float(np.mean([v[key]["radius_per_V"] for v in vals])))
    out["cv"] = cv
    out["tau_val"] = TAU[(TAU >= 5) & (TAU <= T_H)].tolist()
    out["model_all"] = fit(E, V)                      # 전체 자료 적합 (플랫폼·배포용)
    (C.RES / "adsb_error_model.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("n_obs", "n_aircraft", "n_blocks", "categories", "tail")}, indent=1,
                     ensure_ascii=False))
    print("growth beta", {p: round(v["beta"], 2) for p, v in out["growth"].items()})
    for eps in EPS:
        print("eps", eps, " | ".join(f"{k}: {cv[f'{k}{eps}']['mean']:.3f} [{cv[f'{k}{eps}']['mean_p10']:.3f},"
                                     f"{cv[f'{k}{eps}']['mean_p90']:.3f}] max {cv[f'{k}{eps}']['max_mean']:.3f} "
                                     f"r/V {cv[f'{k}{eps}']['radius_per_V']:.1f}s" for k in METHODS))
    figure(out, err)


def figure(out, err):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "mathtext.fontset": "dejavusans",
                         "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200,
                         "savefig.bbox": "tight", "axes.titleweight": "bold"})
    E, V = err["E"], err["V"]
    rn = radius(E) / V[:, None]
    mod = out["model_all"]
    fig, axs = plt.subplots(1, 3, figsize=(15.5, 4.5))
    # (a) 정규화 오차 분위수 vs τ
    ax = axs[0]
    tt = TAU[TAU <= 90]
    for p, col in ((0.5, "#9E9E9E"), (0.95, "#1B6CA8"), (0.99, "#0B3954")):
        ax.plot(tt, [np.nanquantile(rn[:, list(TAU).index(t)], p) if t > 0 else 0 for t in tt], color=col, lw=2.2,
                label=f"실측 {100 * p:g}% 분위수")
    for eps, col in ((0.05, "#1B6CA8"), (0.01, "#0B3954")):
        ax.plot(tt, eval_profile(knots(mod, "G", eps, 1.0), tt), color=col, lw=1.4, ls="--",
                label=f"가우시안 튜브 κσ(τ), ε={eps:g}")
    ax.plot(tt, eval_profile(knots(mod, "Q", 0.05, 1.0), tt), color="#2CA58D", lw=1.6, ls="-.", marker="o", ms=3,
            label="데이터 분위수 튜브 (꺾임점), ε=0.05")
    ax.axvline(T_H, color="#bbb", lw=0.8)
    ax.set_xlabel("예측 시간 τ [s]"); ax.set_ylabel("오차 반경 / 침입기 속도 [s]")
    ax.set_title(f"(a) 오차 성장: 분위수 ∝ τ^{out['growth']['0.95']['beta']:.1f} (선형 아님)", loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8); ax.grid(alpha=0.25)
    # (b) 꼬리
    ax = axs[1]
    jj = (TAU >= 10) & (TAU <= T_H)
    En = E / V[:, None, None]
    rms = np.sqrt(np.nanmean(En[:, jj, 0] ** 2 + En[:, jj, 1] ** 2, axis=0) / 2)
    U = (radius(En)[:, jj] / rms).ravel(); U = np.sort(U[np.isfinite(U)])
    ax.loglog(U, 1 - np.arange(len(U)) / len(U), color="#E07B39", lw=2.2, label="실측 (τ = 10–60 s)")
    uu = np.linspace(0.05, 12, 400)
    ax.loglog(uu, np.exp(-uu ** 2 / 2), color="#1B6CA8", lw=1.5, ls="--", label="가우시안 (레일리)")
    df = out["tail"]["student_t_df_axis"]
    tt_ = stats.t.rvs(max(df, 2.05), size=(400000, 2), random_state=np.random.default_rng(0))
    tt_ = tt_ / np.sqrt(np.mean(tt_ ** 2)); ut = np.sort(np.hypot(tt_[:, 0], tt_[:, 1]))
    ax.set_ylim(1e-4, 1.3); ax.set_xlim(0.05, 12)
    ax.set_yticks([1, 1e-1, 1e-2, 1e-3, 1e-4]); ax.set_yticklabels(["1", "0.1", "0.01", "0.001", "0.0001"])
    ax.set_xticks([0.1, 1, 10]); ax.set_xticklabels(["0.1", "1", "10"])
    ax.minorticks_off()
    ax.set_xlabel("표준화 오차 반경 ‖e‖ / σ_RMS(τ)"); ax.set_ylabel("초과 확률")
    q = out["tail"]["quantiles"]
    ax.set_title(f"(b) 꼬리: 99.9% 분위수 {q['0.999']['data']:.1f}σ (가우시안 {q['0.999']['gaussian']:.1f}σ)",
                 loc="left", fontsize=10.5)
    ax.legend(frameon=False, fontsize=8.5); ax.grid(alpha=0.25, which="both")
    # (c) 교차 검증 초과확률 (ε=0.05, 0.01)
    ax = axs[2]
    tv = np.array(out["tau_val"])
    for kind, col, lab in (("G", "#1B6CA8", "가우시안 튜브"), ("B", "#E07B39", "가우시안 고정 버퍼"),
                           ("Q", "#2CA58D", "데이터 분위수 튜브"), ("QB", "#8E6C8A", "데이터 고정 버퍼")):
        for eps, ls in ((0.05, "-"), (0.01, ":")):
            c = out["cv"][f"{kind}{eps}"]
            ax.plot(tv, np.array(c["curve"]) * 100, color=col, lw=2 if ls == "-" else 1.6, ls=ls,
                    label=lab if eps == 0.05 else None)
    ax.axhline(5, color="k", lw=0.8, ls="-", alpha=0.5); ax.axhline(1, color="k", lw=0.8, ls=":", alpha=0.5)
    ax.text(tv[-1], 5.25, "ε = 5% (실선)", ha="right", fontsize=8); ax.text(tv[-1], 1.15, "ε = 1% (점선)", ha="right", fontsize=8)
    ax.set_xlabel("예측 시간 τ [s]"); ax.set_ylabel("학습에 쓰지 않은 블록의 초과 확률 [%]")
    ax.set_title(f"(c) 보정 검증 (블록 2분할 × {len(out['seeds'])}회 평균)", loc="left", fontsize=10.5)
    ax.set_ylim(0, 16)
    ax.legend(frameon=False, fontsize=8, loc="upper left"); ax.grid(alpha=0.25)
    fig.suptitle(f"실측 ADS-B 항적의 등속 예측 오차 – 저고도 비항공사 기체 {out['n_aircraft']}대, 관측 {out['n_obs']:,}개 "
                 f"(OpenSky, 1 Hz)", fontweight="bold", y=1.03)
    fig.savefig(C.FIG / "F20_adsb_error_tail.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
