"""In-app updater for the packaged (frozen) build.

check()  — ask the GitHub 'latest release' API for the newest version and compare to the version
           baked into this build.
apply()  — download that release's signed zip, VERIFY its signature against the trusted key,
           stage it, then hand off to a tiny swapper that waits for this exe to exit, replaces the
           app files (preserving user state in data/), and relaunches. Windows can't overwrite a
           running .exe, hence the swap-on-relaunch.

Only meaningful in a frozen build; in dev it reports that updates apply to the packaged app only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import signing

DEFAULT_UPDATE_URL = "https://api.github.com/repos/ngari-qds/ace-studio/releases/latest"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def app_dir() -> Path | None:
    """The folder holding the .exe (and version.txt, data/, update_url.txt). None in dev."""
    return Path(sys.executable).resolve().parent if is_frozen() else None


def current_version() -> str:
    d = app_dir()
    if d and (d / "version.txt").exists():
        return (d / "version.txt").read_text(encoding="utf-8").strip()
    try:
        from app.core.config import get_settings
        return get_settings().version
    except Exception:
        return "0.0.0"


def _cfg_text(name: str, default: str = "") -> str:
    d = app_dir()
    if d and (d / name).exists():
        return (d / name).read_text(encoding="utf-8").strip()
    return default


def _ver_tuple(s: str) -> tuple:
    s = re.sub(r"[^0-9.]", "", (s or "").lstrip("vV"))
    parts = [int(x) for x in s.split(".") if x != ""]
    return tuple(parts) or (0,)


def _open(url: str, token: str, accept: str = ""):
    req = urllib.request.Request(url, headers={"User-Agent": "ace-studio-updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if accept:
        req.add_header("Accept", accept)
    return urllib.request.urlopen(req, timeout=60)


def _update_api_url() -> str:
    """The GitHub 'releases' API URL to query. Ignores a stale source/zipball pointer (used by the
    older updater) so a wrong update_url.txt can't break the check — that returns a binary zip, not
    JSON. Only a URL that actually targets the releases API is honoured; otherwise the default."""
    u = _cfg_text("update_url.txt", "")
    return u if "/releases" in u else DEFAULT_UPDATE_URL


def _latest(url: str, token: str) -> dict:
    import json
    with _open(url, token, "application/vnd.github+json") as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("update source did not return release info (is the update URL the "
                           "GitHub releases API?)") from exc


def _asset_url(rel: dict) -> str | None:
    assets = [a for a in rel.get("assets", []) if str(a.get("name", "")).lower().endswith(".zip")]
    # prefer a platform-matching asset when several are published (win/mac/linux)
    plat = {"win32": "win", "darwin": "mac", "linux": "linux"}.get(sys.platform, "")
    for a in assets:
        if plat and plat in str(a.get("name", "")).lower():
            return a["url"]
    return assets[0]["url"] if assets else None


def check() -> dict:
    cur = current_version()
    if not is_frozen():
        return {"frozen": False, "current": cur, "available": False,
                "message": "Updates apply to the installed app; you're running from source."}
    url = _update_api_url()
    tok = _cfg_text("update_token.txt", "")
    try:
        rel = _latest(url, tok)
    except Exception as e:  # noqa: BLE001
        return {"frozen": True, "current": cur, "available": False,
                "error": f"Could not check for updates: {str(e).splitlines()[0][:160]}"}
    latest = str(rel.get("tag_name") or rel.get("name") or "").strip()
    available = _ver_tuple(latest) > _ver_tuple(cur)
    return {"frozen": True, "current": cur, "latest": latest, "available": available,
            "notes": (rel.get("body") or "")[:1000]}


def apply(progress) -> dict:
    if not is_frozen():
        return {"error": "Updates apply to the installed app; you're running from source."}
    d = app_dir()
    url = _update_api_url()
    tok = _cfg_text("update_token.txt", "")
    tmp = Path(tempfile.mkdtemp(prefix="ace_upd_"))
    try:
        progress(message="finding the latest release…")
        rel = _latest(url, tok)
        latest = str(rel.get("tag_name") or "").strip()
        if _ver_tuple(latest) <= _ver_tuple(current_version()):
            return {"error": "You're already on the latest version."}
        asset = _asset_url(rel)
        if not asset:
            return {"error": "The latest release has no downloadable build."}

        progress(message=f"downloading {latest}…")
        zp = tmp / "release.zip"
        with _open(asset, tok, "application/octet-stream") as r, open(zp, "wb") as f:
            shutil.copyfileobj(r, f)

        progress(message="verifying signature…")
        with zipfile.ZipFile(zp) as z:
            z.extractall(tmp / "x")
        roots = [p.parent for p in (tmp / "x").rglob("MANIFEST.json")]
        if not roots:
            return {"error": "Downloaded build is not signed — rejected."}
        newroot = roots[0]
        ok, msg = signing.verify_release(newroot)
        if not ok:
            return {"error": f"Update rejected: {msg}"}

        progress(message="staging update…")
        staging = d / ".update_staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.move(str(newroot), str(staging))

        swapper = _write_swapper(d)
        progress(message="restarting to apply…")
        _launch_detached(swapper, d)
        # Give the response a beat to reach the browser, then exit so files unlock.
        threading.Thread(target=_delayed_exit, daemon=True).start()
        return {"ok": True, "version": latest,
                "message": f"Updating to {latest}. The app will close and reopen automatically."}
    except Exception as e:  # noqa: BLE001
        return {"error": f"Update failed: {str(e).splitlines()[0][:180]}"}
    finally:
        # tmp is best-effort; the extracted tree was moved out on success.
        pass


def _delayed_exit() -> None:
    time.sleep(1.5)
    os._exit(0)


def _write_swapper(d: Path) -> Path:
    """Emit the platform swapper that waits for this exe to quit, copies the staged build over the
    live folder (keeping data/), deletes staging, and relaunches."""
    exe = Path(sys.executable).name
    if os.name == "nt":
        bat = d / "apply_update.bat"
        bat.write_text(
            "@echo off\r\n"
            'cd /d "%~dp0"\r\n'
            'set "LOG=%~dp0apply_update.log"\r\n'
            f'echo [%date% %time%] swapper started, waiting for {exe} to exit... > "%LOG%"\r\n'
            ":waitloop\r\n"
            f'tasklist /FI "IMAGENAME eq {exe}" 2>nul | find /I "{exe}" >nul\r\n'
            "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto waitloop )\r\n"
            'echo [%date% %time%] app exited; copying new build over the folder... >> "%LOG%"\r\n'
            'robocopy ".update_staging" "." /E /XD "data" ".update_staging" >> "%LOG%" 2>&1\r\n'
            'echo [%date% %time%] robocopy finished (code %errorlevel%); cleaning up + relaunching >> "%LOG%"\r\n'
            'rmdir /s /q ".update_staging" 2>nul\r\n'
            f'start "" "{exe}"\r\n'
            'echo [%date% %time%] relaunched. >> "%LOG%"\r\n'
            '(goto) 2>nul & del "%~f0"\r\n',
            encoding="utf-8", newline="")
        return bat
    sh = d / "apply_update.sh"
    sh.write_text(
        "#!/usr/bin/env bash\n"
        'cd "$(dirname "$0")"\n'
        f'while pgrep -x "{exe}" >/dev/null 2>&1; do sleep 1; done\n'
        'cp -R .update_staging/. . 2>/dev/null\n'
        'rm -rf .update_staging\n'
        f'nohup "./{exe}" >/dev/null 2>&1 &\n'
        'rm -- "$0"\n',
        encoding="utf-8", newline="\n")
    os.chmod(sh, 0o755)
    return sh


def _launch_detached(script: Path, cwd: Path) -> None:
    """Launch the swapper so it OUTLIVES this app exiting. On Windows a DETACHED_PROCESS child can
    be killed with the parent (job object), and it has no console for timeout/start — so use
    ShellExecute (os.startfile), which runs the .bat as a fully independent process with its own
    console. Falls back to a break-away detached cmd."""
    if os.name == "nt":
        try:
            os.startfile(str(script))  # noqa: S606 — ShellExecute, independent of this process
            return
        except Exception:
            pass
        # BREAKAWAY_FROM_JOB so a job object can't take the child down with us.
        flags = 0x00000008 | 0x00000200 | 0x01000000
        try:
            subprocess.Popen(["cmd", "/c", str(script)], cwd=str(cwd), close_fds=True, creationflags=flags)
        except Exception:
            subprocess.Popen(["cmd", "/c", str(script)], cwd=str(cwd), close_fds=True)
    else:
        subprocess.Popen(["bash", str(script)], cwd=str(cwd), close_fds=True, start_new_session=True)
