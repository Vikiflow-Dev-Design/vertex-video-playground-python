from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import dataclass
import re
from typing import Any

from scripts.style_profiles import normalize_style_name

SCRIPT_NAME_RE = re.compile(r"^SCRIPT NAME:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
SCRIPT_BODY_MARKER_RE = re.compile(r"^SCRIPT:\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class ScriptBlock:
    style: str
    tab_title: str
    tab_id: str
    script_name: str
    text: str


def normalize_text_key(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip()).strip("-")


def extract_doc_id(url: str) -> str:
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Could not extract Google Doc id from URL: {url}")
    return match.group(1)


def extract_text_from_content(content: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in content or []:
        if "paragraph" in block:
            paragraph = block.get("paragraph", {})
            text_parts: list[str] = []
            for element in paragraph.get("elements", []):
                text_run = element.get("textRun")
                if text_run:
                    text_parts.append(text_run.get("content", ""))
            if text_parts:
                lines.append("".join(text_parts).rstrip("\n"))
            else:
                lines.append("")
            continue
        if "table" in block:
            table = block.get("table", {})
            for row in table.get("tableRows", []):
                row_text: list[str] = []
                for cell in row.get("tableCells", []):
                    row_text.append(extract_text_from_content(cell.get("content", [])))
                lines.append("\t".join(part for part in row_text if part))
            continue
        if "sectionBreak" in block:
            lines.append("")
    return "\n".join(line for line in lines).strip()


def _extract_tab_text(node: dict[str, Any]) -> str:
    document_tab = node.get("documentTab") or {}
    body = document_tab.get("body") or node.get("body") or {}
    return extract_text_from_content(body.get("content", []))


def _iter_tabs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tabs = payload.get("tabs", []) or []
    ordered: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        ordered.append(node)
        for child in node.get("childTabs", []) or []:
            visit(child)

    for tab in tabs:
        visit(tab)
    return ordered


def _parse_script_blocks(text: str, *, default_style: str, tab_title: str, tab_id: str) -> list[ScriptBlock]:
    stripped = text.strip()
    if not stripped:
        return []

    matches = list(SCRIPT_NAME_RE.finditer(text))
    if not matches:
        return [
            ScriptBlock(
                style=normalize_style_name(default_style or tab_title),
                tab_title=tab_title,
                tab_id=tab_id,
                script_name=tab_title or "script-1",
                text=stripped,
            )
        ]

    blocks: list[ScriptBlock] = []
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        body = SCRIPT_BODY_MARKER_RE.sub("", body, count=1).strip()
        blocks.append(
            ScriptBlock(
                style=normalize_style_name(default_style or tab_title),
                tab_title=tab_title,
                tab_id=tab_id,
                script_name=name,
                text=body,
            )
        )
    return blocks


def parse_script_blocks_from_tab_text(text: str, default_style: str, tab_title: str, tab_id: str = "") -> list[ScriptBlock]:
    return _parse_script_blocks(text, default_style=default_style, tab_title=tab_title, tab_id=tab_id)


def parse_tabbed_document(payload: dict[str, Any]) -> list[ScriptBlock]:
    scripts: list[ScriptBlock] = []
    for tab in _iter_tabs(payload):
        props = tab.get("tabProperties", {}) or {}
        tab_title = props.get("title", "") or ""
        tab_id = props.get("tabId", "") or ""
        tab_text = _extract_tab_text(tab)
        scripts.extend(_parse_script_blocks(tab_text, default_style=tab_title, tab_title=tab_title, tab_id=tab_id))
    return scripts


def select_script_from_tabbed_document(payload: dict[str, Any], *, style: str, script_name: str | None = None) -> ScriptBlock:
    scripts = parse_tabbed_document(payload)
    wanted_style = normalize_style_name(style)
    matching_style = [item for item in scripts if normalize_style_name(item.style) == wanted_style]
    if not matching_style:
        available = sorted({normalize_style_name(item.style) for item in scripts if item.style})
        raise ValueError(
            f"Could not find a tab styled '{style}'. Available styles: {', '.join(available) if available else 'none'}"
        )

    if script_name:
        wanted_script = normalize_text_key(script_name)
        matching_script = [item for item in matching_style if normalize_text_key(item.script_name) == wanted_script]
        if not matching_script:
            available_scripts = ", ".join(item.script_name for item in matching_style)
            raise ValueError(
                f"Could not find script '{script_name}' in style '{style}'. Available scripts: {available_scripts}"
            )
        return matching_script[0]

    if len(matching_style) == 1:
        return matching_style[0]

    available_scripts = ", ".join(item.script_name for item in matching_style)
    raise ValueError(
        f"Style '{style}' contains multiple scripts. Pass --script-name. Available scripts: {available_scripts}"
    )
