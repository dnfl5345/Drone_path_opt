"""학술 발표 자료 (Intro → Background → Related Works → Proposed Method → Results → Conclusion)
KETI 회의자료 템플릿(표지·머리말·제목 글꼴·세로선·로고·끝 장)을 그대로 써서 PPTX 로 만든다.

사용: PYTHONPATH=src python scripts/build_academic_ppt.py <template.pptx> <out.pptx>
- 머리말의 'Path Planning' 자리에 절 이름(01 Introduction …)을 넣는다.
- 본문 표기는 템플릿의 '□ 소제목 / ○ 항목' 방식을 따른다.
- DJI 조건 검토 수치는 results/dji_conditions.json 에서 읽는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.util import Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_ppt import (BOX, FIG, GRAY, INK, LIGHT, NAVY, ORANGE, ROOT, TEAL, TITLE, WHITE, Deck, box, box_text,  # noqa: E402
                       normalize_partnames, notes, picture, set_run, table, textbox)

DJ = json.loads((ROOT / "results" / "dji_conditions.json").read_text())
_X = ROOT / "results" / "x650.json"
X6 = json.loads(_X.read_text()) if _X.exists() else None
X0, W = 0.6, 12.15          # 본문 좌우 범위 [in]
SRC_Y = 7.02                # 출처 줄 위치 (오른쪽 아래 로고와 겹치지 않게 폭 제한)


# ------------------------------------------------------------------ 공통 요소
def section(slide, label):
    for sh in slide.shapes:
        if sh.name == "TextBox 10":
            sh.text_frame.paragraphs[0].runs[0].text = label


def head(slide, x, y, text, w=6.0, size=16):
    """템플릿의 '□ 소제목' 표기."""
    return textbox(slide, x, y, w, 0.36, [[("□ ", {"color": TITLE}), (text, {"color": TITLE})]], size=size, bold=True)


def items(slide, x, y, w, h, rows, size=14, gap=5, mark="○ "):
    """템플릿의 '○ 항목' 표기. rows: str | (굵은 앞말, 뒷말)."""
    paras = []
    for r in rows:
        if isinstance(r, tuple):
            paras.append([(mark, {"bold": True, "color": INK}), (r[0], {"bold": True, "color": INK}),
                          (r[1], {"bold": False, "color": INK})])
        else:
            paras.append([(mark, {"bold": True, "color": INK}), (r, {"bold": False, "color": INK})])
    return textbox(slide, x, y, w, h, paras, size=size, space_after=gap)


def stat(slide, x, y, w, h, label, value, sub, dark=False, vsize=26):
    sp = box(slide, x, y, w, h, fill=NAVY if dark else BOX, shadow=False)
    box_text(sp, [[(label, {"size": 12, "bold": False, "color": WHITE if dark else GRAY})],
                  [(value, {"size": vsize, "bold": True, "color": ORANGE if dark else NAVY})],
                  [(sub, {"size": 12, "bold": False, "color": WHITE if dark else GRAY})]])
    return sp


def card(slide, x, y, w, h, tag, title, body, size=13, fill=BOX, tag_color=TEAL):
    sp = box(slide, x, y, w, h, fill=fill, shadow=False)
    paras = []
    if tag:
        paras.append([(tag, {"size": 11, "bold": True, "color": tag_color})])
    paras.append([(title, {"size": 16, "bold": True, "color": NAVY})])
    for b in (body if isinstance(body, list) else [body]):
        paras.append(b if isinstance(b, list) else [(b, {"size": size, "bold": False, "color": INK})])
    box_text(sp, paras, anchor=MSO_ANCHOR.TOP)
    sp.text_frame.margin_top = Pt(9)
    for p in sp.text_frame.paragraphs:
        p.space_after = Pt(5)
    return sp


def line(slide, y, runs, size=15, x=X0, w=W, h=0.42):
    return textbox(slide, x, y, w, h, [runs], size=size, bold=False, color=INK)


def source(slide, text):
    return textbox(slide, X0, SRC_Y, 10.6, 0.3, [text], size=10.5, bold=False, color=GRAY)


def b(t):
    return (t, {"bold": True, "color": NAVY})


def n(t):
    return (t, {"bold": False, "color": INK})


# ------------------------------------------------------------------ 슬라이드
def build(template, out):
    D = Deck(template)
    D.set_title_page(["바람·불확실 이동 장애물 하의", "드론 배송 에너지 최적 시공간 경로계획"], "2026. 11.")
    SEC = ["01  Introduction", "02  Background", "03  Related Works", "04  Proposed Method", "05  Results",
           "06  Conclusion"]

    # ---- 발표 순서
    s = D.content("발표 순서")
    section(s, "Outline")
    cards = [("01", "Introduction", "연구 동기, 연구 질문, 기여"),
             ("02", "Background", "왜 SOC·왜 바람·왜 경로인가, DJI 드론 조건 전수 검토, 전력 모델, 시공간 계획"),
             ("03", "Related Works", "최신 연구와의 비교, 남은 공백"),
             ("04", "Proposed Method", "문제 정식화, WE-SIPP-Theta*, 해석적 위험구간, 실시간 운용, 대상 기체 X650·3D 지도"),
             ("05", "Results", "실측 데이터 기반 실험 E0–E12, 실시간 벤치"),
             ("06", "Conclusion", "핵심 결론, 한계, 향후 연구")]
    for i, (no, t, d) in enumerate(cards):
        x, y = X0 + (i % 3) * 4.1, 1.5 + (i // 3) * 2.45
        card(s, x, y, 3.95, 2.1, None, f"{no}  {t}", d, size=15)
    notes(s, "여섯 부분으로 진행합니다. 배경에서는 왜 SOC가 목적 변수이고 왜 바람이 변인인지를, DJI 드론이 내놓는 조건 전체를 "
             "검토한 결과로 설명합니다. 결과는 계획 성능 → 불확실성 → 실측 항적 → 실시간 운용 순서로 점점 현실에 가까워집니다.")

    # ================================================================= 01 Introduction
    s = D.content("왜 '최단 경로'가 아니라 '최소 SOC'인가")
    section(s, SEC[0])
    head(s, X0, 1.2, "문제를 만드는 세 가지 사실")
    items(s, X0, 1.7, 7.2, 4.6, [
        ("배터리가 임무 가능성을 결정합니다. ", "소형 배송 드론은 예비 SOC를 남기고 왕복을 마쳐야 합니다."),
        ("저고도 바람은 강하고 변합니다. ", "지면 30–150 m 바람은 고도·시간에 따라 바뀝니다. 실측 비행에서 거리 비례 에너지 "
                                  "모델은 R² −1.47로 설명력이 없었습니다."),
        ("공역은 불확실한 이웃과 함께 씁니다. ", "다른 드론·헬기의 미래 위치는 예측만 할 수 있고, 예측 오차는 앞을 볼수록 커집니다."),
    ], size=17, gap=22)
    head(s, 8.2, 1.2, "강풍일 배송 왕복 SOC 소모", w=4.5)
    stat(s, 8.2, 1.75, 4.55, 2.25, "최단 경로 + 정속 10 m/s", "102.9 %p", "배터리 부족, 비행 불가", vsize=36)
    stat(s, 8.2, 4.2, 4.55, 2.25, "제안 방법 (WE-LT*)", "76.7 %p", "예비 SOC 20 % 확보", dark=True, vsize=36)
    source(s, "조건: 2026-10-27 15시 출발, 거점↔배송지 2.4 km 왕복, 탑재 0.5 kg, DJI M100 전력 모델")
    notes(s, "세 가지 사실로 문제를 세웁니다. 첫째, 배송 드론은 배터리 한계 안에서 왕복해야 합니다. 둘째, 바람 때문에 거리와 "
             "에너지가 비례하지 않습니다. CMU 실측 비행에서 거리 비례 모델은 R² −1.47이었습니다. 셋째, 다른 비행체 궤적은 "
             "불확실합니다. 오른쪽 수치는 강풍일 15시, 2.4 km 왕복, 탑재 0.5 kg 조건입니다.")

    s = D.content("연구 질문과 기여")
    section(s, SEC[0])
    head(s, X0, 1.2, "연구 질문")
    rq = [("RQ1", "바람 속에서 SOC를 줄이는 것은 경로·고도·속도 중 무엇인가"),
          ("RQ2", "불확실한 이동 장애물의 충돌 위험을 에너지 효율적으로 제한할 수 있는가"),
          ("RQ3", "그 결론이 실측 예측 오차와 폐루프·실시간 운용에서도 유지되는가")]
    for i, (k, t) in enumerate(rq):
        sp = box(s, X0, 1.75 + i * 1.6, 5.3, 1.4, fill=BOX, shadow=False)
        box_text(sp, [[(k, {"size": 14, "bold": True, "color": ORANGE})], [(t, {"size": 14, "bold": False, "color": INK})]])
    head(s, 6.35, 1.2, "기여")
    cb = [("정식화", "경로·고도·구간 속도·대기를 함께 고르는 SOC 최소화 + 시점별 충돌 기회 제약"),
          ("알고리즘", "WE-SIPP-Theta*: 에너지 비교형 지름길 + 구간별 선형 팽창 반경의 해석적 안전구간"),
          ("실증", "실측 지형·건물·GFS 예보, 실측 비행 198회, ADS-B 실측 항적 18대"),
          ("실시간 운용", "비동기 재계획·비상 정지·즉시 계획 후 개선을 갖춘 운용 플랫폼")]
    for i, (k, t) in enumerate(cb):
        y = 1.75 + i * 1.2
        sp = box(s, 6.35, y, 0.62, 1.0, fill=NAVY, shadow=False)
        box_text(sp, [[(str(i + 1), {"size": 20, "bold": True, "color": WHITE})]], align=PP_ALIGN.CENTER)
        textbox(s, 7.1, y + 0.02, 5.65, 1.0, [[(k, {"size": 15, "bold": True, "color": NAVY})],
                                             [(t, {"size": 13, "bold": False, "color": INK})]], space_after=2)
    notes(s, "연구 질문 세 개와 기여 네 가지입니다. RQ1은 계획 요소별 기여, RQ2는 불확실성 튜브와 고정 버퍼의 비교, RQ3는 실측 "
             "ADS-B 항적과 실시간 플랫폼으로 답합니다. 기여 2의 핵심은 팽창 반경이 시간에 따라 구간별 선형이면 위험 시간구간을 "
             "표본 없이 해석적으로 구할 수 있다는 점입니다.")

    # ================================================================= 02 Background
    s = D.content("왜 SOC를 목적 변수로 두는가")
    section(s, SEC[1])
    picture(s, FIG / "F22_why_soc.png", X0, 1.1, W, 3.55)
    stat(s, X0, 4.8, 3.95, 1.45, "실측 비행: 거리 비례 에너지 모델", "R² −1.47", "전력 모델은 R² 0.77")
    stat(s, X0 + 4.1, 4.8, 3.95, 1.45, "SOC 최소 계획 (12회 평균)", "거리 +6.6 %", "SOC −24 % · 시간 −22 %")
    stat(s, X0 + 8.2, 4.8, 3.95, 1.45, "예비 SOC 20 % 충족", "0/12 → 12/12", "거리 최소 → SOC 최소", dark=True)
    line(s, 6.4, [n("SOC는 "), b("임무 가능 여부의 단위"), n("이고, 바람·중량·속도·고도·대기를 "), b("한 척도로 합산"),
                  n("하며, 기체가 "), b("비행 중 직접 계측"), n("합니다.")])
    source(s, "배송 왕복, 출발 09–20시 12회 · 실측 CMU M100 56회(학습에 쓰지 않은 날짜)")
    notes(s, "목적 변수 후보는 거리, 시간, 에너지, SOC입니다. 네 기준으로 골랐습니다. 임무 가능 여부와 같은 단위인가, 바람·중량·"
             "속도·고도·대기를 한 척도로 합산하는가, 기체가 비행 중 직접 재는가, 기체·배터리가 달라도 비교할 수 있는가. "
             "실측 비행에서 거리 비례 모델은 R² −1.47이었습니다. 같은 임무를 세 목적으로 계획하면 SOC 최소 계획은 거리가 6.6 % "
             "길지만 SOC는 24 %, 시간은 22 % 적습니다. 거리 최소 계획은 12회 모두 임무 불가였습니다. 질문 대비: 한 기체·한 "
             "배터리에서는 에너지 최소와 SOC 최소의 최적 경로가 같습니다. SOC를 쓰는 이유는 제약, 계측, 운용 판단이 모두 SOC "
             "단위이기 때문입니다. 전압 강하·온도·노화의 비선형성은 아직 모델에 넣지 않았습니다.")

    s = D.content("회전익 전력 모델과 SOC")
    section(s, SEC[1])
    eq = [("① 시간", "tₖ = dₖ / V_g,ₖ"), ("② 구간 에너지", "Eₖ = P(Vₐ, V_z, m) · tₖ"),
          ("③ 총 에너지", "E = ΣEₖ + E이착륙 + E호버"), ("④ SOC 소모", "ΔSOC = E / E_b × 100"),
          ("⑤ 목적", "π* = argmin ΔSOC")]
    for i, (k, f) in enumerate(eq):
        sp = box(s, X0 + i * 2.45, 1.2, 2.3, 1.15, fill=NAVY if i == 4 else BOX, shadow=False)
        box_text(sp, [[(k, {"size": 12, "bold": True, "color": WHITE if i == 4 else TEAL})],
                      [(f, {"size": 13, "bold": True, "color": WHITE if i == 4 else INK})]], align=PP_ALIGN.CENTER)
    line(s, 2.5, [b("P = P유도(m, Vₐ) + P형상(Vₐ) + P유해(Vₐ) + P상승(m, V_z)"), n(",  대기속도 Vₐ = ‖V_g − W‖")], size=15)
    line(s, 2.92, [n("바람 W가 대기속도를 바꾸므로 같은 거리도 방향에 따라 에너지가 다릅니다 (Leishman 2006; Zeng et al. 2019).")],
         size=14)
    picture(s, FIG / "F3_energy_validation.png", X0, 3.45, W, 3.45)
    source(s, "그림: CMU DJI M100 실측 198회, 12개 날짜 학습 · 6개 날짜 시험 → R² 0.77, MAPE 9.6 %")
    notes(s, "에너지 계산은 다섯 단계 식입니다. 전력은 유도·형상·유해·상승 항의 합이고, 바람 때문에 대기속도가 달라집니다. 계수는 "
             "CMU의 DJI Matrice 100 실측 198회 비행으로 보정했습니다. 12개 날짜로 학습하고 다른 6개 날짜로 시험한 결과 R² 0.77, "
             "MAPE 9.6 %였습니다. 탑재중량별로 평균 전력을 3–9 % 낮게 예측하는 편향은 한계로 남아 있습니다.")

    # ---- DJI 조건 전수 검토 (신규)
    G, CR = DJ["groups"], DJ["counts_by_role"]
    g = lambda *k: sum(G[x]["n"] for x in k)  # noqa: E731
    s = D.content(f"DJI 드론이 내놓는 조건 전수 검토 – {DJ['n_total']}개 항목")
    section(s, SEC[1])
    rows = [["역할", "항목 수", "물리량 (대표 항목)"],
            ["결정 변수와 그 결과", str(CR["DEC"]),
             f"위치 {g('pos')} · 고도 {g('alt')} · 대지속도 {g('vel')} · 자세 {g('att')} · 가속도 {g('acc')} · 각속도 {g('gyro')} · "
             f"기수 방위 {g('heading')} · 모터 {g('esc')}  (GPS_FUSED, VELOCITY, QUATERNION, ESC_DATA)"],
            ["상태·안전 감시", str(CR["MON"]),
             f"비행 상태·모드 {g('mode')} · GNSS·RTK 품질 {g('gnss')} · 이상 플래그 {g('fault')}  (STATUS_FLIGHT, FLIGHT_ANOMALY)"],
            ["고정 입력·설정", str(CR["FIX"]),
             f"홈포인트 {g('home')} · 제한·귀환 설정 {g('limits')} · 배터리 노화 {g('aging')} · 배터리 온도 {g('batt_temp')} · "
             f"비행 이력 {g('history')}"],
            ["배터리 (목적·제약)", str(CR["OBJ"]),
             f"잔량 {g('soc')} · 전압·전류 {g('power')} · 저전압 경고 {g('battwarn')}  (KeyChargeRemainingInPercent, KeyVoltage, "
             f"KeyCurrent)"],
            ["외생 환경", str(CR["ENV"]),
             [("풍속 1 · 풍향 1 · 강풍 경고 1", {"color": ORANGE}),
              (f"  (KeyWindSpeed, KeyWindDirection, KeyWindWarning) · 주변 유인기 {g('traffic')} · 장애물 거리 {g('obst')}", {})]],
            ["명령·조종 입력·식별", str(CR["ETC"]),
             f"명령 키 {g('cmd')} · 짐벌 {g('gimbal')} · 조종기 입력 {g('rc')} · 식별·연결 {g('ident')} · 시각 {g('time')}"]]
    table(s, X0, 1.15, W, rows, [2.3, 0.9, 8.95], row_h=0.5, head_size=13, body_size=12, highlight=(5,),
          aligns=[PP_ALIGN.CENTER, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    stat(s, X0, 4.9, 3.95, 1.45, "드론 밖에서 주어지는 환경 조건", f"{CR['ENV']} / {DJ['n_total']}개",
         "바람 3 · 주변 유인기 2 · 장애물 2", dark=True)
    items(s, X0 + 4.2, 4.9, 7.95, 1.9, [
        ("배터리 값은 결과", "이고, 위치·속도·자세·모터 값은 우리가 정하는 경로의 결과입니다."),
        ("탑재중량을 재는 항목은 없습니다. ", "주문 정보로 넣어야 합니다."),
        ("외기온·기압 항목도 없습니다. ", "기압고도로 간접 추정할 수 있을 뿐입니다."),
    ], size=14, gap=6)
    source(s, f"출처: DJI Onboard SDK 토픽 {DJ['n_by_sdk']['Onboard SDK']} · Payload SDK 토픽 {DJ['n_by_sdk']['Payload SDK']} · "
              f"Mobile SDK V5 비행제어기 키 {DJ['n_by_sdk']['Mobile SDK V5 · 비행제어기']} + 배터리 키 "
              f"{DJ['n_by_sdk']['Mobile SDK V5 · 배터리']} (같은 물리량이 SDK마다 중복)")
    notes(s, f"왜 바람인가에 답하기 위해 후보를 임의로 고르지 않고, DJI 드론이 실제로 내놓는 조건 전부를 놓고 걸렀습니다. DJI 공식 "
             f"SDK 세 가지, 곧 기체 탑재 컴퓨터용 Onboard SDK, 탑재 장치용 Payload SDK, 조종기 앱용 Mobile SDK V5의 비행 관련 "
             f"항목을 원문에서 모두 추출하니 {DJ['n_total']}개였습니다. 계획에서의 역할로 나누면, 드론이 정하지 않고 밖에서 "
             f"주어지는 환경 조건은 {CR['ENV']}개뿐입니다. 바람 3개, 주변 유인기 2개, 장애물 거리 2개입니다. 나머지는 결과이거나 "
             f"설정·감시·명령입니다. 기체마다 지원 항목이 다르다는 점, 카메라·짐벌 전용 키는 뺐다는 점은 미리 밝힙니다.")

    s = D.content("다섯 질문으로 선별 – 모두 '예'인 조건은 바람뿐")
    section(s, SEC[1])
    GRN, AMB, MUTE = RGBColor(0x2C, 0xA5, 0x8D), RGBColor(0xC9, 0xA2, 0x27), RGBColor(0xA9, 0xB1, 0xB8)
    sym = {1: ("✔", GRN), 0.5: ("△", AMB), 0: ("✘", MUTE), None: ("–", MUTE)}
    e = DJ["cmu"]["effects"]
    EV = {"풍속·풍향": "모델: 10 m/s에서 방향별 −38 ~ +150 %", "탑재중량": f"실측: 0→500 g에서 평균 전력 {e['payload_0_500_pct']:+.0f} %",
          "대지속도·고도·위치": f"실측: 속도 4→12 m/s에서 미터당 {e['speed_4_12_spec_pct']:+.0f} %",
          "강풍 경고 등급": "등급만 제공, 현재 위치 값"}
    rows = [["조건", "외생\n조건", "에너지\n영향 큼", "시간·공간\n변동", "경로별\n작용 차이", "사전에\n파악 가능", "본 연구에서의 역할", "근거"]]
    for r in DJ["screening"]:
        ok = all(q == 1 for q in r["q"])
        rows.append([[(r["name"], {"color": ORANGE if ok else INK})]]
                    + [[(sym[q][0], {"color": sym[q][1], "size": 14})] for q in r["q"]]
                    + [[(r["role"], {"color": ORANGE if ok else INK})],
                       [(EV.get(r["name"], r["ev"]).replace("-", "−"), {"bold": False, "size": 11})]])
    table(s, X0, 1.1, W, rows, [2.2, 0.72, 0.82, 0.88, 0.9, 0.93, 2.6, 3.1], row_h=0.4, head_size=11, body_size=12,
          highlight=(1,), aligns=[PP_ALIGN.CENTER] * 6 + [PP_ALIGN.LEFT, PP_ALIGN.LEFT])
    line(s, 6.5, [b("바람"), n("은 비용 변인, "), b("주변 유인기·장애물"), n("은 제약 변인, "), b("탑재중량·공기밀도"),
                  n("는 고정 입력입니다. 계획기는 이 구분을 그대로 따릅니다.")], size=14)
    source(s, "✔ 예 · △ 부분적 · ✘ 아니오 · – 해당 없음 (조건이 아니라 결과이거나 결정 변수) · 근거의 실측은 CMU DJI M100, 모델은 보정 전력 모델")
    notes(s, "비용 변인이 되려면 다섯 질문에 모두 '예'여야 합니다. 드론의 결정과 무관하게 주어지는가, 에너지 영향이 큰가, 시간·"
             "공간에 따라 변하는가, 경로에 따라 작용이 달라지는가, 경로 전체에 대해 미리 알 수 있는가. 가운데 표에서 다섯 개 모두 "
             "'예'인 것은 풍속·풍향뿐입니다. 주변 유인기와 장애물은 에너지를 직접 바꾸지는 않지만 지나갈 수 있는 공간을 바꾸므로 "
             "제약으로 다룹니다. 탑재중량은 영향이 크지만 주문이 정하고 구간 안에서 일정해 경로를 가르지 않습니다. 강풍 경고 "
             "등급은 수치가 아니고 현재 위치 값이라 안전 감시용입니다.")

    C = DJ["cmu"]
    P, SP, WS = C["targets"]["mean_power_w"], C["targets"]["specific_energy_j_m"], C["wind_sensor"]
    s = D.content("실측 확인 – 조건별 설명력과 풍속 측정의 한계")
    section(s, SEC[1])
    head(s, X0, 1.15, f"실측 비행 {C['n']}회: 관측 범위만큼 바꿨을 때 변화", w=7.0)
    unit = {"탑재중량": "0 → 500 g", "설정 속도": "4 → 12 m/s", "설정 고도": "25 → 100 m",
            "풍속 (기체 풍속계, 스칼라)": "3.3 → 6.0 m/s", "배터리 전압": "21.3 → 23.7 V"}
    role = {"탑재중량": "고정 입력", "설정 속도": "결정 변수", "설정 고도": "결정 변수",
            "풍속 (기체 풍속계, 스칼라)": "방향 없음", "배터리 전압": "결과"}
    rows = [["조건", "관측 범위", "평균 전력", "미터당 에너지", "역할"]]
    for k in P["range_effect_pct"]:
        rows.append([k.replace(" (기체 풍속계, 스칼라)", " (스칼라)"), unit[k], f"{P['range_effect_pct'][k]['v']:+.1f} %",
                     f"{abs(SP['range_effect_pct'][k]['v']):.1f} %", role[k]])
    table(s, X0, 1.65, 7.0, rows, [2.0, 1.7, 1.25, 1.45, 1.2], row_h=0.5, head_size=13, body_size=13, highlight=(4,),
          aligns=[PP_ALIGN.CENTER] * 5)
    textbox(s, X0, 4.75, 7.0, 0.6, [f"다중 회귀, 전체 R²: 평균 전력 {P['r2_full']:.2f} · 미터당 에너지 {SP['r2_full']:.2f}. "
                                    "미터당 에너지 변화는 크기(절댓값)"], size=11, bold=False, color=GRAY)
    stat(s, X0, 5.35, 3.4, 1.45, "스칼라 풍속의 미터당 에너지 설명력", f"ΔR² {SP['drop_r2']['풍속 (기체 풍속계, 스칼라)']['v']:.3f}",
         "평균 전력은 +10 %")
    stat(s, X0 + 3.6, 5.35, 3.4, 1.45, "최대 풍속과 설정 속도의 상관", f"{WS['corr_max_vs_speed']:.2f}",
         "풍속계가 기체 속도도 함께 잼", dark=True)
    head(s, 8.0, 1.15, "풍속 크기만으로는 효과가 보이지 않는 이유", w=4.8)
    items(s, 8.0, 1.65, 4.75, 5.2, [
        ("방향이 없습니다. ", "비행 단위 자료에는 풍속 크기만 있습니다. 바람의 효과는 방향에 따라 부호가 바뀝니다."),
        ("닫힌 경로입니다. ", "출발지로 돌아오면 역풍과 순풍이 섞입니다. 모델로도 4 m/s 바람의 왕복 평균 증가는 6 %입니다."),
        ("기체 풍속계는 바람과 기체 속도를 합쳐 잽니다. ", f"설정 속도 4→12 m/s에서 최대 풍속이 {WS['max_at_4']:.1f}→"
                                              f"{WS['max_at_12']:.1f} m/s로 늘었습니다."),
        ("시험 당일 바람이 약했습니다. ", f"일평균 {WS['day_mean_range'][0]:.1f}–{WS['day_mean_range'][1]:.1f} m/s."),
    ], size=14, gap=9)
    source(s, "CMU DJI M100 실측 (경로 R1, 설정 속도 > 0, 탑재 0·250·500 g) · 5–95 백분위 범위")
    notes(s, f"실측으로 확인한 결과입니다. 조건이 고르게 바뀐 {C['n']}회 비행에서, 미터당 에너지를 가장 많이 바꾸는 것은 속도이고 "
             "이는 계획기가 정하는 결정 변수입니다. 탑재중량은 평균 전력을 가장 많이 설명하지만 고정 입력입니다. 배터리 전압은 "
             "설명력이 없습니다. 정직하게 밝힐 점은, 스칼라 풍속이 미터당 에너지를 설명하지 못했다는 것입니다. 이것은 바람이 "
             "중요하지 않다는 뜻이 아니라 이 자료로는 볼 수 없다는 뜻입니다. 방향 정보가 없고, 닫힌 경로라 상쇄되고, 기체에 단 "
             "풍속계가 기체 속도까지 함께 재기 때문입니다. 그래서 바람은 크기만이 아니라 방향을 가진 벡터로 다뤄야 하고, 다음 "
             "장에서 모델로 그 효과를 보입니다. 또 실측의 탑재중량 효과가 모델의 수평 순항 효과보다 커서, 모델의 질량 민감도는 "
             "따로 확인이 필요합니다.")

    s = D.content("DJI에서 바람을 얻는 방법과 본 연구의 선택")
    section(s, SEC[1])
    rows = [["경로", "얻는 값", "한계"],
            ["Mobile SDK V5\nKeyWindSpeed · KeyWindDirection", "풍속(dm/s)과 풍향(지구 좌표)\n기체가 추정한 값",
             "현재 위치의 값만 제공\n지원 기체·정확도는 문서에 없음 [확인요망]"],
            ["KeyWindWarning\nFLIGHT_ANOMALY.strongWindLevel1/2", "강풍 등급",
             "수치가 아닌 등급\nOnboard·Payload SDK에는 풍속·풍향 수치 토픽이 없음"],
            ["추가 풍속계\n(CMU M100 실측 방식)", "기체 기준 풍속·풍향", "M100에는 바람 항목이 없어 따로 장착\n기체 속도를 빼야 실제 바람"],
            ["수치 예보\n(본 연구: NOAA GFS)", "공역 전체의 시간별 바람장", "격자가 성기고 예보 오차가 있음"]]
    table(s, X0, 1.15, W, rows, [3.9, 3.5, 4.75], row_h=0.78, head_size=13, body_size=12.5, highlight=(4,),
          aligns=[PP_ALIGN.LEFT] * 3, first_col_center=False)
    items(s, X0, 5.25, W, 1.7, [
        ("기체가 주는 바람은 '지금 여기'의 값이고, 계획에는 '앞으로 갈 곳'의 바람이 필요합니다. ", "그래서 예보 바람장으로 계획합니다."),
        ("기체의 바람 값은 예보를 확인·보정하는 데 쓸 수 있습니다. ", "현재 구현에는 넣지 않았고 향후 과제입니다."),
        ("강풍 플래그는 비용이 아니라 안전 판단(운항 중단)에 맞는 값입니다.", ""),
    ], size=14, gap=5)
    source(s, "출처: DJI Mobile-SDK-Android-V5 API 문서, Payload-SDK dji_fc_subscription.h, Onboard-SDK dji_telemetry.hpp · Rodrigues et al. 2021")
    notes(s, "DJI에서 바람을 얻는 경로는 네 가지입니다. Mobile SDK V5에는 풍속과 풍향 키가 있고, 기체가 추정한 현재 위치의 값입니다. "
             "어느 기체가 지원하는지와 정확도는 문서에서 확인하지 못했습니다. Onboard SDK와 Payload SDK에는 풍속·풍향 수치가 없고 "
             "강풍 플래그 두 개만 있습니다. CMU 실측에 쓰인 M100에는 바람 항목이 없어 풍속계를 따로 달았습니다. 계획에는 앞으로 "
             "갈 곳의 바람이 필요하므로 본 연구는 수치 예보 바람장을 씁니다. 기체의 바람 값으로 예보를 보정하는 것은 향후 "
             "과제입니다.")

    s = D.content("왜 바람의 크기와 방향을 함께 변인으로 두는가")
    section(s, SEC[1])
    picture(s, FIG / "F23_why_wind.png", X0, 1.1, W, 3.6)
    stat(s, X0, 4.85, 3.95, 1.45, "바람 10 m/s, 순풍 ↔ 역풍", "−38 ~ +150 %", "미터당 에너지 4.1배 차이", dark=True)
    stat(s, X0 + 4.1, 4.85, 3.95, 1.45, "다른 요인의 전체 범위", "14 % · 6 %", "탑재중량 0–0.75 kg · 공기밀도")
    stat(s, X0 + 8.2, 4.85, 3.95, 1.45, "왕복해도 상쇄되지 않음 (10 m/s)", "1.56배", "역풍 + 순풍 평균 / 무풍")
    line(s, 6.42, [n("방향에 따라 효과의 "), b("부호가 바뀌고"), n(", 실제 바람은 고도·시각에 따라 "), b("6.7–12.5 m/s"),
                   n("로 변합니다 → 바람장 W(x, y, z, t).")])
    source(s, "실측 보정 전력 모델 (무풍·3.6 kg 기준 29.9 J/m) · NOAA GFS 예보 2026-10-27")
    notes(s, "왼쪽 그림은 보정한 전력 모델로 계산한 요인별 영향입니다. 10 m/s 바람은 방향에 따라 미터당 에너지를 −38 %에서 +150 %"
             "까지 바꿉니다. 탑재중량은 보정 범위 전체를 바꿔도 14 %, 공기밀도는 6 %입니다. 가운데 그림은 왜 방향까지 필요한지 "
             "보여 줍니다. 방향에 따라 부호가 바뀌고, 왕복해도 상쇄되지 않습니다. 최적 속도도 순풍 20, 측풍 13.5, 역풍 7 m/s로 "
             "달라집니다. 오른쪽 그림은 실제 예보 바람입니다. 임무 수준에서도 출발 시각에 따른 바람 차이로 SOC가 12.6 %p "
             "달라지지만, 탑재중량 전체 범위로는 4.4 %p입니다. 한계: 민감도는 모델 계산입니다.")

    s = D.content("바람 속에서는 '보이면 직선'이 최적이 아니다")
    section(s, SEC[1])
    picture(s, FIG / "F13_layered_8way.png", X0, 1.15, 7.4, 5.7)
    card(s, 8.25, 1.2, 4.5, 1.55, None, "거리 비용", "삼각부등식이 성립합니다. 시야가 트이면 직결하는 Theta*·Lazy Theta*가 최적입니다.")
    card(s, 8.25, 2.9, 4.5, 1.75, None, "에너지 비용", "비용이 방위·대지속도에 따라 달라 단위구가 볼록하지 않습니다. 직결이 오히려 "
                                                "비쌀 수 있습니다.")
    stat(s, 8.25, 4.8, 4.5, 1.3, "볼록 껍질 대비 안쪽으로 들어간 정도", "1.1–9.0 %", "실제 기체·실제 바람", dark=True)
    line(s, 6.25, [n("→ 지름길은 "), b("에너지로 비교해"), n(" 더 쌀 때만 채택")], x=8.25, w=4.5, size=14)
    source(s, "8방향 탐색·고도별 바람 조회표, 실제 GFS 바람에서 그린 에너지 비용 노름의 단위구")
    notes(s, "Theta* 계열은 거리 비용이 삼각부등식을 만족한다는 사실에 기대어, 부모 노드가 보이면 바로 잇습니다. 바람이 있으면 "
             "비용 노름의 단위구가 볼록하지 않습니다. 실제 기체 전력 모델과 실제 GFS 바람으로 그렸을 때 볼록 껍질보다 1.1–9.0 % "
             "안쪽으로 들어갔습니다. 그래서 지름길을 에너지로 비교해 채택하는 규칙이 필요합니다. 고도별 선형 바람 가정을 쓰면 "
             "간선 비용을 조회표로 바꿔 계산을 약 20배(8.9 s → 0.44 s) 빠르게 할 수 있습니다.")

    s = D.content("왜 경로를, 왜 드론 쪽에서 결정하는가")
    section(s, SEC[1])
    picture(s, FIG / "F24_why_path.png", X0, 1.1, W, 3.6)
    stat(s, X0, 4.85, 3.95, 1.45, "조절 가능한 것은 경로·고도·속도뿐", "102.9 → 76.7 %p", "목적지·화물·기체·바람은 주어짐")
    stat(s, X0 + 4.1, 4.85, 3.95, 1.45, "원격 판단만, 결정 지연 20 s / 30 s", "침범 38 % / 88 %", "지연 3 s 이하이면 0/16",
         dark=True)
    stat(s, X0 + 8.2, 4.85, 3.95, 1.45, "기체 쪽 계산 가능성", "7 ms · 0.8 s", "남은 경로 검사 · 재계획")
    line(s, 6.42, [n("바람은 바꿀 수 없지만 "), b("진행 방향과 고도를 바꾸면 바람의 작용이 달라집니다."),
                   n(" 결정이 늦으면 다른 비행체를 피할 수 없습니다.")])
    source(s, "E12: 실측 항적 침입기 16 조우, 조우까지 25 s · 계산 시간은 서버급 CPU 기준")
    notes(s, "두 가지 질문에 답합니다. 첫째, 왜 경로인가. 주문이 정해지면 목적지와 화물 무게, 기체와 배터리는 고정이고 바람은 "
             "환경입니다. 바꿀 수 있는 것은 수평 경로, 고도, 구간 속도, 대기뿐입니다. 둘째, 왜 드론 쪽에서 결정하는가. 가운데 "
             "그림은 탐지부터 새 경로 적용까지의 지연을 바꿔 본 결과입니다. 지연이 3초 이하이면 침범이 없지만, 원격 판단만 쓰면 "
             "20초에서 38 %, 30초에서 88 %가 침범합니다. 오른쪽 그림은 정직하게 밝힐 부분입니다. 이날은 풍향이 일정해서, 오래된 "
             "바람 정보로 만든 경로의 평균 손해는 거의 없었습니다(최대 7.9 %). 따라서 비행 중 결정이 필요한 주된 이유는 바람의 "
             "변화가 아니라 장애물과 결정 지연입니다. 표본 16회라 신뢰구간이 넓습니다.")

    s = D.content("시공간 계획과 기회 제약")
    section(s, SEC[1])
    card(s, X0, 1.25, 3.95, 3.05, "SAFE INTERVAL", "안전구간 탐색 (SIPP)",
         ["상태 = (셀, 안전구간). 셀마다 이동 장애물이 없는 시간구간을 미리 구해, 시간축을 잘게 나누지 않고 기다림까지 다룹니다.",
          [("Phillips & Likhachev 2011 · Yakovlev & Andreychuk 2017", {"size": 11, "bold": False, "color": GRAY})]])
    card(s, X0 + 4.1, 1.25, 3.95, 3.05, "CHANCE CONSTRAINT", "기회 제약",
         ["P(충돌) ≤ ε. 2차원 가우시안 위치 오차라면 장애물을 κ_ε·σ만큼 부풀리면 충분합니다.",
          [("κ_ε = √(2 ln(1/ε))", {"size": 15, "bold": True, "color": NAVY})],
          [("Blackmore et al. 2011 · Du Toit & Burdick 2012 · Zhu & Alonso-Mora 2019", {"size": 11, "bold": False, "color": GRAY})]])
    card(s, X0 + 8.2, 1.25, 3.95, 3.05, "THE HARD PART", "둘을 결합하려면",
         ["예측 오차는 앞을 볼수록 커집니다. 커지는 팽창 반경을 쓰면서도 안전구간을 표본 없이 빠르게 구해야 수 km 임무 전체를 "
          "에너지로 탐색할 수 있습니다."], fill=LIGHT, size=15)
    table(s, X0, 4.7, W, [["위험 한도 ε", "0.2", "0.1", "0.05", "0.01"], ["팽창 계수 κ_ε", "1.79", "2.15", "2.45", "3.03"]],
          [3, 2.3, 2.3, 2.3, 2.3], row_h=0.7, head_size=16, body_size=16, aligns=[PP_ALIGN.CENTER] * 5)
    notes(s, "시공간 계획의 표준 도구는 SIPP입니다. 기회 제약은 충돌 확률을 ε 이하로 묶는 방식이고, 가우시안 오차라면 반경을 κ_ε σ"
             "만큼 늘리는 것으로 바뀝니다. 문제는 이 둘을 결합할 때입니다. 예측 오차가 시간에 따라 커지므로 팽창 반경도 커져야 "
             "하는데, 이때 안전구간을 표본 추출로 구하면 탐색이 느려집니다. 제안 방법은 이 지점을 해석적으로 풉니다.")

    # ================================================================= 03 Related Works
    s = D.content("세 갈래의 선행 연구")
    section(s, SEC[2])
    rows = [["연구", "바람·에너지", "이동 장애물", "불확실성", "any-angle", "재계획"],
            ["Betti Sorbelli et al. 2021 (T-ITS)", "그래프, 탑재중량", "–", "–", "–", "–"],
            ["Lian et al. 2025 WA-LPA* (Drones)", "동적 바람장", "원문 확인", "–", "격자", "증분 (바람)"],
            ["Lian et al. 2025 (ITSC)", "바람장 A*", "–", "–", "격자", "–"],
            ["Gu et al. 2026 (CJA)", "CFD 바람장", "–", "–", "Theta*", "–"],
            ["Phillips & Likhachev 2011 SIPP", "–", "알려진 궤적", "–", "–", "–"],
            ["Yakovlev & Andreychuk 2017", "–", "알려진 궤적", "–", "AA-SIPP", "–"],
            ["Blackmore et al. 2011 (T-RO)", "–", "– (정적)", "기회 제약", "연속", "–"],
            ["Du Toit & Burdick 2012 (T-RO)", "–", "다룸", "기회 제약", "연속", "RHC"],
            ["Zhu & Alonso-Mora 2019 (RA-L)", "–", "다룸", "기회 제약", "연속", "MPC"],
            [[("본 연구", {"color": ORANGE})], "3D·시간가변, 속도", "다룸", "실측 보정 튜브", "에너지 비교", "비동기 실시간"]]
    table(s, X0, 1.15, W, rows, [3.6, 1.9, 1.6, 1.75, 1.5, 1.8], row_h=0.5, head_size=13, body_size=12.5, highlight=(10,),
          first_col_center=False, aligns=[PP_ALIGN.LEFT] + [PP_ALIGN.CENTER] * 5)
    source(s, "\"–\" 다루지 않음 · \"원문 확인\" 초록으로 확인하지 못함")
    notes(s, "관련 연구는 세 갈래입니다. 위 네 편은 바람과 에너지를 고려한 경로계획, 가운데 두 편은 궤적이 알려진 이동 장애물을 "
             "다루는 SIPP 계열, 아래 세 편은 기회 제약 계획으로 주로 연속 최적화나 짧은 지평의 MPC입니다. '원문 확인'은 초록만으로 "
             "판단할 수 없었던 항목이라 투고 전에 원문을 읽고 확정해야 합니다.")

    s = D.content("남은 공백과 본 연구의 위치")
    section(s, SEC[2])
    gaps = [("공백 1", "에너지와 시공간이 분리", "바람 에너지 계획은 이동 장애물을, SIPP는 에너지·속도를 다루지 않습니다.",
             "WE-SIPP-Theta*", "하나의 탐색에서 에너지·속도·대기·안전구간을 함께"),
            ("공백 2", "불확실성은 짧은 지평에서만", "기회 제약 계획은 주로 연속 최적화·MPC로, 수 km 임무를 그래프 탐색하지 않습니다.",
             "해석적 위험구간", "시간 증가 튜브를 안전구간표로 정확히 변환"),
            ("공백 3", "가정된 오차, 검증 안 된 위험", "위험 한도는 대개 가우시안 가정 아래서만 평가되고, 실제 운용 지연은 빠집니다.",
             "실측 ADS-B 보정 + 실시간 운용", "데이터 팽창 모양과 비동기 재계획")]
    for i, (tag, t, d, a, ad) in enumerate(gaps):
        x = X0 + i * 4.1
        card(s, x, 1.25, 3.95, 2.35, tag, t, d, size=15)
        sp = box(s, x, 3.8, 3.95, 1.7, fill=NAVY, shadow=False)
        box_text(sp, [[("→ " + a, {"size": 15, "bold": True, "color": ORANGE})],
                      [(ad, {"size": 14, "bold": False, "color": WHITE})]])
    line(s, 5.85, [n("확인한 문헌 범위에서는 세 흐름을 결합한 사례를 찾지 못했습니다. 2024–2026 문헌은 투고 전 추가 조사가 필요합니다.")],
         size=14)
    notes(s, "공백을 세 가지로 정리합니다. 에너지 계획과 시공간 계획이 분리되어 있고, 불확실성을 다루는 계획은 짧은 지평이 "
             "중심이며, 위험 한도가 가정된 분포에서만 평가됩니다. '처음'이라는 표현은 쓰지 않고 '확인한 문헌 범위에서는'으로 "
             "한정합니다.")

    # ================================================================= 04 Proposed Method
    s = D.content("문제 정식화 (P)")
    section(s, SEC[3])
    sp = box(s, X0, 1.2, W, 1.55, fill=NAVY, shadow=False)
    box_text(sp, [[("min  over 경로 π = (n₀ … n_K), 구간 대지속도 vₖ, 대기 wₖ", {"size": 13, "bold": False, "color": WHITE})],
                  [("ΔSOC = 100 / E_b · [ Σₖ P(Vₐ,ₖ, V_z,ₖ, m) · dₖ / vₖ + Σₖ P호버 · wₖ + E이륙 + E착륙 ]",
                    {"size": 17, "bold": True, "color": WHITE})]])
    head(s, X0, 2.95, "제약")
    items(s, X0, 3.42, W, 3.4, [
        ("바람  ", "Vₐ,ₖ = ‖vₖuₖ − W(xₖ, tₖ)‖ ≤ Vₐ,max  (3D·시간가변 바람장)"),
        ("공역  ", "지면+30–150 m, 지형·건물·금지구역 비점유"),
        ("배터리  ", "누적 에너지 ≤ E_b (1 − 예비 SOC)"),
        ("질량  ", "구간별 m (배송: 갈 때 적재 · 회수: 올 때 적재)"),
        ("충돌 기회 제약  ", "모든 장애물 j, 모든 시각 t에서 P( ‖x(t) − p_j(t)‖ ≤ R_j ) ≤ ε  (p_j는 예측만 가능, 오차는 예측 "
                       "시간에 따라 증가)"),
    ], size=14, gap=7)
    notes(s, "목적은 SOC 소모 최소화이고, 결정 변수는 경로, 구간별 대지속도, 대기 시간입니다. 제약은 다섯 가지입니다. 배경에서 본 "
             "대로 바람은 비용에, 주변 비행체는 충돌 기회 제약에, 탑재중량은 구간별 질량으로 들어갑니다.")

    s = D.content("WE-SIPP-Theta*: 네 가지 구성 요소")
    section(s, SEC[3])
    comp = [("①", "구간 속도 내부 최적화", "각 간선에서 e* = min_v P(Vₐ, V_z, m)·d / v. 바람이 세면 빨리, 약하면 효율 속도로 "
                                     "납니다. 속도가 경로와 함께 결정됩니다."),
            ("②", "에너지 비교형 지름길", "Lazy Theta*의 부모 직결을 시야가 아니라 에너지로 판정합니다. 비볼록 비용 노름에서도 손해 "
                                   "보는 직결을 막습니다."),
            ("③", "(셀, 안전구간) 상태와 대기", "다음 안전구간 진입 시각까지 호버·이륙 지연을 허용합니다. '기다릴지, 돌아갈지'를 "
                                       "에너지로 고릅니다."),
            ("④", "방위별 휴리스틱·예산 가지치기", "h = e_min(방위)·거리 (바람 표본 전체 최소 × 0.97). 배터리 예산을 넘는 상태는 "
                                        "버립니다.")]
    for i, (no, t, d) in enumerate(comp):
        x, y = X0 + (i % 2) * 6.15, 1.25 + (i // 2) * 2.2
        card(s, x, y, 6.0, 2.0, None, f"{no}  {t}", d, size=15)
    for i, (k, v) in enumerate([("탐색 공간", "3D 26방향 격자 · 25 m 셀"), ("강풍일 왕복 계획", "7.9 s"),
                                ("고도별 선형 바람 조회표", "0.44 s")]):
        sp = box(s, X0 + i * 4.1, 5.75, 3.95, 1.0, fill=LIGHT, shadow=False)
        box_text(sp, [[(k, {"size": 12, "bold": False, "color": GRAY})], [(v, {"size": 15, "bold": True, "color": NAVY})]],
                 align=PP_ALIGN.CENTER)
    notes(s, "탐색기는 네 요소로 구성됩니다. ①은 간선마다 에너지를 최소화하는 대지속도를 내부에서 고르는 것으로, 절감에 가장 크게 "
             "기여합니다. ②는 비볼록성 때문에 직결을 에너지로 판정하는 것입니다. ③은 SIPP의 상태 구조로, 기다리는 선택이 탐색 "
             "안에 들어갑니다. ④의 휴리스틱은 표본 최소에 0.97을 곱한 근사라, 시간가변 비용에서 최적을 보장하지는 않습니다.")

    s = D.content("시간 증가 튜브의 위험구간을 해석해로")
    section(s, SEC[3])
    head(s, X0, 1.2, "팽창 반경", w=5.4)
    textbox(s, X0, 1.68, 5.6, 1.1, [[b("ρ(τ) = R + 이격 + 셀 + r(τ)")],
                                    [n("가우시안: r(τ) = κ_ε σ(τ),  σ(τ) = σ₀ + σ_v τ")]], size=15, space_after=5)
    head(s, 6.5, 1.2, "셀이 튜브 안에 드는 조건", w=6.25)
    sp = box(s, 6.5, 1.68, 6.25, 1.2, fill=NAVY, shadow=False)
    box_text(sp, [[("셀 c, 장애물 p(τ) = p₀ + vτ", {"size": 12, "bold": False, "color": WHITE})],
                  [("‖c − p₀ − vτ‖ ≤ ρ₀ + gτ  ⇔  aτ² + bτ + c′ ≤ 0", {"size": 15, "bold": True, "color": WHITE})],
                  [("a = ‖v‖² − g²", {"size": 15, "bold": True, "color": ORANGE})]])
    picture(s, FIG / "F18_robust_example.png", X0, 3.05, 8.0, 3.75)
    items(s, 8.8, 3.1, 3.95, 3.75, [
        ("a > 0 ", "→ 위험구간 1개"),
        ("a < 0 ", "(튜브가 장애물보다 빨리 커짐) → 앞뒤 2개로 분리"),
        ("r(τ)가 구간별 선형 ", "→ 꺾임점마다 같은 식"),
        ("표본 추출 없이 ", "셀별 위험 시간구간이 나와 SIPP 안전구간표에 들어갑니다."),
    ], size=14, gap=10)
    source(s, "해석해가 전수 검사와 일치함을 단위 시험으로 확인 · 그림: 튜브 예시와 예측 시간별 반경(튜브 vs 고정 버퍼)")
    notes(s, "핵심 아이디어입니다. 장애물 반경에 시간에 따라 커지는 불확실성 항 r(τ)를 더합니다. 장애물이 등속이고 r이 선형이면, "
             "셀 중심이 튜브 안에 드는 조건은 τ에 대한 2차 부등식이 됩니다. a가 음수, 즉 튜브가 장애물보다 빨리 커지면 위험구간이 "
             "앞뒤 두 개로 갈라집니다. r이 구간별 선형이면 꺾임점마다 같은 식을 풀면 되므로, 데이터 기반 모양도 해석해를 "
             "유지합니다.")

    s = D.content("실측 항적으로 맞춘 팽창 모양")
    section(s, SEC[3])
    card(s, X0, 1.25, 3.95, 3.2, "DATA", "OpenSky ADS-B 실측 항적",
         ["traffic 패키지 샘플 중 1 Hz 저고도 비항공사 기체 18대 (응급 헬기·일반 항공), 관측 3,186개",
          "예측기: 관측 순간 ADS-B 보고 속도로 등속 외삽"], size=15)
    card(s, X0 + 4.1, 1.25, 3.95, 3.2, "PROFILE", "데이터 분위수 튜브",
         [[("r_ε(τ) = V · q̂₁₋ε( ‖e(τ)‖ / V )", {"size": 14, "bold": True, "color": NAVY})],
          "속도 정규화 오차의 분위수, 꺾임점 0·5·10·20·30·45·60 s, 단조 보정. 분포 가정 없이 시점별 초과확률 ε"], size=15)
    card(s, X0 + 8.2, 1.25, 3.95, 3.2, "CLOSED LOOP", "반응 시간 하한",
         [[("r^F(τ) = max( r_ε(τ), r_ε(T_r) )", {"size": 14, "bold": True, "color": NAVY})],
          "T_r = 감시 주기 + 지연 + 계산 ≈ 10 s. 다음 관측·재계획 전까지 쌓이는 오차만큼은 늘 확보"], fill=LIGHT, size=15)
    sp = box(s, X0, 4.75, W, 1.2, fill=NAVY, shadow=False)
    box_text(sp, [[("검증 방법", {"size": 13, "bold": True, "color": ORANGE})],
                  [("10분 블록(기체·시간) 단위 2분할을 20회 반복했습니다. 같은 비행이 학습과 시험에 섞이지 않습니다.",
                    {"size": 15, "bold": False, "color": WHITE})]])
    source(s, "Olive 2019 (traffic) · Schäfer et al. 2014 (OpenSky)")
    notes(s, "가우시안 가정이 실제로 맞는지 확인하려고 실측 항적을 썼습니다. 관측 순간의 보고 속도로 등속 외삽했을 때의 오차를 "
             "쟀습니다. 팽창 모양은 오차를 속도로 나눈 값의 분위수 곡선을 꺾임점으로 적합한 것입니다. 폐루프에서는 반응 시간 동안 "
             "쌓이는 오차만큼을 하한으로 두는 변형도 제안합니다.")

    s = D.content("실시간 운용: 계산하는 동안에도 비행은 계속")
    section(s, SEC[3])
    card(s, X0, 1.25, 3.0, 3.1, "입력", "비행 중 들어오는 정보", ["장애물 추가·삭제", "실측 ADS-B 침입기", "외부 감시 보고 (REST)"], size=14)
    card(s, X0 + 3.2, 1.25, 4.2, 3.1, "감시 스레드", "남은 경로 검사 (약 7 ms)",
         ["예측 + 팽창 튜브 → 위험구간표 → 남은 경로 검사",
          [("충돌까지 여유 < 재계획 소요 → 비상 정지(호버)", {"size": 14, "bold": True, "color": ORANGE})]], size=14)
    card(s, X0 + 7.6, 1.25, 4.55, 3.1, "재계획·개선 스레드", "새 경로를 바꿔 끼우는 시각",
         ["T_sw = 지금 + 지연 + 1.5 × 계산 × 배속", "완화 단계 ε → 0.2 → 팽창 없음, 늦으면 폐기·재시도",
          "빠른 계획 후 정밀 탐색으로 개선 (anytime)"], fill=LIGHT, size=14)
    foot = [("시계 20 Hz · 화면 10 Hz", "탐색 함수를 GIL 해제(nogil)로 컴파일해 계산이 시계를 멈추지 않음"),
            ("지상 출발 대기", "구간 계획이 준비된 뒤 이륙. 공중 호버로 배터리를 쓰지 않음"),
            ("비상 착륙·중단", "예비 SOC 도달 또는 4회 연속 실패 시 착륙")]
    for i, (k, v) in enumerate(foot):
        sp = box(s, X0 + i * 4.1, 4.6, 3.95, 1.75, fill=BOX, shadow=False)
        box_text(sp, [[(k, {"size": 14, "bold": True, "color": NAVY})], [(v, {"size": 14, "bold": False, "color": INK})]])
    notes(s, "알고리즘을 실제 운용에 옮길 때의 문제는 계산 지연입니다. 계산하는 동안에도 드론은 날아가고 있으므로, 새 경로를 바꿔 "
             "끼울 시각을 미리 정합니다. 계산이 그보다 늦게 끝나면 결과를 버리고 다시 계산합니다. 충돌까지 남은 시간이 재계획에 "
             "필요한 시간보다 짧으면 그 자리에서 멈춰 호버합니다.")

    if X6:
        xd, xs, xc = X6["drone"], X6["spec"], X6["curves"]["x650"]
        s = D.content("실증 대상 기체 Holybro X650 – 제원과 전력 모델")
        section(s, SEC[3])
        head(s, X0, 1.15, "첨부 제원 (실증 기체 3대 동일 구성)", w=6.0)
        rows = [["구분", "내용"], ["비행제어", "Pixhawk 6X + ArduCopter (AUTO·RTL·Geofence)"],
                ["추진", "MN4014 KV330 × 4 · Tekko32 45A · 15×5.5 in 프로펠러"],
                ["전원", "LiPo 6S 10,000 mAh (222 Wh) · PM02D 전압·전류 측정"],
                ["측위", "H-RTK F9P (RTK 8 Hz)"],
                ["중량", f"자체 {xs['empty_kg']} kg · 최대이륙 {xs['mtow_kg']} kg → 탑재 최대 {X6['payload_max']:g} kg"],
                ["호버 시간", "무탑재 약 30분 (제조사)"]]
        table(s, X0, 1.62, 6.0, rows, [1.25, 4.75], row_h=0.5, head_size=13, body_size=12.5,
              aligns=[PP_ALIGN.CENTER, PP_ALIGN.LEFT])
        head(s, 6.95, 1.15, "전력 모델: 제원으로 정한 값과 가정한 값", w=5.8)
        rows = [["계수", "값", "출처"], ["질량·회전면", f"{xd['m_empty']} kg · {xd['area']:.3f} m²", "제원"],
                ["상승 / 하강률", f"{xd['vz_up']} / {xd['vz_dn']} m/s", "ArduCopter 기본값"],
                ["종합 효율 η", f"{xd['eta']:.3f}", "호버 30분에 맞춤"],
                ["형상동력 · 항력면적", f"{xd['p0']:.0f} W · {xd['cda']} m²", "가정"],
                ["속도 상한", f"대기 {xd['va_max']:.0f} · 대지 {xd['vg_max']:.0f} m/s", "가정"]]
        table(s, 6.95, 1.62, 5.8, rows, [1.95, 2.0, 1.85], row_h=0.5, head_size=13, body_size=12,
              highlight=(3,), aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER], first_col_center=False)
        stat(s, X0, 5.3, 3.95, 1.45, "호버 전력 (탑재 0 / 1 / 2.2 kg)",
             f"{xc['hover_W']['0.0']:.0f} / {xc['hover_W']['1.0']:.0f} / {xc['hover_W']['2.2']:.0f} W",
             f"호버 시간 {xc['hover_min']['0.0']:.0f} / {xc['hover_min']['1.0']:.0f} / {xc['hover_min']['2.2']:.0f}분", vsize=20)
        sv = [v["soc"] for v in X6["sensitivity"].values()]
        stat(s, X0 + 4.1, 5.3, 3.95, 1.45, "가정에 따른 왕복 SOC 범위", f"{min(sv):.1f}–{max(sv):.1f} %p",
             f"기준 {sv[0]:.1f} %p (탑재 1 kg)", dark=True, vsize=24)
        items(s, X0 + 8.25, 5.3, 3.9, 1.6, [("비행 자료로 보정하지 않은 추정", "입니다."),
                                           ("호버 시간·항력면적", " 가정이 가장 크게 작용합니다.")], size=13, gap=6)
        source(s, "출처: 실증 드론 부품 구성표·초경량비행장치 제원 및 성능표 (첨부) · ArduPilot AC_WPNav.cpp")
        notes(s, "실증 대상 기체는 DJI가 아니라 Holybro X650이고, Pixhawk 6X와 ArduCopter로 비행합니다. 배터리는 222 Wh로 M100의 "
                 "2.2배입니다. 이 기체의 비행 자료가 아직 없어 전력 모델은 추정입니다. 질량, 회전면 면적, 배터리는 제원에서 "
                 "가져왔고, 종합 효율은 제조사가 밝힌 무탑재 호버 30분에 맞췄습니다. 형상동력과 항력면적은 가정입니다. 가정을 "
                 f"바꾸면 같은 임무의 왕복 SOC가 {min(sv):.1f}에서 {max(sv):.1f} %p까지 달라지므로, 절댓값은 참고용이고 비행 "
                 "기록으로 보정해야 합니다. 두 첨부 문서의 무게 표기가 달라(프레임 2.0 kg, 자체무게 4.1 kg) 4.1 kg을 무탑재 이륙 "
                 "중량으로 보았습니다.")

        s = D.content("3D 지도에서 경로 생성 → ArduPilot 미션")
        section(s, SEC[3])
        picture(s, FIG / "platform_3d.png", X0, 1.1, 8.3, 5.75)
        head(s, 9.1, 1.15, "3D 지도 화면 (/3d)", w=3.7)
        items(s, 9.1, 1.6, 3.65, 2.5, ["지형·건물 7천여 동·금지구역·바람", "지도를 눌러 출발·도착 지정",
                                       "경로, 고도 단면, 시간 재생", "기체 선택 (X650 / M100)"], size=13, gap=5)
        head(s, 9.1, 3.75, "미션 내보내기", w=3.7)
        ex = X6["mission_export"]
        items(s, 9.1, 4.2, 3.65, 2.7, [
            "QGC WPL 110 (.waypoints) + 경로표",
            "이륙·경로점·속도 변경·대기·착륙",
            "고도는 출발지 지면 기준 상대 고도",
            f"예: 가는 편 {ex['out']['n_items']}개, 오는 편 {ex['back']['n_items']}개 항목",
            "시간·SOC가 시뮬레이션과 일치"], size=13, gap=5)
        source(s, "pymavlink 미션 로더로 형식 확인 · SITL·실기체 비행은 하지 않음")
        notes(s, "계획기는 처음부터 3D 격자에서 경로를 찾았습니다. 이번에 더한 것은 그 3D 공간을 그대로 보면서 임무를 정하는 "
                 "화면입니다. 지형과 건물, 비행금지구역, 고도별 바람을 3D로 보고, 지도를 눌러 출발지와 도착지를 정하면 경로와 "
                 "고도 단면이 나옵니다. 만든 경로는 ArduCopter의 AUTO 모드가 읽는 미션 파일로 내보냅니다. 이륙, 경로점, 구간 "
                 "대지속도, 호버 대기, 착륙 명령으로 바꾸며, 고도는 출발지 지면 기준 상대 고도입니다. 미션의 예상 시간과 SOC는 "
                 "전체 시뮬레이션과 일치하고 pymavlink 로더가 파일을 읽는 것까지 확인했습니다. 모의 비행(SITL)과 실기체 "
                 "비행은 아직 하지 않았습니다. 이 기체는 주변 비행체를 스스로 탐지하지 못하므로, 동적 장애물 정보는 지상에서 "
                 "받아 넣어야 합니다.")

    # ================================================================= 05 Results
    s = D.content("실험 설정: 실측 데이터만 사용")
    section(s, SEC[4])
    rows = [["항목", "설정"],
            ["지역", "성남 판교·분당 6.2×5.5 km, Copernicus GLO-30 DSM, Overture 건물 7,109동, 25 m 격자"],
            ["바람", "NOAA GFS 0.25°, 고도별·시간별, 2026-10-27 강풍일 (00Z로 계획, 06Z를 실제로 사용)"],
            ["기체", "DJI M100 전력 모델 (CMU 실측 198회 보정), 99.9 Wh, 탑재 0.5 kg"],
            ["임무", "거점↔배송지 직선 2.4 km 왕복, 배송·회수"],
            ["장애물", "합성 시나리오 30 × 1,000 샘플 (E10), 실측 ADS-B 항적 400 조우 (E11)"],
            ["통계", "Wilson 95 % CI, 부트스트랩, 짝지은 Wilcoxon·McNemar"]]
    table(s, X0, 1.2, 6.3, rows, [1.0, 5.3], row_h=0.78, head_size=13, body_size=12.5, aligns=[PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    picture(s, FIG / "F10_3d_paths.png", 7.05, 1.15, 5.7, 5.6)
    source(s, "바람의 '실제'는 관측이 아니라 갱신 예보 · 금지구역 원은 예시")
    notes(s, "모든 실험은 실제 데이터 위에서 했습니다. 00시 예보로 계획하고, 06시 갱신 예보를 실제 바람으로 간주해 평가했습니다. "
             "관측 바람이 아니라는 점은 한계입니다. 판교 일대는 실제로는 서울공항 관제권이라 비행승인이 필요합니다.")

    s = D.content("RQ1 · 강풍일 왕복 SOC: 비행 불가 → 예비 확보")
    section(s, SEC[4])
    picture(s, FIG / "F5_soc_methods.png", X0, 1.1, W, 3.6)
    stat(s, X0, 4.9, 3.3, 1.75, "예비 SOC 20 % 충족 (24회 중)", "0 → 23회", "기존 방식 → WE-LT*", dark=True, vsize=30)
    for i, (k, v, d) in enumerate([("최단 + 정속 10 m/s", "102.9 %p", "배터리 부족"), ("최단 + 속도 최적화", "86.1 %p", "예비 부족"),
                                   ("에너지 A* (26방향)", "80.8 %p", "경로점 199개"), ("제안 WE-LT*", "76.7 %p", "회수 78.5 %p")]):
        stat(s, X0 + 3.5 + i * 2.18, 4.9, 2.05, 1.75, k, v, d, vsize=20)
    source(s, "E1–E2 · 15시 출발, 2.4 km 왕복, 0.5 kg · 출발 시각 09–20시 배송·회수 24회")
    notes(s, "가장 기본적인 결과입니다. 최단 경로를 10 m/s 정속으로 날면 SOC 102.9 %p가 들어 비행할 수 없습니다. 같은 경로에서 "
             "속도만 최적화해도 86.1 %p로 줄고, 에너지 A*는 80.8 %p, 제안 방법은 76.7 %p입니다. 24회를 비교하면 예비 SOC 20 %를 "
             "지킨 경우가 기존 방식 0회, 제안 방법 23회였습니다.")

    s = D.content("RQ1 · 무엇이 절감을 만드는가 (가정 축소 실험)")
    section(s, SEC[4])
    picture(s, FIG / "F11_ablation.png", X0, 1.2, 7.7, 5.4)
    head(s, 8.45, 1.2, "빼면 늘어나는 SOC", w=4.3)
    table(s, 8.45, 1.7, 4.3, [["구성 요소", "증가 (%p)"], ["구간 속도 최적화", "+10.2–13.0"], ["에너지 비교 지름길", "+4.1"],
                              ["3D 고도 선택", "+2.8–3.1"], ["시간가변 바람", "≤ +0.3"], ["구간별 질량", "≤ +0.2"]],
          [2.5, 1.8], row_h=0.55, head_size=13, body_size=13, highlight=(1,), aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER],
          first_col_center=False)
    textbox(s, 8.45, 5.2, 4.3, 1.3, [[n("경로 모양보다 "), b("속도 결정"), n("을 먼저 넣는 것이 효과적입니다.")]], size=14)
    source(s, "E6 가정 축소 · 배송/회수 왕복 기준")
    notes(s, "RQ1의 답입니다. 구성 요소를 하나씩 빼고 SOC가 얼마나 늘어나는지 쟀습니다. 구간 속도 최적화가 가장 커서, 10 m/s로 "
             "고정하면 10–13 %p가 늘고 비행이 불가능해집니다. 그다음이 에너지 비교 지름길 4.1 %p, 고도 선택 3 %p 안팎입니다.")

    s = D.content("이동 장애물과 비행 중 변경")
    section(s, SEC[4])
    picture(s, FIG / "F14_dynamic_obstacles.png", X0, 1.15, 6.0, 3.95)
    picture(s, FIG / "F15_obstacle_replanning.png", X0 + 6.15, 1.15, 6.0, 3.95)
    items(s, X0, 5.3, W, 1.6, [
        ("E8  ", "무시하면 3종 모두 충돌(최대 136 m 침범) → 시공간 계획은 최소 이격 29 m, 에너지 +9.5 %, 계산 2.8 s"),
        ("E9  ", "1회 계획은 충돌, 이벤트 기반은 재계획 2회로 회피 (30 s 주기는 5회 필요, 60 s 주기는 충돌)"),
    ], size=14, gap=8)
    source(s, "E8 동적 장애물(다른 드론·응급 헬기·행사 제한구역) · E9 비행 중 변경 발표 시 재계획")
    notes(s, "궤적이 알려진 이동 장애물부터 봅니다. 장애물을 무시한 경로는 셋 모두와 충돌하고, 시공간 계획은 최소 29 m 이격으로 "
             "피하며 에너지는 9.5 % 더 듭니다. E9에서는 변경이 발표될 때만 검사하고 충돌이 예상되면 재계획하는 이벤트 기반 방식이 "
             "2회 재계획으로 안전했습니다.")

    s = D.content("RQ2 · 같은 안전성, 4.9 % 적은 에너지")
    section(s, SEC[4])
    picture(s, FIG / "F16_robust_mc.png", X0, 1.1, W, 3.55)
    stat(s, X0, 4.8, 3.95, 1.45, "공칭 (불확실성 무시)", "16.4 %", "충돌률 (95 % CI 16.0–16.9)")
    stat(s, X0 + 4.1, 4.8, 3.95, 1.45, "고정 버퍼", "0 / 30,000", "에너지 +17.1 % (공칭 대비)")
    stat(s, X0 + 8.2, 4.8, 3.95, 1.45, "시간 증가 튜브 ε = 0.05", "0 / 30,000", "에너지 +11.5 % (공칭 대비)", dark=True)
    line(s, 6.4, [n("짝 비교 "), b("−4.9 % [−6.0, −3.7]"), n(" · 30개 중 28개 시나리오 우세 · Wilcoxon p = 1.6×10⁻⁷")])
    source(s, "E10 · 30 시나리오 × 1,000 샘플, σ₀ = 10 m, σ_v = 0.5 m/s, σ_t = 2 s (가우시안 오차 가정)")
    notes(s, "RQ2입니다. 예측을 그대로 믿으면 16.4 %가 충돌합니다. 고정 최악 버퍼와 시간 증가 튜브는 둘 다 3만 회 중 충돌이 "
             "0이었습니다. 에너지는 튜브가 4.9 % 적게 들었습니다. 단, 이 결과는 오차가 가우시안이라는 가정 아래의 결과이며, 다음 "
             "장에서 그 가정을 깹니다.")

    s = D.content("RQ2 · 꼬리가 두꺼우면 이득이 사라진다")
    section(s, SEC[4])
    picture(s, FIG / "F19_frontier_mismatch.png", X0, 1.2, 7.7, 5.4)
    card(s, 8.5, 1.2, 4.25, 1.55, None, "가우시안 오차", "튜브 곡선이 고정 버퍼보다 아래에 있습니다. 튜브가 우월합니다.", size=13)
    card(s, 8.5, 2.9, 4.25, 2.0, None, "Student-t 오차 (ν = 3)", "같은 위험에서 에너지 +1.2 % (p = 0.27, 유의차 없음). 최저 위험 "
                                                           "영역은 고정 버퍼가 우세합니다.", size=13, fill=LIGHT)
    card(s, 8.5, 5.05, 4.25, 1.75, None, "왜", "꼬리가 두꺼울수록 완만한 팽창 모양이 최적입니다. 크기 ε뿐 아니라 모양을 오차 꼬리에 "
                                          "맞춰야 합니다.", size=13)
    source(s, "E10c·E10d · 29 시나리오 × 1,000 샘플, 같은 표준편차의 Student-t 오차")
    notes(s, "계획은 가우시안을 가정한 채로, 실제 오차만 같은 표준편차의 Student-t 분포로 바꿔 평가했습니다. 가우시안에서는 튜브가 "
             "우월했지만, 꼬리가 두꺼우면 같은 위험에서 에너지 차이가 유의하지 않았습니다. 결론은 팽창의 모양을 실제 오차 꼬리에 "
             "맞춰야 한다는 것이고, 그래서 실측 항적으로 갔습니다.")

    s = D.content("RQ3 · 실측 예측 오차: 두꺼운 꼬리, 가속 성장")
    section(s, SEC[4])
    picture(s, FIG / "F20_adsb_error_tail.png", X0, 1.1, W, 3.75)
    stat(s, X0, 5.0, 3.95, 1.5, "99.9 % 분위수 (표준화)", "9.1σ", "가우시안 3.7σ · 첨도 12", dark=True)
    stat(s, X0 + 4.1, 5.0, 3.95, 1.5, "오차 분위수 성장", "∝ τ^1.5", "선형 가정과 다른 모양")
    stat(s, X0 + 8.2, 5.0, 3.95, 1.5, "초과확률 (ε = 0.05 / 0.01)", "5.1 % / 1.1 %", "데이터 튜브 · 가우시안 7.5 % / 4.2 %")
    source(s, "OpenSky ADS-B 1 Hz, 저고도 비항공사 18대, 3,186 관측 · 10분 블록 2분할 × 20회 · 시연용 샘플(무작위 아님)")
    notes(s, "실측 항적의 등속 예측 오차입니다. 분위수가 예측 시간의 약 1.5제곱으로 커집니다. 꼬리가 가우시안보다 훨씬 두꺼워, "
             "99.9 % 분위수가 9.1σ입니다. 데이터 튜브는 학습에 쓰지 않은 블록에서 모든 예측 시간에 ε 부근을 유지했습니다. "
             "가우시안 튜브는 20초 이전에는 과하게 덮고, 그 뒤로는 부족했습니다.")

    s = D.content("RQ3 · 폐루프: 시점별 보정 ≠ 폐루프 안전")
    section(s, SEC[4])
    picture(s, FIG / "F21_adsb_closed_loop.png", X0, 1.1, W, 3.75)
    stat(s, X0, 5.0, 3.95, 1.5, "회피 불가 조우 (모든 방법 충돌)", "4.5 %", "회피 가능 382회: 공칭 7 · 팽창 2–4")
    stat(s, X0 + 4.1, 5.0, 3.95, 1.5, "최소 이격 중앙값", "45 → 107–184 m", "공칭 → 팽창한 방법들", dark=True)
    stat(s, X0 + 8.2, 5.0, 3.95, 1.5, "데이터 튜브 에너지 (가우시안 대비)", "+2.1 %", "반응 시간 하한으로 −0.6 %")
    source(s, "E11 · 실측 항적(8–30 m/s 축소) 400 조우, 5 s 감시, 20 s 지평, 교차 적합")
    notes(s, "실측 항적을 실제 침입기 궤적으로 쓴 폐루프 시험입니다. 400회 중 18회는 어떤 방법도 피하지 못했고, 나머지 382회에서 "
             "충돌 차이는 유의하지 않았습니다. 팽창은 간격을 크게 늘렸습니다. 시점별로 정확한 데이터 튜브는 에너지가 2.1 % 더 "
             "들었습니다. 속도 축소 가정은 드론 실측 항적으로 다시 검증해야 합니다.")

    s = D.content("실시간 운용: 10배속 5회, 모두 무침범 완주")
    section(s, SEC[4])
    picture(s, FIG / "live_platform_flight.png", X0, 1.15, 7.0, 5.7)
    rows = [["지표", "결과"], ["완주 / 침범", "5/5 · 0건"], ["최소 이격", "37.5 m"], ["감시 검사 (중앙값)", "6.9 ms"],
            ["재계획 계산", "0.81 s (최대 1.62)"], ["시계 주기 p95 (목표 50)", "63 ms"], ["화면 갱신", "약 10 Hz"],
            ["비상 정지 / 재계획 / 실패", "8 / 9 / 0"]]
    table(s, 7.85, 1.2, 4.9, rows, [2.8, 2.1], row_h=0.5, head_size=13, body_size=13, highlight=(1,),
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER], first_col_center=False)
    textbox(s, 7.85, 5.4, 4.9, 1.4, ["매회 실측 ADS-B 침입기 + 경로 위 정지 장애물 + 외부 감시 보고를 동시에 투입 (20시 왕복)"],
            size=13, bold=False, color=INK)
    source(s, "실시간 벤치 · 스레드 실제 구동, 브라우저 종단 시험 포함")
    notes(s, "20시 출발 왕복을 10배속으로 5회 돌리면서, 매회 실측 ADS-B 침입기, 정지 장애물, 외부 감시 보고 항적을 동시에 "
             "넣었습니다. 다섯 번 모두 침범 없이 완주했습니다. 시계 주기 p95는 63 ms로 목표 50 ms보다 조금 깁니다.")

    if X6:
        bm, bp = X6["missions"]["by_method"], X6["missions"]["by_payload"]
        s = D.content("대상 기체 X650 결과 (추정 모델)")
        section(s, SEC[4])
        picture(s, FIG / "F26_x650_target.png", X0, 1.1, W, 3.7)
        A, Dm = bm["A_shortest_v10"], bm["D_WE_LTstar"]
        stat(s, X0, 4.95, 3.95, 1.5, "왕복 SOC 소모 (탑재 1 kg, 15시)", f"{A['soc']:.1f} → {Dm['soc']:.1f} %p",
             "최단 + 정속 10 m/s → 제안 WE-LT*", dark=True, vsize=24)
        stat(s, X0 + 4.1, 4.95, 3.95, 1.5, "비행 시간", f"{A['T']:.0f} → {Dm['T']:.0f} s",
             f"{(Dm['T'] / A['T'] - 1) * 100:+.0f} %", vsize=24)
        stat(s, X0 + 8.2, 4.95, 3.95, 1.5, "탑재 0 / 1 / 2.2 kg (제안 방법)",
             " / ".join(f"{bp[p]['D_WE_LTstar']['soc']:.1f}" for p in ("0.0", "1.0", "2.2")) + " %p",
             "모두 예비 20 % 이상", vsize=22)
        source(s, "성남 판교·분당, GFS 2026-10-27, 2.4 km 왕복 · X650 전력 모델은 미보정 추정 → 절댓값은 참고용")
        notes(s, f"대상 기체로 같은 임무를 다시 계산했습니다. 배터리가 커서 정속 최단 경로도 예비 20 %를 지키지만, 제안 방법은 "
                 f"SOC를 {A['soc']:.1f}에서 {Dm['soc']:.1f} %p로, 비행 시간을 {A['T']:.0f}초에서 {Dm['T']:.0f}초로 줄입니다. "
                 "남는 SOC는 더 먼 배송지나 더 무거운 화물에 쓸 수 있습니다. 오른쪽 그림처럼 모델 가정에 따라 절댓값이 "
                 "달라지므로, 이 수치는 비행 기록으로 보정하기 전의 추정입니다.")

    # ================================================================= 06 Conclusion
    s = D.content("결론")
    section(s, SEC[5])
    con = [("SOC와 바람을 고른 근거", f"DJI 드론이 내놓는 {DJ['n_total']}개 조건 중 외생·영향·변동·경로 의존·사전 지식을 모두 만족하는 "
                             "것은 바람뿐입니다. SOC는 임무 가능 여부의 단위입니다."),
           ("바람 속 SOC는 속도와 에너지 비교 지름길이 좌우", "강풍일 왕복 102.9 → 76.7 %p. 비행 불가에서 예비 확보로 바뀌었습니다."),
           ("구간별 선형 팽창 반경의 위험구간을 해석해로", "가우시안 오차에서 고정 버퍼와 같은 안전성을 4.9 % 적은 에너지로 얻었습니다."),
           ("실측 오차는 꼬리가 두껍고 τ^1.5로 성장", "데이터 모양이 시점별 위험을 보정하지만, 폐루프 안전은 탐지 시각과 반응 시간 "
                                           "여유가 좌우합니다."),
           ("비동기 실시간 운용", "비상 정지·즉시 계획 후 개선으로 10배속 5/5 무침범 완주했습니다.")]
    for i, (t, d) in enumerate(con):
        y = 1.2 + i * 1.13
        sp = box(s, X0, y, 0.7, 0.95, fill=NAVY, shadow=False)
        box_text(sp, [[(str(i + 1), {"size": 20, "bold": True, "color": WHITE})]], align=PP_ALIGN.CENTER)
        textbox(s, X0 + 0.9, y, 11.2, 0.95, [[(t, {"size": 16, "bold": True, "color": NAVY})],
                                            [(d, {"size": 14, "bold": False, "color": INK})]], space_after=2)
    notes(s, "다섯 가지로 정리합니다. 첫째, SOC와 바람은 임의로 고른 것이 아니라 DJI 드론이 내놓는 조건 전체를 검토해 고른 "
             "것입니다. 둘째, 바람 속 에너지 최적화에서는 속도 결정과 에너지 비교 지름길이 결정적입니다. 셋째, 팽창 반경이 구간별 "
             "선형이면 위험구간을 해석적으로 구할 수 있습니다. 넷째, 실측 오차는 꼬리와 성장 모양이 가우시안과 달랐고, 이 부정적 "
             "결과도 기여로 봅니다. 다섯째, 실시간 운용 플랫폼으로 구현해 검증했습니다.")

    s = D.content("한계와 향후 연구")
    section(s, SEC[5])
    head(s, X0, 1.2, "한계")
    items(s, X0, 1.7, 5.9, 3.6, [
        "한 지역·하루·한 기체(DJI M100)의 결과입니다.",
        "GFS 0.25°는 성글고, '실제' 바람은 관측이 아닌 갱신 예보입니다.",
        "실측 비행 자료에는 풍향이 없어 방향 효과를 직접 검증하지 못했습니다.",
        "DJI 풍속·풍향 키의 지원 기체와 정확도를 확인하지 못했습니다.",
        "ADS-B 표본 18대는 시연용 샘플입니다(무작위 아님).",
        "X650 전력 모델은 추정이며, 2D 충돌 판정에 실비행 검증이 없습니다.",
    ], size=14, gap=7)
    head(s, 6.85, 1.2, "향후 연구")
    items(s, 6.85, 1.7, 5.9, 3.6, [
        "기체의 풍속·풍향 값으로 예보 바람장을 비행 중 보정",
        "소규모 실비행으로 계획 대비 실측 SOC와 풍향 효과 검증",
        "드론 원격식별(Remote ID)·OpenSky 이력으로 항적 표본 확대",
        "감시 주기·탐지 시각·반응 하한을 함께 설계해 궤적 수준 위험 보정",
        "기상청 LDAPS·관측 바람, 다일·다지역 평가",
        "배터리 온도·노화를 넣은 SOC 모델, 다중 드론·UTM 연동",
    ], size=14, gap=7)
    sp = box(s, X0, 5.45, W, 1.35, fill=NAVY, shadow=False)
    box_text(sp, [[("가장 먼저 할 일 – 소규모 실비행", {"size": 15, "bold": True, "color": ORANGE})],
                  [("X650으로 호버·정속 왕복 비행을 하며 전압·전류, 기체 추정 바람, 지상 풍속계를 함께 기록합니다. 전력 모델 보정, 바람 "
                    "추정 정확도, 풍향 효과, 계획 대비 실측 SOC를 한 번에 확인할 수 있습니다.", {"size": 14, "bold": False, "color": WHITE})]])
    notes(s, "한계는 먼저 밝혀 두는 것이 좋습니다. 결과는 한 지역, 하루, 한 기체에서 나왔습니다. 바람은 운영 예보라 해상도가 낮고, "
             "실측 비행 자료에는 풍향이 없습니다. DJI 풍속·풍향 키는 SDK 문서에서 존재만 확인했습니다. 향후에는 기체가 주는 바람 "
             "값으로 예보를 보정하고, 실비행으로 풍향 효과와 SOC를 검증하는 것이 가장 먼저 할 일입니다.")

    s = D.content("참고문헌")
    section(s, "References")
    left = ["[1] Nash, Koenig, Tovey. Lazy Theta*. AAAI 2010.", "[2] Phillips, Likhachev. SIPP. ICRA 2011.",
            "[3] Yakovlev, Andreychuk. Any-angle SIPP. ICAPS 2017.",
            "[4] Koenig, Likhachev, Furcy. Lifelong Planning A*. Artif. Intell. 2004.",
            "[5] Zeng, Xu, Zhang. Energy minimization, rotary-wing UAV. IEEE TWC 2019.",
            "[6] Leishman. Principles of Helicopter Aerodynamics, 2nd ed. 2006.",
            "[7] Rodrigues et al. DJI Matrice 100 delivery energy data set. Sci. Data 2021.",
            "[8] Betti Sorbelli et al. Delivery with drones under varying wind. IEEE T-ITS 2021.",
            "[9] Lian et al. WA-LPA*, dynamic wind. Drones 2025.", "[10] Lian et al. UAV path planning with wind field. IEEE ITSC 2025.",
            "[11] Gu, Zhao, Ren. Wind field analysis in UAV path planning. Chin. J. Aeronaut. 2026."]
    right = ["[12] Blackmore, Ono, Williams. Chance-constrained path planning. IEEE T-RO 2011.",
             "[13] Du Toit, Burdick. Planning in dynamic, uncertain environments. IEEE T-RO 2012.",
             "[14] Zhu, Alonso-Mora. Chance-constrained avoidance for MAVs. IEEE RA-L 2019.",
             "[15] Olive. traffic, air traffic data toolbox. JOSS 2019.", "[16] Schäfer et al. Bringing up OpenSky. IPSN 2014.",
             "[17] Dorling et al. Vehicle routing problems for drone delivery. IEEE TSMC 2017.",
             "[18] Zhang et al. Energy consumption models for delivery drones. Transp. Res. D 2021.",
             "[19] Stolaroff et al. Energy use of drones for package delivery. Nat. Commun. 2018.",
             "[20] Thibbotuwawa et al. UAV mission planning resistant to weather uncertainty. Sensors 2020.",
             "[21] DJI. Onboard SDK, Payload SDK, Mobile SDK V5 (GitHub: dji-sdk), 2026-11 열람.",
             "[22] ArduPilot (ArduCopter), MAVLink common.xml·ardupilotmega.xml (GitHub), 2026-11 열람."]
    textbox(s, X0, 1.2, 6.0, 5.6, [[(r, {"size": 12, "bold": False, "color": INK})] for r in left], space_after=7)
    textbox(s, 6.75, 1.2, 6.0, 5.6, [[(r, {"size": 12, "bold": False, "color": INK})] for r in right], space_after=7)
    source(s, "데이터: Copernicus GLO-30 DSM · Overture Maps · NOAA GFS · CMU M100 비행 · OpenSky ADS-B (traffic 2.13)")
    notes(s, "전체 서지(DOI 포함)는 저장소의 docs/paper/references.bib에 있습니다.")

    normalize_partnames(D.prs)
    D.prs.save(out)
    print("saved", out, len(D.prs.slides), "slides")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
