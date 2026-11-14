# 드론 SoC 최대화 경로계획 (WE-LT*)

바람·지형·탑재중량을 반영해 **도착 SOC(배터리 잔량)를 최대화**하는 드론 물류 경로계획 알고리즘입니다.
지난 회의에서 선정한 최단 경로 알고리즘(Lazy Theta*)을 3D 시공간 에너지 탐색기 **WE-LT\* (Wind-aware Energy Lazy Theta\*)**로 확장했습니다.

- 회의 보고 자료: [`docs/drone_soc_path_planning_dev.pptx`](docs/drone_soc_path_planning_dev.pptx) (기존 회의자료와 같은 포맷, 31장)
- **학술 발표 자료** (Intro → Background → Related Works → Proposed Method → Results → Conclusion, KETI 템플릿, 39장, 발표자 노트 포함, 대상 기체·3D 지도·창원 순회 시뮬레이션 반영): [`docs/drone_soc_academic_talk.pptx`](docs/drone_soc_academic_talk.pptx)
- 최신 연구 대비 정식화 비교: [`docs/algorithm_comparison.md`](docs/algorithm_comparison.md) (문제 (P) 정의, 문헌별 정식화, 가정 축소 실험)
- 확장안 (8방향 탐색 + 고도별 균일·선형 바람, LUT): [`docs/extension_8way_linear_wind.md`](docs/extension_8way_linear_wind.md)
- 3D 시공간 확장 (동적 장애물) + 장애물 편집 플랫폼 + 장애물 변경 시 이벤트 기반 재계획: [`docs/spatiotemporal_dynamic_obstacles.md`](docs/spatiotemporal_dynamic_obstacles.md)
- **창원 창이대로 71 주변 순회 시뮬레이션과 3D 장애물 편집**: [`docs/changwon_tour.md`](docs/changwon_tour.md)
  - 지역 전환: `python -m dpo.webapp --site changwon` (또는 환경변수 `DPO_SITE`), 자료 수집 `scripts/build_site.py`
  - 3D 화면(`/3d`)에서 장애물 추가·끌어 옮기기·속성 편집, 순회 임무(경유점), 단일 ArduPilot 미션
  - 결과 `scripts/run_changwon.py` → `results/changwon.json`, 그림 F27
- **실증 대상 기체(Holybro X650, ArduPilot) 반영과 3D 지도 경로 생성**: [`docs/target_x650.md`](docs/target_x650.md)
  - 기체 프리셋 `src/dpo/drones.py` (전력 모델은 제원·제조사 호버 시간 기반 추정), 결과 `scripts/run_x650.py` → `results/x650.json`, 그림 F26
  - 3D 지도 화면 `http://127.0.0.1:8765/3d` (지형·건물·바람·경로·이동 장애물, 지도에서 출발·도착 지정)
  - ArduPilot 미션 내보내기 `src/dpo/ardupilot.py` (QGC WPL 110 `.waypoints` + 경로표 CSV), 예: `results/missions/`
- **연구 배경** (왜 SOC가 목적 변수인가, 왜 바람의 크기·방향이 변인인가, 왜 드론이 경로를 결정하는가): [`docs/research_background.md`](docs/research_background.md)
  - 바람 선택 근거: DJI SDK 항목 200개(Onboard 47 · Payload 56 · Mobile V5 97) 전수 분류와 다섯 질문 선별, CMU 실측 확인 → `scripts/dji_conditions_review.py`, `results/dji_conditions.json`, 그림 F25, 항목 목록 `data/raw/dji_sdk/`
- 장애물 궤적 불확실성 → 위험 한도(ε) 강건 계획 + 몬테카를로 검증: [`docs/robust_uncertainty.md`](docs/robust_uncertainty.md)
- **논문 자료**: 영문 원고 초안 [`docs/paper/manuscript_en.md`](docs/paper/manuscript_en.md), 참고문헌 [`docs/paper/references.bib`](docs/paper/references.bib), 논문화 가이드(학술적 의미·투고처·남은 작업) [`docs/paper/논문화_가이드.md`](docs/paper/논문화_가이드.md)

## 실시간 운용 플랫폼 (`/live`)

```bash
PYTHONPATH=src python -m dpo.webapp        # 브라우저에서 http://localhost:8765/live
```

드론이 시계에 맞춰 비행하는 동안(1~30배속) 감시·충돌 검사·재계획을 계속 수행합니다. 계산은 백그라운드에서 돌고 비행은 멈추지 않습니다.
- **비행 중 입력:** 정지·이동 장애물 추가·삭제(지도 클릭), 실측 ADS-B 침입기 투입, 외부 감시 보고 REST(`POST /api/live/report`)
- **안전 장치:** 불확실성 튜브(실측 분위수·가우시안), 완화 단계, 비상 정지(호버), 지상 출발 대기, 배터리 예비 도달·연속 실패 시 비상 착륙
- **효율:** 즉시 계획 후 백그라운드 개선(anytime)
- **화면:** SSE 10 Hz 상태 화면, 이벤트 타임라인, 로그(JSON) 내려받기
- **벤치(10배속 왕복 5회, 침입기·경로 위 장애물·외부 보고 동시 투입):**
  - 완주 5/5, 침범 0
  - 감시 검사 6.9 ms, 재계획 0.8 s(최대 1.6 s)
  - 시계 주기 p95 63 ms
- 상세: [`docs/live_platform.md`](docs/live_platform.md)

## 장애물 편집·경로계획 플랫폼

```bash
PYTHONPATH=src python -m dpo.webapp        # 브라우저에서 http://localhost:8765
```

지도를 클릭해 원기둥·구·박스 장애물을 놓고, 크기·고도 범위·존재 시간·안전 이격을 입력합니다. 경로점을 추가하면 장애물이 등속으로 이동하고, 드래그로 위치를 옮길 수 있습니다. `경로 계획 실행`을 누르면 3D 시공간 계획기(WE-SIPP-Theta*)가 장애물을 피하는 에너지 최적 경로를 계산합니다. 결과는 시간 슬라이더로 재생해 확인합니다. 허용 범위(지도 범위, 반경 5~1500 m, 이동 속도 ≤ 80 m/s, 공역·대기속도·배터리 예비 SOC)를 벗어난 입력은 거부하거나 "계획 불가"로 판정합니다.

**비행 중 장애물 변경**: 재생 슬라이더를 변경 발표 시각에 두고 장애물을 수정한 뒤 `변경 반영`을 누릅니다. 남은 경로를 위험 시간구간 표로 검사해, 충돌이 없으면 기존 경로를 유지하고(약 0.3 s), 충돌이 예상되면 그 시점부터 다시 계획합니다(약 2 s, 재계획 지연 3 s 반영). 변경 이력과 재계획 지점이 함께 표시됩니다.

**실측 팽창 모양**: '팽창 모양'에서 '실측 ADS-B 분위수'를 고르면, 이동 장애물의 팽창 반경이 '속도 × 실측 오차 분위수 곡선'(20 s 지평)으로 정해집니다.

**궤적 불확실성**: 장애물마다 위치 오차 σ0, 오차 증가율 σv, 도달 시각 오차 σt를 입력하고 '궤적 불확실성'에서 위험 한도 ε를 고르면, 시간에 따라 커지는 불확실성 튜브를 피하도록 계획합니다(지도에 옅은 원으로 표시).
- 결과 그림: `results/figures/` · 수치 요약: `results/summary.json`

## 개발 항목별 구현

| 항목 | 구현 | 위치 |
|---|---|---|
| 1. 최단 경로를 실제 지도에 적용 | 성남 판교·분당 6.2×5.5 km, Copernicus DSM 30 m 지형 + Overture 건물 7,109동 → 25 m 3D 점유격자, Lazy Theta* (mode 0) | `src/dpo/data_terrain.py`, `planner.py` |
| 2. 최적 경로 수식 | ① t=d/Vg ② E=P·t ③ ΣE ④ ΔSOC=E/E_batt×100 ⑤ argmin ΔSOC + 회전익 전력모델 P(Va,Vz,m) | `src/dpo/energy.py`, `mission.py` |
| 3. 바람 정보 + 바람 지도 | NOAA GFS 0.25° 고도별(10~100 m, 975/950 hPa)·시간별 예보 → 지형·도시 보정 다운스케일 3D 바람장. 기상청 어댑터 별도 | `src/dpo/data_wind.py`, `wind_field.py`, `kma_adapter.py` |
| 4. 최적 경로를 실제 지도에 적용 | WE-LT* (확장안: `layered.py` 8방향 + 선형 바람 LUT): 26방향 3D, 시간가변 바람 비용, 구간 속도 최적화, 에너지 비교형 Lazy Theta* 지름길, 호버링/우회 판단, 롤링 재계획 | `src/dpo/planner.py`, `mission.py` |
| (추가) 3D 시공간·동적 장애물 | 이동 장애물의 위험 시간구간을 해석해로 계산하고, (노드, 안전구간) 상태의 SIPP + any-angle + 에너지 비용으로 탐색. 장애물 편집 웹 플랫폼 | `obstacles.py`, `st_planner.py`, `webapp/` |
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
- 확장안(E7): 바람을 "같은 고도면 같고, 고도에 따라 선형"으로 가정하면 간선 비용을 조회표(LUT)로 바꿀 수 있습니다. 이렇게 하면 계산이 8.9 s → 0.44 s로 **약 20배 빨라지고**, SOC는 같은 가정의 WE-LT*와 같습니다(78.2 vs 78.1 %p). 3D 바람장 대비로는 약 1.4 %p 손해입니다.
- 3D 시공간(E8): 다른 드론·응급 헬기·행사 제한구역이 기존 경로를 가로지르는 시나리오에서, 동적 장애물을 무시한 경로는 3종 모두와 충돌합니다(최대 136 m 침범). 시공간 계획은 최소 29 m 이격으로 회피하고, 그 대가로 에너지가 +9.5 % 듭니다.
- 장애물 변경(E9): 비행 중 헬기 항로 변경과 행사 제한 확대가 발표되면 이벤트 기반 재계획이 재계획 2회로 충돌을 피합니다. 30 s 주기 방식은 같은 결과에 재계획 5회가 들고, 60 s 주기 방식은 변경을 늦게 확인해 충돌합니다. 1회 계획도 충돌합니다.
- 궤적 불확실성(E10, 30 시나리오 × 1,000 샘플): 예측 궤적을 그대로 믿으면 충돌률이 16.4 %입니다. 고정 최악 버퍼와 위험 한도 튜브(ε=0.05)는 모두 0/30,000이지만, 튜브가 에너지를 **4.9 % 덜** 씁니다(28승 2패, Wilcoxon p = 1.6×10⁻⁷). 20 s 재관측 폐루프에서는 재계획 횟수를 73 % 줄입니다(충돌 차이는 유의하지 않음). 단, 오차 꼬리가 두꺼우면(Student-t) 이 에너지 이득은 사라지고 낮은 위험 영역에서는 고정 버퍼가 낫습니다(E10c·E10d, 그림 F19). 따라서 팽창 모양은 실제 예측 오차의 꼬리에 맞춰 정해야 합니다.
- 실측 ADS-B 항적(OpenSky, 저고도 비항공사 18대, 관측 3,186개, 그림 F20)의 등속 예측 오차는 꼬리가 두껍고(99.9 % 분위수 9.1σ, 가우시안 3.7σ), 예측 시간의 약 1.5제곱으로 커집니다. 데이터로 맞춘 구간별 선형 팽창 모양을 쓰면, 학습에 쓰지 않은 기체에서 초과확률이 ε=0.05일 때 5.1 %, ε=0.01일 때 1.1 %로 목표에 맞습니다. 가우시안 튜브는 같은 조건에서 7.5 %와 4.2 %입니다.
- 실측 궤적 침입기 폐루프(E11, 조우 400회, 그림 F21)에서는 충돌의 대부분(4.5 %)이 어떤 방법으로도 회피할 수 없는 조우에서 나왔습니다. 팽창 모양 간 충돌 차이는 유의하지 않았고, 데이터 튜브는 에너지가 +2.1 % 더 들었습니다. '반응 시간 하한'을 두면 이 차이가 줄어듭니다. 원래 속도(50 m/s)의 유인기는 수평 회피만으로는 막을 수 없었습니다.
- 가정 축소 실험(E6) 결과, 비교 축별 기여는 **구간 속도 최적화 +10.2~13.0 %p**(10 m/s 고정이면 비행 불가) > **에너지 비교형 any-angle +4.1 %p** > **3D 고도 선택 +2.8~3.1 %p** 순입니다.

## 실행

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m dpo.data_terrain                  # DSM·건물 수집 → data/processed/site_grid.npz
PYTHONPATH=src python -m dpo.data_wind                     # GFS 10월 강풍일 스캔
PYTHONPATH=src python scripts/run_experiments.py           # E0~E9 (약 16분)
PYTHONPATH=src python -c "import sys; sys.path.insert(0,'scripts'); import run_experiments as R; R.E10(n_closed=0); R.E10b(); R.E10c(); R.E10d()"   # 불확실성 (약 65분)
PYTHONPATH=src python scripts/analyze_robust.py            # 통계·그림 F16/F17/F19
PYTHONPATH=src python scripts/analyze_adsb.py              # 실측 ADS-B 오차·꼬리·보정 (F20)
PYTHONPATH=src python scripts/run_e11.py 80 5 4             # 실측 궤적 침입기 폐루프 (4코어 약 32분)
E11_METHODS="QF0.05,QF0.01" E11_OUT=E11b PYTHONPATH=src python scripts/run_e11.py 80 5 4
PYTHONPATH=src python scripts/analyze_e11.py               # 통계·그림 F21
PYTHONPATH=src python scripts/make_figures.py && PYTHONPATH=src python scripts/make_figures.py gif
PYTHONPATH=src python scripts/build_ppt.py <회의자료 템플릿.pptx> docs/drone_soc_path_planning_dev.pptx
PYTHONPATH=src python scripts/dji_conditions_review.py                                   # DJI 조건 검토 (F25)
PYTHONPATH=src python scripts/build_academic_ppt.py <회의자료 템플릿.pptx> docs/drone_soc_academic_talk.pptx
PYTHONPATH=src python scripts/bench_live.py 5 10              # 실시간 엔진 벤치 (약 6분)
PYTHONPATH=src python -m pytest -q tests
```

GFS 원자료(`data/raw/gfs_*.npz`)와 격자(`data/processed/site_grid.npz`)는 저장소에 포함되어 있어, 실험은 네트워크 없이 다시 돌릴 수 있습니다(실측 CSV는 처음 실행할 때 내려받습니다).

## 참고문헌

1. A. Nash, S. Koenig, C. Tovey, “Lazy Theta\*: Any-Angle Path Planning and Path Length Analysis in 3D,” *AAAI*, 2010.
2. M. Phillips, M. Likhachev, “SIPP: Safe Interval Path Planning for Dynamic Environments,” *IEEE ICRA*, 2011.
3. Y. Zeng, J. Xu, R. Zhang, “Energy Minimization for Wireless Communication With Rotary-Wing UAV,” *IEEE TWC*, 2019. DOI: 10.1109/TWC.2019.2902559
4. F. Betti Sorbelli, F. Corò, S. K. Das, C. M. Pinotti, “Energy-Constrained Delivery of Goods With Drones Under Varying Wind Conditions,” *IEEE T-ITS*, 22(9):6048–6060, 2021. DOI: 10.1109/TITS.2020.3044420
5. F. Lian, B. Li, Q. Yang, H. Zhu, D. Du, “WA-LPA\*: An Energy-Aware Path-Planning Algorithm for UAVs in Dynamic Wind Environments,” *Drones*, 9(12):850, 2025. DOI: 10.3390/drones9120850
6. F. Lian, Q. Yang, B. Li, P. Wang, H. Luo, D. Du, “Optimal Path Planning of Unmanned Aerial Vehicle with Wind Field,” *IEEE ITSC*, pp. 4195–4200, 2025.
7. R. Gu, Y. Zhao, X. Ren, “Integrating wind field analysis in UAV path planning: Enhancing safety and energy efficiency for urban logistics,” *Chinese Journal of Aeronautics*, 39(1):103605, 2026. DOI: 10.1016/j.cja.2025.103605
8. T. A. Rodrigues et al., “In-flight positional and energy use data set of a DJI Matrice 100 quadcopter for small package delivery,” *Scientific Data*, 8:155, 2021. DOI: 10.1038/s41597-021-00930-x
9. J. G. Leishman, *Principles of Helicopter Aerodynamics*, 2nd ed., Cambridge University Press, 2006.
10. S. Koenig, M. Likhachev, D. Furcy, “Lifelong Planning A\*,” *Artificial Intelligence*, 155, 2004.
11. L. Blackmore, M. Ono, B. C. Williams, “Chance-Constrained Optimal Path Planning With Obstacles,” *IEEE T-RO*, 27(6):1080–1094, 2011. DOI: 10.1109/TRO.2011.2161160
12. N. E. Du Toit, J. W. Burdick, “Robot Motion Planning in Dynamic, Uncertain Environments,” *IEEE T-RO*, 28(1):101–115, 2012. DOI: 10.1109/TRO.2011.2166435
13. H. Zhu, J. Alonso-Mora, “Chance-Constrained Collision Avoidance for MAVs in Dynamic Environments,” *IEEE RA-L*, 4(2):776–783, 2019. DOI: 10.1109/LRA.2019.2893494
14. K. Yakovlev, A. Andreychuk, “Any-Angle Pathfinding for Multiple Agents Based on SIPP Algorithm,” *ICAPS*, 2017. DOI: 10.1609/icaps.v27i1.13856
15. X. Olive, “traffic, a toolbox for processing and analysing air traffic data,” *J. Open Source Softw.*, 4, 1518, 2019. DOI: 10.21105/joss.01518
16. M. Schäfer, M. Strohmeier, V. Lenders, I. Martinovic, M. Wilhelm, “Bringing up OpenSky: A large-scale ADS-B sensor network for research,” *IPSN*, pp. 83–94, 2014. DOI: 10.1109/IPSN.2014.6846743

## 데이터 출처

- 지형: Copernicus GLO-30 DSM (© DLR/Airbus, ESA Copernicus, AWS Open Data)
- 건물: Overture Maps Foundation buildings 2026-10-21 (OSM 등, ODbL)
- 바람: NOAA GFS 0.25° (AWS Open Data `noaa-gfs-bdp-pds`), 00Z 사이클은 계획용, 06Z 사이클은 '실제' 바람 대용
- 항적: OpenSky Network ADS-B 샘플 (`traffic` 2.13 패키지 배포본, MIT) → `data/raw/adsb/`
- 드론 실측: Rodrigues et al., *Scientific Data* 8:155 (2021), CMU KiltHub, CC BY 4.0 (비행 단위 집계본 사용)

## 한계 및 가정

- 기상청 API(data.go.kr, apihub)는 개발 환경의 네트워크 정책 때문에 접속할 수 없었습니다. 그래서 같은 입력 형식의 어댑터만 구현했고, 실제 API 호출은 검증하지 못했습니다(격자 변환만 테스트함).
- 지형·도시 바람 보정은 간이 근사입니다(보정 계수 0.80~1.19). LDAPS나 CFD로 바꾸는 것을 권장합니다.
- 전력모델 질량(3.1 kg)과 배터리(99.9 Wh, TB47D급)는 DJI 사양을 바탕으로 한 가정입니다. 검증 날짜에서는 평균 전력을 3~9 % 정도 낮게 예측했습니다.
- WE-LT*는 노드마다 도착 시각 라벨을 하나만 두는 근사 탐색이라, 시간가변 비용에서 전역 최적을 보장하지 않습니다.
- 장애물 불확실성은 가우시안·직선 등속 모델이고 σ 값은 가정입니다. 오차 꼬리가 두꺼우면 튜브의 이득이 사라집니다. 실측 항적 표본은 시연용 샘플 18대라 무작위 표본이 아니고, 폐루프 시험은 유인기 항적을 드론 속도로 축소했습니다(속도 정규화 오차가 속도와 무관하다는 가정). 위험 한도 ε는 시점별 상계라 실측 위험보다 보수적입니다(임무 위험 확률로 직접 읽으면 안 됨).
- 비행금지구역 원은 예시(가상)입니다. 실제 판교 일대는 서울공항 관제권이라 비행승인이 필요합니다.
