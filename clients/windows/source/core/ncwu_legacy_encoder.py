"""Compatibility encoder for the school's observed legacy normal login form.

The first-party login script uses strEnc with a non-standard DES PC-1 key
permutation, UTF-16BE 4-code-unit blocks, zero padding and E-E-E cascade
for keys longer than four code units. This is ONLY interoperability with
the school's existing HTTPS login form, not new crypto or an auth bypass.

No captured secret, account, one-time token or school Javascript is embedded.
"""
from __future__ import annotations

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

_FIPS_PC1 = (
    56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17, 9, 1,
    58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35,
    62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13,
    5, 60, 52, 44, 36, 28, 20, 12, 4, 27, 19, 11, 3,
)
_LEGACY_PC1 = tuple(8 * (7 - j) + i for i in range(7) for j in range(8))


def _blocks_utf16(text: str) -> tuple[bytes, ...]:
    raw = text.encode("utf-16-be", errors="surrogatepass")
    if not raw:
        return ()
    padded = raw + b"\0" * (-len(raw) % 8)
    return tuple(padded[i:i + 8] for i in range(0, len(padded), 8))


def _des_compatible_key(key_block: bytes) -> bytes:
    """Map the site's PC-1 ordering to FIPS PC-1 on a derived 64-bit key."""
    if len(key_block) != 8:
        raise ValueError("legacy key block must be 8 bytes")
    original = int.from_bytes(key_block, "big")
    transformed = 0
    for dst, src in zip(_FIPS_PC1, _LEGACY_PC1):
        bit = (original >> (63 - src)) & 1
        transformed |= bit << (63 - dst)
    return transformed.to_bytes(8, "big")


def encode_legacy_strenc(plaintext: str, ephemeral_key: str) -> str:
    """Hex uppercase compatible with the site's observed browser strEnc."""
    if (not isinstance(plaintext, str) or not plaintext
            or len(plaintext) > 4096 or not isinstance(ephemeral_key, str)
            or not ephemeral_key or len(ephemeral_key) > 128):
        raise ValueError("invalid bounded legacy form payload/key")
    blocks = _blocks_utf16(plaintext)
    keys = _blocks_utf16(ephemeral_key)
    if not keys:
        raise ValueError("server did not provide an ephemeral key")
    output = []
    for block in blocks:
        for key_block in keys:
            cipher = Cipher(TripleDES(_des_compatible_key(key_block)), modes.ECB())
            encryptor = cipher.encryptor()
            block = encryptor.update(block) + encryptor.finalize()
        output.append(block.hex().upper())
    return "".join(output)
