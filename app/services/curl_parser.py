"""纯 Python 的 curl 解析与代码生成。

替代 curlconverter（Node + tree-sitter），只用标准库：

  * 启动零开销（原方案每次转换要 fork 一个 Node 进程，冷启动约 1 秒）
  * 无原生扩展，任何 CPU 架构行为一致（armv7 上实测快三个数量级）
  * 内存常驻，不再随并发线性叠加

输出结构与 curlconverter 的 ``--language json`` 保持兼容（``url`` /
``raw_url`` / ``queries`` / ``headers`` / ``data`` / ``method``），
因此上游 Feapder 生成逻辑无需改动。
"""

import json
import re
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlsplit, urlunsplit


class CurlParseError(ValueError):
    """curl 命令无法解析。"""


# ---------------------------------------------------------------- 选项词表

# 需要取值的短选项。顺序无关，只做成员判断。
SHORT_WITH_VALUE = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
# 无值短选项
SHORT_FLAGS = set("aBfgiIjJkLMnNOpqRsSvZ012346#")

LONG_WITH_VALUE = {
    "url", "request", "header", "data", "data-ascii", "data-raw", "data-binary",
    "data-urlencode", "json", "form", "form-string", "user", "cookie",
    "cookie-jar", "proxy", "proxy-user", "user-agent", "referer", "output",
    "upload-file", "max-time", "connect-timeout", "write-out", "cacert", "cert",
    "key", "resolve", "retry", "limit-rate", "interface", "oauth2-bearer",
    "header-file", "range", "continue-at", "url-query", "config", "cacert-type",
    "cert-type", "key-type", "dns-servers", "connect-to", "unix-socket",
}

LONG_FLAGS = {
    "compressed", "insecure", "location", "silent", "show-error", "verbose",
    "fail", "fail-with-body", "head", "include", "globoff", "http1.1", "http2",
    "http2-prior-knowledge", "no-buffer", "path-as-is", "tcp-nodelay", "get",
    "no-keepalive", "raw", "tr-encoding", "post301", "post302", "post303",
}

# requests 会自动设置、原样保留反而可能出错的头部
COMMENTED_HEADERS = {"content-length"}


# ---------------------------------------------------------------- 词法分析

def tokenize(text):
    """按 shell 规则切分命令，支持引号、$'...' 与反斜杠续行。"""
    tokens, cur = [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r":
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
        elif c == "\n":
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
        elif c == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
                continue
            if i + 1 < n:
                cur.append(text[i + 1])
                i += 2
            else:
                i += 1
        elif c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                raise CurlParseError("单引号未闭合，请检查 curl 命令")
            cur.append(text[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt in '"\\$`':
                        cur.append(nxt)
                        i += 2
                    elif nxt == "\n":
                        i += 2
                    else:
                        cur.append(text[i])
                        i += 1
                else:
                    cur.append(text[i])
                    i += 1
            if i >= n:
                raise CurlParseError("双引号未闭合，请检查 curl 命令")
            i += 1
        elif c == "$" and i + 1 < n and text[i + 1] == "'":
            i, cur = _read_ansi_c(text, i + 2, cur)
        else:
            cur.append(c)
            i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


_ANSI_SIMPLE = {
    "n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"',
    "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b", "0": "\0",
}


def _read_ansi_c(text, i, cur):
    """解析 $'...' 转义序列，返回新的游标。"""
    n = len(text)
    while i < n and text[i] != "'":
        if text[i] == "\\" and i + 1 < n:
            esc = text[i + 1]
            if esc == "x" and i + 3 < n:
                try:
                    cur.append(chr(int(text[i + 2:i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if esc == "u" and i + 5 < n:
                try:
                    cur.append(chr(int(text[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if esc in _ANSI_SIMPLE:
                cur.append(_ANSI_SIMPLE[esc])
                i += 2
                continue
            cur.append(esc)
            i += 2
        else:
            cur.append(text[i])
            i += 1
    if i >= n:
        raise CurlParseError("$'...' 未闭合，请检查 curl 命令")
    return i + 1, cur


def _is_curl_word(token):
    """判断首词是否为 curl（兼容 curl.exe 与绝对路径）。"""
    base = re.split(r"[\\/]", token)[-1]
    return bool(re.match(r"^curl(\.exe)?$", base, re.I))


# ---------------------------------------------------------------- 语法分析

def _split_header(item):
    """拆分 `Name: Value`；空值头（`X-Foo;`）按无值处理。"""
    if ":" in item:
        key, value = item.split(":", 1)
        return key.strip(), value.strip()
    return item.rstrip(";").strip(), ""


def _collect_forms(specs):
    """把 -F / --form 规格拆成文件与普通字段，保留原始顺序。"""
    files, fields, order = {}, {}, []
    for spec in specs:
        key, _, value = spec.partition("=")
        key = key.strip()
        if not key:
            continue
        if value.startswith("@"):
            path = value[1:].split(";")[0]
            files[key] = path
            order.append((key, "file"))
        elif value.startswith("<"):
            files[key] = value[1:]
            order.append((key, "file"))
        else:
            fields[key] = value
            order.append((key, "field"))
    return files, fields, order


def _build_body(data_parts, headers, warnings):
    """根据 -d 系列选项还原请求体，返回 (data, is_json)。

    判断顺序很关键：先看 Content-Type 是否声明 JSON，再看正文是否以
    ``{`` / ``[`` 开头（此时必须原样保留，不能擅自拆成表单），
    最后才尝试按 ``k=v&k=v`` 还原表单字典。
    """
    content_type = next(
        (v for k, v in headers.items() if k.lower() == "content-type"), ""
    ).lower()

    pieces = []
    for kind, value in data_parts:
        if kind == "data-urlencode":
            if value.startswith("@"):
                warnings.append("-d @文件 需要读取本地文件，生成代码保留路径原样")
                pieces.append(value)
            elif value.startswith("="):
                pieces.append(quote_plus(value[1:]))
            elif "=" in value:
                key, raw = value.split("=", 1)
                pieces.append(f"{key}={quote_plus(raw)}")
            else:
                pieces.append(quote_plus(value))
        elif value.startswith("@"):
            warnings.append("-d @文件 需要读取本地文件，生成代码保留路径原样")
            pieces.append(value)
        else:
            pieces.append(value)

    merged = "&".join(pieces).strip()

    if "json" in content_type:
        try:
            return json.loads(merged), True
        except (json.JSONDecodeError, ValueError):
            return merged, False

    if merged.startswith("{") or merged.startswith("["):
        return merged, False

    segments = [seg for seg in merged.split("&") if seg]
    if segments and all("=" in seg for seg in segments):
        body = {}
        for seg in segments:
            key, value = seg.split("=", 1)
            key, value = unquote_plus(key), unquote_plus(value)
            body[key] = f"{body[key]}&{value}" if key in body else value
        return body, False

    return merged, False


def parse_curl(cmd):
    """把 curl 命令解析成请求结构。

    返回字段与 curlconverter 的 json 输出对齐，另含 ``warnings``。
    """
    if not isinstance(cmd, str) or not cmd.strip():
        raise CurlParseError("请输入 curl 命令")

    tokens = tokenize(cmd)
    if not tokens:
        raise CurlParseError("请输入 curl 命令")
    if not _is_curl_word(tokens[0]):
        raise CurlParseError("命令必须以 curl 开头")
    tokens = tokens[1:]

    warnings = []
    urls, headers_raw, data_parts, forms = [], [], [], []
    url_queries = []
    method = None
    auth = None
    proxy = None
    cookie_str = None
    is_head = False
    get_mode = False
    user_agent = None
    referer = None
    compressed = False

    i = 0
    total = len(tokens)
    while i < total:
        tok = tokens[i]

        if tok == "--":
            i += 1
            continue

        if tok.startswith("--"):
            name, eq, inline = tok[2:].partition("=")
            name = name.lower()
            if eq:
                value, consumed = inline, 1
            elif name in LONG_WITH_VALUE:
                if i + 1 >= total:
                    raise CurlParseError(f"--{name} 缺少参数")
                value, consumed = tokens[i + 1], 2
            else:
                value, consumed = None, 1

            if name == "url":
                if value is not None:
                    urls.append(value)
            elif name == "url-query":
                if value is not None:
                    url_queries.append(value)
            elif name == "request":
                if value:
                    method = value.upper()
            elif name == "header":
                if value is not None:
                    headers_raw.append(value)
            elif name in ("data", "data-ascii", "data-raw", "data-binary"):
                if value is not None:
                    data_parts.append(("data", value))
            elif name == "data-urlencode":
                if value is not None:
                    data_parts.append(("data-urlencode", value))
            elif name == "json":
                if value is not None:
                    data_parts.append(("data", value))
                    if not any(_split_header(h)[0].lower() == "content-type"
                               for h in headers_raw):
                        headers_raw.append("Content-Type: application/json")
                    if not any(_split_header(h)[0].lower() == "accept"
                               for h in headers_raw):
                        headers_raw.append("Accept: application/json")
            elif name in ("form", "form-string"):
                if value is not None:
                    forms.append(value)
            elif name == "user":
                if value is not None:
                    auth = tuple(value.split(":", 1)) if ":" in value else (value, "")
            elif name == "cookie":
                if value is not None:
                    cookie_str = value
            elif name == "user-agent":
                if value is not None:
                    user_agent = value
            elif name == "referer":
                if value is not None:
                    referer = value
            elif name == "proxy":
                if value is not None:
                    proxy = value
            elif name == "compressed":
                compressed = True
            elif name == "head":
                is_head = True
            elif name == "get":
                get_mode = True
            elif name in ("config",):
                warnings.append("不支持 -K/--config 配置文件，已忽略")
            elif name not in LONG_FLAGS and name not in LONG_WITH_VALUE:
                warnings.append(f"未识别的选项 --{name}，已忽略")
            i += consumed
            continue

        if tok.startswith("-") and len(tok) > 1:
            chars = tok[1:]
            j = 0
            consumed = 1
            while j < len(chars):
                ch = chars[j]
                if ch in SHORT_WITH_VALUE:
                    rest = chars[j + 1:]
                    if rest:
                        value = rest
                    else:
                        if i + 1 >= total:
                            raise CurlParseError(f"-{ch} 缺少参数")
                        value = tokens[i + 1]
                        consumed = 2
                    if ch == "X":
                        method = value.upper()
                    elif ch == "H":
                        headers_raw.append(value)
                    elif ch == "d":
                        data_parts.append(("data", value))
                    elif ch == "F":
                        forms.append(value)
                    elif ch == "u":
                        auth = tuple(value.split(":", 1)) if ":" in value else (value, "")
                    elif ch == "b":
                        cookie_str = value
                    elif ch == "A":
                        user_agent = value
                    elif ch == "e":
                        referer = value
                    elif ch == "x":
                        proxy = value
                    j = len(chars)
                    break
                if ch == "I":
                    is_head = True
                elif ch == "G":
                    get_mode = True
                elif ch in SHORT_FLAGS:
                    pass
                else:
                    warnings.append(f"未识别的选项 -{ch}，已忽略")
                j += 1
            i += consumed
            continue

        urls.append(tok)
        i += 1

    if not urls:
        raise CurlParseError("未找到请求 URL，请检查 curl 命令")

    raw_url = urls[-1].strip()
    if not raw_url:
        raise CurlParseError("未找到请求 URL，请检查 curl 命令")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw_url):
        # curl 允许省略协议，默认 http
        raw_url = "http://" + raw_url.lstrip("/")

    headers = {}
    for item in headers_raw:
        key, value = _split_header(item)
        if not key:
            continue
        if key in headers:
            headers[key] = f"{headers[key]}, {value}"
        else:
            headers[key] = value

    if user_agent and not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = user_agent
    if referer and not any(k.lower() == "referer" for k in headers):
        headers["Referer"] = referer

    cookies = {}
    cookie_header = next(
        (v for k, v in headers.items() if k.lower() == "cookie"), None
    )
    if cookie_str:
        if cookie_str.startswith("@"):
            warnings.append("-b @文件 需要读取本地 cookie 文件，已忽略")
        elif "=" in cookie_str:
            cookie_header = cookie_str
        else:
            warnings.append("-b 传入的不是 cookie 字符串，已忽略")

    if cookie_header:
        for piece in cookie_header.split(";"):
            piece = piece.strip()
            if "=" in piece:
                key, value = piece.split("=", 1)
                cookies[key.strip()] = value.strip()
        # 保留原头部，生成代码时注释掉——requests 会从 cookies 自动拼装
        if not any(k.lower() == "cookie" for k in headers):
            headers["Cookie"] = cookie_header

    files, form_fields, form_order = _collect_forms(forms)

    data, data_is_json = (None, False)
    implicit_headers = []
    if forms:
        data = form_fields or None
    elif data_parts:
        # curl 对 -d 系列默认按表单提交，缺省补上对应 Content-Type。
        # 这个头是推断出来的，生成的代码里不写死——requests 对 dict 型 data
        # 会自动带上同样的头，写进 headers 反而是噪音。
        if not any(k.lower() == "content-type" for k in headers):
            headers["content-type"] = "application/x-www-form-urlencoded"
            implicit_headers.append("content-type")
        data, data_is_json = _build_body(data_parts, headers, warnings)

    if method is None:
        if is_head:
            method = "HEAD"
        elif data_parts or forms:
            method = "POST"
        else:
            method = "GET"

    split = urlsplit(raw_url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)

    if get_mode and data is not None:
        extra = data if isinstance(data, dict) else dict(
            parse_qsl(str(data), keep_blank_values=True)
        )
        query_pairs.extend(extra.items())
        data, data_is_json = None, False
        method = "GET"

    for piece in url_queries:
        if "=" in piece:
            key, value = piece.split("=", 1)
            query_pairs.append((key, value))
        else:
            query_pairs.append((piece, ""))

    return {
        "method": method,
        "url": urlunsplit((split.scheme, split.netloc, split.path, "", "")),
        "raw_url": raw_url,
        "queries": [[k, v] for k, v in query_pairs],
        "headers": headers,
        "implicit_headers": implicit_headers,
        "cookies": cookies,
        "data": data,
        "data_is_json": data_is_json,
        "files": files,
        "form_fields": form_fields,
        "form_order": form_order,
        "auth": auth,
        "proxy": proxy,
        "compressed": compressed,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- 代码生成

def _py_str(value):
    return repr(value)


def _py_literal(value, indent=0):
    """生成带缩进的 Python 字面量，风格贴近 curlconverter。"""
    pad = "    " * indent
    inner = "    " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = "\n".join(
            f"{inner}{_py_str(k)}: {_py_literal(v, indent + 1)},"
            for k, v in value.items()
        )
        return "{\n" + body + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        body = "\n".join(
            f"{inner}{_py_literal(v, indent + 1)}," for v in value
        )
        return "[\n" + body + f"\n{pad}]"
    if isinstance(value, str):
        return _py_str(value)
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    return repr(value)


def _assign_block(name, value):
    return [f"{name} = {_py_literal(value)}", ""]


def _header_block(headers, has_cookies):
    lines = ["headers = {"]
    for key, value in headers.items():
        if key.lower() in COMMENTED_HEADERS or (
            has_cookies and key.lower() == "cookie"
        ):
            lines.append(f"    # {_py_str(key)}: {_py_str(value)},")
        else:
            lines.append(f"    {_py_str(key)}: {_py_str(value)},")
    lines.append("}")
    lines.append("")
    return lines


def _files_block(req):
    lines = ["files = {"]
    for key, kind in req["form_order"]:
        if kind == "file":
            path = req["files"].get(key, "")
            lines.append(f"    {_py_str(key)}: open({_py_str(path)}, 'rb'),")
        else:
            lines.append(f"    {_py_str(key)}: (None, {_py_str(req['form_fields'][key])}),")
    lines.append("}")
    lines.append("")
    return lines


def visible_headers(req):
    """生成代码时要写出的头部。

    推断补上的 Content-Type 默认不写（requests 对 dict 型 data 会自动带上）。
    但如果本来就有别的头部需要成块输出，就一并写进去——保持与 curlconverter
    完全一致的输出形态。
    """
    implicit = list(req.get("implicit_headers") or ())
    headers = {k: v for k, v in req["headers"].items() if k not in implicit}
    if headers:
        for key in implicit:
            headers[key] = req["headers"][key]
    return headers


def render_python(req):
    """按 curlconverter 的风格生成 requests 代码。"""
    lines = ["import requests", ""]
    used = {}

    if req["cookies"]:
        lines += _assign_block("cookies", req["cookies"])
        used["cookies"] = True

    headers = visible_headers(req)
    if headers:
        lines += _header_block(headers, bool(req["cookies"]))
        used["headers"] = True

    params = dict(req["queries"])
    if params:
        lines += _assign_block("params", params)
        used["params"] = True

    data_kw = None
    if req["files"]:
        lines += _files_block(req)
        data_kw = "files=files"
    elif req["data"] is not None:
        if isinstance(req["data"], dict):
            name = "json_data" if req["data_is_json"] else "data"
            lines += _assign_block(name, req["data"])
            data_kw = "json=json_data" if req["data_is_json"] else "data=data"
        else:
            lines += [f"data = {_py_str(req['data'])}", ""]
            data_kw = "data=data"

    if req["proxy"]:
        lines += _assign_block("proxies", {"http": req["proxy"], "https": req["proxy"]})
        used["proxies"] = True

    url = req["url"] if params else req["raw_url"]
    call = [_py_str(url)]
    for keyword in ("params", "cookies", "headers"):
        if used.get(keyword):
            call.append(f"{keyword}={keyword}")
    if data_kw:
        call.append(data_kw)
    if req["auth"]:
        call.append(f"auth={_py_literal(tuple(req['auth']))}")
    if used.get("proxies"):
        call.append("proxies=proxies")

    lines.append(f"response = requests.{req['method'].lower()}({', '.join(call)})")
    lines.append("")

    if req["data_is_json"]:
        original = json.dumps(req["data"], ensure_ascii=False, separators=(",", ":"))
        # 原始正文含非 ASCII 时，requests 需要 bytes 才能还原出同样的字节
        literal = _py_str(original)
        if not original.isascii():
            literal += ".encode()"
        alt = [part for part in call if part != data_kw] + ["data=data"]
        lines.append("# Note: json_data will not be serialized by requests")
        lines.append("# exactly as it was in the original request.")
        lines.append(f"#data = {literal}")
        lines.append(f"#response = requests.{req['method'].lower()}({', '.join(alt)})")
        lines.append("")

    lines.append("print(response.text)")
    return "\n".join(lines)


def render_feapder(req):
    """生成 Feapder 爬虫骨架。"""
    headers = visible_headers(req)
    params = dict(req["queries"])
    data = req["data"]
    lines = [
        "import feapder",
        "",
        "",
        "class GeneratedSpider(feapder.AirSpider):",
        "    __custom_setting__ = {\"SPIDER_THREAD_COUNT\": 1}",
        "",
        "    def start_requests(self):",
        (
            f"        yield feapder.Request({req['url']!r}, method={req['method']!r}, "
            f"headers={headers!r}, params={params!r}, data={data!r})"
        ),
        "",
        "    def parse(self, request, response):",
        "        print(response.text)",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    GeneratedSpider.start()",
    ]
    return "\n".join(lines)
