"""배경 실험 E12: 결정 지연(탐지 → 새 경로 적용)이 길어지면 회피할 수 있는가? → results/E12_latency.json

'왜 드론(기체·현장)에서 경로를 결정해야 하는가'의 근거.
- 임무: 20시 출발 편도 배송. t = 60 s 에 실측 ADS-B 항적 침입기(20 m/s, 반경 80 m)가 LEAD 초 뒤 드론 예정 위치로 향함.
- 지연 L: 충돌 예상을 알아챈 뒤 새 경로가 실제로 적용되기까지의 시간 (통신 왕복 + 계산 + 승인).
- 두 방식:  onboard = 기체가 즉시 비상 정지(호버) 가능 + 재계획 지연 L
            remote  = 기체는 스스로 멈추지 못하고, 지연 L 뒤에 새 경로만 적용
- 동기(결정적) 모드, 시드별 같은 침입기. 지표: 침범 여부, 최소 이격.
사용: PYTHONPATH=src python scripts/exp_latency.py [n_seeds]
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from dpo import config as C
from dpo.energy import Drone
from dpo.live import LiveEngine
from dpo.robust_eval import wilson
from dpo.wind_field import Site3D

LATENCIES = [1.0, 3.0, 10.0, 20.0, 30.0]
LEAD = 25.0


def main(n=16):
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    site.add_static_nfz_circle(3650.0, 2250.0, 250.0)
    dd = json.loads((C.RES / "drone_params.json").read_text())
    drone = Drone(**{k: v for k, v in dd.items() if k in Drone.__dataclass_fields__})
    m = json.loads((C.RES / "adsb_error_model.json").read_text())
    model = dict(knots=m["knots"], q=m["model_all"]["q"], s1=m["model_all"]["s1"])
    E = LiveEngine(site, drone, model, sync=True)
    rows = []
    t0 = time.time()
    for mode in ("onboard", "remote"):
        for L in LATENCIES:
            hit, seps, holds = 0, [], 0
            for seed in range(1, n + 1):
                E.start(dict(dep_hour=20, round_trip=False, eps=0.05, shape="adsb", latency=L, period=2.0,
                             hold=(mode == "onboard"), refine=False))
                r = E.run_sync(t_max=230.0, dt=0.5,
                               hooks={60.0: lambda e, s=seed: e.inject_intruder(dict(seed=s, lead=LEAD, speed=20,
                                                                                    radius=80))})
                hit += int(r["collisions"] > 0); seps.append(r["min_sep"]); holds += r.get("holds", 0)
            p, lo, hi = wilson(hit, n)
            rows.append(dict(mode=mode, latency=L, n=n, intrusions=hit, p=p, lo=lo, hi=hi,
                             min_sep_median=float(np.median(seps)), min_sep_min=float(np.min(seps)),
                             holds_per_flight=holds / n))
            print(rows[-1], f"({time.time() - t0:.0f}s)", flush=True)
    (C.RES / "E12_latency.json").write_text(json.dumps(dict(lead=LEAD, rows=rows), indent=1, default=float))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 16)
