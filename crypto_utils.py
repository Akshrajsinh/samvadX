"""
crypto_utils.py
----------------
The "unique idea" for this build: every 1:1 chat thread gets its own
AES-256-GCM key, generated the first time two users connect, and never
reused across threads. Messages are stored encrypted at rest; the server
decrypts them only in memory, just long enough to relay/display them.

This is "E2E-style" rather than textbook E2EE: the server still holds the
thread key (so a pure JS port without a real key-exchange UI stays
runnable), but it gets the two properties people actually care about in a
demo/portfolio app:

  1. Per-thread keys -> compromising one conversation's key/database row
     does not expose any other conversation.
  2. A visible "safety number" (fingerprint) derived from the key, shown
     to both users -- the same idea Signal/WhatsApp use so people can
     verify they're really talking to who they think they are, not a
     MITM'd session. If the fingerprint ever changes unexpectedly, the
     UI flags it.

For a production system you'd do real asymmetric key exchange (X3DH/Double
Ratchet) client-side so the server never sees plaintext or keys at all --
that's out of scope for a Flask+SQLite demo, but the storage layout here
(one key per thread, authenticated encryption, rotation support) is the
same shape you'd grow into.
"""

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Server-side "master" secret used only to derive a deterministic but
# unguessable fingerprint salt -- NOT used to encrypt messages themselves.
_FP_SALT = b"vaultgram-fingerprint-salt-v1"


def new_thread_key() -> bytes:
    """32 random bytes -> AES-256 key for one chat thread."""
    return AESGCM.generate_key(bit_length=256)


def key_to_b64(key: bytes) -> str:
    return base64.b64encode(key).decode("ascii")


def key_from_b64(key_b64: str) -> bytes:
    return base64.b64decode(key_b64)


def encrypt_message(key: bytes, plaintext: str) -> tuple[str, str]:
    """Returns (ciphertext_b64, nonce_b64)."""
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(ct).decode("ascii"), base64.b64encode(nonce).decode("ascii")


def decrypt_message(key: bytes, ciphertext_b64: str, nonce_b64: str) -> str:
    aesgcm = AESGCM(key)
    ct = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    pt = aesgcm.decrypt(nonce, ct, None)
    return pt.decode("utf-8")


def compute_fingerprint(key: bytes, user_a: str, user_b: str) -> str:
    """
    Safety-number style fingerprint: a short, human-comparable code derived
    from the thread key + the two usernames. Deterministic given the same
    key/pair, so both participants always see the same value, and it
    changes if the underlying key ever changes (key rotation, tamper, etc).
    Formatted in groups for easy visual comparison, like a hardware key
    fingerprint.
    """
    pair = "|".join(sorted([user_a.lower(), user_b.lower()]))
    digest = hmac.new(_FP_SALT, key + pair.encode("utf-8"), hashlib.sha256).hexdigest()
    digits = "".join(ch for ch in digest if ch.isdigit()) + digest
    code = digits[:20]
    return " ".join(code[i:i + 4] for i in range(0, 20, 4))


def hash_for_audit(value: str) -> str:
    """One-way hash used for lightweight audit logging (e.g. IP bucketing)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
