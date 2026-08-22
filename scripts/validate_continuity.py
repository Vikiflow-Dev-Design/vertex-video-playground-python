#!/usr/bin/env python3
"""Validate a project's continuity registry before generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.continuity import load_continuity_manifest, validate_continuity_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate video continuity metadata and reference images")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(ROOT / "video_projects"), help="Base directory for project slugs")
    args = parser.parse_args()

    candidate = Path(args.project).expanduser()
    project_dir = candidate if candidate.exists() else Path(args.base_dir).expanduser() / args.project
    project_dir = project_dir.resolve()
    if not project_dir.exists():
        print(f"Project directory does not exist: {project_dir}", file=sys.stderr)
        return 1

    try:
        manifest = load_continuity_manifest(project_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    errors = validate_continuity_manifest(manifest, project_dir=project_dir)
    print(f"Project: {project_dir}")
    print(f"Style: {manifest.get('style_id') or '(not configured)'}")
    print(f"Characters: {len(manifest.get('characters', {}))}")
    print(f"Locations: {len(manifest.get('locations', {}))}")
    print(f"Props: {len(manifest.get('props', {}))}")
    print(f"Shots: {len(manifest.get('shots', {}))}")
    print(f"Issues: {len(errors)}")
    for error in errors:
        print(f"- {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
