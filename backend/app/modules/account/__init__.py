from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.account import service
from app.core.jobs import jobs
from app.brain import engine

PREFIX="/api/account-research"; router=APIRouter()

class SyncReq(BaseModel):
    quarter:str
    # Footprint enrichment is intentionally limited to submitted REGULAR alphas.
    # Draft/unsubmitted research is never inventoried by this feature.
    include_submitted_regular: bool = True

@router.post("/sync")
def sync(req:SyncReq):
    try: service._quarter_bounds(req.quarter)
    except ValueError as e: raise HTTPException(400,str(e))
    state = engine.ui_session_status()
    if not state.get("ok") or state.get("state") != "valid":
        raise HTTPException(409, "Account metrics sync waits until the BRAIN session is confirmed valid.")
    def task(progress,should_cancel): return service.sync_quarter(req.quarter,req.include_submitted_regular,progress,should_cancel)
    return {"job_id":jobs.submit("account-research-sync",task)}

@router.get("/report")
def report(quarter:str):
    try:return service.report(quarter)
    except ValueError as e:raise HTTPException(400,str(e))
