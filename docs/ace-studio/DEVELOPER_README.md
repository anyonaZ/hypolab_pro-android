# ACE Studio

ACE Studio is a local-first research workspace for WorldQuant BRAIN consultants. It connects data discovery, hypothesis formation, FastExpr generation, template reuse, BRAIN simulation, result review, and portfolio correlation around the user's own BRAIN account.

This README is the developer handoff. Start here if you have never run or modified the project. For the product rationale, read [`docs/HOW_I_THINK_ABOUT_ACE_STUDIO.md`](docs/HOW_I_THINK_ABOUT_ACE_STUDIO.md). [`docs/ACE_STUDIO_V2_PLAN.md`](docs/ACE_STUDIO_V2_PLAN.md) is an older design plan; some names and ambitions there are historical. Running code and this README describe current behavior.

> ACE Studio assists research. Its internal validation and result gates do not guarantee that BRAIN will accept an alpha, and repeated in-sample search is not independent evidence.

## System at a glance

```text
Browser (React + TypeScript + Vite)
        │ /api requests
        ▼
FastAPI backend on 127.0.0.1:8766
        ├── vendored BRAIN adapter and authenticated user session
        ├── configured LLM provider SDKs
        ├── SQLite research/catalog database
        ├── in-memory background job registry
        └── signed executable updater and optional support collector
```

The application runs locally, but it is not offline software. It contacts BRAIN for account options, metadata, alpha details, PnL, checks, and simulations. Research workflows send selected context to the AI provider configured by the user. The executable updater contacts GitHub, and an issue report contacts the support collector only when the user sends it.

## Technology

- Python 3.11 or newer
- FastAPI, Pydantic 2, SQLAlchemy 2, SQLite, pandas, pypdf, and cryptography
- OpenAI-compatible, Anthropic, and Google GenAI SDKs
- React 18, React Router 6, TypeScript 5, and Vite 5
- PyInstaller `onedir` builds for the preferred end-user package
- Ed25519 release manifests for executable update integrity
- Optional Cloudflare Worker support collector forwarding reports to Telegram

## Repository map

```text
backend/
  _ace_entry.py                Frozen executable entry point
  app/
    main.py                    FastAPI app, module registration, SPA serving
    core/                      Settings, feature loader, jobs, device ID, LLM cache
    brain/engine.py            Boundary around the vendored BRAIN implementation
    db/                        SQLAlchemy engine and persistent models
    catalog/                   Local catalog and resumable metadata sync
    knowledge/                 Dataset/field relationships and matching
    research/                  Hypotheses, papers, saved research, alpha replication
    generation/                Prompts, templates, validation, repair, data nuggets
    evolution/                 Deterministic mutation, staged evaluation and Pareto selection
    simulation/                Simulation, sweeps, walk-forward and result gate
    strategy/                  Category and field research guidance
    operators/                 Operator reference and user annotations
    super/                     Separate SuperAlpha selection/combo logic
    analytics/                 Results, PnL, correlation and portfolio analytics
    update/                    Frozen release verification and swapping
    modules/                   Thin FastAPI routers, one package per feature
  vendor/                      BRAIN/session/validator/LLM compatibility layer
  tests/                       Focused backend regression tests
frontend/
  src/app/                     Shell, navigation, login gate and global jobs
  src/features/                Screen implementations
  src/lib/                     API, shared stores, toasts and controls
  src/styles/                  Theme and layout tokens
  dist/                        Generated production frontend; do not hand-edit
support-worker/                Optional Cloudflare support collector
build/ace-studio.ico           Windows executable icon
data/                          Local runtime state; ignored except .gitignore
docs/                          Product rationale and historical architecture plan
run.bat / run.sh               Source-development launchers
build_pyinstaller.py           Preferred signed executable build
package.py                     Signing and legacy bytecode packaging
package_for_sharing.bat        Clean source ZIP workflow
_update.ps1                    Source-package updater outside Git checkouts
```

## First development setup

### Windows

Install Git, Python 3.11+, and a current Node.js LTS release. Clone the repository and run:

```powershell
.\run.bat
```

The launcher creates `backend/.venv`, installs backend and frontend dependencies, starts the backend first, waits for `http://127.0.0.1:8766/api`, starts Vite, and opens `http://localhost:5173`. It opens separate backend and frontend terminals; close both to stop development.

### macOS or Linux

```bash
bash run.sh
```

The shell launcher creates the environment, installs changed dependencies, starts and waits for the backend, then runs Vite in the foreground. Press Ctrl+C to stop it.

### Manual startup

Manual startup is useful for debugging. Start the backend before the frontend:

```powershell
python -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm install
cd ..
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8766 --reload
```

In another terminal:

```powershell
cd frontend
npm run dev
```

Development endpoints:

- UI: `http://localhost:5173`
- API root: `http://127.0.0.1:8766/api`
- OpenAPI: `http://127.0.0.1:8766/docs`

Vite proxies `/api` to port 8766. `ECONNREFUSED 8766` means the backend is absent or failed during import. The backend prints every mounted feature. A line such as `[modules] skipping 'simulate': ...` identifies an import or dependency failure.

## Authentication and private state

The app checks `/api/session/status`. Without a valid BRAIN session, `LoginGate` blocks workspace interaction and the sidebar shows a login action. Authentication is persisted at:

```text
~/secrets/session.pkl
```

LLM keys and preferred provider/model settings are managed in Settings and stored at:

```text
~/secrets/ace_keys.json
```

The BRAIN adapter may also use `~/secrets/platform-brain.json`. Never copy these files into a source ZIP, executable build, commit, support report, or fixture. Every developer and recipient uses their own BRAIN account and AI keys.

## Current screens and workflow

Navigation comes from `backend/app/modules/meta/__init__.py`; React routes are declared in `frontend/src/app/App.tsx`. A sidebar route must exist in both.

| Route | Screen | Responsibility |
|---|---|---|
| `/` | System Overview | Research results, catalog coverage, active jobs, and next actions. The component retains the historical name `CommandCenter.tsx`. |
| `/data` | Data Explorer | Automatically refreshed and locally cached datasets/fields; persistent filters and selections; MATRIX, VECTOR and GROUP awareness. |
| `/strategies` | Strategy Lab | Economic uses for categories/fields and reusable research directions. |
| `/generate` | Alpha Lab | Data-grounded/general hypotheses, candidate generation, papers, saved hypotheses and alpha-by-ID replication. |
| `/templates` | Template Studio | `{field}`/`{field2}` templates, transformation variants, validation, saved templates and data nuggets. |
| `/simulate` | Simulation | Regular runs, batch queues, cross-region sweeps and eligible D0/D1 checks. |
| `/evolution` | Evolution Lab | Manual seed variation with explicit field scope, reproducible search, staged BRAIN tests and Pareto survivors. |
| `/results` | Results & Analytics | Per-alpha positive-direction gates, inversion candidates, complete metrics, PnL/year evidence, test-period retention, signed self/production/powerpool checks and exports. |
| `/super` | SuperAlpha | Separate selection/combo vocabulary, validation, previews and simulation. |
| `/prompts` | Prompt Library | Reusable dataset-focused generation or template instructions. |
| `/operators` | Operator Atlas | Live signatures, examples, usage and protected user notes. |
| `/regions` | Market Transfer Lab | Fetches and locally stores exact dataset/field intersections across selected regions or D0/D1, then hands the verified field pool to Simulation. |
| `/settings` | Settings | BRAIN login, AI keys, providers/models, tests, theme and updates. Opened from the header. |

The shared frontend store is `frontend/src/lib/store.tsx`. It persists market context, datasets, fields, selected IDs, pending simulation expressions and guided-flow state under `ace2-research`. Feature controls use `usePersistentState`. If a persisted value changes meaning, migrate it or use a new versioned key.

### Automatic data cache

The catalog stores each dataset or field concept once and availability separately for every `(instrument, region, delay, universe)`. Database uniqueness constraints prevent duplicate availability rows.

There is no separate catalog-management screen. Data Explorer asks BRAIN for current data and
automatically upserts the complete dataset, field, description, type, category and availability
context into SQLite. Selecting a known dataset immediately reveals saved fields; an explicit fetch
refreshes them. If BRAIN is temporarily unavailable, Data Explorer transparently falls back to the
saved context. Field availability keeps both cross-sectional coverage and BRAIN's date-coverage
value, so users can filter sparse histories before generation. BRAIN remains authoritative for
current availability and final simulation validity.

### Hypotheses and generation

Research prompts carry full selected field descriptions. General hypotheses may omit current field binding; grounded hypotheses may not. Saved hypotheses are deduplicated by semantic fingerprint and retain their original context and reuse count.

Generation supports:

- deep single-field exploration;
- controlled multi-field generation with category constraints;
- raw and rewritten master prompts;
- allowed-operator restrictions;
- deterministic field, type, syntax, arity and complexity checks;
- repair rounds and optional economic review.

VECTOR fields must retain their real type and use compatible `vec_*` scalarizers before scalar operators. Never trim descriptions in an API or LLM payload; truncation is a UI concern only.

Alpha replication fetches an accessible BRAIN alpha by ID, extracts its durable mechanism, and binds it only to selected fields whose descriptions support that mechanism. Preserve its checks against blind transfer, leakage, arbitrary signs, redundant inputs and needless complexity.

PDF research parses every page by default, preserves page boundaries, and sends the complete extracted text into both goal drafting and hypothesis research. A page range is optional and means the user deliberately restricted the source. Do not reintroduce silent character slicing at the upload, API or prompt layer. Selected field descriptions are likewise complete; compact text belongs only in the rendered UI.

### Templates and data nuggets

Templates contain `{field}` and optionally `{field2}`. Template Studio applies structural transformations, expands compatible fields, validates every expression and hands valid output to Simulation. A saved asset includes its name, template, data nugget, context, datasets, fields, vector operators, variants and multi-field settings.

`Let the LLM pick fields` was deliberately removed. Field selection remains explicit and catalog-grounded.

### Evolution Lab

Evolution Lab starts empty. A researcher pastes valid FastExpr seeds or deliberately sends expressions from Alpha Lab or Template Studio. It does not load seeds from earlier runs, fetch alphas by ID, or call an LLM. **Preserve seed fields** changes only parameters and structure, **Selected pool** permits typed replacements from Data Explorer, **Same datasets** expands through locally catalogued fields in the seed datasets, and **Same categories** expands across locally stored datasets without leaving the selected categories. Two-field seeds may therefore preserve a same-dataset pair, explore different datasets in one category, or use an explicitly selected two-category pair. Every expression remains capped at two data categories.

Researchers can independently enable horizon, field substitution, operator-family, Operator Atlas expansion, signature-parameter, normalization, smoothing, difference, sign, group-level, simplification and crossover mutations. Each run snapshots the user's current REGULAR Operator Atlas and derives time-series, cross-sectional, group, vector, arithmetic, logical, regression, turnover-control and transform families from its signatures. Atlas expansion supplies the required positional arguments, rotates through the selected lookbacks and valid groups, varies supported keyword parameters, uses available `vec_*` reducers for VECTOR substitutions, and constructs multi-input candidates when two parents exist. The builder deliberately interleaves methods and Atlas families so a large field pool or an alphabetically early operator cannot crowd other experiments out of the population. Every resulting candidate is validated again before it can consume a simulation.

Quick, Balanced and Robust are bounded search presets. Every unique candidate receives one pilot configuration. Balanced promotes roughly the strongest third and Robust promotes roughly half to the remaining selected universe and neutralization combinations. The preview separates the candidate target from the maximum BRAIN configuration cost. If the valid mutation space is exhausted, the result says so instead of pretending that the requested target was evaluated.

**Generate preview** validates the seeds and builds the entire deterministic candidate population without contacting BRAIN. The expression ledger therefore shows every genetically modified expression before the researcher spends simulation capacity. Seed-field relationships are summarized by count instead of rendering one profile line per seed. **Test reviewed population** freezes that preview into the run specification and evaluates those exact expressions.

Each run stores its random seed, canonical seed hashes, complete field snapshots, expanded protocol, reviewed candidate population, application version, mutation grammar version, operator-reference hash, genealogy and evidence in SQLite. Reproduce reruns the saved specification. Canonical AST serialization prevents whitespace-only variants from consuming the budget. Candidate syntax, field existence, MATRIX/VECTOR compatibility, operator count and the two-category rule are checked before simulation. Metrics are normalized for Pareto selection, and signed returns and margin keep their direction. The expression ledger remains visible after evaluation and lists every candidate with its parent, mutation, stage, canonical hash, complexity, configuration count and metrics.

### Simulation and evidence

Simulation supports expressions, universes, neutralizations, decay, truncation, concurrency, tags and optional submission checks. Cross-region simulation chooses a valid universe and neutralization independently for each region and performs final live field verification.

Market Transfer Lab creates a persistent local study. It can fetch two or more selected regions, intersect their dataset IDs, fetch fields only for the shared datasets the user chooses, and retain only field IDs present everywhere. Its D0/D1 mode applies the same procedure to both delays in one region. Markets, selections, completed context checkpoints, common fields, candidate lineage and result links are stored in SQLite. Leaving the route does not stop its background comparison, and reopening it reconnects to the same job. After fields are verified, the active study follows Strategy Lab, Alpha Lab, Template Studio, Evolution Lab and Simulation through the header context.

When a transfer study is active, Simulation derives its configurations directly from every saved study context and hides its separate manual cross-region controls. The same expression is evaluated in every region/delay with a valid universe and neutralization, and results retain the study ID for filtering and transferability review. D0/D1 is a delay-robustness check; classical temporal walk-forward evidence still comes from Simulation test-period controls and matched baseline/holdout analysis. Exit transfer mode from the header to return to ordinary single-context research without deleting the saved study.

Default internal thresholds:

| Delay | Minimum positive Sharpe | Minimum positive Fitness |
|---|---:|---:|
| 0 | 2.69 | 1.50 |
| other | 1.58 | 1.00 |

Maximum turnover defaults to 0.70. Reported in-sample failures reject a result. Correlation limits apply when requested. Missing optional metrics are not always failures, so `passed_gate` is a research filter, not a submission guarantee.

The success gate is positive-direction only. A strong negative Sharpe/Fitness result fails the current expression and is retained as an **inversion candidate**. Results can queue `-(expression)` into Simulation or turn it into a Template Studio variant, but the inverted expression must be simulated before it can pass. Negation flips signed correlation values and leaves absolute correlation magnitude unchanged.

Run the same expression/configuration once with **Full period** and again with a 1–3 year test-period preset. Results & Analytics pairs those stored runs and reports signed Sharpe retention: at least 80% is labelled strong, 60–80% moderate, a smaller positive value weak, and a sign reversal failed direction. This is holdout evidence rather than a promise of future out-of-sample performance. Candidate PnL correlation uses `abs(correlation) < threshold` to build mutually compatible sets while displaying actual signed values. Production correlation remains a per-alpha BRAIN submission check; self and powerpool values are shown when BRAIN returns them.

Jobs survive page navigation and refresh because the browser can poll them. They are in memory and do not survive backend termination. Catalog plans are the exception because their tasks are persisted.

## Backend architecture

### Module contract

`backend/app/modules/__init__.py` contains `ENABLED`. Each `app.modules.<name>` exposes `router: APIRouter` and may expose `PREFIX` and an idempotent `on_startup()`.

`register_modules()` logs and skips a module that cannot import. Treat a missing API group as a possible skipped-module error before debugging React.

Keep request models and response shaping in `app/modules/<feature>/__init__.py`; put domain logic in `app/<feature>/service.py`. Keep BRAIN access behind `app/brain/engine.py` or feature services rather than importing vendor internals into React-facing code.

### API groups

Router files and `/docs` are authoritative:

```text
/api/health       liveness
/api/meta         navigation, build info and global jobs
/api/session      session status and restart
/api/settings     provider keys/models/tests and login
/api/data         live options, datasets and fields
/api/catalog      sync, local metadata, transfer, backup/reset
/api/knowledge    relationships, equivalents and judgments
/api/research     goals, papers, hypotheses and alpha replication
/api/generate     generation, validation, templates and prompts
/api/simulate     runs, batches, sweeps, walk-forward and jobs
/api/evolution    preview, reproducible runs, cancellation and run history
/api/analytics    summaries, results, PnL and correlations
/api/operators    operator references
/api/strategy     category and field research
/api/regions      region/universe information
/api/super        SuperAlpha tools
/api/update       frozen update check/apply
/api/support      status and user-triggered reports
```

### Persistence and schema changes

Source runs use `data/`; frozen builds use a `data/` folder next to the executable. The main database is `data/ace_studio_v2.db`. Models cover catalog concepts/availability/tasks, usage counters, research sessions, unique hypotheses, prompts, template assets, reproducible evolution runs and individuals, simulation results and operator references.

`init_db()` calls `Base.metadata.create_all()` and `_ensure_columns()`. There is no active Alembic workflow despite the dependency. `create_all()` does not add columns to an existing table, so add safe upgrades to `_ensure_columns()` or introduce migrations. Never solve an upgrade by deleting a user's only research database.

`data/llm_cache.json` caches only deterministic calls such as relationship judgments or naming. Fresh idea generation must not be cached.

### Background jobs

`backend/app/core/jobs.py` runs daemon threads. A task receives `progress` and `should_cancel`:

```python
def task(progress, should_cancel):
    progress(message="working", total=10, done=3, log="batch complete")
    if should_cancel():
        return
```

Cancellation is cooperative. Keep progress logs bounded and free of credentials. Persist a plan like Catalog Sync for anything that must resume after process restart.

### Frontend conventions

- Add a route to `SCREENS` in `App.tsx` and its navigation entry to the backend meta module.
- Use `api.get/post/delete` from `frontend/src/lib/api.ts`; do not hardcode origins.
- Use toasts for human-readable outcomes instead of rendering raw payloads above pages.
- Show `.spin` on asynchronous buttons.
- The shell never scrolls. Use compact panels/tabs; reserve `.panel-scroll` for genuinely long tables or evidence.
- Use CSS variables from `tokens.css` so controls work in dark and light themes.
- Preserve full descriptions in state and requests. Use `title` or details for full UI text.
- Keep instrument, region, delay and universe in `useResearch()`.

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q

cd ..\frontend
npm run typecheck
npm run build

cd ..
git diff --check
git status --short
```

Tests must not contact real BRAIN or LLM services; monkeypatch those boundaries. Current tests cover catalog behavior, template assets, vector expansion, saved research and alpha replication.

Add a regression test when changing expression validation, VECTOR handling, catalog uniqueness, budget accounting, or saved assets. Run the UI at representative viewport sizes for visual changes.

## Configuration

Backend settings use `ACE_` environment variables and may be put in an ignored `.env`:

```text
ACE_HOST=127.0.0.1
ACE_PORT=8766
ACE_FRONTEND_DEV_ORIGIN=http://localhost:5173
```

The source launcher explicitly passes port 8766, so update the launcher too if changing the development port.

There is currently no donation or entitlement feature in active code.

## Secrets and tracked files

Never commit or distribute personal state:

```text
~/secrets/session.pkl
~/secrets/ace_keys.json
~/secrets/platform-brain.json
data/
.env
ace.log
the private release-signing seed
proprietary simulation exports, expressions or alpha IDs
```

`update_token.txt` and `support_token.txt` are ignored local distribution credentials. `update_url.txt` and `support_url.txt` may be tracked. PyInstaller and source-sharing scripts include local tokens when present because private recipients need them. Anyone receiving the package can read them. Use narrowly scoped, revocable credentials and rotate them if necessary.

Signing proves origin and integrity; it does not hide ZIP or executable contents. Untracked environments, `node_modules`, generated ZIPs, build directories and the existing `ACE Studio.zip` are not source changes.

## Distribution

### Clean source ZIP

```powershell
.\package_for_sharing.bat
```

This creates `Desktop\ACE Studio.zip`. It excludes Git metadata, databases, sessions, logs, `.env`, environments, dependencies, built frontend files and caches. It includes source launchers and any local update/support credentials needed by the private distribution.

A source package without `.git` runs the source updater before setup. A developer Git checkout skips it because Git controls development changes. This overlay updater is separate from signed executable updates.

### Preferred PyInstaller build

Build the frontend, then supply an explicit version:

```powershell
cd frontend
npm run build
cd ..
.\backend\.venv\Scripts\python.exe build_pyinstaller.py --version 2.0.60
```

Outputs include:

```text
pyi_dist/ace-studio/
ACE-Studio-pyi-win-x64.zip
```

The script embeds the frontend/backend/runtime, stamps the version and release API URL, copies optional update/support tokens, signs and verifies the manifest, and creates a ZIP rooted at `ACE Studio`. Build separately on every target operating system.

Verify independently:

```powershell
.\backend\.venv\Scripts\python.exe package.py verify .\ACE-Studio-pyi-win-x64.zip
```

Expected output is `OK: ok`.

### Signing key

The Ed25519 private key is outside the repository under `~/.ace_studio_keys`. Generate a key only for a new trust chain:

```powershell
.\backend\.venv\Scripts\python.exe package.py keygen
```

Never replace the established maintainer key. Existing installations trust the public key hardcoded in `backend/app/update/signing.py` and reject another signer. Back up the original private seed securely.

`package.py build` is an older Python-minor-specific bytecode distribution. PyInstaller is preferred because it includes Python and dependencies.

## Executable updater

Frozen builds query the GitHub releases API. The updater reads `version.txt`, downloads a platform ZIP, verifies its signature against the installed hardcoded public key, verifies all signed hashes, stages it, exits, preserves `data/`, swaps files and relaunches.

Unsigned builds, different keys, changed files and incomplete manifests are rejected. `update_url.txt` and `update_token.txt` are runtime configuration excluded from the signed manifest so they can be rotated.

## Publishing a release

1. Run tests, typecheck and production frontend build.
2. Commit and push the exact source state.
3. Build with the intended semantic version.
4. Verify the final ZIP.
5. Calculate its SHA-256.
6. Confirm full `HEAD` equals `origin/main`.
7. Create the GitHub release targeting that full commit.
8. Read the release back and compare GitHub's asset digest with the local hash.
9. Record a rollback release/commit.

Example after all gates pass:

```powershell
$commit = (git rev-parse HEAD).Trim()
$hash = (Get-FileHash -Algorithm SHA256 .\ACE-Studio-pyi-win-x64.zip).Hash.ToLower()
gh release create v2.0.60 .\ACE-Studio-pyi-win-x64.zip `
  --repo ngari-qds/ace-studio `
  --target $commit `
  --title "ACE Studio 2.0.60" `
  --notes-file .\release-notes.md `
  --latest
gh release view v2.0.60 --repo ngari-qds/ace-studio `
  --json tagName,targetCommitish,assets,url
```

Do not infer a full commit hash from a short hash; resolve it with Git immediately before publishing.

## Support collector

`support-worker/` accepts an authenticated JSON report and forwards it to Telegram. The app reads `support_url.txt` and `support_token.txt`. The Worker stores `TG_TOKEN`, `TG_CHAT`, and `SHARED_SECRET`; the latter must equal the app token. See `support-worker/README.md` for deployment.

Logs are included only when the user chooses. Never record credentials, cookies or raw proprietary data in logs.

## Adding a feature

Backend:

1. Put domain logic in `backend/app/<feature>/service.py`.
2. Add request models and a router in `backend/app/modules/<feature>/__init__.py`.
3. Add the module to `backend/app/modules/__init__.py`.
4. Make startup hooks idempotent.
5. Add models plus an existing-database upgrade path when needed.
6. Add network-free regression tests.

Frontend:

1. Create `frontend/src/features/<feature>/<Screen>.tsx`.
2. Register it in `SCREENS`.
3. Add backend navigation metadata.
4. Reuse research/session state.
5. Add spinners, toasts, theme-aware controls and bounded layout.
6. Typecheck, build and run against the backend.

Protect these invariants:

- Generated field IDs come from selected/live/catalog fields.
- Full descriptions reach prompts.
- VECTOR/MATRIX types survive every handoff.
- Multi-field category limits remain enforced after mutation and repair.
- Seed fields cannot silently replace the selected field pool.
- BRAIN verifies final availability before simulation.
- Configuration budgets are calculated before a population starts.
- Gate reasons remain stored and are never presented as acceptance promises.
- General hypotheses remain mechanisms; grounded ones retain provenance.
- SuperAlpha vocabularies remain separate from regular FastExpr.
- Personal state and credentials never enter Git or signed manifests.

## Troubleshooting

### Frontend cannot reach port 8766

Start the backend first. Check its terminal, then visit `/api` directly.

### An API group returns 404

Check the startup module list. A feature import failed and was skipped; install requirements and inspect the first error.

### Login blocks the workspace

This is intentional. Complete BRAIN login and verify `~/secrets/session.pkl` is valid. Re-login when the sidebar warns that it is expiring.

### All LLM providers fail

Test the provider/model in Settings. Check its key, quota, endpoint and timeout. Manual templates and manual Evolution protocols can work without an LLM; hypothesis generation and requested economic reviews cannot.

### Catalog sync reached its ceiling

Resume the same run with more allowance, or create a Missing-only plan. Do not choose All unless refetching is intended.

### Evolution simulated fewer expressions than expected

Check expression capacity, deterministic validation, mutation space, cancellation and live BRAIN errors. Economic review no longer shrinks a population. `population × universes × neutralizations` funds one complete population.

### VECTOR expression fails

Confirm `type: VECTOR` survived the handoff and use an allowed `vec_*` scalarizer before scalar operators. Add a regression case.

### Existing database lacks a column

Add an idempotent upgrade in `backend/app/db/base.py::_ensure_columns()` or implement migrations. Do not delete user data.

### PyInstaller warns about optional transports or database drivers

Missing `aiohttp`, `MySQLdb`, `psycopg2`, or `pysqlite2` can be benign when ACE uses synchronous provider transports and SQLite. Use the build exit code, executable startup and package verification as the gates.

### Executable update is rejected

Confirm the established signing seed was used, the hardcoded public key matches, the ZIP has one signed root, and no signed file changed afterward.

## Current limitations

- Most background jobs do not resume after backend shutdown; persisted catalog plans do.
- Knowledge matching is lightweight text similarity, not a full embedding database.
- Schema upgrading is not yet a complete migration system.
- Private distribution tokens are readable by recipients.
- PyInstaller/bytecode packaging does not make local code secret.
- Internal gate success does not guarantee BRAIN submission.

## Product intent

```text
data availability
→ economic hypothesis and falsification
→ field binding
→ controlled generation or templates
→ BRAIN simulation
→ gate, robustness and correlation evidence
→ saved research memory
```

The value is continuity. Preserve the provenance connecting these stages instead of turning ACE Studio into an ungrounded formula generator.
