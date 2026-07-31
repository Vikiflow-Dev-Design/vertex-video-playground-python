from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_visual_prompts import run_prompt_generation


def test_run_prompt_generation_writes_single_project_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo-project"
    clips_dir = project_dir / "clips"
    prompts_dir = project_dir / "prompts"
    clips_dir.mkdir(parents=True)
    prompts_dir.mkdir(parents=True)

    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "slug": "demo-project",
                "gcp_project_id": "proj-123",
                "gcp_location": "global",
                "gcs_bucket": "bucket-name",
                "gcs_prefix": "hermes",
                "gemini_model": "gemini-2.5-flash",
            }
        ),
        encoding="utf-8",
    )

    (clips_dir / "001-opening_clips.txt").write_text(
        """Total words: 4
Total clips: 2
Clip ceiling: 8.0s

Clip 1 (2 words, 1.00s):
First opening clip

Clip 2 (2 words, 1.00s):
Second opening clip
""",
        encoding="utf-8",
    )
    (clips_dir / "002-origins_clips.txt").write_text(
        """Total words: 2
Total clips: 1
Clip ceiling: 8.0s

Clip 1 (2 words, 1.00s):
Third clip
""",
        encoding="utf-8",
    )

    calls: list[str] = []

    def fake_send(_client, _model, _master_prompt, clip_payload, _temperature, _thinking_budget):
        calls.append(clip_payload)
        clip_numbers: list[str] = []
        in_clips = False
        for line in clip_payload.splitlines():
            if line.strip() == "CLIPS:":
                in_clips = True
                continue
            if not in_clips:
                continue
            if line[:3].isdigit():
                clip_numbers.append(line.split(":", 1)[0])
        return "\n\n".join(
            f"{clip_number}: Prompt for {clip_number}\n\n--\n\nSTYLE: alpha" for clip_number in clip_numbers
        ) + "\n"

    def fake_client_factory(*_args, **_kwargs):
        return object()

    summary = run_prompt_generation(
        project_dir,
        send_fn=fake_send,
        client_factory=fake_client_factory,
        batch_size=2,
    )

    output_path = prompts_dir / "visual_prompts.md"
    state_path = prompts_dir / "visual_prompts.state.json"
    json_path = prompts_dir / "visual_prompts.json"

    assert output_path.exists()
    assert state_path.exists()
    assert json_path.exists()
    assert len(calls) == 2
    assert summary["output_path"] == str(output_path)
    assert summary["batch_size"] == 2
    assert "001: Prompt for 001" in output_path.read_text(encoding="utf-8")
    assert "002: Prompt for 002" in output_path.read_text(encoding="utf-8")
    assert "001: Prompt for 001" in output_path.read_text(encoding="utf-8")
