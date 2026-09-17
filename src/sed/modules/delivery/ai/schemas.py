"""Output models of the delivery drafting skills: the only source of truth for their output_schema.json files."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from sed.ai.contract import REF_PATTERN, BatchMeta, StrictModel

PRIORITIES = ("highest", "high", "medium", "low", "lowest")
ESTIMATES = (1, 2, 3, 5, 8, 13)
TEST_TYPES = ("functional", "negative", "integration", "performance", "security", "accessibility", "uat")
FACT_KEY_PATTERN = r"^[A-Za-z0-9_.:\-]{1,120}$"
ISSUE_KEY_PATTERN = r"^[A-Z][A-Z0-9_]{1,19}-\d{1,7}$"


def _text(max_length: int, description: str = "", min_length: int = 1) -> Any:
    return Field(min_length=min_length, max_length=max_length, description=description)


# -- sed-draft-stories ---------------------------------------------------------------------------------------------


class Story(StrictModel):
    """One user story drafted from a requirements page."""

    title: str = _text(120, "Short imperative title, unique within the page; no names")
    as_a: str = _text(120, "The role, e.g. 'accounts payable clerk'; never a person's name")
    i_want: str = _text(300, "What the role wants to do")
    so_that: str = _text(300, "The business benefit")
    acceptance_criteria: list[str] = Field(
        min_length=1, max_length=8, description="Testable criteria, ideally 'Given ..., when ..., then ...'"
    )
    priority: Literal["highest", "high", "medium", "low", "lowest"] = "medium"
    estimate_points: Literal[1, 2, 3, 5, 8, 13] | None = Field(None, description="Relative size, or null when unknown")
    epic_key: str | None = Field(
        None, pattern=ISSUE_KEY_PATTERN, description="An epic key listed in context.md for this project, or null"
    )


class StorySet(StrictModel):
    ref: str = Field(pattern=REF_PATTERN)
    stories: list[Story] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(
        default_factory=list, max_length=8, description="What the page leaves unclear; required when stories is empty"
    )
    confidence: float = Field(ge=0, le=1)


class StoriesOutput(StrictModel):
    """The JSON file the agent writes for one packet of requirements pages."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[StorySet]


# -- sed-draft-adr -------------------------------------------------------------------------------------------------


class AdrOption(StrictModel):
    name: str = _text(80, "Short option name, unique within the ADR")
    pros: list[str] = Field(default_factory=list, max_length=6)
    cons: list[str] = Field(default_factory=list, max_length=6)


class Adr(StrictModel):
    """One architecture decision record in the MADR shape, status proposed."""

    title: str = _text(100, "The decision in a few words, e.g. 'Use the integration layer for tax validation'")
    context: str = _text(1200, "The problem and the forces at play, from the packet only")
    drivers: list[str] = Field(min_length=1, max_length=6, description="Decision drivers")
    options: list[AdrOption] = Field(min_length=2, max_length=4)
    chosen_option: str = _text(80, "The name of one of the options, verbatim")
    rationale: str = _text(800, "Why the chosen option wins against the drivers")
    consequences: list[str] = Field(min_length=1, max_length=8, description="Positive and negative consequences")
    related_page_ids: list[str] = Field(
        default_factory=list, max_length=10, description="page_id values of packet pages this ADR relies on"
    )


class AdrSet(StrictModel):
    ref: str = Field(pattern=REF_PATTERN)
    adrs: list[Adr] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)


class AdrOutput(StrictModel):
    """The JSON file the agent writes for the project's design packet."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[AdrSet]


# -- sed-draft-test-plan -------------------------------------------------------------------------------------------


class PlanCase(StrictModel):
    """One manual or automatable test case covering one story."""

    title: str = _text(120, "What the case checks, unique within the plan")
    story: str = _text(120, "The title of the story it covers, verbatim from the packet")
    type: Literal["functional", "negative", "integration", "performance", "security", "accessibility", "uat"]
    preconditions: list[str] = Field(default_factory=list, max_length=6)
    steps: list[str] = Field(min_length=1, max_length=12)
    expected: str = _text(400, "The observable expected result")


class CasePlan(StrictModel):
    ref: str = Field(pattern=REF_PATTERN)
    cases: list[PlanCase] = Field(min_length=1, max_length=40)
    not_covered: list[str] = Field(
        default_factory=list, max_length=8, description="Risks or criteria these cases do not cover, and why"
    )
    confidence: float = Field(ge=0, le=1)


class CasesOutput(StrictModel):
    """The JSON file the agent writes for one packet of approved story sets."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[CasePlan]


# -- sed-draft-release-notes ---------------------------------------------------------------------------------------


class ReleaseEntry(StrictModel):
    text: str = _text(300, "One user-facing change in plain words; numbers only as {{f:<fact_key>}} tokens")
    issue_keys: list[str] = Field(min_length=1, max_length=20, description="Packet issue keys this entry covers")


class ReleaseSection(StrictModel):
    heading: str = _text(60, "e.g. 'New features', 'Improvements', 'Fixes'")
    entries: list[ReleaseEntry] = Field(min_length=1, max_length=30)


class ReleaseNotes(StrictModel):
    ref: str = Field(pattern=REF_PATTERN)
    title: str = _text(100, "e.g. 'Release notes – partner portal, August'; no numbers")
    summary_md: str = _text(800, "Two to four sentences; numbers only as {{f:<fact_key>}} tokens")
    sections: list[ReleaseSection] = Field(min_length=1, max_length=6)
    known_issues: list[str] = Field(default_factory=list, max_length=8)
    facts_cited: list[str] = Field(default_factory=list, max_length=20, description="Packet fact keys used as tokens")
    confidence: float = Field(ge=0, le=1)


class ReleaseNotesOutput(StrictModel):
    """The JSON file the agent writes for the release packet."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[ReleaseNotes]
