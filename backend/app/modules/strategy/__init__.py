"""Strategy Atlas module — per-category LLM strategy exploration (device-seeded, never
hardcoded), plus the category list learned by the knowledge DB."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.strategy import service
from app.knowledge import service as knowledge

PREFIX = "/api/strategy"
router = APIRouter()


@router.get("/categories")
def categories():
    return {"categories": knowledge.overview().get("categories", [])}


class ExploreReq(BaseModel):
    category: str
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    n: int = 6
    mode: str = "single"   # single | two_categories


@router.post("/explore")
def explore(req: ExploreReq):
    def task(progress, should_cancel):
        return service.explore(req.category, req.region, req.delay, req.instrument, req.n, req.mode, progress)
    return {"job_id": jobs.submit("strategy", task)}


class FieldIntelReq(BaseModel):
    fields: list[dict] = []
    categories: dict[str, str] = {}
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    mode: str = "single"          # single | multi
    n: int = 8
    max_operators: int = 4
    transfer_context: list[dict] = []


@router.post("/field-intel")
def field_intel(req: FieldIntelReq):
    if not req.fields:
        raise HTTPException(400, "Select datafields first — Strategy Lab analyses your selection.")

    def task(progress, should_cancel):
        return service.field_intel(fields=req.fields, categories=req.categories, region=req.region,
                                   delay=req.delay, instrument=req.instrument, universe=req.universe,
                                   mode=req.mode, n=req.n, max_operators=req.max_operators,
                                   transfer_context=req.transfer_context, progress=progress)
    return {"job_id": jobs.submit("field-intel", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j
