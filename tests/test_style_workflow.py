from __future__ import annotations

import json
from pathlib import Path

import scripts.create_video_project as create_video_project
from scripts.create_video_project import create_project_workspace
from scripts.style_profiles import resolve_style_template_path
from scripts.start_video_creation import build_workflow_commands


def test_resolve_style_template_path_current_exists() -> None:
    path = resolve_style_template_path("current")
    assert path.name == "visual_prompt_master_prompt.md"
    assert path.exists()
    assert "styles/current" in str(path)


def test_create_project_workspace_records_style_and_copies_style_prompt(tmp_path: Path) -> None:
    project_dir = create_project_workspace(
        slug="demo-style-project",
        title="Demo Style Project",
        description="",
        base_dir=tmp_path,
        style="current",
        gcp_project_id=None,
        gcp_location="global",
        gcs_bucket=None,
        gcs_prefix="hermes",
        gemini_model="gemini-2.5-flash",
        story_model="gemini-2.5-flash",
        veo_model="veo-3.1-lite-generate-001",
        mongo_uri=None,
        mongo_db=None,
        user_id=None,
    )

    manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert manifest["style"] == "current"
    assert manifest["visual_prompt_master_prompt"] == "instructions/visual_prompt_master_prompt.md"

    copied_prompt = (project_dir / "instructions" / "visual_prompt_master_prompt.md").read_text(encoding="utf-8")
    template_prompt = resolve_style_template_path("current").read_text(encoding="utf-8")
    assert copied_prompt == template_prompt


def test_create_project_workspace_uses_workspace_defaults(tmp_path: Path, monkeypatch) -> None:
    defaults_file = tmp_path / "_workspace_defaults.json"
    defaults_file.write_text(
        json.dumps(
            {
                "gcp_project_id": "proj-default",
                "gcp_location": "us-central1",
                "gcs_bucket": "bucket-default",
                "gcs_prefix": "hermes-default",
                "gemini_model": "gemini-default",
                "story_model": "story-default",
                "veo_model": "veo-default",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(create_video_project, "WORKSPACE_DEFAULTS_PATH", defaults_file)

    project_dir = create_video_project.create_project_workspace(
        slug="demo-defaults-project",
        title="Demo Defaults Project",
        description="",
        base_dir=tmp_path,
        style="current",
        gcp_project_id=None,
        gcp_location="",
        gcs_bucket=None,
        gcs_prefix="",
        gemini_model="",
        story_model="",
        veo_model="",
        mongo_uri=None,
        mongo_db=None,
        user_id=None,
    )

    manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert manifest["gcp_project_id"] == "proj-default"
    assert manifest["gcp_location"] == "us-central1"
    assert manifest["gcs_bucket"] == "bucket-default"
    assert manifest["gcs_prefix"] == "hermes-default"
    assert manifest["gemini_model"] == "gemini-default"
    assert manifest["story_model"] == "story-default"
    assert manifest["veo_model"] == "veo-default"


def test_build_workflow_commands_uses_style_and_text_input(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-style-project"
    commands = build_workflow_commands(
        project="demo-style-project",
        style="paper",
        project_dir=project_dir,
        title="Demo Style Project",
        description="",
        source_text="STYLE: paper\n\nHello world.",
        source_file=None,
        doc_url=None,
        base_dir=tmp_path,
        gcp_project_id=None,
        gcp_location="global",
        gcs_bucket=None,
        gcs_prefix="hermes",
        gemini_model="gemini-2.5-flash",
        story_model="gemini-2.5-flash",
        veo_model="veo-3.1-lite-generate-001",
        mongo_uri=None,
        mongo_db=None,
        user_id=None,
        skip_story_outline=False,
    )

    assert len(commands) == 4
    assert commands[0][1] == "scripts/create_video_project.py"
    assert "--style" in commands[0]
    assert "paper" in commands[0]
    assert commands[1][1] == "scripts/ingest_sections.py"
    assert "--text" in commands[1]
    assert commands[2][1] == "scripts/generate_section_clips.py"
    assert commands[3][1] == "scripts/generate_visual_prompts.py"
