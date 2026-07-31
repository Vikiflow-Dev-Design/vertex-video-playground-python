#!/usr/bin/env python3
"""Ingest raw narration scripts into a video project workspace.

Supports pasted text, plain files, and Google Doc URLs. The raw source is
stored in source/sections_raw.txt and provenance metadata is written to
source/source.json.

Optionally, the full raw script is also sent to Gemini for story-structure
analysis. That produces a story outline in story/story_outline.md, which is a
separate planning artifact and is not used for clip splitting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests
from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig

try:
    from google.genai.types import ThinkingConfig
except Exception:  # pragma: no cover - older SDKs may omit it
    ThinkingConfig = None

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except Exception:  # pragma: no cover - optional until doc URLs are used
    Credentials = None
    Request = None

from scripts.google_doc_tabs import extract_doc_id, extract_text_from_content, parse_tabbed_document, select_script_from_tabbed_document

load_dotenv()

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "video_projects"
DEFAULT_WORKSPACE_TOKEN = Path(os.getenv("GOOGLE_WORKSPACE_TOKEN_FILE", "/home/victor/.hermes/google_token.json"))
DEFAULT_STORY_PROMPT = Path(__file__).resolve().parents[1] / "templates" / "story_sectioning_master_prompt.md"
DEFAULT_STORY_MODEL = os.getenv("GEMINI_STORY_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
DOCS_SCOPE = "https://www.googleapis.com/auth/documents.readonly"


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    path = Path(project).expanduser()
    if path.exists():
        return path.resolve()
    return (base_dir / project).resolve()


def extract_doc_id(url: str) -> str:
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Could not extract Google Doc id from URL: {url}")
    return match.group(1)


def read_file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_doc_payload(url: str, token_file: Path) -> dict[str, object]:
    if Credentials is None or Request is None:
        raise RuntimeError("google-auth is not available in this environment")
    if not token_file.exists():
        raise FileNotFoundError(f"Workspace token file not found: {token_file}")

    creds = Credentials.from_authorized_user_file(str(token_file), scopes=[DOCS_SCOPE])
    if not creds.valid:
        creds.refresh(Request())

    doc_id = extract_doc_id(url)
    response = requests.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}?includeTabsContent=true",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def build_doc_inventory(payload: dict[str, object]) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for script in parse_tabbed_document(payload):
        inventory.append(
            {
                "style": script.style,
                "tab_title": script.tab_title,
                "tab_id": script.tab_id,
                "script_name": script.script_name,
                "characters": len(script.text),
            }
        )
    return inventory


def read_doc_text(url: str, token_file: Path, *, style: str | None = None, script_name: str | None = None) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    payload = read_doc_payload(url, token_file)
    tabs = payload.get("tabs", []) or []
    if tabs:
        inventory = build_doc_inventory(payload)
        if style is None:
            unique_styles = sorted({item["style"] for item in inventory if item.get("style")})
            if len(unique_styles) == 1:
                style = unique_styles[0]
            else:
                raise ValueError(
                    f"Tabbed doc requires --doc-style. Available styles: {', '.join(unique_styles)}"
                )
        selected = select_script_from_tabbed_document(payload, style=style, script_name=script_name)
        provenance = {
            "source_type": "google_doc_tab",
            "source_url": url,
            "doc_id": extract_doc_id(url),
            "style": selected.style,
            "tab_title": selected.tab_title,
            "tab_id": selected.tab_id,
            "script_name": selected.script_name,
            "tabbed": True,
        }
        return selected.text, provenance, inventory

    body = payload.get("body", {}) or {}
    content = body.get("content", []) or []
    raw_text = extract_text_from_content(content)
    provenance = {
        "source_type": "google_doc",
        "source_url": url,
        "doc_id": extract_doc_id(url),
        "tabbed": False,
    }
    return raw_text, provenance, []


def build_client(project_id: Optional[str], location: Optional[str]) -> genai.Client:
    project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    loc = location or os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Gemini calls")
    return genai.Client(vertexai=True, project=project, location=loc)


def read_story_prompt(project_dir: Path) -> str:
    project_prompt = project_dir / "instructions" / "story_sectioning_master_prompt.md"
    if project_prompt.exists():
        return project_prompt.read_text(encoding="utf-8")
    if DEFAULT_STORY_PROMPT.exists():
        return DEFAULT_STORY_PROMPT.read_text(encoding="utf-8")
    raise FileNotFoundError("Story sectioning master prompt not found")


def generate_story_outline(project_dir: Path, raw_text: str, project_id: Optional[str], location: Optional[str], model: str, temperature: float, thinking_budget: int) -> tuple[str, dict[str, object]]:
    client = build_client(project_id, location)
    prompt = read_story_prompt(project_dir)
    config_kwargs = {
        "systemInstruction": prompt,
        "temperature": temperature,
        "maxOutputTokens": 4096,
        "responseMimeType": "text/plain",
    }
    if thinking_budget > 0 and ThinkingConfig is not None:
        config_kwargs["thinkingConfig"] = ThinkingConfig(thinking_budget=thinking_budget)
    response = client.models.generate_content(model=model, contents=raw_text, config=GenerateContentConfig(**config_kwargs))
    text = getattr(response, "text", None)
    if not text:
        parts: list[str] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "text", None):
                    parts.append(part.text)
        text = "".join(parts)
    story = text.strip()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "temperature": temperature,
        "thinking_budget": thinking_budget,
        "characters": len(raw_text),
        "output_file": str(project_dir / "story" / "story_outline.md"),
    }
    return story, metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest section scripts into a video project")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Base directory for video projects")
    parser.add_argument("--file", help="Path to a raw sections file")
    parser.add_argument("--doc-url", help="Google Doc URL to import")
    parser.add_argument("--text", help="Raw pasted sections text")
    parser.add_argument("--workspace-token-file", default=str(DEFAULT_WORKSPACE_TOKEN), help="OAuth token file used for Google Docs API")
    parser.add_argument("--skip-story-outline", action="store_true", help="Do not call Gemini to generate a story outline")
    parser.add_argument("--story-model", default=DEFAULT_STORY_MODEL, help="Gemini model used for story outlining")
    parser.add_argument("--story-temperature", type=float, default=0.2, help="Story outline generation temperature")
    parser.add_argument("--story-thinking-budget", type=int, default=0, help="Optional thinking budget for the story outline")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)

    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory does not exist: {project_dir}")

    project_manifest_path = project_dir / "project.json"
    project_manifest = json.loads(project_manifest_path.read_text(encoding="utf-8")) if project_manifest_path.exists() else {}
    project_id = project_manifest.get("gcp_project_id")
    location = project_manifest.get("gcp_location")

    if args.file:
        source_type = "file"
        source_path = Path(args.file).expanduser().resolve()
        raw_text = read_file_text(source_path)
        provenance: dict[str, object] = {"source_type": source_type, "source_path": str(source_path)}
    elif args.doc_url:
        source_type = "google_doc"
        raw_text = read_doc_text(args.doc_url, Path(args.workspace_token_file).expanduser())
        provenance = {"source_type": source_type, "source_url": args.doc_url, "workspace_token_file": str(Path(args.workspace_token_file).expanduser())}
    elif args.text:
        source_type = "pasted_text"
        raw_text = args.text
        provenance = {"source_type": source_type}
    else:
        raise SystemExit("Provide one of --file, --doc-url, or --text")

    source_dir = project_dir / "source"
    story_dir = project_dir / "story"
    source_dir.mkdir(parents=True, exist_ok=True)
    story_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "sections_raw.txt").write_text(raw_text.rstrip() + "\n", encoding="utf-8")
    (source_dir / "source.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    story_status: dict[str, object] = {"status": "skipped"}
    if not args.skip_story_outline:
        story_text, story_meta = generate_story_outline(
            project_dir,
            raw_text,
            project_id,
            location,
            args.story_model,
            args.story_temperature,
            args.story_thinking_budget,
        )
        (story_dir / "story_outline.md").write_text(story_text.rstrip() + "\n", encoding="utf-8")
        story_meta.update({"status": "generated"})
        (story_dir / "story_outline.json").write_text(json.dumps(story_meta, indent=2) + "\n", encoding="utf-8")
        story_status = story_meta

    print(json.dumps({"status": "ingested", "project_dir": str(project_dir), "source": provenance, "characters": len(raw_text), "story": story_status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
