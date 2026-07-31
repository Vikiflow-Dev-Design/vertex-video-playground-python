#!/usr/bin/env python3
"""
stitch_project_master.py

Automated script to:
1. Load `clips/clips_manifest.json` for a project in `video_projects/<project_name>`.
2. Fetch or reuse local trimmed 8-second video clips.
3. Concatenate all trimmed clips in sequential order into a master movie in `exports/<project_name>_master.mp4`.
4. Automatically mux the master narration audio track (`audio/<project_name>_narration_master.mp3`) if available.
"""

import os
import sys
import json
import re
import argparse
import subprocess
import urllib.request

# Base directory setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYGROUND_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_PROJECTS_DIR = os.path.join(PLAYGROUND_DIR, "video_projects")
DEFAULT_MONGO_URI = "mongodb://victor:victoruche22123vic@76.13.42.74:27017/?directConnection=true"
DEFAULT_BASE_URL = "https://veedology.celfedu.com"

def parse_clip_number(prompt: str) -> int | None:
    """Extract numeric clip index from prompt string (e.g. '001: A shot...' -> 1)"""
    if not prompt:
        return None
    match = re.match(r"^(\d+)\s*:", prompt.strip())
    if match:
        return int(match.group(1))
    return None

def main():
    parser = argparse.ArgumentParser(description="Trim and stitch project video clips into a master movie.")
    parser.add_argument("--project", default="the-entire-history-of-jerusalem", help="Project directory name in video_projects/")
    parser.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI, help="MongoDB connection string")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL for video serving API")
    parser.add_argument("--use-section-exports", action="store_true", help="Stitch already-exported section_N_final.mp4 files directly")
    parser.add_argument("--insert-title-cards", action="store_true", help="Insert title cards between sections (Sections 2-9)")
    args = parser.parse_args()

    project_dir = os.path.join(DEFAULT_PROJECTS_DIR, args.project)
    if not os.path.exists(project_dir):
        print(f"[Error] Project directory not found: {project_dir}")
        sys.exit(1)

    exports_dir = os.path.join(project_dir, "exports")

    if args.use_section_exports:
        print("[Info] Stitching section final exports directly...")
        section_files = []
        for s_idx in range(1, 30):
            sec_path = os.path.join(exports_dir, f"section_{s_idx}_final.mp4")
            if os.path.exists(sec_path):
                if s_idx > 1 and args.insert_title_cards:
                    card_path = os.path.join(project_dir, "title_cards", f"section_{s_idx}_card.mp4")
                    if os.path.exists(card_path):
                        print(f"  Inserting title card for Section {s_idx} before video clip.")
                        section_files.append(card_path)
                    else:
                        print(f"  [Warning] Title card for Section {s_idx} requested but not found at {card_path}")
                section_files.append(sec_path)
        
        if not section_files:
            print("[Error] No section_N_final.mp4 files found in exports directory!")
            sys.exit(1)
            
        print(f"[OK] Found {len(section_files)} total video clips (sections + title cards) to stitch.")
        
        concat_txt_path = os.path.join(exports_dir, "concat_sections_manifest.txt")
        with open(concat_txt_path, "w", encoding="utf-8") as f:
            for filepath in section_files:
                safe_p = filepath.replace("\\", "/")
                f.write(f"file '{safe_p}'\n")
                
        master_filename = f"{args.project}_master.mp4"
        master_output_path = os.path.join(exports_dir, master_filename)
        
        print(f"\n[Info] Merging {len(section_files)} section videos into master movie...")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_txt_path,
            "-c", "copy",
            master_output_path
        ]
        res = subprocess.run(concat_cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(master_output_path):
            size_mb = os.path.getsize(master_output_path) / (1024 * 1024)
            print(f"\n[SUCCESS] MASTER SECTIONS VIDEO CREATED!")
            print(f"Path: {master_output_path}")
            print(f"Size: {size_mb:.2f} MB")
        else:
            print(f"[Error] Concat failed: {res.stderr}")
        return

    clips_manifest_path = os.path.join(project_dir, "clips", "clips_manifest.json")
    if not os.path.exists(clips_manifest_path):
        print(f"[Error] Manifest file not found: {clips_manifest_path}")
        sys.exit(1)

    print(f"[Info] Reading clip targets from: {clips_manifest_path}")
    with open(clips_manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Build map of clip_number -> target duration_seconds
    clip_durations: dict[int, float] = {}
    for section in manifest.get("sections", []):
        for clip in section.get("clips", []):
            c_num = int(clip["clip_number"])
            c_dur = float(clip["duration_seconds"])
            clip_durations[c_num] = c_dur

    total_expected_clips = len(clip_durations)
    print(f"[OK] Found {total_expected_clips} clip targets in manifest.")

    raw_dir = os.path.join(project_dir, "raw_clips")
    trimmed_dir = os.path.join(project_dir, "trimmed_clips")
    exports_dir = os.path.join(project_dir, "exports")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(trimmed_dir, exist_ok=True)
    os.makedirs(exports_dir, exist_ok=True)

    # Check local trimmed files
    trimmed_files: list[tuple[int, str]] = []
    missing_clips: list[int] = []

    for c_num in sorted(clip_durations.keys()):
        trimmed_path = os.path.join(trimmed_dir, f"clip_{c_num:03d}_trimmed.mp4")
        if os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
            trimmed_files.append((c_num, trimmed_path))
        else:
            missing_clips.append(c_num)

    print(f"[Info] Found {len(trimmed_files)} / {total_expected_clips} pre-trimmed clips on disk.")

    # Only attempt DB connection if there are missing clips to download/trim
    if missing_clips:
        print(f"[Info] Missing {len(missing_clips)} clips. Attempting MongoDB lookup...")
        try:
            from pymongo import MongoClient
            client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
            db = client["video-studio"]

            proj_doc = db.projects.find_one({"$or": [{"slug": args.project}, {"envId": f"project-{args.project}"}]})
            project_env_id = proj_doc.get("envId") if proj_doc else None
            
            if not project_env_id:
                sample_asset = db.mediaassets.find_one({"prompt": {"$regex": "^001:"}})
                if sample_asset:
                    project_env_id = sample_asset.get("projectEnvId")

            query = {"projectEnvId": project_env_id} if project_env_id else {}
            db_assets = list(db.mediaassets.find(query))

            clip_assets = {}
            for asset in db_assets:
                prompt = asset.get("prompt", "")
                c_num = parse_clip_number(prompt)
                if c_num is not None and c_num in clip_durations:
                    clip_assets[c_num] = asset

            for c_num in missing_clips:
                target_duration = clip_durations[c_num]
                asset = clip_assets.get(c_num)
                if not asset:
                    print(f"[Warning] Missing asset for Clip #{c_num:03d}")
                    continue

                asset_id = asset.get("id") or str(asset.get("_id"))
                raw_path = os.path.join(raw_dir, f"clip_{c_num:03d}.mp4")
                trimmed_path = os.path.join(trimmed_dir, f"clip_{c_num:03d}_trimmed.mp4")

                if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                    download_url = f"{args.base_url.rstrip('/')}/api/videos/{asset_id}.mp4"
                    print(f"[{c_num:03d}/{total_expected_clips}] Downloading raw clip from {download_url}...")
                    req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req) as resp, open(raw_path, "wb") as out_f:
                        out_f.write(resp.read())

                if not os.path.exists(trimmed_path) or os.path.getsize(trimmed_path) == 0:
                    print(f"[{c_num:03d}/{total_expected_clips}] Trimming clip to {target_duration:.3f}s...")
                    trim_cmd = [
                        "ffmpeg", "-y",
                        "-i", raw_path,
                        "-t", f"{target_duration:.3f}",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "18",
                        "-an",
                        trimmed_path
                    ]
                    res = subprocess.run(trim_cmd, capture_output=True, text=True)
                    if res.returncode == 0:
                        trimmed_files.append((c_num, trimmed_path))
        except Exception as e:
            print(f"[Warning] MongoDB lookup skipped or failed: {e}")

    # Re-sort trimmed files by clip number
    trimmed_files.sort(key=lambda x: x[0])
    print(f"\n[OK] Ready to merge {len(trimmed_files)} trimmed clips.")

    # Write concat manifest
    concat_txt_path = os.path.join(trimmed_dir, "concat_manifest.txt")
    with open(concat_txt_path, "w", encoding="utf-8") as f:
        for c_num, t_path in trimmed_files:
            safe_p = t_path.replace("\\", "/")
            f.write(f"file '{safe_p}'\n")

    master_filename = f"{args.project}_master.mp4"
    master_output_path = os.path.join(exports_dir, master_filename)

    print(f"\n[Info] Merging {len(trimmed_files)} clips into master movie...")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_txt_path,
        "-c", "copy",
        master_output_path
    ]
    res = subprocess.run(concat_cmd, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(master_output_path):
        size_mb = os.path.getsize(master_output_path) / (1024 * 1024)
        print(f"\n[SUCCESS] MASTER VIDEO CREATED!")
        print(f"Path: {master_output_path}")
        print(f"Size: {size_mb:.2f} MB")

        # Check for narration audio track
        audio_dir = os.path.join(project_dir, "audio")
        narration_mp3 = os.path.join(audio_dir, f"{args.project}_narration_master.mp3")
        if os.path.exists(narration_mp3):
            master_audio_video_path = os.path.join(exports_dir, f"{args.project}_master_with_audio.mp4")
            print(f"\n[Info] Muxing Master Video with Male Narration Audio Track...")
            mux_cmd = [
                "ffmpeg", "-y",
                "-i", master_output_path,
                "-i", narration_mp3,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                master_audio_video_path
            ]
            m_res = subprocess.run(mux_cmd, capture_output=True, text=True)
            if m_res.returncode == 0 and os.path.exists(master_audio_video_path):
                m_size_mb = os.path.getsize(master_audio_video_path) / (1024 * 1024)
                print(f"\n[SUCCESS] MASTER MOVIE WITH AUDIO SUCCESSFULLY CREATED!")
                print(f"Path: {master_audio_video_path}")
                print(f"Size: {m_size_mb:.2f} MB")
            else:
                print(f"[Error] Audio muxing failed: {m_res.stderr}")
    else:
        print(f"[Error] Concat failed: {res.stderr}")

if __name__ == "__main__":
    main()
