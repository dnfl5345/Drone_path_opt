"""업체 시험용 배포판을 만든다 → dist/DronePathOpt_v<판>.zip

    python scripts/make_release.py

먼저 할 것: python -m build --wheel --outdir dist/ · python scripts/make_data_bundle.py · 보고서·매뉴얼·시험 안내서 생성
          (scripts/release_test.py 로 빈 폴더 설치 시험을 통과한 wheel·자료 묶음을 넣는다)
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__ as VER  # noqa: E402

NAME = f"DronePathOpt_v{VER}"
WHL, DATA = f"drone_path_opt-{VER}-py3-none-any.whl", f"dpo_data_{VER}.zip"

# Windows 실행 파일: 명령은 ASCII 만, 안내 글은 한국어 Windows 기본 코드 페이지(cp949)로 저장하고 CRLF 로 끝낸다.
# (UTF-8 로 저장하면 chcp 65001 이 실행되기 전 줄을 cp949 로 읽다가 한글 바이트가 줄바꿈을 삼켜 다음 명령이 사라질 수 있다.)
# 괄호 블록 안에서는 echo 에 괄호를 쓰지 않는다 (블록이 일찍 닫힘). 분기는 goto 로 한다.
INSTALL_BAT = rf"""@echo off
setlocal
rem DronePathOpt v{VER} - Windows install (run once)
set PYTHONUTF8=1
cd /d "%~dp0"
echo [1/4] Python 확인
set "PYEXE="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto nopython
%PYEXE% --version
echo [2/4] 가상환경 만들기: dpo-env
%PYEXE% -m venv dpo-env
if not exist dpo-env\Scripts\python.exe goto novenv
echo [3/4] 프로그램 설치 - 인터넷에서 numpy 등 필요한 라이브러리를 받습니다. 몇 분 걸릴 수 있습니다.
dpo-env\Scripts\python.exe -m pip install --disable-pip-version-check -q --upgrade pip >nul 2>nul
dpo-env\Scripts\python.exe -m pip install --disable-pip-version-check "{WHL}[mavlink]"
if errorlevel 1 goto nopip
echo [4/4] 자료 묶음 풀기: {DATA}
dpo-env\Scripts\python.exe -c "import zipfile; zipfile.ZipFile('{DATA}').extractall('.')"
if errorlevel 1 goto nodata
dpo-env\Scripts\dpo-plan.exe --check
if errorlevel 1 goto nocheck
echo.
echo 설치가 끝났습니다. run_windows.bat 를 두 번 눌러 실행하십시오. 창원 지역은 run_windows_changwon.bat
pause
exit /b 0

:nopython
echo [오류] Python 3.10 이상을 찾지 못했습니다.
echo        python.org 에서 Python 을 설치할 때 첫 화면의 'Add python.exe to PATH' 를 켠 뒤 이 파일을 다시 실행하십시오.
pause
exit /b 1
:novenv
echo [오류] 가상환경 dpo-env 를 만들지 못했습니다. 폴더에 쓰기 권한이 있는지 확인하십시오.
pause
exit /b 1
:nopip
echo [오류] 프로그램 설치에 실패했습니다. 인터넷 연결 또는 회사 프록시 설정을 확인하고 이 파일을 다시 실행하십시오.
pause
exit /b 1
:nodata
echo [오류] 자료 묶음 {DATA} 을 풀지 못했습니다. 배포판 zip 을 모두 풀었는지 확인하십시오.
pause
exit /b 1
:nocheck
echo [오류] 점검에 실패했습니다. 위의 글을 화면 캡처하여 회신해 주십시오.
pause
exit /b 1
"""

RUN_BAT = r"""@echo off
setlocal
rem DronePathOpt - run ({site})
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist dpo-env\Scripts\dpo-server.exe goto noinstall
echo {label} 지역으로 켭니다. 잠시 뒤 브라우저에 3D 화면이 열립니다.
echo 열리지 않으면 브라우저 주소창에 http://127.0.0.1:8765/3d 를 넣으십시오.
echo 이 검은 창을 닫으면 프로그램이 꺼집니다.
dpo-env\Scripts\dpo-server.exe --site {site} --open
pause
exit /b 0
:noinstall
echo 먼저 install_windows.bat 를 두 번 눌러 설치하십시오.
pause
exit /b 1
"""

INSTALL_SH = f"""#!/bin/sh
# 드론 SoC 최대화 경로계획 v{VER} – Linux·macOS 설치 (처음 한 번): sh install_linux_mac.sh
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -n "$PY" ] || {{ echo "[오류] Python 3.10 이상이 필요합니다"; exit 1; }}
"$PY" -m venv dpo-env
. dpo-env/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q "{WHL}[mavlink]"
python -c "import zipfile; zipfile.ZipFile('{DATA}').extractall('.')"
dpo-plan --check
echo "설치가 끝났습니다. 실행: sh run_linux_mac.sh  (창원: sh run_linux_mac.sh changwon)"
"""

RUN_SH = """#!/bin/sh
# 드론 SoC 최대화 경로계획 – 실행: sh run_linux_mac.sh [pangyo|changwon]. 끄기: Ctrl+C
cd "$(dirname "$0")"
[ -f dpo-env/bin/activate ] || { echo "먼저 sh install_linux_mac.sh 를 실행하십시오"; exit 1; }
. dpo-env/bin/activate
exec dpo-server --site "${1:-pangyo}" --open
"""

README = f"""드론 SoC 최대화 경로계획 (drone-path-opt) v{VER} – 업체 시험용 배포판
과제: 배터리 잔존 용량을 고려한 드론 경로 최적화 알고리즘 개발 (한국전자기술연구원)

1. 설치 (처음 한 번, 인터넷 필요)
   Windows      : install_windows.bat 를 두 번 누름
   Linux·macOS  : sh install_linux_mac.sh
   필요: Python 3.10 이상, Chrome 또는 Edge

2. 실행
   Windows      : run_windows.bat (판교) / run_windows_changwon.bat (창원)
   Linux·macOS  : sh run_linux_mac.sh  /  sh run_linux_mac.sh changwon
   → 브라우저에 3D 화면 (http://127.0.0.1:8765/3d). 끄기: 검은 창 닫기 또는 Ctrl+C

3. 무엇을 시험하고 무엇을 회신하는지: 시험안내서.hwpx
   화면 순서대로 쓰는 법: 사용자매뉴얼.docx

4. 알아 둘 것
   - 기본 기체는 실증 기체 DJI Matrice 4T (탑재 최대 0.2 kg). 전력 모델은 DJI 공식 사양
     (무탑재 호버 42분, 9 m/s 정속 49분)에 맞춘 값이며, 탑재·고속 비행은 실측 비행 기록으로 확인 전입니다.
   - M4T 의 DJI 경로 파일은 DJI Pilot 2 에서 그 기체로 내보낸 .kmz 를 지정해 만듭니다 (시험안내서 T5).
   - DJI 경로 파일은 DJI 소프트웨어에서 열어 확인하지 못했습니다. 불러온 뒤 고도·속도·복귀 고도를 확인하십시오.
   - Windows 설치·실행 파일은 개발 환경에 Windows 가 없어 실제 Windows 에서는 시험하지 못했습니다.
     같은 설치 순서를 Linux 에서 시험하였고, Windows 용 라이브러리(Python 3.10–3.14)가 모두 있음을 확인했습니다.
     오류가 나면 검은 창을 화면 캡처하여 회신해 주십시오.
   - 실제 비행 전에는 비행 승인과 현장 안전 확인이 필요합니다.

5. 파일 확인: SHA256SUMS.txt 의 값과 각 파일의 SHA-256 이 같으면 온전히 전달된 것입니다.
"""

DOCS = {
    "사용자매뉴얼.docx": "docs/drone_soc_user_manual.docx",
    "시험안내서.hwpx": "docs/drone_soc_test_guide.hwpx",
    "비행기록표.xlsx": "docs/drone_soc_flight_log_template.xlsx",
    "보고서/결과보고서.hwpx": "docs/drone_soc_final_report.hwpx",
    "보고서/사업내용보고서.hwpx": "docs/drone_soc_business_report.hwpx",
    "보고서/사업내용_발표자료.pptx": "docs/drone_soc_business_talk.pptx",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-report", help="결과보고서 대신 넣을 파일 (예: 연구책임자를 채운 전달용 판 – 저장소에는 넣지 않음)")
    ap.add_argument("--dist", default=str(ROOT / "dist"), help="배포판을 만들 폴더")
    a = ap.parse_args()
    docs = dict(DOCS)
    if a.final_report:
        docs["보고서/결과보고서.hwpx"] = str(Path(a.final_report).resolve())
    out = Path(a.dist) / NAME
    if out.exists():
        shutil.rmtree(out)
    (out / "보고서").mkdir(parents=True)
    for f in (WHL, DATA):
        src = ROOT / "dist" / f
        if not src.exists():
            sys.exit(f"없음: dist/{f} – 먼저 wheel 과 자료 묶음을 만드십시오")
        shutil.copy2(src, out / f)
    for dst, src in docs.items():
        if not (ROOT / src).exists():
            sys.exit(f"없음: {src}")
        shutil.copy2(ROOT / src, out / dst)
    crlf = lambda t, enc="utf-8": t.replace("\r\n", "\n").replace("\n", "\r\n").encode(enc)
    (out / "install_windows.bat").write_bytes(crlf(INSTALL_BAT, "cp949"))
    (out / "run_windows.bat").write_bytes(crlf(RUN_BAT.format(site="pangyo", label="판교"), "cp949"))
    (out / "run_windows_changwon.bat").write_bytes(crlf(RUN_BAT.format(site="changwon", label="창원"), "cp949"))
    (out / "install_linux_mac.sh").write_text(INSTALL_SH, encoding="utf-8")
    (out / "run_linux_mac.sh").write_text(RUN_SH, encoding="utf-8")
    (out / "README.txt").write_bytes(crlf(README))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.relative_to(out).as_posix()}\n" for p in files), encoding="utf-8")
    z = Path(a.dist) / f"{NAME}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(f"{NAME}/{p.relative_to(out).as_posix()}", date_time=(2026, 11, 22, 0, 0, 0))
                info.external_attr = (0o755 if p.suffix == ".sh" else 0o644) << 16
                info.flag_bits |= 0x800                       # 파일 이름 UTF-8 (Windows 탐색기에서 한글 이름이 깨지지 않게)
                zf.writestr(info, p.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    print(f"저장: {z} ({z.stat().st_size / 1e6:.1f} MB, 파일 {len(files) + 1}개)")


if __name__ == "__main__":
    main()
