# DJI SDK에서 추출한 항목 목록

DJI 공식 SDK 저장소의 헤더와 API 문서에서 **항목 이름과 설명만** 추출한 목록입니다(2026-11-12 내려받음).
`scripts/dji_conditions_review.py`가 이 목록을 읽어 역할별로 분류합니다.

| 파일 | 내용 | 항목 수 | 원문 |
|---|---|---|---|
| `osdk_topics.txt` | Onboard SDK 텔레메트리 토픽 (`TOPIC_*`) | 47 | `dji-sdk/Onboard-SDK` · `osdk-core/api/inc/dji_telemetry.hpp` |
| `psdk_topics.txt` | Payload SDK 구독 토픽 (`DJI_FC_SUBSCRIPTION_TOPIC_*`) | 56 | `dji-sdk/Payload-SDK` · `psdk_lib/include/dji_fc_subscription.h` |
| `psdk_flight_anomaly_flags.tsv` | `FLIGHT_ANOMALY` 토픽의 플래그 (강풍 플래그 2개 포함) | 13 | 위와 같음 |
| `msdk_v5_flightcontroller_keys.tsv` | Mobile SDK V5 `FlightControllerKey` | 82 | `dji-sdk/Mobile-SDK-Android-V5` · `Docs/Android_API/en/Components/IKeyManager/Key_FlightController_FlightControllerKey.html` (커밋 d66e002) |
| `msdk_v5_battery_keys.tsv` | Mobile SDK V5 `BatteryKey` | 15 | 같은 폴더 `Key_Battery_BatteryKey.html` |

- 항목 수는 원문을 파싱해 센 값입니다. 카메라·짐벌·조종기·영상 링크 키는 비행 에너지와 무관해 넣지 않았습니다.
- 기체마다 지원하는 항목이 다릅니다. 이 목록은 "SDK가 정의한 항목"이지 "모든 기체가 내놓는 값"이 아닙니다.
- developer.dji.com 문서는 이 환경에서 접속이 막혀 GitHub 저장소의 원문만 사용했습니다.
