"""Settings module — API keys, LLM provider/model choice + live key test, and BRAIN login.

Reuses the studio key store (keys) and session manager (session_manager) via the brain
adapter, so v2 shares the exact same secrets file and session the user already has.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel

from app.brain import engine  # noqa: F401 — loads the vendored engine + keys into env

import keys as keymgr          # noqa: E402
import session_manager as sm   # noqa: E402
import llm_providers as L      # noqa: E402

PREFIX = "/api/settings"
router = APIRouter()


def _storage_groups():
    from app.db import models as M
    return {
        "results": [M.SimResult],
        "catalog": [M.CatalogSnapshot, M.CatalogSyncTask, M.CatalogSyncRun, M.FieldAvailability, M.FieldConcept,
                    M.DatasetAvailability, M.DatasetConcept, M.Field, M.Dataset],
        "research": [M.SavedHypothesis, M.ResearchSession, M.ResearchCollection],
        "prompts": [M.PromptRevision, M.Prompt],
        "templates": [M.TemplateRevision, M.TemplateAsset],
        "evolution": [M.EvolutionIndividual, M.EvolutionRun],
        "account_footprint": [M.AccountAlphaSnapshot, M.AccountResearchSync],
        "usage_and_audit": [M.ResearchAudit, M.Usage],
        "operator_edits": [M.OperatorRef],
    }


@router.get("/storage")
def storage_inventory():
    from app.db.base import SessionLocal
    from app.core.config import get_settings
    db_path=Path(get_settings().db_path)
    with SessionLocal() as db:
        return {"groups": {name: sum(db.query(model).count() for model in models)
                           for name, models in _storage_groups().items()},
                "database_bytes":db_path.stat().st_size if db_path.exists() else 0}


@router.get("/storage/backup")
def storage_backup():
    from app.core.config import get_settings
    path=Path(get_settings().db_path)
    if not path.exists(): raise HTTPException(404,"Local database does not exist.")
    return FileResponse(path,filename=f"hypolab-backup-{int(__import__('time').time())}.db")


_SQLITE_MAGIC = b"SQLite format 3\x00"
_KNOWN_TABLES = {"sim_results", "catalog_field_concepts", "catalog_dataset_concepts", "operator_ref",
                 "prompts", "template_assets", "saved_hypotheses", "usage"}


@router.post("/storage/restore")
def storage_restore(file: UploadFile = File(...)):
    """Replace the local database with an uploaded backup (e.g. data/ace_studio_v2.db copied from the
    desktop app, or a file from "Backup database"). The current database is kept as
    backups/before-restore.db so a bad restore can be undone by restoring that file."""
    import os
    import shutil
    import sqlite3
    import time
    from app.core.config import get_settings
    from app.core.jobs import jobs
    from app.db.base import engine as db_engine, init_db

    if any(j.get("status") == "running" for j in jobs.list_recent(200)):
        raise HTTPException(409, "Stop running background jobs before restoring a database.")
    settings = get_settings()
    db_path = Path(settings.db_path)
    incoming = db_path.with_name("restore-incoming.db")
    try:
        with open(incoming, "wb") as out:
            shutil.copyfileobj(file.file, out, length=4 * 1024 * 1024)
        with open(incoming, "rb") as fh:
            if fh.read(16) != _SQLITE_MAGIC:
                raise HTTPException(400, "That file is not a SQLite database.")
        con = sqlite3.connect(f"file:{incoming}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            check = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if not tables & _KNOWN_TABLES:
            raise HTTPException(400, "That database does not look like an ACE Studio / HypoLab database.")
        if str(check).lower() != "ok":
            raise HTTPException(400, f"The database failed its integrity check: {check}")
        backups = db_path.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        db_engine.dispose()
        if db_path.exists():
            shutil.copy2(db_path, backups / "before-restore.db")
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                os.remove(str(db_path) + suffix)
            except FileNotFoundError:
                pass
        os.replace(incoming, db_path)
        init_db()   # adds any tables/columns introduced since the backup was made
    finally:
        try:
            incoming.unlink(missing_ok=True)
        except Exception:
            pass
    return {"restored": True, "bytes": db_path.stat().st_size, "tables": len(tables),
            "previous_copy": "backups/before-restore.db", "at": int(time.time())}


@router.post("/storage/maintain")
def storage_maintain():
    from app.db.base import engine as db_engine
    with db_engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA optimize")
        conn.exec_driver_sql("VACUUM")
    return {"optimized":True}


@router.delete("/storage/{group}")
def storage_clear(group: str):
    from app.db.base import SessionLocal
    models = _storage_groups().get(group)
    if not models: raise HTTPException(404, "Unknown local data group.")
    with SessionLocal() as db:
        deleted = sum(int(db.query(model).delete() or 0) for model in models)
        db.commit()
    return {"group": group, "deleted": deleted}


# ── API keys ─────────────────────────────────────────────────────────────────────────

@router.get("/keys")
def get_keys():
    return {"status": keymgr.status()}


class KeysReq(BaseModel):
    updates: dict


@router.post("/keys")
def set_keys(req: KeysReq):
    return {"status": keymgr.set_keys(req.updates)}


@router.post("/keys/clear")
def clear_keys():
    return {"status": keymgr.set_keys({name: keymgr.CLEAR for name in keymgr.PROVIDERS})}


@router.post("/keys/{provider}/delete")
def delete_key(provider: str):
    """Delete a single provider's stored API key."""
    return {"status": keymgr.set_keys({provider: keymgr.CLEAR})}


# ── providers / models ───────────────────────────────────────────────────────────────

@router.get("/providers")
def providers():
    pref = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()], "preferred": pref,
            "used": [p.name for p in L.default_chain(pref)]}


class PreferReq(BaseModel):
    preferred: str = ""


@router.post("/providers")
def set_provider(req: PreferReq):
    keymgr.set_preferred(req.preferred)
    pref = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()], "preferred": pref,
            "used": [p.name for p in L.default_chain(pref)]}


@router.get("/providers/{provider}/models")
def provider_models(provider: str):
    if provider not in L.PROVIDER_KEY_ENV:
        raise HTTPException(404, f"unknown provider {provider!r}")
    return {"provider": provider, "models": L.list_models(provider),
            "current": L.provider_model(provider), "default": L.provider_default(provider)}


class ModelReq(BaseModel):
    model: str = ""


@router.post("/providers/{provider}/model")
def set_provider_model(provider: str, req: ModelReq):
    if provider not in L.PROVIDER_KEY_ENV:
        raise HTTPException(404, f"unknown provider {provider!r}")
    keymgr.set_provider_model(provider, req.model)
    return {"provider": provider, "current": L.provider_model(provider)}


@router.post("/providers/{provider}/test")
def provider_test(provider: str):
    return L.test_provider(provider)


# ── BRAIN login (biometrics, two steps) ──────────────────────────────────────────────

class LoginReq(BaseModel):
    email: str | None = None
    password: str | None = None
    force: bool = False


@router.get("/login/status")
def login_status():
    return {"credentials_saved": sm.credentials_available()}


@router.post("/login/begin")
def login_begin(req: LoginReq):
    # Guard against the classic footgun: re-logging in gives you a NEW BRAIN session and CANCELS the
    # current one, so any running simulation instantly starts failing with 401 "Incorrect
    # authentication credentials". Refuse while a task is running unless the caller forces it.
    if not req.force:
        from app.core.jobs import jobs
        running = [j for j in jobs.list_recent(200) if j.get("status") == "running"]
        if running:
            kinds = ", ".join(sorted({str(j.get("kind", "task")) for j in running}))
            return {"error": f"A background task is running ({len(running)}: {kinds}). Re-logging in now "
                             "starts a fresh BRAIN session and cancels this one, so the running task would "
                             "fail with authentication errors. Wait for it to finish (or stop it), then log "
                             "in. Use “Log in anyway” only if you accept losing the running task.",
                    "job_running": len(running)}
    result = sm.begin_login(req.email, req.password)
    if result.get("state") == "done":
        engine.accept_authenticated_session(result.get("remaining", 0))
    return result


@router.post("/login/complete")
def login_complete():
    result = sm.complete_login()
    if result.get("state") == "done":
        engine.accept_authenticated_session(result.get("remaining", 0))
    return result
