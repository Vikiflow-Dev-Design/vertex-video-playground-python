from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.precompute_clip_durations import (
    compute_clip_target_durations,
    precompute_clip_durations,
    veo_ceiling,
)


def _write_silent_mp3(path: Path, seconds: float) -> None:
    """Render a real MP3 of a given duration so ffprobe reports it accurately."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{seconds:.3f}",
            "-q:a",
            "9",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _build_project(tmp_path: Path, sentence_specs: list[tuple[int, float, float]]) -> Path:
    """
    sentence_specs: list of (clip_number, manifest_word_span, real_audio_seconds).
    Builds a minimal project with a 1:1 sentence->clip mapping.
    """
    project_dir = tmp_path / "proj"
    (project_dir / "clips").mkdir(parents=True, exist_ok=True)
    sentences_dir = project_dir / "audio" / "section_1" / "sentences"
    sentences_dir.mkdir(parents=True, exist_ok=True)

    clips = []
    sentence_clips = []
    for clip_number, word_span, real_seconds in sentence_specs:
        clips.append(
            {
                "clip_number": clip_number,
                "global_clip_number": clip_number,
                "duration_seconds": word_span,
                "text": f"sentence {clip_number}",
            }
        )
        sentence_clips.append(
            {
                "sentence_clip_number": clip_number,
                "total_duration_seconds": word_span,
                "video_clip_numbers": [clip_number],
                "split": False,
            }
        )
        _write_silent_mp3(sentences_dir / f"sent_{clip_number:03d}.mp3", real_seconds)

    manifest = {
        "sections": [
            {
                "section_index": 1,
                "section_name": "section-1",
                "clips": clips,
                "sentence_clips": sentence_clips,
            }
        ]
    }
    (project_dir / "clips" / "clips_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project_dir


def test_veo_ceiling_maps_to_valid_steps() -> None:
    assert veo_ceiling(3.9) == 4
    assert veo_ceiling(4.0) == 4
    assert veo_ceiling(4.1) == 6
    assert veo_ceiling(6.0) == 6
    assert veo_ceiling(6.5) == 8
    assert veo_ceiling(99.0) == 8


def test_real_targets_use_full_audio_not_word_span(tmp_path: Path) -> None:
    """
    Regression: the trim target MUST come from the real sentence audio (which
    includes leading/trailing silence), not the manifest word-span. Otherwise
    clips are ~0.5s short each and drift ahead of the narration.
    """
    specs = [
        (1, 4.431, 4.944),
        (2, 2.722, 3.264),
        (3, 3.306, 3.816),
    ]
    project_dir = _build_project(tmp_path, specs)

    targets = compute_clip_target_durations(project_dir, section_index=1, verbose=False)

    # Each clip target should match its REAL audio duration, not the word-span.
    for clip_number, word_span, real_seconds in specs:
        assert targets[clip_number] == pytest.approx(real_seconds, abs=0.06)
        assert targets[clip_number] > word_span

    # The total must match the narration length (sum of real audio), not the
    # shorter word-span total. Allow for MP3 frame-quantization on the synthetic
    # fixtures (~0.05s/file).
    real_total = sum(real for _, _, real in specs)
    word_span_total = sum(ws for _, ws, _ in specs)
    targets_total = sum(targets.values())
    assert targets_total == pytest.approx(real_total, abs=0.25)
    # And it must be clearly larger than the buggy word-span total.
    assert targets_total > word_span_total + 1.0


def test_each_clip_trimmed_to_own_audio_without_borrowing(tmp_path: Path) -> None:
    """
    Each clip must be trimmed to its OWN real audio — never inflated by borrowing
    time from a neighbour. This is what keeps every clip aligned with its own
    narration segment (the residual-drift fix on clips 3 and 9).
    """
    specs = [
        (1, 3.306, 3.816),
        (2, 5.750, 6.264),  # real audio just over the old 6s word-span ceiling
        (3, 4.694, 5.208),
    ]
    project_dir = _build_project(tmp_path, specs)

    targets = compute_clip_target_durations(project_dir, section_index=1, verbose=False)

    # Clip 2 keeps its own full audio (no cap to 6.0), and neighbours 1 and 3
    # are NOT inflated beyond their own audio.
    for clip_number, _word_span, real_seconds in specs:
        assert targets[clip_number] == pytest.approx(real_seconds, abs=0.06)


def test_target_never_exceeds_max_veo_step(tmp_path: Path) -> None:
    """A pathologically long sentence is capped at the 8s Veo maximum."""
    specs = [(1, 7.9, 9.5)]  # real audio 9.5s cannot exceed an 8s source clip
    project_dir = _build_project(tmp_path, specs)

    targets = compute_clip_target_durations(project_dir, section_index=1, verbose=False)

    assert targets[1] == pytest.approx(8.0, abs=0.01)


def test_precompute_maps_real_targets_to_veo_steps(tmp_path: Path) -> None:
    specs = [
        (1, 4.431, 4.944),  # 4.944 -> 6s ceiling
        (2, 2.722, 3.264),  # 3.264 -> 4s ceiling
    ]
    project_dir = _build_project(tmp_path, specs)

    jobs = precompute_clip_durations(project_dir, section_index=1, verbose=False)

    assert jobs[1] == 6
    assert jobs[2] == 4
