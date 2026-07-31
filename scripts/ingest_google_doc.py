#!/usr/bin/env python3
"""Ingest a Google Doc into a multi-script video project.

This workflow is tab-aware:
- tab title = style name
- each tab can contain one or more scripts
- each script is written to its own source file so the existing clip/prompt
  pipeline can fan out over many scripts in one document

If the document is not tabbed, the script falls back to the classic single-
script ingestion flow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
from dotenv import load_dotenv

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except Exception:  # pragma: no cover - optional until doc URLs are used
    Credentials = None
    Request = None

from scripts.google_doc_tabs import (
    ScriptBlock,
    extract_doc_id,
    extract_text_from_content,
    parse_tabbed_document,
)
from scripts.style_profiles import resolve_style_template_path

load_dotenv()

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "video_projects"
DEFAULT_WORKSPACE_TOKEN = Path(os.getenv("GOOGLE_WORKSPACE_TOKEN_FILE", "/home/victor/.hermes/google_token.json"))
DOCS_SCOPE = "https://www.googleapis.com/auth/documents.readonly"


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "script"


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    path = Path(project).expanduser()
    if path.exists():
        return path.resolve()
    return (base_dir / project).resolve()


def ensure_style_prompt(project_dir: Path, style: str) -> Path:
    normalized = slugify(style)
    template = resolve_style_template_path(normalized)
    style_dir = project_dir / "instructions" / "styles" / normalized
    style_dir.mkdir(parents=True, exist_ok=True)
    target = style_dir / "visual_prompt_master_prompt.md"
    if not target.exists():
        target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def read_doc_payload(url: str, token_file: Path) -> dict[str, Any]:
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


def read_plain_doc_text(url: str, token_file: Path) -> str:
    payload = read_doc_payload(url, token_file)
    body = payload.get("body", {}) or {}
    content = body.get("content", []) or []
    return extract_text_from_content(content)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def script_source_text(block: ScriptBlock) -> str:
    return block.text.rstrip() + "\n"


def write_tabbed_sources(project_dir: Path, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    scripts = parse_tabbed_document(payload)
    if not scripts:
        raise ValueError("Tabbed Google Doc did not contain any script content")

    source_dir = project_dir / "source"
    scripts_dir = source_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    inventory: list[dict[str, Any]] = []
    styles: list[str] = []
    for index, block in enumerate(scripts, start=1):
        style = slugify(block.style)
        if style not in styles:
            styles.append(style)
        ensure_style_prompt(project_dir, style)
        style_dir = scripts_dir / style
        style_dir.mkdir(parents=True, exist_ok=True)

        script_slug = slugify(block.script_name)
        source_path = style_dir / f"{index:03d}-{script_slug}.md"
        write_text(source_path, script_source_text(block))

        inventory.append(
            {
                "index": index,
                "style": style,
                "tab_title": block.tab_title,
                "tab_id": block.tab_id,
                "script_name": block.script_name,
                "source_file": str(source_path.relative_to(project_dir)),
                "characters": len(block.text),
            }
        )

    (source_dir / "scripts_manifest.json").write_text(
        json.dumps({"scripts": inventory}, indent=2) + "\n",
        encoding="utf-8",
    )

    concatenated = []
    for item in inventory:
        source_path = project_dir / item["source_file"]
        concatenated.append(
            f"### STYLE: {item['style']}\n"
            f"### TAB: {item['tab_title']}\n"
            f"### SCRIPT NAME: {item['script_name']}\n\n"
            f"{source_path.read_text(encoding='utf-8').rstrip()}\n"
        )
    (source_dir / "sections_raw.txt").write_text("\n---\n\n".join(concatenated).rstrip() + "\n", encoding="utf-8")

    provenance = {
        "source_type": "google_doc_tabs",
        "source_url": url,
        "doc_id": extract_doc_id(url),
        "tabbed": True,
        "script_count": len(inventory),
        "styles": styles,
    }
    (source_dir / "source.json").write_text(json.dumps({**provenance, "scripts": inventory}, indent=2) + "\n", encoding="utf-8")
    return {"provenance": provenance, "inventory": inventory}


def write_plain_doc_source(project_dir: Path, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body", {}) or {}
    content = body.get("content", []) or []
    raw_text = extract_text_from_content(content)
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    write_text(source_dir / "sections_raw.txt", raw_text.rstrip() + "\n")
    provenance = {
        "source_type": "google_doc",
        "source_url": url,
        "doc_id": extract_doc_id(url),
        "tabbed": False,
    }
    (source_dir / "source.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return {"provenance": provenance, "inventory": []}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest a Google Doc into a multi-script video project")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Base directory for video projects")
    parser.add_argument("--doc-url", required=True, help="Google Doc URL to import")
    parser.add_argument("--workspace-token-file", default=str(DEFAULT_WORKSPACE_TOKEN), help="OAuth token file used for Google Docs API")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory does not exist: {project_dir}")

    payload = read_doc_payload(args.doc_url, Path(args.workspace_token_file).expanduser())
    tabs = payload.get("tabs", []) or []

    if tabs:
        result = write_tabbed_sources(project_dir, args.doc_url, payload)
    else:
        result = write_plain_doc_source(project_dir, args.doc_url, payload)

    print(json.dumps({
        "status": "ingested",
        "project_dir": str(project_dir),
        **result,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
