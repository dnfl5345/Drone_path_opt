"""E11: 실측 ADS-B 항적을 '실제 침입기'로 쓰는 폐루프 몬테카를로 – 팽창 모양(가우시안 / 고정 / 데이터 분위수) 비교.

설정
- 드론: E10 과 같은 판교 배송 임무(15–18시 GFS 바람), 처음에는 침입기를 모름 → 장애물 없는 계획으로 출발
- 침입기: 시험 폴드의 실측 1 Hz 항적 구간(응급 헬기·일반 항공). 시간·선회 이력은 그대로 두고 공간만
  s = U/V0 배 축소해 속도 U ∈ [8, 30] m/s 의 저속 침입기(다른 드론·경량 헬기, E10 과 같은 범위)로 만든다
  (가정: 속도 정규화 오차 ‖e‖/V 의 분포가 속도와 무관 – 선회율이 같으면 위치 오차는 속도에 비례).
  처음 탐지 시각 t_pop 에서 L 초 뒤 등속 예측 위치가 드론의 공칭 위치와 겹치도록 회전·평행이동(충돌 코스).
  반경 R ∈ [40, 150] m (E10 과 같음), 충돌 = R + 0.5 m 이내.
- 감시: PERIOD 초마다 침입기 상태(위치·ADS-B 보고 속도)를 관측 → 등속 예측 + 팽창 튜브(예측 지평 H 초)
  → 남은 경로와 충돌이 예상되면 현재 위치에서 재계획(지연 3 s)
- 방법: N(팽창 없음), G(ε) 가우시안 튜브, B(ε) 가우시안 고정 버퍼, Q(ε) 데이터 분위수 튜브
  모두 '반대 폴드' 학습 자료로 적합 (교차 적합, 10분 블록 단위 분할)
- 평가: 실측 궤적과의 최소 이격, NMAC(수평 500 ft = 152.4 m) 침범 여부, 에너지, 재계획 횟수

사용: PYTHONPATH=src python scripts/run_e11.py [n_scen] [K] [workers]
"""
from __future__ import annotations

import math
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
from run_experiments import sites, drone_calibrated, PAYLOAD, KST, HUB, DST, E10_HOURS, save  # noqa: E402
from dpo.planner import simulate, vertical_energy  # noqa: E402
from dpo.obstacles import DynObstacle, risk_kappa  # noqa: E402
from dpo.adsb_errors import load_tracks, velocity, split_blocks, TAU  # noqa: E402
from dpo import config as C  # noqa: E402

H = 20.0                  # 튜브 예측 지평 [s] (재관측 주기 + 여유)
PERIOD = 5.0              # 감시 갱신 주기 [s]
LATENCY = 3.0
R_NMAC = 152.4            # (원래 속도 그대로 쓰는 민감도 실험용) 근접 공중 충돌 수평 기준 500 ft
MARGIN = 15.0
SEG = 150.0               # 침입기 실측 구간 길이 [s]
KN = [0.0, 5.0, 10.0, 15.0, 20.0]
METHODS = [("N", None), ("G", 0.05), ("B", 0.05), ("Q", 0.05), ("G", 0.01), ("B", 0.01), ("Q", 0.01)]

G_ = {}


# ------------------------------------------------------------------ 오차 모델 적합 (지평 H)
def fit_models(err, keys):
    m = np.array([b in keys for b in err["block"]])
    E, V = err["E"][m], err["V"][m]
    En = E / V[:, None, None]
    jj = TAU <= H + 1e-9
    rms = np.sqrt(np.nanmean(En[:, jj, 0] ** 2 + En[:, jj, 1] ** 2, axis=0) / 2)
    A = np.c_[np.ones(jj.sum()), TAU[jj]]
    s0, s1 = np.linalg.lstsq(A, rms, rcond=None)[0]
    if s0 < 0:
        s0, s1 = 0.0, float(np.sum(TAU[jj] * rms) / np.sum(TAU[jj] ** 2))
    rn = np.hypot(En[..., 0], En[..., 1])
    q = {}
    for eps in (0.2, 0.05, 0.01):
        qq = [np.nanquantile(rn[:, list(TAU).index(k)], 1 - eps) if k > 0 else 0.0 for k in KN]
        q[eps] = np.maximum.accumulate(qq).tolist()
    return dict(s0=float(s0), s1=float(s1), q=q, n=int(m.sum()))


def tube_knots(model, kind, eps, V):
    if kind == "N":
        return None
    k = risk_kappa(eps)
    if kind == "G":
        return [[0.0, V * k * model["s0"]], [H, V * k * (model["s0"] + model["s1"] * H)]]
    if kind == "B":
        return [[0.0, V * k * (model["s0"] + model["s1"] * H)]]
    return [[t, V * r] for t, r in zip(KN, model["q"][eps])]


# ------------------------------------------------------------------ 침입기 (실측 항적 변환)
class Intruder:
    def __init__(self, tr, i0, t_pop, L, heading, D, U=None, R=R_NMAC):
        self.tr, self.i0, self.t_pop, self.R = tr, i0, t_pop, R
        v0 = velocity(tr, i0)
        h0 = math.atan2(v0[0], v0[1])
        d = heading - h0
        self.k = 1.0 if U is None else U / math.hypot(*v0)        # 공간 축소 배율
        self.c, self.s = math.cos(d) * self.k, math.sin(d) * self.k
        rv = self.rot(*v0)
        self.P0 = (D[0] - L * rv[0], D[1] - L * rv[1])
        self.s0 = tr["t"][i0]
        self.s_end = min(tr["t"][-1], self.s0 + SEG)

    def rot(self, x, y):           # 방위각(북 기준 시계방향)을 d 만큼 회전 + 배율 k
        return (x * self.c + y * self.s, -x * self.s + y * self.c)

    def pos(self, t_rel):
        """출발 기준 시각 배열 → 실제 위치 (x, y), 존재 마스크."""
        t_rel = np.asarray(t_rel, float)
        s = self.s0 + (t_rel - self.t_pop)
        act = (t_rel >= self.t_pop) & (s <= self.s_end)
        tr = self.tr
        dx = np.interp(s, tr["t"], tr["x"]) - tr["x"][self.i0]
        dy = np.interp(s, tr["t"], tr["y"]) - tr["y"][self.i0]
        x, y = self.rot(dx, dy)
        return self.P0[0] + x, self.P0[1] + y, act

    def observe(self, t_rel, kind, eps, model):
        if t_rel < self.t_pop:
            return []
        s = self.s0 + (t_rel - self.t_pop)
        if s > self.s_end:
            return []
        tr = self.tr
        i = int(np.searchsorted(tr["t"], s, side="right")) - 1
        v = velocity(tr, i) or (0.0, 0.0)
        vx, vy = self.rot(*v)
        V = max(math.hypot(vx, vy), 1.0)
        x, y, _ = self.pos(np.array([t_rel]))
        x, y = float(x[0]), float(y[0])
        t_end = min(t_rel + H, self.t_pop + (self.s_end - self.s0))
        return [DynObstacle(name="intruder", kind="cylinder", radius=self.R, margin=MARGIN, z_min=0.0, z_max=5000.0,
                            path=[dict(x=x, y=y, t=t_rel), dict(x=x + vx * (t_end - t_rel), y=y + vy * (t_end - t_rel),
                                                                   t=t_end)],
                            t_start=t_rel, t_end=t_end, t_obs=t_rel, tube=tube_knots(model, kind, eps, V))]


# ------------------------------------------------------------------ 작업자
def _init():
    from dpo.st_planner import STPlanner
    g, fc, tr = sites()
    dr = drone_calibrated()
    m = dr.m_empty + PAYLOAD
    SP = STPlanner(fc, dr)
    st, gl = fc.lowest_free_k(*HUB), fc.lowest_free_k(*DST)
    noms = {}
    for h in E10_HOURS:
        t_dep = (h - KST) * 3600.0
        SP.set_obstacles([], t_dep)
        _, t_to = vertical_energy(fc, dr, HUB[0], HUB[1], st[0], t_dep, m, up=True)
        r0 = SP.plan(st, gl, t_start=t_dep + t_to, mass=m, weight=1.0)
        sim = simulate(fc, dr, r0["path"], r0["wait_before"], t_dep + t_to, m)
        noms[h] = (r0, sim)
    G_.update(fc=fc, dr=dr, m=m, noms=noms, usable=dr.battery_wh * 3600 * 0.8,
              tracks=load_tracks(rate="1hz"))


def run_job(job):
    from dpo.st_rolling import ObstacleTimeline, run_obstacle_replanning
    if not G_:
        _init()
    fc, dr, m = G_["fc"], G_["dr"], G_["m"]
    h, t_pop, L, heading, k, i0, model = job["hour"], job["t_pop"], job["L"], job["heading"], job["k"], job["i0"], \
        job["model"]
    t_dep = (h - KST) * 3600.0
    r0, nom = G_["noms"][h]
    tn = nom["t"] - t_dep
    D = (float(np.interp(t_pop + L, tn, nom["x"])), float(np.interp(t_pop + L, tn, nom["y"])))
    intr = Intruder(G_["tracks"][k], i0, t_pop, L, heading, D, U=job["U"], R=job["R"])
    out = dict(job={kk: v for kk, v in job.items() if kk != "model"}, V0=float(math.hypot(*velocity(G_["tracks"][k], i0))))
    for kind, eps in METHODS:
        key = kind if eps is None else f"{kind}{eps}"
        t0 = time.perf_counter()
        res = run_obstacle_replanning(fc, fc, dr, HUB, DST, t_dep, m, ObstacleTimeline([(0.0, [])]), policy="observe",
                                      period=PERIOD, latency=LATENCY, budget=G_["usable"], weight=1.0,
                                      observe=lambda t_rel: intr.observe(t_rel, kind, eps, model),
                                      initial_plan=r0, replan_weight=1.2, max_replans=60,
                                      fallbacks=() if kind == "N" else (
                                          lambda t_rel: intr.observe(t_rel, kind, 0.2, model),
                                          lambda t_rel: intr.observe(t_rel, "N", None, model)))
        if not res.get("feasible", True):
            out[key] = dict(feasible=False)
            continue
        tr_ = res["trace"]
        x, y, act = intr.pos(tr_["t"] - t_dep)
        d = np.where(act, np.hypot(tr_["x"] - x, tr_["y"] - y), np.inf)
        stuck = sum(1 for e in res["events"] if "기존 경로 유지" in e.get("reason", ""))
        degr = sum(1 for e in res["events"] if (e.get("fallback") or 0) > 0)
        out[key] = dict(feasible=True, min_sep=float(d.min() - job["R"]), nmac=bool(d.min() <= job["R"] + 0.5), E=res["E"],
                        T=res["T"], replans=res["n_replans"], stuck=stuck, degraded=degr,
                        cpu=time.perf_counter() - t0)
    return out


# ------------------------------------------------------------------ 시나리오
def _cruise_windows():
    """시각별 공칭 계획의 순항 시작·끝 (출발 기준 s)."""
    if not G_:
        _init()
    out = {}
    for h, (r0, sim) in G_["noms"].items():
        t_dep = (h - KST) * 3600.0
        out[h] = (float(sim["t"][0] - t_dep), float(sim["t"][-1] - t_dep))
    return out


def make_jobs(n_scen, K, seed=2027):
    with open(C.RES / "adsb_errors_1hz.pkl", "rb") as f:
        err = pickle.load(f)
    A, B = split_blocks(err["block"], seed=11)
    models = [fit_models(err, A), fit_models(err, B)]          # 폴드 0: A 로 적합 → B 의 항적으로 시험
    tracks = load_tracks(rate="1hz")
    pool = [[], []]
    for k, tr in enumerate(tracks):
        t = tr["t"]; nxt = t[0]
        for i in range(len(t)):
            if t[i] < nxt or t[-1] - t[i] < SEG:
                continue
            nxt = t[i] + 10.0
            if np.isfinite(tr["alt"][i]) and tr["alt"][i] > tr["alt_max"]:
                continue
            v = velocity(tr, i)
            if v is None or not (10.0 <= math.hypot(*v) <= 75.0):
                continue
            blk = f"{tr['src']}|{tr['icao']}|{int(t[i] // 600)}"
            if blk in B:
                pool[0].append((k, i))
            elif blk in A:
                pool[1].append((k, i))
    cruise = _cruise_windows()
    rng = np.random.default_rng(seed)
    jobs = []
    for q in range(n_scen):
        h = int(rng.choice(E10_HOURS))
        fold = q % 2
        L = float(rng.uniform(15.0, 40.0))
        c0, c1 = cruise[h]                     # 순항 구간(이륙 상승·착륙 하강 제외)에서 탐지
        t_pop = float(rng.uniform(c0 + 5.0, max(c1 - L - 5.0, c0 + 6.0)))
        for kk in range(K):
            k, i0 = pool[fold][int(rng.integers(len(pool[fold])))]
            jobs.append(dict(scen=q, sample=kk, hour=h, fold=fold, t_pop=t_pop, L=L,
                             heading=float(rng.uniform(0, 2 * np.pi)), k=k, i0=i0, model=models[fold],
                             U=float(rng.uniform(8.0, 30.0)), R=float(rng.uniform(40.0, 150.0))))
    return jobs, models, [len(p) for p in pool]


def main():
    n_scen = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    jobs, models, npool = make_jobs(n_scen, K)
    print(f"[E11] jobs {len(jobs)} pool {npool} models", [{k: (round(v, 3) if isinstance(v, float) else v)
                                                          for k, v in mm.items() if k != 'q'} for mm in models], flush=True)
    t0 = time.time()
    rows = []
    if workers > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers) as pool:
            for n, r in enumerate(pool.imap_unordered(run_job, jobs, chunksize=1)):
                rows.append(r)
                if n % 10 == 0 or n == len(jobs) - 1:
                    s = " ".join(f"{k}:{np.mean([x[k]['nmac'] for x in rows if x.get(k, {}).get('feasible')]):.3f}"
                                 for k in ("N", "G0.05", "B0.05", "Q0.05", "G0.01", "B0.01", "Q0.01"))
                    print(f"[E11] {n + 1}/{len(jobs)} nmac {s} ({time.time() - t0:.0f}s)", flush=True)
                    save("E11_partial", dict(rows=rows))
    else:
        for n, j in enumerate(jobs):
            rows.append(run_job(j))
            print(f"[E11] {n + 1}/{len(jobs)} ({time.time() - t0:.0f}s)", {k: (v.get("nmac"), round(v.get("min_sep", 0)),
                                                                              v.get("replans"), v.get("degraded"), v.get("stuck"))
                                                                          for k, v in rows[-1].items()
                                                                          if isinstance(v, dict) and "feasible" in v},
                  flush=True)
    save("E11", dict(rows=rows, models=models, params=dict(n_scen=n_scen, K=K, H=H, PERIOD=PERIOD, LATENCY=LATENCY,
                                                           R_NMAC=R_NMAC, MARGIN=MARGIN, SEG=SEG, KN=KN,
                                                           METHODS=METHODS)))
    print(f"[E11] done {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
