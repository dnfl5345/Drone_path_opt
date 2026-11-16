# 인수인계 (2026-11-16 기준, v1.0.0)

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

## ③ 다음에 할 일

1. **X650 전력 곡선 보정** (5–15 m/s 정속 비행 로그). 속도 이득의 크기가 여기에 달려 있다.
2. **ArduPilot SITL 시험:** `COMMAND_LONG` 속도 명령과 미션 내 속도 항목의 우선순위, `WIND` 메시지 수신(`EK3_DRAG_BCOEF_X/Y`).
3. **기체 위치 추종:** 지금 실시간 엔진은 기체를 모의한다. `GLOBAL_POSITION_INT`로 실제 위치를 받아 계획과 맞추는 단계가 없다.
4. **Docker 이미지 빌드 확인** (이 환경에는 데몬이 없었다).
5. **실측 바람과 예보의 차이 측정**, 기상청 고해상도 자료 연동.
6. 순회 임무의 실시간 운용, 서버 인증.

## 확인 방법

```bash
pip install -e ".[dev]" && dpo-plan --check && pytest -q
```
