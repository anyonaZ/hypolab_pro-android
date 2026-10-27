# ACE Studio research modes roadmap

Research modes should describe the question being answered. They should not duplicate Data Explorer,
Template Studio, Simulation, or Results. Every mode should produce the same durable research object:
hypothesis, selected evidence, field requirements, candidate family, experiment specification, results,
falsification outcome, and decision.

## Modes already present

1. **Field discovery** — start from one or more selected fields and infer defensible mechanisms.
2. **General hypothesis** — start from an economic mechanism and bind suitable catalogued fields later.
3. **Paper replication** — read a complete paper, extract claims and tests, then map only supported claims
   onto available BRAIN fields.
4. **Alpha thesis transfer** — fetch an accessible alpha, separate mechanism from implementation, and
   test the mechanism with selected target fields.
5. **Template sweep** — hold structure constant while varying fields and controlled transforms.
6. **Evolution study** — deterministically mutate reviewed expressions with an auditable genealogy.
7. **Cross-region transfer** — test fields that actually coexist in another region using that region's
   valid universe and neutralization settings.
8. **Delay robustness** — compare D0 and D1 only where the same fields exist at both delays.
9. **SuperAlpha research** — study selection/combo logic using its separate vocabulary.

These should appear in one **New research** launcher. Choosing a mode creates a mode-specific brief and
opens the first required step. The sidebar remains organized by reusable tools rather than duplicating a
full screen tree per mode.

## High-value modes to add

### 1. Anomaly-first research

Start with a documented market anomaly such as underreaction, revisions, liquidity provision, crowding,
quality, or event drift. Require a causal chain, expected sign, horizon, invalidating regime, and field
properties before showing candidate fields.

### 2. Dataset audit

Treat one dataset as the subject. Profile field types, date coverage, cross-sectional coverage, missingness,
update cadence, redundancy, popularity, and region/delay availability. Produce a ranked opportunity map and
explicit warnings about sparse or late-starting histories.

### 3. Event study

Define an event proxy, direction, pre/post windows, decay shape, eligibility condition, and placebo dates.
Generate expressions only after the event definition passes leakage and timestamp checks.

### 4. Orthogonalization research

Start from an existing promising alpha or portfolio exposure. Diagnose which component overlaps with
production, self history, known factors, sectors, countries, or the user's candidate set. Search for a
mechanistically different residual signal rather than blindly adding operators.

### 5. Regime robustness

Evaluate performance by year and by observable regimes such as volatility, liquidity, trend, dispersion,
or crisis state. A mode result should say where the hypothesis works, where it fails, and whether a regime
condition was specified before seeing performance.

### 6. Ablation and parameter sensitivity

Start from one expression. Test sign, field, operator, lookback, neutralization, decay, truncation, universe,
and optional condition one dimension at a time. Report a response surface and stability plateau; avoid
selecting a single sharp optimum.

### 7. Negative-signal inversion

Start only from a stored strong negative result. Form a separate inversion hypothesis, simulate the negated
expression, compare both signed PnLs, and retain the inverse only if the new run is positive and robust.
Negation must never convert an old result into a pass without a new simulation.

### 8. Portfolio gap research

Start from the user's validated alpha set. Identify underrepresented mechanisms, datasets, horizons, regions,
and exposure patterns. Generate research briefs aimed at filling a specific diversification gap. Candidate
correlation is evidence; production correlation remains a per-alpha BRAIN check.

### 9. Cross-dataset confirmation

Use two datasets measuring the same economic construct as independent confirmation, or two categories with
different mechanisms as a controlled interaction. Require the user to choose confirmation, spread, ratio,
conditioning, or disagreement before generation.

### 10. Literature synthesis

Accept several complete papers. Build a claim ledger with page provenance, agreements, contradictions,
assumptions, horizons, and data requirements. Generate hypotheses from the intersection of supported claims,
not from a generic combined summary.

### 11. Reproduction challenge

Freeze a research package containing source material hashes, catalog snapshot, prompt, provider/model,
expressions, settings, random seed, and results. A colleague can rerun it and ACE reports exact, semantic,
and performance differences.

### 12. Research review

Open any completed study as a reviewer. Check hypothesis quality, pre-registration, multiple-testing burden,
field availability, economic meaning, leakage risk, complexity, parameter stability, test-period retention,
correlations, and the final keep/revise/reject decision.

## Shared capabilities every mode needs

- A versioned experiment manifest and persistent jobs, including restart-safe simulation queues.
- A claim/evidence ledger linking every conclusion to a paper page, field description, simulation, or check.
- Dataset profiling with earliest/latest usable dates, date and cross-sectional coverage, missingness, update
  frequency, unit, type, and region/delay availability.
- Pre-registered hypotheses and falsification rules that are locked before simulation, with revisions stored.
- Multiple-testing accounting by hypothesis family, expression family, field, parameter and configuration.
- Walk-forward or holdout comparisons with explicit train/test boundaries where BRAIN supports them.
- Parameter stability surfaces, ablations, yearly/regime attribution, and bootstrap confidence intervals from
  available PnL rather than a single headline Sharpe.
- Signed performance throughout, with separately simulated inversion candidates.
- Exposure and redundancy diagnostics across expressions, fields, operators, PnLs and economic mechanisms.
- A decision record that distinguishes platform checks, ACE heuristics, user judgment, and verified evidence.
- Reusable research packages for teams that exclude credentials, sessions, proprietary observations and
  private local state.

## Recommended implementation order

1. Create the common research-study schema and **New research** launcher.
2. Add dataset audit and ablation/sensitivity modes because they improve every existing workflow.
3. Add persistent simulation queues and complete experiment manifests.
4. Add regime analysis, portfolio-gap research and orthogonalization on top of Results.
5. Add multi-paper claim provenance and reproduction packages for team workflows.

