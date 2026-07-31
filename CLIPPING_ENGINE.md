# Sentence-Aware Optimal Clipping Engine Specification

This document defines the architecture, rules, and manifest schema for the **Sentence-Aware Optimal Clipping Engine** in `vertex-video-playground`.

---

## 1. Overview & Objective

The objective of the clipping engine is to transform raw narration text into discrete, perfectly timed video clip units suitable for Google Vertex AI Veo generation (`veo-3.1-lite-generate-001`).

### The Core Problem Solved
Traditional clipping engines split text by raw character/word counts or arbitrary 8-second time offsets from a single audio stream. This caused two major defects:
1. **Mid-Sentence Cuts**: Video cuts occurred in the middle of spoken words or phrases, breaking narrative and visual flow.
2. **Context Drift & Silence Gap Errors**: Single-stream TTS offsets drift from isolated clip generation because neural TTS engines adjust speech speed and breath pauses dynamically based on surrounding text context.

---

## 2. The 4-Pass Clipping Algorithm

To achieve 100% audio timing accuracy and visual continuity, the engine executes four sequential passes:

```
[Raw Script] 
     │
     ▼
┌────────────────────────────────────────────────────────┐
│ Pass 1: Sentence Parsing & Micro-Consolidation         │
│ (Group sentences by . ! ?, merge micro-sentences < 8w) │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Pass 2: Independent Per-Clip Edge TTS Verification     │
│ (Send each text chunk independently to Edge TTS)       │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Pass 3: Optimal Clause Splitting for Over-8s Sentences │
│ (Split > 8.0s at furthest em-dash/semicolon/comma)     │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Pass 4: Relation ID & Manifest Generation             │
│ (Tag linked clips, output txt & clips_manifest.json)   │
└────────────────────────────────────────────────────────┘
```

### Pass 1: Sentence Parsing & Micro-Consolidation
- Script text is split into sentences using standard punctuation regex `(?<=[.!?])\s+`.
- Micro-sentences (under 8 words or under 3.0s, e.g. *"One hill."*, *"Let's begin."*) are merged with adjacent sentences so every clip represents a complete, logical thought.

### Pass 2: Per-Clip Independent TTS Verification
- Each consolidated text chunk is sent to Edge TTS independently (`edge_tts.Communicate`).
- **Configuration**:
  - Voice: `en-US-AndrewNeural`
  - Speed Rate: `-10%`
  - Pitch: `-15Hz`
- **Result**: Retrieves the exact, real-world audio duration for that specific chunk without offset math errors.

### Pass 3: Optimal Clause Splitting (> 8.0s)
- If Edge TTS returns a duration greater than **8.0 seconds**:
  1. Scans the text for natural clause punctuation marks (`—`, `;`, `,`).
  2. Selects the **furthest punctuation mark under 8.0 seconds**.
  3. Splits the sentence into **Part A** and **Part B**.
  4. Re-sends Part A and Part B to Edge TTS independently to verify that both sub-clips are $\le 8.0$ seconds.

### Pass 4: Relation ID Tracking & Manifest Output
- Assigns relation identifiers:
  - `parent_sentence_id`: e.g. `sec_01_sent_005`
  - `relation_id`: e.g. `sec_01_sent_005_part_a`, `sec_01_sent_005_part_b`, `sec_01_sent_001_standalone`
- Generates formatted section clip text files (`clips/001-section-1_clips.txt`) and master `clips/clips_manifest.json`.

---

## 3. Master Manifest Schema (`clips_manifest.json`)

The manifest now outputs **two clip arrays per section**: `clips[]` for video generation and `sentence_clips[]` for audio generation.

```json
{
  "project_slug": "the-entire-history-of-rome",
  "voice": "en-US-AndrewNeural",
  "rate": "-10%",
  "pitch": "-15Hz",
  "clip_seconds": 8.0,
  "section_count": 9,
  "sections": [
    {
      "section_index": 1,
      "section_title": "Section 1",
      "total_clips": 23,
      "total_sentence_clips": 20,
      "output_file": "clips/001-section-1_clips.txt",
      "clips": [
        {
          "global_clip_number": 6,
          "clip_number": 6,
          "parent_sentence_id": "sec_01_sent_005",
          "relation_id": "sec_01_sent_005_part_a",
          "text": "Nowhere else on earth do so many people believe so deeply",
          "duration_seconds": 7.89,
          "word_count": 10,
          "status": "split_part_a"
        }
      ],
      "sentence_clips": [
        {
          "sentence_clip_number": 5,
          "parent_sentence_id": "sec_01_sent_005",
          "text": "Nowhere else on earth do so many people believe so deeply that this particular piece of ground belongs to them.",
          "total_duration_seconds": 7.89,
          "word_count": 20,
          "video_clip_numbers": [6, 7],
          "part_durations": [
            { "clip_number": 6, "duration_seconds": 4.96 },
            { "clip_number": 7, "duration_seconds": 2.93 }
          ],
          "split": true
        }
      ]
    }
  ]
}
```

> **Note**: A companion file `clips/001-section-1_sentence_clips.json` is also written alongside each `*_clips.txt` file for easy per-section access.

---

## 4. Downstream Integration Guidelines

### Gemini Visual Prompt Generation (`generate_visual_prompts.py`)
- Reads `parent_sentence_id` and `relation_id` from `clips[]`.
- When generating visual prompts for linked sub-clips (`part_a` and `part_b`), Gemini is instructed to maintain **visual scene continuity** (same environment, lighting, characters, and camera movement flow).

### Audio Generation (`generate_project_audio.py`)
- Reads `sentence_clips[]` — **not** `clips[]` — for TTS synthesis.
- Synthesizes one full MP3 per sentence. Split sentences are synthesized as a single joined audio file.
- Outputs to `audio/section_N/sentences/sent_001.mp3 ...`

### Smart Re-cut & Final Assembly (`recut_section_clips.py`)
- Reads both `clips[]` (for split ratios) and `sentence_clips[]` (for durations).
- Probes actual sentence audio durations via ffprobe.
- Re-trims raw Veo 8s clips from `veo/trimmed/downloaded/` to exactly match audio.
- Handles overflow (clip needs > 8s) by redistributing to shorter neighbour clip.
- Produces final `exports/section_N_final.mp4` with video and narration in perfect sync.

---

## 5. Verification Reference Project
- Working reference code: [`scratch/run_full_clipping_test.py`](file:///c:/Users/victor/Desktop/google-cloud-video-automation/vertex-video-playground/vertex-video-playground/scratch/run_full_clipping_test.py)
- Verified test project outputs: [`video_projects/jerusalem-full-test/clips/`](file:///c:/Users/victor/Desktop/google-cloud-video-automation/vertex-video-playground/vertex-video-playground/video_projects/jerusalem-full-test/clips/) (283 clips across 9 sections, 0 clips > 8s).
