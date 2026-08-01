#!/usr/bin/env python3
"""
cleanup_project.py

Utility script to clean up specific directories and files for a project
to reset the generation/caching state or free up disk space.

Usage:
  # To clean up teaser clips and selections:
  python scripts/cleanup_project.py --project <project_name> --teaser-clips

  # To clean up only the enqueued raw/cut videos in the veo/ folder that were NOT selected for the teaser:
  python scripts/cleanup_project.py --project <project_name> --non-selected-teaser-clips

  # To reset title cards videos:
  python scripts/cleanup_project.py --project <project_name> --title-cards

  # To delete audio and veo folders:
  python scripts/cleanup_project.py --project <project_name> --audio --veo
"""

import os
import sys
import shutil
import glob
import re
import json
import argparse
from pathlib import Path

# Base directory setup
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PROJECT_ROOT / "video_projects"

def get_selected_clips(selections_path: Path) -> dict[int, list[int]]:
    """Loads the teaser selections JSON file and returns a mapping of section -> list of clip numbers."""
    if not selections_path.exists():
        return {}
    try:
        data = json.loads(selections_path.read_text(encoding="utf-8"))
        # Convert keys to int section index and values to list of int clip numbers
        return {int(k): [int(v) for v in clips] for k, clips in data.items()}
    except Exception as e:
        print(f"[Warning] Failed to load teaser selections from {selections_path.name}: {e}")
        return {}

def delete_path(path: Path):
    """Safely deletes a file or directory tree."""
    if not path.exists():
        return
    try:
        if path.is_file():
            path.unlink()
            print(f"  Deleted file: {path.relative_to(PROJECT_ROOT)}")
        elif path.is_dir():
            shutil.rmtree(path)
            print(f"  Deleted directory: {path.relative_to(PROJECT_ROOT)}")
    except Exception as e:
        print(f"  [ERROR] Failed to delete {path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Clean up specific folders and files for a video project.")
    parser.add_argument("--project", required=True, help="Project directory name in video_projects/")
    parser.add_argument("--teaser-clips", action="store_true", help="Delete exports/teaser_clips/, teaser_highlights.mp4, and teaser_selections.json")
    parser.add_argument("--selected-teaser-clips", action="store_true", help="Delete original Veo source videos that WERE selected for the teaser highlights")
    parser.add_argument("--non-selected-teaser-clips", action="store_true", help="Delete original Veo source videos that were NOT selected for the teaser highlights (saves space)")
    parser.add_argument("--audio", action="store_true", help="Delete the project's audio/ directory")
    parser.add_argument("--exports", action="store_true", help="Delete the project's exports/ directory entirely")
    parser.add_argument("--title-cards", action="store_true", help="Delete only the .mp4 title card videos inside the title_cards/ folder")
    parser.add_argument("--veo", action="store_true", help="Delete the project's veo/ directory entirely")
    parser.add_argument("--all", action="store_true", help="Delete all of the above to completely reset the project state")
    args = parser.parse_args()

    project_dir = DEFAULT_PROJECTS_DIR / args.project
    if not project_dir.exists():
        print(f"[Error] Project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting cleanup operations for project: {args.project}\n")

    # 1. Clean exports directory
    if args.exports or args.all:
        print("Cleaning exports folder...")
        delete_path(project_dir / "exports")

    # 2. Clean teaser clips specifically
    elif args.teaser_clips:
        print("Cleaning teaser clips and metadata...")
        delete_path(project_dir / "exports" / "teaser_clips")
        delete_path(project_dir / "exports" / "teaser_highlights.mp4")
        delete_path(project_dir / "exports" / "teaser_selections.json")

    # 3. Clean selected original teaser clips from veo folder
    if (args.selected_teaser_clips or args.all) and not (args.veo or args.all):
        selections_path = project_dir / "exports" / "teaser_selections.json"
        selections = get_selected_clips(selections_path)
        if selections:
            print("Cleaning selected original teaser clips from veo/...")
            for sec_idx, clips in selections.items():
                sec_dir = project_dir / "veo" / f"section_{sec_idx}"
                for c_num in clips:
                    delete_path(sec_dir / "downloaded" / f"clip_{c_num:03d}_raw.mp4")
                    delete_path(sec_dir / "cut" / f"clip_{c_num:03d}.mp4")
        else:
            print("[Warning] No teaser selections cache found. Cannot perform selected-teaser-clips cleanup.")

    # 4. Clean non-selected original teaser clips from veo folder to free up space
    if args.non_selected_teaser_clips and not (args.veo or args.all):
        selections_path = project_dir / "exports" / "teaser_selections.json"
        selections = get_selected_clips(selections_path)
        if selections:
            print("Cleaning non-selected original teaser clips from veo/...")
            veo_dir = project_dir / "veo"
            for sec_path in veo_dir.glob("section_*"):
                match = re.search(r"section_(\d+)", sec_path.name)
                if not match:
                    continue
                sec_idx = int(match.group(1))
                selected_for_sec = selections.get(sec_idx, [])
                
                # Check downloaded raw files
                for f in (sec_path / "downloaded").glob("clip_*_raw.mp4"):
                    f_match = re.search(r"clip_(\d+)_raw", f.name)
                    if f_match and int(f_match.group(1)) not in selected_for_sec:
                        delete_path(f)
                
                # Check trimmed cut files
                for f in (sec_path / "cut").glob("clip_*.mp4"):
                    f_match = re.search(r"clip_(\d+)", f.name)
                    if f_match and int(f_match.group(1)) not in selected_for_sec:
                        delete_path(f)
        else:
            print("[Warning] No teaser selections cache found. Cannot perform non-selected-teaser-clips cleanup.")

    # 5. Clean audio folder
    if args.audio or args.all:
        print("Cleaning audio folder...")
        delete_path(project_dir / "audio")

    # 6. Clean title card videos (only .mp4 files inside title_cards/)
    if args.title_cards or args.all:
        print("Cleaning title card videos...")
        title_cards_dir = project_dir / "title_cards"
        if title_cards_dir.exists():
            for f in title_cards_dir.glob("*.mp4"):
                delete_path(f)

    # 7. Clean veo folder entirely
    if args.veo or args.all:
        print("Cleaning veo folder...")
        delete_path(project_dir / "veo")

    print("\nCleanup operation completed successfully.")

if __name__ == "__main__":
    main()
