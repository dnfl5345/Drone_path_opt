"""회의자료(기존 KETI 포맷)를 템플릿으로 개발 결과 보고 PPT 생성.

사용: PYTHONPATH=src python scripts/build_ppt.py <template.pptx> <out.pptx>
- 표지/마지막 장은 템플릿 원본을 재사용, 본문 슬라이드는 템플릿의
  'Path Planning' 머리말·제목(KoPub돋움체 Bold 32pt, #376092)·좌측 세로선을 그대로 복제
- 수치는 results/summary.json 에서 읽어 자동 기입
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "results" / "figures"
S = json.loads((ROOT / "results" / "summary.json").read_text())

TEAL, NAVY, TITLE = RGBColor(0x1B, 0x6E, 0x8C), RGBColor(0x00, 0x4E, 0x8F), RGBColor(0x37, 0x60, 0x92)
INK, GRAY, LIGHT = RGBColor(0x16, 0x20, 0x2A), RGBColor(0x4C, 0x5A, 0x66), RGBColor(0xE3, 0xEE, 0xF3)
BOX, ORANGE, WHITE = RGBColor(0xF3, 0xF5, 0xF4), RGBColor(0xE4, 0x6C, 0x0A), RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x1B, 0x6C, 0xA8)
FONT = "맑은 고딕"


# ------------------------------------------------------------------ 기본 도구
def _row(kind, key):
    return next(r for r in S["E1_main"] if r["kind"] == kind and r["method"] == key)


def set_run(r, size=None, bold=None, color=None, font=FONT):
    if size:
        r.font.size = Pt(size)
    if bold is not None:
        r.font.bold = bold
    if color is not None:
        r.font.color.rgb = color
    if font:
        r.font.name = font
        rPr = r._r.get_or_add_rPr()
        for tag in ("a:ea", "a:cs"):
            el = rPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag.split(":")[1])
            if el is None:
                el = etree.SubElement(rPr, "{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag.split(":")[1])
            el.set("typeface", font)


def textbox(slide, x, y, w, h, paras, size=14, color=GRAY, bold=True, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
            space_after=4, name=None):
    """paras: list of str | list[(text, {opts})] (한 문단 안 여러 run)."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    for n, p in enumerate(paras):
        para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
        para.alignment = align
        para.space_after = Pt(space_after)
        runs = p if isinstance(p, list) else [(p, {})]
        for text, o in runs:
            r = para.add_run()
            r.text = text
            set_run(r, o.get("size", size), o.get("bold", bold), o.get("color", color))
    return tb


def picture(slide, path, x, y, w=None, h=None, name=None):
    """박스(x,y,w,h) 안에 종횡비 유지로 맞춤 배치(가운데 정렬)."""
    im = Image.open(path)
    ar = im.width / im.height
    if w and h:
        if w / h > ar:
            ww, hh = h * ar, h
        else:
            ww, hh = w, w / ar
        x, y = x + (w - ww) / 2, y + (h - hh) / 2
    elif w:
        ww, hh = w, w / ar
    else:
        ww, hh = h * ar, h
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(ww), Inches(hh))
    pic.name = name or Path(path).stem
    pic._element.nvPicPr.cNvPr.set("descr", Path(path).stem)
    return pic


def table(slide, x, y, w, rows, col_w, row_h=0.36, head_size=13, body_size=11, highlight=(), first_col_center=True,
          aligns=None):
    nr, nc = len(rows), len(rows[0])
    gt = slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * nr))
    tbl = gt.table
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
            c.fill.fore_color.rgb = TEAL if i == 0 else (LIGHT if i in highlight else WHITE)
            val = rows[i][j]
            runs = val if isinstance(val, list) else [(str(val), {})]
            tf = c.text_frame
            tf.word_wrap = True
            para = tf.paragraphs[0]
            if i == 0 or (j == 0 and first_col_center):
                para.alignment = PP_ALIGN.CENTER
            elif aligns:
                para.alignment = aligns[j]
            else:
                para.alignment = PP_ALIGN.LEFT
            for text, o in runs:
                r = para.add_run()
                r.text = text
                set_run(r, o.get("size", head_size if i == 0 else body_size), o.get("bold", True),
                        o.get("color", WHITE if i == 0 else INK))
    return gt


def box(slide, x, y, w, h, fill=BOX, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, shadow=True, name=None):
    sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(1.25)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sp.adjustments[0] = 0.12
    if not shadow:
        sp.shadow.inherit = False
    if name:
        sp.name = name
    sp.text_frame.text = ""
    return sp


def box_text(sp, paras, size=14, color=INK, bold=True, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE):
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.12)
    for n, p in enumerate(paras):
        para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
        para.alignment = align
        runs = p if isinstance(p, list) else [(p, {})]
        for text, o in runs:
            r = para.add_run()
            r.text = text
            set_run(r, o.get("size", size), o.get("bold", bold), o.get("color", color))


def arrow(slide, x, y, w, h, color=NAVY, shape=MSO_SHAPE.RIGHT_ARROW):
    sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.fill.solid(); sp.fill.fore_color.rgb = color
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


# ------------------------------------------------------------------ 템플릿 머리말 복제
class Deck:
    def __init__(self, template):
        self.prs = Presentation(template)
        sl = list(self.prs.slides)
        self.title_slide, self.end_slide = sl[0], sl[-1]
        # 본문 머리말(제목/Path Planning/세로선) 원본 XML 확보 (회의자료 9번 슬라이드)
        src = sl[8]
        self.hdr = [copy.deepcopy(sh._element) for sh in src.shapes
                    if sh.name in ("제목 1", "TextBox 10", "직선 연결선 4")]
        self.layout = src.slide_layout
        # 기존 본문 슬라이드(2~9) 제거
        sldIdLst = self.prs.slides._sldIdLst
        for sid in list(sldIdLst)[1:-1]:
            self.prs.part.drop_rel(sid.rId)
            sldIdLst.remove(sid)

    def content(self, title, size=None):
        s = self.prs.slides.add_slide(self.layout)
        for ph in list(s.placeholders):
            ph._element.getparent().remove(ph._element)
        for el in self.hdr:
            s.shapes._spTree.append(copy.deepcopy(el))
        for sh in s.shapes:
            if sh.name == "제목 1":
                r = sh.text_frame.paragraphs[0].runs[0]
                r.text = title
                if size:
                    r.font.size = Pt(size)
        # 마지막(End) 슬라이드 앞으로 이동
        lst = self.prs.slides._sldIdLst
        ids = list(lst)
        new = ids[-1]
        lst.remove(new)
        lst.insert(len(ids) - 2, new)
        return s

    def set_title_page(self, title_lines, date):
        for sh in self.title_slide.shapes:
            if not sh.has_text_frame:
                continue
            t = sh.text_frame.text
            if "2026" in t:
                runs = sh.text_frame.paragraphs[0].runs
                runs[0].text = date
                for r in runs[1:]:
                    r.text = ""
            elif "드론" in t:
                ps = sh.text_frame.paragraphs
                for n, p in enumerate(ps):
                    rs = p.runs
                    rs[0].text = title_lines[n] if n < len(title_lines) else ""
                    for r in rs[1:]:
                        r.text = ""
                    for r in rs:
                        r.font.size = Pt(36)


def normalize_partnames(prs):
    """슬라이드 삭제 후 python-pptx 가 재사용한 파트 이름 충돌 방지: 슬라이드·노트·미디어 일괄 재번호."""
    from pptx.opc.packuri import PackURI
    for n, sl in enumerate(prs.slides, 1):
        sl.part.partname = PackURI(f"/ppt/slides/slide{n}.xml")
        if sl.has_notes_slide:
            sl.notes_slide.part.partname = PackURI(f"/ppt/notesSlides/notesSlide{n}.xml")
    media = [pt for pt in prs.part.package.iter_parts() if str(pt.partname).startswith("/ppt/media/")]
    for n, pt in enumerate(media, 1):
        pt.partname = PackURI(f"/ppt/media/image{n}.{pt.partname.ext}")


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


# ------------------------------------------------------------------ 슬라이드 구성
def build(template, out):
    D = Deck(template)
    D.set_title_page(["바람·지형·탑재중량을 고려한", "드론 SoC 최대화 경로계획 알고리즘 개발"], "2026. 11.")
    E0 = S["E0_calibration"]
    dA, dD = _row("delivery", "A_shortest_v10"), _row("delivery", "D_WE_LTstar")
    pA, pD = _row("pickup", "A_shortest_v10"), _row("pickup", "D_WE_LTstar")
    dB, dC = _row("delivery", "B_shortest_vopt"), _row("delivery", "C_energy_Astar")

    # 2. 개발 개요 --------------------------------------------------------
    s = D.content("개발 개요 – 지난 회의 이후 진행 내용")
    rows = [["개발 항목", "구현 내용", "결과"],
            ["1. 최단 경로 → 실제 Map", "성남 판교·분당 6.2×5.5 km, 실제 지형(DSM 30 m)+건물 7,109동, 3D Lazy Theta*",
             "거리·웨이포인트"],
            ["2. 최적 경로 수식 개발", "식 ①~⑤ + 회전익 전력모델 (탑재중량·바람·속도·상승 반영)",
             f"실측 검증 R² {E0['test']['R2']:.2f}"],
            ["3. 바람 정보·바람 지도", "기상청·NOAA·ECMWF 조사 → GFS 고도별(10~100 m)·시간별 바람을 25 m 격자로",
             "3D·시간가변 바람장"],
            ["4. 최적 경로 → 실제 Map", "WE-LT*: 3D 시공간 에너지 Lazy Theta* + 호버링/우회 + 롤링 재계획",
             f"SOC {dA['soc_used']:.0f}→{dD['soc_used']:.0f}%p"],
            ["5. 드론 데이터와 비교", f"CMU DJI M100 실측 비행 {E0['n_train'] + E0['n_test']}회 (탑재 0~750 g, 바람 실측)",
             f"MAPE {E0['test']['MAPE']:.1f}%"],
            ["추가 요청 사항", "배송(갈 때 적재)/회수(올 때 적재) 중량 변화, 시간에 따른 경로 변경(출발시각·비행 중)",
             "시나리오 비교"]]
    table(s, 0.6, 1.25, 12.1, rows, [2.6, 7.3, 2.2], row_h=0.62, head_size=15, body_size=13, highlight=(4,))
    textbox(s, 0.6, 5.85, 12.1, 0.9, [
        [("○ 목표: ", {"color": TITLE}), ("장애물·비행금지구역과 충돌하지 않는 경로 중 ", {}),
         ("예상 SOC 감소량이 최소", {"color": ORANGE}), ("인 경로 → 도착 SOC(잔량) 최대화", {})],
        [("○ 기준 시나리오: ", {"color": TITLE}),
         ("2026-10-27 실제 강풍일(100 m 북서풍 10~12 m/s) 15시 출발, 거점↔배송지 2.4 km 왕복, 0.5 kg", {})]],
        size=14)
    notes(s, "사용자 요청 5개 항목과 추가 요청(중량 변화, 시간가변 경로)을 모두 구현. 4번 행 강조.")

    # 3. 최신 연구 동향 ----------------------------------------------------
    s = D.content("최신 경로 최적화 연구 동향 및 적용 기법")
    rows = [["연구 (출처 번호)", "핵심 아이디어", "본 개발 반영"],
            ["Lazy Theta* [1] (AAAI 2010)", "가시선(LOS) 검사를 확장 시점으로 지연한 any-angle 탐색", "탐색 골격 (지난 회의 선정)"],
            ["SIPP [2] (ICRA 2011)", "안전 시간구간 + 대기(wait) 행동", "호버링 대기 vs 우회 판단"],
            ["Betti Sorbelli 외 [4]\n(IEEE T-ITS 2021)", "바람·탑재중량 의존 배송 에너지, 비행 중 경로 변경",
             "배송/회수 중량, 이벤트 재계획"],
            ["WA-LPA* [5] (Drones 2025)", "LPA* 증분 재계획 + 에너지 모델 + 바람 정렬 휴리스틱 + 고도 계층", "에너지 비용·재계획"],
            ["Wind-augmented A* [6]\n(IEEE ITSC 2025)", "국소 바람 기반 에너지 예측으로 A* 휴리스틱 조정", "에너지 하한 휴리스틱"],
            ["Wind-aware Theta* [7]\n(Chinese J. Aeronautics 2026)", "CFD 도시 바람장 + Theta*, 바람 미고려 대비 에너지 7.69% 절감",
             "any-angle + 바람 에너지"],
            ["Zeng 외 [3] (IEEE TWC 2019)\n· Leishman [9]", "회전익 전진비행 전력 모델(운동량 이론)", "P(V, m) 수식, 실측 보정"]]
    table(s, 0.6, 1.2, 12.1, rows, [3.6, 5.3, 3.2], row_h=0.66, head_size=14, body_size=12)
    textbox(s, 0.6, 6.5, 12.1, 0.4, [[("→ 공통 요소(바람 에너지 비용 + any-angle + 재계획)를 하나의 탐색기 ", {}),
                                       ("WE-LT* (Wind-aware Energy Lazy Theta*)", {"color": ORANGE}),
                                       ("로 통합", {})]], size=13)
    notes(s, "모든 출처는 마지막 '참고문헌' 슬라이드에 번호로 정리. 1차 보고서의 'Highways in the Sky(TRB)'는 "
             "저자·DOI 확인이 불가하여 제외하고 [6] ITSC 2025로 대체.")

    # 4. 실제 Map ----------------------------------------------------------
    s = D.content("실제 Map 구축 – 지형·건물·비행금지구역·바람")
    picture(s, FIG / "F1_site_wind.png", 0.6, 1.05, 12.1, 5.0)
    textbox(s, 0.6, 6.15, 12.1, 0.8, [
        [("○ 지형: ", {"color": TITLE}), ("Copernicus GLO-30 DSM(30 m) → 25 m 격자  ", {}),
         ("○ 건물: ", {"color": TITLE}), ("Overture Maps(OSM 기반) 7,109동 높이", {})],
        [("○ 비행 가능 공간: ", {"color": TITLE}),
         ("지면+30~150 m(항공안전법), 건물 상단+20 m·수평 25 m 이격, 비행금지구역(예시) 제외 → 3D 점유격자", {})]],
        size=13)

    # 5. 바람 정보 출처 ----------------------------------------------------
    s = D.content("바람 정보 출처 조사 및 선정")
    rows = [["출처", "해상도 / 주기", "고도 정보", "접근", "본 개발 활용"],
            ["기상청 초단기예보 (공공데이터포털)", "5 km / 1시간, 6시간", "지상 10 m (UUU·VVV)", "API 키",
             "어댑터 구현 (kma_adapter.py)"],
            ["기상청 단기예보", "5 km / 1시간, 약 3일", "지상 10 m", "API 키", "출발시각 계획용 (어댑터 동일)"],
            ["기상청 LDAPS 국지모델 (API허브)", "1.5 km / 1시간", "연직 다층", "API허브 신청", "3D 바람장 고도화 1순위"],
            ["기상청 AWS·윈드프로파일러", "관측 지점", "지상 / 고도별 실측", "API허브", "비행 중 편향 보정"],
            [[("NOAA GFS (AWS Open Data) ★", {"color": ORANGE})], "0.25° / 1시간", "10~100 m 7개 층 + 975·950 hPa",
             "무료·무인증", [("본 개발 사용 (실제 예보)", {"color": ORANGE})]],
            ["ECMWF Open Data", "0.25° / 3시간", "기압면", "무료", "교차 검증 후보"]]
    table(s, 0.6, 1.2, 12.1, rows, [3.4, 2.2, 2.6, 1.4, 2.9], row_h=0.55, head_size=14, body_size=12, highlight=(5,))
    textbox(s, 0.6, 5.2, 12.1, 1.7, [
        [("○ 선정 이유: ", {"color": TITLE}),
         ("운용 고도(30~150 m) 바람 필요 → 기상청 동네예보는 지상 10 m만, GFS는 10~100 m 고도별·시간별 제공", {})],
        [("○ 기상청 연동: ", {"color": TITLE}),
         ("개발 환경에서 data.go.kr·apihub 접속 차단 → 동일 형식 어댑터 구현(격자변환 테스트), 키 등록 후 교체", {})],
        [("○ 사례일 선정: ", {"color": TITLE}),
         ("2026년 10월 31일치 GFS 스캔 → 정오 100 m 풍속 상위(10.1 m/s, 북서풍)인 10월 27일 선정", {})]], size=13)

    # 6. 바람 지도 ----------------------------------------------------------
    s = D.content("바람 지도 생성 – 고도별·시간별 3D 바람장")
    picture(s, FIG / "F2_wind_profile_time.png", 0.6, 1.0, 12.1, 4.5)
    textbox(s, 0.6, 5.6, 12.1, 1.4, [
        [("○ 다운스케일링: ", {"color": TITLE}),
         ("GFS 격자점 쌍선형 보간(수평) → log-고도 보간(연직) → 1시간 예보 선형 보간(시간)", {})],
        [("○ 국지 보정(간이): ", {"color": TITLE}),
         ("능선 가속·계곡 감속(지형 고도 편차 기반) + 건물 밀집지 저고도 감속 → 보정계수 0.80~1.19", {})],
        [("○ 결과: ", {"color": TITLE}), ("고도가 높을수록 바람이 강함(30 m ≈ 9 m/s, 150 m ≈ 12 m/s) → ", {}),
         ("역풍 구간은 낮게, 순풍 구간은 바람을 활용", {"color": ORANGE})]], size=13)

    # 7. 수식 -------------------------------------------------------------
    s = D.content("최적 경로 수식 – 에너지·SOC 모델")
    textbox(s, 4.3, 0.85, 8.4, 0.35, [[("근거: Principles of Helicopter Aerodynamics(Leishman), Zeng 외 2019 – "
                                        "실측 데이터로 계수 보정", {"size": 12, "color": GRAY})]])
    eqs = [("①", "구간 비행시간 t", "= 구간 거리 d ÷ 바람을 고려한 지면속도 Vg"),
           ("②", "구간 소비에너지 E", "= 예상 소비전력 P(Va, Vz, m) × t"),
           ("③", "경로 전체 에너지", "= Σ 구간 에너지 + 이륙·착륙 + 호버링 대기"),
           ("④", "예상 SOC 감소량", "≈ 전체 에너지 ÷ 배터리 에너지(99.9 Wh) × 100"),
           ("⑤", "에너지 최적 경로", "= 비충돌 경로 중 ④ 최소 (잔량 ≥ 20%)")]
    for n, (num, a, b) in enumerate(eqs):
        sp = box(s, 0.6, 1.35 + n * 0.78, 7.0, 0.66)
        box_text(sp, [[(num + " ", {"color": ORANGE, "size": 15}), (a + " ", {"color": TITLE, "size": 15}),
                       (b, {"size": 13, "color": INK})]])
    sp = box(s, 7.9, 1.35, 4.8, 3.78, fill=WHITE, line=TEAL)
    box_text(sp, [[("소비전력 P [W]", {"color": TITLE, "size": 15})],
                  [("= (P유도 + P형상 + P유해 + P상승) / η + P전장", {"size": 13})],
                  [("· P유도 = k·T·vᵢ ,  T = √((m g)² + D²)", {"size": 12, "color": GRAY})],
                  [("   → 탑재중량 m ↑ 이면 약 m^1.5 로 증가", {"size": 12, "color": ORANGE})],
                  [("· P형상 = P₀(1 + 3Va²/U²tip)  (날개 마찰)", {"size": 12, "color": GRAY})],
                  [("· P유해 = ½ρ·CdA·Va³  (공기저항)", {"size": 12, "color": GRAY})],
                  [("· P상승 = m g Vz  (하강 시 25% 회수)", {"size": 12, "color": GRAY})],
                  [("대기속도 Va = | Vg·ê − w |", {"color": TITLE, "size": 14})],
                  [("측풍은 기수를 틀어 상쇄, 남는 전진속도 ± 순풍/역풍", {"size": 12, "color": GRAY})],
                  [("배터리 내부저항 손실(I²R) 반영", {"size": 12, "color": GRAY})]],
             anchor=MSO_ANCHOR.MIDDLE)
    sp = box(s, 0.6, 5.35, 12.1, 1.45, fill=LIGHT)
    box_text(sp, [[("구간별 속도 최적화: ", {"color": TITLE, "size": 14}),
                   ("각 구간에서 지면속도 Vg(3~20 m/s)를 바꿔가며 '미터당 에너지 P/Vg'가 최소인 속도를 선택", {"size": 13})],
                  [("→ 순풍: 빠르게(바람을 타고), 역풍: 대기속도 한계(17 m/s) 안에서 가능한 속도, 바람 > 한계면 해당 방향 비행불가 처리",
                    {"size": 13, "color": GRAY})]])

    # 8. 검증 -------------------------------------------------------------
    s = D.content("에너지 모델 검증 – 실제 드론 비행 데이터와 비교")
    picture(s, FIG / "F3_energy_validation.png", 0.6, 1.0, 12.1, 4.0)
    stats = [(f"{E0['test']['R2']:.2f}", "검증 R² (미사용 날짜)"), (f"{E0['test']['MAPE']:.1f}%", "에너지 예측 오차 MAPE"),
             (f"{E0['base_dist']['R2']:.2f}", "거리 비례 모델 R²\n(최단경로식 가정)"),
             (f"{E0['adaptive']['MAPE']:.1f}%", "당일 2회 비행 온라인 보정 시")]
    for n, (v, lab) in enumerate(stats):
        x = 0.6 + n * 3.08
        textbox(s, x, 5.1, 2.9, 0.75, [[(v, {"size": 32, "color": ORANGE if n == 0 else TITLE})]],
                align=PP_ALIGN.CENTER)
        textbox(s, x, 5.85, 2.9, 0.6, [[(lab, {"size": 12, "color": GRAY})]], align=PP_ALIGN.CENTER)
    textbox(s, 0.6, 6.45, 11.0, 0.5, [[("데이터: Rodrigues et al., Scientific Data 8:155 (2021), DJI Matrice 100 택배 비행 "
                                        f"{E0['n_train'] + E0['n_test']}회 – 날짜 기준 학습 {E0['train_dates']}일 / 검증 {E0['test_dates']}일 분리",
                                        {"size": 11, "color": GRAY, "bold": False})]])

    # 9. 알고리즘 구조 ----------------------------------------------------
    s = D.content("제안 알고리즘 – WE-LT* (바람·에너지 Lazy Theta*)")
    steps = [("입력", "3D 지도(지형·건물)\n바람 예보(시간별)\n탑재중량·비행금지\n장애물 시간표"),
             ("① 3D 시공간 A*", "26방향 이웃\n구간 에너지 = 식 ②\n(도착 시각의 바람)\n+ 구간 속도 최적화"),
             ("② 에너지 Lazy θ*", "지름길(조부모 직결)\n먼저 가정 →\n가시선은 나중 검사\n에너지 작은 쪽 선택"),
             ("③ 호버링 vs 우회", "시간제 금지구역에\n막히면 호버링 대기\n후보 생성 →\n에너지로 자동 선택"),
             ("출력", "웨이포인트·고도\n통과 시각·속도\n구간 에너지\n예상 SOC")]
    for n, (h, b) in enumerate(steps):
        x = 0.6 + n * 2.5
        hd = box(s, x, 1.25, 2.15, 0.6, fill=TEAL if 0 < n < 4 else NAVY, shadow=False)
        box_text(hd, [[(h, {"color": WHITE, "size": 14})]], align=PP_ALIGN.CENTER)
        bd = box(s, x, 1.95, 2.15, 1.85)
        box_text(bd, [[(b, {"size": 12, "color": INK})]], align=PP_ALIGN.CENTER)
        if n < 4:
            arrow(s, x + 2.18, 2.6, 0.29, 0.4)
    lp = box(s, 0.6, 4.05, 12.1, 0.75, fill=LIGHT, shadow=False)
    box_text(lp, [[("④ 롤링 호라이즌 재계획(비행 중): ", {"color": TITLE, "size": 14}),
                   ("60 s 주기 또는 이벤트(돌발 금지구역 발표·새 예보) 시 현재 위치에서 ①~③ 재실행 + 기체 추정 바람으로 예보 편향 보정",
                    {"size": 13})]])
    rows = [["회의자료 개발 순서", "기존 계획", "이번 구현"],
            ["3. 최적(바람만)", "A* (8방향)", "3D 26방향 + 에너지 Lazy Theta* 지름길"],
            ["4. 최적(바람+동적 장애물)", "시공간 A*", "시간가변 바람 + 장애물 시간표 + 호버링 대기"],
            ["5. 3D 최적 경로", "3D 시공간 A* (26방향)", "고도 레이어 20 m, 30~150 m AGL, 출력: 고도·통과시각·에너지"]]
    table(s, 0.6, 5.0, 12.1, rows, [3.0, 3.0, 6.1], row_h=0.45, head_size=13, body_size=12)

    # 9-1. 정식화 ------------------------------------------------------------
    s = D.content("제안 알고리즘 정식화 – 문제 (P)와 핵심 규칙")
    picture(s, FIG / "F12_formulation.png", 0.6, 1.0, 12.1, 5.25)
    textbox(s, 0.6, 6.25, 12.1, 0.7, [
        [("○ 결정변수: ", {"color": TITLE}), ("경로(any-angle·고도) + 구간 속도 + 대기 시간을 ", {}),
         ("하나의 목적함수 ΔSOC", {"color": ORANGE}), ("로 동시 결정, 질량은 갈 때/올 때 별도", {})],
        [("상세 유도: docs/algorithm_comparison.md", {"color": GRAY, "size": 11})]], size=13)

    # 9-2. 최신 연구 대비 차이점 ---------------------------------------------
    s = D.content("최신 연구 대비 차이점 – 정식화 구성요소 비교")
    Y = [("● ", {"color": ORANGE})]
    rows = [["구성요소", "[4] Betti Sorbelli\n(T-ITS 2021)", "[5] WA-LPA*\n(Drones 2025)", "[6] Wind A*\n(ITSC 2025)",
             "[7] Wind Theta*\n(CJA 2026)", "제안 WE-LT*"],
            ["목적함수", "에너지 (예산 내 배송)", "에너지", "에너지", "에너지", [("ΔSOC = ΣP·t / E_b", {"color": ORANGE})]],
            ["바람 w", "변동 바람", "동적 바람장", "국소 바람", "CFD 도시 바람장", [("w(x, t) 3D·시간 보간", {"color": ORANGE})]],
            ["탐색 공간", "배송 그래프", "격자 + 고도 계층", "격자 A*", "any-angle (Theta*)", "3D 26방향 + any-angle"],
            ["지름길 규칙", "–", "–", "–", "LOS면 채택", [("LOS ∧ 에너지 비교", {"color": ORANGE})]],
            ["속도 결정", "미확인", "미확인", "미확인", "미확인", [("구간별 내부 최적화 e*", {"color": ORANGE})]],
            ["탑재 질량", "반영", "미확인", "미확인", "미확인", [("갈 때/올 때 별도 m_ℓ", {"color": ORANGE})]],
            ["대기(호버링)", "–", "미확인", "–", "–", [("에너지 기준 대기 vs 우회", {"color": ORANGE})]],
            ["재계획", "비행 중 경로 변경", "LPA* 증분 재계획", "–", "–", "롤링 호라이즌 + 바람 보정"],
            ["실측 보정", "–", "미확인", "미확인", "미확인", "DJI M100 198회 [8]"]]
    table(s, 0.6, 1.1, 12.1, rows, [1.6, 2.1, 2.0, 1.7, 2.0, 2.7], row_h=0.47, head_size=12, body_size=11,
          highlight=(4, 5), aligns=[PP_ALIGN.CENTER] * 6)
    textbox(s, 0.6, 5.85, 12.1, 1.1, [
        [("○ 차이 ①: ", {"color": TITLE}), ("바람장에서 c_E(p,v) ≤ c_E(p,u)+c_E(u,v) 불성립 → 'LOS면 직결'(Theta*) 대신 ", {}),
         ("에너지 비교로 직결 여부 결정", {"color": ORANGE})],
        [("○ 차이 ②: ", {"color": TITLE}), ("속도를 간선 비용의 내부 최적화로 흡수 → 경로·고도·속도·대기·질량을 ", {}),
         ("단일 ΔSOC로 동시 최적화", {"color": ORANGE})],
        [("※ 문헌 항목은 공개 초록 기준이며, '미확인'은 원문에서 확인하지 못한 항목", {"color": GRAY, "size": 11})]], size=13)
    notes(s, "[1] Lazy Theta*, [2] SIPP 는 구성 기법으로 사용(표에서 제외). 문헌 [5]~[7] 정식화는 초록 기준이며 "
             "원문 미확인 항목은 '미확인'으로 표기. 상세: docs/algorithm_comparison.md")

    # 10. 결과 ① 경로 -------------------------------------------------------
    import pickle
    st1 = pickle.load(open(ROOT / "results" / "E1.pkl", "rb"))["store"]
    Lb_A, Lb_D = st1[("delivery", "A_shortest_v10")]["back"]["length"], st1[("delivery", "D_WE_LTstar")]["back"]["length"]
    s = D.content("결과 ① 실제 Map 적용 – 최단 경로 vs 최적 경로")
    picture(s, FIG / "F4_paths_realmap.png", 0.6, 1.0, 12.1, 5.25)
    textbox(s, 0.6, 6.3, 12.1, 0.65, [
        [("○ 갈 때(순풍): ", {"color": TITLE}), ("직선에 가깝게, 빠른 속도로 바람을 탐  ", {}),
         ("○ 올 때(역풍): ", {"color": TITLE}), ("최단보다 ", {}),
         (f"{(Lb_D - Lb_A) / Lb_A * 100:.0f}% 긴 경로", {"color": ORANGE}),
         ("지만 바람 약한 저고도·계곡을 따라 비행", {})]], size=13)

    # 11. 결과 ② SOC -------------------------------------------------------
    s = D.content("결과 ② 방법별 SOC 소모 비교 (실제 바람 기준 평가)")
    picture(s, FIG / "F5_soc_methods.png", 0.6, 1.0, 12.1, 4.15)
    rows = [["구분", "최단+정속 10 m/s", "최단+속도최적화", "에너지 A*(26방향)", "제안 WE-LT*"],
            ["배송 SOC 소모 [%p]"] + [f"{_row('delivery', k)['soc_used']:.1f}" for k in
                                     ("A_shortest_v10", "B_shortest_vopt", "C_energy_Astar", "D_WE_LTstar")],
            ["회수 SOC 소모 [%p]"] + [f"{_row('pickup', k)['soc_used']:.1f}" for k in
                                     ("A_shortest_v10", "B_shortest_vopt", "C_energy_Astar", "D_WE_LTstar")],
            ["웨이포인트 수 (배송)"] + [f"{_row('delivery', k)['waypoints']}" for k in
                                     ("A_shortest_v10", "B_shortest_vopt", "C_energy_Astar", "D_WE_LTstar")]]
    table(s, 0.6, 5.2, 9.3, rows, [2.2, 1.8, 1.8, 1.8, 1.7], row_h=0.36, head_size=12, body_size=12, highlight=(1,),
          aligns=[PP_ALIGN.CENTER] * 5)
    sp = box(s, 10.1, 5.2, 2.6, 1.45, fill=LIGHT, shadow=False)
    box_text(sp, [[(f"−{dA['soc_used'] - dD['soc_used']:.0f}%p", {"size": 28, "color": ORANGE})],
                  [("기존 대비 SOC 절감\n(배송, 예비 20% 충족)", {"size": 11, "color": GRAY})]], align=PP_ALIGN.CENTER)
    notes(s, f"A* 대비 웨이포인트 {(1 - dD['waypoints'] / dC['waypoints']) * 100:.0f}% 감소, SOC {dC['soc_used'] - dD['soc_used']:.1f}%p 추가 절감. "
             f"계획(예보) 예측 SOC {dD['soc_pred']:.1f} vs 실제 {dD['soc_used']:.1f}.")

    # 12. 결과 ③ 탑재중량 ---------------------------------------------------
    s = D.content("결과 ③ 배송 vs 회수 – 탑재중량 변화 반영")
    picture(s, FIG / "F9_payload.png", 0.6, 1.0, 12.1, 4.2)
    E3 = S["E3_payload"]
    g = {(r["payload"], r["kind"], r["method"]): r for r in E3}
    textbox(s, 0.6, 5.3, 12.1, 1.6, [
        [("○ 배송: ", {"color": TITLE}), ("무거울 때 순풍(갈 때), 가벼울 때 역풍(올 때) → 중량 영향 작음 ", {}),
         (f"(0→750 g: {g[(0.0, 'delivery', 'D_WE_LTstar')]['soc_used']:.1f}→{g[(0.75, 'delivery', 'D_WE_LTstar')]['soc_used']:.1f}%p)",
          {"color": GRAY})],
        [("○ 회수: ", {"color": TITLE}), ("무거운 상태로 역풍 귀환 → 중량 영향 큼 ", {}),
         (f"(0→750 g: {g[(0.0, 'pickup', 'D_WE_LTstar')]['soc_used']:.1f}→{g[(0.75, 'pickup', 'D_WE_LTstar')]['soc_used']:.1f}%p, 750 g은 예비 20% 미달)",
          {"color": ORANGE})],
        [("○ 알고리즘은 갈 때/올 때를 ", {"color": TITLE}), ("각각의 질량으로 따로 계획", {"color": ORANGE}),
         (" → 같은 왕복이라도 바람 방향과 적재 순서에 따라 경로·속도·고도가 달라짐", {})]], size=13)

    # 13. 결과 ④ 시간 -------------------------------------------------------
    s = D.content("결과 ④ 시간에 따른 경로 변경 – 출발 시각 최적화")
    picture(s, FIG / "F6_departure_time.png", 0.6, 1.0, 12.1, 4.5)
    E2 = S["E2_departure"]
    dd = [r for r in E2 if r["kind"] == "delivery" and r["method"] == "D_WE_LTstar"]
    best = min(dd, key=lambda r: r["soc_used"]); worst = max(dd, key=lambda r: r["soc_used"])
    nA = sum(1 for r in E2 if r["method"] == "A_shortest_v10" and r["feasible"])
    nD = sum(1 for r in E2 if r["method"] == "D_WE_LTstar" and r["feasible"])
    textbox(s, 0.6, 5.6, 12.1, 1.3, [
        [("○ 시간별 예보(1시간 간격)를 탐색 비용에 직접 반영 → ", {}), ("출발 시각마다 경로·고도·속도가 달라짐", {"color": ORANGE})],
        [("○ 배송 SOC 소모: ", {"color": TITLE}),
         (f"{best['hour']}시 {best['soc_used']:.1f}%p (최소) ~ {worst['hour']}시 {worst['soc_used']:.1f}%p (최대) → 강풍 시간대 회피 권고", {})],
        [("○ 예비 SOC 20% 충족 출발시각(24개 = 12시간×배송/회수): ", {"color": TITLE}),
         (f"기존 {nA}개 → WE-LT* {nD}개", {"color": ORANGE})]], size=13)

    # 14. 결과 ⑤ 호버링 vs 우회 -------------------------------------------
    s = D.content("결과 ⑤ 동적 장애물(장애물 시간표) – 호버링 vs 우회")
    picture(s, FIG / "F7_hover_vs_detour.png", 0.6, 1.0, 12.1, 4.6)
    E4 = S["E4_hover_vs_detour"]
    textbox(s, 0.6, 5.7, 12.1, 1.25, [
        [("○ 띠형 임시 비행제한(폭 150 m)이 경로를 가로막는 상황: ", {"color": TITLE}),
         ("해제까지 짧으면 ", {}), ("제자리 호버링", {"color": BLUE}), (", 길면 ", {}), ("우회", {"color": ORANGE}),
         (" – 회의자료의 '우회 vs 호버링 에너지 비교'를 탐색 안에서 자동 수행", {})],
        [("○ 해제 30 s 후: ", {"color": TITLE}),
         (f"호버링 {E4['short']['wait_s']:.0f} s 선택 ({E4['short']['E_wait_option'] / 1000:.1f} kJ, 우회만 허용 시 "
          f"{E4['short']['E_detour_only'] / 1000:.1f} kJ)  ", {}),
         ("○ 해제 300 s 후: ", {"color": TITLE}), (f"우회 선택 ({E4['long']['E_wait_option'] / 1000:.1f} kJ)", {})]],
        size=13)

    # 15. 결과 ⑥ 재계획 -------------------------------------------------------
    s = D.content("결과 ⑥ 비행 중 실시간 재계획 (롤링 호라이즌)")
    picture(s, FIG / "rolling_replan.gif", 0.6, 1.1, 8.0, 3.45)
    picture(s, FIG / "F8_rolling_replan.png", 0.6, 4.6, 8.0, 2.35)
    E5 = S["E5_rolling"]
    sp = box(s, 8.75, 1.1, 3.95, 5.8, fill=LIGHT, shadow=False)
    box_text(sp, [[("시나리오", {"color": TITLE, "size": 15})],
                  [("배송 후 역풍 귀환 중, 출발 60 s 후 전방에 돌발 금지구역(반경 300 m) 발표", {"size": 12})],
                  [("", {"size": 6})],
                  [("재계획 규칙", {"color": TITLE, "size": 15})],
                  [("· 60 s 주기 + 이벤트 즉시\n· 현재 위치에서 WE-LT* 재실행\n· 기체 추정 바람으로 예보 편향 보정",
                    {"size": 12})],
                  [("", {"size": 6})],
                  [("결과", {"color": TITLE, "size": 15})],
                  [(f"· 초기 계획 고수: 금지구역 침범\n  (SOC {E5['static_soc']:.1f}%p)", {"size": 12})],
                  [(f"· 롤링 재계획: 안전 회피\n  (SOC {E5['rolling_soc']:.1f}%p, {E5['n_replans']}회 재계획)",
                    {"size": 12, "color": ORANGE})],
                  [(f"· 재계획 1회 평균 {E5['replan_cpu_mean']:.1f} s (최대 {E5['replan_cpu_max']:.1f} s)\n  → 60 s 주기 실시간 운용 가능",
                    {"size": 12})],
                  [(f"· 예보 기반 예측 SOC {E5['static_pred_soc']:.1f}%p vs 실제 {E5['static_soc']:.1f}%p",
                    {"size": 12, "color": GRAY})]], anchor=MSO_ANCHOR.TOP)

    # 15-1. 결과 ⑦ 가정 축소 실험 --------------------------------------------
    s = D.content("결과 ⑦ 비교 축별 기여도 – 가정 축소 실험")
    picture(s, FIG / "F11_ablation.png", 0.6, 1.0, 12.1, 4.95)
    E6 = {(r["key"], r["kind"]): r for r in S["E6_ablation"]}
    textbox(s, 0.6, 6.0, 12.1, 0.95, [
        [("○ 기여 순위: ", {"color": TITLE}),
         (f"속도 최적화 +{E6[('fixed_va14', 'delivery')]['d_soc']:.1f}~{E6[('fixed_va14', 'pickup')]['d_soc']:.1f}%p "
          f"(10 m/s 고정은 비행 불가) > any-angle 에너지 규칙 +{E6[('grid', 'delivery')]['d_soc']:.1f}%p > "
          f"3D 고도 선택 +{E6[('terrain_2p5d', 'pickup')]['d_soc']:.1f}~{E6[('terrain_2p5d', 'delivery')]['d_soc']:.1f}%p", {"color": ORANGE})],
        [("○ 시간가변 바람·leg별 질량은 1회 비행의 경로 형상에는 영향 작음(≤0.3%p) → ", {}),
         ("출발시각 선택(E2)·재계획(E5)·SOC 예측 정확도", {"color": TITLE}), ("에서 효과", {})]], size=13)

    # 15-2. 결과 ⑧ 확장안 ------------------------------------------------------
    s = D.content("결과 ⑧ 확장안 – 8방향 탐색 + 고도별 균일·선형 바람")
    picture(s, FIG / "F13_layered_8way.png", 0.5, 1.0, 7.9, 5.95)
    E7 = {(r["key"], r["kind"]): r for r in S["E7_layered"]["rows"]}
    sp = box(s, 8.6, 1.05, 4.15, 5.85, fill=LIGHT, shadow=False)
    box_text(sp, [[("가정", {"color": TITLE, "size": 14})],
                  [("(A1) 같은 고도면 같은 바람  w(z, t)\n(A2) 고도에 선형  w = a(t) + b(t)(z − h̄)\n"
                    "     GFS 적합 R² 0.78~0.88, 시어 ≈ 2 m/s/100 m", {"size": 11})],
                  [("", {"size": 5})],
                  [("결과", {"color": TITLE, "size": 14})],
                  [("① 간선 비용이 위치와 무관 → (시각·고도·방위·경사) 조회표(LUT) 1회 조회", {"size": 11})],
                  [(f"   계산 {E7[('WELT_linear', 'pickup')]['cpu']:.1f} s → {E7[('L26_LT3D', 'pickup')]['cpu']:.2f} s (약 20배)",
                    {"size": 11, "color": ORANGE})],
                  [("② 레이어 안 비용 노름이 비볼록(최대 9%) → 직결은 에너지 비교로 판정 유지", {"size": 11})],
                  [("③ 상승 손익분기 L* = (E↑+E↓)/Δe* : 순풍 +20 m에 105 km → 상승 이득 없음, 역풍은 저고도", {"size": 11})],
                  [("", {"size": 5})],
                  [("SOC (배송, 실제 바람)", {"color": TITLE, "size": 14})],
                  [(f"· 8방향+수직 A*: {E7[('L8_Astar', 'delivery')]['soc_used']:.1f}%p\n"
                    f"· 8방향 + 3D 지름길(LUT): {E7[('L26_LT3D', 'delivery')]['soc_used']:.1f}%p\n"
                    f"· WE-LT* (3D 바람장): {E7[('WELT_full', 'delivery')]['soc_used']:.1f}%p", {"size": 11})],
                  [("→ 사전계획·출발시각 탐색엔 확장안, 최종·재계획엔 3D 바람장", {"size": 11, "color": ORANGE})]],
             anchor=MSO_ANCHOR.TOP)
    notes(s, "상세: docs/extension_8way_linear_wind.md, src/dpo/layered.py, 실험 E7. 수직 이동만 허용하면 제자리 상승(약 180 J/m) "
             "때문에 +3.6~5.1%p 손해, 3D 지름길로 해소. 남은 1.4%p는 지형·도시 바람 효과 미반영 때문.")

    # 16. 결론 ---------------------------------------------------------------
    s = D.content("결론 및 추후 개발할 내용")
    textbox(s, 0.8, 1.1, 7.4, 3.1, [
        [("□ 결론", {"size": 18, "color": INK})],
        [(" ○ 실제 지형·건물·GFS 바람 → 3D 바람 지도, 실제 Map에 경로 적용", {})],
        [(" ○ 실측 보정 에너지·SOC 수식 ①~⑤: R² "
          f"{E0['test']['R2']:.2f}, MAPE {E0['test']['MAPE']:.1f}%", {})],
        [(" ○ WE-LT*: 강풍 배송 SOC ", {}), (f"{dA['soc_used']:.1f} → {dD['soc_used']:.1f}%p", {"color": ORANGE}),
         (", 회수 ", {}), (f"{pA['soc_used']:.1f} → {pD['soc_used']:.1f}%p", {"color": ORANGE})],
        [(" ○ 시간가변: 출발시각별 경로, 호버링/우회 판단, 비행 중 재계획", {})],
        [(" ○ 최신 연구 대비: 속도 최적화·에너지 지름길·고도 선택이 핵심 기여", {})]], size=14,
        space_after=8)
    textbox(s, 0.8, 3.95, 7.4, 2.6, [
        [("□ 향후 계획", {"size": 18, "color": INK})],
        [(" ○ 기상청 LDAPS(1.5 km)·윈드프로파일러 연동, 도심 CFD 바람장", {})],
        [(" ○ WA-LPA*[5]식 증분 재계획 결합으로 재계획 시간 단축", {})],
        [(" ○ 보유 기체 비행 로그로 계수 재보정, 계획 vs 실측 SOC 비교", {})],
        [(" ○ 배터리 전압·온도 모델, 돌풍 불확실성(강건) 계획", {})],
        [(" ○ 다중 배송지(VRP) 결합, UTM 비행승인·관제권 반영", {})]], size=14,
        space_after=8)
    picture(s, FIG / "F10_3d_paths.png", 8.4, 1.15, 4.3, 3.2)
    textbox(s, 8.4, 4.35, 4.3, 0.35, [[("3D 경로 (지형·건물 위)", {"size": 12, "color": GRAY})]], align=PP_ALIGN.CENTER)
    sp = box(s, 8.4, 4.8, 4.3, 1.35, fill=LIGHT, shadow=False)
    box_text(sp, [[("코드·재현", {"color": TITLE, "size": 14})],
                  [("src/dpo: 데이터·에너지·바람장·탐색기\nscripts/run_experiments.py → make_figures.py\ntests/: 단위 테스트 9종\ndocs/: 정식화 비교 · 8방향 확장안",
                    {"size": 11, "color": INK})]], anchor=MSO_ANCHOR.TOP)
    notes(s, "실제 운용 시 판교 일대는 서울공항 관제권으로 비행승인 필요. 비행금지구역 원은 예시(가상).")

    # 17. 참고문헌 -----------------------------------------------------------
    s = D.content("참고문헌")
    refs = [
        "[1] A. Nash, S. Koenig, C. Tovey, \"Lazy Theta*: Any-Angle Path Planning and Path Length Analysis in 3D,\" Proc. AAAI, 2010.",
        "[2] M. Phillips, M. Likhachev, \"SIPP: Safe Interval Path Planning for Dynamic Environments,\" Proc. IEEE ICRA, 2011.",
        "[3] Y. Zeng, J. Xu, R. Zhang, \"Energy Minimization for Wireless Communication With Rotary-Wing UAV,\" IEEE Trans. "
        "Wireless Commun., 2019. DOI: 10.1109/TWC.2019.2902559",
        "[4] F. Betti Sorbelli, F. Corò, S. K. Das, C. M. Pinotti, \"Energy-Constrained Delivery of Goods With Drones Under "
        "Varying Wind Conditions,\" IEEE Trans. Intell. Transp. Syst., 22(9), 6048–6060, 2021. DOI: 10.1109/TITS.2020.3044420",
        "[5] F. Lian, B. Li, Q. Yang, H. Zhu, D. Du, \"WA-LPA*: An Energy-Aware Path-Planning Algorithm for UAVs in Dynamic "
        "Wind Environments,\" Drones, 9(12), 850, 2025. DOI: 10.3390/drones9120850",
        "[6] F. Lian, Q. Yang, B. Li, P. Wang, H. Luo, D. Du, \"Optimal Path Planning of Unmanned Aerial Vehicle with Wind "
        "Field,\" Proc. 28th IEEE ITSC, pp. 4195–4200, 2025.",
        "[7] R. Gu, Y. Zhao, X. Ren, \"Integrating wind field analysis in UAV path planning: Enhancing safety and energy "
        "efficiency for urban logistics,\" Chinese J. Aeronautics, 39(1), 103605, 2026. DOI: 10.1016/j.cja.2025.103605",
        "[8] T. A. Rodrigues et al., \"In-flight positional and energy use data set of a DJI Matrice 100 quadcopter for small "
        "package delivery,\" Scientific Data, 8, 155, 2021. DOI: 10.1038/s41597-021-00930-x",
        "[9] J. G. Leishman, Principles of Helicopter Aerodynamics, 2nd ed., Cambridge University Press, 2006.",
        "[10] S. Koenig, M. Likhachev, D. Furcy, \"Lifelong Planning A*,\" Artificial Intelligence, 155, 2004.",
        "데이터: Copernicus GLO-30 DSM (ESA/AWS Open Data) · Overture Maps buildings 2026-10-21 (ODbL) · "
        "NOAA GFS 0.25° (AWS Open Data)",
    ]
    textbox(s, 0.6, 1.1, 12.1, 5.8, [[(r, {"size": 12, "bold": False, "color": INK})] for r in refs], space_after=7)

    normalize_partnames(D.prs)
    D.prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
