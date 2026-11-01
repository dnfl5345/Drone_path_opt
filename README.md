# 드론 SoC 최대화 경로계획 (WE-LT*)

바람·지형·탑재중량을 반영해 **도착 SOC(배터리 잔량)를 최대화**하는 드론 물류 경로계획 알고리즘입니다.
지난 회의에서 선정한 최단 경로 알고리즘(Lazy Theta*)을 3D 시공간 에너지 탐색기 **WE-LT\* (Wind-aware Energy Lazy Theta\*)**로 확장했습니다.

- 회의 보고 자료: [`docs/drone_soc_path_planning_dev.pptx`](docs/drone_soc_path_planning_dev.pptx) (기존 회의자료와 같은 포맷, 17장)
- 결과 그림: `results/figures/` · 수치 요약: `results/summary.json`

## 개발 항목별 구현

| 항목 | 구현 | 위치 |
|---|---|---|
| 1. 최단 경로를 실제 지도에 적용 | 성남 판교·분당 6.2×5.5 km, Copernicus DSM 30 m 지형 + Overture 건물 7,109동 → 25 m 3D 점유격자, Lazy Theta* (mode 0) | `src/dpo/data_terrain.py`, `planner.py` |
| 2. 최적 경로 수식 | ① t=d/Vg ② E=P·t ③ ΣE ④ ΔSOC=E/E_batt×100 ⑤ argmin ΔSOC + 회전익 전력모델 P(Va,Vz,m) | `src/dpo/energy.py`, `mission.py` |
| 3. 바람 정보 + 바람 지도 | NOAA GFS 0.25° 고도별(10~100 m, 975/950 hPa)·시간별 예보 → 지형·도시 보정 다운스케일 3D 바람장. 기상청 어댑터 별도 | `src/dpo/data_wind.py`, `wind_field.py`, `kma_adapter.py` |
| 4. 최적 경로를 실제 지도에 적용 | WE-LT*: 26방향 3D, 시간가변 바람 비용, 구간 속도 최적화, 에너지 비교형 Lazy Theta* 지름길, 호버링/우회 판단, 롤링 재계획 | `src/dpo/planner.py`, `mission.py` |
| 5. 드론 데이터와 비교 | CMU DJI Matrice 100 실측 198회(탑재 0~750 g)로 전력모델 보정·검증 (날짜 분리 검증 R² 0.77, MAPE 9.6%) | `energy.py`, `scripts/run_experiments.py: E0` |

## 주요 결과 (2026-10-27 강풍일, 15:00 KST, 거점↔배송지 2.4 km 왕복, 0.5 kg)

| 방법 | 배송 SOC 소모 | 회수 SOC 소모 | 웨이포인트 |
|---|---|---|---|
| 최단(Lazy Theta*) + 정속 10 m/s | 102.9 %p (배터리 부족) | 102.2 %p | 21 |
| 최단 + 구간 속도 최적화 | 86.1 %p | 88.2 %p | 21 |
| 에너지 A* (26방향) | 80.8 %p | 82.7 %p | 199 |
| **제안 WE-LT\*** | **76.7 %p** | **78.5 %p** | 37 |

- 출발시각(09~20시, 시간별 예보)에 따라 경로·SOC가 바뀌며, 예비 SOC 20 %를 지키는 경우는 기존 방식 0/24회, WE-LT* 23/24회입니다.
- 장애물 시간표(띠 모양 임시 비행제한): 해제까지 30 s면 **28 s 호버링**(57.9 kJ)을, 300 s면 **우회**(70.9 kJ)를 고릅니다.
- 비행 중 돌발 금지구역이 생기면 롤링 재계획(60 s 주기 + 이벤트 발생 시)으로 피해 갑니다. 재계획 1회에 평균 5.3 s가 걸립니다.

## 실행

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m dpo.data_terrain                  # DSM·건물 수집 → data/processed/site_grid.npz
PYTHONPATH=src python -m dpo.data_wind                     # GFS 10월 강풍일 스캔
PYTHONPATH=src python scripts/run_experiments.py           # E0~E5 (약 10분)
PYTHONPATH=src python scripts/make_figures.py && PYTHONPATH=src python scripts/make_figures.py gif
PYTHONPATH=src python scripts/build_ppt.py <회의자료 템플릿.pptx> docs/drone_soc_path_planning_dev.pptx
PYTHONPATH=src python -m pytest -q tests
```

GFS 원자료(`data/raw/gfs_*.npz`)와 격자(`data/processed/site_grid.npz`)는 저장소에 포함되어 있어, 실험은 네트워크 없이 다시 돌릴 수 있습니다(실측 CSV는 처음 실행할 때 내려받습니다).

## 데이터 출처

- 지형: Copernicus GLO-30 DSM (© DLR/Airbus, ESA Copernicus, AWS Open Data)
- 건물: Overture Maps Foundation buildings 2026-10-21 (OSM 등, ODbL)
- 바람: NOAA GFS 0.25° (AWS Open Data `noaa-gfs-bdp-pds`), 00Z 사이클은 계획용, 06Z 사이클은 '실제' 바람 대용
- 드론 실측: Rodrigues et al., *Scientific Data* 8:155 (2021), CMU KiltHub, CC BY 4.0 (비행 단위 집계본 사용)

## 한계 및 가정

- 기상청 API(data.go.kr, apihub)는 개발 환경의 네트워크 정책 때문에 접속할 수 없었습니다. 그래서 같은 입력 형식의 어댑터만 구현했고, 실제 API 호출은 검증하지 못했습니다(격자 변환만 테스트함).
- 지형·도시 바람 보정은 간이 근사입니다(보정 계수 0.80~1.19). LDAPS나 CFD로 바꾸는 것을 권장합니다.
- 전력모델 질량(3.1 kg)과 배터리(99.9 Wh, TB47D급)는 DJI 사양을 바탕으로 한 가정입니다. 검증 날짜에서는 평균 전력을 3~9 % 정도 낮게 예측했습니다.
- WE-LT*는 노드마다 도착 시각 라벨을 하나만 두는 근사 탐색이라, 시간가변 비용에서 전역 최적을 보장하지 않습니다.
- 비행금지구역 원은 예시(가상)입니다. 실제 판교 일대는 서울공항 관제권이라 비행승인이 필요합니다.
