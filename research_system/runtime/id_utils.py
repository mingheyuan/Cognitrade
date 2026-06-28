from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars))


def generate_ulid() -> str:
    """Generate a Crockford Base32 ULID string."""
    timestamp_ms = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return f"{_encode_base32(timestamp_ms, 10)}{_encode_base32(randomness, 16)}"
