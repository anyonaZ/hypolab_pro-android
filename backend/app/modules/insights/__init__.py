"""Cross-workspace research intelligence computed from the local evidence ledger."""
from collections import defaultdict
import hashlib, json
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from app.db.base import SessionLocal
from app.db import models as M
from app.core.jobs import jobs

PREFIX="/api/insights"; router=APIRouter()

@router.get("/overview")
def overview():
    with SessionLocal() as db:
        result_count=db.scalar(select(func.count()).select_from(M.SimResult)) or 0
        expressions=db.scalar(select(func.count(func.distinct(M.SimResult.expression)))) or 0
        passed=db.scalar(select(func.count()).select_from(M.SimResult).where(M.SimResult.passed_gate.is_(True))) or 0
        return {"funnel":{"fields":db.scalar(select(func.count()).select_from(M.FieldConcept)) or 0,
          "hypotheses":db.scalar(select(func.count()).select_from(M.SavedHypothesis)) or 0,
          "expressions":expressions,"simulations":result_count,"useful":passed},
          "jobs":jobs.list_recent(80),"request_history":[{"id":x.id,"used":x.requests_used,"budget":x.request_budget,"status":x.status,"at":x.created_at} for x in db.scalars(select(M.CatalogSyncRun).order_by(desc(M.CatalogSyncRun.created_at)).limit(30)).all()],
          "allocation":[{"stage":"data","share":20},{"stage":"hypothesis","share":15},{"stage":"pilot","share":25},{"stage":"robustness","share":30},{"stage":"review","share":10}]}

@router.get("/fields")
def field_insights(region:str="",delay:int|None=None,universe:str=""):
    with SessionLocal() as db:
        rows=db.scalars(select(M.FieldConcept)).all(); av=db.scalars(select(M.FieldAvailability)).all()
    availability=defaultdict(list)
    for x in av:
        if (not region or x.region==region) and (delay is None or x.delay==delay) and (not universe or x.universe==universe): availability[x.field_id].append(x)
    buckets=defaultdict(list)
    for x in rows:
        key=" ".join(sorted(set((x.description or "").lower().split())))[:180]; buckets[key].append(x.field_id)
    duplicates=[v for k,v in buckets.items() if k and len(v)>1]
    out=[]
    for x in rows:
        aa=availability.get(x.field_id,[]); saturation=max([a.alpha_count for a in aa] or [0]); coverage=max([a.coverage for a in aa] or [0])
        compatible=["vec reduction"] if x.type.upper()=="VECTOR" else ["time series","cross sectional"]
        if x.nature in {"event","flag","sparse"}: compatible=["event gate","persistence","time since change"]
        out.append({"id":x.field_id,"saturation":saturation,"coverage":coverage,"type":x.type,"nature":x.nature,"compatible":compatible})
    return {"fields":out,"near_duplicates":duplicates,"capacity":sum(max(1,4-min(3,x["saturation"]//100)) for x in out)}

@router.get("/results")
def result_intelligence():
    with SessionLocal() as db: rows=db.scalars(select(M.SimResult).order_by(desc(M.SimResult.created_at))).all()
    fam=defaultdict(list)
    for x in rows: fam[hashlib.sha1(''.join(c for c in x.expression if not c.isdigit()).encode()).hexdigest()[:10]].append(x)
    families=[]
    for key,xs in fam.items():
        sharpes=[x.sharpe for x in xs if x.sharpe is not None]; fits=[x.fitness for x in xs if x.fitness is not None]
        families.append({"id":key,"trials":len(xs),"best_sharpe":max(sharpes or [None]),"best_fitness":max(fits or [None]),"expression":xs[0].expression})
    warnings=[]
    if len(rows)>500: warnings.append(f"{len(rows)} simulations are stored; treat isolated winners as multiple-testing risks.")
    return {"families":sorted(families,key=lambda x:x["best_fitness"] or -99,reverse=True)[:100],"warnings":warnings,"trial_count":len(rows)}

class SnapshotReq(BaseModel): name:str="Catalog snapshot"
@router.post("/catalog/snapshot")
def snapshot(req:SnapshotReq):
    from app.catalog import service
    payload=service.export_catalog()
    with SessionLocal() as db:
        row=M.CatalogSnapshot(name=req.name,payload_json=json.dumps(payload));db.add(row);db.commit();return {"id":row.id,"name":row.name}

@router.get("/catalog/snapshots")
def snapshots():
    with SessionLocal() as db: rows=db.scalars(select(M.CatalogSnapshot).order_by(desc(M.CatalogSnapshot.created_at))).all()
    return {"snapshots":[{"id":x.id,"name":x.name,"created_at":x.created_at} for x in rows]}
