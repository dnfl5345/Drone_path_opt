"""장애물 궤적 불확실성 하의 강건 계획 – 무작위 시나리오 생성과 몬테카를로 평가.

불확실성(생성) 모델 – 직선 등속 장애물 j:
    p_j^true(t) = p_j^nom(t − δt_j) + e_j + δv_j · (t − t_obs)
    e_j ~ N(0, σ0² I₂),  δv_j ~ N(0, σ_v² I₂),  δt_j ~ N(0, σ_t²)
    → 축별 표준편차 √(σ0² + σ_v² τ²) ≤ σ0 + σ_v τ  (계획기의 튜브는 이 상계를 사용 → 시점별 보수적)
충돌 판정(실제): 장애물 물리 반경 R + 드론 반경 r_d 안으로 들어가면 충돌 (안전 이격 margin 제외).
"""
from __future__ import annotations

import copy
import math

import numpy as np

from .obstacles import DynObstacle

R_DRONE = 0.5


# ------------------------------------------------------------------ 시나리오
def make_scenario(rng, nom_sim, t_dep, n_obs=3, sigma0=10.0, sigma_v=0.5, sigma_t=2.0, z_all=True):
    """공칭(장애물 없음) 경로를 무작위 시각·방향·속도로 가로지르는 직선 이동 장애물 n_obs 개."""
    T = nom_sim["t"][-1] - t_dep
    obs = []
    for q in range(n_obs):
        tc = float(rng.uniform(25.0, max(T - 15.0, 30.0)))
        i = int(np.searchsorted(nom_sim["t"] - t_dep, tc))
        cx, cy, cz = float(nom_sim["x"][i]), float(nom_sim["y"][i]), float(nom_sim["z"][i])
        th = rng.uniform(0, 2 * np.pi)
        U = float(rng.uniform(8.0, 30.0))
        R = float(rng.uniform(40.0, 150.0))
        vx, vy = U * math.sin(th), U * math.cos(th)
        span = 120.0
        t0, t1 = max(tc - span, 0.0), tc + span
        o = DynObstacle(name=f"O{q + 1}", kind="cylinder", radius=R, margin=15.0,
                        z_min=0.0 if z_all else cz - 60, z_max=1000.0 if z_all else cz + 60,
                        path=[dict(x=cx + vx * (t0 - tc), y=cy + vy * (t0 - tc), t=t0),
                              dict(x=cx + vx * (t1 - tc), y=cy + vy * (t1 - tc), t=t1)],
                        sigma0=sigma0, sigma_v=sigma_v, sigma_t=sigma_t, t_obs=0.0)
        obs.append(o)
    return obs


def with_sigma(obs, sigma0=None, sigma_v=None, sigma_t=None):
    out = []
    for o in obs:
        c = copy.deepcopy(o)
        if sigma0 is not None: c.sigma0 = sigma0
        if sigma_v is not None: c.sigma_v = sigma_v
        if sigma_t is not None: c.sigma_t = sigma_t
        out.append(c)
    return out


def fixed_buffer(obs, horizon):
    """고정 버퍼 비교안: 예측 지평 끝의 σ(T_h) 를 시간과 무관하게 적용 (σ_v=0, σ0=σ(T_h))."""
    out = []
    for o in obs:
        c = copy.deepcopy(o)
        c.sigma0 = o.sigma0 + o.sigma_v * max(horizon - o.t_obs, 0.0)
        c.sigma_v = 0.0
        out.append(c)
    return out


def blend_buffer(obs, horizon, lam):
    """혼합 튜브: σ_λ(τ) = (1−λ)·σ(τ) + λ·σ(T_h)  (λ=0 시간 증가 튜브, λ=1 고정 버퍼). 여전히 τ 에 선형 → 해석해 유지."""
    out = []
    for o in obs:
        c = copy.deepcopy(o)
        c.sigma0 = o.sigma0 + lam * o.sigma_v * max(horizon - o.t_obs, 0.0)
        c.sigma_v = (1.0 - lam) * o.sigma_v
        out.append(c)
    return out


# ------------------------------------------------------------------ 실제 궤적 샘플
def sample_truth(rng, obs, K):
    """각 장애물의 실제 궤적 파라미터 K 세트 (e, δv, δt)."""
    S = []
    for o in obs:
        S.append(dict(e=rng.normal(0, o.sigma0, (K, 2)), dv=rng.normal(0, o.sigma_v, (K, 2)),
                      dt=rng.normal(0, o.sigma_t, K)))
    return S


def sample_truth_t(rng, obs, K, df=3):
    """모델 불일치 검사용: 같은 표준편차의 Student-t(df) 오차 (꼬리가 두꺼움)."""
    sc = math.sqrt((df - 2) / df)
    return [dict(e=rng.standard_t(df, (K, 2)) * sc * o.sigma0, dv=rng.standard_t(df, (K, 2)) * sc * o.sigma_v,
                 dt=rng.standard_t(df, K) * sc * o.sigma_t) for o in obs]


def true_positions(o, smp, t):
    """t: (T,) 출발 기준 시각 → (K, T) x, y, 존재 마스크."""
    p0, p1 = o.path[0], o.path[-1]
    ta, tb = p0["t"], p1["t"]
    vx, vy = (p1["x"] - p0["x"]) / (tb - ta), (p1["y"] - p0["y"]) / (tb - ta)
    ts = t[None, :] - smp["dt"][:, None]                       # 시각 오차
    x = p0["x"] + vx * (ts - ta) + smp["e"][:, 0:1] + smp["dv"][:, 0:1] * np.maximum(t[None] - o.t_obs, 0)
    y = p0["y"] + vy * (ts - ta) + smp["e"][:, 1:2] + smp["dv"][:, 1:2] * np.maximum(t[None] - o.t_obs, 0)
    w0, w1 = o.window()
    act = (ts >= w0) & (ts <= w1)
    return x, y, act


def mc_collisions(sim, t_dep, obs, samples, stride=1):
    """개루프 궤적 1개 × 샘플 K 개 → 샘플별 충돌 여부(K,), 샘플별 최소 이격(K,)."""
    t = sim["t"][::stride] - t_dep
    X, Y, Z = sim["x"][::stride], sim["y"][::stride], sim["z"][::stride]
    K = samples[0]["e"].shape[0]
    hit = np.zeros(K, bool)
    clear = np.full(K, np.inf)
    for o, smp in zip(obs, samples):
        x, y, act = true_positions(o, smp, t)
        d = np.hypot(X[None] - x, Y[None] - y) - o.radius - R_DRONE
        inz = (Z >= o.z_min) & (Z <= o.z_max)
        d = np.where(act & inz[None], d, np.inf)
        hit |= (d <= 0).any(axis=1)
        clear = np.minimum(clear, d.min(axis=1))
    return hit, clear


def observe_fn(obs, samples, k, t_now_rel):
    """샘플 k 의 '실제' 상태를 시각 t 에 관측 → 이미 존재하는 장애물은 실제 위치·속도로 예측을 갱신하고
    불확실성을 재설정 (σ0 유지, t_obs = t). 아직 나타나지 않은 장애물은 공칭 예측 유지."""
    out = []
    for o, smp in zip(obs, samples):
        w0, w1 = o.window()
        ts_true = t_now_rel - smp["dt"][k]
        if ts_true < w0:
            out.append(o)
            continue
        x, y, _ = true_positions(o, {kk: v[k:k + 1] for kk, v in smp.items()}, np.array([t_now_rel]))
        p0, p1 = o.path[0], o.path[-1]
        vx = (p1["x"] - p0["x"]) / (p1["t"] - p0["t"]) + smp["dv"][k, 0]
        vy = (p1["y"] - p0["y"]) / (p1["t"] - p0["t"]) + smp["dv"][k, 1]
        t_end = w1 + smp["dt"][k]
        if t_end <= t_now_rel:
            continue
        c = copy.deepcopy(o)
        xn, yn = float(x[0, 0]), float(y[0, 0])
        c.path = [dict(x=xn, y=yn, t=t_now_rel), dict(x=xn + vx * (t_end - t_now_rel), y=yn + vy * (t_end - t_now_rel),
                                                       t=t_end)]
        c.t_start, c.t_end = t_now_rel, t_end
        c.t_obs = t_now_rel
        c.sigma_t = 0.0                       # 현재 위치를 관측했으므로 시각 오차 없음
        out.append(c)
    return out


def wilson(k, n, z=1.96):
    """이항 비율의 Wilson 95% 신뢰구간."""
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (p, max(c - h, 0.0), min(c + h, 1.0))
