# Vertex Video Playground

A small local project scaffold for generating videos with Google Vertex AI / Veo.

## What this is

This project is the code side of the setup. It does not create the Google Cloud project for you.
You still need to:

1. Create or choose a Google Cloud project
2. Enable the Vertex AI API
3. Set up authentication
4. Create a Cloud Storage bucket for outputs (recommended)

## Required Google Cloud setup

Enable these APIs in the same GCP project:

- Vertex AI API: `aiplatform.googleapis.com`
- Cloud Storage API: `storage.googleapis.com` (if you want to write results to a bucket)

Recommended IAM roles for the account or service account:

- `roles/aiplatform.user`
- `roles/storage.objectAdmin` on the output bucket
- `roles/serviceusage.serviceUsageAdmin` only if you need to enable APIs yourself

## Local authentication options

For local development:

```bash
gcloud auth application-default login
```

For a server or CI:

- use a service account with the required roles
- point credentials at it with `GOOGLE_APPLICATION_CREDENTIALS`

## Environment variables

Create a `.env` file from `.env.example` and fill in your values.

## Install

This VPS does not have `python3-venv`, so use `uv` to create the environment:

```bash
uv venv .venv2
source .venv2/bin/activate
uv pip install -r requirements.txt
```

## Authenticate the project

Choose one of these:

### Option A: ADC with your Google account

Use this on a machine where `gcloud` is installed:

```bash
gcloud auth application-default login
```

Then set your project:

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=True
```

### Option B: Service account key

1. Create a service account in the same GCP project.
2. Grant it:
   - `roles/aiplatform.user`
   - `roles/storage.objectAdmin` on your output bucket
3. Download a JSON key.
4. Point this project at it:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=True
```

### Verify auth

```bash
source .venv2/bin/activate
python check_auth.py
```

## Project workflow

This repo supports a per-video-project workspace under `video_projects/`. Follow these **8 steps in order** for every section.

> For full detail on each step see [`AGENTS.md`](AGENTS.md).

---

### Step 1 — Script Clipping

Split the raw narration script into sentence-aware clip files with TTS-verified durations:

```bash
.venv2\Scripts\python.exe scripts/generate_section_clips.py --project <project-name>
```

Outputs `clips/clips_manifest.json` (with `clips[]` and `sentence_clips[]` per section), `*_clips.txt` human-readable files, and `*_sentence_clips.json` files.

---

### Step 2 — Visual Prompt Generation

Generate Gemini cinematic visual prompts for every clip:

```bash
.venv2\Scripts\python.exe scripts/generate_visual_prompts.py --project <project-name>
```

Outputs `prompts/visual_prompts.json`.

---

### Step 3 — Veo Video Generation

Submit visual prompts to Veo 3.1 Lite via the queue system. Each clip generates an 8-second `.mp4` stored in GCS, with metadata written to MongoDB `mediaassets`.

```bash
.venv2\Scripts\python.exe generate_video.py --project-dir video_projects/<project-name> \
  --prompt "your visual prompt" \
  --output-gcs-uri gs://YOUR_BUCKET/outputs/
```

---

### Step 4 — Download & Initial Cut

Download generated clips from GCS and trim each to its manifest `duration_seconds`:

```bash
.venv2\Scripts\python.exe scripts/download_and_cut_db_videos.py --project <project-name> --section-index 1
```

Outputs to:
- `veo/trimmed/downloaded/clip_001_raw.mp4 ...` — **keep these, they are re-used in Step 7**
- `veo/trimmed/cut/clip_001.mp4 ...` — initial cut (overwritten in Step 7)

---

### Step 5 — Audio Generation (TTS per Sentence)

Synthesize one MP3 per sentence using Edge TTS:

```bash
.venv2\Scripts\python.exe scripts/generate_project_audio.py --project <project-name> --section-index 1
```

Outputs `audio/section_1/sentences/sent_001.mp3 ...` and `audio/section_1/narration.mp3`.

> Only the `sentences/` folder is produced. No `clips/` or `padded/` directories.

---

### Step 6 — Verify Narration Duration

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 audio/section_1/narration.mp3
```

The narration will typically be slightly longer than the initially cut video clips (TTS synthesis varies between runs). Step 7 fixes this.

---

### Step 7 — Smart Re-cut (Sync Video to Narration)

Re-cut every clip from the raw 8s sources so video duration exactly equals narration duration:

```bash
.venv2\Scripts\python.exe scripts/recut_section_clips.py --project <project-name> --section-index 1
```

This script:
- Probes actual sentence audio durations
- Computes new clip durations matched to audio (using original split ratios for split sentences)
- Applies overflow rule: if a clip needs > 8s, caps it and gives overflow to the shorter neighbour
- Re-trims all clips from `veo/trimmed/downloaded/`
- Joins all clips and muxes with narration

**Output:** `exports/section_1_final.mp4` — video and narration in perfect sync ✅

---

### Step 8 — Final Output

```
exports/
  section_1_final.mp4   ← final deliverable
  section_2_final.mp4
  ...
```

Video duration = Narration duration = exact match. Every clip plays for exactly as long as the narrator speaks that sentence.

---

## Direct Veo generation

When you already have a finished visual prompt, you can send it straight to Veo with:

```bash
.venv2\Scripts\python.exe generate_video.py \
  --project-id YOUR_PROJECT_ID \
  --location global \
  --model veo-3.1-lite-generate-001 \
  --prompt "your final visual prompt here" \
  --output-gcs-uri gs://YOUR_BUCKET/outputs/
```

---

## MongoDB records created

- `projects` collection: one record per project
- `mediaassets` collection: one record per generated video clip

Set these env vars:

```bash
export MONGODB_URI='mongodb://...'
export MONGODB_DB='video-studio'
export MONGODB_USER_ID='your-user-id'
```


