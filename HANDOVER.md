# 인수인계 (2026-11-18 기준, v1.1.0)

## ① 지금까지 정한 것

- **비교 기준:** 기존 경로 = 최단 경로 + 정속 10 m/s. '최단 + 속도 최적화'도 함께 보고한다 (`docs/review_gaps.md`).
- **속도 갱신:** 순항 5초 뒤 첫 측정, 이후 30초 주기. 예상 에너지가 0.3 % 넘게 줄고 새 궤적이 위험구간과 겹치지 않을 때만 채택. 경로는 바람 때문에 바꾸지 않는다.
- **안전장치:** 에너지 탐색 경로와 '비행 가능하게 고친 최단 경로 + 속도 최적화'를 예보 기준으로 비교. 최단 후보는 위험구간 검사를 통과해야 한다.
- **기체 연결:** 명령 방식(`DO_CHANGE_SPEED`)이 기본. 남은 구간 미션 올리기는 운용자가 명시적으로 부를 때만.
- **배포 형태:** pip 패키지 `drone-path-opt` + 자료 폴더 `DPO_HOME`. 기본 의존성은 numpy·scipy·numba·matplotlib.

## ② 만든 파일 (이번 작업)

| 파일 | 내용 |
|---|---|
| `src/dpo/speed_update.py` | 바람 편차 추정, 남은 경로 자르기, 속도 재결정, 속도 명령 목록 |
| `src/dpo/mission.py` | `st_safeguard`, `make_flyable`(직선 통과 검사·대기 보존) |
| `src/dpo/planner.py` | `simulate`에 명령 속도(`vcmd`)·바람 보정(`bias`)·구간 기록 |
| `src/dpo/live.py` | 실제 바람, 속도 갱신 루프, 안전장치, 속도 명령 대기열, `mission()` |
| `src/dpo/ardupilot.py` | `remaining_mission` (비행 중 남은 구간 미션) |
| `src/dpo/mavlink_bridge.py` | `MavBridge`, `FakeVehicle` |
| `src/dpo/cli.py`, `pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml` | 명령줄, 패키징, 이미지, CI |
| `src/dpo/webapp/` | 계획 API 안전장치, 실시간 시작 옵션, `/api/live/mission*`, `/api/health`, 화면 |
| `tests/test_deploy.py` | 8개 시험 |
| `scripts/exp_live_closed_loop.py` | E15 → `results/E15_*.json`, F30 |
| `scripts/make_deploy_figures.py` | F31–F33 |
| `scripts/build_deploy_report.js` | Word 보고서 생성 |
| `docs/deployment.md`, `docs/drone_soc_deployment_report.docx` | 배포 안내, 보고서 |

## v1.1.0 에서 더한 것 (2026-11-18)

- **정한 것**
  - 실증 기체는 Holybro X650 (Pixhawk 6X, ArduPilot). 사양서에 DJI 부품은 없다.
  - 비행 중 속도 갱신을 쓰는 비행은 '명령 방식' 미션(속도 항목은 첫 구간만)으로 올린다. ArduCopter 는 미션의 속도 항목과 외부 명령이 같은 값을 덮어쓴다 (4.7.2 소스·SITL 확인).
  - 출발 판정은 신뢰수준 95 % 를 기본으로 한다. 예보 시점(발표 후 경과 시간)을 `--forecast-age` 로 넣는다.
  - 계절별로 알고리즘을 바꾸지 않는다. 설정은 하나, 여유와 출발 시각만 계절에 맞춘다 (E16).
  - `dpo-fly` 는 시동·이륙·모드 전환을 하지 않는다. 예보와 8 m/s 넘게 다른 바람 측정은 버린다.
- **만든 파일**: `src/dpo/reliability.py`, `flight.py`, `sim_vehicle.py`, `logcal.py`, `scripts/exp_reliability.py`, `fetch_gfs_clim.py`, `sitl_check.py`, `sitl_speed_test.py`, `tests/test_reliability.py`, `test_field.py`, `docs/reliability.md`, `field_demo_x650.md`, `results/E16_*`, `wind_error_model.json`, `seasonal_table.json`, `sitl_check.json`, `sitl_speed.json`, 그림 F34–F36
- **SITL 에서 알게 된 것**: 한계를 넘는 속도 명령은 수락되지만 느리게 난다 / 90° 꺾임은 경로점당 약 1.8 s, 출발·도착 합 약 9.7 s (8 m/s) / EKF 바람 추정의 크기는 `EK3_DRAG_BCOEF` 에 따라 0.9–3.2배

## 2026-11-18 추가 (보고서·발표자료 개정)

- **정한 것**
  - 발주처 설명용 비교는 '기존 = 최단 + 지면 기준 정속 10 m/s', '대조 = 최단 + 계산한 속도', '제안' 세 가지로 한다. 화면의 근거 문장은 수치에서 만든다 (`App.compare`).
  - 3D 화면의 예시(2026-10-27)는 바람이 센 날이다. 경로·고도의 몫(12–21 %)이 평균(1–5 %)보다 크다는 점을 항상 함께 적는다.
  - 용어는 '예보 시점'(발표 후 경과 시간)으로 통일한다. 명령줄 인자 이름 `--forecast-age` 는 그대로 둔다.
  - DJI 쪽 설명은 공개 문서에서 확인한 것만 적고 나머지는 `[확인요망]` 으로 둔다. WPML 내보내기는 없다.
- **만든·고친 파일**: `src/dpo/webapp/server.py` (`compare`, `/api/compare`), `static/map3d.*`, `scripts/capture_compare.js`, `results/compare_examples.json`, `results/platform_3d_compare_*.json`, `results/figures/platform_3d_compare_*.png`, `scripts/build_deploy_report.js` (2·3·12·13장), `scripts/build_academic_ppt.py` (슬라이드 5장), `tests/test_field.py` (비교 시험).
- **확인하지 못한 것**: DJI 기체에서의 동작 전부, DJI 의 복귀 판단이 바람을 쓰는지 여부.
- **회의용 발표자료에도 반영** (31 → 37장): 두 발표자료가 같은 슬라이드 코드(`scripts/build_ppt.py` 의 `add_v11_slides`)를 쓴다. 회의자료에는 대상 기체·여러 날 통계 한 장(`bridge=True`)을 앞에 더 넣는다. 수치를 고치면 두 자료를 모두 다시 만든다.

## ③ 다음에 할 일

1. **X650 보정 비행** (호버 + 5–15 m/s 정속 왕복): `dpo-calibrate --save` 로 전력 곡선, 최대 대기속도, `EK3_DRAG_BCOEF` 를 정한다. 실증 기체의 펌웨어 판도 이때 확인한다.
2. **지상 점검과 비교 비행** (`docs/field_demo_x650.md` 5–7절). SITL 은 통과했지만 SITL 의 기체는 X650 이 아니다.
3. **관측 바람으로 예보 오차 다시 재기.** E16 은 예보끼리의 비교라 실제 오차보다 작을 수 있다.
4. **Docker 이미지 빌드 확인** (이 환경에는 데몬이 없었다).
5. **실측 바람과 예보의 차이 측정**, 기상청 고해상도 자료 연동.
6. 순회 임무의 실시간 운용, 서버 인증.

## 확인 방법

```bash
pip install -e ".[dev]" && dpo-plan --check && pytest -q
```
