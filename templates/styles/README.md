# Video Style Profiles

A style profile is a rendering contract, not a content summary. Each profile is
copied into a project's `instructions/styles/<style>/` directory and frozen for
that project's lifetime.

## Profiles

- `current/` — existing grounded cinematic historical realism contract.
- `paper/` — legacy profile retained for existing projects.
- `2d/` — legacy profile retained for existing projects.
- `3d/` — legacy profile retained for existing projects.
- `low-poly-3d/` — stylized low-poly polygonal 3D cinematic contract.
- `fern-animation/` — original Fern-inspired documentary animation contract.

## Fern-animation

Use `fern-animation` when the project needs scene-first documentary storytelling
with restrained tactile 2.5D motion. Read all five files in the profile before
creating prompts:

- `visual_prompt_master_prompt.md`
- `style_bible.yaml`
- `camera_language.md`
- `motion_rules.md`
- `negative_prompt.md`

Do not copy another creator's exact visual identity. Use the profile as a stable
house style and combine it with the project's continuity registry.
