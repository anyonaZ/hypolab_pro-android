"""Operator Lab — a DB-backed reference of how each operator is used correctly.

Seeded from the account's own operator definitions (the `definition` column already carries the
canonical signature, e.g. `ts_regression(y, x, d, lag=0, rettype=0)`), enriched with curated
examples/notes for the operators whose USAGE is easy to get wrong (keyword args, bucket-as-group,
densify-on-group, vector wrapping). The user can edit any row in the Operator Atlas; edits are
protected from re-seeding. `reference_block()` injects the important examples into every LLM prompt
so generation follows correct keyword/semantic usage.
"""

from __future__ import annotations

import json
import re
import time

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db import models as M
from app.brain import engine

# Curated (example, note) for operators whose correct usage the LLM most often gets wrong.
CURATED = {
    "keep": ("keep(x, f, period=5)", "period is a KEYWORD argument."),
    "ts_regression": ("ts_regression(y, ts_zscore(x, 60), 120, lag=0, rettype=0)",
                      "x must be a TRANSFORM of / different from y (never identical to y); lag & rettype are keywords."),
    "bucket": ('group_zscore(x, bucket(rank(y), range="0,1,0.1"))',
               "bucket() BUILDS a group — use it ONLY as the group argument of a group_* operator, never as a standalone alpha."),
    "densify": ('densify(bucket(rank(x), range="0,1,0.1"))',
                "densify() applies to a GROUP (e.g. a bucket / group identifier), never to a raw datafield."),
    "vec_avg": ("ts_rank(vec_avg(vector_field), 60)",
                "VECTOR fields MUST be wrapped in a vec_* op before any other operator uses them."),
    "group_zscore": ("group_zscore(x, industry)", "The group is an identifier (industry/subindustry/sector/market/country), never a number."),
    "group_neutralize": ("group_neutralize(x, industry)", "Group is an identifier, not a number."),
    "trade_when": ("trade_when(condition, alpha, -1)", "Gate an alpha on a boolean condition; -1 holds the previous value."),
    "ts_decay_linear": ("ts_decay_linear(x, 20, dense=false)", "dense is a keyword argument."),
    "kth_element": ("kth_element(close, 20, k=5)", "k is a KEYWORD argument (kth_element(x, d, k=…)), never positional."),
}

# Params BRAIN accepts ONLY as keywords, even though its raw signature lists them WITHOUT a default
# (so the signature parser would otherwise call them positional). Forced to keyword everywhere — in
# the seeded reference AND in the validator — so `k=5` is required and a positional `k` is rejected.
KEYWORD_ONLY_PARAMS: dict[str, set[str]] = {
    "kth_element": {"k"},
}


def _parse_definition(defn: str) -> list:
    """Split a signature `op(a, b, kw=default)` into typed params."""
    m = re.match(r"\s*[A-Za-z_]\w*\s*\((.*)\)\s*$", (defn or "").strip(), re.S)
    inner = m.group(1) if m else ""
    args, depth, cur = [], 0, ""
    for ch in inner:
        if ch in "([{":
            depth += 1; cur += ch
        elif ch in ")]}":
            depth -= 1; cur += ch
        elif ch == "," and depth == 0:
            args.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur)
    params = []
    for a in args:
        a = a.strip()
        if not a:
            continue
        if "=" in a:
            nm, _, dv = a.partition("=")
            params.append({"name": nm.strip(), "kind": "keyword", "required": False, "default": dv.strip()})
        else:
            params.append({"name": a, "kind": "positional", "required": True, "default": ""})
    return params


def _cols(df):
    name_col = next((c for c in ["name", "id"] if c in df.columns), None)
    def_col = next((c for c in ["definition", "description", "desc"] if c in df.columns), None)
    scope_col = "scope" if "scope" in df.columns else None
    return name_col, def_col, scope_col


def migrate_schema() -> None:
    """Ensure operator_ref has the composite (name, scope) primary key. An earlier build used a
    name-only PK (REGULAR-only); migrate it in place, preserving any user-edited rows."""
    from sqlalchemy import inspect, text
    from app.db.base import engine, init_db
    insp = inspect(engine)
    if "operator_ref" in insp.get_table_names():
        pk = insp.get_pk_constraint("operator_ref").get("constrained_columns", [])
        if pk == ["name"]:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE operator_ref RENAME TO operator_ref_old"))
            init_db()   # recreate operator_ref with the composite PK
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT OR IGNORE INTO operator_ref "
                    "(name, scope, signature, params_json, example, notes, user_edited, updated_at) "
                    "SELECT name, COALESCE(scope, 'REGULAR'), signature, params_json, example, notes, "
                    "user_edited, updated_at FROM operator_ref_old"))
                conn.execute(text("DROP TABLE operator_ref_old"))
            return
    init_db()


def seed() -> dict:
    """(Re)seed the reference from the live operator list, across ALL scopes (REGULAR, SELECTION,
    COMBO). The composite (name, scope) key lets the same operator name live under each scope.
    Protects user-edited rows."""
    ops = engine.operators_df()   # requires a session; cached after first call
    name_col, def_col, scope_col = _cols(ops)
    if not name_col:
        return {"error": "operator list has no name column"}
    added = updated = 0
    seen: set = set()
    with SessionLocal() as db:
        for _, r in ops.iterrows():
            name = str(r[name_col]).strip()
            if not name or name == "nan":
                continue
            scope = (str(r[scope_col]).strip().upper() if scope_col else "REGULAR") or "REGULAR"
            if scope not in ("REGULAR", "SELECTION", "COMBO"):
                scope = "REGULAR"
            key = (name, scope)
            if key in seen:
                continue
            seen.add(key)
            defn = str(r[def_col]).strip() if def_col else ""

            # Normalize signatures whose canonical form differs from the raw API definition.
            NORMALIZE_SIGNATURES = {
                "ts_backfill": {
                    r"lookback\s*=\s*d": "d",
                },
            }

            for pattern, replacement in NORMALIZE_SIGNATURES.get(name, {}).items():
                defn = re.sub(pattern, replacement, defn, flags=re.IGNORECASE)

            params = _parse_definition(defn)
            # Force keyword-only params (e.g. kth_element's k) to keyword, so the stored reference,
            # the LLM prompts and the validator all agree it is never positional.
            for p in params:
                if p.get("name") in KEYWORD_ONLY_PARAMS.get(name, set()):
                    p["kind"] = "keyword"
            # Curated examples/notes are REGULAR-operator specific — don't apply them to a
            # same-named SELECTION/COMBO operator.
            cur = CURATED.get(name, (None, None)) if scope == "REGULAR" else (None, None)
            example = cur[0] or (defn if "(" in defn else f"{name}(x)")
            notes = cur[1] or ""
            row = db.get(M.OperatorRef, key)
            if row and row.user_edited:
                continue
            if row:
                row.signature = defn or row.signature
                row.params_json = json.dumps(params)
                row.example = row.example or example
                row.notes = row.notes or notes
                row.updated_at = time.time()
                updated += 1
            else:
                db.add(M.OperatorRef(name=name, scope=scope, signature=defn,
                                     params_json=json.dumps(params), example=example, notes=notes))
                added += 1
        db.commit()
    return {"seeded": True, "added": added, "updated": updated}


def _ensure_seeded():
    """Populate on first use if empty and a session is available (best-effort)."""
    with SessionLocal() as db:
        n = db.scalar(select(M.OperatorRef).limit(1))
    if n is None:
        try:
            seed()
        except Exception:
            pass


def list_ops(scope: str = "") -> list:
    _ensure_seeded()
    with SessionLocal() as db:
        q = select(M.OperatorRef).order_by(M.OperatorRef.name)
        if scope:
            q = q.where(M.OperatorRef.scope == scope)
        rows = db.scalars(q).all()
        usage = {x.key: {"uses": x.count, "mean_abs_fitness": x.sum_abs_fitness / max(1, x.count)}
                 for x in db.scalars(select(M.Usage).where(M.Usage.kind == "operator")).all()}
        out=[]
        for r in rows:
            params=json.loads(r.params_json or "[]")
            low=r.name.lower()
            input_type="VECTOR" if low.startswith("vec_") else "GROUP" if low=="densify" else "SCALAR/MATRIX"
            output_type="GROUP" if low in {"bucket","densify"} else "SCALAR"
            semantic=[]
            for p in params:
                n=str(p.get("name","")).lower()
                role="window" if n in {"d","window","lookback","period"} else "group" if "group" in n else "lag" if n=="lag" else "threshold" if n in {"threshold","limit","lower","upper"} else "regression setting" if n in {"rettype","lambda"} else "input"
                p["semantic_role"]=role
                p["value_domain"]="positive integer" if role=="window" else "group identifier" if role=="group" else "declared by BRAIN signature"
                semantic.append(role)
            out.append({"name":r.name,"scope":r.scope,"signature":r.signature,"params":params,
                "example":r.example,"notes":r.notes,"user_edited":r.user_edited,"input_type":input_type,
                "output_type":output_type,"semantic_roles":sorted(set(semantic)),"evidence":usage.get(r.name,{}),
                "common_invalid_usage":"VECTOR input requires vec_* reduction first" if input_type!="VECTOR" else "Do not pass MATRIX fields to vec_* operators"})
        return out


def param_spec_map() -> dict:
    """{operator: {"positional": count, "keywords": set(names)}} — drives the validator's
    keyword-argument enforcement."""
    try:
        _ensure_seeded()
        with SessionLocal() as db:
            rows = db.scalars(select(M.OperatorRef).where(M.OperatorRef.scope == "REGULAR")).all()
    except Exception:
        return {}
    out = {}
    for r in rows:
        params = json.loads(r.params_json or "[]")
        # Every declared parameter name — positional OR keyword. BRAIN accepts a positionally
        # declared param passed BY NAME too, so `names` decides whether a kwarg is real.
        names = {p.get("name") for p in params if p.get("name")}
        # Keyword-only overrides are forced in-code so they hold even before a re-seed.
        ko = KEYWORD_ONLY_PARAMS.get(r.name, set()) & names
        kws = {p.get("name") for p in params if p.get("kind") == "keyword"} | ko
        pos = sum(1 for p in params if p.get("kind") == "positional" and p.get("name") not in kws)
        # Only enforce when the signature actually declared keyword params (else too strict).
        if kws:
            out[r.name] = {"positional": pos, "keywords": kws, "names": names}
    return out


def update_op(
    name: str,
    scope: str = "REGULAR",
    example: str | None = None,
    notes: str | None = None,
) -> dict:
    scope = (scope or "REGULAR").upper()

    with SessionLocal() as db:
        row = db.get(M.OperatorRef, (name, scope))
        if not row:
            return {
                "error": f"no such operator '{name}' in scope '{scope}'"
            }

        if example is not None:
            row.example = example

        if notes is not None:
            row.notes = notes

        row.user_edited = True
        row.updated_at = time.time()

        db.commit()

        return {
            "ok": True,
            "name": name,
            "scope": scope,
        }


def reference_block(scope: str = "REGULAR", limit: int = 80) -> str:
    """Prompt block of the IMPORTANT operator examples (curated/noted + any user edits). Empty
    if nothing is seeded yet — the caller already lists full signatures separately."""
    try:
        _ensure_seeded()
        with SessionLocal() as db:
            rows = db.scalars(select(M.OperatorRef).where(M.OperatorRef.scope == scope)).all()
    except Exception:
        return ""
    important = [r for r in rows if r.user_edited or (r.notes and r.notes.strip())]
    if not important:
        return ""
    lines = []
    for r in important[:limit]:
        line = f"  {r.example}"
        if r.notes:
            line += f"   # {r.notes}"
        lines.append(line)
    return "=== OPERATOR USAGE EXAMPLES (copy these correct forms exactly) ===\n" + "\n".join(lines) + "\n"
