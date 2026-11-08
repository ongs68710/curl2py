#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curl2py 工具箱 MCP server。

传输：
* stdio（默认）：按行分隔的 JSON-RPC 2.0，供宿主机 MCP 客户端拉起；
* http（--transport http）：Streamable HTTP，POST /mcp 收发 JSON-RPC，
  供局域网其他设备（群晖、手机、同事的 Cursor）直连，容器内也能跑。
依赖：仅标准库（armv7 容器与本机都无外网，装不了官方 mcp SDK）。

设计取舍：
* stdout 是协议通道，任何日志一律走 stderr；
* 对比类接口默认只回统一 diff + 统计，visual_lines 是给浏览器双栏渲染用的，
  原样返回会撑爆模型上下文；
* 脚本执行默认不注册，需 --allow-script-run 且后端已配置 Token；
* HTTP 模式下服务端不主动推送，GET /mcp 返回 405（Streamable HTTP 允许如此）。
"""
import argparse
import base64
import http.client
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
SERVER_NAME = "curl2py-toolbox"
SERVER_VERSION = "1.5.0"

INSTRUCTIONS = (
    "curl2py 工具箱：curl 转 Python/Feapder、JSON/Header/Cookie 格式化、URL 编解码、"
    "摘要与对称加解密、文本/JSON 对比、时间戳转换。"
    "不确定算法名时先调 crypto_operations；对比类默认只回统一 diff 与统计，"
    "确实需要逐行明细再传 detail=lines；调用失败时读返回文本里的原因，不要盲目重试。"
    "实用约定：单段文本上限 1MB、对比单侧 512KB、curl 256KB，超了先截断或分段；"
    "时间戳默认按秒，毫秒要显式传 unit=ms；curl 转代码直接贴浏览器里复制的原始 curl，"
    "不用自己整理格式；JSON 压缩用 compact=true，indent=0 只是不缩进、不压缩。"
)

TEXT_MAX_BYTES = 1 * 1024 * 1024
CURL_MAX_BYTES = 256 * 1024
DIFF_SIDE_MAX_BYTES = 512 * 1024
MAX_OUTPUT_CHARS = 120_000
MAX_DIFF_LINES = 800
FILE_MAX_BYTES = 5 * 1024 * 1024      # 与后端 IMAGE_MAX_BYTES 保持一致
HTTP_MAX_BODY = 8 * 1024 * 1024
MAX_BATCH_ITEMS = 32                  # 单个 POST 里的 JSON-RPC 批条数上限
DEFAULT_MAX_CONCURRENCY = 64          # 同时处理的连接数上限，超出直接 503
REQUEST_QUEUE_SIZE = 128              # listen backlog；默认 5 在并发 16 就开始 RST
IDLE_TIMEOUT_S = 30                   # keep-alive 空闲连接回收

# 后端 /api/crypto/catalog 实测支持的算法（crypto_operations 返回同源清单）。
# 写进 schema 的 enum 是为了让模型不用猜——猜错一次就是一轮 422 往返。
DIGEST_ALGORITHMS = [
    "md5", "sha1", "sha224", "sha256", "sha384", "sha512",
    "sha3-224", "sha3-256", "sha3-384", "sha3-512", "ripemd160", "sm3",
]
SYMMETRIC_ALGORITHMS = [
    "AES", "Blowfish", "DES", "TripleDES", "RC2", "RC4", "RC4Drop",
    "ChaCha20", "Rabbit", "SM4",
]
HMAC_ALGORITHMS = [
    "HmacMD5", "HmacSHA1", "HmacSHA224", "HmacSHA256", "HmacSHA384", "HmacSHA512",
]
CRYPTO_ALGORITHMS = SYMMETRIC_ALGORITHMS + HMAC_ALGORITHMS + [a.upper() for a in DIGEST_ALGORITHMS]

# 后端 services/crypto_tools.py 的 MODES / PADDINGS / 编码组，逐个抄自源码。
# 写进 enum 是为了让模型一次就选对：自由字符串每猜错一次就是一轮 422 往返。
BLOCK_MODES = ["CBC", "ECB", "CFB", "OFB", "CTR"]
PADDING_MODES = ["Pkcs7", "Iso97971", "AnsiX923", "Iso10126", "ZeroPadding", "NoPadding"]
ENCODING_FORMATS = ["Utf8", "Utf16", "Utf16LE", "Latin1", "Hex", "Base64", "Base64 URL Safe"]

# 工具的人类可读名，客户端列表里显示在 name 旁边
TOOL_TITLES = {
    "toolbox_health": "后端体检",
    "toolbox_catalog": "能力目录",
    "json_format": "JSON 格式化",
    "header_to_dict": "请求头转字典",
    "cookie_to_dict": "Cookie 转字典",
    "python_dict_parse": "Python 字典转 JSON",
    "javascript_clean": "JS 去噪压缩",
    "html_clean": "HTML 去空行",
    "url_parse": "URL 拆解",
    "url_encode": "URL 编码",
    "url_decode": "URL 解码",
    "curl_to_python": "curl 转 requests",
    "curl_to_feapder": "curl 转 feapder",
    "base64": "Base64 编解码",
    "hash": "消息摘要",
    "crypto_operations": "加解密清单",
    "crypto_process": "对称加解密",
    "text_diff": "文本对比",
    "json_diff": "JSON 对比",
    "timestamp_to_date": "时间戳转日期",
    "date_to_timestamp": "日期转时间戳",
    "run_python": "执行 Python",
    "file_to_base64": "文件转 Base64",
}

# 工具注解（MCP 2025-06-18）。这 21 个工具全是无副作用的纯变换，
# 除 run_python 外都不写环境、不访问外部实体，只惩罚性地标成 openWorld。
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
SCRIPT_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def log(message):
    print(f"[curl2py-mcp] {message}", file=sys.stderr, flush=True)


class ToolboxError(Exception):
    """后端返回的、可以直接展示给模型的错误。"""


class ToolboxClient:
    """工具箱后端客户端。

    用 http.client + 线程本地连接，而不是 urllib 的「一次请求一次握手」：
    容器内实测同一接口 11.3ms → 8.1ms（约 -28%），顺带消除高频调用下的 TIME_WAIT 堆积。
    连接可能被后端重启掐断，所以失败时丢连接重试一次，只重试一次避免放大故障。
    """

    # 收藏夹前缀：见 _request 的说明，这里是纵深防御的第一层。
    FORBIDDEN_PREFIXES = ("/api/vault",)

    def __init__(self, base_url, token="", timeout=30):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        parts = urllib.parse.urlsplit(self.base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or (443 if self._https else 80)
        self._base_path = parts.path.rstrip("/")
        self._local = threading.local()

    def _headers(self, content_type=None):
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip"}
        # 后端据此识别「请求来自 MCP」，Cookie 收藏夹会直接拒绝这类请求。
        headers["X-Client-Name"] = "curl2py-mcp"
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _new_conn(self):
        if self._https:
            return http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)

    def _drop_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关连接失败不影响主流程
                pass
        self._local.conn = None

    def _request(self, method, path, body=None, content_type=None):
        # Cookie 收藏夹不对 MCP 开放：即使有人新增工具指向该前缀，这里也会先拦住。
        # 隔离不依赖这一行——后端还会校验独立的 X-Vault-Token，且数据卷不挂到本容器。
        if any(path.startswith(prefix) for prefix in self.FORBIDDEN_PREFIXES):
            raise ToolboxError("Cookie 收藏夹不对 MCP 开放，请在前端界面操作")
        full = self._base_path + path
        headers = self._headers(content_type)
        for attempt in (0, 1):
            conn = getattr(self._local, "conn", None)
            if conn is None:
                conn = self._new_conn()
                self._local.conn = conn
            try:
                conn.request(method, full, body=body, headers=headers)
                response = conn.getresponse()
                raw = response.read()
                status = response.status
                encoding = response.getheader("Content-Encoding")
            except (http.client.HTTPException, OSError) as exc:
                self._drop_conn()
                if attempt == 0:
                    continue
                raise ToolboxError(
                    f"无法连接工具箱后端 {self.base_url}（{exc}）。"
                    "请确认容器已启动，或检查 --base-url 是否正确。") from exc
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            if status >= 400:
                raise self._http_error(status, raw)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ToolboxError(f"后端返回的不是 JSON：{exc}") from exc
        raise ToolboxError(f"无法连接工具箱后端 {self.base_url}")

    @staticmethod
    def _http_error(status, raw):
        try:
            payload = json.loads(raw.decode("utf-8"))
            detail = payload.get("error") or payload.get("message") or raw[:200]
        except (ValueError, UnicodeDecodeError):
            detail = raw[:200].decode("utf-8", "replace")
        if status == 401:
            return ToolboxError("后端拒绝了请求：API Token 缺失或无效")
        return ToolboxError(f"后端返回 {status}：{detail}")

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._request("POST", path, body=body, content_type="application/json")


def _canonical_algorithm(raw):
    """算法名大小写归一。

    后端要求 AES / SHA3-256 / HmacSHA256 这种写法，而模型很自然会写 aes、sha3_256。
    枚举能挡住大部分，但枚举之外的写法不该直接换来一句 422。
    只在真能匹配上时替换，匹配不上就原样送回去，让后端的报错保持准确。
    """
    token = raw.strip()

    def norm(value):
        return value.lower().replace("-", "").replace("_", "").replace(" ", "")

    target = norm(token)
    for name in CRYPTO_ALGORITHMS:
        if norm(name) == target:
            return name
    return token


def _rss_kb():
    """当前进程 RSS（KB）；非 Linux 或读不到时返回 0。"""
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _byte_size(value):
    return len(value.encode("utf-8"))


def _require_text(args, field="text", limit=TEXT_MAX_BYTES):
    value = args.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ToolboxError(f"参数 {field} 不能为空")
    size = _byte_size(value)
    if size > limit:
        label = f"{limit // 1024}KB" if limit < 1024 * 1024 else f"{limit // (1024 * 1024)}MB"
        raise ToolboxError(f"参数 {field} 为 {size / 1024:.1f}KB，超过上限 {label}")
    return value


def _require_scalar(args, field, limit=256):
    """数字字段的宽容读取：时间戳这类参数模型天然会传 int。

    早期版本只认 str，传 1700000000 会被当成空值报错——这是实测出来的真实故障，
    不是理论问题。这里统一把 int/float 转成字符串，同时仍然拒绝 None/bool/空串。
    """
    value = args.get(field)
    if value is None:
        raise ToolboxError(f"参数 {field} 不能为空")
    if isinstance(value, bool):
        raise ToolboxError(f"参数 {field} 必须是数字或字符串，收到布尔值")
    if isinstance(value, (int, float)):
        text = repr(value) if isinstance(value, float) else str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise ToolboxError(f"参数 {field} 必须是数字或字符串")
    if not text:
        raise ToolboxError(f"参数 {field} 不能为空")
    if len(text) > limit:
        raise ToolboxError(f"参数 {field} 过长（{len(text)} 字符，上限 {limit}）")
    return text


def _unwrap(body):
    """把 {"success":true,"result":...} 拆成模型友好的形态。"""
    if not isinstance(body, dict):
        return body
    if body.get("success") is False:
        raise ToolboxError(str(body.get("error") or "后端返回失败"))
    result = body.get("result")
    extras = {k: v for k, v in body.items() if k not in {"success", "result"}}
    if not extras:
        return result
    merged = {"result": result}
    merged.update(extras)
    return merged


def _clip(text):
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = text[:MAX_OUTPUT_CHARS]
    return head + f"\n\n…（输出过长已截断，原始长度 {len(text)} 字符，共省略 {len(text) - MAX_OUTPUT_CHARS} 字符）"


# ---------------------------------------------------------------- 工具实现


def build_tools(client, allow_script_run=False, allow_local_file=False, local_file_root=None):
    """返回 [(定义, 处理函数), ...]。"""

    def simple(path, field="text", limit=TEXT_MAX_BYTES, extra=None):
        def handler(args):
            payload = {field: _require_text(args, field, limit)}
            if extra:
                for key in extra:
                    if key in args and args[key] is not None:
                        payload[key] = args[key]
            return _unwrap(client.post(path, payload))
        return handler

    def diff_tool(path):
        """对比结果用文本块呈现：统一 diff 本身是可读文本，塞进 JSON 只会多一层转义。"""
        def handler(args):
            left = _require_text(args, "left", DIFF_SIDE_MAX_BYTES)
            right = _require_text(args, "right", DIFF_SIDE_MAX_BYTES)
            body = client.post(path, {"left": left, "right": right})
            if body.get("success") is False:
                raise ToolboxError(str(body.get("error") or "对比失败"))
            stats = body.get("stats") or {}
            diff_text = _clip(body.get("result") or "")
            parts = [
                f"统计：新增 {stats.get('added', 0)} / 删除 {stats.get('removed', 0)} "
                f"/ 修改 {stats.get('modified', 0)} / 未变 {stats.get('unchanged', 0)}",
            ]
            parts.append(diff_text if diff_text else "（两侧内容一致，无差异）")
            if args.get("detail") == "lines":
                records = body.get("lines") or []
                shown = records[:MAX_DIFF_LINES]
                rows = []
                for record in shown:
                    kind = record.get("type")
                    left_text = record.get("left_text")
                    right_text = record.get("right_text")
                    if kind == "equal":
                        rows.append(f"  {record.get('left_line')} = {left_text}")
                    elif kind == "insert":
                        rows.append(f"  {record.get('right_line')} + {right_text}")
                    elif kind == "delete":
                        rows.append(f"  {record.get('left_line')} - {left_text}")
                    else:
                        rows.append(f"  {record.get('left_line')} ~ {left_text}  ->  {right_text}")
                suffix = f"，仅显示前 {len(shown)} 行" if len(records) > len(shown) else ""
                parts.append(f"逐行明细（共 {len(records)} 行{suffix}）：")
                parts.extend(rows)
            return "\n".join(parts)
        return handler

    def hash_handler(args):
        algorithm = str(args.get("algorithm") or "sha256").strip().lower()
        text = _require_text(args)
        if algorithm in {"md5", "sha1", "sha256"}:
            return _unwrap(client.post("/api/crypto/hash", {"text": text, "algorithm": algorithm}))
        # /api/crypto/hash 只支持这三种，其余摘要走 crypto_process
        canonical = algorithm.upper()
        if canonical.startswith("SHA3_"):
            canonical = canonical.replace("_", "-")
        return _unwrap(client.post(
            "/api/crypto/process",
            {"operation": canonical, "text": text, "action": "digest"},
        ))

    def curl_handler(path):
        """代码类结果直接返回源码本体，告警以注释形式附在末尾，避免塞进 JSON 字符串转义。"""
        def handler(args):
            curl = _require_text(args, "curl", CURL_MAX_BYTES)
            body = client.post(path, {"curl": curl})
            if body.get("success") is False:
                raise ToolboxError(str(body.get("error") or "curl 转换失败"))
            code = body.get("result") or ""
            warnings = body.get("warnings") or []
            if warnings:
                code += "\n\n# ---- 解析告警 ----\n" + "\n".join(f"# {item}" for item in warnings)
            return code
        return handler

    def json_format_handler(args):
        payload = {"text": _require_text(args)}
        if args.get("indent") is not None:
            payload["indent"] = args["indent"]
        value = _unwrap(client.post("/api/format/json", payload))
        if args.get("compact"):
            # 后端的 indent=0 是「换行但不缩进」，不是单行压缩，这里补上真正的压缩
            return json.dumps(json.loads(value), ensure_ascii=False, separators=(",", ":"))
        return value

    def timestamp_handler(args):
        payload = {
            "timestamp": _require_scalar(args, "timestamp"),
            "unit": args.get("unit") or "s",
            "tz": args.get("tz") or "Asia/Shanghai",
        }
        return _unwrap(client.post("/api/time/timestamp", payload))

    def datetime_handler(args):
        payload = {
            "datetime": _require_text(args, "datetime", 256),
            "unit": args.get("unit") or "s",
            "tz": args.get("tz") or "Asia/Shanghai",
        }
        return _unwrap(client.post("/api/time/datetime", payload))

    def script_handler(args):
        code = _require_text(args, "code", 128 * 1024)
        return _unwrap(client.post("/api/run/python", {"code": code}))

    def file_base64_handler(args):
        """本机/本容器内文件转 data URI，补齐后端 /api/image/base64 的能力。

        不走后端是因为后端只接受 multipart 上传，模型没有文件句柄可传。
        """
        path = _require_text(args, "path", 4096)
        if not os.path.isabs(path):
            raise ToolboxError("path 必须是绝对路径")
        if not os.path.isfile(path):
            raise ToolboxError(f"文件不存在：{path}")
        if local_file_root:
            root = os.path.realpath(local_file_root)
            target = os.path.realpath(path)
            try:
                inside = os.path.commonpath([root, target]) == root
            except ValueError:      # Windows 跨盘符
                inside = False
            if not inside:
                raise ToolboxError(
                    f"path 超出允许范围：只允许读取 {root} 下的文件")
        size = os.path.getsize(path)
        if size > FILE_MAX_BYTES:
            raise ToolboxError(
                f"文件 {size / 1024 / 1024:.1f}MB，超过上限 {FILE_MAX_BYTES // (1024 * 1024)}MB")
        with open(path, "rb") as handle:
            raw = handle.read()
        mime = args.get("mime_type") or mimetypes.guess_type(path)[0] or "application/octet-stream"
        encoded = base64.b64encode(raw).decode("ascii")
        if args.get("output") == "raw":
            return encoded
        return f"data:{mime};base64,{encoded}"

    def crypto_process_handler(args):
        payload = {
            "operation": _canonical_algorithm(_require_text(args, "operation", 256)),
            "text": _require_text(args),
        }
        for key in ("action", "key", "iv", "mode", "padding", "input_format", "output_format"):
            if args.get(key) is not None:
                payload[key] = args[key]
        return _unwrap(client.post("/api/crypto/process", payload))

    tools = [
        {
            "name": "toolbox_health",
            "description": "检查 curl2py 工具箱后端是否在线，并返回脚本运行器状态。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": lambda args: _unwrap(client.get("/health")),
        },
        {
            "name": "toolbox_catalog",
            "description": "列出工具箱全部工具的分组、名称与前端路由，便于了解还能做什么。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": lambda args: _unwrap(client.get("/api/tools")),
        },
        {
            "name": "json_format",
            "description": "格式化或校验 JSON，非法时返回具体错误位置。"
                           "compact=true 时输出单行压缩 JSON（适合塞进命令行或传输）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待处理的 JSON 文本"},
                    "indent": {"type": "integer", "minimum": 0, "maximum": 8,
                               "description": "缩进空格数，默认 2。注意 0 表示「换行但不缩进」，不是压缩"},
                    "compact": {"type": "boolean",
                                "description": "为 true 时输出无换行无空格的单行 JSON"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": json_format_handler,
        },
        {
            "name": "header_to_dict",
            "description": "把浏览器里复制的 HTTP Header 文本转换成字典，并提示无法识别的行。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "多行 Header 文本"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/format/header"),
        },
        {
            "name": "cookie_to_dict",
            "description": "把 Cookie 文本（k=v; k2=v2 形式）转换成字典。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Cookie 文本"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/format/cookie"),
        },
        {
            "name": "python_dict_parse",
            "description": "解析 Python 字面量形式的字典或列表（单引号、True/False/None 等）并返回标准 JSON 结构。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Python 字典/列表文本"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/format/dict"),
        },
        {
            "name": "javascript_clean",
            "description": "清理 JavaScript 文本中的多余空行和首尾空白。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "JavaScript 源码"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/format/javascript"),
        },
        {
            "name": "html_clean",
            "description": "把 HTML 源码里连续的空行压成一行、去掉每行首尾空白。"
                           "注意：它不抽取正文、不删标签、不去脚本，只做空行规范化；"
                           "想取正文请用别的链路。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "HTML 源码"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/format/html"),
        },
        {
            "name": "url_parse",
            "description": "解析 URL，拆出协议、主机、路径和查询参数。",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "完整 URL"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            "handler": simple("/api/url/parse", field="url"),
        },
        {
            "name": "url_encode",
            "description": "对 URL 或文本做百分号编码。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "待编码文本"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/url/encode"),
        },
        {
            "name": "url_decode",
            "description": "还原百分号编码的 URL 或文本。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "待解码文本"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/url/decode"),
        },
        {
            "name": "curl_to_python",
            "description": "把浏览器复制的 curl 命令转换成等价的 Python requests 代码，并返回解析告警。",
            "inputSchema": {
                "type": "object",
                "properties": {"curl": {"type": "string", "description": "完整的 curl 命令"}},
                "required": ["curl"],
                "additionalProperties": False,
            },
            "handler": curl_handler("/api/convert/curl2python"),
        },
        {
            "name": "curl_to_feapder",
            "description": "把 curl 命令转换成 Feapder 爬虫模板。",
            "inputSchema": {
                "type": "object",
                "properties": {"curl": {"type": "string", "description": "完整的 curl 命令"}},
                "required": ["curl"],
                "additionalProperties": False,
            },
            "handler": curl_handler("/api/convert/curl2feapder"),
        },
        {
            "name": "base64",
            "description": "Base64 编码或解码。解码时输入非法 Base64 会返回明确错误。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待处理文本"},
                    "operation": {"type": "string", "enum": ["encode", "decode"],
                                  "description": "encode 编码（默认），decode 解码"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": simple("/api/crypto/base64", extra=["operation"]),
        },
        {
            "name": "hash",
            "description": "计算文本摘要，返回十六进制字符串。md5/sha1/sha256 直连；"
                           "sha3-256、ripemd160、sm3 等其他算法会自动走通用加解密接口。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待摘要文本"},
                    "algorithm": {"type": "string", "enum": DIGEST_ALGORITHMS,
                                  "description": "摘要算法，默认 sha256。只支持下面枚举里的值，"
                                                 "不支持 crc32 这类校验和"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "handler": hash_handler,
        },
        {
            "name": "crypto_operations",
            "description": "列出可用的加密算法、摘要算法、HMAC 算法和字符编码清单。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "handler": lambda args: _unwrap(client.get("/api/crypto/operations")),
        },
        {
            "name": "crypto_process",
            "description": "通用加解密：摘要、HMAC、对称加密（AES/DES/SM4/RC4 等）。"
                           "operation 取 crypto_operations 返回的名称。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": CRYPTO_ALGORITHMS,
                                  "description": "算法名，枚举已列出后端支持的全部值；"
                                                 "对称算法用大写（AES/SM4），摘要用大写（SHA3-256），"
                                                 "HMAC 用 Hmac 前缀（HmacSHA256）"},
                    "text": {"type": "string", "description": "待处理文本（按 input_format 解读）"},
                    "action": {"type": "string", "enum": ["digest", "encrypt", "decrypt"],
                               "description": "动作，默认 digest"},
                    "key": {"type": "string", "description": "密钥（HMAC 与对称加密需要）"},
                    "iv": {"type": "string", "description": "初始向量"},
                    "mode": {"type": "string", "enum": BLOCK_MODES,
                             "description": "分组模式，默认 CBC。ECB 不需要 iv；SM4 只支持 CBC/ECB"},
                    "padding": {"type": "string", "enum": PADDING_MODES,
                                "description": "填充方式，默认 Pkcs7。NoPadding 要求输入长度是块大小整数倍"},
                    "input_format": {"type": "string", "enum": ENCODING_FORMATS,
                                     "description": "输入按什么编码解读，默认 Utf8"},
                    "output_format": {"type": "string", "enum": ENCODING_FORMATS,
                                      "description": "输出用什么编码表示，默认 Base64"},
                },
                "required": ["operation", "text"],
                "additionalProperties": False,
            },
            "handler": crypto_process_handler,
        },
        {
            "name": "text_diff",
            "description": "对比两段文本，返回统一 diff 和统计。默认不返回逐行明细以控制体积，"
                           "需要时传 detail=lines。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "left": {"type": "string", "description": "原始文本"},
                    "right": {"type": "string", "description": "新文本"},
                    "detail": {"type": "string", "enum": ["summary", "lines"],
                               "description": "summary 只回 diff 与统计（默认），lines 额外回逐行记录"},
                },
                "required": ["left", "right"],
                "additionalProperties": False,
            },
            "handler": diff_tool("/api/text/diff"),
        },
        {
            "name": "json_diff",
            "description": "按字段路径对比两个 JSON，返回差异行与统计。默认不返回逐行明细。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "left": {"type": "string", "description": "原始 JSON 文本"},
                    "right": {"type": "string", "description": "新 JSON 文本"},
                    "detail": {"type": "string", "enum": ["summary", "lines"],
                               "description": "summary 只回 diff 与统计（默认），lines 额外回逐行记录"},
                },
                "required": ["left", "right"],
                "additionalProperties": False,
            },
            "handler": diff_tool("/api/json/diff"),
        },
        {
            "name": "timestamp_to_date",
            "description": "把 Unix 时间戳转换成指定时区的日期时间字符串。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": ["integer", "number", "string"],
                                  "description": "Unix 时间戳，传数字或数字字符串都行，如 1700000000"},
                    "unit": {"type": "string", "enum": ["s", "ms"], "description": "秒或毫秒，默认 s"},
                    "tz": {"type": "string", "description": "IANA 时区，如 Asia/Shanghai，默认 Asia/Shanghai"},
                },
                "required": ["timestamp"],
                "additionalProperties": False,
            },
            "handler": timestamp_handler,
        },
        {
            "name": "date_to_timestamp",
            "description": "把日期时间字符串转换成 Unix 时间戳。支持 YYYY-MM-DD HH:MM:SS 与 ISO-8601。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "datetime": {"type": "string", "description": "日期时间，如 2023-11-15 06:13:20"},
                    "unit": {"type": "string", "enum": ["s", "ms"], "description": "秒或毫秒，默认 s"},
                    "tz": {"type": "string", "description": "IANA 时区，默认 Asia/Shanghai"},
                },
                "required": ["datetime"],
                "additionalProperties": False,
            },
            "handler": datetime_handler,
        },
    ]

    if allow_script_run:
        tools.append({
            "name": "run_python",
            "description": "在后端沙箱里执行一段 Python 代码并返回标准输出。"
                           "需要后端开启 ENABLE_SCRIPT_RUNNER 并配置 API Token。",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python 源码"}},
                "required": ["code"],
                "additionalProperties": False,
            },
            "annotations": dict(SCRIPT_ANNOTATIONS),
            "handler": script_handler,
        })

    if allow_local_file:
        tools.append({
            "name": "file_to_base64",
            "description": "把服务端所在机器（或容器）上的本地文件转成 data URI，"
                           "常用于把图片塞进 HTML/JSON 或直接交给模型。"
                           "需要以 --allow-local-file 启动。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "绝对路径，单文件上限 5MB"},
                    "mime_type": {"type": "string",
                                  "description": "MIME 类型，默认按扩展名推断"},
                    "output": {"type": "string", "enum": ["data_uri", "raw"],
                               "description": "data_uri 返回 data:...;base64,...（默认），raw 只返回 Base64 串"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "handler": file_base64_handler,
        })

    for tool in tools:
        tool.setdefault("annotations", dict(READ_ONLY_ANNOTATIONS))
        tool.setdefault("title", TOOL_TITLES.get(tool["name"], tool["name"]))

    return tools


# ---------------------------------------------------------------- JSON-RPC


class Server:
    def __init__(self, tools, protocol_version):
        self.tools = {tool["name"]: tool for tool in tools}
        self.tool_list = []
        for t in tools:
            entry = {"name": t["name"], "title": t.get("title", t["name"]),
                     "description": t["description"], "inputSchema": t["inputSchema"]}
            if t.get("annotations"):
                entry["annotations"] = t["annotations"]
            self.tool_list.append(entry)
        self.protocol_version = protocol_version
        self.started_at = time.time()
        self.call_count = 0
        self.error_count = 0
        # 传输层计数：RPC 层的 calls/errors 看不到 accept 失败和 4xx，
        # 压测时「错误 0 但客户端一堆 RST」就是这么来的，这里补上。
        self.transport = "stdio"
        self.http_requests = 0
        self.http_rejected = 0
        self.by_tool = {}
        self.slowest_ms = 0
        # 由 main() 注入：/health 里直接告诉运维哪些「可选能力」真的开着，
        # 免得靠翻启动参数猜为什么少了两个工具。
        self.capabilities = {}
        self._lock = threading.Lock()

    def stats(self):
        with self._lock:
            return {
                "service": SERVER_NAME,
                "version": SERVER_VERSION,
                "status": "ok",
                "protocol": self.protocol_version,
                "transport": self.transport,
                "tools": len(self.tool_list),
                "tool_names": [t["name"] for t in self.tool_list],
                "uptime_s": round(time.time() - self.started_at, 1),
                "calls": self.call_count,
                "errors": self.error_count,
                "http_requests": self.http_requests,
                "http_rejected": self.http_rejected,
                "slowest_tool_ms": round(self.slowest_ms, 1),
                "capabilities": dict(self.capabilities),
                "by_tool": self._by_tool_snapshot(),
                "rss_kb": _rss_kb(),
                "pid": os.getpid(),
            }

    def _by_tool_snapshot(self, limit=24):
        """调用方必须已持有 self._lock。"""
        rows = []
        for name, (ok, err, ms) in self.by_tool.items():
            total = ok + err
            rows.append((name, {"ok": ok, "err": err,
                                "avg_ms": round(ms / total, 1) if total else 0}))
        rows.sort(key=lambda kv: -(kv[1]["ok"] + kv[1]["err"]))
        return dict(rows[:limit])

    def count_http(self, rejected=False):
        with self._lock:
            self.http_requests += 1
            if rejected:
                self.http_rejected += 1

    def handle(self, message):
        """返回响应字典；通知（无 id）返回 None。"""
        if not isinstance(message, dict):
            return self._error(None, JSONRPC_INVALID_REQUEST, "消息必须是 JSON 对象")

        method = message.get("method")
        message_id = message.get("id")
        is_notification = "id" not in message

        if not isinstance(method, str):
            return None if is_notification else self._error(
                message_id, JSONRPC_INVALID_REQUEST, "缺少 method")

        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            version = requested if requested in SUPPORTED_VERSIONS else self.protocol_version
            return self._ok(message_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            })

        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None

        if method == "ping":
            return self._ok(message_id, {})

        if method == "tools/list":
            return self._ok(message_id, {"tools": self.tool_list})

        if method == "tools/call":
            return self._call_tool(message_id, message.get("params") or {})

        if is_notification:
            return None
        return self._error(message_id, JSONRPC_METHOD_NOT_FOUND, f"不支持的方法：{method}")

    def _call_tool(self, message_id, params):
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tools:
            return self._error(message_id, JSONRPC_INVALID_PARAMS, f"未知工具：{name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(message_id, JSONRPC_INVALID_PARAMS, "arguments 必须是对象")

        tool = self.tools[name]
        missing = [key for key in tool["inputSchema"].get("required", []) if key not in arguments]
        if missing:
            self._bump(name, 0.0, error=True)
            return self._ok(message_id, self._text_result(
                f"缺少必填参数：{', '.join(missing)}", is_error=True))

        started = time.perf_counter()
        try:
            value = tool["handler"](arguments)
        except ToolboxError as exc:
            self._bump(name, time.perf_counter() - started, error=True)
            return self._ok(message_id, self._text_result(str(exc), is_error=True))
        except Exception as exc:  # noqa: BLE001 - 任何异常都要转成可读结果
            log(f"工具 {name} 执行异常：{exc!r}")
            self._bump(name, time.perf_counter() - started, error=True)
            return self._ok(message_id, self._text_result(
                f"工具执行失败：{exc}", is_error=True))

        self._bump(name, time.perf_counter() - started)
        return self._ok(message_id, self._text_result(value))

    def _bump(self, name=None, elapsed=0.0, error=False):
        with self._lock:
            self.call_count += 1
            if error:
                self.error_count += 1
            if name:
                slot = self.by_tool.setdefault(name, [0, 0, 0.0])
                slot[0 if not error else 1] += 1
                slot[2] += elapsed * 1000
            ms = elapsed * 1000
            if ms > self.slowest_ms:
                self.slowest_ms = ms

    @staticmethod
    def _text_result(value, is_error=False):
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}

    @staticmethod
    def _ok(message_id, result):
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    @staticmethod
    def _error(message_id, code, message):
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------- HTTP 传输
#
# 实现的是 MCP Streamable HTTP 里最实用的子集：
#   POST /mcp  收发 JSON-RPC（单条或批量），通知返回 202
#   GET  /mcp  405（本服务端不主动推送，规范允许）
#   DELETE /mcp 结束会话
#   GET  /health 存活与调用统计，便于容器探针和人工排查
# 没有 SSE、没有服务端发起请求，所以不需要长连接和事件存储。


class MCPHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"curl2py-mcp/{SERVER_VERSION}"
    # 空闲的 keep-alive 连接必须能被回收，否则慢客户端会一直占着一个线程
    timeout = IDLE_TIMEOUT_S

    rpc_server = None          # 由 factory 注入
    http_token = ""
    sessions = None            # set，由 factory 注入
    session_lock = None
    allowed_origins = frozenset()

    # -- 基础设施 --------------------------------------------------

    def log_message(self, fmt, *args):  # 默认实现往 stderr 打，这里统一加前缀
        log(f"http {self.address_string()} {fmt % args}")

    def _origin(self):
        return (self.headers.get("Origin") or "").strip()

    def _origin_ok(self):
        """DNS rebinding 防护：浏览器来的请求必须显式放行。

        局域网 IP 上的 MCP 如果对任意 Origin 开放，受害者随便打开一个网页就能
        POST /mcp 调工具（Origin 只限制读取响应，不限制发出请求）。
        非浏览器客户端不带 Origin，不受影响。
        """
        origin = self._origin()
        return not origin or origin in self.allowed_origins

    def _cors(self):
        origin = self._origin()
        if origin and origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")

    def _content_length(self):
        raw = self.headers.get("Content-Length")
        if not raw:
            return 0
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def _drain_body(self):
        """把请求体读掉丢弃。

        HTTP/1.1 keep-alive 下，提前返回（401/403/404）却不读完 body，
        剩下的字节会被当成下一个请求的起始行——实测会返回 400 + 一页 HTML，
        客户端整条连接就废了。所以除了 413（body 太大，直接关连接）以外，
        所有提前返回的路径都必须先 drain。
        """
        length = self._content_length()
        if length <= 0:
            return
        if length > HTTP_MAX_BODY:
            self.close_connection = True
            return
        try:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (OSError, ValueError):
            self.close_connection = True

    def _read_body(self, length):
        """按 Content-Length 读满；读不完整说明连接断了，返回 None。"""
        if length <= 0:
            return b""
        chunks = []
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except (OSError, ValueError):
            self.close_connection = True
            return None
        if remaining > 0:
            self.close_connection = True
            return None
        return b"".join(chunks)

    def _send_json(self, code, payload, session=None, close=False):
        if self.rpc_server is not None:
            self.rpc_server.count_http()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        if session:
            self.send_header("Mcp-Session-Id", session)
        if close:
            self.close_connection = True
            self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, code):
        if self.rpc_server is not None:
            self.rpc_server.count_http()
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()

    def send_error(self, code, message=None, explain=None):
        """默认实现回 HTML 错误页，MCP 客户端解析不了；这里统一成 JSON。"""
        try:
            self._send_json(code, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": JSONRPC_PARSE_ERROR,
                          "message": message or f"HTTP {code}"}},
                close=True)
        except Exception:  # noqa: BLE001 - 响应已发出或连接已断，只能关掉
            self.close_connection = True

    def _reject(self, code, payload, close=False):
        """所有提前返回都走这里，保证 body 被 drain 或连接被关闭。"""
        if not close:
            self._drain_body()
        if self.rpc_server is not None:
            self.rpc_server.count_http(rejected=True)
        self._send_json(code, payload, close=close)

    def _reject_origin(self):
        origin = self._origin()
        self._reject(403, {
            "error": f"拒绝来自 Origin {origin} 的请求。"
                     f"若确需浏览器直连，请用 --allow-origin {origin} 启动"})

    def _auth_ok(self):
        if not self.http_token:
            return True
        header = self.headers.get("Authorization", "")
        supplied = ""
        if header.lower().startswith("bearer "):
            supplied = header[7:].strip()
        if not supplied:
            supplied = (self.headers.get("X-API-Token") or "").strip()
        import hmac as _hmac
        return bool(supplied) and _hmac.compare_digest(supplied, self.http_token)

    # -- 方法 ------------------------------------------------------

    def do_OPTIONS(self):
        self._send_empty(204)

    def do_GET(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in {"/", "/health"}:
            self._send_json(200, self.rpc_server.stats())
            return
        if path == "/mcp":
            self._send_json(405, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": JSONRPC_INVALID_REQUEST,
                          "message": "本服务端不支持 SSE 推送，请用 POST /mcp 发送 JSON-RPC"}})
            return
        self._send_json(404, {"error": f"未知路径 {path}"})

    def do_DELETE(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/mcp":
            self._reject(404, {"error": f"未知路径 {path}"})
            return
        self._drain_body()
        session = self.headers.get("Mcp-Session-Id", "")
        if session and self.session_lock:
            with self.session_lock:
                self.sessions.discard(session)
        self._send_json(200, {"ok": True, "session_terminated": bool(session)})

    def do_POST(self):
        # 先拿 Content-Length：无论后面从哪个分支返回，都得知道要不要 drain body
        length = self._content_length()
        if length > HTTP_MAX_BODY:
            # body 太大，读掉它纯属浪费带宽，直接关连接
            self._reject(413, {"error": f"请求体 {length} 字节超过上限 {HTTP_MAX_BODY}，"
                                        f"连接已关闭"}, close=True)
            return
        if not self._origin_ok():
            self._reject_origin()
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/mcp":
            self._reject(404, {"error": f"未知路径 {path}"})
            return
        if not self._auth_ok():
            self._reject(401, {"error": "缺少或错误的 Token（Authorization: Bearer <token>）"})
            return

        raw = self._read_body(length)
        if raw is None:
            return  # 读不完整，连接已标记关闭
        if not raw.strip():
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": JSONRPC_PARSE_ERROR, "message": "请求体为空"}})
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": JSONRPC_PARSE_ERROR,
                                            "message": f"JSON 解析失败：{exc}"}})
            return

        if isinstance(payload, list) and len(payload) > MAX_BATCH_ITEMS:
            self._send_json(400, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": JSONRPC_INVALID_REQUEST,
                          "message": f"批量请求 {len(payload)} 条，超过上限 {MAX_BATCH_ITEMS} 条，"
                                     f"请拆成多个 POST"}})
            return

        # 会话：客户端带了不认识的 id 就顺手登记（部分客户端会自己生成 GUID），不强求严格
        session = (self.headers.get("Mcp-Session-Id") or "").strip()
        new_session = None
        if self.session_lock is not None:
            with self.session_lock:
                if session and session not in self.sessions:
                    self.sessions.add(session)
                if not session and self._is_initialize(payload):
                    new_session = uuid.uuid4().hex
                    session = new_session
                    self.sessions.add(session)

        result = self._dispatch(payload)
        if result is None:
            self._send_empty(202)
            return
        self._send_json(200, result, session=new_session)

    # -- 内部 ------------------------------------------------------

    @staticmethod
    def _is_initialize(payload):
        items = payload if isinstance(payload, list) else [payload]
        return any(isinstance(item, dict) and item.get("method") == "initialize" for item in items)

    def _dispatch(self, payload):
        if isinstance(payload, list):
            if not payload:
                return None
            responses = []
            for item in payload:
                response = self.rpc_server.handle(item)
                if response is not None:
                    responses.append(response)
            return responses or None
        return self.rpc_server.handle(payload)


class MCPHTTPServer(ThreadingHTTPServer):
    """带 backlog 与并发上限的 HTTP 服务端。

    压测暴露的两个默认值问题：
    * socketserver.TCPServer.request_queue_size 默认 5，并发 16 时 SYN 队列就溢出，
      客户端拿到的是 ConnectionReset 而不是 503，看起来像「服务崩了」；
    * ThreadingMixIn 不限制线程数，慢连接可以无限堆线程，等于给自己开了个 DoS。
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = REQUEST_QUEUE_SIZE

    def __init__(self, address, handler, max_concurrency=DEFAULT_MAX_CONCURRENCY):
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            body = json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": JSONRPC_INTERNAL_ERROR,
                          "message": f"服务端并发已满（上限 {self.max_concurrency}），请稍后重试"},
            }, ensure_ascii=False).encode("utf-8")
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json; charset=utf-8\r\n"
                    b"Retry-After: 1\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            finally:
                if self.rpc_server is not None:  # handler 类上的引用，见 build_http_server
                    self.rpc_server.count_http(rejected=True)
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            try:
                self._slots.release()
            except ValueError:
                pass


def build_http_server(rpc_server, host, port, token="", allowed_origins=(),
                      max_concurrency=DEFAULT_MAX_CONCURRENCY):
    handler = type("BoundMCPHTTPHandler", (MCPHTTPHandler,), {
        "rpc_server": rpc_server,
        "http_token": token,
        "sessions": set(),
        "session_lock": threading.Lock(),
        "allowed_origins": frozenset(allowed_origins),
    })
    rpc_server.transport = "http"
    httpd = MCPHTTPServer((host, port), handler, max_concurrency=max_concurrency)
    httpd.rpc_server = rpc_server      # 给 process_request 里的 503 计数用
    return httpd


def serve_http(rpc_server, host, port, token="", allowed_origins=(),
               max_concurrency=DEFAULT_MAX_CONCURRENCY):
    httpd = build_http_server(rpc_server, host, port, token, allowed_origins,
                              max_concurrency=max_concurrency)
    log(f"HTTP 传输已就绪：http://{host}:{port}/mcp"
        f"（{len(rpc_server.tool_list)} 个工具，并发上限 {max_concurrency}，"
        f"backlog {REQUEST_QUEUE_SIZE}{'，需要 Token' if token else ''}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("收到中断，正在退出")
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="curl2py 工具箱 MCP server（stdio / Streamable HTTP）")
    parser.add_argument("--base-url", default=os.environ.get("CURL2PY_BASE_URL", "http://127.0.0.1:9997"),
                        help="工具箱后端地址，默认 http://127.0.0.1:9997")
    parser.add_argument("--token", default=os.environ.get("CURL2PY_API_TOKEN", ""),
                        help="后端 API Token，默认读环境变量 CURL2PY_API_TOKEN")
    parser.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数，默认 30")
    parser.add_argument("--allow-script-run", action="store_true",
                        help="额外注册 run_python 工具（需后端已启用脚本运行器）")
    parser.add_argument("--allow-local-file", action="store_true",
                        help="额外注册 file_to_base64 工具（可读服务端本地文件）")
    parser.add_argument("--local-file-root", default=os.environ.get("CURL2PY_MCP_FILE_ROOT", ""),
                        help="配合 --allow-local-file，限制只能读取该目录下的文件")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="传输方式，默认 stdio；http 供局域网/容器内使用")
    parser.add_argument("--host", default=os.environ.get("CURL2PY_MCP_HOST", "127.0.0.1"),
                        help="HTTP 监听地址，默认 127.0.0.1，容器内需 0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CURL2PY_MCP_PORT", "9999")),
                        help="HTTP 监听端口，默认 9999")
    parser.add_argument("--http-token", default=os.environ.get("CURL2PY_MCP_TOKEN", ""),
                        help="HTTP 模式下要求的 Token，默认读环境变量 CURL2PY_MCP_TOKEN")
    parser.add_argument("--allow-origin", action="append", default=[],
                        metavar="ORIGIN",
                        help="允许的浏览器 Origin，可重复。默认拒绝所有带 Origin 的请求（防 DNS rebinding）")
    parser.add_argument("--max-concurrency", type=int,
                        default=int(os.environ.get("CURL2PY_MCP_MAX_CONCURRENCY",
                                                   str(DEFAULT_MAX_CONCURRENCY))),
                        help=f"同时处理的连接数上限，超出返回 503，默认 {DEFAULT_MAX_CONCURRENCY}")
    parser.add_argument("--list-tools", action="store_true",
                        help="打印工具清单后退出，用于调试")
    args = parser.parse_args(argv)

    # run_python 会在后端容器里执行任意代码。HTTP + 无 Token 等于把它挂到局域网上裸奔，
    # 谁扫到 9999 谁就能在那台机器上跑命令，所以这个组合直接拒绝启动。
    if args.transport == "http" and args.allow_script_run and not args.http_token:
        raise SystemExit(
            "拒绝启动：--allow-script-run 在 HTTP 传输下必须同时给 --http-token。\n"
            "run_python 会执行任意代码，没有 Token 就等于对局域网开放远程命令执行。\n"
            "要么加 --http-token <一串随机值>，要么去掉 --allow-script-run。")

    client = ToolboxClient(args.base_url, args.token, args.timeout)
    tools = build_tools(client,
                        allow_script_run=args.allow_script_run,
                        allow_local_file=args.allow_local_file,
                        local_file_root=args.local_file_root)
    server = Server(tools, PROTOCOL_VERSION)
    server.capabilities = {
        "script_run": bool(args.allow_script_run),
        "local_file": bool(args.allow_local_file),
        "local_file_root": os.path.realpath(args.local_file_root) if args.local_file_root else None,
        "http_token": bool(args.http_token),
        "max_concurrency": args.max_concurrency,
    }

    if args.list_tools:
        print(json.dumps(server.tool_list, ensure_ascii=False, indent=2))
        return 0

    if args.transport == "http":
        return serve_http(server, args.host, args.port, args.http_token, args.allow_origin,
                          max_concurrency=args.max_concurrency)

    log(f"已就绪：{len(tools)} 个工具，后端 {args.base_url}"
        f"{'，已启用 run_python' if args.allow_script_run else ''}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": JSONRPC_PARSE_ERROR, "message": f"JSON 解析失败：{exc}"}}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        response = server.handle(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
