# Vendored UZI source

- Repository: `https://github.com/wbh604/UZI-Skill`
- Analysis commit: `0acf25122cabf493e7bc442faff62ceceb7f2f02`
- Analysis version: `3.9.4`
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

The files under `scripts/`, `commands/`, `assets/`, and `skill/` are pinned to
the same upstream commit. Cognitrade integrates them through an in-process DomainPack
boundary and never requires an external UZI checkout, CLI, cache, or plugin
installation at runtime.
