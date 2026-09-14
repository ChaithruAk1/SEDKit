from __future__ import annotations

import typer

from sed.cli_common import JsonOpt, handle_errors
from sed.output import emit

app = typer.Typer(no_args_is_help=True, help="Hello test module")


@app.command("ping")
@handle_errors
def ping(as_json: JsonOpt = False) -> None:
    """Reply with pong."""
    emit({"module": "hello", "reply": "pong"}, as_json)
