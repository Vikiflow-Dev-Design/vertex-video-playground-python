# Video Projects

Each real video effort gets its own subfolder here.

Recommended structure:

- `source/` — raw narration, pasted text, or imported Google Doc content
- `clips/` — `*_clips.txt` files produced by the clip splitter
- `prompts/` — Gemini-generated visual prompt markdown
- `veo/` — downstream Veo request artifacts and result handles
- `logs/` — run logs and debugging output
- `instructions/` — the frozen prompt contract used for prompt generation

Suggested workflow:

1. Run `scripts/create_video_project.py` to create a new project folder.
2. Ingest the raw section script into `source/` with `scripts/ingest_sections.py`.
3. Generate section clip files with the clip splitter.
4. Run `scripts/generate_visual_prompts.py` to create one visual prompt batch per clip file.
5. Feed the prompt batches into the Veo generation step.
