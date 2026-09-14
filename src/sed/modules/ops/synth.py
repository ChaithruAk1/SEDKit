"""Synthetic data for the ops module (wraps the legacy sed.synth generator)."""

from __future__ import annotations

from typing import Any

from sed.modules.contract import SynthRequest
from sed.paths import Paths


def generate(paths: Paths, req: SynthRequest) -> dict[str, Any]:
    from sed.synth.generate import SynthOptions
    from sed.synth.generate import generate as run

    options = SynthOptions(
        seed=req.seed, as_of=req.as_of, anchor=req.anchor, months=req.months, scale=req.scale, clean=req.clean
    )
    return run(paths, options)
