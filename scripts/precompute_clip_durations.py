#!/usr/bin/env python3
"""
precompute_clip_durations.py

Pre-computes the correct Veo job duration for each video clip in a section
BEFORE enqueuing generation jobs. This ensures every generated source video
has enough footage to cover any trim the recut script will later require.

Algorithm:
  1. Read sentence_clips from clips_manifest.json.
  2. Probe each sentence MP3 from audio/section_N/sentences/ via ffprobe.
     → Fail loudly if audio files are missing (user must run generate_project_audio.py first).
  3. Compute target duration per video clip (same logic as recut_section_clips.py Step 2).
  4. Apply overflow redistribution (same logic as recut_section_clips.py Step 3):
       - If a clip's computed duration exceeds its Veo step ceiling, cap it.
       - Add the overflow to the shorter immediate neighbour.
       - Repeat until stable.
  5. Map each final clip duration to its CEILING Veo step:
       - 0s < dur ≤ 4s → 4s
       - 4s < dur ≤ 6s → 6s
       - 6s < dur      → 8s
  6. Return dict[clip_number (int) → veo_job_duration (int)].

WHY CEILING:
  A clip needing 5.488s MUST be enqueued at 6s (not 4s),
  so the raw source footage has enough frames to trim.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLAYGROUND_DIR = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PLAYGROUND_DIR / "video_projects"

VEO_STEPS = (4, 6, 8)   # Valid Veo generation durations in seconds
MAX_VEO_DUR = float(max(VEO_STEPS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def probe_duration(path: Path) -> float:
    """Return the duration of a media file in seconds via ffprobe."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    raw = res.stdout.strip()
    if not raw:
        raise RuntimeError(f"ffprobe returned empty output for {path}")
    return float(raw)


def veo_ceiling(dur: float) -> int:
    """Map a duration to its CEILING Veo step (4, 6, or 8)."""
    for step in VEO_STEPS:
        if dur <= float(step):
            return step
    return max(VEO_STEPS)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def compute_clip_target_durations(
    project_dir: Path,
    section_index: int,
    verbose: bool = True,
) -> dict[int, float]:
    """
    Compute the REAL per-clip trim target durations from actual sentence audio.

    This is the single source of truth for how long each clip must be to stay
    in sync with narration.mp3. It measures the real rendered sentence MP3s
    (which include natural leading/trailing silence) and distributes them across
    video clips via the manifest's sentence->clip mapping (proportionally for
    split sentences).

    Each clip is trimmed to its OWN audio segment — we never borrow time from a
    neighbour. The only cap is the maximum Veo step (8s), which a clip's source
    footage can physically hold; the sentence clipper keeps clips under this in
    practice, so the cap is a safety net for rare edge cases.

    Returns
    -------
    dict mapping clip_number (int) -> real target duration in seconds (float).
    """
    manifest_path  = project_dir / "clips" / "clips_manifest.json"
    sentences_dir  = project_dir / "audio" / f"section_{section_index}" / "sentences"

    if not manifest_path.exists():
        raise FileNotFoundError(f"clips_manifest.json not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    section = next(
        (s for s in manifest.get("sections", []) if int(s.get("section_index", -1)) == section_index),
        None,
    )
    if not section:
        raise ValueError(f"Section {section_index} not found in clips_manifest.json")

    sentence_clips = section["sentence_clips"]

    # ── Step 1: Probe REAL sentence audio durations ─────────────────────────
    if not sentences_dir.exists():
        raise FileNotFoundError(
            f"Sentence audio directory not found: {sentences_dir}\n"
            f"  -> Run: python scripts/generate_project_audio.py --project <project> --section-index {section_index}"
        )

    sent_audio_durs: dict[int, float] = {}
    missing: list[str] = []
    for sent in sentence_clips:
        snum = int(sent["sentence_clip_number"])
        mp3_path = sentences_dir / f"sent_{snum:03d}.mp3"
        if not mp3_path.exists():
            missing.append(str(mp3_path))
        else:
            sent_audio_durs[snum] = probe_duration(mp3_path)

    if missing:
        paths_str = "\n    ".join(missing)
        raise FileNotFoundError(
            f"\nMissing sentence audio files for Section {section_index}:\n    {paths_str}\n"
            f"  -> Run: python scripts/generate_project_audio.py --project <project> --section-index {section_index}"
        )

    # ── Step 2: Distribute real audio across video clips (no borrowing) ─────
    new_durs: dict[int, float] = {}
    for sent in sentence_clips:
        snum = int(sent["sentence_clip_number"])
        new_sent_dur = sent_audio_durs[snum]

        if not sent.get("split", False):
            cn = int(sent["video_clip_numbers"][0])
            new_durs[cn] = round(min(new_sent_dur, MAX_VEO_DUR), 3)
        else:
            parts = sent["part_durations"]
            orig_total = sum(p["duration_seconds"] for p in parts)
            for part in parts:
                cn = int(part["clip_number"])
                ratio = part["duration_seconds"] / orig_total if orig_total else 0.5
                new_durs[cn] = round(min(new_sent_dur * ratio, MAX_VEO_DUR), 3)

    return new_durs


def precompute_clip_durations(
    project_dir: Path,
    section_index: int,
    verbose: bool = True,
) -> dict[int, int]:
    """
    Pre-computes the correct Veo job duration for every video clip in the section.

    Parameters
    ----------
    project_dir   : Path to the project directory (e.g., video_projects/the-entire-history-of-rome)
    section_index : 1-based section number
    verbose       : If True, prints progress to stdout

    Returns
    -------
    dict mapping clip_number (int) → veo_job_duration (int ∈ {4, 6, 8})
    """
    if verbose:
        print(f"\n[Pre-compute] Computing real per-clip target durations for Section {section_index}")

    new_durs = compute_clip_target_durations(project_dir, section_index, verbose=verbose)
    clip_nums = sorted(new_durs.keys())

    # ── Map to ceiling Veo steps ────────────────────────────────────────────
    if verbose:
        print(f"\n[Pre-compute] Mapping to Veo job durations (ceiling step)")
        print(f"\n  {'Clip':<10} {'Target':>10} {'Veo Job':>10}")
        print("  " + "-" * 32)

    job_durations: dict[int, int] = {}
    for cn in clip_nums:
        target = new_durs[cn]
        job_dur = veo_ceiling(target)
        job_durations[cn] = job_dur
        if verbose:
            print(f"  clip_{cn:03d}   {target:>8.3f}s   {job_dur:>6}s")

    if verbose:
        print(f"\n[Pre-compute] Done. {len(job_durations)} clips pre-computed for Section {section_index}.")

    return job_durations


# ---------------------------------------------------------------------------
# CLI (for standalone use / dry-run inspection)
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Pre-compute Veo job durations from real sentence audio durations."
    )
    parser.add_argument("--project", required=True, help="Project slug under video_projects/")
    parser.add_argument("--section-index", type=int, required=True, help="Section number (1-based)")
    args = parser.parse_args()

    project_dir = DEFAULT_PROJECTS_DIR / args.project
    if not project_dir.exists():
        print(f"[Error] Project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        job_durations = precompute_clip_durations(project_dir, args.section_index, verbose=True)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[Result] Pre-computed Veo job durations for Section {args.section_index}:")
    for cn, dur in sorted(job_durations.items()):
        print(f"  Clip {cn:03d}: {dur}s")


if __name__ == "__main__":
    main()
