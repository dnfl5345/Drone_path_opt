"""창원 창이대로 71 주변 순회 비행 시뮬레이션 → results/changwon.json, 그림 F27, results/missions/changwon_*

대상 기체 Holybro X650 (전력 모델은 추정), 출발·복귀 지점 = 창이대로 71, 경유점 5곳을 돌고 돌아온다.
(1) 장애물 없음 / 예시 장애물 3종 / 장애물 + 위치 불확실성(ε = 0.05) 세 경우의 계획 결과
(2) 같은 순회를 '최단 경로 + 정속 10 m/s' 로 날았을 때와 비교
(3) 출발 시각 09–20시에 따른 SOC
(4) ArduPilot 단일 미션 내보내기

사용: PYTHONPATH=src python scripts/run_changwon.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

from dpo import config as C

C.use_site("changwon")
from dpo.energy import soc_drop  # noqa: E402
from dpo.planner import Planner, simulate, vertical_energy  # noqa: E402
from dpo.webapp.server import App, KST  # noqa: E402

PAYLOAD, DWELL, HOUR = 0.0, 10.0, 15


def leg_rows(r):
    out = []
    for L in r["legs"]:
        if not L["feasible"]:
            out.append(dict(name=L["name"], feasible=False))
            continue
        zs = [p["z"] for p in L["samples"]]
        mc = L["conflicts"]["min_clearance"]
        out.append(dict(name=L["name"], feasible=True, E_kJ=L["E_kJ"], soc=L["soc_leg"], T=L["T"], L=L["L"], wait=L["wait"],
                        z_min=min(zs), z_max=max(zs), min_clearance=mc if mc is not None and math.isfinite(mc) else None,
                        hits=L["conflicts"]["n_collision_samples"]))
    return out


def case(app, name, obstacles, eps=None, hour=HOUR, keep=False):
    r = app.plan(dict(drone="x650", payload=PAYLOAD, dep_hour=hour, reserve=20, tour=app.tour0, dwell=DWELL,
                      obstacles=obstacles, eps=eps))
    rows = leg_rows(r)
    ok = [x for x in rows if x["feasible"]]
    m = app.mission()["legs"][0] if r["feasible"] or len(ok) == len(rows) else None
    out = dict(name=name, feasible=r["feasible"], soc_used=r["soc_used"], soc_end=r["soc_end"], legs=rows,
               T=m["summary"]["duration_s"] if m else None, L=sum(x["L"] for x in ok),
               wait=sum(x["wait"] for x in ok) - DWELL * (len(ok) - 1),
               baseline_hit_legs=[b["name"] for b in r["baseline"] if b["conflicts"]["n_collision_samples"]],
               hits=sum(x["hits"] for x in ok),
               min_clearance=min([x["min_clearance"] for x in ok if x["min_clearance"] is not None], default=None),
               mission=None if m is None else m["summary"], errors=r["errors"])
    print(f"[{name}] SOC 소모 {out['soc_used']:.1f} %p, 시간 {out['T']:.0f} s, 거리 {out['L']:.0f} m, 추가 대기 {out['wait']:.0f} s, "
          f"최소 이격 {out['min_clearance']}, 충돌 표본 {out['hits']}, 장애물 무시 경로 충돌 구간 {out['baseline_hit_legs']}", flush=True)
    return (out, r, m) if keep else out


def tour_static(site, d, pts, hour, mode, policy, vconst=10.0):
    """장애물 없는 순회를 Planner 로 직접 계산 (최단 경로 + 정속과의 비교용)."""
    P = Planner(site, d)
    t = (hour - KST) * 3600.0
    t_dep, m, e = t, d.m_empty + PAYLOAD, 0.0
    L = 0.0
    for q in range(len(pts) - 1):
        a, b = pts[q], pts[q + 1]
        st, gl = site.lowest_free_k(*a), site.lowest_free_k(*b)
        if q == 0:
            e_to, t_to = vertical_energy(site, d, a[0], a[1], st[0], t, m, up=True)
            e += e_to
            t += t_to
        r = P.plan(st, gl, t_start=t, mass=m, mode=mode, vconst=vconst)
        if r is None:
            return None
        waits = np.array(r["wait_before"], float)
        if q > 0:
            waits[1] += DWELL
        sim = simulate(site, d, r["path"], waits, t, m, policy, vconst)
        if sim is None:
            return None
        e += float(sim["e"][-1])
        t = float(sim["t"][-1])
        L += float(np.sum(np.hypot(np.diff(sim["x"]), np.diff(sim["y"]))))
    e_ld, t_ld = vertical_energy(site, d, pts[-1][0], pts[-1][1], gl[0], t, m, up=False)
    return dict(soc=float(soc_drop(e + e_ld, d)), T=float(t + t_ld - t_dep), L=L)


def figure(R, app, plans):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from matplotlib.colors import LightSource
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "mathtext.fontset": "dejavusans", "savefig.dpi": 200, "savefig.bbox": "tight",
                         "axes.titleweight": "bold"})
    PAL = ["#E46C0A", "#1B6CA8", "#2CA58D", "#8E44AD", "#C9A227", "#D7263D"]
    s = app.site
    W, H = s.nx * s.dx / 1000, s.ny * s.dx / 1000
    fig = plt.figure(figsize=(17, 6.4))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.08, 1.0], hspace=0.42, wspace=0.16)

    ax = fig.add_subplot(gs[:, 0])
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap
    terr = LinearSegmentedColormap.from_list("terr", ["#DDE6D5", "#BFD2A8", "#D8CFA6", "#B79F7C", "#F1ECE2"])
    rgb = LightSource(315, 45).shade(s.ground, cmap=terr, vert_exag=2.5, blend_mode="soft")
    ax.imshow(rgb, origin="lower", extent=[0, W, 0, H])
    ax.imshow(np.ma.masked_where(s.bldg_h <= 0, s.bldg_h), origin="lower", extent=[0, W, 0, H],
              cmap=ListedColormap(["#6B7480"]), alpha=0.55)
    r0, r1 = plans["none"], plans["obs"]
    for L in r0["legs"]:
        ax.plot([p["x"] / 1000 for p in L["samples"]], [p["y"] / 1000 for p in L["samples"]], color="#111", lw=1.1, ls="--",
                zorder=3)
    for q, L in enumerate(r1["legs"]):
        ax.plot([p["x"] / 1000 for p in L["samples"]], [p["y"] / 1000 for p in L["samples"]], color=PAL[q % 6], lw=3.0, zorder=4)
    oc = ["#8E44AD", "#D7263D", "#2C7FB8"]
    box = dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.9)
    key = []
    for k, o in enumerate(r1["obstacles"]):
        xs, ys = [p["x"] / 1000 for p in o["path"]], [p["y"] / 1000 for p in o["path"]]
        tag = "ABC"[k % 3]
        if o["kind"] == "box":
            ax.add_patch(plt.Rectangle((xs[0] - o["half_x"] / 1000, ys[0] - o["half_y"] / 1000), 2 * o["half_x"] / 1000,
                                       2 * o["half_y"] / 1000, fc=oc[k % 3], ec=oc[k % 3], alpha=0.35, zorder=3))
            ax.text(xs[0], ys[0], tag, color=oc[k % 3], fontsize=10, ha="center", va="center", fontweight="bold", bbox=box, zorder=8)
            key.append(f"{tag}  {o['name']}, 0–{o['t_end']:.0f} s")
        else:
            ax.annotate("", xy=(xs[-1], ys[-1]), xytext=(xs[0], ys[0]), arrowprops=dict(arrowstyle="->", color=oc[k % 3], lw=1.8, ls=":"),
                        zorder=5)
            ax.add_patch(plt.Circle((xs[1], ys[1]), (o["radius"] + o["margin"]) / 1000, fc=oc[k % 3], alpha=0.35, zorder=3))
            ax.text(xs[0], ys[0], tag, color=oc[k % 3], fontsize=10, ha="center", va="center", fontweight="bold", bbox=box, zorder=8)
            key.append(f"{tag}  {o['name']}, 경로와 만나는 시각 {o['path'][1]['t']:.0f} s")
    ax.text(0.015, 0.015, "\n".join(key), transform=ax.transAxes, fontsize=9, va="bottom", ha="left", bbox=box, zorder=9)
    hub = app.hub0
    ax.scatter([hub[0] / 1000], [hub[1] / 1000], s=260, marker="*", color="#004E8F", ec="w", zorder=7)
    ax.text(hub[0] / 1000 + 0.16, hub[1] / 1000 + 0.02, "창이대로 71\n(출발·복귀)", fontsize=9.5, color="#004E8F", fontweight="bold",
            ha="left", va="center", bbox=box, zorder=8)
    for n, p in enumerate(app.tour0):
        ax.scatter([p[0] / 1000], [p[1] / 1000], s=210, color="#E46C0A", ec="w", zorder=6)
        ax.text(p[0] / 1000, p[1] / 1000, str(n + 1), color="w", fontsize=9, ha="center", va="center", fontweight="bold", zorder=7)
    ax.set_xlim(0, W); ax.set_ylim(0, H)
    ax.set_xlabel("동쪽 [km]"); ax.set_ylabel("북쪽 [km]")
    ax.set_title("(a) 창이대로 71 주변 순회 경로 (실선: 장애물 회피, 점선: 장애물 없을 때)")

    ax = fig.add_subplot(gs[0, 1])
    d0, tot = 0.0, []
    for q, L in enumerate(r1["legs"]):
        sm = L["samples"]
        d = d0 + np.concatenate([[0], np.cumsum(np.hypot(np.diff([p["x"] for p in sm]), np.diff([p["y"] for p in sm])))])
        ii = np.clip((np.array([p["x"] for p in sm]) // s.dx).astype(int), 0, s.nx - 1)
        jj = np.clip((np.array([p["y"] for p in sm]) // s.dx).astype(int), 0, s.ny - 1)
        ax.fill_between(d / 1000, 0, s.ground[jj, ii] + s.bldg_h[jj, ii], color="#B9C2CC", lw=0)
        ax.fill_between(d / 1000, 0, s.ground[jj, ii], color="#C9B88A", lw=0)
        ax.plot(d / 1000, [p["z"] for p in sm], color=PAL[q % 6], lw=2.2)
        tot.append((d / 1000, [p["soc"] for p in sm]))
        ax.text(d[0] / 1000 + 0.03, max(p["z"] for p in sm) + 9, f"구간 {q + 1}", color=PAL[q % 6], fontsize=8.5)
        d0 = d[-1]
    ax.set_ylim(0, max(max(p["z"] for p in L["samples"]) for L in r1["legs"]) + 28); ax.set_xlim(0, d0 / 1000)
    ax.set_ylabel("고도 (해발) [m]"); ax.set_xlabel("비행 거리 [km]")
    ax2 = ax.twinx()
    for dd, sc in tot:
        ax2.plot(dd, sc, color="#16202A", lw=1.2)
    ax2.set_ylabel("SOC [%]"); ax2.set_ylim(50, 101)
    ax.set_title("(b) 경로 아래 지형·건물과 비행 고도, SOC (검은 선)")

    ax = fig.add_subplot(gs[1, 1])
    sw = R["sweep"]
    hrs = [x["hour"] for x in sw]
    ax.plot(hrs, [x["shortest_v10"]["soc"] if x["shortest_v10"] else np.nan for x in sw], "o-", color="#9E9E9E", label="최단 경로 + 정속 10 m/s")
    ax.plot(hrs, [x["proposed"]["soc"] for x in sw], "o-", color="#E46C0A", lw=2.2, label="제안 방법")
    ax.set_ylabel("순회 SOC 소모 [%p]"); ax.set_xlabel("출발 시각 [시]")
    ax.set_xticks(hrs)
    ax3 = ax.twinx()
    ax3.bar(hrs, [x["wind"] for x in sw], color="#1B6CA8", alpha=0.18, width=0.7)
    ax3.set_ylabel("지면 위 100 m 풍속 [m/s]", color="#1B6CA8")
    ax.set_zorder(ax3.get_zorder() + 1); ax.patch.set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.set_title("(c) 출발 시각에 따른 SOC 소모 (막대: 풍속)")
    fig.savefig(C.FIG / "F27_changwon_tour.png")
    plt.close(fig)


def main():
    app = App()
    s, d = app.site, app.drones["x650"]
    from dpo.data_terrain import xy2ll
    ex = app.example()["obstacles"]
    unc = [dict(o, sigma0=10.0, sigma_v=0.5) if len(o["path"]) > 1 else o for o in ex]
    R = dict(site=dict(key=C.SITE_KEY, label=C.SITE["label"], anchor=C.SITE["anchor"], W=s.nx * s.dx, H=s.ny * s.dx,
                       ground=[float(s.ground.min()), float(s.ground.max())], n_buildings=int(app.g["n_buildings"]),
                       bldg_max=float(s.bldg_h.max()), z=[float(s.z[0]), float(s.z[-1])], date=C.SITE["date"]),
             hub=dict(xy=app.hub0, lonlat=[float(v) for v in xy2ll(*app.hub0)]),
             tour=[dict(xy=p, lonlat=[float(v) for v in xy2ll(*p)]) for p in app.tour0],
             payload=PAYLOAD, dwell=DWELL, hour=HOUR, wind=app.site_info()["wind"], obstacles=ex, cases={})
    plans = {}
    R["cases"]["none"], plans["none"], _ = case(app, "장애물 없음", [], keep=True)
    R["cases"]["obs"], plans["obs"], m = case(app, "예시 장애물 3종", ex, keep=True)
    out_dir = C.RES / "missions"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "changwon_x650_15h_tour.waypoints").write_text(m["wpl"])
    (out_dir / "changwon_x650_15h_tour.csv").write_text(m["csv"], encoding="utf-8-sig")
    try:
        from pymavlink import mavwp
        Ld = mavwp.MAVWPLoader()
        R["cases"]["obs"]["pymavlink_loaded"] = int(Ld.load(str(out_dir / "changwon_x650_15h_tour.waypoints")))
    except ImportError:
        R["cases"]["obs"]["pymavlink_loaded"] = None
    R["cases"]["unc"] = case(app, "장애물 + 위치 불확실성 (ε = 0.05)", unc, eps=0.05)
    pts = [app.hub0] + list(app.tour0) + [app.hub0]
    R["methods"] = dict(shortest_v10=tour_static(s, d, pts, HOUR, 0, "const"), shortest_vopt=tour_static(s, d, pts, HOUR, 0, "opt"),
                        proposed_static=tour_static(s, d, pts, HOUR, 1, "opt"))
    print("[방법]", {k: None if v is None else (round(v["soc"], 1), round(v["T"]), round(v["L"])) for k, v in R["methods"].items()}, flush=True)
    R["sweep"] = []
    wind = {w["hour"]: w["speed"] for w in R["wind"]}
    for h in range(9, 21):
        a = tour_static(s, d, pts, h, 0, "const")
        b = tour_static(s, d, pts, h, 1, "opt")
        R["sweep"].append(dict(hour=h, wind=wind[h], shortest_v10=a, proposed=b))
        print("[시각]", h, round(wind[h], 1), None if a is None else round(a["soc"], 1), round(b["soc"], 1), flush=True)
    (C.RES / "changwon.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R, app, plans)


if __name__ == "__main__":
    main()
