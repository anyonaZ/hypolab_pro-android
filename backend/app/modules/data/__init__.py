"""Data module — the research context's options, datasets, and datafields.

Thin HTTP over `brain.engine`. Datafields carry the VECTOR/MATRIX split and a computed
`prefix` (the field-family first 3 letters) so the Data Explorer can facet without
re-deriving it client-side. A clean 409 is returned when there is no session, so the UI
can prompt for login instead of failing deep in the API.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.brain import engine
from app.knowledge import service as knowledge
from app.catalog import service as catalog

PREFIX = "/api/data"
router = APIRouter()


def _learn(fn, *a, **k):
    """Feed the knowledge DB without ever failing the user-facing request."""
    try:
        fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        print(f"[knowledge] ingest skipped: {e}")


def field_prefix(fid: str) -> str:
    """Field family = everything up to the first underscore (nws18_acb -> 'nws18'). Fields
    with no underscore are grouped under a single general bucket."""
    fid = str(fid or "")
    return fid.split("_", 1)[0].lower() if "_" in fid else "general"


def _field_research_metadata(row: dict) -> dict:
    text = f"{row.get('id', '')} {row.get('description', '')}".lower()
    unit = "category" if any(x in text for x in ("sector", "industry", "country", "classification")) else \
        "timestamp" if any(x in text for x in ("date", "time", "days since")) else \
        "percentage" if any(x in text for x in ("percent", "%", "margin", "yield", "rate")) else \
        "currency" if any(x in text for x in ("usd", "dollar", "revenue", "income", "price")) else \
        "count" if any(x in text for x in ("count", "number of", "volume")) else \
        "ratio" if any(x in text for x in ("ratio", "per share", "score", "return")) else "unknown"
    lag = "event-driven" if any(x in text for x in ("news", "estimate", "transaction", "trade", "price")) else \
        "quarterly/reporting lag" if any(x in text for x in ("fundamental", "income", "balance", "quarter")) else "unknown"
    vector = str(row.get("type", "")).upper() == "VECTOR"
    reductions = ["vec_avg", "vec_sum"] if any(x in text for x in ("amount", "value", "volume")) else \
        ["vec_max", "vec_min"] if any(x in text for x in ("high", "low", "boundary", "extreme")) else \
        ["vec_avg", "vec_stddev", "vec_count"] if vector else []
    coverage = float(row.get("dateCoverage") or row.get("coverage") or 0)
    if coverage > 1: coverage /= 100
    alpha_count = int(row.get("alphaCount") or 0)
    score = round(max(0, min(100, coverage * 70 + (15 if row.get("description") else 0) +
                                 (10 if row.get("is_virgin") else max(0, 10 - alpha_count / 20)) + 5)))
    reason = ("strong historical coverage" if coverage >= .8 else "moderate historical coverage" if coverage >= .5 else "limited historical coverage")
    return {"unit": unit, "reporting_lag": lag, "vector_reductions": reductions,
            "quality_score": score, "research_potential": f"{reason}; {unit} semantics; {'low' if alpha_count < 20 else 'established'} existing usage"}


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except engine.SessionExpired as e:
        raise HTTPException(409, str(e))


@router.get("/options")
def options(refresh: bool = False):
    # Options change infrequently and are needed to mount the whole workspace. A transient BRAIN
    # response must not turn startup into a 500 when the last successful matrix is already local.
    rows = catalog.account_options(refresh=refresh)
    if rows:
        return {"records": rows, "source": "local-fallback" if refresh else "cache"}
    # First-run/offline state: keep workspace startup healthy and let the login/catalog screens
    # explain what is missing. Retrying the same failing network call here only produced a 500.
    return {"records": [], "source": "unavailable",
            "warning": "Account options are not cached yet. Confirm the BRAIN session and refresh."}


@router.get("/datasets")
def datasets(region: str = "USA", universe: str = "TOP3000", delay: int = 1,
             instrument_type: str = "EQUITY", theme: bool = False,
             coverage_min: float = 0.0, value_min: float = 0.0, category: str = ""):
    rows = _guard(engine.get_datasets, region=region, universe=universe, delay=delay,
                  instrument_type=instrument_type, theme=theme, coverage_min=coverage_min,
                  value_min=value_min, category=category)
    # The local catalog is the durable source used when this context is opened again. Unlike
    # optional knowledge enrichment, persistence must not fail silently after a successful BRAIN
    # response: reporting success without saving is what left Data Explorer empty on revisit.
    try:
        catalog.ingest_datasets(instrument_type, region, delay, universe, rows)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Datasets were retrieved but could not be saved locally: {e}")
    _learn(knowledge.ingest_datasets, region, delay, instrument_type, universe, rows)
    return {"count": len(rows), "datasets": rows, "saved_locally": True}


class FieldsReq(BaseModel):
    dataset_ids: list[str]
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    instrument_type: str = "EQUITY"
    data_type: str = "ALL"
    search: str = ""
    limit: int = 2000


@router.post("/datafields")
def datafields(req: FieldsReq):
    df, raw = _guard(engine.fetch_fields, req.dataset_ids, region=req.region,
                     universe=req.universe, delay=req.delay,
                     instrument_type=req.instrument_type, data_type=req.data_type,
                     search=req.search)
    if df is None or getattr(df, "empty", True):
        return {"count": 0, "fetched": raw, "duplicates": 0, "vector": 0, "matrix": 0, "fields": []}

    cols = [c for c in ["id", "description", "type", "alphaCount", "dateCoverage",
                        "coverage", "is_virgin", "dataset_id", "startDate", "endDate",
                        "lastUpdated", "frequency"] if c in df.columns]
    out = df[cols].head(req.limit).to_dict(orient="records")
    for row in out:
        row["prefix"] = field_prefix(row.get("id", ""))
        row.update(_field_research_metadata(row))
    n_vec = int((df["type"].astype(str).str.upper() == "VECTOR").sum()) if "type" in df.columns else 0
    n_mat = int((df["type"].astype(str).str.upper() == "MATRIX").sum()) if "type" in df.columns else 0
    _learn(knowledge.ingest_fields, req.region, req.delay, req.dataset_ids, out)
    # Preserve the full availability context in the normalized local catalog.
    by_dataset = {}
    for row in out:
        by_dataset.setdefault(str(row.get("dataset_id") or (req.dataset_ids[0] if req.dataset_ids else "")), []).append(row)
    for did, rows in by_dataset.items():
        _learn(catalog.ingest_fields, req.instrument_type, req.region, req.delay, req.universe, did, rows)
    return engine._json_safe({
        "count": len(df), "fetched": raw, "duplicates": max(0, raw - len(df)),
        "vector": n_vec, "matrix": n_mat, "fields": out,
    })
