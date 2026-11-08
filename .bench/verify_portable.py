"""验证绿色版单文件 exe：在空目录内启动，确认 curl2py-data 生成在 exe 同级目录。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "dist", "curl2py-0.5.3-win-x64-portable.exe")
BASE = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "c2p-portable-test")
DIR = os.path.join(BASE, "greenrun")
PORT = 9333


def find_listening(pid: int) -> list[int]:
    """用 netstat 找该 pid 的监听端口（便携版不带 CDP 参数时也能判断后端是否起来）。"""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         encoding="mbcs", errors="replace").stdout
    ports = []
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        cols = line.split()
        if len(cols) < 5 or cols[-1] != str(pid):
            continue
        local = cols[1]
        try:
            ports.append(int(local.rsplit(":", 1)[1]))
        except (IndexError, ValueError):
            pass
    return ports


def all_pids_named(name: str) -> list[int]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         encoding="mbcs", errors="replace").stdout
    pids = []
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[0].lower() == name.lower():
            try:
                pids.append(int(cols[1]))
            except ValueError:
                pass
    return pids


def main():
    if not os.path.exists(SRC):
        print(f"!! 找不到 {SRC}")
        return 1
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(DIR, exist_ok=True)
    target = os.path.join(DIR, os.path.basename(SRC))
    shutil.copy2(SRC, target)
    print(f"沙箱目录: {DIR}")
    print(f"复制绿色版: {os.path.basename(target)} ({os.path.getsize(target)/1048576:.1f} MB)")
    print(f"启动前目录内容: {os.listdir(DIR)}")

    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    before_e = set(all_pids_named("curl2py.exe"))
    t0 = time.time()
    log = open(os.path.join(BASE, "launch.log"), "wb")
    proc = subprocess.Popen([target], cwd=DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    print(f"launched pid={proc.pid}，等待解包与后端就绪 ...", flush=True)

    data_dir = os.path.join(DIR, "curl2py-data")
    ready = None
    port = None
    while time.time() - t0 < 180:
        if os.path.isdir(data_dir) and os.listdir(data_dir):
            ready = time.time() - t0
            break
        time.sleep(0.5)

    if ready is None:
        print("!! 180s 内未生成 curl2py-data")
        print(f"目录内容: {os.listdir(DIR)}")
    else:
        print(f"curl2py-data 出现: {ready:.1f}s")
        print(f"目录内容: {os.listdir(DIR)}")
        print(f"curl2py-data 内: {os.listdir(data_dir)}")

    # 便携版解包后才会拉起真正的 curl2py.exe 与后端，这里轮询等端口出现
    print("等待后端监听端口 ...", flush=True)
    before_e = set()  # 便携版子进程可能是新 exe，全部纳入扫描
    found = {}
    port = None
    http_ok = False
    deadline = time.time() + 90
    while time.time() < deadline:
        cand = set(all_pids_named("curl2py.exe")) | set(all_pids_named("python.exe"))
        for p in cand:
            for lp in find_listening(p):
                found[lp] = p
        for lp in list(found):
            if port is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{lp}/", timeout=2) as r:
                    body = r.read(4000).decode("utf-8", "replace")
                if r.status == 200 and "curl2py" in body.lower():
                    http_ok, port = True, lp
                    break
            except Exception:
                pass
        if port is not None:
            break
        time.sleep(0.6)

    if port:
        print(f"HTTP 200 于 127.0.0.1:{port}（首页含 curl2py）")
    else:
        print(f"监听端口样本: {dict(list(found.items())[:8]) or '未发现'}")
        print(f"总耗时 {round(time.time()-t0,1)}s")
        # 附带后端日志便于判断
        prof = os.path.join(DIR, "curl2py-data", "profile", "backend.log")
        if os.path.exists(prof):
            with open(prof, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-1200:]
            print(f"backend.log 尾部:\n{tail}")

    # 写一个标记文件，确认数据目录可写
    probe = os.path.join(data_dir, "portable-probe.txt")
    writable = False
    if os.path.isdir(data_dir):
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            writable = os.path.exists(probe)
        except OSError as e:
            print(f"  数据目录不可写: {e}")

    result = {
        "portable_exe_mb": round(os.path.getsize(target) / 1048576, 1),
        "data_dir_created_s": round(ready, 1) if ready else None,
        "data_dir_path": data_dir,
        "dir_listing": os.listdir(DIR),
        "data_listing": os.listdir(data_dir) if os.path.isdir(data_dir) else [],
        "backend_port": port,
        "http_ok": http_ok,
        "data_dir_writable": writable,
        "total_launch_s": round(time.time() - t0, 1),
    }
    out = os.path.join(ROOT, ".bench", "portable.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"写入 {out}")

    victims = {proc.pid} | set(found.values())
    for p in victims:
        subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
    log.close()
    print(f"退出码判定: data_dir={'OK' if ready else 'FAIL'} http={'OK' if http_ok else 'FAIL'} "
          f"writable={'OK' if writable else 'FAIL'}")
    return 0 if (ready and http_ok and writable) else 2


if __name__ == "__main__":
    sys.exit(main())
