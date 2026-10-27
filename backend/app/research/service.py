"""Research engine.

An LLM research step that is deliberately SEPARATE from generation: the user researches a
category / datasets / region (optionally attaching a research paper), gets structured
hypotheses grounded in the exact fields provided, and only then pushes chosen ideas to
generation or templates. It reuses the studio's provider stack (llm_providers, keys) via
the brain adapter's sys.path, so no LLM code is duplicated.

Output is requested as a JSON array of delimited thesis records. Expression compilation is
deliberately deferred to Generation so a useful hypothesis is not lost to a syntax mistake.
"""

from __future__ import annotations

import json
import hashlib
import time

from app.brain import engine  # noqa: F401  — loads the vendored engine

import llm_providers as L  # noqa: E402
import keys as keymgr       # noqa: E402

DELIM = "|||"


def providers() -> dict:
    preferred = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()],
            "preferred": preferred,
            "used": [p.name for p in L.default_chain(preferred)]}


def _field_summary(fields: list, categories: dict | None = None, max_fields: int | None = None) -> str:
    # Reuse Generation's grounded summary: FULL descriptions + a data-nature tag (continuous/ratio/
    # count/event/group/vector), so hypotheses are formed from what each field actually measures.
    from app.generation import service as gen
    return gen._field_summary(fields, categories or {}, max_fields=max_fields)


def _generalisation_rule(categories: dict) -> str:
    cats = sorted({str(c) for c in (categories or {}).values() if c})
    if len(cats) > 1:
        return ("GENERALISE ACROSS CATEGORIES: several data categories are present (" + ", ".join(cats) +
                "). Frame each hypothesis at the CATEGORY / mechanism level and span the different "
                "categories — not a single field or a single category.\n")
    return ("GENERALISE ACROSS THE DATAFIELDS: a single category is being explored, so frame each "
            "hypothesis generally over its datafields (the shared signal they carry), not overfit to one field.\n")


_MODE_RULES = {
    "single": (
        "FIELD USE — SINGLE FIELD (default): each candidate FastExpr uses EXACTLY ONE datafield. "
        "Operators needing 2+ inputs reuse that SAME field under a DIFFERENT transformation — never a "
        "second field."),
    "multi_single_dataset": (
        "FIELD USE — MULTI-FIELD, ONE DATASET (STRICT): each candidate MUST combine TWO OR MORE DIFFERENT "
        "datafields, ALL from the SAME dataset — never just one field, and never a field from another "
        "dataset. Combine them meaningfully (ratio, spread, ts_corr, conditioning)."),
    "multi_two_categories": (
        "FIELD USE — MULTI-FIELD, TWO CATEGORIES (STRICT): each candidate MUST combine datafields from TWO "
        "DIFFERENT categories (each field's category is in its [TYPE|category] tag) — never a single "
        "category, and NEVER three or more (that overfits and is rejected)."),
}


def _prompt(category, region, delay, instrument, dataset_names, fields, categories, goal,
            paper_text, paper_is_community, mode, max_operators, n, general=False) -> str:
    paper_block = ""
    if paper_text:
        which = ("This is a WorldQuant COMMUNITY paper — map its mechanism ONLY onto the specific datasets/"
                 "fields the user selected for it. " if paper_is_community else
                 "This is the user's own paper. ")
        paper_block = ("\n=== COMPLETE RESEARCH PAPER ===\n" + which +
                       "Extract the underlying economic mechanism and adapt it to the datafields below. "
                       "Read all supplied pages before deciding what is relevant. Do NOT invent or reference "
                       "any field the paper mentions but that is not in the list.\n"
                       + paper_text + "\n")
    goal_line = f"User goal: {goal}\n" if goal else ""
    mode_rule = _MODE_RULES.get(mode, _MODE_RULES["single"])
    from app.knowledge import categories as cats
    field_block = _field_summary(fields, categories) if fields else "No fields are bound yet."
    grounding = ("Create portable hypotheses at the economic-mechanism level. State the data properties "
                 "needed to test each thesis; do not invent field identifiers." if general else
                 "Ground every thesis in the supplied field descriptions, while keeping the mechanism reusable.")
    cat_guide = cats.combination_guidance(mode, list((categories or {}).values()))
    return (
        "You are a senior quantitative researcher designing alpha ideas for the WorldQuant BRAIN platform. "
        "Your hypotheses must be concrete, mechanism-first, falsifiable, and directly testable.\n"
        f"Market context: region {region}, delay {delay}, {instrument}. "
        f"Data category: {category or 'unspecified'}. Datasets: {', '.join(dataset_names) or 'unspecified'}.\n"
        "Reason explicitly about what drives THIS market and region — liquidity and microstructure, who "
        "trades and why, settlement/delay conventions, how and how fast information diffuses into prices — "
        "and about which of the fields below actually captures that force, on what horizon, and why the "
        "effect should persist rather than be arbitraged away.\n"
        + (f"NOTE: delay {delay} — for delay 0, favour ideas whose edge is a strong risk-adjusted return "
           "(Sharpe), since delay-0 alphas are judged primarily on Sharpe.\n" if str(delay) == "0" else "")
        + goal_line + paper_block +
        f"\n{grounding}\nPropose {n} DISTINCT, testable hypotheses. Vary the economic mechanism, horizon, "
        "conditioning information and expected sign. Do not force novelty through unnecessary operators.\n"
        + _generalisation_rule(categories) +
        "Balance coverage across the available datasets/categories when the hypothesis budget permits. "
        "Never add a weak thesis merely to satisfy a quota.\n"
        f"Implementation constraint for later expression generation: {mode_rule} Use at most {max_operators} "
        "operators, and fewer when they express the thesis cleanly.\n"
        + cat_guide + "\n"
        + f"=== AVAILABLE DATA CONTEXT ===\n{field_block}\n\n"
        + "Every hypothesis must state a baseline, a competing explanation, and a falsification rule. Reject "
        "circular mechanisms, target leakage, contemporaneous information unavailable at the chosen delay, and "
        "claims the selected data cannot test.\n"
        + f"Return ONLY a JSON array of {n} strings. Each string MUST use this exact delimited form:\n"
        f"  idea {DELIM} economic mechanism {DELIM} expected sign {DELIM} horizon in days {DELIM} confidence 1-5 "
        f"{DELIM} falsification test {DELIM} data requirements {DELIM} information arrival {DELIM} applicable regime "
        f"{DELIM} necessary transformation {DELIM} neutralization rationale {DELIM} simple baseline "
        f"{DELIM} alternative explanation {DELIM} decision rule\n"
    )


def _parse(items: list) -> tuple[list, list]:
    hypotheses, expressions = [], []
    for s in items:
        parts = [p.strip() for p in str(s).split(DELIM)]
        if len(parts) >= 2:
            # Ideation returns research metadata; expressions are compiled and repaired later.
            conf = ""
            conf = parts[4] if len(parts) > 4 else ""
            expr = ""
            try:
                conf_n = max(1, min(5, int(float(conf))))
            except (TypeError, ValueError):
                conf_n = 0
            h = {"idea": parts[0], "mechanism": parts[1],
                 "sign": parts[2] if len(parts) > 2 else "",
                 "horizon": parts[3] if len(parts) > 3 else "",
                 "confidence": conf_n, "falsification": parts[5] if len(parts) > 5 else "",
                 "field_requirements": parts[6] if len(parts) > 6 else "",
                 "information_arrival": parts[7] if len(parts) > 7 else "",
                 "regime": parts[8] if len(parts) > 8 else "",
                 "transformation": parts[9] if len(parts) > 9 else "",
                 "neutralization_rationale": parts[10] if len(parts) > 10 else "",
                 "baseline": parts[11] if len(parts) > 11 else "",
                 "alternative_explanation": parts[12] if len(parts) > 12 else "",
                 "decision_rule": parts[13] if len(parts) > 13 else "continue, revise, invert, combine, or abandon after falsification",
                 "quality_checks": {"falsifiable": bool(parts[5] if len(parts) > 5 else ""),
                    "has_baseline": bool(parts[11] if len(parts) > 11 else ""),
                    "has_alternative": bool(parts[12] if len(parts) > 12 else ""),
                    "leakage_reviewed": True}, "expression": expr}
            hypotheses.append(h)
            if h["expression"]:
                expressions.append(h["expression"])
        else:
            hypotheses.append({"idea": str(s), "mechanism": "", "sign": "", "horizon": "", "confidence": 0, "expression": ""})
    return hypotheses, expressions


def suggest_goal(*, region, delay, instrument, dataset_names, fields, categories, paper_text) -> str:
    """Auto-generate a concise research goal from the DATA (datasets/fields/categories) or, if a
    paper was uploaded, from the PAPER. One or two sentences the user can edit."""
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    cats = sorted({str(v) for v in (categories or {}).values() if v})
    fld = ", ".join(f.get("id", "") for f in (fields or []))
    if (paper_text or "").strip():
        src = ("A complete research paper was provided. Read all supplied pages before identifying its "
               "core tradeable idea; do not infer relevance from the opening pages alone.\n"
               "=== COMPLETE PAPER ===\n" + paper_text.strip() + "\n")
    else:
        src = ("Base the goal on the selected data below.\n"
               f"Datasets: {', '.join(dataset_names) or 'unspecified'}. Categories: {', '.join(cats) or 'unspecified'}.\n"
               f"Datafields: {fld or 'unspecified'}.\n")
    meta = (
        "You set the research goal for a WorldQuant BRAIN alpha-research session in "
        f"{region} ({instrument}, delay {delay}). " + src +
        "Write ONE clear, specific, mechanism-oriented research goal (1-2 sentences) that would guide generating "
        "diverse alphas from this data across ALL the datasets/categories provided. No preamble.\n"
        "Return ONLY a JSON array containing EXACTLY ONE string: the goal."
    )
    from app.core import llm_cache
    res = llm_cache.cached_generate_list(chain, meta, n=1)
    return (res.expressions[0].strip() if getattr(res, "expressions", None) else "")


def run_research(*, category, region, delay, instrument, dataset_names, fields, categories, goal,
                 paper_text, paper_is_community, mode, max_operators, n, progress,
                 economic_review=True, general=False) -> dict:
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings "
                           "(Claude, Gemini, OpenAI, DeepSeek, Groq, or Hugging Face).")
    progress(message=f"researching with {', '.join(p.name for p in chain)}…")
    meta = _prompt(category, region, delay, instrument, dataset_names, fields, categories, goal,
                   paper_text, paper_is_community, mode, max_operators, n, general)
    res = L.MultiLLM(chain).generate_list(meta, n=n)
    hypotheses, _ = _parse(res.expressions)

    expressions = []
    progress(message=f"{len(hypotheses)} testable hypotheses via {res.provider}")
    return {"hypotheses": hypotheses, "expressions": expressions,
            "provider": res.provider, "model": res.model}


def _hypothesis_fingerprint(h: dict) -> str:
    keys = ("idea", "mechanism", "sign", "horizon")
    raw = "|".join(_normalize(str(h.get(k, ""))) for k in keys)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def save_hypothesis(hypothesis: dict, context: dict | None = None) -> dict:
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    fp = _hypothesis_fingerprint(hypothesis)
    with SessionLocal() as db:
        row = db.scalar(select(M.SavedHypothesis).where(M.SavedHypothesis.fingerprint == fp))
        if row:
            return {"id": row.id, "duplicate": True}
        row = M.SavedHypothesis(fingerprint=fp, title=str(hypothesis.get("idea", ""))[:500],
                                payload_json=json.dumps(hypothesis), context_json=json.dumps(context or {}))
        db.add(row); db.commit(); db.refresh(row)
        return {"id": row.id, "duplicate": False}


def list_hypotheses(limit=100) -> list:
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    with SessionLocal() as db:
        rows = db.scalars(select(M.SavedHypothesis).order_by(M.SavedHypothesis.updated_at.desc()).limit(limit)).all()
        return [{"id": r.id, "created_at": r.created_at, "use_count": r.use_count,
                 "hypothesis": json.loads(r.payload_json or "{}"),
                 "context": json.loads(r.context_json or "{}")} for r in rows]


def delete_hypothesis(hid: int) -> bool:
    from app.db.base import SessionLocal
    from app.db import models as M
    with SessionLocal() as db:
        row = db.get(M.SavedHypothesis, hid)
        if not row: return False
        db.delete(row); db.commit(); return True


def fetch_alpha(alpha_id: str) -> dict:
    s = engine.require_session()
    data = engine.brain_call(f"alpha {alpha_id}", engine.ace.get_simulation_result_json, s, alpha_id)
    def expression(v):
        if isinstance(v, str) and "(" in v: return v
        if isinstance(v, dict):
            for k in ("code", "expression", "regular", "formula"):
                e = expression(v.get(k))
                if e: return e
        return ""
    return {"alpha_id": alpha_id, "expression": expression(data), "raw": data}


def replicate_alpha(*, alpha_id, fields, categories, region, delay, instrument, goal, n, progress) -> dict:
    source = fetch_alpha(alpha_id)
    if not source["expression"]:
        raise RuntimeError("BRAIN returned the alpha, but its FastExpr code was not present.")
    chain = L.default_chain(keymgr.get_preferred())
    if not chain: raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    prompt = (
        "Reverse-engineer the economic thesis of the source WorldQuant BRAIN alpha below. Separate the durable "
        "mechanism from its exact fields, windows and operators. Then propose portable, falsifiable hypotheses "
        "that can be tested with the TARGET fields. Reject blind field substitution, timing leakage, redundant "
        "inputs, unjustified sign changes and complexity added only to fit history. Use the full field descriptions "
        "to confirm economic meaning. Do not output expressions; the validated compiler will do that later.\n"
        f"Market: {region}, delay {delay}, {instrument}. Goal: {goal or 'faithful mechanism transfer'}.\n"
        f"SOURCE ALPHA: {source['expression']}\nTARGET FIELDS:\n{_field_summary(fields, categories)}\n"
        f"Return ONLY a JSON array of {n} strings in this form: idea {DELIM} mechanism {DELIM} expected sign "
        f"{DELIM} horizon in days {DELIM} confidence 1-5 {DELIM} falsification test {DELIM} data requirements")
    progress(message="extracting the source mechanism and testing transferability…")
    res = L.MultiLLM(chain).generate_list(prompt, n=n)
    hypotheses, _ = _parse(res.expressions)
    return {"source": source, "hypotheses": hypotheses, "provider": res.provider, "model": res.model}


# ── persistence ──────────────────────────────────────────────────────────────────────

def save_session(payload: dict) -> int:
    from app.db.base import SessionLocal
    from app.db import models as M
    with SessionLocal() as db:
        row = M.ResearchSession(
            category=payload.get("category", ""), region=payload.get("region", ""),
            delay=int(payload.get("delay", 1)), goal=payload.get("goal", ""),
            datasets_json=json.dumps(payload.get("dataset_names", [])),
            fields_json=json.dumps([f.get("id") for f in payload.get("fields", [])]),
            provider=payload.get("provider", ""), model=payload.get("model", ""),
            paper_name=payload.get("paper_name", ""),
            hypotheses_json=json.dumps(payload.get("hypotheses", [])),
            expressions_json=json.dumps(payload.get("expressions", [])),
            templates_json=json.dumps(payload.get("templates", [])))
        db.add(row)
        db.commit()
        return row.id


def list_sessions(limit: int = 30) -> list:
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    with SessionLocal() as db:
        rows = db.scalars(select(M.ResearchSession).order_by(M.ResearchSession.created_at.desc()).limit(limit)).all()
        out = []
        for r in rows:
            out.append({"id": r.id, "created_at": r.created_at, "category": r.category,
                        "region": r.region, "delay": r.delay, "goal": r.goal,
                        "provider": r.provider, "paper_name": r.paper_name,
                        "hypotheses": json.loads(r.hypotheses_json or "[]"),
                        "expressions": json.loads(r.expressions_json or "[]")})
        return out


import re as _re


def _normalize(text: str) -> str:
    return _re.sub(r"\s+", " ", (text or "").strip().lower())


def _fallback_name(category: str, region: str, source: str) -> str:
    base = (category or "Research").replace(",", " /").strip().title()
    tag = "Strategy" if source == "strategy" else "Research"
    return f"{base} {tag} · {region}"


def _fallback_compose(raw: str, category: str, region: str, dataset_names, source: str) -> dict:
    ds = ", ".join(dataset_names or []) or "the selected datasets"
    body = (
        "## Role\n"
        "You are a senior quantitative researcher and WorldQuant BRAIN alpha engineer, working with a "
        "risk-aware portfolio manager's mindset.\n\n"
        "## Context\n"
        f"Region {region}. Category: {category or 'mixed'}. Datasets: {ds}.\n\n"
        "## Objective\n"
        "Generate high-quality, diverse alpha expressions that TEST the researched ideas below and add "
        "genuine, low-correlation value.\n\n"
        "## Instructions\n" + (raw or "").strip() + "\n\n"
        "## Constraints\n"
        "Use ONLY the provided datafields; wrap VECTOR fields in a vec_* operator before use; pass named "
        "parameters as keywords; every expression must express a DIFFERENT mechanism; treat any example "
        "expression as an illustration, not a field restriction."
    )
    return {"name": _fallback_name(category, region, source), "body": body}


def nice_name(raw, category, region, source="research") -> str:
    """Just a concise, descriptive name for a saved prompt (no body rewrite). LLM name-only with a
    deterministic fallback."""
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        return _fallback_name(category, region, source)
    meta = ("Return ONLY a JSON array containing exactly ONE string: a concise, descriptive 4-8 word name "
            "(human readable, NOT just the category) for a prompt that generates WorldQuant BRAIN alpha "
            "expressions from the notes below.\n"
            f"Region {region}. Category: {category or 'mixed'}.\n=== NOTES ===\n{(raw or '')[:1500]}\n")
    try:
        from app.core import llm_cache
        res = llm_cache.cached_generate_list(chain, meta, n=1)
        items = [str(x).strip().strip('"') for x in res.expressions if str(x).strip()]
        return items[0][:80] if items else _fallback_name(category, region, source)
    except Exception:
        return _fallback_name(category, region, source)


def compose_prompt(*, raw, category, region, dataset_names, source="research") -> dict:
    """Wrap raw research/strategy notes in a STRUCTURED prompt (Role → Context → Objective →
    Instructions → Constraints) with a concise, descriptive name. The LLM only writes the framing
    sections and the name; the ORIGINAL notes are embedded VERBATIM as the Instructions section,
    so every hypothesis/strategy survives intact. Falls back fully if no LLM is available."""
    raw = (raw or "").strip()
    ds_line = ", ".join(dataset_names or []) or "the selected datasets"
    instructions = (
        "## Instructions\n"
        "Generate diverse alpha expressions that TEST each of the ideas below. Treat every idea on its own; "
        "spread across the ideas and the available datafields. Any example expression is an ILLUSTRATION of the "
        "mechanism, NOT a field restriction.\n\n" + raw
    )
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        return _fallback_compose(raw, category, region, dataset_names, source)
    meta = (
        "You are packaging research notes into a high-quality prompt for generating WorldQuant BRAIN alphas. Do "
        "NOT rewrite or summarise the notes themselves — only write the framing.\n"
        "Return ONLY a JSON array of strings:\n"
        "- FIRST string: 'NAME||| <a concise, descriptive 4-8 word name, human readable, NOT just the category>'.\n"
        "- Then ONE string per section, each starting with a '## ' header, in this order: '## Role' (assign one or "
        "MORE expert roles), '## Context' (region, category, datasets), '## Objective', '## Constraints' (use only "
        "provided datafields, wrap VECTOR fields in a vec_* op, pass named params as keywords, every expression a "
        "DIFFERENT mechanism, examples are illustrative). Do NOT include an Instructions section — it is added "
        "separately.\n"
        f"Region: {region}. Category: {category or 'mixed'}. Datasets: {ds_line}.\n"
        f"For reference only (do not copy): the notes have {raw.count(chr(10)) + 1} lines of ideas.\n")
    try:
        res = L.MultiLLM(chain).generate_list(meta, n=6)
        items = [str(x).strip() for x in res.expressions if str(x).strip()]
        name, role, context, objective, constraints = "", "", "", "", ""
        for it in items:
            up = it.upper()
            if up.startswith("NAME|||") or up.startswith("NAME:") or up.startswith("NAME "):
                name = _re.split(r"\|\|\||:", it, maxsplit=1)[-1].strip().strip('"')[:80]
            elif up.startswith("## ROLE"):
                role = it
            elif up.startswith("## CONTEXT"):
                context = it
            elif up.startswith("## OBJECTIVE"):
                objective = it
            elif up.startswith("## CONSTRAINT"):
                constraints = it
        if not (role or context or objective):
            return _fallback_compose(raw, category, region, dataset_names, source)
        role = role or "## Role\nYou are a senior quantitative researcher and WorldQuant BRAIN alpha engineer."
        context = context or f"## Context\nRegion {region}. Category: {category or 'mixed'}. Datasets: {ds_line}."
        objective = objective or "## Objective\nGenerate high-quality, diverse alphas that add low-correlation value."
        constraints = constraints or (
            "## Constraints\nUse ONLY the provided datafields; wrap VECTOR fields in a vec_* operator; pass named "
            "parameters as keywords; every expression must express a DIFFERENT mechanism.")
        body = "\n\n".join([role, context, objective, instructions, constraints])
        if not name:
            name = _fallback_name(category, region, source)
        return {"name": name, "body": body, "provider": res.provider}
    except Exception:
        return _fallback_compose(raw, category, region, dataset_names, source)


def save_prompt(*, name, scope, category, region, body, dataset_names, research_id=0) -> dict:
    """Save a prompt, avoiding duplicates: if an existing prompt in the same scope has the same
    (normalised) body, return it instead of creating another."""
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    norm = _normalize(body)
    with SessionLocal() as db:
        for e in db.scalars(select(M.Prompt).where(M.Prompt.scope == scope)).all():
            if _normalize(e.body) == norm:
                return {"id": e.id, "name": e.name, "duplicate": True}
        row = M.Prompt(name=name, scope=scope, category=category, region=region, body=body,
                       datasets_json=json.dumps(dataset_names or []), source_research_id=research_id)
        db.add(row)
        db.flush()
        db.add(M.PromptRevision(prompt_id=row.id, revision=1, body=body,
            protocol_json=json.dumps({"scope": scope, "category": category, "region": region,
                                      "datasets": dataset_names or []})))
        db.commit()
        return {"id": row.id, "name": row.name, "duplicate": False}


def export_all() -> dict:
    """Everything portable: saved prompts (generate + template scope) and research sessions."""
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    with SessionLocal() as db:
        prompts = [{"name": p.name, "scope": p.scope, "category": p.category, "region": p.region,
                    "body": p.body, "datasets": json.loads(p.datasets_json or "[]")}
                   for p in db.scalars(select(M.Prompt)).all()]
        sessions = [{"category": s.category, "region": s.region, "delay": s.delay, "goal": s.goal,
                     "dataset_names": json.loads(s.datasets_json or "[]"), "provider": s.provider,
                     "paper_name": s.paper_name, "hypotheses": json.loads(s.hypotheses_json or "[]"),
                     "expressions": json.loads(s.expressions_json or "[]")}
                    for s in db.scalars(select(M.ResearchSession)).all()]
    return {"format": "ace-studio/v2", "exported_at": time.time(), "prompts": prompts, "research_sessions": sessions}


def import_all(payload: dict) -> dict:
    """Merge an exported bundle back in, de-duplicating prompts by body."""
    added_p = 0
    for p in payload.get("prompts", []):
        r = save_prompt(name=p.get("name", ""), scope=p.get("scope", "generate"),
                        category=p.get("category", ""), region=p.get("region", ""),
                        body=p.get("body", ""), dataset_names=p.get("datasets", []))
        if not r.get("duplicate"):
            added_p += 1
    added_s = 0
    for s in payload.get("research_sessions", []):
        try:
            save_session(s); added_s += 1
        except Exception:
            pass
    return {"prompts_added": added_p, "sessions_added": added_s}


def extract_pdf_text(data: bytes, pages: str = "") -> str:
    """Extract text from a PDF. `pages` is a 1-based spec like '1-3,5' (empty = whole doc)."""
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    want = _page_set(pages, total)
    chunks = []
    for i in range(total):
        if want is None or (i + 1) in want:
            try:
                chunks.append(f"\n=== PAGE {i + 1} OF {total} ===\n{reader.pages[i].extract_text() or ''}")
            except Exception:
                pass
    return "\n".join(chunks).strip()


def _page_set(spec: str, total: int):
    spec = (spec or "").strip()
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            out.add(int(part))
    return {p for p in out if 1 <= p <= total} or None
