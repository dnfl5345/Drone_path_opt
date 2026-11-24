# 실측 ADS-B 항적 샘플

- 출처: Python 패키지 `traffic` 2.13 (X. Olive, MIT 라이선스, PyPI 배포본)의 `traffic/data/samples/` 중 저고도 비행 관련 분류
  (ambulances, collections, gliders, surveys, calibration, tv_relay, firefighting)
- 원자료: OpenSky Network ADS-B 수신 자료 (각 파일은 `traffic` 패키지가 배포한 샘플 그대로)
- 사용: `src/dpo/adsb_errors.py` → `scripts/analyze_adsb.py`, `scripts/run_e11.py`
  (본 분석은 1 Hz 로 기록된 항적만 사용: ambulances 3편, collections 의 비항공사 기체)
- 주의: 샘플은 시연용으로 선별된 항적이라 무작위 표본이 아니다 (논문에서 한계로 명시)
