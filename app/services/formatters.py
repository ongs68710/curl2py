import ast
import json
import re


def require_text(payload, field="text", max_bytes=None):
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 不能为空")
    if max_bytes is not None:
        size = len(value.encode("utf-8"))
        if size > max_bytes:
            limit_label = f"{max_bytes // 1024}KB" if max_bytes < 1024 * 1024 else f"{max_bytes // (1024 * 1024)}MB"
            raise ValueError(f"{field} 不能超过 {limit_label}")
    return value


def format_json(text, indent=2):
    if isinstance(indent, bool) or not isinstance(indent, int) or not 0 <= indent <= 8:
        raise ValueError("JSON 缩进必须是 0 到 8 之间的整数")
    value = json.loads(text)
    return json.dumps(value, ensure_ascii=False, indent=indent)


def parse_headers(text):
    result = {}
    warnings = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            warnings.append(f"无法识别 Header 行：{line}")
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        if name:
            result[name] = value.strip()
        else:
            warnings.append("Header 名称不能为空")
    return result, warnings


def parse_cookies(text):
    result = {}
    for item in re.split(r"[;\n]", text):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"无法识别 Cookie：{item}")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError("Cookie 名称不能为空")
        result[name] = value.strip()
    return result


def parse_python_dict(text):
    value = ast.literal_eval(text)
    if not isinstance(value, (dict, list, tuple)):
        raise ValueError("请输入 Python 字典、列表或元组")
    return value


def format_source(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)
