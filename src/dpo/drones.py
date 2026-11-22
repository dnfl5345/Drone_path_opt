"""기체 프리셋.

- m100 : DJI Matrice 100 – CMU 실측 비행으로 보정한 전력 모델 (results/drone_params.json)
- m4t  : DJI Matrice 4T – 실증 기체. DJI 공식 사양(무탑재 호버 42분, 9 m/s 정속 49분, 99 Wh)으로 맞춘 모델 (아래 m4t()).
- x650 : Holybro X650 (ArduPilot, Pixhawk 6X) – 시험비행 기체 (2026-11-13 비행).
         제원은 첨부된 '초경량비행장치 제원 및 성능표'·'실증 드론 부품 구성표'에서 가져왔다.
         전력 모델은 아직 비행 자료로 보정하지 않은 **추정**이다 (아래 x650() 참고).
"""
from __future__ import annotations

import json
import math

from . import config as C
from .energy import G, RHO0, Drone, power_w

# 첨부 문서에서 읽은 X650 제원 (출처 표기용으로도 쓴다)
X650_SPEC = dict(
    frame="Holybro X650 (쿼드콥터, 휠베이스 650 mm)",
    fc="Holybro Pixhawk 6X + ArduCopter (MAVLink2, AUTO·RTL·Geofence)",
    motor="T-Motor MN4014 KV330 × 4", esc="Holybro Tekko32 F4 45A", prop="1555 카본 (15×5.5 in)",
    battery="Tattu LiPo 6S 10,000 mAh (22.2 V, 222 Wh)", power_module="Holybro PM02D (전압·전류)",
    gnss="Holybro H-RTK F9P (u-blox ZED-F9P, RTK 8 Hz)", radio="Skydroid H12 Pro / R12 PRO (2.4 GHz)",
    camera="Skydroid C11 3축 짐벌",
    mtow_kg=6.3, empty_kg=4.1, battery_wh=222.0, hover_min_no_payload=30.0, prop_in=15.0, n_units=3)

# ArduCopter 기본값 (AC_WPNav.cpp: WP_SPD 10 m/s, WP_SPD_UP 2.5 m/s, WP_SPD_DN 1.5 m/s)
ARDUCOPTER_DEFAULTS = dict(WP_SPD=10.0, WP_SPD_UP=2.5, WP_SPD_DN=1.5, WP_RADIUS_M=2.0)


def m100() -> Drone:
    p = C.RES / "drone_params.json"
    dd = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return Drone(**{k: v for k, v in dd.items() if k in Drone.__dataclass_fields__})


def x650(hover_min: float | None = None, p0: float = 30.0, p_av: float = 10.0, cda: float = 0.08,
         va_max: float = 15.0, vg_max: float = 15.0) -> Drone:
    """Holybro X650 전력 모델 (추정).

    제원에서 직접 정한 값
      - 질량 4.1 kg(자체무게), 프로펠러 15 in × 4 → 회전면 면적, 배터리 222 Wh
      - 상승·하강률: ArduCopter 기본값 (WP_SPD_UP 2.5, WP_SPD_DN 1.5 m/s)
    가정한 값 (비행 자료로 보정 필요)
      - 형상동력 p0 30 W: M100 보정값(68.6 W)을 회전면 면적비(1.33)와 팁 속도비의 세제곱(0.33)으로 환산
      - 팁 속도 72 m/s: 추력계수 0.11을 가정한 호버 회전수(약 3,600 rpm)
      - 동체 항력면적 0.08 m², 항전 10 W, 최대 대기속도 15 m/s, 대지속도 상한 15 m/s(실증 운용 여유)
    효율 η 는 '무탑재 호버 약 30분(제조사)'에 맞춘다:
      호버 30분에 SOC 100 %p 가 줄도록 (soc_drop 과 같은 손실 계수 사용) η 를 푼다.
    """
    hover_min = X650_SPEC["hover_min_no_payload"] if hover_min is None else hover_min
    d = Drone(name="Holybro X650 (제원 기반 추정, 미보정)", m_empty=X650_SPEC["empty_kg"],
              rotor_r=X650_SPEC["prop_in"] * 0.0254 / 2, n_rotor=4, u_tip=72.0, k_ind=1.15, eta=0.75, p0=p0, cda=cda,
              p_av=p_av, c_desc=0.25, battery_wh=X650_SPEC["battery_wh"], usable_frac=0.95, va_max=va_max,
              vz_up=ARDUCOPTER_DEFAULTS["WP_SPD_UP"], vz_dn=ARDUCOPTER_DEFAULTS["WP_SPD_DN"], vg_max=vg_max)
    loss = 1.0 + 400.0 * 0.03 / 22.2 ** 2                       # energy.soc_drop 의 내부저항 손실 계수
    p_hover = d.battery_wh * 3600.0 / (hover_min * 60.0 * loss)  # 호버 전기 전력 [W]
    w = d.m_empty * G
    p_mech = d.k_ind * w * math.sqrt(w / (2.0 * RHO0 * d.area)) + d.p0
    d.eta = p_mech / (p_hover - d.p_av)
    assert 0.5 < d.eta < 0.9, d.eta
    assert abs(power_w(0.0, 0.0, d.m_empty, RHO0, d.params()) - p_hover) < 0.5
    return d


# DJI Matrice 4T 공식 사양 (enterprise.dji.com Matrice 4 Series Specs, 배터리 BPX345-6741-14.76)
M4T_SPEC = dict(
    takeoff_kg=1.219, mtow_kg=1.420, payload_max_kg=0.200, prop_in=10.8, wheelbase_mm=438.8,
    battery_wh=99.0, battery_v=14.76, battery_mah=6741, battery_type="Li-ion 4S",
    hover_min=42.0,          # 무풍 해수면 호버, 100 % → 0 %
    flight_min=49.0,         # 무탑재 약 9 m/s 정속, 무풍, 100 % → 0 %
    flight_speed=9.0,
    range_km=35.0,           # 측정 조건 미확인 → 보정에 쓰지 않고 점검용으로만 비교
    v_h_max=21.0, wind_max=12.0, climb_max=10.0, descent_max=8.0)


def m4t(p0: float = 20.0, p_av: float = 15.0, va_max: float = 18.0, vg_max: float = 15.0) -> Drone:
    """DJI Matrice 4T 전력 모델 (공식 사양 두 점으로 보정).

    제원에서 정한 값: 질량 1.219 kg(이륙 무게), 10.8 in 프로펠러 4개 → 회전면 면적, 배터리 99 Wh
    가정한 값: 형상동력 p0 20 W, 항전·카메라 15 W, 팁 속도 110 m/s, 상승·하강 3 / 2 m/s(운용 값),
             대기속도 상한 18 m/s(최대 수평 21 m/s 아래), 대지속도 상한 15 m/s(운용 여유, DJI 경로 파일 상한과 같음)
    보정: 효율 η 와 동체 항력면적 CdA 를 두 식에 맞춘다
      ① 무풍 호버 42분에 SOC 100 %p 소모   ② 9 m/s 정속 49분에 SOC 100 %p 소모   (soc_drop 과 같은 손실 계수)
    점검: 이 모델의 최대 거리는 약 29 km 로 DJI 표기 35 km 보다 짧다 (35 km 의 측정 조건은 확인하지 못함) →
          고속 구간 전력을 크게 보는 쪽(보수적)이다.
    """
    from scipy.optimize import least_squares
    S = M4T_SPEC
    loss = 1.0 + 400.0 * 0.03 / 22.2 ** 2                        # energy.soc_drop 의 내부저항 손실 계수
    p_h = S["battery_wh"] * 3600.0 / (S["hover_min"] * 60.0 * loss)
    p_f = S["battery_wh"] * 3600.0 / (S["flight_min"] * 60.0 * loss)
    d = Drone(name="DJI Matrice 4T (공식 사양 보정)", m_empty=S["takeoff_kg"], rotor_r=S["prop_in"] * 0.0254 / 2, n_rotor=4,
              u_tip=110.0, k_ind=1.15, eta=0.7, p0=p0, cda=0.04, p_av=p_av, c_desc=0.25, battery_wh=S["battery_wh"],
              usable_frac=0.95, va_max=va_max, vz_up=3.0, vz_dn=2.0, vg_max=vg_max)

    def f(x):
        d.eta, d.cda = float(x[0]), float(x[1])
        pr = d.params()
        return [power_w(0.0, 0.0, d.m_empty, RHO0, pr) / p_h - 1.0, power_w(S["flight_speed"], 0.0, d.m_empty, RHO0, pr) / p_f - 1.0]

    r = least_squares(f, [0.7, 0.04], bounds=([0.3, 0.0], [0.95, 0.3]))
    d.eta, d.cda = float(r.x[0]), float(r.x[1])
    assert max(abs(v) for v in f(r.x)) < 1e-3, r.fun
    assert 0.5 < d.eta < 0.9 and 0.01 < d.cda < 0.1, (d.eta, d.cda)
    return d


PAYLOAD_MAX = dict(m100=0.75, x650=round(X650_SPEC["mtow_kg"] - X650_SPEC["empty_kg"], 2), m4t=M4T_SPEC["payload_max_kg"])
PAYLOAD_DEFAULT = dict(m100=0.5, x650=1.0, m4t=0.2)
LABEL = dict(m4t="DJI Matrice 4T (실증 기체, 공식 사양 보정)", m100="DJI Matrice 100 (실측 보정)",
             x650="Holybro X650 (시험비행 기체, 추정)")


def calibration_file(key: str):
    """비행 로그로 보정한 계수 파일의 위치 (dpo-calibrate 가 쓴다): DPO_HOME/calibration/<기체>.json"""
    from . import config as C
    return C.ROOT / "calibration" / f"{key}.json"


def get(key: str, calibrated: bool = True) -> Drone:
    """기체 모델. calibrated=True 이고 보정 파일이 있으면 그 계수(η, P0, CdA, 항전 전력)를 쓴다."""
    if key == "m100":
        d = m100()
    elif key == "x650":
        d = x650()
    elif key == "m4t":
        d = m4t()
    else:
        raise ValueError(f"알 수 없는 기체: {key}")
    f = calibration_file(key)
    if calibrated and f.exists():
        import json
        c = json.loads(f.read_text(encoding="utf-8"))
        for k in ("eta", "p0", "cda", "p_av"):
            setattr(d, k, float(c["params"][k]))
        d.name = f"{d.name.split(' (')[0]} (비행 로그 보정 {c.get('date', '')})"
    return d
