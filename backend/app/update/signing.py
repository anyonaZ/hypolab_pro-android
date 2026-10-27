"""Release-integrity verification for the in-app updater.

Byte-compatible with the manifest/signature that `package.py` produces (same iter_files /
build_manifest / canonical), but the trust anchor is the HARDCODED public key below — never the
`public_key.hex` shipped inside a downloaded release. That is the whole security property: this
running app only accepts an update whose MANIFEST.sig was made by the matching PRIVATE key, so
nobody but the release owner can push code onto an install.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# ACE Studio release-signing public key (hex). The matching private key lives only on the
# maintainer's machine (~/.ace_studio_keys). Changing this rejects every existing release.
TRUSTED_PUBKEY_HEX = "5fe777bd5cdd20e50ace71bd0b03fd947ab08e485895d3ee0cafd1928cb59b35"

# Kept identical to package.py so hashes/manifests line up exactly.
RUNTIME_DIRS = {"data", "__pycache__", ".venv", "node_modules", ".git", ".claude"}
RUNTIME_FILES = {"MANIFEST.json", "MANIFEST.sig", "session.pkl", "ace.log",
                 ".env", "requirements.lock", "update_url.txt", "update_token.txt"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(root: Path):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = Path(dirpath) == root
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not (at_root and d in RUNTIME_DIRS)]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if fn in RUNTIME_FILES or fn.endswith(".log"):
                continue
            yield rel, p


def build_manifest(root: Path) -> dict:
    return {rel: _sha256(p) for rel, p in sorted(iter_files(root))}


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def _verify_sig(sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(TRUSTED_PUBKEY_HEX)).verify(sig, msg)
        return True
    except Exception:
        return False


def verify_release(root: Path) -> tuple[bool, str]:
    """Return (ok, message). Rejects unless MANIFEST.sig verifies against the TRUSTED key AND
    every file matches the manifest AND the release's own public_key.hex equals the trusted key."""
    root = Path(root)
    mpath, spath, ppath = root / "MANIFEST.json", root / "MANIFEST.sig", root / "public_key.hex"
    if not (mpath.exists() and spath.exists() and ppath.exists()):
        return False, "release is not signed (missing manifest/signature/public key)"
    if ppath.read_text().strip().lower() != TRUSTED_PUBKEY_HEX.lower():
        return False, "release is signed by a DIFFERENT key — rejected"
    manifest = json.loads(mpath.read_text())
    try:
        sig = bytes.fromhex(spath.read_text().strip())
    except ValueError:
        return False, "malformed signature"
    if not _verify_sig(sig, canonical(manifest)):
        return False, "signature INVALID — this release was not produced by the trusted key"
    actual = build_manifest(root)
    manifest.pop("public_key.hex", None)
    actual.pop("public_key.hex", None)
    if actual != manifest:
        changed = sorted(set(actual) ^ set(manifest)) or \
            [k for k in manifest if actual.get(k) != manifest[k]]
        return False, f"file integrity FAILED — changed/added/removed: {', '.join(changed[:8])}"
    return True, "ok"
