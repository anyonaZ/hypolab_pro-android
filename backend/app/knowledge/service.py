"""Knowledge service — the studio's memory and cross-region intelligence.

Every dataset/field fetch is ingested here, so the DB steadily learns the whole data
landscape. Similarity is computed WITHOUT any heavy embedding model (fully offline):
a lightweight token cosine over name+description, plus the strong exact-id-across-regions
signal that BRAIN's shared dataset/field ids give us. That is enough to answer "which
datasets/fields are the same concept in other regions" — the core of cross-region
research — with zero extra dependencies.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter

from sqlalchemy import func, select

from app.db.base import SessionLocal
from app.db import models as M

DELIM = "|||"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "by", "with",
         "data", "dataset", "value", "score", "field", "based", "is", "are", "this"}


def _tokens(*parts: str) -> Counter:
    text = " ".join(p or "" for p in parts).lower()
    return Counter(t for t in _TOKEN.findall(text) if t not in _STOP and len(t) > 1)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


# ── ingestion ────────────────────────────────────────────────────────────────────────

def ingest_datasets(region: str, delay: int, instrument: str, universe: str, rows: list) -> None:
    if not rows:
        return
    now = time.time()
    with SessionLocal() as db:
        for r in rows:
            did = str(r.get("id") or "").strip()
            if not did:
                continue
            existing = db.scalar(select(M.Dataset).where(
                M.Dataset.dataset_id == did, M.Dataset.region == region, M.Dataset.delay == delay))
            if existing:
                existing.fetch_count += 1
                existing.last_seen = now
                existing.category = r.get("category_id") or existing.category
                existing.value_score = float(r.get("valueScore") or existing.value_score or 0)
                existing.alpha_count = int(r.get("alphaCount") or existing.alpha_count or 0)
            else:
                db.add(M.Dataset(
                    dataset_id=did, region=region, delay=delay, instrument=instrument,
                    universe=universe, category=str(r.get("category_id") or ""),
                    name=str(r.get("name") or ""), description=str(r.get("description") or ""),
                    coverage=float(r.get("coverage") or 0), value_score=float(r.get("valueScore") or 0),
                    alpha_count=int(r.get("alphaCount") or 0)))
        db.commit()


def ingest_fields(region: str, delay: int, dataset_ids: list, rows: list) -> None:
    if not rows:
        return
    now = time.time()
    with SessionLocal() as db:
        for r in rows:
            fid = str(r.get("id") or "").strip()
            if not fid:
                continue
            existing = db.scalar(select(M.Field).where(
                M.Field.field_id == fid, M.Field.region == region, M.Field.delay == delay))
            if existing:
                existing.last_seen = now
            else:
                _pfx = r.get("prefix") or (fid.split("_", 1)[0] if "_" in fid else "general")
                db.add(M.Field(
                    field_id=fid, dataset_id=str(r.get("dataset_id") or (dataset_ids[0] if dataset_ids else "")),
                    region=region, delay=delay, type=str(r.get("type") or "MATRIX").upper(),
                    prefix=str(_pfx).lower(),
                    description=str(r.get("description") or ""),
                    alpha_count=int(r.get("alphaCount") or 0), is_virgin=bool(r.get("is_virgin"))))
        db.commit()


# ── queries ──────────────────────────────────────────────────────────────────────────

def overview() -> dict:
    with SessionLocal() as db:
        n_ds = db.scalar(select(func.count()).select_from(M.Dataset)) or 0
        n_fields = db.scalar(select(func.count()).select_from(M.Field)) or 0
        regions = [r for (r,) in db.execute(select(M.Dataset.region).distinct())]
        # datasets seen in more than one region (by shared id) = cross-region concepts
        by_id = db.execute(
            select(M.Dataset.dataset_id, func.count(func.distinct(M.Dataset.region)).label("nreg"))
            .group_by(M.Dataset.dataset_id)).all()
        cross = [d for d, nreg in by_id if nreg > 1]
        cats = db.execute(
            select(M.Dataset.category, func.count()).where(M.Dataset.category != "")
            .group_by(M.Dataset.category)).all()
    return {
        "datasets": n_ds, "fields": n_fields, "regions": sorted(regions),
        "cross_region_concepts": len(cross),
        "categories": sorted([{"category": c, "count": n} for c, n in cats],
                             key=lambda x: -x["count"]),
    }


def judge_relationships(base_id: str, region: str, delay: int, candidates: list, progress=None) -> dict:
    """AI check over lexical candidates: the cosine step finds datasets whose NAMES/DESCRIPTIONS
    overlap, which can surface coincidental word matches. This asks an LLM whether each candidate
    is GENUINELY the same or a closely related concept for quant research, with a one-line reason —
    so nonsensical links are dropped rather than shown just because they share words."""
    from app.brain import engine  # noqa: F401 — ensures the vendored LLM stack is importable
    import llm_providers as L
    import keys as keymgr

    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == base_id))
    base_name = base.name if base else base_id
    base_desc = (base.description if base else "") or ""

    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings to verify relationships.")
    if not candidates:
        return {"judged": []}
    lst = "\n".join(f"{i + 1}. {c.get('name') or c.get('dataset_id')} "
                    f"[{c.get('region', region)}] — {(c.get('description') or c.get('category') or '')[:90]}"
                    for i, c in enumerate(candidates))
    meta = (
        "You are validating candidate relationships between WorldQuant BRAIN datasets. A lexical filter "
        "already proposed these because their names/descriptions overlap, but some overlaps are "
        "coincidental. For EACH candidate decide whether it genuinely represents the SAME concept or a "
        "closely related one that a quant could reasonably substitute or pair, versus an unrelated dataset "
        "that merely shares words.\n"
        f"BASE DATASET: {base_name} — {base_desc[:120]}\n"
        f"CANDIDATES:\n{lst}\n"
        f"Return ONLY a JSON array of {len(candidates)} strings, each: index ||| yes|no ||| short reason\n")
    if progress:
        progress(message=f"verifying {len(candidates)} relationships with AI…")
    from app.core import llm_cache
    res = llm_cache.cached_generate_list(chain, meta, n=len(candidates))
    verdicts = {}
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split("|||")]
        if len(parts) >= 2:
            try:
                idx = int("".join(ch for ch in parts[0] if ch.isdigit())) - 1
            except ValueError:
                continue
            verdicts[idx] = {"related": parts[1].lower().startswith("y"),
                             "reason": parts[2] if len(parts) > 2 else ""}
    judged = []
    for i, c in enumerate(candidates):
        v = verdicts.get(i, {"related": True, "reason": ""})
        judged.append({**c, "ai_related": v["related"], "ai_reason": v["reason"]})
    return {"judged": judged, "provider": res.provider}


def cross_region_concepts(min_regions: int = 2, limit: int = 60) -> list:
    """Datasets whose SAME id is catalogued in >= min_regions regions — the reliable cross-region
    concepts (exact id match, no lexical guessing)."""
    with SessionLocal() as db:
        rows = db.scalars(select(M.Dataset)).all()
    by_id: dict = {}
    for r in rows:
        e = by_id.setdefault(r.dataset_id, {"dataset_id": r.dataset_id, "name": r.name,
                                            "category": r.category, "regions": set()})
        e["regions"].add(r.region)
    out = [{**v, "regions": sorted(v["regions"])} for v in by_id.values() if len(v["regions"]) >= min_regions]
    out.sort(key=lambda x: -len(x["regions"]))
    return out[:limit]


def analyze_cross_region(min_regions: int = 2, progress=None) -> dict:
    """AI pass over the cross-region concepts: rate how worthwhile each is to research across its
    regions (to diversify / test faster) and why, based on its CATEGORY — not word overlap."""
    concepts = cross_region_concepts(min_regions)
    n_regions = 0
    with SessionLocal() as db:
        n_regions = db.scalar(select(func.count(func.distinct(M.Dataset.region)))) or 0
    if n_regions < min_regions:
        return {"enough_regions": False, "regions_known": n_regions, "concepts": []}
    if not concepts:
        return {"enough_regions": True, "regions_known": n_regions, "concepts": [], "note": "No shared cross-region concepts yet."}
    from app.brain import engine  # noqa: F401
    import llm_providers as L
    import keys as keymgr
    chain = L.default_chain(keymgr.get_preferred())
    if not chain:
        return {"enough_regions": True, "regions_known": n_regions, "concepts": concepts,
                "note": "Add an LLM key in Settings for the AI rating."}
    take = concepts[:40]
    lst = "\n".join(f"{i + 1}. {c['name'] or c['dataset_id']} [{c['category'] or 'uncategorised'}] "
                    f"in regions {', '.join(c['regions'])}" for i, c in enumerate(take))
    meta = (
        "You advise which CROSS-REGION data concepts are most worth exploiting on WorldQuant BRAIN. Each item "
        "below is the SAME dataset available in several regions. Judge — from its data CATEGORY and how that "
        "kind of signal typically travels across markets — how promising it is to research the same idea across "
        "those regions to diversify and test faster. Do NOT rate on the name wording; rate on the mechanism.\n"
        + lst +
        f"\nReturn ONLY a JSON array of {len(take)} strings, each: index {DELIM} high|medium|low {DELIM} one-line reason\n")
    if progress:
        progress(message=f"rating {len(take)} cross-region concepts…")
    from app.core import llm_cache
    res = llm_cache.cached_generate_list(chain, meta, n=len(take))
    ranked = {}
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split(DELIM)]
        if len(parts) >= 2:
            try:
                idx = int("".join(ch for ch in parts[0] if ch.isdigit())) - 1
            except ValueError:
                continue
            ranked[idx] = {"rating": parts[1].lower(), "reason": parts[2] if len(parts) > 2 else ""}
    out = []
    for i, c in enumerate(take):
        out.append({**c, **ranked.get(i, {"rating": "", "reason": ""})})
    order = {"high": 0, "medium": 1, "low": 2, "": 3}
    out.sort(key=lambda x: order.get(x.get("rating", ""), 3))
    return {"enough_regions": True, "regions_known": n_regions, "concepts": out, "provider": res.provider}


def datasets_in_category(category: str, region: str = "", limit: int = 300) -> list:
    """Every catalogued dataset in a category (deduped by id) — so Strategy Atlas can explore
    the WHOLE category rather than a single dataset."""
    with SessionLocal() as db:
        q = select(M.Dataset).where(M.Dataset.category == category)
        if region:
            q = q.where(M.Dataset.region == region)
        rows = db.scalars(q.limit(limit * 2)).all()
    seen, out = set(), []
    for r in rows:
        if r.dataset_id in seen:
            continue
        seen.add(r.dataset_id)
        out.append({"id": r.dataset_id, "name": r.name, "description": r.description})
        if len(out) >= limit:
            break
    return out


def dataset_twins(dataset_id: str, region: str, delay: int) -> dict:
    """A TWIN is the SAME dataset (identical BRAIN dataset id) catalogued in ANOTHER region — the
    only real, non-guessed cross-region equivalence BRAIN gives us. No lexical/word matching: an
    entry appears only when the exact same id exists in another region, so every twin is genuine."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(
            M.Dataset.dataset_id == dataset_id, M.Dataset.region == region)) or \
            db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == dataset_id))
        n_regions = db.scalar(select(func.count(func.distinct(M.Dataset.region)))) or 0
        if base is None:
            return {"dataset_id": dataset_id, "region": region, "regions_known": n_regions,
                    "name": dataset_id, "category": "", "twins": []}
        others = db.scalars(select(M.Dataset).where(
            M.Dataset.region != region, M.Dataset.dataset_id == dataset_id)).all()
        seen, twins = set(), []
        for o in others:
            if o.region in seen:
                continue
            seen.add(o.region)
            twins.append({"dataset_id": o.dataset_id, "region": o.region, "delay": o.delay,
                          "name": o.name, "category": o.category, "exact": True, "score": 1.0})
        twins.sort(key=lambda t: t["region"])
        return {"dataset_id": dataset_id, "region": region, "regions_known": n_regions,
                "name": base.name, "category": base.category, "twins": twins}


def field_equivalents(field_id: str, region: str, delay: int) -> dict:
    """Fields that represent the same signal in other regions — exact id first, then
    same-prefix/type/description-similar fields."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Field).where(
            M.Field.field_id == field_id, M.Field.region == region)) or \
               db.scalar(select(M.Field).where(M.Field.field_id == field_id))
        if base is None:
            return {"field_id": field_id, "equivalents": []}
        base_tok = _tokens(base.description)
        others = db.scalars(select(M.Field).where(M.Field.region != region)).all()
        out = []
        for o in others:
            exact = o.field_id == field_id
            score = 1.0 if exact else (
                _cosine(base_tok, _tokens(o.description)) if o.type == base.type and o.prefix == base.prefix else 0.0)
            if exact or score >= 0.5:
                out.append({"field_id": o.field_id, "region": o.region, "type": o.type,
                            "exact": exact, "score": round(score, 3)})
        out.sort(key=lambda t: (-t["exact"], -t["score"]))
        return {"field_id": field_id, "region": region, "type": base.type, "equivalents": out[:30]}


def similar_datasets(dataset_id: str, region: str, delay: int, top: int = 12) -> list:
    """Datasets in the SAME region and SAME data CATEGORY — no word/lexical matching. Category is
    the reliable relatedness signal (an Analyst set relates to other Analyst sets); WHY they pair
    and how to use them together is left to the AI verification step, not a hand-rolled algorithm."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(
            M.Dataset.dataset_id == dataset_id, M.Dataset.region == region)) or \
            db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == dataset_id))
        if base is None:
            return []
        cat = (base.category or "").strip()
        if not cat:
            return []   # no category on this dataset -> nothing to match by (we never guess by words)
        rows = db.scalars(select(M.Dataset).where(
            M.Dataset.region == region,
            func.lower(M.Dataset.category) == cat.lower(),
            M.Dataset.dataset_id != dataset_id)).all()
        seen, out = set(), []
        for r in rows:
            if r.dataset_id in seen:
                continue
            seen.add(r.dataset_id)
            out.append({"dataset_id": r.dataset_id, "name": r.name, "category": r.category})
        return out[:top]
