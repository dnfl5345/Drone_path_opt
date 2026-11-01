"""결과 시각화 (results/figures/*.png). 사용: PYTHONPATH=src python scripts/make_figures.py"""
from __future__ import annotations

import json
import pickle
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.colors import LightSource
from matplotlib.patches import Circle, Polygon
import numpy as np

sys.path.insert(0, "scripts")
from dpo import config as C  # noqa: E402
from dpo.energy import Drone, power_w, soc_drop  # noqa: E402
from run_experiments import (sites, drone_calibrated, HUB, DST, NFZ, T_MAIN, KST, PAYLOAD,  # noqa: E402
                             METHODS)

for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
    try:
        fm.fontManager.addfont(f)
    except Exception:
        pass
plt.rcParams.update({"font.family": "NanumGothic", "axes.unicode_minus": False, "font.size": 12,
                     "axes.titlesize": 14, "axes.titleweight": "bold", "axes.spines.top": False,
                     "axes.spines.right": False, "savefig.dpi": 200, "savefig.bbox": "tight"})

COL = {"A_shortest_v10": "#E07B39", "B_shortest_vopt": "#F2B134", "C_energy_Astar": "#2CA58D",
       "D_WE_LTstar": "#1B6CA8"}
LBL = {k: v["label"] for k, v in METHODS.items()}
INK = "#222222"
MUTED = "#6B6B6B"


def L(name):
    with open(C.RES / f"{name}.pkl", "rb") as f:
        return pickle.load(f)


def basemap(ax, g, site, alpha_b=1.0, nfz=True, extent_km=True):
    ls = LightSource(azdeg=315, altdeg=45)
    ny, nx = g["ground"].shape
    ext = [0, nx * C.DX / 1000, 0, ny * C.DX / 1000]
    rgb = ls.shade(g["ground"], cmap=plt.cm.gist_earth, vert_exag=3, blend_mode="soft",
                   vmin=-50, vmax=g["ground"].max() + 60)
    ax.imshow(rgb, origin="lower", extent=ext, alpha=0.85)
    bh = np.ma.masked_where(g["bldg_h"] <= 0, g["bldg_h"])
    from matplotlib.colors import LinearSegmentedColormap
    bcm = LinearSegmentedColormap.from_list("bld", ["#C9C9C9", "#2E2E2E"])
    im = ax.imshow(bh, origin="lower", extent=ext, cmap=bcm, vmin=0, vmax=90, alpha=0.9 * alpha_b)
    if nfz:
        ax.add_patch(Circle((NFZ[0] / 1000, NFZ[1] / 1000), NFZ[2] / 1000, fc="#D7263D", alpha=0.25,
                            ec="#D7263D", lw=1.5, ls="--"))
        ax.text(NFZ[0] / 1000, NFZ[1] / 1000, "비행금지\n구역(예시)", ha="center", va="center", fontsize=8,
                color="#8B0000")
    ax.plot(HUB[0] / 1000, HUB[1] / 1000, marker="s", ms=11, mfc="white", mec=INK, mew=2, zorder=10)
    ax.plot(DST[0] / 1000, DST[1] / 1000, marker="*", ms=17, mfc="#FFD23F", mec=INK, mew=1.2, zorder=10)
    ax.text(HUB[0] / 1000 + 0.07, HUB[1] / 1000 + 0.07, "물류거점(Hub)", fontsize=10, weight="bold", ha="left",
            bbox=dict(fc="white", ec="none", alpha=0.7, pad=1))
    ax.text(DST[0] / 1000 + 0.08, DST[1] / 1000 - 0.16, "배송지", fontsize=10, weight="bold",
            bbox=dict(fc="white", ec="none", alpha=0.7, pad=1))
    ax.set_xlabel("동서 [km]")
    ax.set_ylabel("남북 [km]")
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    return im


def P2(site, path):
    p = np.asarray(path, float)
    return (p[:, 2] + .5) * site.dx / 1000, (p[:, 1] + .5) * site.dx / 1000, site.z[0] + p[:, 0] * site.dz


# ================================================================== F1 사이트·바람
def fig_site(g, fc):
    fig, ax = plt.subplots(1, 2, figsize=(14, 6))
    im = basemap(ax[0], g, fc)
    cb = plt.colorbar(im, ax=ax[0], shrink=0.8); cb.set_label("건물 높이 [m]")
    ax[0].set_title("실제 지형(Copernicus DSM 30 m) + 건물 7,109동(Overture)")
    # 100 m AGL 바람 (15:00 KST)
    k_t = int(np.searchsorted(fc.t_axis, T_MAIN))
    agl = fc.agl
    kk = np.argmin(np.abs(agl - 100.0), axis=0)
    J, I = np.indices(kk.shape)
    U = fc.U[k_t][kk, J, I]; V = fc.V[k_t][kk, J, I]
    ws = np.hypot(U, V)
    ext = [0, fc.nx * fc.dx / 1000, 0, fc.ny * fc.dx / 1000]
    im2 = ax[1].imshow(ws, origin="lower", extent=ext, cmap="YlGnBu", vmin=ws.min(), vmax=ws.max())
    s = 12
    X, Y = (np.arange(fc.nx) + .5) * fc.dx / 1000, (np.arange(fc.ny) + .5) * fc.dx / 1000
    ax[1].quiver(X[::s], Y[::s], U[::s, ::s], V[::s, ::s], color=INK, scale=260, width=0.003)
    ax[1].contour(X, Y, g["ground"], levels=np.arange(50, 300, 50), colors="white", linewidths=0.6, alpha=0.8)
    cb = plt.colorbar(im2, ax=ax[1], shrink=0.8); cb.set_label("풍속 [m/s]")
    ax[1].plot(HUB[0] / 1000, HUB[1] / 1000, "s", ms=10, mfc="white", mec=INK, mew=2)
    ax[1].plot(DST[0] / 1000, DST[1] / 1000, "*", ms=16, mfc="#FFD23F", mec=INK)
    ax[1].set_title("바람 지도: 지상 100 m, 2026-10-27 15:00 KST (GFS 다운스케일)")
    ax[1].set_xlabel("동서 [km]"); ax[1].set_ylabel("남북 [km]")
    fig.tight_layout()
    fig.savefig(C.FIG / "F1_site_wind.png")
    plt.close(fig)


def fig_wind_profile(g, fc, tr):
    fig, ax = plt.subplots(1, 2, figsize=(14, 5.2))
    j, i = fc.ny // 2, fc.nx // 2
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    f6 = dict(np.load(C.RAW / "gfs_20261027_06z.npz", allow_pickle=True))
    levs = C.GFS_LEVELS_AGL
    cmap = plt.cm.viridis
    hours = [0, 3, 6, 9, 12]
    for n, h in enumerate(hours):
        ws = [np.hypot(f0[f"UGRD_agl{l}"][h], f0[f"VGRD_agl{l}"][h]).mean() for l in levs]
        z975 = (f0["HGT_p975"][h] - f0["HGT_sfc"][h]).mean()
        ws.append(np.hypot(f0["UGRD_p975"][h], f0["VGRD_p975"][h]).mean())
        ax[0].plot(ws, levs + [z975], "o-", color=cmap(n / (len(hours) - 1)), label=f"{h + KST:02d}:00 KST")
    ax[0].axhspan(30, 150, color="#1B6CA8", alpha=0.08)
    ax[0].text(0.3, 140, "드론 운용 고도대 (30~150 m)", color="#1B6CA8", fontsize=10)
    ax[0].set_xlabel("풍속 [m/s]"); ax[0].set_ylabel("지상 고도 [m]"); ax[0].set_xlim(0, None)
    ax[0].set_title("고도별 풍속 프로파일 (GFS 0.25°, 10/20/…/100 m + 975 hPa)")
    ax[0].legend(fontsize=9, frameon=False)
    t0 = f0["valid"].astype("datetime64[h]").astype(int) % 24 + KST
    t6 = f6["valid"].astype("datetime64[h]").astype(int) % 24 + KST
    for l, c in ((10, "#8FB8DE"), (50, "#1B6CA8"), (100, "#0B3954")):
        ax[1].plot(t0, [np.hypot(f0[f"UGRD_agl{l}"][h], f0[f"VGRD_agl{l}"][h]).mean() for h in range(len(t0))],
                   "-", color=c, lw=2, label=f"{l} m – 00Z 예보(계획용)")
        ax[1].plot(t6, [np.hypot(f6[f"UGRD_agl{l}"][h], f6[f"VGRD_agl{l}"][h]).mean() for h in range(len(t6))],
                   "--", color=c, lw=2, label=f"{l} m – 06Z 갱신(실제 대용)")
    ax[1].axvline(15, color="#D7263D", lw=1, ls=":")
    ax[1].text(15.1, ax[1].get_ylim()[1] - 0.4, "임무 출발 15:00", color="#D7263D", fontsize=10)
    ax[1].set_xlabel("시각 [KST]"); ax[1].set_ylabel("풍속 [m/s]")
    ax[1].set_title("시간별 풍속 변화 (예보 vs 갱신 예보)")
    ax[1].legend(fontsize=8, ncol=2, frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(C.FIG / "F2_wind_profile_time.png")
    plt.close(fig)


# ================================================================== F3 에너지 모델 검증
def fig_energy():
    E0 = L("E0")
    te, tr = E0["te"], E0["tr"]
    dr = drone_calibrated()
    res = E0["res"]
    fig, ax = plt.subplots(1, 3, figsize=(16, 5))
    ax[0].scatter(tr.total_energy_j / 3600, tr.yh / 3600, s=14, c="#B0B0B0", label=f"학습 {len(tr)}회")
    ax[0].scatter(te.total_energy_j / 3600, te.yh / 3600, s=22, c="#1B6CA8", label=f"검증 {len(te)}회 (미사용 날짜)")
    lim = [5, 42]
    ax[0].plot(lim, lim, color=INK, lw=1)
    ax[0].set_xlim(lim); ax[0].set_ylim(lim)
    ax[0].set_xlabel("실측 비행 에너지 [Wh]"); ax[0].set_ylabel("모델 예측 [Wh]")
    ax[0].set_title(f"CMU DJI M100 실측 비행 {len(tr) + len(te)}회 대비 검증")
    ax[0].text(6, 37, f"검증: R² {res['test']['R2']:.2f}, MAPE {res['test']['MAPE']:.1f}%\n"
                      f"(거리비례 모델 R² {res['base_dist']['R2']:.2f}, 시간비례 {res['base_time']['R2']:.2f})",
               fontsize=10)
    ax[0].legend(frameon=False, loc="lower right", fontsize=9)
    # payload 별 평균전력
    for n, (col, lab) in enumerate((("P", "실측"), ("Ph", "모델"))):
        d = te.assign(P=te.total_energy_j / te.duration_s, Ph=te.yh / te.duration_s).groupby("payload_g")[col].mean()
        ax[1].bar(np.arange(len(d)) + (n - 0.5) * 0.38, d.values, 0.36, color=["#B0B0B0", "#1B6CA8"][n], label=lab)
        for x, v in zip(np.arange(len(d)) + (n - 0.5) * 0.38, d.values):
            ax[1].text(x, v + 4, f"{v:.0f}", ha="center", fontsize=9)
    ax[1].set_xticks(range(len(d))); ax[1].set_xticklabels([f"{int(p)} g" for p in d.index])
    ax[1].set_ylim(300, 480); ax[1].set_ylabel("비행 평균 전력 [W]"); ax[1].set_xlabel("탑재 중량")
    ax[1].set_title("탑재중량에 따른 평균 소비전력 (검증셋)")
    ax[1].legend(frameon=False, fontsize=9)
    va = np.linspace(0, 17, 120)
    prm = dr.params()
    for pl, c in ((0, "#8FB8DE"), (0.25, "#4F8FC0"), (0.5, "#1B6CA8"), (0.75, "#0B3954")):
        p = [power_w(v, 0, dr.m_empty + pl, 1.225, prm) for v in va]
        ax[2].plot(va, p, color=c, lw=2, label=f"+{pl:.2f} kg")
    ax[2].set_xlabel("대기속도 [m/s]"); ax[2].set_ylabel("소비전력 [W]")
    ax[2].set_title("보정된 전력 곡선 P(V, m)")
    ax[2].legend(frameon=False, fontsize=9, title="탑재중량")
    ax[2].text(0.4, ax[2].get_ylim()[0] + 5, f"보정값: 효율 {dr.eta:.2f}, 형상동력 {dr.p0:.0f} W, CdA {dr.cda:.3f} m²",
               fontsize=9, color=MUTED)
    fig.tight_layout()
    fig.savefig(C.FIG / "F3_energy_validation.png")
    plt.close(fig)


# ================================================================== F4 실제 지도 경로
def fig_paths(g, fc):
    E1 = L("E1")
    st = E1["store"]
    fig = plt.figure(figsize=(16, 6.4))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.25], hspace=0.45, wspace=0.15)
    ax = fig.add_subplot(gs[:, 0])
    basemap(ax, g, fc)
    for key in ("D_WE_LTstar", "C_energy_Astar", "A_shortest_v10"):
        r = st[("delivery", key)]
        for leg, lsx in (("out", "-"), ("back", "--")):
            x, y, z = P2(fc, r[leg]["path"])
            ax.plot(x, y, lsx, color=COL[key], lw={"D_WE_LTstar": 3.2, "C_energy_Astar": 1.4}.get(key, 2.0),
                    marker="o" if key != "C_energy_Astar" else None, ms=3.5, zorder=5,
                    label=f"{LBL[key]} ({'갈 때' if leg == 'out' else '올 때'})")
    ax.set_title("실제 지도 위 경로 (배송 0.5 kg, 15:00 KST, 북서풍 ~11 m/s)", fontsize=13)
    ax.set_xlim(1.8, 5.4); ax.set_ylim(0.9, 3.8)
    ax.legend(fontsize=8, loc="lower left", frameon=True, framealpha=0.9)
    # 고도 단면
    for n, leg in enumerate(("out", "back")):
        a2 = fig.add_subplot(gs[n, 1])
        for key in ("A_shortest_v10", "D_WE_LTstar"):
            sim = st[("delivery", key)][leg]["sim"]
            d = np.r_[0, np.cumsum(np.hypot(np.diff(sim["x"]), np.diff(sim["y"])))] / 1000
            a2.plot(d, sim["z"], color=COL[key], lw=2, label=LBL[key])
            jj = np.clip((sim["y"] // fc.dx).astype(int), 0, fc.ny - 1)
            ii = np.clip((sim["x"] // fc.dx).astype(int), 0, fc.nx - 1)
            if key == "D_WE_LTstar":
                a2.fill_between(d, 0, g["ground"][jj, ii], color="#8C6D46", alpha=0.35, label="지면(경로 하부)")
                a2.fill_between(d, g["ground"][jj, ii], g["surface"][jj, ii], color="#555555", alpha=0.35,
                                label="건물")
        a2.set_ylim(0, 320)
        a2.set_ylabel("해발고도 [m]")
        a2.set_title(("갈 때 (순풍, 0.5 kg 적재)" if leg == "out" else "올 때 (역풍, 빈 기체)"), fontsize=12)
        if n == 1:
            a2.set_xlabel("비행 거리 [km]")
        a2.legend(fontsize=8, ncol=4, frameon=False, loc="upper left")
    fig.savefig(C.FIG / "F4_paths_realmap.png")
    plt.close(fig)


def fig_soc(fc):
    E1 = L("E1")
    rows = E1["rows"]
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw=dict(width_ratios=[1.15, 1]))
    keys = list(METHODS)
    for n, kind in enumerate(("delivery", "pickup")):
        vals = [next(r for r in rows if r["kind"] == kind and r["method"] == k) for k in keys]
        x = np.arange(len(keys)) + (n - 0.5) * 0.38
        for xi, v, k in zip(x, vals, keys):
            ax[0].bar(xi, v["soc_out"], 0.36, color=COL[k], alpha=1.0 if n == 0 else 0.55,
                      hatch=None if n == 0 else "//", edgecolor="white")
            ax[0].bar(xi, v["soc_back"], 0.36, bottom=v["soc_out"], color=COL[k], alpha=0.65 if n == 0 else 0.35,
                      hatch=None if n == 0 else "//", edgecolor="white")
            ax[0].text(xi, v["soc_used"] + 1.5, f"{v['soc_used']:.1f}", ha="center", fontsize=10, weight="bold")
    ax[0].axhline(80, color="#D7263D", ls="--", lw=1.2)
    ax[0].text(-0.45, 83, "사용 한계 80% (예비 SOC 20%)", color="#D7263D", fontsize=9, ha="left")
    ax[0].set_xticks(range(len(keys)))
    ax[0].set_xticklabels([LBL[k].replace("+", "\n+").replace("(26", "\n(26") for k in keys], fontsize=10)
    ax[0].set_ylabel("왕복 SOC 소모 [%p]")
    ax[0].set_title("방법별 왕복 SOC 소모 (좌: 배송, 우(빗금): 회수 / 아래: 갈 때, 위: 올 때)", fontsize=12)
    ax[0].set_ylim(0, 115)
    st = E1["store"]
    for key in ("A_shortest_v10", "B_shortest_vopt", "C_energy_Astar", "D_WE_LTstar"):
        r = st[("delivery", key)]
        o, b = r["out"], r["back"]
        e_to = o["e_total"] - o["sim"]["e"][-1]          # 이륙+착륙 합
        t1 = np.r_[0, o["sim"]["t"] - T_MAIN]
        e1 = np.r_[0, o["sim"]["e"]] + 0.5 * e_to
        t2 = np.r_[t1[-1] + 30, b["sim"]["t"] - T_MAIN]
        e2 = np.r_[o["e_total"], b["sim"]["e"] + o["e_total"] + 0.5 * (b["e_total"] - b["sim"]["e"][-1])]
        tt = np.r_[t1, t1[-1] + 30, t2, t2[-1] + 30] / 60
        ee = np.r_[e1, o["e_total"], e2, o["e_total"] + b["e_total"]]
        dr = drone_calibrated()
        soc = 100 - soc_drop(ee, dr)
        ax[1].plot(tt, soc, color=COL[key], lw=2, label=f"{LBL[key]} (도착 SOC {soc[-1]:.0f}%)")
    ax[1].axhline(20, color="#D7263D", ls="--", lw=1.2)
    ax[1].set_xlabel("출발 후 경과 시간 [분]"); ax[1].set_ylabel("배터리 SOC [%]")
    ax[1].set_title("배송 임무 SOC 시계열 (실제 바람 기준)")
    ax[1].set_ylim(-8, 102)
    ax[1].text(0.2, 15, "예비 SOC 20%", color="#D7263D", fontsize=9)
    ax[1].legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(C.FIG / "F5_soc_methods.png")
    plt.close(fig)


# ================================================================== F6 출발시각
def fig_departure(g, fc):
    E2 = L("E2")
    import pandas as pd
    d = pd.DataFrame(E2["rows"])
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw=dict(width_ratios=[1.1, 1]))
    for kind, mk in (("delivery", "o"), ("pickup", "s")):
        for key in ("A_shortest_v10", "D_WE_LTstar"):
            s = d[(d.kind == kind) & (d.method == key)]
            ax[0].plot(s.hour, s.soc_used, marker=mk, color=COL[key], lw=2,
                       ls="-" if kind == "delivery" else "--",
                       label=f"{LBL[key]} – {'배송' if kind == 'delivery' else '회수'}")
    ax[0].axhline(80, color="#D7263D", ls="--", lw=1.2)
    ax[0].set_xlabel("출발 시각 [KST]"); ax[0].set_ylabel("왕복 SOC 소모 [%p]")
    ax[0].set_title("출발 시각(시간가변 예보)에 따른 SOC 소모")
    a2 = ax[0].twinx()
    s = d[(d.kind == "delivery") & (d.method == "D_WE_LTstar")]
    a2.bar(s.hour, s.ws_mean, color="#8FB8DE", alpha=0.25, width=0.6)
    a2.set_ylabel("운용고도 평균 풍속 [m/s]", color="#4F8FC0")
    a2.spines["top"].set_visible(False)
    ax[0].set_zorder(a2.get_zorder() + 1); ax[0].patch.set_visible(False)
    ax[0].legend(fontsize=8, frameon=False, loc="upper left")
    basemap(ax[1], g, fc)
    cm = plt.cm.plasma
    hrs = sorted({h for (h, k) in E2["paths"] if k == "delivery"})
    for n, h in enumerate(hrs):
        if h not in (9, 12, 15, 18, 20):
            continue
        po, pb = E2["paths"][(h, "delivery")]
        for p, lsx in ((po, "-"), (pb, "--")):
            x, y, _ = P2(fc, p)
            ax[1].plot(x, y, lsx, color=cm(0.1 + 0.8 * n / len(hrs)), lw=2.4, label=f"{h}:00" if lsx == "-" else None)
    ax[1].set_xlim(2.3, 5.0); ax[1].set_ylim(1.2, 3.4)
    ax[1].legend(title="출발시각 (실선 갈 때/점선 올 때)", fontsize=8, title_fontsize=9, loc="lower left")
    ax[1].set_title("출발 시각별 WE-LT* 경로 변화")
    fig.tight_layout()
    fig.savefig(C.FIG / "F6_departure_time.png")
    plt.close(fig)


# ================================================================== F7 호버링 vs 우회
def fig_hover(g, fc):
    E4 = L("E4")
    import pandas as pd
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw=dict(width_ratios=[1, 1.05]))
    basemap(ax[0], g, fc)
    band = np.array(E4["band"]) / 1000
    ax[0].add_patch(Polygon(band, fc="#F2B134", alpha=0.45, ec="#B8860B", lw=1.5))
    x, y, _ = P2(fc, E4["base"]["path"])
    ax[0].plot(x, y, color="#9E9E9E", lw=1.5, ls=":", label="제한 없음")
    for name, col, lab in (("short", "#1B6CA8", "제한 30 s → 호버링 대기"), ("long", "#E07B39", "제한 300 s → 우회")):
        c = E4["cases"][name]["wait"]
        x, y, _ = P2(fc, c["path"])
        ax[0].plot(x, y, color=col, lw=2.4, label=lab)
        if c["total_wait"] > 0:
            k = int(np.argmax(c["wait"] > 0)) - 1
            ax[0].plot(x[k], y[k], "o", ms=12, mfc="none", mec=col, mew=2.5)
            ax[0].annotate(f"호버링 {c['total_wait']:.0f} s 대기", (x[k], y[k]), (x[k] + 0.45, y[k] + 0.45), fontsize=11,
                           color=col, weight="bold", arrowprops=dict(arrowstyle="->", color=col))
    ax[0].set_xlim(2.2, 5.4); ax[0].set_ylim(1.0, 3.6)
    ax[0].legend(fontsize=9, loc="lower left", framealpha=0.9)
    ax[0].set_title("시간제 비행제한(띠형, 장애물 시간표) 대응")
    s = pd.DataFrame(E4["sweep"])
    win = s.extra + E4["t_enter"]
    ax[1].plot(win, s.E_nowait / 1000, "o-", color="#E07B39", lw=2, label="우회만 허용")
    ax[1].plot(win, s.E_wait / 1000, "s-", color="#1B6CA8", lw=2, label="WE-LT* (호버링/우회 자동선택)")
    shown = set()
    for xw, e, w in zip(win, s.E_wait / 1000, s.wait):
        if w > 0 and (len(shown) == 0 or w - max(shown) >= 14):
            shown.add(w)
            ax[1].annotate(f"호버링 {w:.0f} s", (xw, e), (xw + 12, e - 1.5), fontsize=10, color="#1B6CA8",
                           arrowprops=dict(arrowstyle="-", color="#8FB8DE"))
    ax[1].axvspan(win[s.wait > 0].min() - 3, win[s.wait > 0].max() + 3, color="#1B6CA8", alpha=0.07)
    ax[1].text(win[s.wait > 0].mean(), 71.8, "호버링 유리 구간", ha="center", color="#1B6CA8", fontsize=10)
    ax[1].set_xlabel("제한 해제 시각 (출발 후, 초)"); ax[1].set_ylabel("편도 소비 에너지 [kJ]")
    ax[1].set_title("우회 vs 호버링 – 에너지 기준 손익분기")
    ax[1].legend(frameon=False, fontsize=9, loc="lower right")
    fig.tight_layout()
    fig.savefig(C.FIG / "F7_hover_vs_detour.png")
    plt.close(fig)


# ================================================================== F8 롤링 재계획
def fig_rolling(g, fc):
    E5 = L("E5")
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw=dict(width_ratios=[1, 1.05]))
    basemap(ax[0], g, fc)
    cx, cy, R, ta = E5["popup"]
    ax[0].add_patch(Circle((cx / 1000, cy / 1000), R / 1000, fc="#9B5DE5", alpha=0.3, ec="#6A2C9E", lw=1.5))
    ax[0].text(cx / 1000, cy / 1000, "돌발 금지구역\n(출발 60 s 후 발표)", ha="center", fontsize=8, color="#4B0082")
    xs = np.concatenate([tr["x"] for tr in E5["roll"]["trace"]]) / 1000
    ys = np.concatenate([tr["y"] for tr in E5["roll"]["trace"]]) / 1000
    ax[0].plot(xs, ys, color="#0B3954", lw=4, label="실제 비행 궤적 (롤링 재계획)", zorder=6)
    cm = plt.cm.Blues
    plans = E5["roll"]["plans"]
    for n, p in enumerate(plans[1:4], start=1):
        x, y, _ = P2(fc, p["path"])
        ax[0].plot(x, y, color=["#4FC3F7", "#29B6F6", "#0288D1"][n - 1], lw=1.6, zorder=7,
                   label=f"재계획 #{n} (t={p['t'] - E5['t0']:.0f} s)")
    x, y, _ = P2(fc, E5["base"]["path"])
    ax[0].plot(x, y, color="#D7263D", lw=2, ls="--", label="초기 계획 고수 (금지구역 침범)", zorder=8)
    ax[0].set_xlim(2.3, 5.0); ax[0].set_ylim(1.2, 3.4)
    ax[0].legend(fontsize=8, loc="lower left", framealpha=0.9)
    ax[0].set_title("롤링 호라이즌 재계획 (60 s 주기 + 이벤트)")
    dr = drone_calibrated()
    sb = E5["base"]["sim"]
    ax[1].plot((sb["t"] - E5["t0"]) / 60, 100 - soc_drop(sb["e"], dr), "--", lw=2,
               label=f"초기 계획 고수: SOC 소모 {E5['base']['soc']:.1f}%p (금지구역 침범)", color="#D7263D")
    ts = np.concatenate([tr["t"] for tr in E5["roll"]["trace"]])
    es = np.concatenate([tr["e"] for tr in E5["roll"]["trace"]])
    ax[1].plot((ts - E5["t0"]) / 60, 100 - soc_drop(es, dr), color="#0B3954", lw=2.5,
               label=f"롤링 재계획: SOC 소모 {E5['roll']['soc']:.1f}%p (안전 회피)")
    for p in plans:
        ax[1].axvline((p["t"] - E5["t0"]) / 60, color="#8FB8DE", lw=0.8)
    ax[1].axvline(1.0, color="#9B5DE5", lw=2)
    ax[1].text(1.05, 98, "금지구역 발표", color="#6A2C9E", fontsize=9)
    ax[1].set_xlabel("귀환 비행 경과 [분]"); ax[1].set_ylabel("귀환 구간 SOC [%] (출발 100% 기준)")
    ax[1].set_title("재계획 시점(세로선)과 SOC 변화")
    ax[1].legend(frameon=False, fontsize=9, loc="lower left")
    fig.tight_layout()
    fig.savefig(C.FIG / "F8_rolling_replan.png")
    plt.close(fig)


# ================================================================== F9 탑재중량
def fig_payload():
    E3 = L("E3")
    import pandas as pd
    d = pd.DataFrame(E3["rows"])
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.8))
    for n, kind in enumerate(("delivery", "pickup")):
        for key in ("A_shortest_v10", "D_WE_LTstar"):
            s = d[(d.kind == kind) & (d.method == key)]
            ax[n].plot(s.payload * 1000, s.soc_used, "o-", color=COL[key], lw=2, label=LBL[key])
            s2 = s[s.method == "D_WE_LTstar"]
        s = d[(d.kind == kind) & (d.method == "D_WE_LTstar")]
        ax[n].bar(s.payload * 1000 - 30, s.soc_out, 55, color="#1B6CA8", alpha=0.25, label="WE-LT* 갈 때")
        ax[n].bar(s.payload * 1000 + 30, s.soc_back, 55, color="#0B3954", alpha=0.25, label="WE-LT* 올 때")
        ax[n].axhline(80, color="#D7263D", ls="--", lw=1.2)
        ax[n].set_xlabel("탑재 중량 [g]"); ax[n].set_ylabel("SOC 소모 [%p]")
        ax[n].set_title("배송: 갈 때 적재 → 올 때 빈 기체" if kind == "delivery" else "회수: 갈 때 빈 기체 → 올 때 적재")
        ax[n].set_ylim(0, 125)
        ax[n].legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(C.FIG / "F9_payload.png")
    plt.close(fig)


# ================================================================== F10 3D 시각화
def fig_3d(g, fc):
    E1 = L("E1")
    st = E1["store"]
    fig = plt.figure(figsize=(12, 6.5))
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    i0, i1, j0, j1 = int(2300 / fc.dx), int(5000 / fc.dx), int(1200 / fc.dx), int(3400 / fc.dx)
    s = 2
    X, Y = np.meshgrid((np.arange(i0, i1, s) + .5) * fc.dx / 1000, (np.arange(j0, j1, s) + .5) * fc.dx / 1000)
    Z = g["surface"][j0:j1:s, i0:i1:s]
    ax.plot_surface(X, Y, Z, cmap="gist_earth", vmin=-80, vmax=250, linewidth=0, alpha=0.9, antialiased=False,
                    zorder=1)
    for key in ("A_shortest_v10", "D_WE_LTstar"):
        for leg, lsx in (("out", "-"), ("back", "--")):
            sim = st[("delivery", key)][leg]["sim"]
            ax.plot(sim["x"] / 1000, sim["y"] / 1000, sim["z"], lsx, color=COL[key], lw=3, zorder=10,
                    label=f"{LBL[key]} ({'갈 때' if leg == 'out' else '올 때'})")
    ax.set_zlim(0, 250)
    ax.set_xlabel("동서 [km]"); ax.set_ylabel("남북 [km]"); ax.set_zlabel("해발고도 [m]")
    ax.view_init(elev=38, azim=-60)
    ax.set_box_aspect((2.7, 2.2, 0.9))
    ax.legend(fontsize=9, loc="upper left")
    ax.set_title("3D 경로 (지형·건물 위 30~150 m AGL, 배송 0.5 kg, 15:00 KST)", fontsize=13)
    fig.tight_layout()
    fig.savefig(C.FIG / "F10_3d_paths.png")
    plt.close(fig)



# ================================================================== F11 가정 축소 실험
def fig_ablation():
    E6 = L("E6")
    rows = E6["rows"]
    keys = []
    for r in rows:
        if r["key"] not in keys:
            keys.append(r["key"])
    fig, ax = plt.subplots(figsize=(13, 5.4))
    y = np.arange(len(keys))[::-1]
    for n, (kind, col) in enumerate((("delivery", "#1B6CA8"), ("pickup", "#E07B39"))):
        for yi, k in zip(y, keys):
            r = next(q for q in rows if q["key"] == k and q["kind"] == kind)
            yy = yi + (0.2 if n == 0 else -0.2)
            if r["soc_used"] is None:
                ax.text(0.3, yy, "계획 불가 (역풍 > 고정 대기속도)", va="center", fontsize=10, color=col)
                continue
            d = r["d_soc"]
            ax.barh(yy, d, 0.38, color=col, alpha=0.9 if r["feasible"] else 0.45,
                    hatch=None if r["feasible"] else "//", edgecolor="white",
                    label=("배송" if n == 0 else "회수") if k == keys[0] else None)
            ax.text(d + 0.15 if d >= 0 else d - 0.15, yy,
                    f"{r['soc_used']:.1f}%p ({d:+.1f})" + ("" if r["feasible"] else " 예비 미달"),
                    va="center", ha="left" if d >= 0 else "right", fontsize=10, color=INK)
    first = {k: next(q for q in rows if q["key"] == k) for k in keys}
    ax.set_yticks(y)
    ax.set_yticklabels([first[k]["label"] + "\n" + first[k]["ref"] for k in keys], fontsize=10)
    ax.axvline(0, color=INK, lw=1)
    ax.set_xlabel("제안 WE-LT* 대비 왕복 SOC 소모 증가 [%p]  (빗금: 예비 SOC 20% 미달)")
    ax.set_title("가정 축소 실험: 비교 축을 하나씩 단순화하면 SOC가 얼마나 더 드는가 (15:00, 0.5 kg, 실제 바람)",
                 fontsize=13)
    ax.legend(frameon=False, loc="lower right")
    ds = [q["d_soc"] for q in rows if q["d_soc"] is not None]
    ax.set_xlim(min(ds + [0]) - 1.5, max(ds + [1]) + 7)
    fig.tight_layout()
    fig.savefig(C.FIG / "F11_ablation.png")
    plt.close(fig)


# ================================================================== F12 정식화(수식) 이미지
def fig_formulation():
    fig = plt.figure(figsize=(13, 5.6))

    def T(x, y, s, **k):
        fig.text(x, y, s, **{"fontsize": 15, "color": INK, **k})

    H = dict(fontsize=14, weight="bold", color="#376092")
    T(0.0, 0.94, "(P) 에너지 최적 경로 = 예상 SOC 감소량 최소화", **H)
    T(0.02, 0.81, r"$\min_{\pi,\ V_{g,i},\ \tau_i}\ \Delta SOC=\frac{1+\epsilon_R}{E_b}\left[\sum_{\ell}\sum_{i\in\ell}"
                  r"P\left(V_{a,i},\,V_{g,i}s_i,\,m_\ell,\,\rho(z_i)\right)\frac{d_i}{V_{g,i}\sqrt{1+s_i^2}}"
                  r"+\sum_i P_h\,\tau_i+E_{TO}+E_{LD}\right]\times 100$", fontsize=17)
    T(0.02, 0.67, r"s.t.  $V_{a,i}=\Vert V_{g,i}\hat{e}_i-\mathbf{w}(\mathbf{x}_i,t_i)\Vert\leq V_{a,\max}$,   "
                  r"$t_{i+1}=t_i+\tau_i+\frac{d_i}{V_{g,i}\sqrt{1+s_i^2}}$,   "
                  r"$\mathbf{x}(t)\in\mathcal{F},\ \mathbf{x}(t)\notin\mathcal{O}_k\ \forall t\in[a_k,b_k]$,   "
                  r"$100-\Delta SOC\geq 20$", fontsize=14)
    T(0.0, 0.53, "(1) 구간 속도 = 탐색 내부의 최적화 (간선 비용 정의)", **H)
    T(0.02, 0.43, r"$e^\star(\hat{e},\mathbf{w},m)=\min_{V_g\in\mathcal{V}}\ \frac{P(\Vert V_g\hat{e}-\mathbf{w}\Vert,"
                  r"\ V_g s,\ m)}{V_g\sqrt{1+s^2}}\ \leq\ e_{fix}\quad"
                  r"(fixed\ V_a:\ V_g=w_\parallel+\sqrt{\bar V_a^2-w_\perp^2})$", fontsize=15)
    T(0.0, 0.31, "(2) 에너지 비교형 지름길 (바람장에서는 삼각부등식이 성립하지 않음)", **H)
    T(0.02, 0.22, r"$c_E(p,v)\ \nleq\ c_E(p,u)+c_E(u,v)\ \ \Rightarrow\ \ "
                  r"par(v)\leftarrow\arg\min\{\,g(u)+c_E(u,v,t_u),\ g(p)+c_E(p,v,t_p)\,\}$", fontsize=15)
    T(0.0, 0.10, "(3) 호버링 vs 우회 (대기 행동)                                (4) 배송/회수 질량 (leg별)", **H)
    T(0.02, 0.01, r"hover $\Leftrightarrow P_h(m,\mathbf{w})\,\tau+E(\pi_{direct})<E(\pi_{detour})$", fontsize=15)
    T(0.50, 0.01, r"$E_{pickup}-E_{delivery}\approx\frac{\partial P}{\partial m}\,\Delta m\,(T_{back}-T_{out})$",
      fontsize=15)
    fig.savefig(C.FIG / "F12_formulation.png")
    plt.close(fig)



# ================================================================== F13 확장: 8방향 + 고도별 균일 선형 바람
def fig_layered(g, fc):
    E7 = L("E7")
    from scipy.spatial import ConvexHull
    fig = plt.figure(figsize=(16, 9.6))
    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 1.25], hspace=0.38, wspace=0.32)
    # (a) 선형 시어 적합
    ax = fig.add_subplot(gs[0, 0])
    prof, n6 = E7["prof"], E7["n6"]
    zs, us, vs = prof["levels"][n6], prof["u"][n6], prof["v"][n6]
    zz = np.linspace(10, 320, 50)
    for comp, val, a, b, col in (("U(동서)", us, prof["a_u"][n6], prof["b_u"][n6], "#1B6CA8"),
                                 ("V(남북)", vs, prof["a_v"][n6], prof["b_v"][n6], "#E07B39")):
        ax.plot(val, zs, "o", color=col, label=f"GFS {comp}")
        ax.plot(a + b * zz, zz, "-", color=col, lw=2, label=f"선형 적합 {comp}: {b * 100:+.1f} m/s/100 m")
    ax.axhspan(30, 150, color="#1B6CA8", alpha=0.07)
    ax.set_xlabel("바람 성분 [m/s]"); ax.set_ylabel("지상 고도 [m]")
    ax.set_title(f"(a) 고도별 균일·선형 바람 적합 (15시, R² {prof['r2'][n6]:.2f})", fontsize=12)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    # (b) 단위구(비용 노름) 비볼록성
    ax = fig.add_subplot(gs[0, 1])
    E = E7["E"]
    th = 2 * np.pi * np.arange(72) / 72
    for k, col in ((2, "#8FB8DE"), (8, "#1B6CA8"), (14, "#0B3954")):
        e = E[n6, k]
        if not np.isfinite(e).all():
            continue
        r = 1000.0 / e                           # e: J/m → 1 kJ 로 갈 수 있는 거리 [m]
        x, y = r * np.sin(th), r * np.cos(th)
        ax.plot(np.r_[x, x[0]], np.r_[y, y[0]], color=col, lw=2, label=f"해발 {E7['z'][k]:.0f} m")
        h = ConvexHull(np.c_[x, y])
        hv = np.r_[h.vertices, h.vertices[0]]
        ax.plot(x[hv], y[hv], "--", color=col, lw=1)
    for d in range(8):
        a = 2 * np.pi * d / 8
        ax.plot([0, 60 * np.sin(a)], [0, 60 * np.cos(a)], color="#CCCCCC", lw=0.8, zorder=0)
    ax.set_aspect("equal")
    ax.set_xlabel("동 [m/kJ]"); ax.set_ylabel("북 [m/kJ]")
    cv = E7["convexity"]
    md = max(v["max_deficit"] for v in cv.values())
    ax.set_title(f"(b) 1 kJ 도달거리 = 비용 노름 단위구\n실선이 점선(볼록 껍질) 안으로 최대 {md * 100:.0f}% 들어감 → 비볼록",
                 fontsize=11)
    ax.legend(fontsize=8, frameon=False, loc="lower left")
    # (c) 손익분기 상승 거리
    ax = fig.add_subplot(gs[0, 2])
    be = E7["breakeven"]
    dzs = [int(k[:-1]) for k in be]
    tail = [be[k]["tail"] / 1000 for k in be]
    ax.bar(np.arange(len(dzs)), tail, color="#1B6CA8")
    for x, v in enumerate(tail):
        ax.text(x, v + 3, f"{v:.0f} km", ha="center", fontsize=10)
    ax.set_xticks(range(len(dzs))); ax.set_xticklabels([f"+{d} m" for d in dzs])
    ax.set_ylabel("손익분기 수평거리 L* [km]")
    ax.set_title("(c) 순풍을 더 받으려고 상승할 때의 손익분기 거리\n(역풍 방향은 상승할수록 손해 → L* = ∞)", fontsize=11)
    ax.axhline(2.4, color="#D7263D", ls="--"); ax.text(-0.35, 14, "본 임무 편도 2.4 km (빨간 점선)", color="#D7263D", fontsize=9)
    # (d) SOC·CPU 비교
    ax = fig.add_subplot(gs[1, 0:2])
    rows = E7["rows"]
    keys = []
    for r in rows:
        if r["key"] not in keys:
            keys.append(r["key"])
    x = np.arange(len(keys))
    for n, (kind, col) in enumerate((("delivery", "#1B6CA8"), ("pickup", "#E07B39"))):
        v = [next(r for r in rows if r["kind"] == kind and r["key"] == k)["soc_used"] for k in keys]
        ax.bar(x + (n - 0.5) * 0.38, v, 0.36, color=col, label="배송" if n == 0 else "회수")
        for xi, vi in zip(x + (n - 0.5) * 0.38, v):
            ax.text(xi, vi + 0.4, f"{vi:.1f}", ha="center", fontsize=9)
    cpu = [np.mean([r["cpu"] for r in rows if r["key"] == k and r["kind"] == "pickup"]) for k in keys]
    labs = [next(r for r in rows if r["key"] == k)["label"].replace(" + ", "\n+ ").replace(" (", "\n(") for k in keys]
    ax.set_xticks(x); ax.set_xticklabels([f"{l}\n[{c:.2f} s]" for l, c in zip(labs, cpu)], fontsize=8.5)
    ax.axhline(80, color="#D7263D", ls="--", lw=1)
    ax.set_ylim(70, 90); ax.set_ylabel("왕복 SOC 소모 [%p] (실제 바람 평가)")
    ax.set_title("(d) 탐색 구조별 SOC와 왕복 계산시간 [ ]  (선형 바람 가정 4종 vs 3D 바람장 기준)", fontsize=12)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    # (e) 경로
    ax = fig.add_subplot(gs[1, 2])
    basemap(ax, g, fc)
    st = E7["store"]
    for key, col, lab in (("L8_Astar", "#E07B39", "8방향+수직 A*"), ("L26_LT3D", "#2CA58D", "8방향 + 3D 지름길(LUT)"),
                          ("WELT_full", "#1B6CA8", "WE-LT* (3D 바람장)")):
        for leg, lsx in (("out", "-"), ("back", "--")):
            xx, yy, _ = P2(fc, st[("delivery", key)][leg])
            ax.plot(xx, yy, lsx, color=col, lw=2, label=lab if leg == "out" else None)
    ax.set_xlim(2.3, 5.0); ax.set_ylim(1.2, 3.4)
    ax.legend(fontsize=8, loc="lower left", framealpha=0.9)
    ax.set_title("(e) 배송 경로 (실선 갈 때 / 점선 올 때)", fontsize=12)
    fig.savefig(C.FIG / "F13_layered_8way.png")
    plt.close(fig)



# ================================================================== F14 3D 시공간 (동적 장애물)
def _clearance_series(sim, o, t_off):
    import math as _m
    out = []
    for t, x, y, z in zip(sim["t"], sim["x"], sim["y"], sim["z"]):
        p = o.position(t - t_off)
        if p is None:
            out.append(np.nan); continue
        if o.kind == "box":
            dxy = max(abs(x - p[0]) - o.half_x, abs(y - p[1]) - o.half_y, 0.0)
        elif o.kind == "sphere":
            out.append(_m.sqrt((x - p[0]) ** 2 + (y - p[1]) ** 2 + (z - p[2]) ** 2) - o.radius); continue
        else:
            dxy = _m.hypot(x - p[0], y - p[1]) - o.radius
        inz = o.z_min <= z <= o.z_max
        out.append(dxy if inz else max(dxy, min(abs(z - o.z_min), abs(z - o.z_max))))
    return np.array(out)


def fig_dynamic(g, fc):
    from dpo.obstacles import DynObstacle
    E8 = L("E8")
    obs = [DynObstacle.from_dict(o) for o in E8["obstacles"]]
    st = E8["store"]
    cols = ["#9B5DE5", "#E07B39", "#2CA58D"]
    fig = plt.figure(figsize=(16, 10))
    gsp = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.2], hspace=0.32, wspace=0.18)
    ax = fig.add_subplot(gsp[:, 0])
    basemap(ax, g, fc)
    s0, s1 = st["static_WELT"]["sim"], st["st_full"]["sim"]
    for o, c in zip(obs, cols):
        xs = [p["x"] / 1000 for p in o.path]; ys = [p["y"] / 1000 for p in o.path]
        if len(xs) > 1:
            ax.annotate("", (xs[-1], ys[-1]), (xs[0], ys[0]), arrowprops=dict(arrowstyle="->", color=c, lw=1.5, ls="--"))
        # 기준 경로와 가장 가까워지는 시각의 형상
        cl = _clearance_series(s0, o, T_MAIN)
        q = int(np.nanargmin(cl)) if np.isfinite(cl).any() else 0
        p = o.position(s0["t"][q] - T_MAIN) or (o.path[0]["x"], o.path[0]["y"], 0)
        if o.kind == "box":
            ax.add_patch(plt.Rectangle(((p[0] - o.half_x) / 1000, (p[1] - o.half_y) / 1000), 2 * o.half_x / 1000,
                                       2 * o.half_y / 1000, fc=c, alpha=0.35, ec=c, lw=1.5))
        else:
            ax.add_patch(Circle((p[0] / 1000, p[1] / 1000), o.radius / 1000, fc=c, alpha=0.35, ec=c, lw=1.5))
        ax.text(p[0] / 1000 + 0.05, p[1] / 1000 + 0.05, f"{o.name}\n(t={s0['t'][q] - T_MAIN:.0f} s)", fontsize=8, color=INK)
    ax.plot(s0["x"] / 1000, s0["y"] / 1000, "--", color="#555555", lw=2, label="WE-LT* (동적 장애물 무시)")
    ax.plot(s1["x"] / 1000, s1["y"] / 1000, "-", color="#1B6CA8", lw=3, label="시공간 WE-SIPP-Theta*")
    for o in obs:
        cl = _clearance_series(s0, o, T_MAIN)
        bad = np.where(cl <= 0)[0]
        if len(bad):
            ax.plot(s0["x"][bad] / 1000, s0["y"][bad] / 1000, "x", color="#D7263D", ms=8, mew=2)
    ax.plot([], [], "x", color="#D7263D", ms=8, mew=2, label="충돌 지점")
    ax.set_xlim(2.4, 5.0); ax.set_ylim(1.2, 3.4)
    ax.legend(fontsize=9, loc="lower left", framealpha=0.9)
    ax.set_title("동적 장애물 3종 (배송 갈 때, 15:00, 0.5 kg)", fontsize=13)
    # (b) 이격 거리
    ax = fig.add_subplot(gsp[0, 1])
    for o, c in zip(obs, cols):
        for sim, ls, lab in ((s0, "--", "무시"), (s1, "-", "시공간")):
            cl = _clearance_series(sim, o, T_MAIN)
            ax.plot(sim["t"] - T_MAIN, cl, ls, color=c, lw=2 if ls == "-" else 1.5, label=f"{o.name} – {lab}")
    ax.axhline(0, color="#D7263D", lw=1.5)
    ax.fill_between([0, 150], -400, 0, color="#D7263D", alpha=0.07)
    ax.set_ylim(-250, 800); ax.set_xlim(0, 150)
    ax.set_xlabel("출발 후 시간 [s]"); ax.set_ylabel("장애물 표면까지 거리 [m]")
    ax.set_title("시간별 이격 거리 (0 아래 = 충돌, 점선 = 장애물 무시 경로)", fontsize=12)
    ax.legend(fontsize=7.5, ncol=2, frameon=False, loc="upper right")
    # (c) 표
    ax = fig.add_subplot(gsp[1, 1]); ax.axis("off")
    rows = [r for r in E8["rows"] if r.get("feasible")]
    tab = [[r["label"], f"{r['E_kJ']:.1f}", f"{r['soc_leg']:.1f}", f"{r['T']:.0f}", f"{r['wait']:.0f}",
            f"{r['L'] / 1000:.2f}", ("충돌" if r["n_conf"] else f"{r['min_clear']:.0f}") if np.isfinite(r["min_clear"]) else "–",
            f"{r['cpu']:.1f}"] for r in rows]
    t = ax.table(cellText=tab, colLabels=["계획기", "에너지[kJ]", "SOC[%p]", "시간[s]", "대기[s]", "거리[km]",
                                           "최소이격[m]", "계산[s]"], loc="center", cellLoc="center",
                 colWidths=[0.34, 0.1, 0.09, 0.08, 0.08, 0.09, 0.11, 0.08])
    t.auto_set_font_size(False); t.set_fontsize(9.5); t.scale(1, 1.8)
    for (rr, cc), cell in t.get_celld().items():
        if rr == 0:
            cell.set_facecolor("#1B6E8C"); cell.set_text_props(color="white", weight="bold")
        elif "충돌" in tab[rr - 1][6] and cc == 6:
            cell.set_text_props(color="#D7263D", weight="bold")
    ax.set_title("결과 (실제 바람 평가 · B = 지도 전폭 통제 띠 0~80 s)", fontsize=12)
    fig.savefig(C.FIG / "F14_dynamic_obstacles.png")
    plt.close(fig)


def gif_dynamic(g, fc, n_frames=40):
    import imageio.v2 as imageio
    from dpo.obstacles import DynObstacle
    E8 = L("E8")
    obs = [DynObstacle.from_dict(o) for o in E8["obstacles"]]
    cols = ["#9B5DE5", "#E07B39", "#2CA58D"]
    s0, s1 = E8["store"]["static_WELT"]["sim"], E8["store"]["st_full"]["sim"]
    tmax = max(s0["t"][-1], s1["t"][-1]) - T_MAIN
    frames = []
    for tf in np.linspace(0, tmax, n_frames):
        fig, axs = plt.subplots(1, 2, figsize=(11, 4.9))
        for ax, sim, title, col in ((axs[0], s0, "동적 장애물 무시 (WE-LT*)", "#555555"),
                                    (axs[1], s1, "3D 시공간 계획 (WE-SIPP-Theta*)", "#1B6CA8")):
            basemap(ax, g, fc, nfz=True)
            ax.set_xlim(2.4, 5.0); ax.set_ylim(1.2, 3.4)
            for o, c in zip(obs, cols):
                p = o.position(tf)
                if p is None:
                    continue
                if o.kind == "box":
                    ax.add_patch(plt.Rectangle(((p[0] - o.half_x) / 1000, (p[1] - o.half_y) / 1000), 2 * o.half_x / 1000,
                                               2 * o.half_y / 1000, fc=c, alpha=0.45, ec=c))
                else:
                    ax.add_patch(Circle((p[0] / 1000, p[1] / 1000), o.radius / 1000, fc=c, alpha=0.45, ec=c))
            m = sim["t"] - T_MAIN <= tf
            ax.plot(sim["x"] / 1000, sim["y"] / 1000, ":", color=col, lw=1)
            ax.plot(sim["x"][m] / 1000, sim["y"][m] / 1000, "-", color=col, lw=3)
            if m.any():
                q = np.where(m)[0][-1]
                hitting = any((_clearance_series({k: v[q:q + 1] for k, v in sim.items()}, o, T_MAIN) <= 0).any() for o in obs)
                ax.plot(sim["x"][q] / 1000, sim["y"][q] / 1000, "o", ms=11, mfc="#D7263D" if hitting else "#FFD23F", mec=INK)
                if hitting:
                    ax.text(sim["x"][q] / 1000 + 0.08, sim["y"][q] / 1000, "충돌!", color="#D7263D", fontsize=13, weight="bold")
            ax.set_title(f"{title}   t = {tf:4.0f} s", fontsize=11)
            ax.set_xlabel(""); ax.set_ylabel("")
        fig.tight_layout()
        fig.canvas.draw()
        frames.append(np.asarray(fig.canvas.buffer_rgba())[..., :3].copy())
        plt.close(fig)
    mid = frames[len(frames) // 2]
    imageio.mimsave(C.FIG / "dynamic_obstacles.gif", [mid] + frames, duration=[1.5] + [0.22] * (len(frames) - 1) + [1.5],
                    loop=0)


if __name__ == "__main__":
    g, fc, tr = sites()
    todo = sys.argv[1:] or ["site", "wind", "energy", "paths", "soc", "departure", "hover", "rolling", "payload", "3d",
                            "ablation", "formulation", "layered", "dynamic", "gifdyn"]
    fns = dict(site=lambda: fig_site(g, fc), wind=lambda: fig_wind_profile(g, fc, tr), energy=fig_energy,
               paths=lambda: fig_paths(g, fc), soc=lambda: fig_soc(fc), departure=lambda: fig_departure(g, fc),
               hover=lambda: fig_hover(g, fc), rolling=lambda: fig_rolling(g, fc), payload=fig_payload,
               ablation=fig_ablation, formulation=fig_formulation, layered=lambda: fig_layered(g, fc),
               dynamic=lambda: fig_dynamic(g, fc), gifdyn=lambda: gif_dynamic(g, fc),
               **{"3d": lambda: fig_3d(g, fc)})
    for t in todo:
        if t == "gif":
            continue
        try:
            fns[t]()
            print("ok", t)
        except FileNotFoundError as ex:
            print("skip", t, ex)


# ================================================================== GIF: 롤링 재계획 애니메이션
def gif_rolling(g, fc, n_frames=36):
    import imageio.v2 as imageio
    E5 = L("E5")
    dr = drone_calibrated()
    cx, cy, R, ta = E5["popup"]
    t0 = E5["t0"]
    tr = E5["roll"]["trace"]
    ts = np.concatenate([s["t"] for s in tr]); xs = np.concatenate([s["x"] for s in tr]) / 1000
    ys = np.concatenate([s["y"] for s in tr]) / 1000; es = np.concatenate([s["e"] for s in tr])
    sb = E5["base"]["sim"]
    plans = E5["roll"]["plans"]
    frames = []
    tmax = ts[-1]
    for tf in np.linspace(t0, tmax, n_frames):
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw=dict(width_ratios=[1, 1]))
        basemap(ax[0], g, fc)
        ax[0].set_xlim(2.2, 5.2); ax[0].set_ylim(1.1, 3.6)
        if tf >= ta:
            ax[0].add_patch(Circle((cx / 1000, cy / 1000), R / 1000, fc="#9B5DE5", alpha=0.35, ec="#6A2C9E"))
            ax[0].text(cx / 1000, cy / 1000, "돌발\n금지구역", ha="center", fontsize=8, color="#4B0082")
        x0, y0, _ = P2(fc, E5["base"]["path"])
        ax[0].plot(x0, y0, "--", color="#9E9E9E", lw=1.5, label="초기 계획")
        cur = ([p for p in plans if p["t"] <= tf] or plans[:1])[-1]
        x1, y1, _ = P2(fc, cur["path"])
        ax[0].plot(x1, y1, color="#8FB8DE", lw=2, label="현재 계획")
        m = ts <= tf
        ax[0].plot(xs[m], ys[m], color="#0B3954", lw=3, label="비행 궤적")
        if m.any():
            ax[0].plot(xs[m][-1], ys[m][-1], "o", ms=9, mfc="#FFD23F", mec=INK)
        ax[0].legend(fontsize=8, loc="lower left")
        ax[0].set_title(f"귀환 비행 t = {tf - t0:4.0f} s  (재계획 {sum(p['t'] <= tf for p in plans)}회)", fontsize=12)
        ax[1].plot((sb["t"] - t0) / 60, 100 - soc_drop(sb["e"], dr), "--", color="#9E9E9E", label="초기 계획(침범)")
        ax[1].plot((ts[m] - t0) / 60, 100 - soc_drop(es[m], dr), color="#0B3954", lw=2.5, label="롤링 재계획")
        ax[1].set_xlim(0, (tmax - t0) / 60 + 0.3); ax[1].set_ylim(35, 100)
        ax[1].set_xlabel("경과 [분]"); ax[1].set_ylabel("SOC [%]")
        ax[1].legend(fontsize=8, frameon=False, loc="lower left")
        ax[1].set_title("SOC 변화", fontsize=12)
        fig.tight_layout()
        fig.canvas.draw()
        frames.append(np.asarray(fig.canvas.buffer_rgba())[..., :3].copy())
        plt.close(fig)
    # 첫 프레임 = 최종 상태(정지 화면/인쇄용), 이후 처음부터 재생
    frames = [frames[-1]] + frames
    imageio.mimsave(C.FIG / "rolling_replan.gif", frames, duration=[1.5] + [0.25] * (len(frames) - 2) + [2.0], loop=0)


if __name__ == "__main__" and "gif" in sys.argv[1:]:
    gif_rolling(*sites()[:2])
    print("ok gif")
