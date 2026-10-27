"""Research module — the Research Lab's backend.

Runs LLM research as a background job (survives a refresh), ingests research papers
(paste or PDF upload with a page range), saves sessions, and pushes chosen ideas to a
saved prompt for later generation. Separate from generation by design.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.jobs import jobs
from app.research import service

PREFIX = "/api/research"
router = APIRouter()


@router.get("/providers")
def providers():
    return service.providers()


class RunReq(BaseModel):
    category: str = ""
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict = {}                 # field_id -> category (for two-category mode)
    goal: str = ""
    paper_text: str = ""
    paper_name: str = ""
    paper_is_community: bool = False      # WorldQuant community paper → stricter grounding
    n: int = 6
    # single (default) | multi_single_dataset | multi_two_categories — multi only when chosen
    mode: str = "single"
    max_operators: int = 6
    economic_review: bool = True          # drop hypotheses whose expression makes no economic sense
    general: bool = False                 # mechanism-first hypotheses that can be rebound to fields later


@router.post("/run")
def run(req: RunReq):
    if not req.fields and not req.general:
        raise HTTPException(400, "Select some datafields first so the research is grounded in your data.")
    if req.paper_is_community and req.paper_text and not req.fields:
        raise HTTPException(400, "For a WorldQuant community paper, first select the datasets/fields it "
                                 "suggests so the research maps to the right data.")

    def task(progress, should_cancel):
        out = service.run_research(
            category=req.category, region=req.region, delay=req.delay, instrument=req.instrument,
            dataset_names=req.dataset_names, fields=req.fields, categories=req.categories, goal=req.goal,
            paper_text=req.paper_text, paper_is_community=req.paper_is_community,
            mode=req.mode, max_operators=req.max_operators, n=req.n,
            economic_review=req.economic_review, general=req.general, progress=progress)
        sid = service.save_session({**req.model_dump(), **out})
        out["session_id"] = sid
        return out

    return {"job_id": jobs.submit("research", task)}


@router.post("/goal")
def goal(req: RunReq):
    """Auto-generate a research goal from the selected data — or from the uploaded paper if present."""
    try:
        g = service.suggest_goal(
            region=req.region, delay=req.delay, instrument=req.instrument,
            dataset_names=req.dataset_names, fields=req.fields, categories=req.categories,
            paper_text=req.paper_text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"goal": g}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@router.post("/paper")
async def paper(file: UploadFile = File(...), pages: str = Form("")):
    """Extract every PDF page by default; a range is an explicit user restriction."""
    data = await file.read()
    try:
        text = service.extract_pdf_text(data, pages)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read that PDF ({e}).")
    if not text:
        raise HTTPException(400, "No selectable text found in that PDF (is it a scan?).")
    return {"name": file.filename, "chars": len(text), "text": text,
            "complete": not bool(pages.strip())}


@router.get("/sessions")
def sessions(limit: int = 30):
    return {"sessions": service.list_sessions(limit)}


class HypothesisReq(BaseModel):
    hypothesis: dict
    context: dict = {}


@router.get("/hypotheses")
def hypotheses(limit: int = 100):
    return {"hypotheses": service.list_hypotheses(limit)}


@router.post("/hypotheses")
def save_hypothesis(req: HypothesisReq):
    return service.save_hypothesis(req.hypothesis, req.context)


@router.delete("/hypotheses/{hid}")
def delete_hypothesis(hid: int):
    if not service.delete_hypothesis(hid): raise HTTPException(404, "No such saved hypothesis")
    return {"ok": True}


@router.get("/alpha/{alpha_id}")
def alpha(alpha_id: str):
    try: return service.fetch_alpha(alpha_id)
    except Exception as e: raise HTTPException(400, str(e))


class ReplicateReq(BaseModel):
    alpha_id: str
    fields: list[dict] = []
    categories: dict = {}
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    goal: str = ""
    n: int = 6


@router.post("/replicate")
def replicate(req: ReplicateReq):
    if not req.fields: raise HTTPException(400, "Select target datafields first.")
    def task(progress, should_cancel):
        return service.replicate_alpha(**req.model_dump(), progress=progress)
    return {"job_id": jobs.submit("replicate-alpha", task)}


class PushReq(BaseModel):
    name: str = ""
    scope: str = "generate"      # generate | template
    category: str = ""
    region: str = ""
    body: str
    dataset_names: list[str] = []
    research_id: int = 0
    compose: bool = True         # AI-rewrite into a structured prompt with a nice name
    source: str = "research"     # research | strategy


@router.post("/push")
def push(req: PushReq):
    def task(progress, should_cancel):
        progress(message="naming and saving the prompt…")
        # Save the RAW notes as-is (no restructuring) with a nice name. The user can auto-rewrite
        # into a full master prompt on demand in Generation.
        name = (req.name or "").strip() or service.nice_name(req.body, req.category, req.region, req.source)
        saved = service.save_prompt(name=name, scope=req.scope, category=req.category,
                                    region=req.region, body=req.body, dataset_names=req.dataset_names,
                                    research_id=req.research_id)
        return {"ok": True, "prompt_id": saved["id"], "name": saved["name"], "duplicate": saved["duplicate"]}
    return {"job_id": jobs.submit("push-prompt", task)}


class CollectionReq(BaseModel):
    name: str
    theme: str = ""
    description: str = ""
    fields: list[dict] = []
    context: dict = {}


@router.get("/collections")
def collections(limit: int = 100):
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    import json
    with SessionLocal() as db:
        rows = db.scalars(select(M.ResearchCollection).order_by(M.ResearchCollection.updated_at.desc()).limit(limit)).all()
        return {"collections": [{"id": x.id, "name": x.name, "theme": x.theme, "description": x.description,
            "fields": json.loads(x.fields_json or "[]"), "context": json.loads(x.context_json or "{}")}
            for x in rows]}


@router.post("/collections")
def save_collection(req: CollectionReq):
    from app.db.base import SessionLocal
    from app.db import models as M
    import json
    if not req.name.strip() or not req.fields: raise HTTPException(400, "Name the collection and include fields.")
    with SessionLocal() as db:
        row = M.ResearchCollection(name=req.name.strip(), theme=req.theme.strip(),
            description=req.description.strip(), fields_json=json.dumps(req.fields), context_json=json.dumps(req.context))
        db.add(row); db.commit(); db.refresh(row)
        return {"id": row.id, "name": row.name}


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int):
    from app.db.base import SessionLocal
    from app.db import models as M
    with SessionLocal() as db:
        row = db.get(M.ResearchCollection, collection_id)
        if not row: raise HTTPException(404, "No such collection")
        db.delete(row); db.commit(); return {"ok": True}
