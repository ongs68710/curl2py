import json
import ast
import math
import os
import re
import shutil
import subprocess
import sys
import base64
import mimetypes
import tempfile
import secrets
import threading
from functools import wraps
from datetime import datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, jsonify, request, send_from_directory

from services import curl_parser
from services.curl_parser import CurlParseError
from services.formatters import (
    format_source,
    format_json,
    parse_cookies,
    parse_headers,
    parse_python_dict,
    require_text,
)
from services import cookie_vault
from services.crypto_tools import CRYPTO_OPERATIONS, base64_transform, crypto_process, hash_text
from services.diff_tools import build_text_diff, json_diff, visual_json_diff
from services.gzip_middleware import GzipMiddleware
from services.tool_catalog import TOOL_GROUPS
from services.url_tools import decode_url, encode_url, parse_url


app = Flask(__name__, static_folder="static", template_folder="templates")
# 文本响应（HTML/CSS/JS/JSON/SVG）>1KB 时 gzip 压缩，压缩结果按文件版本缓存，
# 避免在 armv7 上对同一份 app.js 反复现压。图片等二进制类型自动跳过。
app.wsgi_app = GzipMiddleware(app.wsgi_app)
# Overall Flask body cap (images). Per-route limits below are stricter and return clearer errors.
# 100MB/张 × 10 张场景下，multipart 由 Werkzeug 落盘缓存，内存只承受分块与流式输出，
# 这里给总请求体留出余量（略高于 IMAGE_MAX_TOTAL_BYTES）。
MAX_REQUEST_BYTES = 220 * 1024 * 1024
CURL_MAX_BYTES = 256 * 1024
SCRIPT_RUNNER_TIMEOUT = 12
SCRIPT_RUNNER_MAX_BYTES = 128 * 1024
TEXT_MAX_BYTES = 1 * 1024 * 1024
DIFF_SIDE_MAX_BYTES = 512 * 1024
IMAGE_MAX_FILES = 10
IMAGE_MAX_BYTES = 100 * 1024 * 1024
IMAGE_MAX_TOTAL_BYTES = 200 * 1024 * 1024
# 3 的倍数：base64 按 3 字节分组，整块对齐后逐块编码无需拼接补位
IMAGE_CHUNK_BYTES = 3 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
API_ERROR_STATUS = 422
API_ERRORS = (ValueError, TypeError, UnicodeError, SyntaxError, RuntimeError, OverflowError, OSError)
# One process-stable identity lets the UI invalidate authorization after a restart.
RUNNER_INSTANCE_ID = os.environ.get("CURL2PY_RUNNER_INSTANCE_ID", "").strip() or secrets.token_urlsafe(12)
# native：纯 Python 解析（默认，毫秒级、零子进程）
# node：curlconverter 子进程（保留用于对照）
# auto：优先 native，解析失败时回退 node
CONVERTER_MODE = os.environ.get("CURL2PY_CONVERTER", "native").strip().lower()


def _script_runner_max_concurrent():
    try:
        return max(1, int(os.environ.get("SCRIPT_RUNNER_MAX_CONCURRENT", "2")))
    except ValueError:
        return 2


_script_runner_gate = threading.BoundedSemaphore(_script_runner_max_concurrent())


def api_token_configured():
    return bool(os.environ.get("CURL2PY_API_TOKEN", "").strip())


def enforce_text_size(value, limit, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是文本")
    size = len(value.encode("utf-8"))
    if size > limit:
        limit_label = f"{limit // 1024}KB" if limit < 1024 * 1024 else f"{limit // (1024 * 1024)}MB"
        raise ValueError(f"{label}不能超过 {limit_label}")
    return value


PROTECTED_API_PATHS = {
    "/convert/curl2python",
    "/convert/ts2date",
    "/convert/date2ts",
}


@app.before_request
def verify_api_token():
    expected = os.environ.get("CURL2PY_API_TOKEN", "").strip()
    is_api_request = request.path.startswith("/api/") or request.path in PROTECTED_API_PATHS
    if not expected or not is_api_request:
        return None
    supplied = request.headers.get("X-API-Token", "")
    authorization = request.headers.get("Authorization", "")
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if supplied != expected:
        return jsonify({"success": False, "error": "缺少或无效的 API Token"}), 401
    return None


def payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def normalize_curl(curl_cmd):
    if not isinstance(curl_cmd, str) or not curl_cmd.strip():
        raise ValueError("请输入 curl 命令")
    text = curl_cmd.replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8")) > CURL_MAX_BYTES:
        raise ValueError("curl 命令不能超过 256KB")
    text = re.sub(r"^\s*curl\.exe\b", "curl", text, flags=re.IGNORECASE)
    return text.strip()


def converter_binary():
    name = os.environ.get("CURLCONVERTER_BIN", "curlconverter")
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"curlconverter 命令不存在：{name}")
    return binary


def run_node_converter(curl_cmd, language):
    """调用 curlconverter 子进程（保留为对照与兜底路径）。"""
    command = [converter_binary(), "--language", language, "-"]
    try:
        result = subprocess.run(
            command,
            input=curl_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("curl 转换超时，请简化命令后重试") from exc
    except OSError as exc:
        raise RuntimeError(f"curlconverter 启动失败：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "curl 转换失败")
    warnings = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return result.stdout.strip(), warnings


def run_converter(curl_cmd, language):
    """把 curl 命令转换为目标语言代码或请求结构。

    默认走纯 Python 解析：原方案每次转换都要 fork 一个 Node 进程，
    冷启动约 1 秒且内存按并发线性叠加；纯 Python 版在同一台 armv7
    设备上耗时不到 1 毫秒。
    """
    text = normalize_curl(curl_cmd)

    if CONVERTER_MODE in ("native", "auto") and language in ("python", "json"):
        try:
            parsed = curl_parser.parse_curl(text)
        except CurlParseError:
            if CONVERTER_MODE == "native":
                raise
            return run_node_converter(text, language)
        if language == "json":
            return json.dumps(parsed, ensure_ascii=False), parsed["warnings"]
        return curl_parser.render_python(parsed), parsed["warnings"]

    return run_node_converter(text, language)


def script_runner_enabled():
    return os.environ.get("ENABLE_SCRIPT_RUNNER", "false").strip().lower() in {"1", "true", "yes", "on"}


def script_runner_ready():
    """Runner is usable only when explicitly enabled and an API token is configured."""
    return script_runner_enabled() and api_token_configured()


def run_python_script(code):
    if not script_runner_enabled():
        raise PermissionError(
            "脚本运行功能未启用，请在 Docker 环境中设置 ENABLE_SCRIPT_RUNNER=true，并配置 CURL2PY_API_TOKEN"
        )
    if not api_token_configured():
        raise PermissionError("启用脚本运行时必须配置 CURL2PY_API_TOKEN")
    if len(code.encode("utf-8")) > SCRIPT_RUNNER_MAX_BYTES:
        raise ValueError("脚本不能超过 128KB")

    if not _script_runner_gate.acquire(blocking=False):
        raise RuntimeError("脚本运行繁忙，请稍后再试")

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    try:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                text=True,
                timeout=SCRIPT_RUNNER_TIMEOUT,
                check=False,
                # 容器里有可写 tmpfs；桌面版没有 /tmp，退到系统临时目录。
                # 注意 Windows 下 os.path.isdir("/tmp") 会命中 C:\tmp（可能是
                # 历史遗留的空目录），所以 NT 上直接用 %TEMP%。
                cwd=("/tmp" if os.name != "nt" and os.path.isdir("/tmp") else tempfile.gettempdir()),
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return {"stdout": stdout, "stderr": stderr, "returncode": None, "timed_out": True}
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
            "timed_out": False,
        }
    finally:
        _script_runner_gate.release()


def post_process_requests(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    candidates = []
    request_methods = {"get", "post", "put", "patch", "delete", "head", "options", "request"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        if not isinstance(function, ast.Attribute) or function.attr not in request_methods:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                candidates.append(target.id)

    if not candidates:
        return code
    variable = candidates[-1]
    print_line = f"print({variable}.text)"
    if print_line not in code:
        return code.rstrip() + f"\n\n{print_line}"
    return code


def curl_to_python(curl_cmd):
    code, warnings = run_converter(curl_cmd, "python")
    return post_process_requests(code), warnings


def resolve_request_target(request_data):
    """还原请求地址与查询参数。

    curlconverter 的 json 输出会把 query 从 url 里剥掉，单独放在 queries 字段；
    部分版本只保留完整的 raw_url。两条路径都要兜底，否则生成的爬虫会丢掉查询参数，
    请求到错误的地址。
    """
    raw_url = request_data.get("raw_url")
    url = request_data.get("url")
    if not isinstance(url, str) or not url.strip():
        url = raw_url if isinstance(raw_url, str) else ""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("转换结果中缺少请求 URL")

    params = request_data.get("params") or request_data.get("queries") or {}
    if isinstance(params, list):
        params = dict(params)
    if not isinstance(params, dict):
        params = {}
    params = {str(key): str(value) for key, value in params.items()}

    split = urlsplit(url)
    if split.query:
        merged = dict(parse_qsl(split.query, keep_blank_values=True))
        merged.update(params)
        params = merged
        url = urlunsplit((split.scheme, split.netloc, split.path, "", split.fragment))

    if not params and isinstance(raw_url, str) and raw_url.strip():
        raw_query = urlsplit(raw_url).query
        if raw_query:
            params = dict(parse_qsl(raw_query, keep_blank_values=True))

    return url, params


def curl_to_feapder(curl_cmd):
    raw_json, warnings = run_converter(curl_cmd, "json")
    request_data = json.loads(raw_json)
    if not isinstance(request_data, dict):
        raise ValueError("curlconverter 未返回有效的请求结构")
    method = str(request_data.get("method", "GET")).upper()
    url, params = resolve_request_target(request_data)
    headers = request_data.get("headers") or {}
    data = request_data.get("data")
    lines = [
        "import feapder",
        "",
        "",
        "class GeneratedSpider(feapder.AirSpider):",
        "    __custom_setting__ = {\"SPIDER_THREAD_COUNT\": 1}",
        "",
        "    def start_requests(self):",
        f"        yield feapder.Request({url!r}, method={method!r}, headers={headers!r}, params={params!r}, data={data!r})",
        "",
        "    def parse(self, request, response):",
        "        print(response.text)",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    GeneratedSpider.start()",
    ]
    return "\n".join(lines), warnings


def ok(result, **extra):
    body = {"success": True, "result": result}
    body.update(extra)
    return jsonify(body)


def fail(message, status=API_ERROR_STATUS):
    return jsonify({"success": False, "error": str(message)}), status


@app.errorhandler(413)
def request_too_large(_exc):
    return jsonify({"success": False, "error": "请求体过大：单张图片上限 100MB，单次合计上限 200MB，请分批转换"}), 413


def api_errors(status=API_ERROR_STATUS):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except API_ERRORS as exc:
                return fail(exc, status)

        return wrapped

    return decorator


@app.errorhandler(413)
def request_too_large(_error):
    limit_mb = MAX_REQUEST_BYTES // (1024 * 1024)
    return fail(
        f"请求体超过服务器上限（{limit_mb}MB）。curl ≤256KB，脚本 ≤128KB，普通文本 ≤1MB，对比单侧 ≤512KB，单张图片 ≤5MB。",
        413,
    )


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "runner_enabled": bool(script_runner_ready()),
        "runner_instance": RUNNER_INSTANCE_ID,
    })


@app.get("/api/tools")
def tools_catalog():
    return jsonify({"groups": TOOL_GROUPS})


@app.post("/api/convert/curl2python")
@api_errors()
def api_curl2python():
    code, warnings = curl_to_python(payload().get("curl", ""))
    return ok(code, warnings=warnings, language="python")


@app.post("/api/convert/curl2feapder")
@api_errors()
def api_curl2feapder():
    code, warnings = curl_to_feapder(payload().get("curl", ""))
    return ok(code, warnings=warnings, language="feapder")


@app.post("/api/run/python")
def api_run_python():
    try:
        return ok(run_python_script(require_text(payload(), "code", max_bytes=SCRIPT_RUNNER_MAX_BYTES)))
    except PermissionError as exc:
        return fail(exc, 403)
    except API_ERRORS as exc:
        return fail(exc)


@app.route("/convert/curl2python", methods=["POST"])
def legacy_curl2python():
    response = api_curl2python()
    if isinstance(response, tuple):
        return response
    data = response.get_json()
    if not data.get("success"):
        return jsonify({"error": data["error"]}), API_ERROR_STATUS
    return jsonify({"code": data["result"], "warnings": data.get("warnings", [])})


@app.post("/api/format/json")
@api_errors()
def api_format_json():
    data = payload()
    indent = data.get("indent", 2)
    if isinstance(indent, bool) or not isinstance(indent, int):
        raise ValueError("JSON 缩进必须是 0 到 8 之间的整数")
    return ok(format_json(require_text(data, max_bytes=TEXT_MAX_BYTES), indent))


@app.post("/api/format/header")
@api_errors()
def api_format_header():
    result, warnings = parse_headers(require_text(payload(), max_bytes=TEXT_MAX_BYTES))
    return ok(result, warnings=warnings)


@app.post("/api/format/cookie")
@api_errors()
def api_format_cookie():
    return ok(parse_cookies(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))


@app.post("/api/format/dict")
@api_errors()
def api_format_dict():
    return ok(parse_python_dict(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))


@app.post("/api/format/javascript")
@api_errors()
def api_format_javascript():
    return ok(format_source(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))


@app.post("/api/format/html")
@api_errors()
def api_format_html():
    return ok(format_source(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))


@app.post("/api/url/parse")
@api_errors()
def api_url_parse():
    try:
        return ok(parse_url(require_text(payload(), "url", max_bytes=TEXT_MAX_BYTES)))
    except ValueError as exc:
        return fail(exc)


@app.post("/api/url/encode")
@api_errors()
def api_url_encode():
    try:
        return ok(encode_url(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))
    except ValueError as exc:
        return fail(exc)


@app.post("/api/url/decode")
@api_errors()
def api_url_decode():
    try:
        return ok(decode_url(require_text(payload(), max_bytes=TEXT_MAX_BYTES)))
    except ValueError as exc:
        return fail(exc)


@app.post("/api/crypto/base64")
@api_errors()
def api_base64():
    try:
        data = payload()
        return ok(base64_transform(require_text(data, max_bytes=TEXT_MAX_BYTES), data.get("operation", "encode")))
    except (ValueError, UnicodeError) as exc:
        return fail(exc)


@app.post("/api/crypto/hash")
@api_errors()
def api_hash():
    data = payload()
    return ok(hash_text(require_text(data, max_bytes=TEXT_MAX_BYTES), data.get("algorithm", "sha256")))


@app.post("/api/crypto/process")
@api_errors()
def api_crypto_process():
    data = payload()
    result = crypto_process(
        data.get("operation", ""),
        require_text(data, max_bytes=TEXT_MAX_BYTES),
        action=data.get("action", "digest"),
        key=data.get("key", ""),
        iv=data.get("iv", ""),
        mode=data.get("mode", "CBC"),
        padding=data.get("padding", "Pkcs7"),
        input_format=data.get("input_format", "Utf8"),
        output_format=data.get("output_format", "Base64"),
    )
    return ok(result, operation=data.get("operation"))


@app.get("/api/crypto/operations")
def crypto_operations():
    return jsonify({"groups": CRYPTO_OPERATIONS})


IMAGE_MIME_PATTERN = re.compile(r"^[\w.+-]+/[\w.+-]+$")
_IMAGE_EXTENSION_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml", "bmp": "image/bmp"}
# armv7 上 base64 全靠 CPU，100MB 级转换不能无限并发
_image_gate = threading.BoundedSemaphore(2)


def _image_mime(image):
    candidate = (image.mimetype or "").split(";")[0].strip().lower()
    if IMAGE_MIME_PATTERN.match(candidate) and candidate != "application/octet-stream":
        return candidate
    extension = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""
    return _IMAGE_EXTENSION_TYPES.get(extension) or mimetypes.guess_type(image.filename)[0] or "application/octet-stream"


def _image_size(image):
    stream = image.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position)
    return size


def _materialize_upload(image):
    """把 Werkzeug 的上传流复制到自管的 SpooledTemporaryFile。

    Werkzeug 在请求收尾时会关闭 request.files 里的流，而流式响应的生成器
    要等到响应阶段才读数据——直接引用会撞上 "I/O operation on closed file"。
    自管副本超过 1MB 自动落盘，内存占用保持有界。
    """
    buffer = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    shutil.copyfileobj(image.stream, buffer, 1024 * 1024)
    buffer.seek(0)
    return buffer


@app.post("/api/image/base64")
@api_errors()
def api_image_base64():
    """图片转 Data URL。结果体可能超过 100MB，因此不走 ok()/jsonify 的整块
    序列化：先完成全部校验（此时还未开始发响应，报错仍是标准 422 JSON），
    再用生成器逐块产出 JSON，输入分块读、输出分块写，内存占用恒定。
    """
    files = [image for image in request.files.getlist("files") if image and image.filename]
    if not files:
        raise ValueError("请至少选择一张图片")
    if len(files) > IMAGE_MAX_FILES:
        raise ValueError(f"一次最多转换 {IMAGE_MAX_FILES} 张图片")
    output = request.form.get("output", "direct")
    if output not in {"direct", "array"}:
        raise ValueError("output 必须是 direct 或 array")
    mimes = []
    total = 0
    for image in files:
        size = _image_size(image)
        if size > IMAGE_MAX_BYTES:
            raise ValueError(f"图片过大（单张上限 {IMAGE_MAX_BYTES // (1024 * 1024)}MB）：{image.filename}")
        total += size
        mimes.append(_image_mime(image))
    if total > IMAGE_MAX_TOTAL_BYTES:
        raise ValueError(f"图片合计超过 {IMAGE_MAX_TOTAL_BYTES // (1024 * 1024)}MB 上限（当前约 {total // (1024 * 1024)}MB），请分批转换")

    buffers = [_materialize_upload(image) for image in files]
    # 排队而不是立刻拒绝：桌面版多开标签/批量转换时，直接报「已有转换在进行」
    # 会让用户以为功能坏了；这里最多等 30 秒，超时才提示。
    if not _image_gate.acquire(timeout=30):
        for buffer in buffers:
            buffer.close()
        raise RuntimeError("图片转换排队超时，请稍后再试")

    def generate():
        try:
            yield b'{"success":true,"result":'
            if output == "array":
                yield b"["
            for index, buffer in enumerate(buffers):
                if index:
                    yield b","
                yield b'"data:' + mimes[index].encode("ascii") + b';base64,'
                while True:
                    chunk = buffer.read(IMAGE_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield base64.b64encode(chunk)
                yield b'"'
            if output == "array":
                yield b"]"
            yield b',"count":' + str(len(files)).encode("ascii") + b',"output":"' + output.encode("ascii") + b'"}'
        finally:
            _image_gate.release()
            for buffer in buffers:
                buffer.close()

    return Response(generate(), mimetype="application/json")


@app.post("/api/json/diff")
@api_errors()
def api_json_diff():
    try:
        data = payload()
        left = require_text(data, "left", max_bytes=DIFF_SIDE_MAX_BYTES)
        right = require_text(data, "right", max_bytes=DIFF_SIDE_MAX_BYTES)
        visual_lines, stats = visual_json_diff(left, right)
        formatted_left = json.dumps(json.loads(left), indent=2, ensure_ascii=False, sort_keys=True)
        formatted_right = json.dumps(json.loads(right), indent=2, ensure_ascii=False, sort_keys=True)
        return ok(json_diff(left, right), visual_lines=visual_lines, stats=stats, formatted_left=formatted_left, formatted_right=formatted_right)
    except ValueError as exc:
        return fail(exc)


@app.post("/api/text/diff")
@api_errors()
def api_text_diff():
    try:
        data = payload()
        left = data.get("left")
        right = data.get("right")
        if not isinstance(left, str) or not isinstance(right, str):
            raise ValueError("left 和 right 必须是文本")
        enforce_text_size(left, DIFF_SIDE_MAX_BYTES, "左侧文本")
        enforce_text_size(right, DIFF_SIDE_MAX_BYTES, "右侧文本")
        result = build_text_diff(left, right)
        return ok(
            result["diff"],
            lines=result["lines"],
            stats=result["stats"],
            visual_lines=result["visual_lines"],
        )
    except ValueError as exc:
        return fail(exc)


def timezone(tz_name):
    if not isinstance(tz_name, str) or not tz_name.strip():
        raise ValueError("时区不能为空")
    try:
        return ZoneInfo(tz_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"不支持的时区：{tz_name}") from exc


def timestamp_to_date(ts_str, unit, tz_name):
    if not isinstance(ts_str, str) or not ts_str.strip():
        raise ValueError("时间戳不能为空")
    if unit not in {"s", "ms"}:
        raise ValueError("时间戳单位必须是 s 或 ms")
    ts = float(ts_str.strip())
    if not math.isfinite(ts):
        raise ValueError("时间戳必须是有限数字")
    if unit == "ms":
        ts /= 1000
    return datetime.fromtimestamp(ts, tz=timezone(tz_name)).strftime("%Y-%m-%d %H:%M:%S")


def date_to_timestamp(dt_str, unit, tz_name):
    if not isinstance(dt_str, str) or not dt_str.strip():
        raise ValueError("日期时间不能为空")
    if unit not in {"s", "ms"}:
        raise ValueError("时间戳单位必须是 s 或 ms")
    value = dt_str.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt).replace(tzinfo=timezone(tz_name))
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError("日期格式不支持，请使用 YYYY-MM-DD HH:MM:SS 或 ISO-8601")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(tz_name))
    timestamp = dt.timestamp()
    if not math.isfinite(timestamp):
        raise ValueError("日期超出可转换范围")
    return str(int(timestamp * 1000)) if unit == "ms" else str(int(timestamp))


@app.post("/api/time/timestamp")
@api_errors()
def api_timestamp():
    try:
        data = payload()
        tz_name = data.get("tz", data.get("timezone", "Asia/Shanghai"))
        return ok(timestamp_to_date(data.get("timestamp", ""), data.get("unit", "s"), tz_name))
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        return fail(exc)


@app.post("/api/time/datetime")
@api_errors()
def api_datetime():
    try:
        data = payload()
        tz_name = data.get("tz", data.get("timezone", "Asia/Shanghai"))
        return ok(date_to_timestamp(data.get("datetime", ""), data.get("unit", "s"), tz_name))
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        return fail(exc)


@app.route("/convert/ts2date", methods=["POST"])
def legacy_ts2date():
    response = api_timestamp()
    if isinstance(response, tuple):
        return response
    data = response.get_json()
    return jsonify({"result": data["result"]}) if data["success"] else (jsonify({"error": data["error"]}), API_ERROR_STATUS)


@app.route("/convert/date2ts", methods=["POST"])
def legacy_date2ts():
    response = api_datetime()
    if isinstance(response, tuple):
        return response
    data = response.get_json()
    return jsonify({"result": data["result"]}) if data["success"] else (jsonify({"error": data["error"]}), API_ERROR_STATUS)


# ============================================================ Cookie 收藏夹
# 全项目唯一的服务端持久化模块，实现见 services/cookie_vault.py。
# 与 MCP 的隔离分三层：
#   1) MCP 服务端对 /api/vault 前缀做黑名单，任何工具都调不到；
#   2) 本组接口要求独立的 X-Vault-Token（由主密码换发），MCP 拿不到；
#   3) 数据文件落在 curl2py 容器数据卷，MCP 容器不挂载该卷。

VAULT_PREFIX = "/api/vault/"


@app.errorhandler(cookie_vault.VaultError)
def vault_error(exc):
    return fail(exc.message, exc.status)


def _is_mcp_client():
    marker = f"{request.headers.get('X-Client-Name', '')} {request.headers.get('User-Agent', '')}".lower()
    return "curl2py-mcp" in marker


@app.before_request
def block_mcp_on_vault():
    """即使 MCP 侧被改动，后端也不接受它访问收藏夹接口。"""
    if request.path.startswith(VAULT_PREFIX) and _is_mcp_client():
        return fail("Cookie 收藏夹不对 MCP 开放，请在浏览器界面操作", 403)
    return None


def _vault_client():
    return (request.remote_addr or "unknown")[:64]


def _vault_session_token():
    return request.headers.get("X-Vault-Token", "")


def _vault_sign_secret():
    return os.environ.get("VAULT_SIGN_SECRET", cookie_vault.DEFAULT_SIGN_SECRET)


def require_unlocked():
    """收藏夹写读接口的双重准入：先验请求签名，再验会话票据。

    签名 = MD5(SECRET + 时间戳)，由浏览器端自动附加，时间戳偏离服务器
    5 分钟以上直接拒绝；票据由主密码（或 attach 换发）取得，5 分钟滑动续期。
    """
    sign = request.headers.get("X-Vault-Sign", "")
    ts = request.headers.get("X-Vault-Ts", "")
    if not cookie_vault.verify_sign(sign, ts, _vault_sign_secret()):
        raise cookie_vault.VaultError("请求签名缺失、无效或已过期（5 分钟窗口）", 401)
    if not cookie_vault.check_session(_vault_session_token()):
        raise cookie_vault.VaultError("收藏夹已锁定，请先输入主密码", 401)


@app.route("/api/vault/status", methods=["GET", "POST"])
def vault_status():
    return jsonify({"success": True, **cookie_vault.status(_vault_client())})


@app.post("/api/vault/attach")
def vault_attach():
    """页面刷新后票据丢失、但服务端仍处于解锁态时，用签名换一张新票据。

    没有这条路径时会出现「明明已解锁，列表也在，一操作就提示已锁定」的
    假状态——这正是用户实测报出的 bug。签名不通过或库未解锁一律 401。
    """
    sign = request.headers.get("X-Vault-Sign", "")
    ts = request.headers.get("X-Vault-Ts", "")
    if not cookie_vault.verify_sign(sign, ts, _vault_sign_secret()):
        raise cookie_vault.VaultError("请求签名缺失、无效或已过期（5 分钟窗口）", 401)
    state = cookie_vault.status(_vault_client())
    if not (state.get("enabled") and state.get("initialized") and state.get("unlocked")):
        raise cookie_vault.VaultError("收藏夹尚未解锁，请先输入主密码", 401)
    return jsonify({"success": True, **cookie_vault.issue_session()})


@app.post("/api/vault/init")
def vault_init():
    password = payload().get("password", "")
    result = cookie_vault.initialize(password, _vault_client())
    return jsonify({"success": True, **result})


@app.post("/api/vault/unlock")
def vault_unlock():
    password = payload().get("password", "")
    result = cookie_vault.unlock(password, _vault_client())
    return jsonify({"success": True, **result})


@app.post("/api/vault/lock")
def vault_lock():
    cookie_vault.lock()
    return ok({"locked": True})


@app.post("/api/vault/list")
def vault_list():
    require_unlocked()
    body = payload()
    return ok(cookie_vault.list_cookies(body.get("query", ""), body.get("tag", "")))


@app.post("/api/vault/detail")
def vault_detail():
    require_unlocked()
    body = payload()
    return ok(cookie_vault.get_cookie(body.get("id", ""), with_value=bool(body.get("with_value"))))


@app.post("/api/vault/save")
def vault_save():
    require_unlocked()
    body = payload()
    return ok(
        cookie_vault.save_cookie(
            name=body.get("name", ""),
            value=body.get("value", ""),
            domain=body.get("domain", ""),
            tags=body.get("tags"),
            cookie_id=body.get("id", ""),
        )
    )


@app.post("/api/vault/delete")
def vault_delete():
    require_unlocked()
    return ok(cookie_vault.delete_cookie(payload().get("id", "")))


@app.post("/api/vault/tags")
def vault_tags():
    require_unlocked()
    return ok(cookie_vault.tag_summary())


@app.post("/api/vault/from_curl")
def vault_from_curl():
    """只解析用户自己输入的 curl，不落库，因此不需要解锁。"""
    curl = enforce_text_size(payload().get("curl", ""), CURL_MAX_BYTES, "curl 命令")
    return ok(cookie_vault.from_curl(curl))


@app.get("/")
def home():
    # HTML 必须可重新校验：模板里的 ?v= 版本号变了，浏览器才会去拿新的
    # JS/CSS。曾经因为版本号没 bump，用户浏览器一直跑旧版 app.js，
    # 出现「已解锁却提示锁定」「保存不转字典」两个假 bug。
    response = send_from_directory(app.template_folder, "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
