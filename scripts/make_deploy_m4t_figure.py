"""알고리즘 탑재 구성도 (실증 기체 DJI Matrice 4T + 시험비행 기체 X650) → results/figures/F38_deploy_m4t.png"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

for _f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
    if Path(_f).exists():
        fm.fontManager.addfont(_f)
plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
OUT = Path(__file__).resolve().parents[1] / "results" / "figures" / "F38_deploy_m4t.png"

BLUE, BLUE_F = "#1F4E79", "#DCE6F2"
GREEN, GREEN_F = "#2E7D5B", "#E3F2EA"
ORANGE, ORANGE_F = "#C55A11", "#FBE5D6"
GREY = "#595959"


def box(ax, x, y, w, h, title, lines, ec, fc, dashed=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                ec=ec, fc=fc, lw=2, ls="--" if dashed else "-"))
    ax.text(x + w / 2, y + h - 0.32, title, ha="center", va="center", fontsize=13, weight="bold", color="#1A1A1A")
    for i, t in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.78 - 0.4 * i, t, ha="center", va="center", fontsize=10.5, color="#262626")


def arrow(ax, a, b, color=GREY, label=None, dashed=False, lx=0, ly=0.18, rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=16, lw=1.8, color=color,
                                 ls="--" if dashed else "-", connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly, label, ha="center", va="center", fontsize=9.5, color=color)


fig, ax = plt.subplots(figsize=(14, 7.4), dpi=160)
ax.set_xlim(0, 14)
ax.set_ylim(0, 7.4)
ax.axis("off")

box(ax, 0.2, 4.4, 3.0, 2.6, "입력 자료", ["지형·건물·비행 금지 구역", "수치예보 바람 (고도·시간별)", "기체·탑재중량·출발 시각"], BLUE, BLUE_F)
box(ax, 4.0, 2.6, 3.6, 4.4, "지상 PC (계획 서버)", ["dpo-server · 3D 계획 화면", "WE-LT* 에너지 경로 탐색", "구간 속도 · SoC 예상", "출발 판정 (go / caution / no-go)", "실시간 엔진 (속도 갱신)"], BLUE, BLUE_F)
arrow(ax, (3.2, 5.7), (4.0, 5.7))

box(ax, 8.4, 5.1, 2.6, 1.9, "DJI 경로 파일", [".kmz (WPML)", "경로점 · 구간 속도"], GREEN, GREEN_F)
box(ax, 8.4, 2.6, 2.6, 1.9, "ArduPilot 미션", [".waypoints", "구간 속도 명령 포함"], ORANGE, ORANGE_F)
arrow(ax, (7.6, 6.05), (8.4, 6.05))
arrow(ax, (7.6, 3.55), (8.4, 3.55))

box(ax, 11.6, 5.1, 2.2, 1.9, "실증 기체", ["DJI Matrice 4T", "DJI Pilot 2"], GREEN, GREEN_F)
box(ax, 11.6, 2.6, 2.2, 1.9, "시험비행 기체", ["Holybro X650", "ArduCopter"], ORANGE, ORANGE_F)
arrow(ax, (11.0, 6.05), (11.6, 6.05), GREEN, "불러오기", ly=0.22)
arrow(ax, (11.0, 3.55), (11.6, 3.55), ORANGE, "업로드", ly=0.22)

box(ax, 4.0, 0.15, 9.8, 1.85, "비행 중 연동", ["X650: MAVLink 연결 (구현) – 구간 속도 명령 송신, 바람·배터리 수신", "M4T: Mobile SDK V5 기반 앱 필요 (개발 예정)"], GREY, "#F2F2F2", dashed=True)
arrow(ax, (5.8, 2.6), (5.8, 2.0))
ax.add_patch(FancyArrowPatch((12.7, 2.0), (12.7, 2.6), arrowstyle="<|-|>", mutation_scale=16, lw=1.8, color=ORANGE))
ax.text(12.85, 2.3, "MAVLink", ha="left", va="center", fontsize=9.5, color=ORANGE)

fig.tight_layout()
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, facecolor="white")
print("저장:", OUT)
