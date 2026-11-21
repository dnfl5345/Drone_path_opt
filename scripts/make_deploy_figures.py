"""배포 문서·보고서용 그림: 구성도(F31), 속도 갱신 판단 흐름(F32), 실시간 운용 한 편의 시간 이력(F33).

사용: PYTHONPATH=src python scripts/make_deploy_figures.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager as fm
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from dpo import config as C, drones as DR
from dpo.live import LiveEngine
from dpo.wind_field import Site3D

for f in ("/root/.fonts/NanumGothic-Regular.ttf", "/root/.fonts/NanumGothic-Bold.ttf"):
    if os.path.exists(f):
        fm.fontManager.addfont(f)
plt.rcParams.update({"font.family": ["NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False, "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200, "savefig.bbox": "tight",
                     "axes.titleweight": "bold"})
NAVY, TEAL, ORANGE, GREEN, GRAY, LIGHT, INK = "#004E8F", "#1B6E8C", "#E46C0A", "#2CA58D", "#6B7780", "#E3EEF3", "#16202A"


def box(ax, x, y, w, h, title, lines=(), fc=LIGHT, ec=TEAL, tc=INK, fs=10.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=fc, ec=ec, lw=1.4))
    ax.text(x + w / 2, y + h - 0.26, title, ha="center", va="top", fontsize=fs, fontweight="bold", color=tc)
    for n, t in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.66 - 0.31 * n, t, ha="center", va="top", fontsize=8.6, color=tc)


def arrow(ax, p, q, text=None, color=GRAY, rad=0.0, off=(0, 0.12), ls="-"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, lw=1.4, color=color, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}"))
    if text:
        ax.text((p[0] + q[0]) / 2 + off[0], (p[1] + q[1]) / 2 + off[1], text, ha="center", va="bottom", fontsize=8.2, color=color)


def architecture():
    fig, ax = plt.subplots(figsize=(13.2, 7.3))
    ax.set_xlim(0, 13.2); ax.set_ylim(-0.75, 6.55); ax.axis("off")
    box(ax, 0.2, 4.55, 2.7, 1.8, "국토 정보", ["지형 (DSM 30 m)", "건물 높이", "금지구역 · 이동 장애물"])
    box(ax, 0.2, 2.35, 2.7, 1.8, "기상 정보 (예보)", ["고도별 바람 0.25°, 1시간", "출발 전에 받음"])
    box(ax, 0.2, 0.25, 2.7, 1.7, "기체가 잰 바람", ["MAVLink WIND", "비행 중 30 s 주기"], fc="#FDEBD9", ec=ORANGE)
    box(ax, 3.7, 3.6, 3.0, 2.75, "계획기", ["WE-LT* 에너지 탐색", "시공간 장애물 회피", "", "안전장치:", "최단 경로 후보와 비교"], fc="#DCEAF6", ec=NAVY)
    box(ax, 3.7, 0.25, 3.0, 2.75, "실시간 엔진", ["감시 · 충돌 검사", "재계획 (장애물 변경)", "", "속도 갱신:", "바람 편차 → 구간 속도"], fc="#DCEAF6", ec=NAVY)
    box(ax, 7.5, 4.55, 2.6, 1.8, "미션 파일", [".waypoints (QGC WPL 110)", "경로표 .csv", "dpo-plan · /api/mission"])
    box(ax, 7.5, 2.35, 2.6, 1.8, "화면 · API", ["/live 실시간 상태", "/3d 지도 · 장애물 편집", "/api/health"])
    box(ax, 7.5, 0.25, 2.6, 1.7, "MAVLink 연결", ["DO_CHANGE_SPEED 송신", "남은 구간 미션 올리기"], fc="#FDEBD9", ec=ORANGE)
    box(ax, 10.9, 1.9, 2.1, 2.8, "기체", ["Holybro X650", "ArduCopter", "", "(시험: 모의 기체", "루프백까지)"], fc="#E6F4EF", ec=GREEN)
    arrow(ax, (2.9, 5.45), (3.7, 5.2)); arrow(ax, (2.9, 3.25), (3.7, 4.4))
    arrow(ax, (2.9, 1.1), (3.7, 1.3), color=ORANGE)
    arrow(ax, (5.2, 3.6), (5.2, 3.0))
    ax.text(5.32, 3.3, "계획", ha="left", va="center", fontsize=8.2, color=GRAY)
    arrow(ax, (6.7, 5.2), (7.5, 5.45)); arrow(ax, (6.7, 1.9), (7.5, 3.0)); arrow(ax, (6.7, 1.1), (7.5, 1.1), color=ORANGE)
    arrow(ax, (10.1, 5.3), (11.6, 4.7), "출발 전 올리기", rad=-0.15, off=(0.35, 0.15))
    arrow(ax, (10.1, 1.25), (10.9, 2.5), color=ORANGE)
    ax.text(10.62, 1.42, "속도 명령", ha="left", va="center", fontsize=8.2, color=ORANGE)
    ax.plot([11.95, 11.95, 1.55], [1.9, -0.45, -0.45], color=ORANGE, lw=1.4, ls="--")       # 기체 → 실시간 엔진으로 되돌아오는 측정값
    arrow(ax, (1.55, -0.45), (1.55, 0.25), color=ORANGE, ls="--")
    ax.text(6.75, -0.38, "바람 · 위치 · 배터리 (MAVLink)", ha="center", va="bottom", fontsize=8.2, color=ORANGE)
    fig.savefig(C.FIG / "F31_architecture.png")
    plt.close(fig)


def flow():
    fig, ax = plt.subplots(figsize=(12.6, 5.2))
    ax.set_xlim(0, 12.6); ax.set_ylim(0, 5.2); ax.axis("off")
    steps = [("① 바람 측정", ["순항 시작 5 s 뒤,", "이후 30 s 마다"]), ("② 편차 추정", ["실측 − 예보", "지수평활 (0.6)"]),
             ("③ 속도 재결정", ["보정한 예보에서", "남은 구간 최적 속도"]), ("④ 이득 확인", ["예상 에너지", "0.3 % 넘게 감소?"]),
             ("⑤ 안전 확인", ["새 속도의 궤적이", "위험구간과 안 겹침?"]), ("⑥ 채택", ["3 s 뒤 교체", "속도 명령 전송"])]
    w, h, gap = 1.78, 1.55, 0.3
    for n, (t, ls) in enumerate(steps):
        x = 0.2 + n * (w + gap)
        fc, ec = ("#E6F4EF", GREEN) if n == 5 else (("#FDEBD9", ORANGE) if n in (3, 4) else ("#DCEAF6", NAVY))
        box(ax, x, 3.3, w, h, t, ls, fc=fc, ec=ec)
        if n < 5:
            arrow(ax, (x + w, 4.07), (x + w + gap, 4.07), "예" if n in (3, 4) else None, color=GREEN if n in (3, 4) else GRAY, off=(0, 0.06))
    box(ax, 6.45, 0.45, 3.95, 1.5, "지금 속도 유지", ["다음 주기에 다시 판단", "(안전 확인에서 걸리면 '보류'로 기록)"], fc="#F1F3F5", ec=GRAY)
    arrow(ax, (7.33, 3.3), (7.7, 1.95), "아니오", color=GRAY, off=(-0.42, -0.1))
    arrow(ax, (9.41, 3.3), (9.1, 1.95), "아니오", color=GRAY, off=(0.42, -0.1))
    box(ax, 0.2, 0.45, 5.6, 1.5, "건너뛰는 경우", ["경로 재계획이 진행 중이거나 교체가 예약돼 있을 때", "남은 비행 시간이 20 s 보다 짧을 때"], fc="#F1F3F5", ec=GRAY)
    fig.savefig(C.FIG / "F32_speed_update_flow.png")
    plt.close(fig)


def timeline():
    g = dict(np.load(C.PROC / "site_grid.npz"))
    f0 = dict(np.load(C.RAW / "gfs_20261027_00z.npz", allow_pickle=True))
    site = Site3D(g, f0, np.datetime64("2026-10-27T00:00"))
    d = DR.m100()
    runs = {}
    for name, su in (("끔", 0), ("30 s", 30)):
        E = LiveEngine(site, d, sync=True)
        E.start(dict(dep_hour=15, payload=0.5, round_trip=True, refine=False, wind_truth=dict(rot=60), speed_update=su))
        s = E.run_sync(t_max=3000)
        runs[name] = (E.telemetry, s, E.speed_log)
    fig, axs = plt.subplots(3, 1, figsize=(11.5, 7.4), sharex=True, gridspec_kw=dict(hspace=0.16, height_ratios=[1.0, 1.0, 0.8]))
    col = {"끔": GRAY, "30 s": "#1B6CA8"}
    for name, (tel, s, log) in runs.items():
        t = np.array([r["t"] for r in tel])
        v = np.array([np.nan if r["v_cmd"] is None else r["v_cmd"] for r in tel], float)
        axs[0].plot(t, v, color=col[name], lw=1.8, label=f"속도 갱신 {name}")
        axs[1].plot(t, [r["soc"] for r in tel], color=col[name], lw=1.8,
                    label=f"속도 갱신 {name}: 도착 SOC {100 - s['soc_used']:.1f} %")
        if name == "30 s":
            axs[2].plot(t, [r["bias"] for r in tel], color=ORANGE, lw=1.8)
            for u in log:
                for ax in axs:
                    ax.axvline(u["t"], color=ORANGE, lw=0.8, ls=":")
            axs[2].text(log[0]["t"] + 4, 1.0, "점선: 속도 갱신을 채택한 시각", color=ORANGE, fontsize=8.5)
    axs[0].set_ylabel("명령 대지속도 [m/s]"); axs[0].legend(frameon=False, fontsize=9, loc="upper right", ncol=2)
    axs[0].set_title("실시간 운용 한 편 (판교, M100, 왕복, 실제 풍향이 예보와 60° 다른 경우)", fontsize=11)
    axs[1].set_ylabel("SOC [%]"); axs[1].axhline(20, color="#D7263D", lw=0.8, ls="--")
    axs[1].text(4, 21.5, "예비 20 %", color="#D7263D", fontsize=8.5); axs[1].legend(frameon=False, fontsize=9, loc="upper right")
    axs[2].set_ylabel("추정한 바람 편차 [m/s]"); axs[2].set_xlabel("출발 뒤 시간 [s]"); axs[2].set_ylim(bottom=0)
    fig.savefig(C.FIG / "F33_live_timeline.png")
    plt.close(fig)
    return {k: dict(soc_end=100 - s["soc_used"], updates=s["speed_updates"], T=s["t"]) for k, (_, s, _) in runs.items()}


if __name__ == "__main__":
    architecture()
    flow()
    print(timeline())
