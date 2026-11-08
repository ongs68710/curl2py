"""起后端 → 跑 bench.py → 收尾，输出干净环境下的一轮完整基准。"""
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
LABEL = sys.argv[1] if len(sys.argv) > 1 else "verified"
DURATION = sys.argv[2] if len(sys.argv) > 2 else "4"


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
    # 后端 stdout 必须持续消费：只用 PIPE 读到 READY 就停，日志会把 64KB 管道缓冲填满，
    # 后端写日志时整进程阻塞，压测后段会出现「全部请求 120s 超时」的假故障。
    log_path = os.path.join(ROOT, ".bench", f"app-{LABEL}.log")
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
        print(f"== 后端就绪于 {port}，开始压测 (label={LABEL}, {DURATION}s/场景) ==", flush=True)
        rc = subprocess.call([sys.executable, os.path.join(ROOT, ".bench", "bench.py"),
                              "--port", str(port), "--token", TOKEN,
                              "--label", LABEL, "--duration", DURATION],
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
