#!/usr/bin/env python3
"""
generate_teaser_intro.py

1. Reads clips/clips_manifest.json for a project.
2. Uses Gemini (via Vertex AI) to select the top X most visually striking or curiosity-inducing scene clips for each section.
3. Caches selection to exports/teaser_selections.json to prevent redundant API calls.
4. Extracts 3-second sub-clips without audio (-an) from the local trimmed clip files.
5. Saves individual sub-clips to exports/teaser_clips/.
6. Concatenates all sub-clips into exports/teaser_highlights.mp4.
"""

import os
import sys
import json
import re
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PROJECT_ROOT / "video_projects"
KEY_PATH = PROJECT_ROOT / "gcp-key.json"

# Load environment variables
load_dotenv()
if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and KEY_PATH.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(KEY_PATH)

def get_video_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filepath
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        try:
            return float(res.stdout.strip())
        except ValueError:
            pass
    return 0.0

def generate_teaser_selections(gcp_project: str, gcp_location: str, gemini_model: str, section_index: int, clips_list: list, clips_per_section: int) -> list[int]:
    from google import genai

    # Create Vertex AI client
    client = genai.Client(vertexai=True, project=gcp_project, location=gcp_location)

    system_instruction = (
        "You are an expert video editor and art director for historical documentary videos.\n"
        "You will receive a list of scenes (comprising a clip_number and the descriptive script text) from one section of the documentary.\n"
        f"Your task is to select exactly {clips_per_section} scenes that would make the most visually striking, curiosity-inducing, dramatic, or high-impact teaser/hook clip.\n"
        "Focus on action, strong historical symbols, iconic moments, drama, or high-contrast imagery.\n"
        "Output MUST be a valid JSON array of numbers, representing the chosen 'clip_number's (e.g. [3, 12]).\n"
        "Do NOT output markdown code blocks (e.g. ```json), preambles, explanation, or additional formatting. Only output the raw JSON array."
    )

    scenes_text = "\n".join([f"Clip #{c['clip_number']}: {c['text']}" for c in clips_list])
    prompt = f"Here is the list of scenes for Section {section_index}:\n\n{scenes_text}\n\nSelect the top {clips_per_section} most striking clip numbers."

    config = {
        "systemInstruction": system_instruction,
        "temperature": 0.2,
        "maxOutputTokens": 1024,
        "responseMimeType": "application/json",
    }

    print(f"  Calling Gemini ({gemini_model}) to select {clips_per_section} teaser clips for Section {section_index}...")
    response = client.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=config
    )
    text = response.text.strip()

    try:
        # Extract the JSON array using regex
        match = re.search(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", text)
        if match:
            array_str = match.group(0)
            selection = json.loads(array_str)
            if isinstance(selection, list):
                # Ensure they are integers and exist in the section
                valid_clip_numbers = {c['clip_number'] for c in clips_list}
                resolved = [int(x) for x in selection if int(x) in valid_clip_numbers]
                if len(resolved) == clips_per_section:
                    return resolved
                else:
                    # If length mismatch, pad or truncate
                    resolved = resolved[:clips_per_section]
                    remaining = list(valid_clip_numbers - set(resolved))
                    while len(resolved) < clips_per_section and remaining:
                        resolved.append(remaining.pop(0))
                    return resolved
        else:
            print(f"  [Warning] No JSON array found in Gemini response: {text}")
    except Exception as e:
        print(f"  [Warning] Failed to parse Gemini response: {text}. Error: {e}")

    # Fallback selection
    fallback = [c['clip_number'] for c in clips_list[:clips_per_section]]
    print(f"  [Warning] Falling back to default selection: {fallback}")
    return fallback

def main():
    parser = argparse.ArgumentParser(description="Generate visually striking teaser highlights video.")
    parser.add_argument("--project", default="the-entire-history-of-rome", help="Project slug or directory")
    parser.add_argument("--clips-per-section", type=int, default=2, help="Number of teaser clips to select per section")
    parser.add_argument("--force-regen", action="store_true", help="Force selection and cutting even if outputs exist")
    args = parser.parse_args()

    project_dir = DEFAULT_PROJECTS_DIR / args.project
    if not project_dir.exists():
        print(f"[Error] Project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    project_manifest_path = project_dir / "project.json"
    if not project_manifest_path.exists():
        print(f"[Error] project.json not found in {project_dir}", file=sys.stderr)
        sys.exit(1)

    project_manifest = json.loads(project_manifest_path.read_text(encoding="utf-8"))
    
    # Resolve GCP credentials
    gemini_model = project_manifest.get("gemini_model") or "gemini-2.5-flash"
    if "3.6" in gemini_model:
        gemini_model = "gemini-2.5-flash"
        
    gcp_project = None
    if KEY_PATH.exists():
        try:
            with open(KEY_PATH, "r", encoding="utf-8") as f:
                gcp_project = json.load(f).get("project_id")
        except Exception:
            pass
            
    if not gcp_project:
        gcp_project = project_manifest.get("gcp_project_id")
        
    gcp_location = project_manifest.get("gcp_location") or "global"
    if gcp_location == "global":
        gcp_location = "us-central1"

    if not gcp_project:
        print("[Error] GCP Project ID missing from project.json or key file.", file=sys.stderr)
        sys.exit(1)

    # Establish dirs
    exports_dir = project_dir / "exports"
    teaser_dir = exports_dir / "teaser_clips"
    
    if args.force_regen and teaser_dir.exists():
        print(f"[Info] Clearing old teaser clips directory: {teaser_dir.name}")
        shutil.rmtree(teaser_dir)
        
    exports_dir.mkdir(parents=True, exist_ok=True)
    teaser_dir.mkdir(parents=True, exist_ok=True)

    # Load clips manifest
    manifest_path = project_dir / "clips/clips_manifest.json"
    if not manifest_path.exists():
        print(f"[Error] clips_manifest.json not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)
    
    clips_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = clips_manifest.get("sections", [])

    # Load or initialize selections cache
    selections_cache_path = exports_dir / "teaser_selections.json"
    selections_cache = {}
    if selections_cache_path.exists() and not args.force_regen:
        try:
            selections_cache = json.loads(selections_cache_path.read_text(encoding="utf-8"))
            print(f"[Info] Loaded selections cache from {selections_cache_path.name}")
        except Exception:
            pass

    # Step 1: Select clips for all sections
    all_selections = {}
    for sec in sections:
        sec_idx = int(sec.get("section_index", 1))
        clips_list = sec.get("clips", [])
        if not clips_list:
            continue

        cache_key = str(sec_idx)
        if cache_key in selections_cache and not args.force_regen:
            selected_clips = selections_cache[cache_key]
            print(f"Section {sec_idx}: Re-using cached selection: {selected_clips}")
        else:
            selected_clips = generate_teaser_selections(
                gcp_project, gcp_location, gemini_model,
                sec_idx, clips_list, args.clips_per_section
            )
            selections_cache[cache_key] = selected_clips
            # Save cache immediately
            selections_cache_path.write_text(json.dumps(selections_cache, indent=2), encoding="utf-8")
            print(f"Section {sec_idx}: Selected clips: {selected_clips}")
        
        all_selections[sec_idx] = selected_clips

    # Step 2: Cut the selected clips to exactly 3 seconds
    print("\n--- PHASE 2: EXTRACTING 3-SECOND CLIP SEGMENTS ---")
    cut_teaser_files = []
    
    for sec_idx, chosen_clips in sorted(all_selections.items()):
        for c_num in chosen_clips:
            input_clip_path = project_dir / "veo" / f"section_{sec_idx}" / "cut" / f"clip_{c_num:03d}.mp4"
            if not input_clip_path.exists():
                print(f"  [Warning] Local clip file not found: {input_clip_path}. Trying trimmed_clips/ ...")
                # Fallback to trimmed_clips
                input_clip_path = project_dir / "trimmed_clips" / f"clip_{c_num:03d}_trimmed.mp4"
                if not input_clip_path.exists():
                    print(f"  [ERROR] Clip #{c_num:03d} not found. Skipping.")
                    continue

            output_clip_path = teaser_dir / f"sec_{sec_idx}_clip_{c_num:03d}_teaser.mp4"
            
            if output_clip_path.exists() and not args.force_regen:
                print(f"  Teaser clip already exists: {output_clip_path.name}")
                cut_teaser_files.append(output_clip_path)
                continue

            # Determine video duration
            duration = get_video_duration(str(input_clip_path))
            if duration <= 0:
                duration = 8.0  # fallback assumption
            
            # Determine start offset: skip first second if video is long enough
            start_time = 0.0
            if duration >= 4.0:
                start_time = 1.0
            elif duration > 3.0:
                start_time = duration - 3.0

            print(f"  Extracting 3s from {input_clip_path.name} starting at {start_time:.2f}s...")
            
            # Re-encode to 3.0 seconds, 24 fps, no audio (-an)
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start_time:.3f}",
                "-i", str(input_clip_path),
                "-t", "3.000",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-an",
                "-r", "24",
                "-pix_fmt", "yuv420p",
                str(output_clip_path)
            ]
            
            res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            if res.returncode == 0 and output_clip_path.exists() and output_clip_path.stat().st_size > 0:
                cut_teaser_files.append(output_clip_path)
            else:
                print(f"  [ERROR] Failed to cut clip: {res.stderr}")

    if not cut_teaser_files:
        print("[Error] No teaser sub-clips were successfully created!")
        sys.exit(1)

    # Step 3: Concatenate all sub-clips into exports/teaser_highlights.mp4
    print("\n--- PHASE 3: CONCATENATING SUB-CLIPS INTO HIGHLIGHTS MASTER ---")
    concat_txt_path = teaser_dir / "teaser_concat_list.txt"
    with open(concat_txt_path, "w", encoding="utf-8") as f:
        for filepath in cut_teaser_files:
            safe_p = str(filepath).replace("\\", "/")
            f.write(f"file '{safe_p}'\n")

    highlights_output_path = exports_dir / "teaser_highlights.mp4"
    print(f"Merging {len(cut_teaser_files)} teaser clips into {highlights_output_path.name}...")
    
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_txt_path),
        "-c", "copy",
        str(highlights_output_path)
    ]
    
    res = subprocess.run(concat_cmd, capture_output=True, text=True)
    if res.returncode == 0 and highlights_output_path.exists():
        size_mb = highlights_output_path.stat().st_size / (1024 * 1024)
        print(f"\n[SUCCESS] TEASER HIGHLIGHTS MOVIE CREATED!")
        print(f"Path: {highlights_output_path}")
        print(f"Size: {size_mb:.2f} MB")
        print(f"Duration: {len(cut_teaser_files) * 3} seconds")
    else:
        print(f"[Error] Concat failed: {res.stderr}")

if __name__ == "__main__":
    main()
