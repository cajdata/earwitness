"""Type designators to engine classes."""

import json

import pytest

from earwitness.data import aircraft_types as at


def test_engine_class_from_description():
    assert at.engine_class("L1P") == "piston"
    assert at.engine_class("L2T") == "turboprop"
    assert at.engine_class("L2J") == "jet"
    assert at.engine_class("H1P") == "helicopter"
    assert at.engine_class("H2T") == "helicopter"
    assert at.engine_class("A1P") == "piston"  # amphibian
    assert at.engine_class("G1P") == "other"  # gyrocopter
    assert at.engine_class("L1E") == "other"
    assert at.engine_class("") == "unknown"
    assert at.engine_class(None) == "unknown"


def test_type_info_parts():
    info = at.TypeInfo("C208", "L1T", "L", "CESSNA 208 Caravan")
    assert info.vehicle == "landplane"
    assert info.engines == 1
    assert info.engine_type == "turboprop"
    assert at.TypeInfo("R44", "H1P").vehicle == "helicopter"
    assert at.TypeInfo("X", "").engines is None


def test_tables_merge_with_icao_export_winning(tmp_path):
    icao = [
        {
            "Designator": "C172",
            "Description": "L1P",
            "WTC": "L",
            "ManufacturerCode": "CESSNA",
            "ModelFullName": "172",
        },
        {
            "Designator": "P28A",
            "Description": "L1P",
            "WTC": "L",
            "ManufacturerCode": "PIPER",
            "ModelFullName": "PA-28",
        },
        {
            "Designator": "C172",
            "Description": "L1P",
            "WTC": "L",
            "ManufacturerCode": "REIMS",
            "ModelFullName": "F172",
        },
        {"Designator": "", "Description": "L1P"},
    ]
    old = {
        "C172": {"desc": "L1P", "wtc": "L"},
        "B738": {"desc": "L2J", "wtc": "M"},
        "ZZZZ": {"desc": ""},
    }
    base = tmp_path / "aircraft_types"
    base.mkdir()
    (base / "icao_doc8643.json").write_text(json.dumps(icao))
    (base / "icao_aircraft_types.json").write_text(json.dumps(old))
    table = at.TypeTable.load(tmp_path, allow_download=False)
    assert len(table) == 3
    assert table.get("c172").name == "CESSNA 172"
    assert table.engine_class("P28A") == "piston"
    assert table.engine_class("B738") == "jet"
    assert table.engine_class("ZZZZ") == "unknown"
    assert table.engine_class("") == "unknown"
    assert at.classify("P28A", table) == "piston"
    assert at.classify("P28A", None) == "unknown"
    assert "ICAO DOC 8643" in table.source
    assert "dump1090" in table.source


def test_load_without_any_table_fails(tmp_path):
    with pytest.raises(RuntimeError, match="no aircraft type table"):
        at.TypeTable.load(tmp_path, allow_download=False)
