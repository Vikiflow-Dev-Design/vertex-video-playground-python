from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.cut_generated_videos import (
    ClipTarget,
    collect_generated_artifacts,
    load_clip_targets,
    package_trimmed_videos,
    resolve_gcp_key_path,
    trim_video_file,
)


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def test_resolve_gcp_key_path_uses_configured_relative_path(tmp_path: Path, monkeypatch) -> None:
    key_path = tmp_path / "credentials.json"
    key_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_path))

    assert resolve_gcp_key_path() == key_path.resolve()


def test_load_clip_targets_reads_manifest(tmp_path: Path) -> None:
    manifest = {
        "sections": [
            {
                "section_index": 1,
                "section_name": "section-1",
                "clips": [
                    {"clip_number": 1, "duration_seconds": 7.611},
                    {"clip_number": 2, "duration_seconds": 7.361},
                ],
            }
        ]
    }
    manifest_path = tmp_path / "clips_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    targets = load_clip_targets(manifest_path)

    assert targets == [
        ClipTarget(section_name="section-1", clip_number=1, duration_seconds=7.611),
        ClipTarget(section_name="section-1", clip_number=2, duration_seconds=7.361),
    ]


def test_collect_generated_artifacts_parses_clip_number_and_gcs_uri(tmp_path: Path) -> None:
    veo_dir = tmp_path / "veo"
    veo_dir.mkdir()
    payload = {
        "status": "done",
        "videos": [{"index": 0, "uri": "gs://bucket/path/sample_0.mp4"}],
    }
    (veo_dir / "001_generate_video.json").write_text(json.dumps(payload), encoding="utf-8")

    artifacts = collect_generated_artifacts(veo_dir)

    assert len(artifacts) == 1
    assert artifacts[0].clip_number == 1
    assert artifacts[0].source_uri == "gs://bucket/path/sample_0.mp4"


@pytest.mark.parametrize("target_seconds", [1.0, 1.25])
def test_trim_video_file_creates_requested_duration(tmp_path: Path, target_seconds: float) -> None:
    source = tmp_path / "source.mp4"
    trimmed = tmp_path / "trimmed.mp4"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    trim_video_file(source, trimmed, target_seconds)

    assert trimmed.exists()
    duration = _ffprobe_duration(trimmed)
    assert duration == pytest.approx(target_seconds, abs=0.12)


def test_package_trimmed_videos_writes_zip(tmp_path: Path) -> None:
    files = []
    for idx in range(1, 3):
        path = tmp_path / f"clip_{idx:03d}.mp4"
        path.write_bytes(f"video-{idx}".encode("utf-8"))
        files.append(path)

    zip_path = tmp_path / "bundle.zip"
    manifest_path = tmp_path / "summary.json"
    manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    package_trimmed_videos(zip_path, files, manifest_path)

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == ["clip_001.mp4", "clip_002.mp4", "summary.json"]
