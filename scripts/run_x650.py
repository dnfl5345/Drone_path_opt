"""실증 대상 기체 Holybro X650 (ArduPilot) 결과 → results/x650.json, 그림 F26, results/missions/*.waypoints

(1) 전력 곡선과 호버 시간 (제원·제조사 호버 시간으로 추정한 모델, M100 실측 보정 모델과 비교)
(2) 같은 임무(거점↔배송지 2.4 km 왕복, 15시)에서 방법별·탑재중량별 SOC
(3) 출발 시각 09–20시 임무 가능 횟수
(4) 모델 가정(호버 시간·항력면적·형상동력)을 바꿨을 때 SOC 가 얼마나 달라지는가
(5) ArduPilot 미션 파일 내보내기 예 (QGC WPL 110) 와 pymavlink 로더 확인

사용: PYTHONPATH=src python scripts/run_x650.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, "scripts")
from run_experiments import sites, HUB, DST, KST, METHODS, T_MAIN  # noqa: E402
from dpo import config as C, drones as DR  # noqa: E402
from dpo.ardupilot import leg_items, to_csv, to_wpl  # noqa: E402
from dpo.energy import RHO0, power_w, soc_drop  # noqa: E402
from dpo.mission import Mission, run_round_trip  # noqa: E402
from dpo.planner import Planner  # noqa: E402

PAY = [0.0, 1.0, 2.2]          # 탑재중량 [kg] (2.2 kg = 최대이륙중량 6.3 kg)
PAY_MAIN = 1.0


def curves(d, payloads):
    v = np.arange(0.0, d.va_max + 0.01, 0.5)
    prm = d.params()
    out = dict(v=v.tolist(), P={}, hover_W={}, hover_min={}, best_range={})
    loss = soc_drop(1.0, d) * d.battery_wh * 36.0             # soc_drop 의 손실 계수
    for p in payloads:
        m = d.m_empty + p
        P = np.array([power_w(x, 0.0, m, RHO0, prm) for x in v])
        out["P"][str(p)] = P.tolist()
        out["hover_W"][str(p)] = float(P[0])
        out["hover_min"][str(p)] = float(d.battery_wh * 60.0 / (P[0] * loss))
        k = 1 + int(np.argmin(P[1:] / v[1:]))
        out["best_range"][str(p)] = dict(v=float(v[k]), J_per_m=float(P[k] / v[k]),
                                         km_full=float(d.battery_wh * 3600 / loss / (P[k] / v[k]) / 1000))
    return out


def rt_row(rt):
    if rt is None:
        return None
    return dict(soc=rt["soc_used"], soc_out=rt["soc_out"], soc_back=rt["soc_back"], T=rt["t_total"], L=rt["L"],
                feasible=bool(rt["feasible"]))


def missions(fc, tr, d):
    P = Planner(fc, d)
    res = dict(by_method={}, by_payload={}, sweep=[])
    for k, cfg in METHODS.items():
        kw = {a: b for a, b in cfg.items() if a != "label"}
        rt = run_round_trip(P, Mission(HUB, DST, PAY_MAIN, "delivery", T_MAIN), site_truth=tr, **kw)
        res["by_method"][k] = dict(label=cfg["label"], **(rt_row(rt) or dict(soc=None)))
        print("[method]", cfg["label"], None if rt is None else round(rt["soc_used"], 1), flush=True)
    for p in PAY:
        row = {}
        for k in ("A_shortest_v10", "D_WE_LTstar"):
            kw = {a: b for a, b in METHODS[k].items() if a != "label"}
            row[k] = rt_row(run_round_trip(P, Mission(HUB, DST, p, "delivery", T_MAIN), site_truth=tr, **kw))
        res["by_payload"][str(p)] = row
        print("[payload]", p, {k: None if v is None else round(v["soc"], 1) for k, v in row.items()}, flush=True)
    for h in range(0, 12):
        row = dict(hour=h + KST)
        for k in ("A_shortest_v10", "D_WE_LTstar"):
            kw = {a: b for a, b in METHODS[k].items() if a != "label"}
            row[k] = rt_row(run_round_trip(P, Mission(HUB, DST, PAY_MAIN, "delivery", h * 3600.0), site_truth=fc, **kw))
        res["sweep"].append(row)
        print("[sweep]", row["hour"], {k: None if row[k] is None else round(row[k]["soc"], 1) for k in row if k != "hour"},
              flush=True)
    for k in ("A_shortest_v10", "D_WE_LTstar"):
        res[f"{k}_feasible"] = int(sum(1 for r in res["sweep"] if r[k] is not None and r[k]["feasible"]))
    return res, P


def sensitivity(fc, tr):
    """모델 가정을 하나씩 바꿔 같은 임무(15시, 1 kg, 제안 방법)의 SOC 를 다시 계산."""
    cases = {"기준 (호버 30분, CdA 0.08, P0 30 W)": {},
             "호버 25분": dict(hover_min=25.0), "호버 35분": dict(hover_min=35.0),
             "항력면적 0.06 m²": dict(cda=0.06), "항력면적 0.12 m²": dict(cda=0.12),
             "형상동력 20 W": dict(p0=20.0), "형상동력 45 W": dict(p0=45.0),
             "대지속도 상한 10 m/s": dict(vg_max=10.0), "대지속도 상한 20 m/s": dict(vg_max=20.0, va_max=17.0)}
    out = {}
    kw = {a: b for a, b in METHODS["D_WE_LTstar"].items() if a != "label"}
    for name, over in cases.items():
        d = DR.x650(**over)
        rt = run_round_trip(Planner(fc, d), Mission(HUB, DST, PAY_MAIN, "delivery", T_MAIN), site_truth=tr, **kw)
        out[name] = dict(over=over, eta=d.eta, **(rt_row(rt) or dict(soc=None)))
        print("[sens]", name, None if rt is None else round(rt["soc_used"], 1), flush=True)
    return out


def export_example(fc, d, P):
    """15시·1 kg 배송 왕복 계획을 ArduPilot 미션으로 내보내고 pymavlink 로더로 다시 읽는다."""
    out_dir = C.RES / "missions"
    out_dir.mkdir(exist_ok=True)
    kw = {a: b for a, b in METHODS["D_WE_LTstar"].items() if a != "label"}
    rt = run_round_trip(P, Mission(HUB, DST, PAY_MAIN, "delivery", T_MAIN), site_truth=fc, **kw)
    info, e_before = {}, 0.0
    m_out, m_back = d.m_empty + PAY_MAIN, d.m_empty
    t0 = T_MAIN
    for name, leg, m in (("out", rt["out"], m_out), ("back", rt["back"], m_back)):
        r = leg["plan"]
        M = leg_items(fc, d, r["path"], r["wait_before"], t0 + leg["t_takeoff"], m, e_before=e_before,
                      e_takeoff=leg["e_takeoff"], t_takeoff=leg["t_takeoff"], t_land=leg["t_land"], e_land=leg["e_land"],
                      t_ref=T_MAIN)
        f = out_dir / f"x650_15h_{name}.waypoints"
        f.write_text(to_wpl(M["items"]))
        (out_dir / f"x650_15h_{name}.csv").write_text(to_csv(M["rows"]), encoding="utf-8-sig")
        s = dict(M["summary"])
        s["sim_soc_end"] = float(100 - soc_drop(e_before + leg["e_total"], d))
        s["sim_duration_s"] = float(leg["t_end"] - t0)
        try:
            from pymavlink import mavwp
            L = mavwp.MAVWPLoader()
            s["pymavlink_loaded"] = int(L.load(str(f)))
            s["pymavlink_commands"] = sorted({int(L.wp(i).command) for i in range(L.count())})
        except ImportError:
            s["pymavlink_loaded"] = None
        info[name] = s
        e_before += leg["e_total"]
        t0 = leg["t_end"] + 30.0
        print("[mission]", name, {k: s[k] for k in ("n_items", "n_plan_nodes", "n_waypoints", "n_speed_changes", "soc_end",
                                                   "sim_soc_end", "duration_s", "sim_duration_s", "pymavlink_loaded")}, flush=True)
    return info


def figure(R):
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
    GRAY, ORG, BLUE, NAVY = "#9E9E9E", "#E07B39", "#1B6CA8", "#0B3954"
    fig, axs = plt.subplots(1, 3, figsize=(17.5, 4.6), gridspec_kw=dict(width_ratios=[1.05, 1.0, 1.0], wspace=0.5))

    ax = axs[0]
    X, M = R["curves"]["x650"], R["curves"]["m100"]
    for p, c, lw in (("0.0", "#7FB2D9", 1.6), ("1.0", BLUE, 2.4), ("2.2", NAVY, 1.6)):
        ax.plot(X["v"], X["P"][p], color=c, lw=lw, label=f"X650 탑재 {float(p):g} kg")
    ax.plot(M["v"], M["P"]["0.5"], color=GRAY, lw=1.6, ls="--", label="M100 탑재 0.5 kg (실측 보정)")
    ax.scatter([0], [X["hover_W"]["0.0"]], color="#7FB2D9", zorder=5, s=28)
    ax.annotate(f"무탑재 호버 {X['hover_W']['0.0']:.0f} W\n= 30분 (제조사 값에 맞춤)", xy=(0, X["hover_W"]["0.0"]),
                xytext=(0.6, X["hover_W"]["0.0"] - 150), fontsize=9, color="#3D6E99", arrowprops=dict(arrowstyle="-", color="#3D6E99", lw=0.8))
    ax.set_xlabel("대기속도 [m/s]"); ax.set_ylabel("소비전력 [W]")
    ax.set_ylim(100, None)
    ax.set_title("(a) X650 전력 곡선 (추정 모델)")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")

    ax = axs[1]
    bp = R["missions"]["by_payload"]
    x = np.arange(len(PAY))
    for k, (key, c, lb) in enumerate((("A_shortest_v10", GRAY, "최단 경로 + 정속 10 m/s"), ("D_WE_LTstar", ORG, "제안 WE-LT*"))):
        v = [bp[str(p)][key]["soc"] if bp[str(p)][key] else np.nan for p in PAY]
        b = ax.bar(x + (k - 0.5) * 0.38, v, 0.36, color=c, label=lb)
        for r, val in zip(b, v):
            ax.text(r.get_x() + r.get_width() / 2, val + 1.2, f"{val:.1f}", ha="center", fontsize=9.5,
                    fontweight="bold" if k else "normal")
    ax.axhline(80, color="#D7263D", ls="--", lw=1)
    ax.text(len(PAY) - 0.52, 81.5, "예비 20 % 한계", color="#D7263D", fontsize=8.5, ha="right")
    ax.set_xticks(x); ax.set_xticklabels([f"탑재 {p:g} kg" for p in PAY])
    ax.set_ylabel("왕복 SOC 소모 [%p]"); ax.set_ylim(0, 100)
    ax.set_title("(b) 배송 왕복 2.4 km, 15시 출발")
    ax.legend(frameon=False, fontsize=9, loc="upper left")

    ax = axs[2]
    S = R["sensitivity"]
    names = list(S)
    base = S[names[0]]["soc"]
    vals = [S[n]["soc"] for n in names]
    y = np.arange(len(names))
    ax.barh(y, vals, color=[ORG] + [BLUE] * (len(names) - 1), height=0.62)
    for yy, v in zip(y, vals):
        ax.text(v + 0.6, yy, f"{v:.1f}" + ("" if yy == 0 else f"  ({v - base:+.1f})"), va="center", fontsize=9)
    ax.set_yticks(y); ax.set_yticklabels(names, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals) * 1.25)
    ax.set_xlabel("왕복 SOC 소모 [%p] (제안 방법, 탑재 1 kg)")
    ax.set_title("(c) 모델 가정에 따른 차이")
    fig.savefig(C.FIG / "F26_x650_target.png")
    plt.close(fig)


def main():
    g, fc, tr = sites()
    d = DR.x650()
    R = dict(spec=DR.X650_SPEC, drone=d.to_dict(), payload_max=DR.PAYLOAD_MAX["x650"],
             curves=dict(x650=curves(d, PAY), m100=curves(DR.m100(), [0.5])))
    print("η", round(d.eta, 3), "호버", {k: round(v) for k, v in R["curves"]["x650"]["hover_W"].items()},
          {k: round(v, 1) for k, v in R["curves"]["x650"]["hover_min"].items()}, R["curves"]["x650"]["best_range"], flush=True)
    R["missions"], P = missions(fc, tr, d)
    R["sensitivity"] = sensitivity(fc, tr)
    R["mission_export"] = export_example(fc, d, P)
    (C.RES / "x650.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fig":
        figure(json.loads((C.RES / "x650.json").read_text()))
    else:
        main()
