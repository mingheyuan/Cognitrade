# Vendored UZI source

- Repository: `https://github.com/wbh604/UZI-Skill`
- Analysis commit: `22e65f2e0cee2cb41cb40fdbe391c9600b7fe359`
- Analysis version: `3.9.2`
- Report presentation commit: `0acf25122cabf493e7bc442faff62ceceb7f2f02`
- Report presentation version: `3.9.4`
- License: MIT; see `LICENSE`
- Imported capability roots:
  - `commands/*.md`
  - `skills/deep-analysis/scripts/**/*.py` excluding upstream tests
  - `skills/deep-analysis/assets/report-template.html` from the report presentation commit
  - `skills/deep-analysis/scripts/lib/report/{dim_viz,institutional,panel_cards,svg_primitives}.py`
    from the report presentation commit
  - `skills/deep-analysis/assets/{avatars,data-contracts,disclaimer,quality-checklist}`
  - `skills/deep-analysis/{SKILL.md,references/**,personas/**}`
  - `agents/investor-panel.md`

Except for the explicitly listed report presentation overlay, the files under
`scripts/`, `commands/`, `assets/`, `skill/`, and `agents/` are pinned to the
analysis commit. Cognitrade integrates them through an in-process DomainPack
boundary and never requires an external UZI checkout, CLI, cache, or plugin
installation at runtime.
