#!/usr/bin/env python3
"""
recut_section_clips.py

Re-cuts video clips from raw Veo sources so every clip duration matches
its actual sentence audio duration, making video and narration perfectly aligned.

Algorithm:
  1. Probe each sentence MP3 for its real duration.
  2. For standalone clips:  new_dur = sentence_audio_dur
  3. For split clips:       new_dur = sentence_audio_dur * (orig_part_dur / orig_total_dur)
  4. Overflow rule (clip > 8s source limit):
       - Cap the clip at MAX_SOURCE_DUR (8.0s)
       - Find the shorter of its immediate neighbours (before / after)
       - Add the overflow seconds to that neighbour
  5. Re-trim each clip from veo/trimmed/downloaded/ raw source.
  6. Concatenate all re-cut clips + narration.mp3 -> exports/section_N_final.mp4

Usage:
    python scripts/recut_section_clips.py --project the-entire-history-of-rome --section-index 1
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLAYGROUND_DIR = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PLAYGROUND_DIR / "video_projects"
MAX_SOURCE_DUR = 8.0  # Veo generates 8-second clips


def probe_duration(path: str) -> float:
    """Return the duration of a media file in seconds via ffprobe."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True
    )
    return float(res.stdout.strip())


def trim_clip(src: str, dst: str, duration: float):
    """Trim a video clip from 0 to duration seconds (copy stream, no re-encode)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-t", f"{duration:.3f}",
        "-c", "copy",
        dst
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [ERROR] ffmpeg trim failed for {dst}:\n{res.stderr[-300:]}")
    return res.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Re-cut clips to match sentence audio durations.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--section-index", type=int, required=True)
    args = parser.parse_args()

    project_dir = DEFAULT_PROJECTS_DIR / args.project
    sec_idx = args.section_index

    # ── Paths ──────────────────────────────────────────────────────────────
    manifest_path = project_dir / "clips" / "clips_manifest.json"
    sentences_dir = project_dir / "audio" / f"section_{sec_idx}" / "sentences"
    raw_dir       = project_dir / "veo" / f"section_{sec_idx}" / "downloaded"
    if not (raw_dir.exists() and any(raw_dir.glob("*.mp4"))):
        raw_dir = project_dir / "veo" / "trimmed" / "downloaded"
    cut_dir       = project_dir / "veo" / f"section_{sec_idx}" / "cut"
    exports_dir   = project_dir / "exports"
    narration_mp3 = project_dir / "audio" / f"section_{sec_idx}" / "narration.mp3"
    output_video  = exports_dir / f"section_{sec_idx}_final.mp4"
    exports_dir.mkdir(parents=True, exist_ok=True)
    cut_dir.mkdir(parents=True, exist_ok=True)

    # ── Load manifest ──────────────────────────────────────────────────────
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    section = next((s for s in manifest["sections"] if int(s["section_index"]) == sec_idx), None)
    if not section:
        print(f"[Error] Section {sec_idx} not found in manifest.")
        sys.exit(1)

    sentence_clips = section["sentence_clips"]
    total_video_clips = section["total_clips"]

    # ── Step 1: Probe actual sentence audio durations ──────────────────────
    print(f"\n[Step 1] Probing sentence audio durations from {sentences_dir}")
    sent_audio_durs: dict[int, float] = {}
    for sent in sentence_clips:
        snum = int(sent["sentence_clip_number"])
        mp3_path = sentences_dir / f"sent_{snum:03d}.mp3"
        if not mp3_path.exists():
            print(f"  [WARN] Missing sentence audio: {mp3_path}")
            sent_audio_durs[snum] = sent["total_duration_seconds"]  # fallback to manifest
        else:
            dur = probe_duration(str(mp3_path))
            sent_audio_durs[snum] = dur
            print(f"  sent_{snum:03d}.mp3 = {dur:.3f}s")

    # ── Step 2: Compute new per-clip durations ─────────────────────────────
    print(f"\n[Step 2] Computing new clip durations")
    # new_durs[clip_number] = float seconds
    new_durs: dict[int, float] = {}

    for sent in sentence_clips:
        snum = int(sent["sentence_clip_number"])
        new_sent_dur = sent_audio_durs[snum]

        if not sent["split"]:
            cn = int(sent["video_clip_numbers"][0])
            new_durs[cn] = round(new_sent_dur, 3)
        else:
            parts = sent["part_durations"]
            orig_total = sum(p["duration_seconds"] for p in parts)
            for part in parts:
                cn = int(part["clip_number"])
                ratio = part["duration_seconds"] / orig_total
                new_durs[cn] = round(new_sent_dur * ratio, 3)

    # ── Step 3: Overflow redistribution & Headroom Allocation ─────────────
    print(f"\n[Step 3] Checking for overflow and allocating raw headroom...")

    # Probe actual source caps for all clips
    source_caps: dict[int, float] = {}
    manifest_clip_durs = {int(c["clip_number"]): float(c["duration_seconds"]) for c in section["clips"]}

    def get_veo_duration(dur: float) -> int:
        if dur <= 4.0:
            return 4
        elif dur <= 6.0:
            return 6
        else:
            return 8

    clip_nums = sorted(new_durs.keys())

    for cn in clip_nums:
        raw_path = raw_dir / f"clip_{cn:03d}_raw.mp4"
        if raw_path.exists():
            try:
                source_caps[cn] = probe_duration(str(raw_path))
            except Exception:
                orig_dur = manifest_clip_durs.get(cn, 8.0)
                source_caps[cn] = float(get_veo_duration(orig_dur))
        else:
            orig_dur = manifest_clip_durs.get(cn, 8.0)
            source_caps[cn] = float(get_veo_duration(orig_dur))

    # Collect overflow from clips that exceed source cap
    for i, cn in enumerate(clip_nums):
        cap = source_caps[cn]
        if new_durs[cn] > cap:
            overflow = round(new_durs[cn] - cap, 3)
            print(f"  clip_{cn:03d}: {new_durs[cn]:.3f}s exceeds source cap {cap:.3f}s -> overflow = {overflow:.3f}s")
            new_durs[cn] = cap

            # Search outward for nearest clips with headroom
            dist = 1
            while overflow > 0.001 and dist < len(clip_nums):
                candidates = []
                if i - dist >= 0:
                    candidates.append(clip_nums[i - dist])
                if i + dist < len(clip_nums):
                    candidates.append(clip_nums[i + dist])

                for target in candidates:
                    headroom = round(source_caps[target] - new_durs[target], 3)
                    if headroom > 0.001:
                        alloc = min(overflow, headroom)
                        new_durs[target] = round(new_durs[target] + alloc, 3)
                        overflow = round(overflow - alloc, 3)
                        print(f"  -> Allocated {alloc:.3f}s overflow to clip_{target:03d} (new dur: {new_durs[target]:.3f}s)")
                        if overflow <= 0.001:
                            break
                dist += 1

            if overflow > 0.001:
                print(f"  [WARN] {overflow:.3f}s unallocated overflow remaining after filling headroom!")

    # Check total video duration vs narration duration
    narr_dur = probe_duration(str(narration_mp3))
    total_new = sum(new_durs[cn] for cn in clip_nums)
    deficit = round(narr_dur - total_new, 3)

    if deficit > 0.001:
        print(f"\n  [Adjustment] Video deficit of {deficit:.3f}s detected relative to narration ({narr_dur:.3f}s). Filling from headroom...")
        for cn in clip_nums:
            if deficit <= 0.001:
                break
            headroom = round(source_caps[cn] - new_durs[cn], 3)
            if headroom > 0.001:
                alloc = min(deficit, headroom)
                new_durs[cn] = round(new_durs[cn] + alloc, 3)
                deficit = round(deficit - alloc, 3)
                print(f"  -> Added {alloc:.3f}s to clip_{cn:03d} (new dur: {new_durs[cn]:.3f}s)")

    # ── Print plan ─────────────────────────────────────────────────────────
    print(f"\n{'Clip':<8} {'New Duration':>14} {'Source Cap':>12}")
    print("-" * 38)
    total_new = 0.0
    for cn in clip_nums:
        print(f"  clip_{cn:03d}   {new_durs[cn]:>8.3f}s   {source_caps[cn]:>8.3f}s")
        total_new += new_durs[cn]
    print(f"\n  Total new video duration: {total_new:.3f}s")
    print(f"  Narration duration:       {narr_dur:.3f}s")

    # ── Step 4: Re-trim clips ──────────────────────────────────────────────
    print(f"\n[Step 4] Re-cutting clips from {raw_dir}")
    cut_clip_paths = []

    for cn in clip_nums:
        raw_path = raw_dir / f"clip_{cn:03d}_raw.mp4"
        cut_path = cut_dir / f"clip_{cn:03d}.mp4"
        new_dur  = new_durs[cn]

        if not raw_path.exists():
            print(f"  [ERROR] Raw source not found: {raw_path}")
            sys.exit(1)

        print(f"  clip_{cn:03d}: trimming to {new_dur:.3f}s ...", end=" ")
        ok = trim_clip(str(raw_path), str(cut_path), new_dur)
        print("OK" if ok else "FAILED")
        cut_clip_paths.append(str(cut_path))

    # ── Step 5: Concat video clips ─────────────────────────────────────────
    print(f"\n[Step 5] Concatenating {len(cut_clip_paths)} clips")
    concat_list = cut_dir / "concat_list.txt"
    lines = [f"file '{p.replace(chr(92), '/')}'" for p in cut_clip_paths]
    concat_list.write_text("\n".join(lines), encoding="utf-8")

    joined_video = exports_dir / f"section_{sec_idx}_joined.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(joined_video)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Video concat failed:\n{res.stderr[-400:]}")
        sys.exit(1)
    joined_dur = probe_duration(str(joined_video))
    print(f"  Joined video: {joined_dur:.3f}s -> {joined_video}")

    # ── Step 6: Mux with narration audio and apply transitions ─────────
    print(f"\n[Step 6] Muxing with narration audio and applying transitions -> {output_video}")
    pad_needed = round(narr_dur - joined_dur, 3)

    # Determine visual duration for the fade-out start time
    target_video_dur = narr_dur if pad_needed > 0.05 else joined_dur

    if pad_needed > 0.05:
        print(f"  Padding final frame by {pad_needed:.3f}s to match narration length perfectly...")
        vf = f"tpad=stop_mode=clone:stop_duration={pad_needed:.3f},fade=t=in:st=0:d=0.75,fade=t=out:st={narr_dur - 0.75:.3f}:d=0.75"
    else:
        vf = f"fade=t=in:st=0:d=0.75,fade=t=out:st={joined_dur - 0.75:.3f}:d=0.75"

    af = f"afade=t=in:st=0:d=0.75,afade=t=out:st={narr_dur - 0.75:.3f}:d=0.75"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(joined_video),
        "-i", str(narration_mp3),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(output_video)
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Mux failed:\n{res.stderr[-400:]}")
        sys.exit(1)

    final_dur = probe_duration(str(output_video))
    size_mb = os.path.getsize(str(output_video)) / (1024 * 1024)
    print(f"\n[SUCCESS] {output_video}")
    print(f"  Duration: {final_dur:.3f}s | Size: {size_mb:.2f} MB")

    # Cleanup temp joined video
    joined_video.unlink(missing_ok=True)
    print("[Done]")


if __name__ == "__main__":
    main()
