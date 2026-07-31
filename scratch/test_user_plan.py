import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_tts

# Read sample script
script_file = ROOT / "video_projects" / "the-entire-history-of-jerusalem" / "source" / "sample_clipping_script.txt"
raw_script = script_file.read_text(encoding="utf-8")

voice = "en-US-AndrewNeural"
rate = "-10%"
pitch = "-15Hz"
MAX_SECONDS = 8.0
MIN_SECONDS = 3.0


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


def parse_raw_sentences(text: str) -> list[str]:
    """Split text by sentence punctuation (. ! ?)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return sentences


def consolidate_short_sentences(sentences: list[str]) -> list[str]:
    """Pass 1: Combine micro-sentences (< ~8 words) with the next sentence so they form sensible thoughts."""
    consolidated = []
    buffer = ""
    for s in sentences:
        if buffer:
            buffer = buffer + " " + s
        else:
            buffer = s
        
        # Word count check: if buffer has at least 8 words or end of list, keep as unit
        words = buffer.split()
        if len(words) >= 8:
            consolidated.append(buffer)
            buffer = ""
            
    if buffer:
        if consolidated:
            consolidated[-1] = consolidated[-1] + " " + buffer
        else:
            consolidated.append(buffer)
            
    return consolidated


def split_long_sentence_text(sentence: str) -> tuple[str, str]:
    """Find the furthest comma or clause punctuation in the sentence to split into Part A and Part B."""
    # Look for commas, em-dashes, or semicolons
    matches = list(re.finditer(r"([—;,])", sentence))
    if not matches:
        # Fallback to middle word boundary
        words = sentence.split()
        mid = len(words) // 2
        return " ".join(words[:mid]), " ".join(words[mid:])
    
    # Pick mid candidate
    mid_idx = len(matches) // 2
    match = matches[mid_idx]
    split_pos = match.end()
    part_a = sentence[:split_pos].strip()
    part_b = sentence[split_pos:].strip()
    return part_a, part_b


async def process_script():
    print("=== Step 1: Parsing & Consolidating Sentences ===")
    raw_sentences = parse_raw_sentences(raw_script)
    print(f"Raw Sentences Count: {len(raw_sentences)}")
    
    consolidated_sentences = consolidate_short_sentences(raw_sentences)
    print(f"Consolidated Sentences Count: {len(consolidated_sentences)}\n")
    
    final_clips = []
    clip_counter = 1
    
    print("=== Step 2 & 3: Measuring TTS & Splitting Sentences > 8.0s ===")
    for s_idx, sentence in enumerate(consolidated_sentences, start=1):
        parent_id = f"sent_{s_idx:03d}"
        duration = await measure_tts_duration(sentence)
        
        if duration <= MAX_SECONDS:
            # Standalone sentence clip
            final_clips.append({
                "clip_number": clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_standalone",
                "text": sentence,
                "duration_seconds": round(duration, 3),
                "status": "fits_single_clip"
            })
            print(f"Clip {clip_counter:02d} [{parent_id}_standalone] ({duration:.2f}s): \"{sentence}\"")
            clip_counter += 1
        else:
            # Over 8 seconds -> Split into Part A and Part B
            print(f"\n[OVER 8s DETECTED] ({duration:.2f}s) -> Splitting Sentence {s_idx}: \"{sentence}\"")
            part_a_text, part_b_text = split_long_sentence_text(sentence)
            
            dur_a = await measure_tts_duration(part_a_text)
            dur_b = await measure_tts_duration(part_b_text)
            
            final_clips.append({
                "clip_number": clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_part_a",
                "text": part_a_text,
                "duration_seconds": round(dur_a, 3),
                "status": "split_part_a"
            })
            print(f"  -> Clip {clip_counter:02d} [{parent_id}_part_a] ({dur_a:.2f}s): \"{part_a_text}\"")
            clip_counter += 1
            
            final_clips.append({
                "clip_number": clip_counter,
                "parent_sentence_id": parent_id,
                "relation_id": f"{parent_id}_part_b",
                "text": part_b_text,
                "duration_seconds": round(dur_b, 3),
                "status": "split_part_b"
            })
            print(f"  -> Clip {clip_counter:02d} [{parent_id}_part_b] ({dur_b:.2f}s): \"{part_b_text}\"\n")
            clip_counter += 1
            
    print("\n=======================================================")
    print(f"TEST RUN COMPLETE: Total Final Clips Generated = {len(final_clips)}")
    print("=======================================================\n")
    
    # Print sample JSON manifest snippet
    print("Sample Manifest Output Snippet:")
    print(json.dumps(final_clips[:4], indent=2))

if __name__ == "__main__":
    asyncio.run(process_script())
