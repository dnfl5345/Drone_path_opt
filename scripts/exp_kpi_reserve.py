"""E17: 공식 평가항목 '외란을 고려한 배터리 안전여유 확보율' PoC 시험 (2026. 7. 28. 계획서 표2)

평가방법(계획서): 승인된 임무 종료 후 정상 복귀/착륙 여부와 종료 시 Reserve 가 정책 마진 이상인지 확인하여 성공률을 산정.

  기체      DJI Matrice 4T (공식 사양으로 맞춘 전력 모델), 탑재 0.2 kg, 배송 왕복 (가서 내려놓고 빈 몸으로 복귀)
  정책 마진 예비 SoC 20 %
  승인      플랫폼의 출발 판정과 같은 규칙 (webapp/server.py confidence): 계획에 쓴 예보(00Z)로
            구간별 SoC 소모의 95 % 분위수를 구해 더한 값 U95 가, 출발 SoC − 20 % 이하이면 승인(go).
            명목 소모는 배포하는 엔진이 예보 그대로 날았을 때의 값(U_nom)을 쓰고, 95 % 여유 Δ95 = Σ(q95 − 명목)
            는 플랫폼과 같은 mission_confidence(σ = 예보 오차 모형, 예보 발표 후 6 h, 그 달의 계절)로 구한다.
            가장 엄한 조건으로, 승인이 겨우 나는 출발 SoC  s0 = 20 + U_nom + Δ95 에서 비행시킨다
            (배터리가 이보다 많으면 성공하기가 더 쉽다). 비교로 명목 승인(s0 = 20 + U_nom, 여유 없음)도 비행.
  비행      배포하는 실시간 엔진(dpo.live.LiveEngine, 안전장치 켬·속도 갱신 30 s)으로 이륙–순항–착륙–하역–복귀.
            출발 SoC 는 예비를 120 − s0 로 두어 나타낸다 (쓸 수 있는 에너지 = 배터리 × (s0 − 20) %, 같은 뜻).
  날짜      GFS 표본 2025-11 … 2026-10 의 5·15·25일 (지역당 36일, 사계절), 15시 출발, 지역·날짜당 경로 3개
  외란      real   실제 바람 = 6 시간 뒤 발표 예보(06Z)  ← 실제 예보 오차 (평가항목 산정)
            obs    real + 경로를 가로지르는 이동 장애물 (회피·대기 비용)  (평가항목 산정)
            rot30  풍향을 30° 돌림,  spd1.3  풍속 1.3 배          ← 오차 모형 밖 스트레스 (따로 보고)
  성공      임무 완료(정상 복귀·착륙) 이고 침범 0 이며 종료 SoC = s0 − 소모 ≥ 20 %.

사용: PYTHONPATH=src python scripts/exp_kpi_reserve.py run pangyo
      PYTHONPATH=src python scripts/exp_kpi_reserve.py analyze
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
from exp_live_closed_loop import crossing_obstacle  # noqa: E402
from exp_reliability import days_available  # noqa: E402
from exp_superiority import _load_day, od_pairs  # noqa: E402

DKEY = "m4t"
PAYLOAD = 0.2
HOUR = 15
RESERVE = 20.0
LEVEL = 95
AGE_H = 6.0
N_PAIRS = int(os.environ.get("E17_PAIRS", 3))
CASES = [("real", "06z"), ("obs", "06z"), ("rot30", dict(rot=30.0)), ("spd1.3", dict(scale=1.3))]
KPI_CASES = ("real", "obs")
KST = 9


def leg_plan(P, fc, d, a, b, t0, m):
    """E16 과 같은 규칙으로 한 구간을 계획 (에너지 탐색과 최단 중 예보 기준 에너지가 적은 쪽)."""
    from dpo.mission import make_flyable
    from dpo.planner import simulate, vertical_energy
    st, gl = fc.lowest_free_k(*a), fc.lowest_free_k(*b)
    e_to, t_to = vertical_energy(fc, d, a[0], a[1], st[0], t0, m, up=True)
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
        return None
    k = min(cands, key=lambda c: float(cands[c][2]["e"][-1]))
    path, waits, nom = cands[k]
    e_ld, t_ld = vertical_energy(fc, d, b[0], b[1], gl[0], nom["t"][-1], m, up=False)
    return dict(path=path, waits=waits, t0=t0 + t_to, e_fixed=e_to + e_ld, t_end=float(nom["t"][-1] + t_ld))


def worker(args):
    site_key, ymd = args
    from dpo.live import LiveEngine
    from dpo.reliability import WindErrorModel, mission_confidence
    fc, tr, d, P = _load_day(site_key, ymd, DKEY)
    sigma = float(WindErrorModel.load().sigma(AGE_H, month=int(ymd[4:6])))
    pairs = od_pairs(fc, N_PAIRS)
    E = LiveEngine(fc, d, sync=True)
    rows, tb = [], time.time()

    def fly(a, b, wt, s0, obs=()):
        cfg = dict(hub=a, dst=b, dep_hour=HOUR, payload=PAYLOAD, kind="delivery", round_trip=True, refine=False, eps=0.05,
                   shape="gauss", reserve=120.0 - s0, wind_truth=None if wt in (None, "06z") else wt,
                   safeguard=True, speed_update=30)
        E.start(cfg, list(obs), truth=tr if wt == "06z" else None)
        s = E.run_sync(t_max=6000)
        used = float(s["soc_used"])
        denied = s["state"] == "aborted" and float(s["t"]) < 1.0
        ok = s["state"] == "done" and int(s["collisions"]) == 0 and s0 - used >= RESERVE - 1e-6
        alerts = [e["msg"] for e in E.events if e["kind"] == "alert"]
        return dict(state=s["state"], denied=bool(denied), used=used, end=s0 - used, ok=bool(ok), T=float(s["t"]),
                    why=alerts[-1][:60] if alerts and not ok else None,
                    col=int(s["collisions"]), upd=int(s["speed_updates"]),
                    min_sep=None if not math.isfinite(s["min_sep"]) else float(s["min_sep"]))

    m_out, m_back = d.m_empty + PAYLOAD, d.m_empty
    t_dep = (HOUR - KST) * 3600.0
    for q, (a, b) in enumerate(pairs):
        lo = leg_plan(P, fc, d, a, b, t_dep, m_out)
        lb = None if lo is None else leg_plan(P, fc, d, b, a, lo["t_end"] + 20.0, m_back)
        row = dict(site=site_key, drone=DKEY, day=ymd, pair=q, dist=float(math.hypot(a[0] - b[0], a[1] - b[1])), sigma=sigma)
        if lb is None:
            row["plan"] = None
            rows.append(row)
            continue
        nom, q95 = 0.0, 0.0
        for L, m in ((lo, m_out), (lb, m_back)):
            c = mission_confidence(fc, d, L["path"], L["waits"], L["t0"], m, sigma, n=200, seed=17 + q, adapt=False,
                                   e_fixed=L["e_fixed"], reserve=RESERVE)
            nom += c["nominal"]["soc_used"]
            q95 += c["soc_used_q"][LEVEL]
        row["plan"] = dict(nominal=nom, q95=q95, margin=q95 - nom)
        if not math.isfinite(q95):
            rows.append(row)
            continue
        ref = fly(a, b, None, 100.0)                  # 엔진이 예보 그대로 날 때의 명목 소모
        row["engine_nominal"] = ref["used"]
        if ref["state"] != "done":
            row["s0"] = None
            rows.append(row)
            continue
        s_conf = min(100.0, math.ceil((RESERVE + ref["used"] + (q95 - nom)) * 10) / 10)
        s_nom = min(100.0, math.ceil((RESERVE + ref["used"]) * 10) / 10)
        row["s0"] = dict(conf=s_conf, nom=s_nom)
        ob = crossing_obstacle(a, b, d.vg_max)
        for cname, wt in CASES:
            obs = [ob] if cname == "obs" else ()
            row[cname] = dict(conf=fly(a, b, wt, s_conf, obs), nom=fly(a, b, wt, s_nom, obs))
        rows.append(row)
    print(f"[E17 {site_key} {ymd}] {len(rows)}건, {time.time() - tb:.0f} s", flush=True)
    return rows


def run(site_key):
    from dpo import config as C
    days = os.environ["E17_DAYS"].split(",") if os.environ.get("E17_DAYS") else days_available(site_key)
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(worker, [(site_key, d_) for d_ in days]))
    rows = [r for day in res for r in day]
    (C.RES / f"E17_{site_key}_{DKEY}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows))


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def analyze():
    from dpo import config as C
    rows = [r for f in sorted(C.RES.glob(f"E17_*_{DKEY}.json")) for r in json.loads(f.read_text(encoding="utf-8"))]
    ok_rows = [r for r in rows if r.get("s0")]
    out = dict(n_missions=len(rows), n_planned=len(ok_rows), reserve=RESERVE, level=LEVEL, age_h=AGE_H, cases={},
               plan=dict(nominal=float(np.mean([r["engine_nominal"] for r in ok_rows])),
                         nominal_rng=[float(min(r["engine_nominal"] for r in ok_rows)), float(max(r["engine_nominal"] for r in ok_rows))],
                         plan_vs_engine=float(np.mean([r["engine_nominal"] - r["plan"]["nominal"] for r in ok_rows])),
                         margin=float(np.mean([r["plan"]["margin"] for r in ok_rows])),
                         margin_rng=[float(min(r["plan"]["margin"] for r in ok_rows)), float(max(r["plan"]["margin"] for r in ok_rows))],
                         s0_conf=[float(min(r["s0"]["conf"] for r in ok_rows)), float(max(r["s0"]["conf"] for r in ok_rows))],
                         dist=[float(min(r["dist"] for r in ok_rows)), float(max(r["dist"] for r in ok_rows))]),
               sites=sorted({r["site"] for r in rows}), days=sorted({r["day"] for r in rows}))
    for cname, _ in CASES:
        e = {}
        for pol in ("conf", "nom"):
            fl = [r[cname][pol] for r in ok_rows]
            appr = [x for x in fl if not x["denied"]]
            k = sum(x["ok"] for x in appr)
            fails = [x for x in appr if not x["ok"]]
            e[pol] = dict(flights=len(fl), approved=len(appr), success=k, rate=k / len(appr) if appr else None,
                          ci=wilson(k, len(appr)), emerg=sum(x["state"] == "aborted" and "비상" in (x["why"] or "") for x in appr),
                          hold=sum(x["state"] == "aborted" and "비상" not in (x["why"] or "") for x in appr),
                          below=sum(x["state"] == "done" and x["end"] < RESERVE - 1e-6 for x in appr),
                          col=sum(x["col"] > 0 for x in appr),
                          end_min=float(min(x["end"] for x in appr)) if appr else None,
                          end_mean=float(np.mean([x["end"] for x in appr])) if appr else None,
                          fail_cases=[f"{r['site']}/{r['day']}/p{r['pair']}" for r in ok_rows if not r[cname][pol]["denied"] and not r[cname][pol]["ok"]])
        out["cases"][cname] = e
    for pol in ("conf", "nom"):                     # 실제 예보 오차(real, obs) 묶음과 스트레스 포함 전체
        for key, names in (("realistic", ("real", "obs")), ("all_dist", ("real", "obs", "rot30", "spd1.3"))):
            k = sum(out["cases"][c][pol]["success"] for c in names)
            n = sum(out["cases"][c][pol]["approved"] for c in names)
            out.setdefault(key, {})[pol] = dict(success=k, approved=n, rate=k / n if n else None, ci=wilson(k, n))
    (C.RES / "E17_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"임무 {out['n_planned']}/{out['n_missions']}  명목 소모 평균 {out['plan']['nominal']:.1f}%p  95 % 여유 평균 {out['plan']['margin']:.2f}%p "
          f"({out['plan']['margin_rng'][0]:.2f}–{out['plan']['margin_rng'][1]:.2f})  승인 경계 s0 {out['plan']['s0_conf']}")
    for c, e in out["cases"].items():
        print(f"  {c:7s} 95%승인 {e['conf']['success']}/{e['conf']['approved']} ({100 * (e['conf']['rate'] or 0):.1f}%) 최저 종료 {e['conf']['end_min']:.1f}%"
              f" | 명목승인 {e['nom']['success']}/{e['nom']['approved']} ({100 * (e['nom']['rate'] or 0):.1f}%) 비상 {e['nom']['emerg']}")
    for key in ("realistic", "all_dist"):
        print(key, {p: f"{v['success']}/{v['approved']} = {100 * v['rate']:.1f}% CI {100 * v['ci'][0]:.1f}–{100 * v['ci'][1]:.1f}" for p, v in out[key].items()})
    fig(out)
    return out


def fig(S):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False,
                         "axes.spines.top": False, "axes.spines.right": False})
    names = [c for c, _ in CASES]
    lab = {"none": "예보대로\n(점검)", "real": "실제 예보\n오차", "rot30": "풍향\n30° 회전", "rot60": "풍향\n60° 회전",
           "spd1.3": "풍속\n1.3배", "obs": "예보 오차\n+이동 장애물"}
    fig_, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    x = np.arange(len(names))
    for k, (pol, col, name) in enumerate((("conf", "#1B6CA8", "95 % 신뢰 승인 (제안)"), ("nom", "#BDBDBD", "명목 승인 (여유 없음)"))):
        r = np.array([100 * S["cases"][c][pol]["rate"] for c in names])
        lo = np.array([100 * S["cases"][c][pol]["ci"][0] for c in names])
        hi = np.array([100 * S["cases"][c][pol]["ci"][1] for c in names])
        ax.bar(x + (k - 0.5) * 0.38, r, 0.36, color=col, label=name, yerr=[r - lo, hi - r], capsize=3, ecolor="#555")
        for xi, v, h in zip(x + (k - 0.5) * 0.38, r, hi):
            ax.text(xi, h + 1.2, f"{v:.1f}", ha="center", fontsize=8, fontweight="bold" if pol == "conf" else None)
    ax.axhline(95, color="#C62828", ls="--", lw=1)
    ax.text(len(names) - 0.45, 90.5, "목표 95 %", color="#C62828", ha="right", fontsize=8)
    ax.set_xticks(x, [lab[c] for c in names], fontsize=8)
    ax.set_ylabel("안전여유 확보율 [%]")
    ax.set_ylim(0, 114)
    ax.legend(fontsize=8, loc="lower left")
    ax.axvspan(1.5, len(names) - 0.5, color="#F2F2F2", zorder=-1)
    ax.text(0.5, 108, "평가항목 산정 (실제 예보 오차)", ha="center", fontsize=8, color="#333")
    ax.text((1.5 + len(names) - 0.5) / 2, 108, "오차 모형 밖 스트레스 (참고)", ha="center", fontsize=8, color="#666")
    ax.set_title(f"M4T 왕복 {S['n_planned']}개 임무 × 외란 4종 · 승인이 겨우 나는 출발 SoC 에서 비행 (막대: 성공률, 선: 95 % 신뢰구간)", fontsize=9)
    fig_.tight_layout()
    from dpo import config as C
    p = C.RES / "figures" / "F39_kpi_reserve.png"
    fig_.savefig(p)
    print("그림", p)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "run":
        run(sys.argv[2])
    else:
        analyze()
