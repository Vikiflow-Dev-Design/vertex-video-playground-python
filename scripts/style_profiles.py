from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STYLES_DIR = PROJECT_ROOT / "templates" / "styles"
DEFAULT_STYLE = "current"
FALLBACK_TEMPLATE = PROJECT_ROOT / "templates" / "visual_prompt_master_prompt.md"


def normalize_style_name(style: str | None) -> str:
    value = (style or DEFAULT_STYLE).strip().lower()
    if not value:
        return DEFAULT_STYLE
    slug = "".join(ch if ch.isalnum() else "-" for ch in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or DEFAULT_STYLE


def resolve_style_template_path(style: str | None) -> Path:
    normalized = normalize_style_name(style)
    candidate = STYLES_DIR / normalized / "visual_prompt_master_prompt.md"
    if candidate.exists():
        return candidate
    if normalized == DEFAULT_STYLE and FALLBACK_TEMPLATE.exists():
        return FALLBACK_TEMPLATE
    available = sorted(
        p.parent.name for p in STYLES_DIR.glob("*/visual_prompt_master_prompt.md") if p.is_file()
    )
    raise FileNotFoundError(
        f"Unknown video style '{style}'. Available styles: {', '.join(available) if available else 'none'}"
    )


def list_available_styles() -> list[str]:
    styles = sorted({p.parent.name for p in STYLES_DIR.glob("*/visual_prompt_master_prompt.md") if p.is_file()})
    if FALLBACK_TEMPLATE.exists() and DEFAULT_STYLE not in styles:
        styles.insert(0, DEFAULT_STYLE)
    return styles
