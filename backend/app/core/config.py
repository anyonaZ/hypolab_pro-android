"""Central configuration for the ACE Studio v2 backend.

Everything is local-first: paths live under <repo>/data, which is created on first run
and holds the SQLite database, caches and saved prompts. No settings require
the network to start the app.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _asset_root() -> Path:
    """Where bundled READ-ONLY assets (frontend/dist) live.

    • Source / .pyc release: three levels up from this file — the repo (or release) root.
    • PyInstaller onedir: bundled data is unpacked under sys._MEIPASS (the app's _internal folder).
    • Nuitka standalone: assets are shipped next to the .exe.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)                                   # PyInstaller
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent               # Nuitka
    return Path(__file__).resolve().parents[3]                     # source


def _state_root() -> Path:
    """Where WRITABLE per-user state (data/, db, caches) lives — next to the .exe when frozen,
    never inside a read-only bundle."""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


_ASSET_ROOT = _asset_root()
_STATE_ROOT = _state_root()


class Settings(BaseSettings):
    """Runtime settings. Override any field with an ACE_ env var (e.g. ACE_PORT)."""

    model_config = SettingsConfigDict(env_prefix="ACE_", env_file=".env", extra="ignore")

    app_name: str = "HypoLab"
    # The Android build overrides this with the APK's versionName (ACE_VERSION).
    version: str = "1.0.0"

    host: str = "127.0.0.1"
    # v2 runs on 8766 so it never collides with the legacy studio/ app on 8765.
    port: int = 8766

    repo_root: Path = _ASSET_ROOT          # holds frontend/dist (read-only bundle when frozen)
    data_dir: Path = _STATE_ROOT / "data"  # writable per-user state, next to the .exe when frozen

    # Vite dev server origin, allowed through CORS while developing. In production the
    # built frontend is served by FastAPI itself, so CORS is a non-issue.
    frontend_dev_origin: str = "http://localhost:5173"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ace_studio_v2.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
