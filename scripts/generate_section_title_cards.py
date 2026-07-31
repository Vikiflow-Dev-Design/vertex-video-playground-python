#!/usr/bin/env python3
"""
generate_section_title_cards.py

1. Reads sections from source/sections_raw.txt.
2. Uses Gemini (via Vertex AI) to generate a high-quality Imagen prompt matching that section's historical theme.
3. Submits an image generation job (type='image') to the MongoDB queuejobs collection.
4. Polls MongoDB mediaassets for job completion.
5. Downloads the completed JPEG.
6. Composites a 2-second title card video using FFmpeg with blur, overlay, and text.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from bson import ObjectId
from pymongo import MongoClient
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

sys.path.insert(0, str(PROJECT_ROOT))
from scripts.style_profiles import resolve_style_template_path

SECTION_TITLES = {
    2: "Rome's Global Impact",
    3: "The Unlikely Origin",
    4: "Myth & Early Settlements",
    5: "The Kingdom & Etruscan Influence",
    6: "Birth of the Republic",
    7: "Expansion & the Punic Wars",
    8: "Caesar's Rise & the Empire",
    9: "The Enduring City"
}

def get_master_style(project_dir: Path) -> str:
    # Look for instructions/visual_prompt_master_prompt.md or default template
    project_prompt = project_dir / "instructions" / "visual_prompt_master_prompt.md"
    if project_prompt.exists():
        return project_prompt.read_text(encoding="utf-8")
    
    default_prompt = PROJECT_ROOT / "templates" / "visual_prompt_master_prompt.md"
    if default_prompt.exists():
        return default_prompt.read_text(encoding="utf-8")
    
    return "grounded cinematic historical realism, prestige drama visual tone"

def extract_section_script(sections_raw_path: Path, section_num: int) -> str | None:
    if not sections_raw_path.exists():
        return None
    raw_text = sections_raw_path.read_text(encoding="utf-8")
    parts = re.split(r'(?m)^#\s+Section\s+(\d+)\s*$', raw_text)
    
    # Section 1 is index 0
    if section_num == 1:
        return parts[0].strip()
        
    for i in range(1, len(parts), 2):
        if int(parts[i]) == section_num:
            return parts[i+1].strip()
    return None

def generate_image_prompt(gcp_project: str, gcp_location: str, gemini_model: str, script_text: str, master_style: str) -> str:
    from google import genai
    
    # Create Vertex AI client
    client = genai.Client(vertexai=True, project=gcp_project, location=gcp_location)
    
    system_instruction = (
        "You are an art director for a historical documentary series about Rome.\n"
        "You will receive the script for one section of the documentary and the visual style guide.\n"
        "Your task: write ONE single Imagen image generation prompt that captures the historical theme and atmosphere of this section.\n\n"
        "Rules:\n"
        "- The prompt is for a STILL IMAGE (not a video). Do NOT include camera movement or panning instructions.\n"
        "- Keep it 2-4 sentences. Be concrete: describe the physical scene, lighting, composition, and atmosphere.\n"
        "- Match the project's visual style (grounded cinematic historical realism, natural daylight, ultra-detailed photorealism, serious prestige-drama visual tone).\n"
        "- Never name real historical figures. Use role descriptions instead: 'a Roman emperor', 'a general', 'soldiers', etc.\n"
        "- No graphic violence, blood, or gore. Suggest battles through smoke, ruins, massing armies at distance.\n"
        "- No minors.\n"
        "- No aspect ratio tag (do not mention 16:9 or dimensions).\n"
        "- Respond with ONLY the prompt text — no explanation, preamble, or labels."
    )
    
    prompt = f"STYLE GUIDE:\n{master_style}\n\nSECTION SCRIPT:\n{script_text}"
    
    config = {
        "systemInstruction": system_instruction,
        "temperature": 0.3,
        "maxOutputTokens": 1024,
    }
    
    print(f"  Calling Gemini ({gemini_model}) to generate Imagen prompt...")
    response = client.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=config
    )
    
    text = response.text.strip()
    # Clean up formatting/quotes if Gemini added any
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    return text

def download_image_file(asset_id: str, gcs_uri: str, destination: Path, key_path: Path) -> bool:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    destination.parent.mkdir(parents=True, exist_ok=True)
    
    # Method 1: Try public URL first
    public_url = f"https://veedology.celfedu.com/api/videos/{asset_id}.jpg"
    try:
        print(f"    Trying public URL: {public_url} ...")
        req = urllib.request.Request(public_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp, open(destination, "wb") as out:
            shutil.copyfileobj(resp, out)
        if destination.stat().st_size > 0:
            print(f"    Success: Public download completed.")
            return True
    except Exception as e:
        print(f"    Public download skipped/failed ({e}). Trying direct GCS...")

    # Method 2: Fall back to GCS download
    try:
        clean_uri = gcs_uri.replace("gs://", "")
        bucket_name, blob_path = clean_uri.split("/", 1)
        encoded_blob = urllib.parse.quote(blob_path, safe="")
        url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket_name}/o/{encoded_blob}?alt=media"

        creds = service_account.Credentials.from_service_account_file(
            str(key_path),
            scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
        )
        creds.refresh(Request())

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
        with urllib.request.urlopen(req) as resp, open(destination, "wb") as out:
            shutil.copyfileobj(resp, out)
        if destination.stat().st_size > 0:
            print(f"    Success: Direct GCS download completed.")
            return True
    except Exception as e:
        print(f"    [ERROR] GCS download failed: {e}")
        
    return False

def composite_title_card(bg_image_path: Path, section_num: int, title_text: str, output_path: Path) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Find Windows Fonts
    font_regular = "C:/Windows/Fonts/arial.ttf".replace(":", "\\:")
    font_bold = "C:/Windows/Fonts/arialbd.ttf".replace(":", "\\:")

    part_text = f"PART {section_num}"
    
    # Write title text to a temp file to avoid complex shell/FFmpeg escaping rules
    temp_file = output_path.parent / f"temp_text_{section_num}.txt"
    temp_file.write_text(title_text.upper(), encoding="utf-8")
    temp_file_escaped = str(temp_file).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(bg_image_path),
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-vf", (
            f"scale=1280:720,format=yuv420p,"
            f"gblur=sigma=6,"
            f"drawbox=w=iw:h=ih:color=black@0.50:t=fill,"
            f"drawtext=text='{part_text}':fontsize=30:fontcolor=white@0.70:"
            f"x=(w-text_w)/2:y=(h/2)-90:fontfile='{font_regular}',"
            f"drawtext=textfile='{temp_file_escaped}':fontsize=56:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h/2)-20:fontfile='{font_bold}',"
            f"fade=t=in:st=0:d=0.75,"
            f"fade=t=out:st=3.25:d=0.75"
        ),
        "-af", "afade=t=in:st=0:d=0.75,afade=t=out:st=3.25:d=0.75",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "96k",
        "-t", "4", "-r", "24", "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path)
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    # Clean up the temp file
    if temp_file.exists():
        try:
            temp_file.unlink()
        except Exception:
            pass
            
    if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return True
    else:
        print(f"    [ERROR] FFmpeg failed with code {res.returncode}")
        if res.stderr:
            print(f"    Stderr: {res.stderr}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Generate title cards for sections 2-9")
    parser.add_argument("--project", default="the-entire-history-of-rome", help="Project slug or directory")
    parser.add_argument("--sections", default="2,3,4,5,6,7,8,9", help="Comma-separated section indices to process")
    parser.add_argument("--prompts-only", action="store_true", help="Generate Gemini prompts only, no queue/stitching")
    parser.add_argument("--skip-prompt-gen", action="store_true", help="Skip Gemini prompt gen, use existing saved prompts")
    parser.add_argument("--force-regen", action="store_true", help="Force image regeneration even if local outputs exist")
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
    
    # Resolve MongoDB & GCP credentials
    mongo_uri = project_manifest.get("mongo_uri") or os.getenv("MONGODB_URI")
    mongo_db = project_manifest.get("mongo_db") or os.getenv("MONGODB_DB") or "video-studio"
    mongo_user_id = project_manifest.get("mongo_user_id") or os.getenv("MONGODB_USER_ID")
    mongo_project_id = project_manifest.get("mongo_project_id")
    gemini_model = project_manifest.get("gemini_model") or "gemini-3.6-flash"
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

    if not mongo_uri or not mongo_db or not mongo_user_id or not mongo_project_id:
        print("[Error] MongoDB connection parameters missing from project.json or environment.", file=sys.stderr)
        sys.exit(1)

    if not gcp_project:
        print("[Error] GCP Project ID missing from project.json or key file.", file=sys.stderr)
        sys.exit(1)

    sections_to_process = [int(s.strip()) for s in args.sections.split(",") if s.strip()]
    
    # Establish dirs
    title_cards_dir = project_dir / "title_cards"
    prompts_dir = title_cards_dir / "prompts"
    raw_dir = title_cards_dir / "raw"
    
    title_cards_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    sections_raw_path = project_dir / "source" / "sections_raw.txt"
    master_style = get_master_style(project_dir)

    print("\n--- PHASE 0 & 1: PROMPT GENERATION & QUEUE SUBMISSION ---")
    
    # Connect to MongoDB
    print(f"Connecting to MongoDB...")
    client_db = MongoClient(mongo_uri)
    db = client_db[mongo_db]
    
    image_jobs = {}
    jobs_file = title_cards_dir / "image_jobs.json"
    if jobs_file.exists():
        try:
            image_jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    for s_num in sections_to_process:
        print(f"\nProcessing Section {s_num}: {SECTION_TITLES.get(s_num, 'N/A')}")
        
        prompt_file = prompts_dir / f"section_{s_num}_prompt.txt"
        bg_image_path = raw_dir / f"section_{s_num}_bg.jpg"
        card_video_path = title_cards_dir / f"section_{s_num}_card.mp4"
        
        # Check if local video card already exists and we are not forcing regeneration
        if card_video_path.exists() and not args.force_regen:
            print(f"  Title card video already exists locally: {card_video_path.name}. Skipping generation flow.")
            continue

        # Get prompt
        prompt_text = None
        if prompt_file.exists() and args.skip_prompt_gen:
            prompt_text = prompt_file.read_text(encoding="utf-8").strip()
            print(f"  Loaded saved prompt: \"{prompt_text[:80]}...\"")
        else:
            script_text = extract_section_script(sections_raw_path, s_num)
            if not script_text:
                print(f"  [Warning] Script text for Section {s_num} not found in sections_raw.txt. Skipping.")
                continue
            try:
                prompt_text = generate_image_prompt(gcp_project, gcp_location, gemini_model, script_text, master_style)
                prompt_file.write_text(prompt_text, encoding="utf-8")
                print(f"  Gemini prompt: \"{prompt_text[:80]}...\"")
            except Exception as e:
                print(f"  [ERROR] Gemini generation failed: {e}")
                continue

        if args.prompts_only:
            continue

        # Push to Queue if bg image doesn't exist
        if bg_image_path.exists() and not args.force_regen:
            print(f"  Raw background image already exists locally: {bg_image_path.name}")
            continue

        # Check if we already have an active job ID in our state
        existing_job_id = image_jobs.get(str(s_num))
        if existing_job_id:
            # Check if this job is still in DB or finished
            job_doc = db["queuejobs"].find_one({"_id": ObjectId(existing_job_id)})
            if job_doc:
                print(f"  Job already exists in queue database with ID: {existing_job_id} (Status: {job_doc.get('status')})")
                continue
            
            # Check if it was already processed into a mediaasset
            asset_doc = db["mediaassets"].find_one({"batchQueueItemId": existing_job_id})
            if asset_doc:
                print(f"  Asset already processed in DB for job ID: {existing_job_id}")
                continue

        # Push new job
        job_doc = {
            "userId": ObjectId(mongo_user_id) if isinstance(mongo_user_id, str) else mongo_user_id,
            "type": "image",
            "task": "image",
            "status": "queued",
            "prompt": prompt_text,
            "model": "imagen-3.0-generate-002",
            "aspectRatio": "16:9",
            "durationSeconds": 0,
            "numberOfImages": 1,
            "tokenCost": 5,
            "projectEnvId": mongo_project_id,
            "titleCardSection": s_num,
            "titleCardTag": f"title-card-section-{s_num}",
            "createdAt": datetime.datetime.now(datetime.timezone.utc),
            "updatedAt": datetime.datetime.now(datetime.timezone.utc),
            "refunded": False
        }
        
        res = db["queuejobs"].insert_one(job_doc)
        job_id_str = str(res.inserted_id)
        image_jobs[str(s_num)] = job_id_str
        jobs_file.write_text(json.dumps(image_jobs, indent=2), encoding="utf-8")
        print(f"  [Enqueued] Pushed job to MongoDB: ID {job_id_str}")

    if args.prompts_only:
        print("\n[OK] Prompts generated. --prompts-only specified. Stopping.")
        return

    print("\n--- PHASE 2: POLLING & DOWNLOADING IMAGES ---")
    pending_sections = []
    for s_num in sections_to_process:
        bg_image_path = raw_dir / f"section_{s_num}_bg.jpg"
        if not bg_image_path.exists() or args.force_regen:
            pending_sections.append(s_num)

    if not pending_sections:
        print("All raw background images already downloaded locally.")
    else:
        print(f"Polling MongoDB for {len(pending_sections)} pending section image(s)...")
        max_wait_seconds = 900  # 15 minutes timeout
        start_poll_time = time.time()
        
        while pending_sections and (time.time() - start_poll_time < max_wait_seconds):
            still_pending = []
            for s_num in pending_sections:
                job_id_str = image_jobs.get(str(s_num))
                if not job_id_str:
                    print(f"  [Warning] Section {s_num} has no enqueued job ID in state.")
                    continue
                
                # Check mediaassets first
                asset = db["mediaassets"].find_one({"batchQueueItemId": job_id_str})
                if asset:
                    gcs_uri = asset.get("gcsUri")
                    asset_id = asset.get("id") or str(asset.get("_id"))
                    print(f"  [Ready] Section {s_num} image generated in DB. Downloading...")
                    bg_image_path = raw_dir / f"section_{s_num}_bg.jpg"
                    success = download_image_file(asset_id, gcs_uri, bg_image_path, KEY_PATH)
                    if success:
                        print(f"  [Downloaded] Section {s_num} background image.")
                    else:
                        print(f"  [Error] Failed to download Section {s_num} image.")
                else:
                    # Check if job failed in queuejobs
                    job_doc = db["queuejobs"].find_one({"_id": ObjectId(job_id_str)})
                    if job_doc and job_doc.get("status") == "failed":
                        print(f"  [Failed] Section {s_num} job {job_id_str} failed. Error: {job_doc.get('error')}")
                    else:
                        still_pending.append(s_num)
            
            pending_sections = still_pending
            if pending_sections:
                print(f"  Still waiting for section(s): {pending_sections}. Sleeping for 10s...")
                time.sleep(10)
                
        if pending_sections:
            print(f"\n[Warning] Timed out waiting for image generation of sections: {pending_sections}")

    print("\n--- PHASE 3: COMPOSITING TITLE CARD VIDEOS ---")
    composited_count = 0
    for s_num in sections_to_process:
        bg_image_path = raw_dir / f"section_{s_num}_bg.jpg"
        card_video_path = title_cards_dir / f"section_{s_num}_card.mp4"
        
        if card_video_path.exists() and not args.force_regen:
            continue
            
        if not bg_image_path.exists():
            print(f"  [Skip] Cannot composite Section {s_num} title card because raw background is missing.")
            continue
            
        title_text = SECTION_TITLES.get(s_num, f"Section {s_num}")
        print(f"Compositing card for Section {s_num}...")
        success = composite_title_card(bg_image_path, s_num, title_text, card_video_path)
        if success:
            composited_count += 1
            
    print(f"\n[OK] Compositing completed. {composited_count} new card videos generated.")

if __name__ == "__main__":
    main()
