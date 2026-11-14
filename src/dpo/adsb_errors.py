"""실측 ADS-B 항적으로 '등속 직선 예측'의 오차를 추정한다.

데이터: traffic 패키지(X. Olive, MIT 라이선스, PyPI 배포본)에 포함된 OpenSky Network 기반 실측 항적 샘플
        → data/raw/adsb/<분류>/*.json.gz  (응급 헬기, 파리 일대 일반 항공, 감시 비행, 글라이더, 측량·교정 비행 등)
전처리: 항공사 편명 제외, 기압고도 ≤ 6000 ft, 반복 보고된(갱신 안 된) 위치 제거, 위치 이상값 제거,
        15 s 넘게 끊기면 구간 분리. 기본은 1 Hz 로 기록된 항적만 사용(main) – 5~30 s 간격 자료는
        위치·시각 불일치가 커서 예측 오차를 과대평가한다(민감도 분석으로만 사용).
예측기: 관측 시각 t0 의 ADS-B 보고 속도(지면속도·방위)로 등속 직선 외삽  p̂(t0+τ) = p(t0) + v(t0) τ
        (침입기가 방송하는 상태로 계획기가 궤적을 예측하는 방식). 보고 방위가 위치 변화(직전 3 s)와
        45° 넘게 어긋나면 위치 변화로 추정한 속도를 쓴다.
오차  : e(τ) = p(t0+τ) − p̂(t0+τ), 예측 방향 기준 (진행 방향 along, 측면 cross) 좌표
"""
from __future__ import annotations

import glob
import math
import os
import re

import numpy as np

from . import config as C

RAW = C.ROOT / "data" / "raw" / "adsb"
TAU = np.arange(0.0, 180.0 + 1e-9, 5.0)           # 예측 시간 격자 [s]
KT = 0.514444
FT = 0.3048
AIRLINE = re.compile(r"^[A-Z]{3}\d")               # 항공사 편명 (저고도 공역 사용자 아님) 제외


def _read(path):
    import pandas as pd
    d = pd.read_json(path)
    col = lambda k: d[k].to_numpy() if k in d else np.full(len(d), np.nan)
    ts = pd.to_datetime(d["timestamp"], utc=True)
    return dict(t=(ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy(), icao=d["icao24"].astype(str).to_numpy(),
                cs=d["callsign"].fillna("").astype(str).str.strip().to_numpy() if "callsign" in d else np.full(len(d), ""),
                lat=col("latitude").astype(float), lon=col("longitude").astype(float),
                gs=col("groundspeed").astype(float) * KT, trk=col("track").astype(float), alt=col("altitude").astype(float) * FT)


def load_tracks(max_gap=15.0, min_len=60.0, alt_max=6000 * FT, exclude=("collections/switzerland",), rate="1hz"):
    """파일·기체·연속 구간별 항적 목록. rate='1hz' 이면 샘플 간격 중앙값 ≤ 2 s 인 구간만."""
    out = []
    for f in sorted(glob.glob(str(RAW / "*" / "*.json.gz"))):
        rel = os.path.relpath(f, RAW)
        if any(rel.startswith(e) for e in exclude):
            continue
        d = _read(f)
        cat = rel.split(os.sep)[0]
        for ic in np.unique(d["icao"]):
            m = (d["icao"] == ic) & np.isfinite(d["t"]) & np.isfinite(d["lat"]) & np.isfinite(d["lon"])
            if m.sum() < 5:
                continue
            o = np.argsort(d["t"][m], kind="stable")
            sub = {k: v[m][o] for k, v in d.items()}
            cs = next((c for c in sub["cs"] if c and c != "None"), "")
            if AIRLINE.match(cs):
                continue
            # 같은 시각 중복 + 갱신되지 않은 위치(직전과 같은 좌표가 반복 보고됨) 제거 → 위치가 바뀐 첫 시각만
            keep = np.r_[True, (np.diff(sub["t"]) > 0.5) & ((np.diff(sub["lat"]) != 0) | (np.diff(sub["lon"]) != 0))]
            sub = {k: v[keep] for k, v in sub.items()}
            lat0 = float(np.nanmean(sub["lat"]))
            x = (sub["lon"] - np.nanmean(sub["lon"])) * 111320.0 * math.cos(math.radians(lat0))
            y = (sub["lat"] - lat0) * 110540.0
            # 위치 이상값(직전 유효점 대비 120 m/s 초과 이동) 제거
            good = np.zeros(len(x), bool); good[0] = True; last = 0
            for q in range(1, len(x)):
                if math.hypot(x[q] - x[last], y[q] - y[last]) <= 120.0 * (sub["t"][q] - sub["t"][last]) + 30.0:
                    good[q] = True; last = q
            sub = {k: v[good] for k, v in sub.items()}; x, y = x[good], y[good]
            cut = np.where(np.diff(sub["t"]) > max_gap)[0] + 1
            for s_ in np.split(np.arange(len(x)), cut):
                if len(s_) < 5 or sub["t"][s_[-1]] - sub["t"][s_[0]] < min_len:
                    continue
                if rate == "1hz" and np.median(np.diff(sub["t"][s_])) > 2.0:
                    continue
                out.append(dict(src=rel, cat=cat, icao=ic, cs=cs, t=sub["t"][s_], x=x[s_], y=y[s_],
                                gs=sub["gs"][s_], trk=sub["trk"][s_], alt=sub["alt"][s_], alt_max=alt_max))
    return out


def velocity(tr, i, window=3.0):
    """관측 시각 i 의 예측 속도 (vx, vy): ADS-B 보고값, 위치 변화와 45° 넘게 어긋나면 위치 변화(직전 window s)."""
    t, x, y = tr["t"], tr["x"], tr["y"]
    j = int(np.searchsorted(t, t[i] - window, side="right")) - 1
    fd = None
    if j >= 0 and t[i] - t[j] <= window + 15.0:
        fd = ((x[i] - x[j]) / (t[i] - t[j]), (y[i] - y[j]) / (t[i] - t[j]))
    gs, trk = tr["gs"][i], tr["trk"][i]
    if np.isfinite(gs) and np.isfinite(trk):
        rv = (gs * math.sin(math.radians(trk)), gs * math.cos(math.radians(trk)))
        if fd is None or math.hypot(*fd) < 1.0:
            return rv
        dang = (math.degrees(math.atan2(rv[0], rv[1]) - math.atan2(fd[0], fd[1])) + 180) % 360 - 180
        if abs(dang) <= 45.0:
            return rv
    return fd


def prediction_errors(tracks, stride=10.0, v_min=10.0, v_max=75.0):
    """관측마다 예측 오차 궤적 e(τ) (along, cross) [m], τ ∈ TAU. 없는 τ 는 NaN.
    반환 dict(E[n, len(TAU), 2], V[n], cat[n], track[n], src[n], block[n], i0[n])."""
    E, V, CAT, TID, SRC, BLK, I0 = [], [], [], [], [], [], []
    for k, tr in enumerate(tracks):
        t, x, y = tr["t"], tr["x"], tr["y"]
        t_next = t[0]
        for i in range(len(t)):
            if t[i] < t_next:
                continue
            t_next = t[i] + stride
            alt = tr["alt"][i]
            if np.isfinite(alt) and alt > tr["alt_max"]:
                continue
            v = velocity(tr, i)
            if v is None:
                continue
            gs = math.hypot(*v)
            if not (v_min <= gs <= v_max):
                continue
            ux, uy = v[0] / gs, v[1] / gs
            tt = t[i] + TAU
            ok = tt <= t[-1]
            if ok.sum() < 4:
                continue
            ex = np.interp(tt, t, x) - (x[i] + v[0] * TAU); ey = np.interp(tt, t, y) - (y[i] + v[1] * TAU)
            e = np.stack([ex * ux + ey * uy, -ex * uy + ey * ux], -1)     # (along, cross)
            e[~ok] = np.nan
            E.append(e); V.append(gs); CAT.append(tr["cat"]); TID.append(k); I0.append(i)
            SRC.append(f"{tr['src']}|{tr['icao']}"); BLK.append(f"{tr['src']}|{tr['icao']}|{int(t[i] // 600)}")
    return dict(E=np.array(E), V=np.array(V), cat=np.array(CAT), track=np.array(TID), src=np.array(SRC),
                block=np.array(BLK), i0=np.array(I0), tau=TAU)


def split_blocks(keys, frac_test=0.5, seed=11):
    """10분 블록(기체·시간) 단위 무작위 학습/시험 분할. 반환 (train_keys, test_keys)."""
    keys = np.unique(keys)
    rng = np.random.default_rng(seed)
    test = set(rng.choice(keys, int(round(len(keys) * frac_test)), replace=False).tolist())
    return set(keys.tolist()) - test, test


def split_tracks(err, frac_test=0.5, seed=11):
    """관측별 학습/시험 마스크 (10분 블록 단위)."""
    _, test = split_blocks(err["block"], frac_test, seed)
    is_test = np.array([b in test for b in err["block"]])
    return ~is_test, is_test
