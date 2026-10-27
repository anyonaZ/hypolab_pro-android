"""Brain engine adapter.

Reuses the battle-tested engine that already lives in `studio/` (ace_lib,
session_manager, wqb_data, keys) rather than duplicating ~2000 lines, and rebuilds only
the thin caching / session / retry layer the v2 HTTP modules need. The shared session
lives in `<repo>/session.pkl`, so v2 uses whatever session the user already has.

Everything here is import-safe: no network call happens at import time, and the engine
never triggers an interactive login (a headless server must fail cleanly instead).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import json
from pathlib import Path

from app.core.config import get_settings

_settings = get_settings()
# The engine (ace_lib, session_manager, wqb_data, keys, llm_providers, wqb_llm, validator,
# …) is VENDORED under backend/vendor so v2 is fully self-contained and no longer needs the
# studio/ folder. engine.py is at backend/app/brain/engine.py → parents[2] == backend.
_VENDOR = Path(__file__).resolve().parents[2] / "vendor"

# Persist the BRAIN session in the user's home secrets dir (like the API keys) so it survives
# app updates / folder swaps and is NEVER inside the read-only frozen bundle. Set this BEFORE
# importing session_manager (it reads the path at import time). Migrate a session from a legacy
# location (old repo/exe folder or cwd) on first run so nobody is forced to re-log-in.
_SECRETS = Path(os.path.expanduser("~")) / "secrets"
try:
    _SECRETS.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_SESSION = _SECRETS / "session.pkl"
if not (_SESSION.exists() and _SESSION.stat().st_size > 0):
    for _legacy in (_settings.repo_root / "session.pkl", Path("session.pkl").resolve()):
        try:
            if _legacy != _SESSION and _legacy.exists() and _legacy.stat().st_size > 0:
                import shutil as _shutil
                _shutil.copy2(_legacy, _SESSION)
                break
        except Exception:
            pass
os.environ.setdefault("ACE_SESSION_FILE", str(_SESSION))
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import ace_lib as ace            # noqa: E402
import session_manager as sm     # noqa: E402
import wqb_data                  # noqa: E402
import keys as keymgr            # noqa: E402


def _no_interactive_login(*_a, **_k):
    raise RuntimeError("BRAIN session expired. Log in again from Settings, then retry.")


# In a headless server we must never fall into ace.start_session()'s input()/biometrics.
ace.start_session = _no_interactive_login
keymgr.load_into_env()


class SessionExpired(Exception):
    """Raised when an operation needs a session and none is valid."""


_lock = threading.Lock()
_cache: dict = {"operators": None, "fields": {}, "options": None}
_OPTIONS_FILE = _settings.data_dir / "wqb_options.json"
_UI_SESSION_FILE = _SECRETS / "session-status.json"
_PROFILE_FILE = _SECRETS / "brain-profile.json"


def _load_ui_session_cache() -> dict:
    """Load non-secret, last-verified timing data so restarts never begin at zero."""
    try:
        saved = json.loads(_UI_SESSION_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("state") in {"valid", "unknown", "expired"}:
            return saved
    except Exception:
        pass
    return {}


def _save_ui_session_cache(state: dict) -> None:
    """Persist only status/expiry metadata; the authenticated session remains in session.pkl."""
    try:
        payload = {k: v for k, v in state.items() if k in {
            "ok", "state", "remaining", "remaining_human", "status", "_cached_at"
        }}
        tmp = _UI_SESSION_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, _UI_SESSION_FILE)
    except Exception:
        pass


_ui_session_cache: dict = _load_ui_session_cache()
_ui_session_refreshing = False


# ── session ──────────────────────────────────────────────────────────────────────────

def session_status() -> dict:
    """Lenient status for the UI: {ok, state, remaining, remaining_human}."""
    u = sm.ui_session()
    return {**u, "status": sm.status_line(),
            "remaining_human": sm.format_time(u.get("remaining", 0))}


def user_profile() -> dict:
    """Return the authenticated account identity without exposing credentials."""
    try:
        cached = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
        session_mtime = _SESSION.stat().st_mtime
        if cached.get("session_mtime") == session_mtime and cached.get("user_id"):
            return {"user_id": cached["user_id"], "display_name": cached.get("display_name", "")}
    except Exception:
        pass
    s = require_session()
    response = s.get(f"{ace.brain_api_url}/users/self", timeout=15)
    response.raise_for_status()
    raw = response.json() if response.content else {}
    profile = {
        "user_id": str(raw.get("id") or raw.get("userId") or raw.get("username") or ""),
        "display_name": str(raw.get("name") or raw.get("displayName") or ""),
    }
    try:
        _PROFILE_FILE.write_text(json.dumps({**profile, "session_mtime": _SESSION.stat().st_mtime}), encoding="utf-8")
    except Exception:
        pass
    return profile


def cached_user_profile() -> dict:
    """Read identity metadata only when it belongs to the current session file."""
    try:
        cached = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
        if cached.get("session_mtime") == _SESSION.stat().st_mtime:
            return {"user_id": cached.get("user_id", ""), "display_name": cached.get("display_name", "")}
    except Exception:
        pass
    return {}


def ui_session_status() -> dict:
    """Return instantly during startup when a persisted session exists.

    The network validation runs once in a daemon thread and replaces the optimistic
    state. This keeps the shell responsive when BRAIN's authentication endpoint is slow.
    """
    global _ui_session_refreshing
    now = time.time()
    # The non-secret timing cache must never manufacture a session after session.pkl was removed.
    if not (_SESSION.exists() and _SESSION.stat().st_size > 0):
        _ui_session_cache.clear()
        return {"ok": False, "state": "none", "remaining": 0,
                "remaining_human": "", "status": "Session: none (login required)"}
    stale_answer = None
    if _ui_session_cache:
        answer = dict(_ui_session_cache)
        age = max(0, int(now - float(answer.pop("_cached_at", now))))
        if answer.get("state") == "valid":
            answer["remaining"] = max(0, int(answer.get("remaining") or 0) - age)
            answer["remaining_human"] = sm.format_time(answer["remaining"])
        if age < 30:
            return answer
        stale_answer = answer
    optimistic = {"ok": True, "state": "unknown", "remaining": 0,
                  "remaining_human": "checking…", "status": "Session: checking in background"}
    if not _ui_session_refreshing:
        _ui_session_refreshing = True
        def refresh_status():
            global _ui_session_refreshing
            try:
                fresh = session_status()
                _ui_session_cache.clear()
                _ui_session_cache.update(fresh, _cached_at=time.time())
                _save_ui_session_cache(_ui_session_cache)
            finally:
                _ui_session_refreshing = False
        threading.Thread(target=refresh_status, daemon=True).start()
    return stale_answer or optimistic


def accept_authenticated_session(remaining: int) -> None:
    """Replace the engine-level UI cache immediately after a successful login."""
    remaining = max(0, int(remaining or 0))
    fresh = {"ok": True, "state": "valid", "remaining": remaining,
             "remaining_human": sm.format_time(remaining),
             "status": f"Session: valid, {sm.format_time(remaining)} remaining",
             "_cached_at": time.time()}
    _ui_session_cache.clear(); _ui_session_cache.update(fresh)
    _save_ui_session_cache(fresh)


def session_restart() -> dict:
    with _lock:
        _cache["operators"] = None
        _cache["fields"].clear()
        _cache["options"] = None
        _ui_session_cache.clear()
        try:
            _UI_SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return sm.soft_reset()


def session_or_none():
    try:
        return sm.get_session(login=False)
    except sm.SessionExpired:
        return None


def require_session():
    s = session_or_none()
    if s is None:
        raise SessionExpired("No active BRAIN session. Log in from Settings, then retry.")
    return s


def require_session_window(min_seconds: int = 600):
    """Refuse long BRAIN work when too little authenticated time remains."""
    state = session_status()
    remaining = int(state.get("remaining") or 0)
    if not state.get("ok") or remaining < min_seconds:
        raise SessionExpired(
            f"Your BRAIN session has {state.get('remaining_human') or 'less than 10 minutes'} remaining. "
            "Log in again before starting a simulation batch."
        )
    return require_session()


# ── retry wrapper ────────────────────────────────────────────────────────────────────

def brain_call(what: str, fn, *args, tries: int = 3, **kwargs):
    """Call a BRAIN data function with a few retries; surface a clean message on failure
    instead of a raw pandas/requests traceback."""
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries - 1:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Could not load {what} from BRAIN — it returned a transient error "
                       f"({last}). Please try again in a moment.")


# ── options (instrument / region / delay / universe / neutralization) ────────────────

def get_options(refresh: bool = False) -> dict:
    if not refresh and _cache["options"] is not None:
        return _cache["options"]
    if not refresh and _OPTIONS_FILE.exists():
        try:
            import json
            _cache["options"] = json.loads(_OPTIONS_FILE.read_text())
            return _cache["options"]
        except Exception:
            pass
    s = require_session()
    df = brain_call("options", ace.get_instrument_type_region_delay, s)
    records = []
    for _, r in df.iterrows():
        records.append({
            "instrument": str(r["InstrumentType"]),
            "region": str(r["Region"]),
            "delay": int(r["Delay"]),
            "universes": [str(u) for u in (r["Universe"] or [])],
            "neutralizations": [str(n) for n in (r["Neutralization"] or [])],
        })
    data = {"records": records}
    try:
        import json
        _OPTIONS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass
    _cache["options"] = data
    return data


def valid_combo(instrument_type: str, region: str, delay, universe: str):
    """Correct (delay, universe) to a combination that actually exists for this instrument+region,
    using the fetched options. So a fetch works even when the selected universe/delay isn't valid
    for the region (e.g. JPN doesn't have TOP3000) — we snap to a real one instead of erroring."""
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = 1
    try:
        recs = [r for r in get_options().get("records", [])
                if r["instrument"] == instrument_type and r["region"] == region]
    except Exception:
        return region, delay, universe
    if not recs:
        return region, delay, universe            # unknown region — let the call try as-is
    delays = sorted({r["delay"] for r in recs})
    if delay not in delays:
        delay = delays[0]
    universes = [u for r in recs if r["delay"] == delay for u in r["universes"]]
    if universe not in universes and universes:
        universe = universes[0]
    return region, delay, universe


def valid_neutralizations(instrument_type: str, region: str) -> set:
    """The neutralizations BRAIN actually offers for this instrument+region. Empty set means
    'unknown' (options not loaded) — the caller should then NOT filter. Used to drop a stale choice
    like COUNTRY on USA before it becomes an alpha config BRAIN would reject."""
    try:
        recs = [r for r in get_options().get("records", [])
                if r["instrument"] == instrument_type and r["region"] == region]
    except Exception:
        return set()
    return {str(n) for r in recs for n in r.get("neutralizations", [])}


# ── operators (cached) ───────────────────────────────────────────────────────────────

def operators_df():
    if _cache["operators"] is None:
        s = require_session()
        _cache["operators"] = brain_call("operators", ace.get_operators, s)
    return _cache["operators"]


# ── datasets ─────────────────────────────────────────────────────────────────────────

def get_datasets(region="USA", universe="TOP3000", delay=1, instrument_type="EQUITY",
                 theme=False, coverage_min=0.0, value_min=0.0, category="") -> list:
    import re
    import pandas as pd
    s = require_session()
    # Snap to a valid delay/universe for this region so the fetch doesn't fail on a mismatch.
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    try:
        df = brain_call("datasets", ace.get_datasets, s, instrument_type=instrument_type,
                        region=region, delay=delay, universe=universe, theme=theme)
    except RuntimeError as e:
        # BRAIN returns nothing for a region/combo with no datasets -> pandas 'No objects to
        # concatenate'. That's an empty result, not a server error.
        if "No objects to concatenate" in str(e) or "concatenate" in str(e):
            return []
        raise
    if df is None or df.empty:
        return []
    # Ensure category_id/category_name are populated regardless of how BRAIN shaped the field. The
    # generic dict-expander only makes category_id when the FIRST row's category is a dict (a null
    # first row drops the whole column, and it can leave NaN on other rows), so when the raw
    # `category` object column is present we ALWAYS re-derive from it.
    def _cat(c, key):
        if isinstance(c, dict):
            return str(c.get(key) or "")
        return str(c) if (key == "id" and c is not None and str(c) != "nan") else ""
    if "category" in df.columns:
        df["category_id"] = df["category"].apply(lambda c: _cat(c, "id"))
        df["category_name"] = df["category"].apply(lambda c: _cat(c, "name"))
    else:
        if "category_id" not in df.columns:
            src = next((c for c in ("categoryId", "category.id", "categoryName", "category.name") if c in df.columns), None)
            if src:
                df["category_id"] = df[src].astype(str)
    if "coverage" in df.columns and coverage_min > 0:
        df = df[pd.to_numeric(df["coverage"], errors="coerce").fillna(0) >= coverage_min]
    if "valueScore" in df.columns and value_min > 0:
        df = df[pd.to_numeric(df["valueScore"], errors="coerce").fillna(0) >= value_min]
    if category:
        cats = {c.strip().lower() for c in re.split(r"[,\s]+", category) if c.strip()}
        cat_cols = [c for c in ("category_id", "category_name", "category",
                                "subcategory_id", "subcategory_name") if c in df.columns]
        if cats and cat_cols:
            mask = None
            for col in cat_cols:
                m = df[col].astype(str).str.lower().isin(cats)
                mask = m if mask is None else (mask | m)
            df = df[mask]
    if "valueScore" in df.columns:
        df = df.sort_values("valueScore", ascending=False)
    cols = [c for c in ["id", "name", "description", "coverage", "valueScore", "alphaCount",
                        "delay", "category_id", "category_name"] if c in df.columns]
    return _json_safe(df[cols].to_dict(orient="records"))


def datafields_present(region, delay, universe, instrument_type, ids, data_type="ALL") -> set:
    """Which of `ids` are REAL datafields in this region+universe, confirmed by BRAIN's
    region-specific /data-fields search (exact id match). Used by the cross-region sweep to
    verify data exists BEFORE simulating, instead of letting a doomed simulation fail. Snaps to a
    valid universe/delay for the region first, so the check uses the same combo the sim will."""
    want = {str(i).strip() for i in ids if str(i).strip()}
    if not want:
        return set()
    s = require_session()
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    found = set()
    for fid in want:
        try:
            df = brain_call(f"datafield {fid}", ace.get_datafields, s,
                            instrument_type=instrument_type, region=region, delay=delay,
                            universe=universe, data_type=data_type, search=fid)
        except Exception:
            continue
        if df is not None and not getattr(df, "empty", True) and "id" in df.columns:
            if (df["id"].astype(str) == fid).any():
                found.add(fid)
    return found


# ── datafields (cached, deduped) ─────────────────────────────────────────────────────

def _dedup_fields(df):
    if df is None or getattr(df, "empty", True) or "id" not in df.columns:
        return df
    ids = df["id"].astype(str).str.strip()
    df = df[ids.ne("") & ids.ne("nan") & df["id"].notna()]
    return df.drop_duplicates(subset="id", keep="first").reset_index(drop=True)


def _fields_key(ids, region, universe, delay, itype, data_type, search):
    return f"{itype}|{region}|{universe}|{delay}|{data_type}|{search}|{','.join(sorted(ids))}"


def fetch_fields(dataset_ids, region="USA", universe="TOP3000", delay=1,
                 instrument_type="EQUITY", data_type="ALL", search=""):
    import pandas as pd
    if not dataset_ids:
        return pd.DataFrame(), 0
    # Snap to a valid delay/universe for the region so a mismatch doesn't fail the fetch.
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    key = _fields_key(dataset_ids, region, universe, delay, instrument_type, data_type, search)
    if key in _cache["fields"]:
        df = _cache["fields"][key]
        return df, int(df.attrs.get("raw_count", len(df)))
    s = require_session()
    try:
        df = brain_call("datafields", wqb_data.fetch_datafields, dataset_ids, s=s,
                        instrument_type=instrument_type, region=region, delay=delay,
                        universe=universe, data_type=data_type, search=search)
    except RuntimeError as e:
        if "concatenate" in str(e):
            return pd.DataFrame(), 0
        raise
    raw_n = 0 if (df is None or getattr(df, "empty", True)) else len(df)
    df = _dedup_fields(df)
    try:
        df.attrs["raw_count"] = raw_n
    except Exception:
        pass
    _cache["fields"][key] = df
    return df, raw_n


# ── alpha PnL / yearly stats (for the Results equity curve) ──────────────────────────

def alpha_pnl(alpha_id: str) -> dict:
    """Cumulative-PnL series for an alpha, plus max drawdown, for the equity curve."""
    s = require_session()
    try:
        df = brain_call("alpha PnL", ace.get_alpha_pnl, s, alpha_id)
    except Exception:
        return {"points": [], "max_drawdown": 0.0}
    if df is None or getattr(df, "empty", True):
        return {"points": [], "max_drawdown": 0.0}
    cols = list(df.columns)
    pnl_col = next((c for c in cols if str(c).lower() == "pnl"), None) or \
        next((c for c in cols if "pnl" in str(c).lower()), cols[-1])
    date_col = next((c for c in cols if "date" in str(c).lower()), cols[0])
    pts, peak, dd = [], -1e18, 0.0
    for _, r in df.iterrows():
        try:
            v = float(r[pnl_col])
        except (TypeError, ValueError):
            continue
        pts.append({"date": str(r[date_col])[:10], "pnl": round(v, 2)})
        peak = max(peak, v)
        dd = min(dd, v - peak)
    daily = [pts[i]["pnl"] - pts[i - 1]["pnl"] for i in range(1, len(pts))]
    import statistics
    wins = sum(1 for x in daily if x > 0)
    losses = sum(1 for x in daily if x < 0)
    longest_loss = cur = 0
    for x in daily:
        cur = cur + 1 if x < 0 else 0; longest_loss = max(longest_loss, cur)
    vol = statistics.pstdev(daily) if len(daily) > 1 else 0.0
    mean = statistics.fmean(daily) if daily else 0.0
    pnl_sharpe = (mean / vol * (252 ** .5)) if vol else None
    dates = [p["date"] for p in pts if p.get("date")]
    years = len({d[:4] for d in dates})
    return {"points": pts, "max_drawdown": round(dd, 2), "diagnostics": {
        "observations": len(daily), "start": dates[0] if dates else "", "end": dates[-1] if dates else "",
        "calendar_years": years, "positive_days": round(wins / len(daily), 4) if daily else None,
        "negative_days": round(losses / len(daily), 4) if daily else None,
        "longest_loss_streak": longest_loss, "daily_mean_pnl": round(mean, 4),
        "daily_pnl_volatility": round(vol, 4), "pnl_implied_sharpe": round(pnl_sharpe, 3) if pnl_sharpe is not None else None}}


def alpha_correlations(alpha_id: str, threshold: float = .7) -> dict:
    """Return signed min/max correlation distributions and submission checks for one alpha."""
    s = require_session()
    def summary(kind, fn, min_col, max_col):
        try: df = brain_call(f"{kind} correlation", fn, s, alpha_id)
        except Exception as e: return {"min": None, "max": None, "focus": None, "error": str(e).splitlines()[0][:160]}
        if df is None or getattr(df, "empty", True): return {"min": None, "max": None, "focus": None}
        def val(col):
            try: return _json_safe(float(df[col].iloc[0])) if col in df.columns else None
            except Exception: return None
        lo, hi = val(min_col), val(max_col)
        return {"min": lo, "max": hi, "focus": hi}
    self_corr = summary("self", ace.get_self_corr, "alpha_min_self_corr", "alpha_max_self_corr")
    prod_corr = summary("production", ace.get_prod_corr, "alpha_min_prod_corr", "alpha_max_prod_corr")
    try:
        tests_df = brain_call("submission checks", ace.get_check_submission, s, alpha_id)
        tests = _json_safe(tests_df.to_dict(orient="records")) if tests_df is not None and not getattr(tests_df, "empty", True) else []
    except Exception as e:
        tests = [{"test": "SUBMISSION_CHECKS", "result": "ERROR", "message": str(e).splitlines()[0][:160]}]
    return {"alpha_id": alpha_id, "threshold": threshold, "self": self_corr, "production": prod_corr,
            "submission_tests": tests}


def prod_corr(alpha_id: str, threshold: float = 0.7) -> dict:
    """The alpha's PRODUCTION correlation — its max correlation against the production/OS pool.
    This is the real submission gate: an alpha is only submittable when this is below the
    threshold (0.70). Returns {value, result, submittable}. value is None when BRAIN has no
    prod-corr yet (e.g. the alpha isn't finished computing)."""
    s = require_session()
    try:
        df = brain_call("prod correlation", ace.check_prod_corr_test, s, alpha_id, threshold)
    except Exception as e:  # noqa: BLE001
        return {"alpha_id": alpha_id, "value": None, "result": "ERROR",
                "submittable": False, "error": str(e).splitlines()[0][:160]}
    if df is None or getattr(df, "empty", True):
        return {"alpha_id": alpha_id, "value": None, "result": "NONE", "submittable": False}
    row = df.iloc[0].to_dict()
    val = _json_safe(row.get("value"))
    res = str(row.get("result") or "")
    return {"alpha_id": alpha_id, "value": val, "result": res,
            "threshold": threshold, "submittable": (res == "PASS")}


def alpha_yearly(alpha_id: str) -> list:
    """Per-year statistics for an alpha (Sharpe/returns/turnover by year)."""
    s = require_session()
    try:
        df = brain_call("yearly stats", ace.get_alpha_yearly_stats, s, alpha_id)
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    return _json_safe(df.to_dict(orient="records"))


# ── json safety ──────────────────────────────────────────────────────────────────────

def _json_safe(x):
    import math
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (str, bytes, bool, int)) or x is None:
        return x
    if hasattr(x, "item"):
        try:
            return _json_safe(x.item())
        except Exception:
            return str(x)
    return x
