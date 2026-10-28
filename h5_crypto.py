"""Crypto framing observed in Starbucks' public H5 shared JavaScript.

Source: official srkit detail lazy chunk, retained in evidence/h5-http/.
This recovers transport framing only; it does not establish login payloads,
session ownership, device checks or redeemable QR semantics.
"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sm3_hex(text):
    return hashlib.new("sm3", text.encode("utf-8")).hexdigest()


def transport_key():
    # Public client constants, not an account credential. The JS selects entries
    # 3 and 9, interleaves characters, then uses the MD5 hex *text* as AES key.
    left = "3e4f5a6b-7c8d-9e0f-1a2b-3c4d5e6f7a8b"
    right = "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
    combined = "".join((left[i] if i < len(left) else "") + (right[i] if i < len(right) else "")
                       for i in range(max(len(left), len(right))))
    return hashlib.md5(combined.encode("utf-8")).hexdigest().encode("ascii")


def encrypt_text(plaintext):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    worker = Cipher(algorithms.AES(transport_key()), modes.CBC(b"0123456789abcdef")).encryptor()
    return base64.b64encode(worker.update(padded) + worker.finalize()).decode("ascii")


def decrypt_text(ciphertext):
    raw = base64.b64decode(ciphertext, validate=True)
    if not raw or len(raw) % 16:
        raise ValueError("Invalid AES-CBC ciphertext")
    worker = Cipher(algorithms.AES(transport_key()), modes.CBC(b"0123456789abcdef")).decryptor()
    padded = worker.update(raw) + worker.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def post_frame(payload):
    """Caller supplies the observed request schema and nonce; never guesses it."""
    text = compact_json(payload)
    return {"encrypted": encrypt_text(text)}, sm3_hex(text)
