"""DJI 드론에서 얻을 수 있는 조건 전수 검토 → 왜 바람을 변인으로 고르는가
→ results/dji_conditions.json, 그림 F25

(1) DJI 공식 SDK 3종(Onboard SDK 토픽, Payload SDK 토픽, Mobile SDK V5 키)의 모든 항목을 역할별로 분류
(2) 물리량 단위로 묶어 다섯 질문(외생·영향 크기·시공간 변동·경로 의존·사전 지식)으로 선별
(3) CMU DJI M100 실측 비행으로 각 조건이 평균 전력·미터당 에너지를 얼마나 설명하는지 확인

사용: PYTHONPATH=src python scripts/dji_conditions_review.py
입력: data/raw/dji_sdk/*.tsv|txt (SDK 원문에서 추출, 출처는 같은 폴더 README), data/raw/cmu_m100_flight_level.csv
"""
from __future__ import annotations

import json
import os
import re

import numpy as np
import pandas as pd

from dpo import config as C

SDK = C.RAW / "dji_sdk"

# ------------------------------------------------------------------ 역할 분류
ROLES = {
    "OBJ": "목적·제약 상태 (배터리)",
    "DEC": "결정 변수와 그 결과 (위치·속도·자세·모터)",
    "ENV": "외생 환경 (바람·주변 비행체·장애물)",
    "FIX": "임무 전 고정 입력·설정",
    "MON": "상태·안전 감시",
    "ETC": "명령·조종 입력·식별",
}
GROUPS = {  # 묶음 → (역할, 한글 이름)
    "soc": ("OBJ", "배터리 잔량 (%, mAh)"), "power": ("OBJ", "배터리 전압·전류 (→ 전력)"),
    "battwarn": ("OBJ", "저전압 경고·귀환 판단"),
    "pos": ("DEC", "수평 위치"), "alt": ("DEC", "고도·높이"), "vel": ("DEC", "대지속도 벡터"),
    "att": ("DEC", "자세"), "acc": ("DEC", "가속도"), "gyro": ("DEC", "각속도"), "heading": ("DEC", "기수 방위"),
    "esc": ("DEC", "모터·ESC (전류·회전수·온도)"),
    "wind_speed": ("ENV", "풍속"), "wind_dir": ("ENV", "풍향"), "wind_warn": ("ENV", "강풍 경고 등급"),
    "traffic": ("ENV", "주변 유인기 (AirSense, ADS-B)"), "obst": ("ENV", "장애물 거리"),
    "home": ("FIX", "홈포인트·이륙 고도"), "limits": ("FIX", "고도·거리 제한, 귀환·경고 설정"),
    "aging": ("FIX", "배터리 노화 (완충 용량·방전 횟수)"), "batt_temp": ("FIX", "배터리 온도"),
    "history": ("FIX", "누적 비행 이력"),
    "mode": ("MON", "비행 상태·모드"), "gnss": ("MON", "GNSS·RTK 품질"), "fault": ("MON", "이상·오류 플래그"),
    "cmd": ("ETC", "명령 키 (이륙·착륙·귀환·보정 등)"), "rc": ("ETC", "조종기 입력"), "gimbal": ("ETC", "짐벌"),
    "ident": ("ETC", "식별·연결 정보"), "time": ("ETC", "시각·동기"),
}

TOPIC_RULES = [  # Onboard / Payload SDK 토픽 이름 → 묶음 (위에서부터 처음 맞는 규칙)
    (r"^QUATERNION$|^IMU_ATTI_NAVI", "att"), (r"^ACCELERATION_", "acc"), (r"^ANGULAR_RATE_", "gyro"),
    (r"^VELOCITY$|^GPS_VELOCITY$|^RTK_VELOCITY$", "vel"),
    (r"^ALTITUDE_OF_HOMEPOINT$|^HOME_POINT_", "home"), (r"^ALTITUDE_|^HEIGHT_", "alt"),
    (r"^GPS_FUSED$|^POSITION_FUSED$|^GPS_POSITION$|^RTK_POSITION$|^POSITION_VO$", "pos"),
    (r"^GPS_DATE$|^GPS_TIME$|^HARD_SYNC$", "time"),
    (r"^GPS_DETAILS$|^GPS_SIGNAL_LEVEL$|^GPS_CONTROL_LEVEL$|^RTK_POSITION_INFO$|^RTK_YAW_INFO$|^RTK_CONNECT_STATUS$", "gnss"),
    (r"^RTK_YAW$|^COMPASS$", "heading"), (r"^RC$|^RC_", "rc"), (r"GIMBAL", "gimbal"),
    (r"^STATUS_FLIGHT$|^STATUS_DISPLAYMODE$|^STATUS_LANDINGGEAR$|^CONTROL_DEVICE$", "mode"),
    (r"^STATUS_MOTOR_START_ERROR$|^FLIGHT_ANOMALY$", "fault"), (r"^BATTERY_", "soc"), (r"^ESC_DATA$", "esc"),
    (r"^AVOID_DATA$", "obst"),
]
MSDK_FC = {
    "pos": ["KeyAircraftLocation3D"], "alt": ["KeyUltrasonicHeight"], "vel": ["KeyAircraftVelocity"],
    "att": ["KeyAircraftAttitude"], "heading": ["KeyCompassHeading"], "time": ["KeyFlightTimeInSeconds"],
    "wind_speed": ["KeyWindSpeed"], "wind_dir": ["KeyWindDirection"], "wind_warn": ["KeyWindWarning"],
    "traffic": ["KeyAirSenseSystemSupported", "KeyAirSenseSystemInformation"],
    "home": ["KeyIsHomeLocationSet", "KeyHomeLocation", "KeyTakeoffLocationAltitude"],
    "limits": ["KeyHeightLimitRange", "KeyHeightLimit", "KeyDistanceLimitEnabled", "KeyDistanceLimitRange",
               "KeyDistanceLimit", "KeyGoHomeHeight", "KeyGoHomeHeightRange", "KeyFailsafeAction",
               "KeyLowBatteryWarningThreshold", "KeySeriousLowBatteryWarningThreshold", "KeyLowBatteryRTHEnabled",
               "KeyMultipleFlightModeEnabled", "KeyNavigationSatelliteSystemSource", "KeyCoordinatedTurnEnabled",
               "KeyESCBeepEnabled", "KeyLockMotors", "KeyLEDsSettings"],
    "history": ["KeyAircraftTotalFlightDuration", "KeyAircraftTotalFlightDistance", "KeyAircraftTotalFlightTimes"],
    "mode": ["KeyIsFlying", "KeyRemoteControllerFlightMode", "KeyFlightMode", "KeyGoHomeStatus",
             "KeyIsLandingConfirmationNeeded"],
    "gnss": ["KeyGPSSatelliteCount", "KeyGPSSignalLevel"],
    "fault": ["KeyCompassHasError", "KeyIsFailSafe", "KeyIsNearHeightLimit", "KeyIsNearDistanceLimit",
              "KeyIsCompassCalibrating", "KeyCompassCalibrationStatus", "KeyIMUCalibrationInfo"],
    "battwarn": ["KeyIsLowBatteryWarning", "KeyIsSeriousLowBatteryWarning", "KeyLowBatteryRTHInfo", "KeyGoHomeInfo"],
    "ident": ["KeyConnection", "KeyFlightLogIndex", "KeySerialNumber", "KeyFirmwareVersion", "KeyCompassCount",
              "KeyIMUCount", "KeyAccessLockerAllDeviceStatus"],
    "gimbal": ["KeyMultiGimbalSyncStatus"],
}
MSDK_BAT = {
    "soc": ["KeyChargeRemaining", "KeyChargeRemainingInPercent"],
    "power": ["KeyVoltage", "KeyCurrent", "KeyCellVoltages"], "batt_temp": ["KeyBatteryTemperature"],
    "aging": ["KeyFullChargeCapacity", "KeyNumberOfDischarges", "KeyBatteryManufacturedDate",
              "KeyBatteryHighVoltageStorageTime"],
    "ident": ["KeyConnection", "KeySerialNumber", "KeyFirmwareVersion", "KeyNumberOfCells"],
    "limits": ["KeyBatterySelfDischargeInDays"],
}


def classify():
    """SDK 항목 전부를 묶음에 배정한다. 빠진 항목이 있으면 오류."""
    rows = []
    for sdk, f in (("Onboard SDK", "osdk_topics.txt"), ("Payload SDK", "psdk_topics.txt")):
        for n in (SDK / f).read_text().split():
            g = next((grp for pat, grp in TOPIC_RULES if re.search(pat, n)), None)
            assert g, f"미분류 토픽 {n}"
            rows.append(dict(sdk=sdk, name=n, group=g))
    for sdk, f, table in (("Mobile SDK V5 · 비행제어기", "msdk_v5_flightcontroller_keys.tsv", MSDK_FC),
                          ("Mobile SDK V5 · 배터리", "msdk_v5_battery_keys.tsv", MSDK_BAT)):
        d = pd.read_csv(SDK / f, sep="\t").fillna("")
        inv = {k: g for g, ks in table.items() for k in ks}
        for r in d.itertuples():
            g = inv.get(r.key)
            if g is None:                      # 값이 아니라 동작을 실행하는 키
                assert r.action == "action" or "," in r.type, f"미분류 키 {r.key}"
                g = "cmd"
            rows.append(dict(sdk=sdk, name=r.key, group=g, desc=r.description))
        assert set(inv) <= set(d.key), set(inv) - set(d.key)
    for r in rows:
        r["role"] = GROUPS[r["group"]][0]
    return rows


# ------------------------------------------------------------------ 선별 표 (물리량 단위)
# 다섯 질문: Q1 외생인가 / Q2 에너지 영향이 큰가 / Q3 시간·공간에 따라 변하는가 /
#            Q4 경로를 바꾸면 작용이 달라지는가 / Q5 경로 전체에 대해 미리 알 수 있는가
# 값: 1 = 예, 0.5 = 부분적, 0 = 아니오, None = 해당 없음
def screening(cmu, B):
    f = B["factors"]
    wd = f["바람 방향 (10 m/s, 순풍↔역풍)"]
    pl, rho, spd = f["탑재중량 (0→0.75 kg)"], f["공기밀도 (35 °C·450 m ↔ −10 °C·50 m)"], f["속도 선택 (무풍, 최적↔10 m/s 고정)"]
    e = cmu["effects"]
    return [
        dict(name="풍속·풍향", src="MSDK KeyWindSpeed [dm/s], KeyWindDirection (지구 좌표)", q=[1, 1, 1, 1, 1],
             role="비용 변인 (바람장)",
             ev=f"모델: 10 m/s에서 방향에 따라 {wd['lo_pct']:+.0f} ~ {wd['hi_pct']:+.0f} %"),
        dict(name="강풍 경고 등급", src="MSDK KeyWindWarning, OSDK·PSDK FLIGHT_ANOMALY.strongWindLevel1/2", q=[1, 0.5, 1, 1, 0],
             role="안전 감시", ev="등급(2–3단계)만 제공, 현재 위치만"),
        dict(name="주변 유인기", src="MSDK KeyAirSenseSystemInformation (ADS-B 수신)", q=[1, 0, 1, 1, 0.5],
             role="제약 변인 (동적 장애물)", ev="에너지에는 우회·대기로만 작용"),
        dict(name="장애물 거리", src="OSDK·PSDK AVOID_DATA", q=[1, 0, 1, 1, 0.5],
             role="제약 (지형·건물 지도)", ev="감지 거리 수십 m, 계획에는 지도를 사용"),
        dict(name="탑재중량", src="DJI 키 없음 (주문 정보로 입력)", q=[1, 1, 0, 0, 1], role="고정 입력 (구간별 질량)",
             ev=f"실측: 0→500 g 평균 전력 {e['payload_0_500_pct']:+.0f} % · 모델: {pl['lo_pct']:+.0f} ~ {pl['hi_pct']:+.0f} %"),
        dict(name="공기밀도 (기온·기압)", src="직접 키 없음 (ALTITUDE_BAROMETER로 간접)", q=[1, 0.5, 0.5, 0.5, 1],
             role="고정 입력 (고도별 밀도)", ev=f"모델: {rho['lo_pct']:+.0f} ~ {rho['hi_pct']:+.0f} %"),
        dict(name="배터리 온도·노화", src="MSDK KeyBatteryTemperature, KeyFullChargeCapacity, KeyNumberOfDischarges",
             q=[1, None, 0, 0, 1], role="고정 입력 (용량 보정)", ev="모델 미반영 (향후 과제)"),
        dict(name="GNSS·RTK 품질", src="GPS_SIGNAL_LEVEL, GPS_DETAILS, RTK_*_INFO, KeyGPSSatelliteCount", q=[1, 0, 1, 0.5, 0],
             role="안전 감시", ev="에너지와 무관"),
        dict(name="배터리 잔량·전압·전류", src="BATTERY_INFO, BATTERY_SINGLE_INFO, KeyChargeRemainingInPercent, KeyVoltage, KeyCurrent",
             q=[0, None, None, None, None], role="목적·제약 (SOC)", ev="조건이 아니라 결과"),
        dict(name="대지속도·고도·위치", src="VELOCITY, ALTITUDE_FUSED, GPS_FUSED, KeyAircraftVelocity, KeyAircraftLocation3D",
             q=[0, 1, None, None, None], role="결정 변수 (경로·고도·속도)",
             ev=f"실측: 속도 4→12 m/s 미터당 에너지 {e['speed_4_12_spec_pct']:+.0f} % · 모델: 속도 고정 {spd['hi_pct']:+.0f} %"),
        dict(name="자세·가속도·각속도·모터", src="QUATERNION, ACCELERATION_*, ANGULAR_RATE_*, ESC_DATA, KeyAircraftAttitude",
             q=[0, None, None, None, None], role="제어 결과 (관측용)", ev="경로·바람이 정해지면 따라오는 값"),
        dict(name="비행 모드·이상 플래그", src="STATUS_FLIGHT, STATUS_DISPLAYMODE, FLIGHT_ANOMALY, KeyFlightMode",
             q=[0, None, None, None, None], role="안전 감시", ev="이산 상태"),
    ]


# ------------------------------------------------------------------ CMU 실측 비행
def cmu_analysis(nboot=2000, seed=0):
    d = pd.read_csv(C.RAW / "cmu_m100_flight_level.csv")
    n_all = len(d)
    d = d[(d.route == "R1") & (d.speed_prog_ms > 0) & d.alt_prog_num.notna() & (d.payload_g <= 500)].copy()
    d["v2"] = d.speed_prog_ms ** 2
    groups = {"탑재중량": ["payload_g"], "설정 속도": ["speed_prog_ms", "v2"], "설정 고도": ["alt_prog_num"],
              "풍속 (기체 풍속계, 스칼라)": ["mean_wind_speed_ms"], "배터리 전압": ["mean_voltage_v"]}
    full = [c for cs in groups.values() for c in cs]

    def r2(df, cols, y):
        X = np.column_stack([np.ones(len(df))] + [df[c].values for c in cols])
        b = np.linalg.lstsq(X, y, rcond=None)[0]
        return 1 - ((y - X @ b) ** 2).sum() / ((y - y.mean()) ** 2).sum(), b

    def range_effect(df, tgt):
        """다중 회귀에서 각 조건을 관측 범위(5→95 백분위)만큼 움직였을 때 목표값 변화 [% of mean]."""
        y = df[tgt].values
        _, b = r2(df, full, y)
        coef = dict(zip(full, b[1:]))
        out = {}
        for g, cols in groups.items():
            lo, hi = np.percentile(df[cols[0]], [5, 95])
            if g == "설정 속도":
                grid = np.linspace(lo, hi, 33)
                val = coef["speed_prog_ms"] * grid + coef["v2"] * grid ** 2
                out[g] = (val.max() - val.min()) / y.mean() * 100
            else:
                out[g] = coef[cols[0]] * (hi - lo) / y.mean() * 100
        return out

    rng = np.random.default_rng(seed)
    res = dict(n_all=n_all, n=len(d), filter="경로 R1, 설정 속도 > 0, 탑재 0·250·500 g", targets={})
    for tgt in ("mean_power_w", "specific_energy_j_m"):
        y = d[tgt].values
        r_full = r2(d, full, y)[0]
        drop = {g: r_full - r2(d, [c for c in full if c not in cols], y)[0] for g, cols in groups.items()}
        eff = range_effect(d, tgt)
        bd, be = {g: [] for g in groups}, {g: [] for g in groups}
        for _ in range(nboot):
            s = d.iloc[rng.integers(0, len(d), len(d))]
            ys = s[tgt].values
            rf = r2(s, full, ys)[0]
            ee = range_effect(s, tgt)
            for g, cols in groups.items():
                bd[g].append(rf - r2(s, [c for c in full if c not in cols], ys)[0])
                be[g].append(ee[g])
        res["targets"][tgt] = dict(
            r2_full=float(r_full), mean=float(y.mean()),
            drop_r2={g: dict(v=float(drop[g]), lo=float(np.percentile(bd[g], 2.5)), hi=float(np.percentile(bd[g], 97.5)))
                     for g in groups},
            range_effect_pct={g: dict(v=float(eff[g]), lo=float(np.percentile(be[g], 2.5)),
                                      hi=float(np.percentile(be[g], 97.5))) for g in groups})
    res["ranges"] = {g: [float(x) for x in np.percentile(d[cols[0]], [5, 95])] for g, cols in groups.items()}
    gp = d.groupby("payload_g")[["mean_power_w", "specific_energy_j_m"]].mean()
    gs = d.groupby("speed_prog_ms")[["mean_power_w", "specific_energy_j_m", "mean_wind_speed_ms", "max_wind_speed_ms"]].mean()
    ga = d.groupby("alt_prog_num")[["mean_power_w", "specific_energy_j_m"]].mean()
    res["by_payload"] = {str(int(k)): v for k, v in gp.round(2).to_dict("index").items()}
    res["by_speed"] = {str(int(k)): v for k, v in gs.round(2).to_dict("index").items()}
    res["by_alt"] = {str(int(k)): v for k, v in ga.round(2).to_dict("index").items()}
    res["effects"] = dict(
        payload_0_500_pct=float((gp.mean_power_w[500] / gp.mean_power_w[0] - 1) * 100),
        payload_0_500_spec_pct=float((gp.specific_energy_j_m[500] / gp.specific_energy_j_m[0] - 1) * 100),
        alt_25_100_pct=float((ga.mean_power_w[100] / ga.mean_power_w[25] - 1) * 100),
        speed_4_12_spec_pct=float((gs.specific_energy_j_m[12] / gs.specific_energy_j_m[4] - 1) * 100),
        speed_power_span_pct=float((gs.mean_power_w.max() / gs.mean_power_w.min() - 1) * 100))
    # 기체에 단 풍속계는 '바람 + 기체 속도'를 잰다: 설정 속도와의 상관
    res["wind_sensor"] = dict(
        mean=float(d.mean_wind_speed_ms.mean()), p5=float(d.mean_wind_speed_ms.quantile(.05)),
        p95=float(d.mean_wind_speed_ms.quantile(.95)), min=float(d.mean_wind_speed_ms.min()),
        max=float(d.mean_wind_speed_ms.max()),
        corr_mean_vs_speed=float(np.corrcoef(d.mean_wind_speed_ms, d.speed_prog_ms)[0, 1]),
        corr_max_vs_speed=float(np.corrcoef(d.max_wind_speed_ms, d.speed_prog_ms)[0, 1]),
        corr_std_vs_speed=float(np.corrcoef(d.std_wind_speed_ms, d.speed_prog_ms)[0, 1]),
        max_at_4=float(gs.max_wind_speed_ms[4]), max_at_12=float(gs.max_wind_speed_ms[12]),
        n_days=int(d.date.nunique()),
        day_mean_range=[float(d.groupby("date").mean_wind_speed_ms.mean().min()),
                        float(d.groupby("date").mean_wind_speed_ms.mean().max())])
    return res


# ------------------------------------------------------------------ 그림
def figure(R):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                         "mathtext.fontset": "dejavusans", "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 200, "savefig.bbox": "tight", "axes.titleweight": "bold"})
    GRAY, ORG, BLUE, NAVY, GRN, LIGHT = "#9E9E9E", "#E07B39", "#1B6CA8", "#0B3954", "#2CA58D", "#E8ECEF"
    fig = plt.figure(figsize=(17.5, 5.8))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.75, 0.95], wspace=0.40)

    # (a) SDK 항목 수 – 역할별
    ax = fig.add_subplot(gs[0])
    order = ["DEC", "MON", "FIX", "OBJ", "ENV", "ETC"]
    sdks = list(R["counts_by_sdk"])
    cols = [NAVY, BLUE, GRN, GRAY]
    left = np.zeros(len(order))
    for s, c in zip(sdks, cols):
        v = np.array([R["counts_by_sdk"][s].get(r, 0) for r in order])
        ax.barh(range(len(order)), v, left=left, color=c, label=s, height=0.62)
        left += v
    for i, r in enumerate(order):
        ax.text(left[i] + 1.2, i, f"{int(left[i])}", va="center", fontsize=10, color=NAVY, fontweight="bold")
    lab = {"DEC": "결정 변수와 그 결과\n(위치·속도·자세·모터)", "MON": "상태·안전 감시", "FIX": "고정 입력·설정",
           "OBJ": "배터리\n(목적·제약 상태)", "ENV": "외생 환경\n(바람·유인기·장애물)", "ETC": "명령·조종 입력·식별"}
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([lab[r] for r in order])
    ax.invert_yaxis()
    ax.set_xlim(0, left.max() * 1.18)
    ax.set_xlabel("SDK 항목 수")
    ax.set_title(f"(a) DJI SDK 항목 {R['n_total']}개의 역할")
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.42, -0.13), ncol=2)
    env = R["counts_by_group"]
    i_env = order.index("ENV")
    ax.annotate(f"바람 관련 {env['wind_speed'] + env['wind_dir'] + env['wind_warn']}개\n(풍속·풍향·경고)",
                xy=(left[i_env] + 5.5, i_env), xytext=(left[i_env] + 12, i_env), fontsize=9, color=ORG, va="center",
                fontweight="bold", arrowprops=dict(arrowstyle="-", color=ORG, lw=1))

    # (b) 선별 표
    ax = fig.add_subplot(gs[1])
    S = R["screening"]
    Q = ["외생\n조건", "에너지\n영향 큼", "시간·공간\n변동", "경로별\n작용 차이", "사전에\n파악 가능"]
    sym = {1: ("✔", GRN), 0.5: ("△", "#C9A227"), 0: ("✘", "#B0B7BD"), None: ("–", "#D5DADF")}
    for i, row in enumerate(S):
        allyes = all(q == 1 for q in row["q"])
        if allyes:
            ax.add_patch(plt.Rectangle((-0.5, i - 0.5), len(Q) + 2.6, 1, color="#FBE9DD", lw=0))
        for j, q in enumerate(row["q"]):
            s, c = sym[q]
            ax.text(j, i, s, ha="center", va="center", fontsize=13, color=c, fontweight="bold",
                    fontfamily="DejaVu Sans")
        ax.text(len(Q) - 0.35, i, row["role"], ha="left", va="center", fontsize=9,
                color=ORG if allyes else NAVY, fontweight="bold" if allyes else "normal")
    ax.set_xlim(-0.5, len(Q) + 2.1)
    ax.set_ylim(len(S) - 0.5, -0.5)
    ax.set_xticks(range(len(Q)))
    ax.set_xticklabels(Q, fontsize=9)
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(S)))
    ax.set_yticklabels([r["name"] for r in S], fontsize=9.5)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    for i in range(len(S) + 1):
        ax.axhline(i - 0.5, color=LIGHT, lw=0.8)
    ax.text(len(Q) - 0.35, -0.95, "본 연구에서의 역할", fontsize=8.5, va="center", color="#555")
    ax.set_title("(b) 다섯 질문으로 선별: 모두 '예'인 조건은 바람뿐", y=1.11)

    # (c) CMU 실측: 관측 범위만큼 바꿨을 때 변화
    ax = fig.add_subplot(gs[2])
    T = R["cmu"]["targets"]
    names = list(T["mean_power_w"]["range_effect_pct"])
    rg = R["cmu"]["ranges"]
    unit = {"탑재중량": "{:.0f}–{:.0f} g", "설정 속도": "{:.0f}–{:.0f} m/s", "설정 고도": "{:.0f}–{:.0f} m",
            "풍속 (기체 풍속계, 스칼라)": "{:.1f}–{:.1f} m/s", "배터리 전압": "{:.1f}–{:.1f} V"}
    short = {"풍속 (기체 풍속계, 스칼라)": "풍속 (스칼라)"}
    y = np.arange(len(names))
    for k, (tgt, c, lb) in enumerate((("mean_power_w", NAVY, "평균 전력"), ("specific_energy_j_m", ORG, "미터당 에너지"))):
        v = np.array([abs(T[tgt]["range_effect_pct"][n]["v"]) for n in names])
        lo = np.array([T[tgt]["range_effect_pct"][n]["lo"] for n in names])
        hi = np.array([T[tgt]["range_effect_pct"][n]["hi"] for n in names])
        sg = np.sign([T[tgt]["range_effect_pct"][n]["v"] for n in names])
        a, b = np.minimum(lo * sg, hi * sg), np.maximum(lo * sg, hi * sg)
        ax.barh(y + (k - 0.5) * 0.36, v, height=0.34, color=c, label=lb,
                xerr=[np.clip(v - a, 0, None), np.clip(b - v, 0, None)], error_kw=dict(ecolor="#555", lw=1, capsize=2))
    ax.set_yticks(y)
    ax.set_yticklabels([f"{short.get(n, n)}\n{unit[n].format(*rg[n])}" for n in names], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("관측 범위만큼 바꿨을 때 변화의 크기 [%]")
    ax.set_title(f"(c) 실측 비행 {R['cmu']['n']}회: 무엇이 에너지를 바꾸나")
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.savefig(C.FIG / "F25_dji_conditions.png")
    plt.close(fig)


def main():
    rows = classify()
    B = json.loads((C.RES / "background.json").read_text())["B"]
    cmu = cmu_analysis()
    by_sdk, by_group, by_role = {}, {}, {}
    for r in rows:
        by_sdk.setdefault(r["sdk"], {}).setdefault(r["role"], 0)
        by_sdk[r["sdk"]][r["role"]] += 1
        by_group[r["group"]] = by_group.get(r["group"], 0) + 1
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    for g in GROUPS:
        by_group.setdefault(g, 0)
    R = dict(n_total=len(rows), n_by_sdk={s: sum(v.values()) for s, v in by_sdk.items()}, counts_by_sdk=by_sdk,
             counts_by_role=by_role, counts_by_group=by_group, roles=ROLES,
             groups={g: dict(role=v[0], name=v[1], n=by_group[g],
                             items=[f"{r['sdk'].split(' ')[0]}:{r['name']}" for r in rows if r["group"] == g])
                     for g, v in GROUPS.items()},
             screening=screening(cmu, B), cmu=cmu,
             wind_sources=dict(
                 msdk=["KeyWindSpeed (Integer, dm/s)", "KeyWindDirection (WindDirection, 지구 좌표)",
                       "KeyWindWarning (WindWarning, 풍속 등급)"],
                 osdk_psdk=["FLIGHT_ANOMALY.strongWindLevel1", "FLIGHT_ANOMALY.strongWindLevel2"],
                 note="Onboard·Payload SDK에는 풍속·풍향 수치 토픽이 없고 강풍 플래그 2개만 있다."))
    (C.RES / "dji_conditions.json").write_text(json.dumps(R, ensure_ascii=False, indent=1))
    figure(R)
    print("항목", R["n_total"], R["n_by_sdk"])
    print("역할", {ROLES[k]: v for k, v in by_role.items()})
    for tgt, t in cmu["targets"].items():
        print(tgt, "R2", round(t["r2_full"], 3))
        for g in t["drop_r2"]:
            print(f"   {g:24s} ΔR² {t['drop_r2'][g]['v']:.3f} [{t['drop_r2'][g]['lo']:.3f},{t['drop_r2'][g]['hi']:.3f}]"
                  f"  범위효과 {t['range_effect_pct'][g]['v']:+.1f}% [{t['range_effect_pct'][g]['lo']:+.1f},{t['range_effect_pct'][g]['hi']:+.1f}]")
    print(cmu["effects"], cmu["wind_sensor"], cmu["ranges"])


if __name__ == "__main__":
    main()
