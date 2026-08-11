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
  - `skills/{investor-panel,lhb-analyzer,trap-detector}/**`
  - `agents/investor-panel.md`

The files under `scripts/`, `commands/`, `assets/`, `skill/`, and `skills/` are
pinned to the same upstream commit. The historical `skill/`, `scripts/`, and
`assets/` directories map respectively to upstream
`skills/deep-analysis/{SKILL.md,references,personas}`, `skills/deep-analysis/scripts`,
and `skills/deep-analysis/assets`. The three standalone methodology trees retain
their canonical upstream-relative paths under `skills/` so their source material
is auditable next to the implementations that were translated into DomainPack
capabilities.

Cognitrade does not dynamically execute UZI Markdown skills. Agent OS owns routing,
and the in-process DomainPack implements the mapped commands, methods, fetchers,
panel, and trap checks. The vendored standalone skill files are pinned methodology
references, not a hidden second runtime. Agent OS reproduces the root router's
resolved-equity collection branches in process, while the two CLI entrypoints,
installer/plugin manifests, documentation, screenshots, and test tree remain
non-production dependencies. CI checks out the fixed upstream
commit separately and runs its test tree against the vendored Python implementation.
UZI collection keeps the upstream executor semantics: the documented wave values
classify unfinished dimensions but cannot force-cancel an already running Python
thread. Agent OS adds a context-scoped default timeout for UZI `requests` calls
and propagates that context into nested UZI executor tasks; peer DomainPack
traffic runs with the guard inactive.

The root v3 collection executes all 22 registry entries for every depth.  Fixed
`generate_panel()` likewise ignores the profile's stale 10/51/51 display counts
and executes all 66 seats; only the standalone `/quick-scan` command explicitly
selects its Top 10.  Standalone command collection follows the pinned implicit
profiles (`quick-scan`/`panel-only` lite, `initiate`/`ic-memo` deep).

Request-local `no_resume` maps to `resume=False` only for the single-stock root
route.  The fixed versus/portfolio runners always resume and reject that option.
Snapshot Artifacts preserve both native and recovery `source_trace` entries;
before recovery, lane-specific raw envelopes round-trip without display aliases,
while any recovered field is an explicit, traced mutation rather than vendored
source parity.
