# -*- coding: utf-8 -*-
"""curl2py 桌面版安全性自检（仅标准库）。

覆盖：监听范围、鉴权、脚本运行器沙箱边界、路径穿越、体积限制、
MCP 隔离、收藏夹签名/票据/爆破锁定、安全响应头、跨站读取。

用法：python sec.py --port 55657 --token xxx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from http.client import HTTPConnection

PORT = 0
TOKEN = ""
PASS, FAIL, WARN = [], [], []


def conn():
    return HTTPConnection("127.0.0.1", PORT, timeout=20)


def req(method, path, body=None, headers=None, ctype="application/json"):
    c = conn()
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Type"] = ctype
        h["Content-Length"] = str(len(data))
    try:
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        c.close()


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def warn(name, detail):
    WARN.append(f"{name} — {detail}")
    print(f"  WARN  {name}  [{detail}]")


def signed(path, extra=None, ts=None, secret="GGB"):
    ts = ts or str(int(time.time()))
    sign = hashlib.md5((secret + ts).encode()).hexdigest()
    h = {"X-Vault-Sign": sign, "X-Vault-Ts": ts, "X-API-Token": TOKEN}
    if extra:
        h.update(extra)
    return req("POST", path, {}, headers=h)


def main():
    global PORT, TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--token", required=True)
    a = ap.parse_args()
    PORT, TOKEN = a.port, a.token
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)

    print("== 1. 监听范围 ==")
    sock = socket.socket()
    sock.settimeout(3)
    try:
        sock.connect(("127.0.0.1", PORT))
        check("localhost 可连（服务正常）", True)
    except OSError as e:
        check("localhost 可连（服务正常）", False, str(e))
    finally:
        sock.close()
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        ips = []
    lan = [ip for ip in ips if not ip.startswith("127.")]
    if lan:
        s2 = socket.socket()
        s2.settimeout(2)
        try:
            s2.connect((lan[0], PORT))
            check("局域网 IP 不可直连（应只绑 127.0.0.1）", False, f"{lan[0]}:{PORT} 可连")
        except OSError:
            check("局域网 IP 不可直连（应只绑 127.0.0.1）", True, f"{lan[0]} 被拒绝")
        finally:
            s2.close()
    else:
        warn("局域网隔离", "本机无其它网卡地址，未能验证")

    print("== 2. 鉴权 ==")
    st, _, _ = req("POST", "/api/convert/curl2python", {"curl": "curl https://a.com"}, {"X-API-Token": ""})
    check("无令牌访问 /api/* 被拒", st == 401, f"HTTP {st}")
    st, _, _ = req("POST", "/api/convert/curl2python", {"curl": "curl https://a.com"}, {"X-API-Token": "wrong"})
    check("错误令牌被拒", st == 401, f"HTTP {st}")
    st, _, _ = req("GET", "/health")
    check("/health 无需令牌（前端探活需要）", st == 200, f"HTTP {st}")
    st, _, _ = req("POST", "/api/convert/curl2python", {"curl": "curl https://a.com"}, {"X-API-Token": TOKEN})
    check("正确令牌可访问", st == 200, f"HTTP {st}")

    print("== 3. 脚本运行器边界 ==")
    st, body, _ = req("POST", "/api/run/python", {"code": "print(1)"})
    check("运行脚本需令牌", st == 401, f"HTTP {st}")
    st, body, _ = req("POST", "/api/run/python",
                      {"code": "import time; time.sleep(30)"}, {"X-API-Token": TOKEN})
    d = json.loads(body) if body else {}
    timed = bool(d.get("result", {}).get("timed_out"))
    check("超时脚本被强制终止（12s 上限）", st == 200 and timed, f"HTTP {st} timed_out={timed}")
    big = "x = 1\n" * 40000  # ~200KB > 128KB 上限
    st, _, _ = req("POST", "/api/run/python", {"code": big}, {"X-API-Token": TOKEN})
    check("超长脚本被拒（128KB 上限）", st in (413, 422), f"HTTP {st}")
    st, body, _ = req("POST", "/api/run/python",
                      {"code": "import os;print(os.getcwd())"}, {"X-API-Token": TOKEN})
    cwd = json.loads(body).get("result", {}).get("stdout", "").strip() if body else ""
    ok = os.path.normcase(cwd).startswith(os.path.normcase(os.environ.get("TEMP", "\x00")))
    check("脚本 cwd 为可写临时目录（非安装目录）", ok, cwd or "无输出")

    print("== 4. 路径穿越 ==")
    for payload in ("/static/../../app.py", "/static/..%2f..%2fapp.py", "/static/%2e%2e/%2e%2e/desktop_launcher.py"):
        st, body, _ = req("GET", payload)
        leaked = b"Flask(" in body or b"def main" in body
        check(f"穿越被拦：{payload}", st in (400, 404) and not leaked, f"HTTP {st} leaked={leaked}")

    print("== 5. 体积限制 ==")
    st, _, _ = req("POST", "/api/convert/curl2python",
                   {"curl": "curl " + "a" * (300 * 1024)}, {"X-API-Token": TOKEN})
    check("超大 curl 被拒（256KB 上限）", st in (413, 422), f"HTTP {st}")

    print("== 6. MCP 隔离 ==")
    st, _, _ = signed("/api/vault/list", {"X-Client-Name": "curl2py-mcp"})
    check("MCP 客户端无法访问收藏夹", st == 403, f"HTTP {st}")

    print("== 7. 收藏夹准入 ==")
    st, _, _ = req("POST", "/api/vault/list", {}, {"X-API-Token": TOKEN,
                                                   "X-Vault-Sign": "bad", "X-Vault-Ts": str(int(time.time()))})
    check("伪造签名被拒", st == 401, f"HTTP {st}")
    old_ts = str(int(time.time()) - 3600)
    st, _, _ = signed("/api/vault/list", ts=old_ts)
    check("过期时间戳被拒（5 分钟窗口）", st == 401, f"HTTP {st}")
    st, _, _ = req("POST", "/api/vault/list", {}, {"X-API-Token": TOKEN})
    check("无票据被拒", st == 401, f"HTTP {st}")

    print("== 8. 响应头 ==")
    _, _, hdrs = req("GET", "/", None, headers={"Accept": "text/html"})
    lower = {k.lower(): v for k, v in hdrs.items()}
    check("X-Content-Type-Options: nosniff", lower.get("x-content-type-options") == "nosniff",
          lower.get("x-content-type-options", "缺失"))
    check("X-Frame-Options 存在", "x-frame-options" in lower, lower.get("x-frame-options", "缺失"))
    check("Referrer-Policy 存在", "referrer-policy" in lower, lower.get("referrer-policy", "缺失"))
    check("未开放跨域读取（无 ACAO 或仅同源）",
          lower.get("access-control-allow-origin") in (None, ""), lower.get("access-control-allow-origin", "无"))

    print("== 9. 错误处理 ==")
    st, body, _ = req("GET", "/api/does-not-exist", None, {"X-API-Token": TOKEN})
    check("未知接口返回 404 且不泄露堆栈", st == 404 and b"Traceback" not in body, f"HTTP {st}")
    st, body, _ = req("POST", "/api/convert/curl2python", {"curl": 123}, {"X-API-Token": TOKEN})
    check("非法入参返回结构化错误", st in (400, 422) and b"Traceback" not in body, f"HTTP {st}")

    print("\n===== 汇总 =====")
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)} / 提示 {len(WARN)}")
    for f in FAIL:
        print("  ✗", f)
    for w in WARN:
        print("  !", w)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
