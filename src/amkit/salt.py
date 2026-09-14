"""PII salt: created only by `amkit init --new-salt`, never auto-generated.

The salt file lives in DATA_DIR\\secret and must be backed up by the user; the DB stores only an HMAC
fingerprint so imports can refuse to run with a missing or different salt (pseudonym continuity).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from amkit.errors import PreconditionFailed

FINGERPRINT_MESSAGE = b"amkit-salt-fingerprint-v1"


def create_salt(salt_file: Path) -> bytes:
    if salt_file.exists():
        raise PreconditionFailed(
            f"A salt already exists at {salt_file}. Refusing to overwrite it: pseudonyms would change. "
            "Restore the original salt instead of creating a new one.",
        )
    salt_file.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    salt_file.write_text(value + "\n", encoding="utf-8")
    return value.encode("ascii")


_HEX = set("0123456789abcdefABCDEF")


def read_salt(salt_file: Path) -> bytes | None:
    """Read the salt, tolerating BOMs/UTF-16 from Windows editors; the value must be 64 hex characters."""
    if not salt_file.is_file():
        return None
    raw = salt_file.read_bytes()
    codec = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = raw.decode(codec)
    except UnicodeDecodeError as exc:
        raise PreconditionFailed(f"Salt file {salt_file} is not readable text: {exc}") from exc
    value = text.strip().lstrip(chr(0xFEFF))
    if len(value) != 64 or not set(value) <= _HEX:
        raise PreconditionFailed(f"Salt file {salt_file} is malformed (expected 64 hex characters).")
    return value.lower().encode("ascii")


def fingerprint(salt: bytes) -> str:
    return hmac.new(salt, FINGERPRINT_MESSAGE, hashlib.sha256).hexdigest()[:24]


def require_salt(salt_file: Path, expected_fingerprint: str | None) -> bytes:
    """Return the salt or raise if it is missing or does not match the DB fingerprint."""
    salt = read_salt(salt_file)
    if salt is None:
        raise PreconditionFailed(
            f"PII salt missing at {salt_file}. Restore it from your backup "
            "(or, for a brand-new profile only, run `amkit init --new-salt`).",
        )
    if expected_fingerprint and not hmac.compare_digest(fingerprint(salt), expected_fingerprint):
        raise PreconditionFailed("PII salt does not match this database's fingerprint; restore the original salt.")
    return salt
