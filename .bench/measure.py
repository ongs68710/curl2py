"""打包产物启动时间与内存占用测量。

隔离 user-data-dir 避免与用户已安装实例的单实例锁冲突；
用 EnumProcesses + GetProcessTimes 按创建时间过滤"本次启动"的进程，
并排除测量脚本自身；再用一次 WMI 快照给每个 curl2py.exe 标注 --type。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "win-unpacked", "curl2py.exe")
DATA_DIR = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "c2p-measure")
PORT = 9223

psapi = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PQLI = 0x1000
PVM_READ = 0x0010


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def enum_pids():
    arr = (wt.DWORD * 4096)()
    need = wt.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(need)):
        return []
    return [arr[i] for i in range(need.value // ctypes.sizeof(wt.DWORD))]


def proc_info(pid: int):
    h = kernel32.OpenProcess(PQLI | PVM_READ, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        name = os.path.basename(buf.value).lower()
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        ws = pv = 0.0
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            ws, pv = c.WorkingSetSize / 1048576, c.PrivateUsage / 1048576
        ct, et, kt, ut = (wt.FILETIME() for _ in range(4))
        created = 0.0
        if kernel32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                                    ctypes.byref(kt), ctypes.byref(ut)):
            created = ((ct.dwHighDateTime << 32) | ct.dwLowDateTime) / 1e7 - 11644473600
        return name, created, round(ws, 1), round(pv, 1)
    finally:
        kernel32.CloseHandle(h)


def wmi_types() -> dict[int, str]:
    """pid -> Electron 进程类型（browser/gpu/utility/network/renderer/python）。"""
    ps1 = os.path.join(os.environ.get("TEMP", "."), "c2p-types.ps1")
    out = os.path.join(os.environ.get("TEMP", "."), "c2p-types.json")
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','curl2py.exe') } |"
        "Select-Object ProcessId,CommandLine |"
        f"ConvertTo-Json -Depth 2 -Compress | Out-File -FilePath '{out}' -Encoding utf8"
    )
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", ps1],
                   capture_output=True, timeout=120)
    res = {}
    try:
        with open(out, "r", encoding="utf-8-sig") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows:
            cl = r.get("CommandLine") or ""
            t = "browser"
            for k in ("gpu-process", "utility-sub-type=network", "utility",
                      "renderer", "desktop_launcher"):
                if k in cl:
                    t = {"gpu-process": "gpu", "utility-sub-type=network": "network",
                         "utility": "utility", "renderer": "renderer",
                         "desktop_launcher": "python-backend"}[k]
                    break
            res[r.get("ProcessId")] = t
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    finally:
        for p in (ps1, out):
            try:
                os.remove(p)
            except OSError:
                pass
    return res


def page_ready(timeout=90.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1.0) as r:
                items = json.loads(r.read().decode("utf-8", "replace"))
            for it in items:
                if it.get("type") == "page" and it.get("url", "").startswith("http://127.0.0.1"):
                    return round(time.time() - t0, 2), it["url"]
        except Exception:
            pass
        time.sleep(0.1)
    return None, None


def snapshot(cutoff):
    electron, python = [], []
    me = os.getpid()
    for pid in enum_pids():
        if pid == me:
            continue
        info = proc_info(pid)
        if not info:
            continue
        name, created, ws, pv = info
        if created < cutoff:
            continue
        row = {"pid": pid, "ws_mb": ws, "private_mb": pv}
        if name == "curl2py.exe":
            electron.append(row)
        elif name == "python.exe":
            python.append(row)
    return electron, python


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)

    print(f"exe      : {EXE}")
    print(f"userData : {DATA_DIR}", flush=True)

    log = open(os.path.join(ROOT, ".bench", "measure-app.log"), "wb")
    extra = os.environ.get("C2P_EXTRA", "").split()
    if extra:
        print(f"额外参数 : {' '.join(extra)}")
    t_launch = time.time()
    proc = subprocess.Popen(
        [EXE, f"--user-data-dir={DATA_DIR}", f"--remote-debugging-port={PORT}", *extra],
        cwd=os.path.dirname(EXE), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    print(f"launched pid={proc.pid}, 等待页面可用 ...", flush=True)

    dt, url = page_ready()
    if dt is None:
        print("!! 页面未在 90s 内就绪")
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return 1
    print(f"启动到页面可用: {dt}s  url={url}", flush=True)

    cutoff = t_launch - 1.5
    time.sleep(5.0)  # 等子进程全部拉起并稳定
    e1, p1 = snapshot(cutoff)
    types = wmi_types()

    def agg(rows):
        return round(sum(r["ws_mb"] for r in rows), 1), round(sum(r["private_mb"] for r in rows), 1)

    e_ws, e_pv = agg(e1)
    p_ws, p_pv = agg(p1)

    print(f"\n[启动 5s 后]")
    print(f"Electron {len(e1)} 进程: 工作集 {e_ws} MB / 私有 {e_pv} MB")
    for r in sorted(e1, key=lambda x: -x["ws_mb"]):
        print(f"    {types.get(r['pid'], '?'):<14} pid {r['pid']:>6}  "
              f"ws {r['ws_mb']:>7} MB  private {r['private_mb']:>7} MB")
    print(f"Python   {len(p1)} 进程: 工作集 {p_ws} MB / 私有 {p_pv} MB")
    for r in sorted(p1, key=lambda x: -x["ws_mb"]):
        print(f"    {'python-backend':<14} pid {r['pid']:>6}  "
              f"ws {r['ws_mb']:>7} MB  private {r['private_mb']:>7} MB")
    print(f"合计: 工作集 {round(e_ws + p_ws, 1)} MB / 私有 {round(e_pv + p_pv, 1)} MB")

    # 空闲 30s 后再测一次，看是否回落
    print("\n空闲 30s 复测 ...", flush=True)
    time.sleep(30.0)
    e2, p2 = snapshot(cutoff)
    e2_ws, e2_pv = agg(e2)
    p2_ws, p2_pv = agg(p2)
    print(f"Electron {len(e2)} 进程: 工作集 {e2_ws} MB / 私有 {e2_pv} MB")
    print(f"Python   {len(p2)} 进程: 工作集 {p2_ws} MB / 私有 {p2_pv} MB")
    print(f"合计: 工作集 {round(e2_ws + p2_ws, 1)} MB / 私有 {round(e2_pv + p2_pv, 1)} MB")

    for r in e1 + e2:
        r["type"] = types.get(r["pid"], "?")
    for r in p1 + p2:
        r["type"] = "python-backend"

    result = {
        "startup_to_page_ready_s": dt,
        "page_url": url,
        "boot_5s": {"electron_procs": e1, "python_procs": p1,
                    "electron_ws_mb": e_ws, "electron_private_mb": e_pv,
                    "python_ws_mb": p_ws, "python_private_mb": p_pv,
                    "total_ws_mb": round(e_ws + p_ws, 1),
                    "total_private_mb": round(e_pv + p_pv, 1)},
        "idle_35s": {"electron_procs": e2, "python_procs": p2,
                     "electron_ws_mb": e2_ws, "electron_private_mb": e2_pv,
                     "python_ws_mb": p2_ws, "python_private_mb": p2_pv,
                     "total_ws_mb": round(e2_ws + p2_ws, 1),
                     "total_private_mb": round(e2_pv + p2_pv, 1)},
    }
    out = os.path.join(ROOT, ".bench", "measure.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n写入 {out}")

    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
