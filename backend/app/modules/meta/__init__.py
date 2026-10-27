"""Meta module — app-wide metadata the frontend shell needs at boot: the navigation
map (so the sidebar/header render from one source of truth) and build info.

The navigation map lives on the backend deliberately: as modules come online they can
contribute or gate screens, and deployment configuration can later hide or unlock
routes without a frontend change.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()

# groups the sidebar renders, in order. `ready=False` screens show a "coming soon"
# placeholder until their module lands in a later phase.
NAV = [
    {"group": "Overview", "items": [
        {"id": "command", "path": "/", "label": "System Overview", "icon": "grid", "ready": True},
    ]},
    {"group": "Research", "items": [
        {"id": "catalog", "path": "/catalog", "label": "Data Catalog", "icon": "catalog", "ready": True},
        {"id": "data", "path": "/data", "label": "Data Explorer", "icon": "database", "ready": True},
        # Strategy Lab absorbs the old Knowledge Graph + Strategy Atlas: semantic + AI per-field
        # hypotheses/operators, composed straight into Alpha Lab.
        {"id": "strategies", "path": "/strategies", "label": "Strategy Lab", "icon": "share", "ready": True},
        {"id": "prompts", "path": "/prompts", "label": "Prompt Library", "icon": "bookmark", "ready": True},
    ]},
    {"group": "Build", "items": [
        {"id": "templates", "path": "/templates", "label": "Template Studio", "icon": "layout", "ready": True},
        # Research Lab merged into this unified workspace: hypotheses/papers -> composed instruction
        # -> validated alphas, all in one screen.
        {"id": "generate", "path": "/generate", "label": "Alpha Lab", "icon": "flask", "ready": True},
    ]},
    {"group": "Run", "items": [
        {"id": "simulate", "path": "/simulate", "label": "Simulation", "icon": "play", "ready": True},
        {"id": "evolution", "path": "/evolution", "label": "Evolution Lab", "icon": "diff", "ready": True},
        {"id": "super", "path": "/super", "label": "SuperAlpha", "icon": "star", "ready": True},
    ]},
    {"group": "Analyse", "items": [
        {"id": "results", "path": "/results", "label": "Results & Analytics", "icon": "chart", "ready": True},
        {"id": "account", "path": "/account-research", "label": "Research Footprint", "icon": "footprint", "ready": True},
        {"id": "operators", "path": "/operators", "label": "Operator Atlas", "icon": "list", "ready": True},
        {"id": "regions", "path": "/regions", "label": "Transfer Lab", "icon": "globe", "ready": True},
    ]},
    # Settings lives on the header (gear icon) and Success is reached from there — neither clutters
    # the sidebar. Their routes still exist; they are just not sidebar entries.
]


@router.get("/nav")
def nav():
    return {"nav": NAV}


@router.get("/info")
def info():
    s = get_settings()
    return {"app": s.app_name, "version": s.version}


@router.get("/jobs")
def jobs_list(limit: int = 40):
    """All recent background jobs (running first) for the global job tray."""
    from app.core.jobs import jobs
    return {"jobs": jobs.list_recent(limit)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """One cancellation endpoint for the global header tray, regardless of job kind."""
    from app.core.jobs import jobs
    return {"cancelled": jobs.cancel(job_id)}
