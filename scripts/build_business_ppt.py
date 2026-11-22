"""사업 내용 발표자료 — 사업 내용 보고서(docs/drone_soc_business_report.hwpx)와 같은 구성.

    PYTHONPATH=src python scripts/build_business_ppt.py <회의자료 템플릿.pptx> [docs/drone_soc_business_talk.pptx]

템플릿은 2026. 9. 4. 회의자료 (표지·마지막 장·'Path Planning' 머리말을 재사용, scripts/build_ppt.py 의 Deck).
구성: 1 과제 개요 / 2 개발 알고리즘 / 3 알고리즘 검증 (3-1 가상 시운전, 3-2 실제 비행 데이터) / 4 탑재 방법 및 지원 계획
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_ppt as BP  # noqa: E402
from build_ppt import (FIG, ROOT, X0, Deck, MSO_ANCHOR, PP_ALIGN, arrow, box, box_text, normalize_partnames, notes,  # noqa: E402
                       picture)
from lxml import etree  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402

# ── 기관 양식(민간수탁연구사업 계획서)에 맞춘 서식 ──
#   본문 휴먼명조, 표 돋움 · 제목 줄 #BFBFBF · 검은 가는 선, 소제목 '■', 글머리 '-', 강조는 남색 한 가지
FONT_BODY, FONT_TABLE = "휴먼명조", "돋움"
BP.set_run.__defaults__ = (None, None, None, FONT_BODY)          # 모든 글자의 기본 글꼴
INK, GRAY = RGBColor(0x00, 0x00, 0x00), RGBColor(0x40, 0x40, 0x40)
NAVY, TEAL, GREEN = RGBColor(0x1F, 0x38, 0x64), RGBColor(0x40, 0x40, 0x40), RGBColor(0x59, 0x59, 0x59)
ORANGE = RGBColor(0xC0, 0x00, 0x00)                               # 강조 수치
BOX, LIGHT, HEAD_FILL, WHITE = RGBColor(0xF2, 0xF2, 0xF2), RGBColor(0xF2, 0xF2, 0xF2), RGBColor(0xBF, 0xBF, 0xBF), RGBColor(0xFF, 0xFF, 0xFF)
TITLE = INK
SRC_Y = BP.SRC_Y
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TABLE_GRID = "{5940675A-B579-460E-94D1-54222C63F5DA}"           # 'No Style, Table Grid': 검은 가는 선


def textbox(slide, x, y, w, h, paras, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
            space_after=4, name=None):
    return BP.textbox(slide, x, y, w, h, paras, size=size, color=color, bold=bold, align=align, anchor=anchor,
                      space_after=space_after, name=name)


def head(slide, x, y, text, w=6.0, size=16):
    """양식의 '■ 소제목'."""
    return textbox(slide, x, y, w, 0.36, [[("■ ", {"color": INK, "bold": True}), (text, {"color": INK, "bold": True})]], size=size)


def items(slide, x, y, w, h, rows, size=14, gap=5, mark="- "):
    """양식의 '- 항목'. rows: str | (굵은 앞말, 뒷말)."""
    paras = []
    for r in rows:
        if isinstance(r, tuple):
            paras.append([(mark, {}), (r[0], {"bold": True}), (r[1], {})])
        else:
            paras.append([(mark, {}), (r, {})])
    return textbox(slide, x, y, w, h, paras, size=size, space_after=gap)


def stat(slide, x, y, w, h, label, value, sub, dark=False, vsize=26):
    sp = box(slide, x, y, w, h, fill=LIGHT, line=NAVY if dark else HEAD_FILL, shadow=False)
    box_text(sp, [[(label, {"size": 12, "bold": False, "color": GRAY})],
                  [(value, {"size": vsize, "bold": True, "color": ORANGE if dark else NAVY})],
                  [(sub, {"size": 12, "bold": False, "color": GRAY})]])
    return sp


def source(slide, text):
    return textbox(slide, X0, SRC_Y, 10.6, 0.3, [text], size=10.5, color=GRAY)


def table(slide, x, y, w, rows, col_w, row_h=0.36, head_size=13, body_size=11, highlight=(), first_col_center=True,
          aligns=None):
    """양식의 표: 돋움, 제목 줄 회색(#BFBFBF) 바탕 검은 글자, 검은 가는 선. highlight 줄은 연회색."""
    nr, nc = len(rows), len(rows[0])
    gt = slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * nr))
    tbl = gt.table
    tblPr = tbl._tbl.tblPr
    tblPr.set("firstRow", "0")
    tblPr.set("bandRow", "0")
    sid = tblPr.find(_A + "tableStyleId")
    if sid is None:
        sid = etree.SubElement(tblPr, _A + "tableStyleId")
    sid.text = TABLE_GRID
    tot = sum(col_w)
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = Emu(int(Inches(w) * cw / tot))
    for i in range(nr):
        tbl.rows[i].height = Inches(row_h)
        for j in range(nc):
            c = tbl.cell(i, j)
            c.margin_left = c.margin_right = Inches(0.06)
            c.margin_top = c.margin_bottom = Inches(0.02)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            c.fill.solid()
            c.fill.fore_color.rgb = HEAD_FILL if i == 0 else (LIGHT if i in highlight else WHITE)
            val = rows[i][j]
            runs = val if isinstance(val, list) else [(str(val), {})]
            para = c.text_frame.paragraphs[0]
            c.text_frame.word_wrap = True
            if i == 0 or (j == 0 and first_col_center):
                para.alignment = PP_ALIGN.CENTER
            elif aligns:
                para.alignment = aligns[j]
            else:
                para.alignment = PP_ALIGN.LEFT
            for text, o in runs:
                r = para.add_run()
                r.text = text
                BP.set_run(r, o.get("size", head_size if i == 0 else body_size), o.get("bold", False), o.get("color", INK),
                           font=FONT_TABLE)
    return gt


MAN = ROOT / "docs" / "manual_img"
from report_values import (wind_trend, SETS_M4T, SETS_REF, _f, cmp3d_rows, e13_rows, e13_split_rows, e15_table,  # noqa: E402
                           summary_values)


def J(name):
    return json.loads((ROOT / "results" / name).read_text(encoding="utf-8"))


def section_tag(s, text):
    """오른쪽 위 장 표시 (예: '3. 알고리즘 검증')."""
    textbox(s, 9.0, 0.32, 3.75, 0.32, [text], size=11, bold=False, color=GRAY, align=PP_ALIGN.RIGHT)


def code(s, x, y, w, lines, size=12):
    sp = box(s, x, y, w, 0.12 + 0.3 * len(lines), fill=RGB_CODE, shadow=False)
    box_text(sp, [[(t, {"size": size, "bold": False, "color": INK})] for t in lines], anchor=MSO_ANCHOR.MIDDLE)
    return sp


RGB_CODE = RGBColor(0xF2, 0xF2, 0xF2)


def build(template: str, out: str) -> None:
    D = Deck(template)
    D.set_title_page(["배터리 잔존 용량을 고려한", "드론 경로 최적화 알고리즘 개발"], "2026. 11.")
    V = summary_values()
    M = V["M"]
    T15 = e15_table()
    WT = wind_trend()
    SITL = J("sitl_check.json")

    # ── 목차 ───────────────────────────────────────────
    s = D.content("목차")
    chapters = [
        ("1", "과제 개요", "과제 배경 · 목표 · 요약"),
        ("2", "개발 알고리즘", "경로 탐색 선정 · 에너지·SoC 모델 · 3차원 시공간 경로계획 · 장애물 회피 · 속도 갱신"),
        ("3", "알고리즘 검증", "3-1 가상 시운전 검증 · 3-2 실제 비행 데이터 검증"),
        ("4", "알고리즘 탑재 방법 및 지원 계획", "탑재 구성 · 설치·경로 생성 매뉴얼 · 기체 적용 · 활용 · 지원 계획"),
    ]
    for n, (no, t, sub) in enumerate(chapters):
        y = 1.35 + 1.35 * n
        b = box(s, 1.4, y, 1.0, 1.0, fill=NAVY, shadow=False)
        box_text(b, [[(no, {"size": 30, "color": WHITE})]], align=PP_ALIGN.CENTER)
        textbox(s, 2.65, y + 0.05, 9.5, 0.5, [t], size=22, color=INK)
        textbox(s, 2.65, y + 0.55, 9.5, 0.4, [sub], size=13, bold=False, color=GRAY)

    # ===================== 1. 과제 개요 =====================
    s = D.content("1-1. 과제 배경")
    section_tag(s, "1. 과제 개요")
    head(s, X0, 1.05, "왜 SoC 기준 경로가 필요한가")
    stat(s, X0, 1.5, 3.9, 1.35, "실측 비행: 거리로 에너지 설명", "R² −1.47", "거리는 에너지를 설명하지 못함 (DJI M100)")
    stat(s, X0 + 4.12, 1.5, 3.9, 1.35, "최단 경로 계획, 강풍일", "12회 모두 불가", "임무 불가 판정")
    stat(s, X0 + 8.24, 1.5, 3.9, 1.35, "10 m/s 바람의 영향 (방향별)", "−38 ~ +150 %", "미터당 에너지 (탑재중량 14 %, 공기밀도 6 %)", dark=True, vsize=24)
    head(s, X0, 3.1, "기존 경로계획과 본 과제의 차이")
    table(s, X0, 3.5, 12.15, [
        ["구분", "기존 (최단 경로)", "본 과제 (에너지 최적 경로)"],
        ["최소화 목표", "이동 거리", "배터리 소모 (SoC), 도착 예비 20 % 제약"],
        ["바람", "미반영", "고도·시간별 수치예보 바람장 (순풍·역풍·측풍)"],
        ["왕복 · 속도", "갈 때·올 때 비슷한 길, 정속 (예: 10 m/s)", "갈 때·올 때 다른 길 (순풍 활용), 구간별 최적 속도"],
        ["호버링 판단", "시간 기준 (우회 5초 vs 대기 3초)", "에너지 기준 (우회 589 W vs 대기 491 W)"],
        ["비행 중", "출발 전 계획 고정", "30초 주기 속도 갱신, 장애물 변경 시 재계획"],
    ], [1.6, 4.6, 5.95], row_h=0.5, head_size=13, body_size=12.5, highlight=(1,))
    source(s, "R²·12회: CMU DJI M100 공개 실측 비행 분석 · 바람 영향: 보정된 전력 모델 · 호버링 예: 2026. 9. 4. 회의자료")
    notes(s, "임무 성패는 거리가 아니라 배터리 잔량으로 정해집니다. 실측 비행에서 거리는 에너지를 설명하지 못했고, 바람은 방향에 따라 "
             "에너지를 크게 바꿉니다. 그래서 목적을 거리에서 SoC로 바꿨습니다.")

    s = D.content("1-2. 과제 목표")
    section_tag(s, "1. 과제 개요")
    head(s, X0, 1.05, "최종목표")
    b = box(s, X0, 1.48, 12.15, 1.25, fill=LIGHT, shadow=False)
    box_text(b, [[("- ", {"color": INK}), ("SoC를 명시 상태 변수로 포함한 무인 운반 드론 특화 에너지 인식 경로 최적화 알고리즘 개발", {"color": INK})],
                 [("- ", {"color": INK}), ("가상 시뮬레이션 및 하드웨어 실증을 통한 알고리즘 성능 검증", {"color": INK})]], size=17, bold=False)
    head(s, X0, 2.95, "세부 목표")
    items(s, X0, 3.35, 12.15, 1.3, [
        "바람·지형·건물·비행 금지 구역·이동 장애물을 반영한 3차원 시공간 경로와 구간별 속도 산출",
        "비행 중 기상 정보 갱신에 따른 속도 재계산과 출발 전 신뢰도 판정 (go / caution / no-go)",
        "실증 기체(DJI Matrice 4T)·시험비행 기체(Holybro X650)에 올릴 수 있는 경로 파일과 실시간 연동",
    ], size=14, gap=4)
    head(s, X0, 4.75, "대상 기체")
    table(s, X0, 5.15, 12.15, [
        ["구분", "기체", "연동", "주요 제원"],
        ["실증 기체", "DJI Matrice 4T", "DJI Pilot 2 경로 파일 (WPML), Mobile SDK V5", "이륙 1219 g · 페이로드 200 g · 수평 21 m/s · 내풍 12 m/s · 49분"],
        ["시험비행 기체", "Holybro X650", "Pixhawk 6X + ArduCopter, MAVLink", "자체 4.1 kg · 최대 이륙 6.3 kg · 6S 222 Wh · 호버 약 30분"],
        ["개발 기체", "Holybro X500", "MAVLink", "프레임 610 g · 4S · 호버 약 18분"],
    ], [1.5, 1.8, 3.7, 5.15], row_h=0.42, head_size=12.5, body_size=11.5, highlight=(1,))
    notes(s, "최종목표 두 줄은 보고서 3장과 같은 문장입니다. 실증 기체는 Matrice 4T이고, 11월 13일 시험비행은 X650으로 했습니다.")

    s = D.content("1-3. 요약")
    section_tag(s, "1. 과제 개요")
    cards = [
        ("개발 알고리즘", ["경로 탐색 5종 비교 → Lazy Theta* 선정", "바람·시간·고도로 확장한 3차원 시공간 에너지 탐색 (WE-LT*)",
                       "SoC 모델 · 이동 장애물 안전 구간", "30초 속도 갱신 · 출발 판정"], NAVY),
        ("가상 시운전 검증", [f"실증 기체 M4T: 기존 경로 대비 SoC 소모 {V['r4']} 감소 ({V['n4']}건)", f"비교 기체 X650·M100: {V['rR']} 감소",
                         f"이동 장애물 {T15['obs_n']}건 침범 {T15['obs_col']}건", "ArduCopter SITL 미션 13/13 일치"], NAVY),
        ("실제 비행 데이터 검증", ["X650 시험비행 (2026-11-13, 103초)", "시간 예측 오차 3 %", "에너지: 제원 중량 −16 %, 중량 보정 +2 %",
                            "비행 중 위치·배터리·바람 약 2 Hz 수신"], NAVY),
        ("탑재 · 지원", ["지상 PC 설치 약 20초, 경로 생성 13초", "M4T: .kmz → DJI Pilot 2", "X650: .waypoints → Mission Planner, MAVLink 연동",
                      "M4T 비행 기록으로 모델 확인, 경로 파일 기종 추가 지원"], NAVY),
    ]
    for n, (t, lines, col) in enumerate(cards):
        x = X0 + n * 3.08
        hb = box(s, x, 1.15, 2.92, 0.62, fill=col, shadow=False)
        box_text(hb, [[(t, {"size": 16, "color": WHITE})]], align=PP_ALIGN.CENTER)
        bb = box(s, x, 1.85, 2.92, 4.3, fill=BOX, shadow=False)
        box_text(bb, [[("- ", {"bold": False, "color": INK}), (l_, {"bold": False, "color": INK})] for l_ in lines], size=14, anchor=MSO_ANCHOR.TOP)
        bb.text_frame.margin_top = Pt(11)
        for para in bb.text_frame.paragraphs:
            para.space_after = Pt(12)
    b = box(s, X0, 6.3, 12.15, 0.6, fill=LIGHT, shadow=False)
    box_text(b, [[("＊ 실증 기체 M4T의 전력 모델은 DJI 공식 사양(무탑재 호버 42분, 9 m/s 정속 49분)에 맞춘 값이며, 탑재·고속 비행은 M4T 비행 기록으로 확인합니다.", {"size": 13, "bold": False, "color": INK})]])

    # ===================== 2. 개발 알고리즘 =====================
    s = D.content("2-1. 알고리즘 구성과 개발 단계")
    section_tag(s, "2. 개발 알고리즘")
    picture(s, FIG / "F31_architecture.png", X0, 1.1, 7.1, 4.6)
    head(s, 7.95, 1.05, "개발 단계", w=4.8)
    table(s, 7.95, 1.45, 4.8, [
        ["단계", "탐색", "더해진 결과"],
        ["1. 최단 경로", "Lazy Theta*", "거리, 경로점"],
        ["2. 실제 지도", "Lazy Theta*", "판교·창원 지형·건물"],
        ["3. 바람", "A* 8방향", "비행 시간, 에너지"],
        ["4. 이동 장애물", "시공간 A*", "통과 시각"],
        ["5. 3차원", "WE-LT* 26방향", "고도, 구간 속도, SoC"],
    ], [1.55, 1.5, 1.75], row_h=0.5, head_size=12, body_size=11.5, highlight=(5,), first_col_center=False)
    items(s, X0, 5.85, 12.15, 1.0, [
        "입력(지도·바람·기체·임무) → 계획기가 경로와 구간 속도 결정 → 미션 파일로 기체에 올림 → 비행 중 실시간 엔진이 속도 갱신",
    ], size=13.5)

    s = D.content("2-2. 경로 탐색 알고리즘 선정")
    section_tag(s, "2. 개발 알고리즘")
    table(s, X0, 1.2, 12.15, [
        ["알고리즘", "구분", "계산 시간 (s)", "이동 거리 (m)", "경로점 수"],
        ["A*", "그래프 기반", "0.3017", "533.16", "12"],
        [[("Lazy Theta*  ★ 선정", {"color": ORANGE})], "Any-angle 기반", "0.2593", "526.88", "9"],
        ["Q-learning", "강화학습 기반", "0.6512", "533.77", "13"],
        ["Lazy Theta* + Q-learning", "하이브리드", "4.7769", "533.77", "13"],
        ["RRT*", "샘플링 기반", "1.3528", "539.64", "9"],
    ], [3.2, 2.3, 2.2, 2.2, 2.25], row_h=0.5, head_size=14, body_size=13.5, highlight=(2,))
    items(s, X0, 4.45, 12.15, 2.3, [
        ("계산 시간·이동 거리 최소, 경로점 최소  ", "같은 2D 지도에서 5종 비교 (2026. 6. 30.)"),
        ("가시선 검사를 필요한 때로 미룸  ", "격자에 묶이지 않는 직선 경로를 적은 계산으로, 결과가 항상 같음 (재현성)"),
        ("제외 이유  ", "RRT*는 실행마다 결과가 다르고, Q-learning 계열은 학습 횟수·보상 설계에 민감"),
        ("확장  ", "비용을 거리 → 에너지로 바꾸고 바람·시간·고도 차원을 더함 (WE-LT*)"),
    ], size=14, gap=6)

    s = D.content("2-3. 에너지 · SoC 모델")
    section_tag(s, "2. 개발 알고리즘")
    head(s, X0, 1.05, "전력 → 에너지 → SoC")
    flow = [("필요 전력", "유도 + 형상 + 동체 항력\n+ 상승 + 항전 장비"), ("구간 에너지", "바람장에서 대기속도를 풀어\n전력 × 비행 시간"),
            ("SoC 소모", "배터리 내부저항 반영\n(X650 실측 0.028 Ω)"), ("제약", "도착 예비 SoC\n20 % 이상")]
    for n, (t, d) in enumerate(flow):
        x = X0 + n * 3.12
        b = box(s, x, 1.5, 2.7, 1.55, fill=NAVY if n == 2 else BOX, shadow=False)
        box_text(b, [[(t, {"size": 16, "color": WHITE if n == 2 else NAVY})], [(d, {"size": 12, "bold": False, "color": WHITE if n == 2 else INK})]],
                 align=PP_ALIGN.CENTER)
        if n < 3:
            arrow(s, x + 2.75, 2.1, 0.32, 0.35)
    head(s, X0, 3.35, "기체별 전력 모델 현황")
    table(s, X0, 3.75, 12.15, [
        ["기체", "모델 계수의 근거", "검증 상태"],
        ["DJI M100", "CMU 공개 실측 비행 198회로 보정", "보정에 쓰지 않은 56회로 예측 확인"],
        ["Holybro X650", "제원 (4.1 kg, 회전면 0.456 m², 222 Wh) + 제조사 호버 30분", "2026-11-13 시험비행으로 비교 (3-2)"],
        [[("DJI Matrice 4T (실증)", {"color": NAVY, "bold": True})], f"공식 사양 두 점 (호버 42분, 9 m/s 정속 49분): 효율 {M['eta']:.2f}, 항력 면적 {M['cda']:.3f} m²",
         "공식 사양과 일치, 실측 비행 비교 전"],
    ], [2.4, 5.4, 4.35], row_h=0.55, head_size=13, body_size=12.5, highlight=(3,))
    b0, b2 = M["by_payload"]["0.0"], M["by_payload"]["0.2"]
    items(s, X0, 6.05, 12.15, 0.9, [
        ("M4T 모델  ", f"무탑재 호버 {b0['hover_w']:.0f} W, 0.2 kg 탑재 호버 {b2['hover_w']:.0f} W ({b2['hover_min']:.1f}분), 효율 속도 약 {b2['v_best']:.0f} m/s, "
                      f"최대 거리 {b0['range_km']:.1f} km (DJI 표기 35 km보다 짧게 = 보수적)"),
    ], size=12.5)
    source(s, "전력 모델: 회전익 이론 (Leishman 2006; Zeng 외 2019) · M100 자료: Rodrigues 외, Scientific Data 2021")

    s = D.content("2-4. 바람장 반영 3차원 시공간 경로계획")
    section_tag(s, "2. 개발 알고리즘")
    picture(s, FIG / "F4_paths_realmap.png", X0, 1.1, 12.15, 3.6)
    items(s, X0, 4.9, 12.15, 2.0, [
        ("시공간 바람장  ", "수치예보(GFS)의 고도별·시간별 바람을 계획 영역에 보간"),
        ("경로와 속도를 함께  ", "26방향 3차원 탐색 + 구간별 최적 대기속도 (대지속도 상한 안)"),
        ("왕복을 따로  ", "가는 편과 오는 편의 바람이 달라 순풍 쪽 고도·경로를 고름"),
        ("안전장치  ", "예보 기준으로 제안 경로가 최단 경로보다 불리하면 최단 경로를 선택"),
    ], size=14, gap=5)

    s = D.content("2-5~6. 장애물 회피와 비행 중 갱신")
    section_tag(s, "2. 개발 알고리즘")
    head(s, X0, 1.05, "비행 금지 구역 · 이동 장애물", w=5.8)
    items(s, X0, 1.45, 5.8, 2.3, [
        "금지 구역·건물: 3차원 점유 격자",
        "이동 장애물: 시간 축 안전 구간 (Safe Interval)",
        "실측 ADS-B 예측 오차로 만든 불확실성 여유",
        "우회 vs 호버 대기를 에너지로 비교",
        "장애물·금지 구역이 바뀌면 남은 구간 재계획",
    ], size=13.5, gap=5)
    head(s, 6.95, 1.05, "출발 판정", w=5.8)
    table(s, 6.95, 1.45, 5.8, [
        ["판정", "조건"],
        [[("go", {"color": GREEN})], "임무 성립 확률 ≥ 요구 신뢰수준"],
        [[("caution", {"color": ORANGE})], "예보대로면 성립, 신뢰수준 미달"],
        [[("no-go", {"color": RGB_RED})], "예비 SoC 미확보 또는 확률 50 % 미만"],
    ], [1.3, 4.5], row_h=0.48, head_size=12.5, body_size=12)
    head(s, X0, 3.8, "비행 중 속도 갱신 (30초 주기)의 판단 순서", w=8.0)
    picture(s, FIG / "F32_speed_update_flow.png", X0, 4.2, 12.15, 2.75)

    # ===================== 3. 알고리즘 검증 =====================
    s = D.content("3-1. 가상 시운전 – 기존 경로 대비 SoC")
    section_tag(s, "3. 알고리즘 검증")
    picture(s, FIG / "F28_superiority.png", X0, 1.08, 6.4, 5.0)
    rows = [["조합", "기존 → 제안", "변화 (95 % 구간)"]]
    for r in e13_rows(SETS_M4T):
        rows.append([[(r[0], {"bold": True})], f"{r[1][:-3]} → {r[2]}", [(r[3].lstrip("*"), {"bold": True, "color": NAVY})]])
    for r in e13_rows(SETS_REF):
        rows.append([r[0] + " (비교)", f"{r[1][:-3]} → {r[2]}", r[3].lstrip("*")])
    table(s, 7.2, 1.15, 5.55, rows, [1.75, 1.6, 2.2], row_h=0.44, head_size=12, body_size=11, first_col_center=False)
    items(s, 7.2, 3.45, 5.55, 1.8, [
        ("실증 기체 M4T  ", f"판교·창원 {V['n4']}건, 탑재 0.2 kg, 공식 사양 보정 모델"),
        ("기존 경로  ", "최단 경로 + 정속 10 m/s"),
        ("M4T 절감의 출처  ", f"속도 선택 {V['sp4']}, 경로 모양 {V['pa4']}"),
        ("바람이 셀수록 절감 ↑  ", f"약풍일 {_f(WT['p_lo'])} % → 강풍일({WT['w_hi']:.0f} m/s) {_f(WT['p_hi'])} %"),
    ], size=12, gap=3)
    stat(s, 7.2, 5.3, 5.55, 1.1, f"실증 기체 M4T: SoC 소모 변화 ({V['n4']}건)", f"−{V['r4']}", f"제안이 우수한 경우 {V['win4']}건 · 비교 기체 −{V['rR']}", dark=True, vsize=22)
    source(s, "가상 시운전 · 계획 00Z 예보, 평가 06Z 예보 · 편도 1.5–2.8 km · M4T 모델은 공식 사양 보정(탑재·고속 실측 전)")

    s = D.content("3-1. 가상 시운전 – 3D 화면 왕복 비교")
    section_tag(s, "3. 알고리즘 검증")
    picture(s, FIG / "platform_3d_compare_pangyo_m4t.png", X0, 1.08, 7.4, 5.0)
    rows = [["지역 · 기체", "기존", "제안", "변화"]]
    for r in cmp3d_rows(SETS_M4T):
        rows.append([[(r[0], {"bold": True})], r[1].replace(" (임무 불가)", "\n(임무 불가)"), r[3].lstrip("*"), [(r[4], {"bold": True, "color": NAVY})]])
    for r in cmp3d_rows([("pangyo_x650", "판교 · X650"), ("changwon_x650", "창원 · X650"), ("pangyo_m100", "판교 · M100")]):
        rows.append([r[0] + " (비교)", r[1].replace(" (임무 불가)", "\n(임무 불가)"), r[3].lstrip("*"), r[4]])
    table(s, 8.2, 1.15, 4.55, rows, [1.75, 1.0, 0.9, 0.9], row_h=0.48, head_size=12, body_size=11, first_col_center=False)
    items(s, 8.2, 4.35, 4.55, 2.4, [
        "2026-10-27 예보, 15시 출발 왕복, M4T 탑재 0.2 kg",
        "판교 15시 맞바람 약 11 m/s: M4T 내풍 한계(12 m/s)에 가까워 출발 판정으로 걸러야 함",
        "이 강풍 사례는 경로 모양의 몫이 컸음 (여러 날 평균은 속도의 몫이 더 큼)",
    ], size=12, gap=5)

    s = D.content("3-1. 가상 시운전 – 비행 중 갱신·신뢰도")
    section_tag(s, "3. 알고리즘 검증")
    head(s, X0, 1.05, "실시간 엔진 폐루프: SoC 소모 변화 (30초 속도 갱신 + 안전장치)", w=8)
    table(s, X0, 1.45, 7.3, [[h.replace(" (비교)", "\n(비교)") for h in T15["head"]]] + T15["rows"],
          [2.6] + [1.0] * T15["n_sets"], row_h=0.46, head_size=10.5, body_size=11, first_col_center=False)
    items(s, X0, 4.4, 7.3, 2.4, [
        ("갱신 주기  ", "틀린 예보 손해를 2분 주기 12–35 %, 30초 주기 42–73 % 회복"),
        ("속도 갱신의 안전성  ", f"{T15['n_cases']}건 모두 안전장치만 쓴 경우보다 나쁘지 않음, 판단 1회 2–3 ms"),
        ("신뢰도  ", "95 % 상한 포함률 409/432 (94.7 %), 36일 · 2지역"),
        ("필요 SoC 여유  ", "6시간 전 예보 3.7 %, 30시간 전 14 %, 78시간 전 27 %"),
    ], size=12.5, gap=5)
    stat(s, 8.25, 1.45, 4.5, 1.6, f"가로지르는 이동 장애물 {T15['obs_n']}건", f"침범 {T15['obs_col']}건",
         f"완주 {T15['obs_done']}건 · 최소 이격 {T15['obs_sep'][0]:.0f}–{T15['obs_sep'][1]:.0f} m", dark=True)
    stat(s, 8.25, 3.25, 4.5, 1.6, "30초 속도 갱신의 손해 회복", "42–73 %", "틀린 예보로 생긴 SoC 손해 대비")
    stat(s, 8.25, 5.05, 4.5, 1.6, "출발 판정 95 % 상한 포함률", "94.7 %", "409 / 432건")

    s = D.content("3-1. 가상 시운전 – 비행제어기 SITL")
    section_tag(s, "3. 알고리즘 검증")
    c, m = SITL["checks"], SITL["mission"]
    table(s, X0, 1.2, 7.0, [
        ["점검 항목", "결과"],
        ["펌웨어", SITL["firmware"]],
        ["미션 업로드 / 읽기", f"{c['mission_count_sent']} / {c['mission_count_readback']} 항목 (일치)"],
        ["AUTO 모드 시동 · 완주", "성공" if c["armed_in_auto"] and c["flight_completed"] else "실패"],
        ["구간 속도 명령 (DO_CHANGE_SPEED)", "모두 수락" if c["speed_cmds_all_accepted"] else "일부 거부"],
        ["바람 메시지 수신", "수신" if c["wind_received"] else "미수신"],
        ["비행 로그 읽기", "가능" if c["dataflash_readable"] else "불가"],
        ["미션 규모 (창원 · X650)", f"{m['distance_m']/1000:.2f} km · 예상 {m['duration_s']:.0f} s · 경로점 {m['n_waypoints']}개"],
    ], [3.3, 3.7], row_h=0.52, head_size=13, body_size=12.5, first_col_center=False)
    items(s, 8.0, 1.2, 4.75, 3.5, [
        ("SITL이란  ", "실제 비행제어 소프트웨어(ArduCopter)를 PC에서 돌려 기체 없이 미션·명령을 시험하는 방식"),
        ("확인한 것  ", "계획기가 만든 미션 파일을 비행제어기가 그대로 받아 끝까지 비행하고, 비행 중 보낸 속도 명령을 수락함"),
        ("다음 단계  ", "실기체에서 조종기 경유 링크로 속도 명령 송신 시험"),
    ], size=14.5, gap=12)
    stat(s, 8.0, 5.0, 4.75, 1.35, "미션 항목 업로드 / 읽기", f"{c['mission_count_sent']} / {c['mission_count_readback']}", "속도 명령 전부 수락 · 완주", dark=True)

    s = D.content("3-2. 실제 비행 – X650 시험비행 결과")
    section_tag(s, "3. 알고리즘 검증")
    head(s, X0, 1.05, "비행 결과 (기체 로그)", w=5.5)
    table(s, X0, 1.45, 5.5, [
        ["항목", "값"],
        ["미션", "이륙 15 m → 경로점 4개 → RTL, 3 m/s"],
        ["비행 시간", "103 s"],
        ["사용 에너지", "14.6 Wh (배터리의 6.6 %)"],
        ["평균 전류 · 전력", "22.1 A · 512 W"],
        ["최고 고도 · 수평 속도", "30.2 m · 3.5 m/s"],
        ["내부저항 (추정)", "0.028 Ω (가정 0.03 Ω)"],
    ], [2.0, 3.5], row_h=0.46, head_size=12.5, body_size=12, first_col_center=False)
    head(s, 6.5, 1.05, "알고리즘 예측과의 비교", w=6.2)
    table(s, 6.5, 1.45, 6.25, [
        ["항목", "실측", "예측", "오차"],
        ["경로점 구간 시간", "45.4 s", "46.9 s", [("+3 %", {"color": GREEN})]],
        ["에너지 (제원 4.1 kg)", "14.6 Wh", "12.5 Wh", [("−16 %", {"color": ORANGE})]],
        ["에너지 (보정 4.66 kg)", "14.6 Wh", "14.9 Wh", [("+2 %", {"color": GREEN})]],
        ["호버 전력", "515 W", "434 W", "−16 %"],
    ], [2.35, 1.3, 1.3, 1.3], row_h=0.46, head_size=12.5, body_size=12, first_col_center=False)
    items(s, 6.5, 3.95, 6.25, 2.9, [
        ("시간 예측  ", "3 % 안으로 맞음"),
        ("에너지 차이의 원인  ", "카메라·짐벌·RTK를 단 실제 이륙 중량이 제원보다 무거운 것으로 보임 → 중량 실측 필요"),
        ("아직 못 본 것  ", "3.5 m/s 이하 비행이라 고속 전력(동체 항력)은 미검증"),
    ], size=12.5, gap=6)

    s = D.content("3-2. 실제 비행 – 구간별 전력·수신 데이터")
    section_tag(s, "3. 알고리즘 검증")
    picture(s, FIG / "F37_flight_20261113.png", X0, 1.08, 7.0, 4.0)
    head(s, 7.85, 1.05, "비행 중 수신 (발주처 정리 문서)", w=4.9)
    table(s, 7.85, 1.45, 4.9, [
        ["알고리즘이 쓰는 값", "메시지", "수신"],
        ["위치 · 속도", "GLOBAL_POSITION_INT", "약 2 Hz"],
        ["바람 추정", "WIND", [("값 0 *", {"color": ORANGE})]],
        ["배터리", "BATTERY_STATUS", "약 2 Hz"],
        ["현재 경로점", "MISSION_CURRENT", "약 2 Hz"],
    ], [1.5, 2.3, 1.1], row_h=0.46, head_size=11.5, body_size=11, first_col_center=False)
    textbox(s, 7.85, 3.85, 4.9, 0.6, ["* EK3_DRAG_BCOEF_X/Y = 0이라 바람 추정이 꺼져 있음 → 설정 필요"], size=11, bold=False, color=GRAY)
    head(s, X0, 5.2, "실증 지역(고성) 왕복 400 m 재추정", w=12)
    items(s, X0, 5.6, 12.15, 1.3, [
        "2분 안에 왕복하려면 고도 20 m에서 7–8 m/s 필요 (1분 53–58초) · 고도 30 m, 7 m/s면 약 2분 11초",
        "실증 기체 M4T의 비행 데이터는 아직 받지 못해, M4T 예측 검증은 M4T 비행 기록을 받은 뒤 같은 방식으로 수행",
    ], size=13, gap=4)

    # ===================== 4. 탑재 방법 및 지원 계획 =====================
    s = D.content("4-1. 알고리즘 탑재 구성")
    section_tag(s, "4. 탑재 방법 및 지원 계획")
    picture(s, FIG / "F38_deploy_m4t.png", X0, 1.05, 8.3, 4.4)
    items(s, 9.1, 1.15, 3.65, 4.3, [
        ("지상 PC  ", "알고리즘(계획 서버) 실행"),
        ("실증 기체 M4T  ", ".kmz 경로 파일 → DJI Pilot 2"),
        ("시험비행 기체 X650  ", ".waypoints → Mission Planner, MAVLink로 비행 중 속도 갱신"),
        ("M4T 비행 중 연동  ", "Mobile SDK V5 앱 필요 (개발 예정)"),
    ], size=12.5, gap=8)
    table(s, X0, 5.6, 12.15, [
        ["지상 PC", "브라우저", "설치 파일", "기체 쪽 소프트웨어"],
        ["Python 3.10 이상 (시험: Linux)", "Chrome · Edge 최신판 (WebGL)", "프로그램 0.4 MB + 자료 묶음 20 MB", "DJI Pilot 2 / Mission Planner · QGC"],
    ], [3, 3, 3.15, 3], row_h=0.45, head_size=12, body_size=11.5)

    s = D.content("4-2. 설치 및 실행 (매뉴얼)")
    section_tag(s, "4. 탑재 방법 및 지원 계획")
    head(s, X0, 1.05, "① 설치 (처음 한 번, 약 20초)", w=6)
    code(s, X0, 1.45, 5.9, ["python3 -m venv dpo-env", "source dpo-env/bin/activate",
                           'pip install "drone_path_opt-1.2.0-py3-none-any.whl[mavlink]"', "unzip dpo_data_1.2.0.zip"], size=11.5)
    head(s, X0, 2.95, "② 점검 (8초)", w=6)
    code(s, X0, 3.35, 5.9, ["dpo-plan --check"], size=11.5)
    head(s, X0, 3.95, "③ 켜기 (4초) → 3D 화면이 브라우저에 열림", w=6)
    code(s, X0, 4.35, 5.9, ["dpo-server --open"], size=11.5)
    table(s, X0, 5.05, 5.9, [
        ["설치", "점검", "서버 시작", "화면", "경로 생성"],
        ["약 17 s", "8 s", "3.8 s", "4.0 s", "13.1 s"],
    ], [1, 1, 1, 1, 1], row_h=0.42, head_size=11.5, body_size=12)
    textbox(s, X0, 6.0, 5.9, 0.8, ["끄기: 터미널에서 Ctrl+C  ·  Windows: py -m venv dpo-env, dpo-env\\Scripts\\activate (미시험)"], size=11, bold=False, color=GRAY)
    picture(s, MAN / "01_start.png", 6.75, 1.1, 6.0, 4.6)
    textbox(s, 6.75, 5.75, 6.0, 0.4, ["프로그램을 켠 화면: ① 3D 지도 ② 표시 선택 ③ 임무 설정"], size=11.5, bold=False, color=GRAY, align=PP_ALIGN.CENTER)

    s = D.content("4-3. 경로 생성 (매뉴얼)")
    section_tag(s, "4. 탑재 방법 및 지원 계획")
    table(s, X0, 1.15, 5.6, [
        ["순서", "할 일"],
        ["1", "기체와 탑재중량 고르기"],
        ["2", "임무 형태 (배송·회수 / 순회, 왕복)"],
        ["3", "지도에서 출발지·도착지 찍기"],
        ["4", "출발 시각 · 예비 SoC · 대지속도 상한"],
        ["5", "'경로 생성' (약 13초)"],
        ["6", "결과: 도착 SoC, 임무 가능 여부, 구간별 값"],
        ["7", "기존 경로와 비교"],
        ["8", "미션 파일 (.waypoints / .kmz) 내려받기"],
    ], [0.7, 4.9], row_h=0.47, head_size=12.5, body_size=12, highlight=(5, 8))
    picture(s, MAN / "07_result.png", 6.45, 1.1, 6.3, 2.85)
    picture(s, MAN / "10_export.png", 6.45, 4.05, 6.3, 2.85)
    textbox(s, X0, 5.5, 5.6, 1.3, ["화면 없이: dpo-plan --drone x650 --payload 1.0 --dep-hour 15 --out missions",
                                   "출발 판정까지: … --confidence 0.95 --forecast-age 6"], size=11, bold=False, color=GRAY)

    s = D.content("4-4~5. 기체 적용과 비행 전 점검")
    section_tag(s, "4. 탑재 방법 및 지원 계획")
    for n, (t, col, lines) in enumerate([
        ("실증 기체 DJI Matrice 4T", NAVY, [".kmz 경로 파일을 DJI Pilot 2에서 불러옴", "고도는 이륙점 기준 → 계획의 출발지에서 이륙",
                                            "대지속도 상한·탑재(≤ 200 g)를 M4T 제원에 맞춤", "선행: M4T 기종 번호 추가 · 불러오기 시험"]),
        ("시험비행 기체 Holybro X650", TEAL, [".waypoints를 Mission Planner로 업로드 (구간 속도 포함)", "비행 중 속도 갱신: dpo-server --mavlink udpin:0.0.0.0:14550",
                                              "EK3_DRAG_BCOEF_X/Y 설정 (바람 추정)", "BATT_CAPACITY = 10000 (잔량 % 보정)"]),
    ]):
        x = X0 + n * 6.15
        hb = box(s, x, 1.1, 6.0, 0.55, fill=col, shadow=False)
        box_text(hb, [[(t, {"size": 15, "color": WHITE})]], align=PP_ALIGN.CENTER)
        items(s, x + 0.05, 1.75, 5.9, 2.0, lines, size=13.5, gap=5)
    head(s, X0, 3.85, "비행 전 점검표", w=8)
    table(s, X0, 4.25, 12.15, [
        ["번호", "점검 항목", "M4T", "X650"],
        ["1", "경로 모양이 3D 화면의 경로와 같은지 · 고도가 이륙점 기준 상대 고도인지 (경로표 alt_rel_m 비교)", "○", "○"],
        ["2", "이륙 위치가 계획의 출발지와 같은지 · 출발 판정이 go인지", "○", "○"],
        ["3", "구간 속도, 복귀 고도, 신호 두절·종료 동작이 운용 기준에 맞는지", "○", "○"],
        ["4", "처음에는 낮은 속도로, 조종사가 언제든 개입할 수 있는 상태에서 시험", "○", "○"],
        ["5", "MAVLink 연결과 HEARTBEAT 수신 (비행 중 속도 갱신 시)", "–", "○"],
    ], [0.7, 9.65, 0.9, 0.9], row_h=0.44, head_size=12, body_size=11.5)

    s = D.content("4-6~7. 활용 및 지원 계획")
    section_tag(s, "4. 탑재 방법 및 지원 계획")
    head(s, X0, 1.0, "활용 계획", w=12)
    items(s, X0, 1.38, 12.15, 1.3, [
        "실증 기체 M4T로 실증 지역 왕복 비행 → 경로 파일 적용과 SoC 예측을 현장 검증 · 배송·점검 운용 계획 도구 (출발 전 경로·속도·판정)",
        "비행 로그를 계속 모아 기체별 전력 모델 갱신 · 학술지 투고, 지식재산 (확인 필요)",
    ], size=12.5, gap=3)
    head(s, X0, 2.45, "지원 계획", w=12)
    table(s, X0, 2.85, 12.15, [
        ["번호", "지원 항목", "내용", "필요한 것 (발주처)"],
        ["1", "M4T 전력 모델 구축", "호버·속도별 전력 곡선 맞춤, SoC 예측 검증", "30초 호버 + 왕복 5·7·10 m/s 비행 기록, 이륙 중량"],
        ["2", "M4T 경로 파일 지원", "기종·카메라 번호 추가, DJI Pilot 2 불러오기 시험", "불러오기 결과 (화면·오류 문구)"],
        ["3", "실증 지역 지도 구축", "고성 지형·건물·금지 구역·바람 (현재 판교·창원)", "실증 지역 범위, 이착륙 지점"],
        ["4", "설치 · 교육", "설치 패키지, 매뉴얼 기반 운용자 교육, 원격 설치", "운용 PC 환경"],
        ["5", "M4T 비행 중 연동", "Mobile SDK V5 앱: 바람·배터리 수신, 속도 갱신", "개발용 M4T · 조종기 일정"],
        ["6", "X650 추가 시험 분석", "중량·속도별 시험 로그 분석, 바람 추정 정확도", ".bin · .tlog · 지상 풍속계 기록"],
        ["7", "유지보수", "오류 수정, 기체·지역 추가, 보고서 갱신", "오류 화면·로그"],
    ], [0.6, 2.4, 4.75, 4.4], row_h=0.44, head_size=12, body_size=11.2, highlight=(1, 2, 3))
    source(s, "지원 일정은 발주처와 협의하여 정함")

    s = D.content("정리")
    for n, (t, d, col) in enumerate([
        ("알고리즘", "SoC를 목적으로 바람·지형·장애물을 반영한 3차원 시공간 경로와 구간 속도를 함께 정함 (WE-LT*)", NAVY),
        ("가상 시운전", f"실증 기체 M4T: 기존 경로 대비 SoC 소모 {V['r4']} 감소 ({V['n4']}건), 이동 장애물 침범 0건, 비행제어기 연동 확인", NAVY),
        ("실제 비행", "X650 시험비행: 시간 예측 3 %, 에너지는 중량 보정 시 2 % (제원 중량 −16 %)", NAVY),
        ("다음 단계", "M4T 비행 기록 → 모델 확인 · M4T 경로 파일 → 실증 지역 지도 → M4T 실증", NAVY),
    ]):
        y = 1.2 + n * 1.35
        hb = box(s, X0, y, 2.6, 1.1, fill=col, shadow=False)
        box_text(hb, [[(t, {"size": 18, "color": WHITE})]], align=PP_ALIGN.CENTER)
        bb = box(s, X0 + 2.75, y, 9.4, 1.1, fill=BOX, shadow=False)
        box_text(bb, [[(d, {"size": 15, "bold": False, "color": INK})]])

    normalize_partnames(D.prs)
    D.prs.save(out)
    print("saved", out)


RGB_RED = RGBColor(0xC0, 0x00, 0x00)

if __name__ == "__main__":
    tpl = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "docs" / "drone_soc_business_talk.pptx")
    build(tpl, out)
