"""Support module — an in-app issue reporter that forwards to a collector the maintainer hosts.

No secret is baked into the build: the collector URL lives in `support_url.txt` (and an optional
shared nonce in `support_token.txt`) placed next to the exe (frozen) or at the repo root (source).
Until that file exists the reporter reports itself as unconfigured, and the UI offers a copy-report
fallback. The backend attaches app version / OS / (optional) recent log tail so reports are useful.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

PREFIX = "/api/support"
router = APIRouter()


def _cfg(name: str) -> str:
    """Read a config file from: next to the exe (frozen), the data dir, or the repo root (source)."""
    cands: list[Path] = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / name)
    try:
        cands.append(Path(get_settings().data_dir) / name)
    except Exception:
        pass
    cands.append(Path(__file__).resolve().parents[4] / name)   # backend/app/modules/support -> repo root
    for c in cands:
        try:
            if c.exists():
                return c.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _version() -> str:
    try:
        from app.update.service import current_version
        return current_version()
    except Exception:
        try:
            return get_settings().version
        except Exception:
            return "0.0.0"


def _log_tail(limit: int = 6000) -> str:
    bases: list[Path] = []
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    try:
        bases.append(Path(get_settings().data_dir))
    except Exception:
        pass
    for base in bases:
        for name in ("ace.log", "ace_studio.log"):
            try:
                p = base / name
                if p.exists():
                    return p.read_text(encoding="utf-8", errors="replace")[-limit:]
            except Exception:
                pass
    return ""


@router.get("/status")
def status():
    """Whether an issue-reporting collector is configured (so the UI can adapt)."""
    return {"configured": bool(_cfg("support_url.txt"))}


class ReportReq(BaseModel):
    category: str = "bug"          # bug | idea | question
    message: str
    email: str = ""
    include_logs: bool = False


@router.post("/report")
def report(req: ReportReq):
    if not (req.message or "").strip():
        return {"ok": False, "error": "Write a short description first."}
    url = _cfg("support_url.txt")
    if not url:
        return {"ok": False, "configured": False,
                "error": "The issue reporter isn't set up yet — copy your report and send it to the maintainer."}
    token = _cfg("support_token.txt")
    payload = {
        "category": req.category, "message": req.message.strip(), "email": req.email.strip(),
        "app": getattr(get_settings(), "app_name", "ACE Studio"), "version": _version(),
        "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
    }
    if req.include_logs:
        tail = _log_tail()
        if tail:
            payload["logs"] = tail
    headers = {"User-Agent": "ace-studio-support", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if 200 <= r.status_code < 300:
            return {"ok": True, "configured": True}
        return {"ok": False, "configured": True, "error": f"The collector returned HTTP {r.status_code}."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "configured": True, "error": str(e).splitlines()[0][:160]}
