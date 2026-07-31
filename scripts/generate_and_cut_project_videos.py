#!/usr/bin/env python3
"""Run the full generation workflow for a video project.

This wires together the existing generation stage and the trimming stage:
1. Read the project's visual-prompt markdown files in order.
2. Generate every clip video with Vertex / Veo in 8-wide batches by default.
3. Continue automatically through every section prompt file until the whole
   project is complete.
4. After all generation is complete, trim the resulting videos to the exact clip
   durations from `clips/clips_manifest.json`.
5. Zip the trimmed outputs.

The workflow is intentionally batch-oriented so trimming happens only after the
full project render is finished.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from generate_video import VideoJob, run_video_job
from scripts.cut_generated_videos import cut_project_videos

PROMPT_HEADER_RE = re.compile(r"^(?P<number>\d{3}):\s+(.+)$", re.MULTILINE)
# Prompt files are numbered sequentially across the whole Rome project.
# The first file starts at 001; later section files continue from the prior
# file's last clip number instead of restarting at 001.


@dataclass(frozen=True)
class PromptBlock:
    clip_number: int
    prompt_text: str
    source_file: Path


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    candidate = Path(project).expanduser()
    if candidate.exists():
        return candidate.resolve()
    return (base_dir / project).resolve()


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def load_project_manifest(project_dir: Path) -> dict[str, object]:
    path = project_dir / "project.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_clip_manifest(project_dir: Path) -> dict[str, object]:
    path = project_dir / "clips" / "clips_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_prompt_file(path: Path) -> list[PromptBlock]:
    text = path.read_text(encoding="utf-8").strip()
    matches = list(PROMPT_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError(f"No numbered prompts found in {path}")

    blocks: list[PromptBlock] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        blocks.append(
            PromptBlock(
                clip_number=int(match.group("number")),
                prompt_text=block,
                source_file=path,
            )
        )
    return blocks


def batch_prompt_blocks(blocks: list[PromptBlock], batch_size: int) -> list[list[PromptBlock]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    return [blocks[index : index + batch_size] for index in range(0, len(blocks), batch_size)]


def run_generation_batch(
    *,
    batch: list[PromptBlock],
    generate_fn: Callable[[VideoJob], dict[str, object]],
    manifest: dict[str, object],
    project_dir: Path,
    project_slug: str,
    clip_durations: dict[int, float],
) -> list[dict[str, object]]:
    if not batch:
        return []

    veo_dir = project_dir / "veo"
    veo_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object] | None] = [None] * len(batch)
    with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as executor:
        future_to_index = {}
        for index, block in enumerate(batch):
            artifact_path = veo_dir / f"{block.clip_number:03d}_generate_video.json"
            if artifact_path.exists():
                try:
                    cached = json.loads(artifact_path.read_text(encoding="utf-8"))
                    if isinstance(cached, dict) and cached.get("result", {}).get("status") == "done":
                        print(f"Clip {block.clip_number:03d} already completed. Reusing cached render.")
                        results[index] = {
                            "clip_number": block.clip_number,
                            "prompt_file": str(block.source_file),
                            "artifact_path": str(artifact_path),
                            "result": cached.get("result"),
                        }
                        continue
                except Exception:
                    pass

            duration_val = clip_durations.get(block.clip_number)
            job = build_video_job(
                manifest=manifest,
                project_dir=project_dir,
                project_slug=project_slug,
                prompt=block,
                duration_seconds=duration_val,
            )
            future_to_index[executor.submit(generate_fn, job)] = (index, block, job)

        for future in as_completed(future_to_index):
            index, block, job = future_to_index[future]
            try:
                result = future.result()
                artifact_path = veo_dir / f"{block.clip_number:03d}_generate_video.json"
                artifact_payload = {
                    "clip_number": block.clip_number,
                    "prompt_file": str(block.source_file),
                    "job": asdict(job),
                    "result": result,
                }
                artifact_path.write_text(json.dumps(artifact_payload, indent=2) + "\n", encoding="utf-8")
                results[index] = {
                    "clip_number": block.clip_number,
                    "prompt_file": str(block.source_file),
                    "artifact_path": str(artifact_path),
                    "result": result,
                }
            except Exception as exc:  # pragma: no cover - exercised through batch runner integration
                results[index] = {
                    "clip_number": block.clip_number,
                    "prompt_file": str(block.source_file),
                    "error": f"{exc.__class__.__name__}: {exc}",
                }

    return [item for item in results if item is not None]


def discover_prompt_files(project_dir: Path, prompt_file: str | None = None) -> list[Path]:
    if prompt_file:
        candidate = Path(prompt_file).expanduser()
        if not candidate.is_absolute():
            candidate = project_dir / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"Prompt file not found: {candidate}")
        return [candidate.resolve()]

    prompt_dir = project_dir / "prompts"
    consolidated = prompt_dir / "visual_prompts.md"
    if consolidated.exists():
        return [consolidated]
    files = sorted(prompt_dir.glob("*_visual_prompts.md"))
    if not files:
        raise FileNotFoundError(f"No visual prompt files found in {prompt_dir}")
    return files


def build_output_gcs_uri(manifest: dict[str, object], project_slug: str, clip_number: int) -> str | None:
    bucket = manifest.get("gcs_bucket")
    if not bucket:
        return None
    prefix = str(manifest.get("gcs_prefix") or "hermes").strip("/")
    return f"gs://{bucket}/{prefix}/{project_slug}/{clip_number:03d}/"


def build_video_job(
    *,
    manifest: dict[str, object],
    project_dir: Path,
    project_slug: str,
    prompt: PromptBlock,
    duration_seconds: float | None = None,
) -> VideoJob:
    project_id = manifest.get("gcp_project_id")
    location = manifest.get("gcp_location") or "global"
    model = str(manifest.get("veo_model") or "veo-3.1-lite-generate-001")
    resolution = str(manifest.get("veo_resolution") or "720p")
    generate_audio = bool(manifest.get("veo_generate_audio", False))
    mongo_db = manifest.get("mongo_db")
    mongo_user_id = manifest.get("mongo_user_id")
    mongo_project_id = manifest.get("mongo_project_id")
    mongo_uri = None
    output_gcs_uri = build_output_gcs_uri(manifest, project_slug, prompt.clip_number)

    if not project_id:
        raise ValueError("Project manifest is missing gcp_project_id")

    return VideoJob(
        project_id=str(project_id),
        location=str(location),
        model=model,
        prompt=prompt.prompt_text,
        aspect_ratio="16:9",
        duration_seconds=duration_seconds,
        output_gcs_uri=output_gcs_uri,
        poll_seconds=15,
        project_dir=str(project_dir),
        mongo_uri=mongo_uri,
        mongo_db=str(mongo_db) if mongo_db is not None else None,
        user_id=str(mongo_user_id) if mongo_user_id is not None else None,
        project_env_id=str(mongo_project_id) if mongo_project_id is not None else None,
        resolution=resolution,
        generate_audio=generate_audio,
        watermarked=False,
    )


def run_generation_workflow(
    project_dir: Path,
    *,
    prompt_file: str | None = None,
    generate_fn: Callable[[VideoJob], dict[str, object]] = run_video_job,
    cut_fn: Callable[[Path, int | None], dict[str, object]] = cut_project_videos,
    section_index: int | None = None,
    batch_size: int = 8,
) -> dict[str, object]:
    manifest = load_project_manifest(project_dir)
    if "veo_resolution" not in manifest:
        manifest["veo_resolution"] = "720p"
    if "veo_generate_audio" not in manifest:
        manifest["veo_generate_audio"] = False
    prompt_files = discover_prompt_files(project_dir, prompt_file=prompt_file)
    project_slug = str(manifest.get("slug") or project_dir.name)

    clip_manifest = load_clip_manifest(project_dir)
    clip_durations = {}
    for section in clip_manifest.get("sections", []) or []:
        for clip in section.get("clips", []) or []:
            clip_num = clip.get("global_clip_number") or clip.get("clip_number")
            dur = clip.get("duration_seconds")
            if clip_num is not None and dur is not None:
                clip_durations[int(clip_num)] = float(dur)

    generated: list[dict[str, object]] = []
    for file_path in prompt_files:
        blocks = parse_prompt_file(file_path)
        for batch in batch_prompt_blocks(blocks, batch_size):
            generated.extend(
                run_generation_batch(
                    batch=batch,
                    generate_fn=generate_fn,
                    manifest=manifest,
                    project_dir=project_dir,
                    project_slug=project_slug,
                    clip_durations=clip_durations,
                )
            )

    cut_summaries: list[dict[str, object]] = []
    clip_manifest = load_clip_manifest(project_dir)
    clip_sections = clip_manifest.get("sections")
    if section_index is None:
        sections = manifest.get("sections", [])
        for section in sections:
            if not isinstance(section, dict):
                continue
            s_idx = int(section.get("section_index", 1))
            cut_summaries.append(cut_fn(project_dir, section_index=s_idx))
    else:
        cut_summaries.append(cut_fn(project_dir, section_index=section_index))

    # Auto-stitch trimmed clips into final master movie
    try:
        from scripts.stitch_project_master import main as run_stitch
        sys.argv = ["stitch_project_master.py", "--project", project_dir.name]
        run_stitch()
    except Exception as e:
        print(f"[Warning] Failed to auto-stitch master movie: {e}")

    return {
        "project_dir": str(project_dir),
        "prompt_files": [str(path) for path in prompt_files],
        "batch_size": batch_size,
        "generated": generated,
        "cut_summary": cut_summaries[0] if len(cut_summaries) == 1 else cut_summaries,
        "master_movie": str(project_dir / "exports" / f"{project_dir.name}_master.mp4"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate all videos for a project, then trim and zip them")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parents[1] / "video_projects"), help="Base directory for video projects")
    parser.add_argument("--prompt-file", default=None, help="Optional single visual prompt file to process")
    parser.add_argument("--section-index", type=int, default=None, help="Optional section index to pass to the trim step")
    parser.add_argument("--batch-size", type=int, default=8, help="Number of prompts to generate concurrently before moving to the next batch")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)
    if not project_dir.exists():
        raise SystemExit(f"Project directory does not exist: {project_dir}")

    summary = run_generation_workflow(
        project_dir,
        prompt_file=args.prompt_file,
        section_index=args.section_index,
        batch_size=args.batch_size,
    )
    print(json.dumps({"status": "ok", **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
