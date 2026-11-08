"""起后端 → 跑 sec.py → 收尾。用于最终产物回归。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "runtime", "python.exe")
LAUNCHER = os.path.join(ROOT, "desktop_launcher.py")
TOKEN = "bench-token-0123456789abcdef"


def main():
    env = dict(os.environ)
    env.update({
        "CURL2PY_API_TOKEN": TOKEN,
        "ENABLE_SCRIPT_RUNNER": "true",
        "CURL2PY_DATA_DIR": os.path.join(ROOT, ".bench", "data"),
        "CURL2PY_RUNTIME_DIR": os.path.join(ROOT, ".bench", "rt"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    log_path = os.path.join(ROOT, ".bench", "app-sec.log")
    log = open(log_path, "wb")
    proc = subprocess.Popen([PY, LAUNCHER], cwd=os.path.dirname(LAUNCHER), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    port = None
    t0 = time.time()
    try:
        while time.time() - t0 < 90:
            time.sleep(0.2)
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    m = re.search(r"CURL2PY_READY port=(\d+)", f.read())
            except OSError:
                m = None
            if m:
                port = int(m.group(1))
                break
        if not port:
            print("!! 后端未就绪")
            return 1
        print(f"== 后端就绪于 {port}，开始安全自检 ==", flush=True)
        rc = subprocess.call([sys.executable, os.path.join(ROOT, ".bench", "sec.py"),
                              "--port", str(port), "--token", TOKEN],
                             cwd=ROOT)
        return rc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
