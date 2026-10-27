"""Local, opaque identity used only to seed deterministic research diversity."""
from __future__ import annotations
import hashlib
import uuid
from functools import lru_cache
from pathlib import Path
from app.core.config import get_settings

def _id_path() -> Path:
    return Path(get_settings().data_dir) / "installation_id.txt"

@lru_cache
def installation_id() -> str:
    path = _id_path()
    try:
        if path.exists():
            return str(uuid.UUID(path.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value

def device_id() -> str:
    """Opaque compatibility id used by existing research-diversity code."""
    return hashlib.sha256(installation_id().encode()).hexdigest()

def device_seed() -> int:
    return int(device_id()[:12], 16)
