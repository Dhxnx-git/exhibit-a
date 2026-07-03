# Changelog

All notable changes to exhibit-a are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semver.

## [0.1.0] 2026-07-02

First working release. The core is real and tested end to end. Treat it as an
honest alpha, not a finished product.

### Added
- **Deterministic validation engine.** The five-gate gauntlet (collects,
  fails_on_head, symptom_match, stable, known_good) that decides CONFIRMED vs
  UNREPRODUCIBLE by running tests and parsing junit XML, never by trusting the
  model. This is the novel, load-bearing part.
- **Two LLM stages** (extract, synthesize) behind a `FakeClient` seam, each a
  forced tool call re-validated by our own schema parsers.
- **CLI**: `exhibit init`, `exhibit run` (LLM and fully-offline paths),
  `exhibit selfcheck` (end-to-end proof with no API key).
- **GitHub Action** with the split-privilege design: a `permissions: {}`
  reproduction job and a separate `issues: write` poster that runs no model.
- **Docker sandbox mode** with `--network none` during test execution and no
  host env passed through.
- Bundled `examples/buggy_calc` demo package plus real captured receipts.
- About 66 tests, including real-venv end-to-end coverage of every verdict path.

### Known limits
- pytest and Python only. vitest is the next target.
- Repos that do not build cleanly yield `ENV_FAILED` (by design).
- See README "Limitations" for the full honest list.
