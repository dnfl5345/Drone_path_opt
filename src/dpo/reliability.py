"""신뢰도 평가 – 과거 시점의 자료(예보)로 계획한 경로가 실제 비행에서도 성립할 확률을 따진다.

배경 (docs/reliability.md, 실험 E16)
  계획에 쓰는 바람은 언제나 과거에 발표된 예보다. 예보가 오래될수록 실제 바람과 더 다르다.
  이 모듈은 그 차이를 '영역 전체에 같은 바람 편차 (du, dv)' 로 보고, 편차의 크기 σ 를 자료의 나이(예보 선행 시간)와
  계절의 함수로 둔다. σ 는 GFS 표본(12개월 × 3일)에서 잰 값이다 (results/wind_error_model.json).

구성
  WindErrorModel          σ(선행 시간, 계절) – 성분별 표준편차 [m/s]
  mission_confidence      편차를 몬테카를로로 뽑아 계획 경로의 SOC 소모 분포·분위수·임무 성립 확률을 구한다
  go_no_go                요구 신뢰수준에서의 출발 판정 (go / caution / no-go)
  robust_choice           후보 경로 가운데 요구 신뢰수준의 SOC 소모 분위수가 가장 작은 것을 고른다
  seasonal_recommendation 월·시각별 권고 (설정, SOC 여유) – results/seasonal_table.json 에서 읽는다

한계: σ 는 관측이 아니라 예보끼리의 차이에서 잰 값이고, 편차가 영역 전체에 같다고 가정한다 (4–6 km 지도는
GFS 격자 한 칸보다 작다). 지형·건물 자료의 나이는 다루지 않는다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .energy import soc_drop
from .planner import simulate

SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
SEASON_KO = {"DJF": "겨울", "MAM": "봄", "JJA": "여름", "SON": "가을"}
LEVELS = (50, 90, 95, 99)

# 자료 파일이 없을 때 쓰는 기본값: E16 에서 잰 전체 평균(6·12·30·54·78 h: 0.31·0.40·1.00·1.33·1.74 m/s)에
# 덮임률 보정 배율 1.25 를 곱해 올림한 값. [선행 시간 h] → 성분별 σ [m/s]
DEFAULT_MODEL = dict(leads_h=[6.0, 12.0, 30.0, 54.0, 78.0], sigma=[0.5, 0.6, 1.3, 1.7, 2.2], by_season={}, scale=1.0,
                     source="built-in default (E16 전체 평균 × 1.25 를 올림한 값)")


def season_of(month: int) -> str:
    for k, ms in SEASONS.items():
        if int(month) in ms:
            return k
    raise ValueError(f"월은 1–12: {month}")


class WindErrorModel:
    """예보 바람 오차의 성분별 표준편차 σ [m/s] = f(자료의 나이 [h], 계절).

    leads_h, sigma : 잰 선행 시간과 그때의 σ (전체)
    by_season      : {"DJF": [σ…], …} 같은 선행 시간에서의 계절별 σ (없으면 전체 값을 쓴다)
    scale          : 덮임률 검증에서 정한 보정 배율 (σ 에 곱한다)
    """

    def __init__(self, leads_h, sigma, by_season=None, scale=1.0, source=""):
        self.leads_h = [float(v) for v in leads_h]
        self.sigma_all = [float(v) for v in sigma]
        self.by_season = {k: [float(v) for v in vs] for k, vs in (by_season or {}).items()}
        self.scale = float(scale)
        self.source = source
        if len(self.leads_h) != len(self.sigma_all) or len(self.leads_h) < 1:
            raise ValueError("leads_h 와 sigma 의 길이가 같아야 합니다")

    @classmethod
    def load(cls, path=None):
        """results/wind_error_model.json 을 읽는다. 없으면 기본값."""
        if path is None:
            from . import config as C
            path = C.RES / "wind_error_model.json"
        path = Path(path)
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls(d["leads_h"], d["sigma"], d.get("by_season"), d.get("scale", 1.0), d.get("source", str(path.name)))
        return cls(**DEFAULT_MODEL)

    def to_dict(self):
        return dict(leads_h=self.leads_h, sigma=self.sigma_all, by_season=self.by_season, scale=self.scale, source=self.source)

    def sigma(self, lead_h, season=None, month=None, scaled=True):
        """자료의 나이 lead_h [h] 에서의 성분별 σ [m/s]. 잰 범위 안은 선형 보간, 밖은 마지막 기울기로 늘린다.
        나이가 들수록 줄어들지 않게 한다 (표본이 적어 생기는 역전을 막는다)."""
        if month is not None and season is None:
            season = season_of(month)
        ys = self.by_season.get(season, self.sigma_all) if season else self.sigma_all
        ys = list(np.maximum.accumulate(np.asarray(ys, float)))
        x, lead = self.leads_h, max(float(lead_h), 0.0)
        if lead <= x[-1] or len(x) < 2:
            s = float(np.interp(lead, x, ys))
        else:
            slope = max((ys[-1] - ys[-2]) / (x[-1] - x[-2]), 0.0)
            s = ys[-1] + slope * (lead - x[-1])
        return s * (self.scale if scaled else 1.0)


def _quantile(values, n_bad, level):
    """오름차순 분위수. 비행 불가 표본(n_bad)은 가장 나쁜 쪽에 있다고 본다 – 분위수가 그 안에 들면 inf."""
    n = len(values) + n_bad
    if n == 0:
        return math.inf
    k = level / 100.0 * (n - 1)
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if hi >= len(values):
        return math.inf
    v = np.sort(np.asarray(values, float))
    return float(v[lo] + (v[hi] - v[lo]) * (k - lo))


def mission_confidence(site, drone, path, waits, t_start, mass, sigma, n=200, seed=0, adapt=False, e_fixed=0.0,
                       reserve=20.0, soc_start=100.0, return_samples=False):
    """계획 경로의 SOC 소모 분포 (바람 편차에 대한 몬테카를로).

    path, waits : 계획 경로(격자 노드)와 노드별 출발 전 대기 [s]
    t_start     : 순항 시작 시각 (site 절대 시각 [s])
    sigma       : 바람 편차의 성분별 표준편차 [m/s] (WindErrorModel.sigma 로 구한다)
    adapt       : False = 계획 때 정한 구간 대지속도 명령을 그대로 따름 (낼 수 없으면 감속)
                  True  = 비행 중 속도 갱신이 편차를 알아내 속도를 다시 정한다고 봄 (갱신 직후부터의 이상적인 경우)
    e_fixed     : 순항 밖의 에너지 [J] (이륙·착륙·앞 구간에서 이미 쓴 양). 그대로 더한다.
    반환: dict(n, sigma, adapt, nominal{e, soc_used, soc_end}, mean_soc_used, soc_used_q{50,90,95,99},
               soc_end_q{…}, p_ok, infeasible, reserve [, samples])
          soc_used_q[95] = 표본의 95 % 가 이보다 적게 쓴다. soc_end_q[95] = soc_start − soc_used_q[95].
          p_ok = 도착 SOC ≥ reserve 인 표본의 비율 (비행 불가 표본은 실패로 센다).
    """
    waits = np.asarray(waits, float)
    nom = simulate(site, drone, path, waits, t_start, mass)
    if nom is None:
        raise ValueError("계획 경로를 예보 바람에서 비행할 수 없습니다")
    cmd = np.array(nom["vg"], float)
    e_nom = float(nom["e"][-1]) + float(e_fixed)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((int(n), 2))
    soc, bad = [], 0
    sigma = float(sigma)
    for du, dv in z * sigma:
        if sigma <= 0.0:
            sim = nom
        elif adapt:
            sim = simulate(site, drone, path, waits, t_start, mass, bias=(du, dv))
        else:
            sim = simulate(site, drone, path, waits, t_start, mass, vcmd=cmd, bias=(du, dv))
        if sim is None:
            bad += 1
            continue
        soc.append(float(soc_drop(float(sim["e"][-1]) + float(e_fixed), drone)))
    soc = np.asarray(soc, float)
    used_q = {lv: _quantile(soc, bad, lv) for lv in LEVELS}
    out = dict(n=int(n), sigma=sigma, adapt=bool(adapt), reserve=float(reserve),
               nominal=dict(e=e_nom, soc_used=float(soc_drop(e_nom, drone)), soc_end=float(soc_start - soc_drop(e_nom, drone))),
               mean_soc_used=float(soc.mean()) if len(soc) else math.inf, soc_used_q=used_q,
               soc_end_q={lv: (float(soc_start - v) if math.isfinite(v) else -math.inf) for lv, v in used_q.items()},
               p_ok=float(np.sum(soc_start - soc >= reserve) / max(int(n), 1)), infeasible=float(bad / max(int(n), 1)))
    if return_samples:
        out["samples"] = soc
    return out


def go_no_go(conf, reserve=None, level=0.95):
    """요구 신뢰수준에서의 출발 판정.

    go      : 임무 성립 확률 p_ok 가 level 이상 (도착 SOC ≥ 예비)
    caution : 예보대로라면 성립하지만 (명목 도착 SOC ≥ 예비) 요구 신뢰수준에는 못 미침
    no-go   : 예보대로 날아도 예비를 지키지 못하거나, 성립 확률이 절반에 못 미침
    conf: mission_confidence 의 반환값. reserve 를 따로 주면 분위수로 다시 따진다.
    """
    lv = int(round(float(level) * 100))
    reserve = conf["reserve"] if reserve is None else float(reserve)
    if lv in conf["soc_end_q"] and (reserve != conf["reserve"]):
        ok_at_level = conf["soc_end_q"][lv] >= reserve
    else:
        ok_at_level = conf["p_ok"] >= float(level)
    nominal_ok = conf["nominal"]["soc_end"] >= reserve
    q = conf["soc_end_q"].get(lv)
    qtxt = "" if q is None else (f"신뢰수준 {lv} % 의 도착 SOC {q:.1f} %" if math.isfinite(q) else f"신뢰수준 {lv} % 에서 비행 불가 표본 포함")
    if ok_at_level and nominal_ok:
        return dict(decision="go", level=float(level), reason=f"임무 성립 확률 {conf['p_ok'] * 100:.0f} % ≥ {lv} % · {qtxt} (예비 {reserve:.0f} %)")
    if nominal_ok and conf["p_ok"] >= 0.5:
        return dict(decision="caution", level=float(level),
                    reason=f"예보대로면 도착 SOC {conf['nominal']['soc_end']:.1f} % 로 성립하지만 성립 확률 {conf['p_ok'] * 100:.0f} % < {lv} % · {qtxt}. "
                           "탑재를 줄이거나, 더 새 예보를 받거나, 바람이 약한 시각으로 옮기십시오")
    return dict(decision="no-go", level=float(level),
                reason=f"명목 도착 SOC {conf['nominal']['soc_end']:.1f} %, 성립 확률 {conf['p_ok'] * 100:.0f} % (예비 {reserve:.0f} %)")


def robust_choice(candidates, level=0.9, *, site=None, drone=None, t_start=None, mass=None, sigma=None, n=200, seed=0,
                  adapt=False, e_fixed=0.0, confs=None):
    """후보 경로 가운데 요구 신뢰수준의 SOC 소모 분위수가 가장 작은 것을 고른다 (신뢰도 기반 경로 검토).

    candidates : {"energy": (path, waits), "shortest": (path, waits), …}
    confs      : 이미 구한 {이름: mission_confidence 결과} 가 있으면 그것을 쓴다 (없으면 같은 난수로 계산한다)
    반환: dict(chosen, level, by={이름: 그 분위수의 SOC 소모}, nominal={이름: 명목 SOC 소모}, confs)
    """
    lv = int(round(float(level) * 100))
    if lv not in LEVELS:
        raise ValueError(f"신뢰수준은 {', '.join(str(v / 100) for v in LEVELS)} 중 하나")
    if confs is None:
        confs = {k: mission_confidence(site, drone, p, w, t_start, mass, sigma, n=n, seed=seed, adapt=adapt, e_fixed=e_fixed)
                 for k, (p, w) in candidates.items()}
    by = {k: c["soc_used_q"][lv] for k, c in confs.items()}
    chosen = min(by, key=lambda k: (by[k], confs[k]["nominal"]["soc_used"]))
    return dict(chosen=chosen, level=float(level), by=by, nominal={k: c["nominal"]["soc_used"] for k, c in confs.items()}, confs=confs)


def load_seasonal_table(path=None):
    if path is None:
        from . import config as C
        path = C.RES / "seasonal_table.json"
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def seasonal_recommendation(month, hour, table=None, site=None):
    """월·출발 시각(KST)의 권고. table: load_seasonal_table() 결과 (없으면 파일에서 읽는다).

    반환: dict(month, hour, season, setting, wind_class, wind_ms, saving_vs_baseline_pct, saving_vs_shortest_pct,
               margin95_pct, sigma6, best_hours, note, source)
    자료가 없으면 계절과 무관한 기본 권고를 돌려주고 source 에 그 사실을 적는다.
    """
    month, hour = int(month), int(hour)
    season = season_of(month)
    base = dict(month=month, hour=hour, season=season,
                setting="에너지 탐색 + 안전장치 + 속도 갱신 30 s", wind_class=None, wind_ms=None,
                saving_vs_baseline_pct=None, saving_vs_shortest_pct=None, margin95_pct=None, sigma6=None, best_hours=None,
                note="계절별 자료가 없습니다. 한 가지 설정을 쓰고, 출발 전에 mission_confidence 로 신뢰수준을 확인하십시오.",
                source="default (results/seasonal_table.json 없음)")
    table = table if table is not None else load_seasonal_table()
    if not table:
        return base
    sites = table.get("sites", {})
    key = site if site in sites else (table.get("default_site") if table.get("default_site") in sites else next(iter(sites), None))
    if key is None or str(month) not in sites[key].get("months", {}):
        return base
    M = sites[key]["months"][str(month)]
    hrs = M.get("hours", {})
    hk = min(hrs, key=lambda h: abs(int(h) - hour)) if hrs else None
    H = hrs.get(hk, {}) if hk is not None else {}
    out = dict(base)
    out.update(setting=M.get("setting", base["setting"]), wind_class=H.get("wind_class", M.get("wind_class")),
               wind_ms=H.get("wind_ms", M.get("wind_ms")), saving_vs_baseline_pct=M.get("saving_vs_baseline_pct"),
               saving_vs_shortest_pct=M.get("saving_vs_shortest_pct"), margin95_pct=M.get("margin95_pct"),
               sigma6=M.get("sigma6"), best_hours=M.get("best_hours"), note=M.get("note", ""),
               source=f"{table.get('source', 'results/seasonal_table.json')} · {key}")
    return out
