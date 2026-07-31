# Vertex Video Playground - Agent Guidelines & Project Standard

This guide outlines core conventions, project architecture, authentication, and the mandatory workflow for agents working on `vertex-video-playground`.

---

## 1. Core Technology Stack & Architecture
- **Language**: Python 3.10+
- **Video AI Generation**: Google GenAI SDK (`google-genai`), Vertex AI Veo 3.1 Lite (`veo-3.1-lite-generate-001`).
- **Text-to-Speech Engine**: `edge-tts` (Microsoft Azure Neural Speech backend).
- **Post-Processing & Video Editing**: `ffmpeg` (trimming clips to exact duration, master concatenation).
- **Persistence & Queueing**: MongoDB (`pymongo`) for `projects`, `mediaassets`, and `queuejobs`.

---

## 2. COMPLETE SECTION PIPELINE (The Proven Workflow)

This is the **exact, validated pipeline** used to produce a perfect section video with synced narration. Follow these steps in order for every section.

```
STEP 1 → STEP 2 → STEP 3 → STEP 4 → STEP 5 → STEP 6 → STEP 7
 Clip     Audio    Visual   Pre-compute Video    Smart    Final
 Script   (TTS)    Prompts  & Push      Download Re-cut   Mux
```

---

### STEP 1 — Script Clipping
**Script:** [`scripts/generate_section_clips.py`](scripts/generate_section_clips.py)

Reads the raw narrated script, splits it into sentence-aware clips using the 4-pass clipping engine (see Section 3).

**Outputs:**
- `clips/clips_manifest.json` — master manifest with `clips[]` AND `sentence_clips[]` per section
- `clips/001-section-1_clips.txt` — human-readable clip breakdown
- `clips/001-section-1_sentence_clips.json` — sentence-level audio source list

```bash
python scripts/generate_section_clips.py --project <project-name>
```

---

### STEP 2 — Audio Generation (TTS per Sentence)
**Script:** [`scripts/generate_project_audio.py`](scripts/generate_project_audio.py)

Synthesizes one MP3 per **sentence** (not per clip) using Edge TTS. Reads from `sentence_clips[]` in the manifest. Automatically concatenates all sentence files in order to build `narration.mp3`.

**Outputs:**
```
audio/
  section_1/
    sentences/
      sent_001.mp3    ← full natural sentence synthesis
      sent_002.mp3
      ...
    narration.mp3     ← all sentences joined in order (preview master)
```

> ⚠️ **CRITICAL**: Sentence audio MUST be generated BEFORE pushing jobs to MongoDB or generating video. This allows accurate duration pre-computation.

```bash
python scripts/generate_project_audio.py --project <project-name> --section-index 1
```

---

### STEP 3 — Visual Prompt Generation
**Script:** [`scripts/generate_visual_prompts.py`](scripts/generate_visual_prompts.py)

Sends each clip's text to Gemini (gemini-2.5-flash) to generate a rich cinematic visual prompt for Veo. Reads from `clips_manifest.json`.

**Outputs:**
- `prompts/visual_prompts.json` — per-clip visual prompts
- `prompts/section_N_visual_prompts.md` — human-readable prompt review

```bash
python scripts/generate_visual_prompts.py --project <project-name>
```

---

### STEP 4 — Veo Video Generation (Pre-compute & Push)
**Script:** [`scripts/push_project_to_queue.py`](scripts/push_project_to_queue.py) (uses [`scripts/precompute_clip_durations.py`](scripts/precompute_clip_durations.py) internally)

Submits each clip's visual prompt to MongoDB queue. Before pushing, it **pre-computes the exact target durations** from real sentence audio files and applies the overflow redistribution rules, mapping each clip to its correct **ceiling** Veo duration (4s, 6s, or 8s).

#### CRITICAL — Audio-First Duration Pre-Computation Rule
> Veo supports exactly **three video durations: 4s, 6s, and 8s**. To ensure that a trimmed clip has enough source footage when overflow is redistributed, the push script maps each pre-computed target clip duration to its **ceiling** Veo step.
>
> **Example:**
> - clip target dur ≤ 4.0s → request **4 seconds**
> - 4.0s < clip target dur ≤ 6.0s → request **6 seconds**
> - 6.0s < clip target dur → request **8 seconds**

```bash
# Dry run to preview pre-computed Veo job durations
python scripts/push_project_to_queue.py --project <project-name> --section-index 1 --dry-run

# Push to queue and poll for completion
python scripts/push_project_to_queue.py --project <project-name> --section-index 1 --wait
```

---

### STEP 5 — Video Download & Cut
**Script:** [`scripts/download_and_cut_db_videos.py`](scripts/download_and_cut_db_videos.py)

Downloads generated clips and trims each to its initial duration. Attempts to download from the public server endpoint first to bypass GCS permissions, falling back to authenticated GCS download if needed.

**Outputs:**
```
veo/trimmed/
  downloaded/
    clip_001_raw.mp4   ← raw Veo clip (4s, 6s, or 8s — keep these, needed for Step 6)
    clip_002_raw.mp4
    ...
  cut/
    clip_001.mp4       ← trimmed clip (temporary — overwritten in Step 6)
    clip_002.mp4
```

```bash
python scripts/download_and_cut_db_videos.py --project <project-name> --section-index 1
```

---

### STEP 6 — Smart Re-cut (Sync Video to Narration)
**Script:** [`scripts/recut_section_clips.py`](scripts/recut_section_clips.py)

Re-cuts every video clip from the raw `downloaded/` sources so its duration exactly matches its sentence audio duration, applying overflow redistribution at trim time. Since raw videos were enqueued using pre-computed ceiling durations, the sources are guaranteed to have sufficient footage.

**Algorithm:**
1. Probe actual durations of all `sentences/sent_*.mp3` files via ffprobe.
2. For **standalone clips**: `new_dur = sentence_audio_dur`
3. For **split clips**: `new_dur = sentence_audio_dur × (orig_part_dur / orig_total_dur)`
4. **Overflow rule** (new_dur > source cap):
   - Cap clip at raw video duration
   - Find shorter of immediate neighbours (clip before or clip after)
   - Add overflow seconds to that shorter neighbour
5. Re-trim all clips from `veo/trimmed/downloaded/` raw sources → `veo/trimmed/cut/`
6. Concatenate re-cut clips into joined video.
7. Mux joined video + narration.mp3 → `exports/section_N_final.mp4`

```bash
python scripts/recut_section_clips.py --project <project-name> --section-index 1
```

---

### STEP 7 — Final Output
The final section video is generated at:
- `exports/section_N_final.mp4`
- Video duration = Narration duration = **exact match** ✅
- Every clip plays for exactly as long as the narrator speaks that sentence.

---

## 3. Mandatory Clipping Engine Standard

Whenever processing, sectioning, or splitting script text into video clips, agents **MUST ALWAYS** follow the 4-pass clipping engine documented in [`CLIPPING_ENGINE.md`](CLIPPING_ENGINE.md):

### Rule A: Sentence Boundary Integrity
- Clips must be formed around complete sentences by default.
- Never slice script text using arbitrary word/character offsets.

### Rule B: Micro-Sentence Consolidation (< 8 Words / < 3.0s)
- Micro-sentences (under 8 words or under 3 seconds) must be merged with adjacent sentences into logical chunks (~4s–7s) to prevent visual flickering.

### Rule C: Per-Clip Independent TTS Verification
- Every consolidated text chunk **MUST** be sent independently to `edge-tts` to retrieve its exact real-world audio duration.
- Never estimate clip lengths by math-slicing a continuous full-script TTS stream, as context drift and silence gaps cause offset inaccuracies.

### Rule D: Optimal Clause Splitting for Over-8s Sentences
- Any sentence exceeding 8.0 seconds must be split into Part A and Part B at the **furthest natural clause punctuation mark (`—`, `;`, `,`) under 8.0 seconds**.
- Both Part A and Part B must be re-sent independently to Edge TTS to verify that both sub-clips are ≤ 8.0 seconds.

### Rule E: Relation ID Tracking
- All split clips must carry a `parent_sentence_id` (e.g. `sec_01_sent_005`) and `relation_id` (e.g. `sec_01_sent_005_part_a`, `sec_01_sent_005_part_b`) in `clips_manifest.json`.
- This informs downstream LLMs (Gemini visual prompt generation) and Veo to maintain visual environment and character continuity across related clips.

---

## 4. Environment & Workspace Configuration
- Project defaults are stored in [`video_projects/_workspace_defaults.json`](video_projects/_workspace_defaults.json).
- Default GCP Project: `project-fb2dc00c-a54a-48bd-884`
- Default Location: `global`
- Default GCS Bucket: `my-video-automation-bucket-1`
- Default Voice: `en-US-AndrewNeural` (Rate: `-10%`, Pitch: `-15Hz`).

---

## 5. Key Execution Scripts

| Script | Purpose |
|---|---|
| [`scripts/generate_section_clips.py`](scripts/generate_section_clips.py) | STEP 1 — Script clipping engine |
| [`scripts/generate_visual_prompts.py`](scripts/generate_visual_prompts.py) | STEP 2 — Gemini visual prompt writer |
| [`generate_video.py`](generate_video.py) | STEP 3 — Veo video generation |
| [`scripts/download_and_cut_db_videos.py`](scripts/download_and_cut_db_videos.py) | STEP 4 — GCS download & initial cut |
| [`scripts/generate_project_audio.py`](scripts/generate_project_audio.py) | STEP 5 — Edge TTS sentence audio |
| [`scripts/recut_section_clips.py`](scripts/recut_section_clips.py) | STEP 7 — Smart re-cut to sync video to narration |
| [`mongo_store.py`](mongo_store.py) | Shared MongoDB schema & connection helper |

---

## 6. Sentence Clips & Audio Workflow Details

### Two Clip Arrays in clips_manifest.json

| Array | Purpose |
|---|---|
| `clips[]` | One entry per **video clip**. Sent to Veo for AI video generation. |
| `sentence_clips[]` | One entry per **original sentence**. Used for audio generation (TTS). |

For sentences that fit within one video clip, `sentence_clips` and `clips` have a 1:1 relationship.
For long sentences split across two clips, the `sentence_clips` entry contains the **full joined sentence** and references both `video_clip_numbers`.

### CRITICAL DESIGN DECISION — Sentences Only Audio Output (DO NOT CHANGE)
> **`generate_project_audio.py` produces ONLY sentence-level audio files in `sentences/`. Nothing else. This was an explicit user decision. Do NOT add `clips/`, `padded/`, or any intermediate folders. Ever.**

Slicing of split sentences to match individual video clip durations happens in `recut_section_clips.py` (Step 7), NOT during audio generation.

### sentence_clips Schema
```json
{
  "sentence_clip_number": 5,
  "parent_sentence_id": "sec_01_sent_005",
  "text": "Full original sentence text here.",
  "total_duration_seconds": 7.82,
  "word_count": 42,
  "video_clip_numbers": [8, 9],
  "part_durations": [
    { "clip_number": 8, "duration_seconds": 3.78 },
    { "clip_number": 9, "duration_seconds": 4.04 }
  ],
  "split": true
}
```

### CRITICAL — Veo Duration Selection Rule (Per Clip)
Veo supports only **4s, 6s, or 8s** durations. Each clip must be assigned the **smallest bracket ≥ its audio duration**:

```python
def get_veo_duration(audio_dur_seconds: float) -> int:
    """Return the correct Veo video duration for a given audio duration."""
    if audio_dur_seconds <= 4.0:
        return 4
    elif audio_dur_seconds <= 6.0:
        return 6
    else:
        return 8
```

This `veo_duration_seconds` (4, 6, or 8) must be stored on every entry in `clips[]` in `clips_manifest.json`:
```json
{
  "clip_number": 7,
  "duration_seconds": 2.208,
  "veo_duration_seconds": 4,
  ...
}
```

Do NOT request an 8s video for a 2s clip. Do NOT request a 6s video for a 4.5s clip. Always use the rule above.

### Re-cut Overflow Rule (for clips that would exceed their Veo source duration)
When a clip's required new duration exceeds its `veo_duration_seconds` source cap:
1. Cap the clip at its `veo_duration_seconds` value (4.0, 6.0, or 8.0)
2. Compute overflow = `required_dur - veo_duration_seconds`
3. Find the **shorter** of the clip's immediate neighbours (before and after)
4. Add overflow to that shorter neighbour

This preserves total duration accuracy while staying within each clip's actual source length.

### Key Rules for Agents
1. **ALWAYS read from `sentence_clips`** when generating audio — never iterate `clips[]` directly for TTS.
2. **Sentence audio is synthesized once** per sentence (skip-existing logic). Re-runs are safe.
3. **Never delete `downloaded/` raw clips** — Step 7 re-cuts from them.
4. **`clips_manifest.json` must contain `sentence_clips`** arrays. If missing, run `scripts/migrate_sentence_clips.js` to derive them from existing clips data without re-running TTS.
5. **Final deliverable** is always `exports/section_N_final.mp4` — produced by Step 7.
6. **NEVER request 8s from Veo for all clips.** Each clip in `clips_manifest.json` must have a `veo_duration_seconds` field (4, 6, or 8) computed from its audio duration using the bracket rule above. Sending the wrong duration wastes Veo compute quota and API time.
