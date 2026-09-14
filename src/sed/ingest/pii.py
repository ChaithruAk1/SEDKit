"""PII handling at import time.

* Person fields -> pseudonyms ``P-`` + 10 hex of HMAC-SHA256(salt, normalised name).
* Free text -> signature blocks stripped, then regex scrubs (token URLs, emails, employee IDs, IPs, phones),
  then names replaced using a GLOBAL person dictionary. The dictionary stores only salted hashes of name variants
  ("first last", "last first") and of individual name tokens (for fast pre-filtering) in the person_key table, so
  raw names never persist.
* Content hashes (open/resolved) are keyed HMACs over the normalised PRE-scrub text, so they do not change when the
  dictionary grows or scrub rules improve.

Modes: pseudonymize (default), drop (no identities at all), keep (synthetic profiles only; nothing scrubbed).
Residual risk (documented): unknown third-party names in free text that never appeared in any person field.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

TOKEN_MARKER = "#token"
MAX_NAME_TOKENS = 5
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)
_SEPARATOR_OK = re.compile(r"^[\s.,]{1,3}$")
_DATE_LIKE = re.compile(r"^\d{4}[-/.]\d{2}[-/.]\d{2}|^\d{2}[-/.]\d{2}[-/.]\d{4}")


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^\w\s'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _name_tokens(value: str) -> list[str]:
    return [t for t in (normalize_name(m.group(0)) for m in _TOKEN_RE.finditer(value)) if len(t) >= 2]


def text_hash(salt: bytes, *parts: Any) -> str:
    normalised = "\x1f".join(re.sub(r"\s+", " ", str(p)).strip() if p is not None else "" for p in parts)
    return hmac.new(salt, normalised.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


@dataclass
class PiiConfig:
    pseudonym_prefix: str = "P-"
    pseudonym_hex_chars: int = 10
    truncate: dict[str, int] = field(default_factory=dict)
    regex: dict[str, str | None] = field(default_factory=dict)
    replacements: dict[str, str] = field(default_factory=dict)
    signature_markers: list[str] = field(default_factory=list)
    signature_max_lines: int = 6

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiiConfig:
        return cls(
            pseudonym_prefix=data.get("pseudonym_prefix", "P-"),
            pseudonym_hex_chars=int(data.get("pseudonym_hex_chars", 10)),
            truncate=dict(data.get("truncate") or {}),
            regex=dict(data.get("regex") or {}),
            replacements=dict(data.get("replacements") or {}),
            signature_markers=list(data.get("signature_markers") or []),
            signature_max_lines=int(data.get("signature_max_lines", 6)),
        )


class PiiProcessor:
    def __init__(self, salt: bytes, mode: str, config: PiiConfig, known_keys: dict[str, str] | None = None) -> None:
        if mode not in {"pseudonymize", "drop", "keep"}:
            raise ValueError(f"invalid pii mode {mode}")
        self.salt = salt
        self.mode = mode
        self.cfg = config
        self._keys: dict[str, str] = dict(known_keys or {})  # name/token hash -> pid (or TOKEN_MARKER)
        self._new_keys: dict[str, str] = {}
        self._token_cache: dict[str, bool] = {}
        self.display_names: dict[str, str] = {}
        self._compiled = self._compile_regex()
        markers = [re.escape(m) for m in sorted(config.signature_markers, key=len, reverse=True)]
        self._signature_re = (
            re.compile(r"^\s*(?:" + "|".join(markers) + r")\b[\s,.!:-]*\S{0,40}\s*$", re.IGNORECASE)
            if markers
            else None
        )
        self._name_repl = config.replacements.get("name", "[person]")

    # -- hashing ---------------------------------------------------------------------------------------------

    def _key(self, normalised: str) -> str:
        return hashlib.blake2b(normalised.encode("utf-8"), key=self.salt[:64], digest_size=16).hexdigest()

    def pseudonym(self, raw: str) -> str:
        digest = hmac.new(self.salt, raw.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()
        return self.cfg.pseudonym_prefix + digest[: self.cfg.pseudonym_hex_chars]

    # -- person dictionary -----------------------------------------------------------------------------------

    def register_person(self, raw: Any) -> str | None:
        """Register a person-field value in the dictionary and return its pseudonym (or None in drop mode)."""
        if raw is None or not str(raw).strip():
            return None
        name = str(raw).strip()
        if self.mode == "keep":
            return name
        pid = self.pseudonym(name)
        tokens = _name_tokens(name)[:MAX_NAME_TOKENS]
        if len(tokens) >= 2:
            # Every rotation: "angela van der wal", "van der wal angela", "wal angela van der", ...
            variants = {" ".join(tokens[k:] + tokens[:k]) for k in range(len(tokens))}
            for variant in variants:
                self._add_key(self._key(variant), pid)
            for token in tokens:
                self._add_key(self._key("t:" + token), TOKEN_MARKER)
                self._token_cache[token] = True
        if self.mode == "drop":
            return None
        return pid

    def remember_display_name(self, pid: str | None, raw: Any) -> None:
        if pid and raw:
            self.display_names[pid] = str(raw).strip()

    def _add_key(self, key: str, value: str) -> None:
        if key not in self._keys:
            self._keys[key] = value
            self._new_keys[key] = value

    def new_keys(self) -> list[tuple[str, str]]:
        out = list(self._new_keys.items())
        self._new_keys.clear()
        return out

    def _is_name_token(self, token: str) -> bool:
        cached = self._token_cache.get(token)
        if cached is None:
            cached = self._keys.get(self._key("t:" + token)) == TOKEN_MARKER
            self._token_cache[token] = cached
        return cached

    # -- scrubbing -------------------------------------------------------------------------------------------

    def _compile_regex(self) -> list[tuple[str, re.Pattern[str]]]:
        order = ["token_url", "email", "employee_id", "ipv4", "phone"]
        compiled = []
        for name in order:
            pattern = self.cfg.regex.get(name)
            if pattern:
                compiled.append((name, re.compile(pattern)))
        return compiled

    def strip_signatures(self, text: str) -> str:
        if not self._signature_re:
            return text
        lines = text.splitlines()
        out: list[str] = []
        skip = 0
        for line in lines:
            if skip > 0:
                if not line.strip():
                    skip = 0
                    out.append(line)
                    continue
                skip -= 1
                continue
            if self._signature_re.match(line):
                out.append("[signature removed]")
                skip = self.cfg.signature_max_lines
                continue
            out.append(line)
        return "\n".join(out)

    def _regex_scrub(self, text: str) -> str:
        for name, pattern in self._compiled:
            repl = self.cfg.replacements.get(name, f"[{name}]")
            if name == "phone":
                text = pattern.sub(lambda m, r=repl: self._phone_repl(m, r), text)
            else:
                text = pattern.sub(repl, text)
        return text

    @staticmethod
    def _phone_repl(match: re.Match[str], repl: str) -> str:
        value = match.group(0)
        if sum(ch.isdigit() for ch in value) < 9 or _DATE_LIKE.match(value.strip()):
            return value
        return repl

    def _name_scrub(self, text: str) -> str:
        matches = list(_TOKEN_RE.finditer(text))
        if len(matches) < 2:
            return text
        tokens = [normalize_name(m.group(0)) for m in matches]
        flags = [len(tok) >= 2 and self._is_name_token(tok) for tok in tokens]
        spans: list[tuple[int, int, str]] = []
        i = 0
        while i < len(matches):
            if not flags[i]:
                i += 1
                continue
            found = False
            for width in range(MAX_NAME_TOKENS, 1, -1):
                j = i + width - 1
                if j >= len(matches) or not all(flags[i : j + 1]):
                    continue
                if not all(_SEPARATOR_OK.match(text[matches[k].end() : matches[k + 1].start()]) for k in range(i, j)):
                    continue
                window = tokens[i : j + 1]
                for variant in (" ".join(window),):
                    pid = self._keys.get(self._key(variant))
                    if pid and pid != TOKEN_MARKER:
                        replacement = pid if self.mode == "pseudonymize" else self._name_repl
                        spans.append((matches[i].start(), matches[j].end(), replacement))
                        i = j + 1
                        found = True
                        break
                if found:
                    break
            if not found:
                i += 1
        if not spans:
            return text
        pieces, last = [], 0
        for start, end, replacement in spans:
            pieces.append(text[last:start])
            pieces.append(replacement)
            last = end
        pieces.append(text[last:])
        return "".join(pieces)

    def scrub(self, text: Any, field_name: str | None = None) -> str | None:
        if text is None:
            return None
        value = str(text)
        if not value.strip():
            return None
        if self.mode != "keep":
            value = self.strip_signatures(value)
            value = self._regex_scrub(value)
            value = self._name_scrub(value)
        limit = self.cfg.truncate.get(field_name or "")
        if limit and len(value) > limit:
            value = value[: limit - 1].rstrip() + "…"
        return value

    def register_many(self, names: Iterable[Any]) -> None:
        for name in names:
            self.register_person(name)
