"""API connectors (`sed pull <connector>`): read-only pulls that write export-shaped files into DATA_DIR/inbox.

The normal `sed import` then runs unchanged (same mappings, PII scrubbing, hooks, rule findings and reports), so a
pulled week and an exported week import to the same rows. Connectors never write the database except their
watermarks (`meta` keys `connector.<name>.<source>.watermark`) and never store credentials: secrets come from an
environment variable, the Windows Credential Manager (when `keyring` is installed) or `DATA_DIR/secret/connectors/`.
Configuration is `config/connectors.yaml` (placeholders only), overridden in `DATA_DIR/config/connectors.yaml`.
"""
