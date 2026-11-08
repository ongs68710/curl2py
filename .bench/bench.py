# -*- coding: utf-8 -*-
"""curl2py 桌面版后端压测 + 资源采集（仅标准库）。

用法：python bench.py --port 59491 --token xxx [--label baseline]
输出：JSON 结果 + 控制台表格
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from http.client import HTTPConnection, HTTPException
import hashlib
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

BASE = ""
TOKEN = ""
PORT = 0

_local = threading.local()


def _conn() -> "HTTPConnection":
    c = getattr(_local, "conn", None)
    if c is None:
        c = HTTPConnection("127.0.0.1", PORT, timeout=60)
        _local.conn = c
    return c


def _drop_conn() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _local.conn = None


def _install_noproxy():
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def find_pid(port: int) -> int:
    """通过 netstat 找监听该端口的 PID（避免依赖 psutil）。"""
    import re
    import subprocess

    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                return int(m.group(1))
    return 0


def proc_mem(pid: int) -> tuple[int, int]:
    """返回 (working_set, private_bytes)，单位字节。"""
    PROCESS_QUERY_LIMITED = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return (0, 0)
    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return (int(pmc.WorkingSetSize), int(pmc.PagefileUsage))
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


class ProcTimes(ctypes.Structure):
    _fields_ = [
        ("creation", wintypes.FILETIME),
        ("exit", wintypes.FILETIME),
        ("kernel", wintypes.FILETIME),
        ("user", wintypes.FILETIME),
    ]


def _ft(ft: "wintypes.FILETIME") -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def proc_cpu(pid: int) -> float:
    """累计 CPU 秒数（kernel+user）。"""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return 0.0
    try:
        t = ProcTimes()
        ctypes.windll.kernel32.GetProcessTimes(h, ctypes.byref(t.creation), ctypes.byref(t.exit),
                                              ctypes.byref(t.kernel), ctypes.byref(t.user))
        return (_ft(t.kernel) + _ft(t.user)) / 1e7
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def http(path: str, *, method="GET", body=None, headers=None, timeout=60):
    """每线程复用一条 keep-alive 连接，模拟浏览器/桌面客户端的真实行为。

    用 urllib 每次新建连接会在高并发下把 Windows 动态端口（约 1.6 万个）
    耗尽，测出来的是客户端限制而不是服务端能力。
    """
    h = {"Accept": "application/json"}
    if TOKEN:
        h["X-API-Token"] = TOKEN
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        if "Content-Type" not in h:
            h["Content-Type"] = "application/json"
        h["Content-Length"] = str(len(data))

    for attempt in (0, 1):
        conn = _conn()
        try:
            conn.request(method, path, body=data, headers=h)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload
        except (HTTPException, OSError):
            _drop_conn()
            if attempt:
                raise
    raise RuntimeError("unreachable")


def multipart(fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----c2pbench" + hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
    buf = b""
    for k, v in fields.items():
        buf += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, filename, content in files:
        buf += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + b"\r\n"
    buf += f"--{boundary}--\r\n".encode()
    return buf, f"multipart/form-data; boundary={boundary}"


# ---------------- 场景 ----------------

def make_curl(big: bool) -> str:
    if not big:
        return 'curl "https://example.com/api/v1/items?page=1&size=20" -H "Accept: application/json"'
    headers = " ".join(f'-H "X-Header-{i}: value-{i}-{"x" * 40}"' for i in range(60))
    cookies = "; ".join(f"k{i}=v{i}" for i in range(80))
    payload = json.dumps({"items": [{"id": i, "name": f"n{i}"} for i in range(120)]})
    return f'curl "https://example.com/api/v2/search?q=test&page=3" {headers} -b "{cookies}" -d \'{payload}\''


def make_png(size_kb: int) -> bytes:
    """生成一张不压缩的 PNG（用随机像素，避免被 gzip 压没）。"""
    import zlib
    import struct

    w = h = int((size_kb * 1024 / 3) ** 0.5) or 16
    raw = b""
    seed = hashlib.sha256(b"c2p").digest()
    for y in range(h):
        raw += b"\x00" + bytes((seed[(x + y) % 32] * 7 + x) % 256 for x in range(w * 3))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b"")


class Scenario:
    def __init__(self, name, fn, weight=1):
        self.name = name
        self.fn = fn
        self.weight = weight


def build_scenarios():
    small_curl = make_curl(False)
    big_curl = make_curl(True)
    png = make_png(700)
    text4k = ("curl2py benchmark " * 220)[:4096]

    def s_convert_small():
        return http("/api/convert/curl2python", method="POST", body={"curl": small_curl})

    def s_convert_big():
        return http("/api/convert/curl2feapder", method="POST", body={"curl": big_curl})

    def s_hash():
        return http("/api/crypto/hash", method="POST", body={"text": text4k, "algo": "sha256"})

    def s_image():
        body, ctype = multipart({"output": "array"}, [("files", "bench.png", png)])
        return http("/api/image/base64", method="POST", body=body, headers={"Content-Type": ctype})

    def s_static():
        return http("/static/js/app.js", headers={"Accept-Encoding": "gzip"})

    def s_index():
        return http("/", headers={"Accept": "text/html"})

    def s_vault_list():
        return http("/api/vault/list", method="POST", body={})

    return [
        Scenario("convert_python", s_convert_small),
        Scenario("convert_feapder_30k", s_convert_big),
        Scenario("hash_sha256_4k", s_hash),
        Scenario("image_b64_700k", s_image),
        Scenario("static_appjs", s_static),
        Scenario("index_html", s_index),
        Scenario("vault_list", s_vault_list),
    ]


def run_one(sc: Scenario) -> tuple[float, bool, int]:
    t0 = time.perf_counter()
    try:
        status, _ = sc.fn()
        ok = 200 <= status < 300
    except urllib.error.HTTPError as e:
        status, ok = e.code, False
    except Exception:
        status, ok = 0, False
    return (time.perf_counter() - t0) * 1000, ok, status


def percentiles(vals: list[float]) -> dict:
    if not vals:
        return {}
    vals = sorted(vals)
    def q(p):
        idx = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        return round(vals[idx], 2)
    return {
        "p50": q(0.50), "p90": q(0.90), "p95": q(0.95), "p99": q(0.99),
        "max": round(vals[-1], 2), "mean": round(statistics.fmean(vals), 2),
    }


def bench(sc: Scenario, concurrency: int, duration: float, pid: int, stop_flag) -> dict:
    lat, errs = [], 0
    statuses = {}
    mem_samples = []
    deadline = time.perf_counter() + duration
    count = 0

    def worker():
        nonlocal count, errs
        while time.perf_counter() < deadline and not stop_flag.is_set():
            ms, ok, st = run_one(sc)
            lat.append(ms)
            count += 1
            statuses[st] = statuses.get(st, 0) + 1
            if not ok:
                errs += 1

    def sampler():
        while time.perf_counter() < deadline and not stop_flag.is_set():
            mem_samples.append(proc_mem(pid)[0])
            time.sleep(0.25)

    cpu0 = proc_cpu(pid)
    t0 = time.perf_counter()
    st = threading.Thread(target=sampler, daemon=True)
    st.start()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for _ in range(concurrency):
            ex.submit(worker)
    elapsed = time.perf_counter() - t0
    cpu = proc_cpu(pid) - cpu0

    return {
        "scenario": sc.name,
        "concurrency": concurrency,
        "requests": count,
        "errors": errs,
        "elapsed_s": round(elapsed, 2),
        "rps": round(count / elapsed, 1),
        "latency_ms": percentiles(lat),
        "status": {str(k): v for k, v in sorted(statuses.items())},
        "cpu_pct": round(cpu / elapsed * 100, 1),
        "rss_peak_mb": round(max(mem_samples) / 1048576, 1) if mem_samples else 0,
        "rss_avg_mb": round(statistics.fmean(mem_samples) / 1048576, 1) if mem_samples else 0,
    }


def main():
    global BASE, TOKEN, PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--token", default="")
    ap.add_argument("--label", default="run")
    ap.add_argument("--duration", type=float, default=6.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    _install_noproxy()
    PORT = args.port
    BASE = f"http://127.0.0.1:{args.port}"
    TOKEN = args.token
    pid = find_pid(args.port)
    if not pid:
        print("找不到监听端口的进程", file=sys.stderr)
        return 2

    idle_ws, idle_priv = proc_mem(pid)
    print(f"后端 PID={pid} 空闲内存 工作集={idle_ws/1048576:.1f}MB 私有={idle_priv/1048576:.1f}MB")

    # 预热 + 收藏夹准备（vault_list 需要解锁）
    http("/health")
    try:
        http("/api/vault/init", method="POST", body={"password": "BenchPass123"})
    except Exception:
        pass
    ts = str(int(time.time()))
    sign = hashlib.md5(("GGB" + ts).encode()).hexdigest()

    scenarios = build_scenarios()
    # vault_list 需要签名头
    for sc in scenarios:
        if sc.name == "vault_list":
            sign_h = {"X-Vault-Sign": sign, "X-Vault-Ts": ts}
            try:
                st, body = http("/api/vault/unlock", method="POST", body={"password": "BenchPass123"})
                vt = json.loads(body).get("token", "")
                sign_h["X-Vault-Token"] = vt
            except Exception:
                pass
            sc.fn = (lambda h=sign_h: http("/api/vault/list", method="POST", body={}, headers=h))

    results = []
    for conc in (1, 8, 32):
        for sc in scenarios:
            stop = threading.Event()
            r = bench(sc, conc, args.duration, pid, stop)
            results.append(r)
            print(f"  {r['scenario']:<20} c={conc:<3} rps={r['rps']:<8} "
                  f"p50={r['latency_ms'].get('p50')}ms p99={r['latency_ms'].get('p99')}ms "
                  f"err={r['errors']} cpu={r['cpu_pct']}% rss={r['rss_peak_mb']}MB")

    ws_after, priv_after = proc_mem(pid)
    summary = {
        "label": args.label,
        "pid": pid,
        "idle_working_set_mb": round(idle_ws / 1048576, 1),
        "idle_private_mb": round(idle_priv / 1048576, 1),
        "after_working_set_mb": round(ws_after / 1048576, 1),
        "after_private_mb": round(priv_after / 1048576, 1),
        "results": results,
    }
    out = args.out or f"bench-{args.label}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n低负载空闲 {summary['idle_working_set_mb']}MB → 压测后 {summary['after_working_set_mb']}MB")
    print("结果已写入", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
