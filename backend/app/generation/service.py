"""Generation engine (v2).

Two grounded modes, both LLM-driven and both validated/repaired before anything is shown:

  single  — DEEP signal extraction from ONE field per expression, covering every structural
            mechanism (arithmetic, time-series, cross-sectional, group-relative, logical),
            not just a couple of operators.
  multi   — combine fields, but STRICTLY from at most TWO data categories. Enforced in the
            prompt AND by a hard post-validation reject, so a 3+-category expression never
            slips through either path.

A diversity engine reads the local usage table to steer each user's generation toward
under-used operators and fields (and records what this batch used), so two users — and the
same user over time — diverge rather than converging on a few favourites.

Reuses the studio engine (validator, llm_providers, wqb_llm, keys) via the brain adapter.
"""

from __future__ import annotations

import json
import time

from app.brain import engine  # loads the vendored engine + gives cached operators

import validator as V      # noqa: E402
import llm_providers as L  # noqa: E402
import wqb_llm             # noqa: E402
import keys as keymgr      # noqa: E402

from app.db.base import SessionLocal
from app.db import models as M
from sqlalchemy import desc, select, func


# ── registry / validator ─────────────────────────────────────────────────────────────

def _registry(scope: str = "REGULAR") -> V.OperatorRegistry:
    # Operator Atlas is the durable account-specific registry and includes user corrections. Use
    # it first so syntax validation never depends on network availability; use BRAIN only before
    # the Atlas has been seeded.
    import pandas as pd
    with SessionLocal() as db:
        rows=db.scalars(select(M.OperatorRef).where(M.OperatorRef.scope==scope)).all()
        ops=pd.DataFrame([{"name":x.name,"scope":x.scope,"definition":x.signature} for x in rows])
    if ops.empty:
        ops = engine.operators_df()
    df = ops[ops["scope"] == scope] if ("scope" in ops.columns and (ops["scope"] == scope).any()) else ops
    return V.OperatorRegistry.from_dataframe(df)


def _clamp_ops(n) -> int:
    try:
        return max(1, min(20, int(n)))
    except (TypeError, ValueError):
        return 3


def _validator(fields: list, max_operators: int, multi_field: bool) -> V.Validator:
    ftypes = {f["id"]: str(f.get("type", "MATRIX")).upper() for f in fields}
    # ALWAYS restrict to the provided fields — in both single AND multi mode — so an LLM can
    # never introduce a datafield it wasn't given (UNKNOWN_FIELD then rejects it). Multi mode
    # only relaxes the single-field rule, never the which-fields-are-allowed rule.
    known = {f["id"] for f in fields}
    return V.Validator(
        _registry("REGULAR"), known_fields=known, field_types=ftypes,
        check_unknown_kwargs=True, require_single_field=not multi_field,
        max_operators=_clamp_ops(max_operators),
        op_count_ignore_prefixes=("vec_",))


# ── AST helpers (extract the raw fields an expression uses) ───────────────────────────

def _fields_in(expr: str, field_ids: set) -> list:
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            if n.name in field_ids:
                out.append(n.name)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


def _leaf_idents(expr: str) -> list:
    """Every identifier LEAF in the expression (datafields + group fields), regardless of a known
    field set — operators are Call.name so they're excluded, and kwarg keywords aren't values.
    Used by the cross-region pre-flight to know which datafields must exist in a region."""
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            out.append(n.name)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


def _operators_in(expr: str) -> list:
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Call):
            out.append(n.name)
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


# ── operator keyword / semantic enforcement (driven by the Operator Lab metadata) ────

_GROUP_IDENTS = {"industry", "subindustry", "sector", "market", "exchange", "country", "currency", "sedol"}


def _same_ast(a, b) -> bool:
    # Dataclass AST nodes have value-based repr, so equal structure => equal repr. Safe: only
    # ever flags when the two subtrees are genuinely identical.
    return repr(a) == repr(b)


def operator_issues(expr: str, specs: dict) -> list:
    """Metadata-driven checks the vendored validator doesn't do: keyword params must be passed
    by keyword (not positionally), plus the semantic rules for bucket/densify/ts_regression.
    Returns a list of (code, message)."""
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    issues, stack = [], [(ast, None)]
    while stack:
        node, parent = stack.pop()
        if isinstance(node, V.Call):
            nm = node.name
            pos_args = [a for a in node.args if not isinstance(a, V.KwArg)]
            kw_args = [a for a in node.args if isinstance(a, V.KwArg)]
            spec = specs.get(nm)
            if spec:
                if len(pos_args) > spec["positional"]:
                    issues.append(("KEYWORD_REQUIRED",
                                   f"{nm}: pass its keyword parameter(s) by name (e.g. {sorted(spec['keywords'])[0]}=…), "
                                   "not positionally"))
                # A kwarg is only "unknown" if its name matches NO declared parameter — positional
                # or keyword. BRAIN lets you pass a positionally-declared param by name (e.g.
                # kth_element(x, d, k=5)), so accept any real parameter name here.
                valid_names = spec.get("names") or spec["keywords"]
                for kw in kw_args:
                    if getattr(kw, "name", None) and kw.name not in valid_names:
                        issues.append(("UNKNOWN_KWARG", f"{nm}: '{kw.name}=' is not a parameter of this operator"))
            low = nm.lower()
            if low == "clamp":
                supplied = {str(getattr(kw, "name", "")).lower() for kw in kw_args}
                if not ({"lower", "upper"} & supplied):
                    issues.append(("CLAMP_BOUND_REQUIRED", "clamp(): supply at least one bound as lower=… or upper=…"))
            if low == "densify" and pos_args:
                a = pos_args[0]
                ok = (isinstance(a, V.Call) and a.name.lower() == "bucket") or \
                     (isinstance(a, V.Ident) and a.name.lower() in _GROUP_IDENTS)
                if not ok:
                    issues.append(("DENSIFY_NEEDS_GROUP", "densify() must wrap a group (a bucket(...) or a group identifier)"))
            if low == "bucket":
                pl = parent.name.lower() if isinstance(parent, V.Call) else ""
                if not (pl.startswith("group_") or pl == "densify"):
                    issues.append(("BUCKET_NOT_GROUP", "bucket() builds a group — use it only as the group argument of a group_* operator (or inside densify)"))
            if low == "ts_regression" and len(pos_args) >= 2 and _same_ast(pos_args[0], pos_args[1]):
                issues.append(("TS_REGRESSION_XY_SAME", "ts_regression: x must be a transform of / different from y, not identical"))
            for a in node.args:
                stack.append((a, node))
        elif isinstance(node, V.KwArg):
            stack.append((node.value, parent))
        elif isinstance(node, V.Unary):
            stack.append((node.operand, parent))
        elif isinstance(node, V.BinOp):
            stack.append((node.left, parent)); stack.append((node.right, parent))
        elif isinstance(node, V.Seq):
            for s in node.statements:
                stack.append((s, parent))
    # de-dup preserving order
    seen, out = set(), []
    for code, msg in issues:
        if (code, msg) not in seen:
            seen.add((code, msg)); out.append((code, msg))
    return out


def _param_specs() -> dict:
    try:
        from app.operators import service as opsvc
        return opsvc.param_spec_map()
    except Exception:
        return {}


# ── diversity engine ─────────────────────────────────────────────────────────────────

def usage_snapshot(region: str) -> dict:
    with SessionLocal() as db:
        rows = db.scalars(select(M.Usage).where(M.Usage.region == region)).all()
        ops = {r.key: r.count for r in rows if r.kind == "operator"}
        fields = {r.key: r.count for r in rows if r.kind == "field"}
    return {"operators": ops, "fields": fields}


def _diversity_hint(region: str, available_ops: list) -> str:
    snap = usage_snapshot(region)
    used = snap["operators"]
    if not used:
        return ""
    over = sorted(used, key=lambda k: -used[k])[:8]
    under = [o for o in available_ops if o not in used][:14]
    parts = []
    if over:
        parts.append("You have leaned on these operators a lot already — use them SPARINGLY now: "
                     + ", ".join(over) + ".")
    if under:
        parts.append("PREFER these operators you have barely used, to widen your coverage: "
                     + ", ".join(under) + ".")
    return ("\n=== DIVERSITY (personal to you) ===\n" + " ".join(parts) + "\n") if parts else ""


def winners_hint(region: str, field_ids: set | None = None, limit: int = 50) -> str:
    """A light 'what has WORKED for you' signal: the operators (and any currently-selected fields)
    that show up most in your strong, gate-passing alphas — so generation can favour them while the
    diversity hint keeps it from repeating itself. Empty until you have simulation history."""
    try:
        from app.db.base import SessionLocal
        from app.db import models as M
        with SessionLocal() as db:
            rows = db.scalars(
                select(M.SimResult)
                .where(M.SimResult.region == region, M.SimResult.passed_gate == True,  # noqa: E712
                       M.SimResult.fitness.isnot(None))
                .order_by(M.SimResult.fitness.desc())
                .limit(limit)).all()
    except Exception:
        return ""
    ops: dict = {}
    flds: dict = {}
    for r in rows:
        e = r.expression or ""
        for o in set(_operators_in(e)):
            ops[o] = ops.get(o, 0) + 1
        if field_ids:
            for f in set(_fields_in(e, field_ids)):
                flds[f] = flds.get(f, 0) + 1
    top_ops = [o for o, _ in sorted(ops.items(), key=lambda x: -x[1])[:8]]
    top_flds = [f for f, _ in sorted(flds.items(), key=lambda x: -x[1])[:6]]
    if not top_ops and not top_flds:
        return ""
    parts = []
    if top_ops:
        parts.append("operators that have produced strong alphas for you: " + ", ".join(top_ops))
    if top_flds:
        parts.append("selected fields that have worked: " + ", ".join(top_flds))
    return "\n=== WHAT HAS WORKED FOR YOU (favour these, but stay diverse) ===\n" + "; ".join(parts) + ".\n"


def record_usage(region: str, operators: list, fields: list) -> None:
    """Credit this batch so the diversity engine keeps pushing the user outward. Called at
    generation time as a proxy for 'explored'; simulation results will later refine it."""
    now = time.time()
    with SessionLocal() as db:
        for kind, keys in (("operator", operators), ("field", fields)):
            for k in keys:
                row = db.scalar(select(M.Usage).where(
                    M.Usage.kind == kind, M.Usage.key == k, M.Usage.region == region))
                if row:
                    row.count += 1
                    row.last_at = now
                else:
                    db.add(M.Usage(kind=kind, key=k, region=region, count=1, last_at=now))
        db.commit()


# ── prompts ──────────────────────────────────────────────────────────────────────────

def _field_nature(f: dict) -> str:
    """A coarse DATA-NATURE label inferred from the field's type + description so the model can pick
    operators that fit what the field actually measures (continuous / ratio / count / event / group /
    vector). Heuristic — cheap, deterministic, and the FULL description is shown alongside so the LLM
    can refine the judgement itself."""
    if str(f.get("type", "MATRIX")).upper() == "VECTOR":
        return "vector"
    d = (str(f.get("description", "")) + " " + str(f.get("id", ""))).lower()

    def has(*ws):
        return any(w in d for w in ws)

    if has("flag", "indicator", "dummy", "binary", "1 if", "0 or 1", "event", "announc", "rating",
           "recommendation", "upgrade", "downgrade", "buy/sell", "action", "surprise", "beat", "miss"):
        return "event"
    if has("number of", "count", "# of", "days since", "days to", "days from", "quarters", "how many"):
        return "count"
    if has("ratio", "percent", "%", "yield", "margin", "growth", " rate", "per share", "return", "score"):
        return "ratio"
    if has("sector", "industry", "country", "currency", "exchange", "classification", "grouping"):
        return "group"
    return "continuous"


def _field_summary(fields: list, categories: dict, max_fields: int | None = None) -> str:
    lines = []
    for f in fields if max_fields is None else fields[:max_fields]:
        t = str(f.get("type", "MATRIX")).upper()
        nature = _field_nature(f)
        # FULL description (whitespace-normalised) — the model must read what each field means.
        desc = " ".join(str(f.get("description", "")).split()) or "(no description)"
        cat = categories.get(f["id"], "")
        ds = f.get("dataset_id", "")
        tag = f"[{'VECTOR' if t == 'VECTOR' else 'MATRIX'}|{nature}{('|' + cat) if cat else ''}{('|ds:' + ds) if ds else ''}]"
        lines.append(f"  {tag} {f['id']}: {desc}")
    return "\n".join(lines) or "(no fields)"


def _coverage_rule(fields: list, n: int) -> str:
    """Force the batch to span EVERY selected FIELD (and every dataset), never fixating on the
    first one. Per-field coverage is what stops the common failure of pairing one field with all
    the others instead of actually using every selected field."""
    ids = [f.get("id") for f in fields if f.get("id")]
    dsets = list(dict.fromkeys(f.get("dataset_id") for f in fields if f.get("dataset_id")))
    out = ""
    if len(ids) > 1:
        shown = ", ".join(ids[:24]) + (" …" if len(ids) > 24 else "")
        out += ("- USE EVERY SELECTED FIELD: " + str(len(ids)) + " fields were selected (" + shown + "). When the batch is large enough, each should "
                "appear as the PRIMARY field before reuse. When it is smaller, maximize balanced coverage. Do "
                "NOT fixate on the first field in the list and reuse it in every expression — if you selected, say, "
                "several fields from one dataset, actually use those several fields.\n")
    if len(dsets) > 1:
        out += ("- COVER ALL DATASETS EQUALLY: the selected fields span " + str(len(dsets)) + " datasets — "
                + ", ".join(dsets) + ". Distribute the " + str(n) + " expressions as evenly as the batch budget permits; do NOT draw them mostly from one dataset. SINGLE-field mode: rotate the one field through "
                "EVERY dataset across the batch. MULTI-field mode: pair fields so every dataset appears, and when a "
                "dataset contributes several fields, combine those SEVERAL fields — never collapse a dataset to just "
                "its first field. If there are more datasets than expressions, cover as many as possible without weakening economic meaning.\n")
    return out


def _breadth_rule(op_count: int, field_count: int, n: int, max_operators: int) -> str:
    """Force the batch to EXPLOIT the whole toolbox. The common failure is a batch that reuses ~20
    operators and a fraction of the fields even when 130+ operators and 100+ fields were provided;
    breadth targets scale with the batch size so a bigger batch reaches further into the catalogue."""
    if not op_count and not field_count:
        return ""
    # Each expression uses `max_operators` ops, so up to n*max_operators distinct ops are reachable.
    reachable = max(n, n * max(1, _clamp_ops(max_operators)))
    op_target = min(op_count, max(n, int(reachable * 0.6))) if op_count else 0
    field_target = min(field_count, n) if field_count else 0
    out = ""
    if op_target > 1:
        out += (f"- EXPLOIT THE WHOLE OPERATOR TOOLBOX. {op_count} operators are available; across these {n} "
                f"expressions use at LEAST {op_target} DISTINCT operators — do NOT lean on the usual handful "
                "(rank, ts_rank, zscore, ts_mean, ts_delta). Deliberately reach into EVERY family: time-series "
                "(ts_rank, ts_zscore, ts_delta, ts_std_dev, ts_corr, ts_regression, ts_decay_linear, ts_backfill, "
                "hump, kth_element, days_from_last_change…), cross-sectional (rank, zscore, scale, quantile, "
                "winsorize, normalize, sigmoid), group (group_rank, group_zscore, group_neutralize, group_mean…), "
                "arithmetic/logical (add, subtract, divide, power, log, sign, abs, if_else, trade_when, clamp), and "
                "vector (vec_*) wherever VECTOR fields appear. Reusing the same few operators across the batch is a "
                "FAILURE — vary the CORE operator every time.\n")
    if field_target > 1:
        out += (f"- USE THE FIELDS BROADLY. {field_count} datafields were provided; across the batch use at LEAST "
                f"{field_target} DISTINCT datafields (ideally ALL of them). Never confine the batch to a small subset "
                "of the fields.\n")
    return out


_SINGLE_RULES = (
    "- EXACTLY ONE raw datafield per expression (mandatory). Operators needing 2+ inputs must\n"
    "  reuse the SAME field with a DIFFERENT transformation, never two different fields.\n"
    "- Extract signal from that one field in as MANY structural ways as possible across the\n"
    "  batch: arithmetic (differences, ratios, spreads of its own transforms), time-series\n"
    "  (momentum, reversal, decay, volatility, seasonality), cross-sectional (rank, zscore,\n"
    "  scale, winsorize, normalize), group-relative (industry/sector neutralise & compare),\n"
    "  and logical/conditional (regime gates, if_else, sign, clamp). Do NOT repeat one shape.\n"
)
_MULTI_ANTIFIXATE = (
    "- VARY BOTH OPERANDS across the batch: do NOT fix one field (e.g. the first in the list) and pair\n"
    "  it with every other field. Rotate the PRIMARY field through ALL the selected fields; every\n"
    "  selected field must be a primary operand somewhere in the batch.\n"
)


def _multi_rules(fields: list, categories: dict) -> str:
    """STRICT multi-field rules, chosen from the actual selection:
      • ≥2 categories selected → EVERY expression MUST combine exactly two different categories.
      • one dataset/category   → EVERY expression MUST combine 2+ different fields from it.
    (Single-field mode is unchanged.)"""
    cats = list(dict.fromkeys(c for c in (categories or {}).values() if c))
    if len(cats) >= 2:
        return (
            "- MULTI-FIELD, TWO CATEGORIES (STRICT): two data categories are selected (" + ", ".join(cats[:2])
            + "). EVERY expression MUST combine datafields from TWO DIFFERENT categories (each field's\n"
            "  category is in its [TYPE|category] tag) — never a single category, and NEVER three or more\n"
            "  (that overfits and is rejected). Combine them meaningfully: ts_corr(a, b, 60),\n"
            "  subtract(rank(a), rank(b)), ratios, spreads, conditioning.\n"
            + _MULTI_ANTIFIXATE)
    return (
        "- MULTI-FIELD, ONE DATASET (STRICT): a single dataset is selected. EVERY expression MUST combine\n"
        "  TWO OR MORE DIFFERENT datafields from THIS dataset — never just one field, and never a field\n"
        "  from any other dataset. Combine them meaningfully: ratio, spread, ts_corr(a, b, 60), conditioning.\n"
        + _MULTI_ANTIFIXATE)
_COMMON_RULES = (
    "- Use each operator STRICTLY per its signature; windows/lookbacks and groups are POSITIONAL\n"
    "  bare values: ts_rank(x, 20), group_zscore(x, industry). A group is one of industry,\n"
    "  subindustry, sector, market, country, exchange, currency — NEVER a number.\n"
    "- Use ONLY the datafields listed below — NEVER invent, rename, or assume any other field.\n"
    "- READ EACH FIELD'S DESCRIPTION and match operators to its DATA NATURE (shown in the tag as\n"
    "  [TYPE|nature|…]); do NOT apply operators at random:\n"
    "    • continuous / ratio (prices, valuations, margins, yields, returns): cross-sectional rank/\n"
    "      zscore/scale, time-series momentum/reversal/regression, group-relative neutralisation.\n"
    "    • count (\"number of…\", \"days since…\"): decay/ts_sum/ts_mean/ts_delta and ranks; avoid tiny windows.\n"
    "    • event / flag / sparse (announcements, ratings, surprises, 0/1 indicators): EVENT operators —\n"
    "      ts_count, hump, days_from_last_change, kth_element, ts_decay_linear, or use them to GATE another\n"
    "      signal (if_else/trade_when). NEVER push a sparse 0/1 field straight into ts_zscore/ts_rank as if\n"
    "      it were continuous — that has no economic meaning.\n"
    "    • group (sector/industry/country/…): use ONLY as the group argument of a group_* operator.\n"
    "  Every expression must express a COHERENT economic idea for what its fields MEASURE.\n"
    "- FIELD TYPE MATTERS. A field tagged [VECTOR] holds many values per instrument and MUST be\n"
    "  reduced to a scalar by wrapping it in a vec_* operator (vec_avg, vec_sum, vec_max, vec_min,\n"
    "  vec_stddev, vec_norm, …) BEFORE any other operator touches it — e.g. ts_rank(vec_avg(f), 60),\n"
    "  never ts_rank(f, 60) for a VECTOR f. A field tagged [MATRIX] is used directly and must NOT\n"
    "  be wrapped in vec_*. bucket() is valid only when its output supplies the group argument "
    "of a group_* operator (optionally through densify).\n"
    "- DIVERSIFY: every expression must use a DIFFERENT core operator/structure and express a\n"
    "  DIFFERENT economic idea. Spread across ALL the datafields provided — use as many\n"
    "  different fields as there are expressions; if several datasets are present, each must\n"
    "  contribute.\n"
    "- If the instruction includes example expressions, treat them ONLY as ILLUSTRATIONS of the\n"
    "  idea/mechanism — NOT as a restriction on which fields to use. Pick the best datafields for\n"
    "  each hypothesis from the list below; your job is to TEST the hypotheses, not copy examples.\n"
    "- OPERATOR COUNT IS EXACT: every expression must use EXACTLY the operator count stated above —\n"
    "  no more, no fewer. Treat the hypotheses as a DIRECTION, not a whitelist of operators: choose\n"
    "  the operators WISELY to build a sound alpha for that idea. You are NOT limited to operators\n"
    "  that appear in any example, and you must NOT copy the example's structure.\n"
    "- ARGUMENTS: pass any NAMED parameter as a KEYWORD (name=value), never positionally — e.g.\n"
    "  keep(x, f, period=5), ts_regression(y, x, 120, lag=0, rettype=0). Only a plain window/lookback or\n"
    "  a group that the signature lists as positional stays positional. Do not drop a required keyword.\n"
    "- SUPPLY EVERY REQUIRED PARAMETER an operator's signature lists — never call an operator with\n"
    "  fewer arguments than it needs. In particular, operators that require a `sensitivity=` or\n"
    "  `maxpercent=` (or similar) attribute MUST be given it, or BRAIN rejects the whole expression.\n"
    "- GROUP OPERATORS (group_mean, group_neutralize, group_zscore, group_rank, …): the GROUP argument\n"
    "  must be a valid group identifier (industry/subindustry/sector/market/country/exchange/currency) in the\n"
    "  correct position — never a plain field, a number, or another group where a value is expected.\n"
    "- ts_regression(y, x, d, …): x MUST be a DIFFERENT signal from y — a TRANSFORM of the field (or\n"
    "  another field). Never make x identical to y (that regression is degenerate). Options like lag/\n"
    "  rettype are keywords.\n"
    "- densify() applies ONLY to a GROUP (e.g. a bucket()/group identifier), never to a raw datafield.\n"
    "- bucket() BUILDS a group; it is NEVER a standalone alpha expression. Use its output only as the\n"
    "  GROUP argument of a group_* operator, e.g. group_zscore(x, bucket(rank(y), range=\"0,1,0.1\")).\n"
)


def build_prompt(*, mode, prompt, region, delay, instrument, universe, dataset_names,
                 fields, categories, max_operators, n, region_note, allowed_operators=None) -> str:
    ops = engine.operators_df()
    ops = ops[ops["scope"] == "REGULAR"] if ("scope" in ops.columns and (ops["scope"] == "REGULAR").any()) else ops
    # Restrict to an allowed operator set (Strategy Lab constrains generation to the operators it
    # suggested). Only narrow when the filter actually matches something, so a bad list can't empty it.
    allow = {str(o).strip().lower() for o in (allowed_operators or []) if str(o).strip()}
    if allow and "name" in ops.columns:
        f = ops[ops["name"].astype(str).str.lower().isin(allow)]
        if len(f):
            ops = f
    op_summary = wqb_llm._build_operator_summary(ops)
    op_names = [str(x) for x in (ops["name"].tolist() if "name" in ops.columns else [])]
    head = (f"Generate EXACTLY {n} distinct WorldQuant FastExpr alpha expressions.\n"
            f"Market: region {region}, universe {universe}, delay {delay}, {instrument}. "
            f"Datasets: {', '.join(dataset_names) or 'unspecified'}.\n")
    if str(delay) == "0":
        head += ("This is DELAY 0 — favour ideas with a strong risk-adjusted return (Sharpe), "
                 "since delay-0 alphas are judged primarily on Sharpe.\n")
    if region_note:
        head += region_note + "\n"
    task = (prompt + "\n\n") if prompt else ""
    rules = ("=== RULES (invalid expressions are discarded) ===\n"
             + f"- Output EXACTLY {n} expressions.\n"
             + f"- Use AT MOST {_clamp_ops(max_operators)} operators in each expression; use fewer when sufficient.\n"
             + (_SINGLE_RULES if mode == "single" else _multi_rules(fields, categories))
             + _COMMON_RULES
             + _coverage_rule(fields, n)
             + _breadth_rule(len(op_names), len([f for f in fields if f.get("id")]), n, max_operators)
             + (("- USE ONLY these operators (no others): " + ", ".join(op_names) + ".\n") if allow else "")
             + "Return ONLY a JSON array of expression strings.")
    div = _diversity_hint(region, op_names)
    win = winners_hint(region, {f["id"] for f in fields if f.get("id")})
    guide = ""
    if mode == "multi":
        from app.knowledge import categories as cats
        guide = cats.combination_guidance("multi_two_categories", list((categories or {}).values())) + "\n"
    try:
        from app.operators import service as opsvc
        op_examples = opsvc.reference_block("REGULAR")
    except Exception:
        op_examples = ""
    return (f"{head}{task}=== AVAILABLE OPERATORS ===\n{op_summary}\n\n{op_examples}"
            f"=== DATAFIELDS (type|category tagged) ===\n{_field_summary(fields, categories)}\n\n{guide}{rules}{div}{win}")


def rewrite_master_prompt(*, raw, region, delay, instrument, universe, dataset_names, fields,
                          categories, max_operators, n) -> str:
    """AI-driven auto-rewrite: an LLM turns the raw notes into a clean, self-contained instruction
    that PRESERVES every hypothesis and STRICTLY enforces equal exploration across ALL datasets —
    for single-field AND multi-field alphas, even when there are more fields than expressions. Falls
    back to a deterministic template when no LLM is configured. The operator/datafield catalogues are
    still injected separately at generation time, so this never dumps them."""
    static = _static_master_prompt(raw=raw, region=region, delay=delay, instrument=instrument, n=n)
    if not (raw or "").strip():
        return static
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        return static

    ds = list(dict.fromkeys(f.get("dataset_id") for f in fields if f.get("dataset_id")))
    cats = sorted({str(v) for v in (categories or {}).values() if v})
    ds_line = ("The provided datafields span these DATASETS: " + ", ".join(ds) + ". ") if len(ds) > 1 else ""
    cat_line = ("Data categories present: " + ", ".join(cats) + ". ") if cats else ""
    d0 = (" This is DELAY 0 — favour ideas with a strong risk-adjusted return (Sharpe)." if str(delay) == "0" else "")
    meta = (
        "You refine alpha-research instructions for WorldQuant BRAIN. Rewrite the researcher's RAW NOTES below into "
        "ONE clean, self-contained instruction that a generation LLM will follow to write FastExpr alpha expressions.\n"
        "HARD REQUIREMENTS:\n"
        "1. PRESERVE every hypothesis / idea in the notes — do not drop, merge away, or invent ideas. Mechanism-first.\n"
        "2. Structure it as: ## Role, ## Objective, ## Hypotheses (each individually testable), ## Directives.\n"
        f"3. DATASET COVERAGE. {ds_line}{cat_line}Balance expressions across compatible datasets and fields as "
        "far as the requested count permits. Never force an economically weak combination to meet a quota. State "
        "this requirement explicitly in the Directives.\n"
        f"4. OPERATOR COUNT: instruct that every expression uses AT MOST {_clamp_ops(max_operators)} operators, "
        "and fewer whenever they express the mechanism cleanly. The hypotheses are a DIRECTION only: the generator must CHOOSE operators "
        "WISELY to fit each idea, is NOT restricted to any operators named in the notes/examples, and must not copy "
        "example expressions.\n"
        "5. Do NOT list the datafields or the operator catalogue (they are injected separately). Do NOT add concrete "
        "example expressions that would restrict which fields are used. Keep it concise but complete.\n"
        "6. OPERATOR BREADTH: instruct the generator to EXPLOIT THE WHOLE operator toolbox — spread across many "
        "compatible operators from different families when they fit the mechanism; never add an operator merely to meet "
        "a diversity quota — and to USE THE PROVIDED DATAFIELDS BROADLY (ideally all of them), scaled to the number "
        "of expressions. State this in the Directives." + d0 + "\n\n"
        "=== RAW NOTES ===\n" + (raw or "").strip() + "\n\n"
        "Return ONLY a JSON array containing EXACTLY ONE string: the full rewritten instruction (with its \\n line breaks)."
    )
    try:
        from app.core import llm_cache
        res = llm_cache.cached_generate_list(chain, meta, n=1)
        out = (res.expressions[0] if getattr(res, "expressions", None) else "").strip()
        return out or static
    except Exception:
        return static


def _static_master_prompt(*, raw, region, delay, instrument, n) -> str:
    """Deterministic fallback used when no LLM is configured (or the AI rewrite fails)."""
    d0 = (" For delay 0, favour ideas with a strong risk-adjusted return (Sharpe)." if str(delay) == "0" else "")
    return (
        "## Role\n"
        "You are a senior quantitative researcher and WorldQuant BRAIN alpha engineer with a risk-aware "
        "portfolio manager's judgement. Think mechanism-first: what real economic force each idea captures and "
        "why it should persist.\n\n"
        "## Objective\n"
        f"Generate distinct, high-quality FastExpr alpha expressions that TEST every hypothesis/idea below and add "
        f"genuine, low-correlation value in {region} ({instrument}, delay {delay})." + d0 + " Spread the batch "
        "across ALL the available datasets and across DIFFERENT mechanisms — do not fixate on one.\n\n"
        "## Hypotheses / Strategy (test each one; any example expression is an ILLUSTRATION of the mechanism, "
        "NOT a restriction on which datafields to use)\n"
        + (raw or "").strip() + "\n\n"
        "## Directives\n"
        "- Use ONLY the datafields you are given; never invent one. Wrap any VECTOR field in a vec_* operator "
        "before use; use MATRIX fields directly.\n"
        "- Every expression must express a DIFFERENT mechanism/operator family. Prefer real transformations over "
        "filler — no identity arithmetic (no * 1, + 0, / 1).\n"
        "- EXPLOIT THE WHOLE TOOLBOX: spread across many distinct operators from EVERY family (time-series, "
        "cross-sectional, group, arithmetic/logical, vector) and use the provided datafields BROADLY — do not lean "
        "on a handful of operators or a fraction of the fields.\n"
        "- Pass named operator parameters as keywords; groups are identifiers, never numbers.\n"
        "The exact operator list (with correct signatures/examples) and the available datafields are provided "
        "separately at generation time — follow them precisely."
    )


# ── economic-sense critic ────────────────────────────────────────────────────────────

def _economic_review(expressions, fields, region, delay, instrument, chain, min_score=3, progress=None):
    """LLM 'does this make economic sense?' gate. Judges each candidate against what its datafields
    MEASURE (not just syntax) and drops the ones with no coherent, testable rationale — so simulation
    quota isn't spent on nonsense. One call for the whole batch. FAILS OPEN (keeps everything) on any
    error or if it would drop the entire batch, so a critic hiccup never throws away a good generation.
    Works identically for single- and multi-field output. Returns (kept, [(expr, reason), …])."""
    exprs = [e for e in expressions if e]
    if not exprs or not chain:
        return list(exprs), []
    field_ids = {f["id"] for f in fields}
    fmap = {f["id"]: f for f in fields}
    used = set()
    for e in exprs:
        used |= set(_fields_in(e, field_ids))
    desc_lines = []
    for fid in sorted(used):
        f = fmap.get(fid)
        if f:
            d = " ".join(str(f.get("description", "")).split()) or "(no description)"
            desc_lines.append(f"  {fid} ({_field_nature(f)}): {d}")
    numbered = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(exprs))
    meta = (
        "You are a STRICT WorldQuant BRAIN alpha reviewer. For EACH candidate expression below, judge "
        "whether it expresses a COHERENT, TESTABLE economic idea GIVEN WHAT ITS DATAFIELDS MEASURE — not "
        "merely whether it is syntactically valid.\n"
        "DROP when: an operator does not fit the field's data nature (e.g. ts_zscore/ts_rank on a sparse "
        "0/1 event field), fields are combined with no plausible thesis, the maths is degenerate/identity, "
        "or the idea is incoherent. KEEP genuinely sensible, testable ideas.\n"
        f"Region {region}, delay {delay}, {instrument}.\n"
        "=== DATAFIELDS (id (nature): description) ===\n" + ("\n".join(desc_lines) or "(none)") + "\n"
        "=== CANDIDATES (review each, IN ORDER) ===\n" + numbered + "\n"
        f"Return ONLY a JSON array of EXACTLY {len(exprs)} strings, one per candidate IN THE SAME ORDER, "
        'each EXACTLY: "KEEP|<score 1-5>|<one concise reason>" or "DROP|<score 1-5>|<reason>".'
    )
    if progress:
        try:
            progress(message=f"reviewing economic sense of {len(exprs)}…")
        except Exception:
            pass
    try:
        res = L.MultiLLM(chain).generate_list(meta, n=len(exprs))
        verdicts = [str(x) for x in (res.expressions or [])]
    except Exception:
        return list(exprs), []
    if len(verdicts) != len(exprs):
        return list(exprs), []
    kept, dropped = [], []
    for e, v in zip(exprs, verdicts):
        parts = [p.strip() for p in v.split("|", 2)]
        decision = (parts[0].upper() if parts else "KEEP")
        try:
            m = re.search(r"\d", parts[1]) if len(parts) > 1 else None
            score = int(m.group()) if m else 5
        except Exception:
            score = 5
        reason = parts[2] if len(parts) > 2 else ""
        if decision.startswith("DROP") or score < min_score:
            dropped.append((e, reason or "weak economic rationale"))
        else:
            kept.append(e)
    if not kept:
        return list(exprs), []               # never wipe the whole batch on the critic's say-so
    return kept, dropped


# ── run ──────────────────────────────────────────────────────────────────────────────

def run_generation(*, mode, prompt, region, delay, instrument, universe, dataset_names,
                   fields, categories, max_operators, n, repair_rounds, region_note, progress,
                   raw_prompt=False, economic_review=True, economic_min=3, allowed_operators=None) -> dict:
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    progress(message=f"generating with {', '.join(p.name for p in chain)}…")
    val = _validator(fields, max_operators, multi_field=(mode == "multi"))
    if raw_prompt and prompt.strip():
        # A user-authored / auto-rewritten master prompt: send it as-is, only guaranteeing the
        # output contract. Validation against the provided fields still applies.
        full = prompt.strip()
        if "json array" not in full.lower():
            full += f"\n\nReturn ONLY a JSON array of exactly {n} expression strings."
    else:
        full = build_prompt(mode=mode, prompt=prompt, region=region, delay=delay, instrument=instrument,
                            universe=universe, dataset_names=dataset_names, fields=fields,
                            categories=categories, max_operators=max_operators, n=n, region_note=region_note,
                            allowed_operators=allowed_operators)
    multi = L.MultiLLM(chain)
    out = L.generate_valid(multi, val, full, repair_rounds=repair_rounds)

    valid = out.get("valid", [])
    rejected = list(out.get("rejected", []))
    field_ids = {f["id"] for f in fields}

    # Operator keyword / semantic enforcement (keyword-required args, bucket-as-group,
    # densify-on-group, ts_regression x!=y) — reject anything the LLM got wrong.
    specs = _param_specs()
    kept2 = []
    for e in valid:
        extra = operator_issues(e, specs)
        if extra:
            rejected.append((e, [type("I", (), {"code": c})() for c, _ in extra]))
        else:
            kept2.append(e)
    valid = kept2

    # STRICT multi-field guard (belt-and-braces beyond the prompt):
    #   • ≥2 categories selected → every expression MUST combine EXACTLY two different categories.
    #   • one dataset/category   → every expression MUST combine 2+ distinct datafields.
    # (Single-field mode is untouched.)
    if mode == "multi":
        distinct_cats = len({c for c in (categories or {}).values() if c})
        kept = []
        for e in valid:
            used_fields = set(_fields_in(e, field_ids))
            used_cats = {categories.get(fid) for fid in used_fields if categories.get(fid)}
            if distinct_cats >= 2:
                if len(used_cats) != 2:
                    code = "NEEDS_TWO_CATEGORIES" if len(used_cats) < 2 else "TOO_MANY_CATEGORIES"
                    rejected.append((e, [type("I", (), {"code": code})()]))
                    continue
            elif len(used_fields) < 2:
                rejected.append((e, [type("I", (), {"code": "NEEDS_MULTIPLE_FIELDS"})()]))
                continue
            kept.append(e)
        valid = kept

    # Economic-sense gate (SINGLE and MULTI alike): drop candidates that don't make economic sense
    # given what their fields measure, so simulation quota is never spent on nonsense. The reason is
    # surfaced in 'rejected' so the user sees WHY (and can turn the gate off).
    if economic_review and valid:
        kept, dropped = _economic_review(valid, fields, region, delay, instrument, chain,
                                         min_score=economic_min, progress=progress)
        for e, reason in dropped:
            rejected.append((e, [type("I", (), {"code": f"no economic sense: {reason}"[:140]})()]))
        valid = kept

    # Diversity credit + report.
    all_ops, all_fields = [], []
    for e in valid:
        all_ops += _operators_in(e)
        all_fields += _fields_in(e, field_ids)
    if valid:
        try:
            record_usage(region, list(set(all_ops)), list(set(all_fields)))
        except Exception:
            pass

    progress(message=f"{len(valid)} valid via {out.get('provider')}")
    return {
        "valid": valid,
        "rejected": [{"expr": e, "issues": [getattr(i, "code", str(i)) for i in iss]} for e, iss in rejected],
        "provider": out.get("provider"), "model": out.get("model"),
        "report": {"total": len(valid) + len(rejected), "valid": len(valid), "rejected": len(rejected)},
        "operators_used": sorted(set(all_ops)),
        "fields_used": sorted(set(all_fields)),
    }


def validate_expressions(exprs: list, fields: list, max_operators: int, multi_field: bool) -> dict:
    val = _validator(fields, max_operators, multi_field)
    specs = _param_specs()
    results = []
    for e in exprs:
        r = val.validate(e)
        extra = operator_issues(e, specs) if r.ok else []
        ok = r.ok and not extra
        issues = [{"code": i.code, "message": i.message} for i in r.issues] + \
                 [{"code": c, "message": m} for c, m in extra]
        results.append({"expr": e, "ok": ok, "issues": issues})
    valid = sum(1 for x in results if x["ok"])
    return {"results": results, "report": {"total": len(exprs), "valid": valid, "rejected": len(exprs) - valid}}


_SLOT_RE = __import__("re").compile(r"\{(field\d*)(?::([A-Za-z]+))?\}")


def expand_templates(templates: list, field_ids: list, fields: list, vec_ops: list,
                     max_operators: int, multi_field: bool, max_combos: int = 60,
                     field2_ids: list | None = None) -> dict:
    """Expand {field} templates over the selected fields. VECTOR fields fan out across each
    vec_* reduction; multi-field templates ({field2}) take a bounded Cartesian product, drawing
    {field2} from field2_ids when given (so the user can pick a different dataset — or their own
    fields — for the second slot). Everything is validated."""
    from itertools import product
    types = {f["id"]: str(f.get("type", "MATRIX")).upper() for f in fields}
    pool2 = field2_ids or field_ids   # {field2} pool; default to the same fields

    def variants(fid):
        if types.get(fid, "MATRIX") == "VECTOR":
            return [f"{op}({fid})" for op in (vec_ops or ["vec_avg"])]
        return [fid]

    def pool_for(name: str) -> list:
        return pool2 if name == "field2" else field_ids

    exprs, seen = [], set()
    # max_combos <= 0 means "all possible combinations" (bounded only by a safety ceiling so a
    # pathological pool can't build hundreds of thousands of strings). A positive value caps it.
    CEIL = 20000
    mc = int(max_combos or 0)
    cap = CEIL if mc <= 0 else max(1, min(mc, CEIL))
    for t in [x for x in templates if x.strip()]:
        names = sorted({n for n, _ in _SLOT_RE.findall(t)})
        if not names:
            if t not in seen:
                seen.add(t); exprs.append(t)
            continue
        if len(names) == 1:
            for fid in pool_for(names[0]):
                for v in variants(fid):
                    e = _SLOT_RE.sub(lambda m: v, t)
                    if e not in seen:
                        seen.add(e); exprs.append(e)
        else:
            pools = [pool_for(nm) for nm in names]
            # Build every valid combo (no slot reuses another slot's field — so {field} != {field2}),
            # then REORDER so a small cap still spans many distinct {field} values. Plain product()
            # fixes {field} to the first field and burns the whole cap pairing it with every
            # {field2} before advancing; instead we round-robin by the {field} slot — one pairing
            # from each {field} value in rotation — so the sample goes DEEP across fields.
            groups: dict = {}
            for combo in product(*pools):
                if len(set(combo)) != len(combo):   # any two slots the same field -> skip
                    continue
                groups.setdefault(combo[0], []).append(combo)
            ordered, depth = [], 0
            while any(depth < len(g) for g in groups.values()):
                for g in groups.values():
                    if depth < len(g):
                        ordered.append(g[depth])
                depth += 1
            n = 0
            for combo in ordered:
                # Each slot expands independently. If either {field} or {field2} is VECTOR,
                # fan out over every selected vec_* reducer. User-entered custom ids remain
                # MATRIX because their type cannot be verified locally.
                for reduced in product(*(variants(fid) for fid in combo)):
                    mapping = {names[i]: reduced[i] for i in range(len(names))}
                    e = _SLOT_RE.sub(lambda m: mapping[m.group(1)], t)
                    if e not in seen:
                        seen.add(e); exprs.append(e); n += 1
                    if n >= cap:
                        break
                if n >= cap:
                    break
    # Validate against ALL fields that can appear (field1 + field2 pools + any custom).
    val = _validator(fields, max_operators, multi_field)
    specs = _param_specs()
    results = []
    for e in exprs:
        r = val.validate(e)
        extra = operator_issues(e, specs) if r.ok else []
        ok = r.ok and not extra
        results.append({"expr": e, "ok": ok, "issues": [i.code for i in r.issues] + [c for c, _ in extra]})
    valid = sum(1 for x in results if x["ok"])
    return {"expressions": exprs, "results": results,
            "report": {"total": len(exprs), "valid": valid, "rejected": len(exprs) - valid}}


def explain_expression(*, expression, fields, region, delay, instrument) -> dict:
    """Plain-English economic rationale for ONE expression, grounded in its fields' meanings."""
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    field_ids = {f["id"] for f in fields if f.get("id")}
    fmap = {f["id"]: f for f in fields}
    desc = []
    for fid in sorted(set(_fields_in(expression, field_ids))):
        f = fmap.get(fid)
        if f:
            d = " ".join(str(f.get("description", "")).split()) or "(no description)"
            desc.append(f"  {fid} ({_field_nature(f)}): {d}")
    meta = (
        f"Explain this WorldQuant BRAIN FastExpr alpha to a researcher in plain English (region {region}, "
        f"delay {delay}, {instrument}): what economic signal it captures, why it might predict returns, how "
        "the operators transform the data, and its main risk/weakness. Be concrete, 4-6 sentences.\n"
        "=== DATAFIELDS USED ===\n" + ("\n".join(desc) or "(none named)") + "\n"
        "=== EXPRESSION ===\n" + str(expression) + "\n"
        "Return ONLY a JSON array containing EXACTLY ONE string: the explanation."
    )
    try:
        res = L.MultiLLM(chain).generate_list(meta, n=1)
        text = (res.expressions[0] if res.expressions else "").strip()
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not explain: {str(e).splitlines()[0][:160]}"}
    return {"explanation": text or "(no explanation returned)", "provider": res.provider, "model": res.model}


def suggest_field_picks(*, region, delay, instrument, universe, dataset_names, fields, categories,
                        max_operators, n, multi_field, progress) -> dict:
    """The LLM reads the fields' FULL descriptions + data nature and proposes N concrete,
    economically-motivated field+template picks for the user to SELECT — so the search starts from
    sensible combos, not a blind sweep. Each pick names the exact field id(s), a ready template, and
    a one-line reason. Only picks whose fields actually exist are returned."""
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    n_ops = _clamp_ops(max_operators)
    field_ids = {str(f.get("id")) for f in fields if f.get("id")}
    op_summary = operator_summary()
    try:
        from app.operators import service as opsvc
        op_examples = opsvc.reference_block("REGULAR")
    except Exception:
        op_examples = ""
    slot = ("Each pick uses TWO fields — {field} and {field2} — that interact meaningfully (ratio, spread, "
            "correlation, conditioning)." if multi_field
            else "Each pick uses ONE field — {field} — extracted deeply.")
    meta = (
        "You are choosing WHICH datafields are worth testing and HOW, for a WorldQuant BRAIN researcher.\n"
        f"Region {region}, delay {delay}, {instrument}. Datasets: {', '.join(dataset_names) or 'unspecified'}.\n"
        f"From the datafields below, propose {n} DISTINCT, economically-motivated ideas. {slot} Use EXACTLY "
        f"{n_ops} operators, MATCHED to each field's DATA NATURE (continuous/ratio -> rank/zscore/regression; "
        "count -> decay/sums; event/flag -> ts_count/hump/days_from_last_change or as a gate; group -> group_* "
        "only). Choose fields whose DESCRIPTION supports a real economic thesis; skip the rest.\n"
        "USE ONLY operators from the ATLAS below, with their EXACT signatures/arity — NEVER invent an operator "
        "or guess a signature. Pass named parameters as keywords; windows/groups are positional; groups are "
        "identifiers (industry/sector/…), never numbers. bucket() may build a group only inside a "
        "group_* group argument. Do not add vec_* here because VECTOR reduction is applied later.\n"
        f"=== OPERATORS (use ONLY these) ===\n{op_summary}\n{op_examples}"
        "=== DATAFIELDS (id (nature): description) ===\n" + _field_summary(fields, categories) + "\n"
        f"Return ONLY a JSON array of EXACTLY {n} strings, each EXACTLY of the form:\n"
        + ('"<field id>|<field2 id> | <template using {field} and {field2}> | <one-line rationale>".'
           if multi_field else
           '"<field id> | <template using {field}> | <one-line rationale>".')
    )
    progress(message=f"choosing {n} field ideas…")
    res = L.MultiLLM(chain).generate_list(meta, n=n)

    import re
    lo = {fid.lower(): fid for fid in field_ids}   # case-insensitive lookup -> canonical id

    def _canon(x):
        x = str(x).strip().strip("`\"' ").strip()
        return lo.get(x.lower())

    def _placeholderize(tmpl, f1, f2=None):
        # If the model wrote the CONCRETE expression (real field ids) instead of {field}/{field2},
        # turn it back into a template so it's usable.
        if f1 and "{field}" not in tmpl:
            tmpl = re.sub(r"(?<![\w.])" + re.escape(f1) + r"(?![\w.])", "{field}", tmpl, flags=re.IGNORECASE)
        if f2 and "{field2}" not in tmpl:
            tmpl = re.sub(r"(?<![\w.])" + re.escape(f2) + r"(?![\w.])", "{field2}", tmpl, flags=re.IGNORECASE)
        return tmpl

    picks = []
    for v in (res.expressions or []):
        parts = [p.strip() for p in str(v).split("|")]
        if multi_field:
            if len(parts) < 3:
                continue
            f1, f2, tmpl = _canon(parts[0]), _canon(parts[1]), parts[2]
            reason = parts[3] if len(parts) > 3 else ""
            if not f1 or not f2:
                continue
            tmpl = _placeholderize(tmpl, f1, f2)
            if "{field}" not in tmpl or "{field2}" not in tmpl:
                continue
            chosen = [f1, f2]
        else:
            if len(parts) < 2:
                continue
            f1, tmpl = _canon(parts[0]), parts[1]
            reason = parts[2] if len(parts) > 2 else ""
            if not f1:
                continue
            tmpl = _placeholderize(tmpl, f1)
            if "{field}" not in tmpl:
                continue
            chosen = [f1]
        picks.append({"fields": chosen, "template": tmpl, "reason": reason})
    return {"picks": picks, "provider": res.provider, "model": res.model,
            "raw": [] if picks else [str(x)[:160] for x in (res.expressions or [])[:4]]}


def suggest_templates(*, region, delay, instrument, universe, dataset_names, fields, categories,
                      max_operators, n, multi_field, progress) -> dict:
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    n_ops = _clamp_ops(max_operators)
    slot = ("Every template MUST contain BOTH {field} and {field2} (a two-field idea) — this is the "
            "MULTI-FIELD mode, so make the two placeholders interact meaningfully (ratio, spread, "
            "correlation, conditioning). Produce MANY structurally different two-field combinations."
            if multi_field
            else "Each template MUST contain {field} EXACTLY once and MUST NOT contain {field2}.")
    op_summary = operator_summary()
    try:
        from app.operators import service as opsvc
        op_examples = opsvc.reference_block("REGULAR")
    except Exception:
        op_examples = ""
    meta = (
        "Design reusable WorldQuant FastExpr alpha TEMPLATES.\n"
        f"Region {region}, universe {universe}, delay {delay}, {instrument}. Datasets: {', '.join(dataset_names) or 'unspecified'}.\n"
        f"Propose {n} templates. {slot} Use EXACTLY {n_ops} operators per template.\n"
        "CRITICAL: a template is an abstract pattern, so use ONLY the literal placeholders {field}"
        + (" and {field2}" if multi_field else "") + " where a datafield goes. NEVER write an actual "
        "field id, dataset name, or any concrete data value in a template — placeholders only.\n"
        "NO FILLER ARITHMETIC: never pad a template with identity operations — no '* 1', '+ 0', '/ 1', "
        "'- 0', '0 *', '1 /'. Every arithmetic operator must CHANGE the signal (a real ratio, spread, or "
        "sum of two DIFFERENT transformed terms), otherwise use none.\n"
        "PASS NAMED PARAMETERS AS KEYWORDS (e.g. keep(x, f, period=5), ts_decay_linear(x, 20, dense=false)); "
        "windows/groups are positional; groups are identifiers (industry/sector/…), never numbers.\n"
        "DIVERSIFY: every template a DIFFERENT economic idea AND operator family. bucket() is allowed only "
        "as a group_* group argument. Do not use vec_* here (the placeholder is substituted with an already-reduced field, so VECTOR handling is "
        "applied at expansion time).\n"
        f"=== OPERATORS ===\n{op_summary}\n{op_examples}"
        f"=== DATAFIELDS (for context only — do NOT put these ids in a template) ===\n{_field_summary(fields, categories)}\n"
        "Return ONLY a JSON array of template strings using placeholders only.")
    progress(message="suggesting templates…")
    res = L.MultiLLM(chain).generate_list(meta, n=n)
    import re as _re
    banned = _re.compile(r"\bbucket\s*\(|\bvec_[A-Za-z0-9_]*\s*\(", _re.IGNORECASE)
    # identity arithmetic: `* 1`, `+ 0`, `/ 1`, `- 0`, `0 *`, `1 /` and similar padding
    filler = _re.compile(r"([*/+\-]\s*[01](?![.\d])|(?<![.\d])[01]\s*[*/])")
    field_ids = {str(f.get("id")) for f in fields if f.get("id")}

    def literal_free(t: str) -> bool:
        toks = set(_re.findall(r"[A-Za-z_][A-Za-z0-9_]*", t))
        return not (toks & field_ids)

    need2 = "{field2}" if multi_field else None
    tmpls = [t.strip() for t in res.expressions
             if "{field}" in t and (need2 is None or need2 in t)
             and not banned.search(t) and not filler.search(t) and literal_free(t)]
    return {"templates": tmpls[:max(1, n * 2)], "provider": res.provider, "model": res.model}


def operator_summary() -> str:
    """Compact REGULAR-operator reference (name + signature) other LLM screens can inject so
    the model only uses operators that actually exist with correct arity."""
    ops = engine.operators_df()
    scoped = ops[ops["scope"] == "REGULAR"] if ("scope" in ops.columns and (ops["scope"] == "REGULAR").any()) else ops
    return wqb_llm._build_operator_summary(scoped)


def operators_list() -> list:
    reg = _registry("REGULAR")
    return [{"name": n, "min": s.min_args, "max": s.max_args, "params": s.param_names}
            for n, s in sorted(reg._sigs.items())]


def sandbox_validate(expr: str) -> dict:
    """Operator-sandbox check: validates syntax, operator existence/arity, keyword args and the
    semantic rules — but NOT field membership (any field name is allowed), so a user can test an
    operator's usage without first selecting matching datafields."""
    val = V.Validator(_registry("REGULAR"), known_fields=None, field_types={},
                      check_unknown_kwargs=True, require_single_field=False,
                      op_count_ignore_prefixes=("vec_",))
    r = val.validate(expr)
    extra = operator_issues(expr, _param_specs())
    ok = r.ok and not extra
    return {"ok": ok, "issues": [{"code": i.code, "message": i.message} for i in r.issues]
            + [{"code": c, "message": m} for c, m in extra]}


_TEMPLATIZE_SKIP = _GROUP_IDENTS | {"true", "false", "nan", "inf", "none"}


def _strip_vec_wrappers(s: str) -> str:
    """Unwrap any vec_*( … ) → its inner expression. A template must NOT carry vec_* operators —
    VECTOR fields get re-wrapped with the user's chosen vec op at EXPANSION time, so leaving one in
    the template double-wraps. Balanced-paren unwrap, repeated until none remain."""
    import re as _re
    pat = _re.compile(r"vec_[A-Za-z0-9_]+\s*\(", _re.IGNORECASE)
    while True:
        m = pat.search(s)
        if not m:
            return s
        start, open_i = m.start(), m.end() - 1
        depth, close = 0, -1
        for i in range(open_i, len(s)):
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close == -1:
            return s
        s = s[:start] + s[open_i + 1:close] + s[close + 1:]


def templatize(expr: str) -> dict:
    """Turn a concrete expression back into a TEMPLATE: replace each datafield with {field}/
    {field2}/… placeholders, and map each field to its dataset (from the knowledge DB) so the user
    knows which dataset to retry with. Operators, group identifiers and numbers are left alone.
    vec_* wrappers are stripped (re-applied at expansion time)."""
    import re as _re
    expr = _strip_vec_wrappers(str(expr))
    try:
        ast = V.parse(expr)
    except Exception:
        return {"template": expr, "fields": [], "datasets": [], "multi": False, "error": "could not parse"}
    # Operators are Call.name strings (not Ident nodes), so any Ident here is a datafield, a group
    # identifier or a literal — no operator list / network needed.
    names, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            nm = n.name
            if nm.lower() not in _TEMPLATIZE_SKIP:
                names.append(nm)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    distinct = list(dict.fromkeys(names))
    mapping = {f: ("{field}" if i == 0 else "{field2}" if i == 1 else "{field%d}" % (i + 1))
               for i, f in enumerate(distinct)}
    template = expr
    for f, ph in mapping.items():
        template = _re.sub(r"(?<![\w.])" + _re.escape(f) + r"(?![\w])", ph, template)

    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    fields_out = []
    with SessionLocal() as db:
        for f in distinct:
            row = db.scalar(select(M.Field).where(M.Field.field_id == f))
            fields_out.append({"id": f, "dataset_id": (row.dataset_id if row else ""), "type": (row.type if row else "MATRIX")})
    return {"template": template, "fields": fields_out, "multi": len(distinct) > 1,
            "datasets": list(dict.fromkeys(x["dataset_id"] for x in fields_out if x["dataset_id"]))}


# ── prompt library ───────────────────────────────────────────────────────────────────

def save_template_asset(payload: dict) -> dict:
    name = str(payload.get("name") or "Untitled template").strip()[:200]
    text = str(payload.get("template_text") or "").strip()
    if not text: raise ValueError("Template text is required.")
    with SessionLocal() as db:
        row = db.scalar(select(M.TemplateAsset).where(M.TemplateAsset.name == name))
        if not row:
            row = M.TemplateAsset(name=name); db.add(row); db.flush()
        revision = int(db.scalar(select(func.max(M.TemplateRevision.revision)).where(M.TemplateRevision.asset_id == row.id)) or 0) + 1
        row.template_text=text; row.nugget=str(payload.get("nugget") or "")
        row.context_json=json.dumps(payload.get("context") or {}); row.updated_at=time.time()
        db.add(M.TemplateRevision(asset_id=row.id,revision=revision,template_text=text,nugget=row.nugget,context_json=row.context_json))
        db.commit(); db.refresh(row)
        return {"id": row.id, "name": row.name, "revision": revision}


def list_template_assets(limit: int = 100) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(M.TemplateAsset).order_by(desc(M.TemplateAsset.updated_at)).limit(limit)).all()
        versions={x.id:int(db.scalar(select(func.max(M.TemplateRevision.revision)).where(M.TemplateRevision.asset_id==x.id)) or 1) for x in rows}
    return [{"id": x.id, "name": x.name, "template_text": x.template_text, "nugget": x.nugget,
             "context": json.loads(x.context_json or "{}"), "updated_at": x.updated_at,"revision":versions[x.id]} for x in rows]


def template_revisions(asset_id:int)->list[dict]:
    with SessionLocal() as db:
        rows=db.scalars(select(M.TemplateRevision).where(M.TemplateRevision.asset_id==asset_id).order_by(desc(M.TemplateRevision.revision))).all()
    return [{"id":x.id,"revision":x.revision,"created_at":x.created_at,"template_text":x.template_text,
             "nugget":x.nugget,"context":json.loads(x.context_json or "{}")} for x in rows]


def delete_template_asset(asset_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(M.TemplateAsset, asset_id)
        if not row: return False
        db.delete(row); db.commit(); return True

def list_prompts(scope: str = "") -> list:
    with SessionLocal() as db:
        q = select(M.Prompt).order_by(M.Prompt.created_at.desc())
        if scope:
            q = q.where(M.Prompt.scope == scope)
        rows = db.scalars(q.limit(100)).all()
        import json
        return [{"id": r.id, "name": r.name, "scope": r.scope, "category": r.category,
                 "region": r.region, "body": r.body, "created_at": r.created_at,
                 "datasets": json.loads(r.datasets_json or "[]")} for r in rows]


def delete_prompt(pid: int) -> None:
    with SessionLocal() as db:
        row = db.get(M.Prompt, pid)
        if row:
            db.delete(row)
            db.commit()


def clear_prompts() -> dict:
    """Delete ALL saved prompts."""
    with SessionLocal() as db:
        n = db.query(M.Prompt).delete()
        db.commit()
    return {"deleted": int(n or 0)}
