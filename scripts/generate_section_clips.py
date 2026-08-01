#!/usr/bin/env python3
"""Split narrated sections into sentence-aware, optimal-clause clip files.

This script reads raw script files from a video project, breaks them into
sections, applies a 4-pass sentence-based clipping engine using Edge TTS, and
writes formatted `*_clips.txt` files and `clips_manifest.json`.

Features:
- Sentence boundary integrity
- Micro-sentence consolidation (< 8 words / < 3s)
- Independent per-clip Edge TTS duration verification
- Clause splitting (> 8s) at furthest fitting punctuation
- Relation ID tracking (`parent_sentence_id`, `relation_id`)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

try:
    import edge_tts
except Exception as exc:  # pragma: no cover - import error is surfaced at runtime
    edge_tts = None
    EDGE_TTS_IMPORT_ERROR = exc
else:
    EDGE_TTS_IMPORT_ERROR = None

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "video_projects"
DEFAULT_SOURCE_FILE = "source/sections_raw.txt"
DEFAULT_SOURCE_MANIFEST = "source/scripts_manifest.json"
DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AndrewNeural")
DEFAULT_RATE = os.getenv("EDGE_TTS_RATE", "-10%")
DEFAULT_PITCH = os.getenv("EDGE_TTS_PITCH", "-15Hz")
DEFAULT_CLIP_SECONDS = float(os.getenv("EDGE_TTS_CLIP_SECONDS", "8.0"))
MIN_WORDS_CONSOLIDATE = 8

DEFAULT_HEADING_PATTERNS = (
    re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$"),
    re.compile(r"^(?:section|chapter|part)\s+(?P<num>\d+)(?:\s*[:.\-]\s*(?P<title>.+))?$", re.IGNORECASE),
)


@dataclass
class Section:
    name: str
    title: str
    text: str
    index: int
    style: str | None = None
    script_name: str | None = None
    tab_title: str | None = None
    tab_id: str | None = None
    source_file: str | None = None


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "section"


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    candidate = Path(project).expanduser()
    if candidate.exists():
        return candidate.resolve()
    return (base_dir / project).resolve()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_sections(raw_text: str) -> list[Section]:
    lines = raw_text.splitlines()
    sections: list[Section] = []
    current_title = ""
    current_lines: list[str] = []
    current_index = 0

    def flush_section() -> None:
        nonlocal current_title, current_lines, current_index
        text = "\n".join(current_lines).strip()
        if not text:
            current_title = ""
            current_lines = []
            return
        current_index += 1
        title = current_title.strip() or f"Section {current_index}"
        sections.append(Section(
            name=slugify(title),
            title=title,
            text=text,
            index=current_index,
        ))
        current_title = ""
        current_lines = []

    for line in lines:
        stripped = line.strip()
        matched_heading = None
        for pattern in DEFAULT_HEADING_PATTERNS:
            match = pattern.match(stripped)
            if match:
                matched_heading = match
                break
        if matched_heading:
            flush_section()
            title = matched_heading.groupdict().get("title") or stripped.lstrip("#").strip()
            current_title = title or f"Section {current_index + 1}"
            continue
        current_lines.append(line)

    flush_section()

    if sections:
        return sections

    cleaned = raw_text.strip()
    if not cleaned:
        return []
    return [Section(name="section-001", title="Section 1", text=cleaned, index=1)]


def parse_raw_sentences(text: str) -> list[str]:
    """Split section text into sentences (. ! ?)."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def consolidate_short_sentences(sentences: list[str], min_words: int = MIN_WORDS_CONSOLIDATE) -> list[str]:
    """Pass 1: Combine micro-sentences (< min_words) with the next sentence so they form sensible thoughts."""
    consolidated = []
    buffer = ""
    for s in sentences:
        if buffer:
            buffer = buffer + " " + s
        else:
            buffer = s
        
        words = buffer.split()
        if len(words) >= min_words:
            consolidated.append(buffer)
            buffer = ""
            
    if buffer:
        if consolidated:
            consolidated[-1] = consolidated[-1] + " " + buffer
        else:
            consolidated.append(buffer)
            
    return consolidated


async def measure_tts_duration(text: str, voice: str, rate: str, pitch: str) -> float:
    """Pass 2: Send a specific text chunk independently to Edge TTS and return exact spoken duration."""
    if edge_tts is None:
        raise RuntimeError(
            "edge-tts is not available. Install it with `uv pip install edge-tts` "
            f"(original import error: {EDGE_TTS_IMPORT_ERROR})"
        )

    import asyncio
    import random

    async def _fetch():
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
        words = []
        async for chunk in comm.stream():
            if chunk.get("type") == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append((start, start + dur))
        if not words:
            return 0.0
        return words[-1][1] - words[0][0]

    max_retries = 5
    backoff = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            return await asyncio.wait_for(_fetch(), timeout=15.0)
        except Exception as exc:
            if attempt == max_retries:
                raise
            sleep_time = (backoff ** attempt) + random.uniform(0.5, 1.5)
            print(f"[WARN] Edge TTS error: {exc}. Retrying in {sleep_time:.2f}s (Attempt {attempt}/{max_retries})...")
            await asyncio.sleep(sleep_time)


def split_long_sentence_text(sentence: str) -> tuple[str, str]:
    """Pass 3: Find the furthest fitting clause punctuation (—, ;, ,) to split a long sentence (>8s)."""
    matches = list(re.finditer(r"([—;,])", sentence))
    if not matches:
        words = sentence.split()
        mid = len(words) // 2
        return " ".join(words[:mid]), " ".join(words[mid:])
    
    mid_idx = len(matches) // 2
    match = matches[mid_idx]
    split_pos = match.end()
    part_a = sentence[:split_pos].strip()
    part_b = sentence[split_pos:].strip()
    return part_a, part_b


def load_source_scripts(project_dir: Path, source_file: Path) -> list[Section]:
    manifest_path = project_dir / DEFAULT_SOURCE_MANIFEST
    if not manifest_path.exists():
        raw_text = read_text(source_file)
        return parse_sections(raw_text)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections: list[Section] = []
    for item in payload.get("scripts", []) or []:
        relative = Path(item["source_file"])
        script_path = project_dir / relative
        raw_text = read_text(script_path)
        parsed = parse_sections(raw_text)
        if not parsed:
            continue
        for section in parsed:
            section.style = item.get("style")
            section.script_name = item.get("script_name")
            section.tab_title = item.get("tab_title")
            section.tab_id = item.get("tab_id")
            section.source_file = str(relative)
            sections.append(section)
    return sections or parse_sections(read_text(source_file))


def clip_file_name(section: Section) -> str:
    if section.style and section.script_name:
        style_slug = slugify(section.style)
        script_slug = slugify(section.script_name)
        section_slug = slugify(section.name)
        return f"{section.index:03d}-{style_slug}-{script_slug}-{section_slug}_clips.txt"
    return f"{section.index:03d}-{section.name}_clips.txt"


def clip_file_header(section: Section) -> list[str]:
    lines: list[str] = []
    if section.style:
        lines.append(f"STYLE: {section.style}")
    if section.tab_title:
        lines.append(f"TAB TITLE: {section.tab_title}")
    if section.script_name:
        lines.append(f"SCRIPT NAME: {section.script_name}")
    if section.source_file:
        lines.append(f"SOURCE FILE: {section.source_file}")
    if lines:
        lines.append("")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split narrated sections into sentence-aware clip files")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Base directory for video projects")
    parser.add_argument("--source-file", default=DEFAULT_SOURCE_FILE, help="Source text file relative to the project dir, or an absolute path")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Edge TTS voice name")
    parser.add_argument("--rate", default=DEFAULT_RATE, help="Speech rate e.g. -10%%")
    parser.add_argument("--pitch", default=DEFAULT_PITCH, help="Pitch adjustment e.g. -15Hz")
    parser.add_argument("--clip-seconds", type=float, default=DEFAULT_CLIP_SECONDS, help="Maximum clip duration limit")
    parser.add_argument("--output-dir", default="clips", help="Output folder relative to the project dir")
    parser.add_argument("--manifest-name", default="clips_manifest.json", help="Manifest filename written into the output dir")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print manifest without writing files")
    return parser


async def process_section(
    section: Section,
    global_clip_start: int,
    output_dir: Path,
    voice: str,
    rate: str,
    pitch: str,
    clip_seconds: float,
    dry_run: bool
) -> tuple[dict[str, object], int]:
    raw_sentences = parse_raw_sentences(section.text)
    consolidated = consolidate_short_sentences(raw_sentences)
    output_path = output_dir / clip_file_name(section)

    sec_clips: list[dict[str, object]] = []
    # sentence_clips: one entry per original sentence (split parts are merged back)
    # This is the list sent to Edge TTS for audio generation.
    sentence_clips: list[dict[str, object]] = []
    clip_content_lines: list[str] = []

    sec_clip_counter = 1
    current_global_clip = global_clip_start
    sentence_clip_counter = 1

    for s_idx, sentence in enumerate(consolidated, start=1):
        parent_id = f"sec_{section.index:02d}_sent_{s_idx:03d}"

        if dry_run:
            duration = min(len(sentence.split()) * 0.35, 7.5)
        else:
            duration = await measure_tts_duration(sentence, voice, rate, pitch)

        if duration <= clip_seconds:
            words_count = len(sentence.split())
            clip_entry = {
                "global_clip_number": current_global_clip,
                "clip_number": sec_clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_standalone",
                "text": sentence,
                "duration_seconds": round(duration, 3),
                "word_count": words_count,
                "status": "fits_single_clip",
            }
            sec_clips.append(clip_entry)
            clip_content_lines.append(f"Clip {sec_clip_counter:02d} ({words_count} words, {duration:.2f}s):")
            clip_content_lines.append(sentence)
            clip_content_lines.append("")

            # Sentence clip: single-clip sentence maps 1:1
            sentence_clips.append({
                "sentence_clip_number": sentence_clip_counter,
                "parent_sentence_id": parent_id,
                "text": sentence,
                "total_duration_seconds": round(duration, 3),
                "word_count": words_count,
                "video_clip_numbers": [sec_clip_counter],
                "split": False,
            })
            sentence_clip_counter += 1

            current_global_clip += 1
            sec_clip_counter += 1
        else:
            part_a_text, part_b_text = split_long_sentence_text(sentence)
            if dry_run:
                dur_a = min(len(part_a_text.split()) * 0.35, 6.0)
                dur_b = min(len(part_b_text.split()) * 0.35, 5.0)
            else:
                dur_a = await measure_tts_duration(part_a_text, voice, rate, pitch)
                dur_b = await measure_tts_duration(part_b_text, voice, rate, pitch)

            # Part A
            words_a = len(part_a_text.split())
            clip_a = {
                "global_clip_number": current_global_clip,
                "clip_number": sec_clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_part_a",
                "text": part_a_text,
                "duration_seconds": round(dur_a, 3),
                "word_count": words_a,
                "status": "split_part_a",
            }
            sec_clips.append(clip_a)
            clip_content_lines.append(f"Clip {sec_clip_counter:02d} ({words_a} words, {dur_a:.2f}s):")
            clip_content_lines.append(part_a_text)
            clip_content_lines.append("")
            clip_a_number = sec_clip_counter
            current_global_clip += 1
            sec_clip_counter += 1

            # Part B
            words_b = len(part_b_text.split())
            clip_b = {
                "global_clip_number": current_global_clip,
                "clip_number": sec_clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_part_b",
                "text": part_b_text,
                "duration_seconds": round(dur_b, 3),
                "word_count": words_b,
                "status": "split_part_b",
            }
            sec_clips.append(clip_b)
            clip_content_lines.append(f"Clip {sec_clip_counter:02d} ({words_b} words, {dur_b:.2f}s):")
            clip_content_lines.append(part_b_text)
            clip_content_lines.append("")

            # Sentence clip: split sentence spans two video clips — ONE entry with full joined text
            full_sentence_text = f"{part_a_text} {part_b_text}"
            sentence_clips.append({
                "sentence_clip_number": sentence_clip_counter,
                "parent_sentence_id": parent_id,
                "text": full_sentence_text,
                "total_duration_seconds": round(dur_a + dur_b, 3),
                "word_count": words_a + words_b,
                "video_clip_numbers": [clip_a_number, sec_clip_counter],
                "part_durations": [
                    {"clip_number": clip_a_number, "duration_seconds": round(dur_a, 3)},
                    {"clip_number": sec_clip_counter, "duration_seconds": round(dur_b, 3)},
                ],
                "split": True,
            })
            sentence_clip_counter += 1

            current_global_clip += 1
            sec_clip_counter += 1

    if not dry_run:
        total_words = sum(c["word_count"] for c in sec_clips)
        file_lines = clip_file_header(section) + [
            f"Total words: {total_words}",
            f"Total clips: {len(sec_clips)}",
            f"Clip ceiling: {clip_seconds}s",
            "",
        ] + clip_content_lines
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(file_lines).rstrip() + "\n", encoding="utf-8")

        # Write sentence clips file alongside the main clips file
        sentence_clips_path = output_path.parent / output_path.name.replace("_clips.txt", "_sentence_clips.json")
        sentence_clips_path.write_text(json.dumps(sentence_clips, indent=2) + "\n", encoding="utf-8")

    manifest_section = {
        "section_index": section.index,
        "section_title": section.title,
        "section_name": section.name,
        "style": section.style,
        "script_name": section.script_name,
        "tab_title": section.tab_title,
        "tab_id": section.tab_id,
        "source_file": section.source_file,
        "total_clips": len(sec_clips),
        "total_sentence_clips": len(sentence_clips),
        "output_file": str(output_path),
        "clips": sec_clips,
        "sentence_clips": sentence_clips,
    }

    return manifest_section, current_global_clip


async def main_async() -> int:
    args = build_parser().parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory does not exist: {project_dir}")

    source_file = Path(args.source_file).expanduser()
    if not source_file.is_absolute():
        source_file = project_dir / source_file
    if not source_file.exists():
        raise FileNotFoundError(f"Source file not found: {source_file}")

    sections = load_source_scripts(project_dir, source_file)
    if not sections:
        raise SystemExit(f"No sections found in {source_file}")

    output_dir = project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Run ALL sections concurrently ---
    # Pass global_clip_start=1 for every section; we renumber after all finish.
    print(f"Processing {len(sections)} sections concurrently...")
    tasks = [
        process_section(
            section,
            1,          # placeholder; reassigned below
            output_dir,
            args.voice,
            args.rate,
            args.pitch,
            args.clip_seconds,
            args.dry_run,
        )
        for section in sections
    ]
    results = await asyncio.gather(*tasks)

    # Sort results by section index and reassign global_clip_number sequentially
    global_clip_counter = 1
    manifest_sections: list[dict[str, object]] = []
    for section, (sec_manifest, _) in sorted(
        zip(sections, results), key=lambda pair: pair[0].index
    ):
        for clip in sec_manifest["clips"]:
            clip["global_clip_number"] = global_clip_counter
            global_clip_counter += 1
        label = section.script_name or section.title
        print(f"  section {section.index:03d}: {label} — {sec_manifest['total_clips']} clips")
        if not args.dry_run:
            print(f"    wrote {sec_manifest['output_file']}")
        manifest_sections.append(sec_manifest)

    master_manifest = {
        "project_dir": str(project_dir),
        "source_file": str(source_file),
        "source_manifest": str(project_dir / DEFAULT_SOURCE_MANIFEST) if (project_dir / DEFAULT_SOURCE_MANIFEST).exists() else None,
        "voice": args.voice,
        "rate": args.rate,
        "pitch": args.pitch,
        "clip_seconds": args.clip_seconds,
        "section_count": len(sections),
        "total_global_clips": global_clip_counter - 1,
        "sections": manifest_sections,
        "dry_run": args.dry_run,
    }

    manifest_path = output_dir / args.manifest_name
    if not args.dry_run:
        manifest_path.write_text(json.dumps(master_manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Master manifest written to {manifest_path}")
    else:
        print(json.dumps(master_manifest, indent=2))

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
