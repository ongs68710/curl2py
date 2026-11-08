"""WSGI 响应压缩中间件。

只压文本类响应（HTML / CSS / JS / JSON / SVG），且超过 min_size 才压，
已经是 gzip 的响应直接放行。压缩结果按 (路径, ETag, 长度) 做有界缓存 ——
armv7 设备单核主频低，79KB 的 app.js 每次现压要十几毫秒，缓存后只在
文件变化时压一次。

两个关键设计：
1. 先看响应头再决定是否缓冲。图片、已压缩、超大响应直接流式转发，
   避免把几 MB 的 PNG 整个读进内存（armv7 设备内存本就紧张）。
2. 放在独立模块里，可以脱离 Flask / gunicorn 单独做单元测试。
"""
import gzip

COMPRESSIBLE_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


class GzipMiddleware:
    def __init__(self, app, min_size=1024, max_size=8 * 1024 * 1024,
                 cache_entries=32, level=6):
        self.app = app
        self.min_size = min_size
        self.max_size = max_size
        self.cache_entries = cache_entries
        self.level = level
        self._cache = {}

    # ---- 内部工具 ----
    @staticmethod
    def _header(headers, name):
        for key, value in headers:
            if key.lower() == name:
                return value
        return ""

    def _remember(self, key, payload):
        if len(self._cache) >= self.cache_entries:
            self._cache.pop(next(iter(self._cache)), None)  # 简单 FIFO
        self._cache[key] = payload

    def _should_buffer(self, headers):
        ctype = self._header(headers, "content-type").lower()
        if not ctype.startswith(COMPRESSIBLE_PREFIXES):
            return False
        if self._header(headers, "content-encoding"):
            return False
        length = self._header(headers, "content-length")
        # 没有 Content-Length 的流式响应（如 /api/image/base64 的生成器输出）
        # 不缓冲：整体读入会吃掉上百 MB，正是这个中间件要避免的事。
        if not length.isdigit():
            return False
        if int(length) > self.max_size:
            return False
        return True

    def __call__(self, environ, start_response):
        accepts = (environ.get("HTTP_ACCEPT_ENCODING") or "").lower()
        if "gzip" not in accepts or environ.get("REQUEST_METHOD") == "HEAD":
            return self.app(environ, start_response)

        captured = {}

        def capture(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = list(headers)
            return lambda _data: None

        iterable = self.app(environ, capture)
        status = captured.get("status", "200 OK")
        headers = captured.get("headers", [])

        # 二进制 / 已压缩 / 超大响应：不缓冲，直接流式转发
        if not self._should_buffer(headers):
            start_response(status, headers)
            return iterable

        body = b"".join(iterable)
        if len(body) < self.min_size:
            start_response(status, headers)
            return [body]

        key = (environ.get("PATH_INFO", ""), self._header(headers, "content-type"),
               self._header(headers, "etag"), len(body))
        payload = self._cache.get(key)
        if payload is None:
            payload = gzip.compress(body, self.level)
            if len(payload) >= len(body):
                start_response(status, headers)
                return [body]
            self._remember(key, payload)

        out = [(k, v) for k, v in headers
               if k.lower() not in ("content-length", "content-encoding")]
        out.append(("Content-Encoding", "gzip"))
        out.append(("Content-Length", str(len(payload))))
        out.append(("Vary", "Accept-Encoding"))
        start_response(status, out)
        return [payload]
