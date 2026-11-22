"""배포판 설치 시험: 빈 폴더에서 새 가상환경을 만들어 받은 사람과 똑같이 설치·점검·실행·계획해 보고 기록을 남긴다.

    python scripts/release_test.py <빈 작업 폴더> [--port 8781] [--keep-server]

  1. python -m venv dpo-env
  2. pip install "drone_path_opt-<판>-py3-none-any.whl[mavlink]"
  3. 자료 묶음 풀기 (unzip)
  4. dpo-plan --check
  5. dpo-server (서버 준비 시간, /api/health)
  6. dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --out missions
  7. dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --one-way --wpml m30 --out missions
기록: docs/manual_img/install_log.json (매뉴얼의 터미널 화면과 시간 표가 이 기록을 쓴다).
--keep-server 이면 서버를 켜 둔 채 끝낸다 (이어서 scripts/capture_manual.js 로 화면을 찍을 때).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__ as VER  # noqa: E402

WHL = f"drone_path_opt-{VER}-py3-none-any.whl"
DATA = f"dpo_data_{VER}.zip"


def run(cmd, cwd, env=None, timeout=900, show=None):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str))
    dt = time.time() - t0
    out = (p.stdout + p.stderr).replace(str(cwd), "~/dpo")
    return dict(cmd=show or (cmd if isinstance(cmd, str) else " ".join(cmd)), rc=p.returncode, sec=round(dt, 1), out=out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--port", type=int, default=8781)
    ap.add_argument("--keep-server", action="store_true")
    a = ap.parse_args()
    W = Path(a.work).resolve()
    W.mkdir(parents=True, exist_ok=True)
    if any(W.iterdir()):
        sys.exit(f"빈 폴더가 아닙니다: {W}")
    for f in (WHL, DATA):
        shutil.copy2(ROOT / "dist" / f, W / f)
    log = dict(version=VER, steps={})
    env0 = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DPO_HOME", "VIRTUAL_ENV")}
    S = log["steps"]
    S["venv"] = run([sys.executable, "-m", "venv", "dpo-env"], W, env0, show="python3 -m venv dpo-env")
    bin_ = W / "dpo-env" / "bin"
    env = dict(env0, PATH=f"{bin_}:{env0.get('PATH', '')}", VIRTUAL_ENV=str(W / "dpo-env"))
    S["pip"] = run([str(bin_ / "pip"), "install", "-q", f"{WHL}[mavlink]"], W, env, show=f'pip install "{WHL}[mavlink]"')
    S["unzip"] = run(["unzip", "-q", DATA], W, env, show=f"unzip {DATA}")
    S["ls"] = run("ls", W, env, show="ls")
    S["check"] = run([str(bin_ / "dpo-plan"), "--check"], W, env, show="dpo-plan --check")
    # 서버
    t0 = time.time()
    srv = subprocess.Popen([str(bin_ / "dpo-server"), "--port", str(a.port)], cwd=W, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    health = None
    for _ in range(600):
        try:
            health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/api/health", timeout=2).read())
            break
        except Exception:
            if srv.poll() is not None:
                break
            time.sleep(0.2)
    S["server"] = dict(cmd=f"dpo-server --port {a.port}", sec=round(time.time() - t0, 1), health=health, rc=0 if health else 1)
    S["plan"] = run([str(bin_ / "dpo-plan"), "--drone", "m4t", "--payload", "0.2", "--dep-hour", "15", "--out", "missions"], W, env,
                    show="dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --out missions")
    S["plan_wpml"] = run([str(bin_ / "dpo-plan"), "--drone", "m4t", "--payload", "0.2", "--dep-hour", "15", "--one-way", "--wpml", "m30",
                          "--out", "missions"], W, env, show="dpo-plan --drone m4t --payload 0.2 --dep-hour 15 --one-way --wpml m30 --out missions")
    S["files"] = run("ls missions", W, env, show="ls missions")
    log["ok"] = all(v.get("rc", 0) in (0, 4) for v in S.values())
    (ROOT / "docs" / "manual_img" / "install_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in S.items():
        print(f"[{k}] rc={v.get('rc')} {v.get('sec', '')} s :: {v.get('cmd')}")
    print("통과" if log["ok"] else "실패", "→ docs/manual_img/install_log.json")
    if a.keep_server and health:
        print(f"서버 켜 둠: http://127.0.0.1:{a.port}  (pid {srv.pid})")
        return 0 if log["ok"] else 1
    srv.terminate()
    return 0 if log["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
