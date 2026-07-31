from __future__ import annotations

import json

from scripts.generate_section_clips import Section, clip_file_name, load_source_scripts
from scripts.generate_visual_prompts import parse_clip_metadata
from scripts.ingest_google_doc import write_tabbed_sources


def test_write_tabbed_sources_creates_manifest_and_style_files(tmp_path) -> None:
    project_dir = tmp_path / "project"
    (project_dir / "instructions").mkdir(parents=True)
    payload = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t-paper", "title": "paper"},
                "documentTab": {
                    "body": {
                        "content": [
                            {"paragraph": {"elements": [{"textRun": {"content": "SCRIPT NAME: sunrise\n"}}]}},
                            {"paragraph": {"elements": [{"textRun": {"content": "SCRIPT:\n"}}]}},
                            {"paragraph": {"elements": [{"textRun": {"content": "A paper city wakes up.\n"}}]}},
                        ]
                    }
                },
            }
        ]
    }

    result = write_tabbed_sources(project_dir, "https://docs.google.com/document/d/test/edit", payload)

    manifest = json.loads((project_dir / "source" / "scripts_manifest.json").read_text(encoding="utf-8"))
    assert manifest["scripts"][0]["style"] == "paper"
    assert manifest["scripts"][0]["script_name"] == "sunrise"
    assert (project_dir / "source" / "scripts" / "paper" / "001-sunrise.md").exists()
    assert (project_dir / "instructions" / "styles" / "paper" / "visual_prompt_master_prompt.md").exists()
    assert result["provenance"]["tabbed"] is True


def test_load_source_scripts_reads_manifest(tmp_path) -> None:
    project_dir = tmp_path / "project"
    source_dir = project_dir / "source"
    scripts_dir = source_dir / "scripts" / "paper"
    scripts_dir.mkdir(parents=True)
    script_path = scripts_dir / "001-sunrise.md"
    script_path.write_text("The sun rises over the city.\n", encoding="utf-8")
    (source_dir / "scripts_manifest.json").write_text(
        json.dumps(
            {
                "scripts": [
                    {
                        "index": 1,
                        "style": "paper",
                        "tab_title": "paper",
                        "tab_id": "t-paper",
                        "script_name": "sunrise",
                        "source_file": "source/scripts/paper/001-sunrise.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sections = load_source_scripts(project_dir, source_dir / "sections_raw.txt")
    assert len(sections) == 1
    assert sections[0].style == "paper"
    assert sections[0].script_name == "sunrise"
    assert sections[0].text.startswith("The sun rises")
    assert clip_file_name(Section(name="intro", title="Intro", text="Hello", index=1, style="paper", script_name="sunrise")) == "001-paper-sunrise-intro_clips.txt"


def test_parse_clip_metadata_extracts_style_from_clip_file_header() -> None:
    text = """STYLE: paper
TAB TITLE: paper
SCRIPT NAME: sunrise

Total words: 3
Total clips: 1
Clip ceiling: 8s
"""
    metadata = parse_clip_metadata(text)
    assert metadata["style"] == "paper"
    assert metadata["script_name"] == "sunrise"
