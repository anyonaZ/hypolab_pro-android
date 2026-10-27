"""Generation module — deep single-field / ≤2-category multi-field LLM generation, static
validation, the operator list (for autocomplete + checks), and the Prompt Library."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.generation import service

PREFIX = "/api/generate"
router = APIRouter()


class GenReq(BaseModel):
    mode: str = "single"                 # single | multi
    prompt: str = ""
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict[str, str] = {}      # field_id -> category
    max_operators: int = 4
    n: int = 12
    repair_rounds: int = 2
    region_note: str = ""
    raw_prompt: bool = False              # send `prompt` to the LLM as-is (a master prompt)
    economic_review: bool = True          # LLM 'does this make economic sense?' gate before results
    economic_min: int = 3                 # keep expressions scored >= this (1-5)
    allowed_operators: list[str] = []     # if set, generation may use ONLY these operators


@router.post("/run")
def run(req: GenReq):
    if not req.fields:
        raise HTTPException(400, "Select datafields first — generation is grounded in your data.")

    def task(progress, should_cancel):
        return service.run_generation(
            mode=req.mode, prompt=req.prompt, region=req.region, delay=req.delay,
            instrument=req.instrument, universe=req.universe, dataset_names=req.dataset_names,
            fields=req.fields, categories=req.categories, max_operators=req.max_operators,
            n=req.n, repair_rounds=req.repair_rounds, region_note=req.region_note,
            raw_prompt=req.raw_prompt, economic_review=req.economic_review,
            economic_min=req.economic_min, allowed_operators=req.allowed_operators, progress=progress)

    return {"job_id": jobs.submit("generate", task)}


@router.post("/rewrite")
def rewrite(req: GenReq):
    """Auto-rewrite the current instruction into a long, self-contained MASTER prompt that
    re-provides the datasets, operators (with examples), datafields and all hypotheses/strategy."""
    def task(progress, should_cancel):
        progress(message="composing a master prompt…")
        master = service.rewrite_master_prompt(
            raw=req.prompt, region=req.region, delay=req.delay, instrument=req.instrument,
            universe=req.universe, dataset_names=req.dataset_names, fields=req.fields,
            categories=req.categories, max_operators=req.max_operators, n=req.n)
        return {"prompt": master}
    return {"job_id": jobs.submit("rewrite", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


class TemplatizeReq(BaseModel):
    expression: str


@router.post("/templatize")
def templatize(req: TemplatizeReq):
    """Turn a concrete expression into a {field}/{field2} template + its datasets, for reuse."""
    return service.templatize(req.expression)


class ValidateReq(BaseModel):
    expressions: list[str]
    fields: list[dict] = []
    max_operators: int = 4
    multi_field: bool = False


@router.post("/validate")
def validate(req: ValidateReq):
    return service.validate_expressions(req.expressions, req.fields, req.max_operators, req.multi_field)


class TemplateExpandReq(BaseModel):
    templates: list[str]
    field_ids: list[str]
    field2_ids: list[str] = []          # pool for {field2}; empty = same as field_ids
    fields: list[dict] = []
    vec_ops: list[str] = ["vec_avg", "vec_max", "vec_min", "vec_norm"]
    max_operators: int = 4
    multi_field: bool = False
    max_combos: int = 0          # 0 = all possible combinations (bounded by a safety ceiling)


@router.post("/templates/expand")
def templates_expand(req: TemplateExpandReq):
    return service.expand_templates(req.templates, req.field_ids, req.fields, req.vec_ops,
                                    req.max_operators, req.multi_field, req.max_combos,
                                    field2_ids=req.field2_ids)


class TemplateSuggestReq(BaseModel):
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict[str, str] = {}
    max_operators: int = 4
    n: int = 6
    multi_field: bool = False


@router.post("/templates/suggest")
def templates_suggest(req: TemplateSuggestReq):
    def task(progress, should_cancel):
        return service.suggest_templates(region=req.region, delay=req.delay, instrument=req.instrument,
                                         universe=req.universe, dataset_names=req.dataset_names,
                                         fields=req.fields, categories=req.categories,
                                         max_operators=req.max_operators, n=req.n,
                                         multi_field=req.multi_field, progress=progress)
    return {"job_id": jobs.submit("templates", task)}


@router.post("/fields/suggest")
def fields_suggest(req: TemplateSuggestReq):
    """LLM reads the field descriptions and proposes concrete field+template picks to select."""
    def task(progress, should_cancel):
        return service.suggest_field_picks(region=req.region, delay=req.delay, instrument=req.instrument,
                                           universe=req.universe, dataset_names=req.dataset_names,
                                           fields=req.fields, categories=req.categories,
                                           max_operators=req.max_operators, n=req.n,
                                           multi_field=req.multi_field, progress=progress)
    return {"job_id": jobs.submit("field-picks", task)}


class ExplainReq(BaseModel):
    expression: str
    fields: list[dict] = []
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"


@router.post("/explain")
def explain(req: ExplainReq):
    if not req.expression.strip():
        raise HTTPException(400, "No expression to explain.")
    return service.explain_expression(expression=req.expression, fields=req.fields,
                                      region=req.region, delay=req.delay, instrument=req.instrument)


@router.get("/operators")
def operators():
    try:
        ops = service.operators_list()
    except Exception as e:  # noqa: BLE001 — clean message if no session
        raise HTTPException(409, str(e))
    return {"count": len(ops), "operators": ops}


class TemplateAssetReq(BaseModel):
    name: str = ""
    template_text: str
    nugget: str = ""
    context: dict = {}


@router.get("/templates/saved")
def saved_templates(limit: int = 100): return {"templates": service.list_template_assets(limit)}

@router.get("/templates/saved/{asset_id}/revisions")
def saved_template_revisions(asset_id:int): return {"revisions":service.template_revisions(asset_id)}


@router.post("/templates/saved")
def save_template(req: TemplateAssetReq):
    try: return service.save_template_asset(req.model_dump())
    except ValueError as e: raise HTTPException(400, str(e))


@router.delete("/templates/saved/{asset_id}")
def delete_template(asset_id: int):
    if not service.delete_template_asset(asset_id): raise HTTPException(404, "No such template")
    return {"ok": True}


# ── prompt library ───────────────────────────────────────────────────────────────────

@router.get("/prompts")
def prompts(scope: str = ""):
    return {"prompts": service.list_prompts(scope)}


@router.post("/prompts/{pid}/delete")
def delete_prompt(pid: int):
    service.delete_prompt(pid)
    return {"ok": True}


class PromptRevisionReq(BaseModel):
    body: str
    protocol: dict = {}


@router.get("/prompts/{pid}/revisions")
def prompt_revisions(pid: int):
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    import json
    with SessionLocal() as db:
        rows = db.scalars(select(M.PromptRevision).where(M.PromptRevision.prompt_id == pid)
                          .order_by(M.PromptRevision.revision.desc())).all()
        return {"revisions": [{"id": x.id, "revision": x.revision, "created_at": x.created_at,
            "body": x.body, "protocol": json.loads(x.protocol_json or "{}"),
            "evidence": json.loads(x.evidence_json or "{}")} for x in rows]}


@router.post("/prompts/{pid}/revisions")
def save_prompt_revision(pid: int, req: PromptRevisionReq):
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select, func
    import json
    with SessionLocal() as db:
        prompt = db.get(M.Prompt, pid)
        if not prompt: raise HTTPException(404, "No such prompt")
        revision = int(db.scalar(select(func.max(M.PromptRevision.revision)).where(M.PromptRevision.prompt_id == pid)) or 0) + 1
        row = M.PromptRevision(prompt_id=pid, revision=revision, body=req.body,
                               protocol_json=json.dumps(req.protocol))
        prompt.body = req.body; db.add(row); db.commit()
        return {"id": row.id, "revision": revision}


@router.post("/prompts/clear")
def clear_prompts():
    return service.clear_prompts()


@router.get("/export")
def export_all():
    """Portable bundle of saved prompts + research sessions (for backup / sharing setups)."""
    from app.research import service as rs
    return rs.export_all()


class ImportReq(BaseModel):
    format: str = ""
    prompts: list[dict] = []
    research_sessions: list[dict] = []


@router.post("/import")
def import_all(req: ImportReq):
    from app.research import service as rs
    return rs.import_all(req.model_dump())
