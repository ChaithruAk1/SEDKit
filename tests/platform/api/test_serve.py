"""`sed serve` (sed.api.serve.run) over real HTTP: 127.0.0.1 only, lock file lifecycle, token injection, dev token."""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from sed import bootstrap, db
from sed.api import serve
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import get_paths
from tests.platform.api.conftest import SECURITY_HEADERS, free_port


@pytest.fixture
def profile(data_root):
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, write_claude_settings=False)
    return paths


@contextmanager
def running(paths, port=None, **kwargs):
    """Run serve.run in a thread; yield (base_url, errors); stop it and wait for the thread on exit."""
    port = port or free_port()
    errors: list[BaseException] = []

    def target() -> None:
        try:
            serve.run(paths, port=port, open_browser=False, **kwargs)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target, name="sed-serve-test", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not errors:
        try:
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    try:
        assert not errors, errors
        yield base, errors
    finally:
        serve.shutdown()
        thread.join(timeout=30)
        assert not thread.is_alive(), "server thread did not stop"


def test_run_serves_the_api_and_holds_the_lock_while_running(profile, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(serve, "default_web_dist", lambda: None)  # API only, even where web/dist has been built
    with running(profile) as (base, _):
        health = httpx.get(f"{base}/api/health")
        assert health.status_code == 200 and health.json()["ok"] is True
        for name, value in SECURITY_HEADERS.items():
            assert health.headers[name] == value
        assert "server" not in health.headers

        meta = httpx.get(f"{base}/api/meta")
        assert meta.status_code == 200 and meta.json()["profile"] == "synthetic"

        assert profile.serve_lock.is_file()
        assert int(profile.serve_lock.read_text(encoding="utf-8").split()[0]) == os.getpid()
        assert db.serve_lock_holder(profile.serve_lock) is not None

        conn = db.connect(profile.db)
        try:
            backup_file = db.backup(conn, tmp_path / "backups", reason="test")
        finally:
            conn.close()
        with pytest.raises(PreconditionFailed, match="sed serve"):
            db.restore(backup_file, profile.db, profile.backups, profile.serve_lock)

        forbidden = httpx.post(f"{base}/api/aliases", json={"kind": "vendor", "raw_value": "a", "target": "b"})
        assert forbidden.status_code == 403 and forbidden.json()["error"]["kind"] == "forbidden"
        assert httpx.get(f"{base}/api/health", headers={"Host": "evil.com"}).status_code == 400
        assert httpx.get(f"{base}/").status_code == 404  # API only

        with pytest.raises(PreconditionFailed, match="already running"):
            serve.run(profile, port=free_port(), open_browser=False)
    assert not profile.serve_lock.exists()
    err = capsys.readouterr().err
    assert "serving on http://127.0.0.1:" in err and "web/dist not found" in err


def test_run_serves_web_dist_with_the_launch_token(profile, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<meta name="sed-token" content="__SED_TOKEN__">', encoding="utf-8")
    monkeypatch.setattr(serve, "default_web_dist", lambda: dist)
    port = free_port()
    with httpx.Client() as client:  # keep-alive connections stay open while the first server shuts down
        with running(profile, port=port) as (base, _):
            index = client.get(f"{base}/")
            assert index.status_code == 200 and index.headers["cache-control"] == "no-store"
            token = re.search(r'content="([^"]+)"', index.text).group(1)
            assert token != "__SED_TOKEN__" and len(token) >= 40
            body = {"kind": "vendor", "raw_value": "Unknown", "target": "Nobody"}
            with_token = client.post(f"{base}/api/aliases", json=body, headers={"X-SED-Token": token})
            assert with_token.status_code == 422  # past the token check; the unknown target is a validation error
        # An immediate restart on the same port works and gets a new token.
        with running(profile, port=port) as (base, _):
            assert re.search(r'content="([^"]+)"', httpx.get(f"{base}/").text).group(1) != token


def test_dev_mode_uses_sed_dev_token(profile, monkeypatch):
    monkeypatch.delenv(serve.DEV_TOKEN_ENV, raising=False)
    with pytest.raises(PreconditionFailed, match="SED_DEV_TOKEN"):
        serve.run(profile, port=free_port(), open_browser=False, dev=True)
    monkeypatch.setenv(serve.DEV_TOKEN_ENV, "not a safe token")
    with pytest.raises(ValidationFailed):
        serve.run(profile, port=free_port(), open_browser=False, dev=True)
    assert not profile.serve_lock.exists()

    monkeypatch.setenv(serve.DEV_TOKEN_ENV, "dev-test-token")
    with running(profile, dev=True) as (base, _):
        body = {"kind": "vendor", "raw_value": "Unknown", "target": "Nobody"}
        url = f"{base}/api/aliases"
        assert httpx.post(url, json=body, headers={"X-SED-Token": "dev-test-token"}).status_code == 422
        assert httpx.post(url, json=body, headers={"X-SED-Token": "other"}).status_code == 403


def test_run_refuses_without_a_database_or_with_a_busy_port(data_root):
    paths = get_paths("synthetic")
    with pytest.raises(PreconditionFailed, match="sed init"):
        serve.run(paths, port=free_port(), open_browser=False)
    bootstrap.init_profile(paths, write_claude_settings=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(PreconditionFailed, match="Cannot listen"):
            serve.run(paths, port=taken.getsockname()[1], open_browser=False)
    assert not paths.serve_lock.exists()


def test_lock_refuses_a_live_holder_and_replaces_a_stale_one(profile):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        profile.serve_lock.write_text(f"{sleeper.pid} 8000\n", encoding="utf-8")
        with pytest.raises(PreconditionFailed, match=f"pid {sleeper.pid}"):
            serve.acquire_lock(profile, port=8001)
    finally:
        sleeper.kill()
        sleeper.wait(timeout=30)
    # The holder is gone: its lock is stale and gets replaced.
    lock = serve.acquire_lock(profile, port=8001)
    assert lock.read_text(encoding="utf-8").split() == [str(os.getpid()), "8001", db.process_start_token(os.getpid())]
    serve.release_lock(profile)
    assert not lock.exists()


def test_a_lock_whose_pid_was_reused_by_another_process_is_stale(profile):
    token = db.process_start_token(os.getpid())
    assert token, "the start time of a live process is readable"
    profile.serve_lock.write_text(f"{os.getpid()} 8000 {token}\n", encoding="utf-8")
    assert db.serve_lock_holder(profile.serve_lock) == os.getpid()
    profile.serve_lock.write_text(f"{os.getpid()} 8000 1\n", encoding="utf-8")  # written by an earlier process
    assert db.serve_lock_holder(profile.serve_lock) is None
    lock = serve.acquire_lock(profile, port=8001)
    assert lock.read_text(encoding="utf-8").split()[:2] == [str(os.getpid()), "8001"]
    serve.release_lock(profile)


@pytest.mark.parametrize("port", [70000, -1])
def test_an_out_of_range_port_is_a_validation_error(profile, port):
    from sed.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="between 0 and 65535"):
        serve.bind_socket(port)


def test_release_keeps_a_lock_that_belongs_to_another_process(profile):
    profile.serve_lock.write_text("4 8000\n", encoding="utf-8")
    serve.release_lock(profile)
    assert profile.serve_lock.exists()


def test_dev_app_factory_reads_profile_and_token_from_the_environment(profile, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SED_PROFILE", "synthetic")
    monkeypatch.setenv(serve.DEV_TOKEN_ENV, "dev-test-token")
    app = serve.dev_app()
    try:
        assert profile.serve_lock.read_text(encoding="utf-8").split()[0] == str(os.getppid())
        client = TestClient(app, base_url="http://127.0.0.1")
        assert client.get("/api/meta").json()["profile"] == "synthetic"
        body = {"kind": "vendor", "raw_value": "Unknown", "target": "Nobody"}
        assert client.post("/api/aliases", json=body, headers={"X-SED-Token": "dev-test-token"}).status_code == 422
    finally:
        Path(profile.serve_lock).unlink(missing_ok=True)


def test_cli_serve_passes_the_options_through(profile, monkeypatch):
    from typer.testing import CliRunner

    from sed.cli import app

    calls = []
    monkeypatch.setattr(serve, "run", lambda paths, **kwargs: calls.append((paths.profile, kwargs)))
    result = CliRunner().invoke(app, ["serve", "--profile", "synthetic", "--no-browser", "--port", "8123", "--dev"])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(app, ["serve", "--profile", "real", "--port", "8124", "--developer-mode"])
    assert result.exit_code == 0, result.output
    assert calls == [
        ("synthetic", {"port": 8123, "open_browser": False, "dev": True, "developer_mode": False}),
        ("real", {"port": 8124, "open_browser": True, "dev": False, "developer_mode": True}),
    ]


def test_the_start_up_line_says_how_sign_in_works(profile, data_root, monkeypatch):
    from sed.api.app import create_app
    from sed.paths import get_paths

    monkeypatch.setenv("USERNAME", "synthetic-user")
    developer = create_app(profile, token="t")
    assert serve.sign_in_line(developer) == (
        "Developer mode: nobody signs in; actions are recorded under the Windows account 'synthetic-user'."
    )
    real = create_app(get_paths("real"), token="t")
    assert serve.sign_in_line(real).startswith("Sign-in required, but nobody can sign in yet: no sign-in provider")


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break is a Windows console event")
def test_cli_serve_stopped_with_ctrl_break_removes_its_lock(profile, data_root, tmp_path):
    port = free_port()
    env = {**os.environ, "SED_DATA_ROOT": str(data_root), "PYTHONUTF8": "1"}
    env.pop("SED_PROFILE", None)
    log = tmp_path / "serve.log"
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "sed", "serve", "--profile", "synthetic", "--no-browser", "--port", str(port)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/meta", timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.2)
            assert proc.poll() is None, log.read_text(encoding="utf-8")
            assert int(profile.serve_lock.read_text(encoding="utf-8").split()[0]) != os.getpid()
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
                if "Shutting down" not in log.read_text(encoding="utf-8"):
                    pytest.skip("console control events are not delivered in this environment")
                raise
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
    output = log.read_text(encoding="utf-8")
    assert code == 0, output
    assert "SED server stopped." in output
    assert not profile.serve_lock.exists()
