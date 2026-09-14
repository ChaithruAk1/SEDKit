from __future__ import annotations

import pytest

from sed.ingest.pii import TOKEN_MARKER, PiiConfig, PiiProcessor, text_hash
from sed.settings import load_layered

SALT = b"a" * 64


@pytest.fixture
def cfg() -> PiiConfig:
    return PiiConfig.from_dict(load_layered("pii.yaml", None))


def test_pseudonyms_are_deterministic_per_salt(cfg: PiiConfig):
    a = PiiProcessor(SALT, "pseudonymize", cfg)
    b = PiiProcessor(SALT, "pseudonymize", cfg)
    c = PiiProcessor(b"b" * 64, "pseudonymize", cfg)
    assert a.register_person("Jean Dupont") == b.register_person(" jean dupont ")
    assert a.register_person("Jean Dupont") != c.register_person("Jean Dupont")
    assert a.register_person("Jean Dupont").startswith("P-") and len(a.register_person("Jean Dupont")) == 12


def test_name_scrub_uses_global_dictionary_and_variants(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    pid = p.register_person("Élodie Martin")
    p.register_person("Kofi Mensah")
    text = "Called elodie martin, then MARTIN Elodie; also Martin, Élodie and Kofi.Mensah confirmed."
    out = p.scrub(text)
    assert "martin" not in out.lower() and "elodie" not in out.lower() and "mensah" not in out.lower()
    assert out.count(pid) == 3


def test_dictionary_persists_only_hashes(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    p.register_person("Ana Sousa")
    keys = p.new_keys()
    assert keys and all(len(k) == 32 for k, _ in keys)
    assert all("sousa" not in k and "ana" not in k for k, _ in keys)
    assert any(v == TOKEN_MARKER for _, v in keys)
    # A new processor seeded with the persisted keys scrubs the same names.
    q = PiiProcessor(SALT, "pseudonymize", cfg, known_keys=dict(keys))
    assert "Sousa" not in q.scrub("Please ask Ana Sousa")


def test_common_words_not_scrubbed(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    p.register_person("Grace Hopper")
    assert p.scrub("Interface posting timeout after change") == "Interface posting timeout after change"
    assert p.scrub("grace period expired") == "grace period expired"


def test_regex_scrubs(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    out = p.scrub(
        "Mail john.q@example.org or call +33 6 12 34 56 78; host 10.2.3.4; "
        "link https://x.example.com/reset?token=abc123; opened 2026-09-01 10:00:00"
    )
    assert "example.org" not in out and "[email]" in out
    assert "12 34 56 78" not in out and "[phone]" in out
    assert "10.2.3.4" not in out
    assert "token=abc123" not in out
    assert "2026-09-01 10:00:00" in out  # dates are not phone numbers


def test_signature_block_removed(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    text = "Export fails since Monday.\n\nCordialement,\nJeanne Moreau\nFinance Ops\nTel 01 23 45 67 89\n\nNext line"
    out = p.scrub(text)
    assert "Jeanne" not in out and "Finance Ops" not in out
    assert "[signature removed]" in out and "Export fails since Monday." in out and "Next line" in out


def test_drop_and_keep_modes(cfg: PiiConfig):
    drop = PiiProcessor(SALT, "drop", cfg)
    assert drop.register_person("Lars Nilsson") is None
    assert "Nilsson" not in drop.scrub("Lars Nilsson reported it")
    assert "[person]" in drop.scrub("Lars Nilsson reported it")
    keep = PiiProcessor(SALT, "keep", cfg)
    assert keep.register_person("Lars Nilsson") == "Lars Nilsson"
    assert keep.scrub("mail lars@example.org") == "mail lars@example.org"


def test_truncation(cfg: PiiConfig):
    p = PiiProcessor(SALT, "pseudonymize", cfg)
    out = p.scrub("x" * 5000, "description")
    assert len(out) == cfg.truncate["description"]


def test_text_hash_is_whitespace_stable():
    assert text_hash(SALT, "incident", "Login  fails", None) == text_hash(SALT, "incident", "Login fails ", "")
    assert text_hash(SALT, "a") != text_hash(b"z" * 64, "a")
