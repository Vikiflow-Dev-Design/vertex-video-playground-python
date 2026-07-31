from __future__ import annotations

import pytest

from scripts.google_doc_tabs import (
    parse_script_blocks_from_tab_text,
    parse_tabbed_document,
    select_script_from_tabbed_document,
)


def _paragraph(text: str) -> dict:
    return {"paragraph": {"elements": [{"textRun": {"content": text}}]}}


def test_parse_tabbed_document_recurses_into_child_tabs() -> None:
    payload = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t-paper", "title": "paper"},
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph("SCRIPT NAME: sunrise\n"),
                            _paragraph("SCRIPT:\n"),
                            _paragraph("A paper-cutout city wakes up in the morning.\n"),
                        ]
                    }
                },
            },
            {
                "tabProperties": {"tabId": "t-group", "title": "group"},
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "t-3d", "title": "3d"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    _paragraph("SCRIPT NAME: robot-turns\n"),
                                    _paragraph("SCRIPT:\n"),
                                    _paragraph("A glossy 3D robot turns toward camera.\n"),
                                ]
                            }
                        },
                    }
                ],
            },
        ]
    }

    scripts = parse_tabbed_document(payload)
    assert [item.style for item in scripts] == ["paper", "3d"]
    assert [item.script_name for item in scripts] == ["sunrise", "robot-turns"]
    assert "paper-cutout city" in scripts[0].text
    assert "glossy 3D robot" in scripts[1].text


def test_select_script_from_tabbed_document_matches_style_and_name() -> None:
    payload = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t-current", "title": "current"},
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph("SCRIPT NAME: intro\n"),
                            _paragraph("SCRIPT:\n"),
                            _paragraph("Current style script text.\n"),
                        ]
                    }
                },
            },
            {
                "tabProperties": {"tabId": "t-paper", "title": "paper"},
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph("SCRIPT NAME: intro\n"),
                            _paragraph("SCRIPT:\n"),
                            _paragraph("Paper style script text.\n"),
                        ]
                    }
                },
            },
        ]
    }

    selected = select_script_from_tabbed_document(payload, style="paper", script_name="intro")
    assert selected.style == "paper"
    assert selected.script_name == "intro"
    assert "Paper style script text" in selected.text


def test_parse_script_blocks_from_tab_text_requires_selection_when_multiple_scripts() -> None:
    text = """SCRIPT NAME: one
SCRIPT:
One body.

SCRIPT NAME: two
SCRIPT:
Two body.
"""
    blocks = parse_script_blocks_from_tab_text(text, default_style="paper", tab_title="paper")
    assert [block.script_name for block in blocks] == ["one", "two"]
    assert blocks[0].style == "paper"
    assert blocks[1].style == "paper"
