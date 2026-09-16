"""SAP module (module #2): SAP L3 support on top of the ops tickets, ChaRM changes with their transports, IDoc health.

SAP tickets stay ops tickets. The SAP scope (config/sap/scope.yaml) selects them on read by assignment group, category
or custom field and gives their SAP area and landscape; see scope.py. ChaRM change documents and transport imports are
SAP tables read through config/sap/charm.yaml (charm.py); IDocs are read through config/sap/idoc.yaml (idoc.py).
"""

from __future__ import annotations

from sed.modules.contract import ApiMount, Module, NavItem, ReportDef, SynthDef

MODULE = Module(
    key="sap",
    title="SAP application support",
    description=(
        "SAP L3 support by area and landscape, ChaRM changes and transports, IDoc health, SAP risks and "
        "the weekly review"
    ),
    depends_on=("ops",),
    api=ApiMount("sed.modules.sap.api:router"),
    nav=(
        NavItem("sap.overview", "SAP", "/sap", 60, "building-factory"),
        NavItem("sap.tickets", "SAP L3 tickets", "/sap/tickets", 61, "ticket"),
        NavItem("sap.changes", "SAP changes", "/sap/changes", 62, "git-pull-request"),
        NavItem("sap.idocs", "SAP IDocs", "/sap/idocs", 63, "arrows-exchange"),
    ),
    reports=(
        ReportDef(
            "sap-weekly",
            "Weekly SAP Operations Review",
            "sap/reports/sap-weekly.yaml",
            "sed.modules.sap.reports.weekly:build",
            ("week",),
            formats=("xlsx", "md", "pptx"),
            markdown="sed.modules.sap.reports.markdown:render",
        ),
    ),
    mappings_dir="sap/mappings",
    ingest_targets="sed.modules.sap.ingest:TARGETS",
    synth=SynthDef("sed.modules.sap.synth:generate"),
    metric_definitions="sed.modules.sap.definitions:DEFINITIONS",
    finding_kinds=("sap_backlog_risk", "sap_change_risk", "sap_idoc_risk"),
    rule_findings="sed.modules.sap.rules:compute",
    doctor_checks="sed.modules.sap.doctor:checks",
    config_files=(
        "sap/scope.yaml",
        "sap/charm.yaml",
        "sap/idoc.yaml",
        "sap/risk_rules.yaml",
        "sap/reports/sap-weekly.yaml",
    ),
    tables=("sap_change", "sap_change_status", "sap_transport_import", "sap_idoc", "sap_idoc_status"),
    data_subdirs=("config/sap", "config/sap/mappings", "config/sap/reports"),
    owned_paths=(
        "src/sed/modules/sap/**",
        "config/sap/**",
        "web/src/modules/sap/**",
        "tests/modules/sap/**",
    ),
)
