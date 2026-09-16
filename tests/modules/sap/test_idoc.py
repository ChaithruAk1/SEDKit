"""IDoc configuration (config/sap/idoc.yaml): validation, status groups, message-type areas, text normalisation and
layered overrides."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sed.errors import ValidationFailed
from sed.modules.sap.idoc import UNKNOWN_GROUP, IdocConfig, load_idoc, normalise_code, normalise_text
from sed.modules.sap.scope import UNASSIGNED, load_scope
from tests.modules.sap.conftest import write_sap_config


def test_repo_defaults():
    idoc = load_idoc(None, load_scope(None))
    assert idoc.group("51") == "error" and idoc.group("53") == "ok" and idoc.group("3") == "ok"
    assert idoc.group("68") == "closed" and idoc.group("64") == "in_process" and idoc.group("99") == UNKNOWN_GROUP
    assert idoc.codes("error") == ["02", "04", "05", "26", "29", "51", "56", "60", "65"]
    assert idoc.area("invoic") == "fi_co" and idoc.area("ZCUSTOM") == UNASSIGNED
    t = idoc.config.thresholds
    assert (t.reprocess_grace_hours, t.aged_error_hours, t.spike_window_hours, t.history_days) == (24, 48, 48, 120)


@pytest.mark.parametrize(("raw", "code"), [("3", "03"), (" 51 ", "51"), (2, "02"), ("", ""), (None, ""), ("E1", "E1")])
def test_normalise_code(raw, code):
    assert normalise_code(raw) == code


def test_normalise_text_strips_document_numbers_only():
    assert normalise_text("Sold-to party 100023 not maintained for sales area S100/01/00") == (
        "Sold-to party # not maintained for sales area S100/01/00"
    )
    assert normalise_text("Syntax  error (segment E1EDP01 missing) for billing document 912345") == (
        "Syntax error (segment E1EDP01 missing) for billing document #"
    )
    assert normalise_text(None) == ""


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"statuses": [{"code": "51", "group": "error"}, {"code": "51", "group": "ok"}]}, "'51' is listed twice"),
        ({"statuses": [{"code": "3", "group": "ok"}, {"code": "03", "group": "ok"}]}, "'03' is listed twice"),
        ({"statuses": [{"code": "511", "group": "error"}]}, "String should match pattern"),
        ({"statuses": [{"code": "51", "group": "failed"}]}, "Input should be"),
        ({"message_types": [{"type": "ORDERS", "area": "sd"}, {"type": "ORDERS", "area": "mm"}]}, "listed twice"),
        ({"message_types": [{"type": "orders", "area": "sd"}]}, "String should match pattern"),
        ({"thresholds": {"history_days": 3}}, "greater than or equal to 7"),
        ({"thresholds": {"spike_min_lift": 0}}, "greater than or equal to 1"),
    ],
)  # fmt: skip
def test_invalid_config_is_rejected(data, message):
    with pytest.raises(ValidationError) as info:
        IdocConfig.model_validate(data)
    assert message in str(info.value)


def test_message_types_must_name_scope_areas(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(paths, "idoc.yaml", {"message_types": [{"type": "HRMD_A", "area": "hcm"}]})
    with pytest.raises(ValidationFailed) as info:
        load_idoc(paths, load_scope(paths))
    assert "unknown SAP area 'hcm' for 'HRMD_A'" in json.dumps(info.value.details)


def test_local_lists_replace_or_extend_the_defaults(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(
        paths,
        "idoc.yaml",
        {
            "statuses+": [{"code": "69", "group": "closed"}],
            "message_types": [{"type": "ZINVOIC", "area": "fi_co"}],
            "thresholds": {"reprocess_grace_hours": 8},
        },
    )
    idoc = load_idoc(paths, load_scope(paths))
    assert idoc.group("69") == "closed" and idoc.group("51") == "error"
    assert idoc.area("ZINVOIC") == "fi_co" and idoc.area("INVOIC") == UNASSIGNED
    assert idoc.config.thresholds.reprocess_grace_hours == 8 and idoc.config.thresholds.aged_error_hours == 48
