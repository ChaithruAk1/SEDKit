"""Branding kept on this machine (`sed branding`): images are validated by type and content, stored per asset outside
the repository, served to the dashboard (404 when not set), and the strip title is plain text."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import branding
from sed.errors import ValidationFailed
from tests.fixtures.api import api_client


def _png(path: Path) -> Path:
    """A valid 1x1 PNG, written by hand (no image library needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))
    return path


def test_assets_are_validated_stored_and_replaced(tmp_path):
    root = tmp_path / "branding"
    logo = _png(tmp_path / "logo-source.png")
    stored = branding.set_asset("logo", logo, root)
    assert Path(stored["file"]) == root / "logo.png" and stored["media_type"] == "image/png"
    assert branding.find_asset("logo", root) == root / "logo.png"
    assert branding.find_asset("watermark", root) is None

    dark = _png(tmp_path / "dark.png")
    branding.set_asset("watermark-dark", dark, root)
    assert branding.status(root)["watermark-dark"] == str(root / "watermark-dark.png")

    with pytest.raises(ValidationFailed, match="Unknown branding asset"):
        branding.set_asset("favicon", logo, root)
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", encoding="utf-8")
    with pytest.raises(ValidationFailed, match="PNG, JPEG or WebP"):
        branding.set_asset("logo", svg, root)
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"GIF89a not really a png")
    with pytest.raises(ValidationFailed, match=r"not a valid \.png image"):
        branding.set_asset("logo", fake, root)
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ValidationFailed, match="between 1 byte"):
        branding.set_asset("logo", empty, root)

    assert branding.clear_asset("logo", root)["removed"] == [str(root / "logo.png")]
    assert branding.find_asset("logo", root) is None


def test_title_is_plain_single_line_text(tmp_path):
    root = tmp_path / "branding"
    assert branding.title(root) is None
    assert branding.set_title("  Fictional   Service\nCentres ", root) == {"title": "Fictional Service Centres"}
    assert branding.title(root) == "Fictional Service Centres"
    assert json.loads((root / "branding.json").read_text(encoding="utf-8")) == {"title": "Fictional Service Centres"}
    with pytest.raises(ValidationFailed, match="plain text"):
        branding.set_title("<b>bold</b>", root)
    with pytest.raises(ValidationFailed, match="at most"):
        branding.set_title("x" * 81, root)
    assert branding.set_title("", root) == {"title": None}
    assert branding.title(root) is None


def test_api_serves_branding_from_the_data_root(ops_profile, tmp_path):
    client = api_client(ops_profile.paths)
    assert client.get("/api/branding").json() == {
        "title": None,
        "logo": False,
        "watermark": False,
        "watermark_dark": False,
    }
    assert client.get("/api/branding/logo").status_code == 404
    assert client.get("/api/branding/secret").status_code == 422

    branding.set_asset("logo", _png(tmp_path / "logo.png"))
    branding.set_title("Fictional Centre")
    body = client.get("/api/branding").json()
    assert body == {"title": "Fictional Centre", "logo": True, "watermark": False, "watermark_dark": False}
    served = client.get("/api/branding/logo")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content.startswith(b"\x89PNG")


def test_cli_sets_and_shows_branding(tmp_path):
    from sed.cli import app

    runner = CliRunner()
    source = _png(tmp_path / "mark.png")
    result = runner.invoke(app, ["branding", "watermark", str(source), "--dark", "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1])["asset"] == "watermark-dark"
    result = runner.invoke(app, ["branding", "title", "Fictional Centre", "--json"])
    assert result.exit_code == 0, result.stdout
    shown = json.loads(runner.invoke(app, ["branding", "show", "--json"]).stdout.strip().splitlines()[-1])
    assert shown["title"] == "Fictional Centre" and shown["watermark-dark"] and shown["logo"] is None
    bad = runner.invoke(app, ["branding", "logo", str(tmp_path / "missing.png"), "--json"])
    assert bad.exit_code == 2
