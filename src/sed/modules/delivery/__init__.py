"""Delivery module (module #3, M7): delivery management of new business applications.

Projects come from the project register, milestones from plan exports (every plan version kept, so slips stay visible),
RAID items from the RAID log; Jira issues and Confluence pages are the ops `work_item` and `doc_page` rows linked by the
register's Jira project keys and Confluence space. Health, slips, scope growth and forecast finish are computed on read
(queries/portfolio.py) and system-detected delivery risks come from rules.py. The AI drafting skills (ai/: user stories,
ADRs, test plans, release notes) put drafts in the review queue; approved drafts export as files (ai/export.py).
"""

from __future__ import annotations

from sed.modules.contract import ApiMount, CliMount, Module, NavItem, ReportDef, SkillDef, SynthDef

MODULE = Module(
    key="delivery",
    title="Delivery management",
    description=(
        "Delivery of new business applications: project register, plan milestones and slips, RAID log, Jira progress "
        "and forecast, requirements and ADR pages, delivery risks"
    ),
    depends_on=("ops",),
    cli=(CliMount("delivery", "sed.modules.delivery.cli:app"),),
    api=ApiMount("sed.modules.delivery.api:router"),
    nav=(NavItem("delivery.portfolio", "Delivery", "/delivery", 70, "chart-bar"),),
    reports=(
        ReportDef(
            "delivery-status",
            "Monthly Delivery Status",
            "delivery/reports/delivery-status.yaml",
            "sed.modules.delivery.reports.status:build",
            ("month",),
            formats=("xlsx", "md", "pptx"),
            markdown="sed.modules.delivery.reports.markdown:render",
        ),
    ),
    skills=(
        SkillDef("sed-draft-stories", "sed.modules.delivery.ai.stories:StoriesHandler"),
        SkillDef("sed-draft-adr", "sed.modules.delivery.ai.adr:AdrHandler"),
        SkillDef("sed-draft-test-plan", "sed.modules.delivery.ai.test_plan:TestPlanHandler"),
        SkillDef("sed-draft-release-notes", "sed.modules.delivery.ai.release_notes:ReleaseNotesHandler"),
    ),
    mappings_dir="delivery/mappings",
    ingest_targets="sed.modules.delivery.ingest:TARGETS",
    synth=SynthDef("sed.modules.delivery.synth:generate"),
    metric_definitions="sed.modules.delivery.definitions:DEFINITIONS",
    finding_kinds=(
        "delivery_risk",
        "delivery_stories",
        "delivery_adr",
        "delivery_test_plan",
        "delivery_release_notes",
    ),
    rule_findings="sed.modules.delivery.rules:compute",
    config_files=("delivery/risk_rules.yaml", "delivery/reports/delivery-status.yaml"),
    tables=("delivery_project", "delivery_milestone", "delivery_raid"),
    data_subdirs=("config/delivery", "config/delivery/mappings", "config/delivery/reports"),
    owned_paths=(
        "src/sed/modules/delivery/**",
        "config/delivery/**",
        "web/src/modules/delivery/**",
        "tests/modules/delivery/**",
        ".claude/skills/sed-draft-stories/**",
        ".claude/skills/sed-draft-adr/**",
        ".claude/skills/sed-draft-test-plan/**",
        ".claude/skills/sed-draft-release-notes/**",
    ),
)
