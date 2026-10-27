# Evolution Lab redesign plan

> Implemented in the manual-first Evolution Lab. The active screen and API follow this design; alpha-ID import and LLM-directed mutation are intentionally outside this version.

## Decision

The first replacement will not fetch BRAIN alphas or depend on alpha IDs. Seeds enter through manual FastExpr input or an intentional one-time handoff from Alpha Lab or Template Studio. The genetic engine is deterministic and does not require an LLM.

## Audit of the retired implementation

The old UI exposed too many engine details: four setup tabs, hypothesis, alpha IDs, population, generations, elites, mutation rate, operator ceiling, catalog policy, universes, neutralizations, simulation settings, correlation, walk-forward, and LLM controls.

The backend had useful validation, selected-field coverage, genealogy, a seeded random generator, and Pareto ranking. It still mutated strings instead of typed trees, used formatting-sensitive deduplication, allowed naive `add(parent, mate)` crossover, measured novelty through token overlap, mixed unscaled objectives, used absolute values too broadly, silently swallowed expansion failures, seeded randomness from the database ID, and stored active jobs only in memory.

## Minimal workflow

### 1. Seed

Paste expressions or choose **Explore variants** in Alpha Lab or Template Studio. Nothing from an old run loads automatically. Each seed card immediately shows validation, fields, operators, type flow, and complexity.

### 2. Scope

Use the fields already selected in Data Explorer and choose one policy:

- **Keep fields** varies structure and parameters only;
- **Selected fields** permits compatible replacements from the visible selection;
- **Same dataset** permits typed replacements from selected datasets.

No field is introduced invisibly. Cross-category expansion is postponed until the core engine is proven.

### 3. Search

Choose **Quick**, **Balanced**, or **Robust**, enter the desired number of unique candidates, review the maximum BRAIN configuration cost, and run. Expert parameters live in an Advanced drawer.

### 4. Results

Show funnel counts, progress, survivors, and a searchable table of every candidate. Every row exposes parent, mutation, rejection reason, simulation evidence, and complexity change. Selected expressions can move to Simulation.

## Backend design

Parse seeds into typed abstract syntax trees. Nodes retain operator signatures and MATRIX, VECTOR, GROUP, scalar, or Boolean types. Serialize trees canonically and use the canonical hash for uniqueness.

Mutation operates on nodes: compatible field replacement, window changes, operator-family replacement, normalization, smoothing, sign tests, group changes, simplification, conditions, and type-compatible crossover. Every child records its exact transformation and must validate before entering a population.

Population diversity is enforced across field exposure, operator family, horizon, and structural fingerprint. Cosmetic variants cannot consume budget.

Complexity measures operator count, tree depth, distinct fields, datasets, categories, horizons, conditions, groups, and distance from the seed. Presets define hard ceilings. A complex child must demonstrate a material robustness improvement over a simpler ancestor.

## Evaluation and selection

1. Parse, type-check, and canonicalize.
2. Remove exact and near duplicates.
3. Apply field-scope and complexity constraints.
4. Run a primary-configuration pilot.
5. Eliminate clearly dominated candidates.
6. Run preset robustness configurations on survivors.
7. Run correlation and eligible D0/D1 checks only for finalists.
8. Select a small diverse Pareto set with normalized NSGA-II objectives.

Directional metrics must retain their sign. Missing metrics are marked as missing evidence rather than converted to favorable zeroes. Candidate-expression budget and BRAIN configuration budget are displayed separately.

## Reproducibility

Every run saves an immutable specification: application version, Git commit, explicit random seed, canonical seeds and hashes, full field snapshots, operator-reference hash, mutation grammar version, expanded preset, simulation settings, ordered population, validation decisions, results with timestamps, and complete genealogy.

The same specification must regenerate the same expressions. BRAIN metrics remain timestamped because platform data can change.

Persist run stages and candidate/configuration completion in SQLite. A backend restart resumes unfinished work. Cache keys combine canonical expression and complete simulation configuration.

## LLM boundary

Manual evolution requires no LLM. Optional assistance may draft a hypothesis or explain a seed or result. It cannot mutate populations, introduce fields, bypass validation, score candidates, or select winners.

## Frontend

Use a two-column screen without page scrolling. The left card contains seed, field scope, preset, candidate count, cost estimate, and Run. The right workspace switches between progress, survivors, and all candidates. Advanced controls remain hidden until requested.

## API

- `POST /api/evolution/preview` validates seeds and returns scope, preset expansion, diversity plan, and cost.
- `POST /api/evolution/runs` stores the immutable specification and starts work.
- `GET /api/evolution/runs/{id}` returns progress, funnel, results, and provenance.
- `POST /api/evolution/runs/{id}/cancel` cancels cooperatively.
- `POST /api/evolution/runs/{id}/resume` resumes persisted work.
- `POST /api/evolution/runs/{id}/reproduce` starts from the exact saved specification.

## Delivery order

1. Typed AST and canonical hashing.
2. Immutable run specification.
3. Grammar-aware mutation registry.
4. Diversity and complexity policies.
5. Staged evaluator and cache.
6. Normalized NSGA-II selection.
7. Persisted cancellation and resumption.
8. Preview and exact cost accounting.
9. Minimal two-column UI.
10. One-time Alpha Lab and Template Studio handoffs.
11. Optional LLM explanations after deterministic behavior is stable.

Tests must cover typed mutations, fixed-seed population snapshots, duplicate prevention, field/operator/horizon coverage, complexity ceilings, missing metrics, NSGA-II tradeoffs, exact cost accounting, cancellation/resumption, and one-time handoff consumption.
