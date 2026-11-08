import base64
import hashlib
import hmac
import re

from Crypto.Cipher import AES, ARC2, ARC4, Blowfish, ChaCha20, DES, DES3
from Crypto.Util import Counter
from gmssl.sm4 import CryptSM4, SM4_DECRYPT, SM4_ENCRYPT


CRYPTO_OPERATIONS = {
    "symmetric": ["AES", "Blowfish", "DES", "TripleDES", "RC2", "RC4", "RC4Drop", "ChaCha20", "Rabbit", "SM4"],
    "digest": ["MD5", "SHA1", "SHA224", "SHA256", "SHA384", "SHA512", "SHA3-224", "SHA3-256", "SHA3-384", "SHA3-512", "RIPEMD160", "SM3"],
    "hmac": ["HmacMD5", "HmacSHA1", "HmacSHA224", "HmacSHA256", "HmacSHA384", "HmacSHA512"],
    "encoding": ["Utf8", "Utf16", "Utf16LE", "Latin1", "Hex", "Base64", "Base64 URL Safe"],
}

PADDINGS = {"Pkcs7", "Iso97971", "AnsiX923", "Iso10126", "ZeroPadding", "NoPadding"}
MODES = {"CBC", "ECB", "CFB", "OFB", "CTR"}


def _operation_name(operation):
    return str(operation or "").strip()


def _padding_name(padding):
    normalized = re.sub(r"[\s_-]", "", str(padding or "Pkcs7")).lower()
    return {
        "pkcs5": "Pkcs7",
        "pkcs7": "Pkcs7",
        "iso97971": "Iso97971",
        "ansix923": "AnsiX923",
        "iso10126": "Iso10126",
        "zeropadding": "ZeroPadding",
        "zero": "ZeroPadding",
        "nopadding": "NoPadding",
        "none": "NoPadding",
    }.get(normalized, padding or "Pkcs7")


def _decode_input(value, encoding):
    encoding = str(encoding or "Utf8").replace("_", " ").lower()
    if encoding == "hex":
        try:
            return bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError("Hex 输入格式无效") from exc
    if encoding == "base64":
        try:
            return base64.b64decode(value.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Base64 输入格式无效") from exc
    if encoding in {"base64 url safe", "base64urlsafe", "url safe base64"}:
        try:
            return base64.urlsafe_b64decode(value.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Base64 URL Safe 输入格式无效") from exc
    if encoding in {"latin1", "latin 1"}:
        return value.encode("latin-1")
    if encoding == "utf16":
        return value.encode("utf-16")
    if encoding == "utf16le":
        return value.encode("utf-16-le")
    return value.encode("utf-8")


def _encode_output(value, encoding):
    encoding = str(encoding or "Utf8").replace("_", " ").lower()
    if encoding == "hex":
        return value.hex()
    if encoding == "base64":
        return base64.b64encode(value).decode("ascii")
    if encoding in {"base64 url safe", "base64urlsafe", "url safe base64"}:
        return base64.urlsafe_b64encode(value).decode("ascii")
    if encoding in {"latin1", "latin 1"}:
        return value.decode("latin-1")
    if encoding == "utf16":
        return value.decode("utf-16")
    if encoding == "utf16le":
        return value.decode("utf-16-le")
    return value.decode("utf-8")


def _pad(value, block_size, padding):
    padding = _padding_name(padding)
    if padding not in PADDINGS:
        raise ValueError(f"不支持的填充方式：{padding}")
    amount = block_size - len(value) % block_size
    if padding == "NoPadding":
        if len(value) % block_size:
            raise ValueError("NoPadding 要求输入长度是块大小的整数倍")
        return value
    if padding in {"Pkcs7", "Iso10126"}:
        return value + bytes([amount]) * amount
    if padding == "Iso97971":
        return value + b"\x80" + b"\x00" * (amount - 1)
    if padding == "AnsiX923":
        return value + b"\x00" * (amount - 1) + bytes([amount])
    return value + b"\x00" * amount


def _unpad(value, block_size, padding):
    padding = _padding_name(padding)
    if padding == "NoPadding":
        return value
    if not value or len(value) % block_size:
        raise ValueError("密文长度不符合块大小")
    amount = value[-1]
    if padding in {"Pkcs7", "Iso10126"}:
        if amount < 1 or amount > block_size or value[-amount:] != bytes([amount]) * amount:
            raise ValueError("Pkcs7 填充无效")
        return value[:-amount]
    if padding == "Iso97971":
        marker = value.rfind(b"\x80")
        if marker < 0 or any(value[marker + 1:]):
            raise ValueError("Iso97971 填充无效")
        return value[:marker]
    if padding == "AnsiX923":
        if amount < 1 or amount > block_size or value[-amount:-1] != b"\x00" * (amount - 1):
            raise ValueError("AnsiX923 填充无效")
        return value[:-amount]
    return value.rstrip(b"\x00")


def _cipher(operation, key, iv, mode):
    mode = str(mode or "CBC").upper()
    if mode not in MODES:
        raise ValueError(f"不支持的分组模式：{mode}")
    if operation == "AES":
        cipher_cls, valid_lengths, block_size = AES, {16, 24, 32}, 16
    elif operation == "Blowfish":
        cipher_cls, valid_lengths, block_size = Blowfish, set(range(4, 57)), 8
    elif operation == "DES":
        cipher_cls, valid_lengths, block_size = DES, {8}, 8
    elif operation == "TripleDES":
        cipher_cls, valid_lengths, block_size = DES3, {16, 24}, 8
    elif operation == "RC2":
        cipher_cls, valid_lengths, block_size = ARC2, set(range(5, 129)), 8
    else:
        raise ValueError(f"{operation} 暂不支持该分组模式")
    if len(key) not in valid_lengths:
        raise ValueError(f"{operation} 密钥长度不正确")
    if mode == "ECB":
        cipher = cipher_cls.new(key, cipher_cls.MODE_ECB)
    elif mode == "CTR":
        nonce = (iv or b"")[:8]
        cipher = cipher_cls.new(key, cipher_cls.MODE_CTR, counter=Counter.new(64, prefix=nonce, initial_value=0))
    else:
        if len(iv or b"") != block_size:
            raise ValueError(f"{operation} 的 {mode} 模式需要 {block_size} 字节 IV")
        mode_value = getattr(cipher_cls, f"MODE_{mode}")
        cipher = cipher_cls.new(key, mode_value, iv=iv)
    return cipher, block_size, mode


def base64_transform(text, operation):
    if operation == "encode":
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    if operation == "decode":
        return base64.b64decode(text.encode("ascii"), validate=True).decode("utf-8")
    raise ValueError("operation 必须是 encode 或 decode")


def hash_text(text, algorithm):
    if algorithm not in {"md5", "sha1", "sha256"}:
        raise ValueError("仅支持 md5、sha1 和 sha256")
    return hashlib.new(algorithm, text.encode("utf-8")).hexdigest()


def crypto_process(operation, text, action="digest", key="", iv="", mode="CBC", padding="Pkcs7", input_format="Utf8", output_format="Base64"):
    operation = _operation_name(operation)
    if operation in CRYPTO_OPERATIONS["digest"]:
        algorithm = operation.lower().replace("-", "_")
        if algorithm == "sm3":
            algorithm = "sm3"
        return hashlib.new(algorithm, _decode_input(text, input_format)).hexdigest()
    if operation in CRYPTO_OPERATIONS["hmac"]:
        algorithm = operation[4:].lower()
        return hmac.new(_decode_input(key, input_format), _decode_input(text, input_format), algorithm).hexdigest()
    if operation in CRYPTO_OPERATIONS["encoding"]:
        if action.lower() in {"decode", "decrypt"}:
            return _encode_output(_decode_input(text, operation), output_format if output_format != "Base64" else "Utf8")
        return _encode_output(_decode_input(text, input_format), operation)
    if operation == "Base64":
        return _encode_output(_decode_input(text, input_format), "Base64")
    if operation in {"RC4", "RC4Drop"}:
        key_bytes = _decode_input(key, input_format)
        if not 5 <= len(key_bytes) <= 256:
            raise ValueError("RC4 密钥长度必须在 5 到 256 字节之间")
        cipher = ARC4.new(key_bytes)
        if operation == "RC4Drop":
            cipher.encrypt(b"\x00" * 3072)
        data = _decode_input(text, output_format) if action.lower() in {"decrypt", "decode"} else _decode_input(text, input_format)
        result = cipher.encrypt(data)
        return _encode_output(result, input_format if action.lower() in {"decrypt", "decode"} else output_format)
    if operation == "ChaCha20":
        key_bytes = _decode_input(key, input_format)
        nonce = _decode_input(iv, input_format)
        if len(key_bytes) != 32 or len(nonce) not in {8, 12, 24}:
            raise ValueError("ChaCha20 需要 32 字节密钥和 8/12/24 字节 nonce")
        cipher = ChaCha20.new(key=key_bytes, nonce=nonce)
        decrypting = action.lower() in {"decrypt", "decode"}
        data = _decode_input(text, output_format) if decrypting else _decode_input(text, input_format)
        result = cipher.encrypt(data)
        return _encode_output(result, input_format if decrypting else output_format)
    if operation == "SM4":
        key_bytes = _decode_input(key, input_format)
        if len(key_bytes) != 16:
            raise ValueError("SM4 密钥必须是 16 字节")
        iv_bytes = _decode_input(iv, input_format) if iv else b""
        mode = str(mode or "CBC").upper()
        if mode not in {"CBC", "ECB"}:
            raise ValueError("SM4 仅支持 CBC 和 ECB 模式")
        if mode == "CBC" and len(iv_bytes) != 16:
            raise ValueError("SM4 的 CBC 模式需要 16 字节 IV")
        decrypting = action.lower() in {"decrypt", "decode"}
        data = _decode_input(text, output_format) if decrypting else _decode_input(text, input_format)
        crypt = CryptSM4()
        crypt.set_key(key_bytes, SM4_DECRYPT if decrypting else SM4_ENCRYPT)
        if decrypting:
            result = crypt.crypt_cbc(iv_bytes, data) if mode == "CBC" else crypt.crypt_ecb(data)
            result = _unpad(result, 16, padding)
            return _encode_output(result, input_format)
        data = _pad(data, 16, padding)
        result = crypt.crypt_cbc(iv_bytes, data) if mode == "CBC" else crypt.crypt_ecb(data)
        return _encode_output(result, output_format)
    if operation == "Rabbit":
        raise ValueError("Rabbit 暂无稳定的 Python 运行时实现，请使用 CryptoJS 或其他兼容库")
    if operation not in {"AES", "Blowfish", "DES", "TripleDES", "RC2"}:
        raise ValueError(f"暂不支持的加解密类型：{operation}")
    key_bytes = _decode_input(key, input_format)
    iv_bytes = _decode_input(iv, input_format) if iv else b""
    cipher, block_size, actual_mode = _cipher(operation, key_bytes, iv_bytes, mode)
    if action.lower() in {"decrypt", "decode"}:
        result = cipher.decrypt(_decode_input(text, output_format))
        return _encode_output(_unpad(result, block_size, padding), input_format)
    result = cipher.encrypt(_pad(_decode_input(text, input_format), block_size, padding))
    return _encode_output(result, output_format)
