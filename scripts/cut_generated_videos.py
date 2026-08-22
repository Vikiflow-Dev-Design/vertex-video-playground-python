#!/usr/bin/env python3
"""Download generated Veo videos, trim them to clip duration, and zip the results.

The script is intentionally batch-oriented: it waits until the generation phase
is done, then post-processes every generated video in one pass.

Workflow:
1. Read `clips/clips_manifest.json` and build clip-duration targets.
2. Find `*_generate_video.json` files under `veo/` and extract their GCS URIs.
3. Download each generated video with `gsutil cp`.
4. Trim each download to the exact duration from the manifest using ffmpeg.
5. Package the trimmed videos plus a JSON summary into a zip archive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:  # Support both "python scripts/x.py" and "python -m scripts.x" invocations
    from scripts.precompute_clip_durations import compute_clip_target_durations
except ImportError:  # pragma: no cover - fallback when run as a top-level script
    from precompute_clip_durations import compute_clip_target_durations

CLIP_JSON_RE = re.compile(r"^(?P<clip>\d+)(?:[-_].*)?_(?:generate_video|video)$")
GCS_URI_RE = re.compile(r"^gs://.+\.mp4(?:\?.*)?$")
PLAYGROUND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PLAYGROUND_DIR / ".env")


@dataclass(frozen=True)
class ClipTarget:
    section_name: str
    clip_number: int
    duration_seconds: float


@dataclass(frozen=True)
class GeneratedArtifact:
    clip_number: int
    source_json: Path
    source_uri: str
    sample_index: int


@dataclass
class TrimmedVideo:
    clip_number: int
    sample_index: int
    source_json: str
    source_uri: str
    downloaded_path: str
    trimmed_path: str
    target_duration_seconds: float
    actual_duration_seconds: float


class VideoCutError(RuntimeError):
    pass


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    candidate = Path(project).expanduser()
    if candidate.exists():
        return candidate.resolve()
    return (base_dir / project).resolve()


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise VideoCutError(f"Required command not found on PATH: {name}")


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoCutError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def load_clip_targets(manifest_path: Path, section_index: int | None = None) -> list[ClipTarget]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = payload.get("sections", [])
    if not sections:
        raise VideoCutError(f"No sections found in manifest: {manifest_path}")

    if section_index is None:
        if len(sections) > 1:
            raise VideoCutError(
                f"Manifest has {len(sections)} sections; pass --section-index to choose one"
            )
        selected_sections = sections
    else:
        selected_sections = [section for section in sections if int(section.get("section_index", -1)) == section_index]
        if not selected_sections:
            raise VideoCutError(f"Section {section_index} was not found in {manifest_path}")

    targets: list[ClipTarget] = []
    for section in selected_sections:
        section_name = str(section.get("section_name") or f"section-{section.get('section_index', 'unknown')}")
        for clip in section.get("clips", []):
            clip_number = int(clip["clip_number"])
            duration_seconds = float(clip["duration_seconds"])
            targets.append(ClipTarget(section_name=section_name, clip_number=clip_number, duration_seconds=duration_seconds))
    return targets


def _extract_gcs_uris(value: Any) -> list[str]:
    uris: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            uris.extend(_extract_gcs_uris(item))
    elif isinstance(value, list):
        for item in value:
            uris.extend(_extract_gcs_uris(item))
    elif isinstance(value, str) and GCS_URI_RE.match(value):
        uris.append(value)
    return uris


def _clip_number_from_path(path: Path) -> int:
    match = CLIP_JSON_RE.match(path.stem)
    if not match:
        digits = re.match(r"^(\d+)", path.stem)
        if not digits:
            raise VideoCutError(f"Could not infer clip number from file name: {path.name}")
        return int(digits.group(1))
    return int(match.group("clip"))


def collect_generated_artifacts(veo_dir: Path) -> list[GeneratedArtifact]:
    artifacts: list[GeneratedArtifact] = []
    for json_path in sorted(veo_dir.glob("*_generate_video.json")):
        clip_number = _clip_number_from_path(json_path)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        uris = _extract_gcs_uris(payload)
        if not uris:
            raise VideoCutError(f"No GCS URIs found in {json_path}")
        for sample_index, uri in enumerate(dict.fromkeys(uris)):
            artifacts.append(
                GeneratedArtifact(
                    clip_number=clip_number,
                    source_json=json_path,
                    source_uri=uri,
                    sample_index=sample_index,
                )
            )
    return artifacts


def ffprobe_duration_seconds(path: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def has_audio_stream(path: Path) -> bool:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return bool(result.stdout.strip())


def resolve_gcp_key_path() -> Path:
    """Resolve the project credential path independent of the caller's cwd."""
    configured = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    candidates = []
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(configured_path if configured_path.is_absolute() else PLAYGROUND_DIR / configured_path)
    candidates.extend([PLAYGROUND_DIR / "gcp-key.json", Path.cwd() / "gcp-key.json"])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Google service-account key not found. Searched: {searched}")


def download_gcs_uri(gcs_uri: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("gsutil"):
        res = subprocess.run(["gsutil", "cp", gcs_uri, str(destination)], capture_output=True)
        if res.returncode == 0:
            return
    import urllib.request
    import urllib.parse
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    
    key_path = resolve_gcp_key_path()

    creds = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
    )
    creds.refresh(Request())

    clean_uri = gcs_uri.replace("gs://", "")
    bucket_name, blob_path = clean_uri.split("/", 1)
    encoded_blob = urllib.parse.quote(blob_path, safe="")
    url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket_name}/o/{encoded_blob}?alt=media"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
    with urllib.request.urlopen(req) as resp, open(destination, "wb") as out:
        shutil.copyfileobj(resp, out)


def trim_video_file(source: Path, destination: Path, duration_seconds: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    common_args = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_audio_stream(source):
        args = [*common_args, "-c:a", "aac", "-b:a", "128k", str(destination)]
    else:
        args = [*common_args, "-an", str(destination)]
    run_command(args)


def package_trimmed_videos(zip_path: Path, files: list[Path], summary_path: Path | None = None) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
        if summary_path is not None and summary_path.exists():
            archive.write(summary_path, arcname=summary_path.name)


def build_summary(
    targets: list[ClipTarget],
    artifacts: list[GeneratedArtifact],
    trimmed_files: list[TrimmedVideo],
    project_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    targets_by_clip = {target.clip_number: target for target in targets}
    remaining = set(targets_by_clip)
    for artifact in artifacts:
        remaining.discard(artifact.clip_number)

    return {
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "clip_targets": [asdict(target) for target in targets],
        "generated_artifacts": [
            {
                "clip_number": artifact.clip_number,
                "sample_index": artifact.sample_index,
                "source_json": str(artifact.source_json),
                "source_uri": artifact.source_uri,
            }
            for artifact in artifacts
        ],
        "trimmed_videos": [asdict(item) for item in trimmed_files],
        "missing_clip_numbers": sorted(remaining),
    }


def cut_project_videos(project_dir: Path, section_index: int | None = None) -> dict[str, Any]:
    ensure_command("ffmpeg")
    ensure_command("ffprobe")

    manifest_path = project_dir / "clips" / "clips_manifest.json"
    veo_dir = project_dir / "veo"
    if not manifest_path.exists():
        raise VideoCutError(f"Manifest not found: {manifest_path}")
    if not veo_dir.exists():
        raise VideoCutError(f"Veo directory not found: {veo_dir}")

    targets = load_clip_targets(manifest_path, section_index=section_index)
    if not targets:
        raise VideoCutError("No clip targets were loaded from the manifest")
    target_map = {target.clip_number: target for target in targets}

    # The manifest stores the spoken WORD-SPAN per clip, but narration.mp3 is
    # built from the full sentence MP3s (which include natural leading/trailing
    # silence). Trimming to the word-span makes every clip ~0.5s short, and the
    # drift accumulates until the visuals race ahead of the voice. Recompute the
    # REAL per-clip trim targets from the actual sentence audio so each clip
    # matches its narration segment exactly.
    real_targets: dict[int, float] = {}
    try:
        section_for_durations = section_index
        if section_for_durations is None:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_sections = manifest_payload.get("sections", [])
            if len(manifest_sections) == 1:
                section_for_durations = int(manifest_sections[0].get("section_index", 1))
        if section_for_durations is not None:
            real_targets = compute_clip_target_durations(
                project_dir, section_for_durations, verbose=False
            )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"[WARN] Falling back to manifest word-span durations: {exc}")

    artifacts = collect_generated_artifacts(veo_dir)
    if not artifacts:
        raise VideoCutError(f"No generated video JSON files found in {veo_dir}")

    output_dir = veo_dir / "trimmed"
    download_dir = output_dir / "downloaded"
    cut_dir = output_dir / "cut"
    output_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    cut_dir.mkdir(parents=True, exist_ok=True)

    trimmed_files: list[TrimmedVideo] = []
    skipped: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(dir=output_dir, prefix="work-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        for artifact in artifacts:
            target = target_map.get(artifact.clip_number)
            if target is None:
                skipped.append(
                    {
                        "clip_number": artifact.clip_number,
                        "source_json": str(artifact.source_json),
                        "reason": "no matching duration target in manifest",
                    }
                )
                continue

            downloaded = download_dir / f"{artifact.clip_number:03d}_sample_{artifact.sample_index}_source.mp4"
            trimmed = cut_dir / f"{artifact.clip_number:03d}_sample_{artifact.sample_index}_cut.mp4"
            tmp_download = tmp_root / downloaded.name
            tmp_trimmed = tmp_root / trimmed.name

            download_gcs_uri(artifact.source_uri, tmp_download)

            # Prefer the REAL audio-derived target so the clip matches its
            # narration segment. Never request more than the source footage
            # actually contains (avoids ffmpeg padding / freeze frames).
            source_footage = ffprobe_duration_seconds(tmp_download)
            desired_duration = real_targets.get(artifact.clip_number, target.duration_seconds)
            trim_duration = min(desired_duration, source_footage)

            trim_video_file(tmp_download, tmp_trimmed, trim_duration)
            shutil.move(str(tmp_trimmed), trimmed)
            shutil.move(str(tmp_download), downloaded)

            trimmed_files.append(
                TrimmedVideo(
                    clip_number=artifact.clip_number,
                    sample_index=artifact.sample_index,
                    source_json=str(artifact.source_json),
                    source_uri=artifact.source_uri,
                    downloaded_path=str(downloaded),
                    trimmed_path=str(trimmed),
                    target_duration_seconds=trim_duration,
                    actual_duration_seconds=ffprobe_duration_seconds(trimmed),
                )
            )

    summary = build_summary(targets, artifacts, trimmed_files, project_dir, output_dir)
    summary["skipped"] = skipped

    summary_path = output_dir / "trimmed_videos.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    zip_path = output_dir / f"{slugify(project_dir.name)}_trimmed_videos.zip"
    package_trimmed_videos(zip_path, [Path(item.trimmed_path) for item in trimmed_files], summary_path)

    summary["summary_path"] = str(summary_path)
    summary["zip_path"] = str(zip_path)
    summary["trimmed_count"] = len(trimmed_files)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download, trim, and zip generated Veo videos")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parents[1] / "video_projects"), help="Base directory for video projects")
    parser.add_argument("--section-index", type=int, default=None, help="Specific section index to use when the manifest contains multiple sections")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)
    if not project_dir.exists():
        raise SystemExit(f"Project directory does not exist: {project_dir}")

    summary = cut_project_videos(project_dir, section_index=args.section_index)
    print(json.dumps({"status": "ok", **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
