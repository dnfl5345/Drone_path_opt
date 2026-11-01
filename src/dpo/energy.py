"""회전익(멀티콥터) 소비전력 모델 + 바람/탑재중량 반영 + 실측 데이터 보정.

근거
- Leishman, *Principles of Helicopter Aerodynamics* (운동량 이론: 유도/형상/유해 동력)
- Zeng, Xu, Zhang (2019) IEEE TWC, rotary-wing UAV 전진비행 전력모델
- 보정·검증 데이터: Rodrigues et al. (2021) Scientific Data 8:155,
  DJI Matrice 100 소형 택배 비행 209회 (CMU KiltHub, CC BY 4.0)

전력 [W] (공기기준 수평속도 Va, 상승률 Vz, 총질량 m):
  D      = ½ ρ CdA Va²                              (동체 항력)
  T      = √((m g)² + D²)                           (요구 추력)
  v_h    = √(T / (2 ρ A))                           (호버 유도속도)
  v_i    = √(√(Va⁴/4 + v_h⁴) − Va²/2)              (전진비행 유도속도)
  P_ind  = k · T · v_i                              ← 탑재중량 ↑ → m^1.5 로 증가
  P_pro  = P0 · (1 + 3 Va²/U_tip²) · ρ/ρ0           (블레이드 형상(마찰) 동력)
  P_par  = D · Va                                   (유해(공기저항) 동력)
  P_clb  = m g Vz  (하강 시 일부만 회수: c_d·m g Vz)
  P_elec = (P_ind + P_pro + P_par + P_clb) / η + P_av
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
from numba import njit

G = 9.80665
RHO0 = 1.225


@dataclass
class Drone:
    name: str = "DJI Matrice 100 (CMU 실측 보정)"
    m_empty: float = 3.10       # 기체+배터리+탑재장치 [kg] (DJI 사양 2.43 kg + 센서/장치 가정)
    rotor_r: float = 0.1651     # 13" 프로펠러 반경 [m]
    n_rotor: int = 4
    u_tip: float = 104.0        # 블레이드 팁 속도 [m/s]
    k_ind: float = 1.15         # 유도동력 보정계수
    eta: float = 0.62           # 모터·ESC·프로펠러 종합효율 (보정 대상)
    p0: float = 35.0            # 형상동력 [W] (보정 대상)
    cda: float = 0.10           # 동체 항력면적 Cd·A [m²] (보정 대상)
    p_av: float = 15.0          # 항전·탑재장비 [W] (보정 대상)
    c_desc: float = 0.25        # 하강 위치에너지 회수 비율
    battery_wh: float = 99.9    # 배터리 공칭에너지 [Wh] (DJI TB47D 6S 4500 mAh 가정)
    usable_frac: float = 0.95   # 사용 가능 비율 (전압 강하/BMS 컷오프)
    va_max: float = 17.0        # 최대 대기속도 [m/s]
    vz_up: float = 3.0          # 상승률 [m/s]
    vz_dn: float = 2.0          # 하강률 [m/s]

    @property
    def area(self):
        return self.n_rotor * math.pi * self.rotor_r ** 2

    def params(self):
        """numba 함수용 파라미터 배열."""
        return np.array([self.area, self.u_tip, self.k_ind, self.eta, self.p0, self.cda,
                         self.p_av, self.c_desc], dtype=np.float64)

    def to_dict(self):
        d = asdict(self)
        d["area"] = self.area
        return d


@njit(cache=True, fastmath=True)
def power_w(va, vz, m, rho, prm):
    """전기 소비전력 [W]. va: 수평 대기속도, vz: 상승률(+)/하강률(-)."""
    area, u_tip, k_ind, eta, p0, cda, p_av, c_desc = prm[0], prm[1], prm[2], prm[3], prm[4], prm[5], prm[6], prm[7]
    w = m * G
    d = 0.5 * rho * cda * va * va
    t = math.sqrt(w * w + d * d)
    vh2 = t / (2.0 * rho * area)
    vi = math.sqrt(max(math.sqrt(va ** 4 / 4.0 + vh2 * vh2) - va * va / 2.0, 1e-9))
    p_ind = k_ind * t * vi
    p_pro = p0 * (1.0 + 3.0 * va * va / (u_tip * u_tip)) * rho / RHO0
    p_par = d * va
    if vz >= 0:
        p_clb = w * vz
    else:
        p_clb = c_desc * w * vz
    p_mech = p_ind + p_pro + p_par + p_clb
    p_mech = max(p_mech, 0.5 * (p_ind + p_pro))
    return p_mech / eta + p_av


@njit(cache=True, fastmath=True)
def edge_energy_per_m(dx, dy, dz, wu, wv, m, rho, prm, va_max, vz_up, vz_dn, speeds):
    """한 구간(방향 dx,dy,dz 정규화 전 길이 포함)의 '미터당 에너지' 최소화.

    대지속도 Vg(후보 speeds)를 바꿔가며 J/m = P/Vg 를 최소로 하는 값을 고른다.
    바람 w=(wu,wv) 에서 지면 궤적을 유지하려면 대기속도 벡터 = Vg·ê − w
    (측풍은 기수를 틀어 상쇄 → 남는 전진속도 ± 순풍/역풍).
    반환: (J/m, 선택 대지속도[m/s]) — 불가능하면 (inf, 0)
    """
    lh = math.sqrt(dx * dx + dy * dy)
    L = math.sqrt(lh * lh + dz * dz)
    if L < 1e-9:
        return 0.0, 0.0
    best = 1e18
    bv = 0.0
    if lh < 1e-6:  # 순수 수직 이동: 바람 속 호버링 + 상승/하강
        vz = vz_up if dz > 0 else -vz_dn
        p = power_w(math.sqrt(wu * wu + wv * wv), vz, m, rho, prm)
        return p / abs(vz), abs(vz)
    ex, ey = dx / lh, dy / lh
    slope = dz / lh
    for k in range(speeds.shape[0]):
        vg = speeds[k]                # 수평 대지속도
        vz = vg * slope
        if vz > vz_up or vz < -vz_dn:
            continue
        ax = vg * ex - wu
        ay = vg * ey - wv
        va = math.sqrt(ax * ax + ay * ay)
        if va > va_max:
            continue
        p = power_w(va, vz, m, rho, prm)
        e = p / (vg * math.sqrt(1.0 + slope * slope))  # J per 3D meter
        if e < best:
            best = e
            bv = vg
    if best >= 1e17:
        return np.inf, 0.0
    return best, bv


def air_density(z_msl, t_c=15.0):
    """국제표준대기 + 지상기온 보정."""
    t = (t_c + 273.15) - 0.0065 * z_msl
    p = 101325.0 * (1 - 2.25577e-5 * z_msl) ** 5.25588
    return p / (287.05 * t)


# ------------------------------------------------------------------ 보정(calibration)
CMU_URL = ("https://raw.githubusercontent.com/Noice-Nishtha/UAV-Flight-ML/main/"
           "data/processed/flight_level_master.csv")


def load_cmu():
    import pandas as pd
    import requests
    from . import config as C

    p = C.RAW / "cmu_m100_flight_level.csv"
    if not p.exists():
        r = requests.get(CMU_URL, timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
    d = pd.read_csv(p)
    d = d[(d.total_energy_j > 0) & (d.duration_s > 30) & d.alt_prog_num.notna()].copy()
    return d


def predict_flight_energy(df, drone: Drone, n_theta=24):
    """비행 단위 에너지 예측: 순항 + 수직 + (바람 속) 호버 구간 분해.

    순항 구간 대기속도는 풍향-경로각이 미지이므로 왕복 경로 가정하에 0~360° 평균.
    """
    prm = drone.params()
    out = np.zeros(len(df))
    th = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    for i, r in enumerate(df.itertuples()):
        m = drone.m_empty + r.payload_g / 1000.0
        T = r.duration_s
        w = r.mean_wind_speed_ms
        dh = r.mean_horiz_speed_ms * T
        dv = r.mean_vert_speed_ms * T
        v = max(r.speed_prog_ms, 0.0)
        t_cr = min(dh / v, 0.8 * T) if v > 0.5 else 0.0
        t_up = 0.5 * dv / drone.vz_up
        t_dn = 0.5 * dv / drone.vz_dn
        t_hv = max(T - t_cr - t_up - t_dn, 0.0)
        if t_cr > 0:
            pc = np.mean([power_w(math.hypot(v * math.cos(a) - w, v * math.sin(a)), 0.0, m, RHO0, prm)
                          for a in th])
        else:
            pc = 0.0
        p_hv = power_w(w, 0.0, m, RHO0, prm)
        p_up = power_w(w, drone.vz_up, m, RHO0, prm)
        p_dn = power_w(w, -drone.vz_dn, m, RHO0, prm)
        out[i] = t_cr * pc + t_hv * p_hv + t_up * p_up + t_dn * p_dn
    return out


def calibrate(df_train, drone: Drone | None = None):
    """비선형 최소제곱으로 η, P0, CdA, P_av 보정 (질량은 DJI 사양 기반 고정 3.1 kg).

    질량·효율·형상동력을 동시에 풀면 서로 상쇄되어 식별 불가(비물리적 해)가 되므로
    질량은 사양값으로 고정하고 나머지만 물리적 범위 안에서 추정한다.
    """
    from scipy.optimize import least_squares

    drone = drone or Drone()
    y = df_train.total_energy_j.values

    def res(x):
        return (predict_flight_energy(df_train, _mk(drone, x)) - y) / y

    sol = least_squares(res, x0=[0.65, 40.0, 0.10, 15.0],
                        bounds=([0.5, 20.0, 0.03, 5.0], [0.85, 120.0, 0.4, 40.0]))
    return _mk(drone, sol.x)


def _mk(drone, x):
    return Drone(**{**drone.__dict__, "eta": float(x[0]), "p0": float(x[1]), "cda": float(x[2]),
                    "p_av": float(x[3])})


def soc_drop(e_j, drone: Drone, p_mean=400.0, v_nom=22.2, r_int=0.03):
    """④ 예상 SOC 감소량 [%p] = 소비에너지 / 배터리 기준에너지 × 100.
    배터리 내부저항 손실(I²R)을 평균전력 기준 계수로 반영."""
    loss = 1.0 + p_mean * r_int / v_nom ** 2
    return e_j * loss / (drone.battery_wh * 3600.0) * 100.0


def metrics(y, yhat):
    e = yhat - y
    mape = np.mean(np.abs(e) / y) * 100
    rmse = np.sqrt(np.mean(e ** 2))
    r2 = 1 - np.sum(e ** 2) / np.sum((y - y.mean()) ** 2)
    return dict(MAPE=mape, RMSE_Wh=rmse / 3600, R2=r2)
