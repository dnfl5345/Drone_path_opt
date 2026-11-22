"""업체 시험 안내서(HWPX) – 배포판과 함께 주는 설치·시험·회신 안내. 보고서와 같은 기관 양식 서식.

    python scripts/build_test_guide_hwpx.py [--out dist/시험안내서.hwpx]

설치 시간은 docs/manual_img/install_log.json (scripts/release_test.py 의 실제 기록)에서 읽는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_final_report_hwpx import C_BODY, C_H1, P_HEAD, ROOT, Writer, j  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__ as VER  # noqa: E402


def build(out: Path) -> None:
    INST = json.loads((ROOT / "docs" / "manual_img" / "install_log.json").read_text(encoding="utf-8"))["steps"]
    cmp_pg = {m["key"]: m for m in j("platform_3d_compare_pangyo_m4t.json")["methods"]}
    cmp_cw = {m["key"]: m for m in j("platform_3d_compare_changwon_m4t.json")["methods"]}
    w = Writer()
    doc = w.doc

    # ===== 표지 =====
    w.center("민간수탁연구사업 프로그램 시험 안내서", 20, bold=True)
    doc.paragraphs[0].add_page_hiding(header=True, footer=True, page_num=True)
    w.blank()
    w.cover_table([
        ("과  제  명", "배터리 잔존 용량을 고려한 드론 경로 최적화 알고리즘 개발"),
        ("프  로  그  램", f"드론 SoC 최대화 경로계획 (drone-path-opt) v{VER}"),
        ("실 증 기 체", "DJI Matrice 4T (탑재 최대 0.2 kg)"),
        ("기  관  명", "한국전자기술연구원"),
        ("작  성  일", "2026. 11. 22."),
    ])

    # ===== 1. 배포판 구성 =====
    w.h1("1. 배포판 구성", page_break=True)
    w.table(
        "배포판 파일",
        ["파일", "내용"],
        [
            [f"drone_path_opt-{VER}-py3-none-any.whl", "프로그램 (약 0.4 MB)"],
            [f"dpo_data_{VER}.zip", "자료 묶음: 판교·창원 지형·건물, 예보 바람, 오차 모형 (약 20 MB)"],
            ["install_windows.bat / run_windows.bat", "Windows 설치·실행 (두 번 눌러 실행), run_windows_changwon.bat 은 창원 지역"],
            ["install_linux_mac.sh / run_linux_mac.sh", "Linux·macOS 설치·실행 (창원: sh run_linux_mac.sh changwon)"],
            ["사용자매뉴얼.docx", "설치부터 경로 생성·내보내기까지 화면 순서대로 (20쪽)"],
            ["시험안내서.hwpx", "이 문서: 시험 항목과 회신 요청 자료"],
            ["비행기록표.xlsx", "시험비행마다 적는 기록표 (기체·배터리·풍속계)"],
            ["보고서/", "결과 보고서, 사업 내용 보고서, 발표자료"],
            ["SHA256SUMS.txt", "파일이 온전히 전달되었는지 확인하는 값"],
        ],
        widths=[2.3, 3.7],
        align=["L", "L"],
    )

    # ===== 2. 설치와 실행 =====
    w.h1("2. 설치와 실행")
    w.h2("2-1. 준비")
    w.b("Python 3.10 이상이 설치된 PC (Windows 10/11, Linux, macOS). Windows 는 python.org 설치 시 'Add python.exe to PATH' 를 켬")
    w.b("설치할 때 인터넷 연결 필요 (numpy·numba 등 의존 패키지를 받음). 설치 뒤에는 인터넷 없이 실행됨")
    w.b("브라우저: Chrome 또는 Edge 최신판 (3D 화면에 그래픽 가속 WebGL 필요)")
    w.h2("2-2. Windows")
    w.b("메일로 받은 zip 은 오른쪽 클릭 → 속성 → '차단 해제' 를 켠 뒤 '모두 압축 풀기' (경로에 한글이 있어도 됨)")
    w.b("폴더 안의 install_windows.bat 를 두 번 눌러 설치함 (처음 한 번). [1/4]–[4/4] 단계가 지나고 '정상: v… ' 과 '설치가 끝났습니다' 가 나오면 완료. 'Windows의 PC 보호' 창이 뜨면 '추가 정보' → '실행'")
    w.b("run_windows.bat (판교) 또는 run_windows_changwon.bat (창원) 를 두 번 눌러 실행하면 브라우저에 3D 화면이 열림. 검은 창을 닫으면 프로그램이 꺼짐")
    w.b("[오류] 로 시작하는 줄이 나오면 그 줄의 안내를 따르고, 해결되지 않으면 검은 창을 화면 캡처하여 회신 (R1)")
    w.note("※ Windows 설치·실행 파일은 개발 환경에 Windows 가 없어 실제 Windows 에서는 시험하지 못하였음. 같은 순서를 Linux 에서 시험하였고, Windows 용 라이브러리가 Python 3.10–3.14 모두 있음을 확인함")
    w.h2("2-3. Linux · macOS")
    w.b("터미널에서 배포판 폴더로 이동한 뒤 sh install_linux_mac.sh (처음 한 번), 실행은 sh run_linux_mac.sh")
    w.b(f"개발 환경(Linux, Python 3.13)에서 배포판 zip 을 빈 폴더에 풀어 시험한 결과: 설치 파일 {INST['install']['sec']} s, "
        f"서버 준비 판교 {INST['server_pangyo']['sec']} s · 창원 {INST['server_changwon']['sec']} s, 명령줄 경로 계획 {INST['plan']['sec']} s")
    acc = json.loads((ROOT / "docs" / "manual_img" / "install_log.json").read_text(encoding="utf-8")).get("acceptance", {})
    if acc:
        w.b("같은 시험에서 아래 3장의 T2–T7 을 브라우저 자동 조작으로 실행: "
            + ", ".join(f"{'판교' if k == 'pangyo' else '창원'} {sum(x['ok'] for x in v['steps'])}/{len(v['steps'])} 통과 (브라우저 오류 {len(v['errors'])}건)" for k, v in acc.items()))
    w.h2("2-4. 화면 없이 명령으로 (선택)")
    w.b("가상환경을 켠 뒤: dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --out missions (결과 요약과 미션 파일)")
    w.b("창원 지역: 명령에 --site changwon, 서버는 dpo-server --site changwon --open")

    # ===== 3. 시험 항목 =====
    w.h1("3. 시험 항목")
    e, p = cmp_pg["existing"], cmp_pg["proposed"]
    e2, p2 = cmp_cw["existing"], cmp_cw["proposed"]
    w.table(
        "시험 항목과 기대 결과",
        ["번호", "시험", "방법", "기대 결과 (개발 환경)"],
        [
            ["T1", "설치·점검", "설치 파일 실행", "'정상: v%s · 성남 판교·분당 …' 표시" % VER],
            ["T2", "경로 생성 (판교)", "3D 화면: 기체 M4T, 탑재 0.2 kg, 배송, 왕복, 15시 → '경로 생성'",
             f"도착 SoC {100 - p['soc_used']:.1f} %, 임무 가능"],
            ["T3", "기존 경로와 비교 (판교)", "'기존 경로와 비교' 누르기",
             f"기존 {e['soc_used']:.1f} %p → 제안 {p['soc_used']:.1f} %p"],
            ["T4", "창원 지역", "run_windows_changwon.bat (Linux·macOS: sh run_linux_mac.sh changwon) 으로 켜고 T2·T3 반복",
             f"기존 {e2['soc_used']:.1f} %p → 제안 {p2['soc_used']:.1f} %p"],
            ["T5", "M4T 경로 파일", "M4T 의 DJI Pilot 2 에서 아무 경로나 내보낸 .kmz → 3D 화면 기종 'Pilot 2 파일로 지정' → DJI 경로 파일 내려받기 → Pilot 2 로 불러오기",
             "불러오기 성공, 경로점 고도·속도·복귀 고도가 3D 화면과 같음 (개발 환경에서는 확인 불가)"],
            ["T6", "X650 미션 (선택)", "기체 X650 으로 계획 → 미션 파일 (.waypoints) → Mission Planner 로 불러오기", "경로점·속도 명령이 3D 화면과 같음"],
            ["T7", "실시간 운용 (선택)", "3D 화면 위의 '실시간 운용' → '비행 시작'", "모의 비행이 '완료 (올 때)' 까지 진행, 침범 0"],
        ],
        widths=[0.6, 1.4, 3.4, 2.3],
        font_pt=9,
        align=["C", "L", "L", "L"],
    )
    w.note("※ 기대 결과는 2026-10-27 예보 바람(자료 묶음에 든 날) 기준. 출발·도착 위치를 바꾸면 값이 달라짐")
    w.b("실제 비행 전에는 반드시 비행 승인과 현장 안전 확인을 하고, 처음에는 낮은 속도로 조종사가 언제든 개입할 수 있는 상태에서 시험할 것")

    # ===== 4. 회신 요청 자료 =====
    w.h1("4. 회신 요청 자료")
    w.table(
        "회신 요청 자료와 쓰임",
        ["번호", "자료", "쓰임"],
        [
            ["R1", "T1–T7 결과 (성공/실패, 오류가 나면 화면 캡처와 검은 창의 글자)", "설치·실행 문제 수정"],
            ["R2", "M4T 의 DJI Pilot 2 에서 내보낸 경로 파일 원본 (.kmz) 1개와, 우리가 만든 파일을 Pilot 2 로 불러온 결과 (화면 또는 오류 문구)",
             "M4T 기종 번호를 프로그램에 고정하고 경로 파일 형식 확인"],
            ["R3", "M4T 비행 기록: 시작 전 30초 호버 + 같은 직선 왕복을 5·7·10 m/s 로 각 1회, 탑재 0 kg 과 0.2 kg 각각. DJI Pilot 2 비행 기록 파일과 이륙 중량",
             "공식 사양으로 맞춘 M4T 전력 모델을 실측으로 확인·보정 (탑재·고속 구간)"],
            ["R4", "실증 지역 정보: 비행 범위(위도·경도), 이착륙 지점, 비행 고도 제한, 비행 금지 구역", "실증 지역(고성) 지형·건물·바람 자료 구축"],
            ["R5", "비행기록표.xlsx (비행마다 기체·배터리·풍속계 기록)", "비행 기록과 조건을 맞추어 분석"],
        ],
        widths=[0.6, 4.2, 2.6],
        font_pt=9,
        align=["C", "L", "L"],
    )
    w.note("※ DJI Pilot 2 비행 기록은 암호화되어 있어, 읽으려면 DJI 가 제공하는 해독 절차나 변환 서비스가 필요할 수 있음 [확인요망: M4T 비행 기록 해독 방법]")

    # ===== 5. 문제 해결 =====
    w.h1("5. 문제가 생기면")
    w.table(
        "자주 생기는 문제와 조치",
        ["증상", "원인과 조치"],
        [
            ["[오류] Python 3.10 이상을 찾지 못했습니다", "Python 3.10 이상을 설치하며 'Add python.exe to PATH' 를 켜고 설치 파일을 다시 실행"],
            ["[오류] 프로그램 설치에 실패했습니다", "인터넷 연결·회사 프록시 확인 후 설치 파일을 다시 실행 (이어서 설치됨)"],
            ["'Windows의 PC 보호' 창", "'추가 정보' → '실행'. 메일로 받은 zip 은 풀기 전에 속성의 '차단 해제'"],
            ["필수 자료가 없습니다", "자료 묶음을 풀지 않았거나 다른 폴더에서 실행함. 설치 파일과 같은 폴더에서 실행"],
            ["포트 8765 를 열 수 없습니다", "이미 켜져 있음. 브라우저에서 http://127.0.0.1:8765/3d 를 열거나 다른 창을 닫음"],
            ["결과가 임무 불가", "예비 SoC 를 지키는 경로가 없음. 탑재중량·출발 시각·거리를 바꿈"],
            ["3D 화면이 나오지 않음", "Chrome·Edge 최신판에서 그래픽 가속을 켬"],
            ["첫 계획이 느림", "설치 후 처음 한 번은 계산 코드를 컴파일함 (다음부터 빨라짐)"],
        ],
        widths=[2.2, 4.4],
        align=["L", "L"],
    )

    w.finish()
    rep = doc.validate()
    if rep.issues:
        raise SystemExit(f"검증 실패: {rep.issues}")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save_to_path(str(out))
    print("저장:", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "drone_soc_test_guide.hwpx"))
    build(Path(ap.parse_args().out))
