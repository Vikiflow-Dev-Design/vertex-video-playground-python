# MASTER PROMPT: Section Clips → Veo Visual Prompts

You will be given a `section_clips.txt` file. It contains a numbered list of
narration clips, each already timed to fit an 8-second AI video generation
limit (Google Veo). Each clip entry looks like this:

    Clip N (X words, Y.YYs):
    <narration text for that clip>

Your job is to convert EVERY clip into exactly ONE visual/video generation
prompt describing what should be shown on screen while that line of
narration plays. Follow every rule below exactly.

---

## 1. ONE PROMPT PER CLIP — NO EXCEPTIONS

- The input will state "Total clips: N". Your output must contain EXACTLY N
  prompts. Not N+1, not N-1.
- Before finalizing your output, count your own prompts and count the
  clips in the input. If they don't match, find the mismatch and fix it
  before responding.
- Do not merge two clips into one prompt, and do not split one clip into
  two prompts, even if it feels natural to do so.

## 2. NUMBERING

- I will tell you what number to start counting from at the time I paste
  the clips (e.g. "start from 108"). If I don't specify, start from 001.
- Numbers are zero-padded to 3 digits (001, 002, ... 010, 011, ... 108,
  109, etc.).
- Numbering must be sequential with no gaps and no repeats.

## 3. OUTPUT FORMAT

- Format every entry exactly as:
  `NUMBER: prompt text here`
- Do NOT add section headings, part labels, or titles anywhere in the
  output (no "PART 1", no "— Founding —", nothing except the numbered
  prompts themselves).
- Do NOT add an aspect ratio tag (e.g. "16:9") anywhere in the prompt text.
- Separate each prompt from the next with exactly ONE blank line. Do NOT
  use a horizontal rule, dashes, "---", or any other divider between
  prompts — a blank line is the only separator.
- Deliver the final output as a single markdown (.md) file.

## 4. VISUAL CONTINUITY ACROSS CLIPS

The clips are sequential fragments of one continuous narration, so the
visuals must feel like one unfolding scene or storyline, not a set of
disconnected stock images.

- Read the FULL clip list first, before writing any prompts, so you
  understand the overall arc of the section.
- When the narration clearly continues the same moment, location, or
  character across multiple clips, make the prompts reflect that
  continuity (e.g. "the same commander," "the same city," "continuation
  of the previous shot") rather than reintroducing a generic new scene
  each time.
- Vary camera framing deliberately across the sequence (wide establishing
  shots, tracking shots, close-ups, aerials, slow push-ins) the way a real
  editor would cut a documentary — avoid using the same shot type for
  every single clip in a row.
- Each prompt should describe: the setting, what is visually happening,
  any figures present (see rules below), and a simple camera instruction
  (e.g. "Camera holds a slow push-in," "Camera tracks alongside," "Camera
  holds a wide aerial shot").

## 5. NEVER NAME REAL HISTORICAL FIGURES

- Even if the narration names a real historical person (e.g. an emperor,
  general, sultan, architect, founder), the VISUAL PROMPT must never use
  their name.
- Replace names with generic role descriptions instead: "a Roman
  emperor," "a robed church leader," "a Renaissance artist," "a general,
  "a settler," "a religious leader," etc.
- If the same real figure recurs across several clips, keep the SAME
  generic description consistent across all of them (e.g. always "the
  commander," not switching between "the general" and "the ruler") so the
  visual throughline still reads like one continuous character.

## 6. NEVER DEPICT MINORS

- Do not include children or minors in any prompt, under any framing —
  especially not in ceremonial, ritual, or altar-type scenes.
- If the narration implies families, populations, or crowds, depict adult
  figures only, or use object-focused / environmental shots instead of
  showing people directly.

## 7. VIOLENCE, DEATH, AND ATROCITY — IMPLY, DON'T DEPICT

- Do not describe graphic violence, blood, gore, or visible corpses, even
  when the narration describes battles, sieges, massacres, plagues, or
  executions.
- Convey these moments through environment and aftermath instead: smoke,
  rubble, abandoned streets, covered carts, overturned objects, distant or
  obscured figures, empty doorways, damaged structures.
- Keep combat and siege scenes wide, distant, and impressionistic rather
  than close and detailed.

## 8. HANDLING NON-VISUAL / META LINES

- Some clips are narration about the video itself (e.g. "We've used
  advanced AI to bring these paintings to life," "subscribe," "let's
  begin," "hit like"). These still need a prompt — interpret them
  visually in a way that fits the surrounding scene (e.g. an animated
  painting subtly gaining motion/depth) rather than skipping them or
  depicting the YouTube UI itself.

## 9. TONE OF THE PROMPTS THEMSELVES

- Write in clear, concrete, cinematic language — describe what a camera
  would actually see, not abstract concepts or emotions.
- Keep each prompt to roughly 2–4 sentences: enough detail to guide
  generation, not an overloaded paragraph.
- Do not use flowery or vague language ("a sense of destiny," "the
  weight of history") — describe the physical scene instead.

## 10. VIDEO STYLE BLOCK

Every individual prompt must end with a style block appended after the
scene description, separated by a line containing only `--`, like this:

    NUMBER: scene description here.

    --

    STYLE: style keywords here

- When I paste the section_clips.txt, I may also provide a style block at the
  same time (e.g. "STYLE: warm nostalgic 1980s home-video look, soft grain,
  handheld camera..."). If I provide one, use that exact style block,
  appended unchanged, on every prompt in that batch.
- If I do NOT provide a style block along with the clips, use this DEFAULT
  style on every prompt instead:

    STYLE: grounded cinematic historical realism, natural true-to-life
    color palette, soft natural daylight with balanced shadow contrast,
    weathered tactile textures, accurate filmic color grading, clear
    atmospheric depth, clean composed widescreen framing, subtle
    period-authentic environment styling, ultra-detailed photorealism,
    serious prestige-drama visual tone

- Never mix styles within a single batch — every prompt in one response
  uses the exact same style block, whether it's the one I supplied or the
  default above.
- The style block itself is never altered, paraphrased, shortened, or
  reworded — reproduce it exactly as given (mine or the default).

## 11. SELF-CHECK BEFORE RESPONDING

Before giving your final answer, verify:
- [ ] Number of prompts == number of clips in the input
- [ ] Numbering is sequential, correctly zero-padded, starting at the
      specified number
- [ ] No section headings or labels anywhere
- [ ] Every prompt ends with a `--` separator and a style block (mine
      if I gave one, the default otherwise) — and it's the SAME style
      block on every prompt in the batch
- [ ] No aspect ratio tag anywhere
- [ ] No dividers between prompts themselves — blank lines only
- [ ] No real historical names appear in any prompt
- [ ] No minors appear in any prompt
- [ ] No graphic violence/gore/bodies are explicitly depicted
- [ ] Visual continuity is maintained across consecutive related clips

---

When I paste a section_clips.txt file, respond with ONLY the finished
markdown file content — no explanation, no preamble, no commentary before
or after the prompts.
