"""보고서·발표자료가 함께 쓰는 결과 값 (results/*.json 에서 표 줄과 문장 값을 만든다).

실증 기체 DJI Matrice 4T 결과를 먼저 두고, X650·M100 은 비교 기체로 둔다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"


def j(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


def pct(a, b):
    return (b - a) / a * 100.0


# ── 결과 파일에서 표 줄을 만드는 도구 (M4T 를 먼저, X650·M100 은 참고) ──
SETS_M4T = [("pangyo_m4t", "판교 · M4T"), ("changwon_m4t", "창원 · M4T")]
SETS_REF = [("changwon_x650", "창원 · X650"), ("pangyo_x650", "판교 · X650"), ("pangyo_m100", "판교 · M100")]


def _f(x, nd=1):
    return f"{x:+.{nd}f}".replace("-", "−")


def e13_rows(sets):
    R = j("E13_stats.json")["sets"]
    out = []
    for k, lab in sets:
        if k not in R:
            continue
        c = R[k]["EX_P0"]
        out.append([lab, f"{c['a_soc']:.1f} %p", f"{c['b_soc']:.1f} %p", f"**{_f(c['mean_pct'])} % ({_f(c['ci'][0])}, {_f(c['ci'][1])})",
                    f"{round(c['win'] * c['n'])}/{c['n']}", f"{_f(c['time_pct'])} %"])
    return out


def e13_split_rows(sets):
    R = j("E13_stats.json")["sets"]
    out = []
    for k, lab in sets:
        if k not in R:
            continue
        S = R[k]
        sg = S.get("EXv_P0s")
        out.append([lab, f"{_f(S['EX_EXv']['mean_pct'])} %", f"{_f(S['EXv_P0']['mean_pct'])} % ({_f(S['EXv_P0']['ci'][0])}, {_f(S['EXv_P0']['ci'][1])})",
                    f"{_f(sg['mean_pct'])} %, 최악 {_f(sg['worst_pct'])} %" if sg else "–"])
    return out


def e13_range(sets, key="EX_P0"):
    R = j("E13_stats.json")["sets"]
    v = [R[k][key]["mean_pct"] for k, _ in sets if k in R]
    return (max(v), min(v)) if v else (float("nan"), float("nan"))


def e13_n(sets):
    R = j("E13_stats.json")["sets"]
    return sum(R[k]["n"] for k, _ in sets if k in R)


def cmp3d_rows(sites):
    rows = []
    for site, lab in sites:
        f = RES / f"platform_3d_compare_{site}.json"
        if not f.exists():
            continue
        d = {m["key"]: m for m in json.loads(f.read_text(encoding="utf-8"))["methods"]}
        e, v, p = d["existing"], d["existing_vopt"], d["proposed"]
        rows.append([lab, f"{e['soc_used']:.1f} %p" + ("" if e["feasible"] else " (임무 불가)"),
                     f"{v['soc_used']:.1f} %p" + ("" if v["feasible"] else " (임무 불가)"),
                     f"**{p['soc_used']:.1f} %p", f"{_f(pct(e['soc_used'], p['soc_used']))} %", f"{e['T']/60:.1f} → {p['T']/60:.1f} 분"])
    return rows


def _rng(a, b):
    lo, hi = sorted((abs(a), abs(b)))
    return f"{lo:.1f}–{hi:.1f} %"


def summary_values():
    """보고서 문장에 넣는 값 (M4T 결과와 비교 기체 결과)."""
    E = j("E13_stats.json")["sets"]
    V = dict(M=m4t_model())
    V["n4"], V["nR"] = e13_n(SETS_M4T), e13_n(SETS_REF)
    V["r4"], V["rR"] = _rng(*e13_range(SETS_M4T)), _rng(*e13_range(SETS_REF))
    V["sp4"] = _rng(*e13_range(SETS_M4T, "EX_EXv"))
    V["pa4"] = _rng(*e13_range(SETS_M4T, "EXv_P0"))
    V["spR"], V["paR"] = _rng(*e13_range(SETS_REF, "EX_EXv")), _rng(*e13_range(SETS_REF, "EXv_P0"))
    ci = [E[k]["EX_P0"]["ci"][1] for k, _ in SETS_M4T if k in E]
    V["ci4"] = _rng(min(ci), max(ci)) if ci else "–"
    wins = [(round(E[k]["EX_P0"]["win"] * E[k]["EX_P0"]["n"]), E[k]["EX_P0"]["n"]) for k, _ in SETS_M4T if k in E]
    V["win4"] = f"{sum(a for a, _ in wins)}/{sum(b for _, b in wins)}"
    inf = {k: E[k]["infeasible"] for k, _ in SETS_M4T if k in E}
    V["inf4"] = inf
    T = e15_table()
    V["e15_m4"], V["obs_n"], V["obs_col"] = T["m4"], T["obs_n"], T["obs_col"]
    return V


E15_CASES = [("real", "실제 예보 오차"), ("rot30", "풍향 30° 회전"), ("rot60", "풍향 60° 회전"), ("rot90", "풍향 90° 회전"),
             ("spd0.6", "풍속 0.6배")]


def e15_table():
    """E15: 연결 전 플랫폼(P0) 대비 안전장치 + 30초 속도 갱신(P3)의 SoC 소모 변화. M4T 를 먼저."""
    E = j("E15_stats.json")
    order = [(k, l) for k, l in SETS_M4T + [("changwon_x650", "창원 · X650"), ("pangyo_m100", "판교 · M100"), ("pangyo_x650", "판교 · X650")] if k in E]
    head = ["바람 경우 (추정 편차)"] + [l + ("" if "M4T" in l else " (비교)") for _, l in order]
    rows = []
    for c, lab in E15_CASES:
        b = [E[k]["cases"][c]["bias"] for k, _ in order if c in E[k]["cases"]]
        r = [f"{lab} ({min(b):.1f}–{max(b):.1f} m/s)" if b and max(b) - min(b) > 0.05 else f"{lab} ({b[0]:.1f} m/s)" if b else lab]
        for k, _ in order:
            cc = E[k]["cases"].get(c)
            r.append(f"{_f(cc['P3_vs_P0']['mean'])} %" if cc else "–")
        rows.append(r)
    obs = [(v["P3"]["done"], v["n"], v["P3"]["col"], v["P3"]["min_sep"]) for k, _ in order for v in E[k].get("obstacle", {}).values()]
    worst = max(E[k]["cases"][c]["P3_vs_P1"]["worst"] for k, _ in order for c in E[k]["cases"])
    n_cases = sum(E[k]["cases"][c]["n"] for k, _ in order for c in E[k]["cases"])
    m4 = [E[k]["cases"][c]["P3_vs_P0"]["mean"] for k, _ in SETS_M4T if k in E for c in E[k]["cases"]]
    return dict(head=head, rows=rows, n_sets=len(order), n_cases=n_cases, worst=worst,
                obs_n=sum(o[1] for o in obs), obs_done=sum(o[0] for o in obs), obs_col=sum(o[2] for o in obs),
                obs_sep=(min(o[3] for o in obs), max(o[3] for o in obs)) if obs else (0, 0),
                m4=(_rng(max(m4), min(m4)) if m4 else None))


def m4t_model():
    """M4T 전력 모델 요약 (scripts/m4t_model_summary.py 가 만든 results/m4t_model.json)."""
    return j("m4t_model.json")


def wind_trend(sets=None):
    """날짜별 평균 풍속에 따른 기존 경로 대비 절감 (가장 약한 날과 가장 강한 날)."""
    sets = sets or SETS_M4T
    R = j("E13_stats.json")["sets"]
    days = [(v["wind"], v["EX_P0"]) for k, _ in sets if k in R for v in R[k]["by_day"].values() if v["EX_P0"] is not None]
    if not days:
        return None
    lo, hi = min(days), max(days)
    return dict(w_lo=lo[0], p_lo=lo[1], w_hi=hi[0], p_hi=hi[1])


def kpi():
    """E17 (scripts/exp_kpi_reserve.py): 7/28 계획서 표2 평가항목 '외란을 고려한 배터리 안전여유 확보율'."""
    S = j("E17_stats.json")
    pc = lambda x: f"{100 * x:.1f}"
    R, C = S["realistic"], S["cases"]
    out = dict(
        n=S["n_planned"], sites=len(S["sites"]), days=len(S["days"]),
        rate=pc(R["conf"]["rate"]), k=R["conf"]["success"], N=R["conf"]["approved"],
        lo=pc(R["conf"]["ci"][0]), hi=pc(R["conf"]["ci"][1]),
        nom_rate=pc(R["nom"]["rate"]), nom_k=R["nom"]["success"], nom_N=R["nom"]["approved"],
        margin=f"{S['plan']['margin']:.2f}", margin_rng=f"{S['plan']['margin_rng'][0]:.2f}–{S['plan']['margin_rng'][1]:.2f}",
        used=f"{S['plan']['nominal']:.1f}", used_rng=f"{S['plan']['nominal_rng'][0]:.1f}–{S['plan']['nominal_rng'][1]:.1f}",
        s0=f"{S['plan']['s0_conf'][0]:.1f}–{S['plan']['s0_conf'][1]:.1f}",
        dist=f"{S['plan']['dist'][0] / 1000:.1f}–{S['plan']['dist'][1] / 1000:.1f}",
        cases={c: dict(conf=f"{C[c]['conf']['success']}/{C[c]['conf']['approved']} ({pc(C[c]['conf']['rate'])} %)",
                       nom=f"{C[c]['nom']['success']}/{C[c]['nom']['approved']} ({pc(C[c]['nom']['rate'])} %)",
                       below=C[c]["conf"]["below"], hold=C[c]["conf"]["hold"], emerg=C[c]["conf"]["emerg"], col=C[c]["conf"]["col"],
                       end_min=f"{C[c]['conf']['end_min']:.1f}") for c in C},
        col_total=sum(C[c][p]["col"] for c in C for p in ("conf", "nom")),
    )
    return out
