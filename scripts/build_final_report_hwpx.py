"""최종 보고서(HWPX, 한글 문서) 생성기.

    python scripts/build_final_report_hwpx.py [--out docs/drone_soc_final_report.hwpx]

필요: python-hwpx (pip install python-hwpx), Pillow.  저장소 결과 파일(results/*.json, results/figures)을 읽는다.

서식 (요청 이미지 기준)
    3. 기술개발의 목표 및 내용        ← 1수준: 왼쪽 0 mm, 15 pt 굵게
     3-1. 최종목표 및 평가방법         ← 2수준: 3 mm, 13 pt 굵게
      (1) 최종목표                     ← 3수준: 6 mm, 12 pt
        - 내용 ...                     ← 글머리: '-' 는 9 mm, 글자는 12 mm (내어쓰기 3 mm,
          둘째 줄도 글자에 맞춤          '내어쓰기용 자동 탭'으로 첫 줄 글자도 같은 자리에서 시작)
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from hwpx import HwpxDocument
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
FIG = RES / "figures"
MM = 7200 / 25.4  # HWPUNIT per mm
TEXT_W_MM = 170.0  # A4 210 - 좌우 여백 20 mm

# ───────────────────────── 서식 ─────────────────────────
BODY_PT = 11.0
FONT = "함초롬바탕"
FONT_G = "함초롬돋움"


class Writer:
    def __init__(self) -> None:
        self.doc = HwpxDocument.new()
        self.st = self.doc.styles
        self._para: dict[str, str] = {}
        self._run: dict[str, str] = {}
        self.n_tab = {}
        self.n_fig = {}
        self.chapter = 0
        self.first_used = False

    # 문단 모양 (단위: mm, pt)
    def para(self, key, *, left=0.0, hang=0.0, before=0.0, after=0.0, align="JUSTIFY", line=160,
             keep_next=False, autotab=False):
        if key in self._para:
            return self._para[key]
        margins = {
            "intent": int(round(-hang * MM)),
            "left": int(round(left * MM)),
            "right": 0,
            "prev": int(round(before * 100)),
            "next": int(round(after * 100)),
        }
        kw = dict(alignment=align, line_spacing_percent=line, margins=margins)
        if keep_next:
            kw["break_setting"] = {"keepWithNext": True}
        if autotab:
            kw["tab_pr_id_ref"] = "1"  # 내어쓰기용 자동 탭
        pid = self.st.ensure_paragraph_format(**kw)
        self._para[key] = pid
        return pid

    def run(self, key, *, size=BODY_PT, bold=False, font=FONT, color=None):
        if key in self._run:
            return self._run[key]
        cid = self.st.ensure_run(bold=bold, size=size, font=font, color=color)
        self._run[key] = cid
        return cid

    def _p(self, text, pid, cid, page_break=False):
        if not self.first_used:
            p = self.doc.paragraphs[0]
            p.para_pr_id_ref = pid
            p.text = text
            for r in p.runs:
                r.char_pr_id_ref = cid
            self.first_used = True
        else:
            p = self.doc.add_paragraph(text, para_pr_id_ref=pid, char_pr_id_ref=cid)
        if page_break:
            p.element.set("pageBreak", "1")
        return p

    # ── 제목 수준 ──
    def h1(self, text, page_break=True):
        self.chapter = int(text.split(".")[0]) if text[0].isdigit() else self.chapter
        pid = self.para("h1", left=0, before=6, after=8, align="LEFT", keep_next=True)
        cid = self.run("h1", size=15, bold=True)
        return self._p(text, pid, cid, page_break=page_break)

    def h2(self, text):
        pid = self.para("h2", left=3, before=12, after=4, align="LEFT", keep_next=True)
        cid = self.run("h2", size=13, bold=True)
        return self._p(text, pid, cid)

    def h3(self, text):
        pid = self.para("h3", left=6, before=8, after=2, align="LEFT", keep_next=True)
        cid = self.run("h3", size=12)
        return self._p(text, pid, cid)

    # ── 본문 ──
    def b(self, text):
        """'- ' 글머리 (내어쓰기 4 mm, 둘째 줄을 글자에 맞춤)."""
        pid = self.para("b", left=12, hang=3, before=3, after=0, autotab=True)
        return self._p("-\t" + text, pid, self.run("body"))

    def bb(self, text):
        """하위 글머리 '·'."""
        pid = self.para("bb", left=16, hang=3, before=1, after=0, autotab=True)
        return self._p("·\t" + text, pid, self.run("body"))

    def p(self, text):
        pid = self.para("p", left=9, before=3, after=0)
        return self._p(text, pid, self.run("body"))

    def note(self, text):
        pid = self.para("note", left=12, hang=0, before=1, after=0, line=140)
        return self._p(text, pid, self.run("note", size=9, color="#404040"))

    def blank(self, size=6):
        pid = self.para(f"blank{size}", line=100)
        return self._p("", pid, self.run(f"blank{size}", size=size))

    def center(self, text, size, bold=False, before=0, after=0, font=FONT, page_break=False, color=None):
        pid = self.para(f"c{before}_{after}", before=before, after=after, align="CENTER", line=150)
        cid = self.run(f"c{size}{bold}{font}{color}", size=size, bold=bold, font=font, color=color)
        return self._p(text, pid, cid, page_break=page_break)

    # ── 표 ──
    def table(self, caption, header, rows, widths=None, width_mm=160.0, font_pt=9.0, align=None):
        ch = self.chapter
        self.n_tab[ch] = self.n_tab.get(ch, 0) + 1
        if caption:
            cap = f"<표 {ch}-{self.n_tab[ch]}> {caption}"
            pid_cap = self.para("cap_t", before=8, after=2, align="CENTER", line=140, keep_next=True)
            self._p(cap, pid_cap, self.run("cap", size=10, bold=True, font=FONT_G))

        nr, nc = len(rows) + 1, len(header)
        t = self.doc.add_table(nr, nc, width=int(width_mm * MM))
        # 표를 품은 문단: 가운데
        holder = t.element.getparent()
        while holder is not None and not holder.tag.endswith("}p"):
            holder = holder.getparent()
        if holder is not None:
            holder.set("paraPrIDRef", self.para("tblhold", align="CENTER", line=100, after=2))
        try:
            t.set_treat_as_char(True)
        except Exception:
            pass
        t.element.set("repeatHeader", "1")
        if widths:
            t.set_column_widths(widths)
        p_h = self.para("cell_c", align="CENTER", line=130)
        p_l = self.para("cell_l", align="LEFT", line=130)
        c_h = self.run(f"cellh{font_pt}", size=font_pt, bold=True, font=FONT_G)
        c_b = self.run(f"cell{font_pt}", size=font_pt, font=FONT_G)
        c_bb = self.run(f"cellbb{font_pt}", size=font_pt, bold=True, font=FONT_G)
        al = align or (["L"] + ["C"] * (nc - 1))
        for r in range(nr):
            vals = header if r == 0 else rows[r - 1]
            for c in range(nc):
                raw = str(vals[c])
                bold = r == 0 or raw.startswith("**")
                txt = raw.strip("*")
                t.set_cell_text(r, c, txt)
                cell = t.cell(r, c)
                for para in cell.paragraphs:
                    para.para_pr_id_ref = p_h if (r == 0 or al[c] == "C") else p_l
                    for run in para.runs:
                        run.char_pr_id_ref = c_h if r == 0 else (c_bb if bold else c_b)
                if r == 0:
                    t.set_cell_shading(r, c, "#DCE6F2")
                    cell.element.set("header", "1")
        return t

    # ── 그림 ──
    def figure(self, path: Path, caption, width_mm=150.0):
        ch = self.chapter
        self.n_fig[ch] = self.n_fig.get(ch, 0) + 1
        im = Image.open(path).convert("RGB")
        if im.width > 1800:
            im = im.resize((1800, int(im.height * 1800 / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86)
        h_mm = width_mm * im.height / im.width
        pid = self.para("fig", before=6, after=0, align="CENTER", line=100, keep_next=True)
        self.doc.add_picture(buf.getvalue(), "jpg", width_mm=width_mm, height_mm=h_mm, align="CENTER",
                             para_pr_id_ref=pid, char_pr_id_ref=self.run("body"))
        cap = f"[그림 {ch}-{self.n_fig[ch]}] {caption}"
        pid_cap = self.para("cap_f", before=2, after=6, align="CENTER", line=140)
        self._p(cap, pid_cap, self.run("cap", size=10, bold=True, font=FONT_G))


def j(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


def pct(a, b):
    return (b - a) / a * 100.0


# ───────────────────────── 내용 ─────────────────────────
def build(out: Path) -> None:
    w = Writer()
    doc = w.doc
    mm = lambda v: int(round(v * MM))
    doc.set_page_margins(left=mm(20), right=mm(20), top=mm(15), bottom=mm(15), header=mm(10), footer=mm(10), gutter=0)

    # ===== 표지 =====
    w.center("", 12, before=40)
    doc.paragraphs[0].add_page_hiding(header=True, footer=True, page_num=True)  # 표지: 머리말·쪽 번호 감춤
    w.center("최 종 보 고 서", 26, bold=True, before=30, after=30, font=FONT_G)
    w.center("드론 비행 금지 구역 회피 경로 제공 방법 및 장치", 17, bold=True, before=10, after=4)
    w.center("― SoC 기반 무인 운반 드론 에너지 인식 경로 최적화 알고리즘 개발 ―", 13, after=40)
    w.table(
        None,
        ["구분", "내용"],
        [
            ["과제명", "드론 비행 금지 구역 회피 경로 제공 방법 및 장치  [확인요망: 협약서상 공식 과제명]"],
            ["세부 주제", "SoC를 명시 상태 변수로 포함한 무인 운반 드론 에너지 인식 경로 최적화"],
            ["수행 기간", "[자료필요: 협약 기간]"],
            ["수행 기관 · 책임자", "[자료필요]"],
            ["실증 기체", "DJI Matrice 4T"],
            ["시험비행 · 개발 기체", "Holybro X650 (Pixhawk 6X + ArduCopter), Holybro X500"],
            ["작성일", "2026. 11. 20."],
        ],
        widths=[1, 3.2],
        width_mm=140,
        font_pt=10,
    )
    w.n_tab = {}

    # ===== 요약 =====
    w.chapter = 0
    w.center("요  약", 16, bold=True, before=0, after=10, font=FONT_G, page_break=True)
    w.table(
        None,
        ["구분", "내용"],
        [
            ["최종목표", "· SoC를 명시 상태 변수로 포함한 무인 운반 드론 특화 에너지 인식 경로 최적화 알고리즘 개발\n· 가상 시뮬레이션 및 하드웨어 실증을 통한 알고리즘 성능 검증"],
            ["개발 내용", "· 경로 탐색 알고리즘 5종 비교 후 Lazy Theta* 선정, 바람·시간·고도를 더한 3차원 시공간 에너지 탐색(WE-LT*)으로 확장\n· 회전익 전력 모델 + 배터리 내부저항 기반 SoC 환산, DJI M100 실측 보정, X650 제원 모델\n· 수치예보(GFS) 바람장, 실제 지형·건물·비행 금지 구역(판교·창원), 이동 장애물 안전 구간\n· 비행 중 30초 주기 속도 갱신, 예보 시점별 오차 모형에 따른 출발 판정(go / caution / no-go)\n· ArduPilot 미션(.waypoints)·MAVLink 실시간 연동, DJI 경로 파일(WPML .kmz) 내보내기, 웹 3D 계획 화면"],
            ["주요 결과", "· 기존 경로(최단 + 정속 10 m/s) 대비 SoC 소모 17.5–21.2 % 감소 (지역·기체 3개 조합 × 7일, 모두 630건)\n· 틀린 예보로 생긴 손해를 30초 속도 갱신이 42–73 % 회복, 이동 장애물 144건 침범 0건\n· 95 % 신뢰 상한 포함률 409/432 (94.7 %)\n· ArduCopter SITL: 미션 13항목 업로드·읽기 일치, 속도 명령 전부 수락\n· X650 시험비행(2026-11-13): 시간 예측 오차 3 %, 에너지는 제원 중량 기준 16 % 과소 예측(중량 보정 시 2 % 이내)\n· 위 정량 결과는 X650·M100 전력 모델 기준이며, 실증 기체 M4T의 전력 모델은 아직 없음"],
            ["남은 과제", "· 실증 기체 DJI Matrice 4T: 정속 비행 전력 기록으로 전력 모델 구축, 경로 파일(WPML) 기종 추가와 DJI Pilot 2 불러오기 확인, Mobile SDK V5 기반 비행 중 속도 갱신\n· 시험비행 기체 X650: 이륙 중량 실측, 5 m/s 이상 속도별 전력 보정, 기체 바람 추정(EK3_DRAG_BCOEF) 활성화"],
        ],
        widths=[1, 5],
        width_mm=165,
        font_pt=9.5,
        align=["C", "L"],
    )

    # ===== 목차 =====
    w.center("목  차", 16, bold=True, before=0, after=10, font=FONT_G, page_break=True)
    toc = [
        ("1. 연구개발과제의 개요", ["1-1. 연구개발의 필요성", "1-2. 연구개발의 범위"]),
        ("2. 국내외 기술개발 현황", ["2-1. 드론 경로계획 기술의 분류", "2-2. 국내외 연구 동향", "2-3. 기존 기술의 한계와 본 과제의 차별성"]),
        ("3. 기술개발의 목표 및 내용", ["3-1. 최종목표 및 평가방법", "3-2. 기술개발 내용", "3-3. 추진 경과"]),
        ("4. 연구개발 수행 내용 및 결과", ["4-1. 경로 탐색 알고리즘 비교 및 선정", "4-2. 최단 경로와 에너지 최적 경로", "4-3. 에너지·SoC 모델",
                                     "4-4. 실제 지도 기반 3차원 시공간 경로계획", "4-5. 기존 경로 대비 성능", "4-6. 비행 중 갱신과 신뢰도",
                                     "4-7. 대상 기체와 취득 데이터", "4-8. 가상 시뮬레이션(SITL) 검증", "4-9. 하드웨어 시험비행 (Holybro X650)", "4-10. 운용 소프트웨어"]),
        ("5. 목표 달성도 및 관련 분야 기여도", ["5-1. 목표 달성도", "5-2. 관련 분야 기여도"]),
        ("6. 연구개발 결과의 활용계획 및 향후 과제", ["6-1. 활용 계획", "6-2. 향후 과제", "6-3. 한계"]),
        ("7. 참고문헌", []),
        ("부록. 산출물 목록", []),
    ]
    p1 = w.para("toc1", left=0, before=6, after=0, align="LEFT")
    p2 = w.para("toc2", left=8, before=1, after=0, align="LEFT")
    for t1, subs in toc:
        w._p(t1, p1, w.run("toc1", size=11.5, bold=True))
        for s in subs:
            w._p(s, p2, w.run("body"))

    # ===== 1장 =====
    w.h1("1. 연구개발과제의 개요")
    w.h2("1-1. 연구개발의 필요성")
    w.h3("(1) 기술적 필요성")
    w.b("무인 운반 드론의 임무 성패는 배터리 잔량(SoC, State of Charge)으로 결정되나, 일반적인 경로계획은 이동 거리 최소화를 목표로 하여 바람·고도·속도에 따른 에너지 차이를 반영하지 못함")
    w.b("DJI M100 실측 비행 자료에서 이동 거리는 비행 에너지를 설명하지 못하였고(결정계수 R² −1.47), 최단 경로 계획은 강풍일 12회 모두 임무 불가로 판정됨 [7]")
    w.b("10 m/s 바람은 비행 방향에 따라 미터당 에너지를 −38 %에서 +150 %까지 변화시키며, 이는 탑재중량 전 범위의 영향(14 %)과 공기밀도의 영향(6 %)보다 큼")
    w.b("바람은 고도와 시간에 따라 변하고 방향에 따라 이득과 손해가 바뀌므로, 크기와 방향을 가진 시공간 바람장으로 다루어야 함")
    w.b("비행 금지 구역·건물·이동 장애물(유인기 등)을 회피하면서 동시에 SoC 소모를 최소화하는 3차원·시간 의존 경로계획 기술이 필요함")
    w.h3("(2) 경제·산업적 필요성")
    w.b("배송 드론의 에너지 소비는 운용 거리, 배터리 용량, 예비 전력 설계를 좌우하며, 이에 관한 에너지 모델·운용 계획 연구가 활발히 보고됨 [17, 18, 19]")
    w.b("주문이 정해진 뒤 운용자가 바꿀 수 있는 것은 경로·고도·속도뿐이므로, 이를 에너지 기준으로 정하는 계획 도구가 같은 배터리로 더 먼 거리와 더 큰 예비 전력을 확보하는 수단이 됨")
    w.b("시험비행 기체(ArduPilot 계열 Holybro X650)와 실증 기체(DJI Matrice 4T)에 공통으로 적용할 수 있는 경로 파일·실시간 연동 체계가 필요함")
    w.h2("1-2. 연구개발의 범위")
    w.table(
        "연구개발 범위",
        ["구분", "범위"],
        [
            ["알고리즘", "경로 탐색(Lazy Theta* 기반), 에너지·SoC 모델, 바람장 반영 3차원 시공간 탐색, 이동 장애물 안전 구간, 비행 중 속도 갱신, 출발 신뢰도 판정"],
            ["데이터", "실제 지형(DSM)·건물·비행 금지 구역, GFS 수치예보 바람(고도·시간별), 기체 텔레메트리(MAVLink, DJI SDK)"],
            ["검증", "가상 시뮬레이션(630건 비교, 폐루프, 432건 신뢰도), ArduCopter SITL, Holybro X650 시험비행"],
            ["운용 SW", "웹 3D 계획 화면, ArduPilot 미션(.waypoints)·DJI 경로 파일(.kmz) 내보내기, 실시간 연동 서버, 사용자 매뉴얼"],
        ],
        widths=[1, 5],
        align=["C", "L"],
    )

    # ===== 2장 =====
    w.h1("2. 국내외 기술개발 현황")
    w.h2("2-1. 드론 경로계획 기술의 분류")
    w.b("자율비행 기술은 상태 추정, 환경 인지, 경로계획으로 구분되며, 경로계획은 추정한 상태와 환경 정보를 바탕으로 장애물을 회피하며 목표 지점까지의 경로를 생성함 [1]")
    w.b("임무에 맞는 경로를 고르기 위해 비용 함수(또는 이득 함수)를 설계하는 것이 핵심이며, 본 과제는 비용을 이동 거리 대신 SoC 소모로 정의함")
    w.table(
        "드론 경로계획 기술 분류 (2026. 6. 30. 회의자료 정리)",
        ["대분류(계획 범위)", "중분류(공간 표현·탐색 방식)", "대표 기법"],
        [
            ["전역 / 지역 경로계획", "그래프·격자 탐색", "Dijkstra, A*, D*, Hybrid A*, Theta*, Lazy Theta*"],
            ["", "샘플링 기반", "RRT, RRT*, Informed RRT*, PRM"],
            ["", "포텐셜·벡터장 기반", "APF, VFH / 3D-VFH"],
            ["", "궤적 최적화 기반", "CHOMP, MPC"],
            ["", "메타휴리스틱·학습 기반", "GA, ACO, PSO, 강화학습(Q-learning), Neural RRT"],
        ],
        widths=[1.3, 1.6, 3],
        align=["C", "C", "L"],
    )
    w.h2("2-2. 국내외 연구 동향")
    w.h3("(1) 탐사형 경로계획")
    w.b("ETH Zürich(Autonomous Systems Lab)의 Receding Horizon Next-Best-View 계획기는 깊이 센서로 새로 관측할 공간의 양을 가시적 이득으로 정의하여 순간마다 탐사 경로를 갱신함 [인용필요: 원 논문]")
    w.b("HKUST(Aerial Robotics Group)의 Frontier 기반 계획기는 아직 보지 못한 공간과의 경계면(frontier)을 후보 지점으로 삼아 방문 경로를 구성함 [인용필요: 원 논문]")
    w.h3("(2) 에너지·바람 인식 경로계획")
    w.b("바람이 변하는 조건에서 에너지 제약을 둔 배송 경로 연구 [8]와 기상 불확실성에 강건한 임무 계획 연구 [9]가 보고됨")
    w.b("최근 동적 바람장에서의 에너지 인식 경로계획(WA-LPA*) [10], 바람장 반영 최적 경로 [11], 도시 물류용 바람장 분석 결합 경로계획 [12] 연구가 활발함")
    w.h3("(3) 이동 장애물과 불확실성")
    w.b("시간 축 안전 구간(Safe Interval)으로 이동 장애물을 다루는 SIPP [3]와 그 any-angle 확장 [4], 확률 제약(chance-constrained) 기반 충돌 회피 [13, 14]가 대표적임")
    w.h2("2-3. 기존 기술의 한계와 본 과제의 차별성")
    w.table(
        "일반적인 최단 경로 계획과 본 과제의 비교",
        ["구분", "일반적인 최단 경로 계획", "본 과제"],
        [
            ["목적 함수", "이동 거리", "SoC 소모 (명시 상태 변수, 예비 20 % 제약)"],
            ["바람", "미반영 또는 고정값", "고도·시간별 수치예보 바람장, 순풍·역풍·측풍 반영"],
            ["탐색 공간", "2차원 격자", "3차원 + 시간 (26방향, 구간 속도 동시 결정)"],
            ["장애물", "정적 장애물", "정적 + 이동 장애물 안전 구간, 불확실성 여유"],
            ["호버링 판단", "우회 vs 대기 시간 비교", "우회 vs 대기 에너지 비교"],
            ["비행 중", "출발 전 계획 고정", "30초 주기 속도 갱신, 장애물 변경 시 재계획"],
            ["검증", "시뮬레이션", "시뮬레이션 + SITL + 실기체 시험비행"],
        ],
        widths=[1, 2, 3],
        align=["C", "L", "L"],
    )

    # ===== 3장 (요청 이미지의 서식) =====
    w.h1("3. 기술개발의 목표 및 내용")
    w.h2("3-1. 최종목표 및 평가방법")
    w.h3("(1) 최종목표")
    w.b("SoC를 명시 상태 변수로 포함한 무인 운반 드론 특화 에너지 인식 경로 최적화 알고리즘 개발")
    w.b("가상 시뮬레이션 및 하드웨어 실증을 통한 알고리즘 성능 검증")
    w.h3("(2) 세부 목표")
    w.b("바람·지형·건물·비행 금지 구역·이동 장애물을 반영한 3차원 시공간 경로와 구간별 속도 산출")
    w.b("기상 정보 갱신에 따른 비행 중 속도 재계산과 출발 전 신뢰도 판정(go / caution / no-go)")
    w.b("ArduPilot 미션·MAVLink 실시간 연동 및 DJI 경로 파일(WPML) 내보내기")
    w.h3("(3) 평가 항목 및 방법")
    w.table(
        "평가 항목 및 방법",
        ["번호", "평가 항목", "평가 방법", "판단 기준"],
        [
            ["1", "기존 경로 대비 SoC 소모", "지역·기체 3개 조합 × 7일 × 출발 3회 × 출발·도착 쌍 10 = 630건, 날짜 단위 부트스트랩 95 % 구간", "95 % 구간 상한 < 0 %"],
            ["2", "틀린 예보에 대한 대응", "예보 오차 시나리오별 갱신 주기에 따른 손해 회복률", "30초 갱신 시 회복률 제시"],
            ["3", "이동 장애물 안전", "가로지르는 이동 장애물 시나리오의 침범 횟수", "0건"],
            ["4", "출발 신뢰도 보정", "95 % 상한 안에 실제 SoC 소모가 든 비율 (432건)", "93–97 %"],
            ["5", "비행제어기 연동", "ArduCopter SITL 미션 업로드·속도 명령", "항목 수 일치, 명령 수락"],
            ["6", "실기체 예측 정확도", "X650 시험비행 로그 대비 에너지·시간 예측", "오차 ±10 % 이내"],
            ["7", "운용성", "설치부터 경로 생성까지 소요 시간", "1분 이내"],
        ],
        widths=[0.6, 1.7, 3.4, 1.9],
        align=["C", "L", "L", "C"],
    )
    w.note("※ 판단 기준은 본 보고서에서 설정한 기준이며, 협약서상 정량 목표가 있으면 그 값으로 바꾸어 판정해야 함 [확인요망: 협약 정량 목표]")
    w.h3("(4) 평가 환경")
    w.b("지역: 성남 판교(6.2 × 5.5 km), 창원 창이대로 71 주변(4.4 × 4.0 km)의 실제 지형·건물·비행 금지 구역")
    w.b("바람: 미국 해양대기청(NOAA) GFS 수치예보, 계획에는 00Z 발표분, 평가에는 6시간 뒤 06Z 발표분 사용(계획 때는 모르는 값으로 취급)")
    w.b("기체: Holybro X650(제원 기반 전력 모델, 탑재 1.0 kg), DJI M100(CMU 실측 비행으로 보정한 전력 모델, 탑재 0.5 kg)")
    w.b("SITL·시험비행: ArduCopter 4.7.2 SITL, Holybro X650 실기체(ArduCopter 4.7.1, Pixhawk 6X)")
    w.b("실증 기체: DJI Matrice 4T (경로 파일 WPML 연동, 전력 모델은 6-2절 과제)")

    w.h2("3-2. 기술개발 내용")
    w.h3("(1) 경로 탐색 알고리즘 선정")
    w.b("A*, Lazy Theta*, Q-learning, Lazy Theta* + Q-learning, RRT* 5종을 같은 지도에서 계산 시간·이동 거리·경로점 수로 비교하여 Lazy Theta*를 기본 탐색기로 선정 [2]")
    w.h3("(2) 에너지·SoC 모델")
    w.b("회전익 전력 모델(유도 동력 + 형상 동력 + 동체 항력 동력)로 대기속도·상승률·질량에 따른 전력을 계산하고 [5, 6], 배터리 내부저항을 반영하여 SoC로 환산")
    w.b("DJI M100은 CMU 공개 실측 비행 자료로 계수를 보정하고 [7], Holybro X650은 제원과 제조사 호버 시간으로 추정")
    w.h3("(3) 바람장 반영 3차원 시공간 경로계획(WE-LT*)")
    w.b("고도·시간별 바람장에서 대지속도와 대기속도의 관계를 풀어 구간 에너지를 계산하고, 26방향 3차원 탐색과 구간 속도를 함께 결정")
    w.b("가는 편과 오는 편의 바람이 다르므로 왕복 경로를 따로 최적화하고, 우회와 호버 대기 중 에너지가 적은 쪽을 선택")
    w.h3("(4) 비행 금지 구역과 이동 장애물")
    w.b("비행 금지 구역·건물은 3차원 점유 격자로, 이동 장애물은 시간 축 안전 구간(SIPP)으로 표현하고 [3], 실측 ADS-B 예측 오차로 만든 불확실성 여유를 더함 [15, 16]")
    w.b("비행 중 장애물·금지 구역이 바뀌면 남은 구간을 다시 계획")
    w.h3("(5) 비행 중 갱신과 출발 신뢰도")
    w.b("기체가 잰 바람으로 예보를 보정하여 30초마다 남은 구간 속도를 다시 정하고, 예보 기준으로 불리한 경로 변경은 막는 안전장치를 둠")
    w.b("예보 시점(발표 후 경과 시간)별 오차 모형으로 SoC 소모 분포를 구해 출발 판정(go / caution / no-go)을 냄")
    w.h3("(6) 기체 연동과 운용 소프트웨어")
    w.b("ArduPilot 미션 파일(QGC WPL 110), MAVLink 실시간 속도 명령·바람 수신, DJI WPML 경로 파일(.kmz) 내보내기")
    w.b("웹 기반 3차원 계획 화면, 실시간 연동 서버, 설치 패키지와 사용자 매뉴얼")

    w.h2("3-3. 추진 경과")
    w.table(
        "추진 경과",
        ["시점", "주요 내용"],
        [
            ["2026. 6. 30.", "경로계획 기술 분류·동향 정리, 경로 탐색 알고리즘 5종 비교(2D 지도), 기체 에너지 실험 계획(호버·상승·하강·수평 속도별·선회·탑재·SOH) 수립"],
            ["2026. 7. 1.", "Mission Planner MAVLink Inspector로 개발 기체의 실시간 수신 메시지·주기 점검"],
            ["2026. 9. 4.", "Lazy Theta* 선정, 최단 경로와 최적 경로 정의, 개발 순서 5단계(최단 → 실제 지도 → 바람 → 시공간 → 3차원 시공간), 에너지 수식 정리"],
            ["2026. 9.–11.", "실제 지도(판교·창원), 3차원 시공간 에너지 탐색, X650 모델, 기존 경로 대비 비교(630건), 실시간 연동, 신뢰도 판정, SITL 검증, DJI 경로 파일, 사용자 매뉴얼"],
            ["2026. 11. 13.", "Holybro X650 시험비행, 비행 로그 분석과 모델 비교"],
            ["2026. 11. 20.", "최종 보고서 작성"],
        ],
        widths=[1.1, 5],
        align=["C", "L"],
    )

    # ===== 4장 =====
    w.h1("4. 연구개발 수행 내용 및 결과")
    w.h2("4-1. 경로 탐색 알고리즘 비교 및 선정")
    w.h3("(1) 비교 조건")
    w.b("2차원 지도에 건물(사각형)과 나무(원형)를 무작위로 배치하고 장애물 안전 영역을 두어, 같은 시작·목적·복귀 지점으로 5종 알고리즘을 실행")
    w.h3("(2) 비교 결과")
    w.table(
        "경로 탐색 알고리즘 5종 비교 (2026. 6. 30. 회의자료)",
        ["알고리즘", "구분", "계산 시간 (s)", "이동 거리 (m)", "경로점 수"],
        [
            ["A*", "그래프 기반", "0.3017", "533.16", "12"],
            ["**Lazy Theta*", "**Any-angle 기반", "**0.2593", "**526.88", "**9"],
            ["Q-learning", "강화학습 기반", "0.6512", "533.77", "13"],
            ["Lazy Theta* + Q-learning", "하이브리드", "4.7769", "533.77", "13"],
            ["RRT*", "샘플링 기반", "1.3528", "539.64", "9"],
        ],
        widths=[2.2, 1.6, 1.3, 1.3, 1],
    )
    w.b("Lazy Theta*가 계산 시간(0.2593 s)과 이동 거리(526.88 m)가 가장 작고, 경로점 수(9개)는 RRT*와 같이 가장 적음")
    w.b("RRT*는 무작위 표본으로 실행마다 결과가 달라 재현성이 낮고, Q-learning 계열은 학습 횟수·보상 설계에 민감하여 단일 정적 지도에서는 탐색 알고리즘보다 느림")
    w.h3("(3) 선정")
    w.b("가시선 검사를 필요한 때로 미루어 Theta* 수준의 자유각 경로를 적은 계산으로 얻고 결과가 항상 같은 Lazy Theta*를 기본 탐색기로 선정 [2]")
    w.b("경로점이 적어 비행제어기 미션 입력이 단순하며, 이후 비용을 거리에서 에너지로 바꾸고 바람·시간·고도 차원을 더하는 방식으로 확장")

    w.h2("4-2. 최단 경로와 에너지 최적 경로")
    w.table(
        "최단 경로와 최적 경로의 정의 (2026. 9. 4. 회의자료)",
        ["구분", "최단 경로", "최적 경로"],
        [
            ["최소화 목표", "이동 거리", "배터리 소모 (SoC)"],
            ["바람 정보", "미반영", "반영 (순풍·역풍·측풍)"],
            ["왕복 경로", "갈 때·올 때 비슷한 길", "갈 때·올 때 다른 길 (순풍 활용)"],
            ["필요한 정보", "지도 + 장애물 시간표", "지도 + 장애물 시간표 + 바람 지도"],
            ["호버링 판단", "시간 기준 (예: 우회 5초 vs 대기 3초 → 대기)", "에너지 기준 (예: 우회 589 W vs 대기 491 W → 대기)"],
        ],
        widths=[1.2, 2.4, 2.6],
        align=["C", "L", "L"],
    )
    w.table(
        "개발 순서",
        ["단계", "탐색 알고리즘", "결과물"],
        [
            ["1. 최단 경로", "Lazy Theta*", "거리, 경로점"],
            ["2. 최단 경로 (실제 지도)", "Lazy Theta*", "거리, 경로점"],
            ["3. 최적 경로 (바람)", "A* (8방향)", "거리, 경로점, 비행 시간, 에너지"],
            ["4. 최적 경로 (바람 + 이동 장애물)", "시공간 A* (8방향 + 시간)", "+ 경로점별 통과 시각"],
            ["5. 3차원 최적 경로", "3차원 시공간 탐색 (26방향 + 시간)", "+ 고도"],
        ],
        widths=[2.2, 2.2, 2.4],
        align=["L", "C", "L"],
    )
    w.b("5단계는 Lazy Theta*의 가시선 단축을 에너지 비용에 적용한 시공간 탐색(WE-LT*)으로 구현하였으며, 4-5절의 '제안 경로'가 이 결과임")

    w.h2("4-3. 에너지·SoC 모델")
    w.h3("(1) 전력 모델")
    w.b("필요 전력 = 유도 동력(추력 생성) + 형상 동력(블레이드 항력) + 동체 항력 동력 + 상승 동력 + 항전 장비 전력으로 구성 [5, 6]")
    w.b("바람장에서 원하는 대지속도를 내기 위한 대기속도를 구간마다 풀어 전력과 비행 시간을 곱해 구간 에너지를 계산")
    w.b("배터리 전압 강하(내부저항)를 반영하여 소모 에너지를 SoC로 환산하고, 도착 시 예비 SoC 20 % 이상을 제약으로 둠")
    w.h3("(2) 보정과 검증")
    w.b("DJI M100은 CMU 공개 실측 비행 198회로 계수를 보정하고, 보정에 쓰지 않은 날짜 56회로 예측을 확인함 [7]")
    w.figure(FIG / "F3_energy_validation.png", "DJI M100 실측 비행에 대한 에너지 모델 검증", 155)
    w.h3("(3) 시험비행 기체 Holybro X650 모델")
    w.table(
        "Holybro X650 전력 모델의 계수와 출처",
        ["계수", "값", "출처"],
        [
            ["무탑재 질량", "4.1 kg", "제원표"],
            ["회전면 면적", "0.456 m²", "제원 (15 in 프로펠러 4개)"],
            ["배터리 에너지", "222 Wh (6S 10,000 mAh)", "제원"],
            ["상승률 / 하강률", "2.5 / 1.5 m/s", "ArduCopter 기본값"],
            ["종합 효율", "0.726", "무탑재 호버 30분에 맞춘 값"],
            ["형상 동력", "30 W", "가정 (M100 보정값 환산)"],
            ["동체 항력 면적 CdA", "0.08 m²", "가정 (M100 보정값 0.070 m²와 비슷한 크기)"],
            ["대지속도 상한", "15 m/s", "가정 (실증 운용 여유)"],
        ],
        widths=[1.6, 1.8, 3],
        align=["L", "C", "L"],
    )
    w.table(
        "X650 탑재중량별 호버 전력과 효율 속도 (모델)",
        ["탑재", "총 질량", "호버 전력", "호버 시간", "미터당 에너지 최소 속도", "미터당 에너지"],
        [
            ["0 kg", "4.1 kg", "433 W", "30.0 분", "13.5 m/s", "29.4 J/m"],
            ["1 kg", "5.1 kg", "581 W", "22.4 분", "15.0 m/s", "35.2 J/m"],
            ["2.2 kg", "6.3 kg", "779 W", "16.7 분", "15.0 m/s", "43.1 J/m"],
        ],
        widths=[1, 1, 1, 1, 1.6, 1.2],
        align=["C"] * 6,
    )
    w.note("※ 항력 면적 등 가정 계수는 4-9절 시험비행과 6-2절의 속도별 시험으로 보정해야 함")

    w.h2("4-4. 실제 지도 기반 3차원 시공간 경로계획")
    w.h3("(1) 입력 자료")
    w.b("지형·건물: 수치표면모델(DSM)과 건물 높이로 3차원 점유 격자 구성, 비행 금지 구역 반영")
    w.b("바람: GFS 수치예보의 고도별·시간별 바람을 계획 영역에 보간하여 시공간 바람장 구성")
    w.figure(FIG / "F4_paths_realmap.png", "실제 지도(판교)에서의 최단 경로와 에너지 최적 경로", 160)
    w.h3("(2) 3차원 계획 화면에서의 비교 (2026-10-27 예보, 15시 출발 왕복)")
    rows = []
    for site, lab in [("pangyo_x650", "판교 · X650"), ("changwon_x650", "창원 · X650"), ("pangyo_m100", "판교 · M100")]:
        d = {m["key"]: m for m in j(f"platform_3d_compare_{site}.json")["methods"]}
        e, v, p = d["existing"], d["existing_vopt"], d["proposed"]
        fe = "" if e["feasible"] else " (임무 불가)"
        rows.append([lab, f"{e['soc_used']:.1f} %p{fe}", f"{v['soc_used']:.1f} %p" + ("" if v["feasible"] else " (임무 불가)"),
                     f"**{p['soc_used']:.1f} %p", f"{pct(e['soc_used'], p['soc_used']):+.1f} %", f"{e['T']/60:.1f} → {p['T']/60:.1f} 분"])
    w.table(
        "기존 경로와 제안 경로의 왕복 SoC 소모 (3차원 화면 비교 결과)",
        ["지역 · 기체", "기존 (최단 + 정속 10 m/s)", "최단 + 속도 최적화", "제안 경로", "기존 대비", "비행 시간"],
        rows,
        widths=[1.3, 1.8, 1.5, 1.1, 1, 1.3],
    )
    w.b("판교 M100은 기존 경로로는 SoC가 바닥나 임무가 성립하지 않으나, 제안 경로는 예비 20 %를 지키며 완주함")
    w.figure(FIG / "platform_3d_compare_pangyo_x650.png", "3차원 계획 화면의 경로 비교 (판교 · X650)", 160)

    w.h2("4-5. 기존 경로 대비 성능")
    w.h3("(1) 비교 방법")
    w.b("7일(2026-10-01–31 중, 지면 위 100 m 평균 풍속 0.5–11.6 m/s) × 출발 시각 3 × 출발·도착 쌍 10 = 지역·기체 조합마다 210건, 모두 630건 (편도 1.5–2.8 km, 탑재 1.0 kg)")
    w.b("기존 경로는 최단 경로 + 정속 10 m/s(ArduCopter 기본 수평 속도), 제안 경로는 에너지 탐색(WE-LT*) + 구간 속도 최적화")
    w.h3("(2) 결과")
    w.table(
        "기존 경로 대비 SoC 소모 (630건)",
        ["조합", "기존 SoC 소모", "제안 SoC 소모", "변화 (95 % 구간)", "제안이 우수한 경우", "비행 시간"],
        [
            ["창원 · X650", "16.6 %p", "13.7 %p", "**−17.5 % (−18.5, −16.3)", "210/210", "−22.7 %"],
            ["판교 · X650", "18.6 %p", "15.0 %p", "**−18.9 % (−20.0, −17.6)", "206/210", "−21.8 %"],
            ["판교 · M100", "35.1 %p", "27.5 %p", "**−21.2 % (−23.0, −19.2)", "210/210", "−28.8 %"],
        ],
        widths=[1.2, 1.1, 1.1, 2, 1.3, 1],
    )
    w.table(
        "절감의 출처 분해",
        ["조합", "속도만 바꾼 효과", "경로 모양의 효과 (95 % 구간)", "안전장치 적용 시"],
        [
            ["창원 · X650", "−15.9 %", "−1.8 % (−2.3, −1.5)", "−2.0 %, 최악 +0.1 %"],
            ["판교 · X650", "−14.7 %", "−4.9 % (−5.5, −4.5)", "−5.0 %, 최악 +0.2 %"],
            ["판교 · M100", "−18.2 %", "−3.6 % (−4.2, −3.2)", "−3.7 %, 최악 0.0 %"],
        ],
        widths=[1.2, 1.4, 2, 2],
    )
    w.b("절감의 대부분은 구간 속도 선택(14.7–18.2 %)에서 나오며, 경로 모양의 몫은 1.8–4.9 %임")
    w.b("따라서 '기존 경로'를 최단 경로 + 속도 최적화로 정의하면 절감은 2–5 %로 줄어듦. 비교 기준의 정의를 발주처와 확정해야 함 [확인요망]")
    w.b("속도 선택 효과는 기체 항력 면적 가정에 크게 좌우됨 (CdA 0.08 m²에서 10 m/s 대비 −22.2 %, 0.16 m²이면 −6.6 %)")
    w.figure(FIG / "F28_superiority.png", "기존 경로 대비 SoC 소모 비교 (630건)", 150)

    w.h2("4-6. 비행 중 갱신과 신뢰도")
    w.h3("(1) 기상 정보 갱신 주기 (E14)")
    w.b("예보가 맞을 때는 비행 중 갱신의 이득이 0.0–0.3 %로 작으나, 예보가 틀릴 때 손해를 2분 주기 갱신은 12–35 %, 30초 주기 갱신은 42–73 % 되찾음")
    w.b("이득은 경로 재계획이 아니라 속도 갱신에서 나오며, 경로 재계획은 장애물·금지 구역이 바뀔 때 수행")
    w.h3("(2) 실시간 엔진 폐루프 (E15)")
    w.table(
        "연결 전 플랫폼 대비 SoC 소모 변화 (안전장치 + 속도 갱신 30초, 경우마다 24건)",
        ["바람 경우 (추정 편차)", "창원 · X650", "판교 · M100", "판교 · X650"],
        [
            ["실제 예보 오차 (0.3–0.4 m/s)", "−3.8 %", "−1.4 %", "−1.4 %"],
            ["풍향 30° 회전 (3.2 m/s)", "−4.7 %", "−2.1 %", "−1.2 %"],
            ["풍향 60° 회전 (6.2 m/s)", "−6.3 %", "−3.5 %", "−1.9 %"],
            ["풍향 90° 회전 (8.8 m/s)", "−7.9 %", "−4.3 %", "−4.5 %"],
            ["풍속 0.6배 (2.5 m/s)", "−6.5 %", "−2.9 %", "−4.6 %"],
        ],
        widths=[2.4, 1.2, 1.2, 1.2],
    )
    w.b("속도 갱신은 360건 모두에서 안전장치만 쓴 경우보다 나쁘지 않았고(최악 +0.00 %), 판단 1회에 2–3 ms 소요")
    w.b("가로지르는 이동 장애물을 넣은 144건은 모두 완주하였고 침범은 0건(최소 이격 32–38 m)")
    w.h3("(3) 예보 시점별 오차와 출발 신뢰도 (E16)")
    w.table(
        "예보 시점별 바람 오차 σ (성분별, m/s; 2025-11–2026-10, 36일)",
        ["예보 시점 (발표 후)", "전체", "겨울", "봄", "여름", "가을"],
        [
            ["6시간", "0.31", "0.26", "0.22", "0.43", "0.27"],
            ["9시간", "0.40", "0.36", "0.36", "0.50", "0.36"],
            ["12시간", "0.40", "0.24", "0.29", "0.44", "0.55"],
            ["30시간", "1.00", "1.06", "0.97", "1.16", "0.78"],
            ["54시간", "1.33", "1.71", "1.21", "1.38", "0.90"],
            ["78시간", "1.74", "1.88", "1.17", "2.42", "1.19"],
        ],
        widths=[1.6, 1, 1, 1, 1, 1],
    )
    w.b("95 % 상한 안에 실제 SoC 소모가 든 비율은 432건 중 409건(94.7 %)으로 목표 신뢰수준과 거의 같음")
    w.b("95 % 신뢰수준에 필요한 SoC 여유는 6시간 전 예보에서 명목값의 3.7 %, 30시간 전 14 %, 78시간 전 27 %")
    w.b("계절별로 알고리즘을 바꿀 근거는 없었으며, 설정은 하나로 두고 여유만 예보 시점과 계절에 맞춤")
    w.figure(FIG / "F34_forecast_age.png", "예보 시점에 따른 바람 오차와 필요 SoC 여유", 160)

    w.h2("4-7. 대상 기체와 취득 데이터")
    w.h3("(1) 실증 기체 DJI Matrice 4T 사양")
    w.table(
        "실증 기체 DJI Matrice 4T 주요 사양",
        ["항목", "사양"],
        [
            ["이륙 무게 / 최대 이륙 무게", "1219 g (표준 프로펠러) / 1420 g"],
            ["최대 페이로드", "200 g"],
            ["크기 (펼침)", "307.0 × 387.5 × 149.5 mm, 대각선 축간거리 438.8 mm, 프로펠러 10.8 in"],
            ["최대 상승 / 하강 속도", "10 m/s / 8 m/s (액세서리 사용 시 6 / 6 m/s)"],
            ["최대 수평 속도", "21 m/s (전진), 18 m/s (후진), 19 m/s (측면)"],
            ["최대 비행 / 호버 시간", "49 분 / 42 분 (무풍, 표준 프로펠러)"],
            ["최대 비행 거리", "35 km (무풍)"],
            ["내풍 최대 풍속 · 최대 피치각", "12 m/s · 35°"],
            ["작동 온도 · 최고 고도", "−10–40 °C · 6000 m"],
            ["GNSS · RTK 정확도", "GPS + Galileo + BeiDou + GLONASS, RTK 1 cm + 1 ppm (수평), 1.5 cm + 1 ppm (수직)"],
            ["호버링 정확도", "±0.1 m (비전·RTK), ±0.5 m (GNSS)"],
            ["확장 포트", "E-Port × 1, E-Port Lite × 1 (타사 PSDK 기기 지원)"],
            ["레이저 거리계", "1800 m (1 Hz, 반사율 20 %)"],
            ["카메라", "광각 1/1.3″ 48 MP, 미디엄 망원 1/1.3″ 48 MP (70 mm), 망원 1/1.5″ 48 MP (168 mm)"],
            ["열화상 카메라", "비냉각 VOx, 640 × 512 (초해상도 1280 × 1024), 30 Hz [21]"],
        ],
        widths=[2, 5],
        font_pt=8.5,
        align=["L", "L"],
    )
    w.note("※ 기체·비행 성능은 첨부 사양서의 값(Matrice 4 시리즈 공통)이며, 첨부 사양서 본문은 'Matrice 4E 사양'으로 표기되어 있어 카메라는 4T의 DJI 공식 사양 [21]으로 적음")
    w.b("최대 수평 속도 21 m/s, 내풍 12 m/s, 최대 페이로드 200 g을 계획기의 대지속도 상한·운용 풍속·탑재 조건에 반영해야 함")
    w.b("이륙 무게 약 1.2 kg으로 시험비행 기체 X650(4.1 kg)이나 M100과 전력 특성이 크게 다르므로, 4-3절~4-6절의 절감률을 M4T에 그대로 적용할 수 없음. M4T 정속 비행 전력 기록으로 전력 모델을 따로 구축해야 함 [자료필요: M4T 호버·속도별 전력 기록]")
    w.h3("(2) 실증 기체(M4T)에서 얻을 수 있는 데이터")
    w.b("DJI 공식 SDK 3종(Mobile SDK V5, Onboard SDK, Payload SDK)의 비행 관련 항목 200개를 모두 검토하여 경로계획에서의 역할로 분류함")
    w.b("Mobile SDK V5는 v5.12.0(2025-01-08)부터 Matrice 4E/4T를 지원하며 [23], M4T는 E-Port를 통한 Payload SDK 기기도 지원함")
    w.table(
        "DJI SDK 항목과 경로계획에서의 역할",
        ["구분", "DJI 항목 (예)", "역할"],
        [
            ["풍속 · 풍향", "MSDK KeyWindSpeed (dm/s), KeyWindDirection", "비용 변인 (바람장 보정)"],
            ["강풍 경고", "MSDK KeyWindWarning, PSDK FLIGHT_ANOMALY", "안전 감시 (등급만 제공)"],
            ["주변 유인기", "MSDK KeyAirSenseSystemInformation (ADS-B)", "제약 (이동 장애물)"],
            ["배터리", "KeyChargeRemainingInPercent, KeyVoltage (mV), KeyCurrent (mA), KeyCellVoltages", "목적·제약 (SoC)"],
            ["배터리 노화 · 온도", "KeyFullChargeCapacity, KeyNumberOfDischarges, KeyBatteryTemperature", "고정 입력 (용량 보정)"],
            ["위치 · 속도 · 자세", "KeyAircraftLocation3D, KeyAircraftVelocity, KeyAircraftAttitude", "결정 변수의 결과 (추종 확인)"],
            ["비행 상태 · GNSS", "KeyFlightMode, KeyGPSSatelliteCount", "안전 감시"],
            ["탑재중량", "해당 키 없음", "주문 정보로 입력"],
        ],
        widths=[1.4, 3.6, 1.8],
        font_pt=8.5,
        align=["C", "L", "L"],
    )
    w.b("M4T는 MAVLink를 쓰지 않으므로, 비행 중 속도 갱신을 하려면 Mobile SDK V5 기반 조종기 앱이 필요함 (현재 실시간 연동은 MAVLink만 구현)")
    w.b("DJI가 주는 외생 조건은 바람(비용)과 주변 비행체·장애물(제약) 두 가지이며, 본 계획기는 정확히 이 둘을 다룸")
    w.b("풍속·풍향 키는 현재 위치의 기체 추정값만 주며, M4T에서 실제로 값을 주는지와 추정 정확도는 실기체에서 확인해야 함 [확인요망]")
    w.h3("(3) 실증 기체용 경로 파일(WPML) 내보내기")
    w.b("계획 경로를 DJI Pilot 2·FlightHub 2·MSDK V5가 읽는 경로 파일(.kmz: template.kml, waylines.wpml)로 내보냄. 고도는 이륙점 기준 상대 고도, 구간 대지속도는 경로점 속도로 기록")
    w.b("DJI WPML 문서는 Matrice 4E/4T를 경로 파일 지원 기종으로 명시함 [24, 25]. 본 도구의 현재 지원 기종은 M30·M300·M350·Mavic 3 계열로, M4T의 기종·카메라 번호를 넣고 DJI Pilot 2에서 불러오기를 확인해야 함 [자료필요: M4T 기종 번호, DJI Pilot 2 불러오기 결과]")
    w.h3("(4) 시험비행 기체 Holybro X650")
    w.table(
        "Holybro X650 구성",
        ["구분", "내용"],
        [
            ["기체", "쿼드콥터, 휠베이스 650 mm, 자체 무게 4.1 kg, 최대 이륙 중량 6.3 kg"],
            ["비행제어", "Holybro Pixhawk 6X + ArduCopter (MAVLink2, AUTO·RTL·Geofence)"],
            ["추진", "T-Motor MN4014 KV330 × 4, Tekko32 F4 45A ESC, 15 × 5.5 in 카본 프로펠러"],
            ["전원", "LiPo 6S 10,000 mAh (22.2 V, 222 Wh), PM02D 전압·전류 측정"],
            ["측위 · 통신", "H-RTK F9P (RTK 8 Hz), Skydroid H12 Pro (2.4 GHz), C11 3축 짐벌"],
            ["호버 시간", "무탑재 약 30분 (제조사)"],
        ],
        widths=[1, 5],
        align=["C", "L"],
    )
    w.h3("(5) X650 비행 중 실시간 수신 데이터 (MAVLink)")
    w.b("Mission Planner의 MAVLink Inspector(Ctrl+F)로 개발 기체의 수신 메시지와 주기를 지상에서 점검함 (2026. 6. 30.–7. 1.)")
    w.table(
        "MAVLink 수신 메시지와 경로계획에서의 활용",
        ["메시지", "주기", "주요 값 (단위)", "활용"],
        [
            ["HEARTBEAT", "1 Hz", "비행 모드, 시동 상태, 기체 종류", "상태 확인"],
            ["GLOBAL_POSITION_INT", "2 Hz", "위도·경도 (degE7), 상대 고도 (mm), 속도 (cm/s), 방위 (cdeg)", "위치 추종, 남은 구간 계산"],
            ["GPS_RAW_INT", "2 Hz", "고정 유형, 위성 수, HDOP·VDOP", "측위 품질 감시"],
            ["ATTITUDE", "4 Hz", "롤·피치·요 (rad), 각속도 (rad/s)", "관측"],
            ["VFR_HUD", "4 Hz", "대기속도·대지속도 (m/s), 방위, 스로틀 (%), 상승률", "속도 추종 확인"],
            ["BATTERY_STATUS", "2 Hz", "셀 전압 (mV), 전류 (cA), 사용량 (mAh), 에너지 (hJ), 잔량 (%), 온도 (cdegC)", "SoC 추적"],
            ["SYS_STATUS", "–", "배터리 전압·전류·잔량, 센서 상태", "상태 확인"],
            ["EKF_STATUS_REPORT", "2 Hz", "속도·위치·고도 추정 분산", "추정 신뢰도"],
            ["MISSION_CURRENT", "–", "현재 경로점 번호", "진행 상황"],
            ["NAV_CONTROLLER_OUTPUT", "–", "목표 방위, 경로점 거리, 경로 이탈 거리", "추종 오차"],
            ["SCALED_PRESSURE", "–", "절대 기압 (hPa), 온도 (cdegC)", "공기밀도"],
            ["VIBRATION", "2 Hz", "진동 (m/s²), 가속도계 포화 횟수", "기체 상태"],
            ["SERVO_OUTPUT_RAW", "–", "모터 출력 PWM (µs)", "관측"],
            ["RADIO_STATUS", "–", "수신 세기, 잡음, 오류 수", "링크 품질"],
            ["WIND", "약 2 Hz", "풍향 (°), 풍속 (m/s), 수직 풍속", "바람장 보정"],
        ],
        widths=[2.3, 0.7, 3.1, 1.3],
        font_pt=8.5,
        align=["L", "C", "L", "L"],
    )
    w.note("※ 주기 '–'는 점검 화면에서 확인하지 못한 항목. WIND는 점검 화면 목록에는 없었고, 시험비행 정리 문서(발주처)에서 약 2 Hz 수신이 확인됨. 멀티콥터는 EK3_DRAG_BCOEF_X/Y를 설정해야 바람 추정이 켜짐(기본 0 = 꺼짐)")
    w.h3("(6) X650 착륙 후 비행 로그 (DataFlash .bin)")
    w.b("배터리(전압·전류·누적 사용량) 10 Hz, GPS 5 Hz, 제어·EKF 기록을 담으며, 비행 후 전력 모델 보정과 경로 추종 분석에 사용")
    w.h3("(7) 개발용 기체 Holybro X500")
    w.b("PX4 Development Kit X500: 휠베이스 500 mm, 프레임 610 g, 2216 KV920 모터, 4S 배터리, PM02 V3 전원 모듈, M10 GPS, SiK 텔레메트리, 무탑재 호버 약 18분(5000 mAh), 최대 탑재 약 1 kg")
    w.b("MAVLink 수신 점검(4-7절 (5))과 지상 시험에 사용")

    w.h2("4-8. 가상 시뮬레이션(SITL) 검증")
    s = j("sitl_check.json")
    c, m = s["checks"], s["mission"]
    w.table(
        "ArduCopter SITL 연동 시험 (창원 · X650 미션)",
        ["점검 항목", "결과"],
        [
            ["펌웨어", s["firmware"]],
            ["미션 업로드 / 읽기", f"{c['mission_count_sent']} / {c['mission_count_readback']} 항목 (일치)"],
            ["AUTO 모드 시동 · 완주", "성공" if c["armed_in_auto"] and c["flight_completed"] else "실패"],
            ["구간 속도 명령 (DO_CHANGE_SPEED)", "모두 수락" if c["speed_cmds_all_accepted"] else "일부 거부"],
            ["바람 메시지 수신", "수신" if c["wind_received"] else "미수신"],
            ["비행 로그 읽기", "가능" if c["dataflash_readable"] else "불가"],
            ["미션 규모", f"거리 {m['distance_m']/1000:.2f} km, 예상 {m['duration_s']:.0f} s, 경로점 {m['n_waypoints']}개"],
        ],
        widths=[2.2, 4],
        align=["L", "L"],
    )

    w.h2("4-9. 하드웨어 시험비행 (Holybro X650)")
    w.h3("(1) 시험 개요 (2026. 11. 13.)")
    f = j("flight_20261113.json")
    w.b("Holybro X650(ArduCopter 4.7.1)으로 이륙 15 m → 경로점 4개(30, 30, 30, 20 m) → 자동 복귀(RTL) 미션을 3 m/s로 비행하고 기체 로그를 분석")
    w.table(
        "시험비행 결과 (기체 로그에서 계산)",
        ["항목", "값"],
        [
            ["비행 시간 (이륙–착륙)", "103 s"],
            ["사용 에너지", "14.6 Wh (630 mAh), 배터리 222 Wh의 6.6 %"],
            ["평균 전류 · 평균 전력", "22.1 A · 512 W"],
            ["전압", "24.2 V → 23.3 V"],
            ["최고 고도 · 최고 수평 속도", "30.2 m · 3.5 m/s"],
            ["배터리 내부저항 (로그 추정)", "0.028 Ω (계획기 가정 0.03 Ω)"],
        ],
        widths=[2.2, 4],
        align=["L", "L"],
    )
    w.h3("(2) 모델과의 비교")
    w.table(
        "구간별 측정 전력과 모델",
        ["구간", "시간", "측정", "모델 (4.1 kg)", "모델 (4.66 kg)"],
        [
            ["이륙 (0 → 15 m)", "8.8 s", "515 W", "530 W", "625 W"],
            ["상승하며 이동 (15 → 30 m)", "15.9 s", "523 W", "474 W", "561 W"],
            ["수평 이동 (30 m, 3 m/s)", "20.5 s", "496–518 W", "416–418 W", "496–498 W"],
            ["하강하며 이동 (30 → 20 m)", "9.1 s", "500 W", "406 W", "484 W"],
            ["RTL 복귀 · 대기 · 착륙", "48.3 s", "509–515 W", "424–442 W", "504–524 W"],
            ["**합계 에너지", "**103 s", "**14.6 Wh", "**12.5 Wh (−16 %)", "**14.9 Wh (+2 %)"],
        ],
        widths=[2.2, 0.9, 1.1, 1.4, 1.4],
    )
    w.b("제원 중량(4.1 kg)의 모델은 에너지를 16 % 적게 예측함. 측정 호버 전력 515 W는 모델 434 W보다 높음")
    w.b("모델 중량을 4.66 kg으로 두면 측정 호버 전력과 같아지고 전체 에너지가 2 % 안으로 맞음. 카메라·짐벌·RTK를 단 실제 이륙 중량이 제원보다 무거운 것으로 보이나, 중량을 재지 않아 확정할 수 없음 [자료필요: 이륙 중량 실측]")
    w.b("상승 구간은 모델이 17–20 % 크게 예측함")
    w.b("시간 예측은 경로점 구간 실제 45.4 s, 계획기 46.9 s로 오차 3 %")
    w.b("이번 비행은 3.5 m/s 이하로만 날아, 계획기가 속도를 고르는 데 쓰는 고속 전력(동체 항력)은 검증하지 못함")
    w.figure(FIG / "F37_flight_20261113.png", "2026-11-13 시험비행: 구간별 전력과 모델 비교", 160)
    w.h3("(3) 실증 지역(고성) 왕복 400 m 재추정")
    w.table(
        "고성 왕복 400 m의 예상 비행 시간과 에너지 (보정 모델)",
        ["수평 속도", "고도 30 m: 시간", "고도 30 m: 에너지", "고도 20 m: 시간", "고도 20 m: 에너지"],
        [
            ["5 m/s", "2분 30초", "19.9 Wh (9.0 %)", "2분 17초", "18.2 Wh (8.2 %)"],
            ["**7 m/s", "**2분 11초", "16.9 Wh (7.6 %)", "**1분 58초", "15.1 Wh (6.8 %)"],
            ["8 m/s", "2분 6초", "16.1 Wh (7.2 %)", "1분 53초", "14.3 Wh (6.4 %)"],
            ["10 m/s", "2분 0초", "15.2 Wh (6.8 %)", "1분 48초", "13.4 Wh (6.0 %)"],
        ],
        widths=[1, 1.3, 1.5, 1.3, 1.5],
    )
    w.b("2분 안에 왕복하려면 고도 20 m에서 7–8 m/s가 필요하며, 고도 30 m에서는 7 m/s로 약 2분 11초가 예상됨")

    w.h2("4-10. 운용 소프트웨어")
    w.b("웹 기반 3차원 계획 화면에서 지도의 출발·도착 지점을 찍고 기체·탑재·출발 시각을 넣으면 기존 경로와 제안 경로를 함께 계산하여 비교 표와 함께 보여줌")
    w.b("결과는 ArduPilot 미션(.waypoints), 경로표(.csv), DJI 경로 파일(.kmz)로 내려받으며, 실시간 화면은 MAVLink로 기체와 연결하여 속도 갱신을 보냄")
    w.table(
        "설치부터 경로 생성까지 소요 시간 (사용자 매뉴얼 시험)",
        ["단계", "소요 시간"],
        [["패키지 설치", "약 17 s"], ["설치 점검", "8 s"], ["서버 시작", "3.8 s"], ["화면 열기", "4.0 s"], ["경로 계획 (왕복 비교)", "13.1 s"]],
        widths=[2, 1.5],
        width_mm=110,
        align=["L", "C"],
    )
    w.figure(FIG / "platform_3d.png", "3차원 계획 화면", 150)

    # ===== 5장 =====
    w.h1("5. 목표 달성도 및 관련 분야 기여도")
    w.h2("5-1. 목표 달성도")
    w.table(
        "평가 항목별 달성도",
        ["번호", "평가 항목", "판단 기준", "실적", "판정"],
        [
            ["1", "기존 경로 대비 SoC 소모", "95 % 구간 상한 < 0 %", "−17.5 – −21.2 % (상한 −16.3 – −19.2 %)", "달성"],
            ["2", "틀린 예보 대응", "회복률 제시", "30초 갱신 42–73 % 회복", "달성 (시뮬레이션)"],
            ["3", "이동 장애물 안전", "침범 0건", "144건 중 0건", "달성 (시뮬레이션)"],
            ["4", "출발 신뢰도 보정", "93–97 %", "409/432 (94.7 %)", "달성"],
            ["5", "비행제어기 연동", "항목 일치·명령 수락", "13/13, 속도 명령 전부 수락 (SITL)", "달성"],
            ["6", "실기체 에너지 예측", "±10 %", "−16 % (제원 중량), +2 % (호버 보정 중량)", "부분 달성"],
            ["6", "실기체 시간 예측", "±10 %", "+3 %", "달성"],
            ["7", "운용성", "1분 이내", "설치 후 계획까지 약 46 s", "달성"],
            ["–", "실비행 중 속도 갱신", "실기체 폐루프", "미실시", "미검증"],
            ["–", "실증 기체(M4T) 경로 파일", "DJI Pilot 2 불러오기", "M4T 기종 번호 미반영, 불러오기 미확인", "미검증"],
            ["–", "실증 기체(M4T) SoC 예측", "±10 %", "M4T 전력 모델 없음 (기록 필요)", "미검증"],
        ],
        widths=[0.6, 1.7, 1.5, 3, 1.2],
        font_pt=8.5,
        align=["C", "L", "C", "L", "C"],
    )
    w.note("※ 1–5는 시뮬레이션·SITL 결과이며, 6의 실기체 에너지 예측은 이륙 중량 실측과 고속 구간 시험 후 다시 판정해야 함")
    w.h2("5-2. 관련 분야 기여도")
    w.h3("(1) 학술적 기여")
    w.b("SoC를 목적·제약 상태 변수로 두고 시공간 바람장·이동 장애물·구간 속도를 한 번에 다루는 3차원 탐색(WE-LT*)을 제시")
    w.b("절감을 속도 선택과 경로 모양의 몫으로 분해하여, 경로 모양만의 이득(1.8–4.9 %)과 속도 선택의 이득을 구분해 보고")
    w.b("예보 시점별 실측 오차로 SoC 신뢰 구간을 보정하여 출발 판정의 포함률을 실제로 검증(94.7 %)")
    w.h3("(2) 기술적 기여")
    w.b("ArduPilot 미션·MAVLink와 DJI WPML 등 공개 형식으로 계획 결과를 내보내 특정 기체에 묶이지 않는 운용 체계를 마련")
    w.b("시험비행 로그를 계획기 모델과 바로 비교하는 분석 도구로 실기체 보정 절차를 정립")
    w.h3("(3) 산업적 기여")
    w.b("같은 배터리로 더 큰 예비 전력을 확보하여 배송·점검 임무의 성립 범위를 넓힘 (판교 M100 사례: 기존 경로 임무 불가 → 제안 경로 완주)")

    # ===== 6장 =====
    w.h1("6. 연구개발 결과의 활용계획 및 향후 과제")
    w.h2("6-1. 활용 계획")
    w.b("실증 기체 DJI Matrice 4T에 경로 파일(WPML)로 계획 경로와 구간 속도를 적용하여 실증")
    w.b("실증 지역(고성) 왕복 시험에 계획기와 미션 내보내기를 적용하여 운용 절차를 검증")
    w.b("배송·점검 운용 계획 도구로 활용: 출발 전 경로·속도·출발 판정, 비행 중 속도 갱신")
    w.b("학술지 투고: 영문 원고 초안 준비(docs/paper) [확인요망: 투고 대상 학술지]")
    w.b("지식재산: '드론 비행 금지 구역 회피 경로 제공 방법 및 장치' [확인요망: 특허 출원 여부와 청구 범위]")
    w.h2("6-2. 향후 과제")
    w.table(
        "다음 시험에서 수행할 항목",
        ["순위", "할 일", "이유"],
        [
            ["1", "M4T 정속 비행 전력 기록 (30초 호버, 같은 왕복을 5·7·10 m/s로 각 1회, 비행 기록 내려받기)", "실증 기체의 전력 모델 구축 (현재 없음)"],
            ["2", "M4T 기종·카메라 번호를 경로 파일에 넣고 DJI Pilot 2에서 불러오기 시험", "실증 기체에 계획 경로 적용"],
            ["3", "M4T 풍속·풍향 키(KeyWindSpeed 등) 값 기록 + 지상 풍속계 비교", "기체 바람 추정의 사용 가능 여부와 정확도 확인"],
            ["4", "Mobile SDK V5 기반 속도 갱신 앱 개발", "M4T에서 비행 중 속도 갱신 (현재 MAVLink만 구현)"],
            ["5", "X650 이륙 중량 측정과 속도별 왕복 3회 (5, 7, 10 m/s)", "4.66 kg 추정 확인, 고속 전력 곡선(항력 면적) 보정"],
            ["6", "X650 BATT_CAPACITY = 10000, EK3_DRAG_BCOEF_X/Y 설정", "잔량 % 보정, 기체 바람 추정 활성화"],
            ["7", "X650 지상국 기록(.tlog) 확보와 속도 명령(DO_CHANGE_SPEED) 송신 시험", "실제 수신 주기 확인, 폐루프 속도 갱신의 실기체 검증"],
        ],
        widths=[0.6, 3.2, 3.2],
        font_pt=8.5,
        align=["C", "L", "L"],
    )
    w.h2("6-3. 한계")
    w.b("X650 전력 모델의 항력 면적·형상 동력은 가정값이며, 속도 선택에 따른 절감 크기가 이 가정에 크게 좌우됨")
    w.b("기존 경로 대비 비교와 폐루프 시험의 평가 바람은 실측이 아닌 더 새 예보이며, 비행 고도의 실측 바람과 예보의 차이는 아직 재지 못함 [자료필요: 비행 고도 실측 바람]")
    w.b("본 보고서의 정량 결과(4-4절~4-6절)는 X650·M100 전력 모델 기준이며, 실증 기체 M4T의 전력 모델이 없어 M4T 경로 파일의 SoC 예상은 아직 맞지 않음")

    # ===== 7장 =====
    w.h1("7. 참고문헌")
    refs = [
        "전자통신동향분석, 제39권 제4호, pp. 10–20, 2024. doi:10.22648/ETRI.2024.J.390402 [확인요망: 저자·제목]",
        "A. Nash, S. Koenig, C. Tovey, \"Lazy Theta*: Any-Angle Path Planning and Path Length Analysis in 3D,\" Proc. AAAI, 2010. doi:10.1609/aaai.v24i1.7566",
        "M. Phillips, M. Likhachev, \"SIPP: Safe Interval Path Planning for Dynamic Environments,\" Proc. IEEE ICRA, 2011.",
        "K. Yakovlev, A. Andreychuk, \"Any-Angle Pathfinding for Multiple Agents Based on SIPP Algorithm,\" Proc. ICAPS, 2017. doi:10.1609/icaps.v27i1.13856",
        "J. G. Leishman, Principles of Helicopter Aerodynamics, Cambridge University Press, 2006.",
        "Y. Zeng, J. Xu, R. Zhang, \"Energy Minimization for Wireless Communication With Rotary-Wing UAV,\" IEEE Trans. Wireless Commun., 2019. doi:10.1109/TWC.2019.2902559",
        "T. A. Rodrigues et al., \"In-flight positional and energy use data set of a DJI Matrice 100 quadcopter for small package delivery,\" Scientific Data, 8, 155, 2021. doi:10.1038/s41597-021-00930-x",
        "F. Betti Sorbelli et al., \"Energy-Constrained Delivery of Goods With Drones Under Varying Wind Conditions,\" IEEE Trans. ITS, 2021. doi:10.1109/TITS.2020.3044420",
        "A. Thibbotuwawa et al., \"UAV Mission Planning Resistant to Weather Uncertainty,\" Sensors, 20(2), 515, 2020. doi:10.3390/s20020515",
        "F. Lian et al., \"WA-LPA*: An Energy-Aware Path-Planning Algorithm for UAVs in Dynamic Wind Environments,\" Drones, 9(12), 850, 2025. doi:10.3390/drones9120850",
        "F. Lian et al., \"Optimal Path Planning of Unmanned Aerial Vehicle with Wind Field,\" Proc. IEEE ITSC, 2025.",
        "R. Gu, Y. Zhao, X. Ren, \"Integrating wind field analysis in UAV path planning: Enhancing safety and energy efficiency for urban logistics,\" Chinese J. Aeronautics, 39(1), 103605, 2026. doi:10.1016/j.cja.2025.103605",
        "L. Blackmore, M. Ono, B. C. Williams, \"Chance-Constrained Optimal Path Planning With Obstacles,\" IEEE Trans. Robotics, 27(6), 2011. doi:10.1109/TRO.2011.2161160",
        "H. Zhu, J. Alonso-Mora, \"Chance-Constrained Collision Avoidance for MAVs in Dynamic Environments,\" IEEE RA-L, 4(2), 2019. doi:10.1109/LRA.2019.2893494",
        "M. Schäfer et al., \"Bringing up OpenSky: A large-scale ADS-B sensor network for research,\" Proc. IPSN, 2014. doi:10.1109/IPSN.2014.6846743",
        "X. Olive, \"traffic, a toolbox for processing and analysing air traffic data,\" J. Open Source Software, 4(39), 1518, 2019. doi:10.21105/joss.01518",
        "J. K. Stolaroff et al., \"Energy use and life cycle greenhouse gas emissions of drones for commercial package delivery,\" Nature Communications, 9, 409, 2018. doi:10.1038/s41467-017-02411-5",
        "K. Dorling et al., \"Vehicle Routing Problems for Drone Delivery,\" IEEE Trans. SMC: Systems, 2017. doi:10.1109/TSMC.2016.2582745",
        "J. Zhang et al., \"Energy consumption models for delivery drones: A comparison and assessment,\" Transportation Research Part D, 2021. doi:10.1016/j.trd.2020.102668",
        "MAVLink Developer Guide, Common Message Set. https://mavlink.io/en/messages/common.html",
        "DJI, Matrice 4 Series Specs. https://enterprise.dji.com/matrice-4-series/specs",
        "DJI, Cloud API Documentation – DJI WPML (공개 저장소 dji-sdk/Cloud-API-Doc, 2024-11-07 판)",
        "DJI, Mobile SDK V5 Release Note (v5.12.0, 2025-01-08: Matrice 4E/4T 지원 추가). https://developer.dji.com/doc/mobile-sdk-tutorial/en",
        "DJI, Cloud API – Product Supported. https://developer.dji.com/doc/cloud-api-tutorial/en/overview/product-support.html",
        "DJI, Cloud API – DJI WPML Common Elements. https://developer.dji.com/doc/cloud-api-tutorial/en/api-reference/dji-wpml/common-element.html",
    ]
    pr = w.para("ref", left=9, hang=9, before=2, after=0, line=140, align="LEFT", autotab=True)
    for i, r in enumerate(refs, 1):
        w._p(f"[{i}]\t{r}", pr, w.run("ref", size=9.5))

    # ===== 부록 =====
    w.h1("부록. 산출물 목록", page_break=True)
    w.chapter = "부록"
    w.table(
        "주요 산출물",
        ["구분", "산출물", "위치 (저장소)"],
        [
            ["소스 코드", "경로계획·에너지 모델·실시간 엔진·웹 서버", "src/dpo/"],
            ["보고서", "배포 보고서(Word), 신뢰도·현장 실증 보고서, 본 최종 보고서", "docs/*.docx, docs/*.hwpx"],
            ["발표자료", "학술 발표(46장), 회의용(37장)", "docs/*.pptx"],
            ["매뉴얼", "설치부터 첫 경로까지 사용자 매뉴얼", "docs/drone_soc_user_manual.docx"],
            ["시험비행 분석", "2026-11-13 시험비행 분석 보고서, 비행 기록표 양식", "docs/drone_soc_flight_test_20261113.docx"],
            ["자료 요청", "발주처 자료 요청서", "docs/drone_soc_data_request.docx"],
            ["실험 결과", "E13–E16, SITL, 시험비행 결과 수치", "results/*.json, results/figures/"],
            ["논문 원고", "영문 원고 초안, 참고문헌", "docs/paper/"],
        ],
        widths=[1.2, 3.4, 2.6],
        align=["C", "L", "L"],
    )

    # 쪽 번호
    try:
        doc.set_page_number(position="BOTTOM_CENTER", prefix="- ", suffix=" -")
    except Exception as exc:  # pragma: no cover
        print("쪽 번호 실패:", exc)
    try:
        doc.set_header_text("SoC 기반 무인 운반 드론 에너지 인식 경로 최적화 ― 최종 보고서")
    except Exception as exc:  # pragma: no cover
        print("머리말 실패:", exc)

    rep = doc.validate()
    if rep.issues:
        raise SystemExit(f"검증 실패: {rep.issues}")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save_to_path(str(out))
    print("저장:", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "drone_soc_final_report.hwpx"))
    build(Path(ap.parse_args().out))
