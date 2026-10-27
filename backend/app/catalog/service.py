from __future__ import annotations

import json
import time
from collections import Counter

from sqlalchemy import and_, delete, desc, func, or_, select

from app.brain import engine
from app.db import models as M
from app.db.base import SessionLocal


def _ctx(instrument, region, delay, universe):
    return f"{instrument}|{region}|{int(delay)}|{universe}"


def _parse_ctx(key):
    instrument, region, delay, universe = key.split("|", 3)
    return instrument, region, int(delay), universe


def cached_account_options() -> list[dict]:
    """Last successfully observed BRAIN option matrix, usable during transient API failures."""
    with SessionLocal() as db:
        row = db.get(M.AccountOptionCache, "active")
        try:
            return json.loads(row.payload_json or "[]") if row else []
        except Exception:
            return []


def account_options(refresh: bool = False) -> list[dict]:
    """Prefer a fresh/cached engine result and fall back to the durable database snapshot."""
    try:
        rows = engine.get_options(refresh=refresh).get("records", [])
        if rows:
            with SessionLocal() as db:
                row = db.get(M.AccountOptionCache, "active")
                payload = json.dumps(rows)
                if row:
                    row.payload_json = payload; row.updated_at = time.time()
                else:
                    db.add(M.AccountOptionCache(key="active", payload_json=payload, updated_at=time.time()))
                db.commit()
            return rows
    except Exception:
        pass
    return cached_account_options()


def _num(v, default=0.0):
    try: return float(v if v is not None else default)
    except (TypeError, ValueError): return default


def _full_dataset_sync_at(db, context_key: str):
    """Newest complete, unfiltered catalog scan for a context (partial Explorer caches do not count)."""
    rows = db.execute(select(M.CatalogSyncTask.updated_at, M.CatalogSyncRun.scope_json).join(
        M.CatalogSyncRun, M.CatalogSyncRun.id == M.CatalogSyncTask.run_id).where(
        M.CatalogSyncTask.kind == "datasets", M.CatalogSyncTask.context_key == context_key,
        M.CatalogSyncTask.status.in_(["complete", "empty"])).order_by(desc(M.CatalogSyncTask.updated_at))).all()
    for updated_at, raw_scope in rows:
        try: scope = json.loads(raw_scope or "{}")
        except Exception: continue
        if not scope.get("categories") and not _num(scope.get("coverage_min")) and not _num(scope.get("value_min")):
            return updated_at
    return None


def ingest_datasets(instrument, region, delay, universe, rows):
    now = time.time()
    rows = [r for r in (rows or []) if str(r.get("id") or "").strip()]
    ids = {str(r.get("id") or "").strip() for r in rows}
    with SessionLocal() as db:
        concepts = {x.dataset_id: x for x in db.scalars(
            select(M.DatasetConcept).where(M.DatasetConcept.dataset_id.in_(ids))).all()} if ids else {}
        availability = {x.dataset_id: x for x in db.scalars(select(M.DatasetAvailability).where(
            M.DatasetAvailability.dataset_id.in_(ids), M.DatasetAvailability.instrument == instrument,
            M.DatasetAvailability.region == region, M.DatasetAvailability.delay == int(delay),
            M.DatasetAvailability.universe == universe)).all()} if ids else {}
        for r in rows:
            did = str(r.get("id") or "").strip()
            c = concepts.get(did)
            if not c:
                c = M.DatasetConcept(dataset_id=did); db.add(c); concepts[did] = c
            c.name = str(r.get("name") or c.name or "")
            c.description = str(r.get("description") or c.description or "")
            c.category = str(r.get("category_id") or r.get("category_name") or c.category or "")
            c.subcategory = str(r.get("subcategory_id") or r.get("subcategory_name") or c.subcategory or "")
            c.last_seen = now
            a = availability.get(did)
            if not a:
                a = M.DatasetAvailability(dataset_id=did, instrument=instrument, region=region,
                    delay=int(delay), universe=universe); db.add(a); availability[did] = a
            a.coverage = _num(r.get("coverage")); a.value_score = _num(r.get("valueScore"))
            a.alpha_count = int(_num(r.get("alphaCount"))); a.available = True; a.last_verified = now
        db.commit()


def ingest_fields(instrument, region, delay, universe, dataset_id, rows):
    from app.generation.service import _field_nature
    now = time.time()
    rows = [r for r in (rows or []) if str(r.get("id") or "").strip()]
    ids = {str(r.get("id") or "").strip() for r in rows}
    with SessionLocal() as db:
        concepts = {x.field_id: x for x in db.scalars(
            select(M.FieldConcept).where(M.FieldConcept.field_id.in_(ids))).all()} if ids else {}
        availability = {x.field_id: x for x in db.scalars(select(M.FieldAvailability).where(
            M.FieldAvailability.field_id.in_(ids), M.FieldAvailability.instrument == instrument,
            M.FieldAvailability.region == region, M.FieldAvailability.delay == int(delay),
            M.FieldAvailability.universe == universe)).all()} if ids else {}
        for r in rows:
            fid = str(r.get("id") or "").strip()
            c = concepts.get(fid)
            payload = {"id": fid, "description": r.get("description", ""), "type": r.get("type", "MATRIX")}
            if not c:
                c = M.FieldConcept(field_id=fid, dataset_id=str(r.get("dataset_id") or dataset_id),
                    prefix=fid.split("_", 1)[0].lower() if "_" in fid else "general"); db.add(c); concepts[fid] = c
            c.dataset_id = str(r.get("dataset_id") or dataset_id or c.dataset_id)
            c.description = str(r.get("description") or c.description or "")
            c.type = str(r.get("type") or c.type or "MATRIX").upper(); c.nature = _field_nature(payload); c.last_seen = now
            a = availability.get(fid)
            if not a:
                a = M.FieldAvailability(field_id=fid, instrument=instrument, region=region,
                    delay=int(delay), universe=universe); db.add(a); availability[fid] = a
            a.coverage = _num(r.get("coverage")); a.date_coverage = _num(r.get("dateCoverage"))
            a.alpha_count = int(_num(r.get("alphaCount")))
            a.is_virgin = bool(r.get("is_virgin")); a.available = True; a.last_verified = now
        db.commit()


def plan(scope: dict) -> dict:
    records = account_options()
    instruments = set(scope.get("instruments") or []); regions = set(scope.get("regions") or [])
    delays = {int(x) for x in (scope.get("delays") or [])}; universes = set(scope.get("universes") or [])
    contexts = []
    exact = {str(x) for x in scope.get("contexts") or []}
    for r in records:
        if instruments and r["instrument"] not in instruments: continue
        if regions and r["region"] not in regions: continue
        if delays and int(r["delay"]) not in delays: continue
        for u in r.get("universes", []):
            if universes and u not in universes: continue
            candidate = (r["instrument"], r["region"], int(r["delay"]), u)
            if exact and _ctx(*candidate) not in exact: continue
            contexts.append(candidate)
    contexts = list(dict.fromkeys(contexts))
    refresh_mode = scope.get("refresh_mode", "all")
    stale_before = time.time() - max(0, int(scope.get("stale_days", 30))) * 86400
    with SessionLocal() as db:
        # Zero means run until every task in the selected scope is complete. Older
        # clients may still send a ceiling; it is deliberately ignored.
        run = M.CatalogSyncRun(scope_json=json.dumps(scope), request_budget=0)
        db.add(run); db.flush()
        scheduled = 0
        for c in contexts:
            key = _ctx(*c); last = _full_dataset_sync_at(db, key)
            dataset_fresh = last is not None and (refresh_mode != "stale" or last >= stale_before)
            if refresh_mode in {"missing", "stale"} and dataset_fresh:
                if scope.get("include_fields", True):
                    instrument, region, delay, universe = c
                    ids = set(db.scalars(select(M.DatasetAvailability.dataset_id).where(
                        M.DatasetAvailability.instrument == instrument, M.DatasetAvailability.region == region,
                        M.DatasetAvailability.delay == delay, M.DatasetAvailability.universe == universe)).all())
                    done_rows = db.execute(select(M.CatalogSyncTask.target_id, func.max(M.CatalogSyncTask.updated_at)).where(
                        M.CatalogSyncTask.kind == "fields", M.CatalogSyncTask.context_key == key,
                        M.CatalogSyncTask.status.in_(["complete", "empty"])).group_by(M.CatalogSyncTask.target_id)).all()
                    done = {target for target, updated in done_rows if refresh_mode == "missing" or updated >= stale_before}
                    for did in sorted(ids - done):
                        db.add(M.CatalogSyncTask(run_id=run.id, kind="fields", context_key=key, target_id=did)); scheduled += 1
                continue
            db.add(M.CatalogSyncTask(run_id=run.id, kind="datasets", context_key=key)); scheduled += 1
        db.commit(); rid = run.id
    return {"run_id": rid, "contexts": len(contexts), "initial_requests": scheduled,
            "field_requests": "created after datasets are discovered", "budget": 0, "continuous": True}


def _field_rows(df, limit=100000):
    if df is None or getattr(df, "empty", True): return []
    cols = [c for c in ["id", "description", "type", "alphaCount", "dateCoverage", "coverage", "is_virgin", "dataset_id"] if c in df.columns]
    return engine._json_safe(df[cols].head(limit).to_dict(orient="records"))


def execute(run_id: int, progress, should_cancel) -> dict:
    with SessionLocal() as db:
        run = db.get(M.CatalogSyncRun, run_id)
        if not run: raise RuntimeError("No such catalog sync plan.")
        scope = json.loads(run.scope_json or "{}"); run.status = "running"; run.updated_at = time.time(); db.commit()
    include_fields = bool(scope.get("include_fields", True)); categories = {str(x).lower() for x in scope.get("categories", [])}
    coverage_min = _num(scope.get("coverage_min")); value_min = _num(scope.get("value_min"))
    while not should_cancel():
        with SessionLocal() as db:
            run = db.get(M.CatalogSyncRun, run_id)
            task = db.scalar(select(M.CatalogSyncTask).where(M.CatalogSyncTask.run_id == run_id,
                M.CatalogSyncTask.status.in_(["pending", "retry"])).order_by(M.CatalogSyncTask.id))
            if not task:
                failures = db.scalar(select(func.count()).select_from(M.CatalogSyncTask).where(
                    M.CatalogSyncTask.run_id == run_id, M.CatalogSyncTask.status == "error")) or 0
                run.status = "incomplete" if failures else "complete"; run.updated_at = time.time(); db.commit(); break
            task.status = "running"; task.attempts += 1; task.updated_at = time.time()
            run.requests_used += 1; used = run.requests_used; db.commit()
            total = db.scalar(select(func.count()).select_from(M.CatalogSyncTask).where(M.CatalogSyncTask.run_id == run_id)) or used
            tid, kind, key, target = task.id, task.kind, task.context_key, task.target_id
        instrument, region, delay, universe = _parse_ctx(key)
        progress(total=total, done=used - 1, message=f"{kind}: {region} D{delay} {universe}{' · ' + target if target else ''}")
        try:
            if kind == "datasets":
                rows = engine.get_datasets(region=region, universe=universe, delay=delay,
                    instrument_type=instrument, coverage_min=coverage_min, value_min=value_min)
                if categories:
                    rows = [r for r in rows if str(r.get("category_id") or r.get("category_name") or "").lower() in categories]
                ingest_datasets(instrument, region, delay, universe, rows)
                if include_fields:
                    with SessionLocal() as db:
                        for r in rows:
                            did = str(r.get("id") or "")
                            if scope.get("refresh_mode") in {"missing", "stale"}:
                                last = db.scalar(select(func.max(M.CatalogSyncTask.updated_at)).where(
                                    M.CatalogSyncTask.kind == "fields", M.CatalogSyncTask.context_key == key,
                                    M.CatalogSyncTask.target_id == did,
                                    M.CatalogSyncTask.status.in_(["complete", "empty"])))
                                stale_before = time.time() - max(0, int(scope.get("stale_days", 30))) * 86400
                                if scope.get("refresh_mode") == "missing" and last is not None: continue
                                if scope.get("refresh_mode") == "stale" and last is not None and last >= stale_before: continue
                            exists = db.scalar(select(M.CatalogSyncTask.id).where(M.CatalogSyncTask.run_id == run_id,
                                M.CatalogSyncTask.kind == "fields", M.CatalogSyncTask.context_key == key,
                                M.CatalogSyncTask.target_id == did))
                            if did and not exists: db.add(M.CatalogSyncTask(run_id=run_id, kind="fields", context_key=key, target_id=did))
                        db.commit()
                count = len(rows)
            else:
                df, _ = engine.fetch_fields([target], region=region, universe=universe, delay=delay,
                    instrument_type=instrument, data_type="ALL", search="")
                rows = _field_rows(df); ingest_fields(instrument, region, delay, universe, target, rows); count = len(rows)
            with SessionLocal() as db:
                task = db.get(M.CatalogSyncTask, tid); task.status = "complete" if count else "empty"
                task.records = count; task.error = ""; task.updated_at = time.time(); db.commit()
        except Exception as e:
            with SessionLocal() as db:
                task = db.get(M.CatalogSyncTask, tid); task.status = "retry" if task.attempts < 3 else "error"
                task.error = str(e).splitlines()[0][:500]; task.updated_at = time.time(); db.commit()
        progress(total=max(total, used), done=used)
    if should_cancel():
        with SessionLocal() as db:
            run = db.get(M.CatalogSyncRun, run_id); run.status = "paused"; run.updated_at = time.time()
            db.execute(__import__('sqlalchemy').update(M.CatalogSyncTask).where(M.CatalogSyncTask.run_id == run_id,
                M.CatalogSyncTask.status == "running").values(status="retry")); db.commit()
    return run_detail(run_id)


def run_detail(run_id):
    with SessionLocal() as db:
        run = db.get(M.CatalogSyncRun, run_id)
        if not run: return None
        counts = dict(db.execute(select(M.CatalogSyncTask.status, func.count()).where(
            M.CatalogSyncTask.run_id == run_id).group_by(M.CatalogSyncTask.status)).all())
        errors = [{"kind": x.kind, "context": x.context_key, "target": x.target_id, "error": x.error}
                  for x in db.scalars(select(M.CatalogSyncTask).where(M.CatalogSyncTask.run_id == run_id,
                    M.CatalogSyncTask.status == "error").limit(30)).all()]
        return {"id": run.id, "status": run.status, "created_at": run.created_at,
                "scope": json.loads(run.scope_json or "{}"), "budget": run.request_budget,
                "requests_used": run.requests_used, "tasks": counts, "errors": errors}


def list_runs(limit=30):
    with SessionLocal() as db:
        ids = db.scalars(select(M.CatalogSyncRun.id).order_by(desc(M.CatalogSyncRun.created_at)).limit(limit)).all()
    return [run_detail(x) for x in ids]


def resume(run_id: int, additional_requests: int = 0, retry_errors: bool = True):
    with SessionLocal() as db:
        run = db.get(M.CatalogSyncRun, run_id)
        if not run: return False
        run.request_budget = 0; run.status = "planned"; run.updated_at = time.time()
        if retry_errors:
            db.execute(__import__('sqlalchemy').update(M.CatalogSyncTask).where(
                M.CatalogSyncTask.run_id == run_id, M.CatalogSyncTask.status == "error").values(status="retry", error=""))
        db.commit(); return True


def health():
    with SessionLocal() as db:
        ds = db.scalar(select(func.count()).select_from(M.DatasetConcept)) or 0
        da = db.scalar(select(func.count()).select_from(M.DatasetAvailability)) or 0
        fs = db.scalar(select(func.count()).select_from(M.FieldConcept)) or 0
        fa = db.scalar(select(func.count()).select_from(M.FieldAvailability)) or 0
        regions = db.scalar(select(func.count(func.distinct(M.DatasetAvailability.region)))) or 0
        contexts = db.scalar(select(func.count()).select_from(select(M.DatasetAvailability.instrument,
            M.DatasetAvailability.region, M.DatasetAvailability.delay, M.DatasetAvailability.universe).distinct().subquery())) or 0
        cats = [{"category": c, "datasets": n} for c, n in db.execute(select(M.DatasetConcept.category, func.count())
            .where(M.DatasetConcept.category != "").group_by(M.DatasetConcept.category).order_by(desc(func.count()))).all()]
        d01 = db.scalar(select(func.count()).select_from(select(M.FieldAvailability.field_id, M.FieldAvailability.region,
            M.FieldAvailability.universe).where(M.FieldAvailability.delay.in_([0, 1])).group_by(M.FieldAvailability.field_id,
            M.FieldAvailability.region, M.FieldAvailability.universe).having(func.count(func.distinct(M.FieldAvailability.delay)) == 2).subquery())) or 0
        stale_before = time.time() - 30 * 86400
        stale_ds = db.scalar(select(func.count()).select_from(M.DatasetAvailability).where(M.DatasetAvailability.last_verified < stale_before)) or 0
        stale_fs = db.scalar(select(func.count()).select_from(M.FieldAvailability).where(M.FieldAvailability.last_verified < stale_before)) or 0
    return {"dataset_concepts": ds, "dataset_availability": da, "field_concepts": fs,
            "field_availability": fa, "regions": regions, "contexts": contexts, "d0_d1_fields": d01,
            "stale_datasets_30d": stale_ds, "stale_fields_30d": stale_fs, "categories": cats}


def context_status(instrument, region, delay, universe, dataset_ids=None) -> dict:
    """Authoritative completeness for one exact BRAIN scope.

    Availability rows are only a cache; they never prove that a complete crawl finished.
    Explorer uses this status as its gate so partial and failed downloads cannot look complete.
    """
    key = _ctx(instrument, region, delay, universe)
    wanted = {str(x) for x in (dataset_ids or []) if str(x)}
    with SessionLocal() as db:
        dataset_at = _full_dataset_sync_at(db, key)
        available_ids = set(db.scalars(select(M.DatasetAvailability.dataset_id).where(
            M.DatasetAvailability.instrument == instrument, M.DatasetAvailability.region == region,
            M.DatasetAvailability.delay == int(delay), M.DatasetAvailability.universe == universe,
            M.DatasetAvailability.available == True)).all())  # noqa: E712
        field_done = set(db.scalars(select(M.CatalogSyncTask.target_id).where(
            M.CatalogSyncTask.kind == "fields", M.CatalogSyncTask.context_key == key,
            M.CatalogSyncTask.status.in_(["complete", "empty"]))).all())
        latest = {}; task_rows = db.scalars(select(M.CatalogSyncTask).where(
            M.CatalogSyncTask.context_key == key).order_by(desc(M.CatalogSyncTask.updated_at))).all()
        for task in task_rows: latest.setdefault((task.kind, task.target_id), task)
        errors = [{"kind": x.kind, "target": x.target_id, "error": x.error}
                  for x in latest.values() if x.status == "error"][:20]
    required = wanted or available_ids
    missing_fields = sorted(required - field_done) if dataset_at is not None else sorted(required)
    return {"key": key, "instrument": instrument, "region": region, "delay": int(delay),
            "universe": universe, "datasets_ready": dataset_at is not None,
            "fields_ready": dataset_at is not None and not missing_fields,
            "dataset_count": len(available_ids), "field_dataset_count": len(field_done),
            "missing_field_datasets": missing_fields, "errors": errors,
            "last_complete": dataset_at or 0}


def scope_matrix() -> dict:
    """Every account-valid context, annotated with durable local completeness."""
    records = account_options()
    contexts = []
    for row in records:
        for universe in row.get("universes", []):
            contexts.append(context_status(row["instrument"], row["region"], int(row["delay"]), universe))
    by_region = {}
    for item in contexts:
        group = by_region.setdefault(item["region"], {"region": item["region"], "instrument": item["instrument"],
            "contexts": [], "ready": 0, "incomplete": 0, "failed": 0})
        group["contexts"].append(item)
        group["ready" if item["datasets_ready"] and item["fields_ready"] else "incomplete"] += 1
        if item["errors"]: group["failed"] += 1
    return {"regions": sorted(by_region.values(), key=lambda x: x["region"]), "contexts": contexts}


def local_datasets(instrument, region, delay, universe, category="", search=""):
    with SessionLocal() as db:
        q = select(M.DatasetConcept, M.DatasetAvailability).join(M.DatasetAvailability,
            M.DatasetAvailability.dataset_id == M.DatasetConcept.dataset_id).where(
            M.DatasetAvailability.instrument == instrument, M.DatasetAvailability.region == region,
            M.DatasetAvailability.delay == int(delay), M.DatasetAvailability.universe == universe)
        if category: q = q.where(M.DatasetConcept.category == category)
        if search:
            like = f"%{search}%"; q = q.where(or_(M.DatasetConcept.dataset_id.ilike(like), M.DatasetConcept.name.ilike(like), M.DatasetConcept.description.ilike(like)))
        rows = db.execute(q.order_by(desc(M.DatasetAvailability.value_score))).all()
    return [{"id": c.dataset_id, "name": c.name, "description": c.description, "category_id": c.category,
        "coverage": a.coverage, "valueScore": a.value_score, "alphaCount": a.alpha_count,
        "last_verified": a.last_verified} for c, a in rows]


def local_fields(dataset_ids, instrument, region, delay, universe, search="", data_type="ALL", field_ids=None):
    with SessionLocal() as db:
        q = select(M.FieldConcept, M.FieldAvailability, M.DatasetConcept).join(M.FieldAvailability,
            M.FieldAvailability.field_id == M.FieldConcept.field_id).where(
            M.FieldAvailability.instrument == instrument, M.FieldAvailability.region == region,
            M.FieldAvailability.delay == int(delay), M.FieldAvailability.universe == universe).outerjoin(
            M.DatasetConcept, M.DatasetConcept.dataset_id == M.FieldConcept.dataset_id)
        if dataset_ids: q = q.where(M.FieldConcept.dataset_id.in_(dataset_ids))
        if field_ids: q = q.where(M.FieldConcept.field_id.in_(field_ids))
        if data_type != "ALL": q = q.where(M.FieldConcept.type == data_type.upper())
        if search:
            like = f"%{search}%"; q = q.where(or_(M.FieldConcept.field_id.ilike(like), M.FieldConcept.description.ilike(like)))
        rows = db.execute(q.limit(20000)).all()
    return [{"id": c.field_id, "dataset_id": c.dataset_id, "description": c.description, "type": c.type,
        "prefix": c.prefix, "nature": c.nature, "coverage": a.coverage,
        "dateCoverage": a.date_coverage, "alphaCount": a.alpha_count,
        "is_virgin": a.is_virgin, "last_verified": a.last_verified,
        "category": (d.category if d else "")} for c, a, d in rows]


def compatible_fields(field_id, instrument, region, delay, universe, policy="semantic", limit=100):
    base_rows = local_fields([], instrument, region, delay, universe)
    base = next((x for x in base_rows if x["id"] == field_id), None)
    if not base: return []
    from app.knowledge.service import _tokens, _cosine
    out = []
    for x in base_rows:
        if x["id"] == field_id or x["type"] != base["type"]: continue
        score = _cosine(_tokens(base["description"]), _tokens(x["description"]))
        same_ds = x["dataset_id"] == base["dataset_id"]
        if policy == "same_dataset" and not same_ds: continue
        if policy == "semantic" and score < .25: continue
        out.append({**x, "compatibility": round(score, 3), "reason": "same dataset" if same_ds else "description similarity"})
    return sorted(out, key=lambda x: (-x["compatibility"], x["alphaCount"]))[:limit]


def match_hypothesis(text, instrument, region, delay, universe, limit=100):
    from app.knowledge.service import _tokens, _cosine
    target = _tokens(text)
    rows = local_fields([], instrument, region, delay, universe)
    out = [{**x, "match": round(_cosine(target, _tokens(x["description"], x["nature"])), 3)} for x in rows]
    return sorted([x for x in out if x["match"] > 0], key=lambda x: (-x["match"], x["alphaCount"]))[:limit]


def export_catalog():
    return {"format": "ace-catalog/v1", "exported_at": time.time(), "health": health(),
            "datasets": local_datasets_export(), "fields": local_fields_export()}


def import_catalog(payload: dict) -> dict:
    """Merge an ACE catalog backup into the local database without deleting newer local rows."""
    if payload.get("format") != "ace-catalog/v1":
        raise ValueError("Unsupported catalog backup format.")
    created = Counter()
    with SessionLocal() as db:
        for row in payload.get("datasets", {}).get("concepts", []):
            key = str(row.get("dataset_id") or "").strip()
            if not key: continue
            obj = db.get(M.DatasetConcept, key) or M.DatasetConcept(dataset_id=key)
            for name in ("name", "description", "category", "subcategory"):
                if row.get(name) is not None: setattr(obj, name, str(row[name]))
            db.add(obj); created["dataset_concepts"] += 1
        for row in payload.get("fields", {}).get("concepts", []):
            key = str(row.get("field_id") or "").strip()
            if not key: continue
            obj = db.get(M.FieldConcept, key) or M.FieldConcept(field_id=key, dataset_id=str(row.get("dataset_id") or ""))
            for name in ("dataset_id", "description", "type", "prefix", "nature"):
                if row.get(name) is not None: setattr(obj, name, str(row[name]))
            db.add(obj); created["field_concepts"] += 1
        db.flush()
        availability_specs = [
            (payload.get("datasets", {}).get("availability", []), M.DatasetAvailability, "dataset_id",
             ("coverage", "value_score", "alpha_count", "last_verified"), "dataset_availability"),
            (payload.get("fields", {}).get("availability", []), M.FieldAvailability, "field_id",
             ("coverage", "date_coverage", "alpha_count", "is_virgin", "last_verified"), "field_availability"),
        ]
        for rows, model, id_name, attrs, counter_name in availability_specs:
            for row in rows:
                key = str(row.get(id_name) or "").strip()
                if not key: continue
                q = select(model).where(getattr(model, id_name) == key,
                    model.instrument == str(row.get("instrument") or "EQUITY"),
                    model.region == str(row.get("region") or ""), model.delay == int(row.get("delay", 1)),
                    model.universe == str(row.get("universe") or ""))
                obj = db.scalar(q) or model(**{id_name: key, "instrument": str(row.get("instrument") or "EQUITY"),
                    "region": str(row.get("region") or ""), "delay": int(row.get("delay", 1)),
                    "universe": str(row.get("universe") or "")})
                for name in attrs:
                    if row.get(name) is not None: setattr(obj, name, row[name])
                db.add(obj); created[counter_name] += 1
        db.commit()
    return {"merged": dict(created), "health": health()}


def graph(limit: int = 500) -> dict:
    """Return a bounded local dataset→field graph for research and future knowledge algorithms."""
    limit = max(1, min(int(limit), 5000))
    with SessionLocal() as db:
        datasets = db.scalars(select(M.DatasetConcept).limit(limit)).all()
        remaining = max(0, limit - len(datasets))
        fields = db.scalars(select(M.FieldConcept).limit(remaining)).all()
    nodes = ([{"id": f"dataset:{x.dataset_id}", "kind": "dataset", "label": x.name or x.dataset_id,
               "category": x.category, "description": x.description} for x in datasets] +
             [{"id": f"field:{x.field_id}", "kind": "field", "label": x.field_id,
               "type": x.type, "nature": x.nature, "description": x.description} for x in fields])
    ds_ids = {x.dataset_id for x in datasets}
    edges = [{"source": f"dataset:{x.dataset_id}", "target": f"field:{x.field_id}", "kind": "contains"}
             for x in fields if x.dataset_id in ds_ids]
    return {"nodes": nodes, "edges": edges, "truncated": len(nodes) >= limit}


def reset_catalog() -> dict:
    """Clear only the local metadata catalog and its sync history; research and results remain."""
    with SessionLocal() as db:
        counts = {"fields": db.scalar(select(func.count()).select_from(M.FieldConcept)) or 0,
                  "datasets": db.scalar(select(func.count()).select_from(M.DatasetConcept)) or 0}
        for model in (M.CatalogSyncTask, M.CatalogSyncRun, M.FieldAvailability, M.FieldConcept,
                      M.DatasetAvailability, M.DatasetConcept):
            db.execute(delete(model))
        db.commit()
    return {"deleted": counts, "health": health()}


def local_datasets_export():
    with SessionLocal() as db:
        concepts = [{k: getattr(x, k) for k in ("dataset_id", "name", "description", "category", "subcategory")}
                    for x in db.scalars(select(M.DatasetConcept)).all()]
        availability = [{k: getattr(x, k) for k in ("dataset_id", "instrument", "region", "delay", "universe", "coverage", "value_score", "alpha_count", "last_verified")}
                        for x in db.scalars(select(M.DatasetAvailability)).all()]
    return {"concepts": concepts, "availability": availability}


def local_fields_export():
    with SessionLocal() as db:
        concepts = [{k: getattr(x, k) for k in ("field_id", "dataset_id", "description", "type", "prefix", "nature")}
                    for x in db.scalars(select(M.FieldConcept)).all()]
        availability = [{k: getattr(x, k) for k in ("field_id", "instrument", "region", "delay", "universe", "coverage", "date_coverage", "alpha_count", "is_virgin", "last_verified")}
                        for x in db.scalars(select(M.FieldAvailability)).all()]
    return {"concepts": concepts, "availability": availability}
