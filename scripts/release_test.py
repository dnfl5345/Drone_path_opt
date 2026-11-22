"""배포판 인수 시험: 업체 직원이 받는 그대로 배포판 zip 을 빈 폴더에서 풀고, 설치 파일·실행 파일·명령줄·3D 화면을 차례로 시험한다.

    python scripts/release_test.py <빈 작업 폴더> [--skip-ui]

  1. 배포판 zip 풀기, SHA256SUMS.txt 확인
  2. sh install_linux_mac.sh               (가상환경 → 프로그램 설치 → 자료 풀기 → 점검)
  3. sh run_linux_mac.sh                   (서버 준비 시간, /api/health)
  4. node scripts/client_acceptance.js     (3D 화면 T2–T6, 실시간 모의 비행) – 판교
  5. sh run_linux_mac.sh changwon + 4      – 창원
  6. dpo-plan … (매뉴얼 6장 명령)
기록: docs/manual_img/install_log.json (매뉴얼의 터미널 화면·시험 기록 표가 이 기록을 쓴다).
Windows 설치 파일(.bat)은 이 환경에서 실행할 수 없어, 인코딩·줄바꿈·명령이 ASCII 인지만 검사한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__ as VER  # noqa: E402

NAME = f"DronePathOpt_v{VER}"
PORT = 8765


def run(cmd, cwd, env=None, timeout=1800, show=None):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str))
    out = (p.stdout + p.stderr).replace(str(cwd), "~/dpo")
    return dict(cmd=show or (cmd if isinstance(cmd, str) else " ".join(cmd)), rc=p.returncode, sec=round(time.time() - t0, 1), out=out)


def health(timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2).read()), round(time.time() - t0, 1)
        except Exception:
            time.sleep(0.3)
    return None, round(time.time() - t0, 1)


def check_bat(path: Path) -> list[str]:
    """Windows 실행 파일 정적 검사: CRLF, cp949 로 읽힘, 명령 줄은 ASCII, 괄호 블록 없음."""
    b = path.read_bytes()
    probs = []
    if b"\n" in b.replace(b"\r\n", b""):
        probs.append("LF 줄바꿈")
    try:
        t = b.decode("cp949")
    except UnicodeDecodeError:
        return probs + ["cp949 로 읽히지 않음"]
    for ln in t.splitlines():
        s = ln.strip()
        if s.lower().startswith(("echo", "rem", ":")) or not s:
            continue
        if any(ord(c) > 127 for c in s):
            probs.append(f"명령에 ASCII 밖 글자: {s[:40]}")
        if s.endswith("("):
            probs.append(f"괄호 블록: {s[:40]}")
    return probs


def server(work, site, env, log):
    cmd = ["sh", "run_linux_mac.sh"] + ([site] if site != "pangyo" else [])
    srv = subprocess.Popen(cmd, cwd=work, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    h, sec = health()
    return srv, h, sec


def stop(srv):
    try:
        os.killpg(srv.pid, signal.SIGINT)
        srv.wait(timeout=20)
    except Exception:
        os.killpg(srv.pid, signal.SIGKILL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--skip-ui", action="store_true")
    a = ap.parse_args()
    W0 = Path(a.work).resolve()
    W0.mkdir(parents=True, exist_ok=True)
    if any(W0.iterdir()):
        sys.exit(f"빈 폴더가 아닙니다: {W0}")
    zsrc = ROOT / "dist" / f"{NAME}.zip"
    shutil.copy2(zsrc, W0 / zsrc.name)
    log = dict(version=VER, steps={}, acceptance={})
    S = log["steps"]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DPO_HOME", "VIRTUAL_ENV", "DPO_SITE")}
    # 1. 풀기·확인
    t0 = time.time()
    with zipfile.ZipFile(W0 / zsrc.name) as z:
        z.extractall(W0)
    W = W0 / NAME
    S["unzip_release"] = dict(cmd=f"unzip {zsrc.name} && cd {NAME}", rc=0, sec=round(time.time() - t0, 1),
                              out="\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in W.iterdir())))
    bad = []
    for ln in (W / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        h, f = ln.split("  ", 1)
        if hashlib.sha256((W / f).read_bytes()).hexdigest() != h:
            bad.append(f)
    n_sum = len((W / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines())
    S["sha"] = dict(cmd="sha256sum -c SHA256SUMS.txt", rc=1 if bad else 0, sec=0.0, out=f"{n_sum}개 파일 모두 일치" if not bad else "불일치: " + ", ".join(bad))
    S["bat"] = dict(cmd="Windows 실행 파일 정적 검사", rc=0, sec=0.0, out="")
    for f in ("install_windows.bat", "run_windows.bat", "run_windows_changwon.bat"):
        p = check_bat(W / f)
        S["bat"]["out"] += f"{f}: {'통과' if not p else '; '.join(p)}\n"
        S["bat"]["rc"] |= bool(p)
    # 2. 설치
    S["install"] = run(["sh", "install_linux_mac.sh"], W, env, show="sh install_linux_mac.sh")
    bin_ = W / "dpo-env" / "bin"
    venv_env = dict(env, PATH=f"{bin_}:{env.get('PATH', '')}", VIRTUAL_ENV=str(W / "dpo-env"))
    # 3–5. 실행 파일과 화면
    for site in ("pangyo", "changwon"):
        srv, h, sec = server(W, site, env, W0 / f"server_{site}.log")
        S[f"server_{site}"] = dict(cmd="sh run_linux_mac.sh" + ("" if site == "pangyo" else " changwon"), rc=0 if h else 1, sec=sec, health=h,
                                   out=(W0 / f"server_{site}.log").read_text(encoding="utf-8").replace(str(W), "~/dpo"))
        if h and not a.skip_ui:
            out_dir = W0 / f"acc_{site}"
            r = run(["node", str(ROOT / "scripts" / "client_acceptance.js"), f"http://127.0.0.1:{PORT}", str(out_dir), str(W0 / "pilot2_like.kmz"), site],
                    W0, dict(env, NODE_PATH=subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()),
                    show=f"3D 화면 인수 시험 ({site})") if _make_tpl(W0, bin_) else None
            acc = json.loads((out_dir / f"acceptance_{site}.json").read_text(encoding="utf-8")) if r and (out_dir / f"acceptance_{site}.json").exists() else None
            log["acceptance"][site] = acc or dict(ok=False, error=(r or {}).get("out", "")[-800:])
        stop(srv)
    # 6. 명령줄 (매뉴얼 6장)
    S["plan"] = run([str(bin_ / "dpo-plan"), "--drone", "m4t", "--payload", "0.2", "--dep-hour", "15", "--out", "missions"], W, venv_env,
                    show="dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --out missions")
    S["plan_wpml"] = run([str(bin_ / "dpo-plan"), "--drone", "m4t", "--payload", "0.2", "--dep-hour", "15", "--one-way", "--wpml-template",
                          "pilot2_m4t.kmz", "--out", "missions"], W, venv_env,
                         show="dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --one-way --wpml-template pilot2_m4t.kmz --out missions")
    S["files"] = run("ls missions", W, venv_env, show="ls missions")
    S["bad_input"] = run([str(bin_ / "dpo-plan"), "--drone", "m4t", "--payload", "0.5"], W, venv_env, show="dpo-plan --drone m4t --payload 0.5")
    S["bad_input"]["rc_expected"] = 2
    ok_steps = all(v.get("rc", 0) in (0, 4) for k, v in S.items() if k != "bad_input") and S["bad_input"]["rc"] == 2
    ok_ui = a.skip_ui or all(v.get("ok") for v in log["acceptance"].values())
    log["ok"] = bool(ok_steps and ok_ui)
    (ROOT / "docs" / "manual_img" / "install_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in S.items():
        print(f"[{k}] rc={v.get('rc')} {v.get('sec', '')} s :: {v.get('cmd')}")
    for site, v in log["acceptance"].items():
        print(f"[화면 {site}] {'통과' if v.get('ok') else '실패'}", [(s['id'], s['ok']) for s in v.get("steps", [])], v.get("errors"))
    print("통과" if log["ok"] else "실패", "→ docs/manual_img/install_log.json")
    return 0 if log["ok"] else 1


def _make_tpl(W0: Path, bin_: Path) -> bool:
    """T5 시험용 'Pilot 2 가 내보낸 파일' 흉내 (기종 번호 99-1, 카메라 89). 진짜 M4T 파일은 업체 회신(R2)으로 받는다."""
    if (W0 / "pilot2_like.kmz").exists():
        return True
    code = r'''
import io, sys, zipfile
from dpo import wpml as WP
wps = [dict(lat=37.40, lon=127.10, height=30.0, speed=10.0, hover=0.0), dict(lat=37.401, lon=127.101, height=40.0, speed=12.0, hover=0.0)]
src = WP.build(wps, model="m30")["kmz"]; buf = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(src)) as zi, zipfile.ZipFile(buf, "w") as zo:
    for n in zi.namelist():
        t = zi.read(n).decode("utf-8")
        t = (t.replace("<wpml:droneEnumValue>67<", "<wpml:droneEnumValue>99<").replace("<wpml:droneSubEnumValue>0<", "<wpml:droneSubEnumValue>1<")
              .replace("<wpml:payloadEnumValue>52<", "<wpml:payloadEnumValue>89<"))
        zo.writestr(n, t)
for p in sys.argv[1:]:
    open(p, "wb").write(buf.getvalue())
'''
    W = W0 / NAME
    r = subprocess.run([str(bin_ / "python"), "-I", "-c", code, str(W0 / "pilot2_like.kmz"), str(W / "pilot2_m4t.kmz")], capture_output=True, text=True)
    return r.returncode == 0


if __name__ == "__main__":
    sys.exit(main())
