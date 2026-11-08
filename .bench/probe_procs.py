"""辅助：查看本次启动产生的 python.exe / curl2py.exe 命令行，确认进程归属。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "win-unpacked", "curl2py.exe")
DATA_DIR = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "c2p-measure")
PS1 = os.path.join(ROOT, ".bench", "ps-dump.ps1")
OUT = os.path.join(ROOT, ".bench", "ps-dump.json")

SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('python.exe','curl2py.exe') } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate |
  ConvertTo-Json -Depth 3 -Compress | Out-File -FilePath '__OUT__' -Encoding utf8
"""

def dump():
    src = SCRIPT.replace("__OUT__", OUT.replace("\\", "\\\\"))
    with open(PS1, "w", encoding="utf-8-sig") as f:
        f.write(src)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", PS1],
                   capture_output=True, timeout=120)
    if not os.path.exists(OUT):
        return []
    with open(OUT, "r", encoding="utf-8-sig") as f:
        try:
            rows = json.load(f)
        except json.JSONDecodeError:
            return []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    t0 = time.time()
    proc = subprocess.Popen(
        [EXE, f"--user-data-dir={DATA_DIR}", "--remote-debugging-port=9224"],
        cwd=os.path.dirname(EXE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(7.0)
        rows = dump()
        print(f"启动 {round(time.time()-t0,1)}s 后进程快照（{len(rows)} 条）:")
        for r in rows:
            print(f"  pid={r.get('ProcessId')} ppid={r.get('ParentProcessId')} "
                  f"name={r.get('Name')} created={r.get('CreationDate')}")
            cl = (r.get("CommandLine") or "").strip()
            print(f"      cmd: {cl[:220]}")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        for f in (PS1, OUT):
            try:
                os.remove(f)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
