"""Simulate module — run alphas across universes/neutralizations, judge them against the
success gate, tag with the user's own tags, and store results. The gate thresholds default
by delay (delay 0 is strict on Sharpe/Fitness) but the caller may override."""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.jobs import jobs
from app.simulation import service
from app.brain import engine
from app.db.base import SessionLocal
from app.db import models as M
import json, hashlib, re, time
from sqlalchemy import select

PREFIX = "/api/simulate"
router = APIRouter()


@router.get("/gate")
def gate(delay: int = 1):
    """The default gate thresholds for a delay — the UI shows these and lets the user edit."""
    t = service.gate_thresholds(delay)
    return {"delay": delay, "sharpe": t["sharpe"], "fitness": t["fitness"],
            "max_turnover": 0.70, "max_corr": 0.70}


class SimReq(BaseModel):
    expressions: list[str]
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universes: list[str] = ["TOP3000"]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    test_period: str = "P0Y"
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    max_trade: str = "OFF"
    visualization: bool = False
    concurrency: int = 3
    limit_of_multi: int = 10
    max_turnover: float = 0.70
    max_corr: float = 0.70
    # thresholds — default by delay when not supplied
    min_sharpe: float | None = None
    min_fitness: float | None = None
    # user-chosen tags (nothing hardcoded)
    tag: str = ""
    winner_tag: str = ""
    winner_color: str = "GREEN"
    tag_winners_above: float = 1.0
    # optional deeper checks (correlations live in the submission check)
    check_submission: bool = False
    get_pnl: bool = False
    get_stats: bool = False
    # Config-level sampling: randomize ALL configs (expr×universe×neutralization) then take a range.
    randomize: bool = False
    range_from: int = 0
    range_to: int = 0        # 0 = to the end


@router.post("/estimate")
def estimate(req: SimReq):
    """Explain configuration cost and locally reusable matches before consuming BRAIN capacity."""
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    expressions=list(dict.fromkeys(x.strip() for x in req.expressions if x.strip()))
    queue=[{"expression":e,"universe":u,"neutralization":n,"test_period":req.test_period}
           for e in expressions for u in (req.universes or ["TOP3000"]) for n in (req.neutralizations or ["INDUSTRY"])]
    with SessionLocal() as db:
        for item in queue:
            match=db.scalar(select(M.SimResult).where(M.SimResult.expression==item["expression"],
                M.SimResult.region==req.region,M.SimResult.delay==req.delay,M.SimResult.universe==item["universe"],
                M.SimResult.neutralization==item["neutralization"],M.SimResult.test_period==req.test_period).limit(1))
            item["local_match"]={"alpha_id":match.alpha_id,"passed":match.passed_gate} if match else None
    return {"expressions":len(expressions),"configurations":len(queue),
            "reusable":sum(1 for x in queue if x["local_match"]),"new_configurations":sum(1 for x in queue if not x["local_match"]),
            "batches":math.ceil(len(queue)/max(2,min(10,req.limit_of_multi))),"queue":queue[:1000]}


@router.post("/run")
def run(req: SimReq):
    if not req.expressions:
        raise HTTPException(400, "No expressions to simulate.")
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    t = service.gate_thresholds(req.delay)
    min_sharpe = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
    min_fitness = req.min_fitness if req.min_fitness is not None else t["fitness"]

    def task(progress, should_cancel):
        return service.run_simulation(
            expressions=req.expressions, region=req.region, delay=req.delay, instrument=req.instrument,
            universes=req.universes or ["TOP3000"], neutralizations=req.neutralizations or ["INDUSTRY"],
            decay=req.decay, truncation=req.truncation, test_period=req.test_period,
            pasteurization=req.pasteurization, unit_handling=req.unit_handling, nan_handling=req.nan_handling,
            max_trade=req.max_trade, visualization=req.visualization, concurrency=req.concurrency,
            limit_of_multi=req.limit_of_multi, max_turnover=req.max_turnover, min_sharpe=min_sharpe,
            min_fitness=min_fitness, max_corr=req.max_corr, tag=req.tag, winner_tag=req.winner_tag,
            winner_color=req.winner_color, tag_winners_above=req.tag_winners_above,
            check_submission=req.check_submission, get_pnl=req.get_pnl, get_stats=req.get_stats,
            randomize=req.randomize, range_from=req.range_from, range_to=req.range_to,
            progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("simulate", task)}


class TransferSimReq(BaseModel):
    study_id: int
    expressions: list[str]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = .08
    test_period: str = "P0Y0M0D"
    concurrency: int = 3
    limit_of_multi: int = 10
    tag: str = ""
    winner_tag: str = ""
    check_submission: bool = False
    context_settings: list[dict] = Field(default_factory=list)
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    max_trade: str = "OFF"
    visualization: bool = False
    max_turnover: float = .70
    max_corr: float = .70
    min_sharpe: float | None = None
    min_fitness: float | None = None
    randomize: bool = False
    range_from: int = 0
    range_to: int = 0


def _transfer_row(study_id:int):
    with SessionLocal() as db:
        row=db.get(M.TransferStudy,study_id)
        if not row or not row.active: raise HTTPException(404,"Active transfer study not found.")
        contexts=json.loads(row.contexts_json or "[]")
        if row.status!="ready" or not json.loads(row.selected_fields_json or "[]"):
            raise HTTPException(409,"Finish the shared-field comparison before simulation.")
        return row.instrument,contexts,set(json.loads(row.selected_fields_json or "[]"))


def _transfer_contexts(instrument:str, saved:list[dict], requested:list[dict], fallback_neuts:list[str]):
    """Expand each verified region/delay into the universes chosen on the Simulation screen."""
    choices={(str(x.get("region")),int(x.get("delay",1))):x for x in requested}
    expanded=[]
    for base in saved:
        region,delay=str(base["region"]),int(base["delay"])
        picked=choices.get((region,delay),{})
        universes=list(dict.fromkeys(str(x) for x in (picked.get("universes") or [base.get("universe")]) if x))
        neutralizations=list(dict.fromkeys(str(x) for x in (picked.get("neutralizations") or base.get("neutralizations") or fallback_neuts or ["INDUSTRY"]) if x))
        for universe in universes:
            valid_region,valid_delay,valid_universe=engine.valid_combo(instrument,region,delay,universe)
            if valid_region!=region or int(valid_delay)!=delay or valid_universe!=universe:
                raise HTTPException(400,f"{region} D{delay} does not offer universe {universe}.")
            expanded.append({"region":region,"delay":delay,"universe":universe,"neutralizations":neutralizations})
    return expanded


@router.post("/transfer/estimate")
def transfer_estimate(req:TransferSimReq):
    instrument,saved,_=_transfer_row(req.study_id)
    contexts=_transfer_contexts(instrument,saved,req.context_settings,req.neutralizations)
    expressions=list(dict.fromkeys(x.strip() for x in req.expressions if x.strip()))
    configurations=sum(max(1,len(x.get("neutralizations") or req.neutralizations or ["INDUSTRY"])) for x in contexts)
    return {"study_id":req.study_id,"expressions":len(expressions),"contexts":contexts,
            "context_configurations":configurations,
            "configurations":len(expressions)*configurations}


@router.post("/transfer/run")
def transfer_run(req:TransferSimReq):
    if not req.expressions: raise HTTPException(400,"No expressions to simulate.")
    instrument,saved_contexts,_allowed_fields=_transfer_row(req.study_id)
    contexts=_transfer_contexts(instrument,saved_contexts,req.context_settings,req.neutralizations)
    try: engine.require_session_window(600)
    except engine.SessionExpired as exc: raise HTTPException(409,str(exc))
    with SessionLocal() as db:
        for expression in dict.fromkeys(x.strip() for x in req.expressions if x.strip()):
            fp=hashlib.sha256(re.sub(r"\s+","",expression).lower().encode()).hexdigest()
            if not db.scalar(select(M.TransferCandidate).where(M.TransferCandidate.study_id==req.study_id,M.TransferCandidate.fingerprint==fp)):
                db.add(M.TransferCandidate(study_id=req.study_id,expression=expression,fingerprint=fp,origin="simulation"))
        study=db.get(M.TransferStudy,req.study_id); study.status="simulating"; study.updated_at=time.time(); db.commit()
    def task(progress,should_cancel):
        try:
            return service.run_transfer_study(study_id=req.study_id,expressions=req.expressions,contexts=contexts,
                instrument=instrument,neutralizations=req.neutralizations,decay=req.decay,truncation=req.truncation,
                test_period=req.test_period,concurrency=req.concurrency,limit_of_multi=req.limit_of_multi,
                tag=req.tag,winner_tag=req.winner_tag,check_submission=req.check_submission,
                pasteurization=req.pasteurization,unit_handling=req.unit_handling,nan_handling=req.nan_handling,
                max_trade=req.max_trade,visualization=req.visualization,max_turnover=req.max_turnover,
                max_corr=req.max_corr,min_sharpe=req.min_sharpe,min_fitness=req.min_fitness,
                randomize=req.randomize,range_from=req.range_from,range_to=req.range_to,
                progress=progress,should_cancel=should_cancel)
        finally:
            with SessionLocal() as db:
                study=db.get(M.TransferStudy,req.study_id)
                if study: study.status="ready"; study.updated_at=time.time(); db.commit()
    return {"job_id":jobs.submit("transfer-simulation",task)}


class SweepReq(BaseModel):
    expressions: list[str]
    regions: list[str]
    delay: int = 1
    instrument: str = "EQUITY"
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    concurrency: int = 3
    limit_of_multi: int = 10
    tag: str = ""
    winner_tag: str = ""
    home_region: str = ""   # where the alpha was built — ground truth for its datafields


@router.post("/sweep")
def sweep(req: SweepReq):
    """Cross-region auto-sweep: run the same expressions across several regions (per-region valid
    universe + gate)."""
    if not req.expressions:
        raise HTTPException(400, "No expressions to sweep.")
    if not req.regions:
        raise HTTPException(400, "Pick at least one region.")
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))

    def task(progress, should_cancel):
        return service.run_cross_region_sweep(
            expressions=req.expressions, regions=req.regions, delay=req.delay, instrument=req.instrument,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, tag=req.tag,
            winner_tag=req.winner_tag, home_region=req.home_region,
            progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("cross-region-sweep", task)}


class BatchReq(BaseModel):
    batches: list[dict]                 # [{label, expressions:[...]}]
    region: str = "USA"
    delay: int = 1
    universes: list[str] = ["TOP3000"]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    concurrency: int = 3
    limit_of_multi: int = 10
    min_sharpe: float | None = None
    min_fitness: float | None = None
    tag: str = ""
    winner_tag: str = ""


@router.post("/batch")
def batch(req: BatchReq):
    """Queue several expression batches to run sequentially, unattended, in one background job."""
    if not req.batches:
        raise HTTPException(400, "The queue is empty.")
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    t = service.gate_thresholds(req.delay)
    ms = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
    mf = req.min_fitness if req.min_fitness is not None else t["fitness"]

    def task(progress, should_cancel):
        return service.run_batch(
            batches=req.batches, region=req.region, delay=req.delay, universes=req.universes,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, min_sharpe=ms, min_fitness=mf,
            tag=req.tag, winner_tag=req.winner_tag, progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("batch-queue", task)}


@router.post("/walkforward")
def walkforward(req: SweepReq):
    """Walk-forward robustness: run the expressions at both delay 1 and delay 0 in one region."""
    if not req.expressions:
        raise HTTPException(400, "No expressions.")
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    region = req.regions[0] if req.regions else "USA"

    def task(progress, should_cancel):
        return service.run_walk_forward(
            expressions=req.expressions, region=region, instrument=req.instrument,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, tag=req.tag,
            winner_tag=req.winner_tag, progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("walk-forward", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str):
    return {"cancelled": jobs.cancel(jid)}


@router.get("/results")
def results(limit: int = 200):
    return service.list_results(limit)
