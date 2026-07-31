import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_tts

project_dir = ROOT / "video_projects" / "jerusalem-full-test"
raw_source_file = project_dir / "source" / "sections_raw.txt"
clips_dir = project_dir / "clips"
clips_dir.mkdir(parents=True, exist_ok=True)

voice = "en-US-AndrewNeural"
rate = "-10%"
pitch = "-15Hz"
MAX_SECONDS = 8.0
MIN_WORDS_CONSOLIDATE = 8


async def measure_tts_duration(text: str) -> float:
    """Send a specific text chunk independently to Edge TTS and return exact spoken duration."""
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


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "section"


def parse_sections(raw_text: str) -> list[dict[str, str]]:
    sections = []
    lines = raw_text.splitlines()
    curr_title = ""
    curr_lines = []
    sec_idx = 1
    
    for line in lines:
        if line.strip().startswith("# "):
            if curr_lines:
                text = "\n".join(curr_lines).strip()
                if text:
                    sections.append({
                        "index": sec_idx,
                        "title": curr_title or f"Section {sec_idx}",
                        "name": slugify(curr_title or f"Section {sec_idx}"),
                        "text": text
                    })
                    sec_idx += 1
                curr_lines = []
            curr_title = line.strip().lstrip("# ").strip()
        else:
            curr_lines.append(line)
            
    if curr_lines:
        text = "\n".join(curr_lines).strip()
        if text:
            sections.append({
                "index": sec_idx,
                "title": curr_title or f"Section {sec_idx}",
                "name": slugify(curr_title or f"Section {sec_idx}"),
                "text": text
            })
            
    return sections


def parse_raw_sentences(text: str) -> list[str]:
    """Split section text into sentences (. ! ?)."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def consolidate_short_sentences(sentences: list[str]) -> list[str]:
    """Combine short micro-sentences until they reach a sensible word threshold."""
    consolidated = []
    buffer = ""
    for s in sentences:
        if buffer:
            buffer = buffer + " " + s
        else:
            buffer = s
        
        words = buffer.split()
        if len(words) >= MIN_WORDS_CONSOLIDATE:
            consolidated.append(buffer)
            buffer = ""
            
    if buffer:
        if consolidated:
            consolidated[-1] = consolidated[-1] + " " + buffer
        else:
            consolidated.append(buffer)
            
    return consolidated


def split_long_sentence_text(sentence: str) -> tuple[str, str]:
    """Find the furthest fitting clause punctuation to split a long sentence (>8s)."""
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


async def process_project():
    raw_text = raw_source_file.read_text(encoding="utf-8")
    sections = parse_sections(raw_text)
    print(f"Loaded {len(sections)} sections from {raw_source_file.name}\n")
    
    master_manifest = {
        "project_slug": "jerusalem-full-test",
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "clip_seconds": MAX_SECONDS,
        "section_count": len(sections),
        "sections": []
    }
    
    global_clip_num = 1
    
    for sec in sections:
        sec_idx = sec["index"]
        sec_title = sec["title"]
        print(f"=== Processing Section {sec_idx:02d}: {sec_title} ===")
        
        raw_sentences = parse_raw_sentences(sec["text"])
        consolidated = consolidate_short_sentences(raw_sentences)
        
        sec_clips = []
        sec_clip_lines = [
            f"SECTION: {sec_title}",
            f"Total sentences: {len(raw_sentences)}",
            f"Consolidated units: {len(consolidated)}",
            ""
        ]
        
        sec_clip_counter = 1
        for s_idx, sentence in enumerate(consolidated, start=1):
            parent_id = f"sec_{sec_idx:02d}_sent_{s_idx:03d}"
            duration = await measure_tts_duration(sentence)
            
            if duration <= MAX_SECONDS:
                clip_entry = {
                    "global_clip_number": global_clip_num,
                    "section_clip_number": sec_clip_counter,
                    "parent_sentence_id": parent_id,
                    "relation_id": f"{parent_id}_standalone",
                    "text": sentence,
                    "duration_seconds": round(duration, 3),
                    "word_count": len(sentence.split()),
                    "status": "fits_single_clip"
                }
                sec_clips.append(clip_entry)
                sec_clip_lines.append(f"Clip {sec_clip_counter:02d} [{parent_id}_standalone] ({len(sentence.split())} words, {duration:.2f}s):")
                sec_clip_lines.append(sentence)
                sec_clip_lines.append("")
                global_clip_num += 1
                sec_clip_counter += 1
            else:
                part_a_text, part_b_text = split_long_sentence_text(sentence)
                dur_a = await measure_tts_duration(part_a_text)
                dur_b = await measure_tts_duration(part_b_text)
                
                # Part A
                clip_a = {
                    "global_clip_number": global_clip_num,
                    "section_clip_number": sec_clip_counter,
                    "parent_sentence_id": parent_id,
                    "relation_id": f"{parent_id}_part_a",
                    "text": part_a_text,
                    "duration_seconds": round(dur_a, 3),
                    "word_count": len(part_a_text.split()),
                    "status": "split_part_a"
                }
                sec_clips.append(clip_a)
                sec_clip_lines.append(f"Clip {sec_clip_counter:02d} [{parent_id}_part_a] ({len(part_a_text.split())} words, {dur_a:.2f}s):")
                sec_clip_lines.append(part_a_text)
                sec_clip_lines.append("")
                global_clip_num += 1
                sec_clip_counter += 1
                
                # Part B
                clip_b = {
                    "global_clip_number": global_clip_num,
                    "section_clip_number": sec_clip_counter,
                    "parent_sentence_id": parent_id,
                    "relation_id": f"{parent_id}_part_b",
                    "text": part_b_text,
                    "duration_seconds": round(dur_b, 3),
                    "word_count": len(part_b_text.split()),
                    "status": "split_part_b"
                }
                sec_clips.append(clip_b)
                sec_clip_lines.append(f"Clip {sec_clip_counter:02d} [{parent_id}_part_b] ({len(part_b_text.split())} words, {dur_b:.2f}s):")
                sec_clip_lines.append(part_b_text)
                sec_clip_lines.append("")
                global_clip_num += 1
                sec_clip_counter += 1
                
        # Write section clips txt file
        sec_clips_file = clips_dir / f"{sec_idx:03d}-{sec['name']}_clips.txt"
        sec_clips_file.write_text("\n".join(sec_clip_lines).rstrip() + "\n", encoding="utf-8")
        print(f"  -> Wrote {sec_clips_file.name} ({len(sec_clips)} clips)")
        
        master_manifest["sections"].append({
            "section_index": sec_idx,
            "section_title": sec_title,
            "section_name": sec['name'],
            "total_clips": len(sec_clips),
            "output_file": str(sec_clips_file),
            "clips": sec_clips
        })

    # Write master manifest json
    manifest_file = clips_dir / "clips_manifest.json"
    manifest_file.write_text(json.dumps(master_manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nMaster manifest successfully generated at: {manifest_file}")
    print(f"Total Global Clips Across Project: {global_clip_num - 1}")

if __name__ == "__main__":
    asyncio.run(process_project())
