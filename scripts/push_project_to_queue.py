#!/usr/bin/env python3
"""Push visual prompts from a video project to the Next.js background queue.

This script parses prompts/visual_prompts.md and clips/clips_manifest.json,
creates corresponding QueueJob documents in MongoDB, and optionally polls for
their completion status.

IMPORTANT — AUDIO-FIRST ORDERING:
  Sentence audio MUST be generated before running this script.
  Run: python scripts/generate_project_audio.py --project <project> --section-index N
  This script reads real sentence audio durations to pre-compute accurate Veo
  job durations, ensuring every source video has enough footage for the recut
  trimmer. It will fail loudly if audio files are missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from mongo_store import connect, resolve_settings
from scripts.generate_and_cut_project_videos import parse_prompt_file, discover_prompt_files, resolve_project_dir, PromptBlock, PROMPT_HEADER_RE
from scripts.precompute_clip_durations import precompute_clip_durations

DEFAULT_BASE_DIR = PROJECT_ROOT / "video_projects"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Push video prompts to MongoDB QueueJobs")
    parser.add_argument("--project", required=True, help="Project slug or directory")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Base directory for video projects")
    parser.add_argument("--prompt-file", default=None, help="Optional single visual prompt file to process")
    parser.add_argument("--section-index", type=int, default=None, help="Process only this section (1-based)")
    parser.add_argument("--wait", action="store_true", help="Wait and poll for queue completion status")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Print pre-computed durations without enqueuing jobs")
    return parser


import re

def parse_prompt_file_for_section(path: Path, section_index: int) -> list[PromptBlock]:
    text = path.read_text(encoding="utf-8").strip()
    
    # Split by "## " at the start of a line to separate sections
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    
    section_text = None
    for part in parts:
        lines = part.splitlines()
        if not lines:
            continue
        header = lines[0]
        # Match header like "002-section-2_clips.txt" or similar
        # e.g., section-2 or section_2
        if f"section-{section_index}" in header.lower() or f"section_{section_index}" in header.lower():
            section_text = part
            break
            
    if not section_text:
        # Fallback: check if the prompt file filename itself matches the section
        if f"section_{section_index}" in path.name.lower() or f"section-{section_index}" in path.name.lower():
            section_text = text
        else:
            raise ValueError(f"Could not find prompts for Section {section_index} in {path.name}")
            
    matches = list(PROMPT_HEADER_RE.finditer(section_text))
    if not matches:
        raise ValueError(f"No numbered prompts found for Section {section_index} in {path.name}")

    blocks: list[PromptBlock] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        block_text = section_text[start:end].strip()
        blocks.append(
            PromptBlock(
                clip_number=int(match.group("number")),
                prompt_text=block_text,
                source_file=path,
            )
        )
    return blocks


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)

    if not project_dir.exists():
        print(f"Error: Project directory does not exist: {project_dir}", file=sys.stderr)
        return 1

    project_manifest_path = project_dir / "project.json"
    if not project_manifest_path.exists():
        print(f"Error: Project manifest project.json not found in {project_dir}", file=sys.stderr)
        return 1

    project_manifest = json.loads(project_manifest_path.read_text(encoding="utf-8"))

    # Resolve DB Settings
    mongo_uri = project_manifest.get("mongo_uri") or os.getenv("MONGODB_URI")
    mongo_db = project_manifest.get("mongo_db") or os.getenv("MONGODB_DB") or "video-studio"
    mongo_user_id = project_manifest.get("mongo_user_id") or os.getenv("MONGODB_USER_ID")
    mongo_project_id = project_manifest.get("mongo_project_id")

    settings = resolve_settings(mongo_uri, mongo_db, mongo_user_id)
    if not settings:
        print("Error: MongoDB connection settings are incomplete. Verify project.json or environment variables.", file=sys.stderr)
        return 1

    # Load prompts
    try:
        prompt_files = discover_prompt_files(project_dir, prompt_file=args.prompt_file)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    prompt_blocks = []
    for file_path in prompt_files:
        try:
            if args.section_index is not None:
                prompt_blocks.extend(parse_prompt_file_for_section(file_path, args.section_index))
            else:
                prompt_blocks.extend(parse_prompt_file(file_path))
        except Exception as exc:
            print(f"Error parsing prompt file {file_path.name}: {exc}", file=sys.stderr)
            return 1

    if not prompt_blocks:
        print("No prompts found to push.", file=sys.stderr)
        return 0

    # Pre-compute accurate Veo job durations from real sentence audio durations.
    # This requires sentence audio to already exist (run generate_project_audio.py first).
    if args.section_index is None:
        print("Error: --section-index is required for audio-first duration pre-computation.", file=sys.stderr)
        return 1

    print(f"Pre-computing Veo job durations from real sentence audio (Section {args.section_index})...")
    try:
        durations = precompute_clip_durations(project_dir, args.section_index, verbose=True)
    except FileNotFoundError as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        print("\nYou must generate sentence audio BEFORE pushing to the queue.", file=sys.stderr)
        print(f"  -> Run: python scripts/generate_project_audio.py --project {args.project} --section-index {args.section_index}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n========================================")
        print("DRY RUN SUMMARY (NO JOBS ENQUEUED)")
        print("========================================")
        for block in prompt_blocks:
            job_duration = durations.get(block.clip_number, 8)
            print(f"  [Clip {block.clip_number:03d}] Would enqueue: {job_duration}s | Prompt: {block.prompt_text[:70]}...")
        print("========================================")
        return 0

    print(f"Connecting to MongoDB...")
    client, db = connect(settings.uri, settings.db_name)

    # Resolve Veo Model and Rate
    veo_model = project_manifest.get("veo_model") or "veo-3.1-lite-generate-001"
    model_lower = veo_model.lower()
    rate = 20
    if "lite" in model_lower:
        rate = 3
    elif "fast" in model_lower:
        rate = 10

    generate_audio = bool(project_manifest.get("veo_generate_audio", False))

    veo_dir = project_dir / "veo"
    clip_to_job_id = {}
    already_generated_count = 0
    print(f"Checking existing video generation records...")

    def get_veo_duration(dur: float) -> int:
        if dur <= 4.0:
            return 4
        elif dur <= 6.0:
            return 6
        else:
            return 8

    for block in prompt_blocks:
        # 1. Check local VEO artifact cache
        artifact_path = veo_dir / f"{block.clip_number:03d}_generate_video.json"
        if artifact_path.exists():
            try:
                cached = json.loads(artifact_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("result", {}).get("status") == "done":
                    print(f"  [Clip {block.clip_number:03d}] Already generated locally. Skipping queue push.")
                    already_generated_count += 1
                    continue
            except Exception:
                pass

        # 2. Check MongoDB mediaassets collection
        # Check by exact prompt text to avoid cross-section collisions (where different sections share clip numbers)
        existing_asset = db["mediaassets"].find_one({
            "projectEnvId": mongo_project_id,
            "prompt": block.prompt_text
        })
        if existing_asset:
            print(f"  [Clip {block.clip_number:03d}] Already present in MongoDB mediaassets. Skipping queue push.")
            already_generated_count += 1
            continue

        # Use pre-computed Veo duration (ceiling step from real audio duration + overflow)
        job_duration = durations.get(block.clip_number, 8)  # fallback to 8s if somehow missing
        token_cost = job_duration * rate

        job_doc = {
            "userId": settings.user_id,
            "type": "video",
            "task": "video",
            "status": "queued",
            "prompt": block.prompt_text,
            "model": veo_model,
            "aspectRatio": "16:9",
            "durationSeconds": job_duration,
            "tokenCost": token_cost,
            "generateAudio": generate_audio,
            "projectEnvId": mongo_project_id,
            "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc),
            "refunded": False,
        }

        res = db["queuejobs"].insert_one(job_doc)
        clip_to_job_id[block.clip_number] = res.inserted_id
        print(f"  [Clip {block.clip_number:03d}] Pushed to queue with duration {job_duration}s. Job ID: {res.inserted_id}")

    results = {}
    
    if args.wait:
        print("\n========================================")
        print("POLLING QUEUE JOBS FOR COMPLETION...")
        print("========================================")
        active_jobs = dict(clip_to_job_id)
        missing_counts = {}

        while active_jobs:
            time.sleep(args.poll_interval)
            for clip_num, job_id in list(active_jobs.items()):
                # 1. Check if it is still in the queue
                job = db["queuejobs"].find_one({"_id": job_id})
                if job:
                    status = job.get("status")
                    if status == "failed":
                        error_msg = job.get("error") or "Unknown generation error"
                        print(f"[FAIL] Clip {clip_num:03d} failed: {error_msg}")
                        results[clip_num] = {
                            "status": "failed",
                            "error": error_msg,
                            "prompt": job.get("prompt"),
                        }
                        del active_jobs[clip_num]
                    elif status == "generating":
                        # We don't spam print, but we want status feedback
                        pass
                else:
                    # 2. Check if it was moved to mediaassets on success
                    asset = db["mediaassets"].find_one({"batchQueueItemId": str(job_id)})
                    if asset:
                        gcs_uri = asset.get("gcsUri") or "unknown GCS path"
                        print(f"[SUCCESS] Clip {clip_num:03d} succeeded: {gcs_uri}")
                        results[clip_num] = {
                            "status": "success",
                            "gcs_uri": gcs_uri,
                            "prompt": asset.get("prompt"),
                        }
                        del active_jobs[clip_num]
                    else:
                        # 3. Safeguard for missing documents (neither in queue nor assets)
                        missing_counts[job_id] = missing_counts.get(job_id, 0) + 1
                        if missing_counts[job_id] > 6:  # Missing for ~30 seconds
                            print(f"[WARN] Clip {clip_num:03d} disappeared from queue without completing asset creation.")
                            results[clip_num] = {
                                "status": "failed",
                                "error": "Job disappeared from queue without completing asset creation.",
                            }
                            del active_jobs[clip_num]

        # Write Summary JSON File
        summary = {
            "project_dir": str(project_dir),
            "project_slug": project_manifest.get("slug"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_pushed": len(clip_to_job_id),
            "succeeded_count": sum(1 for r in results.values() if r["status"] == "success"),
            "failed_count": sum(1 for r in results.values() if r["status"] == "failed"),
            "results": {str(k): v for k, v in sorted(results.items())}
        }
        summary_file = project_dir / "prompts" / "queue_generation_results.json"
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        # Report Summary Block
        print("\n========================================")
        print("QUEUE GENERATION SUMMARY")
        print("========================================")
        print(f"Total Clips Pushed: {summary['total_pushed']}")
        print(f"Succeeded:          {summary['succeeded_count']}")
        print(f"Failed:             {summary['failed_count']}")
        print("========================================")
        if summary['failed_count'] > 0:
            print("\nFailed Clips Details:")
            for clip_num, res in sorted(results.items()):
                if res["status"] == "failed":
                    print(f"  - Clip {clip_num:03d}: {res['error']}")
        print(f"Results log written to: prompts/queue_generation_results.json")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
