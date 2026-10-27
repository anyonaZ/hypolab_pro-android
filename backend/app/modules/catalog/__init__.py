from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.catalog import service
from app.core.jobs import jobs

PREFIX = "/api/catalog"
router = APIRouter()


class PlanReq(BaseModel):
    contexts: list[str] = []
    instruments: list[str] = []
    regions: list[str] = []
    delays: list[int] = []
    universes: list[str] = []
    categories: list[str] = []
    include_fields: bool = True
    coverage_min: float = 0
    value_min: float = 0
    request_budget: int = Field(0, ge=0, le=10000)  # retained for old clients; zero/unlimited is enforced
    refresh_mode: str = "all"       # all|missing|stale
    stale_days: int = Field(30, ge=0, le=3650)


class ImportReq(BaseModel):
    payload: dict


@router.post("/plan")
def plan(req: PlanReq):
    try: return service.plan(req.model_dump())
    except Exception as e: raise HTTPException(400, str(e))


@router.post("/runs/{run_id}/start")
def start(run_id: int):
    if not service.run_detail(run_id): raise HTTPException(404, "no such sync plan")
    def task(progress, should_cancel): return service.execute(run_id, progress, should_cancel)
    return {"job_id": jobs.submit("catalog-sync", task), "run_id": run_id}


@router.post("/runs/{run_id}/resume")
def resume(run_id: int, additional_requests: int = 0, retry_errors: bool = True):
    if not service.resume(run_id, additional_requests, retry_errors): raise HTTPException(404, "no such sync plan")
    def task(progress, should_cancel): return service.execute(run_id, progress, should_cancel)
    return {"job_id": jobs.submit("catalog-sync", task), "run_id": run_id}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j: raise HTTPException(404, "no such job")
    return j


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str): return {"cancelled": jobs.cancel(jid)}


@router.get("/runs")
def runs(limit: int = 30): return {"runs": service.list_runs(limit)}


@router.get("/runs/{run_id}")
def detail(run_id: int):
    x = service.run_detail(run_id)
    if not x: raise HTTPException(404, "no such run")
    return x


@router.get("/health")
def health(): return service.health()


@router.get("/matrix")
def matrix(): return service.scope_matrix()


@router.get("/datasets")
def datasets(instrument: str="EQUITY", region: str="USA", delay: int=1, universe: str="TOP3000", category: str="", search: str=""):
    status = service.context_status(instrument, region, delay, universe)
    rows = service.local_datasets(instrument, region, delay, universe, category, search) if status["datasets_ready"] else []
    return {"count": len(rows), "datasets": rows, "source": "local", "catalog": status}


@router.get("/fields")
def fields(dataset_ids: str="", instrument: str="EQUITY", region: str="USA", delay: int=1,
           universe: str="TOP3000", search: str="", data_type: str="ALL", field_ids: str=""):
    selected = [x for x in dataset_ids.split(",") if x]
    status = service.context_status(instrument, region, delay, universe, selected)
    rows = service.local_fields(selected, instrument, region, delay, universe,
                                search, data_type, [x for x in field_ids.split(",") if x]) if status["fields_ready"] else []
    return {"count": len(rows), "fields": rows, "source": "local", "catalog": status}


@router.get("/compatible/{field_id}")
def compatible(field_id: str, instrument: str="EQUITY", region: str="USA", delay: int=1,
               universe: str="TOP3000", policy: str="semantic", limit: int=100):
    return {"fields": service.compatible_fields(field_id, instrument, region, delay, universe, policy, limit)}


@router.get("/match")
def match(q: str, instrument: str="EQUITY", region: str="USA", delay: int=1,
          universe: str="TOP3000", limit: int=100):
    return {"fields": service.match_hypothesis(q, instrument, region, delay, universe, limit)}


@router.get("/export")
def export(): return JSONResponse(service.export_catalog(), headers={"Content-Disposition": "attachment; filename=hypolab-catalog.json"})


@router.post("/import")
def import_backup(req: ImportReq):
    try: return service.import_catalog(req.payload)
    except ValueError as e: raise HTTPException(400, str(e))


@router.get("/graph")
def graph(limit: int = 500): return service.graph(limit)


@router.delete("/reset")
def reset(): return service.reset_catalog()


def on_startup():
    # A process restart cannot retain worker threads. Preserve tasks and make the interrupted run
    # explicitly resumable by the user instead of silently issuing account requests.
    from app.db.base import SessionLocal, init_db
    from app.db import models as M
    from sqlalchemy import update
    init_db()
    with SessionLocal() as db:
        db.execute(update(M.CatalogSyncRun).where(M.CatalogSyncRun.status == "running").values(status="paused"))
        db.execute(update(M.CatalogSyncTask).where(M.CatalogSyncTask.status == "running").values(status="retry"))
        db.commit()
