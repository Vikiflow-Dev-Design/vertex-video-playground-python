#!/usr/bin/env python3
"""
generate_project_audio.py

Generates per-SENTENCE narration audio using Edge-TTS (en-US-AndrewNeural).

OUTPUT STRUCTURE (sentences only):
------------------------------------
  audio/
    section_1/
      sentences/
        sent_001.mp3   <- full sentence 1 (natural TTS)
        sent_002.mp3
        ...
      narration.mp3    <- all sentences concatenated in order (preview master)
    section_2/
      sentences/ ...
      narration.mp3

IMPORTANT — DESIGN DECISION:
  - Only sentence-level audio files are produced. No per-clip slicing,
    no padded intermediate files. The sentences/ folder IS the audio output.
  - For split sentences (spanning two video clips), the full joined sentence
    is synthesized as one file. Slicing to individual clip durations is handled
    downstream at stitching time, not here.
  - Skip-existing logic: already-generated sentence files are never re-synthesized.

Usage:
    python scripts/generate_project_audio.py --project <project-name>
    python scripts/generate_project_audio.py --project <project-name> --section-index 1
"""

import os
import sys
import json
import asyncio
import argparse
import subprocess
from pathlib import Path

import edge_tts

SCRIPT_DIR = Path(__file__).resolve().parent
PLAYGROUND_DIR = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PLAYGROUND_DIR / "video_projects"


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

async def synthesize_sentence(text: str, voice: str, rate: str, pitch: str, output_path: str):
    """Synthesizes text to MP3 using edge-tts."""
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)


def concatenate_audio_files(input_paths: list[str], output_path: str) -> bool:
    """Concatenates a list of MP3 files using ffmpeg concat demuxer."""
    concat_txt = output_path + ".concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in input_paths:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_txt,
        "-c", "copy",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        os.remove(concat_txt)
    except OSError:
        pass
    return res.returncode == 0 and os.path.exists(output_path)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

async def process_section_audio(
    section_index: int,
    sentence_clips: list[dict],
    voice: str,
    rate: str,
    pitch: str,
    section_dir: Path,
) -> list[str]:
    """
    Synthesizes one MP3 per sentence into section_dir/sentences/.
    Returns ordered list of sentence MP3 paths.
    """
    sentences_dir = section_dir / "sentences"
    sentences_dir.mkdir(parents=True, exist_ok=True)

    total = len(sentence_clips)
    generated_paths: list[str] = []

    for idx, sent in enumerate(sentence_clips, start=1):
        sent_num = int(sent["sentence_clip_number"])
        text = sent["text"].strip()
        is_split = bool(sent.get("split", False))
        video_clips = sent["video_clip_numbers"]

        out_path = str(sentences_dir / f"sent_{sent_num:03d}.mp3")

        label = f"split -> {video_clips}" if is_split else f"clip {video_clips[0]}"
        print(f"[{idx:03d}/{total}] Sentence #{sent_num:03d} ({label}) -- {len(text.split())} words")

        if os.path.exists(out_path):
            print(f"  [SKIP] Already exists.")
        else:
            try:
                await synthesize_sentence(text, voice, rate, pitch, out_path)
                print(f"  [TTS] Synthesized -> {Path(out_path).name}")
            except Exception as e:
                print(f"  [ERROR] TTS failed: {e}")
                continue

        generated_paths.append(out_path)

    return generated_paths


def build_section_master(sentence_paths: list[str], section_dir: Path, section_index: int):
    """Concatenates all sentence MP3s into a preview narration.mp3 for the section."""
    master_path = str(section_dir / "narration.mp3")
    if concatenate_audio_files(sentence_paths, master_path):
        size_mb = os.path.getsize(master_path) / (1024 * 1024)
        print(f"\n[SUCCESS] Section {section_index} narration.mp3 ({size_mb:.2f} MB) -> {master_path}")
    else:
        print(f"\n[ERROR] Failed to build narration.mp3 for section {section_index}.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Synthesize per-sentence narration audio into audio/section_N/sentences/."
    )
    parser.add_argument("--project", required=True, help="Project name under video_projects/")
    parser.add_argument("--section-index", type=int, default=None,
                        help="Process only this section (1-based). Omit to process all.")
    parser.add_argument("--voice", default="en-US-AndrewNeural")
    parser.add_argument("--rate", default="-10%")
    parser.add_argument("--pitch", default="-15Hz")
    args = parser.parse_args()

    project_dir = DEFAULT_PROJECTS_DIR / args.project
    manifest_path = project_dir / "clips" / "clips_manifest.json"

    if not manifest_path.exists():
        print(f"[Error] Manifest not found: {manifest_path}")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = manifest.get("sections", [])

    if args.section_index is not None:
        sections = [s for s in sections if int(s.get("section_index", -1)) == args.section_index]
        if not sections:
            print(f"[Error] Section {args.section_index} not found in manifest.")
            sys.exit(1)

    audio_dir = project_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Info] Project: {args.project}")
    print(f"[Info] Sections to process: {len(sections)}")
    print(f"[Info] Voice: {args.voice} | Rate: {args.rate} | Pitch: {args.pitch}\n")

    for section in sections:
        sec_idx = int(section["section_index"])
        sentence_clips = section.get("sentence_clips", [])

        if not sentence_clips:
            print(f"[WARN] Section {sec_idx} has no sentence_clips. Re-run generate_section_clips.py first.")
            continue

        section_dir = audio_dir / f"section_{sec_idx}"
        section_dir.mkdir(parents=True, exist_ok=True)

        print(f"======================================")
        print(f"Section {sec_idx}: {section.get('section_title', '')} ({len(sentence_clips)} sentences)")
        print(f"Output: {section_dir}")
        print(f"======================================")

        sentence_paths = asyncio.run(process_section_audio(
            section_index=sec_idx,
            sentence_clips=sentence_clips,
            voice=args.voice,
            rate=args.rate,
            pitch=args.pitch,
            section_dir=section_dir,
        ))

        if sentence_paths:
            build_section_master(sentence_paths, section_dir, sec_idx)

    print("\n[Done] Audio generation complete.")


if __name__ == "__main__":
    main()
