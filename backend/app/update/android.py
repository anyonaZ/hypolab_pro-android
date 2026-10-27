"""HypoLab Android updater.

The desktop updater swaps a signed .exe bundle in place. On Android an app cannot replace its own
APK, so this variant only *finds* a newer release on GitHub and hands its download link to the
UI, which opens it in the browser; Android's package installer then upgrades the app in place
(same signing key), keeping all local data.

Release source: the GitHub repository in HYPOLAB_UPDATE_REPO (owner/name). Private repositories
need a token with read access in <data_dir>/update_token.txt; without one the check reports that
updates must be fetched from the Releases page while signed in to GitHub.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from app.core.config import get_settings

DEFAULT_REPO = "anyonaZ/hypolab_pro-android"


def _repo() -> str:
    return os.environ.get("HYPOLAB_UPDATE_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO


def releases_page() -> str:
    return f"https://github.com/{_repo()}/releases"


def current_version() -> str:
    return get_settings().version


def _token() -> str:
    try:
        p = get_settings().data_dir / "update_token.txt"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def _ver_tuple(s: str) -> tuple:
    s = re.sub(r"[^0-9.]", "", (s or "").lstrip("vV"))
    parts = [int(x) for x in s.split(".") if x != ""]
    return tuple(parts) or (0,)


def _latest() -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{_repo()}/releases/latest",
                                 headers={"User-Agent": "hypolab-android-updater",
                                          "Accept": "application/vnd.github+json"})
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _apk_url(rel: dict) -> str:
    for a in rel.get("assets", []) or []:
        if str(a.get("name", "")).lower().endswith(".apk"):
            return str(a.get("browser_download_url") or "")
    return str(rel.get("html_url") or releases_page())


def check() -> dict:
    cur = current_version()
    base = {"frozen": True, "android": True, "current": cur, "available": False,
            "releases_url": releases_page()}
    try:
        rel = _latest()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return {**base, "error": "Automatic update checks need access to the release repository. "
                                     "Open the Releases page (signed in to GitHub) to get new versions."}
        return {**base, "error": f"Could not check for updates (HTTP {e.code})."}
    except Exception as e:  # noqa: BLE001
        return {**base, "error": f"Could not check for updates: {str(e).splitlines()[0][:160]}"}
    latest = str(rel.get("tag_name") or rel.get("name") or "").strip()
    return {**base, "latest": latest, "available": _ver_tuple(latest) > _ver_tuple(cur),
            "url": _apk_url(rel), "notes": (rel.get("body") or "")[:1000]}


def apply(progress=None) -> dict:
    info = check()
    url = info.get("url") or info.get("releases_url") or releases_page()
    return {"url": url, "open_external": True,
            "message": "Opening the download in your browser. Install the APK to update; your data is kept."}
