#!/usr/bin/env python3
"""Send clip batches to Gemini and save the finished visual prompts.

The script reads every `*_clips.txt` file in a project's clips/ directory,
sends clips to Gemini in small batches, validates that the output has exactly
one prompt per clip, and writes the accumulated response to prompts/.

A per-file state file tracks progress so reruns can resume after interruption.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig
from scripts.style_profiles import resolve_style_template_path
from scripts.continuity import (
    build_continuity_context,
    load_continuity_manifest,
    validate_continuity_manifest,
)

try:
    from google.genai.types import ThinkingConfig
except Exception:  # pragma: no cover - older SDKs may omit it
    ThinkingConfig = None

load_dotenv()

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "video_projects"
DEFAULT_MASTER_PROMPT = Path(__file__).resolve().parents[1] / "templates" / "visual_prompt_master_prompt.md"
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_BATCH_SIZE = int(os.getenv("GEMINI_PROMPT_BATCH_SIZE", "5"))
PROJECT_PROMPTS_BASENAME = "visual_prompts"

TOTAL_CLIPS_RE = re.compile(r"^Total clips:\s*(\d+)\s*\r?$", re.MULTILINE)
CLIP_HEADER_RE = re.compile(r"^Clip\s+(\d+)\s+\((\d+)\s+words,\s+([0-9.]+)s\):\s*\r?$", re.MULTILINE)
PROMPT_LINE_RE = re.compile(r"^(\d{3}):\s+(.+)\r?$", re.MULTILINE)
STYLE_RE = re.compile(r"^STYLE:\s+.+\r?$", re.MULTILINE)


@dataclass
class ClipEntry:
    number: int
    text: str
    word_count: int
    duration_seconds: float


@dataclass
class PromptState:
    processed_clips: int = 0
    completed_batches: int = 0
    last_clip_number: int = 0
    last_prompt_text: str = ""
    complete: bool = False


def resolve_project_dir(project: str, base_dir: Path) -> Path:
    path = Path(project).expanduser()
    if path.exists():
        return path.resolve()
    return (base_dir / project).resolve()


def read_master_prompt(project_dir: Path, style: str | None = None) -> str:
    if style:
        project_style_prompt = project_dir / "instructions" / "styles" / style / "visual_prompt_master_prompt.md"
        if project_style_prompt.exists():
            return project_style_prompt.read_text(encoding="utf-8")
        styled_prompt = resolve_style_template_path(style)
        if styled_prompt.exists():
            return styled_prompt.read_text(encoding="utf-8")
    project_prompt = project_dir / "instructions" / "visual_prompt_master_prompt.md"
    if project_prompt.exists():
        return project_prompt.read_text(encoding="utf-8")
    if DEFAULT_MASTER_PROMPT.exists():
        return DEFAULT_MASTER_PROMPT.read_text(encoding="utf-8")
    raise FileNotFoundError("Master prompt template not found")


def parse_clip_metadata(text: str) -> dict[str, str | None]:
    style_match = re.search(r"^STYLE:\s*(.+?)\s*$", text, re.MULTILINE)
    tab_match = re.search(r"^TAB TITLE:\s*(.+?)\s*$", text, re.MULTILINE)
    script_match = re.search(r"^SCRIPT NAME:\s*(.+?)\s*$", text, re.MULTILINE)
    return {
        "style": style_match.group(1).strip() if style_match else None,
        "tab_title": tab_match.group(1).strip() if tab_match else None,
        "script_name": script_match.group(1).strip() if script_match else None,
    }


def parse_total_clips(text: str) -> int:
    match = TOTAL_CLIPS_RE.search(text)
    if not match:
        raise ValueError("Could not find `Total clips:` in clip file")
    return int(match.group(1))


def parse_clip_entries(text: str) -> list[ClipEntry]:
    matches = list(CLIP_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError("Could not find any clip blocks in clip file")

    clips: list[ClipEntry] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        clip_text = text[start:end].strip()
        first_line = clip_text.splitlines()[0].strip() if clip_text.splitlines() else ""
        clips.append(ClipEntry(
            number=int(match.group(1)),
            text=first_line,
            word_count=int(match.group(2)),
            duration_seconds=float(match.group(3)),
        ))
    return clips


def validate_batch(text: str, expected_total: int) -> tuple[bool, dict[str, object]]:
    prompt_count = len(PROMPT_LINE_RE.findall(text))
    style_count = len(STYLE_RE.findall(text))
    ok = prompt_count == expected_total and style_count == expected_total
    return ok, {
        "expected_total": expected_total,
        "prompt_count": prompt_count,
        "style_count": style_count,
        "ok": ok,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Gemini visual prompts for clip batches")
    parser.add_argument("--project", required=True, help="Project slug or project directory")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Base directory for video projects")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument("--clips-file", default=None, help="Optional single clip file to process")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="How many clips to send per Gemini request")
    parser.add_argument("--temperature", type=float, default=0.2, help="Generation temperature")
    parser.add_argument("--thinking-budget", type=int, default=0, help="Optional thinking budget tokens")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without calling Gemini")
    return parser.parse_args()


def create_client(project_id: Optional[str] = None, location: Optional[str] = None) -> genai.Client:
    project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    loc = location or os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex Gemini calls")
    return genai.Client(vertexai=True, project=project, location=loc)


def send_to_gemini(client: genai.Client, model: str, master_prompt: str, clip_payload: str, temperature: float, thinking_budget: int) -> str:
    import time
    import random

    config_kwargs = {
        "systemInstruction": master_prompt,
        "temperature": temperature,
        "maxOutputTokens": 8192,
        "responseMimeType": "text/plain",
    }
    if thinking_budget > 0 and ThinkingConfig is not None:
        config_kwargs["thinkingConfig"] = ThinkingConfig(thinking_budget=thinking_budget)

    max_retries = 5
    backoff_factor = 2.0
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=model, contents=clip_payload, config=GenerateContentConfig(**config_kwargs))
            text = getattr(response, "text", None)
            if text:
                return text.strip()
            parts: list[str] = []
            for candidate in getattr(response, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", []) or []:
                    if getattr(part, "text", None):
                        parts.append(part.text)
            if not parts:
                raise RuntimeError("Gemini returned no text output")
            return "".join(parts).strip()
        except Exception as e:
            e_str = str(e)
            is_rate_limit = "429" in e_str or "RESOURCE_EXHAUSTED" in e_str
            if is_rate_limit and attempt < max_retries - 1:
                sleep_time = (backoff_factor ** attempt) + random.uniform(1.0, 3.0)
                print(f"[WARN] Gemini API rate limit hit (429/RESOURCE_EXHAUSTED). Retrying in {sleep_time:.2f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(sleep_time)
            else:
                raise e


def chunked(items: list[ClipEntry], size: int) -> list[list[ClipEntry]]:
    if size <= 0:
        raise ValueError("batch size must be greater than zero")
    return [items[i:i + size] for i in range(0, len(items), size)]


def discover_clip_files(project_dir: Path, clips_file: str | None = None) -> list[Path]:
    clips_dir = project_dir / "clips"
    if clips_file:
        candidate = Path(clips_file).expanduser()
        if not candidate.is_absolute():
            candidate = clips_dir / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"Clip file not found: {candidate}")
        return [candidate.resolve()]
    clip_files = sorted(clips_dir.glob("*_clips.txt"))
    if not clip_files:
        raise SystemExit(f"No clip files found in {clips_dir}")
    return clip_files


def build_output_paths(prompt_dir: Path) -> tuple[Path, Path, Path]:
    output_path = prompt_dir / f"{PROJECT_PROMPTS_BASENAME}.md"
    state_path = prompt_dir / f"{PROJECT_PROMPTS_BASENAME}.state.json"
    summary_path = prompt_dir / f"{PROJECT_PROMPTS_BASENAME}.json"
    return output_path, state_path, summary_path


def write_prompt_header(output_path: Path, clip_file: Path, style: str | None, script_name: str | None, tab_title: str | None) -> None:
    lines = [
        f"## {clip_file.name}",
    ]
    if style:
        lines.append(f"FILE STYLE: {style}")
    if tab_title:
        lines.append(f"FILE TAB: {tab_title}")
    if script_name:
        lines.append(f"FILE SCRIPT: {script_name}")
    lines.append("")
    prefix = "\n" if output_path.exists() and output_path.stat().st_size > 0 else ""
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(prefix)
        fh.write("\n".join(lines))


def run_prompt_generation(
    project_dir: Path,
    *,
    clips_file: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    model: str | None = None,
    temperature: float = 0.2,
    thinking_budget: int = 0,
    dry_run: bool = False,
    client_factory=create_client,
    send_fn=send_to_gemini,
) -> dict[str, object]:
    project_manifest_path = project_dir / "project.json"
    project_manifest = json.loads(project_manifest_path.read_text(encoding="utf-8")) if project_manifest_path.exists() else {}
    project_model = model or project_manifest.get("gemini_model") or DEFAULT_MODEL
    clip_files = discover_clip_files(project_dir, clips_file)
    prompt_dir = project_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    output_path, state_path, summary_path = build_output_paths(prompt_dir)
    state = load_state(state_path)

    raw_script_path = project_dir / "source" / "sections_raw.txt"
    raw_script = raw_script_path.read_text(encoding="utf-8") if raw_script_path.exists() else ""
    clips_details = load_clips_details(project_dir)
    continuity_manifest = load_continuity_manifest(project_dir)
    continuity_manifest_path = project_dir / "continuity" / "continuity.json"
    if continuity_manifest_path.exists():
        continuity_errors = validate_continuity_manifest(continuity_manifest, project_dir=project_dir)
        if continuity_errors:
            raise ValueError("Continuity manifest validation failed: " + "; ".join(continuity_errors))

    clip_sets: list[dict[str, object]] = []
    total_expected = 0
    for clip_file in clip_files:
        clip_text = clip_file.read_text(encoding="utf-8")
        metadata = parse_clip_metadata(clip_text)
        style = metadata.get("style") or project_manifest.get("style")
        
        master_prompt = read_master_prompt(project_dir, style)
        continuity_rules = (
            "\n\n=== ADDITIONAL MANDATORY PROMPT GUIDELINES ===\n"
            "1. CONTEXT-AWARE CONTINUITY: You will receive the entire raw script for context. "
            "Use it to ensure visual transitions match the flow of the narrative.\n"
            "2. EXPLICIT VISUAL ANCHORS: Always explicitly describe the environment, subject (clothing, dressing, appearance), action, lighting, and camera movement in detail for every prompt.\n"
            "3. RELATION ID RULE: If two or more clips in the batch share the same 'Parent ID' (e.g. they are Part A and Part B of a split sentence, labeled as 'split_part_a' and 'split_part_b'), you MUST copy the exact visual anchor description (the subject look, clothing, and environment detail) verbatim into both prompts. Only modify the camera movement or progress of the physical action to simulate a single continuous camera shot.\n"
            "4. PACING MATCHING: Match the complexity of the prompt action to the clip's 'Duration'. Short clips (<= 4s) must feature slow, static, or simple camera movements. Longer clips (6s to 8s) can feature progressive movements or actions."
        )
        master_prompt = master_prompt + continuity_rules

        expected_total = parse_total_clips(clip_text)
        clips = parse_clip_entries(clip_text)
        total_expected += expected_total
        clip_sets.append(
            {
                "clip_file": clip_file,
                "metadata": metadata,
                "style": style,
                "master_prompt": master_prompt,
                "expected_total": expected_total,
                "clips": clips,
            }
        )

    if dry_run:
        return {
            "project_dir": str(project_dir),
            "clip_files": [str(path) for path in clip_files],
            "output_path": str(output_path),
            "state_path": str(state_path),
            "summary_path": str(summary_path),
            "batch_size": batch_size,
            "model": project_model,
            "resume_from": state.processed_clips,
            "expected_total": total_expected,
        }

    if state.processed_clips and not output_path.exists():
        raise RuntimeError(f"State exists for {project_dir.name} but output file is missing: {output_path}")
    if not state.processed_clips:
        output_path.write_text("", encoding="utf-8")

    client = client_factory(project_manifest.get("gcp_project_id"), project_manifest.get("gcp_location"))
    outputs: list[dict[str, object]] = []
    remaining_to_skip = state.processed_clips

    for clip_set in clip_sets:
        clip_file = clip_set["clip_file"]
        assert isinstance(clip_file, Path)
        clips = clip_set["clips"]
        assert isinstance(clips, list)
        if remaining_to_skip >= len(clips):
            remaining_to_skip -= len(clips)
            continue

        active_clips = clips[remaining_to_skip:]
        remaining_to_skip = 0
        if not active_clips:
            continue

        metadata = clip_set["metadata"] if isinstance(clip_set["metadata"], dict) else {}
        style = clip_set["style"] if isinstance(clip_set["style"], str) else None
        expected_total_obj = clip_set["expected_total"]
        assert isinstance(expected_total_obj, int)
        expected_total = expected_total_obj
        write_prompt_header(
            output_path,
            clip_file,
            style,
            metadata.get("script_name"),
            metadata.get("tab_title"),
        )

        continuity_reference = state.last_prompt_text or None
        for batch_index, batch in enumerate(chunked(active_clips, batch_size), start=1):
            batch_start = batch[0].number
            batch_end = batch[-1].number
            payload = build_batch_payload(
                batch,
                batch_start,
                batch_end,
                expected_total,
                continuity_reference,
                full_script=raw_script,
                clips_details=clips_details,
                continuity_manifest=continuity_manifest,
                project_dir=project_dir,
            )
            generated = send_fn(client, project_model, clip_set["master_prompt"], payload, temperature, thinking_budget)
            normalized = renumber_generated_text(generated, batch_start)
            ok, validation = validate_batch(normalized, len(batch))
            if not ok:
                raise RuntimeError(f"Validation failed for batch {batch_index} of {clip_file.name}: {validation}")

            prefix = "\n" if output_path.exists() and output_path.stat().st_size > 0 else ""
            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(prefix)
                fh.write(normalized.rstrip())
                fh.write("\n")

            state.processed_clips += len(batch)
            state.completed_batches += 1
            state.last_clip_number = batch_end
            state.last_prompt_text = split_prompt_blocks(normalized)[-1]
            save_state(state_path, state, clip_file, output_path, batch_size)
            continuity_reference = state.last_prompt_text

        outputs.append({"clip_file": str(clip_file), "batch_count": len(chunked(active_clips, batch_size))})

    final_text = output_path.read_text(encoding="utf-8")
    overall_validation = validate_batch(final_text, total_expected)[1]
    state.complete = bool(overall_validation["ok"])
    save_state(state_path, state, clip_files[0], output_path, batch_size)
    summary_path.write_text(json.dumps({
        "clip_files": [str(path) for path in clip_files],
        "output_path": str(output_path),
        "model": project_model,
        "batch_size": batch_size,
        "validation": overall_validation,
        "complete": state.complete,
    }, indent=2) + "\n", encoding="utf-8")

    if not overall_validation["ok"]:
        raise RuntimeError(f"Validation failed for {project_dir.name}: {overall_validation}")

    return {
        "project_dir": str(project_dir),
        "clip_files": [str(path) for path in clip_files],
        "output_path": str(output_path),
        "summary_path": str(summary_path),
        "state_path": str(state_path),
        "batch_size": batch_size,
        "model": project_model,
        "outputs": outputs,
        "validation": overall_validation,
        "complete": state.complete,
    }


def load_clips_details(project_dir: Path) -> dict[int, dict[str, object]]:
    manifest_path = project_dir / "clips" / "clips_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        details = {}
        for section in payload.get("sections", []) or []:
            for clip in section.get("clips", []) or []:
                clip_num = clip.get("global_clip_number") or clip.get("clip_number")
                if clip_num is not None:
                    details[int(clip_num)] = clip
        return details
    except Exception:
        return {}


def build_batch_payload(
    batch: list[ClipEntry],
    batch_start: int,
    batch_end: int,
    total: int,
    continuity_reference: str | None,
    full_script: str = "",
    clips_details: dict[int, dict[str, object]] | None = None,
    continuity_manifest: dict[str, object] | None = None,
    project_dir: Path | None = None,
) -> str:
    lines = [
        "Generate visual prompts for the requested clips in this batch.",
        "",
        "CONTEXT:",
        "Below is the complete raw script. Read the whole script to understand the narrative flow, historical backdrop, emotional tone, and scene transitions:",
        "--- START OF SCRIPT ---",
        full_script.strip(),
        "--- END OF SCRIPT ---",
        "",
        "BATCH CLIPS INSTRUCTION:",
        "- You must output exactly one visual prompt per clip in the batch.",
        "- Keep the numbering sequential starting at the first clip number shown below.",
        "- Maintain absolute character appearance, location environment, and clothing continuity for related clips.",
        "- Output plain text in the format: '<number>: <PromptText>' separated by '--' style blocks.",
        "",
    ]
    if continuity_reference:
        lines.extend([
            "Previous batch continuity reference:",
            continuity_reference.strip(),
            "",
        ])
    lines.append("CLIPS FOR THIS BATCH:")
    for clip in batch:
        details = (clips_details.get(clip.number) if clips_details else None) or {}
        relation_id = details.get("relation_id") or f"sent_{clip.number:03d}_standalone"
        parent_id = details.get("parent_sentence_id") or f"sent_{clip.number:03d}"
        duration = details.get("duration_seconds") or 5.0
        status = details.get("status") or "standalone"
        relation_str = f" [Relation ID: {relation_id}, Parent ID: {parent_id}, Duration: {duration}s, Status: {status}]"
        lines.append(f"Clip {clip.number:03d}{relation_str}: {clip.text}")
        if continuity_manifest is not None and project_dir is not None:
            lines.extend([
                build_continuity_context(clip.number, continuity_manifest, project_dir=project_dir),
                "",
            ])
    return "\n".join(lines).strip() + "\n"


def split_prompt_blocks(text: str) -> list[str]:
    matches = list(PROMPT_LINE_RE.finditer(text))
    if not matches:
        raise ValueError("Could not find any numbered prompts in Gemini output")
    blocks: list[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append(text[start:end].strip())
    return blocks


def renumber_prompt_block(text: str, number: int) -> str:
    lines = text.strip().splitlines()
    if not lines:
        raise ValueError("Empty prompt block")
    first = lines[0]
    match = PROMPT_LINE_RE.match(first)
    if match:
        lines[0] = f"{number:03d}: {match.group(2)}"
    else:
        lines.insert(0, f"{number:03d}: {first}")
    return "\n".join(lines).strip()


def renumber_generated_text(text: str, start_number: int) -> str:
    blocks = split_prompt_blocks(text)
    renumbered = [renumber_prompt_block(block, start_number + idx) for idx, block in enumerate(blocks)]
    return "\n\n".join(renumbered).strip() + "\n"


def load_state(state_path: Path) -> PromptState:
    if not state_path.exists():
        return PromptState()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return PromptState(
        processed_clips=int(payload.get("processed_clips", 0)),
        completed_batches=int(payload.get("completed_batches", 0)),
        last_clip_number=int(payload.get("last_clip_number", 0)),
        last_prompt_text=str(payload.get("last_prompt_text", "")),
        complete=bool(payload.get("complete", False)),
    )


def save_state(state_path: Path, state: PromptState, clip_file: Path, output_path: Path, batch_size: int) -> None:
    payload = {
        "clip_file": str(clip_file),
        "output_path": str(output_path),
        "batch_size": batch_size,
        **asdict(state),
    }
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    project_dir = resolve_project_dir(args.project, base_dir)
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory does not exist: {project_dir}")

    result = run_prompt_generation(
        project_dir,
        clips_file=args.clips_file,
        batch_size=args.batch_size,
        model=args.model,
        temperature=args.temperature,
        thinking_budget=args.thinking_budget,
        dry_run=args.dry_run,
    )
    print(json.dumps({"status": "ok", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
