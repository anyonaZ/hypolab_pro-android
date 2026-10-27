"""Strategy Atlas — LLM exploration of the strategy space per data category.

Never hardcodes strategies: for a category/region it asks the model to explore, seeded by
the device so two users get DIFFERENT strategy sets. Each strategy carries a thesis and a
concrete way to build it, and can be pushed to the Research Lab or Generation.
"""

from __future__ import annotations

from app.brain import engine  # noqa: F401 — loads the vendored engine
from app.core.device import device_seed
from app.knowledge import service as knowledge

import llm_providers as L  # noqa: E402
import keys as keymgr       # noqa: E402

DELIM = "|||"


def _op_ref() -> str:
    """Operator reference for the strategy prompt (best-effort — never block exploration)."""
    try:
        from app.generation import service as gen
        from app.operators import service as opsvc
        return gen.operator_summary() + "\n" + opsvc.reference_block("REGULAR")
    except Exception:
        return "(operator list unavailable — use only standard BRAIN operators)"


def explore(category: str, region: str, delay: int, instrument: str, n: int, mode: str, progress) -> dict:
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    seed = device_seed()
    d0 = ("For delay 0, weight strategies whose edge is a strong risk-adjusted return (Sharpe).\n"
          if str(delay) == "0" else "")

    from app.knowledge import categories as catlib

    # In two-category mode, pick the SAFEST different-mechanism partner category (per the BRAIN
    # combining-safely guide) from what's catalogued, and explore strategies that pair them.
    partner = None
    ds = knowledge.datasets_in_category(category, region)
    if mode == "two_categories":
        available = [c["category"] for c in knowledge.overview().get("categories", [])]
        partner = catlib.safest_partner(category, available)
        if partner:
            ds = ds + knowledge.datasets_in_category(partner, region)

    if ds and partner:
        by_cat = {category: knowledge.datasets_in_category(category, region),
                  partner: knowledge.datasets_in_category(partner, region)}
        blocks = []
        for cat, items in by_cat.items():
            lines = "\n".join(f"  - {d['name'] or d['id']}: {(d['description'] or '')[:70]}" for d in items[:60])
            blocks.append(f"=== DATASETS IN {cat.upper()} ===\n{lines}")
        ds_rule = (
            f"TWO-CATEGORY mode. {partner} was chosen as the partner for {category} because they reflect "
            "DIFFERENT economic mechanisms (lowest overfitting risk per the BRAIN combining-safely guide) — judge "
            "each pairing on that mechanism logic and lean into the ones that genuinely complement. EVERY strategy "
            f"MUST combine BOTH categories: its example expression uses ONE field from {category} AND ONE field "
            f"from {partner} TOGETHER (a ratio, spread, correlation, or one conditioning the other) — never one "
            "category alone — and it must name BOTH datasets it relies on. Spread the strategies across the range "
            "of datasets in BOTH categories, not just the obvious field pair.\n" + "\n".join(blocks) + "\n")
    elif ds:
        ds_block = "\n".join(f"  - {d['name'] or d['id']}: {(d['description'] or '')[:80]}" for d in ds[:120])
        ds_rule = (f"This category has {len(ds)} datasets (listed below). Explore the WHOLE category: your "
                   "strategies must COLLECTIVELY draw on the range of these datasets, not just the obvious "
                   "one, and each should say which dataset(s) it uses.\n"
                   f"=== DATASETS IN {category.upper()} ===\n{ds_block}\n")
    else:
        ds_rule = "No datasets are catalogued for this category yet — reason from the category name.\n"

    ds_names = [d["name"] or d["id"] for d in ds]
    meta = (
        "You are a senior quant strategist mapping the strategy space for a WorldQuant BRAIN data "
        "category. Produce DEEP, specific, immediately-actionable strategies — not generic textbook lines.\n"
        f"Category: {category}. Region: {region}, delay {delay}, {instrument}.\n"
        f"Exploration seed: {seed} — let it push you toward LESS obvious, more varied angles; be original.\n"
        + d0 + ds_rule +
        f"Propose {n} DISTINCT strategy archetypes for this category and region. Each must exploit a "
        "DIFFERENT market mechanism, name the SPECIFIC dataset(s) from the list it relies on, and reason "
        "about what actually drives THIS region (liquidity, microstructure, how information diffuses). "
        "Avoid overfitting-prone combinations.\n"
        "MATCH OPERATORS TO THE DATA NATURE in the build + example: continuous/ratio fields (prices, "
        "valuations, margins, yields) -> rank/zscore/regression + time-series momentum/reversal; COUNT "
        "fields (\"number of...\", \"days since...\") -> decay/sums/deltas; EVENT/flag/sparse fields "
        "(announcements, ratings, surprises, 0/1 indicators) -> event operators (ts_count, hump, "
        "days_from_last_change, kth_element) or use them to GATE another signal, NEVER as if continuous; "
        "group fields (sector/industry/country) are group_* arguments only. The example expression must "
        "make ECONOMIC SENSE for what its data measures.\n"
        f"=== OPERATORS (reference the correct ones; pass named params as keywords) ===\n"
        f"{_op_ref()}\n"
        f"Return ONLY a JSON array of {n} strings, each in this EXACT delimited form:\n"
        f"  name {DELIM} economic thesis: the mechanism, why it predicts returns, why it persists rather "
        f"than being arbitraged, and the expected sign + horizon (region-aware, 2-4 sentences) {DELIM} "
        f"how to build it: representative datafields, the operator families to use, neutralization/decay "
        f"hints, and one concrete example expression {DELIM} datasets: the EXACT dataset name(s) from the "
        f"list above that this strategy needs (comma-separated)\n")
    progress(message=f"exploring {category} across {len(ds)} dataset(s)…")
    res = L.MultiLLM(chain).generate_list(meta, n=n)

    def match_datasets(raw: str) -> list:
        # Return the catalogued {id, name} pairs the text refers to (by name OR id), so the user
        # sees the exact dataset id to fetch.
        low = raw.lower()
        seen, out = set(), []
        for d in ds:
            nm = (d["name"] or "").lower()
            did = (d["id"] or "").lower()
            if d["id"] not in seen and ((nm and nm in low) or (did and did in low)):
                seen.add(d["id"]); out.append({"id": d["id"], "name": d["name"]})
        return out

    strategies = []
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split(DELIM)]
        raw_ds = parts[3] if len(parts) > 3 else ""
        strategies.append({"name": parts[0] if parts else str(s),
                           "thesis": parts[1] if len(parts) > 1 else "",
                           "build": parts[2] if len(parts) > 2 else "",
                           "datasets": match_datasets(raw_ds) or match_datasets(parts[2] if len(parts) > 2 else "")})
    return {"category": category, "region": region, "partner": partner, "strategies": strategies,
            "datasets_explored": len(ds), "provider": res.provider, "model": res.model}


def _valid_operator_names() -> set:
    """Lower-cased REGULAR operator names, so suggested operators can be validated (no hallucinations
    reach the allowed-operators constraint)."""
    try:
        ops = engine.operators_df()
        if "scope" in ops.columns and (ops["scope"] == "REGULAR").any():
            ops = ops[ops["scope"] == "REGULAR"]
        return {str(x).strip().lower() for x in ops["name"].tolist()} if "name" in ops.columns else set()
    except Exception:
        return set()


def field_intel(*, fields, categories, region, delay, instrument, universe, mode, n, max_operators, transfer_context=None, progress) -> dict:
    """Strategy Lab: semantic + AI analysis of the SELECTED datafields. For each field (single mode)
    or field pairing (multi mode) it proposes an economically-sound hypothesis and the exact operators
    to build it — matched to the field's data nature and drawn ONLY from the operator atlas. The union
    of suggested operators + the selected fields later constrain generation."""
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    from app.generation import service as gen
    field_ids = {str(f.get("id")) for f in fields if f.get("id")}
    id_by_lower = {i.lower(): i for i in field_ids}
    valid_ops = _valid_operator_names()
    cats = list(dict.fromkeys(c for c in (categories or {}).values() if c))
    d0 = ("For delay 0, favour hypotheses whose edge is a strong risk-adjusted return (Sharpe).\n"
          if str(delay) == "0" else "")
    if mode == "multi":
        if len(cats) >= 2:
            slot = (f"MULTI-FIELD, TWO CATEGORIES: combine TWO fields from DIFFERENT categories "
                    f"({', '.join(cats[:2])}) in each hypothesis (ratio, spread, correlation, conditioning).")
        else:
            slot = "MULTI-FIELD, ONE DATASET: combine TWO OR MORE different fields from the selection in each hypothesis."
        idform = "field id(s) — the two (or more) fields it combines, comma-separated"
    else:
        slot = "SINGLE-FIELD: each hypothesis extracts signal from ONE field. Rotate through EVERY selected field before reusing any."
        idform = "field id — the ONE field it uses"
    meta = (
        "You are a senior quant researcher doing SEMANTIC analysis of a WorldQuant BRAIN datafield "
        "selection. For each field, reason from what its DESCRIPTION and DATA NATURE actually measure, "
        "then propose a hypothesis that makes genuine economic sense for it.\n"
        f"Region {region}, universe {universe}, delay {delay}, {instrument}.\n" +
        (("TRANSFER STUDY: the hypothesis must preserve its economic meaning and expected sign across every "
          "verified context: " + ", ".join(f"{x.get('region')} D{x.get('delay')} {x.get('universe')}" for x in transfer_context) + ".\n") if transfer_context else "") + d0 +
        f"{slot}\n"
        f"Propose {n} DISTINCT hypotheses. For EACH, name the exact operators to build it — matched to the "
        "field's data nature (continuous/ratio -> rank/zscore/regression + ts momentum/reversal; count -> "
        "decay/ts_sum/ts_delta; event/flag/sparse -> ts_count/hump/days_from_last_change/kth_element or as a "
        "gate; group -> group_* only) — and drawn ONLY from the operator atlas below (exact names). Use about "
        f"{gen._clamp_ops(max_operators)} operators per hypothesis.\n"
        "=== OPERATORS (use ONLY these exact names) ===\n" + _op_ref() + "\n"
        "=== SELECTED DATAFIELDS (type|nature|category tagged) ===\n" + gen._field_summary(fields, categories) + "\n"
        f"Return ONLY a JSON array of EXACTLY {n} strings, each in this EXACT delimited form:\n"
        f"  {idform} {DELIM} idea (short title) {DELIM} mechanism: why it predicts returns and why it "
        f"persists (2-3 sentences) {DELIM} operators: comma-separated exact operator names to use {DELIM} "
        f"sign (+/-) {DELIM} horizon in days {DELIM} information arrival {DELIM} applicable regime "
        f"{DELIM} necessary transformation {DELIM} neutralization rationale {DELIM} falsification rule "
        f"{DELIM} simple baseline {DELIM} alternative explanation")
    progress(message=f"analysing {len(field_ids)} field(s)…")
    res = L.MultiLLM(chain).generate_list(meta, n=n)

    items, all_ops = [], []
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split(DELIM)]
        if not parts or not parts[0]:
            continue
        raw_ids = [x.strip() for x in parts[0].replace(";", ",").split(",") if x.strip()]
        fids = [id_by_lower[x.lower()] for x in raw_ids if x.lower() in id_by_lower]
        if not fids:
            continue
        ops_raw = [x.strip() for x in (parts[3] if len(parts) > 3 else "").replace(";", ",").split(",") if x.strip()]
        ops = [o for o in ops_raw if not valid_ops or o.lower() in valid_ops]
        all_ops += ops
        items.append({
            "fields": fids,
            "idea": parts[1] if len(parts) > 1 else "",
            "mechanism": parts[2] if len(parts) > 2 else "",
            "operators": ops,
            "sign": parts[4] if len(parts) > 4 else "",
            "horizon": parts[5] if len(parts) > 5 else "",
            "information_arrival": parts[6] if len(parts) > 6 else "",
            "regime": parts[7] if len(parts) > 7 else "",
            "transformation": parts[8] if len(parts) > 8 else "",
            "neutralization_rationale": parts[9] if len(parts) > 9 else "",
            "falsification": parts[10] if len(parts) > 10 else "",
            "baseline": parts[11] if len(parts) > 11 else "",
            "alternative_explanation": parts[12] if len(parts) > 12 else "",
        })
    return {"items": items, "operators": sorted(set(all_ops)),
            "provider": res.provider, "model": res.model}
