#!/usr/bin/env python3
"""Create a reusable video-project workspace inside vertex-video-playground.

This makes a per-project folder with source, story, clips, prompts, veo, and
logs subdirectories, plus copied prompt templates, a JSON manifest, and an
optional MongoDB project record.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mongo_store import build_project_doc, connect, resolve_settings, upsert_project
from scripts.style_profiles import DEFAULT_STYLE, resolve_style_template_path

load_dotenv()

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "video_projects"
DEFAULT_STORY_PROMPT = Path(__file__).resolve().parents[1] / "templates" / "story_sectioning_master_prompt.md"
DEFAULT_MONGO_USER_ID = os.getenv("MONGODB_USER_ID", "6a4264656320d6dd8421deba")
WORKSPACE_DEFAULTS_PATH = DEFAULT_BASE_DIR / "_workspace_defaults.json"
WORKSPACE_DEFAULTS = {
    "gcp_project_id": "project-fb2dc00c-a54a-48bd-884",
    "gcp_location": "global",
    "gcs_bucket": "my-video-automation-bucket-1",
    "gcs_prefix": "hermes",
    "gemini_model": "gemini-2.5-flash",
    "story_model": "gemini-2.5-flash",
    "veo_model": "veo-3.1-lite-generate-001",
    "veo_resolution": "720p",
    "veo_generate_audio": False,
    "mongo_uri": "",
    "mongo_db": "video-studio",
    "mongo_user_id": DEFAULT_MONGO_USER_ID,
}


def load_workspace_defaults(defaults_path: Path | None = None) -> dict[str, str]:
    path = defaults_path or WORKSPACE_DEFAULTS_PATH
    merged = dict(WORKSPACE_DEFAULTS)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if key in merged and value not in (None, ""):
                        merged[key] = str(value)
        except Exception:
            pass
    return merged


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def _parse_bool(value: object | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a new video project workspace")
    parser.add_argument("slug", help="Project slug, e.g. carthage")
    parser.add_argument("--title", help="Human-friendly title")
    parser.add_argument("--description", default="", help="Optional project description")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="Visual style template to copy into instructions/")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Where to create project folders")
    parser.add_argument("--gcp-project-id", default=None, help="Google Cloud project id")
    parser.add_argument("--gcp-location", default="global", help="Vertex AI location")
    parser.add_argument("--gcs-bucket", default=None, help="Optional output bucket name")
    parser.add_argument("--gcs-prefix", default="hermes", help="Default GCS output prefix")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash", help="Default Gemini model")
    parser.add_argument("--story-model", default="gemini-2.5-flash", help="Default Gemini model for story outlining")
    parser.add_argument("--veo-model", default="veo-3.1-lite-generate-001", help="Default Veo model")
    parser.add_argument("--veo-resolution", default="720p", help="Default Veo resolution")
    parser.add_argument("--veo-generate-audio", action=argparse.BooleanOptionalAction, default=False, help="Whether Veo should generate audio")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB connection string")
    parser.add_argument("--mongo-db", default=None, help="MongoDB database name")
    parser.add_argument("--user-id", default=DEFAULT_MONGO_USER_ID, help="MongoDB user ObjectId string")
    return parser


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_project_workspace(
    *,
    slug: str,
    title: str,
    description: str,
    base_dir: Path,
    style: str = DEFAULT_STYLE,
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
) -> Path:
    project_dir = base_dir / slug
    style_name = style.strip().lower() or DEFAULT_STYLE

    defaults = load_workspace_defaults()
    resolved_gcp_project_id = gcp_project_id or defaults["gcp_project_id"]
    resolved_gcp_location = gcp_location or defaults["gcp_location"]
    resolved_gcs_bucket = gcs_bucket or defaults["gcs_bucket"]
    resolved_gcs_prefix = gcs_prefix or defaults["gcs_prefix"]
    resolved_gemini_model = gemini_model or defaults["gemini_model"]
    resolved_story_model = story_model or defaults["story_model"]
    resolved_veo_model = veo_model or defaults["veo_model"]
    resolved_veo_resolution = veo_resolution or defaults.get("veo_resolution") or "720p"
    resolved_veo_generate_audio = veo_generate_audio if veo_generate_audio is not None else _parse_bool(defaults.get("veo_generate_audio"), False)
    resolved_mongo_uri = mongo_uri or defaults.get("mongo_uri") or os.getenv("MONGODB_URI")
    resolved_mongo_db = mongo_db or defaults.get("mongo_db") or os.getenv("MONGODB_DB", "video-studio")
    resolved_user_id = user_id or defaults.get("mongo_user_id") or DEFAULT_MONGO_USER_ID

    # Look up existing project in MongoDB to reuse ID and avoid duplicate creation
    project_id = None
    settings = resolve_settings(resolved_mongo_uri, resolved_mongo_db, resolved_user_id)
    if settings is not None:
        client = None
        try:
            client, db = connect(settings.uri, settings.db_name)
            existing = db["projects"].find_one({"slug": slug, "userId": settings.user_id})
            if existing:
                project_id = existing.get("id")
        except Exception:
            pass
        finally:
            if client is not None:
                client.close()

    if not project_id:
        project_id = f"project-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

    project_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["source", "story", "clips", "prompts", "veo", "logs", "instructions", "instructions/styles"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    manifest = {
        "slug": slug,
        "title": title,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "style": style_name,
        "visual_prompt_master_prompt": "instructions/visual_prompt_master_prompt.md",
        "style_template": str(resolve_style_template_path(style_name).relative_to(PROJECT_ROOT)),
        "gcp_project_id": resolved_gcp_project_id,
        "gcp_location": resolved_gcp_location,
        "gcs_bucket": resolved_gcs_bucket,
        "gcs_prefix": resolved_gcs_prefix,
        "gemini_model": resolved_gemini_model,
        "story_model": resolved_story_model,
        "veo_model": resolved_veo_model,
        "veo_resolution": resolved_veo_resolution,
        "veo_generate_audio": resolved_veo_generate_audio,
        "mongo_project_id": project_id,
        "mongo_uri": resolved_mongo_uri,
        "mongo_db": resolved_mongo_db,
        "mongo_user_id": resolved_user_id,
    }
    write_text(project_dir / "project.json", json.dumps(manifest, indent=2) + "\n")

    source_readme = """# Source

Drop the raw narration here.

Accepted inputs:
- pasted narration text
- a plain text / markdown file
- a Google Doc URL via `scripts/ingest_sections.py`

The ingestion step writes normalized text to `source/sections_raw.txt` and
records provenance in `source/source.json`.
"""
    write_text(project_dir / "source" / "README.md", source_readme)
    write_text(project_dir / "source" / "sections_raw.txt", "")

    story_readme = """# Story

This folder stores the Gemini story-structure pass for the full raw script.

Expected files:
- `story_outline.md` for the human-readable outline
- `story_outline.json` for metadata and validation notes
"""
    write_text(project_dir / "story" / "README.md", story_readme)

    clips_readme = """# Clips

This folder is for the clip splitter output.

Expected files:
- one `*_clips.txt` file per source script
- each file should follow the `Clip N (X words, Y.YYs):` format
"""
    write_text(project_dir / "clips" / "README.md", clips_readme)

    prompts_readme = """# Prompts

This folder stores Gemini-generated visual prompt markdown.

Expected files:
- canonical project-level `visual_prompts.md`
- progress state in `visual_prompts.state.json`
- optional validation metadata in `visual_prompts.json`
- legacy `*_visual_prompts.md` files are still supported for older workflows
"""
    write_text(project_dir / "prompts" / "README.md", prompts_readme)

    veo_readme = """# Veo

This folder is reserved for downstream Veo request artifacts, metadata, and
result handles.
"""
    write_text(project_dir / "veo" / "README.md", veo_readme)

    instructions_readme = """# Instructions

The copied master prompts live here so each project has its own frozen prompt
contracts for Gemini.
"""
    write_text(project_dir / "instructions" / "README.md", instructions_readme)

    copyfile(resolve_style_template_path(style_name), project_dir / "instructions" / "visual_prompt_master_prompt.md")
    style_prompt_dir = project_dir / "instructions" / "styles" / style_name
    style_prompt_dir.mkdir(parents=True, exist_ok=True)
    copyfile(resolve_style_template_path(style_name), style_prompt_dir / "visual_prompt_master_prompt.md")
    if DEFAULT_STORY_PROMPT.exists():
        copyfile(DEFAULT_STORY_PROMPT, project_dir / "instructions" / "story_sectioning_master_prompt.md")

    settings = resolve_settings(resolved_mongo_uri, resolved_mongo_db, resolved_user_id)
    mongo_status: dict[str, object]
    if settings is None:
        mongo_status = {"status": "skipped", "reason": "MongoDB settings are incomplete"}
    else:
        client = None
        try:
            client, db = connect(settings.uri, settings.db_name)
            project_doc = build_project_doc(
                project_id=project_id,
                name=title,
                description=description,
                user_id=settings.user_id,
                extra={
                    "slug": slug,
                    "style": style_name,
                    "styleTemplate": str(resolve_style_template_path(style_name).relative_to(PROJECT_ROOT)),
                    "workspacePath": str(project_dir),
                    "gcpProjectId": gcp_project_id,
                    "gcpLocation": gcp_location,
                    "gcsBucket": gcs_bucket,
                    "gcsPrefix": gcs_prefix,
                    "geminiModel": gemini_model,
                    "storyModel": story_model,
                    "veoModel": veo_model,
                },
            )
            result = upsert_project(db["projects"], project_doc)
            mongo_status = {
                "status": "written",
                "database": settings.db_name,
                "projectId": project_id,
                "matched": result.matched_count,
                "modified": result.modified_count,
                "upserted_id": str(result.upserted_id) if result.upserted_id is not None else None,
            }
        finally:
            if client is not None:
                client.close()

    print(json.dumps({"status": "created", "project_dir": str(project_dir), "manifest": manifest, "mongo": mongo_status}, indent=2))
    return project_dir


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    slug = slugify(args.slug)
    title = args.title or slug.replace("-", " ").title()
    create_project_workspace(
        slug=slug,
        title=title,
        description=args.description,
        base_dir=base_dir,
        style=args.style,
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
