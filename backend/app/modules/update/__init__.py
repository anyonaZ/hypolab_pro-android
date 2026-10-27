"""update — in-app update check.

Desktop (frozen) builds: /check reports whether a newer signed release exists; /apply downloads +
verifies it and swaps on relaunch. No-ops safely when running from source.

Android (HYPOLAB_ANDROID=1): /check looks for a newer APK on GitHub Releases and /apply returns its
download link for the UI to open, because an Android app cannot replace its own package.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

if os.environ.get("HYPOLAB_ANDROID") == "1":
    from app.update import android as service
else:
    from app.update import service  # needs `cryptography` for signature verification

PREFIX = "/api/update"
router = APIRouter()


@router.get("/check")
def check():
    return service.check()


@router.post("/apply")
def apply():
    return service.apply(progress=lambda **_: None)
