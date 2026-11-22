"""업체 배포판을 만든다 – 프로그램 구동에 필요한 파일과 사용자 매뉴얼만 넣는다 (보고서·시험 안내서는 넣지 않음).

    python scripts/make_release.py [--only windows|linux]

  dist/DronePathOpt_v<판>_Windows.zip       Windows 판: 파이썬과 라이브러리를 함께 넣어 설치 없이 실행 (인터넷 없이 켜짐)
  dist/DronePathOpt_v<판>_Linux_macOS.zip   Linux·macOS 판: 설치 파일이 가상환경을 만들고 인터넷으로 라이브러리를 받음

먼저 할 것: python -m build --wheel --outdir dist/ · python scripts/make_data_bundle.py · 사용자 매뉴얼 생성
Windows 판은 NuGet 의 공식 Windows 파이썬(python.<판>.nupkg)과 PyPI 의 win_amd64 설치 파일을 쓴다 (리눅스에서 만들 수 있음).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__ as VER  # noqa: E402

WHL, DATA = f"drone_path_opt-{VER}-py3-none-any.whl", f"dpo_data_{VER}.zip"
PY_WIN = "3.13.17"                       # NuGet 'python' 패키지 (python.org 의 공식 Windows 빌드)
MANUAL = ROOT / "docs" / "drone_soc_user_manual.docx"
CACHE = ROOT / "dist" / "cache"
ZIP_DATE = (2026, 11, 22, 0, 0, 0)

# ------------------------------------------------------------------ Windows 실행 파일
# 명령은 ASCII 만, 안내 글은 한국어 Windows 기본 코드 페이지(cp949)로 저장하고 CRLF 로 끝낸다 (UTF-8 이면 줄이 합쳐질 수 있음).
# 괄호 블록은 쓰지 않고 goto 로 분기한다.
WIN_ENV = r"""set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "DPO_HOME=%~dp0app\dpo_data"
set "NUMBA_CACHE_DIR=%~dp0app\cache"
set "PY=%~dp0app\python\python.exe"
if not exist "%PY%" goto broken"""

WIN_BROKEN = r""":broken
echo [오류] app 폴더를 찾지 못했습니다.
echo        받은 zip 을 오른쪽 클릭 - '모두 압축 풀기' 로 전부 푼 뒤, 푼 폴더 안의 이 파일을 실행하십시오.
echo        zip 을 연 창에서 바로 실행하면 이 오류가 납니다.
pause
exit /b 1
"""

WIN_RUN = rf"""@echo off
setlocal
rem DronePathOpt v{VER} for Windows - start (no installation needed)
cd /d "%~dp0"
{WIN_ENV}
echo 드론 SoC 최대화 경로계획 v{VER} 을 켭니다. 처음 한 번은 1분 정도 걸립니다.
echo 잠시 뒤 브라우저에 3D 화면이 열립니다. 열리지 않으면 주소창에 http://127.0.0.1:8765/3d 를 넣으십시오.
echo 이 검은 창을 닫으면 프로그램이 꺼집니다.
echo.
"%PY%" -m dpo.webapp.server --open %*
echo.
pause
exit /b 0

{WIN_BROKEN}"""

WIN_CMD = rf"""@echo off
rem DronePathOpt v{VER} for Windows - command prompt with dpo-plan, dpo-site, dpo-server
cd /d "%~dp0"
{WIN_ENV}
doskey dpo-plan="%PY%" -m dpo.cli $*
doskey dpo-site="%PY%" -m dpo.regions $*
doskey dpo-server="%PY%" -m dpo.webapp.server $*
doskey dpo-fly="%PY%" -m dpo.flight $*
echo 명령줄 사용 - 예:
echo   dpo-plan --check
echo   dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --out missions
echo   dpo-site search "37.795, 128.896"
echo   dpo-site create --name 이름 --lat 위도 --lon 경도 --size 5
echo   dpo-server --open
echo.
cmd /k
exit /b 0

{WIN_BROKEN}"""

README_WIN = f"""드론 SoC 최대화 경로계획 (drone-path-opt) v{VER} – Windows 판
과제: 배터리 잔존 용량을 고려한 드론 경로 최적화 알고리즘 개발 (한국전자기술연구원)

1. 준비: Windows 10 또는 11 (64비트), Chrome 또는 Edge. 따로 설치할 것은 없습니다 (파이썬과 라이브러리가 들어 있음).
2. 받은 zip 을 오른쪽 클릭 → '모두 압축 풀기'. (메일로 받았다면 먼저 zip 의 속성에서 '차단 해제'를 켬)
3. 푼 폴더의 '드론경로계획_실행.bat' 를 두 번 누르면 브라우저에 3D 화면이 열립니다.
   'Windows의 PC 보호' 창이 뜨면 '추가 정보' → '실행'. 끌 때는 검은 창을 닫습니다.
4. 지역: 화면 위의 '지역'에서 판교·창원을 고르거나 '+ 새 지역'으로 원하는 곳을 만듭니다 (인터넷 필요, 1–3분).
5. 명령줄: '명령창_열기.bat' (dpo-plan, dpo-site 명령).
6. 쓰는 법: 사용자매뉴얼.docx

알아 둘 것
- 기본 기체는 실증 기체 DJI Matrice 4T (탑재 최대 0.2 kg). 전력 모델은 DJI 공식 사양에 맞춘 값이며 실측 비행 기록으로 확인 전입니다.
- 새 지역의 자료(지형·건물·예보 바람)에는 비행 금지·제한 구역이 없습니다. 장애물 편집으로 넣고 비행 전 따로 확인하십시오.
- DJI 경로 파일은 DJI 소프트웨어에서 열어 확인하지 못했습니다. 불러온 뒤 고도·속도·복귀 고도를 확인하십시오.
- 실제 비행 전에는 비행 승인과 현장 안전 확인이 필요합니다.
- 들어 있는 것: app\\python (Python {PY_WIN}, PSF 사용권 – LICENSE.txt), 라이브러리 (각 패키지 사용권은 app\\python\\Lib\\site-packages 안),
  Microsoft Visual C++ 런타임 (MSVC_RUNTIME_LICENSE.txt), app\\dpo_data (지형·건물·예보 바람 자료).
"""

INSTALL_SH = f"""#!/bin/sh
# 드론 SoC 최대화 경로계획 v{VER} – Linux·macOS 설치 (처음 한 번): sh install_linux_mac.sh
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -n "$PY" ] || {{ echo "[오류] Python 3.10 이상이 필요합니다"; exit 1; }}
"$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || {{ echo "[오류] Python 3.10 이상이 필요합니다"; exit 1; }}
"$PY" -m venv dpo-env
. dpo-env/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q "{WHL}[mavlink,region]"
python -c "import zipfile; zipfile.ZipFile('{DATA}').extractall('.')"
dpo-plan --check
echo "설치가 끝났습니다. 실행: sh run_linux_mac.sh"
"""

RUN_SH = """#!/bin/sh
# 드론 SoC 최대화 경로계획 – 실행: sh run_linux_mac.sh [지역 key]. 끄기: Ctrl+C
cd "$(dirname "$0")"
[ -f dpo-env/bin/activate ] || { echo "먼저 sh install_linux_mac.sh 를 실행하십시오"; exit 1; }
. dpo-env/bin/activate
if [ -n "$1" ]; then exec dpo-server --site "$1" --open; else exec dpo-server --open; fi
"""

README_LX = f"""드론 SoC 최대화 경로계획 (drone-path-opt) v{VER} – Linux·macOS 판
과제: 배터리 잔존 용량을 고려한 드론 경로 최적화 알고리즘 개발 (한국전자기술연구원)

1. 설치 (처음 한 번, 인터넷 필요): sh install_linux_mac.sh      필요: Python 3.10 이상, Chrome·Edge·Firefox
2. 실행: sh run_linux_mac.sh  → 브라우저에 3D 화면 (http://127.0.0.1:8765/3d). 끄기: Ctrl+C
3. 지역: 화면 위의 '지역'에서 판교·창원을 고르거나 '+ 새 지역'으로 원하는 곳을 만듭니다 (인터넷 필요, 1–3분).
4. 명령줄: source dpo-env/bin/activate 뒤 dpo-plan, dpo-site
5. 쓰는 법: 사용자매뉴얼.docx       파일 확인: SHA256SUMS.txt

Windows 에서는 'DronePathOpt_v{VER}_Windows.zip' (설치 없이 실행)을 쓰십시오.
새 지역의 자료에는 비행 금지·제한 구역이 없습니다. 실제 비행 전에는 비행 승인과 현장 안전 확인이 필요합니다.
"""


def crlf(t, enc="utf-8"):
    return t.replace("\r\n", "\n").replace("\n", "\r\n").encode(enc)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def zipdir(src: Path, z: Path):
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(f"{src.name}/{p.relative_to(src).as_posix()}", date_time=ZIP_DATE)
                info.external_attr = (0o755 if p.suffix == ".sh" else 0o644) << 16
                info.flag_bits |= 0x800                       # 파일 이름 UTF-8 (Windows 탐색기에서 한글 이름이 깨지지 않게)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as f:
                    zf.writestr(info, f.read())
    return z


def need(f: Path):
    if not f.exists():
        sys.exit(f"없음: {f} – 먼저 wheel·자료 묶음·매뉴얼을 만드십시오")
    return f


# ------------------------------------------------------------------ Linux·macOS 판
def build_linux(dist: Path) -> Path:
    out = dist / f"DronePathOpt_v{VER}_Linux_macOS"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in (WHL, DATA):
        shutil.copy2(need(dist / f), out / f)
    shutil.copy2(need(MANUAL), out / "사용자매뉴얼.docx")
    (out / "install_linux_mac.sh").write_text(INSTALL_SH, encoding="utf-8")
    (out / "run_linux_mac.sh").write_text(RUN_SH, encoding="utf-8")
    (out / "README.txt").write_bytes(crlf(README_LX))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.relative_to(out).as_posix()}\n" for p in files), encoding="utf-8")
    return zipdir(out, dist / f"{out.name}.zip")


# ------------------------------------------------------------------ Windows 판
def _download(url, dst: Path):
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r:
            tmp = dst.with_suffix(".part")
            tmp.write_bytes(r.read())
            tmp.replace(dst)
    return dst


def _prune(py: Path):
    sp = py / "Lib" / "site-packages"
    keep = {"__init__.py", "ardupilotmega.py", "common.py", "minimal.py", "standard.py"}   # 쓰는 MAVLink 방언만
    for v in ("v10", "v20"):
        d = sp / "pymavlink" / "dialects" / v
        for f in d.iterdir():
            if f.is_file() and f.name not in keep:
                f.unlink()
            elif f.is_dir() and f.name == "__pycache__":
                for g in f.iterdir():
                    if not any(g.name.startswith(k[:-3] + ".") for k in keep):
                        g.unlink()
    for p in sorted(sp.rglob("*"), key=lambda p: -len(p.parts)):
        if p.is_dir() and p.name in ("tests", "test"):
            shutil.rmtree(p, ignore_errors=True)
    for pat in ("*.h", "*.lib", "*.pyx", "*.pxd", "*.c", "*.cpp"):
        for f in sp.rglob(pat):
            if f.is_file():
                f.unlink()
    shutil.rmtree(sp / "bin", ignore_errors=True)
    for d in ("include", "libs"):
        shutil.rmtree(py / d, ignore_errors=True)


def _pe_check(py: Path):
    """모든 .pyd/.dll 이 필요로 하는 DLL 이 묶음 안이나 Windows 기본 DLL 에 있는지 (pefile 이 있을 때)."""
    try:
        import pefile
    except ImportError:
        print("  (pefile 없음 – DLL 검사 생략)")
        return None
    system = set("""kernel32 user32 advapi32 ws2_32 shell32 ole32 oleaut32 gdi32 comdlg32 crypt32 bcrypt ncrypt secur32 shlwapi version winmm
iphlpapi psapi rpcrt4 ntdll dbghelp userenv netapi32 winhttp wininet normaliz wldap32 msvcrt setupapi cfgmgr32 powrprof mswsock dnsapi imm32
comctl32 uxtheme dwmapi opengl32 glu32 mpr wtsapi32 credui cabinet propsys shcore dwrite d2d1 windowscodecs ucrtbase bcryptprimitives kernelbase
odbc32 oleacc msimg32 pdh webservices wsock32""".split())
    optional = {"tbb12.dll"}                      # numba TBB 스레드 계층 (선택)
    bins = [p for p in py.rglob("*") if p.suffix.lower() in (".pyd", ".dll", ".exe")]
    have = {p.name.lower() for p in bins}
    miss = {}
    for p in bins:
        pe = pefile.PE(str(p), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
            d = e.dll.decode().lower()
            if d.startswith(("api-ms-win-", "ext-ms-")) or d[:-4] in system or d in have or d in optional or d == "winspool.drv":
                continue
            miss.setdefault(d, []).append(str(p.relative_to(py)))
    print(f"  DLL 검사: PE 파일 {len(bins)}개, 빠진 DLL {len(miss)}개 {sorted(miss)}")
    if miss:
        sys.exit("Windows 판에 빠진 DLL 이 있습니다")
    return len(bins)


def build_windows(dist: Path) -> Path:
    out = dist / f"DronePathOpt_v{VER}_Windows"
    if out.exists():
        shutil.rmtree(out)
    app = out / "app"
    py = app / "python"
    app.mkdir(parents=True)
    # 1) 공식 Windows 파이썬 (NuGet)
    nup = _download(f"https://api.nuget.org/v3-flatcontainer/python/{PY_WIN}/python.{PY_WIN}.nupkg", CACHE / f"python.{PY_WIN}.nupkg")
    with zipfile.ZipFile(nup) as z:
        for n in z.namelist():
            if n.startswith("tools/") and not n.endswith("/"):
                dst = py / n[len("tools/"):]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(n))
    # 2) 프로그램과 라이브러리 (win_amd64 설치 파일을 그 파이썬의 site-packages 에 바로 풀어 넣음)
    tag = PY_WIN.rsplit(".", 1)[0]
    sp = py / "Lib" / "site-packages"
    pipx = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-warn-conflicts",
            "--platform", "win_amd64", "--python-version", tag, "--implementation", "cp", "--abi", "cp" + tag.replace(".", ""),
            "--only-binary=:all:"]
    subprocess.run(pipx + ["--target", str(sp), f"{need(dist / WHL)}[mavlink,region]"], check=True)
    # 3) Visual C++ 런타임 DLL 을 python.exe 옆에 (VC++ 재배포 패키지가 없는 PC 대비)
    rt = CACHE / "msvc_runtime"
    shutil.rmtree(rt, ignore_errors=True)
    subprocess.run(pipx + ["--no-deps", "--target", str(rt), "msvc-runtime"], check=True)
    data = next(rt.rglob("msvcp140.dll")).parent
    for f in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
              "vcomp140.dll", "concrt140.dll"):
        shutil.copy2(data / f, py / f)
    lic = next(rt.rglob("LICENSE"), None)
    if lic:
        shutil.copy2(lic, py / "MSVC_RUNTIME_LICENSE.txt")
    _prune(py)
    n_pe = _pe_check(py)
    # 4) 자료
    with zipfile.ZipFile(need(dist / DATA)) as z:
        z.extractall(app)
    (app / "cache").mkdir(exist_ok=True)
    # 5) 실행 파일·매뉴얼
    (out / "드론경로계획_실행.bat").write_bytes(crlf(WIN_RUN, "cp949"))
    (out / "명령창_열기.bat").write_bytes(crlf(WIN_CMD, "cp949"))
    (out / "README.txt").write_bytes(crlf(README_WIN))
    shutil.copy2(need(MANUAL), out / "사용자매뉴얼.docx")
    longest = max(len(p.relative_to(out.parent).as_posix()) for p in out.rglob("*"))
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"  가장 긴 경로 {longest}자, 크기 {size / 1e6:.0f} MB")
    (dist / f"{out.name}.info.json").write_text(json.dumps(dict(python=PY_WIN, pe_files=n_pe, longest_path=longest, size_mb=round(size / 1e6)),
                                                          ensure_ascii=False), encoding="utf-8")
    return zipdir(out, dist / f"{out.name}.zip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["windows", "linux"])
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    a = ap.parse_args()
    dist = Path(a.dist)
    out = []
    if a.only in (None, "linux"):
        out.append(build_linux(dist))
    if a.only in (None, "windows"):
        out.append(build_windows(dist))
    for z in out:
        h = sha256(z)
        (dist / f"{z.name}.sha256").write_text(f"{h}  {z.name}\n", encoding="utf-8")
        print(f"저장: {z} ({z.stat().st_size / 1e6:.1f} MB) sha256 {h[:16]}…")


if __name__ == "__main__":
    main()
