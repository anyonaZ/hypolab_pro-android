from __future__ import annotations

import calendar
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import desc, select

from app.brain import engine
from app.db import models as M
from app.db.base import SessionLocal, init_db
from app.core.config import get_settings

_CAPS_FILE = get_settings().data_dir / "account-usage-capabilities.json"


def _quarter_bounds(label: str) -> tuple[datetime, datetime]:
    if label.upper() in {"ALL", "ALL-TIME", "ALL_TIME"}:
        return datetime(2000, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc)
    m = re.fullmatch(r"(20\d{2})-Q([1-4])", label.upper())
    if not m: raise ValueError("Quarter must look like 2026-Q3.")
    year, quarter = int(m.group(1)), int(m.group(2)); month = 1 + (quarter - 1) * 3
    return datetime(year, month, 1, tzinfo=timezone.utc), datetime(year + (month + 2) // 12, (month + 2) % 12 + 1, 1, tzinfo=timezone.utc)


def _months(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        month_end = datetime(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1, tzinfo=timezone.utc)
        yield cursor, min(month_end, end); cursor = month_end


def _expression(row: dict) -> str:
    regular = row.get("regular")
    if isinstance(regular, dict): return str(regular.get("code") or regular.get("expression") or "")
    return str(regular or row.get("expression") or row.get("code") or "")


def _metric(row: dict, name: str):
    block = row.get("is") or row.get("inSample") or {}
    return block.get(name) if isinstance(block, dict) else None


def _analyse(expression: str, field_ids: set[str]) -> tuple[list[str], list[str]]:
    from app.generation.service import _fields_in, _operators_in
    operators = sorted(set(_operators_in(expression)))
    fields = set(_fields_in(expression, field_ids))
    # A footprint must also recognize fields not yet present in the local catalog.
    # Function identifiers, keywords and BRAIN group identifiers are excluded; all
    # remaining identifiers are expression inputs.
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", expression))
    assigned = set(re.findall(r"\b([A-Za-z_]\w*)\s*=", expression))
    names = set(re.findall(r"\b[A-Za-z_]\w*\b", expression))
    reserved = called | assigned | set(operators) | {"true","false","nan","inf","filter","rate","dense","constant",
        "industry","sector","subindustry","market","country","exchange","currency","instrument"}
    fields.update(x for x in names if x.lower() not in reserved and not x.isupper() and not x.isdigit())
    return operators, sorted(fields)


def _wanted_status(status: str, kinds: set[str]) -> bool:
    is_unsubmitted = str(status or "").upper() in {"UNSUBMITTED", "DRAFT", ""}
    return (is_unsubmitted and "unsubmitted" in kinds) or (not is_unsubmitted and "submitted" in kinds)


def _usage_metric(body, keys: tuple[str, ...]) -> dict:
    """Normalize an authoritative account-usage response without guessing values."""
    if isinstance(body, list): rows = body
    elif isinstance(body, dict):
        rows = body.get("results") or body.get("records") or body.get("items") or []
        if isinstance(rows, dict): rows = rows.get("records") or []
    else: rows = []
    items = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            key = next((row.get(k) for k in keys if row.get(k) is not None), None)
            count = row.get("count", row.get("alphaCount", row.get("usageCount")))
            if key is not None: items.append({"key": str(key), "count": int(count or 0)})
    explicit = body.get("count") if isinstance(body, dict) else None
    return {"available": bool(items) or explicit is not None, "count": int(explicit if explicit is not None else len(items)), "items": items}


def sync_quarter(quarter: str, include_submitted_regular=True,
                 progress=lambda **_: None, should_cancel=lambda: False) -> dict:
    init_db(); start, end = _quarter_bounds(quarter); session = engine.require_session()
    with SessionLocal() as db:
        job = M.AccountResearchSync(quarter=quarter.upper()); db.add(job); db.commit(); sync_id = job.id
        field_rows = db.scalars(select(M.FieldConcept)).all()
        field_ids = {x.field_id for x in field_rows}; dataset_by_field = {x.field_id: x.dataset_id for x in field_rows}
    # All-time uses a single query window when the submitted pool fits below BRAIN's
    # 1,000-offset boundary. Quarter reports retain monthly windows.
    all_time = quarter.upper() in {"ALL", "ALL-TIME", "ALL_TIME"}
    windows = [(None, None)] if all_time else list(_months(start, end))
    found: dict[str, dict] = {}; total_steps = len(windows) * 2 + 4
    try:
        # Expressions are useful only for operator/field concentration. Keep this
        # enrichment tightly scoped: submitted, visible, REGULAR alphas only. No
        # draft/unsubmitted alpha is requested or stored.
        if include_submitted_regular:
            try: unsupported = set(json.loads(_CAPS_FILE.read_text(encoding="utf-8")).get("unsupported", []))
            except Exception: unsupported = set()
            for step, (lo, hi) in enumerate(windows):
                if should_cancel(): break
                for hidden_index, hidden in enumerate((False, True)):
                    try:
                        offset = 0
                        while not should_cancel():
                            tokens = ["limit=50", f"offset={offset}", "order=-dateCreated",
                        f"hidden={'true' if hidden else 'false'}",
                        "status!=UNSUBMITTED", "type=REGULAR"]
                            if lo is not None: tokens.append("dateCreated>=" + quote(lo.isoformat(), safe=""))
                            if hi is not None: tokens.append("dateCreated<" + quote(hi.isoformat(), safe=""))
                            url = f"{engine.ace.brain_api_url}/users/self/alphas?{'&'.join(tokens)}"
                            response = session.get(url, headers={"Accept": "application/json;version=4.0"}, timeout=45)
                            response.raise_for_status(); body = response.json(); rows = body.get("results") or []
                            for row in rows:
                                aid = str(row.get("id") or "")
                                status = str(row.get("status") or row.get("stage") or "")
                                alpha_type = str(row.get("type") or ("REGULAR" if row.get("regular") else "")).upper()
                                if aid and not _wanted_status(status, {"unsubmitted"}) and alpha_type == "REGULAR": found[aid] = row
                            offset += len(rows)
                            if len(rows) < 50 or offset >= int(body.get("count") or 0): break
                        progress(total=total_steps, done=step * 2 + hidden_index + 1,
                            message=f"Account metrics · {quarter} · {len(found)} submitted regular alphas")
                    except Exception:
                        # Activity and pyramid metrics remain useful when alpha-list access is
                        # unavailable for an account; do not fail the entire account sync.
                        progress(total=total_steps, done=step * 2 + hidden_index + 1, message="Submitted REGULAR enrichment unavailable; continuing with account metrics")
        def get(path, **params):
            response=session.get(f"{engine.ace.brain_api_url}{path}",params=params or None,timeout=45)
            response.raise_for_status(); return response.json() if response.content else {}
        def activity(kind):
            body=get(f"/users/self/activities/{kind}", **{"date>":start.date().isoformat()})
            outer=body.get("records") if isinstance(body,dict) else {}; values=outer.get("records") if isinstance(outer,dict) else []
            return [[str(x[0]),int(x[1])] for x in values or [] if isinstance(x,(list,tuple)) and len(x)>1 and str(x[0])<end.date().isoformat()]
        extras={}
        for extra_index, (name,loader) in enumerate({
            "simulations":lambda:activity("simulations"), "submissions":lambda:activity("submissions"),
            "pyramid_alphas":lambda:(get("/users/self/activities/pyramid-alphas",startDate=start.date().isoformat(),endDate=end.date().isoformat()).get("pyramids") or []),
            "pyramid_multipliers":lambda:(get("/users/self/activities/pyramid-multipliers").get("pyramids") or []),
        }.items()):
            try: extras[name]=loader()
            except Exception as exc: extras[name]={"unavailable":str(exc)[:300]}
            progress(total=total_steps, done=len(windows)*2+extra_index+1, message=f"Account metrics · {name.replace('_',' ')}")
        # BRAIN installations differ in which account-usage resources they expose.
        # Manual enrichment makes at most these three small requests. A 404/403 is
        # stored as unavailable; the UI never substitutes expression-derived guesses.
        usage = {}
        if include_submitted_regular:
            probes = {
                "operators": ("/users/self/activities/operators", ("operator", "id", "name")),
                "fields": ("/users/self/activities/data-fields", ("field", "fieldId", "id", "name")),
                "datasets": ("/users/self/activities/data-sets", ("dataset", "datasetId", "id", "name")),
            }
            for name, (path, keys) in probes.items():
                if name in unsupported:
                    usage[name] = {"available": False, "count": None, "items": [], "reason": "Not exposed by this BRAIN account/API."}
                    continue
                try: usage[name] = _usage_metric(get(path), keys)
                except Exception as exc:
                    usage[name] = {"available": False, "count": None, "items": [], "reason": str(exc)[:160]}
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status in {403, 404, 405}: unsupported.add(name)
            try: _CAPS_FILE.write_text(json.dumps({"unsupported": sorted(unsupported)}), encoding="utf-8")
            except Exception: pass
        extras["authoritative_usage"] = usage
        try: extras["alpha_summary"] = get("/users/self/alphas/summary")
        except Exception as exc: extras["alpha_summary"] = {"unavailable": str(exc)[:160]}
        now = time.time()
        with SessionLocal() as db:
            for aid, raw in found.items():
                expression = _expression(raw); operators, fields = _analyse(expression, field_ids)
                settings = raw.get("settings") or {}; created = raw.get("dateCreated") or raw.get("createdAt")
                try: created_ts = datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
                except Exception: created_ts = 0
                metrics = {k: _metric(raw, k) for k in ("sharpe", "fitness", "turnover", "returns", "margin", "drawdown")}
                item = db.get(M.AccountAlphaSnapshot, aid) or M.AccountAlphaSnapshot(alpha_id=aid)
                item.created_at=created_ts; item.fetched_at=now; item.status=str(raw.get("status") or raw.get("stage") or "")
                item.region=str(settings.get("region") or ""); item.delay=int(settings.get("delay") or 0)
                item.universe=str(settings.get("universe") or ""); item.expression=expression
                item.operators_json=json.dumps(operators); item.fields_json=json.dumps(fields)
                item.datasets_json=json.dumps(sorted({dataset_by_field[x] for x in fields if x in dataset_by_field}))
                item.metrics_json=json.dumps(metrics); item.payload_json=json.dumps(raw); db.add(item)
            job=db.get(M.AccountResearchSync,sync_id); job.status="cancelled" if should_cancel() else "complete"
            job.fetched=len(found); job.summary_json=json.dumps(extras); job.updated_at=now; db.commit()
        return {"sync_id": sync_id, "quarter": quarter.upper(), "fetched": len(found), "cancelled": should_cancel()}
    except Exception as exc:
        with SessionLocal() as db:
            job=db.get(M.AccountResearchSync,sync_id); job.status="error"; job.error=str(exc)[:1000]; job.updated_at=time.time(); db.commit()
        raise


def report(quarter: str) -> dict:
    start, end = _quarter_bounds(quarter)
    with SessionLocal() as db:
        rows = db.scalars(select(M.AccountAlphaSnapshot).where(
            M.AccountAlphaSnapshot.created_at >= start.timestamp(), M.AccountAlphaSnapshot.created_at < end.timestamp())
            .order_by(desc(M.AccountAlphaSnapshot.created_at))).all()
        concepts = {x.dataset_id: x.category for x in db.scalars(select(M.DatasetConcept)).all()}
        syncs = db.scalars(select(M.AccountResearchSync).where(M.AccountResearchSync.quarter == quarter.upper())
            .order_by(desc(M.AccountResearchSync.created_at)).limit(10)).all()
    rows = [x for x in rows if not _wanted_status(x.status, {"unsubmitted"})]
    operators=Counter(); fields=Counter(); datasets=Counter(); categories=Counter(); regions=Counter(); status_counts=Counter(); metrics=[]
    for row in rows:
        operators.update(json.loads(row.operators_json or "[]")); fields.update(json.loads(row.fields_json or "[]"))
        ds=json.loads(row.datasets_json or "[]"); datasets.update(ds); categories.update(concepts.get(x, "unknown") for x in ds)
        regions.update([f"{row.region} D{row.delay} · {row.universe}"]); status_counts.update([row.status or "unknown"])
        metrics.append(json.loads(row.metrics_json or "{}"))
    def top(counter): return [{"key": k, "count": v} for k,v in counter.most_common(100)]
    def avg(k):
        xs=[float(x[k]) for x in metrics if x.get(k) is not None]
        return round(sum(xs)/len(xs),4) if xs else None
    activities=json.loads(syncs[0].summary_json or "{}") if syncs else {}
    # Automatic metric refreshes deliberately skip optional usage probes. Preserve
    # the newest authoritative probe result so a background refresh cannot turn a
    # previously known account capability into an unexplained blank value.
    authoritative={}
    for sync in syncs:
        try: candidate=json.loads(sync.summary_json or "{}").get("authoritative_usage")
        except Exception: candidate=None
        if candidate:
            authoritative=candidate
            break
    return {"quarter":quarter.upper(),"alphas":len(rows),"statuses":top(status_counts),"operators":top(operators),
        "fields":top(fields),"datasets":top(datasets),"categories":top(categories),"markets":top(regions),
        "averages":{k:avg(k) for k in ("sharpe","fitness","turnover","returns","margin","drawdown")},
        "concentration":{"top_field_share":round(sum(v for _,v in fields.most_common(5))/max(1,sum(fields.values())),3),
            "top_dataset_share":round(sum(v for _,v in datasets.most_common(3))/max(1,sum(datasets.values())),3)},
        "recent":[{"id":x.alpha_id,"created_at":x.created_at,"status":x.status,"region":x.region,
            "delay":x.delay,"universe":x.universe,"expression":x.expression,"metrics":json.loads(x.metrics_json or "{}")} for x in rows],
        "activities":activities, "authoritative_usage":authoritative,
        "syncs":[{"id":x.id,"status":x.status,"fetched":x.fetched,"error":x.error,"updated_at":x.updated_at} for x in syncs]}
