# -*- coding: utf-8 -*-
"""curl2py 桌面版后端启动器。

与容器内的 gunicorn 启动方式等价，但适配 Windows 桌面运行：
  * 监听 127.0.0.1 的随机空闲端口，不对外暴露；
  * 用 waitress 替代 gunicorn（gunicorn 不支持 Windows）；
  * 数据目录由 Electron 主进程通过环境变量注入，落用户目录而非安装目录；
  * 端口以 ``CURL2PY_READY port=NNNN`` 单行输出到 stdout，供主进程解析。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time


def _bootstrap_paths() -> str:
    """把 app 目录放进 sys.path 并切换工作目录，保证静态资源相对路径可用。"""
    base = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.join(base, "app")
    if not os.path.isdir(app_dir):
        # electron-builder 的 extraResources 布局：resources/app + resources/launcher
        app_dir = os.path.join(base, "resources", "app")
    if not os.path.isdir(app_dir):
        raise RuntimeError("找不到应用源码目录（app/）")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    os.chdir(app_dir)
    return app_dir


def _prepare_env(app_dir: str) -> None:
    """补齐桌面运行必需的默认值，不覆盖主进程已注入的设置。"""
    default_data = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "curl2py", "data")
    default_runtime = os.path.join(os.path.expanduser("~"), "AppData", "Local", "curl2py", "runtime")

    data_dir = os.environ.get("CURL2PY_DATA_DIR") or default_data
    runtime_dir = os.environ.get("CURL2PY_RUNTIME_DIR") or default_runtime
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(runtime_dir, exist_ok=True)

    os.environ.setdefault("VAULT_DB_PATH", os.path.join(data_dir, "vault.db"))
    os.environ.setdefault("VAULT_RUNTIME_DIR", runtime_dir)
    # 桌面版默认走纯 Python 解析器，不依赖外部 node/curlconverter
    os.environ.setdefault("CURL2PY_CONVERTER", "native")
    # 桌面版本机运行，脚本运行器默认开启：接口仍要求 X-API-Token，
    # 令牌由 Electron 主进程每会话随机生成后注入页面，不对局域网暴露。
    os.environ.setdefault("ENABLE_SCRIPT_RUNNER", "true")
    if not os.environ.get("CURL2PY_API_TOKEN", "").strip():
        import secrets as _secrets

        os.environ["CURL2PY_API_TOKEN"] = _secrets.token_urlsafe(24)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def _bind_socket() -> socket.socket:
    """先占住一个空闲端口并保持监听，避免端口被抢占的竞态。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
)


class SecurityHeadersMiddleware:
    """补几个桌面端必需的安全响应头。

    这些头由 WSGI 层统一附加，不改业务代码，也不会碰响应体
    （图片接口是流式响应，改 body 会把内存吃掉）。
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        def _start(status, headers, exc_info=None):
            existing = {name.lower() for name, _ in headers}
            for name, value in SECURITY_HEADERS:
                if name.lower() not in existing:
                    headers.append((name, value))
            return start_response(status, headers, exc_info)

        return self.app(environ, _start)


def _emit_ready(port: int) -> None:
    sys.stdout.write("CURL2PY_READY port=%d\n" % port)
    sys.stdout.flush()


def main() -> int:
    app_dir = _bootstrap_paths()
    _prepare_env(app_dir)

    try:
        from waitress.server import create_server
    except ImportError as exc:  # pragma: no cover - 运行时应自带 waitress
        sys.stderr.write("缺少 waitress：%s\n" % exc)
        return 2

    from app import app as flask_app  # noqa: WPS433  (需在 sys.path 调整之后导入)

    sock = _bind_socket()
    port = sock.getsockname()[1]

    cpu = os.cpu_count() or 4
    server = create_server(
        SecurityHeadersMiddleware(flask_app),
        sockets=[sock],
        # 本地单用户场景：线程数给足（转换/图片/哈希都是短任务），
        # 连接上限压住，避免异常情况把句柄吃满。
        threads=max(8, min(24, cpu * 2)),
        connection_limit=128,
        channel_timeout=45,
        cleanup_interval=15,
        # 兜底体积限制：应用层更严格（图片单张 5MB），这里防止超大请求先撑爆内存
        max_request_body_size=64 * 1024 * 1024,
        max_request_header_size=65536,
        ident="curl2py-desktop",
        clear_untrusted_proxy_headers=True,
    )

    def _watch_parent() -> None:
        """父进程（Electron）退出后自杀，避免残留孤儿进程。"""
        parent = os.environ.get("CURL2PY_PARENT_PID")
        if not parent:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x00100000, False, int(parent))  # SYNCHRONIZE
            if not handle:
                os._exit(0)
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(0)
        except Exception:
            return

    threading.Thread(target=_watch_parent, daemon=True, name="parent-watch").start()

    _emit_ready(port)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
