"""加密 Cookie 收藏夹。

这是项目里唯一的服务端持久化模块，其余功能都是无状态计算。之所以要持久化，
是因为 Cookie 的价值在于跨会话复用：浏览器一关就丢的 Cookie 没有收藏的意义。

存储模型（三层，任一层单独泄露都不足以还原明文）：

    KEK = PBKDF2-HMAC-SHA256(主密码, salt, iterations)
    DEK = 随机 32 字节，用 KEK 通过 AES-256-GCM 封装后写入 vault_meta
    Cookie 值 = AES-256-GCM(DEK, 随机 nonce, AAD = id|name|domain)

解封后的 DEK 不写数据库，只落在容器运行目录（0600），容器重启由 entrypoint
清空。这样做的直接原因是后端以 4 worker 运行，密钥若只存进程内存，换个
worker 就解不开；而把 DEK 明文写库又等于没有加密。

会话票据同样落库（存 sha256），因此多 worker 共享，不依赖粘性会话。

隔离：本模块的接口全部挂在 /api/vault 下，MCP 侧对该前缀做硬拦截，且
数据文件不在 MCP 容器的挂载范围内。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from typing import Any

from Crypto.Cipher import AES

DEFAULT_ITERATIONS = 120_000
MAX_COOKIE_BYTES = 64 * 1024
MAX_COOKIES = 2000
SESSION_TTL_SECONDS = 5 * 60
SESSION_REFRESH_SECONDS = 5 * 60
# 请求签名：sign = MD5(SECRET + 时间戳)，时间戳偏离服务器超过该窗口即拒绝。
# 前端 app.js 里的 md5() 与 SECRET 必须与这里保持一致。
SIGN_WINDOW_SECONDS = 5 * 60
DEFAULT_SIGN_SECRET = "GGB"
MAX_FAILS = 5
LOCK_BASE_SECONDS = 60
MAX_LOCK_SECONDS = 30 * 60

DEK_AAD = b"curl2py-vault-dek-v1"
VERIFY_LABEL = b"curl2py-vault-verify-v1"


class VaultError(Exception):
    """带 HTTP 语义的领域错误。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- 基础配置


def enabled() -> bool:
    return os.environ.get("VAULT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def db_path() -> str:
    configured = os.environ.get("VAULT_DB_PATH", "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "vault.db")


def runtime_dir() -> str:
    return os.environ.get("VAULT_RUNTIME_DIR", "").strip() or os.path.join(os.sep, "tmp", ".c2p-vault")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)


_RUNTIME_DIR_CACHE: str | None = None


def _writable_runtime_dir() -> str:
    """选一个真的能写的运行目录。

    首选 /tmp（compose 里是 tmpfs，容器一停即随内存消失，安全性最好）；
    只读根文件系统或 tmpfs 权限异常时，退到数据库同级目录，保证功能不因环境差异失效。

    探测文件必须唯一：多线程同时探测时共用一个 ``.probe`` 会出现
    「别人先删掉了我的探测文件」→ FileNotFoundError → 误判为目录不可写，
    进而抛 500 并让收藏夹显示成「已锁定」。

    结果按进程缓存：探测要建文件 + fsync + 删除，每个请求都做一次的话，
    收藏夹接口会被这一步拖到上百毫秒（运行目录在进程生命周期内不会变）。
    """
    global _RUNTIME_DIR_CACHE
    if _RUNTIME_DIR_CACHE:
        return _RUNTIME_DIR_CACHE

    candidates = [runtime_dir(), os.path.join(os.path.dirname(db_path()) or ".", ".runtime")]
    for candidate in candidates:
        try:
            os.makedirs(candidate, mode=0o700, exist_ok=True)
            handle_fd, probe = tempfile.mkstemp(prefix=".probe-", dir=candidate)
            try:
                os.write(handle_fd, b"1")
            finally:
                os.close(handle_fd)
            os.remove(probe)
            _RUNTIME_DIR_CACHE = candidate
            return candidate
        except OSError:
            continue
    raise VaultError("无法创建密钥缓存目录，请检查 /tmp 或数据卷的写权限", 500)


class _ReusableConnection:
    """线程级复用的 SQLite 连接包装。

    Windows 上 ``sqlite3.connect()`` 单次约 20ms（打开 -wal/-shm、杀软扫描），
    而收藏夹每个请求要开两条连接 → 单请求 60ms 全耗在建连上。
    这里把连接挂在线程本地，``close()`` 变成回滚并且不真的关闭；
    waitress 的线程池是固定的，因此最多也就十几条常驻连接。
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw

    def close(self) -> None:
        try:
            self._raw.rollback()
        except sqlite3.Error:
            pass

    def __getattr__(self, name: str):
        return getattr(self._raw, name)


_TLS = threading.local()


def _connect() -> sqlite3.Connection:
    path = db_path()
    _ensure_dir(os.path.dirname(path) or ".")
    try:
        stamp = os.stat(path).st_ino if os.path.exists(path) else 0
    except OSError:
        stamp = 0

    cached = getattr(_TLS, "entry", None)
    if cached and cached[0] == path and cached[1] == stamp:
        return cached[2]

    raw = sqlite3.connect(path, timeout=10, isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA synchronous=NORMAL")
    raw.execute("PRAGMA busy_timeout=8000")
    conn = _ReusableConnection(raw)
    _TLS.entry = (path, stamp, conn)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_cookies (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',
    nonce      TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_cookies_name ON vault_cookies(name, domain);
CREATE TABLE IF NOT EXISTS vault_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_attempts (
    client     TEXT PRIMARY KEY,
    fails      INTEGER NOT NULL DEFAULT 0,
    locked_at  REAL NOT NULL DEFAULT 0
);
"""


_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """建表只在「本进程首次接触该库」时做一次。

    原来每次请求都 executescript(SCHEMA)（含多条 CREATE ... IF NOT EXISTS 与
    PRAGMA），在 Windows 上单次约几十毫秒，收藏夹列表因此被拖到 60ms 级；
    库文件被外部替换（体积/路径变化）时重新建表。
    """
    path = db_path()
    try:
        stamp = os.stat(path).st_ino if os.path.exists(path) else 0
    except OSError:
        stamp = 0
    key = (path, stamp)
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return
    conn.executescript(SCHEMA)
    with _SCHEMA_LOCK:
        _SCHEMA_READY.add(key)


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM vault_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO vault_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- 密码学


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive_kek(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> tuple[str, str]:
    cipher = AES.new(key, AES.MODE_GCM, nonce=secrets.token_bytes(12))
    cipher.update(aad)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return _b64e(cipher.nonce), _b64e(tag + ciphertext)


def _open(key: bytes, nonce_b64: str, payload_b64: str, aad: bytes) -> bytes:
    blob = _b64d(payload_b64)
    cipher = AES.new(key, AES.MODE_GCM, nonce=_b64d(nonce_b64))
    cipher.update(aad)
    try:
        return cipher.decrypt_and_verify(blob[16:], blob[:16])
    except ValueError as exc:  # 密文被改动、AAD 不匹配或密钥不对
        raise VaultError("Cookie 数据校验失败：密文可能已被篡改", 409) from exc


def validate_password(password: str) -> str:
    """创建主密码时不做任何强度限制。

    长度、字符组合、是否纯数字全部交给用户决定——收藏夹是本地自有数据，
    用户可能就是要用 4 位 PIN 这种口令，由产品替用户判死刑没有意义。
    这里只挡两件真正会坏事的情况：
      * 不是字符串（JSON 里传数字/对象进来会派生失败）
      * 空白串（等于没有口令，任何人点一下就解锁）
    长度不设上限：PBKDF2 对超长输入的开销由 HMAC 的块处理决定，不会线性增长，
    1000 位的口令实测派生时间与 8 位一致。
    """
    if not isinstance(password, str):
        raise VaultError("密码必须是文本")
    password = password.strip()
    if not password:
        raise VaultError("请输入主密码")
    return password


# ---------------------------------------------------------------- 会话与限流


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _purge_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM vault_sessions WHERE expires_at < ?", (time.time(),))


def _issue_session(conn: sqlite3.Connection) -> dict[str, Any]:
    _purge_sessions(conn)
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO vault_sessions(token_hash, created_at, expires_at) VALUES(?, ?, ?)",
        (_hash_token(token), now, now + SESSION_TTL_SECONDS),
    )
    return {"token": token, "expires_at": now + SESSION_TTL_SECONDS}


def check_session(token: str | None) -> bool:
    if not token or not isinstance(token, str):
        return False
    # 票据只证明「曾经解锁过」，密钥缓存才证明「现在还能解密」。
    # 容器重启会清空密钥缓存而票据仍留在库中，两者必须同时成立才算有效。
    if load_cached_dek() is None:
        return False
    try:
        conn = _connect()
    except sqlite3.Error:
        return False
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT expires_at FROM vault_sessions WHERE token_hash = ?", (_hash_token(token),)
        ).fetchone()
        if not row:
            return False
        now = time.time()
        if row["expires_at"] < now:
            conn.execute("DELETE FROM vault_sessions WHERE token_hash = ?", (_hash_token(token),))
            return False
        if row["expires_at"] - now < SESSION_REFRESH_SECONDS:
            conn.execute(
                "UPDATE vault_sessions SET expires_at = ? WHERE token_hash = ?",
                (now + SESSION_TTL_SECONDS, _hash_token(token)),
            )
        return True
    finally:
        conn.close()


def verify_sign(sign: str, ts: str, secret: str) -> bool:
    """校验请求签名：sign = MD5(secret + ts)，且时间戳必须在窗口内。

    这是浏览器端与后端约定的一层轻量准入（前端以 MD5("GGB"+时间戳) 计算），
    主要用于 /api/vault/attach：页面刷新后票据丢失，只要服务端仍处于解锁态，
    浏览器可用有效签名换一张新票据，避免出现「明明已解锁却提示未解锁」的假状态。
    """
    if not isinstance(sign, str) or not isinstance(ts, str):
        return False
    ts = ts.strip()
    if not ts.isdigit():
        return False
    if abs(time.time() - int(ts)) > SIGN_WINDOW_SECONDS:
        return False
    expected = hashlib.md5(f"{secret}{ts}".encode("utf-8")).hexdigest()
    return hmac.compare_digest(sign.strip().lower(), expected)


def issue_session() -> dict[str, Any]:
    """在库已解锁的前提下直接签发新票据（attach 路径专用）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        return _issue_session(conn)
    finally:
        conn.close()


def revoke_all_sessions() -> None:
    try:
        conn = _connect()
    except sqlite3.Error:
        return
    try:
        _ensure_schema(conn)
        conn.execute("DELETE FROM vault_sessions")
    finally:
        conn.close()


def _attempt_state(conn: sqlite3.Connection, client: str) -> tuple[int, float]:
    row = conn.execute("SELECT fails, locked_at FROM vault_attempts WHERE client = ?", (client,)).fetchone()
    return (row["fails"], row["locked_at"]) if row else (0, 0.0)


def _check_lock(conn: sqlite3.Connection, client: str) -> None:
    fails, locked_at = _attempt_state(conn, client)
    if not locked_at:
        return
    wait = min(LOCK_BASE_SECONDS * (2 ** max(fails - MAX_FAILS, 0)), MAX_LOCK_SECONDS)
    remaining = locked_at + wait - time.time()
    if remaining > 0:
        raise VaultError(f"尝试次数过多，请 {int(remaining) + 1} 秒后再试", 429)


def _record_failure(conn: sqlite3.Connection, client: str) -> None:
    fails, _ = _attempt_state(conn, client)
    fails += 1
    conn.execute(
        "INSERT INTO vault_attempts(client, fails, locked_at) VALUES(?, ?, ?) "
        "ON CONFLICT(client) DO UPDATE SET fails = excluded.fails, locked_at = excluded.locked_at",
        (client, fails, time.time() if fails >= MAX_FAILS else 0.0),
    )


def _clear_failures(conn: sqlite3.Connection, client: str) -> None:
    conn.execute("DELETE FROM vault_attempts WHERE client = ?", (client,))


# ---------------------------------------------------------------- DEK 缓存（跨 worker）


# 运行目录里的密钥缓存是「进程内多线程 + 跨 worker」共享的。waitress 是单进程
# 多线程，固定文件名（runtime.key.tmp / dek.bin.tmp）在并发下会互相覆盖，
# 导致 DEK 用错密钥封装 → 读回失败 → 表现为「明明刚解锁却提示已锁定」。
# 因此：进程内加锁串行化，落盘用唯一临时文件 + 原子替换。
_CACHE_LOCK = threading.RLock()
# 密钥版本号：缓存/丢弃 DEK 时自增，让其它线程的线程本地缓存立即失效
_DEK_EPOCH = 0


def _runtime_paths() -> tuple[str, str]:
    base = _writable_runtime_dir()
    return os.path.join(base, "runtime.key"), os.path.join(base, "dek.bin")


def _atomic_write_bytes(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    _ensure_dir(directory)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _runtime_key(create: bool) -> bytes | None:
    with _CACHE_LOCK:
        key_path, _ = _runtime_paths()
        if os.path.exists(key_path):
            with open(key_path, "rb") as handle:
                data = handle.read()
            if len(data) == 32:
                return data
        if not create:
            return None
        key = secrets.token_bytes(32)
        _atomic_write_bytes(key_path, key)
        # 并发下别的线程可能已先写入，回读以收敛到同一把密钥
        try:
            with open(key_path, "rb") as handle:
                existing = handle.read()
            if len(existing) == 32:
                return existing
        except OSError:
            pass
        return key


def cache_dek(dek: bytes) -> None:
    """把 DEK 用运行期密钥封装后写入运行目录，供其他 worker 读取。"""
    global _DEK_EPOCH
    with _CACHE_LOCK:
        _DEK_EPOCH += 1
        key = _runtime_key(create=True)
        if key is None:
            return
        nonce, payload = _seal(key, dek, b"dek-cache")
        _, dek_path = _runtime_paths()
        body = json.dumps({"nonce": nonce, "payload": payload}).encode("utf-8")
        _atomic_write_bytes(dek_path, body)


def load_cached_dek() -> bytes | None:
    """优先用进程内内存缓存，未命中再读运行目录。

    解密本身是微秒级，但「读文件 + AES 解封」在 Windows 上要 4-5ms，
    而每个受保护请求都要调一次，缓存到内存后直接免掉。
    """
    cached_dek = getattr(_TLS, "dek", None)
    if (
        cached_dek is not None
        and getattr(_TLS, "dek_epoch", -1) == _DEK_EPOCH
        and getattr(_TLS, "dek_path", None) == db_path()
    ):
        return cached_dek

    with _CACHE_LOCK:
        key = _runtime_key(create=False)
        if key is None:
            return None
        _, dek_path = _runtime_paths()
        if not os.path.exists(dek_path):
            return None
        try:
            with open(dek_path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            dek = _open(key, cached["nonce"], cached["payload"], b"dek-cache")
        except Exception:
            return None
        if len(dek) != 32:
            return None
        _TLS.dek = dek
        _TLS.dek_epoch = _DEK_EPOCH
        _TLS.dek_path = db_path()
        return dek


def drop_cached_dek() -> None:
    global _DEK_EPOCH
    with _CACHE_LOCK:
        _DEK_EPOCH += 1
        _, dek_path = _runtime_paths()
        for path in (dek_path, dek_path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------- 初始化与解锁


def _state(conn: sqlite3.Connection) -> dict[str, Any]:
    salt = _meta_get(conn, "salt")
    count = conn.execute("SELECT COUNT(*) AS c FROM vault_cookies").fetchone()["c"]
    return {
        "initialized": bool(salt),
        "unlocked": load_cached_dek() is not None,
        "count": count,
        "iterations": int(_meta_get(conn, "iterations") or DEFAULT_ITERATIONS),
        "version": _meta_get(conn, "version") or "0",
    }


def status(client: str = "") -> dict[str, Any]:
    if not enabled():
        return {"enabled": False, "initialized": False, "unlocked": False, "count": 0}
    try:
        conn = _connect()
    except (sqlite3.Error, OSError) as exc:
        # 最典型的成因是容器没挂数据卷（根文件系统只读，/app/data 写不进去）。
        return {
            "enabled": True,
            "initialized": False,
            "unlocked": False,
            "count": 0,
            "error": "无法打开收藏夹数据库，请确认容器已挂载数据卷（-v curl2py-data:/app/data）",
            "detail": str(exc),
        }
    try:
        _ensure_schema(conn)
        data = _state(conn)
        data["enabled"] = True
        try:
            _check_lock(conn, client)
            data["locked_out"] = False
        except VaultError as exc:
            data["locked_out"] = True
            data["lock_message"] = exc.message
        return data
    finally:
        conn.close()


def initialize(password: str, client: str = "") -> dict[str, Any]:
    password = validate_password(password)
    conn = _connect()
    try:
        _ensure_schema(conn)
        if _meta_get(conn, "salt"):
            raise VaultError("主密码已设置，请直接解锁", 409)
        _check_lock(conn, client)
        salt = secrets.token_bytes(16)
        iterations = int(os.environ.get("VAULT_KDF_ITERATIONS", DEFAULT_ITERATIONS))
        kek = _derive_kek(password, salt, iterations)
        dek = secrets.token_bytes(32)
        nonce, wrapped = _seal(kek, dek, DEK_AAD)
        verifier = hmac.new(kek, VERIFY_LABEL, hashlib.sha256).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _meta_set(conn, "salt", _b64e(salt))
            _meta_set(conn, "iterations", str(iterations))
            _meta_set(conn, "dek_nonce", nonce)
            _meta_set(conn, "dek_wrapped", wrapped)
            _meta_set(conn, "verifier", verifier)
            _meta_set(conn, "version", "1")
            _meta_set(conn, "created_at", str(time.time()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        cache_dek(dek)
        _clear_failures(conn, client)
        session = _issue_session(conn)
        return {"created": True, **session}
    finally:
        conn.close()


def _migrate_legacy_values(dek: bytes) -> None:
    """把历史版本保存的裸文本 Cookie 原地迁移成字典（JSON 对象）形式。

    幂等：已经是 JSON 对象、或拆不成键值对的记录原样保留。在解锁成功后
    执行一次（DEK 已就绪），条目量小（≤ MAX_COOKIES），开销可忽略。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, domain, nonce, ciphertext FROM vault_cookies"
        ).fetchall()
        for row in rows:
            aad = f"{row['id']}|{row['name']}|{row['domain']}".encode("utf-8")
            try:
                plain = _open(dek, row["nonce"], row["ciphertext"], aad).decode("utf-8", "replace")
            except Exception:
                continue  # 解不开的记录不动它，避免把用户数据弄丢
            normalized = _normalize_cookie_text(plain)
            if normalized == plain:
                continue
            nonce, ciphertext = _seal(dek, normalized.encode("utf-8"), aad)
            conn.execute(
                "UPDATE vault_cookies SET nonce=?, ciphertext=? WHERE id=?",
                (nonce, ciphertext, row["id"]),
            )
    finally:
        conn.close()


def unlock(password: str, client: str = "") -> dict[str, Any]:
    if not isinstance(password, str) or not password:
        raise VaultError("请输入主密码")
    conn = _connect()
    try:
        _ensure_schema(conn)
        salt_b64 = _meta_get(conn, "salt")
        if not salt_b64:
            raise VaultError("尚未设置主密码", 409)
        _check_lock(conn, client)
        iterations = int(_meta_get(conn, "iterations") or DEFAULT_ITERATIONS)
        kek = _derive_kek(password, _b64d(salt_b64), iterations)
        expected = _meta_get(conn, "verifier") or ""
        actual = hmac.new(kek, VERIFY_LABEL, hashlib.sha256).hexdigest()
        if not expected or not hmac.compare_digest(expected, actual):
            _record_failure(conn, client)
            fails, _locked = _attempt_state(conn, client)
            remaining = max(MAX_FAILS - fails, 0)
            raise VaultError(f"主密码不正确，还可尝试 {remaining} 次" if remaining else "尝试次数过多，已暂时锁定", 401)
        dek = _open(kek, _meta_get(conn, "dek_nonce") or "", _meta_get(conn, "dek_wrapped") or "", DEK_AAD)
        cache_dek(dek)
        _clear_failures(conn, client)
        _migrate_legacy_values(dek)
        return {"created": False, **_issue_session(conn)}
    finally:
        conn.close()


def lock() -> None:
    drop_cached_dek()
    revoke_all_sessions()


# ---------------------------------------------------------------- Cookie 读写


def _require_key() -> bytes:
    if not enabled():
        raise VaultError("Cookie 收藏夹未启用", 403)
    dek = load_cached_dek()
    if dek is None:
        raise VaultError("收藏夹已锁定，请先输入主密码", 401)
    return dek


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        raw = re.split(r"[,，;；\n]", tags)
    elif isinstance(tags, (list, tuple)):
        raw = []
        for item in tags:
            if isinstance(item, str):
                raw.extend(re.split(r"[,，;；\n]", item))
    else:
        raise VaultError("标签必须是文本或文本数组")
    out: list[str] = []
    for item in raw:
        tag = item.strip()[:24]
        if tag and tag not in out:
            out.append(tag)
    if len(out) > 12:
        raise VaultError("单条 Cookie 最多 12 个标签")
    return out


def _row_public(row: sqlite3.Row) -> dict[str, Any]:
    try:
        tags = json.loads(row["tags"])
    except (TypeError, ValueError):
        tags = []
    return {
        "id": row["id"],
        "name": row["name"],
        "domain": row["domain"],
        "tags": tags,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "size": len(row["ciphertext"]),
    }


def list_cookies(query: str = "", tag: str = "") -> list[dict[str, Any]]:
    _require_key()
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM vault_cookies ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    query = (query or "").strip().lower()
    tag = (tag or "").strip()
    items = []
    for row in rows:
        item = _row_public(row)
        haystack = " ".join([item["name"], item["domain"], " ".join(item["tags"])]).lower()
        if query and query not in haystack:
            continue
        if tag and tag not in item["tags"]:
            continue
        items.append(item)
    return items


def get_cookie(cookie_id: str, with_value: bool = False) -> dict[str, Any]:
    dek = _require_key()
    if not cookie_id:
        raise VaultError("缺少 Cookie 标识")
    conn = _connect()
    try:
        _ensure_schema(conn)
        row = conn.execute("SELECT * FROM vault_cookies WHERE id = ?", (cookie_id,)).fetchone()
        if not row:
            raise VaultError("Cookie 不存在或已被删除", 404)
        item = _row_public(row)
        if with_value:
            aad = f"{row['id']}|{row['name']}|{row['domain']}".encode("utf-8")
            plain = _open(dek, row["nonce"], row["ciphertext"], aad)
            item["value"] = plain.decode("utf-8", "replace")
        return item
    finally:
        conn.close()


def _normalize_cookie_text(value: str) -> str:
    """把 Cookie 文本归一成字典（JSON 对象）形式存储。

    接受两种输入：本身就是 JSON 对象的文本，或 `k=v; k2=v2` 的传统 Cookie
    串（分隔符兼容换行与逗号）。统一存成紧凑 JSON，界面显示与复制都是
    字典形式。遇到无法安全拆成键值对的内容（例如没有 `=` 的片段）时原样
    保留，不做有损转换。
    """
    text = value.strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    pairs: dict[str, str] = {}
    for part in text.replace("\n", ";").replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            return text
        key, _, val = part.partition("=")
        key = key.strip()
        if not key:
            return text
        pairs[key] = val.strip()
    if not pairs:
        return text
    return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))


def save_cookie(name: str, value: str, domain: str = "", tags: Any = None, cookie_id: str = "") -> dict[str, Any]:
    dek = _require_key()
    name = (name or "").strip()
    if not name:
        raise VaultError("请填写名称")
    if len(name) > 80:
        raise VaultError("名称不能超过 80 个字符")
    domain = (domain or "").strip()[:120]
    if not isinstance(value, str) or not value.strip():
        raise VaultError("Cookie 内容不能为空")
    value = _normalize_cookie_text(value)
    if len(value.encode("utf-8")) > MAX_COOKIE_BYTES:
        raise VaultError("单条 Cookie 不能超过 64KB")
    normalized_tags = _normalize_tags(tags)
    now = time.time()
    conn = _connect()
    try:
        _ensure_schema(conn)
        total = conn.execute("SELECT COUNT(*) AS c FROM vault_cookies").fetchone()["c"]
        if not cookie_id and total >= MAX_COOKIES:
            raise VaultError(f"收藏夹最多保存 {MAX_COOKIES} 条")
        if cookie_id:
            row = conn.execute("SELECT * FROM vault_cookies WHERE id = ?", (cookie_id,)).fetchone()
            if not row:
                raise VaultError("Cookie 不存在或已被删除", 404)
        else:
            row = None
        target_id = cookie_id or secrets.token_hex(8)
        aad = f"{target_id}|{name}|{domain}".encode("utf-8")
        nonce, ciphertext = _seal(dek, value.encode("utf-8"), aad)
        duplicate = conn.execute(
            "SELECT id FROM vault_cookies WHERE name = ? AND domain = ? AND id <> ?",
            (name, domain, target_id),
        ).fetchone()
        if duplicate:
            raise VaultError("同名同域名的 Cookie 已存在，请改名或直接编辑已有条目", 409)
        if row:
            conn.execute(
                "UPDATE vault_cookies SET name=?, domain=?, tags=?, nonce=?, ciphertext=?, updated_at=? WHERE id=?",
                (name, domain, json.dumps(normalized_tags, ensure_ascii=False), nonce, ciphertext, now, target_id),
            )
        else:
            conn.execute(
                "INSERT INTO vault_cookies(id, name, domain, tags, nonce, ciphertext, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (target_id, name, domain, json.dumps(normalized_tags, ensure_ascii=False), nonce, ciphertext, now, now),
            )
        saved = conn.execute("SELECT * FROM vault_cookies WHERE id = ?", (target_id,)).fetchone()
        return _row_public(saved)
    except sqlite3.IntegrityError as exc:
        raise VaultError("同名同域名的 Cookie 已存在", 409) from exc
    finally:
        conn.close()


def delete_cookie(cookie_id: str) -> dict[str, Any]:
    _require_key()
    if not cookie_id:
        raise VaultError("缺少 Cookie 标识")
    conn = _connect()
    try:
        _ensure_schema(conn)
        cursor = conn.execute("DELETE FROM vault_cookies WHERE id = ?", (cookie_id,))
        if cursor.rowcount == 0:
            raise VaultError("Cookie 不存在或已被删除", 404)
        return {"deleted": cookie_id}
    finally:
        conn.close()


def tag_summary() -> list[dict[str, Any]]:
    _require_key()
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT tags FROM vault_cookies").fetchall()
    finally:
        conn.close()
    counter: dict[str, int] = {}
    for row in rows:
        try:
            tags = json.loads(row["tags"])
        except (TypeError, ValueError):
            continue
        for tag in tags:
            if isinstance(tag, str) and tag:
                counter[tag] = counter.get(tag, 0) + 1
    return [{"tag": tag, "count": count} for tag, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------- 从 curl 提取


def from_curl(curl: str) -> dict[str, Any]:
    """从 curl 命令里取出 Cookie 与域名，供保存弹窗预填。"""
    from services import curl_parser

    if not isinstance(curl, str) or not curl.strip():
        raise VaultError("请先输入 curl 命令")
    try:
        parsed = curl_parser.parse_curl(curl)
    except curl_parser.CurlParseError as exc:
        raise VaultError(str(exc)) from exc
    cookies = parsed.get("cookies") or {}
    if isinstance(cookies, dict):
        parts = [f"{key}={value}" for key, value in cookies.items()]
    elif isinstance(cookies, (list, tuple)):
        parts = [str(item) for item in cookies]
    else:
        parts = []
    cookie_text = "; ".join(part.strip() for part in parts if part and "=" in part)
    if not cookie_text:
        header_cookie = ""
        headers = parsed.get("headers") or {}
        if isinstance(headers, dict):
            for key, value in headers.items():
                if str(key).lower() == "cookie":
                    header_cookie = str(value)
                    break
        cookie_text = header_cookie.strip()
    domain = ""
    url = parsed.get("url") or parsed.get("raw_url") or ""
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:]+)", str(url))
    if match:
        domain = match.group(1).lower()
    suggested = domain.split(".")[-2] if domain.count(".") >= 1 else domain
    return {
        "cookie": cookie_text,
        "domain": domain,
        "name": f"{suggested or 'cookie'} 会话" if suggested else "未命名 Cookie",
        "count": len([part for part in cookie_text.split(";") if "=" in part]),
    }
