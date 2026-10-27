"""Session module — live BRAIN session status and a soft restart.

The engine never logs in from here (headless-safe); logging in stays a Settings action.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.brain import engine

PREFIX = "/api/session"
router = APIRouter()


@router.get("/status")
def status():
    state = engine.ui_session_status()
    if state.get("ok"):
        state.update(engine.cached_user_profile())
    if state.get("ok") and 0 < int(state.get("remaining") or 0) < 600:
        from app.core.jobs import jobs
        state["cancelled_jobs"] = jobs.cancel_kinds(
            {"simulate", "batch-queue", "cross-region-sweep", "walk-forward", "evolution-study"},
            "Stopped safely: BRAIN session has less than 10 minutes remaining. Log in again.")
        state["renew_required"] = True
    return state


@router.get("/profile")
def profile():
    return engine.user_profile()


@router.post("/restart")
def restart():
    return engine.session_restart()
