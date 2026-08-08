# Vendored UZI source

- Repository: `https://github.com/wbh604/UZI-Skill`
- Commit: `22e65f2e0cee2cb41cb40fdbe391c9600b7fe359`
- Version: `3.9.2`
- License: MIT; see `LICENSE`
- Imported capability roots:
  - `commands/*.md`
  - `skills/deep-analysis/scripts/**/*.py` excluding upstream tests
  - `skills/deep-analysis/assets/report-template.html`
  - `skills/deep-analysis/assets/{avatars,data-contracts,disclaimer,quality-checklist}`
  - `skills/deep-analysis/{SKILL.md,references/**,personas/**}`
  - `agents/investor-panel.md`

The files under `scripts/`, `commands/`, `assets/`, `skill/`, and `agents/` are pinned upstream
capability sources. Cognitrade integrates them through an in-process
DomainPack boundary and never requires an external UZI checkout, CLI, cache,
or plugin installation at runtime.
