"""E15: 실시간 엔진 폐루프 검증 – 플랫폼에 연결한 안전장치와 속도 갱신이 실제로 SOC 를 줄이는가

E13·E14 는 실험 코드에서 잰 값이다. 여기서는 배포하는 코드 경로(dpo.live.LiveEngine)를 그대로 돌려,
출발 전 계획 → 이륙 → 순항(감시·속도 갱신) → 착륙까지의 SOC 를 잰다.

  정책   P0  안전장치 끔, 속도 갱신 끔 (연결 전의 플랫폼)
         P1  안전장치 켬, 속도 갱신 끔
         P2  안전장치 켬, 속도 갱신 60 s
         P3  안전장치 켬, 속도 갱신 30 s (기본값)
         P4  안전장치 켬, 속도 갱신 15 s
  바람   real 계획 00Z 예보·실제 06Z 예보 / rot30·rot60·rot90 풍향을 돌림 / spd0.6 풍속 0.6배
  장애물 경로 한가운데를 가로지르는 이동 장애물 1개를 넣은 경우도 따로 돌려 안전(침범 0)을 확인한다.

사용: PYTHONPATH=src python scripts/exp_live_closed_loop.py run pangyo m100
      PYTHONPATH=src python scripts/exp_live_closed_loop.py analyze
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
from exp_superiority import _cluster_ci, _load_day, od_pairs  # noqa: E402

DAYS = ["20261001", "20261012", "20261031", "20261027"]
N_PAIRS = 6
HOUR = 15
PAYLOAD = dict(m100=0.5, x650=1.0, m4t=0.2)
CASES = [("real", "06z"), ("rot30", dict(rot=30.0)), ("rot60", dict(rot=60.0)), ("rot90", dict(rot=90.0)),
         ("spd0.6", dict(scale=0.6))]
POLICIES = [("P0", dict(safeguard=False, speed_update=0)), ("P1", dict(safeguard=True, speed_update=0)),
            ("P2", dict(safeguard=True, speed_update=60)), ("P3", dict(safeguard=True, speed_update=30)),
            ("P4", dict(safeguard=True, speed_update=15))]


def crossing_obstacle(a, b, v_drone=15.0):
    """출발지–도착지 직선의 가운데를, 드론이 그곳에 닿을 무렵 가로지르는 이동 장애물."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    nx, ny = -(b[1] - a[1]) / L, (b[0] - a[0]) / L
    t_mid = 30.0 + 0.5 * L / v_drone
    return dict(name="가로지름", kind="cylinder", radius=80.0, margin=15.0, z_min=0.0, z_max=1000.0, t_start=0.0,
                t_end=2 * t_mid, path=[dict(x=mx + 500 * nx, y=my + 500 * ny, t=0.0), dict(x=mx - 500 * nx, y=my - 500 * ny, t=2 * t_mid)])


def worker(args):
    site_key, ymd, dkey = args
    from dpo.live import LiveEngine
    fc, tr, d, _ = _load_day(site_key, ymd, dkey)
    pairs = od_pairs(fc, N_PAIRS)
    E = LiveEngine(fc, d, sync=True)
    rows, t0 = [], time.time()

    def run(a, b, wt, pol, obs=()):
        cfg = dict(hub=a, dst=b, dep_hour=HOUR, payload=PAYLOAD[dkey], round_trip=False, refine=False, eps=0.05, shape="gauss",
                   wind_truth=None if wt == "06z" else wt, **pol)
        E.start(cfg, list(obs), truth=tr if wt == "06z" else None)
        s = E.run_sync(t_max=4000)
        return dict(state=s["state"], soc=float(s["soc_used"]), T=float(s["t"]), upd=int(s["speed_updates"]),
                    rej=int(s["speed_rejected"]), chk=int(s["speed_checks"]), sg_short=int(s["sg_shortest"]),
                    col=int(s["collisions"]), min_sep=None if not math.isfinite(s["min_sep"]) else float(s["min_sep"]),
                    replans=int(s["replans"]), bias=float(s["bias"]), cpu_ms=s["speed_cpu_ms"])

    for q, (a, b) in enumerate(pairs):
        for cname, wt in CASES:
            row = dict(site=site_key, drone=dkey, day=ymd, pair=q, case=cname, obstacle=False,
                       dist=float(math.hypot(a[0] - b[0], a[1] - b[1])))
            for pname, pol in POLICIES:
                row[pname] = run(a, b, wt, pol)
            rows.append(row)
        ob = crossing_obstacle(a, b, d.vg_max)
        for cname, wt in (CASES[0], CASES[2]):                 # 장애물: 실제 예보 오차, 풍향 60°
            row = dict(site=site_key, drone=dkey, day=ymd, pair=q, case=cname, obstacle=True,
                       dist=float(math.hypot(a[0] - b[0], a[1] - b[1])))
            for pname, pol in (POLICIES[1], POLICIES[3]):
                row[pname] = run(a, b, wt, pol, [ob])
            rows.append(row)
    print(f"[E15 {site_key} {dkey} {ymd}] {len(rows)}건, {time.time() - t0:.0f} s", flush=True)
    return rows


def run_all(site_key, dkey):
    from dpo import config as C
    with cf.ProcessPoolExecutor(4) as ex:
        res = list(ex.map(worker, [(site_key, d_, dkey) for d_ in DAYS]))
    rows = [r for day in res for r in day]
    (C.RES / f"E15_{site_key}_{dkey}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print("저장", len(rows))


def analyze():
    from dpo import config as C
    R = {}
    for f in sorted(C.RES.glob("E15_*_*.json")):
        if f.name == "E15_stats.json":
            continue
        rows = json.loads(f.read_text())
        S = dict(cases={}, obstacle={})
        for cname, _ in CASES:
            rr = [r for r in rows if r["case"] == cname and not r["obstacle"]]
            ok = [r for r in rr if all(r[p]["state"] == "done" for p, _ in POLICIES)]
            e = dict(n=len(rr), n_ok=len(ok), not_done={p: int(sum(1 for r in rr if r[p]["state"] != "done")) for p, _ in POLICIES},
                     bias=float(np.mean([r["P3"]["bias"] for r in ok])) if ok else None,
                     soc_P0=float(np.mean([r["P0"]["soc"] for r in ok])) if ok else None)
            days = np.array([r["day"] for r in ok])
            for p, _ in POLICIES[1:]:
                for ref in ("P0", "P1"):
                    if p == ref or not ok:
                        continue
                    pct = np.array([(r[p]["soc"] / r[ref]["soc"] - 1) * 100 for r in ok])
                    e[f"{p}_vs_{ref}"] = dict(mean=float(pct.mean()), ci=list(_cluster_ci(pct, days)), win=float((pct < -0.05).mean()),
                                              lose=float((pct > 0.05).mean()), worst=float(pct.max()))
                e[f"{p}_upd"] = float(np.mean([r[p]["upd"] for r in ok])) if ok else None
                e[f"{p}_rej"] = int(sum(r[p]["rej"] for r in ok))
                e[f"{p}_sg_short"] = int(sum(r[p]["sg_short"] for r in ok))
            cpu = [r["P3"]["cpu_ms"] for r in ok if r["P3"]["cpu_ms"] is not None]
            e["cpu_ms"] = float(np.median(cpu)) if cpu else None
            e["col"] = int(sum(r[p]["col"] for r in rr for p, _ in POLICIES))
            S["cases"][cname] = e
            if ok:
                print(f"[{f.stem[4:]} {cname}] n={e['n_ok']}/{e['n']} 편차 {e['bias']:.1f} m/s | " + " | ".join(
                    f"{p} {e[p + '_vs_P0']['mean']:+.2f}% (갱신 {e[p + '_upd']:.1f}회)" for p, _ in POLICIES[1:]) + f" | 침범 {e['col']}")
        for cname in ("real", "rot60"):
            rr = [r for r in rows if r["case"] == cname and r["obstacle"]]
            if not rr:
                continue
            o = dict(n=len(rr))
            for p in ("P1", "P3"):
                done = [r for r in rr if r[p]["state"] == "done"]
                ms = [r[p]["min_sep"] for r in rr if r[p]["min_sep"] is not None]
                o[p] = dict(done=len(done), col=int(sum(r[p]["col"] for r in rr)), min_sep=float(min(ms)) if ms else None,
                            replans=float(np.mean([r[p]["replans"] for r in rr])), upd=float(np.mean([r[p]["upd"] for r in rr])),
                            rej=int(sum(r[p]["rej"] for r in rr)), soc=float(np.mean([r[p]["soc"] for r in done])) if done else None)
            both = [r for r in rr if r["P1"]["state"] == "done" and r["P3"]["state"] == "done"]
            o["P3_vs_P1"] = float(np.mean([(r["P3"]["soc"] / r["P1"]["soc"] - 1) * 100 for r in both])) if both else None
            S["obstacle"][cname] = o
            print(f"[{f.stem[4:]} 장애물 {cname}] n={o['n']} | P1 완주 {o['P1']['done']} 침범 {o['P1']['col']} 최소 이격 {o['P1']['min_sep']:.0f} m | "
                  f"P3 완주 {o['P3']['done']} 침범 {o['P3']['col']} 최소 이격 {o['P3']['min_sep']:.0f} m 갱신 {o['P3']['upd']:.1f}회 보류 {o['P3']['rej']} | "
                  f"P3/P1 SOC {o['P3_vs_P1']:+.2f}%")
        R[f.stem[4:]] = S
    (C.RES / "E15_stats.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R)


def figure(R):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from dpo import config as C
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200, "savefig.bbox": "tight",
                         "axes.titleweight": "bold"})
    SETLAB = {"changwon_x650": "창원 · X650", "pangyo_x650": "판교 · X650", "pangyo_m100": "판교 · M100",
              "changwon_m4t": "창원 · M4T", "pangyo_m4t": "판교 · M4T"}
    lab = {"real": "실제\n예보 오차", "rot30": "풍향\n30°", "rot60": "풍향\n60°", "rot90": "풍향\n90°", "spd0.6": "풍속\n0.6배"}
    pol = [("P1", "안전장치만", "#9E9E9E"), ("P2", "+ 속도 갱신 60 s", "#9CC3E4"), ("P3", "+ 속도 갱신 30 s (기본)", "#1B6CA8"),
           ("P4", "+ 속도 갱신 15 s", "#0B3C5D")]
    ncol = 2 if len(R) > 1 else 1
    nrow = int(math.ceil(len(R) / ncol))
    fig, axs = plt.subplots(nrow, ncol, figsize=(6.9 * ncol, 4.5 * nrow), squeeze=False, gridspec_kw=dict(wspace=0.24, hspace=0.5))
    for ax in axs.ravel()[len(R):]:
        ax.axis("off")
    for c, (sname, S) in enumerate(R.items()):
        ax = axs[c // ncol, c % ncol]
        names = [n for n, _ in CASES if n in S["cases"] and S["cases"][n]["n_ok"]]
        x = np.arange(len(names))
        w = 0.8 / len(pol)
        for q, (p, lb, col) in enumerate(pol):
            v = np.array([S["cases"][n][f"{p}_vs_P0"]["mean"] for n in names])
            lo = np.array([S["cases"][n][f"{p}_vs_P0"]["ci"][0] for n in names])
            hi = np.array([S["cases"][n][f"{p}_vs_P0"]["ci"][1] for n in names])
            ax.bar(x + (q - (len(pol) - 1) / 2) * w, v, w * 0.92, color=col, label=lb,
                   yerr=[np.maximum(v - lo, 0), np.maximum(hi - v, 0)], error_kw=dict(ecolor="#333", lw=0.8, capsize=2))
        ax.axhline(0, color="#555", lw=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{lab[n]}\n({S['cases'][n]['bias']:.1f} m/s)" for n in names], fontsize=8.5)
        ax.set_ylabel("연결 전 플랫폼 대비 SOC 소모 변화 [%] (음수 = 절감)")
        ax.set_title(f"{SETLAB.get(sname, sname)}: 실시간 엔진 폐루프 (편도, 경우마다 {S['cases'][names[0]]['n_ok']}건)\n괄호: 기체가 추정한 바람 편차",
                     fontsize=10.5)
        if c == 0:
            if len(R) < nrow * ncol:              # 빈 칸이 있으면 범례를 그리로 보낸다
                h, l = ax.get_legend_handles_labels()
                axs.ravel()[-1].legend(h, l, frameon=False, fontsize=11, loc="center", title="설정 (연결 전 = 안전장치 끔, 속도 갱신 끔)",
                                       title_fontsize=10.5)
            else:
                ax.legend(frameon=False, fontsize=9, loc="lower left")
    fig.savefig(C.FIG / "F30_live_closed_loop.png")
    plt.close(fig)


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run_all(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "x650")
    else:
        analyze()
