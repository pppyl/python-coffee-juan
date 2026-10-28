"""Offline implementation of the shipped Android HTTP protocol.

Evidence: vk.l (ApiUtil), vk.e (AES256), vk.h1 (SafetyUtil), and the
unpacked BffSignInterceptor/HttpEncryptInterceptor. Signing version 1 is the
application's explicit Java fallback; native white-box version 2 is not faked.
The extracted client constants are loaded from an ignored local file.
"""
from __future__ import annotations

import base64
import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote_plus, urlsplit

from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from private_paths import client_config_path


_CONFIG_PATH = client_config_path()
_NONCE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._~"
_BYPASS_PATHS = frozenset({
    "/app-bff-api/customers/resetPassword", "/app-bff-api/account/password",
    "/app-bff-api/sms/resetPasswordByPhoneToken", "/app-bff-api/resetPasswordToken",
    "/app-bff-api/login", "/app-bff-api/user/register",
    "/app-bff-api/default/account/login/temp",
    "/app-bff-api/default/client/config/node-data",
    "/app-bff-api/default/client/config/diff-modules", "/app-log-api/commit",
    "/mod/v4/product/customization", "/mod/v3/combo/product/customization",
    "/mod/comboThird/product/customization", "/mop/v4/customization/product",
    "/mop/v1/combo/product/customization", "/mop/comboThird/product/customization",
    "mop/product/customization/calculate",
})


@lru_cache(maxsize=1)
def _config() -> dict:
    try:
        config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("缺少本机协议配置，请通过 STARBUCKS_CLIENT_CONFIG 指定私有配置文件。") from exc
    required = ("transport_key", "transport_iv", "login_key", "login_iv",
                "signing_private_key_b64", "channel_prefix", "signature_delimiter")
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
        raise RuntimeError("本机应用协议配置不完整")
    return config


def is_ready() -> bool:
    """Check that the locally extracted configuration can drive both ciphers."""
    try:
        config = _config()
        for prefix in ("transport", "login"):
            if len(config[prefix + "_key"].encode("utf-8")) != 32 or len(config[prefix + "_iv"].encode("utf-8")) != 16:
                return False
        return _signing_key().key_size == 2048
    except (RuntimeError, ValueError, TypeError):
        return False


def compact_json(value: Any, *, html_safe: bool = True) -> str:
    """Match Gson's default compact serialization, including HTML escaping.

    Mapping values set to None are omitted, as Gson omits null object fields;
    null array elements are retained. Pass serialized bytes to build_request()
    when a caller already has an exact source-specific JSON representation.
    """
    def prepare(item):
        if isinstance(item, Mapping):
            return {str(k): prepare(v) for k, v in item.items() if v is not None}
        if isinstance(item, (list, tuple)):
            return [prepare(v) for v in item]
        return item
    result = json.dumps(prepare(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for char in ("\u2028", "\u2029"):
        result = result.replace(char, "\\u%04x" % ord(char))
    if html_safe:
        for char in "<>&='":
            result = result.replace(char, "\\u%04x" % ord(char))
    return result


def _encrypt(text: str, key: str, iv: str) -> bytes:
    padder = padding.PKCS7(128).padder()
    plaintext = padder.update(text.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8"))).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def _decrypt(ciphertext: bytes, key: str, iv: str) -> str:
    decryptor = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8"))).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def encrypt_text(plaintext: str) -> str:
    """vk.h1.c: transport AES-CBC, standard Base64 without line wrapping."""
    config = _config()
    return base64.b64encode(_encrypt(plaintext, config["transport_key"], config["transport_iv"])).decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    config = _config()
    return _decrypt(base64.b64decode(ciphertext, validate=True), config["transport_key"], config["transport_iv"])


def encrypt_login_form(payload: Any) -> str:
    """vk.e.h: legacy login AES, Android Base64.DEFAULT (76 columns + LF)."""
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload if isinstance(payload, str) else compact_json(payload)
    if not text:
        raise ValueError("Login plaintext must not be empty")
    config = _config()
    return base64.encodebytes(_encrypt(text, config["login_key"], config["login_iv"])).decode("ascii")


def decrypt_login_form(ciphertext: str) -> str:
    config = _config()
    return _decrypt(base64.b64decode(ciphertext), config["login_key"], config["login_iv"])


def _query_items(query: str) -> list[tuple[str, str | None]]:
    if not query:
        return []
    result = []
    for item in query.split("&"):
        name, separator, value = item.partition("=")
        result.append((unquote_plus(name), unquote_plus(value) if separator else None))
    return result


def canonical_query(path: str) -> str:
    """vk.l.Q + O: sort unique decoded names and retain their last value."""
    values = dict(_query_items(urlsplit(path).query))
    names = sorted(values, key=lambda item: item.encode("utf-16-be", errors="surrogatepass"))
    return "&".join(name + "=" + ("null" if values[name] is None else values[name]) for name in names)


def signature_message(path: str, body: bytes | str, *, version: str = "10.30.1",
                      nonce: str, signature_version: int = 1) -> str:
    if signature_version != 1:
        raise ValueError("Only the verified Java fallback signature version 1 is available")
    config = _config()
    channel = config["channel_prefix"] + "".join(part.zfill(2) if len(part) == 1 else part for part in version.split("."))
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    return config["signature_delimiter"].join((str(signature_version), channel, str(nonce),
                                                urlsplit(path).path or "/", canonical_query(path), text))


@lru_cache(maxsize=1)
def _signing_key():
    der = base64.b64decode(_config()["signing_private_key_b64"].replace("\\n", ""))
    return serialization.load_der_private_key(der, password=None)


def sign_request(path: str, body: bytes | str, *, version: str = "10.30.1",
                 nonce: str | None = None, signature_version: int = 1) -> str:
    """vk.l.J/I: RSA PKCS#1 v1.5 with SHA-256, source version 1 header."""
    if nonce is None:
        nonce = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(32))
    if len(nonce) != 32 or any(char not in _NONCE_ALPHABET for char in nonce):
        raise ValueError("Version 1 nonce must contain 32 source-alphabet characters")
    message = signature_message(path, body, version=version, nonce=nonce, signature_version=signature_version)
    signature = _signing_key().sign(message.encode("utf-8"), rsa_padding.PKCS1v15(), hashes.SHA256())
    channel = message.split(_config()["signature_delimiter"], 2)[1]
    return "version=1,channel=" + channel + ",nonce=" + nonce + ",sign=" + base64.b64encode(signature).decode("ascii")


def _form_bytes(values: Mapping) -> bytes:
    # OkHttp FormBody.Builder.add: ASCII-only encoding and literal %20 for space.
    return "&".join(quote(str(k), safe="*-._") + "=" + quote(str(v), safe="*-._") for k, v in values.items()).encode("ascii")


def build_request(method: str, path: str, payload: Any = None, version: str = "10.30.1", form: bool = False,
                  *, encrypt: bool = True, response_encrypted: bool = True,
                  nonce: str | None = None, sign_before_encrypt: bool = True) -> tuple[bytes, dict[str, str]]:
    """Return exact wire bytes and protocol headers, without doing I/O.

    For POST, bytes are the body. For GET, bytes are the complete query string
    (without '?') and must replace the URL query. ``form=True`` applies login
    JSON encryption and wraps its result in the source ``body`` form field.
    ``encrypt`` represents the CCMS encryption switch; login always follows
    the shipped bypass list. Auth, language, and device headers belong to the
    calling client. The signing order is caller-selectable for a verified
    network builder configuration.
    """
    method = method.upper()
    if method not in {"POST", "GET"}:
        raise ValueError("Only source-supported GET and POST framing is available")
    headers = {"x-phone-type": "ANDROID", "x-app-version": version}
    use_encryption = encrypt and urlsplit(path).path not in _BYPASS_PATHS
    if method == "GET":
        query = urlsplit(path).query
        if payload is not None:
            if not isinstance(payload, Mapping):
                raise TypeError("GET payload must be a mapping")
            query = _form_bytes(payload).decode("ascii")
        original_path = urlsplit(path)._replace(query=query).geturl()
        original_body = b""
        wire = query.encode("ascii")
        if use_encryption:
            # HttpUrl.query() is decoded before queryToJson splits '='; it
            # retains only segment 1 and overwrites duplicate property names.
            data = {}
            for item in unquote_plus(query).split("&"):
                parts = item.split("=")
                if parts[0]:
                    data[parts[0]] = parts[1] if len(parts) > 1 else ""
            cipher = encrypt_text(compact_json(data, html_safe=False))
            wire = ("encrypted=" + quote(cipher, safe="")).encode("ascii")
            headers["x-data-track"] = "2" if response_encrypted else "3"
        sign_path = original_path if sign_before_encrypt else urlsplit(path)._replace(query=wire.decode("ascii")).geturl()
        sign_body = b""
    else:
        if form:
            original_body = _form_bytes({"body": encrypt_login_form(payload)})
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(payload, bytes):
            original_body = payload
            headers["Content-Type"] = "application/json; charset=UTF-8"
        else:
            # DefaultBodyInterceptor replaces an empty POST body with {}.
            original_body = (payload if isinstance(payload, str) else compact_json({} if payload is None else payload)).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        wire = original_body
        if use_encryption:
            wire = compact_json({"encrypted": encrypt_text(original_body.decode("utf-8"))}, html_safe=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["x-data-track"] = "2" if response_encrypted else "3"
        sign_path = path
        sign_body = original_body if sign_before_encrypt else wire
    headers["x-signature"] = sign_request(sign_path, sign_body, version=version, nonce=nonce)
    return wire, headers


def decode_response(raw: bytes | str, headers: Mapping[str, str] | None = None, *, encrypted: bool | None = None) -> Any:
    """Decrypt mode 2 responses, preserving the app's plaintext-error fallback.

    Pass request headers (responses need not echo x-data-track), or explicitly
    pass ``encrypted`` from the corresponding request mode.
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    normalized = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if encrypted is None:
        encrypted = normalized.get("x-data-track") == "2"
    if encrypted and text:
        try:
            text = decrypt_text(text)
        except (ValueError, UnicodeError):
            pass
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text
