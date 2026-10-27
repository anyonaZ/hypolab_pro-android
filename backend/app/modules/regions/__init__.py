"""Persistent Market Transfer Lab and exact cross-context data intersections."""
from __future__ import annotations
import hashlib, json, re, time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from app.brain import engine
from app.catalog import service as catalog
from app.core.jobs import jobs
from app.db.base import SessionLocal
from app.db import models as M
PREFIX = "/api/regions"
router = APIRouter()

class TransferContext(BaseModel):
    region: str
    delay: int
    universe: str = ""
    neutralizations: list[str] = Field(default_factory=list)

class CommonReq(BaseModel):
    instrument: str = "EQUITY"
    contexts: list[TransferContext] = Field(min_length=2, max_length=8)
    dataset_ids: list[str] = Field(default_factory=list)
    stage: str = "datasets"

class StudySave(BaseModel):
    id: int | None = None
    name: str = "Transfer study"
    mode: str = "regions"
    instrument: str = "EQUITY"
    contexts: list[TransferContext] = Field(min_length=2, max_length=8)
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_field_ids: list[str] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)

class CandidateReq(BaseModel):
    expressions: list[str]
    origin: str = "research"
    lineage: dict = Field(default_factory=dict)

class StudySelection(BaseModel):
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_field_ids: list[str] = Field(default_factory=list)


def _loads(value, fallback):
    try: return json.loads(value or "")
    except Exception: return fallback

def _study(row):
    if not row: return None
    return {"id":row.id,"name":row.name,"mode":row.mode,"instrument":row.instrument,
            "status":row.status,"active":bool(row.active),"current_stage":row.current_stage,
            "active_job_id":row.active_job_id,"created_at":row.created_at,"updated_at":row.updated_at,
            "contexts":_loads(row.contexts_json,[]),"datasets":_loads(row.datasets_json,[]),
            "selected_dataset_ids":_loads(row.selected_datasets_json,[]),"fields":_loads(row.fields_json,[]),
            "selected_field_ids":_loads(row.selected_fields_json,[]),"checkpoints":_loads(row.checkpoints_json,{}),
            "settings":_loads(row.settings_json,{})}

def _set_active(db, study_id):
    for row in db.scalars(select(M.TransferStudy).where(M.TransferStudy.active.is_(True))).all():
        row.active = row.id == study_id

def _context_datasets(instrument, region, delay, universe):
    """Mount a complete local catalog scope; fetch only when that exact scope is missing."""
    status = catalog.context_status(instrument, region, delay, universe)
    if status["datasets_ready"]:
        return catalog.local_datasets(instrument, region, delay, universe), "local"
    rows = engine.get_datasets(region=region, universe=universe, delay=delay, instrument_type=instrument)
    catalog.ingest_datasets(instrument, region, delay, universe, rows)
    return rows, "brain"

def _context_fields(instrument, region, delay, universe, dataset_ids):
    """Mount fields only when every requested dataset has a completed local field sync."""
    status = catalog.context_status(instrument, region, delay, universe, dataset_ids)
    if status["fields_ready"]:
        return catalog.local_fields(dataset_ids, instrument, region, delay, universe), "local"
    df,_ = engine.fetch_fields(dataset_ids, region=region, universe=universe, delay=delay,
                               instrument_type=instrument, data_type="ALL", search="")
    cols=[c for c in ("id","description","type","alphaCount","dateCoverage","coverage","is_virgin","dataset_id","startDate","endDate","lastUpdated","frequency") if df is not None and c in df.columns]
    values=engine._json_safe(df[cols].to_dict(orient="records")) if df is not None and not df.empty else []
    by_ds={}
    for value in values:
        did=str(value.get("dataset_id") or (dataset_ids[0] if len(dataset_ids)==1 else "")); value["dataset_id"]=did
        value["prefix"]=str(value.get("id","")).split("_",1)[0].lower(); by_ds.setdefault(did,[]).append(value)
    for did,items in by_ds.items(): catalog.ingest_fields(instrument,region,delay,universe,did,items)
    return values, "brain"

def _save_study(req: StudySave):
    now=time.time()
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,req.id) if req.id else None
        old_contexts=_loads(row.contexts_json,[]) if row else []
        old_mode=row.mode if row else ""
        contexts=[x.model_dump() for x in req.contexts]
        coordinates=lambda rows:[(x.get("region"),int(x.get("delay",1)),x.get("universe","")) for x in rows]
        changed=old_mode!=req.mode or coordinates(old_contexts)!=coordinates(contexts)
        if not row:
            row=M.TransferStudy(created_at=now); db.add(row); db.flush()
        _set_active(db,row.id)
        row.name=req.name.strip() or ("Cross-region study" if req.mode=="regions" else "D0/D1 study")
        row.mode=req.mode; row.instrument=req.instrument; row.contexts_json=json.dumps(contexts)
        row.selected_datasets_json=json.dumps(req.selected_dataset_ids)
        row.selected_fields_json=json.dumps(req.selected_field_ids); row.settings_json=json.dumps(req.settings)
        row.active=True; row.updated_at=now
        if changed:
            row.status="draft"; row.current_stage="setup"; row.active_job_id=""
            row.datasets_json="[]"; row.fields_json="[]"; row.checkpoints_json="{}"
        db.commit(); db.refresh(row); return _study(row)

def _run_study(study_id:int, stage:str, progress, should_cancel):
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise RuntimeError("Transfer study no longer exists.")
        instrument=row.instrument; contexts=_loads(row.contexts_json,[])
        selected=_loads(row.selected_datasets_json,[]); checkpoints=_loads(row.checkpoints_json,{})
    bucket=checkpoints.setdefault(stage,{})
    maps=[]; resolved=[]
    for i,raw in enumerate(contexts):
        if should_cancel(): break
        region,delay,universe=engine.valid_combo(instrument,raw["region"],raw["delay"],raw.get("universe") or "TOP3000")
        offered=engine.valid_neutralizations(instrument,region)
        requested=[str(x) for x in raw.get("neutralizations",[]) if str(x)]
        neutralizations=[x for x in requested if not offered or x in offered]
        if not neutralizations and offered: neutralizations=[sorted(offered)[0]]
        if not neutralizations: neutralizations=["INDUSTRY"]
        ctx={"region":region,"delay":delay,"universe":universe,"neutralizations":neutralizations}; resolved.append(ctx)
        key=f"{region}|{delay}|{universe}"
        cached=bucket.get(key)
        if cached is not None:
            values=cached
            progress(total=len(contexts),done=i+1,message=f"restored {region} D{delay} from checkpoint")
        else:
            progress(total=len(contexts),done=i,message=f"{stage} · {region} D{delay} · {universe}")
            datasets, source = _context_datasets(instrument,region,delay,universe)
            if stage=="datasets": values=datasets
            else:
                chosen=[x for x in selected if any(str(d.get("id"))==x for d in datasets)]
                if not chosen: raise RuntimeError(f"Selected datasets are unavailable in {region} D{delay}.")
                values, source = _context_fields(instrument,region,delay,universe,chosen)
            bucket[key]=values
            with SessionLocal() as db:
                live=db.get(M.TransferStudy,study_id); live.checkpoints_json=json.dumps(checkpoints); live.updated_at=time.time(); db.commit()
            progress(total=len(contexts),done=i+1,message=f"mounted {region} D{delay} from {source}")
        maps.append({str(x.get("id")):x for x in values if x.get("id")})
    if len(maps)!=len(contexts): return {"cancelled":True,"contexts":resolved}
    common=set.intersection(*(set(x) for x in maps)) if maps else set()
    values=[{**maps[0][key],"available_in":resolved} for key in sorted(common)]
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id); row.contexts_json=json.dumps(resolved)
        if stage=="datasets":
            row.datasets_json=json.dumps(values); row.fields_json="[]"; row.selected_fields_json="[]"
            row.status="datasets_ready"; row.current_stage="datasets"
        else:
            row.fields_json=json.dumps(values); row.selected_fields_json=json.dumps([x["id"] for x in values])
            row.status="ready"; row.current_stage="fields"
        row.active_job_id=""; row.updated_at=time.time(); db.commit(); db.refresh(row); result=_study(row)
    result[f"{stage[:-1] if stage.endswith('s') else stage}_count"]=len(values)
    result["dataset_count"]=len(result["datasets"]); result["field_count"]=len(result["fields"])
    return result

def _common_research(req:CommonReq,progress,should_cancel):
    """Pure compatibility helper used by tests and non-persistent API clients."""
    dataset_maps=[]; field_maps=[]; contexts=[]
    for i,raw in enumerate(req.contexts):
        if should_cancel(): break
        region,delay,universe=engine.valid_combo(req.instrument,raw.region,raw.delay,raw.universe or "TOP3000")
        contexts.append({"region":region,"delay":delay,"universe":universe})
        rows,_source=_context_datasets(req.instrument,region,delay,universe)
        dataset_maps.append({str(x.get("id")):x for x in rows if x.get("id")})
        if req.stage=="fields":
            chosen=[x for x in req.dataset_ids if x in dataset_maps[-1]]
            values,_source=_context_fields(req.instrument,region,delay,universe,chosen)
            field_maps.append({str(x.get("id")):x for x in values if x.get("id")})
        progress(total=len(req.contexts),done=i+1)
    common_ds=set.intersection(*(set(x) for x in dataset_maps)) if dataset_maps else set()
    result={"contexts":contexts,"datasets":[dataset_maps[0][x] for x in sorted(common_ds)],"dataset_count":len(common_ds)}
    if req.stage=="fields":
        common_fields=set.intersection(*(set(x) for x in field_maps)) if field_maps else set()
        result.update(fields=[field_maps[0][x] for x in sorted(common_fields)],field_count=len(common_fields))
    return result

@router.post("/studies")
def save_study(req:StudySave): return _save_study(req)

@router.get("/studies")
def list_studies(limit:int=20):
    with SessionLocal() as db:
        rows=db.scalars(select(M.TransferStudy).order_by(M.TransferStudy.updated_at.desc()).limit(max(1,min(100,limit)))).all()
        return {"studies":[_study(x) for x in rows]}

@router.get("/studies/active")
def active_study():
    with SessionLocal() as db:
        row=db.scalar(select(M.TransferStudy).where(M.TransferStudy.active.is_(True)).order_by(M.TransferStudy.updated_at.desc()))
        return {"study":_study(row)}

@router.get("/studies/{study_id}")
def get_study(study_id:int):
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise HTTPException(404,"no such transfer study")
        return _study(row)

@router.post("/studies/{study_id}/compare/{stage}")
def compare_study(study_id:int,stage:str):
    if stage not in {"datasets","fields"}: raise HTTPException(400,"invalid comparison stage")
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise HTTPException(404,"no such transfer study")
        if row.active_job_id and jobs.get(row.active_job_id).get("status")=="running": return {"job_id":row.active_job_id,"study_id":study_id}
        row.status="running"; row.current_stage=stage; row.updated_at=time.time(); db.commit()
    def task(progress,should_cancel):
        try: return _run_study(study_id,stage,progress,should_cancel)
        except Exception:
            with SessionLocal() as db:
                live=db.get(M.TransferStudy,study_id)
                if live: live.status="error"; live.active_job_id=""; live.updated_at=time.time(); db.commit()
            raise
    jid=jobs.submit("market-transfer",task)
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if row.status=="running": row.active_job_id=jid
        db.commit()
    return {"job_id":jid,"study_id":study_id}

@router.post("/studies/{study_id}/deactivate")
def deactivate(study_id:int):
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if row: row.active=False; row.updated_at=time.time(); db.commit()
    return {"ok":True}

@router.post("/studies/{study_id}/clear")
def clear_comparison(study_id:int):
    """Clear comparison output only when the user explicitly starts over."""
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise HTTPException(404,"no such transfer study")
        if row.active_job_id and jobs.get(row.active_job_id).get("status")=="running":
            raise HTTPException(409,"Wait for or cancel the active comparison before clearing it.")
        row.status="draft"; row.current_stage="setup"; row.active_job_id=""
        row.datasets_json="[]"; row.fields_json="[]"
        row.selected_datasets_json="[]"; row.selected_fields_json="[]"
        row.checkpoints_json="{}"; row.updated_at=time.time()
        db.commit(); db.refresh(row)
        return _study(row)

@router.post("/studies/{study_id}/selection")
def save_selection(study_id:int,req:StudySelection):
    """Persist selections without touching contexts or comparison output."""
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise HTTPException(404,"no such transfer study")
        row.selected_datasets_json=json.dumps(req.selected_dataset_ids)
        row.selected_fields_json=json.dumps(req.selected_field_ids)
        row.updated_at=time.time(); db.commit(); db.refresh(row)
        return _study(row)

@router.post("/studies/{study_id}/activate")
def activate(study_id:int):
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row: raise HTTPException(404,"no such transfer study")
        _set_active(db,study_id); row.active=True; row.updated_at=time.time(); db.commit(); db.refresh(row)
        return _study(row)

@router.post("/studies/{study_id}/candidates")
def candidates(study_id:int,req:CandidateReq):
    made=0
    with SessionLocal() as db:
        if not db.get(M.TransferStudy,study_id): raise HTTPException(404,"no such transfer study")
        for expression in dict.fromkeys(x.strip() for x in req.expressions if x.strip()):
            fp=hashlib.sha256(re.sub(r"\s+","",expression).lower().encode()).hexdigest()
            exists=db.scalar(select(M.TransferCandidate).where(M.TransferCandidate.study_id==study_id,M.TransferCandidate.fingerprint==fp))
            if not exists:
                db.add(M.TransferCandidate(study_id=study_id,expression=expression,fingerprint=fp,origin=req.origin,lineage_json=json.dumps(req.lineage))); made+=1
        db.commit()
    return {"saved":made}

@router.get("/studies/{study_id}/candidates")
def list_candidates(study_id:int):
    with SessionLocal() as db:
        rows=db.scalars(select(M.TransferCandidate).where(M.TransferCandidate.study_id==study_id).order_by(M.TransferCandidate.created_at)).all()
        return {"candidates":[{"expression":x.expression,"origin":x.origin,"lineage":_loads(x.lineage_json,{})} for x in rows]}

@router.post("/common/start")
def common_start(req:CommonReq):
    study=_save_study(StudySave(mode="regions",instrument=req.instrument,contexts=req.contexts,selected_dataset_ids=req.dataset_ids))
    return compare_study(study["id"],req.stage)

@router.get("/jobs/{job_id}")
def common_job(job_id:str):
    job=jobs.get(job_id)
    if not job: raise HTTPException(404,"no such job")
    return job

# Light, hand-written market notes so the atlas is useful even before an LLM pass. These
# are guidance, not hard rules — research should still reason from the data.
NOTES = {
    "USA": "Deep, liquid large-cap market; rich fundamental & news coverage; delay-1 is the norm, delay-0 is competitive and Sharpe-driven.",
    "GLB": "Global cross-section; strong for breadth and regionally-neutral ideas; watch currency and coverage differences.",
    "EUR": "Fragmented across exchanges/currencies; sector rotation and cross-listing effects matter; liquidity thinner than USA.",
    "ASI": "Asia ex-Japan; retail-heavy flow, faster reversals, microstructure and liquidity gating are important.",
    "CHN": "China A-shares; strong retail participation and momentum/reversal regimes; policy and liquidity sensitive.",
    "JPN": "Deep, developed market; sector and value/quality effects; disciplined risk model.",
    "KOR": "Liquid but retail-driven; fast information diffusion and reversals.",
    "IND": "Growing, retail-heavy; liquidity concentrated in large caps; momentum and event effects.",
    "HKG": "Cross-listing with China exposure; liquidity varies; sensitive to flows.",
    "TWN": "Tech-heavy, export-sensitive; liquidity concentrated; momentum regimes.",
    "AMR": "Americas ex-USA; thinner coverage; be careful with breadth and data availability.",
    "MEA": "Middle East / Africa; thinner liquidity and coverage; favour robust, simple signals.",
}


@router.get("/info")
def info():
    # Account options are persisted so every known market is available immediately on a later
    # visit, even while the BRAIN session is being renewed. A successful refresh replaces cache.
    opts = catalog.account_options()
    # region -> {delays, universes, neutralizations}
    reg: dict = {}
    for r in opts:
        e = reg.setdefault(r["region"], {"delays": set(), "universes": set(), "neutralizations": set()})
        e["delays"].add(r["delay"])
        e["universes"].update(r.get("universes", []))
        e["neutralizations"].update(r.get("neutralizations", []))

    with SessionLocal() as db:
        ds = dict(db.execute(select(M.Dataset.region, func.count()).group_by(M.Dataset.region)).all())
        fl = dict(db.execute(select(M.Field.region, func.count()).group_by(M.Field.region)).all())
        # cross-region concepts a region shares (by shared dataset id) with others
        by_id = db.execute(
            select(M.Dataset.dataset_id, func.count(func.distinct(M.Dataset.region)).label("n"))
            .group_by(M.Dataset.dataset_id)).all()
        shared_ids = {d for d, n in by_id if n > 1}
        region_shared: dict = {}
        if shared_ids:
            rows = db.execute(select(M.Dataset.region, M.Dataset.dataset_id).distinct()).all()
            for region_name, did in rows:
                if did in shared_ids:
                    region_shared[region_name] = region_shared.get(region_name, 0) + 1

    out = []
    for name, e in sorted(reg.items()):
        out.append({
            "region": name,
            "delays": sorted(e["delays"]),
            "universes": sorted(e["universes"]),
            "neutralizations": sorted(e["neutralizations"]),
            "datasets_known": int(ds.get(name, 0)),
            "fields_known": int(fl.get(name, 0)),
            "cross_region_datasets": int(region_shared.get(name, 0)),
            "delay0": 0 in e["delays"],
            "note": NOTES.get(name, "Reason from the data: check coverage, liquidity and delay conventions for this region."),
        })
    return {"regions": out,"options": opts}
