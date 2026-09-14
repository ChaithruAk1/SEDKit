"""`sed serve`: run the API and dashboard on 127.0.0.1 with a per-launch token.

* Binds 127.0.0.1 only (exclusive address use on Windows), one in-process uvicorn server.
* Token: `secrets.token_urlsafe(32)` per launch, or `SED_DEV_TOKEN` with `--dev` (the Vite dev proxy adds it).
  The token is injected into the served index.html and is never printed, logged or put in a URL.
* `DATA_DIR\\serve.lock` holds the server pid while it runs (so `sed db restore` refuses) and is removed on exit.
* Serves `web/dist` when it has an index.html; otherwise API only, with a warning.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths, repo_root

HOST = "127.0.0.1"
DEV_TOKEN_ENV = "SED_DEV_TOKEN"
_active: list[Any] = []  # running uvicorn servers started by run() in this process
_active_lock = threading.Lock()


def _say(message: str) -> None:
    """Status line on stderr (stdout stays free for machine output)."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def default_web_dist() -> Path | None:
    """The built dashboard (`web/dist` in the repo) when it has an index.html, else None."""
    dist = repo_root() / "web" / "dist"
    return dist if (dist / "index.html").is_file() else None


def launch_token(dev: bool) -> str:
    if not dev:
        return secrets.token_urlsafe(32)
    from sed.api.security import validate_token

    token = os.environ.get(DEV_TOKEN_ENV, "")
    if not token:
        raise PreconditionFailed(f"--dev needs {DEV_TOKEN_ENV} (the Vite dev proxy sends the same value).")
    try:
        return validate_token(token)
    except ValueError as exc:
        raise ValidationFailed(f"{DEV_TOKEN_ENV}: {exc}") from exc


# -- lock file ---------------------------------------------------------------------------------------------------


def _lock_pid(lock: Path) -> int | None:
    try:
        return int(lock.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def acquire_lock(paths: Paths, *, port: int, pid: int | None = None) -> Path:
    """Write `serve.lock` (pid and port). Refuses while another live server holds it; replaces a stale lock."""
    from sed import db

    lock = paths.serve_lock
    pid = os.getpid() if pid is None else pid
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        holder = db.serve_lock_holder(lock)
        if holder is not None and holder != pid:
            raise PreconditionFailed(f"`sed serve` is already running for profile '{paths.profile}' (pid {holder}).")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            with contextlib.suppress(OSError):
                lock.unlink()  # stale (dead pid) or ours: replace it
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{pid} {port}\n")
        return lock
    raise PreconditionFailed(f"Cannot create {lock}; another `sed serve` may be starting.")


def release_lock(paths: Paths, *, pid: int | None = None) -> None:
    """Remove `serve.lock` if it is still ours."""
    pid = os.getpid() if pid is None else pid
    if _lock_pid(paths.serve_lock) == pid:
        with contextlib.suppress(OSError):
            paths.serve_lock.unlink()


@contextlib.contextmanager
def serve_lock(paths: Paths, *, port: int) -> Iterator[Path]:
    lock = acquire_lock(paths, port=port)
    try:
        yield lock
    finally:
        release_lock(paths)


# -- server ------------------------------------------------------------------------------------------------------


def bind_socket(port: int) -> socket.socket:
    """Listening socket on 127.0.0.1 only. Exclusive on Windows, so no other process can bind the same port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((HOST, port))
    except OSError as exc:
        sock.close()
        raise PreconditionFailed(f"Cannot listen on {HOST}:{port} ({exc.strerror or exc}); choose --port.") from exc
    return sock


def _open_browser_when_started(server: Any, url: str) -> None:
    def wait_and_open() -> None:
        deadline = time.monotonic() + 30
        while not server.started and not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.1)
        if server.started and not server.should_exit:
            with contextlib.suppress(Exception):
                webbrowser.open(url)

    threading.Thread(target=wait_and_open, name="sed-open-browser", daemon=True).start()


def shutdown() -> None:
    """Ask every server started by run() in this process to stop (tests, embedding)."""
    with _active_lock:
        for server in _active:
            server.should_exit = True


def run(paths: Paths, *, port: int = 8000, open_browser: bool = True, dev: bool = False) -> None:
    import uvicorn

    from sed.api.app import create_app

    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    token = launch_token(dev)
    web_dist = default_web_dist()
    app = create_app(paths, token=token, web_dist=web_dist)
    sock = bind_socket(port)
    try:
        actual_port = sock.getsockname()[1]
        url = f"http://{HOST}:{actual_port}/"
        with serve_lock(paths, port=actual_port):
            config = uvicorn.Config(
                app,
                host=HOST,
                port=actual_port,
                proxy_headers=False,
                server_header=False,
                ws="none",
                lifespan="auto",
                timeout_graceful_shutdown=5,
                log_level="info",
            )
            server = uvicorn.Server(config)
            data_class = paths.data_class.upper()
            _say(f"SED ({data_class}) profile '{paths.profile}' serving on {url}{' [dev token]' if dev else ''}")
            if web_dist is None:
                _say(
                    "warning: web/dist not found; serving the API only "
                    "(build the dashboard with `npm --prefix web run build`)."
                )
            elif open_browser:
                _open_browser_when_started(server, url)
            with _active_lock:
                _active.append(server)
            try:
                server.run(sockets=[sock])
            except KeyboardInterrupt:
                pass  # uvicorn re-raises Ctrl+C after its graceful shutdown; stopping is the normal way out
            finally:
                with _active_lock:
                    _active.remove(server)
        _say("SED server stopped.")
    finally:
        sock.close()


def dev_app() -> Any:
    """`uvicorn --factory sed.api.serve:dev_app --reload` entry used by `scripts/dev.ps1 -Reload`.

    API only; the profile comes from SED_PROFILE and the token from SED_DEV_TOKEN. The lock records the reloader
    (parent) pid, which lives for the whole dev session; a lock left behind after it exits is stale and ignored.
    """
    from sed.api.app import create_app
    from sed.paths import get_paths

    paths = get_paths(os.environ.get("SED_PROFILE"))
    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    token = launch_token(dev=True)
    acquire_lock(paths, port=0, pid=os.getppid())  # port 0: chosen on the uvicorn command line
    return create_app(paths, token=token, web_dist=None)
