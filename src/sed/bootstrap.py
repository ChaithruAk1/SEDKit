"""`sed init`: create a profile's DATA_DIR, database, meta, salt and Claude Code wiring."""

from __future__ import annotations

import os
from typing import Any

from sed import __version__, claude_setup, db
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths
from sed.salt import create_salt, fingerprint, read_salt
from sed.settings import PII_MODES, load_agent_config, load_settings


def reviewer_name() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def init_profile(
    paths: Paths,
    *,
    new_salt: bool = False,
    ai_approval_note: str | None = None,
    pii_mode: str | None = None,
    write_claude_settings: bool = True,
) -> dict[str, Any]:
    # Validate every input and load all config BEFORE touching the filesystem or database.
    if pii_mode is not None and pii_mode not in PII_MODES:
        raise ValidationFailed(f"--pii-mode must be one of {', '.join(PII_MODES)} (lower case), got '{pii_mode}'")
    if ai_approval_note is not None and not ai_approval_note.strip():
        raise ValidationFailed("--ai-approval-note must not be blank")
    settings = load_settings(paths)
    agent = load_agent_config()

    paths.ensure()
    from sed.modules import enabled

    for module in enabled(paths):
        for sub in module.data_subdirs:
            (paths.data_dir / sub).mkdir(parents=True, exist_ok=True)
    created_db = not paths.db.exists()
    conn = db.connect(paths.db)
    try:
        wal = db.enable_wal(conn)
        migration = db.migrate(conn, paths.db, paths.backups)
        meta = db.all_meta(conn)

        existing_class = meta.get("data_class")
        if existing_class and existing_class != paths.data_class:
            raise PreconditionFailed(
                f"Profile '{paths.profile}' expects data_class={paths.data_class} but the DB says {existing_class}.",
            )

        stored_mode = meta.get("pii_mode")
        if stored_mode and pii_mode and pii_mode != stored_mode:
            raise PreconditionFailed("pii_mode cannot be changed after init (pseudonyms already derived).")
        mode = stored_mode or pii_mode or settings.pii_mode
        if mode not in PII_MODES:
            raise PreconditionFailed(f"Stored pii_mode '{mode}' is invalid; this profile must be recreated.")
        if mode == "keep" and paths.data_class == "real":
            raise PreconditionFailed("pii_mode=keep is only allowed for synthetic profiles.")

        salt_created = False
        if new_salt:
            if meta.get("salt_fingerprint"):
                raise PreconditionFailed(
                    "This database already has a salt fingerprint; refusing to create a new salt. "
                    "Restore the original salt file instead.",
                )
            salt = create_salt(paths.salt_file)
            salt_created = True
        else:
            salt = read_salt(paths.salt_file)
            if salt is not None and meta.get("salt_fingerprint") and fingerprint(salt) != meta["salt_fingerprint"]:
                raise PreconditionFailed(
                    "The salt file does not match this database's fingerprint; restore the original."
                )

        with db.write_tx(conn):
            db.set_meta(conn, "data_class", paths.data_class)
            db.set_meta(conn, "profile", paths.profile)
            db.set_meta(conn, "schema_version", str(db.user_version(conn)))
            db.set_meta(conn, "pii_mode", mode)
            db.set_meta(conn, "sed_version", __version__)
            if created_db or not meta.get("created_at"):
                db.set_meta(conn, "created_at", db.utc_now())
            if salt is not None and not meta.get("salt_fingerprint"):
                db.set_meta(conn, "salt_fingerprint", fingerprint(salt))
            if ai_approval_note:
                db.set_meta(conn, "ai_real_data_approved", "true")
                db.set_meta(conn, "ai_approval_note", ai_approval_note.strip())
                db.set_meta(conn, "ai_approval_recorded_by", reviewer_name())
                db.set_meta(conn, "ai_approval_recorded_at", db.utc_now())
        final_meta = db.all_meta(conn)
    finally:
        conn.close()

    claude: dict[str, Any] = {}
    if write_claude_settings:
        claude_setup.register_profile(paths)
        claude["settings_local"] = claude_setup.write_settings_local(agent)
        claude["claude_md_updated"] = claude_setup.render_claude_md(agent)

    warnings = []
    if salt is None and final_meta.get("salt_fingerprint"):
        warnings.append(f"PII salt missing at {paths.salt_file}: restore the original salt file from your backup.")
    elif salt is None:
        warnings.append("No PII salt yet: run `sed init --new-salt` once for this new profile.")
    if paths.data_class == "real" and final_meta.get("ai_real_data_approved") != "true":
        warnings.append("AI approval note not recorded; pass --ai-approval-note before running AI on real data.")

    return {
        "profile": paths.profile,
        "data_dir": str(paths.data_dir),
        "db": str(paths.db),
        "created_db": created_db,
        "journal_mode": wal,
        "migration": migration,
        "salt_created": salt_created,
        "salt_file": str(paths.salt_file),
        "meta": {k: v for k, v in final_meta.items() if k != "ai_approval_note"},
        "claude": claude,
        "warnings": warnings,
    }
