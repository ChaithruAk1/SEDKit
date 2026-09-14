"""Run handler for the `sed-triage-batch` skill (ws1-ai implements it against sed.ai.contract.SkillHandler)."""

from __future__ import annotations

from typing import Any, ClassVar

from sed.errors import NotImplementedByWorkstream

WS = "ws1-ai"


class TriageBatchHandler:
    skill: ClassVar[str] = "sed-triage-batch"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[Any] = None

    def config_inputs(self, paths: Any) -> dict:
        raise NotImplementedByWorkstream(WS)

    def select(self, ctx: Any) -> list:
        raise NotImplementedByWorkstream(WS)

    def claim(self, ctx: Any, items: list) -> None:
        raise NotImplementedByWorkstream(WS)

    def context_files(self, ctx: Any, items: list) -> dict[str, str]:
        raise NotImplementedByWorkstream(WS)

    def batch_files(self, ctx: Any, batch: str, items: list) -> dict[str, str]:
        raise NotImplementedByWorkstream(WS)

    def validate(self, ctx: Any, output: Any, refs: dict) -> list:
        raise NotImplementedByWorkstream(WS)

    def write(self, ctx: Any, batch_id: str, output: Any, refs: dict) -> Any:
        raise NotImplementedByWorkstream(WS)

    def release(self, ctx: Any) -> int:
        raise NotImplementedByWorkstream(WS)

    def sample_candidates(self, conn: Any, run_id: str) -> list:
        raise NotImplementedByWorkstream(WS)

    def review_card(self, conn: Any, run_id: str, keys: list) -> dict:
        raise NotImplementedByWorkstream(WS)
