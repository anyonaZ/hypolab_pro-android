from __future__ import annotations

import secrets
import hashlib
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.core.jobs import jobs
from app.evolution import service
from app import __version__
from app.brain import engine

PREFIX = "/api/evolution"
router = APIRouter()

class StudyReq(BaseModel):
    expressions: list[str]
    fields: list[dict]
    categories: dict[str, str] = Field(default_factory=dict)
    scope: str = "selected"
    field_mode: str = "multiple"
    primary_field_id: str = ""
    mutation_methods: list[str] = Field(default_factory=lambda: list(service.MUTATION_METHODS))
    lookbacks: list[int] = Field(default_factory=lambda: list(service.WINDOWS))
    preset: str = "balanced"
    candidate_count: int = Field(30, ge=4, le=400)
    hypothesis: str = ""
    instrument: str = "EQUITY"
    region: str = "USA"
    delay: int = 1
    universe: str = "TOP3000"
    universes: list[str] = Field(default_factory=list)
    neutralizations: list[str] = Field(default_factory=lambda: ["INDUSTRY"])
    decay: int = Field(4, ge=0, le=100)
    truncation: float = Field(.08, ge=0, le=1)
    correlation_checks: bool = False
    final_walk_forward: bool = False
    random_seed: int | None = None


def _protocol(req: StudyReq) -> dict:
    if not req.expressions: raise HTTPException(400, "Add at least one seed expression.")
    if not req.fields: raise HTTPException(400, "Select fields in Data Explorer first.")
    # Evolution is intentionally self-configuring. Legacy UI fields remain accepted
    # for saved bundles, but the engine chooses the search depth, mutation grammar,
    # field cardinality and settings space from the actual research context.
    preset = "quick" if req.candidate_count <= 20 else "balanced" if req.candidate_count <= 100 else "robust"
    lookbacks = list(service.WINDOWS)
    p = req.model_dump(); p["preset"] = preset; expanded = dict(service.PRESETS[preset])
    unis = req.universes or [req.universe]
    neuts = req.neutralizations or ["INDUSTRY"]
    if not unis or not neuts: raise HTTPException(400, "Select at least one universe and neutralization.")
    configs = len(unis) * len(neuts)
    robustness_limit = 0 if preset == "quick" else 2 if preset == "balanced" else 4
    promoted = 0 if preset == "quick" else (
        max(expanded["elite_size"], (req.candidate_count + (2 if preset == "balanced" else 1)) //
            (3 if preset == "balanced" else 2)) if configs > 1 else 0)
    max_evaluations = req.candidate_count + min(req.candidate_count, promoted) * min(max(0, configs - 1), robustness_limit)
    mutations = list(service.MUTATION_METHODS)
    resolved_fields = list(req.fields)
    field_ids = {str(f.get("id")) for f in resolved_fields if f.get("id")}
    seed_widths = [len(set(service.gen._fields_in(expr, field_ids))) for expr in req.expressions]
    max_fields = 2 if any(width >= 2 for width in seed_widths) else 1
    p.update(expanded)
    p["fields"] = resolved_fields
    p["scope"] = "selected"
    p["field_mode"] = "multiple" if max_fields == 2 else "single"
    p["primary_field_id"] = ""
    p["categories"] = {**req.categories,
        **{f["id"]: f.get("category", "") for f in resolved_fields if f.get("id")}}
    atlas = service.operator_atlas_catalog()
    p.update({"universes": unis, "neutralizations": neuts,
        "max_evaluations": max_evaluations,
        "robustness_config_limit": robustness_limit,
        "catalog_policy": "selected",
        "max_catalog_fields": req.candidate_count, "economic_review": False,
        "concurrency": 3, "baselines": [], "direction": {"allowed_mutations": mutations,
            "operator_atlas": atlas, "windows": lookbacks or service.WINDOWS,
            "max_fields_per_expression": max_fields,
            "evolve_settings": True,
            "decay_values": sorted(set([0, 1, 2, 4, 8, 12, 20, req.decay])),
            "truncation_values": sorted(set([.01, .03, .05, .08, .10, req.truncation]))},
        "random_seed": req.random_seed if req.random_seed is not None else secrets.randbelow(2**31),
        "specification": {"application_version": __version__, "created_at": time.time(),
            "mutation_grammar": service.MUTATION_GRAMMAR_VERSION,
            "operator_reference_hash": hashlib.sha256(json.dumps(atlas, sort_keys=True).encode("utf-8")).hexdigest(),
            "operator_count": len(atlas)}})
    return p

@router.post("/preview")
def preview(req: StudyReq): return service.preview(_protocol(req))

def _submit(run_id: int, protocol: dict):
    def task(progress, should_cancel):
        return service.run_evolution(run_id=run_id, protocol=protocol, progress=progress, should_cancel=should_cancel)
    return jobs.submit("evolution-study", task)

@router.post("/runs")
def create(req: StudyReq):
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    protocol = _protocol(req); report = service.preview(protocol)
    if not report["valid_seeds"]: raise HTTPException(400, "None of the seeds passed validation.")
    protocol["expressions"] = report["valid_seeds"]
    protocol["planned_candidates"] = report["generated_candidates"]
    run_id = service.create_run(protocol)
    return {"run_id": run_id, "job_id": _submit(run_id, protocol), "preview": report}

@router.get("/jobs/{job_id}")
def job(job_id: str):
    out = jobs.get(job_id)
    if not out: raise HTTPException(404, "No such evolution job.")
    return out

@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, job_id: str): return {"cancelled": jobs.cancel(job_id), "run_id": run_id}

@router.post("/runs/{run_id}/resume")
def resume(run_id: int):
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    row = service.get_run(run_id)
    if not row: raise HTTPException(404, "No such evolution run.")
    if row["status"] == "done": return {"run_id": run_id, "status": "done"}
    return {"run_id": run_id, "job_id": _submit(run_id, row["protocol"])}

@router.post("/runs/{run_id}/reproduce")
def reproduce(run_id: int):
    try: engine.require_session_window(600)
    except engine.SessionExpired as e: raise HTTPException(409, str(e))
    row = service.get_run(run_id)
    if not row: raise HTTPException(404, "No such evolution run.")
    protocol = row["protocol"]; new_id = service.create_run(protocol)
    return {"run_id": new_id, "job_id": _submit(new_id, protocol)}

@router.get("/runs")
def runs(limit: int = 30): return {"runs": service.list_runs(limit)}

@router.get("/runs/{run_id}")
def detail(run_id: int):
    out = service.get_run(run_id)
    if not out: raise HTTPException(404, "No such evolution run.")
    return out
