"""실기체 비행 로그(ArduPilot .bin) 분석: 구간별 시간·에너지, 전력 모델과의 비교, 계획기의 시간 모형 점검.

사용: PYTHONPATH=src python scripts/analyze_flight_log.py <로그.bin> <출력 이름> [--mass KG] [--temp C]
출력: results/flight_<이름>.json, results/figures/F37_flight_<이름>.png
위치는 이륙점 기준 상대 좌표 [m] 로만 남긴다 (위도·경도는 결과에 쓰지 않는다).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import drones as DR  # noqa: E402
from dpo.ardupilot import kinematic_overhead  # noqa: E402
from dpo.energy import RHO0, air_density, power_w  # noqa: E402

MLAT = 111320.0
PARAMS = ["BATT_CAPACITY", "WP_SPD", "WP_SPD_UP", "WP_SPD_DN", "WP_ACC", "EK3_DRAG_BCOEF_X", "EK3_DRAG_BCOEF_Y", "MOT_THST_HOVER",
          "RTL_ALT_M", "RTL_LOIT_TIME", "LAND_SPD_MS", "LAND_ALT_LOW_M", "LAND_ALT_LOW"]


def read(path):
    from pymavlink import mavutil
    m = mavutil.mavlink_connection(str(path), dialect="ardupilotmega")
    B, G, C, A, msgs, cmds, prm, W = [], [], [], [], [], [], {}, []
    while True:
        x = m.recv_match()
        if x is None:
            break
        t = x.get_type()
        if t == "BAT" and getattr(x, "Inst", 0) == 0:
            B.append((x.TimeUS * 1e-6, x.Volt, x.Curr, x.EnrgTot, x.CurrTot, x.RemPct, getattr(x, "Res", float("nan"))))
        elif t == "GPS" and getattr(x, "I", 0) == 0:
            G.append((x.TimeUS * 1e-6, x.Lat, x.Lng, x.Alt, x.Spd, x.GCrs, x.VZ, x.Status))
        elif t == "CTUN":
            C.append((x.TimeUS * 1e-6, x.Alt, x.CRt, x.ThO, x.DAlt))
        elif t == "ATT":
            A.append((x.TimeUS * 1e-6, x.Roll, x.Pitch))
        elif t == "XKF2" and getattr(x, "C", 0) == 0:
            W.append((x.TimeUS * 1e-6, x.VWN, x.VWE))
        elif t == "MSG":
            msgs.append((x.TimeUS * 1e-6, x.Message))
        elif t == "CMD":
            cmds.append(dict(n=int(x.CNum), id=int(x.CId), p1=float(x.Prm1), lat=float(x.Lat), lng=float(x.Lng), alt=float(x.Alt)))
        elif t == "PARM":
            prm[x.Name] = float(x.Value)
    return dict(B=np.array(B), G=np.array(G), C=np.array(C), A=np.array(A), W=np.array(W), msgs=msgs, cmds=cmds, prm=prm)


def phases(L, t0, t1):
    """임무 이벤트(MSG)로 구간을 나눈다: 이륙, 경로점 구간, 복귀(RTL)의 수평 이동·대기·하강."""
    ev = [(t, s) for t, s in L["msgs"] if s.startswith("Mission:")]
    out = []
    for k, (t, s) in enumerate(ev):
        te = ev[k + 1][0] if k + 1 < len(ev) else t1
        name = s.split(":", 1)[1].strip()
        out.append([max(t, t0), min(te, t1), name])
    # RTL 을 다시 나눈다: 수평 복귀 / 홈 위 대기 / 하강
    if out and "RTL" in out[-1][2]:
        a, b, _ = out.pop()
        G = L["G"]
        g = G[(G[:, 0] >= a) & (G[:, 0] <= b)]
        C = L["C"]
        c = C[(C[:, 0] >= a) & (C[:, 0] <= b)]
        t_move = next((t for t, v in zip(g[:, 0], g[:, 4]) if v > 1.0), a)                       # 홈으로 움직이기 시작
        t_slow = next((t for t, v in zip(g[:, 0], g[:, 4]) if t > t_move and v < 0.4), b)        # 홈 위에서 멈춤
        t_desc = next((t for t, cr in zip(c[:, 0], c[:, 2]) if t > t_slow and cr < -0.4), b)   # 하강 시작
        out += [[a, t_slow, "RTL 복귀"], [t_slow, t_desc, "RTL 대기"], [t_desc, b, "RTL 하강·착륙"]]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("name")
    ap.add_argument("--mass", default=None, help="이륙 중량 [kg]. 'hover' 면 측정 호버 전력에 맞는 중량을 쓴다 (모르면 모델의 4.1 kg)")
    ap.add_argument("--temp", type=float, default=15.0, help="외기온 [°C] (공기 밀도)")
    a = ap.parse_args(argv)
    L = read(a.log)
    B, G, C = L["B"], L["G"], L["C"]
    d = DR.x650()
    mass = d.m_empty if a.mass in (None, "hover") else float(a.mass)
    home = L["cmds"][0] if L["cmds"] else dict(lat=G[0, 1], lng=G[0, 2], alt=G[0, 3])
    rho = air_density(home["alt"], a.temp)
    # 비행 구간: 이륙 명령 ~ 전류가 마지막으로 3 A 를 넘은 시각
    t_to = next(t for t, s in L["msgs"] if s.startswith("Mission: 1"))
    flying = B[B[:, 2] > 3.0]
    t_end = float(flying[-1, 0])
    t_start = float(flying[flying[:, 0] >= t_to - 1][0, 0])
    sel = (B[:, 0] >= t_start) & (B[:, 0] <= t_end)
    b = B[sel]
    P = b[:, 1] * b[:, 2]
    E_meas = float((b[-1, 3] - b[0, 3]))                                                     # EnrgTot [Wh]
    E_int = float(np.trapezoid(P, b[:, 0]) / 3600.0)
    mah = float(b[-1, 4] - b[0, 4])
    # 속도: GPS 수평 속도, 상승률: CTUN.CRt
    vh = np.interp(b[:, 0], G[:, 0], G[:, 4])
    vz = np.interp(b[:, 0], C[:, 0], C[:, 2])
    alt = np.interp(b[:, 0], C[:, 0], C[:, 1])
    prm = d.params()

    def model_p(m):
        return np.array([power_w(float(v), float(z), m, rho, prm) for v, z in zip(vh, vz)])

    Pm = model_p(mass)
    Pm_nom, E_nom = Pm, float(np.trapezoid(Pm, b[:, 0]) / 3600.0)                            # 제원 중량(4.1 kg)의 모델
    E_model = E_nom
    # 호버 구간: 수평 0.5 m/s 미만, 수직 0.3 m/s 미만, 지면 위 5 m 이상, 2 s 이상 이어짐
    hov = (vh < 0.5) & (np.abs(vz) < 0.3) & (alt > 5.0)
    P_hover = float(np.median(P[hov])) if hov.sum() >= 10 else None
    P_hover_model = float(power_w(0.0, 0.0, mass, rho, prm))
    m_equiv = None
    if P_hover:
        lo, hi = 1.0, 12.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if power_w(0.0, 0.0, mid, rho, prm) < P_hover else (lo, mid)
        m_equiv = 0.5 * (lo + hi)
    if a.mass == "hover" and m_equiv:
        mass = m_equiv
        Pm = model_p(mass)
        E_model = float(np.trapezoid(Pm, b[:, 0]) / 3600.0)
        P_hover_model = float(power_w(0.0, 0.0, mass, rho, prm))
    # 구간별
    rows = []
    for ta, tb, name in phases(L, t_start, t_end):
        s = (b[:, 0] >= ta) & (b[:, 0] < tb)
        if s.sum() < 2:
            continue
        g = G[(G[:, 0] >= ta) & (G[:, 0] <= tb)]
        x = (g[:, 2] - home["lng"]) * MLAT * math.cos(math.radians(home["lat"]))
        y = (g[:, 1] - home["lat"]) * MLAT
        dist = float(np.sum(np.hypot(np.diff(x), np.diff(y)))) if len(g) > 1 else 0.0
        e_m = float(np.trapezoid(P[s], b[s, 0]) / 3600.0)
        e_p = float(np.trapezoid(Pm[s], b[s, 0]) / 3600.0)
        rows.append(dict(name=name, t=float(tb - ta), dist_m=dist, v_mean=dist / max(tb - ta, 1e-9), alt_from=float(alt[s][0]), alt_to=float(alt[s][-1]),
                         p_meas=float(P[s].mean()), p_model=float(Pm[s].mean()), e_meas_wh=e_m, e_model_wh=e_p, ratio=e_m / max(e_p, 1e-9)))
    # 계획기의 시간 모형: 경로점 구간 (이륙~RTL 전) 을 계획 속도와 가감속 보정으로 예측
    wp = [c for c in L["cmds"] if c["id"] == 16 and c["n"] > 0]
    cx = lambda c: ((c["lng"] - home["lng"]) * MLAT * math.cos(math.radians(home["lat"])), (c["lat"] - home["lat"]) * MLAT, c["alt"])  # noqa: E731
    v_wp, vup, vdn = L["prm"].get("WP_SPD", 10.0), L["prm"].get("WP_SPD_UP", 2.5), L["prm"].get("WP_SPD_DN", 1.5)
    tk = next(c for c in L["cmds"] if c["id"] == 22)["alt"]
    pts = [(0.0, 0.0, tk)] + [cx(c) for c in wp]
    segs, t_plan = [], 0.0
    for p, q in zip(pts[:-1], pts[1:]):
        h, dz = math.hypot(q[0] - p[0], q[1] - p[1]), q[2] - p[2]
        t_plan += max(h / v_wp, abs(dz) / (vup if dz > 0 else vdn))
        segs.append((*p, *q, v_wp if h > 1e-6 else None))
    t_over = kinematic_overhead(segs)
    leg_rows = [r for r in rows if r["name"].endswith("WP")]
    t_actual = float(sum(r["t"] for r in leg_rows))
    out = dict(
        log=Path(a.log).name, firmware=next((s for _, s in L["msgs"] if s.startswith("ArduCopter")), None),
        params={k: L["prm"][k] for k in PARAMS if k in L["prm"]},
        flight=dict(t_s=t_end - t_start, energy_wh=E_meas, energy_wh_integral=E_int, mah=mah, soc_pct_of_222wh=100 * E_meas / 222.0,
                    v_start=float(b[0, 1]), v_end=float(b[-1, 1]), curr_mean=float(b[:, 2].mean()), p_mean=float(P.mean()),
                    alt_max=float(alt.max()), vh_max=float(vh.max()), r_int=float(np.nanmedian(b[:, 6]))),
        model=dict(mass_kg=mass, rho=rho, temp_c=a.temp, energy_wh=E_model, ratio=E_meas / E_model, p_hover_model=P_hover_model,
                   mass_nominal_kg=d.m_empty, energy_wh_nominal=E_nom, ratio_nominal=E_meas / E_nom,
                   p_hover_model_nominal=float(power_w(0.0, 0.0, d.m_empty, rho, prm)),
                   p_hover_meas=P_hover, n_hover=int(hov.sum()), mass_equiv_kg=m_equiv),
        wind=dict(available=bool(L["prm"].get("EK3_DRAG_BCOEF_X", 0) > 0), max_est=float(np.hypot(L["W"][:, 1], L["W"][:, 2]).max()) if len(L["W"]) else None),
        phases=rows,
        timing=dict(wp_speed=v_wp, t_actual_wp_legs=t_actual, t_plan_no_overhead=t_plan, t_overhead_model=t_over, t_plan_with_overhead=t_plan + t_over,
                    n_legs=len(segs)),
    )
    res = ROOT / "results" / f"flight_{a.name}.json"
    res.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    figure(out, b, P, Pm, vh, alt, L, home, t_start, ROOT / "results" / "figures" / f"F37_flight_{a.name}.png", Pm_nom)
    print(json.dumps({k: out[k] for k in ("flight", "model", "timing", "wind")}, ensure_ascii=False, indent=1))
    for r in rows:
        print(f"  {r['name']:<14} {r['t']:6.1f} s  {r['dist_m']:6.1f} m  {r['alt_from']:5.1f}→{r['alt_to']:5.1f} m  P {r['p_meas']:6.0f} / 모델 {r['p_model']:6.0f} W  "
              f"E {r['e_meas_wh']:5.2f} / {r['e_model_wh']:5.2f} Wh  ×{r['ratio']:.2f}")
    return out


def figure(o, b, P, Pm, vh, alt, L, home, t0, path, Pm_nom=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if Path(f).exists():
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    t = b[:, 0] - t0
    fig = plt.figure(figsize=(12.5, 7.2), dpi=170)
    gs = fig.add_gridspec(2, 3, width_ratios=[1.55, 1.55, 1.1], hspace=0.38, wspace=0.32)
    ax = fig.add_subplot(gs[0, :2])
    cols = ["#EEF3F7", "#FFFFFF"]
    for k, r in enumerate(o["phases"]):
        pass
    ph = phases(L, t0 + 0, t0 + t[-1])
    for k, (a, bb, name) in enumerate(ph):
        ax.axvspan(a - t0, bb - t0, color=cols[k % 2], zorder=0)
        ax.text((a + bb) / 2 - t0, 1.02, name.replace("RTL ", "RTL\n"), transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=7.6, color="#5B6770")
    ax.plot(t, P, color="#16202A", lw=1.2, label="측정 전력 (전압 × 전류)")
    if Pm_nom is not None and abs(o["model"]["mass_kg"] - o["model"]["mass_nominal_kg"]) > 1e-6:
        ax.plot(t, Pm_nom, color="#8A8F98", lw=1.3, ls="--", label=f"모델 – 제원 중량 {o['model']['mass_nominal_kg']:.1f} kg (합계 {o['model']['energy_wh_nominal']:.1f} Wh)")
        lab = f"모델 – 호버 전력에 맞춘 중량 {o['model']['mass_kg']:.2f} kg (합계 {o['model']['energy_wh']:.1f} Wh)"
    else:
        lab = f"모델 전력 (X650 추정, {o['model']['mass_kg']:.1f} kg)"
    ax.plot(t, Pm, color="#E46C0A", lw=1.6, label=lab)
    ax.set_ylabel("전력 [W]")
    ax.set_xlim(0, t[-1])
    ax.legend(loc="lower center", fontsize=8.3, frameon=True, framealpha=0.9, edgecolor="none", ncol=1)
    ax.set_ylim(bottom=0)
    ax.set_title("(a) 측정 전력과 모델 전력", loc="left", fontsize=10.5, pad=26)
    ax2 = fig.add_subplot(gs[1, :2], sharex=ax)
    for k, (a, bb, name) in enumerate(ph):
        ax2.axvspan(a - t0, bb - t0, color=cols[k % 2], zorder=0)
    ax2.plot(t, alt, color="#1B6CA8", lw=1.6, label="고도 (이륙점 기준) [m]")
    ax2.set_ylabel("고도 [m]", color="#1B6CA8")
    ax3 = ax2.twinx()
    ax3.plot(t, vh, color="#2E9E5B", lw=1.2, label="수평 속도 [m/s]")
    ax3.set_ylabel("수평 속도 [m/s]", color="#2E9E5B")
    ax3.spines["right"].set_visible(True)
    ax2.set_xlabel("이륙 후 시간 [s]")
    ax2.set_title("(b) 고도와 수평 속도", loc="left", fontsize=10.5)
    # (c) 구간별 에너지
    ax4 = fig.add_subplot(gs[0, 2])
    names = [r["name"].replace("RTL ", "RTL\n") for r in o["phases"]]
    y = np.arange(len(names))
    ax4.barh(y + 0.2, [r["e_meas_wh"] for r in o["phases"]], 0.38, color="#16202A", label="측정")
    ax4.barh(y - 0.2, [r["e_model_wh"] for r in o["phases"]], 0.38, color="#E46C0A", label="모델")
    ax4.set_yticks(y, names, fontsize=8)
    ax4.invert_yaxis()
    ax4.set_xlabel("에너지 [Wh]", labelpad=1)
    ax4.legend(fontsize=8.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2)
    ax4.set_title(f"(c) 구간별 에너지 (합계 측정 {o['flight']['energy_wh']:.1f} / 모델 {o['model']['energy_wh']:.1f} Wh)", loc="left", fontsize=9.5)
    # (d) 평면 궤적 (이륙점 기준)
    ax5 = fig.add_subplot(gs[1, 2])
    G = L["G"]
    g = G[(G[:, 0] >= t0) & (G[:, 0] <= t0 + t[-1])]
    k = math.cos(math.radians(home["lat"]))
    ax5.plot((g[:, 2] - home["lng"]) * MLAT * k, (g[:, 1] - home["lat"]) * MLAT, color="#E46C0A", lw=1.6, label="실제 궤적")
    wp = [c for c in L["cmds"] if c["id"] == 16 and c["n"] > 0]
    xs = [0] + [(c["lng"] - home["lng"]) * MLAT * k for c in wp] + [0]
    ys = [0] + [(c["lat"] - home["lat"]) * MLAT for c in wp] + [0]
    ax5.plot(xs, ys, "--o", color="#1B6CA8", lw=1, ms=4, label="계획 경로점")
    for n, (xx, yy) in enumerate(zip(xs[1:-1], ys[1:-1]), 2):
        ax5.annotate(f"WP{n}", (xx, yy), textcoords="offset points", xytext=(4, 4), fontsize=8, color="#1B6CA8")
    ax5.plot(0, 0, "s", color="#16202A", ms=6)
    ax5.annotate("이륙·착륙", (0, 0), textcoords="offset points", xytext=(7, 2), fontsize=8)
    ax5.set_aspect("equal")
    ax5.set_xlabel("동쪽 [m]")
    ax5.set_ylabel("북쪽 [m]")
    ax5.legend(fontsize=8, frameon=False, loc="center left", bbox_to_anchor=(1.12, 0.5))
    ax5.set_title("(d) 평면 궤적 (이륙점 기준)", loc="left", fontsize=10.5)
    fig.suptitle(f"실기체 시험비행 로그 분석 – X650, {o['firmware'] or ''}, 비행 {o['flight']['t_s']:.0f} s", fontsize=12, x=0.01, ha="left")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
