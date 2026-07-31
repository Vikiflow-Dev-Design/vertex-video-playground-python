#!/usr/bin/env python3
"""Bootstrap a new style-specific video project from a script input.

This is the entrypoint for the chat flow:
1. project name
2. project style
3. script / doc / file

The workflow creates the project workspace, ingests the script, splits it into
clips, and generates visual prompts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.create_video_project import slugify


def build_workflow_commands(
    *,
    project: str,
    style: str,
    project_dir: Path,
    title: str,
    description: str,
    source_text: str | None,
    source_file: str | None,
    doc_url: str | None,
    base_dir: Path,
    gcp_project_id: str | None,
    gcp_location: str,
    gcs_bucket: str | None,
    gcs_prefix: str,
    gemini_model: str,
    story_model: str,
    veo_model: str,
    veo_resolution: str = "720p",
    veo_generate_audio: bool = False,
    mongo_uri: str | None = None,
    mongo_db: str | None = None,
    user_id: str | None = None,
    skip_story_outline: bool = False,
    push_to_queue: bool = False,
    wait: bool = False,
) -> list[list[str]]:
    python = sys.executable
    create_cmd = [
        python,
        "scripts/create_video_project.py",
        project,
        "--title",
        title,
        "--style",
        style,
        "--base-dir",
        str(base_dir),
        "--gcp-location",
        gcp_location,
        "--gcs-prefix",
        gcs_prefix,
        "--gemini-model",
        gemini_model,
        "--story-model",
        story_model,
        "--veo-model",
        veo_model,
        "--veo-resolution",
        veo_resolution,
        "--veo-generate-audio" if veo_generate_audio else "--no-veo-generate-audio",
    ]
    if description:
        create_cmd.extend(["--description", description])
    if gcp_project_id:
        create_cmd.extend(["--gcp-project-id", gcp_project_id])
    if gcs_bucket:
        create_cmd.extend(["--gcs-bucket", gcs_bucket])
    if mongo_uri:
        create_cmd.extend(["--mongo-uri", mongo_uri])
    if mongo_db:
        create_cmd.extend(["--mongo-db", mongo_db])
    if user_id:
        create_cmd.extend(["--user-id", user_id])

    if doc_url:
        ingest_cmd = [
            python,
            "scripts/ingest_google_doc.py",
            "--project",
            project,
            "--base-dir",
            str(base_dir),
            "--doc-url",
            doc_url,
        ]
    else:
        ingest_cmd = [
            python,
            "scripts/ingest_sections.py",
            "--project",
            project,
            "--base-dir",
            str(base_dir),
        ]
    if not doc_url:
        if source_file:
            ingest_cmd.extend(["--file", source_file])
        elif source_text is not None:
            ingest_cmd.extend(["--text", source_text])
        else:
            raise ValueError("Provide one of source_text, source_file, or doc_url")
        if skip_story_outline:
            ingest_cmd.append("--skip-story-outline")
        if story_model:
            ingest_cmd.extend(["--story-model", story_model])

    clip_cmd = [
        python,
        "scripts/generate_section_clips.py",
        "--project",
        project,
        "--base-dir",
        str(base_dir),
    ]

    prompt_cmd = [
        python,
        "scripts/generate_visual_prompts.py",
        "--project",
        project,
        "--base-dir",
        str(base_dir),
    ]

    commands = [create_cmd, ingest_cmd, clip_cmd, prompt_cmd]

    if push_to_queue:
        push_cmd = [
            python,
            "scripts/push_project_to_queue.py",
            "--project",
            project,
            "--base-dir",
            str(base_dir),
        ]
        if wait:
            push_cmd.append("--wait")
        commands.append(push_cmd)

    return commands


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    # Use None for stdin/stdout inherits when wait is active to keep streaming polling messages to stdout
    is_push_with_wait = "push_project_to_queue.py" in args[1] and "--wait" in args
    if is_push_with_wait:
        # Run directly, inheriting stdout/stderr so the polling loop outputs in real-time
        result = subprocess.run(args, check=True, text=True)
        # Return a mocked CompletedProcess that won't break json.loads
        import sys
        return subprocess.CompletedProcess(args=args, returncode=0, stdout='{"status": "ok"}', stderr='')
    
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result


def run_workflow(commands: list[list[str]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for command in commands:
        completed = run_command(command)
        stdout = completed.stdout.strip()
        try:
            payload = json.loads(stdout) if stdout else {}
        except Exception:
            payload = {"status": "ok", "raw_output": stdout}
        results.append({"command": command, "result": payload})
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a new style-specific video project and start the workflow")
    parser.add_argument("--project", required=True, help="Project slug")
    parser.add_argument("--style", required=True, help="Project style, e.g. current, paper, 3d, 2d")
    parser.add_argument("--title", default=None, help="Human-friendly project title")
    parser.add_argument("--description", default="", help="Optional project description")
    parser.add_argument("--base-dir", default=str(ROOT / "video_projects"), help="Base directory for video projects")
    parser.add_argument("--file", default=None, help="Path to a raw sections file")
    parser.add_argument("--doc-url", default=None, help="Google Doc URL to import")
    parser.add_argument("--text", default=None, help="Raw pasted sections text")
    parser.add_argument("--gcp-project-id", default=None, help="Google Cloud project id")
    parser.add_argument("--gcp-location", default="global", help="Vertex AI location")
    parser.add_argument("--gcs-bucket", default=None, help="Optional output bucket name")
    parser.add_argument("--gcs-prefix", default="hermes", help="Default GCS prefix")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash", help="Default Gemini model")
    parser.add_argument("--story-model", default="gemini-2.5-flash", help="Default story-outline model")
    parser.add_argument("--veo-model", default="veo-3.1-lite-generate-001", help="Default Veo model")
    parser.add_argument("--veo-resolution", default="720p", help="Default Veo output resolution")
    parser.add_argument("--veo-generate-audio", action=argparse.BooleanOptionalAction, default=False, help="Generate audio for Veo outputs")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB connection string")
    parser.add_argument("--mongo-db", default=None, help="MongoDB database name")
    parser.add_argument("--user-id", default=None, help="MongoDB user ObjectId string")
    parser.add_argument("--skip-story-outline", action="store_true", help="Skip Gemini story-outline generation")
    parser.add_argument("--push-to-queue", action="store_true", help="Push visual prompts to MongoDB QueueJobs")
    parser.add_argument("--wait", action="store_true", help="Wait and poll for queue completion status")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project = slugify(args.project)
    title = args.title or project.replace("-", " ").title()
    project_dir = base_dir / project

    commands = build_workflow_commands(
        project=project,
        style=args.style,
        project_dir=project_dir,
        title=title,
        description=args.description,
        source_text=args.text,
        source_file=args.file,
        doc_url=args.doc_url,
        base_dir=base_dir,
        gcp_project_id=args.gcp_project_id,
        gcp_location=args.gcp_location,
        gcs_bucket=args.gcs_bucket,
        gcs_prefix=args.gcs_prefix,
        gemini_model=args.gemini_model,
        story_model=args.story_model,
        veo_model=args.veo_model,
        veo_resolution=args.veo_resolution,
        veo_generate_audio=args.veo_generate_audio,
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
        user_id=args.user_id,
        skip_story_outline=args.skip_story_outline,
        push_to_queue=args.push_to_queue,
        wait=args.wait,
    )
    results = run_workflow(commands)
    print(json.dumps({"status": "ok", "project_dir": str(project_dir), "commands": commands, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

