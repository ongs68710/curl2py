TOOL_GROUPS = [
    {
        "id": "common",
        "name": "常用工具",
        "icon": "★",
        "tools": [
            {"id": "curl-python", "name": "curl 转 Python", "description": "将浏览器复制的 curl 转换为 requests 代码"},
            {"id": "curl-feapder", "name": "curl 转 Feapder", "description": "生成适合爬虫项目使用的 Feapder 模板"},
            {"id": "json-diff", "name": "JSON 对比", "description": "查看 JSON 字段新增、删除和修改"},
            {"id": "timestamp", "name": "时间转换", "description": "时间戳与日期时间互相转换"},
        ],
    },
    {
        "id": "cookie",
        "name": "Cookie 管理",
        "icon": "◍",
        "tools": [
            {"id": "cookie-vault", "name": "Cookie 收藏夹", "description": "加密保存常用站点的 Cookie，主密码解锁后可用"},
        ],
    },
    {
        "id": "format",
        "name": "格式化工具",
        "icon": "▦",
        "tools": [
            {"id": "json-format", "name": "JSON 格式化", "description": "格式化、压缩和校验 JSON"},
            {"id": "header-format", "name": "Header 整理", "description": "将 Header 文本转换为字典，并提示无法识别的行"},
            {"id": "cookie-format", "name": "Cookie 格式化", "description": "Cookie 文本转换为字典"},
            {"id": "dict-format", "name": "Dict 格式化", "description": "解析 Python 字典和列表"},
            {"id": "javascript-format", "name": "JavaScript 整理", "description": "清理 JavaScript 文本的空行和首尾空白"},
            {"id": "html-format", "name": "HTML 整理", "description": "清理 HTML 文本的空行和首尾空白"},
        ],
    },
    {
        "id": "url",
        "name": "URL 工具",
        "icon": "↗",
        "tools": [
            {"id": "url-parse", "name": "URL 参数提取", "description": "提取 URL、路径和查询参数"},
            {"id": "url-encode", "name": "URL 编码", "description": "对 URL 或文本进行编码"},
            {"id": "url-decode", "name": "URL 解码", "description": "还原 URL 编码内容"},
        ],
    },
    {
        "id": "text",
        "name": "文本工具",
        "icon": "≡",
        "tools": [
            {"id": "text-diff", "name": "文本对比", "description": "左右对照查看文本差异"},
            {"id": "html-render", "name": "HTML 渲染", "description": "在隔离预览区查看 HTML"},
        ],
    },
    {
        "id": "crypto",
        "name": "加解密工具",
        "icon": "⌁",
        "tools": [
            {"id": "crypto", "name": "加解密工具", "description": "摘要、HMAC、字符编码和常用对称加密", "route": "#/crypto"},
            {"id": "base64", "name": "Base64", "description": "Base64 编码和解码", "route": "#/crypto/base64"},
            {"id": "hash", "name": "摘要计算", "description": "计算 MD5、SHA-1、SHA-2、SHA-3、SM3 等摘要", "route": "#/crypto/hash"},
        ],
    },
    {
        "id": "image",
        "name": "图片工具",
        "icon": "▧",
        "tools": [
            {"id": "image-base64", "name": "图片转 Base64", "description": "单张或批量图片转换为 Data URL 或数组", "route": "#/base64/image"},
        ],
    },
]


TOOL_ROUTES = {
    "cookie-vault": "#/vault",
    "curl-python": "#/curl/python",
    "curl-feapder": "#/curl/feapder",
    "json-diff": "#/json/diff",
    "timestamp": "#/time",
    "json-format": "#/format/json",
    "header-format": "#/format/header",
    "cookie-format": "#/format/cookie",
    "dict-format": "#/format/dict",
    "javascript-format": "#/format/javascript",
    "html-format": "#/format/html",
    "url-parse": "#/url/parse",
    "url-encode": "#/url/encode",
    "url-decode": "#/url/decode",
    "text-diff": "#/text/diff",
    "html-render": "#/text/html",
}

for group in TOOL_GROUPS:
    for tool in group["tools"]:
        route = TOOL_ROUTES.get(tool["id"])
        if route:
            tool["route"] = route
