from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONTINUITY_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "style_id": "",
    "style_bible": "",
    "characters": {},
    "locations": {},
    "props": {},
    "shots": {},
}


def load_continuity_manifest(project_dir: Path) -> dict[str, Any]:
    """Load a project's continuity registry, or return an empty contract."""
    path = project_dir / "continuity" / "continuity.json"
    if not path.exists():
        return dict(DEFAULT_CONTINUITY_MANIFEST)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid continuity manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid continuity manifest: {path}")
    manifest = dict(DEFAULT_CONTINUITY_MANIFEST)
    manifest.update(payload)
    for key in ("characters", "locations", "props", "shots"):
        if not isinstance(manifest.get(key), dict):
            raise ValueError(f"Invalid continuity manifest: {path}: {key} must be an object")
    return manifest


def _reference_path(project_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_dir / path


def _description(registry: dict[str, Any], key: str) -> str:
    value = registry.get(key, {})
    if isinstance(value, dict):
        return str(value.get("description") or "").strip()
    return str(value).strip()


def _refs(registry: dict[str, Any], key: str) -> list[str]:
    value = registry.get(key, {})
    if not isinstance(value, dict):
        return []
    return [str(item) for item in value.get("reference_images", []) or []]


def _shot_for_clip(clip_number: int, manifest: dict[str, Any]) -> dict[str, Any]:
    shots = manifest.get("shots", {})
    shot = shots.get(f"{clip_number:03d}", shots.get(str(clip_number), {}))
    return shot if isinstance(shot, dict) else {}


def _clip_reference_images(clip_number: int, manifest: dict[str, Any]) -> list[str]:
    shot = _shot_for_clip(clip_number, manifest)
    references: list[str] = [str(value) for value in shot.get("reference_images", []) or []]
    for character_id in shot.get("characters", []) or []:
        references.extend(_refs(manifest.get("characters", {}), str(character_id)))
    location_id = shot.get("location")
    if location_id:
        references.extend(_refs(manifest.get("locations", {}), str(location_id)))
    for prop_id in shot.get("props", []) or []:
        references.extend(_refs(manifest.get("props", {}), str(prop_id)))
    return list(dict.fromkeys(references))


def validate_continuity_manifest(manifest: dict[str, Any], *, project_dir: Path) -> list[str]:
    """Return actionable continuity errors without modifying the manifest."""
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("continuity manifest schema_version must be 1")
    if not str(manifest.get("style_id") or "").strip():
        errors.append("continuity manifest style_id is required")

    characters = manifest.get("characters", {})
    locations = manifest.get("locations", {})
    props = manifest.get("props", {})
    shots = manifest.get("shots", {})

    for shot_id, shot in shots.items():
        if not isinstance(shot, dict):
            errors.append(f"shot {shot_id} must be an object")
            continue
        for character_id in shot.get("characters", []) or []:
            if character_id not in characters:
                errors.append(f"shot {shot_id} references unknown character '{character_id}'")
        location_id = shot.get("location")
        if location_id and location_id not in locations:
            errors.append(f"shot {shot_id} references unknown location '{location_id}'")
        for prop_id in shot.get("props", []) or []:
            if prop_id not in props:
                errors.append(f"shot {shot_id} references unknown prop '{prop_id}'")
        for reference in _clip_reference_images(int(shot_id), manifest):
            if not _reference_path(project_dir, reference).exists():
                errors.append(f"shot {shot_id} reference image does not exist: {reference}")
    return errors


def build_continuity_metadata(clip_number: int, manifest: dict[str, Any], *, project_dir: Path) -> dict[str, Any]:
    """Return machine-readable continuity metadata for queue/API handoff."""
    shot = _shot_for_clip(clip_number, manifest)
    return {
        "schemaVersion": manifest.get("schema_version", 1),
        "styleId": manifest.get("style_id") or None,
        "styleBible": manifest.get("style_bible") or None,
        "shotId": f"{clip_number:03d}",
        "characters": [str(value) for value in shot.get("characters", []) or []],
        "location": shot.get("location"),
        "props": [str(value) for value in shot.get("props", []) or []],
        "previousShot": shot.get("previous_shot"),
        "nextShot": shot.get("next_shot"),
        "camera": shot.get("camera"),
        "lightingState": shot.get("lighting_state"),
        "transition": shot.get("transition"),
        "referenceImages": [str(_reference_path(project_dir, value)) for value in _clip_reference_images(clip_number, manifest)],
    }


def build_continuity_context(clip_number: int, manifest: dict[str, Any], *, project_dir: Path) -> str:
    """Compile the stable style/world context for one clip into prompt text."""
    shot = _shot_for_clip(clip_number, manifest)
    lines = [
        "CONTINUITY CONTRACT:",
        f"STYLE ID: {manifest.get('style_id') or 'project-default'}",
    ]
    style_bible = manifest.get("style_bible")
    if style_bible:
        lines.append(f"STYLE BIBLE: {style_bible}")

    character_ids = [str(value) for value in shot.get("characters", []) or []]
    for character_id in character_ids:
        lines.append(f"CHARACTER {character_id}: {_description(manifest.get('characters', {}), character_id)}")
    location_id = shot.get("location")
    if location_id:
        lines.append(f"LOCATION {location_id}: {_description(manifest.get('locations', {}), str(location_id))}")
    for prop_id in shot.get("props", []) or []:
        lines.append(f"PROP {prop_id}: {_description(manifest.get('props', {}), str(prop_id))}")

    references = _clip_reference_images(clip_number, manifest)
    if references:
        lines.append("REFERENCE IMAGES:")
        lines.extend(f"- {_reference_path(project_dir, reference)}" for reference in references)

    for field, label in (("previous_shot", "previous shot"), ("next_shot", "next shot"), ("camera", "camera"), ("lighting_state", "lighting"), ("transition", "transition")):
        if shot.get(field):
            lines.append(f"{label}: {shot[field]}")
    return "\n".join(lines)
