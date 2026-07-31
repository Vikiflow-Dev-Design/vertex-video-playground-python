import os
import json
import re
import shutil
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path
from pymongo import MongoClient
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# Paths
BASE_DIR = Path("C:/Users/victor/Desktop/google-cloud-video-automation/vertex-video-playground/vertex-video-playground")
PROJECT_DIR = BASE_DIR / "video_projects/the-entire-history-of-rome"
MANIFEST_PATH = PROJECT_DIR / "clips/clips_manifest.json"
KEY_PATH = BASE_DIR / "gcp-key.json"

# Output Dirs
VEO_DIR = PROJECT_DIR / "veo"
OUTPUT_DIR = VEO_DIR / "trimmed"
DOWNLOAD_DIR = OUTPUT_DIR / "downloaded"
CUT_DIR = OUTPUT_DIR / "cut"

def parse_clip_number(prompt: str) -> int | None:
    match = re.match(r"^(\d+):", prompt)
    if match:
        return int(match.group(1))
    return None

def download_video_file(asset_id: str, gcs_uri: str, destination: Path, creds) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    
    # Method 1: Try public URL first
    public_url = f"https://veedology.celfedu.com/api/videos/{asset_id}.mp4"
    try:
        req = urllib.request.Request(public_url)
        with urllib.request.urlopen(req, timeout=15) as resp, open(destination, "wb") as out:
            shutil.copyfileobj(resp, out)
        return
    except Exception as e:
        print(f"  Public download failed ({e}). Falling back to GCS download...")

    # Method 2: Fall back to GCS download
    clean_uri = gcs_uri.replace("gs://", "")
    bucket_name, blob_path = clean_uri.split("/", 1)
    encoded_blob = urllib.parse.quote(blob_path, safe="")
    url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket_name}/o/{encoded_blob}?alt=media"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
    with urllib.request.urlopen(req) as resp, open(destination, "wb") as out:
        shutil.copyfileobj(resp, out)

import argparse

def main():
    parser = argparse.ArgumentParser(description="Download and cut video clips from MongoDB for a specific section.")
    parser.add_argument("--section-index", type=int, default=1, help="Section index (1-based)")
    args = parser.parse_args()

    sec_idx = args.section_index
    DOWNLOAD_DIR = VEO_DIR / f"section_{sec_idx}" / "downloaded"
    CUT_DIR = VEO_DIR / f"section_{sec_idx}" / "cut"

    print(f"Creating output directories for Section {sec_idx}...")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading project manifest and clips_manifest...")
    project_json = json.loads((PROJECT_DIR / "project.json").read_text(encoding="utf-8"))
    mongo_project_id = project_json.get("mongo_project_id")
    mongo_uri = project_json.get("mongo_uri")
    mongo_db = project_json.get("mongo_db")

    if not mongo_project_id:
        print("[ERROR] No mongo_project_id found in project.json")
        return

    # Load clip durations from clips_manifest.json
    clips_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    target_section = None
    for sec in clips_manifest.get("sections", []):
        if int(sec.get("section_index", -1)) == sec_idx:
            target_section = sec
            break

    if not target_section:
        print(f"[ERROR] Section {sec_idx} not found in clips_manifest.json")
        return

    clip_durations = {}
    clip_texts = {}
    for clip in target_section.get("clips", []):
        c_num = int(clip["clip_number"])
        clip_durations[c_num] = float(clip["duration_seconds"])
        clip_texts[c_num] = clip["text"].strip()

    total_clips = len(clip_durations)
    print(f"Loaded {total_clips} clip durations for Section {sec_idx}.")

    # Connect to MongoDB
    print(f"Connecting to MongoDB database: {mongo_db}...")
    client = MongoClient(mongo_uri)
    db = client[mongo_db]

    # Query all mediaassets for this projectEnvId
    print(f"Querying mediaassets for projectEnvId: {mongo_project_id}...")
    db_assets = list(db.mediaassets.find({"projectEnvId": mongo_project_id}))
    print(f"Found {len(db_assets)} total assets in DB.")

    # Match assets by clip number prefix (e.g. "001:")
    clip_assets = {}
    for c_num in clip_texts.keys():
        prefix = f"{c_num:03d}:"
        # Filter all assets starting with this prefix
        matching = [a for a in db_assets if a.get("prompt", "").strip().startswith(prefix)]
        if matching:
            # Sort by _id descending (latest first)
            matching.sort(key=lambda x: str(x["_id"]), reverse=True)
            clip_assets[c_num] = matching[0]
            print(f"  Matched Clip {c_num:03d} -> Asset ID: {matching[0]['_id']} (Prompt: \"{matching[0]['prompt'][:60]}...\")")
        else:
            print(f"  [Warning] No asset found starting with \"{prefix}\"")

    # Initialize service account credentials
    print(f"Loading credentials from key: {KEY_PATH}...")
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_PATH),
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
    )
    creds.refresh(Request())

    success_count = 0
    fail_count = 0

    for c_num in sorted(clip_durations.keys()):
        target_duration = clip_durations[c_num]
        asset = clip_assets.get(c_num)
        if not asset:
            print(f"[Warning] Clip {c_num:03d} has no generated asset in DB. Skipping.")
            fail_count += 1
            continue

        gcs_uri = asset.get("gcsUri")
        if not gcs_uri:
            print(f"[Warning] Clip {c_num:03d} asset has no gcsUri. Skipping.")
            fail_count += 1
            continue

        raw_path = DOWNLOAD_DIR / f"clip_{c_num:03d}_raw.mp4"
        trimmed_path = CUT_DIR / f"clip_{c_num:03d}.mp4"

        try:
            if not raw_path.exists() or raw_path.stat().st_size == 0:
                print(f"[{c_num:03d}/{total_clips:03d}] Downloading raw clip for asset {asset.get('_id')}...")
                download_video_file(str(asset["_id"]), gcs_uri, raw_path, creds)

            # Trim with ffmpeg (remove audio stream with -an option as per pipeline style)
            print(f"[{c_num:03d}/{total_clips:03d}] Trimming clip to {target_duration:.3f}s...")
            trim_cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_path),
                "-t", f"{target_duration:.3f}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-an",
                str(trimmed_path)
            ]
            res = subprocess.run(trim_cmd, capture_output=True, text=True)
            if res.returncode == 0:
                success_count += 1
            else:
                print(f"[ERROR] Failed to trim Clip {c_num:03d}: {res.stderr}")
                fail_count += 1

        except Exception as e:
            print(f"[ERROR] Clip {c_num:03d} failed processing: {e}")
            fail_count += 1

    print("\n========================================")
    print("DOWNLOAD AND CUT COMPLETED")
    print("========================================")
    print(f"Successfully processed: {success_count} clips")
    print(f"Failed to process:      {fail_count} clips")
    print("========================================\n")

if __name__ == "__main__":
    main()
