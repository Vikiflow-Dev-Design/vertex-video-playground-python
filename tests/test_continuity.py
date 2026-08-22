from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.continuity import (
    build_continuity_context,
    build_continuity_metadata,
    load_continuity_manifest,
    validate_continuity_manifest,
)


def test_valid_continuity_manifest_builds_stable_context(tmp_path: Path) -> None:
    reference = tmp_path / "references" / "commander-front.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference")
    manifest = {
        "schema_version": 1,
        "style_id": "fern-animation-v1",
        "style_bible": "instructions/styles/fern-animation/style_bible.yaml",
        "characters": {
            "commander_01": {
                "description": "adult commander, charcoal coat, rust-red sash",
                "reference_images": [str(reference)],
            }
        },
        "locations": {
            "archive_room_01": {
                "description": "narrow stone archive room, warm window light from camera-left"
            }
        },
        "props": {},
        "shots": {
            "012": {
                "characters": ["commander_01"],
                "location": "archive_room_01",
                "previous_shot": "011",
                "camera": "slow push-in",
            }
        },
    }

    errors = validate_continuity_manifest(manifest, project_dir=tmp_path)
    assert errors == []

    context = build_continuity_context(12, manifest, project_dir=tmp_path)
    assert "STYLE ID: fern-animation-v1" in context
    assert "CHARACTER commander_01" in context
    assert "LOCATION archive_room_01" in context
    assert "previous shot: 011" in context
    assert str(reference) in context

    metadata = build_continuity_metadata(12, manifest, project_dir=tmp_path)
    assert metadata["styleId"] == "fern-animation-v1"
    assert metadata["shotId"] == "012"
    assert metadata["characters"] == ["commander_01"]
    assert metadata["location"] == "archive_room_01"


def test_invalid_manifest_reports_unknown_ids_and_missing_references(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "style_id": "fern-animation-v1",
        "characters": {},
        "locations": {},
        "props": {},
        "shots": {
            "001": {
                "characters": ["missing_character"],
                "location": "missing_location",
                "reference_images": ["references/missing.png"],
            }
        },
    }

    errors = validate_continuity_manifest(manifest, project_dir=tmp_path)
    assert "shot 001 references unknown character 'missing_character'" in errors
    assert "shot 001 references unknown location 'missing_location'" in errors
    assert "shot 001 reference image does not exist: references/missing.png" in errors


def test_load_missing_manifest_returns_empty_contract(tmp_path: Path) -> None:
    manifest = load_continuity_manifest(tmp_path)
    assert manifest["schema_version"] == 1
    assert manifest["characters"] == {}
    assert manifest["shots"] == {}


def test_load_invalid_json_fails_clearly(tmp_path: Path) -> None:
    continuity_dir = tmp_path / "continuity"
    continuity_dir.mkdir()
    (continuity_dir / "continuity.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid continuity manifest"):
        load_continuity_manifest(tmp_path)
