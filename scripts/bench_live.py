"""실시간 운용 엔진 벤치마크 (스레드 실제 구동, 화면 없음) → results/live_bench.json

시나리오: 20시 출발 배송 왕복, ×SPEED 배속, t≈60 s 실측 ADS-B 침입기 투입(충돌 코스), t≈61 s 진행 경로 60 % 지점에
정지 장애물, 외부 보고 항적 1개. 시드별 반복. 지표: 완주·침범·비상 정지·재계획·늦은 계산, 시계 주기(p95/p99/최대),
감시 검사 시간, 재계획·개선 계산 시간.
사용: PYTHONPATH=src python scripts/bench_live.py [runs] [speed]
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from dpo import config as C
from dpo.energy import Drone
from dpo.live import LiveEngine
from dpo.wind_field import Site3D


def main(runs=5, speed=10.0):
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    site.add_static_nfz_circle(3650.0, 2250.0, 250.0)
    dd = json.loads((C.RES / "drone_params.json").read_text())
    drone = Drone(**{k: v for k, v in dd.items() if k in Drone.__dataclass_fields__})
    m = json.loads((C.RES / "adsb_error_model.json").read_text())
    model = dict(knots=m["knots"], q=m["model_all"]["q"], s1=m["model_all"]["s1"])
    E = LiveEngine(site, drone, model)
    E.SP.plan(site.lowest_free_k(2700, 3000), site.lowest_free_k(4600, 1500), t_start=11 * 3600.0, weight=2.0)  # JIT 예열
    E.SPr.plan(site.lowest_free_k(2700, 3000), site.lowest_free_k(4600, 1500), t_start=11 * 3600.0, weight=2.0)
    time.sleep(5)                                  # 실측 항적 미리 읽기 완료 대기
    rows = []
    for seed in range(1, runs + 1):
        E.start(dict(dep_hour=20, round_trip=True, eps=0.05, shape="adsb", speed=speed, period=2.0, latency=3.0))
        fired = set()
        t0 = time.time()
        while E.state == "running" and time.time() - t0 < 600:
            time.sleep(0.05)
            if E.t > 60 and "i" not in fired:
                fired.add("i"); E.inject_intruder(dict(seed=seed, lead=25, speed=20, radius=80))
            if E.t > 61 and "o" not in fired and E.plan is not None:
                fired.add("o")
                sim = E.plan["sim"]; q = int(len(sim["t"]) * 0.6)
                E.upsert_obstacle(dict(name="경로 위", kind="cylinder", radius=100, margin=15, z_min=0, z_max=1000,
                                       path=[dict(x=float(sim["x"][q]), y=float(sim["y"][q]), t=0)], t_start=E.t))
                x, y, _ = E._drone_at(E.t)
                E.report(dict(id="7", x=x + 900, y=y - 900, vx=-12.0, vy=12.0, radius=50))
        s = E.summary()
        s.update(seed=seed, wall_s=time.time() - t0)
        rows.append(s)
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()}, flush=True)
        E.stop()
    agg = dict(runs=runs, speed=speed,
               completed=sum(r["state"] == "done" for r in rows), collisions=sum(r["collisions"] for r in rows),
               holds=sum(r.get("holds", 0) for r in rows), replans=sum(r["replans"] for r in rows),
               late=sum(r["late"] for r in rows), fail=sum(r["fail"] for r in rows),
               min_sep=min(r["min_sep"] for r in rows),
               tick_ms_p95=float(np.max([r["tick_ms_p95"] for r in rows])),
               tick_ms_p99=float(np.max([r["tick_ms_p99"] for r in rows])),
               tick_ms_max=float(np.max([r["tick_ms_max"] for r in rows])),
               check_ms_median=float(np.median([r["check_ms_median"] for r in rows])),
               replan_cpu_median=float(np.median([r["cpu_median"] for r in rows])),
               replan_cpu_max=float(np.max([r["cpu_max"] for r in rows])),
               soc_used=[round(r["soc_used"], 1) for r in rows])
    (C.RES / "live_bench.json").write_text(json.dumps(dict(aggregate=agg, runs=rows), indent=1, ensure_ascii=False,
                                                      default=float))
    print(json.dumps(agg, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(*(int(sys.argv[1]),) if len(sys.argv) > 1 else (), **({"speed": float(sys.argv[2])} if len(sys.argv) > 2 else {}))
