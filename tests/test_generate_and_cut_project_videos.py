from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from generate_video import VideoJob
from scripts.generate_and_cut_project_videos import (
    PromptBlock,
    batch_prompt_blocks,
    build_output_gcs_uri,
    discover_prompt_files,
    parse_prompt_file,
    run_generation_workflow,
)


def test_parse_prompt_file_reads_blocks(tmp_path: Path) -> None:
    prompt_file = tmp_path / "001-section-1_visual_prompts.md"
    prompt_file.write_text(
        """001: First prompt sentence.

--

STYLE: alpha

002: Second prompt sentence.

--

STYLE: alpha
""",
        encoding="utf-8",
    )

    blocks = parse_prompt_file(prompt_file)

    assert blocks == [
        PromptBlock(clip_number=1, prompt_text="001: First prompt sentence.\n\n--\n\nSTYLE: alpha", source_file=prompt_file),
        PromptBlock(clip_number=2, prompt_text="002: Second prompt sentence.\n\n--\n\nSTYLE: alpha", source_file=prompt_file),
    ]


def test_discover_prompt_files_prefers_consolidated_project_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    prompts_dir = project_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    consolidated = prompts_dir / "visual_prompts.md"
    legacy = prompts_dir / "001-opening_visual_prompts.md"
    consolidated.write_text("001: Consolidated prompt.\n\n--\n\nSTYLE: alpha\n", encoding="utf-8")
    legacy.write_text("001: Legacy prompt.\n\n--\n\nSTYLE: alpha\n", encoding="utf-8")

    assert discover_prompt_files(project_dir) == [consolidated]


def test_build_output_gcs_uri_uses_manifest_bucket_and_prefix() -> None:
    manifest: dict[str, object] = {"gcs_bucket": "bucket-name", "gcs_prefix": "hermes", "slug": "demo-project"}
    assert build_output_gcs_uri(manifest, "demo-project", 7) == "gs://bucket-name/hermes/demo-project/007/"


def test_batch_prompt_blocks_chunks_in_order() -> None:
    prompt_file = Path("/tmp/demo.md")
    blocks = [
        PromptBlock(clip_number=i, prompt_text=f"{i:03d}: Prompt", source_file=prompt_file)
        for i in range(1, 6)
    ]

    batches = batch_prompt_blocks(blocks, 2)

    assert [[block.clip_number for block in batch] for batch in batches] == [[1, 2], [3, 4], [5]]


def test_run_generation_workflow_generates_all_prompts_then_cuts(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    prompts_dir = project_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (project_dir / "clips").mkdir()
    (project_dir / "veo").mkdir()
    (project_dir / "clips" / "clips_manifest.json").write_text(
        json.dumps({"section_count": 2, "sections": [{"section_index": 1}, {"section_index": 2}]}),
        encoding="utf-8",
    )
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "slug": "demo-project",
                "gcp_project_id": "proj-123",
                "gcp_location": "global",
                "gcs_bucket": "bucket-name",
                "gcs_prefix": "hermes",
                "veo_model": "veo-3.1-lite-generate-001",
                "mongo_project_id": "mongo-project",
                "mongo_db": "video-studio",
                "mongo_user_id": "6a4264656320d6dd8421deba",
                "sections": [{"section_index": 1}, {"section_index": 2}],
            }
        ),
        encoding="utf-8",
    )

    prompt_file = prompts_dir / "visual_prompts.md"
    prompt_file.write_text(
        """001: First prompt.

--

STYLE: alpha

002: Second prompt.

--

STYLE: alpha
""",
        encoding="utf-8",
    )

    generated_jobs: list[VideoJob] = []
    cut_calls: list[tuple[Path, int | None]] = []

    def fake_generate(job: VideoJob) -> dict[str, object]:
        generated_jobs.append(job)
        return {"status": "done", "prompt": job.prompt}

    def fake_cut(path: Path, section_index: int | None) -> dict[str, object]:
        cut_calls.append((path, section_index))
        return {"zip_path": str(path / "veo" / "trimmed" / f"section-{section_index}.zip")}

    summary = run_generation_workflow(project_dir, generate_fn=fake_generate, cut_fn=fake_cut)

    assert [job.prompt for job in generated_jobs] == [
        "001: First prompt.\n\n--\n\nSTYLE: alpha",
        "002: Second prompt.\n\n--\n\nSTYLE: alpha",
    ]
    assert generated_jobs[0].output_gcs_uri == "gs://bucket-name/hermes/demo-project/001/"
    assert generated_jobs[1].output_gcs_uri == "gs://bucket-name/hermes/demo-project/002/"
    assert (project_dir / "veo" / "001_generate_video.json").exists()
    assert (project_dir / "veo" / "002_generate_video.json").exists()
    assert cut_calls == [(project_dir, 1), (project_dir, 2)]
    assert summary["prompt_files"] == [str(prompt_file)]
    assert len(cast(list[object], summary["generated"])) == 2
    assert isinstance(summary["cut_summary"], list)
    assert [item["zip_path"] for item in summary["cut_summary"]] == [
        str(project_dir / "veo" / "trimmed" / "section-1.zip"),
        str(project_dir / "veo" / "trimmed" / "section-2.zip"),
    ]
